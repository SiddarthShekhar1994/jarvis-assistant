"""Ask's typed frames (the new look, part 6; DESIGN-SPEC 3.1, 3.3, 3.4 and 3.5): the optional ``topic`` and ``frames`` an
Ask or a web research answer may carry, checked strictly here before anything of them can reach the board. Qt-free and
pure: no file, no log, no clock, no network; nothing here raises for odd data. A frame that fails is dropped with a
reason word (the caller logs the counts and the words, never a value); the answer itself never fails because of one.

    check_answer(data) -> AskFrames            a run's structured output (a dict): its topic and its frames
    check_frame(value) -> AskFrame | str       one frame, else the reason word it is dropped for
    build(frame, *, frame_id) -> frames.Built  its frame message (board.protocol.check_message passes it)
    frame_key(kind, title) -> str              its source key: the kind and the title folded ("table:budget lines")
    format_number(value) -> str                a finite number as the board shows it ("3,400", "0.25", "−4")
    chart_texts(values) / chart_word(value, values)   a chart's words for its values, in one scale ("1.2k", "3.4k")
    maths_text(tree) -> str                    a checked expression as plain words ("x² − 4x + 3 = 0")
    answer_fact(tree) -> str                   a maths answer as a satellite's fact ("= 3,400"; an equation as it is)

The model's fields. Every string must be a string no longer than its cap as given; it is then cleaned with
live.clean_line (secrets redacted, control, bidi and invisible characters shown as "<U+202E>", line breaks as " ↵ ") and
cut to its cap as the page counts it. A number must be an int or a float (never a boolean, never a string), finite,
under NUMBER_LIMIT and zero or at least NUMBER_FLOOR. A key no kind has, a wrong type, too many items or nothing in
it (no columns or rows, bars, nodes or steps): the frame is dropped; another kind's field (the schema's one frame item
offers every kind's) is left out, never read or shown. At most FRAMES_CAP frames (the rest are
dropped, counted); a second frame of the same kind and title is dropped ("repeat": a key names one frame).
    every kind  "kind" (table, chart, timeline, maths) and "title" (words, at most 60)
    table       "columns" (1 to 6 header words, each at most 24), "rows" (1 to 12, each a list of at most as many cells as
                there are columns: words of at most 40 or a number; a shorter row is filled with ""), "unit" (at most
                12), "changed" (a row's index, -1 none) with "change" (its delta line, at most 80), "source" (at most
                60)
    chart       "bars" (1 to 8: {"label" (at most 24), "value" (a number)}), "unit", "changed" (a bar's index, -1 none)
                with "was" (its old value: the ghost tick) and "change" (the delta line, at most 80)
    timeline    "nodes" (1 to 8: {"when" (at most 24), "words" (at most 40), "state" done / next / due / ""}),
                "today" (the TODAY marker stands before that node: 0 to the number of nodes; -1 none)
    maths       "steps" (1 to 8: {"name" (at most 24), "expr"}) and "answer" ({"name", "expr"})
    ("change" or "was" without "changed" is left out; every field but "kind", "title", the kind's lists and a maths
    answer is optional.)
An expression is a number, a name (1 to 12 letters: a-z, A-Z or Greek) or a list [op, argument, ...] with op one of
MATHS_OPS: "+" and "*" 2 to 8 arguments, "-" 1 (negation) or 2, "/" (shown ÷), "frac" (a over b) and "pow" (a to the
power b) 2, "root" 1 or 2 (the radicand, then its index), "()" 1 (brackets), "=" and "or" 2 to 4. At most MATHS_DEPTH
levels in an expression and MATHS_NODES nodes in a frame. Never markup and never TeX: a string is a name or it fails.

What the board gets (board.protocol checks it again; the page's renderers once more): the frame's kind, title, meta (a
table's or a chart's unit, a timeline's "Due THU", a maths frame's "3 steps") and its body - every string cleaned and
capped, every number a finite one the board can show, a number's words made here (format_number: never the page's):
    table     {"columns", "rows" (every row as many cells as columns, a number as its words), "unit", "changed",
               "change", "source"}
    chart     {"bars": [{"label", "value", "text" (its words: "3.4k")}], "unit", "changed", "was" (a number or null),
               "change"}
    timeline  {"nodes": [{"when", "words", "state"}], "today"}
    maths     {"steps": [{"name", "expr"}], "answer": {"name", "expr"}}; an expression is ["num", words] (a negative
               number's words start with "−"), ["name", letters] or [op, expression, ...]
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .. import live
from . import protocol

KINDS = ("table", "chart", "timeline", "maths")
FRAMES_CAP = 3                              # frames in one answer
MINUS = "−"                            # how the board writes a minus sign
NUMBER_LIMIT = protocol.NUMBER_LIMIT        # a number on the board is under this ...
NUMBER_FLOOR = 1e-6                         # ... and zero or at least this (its words never run long)
SIGNIFICANT = 10                            # a number that is not whole: at most this many significant digits
ELLIPSIS = "…"

# Reason words (a log line may carry them; never a value).
D_SHAPE = "shape"           # not a dict / a list where one is wanted, or a wrong type of field
D_FIELD = "field"           # a key the kind does not have, or one it needs is missing
D_KIND = "kind"             # not one of KINDS
D_TOO_LONG = "too_long"     # a string over its cap, or too many items
D_EMPTY = "empty"           # nothing in it: no title, columns, rows, bars, nodes or steps
D_NUMBER = "number"         # not a finite number the board can show (a boolean, a string, NaN, 1e309 ...)
D_INDEX = "index"           # changed or today out of range
D_STATE = "state"           # a timeline state that is none of NODE_STATES
D_MATHS = "maths"           # an expression that is not one: an unknown op, its arguments, a name that is no name
D_DEPTH = "depth"           # an expression deeper than MATHS_DEPTH
D_NODES = "nodes"           # more than MATHS_NODES in a frame
D_REPEAT = "repeat"         # a second frame of the same kind and title
D_TOO_MANY = "too_many"     # past FRAMES_CAP

_KEYS = {
    "table": ("kind", "title", "columns", "rows", "unit", "changed", "change", "source"),
    "chart": ("kind", "title", "bars", "unit", "changed", "was", "change"),
    "timeline": ("kind", "title", "nodes", "today"),
    "maths": ("kind", "title", "steps", "answer"),
}
_ANY_KIND = frozenset(key for keys in _KEYS.values() for key in keys)   # the schema's one frame item: every kind's
_NAME = re.compile(r"[A-Za-zΑ-ΡΣ-Ωα-ω]{1,%d}" % protocol.NAME_LETTERS)
_SUPER_FROM = "0123456789+=()ni" + MINUS
_SUPERSCRIPT = str.maketrans(_SUPER_FROM, "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁼⁽⁾ⁿⁱ⁻")
_CHART_SCALES = ((10 ** 12, "T"), (10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "k"))


class _Drop(Exception):
    """A frame that fails: ``reason`` is one of the D_* words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, repr=False)
