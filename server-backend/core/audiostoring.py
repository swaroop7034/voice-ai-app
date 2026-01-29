import subprocess
import os

def convert_to_mp4(input_wav: str, output_m4a: str):
    """
    Converts WAV to MP4 (AAC) using FFmpeg.
    """

    os.makedirs(os.path.dirname(output_m4a), exist_ok=True)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_wav,
            "-c:a", "aac",
            output_m4a
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )