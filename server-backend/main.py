from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn
import os
import base64
import threading
import asyncio
import time
from datetime import datetime, timezone, timedelta

from logger import log_error, log_request, log_response, log_step

from core.state import alert_queue
from core.stt_engine import speech_to_text
from core.brain import process_text, handle_alert_event, _classify_intent_background
from core.tts_engine import text_to_speech
from core.proactive_agent import monitor_schedule
from core.behavior import behavior_extractor
from core.voice_auth import (
    enroll_voice_for_user,
    handle_voice_reset,
    is_safe_folder_command,
    is_voice_reset_command,
    is_voice_reset_confirmation,
    verify_safe_folder_access,
)
from integrations.supabase_store import log_interaction, get_default_user_id
from core.vault_storage import (
    save_vault_file,
    list_vault_files,
    get_vault_storage_stats,
    get_vault_file_path,
    delete_vault_file,
)

app = FastAPI()

IST = timezone(timedelta(hours=5, minutes=30))
SAFE_FOLDER_KEYWORD = "private files"
VOICE_RESET_STATE: dict[str, str] = {}

os.makedirs("temp_audio/incoming", exist_ok=True)
os.makedirs("temp_audio/outgoing", exist_ok=True)


async def _run_behavior_log_background(user_id: str | None, user_text: str, source: str) -> None:
    try:
        await behavior_extractor.process_and_log(user_id=user_id, user_text=user_text, source=source)
    except Exception as exc:
        log_error(f"BEHAVIOR background logging failed: {exc}")


def _resolve_user_id(user_id: str | None) -> str:
    resolved = (user_id or "").strip()
    return resolved if resolved else get_default_user_id()


