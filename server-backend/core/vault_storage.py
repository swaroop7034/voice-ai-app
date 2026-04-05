from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

VAULT_ROOT = Path("vault_storage")
DEFAULT_TOTAL_BYTES = int(os.getenv("SAFE_VAULT_TOTAL_BYTES", str(5 * 1024 * 1024 * 1024)))

_CATEGORY_BY_EXTENSION = {
    "DOCS": {"doc", "docx", "pdf", "txt", "rtf", "xls", "xlsx", "ppt", "pptx", "csv", "json"},
    "AUDIO": {"mp3", "wav", "m4a", "aac", "ogg", "flac"},
    "VIDEO": {"mp4", "mov", "mkv", "avi", "webm"},
    "IMAGE": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic"},
}


def _safe_name(filename: str) -> str:
    base = Path(filename or "upload.bin").name
    sanitized = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return sanitized or "upload.bin"


def _ext(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 else ""


def _category(filename: str) -> str:
    extension = _ext(filename)
    for category, extensions in _CATEGORY_BY_EXTENSION.items():
        if extension in extensions:
            return category
    return "DOCS"


def _format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _user_dir(user_id: str) -> Path:
    safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", user_id or "anonymous")
    directory = VAULT_ROOT / safe_user
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_vault_file_path(user_id: str, file_id: str) -> Path | None:
    user_directory = _user_dir(user_id)
    requested_name = Path(file_id or "").name
    if not requested_name:
        return None

    candidate = user_directory / requested_name
    try:
        candidate.resolve().relative_to(user_directory.resolve())
    except Exception:
        return None

    return candidate if candidate.exists() and candidate.is_file() else None


def delete_vault_file(user_id: str, file_id: str) -> bool:
    target = get_vault_file_path(user_id, file_id)
    if target is None:
        return False

    target.unlink(missing_ok=True)
    return True


async def save_vault_file(file: UploadFile, user_id: str) -> dict:
    user_directory = _user_dir(user_id)
    original_name = _safe_name(file.filename or "upload.bin")
    now = datetime.now()
    stamped_name = f"{now.strftime('%Y%m%d_%H%M%S')}_{original_name}"

    destination = user_directory / stamped_name
    content = await file.read()
    with open(destination, "wb") as handle:
        handle.write(content)

    size_bytes = destination.stat().st_size
    category = _category(original_name)

    return {
        "id": stamped_name,
        "name": original_name,
        "stored_name": stamped_name,
        "category": category,
        "size_bytes": size_bytes,
        "size": _format_bytes(size_bytes),
        "date": now.strftime("%d %b %Y").upper(),
        "created_at": now.isoformat(),
        "path": str(destination),
    }


def list_vault_files(user_id: str) -> list[dict]:
    user_directory = _user_dir(user_id)
    files: list[dict] = []

    for path in sorted(user_directory.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue

        stored_name = path.name
        original_name = stored_name.split("_", 2)[-1] if "_" in stored_name else stored_name
        created = datetime.fromtimestamp(path.stat().st_mtime)
        size_bytes = path.stat().st_size

        files.append(
            {
                "id": stored_name,
                "name": original_name,
                "stored_name": stored_name,
                "category": _category(original_name),
                "size_bytes": size_bytes,
                "size": _format_bytes(size_bytes),
                "date": created.strftime("%d %b %Y").upper(),
                "created_at": created.isoformat(),
                "path": str(path),
            }
        )

    return files


def get_vault_storage_stats(user_id: str) -> dict:
    files = list_vault_files(user_id)
    used_bytes = sum(int(file.get("size_bytes") or 0) for file in files)
    total_bytes = DEFAULT_TOTAL_BYTES

    return {
        "used_bytes": used_bytes,
        "total_bytes": total_bytes,
        "used_gb": round(used_bytes / (1024 * 1024 * 1024), 3),
        "total_gb": round(total_bytes / (1024 * 1024 * 1024), 3),
        "used_human": _format_bytes(used_bytes),
        "total_human": _format_bytes(total_bytes),
        "file_count": len(files),
    }
