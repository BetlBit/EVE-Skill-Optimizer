from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from .logging_config import configure_logging
from .paths import ensure_runtime_dirs, logs_dir, user_data_dir
from .single_instance import SingleInstance
from .version import VERSION

HOST = "127.0.0.1"
PREFERRED_PORT = 8000


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ensure_runtime_dirs()
    _configure_launcher_logging()
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("EVE Skill Optimizer launcher %s starting", VERSION)

    port = select_port(PREFERRED_PORT)
    lock = SingleInstance(user_data_dir() / "optimizer.lock")
    existing = lock.acquire(port)
    if existing:
        # A second click can happen while the first frozen process is still
        # importing FastAPI and has not bound its HTTP port yet. Give it a
        # bounded startup window before deciding that the lock is stale.
        if wait_for_existing_instance(existing.port, timeout_seconds=12.0):
            url = f"http://{HOST}:{existing.port}/"
            logger.info("Existing EVE Skill Optimizer instance detected on port %s", existing.port)
            if not args.no_browser:
                webbrowser.open(url)
            return 0

        # A PID may have been reused after an unclean shutdown. Only break the
        # lock after proving that the recorded local HTTP endpoint is not ours.
        logger.warning(
            "Stale optimizer lock detected (pid=%s, port=%s); replacing it",
            existing.pid,
            existing.port,
        )
        lock.force_clear()
        existing = lock.acquire(port)
        if existing:
            logger.error("Could not acquire single-instance lock after clearing stale state")
            return 1

    server = _ServerThread(port)
    try:
        server.start()
        wait_until_responsive(port, timeout_seconds=30)
        logger.info("Server ready on http://%s:%s", HOST, port)
        if args.smoke_test:
            _smoke_check(port)
            logger.info("Frozen/source smoke test completed successfully")
            return 0
        if not args.no_browser:
            webbrowser.open(f"http://{HOST}:{port}/")
        _run_control_window(server, port)
        return 0
    except Exception:
        logger.exception("Launcher failed")
        if args.smoke_test:
            return 1
        raise
    finally:
        server.stop()
        lock.release()
        logger.info("EVE Skill Optimizer launcher stopped")


def select_port(preferred: int = PREFERRED_PORT) -> int:
    if _port_available(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def optimizer_responsive(port: int, *, timeout: float = 0.75) -> bool:
    """Return True only for a local EVE Skill Optimizer HTTP endpoint."""
    try:
        with urlopen(f"http://{HOST}:{int(port)}/api/status", timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return (
                bool(payload.get("ok"))
                and payload.get("app") == "EVE Skill Optimizer"
                and isinstance(payload.get("version"), str)
            )
    except (OSError, ValueError, json.JSONDecodeError, URLError):
        return False


def wait_for_existing_instance(port: int, *, timeout_seconds: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if optimizer_responsive(port, timeout=0.75):
            return True
        time.sleep(0.2)
    return False


def wait_until_responsive(port: int, *, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if optimizer_responsive(port, timeout=1.0):
            return
        time.sleep(0.2)
    raise TimeoutError(f"Server did not become responsive on {HOST}:{port}")


class _ServerThread:
    def __init__(self, port: int):
        self.port = port
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        from .main import app

        # Keep release logging under our control. Uvicorn's default dictConfig
        # would otherwise replace/redirect handlers in a windowed executable.
        config = uvicorn.Config(
            app,
            host=HOST,
            port=self.port,
            reload=False,
            log_level="info",
            log_config=None,
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="eve-skill-optimizer-server", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=8)


def _run_control_window(server: _ServerThread, port: int) -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"EVE Skill Optimizer {VERSION}")
    root.geometry("380x180")
    root.resizable(False, False)
    url = f"http://{HOST}:{port}/"

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="EVE Skill Optimizer is running").pack(anchor="w")
    ttk.Label(frame, text=url).pack(anchor="w", pady=(4, 12))
    ttk.Button(frame, text="Open Optimizer", command=lambda: webbrowser.open(url)).pack(fill="x", pady=2)
    ttk.Button(frame, text="Open Logs Folder", command=open_logs_folder).pack(fill="x", pady=2)
    ttk.Button(frame, text="Stop / Exit", command=root.destroy).pack(fill="x", pady=2)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    server.stop()


def open_logs_folder() -> None:
    path = logs_dir()
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)])
    else:
        webbrowser.open(path.as_uri())


def _smoke_check(port: int) -> None:
    import certifi

    ca_bundle = Path(certifi.where())
    if not ca_bundle.is_file():
        raise RuntimeError(f"Bundled CA certificate file is missing: {ca_bundle}")

    checks = ("/", "/api/status", "/static/app.js", "/static/styles.css")
    for path in checks:
        with urlopen(f"http://{HOST}:{port}{path}", timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"Smoke check failed for {path}: {response.status}")
            if path == "/api/status":
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("version") != VERSION:
                    raise RuntimeError(f"Smoke check version mismatch: {payload.get('version')!r}")


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((HOST, port)) != 0


def _configure_launcher_logging() -> None:
    ensure_runtime_dirs()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    path = logs_dir() / "launcher.log"
    target = str(path.resolve()).casefold()
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            filename = str(getattr(handler, "baseFilename", ""))
            try:
                filename = str(Path(filename).resolve())
            except OSError:
                pass
            if filename.casefold() == target:
                return
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"EVE Skill Optimizer {VERSION}")
    parser.add_argument("--smoke-test", action="store_true", help="Start, verify local HTTP endpoints, then exit.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the default browser.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
