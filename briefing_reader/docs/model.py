"""Documents and datasheets: ids, typed values, markdown sections and the caps (no Qt, no I/O, no logs).

    new_id(kind) / kind_of(item_id) / valid_id(item_id)      "d-<12 hex>" a document, "s-<12 hex>" a sheet
    new_column_id() / new_row_id()                            "c-<6 hex>", "r-<8 hex>"
    Column(id, name, type, choices)                           a sheet's column; column_from(data) / column_data(col)
    check_column(name, type, choices) -> (Column | None, reason)
    parse_value(column, raw) -> (value, reason)               a typed cell from what was typed or proposed: None is an
                                                              empty cell; reason "" or a REASON_* word (value None)
    show_value(column, value) / csv_value(column, value)      the cell as the table shows it / as a .csv holds it
    clean_line(text, cap) / clean_name(text, cap)             one line (controls and format characters gone)
    clean_doc_text(text)                                      the owner's markdown: LF line ends, no controls but tab
    headings(text) -> [Heading]                               the ATX headings (# .. ######) outside fenced code, each
                                                              with its section's end (the next heading of the same or
                                                              a higher level, or the end)
    find_headings(text, name) -> [Heading]                    the headings whose title folds to ``name``'s
    add_section(text, new, after=None) / replace_section(text, heading, new)
                                                              the one splice: every other byte stays as it was
    plain_text(markdown) / word_count(text)                   the text without markdown marks (the frames)

Types: text, number, date (YYYY-MM-DD), choice (one of the column's choices), checkbox, link (http / https).
"""

from __future__ import annotations

import math
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from ..format_chars import drop_format

KIND_DOC = "doc"
KIND_SHEET = "sheet"
KINDS = (KIND_DOC, KIND_SHEET)
_PREFIX = {KIND_DOC: "d", KIND_SHEET: "s"}
_ID_RE = re.compile(r"[ds]-[0-9a-f]{12}")
_COLUMN_ID_RE = re.compile(r"c-[0-9a-f]{6}")
_ROW_ID_RE = re.compile(r"r-[0-9a-f]{8}")

TYPE_TEXT, TYPE_NUMBER, TYPE_DATE, TYPE_CHOICE, TYPE_CHECKBOX, TYPE_LINK = (
    "text", "number", "date", "choice", "checkbox", "link")
TYPES = (TYPE_TEXT, TYPE_NUMBER, TYPE_DATE, TYPE_CHOICE, TYPE_CHECKBOX, TYPE_LINK)
TYPE_NAMES = {TYPE_TEXT: "Text", TYPE_NUMBER: "Number", TYPE_DATE: "Date", TYPE_CHOICE: "Choice",
              TYPE_CHECKBOX: "Checkbox", TYPE_LINK: "Link"}

# The caps (PLAN-docs "Limits").
MAX_ITEMS = 300
MAX_DOC_CHARS = 200_000
MAX_DOC_BYTES = 1024 * 1024
MAX_COLUMNS = 20
MAX_ROWS = 2_000
MAX_CELL_CHARS = 500
MAX_CHOICES = 20
MAX_CHOICE_CHARS = 40
MAX_COLUMN_NAME = 40
MAX_SHEET_BYTES = 2 * 1024 * 1024
MAX_NAME = 60
MAX_PENDING_ROWS = 200
MAX_PENDING_SECTIONS = 30
MAX_IMPORTED = 2_000
MAX_KEY_COLUMNS = 3

REASON_TOO_LONG = "too_long"
REASON_NOT_NUMBER = "not_a_number"
REASON_NOT_DATE = "not_a_date"
REASON_NOT_CHOICE = "not_a_choice"
REASON_NOT_CHECKBOX = "not_a_checkbox"
REASON_NOT_LINK = "not_a_link"
REASON_BAD_TYPE = "bad_type"
REASON_NO_NAME = "no_name"
REASON_SAME_NAME = "same_name"
REASON_NO_CHOICES = "no_choices"
REASON_TOO_MANY = "too_many"
# What each reason says on the screen (Jarvis's own words).
REASON_WORDS = {REASON_TOO_LONG: "That is longer than a cell holds (500 characters)",
                REASON_NOT_NUMBER: "That isn't a number",
                REASON_NOT_DATE: "That isn't a date (write it as 2026-11-22)",
                REASON_NOT_CHOICE: "That isn't one of the column's choices",
                REASON_NOT_CHECKBOX: "A checkbox is yes or no",
                REASON_NOT_LINK: "That isn't a web link (https://...)",
                REASON_BAD_TYPE: "That type isn't one Jarvis knows",
                REASON_NO_NAME: "A column needs a name",
                REASON_SAME_NAME: "There is a column with that name already",
                REASON_NO_CHOICES: "A choice column needs its choices (comma between them)",
                REASON_TOO_MANY: "That is more than a sheet holds"}

