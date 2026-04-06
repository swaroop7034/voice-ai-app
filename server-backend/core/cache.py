from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
DEFAULT_TTL_SECONDS = 30
MAX_CACHE_SIZE = 128


def _normalize_key(text: str) -> str:
    return " ".join((text or "").lower().split())


def get_cached_response(text: str) -> Any | None:
    key = _normalize_key(text)
    item = _CACHE.get(key)
    if item is None:
        return None

    expires_at, value = item
    if expires_at < time.time():
        _CACHE.pop(key, None)
        return None

    _CACHE.move_to_end(key)
    return value


def set_cached_response(text: str, response: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    key = _normalize_key(text)
    if len(_CACHE) >= MAX_CACHE_SIZE:
        _CACHE.popitem(last=False)
    _CACHE[key] = (time.time() + max(1, ttl_seconds), response)
