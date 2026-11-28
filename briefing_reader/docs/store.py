"""The documents and datasheets on this PC: ``%LOCALAPPDATA%\\briefing-reader\\docs\\`` (not synced; no Qt).

    DocsStore(data_dir, clock=None)
        items(trashed=False) / info(item_id)       what the index lists (ItemInfo), newest change first
        create_doc(name, text="") / create_sheet(name, columns=(), keys=())   -> (id, "") or ("", reason)
        read_doc(item_id) -> DocData | None        doc.md read again (a change made outside Jarvis becomes a version
                                                   "edited outside Jarvis" first) and its pending changes
        save_doc(item_id, text, version=VERSION_EDIT, burst=True)   -> "" or a reason word
        read_sheet(item_id) -> SheetData | None / save_sheet(item_id, sheet, version=None)
        set_doc_pending(item_id, pending)          a document's pending changes (pending.json)
        rename(item_id, name)
        delete(item_id) / restore(item_id) / deleted()   to the trash (kept 30 days) and back
        versions(item_id) / read_version(item_id, name) / restore_version(item_id, name)
        imported() / mark_imported(run_ids)       standing runs already taken in (at most 2,000 ids)
        find_by_name(name, kind="")                an item whose name folds to ``name``'s (read only)
        export_md(item_id) / export_csv(item_id)   the kept text / the kept rows (never what waits)
    write_export(path, data, mark=False)           the export's file (the .csv with the Mark of the Web)
    synced_folder(path)                            the folder is synced by OneDrive (the file would leave this PC)
    guard_cell(text)                               a cell a spreadsheet would read as a formula gets a leading '
    find_by_name(data_dir, name, kind="") / read_columns(data_dir, sheet_id)
                                                   read-only looks for the standing setup and runner: they never
                                                   write here
    listed(data_dir) / read_doc_text(data_dir, doc_id) / read_sheet_rows(data_dir, sheet_id)
                                                   read-only looks for other AI apps (mcp_server): the items not
                                                   deleted, a document's kept text, a sheet's kept columns and rows
                                                   (never what waits); nothing is written, not even a version

Files: index.json ``{"version": 1, "items": [{id, kind, name, created, changed, words, rows, columns, pending,
trashed, hash}], "imported": [...]}``; ``d-<12 hex>\\doc.md`` (UTF-8, LF) and ``pending.json``; ``s-<12 hex>\\
sheet.json`` ``{"version": 1, "columns": [{id, name, type, choices}], "keys": [column ids], "rows": [{id, cells,
from, made}], "pending": [...]}``; ``<id>\\versions\\<stamp>-<reason>.md|json`` (30 or 4 MB per item, oldest go
first); ``trash\\<id>\\`` (a deleted item, 30 days). Every write is atomic (a temporary file, fsync, os.replace under
a lock); the app is the only writer. A damaged, newer or too-large index makes the whole store read-only for this run
(never overwritten); a damaged or too-large item is read-only on its own. Logs: ids, kinds, counts, sizes and reason
words only (never a name, a heading, a cell or a column name).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import atomic
from . import model

logger = logging.getLogger(__name__)

DOCS_FOLDER = "docs"
INDEX_FILE = "index.json"
DOC_FILE = "doc.md"
PENDING_FILE = "pending.json"
SHEET_FILE = "sheet.json"
VERSIONS_FOLDER = "versions"
TRASH_FOLDER = "trash"
FILE_VERSION = 1
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_PENDING_BYTES = 1024 * 1024
KEEP_VERSIONS = 30
KEEP_VERSION_BYTES = 4 * 1024 * 1024
BURST_SECONDS = 120
TRASH_DAYS = 30

STATE_OK, STATE_DAMAGED, STATE_NEWER, STATE_TOO_LARGE = "ok", "damaged", "newer", "too_large"
VERSION_EDIT, VERSION_KEEP, VERSION_RESTORE, VERSION_OUTSIDE, VERSION_IMPORT = (
    "edit", "keep", "restore", "outside", "import")
VERSION_REASONS = (VERSION_EDIT, VERSION_KEEP, VERSION_RESTORE, VERSION_OUTSIDE, VERSION_IMPORT)
VERSION_WORDS = {VERSION_EDIT: "While you edited", VERSION_KEEP: "Before a Keep", VERSION_RESTORE: "Before a Restore",
                 VERSION_OUTSIDE: "Edited outside Jarvis", VERSION_IMPORT: "Before an agent's rows"}
FROM_OWNER = "owner"

R_READ_ONLY = "read_only"
R_MISSING = "missing"
R_TOO_MANY = "too_many_items"
R_TOO_LONG = "too_long"
R_TOO_LARGE = "too_large"
R_NOT_SAVED = "not_saved"
R_NO_NAME = "no_name"
R_NOT_FOUND = "not_found"
REFUSAL_WORDS = {R_READ_ONLY: "Jarvis can't change this here: its file can't be read safely, so it is left as it is",
                 R_MISSING: "That document is gone",
                 R_TOO_MANY: "Jarvis keeps at most 300 documents and sheets; delete one first",
                 R_TOO_LONG: "A document holds at most 200,000 characters",
                 R_TOO_LARGE: "That is more than a sheet holds (20 columns, 2,000 rows)",
                 R_NOT_SAVED: "Jarvis couldn't save this in the data folder; it is kept for this run only",
                 R_NO_NAME: "It needs a name",
                 R_NOT_FOUND: "That version is gone"}
CSV_GUARD_FIRST = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ItemInfo:
    id: str
    kind: str
    name: str
    created: str
    changed: str
    words: int = 0
    rows: int = 0
    columns: int = 0
    pending: int = 0
    trashed: str = ""
    read_only: bool = False


@dataclass
class DocData:
    id: str
    name: str
    text: str
    pending: list[dict[str, Any]] = field(default_factory=list)
    read_only: bool = False


@dataclass
class Row:
    id: str
    cells: dict[str, Any]
    source: str = FROM_OWNER
    made: str = ""


@dataclass
class SheetData:
    id: str
    name: str
    columns: list[model.Column] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    read_only: bool = False

    def column(self, column_id: str) -> model.Column | None:
        return next((column for column in self.columns if column.id == column_id), None)

    def row(self, row_id: str) -> Row | None:
        return next((row for row in self.rows if row.id == row_id), None)

    def copy(self) -> SheetData:
        return SheetData(self.id, self.name, list(self.columns), list(self.keys),
                         [Row(row.id, dict(row.cells), row.source, row.made) for row in self.rows],
                         [dict(item) for item in self.pending], self.read_only)


@dataclass(frozen=True)
class VersionInfo:
    name: str          # the file's name in versions\ (its stamp and reason)
    when: datetime
    reason: str
    size: int


class _Unreadable(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, data: bytes) -> bool:
    part: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, part = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        atomic.replace(part, path)
        part = None
        return True
    except OSError as exc:
        logger.warning("Docs: a file could not be saved (%s)", type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass


def _read_bytes(path: Path, cap: int) -> bytes | None:
    """At most ``cap`` + 1 bytes (a larger file is refused by the caller); None when there is no file."""
    try:
        with open(path, "rb") as fh:
            return fh.read(cap + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unreadable(type(exc).__name__) from exc


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=True, indent=1) + "\n").encode("ascii")


def _load_json(raw: bytes) -> Any:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    return json.loads(text)


def _now_text(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_time(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def _int(value: Any, cap: int = 10**9) -> int:
    return max(0, min(cap, value)) if isinstance(value, int) and not isinstance(value, bool) else 0


def _entry_from(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    item_id = data.get("id")
    kind = model.kind_of(item_id) if isinstance(item_id, str) else ""
    if not kind or data.get("kind") != kind:
        return None
    name = model.clean_name(data.get("name")) or ("Untitled document" if kind == model.KIND_DOC else "Untitled sheet")
    created = data.get("created") if isinstance(data.get("created"), str) else ""
    changed = data.get("changed") if isinstance(data.get("changed"), str) else created
    return {"id": item_id, "kind": kind, "name": name, "created": created, "changed": changed,
            "words": _int(data.get("words")), "rows": _int(data.get("rows")), "columns": _int(data.get("columns")),
            "pending": _int(data.get("pending")), "trashed": data.get("trashed") if isinstance(
                data.get("trashed"), str) else "", "hash": data.get("hash") if isinstance(data.get("hash"), str) else ""}


def _sheet_from(data: Any, item_id: str, name: str) -> SheetData | None:
    """sheet.json's content, or None when it is not a sheet this version reads."""
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        return None
    raw_columns = data.get("columns")
    raw_rows = data.get("rows")
    if not isinstance(raw_columns, list) or not isinstance(raw_rows, list):
        return None
    columns: list[model.Column] = []
    for raw in raw_columns[:model.MAX_COLUMNS]:
        column = model.column_from(raw)
        if column is None or any(other.id == column.id for other in columns):
            return None
        columns.append(column)
    by_id = {column.id: column for column in columns}
    keys = [key for key in data.get("keys") or [] if isinstance(key, str) and key in by_id][:model.MAX_KEY_COLUMNS]
    rows: list[Row] = []
    seen: set[str] = set()
    for raw in raw_rows[:model.MAX_ROWS]:
        if not isinstance(raw, dict) or not model.valid_row_id(raw.get("id")) or raw["id"] in seen:
            return None
        seen.add(raw["id"])
        cells_raw = raw.get("cells") if isinstance(raw.get("cells"), dict) else {}
        cells = {cid: model.stored_value(by_id[cid], value) for cid, value in cells_raw.items() if cid in by_id}
        rows.append(Row(raw["id"], {cid: value for cid, value in cells.items() if value is not None},
                        str(raw.get("from") or FROM_OWNER)[:40], str(raw.get("made") or "")[:40]))
    pending = [item for item in data.get("pending") or [] if isinstance(item, dict)][:model.MAX_PENDING_ROWS * 2]
    return SheetData(item_id, name, columns, keys, rows, pending)


