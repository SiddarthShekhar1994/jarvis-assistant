"""The glance painter (the new look, part 4; DESIGN-SPEC 3.6 and 6): read-only tiles of the board's frames, painted
natively with QPainter in holo's tokens and hud's typefaces. One painter for every place the board can't be a page: the
board's fallback (BoardView shows GlanceTiles under its "can't start" tile, DESIGN-SPEC 3.6) and the second screen (part 7
paints its glance tiles with ``private=True``).

    tile_layout(frame, width, height=None, *, scale=1.0, private=False, picture=None) -> TileLayout
        where every word, glyph, rule and picture of one tile goes, in the tile's own px (its top-left at 0, 0). Text is
        wrapped at spaces only (a word longer than a line breaks inside it: never clipped), measured with QFontMetricsF,
        and nothing overlaps. ``height`` None: the tile's natural height (it grows); a height: rows that do not fit leave
        and the last row says "+N MORE" (then the bar, then a paragraph's last lines with "…"); a tile too small even
        for its header is clipped to itself, never drawn past it (``clipped``)
    tile_height(frame, width, ...) -> float          the natural height
    paint_tile(painter, rect, frame, *, scale=1.0, private=False, picture=None, ground=False)
        one read-only tile from a frame dict as board.protocol.check_message rebuilt it: outline only (``ground``: the
        frame's own fill, TILE / TILE_AMBER / TILE_ALERT, as the board's fallback shows it), the kind's glyph, the title,
        the meta and the headline rows; ``private`` (the second screen) hides a mail's text and a text frame's
        paragraph (a web research answer: the line Jarvis speaks; its title and source rows stay) and says "Text hidden
        on this screen", and draws a page picture at 62 % instead of 80 %. Nothing in a tile is clickable
    GlanceTiles(QWidget)       stacks the tiles of a list of frames (set_frames), each as tall as it needs; closed
                               frames are left out. Never takes the focus

``scale`` multiplies every size (the second screen's type: 1.333 at 2560x1440); the painter's device pixel ratio does the
rest. Frames' words are drawn as they came (already cleaned and capped by the board's builders and protocol), as text.

``screen`` (part 7): BOARD (the default: the board's own sizes, as the page has them) or SECOND, the second screen's face
(DESIGN-SPEC 6, its still second.html): type read across a desk (titles Chakra 20, bodies Sora 16, the badge role 13 px
and the label role 14 px, a table's change and a maths answer large), 20 / 22 px of padding, a 20 px glyph, a calendar
row at its start time (its meridiem kept) with its meta under its title, free rows plain ("Free", its length under it,
in INK_4), no past rows, no now pill and no hour labels (the day bar's now tick says where now is), no row rules, a
table's headline fitted to its tile (part 7 fixes), and whites capped at OLED_INK (OLED care: INK_1 and AMBER_TX drawn
as OLED_INK and AMBER_TX2, nothing filled with a tile's ground). Lines break at spaces only, never after a hyphen (a
word longer than a line breaks inside it, not right after a hyphen of its own).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import holo, hud

GAP = 12                        # between two tiles of GlanceTiles
PAD_TOP, PAD_SIDE, PAD_BOTTOM = 11, 14, 14
GLYPH = 16
HEAD_GAP = 8                    # under the header row
TITLE_MIN = 165                 # px of title (11 em of 15 px) the meta never squeezes: it goes under the title instead
NARROW = 330                    # px of content from which a calendar row's meta goes under its title
PICTURE_LEVEL = 0.8             # a page picture's brightness ...
PRIVATE_PICTURE_LEVEL = 0.62    # ... and on the second screen
TEXT_LINES = 3                  # a mail's text, at most (the page clamps it the same)
TITLE_LINES = 2                 # a title on the second screen, at most (part 7 fixes: then "…")
HIDDEN = "Text hidden on this screen"
ON_MAIN = "On the main screen, Ctrl+3"
PICTURE_ONLY = "Picture only"
WAITS_WORD, DONE_WORD = "Waits", "Done"
KEY_CAP = "Ctrl 3"
CHANGED = "Changed"
MORE = "+{count} MORE"
ELLIPSIS = "…"
GLYPHS = {"text": "read_all", "calendar": "calendar", "mail_thread": "mail", "mail_list": "inbox", "todos": "todos",
          "picture": "image", "table": "table", "chart": "chart", "timeline": "timeline", "maths": "maths",
          "approvals": "approvals", "agents": "agents"}            # part 3B: the resting frames
# Part 3B: the resting frames' words on a plain tile (the page's own words, as board.js says them).
AGENT_WORDS = {"on": "On", "paused": "Paused", "off": "Not on yet"}
WAIT_WORDS = ("{count} waits for your OK", "{count} wait for your OK")
PROPOSAL_WORDS = ("{count} proposal waits", "{count} proposals wait")
AGENTS_WORDS = ("{count} agent", "{count} agents")
TODAY_WORD = "Today"                                           # part 6: a timeline's TODAY marker (badge role: caps)
STATE_WORDS = {"done": "Done", "next": "Next", "due": "Due"}   # ... and its nodes' states
ROW_NODES = 4                    # a timeline lines its nodes up in a row with at most this many ...
ROW_NODE = 86                    # ... each with at least this much room (px, at the type's scale: 3 in a 448 px tile
                                 # and in the second screen's 384 px ones); else one node per row
ROW_FROM = 300                   # maths: a step's name goes over its line under this much content (the page's rule)
_MORE_RE = re.compile(r"\+(\d+) MORE", re.IGNORECASE)
_ZERO_RE = re.compile(r"[+\-−]?[$€£]?0+(?:[.,]0+)?%?")       # a table cell that says none (the page's)
_FIGURE_RE = re.compile(r"[+\-−]?[$€£]?[0-9]")                 # a delta line that starts with its figure
_PLAIN_NUMBER_RE = re.compile(r"[$€£]?[0-9][0-9,]*(?:\.[0-9]+)?[%kKMB]?")   # "2,900", "$3.4k" (no sign)
# Part 6 fixes (review 6, board 4): a table's change column - headed as one ("Change", "Change %", "Δ", "Diff",
# "Delta", "+/-", "Variance") or a cell with its sign ("+500", "−200") - names the row's before and after figures: the two
# columns just before it.
_SIGNED_RE = re.compile(r"[+\-−±][$€£]?[0-9][0-9,]*(?:\.[0-9]+)?[%kKMB]?")
_CHANGE_HEAD_RE = re.compile(r"\b(?:change|changes|changed|chg|delta|diff|difference|variance)\b|Δ|±|\+/-",
                             re.IGNORECASE)

BOARD, SECOND = "board", "second"   # the faces a tile is laid out in (part 7: SECOND, the second screen)

# Type: (family, px, line height, weight, tracking em, caps) - holo's roles, and the few the board's style sets itself.
_TYPE: dict[str, tuple[str, float, float, int, float, bool]] = {
    "title": (hud.DISPLAY_FAMILY, 15, 20, 500, 0.01, False),      # frame_title
    "badge": (hud.MONO_FAMILY, 10, 12, 500, 0.16, True),          # badge
    "body": (hud.BODY_FAMILY, 13, 20, 400, 0.0, False),           # body_sm
    "row": (hud.BODY_FAMILY, 13, 18, 400, 0.0, False),            # a row's words
    "strong": (hud.BODY_FAMILY, 13, 18, 600, 0.0, False),         # a sender
    "small": (hud.BODY_FAMILY, 12, 16, 400, 0.0, False),          # a calendar row's meta
    "caption": (hud.BODY_FAMILY, 12.5, 16, 400, 0.0, False),      # a picture's caption, a mail row's subject
    "time": (hud.MONO_FAMILY, 12, 16, 500, 0.04, False),          # frame_time
    "axis": (hud.MONO_FAMILY, 11, 14, 500, 0.04, False),          # the calendar's hours
    "meta": (hud.MONO_FAMILY, 10, 12, 500, 0.12, True),           # the next event's "IN 19 MIN"
    "pill": (hud.MONO_FAMILY, 10, 16, 600, 0.06, False),          # the now pill
    "chip": (hud.BODY_FAMILY, 13, 16, 500, 0.0, False),           # the waits chip's words
    "kbd": (hud.MONO_FAMILY, 10, 12, 500, 0.08, True),            # its CTRL 3
    # Part 6, Ask's typed frames (the page's own sizes):
    "label": (hud.MONO_FAMILY, 11, 14, 500, 0.20, True),          # label role: a table's headers
    "cell": (hud.BODY_FAMILY, 12.5, 18, 400, 0.0, False),         # a table's cells
    "cell_strong": (hud.BODY_FAMILY, 12.5, 18, 600, 0.0, False),  # ... the changed row's change
    "value": (hud.MONO_FAMILY, 11, 14, 500, 0.0, False),          # a chart's values
    "delta": (hud.BODY_FAMILY, 12, 17, 400, 0.0, False),          # a delta line ...
    "delta_strong": (hud.BODY_FAMILY, 12, 17, 600, 0.0, False),   # ... and its figure
    "step": (hud.MONO_FAMILY, 10, 12, 500, 0.0, False),           # a maths step's number
    "expr": (hud.BODY_FAMILY, 15, 22, 400, 0.02, False),          # a maths line
    "expr_strong": (hud.BODY_FAMILY, 15, 22, 600, 0.02, False),   # ... the answer
    "headline": (hud.DISPLAY_FAMILY, 32, 38, 500, 0.0, False),    # private: a table's change, large
    "answer": (hud.DISPLAY_FAMILY, 32, 38, 500, 0.0, False),      # ... and a maths answer
}
# Part 7: the second screen's face (DESIGN-SPEC 6: the badge role 13 px, the label role 14 px, bodies Sora 16, titles
# Chakra 20; the rest as second.html sets them), read across a desk. Every key of _TYPE is here.
_SECOND_TYPE: dict[str, tuple[str, float, float, int, float, bool]] = {
    "title": (hud.DISPLAY_FAMILY, 20, 26, 500, 0.01, False),
    "badge": (hud.MONO_FAMILY, 13, 16, 500, 0.16, True),
    "body": (hud.BODY_FAMILY, 16, 24, 400, 0.0, False),
    "row": (hud.BODY_FAMILY, 17, 22, 400, 0.0, False),
    "strong": (hud.BODY_FAMILY, 16, 22, 600, 0.0, False),
    "small": (hud.BODY_FAMILY, 15, 20, 400, 0.0, False),
    "caption": (hud.BODY_FAMILY, 15, 20, 400, 0.0, False),
    "time": (hud.MONO_FAMILY, 15, 20, 500, 0.0, False),
    "axis": (hud.MONO_FAMILY, 13, 16, 500, 0.04, False),
    "meta": (hud.MONO_FAMILY, 13, 16, 500, 0.12, True),
    "pill": (hud.MONO_FAMILY, 13, 18, 600, 0.06, False),
    "chip": (hud.BODY_FAMILY, 17, 22, 400, 0.0, False),
    "kbd": (hud.MONO_FAMILY, 13, 16, 500, 0.08, True),
    "label": (hud.MONO_FAMILY, 14, 18, 500, 0.20, True),
    "cell": (hud.BODY_FAMILY, 15, 20, 400, 0.0, False),
    "cell_strong": (hud.BODY_FAMILY, 15, 20, 600, 0.0, False),
    "value": (hud.MONO_FAMILY, 14, 18, 500, 0.0, False),
    "delta": (hud.BODY_FAMILY, 15, 20, 400, 0.0, False),
    "delta_strong": (hud.BODY_FAMILY, 15, 20, 600, 0.0, False),
    "step": (hud.MONO_FAMILY, 13, 16, 500, 0.0, False),
    "expr": (hud.BODY_FAMILY, 18, 26, 400, 0.02, False),
    "expr_strong": (hud.BODY_FAMILY, 18, 26, 600, 0.02, False),
    "headline": (hud.DISPLAY_FAMILY, 52, 56, 500, 0.0, False),    # "2,900 › 3,400"
    "answer": (hud.BODY_FAMILY, 44, 52, 600, 0.01, False),        # "x = 1 or x = 3"
    # Part 7 fixes (review 7, paint 1): the headline's figures try one line at these sizes before they stack.
    "headline_48": (hud.DISPLAY_FAMILY, 48, 52, 500, 0.0, False),
    "headline_44": (hud.DISPLAY_FAMILY, 44, 48, 500, 0.0, False),
    "headline_40": (hud.DISPLAY_FAMILY, 40, 44, 500, 0.0, False),
    "headline_35": (hud.DISPLAY_FAMILY, 35, 40, 500, 0.0, False),   # about two thirds of 52
}
_TYPES_OF = {BOARD: _TYPE, SECOND: _SECOND_TYPE}
# The second face's headline sizes, largest first: "2,900 › 3,400" keeps one line at the first that fits its tile.
_HEADLINE_STEPS = ("headline", "headline_48", "headline_44", "headline_40", "headline_35")


@dataclass(frozen=True)
class _Face:
    """A face's room (px at scale 1): the tile's padding (from its outer edge), the kind's glyph and the gap after it,
    the header's least height and the gap under it, the title's least room beside the meta, a calendar row's time
    column at least, and a chart bar's track and the gap between bars."""

    pad_top: float
    pad_side: float
    pad_bottom: float
    glyph: float
    glyph_gap: float
    head_min: float
    head_gap: float
    title_min: float
    time_col: float
    track: float
    bar_gap: float


