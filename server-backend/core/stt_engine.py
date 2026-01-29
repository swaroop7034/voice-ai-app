import subprocess
import os

WHISPER_EXE = r"C:\whisper\whisper-cli.exe"
MODEL_PATH = r"C:\whisper\models\ggml-base.en.bin"

def speech_to_text(audio_path: str) -> str | None:
    """
    Converts speech audio to text using Whisper.cpp (offline).
    """

    if not os.path.exists(audio_path):
        print("[STT] Audio file not found:", audio_path)
        return None

    try:
        result = subprocess.run(
            [
                WHISPER_EXE,
                "-m", MODEL_PATH,
                "-f", audio_path,
                "-nt",
                "-l", "en"
            ],
            capture_output=True,
            text=True
        )

        text = result.stdout.strip()
        print("[STT] Recognized:", text)
        return text if text else None

    except Exception as e:
        print("[STT] Error:", e)
        return None