class AskFrame:
    """One checked frame: its kind, title, meta, body (as board.protocol checks it) and source key. repr: its kind."""

    kind: str
    title: str
    meta: str
    body: dict[str, Any] = field(compare=False)
    key: str = ""

    def __repr__(self) -> str:
        return f"AskFrame({self.kind})"


@dataclass(frozen=True, repr=False)
class AskFrames:
    """An answer's topic ("" none) and checked frames; ``dropped`` the reason words of the frames left out (one per
    frame, the ones past FRAMES_CAP as one count), ``topic_dropped`` when a topic was given but did not pass. repr:
    counts and words only."""

    topic: str = ""
    frames: tuple[AskFrame, ...] = ()
    dropped: tuple[tuple[str, int], ...] = ()
    topic_dropped: bool = False

    @property
    def given(self) -> bool:
        """The answer said anything of a topic or frames (kept or not)."""
        return bool(self.topic or self.frames or self.dropped or self.topic_dropped)

    def dropped_count(self) -> int:
        return sum(count for _reason, count in self.dropped)

    def __repr__(self) -> str:
        kinds = ", ".join(frame.kind for frame in self.frames)
        words = ", ".join(f"{reason} {count}" for reason, count in self.dropped)
        return f"AskFrames(topic={bool(self.topic)}, frames=[{kinds}], dropped=[{words}])"


