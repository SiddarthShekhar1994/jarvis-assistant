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
        the meta and the headline rows; ``private`` (the second screen) hides a mail's text and says "Text hidden on
        this screen", and draws a page picture at 62 % instead of 80 %. Nothing in a tile is clickable
    GlanceTiles(QWidget)       stacks the tiles of a list of frames (set_frames), each as tall as it needs; closed
                               frames are left out. Never takes the focus

``scale`` multiplies every size (the second screen's type: 1.333 at 2560x1440); the painter's device pixel ratio does the
rest. Frames' words are drawn as they came (already cleaned and capped by the board's builders and protocol), as text.
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
HIDDEN = "Text hidden on this screen"
ON_MAIN = "On the main screen, Ctrl+3"
PICTURE_ONLY = "Picture only"
WAITS_WORD, DONE_WORD = "Waits", "Done"
KEY_CAP = "Ctrl 3"
CHANGED = "Changed"
MORE = "+{count} MORE"
ELLIPSIS = "…"
GLYPHS = {"text": "read_all", "calendar": "calendar", "mail_thread": "mail", "mail_list": "inbox", "todos": "todos",
          "picture": "image"}
_MORE_RE = re.compile(r"\+(\d+) MORE", re.IGNORECASE)

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
}


def _color(value: Any, alpha: float | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


class _Type:
    """The fonts of one scale (QFont and QFontMetricsF per kind of words)."""

    def __init__(self, scale: float) -> None:
        self.scale = scale
        self.fonts: dict[str, QFont] = {}
        self.metrics: dict[str, QFontMetricsF] = {}
        for key, (family, px, _line, weight, tracking, _caps) in _TYPE.items():
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
        return _TYPE[key][2] * self.scale

    def words(self, key: str, text: str) -> str:
        return text.upper() if _TYPE[key][5] else text

    def width(self, key: str, text: str) -> float:
        return self.metrics[key].horizontalAdvance(self.words(key, text))


_TYPES: dict[float, _Type] = {}


def _type(scale: float) -> _Type:
    key = round(float(scale), 4)
    found = _TYPES.get(key)
    if found is None:
        found = _TYPES[key] = _Type(key)
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
                 picture: QImage | None) -> None:
        self.frame = frame
        self.s = scale
        self.t = _type(scale)
        self.private = private
        self.picture = picture
        self.width = max(40.0 * scale, float(width))
        self.left = PAD_SIDE * scale
        self.inner = max(1.0, self.width - 2 * PAD_SIDE * scale)
        self.right = self.left + self.inner
        self.waits = bool(frame.get("waits")) and frame.get("status", "ok") == "ok"
        self.status = str(frame.get("status", "ok"))

    def px(self, value: float) -> float:
        return value * self.s

    # ---- header ----------------------------------------------------------------------------------------------------

    def header(self) -> _Block:
        frame, t = self.frame, self.t
        ops: list[Op] = []
        glyph = GLYPHS.get(str(frame.get("kind", "")), "read_all")
        glyph_color = holo.AMBER if self.waits else holo.ALERT if self.status == "failed" else holo.HOLO_LO
        ops.append(Op("glyph", QRectF(self.left, self.px(2), self.px(GLYPH), self.px(GLYPH)), _color(glyph_color),
                      name=glyph))
        title_x = self.left + self.px(GLYPH + 8)
        title_room = self.right - title_x
        meta = str(frame.get("meta", ""))
        if frame.get("changed"):
            meta = f"{meta} · {CHANGED}" if meta else CHANGED
        pin = self.px(13 + 6) if frame.get("pinned") else 0.0
        meta_w = t.width("badge", meta) if meta else 0.0
        beside = bool(meta) and title_room - meta_w - pin - self.px(8) >= self.px(TITLE_MIN)
        title_w = title_room - (meta_w + pin + self.px(8) if beside else 0.0)
        title_color = holo.AMBER_TX if self.waits else holo.INK_1
        title = _text_op(str(frame.get("title", "")), "title", title_x, 0.0, title_w, title_color, t)
        ops.append(title)
        height = max(self.px(24), title.rect.height())
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
        return _Block(height + self.px(HEAD_GAP), ops)

    # ---- the body ----------------------------------------------------------------------------------------------------

    def body(self) -> list[_Block]:
        if self.status == "failed":
            return [self.failed()]
        if self.status == "empty":
            return [self.empty()]
        kind = str(self.frame.get("kind", ""))
        data = self.frame.get("body") or {}
        kinds = {"text": self.text, "calendar": self.calendar, "mail_thread": self.mail_thread,
                 "mail_list": self.mail_list, "todos": self.todos, "picture": self.picture_body}
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

    def paragraph(self, text: str, color: Any, most: int = 0, top: float = 0.0) -> _Block:
        def build(lines_most: int) -> _Block:
            op = _text_op(text, "body", self.left, top, self.inner, color, self.t, most=lines_most)
            return _Block(op.rect.bottom(), [op], paragraph=(build, len(op.lines)))
        return build(most)

    # ---- the kinds -----------------------------------------------------------------------------------------------------

    def text(self, data: Mapping[str, Any]) -> list[_Block]:
        blocks: list[_Block] = []
        words = str(data.get("text", ""))
        if words:
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
            ops: list[Op] = [self.rule(0.0)] if divided else []
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
        horizon = -1
        if now:
            horizon = next((index for index, row in enumerate(rows) if row.get("state") != "past"), len(rows))
        narrow = self.inner <= self.px(NARROW)
        time_w = max((t.width("time", row.get("time", "")) for row in rows), default=0.0)
        time_w = max(self.px(86 if narrow else 96), min(time_w, self.inner * (0.45 if narrow else 0.4)))
        meta_w = 0.0 if narrow else min(self.inner * 0.35, max((max(t.width("small", row.get("meta", "")),
                                                                     t.width("meta", row.get("meta", "")))
                                                                 for row in rows), default=0.0))
        gap = self.px(8 if narrow else 10)
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
            top = self.px(5) + (self.px(4) if free else 0.0)
            ops: list[Op] = [self.rule(0.0)] if divided and not free else []
            time_color = (holo.HOLO_HI if state in ("next", "now", "today") else holo.INK_4 if state == "past"
                          else holo.INK_3)
            title_color = (holo.INK_3 if state in ("free", "struck") else holo.INK_4 if state == "past"
                           else holo.HOLO_HI if state == "new" else holo.INK_1)
            columns = [_text_op(str(row.get("time", "")), "time", self.left, top + self.px(1), time_w, time_color, t)]
            x = self.left + time_w + gap
            meta = str(row.get("meta", ""))
            title_w = self.right - x - (meta_w + gap if meta_w else 0.0)
            columns.append(_text_op(str(row.get("title", "")), "row", x, top, title_w, title_color, t,
                                    strike=state == "struck"))
            if meta:
                loud = state in ("next", "now", "new", "struck")
                kind = "meta" if loud else "small"
                color = holo.HOLO_HI if state in ("next", "now", "new") else holo.INK_4
                if narrow:
                    columns.append(_text_op(meta, kind, x, columns[-1].rect.bottom(), self.right - x, color, t))
                else:
                    columns.append(_text_op(meta, kind, self.right - meta_w, top + self.px(2 if loud else 1), meta_w,
                                            color, t, align="right"))
            ops += columns
            bottom = max(op.rect.bottom() for op in columns) + self.px(5)
            if free:
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
            words = _text_op(HIDDEN, "body", self.left + self.px(22), 0.0, self.inner - self.px(22), holo.INK_3, t)
            glyph = Op("glyph", QRectF(self.left, self.px(2), self.px(16), self.px(16)), _color(holo.INK_3),
                       name="hidden")
            blocks.append(_Block(words.rect.bottom(), [glyph, words]))
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
            ops: list[Op] = [] if first else [self.rule(0.0)]
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
            ops: list[Op] = [] if first else [self.rule(0.0)]
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


