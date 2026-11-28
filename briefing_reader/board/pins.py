"""The board's pins kept across a restart (no Qt): board_pins.json in the app's data folder, on this PC only.

    PinStore(data_dir)
        load() -> list[Pin]          what the file holds: at most MAX_PINS pins in the order the owner had them, each
                                     frame checked again as the page would take it (board.protocol); a missing file is
                                     no pins; a damaged, too large or unreadable one is no pins too (logged by its
                                     reason word only) and the next save replaces it; a newer one is no pins and is
                                     never written over (a newer Jarvis keeps it)
        save(pins) -> bool           the pins as they are now, written only when they changed: atomically (temporary
                                     file, fsync, atomic.replace), versioned, at most MAX_FILE_BYTES; no pins left: the
                                     file goes. False when it could not be written (kept for this run only)
    kept(pinned, quiet_groups) -> list[Pin]
                                 what of board.feed.BoardFeed.pinned_view() may be kept (``keepable``), in its order
    keepable(kind, sources, key) -> bool
                                 the rule below

What a pin needs to be shown again is its frame message as its builder made it (kind, title, meta, body, status, the
amber chip's words), its source key (a later answer built from the same source builds it again in its place, and a
document's frame opens its document) and the card its chip opens (an id). Nothing else is kept: never its picture, a
log line or the app's flags.

What is kept (the privacy rules of the sources the frames come from; desk_ui names the sources):

* Jarvis's own answers: a web research's answer and its sources' titles and hosts ("research"), Ask's typed frames -
  a table, a chart, a timeline, maths and a change's big number ("ask:N", "ask:N:number") - and an instant sum's working
  ("instant"): the conversation Jarvis keeps on this PC holds the same answers;
* what Jarvis keeps on this PC already: a capture's frame ("capture": captures.json holds what it found), the to-do
  list ("todos": its to-dos are the Approvals cards and deadlines Jarvis keeps), a document's or sheet's frame ("doc",
  "sheet", keyed "doc:<id>" / "sheet:<id>": the documents folder holds them).

What is never kept (its frame stays pinned for this session only):

* anything from LIVE's pictures - a mail thread Jarvis read (its text), a reply or an email that waits on Approvals,
  a day or a week of the calendar, a page picture, a web search's results page: LIVE keeps them in memory only, never
  in a file (live.py), and a day's frame is the clock's (its now line) - Jarvis draws it again on the next question;
* the follow-ups (a mail list's senders and subjects: followups.py writes only hashes and times);
* the canvas's resting group "Now" (``quiet_groups``): it is made again from what the app holds at every start.

Logs: counts and reason words only - never a title, a word of a frame, a key or a path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import atomic, overrides
from . import protocol

logger = logging.getLogger(__name__)

PINS_FILE = "board_pins.json"
VERSION = 1
MAX_FILE_BYTES = protocol.MAX_PINS * protocol.MESSAGE_CAP + 16 * 1024   # six frames at the page's own cap, and room
DAMAGED, NEWER, TOO_LARGE, UNREADABLE = "damaged", "newer", "too_large", "unreadable"
TYPED_KINDS = frozenset({"table", "chart", "timeline", "maths", "number"})
# A source's class (its words before the first ":") -> the kinds of its frames that are kept.
KEPT_SOURCES: dict[str, frozenset[str]] = {
    "research": frozenset({"text"}), "ask": TYPED_KINDS, "instant": frozenset({"maths"}),
    "capture": frozenset({"capture"}), "todos": frozenset({"todos"}),
    "doc": frozenset({"text"}), "sheet": frozenset({"text", "table"}),
}
_KEYED_SOURCES = {"doc": "doc:", "sheet": "sheet:"}     # these are kept only with their own key (the item's id)
_KEPT_FIELDS = ("kind", "title", "meta", "body", "status", "status_text", "status_meta", "waits")
_SOURCE = re.compile(r"[a-z][a-z0-9:_-]{0,39}")
_KEY_CAP = 200
_CARD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def base_source(source: str) -> str:
    """A source as desk_ui named it, without the "#N" a restored pin's source carries ("research#2" -> "research")."""
    return str(source or "").split("#", 1)[0]


def keepable(kind: str, sources: Iterable[str], key: str = "") -> bool:
    """A pinned frame of ``kind`` built from ``sources`` (every one of them) may be kept on this PC: see the module
    doc."""
    found = [base_source(source) for source in sources]
    if not found:
        return False
    for source in found:
        cls = source.split(":", 1)[0]
        if kind not in KEPT_SOURCES.get(cls, frozenset()):
            return False
        prefix = _KEYED_SOURCES.get(cls)
        if prefix is not None and not (str(key or "").startswith(prefix) and len(key) > len(prefix)):
            return False
    return True


@dataclass(frozen=True, repr=False)
class Pin:
    """One kept pin: its source (as desk_ui named it), its source key, the card its chip opens ("" none) and its frame
    as its builder made it (the kept fields only). repr: its kind only."""

    source: str
    key: str
    approvals: str
    frame: dict[str, Any]

    @property
    def kind(self) -> str:
        return str(self.frame.get("kind", ""))

    def message(self, frame_id: str) -> dict[str, Any]:
        """The frame message for the board, under ``frame_id``."""
        message: dict[str, Any] = {"type": "frame", "id": frame_id}
        message.update(json.loads(json.dumps(self.frame)))      # a copy: the board never shares the kept one
        return message

    def data(self) -> dict[str, Any]:
        return {"source": self.source, "key": self.key, "approvals": self.approvals, "frame": self.frame}

    def __repr__(self) -> str:
        return f"Pin({self.kind})"


