"""Background memory classification using phi3 for personalization."""

import asyncio
import json
from typing import Any, Awaitable, Callable

import ollama
from integrations.supabase_store import get_default_user_id


async def classify_memory(user_text: str, aries_text: str) -> dict[str, Any]:
    """
    Classify conversation into FACT, PREFERENCE, or HISTORY using phi3 in background.
    Returns metadata dict with type and topic.

    This runs async but should be awaited as a background task to avoid blocking main flow.
    """
    try:
        prompt = f"""Classify this user message into ONE of: FACT, PREFERENCE, or HISTORY.

User said: "{user_text}"
Assistant replied: "{aries_text}"

Classify as:
- FACT: Personal info (name, location, age, job, etc.)
- PREFERENCE: User's likes/dislikes or style (short answers, detailed, food, music, etc.)
- HISTORY: Regular conversation, general questions

Respond ONLY with JSON like:
{{"type": "FACT", "topic": "location", "confidence": 0.95}}

Keep topic as single word or two words max."""

        response = await asyncio.to_thread(
            ollama.generate,
            model="phi3",
            prompt=prompt,
            stream=False,
        )

        output_text = response.get("response", "").strip()
        json_start = output_text.find("{")
        json_end = output_text.rfind("}") + 1

        if json_start >= 0 and json_end > json_start:
            result = json.loads(output_text[json_start:json_end])
            normalized_type = str(result.get("type", "HISTORY")).upper().strip()
            result["type"] = normalized_type if normalized_type in {"FACT", "PREFERENCE", "HISTORY"} else "HISTORY"
            result["topic"] = str(result.get("topic", "general")).strip().lower() or "general"
            print(f"[CLASSIFIER] Detected memory_type={result['type']} topic={result['topic']} confidence={result.get('confidence', 0.0)}")
            return result

        print(f"[CLASSIFIER] Could not parse JSON from: {output_text}")
        return {"type": "HISTORY", "topic": "general", "confidence": 0.5}

    except Exception as e:
        print(f"[CLASSIFIER ERROR] {e}")
        return {"type": "HISTORY", "topic": "general", "confidence": 0.0}


def get_importance_score(memory_type: str) -> float:
    """Map memory type to importance score."""
    mapping = {
        "FACT": 0.9,
        "PREFERENCE": 0.8,
        "HISTORY": 0.5,
    }
    return mapping.get(memory_type, 0.5)


async def classify_and_store_background(
    user_id: str | None,
    user_text: str,
    aries_text: str,
    store_callback: Callable[..., Awaitable[None]],
    update_profile_callback: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """
    Classify memory and update Supabase + user profile in background.
    Does not block main request.
    """
    try:
        resolved_user_id = (user_id or "").strip() or get_default_user_id()
        metadata = await classify_memory(user_text, aries_text)
        importance = get_importance_score(metadata.get("type", "HISTORY"))

        print(
            f"[PERSONALIZATION] user_id={resolved_user_id} "
            f"memory_type={metadata.get('type')} topic={metadata.get('topic')} importance={importance:.2f}"
        )

        await store_callback(
            user_id=resolved_user_id,
            user_text=user_text,
            aries_text=aries_text,
            metadata=metadata,
            importance=importance,
        )

        if update_profile_callback is not None:
            await update_profile_callback(
                user_id=resolved_user_id,
                metadata=metadata,
                user_text=user_text,
            )

    except Exception as e:
        print(f"[MEMORY CLASSIFIER] Background task failed: {e}")