def _synthesize_audio_base64(text: str, output_path: str) -> str:
    text_to_speech(text, output_path)
    with open(output_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode("utf-8")


async def _synthesize_audio_base64_async(text: str, output_path: str) -> str:
    t0 = time.perf_counter()
    encoded_audio = await asyncio.to_thread(_synthesize_audio_base64, text, output_path)
    log_step("TTS_COMPLETED", int((time.perf_counter() - t0) * 1000))
    return encoded_audio


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
    log_request(f"User: {user_query}")
    background_tasks.add_task(_run_behavior_log_background, resolved_user_id, user_query, "/text-input")

    brain_start = time.perf_counter()
    aries_reply = await process_text(user_query, resolved_user_id)
    log_step("LLM_COMPLETED", int((time.perf_counter() - brain_start) * 1000))
    suggestion_texts: list[str] = []
    
    # Spawn background phi3 intent classification (non-blocking, for learning/logging)
    background_tasks.add_task(_classify_intent_background, user_query)

    output_path = "temp_audio/outgoing/response.mp3"
    encoded_audio = await _synthesize_audio_base64_async(aries_reply, output_path)

    log_step("RESPONSE_SENT")
    log_response(f"ARIS: {aries_reply}")

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
    suggestion_texts: list[str] = []

    if alert_queue:
        event_obj = alert_queue.pop(0)
        alert_text = handle_alert_event(event_obj)
        background_tasks.add_task(log_interaction, "system", "", alert_text)

        output_path = "temp_audio/outgoing/alert_voice.mp3"
        text_to_speech(alert_text, output_path)

        with open(output_path, "rb") as audio_file:
            encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

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

    stt_start = time.perf_counter()
    user_query = await asyncio.to_thread(speech_to_text, input_path)
    log_step("STT_COMPLETED", int((time.perf_counter() - stt_start) * 1000))
    if not user_query:
        return JSONResponse({"status": "error", "message": "No speech detected"})

    resolved_user_id = _resolve_user_id(user_id)
    log_request(f"User: {user_query}")
    background_tasks.add_task(_run_behavior_log_background, resolved_user_id, user_query, "/chat")

    reset_state = VOICE_RESET_STATE.get(resolved_user_id)
    if reset_state == "awaiting_confirmation":
        if is_voice_reset_confirmation(user_query):
            reset_prepare = handle_voice_reset(resolved_user_id)
            if reset_prepare.get("status") == "pending":
                VOICE_RESET_STATE[resolved_user_id] = "awaiting_new_voice"
                prompt = str(reset_prepare.get("message") or "Please say a sentence to register your new voice.")
                output_path = "temp_audio/outgoing/voice_reset_ready.mp3"
                encoded_audio = _synthesize_audio_base64(prompt, output_path)

                log_step("VOICE_RESET_CONFIRMED")
                log_step("RESPONSE_SENT")
                log_response(f"ARIS: {prompt}")
                return {
                    "status": "pending",
                    "mode": "voice_reset",
                    "message": prompt,
                    "aries_text": prompt,
                    "audio": encoded_audio,
                }

            VOICE_RESET_STATE.pop(resolved_user_id, None)
            fail_msg = str(reset_prepare.get("message") or "Voice reset failed")
            output_path = "temp_audio/outgoing/voice_reset_failed.mp3"
            encoded_audio = _synthesize_audio_base64(fail_msg, output_path)

            log_error(f"Voice reset delete phase failed for user_id={resolved_user_id}: {fail_msg}")
            log_step("RESPONSE_SENT")
            log_response(f"ARIS: {fail_msg}")
            return {
                "status": "error",
                "mode": "voice_reset",
                "reason": str(reset_prepare.get("reason") or "voice_delete_failed"),
                "message": fail_msg,
                "aries_text": fail_msg,
                "audio": encoded_audio,
            }

        VOICE_RESET_STATE.pop(resolved_user_id, None)
        cancel_msg = "Voice reset canceled."
        output_path = "temp_audio/outgoing/voice_reset_cancelled.mp3"
        encoded_audio = _synthesize_audio_base64(cancel_msg, output_path)

        log_step("VOICE_RESET_CANCELLED")
        log_step("RESPONSE_SENT")
        log_response(f"ARIS: {cancel_msg}")
        return {
            "status": "cancelled",
            "mode": "voice_reset",
            "message": cancel_msg,
            "aries_text": cancel_msg,
            "audio": encoded_audio,
        }

    if reset_state == "awaiting_new_voice":
        result = handle_voice_reset(resolved_user_id, input_path)
        if result.get("status") == "success":
            VOICE_RESET_STATE.pop(resolved_user_id, None)
            message = str(result.get("message") or "Your voice has been successfully reset and updated.")
            output_path = "temp_audio/outgoing/voice_reset_success.mp3"
            encoded_audio = _synthesize_audio_base64(message, output_path)

            log_step("RESPONSE_SENT")
            log_response("ARIS: Voice reset successful")
            return {
                "status": "success",
                "mode": "voice_reset",
                "message": message,
                "aries_text": message,
                "audio": encoded_audio,
            }

        reason = str(result.get("reason") or "voice_reset_failed")
        if reason in {"feature_extraction_failed", "voice_enroll_failed"}:
            retry_msg = "Microphone capture failed. Please say your sentence again to retry voice registration."
            output_path = "temp_audio/outgoing/voice_reset_retry.mp3"
            encoded_audio = _synthesize_audio_base64(retry_msg, output_path)

            log_error(f"Voice reset retry required for user_id={resolved_user_id}: {result.get('message')}")
            log_step("RESPONSE_SENT")
            log_response(f"ARIS: {retry_msg}")
            return {
                "status": "pending",
                "mode": "voice_reset",
                "reason": reason,
                "message": retry_msg,
                "aries_text": retry_msg,
                "audio": encoded_audio,
            }

        VOICE_RESET_STATE.pop(resolved_user_id, None)
        fail_msg = str(result.get("message") or "Voice reset failed")
        output_path = "temp_audio/outgoing/voice_reset_failed.mp3"
        encoded_audio = _synthesize_audio_base64(fail_msg, output_path)

        log_error(f"Voice reset failed for user_id={resolved_user_id}: {fail_msg}")
        log_step("RESPONSE_SENT")
        log_response(f"ARIS: {fail_msg}")
        return {
            "status": "error",
            "mode": "voice_reset",
            "reason": reason,
            "message": fail_msg,
            "aries_text": fail_msg,
            "audio": encoded_audio,
        }

    if is_voice_reset_command(user_query):
        VOICE_RESET_STATE[resolved_user_id] = "awaiting_confirmation"
        prompt = "Are you sure you want to reset your voice? Say yes to continue or no to cancel."
        output_path = "temp_audio/outgoing/voice_reset_confirm.mp3"
        encoded_audio = _synthesize_audio_base64(prompt, output_path)

        log_step("VOICE_RESET_REQUESTED")
        log_step("RESPONSE_SENT")
        log_response(f"ARIS: {prompt}")
        return {
            "status": "pending",
            "mode": "voice_reset",
            "message": prompt,
            "aries_text": prompt,
            "audio": encoded_audio,
        }

    if is_safe_folder_command(user_query):
        result = verify_safe_folder_access(resolved_user_id, input_path)
        log_step("AUTH_CHECK_COMPLETED")

        if result.get("status") == "enrolled":
            output_path = "temp_audio/outgoing/safe_folder_enrolled.mp3"
            text_to_speech("Voice registered successfully", output_path)

            with open(output_path, "rb") as audio_file:
                encoded_audio = base64.b64encode(audio_file.read()).decode("utf-8")

            log_step("RESPONSE_SENT")
            log_response("ARIS: Voice registered successfully")

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

            log_step("RESPONSE_SENT")
            log_response("ARIS: Access granted")

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

        log_step("RESPONSE_SENT")
        log_response(f"ARIS: {message}")

        return {
            "status": "error",
            "user_text": SAFE_FOLDER_KEYWORD,
            "access_granted": False,
            "reason": reason,
            "message": message,
            "audio": encoded_audio,
        }

    brain_start = time.perf_counter()
    aries_reply = await process_text(user_query, resolved_user_id)
    log_step("LLM_COMPLETED", int((time.perf_counter() - brain_start) * 1000))
    suggestion_texts: list[str] = []
    
    # Spawn background phi3 intent classification (non-blocking, for learning/logging)
    background_tasks.add_task(_classify_intent_background, user_query)

    output_path = "temp_audio/outgoing/response.mp3"
    encoded_audio = await _synthesize_audio_base64_async(aries_reply, output_path)

    log_step("RESPONSE_SENT")
    log_response(f"ARIS: {aries_reply}")

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
            message = "Voice registered successfully"
            audio = _synthesize_audio_base64(message, "temp_audio/outgoing/safe_folder_enrolled.mp3")
        elif result.get("access_granted"):
            status_code = 200
            message = "Access granted"
            audio = _synthesize_audio_base64(message, "temp_audio/outgoing/safe_folder_open.mp3")
        else:
            status_code = 401 if result.get("reason") in {"keyword_mismatch", "voice_mismatch", "stt_failed", "supabase_read_failed"} else 400
            message = str(result.get("message") or "Could not verify voice")
            audio = _synthesize_audio_base64(message, "temp_audio/outgoing/safe_folder_denied.mp3")

        return JSONResponse(
            status_code=status_code,
            content={
                "user_id": resolved_user_id,
                **result,
                "message": message,
                "audio": audio,
            },
        )
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)



