"""A document's or sheet's run (build plan item 5, part 2): its answer read strictly and checked by Jarvis.

    read_doc_result(event, init)   the run's result event -> DocAnswer or AskFailure (exactly say / question / doc /
                                   sheet within the schema's caps; a plain-text JSON answer only when the run had no
                                   StructuredOutput tool; anything else GARBLED)
    check_doc_answer(answer, job, words, seen="", made="") -> (DocProposal | None, reason)
                                   Jarvis's own check: the changes as pending changes (docs.proposals), or why not -
                                   the wrong kind, over the caps, a heading, row or column that is not there, a value
                                   of the wrong type, columns for a sheet that has them, an email address or a link
                                   that is not in what the run was given (ask.revise's check), a control or invisible
                                   character

Nothing is kept here: every change waits in the document or sheet until the owner's Keep (docs_ui). Nothing is
logged; the reprs hold counts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..docs import model, proposals
from .context import data
from .revise import bad_character, new_link_or_address
from .stream import GARBLED, AskFailure, InitSummary, failure, result_failure

MAX_SAY = 200
MAX_QUESTION = 200
MAX_TITLE = 60
MAX_CHANGES = 6
MAX_CHANGE_CHARS = 8_000
MAX_ALL_CHARS = 16_000
MAX_NEW_COLUMNS = 12
MAX_ADD_ROWS = 50
MAX_SET_CELLS = 50

DOC_EMPTY = "empty"
DOC_WRONG_KIND = "wrong_kind"
DOC_TOO_LONG = "too_long"
DOC_TOO_MANY = "too_many"
DOC_UNKNOWN_HEADING = "unknown_heading"
DOC_UNKNOWN_ROW = "unknown_row"
DOC_UNKNOWN_COLUMN = "unknown_column"
DOC_WRONG_TYPE = "wrong_type"
DOC_COLUMNS = "columns"
DOC_NEW_LINK = "new_link"
DOC_NEW_ADDRESS = "new_address"
DOC_CONTROL = "control"
DOC_REASONS = (DOC_EMPTY, DOC_WRONG_KIND, DOC_TOO_LONG, DOC_TOO_MANY, DOC_UNKNOWN_HEADING, DOC_UNKNOWN_ROW,
               DOC_UNKNOWN_COLUMN, DOC_WRONG_TYPE, DOC_COLUMNS, DOC_NEW_LINK, DOC_NEW_ADDRESS, DOC_CONTROL)
# What a held-back answer says (Jarvis's own words; never the model's).
REASON_TEXT = {DOC_EMPTY: "Jarvis had nothing to change there.",
               DOC_WRONG_KIND: "Jarvis's answer was for the wrong kind of item, so nothing waits.",
               DOC_TOO_LONG: "Jarvis's changes were longer than he may write at once, so nothing waits.",
               DOC_TOO_MANY: "Jarvis's answer had more changes than he may make at once, so nothing waits.",
               DOC_UNKNOWN_HEADING: "Jarvis named a heading that isn't in the document, so nothing waits.",
               DOC_UNKNOWN_ROW: "Jarvis named a row that isn't in the sheet, so nothing waits.",
               DOC_UNKNOWN_COLUMN: "Jarvis named a column that isn't in the sheet, so nothing waits.",
               DOC_WRONG_TYPE: "A value didn't fit its column's type, so nothing waits.",
               DOC_COLUMNS: "Jarvis tried to change the sheet's columns, which he never does, so nothing waits.",
               DOC_NEW_LINK: "Jarvis's text had a link that wasn't in what he was given, so nothing waits.",
               DOC_NEW_ADDRESS: "Jarvis's text had an email address that wasn't in what he was given, so nothing waits.",
               DOC_CONTROL: "Jarvis's text had characters that don't show, so nothing waits."}

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
_KEYS = {"say", "question", "doc", "sheet"}
_DOC_KEYS = {"title", "changes"}
_CHANGE_KEYS = {"op", "heading", "after", "text"}
_SHEET_KEYS = {"title", "columns", "add_rows", "set_cells"}
_COLUMN_KEYS = {"name", "type", "choices"}
_CELL_KEYS = {"row", "column", "value"}


@dataclass(frozen=True)
class DocAnswer:
    say: str = field(repr=False)
    question: str = field(repr=False)
    doc: dict[str, Any] | None = field(default=None, repr=False)
    sheet: dict[str, Any] | None = field(default=None, repr=False)
    via_fallback: bool = False

    def __repr__(self) -> str:
        changes = len(self.doc["changes"]) if self.doc else 0
        rows = len(self.sheet["add_rows"]) if self.sheet else 0
        return f"DocAnswer({changes} change(s), {rows} row(s){', question' if self.question else ''})"


@dataclass(frozen=True)
class DocProposal:
    """What waits for Keep: the pending changes (docs.proposals dicts), a title for a new item ("" none)."""

    kind: str
    items: tuple[dict[str, Any], ...] = field(repr=False)
    title: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"DocProposal({self.kind}, {len(self.items)} change(s))"


def _str(value: Any, cap: int) -> bool:
    return isinstance(value, str) and len(value) <= cap


def _doc_from(value: Any) -> dict[str, Any] | None | bool:
    """The "doc" object, None, or False when it does not fit."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _DOC_KEYS or not _str(value["title"], MAX_TITLE):
        return False
    changes = value["changes"]
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        return False
    for change in changes:
        if not isinstance(change, dict) or set(change) != _CHANGE_KEYS or change["op"] not in ("add", "replace"):
            return False
        if not (_str(change["heading"], 200) and _str(change["after"], 200) and _str(change["text"], MAX_CHANGE_CHARS)):
            return False
    return {"title": value["title"], "changes": [dict(change) for change in changes]}


def _sheet_from(value: Any) -> dict[str, Any] | None | bool:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _SHEET_KEYS or not _str(value["title"], MAX_TITLE):
        return False
    columns, rows, cells = value["columns"], value["add_rows"], value["set_cells"]
    if not isinstance(columns, list) or len(columns) > MAX_NEW_COLUMNS:
        return False
    for column in columns:
        if not isinstance(column, dict) or set(column) != _COLUMN_KEYS or not _str(column["name"], 40):
            return False
        if column["type"] not in model.TYPES or not isinstance(column["choices"], list):
            return False
        if len(column["choices"]) > model.MAX_CHOICES or not all(_str(c, 40) for c in column["choices"]):
            return False
    if not isinstance(rows, list) or len(rows) > MAX_ADD_ROWS:
        return False
    for row in rows:
        if not isinstance(row, list) or len(row) > model.MAX_COLUMNS or not all(_str(c, 500) for c in row):
            return False
    if not isinstance(cells, list) or len(cells) > MAX_SET_CELLS:
        return False
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != _CELL_KEYS:
            return False
        if not (_str(cell["row"], 12) and _str(cell["column"], 40) and _str(cell["value"], 500)):
            return False
    return {"title": value["title"], "columns": columns, "add_rows": rows, "set_cells": cells}


def answer_from(value: Any, *, fallback: bool = False) -> DocAnswer | None:
    """The run's JSON -> DocAnswer, or None when anything does not fit the schema."""
    if not isinstance(value, dict) or set(value) != _KEYS:
        return None
    say, question = value["say"], value["question"]
    if not _str(say, MAX_SAY) or not _str(question, MAX_QUESTION):
        return None
    doc, sheet = _doc_from(value["doc"]), _sheet_from(value["sheet"])
    if doc is False or sheet is False:
        return None
    return DocAnswer(" ".join(say.split()), " ".join(question.split()), doc or None, sheet or None, fallback)


def read_doc_result(event: Mapping[str, Any], init: InitSummary | None) -> DocAnswer | AskFailure:
    """The result event of a document's run -> DocAnswer, or AskFailure (stream.result_failure first)."""
    found = result_failure(event, init)
    if found is not None:
        return found
    assert init is not None
    value = event.get("structured_output")
    if value is not None:
        answer = answer_from(value)
        return answer if answer is not None else failure(GARBLED, "shape")
    if init.structured_tool:
        return failure(GARBLED, "no_structured_output")
    text = event.get("result") if isinstance(event.get("result"), str) else ""
    fenced = _FENCE_RE.match(text or "")
    try:
        parsed = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return failure(GARBLED, "fallback")
    answer = answer_from(parsed, fallback=True)
    return answer if answer is not None else failure(GARBLED, "fallback")


# ---- Jarvis's own check ------------------------------------------------------------------------------------------

def _shown(text: str) -> str:
    return model.fold(data(text, 400))


def _heading_title(snapshot: str, name: str) -> tuple[str, str]:
    """The heading's own title in ``snapshot`` for the name the run gave (as it saw it: through data()), and "" or a
    reason (none, or more than one)."""
    wanted = _shown(name)
    found = [heading for heading in model.headings(snapshot) if _shown(heading.title) == wanted]
    if len(found) != 1:
        return "", DOC_UNKNOWN_HEADING
    return found[0].title, ""


def _text_reason(text: str, seen: str) -> str:
    if bad_character(text):
        return DOC_CONTROL
    found = new_link_or_address(text, seen)
    return {"new_address": DOC_NEW_ADDRESS, "new_link": DOC_NEW_LINK}.get(found, "")


