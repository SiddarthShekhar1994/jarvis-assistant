"""What Jarvis proposes in a document or a sheet: pending changes that wait for Keep (no Qt, no I/O, no logs).

    doc_add(text, after="", source=, run=, made=) / doc_replace(heading, text, ...)   a document's pending change
    sheet_columns(columns, keys=()) / sheet_row(cells, ...) / sheet_cell(row_id, column_id, value, was, ...)
                                                     a sheet's pending change (cells by column id, typed already)
    add_pending(pending, new, cap) -> (pending, let_go)   the new ones after the old; past the cap the oldest go
    apply_doc(item, text) -> (text, reason)          one change spliced in; REASON_STALE when its heading has gone
    at_the_end(item)                                 the same change as an "add" at the end ("Add at the end")
    apply_sheet(item, sheet) -> reason               one change applied to a SheetData (a copy the caller saves)
    diff(before, after) -> [(kind, text)]            word by word: SAME, REMOVED, ADDED (the tile's before/after)
    doc_before(item, text)                           what the change replaces now ("" for an add)
    describe(item, sheet=None) -> str                the tile's head words ("ADD AFTER PLAN", "1 ROW")
    pending_rows(sheet)                              (pending id, the row's words) for the sheet's band

Jarvis's rule for agents: they only append (a section, or rows); a row whose key columns match a kept row becomes
pending cell changes (``merge_rows``). Agents "ask first" by the same Keep: nothing they write is kept without it.
A pending change names what it touches by heading (a document) or by row id and column id (a sheet): every other byte
of the document and every other cell stays as it was.
"""

from __future__ import annotations

import difflib
import re
import secrets
from collections.abc import Sequence
from typing import Any

from . import model
from .store import Row, SheetData

OP_ADD, OP_REPLACE = "add", "replace"
KIND_COLUMNS, KIND_ROW, KIND_CELL = "columns", "row", "cell"
FROM_ASK = "ask"
REASON_STALE = "stale"
REASON_GONE = "gone"
REASON_BAD = "bad"
SAME, REMOVED, ADDED = "same", "removed", "added"
_TOKEN_RE = re.compile(r"\s+|[^\s]+")


def new_pending_id() -> str:
    return f"p-{secrets.token_hex(4)}"


def _base(source: str, run: str, made: str) -> dict[str, Any]:
    return {"id": new_pending_id(), "from": source or FROM_ASK, "run": run, "made": made}


def doc_add(text: str, after: str = "", *, source: str = FROM_ASK, run: str = "", made: str = "") -> dict[str, Any]:
    return {**_base(source, run, made), "op": OP_ADD, "heading": "", "after": after, "text": text}


def doc_replace(heading: str, text: str, *, source: str = FROM_ASK, run: str = "", made: str = "",
                base: str = "") -> dict[str, Any]:
    """``base``: the hash of the section as the run read it (section_hash): a section changed since is marked."""
    item = {**_base(source, run, made), "op": OP_REPLACE, "heading": heading, "after": "", "text": text}
    if base:
        item["base"] = base
    return item


def section_hash(text: str, heading: str) -> str:
    """The hash of ``heading``'s section in ``text`` ("" when it is not there once)."""
    import hashlib

    found = _one(text, heading)
    if found is None:
        return ""
    return hashlib.sha256(text[found.start:found.end].encode("utf-8")).hexdigest()


def changed_since(item: dict[str, Any], current: Any) -> bool:
    """The change was written for something that has changed since Jarvis read it: a replace whose section is not
    what the run read (``current``: the document's text), a cell whose value is no longer the one it saw
    (``current``: the SheetData). Such a change is kept only by its own Keep, never by Keep all."""
    if item.get("op") == OP_REPLACE and isinstance(current, str):
        base = str(item.get("base") or "")
        now = section_hash(current, str(item.get("heading") or ""))
        return bool(base and now and now != base)
    if item.get("kind") == KIND_CELL and isinstance(current, SheetData):
        row = current.row(str(item.get("row") or ""))
        column = current.column(str(item.get("column") or ""))
        if row is None or column is None:
            return False
        return model.stored_value(column, row.cells.get(column.id)) != model.stored_value(column, item.get("was"))
    return False


def sheet_columns(columns: Sequence[model.Column], keys: Sequence[str] = (), *, source: str = FROM_ASK,
                  run: str = "", made: str = "") -> dict[str, Any]:
    return {**_base(source, run, made), "kind": KIND_COLUMNS, "columns": [model.column_data(c) for c in columns],
            "keys": list(keys)}


