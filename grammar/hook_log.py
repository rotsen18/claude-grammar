import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DATA_DIR

LOG_FILE = DATA_DIR / "hook.log"
_MAX_BYTES = 512 * 1024
_BACKUP_COUNT = 3

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("grammar_hook")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(formatter)
        logger.addHandler(stderr_handler)

    _logger = logger
    return logger


def tail_log(lines: int = 200) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        with open(LOG_FILE, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                handle.seek(size)
                data = handle.read(step) + data
            text = data.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"<failed to read log: {exc}>"

    all_lines = text.splitlines()
    return "\n".join(all_lines[-lines:])
