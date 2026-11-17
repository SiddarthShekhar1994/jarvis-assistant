"""Jarvis's own small preferences: ``%LOCALAPPDATA%\\briefing-reader\\assistant.json``.

``{"muted": false, "recent_greetings": ["m3", "n1"]}``: whether the owner muted
Jarvis's voice (the header's speaker button) and the ids of the last few
greetings, so the next one differs; ``"answers_adopted": true`` once the
answers of an older version in runstate.json were taken as viewed (once, at
the first start after the update); ``"live_window": {"x", "y", "w", "h"}`` and
``"live_popped_out"``: where the LIVE pop-out window was and whether it was open
when Jarvis quit (written once the pop-out has been used); ``"prod_hint_shown":
true`` once the productivity bundle's one-time "minimize instead of Done" entry
was shown (R34); ``"followups_off": true`` while the follow-up list is turned off on
this PC (its section's "Turn off on this PC"); ``"desktop_turned_on": true`` once the
owner clicked Turn on on the first-run "Jarvis on your desktop" card (Settings >
Desktop's Turn off takes it away again), with ``"desktop_consent_version": 2`` once the
card he turned on listed Phase 2's rows (none: Phase 1's card, version 1); ``"look":
{"sounds_off": true, "second_screen": true, "board_hints": 2}`` for the new look's switches
on this PC (Settings > Look: the soft sounds turned off, Jarvis on the second screen - true
or false once the owner chose; none: [look] second_screen decides) and how many times the
empty board's one-line hint was shown. Ids, numbers and booleans
only: never any text (the LIVE view's steps are never saved).

The file is written atomically (temporary file + ``os.replace``); a missing,
corrupt or unwritable file counts as the defaults and never stops the app
(a failed write is logged and kept in memory for this run) - except the desktop
OK, which is fail-closed: it counts only once it is saved (a Turn on that could
not be written is undone in memory too), and anything but a stored ``true`` is no.

Qt-free; standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)

PREFS_FILE = "assistant.json"
RECENT_GREETINGS_MAX = 5
_GREETING_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,15}")
BOARD_HINTS_MAX = 99            # the empty board's hint count is kept up to this


@dataclass(frozen=True)
class LookPrefs:
    """The new look's switches on this PC (Settings > Look) and the empty board's hint count."""

    sounds_off: bool = False      # the soft sounds are off on this PC (whatever [look] sounds says)
    # Jarvis shows on the second monitor: True or False once the owner chose in Settings > Look (part 7); None - never
    # chosen on this PC - lets [look] second_screen decide (the default).
    second_screen: bool | None = None
    board_hints: int = 0          # times the empty Home's board hint line was shown (it stops after a few)


class AssistantPrefs:
    """``muted`` and ``recent_greetings`` (oldest first, at most 5 ids), kept in ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        (self._muted, self._recent, self._adopted, self._live_window, self._live_popped,
         self._prod_hint, self._followups_off, self._desktop_on, self._desktop_version, self._look) = _load(self.path)

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

    @property
    def live_window(self) -> tuple[int, int, int, int] | None:
        """The LIVE pop-out's last (x, y, width, height), or None."""
        with self._lock:
            return self._live_window

    @property
    def live_popped_out(self) -> bool:
        """The LIVE pop-out was open when Jarvis last quit (it comes back at the next start)."""
        with self._lock:
            return self._live_popped

    def set_live_window(self, rect: tuple[int, int, int, int] | None, popped_out: bool) -> bool:
        """Remember the LIVE pop-out's place and size (ints) and whether it is open."""
        with self._lock:
            self._live_window = _live_rect(rect)
            self._live_popped = bool(popped_out)
            return self._save_locked()

    @property
    def prod_hint_shown(self) -> bool:
        """The productivity bundle's one-time "minimize instead of Done" entry was shown (R34)."""
        with self._lock:
            return self._prod_hint

    def set_prod_hint_shown(self) -> bool:
        """Remember that the hint was shown (it is shown once)."""
        with self._lock:
            self._prod_hint = True
            return self._save_locked()

    @property
    def followups_off(self) -> bool:
        """The follow-up list is turned off on this PC (nothing is scanned; the section says so)."""
        with self._lock:
            return self._followups_off

    def set_followups_off(self, off: bool) -> bool:
        """Turn the follow-up list off (True) or on again on this PC."""
        with self._lock:
            self._followups_off = bool(off)
            return self._save_locked()

    @property
    def desktop_turned_on(self) -> bool:
        """The owner clicked Turn on on the first-run "Jarvis on your desktop" card and it was saved (until Settings >
        Desktop's Turn off). Anything else - never clicked, a file that could not be read, a value that is not
        ``true`` - is no."""
        with self._lock:
            return self._desktop_on is True

    @property
    def desktop_consent_version(self) -> int:
        """The version of the card the owner turned on: 0 while not turned on, 1 for Phase 1's card (a Turn on saved
        before the card listed Phase 2's rows), else the version saved with it."""
        with self._lock:
            return max(1, self._desktop_version) if self._desktop_on is True else 0

    def set_desktop_turned_on(self, on: bool, version: int = 1) -> bool:
        """The owner's Turn on (True) of the card of ``version``, or his Turn off (False: both go); True when it was
        saved. Fail-closed: a Turn on that could not be saved is undone at once (it never counts unsaved - what was
        saved before stays); a Turn off counts for this run even unsaved."""
        with self._lock:
            before = (self._desktop_on, self._desktop_version)
            self._desktop_on = bool(on)
            self._desktop_version = max(1, int(version)) if on else 0
            saved = self._save_locked()
            if not saved and self._desktop_on:
                self._desktop_on, self._desktop_version = before    # never on unsaved
            return saved

    @property
    def look(self) -> LookPrefs:
        """The new look's switches on this PC (``LookPrefs``)."""
        with self._lock:
            return self._look

    def set_sounds_off(self, off: bool) -> bool:
        """Settings > Look: the soft sounds off (True) or on again on this PC."""
        with self._lock:
            self._look = replace(self._look, sounds_off=bool(off))
            return self._save_locked()

    def set_second_screen(self, on: bool) -> bool:
        """Settings > Look: Jarvis on the second monitor (True) or not, on this PC."""
        with self._lock:
            self._look = replace(self._look, second_screen=bool(on))
            return self._save_locked()

    def note_board_hint(self) -> bool:
        """The empty board's hint line was shown once more (counted up to ``BOARD_HINTS_MAX``)."""
        with self._lock:
            self._look = replace(self._look, board_hints=min(BOARD_HINTS_MAX, self._look.board_hints + 1))
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
        if self._live_window is not None:
            x, y, width, height = self._live_window
            values["live_window"] = {"x": x, "y": y, "w": width, "h": height}
        if self._live_window is not None or self._live_popped:
            values["live_popped_out"] = self._live_popped
        if self._prod_hint:
            values["prod_hint_shown"] = True
        if self._followups_off:
            values["followups_off"] = True
        if self._desktop_on is True:
            values["desktop_turned_on"] = True
            if self._desktop_version > 1:
                values["desktop_consent_version"] = self._desktop_version
        if self._look != LookPrefs():
            look: dict[str, object] = {}
            if self._look.sounds_off:
                look["sounds_off"] = True
            if self._look.second_screen is not None:
                look["second_screen"] = bool(self._look.second_screen)   # an Off chosen here outlives [look]'s on
            if self._look.board_hints:
                look["board_hints"] = self._look.board_hints
            values["look"] = look
        return _write_atomic(self.path, json.dumps(values, indent=2) + "\n")


