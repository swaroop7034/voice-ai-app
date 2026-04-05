from __future__ import annotations

import os
from typing import Any

import librosa
import numpy as np
from dotenv import load_dotenv

from core.stt_engine import speech_to_text
from integrations.voice_auth_store import get_embedding_from_supabase, save_embedding_to_supabase

load_dotenv()

KEYWORD = "safe folder"
VOICE_AUTH_THRESHOLD = float(os.getenv("VOICE_AUTH_THRESHOLD", "0.96"))
VOICE_AUTH_TABLE = os.getenv("SUPABASE_VOICE_AUTH_TABLE", "voice_auth")


def _extract_features(file_path: str) -> np.ndarray:
    signal, sr = librosa.load(file_path, sr=16000)
    if signal.size == 0:
        raise ValueError("Audio file contains no samples")

    pre_emphasis = 0.97
    if signal.size > 1:
        signal = np.append(signal[0], signal[1:] - pre_emphasis * signal[:-1])

    mfcc = librosa.feature.mfcc(y=signal, sr=sr, n_mfcc=20)
    return mfcc.T


def _create_embedding(mfcc: np.ndarray) -> np.ndarray:
    embedding = mfcc.flatten().astype(np.float32, copy=False)
    norm = float(np.linalg.norm(embedding))
    if norm == 0.0:
        raise ValueError("Unable to create voice embedding from empty audio features")
    return embedding / norm


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    min_len = min(len(a), len(b))
    if min_len == 0:
        return 0.0

    a = a[:min_len]
    b = b[:min_len]
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


def enroll_voice_for_user(user_id: str, audio_path: str) -> dict[str, object]:
    try:
        mfcc = _extract_features(audio_path)
        embedding = _create_embedding(mfcc)
        save_result = save_embedding_to_supabase(user_id, embedding)

        if not save_result.get("success"):
            return {
                "status": "error",
                "message": save_result.get("message", "Failed to save voice embedding"),
                "reason": save_result.get("reason", "supabase_error"),
            }

        return {
            "status": "enrolled",
            "message": "Voice enrolled successfully",
        }
    except Exception as exc:
        print(f"[VOICE_AUTH] Enrollment failed for user_id={user_id}: {exc}")
        return {
            "status": "error",
            "message": "Failed to enroll voice",
            "reason": "feature_extraction_failed",
        }


def verify_safe_folder_access(user_id: str, audio_path: str) -> dict[str, object]:
    try:
        transcript = speech_to_text(audio_path)
    except Exception as exc:
        print(f"[VOICE_AUTH] STT failed for user_id={user_id}: {exc}")
        transcript = None

    if not transcript:
        return {
            "access_granted": False,
            "reason": "stt_failed",
            "message": "Could not detect speech",
        }

    if transcript.strip().lower() != KEYWORD:
        return {
            "access_granted": False,
            "reason": "keyword_mismatch",
            "message": "Wrong keyword",
        }

    try:
        stored_embedding = get_embedding_from_supabase(user_id)
    except Exception as exc:
        print(f"[VOICE_AUTH] Supabase lookup failed for user_id={user_id}: {exc}")
        return {
            "access_granted": False,
            "reason": "supabase_read_failed",
            "message": "Voice database unavailable",
        }

    if stored_embedding is None:
        try:
            mfcc = _extract_features(audio_path)
            embedding = _create_embedding(mfcc)
            save_result = save_embedding_to_supabase(user_id, embedding)
            if not save_result.get("success"):
                return {
                    "access_granted": False,
                    "reason": save_result.get("reason", "supabase_error"),
                    "message": save_result.get("message", "Failed to enroll voice"),
                }

            return {
                "status": "enrolled",
                "message": "Voice enrolled successfully",
            }
        except Exception as exc:
            print(f"[VOICE_AUTH] First-time enrollment failed for user_id={user_id}: {exc}")
            return {
                "access_granted": False,
                "reason": "feature_extraction_failed",
                "message": "Failed to enroll voice",
            }

    try:
        test_mfcc = _extract_features(audio_path)
        test_embedding = _create_embedding(test_mfcc)
        score = _cosine_similarity(stored_embedding, test_embedding)
    except Exception as exc:
        print(f"[VOICE_AUTH] Verification failed for user_id={user_id}: {exc}")
        return {
            "access_granted": False,
            "reason": "feature_extraction_failed",
            "message": "Could not process voice sample",
        }

    if score > VOICE_AUTH_THRESHOLD:
        return {
            "access_granted": True,
            "score": score,
            "message": "Access granted",
        }

    return {
        "access_granted": False,
        "reason": "voice_mismatch",
        "score": score,
        "message": "Voice not recognized",
    }