def _sheet_data(sheet: SheetData) -> dict[str, Any]:
    return {"version": FILE_VERSION, "columns": [model.column_data(column) for column in sheet.columns],
            "keys": list(sheet.keys),
            "rows": [{"id": row.id, "cells": {cid: value for cid, value in row.cells.items() if value is not None},
                      "from": row.source, "made": row.made} for row in sheet.rows],
            "pending": list(sheet.pending)}


def guard_cell(text: str) -> str:
    """A .csv cell a spreadsheet would read as a formula (= + - @, a tab or a CR first) gets a leading '."""
    return "'" + text if text.startswith(CSV_GUARD_FIRST) else text


def synced_folder(path: str | os.PathLike[str], environ: Mapping[str, str] | None = None) -> bool:
    """``path`` lies in a OneDrive folder (agent.workspace.under_onedrive): a file saved there leaves this PC."""
    from ..agent.workspace import under_onedrive

    return under_onedrive(Path(path), os.environ if environ is None else environ)


def write_export(path: str | os.PathLike[str], data: bytes, *, mark: bool = False) -> str:
    """The export's file at ``path`` (written whole through a temporary file); ``mark``: the Mark of the Web (a .csv,
    so a spreadsheet opens it as from the internet). "" or R_NOT_SAVED."""
    target = Path(path)
    if not _write_atomic(target, data):
        return R_NOT_SAVED
    if mark:
        from ..agent.workspace import _mark_from_the_web

        _mark_from_the_web(str(target))
    return ""


