"""One standing check at a time (no Qt): a named mutex the check really owns.

    RunnerLock(name)        ``acquire()`` -> True when this process now owns it (also when the last owner died
                            holding it: WAIT_ABANDONED), False when another check holds it; ``release()``; a context
                            manager. Unlike "does the name exist", ownership ends with the process, so a check that
                            crashed never keeps the next one out. Elsewhere than Windows a lock file (fcntl) stands in.
    lock_name(user)         "Local\\briefing-reader-standing-<user>"
"""

from __future__ import annotations

import ctypes
import getpass
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import APP_NAME

WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102


def lock_name(user: str | None = None) -> str:
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no user name available
            user = ""
    user = re.sub(r"[^A-Za-z0-9_-]", "", user or "") or "user"
    return f"Local\\{APP_NAME}-standing-{user}"


class RunnerLock:
    def __init__(self, name: str | None = None) -> None:
        self.name = name or lock_name()
        self._handle: Any = None
        self._file: Any = None
        self.abandoned = False

    def acquire(self) -> bool:
        if sys.platform == "win32":
            return self._acquire_windows()
        return self._acquire_file()

    def _acquire_windows(self) -> bool:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        state = kernel32.WaitForSingleObject(handle, 0)
        if state in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self._handle = handle
            self.abandoned = state == WAIT_ABANDONED
            return True
        kernel32.CloseHandle(handle)
        return False

    def _acquire_file(self) -> bool:
        import fcntl

        path = Path(tempfile.gettempdir()) / (self.name.replace("\\", "_") + ".lock")
        handle = open(path, "a+b")   # noqa: SIM115 - kept open while the lock is held
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def __enter__(self) -> RunnerLock:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def held_elsewhere(name: str | None = None) -> bool:
    """Is a check running now (another process owns the lock)? Tries to own it for a moment and lets go."""
    lock = RunnerLock(name)
    if lock.acquire():
        lock.release()
        return False
    return True