# --------------------------------------------------------------------------
# Numbers and words
# --------------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    """A finite number the board can show: an int or a float (never a bool), under NUMBER_LIMIT, zero or at least
    NUMBER_FLOOR. A huge int is compared as it is (never turned into a float first)."""
    if type(value) is int:
        return -NUMBER_LIMIT < value < NUMBER_LIMIT
    if type(value) is float:
        return math.isfinite(value) and abs(value) < NUMBER_LIMIT and (value == 0 or abs(value) >= NUMBER_FLOOR)
    return False


def _number(value: Any) -> int | float:
    if not _is_number(value):
        raise _Drop(D_NUMBER)
    return value


def _grouped(digits: str) -> str:
    """"1234567.5" (no sign) with thousands separators: "1,234,567.5"."""
    whole, _point, part = digits.partition(".")
    return f"{int(whole or '0'):,}" + ("." + part if part else "")


def _decimal(value: int | float) -> Decimal:
    return Decimal(value) if type(value) is int else Decimal(repr(float(value)))


def format_number(value: Any) -> str:
    """A finite number as the board shows it: a whole number in full with thousands separators ("3,400"), any other
    at most SIGNIFICANT significant digits (rounded half up, never in exponent form: "0.25", "1,234.5") but never fewer
    than its whole digits (12345678901.5 is "12,345,678,902": only a fraction is ever cut), a negative one after "−".
    ValueError for anything that is not a number the board can show."""
    if not _is_number(value):
        raise ValueError("not a number the board can show")
    exact = _decimal(value)
    magnitude = abs(exact)
    if magnitude == magnitude.to_integral_value():
        text = f"{int(magnitude):,}"
    else:
        quantum = Decimal(1).scaleb(min(magnitude.adjusted() - SIGNIFICANT + 1, 0))   # never into the whole part
        try:
            rounded = magnitude.quantize(quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            rounded = magnitude
        digits = format(rounded, "f")
        if "." in digits:
            digits = digits.rstrip("0").rstrip(".")
        text = _grouped(digits)
    return f"{MINUS}{text}" if exact < 0 and text != "0" else text


def _in_scale(value: int | float, scale: int) -> Decimal:
    """``value`` in units of ``scale``, rounded half up to one decimal (as a chart shows it)."""
    return (_decimal(value) / scale).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _scaled(value: int | float, scale: int, suffix: str) -> str:
    """A chart's value in its scale with one decimal ("3.4k", "0.8k", "−1.2M")."""
    shown = _in_scale(value, scale)
    text = _grouped(format(abs(shown), "f")) + suffix
    return f"{MINUS}{text}" if shown < 0 else text


def _chart_scale(values: list[int | float]) -> tuple[int, str] | None:
    """The scale a chart of ``values`` shows them in (see chart_texts); None when the largest is under 1,000."""
    biggest = max((abs(value) for value in values), default=0)
    for at, (scale, suffix) in enumerate(_CHART_SCALES):
        if biggest >= scale:
            if at and abs(_in_scale(biggest, scale)) >= 1000:
                return _CHART_SCALES[at - 1]
            return scale, suffix
    return None


def chart_texts(values: list[int | float]) -> list[str]:
    """The words of a chart's values: all in one scale when the largest is 1,000 or more ("1.2k", "3.4k", "0.8k"),
    else each as format_number says it. The scale is the one the largest needs as it is shown: 999,950 rounds up to a
    thousand k, so it reads "1.0M" (and M moves to B, B to T; T is the top, so 999.95T may read "1,000.0T")."""
    found = _chart_scale(values)
    return [format_number(value) if found is None else _scaled(value, *found) for value in values]


