from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstanceInfo:
    pid: int
    port: int


class SingleInstance:
    """Small cross-platform lock-file helper used by the Windows launcher.

    The final lock file is created with O_EXCL. A short retry handles the tiny
    window where another process has created the file but has not finished
    writing its JSON payload yet.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self, port: int) -> InstanceInfo | None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        for _ in range(12):
            existing = self.read()
            if existing is not None:
                if process_running(existing.pid):
                    return existing
                self._remove_lock()

            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # Another launcher may be between O_EXCL and writing JSON.
                time.sleep(0.05)
                continue

            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "port": int(port)}, handle)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            self.acquired = True
            return None

        # If an unreadable/corrupt lock survived the retry window, treat it as
        # stale. This file contains only PID/port and no user secrets.
        self._remove_lock()
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = self.read()
            return existing if existing is not None else InstanceInfo(pid=-1, port=int(port))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "port": int(port)}, handle)
        self.acquired = True
        return None

    def read(self) -> InstanceInfo | None:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = int(data["pid"])
            port = int(data["port"])
            if pid <= 0 or not (1 <= port <= 65535):
                return None
            return InstanceInfo(pid=pid, port=port)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def force_clear(self) -> None:
        """Remove a lock that the launcher has independently proven stale."""
        self._remove_lock()

    def release(self) -> None:
        if not self.acquired:
            return
        self._remove_lock()
        self.acquired = False

    def _remove_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except OSError:
            pass


def process_running(pid: int) -> bool:
    """Return whether *pid* is still alive without signalling/killing it.

    On POSIX, ``os.kill(pid, 0)`` is the conventional existence check.
    On Windows it is unsafe for this purpose: Python implements non-console
    signals via ``TerminateProcess``, so ``os.kill(pid, 0)`` can terminate the
    target process. Use the Win32 process-query APIs instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_running_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists, but this user cannot signal it.
        return True
    except OSError:
        return False
    return True


def _process_running_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE

    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code.restype = wintypes.BOOL

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        # Access denied still means there is a process at that PID. Treat it
        # as alive rather than risking replacement of a live instance lock.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED

    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            # We successfully opened the process but could not query its exit
            # code. The conservative single-instance choice is "alive".
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        close_handle(handle)