_FACES = {BOARD: _Face(PAD_TOP, PAD_SIDE, PAD_BOTTOM, GLYPH, 8, 24, HEAD_GAP, TITLE_MIN, 96, 11, 8),
          SECOND: _Face(21, 23, 21, 20, 10, 26, 14, 150, 62, 14, 12)}
# Second screen (OLED care, DESIGN-SPEC 6): its whites are capped at OLED_INK - the brightest ink a tile may use there.
_CAPPED = {QColor(holo.INK_1).rgb(): holo.OLED_INK, QColor(holo.FOCUS).rgb(): holo.OLED_INK,
           QColor(holo.AMBER_TX).rgb(): holo.AMBER_TX2}


def _color(value: Any, alpha: float | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


class _Type:
    """The fonts of one scale and face (QFont and QFontMetricsF per kind of words)."""

    def __init__(self, scale: float, screen: str = BOARD) -> None:
        self.scale = scale
        self.table = _TYPES_OF.get(screen, _TYPE)
        self.fonts: dict[str, QFont] = {}
        self.metrics: dict[str, QFontMetricsF] = {}
        for key, (family, px, _line, weight, tracking, _caps) in self.table.items():
            size = max(1, round(px * scale))
            font = QFont()
            font.setFamilies([family, *hud._FALLBACKS.get(family, ())])   # noqa: SLF001 - hud's own fallbacks
            font.setPixelSize(size)
            font.setWeight(QFont.Weight(weight))
            if tracking:
                font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, size * tracking)
            if family == hud.MONO_FAMILY:
                try:
                    font.setFeature(QFont.Tag("tnum"), 1)
                except (AttributeError, TypeError, ValueError):
                    pass
            self.fonts[key] = font
            self.metrics[key] = QFontMetricsF(font)

    def line(self, key: str) -> float:
        return self.table[key][2] * self.scale

    def words(self, key: str, text: str) -> str:
        return text.upper() if self.table[key][5] else text

    def width(self, key: str, text: str) -> float:
        return self.metrics[key].horizontalAdvance(self.words(key, text))


_TYPES: dict[tuple[float, str], _Type] = {}


def _type(scale: float, screen: str = BOARD) -> _Type:
    screen = screen if screen in _TYPES_OF else BOARD
    key = (round(float(scale), 4), screen)
    found = _TYPES.get(key)
    if found is None:
        found = _TYPES[key] = _Type(key[0], screen)
    return found


# --------------------------------------------------------------------------
# The layout: ops in the tile's own px
# --------------------------------------------------------------------------

@dataclass
class Op:
    """One thing a tile draws. ``rect`` is its box in the tile's px; a text op's ``lines`` fill it top-down."""

    kind: str                                  # text, glyph, box, rule, hairline, image, dot
    rect: QRectF
    color: QColor | None = None
    lines: tuple[str, ...] = ()
    font: str = ""
    align: str = "left"
    strike: bool = False
    name: str = ""                             # a glyph's name
    fill: QColor | None = None
    radius: float = 0.0
    dashed: bool = False
    image: QImage | None = None
    level: float = 1.0                         # an image's brightness

    def moved(self, dy: float) -> Op:
        copy = Op(**{name: getattr(self, name) for name in self.__dataclass_fields__})
        copy.rect = QRectF(self.rect).translated(0, dy)
        return copy


@dataclass
class _Block:
    height: float
    ops: list[Op] = field(default_factory=list)
    row: bool = False                          # a row the painter may leave out ("+N MORE")
    more: int = 0                              # the frame's own "+N MORE" row: its N
    bar: bool = False                          # the calendar's day bar (the first to leave when the tile is short)
    paragraph: Any = None                      # (rebuild(max_lines) -> _Block, lines) of a paragraph that may shorten
    fit: Any = None                            # fit(max_height) -> _Block: a picture that may shrink
    drop: bool = False                         # it may leave when the tile is still short (the second face's delta)


@dataclass
class TileLayout:
    """Where everything of one tile goes: ``ops`` in the tile's px, its ``height``, ``more`` rows the painter left out
    (the last row then says "+N MORE"), ``clipped`` when even its header did not fit the height it was given."""

    width: float
    height: float
    ops: list[Op]
    more: int = 0
    clipped: bool = False

    def texts(self) -> list[tuple[QRectF, str]]:
        """Every text op's box and words (one entry per op, its lines joined by "\\n")."""
        return [(op.rect, "\n".join(op.lines)) for op in self.ops if op.kind == "text"]

    def words(self) -> str:
        return "\n".join(text for _rect, text in self.texts())


def _wrap(text: str, kind: str, width: float, types: _Type) -> list[str]:
    """``text`` in lines at most ``width`` px wide, broken at spaces only; a word longer than a line is broken inside it
    (never clipped). An empty text is one empty line."""
    metrics = types.metrics[kind]
    shown = types.words(kind, text)
    room = max(1.0, width)
    lines: list[str] = []
    for paragraph in shown.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            trial = f"{current} {word}" if current else word
            if metrics.horizontalAdvance(trial) <= room:
                current = trial
                continue
            if current:
                lines.append(current)
            while len(word) > 1 and metrics.horizontalAdvance(word) > room:
                low, high = 1, len(word) - 1          # the longest head of the word that fits (at least one char)
                while low < high:
                    middle = (low + high + 1) // 2
                    if metrics.horizontalAdvance(word[:middle]) <= room:
                        low = middle
                    else:
                        high = middle - 1
                if low > 1 and word[low - 1] in "-‐‑":
                    low -= 1                          # never right after a hyphen (DESIGN-SPEC 6): before it
                lines.append(word[:low])
                word = word[low:]
            current = word
        lines.append(current)
    return lines


def _shorten(lines: list[str], most: int, kind: str, width: float, types: _Type) -> list[str]:
    """At most ``most`` lines; the last one kept ends with "…" when lines were left out."""
    if most <= 0:
        return []
    if len(lines) <= most:
        return lines
    kept = lines[:most]
    last = kept[-1].rstrip()
    while last and types.metrics[kind].horizontalAdvance(last + ELLIPSIS) > width:
        last = last[:-1].rstrip()
    kept[-1] = last + ELLIPSIS
    return kept


def _text_op(text: str, kind: str, x: float, y: float, width: float, color: Any, types: _Type, *,
             align: str = "left", strike: bool = False, most: int = 0) -> Op:
    lines = _wrap(text, kind, width, types)
    if most:
        lines = _shorten(lines, most, kind, width, types)
    widest = min(max(0.0, width), max((types.metrics[kind].horizontalAdvance(line) for line in lines), default=0.0))
    if align == "right":
        x += max(0.0, width) - widest
    return Op("text", QRectF(x, y, widest, len(lines) * types.line(kind)), _color(color), tuple(lines), kind,
              align=align, strike=strike)


