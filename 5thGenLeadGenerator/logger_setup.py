"""
logger_setup.py
================
Central logging configuration. Log file path is fully configurable via
config.py / .env (LOG_DIR, LOG_FILE) so Colab runs can point it at Drive
just like the database.
"""

import logging
from pathlib import Path

import config


def get_logger(name: str = "5thGenLeadGenerator") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured (Streamlit re-imports modules on rerun).
        return logger

    logger.setLevel(logging.INFO)

    Path(config.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