def sheet_row(cells: dict[str, Any], *, bad: int = 0, source: str = FROM_ASK, run: str = "",
              made: str = "") -> dict[str, Any]:
    return {**_base(source, run, made), "kind": KIND_ROW, "cells": dict(cells), "bad": bad}


def sheet_cell(row_id: str, column_id: str, value: Any, was: Any = None, *, source: str = FROM_ASK, run: str = "",
               made: str = "") -> dict[str, Any]:
    return {**_base(source, run, made), "kind": KIND_CELL, "row": row_id, "column": column_id, "value": value,
            "was": was}


def add_pending(pending: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]],
                cap: int) -> tuple[list[dict[str, Any]], int]:
    """``new`` after ``pending``; past ``cap`` the oldest are let go (how many, for the count shown)."""
    joined = [dict(item) for item in pending] + [dict(item) for item in new]
    let_go = max(0, len(joined) - cap)
    return joined[let_go:], let_go


# ---- documents ----------------------------------------------------------------------------------------------------

def _one(text: str, name: str) -> model.Heading | None:
    found = model.find_headings(text, name)
    return found[0] if len(found) == 1 else None


def apply_doc(item: dict[str, Any], text: str) -> tuple[str, str]:
    """The document's text with ``item`` spliced in, and "" - or (``text``, REASON_STALE) when the heading it names is
    gone (or is there twice), (``text``, REASON_BAD) when it is not a change."""
    op, body = item.get("op"), item.get("text")
    if not isinstance(body, str):
        return text, REASON_BAD
    if op == OP_ADD:
        after = str(item.get("after") or "")
        if not after:
            return model.add_section(text, body), ""
        heading = _one(text, after)
        if heading is None:
            return text, REASON_STALE
        return model.add_section(text, body, heading), ""
    if op == OP_REPLACE:
        heading = _one(text, str(item.get("heading") or ""))
        if heading is None:
            return text, REASON_STALE
        return model.replace_section(text, heading, body), ""
    return text, REASON_BAD


def at_the_end(item: dict[str, Any]) -> dict[str, Any]:
    """"Add at the end": the change's text as a new section at the end (a replace whose section has gone too)."""
    return {**item, "op": OP_ADD, "heading": "", "after": ""}


def doc_before(item: dict[str, Any], text: str) -> str:
    """The section the change replaces as it is now ("" for an add or when it is gone)."""
    if item.get("op") != OP_REPLACE:
        return ""
    heading = _one(text, str(item.get("heading") or ""))
    return text[heading.start:heading.end] if heading is not None else ""


def diff(before: str, after: str) -> list[tuple[str, str]]:
    """Word by word (spaces kept): what stays, what goes, what comes."""
    a, b = _TOKEN_RE.findall(before or ""), _TOKEN_RE.findall(after or "")
    out: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.append((SAME, "".join(a[i1:i2])))
            continue
        if tag in ("replace", "delete"):
            out.append((REMOVED, "".join(a[i1:i2])))
        if tag in ("replace", "insert"):
            out.append((ADDED, "".join(b[j1:j2])))
    merged: list[tuple[str, str]] = []
    for kind, piece in out:
        if not piece:
            continue
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + piece)
        else:
            merged.append((kind, piece))
    return merged


# ---- sheets -------------------------------------------------------------------------------------------------------

def _columns_of(item: dict[str, Any]) -> list[model.Column]:
    return [column for column in (model.column_from(raw) for raw in item.get("columns") or []) if column is not None]


def apply_sheet(item: dict[str, Any], sheet: SheetData, *, row_id: str = "") -> str:
    """``item`` applied to ``sheet`` (a copy; the caller saves it): "" or a reason word. A row's cells name column
    ids the sheet has (others are left out); a cell change needs its row and column still there."""
    kind = item.get("kind")
    if kind == KIND_COLUMNS:
        names = {model.fold(column.name) for column in sheet.columns}
        for column in _columns_of(item):
            if model.fold(column.name) not in names and len(sheet.columns) < model.MAX_COLUMNS:
                sheet.columns.append(column)
                names.add(model.fold(column.name))
        ids = {column.id for column in sheet.columns}
        if not sheet.keys:
            sheet.keys = [key for key in item.get("keys") or [] if key in ids][:model.MAX_KEY_COLUMNS]
        return ""
    if kind == KIND_ROW:
        if len(sheet.rows) >= model.MAX_ROWS:
            return model.REASON_TOO_MANY
        by_id = {column.id: column for column in sheet.columns}
        cells: dict[str, Any] = {}
        for column_id, value in (item.get("cells") or {}).items():
            column = by_id.get(column_id)
            if column is not None:
                checked = model.stored_value(column, value)
                if checked is not None:
                    cells[column_id] = checked
        sheet.rows.append(Row(row_id or model.new_row_id(), cells, str(item.get("from") or FROM_ASK)[:40],
                              str(item.get("made") or "")[:40]))
        return ""
    if kind == KIND_CELL:
        row = sheet.row(str(item.get("row") or ""))
        column = sheet.column(str(item.get("column") or ""))
        if row is None or column is None:
            return REASON_STALE
        value = model.stored_value(column, item.get("value"))
        if value is None:
            row.cells.pop(column.id, None)
        else:
            row.cells[column.id] = value
        return ""
    return REASON_BAD


