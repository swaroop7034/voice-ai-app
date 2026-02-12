import pyttsx3
import os

def text_to_speech(text, output_path):
    """
    Converts text to speech LOCALLY using Windows SAPI5.
    No internet required.
    """
    try:
        print(f"[TTS] Synthesizing: {text[:30]}...")
        engine = pyttsx3.init()

        # --- JARVIS SETTINGS ---
        # 1. Speed: Default is 200. Increase to 220 for a snappier feel.
        engine.setProperty('rate', 210) 

        # 2. Voice: 0 is usually a male voice (David), 1 is female (Zira).
        voices = engine.getProperty('voices')
        engine.setProperty('voice', voices[0].id) 

        # Save to file for the phone to receive
        engine.save_to_file(text, output_path)
        engine.runAndWait()
        
        print(f"[TTS] Voice generated at {output_path}")
        return True
    except Exception as e:
        print(f"[TTS ERROR] {e}")
        return False