def chart_word(value: int | float, values: list[int | float]) -> str:
    """``value`` in the words a chart of ``values`` gives its bars (a bar's own, or its old value: the ghost tick's)."""
    found = _chart_scale(values)
    return format_number(value) if found is None else _scaled(value, *found)


def _clean(value: str, cap: int) -> str:
    """Model words already within ``cap`` as given: cleaned (board.frames.clean: live.clean_line, lone surrogates
    escaped), then cut to ``cap`` as the page counts it (an escape the cleaner added may make it longer)."""
    from . import frames      # (imported here: ask.stream imports this module, and frames imports ask's mail)

    return frames.clean(value, cap)


def _words(value: Any, cap: int, *, optional: bool = False, required: bool = False) -> str:
    """Words of the model's: a string of at most ``cap`` characters as given, then cleaned and cut. ``optional``: an
    absent field (None) is ""; ``required``: never empty once cleaned."""
    if value is None and optional:
        return ""
    if type(value) is not str:
        raise _Drop(D_SHAPE)
    if len(value) > cap:
        raise _Drop(D_TOO_LONG)
    shown = _clean(value, cap)
    if required and not shown:
        raise _Drop(D_EMPTY)
    return shown


def _items(value: Any, cap: int) -> list[Any]:
    """A list of 1 to ``cap`` items (its length is read before anything in it)."""
    if type(value) is not list:
        raise _Drop(D_SHAPE)
    if len(value) > cap:
        raise _Drop(D_TOO_LONG)
    if not value:
        raise _Drop(D_EMPTY)
    return value


def _fields(value: Any, allowed: tuple[str, ...], required: tuple[str, ...] = ()) -> dict[str, Any]:
    if type(value) is not dict:
        raise _Drop(D_SHAPE)
    if any(type(key) is not str or key not in allowed for key in value):
        raise _Drop(D_FIELD)
    if any(key not in value for key in required):
        raise _Drop(D_FIELD)
    return value


def _index(value: Any, high: int) -> int:
    """An index from -1 (none) to ``high`` (absent: -1); never a boolean or a float."""
    if value is None:
        return -1
    if type(value) is not int:
        raise _Drop(D_INDEX)
    if not -1 <= value <= high:
        raise _Drop(D_INDEX)
    return value


def frame_key(kind: str, title: str) -> str:
    """A typed frame's source key: its kind and its title folded to lower case and single spaces."""
    return f"{kind}:{' '.join(str(title or '').casefold().split())}"


# --------------------------------------------------------------------------
# The kinds
# --------------------------------------------------------------------------

def _cell(value: Any) -> str:
    """A table's cell: words, or a number as its words ("1,200"); a boolean is no number, anything else no cell."""
    if type(value) is str:
        return _words(value, protocol.CELL_CAP)
    if type(value) in (int, float, bool):
        return format_number(_number(value))
    raise _Drop(D_SHAPE)