def needs_columns(sheet: SheetData) -> dict[str, Any] | None:
    """The pending column set a new sheet's rows wait on (kept first by any Keep), or None."""
    return next((item for item in sheet.pending if item.get("kind") == KIND_COLUMNS), None)


def row_words(item: dict[str, Any], sheet: SheetData) -> str:
    """"Acme · Intern · 2026-12-01" (a pending row) / "Row 3 · Status: Applied (was Open)" (a pending cell)."""
    columns = list(sheet.columns) + [c for c in _columns_of(needs_columns(sheet) or {}) if c not in sheet.columns]
    by_id = {column.id: column for column in columns}
    if item.get("kind") == KIND_ROW:
        cells = item.get("cells") or {}
        parts = [model.show_value(by_id[cid], cells[cid]) for cid in by_id if cid in cells]
        return " · ".join(part for part in parts if part) or "An empty row"
    if item.get("kind") == KIND_CELL:
        column = by_id.get(str(item.get("column") or ""))
        index = next((i for i, row in enumerate(sheet.rows) if row.id == item.get("row")), -1)
        if column is None or index < 0:
            return "A cell that has gone"
        new, was = model.show_value(column, item.get("value")), model.show_value(column, item.get("was"))
        tail = f" (was {was})" if was else ""
        return f"Row {index + 1} · {column.name}: {new or 'empty'}{tail}"
    if item.get("kind") == KIND_COLUMNS:
        return "Columns: " + " · ".join(c.name for c in _columns_of(item))
    return ""


def pending_rows(sheet: SheetData) -> list[tuple[str, str]]:
    return [(str(item.get("id")), row_words(item, sheet)) for item in sheet.pending]


def describe(item: dict[str, Any]) -> str:
    """The head of a document change's tile."""
    if item.get("op") == OP_ADD:
        after = str(item.get("after") or "")
        return f"ADD AFTER “{after}”" if after else "ADD AT THE END"
    if item.get("op") == OP_REPLACE:
        heading = str(item.get("heading") or "")
        return f"REMOVE “{heading}”" if not str(item.get("text") or "").strip() else \
            f"CHANGE “{heading}”"
    return ""


def merge_rows(sheet: SheetData, rows: Sequence[dict[str, Any]], *, source: str, run: str,
               made: str) -> list[dict[str, Any]]:
    """An agent's rows as pending changes: a row whose key columns (all filled) match a kept row's becomes pending
    cell changes for the cells that differ (none: nothing); every other row is a pending new row. ``rows`` hold typed
    cells by column id."""
    keys = [key for key in sheet.keys if sheet.column(key) is not None]
    out: list[dict[str, Any]] = []
    for cells in rows:
        match = None
        if keys and all(cells.get(key) is not None for key in keys):
            wanted = tuple(model.fold(str(cells[key])) for key in keys)
            match = next((row for row in sheet.rows
                          if tuple(model.fold(str(row.cells.get(key, ""))) for key in keys) == wanted), None)
            if match is None:   # a duplicate of a row that already waits is dropped too
                waiting = [item for item in sheet.pending + out if item.get("kind") == KIND_ROW]
                if any(tuple(model.fold(str((item.get("cells") or {}).get(key, ""))) for key in keys) == wanted
                       for item in waiting):
                    continue
        if match is None:
            out.append(sheet_row(cells, source=source, run=run, made=made))
            continue
        for column_id, value in cells.items():
            if column_id in keys or value is None or match.cells.get(column_id) == value:
                continue
            out.append(sheet_cell(match.id, column_id, value, match.cells.get(column_id), source=source, run=run,
                                  made=made))
    return out
