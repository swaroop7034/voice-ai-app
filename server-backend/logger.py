from __future__ import annotations

import logging
import os


_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "ERROR": logging.ERROR,
}


def _resolve_level() -> int:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return _LEVELS.get(level_name, logging.INFO)


logger = logging.getLogger("voice-ai-app")


def setup_logging() -> None:
    if getattr(setup_logging, "_configured", False):
        return

    logging.basicConfig(level=_resolve_level(), format="%(message)s")
    logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
    logging.getLogger("fastapi").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logger.setLevel(_resolve_level())
    setup_logging._configured = True


setup_logging()


def log_request(message: str) -> None:
    logger.info("[REQUEST] %s", message)


def log_response(message: str) -> None:
    logger.info("[RESPONSE] %s", message)


def log_step(step_name: str, elapsed_ms: int | None = None) -> None:
    if elapsed_ms is None:
        logger.info("[STEP] %s", step_name)
    else:
        logger.info("[STEP] %s (%dms)", step_name, elapsed_ms)


def log_error(message: str) -> None:
    logger.error("[ERROR] %s", message)


def log_debug(message: str) -> None:
    logger.debug(message)
