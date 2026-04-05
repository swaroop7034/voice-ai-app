"""Generate a voice suggestion from the latest user interactions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from logger import log_debug, log_error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.behavior_analyzer import (
    _extract_topics_phi3,
    _is_generic_topic,
    _pattern_confidence,
    _resolve_user_id,
    _suggestion_from_pattern,
)
from core.tts_engine import text_to_speech
from integrations.supabase_store import interaction_store

DEFAULT_ROWS = 10
DEFAULT_OUTPUT = "temp_audio/outgoing/last10_suggestion.mp3"


def _play_audio_file(path: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            log_debug(f"[VOICE TEST] Playing audio: {path}")
            return True

        log_debug(f"[VOICE TEST] Auto-play not supported on this OS. Audio saved at: {path}")
        return False
    except Exception as exc:
        log_error(f"VOICE TEST failed to play audio file: {exc}")
        return False


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


async def _fetch_recent_rows(user_id: str, rows: int) -> list[dict[str, Any]]:
    if not interaction_store.enabled or interaction_store.client is None:
        return []

    def _execute() -> list[dict[str, Any]]:
        response = (
            interaction_store.client.table(interaction_store.table_name)
            .select("user_text,metadata,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(rows)
            .execute()
        )
        return list(getattr(response, "data", []) or [])

    try:
        return await asyncio.to_thread(_execute)
    except Exception as exc:
        log_error(f"VOICE TEST failed to fetch interactions: {exc}")
        return []


async def _build_patterns_from_rows(user_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(rows)
    topic_counts: Counter[str] = Counter()
    hour_counts: Counter[int] = Counter()

    topic_fix_indexes: list[int] = []
    topic_fix_texts: list[str] = []

    for idx, row in enumerate(rows):
        metadata = row.get("metadata") or {}
        topic = str(metadata.get("topic") or "").strip().lower()
        user_text = str(row.get("user_text") or "").strip()
        if _is_generic_topic(topic) and user_text:
            topic_fix_indexes.append(idx)
            topic_fix_texts.append(user_text)

    inferred_topics = await _extract_topics_phi3(topic_fix_texts) if topic_fix_texts else []
    inferred_by_index: dict[int, str] = {}
    for i, row_index in enumerate(topic_fix_indexes):
        inferred = str(inferred_topics[i] if i < len(inferred_topics) else "unknown").strip().lower()
        if _is_generic_topic(inferred):
            inferred = "unknown"
        inferred_by_index[row_index] = inferred

    for idx, row in enumerate(rows):
        metadata = row.get("metadata") or {}
        topic = str(metadata.get("topic") or "").strip().lower()
        if _is_generic_topic(topic):
            topic = inferred_by_index.get(idx, "")
        if topic and not _is_generic_topic(topic):
            topic_counts[topic] += 1

        dt = _parse_iso(row.get("created_at"))
        if dt is not None:
            hour_counts[dt.hour] += 1

    patterns: list[dict[str, Any]] = []
    for topic, count in topic_counts.most_common(3):
        if count >= 2:
            patterns.append(
                {
                    "user_id": user_id,
                    "pattern_type": "topic",
                    "pattern_data": {"topic": topic, "count": count, "window": total},
                    "confidence": _pattern_confidence(count, total),
                }
            )

    for hour, count in hour_counts.most_common(2):
        if count >= 2:
            patterns.append(
                {
                    "user_id": user_id,
                    "pattern_type": "time",
                    "pattern_data": {"hour": hour, "count": count, "window": total},
                    "confidence": _pattern_confidence(count, total),
                }
            )

    patterns.sort(key=lambda p: (str(p.get("pattern_type") or "") != "topic", -float(p.get("confidence") or 0.0)))
    return patterns


def _pick_suggestion(patterns: list[dict[str, Any]]) -> str:
    for pattern in patterns:
        text = _suggestion_from_pattern(pattern)
        if text:
            return text
    return "I reviewed your latest interactions. Want me to suggest your next best action, Sir?"


async def run(user_id: str | None, rows: int, output_path: str) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    rows = max(rows, 1)

    interactions = await _fetch_recent_rows(resolved_user_id, rows)
    if not interactions:
        log_debug("[VOICE TEST] No interactions found for suggestion generation.")
        return

    patterns = await _build_patterns_from_rows(resolved_user_id, interactions)
    suggestion_text = _pick_suggestion(patterns)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ok = text_to_speech(suggestion_text, output_path)
    played = _play_audio_file(output_path) if ok else False

    payload = {
        "user_id": resolved_user_id,
        "rows_used": len(interactions),
        "suggestion_text": suggestion_text,
        "audio_path": output_path,
        "audio_generated": bool(ok),
        "audio_played": bool(played),
        "patterns": patterns,
    }
    log_debug(json.dumps(payload, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate voice suggestion from latest interactions (default: last 10)."
    )
    parser.add_argument("--user-id", default=None, help="User ID to analyze.")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="How many latest rows to analyze.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output audio path for the suggestion voice file.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.user_id, args.rows, args.output))