class _Builder:
    """Lays out one tile of ``width`` px."""

    def __init__(self, frame: Mapping[str, Any], width: float, scale: float, private: bool,
                 picture: QImage | None, screen: str = BOARD) -> None:
        self.frame = frame
        self.s = scale
        self.screen = screen if screen in _FACES else BOARD
        self.second = self.screen == SECOND
        self.face = _FACES[self.screen]
        self.t = _type(scale, self.screen)
        self.private = private
        self.picture = picture
        self.width = max(40.0 * scale, float(width))
        self.left = self.face.pad_side * scale
        self.inner = max(1.0, self.width - 2 * self.face.pad_side * scale)
        self.right = self.left + self.inner
        self.waits = bool(frame.get("waits")) and frame.get("status", "ok") == "ok"
        self.status = str(frame.get("status", "ok"))
        self.headline = True              # private: a table's changed row as its headline (False: its rows) ...
        self.headlined = False            # ... and the body built shows one

    def px(self, value: float) -> float:
        return value * self.s

    # ---- header ----------------------------------------------------------------------------------------------------

    def header(self) -> _Block:
        frame, t, face = self.frame, self.t, self.face
        ops: list[Op] = []
        glyph = GLYPHS.get(str(frame.get("kind", "")), "read_all")
        glyph_color = holo.AMBER if self.waits else holo.ALERT if self.status == "failed" else holo.HOLO_LO
        top = (t.line("title") - self.px(face.glyph)) / 2 if self.second else self.px(2)
        ops.append(Op("glyph", QRectF(self.left, top, self.px(face.glyph), self.px(face.glyph)), _color(glyph_color),
                      name=glyph))
        title_x = self.left + self.px(face.glyph + face.glyph_gap)
        title_room = self.right - title_x
        meta = str(frame.get("meta", ""))
        if frame.get("changed"):
            meta = f"{meta} · {CHANGED}" if meta else CHANGED
        pin = self.px(13 + 6) if frame.get("pinned") else 0.0
        meta_w = t.width("badge", meta) if meta else 0.0
        beside = bool(meta) and title_room - meta_w - pin - self.px(8) >= self.px(face.title_min)
        if self.second and meta:       # the second screen: beside it exactly when the title then keeps one line
            beside = t.width("title", str(frame.get("title", ""))) + self.px(8) + meta_w + pin <= title_room
        title_w = title_room - (meta_w + pin + self.px(8) if beside else 0.0)
        title_color = holo.AMBER_TX if self.waits else holo.INK_1
        title = _text_op(str(frame.get("title", "")), "title", title_x, 0.0, title_w, title_color, t,
                         most=TITLE_LINES if self.second else 0)      # the second screen: two lines at most ("…")
        ops.append(title)
        height = max(self.px(face.head_min), title.rect.height())
        meta_color = holo.AMBER_TX2 if self.waits else holo.INK_4
        if beside:
            top = (t.line("title") - t.line("badge")) / 2
            ops.append(_text_op(meta, "badge", self.right - meta_w, top, meta_w, meta_color, t))
            if pin:
                ops.append(Op("glyph", QRectF(self.right - meta_w - pin, top - self.px(0.5), self.px(13), self.px(13)),
                              _color(holo.INK_3), name="pin"))
        elif meta or pin:
            line = _text_op(meta, "badge", title_x, height + self.px(2), title_room - pin, meta_color, t, align="right")
            ops.append(line)
            if pin:
                ops.append(Op("glyph", QRectF(line.rect.left() - pin, line.rect.top(), self.px(13), self.px(13)),
                              _color(holo.INK_3), name="pin"))
            height = line.rect.bottom()
        return _Block(height + self.px(face.head_gap), ops)

    # ---- the body ----------------------------------------------------------------------------------------------------

    def body(self) -> list[_Block]:
        if self.status == "failed":
            return [self.failed()]
        if self.status == "empty":
            return [self.empty()]
        kind = str(self.frame.get("kind", ""))
        data = self.frame.get("body") or {}
        kinds = {"text": self.text, "calendar": self.calendar, "mail_thread": self.mail_thread,
                 "mail_list": self.mail_list, "todos": self.todos, "picture": self.picture_body,
                 "table": self.table, "chart": self.chart, "timeline": self.timeline, "maths": self.maths,
                 "number": self.number, "approvals": self.approvals, "agents": self.agents}
        blocks = kinds.get(kind, self.text)(data)
        if self.waits:
            blocks.append(self.waits_chip())
        return blocks

    def failed(self) -> _Block:
        t = self.t
        words = _text_op(str(self.frame.get("status_text", "")), "body", self.left + self.px(26), 0.0,
                         self.inner - self.px(26), holo.ALERT_TX2, t)
        glyph = Op("glyph", QRectF(self.left, self.px(2), self.px(16), self.px(16)), _color(holo.ALERT), name="warning")
        return _Block(max(words.rect.height(), self.px(20)), [glyph, words])

    def empty(self) -> _Block:
        t = self.t
        ring = self.px(28)
        x = self.left + ring + self.px(12)
        words = _text_op(str(self.frame.get("status_text", "")), "body", x, 0.0, self.right - x, holo.INK_1, t)
        ops = [words]
        bottom = words.rect.bottom()
        checked = str(self.frame.get("status_meta", ""))
        if checked:
            meta = _text_op(checked, "badge", x, bottom, self.right - x, holo.INK_3, t)
            ops.append(meta)
            bottom = meta.rect.bottom()
        height = max(bottom, ring) + self.px(4)
        top = (height - ring) / 2
        ops.append(Op("box", QRectF(self.left, top, ring, ring), _color(holo.LINE_STRONG), radius=ring / 2,
                      dashed=True))
        return _Block(height, ops)

    def more_block(self, words: str) -> _Block:
        op = _text_op(words, "badge", self.left, self.px(6), self.inner, holo.INK_3, self.t, align="right")
        found = _MORE_RE.fullmatch(words.strip())
        return _Block(op.rect.bottom(), [op], row=True, more=int(found.group(1)) if found else 0)

    def rule(self, y: float) -> Op:
        return Op("rule", QRectF(self.left, y, self.inner, 0.0), _color(holo.LINE_FAINT))

    def divider(self) -> list[Op]:
        """A list row's divider over it: a LINE_FAINT rule on the board; none on the second screen (no grid there)."""
        return [] if self.second else [self.rule(0.0)]

    def paragraph(self, text: str, color: Any, most: int = 0, top: float = 0.0) -> _Block:
        def build(lines_most: int) -> _Block:
            op = _text_op(text, "body", self.left, top, self.inner, color, self.t, most=lines_most)
            return _Block(op.rect.bottom(), [op], paragraph=(build, len(op.lines)))
        return build(most)

    def hidden(self) -> _Block:
        """Private (the second screen): the hidden glyph and "Text hidden on this screen" where a mail's text or a text
        frame's paragraph would be."""
        words = _text_op(HIDDEN, "body", self.left + self.px(22), 0.0, self.inner - self.px(22), holo.INK_3, self.t)
        glyph = Op("glyph", QRectF(self.left, self.px(2), self.px(16), self.px(16)), _color(holo.INK_3), name="hidden")
        return _Block(words.rect.bottom(), [glyph, words])

    # ---- the kinds -----------------------------------------------------------------------------------------------------

    def number(self, data: Mapping[str, Any]) -> list[_Block]:
        """A big number (the canvas, part 2) as a read-only tile: its figure, then what it was and is, as words."""
        said = " ".join(part for part in (str(data.get("figure", "")), str(data.get("text", ""))) if part)
        return self.text({"text": said})

    def count_line(self, words: str, color: Any) -> _Block:
        """Part 3B: a resting frame's count, one line of its own (never hidden: it says no name and no title)."""
        op = _text_op(words, "row", self.left, 0.0, self.inner, color, self.t)
        return _Block(op.rect.bottom() + self.px(6), [op])

    def approvals(self, data: Mapping[str, Any]) -> list[_Block]:
        """Part 3B: "waits for your OK" as a plain tile - how many wait, then (not on the second screen) the first
        cards: each title with its kind label as its meta."""
        count = _whole(data.get("count"))
        blocks = [self.count_line(_plural(WAIT_WORDS, count), holo.AMBER_TX)]
        rows = [] if self.private else [row for row in (data.get("rows") or []) if isinstance(row, Mapping)]
        listed = [{"lead": "", "text": str(row.get("title", "")), "meta": str(row.get("kind", ""))} for row in rows]
        if count > len(listed) and listed:
            listed.append({"lead": "", "text": "", "meta": MORE.format(count=count - len(listed))})
        return blocks + self.text({"rows": listed})

    def agents(self, data: Mapping[str, Any]) -> list[_Block]:
        """Part 3B: the Agents frame as a plain tile - how many agents, how many of their proposals wait, then (not on
        the second screen: no name there) each agent's name with its state."""
        rows = [row for row in (data.get("rows") or []) if isinstance(row, Mapping)]
        total = len(rows) + _whole(data.get("more"))
        waits = _whole(data.get("waits"))
        words = _plural(AGENTS_WORDS, total) + (" · " + _plural(PROPOSAL_WORDS, waits) if waits else "")
        blocks = [self.count_line(words, holo.AMBER_TX if waits else holo.INK_2)]
        if self.private:
            return blocks
        listed = [{"lead": "", "text": str(row.get("name", "")),
                   "meta": AGENT_WORDS.get(str(row.get("state", "")), "")
                   + (" · " + _plural(PROPOSAL_WORDS, _whole(row.get("waits"))) if _whole(row.get("waits")) else "")}
                  for row in rows]
        if _whole(data.get("more")):
            listed.append({"lead": "", "text": "", "meta": MORE.format(count=_whole(data.get("more")))})
        return blocks + self.text({"rows": listed})

    def text(self, data: Mapping[str, Any]) -> list[_Block]:
        blocks: list[_Block] = []
        words = str(data.get("text", ""))
        if words and self.private:          # a web research answer is the line Jarvis speaks: never drawn privately
            blocks.append(self.hidden())
        elif words:
            blocks.append(self.paragraph(words, holo.INK_2))
        rows = list(data.get("rows") or [])
        leads = any(row.get("lead") for row in rows)
        t = self.t
        lead_w = min(self.inner * 0.3, max((t.width("time", row.get("lead", "")) for row in rows), default=0.0)) \
            if leads else 0.0
        narrow = self.inner <= self.px(NARROW)           # a row's meta then goes under its words
        meta_w = 0.0 if narrow else min(self.inner * 0.45,
                                        max((t.width("small", row.get("meta", "")) for row in rows), default=0.0))
        for index, row in enumerate(rows):
            if not row.get("lead") and not row.get("text"):
                blocks.append(self.more_block(str(row.get("meta", ""))))
                continue
            divided = index > 0 or bool(words)
            top = self.px(5) if divided else 0.0
            ops: list[Op] = self.divider() if divided else []
            x = self.left
            columns: list[Op] = []
            if leads:
                columns.append(_text_op(str(row.get("lead", "")), "time", x, top, lead_w, holo.INK_3, t))
                x += lead_w + self.px(10)
            meta = str(row.get("meta", ""))
            text_w = self.right - x - (meta_w + self.px(10) if meta_w else 0.0)
            columns.append(_text_op(str(row.get("text", "")), "row", x, top, text_w, holo.INK_1, t))
            if meta and narrow:
                columns.append(_text_op(meta, "small", x, columns[-1].rect.bottom(), self.right - x, holo.INK_4, t))
            elif meta:
                columns.append(_text_op(meta, "small", self.right - meta_w, top + self.px(1), meta_w, holo.INK_4, t,
                                        align="right"))
            ops += columns
            blocks.append(_Block(max(op.rect.bottom() for op in columns) + self.px(5), ops, row=True))
        return blocks

    def calendar(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        blocks: list[_Block] = []
        first, last = int(data.get("first", 0)), int(data.get("last", 0))
        if last > first:
            blocks.append(self.day_bar(data, first, last))
        rows = list(data.get("rows") or [])
        now = str(data.get("now", ""))
        if self.second:          # the second screen: what is still to come, free time too (the day bar's tick says
            # where now is), each row at its start. Past rows go, and so does free time that began before now: the
            # free row before the day's now or next row (its start would read as a time gone by).
            soon = next((index for index, row in enumerate(rows) if row.get("state") in ("now", "next")), -1)
            rows = [_start_only(row) for index, row in enumerate(rows)
                    if row.get("state") != "past" and not (row.get("state") == "free" and index < soon)]
            now = ""
        horizon = -1
        if now:
            horizon = next((index for index, row in enumerate(rows) if row.get("state") != "past"), len(rows))
        narrow = self.second or self.inner <= self.px(NARROW)     # the second screen: a row's meta under its title
        time_w = max((t.width("time", row.get("time", "")) for row in rows), default=0.0)
        least = self.face.time_col if self.second else 86 if narrow else 96
        time_w = max(self.px(least), min(time_w, self.inner * (0.45 if narrow else 0.4)))
        meta_w = 0.0 if narrow else min(self.inner * 0.35, max((max(t.width("small", row.get("meta", "")),
                                                                     t.width("meta", row.get("meta", "")))
                                                                 for row in rows), default=0.0))
        gap = self.px(10 if self.second or not narrow else 8)
        divided = False
        for index, row in enumerate(rows):
            if index == horizon:
                blocks.append(self.horizon(now))
                divided = False
            if not row.get("time") and not row.get("title"):
                blocks.append(self.more_block(str(row.get("meta", ""))))
                continue
            state = str(row.get("state", ""))
            free = state == "free"
            boxed = free and not self.second          # the board's dashed free row; the second screen's is plain
            top = self.px(5) + (self.px(4) if boxed else 0.0)
            ops: list[Op] = self.divider() if divided and not free else []
            time_color = (holo.HOLO_HI if state in ("next", "now", "today") else holo.INK_4 if state == "past"
                          else holo.INK_4 if free and self.second else holo.INK_3)
            title_color = (holo.INK_4 if free and self.second else holo.INK_3 if state in ("free", "struck")
                           else holo.INK_4 if state == "past" else holo.HOLO_HI if state == "new" else holo.INK_1)
            columns = [_text_op(str(row.get("time", "")), "time", self.left, top + self.px(1), time_w, time_color, t)]
            x = self.left + time_w + gap
            meta = str(row.get("meta", ""))
            title_w = self.right - x - (meta_w + gap if meta_w else 0.0)
            columns.append(_text_op(str(row.get("title", "")), "row", x, top, title_w, title_color, t,
                                    strike=state == "struck"))
            if meta:
                loud = state in ("next", "now", "new", "struck")
                kind = "meta" if loud else "small"
                color = ((holo.HOLO if self.second else holo.HOLO_HI) if state in ("next", "now", "new")
                         else holo.INK_4)
                if narrow:
                    under = columns[-1].rect.bottom() + (self.px(2) if self.second else 0.0)
                    columns.append(_text_op(meta, kind, x, under, self.right - x, color, t))
                else:
                    columns.append(_text_op(meta, kind, self.right - meta_w, top + self.px(2 if loud else 1), meta_w,
                                            color, t, align="right"))
            ops += columns
            bottom = max(op.rect.bottom() for op in columns) + self.px(5)
            if boxed:
                ops.insert(0, Op("box", QRectF(self.left - self.px(7), self.px(4) + self.px(1), self.inner + self.px(14),
                                               bottom - self.px(5) - self.px(1)), _color(holo.INK_3, 0.35),
                                 fill=_color(holo.HOLO, 0.02), radius=self.px(4), dashed=True))
                bottom += self.px(4)
            blocks.append(_Block(bottom, ops, row=True))
            divided = not free
        if horizon == len(rows) and now:
            blocks.append(self.horizon(now))
        return blocks

    def day_bar(self, data: Mapping[str, Any], first: int, last: int) -> _Block:
        t = self.t
        length = float(last - first)
        ops: list[Op] = [Op("box", QRectF(self.left, 0.0, self.inner, self.px(12)), _color(holo.LINE_FAINT),
                            fill=_color(holo.HOLO, 0.05), radius=self.px(3))]

        def x_of(minute: float) -> float:
            return self.left + self.inner * max(0.0, min(1.0, (minute - first) / length))

        for span in data.get("spans") or []:
            start, end = x_of(span.get("start", first)), x_of(span.get("end", first))
            if span.get("kind") == "free":
                ops.append(Op("box", QRectF(start, self.px(1), max(1.0, end - start), self.px(10)),
                              _color(holo.INK_3, 0.55), radius=self.px(2), dashed=True))
            else:
                fill = _color(holo.HOLO) if span.get("kind") == "next" else _color(holo.HOLO_LO, 0.5)
                ops.append(Op("box", QRectF(start, self.px(2), max(1.0, end - start), self.px(8)), None, fill=fill,
                              radius=self.px(2)))
        now_min = int(data.get("now_min", -1))
        if now_min >= 0:
            x = x_of(now_min)
            ops.append(Op("box", QRectF(x - self.px(1), 0.0, self.px(2), self.px(12)), None, fill=_color(holo.HOLO_HI)))
        if self.second:                          # the second screen: the bar alone (2 px over it, 14 under it)
            return _Block(self.px(2 + 12 + 14), [op.moved(self.px(2)) for op in ops], bar=True)
        top = self.px(12 + 3)
        reach = -1.0
        for tick in data.get("ticks") or []:
            words = str(tick.get("text", ""))
            width = t.width("axis", words)
            at = x_of(tick.get("at", first))
            x = at if at <= self.left + 0.5 else at - width if at >= self.right - 0.5 else at - width / 2
            x = max(self.left, min(self.right - width, x))
            if x < reach + self.px(4):          # a label that would touch the one before it is left out
                continue
            ops.append(_text_op(words, "axis", x, top, width + 0.5, holo.INK_4, t))
            reach = x + width
        return _Block(top + t.line("axis") + self.px(8), ops, bar=True)

    def horizon(self, words: str) -> _Block:
        t = self.t
        pill_w = t.width("pill", words) + self.px(10)
        height = t.line("pill")
        ops = [Op("box", QRectF(self.left, 0.0, pill_w, height), None, fill=_color(holo.HOLO_HI), radius=self.px(3)),
               _text_op(words, "pill", self.left + self.px(5), 0.0, pill_w - self.px(10) + 0.5, holo.HOLO_INK, t),
               Op("hairline", QRectF(self.left + pill_w, height / 2, max(0.0, self.inner - pill_w), 0.0),
                  _color(holo.HOLO_HI))]
        return _Block(height + self.px(2), ops)

    def mail_thread(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        blocks: list[_Block] = []
        sender, when = str(data.get("sender", "")), str(data.get("time", ""))
        sender_color = holo.AMBER_TX if self.waits else holo.INK_1
        if sender or when:
            ops: list[Op] = []
            bottom = 0.0
            when_w = t.width("badge", when) if when else 0.0
            if sender:
                beside = when and t.width("strong", sender) + self.px(10) + when_w <= self.inner
                room = self.inner - (when_w + self.px(10) if beside else 0.0)
                name = _text_op(sender, "strong", self.left, 0.0, room, sender_color, t)
                ops.append(name)
                bottom = name.rect.bottom()
                if when:
                    if beside:
                        x, y = name.rect.right() + self.px(10), self.px(3)
                    else:
                        x, y = self.left, bottom
                    stamp = _text_op(when, "badge", x, y, self.right - x, holo.AMBER_TX2 if self.waits else holo.INK_4, t)
                    ops.append(stamp)
                    bottom = max(bottom, stamp.rect.bottom())
            else:
                stamp = _text_op(when, "badge", self.left, 0.0, self.inner, holo.INK_4, t)
                ops.append(stamp)
                bottom = stamp.rect.bottom()
            blocks.append(_Block(bottom + self.px(3), ops))
        text = str(data.get("text", ""))
        if self.private and text:
            blocks.append(self.hidden())
        elif text:
            blocks.append(self.paragraph(text, holo.AMBER_TX2 if self.waits else holo.INK_2, TEXT_LINES))
        return blocks

    def mail_list(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        blocks: list[_Block] = []
        rows = list(data.get("rows") or [])
        side_w = min(self.inner * 0.45, max((t.width("badge", _side(row)) for row in rows), default=0.0))
        first = True
        for row in rows:
            if not row.get("sender") and not row.get("subject"):
                blocks.append(self.more_block(str(row.get("meta", ""))))
                continue
            top = 0.0 if first else self.px(6)
            ops: list[Op] = [] if first else self.divider()
            x = self.left + self.px(6 + 10)
            side = _side(row)
            sender_w = self.right - x - (side_w + self.px(10) if side else 0.0)
            name = _text_op(str(row.get("sender", "")), "strong", x, top, sender_w, holo.INK_1, t)
            ops.append(name)
            bottom = name.rect.bottom()
            if side:
                stamp = _text_op(side, "badge", self.right - side_w, top + self.px(3), side_w, holo.INK_4, t,
                                 align="right")
                ops.append(stamp)
                bottom = max(bottom, stamp.rect.bottom())
            if row.get("unread"):
                ops.append(Op("dot", QRectF(self.left, top + self.px(6), self.px(6), self.px(6)), None,
                              fill=_color(holo.HOLO_HI), radius=self.px(3)))
            subject = str(row.get("subject", ""))
            if subject:
                line = _text_op(subject, "caption", x, bottom + self.px(1), self.right - x, holo.INK_2, t)
                ops.append(line)
                bottom = line.rect.bottom()
            blocks.append(_Block(bottom + self.px(6), ops, row=True))
            first = False
        return blocks

    def todos(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        blocks: list[_Block] = []
        rows = list(data.get("rows") or [])
        meta_w = min(self.inner * 0.4, max((t.width("badge", _todo_word(row)) for row in rows), default=0.0))
        first = True
        for row in rows:
            if not row.get("text"):
                blocks.append(self.more_block(str(row.get("meta", ""))))
                continue
            state = str(row.get("state", "open"))
            top = 0.0 if first else self.px(6)
            ops: list[Op] = [] if first else self.divider()
            x = self.left + self.px(16 + 10)
            word = _todo_word(row)
            text_w = self.right - x - (meta_w + self.px(10) if word else 0.0)
            color = holo.AMBER_TX if state == "waits" else holo.INK_4 if state == "done" else holo.INK_1
            text = _text_op(str(row.get("text", "")), "row", x, top, text_w, color, t, strike=state == "done")
            ops.append(text)
            line = t.line("row")
            box = QRectF(self.left + self.px(1), top + (line - self.px(14)) / 2, self.px(14), self.px(14))
            if state == "waits":
                ops.append(Op("glyph", box.adjusted(0, 0, -self.px(1), -self.px(1)), _color(holo.AMBER), name="diamond"))
            elif state == "done":
                ops.append(Op("box", box, None, fill=_color(holo.OK), radius=self.px(3)))
                ops.append(Op("glyph", box.adjusted(self.px(1.5), self.px(1.5), -self.px(1.5), -self.px(1.5)),
                              _color(holo.OK_INK), name="check"))
            else:
                ops.append(Op("box", box.adjusted(self.px(0.75), self.px(0.75), -self.px(0.75), -self.px(0.75)),
                              _color(holo.INK_3), radius=self.px(3)))
            bottom = text.rect.bottom()
            if word:
                color = holo.AMBER if state == "waits" else holo.OK if state == "done" else holo.INK_3
                stamp = _text_op(word, "badge", self.right - meta_w, top + (line - t.line("badge")) / 2, meta_w,
                                 color, t, align="right")
                ops.append(stamp)
                bottom = max(bottom, stamp.rect.bottom())
            blocks.append(_Block(bottom + self.px(6), ops, row=True))
            first = False
        return blocks

    def picture_body(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        blocks: list[_Block] = []
        image = self.picture
        if image is not None and not image.isNull() and image.width() > 0 and image.height() > 0:
            level = PRIVATE_PICTURE_LEVEL if self.private else PICTURE_LEVEL

            def fit(most: float) -> _Block:      # as wide as the tile, at most 3/4 as tall, its shape kept
                width = self.inner
                height = width * image.height() / image.width()
                limit = max(self.px(24), min(self.inner * 0.75, most - self.px(8)))
                if height > limit:
                    height, width = limit, limit * image.width() / image.height()
                x = self.left + (self.inner - width) / 2
                return _Block(height + self.px(8), [Op("image", QRectF(x, 0.0, width, height), _color(holo.LINE_FAINT),
                                                       image=image, level=level, radius=self.px(4))], fit=fit)

            blocks.append(fit(self.inner))
        caption = str(data.get("caption", ""))
        only_w = t.width("badge", PICTURE_ONLY)
        host = _text_op(caption, "caption", self.left, 0.0, max(self.px(40), self.inner - only_w - self.px(10)),
                        holo.INK_2, t)
        ops = [host]
        if host.rect.width() + self.px(10) + only_w <= self.inner:
            ops.append(_text_op(PICTURE_ONLY, "badge", self.right - only_w, self.px(2), only_w, holo.INK_4, t))
            bottom = host.rect.bottom()
        else:
            only = _text_op(PICTURE_ONLY, "badge", self.left, host.rect.bottom() + self.px(2), self.inner, holo.INK_4, t)
            ops.append(only)
            bottom = only.rect.bottom()
        blocks.append(_Block(bottom, ops))
        return blocks

    # ---- part 6, Ask's typed frames ------------------------------------------------------------------------------------

    def delta(self, words: str, top: float, most: int = 0) -> _Block:
        """A changed figure's delta line: the delta glyph, its figure first in HOLO_HI 600, then the rest (INK_3);
        ``most`` lines at most (the last ending with "…"), 0: as many as it takes."""
        t = self.t
        indent = self.px(17)
        glyph = Op("glyph", QRectF(self.left, top + self.px(3), self.px(11), self.px(11)), _color(holo.HOLO_HI),
                   name="delta")
        head, _space, rest = words.partition(" ")
        ops = [glyph]
        if _FIGURE_RE.match(head):
            lead = _text_op(head, "delta_strong", self.left + indent, top, self.inner - indent, holo.HOLO_HI, t)
            ops.append(lead)
            x = lead.rect.right() + t.width("delta", " ")
            parts = rest.split(" ") if rest else []
            fit = 0                                   # the words that go on after the figure, on its line ...
            while fit < len(parts) and t.width("delta", " ".join(parts[:fit + 1])) <= self.right - x:
                fit += 1
            if most == 1 and fit < len(parts):        # (one line: as many of them as fit with "…")
                while fit and t.width("delta", " ".join(parts[:fit]) + ELLIPSIS) > self.right - x:
                    fit -= 1
                ops.append(_text_op(" ".join(parts[:fit]) + ELLIPSIS, "delta", x, top, self.right - x, holo.INK_3, t))
            else:
                if fit:
                    ops.append(_text_op(" ".join(parts[:fit]), "delta", x, top, self.right - x, holo.INK_3, t))
                if fit < len(parts):                  # ... and the rest under them, from the indent
                    ops.append(_text_op(" ".join(parts[fit:]), "delta", self.left + indent, lead.rect.bottom(),
                                        self.inner - indent, holo.INK_3, t, most=max(0, most - 1)))
        else:
            ops.append(_text_op(words, "delta", self.left + indent, top, self.inner - indent, holo.INK_3, t,
                                most=most))
        return _Block(max(op.rect.bottom() for op in ops), ops)

    def delta_paragraph(self, words: str, top: float) -> _Block:
        """The second face's headline delta: a paragraph _assemble may shorten to one line ("…") or leave out."""
        def build(most: int) -> _Block:
            block = self.delta(words, top, most)
            lines = round((block.height - top) / self.t.line("delta"))
            block.paragraph, block.drop = (build, max(1, lines)), True
            return block
        return build(0)

    def table(self, data: Mapping[str, Any]) -> list[_Block]:
        columns = [str(word) for word in data.get("columns") or []]
        rows = [[str(cell) for cell in row] for row in data.get("rows") or [] if isinstance(row, list)]
        changed = int(data.get("changed", -1)) if isinstance(data.get("changed"), int) else -1
        if self.private and self.headline and 0 <= changed < len(rows):
            headline = self.table_headline(columns, rows[changed], str(data.get("change", "")))
            if headline:
                self.headlined = True
                return headline
        t = self.t
        count = len(columns)
        if not count:
            return []
        naturals = []
        for index in range(count):
            widths = [t.width("label", columns[index])]
            widths += [t.width("cell_strong" if number == changed else "cell", row[index] if index < len(row) else "")
                       for number, row in enumerate(rows)]
            naturals.append(max(widths) + self.px(1))
        # Each column as wide as its widest words, the first taking what is left; when they do not fit, a narrower gap,
        # then every column cut to its share (its words then wrap at spaces, never cut).
        gap = self.px(10)
        if sum(naturals) + gap * (count - 1) > self.inner:
            gap = self.px(6)
        room = self.inner - gap * (count - 1)
        if sum(naturals) <= room:
            widths = [room - sum(naturals[1:]), *naturals[1:]]
        else:
            widths = [room * natural / sum(naturals) for natural in naturals]
        xs = [self.left]
        for width in widths[:-1]:
            xs.append(xs[-1] + width + gap)
        blocks: list[_Block] = []
        heads = [_text_op(word, "label", x, 0.0, width, holo.INK_4, t, align="left" if index == 0 else "right")
                 for index, (word, x, width) in enumerate(zip(columns, xs, widths))]
        blocks.append(_Block(max(op.rect.bottom() for op in heads) + self.px(6), heads))
        for number, row in enumerate(rows):
            hot = number == changed
            inset = self.px(6) if hot else 0.0
            ops: list[Op] = []
            cells: list[Op] = []
            for index, (x, width) in enumerate(zip(xs, widths)):
                cell = row[index] if index < len(row) else ""
                last = index == count - 1
                kind = "cell_strong" if hot and last else "cell"
                color = (holo.HOLO_HI if hot and last else holo.INK_1 if hot else
                         holo.INK_4 if _ZERO_RE.fullmatch(cell) else holo.INK_2)
                left = x + (inset if index == 0 else 0.0)
                room_here = width - (inset if index in (0, count - 1) else 0.0)
                cells.append(_text_op(cell, kind, left, self.px(4), max(1.0, room_here), color, t,
                                      align="left" if index == 0 else "right"))
            bottom = max(op.rect.bottom() for op in cells) + self.px(4)
            if hot:
                ops.append(Op("box", QRectF(self.left, 0.0, self.inner, bottom), None, fill=_color(holo.HOLO, 0.08)))
            ops.append(self.rule(0.0))
            ops += cells
            blocks.append(_Block(bottom, ops, row=True))
        change = str(data.get("change", ""))
        if 0 <= changed < len(rows) and change:
            blocks.append(self.delta(change, self.px(10)))
        source = str(data.get("source", ""))
        if source:
            line = _text_op(source, "badge", self.left, self.px(10), self.inner, holo.INK_4, t)
            blocks.append(_Block(line.rect.bottom(), [line]))
        return blocks

    def table_headline(self, columns: list[str], row: list[str], change: str) -> list[_Block]:
        """The second screen's table: the changed row's first words, then what it was and is now, large ("2,900 →
        3,400", the new figure in HOLO_HI), then its change; [] when its before and after figures can't be told
        without doubt (headline_figures): the rows show then. On the second face (part 7 fixes) the figures keep one
        line at the largest of _HEADLINE_STEPS that fits (their arrow with them) before they stack, and the change is a
        paragraph the tile may shorten to one line or leave out."""
        figures = headline_figures(columns, row)
        if figures is None:
            return []
        t = self.t
        blocks: list[_Block] = []
        if row[0]:
            label = _text_op(row[0], "body", self.left, 0.0, self.inner, holo.INK_2, t)
            blocks.append(_Block(label.rect.bottom() + self.px(22 if self.second else 6), [label]))
        before, after = figures
        kind = "headline"
        for size in _HEADLINE_STEPS if self.second else (kind,):
            kind = size
            share = t.table[kind][1] / t.table["headline"][1]
            arrow = self.px((50 if self.second else 22) * share)     # the second screen's arrow: 30 px, 10 either side
            mark_size = self.px((30 if self.second else 14) * share)
            width = t.width(kind, before) + arrow + t.width(kind, after)
            if width <= self.inner:
                break
        else:
            kind = "headline"                          # one line at none of them: one figure over the other
        old = _text_op(before, kind, self.left, 0.0, self.inner, holo.INK_1, t)
        if width <= self.inner:
            x = old.rect.right()
            mark = Op("glyph", QRectF(x + self.px((10 if self.second else 3) * share), (t.line(kind) - mark_size) / 2,
                                      mark_size, mark_size), _color(holo.INK_4 if self.second else holo.INK_3),
                      name="chevron")
            new = _text_op(after, kind, x + arrow, 0.0, self.right - x - arrow, holo.HOLO_HI, t)
            blocks.append(_Block(max(old.rect.bottom(), new.rect.bottom()), [old, mark, new]))
        else:
            new = _text_op(after, kind, self.left, old.rect.bottom(), self.inner, holo.HOLO_HI, t)
            blocks.append(_Block(new.rect.bottom(), [old, new]))
        if change and self.second:
            blocks.append(self.delta_paragraph(change, self.px(12)))
        elif change:
            blocks.append(self.delta(change, self.px(8)))
        return blocks

    def chart(self, data: Mapping[str, Any]) -> list[_Block]:
        t = self.t
        bars = [bar for bar in data.get("bars") or [] if isinstance(bar, Mapping)
                and type(bar.get("value")) in (int, float)]
        if not bars:
            return []
        changed = data.get("changed", -1) if isinstance(data.get("changed"), int) else -1
        was = data.get("was") if type(data.get("was")) in (int, float) and changed >= 0 else None
        values = [float(bar["value"]) for bar in bars] + ([float(was)] if was is not None else [])
        low, high = min(0.0, *values), max(0.0, *values)
        span = (high - low) or 1.0
        gap = self.px(10)
        label_w = min(self.inner * 0.4, max(t.width("small", str(bar.get("label", ""))) for bar in bars) + self.px(1))
        value_w = max(t.width("value", str(bar.get("text", ""))) for bar in bars) + self.px(1)
        track_x = self.left + label_w + gap
        track_w = max(self.px(32), self.right - value_w - gap - track_x)
        blocks: list[_Block] = []
        for index, bar in enumerate(bars):
            top = 0.0 if index == 0 else self.px(self.face.bar_gap)
            label = _text_op(str(bar.get("label", "")), "small", self.left, top, label_w, holo.INK_2, t)
            value = _text_op(str(bar.get("text", "")), "value", self.right - value_w, top + self.px(1), value_w,
                             holo.INK_1, t, align="right")
            middle = top + max(label.rect.height(), t.line("value")) / 2
            track = QRectF(track_x, middle - self.px(self.face.track) / 2, track_w, self.px(self.face.track))

            def x_of(number: float) -> float:
                return track.left() + track.width() * (number - low) / span

            start, end = sorted((x_of(0.0), x_of(float(bar["value"]))))
            hot = index == changed
            ops: list[Op] = [Op("box", track, None, fill=_color(holo.HOLO, 0.06), radius=self.px(2)), label, value]
            if end - start > 0.25:
                ops.append(Op("box", QRectF(start, track.top(), end - start, track.height()), None,
                              fill=_color(holo.HOLO) if hot else _color(holo.HOLO_LO, 0.6), radius=self.px(2)))
            if hot and was is not None:
                ghost = x_of(float(was))
                ops.append(Op("vrule", QRectF(ghost, track.top() - self.px(4), 0.0, track.height() + self.px(8)),
                              _color(holo.INK_2), dashed=True, radius=1.5 * self.s))
            blocks.append(_Block(max(label.rect.bottom(), value.rect.bottom(), track.bottom()), ops, row=True))
        change = str(data.get("change", ""))
        if changed >= 0 and change:
            blocks.append(self.delta(change, self.px(10)))
        return blocks

    def timeline(self, data: Mapping[str, Any]) -> list[_Block]:
        nodes = [node for node in data.get("nodes") or [] if isinstance(node, Mapping)]
        if not nodes:
            return []
        today = data.get("today", -1) if isinstance(data.get("today"), int) else -1
        if len(nodes) <= ROW_NODES and self.inner / len(nodes) >= self.px(ROW_NODE):
            return [self.timeline_row(nodes, today)]
        return self.timeline_rows(nodes, today)

    def dot(self, x: float, y: float, state: str) -> list[Op]:
        """A timeline node's dot (18 px) centred on (x, y): DONE a mint disc with its check, NEXT a cyan ring with a
        glow, any other an INK_3 ring."""
        size = self.px(18)
        rect = QRectF(x - size / 2, y - size / 2, size, size)
        if state == "done":
            return [Op("box", rect, None, fill=_color(holo.OK), radius=size / 2),
                    Op("glyph", rect.adjusted(self.px(3.5), self.px(3.5), -self.px(3.5), -self.px(3.5)),
                       _color(holo.OK_INK), name="check")]
        ring = _color(holo.HOLO) if state == "next" else _color(holo.INK_3)
        ops = [Op("box", rect.adjusted(-self.px(3), -self.px(3), self.px(3), self.px(3)), None,
                  fill=_color(holo.HOLO, 0.15), radius=size / 2 + self.px(3))] if state == "next" else []
        return ops + [Op("box", rect.adjusted(self.px(0.75), self.px(0.75), -self.px(0.75), -self.px(0.75)), ring,
                         fill=_color(holo.TILE), radius=size / 2)]

    def _state_color(self, state: str) -> str:
        return holo.OK if state == "done" else holo.HOLO_HI if state == "next" else holo.INK_4

    def timeline_row(self, nodes: list[Mapping[str, Any]], today: int) -> _Block:
        """Up to four nodes in a row: the rail, the dots, under each its when, words and state; TODAY a HOLO_HI line
        with its word."""
        t = self.t
        count = len(nodes)
        top = self.px(18)                      # room for TODAY's word over the rail
        y = top + self.px(9)
        column = self.inner / count
        centres = [self.left + self.px(9) if index == 0 else self.right - self.px(9) if index == count - 1
                   else self.left + column * (index + 0.5) for index in range(count)]
        ops: list[Op] = [Op("box", QRectF(self.left + self.px(9), y - self.px(0.75), max(0.0, self.inner - self.px(18)),
                                         self.px(1.5)), None, fill=_color(holo.LINE))]
        marker = -1.0
        if today >= 0:
            marker = (self.left if today == 0 else self.right if today >= count
                      else (centres[today - 1] + centres[today]) / 2)
            if marker > self.left + self.px(9):
                ops.append(Op("box", QRectF(self.left + self.px(9), y - self.px(0.75), marker - self.left - self.px(9),
                                            self.px(1.5)), None, fill=_color(holo.HOLO_LO)))
        bottom = y + self.px(9)
        for index, (node, x) in enumerate(zip(nodes, centres)):
            state = str(node.get("state", ""))
            ops += self.dot(x, y, state)
            align = "left" if index == 0 else "right" if index == count - 1 else "center"
            line_y = y + self.px(9) + self.px(7)
            for words, kind, color in ((str(node.get("when", "")), "badge", holo.INK_3),
                                       (str(node.get("words", "")), "caption",
                                        holo.INK_3 if state == "done" else holo.INK_1),
                                       (STATE_WORDS.get(state, ""), "badge", self._state_color(state))):
                if not words:
                    continue
                op = self._aligned(words, kind, x, line_y, column - self.px(6), align, color)
                ops.append(op)
                line_y = op.rect.bottom() + self.px(2)
            bottom = max(bottom, line_y)
        if today >= 0:
            ops.append(Op("vrule", QRectF(marker, top - self.px(12), 0.0, self.px(30)), _color(holo.HOLO_HI),
                          radius=1.5 * self.s))
            word_w = t.width("badge", TODAY_WORD)
            word_x = marker + self.px(5) if marker + self.px(5) + word_w <= self.right else marker - self.px(6) - word_w
            ops.append(_text_op(TODAY_WORD, "badge", word_x, top - self.px(13), word_w + 0.5, holo.HOLO_HI, t))
        return _Block(bottom, ops)

    def _aligned(self, words: str, kind: str, x: float, y: float, width: float, align: str, color: Any) -> Op:
        """Words at a node: from its dot's left (the first), to its dot's right (the last), centred on it (the
        others); wrapped at spaces within ``width``."""
        if align == "left":
            return _text_op(words, kind, x - self.px(9), y, width, color, self.t)
        if align == "right":
            return _text_op(words, kind, x + self.px(9) - width, y, width, color, self.t, align="right")
        op = _text_op(words, kind, 0.0, y, width, color, self.t)
        op.rect = QRectF(x - op.rect.width() / 2, op.rect.top(), op.rect.width(), op.rect.height())
        return op

    def timeline_rows(self, nodes: list[Mapping[str, Any]], today: int) -> list[_Block]:
        """One node per row (a narrow tile, or more than four): the dot, its when over its words, its state at the
        right; TODAY a row of its own; the rail down the dots."""
        t = self.t
        blocks: list[_Block] = []
        count = len(nodes)
        state_w = max((t.width("badge", STATE_WORDS.get(str(node.get("state", "")), "")) for node in nodes),
                      default=0.0)
        x = self.left + self.px(18 + 10)
        for index, node in enumerate(nodes):
            if index == today:
                blocks.append(self.today_row(first=not blocks, rail=0 < index < count))
            state = str(node.get("state", ""))
            top = 0.0 if not blocks else self.px(8)
            words_w = self.right - x - (state_w + self.px(10) if state_w else 0.0)
            texts: list[Op] = []
            y = top
            for words, kind, color in ((str(node.get("when", "")), "badge", holo.INK_3),
                                       (str(node.get("words", "")), "caption",
                                        holo.INK_3 if state == "done" else holo.INK_1)):
                if words:
                    op = _text_op(words, kind, x, y, words_w, color, t)
                    texts.append(op)
                    y = op.rect.bottom()
            height = max(y - top, self.px(18))
            middle = top + height / 2
            ops: list[Op] = []
            rail_top = middle if index == 0 else 0.0           # the rail runs from the first dot to the last
            rail_bottom = middle if index == count - 1 else top + height
            if count > 1 and rail_bottom > rail_top:
                ops.append(Op("box", QRectF(self.left + self.px(8.25), rail_top, self.px(1.5), rail_bottom - rail_top),
                              None, fill=_color(holo.LINE)))
            ops += self.dot(self.left + self.px(9), middle, state) + texts
            word = STATE_WORDS.get(state, "")
            if word:
                ops.append(_text_op(word, "badge", self.right - state_w, middle - t.line("badge") / 2, state_w,
                                    self._state_color(state), t, align="right"))
            blocks.append(_Block(top + height, ops, row=True))
        if today >= count:
            blocks.append(self.today_row(first=False, rail=False))
        return blocks

    def today_row(self, *, first: bool, rail: bool) -> _Block:
        """TODAY as a row of its own: a short HOLO_HI line and its word (the rail through it when it stands between
        two nodes)."""
        t = self.t
        top = 0.0 if first else self.px(8)
        height = t.line("badge")
        ops: list[Op] = []
        if rail:
            ops.append(Op("box", QRectF(self.left + self.px(8.25), 0.0, self.px(1.5), top + height), None,
                          fill=_color(holo.LINE)))
        ops.append(Op("box", QRectF(self.left, top + height / 2 - self.px(0.75), self.px(18), self.px(1.5)), None,
                      fill=_color(holo.HOLO_HI)))
        ops.append(_text_op(TODAY_WORD, "badge", self.left + self.px(28), top, self.inner - self.px(28), holo.HOLO_HI,
                            t))
        return _Block(top + height, ops)

    def maths(self, data: Mapping[str, Any]) -> list[_Block]:
        from .ask_frames import maths_text, working_line

        steps = [step for step in data.get("steps") or [] if isinstance(step, Mapping)]
        answer = data.get("answer") if isinstance(data.get("answer"), Mapping) else {}
        if self.private:                          # the second screen: the answer large, the working in one line
            t = self.t
            top = self.px(6) if self.second else 0.0
            big = _text_op(maths_text(answer.get("expr")), "answer", self.left, top, self.inner, holo.HOLO_HI, t)
            blocks = [_Block(big.rect.bottom(), [big])]
            line = working_line(data)
            if line:
                said = _text_op(line, "body", self.left, self.px(12 if self.second else 8), self.inner,
                                holo.INK_3 if self.second else holo.HOLO_LO, t)
                blocks.append(_Block(said.rect.bottom(), [said]))
            return blocks
        narrow = self.width - self.px(2 * PAD_SIDE + 2) < self.px(ROW_FROM)     # the page's content box (and border)
        number_w = self.t.width("step", str(len(steps) + 1)) + self.px(1)
        name_w = 0.0 if narrow else min(self.inner * 0.35, max((self.t.width("badge", str(step.get("name", "")))
                                                                for step in [*steps, answer]), default=0.0))
        blocks: list[_Block] = []
        for index, step in enumerate([*steps, answer]):
            blocks.append(self.maths_row(index + 1, step, maths_text(step.get("expr")), number_w, name_w, narrow,
                                         answer=index == len(steps), first=index == 0))
        return blocks

    def maths_row(self, number: int, step: Mapping[str, Any], line: str, number_w: float, name_w: float, narrow: bool,
                  *, answer: bool, first: bool) -> _Block:
        """One numbered step: its number, its name (above its line when narrow) and its line; the answer boxed in
        HOLO_HI."""
        t = self.t
        top = 0.0 if first else self.px(7)
        gap = self.px(10)
        name = str(step.get("name", ""))
        ops: list[Op] = []
        x = self.left + number_w + gap
        y = top
        if name and narrow:
            label = _text_op(name, "badge", x, y, self.right - x, holo.INK_3, t)
            ops.append(label)
            y = label.rect.bottom() + self.px(3)
        elif name_w:
            ops.append(_text_op(name, "badge", x, y + (t.line("expr") - t.line("badge")) / 2, name_w, holo.INK_3, t))
        if name_w and not narrow:
            x += name_w + gap
        if answer:                                # the page's box: 2 px over and under its words, 8 px either side
            pad = self.px(8)
            words = _text_op(line, "expr_strong", x, y + self.px(2), self.right - x - pad, holo.HOLO_HI, t)
            box = QRectF(x - pad, y, words.rect.width() + 2 * pad, words.rect.height() + self.px(4))
            ops.append(Op("box", box, _color(holo.LINE_STRONG), fill=_color(holo.HOLO, 0.09), radius=self.px(4)))
            bottom = box.bottom()
        else:
            words = _text_op(line, "expr", x, y, self.right - x, holo.INK_1, t)
            bottom = words.rect.bottom()
        ops.append(words)
        number_y = top if name and narrow else y + (t.line("expr") - t.line("step")) / 2
        ops.append(_text_op(str(number), "step", self.left, number_y, number_w, holo.INK_4, t))
        return _Block(bottom + (self.px(5) if narrow else 0.0), ops, row=not answer)

    def waits_chip(self) -> _Block:
        t = self.t
        words = str(self.frame.get("waits", ""))
        top = self.px(10)
        if self.private:          # the second screen: the words, and where to answer (never there)
            line = _text_op(words, "chip", self.left + self.px(20), top, self.inner - self.px(20), holo.AMBER_TX, t)
            dia = Op("glyph", QRectF(self.left, top + self.px(2), self.px(12), self.px(12)), _color(holo.AMBER),
                     name="diamond")
            where = _text_op(ON_MAIN, "small", self.left + self.px(20), line.rect.bottom() + self.px(4),
                             self.inner - self.px(20), holo.INK_3, t)
            return _Block(where.rect.bottom(), [dia, line, where])
        key_w = t.width("kbd", KEY_CAP) + self.px(10)
        fixed = self.px(11) + self.px(12) + self.px(8) + self.px(8) + self.px(12) + self.px(8) + key_w + self.px(8)
        room = max(1.0, self.inner - fixed)
        line = _text_op(words, "chip", 0.0, 0.0, room, holo.AMBER_TX, t)
        chip_h = max(self.px(30), line.rect.height() + self.px(14))
        chip_w = min(self.inner, fixed + line.rect.width())
        x = self.left
        ops = [Op("box", QRectF(x, top, chip_w, chip_h), _color(holo.AMBER), fill=_color(holo.AMBER, 0.10),
                  radius=min(self.px(15), chip_h / 2))]
        cx = x + self.px(11)
        mid = top + chip_h / 2
        ops.append(Op("glyph", QRectF(cx, mid - self.px(6), self.px(12), self.px(12)), _color(holo.AMBER),
                      name="diamond"))
        cx += self.px(12 + 8)
        ops.append(line.moved(0.0))
        ops[-1].rect = QRectF(cx, mid - line.rect.height() / 2, line.rect.width(), line.rect.height())
        cx += line.rect.width() + self.px(8)
        ops.append(Op("glyph", QRectF(cx, mid - self.px(6), self.px(12), self.px(12)), _color(holo.AMBER_TX2),
                      name="chevron"))
        cx += self.px(12 + 8)
        key_h = t.line("kbd") + self.px(2)
        ops.append(Op("box", QRectF(cx, mid - key_h / 2, key_w, key_h), _color(holo.AMBER_LINE),
                      radius=self.px(holo.RADIUS_BADGE)))
        ops.append(_text_op(KEY_CAP, "kbd", cx + self.px(5), mid - t.line("kbd") / 2, key_w - self.px(10) + 0.5,
                            holo.AMBER_TX2, t))
        return _Block(top + chip_h, ops)


def _whole(value: Any) -> int:
    return value if type(value) is int and value > 0 else 0


def _plural(words: tuple[str, str], count: int) -> str:
    return (words[0] if count == 1 else words[1]).format(count=count)


def headline_figures(columns: Sequence[Any], row: Sequence[Any]) -> tuple[str, str] | None:
    """A changed table row's before and after figures, chosen by column (part 6 fixes, review 6 board 4): the two
    columns just before its change column - the first after the row's label headed as a change ("Change", "Change %",
    "Δ", "Diff" ...) or whose cell in this row carries its sign ("+500", "−200") - when both are plain figures ("2,900",
    "17%"); with no change column, the row's only two plain figures after its label. None when that is not without
    doubt (a change column too near the label, a figure that is not plain, three figures and no change column): the
    tile shows the rows then. An unsigned change cell ("500", "17%") under a Change header is never taken for a figure."""
    cells = [str(cell).strip() for cell in row]
    heads = [str(word) for word in columns]
    change = next((index for index in range(1, len(cells))
                   if (index < len(heads) and _CHANGE_HEAD_RE.search(heads[index]))
                   or _SIGNED_RE.fullmatch(cells[index])), None)
    if change is not None:
        if change < 3:
            return None
        before, after = cells[change - 2], cells[change - 1]
        if _PLAIN_NUMBER_RE.fullmatch(before) and _PLAIN_NUMBER_RE.fullmatch(after):
            return before, after
        return None
    figures = [cell for cell in cells[1:] if _PLAIN_NUMBER_RE.fullmatch(cell)]
    return (figures[0], figures[1]) if len(figures) == 2 else None


# A row's time range as board.frames writes it (pictures.time_range): "10:00-10:30" (24-hour), "10:00-10:30 AM" (one
# meridiem, after the end), "11:30 AM-12:30 PM" (across noon or midnight: each its own).
_RANGE_RE = re.compile(r"^(.*?\d{1,2}:\d{2})(?:\s*([AP]M))?"          # the start (its own meridiem) ...
                       r"\s*[-–‒—]\s*\d{1,2}:\d{2}(?:\s*([AP]M))?",      # ... the end (the range's meridiem)
                       re.IGNORECASE)


def _start_only(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """A calendar row with its time's start alone, its meridiem kept (the second screen): "10:00-10:30" -> "10:00",
    "10:00-10:30 AM" -> "10:00 AM", "1:00-2:00 PM" -> "1:00 PM", "11:30 AM-12:30 PM" -> "11:30 AM"; a time that is
    not a range ("All day", "2:00 PM") as it is."""
    found = _RANGE_RE.match(str(row.get("time", "")))
    if found is None:
        return row
    start, own, after = found.groups()
    meridiem = (own or after or "").upper()
    copy = dict(row)
    copy["time"] = f"{start} {meridiem}" if meridiem else start
    return copy


def _side(row: Mapping[str, Any]) -> str:
    """A mail row's right side: its meta (WAITING / OWED) and its time."""
    parts = [str(row.get("meta", "")), str(row.get("time", ""))]
    return " · ".join(part for part in parts if part)


def _todo_word(row: Mapping[str, Any]) -> str:
    state = row.get("state")
    return WAITS_WORD if state == "waits" else DONE_WORD if state == "done" else str(row.get("meta", ""))


def _assemble(builder: _Builder, blocks: list[_Block], height: float | None) -> TileLayout:
    s = builder.s
    top, bottom_pad = builder.face.pad_top * s, builder.face.pad_bottom * s
    head = blocks[0]
    body = blocks[1:]
    dropped = 0

    def total(items: list[_Block]) -> float:
        return top + head.height + sum(block.height for block in items) + bottom_pad

    if height is not None and total(body) > height + 0.01:
        # 1. The calendar's day bar leaves (its rows say more); 2. rows leave from the last one up, and one "+N MORE"
        # row (more = -1: the painter's own) stands where they were; 3. a paragraph keeps its first lines ("…");
        # 4. a picture shrinks; 5. a block that may leave does (the second face's headline delta), the last first.
        body = [block for block in body if not block.bar]
        while total(body) > height + 0.01:
            rows = [index for index, block in enumerate(body) if block.row and block.more >= 0]
            if not rows:
                break
            index = rows[-1]
            removed = body.pop(index)
            dropped += removed.more if removed.more > 0 else 1        # the frame's own "+N MORE" row counts its N
            body = [block for block in body if block.more != -1]      # (it always stood after ``index``)
            marker = builder.more_block(MORE.format(count=dropped))
            marker.more = -1
            body.insert(index, marker)
        for index, block in enumerate(body):
            if block.paragraph is None:
                continue
            build, count = block.paragraph
            while count > 1 and total(body) > height + 0.01:
                count -= 1
                body[index] = build(count)
        for index, block in enumerate(body):
            over = total(body) - height
            if block.fit is not None and over > 0.01:
                body[index] = block.fit(block.height - over)
        while total(body) > height + 0.01:
            leaving = [index for index, block in enumerate(body) if block.drop]
            if not leaving:
                break
            body.pop(leaving[-1])
    ops: list[Op] = []
    y = top
    for block in [head, *body]:
        ops += [op.moved(y) for op in block.ops]
        y += block.height
    natural = y + bottom_pad
    final = natural if height is None else float(height)
    if builder.second:
        ops = [_capped(op) for op in ops]
    return TileLayout(builder.width, final, ops, more=dropped, clipped=natural > final + 0.5)


def cap_ink(color: QColor | None) -> QColor | None:
    """OLED care (the second screen, DESIGN-SPEC 6): ``color`` with its white capped - INK_1 and FOCUS as OLED_INK,
    AMBER_TX as AMBER_TX2 (their alpha kept); any other colour brighter than OLED_INK scaled down to it."""
    if color is None:
        return None
    found = _CAPPED.get(QColor(color).rgb())
    if found is not None:
        capped = QColor(found)
        capped.setAlphaF(color.alphaF())
        return capped
    cap = _luma(QColor(holo.OLED_INK))
    luma = _luma(color)
    if luma <= cap + 0.5:
        return color
    k = cap / luma
    return QColor.fromRgbF(color.redF() * k, color.greenF() * k, color.blueF() * k, color.alphaF())


def _luma(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def _capped(op: Op) -> Op:
    """An op as the second screen draws it: its whites capped (cap_ink), and nothing filled with a tile's ground (a
    timeline's dot is true black there)."""
    copy = op.moved(0.0)
    copy.color = cap_ink(op.color)
    fill = op.fill
    if fill is not None and fill.rgb() == QColor(holo.TILE).rgb():
        fill = QColor(holo.OLED_BLACK)
    copy.fill = cap_ink(fill)
    return copy


def tile_layout(frame: Mapping[str, Any], width: float, height: float | None = None, *, scale: float = 1.0,
                private: bool = False, picture: QImage | None = None, screen: str = BOARD) -> TileLayout:
    """See the module doc."""
    scale = max(0.25, float(scale or 1.0))
    builder = _Builder(frame, width, scale, bool(private), picture, screen)
    layout = _assemble(builder, [builder.header(), *builder.body()], height)
    if layout.clipped and builder.second and builder.headlined:
        builder.headline = False                 # its header and headline do not fit even so: the table's rows
        layout = _assemble(builder, [builder.header(), *builder.body()], height)
    return layout


def tile_height(frame: Mapping[str, Any], width: float, *, scale: float = 1.0, private: bool = False,
                picture: QImage | None = None, screen: str = BOARD) -> float:
    """The natural height of ``frame``'s tile ``width`` px wide."""
    return tile_layout(frame, width, None, scale=scale, private=private, picture=picture, screen=screen).height


# --------------------------------------------------------------------------
# Painting
# --------------------------------------------------------------------------

def _outline(frame: Mapping[str, Any], screen: str = BOARD) -> tuple[QColor, QColor, float]:
    """(border, ground, radius) of a tile: amber and rounder when it waits, coral when it failed (DESIGN-SPEC 3.4). The
    second screen's outlines are LINE (cyan 0.20) and amber at 0.55, as second.html draws them on true black."""
    status = frame.get("status", "ok")
    second = screen == SECOND
    if frame.get("waits") and status == "ok":
        return (_color(holo.AMBER, 0.55) if second else _color(holo.AMBER_LINE), _color(holo.TILE_AMBER),
                float(holo.RADIUS_TILE_AMBER))
    if status == "failed":
        return _color(holo.TILE_STYLES[holo.TILE_ERROR].border), _color(holo.TILE_ALERT), float(holo.RADIUS_FRAME)
    return (_color(holo.LINE) if second else _color(holo.FRAME_EDGE)), _color(holo.TILE), float(holo.RADIUS_FRAME)


def paint_tile(painter: QPainter, rect: QRectF, frame: Mapping[str, Any], *, scale: float = 1.0, private: bool = False,
               picture: QImage | None = None, ground: bool = False, screen: str = BOARD) -> TileLayout:
    """Paint one read-only tile into ``rect`` (see the module doc); returns its layout (what went where)."""
    rect = QRectF(rect)
    layout = tile_layout(frame, rect.width(), rect.height(), scale=scale, private=private, picture=picture,
                         screen=screen)
    types = _type(max(0.25, float(scale or 1.0)), screen)
    border, fill, radius = _outline(frame, screen)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        path = QPainterPath()
        radius *= types.scale
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if ground:
            painter.fillPath(path, fill)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setClipPath(path)                   # nothing is ever drawn past the tile
        painter.translate(rect.topLeft())
        for op in layout.ops:
            _paint_op(painter, op, types)
    finally:
        painter.restore()
    return layout


def _paint_op(painter: QPainter, op: Op, types: _Type) -> None:
    rect = op.rect
    if op.kind == "text":
        font = types.fonts[op.font]
        metrics = types.metrics[op.font]
        line = types.line(op.font)
        painter.setFont(font)
        painter.setPen(op.color or QColor(holo.INK_1))
        for index, words in enumerate(op.lines):
            advance = metrics.horizontalAdvance(words)
            x = rect.right() - advance if op.align == "right" else rect.left()
            baseline = rect.top() + index * line + (line - metrics.height()) / 2 + metrics.ascent()
            painter.drawText(QPointF(x, baseline), words)
            if op.strike and words:
                y = baseline - metrics.xHeight() / 2
                painter.setPen(QPen(_color(holo.INK_4, 0.7), max(1.0, types.scale)))
                painter.drawLine(QPointF(x, y), QPointF(x + advance, y))
                painter.setPen(op.color or QColor(holo.INK_1))
    elif op.kind == "glyph":
        holo.paint_icon(painter, rect, op.name, op.color or QColor(holo.INK_1))
    elif op.kind in ("box", "dot"):
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5) if op.color is not None else rect, op.radius, op.radius)
        if op.fill is not None:
            painter.fillPath(path, op.fill)
        if op.color is not None:
            pen = QPen(op.color, 1.0)
            if op.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
    elif op.kind == "rule":
        painter.setPen(QPen(op.color or _color(holo.LINE_FAINT), 1.0))
        painter.drawLine(QPointF(rect.left(), rect.top() + 0.5), QPointF(rect.right(), rect.top() + 0.5))
    elif op.kind == "vrule":                       # part 6: a chart's ghost tick (dashed), a timeline's TODAY or rail
        pen = QPen(op.color or _color(holo.INK_2), max(1.0, op.radius or 1.0))
        if op.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
    elif op.kind == "hairline":
        gradient = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
        gradient.setColorAt(0.0, op.color or QColor(holo.HOLO_HI))
        gradient.setColorAt(1.0, _color(holo.HOLO_HI, 0.08))
        painter.fillRect(QRectF(rect.left(), rect.top() - 0.5, rect.width(), 1.0), gradient)
    elif op.kind == "image" and op.image is not None:
        path = QPainterPath()
        path.addRoundedRect(rect, op.radius, op.radius)
        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.drawImage(rect, op.image)
        painter.fillRect(rect, QColor(0, 0, 0, round(255 * (1.0 - op.level))))
        painter.restore()
        painter.setPen(QPen(op.color or _color(holo.LINE_FAINT), 1.0))
        painter.drawPath(path)


# --------------------------------------------------------------------------
# GlanceTiles
# --------------------------------------------------------------------------

GROUP_HEAD = 16                 # a group's header line (DESIGN-SPEC 3.1: its time and topic over a LINE_FAINT rule) ...
GROUP_HEAD_GAP = 12             # ... and the gap under it


def paint_group_head(painter: QPainter, rect: QRectF, time_text: str, topic: str, *, older: bool = False,
                     scale: float = 1.0) -> None:
    """A group's header in ``rect`` (one line): its time (badge role) and its topic (Sora 12.5), INK_3 (INK_4 when
    older), then a LINE_FAINT rule to the right edge (left out when there is no room for it: the words are never
    cut - the topic is at most 40 characters)."""
    types = _type(scale)
    color = QColor(holo.INK_4 if older else holo.INK_3)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = rect.left()
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for key, words in (("badge", time_text), ("caption", topic)):
            shown = types.words(key, words)
            if not shown:
                continue
            width = types.width(key, shown)
            painter.setFont(types.fonts[key])
            painter.setPen(color)
            painter.drawText(QRectF(x, rect.top(), width + 2, rect.height()), align, shown)
            x += width + 10 * scale
        if rect.right() - x >= 24 * scale:
            painter.fillRect(QRectF(x, round(rect.center().y()), rect.right() - x, 1.0), _color(holo.LINE_FAINT))
    finally:
        painter.restore()


class GlanceTiles(QWidget):
    """The read-only tiles of a list of frames, one under the other, each as tall as it needs (closed frames left out).
    ``pictures``: a frame id's picture; ``headers``: a group's header (time, topic, older) drawn above the frame it
    starts with (part 5a: the board's groups). Never takes the focus; a screen reader hears each tile's title."""

    def __init__(self, parent: QWidget | None = None, *, scale: float = 1.0, private: bool = False,
                 ground: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("glanceTiles")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._scale = max(0.25, float(scale or 1.0))
        self._private = bool(private)
        self._ground = bool(ground)
        self._frames: tuple[Mapping[str, Any], ...] = ()
        self._pictures: dict[str, QImage] = {}
        self._headers: dict[str, tuple[str, str, bool]] = {}
        self._heights: dict[int, tuple[int, ...]] = {}

    def set_frames(self, frames: Sequence[Mapping[str, Any]], pictures: Mapping[str, QImage] | None = None, *,
                   headers: Mapping[str, tuple[str, str, bool]] | None = None) -> None:
        self._frames = tuple(frame for frame in frames if isinstance(frame, Mapping) and not frame.get("closed"))
        self._pictures = {key: value for key, value in (pictures or {}).items() if isinstance(value, QImage)}
        self._headers = {str(key): (str(value[0]), str(value[1]), bool(value[2]))
                         for key, value in (headers or {}).items() if isinstance(value, tuple) and len(value) == 3}
        self._heights.clear()
        self.setAccessibleName("Read-only tiles")
        self.setAccessibleDescription("; ".join(str(frame.get("title", "")) for frame in self._frames))
        self.setMinimumHeight(self.heightForWidth(max(1, self.width())))
        self.updateGeometry()
        self.update()

    def frames(self) -> tuple[Mapping[str, Any], ...]:
        return self._frames

    def headers(self) -> dict[str, tuple[str, str, bool]]:
        """The groups' headers, by the frame each one is drawn above."""
        return dict(self._headers)

    def _head(self, frame: Mapping[str, Any]) -> float:
        """The room a group's header takes above ``frame`` (0 when none starts there)."""
        return (GROUP_HEAD + GROUP_HEAD_GAP) * self._scale if str(frame.get("id", "")) in self._headers else 0.0

    def _tile_heights(self, width: int) -> tuple[int, ...]:
        found = self._heights.get(width)
        if found is None:
            found = tuple(int(-(-tile_height(frame, width, scale=self._scale, private=self._private,
                                             picture=self._pictures.get(str(frame.get("id", "")))) // 1))
                          for frame in self._frames)
            self._heights = {width: found}
        return found

    def tile_rects(self) -> list[QRectF]:
        """Where each tile goes, in this widget's px (tests and the screen harness read them)."""
        width = max(1, self.width())
        rects: list[QRectF] = []
        y = 0.0
        for frame, height in zip(self._frames, self._tile_heights(width)):
            y += self._head(frame)
            rects.append(QRectF(0.0, y, float(width), float(height)))
            y += height + GAP * self._scale
        return rects

    def head_rects(self) -> dict[str, QRectF]:
        """Where each group's header goes (by the frame it is drawn above), in this widget's px."""
        width = max(1, self.width())
        heads: dict[str, QRectF] = {}
        for frame, rect in zip(self._frames, self.tile_rects()):
            key = str(frame.get("id", ""))
            if key in self._headers:
                top = rect.top() - (GROUP_HEAD + GROUP_HEAD_GAP) * self._scale
                heads[key] = QRectF(0.0, top, float(width), GROUP_HEAD * self._scale)
        return heads

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        heights = self._tile_heights(max(1, int(width)))
        if not heights:
            return 0
        heads = sum(self._head(frame) for frame in self._frames)
        return int(-(-(sum(heights) + heads + GAP * self._scale * (len(heights) - 1)) // 1))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = max(1, self.width())
        return QSize(width, self.heightForWidth(width))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        wanted = self.heightForWidth(max(1, self.width()))
        if self.minimumHeight() != wanted:
            self.setMinimumHeight(wanted)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            heads = self.head_rects()
            for frame, rect in zip(self._frames, self.tile_rects()):
                key = str(frame.get("id", ""))
                if key in heads:
                    time_text, topic, older = self._headers[key]
                    paint_group_head(painter, heads[key], time_text, topic, older=older, scale=self._scale)
                paint_tile(painter, rect, frame, scale=self._scale, private=self._private,
                           picture=self._pictures.get(key), ground=self._ground)
        finally:
            painter.end()


__all__ = ["BOARD", "CHANGED", "GAP", "GLYPHS", "GROUP_HEAD", "GlanceTiles", "HIDDEN", "MORE", "ON_MAIN", "Op",
           "PICTURE_ONLY", "SECOND", "TileLayout", "cap_ink", "headline_figures", "paint_group_head", "paint_tile",
           "tile_height", "tile_layout"]
