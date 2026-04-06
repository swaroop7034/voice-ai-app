from __future__ import annotations

import os
import re
from typing import Any

import librosa
import numpy as np
from dotenv import load_dotenv

from core.stt_engine import speech_to_text
from integrations.voice_auth_store import (
    delete_embedding_from_supabase,
    get_embedding_from_supabase,
    save_embedding_to_supabase,
)
from logger import log_error, log_step

load_dotenv()

KEYWORD = "private files"
VOICE_RESET_PHRASES = {
    "reset voice",
    "reset my voice",
    "delete my voice",
    "delete voice",
}
VOICE_RESET_CONFIRM_WORDS = {
    "yes",
    "confirm",
    "proceed",
    "go ahead",
    "do it",
    "reset it",
    "sure",
    "okay",
    "ok",
}
VOICE_AUTH_THRESHOLD = float(os.getenv("VOICE_AUTH_THRESHOLD", "0.96"))
VOICE_AUTH_TABLE = os.getenv("SUPABASE_VOICE_AUTH_TABLE", "voice_auth")


def _normalize_phrase(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(cleaned.split())


def is_safe_folder_command(text: str | None) -> bool:
    if not text:
        return False

    normalized = _normalize_phrase(text)
    if normalized in {
        "private files",
        "private file",
        "privatefiles",
        "privatefile",
    }:
        return True

    tokens = set(normalized.split())
    has_private_phrase = "private" in tokens and ("files" in tokens or "file" in tokens)
    return has_private_phrase


def is_voice_reset_command(text: str | None) -> bool:
    if not text:
        return False

    normalized = _normalize_phrase(text)
    if normalized in VOICE_RESET_PHRASES:
        return True

    return ("reset" in normalized and "voice" in normalized) or (
        "delete" in normalized and "voice" in normalized
    )


def is_voice_reset_confirmation(text: str | None) -> bool:
    if not text:
        return False

    normalized = _normalize_phrase(text)
    if normalized in {"cancel", "no", "nope", "never mind", "nevermind"}:
        return False

    return any(word in normalized for word in VOICE_RESET_CONFIRM_WORDS)


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
        log_error(f"VOICE_AUTH enrollment failed for user_id={user_id}: {exc}")
        return {
            "status": "error",
            "message": "Failed to enroll voice",
            "reason": "feature_extraction_failed",
        }


def verify_safe_folder_access(user_id: str, audio_path: str) -> dict[str, object]:
    try:
        transcript = speech_to_text(audio_path)
    except Exception as exc:
        log_error(f"VOICE_AUTH STT failed for user_id={user_id}: {exc}")
        transcript = None

    if not transcript:
        return {
            "access_granted": False,
            "reason": "stt_failed",
            "message": "Could not detect speech",
        }

    if not is_safe_folder_command(transcript):
        return {
            "access_granted": False,
            "reason": "keyword_mismatch",
            "message": "Wrong keyword",
        }

    try:
        stored_embedding = get_embedding_from_supabase(user_id)
    except Exception as exc:
        log_error(f"VOICE_AUTH Supabase lookup failed for user_id={user_id}: {exc}")
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
            log_error(f"VOICE_AUTH first-time enrollment failed for user_id={user_id}: {exc}")
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
        log_error(f"VOICE_AUTH verification failed for user_id={user_id}: {exc}")
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


def delete_voice_data(user_id: str) -> dict[str, object]:
    try:
        delete_result = delete_embedding_from_supabase(user_id)
        if not delete_result.get("success"):
            return {
                "success": False,
                "reason": delete_result.get("reason", "supabase_delete_failed"),
                "message": delete_result.get("message", "Failed to delete voice data"),
            }

        # Optional local cleanup if a dedicated per-user voice sample exists.
        local_voice_path = os.path.join("temp_audio", "voice_auth", f"{user_id}.wav")
        if os.path.exists(local_voice_path):
            os.remove(local_voice_path)

        return {
            "success": True,
            "reason": "ok",
            "message": "Voice data deleted",
        }
    except Exception as exc:
        log_error(f"VOICE_AUTH delete failed for user_id={user_id}: {exc}")
        return {
            "success": False,
            "reason": "voice_delete_failed",
            "message": "Failed to delete voice data",
        }


def handle_voice_reset(user_id: str, audio_path: str | None = None) -> dict[str, object]:
    if audio_path is None:
        delete_result = delete_voice_data(user_id)
        if not delete_result.get("success"):
            log_error(f"VOICE_AUTH reset delete failed for user_id={user_id}: {delete_result.get('message')}")
            return {
                "status": "error",
                "message": str(delete_result.get("message") or "Failed to reset voice"),
                "reason": str(delete_result.get("reason") or "voice_delete_failed"),
            }

        log_step("VOICE_DATA_DELETED")
        return {
            "status": "pending",
            "message": "Please say a sentence to register your new voice.",
            "reason": "awaiting_new_voice",
        }

    enroll_result = enroll_voice_for_user(user_id, audio_path)
    if enroll_result.get("status") != "enrolled":
        log_error(f"VOICE_AUTH reset enrollment failed for user_id={user_id}: {enroll_result.get('message')}")
        return {
            "status": "error",
            "message": str(enroll_result.get("message") or "Failed to register new voice"),
            "reason": str(enroll_result.get("reason") or "voice_enroll_failed"),
        }

    log_step("NEW_VOICE_REGISTERED")
    return {
        "status": "success",
        "message": "Your voice has been successfully reset and updated.",
        "reason": "ok",
    }
