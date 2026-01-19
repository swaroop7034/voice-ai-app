from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
import os

# Importing functions your teammates will write
from core.stt_engine import transcribe_audio
from core.brain import get_ai_response
from core.tts_engine import text_to_speech

app = FastAPI()

@app.post("/chat")
async def handle_voice_chat(file: UploadFile = File(...)):
    # 1. Save incoming audio from Member 1 (Mobile)
    temp_input = f"temp_audio/{file.filename}"
    with open(temp_input, "wb") as f:
        f.write(await file.read())

    # 2. Convert Voice to Text (Member 2's task)
    user_text = transcribe_audio(temp_input)

    # 3. Get AI Brain Response (Member 3's task)
    ai_response_text = get_ai_response(user_text)

    # 4. Convert AI Text to Voice (Member 2's task)
    temp_output = "temp_audio/ai_response.mp3"
    text_to_speech(ai_response_text, temp_output)

    # 5. Return the audio file back to Member 1
    return FileResponse(temp_output)