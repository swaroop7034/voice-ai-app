import threading

import pyttsx3

from logger import logger, log_error

_ENGINE_LOCK = threading.Lock()
_ENGINE = None


def _get_engine():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = pyttsx3.init()
            _ENGINE.setProperty('rate', 210)
            voices = _ENGINE.getProperty('voices')
            if voices:
                _ENGINE.setProperty('voice', voices[0].id)
        return _ENGINE


def text_to_speech(text, output_path):
    """
    Converts text to speech LOCALLY using Windows SAPI5.
    No internet required.
    """
    try:
        logger.debug(f"[TTS] Synthesizing: {text[:30]}...")
        engine = _get_engine()

        # Save to file for the phone to receive
        engine.save_to_file(text, output_path)
        engine.runAndWait()

        logger.debug(f"[TTS] Voice generated at {output_path}")
        return True
    except Exception as e:
        log_error(f"TTS error: {e}")
        return False