def _side(row: Mapping[str, Any]) -> str:
    """A mail row's right side: its meta (WAITING / OWED) and its time."""
    parts = [str(row.get("meta", "")), str(row.get("time", ""))]
    return " · ".join(part for part in parts if part)


def _todo_word(row: Mapping[str, Any]) -> str:
    state = row.get("state")
    return WAITS_WORD if state == "waits" else DONE_WORD if state == "done" else str(row.get("meta", ""))


def _assemble(builder: _Builder, blocks: list[_Block], height: float | None) -> TileLayout:
    s = builder.s
    top, bottom_pad = PAD_TOP * s, PAD_BOTTOM * s
    head = blocks[0]
    body = blocks[1:]
    dropped = 0

    def total(items: list[_Block]) -> float:
        return top + head.height + sum(block.height for block in items) + bottom_pad

    if height is not None and total(body) > height + 0.01:
        # 1. The calendar's day bar leaves (its rows say more); 2. rows leave from the last one up, and one "+N MORE"
        # row (more = -1: the painter's own) stands where they were; 3. a paragraph keeps its first lines ("…");
        # 4. a picture shrinks.
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
    ops: list[Op] = []
    y = top
    for block in [head, *body]:
        ops += [op.moved(y) for op in block.ops]
        y += block.height
    natural = y + bottom_pad
    final = natural if height is None else float(height)
    return TileLayout(builder.width, final, ops, more=dropped, clipped=natural > final + 0.5)


