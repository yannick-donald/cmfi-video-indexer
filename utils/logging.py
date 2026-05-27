from __future__ import annotations

import logging
import sys
from pathlib import Path

from pythonjsonlogger.jsonlogger import JsonFormatter


def configure_logging(log_level: str, log_file: Path) -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(log_level.upper())

    for h in list(logger.handlers):
        logger.removeHandler(h)

    log_file.parent.mkdir(parents=True, exist_ok=True)

    json_formatter = JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(filename)s %(lineno)d",
        rename_fields={"levelname": "level", "name": "logger"},
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(log_level.upper())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level.upper())
    console_handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

