import os
import torch

# ─── VRAM-AWARE MODEL SELECTION ────────────────────────────────
# Using faster-whisper with int8 quantization:
#   medium  int8 → ~1.2GB VRAM  ← your sweet spot (4GB VRAM, Llama sharing)
#   large-v3 int8 → ~2.5GB VRAM ← too tight with Llama running
#
# faster-whisper is 4x faster than openai-whisper at same accuracy.
# Install: pip install faster-whisper

DEVICE       = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = "int8_float16" if DEVICE == "cuda" else "int8"

print(f"[STT] Loading faster-whisper medium ({COMPUTE_TYPE}) on {DEVICE}...")

try:
    from faster_whisper import WhisperModel
    model = WhisperModel("medium", device=DEVICE, compute_type=COMPUTE_TYPE)
    USE_FASTER_WHISPER = True
    print("[STT] faster-whisper ready.")
except ImportError:
    print("[STT] faster-whisper not found — falling back to openai-whisper small.")
    import whisper
    model = whisper.load_model("small", device=DEVICE)
    USE_FASTER_WHISPER = False

# ─── INITIAL PROMPT ────────────────────────────────────────────
INITIAL_PROMPT = (
    "This is a voice assistant for scheduling and calendar management. "
    "The speaker has an Indian English accent from Kerala. "
    "Common words: schedule, reschedule, meeting, calendar, tomorrow, "
    "PM, AM, option, confirm, cancel, yes, no."
)

HALLUCINATION_PHRASES = {
    "thank you", "thanks for watching", "bye", "subscribe",
    "you", ".", "", "thanks", "thank you for watching",
    "hello", "goodbye", "see you", "see you next time",
}

def speech_to_text(audio_path: str) -> str | None:
    if not os.path.exists(audio_path):
        return None
    try:
        if USE_FASTER_WHISPER:
            return _transcribe_faster(audio_path)
        else:
            return _transcribe_openai(audio_path)
    except Exception as e:
        print(f"[STT Error] {e}")
        return None


def _transcribe_faster(audio_path: str) -> str | None:
    segments, info = model.transcribe(
        audio_path,
        language="en",
        initial_prompt=INITIAL_PROMPT,
        beam_size=5,
        best_of=5,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        word_timestamps=False,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    if not text or text.lower() in HALLUCINATION_PHRASES or len(text) < 2:
        print(f"[STT] Filtered: '{text}'")
        return None
    print(f"[STT] Transcribed: {text}")
    return text


def _transcribe_openai(audio_path: str) -> str | None:
    result = model.transcribe(
        audio_path,
        language="en",
        fp16=(DEVICE == "cuda"),
        initial_prompt=INITIAL_PROMPT,
        condition_on_previous_text=False,
        temperature=0.0,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
    )
    text = result["text"].strip()
    if not text or text.lower() in HALLUCINATION_PHRASES or len(text) < 2:
        print(f"[STT] Filtered: '{text}'")
        return None
    print(f"[STT] Transcribed: {text}")
    return text