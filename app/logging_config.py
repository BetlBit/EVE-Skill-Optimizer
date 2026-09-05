from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import ensure_runtime_dirs, logs_dir


def _has_file_handler(path: Path) -> bool:
    target = str(path.resolve()).casefold()
    for handler in logging.getLogger().handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        filename = str(getattr(handler, "baseFilename", ""))
        try:
            filename = str(Path(filename).resolve())
        except OSError:
            pass
        if filename.casefold() == target:
            return True
    return False


def configure_logging() -> None:
    """Attach the application rotating log without disturbing launcher logging."""
    ensure_runtime_dirs()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    path = logs_dir() / "app.log"
    if _has_file_handler(path):
        return
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
