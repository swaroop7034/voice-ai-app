from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import os
import base64
import threading
import asyncio
from datetime import datetime, timezone, timedelta

from core.state import alert_queue
from core.stt_engine import speech_to_text
from core.brain import process_text, handle_alert_event
from core.tts_engine import text_to_speech
from core.proactive_agent import monitor_schedule
from core.memory_classifier import classify_and_store_background
from core.user_profile import update_user_profile
from core.behavior_analyzer import maybe_run_behavior_cycle, fetch_unseen_suggestions, mark_suggestions_seen
from core.voice_auth import enroll_voice_for_user, verify_safe_folder_access
from integrations.supabase_store import log_interaction, get_default_user_id

app = FastAPI()

IST = timezone(timedelta(hours=5, minutes=30))
SAFE_FOLDER_KEYWORD = "safe folder"

os.makedirs("temp_audio/incoming", exist_ok=True)
os.makedirs("temp_audio/outgoing", exist_ok=True)


def _resolve_user_id(user_id: str | None) -> str:
    resolved = (user_id or "").strip()
    return resolved if resolved else get_default_user_id()


async def _auto_generate_and_attach_suggestion(
    resolved_user_id: str,
    user_query: str,
    aries_reply: str,
) -> tuple[str, list[str], bool]:
    """
    Generate and deliver suggestion in the same interaction response.
    Returns (updated_reply, delivered_suggestions, personalization_completed_inline).
    """
    suggestion_texts: list[str] = []
    try:
        await asyncio.wait_for(
            classify_and_store_background(
                user_id=resolved_user_id,
                user_text=user_query,
                aries_text=aries_reply,
                store_callback=log_interaction,
                update_profile_callback=update_user_profile,
            ),
            timeout=3.0,
        )
        await asyncio.wait_for(maybe_run_behavior_cycle(resolved_user_id, interaction_step=1), timeout=2.5)

        suggestions = await fetch_unseen_suggestions(resolved_user_id, limit=1)
        suggestion_texts = [str(item.get("suggestion_text") or "") for item in suggestions if item.get("suggestion_text")]
        if suggestions:
            suggestion_ids = [int(item["id"]) for item in suggestions if item.get("id") is not None]
            await mark_suggestions_seen(suggestion_ids)
            print(f"[SUGGESTION] Immediate delivery to user_id={resolved_user_id}: {suggestion_texts}")
            aries_reply = f"{aries_reply} Also, {suggestion_texts[0]}"

        return aries_reply, suggestion_texts, True

    except Exception as exc:
        print(f"[SUGGESTION] Immediate generation fallback for user_id={resolved_user_id}: {exc}")
        return aries_reply, suggestion_texts, False


async def _trigger_background_personalization(user_id: str | None, user_text: str, aries_reply: str):
    """
    Trigger memory classification and profile update in background without waits.
    """
    try:
        await classify_and_store_background(
            user_id=user_id,
            user_text=user_text,
            aries_text=aries_reply,
            store_callback=log_interaction,
            update_profile_callback=update_user_profile,
        )
        await maybe_run_behavior_cycle(user_id)
    except Exception as e:
        print(f"[BACKGROUND PERSONALIZATION] Error: {e}")


class TextInput(BaseModel):
    text: str
    user_id: str | None = None


# --- TEXT INPUT ENDPOINT (used by slot picker in frontend) ---
@app.post("/text-input")
async def handle_text_input(background_tasks: BackgroundTasks, body: TextInput):
    """Accepts raw text directly - no STT needed. Used for slot selection UI."""
    user_query = body.text.strip()
    if not user_query:
        return JSONResponse({"status": "error", "message": "Empty input"})

    resolved_user_id = _resolve_user_id(body.user_id)
    print(f"[REQUEST] /text-input user_id={resolved_user_id} text={user_query}")

    aries_reply = await process_text(user_query, resolved_user_id)
    aries_reply, suggestion_texts, completed_inline = await _auto_generate_and_attach_suggestion(
        resolved_user_id,
        user_query,
        aries_reply,
    )

    if not completed_inline:
        background_tasks.add_task(
            _trigger_background_personalization,
            resolved_user_id,
            user_query,
            aries_reply,
        )

    output_path = "temp_audio/outgoing/response.mp3"
    text_to_speech(aries_reply, output_path)

    with open(output_path, "rb") as audio_file:
        encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

    print(f"\n[USER]: {user_query}")
    print(f"[ARIES]: {aries_reply}\n")

    return {
        "status": "success",
        "user_text": user_query,
        "aries_text": aries_reply,
        "suggestions": suggestion_texts,
        "has_suggestions": bool(suggestion_texts),
        "audio": encoded_audio,
    }


