"""Supabase-backed storage for Aries chat interactions."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

import ollama
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

DEFAULT_TABLE_NAME = "inetartction"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_SIMILARITY_THRESHOLD = 0.55
DEFAULT_MEMORY_LIMIT = 5
DEFAULT_MEMORY_CHAR_LIMIT = 1200


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def _env_int(name: str, default: int) -> int:
    value = _env(name, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _env(name, str(default))
    try:
        return float(value)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def _default_user_id() -> str:
    override = _env("SUPABASE_DEFAULT_USER_ID")
    if override:
        return override

    try:
        from tools.calendar_module import get_primary_calendar_user_id

        resolved_user_id = get_primary_calendar_user_id()
        if resolved_user_id:
            return resolved_user_id
    except Exception as exc:
        print(f"[SUPABASE] Failed to resolve Gmail user id: {exc}")

    return "anonymous"


class SupabaseInteractionStore:
    def __init__(self) -> None:
        self.url = _env("SUPABASE_URL")
        self.key = _env("SUPABASE_SERVICE_ROLE_KEY") or _env("SUPABASE_ANON_KEY")
        self.table_name = _env("SUPABASE_INTERACTION_TABLE", DEFAULT_TABLE_NAME)
        self.embedding_model = _env("SUPABASE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        self.similarity_threshold = _env_float("SUPABASE_SIMILARITY_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD)
        self.memory_limit = _env_int("SUPABASE_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
        self.memory_char_limit = _env_int("SUPABASE_MEMORY_CHAR_LIMIT", DEFAULT_MEMORY_CHAR_LIMIT)
        self.enabled = bool(self.url and self.key)
        self.client: Client | None = None

        if self.enabled:
            self.client = create_client(self.url, self.key)

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.lower().split())

    def _score_from_row(self, row: dict[str, Any]) -> float:
        for key in ("similarity", "score", "match_score", "embedding_score"):
            value = row.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    def _row_to_memory(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id", ""),
            "user_text": row.get("user_text", ""),
            "aries_text": row.get("aries_text", ""),
            "created_at": row.get("created_at"),
            "metadata": row.get("metadata"),
            "importance": float(row.get("importance") or 0.5),
            "similarity": self._score_from_row(row),
            "recency_score": float(row.get("recency_score") or 0.0),
            "score": float(row.get("score") or row.get("rank_score") or self._score_from_row(row)),
        }

    def _extract_keywords(self, query: str) -> list[str]:
        stop_words = {
            "the", "and", "for", "with", "that", "this", "from", "have", "what", "when",
            "where", "who", "why", "how", "tell", "show", "please", "about", "into", "your",
            "you", "are", "was", "were", "can", "could", "would", "should", "then", "than",
            "today", "tomorrow", "meeting", "schedule", "add", "set", "plan", "remind",
        }
        tokens = re.findall(r"[a-zA-Z0-9']+", query.lower())
        keywords: list[str] = []
        for token in tokens:
            if len(token) < 4 or token in stop_words:
                continue
            if token not in keywords:
                keywords.append(token)
        return keywords[:5]

    def _build_embedding_sync(self, text: str) -> list[float] | None:
        cleaned_text = text.strip() or "system interaction"

        try:
            response = ollama.embeddings(model=self.embedding_model, prompt=cleaned_text)
            embedding = list(response.embedding)
            print(f"[SUPABASE] Embedding length: {len(embedding)}")
            return embedding
        except Exception as exc:
            print(f"[SUPABASE] Embedding generation failed: {exc}")
            return None

    async def _embed_query(self, text: str) -> list[float] | None:
        return await asyncio.to_thread(self._build_embedding_sync, text)

    async def _call_rpc(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.client is None:
            return []

        def _execute() -> list[dict[str, Any]]:
            response = self.client.rpc("match_memory", params).execute()
            rows = list(response.data or [])
            print(f"[SUPABASE] RPC response rows: {rows}")
            return rows

        return await asyncio.to_thread(_execute)

    def _format_memory_block(self, memories: list[dict[str, Any]]) -> str:
        if not memories:
            return ""

        lines: list[str] = []
        total_chars = 0

        for memory in memories[: self.memory_limit]:
            user_text = (memory.get("user_text") or "").strip()
            aries_text = (memory.get("aries_text") or "").strip()
            if not user_text and not aries_text:
                continue

            block = f"* User: {user_text}\n  ARIS: {aries_text}"
            if total_chars + len(block) > self.memory_char_limit:
                break

            lines.append(block)
            total_chars += len(block)

        return "\n\n".join(lines)

    async def _keyword_search(self, query: str, user_id: str | None, limit: int) -> list[dict[str, Any]]:
        if not self.enabled or self.client is None:
            return []

        resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()
        keywords = self._extract_keywords(query)
        if not keywords:
            keywords = [query.strip()]

        async def _search_token(token: str) -> list[dict[str, Any]]:
            pattern = f"%{token}%"

            def _execute() -> list[dict[str, Any]]:
                response = (
                    self.client.table(self.table_name)
                    .select("id,user_id,user_text,aries_text,created_at")
                    .eq("user_id", resolved_user_id)
                    .ilike("user_text", pattern)
                    .order("created_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                return list(response.data or [])

            return await asyncio.to_thread(_execute)

        rows = await asyncio.gather(*(_search_token(token) for token in keywords[:3]))
        merged: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        for batch in rows:
            for row in batch:
                row_id = row.get("id")
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                merged.append({
                    **row,
                    "similarity": 0.0,
                    "recency_score": 0.0,
                    "score": 0.0,
                })

        merged.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return merged[:limit]

    async def search_similar(
        self,
        query: str,
        user_id: str | None,
        limit: int = 5,
        threshold: float = 0.75,
    ) -> list[dict[str, Any]]:
        if not self.enabled or self.client is None:
            print("[SUPABASE] Skipping vector search because Supabase is not configured.")
            return []

        resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()
        print(f"[SUPABASE] Searching memory for user_id={resolved_user_id}")
        query_embedding = await self._embed_query(query)
        if not query_embedding:
            print("[SUPABASE] Query embedding generation returned None.")
            return []

        try:
            raw_rows = await self._call_rpc(
                {
                    "query_embedding": query_embedding,
                    "match_count": limit,
                    "user_id": resolved_user_id,
                }
            )
        except Exception as exc:
            print(f"[SUPABASE] vector search RPC failed: {exc}")
            return []

        memories = [self._row_to_memory(row) for row in raw_rows]
        memories = [item for item in memories if item["similarity"] >= threshold]
        memories.sort(key=lambda item: item.get("score", item["similarity"]), reverse=True)

        if not memories:
            print(f"[SUPABASE] No vector memories returned for user {resolved_user_id}; falling back to keyword search.")
            memories = await self._keyword_search(query=query, user_id=resolved_user_id, limit=limit)

        print(f"[SUPABASE] Retrieved {len(memories)} memory candidates for user {resolved_user_id}")
        for index, memory in enumerate(memories, start=1):
            print(
                f"[SUPABASE] Match {index}: similarity={memory.get('similarity', 0.0):.4f} | "
                f"score={memory.get('score', 0.0):.4f} | user_id={resolved_user_id} | "
                f"User={memory['user_text']} | AI={memory['aries_text']}"
            )

        return memories[:limit]

    async def build_memory_context(
        self,
        query: str,
        user_id: str | None,
        limit: int | None = None,
        threshold: float | None = None,
    ) -> str:
        similar_memories = await self.search_similar(
            query=query,
            user_id=user_id,
            limit=limit or self.memory_limit,
            threshold=threshold if threshold is not None else self.similarity_threshold,
        )
        context = self._format_memory_block(similar_memories)
        if context:
            print(f"[SUPABASE] Memory context injected:\n{context}")
        return context

    async def get_recent_interactions(self, user_id: str | None, limit: int = 7) -> list[dict[str, Any]]:
        """Fetch most recent interaction rows for a user, newest first."""
        if not self.enabled or self.client is None:
            return []

        resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()

        def _execute() -> list[dict[str, Any]]:
            response = (
                self.client.table(self.table_name)
                .select("user_text,aries_text,created_at")
                .eq("user_id", resolved_user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return list(getattr(response, "data", []) or [])

        try:
            rows = await asyncio.to_thread(_execute)
            print(f"[SUPABASE] Loaded {len(rows)} recent interactions for user_id={resolved_user_id}")
            return rows
        except Exception as exc:
            print(f"[SUPABASE] Failed loading recent interactions for {resolved_user_id}: {exc}")
            return []

    async def _is_duplicate(self, user_id: str, user_text: str, aries_text: str) -> bool:
        combined_query = f"User: {user_text}\nAI: {aries_text}".strip()
        nearby_memories = await self.search_similar(
            query=combined_query,
            user_id=user_id,
            limit=3,
            threshold=max(self.similarity_threshold, 0.95),
        )

        normalized_user_text = self._normalize_text(user_text)
        normalized_aries_text = self._normalize_text(aries_text)

        for memory in nearby_memories:
            if (
                self._normalize_text(memory.get("user_text", "")) == normalized_user_text
                and self._normalize_text(memory.get("aries_text", "")) == normalized_aries_text
            ):
                print(f"[SUPABASE] Duplicate memory skipped for user {user_id}")
                return True

        return False

    async def log_interaction(
        self,
        user_id: str | None,
        user_text: str,
        aries_text: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> None:
        if not self.enabled or self.client is None:
            print("[SUPABASE] Skipping interaction log because Supabase is not configured.")
            return

        resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()
        print(f"[SUPABASE] Logging interaction for user_id={resolved_user_id}")

        if await self._is_duplicate(resolved_user_id, user_text, aries_text):
            return

        embedding_source = f"User: {user_text}\nAI: {aries_text}".strip()
        embedding = await self._embed_query(embedding_source)
        if embedding is None:
            print("[SUPABASE] Embedding is None, retrying once before insert.")
            embedding = await self._embed_query(embedding_source)

        if embedding is None:
            print("[SUPABASE] Skipping insert because embedding generation failed twice.")
            return

        payload: dict[str, Any] = {
            "user_id": resolved_user_id,
            "user_text": user_text or "",
            "aries_text": aries_text or "",
            "embedding": embedding,
            "metadata": metadata or {},
            "importance": importance,
        }

        try:
            await asyncio.to_thread(lambda: self.client.table(self.table_name).insert(payload).execute())
            print(f"[SUPABASE] Stored interaction for {resolved_user_id} (type={metadata.get('type') if metadata else 'UNKNOWN'}, importance={importance:.2f})")
        except Exception as exc:
            print(f"[SUPABASE] Failed to store interaction: {exc}")


interaction_store = SupabaseInteractionStore()


async def log_interaction(
    user_id: str | None,
    user_text: str,
    aries_text: str,
    metadata: dict[str, Any] | None = None,
    importance: float = 0.5,
) -> None:
    await interaction_store.log_interaction(user_id, user_text, aries_text, metadata, importance)


async def search_similar(query: str, user_id: str | None, limit: int = 5) -> list[dict[str, Any]]:
    return await interaction_store.search_similar(query=query, user_id=user_id, limit=limit)


async def build_memory_context(query: str, user_id: str | None, limit: int = 5) -> str:
    return await interaction_store.build_memory_context(query=query, user_id=user_id, limit=limit)


async def get_recent_interactions(user_id: str | None, limit: int = 7) -> list[dict[str, Any]]:
    return await interaction_store.get_recent_interactions(user_id=user_id, limit=limit)


def get_default_user_id() -> str:
    return _default_user_id()


def _normalize_event_datetime(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    # Google all-day events are date-only. Store as UTC midnight for consistency.
    if "T" not in value:
        return f"{value}T00:00:00+00:00"
    if value.endswith("Z"):
        return value.replace("Z", "+00:00")
    return value


def _calendar_event_payload(user_id: str, event: dict[str, Any], source: str) -> dict[str, Any]:
    start = event.get("start") or {}
    end = event.get("end") or {}

    start_raw = start.get("dateTime") or start.get("date")
    end_raw = end.get("dateTime") or end.get("date")

    event_id = str(event.get("id") or "").strip()
    summary = str(event.get("summary") or "Untitled Event").strip()

    # Fallback key if Google event id is missing.
    if not event_id:
        event_id = f"local::{summary}::{start_raw or 'unknown'}"

    attendees = event.get("attendees")
    if not isinstance(attendees, list):
        attendees = []

    organizer_email = ""
    organizer = event.get("organizer") or {}
    if isinstance(organizer, dict):
        organizer_email = str(organizer.get("email") or "").strip()

    payload: dict[str, Any] = {
        "user_id": user_id,
        "google_event_id": event_id,
        "source": source,
        "summary": summary,
        "description": str(event.get("description") or "").strip() or None,
        "location": str(event.get("location") or "").strip() or None,
        "status": str(event.get("status") or "confirmed").strip() or "confirmed",
        "start_time": _normalize_event_datetime(start_raw),
        "end_time": _normalize_event_datetime(end_raw),
        "event_timezone": str(start.get("timeZone") or end.get("timeZone") or "Asia/Kolkata").strip(),
        "is_all_day": bool(start.get("date") and not start.get("dateTime")),
        "attendees": attendees,
        "organizer_email": organizer_email or None,
        "html_link": str(event.get("htmlLink") or "").strip() or None,
        "updated_at_gcal": _normalize_event_datetime(str(event.get("updated") or "").strip() or None),
        "last_synced_at": datetime.utcnow().isoformat() + "Z",
        "raw_event": event,
    }
    return payload


def upsert_calendar_event_sync(user_id: str | None, event: dict[str, Any], source: str = "google_calendar_detected") -> bool:
    """Sync a single calendar event into Supabase table `calendar_events`."""
    if not interaction_store.enabled or interaction_store.client is None:
        return False

    resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()
    payload = _calendar_event_payload(resolved_user_id, event, source)

    try:
        interaction_store.client.table("calendar_events").upsert(
            payload,
            on_conflict="user_id,google_event_id",
        ).execute()
        print(
            f"[SUPABASE] Calendar event synced user_id={resolved_user_id} "
            f"event_id={payload.get('google_event_id')} source={source}"
        )
        return True
    except Exception as exc:
        print(f"[SUPABASE] Failed syncing calendar event: {exc}")
        return False


def sync_calendar_events_sync(
    user_id: str | None,
    events: list[dict[str, Any]],
    source: str = "google_calendar_detected",
) -> int:
    """Bulk sync calendar events into Supabase table `calendar_events`."""
    if not interaction_store.enabled or interaction_store.client is None:
        return 0
    if not events:
        return 0

    resolved_user_id = user_id.strip() if user_id and user_id.strip() else _default_user_id()
    payloads = [_calendar_event_payload(resolved_user_id, event, source) for event in events]

    try:
        interaction_store.client.table("calendar_events").upsert(
            payloads,
            on_conflict="user_id,google_event_id",
        ).execute()
        print(
            f"[SUPABASE] Synced {len(payloads)} calendar events "
            f"for user_id={resolved_user_id} source={source}"
        )
        return len(payloads)
    except Exception as exc:
        print(f"[SUPABASE] Failed bulk syncing calendar events: {exc}")
        return 0