# --- SAFE FOLDER FILE UPLOAD ---
@app.post("/safe-folder/files/upload")
async def safe_folder_upload_file(
    file: UploadFile = File(...),
    user_id: str | None = Form(default=None),
):
    resolved_user_id = _resolve_user_id(user_id)

    try:
        stored_file = await save_vault_file(file, resolved_user_id)
        stats = get_vault_storage_stats(resolved_user_id)
        return {
            "status": "success",
            "user_id": resolved_user_id,
            "file": stored_file,
            "stats": stats,
        }
    except Exception as exc:
        log_error(f"SAFE_FOLDER_UPLOAD_FAILED user_id={resolved_user_id}: {exc}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Unable to upload file right now."},
        )


# --- SAFE FOLDER FILE LIST + STORAGE STATS ---
@app.get("/safe-folder/files")
async def safe_folder_list_files(user_id: str | None = None):
    resolved_user_id = _resolve_user_id(user_id)
    files = list_vault_files(resolved_user_id)
    stats = get_vault_storage_stats(resolved_user_id)

    return {
        "status": "success",
        "user_id": resolved_user_id,
        "files": files,
        "stats": stats,
    }


# --- SAFE FOLDER FILE OPEN (inline) ---
@app.get("/safe-folder/files/{file_id}/open")
async def safe_folder_open_file(file_id: str, user_id: str | None = None):
    resolved_user_id = _resolve_user_id(user_id)
    file_path = get_vault_file_path(resolved_user_id, file_id)
    if file_path is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "File not found."})

    display_name = file_path.name.split("_", 2)[-1] if "_" in file_path.name else file_path.name
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{display_name}"'},
    )


# --- SAFE FOLDER FILE DOWNLOAD ---
@app.get("/safe-folder/files/{file_id}/download")
async def safe_folder_download_file(file_id: str, user_id: str | None = None):
    resolved_user_id = _resolve_user_id(user_id)
    file_path = get_vault_file_path(resolved_user_id, file_id)
    if file_path is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "File not found."})

    display_name = file_path.name.split("_", 2)[-1] if "_" in file_path.name else file_path.name
    return FileResponse(
        path=str(file_path),
        filename=display_name,
        media_type="application/octet-stream",
    )


# --- SAFE FOLDER FILE DELETE ---
@app.delete("/safe-folder/files/{file_id}")
async def safe_folder_delete_file(file_id: str, user_id: str | None = None):
    resolved_user_id = _resolve_user_id(user_id)
    removed = delete_vault_file(resolved_user_id, file_id)
    if not removed:
        return JSONResponse(status_code=404, content={"status": "error", "message": "File not found."})

    files = list_vault_files(resolved_user_id)
    stats = get_vault_storage_stats(resolved_user_id)
    return {
        "status": "success",
        "user_id": resolved_user_id,
        "files": files,
        "stats": stats,
    }

if __name__ == "__main__":
    watcher_thread = threading.Thread(target=monitor_schedule, daemon=True)
    watcher_thread.start()
    log_step("PROACTIVE_WATCHER_STARTED")
    uvicorn.run(app, host="0.0.0.0", port=8000)