_TRUE_WORDS = frozenset({"yes", "y", "true", "x", "1", "checked", "done", "on"})
_FALSE_WORDS = frozenset({"no", "n", "false", "0", "unchecked", "off", ""})
_US_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def new_id(kind: str) -> str:
    return f"{_PREFIX[kind]}-{secrets.token_hex(6)}"


def kind_of(item_id: str) -> str:
    """KIND_DOC, KIND_SHEET or "" (not an id)."""
    if not valid_id(item_id):
        return ""
    return KIND_DOC if item_id.startswith("d-") else KIND_SHEET


def valid_id(item_id: Any) -> bool:
    return isinstance(item_id, str) and bool(_ID_RE.fullmatch(item_id))


def new_column_id() -> str:
    return f"c-{secrets.token_hex(3)}"


def new_row_id() -> str:
    return f"r-{secrets.token_hex(4)}"


def valid_column_id(column_id: Any) -> bool:
    return isinstance(column_id, str) and bool(_COLUMN_ID_RE.fullmatch(column_id))


def valid_row_id(row_id: Any) -> bool:
    return isinstance(row_id, str) and bool(_ROW_ID_RE.fullmatch(row_id))


# ---- text -------------------------------------------------------------------------------------------------------

def clean_line(text: Any, cap: int = MAX_CELL_CHARS) -> str:
    """One line: control and format characters gone, whitespace collapsed, at most ``cap`` characters."""
    if not isinstance(text, str):
        return ""
    plain = drop_format(_CONTROL_RE.sub(" ", text.replace("\r", " ").replace("\n", " ").replace("\t", " ")))
    return " ".join(plain.split())[:cap].strip()


def clean_name(text: Any, cap: int = MAX_NAME) -> str:
    return clean_line(text, cap)


def fold(text: Any) -> str:
    """For comparing names and headings: casefolded, accents kept, whitespace and punctuation at the ends gone."""
    plain = unicodedata.normalize("NFKC", clean_line(text, 400)).casefold()
    return " ".join(plain.split()).strip(" .:;,-#*_")


def clean_doc_text(text: Any) -> str:
    """The owner's markdown as it is kept: LF line ends, no control character but the tab."""
    if not isinstance(text, str):
        return ""
    return _CONTROL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def word_count(text: str) -> int:
    return len(re.findall(r"\w+(?:['’]\w+)*", plain_text(text)))


