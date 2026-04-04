"""Lightweight intelligence helpers for context quality and personalization."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MAX_MEMORY = 3
MAX_HISTORY = 5


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _memory_type(memory: dict[str, Any]) -> str:
    metadata = memory.get("metadata") or {}
    memory_type = str(metadata.get("type") or "HISTORY").upper()
    return memory_type if memory_type in {"FACT", "PREFERENCE", "HISTORY"} else "HISTORY"


def _memory_topic(memory: dict[str, Any]) -> str:
    metadata = memory.get("metadata") or {}
    topic = str(metadata.get("topic") or "general").strip().lower()
    return topic or "general"


def apply_memory_decay(memories: list[dict[str, Any]], age_factor_days: float = 30.0) -> list[dict[str, Any]]:
    """Apply exponential time decay to memory importance in Python."""
    now = datetime.now(timezone.utc)
    decayed: list[dict[str, Any]] = []

    for memory in memories:
        item = dict(memory)
        base_importance = float(item.get("importance") or 0.5)

        created_at = _parse_timestamp(item.get("created_at"))
        if created_at is None:
            age_days = 0.0
        else:
            created_at = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)

        decay = math.exp(-(age_days / max(age_factor_days, 1.0)))
        decayed_importance = base_importance * decay
        similarity = float(item.get("similarity") or 0.0)
        # Keep ranking shape consistent with backend formula while applying time-decayed importance.
        item["decayed_importance"] = decayed_importance
        item["adjusted_score"] = (similarity * 0.6) + (decayed_importance * 0.2)
        decayed.append(item)

    decayed.sort(key=lambda m: float(m.get("adjusted_score") or 0.0), reverse=True)
    return decayed


def filter_memories_by_type(memories: list[dict[str, Any]], history_similarity_threshold: float = 0.80) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    FACT: always include in candidate pool.
    PREFERENCE: returned separately for profile dominance.
    HISTORY: include only when similarity is high.
    """
    candidates: list[dict[str, Any]] = []
    preference_memories: list[dict[str, Any]] = []

    for memory in memories:
        memory_type = _memory_type(memory)
        if memory_type == "PREFERENCE":
            preference_memories.append(memory)
            continue

        if memory_type == "FACT":
            candidates.append(memory)
            continue

        similarity = float(memory.get("similarity") or 0.0)
        if similarity >= history_similarity_threshold:
            candidates.append(memory)

    return candidates, preference_memories


def smart_memory_selector(memories: list[dict[str, Any]], max_memory: int = MAX_MEMORY) -> list[dict[str, Any]]:
    """
    Pick top 2 by score and 1 diverse-topic memory if available.
    Removes near-duplicate memories by user_text.
    """
    if not memories:
        return []

    ranked = sorted(memories, key=lambda m: float(m.get("adjusted_score") or m.get("score") or 0.0), reverse=True)

    selected: list[dict[str, Any]] = []
    seen_user_text: set[str] = set()

    for memory in ranked:
        key = (memory.get("user_text") or "").strip().lower()
        if key and key in seen_user_text:
            continue
        selected.append(memory)
        if key:
            seen_user_text.add(key)
        if len(selected) >= min(2, max_memory):
            break

    # Add one diverse topic memory when possible.
    used_topics = {_memory_topic(m) for m in selected}
    for memory in ranked:
        if len(selected) >= max_memory:
            break
        key = (memory.get("user_text") or "").strip().lower()
        topic = _memory_topic(memory)
        if key and key in seen_user_text:
            continue
        if topic not in used_topics:
            selected.append(memory)
            if key:
                seen_user_text.add(key)
            used_topics.add(topic)
            break

    return selected[:max_memory]


def compress_context(memories: list[dict[str, Any]], chat_history: list[dict[str, str]], max_bullets: int = 5) -> list[str]:
    """Compress memories + recent chat into 3-5 concise bullet points."""
    bullets: list[str] = []

    for memory in memories[:MAX_MEMORY]:
        memory_type = _memory_type(memory)
        topic = _memory_topic(memory)
        user_text = (memory.get("user_text") or "").strip()
        aries_text = (memory.get("aries_text") or "").strip()
        if not user_text and not aries_text:
            continue

        bullet = f"[{memory_type}:{topic}] User said '{user_text[:90]}'"
        if aries_text:
            bullet += f"; ARIS replied '{aries_text[:90]}'"
        bullets.append(bullet)
        if len(bullets) >= max_bullets - 1:
            break

    # Add one lightweight conversation summary from recent turns.
    if chat_history:
        recent = chat_history[-MAX_HISTORY:]
        user_turns = [m.get("content", "").strip() for m in recent if m.get("role") == "user" and m.get("content")]
        assistant_turns = [m.get("content", "").strip() for m in recent if m.get("role") == "assistant" and m.get("content")]
        if user_turns or assistant_turns:
            latest_user = user_turns[-1][:100] if user_turns else ""
            latest_ai = assistant_turns[-1][:100] if assistant_turns else ""
            convo = f"Recent convo: user '{latest_user}'"
            if latest_ai:
                convo += f", ARIS '{latest_ai}'"
            bullets.append(convo)

    return bullets[:max_bullets]


def conflict_resolver(preferences: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Resolve conflicting preference settings, prioritizing latest explicit style."""
    resolved = dict(preferences or {})
    resolved_notes: list[str] = []

    response_style = str(resolved.get("response_style") or "").lower()

    if "short" in response_style and "detail" in response_style:
        # Keep explicit last style marker when provided, else choose short for latency-friendly defaults.
        latest_style = str(resolved.get("latest_response_style") or "").lower()
        if latest_style in {"short", "detailed"}:
            resolved["response_style"] = latest_style
            resolved_notes.append(f"response_style_conflict_resolved={latest_style}")
        else:
            resolved["response_style"] = "short"
            resolved_notes.append("response_style_conflict_resolved=short")

    # Normalize explicit variants.
    if response_style in {"concise", "brief"}:
        resolved["response_style"] = "short"
    if response_style in {"long", "detailed", "explain"}:
        resolved["response_style"] = "detailed"

    return resolved, resolved_notes
