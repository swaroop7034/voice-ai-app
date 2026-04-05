"""Insight-based behavior analysis, prediction, and proactive suggestion generation for ARIS."""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import ollama
from integrations.supabase_store import get_default_user_id, interaction_store

_PATTERN_COUNTER: dict[str, int] = defaultdict(int)
PATTERN_INTERVAL = 5
MAX_SUGGESTIONS = 2
MIN_INSIGHT_CONFIDENCE = 0.6
GENERIC_TOPICS = {"", "general", "other", "misc", "unknown", "none"}
MAX_PHI3_TOPIC_ROWS = 14


def _is_generic_topic(topic: str | None) -> bool:
    value = str(topic or "").strip().lower()
    return value in GENERIC_TOPICS


def _pattern_confidence(count: int, total: int) -> float:
    ratio = count / max(total, 1)
    return round(min(0.96, 0.30 + (ratio * 0.70)), 3)


def _resolve_user_id(user_id: str | None) -> str:
    resolved = (user_id or "").strip()
    return resolved if resolved else get_default_user_id()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _stage_from_depth(avg_depth: float, total: int) -> str:
    if avg_depth >= 2.2 or total >= 10:
        return "advanced"
    if avg_depth >= 1.3 or total >= 6:
        return "intermediate"
    return "beginner"


def _time_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _derive_intent(user_text: str, metadata: dict[str, Any]) -> str:
    meta_intent = str(metadata.get("intent") or "").strip().lower()
    if meta_intent:
        return meta_intent

    text = user_text.lower()
    if any(k in text for k in ["schedule", "remind", "calendar", "meeting", "plan"]):
        return "planning"
    if any(k in text for k in ["how", "why", "explain", "learn", "what is", "difference"]):
        return "learning_query"
    if any(k in text for k in ["create", "build", "write", "generate", "make"]):
        return "build_task"
    if any(k in text for k in ["repeat", "again", "same", "once more"]):
        return "repetition"
    return "general_query"


def _depth_score(user_text: str) -> int:
    text = user_text.lower()
    score = 1
    if any(k in text for k in ["why", "how", "compare", "tradeoff", "architecture", "optimize"]):
        score += 1
    if any(k in text for k in ["step by step", "project", "implement", "production", "scale"]):
        score += 1
    return min(score, 3)


def _topic_family(topic: str) -> str:
    topic = topic.strip().lower()
    if "ai" in topic or "ml" in topic or "llm" in topic:
        return "ai"
    if "physics" in topic or "quantum" in topic:
        return "physics"
    if "schedule" in topic or "calendar" in topic or "meeting" in topic:
        return "planning"
    return topic.split(" ")[0] if topic else "general"


def _extract_topics_phi3_sync(texts: list[str]) -> list[str]:
    if not texts:
        return []

    numbered = "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(texts))
    prompt = f"""You are a topic extraction engine for user chat logs.

Extract one short specific topic for each line.
Rules:
- Return JSON only.
- lowercase topics.
- Avoid generic labels like general/misc/other.
- If uncertain use unknown.

Lines:
{numbered}

Return format:
{{"topics": ["topic1", "topic2"]}}
"""

    response = ollama.generate(model="phi3", prompt=prompt, stream=False)
    output = str(response.get("response", "")).strip()

    start = output.find("{")
    end = output.rfind("}") + 1
    if start < 0 or end <= start:
        return ["unknown"] * len(texts)

    try:
        parsed = json.loads(output[start:end])
        topics = parsed.get("topics") or []
        normalized = [str(item).strip().lower() for item in topics]
        if len(normalized) < len(texts):
            normalized.extend(["unknown"] * (len(texts) - len(normalized)))
        return normalized[: len(texts)]
    except Exception:
        return ["unknown"] * len(texts)


async def _extract_topics_phi3(texts: list[str]) -> list[str]:
    try:
        return await asyncio.to_thread(_extract_topics_phi3_sync, texts)
    except Exception as exc:
        print(f"[BEHAVIOR] phi3 topic extraction failed: {exc}")
        return ["unknown"] * len(texts)


