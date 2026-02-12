from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
import uvicorn
import os
import base64

# Importing your existing core modules
from core.stt_engine import speech_to_text
from core.brain import process_text
from core.tts_engine import text_to_speech

app = FastAPI()

# Ensure directories exist
os.makedirs("temp_audio/incoming", exist_ok=True)
os.makedirs("temp_audio/outgoing", exist_ok=True)

@app.post("/chat")
async def handle_voice_chat(file: UploadFile = File(...)):
    # 1. Save incoming audio from Phone
    input_path = f"temp_audio/incoming/{file.filename}"
    with open(input_path, "wb") as f:
        f.write(await file.read())

    # 2. STT (Speech to Text)
    user_query = speech_to_text(input_path)
    if not user_query:
        return JSONResponse({"status": "error", "message": "No speech detected"})

    # 3. Brain (LLM Logic - Llama 3.2)
    aries_reply = process_text(user_query)

    # 4. TTS (Text to Speech)
    output_path = "temp_audio/outgoing/response.mp3"
    text_to_speech(aries_reply, output_path)

    # 5. Convert Audio to Base64 String for the Phone
    with open(output_path, "rb") as audio_file:
        encoded_audio = base64.b64encode(audio_file.read()).decode('utf-8')

    # Log to PC Terminal so you can see it working
    print(f"\n[USER]: {user_query}")
    print(f"[ARIES]: {aries_reply}\n")

    # 6. RETURN JSON: These keys MUST match index.tsx
    return {
        "status": "success",
        "user_text": user_query,    # For History Modal
        "aries_text": aries_reply,  # For History Modal
        "audio": encoded_audio      # For Voice Playback
    }

if __name__ == "__main__":
    # Replace host with "0.0.0.0" to allow phone connection
    uvicorn.run(app, host="0.0.0.0", port=8000)