def _table(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    columns = [_words(item, protocol.COLUMN_CAP) for item in _items(data["columns"], protocol.TABLE_COLUMNS_CAP)]
    rows: list[list[str]] = []
    for row in _items(data["rows"], protocol.TABLE_ROWS_CAP):
        cells = [_cell(cell) for cell in _items(row, len(columns))]
        rows.append(cells + [""] * (len(columns) - len(cells)))
    if not any(any(cells) for cells in rows):
        raise _Drop(D_EMPTY)
    changed = _index(data.get("changed"), len(rows) - 1)
    unit = _words(data.get("unit"), protocol.UNIT_CAP, optional=True)
    change = _words(data.get("change"), protocol.CHANGE_CAP, optional=True)
    body = {"columns": columns, "rows": rows, "unit": unit, "changed": changed, "change": change if changed >= 0 else "",
            "source": _words(data.get("source"), protocol.SOURCE_CAP, optional=True)}
    return body, unit


def _chart(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    bars: list[tuple[str, int | float]] = []
    for item in _items(data["bars"], protocol.BARS_CAP):
        bar = _fields(item, ("label", "value"), ("label", "value"))
        bars.append((_words(bar["label"], protocol.LABEL_CAP), _number(bar["value"])))
    changed = _index(data.get("changed"), len(bars) - 1)
    was = data.get("was")
    if was is not None:
        was = _number(was)
    change = _words(data.get("change"), protocol.CHANGE_CAP, optional=True)
    texts = chart_texts([value for _label, value in bars])
    unit = _words(data.get("unit"), protocol.UNIT_CAP, optional=True)
    body = {"bars": [{"label": label, "value": value, "text": text} for (label, value), text in zip(bars, texts)],
            "unit": unit, "changed": changed, "was": was if changed >= 0 else None,
            "change": change if changed >= 0 else ""}
    return body, unit


def _timeline(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    nodes: list[dict[str, str]] = []
    for item in _items(data["nodes"], protocol.NODES_CAP):
        node = _fields(item, ("when", "words", "state"), ("when", "words"))
        state = node.get("state", "")
        if type(state) is not str or state not in protocol.NODE_STATES:
            raise _Drop(D_STATE)
        when, words = _words(node["when"], protocol.WHEN_CAP), _words(node["words"], protocol.NODE_WORDS_CAP)
        if not when and not words:
            raise _Drop(D_EMPTY)
        nodes.append({"when": when, "words": words, "state": state})
    today = _index(data.get("today"), len(nodes))
    due = next((node for node in nodes if node["state"] == "due" and node["when"]), None)
    upcoming = next((node for node in nodes if node["state"] == "next" and node["when"]), None)
    meta = f"Due {due['when']}" if due else f"Next {upcoming['when']}" if upcoming else ""
    return {"nodes": nodes, "today": today}, _cut(meta, protocol.META_CAP)


def _expr(value: Any, depth: int, budget: list[int]) -> list[Any]:
    """The model's expression as the board's checked tree (see the module doc); _Drop when it is not one. The depth is
    read before anything under it, an op's arguments counted before any is read: a huge or deep tree costs nothing."""
    if depth > protocol.MATHS_DEPTH:
        raise _Drop(D_DEPTH)
    budget[0] -= 1
    if budget[0] < 0:
        raise _Drop(D_NODES)
    if type(value) in (int, float):
        return ["num", format_number(_number(value))]
    if type(value) is bool:
        raise _Drop(D_NUMBER)
    if type(value) is str:
        if not _NAME.fullmatch(value):
            raise _Drop(D_MATHS)
        return ["name", value]
    if type(value) is not list or not value:
        raise _Drop(D_MATHS)
    op = value[0]
    if type(op) is not str or op not in protocol.MATHS_OPS:
        raise _Drop(D_MATHS)
    low, high = protocol.MATHS_OPS[op]
    if not low <= len(value) - 1 <= high:
        raise _Drop(D_MATHS)
    return [op, *(_expr(item, depth + 1, budget) for item in value[1:])]


def _step(value: Any, budget: list[int]) -> dict[str, Any]:
    step = _fields(value, ("name", "expr"), ("expr",))
    return {"name": _words(step.get("name"), protocol.STEP_NAME_CAP, optional=True),
            "expr": _expr(step["expr"], 1, budget)}


def _maths(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    budget = [protocol.MATHS_NODES]
    steps = [_step(item, budget) for item in _items(data["steps"], protocol.STEPS_CAP)]
    answer = _step(data["answer"], budget)
    return {"steps": steps, "answer": answer}, f"{len(steps) + 1} steps"


_CHECKS = {"table": _table, "chart": _chart, "timeline": _timeline, "maths": _maths}
_REQUIRED = {"table": ("columns", "rows"), "chart": ("bars",), "timeline": ("nodes",), "maths": ("steps", "answer")}


def _cut(text: str, cap: int) -> str:
    from . import frames

    return frames.cut(text, cap)


def _frame(value: Any) -> AskFrame:
    if type(value) is not dict:
        raise _Drop(D_SHAPE)
    kind = value.get("kind")
    if type(kind) is not str or kind not in _CHECKS:
        raise _Drop(D_KIND)
    if any(type(key) is not str or key not in _ANY_KIND for key in value):
        raise _Drop(D_FIELD)                              # a key no kind has
    # Another kind's field (the schema offers every kind's to each) is left out, never read or shown.
    data = _fields({key: item for key, item in value.items() if key in _KEYS[kind]}, _KEYS[kind],
                   ("kind", "title", *_REQUIRED[kind]))
    title = _words(data["title"], protocol.TITLE_CAP, required=True)
    body, meta = _CHECKS[kind](data)
    return AskFrame(kind, title, meta, body, frame_key(kind, title))


def check_frame(value: Any) -> AskFrame | str:
    """One of the model's frames as the board will show it, or the reason word it is dropped for (never raises)."""
    try:
        return _frame(value)
    except _Drop as drop:
        return drop.reason
    except Exception:  # noqa: BLE001 - odd data is dropped, never raised
        return D_SHAPE


def check_answer(data: Any) -> AskFrames:
    """A run's structured output: its "topic" (a string of at most TOPIC_CAP as given, cleaned; else none) and its
    "frames" (a list; at most FRAMES_CAP checked, each kept or dropped with its reason). Never raises."""
    if type(data) is not dict:
        return AskFrames()
    topic, topic_dropped = "", False
    raw_topic = data.get("topic")
    if raw_topic is not None:
        if type(raw_topic) is str and len(raw_topic) <= protocol.TOPIC_CAP:
            topic = _clean(raw_topic, protocol.TOPIC_CAP)
        else:
            topic_dropped = True
    raw = data.get("frames")
    kept: list[AskFrame] = []
    reasons: dict[str, int] = {}
    if raw is not None and type(raw) is not list:
        reasons[D_SHAPE] = 1
    elif raw:
        keys: set[str] = set()
        for item in raw[:FRAMES_CAP]:
            found = check_frame(item)
            if isinstance(found, str):
                reasons[found] = reasons.get(found, 0) + 1
            elif found.key in keys:
                reasons[D_REPEAT] = reasons.get(D_REPEAT, 0) + 1
            else:
                keys.add(found.key)
                kept.append(found)
        if len(raw) > FRAMES_CAP:
            reasons[D_TOO_MANY] = len(raw) - FRAMES_CAP
    return AskFrames(topic, tuple(kept), tuple(sorted(reasons.items())), topic_dropped)


def build(frame: AskFrame, *, frame_id: str) -> Any:
    """``frame`` as the board's frame message (board.frames.Built: the frame alone); ValueError for a frame id that is
    not one (the caller's mistake). The body is a copy: nothing the feed does reaches the answer's own frame."""
    from . import frames

    if not protocol.is_frame_id(frame_id):
        raise ValueError("frame_id is not a frame id (1 to 40 letters, digits, '-' or '_')")
    message = {"type": "frame", "id": frame_id, "kind": frame.kind, "title": frame.title, "meta": frame.meta,
               "body": copy.deepcopy(frame.body)}
    return frames.Built((message,))


# --------------------------------------------------------------------------
# An expression as plain words (the glance tiles, the facts, the tests)
# --------------------------------------------------------------------------

_PREC = {"or": 0, "=": 1, "+": 2, "*": 3, "/": 3, "pow": 5, "num": 6, "name": 6, "()": 6, "frac": 6, "root": 6}


def negative(node: list[Any]) -> bool:
    """A negation, a number whose words start with "−", or a product whose first factor is one of those ("−8x"): in a
    sum it shows as " − 8x", and as a later factor it is bracketed."""
    if node[0] == "-":
        return len(node) == 2
    if node[0] == "num":
        return str(node[1]).startswith(MINUS)
    return node[0] == "*" and negative(node[1])


def prec(node: list[Any]) -> int:
    """How tightly a node binds (the page's renderer uses the same table): or 0, = 1, + and a difference 2, × and ÷ 3,
    a negation (or a negative number) 4, a power 5, the rest 6."""
    head = node[0]
    if head == "num":
        return 4 if negative(node) else 6
    if head == "-":
        return 4 if len(node) == 2 else 2
    return _PREC.get(head, 6)


def _atom(node: list[Any]) -> bool:
    return node[0] in ("name", "()") or (node[0] == "num" and not negative(node))


def _starts(node: list[Any], bracketed: bool) -> str:
    """What a factor shows first: "open", "digit", "minus", "letter" (one letter), "word", "root", "index" (an indexed
    root), "frac"."""
    if bracketed:
        return "open"
    head = node[0]
    if head == "num":
        return "minus" if negative(node) else "digit"
    if head == "name":
        return "letter" if len(node[1]) == 1 else "word"
    if head == "()":
        return "open"
    if head == "root":
        return "index" if len(node) == 3 else "root"
    if head == "pow":
        return _starts(node[1], not _atom(node[1]))
    if head == "*":
        return _starts(node[1], factor_bracketed(node[1], 0))
    if head == "-":
        return "minus"
    return head


def _ends(node: list[Any], bracketed: bool) -> str:
    """What a factor shows last: "close", "digit", "letter", "word", "sup", "root", "frac"."""
    if bracketed:
        return "close"
    head = node[0]
    if head == "num":
        return "digit"
    if head == "name":
        return "letter" if len(node[1]) == 1 else "word"
    if head == "()":
        return "close"
    if head == "pow":
        return "sup"
    if head == "*":
        return _ends(node[-1], factor_bracketed(node[-1], len(node) - 2))
    if head == "-" and len(node) == 2:
        return _ends(node[1], negation_bracketed(node[1]))
    return head


def factor_bracketed(node: list[Any], index: int) -> bool:
    """A product's factor is bracketed when it binds less than ×, or when it is negative and not the first."""
    return prec(node) <= 2 or (index > 0 and negative(node))


def negation_bracketed(node: list[Any]) -> bool:
    """What a minus sign negates is bracketed when it is a sum, a difference or less, or negative itself."""
    return prec(node) <= 2 or negative(node)


def joined(left: str, right: str) -> bool:
    """Two factors stand side by side with no sign ("4x", "(x − 1)(x − 3)", "2√3") only when nothing can be misread:
    what follows starts with one letter, a bracket or a root sign, and what is before ends with a digit, one letter, a
    bracket or a power. Anything else gets "×"."""
    return right in ("letter", "open", "root") and left in ("digit", "letter", "close", "sup")


def _bracket(text: str, yes: bool) -> str:
    return f"({text})" if yes else text


def _superscript(text: str) -> str | None:
    """``text`` in superscript letters when every character has one ("²", "ⁿ", "¹⁰"); else None."""
    if text and all(char in _SUPER_FROM for char in text):
        return text.translate(_SUPERSCRIPT)
    return None


def _plain(node: list[Any]) -> str:
    head, args = node[0], node[1:]
    if head in ("num", "name"):
        return str(node[1])
    if head == "()":
        return f"({_plain(args[0])})"
    if head == "or":
        return " or ".join(_bracket(_plain(arg), prec(arg) <= 0) for arg in args)
    if head == "=":
        return " = ".join(_bracket(_plain(arg), prec(arg) <= 1) for arg in args)
    if head == "+":
        out = _bracket(_plain(args[0]), prec(args[0]) <= 1)
        for arg in args[1:]:
            if negative(arg):
                out += f" {MINUS} {_negated(arg)}"
            else:
                out += f" + {_bracket(_plain(arg), prec(arg) <= 1)}"
        return out
    if head == "-":
        if len(args) == 1:
            return MINUS + _bracket(_plain(args[0]), negation_bracketed(args[0]))
        left, right = args
        return f"{_bracket(_plain(left), prec(left) <= 1)} {MINUS} {_bracket(_plain(right), negation_bracketed(right))}"
    if head == "*":
        out = ""
        for index, arg in enumerate(args):
            bracketed = factor_bracketed(arg, index)
            if index:
                before = args[index - 1]
                out += "" if joined(_ends(before, factor_bracketed(before, index - 1)), _starts(arg, bracketed)) \
                    else " × "
            out += _bracket(_plain(arg), bracketed)
        return out
    if head == "/":           # a fraction that divides is bracketed inline: "6 ÷ (1/2)", never "6 ÷ 1/2"
        left, right = args
        divisor = prec(right) <= 3 or negative(right) or right[0] == "frac"
        return f"{_bracket(_plain(left), prec(left) <= 2)} ÷ {_bracket(_plain(right), divisor)}"
    if head == "frac":
        top, bottom = args
        return f"{_bracket(_plain(top), not _atom(top))}/{_bracket(_plain(bottom), not _atom(bottom))}"
    if head == "pow":
        base, power = args
        shown = _plain(power)
        small = _superscript(shown)
        return _bracket(_plain(base), not _atom(base)) + (small if small else f"^{_bracket(shown, not _atom(power))}")
    if head == "root":
        radicand = _bracket(_plain(args[0]), not _atom(args[0]))
        if len(args) == 2:
            index = _plain(args[1])
            return (_superscript(index) or f"({index})") + "√" + radicand
        return "√" + radicand
    return ""


def positive(node: list[Any]) -> list[Any]:
    """A negative node (negative() says so) without its sign: a number's words without "−", a negation's argument, a
    product with its first factor made positive (the page's renderer does the same)."""
    if node[0] == "num":
        return ["num", str(node[1])[len(MINUS):]]
    if node[0] == "*":
        return ["*", positive(node[1]), *node[2:]]
    return node[1]


def _negated(node: list[Any]) -> str:
    """What a negative term shows after its " − " in a sum (or a difference)."""
    shown = positive(node)
    bracketed = node[0] == "-" and negation_bracketed(shown)
    return _bracket(_plain(shown), bracketed)


def maths_text(tree: Any) -> str:
    """A checked expression (board.protocol's form) as plain words: "x² − 4x + 3 = 0", "(x − 1)(x − 3) = 0",
    "x = 1 or x = 3", "15/100 × 80". "" for anything that is not one (never raises)."""
    try:
        return _plain(tree) if isinstance(tree, list) and tree else ""
    except (IndexError, TypeError, KeyError, RecursionError):
        return ""


def answer_fact(tree: Any) -> str:
    """A maths answer as a satellite's fact: a number alone, or an equation that ends in one, says "= 3,400"; any other
    answer its own words ("x = 1 or x = 3")."""
    if isinstance(tree, list) and tree:
        if tree[0] == "num":
            return f"= {tree[1]}"
        if tree[0] == "=" and isinstance(tree[-1], list) and tree[-1] and tree[-1][0] == "num":
            return f"= {tree[-1][1]}"
    return maths_text(tree)


def working_line(body: Mapping[str, Any]) -> str:
    """A maths frame's working in one line (the second screen's tile): its steps' names, then the last step
    ("Divide by 2, then factor: (x − 1)(x − 3) = 0"); the last step alone when no step is named."""
    steps = [step for step in body.get("steps", []) if isinstance(step, Mapping)] if isinstance(body, Mapping) else []
    if not steps:
        return ""
    names = [str(step.get("name", "") or "") for step in steps if step.get("name")]
    last = maths_text(steps[-1].get("expr"))
    if not names:
        return last
    said = names[0] + "".join(f", then {name[:1].lower()}{name[1:]}" for name in names[1:])
    return f"{said}: {last}" if last else said


__all__ = ["AskFrame", "AskFrames", "FRAMES_CAP", "KINDS", "MINUS", "answer_fact", "build", "chart_texts",
           "chart_word", "check_answer", "check_frame", "factor_bracketed", "format_number", "frame_key", "joined",
           "maths_text", "negation_bracketed", "negative", "positive", "prec", "working_line"]
