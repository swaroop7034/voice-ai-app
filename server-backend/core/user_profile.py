"""User profile management for personalization."""

from __future__ import annotations

import asyncio
from typing import Any

from supabase import Client, create_client
from dotenv import load_dotenv
import os
from core.intelligence_layer import conflict_resolver

load_dotenv()


class UserProfileManager:
    """Manages user profiles and preferences in Supabase."""

    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
        self.enabled = bool(self.url and self.key)
        self.client: Client | None = None

        if self.enabled:
            self.client = create_client(self.url, self.key)

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        """Fetch user profile from Supabase."""
        if not self.enabled or self.client is None:
            return self._default_profile(user_id)

        try:
            def _fetch():
                # Use limit(1) instead of single()/maybe_single() to avoid strict row coercion errors.
                response = (
                    self.client.table("user_profile")
                    .select("*")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )

                if response is None:
                    return {}

                rows = getattr(response, "data", None)
                if isinstance(rows, list) and rows:
                    return rows[0]
                if isinstance(rows, dict):
                    return rows
                return {}

            profile = await asyncio.to_thread(_fetch)
            if profile:
                return profile

            # No row exists yet for first-time users; create one once to avoid repeated misses.
            print(f"[PROFILE] No profile exists yet for {user_id}; creating default profile.")
            await self._update_profile_db(
                user_id=user_id,
                facts={},
                preferences={},
            )
            return self._default_profile(user_id)
        except Exception as e:
            print(f"[PROFILE] Failed to fetch profile for {user_id}: {e}")
            return self._default_profile(user_id)

    async def update_profile_from_memory(
        self,
        user_id: str,
        metadata: dict[str, Any],
        user_text: str,
    ) -> None:
        """Update user profile based on classified memory."""
        if not self.enabled or self.client is None:
            return

        memory_type = metadata.get("type", "HISTORY")
        topic = metadata.get("topic", "general")

        try:
            profile = await self.get_profile(user_id)
            facts = profile.get("facts", {})
            preferences = profile.get("preferences", {})

            if memory_type == "FACT":
                facts[topic] = user_text
                print(f"[PROFILE] Updated FACT: {topic} -> {user_text[:50]}")

            elif memory_type == "PREFERENCE":
                pref_key, pref_value = self._extract_preference(topic, user_text)
                preferences[pref_key] = pref_value
                preferences["latest_response_style"] = pref_value if pref_key == "response_style" else preferences.get("latest_response_style", "")
                preferences, resolved_notes = conflict_resolver(preferences)
                if resolved_notes:
                    print(f"[PROFILE] Conflicts resolved for {user_id}: {', '.join(resolved_notes)}")
                print(f"[PROFILE] Updated PREFERENCE: {topic} -> {user_text[:50]}")

            await self._update_profile_db(
                user_id=user_id,
                facts=facts,
                preferences=preferences,
            )
        except Exception as e:
            print(f"[PROFILE] Failed to update profile: {e}")

    def _extract_preference(self, topic: str, user_text: str) -> tuple[str, str]:
        """Normalize known preference styles for consistent profile dominance."""
        text = user_text.lower()
        if any(word in text for word in ["short", "concise", "brief"]):
            return "response_style", "short"
        if any(word in text for word in ["detail", "detailed", "explain more", "long"]):
            return "response_style", "detailed"
        return topic, user_text

    async def _update_profile_db(
        self,
        user_id: str,
        facts: dict[str, Any],
        preferences: dict[str, Any],
    ) -> None:
        """Write profile updates to Supabase."""
        if not self.enabled or self.client is None:
            return

        try:
            def _upsert():
                self.client.table("user_profile").upsert({
                    "user_id": user_id,
                    "facts": facts,
                    "preferences": preferences,
                }).execute()

            await asyncio.to_thread(_upsert)
            print(f"[PROFILE] Saved profile for {user_id}")
        except Exception as e:
            print(f"[PROFILE] Failed to save profile: {e}")

    def _default_profile(self, user_id: str = "anonymous") -> dict[str, Any]:
        """Return default profile structure."""
        return {
            "user_id": user_id,
            "facts": {},
            "preferences": {},
            "communication_style": "balanced",
        }

    def format_profile_context(self, profile: dict[str, Any]) -> str:
        """Format profile into readable context for LLM."""
        lines = []

        facts = profile.get("facts", {})
        if facts:
            lines.append("## About the user:")
            for key, value in facts.items():
                lines.append(f"* {key.capitalize()}: {value}")

        prefs = profile.get("preferences", {})
        if prefs:
            lines.append("\n## User preferences:")
            for key, value in prefs.items():
                if key == "latest_response_style":
                    continue
                lines.append(f"* {key.capitalize()}: {value}")

        return "\n".join(lines) if lines else ""


# Singleton instance
profile_manager = UserProfileManager()


async def get_user_profile(user_id: str) -> dict[str, Any]:
    """Public API to fetch user profile."""
    return await profile_manager.get_profile(user_id)


async def update_user_profile(
    user_id: str,
    metadata: dict[str, Any],
    user_text: str,
) -> None:
    """Public API to update profile from classified memory."""
    await profile_manager.update_profile_from_memory(user_id, metadata, user_text)


def format_profile_section(profile: dict[str, Any]) -> str:
    """Public API to format profile into prompt section."""
    return profile_manager.format_profile_context(profile)