def tile_layout(frame: Mapping[str, Any], width: float, height: float | None = None, *, scale: float = 1.0,
                private: bool = False, picture: QImage | None = None) -> TileLayout:
    """See the module doc."""
    scale = max(0.25, float(scale or 1.0))
    builder = _Builder(frame, width, scale, bool(private), picture)
    return _assemble(builder, [builder.header(), *builder.body()], height)


def tile_height(frame: Mapping[str, Any], width: float, *, scale: float = 1.0, private: bool = False,
                picture: QImage | None = None) -> float:
    """The natural height of ``frame``'s tile ``width`` px wide."""
    return tile_layout(frame, width, None, scale=scale, private=private, picture=picture).height


# --------------------------------------------------------------------------
# Painting
# --------------------------------------------------------------------------

def _outline(frame: Mapping[str, Any]) -> tuple[QColor, QColor, float]:
    """(border, ground, radius) of a tile: amber and rounder when it waits, coral when it failed (DESIGN-SPEC 3.4)."""
    status = frame.get("status", "ok")
    if frame.get("waits") and status == "ok":
        return _color(holo.AMBER_LINE), _color(holo.TILE_AMBER), float(holo.RADIUS_TILE_AMBER)
    if status == "failed":
        return _color(holo.TILE_STYLES[holo.TILE_ERROR].border), _color(holo.TILE_ALERT), float(holo.RADIUS_FRAME)
    return _color(holo.FRAME_EDGE), _color(holo.TILE), float(holo.RADIUS_FRAME)


def paint_tile(painter: QPainter, rect: QRectF, frame: Mapping[str, Any], *, scale: float = 1.0, private: bool = False,
               picture: QImage | None = None, ground: bool = False) -> TileLayout:
    """Paint one read-only tile into ``rect`` (see the module doc); returns its layout (what went where)."""
    rect = QRectF(rect)
    layout = tile_layout(frame, rect.width(), rect.height(), scale=scale, private=private, picture=picture)
    types = _type(max(0.25, float(scale or 1.0)))
    border, fill, radius = _outline(frame)
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

class GlanceTiles(QWidget):
    """The read-only tiles of a list of frames, one under the other, each as tall as it needs (closed frames left out).
    ``pictures``: a frame id's picture. Never takes the focus; a screen reader hears each tile's title."""

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
        self._heights: dict[int, tuple[int, ...]] = {}

    def set_frames(self, frames: Sequence[Mapping[str, Any]], pictures: Mapping[str, QImage] | None = None) -> None:
        self._frames = tuple(frame for frame in frames if isinstance(frame, Mapping) and not frame.get("closed"))
        self._pictures = {key: value for key, value in (pictures or {}).items() if isinstance(value, QImage)}
        self._heights.clear()
        self.setAccessibleName("Read-only tiles")
        self.setAccessibleDescription("; ".join(str(frame.get("title", "")) for frame in self._frames))
        self.setMinimumHeight(self.heightForWidth(max(1, self.width())))
        self.updateGeometry()
        self.update()

    def frames(self) -> tuple[Mapping[str, Any], ...]:
        return self._frames

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
        for height in self._tile_heights(width):
            rects.append(QRectF(0.0, y, float(width), float(height)))
            y += height + GAP * self._scale
        return rects

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        heights = self._tile_heights(max(1, int(width)))
        if not heights:
            return 0
        return int(sum(heights) + GAP * self._scale * (len(heights) - 1))

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
            for frame, rect in zip(self._frames, self.tile_rects()):
                paint_tile(painter, rect, frame, scale=self._scale, private=self._private,
                           picture=self._pictures.get(str(frame.get("id", ""))), ground=self._ground)
        finally:
            painter.end()


__all__ = ["CHANGED", "GAP", "GLYPHS", "GlanceTiles", "HIDDEN", "MORE", "ON_MAIN", "Op", "PICTURE_ONLY", "TileLayout",
           "paint_tile", "tile_height", "tile_layout"]
