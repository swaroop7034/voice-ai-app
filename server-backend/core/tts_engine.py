import subprocess
import os

PIPER_EXE = r"C:\piper\piper.exe"
MODEL = r"C:\piper\models\en_US-lessac-low.onnx"
CONFIG = r"C:\piper\models\en_US-lessac-low.onnx.json"

def text_to_speech(text, output_wav):
    try:
        os.makedirs(os.path.dirname(output_wav), exist_ok=True)

        cmd = [
            PIPER_EXE,
            "--model", MODEL,
            "--config", CONFIG,
            "--output_file", output_wav
        ]

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(text + "\n")  

        if stderr:
            print("[TTS STDERR]", stderr)

        return os.path.exists(output_wav)

    except Exception as e:
        print("[TTS ERROR]", e)
        return False  
    