# --- HEARTBEAT ENDPOINT ---
@app.get("/check-alerts")
async def check_alerts(background_tasks: BackgroundTasks, user_id: str | None = None):
    print("[HEARTBEAT] /check-alerts polled")
    resolved_user_id = _resolve_user_id(user_id)
    suggestions = await fetch_unseen_suggestions(resolved_user_id, limit=3)

    if suggestions:
        suggestion_ids = [int(item["id"]) for item in suggestions if item.get("id") is not None]
        await mark_suggestions_seen(suggestion_ids)
        suggestion_texts = [str(item.get("suggestion_text") or "") for item in suggestions if item.get("suggestion_text")]
        print(f"[SUGGESTION] Delivered {len(suggestion_texts)} suggestions to user_id={resolved_user_id}")
    else:
        suggestion_texts = []

    if alert_queue:
        event_obj = alert_queue.pop(0)
        alert_text = handle_alert_event(event_obj)
        background_tasks.add_task(log_interaction, "system", "", alert_text)

        output_path = "temp_audio/outgoing/alert_voice.mp3"
        text_to_speech(alert_text, output_path)

        with open(output_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

        print(f"[PROACTIVE SENT]: {alert_text}")
        return {
            "has_alert": True,
            "message": alert_text,
            "audio": encoded_audio,
            "suggestions": suggestion_texts,
            "has_suggestions": bool(suggestion_texts),
        }

    return {
        "has_alert": False,
        "suggestions": suggestion_texts,
        "has_suggestions": bool(suggestion_texts),
    }


# --- STANDARD VOICE CHAT ---
@app.post("/chat")
async def handle_voice_chat(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str | None = Form(default=None),
):
    input_path = f"temp_audio/incoming/{file.filename}"
    with open(input_path, "wb") as f:
        f.write(await file.read())

    user_query = speech_to_text(input_path)
    if not user_query:
        return JSONResponse({"status": "error", "message": "No speech detected"})

    resolved_user_id = _resolve_user_id(user_id)
    print(f"[REQUEST] /chat user_id={resolved_user_id} text={user_query}")

    if user_query.strip().lower() == SAFE_FOLDER_KEYWORD:
        result = verify_safe_folder_access(resolved_user_id, input_path)

        if result.get("status") == "enrolled":
            output_path = "temp_audio/outgoing/safe_folder_enrolled.mp3"
            text_to_speech("Voice registered successfully", output_path)

            with open(output_path, "rb") as audio_file:
                encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

            return {
                "status": "enrolled",
                "user_text": SAFE_FOLDER_KEYWORD,
                "message": "Voice registered successfully",
                "audio": encoded_audio,
            }

        if result.get("access_granted"):
            output_path = "temp_audio/outgoing/safe_folder_open.mp3"
            text_to_speech("Access granted", output_path)

            with open(output_path, "rb") as audio_file:
                encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

            return {
                "status": "success",
                "user_text": SAFE_FOLDER_KEYWORD,
                "access_granted": True,
                "message": "Access granted",
                "audio": encoded_audio,
            }

        reason = result.get("reason")
        message = (
            "Wrong keyword" if reason == "keyword_mismatch"
            else "Voice not recognized" if reason == "voice_mismatch"
            else "Could not verify voice"
        )
        output_path = "temp_audio/outgoing/safe_folder_denied.mp3"
        text_to_speech(message, output_path)

        with open(output_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

        return {
            "status": "error",
            "user_text": SAFE_FOLDER_KEYWORD,
            "access_granted": False,
            "reason": reason,
            "message": message,
            "audio": encoded_audio,
        }

    aries_reply = await process_text(user_query, resolved_user_id)
    aries_reply, suggestion_texts, completed_inline = await _auto_generate_and_attach_suggestion(
        resolved_user_id,
        user_query,
        aries_reply,
    )

    if not completed_inline:
        background_tasks.add_task(
            _trigger_background_personalization,
            resolved_user_id,
            user_query,
            aries_reply,
        )

    output_path = "temp_audio/outgoing/response.mp3"
    text_to_speech(aries_reply, output_path)

    with open(output_path, "rb") as audio_file:
        encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

    print(f"\n[USER]: {user_query}")
    print(f"[ARIES]: {aries_reply}\n")

    return {
        "status": "success",
        "user_text": user_query,
        "aries_text": aries_reply,
        "suggestions": suggestion_texts,
        "has_suggestions": bool(suggestion_texts),
        "audio": encoded_audio,
    }


# --- SAFE FOLDER VOICE ENROLLMENT ---
@app.post("/safe-folder/enroll")
async def enroll_safe_folder_voice(
    file: UploadFile = File(...),
    user_id: str | None = Form(default=None),
):
    resolved_user_id = _resolve_user_id(user_id)
    input_path = f"temp_audio/incoming/safe_enroll_{resolved_user_id}_{file.filename}"

    with open(input_path, "wb") as f:
        f.write(await file.read())

    try:
        result = enroll_voice_for_user(resolved_user_id, input_path)
        status_code = 201 if result.get("status") == "enrolled" else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "user_id": resolved_user_id,
                **result,
            },
        )
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


# --- SAFE FOLDER ACCESS CHECK ---
@app.post("/safe-folder/access")
async def safe_folder_access(
    file: UploadFile = File(...),
    user_id: str | None = Form(default=None),
):
    resolved_user_id = _resolve_user_id(user_id)
    input_path = f"temp_audio/incoming/safe_access_{resolved_user_id}_{file.filename}"

    with open(input_path, "wb") as f:
        f.write(await file.read())

    try:
        result = verify_safe_folder_access(resolved_user_id, input_path)
        if result.get("status") == "enrolled":
            status_code = 201
        elif result.get("access_granted"):
            status_code = 200
        else:
            status_code = 401 if result.get("reason") in {"keyword_mismatch", "voice_mismatch", "stt_failed", "supabase_read_failed"} else 400

        return JSONResponse(
            status_code=status_code,
            content={
                "user_id": resolved_user_id,
                **result,
            },
        )
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


if __name__ == "__main__":
    watcher_thread = threading.Thread(target=monitor_schedule, daemon=True)
    watcher_thread.start()
    print("[SYSTEM] Aries Proactive Watcher started.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