_LINK_RE = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|~~|`+)(?=\S)(.+?)(?<=\S)\1")


def plain_text(markdown: str) -> str:
    """The words without the markdown marks: heading hashes, emphasis, code ticks, links' targets (their words
    stay), images, quote marks and list bullets ("- " stays a bullet "• ")."""
    lines = []
    in_fence = False
    for line in clean_doc_text(markdown).split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(line)
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            line = (heading.group(2) or "").rstrip("#").rstrip()
        stripped = line.lstrip()
        while stripped.startswith(">"):
            stripped = stripped[1:].lstrip()
        bullet = re.match(r"([-*+])\s+", stripped)
        if bullet:
            stripped = "• " + stripped[bullet.end():]
        stripped = _LINK_RE.sub(lambda m: "" if m.group(0).startswith("!") else m.group(1), stripped)
        for _ in range(3):
            stripped = _EMPHASIS_RE.sub(r"\2", stripped)
        lines.append(stripped)
    return "\n".join(lines).strip()


# ---- markdown sections ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    start: int          # the heading line's first character
    body: int           # just after the heading line (its LF)
    end: int            # the section's end: the next heading of this level or higher, or the end of the text

    @property
    def key(self) -> str:
        return fold(self.title)


def headings(text: str) -> list[Heading]:
    """The ATX headings outside fenced code, in order, with their sections' ends."""
    found: list[tuple[int, str, int, int]] = []
    offset = 0
    in_fence = ""
    for line in text.split("\n"):
        line_end = offset + len(line)
        nl_end = min(len(text), line_end + 1)
        fence = _FENCE_RE.match(line)
        if fence:
            mark = fence.group(1)[0]
            if not in_fence:
                in_fence = mark
            elif in_fence == mark:
                in_fence = ""
        elif not in_fence:
            match = _HEADING_RE.match(line)
            if match:
                title = (match.group(2) or "").rstrip("#").rstrip()
                found.append((len(match.group(1)), title, offset, nl_end))
        offset = line_end + 1
    result: list[Heading] = []
    for index, (level, title, start, body) in enumerate(found):
        end = len(text)
        for later_level, _title, later_start, _body in found[index + 1:]:
            if later_level <= level:
                end = later_start
                break
        result.append(Heading(level, title, start, body, end))
    return result


def find_headings(text: str, name: Any) -> list[Heading]:
    key = fold(name)
    if not key:
        return []
    return [heading for heading in headings(text) if heading.key == key]


def _block(new: str) -> str:
    """A section's text as it is spliced in: LF line ends, ending in one LF."""
    body = clean_doc_text(new).strip("\n")
    return body + "\n" if body else ""


def add_section(text: str, new: str, after: Heading | None = None) -> str:
    """``new`` as a section of its own at the end of ``after``'s section (None: at the end of the text), with a blank
    line before it; nothing else changes."""
    block = _block(new)
    if not block:
        return text
    at = after.end if after is not None else len(text)
    before, rest = text[:at], text[at:]
    if before and not before.endswith("\n"):
        before += "\n"
    if before and not before.endswith("\n\n"):
        before += "\n"
    if rest and not rest.startswith("\n"):
        block += "\n"
    return before + block + rest


def replace_section(text: str, heading: Heading, new: str) -> str:
    """``heading``'s section (the heading and what follows it up to the next heading of its level or higher) becomes
    ``new``; empty ``new`` removes the section. Every other byte stays."""
    block = _block(new)
    before, rest = text[:heading.start], text[heading.end:]
    if block and rest and not rest.startswith("\n"):
        block += "\n"
    return before + block + rest


# ---- columns and values -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    id: str
    name: str
    type: str = TYPE_TEXT
    choices: tuple[str, ...] = field(default=())

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, "Text")


def clean_choices(raw: Any) -> tuple[str, ...]:
    """A choice column's choices from a list or "a, b, c" (each one line, at most 40 characters, no duplicates)."""
    items = raw.split(",") if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        choice = clean_line(item, MAX_CHOICE_CHARS)
        if choice and fold(choice) not in seen:
            seen.add(fold(choice))
            out.append(choice)
    return tuple(out)


def check_column(name: Any, type_: Any, choices: Any = (), *, taken: tuple[str, ...] = (),
                 column_id: str = "") -> tuple[Column | None, str]:
    """A new column, or (None, reason): a name (40 characters), a known type, a choice column's 1..20 choices; a name
    another column has (``taken``, folded) is refused."""
    text = clean_line(name, MAX_COLUMN_NAME)
    if not text:
        return None, REASON_NO_NAME
    if type_ not in TYPES:
        return None, REASON_BAD_TYPE
    if fold(text) in {fold(other) for other in taken}:
        return None, REASON_SAME_NAME
    picked: tuple[str, ...] = ()
    if type_ == TYPE_CHOICE:
        picked = clean_choices(choices)
        if not picked:
            return None, REASON_NO_CHOICES
        if len(picked) > MAX_CHOICES:
            return None, REASON_TOO_MANY
    return Column(column_id or new_column_id(), text, type_, picked), ""


def column_from(data: Any) -> Column | None:
    """A column as sheet.json keeps it, or None when it is not one."""
    if not isinstance(data, dict):
        return None
    column_id, name, type_ = data.get("id"), data.get("name"), data.get("type")
    if not valid_column_id(column_id) or not isinstance(name, str) or type_ not in TYPES:
        return None
    choices = clean_choices(data.get("choices") or ())[:MAX_CHOICES] if type_ == TYPE_CHOICE else ()
    text = clean_line(name, MAX_COLUMN_NAME)
    if not text or (type_ == TYPE_CHOICE and not choices):
        return None
    return Column(column_id, text, type_, choices)


def column_data(column: Column) -> dict[str, Any]:
    data: dict[str, Any] = {"id": column.id, "name": column.name, "type": column.type}
    if column.type == TYPE_CHOICE:
        data["choices"] = list(column.choices)
    return data


def _number(raw: Any) -> float | int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = raw
    else:
        text = str(raw).strip().replace(",", "").replace("_", "").replace(" ", "")
        if text.startswith("$"):
            text = text[1:]
        if not _NUMBER_RE.fullmatch(text):
            return None
        value = float(text)
    if not math.isfinite(value) or abs(value) > 1e15:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _date(raw: Any) -> str | None:
    text = str(raw).strip()
    match = _ISO_DATE_RE.fullmatch(text)
    if match is None and len(text) > 10 and text[10] in "T ":
        match = _ISO_DATE_RE.fullmatch(text[:10])
    try:
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        us = _US_DATE_RE.fullmatch(text)
        if us:
            return date(int(us.group(3)), int(us.group(1)), int(us.group(2))).isoformat()
    except ValueError:
        return None
    return None


def link_ok(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not any(ch.isspace() for ch in url)


def parse_value(column: Column, raw: Any) -> tuple[Any, str]:
    """A cell's value from what was typed (or proposed) in ``column``: (value, "") - None an empty cell - or (None,
    REASON_*). A value already of the column's type is taken as it is (after the same check)."""
    if raw is None:
        return None, ""
    if column.type == TYPE_CHECKBOX:
        if isinstance(raw, bool):
            return raw, ""
        word = clean_line(str(raw), 20).casefold()
        if word in _TRUE_WORDS:
            return True, ""
        if word in _FALSE_WORDS:
            return False, ""
        return None, REASON_NOT_CHECKBOX
    if isinstance(raw, bool):
        return None, {TYPE_NUMBER: REASON_NOT_NUMBER, TYPE_DATE: REASON_NOT_DATE, TYPE_CHOICE: REASON_NOT_CHOICE,
                      TYPE_LINK: REASON_NOT_LINK}.get(column.type, REASON_BAD_TYPE)
    if isinstance(raw, str) and len(raw) > MAX_CELL_CHARS * 2:
        return None, REASON_TOO_LONG
    if column.type == TYPE_NUMBER and isinstance(raw, (int, float)):
        value = _number(raw)
        return (value, "") if value is not None else (None, REASON_NOT_NUMBER)
    text = clean_line(str(raw), MAX_CELL_CHARS + 1)
    if not text:
        return None, ""
    if len(text) > MAX_CELL_CHARS:
        return None, REASON_TOO_LONG
    if column.type == TYPE_TEXT:
        return text, ""
    if column.type == TYPE_NUMBER:
        value = _number(text)
        return (value, "") if value is not None else (None, REASON_NOT_NUMBER)
    if column.type == TYPE_DATE:
        day = _date(text)
        return (day, "") if day is not None else (None, REASON_NOT_DATE)
    if column.type == TYPE_CHOICE:
        for choice in column.choices:
            if fold(choice) == fold(text):
                return choice, ""
        return None, REASON_NOT_CHOICE
    if column.type == TYPE_LINK:
        return (text, "") if link_ok(text) else (None, REASON_NOT_LINK)
    return None, REASON_BAD_TYPE


def stored_value(column: Column, value: Any) -> Any:
    """A value read from sheet.json, kept only when it is still of the column's type (else an empty cell)."""
    if value is None:
        return None
    checked, reason = parse_value(column, value)
    return None if reason else checked


def show_value(column: Column, value: Any) -> str:
    """The cell as the table shows it ("" empty; a checkbox's state is its tick, so "Yes" / "")."""
    if value is None:
        return ""
    if column.type == TYPE_NUMBER and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,}" if isinstance(value, int) else f"{value:,.10g}"
    if column.type == TYPE_CHECKBOX:
        return "Yes" if value is True else ""
    return str(value)


def edit_value(column: Column, value: Any) -> str:
    """The cell as its editor starts (a number without grouping)."""
    if value is None:
        return ""
    if column.type == TYPE_NUMBER and isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return show_value(column, value)


def csv_value(column: Column, value: Any) -> str:
    """The cell in a .csv, by type: a number plain, a checkbox TRUE / FALSE, a date YYYY-MM-DD."""
    if value is None:
        return "FALSE" if column.type == TYPE_CHECKBOX else ""
    if column.type == TYPE_CHECKBOX:
        return "TRUE" if value is True else "FALSE"
    if column.type == TYPE_NUMBER and isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return str(value)
