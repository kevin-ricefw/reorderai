"""Centralized logging for V2 modules."""

from __future__ import annotations

import logging
import sys

from config.settings import get_settings


def get_logger(name: str) -> logging.Logger:
    """Return a configured module logger."""
    settings = get_settings()
    level = getattr(logging, settings.app.log_level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger
