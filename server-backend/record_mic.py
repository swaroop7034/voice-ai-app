import sounddevice as sd
from scipy.io.wavfile import write
import os

SAMPLE_RATE = 16000   # Whisper-friendly
DURATION = 10          # seconds
OUTPUT_PATH = "temp_audio/incoming/user.wav"

def record_audio():
    os.makedirs("temp_audio/incoming", exist_ok=True)

    print("🎙️ Recording... Speak now")
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )
    sd.wait()

    write(OUTPUT_PATH, SAMPLE_RATE, audio)
    print(f"✅ Saved recording to {OUTPUT_PATH}")

if __name__ == "__main__":
    record_audio()
