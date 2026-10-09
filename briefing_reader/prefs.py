"""Jarvis's own small preferences: ``%LOCALAPPDATA%\\briefing-reader\\assistant.json``.

``{"muted": false, "recent_greetings": ["m3", "n1"]}``: whether the owner muted
Jarvis's voice (the header's speaker button) and the ids of the last few
greetings, so the next one differs; ``"answers_adopted": true`` once the
answers of an older version in runstate.json were taken as viewed (once, at
the first start after the update). Ids and booleans only: never any text.

The file is written atomically (temporary file + ``os.replace``); a missing,
corrupt or unwritable file counts as the defaults and never stops the app
(a failed write is logged and kept in memory for this run).

Qt-free; standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

PREFS_FILE = "assistant.json"
RECENT_GREETINGS_MAX = 5
_GREETING_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,15}")


class AssistantPrefs:
    """``muted`` and ``recent_greetings`` (oldest first, at most 5 ids), kept in ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._muted, self._recent, self._adopted = _load(self.path)

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    @property
    def recent_greetings(self) -> list[str]:
        with self._lock:
            return list(self._recent)

    def set_muted(self, muted: bool) -> bool:
        """Remember the mute switch; False when the file could not be written (kept in memory)."""
        with self._lock:
            self._muted = bool(muted)
            return self._save_locked()

    @property
    def answers_adopted(self) -> bool:
        """The older version's answers in runstate.json were taken as viewed already."""
        with self._lock:
            return self._adopted

    def set_answers_adopted(self) -> bool:
        """Remember that the older answers were taken as viewed (it is done once)."""
        with self._lock:
            self._adopted = True
            return self._save_locked()

    def remember_greeting(self, greeting_id: str) -> bool:
        """Add ``greeting_id`` as the most recent greeting (the oldest beyond 5 is forgotten)."""
        if not isinstance(greeting_id, str) or not _GREETING_ID_RE.fullmatch(greeting_id):
            logger.debug("Ignoring a malformed greeting id")
            return False
        with self._lock:
            recent = [item for item in self._recent if item != greeting_id] + [greeting_id]
            self._recent = recent[-RECENT_GREETINGS_MAX:]
            return self._save_locked()

    def _save_locked(self) -> bool:
        values: dict[str, object] = {"muted": self._muted, "recent_greetings": self._recent}
        if self._adopted:
            values["answers_adopted"] = True
        return _write_atomic(self.path, json.dumps(values, indent=2) + "\n")


def _load(path: Path) -> tuple[bool, list[str], bool]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return False, [], False
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read %s (%s); using the defaults", path, type(exc).__name__)
        return False, [], False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; using the defaults", path)
        return False, [], False
    if not isinstance(data, dict):
        logger.warning("%s is not a table of settings; using the defaults", path)
        return False, [], False
    muted = data.get("muted") is True
    raw = data.get("recent_greetings")
    recent = [item for item in raw if isinstance(item, str) and _GREETING_ID_RE.fullmatch(item)] \
        if isinstance(raw, list) else []
    return muted, recent[-RECENT_GREETINGS_MAX:], data.get("answers_adopted") is True


def _write_atomic(path: Path, data: str) -> bool:
    part: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, part = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, path)
        part = None
        return True
    except OSError as exc:
        logger.warning("Could not save %s (%s); kept for this run only", path,
                       exc.strerror or type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass
