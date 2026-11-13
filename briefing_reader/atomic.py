"""Replacing a file that another Jarvis process may be reading at that moment (no Qt).

    replace(part, path, attempts=REPLACE_ATTEMPTS, delay_s=REPLACE_DELAY_S, sleep=time.sleep)
        os.replace(part, path), tried again for a moment while Windows refuses it because another
        process has ``path`` open
    busy(exc)   True for the errors such a reader causes

On Windows os.replace fails with PermissionError (WinError 5, access denied) or a sharing violation
(WinError 32 / 33) while another process holds ``path`` open without FILE_SHARE_DELETE, which is how
Python opens every file. Since standing agents and rules run in a windowless process of their own
(``--standing-run``), a read of a file there for a few milliseconds would otherwise make the app's
save fail, and a decision would be kept for that run only. A short retry (under half a second in
all) rides over such a read. Any other error, or the same one after the last try, is raised as
before, so every caller keeps its own handling of a failed write.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

REPLACE_ATTEMPTS = 8
REPLACE_DELAY_S = 0.06
# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_BUSY_WINERRORS = frozenset({5, 32, 33})


def busy(exc: BaseException) -> bool:
    """True for the errors a reader holding the file open causes (worth a short retry)."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in _BUSY_WINERRORS


def replace(part: str | os.PathLike[str], path: str | os.PathLike[str], *, attempts: int = REPLACE_ATTEMPTS,
            delay_s: float = REPLACE_DELAY_S, sleep: Callable[[float], None] = time.sleep) -> None:
    """``os.replace(part, path)``; while it fails because ``path`` is held open elsewhere, wait ``delay_s``
    and try again, at most ``attempts`` times in all. Raises the last error when every try failed."""
    tries = max(1, int(attempts))
    for attempt in range(tries):
        try:
            os.replace(part, path)
            return
        except OSError as exc:
            if attempt + 1 >= tries or not busy(exc):
                raise
        sleep(max(0.0, float(delay_s)))
