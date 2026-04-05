from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

import numpy as np
from dotenv import load_dotenv
from supabase import Client, create_client

from integrations.supabase_store import get_default_user_id
from logger import logger, log_error

load_dotenv()

VOICE_AUTH_TABLE = os.getenv("SUPABASE_VOICE_AUTH_TABLE", "voice_auth").strip() or "voice_auth"
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        log_error(f"VOICE_AUTH Supabase client init failed: {exc}")
        return None


def _embedding_from_row(row: dict[str, Any]) -> np.ndarray | None:
    embedding_value = row.get("embedding")
    if embedding_value is None:
        return None

    if isinstance(embedding_value, str):
        try:
            embedding_value = json.loads(embedding_value)
        except Exception:
            return None

    try:
        return np.asarray(embedding_value, dtype=np.float32)
    except Exception:
        return None


def get_embedding_from_supabase(user_id: str) -> np.ndarray | None:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured")

    resolved_user_id = (user_id or "").strip() or get_default_user_id()
    response = (
        client.table(VOICE_AUTH_TABLE)
        .select("embedding")
        .eq("user_id", resolved_user_id)
        .limit(1)
        .execute()
    )

    rows = list(getattr(response, "data", []) or [])
    if not rows:
        return None

    return _embedding_from_row(rows[0])


def save_embedding_to_supabase(user_id: str, embedding: np.ndarray) -> dict[str, object]:
    client = get_supabase_client()
    if client is None:
        return {
            "success": False,
            "reason": "supabase_not_configured",
            "message": "Supabase client is not configured",
        }

    resolved_user_id = (user_id or "").strip() or get_default_user_id()
    payload = {
        "user_id": resolved_user_id,
        "embedding": np.asarray(embedding, dtype=np.float32).tolist(),
    }

    try:
        existing_response = (
            client.table(VOICE_AUTH_TABLE)
            .select("user_id")
            .eq("user_id", resolved_user_id)
            .limit(1)
            .execute()
        )
        existing_rows = list(getattr(existing_response, "data", []) or [])

        if existing_rows:
            client.table(VOICE_AUTH_TABLE).update(payload).eq("user_id", resolved_user_id).execute()
        else:
            client.table(VOICE_AUTH_TABLE).insert(payload).execute()

        return {
            "success": True,
            "reason": "ok",
            "message": "Voice embedding stored successfully",
        }
    except Exception as exc:
        log_error(f"VOICE_AUTH failed to store embedding for user_id={resolved_user_id}: {exc}")
        return {
            "success": False,
            "reason": "supabase_write_failed",
            "message": "Failed to store voice embedding",
        }


def delete_embedding_from_supabase(user_id: str) -> dict[str, object]:
    client = get_supabase_client()
    if client is None:
        return {
            "success": False,
            "reason": "supabase_not_configured",
            "message": "Supabase client is not configured",
        }

    resolved_user_id = (user_id or "").strip() or get_default_user_id()

    try:
        existing_response = (
            client.table(VOICE_AUTH_TABLE)
            .select("user_id")
            .eq("user_id", resolved_user_id)
            .limit(1)
            .execute()
        )
        existing_rows = list(getattr(existing_response, "data", []) or [])
        if not existing_rows:
            return {
                "success": True,
                "reason": "not_found",
                "message": "No existing voice data found",
            }

        client.table(VOICE_AUTH_TABLE).delete().eq("user_id", resolved_user_id).execute()
        return {
            "success": True,
            "reason": "ok",
            "message": "Voice data deleted successfully",
        }
    except Exception as exc:
        log_error(f"VOICE_AUTH failed to delete embedding for user_id={resolved_user_id}: {exc}")
        return {
            "success": False,
            "reason": "supabase_delete_failed",
            "message": "Failed to delete voice embedding",
        }