async def _fetch_recent_interactions(user_id: str, limit: int = 60) -> list[dict[str, Any]]:
    if not interaction_store.enabled or interaction_store.client is None:
        return []

    def _execute() -> list[dict[str, Any]]:
        response = (
            interaction_store.client.table(interaction_store.table_name)
            .select("user_text,aries_text,metadata,importance,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(getattr(response, "data", []) or [])

    try:
        return await asyncio.to_thread(_execute)
    except Exception as exc:
        print(f"[BEHAVIOR] Failed to fetch interactions for {user_id}: {exc}")
        return []


async def _build_enriched_sequence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sequence = list(reversed(rows))  # chronological

    topic_fix_indexes: list[int] = []
    topic_fix_texts: list[str] = []
    for idx, row in enumerate(sequence):
        metadata = row.get("metadata") or {}
        topic = str(metadata.get("topic") or "").strip().lower()
        user_text = str(row.get("user_text") or "").strip()
        if _is_generic_topic(topic) and user_text and len(topic_fix_texts) < MAX_PHI3_TOPIC_ROWS:
            topic_fix_indexes.append(idx)
            topic_fix_texts.append(user_text)

    inferred_topics = await _extract_topics_phi3(topic_fix_texts) if topic_fix_texts else []
    inferred_by_index: dict[int, str] = {}
    for i, index in enumerate(topic_fix_indexes):
        inferred = str(inferred_topics[i] if i < len(inferred_topics) else "unknown").strip().lower()
        inferred_by_index[index] = inferred if not _is_generic_topic(inferred) else "unknown"

    enriched: list[dict[str, Any]] = []
    for idx, row in enumerate(sequence):
        metadata = row.get("metadata") or {}
        user_text = str(row.get("user_text") or "").strip()
        topic = str(metadata.get("topic") or "").strip().lower()
        if _is_generic_topic(topic):
            topic = inferred_by_index.get(idx, "unknown")

        dt = _parse_iso(row.get("created_at"))
        intent = _derive_intent(user_text, metadata)
        enriched.append(
            {
                "user_text": user_text,
                "topic": topic,
                "topic_family": _topic_family(topic),
                "intent": intent,
                "created_at": dt,
                "hour": dt.hour if dt else None,
                "depth": _depth_score(user_text),
            }
        )
    return enriched


def _learning_insights(user_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        family = str(event.get("topic_family") or "").strip().lower()
        if family and family not in GENERIC_TOPICS:
            grouped[family].append(event)

    insights: list[dict[str, Any]] = []
    for family, group in grouped.items():
        if len(group) < 3:
            continue

        depth_values = [int(g.get("depth") or 1) for g in group]
        avg_depth = sum(depth_values) / max(len(depth_values), 1)
        stage = _stage_from_depth(avg_depth, len(group))
        confidence = _pattern_confidence(len(group), len(events))
        subtopics = sorted({str(g.get("topic") or "") for g in group if g.get("topic")})

        insight_text = f"User is learning {family}"
        if len(subtopics) >= 2:
            insight_text += f" across {', '.join(subtopics[:3])}"

        insights.append(
            {
                "user_id": user_id,
                "pattern_type": "learning",
                "pattern_data": {
                    "type": "learning",
                    "insight": insight_text,
                    "stage": stage,
                    "topic": family,
                    "subtopics": subtopics[:4],
                    "count": len(group),
                    "window": len(events),
                    "confidence": confidence,
                },
                "confidence": confidence,
            }
        )
    return insights


def _habit_insights(user_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_intent_counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        hour = event.get("hour")
        intent = str(event.get("intent") or "").strip().lower()
        if hour is None or not intent:
            continue
        bucket_intent_counts[(_time_bucket(int(hour)), intent)] += 1

    insights: list[dict[str, Any]] = []
    for (bucket, intent), count in bucket_intent_counts.most_common(3):
        if count < 3:
            continue
        confidence = _pattern_confidence(count, len(events))
        insights.append(
            {
                "user_id": user_id,
                "pattern_type": "habit",
                "pattern_data": {
                    "type": "habit",
                    "insight": f"User regularly does {intent} in the {bucket}",
                    "bucket": bucket,
                    "intent": intent,
                    "count": count,
                    "window": len(events),
                    "confidence": confidence,
                },
                "confidence": confidence,
            }
        )
    return insights


def _repetition_insights(user_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = events[-12:]
    intent_counts: Counter[str] = Counter(
        str(event.get("intent") or "").strip().lower() for event in recent if event.get("intent")
    )

    insights: list[dict[str, Any]] = []
    for intent, count in intent_counts.most_common(3):
        if count < 3:
            continue
        confidence = _pattern_confidence(count, len(recent))
        insights.append(
            {
                "user_id": user_id,
                "pattern_type": "repetition",
                "pattern_data": {
                    "type": "repetition",
                    "insight": f"User repeatedly asks for {intent}",
                    "intent": intent,
                    "count": count,
                    "window": len(recent),
                    "confidence": confidence,
                },
                "confidence": confidence,
            }
        )
    return insights


async def analyze_patterns(user_id: str | None) -> list[dict[str, Any]]:
    """Analyze recent interaction sequence and emit structured behavior insights."""
    resolved_user_id = _resolve_user_id(user_id)
    rows = await _fetch_recent_interactions(resolved_user_id, limit=60)
    if not rows:
        return []

    events = await _build_enriched_sequence(rows)
    insights = []
    insights.extend(_learning_insights(resolved_user_id, events))
    insights.extend(_habit_insights(resolved_user_id, events))
    insights.extend(_repetition_insights(resolved_user_id, events))

    final_insights = [ins for ins in insights if float(ins.get("confidence") or 0.0) >= MIN_INSIGHT_CONFIDENCE]
    final_insights.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)

    print(f"[BEHAVIOR] Insights detected for {resolved_user_id}: {final_insights}")
    return final_insights


async def store_patterns(user_id: str | None, patterns: list[dict[str, Any]]) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    if not interaction_store.enabled or interaction_store.client is None:
        return

    def _execute() -> None:
        # Keep latest snapshot only to avoid unbounded pattern growth.
        interaction_store.client.table("behavior_patterns").delete().eq("user_id", resolved_user_id).execute()
        if patterns:
            interaction_store.client.table("behavior_patterns").insert(patterns).execute()

    try:
        await asyncio.to_thread(_execute)
    except Exception as exc:
        print(f"[BEHAVIOR] Failed storing patterns for {resolved_user_id}: {exc}")


async def _load_patterns(user_id: str) -> list[dict[str, Any]]:
    if not interaction_store.enabled or interaction_store.client is None:
        return []

    def _execute() -> list[dict[str, Any]]:
        response = (
            interaction_store.client.table("behavior_patterns")
            .select("id,pattern_type,pattern_data,confidence,last_updated")
            .eq("user_id", user_id)
            .order("confidence", desc=True)
            .limit(10)
            .execute()
        )
        return list(getattr(response, "data", []) or [])

    try:
        return await asyncio.to_thread(_execute)
    except Exception as exc:
        print(f"[BEHAVIOR] Failed loading patterns for {user_id}: {exc}")
        return []


def predict_next_action(user_insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Predict likely next user actions from behavior insights."""
    predictions: list[dict[str, Any]] = []

    for insight in user_insights:
        ptype = str(insight.get("pattern_type") or "").lower()
        pdata = insight.get("pattern_data") or {}
        confidence = float(insight.get("confidence") or 0.0)

        if ptype == "learning":
            topic = str(pdata.get("topic") or "this topic")
            stage = str(pdata.get("stage") or "beginner")
            if stage == "beginner":
                action = f"Offer a hands-on starter project in {topic}"
            elif stage == "intermediate":
                action = f"Offer a deeper implementation challenge in {topic}"
            else:
                action = f"Offer optimization or architecture-level guidance in {topic}"
            predictions.append(
                {
                    "type": "learning_next_step",
                    "action": action,
                    "priority": round(confidence + 0.05, 3),
                    "reason": pdata.get("insight"),
                }
            )

        elif ptype == "habit":
            intent = str(pdata.get("intent") or "this task")
            bucket = str(pdata.get("bucket") or "that time")
            predictions.append(
                {
                    "type": "routine_automation",
                    "action": f"Offer recurring reminder/automation for {intent} in the {bucket}",
                    "priority": round(confidence, 3),
                    "reason": pdata.get("insight"),
                }
            )

        elif ptype == "repetition":
            intent = str(pdata.get("intent") or "this flow")
            predictions.append(
                {
                    "type": "workflow_shortcut",
                    "action": f"Offer a shortcut/template for repeated {intent}",
                    "priority": round(confidence + 0.03, 3),
                    "reason": pdata.get("insight"),
                }
            )

    predictions.sort(key=lambda p: float(p.get("priority") or 0.0), reverse=True)
    print(f"[BEHAVIOR] Predicted next actions: {predictions}")
    return predictions


def _suggestion_from_pattern(pattern: dict[str, Any]) -> str | None:
    ptype = str(pattern.get("pattern_type") or "").lower()
    pdata = pattern.get("pattern_data") or {}

    if ptype == "learning":
        topic = str(pdata.get("topic") or "the topic")
        stage = str(pdata.get("stage") or "beginner")
        if stage == "beginner":
            return f"Let's solidify {topic} with a 20-minute mini project today, Sir."
        if stage == "intermediate":
            return f"You are progressing in {topic}; shall I queue a practical challenge next, Sir?"
        return f"You are advanced in {topic}; I can propose an optimization roadmap, Sir."

    if ptype == "habit":
        intent = str(pdata.get("intent") or "this task")
        bucket = str(pdata.get("bucket") or "that time")
        return f"You regularly handle {intent} in the {bucket}; should I automate a routine reminder, Sir?"

    if ptype == "repetition":
        intent = str(pdata.get("intent") or "this flow")
        return f"You repeat {intent} often; I can create a reusable shortcut workflow, Sir."

    return None


async def _existing_unseen_suggestions(user_id: str) -> set[str]:
    if not interaction_store.enabled or interaction_store.client is None:
        return set()

    def _execute() -> set[str]:
        response = (
            interaction_store.client.table("suggestions")
            .select("suggestion_text")
            .eq("user_id", user_id)
            .eq("seen", False)
            .limit(20)
            .execute()
        )
        rows = list(getattr(response, "data", []) or [])
        return {str(row.get("suggestion_text") or "").strip() for row in rows if row.get("suggestion_text")}

    try:
        return await asyncio.to_thread(_execute)
    except Exception:
        return set()


async def generate_suggestions(user_id: str | None) -> list[dict[str, Any]]:
    resolved_user_id = _resolve_user_id(user_id)
    patterns = await _load_patterns(resolved_user_id)
    if not patterns:
        return []

    predictions = predict_next_action(patterns)
    existing = await _existing_unseen_suggestions(resolved_user_id)
    suggestions_to_insert: list[dict[str, Any]] = []

    # Keep only 1-2 high quality suggestions, preferring learning/habit actions.
    type_quota = {"learning": 1, "habit": 1, "repetition": 1}
    used_quota = {"learning": 0, "habit": 0, "repetition": 0}

    sorted_patterns = sorted(patterns, key=lambda p: float(p.get("confidence") or 0.0), reverse=True)
    for pattern in sorted_patterns:
        ptype = str(pattern.get("pattern_type") or "").lower()
        confidence = float(pattern.get("confidence") or 0.0)
        if confidence < MIN_INSIGHT_CONFIDENCE:
            continue
        if ptype in type_quota and used_quota[ptype] >= type_quota[ptype]:
            continue

        text = _suggestion_from_pattern(pattern)
        if not text:
            continue
        if text in existing:
            continue

        reason = ""
        for prediction in predictions:
            if ptype in str(prediction.get("type") or ""):
                reason = str(prediction.get("action") or "")
                break

        print(
            f"[BEHAVIOR] Suggestion reasoning user_id={resolved_user_id} "
            f"type={ptype} confidence={confidence:.3f} reason={reason or 'pattern-derived'}"
        )

        suggestions_to_insert.append(
            {
                "user_id": resolved_user_id,
                "suggestion_text": text,
            }
        )
        existing.add(text)
        if ptype in used_quota:
            used_quota[ptype] += 1

        if len(suggestions_to_insert) >= MAX_SUGGESTIONS:
            break

    if not suggestions_to_insert:
        return []

    def _insert() -> list[dict[str, Any]]:
        response = interaction_store.client.table("suggestions").insert(suggestions_to_insert).execute()
        return list(getattr(response, "data", []) or [])

    try:
        inserted = await asyncio.to_thread(_insert)
        print(f"[BEHAVIOR] Generated suggestions for {resolved_user_id}: {inserted}")
        return inserted
    except Exception as exc:
        print(f"[BEHAVIOR] Failed storing suggestions for {resolved_user_id}: {exc}")
        return []


async def maybe_run_behavior_cycle(user_id: str | None, interaction_step: int = PATTERN_INTERVAL) -> None:
    """Run behavior analysis every N interactions for this user."""
    resolved_user_id = _resolve_user_id(user_id)
    _PATTERN_COUNTER[resolved_user_id] += 1
    count = _PATTERN_COUNTER[resolved_user_id]

    if count % max(interaction_step, 1) != 0:
        return

    patterns = await analyze_patterns(resolved_user_id)
    await store_patterns(resolved_user_id, patterns)
    await generate_suggestions(resolved_user_id)


async def build_behavior_context(user_id: str | None, limit: int = 3) -> str:
    """Build concise behavior insight context for prompt injection."""
    resolved_user_id = _resolve_user_id(user_id)
    patterns = await _load_patterns(resolved_user_id)
    if not patterns:
        return ""

    predictions = predict_next_action(patterns)
    lines: list[str] = []

    for pattern in patterns[:limit]:
        pdata = pattern.get("pattern_data") or {}
        insight = str(pdata.get("insight") or "").strip()
        stage = str(pdata.get("stage") or "").strip()
        confidence = float(pattern.get("confidence") or 0.0)
        if not insight:
            continue
        suffix = f" (stage={stage}, confidence={confidence:.2f})" if stage else f" (confidence={confidence:.2f})"
        lines.append(f"* {insight}{suffix}")

    for prediction in predictions[:2]:
        action = str(prediction.get("action") or "").strip()
        if action:
            lines.append(f"* Predicted next need: {action}")

    context = "\n".join(lines)
    print(f"[BEHAVIOR] Prompt behavior context for {resolved_user_id}: {context}")
    return context


async def fetch_unseen_suggestions(user_id: str | None, limit: int = MAX_SUGGESTIONS) -> list[dict[str, Any]]:
    resolved_user_id = _resolve_user_id(user_id)
    if not interaction_store.enabled or interaction_store.client is None:
        return []

    def _execute() -> list[dict[str, Any]]:
        response = (
            interaction_store.client.table("suggestions")
            .select("id,suggestion_text,created_at")
            .eq("user_id", resolved_user_id)
            .eq("seen", False)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return list(getattr(response, "data", []) or [])

    try:
        rows = await asyncio.to_thread(_execute)
        return rows
    except Exception as exc:
        print(f"[BEHAVIOR] Failed fetching unseen suggestions for {resolved_user_id}: {exc}")
        return []


async def mark_suggestions_seen(suggestion_ids: list[int]) -> None:
    if not suggestion_ids:
        return
    if not interaction_store.enabled or interaction_store.client is None:
        return

    def _execute() -> None:
        interaction_store.client.table("suggestions").update({"seen": True}).in_("id", suggestion_ids).execute()

    try:
        await asyncio.to_thread(_execute)
        print(f"[BEHAVIOR] Delivered suggestions marked seen: {suggestion_ids}")
    except Exception as exc:
        print(f"[BEHAVIOR] Failed marking suggestions seen: {exc}")
