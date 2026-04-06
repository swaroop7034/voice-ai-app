from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import ollama

from integrations.supabase_store import get_default_user_id, interaction_store
from logger import log_debug, log_error


BEHAVIOR_MODEL = os.getenv("BEHAVIOR_MODEL", "phi3:mini").strip() or "phi3:mini"
BEHAVIOR_TABLE = os.getenv("SUPABASE_BEHAVIOR_TABLE", "behavior_logs").strip() or "behavior_logs"
BEHAVIOR_MIN_CONFIDENCE = float(os.getenv("BEHAVIOR_MIN_CONFIDENCE", "0.70"))
BUFFER_TTL_SECONDS = int(os.getenv("BEHAVIOR_BUFFER_TTL_SECONDS", "120"))

SMALL_TALK_INTENTS = {"small_talk", "greeting", "chitchat", "gratitude", "casual"}
SMALL_TALK_PATTERNS = [
    re.compile(r"^\s*(hi|hello|hey|yo|thanks|thank you|good morning|good afternoon|good evening)\b", re.I),
]

REQUIRED_KEYS = [
    "intent",
    "category",
    "entities",
    "user_preferences",
    "time_reference",
    "frequency_signal",
    "emotional_tone",
    "confidence",
]


@dataclass
class BufferState:
    parts: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BehaviorExtractor:
    def __init__(self) -> None:
        self._buffers: dict[str, BufferState] = {}
        self._lock = asyncio.Lock()

    async def process_and_log(self, user_id: str | None, user_text: str, source: str) -> dict[str, Any] | None:
        resolved_user_id = (user_id or "").strip() or get_default_user_id()
        normalized_text = " ".join((user_text or "").split())
        if not normalized_text:
            return None

        merged_text = await self._merge_or_buffer(resolved_user_id, normalized_text)
        if not merged_text:
            return None

        behavior = await self.extract_behavior(merged_text)
        if not self._should_log(behavior, merged_text):
            return None

        await self._save_to_supabase(
            user_id=resolved_user_id,
            user_text=merged_text,
            behavior=behavior,
            source=source,
        )
        return behavior

    async def _merge_or_buffer(self, user_id: str, text: str) -> str | None:
        async with self._lock:
            self._evict_stale_locked()
            existing = self._buffers.get(user_id)

            if existing is not None and existing.parts:
                merged_text = " ".join(existing.parts + [text]).strip()
                if self._looks_incomplete(merged_text):
                    self._buffers[user_id] = BufferState(
                        parts=[merged_text],
                        updated_at=datetime.now(timezone.utc),
                    )
                    return None

                del self._buffers[user_id]
                return merged_text

            if self._looks_incomplete(text):
                self._buffers[user_id] = BufferState(parts=[text], updated_at=datetime.now(timezone.utc))
                return None

            return text

    def _evict_stale_locked(self) -> None:
        now = datetime.now(timezone.utc)
        stale_user_ids = [
            uid
            for uid, state in self._buffers.items()
            if (now - state.updated_at).total_seconds() > BUFFER_TTL_SECONDS
        ]
        for uid in stale_user_ids:
            del self._buffers[uid]

    def _looks_incomplete(self, text: str) -> bool:
        lowered = text.strip().lower()
        if len(lowered) < 8:
            return True

        trailing_tokens = {"for", "to", "with", "about", "on", "and", "or", "but", "because"}
        last_token = lowered.split(" ")[-1]
        if last_token in trailing_tokens:
            return True

        if lowered.endswith(("...", " and", " then", " so")):
            return True

        return False

    async def extract_behavior(self, text: str) -> dict[str, Any]:
        prompt = self._build_prompt(text)
        raw = await asyncio.to_thread(self._generate_sync, prompt)
        parsed = self._parse_json(raw)
        if parsed is None:
            parsed = self._heuristic_fallback(text)
        return self._normalize_schema(parsed)

    def _generate_sync(self, prompt: str) -> str:
        try:
            response = ollama.generate(model=BEHAVIOR_MODEL, prompt=prompt, stream=False)
            return str(response.get("response") or "")
        except Exception as exc:
            log_error(f"BEHAVIOR model generation failed: {exc}")
            return ""

    def _build_prompt(self, text: str) -> str:
        return f"""You are a strict behavior extractor.
Extract structured behavior from the input text.
Return ONLY valid JSON with these exact keys and no extras:
{{
  \"intent\": \"\",
  \"category\": \"\",
  \"entities\": {{}},
  \"user_preferences\": [],
  \"time_reference\": \"\",
  \"frequency_signal\": \"\",
  \"emotional_tone\": \"\",
  \"confidence\": 0.0
}}
Rules:
- intent/category must be lowercase snake_case when possible.
- confidence must be from 0.0 to 1.0.
- if no clear intent, set intent to empty string and confidence below 0.5.
- keep entities concise and factual.

Input:
{text}
"""

    def _parse_json(self, raw: str) -> dict[str, Any] | None:
        if not raw:
            return None

        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            return None

        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None

    def _heuristic_fallback(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        entities: dict[str, Any] = {}

        time_reference = ""
        if any(token in lowered for token in ["today", "tomorrow", "tonight", "morning", "evening"]):
            time_reference = "relative_time"

        if "schedule" in lowered or "meeting" in lowered or "calendar" in lowered:
            intent = "scheduling"
            category = "productivity"
            confidence = 0.78
        elif "remind" in lowered:
            intent = "reminder"
            category = "productivity"
            confidence = 0.76
        elif "safe folder" in lowered or "private files" in lowered:
            intent = "private_operation"
            category = "security"
            confidence = 0.82
        elif "reset voice" in lowered:
            intent = "voice_reset"
            category = "security"
            confidence = 0.79
        else:
            intent = ""
            category = "general"
            confidence = 0.45

        match_numbers = re.findall(r"\b\d{1,2}(:\d{2})?\b", lowered)
        if match_numbers:
            entities["mentioned_time"] = match_numbers

        return {
            "intent": intent,
            "category": category,
            "entities": entities,
            "user_preferences": [],
            "time_reference": time_reference,
            "frequency_signal": "",
            "emotional_tone": "neutral",
            "confidence": confidence,
        }

    def _normalize_schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {
            "intent": str(payload.get("intent") or "").strip(),
            "category": str(payload.get("category") or "").strip(),
            "entities": payload.get("entities") if isinstance(payload.get("entities"), dict) else {},
            "user_preferences": payload.get("user_preferences") if isinstance(payload.get("user_preferences"), list) else [],
            "time_reference": str(payload.get("time_reference") or "").strip(),
            "frequency_signal": str(payload.get("frequency_signal") or "").strip(),
            "emotional_tone": str(payload.get("emotional_tone") or "").strip() or "neutral",
            "confidence": self._coerce_confidence(payload.get("confidence")),
        }
        # Ensure strict key set and ordering-compatible dict structure.
        return {key: normalized[key] for key in REQUIRED_KEYS}

    def _coerce_confidence(self, value: Any) -> float:
        try:
            conf = float(value)
        except Exception:
            return 0.0
        return max(0.0, min(1.0, round(conf, 3)))

    def _is_small_talk(self, text: str, behavior: dict[str, Any]) -> bool:
        intent = str(behavior.get("intent") or "").strip().lower()
        if intent in SMALL_TALK_INTENTS:
            return True

        lowered = text.strip().lower()
        return any(pattern.match(lowered) for pattern in SMALL_TALK_PATTERNS)

    def _should_log(self, behavior: dict[str, Any], text: str) -> bool:
        intent = str(behavior.get("intent") or "").strip()
        confidence = float(behavior.get("confidence") or 0.0)
        if not intent:
            return False
        if confidence <= BEHAVIOR_MIN_CONFIDENCE:
            return False
        if self._is_small_talk(text, behavior):
            return False
        return True

    async def _save_to_supabase(self, user_id: str, user_text: str, behavior: dict[str, Any], source: str) -> None:
        if not interaction_store.enabled or interaction_store.client is None:
            return

        payload = {
            "user_id": user_id,
            "source": source,
            "raw_text": user_text,
            "intent": behavior["intent"],
            "category": behavior["category"],
            "confidence": behavior["confidence"],
            "behavior_json": behavior,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        def _execute() -> None:
            interaction_store.client.table(BEHAVIOR_TABLE).insert(payload).execute()

        try:
            await asyncio.to_thread(_execute)
            log_debug(
                f"[BEHAVIOR] Logged behavior user_id={user_id} intent={behavior['intent']} "
                f"confidence={behavior['confidence']:.2f}"
            )
        except Exception as exc:
            log_error(f"BEHAVIOR Supabase insert failed: {exc}")


behavior_extractor = BehaviorExtractor()
