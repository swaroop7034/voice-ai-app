import whisper
import os

# Load model once on startup to save time
model = whisper.load_model("base") 

def speech_to_text(audio_path: str) -> str | None:
    if not os.path.exists(audio_path):
        return None
    try:
        # Processes the file directly
        result = model.transcribe(audio_path, fp16=False)
        return result["text"].strip()
    except Exception as e:
        print(f"STT Error: {e}")
        return None