from fastapi import FastAPI, UploadFile, File, Request
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

app = FastAPI()

IST = timezone(timedelta(hours=5, minutes=30))

os.makedirs("temp_audio/incoming", exist_ok=True)
os.makedirs("temp_audio/outgoing", exist_ok=True)


class TextInput(BaseModel):
    text: str


# --- TEXT INPUT ENDPOINT (used by slot picker in frontend) ---
@app.post("/text-input")
async def handle_text_input(body: TextInput):
    """Accepts raw text directly — no STT needed. Used for slot selection UI."""
    user_query = body.text.strip()
    if not user_query:
        return JSONResponse({"status": "error", "message": "Empty input"})

    aries_reply = process_text(user_query)

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
async def check_alerts():
    if alert_queue:
        event_obj  = alert_queue.pop(0)
        alert_text = handle_alert_event(event_obj)

        output_path = "temp_audio/outgoing/alert_voice.mp3"
        text_to_speech(alert_text, output_path)

        with open(output_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

        print(f"[PROACTIVE SENT]: {alert_text}")
        return {"has_alert": True, "message": alert_text, "audio": encoded_audio}

    return {"has_alert": False}


# --- STANDARD VOICE CHAT ---
@app.post("/chat")
async def handle_voice_chat(file: UploadFile = File(...)):
    input_path = f"temp_audio/incoming/{file.filename}"
    with open(input_path, "wb") as f:
        f.write(await file.read())

    user_query = speech_to_text(input_path)
    if not user_query:
        return JSONResponse({"status": "error", "message": "No speech detected"})

    aries_reply = process_text(user_query)

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