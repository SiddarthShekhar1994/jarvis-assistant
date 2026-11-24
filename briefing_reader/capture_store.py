"""The captures' file in the data folder (no Qt): captures.json and the kept copies of their sources.

    CaptureStore(data_dir)       captures.json, written by the app only, atomically (temporary file, fsync,
                                 atomic.replace) and versioned; a damaged, too large or newer file is kept READ-ONLY
                                 (nothing is saved over it, the problem is logged by its word only)
        add(...)                 a new capture: its label, kind, counts and at most MAX_CARDS_PER_CAPTURE cards (each
                                 kept as its CapturedItem and id); captures past MAX_KEPT_CARDS cards in all are
                                 dropped, the oldest first, with their kept copies
        cards(today)             every kept card, rebuilt (card_from) and re-checked: a record whose card now has
                                 another id is dropped
        remove(capture_id)       Clear: the capture and its kept copy go at once
        prune(now, decided_at)   a capture goes 30 days after it was made, or 7 days after its last card was
                                 decided (``decided_at(card_id)``: when, else None)
        keep_source(id, data, ext) / source_path(id)
                                 the kept copy: <data folder>\\captures\\<id>\\source.<ext>
    card_from(record, label, today)
                                 one kept card rebuilt from its item (capture.to_card), or None

What the file holds (titles, places, notes, a file's label) is never logged: the log gets counts and problem words.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import atomic
from .actions import ProposedAction
from .capture import (EXTENSIONS, MAX_CARDS_PER_CAPTURE, MAX_KEPT_CARDS, CapturedItem, item_from_data, item_to_data,
                      to_card)

logger = logging.getLogger(__name__)

CAPTURES_FILE = "captures.json"
CAPTURES_FOLDER = "captures"
VERSION = 1
MAX_FILE_BYTES = 4_000_000
KEEP_DAYS = 30
KEEP_DECIDED_DAYS = 7
DAMAGED, NEWER, TOO_LARGE = "damaged", "newer", "too_large"
CAPTURE_ID_RE = re.compile(r"[0-9a-f]{12}")
_CARD_ID_RE = re.compile(r"[0-9a-f]{16}")
_KINDS = frozenset({"ics", "link", "picture", "pdf", "text"})


def new_capture_id() -> str:
    return secrets.token_hex(6)


def _now() -> datetime:
    return datetime.now().astimezone()


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_stamp(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def card_from(record: Any, label: str, today: date) -> ProposedAction | None:
    """A kept card rebuilt: its item read back and made into a card again (capture.to_card); None when the record is
    damaged or the card now has another id."""
    if not isinstance(record, Mapping):
        return None
    card_id = record.get("id")
    if not isinstance(card_id, str) or not _CARD_ID_RE.fullmatch(card_id):
        return None
    item = item_from_data(record.get("item"))
    if item is None:
        return None
    try:
        card = to_card(item, label, today=today)
    except (ValueError, TypeError):
        return None
    return card if card.id == card_id else None


class CaptureStore:
    """captures.json: {"version": 1, "captures": {id: {"at", "label", "kind", "ext", "counts", "cards": [{"id",
    "item"}]}}}, oldest first. Written by the app only."""

    def __init__(self, data_dir: Path | str, *, clock: Callable[[], datetime] = _now) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / CAPTURES_FILE
        self._clock = clock
        self._lock = threading.Lock()
        self.read_only = False
        self.problem = ""
        self._captures: dict[str, dict[str, Any]] = {}
        self._load()

    # ---- the file ------------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return
        except OSError:
            self._refuse(DAMAGED)
            return
        if len(raw) > MAX_FILE_BYTES:
            self._refuse(TOO_LARGE)
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._refuse(DAMAGED)
            return
        version = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            self._refuse(DAMAGED)
            return
        if version > VERSION:
            self._refuse(NEWER)
            return
        captures = payload.get("captures")
        if not isinstance(captures, dict):
            self._refuse(DAMAGED)
            return
        skipped = 0
        for capture_id, entry in captures.items():
            if not CAPTURE_ID_RE.fullmatch(str(capture_id)) or not self._entry_checks(entry):
                skipped += 1
                continue
            self._captures[capture_id] = entry
        if skipped:
            logger.warning("Capture: %d kept capture(s) did not check and are left out", skipped)

    @staticmethod
    def _entry_checks(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        if _parse_stamp(entry.get("at")) is None or not isinstance(entry.get("label"), str):
            return False
        if entry.get("kind") not in _KINDS or entry.get("ext", "") not in ("", *EXTENSIONS):
            return False
        cards = entry.get("cards")
        return isinstance(cards, list) and isinstance(entry.get("counts", {}), dict)

    def _refuse(self, problem: str) -> None:
        self.read_only, self.problem = True, problem
        logger.warning("Capture: captures.json is %s; the captures are kept read-only", problem)

    def _save(self) -> bool:
        if self.read_only:
            logger.warning("Capture: captures.json is %s; nothing was saved", self.problem or "read-only")
            return False
        text = json.dumps({"version": VERSION, "captures": self._captures}, ensure_ascii=False, indent=1,
                          sort_keys=False) + "\n"
        part: str | None = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=f"{CAPTURES_FILE}.", suffix=".tmp", dir=self.data_dir)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            atomic.replace(part, self.path)
            part = None
            return True
        except OSError as exc:
            logger.warning("Capture: could not save captures.json (%s)", type(exc).__name__)
            return False
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    # ---- captures ------------------------------------------------------------------------------------------
    def add(self, capture_id: str, *, label: str, kind: str, cards: list[tuple[CapturedItem, ProposedAction]],
            counts: Mapping[str, int] | None = None, ext: str = "") -> bool:
        """Keep a new capture (its first MAX_CARDS_PER_CAPTURE cards); True when it was saved."""
        if not CAPTURE_ID_RE.fullmatch(capture_id or "") or kind not in _KINDS:
            raise ValueError("not a capture id or kind")
        records = [{"id": card.id, "item": item_to_data(item)} for item, card in cards[:MAX_CARDS_PER_CAPTURE]]
        with self._lock:
            if self.read_only:
                return False
            self._captures.pop(capture_id, None)
            self._captures[capture_id] = {
                "at": _stamp(self._clock()), "label": str(label or "")[:200], "kind": kind,
                "ext": ext if ext in EXTENSIONS else "",
                "counts": {str(k): int(v) for k, v in (counts or {}).items() if isinstance(v, int)},
                "cards": records}
            dropped = self._bound()
            saved = self._save()
        for old in dropped:
            self._delete_folder(old)
        return saved

    def _bound(self) -> list[str]:
        """Drop the oldest captures until at most MAX_KEPT_CARDS cards are kept (the newest is never dropped)."""
        dropped: list[str] = []
        total = sum(len(entry["cards"]) for entry in self._captures.values())
        for capture_id in list(self._captures)[:-1]:
            if total <= MAX_KEPT_CARDS:
                break
            total -= len(self._captures.pop(capture_id)["cards"])
            dropped.append(capture_id)
        return dropped

    def captures(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [(capture_id, dict(entry)) for capture_id, entry in self._captures.items()]

    def capture(self, capture_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._captures.get(capture_id)
            return dict(entry) if entry is not None else None

    def cards(self, today: date | None = None) -> list[tuple[str, ProposedAction]]:
        """(capture id, card) for every kept card that still checks, oldest capture first."""
        today = today or self._clock().date()
        found: list[tuple[str, ProposedAction]] = []
        dropped = 0
        for capture_id, entry in self.captures():
            for record in entry.get("cards") or ():
                card = card_from(record, entry.get("label", ""), today)
                if card is None:
                    dropped += 1
                    continue
                found.append((capture_id, card))
        if dropped:
            logger.info("Capture: %d kept card(s) no longer check and are left out", dropped)
        return found

    def remove(self, capture_id: str) -> bool:
        """Clear: the capture and its kept copy go now. True when something was removed."""
        with self._lock:
            had = self._captures.pop(capture_id, None) is not None
            if had:
                self._save()
        self._delete_folder(capture_id)
        return had

    def prune(self, now: datetime | None = None, decided_at: Callable[[str], datetime | None] | None = None,
              *, pending: Callable[[ProposedAction], bool] | None = None) -> list[str]:
        """Drop captures KEEP_DAYS after they were made, or KEEP_DECIDED_DAYS after their last card was decided
        (every card that takes a decision decided: ``decided_at`` says when). Returns the ids dropped."""
        now = now or self._clock()
        dropped: list[str] = []
        for capture_id, entry in self.captures():
            made = _parse_stamp(entry.get("at"))
            if made is None or now - made >= timedelta(days=KEEP_DAYS):
                dropped.append(capture_id)
                continue
            if decided_at is None:
                continue
            cards = [card for card in (card_from(r, entry.get("label", ""), now.date()) for r in entry.get("cards") or ())
                     if card is not None and card.decidable]
            if not cards:
                continue
            times = [decided_at(card.id) for card in cards]
            if all(t is not None for t in times) and now - max(times) >= timedelta(days=KEEP_DECIDED_DAYS):  # type: ignore[type-var]
                dropped.append(capture_id)
        if dropped:
            with self._lock:
                for capture_id in dropped:
                    self._captures.pop(capture_id, None)
                self._save()
            for capture_id in dropped:
                self._delete_folder(capture_id)
            logger.info("Capture: %d old capture(s) pruned", len(dropped))
        return dropped

    # ---- the kept copy -------------------------------------------------------------------------------------
    def folder(self, capture_id: str) -> Path:
        if not CAPTURE_ID_RE.fullmatch(capture_id or ""):
            raise ValueError("not a capture id")
        return self.data_dir / CAPTURES_FOLDER / capture_id

    def keep_source(self, capture_id: str, content: bytes, ext: str) -> Path | None:
        """Keep the capture's source as captures\\<id>\\source<ext>; None when it could not be written."""
        if ext not in EXTENSIONS:
            raise ValueError("not an allowed extension")
        if self.read_only or self.capture(capture_id) is None:
            return None                        # a copy is kept only for a capture captures.json holds
        target = self.folder(capture_id) / f"source{ext}"
        part: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix="source.", suffix=".tmp", dir=target.parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            atomic.replace(part, target)
            part = None
            return target
        except OSError as exc:
            logger.warning("Capture: could not keep the source (%s)", type(exc).__name__)
            return None
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    def source_path(self, capture_id: str) -> Path | None:
        """The kept copy of the capture's source (None when there is none)."""
        entry = self.capture(capture_id)
        if entry is None or not entry.get("ext"):
            return None
        path = self.folder(capture_id) / f"source{entry['ext']}"
        return path if path.is_file() else None

    def _delete_folder(self, capture_id: str) -> None:
        try:
            folder = self.folder(capture_id)
        except ValueError:
            return
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
