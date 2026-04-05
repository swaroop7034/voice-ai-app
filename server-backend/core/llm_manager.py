import asyncio
from datetime import datetime

import ollama
from integrations.supabase_store import get_recent_interactions

CHAT_HISTORY_LIMIT = 14
MAX_HISTORY = 5

# Per-user in-memory chat state. Each value is a list of role/content messages.
chat_history: dict[str, list[dict[str, str]]] = {}
_chat_history_lock = asyncio.Lock()


def _resolve_user_key(user_id: str | None) -> str:
    resolved = (user_id or "anonymous").strip()
    return resolved or "anonymous"


def _trim_history(messages: list[dict[str, str]], limit: int = CHAT_HISTORY_LIMIT) -> list[dict[str, str]]:
    return messages[-limit:] if len(messages) > limit else messages


async def get_recent_chat_history(user_id: str | None, limit: int = CHAT_HISTORY_LIMIT) -> list[dict[str, str]]:
    resolved_user_id = _resolve_user_key(user_id)

    should_hydrate = False
    async with _chat_history_lock:
        history = chat_history.get(resolved_user_id, [])
        if history:
            return [message.copy() for message in history[-limit:]]
        should_hydrate = True

    if should_hydrate:
        hydrated: list[dict[str, str]] = []
        rows = await get_recent_interactions(resolved_user_id, limit=max(1, limit // 2))

        # Rows come newest-first; rebuild chronological user/assistant message pairs.
        for row in reversed(rows):
            user_text = (row.get("user_text") or "").strip()
            aries_text = (row.get("aries_text") or "").strip()
            if user_text:
                hydrated.append({'role': 'user', 'content': user_text})
            if aries_text:
                hydrated.append({'role': 'assistant', 'content': aries_text})

        hydrated = _trim_history(hydrated, limit)

        async with _chat_history_lock:
            existing = chat_history.get(resolved_user_id, [])
            if not existing and hydrated:
                chat_history[resolved_user_id] = hydrated
                print(f"[LOCAL AI] Hydrated in-memory chat history from Supabase for user_id={resolved_user_id}, messages={len(hydrated)}")
            history = chat_history.get(resolved_user_id, [])
            return [message.copy() for message in history[-limit:]]


async def append_chat_message(user_id: str | None, role: str, content: str) -> list[dict[str, str]]:
    resolved_user_id = _resolve_user_key(user_id)
    async with _chat_history_lock:
        history = chat_history.setdefault(resolved_user_id, [])
        history.append({'role': role, 'content': content})
        chat_history[resolved_user_id] = _trim_history(history)
        return [message.copy() for message in chat_history[resolved_user_id]]


def _conversation_section(recent_history: list[dict[str, str]], limit: int = MAX_HISTORY) -> str:
    if not recent_history:
        return "* No recent conversation available."

    lines: list[str] = []
    for message in recent_history[-limit:]:
        role = str(message.get("role") or "user").capitalize()
        content = (message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"* {role}: {content[:160]}")
    return "\n".join(lines) if lines else "* No recent conversation available."


def _build_system_prompt(
    today_date: str,
    memory_context: str,
    profile_context: str = "",
    conversation_context: str = "",
    behavior_context: str = "",
) -> dict[str, str]:
    memory_section = memory_context.strip()
    profile_section = profile_context.strip()
    conversation_section = conversation_context.strip()
    behavior_section = behavior_context.strip()

    context_blocks: list[str] = []

    if profile_section:
        context_blocks.append(f"User Profile:\n{profile_section}")
    else:
        context_blocks.append("User Profile:\n* No explicit profile preferences yet.")

    if memory_section:
        context_blocks.append(f"Relevant Context:\n{memory_section}")
    else:
        context_blocks.append("Relevant Context:\n* No reliable memory context found.")

    context_blocks.append(f"Conversation:\n{conversation_section or '* No recent conversation available.'}")

    if behavior_section:
        context_blocks.append(f"User Behavior Insights:\n{behavior_section}")
    else:
        context_blocks.append("User Behavior Insights:\n* No stable behavior insights yet.")

    context_blocks.append(
        "Instruction:\n"
        "* Be accurate first, then adapt tone.\n"
        "* Always follow user preferences unless explicitly overridden by the user."
    )

    context_block = "\n\n".join(context_blocks)

    response_style_instruction = ""
    if profile_context and "short" in profile_context.lower():
        response_style_instruction = "Keep your answers concise and to the point. "
    elif profile_context and "detailed" in profile_context.lower():
        response_style_instruction = "Provide detailed explanations with examples. "

    return {
        'role': 'system',
        'content': (
            "You are ARIS, a concise local AI assistant inspired by JARVIS. "
            "TECHNICAL CONTEXT: You are running LOCALLY on the user's RTX 3050 GPU. "
            "PERSONALITY: Be slightly formal but witty. Refer to the user as 'Sir'. "
            f"{response_style_instruction}"
            "ANSWERING RULE: Answer correctly first, then add light personality only if it fits and never at the expense of accuracy. "
            f"CURRENT DATE: {today_date}. "
            "IMPORTANT: You are currently in the year 2026. "
            "SEARCH PROTOCOL: If the user asks about events in 2025 or 2026, current news, weather, live data, "
            "or details you do not have in your local offline training, you MUST respond strictly with SEARCH[query]. "
            "Do not guess. Do not be poetic. Just SEARCH. "
            "CONSTRAINTS: Keep final responses to 1 or 2 sentences max.\n\n"
            f"{context_block}"
        )
    }


def _format_prompt_preview(messages_to_send: list[dict[str, str]], limit: int = 2000) -> str:
    parts: list[str] = []
    for message in messages_to_send:
        role = message.get('role', 'unknown').upper()
        content = (message.get('content') or '').strip()
        if not content:
            continue
        parts.append(f"[{role}] {content}")

    preview = "\n".join(parts)
    if len(preview) > limit:
        return preview[:limit] + "... [truncated]"
    return preview


async def get_aries_response(
    user_text: str,
    user_id: str | None = None,
    *,
    memory_context: str = "",
    profile_context: str = "",
    behavior_context: str = "",
    recent_history: list[dict[str, str]] | None = None,
) -> str:
    """
    Communicates with Ollama using per-user sliding-window history, memories, and user profile.
    """
    try:
        resolved_user_id = _resolve_user_key(user_id)
        print(f"[LOCAL AI] Inference started for user_id={resolved_user_id}: {user_text}")

        today_date = datetime.now().strftime("%B %d, %Y")

        if recent_history is None:
            recent_history = await get_recent_chat_history(resolved_user_id, CHAT_HISTORY_LIMIT)
        else:
            recent_history = [message.copy() for message in recent_history[-CHAT_HISTORY_LIMIT:]]

        recent_history = recent_history[-MAX_HISTORY:]

        await append_chat_message(resolved_user_id, 'user', user_text)

        conversation_context = _conversation_section(recent_history, limit=MAX_HISTORY)
        system_prompt = _build_system_prompt(
            today_date,
            memory_context,
            profile_context,
            conversation_context,
            behavior_context,
        )
        messages_to_send = [system_prompt, {'role': 'user', 'content': user_text}]

        prompt_preview = _format_prompt_preview(messages_to_send)
        print(f"[LOCAL AI] Final prompt length={len(prompt_preview)} chars for user_id={resolved_user_id}")
        print(f"[LOCAL AI] Final prompt for user_id={resolved_user_id}:\n{prompt_preview}")

        response = await asyncio.to_thread(
            ollama.chat,
            model='llama3.2',
            messages=messages_to_send,
        )

        ai_message = response['message']['content']
        await append_chat_message(resolved_user_id, 'assistant', ai_message)

        print(f"[LOCAL AI] Response generated for user_id={resolved_user_id}.")
        return ai_message

    except Exception as e:
        print(f"[LOCAL AI ERROR] Connection to Ollama failed: {e}")
        return "My memory banks are currently inaccessible, Sir. Please check the local engine."