def default_export_folder(setting: str = "", environ: Mapping[str, str] | None = None) -> Path | None:
    """[docs] export_folder, or Jarvis\\Exports in the user folder (None: no user folder known). Not made here."""
    environ = os.environ if environ is None else environ
    if setting.strip():
        return Path(setting.strip())
    home = str(environ.get("USERPROFILE") or "").strip()
    return Path(home) / "Jarvis" / "Exports" if home else None


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d-%H%M%S-%f")


def _version_from(path: Path, zone_of: datetime) -> VersionInfo | None:
    stem, suffix = path.stem, path.suffix
    if suffix not in (".md", ".json"):
        return None
    parts = stem.split("-")
    if len(parts) != 4 or parts[3] not in VERSION_REASONS:
        return None
    try:
        moment = datetime.strptime("-".join(parts[:3]), "%Y%m%d-%H%M%S-%f").replace(tzinfo=zone_of.tzinfo)
        size = path.stat().st_size
    except (ValueError, OSError):
        return None
    return VersionInfo(path.name, moment, parts[3], size)


class DocsStore:
    """The docs folder; see the module docstring. Thread-safe (one lock); every change is written at once."""

    def __init__(self, data_dir: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(data_dir) / DOCS_FOLDER
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.RLock()
        self.state = STATE_OK
        self.saved = True
        self._items: dict[str, dict[str, Any]] = {}
        self._imported: list[str] = []
        self._bad: set[str] = set()          # items whose own file can't be read safely (read-only)
        with self._lock:
            self._load()
            self._purge_trash()

    # ---- the index ----------------------------------------------------------------------------------------

    @property
    def read_only(self) -> bool:
        return self.state != STATE_OK

    def _load(self) -> None:
        try:
            raw = _read_bytes(self.root / INDEX_FILE, MAX_INDEX_BYTES)
        except _Unreadable:
            self._refuse(STATE_DAMAGED)
            return
        if raw is None:
            self._items, self._imported = {}, []
            return
        if len(raw) > MAX_INDEX_BYTES:
            self._refuse(STATE_TOO_LARGE)
            return
        try:
            data = _load_json(raw)
        except (ValueError, RecursionError, UnicodeDecodeError):
            self._refuse(STATE_DAMAGED)
            return
        if not isinstance(data, dict) or not isinstance(data.get("version"), int) or not isinstance(
                data.get("items"), list):
            self._refuse(STATE_DAMAGED)
            return
        if data["version"] > FILE_VERSION:
            self._refuse(STATE_NEWER)
            return
        items: dict[str, dict[str, Any]] = {}
        for raw_entry in data["items"]:
            entry = _entry_from(raw_entry)
            if entry is not None and entry["id"] not in items:
                items[entry["id"]] = entry
        self._items = items
        self._imported = [run for run in data.get("imported") or [] if isinstance(run, str)][-model.MAX_IMPORTED:]

    def _refuse(self, state: str) -> None:
        if self.state != state:
            logger.warning("Docs: index.json can't be used (%s); the documents are read-only for this run", state)
        self.state = state
        self._items, self._imported = {}, []

    def _refresh(self) -> bool:
        """index.json again before a change (the app is its only writer; a file damaged meanwhile stops writes)."""
        if self.read_only:
            return False
        self._load()
        return not self.read_only

    def _write_index(self) -> bool:
        data = {"version": FILE_VERSION, "items": list(self._items.values()), "imported": self._imported}
        ok = _write_atomic(self.root / INDEX_FILE, _json_bytes(data))
        self.saved = ok
        return ok

    def _info(self, entry: dict[str, Any]) -> ItemInfo:
        return ItemInfo(entry["id"], entry["kind"], entry["name"], entry["created"], entry["changed"], entry["words"],
                        entry["rows"], entry["columns"], entry["pending"], entry["trashed"],
                        self.read_only or entry["id"] in self._bad)

    def items(self, *, trashed: bool = False) -> list[ItemInfo]:
        with self._lock:
            chosen = [entry for entry in self._items.values() if bool(entry["trashed"]) == trashed]
            chosen.sort(key=lambda entry: entry["trashed"] if trashed else entry["changed"], reverse=True)
            return [self._info(entry) for entry in chosen]

    def info(self, item_id: str) -> ItemInfo | None:
        with self._lock:
            entry = self._items.get(item_id)
            return self._info(entry) if entry is not None else None

    def pending_total(self) -> int:
        with self._lock:
            return sum(entry["pending"] for entry in self._items.values() if not entry["trashed"])

    def find_by_name(self, name: str, kind: str = "") -> ItemInfo | None:
        key = model.fold(name)
        with self._lock:
            for entry in self._items.values():
                if not entry["trashed"] and model.fold(entry["name"]) == key and (not kind or entry["kind"] == kind):
                    return self._info(entry)
        return None

    def _folder(self, item_id: str) -> Path:
        return self.root / item_id

    def _now(self) -> datetime:
        moment = self._clock()
        return moment if moment.tzinfo is not None else moment.astimezone()

    def _touch(self, entry: dict[str, Any]) -> None:
        entry["changed"] = _now_text(self._now())

    # ---- create, rename, delete -------------------------------------------------------------------------------

    def _new_entry(self, kind: str, name: str) -> tuple[dict[str, Any] | None, str]:
        if not self._refresh():
            return None, R_READ_ONLY
        if sum(1 for entry in self._items.values() if not entry["trashed"]) >= model.MAX_ITEMS:
            return None, R_TOO_MANY
        clean = model.clean_name(name) or ("Untitled document" if kind == model.KIND_DOC else "Untitled sheet")
        item_id = model.new_id(kind)
        while item_id in self._items or self._folder(item_id).exists():
            item_id = model.new_id(kind)
        now = _now_text(self._now())
        return {"id": item_id, "kind": kind, "name": clean, "created": now, "changed": now, "words": 0, "rows": 0,
                "columns": 0, "pending": 0, "trashed": "", "hash": ""}, ""

    def create_doc(self, name: str, text: str = "") -> tuple[str, str]:
        body = model.clean_doc_text(text)
        if len(body) > model.MAX_DOC_CHARS:
            return "", R_TOO_LONG
        with self._lock:
            entry, reason = self._new_entry(model.KIND_DOC, name)
            if entry is None:
                return "", reason
            data = body.encode("utf-8")
            if not _write_atomic(self._folder(entry["id"]) / DOC_FILE, data):
                self.saved = False
                return "", R_NOT_SAVED
            entry["hash"] = _sha(data)
            entry["words"] = model.word_count(body)
            self._items[entry["id"]] = entry
            self._write_index()
            logger.info("Docs: %s made (document, %d bytes)", entry["id"], len(data))
            return entry["id"], ""

    def create_sheet(self, name: str, columns: Sequence[model.Column] = (), keys: Sequence[str] = ()) -> tuple[str, str]:
        if len(columns) > model.MAX_COLUMNS:
            return "", R_TOO_LARGE
        with self._lock:
            entry, reason = self._new_entry(model.KIND_SHEET, name)
            if entry is None:
                return "", reason
            ids = {column.id for column in columns}
            sheet = SheetData(entry["id"], entry["name"], list(columns), [key for key in keys if key in ids][:3])
            data = _json_bytes(_sheet_data(sheet))
            if not _write_atomic(self._folder(entry["id"]) / SHEET_FILE, data):
                self.saved = False
                return "", R_NOT_SAVED
            entry["hash"] = _sha(data)
            entry["columns"] = len(columns)
            self._items[entry["id"]] = entry
            self._write_index()
            logger.info("Docs: %s made (sheet, %d columns)", entry["id"], len(columns))
            return entry["id"], ""

    def rename(self, item_id: str, name: str) -> str:
        clean = model.clean_name(name)
        if not clean:
            return R_NO_NAME
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._items.get(item_id)
            if entry is None or entry["trashed"]:
                return R_MISSING
            if entry["name"] == clean:
                return ""
            entry["name"] = clean
            self._touch(entry)
            return "" if self._write_index() else R_NOT_SAVED

    def delete(self, item_id: str) -> str:
        """To the trash (Undo: restore; gone for good after 30 days)."""
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._items.get(item_id)
            if entry is None or entry["trashed"]:
                return R_MISSING
            source, target = self._folder(item_id), self.root / TRASH_FOLDER / item_id
            try:
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    os.replace(source, target)
            except OSError as exc:
                logger.warning("Docs: %s could not go to the trash (%s)", item_id, type(exc).__name__)
                return R_NOT_SAVED
            entry["trashed"] = _now_text(self._now())
            self._write_index()
            logger.info("Docs: %s deleted (to the trash)", item_id)
            return ""

    def restore(self, item_id: str) -> str:
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._items.get(item_id)
            if entry is None or not entry["trashed"]:
                return R_MISSING
            source, target = self.root / TRASH_FOLDER / item_id, self._folder(item_id)
            try:
                if source.exists() and not target.exists():
                    os.replace(source, target)
            except OSError as exc:
                logger.warning("Docs: %s could not come back (%s)", item_id, type(exc).__name__)
                return R_NOT_SAVED
            entry["trashed"] = ""
            self._touch(entry)
            self._write_index()
            logger.info("Docs: %s restored", item_id)
            return ""

    def deleted(self) -> list[ItemInfo]:
        return self.items(trashed=True)

    def _purge_trash(self) -> None:
        """Items deleted more than 30 days ago go for good (their folder in trash\\ and their index entry)."""
        if self.read_only:
            return
        limit = self._now() - timedelta(days=TRASH_DAYS)
        gone = [item_id for item_id, entry in self._items.items()
                if entry["trashed"] and (_parse_time(entry["trashed"]) or self._now()) <= limit]
        if not gone:
            return
        for item_id in gone:
            try:
                shutil.rmtree(self.root / TRASH_FOLDER / item_id, ignore_errors=True)
            except OSError:
                pass
            self._items.pop(item_id, None)
        self._write_index()
        logger.info("Docs: %d deleted items past 30 days removed", len(gone))

    # ---- documents ----------------------------------------------------------------------------------------

    def _entry(self, item_id: str, kind: str) -> dict[str, Any] | None:
        entry = self._items.get(item_id)
        if entry is None or entry["kind"] != kind or entry["trashed"]:
            return None
        return entry

    def _read_doc_bytes(self, item_id: str) -> bytes | None:
        try:
            raw = _read_bytes(self._folder(item_id) / DOC_FILE, model.MAX_DOC_BYTES)
        except _Unreadable:
            self._mark_bad(item_id, "unreadable")
            return None
        if raw is None:
            return b""
        if len(raw) > model.MAX_DOC_BYTES:
            self._mark_bad(item_id, "too_large")
            return None
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            self._mark_bad(item_id, "not_utf8")
            return None
        self._bad.discard(item_id)
        return raw

    def _mark_bad(self, item_id: str, reason: str) -> None:
        if item_id not in self._bad:
            logger.warning("Docs: %s can't be read safely (%s); read-only for this run", item_id, reason)
        self._bad.add(item_id)

    def _outside_check(self, entry: dict[str, Any], raw: bytes) -> None:
        """doc.md changed since Jarvis wrote it (edited outside Jarvis): a version of what it is now, the new hash."""
        digest = _sha(raw)
        if entry["hash"] and digest != entry["hash"] and not self.read_only:
            self._write_version(entry["id"], raw, ".md", VERSION_OUTSIDE)
            logger.info("Docs: %s was changed outside Jarvis; kept as a version", entry["id"])
        if digest != entry["hash"]:
            entry["hash"] = digest
            text = raw.decode("utf-8")
            entry["words"] = model.word_count(text)
            if not self.read_only:
                self._write_index()

    def read_doc(self, item_id: str) -> DocData | None:
        with self._lock:
            entry = self._entry(item_id, model.KIND_DOC)
            if entry is None:
                return None
            raw = self._read_doc_bytes(item_id)
            if raw is None:
                return DocData(item_id, entry["name"], "", [], True)
            self._outside_check(entry, raw)
            text = model.clean_doc_text(raw.decode("utf-8"))
            return DocData(item_id, entry["name"], text, self._read_pending(item_id),
                           self.read_only or item_id in self._bad)

    def _read_pending(self, item_id: str) -> list[dict[str, Any]]:
        try:
            raw = _read_bytes(self._folder(item_id) / PENDING_FILE, MAX_PENDING_BYTES)
        except _Unreadable:
            return []
        if not raw or len(raw) > MAX_PENDING_BYTES:
            return []
        try:
            data = _load_json(raw)
        except (ValueError, RecursionError, UnicodeDecodeError):
            logger.warning("Docs: %s pending changes can't be read; none shown", item_id)
            return []
        items = data.get("pending") if isinstance(data, dict) else None
        return [item for item in items or [] if isinstance(item, dict)][:model.MAX_PENDING_SECTIONS]

    def save_doc(self, item_id: str, text: str, *, version: str = VERSION_EDIT, burst: bool = True) -> str:
        """The owner's text (or a kept proposal) as doc.md. A version of the text before it is kept first: always
        for a Keep or a Restore (``burst`` False), at most once per 2 minutes while he types."""
        body = model.clean_doc_text(text)
        if len(body) > model.MAX_DOC_CHARS:
            return R_TOO_LONG
        data = body.encode("utf-8")
        if len(data) > model.MAX_DOC_BYTES:
            return R_TOO_LONG
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._entry(item_id, model.KIND_DOC)
            if entry is None:
                return R_MISSING
            old = self._read_doc_bytes(item_id)
            if old is None:
                return R_READ_ONLY
            self._outside_check(entry, old)
            if old == data:
                return ""
            if old and (not burst or self._burst_over(item_id)):
                self._write_version(item_id, old, ".md", version)
            if not _write_atomic(self._folder(item_id) / DOC_FILE, data):
                self.saved = False
                return R_NOT_SAVED
            entry["hash"] = _sha(data)
            entry["words"] = model.word_count(body)
            self._touch(entry)
            self._write_index()
            return ""

    def set_doc_pending(self, item_id: str, pending: Sequence[dict[str, Any]]) -> str:
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._entry(item_id, model.KIND_DOC)
            if entry is None:
                return R_MISSING
            kept = [dict(item) for item in pending][-model.MAX_PENDING_SECTIONS:]
            if not _write_atomic(self._folder(item_id) / PENDING_FILE, _json_bytes({"version": FILE_VERSION,
                                                                                     "pending": kept})):
                return R_NOT_SAVED
            entry["pending"] = len(kept)
            self._write_index()
            return ""

    # ---- sheets ---------------------------------------------------------------------------------------------

    def _read_sheet_raw(self, item_id: str) -> tuple[bytes | None, SheetData | None]:
        entry = self._items[item_id]
        try:
            raw = _read_bytes(self._folder(item_id) / SHEET_FILE, model.MAX_SHEET_BYTES)
        except _Unreadable:
            self._mark_bad(item_id, "unreadable")
            return None, None
        if raw is None:
            self._mark_bad(item_id, "missing")
            return None, None
        if len(raw) > model.MAX_SHEET_BYTES:
            self._mark_bad(item_id, "too_large")
            return None, None
        try:
            data = _load_json(raw)
        except (ValueError, RecursionError, UnicodeDecodeError):
            self._mark_bad(item_id, "damaged")
            return None, None
        sheet = _sheet_from(data, item_id, entry["name"])
        if sheet is None:
            self._mark_bad(item_id, "damaged")
            return None, None
        self._bad.discard(item_id)
        return raw, sheet

    def read_sheet(self, item_id: str) -> SheetData | None:
        with self._lock:
            entry = self._entry(item_id, model.KIND_SHEET)
            if entry is None:
                return None
            raw, sheet = self._read_sheet_raw(item_id)
            if sheet is None:
                return SheetData(item_id, entry["name"], read_only=True)
            sheet.read_only = self.read_only
            return sheet

    def save_sheet(self, item_id: str, sheet: SheetData, *, version: str | None = None) -> str:
        """The sheet as sheet.json; ``version``: a version of what it was first (a Keep, a Restore, an import)."""
        if len(sheet.columns) > model.MAX_COLUMNS or len(sheet.rows) > model.MAX_ROWS:
            return R_TOO_LARGE
        data = _json_bytes(_sheet_data(sheet))
        if len(data) > model.MAX_SHEET_BYTES:
            return R_TOO_LARGE
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            entry = self._entry(item_id, model.KIND_SHEET)
            if entry is None:
                return R_MISSING
            old, current = self._read_sheet_raw(item_id)
            if current is None or old is None:
                return R_READ_ONLY
            if old == data:
                return ""
            outside = bool(entry["hash"]) and _sha(old) != entry["hash"]
            if outside:                       # changed outside Jarvis: what it holds is kept as a version first
                self._write_version(item_id, old, ".json", VERSION_OUTSIDE)
                logger.info("Docs: %s was changed outside Jarvis; kept as a version", item_id)
            elif version is not None or self._burst_over(item_id):
                self._write_version(item_id, old, ".json", version or VERSION_EDIT)
            if not _write_atomic(self._folder(item_id) / SHEET_FILE, data):
                self.saved = False
                return R_NOT_SAVED
            entry["hash"] = _sha(data)
            entry["rows"], entry["columns"] = len(sheet.rows), len(sheet.columns)
            entry["pending"] = len(sheet.pending)
            self._touch(entry)
            self._write_index()
            return ""

    # ---- versions -----------------------------------------------------------------------------------------------

    def _versions_dir(self, item_id: str) -> Path:
        return self._folder(item_id) / VERSIONS_FOLDER

    def _burst_over(self, item_id: str) -> bool:
        latest = self._version_list(item_id)
        if not latest:
            return True
        return (self._now() - latest[0].when).total_seconds() >= BURST_SECONDS

    def _version_list(self, item_id: str) -> list[VersionInfo]:
        folder = self._versions_dir(item_id)
        try:
            paths = list(folder.iterdir())
        except OSError:
            return []
        now = self._now()
        found = [info for info in (_version_from(path, now) for path in paths) if info is not None]
        found.sort(key=lambda info: info.name, reverse=True)
        return found

    def _write_version(self, item_id: str, data: bytes, suffix: str, reason: str) -> None:
        folder = self._versions_dir(item_id)
        moment = self._now()
        name = f"{_stamp(moment)}-{reason}{suffix}"
        while (folder / name).exists():
            moment += timedelta(microseconds=1)
            name = f"{_stamp(moment)}-{reason}{suffix}"
        if not _write_atomic(folder / name, data):
            return
        kept, total = 0, 0
        for info in self._version_list(item_id):
            kept += 1
            total += info.size
            if kept > KEEP_VERSIONS or (kept > 1 and total > KEEP_VERSION_BYTES):
                try:
                    os.unlink(folder / info.name)
                except OSError:
                    pass

    def versions(self, item_id: str) -> list[VersionInfo]:
        with self._lock:
            if item_id not in self._items:
                return []
            return self._version_list(item_id)

    def read_version(self, item_id: str, name: str) -> str | SheetData | None:
        """A version's text (a document) or its sheet; None when it is gone or can't be read."""
        with self._lock:
            entry = self._items.get(item_id)
            if entry is None or name not in {info.name for info in self._version_list(item_id)}:
                return None
            try:
                raw = _read_bytes(self._versions_dir(item_id) / name, max(model.MAX_DOC_BYTES, model.MAX_SHEET_BYTES))
            except _Unreadable:
                return None
            if raw is None:
                return None
            try:
                if entry["kind"] == model.KIND_DOC:
                    return model.clean_doc_text(raw.decode("utf-8"))
                return _sheet_from(_load_json(raw), item_id, entry["name"])
            except (ValueError, RecursionError, UnicodeDecodeError):
                return None

    def restore_version(self, item_id: str, name: str) -> str:
        """The version comes back; what was there becomes a version first (so a Restore can be restored)."""
        old = self.read_version(item_id, name)
        if old is None:
            return R_NOT_FOUND
        if isinstance(old, str):
            return self.save_doc(item_id, old, version=VERSION_RESTORE, burst=False)
        current = self.read_sheet(item_id)
        if current is None or current.read_only:
            return R_READ_ONLY
        old.pending = current.pending          # what waits stays waiting
        return self.save_sheet(item_id, old, version=VERSION_RESTORE)

    # ---- standing runs taken in ------------------------------------------------------------------------------

    def imported(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._imported)

    def mark_imported(self, run_ids: Iterable[str]) -> str:
        with self._lock:
            if not self._refresh():
                return R_READ_ONLY
            known = set(self._imported)
            for run in run_ids:
                if isinstance(run, str) and run and run not in known:
                    known.add(run)
                    self._imported.append(run)
            self._imported = self._imported[-model.MAX_IMPORTED:]
            return "" if self._write_index() else R_NOT_SAVED

    # ---- export ------------------------------------------------------------------------------------------------

    def export_md(self, item_id: str) -> bytes | None:
        """The kept text, UTF-8 (what waits for Keep is not in it)."""
        doc = self.read_doc(item_id)
        if doc is None or item_id in self._bad:
            return None                       # a file that can't be read safely: nothing to export
        return doc.text.encode("utf-8")

    def export_csv(self, item_id: str) -> bytes | None:
        """The kept rows (never the pending ones) as UTF-8 with a BOM, each cell by its type, formulas guarded."""
        sheet = self.read_sheet(item_id)
        if sheet is None or item_id in self._bad:
            return None                       # a file that can't be read safely: nothing to export
        return sheet_csv(sheet)


def _csv_cell(column: model.Column, value: Any) -> str:
    """A cell in the .csv: guarded, except a number (a checked number can't be a formula: "-3" stays a number)."""
    text = model.csv_value(column, value)
    return text if column.type == model.TYPE_NUMBER and value is not None else guard_cell(text)


def sheet_csv(sheet: SheetData) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([guard_cell(column.name) for column in sheet.columns])
    for row in sheet.rows:
        writer.writerow([_csv_cell(column, row.cells.get(column.id)) for column in sheet.columns])
    return out.getvalue().encode("utf-8-sig")


# ---- read-only looks (the standing setup and the runner: never a write here) ----------------------------------------

def _read_index(data_dir: Path) -> list[dict[str, Any]]:
    try:
        raw = _read_bytes(Path(data_dir) / DOCS_FOLDER / INDEX_FILE, MAX_INDEX_BYTES)
        if not raw or len(raw) > MAX_INDEX_BYTES:
            return []
        data = _load_json(raw)
    except (_Unreadable, ValueError, RecursionError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION or not isinstance(data.get("items"), list):
        return []
    return [entry for entry in (_entry_from(item) for item in data["items"]) if entry is not None]


def find_by_name(data_dir: Path, name: str, kind: str = "") -> ItemInfo | None:
    """An item (not deleted) whose name folds to ``name``'s, read without writing anything."""
    key = model.fold(name)
    if not key:
        return None
    for entry in _read_index(data_dir):
        if not entry["trashed"] and model.fold(entry["name"]) == key and (not kind or entry["kind"] == kind):
            return ItemInfo(entry["id"], entry["kind"], entry["name"], entry["created"], entry["changed"],
                            entry["words"], entry["rows"], entry["columns"], entry["pending"], "", True)
    return None


def read_columns(data_dir: Path, sheet_id: str) -> tuple[list[model.Column], list[str]] | None:
    """A sheet's columns and key columns only (never a row), read without writing anything; None when it can't be
    read."""
    if model.kind_of(sheet_id) != model.KIND_SHEET:
        return None
    try:
        raw = _read_bytes(Path(data_dir) / DOCS_FOLDER / sheet_id / SHEET_FILE, model.MAX_SHEET_BYTES)
        if not raw or len(raw) > model.MAX_SHEET_BYTES:
            return None
        sheet = _sheet_from(_load_json(raw), sheet_id, "")
    except (_Unreadable, ValueError, RecursionError, UnicodeDecodeError):
        return None
    if sheet is None:
        return None
    return list(sheet.columns), list(sheet.keys)


def listed(data_dir: Path) -> list[ItemInfo]:
    """The documents and sheets not deleted, newest change first, read without writing anything (a damaged, newer or
    too-large index lists none)."""
    entries = [entry for entry in _read_index(data_dir) if not entry["trashed"]]
    entries.sort(key=lambda entry: entry["changed"], reverse=True)
    return [ItemInfo(entry["id"], entry["kind"], entry["name"], entry["created"], entry["changed"], entry["words"],
                     entry["rows"], entry["columns"], entry["pending"], "", True) for entry in entries]


def _listed_entry(data_dir: Path, item_id: str, kind: str) -> dict[str, Any] | None:
    if model.kind_of(item_id) != kind:
        return None
    for entry in _read_index(data_dir):
        if entry["id"] == item_id and not entry["trashed"]:
            return entry
    return None


def read_doc_text(data_dir: Path, doc_id: str) -> DocData | None:
    """A listed document's kept text (doc.md; never its pending changes), read without writing anything - no version
    is made for a change made outside Jarvis. None when it is not listed, is deleted or can't be read."""
    entry = _listed_entry(data_dir, doc_id, model.KIND_DOC)
    if entry is None:
        return None
    try:
        raw = _read_bytes(Path(data_dir) / DOCS_FOLDER / doc_id / DOC_FILE, model.MAX_DOC_BYTES)
    except _Unreadable:
        return None
    if raw is None:
        return DocData(doc_id, entry["name"], "", [], True)
    if len(raw) > model.MAX_DOC_BYTES:
        return None
    try:
        text = model.clean_doc_text(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None
    return DocData(doc_id, entry["name"], text, [], True)


def read_sheet_rows(data_dir: Path, sheet_id: str) -> SheetData | None:
    """A listed sheet's kept columns and rows (never what waits: ``pending`` is empty), read without writing anything.
    None when it is not listed, is deleted or can't be read."""
    entry = _listed_entry(data_dir, sheet_id, model.KIND_SHEET)
    if entry is None:
        return None
    try:
        raw = _read_bytes(Path(data_dir) / DOCS_FOLDER / sheet_id / SHEET_FILE, model.MAX_SHEET_BYTES)
        if not raw or len(raw) > model.MAX_SHEET_BYTES:
            return None
        sheet = _sheet_from(_load_json(raw), sheet_id, entry["name"])
    except (_Unreadable, ValueError, RecursionError, UnicodeDecodeError):
        return None
    if sheet is None:
        return None
    sheet.pending, sheet.read_only = [], True
    return sheet


__all__ = ["DocsStore", "ItemInfo", "DocData", "SheetData", "Row", "VersionInfo", "write_export", "synced_folder",
           "guard_cell", "find_by_name", "read_columns", "sheet_csv", "default_export_folder", "listed",
           "read_doc_text", "read_sheet_rows"]
