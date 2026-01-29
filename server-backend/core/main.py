from core.stt_engine import speech_to_text
from core.brain import process_text
from core.tts_engine import text_to_speech
from core.audiostoring import convert_to_mp4

INCOMING_WAV = "temp_audio/incoming/user.wav"
OUT_WAV = "temp_audio/outgoing/response.wav"
OUT_MP4 = "temp_audio/outgoing/response.m4a"

def run_assistant():
    # 1. Speech → Text
    text = speech_to_text(INCOMING_WAV)
    if not text:
        print("[MAIN] No speech detected")
        return

    # 2. Decide response
    response_text = process_text(text)

    # 3. Text → Speech
    ok = text_to_speech(response_text, OUT_WAV)
    if not ok:
        print("[MAIN] TTS failed")
        return

    # 4. WAV → MP4
    convert_to_mp4(OUT_WAV, OUT_MP4)

    print("[MAIN] Assistant response ready")