from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import os
import base64
import threading
from datetime import datetime, timezone, timedelta

from core.state import alert_queue
from core.stt_engine import speech_to_text
from core.brain import process_text, handle_alert_event
from core.tts_engine import text_to_speech
from core.proactive_agent import monitor_schedule
from core.memory_classifier import classify_and_store_background
from core.user_profile import update_user_profile
from integrations.supabase_store import log_interaction, get_default_user_id

app = FastAPI()

IST = timezone(timedelta(hours=5, minutes=30))

os.makedirs("temp_audio/incoming", exist_ok=True)
os.makedirs("temp_audio/outgoing", exist_ok=True)


def _resolve_user_id(user_id: str | None) -> str:
    resolved = (user_id or "").strip()
    return resolved if resolved else get_default_user_id()


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
        "audio": encoded_audio,
    }


# --- HEARTBEAT ENDPOINT ---
@app.get("/check-alerts")
async def check_alerts(background_tasks: BackgroundTasks):
    print("[HEARTBEAT] /check-alerts polled")
    if alert_queue:
        event_obj = alert_queue.pop(0)
        alert_text = handle_alert_event(event_obj)
        background_tasks.add_task(log_interaction, "system", "", alert_text)

        output_path = "temp_audio/outgoing/alert_voice.mp3"
        text_to_speech(alert_text, output_path)

        with open(output_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

        print(f"[PROACTIVE SENT]: {alert_text}")
        return {"has_alert": True, "message": alert_text, "audio": encoded_audio}

    return {"has_alert": False}


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

    aries_reply = await process_text(user_query, resolved_user_id)
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
        "audio": encoded_audio,
    }


if __name__ == "__main__":
    watcher_thread = threading.Thread(target=monitor_schedule, daemon=True)
    watcher_thread.start()
    print("[SYSTEM] Aries Proactive Watcher started.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