def _checked_frame(frame: Any) -> dict[str, Any] | None:
    """The frame's kept fields as the page would take them (board.protocol.check_message), else None."""
    if not isinstance(frame, dict):
        return None
    message = {field: frame[field] for field in _KEPT_FIELDS if field in frame}
    message.update(type="frame", id="f1")
    try:
        checked = protocol.check_message(message)
    except protocol.Refused:
        return None
    clean = json.loads(checked.text)
    return {field: clean[field] for field in _KEPT_FIELDS if field in clean}


def make_pin(source: Any, key: Any, approvals: Any, frame: Any) -> Pin | None:
    """A Pin when every part of it checks (a source desk_ui names, a key and a card id of the right shape, a frame
    the page takes, of a kind ``keepable`` allows); else None."""
    source = base_source(source) if isinstance(source, str) else ""
    key = key if isinstance(key, str) else None
    approvals = approvals if isinstance(approvals, str) else None
    if not _SOURCE.fullmatch(source) or key is None or len(key) > _KEY_CAP or approvals is None:
        return None
    if approvals and not _CARD_ID.fullmatch(approvals):
        return None
    if any(ord(char) < 32 for char in key):
        return None
    checked = _checked_frame(frame)
    if checked is None or not keepable(checked["kind"], (source,), key):
        return None
    return Pin(source, key, approvals, checked)


def kept(pinned: Iterable[Any], quiet_groups: Collection[str] = ()) -> list[Pin]:
    """What of the pinned frames (board.feed.PinnedFrame, in board order) may be kept: never a frame of a quiet group
    (the canvas's "Now"), never one built from a source that is not kept (``keepable``); at most MAX_PINS, one per
    source key."""
    found: list[Pin] = []
    keys: set[str] = set()
    for item in pinned:
        if getattr(item, "group", "") in quiet_groups:
            continue
        sources = tuple(getattr(item, "sources", ()) or ())
        frame = getattr(item, "frame", None)
        kind = frame.get("kind", "") if isinstance(frame, dict) else ""
        key = str(getattr(item, "key", "") or "")
        if not sources or not keepable(kind, sources, key):
            continue
        pin = make_pin(sources[-1], key, str(getattr(item, "approvals", "") or ""), frame)
        if pin is None or (pin.key and pin.key in keys):
            continue
        keys.add(pin.key)
        found.append(pin)
        if len(found) >= protocol.MAX_PINS:
            break
    return found


def _text(pins: Iterable[Pin]) -> str:
    return json.dumps({"version": VERSION, "pins": [pin.data() for pin in pins]}, ensure_ascii=True, indent=1,
                      sort_keys=True) + "\n"


class PinStore:
    """board_pins.json: {"version": 1, "pins": [{"source", "key", "approvals", "frame": {...}}, ...]}, in the order the
    owner had them. Written by the app only."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / PINS_FILE
        self.problem = ""
        self._written: str | None = None     # what the file holds now ("" none), once read or written
        self._keep_newer = False

    def load(self) -> list[Pin]:
        """See the module doc."""
        self.problem = ""
        if not overrides.readable(self.path):
            return []                        # (while the tests run: only a file in the temporary folder)
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            self._written = ""
            return []
        except OSError:
            return self._refuse(UNREADABLE)
        if len(raw) > MAX_FILE_BYTES:
            return self._refuse(TOO_LARGE)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return self._refuse(DAMAGED)
        version = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return self._refuse(DAMAGED)
        if version > VERSION:
            self._keep_newer = True
            return self._refuse(NEWER)
        records = payload.get("pins")
        if not isinstance(records, list):
            return self._refuse(DAMAGED)
        pins: list[Pin] = []
        keys: set[str] = set()
        skipped = 0
        for record in records:
            pin = make_pin(record.get("source"), record.get("key"), record.get("approvals"),
                           record.get("frame")) if isinstance(record, dict) else None
            if pin is None or (pin.key and pin.key in keys) or len(pins) >= protocol.MAX_PINS:
                skipped += 1
                continue
            keys.add(pin.key)
            pins.append(pin)
        if skipped:
            logger.warning("Board: %d kept pin(s) did not check and are left out", skipped)
        self._written = _text(pins) if not skipped else None
        return pins

    def _refuse(self, problem: str) -> list[Pin]:
        self.problem = problem
        logger.warning("Board: board_pins.json is %s; no pins came back", problem)
        return []

    def save(self, pins: Iterable[Pin]) -> bool:
        """See the module doc."""
        pins = list(pins)[:protocol.MAX_PINS]
        if self._keep_newer:
            return False                     # a newer Jarvis's file is never written over
        if not overrides.readable(self.path):
            return False                     # (while the tests run: never outside the temporary folder)
        if not pins:
            return self._remove()
        text = _text(pins)
        if text == self._written:
            return True
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            logger.warning("Board: the pins were not saved (too_large)")
            return False
        part: str | None = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=f"{PINS_FILE}.", suffix=".tmp", dir=self.data_dir)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            atomic.replace(part, self.path)
            part = None
            self._written = text
            logger.info("Board: %d pin(s) kept", len(pins))
            return True
        except OSError as exc:
            logger.warning("Board: the pins were not saved (%s)", type(exc).__name__)
            return False
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    def _remove(self) -> bool:
        if self._written == "":
            return True
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Board: the pins file was not removed (%s)", type(exc).__name__)
            return False
        self._written = ""
        logger.info("Board: no pins kept")
        return True


__all__ = ["KEPT_SOURCES", "MAX_FILE_BYTES", "PINS_FILE", "Pin", "PinStore", "VERSION", "base_source", "keepable",
           "kept", "make_pin"]