def _check_doc(doc: dict[str, Any], job: Any, seen: str, made: str) -> tuple[list[dict[str, Any]], str]:
    snapshot = str(job.snapshot or "")
    total = 0
    items: list[dict[str, Any]] = []
    for change in doc["changes"]:
        text = model.clean_doc_text(change["text"])
        total += len(text)
        if total > MAX_ALL_CHARS:
            return [], DOC_TOO_LONG
        reason = _text_reason(change["text"], seen)
        if reason:
            return [], reason
        if change["op"] == "replace":
            title, reason = _heading_title(snapshot, change["heading"])
            if reason:
                return [], reason
            items.append(proposals.doc_replace(title, text, source=proposals.FROM_ASK, made=made,
                                               base=proposals.section_hash(snapshot, title)))
            continue
        if not text.strip():
            continue
        after = ""
        if change["after"].strip():
            after, reason = _heading_title(snapshot, change["after"])
            if reason:
                return [], reason
        items.append(proposals.doc_add(text, after, source=proposals.FROM_ASK, made=made))
    return items, ""


def _check_sheet(sheet: dict[str, Any], job: Any, seen: str, made: str) -> tuple[list[dict[str, Any]], str]:
    snapshot = job.snapshot
    columns = list(getattr(snapshot, "columns", ()) or ())
    items: list[dict[str, Any]] = []
    if sheet["columns"]:
        if columns or not job.new:
            return [], DOC_COLUMNS
        made_columns: list[model.Column] = []
        for raw in sheet["columns"]:
            column, reason = model.check_column(raw["name"], raw["type"], raw["choices"],
                                                taken=tuple(c.name for c in made_columns))
            if column is None:
                return [], DOC_COLUMNS
            made_columns.append(column)
        columns = made_columns
        items.append(proposals.sheet_columns(columns, source=proposals.FROM_ASK, made=made))
    if len(sheet["add_rows"]) + len(snapshot.rows if snapshot is not None else ()) > model.MAX_ROWS:
        return [], DOC_TOO_MANY
    for raw_row in sheet["add_rows"]:
        if len(raw_row) > len(columns):
            return [], DOC_UNKNOWN_COLUMN
        cells: dict[str, Any] = {}
        for column, raw in zip(columns, raw_row):
            if not raw.strip():
                continue                      # an empty cell: never a checkbox's "no"
            reason = _text_reason(raw, seen)
            if reason:
                return [], reason
            value, reason = model.parse_value(column, raw)
            if reason:
                return [], DOC_WRONG_TYPE
            if value is not None:
                cells[column.id] = value
        if cells:
            items.append(proposals.sheet_row(cells, source=proposals.FROM_ASK, made=made))
    by_name = {model.fold(column.name): column for column in columns}
    shown_name = {_shown(column.name): column for column in columns}
    rows = {row.id: row for row in (getattr(snapshot, "rows", ()) or ())}
    for cell in sheet["set_cells"]:
        row = rows.get(cell["row"].strip())
        if row is None:
            return [], DOC_UNKNOWN_ROW
        column = by_name.get(model.fold(cell["column"])) or shown_name.get(_shown(cell["column"]))
        if column is None:
            return [], DOC_UNKNOWN_COLUMN
        if cell["value"].strip():
            reason = _text_reason(cell["value"], seen)
            if reason:
                return [], reason
        value, reason = model.parse_value(column, cell["value"])
        if reason:
            return [], DOC_WRONG_TYPE
        if row.cells.get(column.id) == value:
            continue
        items.append(proposals.sheet_cell(row.id, column.id, value, row.cells.get(column.id),
                                          source=proposals.FROM_ASK, made=made))
    return items, ""


def check_doc_answer(answer: DocAnswer, job: Any, words: str, seen: str = "",
                     made: str = "") -> tuple[DocProposal | None, str]:
    """The answer's changes as a DocProposal, or (None, DOC_*). ``seen``: everything the run was given besides the
    item (the tables, the conversation, the notes): a link or an address there or in the item or the words is
    known."""
    kind = getattr(job, "kind", "")
    if kind == model.KIND_DOC:
        known = f"{job.snapshot or ''}\n{words or ''}\n{seen or ''}"
        if answer.sheet is not None:
            return None, DOC_WRONG_KIND
        if answer.doc is None:
            return None, DOC_EMPTY
        title_reason = _text_reason(answer.doc["title"], known) if answer.doc["title"] else ""
        if title_reason:
            return None, title_reason
        items, reason = _check_doc(answer.doc, job, known, made)
        title = model.clean_name(answer.doc["title"])
    elif kind == model.KIND_SHEET:
        cells = [model.show_value(c, row.cells.get(c.id)) for row in getattr(job.snapshot, "rows", ())
                 for c in getattr(job.snapshot, "columns", ())]
        known = "\n".join([*cells, words or "", seen or ""])
        if answer.doc is not None:
            return None, DOC_WRONG_KIND
        if answer.sheet is None:
            return None, DOC_EMPTY
        title_reason = _text_reason(answer.sheet["title"], known) if answer.sheet["title"] else ""
        if title_reason:
            return None, title_reason
        items, reason = _check_sheet(answer.sheet, job, known, made)
        title = model.clean_name(answer.sheet["title"])
    else:
        return None, DOC_WRONG_KIND
    if reason:
        return None, reason
    if not items:
        return None, DOC_EMPTY
    return DocProposal(kind, tuple(items), title), ""