_LIVE_RECT_LIMIT = 100_000   # any sane screen coordinate or size is far below this


def _live_rect(value: object) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) as ints from a tuple or a {"x", "y", "w", "h"} table; None when malformed."""
    if isinstance(value, dict):
        value = tuple(value.get(key) for key in ("x", "y", "w", "h"))
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) and abs(item) < _LIVE_RECT_LIMIT
               for item in value):
        return None
    x, y, width, height = value
    return (x, y, width, height) if width > 0 and height > 0 else None



_Loaded = tuple[bool, list[str], bool, tuple[int, int, int, int] | None, bool, bool, bool, bool, int, LookPrefs]
_DEFAULTS: _Loaded = (False, [], False, None, False, False, False, False, 0, LookPrefs())
_CONSENT_VERSIONS = range(1, 100)   # a stored card version outside this is none (Phase 1's card)


def _load(path: Path) -> _Loaded:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return _DEFAULTS
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read %s (%s); using the defaults", path, type(exc).__name__)
        return _DEFAULTS
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; using the defaults", path)
        return _DEFAULTS
    if not isinstance(data, dict):
        logger.warning("%s is not a table of settings; using the defaults", path)
        return _DEFAULTS
    muted = data.get("muted") is True
    raw = data.get("recent_greetings")
    recent = [item for item in raw if isinstance(item, str) and _GREETING_ID_RE.fullmatch(item)] \
        if isinstance(raw, list) else []
    return (muted, recent[-RECENT_GREETINGS_MAX:], data.get("answers_adopted") is True,
            _live_rect(window) if isinstance(window := data.get("live_window"), dict) else None,
            data.get("live_popped_out") is True, data.get("prod_hint_shown") is True,
            data.get("followups_off") is True, data.get("desktop_turned_on") is True, _consent_version(data),
            _look(data.get("look")))


def _look(raw: object) -> LookPrefs:
    """The "look" table (booleans that are exactly true, the second screen's true or false, a hint count 0-99);
    anything else is the default."""
    if not isinstance(raw, dict):
        return LookPrefs()
    hints = raw.get("board_hints")
    count = hints if isinstance(hints, int) and not isinstance(hints, bool) and 0 <= hints <= BOARD_HINTS_MAX else 0
    second = raw.get("second_screen")
    return LookPrefs(sounds_off=raw.get("sounds_off") is True,
                     second_screen=second if isinstance(second, bool) else None, board_hints=count)


def _consent_version(data: dict) -> int:
    """The card version saved with the desktop OK (an int 1-99; anything else - or none - is Phase 1's card: 1)."""
    if data.get("desktop_turned_on") is not True:
        return 0
    version = data.get("desktop_consent_version")
    if isinstance(version, int) and not isinstance(version, bool) and version in _CONSENT_VERSIONS:
        return version
    return 1


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
