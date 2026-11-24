"""Home as a desk (the new look, part 5a; DESIGN-SPEC 2 and 5): when the board holds frames, Home shows the
conversation column, a 1 px seam and the board's one web view side by side instead of the stage. Native widgets only;
the board itself is board.view.BoardView (made and fed by desk_ui.DeskController).

    desk_geometry(width, height, *, folded_dock) -> DeskGeometry
        every rectangle of the desk in Home's px (DESIGN-SPEC 2.1-2.3): the column (clamp(round(0.37 x width), 300,
        400), 280 with the Dock folded), its ring head (136 px with the 120 px ring; 92 px ring under a 600 px body,
        the compact head with the Dock folded), the turns, the chips row and the ask bar, the seam and the board view
        (from seam + 1 to the right gutter; its frames start 15 px in, 448 px at 1120 x 700); ``drawer`` when the
        frames area would be under 360 px (the board then opens as today's 420 px drawer)
    DeskPage(QWidget)      the desk: ``head`` (RingHead), ``turns`` (DeskTurns), the chips row and the ask bar's
                           places (``chip_row``, ``ask_slot``: the app moves Home's own chip row and command bar in),
                           ``set_board(view)`` (the board's place: its geometry is set once per layout, never
                           animated), the seam it paints; ``apply_geometry()``. Part 5c: ``cue_line`` (CueLine: the
                           cue line's native part, from the column to the seam, never over the view - hidden while
                           big), ``set_big(on)`` (open big: the view spans both columns, ``geometry.big``, set once;
                           the covered ring head and turns take no focus and no click until it is back), ``veil``
                           (Veil: a still over the view while its geometry is set), ``column_image(rect)`` (the column
                           as it looks, never the view: open big's backdrop)
    CueLine, CuePath       the cue line's native part and where it runs (part 5c)
    Veil                   open big's still (part 5c)
    RingHead               the ring (an orb.OrbWidget, 120 px centred with hairlines to the column's edges, cyan 0.22
                           fading out; or 92 px at the left with the state word and "Earlier · 2 turns", which opens
                           today's transcript drawer)
    DeskTurns              the conversation's turns, fed from the same data as the Transcript (transcript_view's
                           entries): Jarvis's labelled "JARVIS · 9:41 AM" (body_sm, INK_3), the current one in Sora
                           16/25 INK_1 with its times in HOLO_HI and its waiting phrase in AMBER_HI, the owner's as
                           right-aligned bubbles; newest at the bottom under a 64 px top fade; a turn that put frames
                           up ends with "4 frames on the board" (``tokenClicked(entry id)``: the board scrolls to its
                           group); a turn's own link stays (``linkClicked(link id)``, as the Transcript's). Part 5c
                           fixes: ``set_read(entry, end)`` - while Jarvis says the current turn, its words before the
                           sentence being said read in INK_3 (the mock-up's ``.read``; desk_cue finds the sentence)
    BoardHint             empty Home's one line: "When I find something to show you, it appears beside our
                           conversation." and a CTRL B key cap
    RingFlight             the ring flying from the stage to the column head as the desk forms (420 ms ease-out-expo,
                           calm x1.6; never with motion off)

Every word from outside (the conversation's) is drawn as plain text (QTextLayout over the words as they are: no markup
is ever read). Logs: none here.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import shiboken6
from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from . import holo, hud
from . import orb as orb_mod

# --------------------------------------------------------------------------
# The geometry (DESIGN-SPEC 2.1 - 2.3)
# --------------------------------------------------------------------------

GUTTER = 24                 # the column's left gutter, the board's right one, and under the ask bar
COMPACT_GUTTER = 20         # ... with the Dock folded (the left and the bottom ones)
TOP_PAD = 12                # above the ring head (the compact column: 10)
COMPACT_TOP_PAD = 10
GAP = 20                    # between the column and the seam (the compact column: 16)
COMPACT_GAP = 16
SEAM_TOP = 16               # the seam runs from here to the bottom gutter
VIEW_TOP = 12               # the board view's top; it runs to the body's bottom (its bar is 32 px, its frames 44 down)
PAD = 15                    # the view's inner pad: the frames' left edge (a lit frame's brackets fit in it)
STRIP = 40                  # the index strip and its gap at the frames' right
COLUMN_MIN, COLUMN_MAX, COLUMN_SHARE = 300, 400, 0.37
COMPACT_COLUMN = 280
RING = orb_mod.COMPACT      # 120 px on the column head ...
RING_SMALL = 92             # ... 92 px in the compact head, and under a 600 px body
HEAD = 136                  # the ring head with the 120 px ring
HEAD_SMALL = RING_SMALL + 16    # ... with the 92 px one, centred
COMPACT_HEAD = 104          # ... the compact head (the ring at the left, the state word and "Earlier" beside it)
SHORT_BODY = 600            # a body under this tall gets the 92 px ring
CHIPS_ROW = 30              # the chips row, 12 px above and below it
CHIPS_MARGIN = 12
ASK_HEIGHT = 52             # the ask bar, the column's full width
DRAWER_BELOW = 360          # a frames area under this: the board opens as a drawer instead
HAIRLINE_GAP = 14           # between a hairline's end and the ring
BIG_GAP = 12                # open big: the wide view ends this far above the ask bar (DESIGN-SPEC 3.5)


@dataclass(frozen=True)
class DeskGeometry:
    """Every place of the desk, in Home's px (its top-left at 0, 0)."""

    compact: bool               # the folded Dock's column (the compact head, the last exchange only, one chip)
    column: QRect               # the conversation column
    head: QRect                 # its ring head
    ring: int                   # the ring's size there (120 or 92)
    turns: QRect                # the turns, between the head and the chips row
    chips: QRect                # the chips row (30 px; 12 above and below)
    ask: QRect                  # the ask bar (52 px, the column's full width)
    seam: QRect                 # the 1 px seam
    view: QRect                 # the board's one web view
    frames_x: int               # the frames' left edge (view + 15)
    frames_width: int           # the frames area (the view less its pad, the strip and its gap)
    drawer: bool                # the frames area would be under DRAWER_BELOW: the board as a drawer instead
    # open big's wide view (part 5c): the column's left edge to the board's right edge, the body's top to BIG_GAP above
    # the ask bar (the ask bar stays outside it, live)
    big: QRect = field(default_factory=QRect)


def desk_geometry(width: int, height: int, *, folded_dock: bool) -> DeskGeometry:
    """See the module doc. ``width`` x ``height``: Home's area (``_main``'s width, the body's height)."""
    width, height = max(0, int(width)), max(0, int(height))
    compact = bool(folded_dock)
    if compact:
        left, top, bottom, gap, column_w = COMPACT_GUTTER, COMPACT_TOP_PAD, COMPACT_GUTTER, COMPACT_GAP, COMPACT_COLUMN
        ring, head_h = RING_SMALL, COMPACT_HEAD
    else:
        left, top, bottom, gap = GUTTER, TOP_PAD, GUTTER, GAP
        column_w = max(COLUMN_MIN, min(COLUMN_MAX, round(COLUMN_SHARE * width)))
        short = height < SHORT_BODY
        ring, head_h = (RING_SMALL, HEAD_SMALL) if short else (RING, HEAD)
    column = QRect(left, top, column_w, max(0, height - top - bottom))
    ask = QRect(left, column.bottom() + 1 - ASK_HEIGHT, column_w, ASK_HEIGHT)
    chips = QRect(left, ask.top() - CHIPS_MARGIN - CHIPS_ROW, column_w, CHIPS_ROW)
    head = QRect(left, top, column_w, head_h)
    turns_top = head.bottom() + 1
    turns = QRect(left, turns_top, column_w, max(0, chips.top() - CHIPS_MARGIN - turns_top))
    seam_x = left + column_w + gap
    seam = QRect(seam_x, SEAM_TOP, 1, max(0, height - SEAM_TOP - bottom))
    view_x = seam_x + 1
    view = QRect(view_x, VIEW_TOP, max(0, width - view_x - GUTTER), max(0, height - VIEW_TOP))
    frames_width = view.width() - PAD - STRIP
    big = QRect(QPoint(left, 0), QPoint(view.right(), max(0, ask.top() - BIG_GAP - 1)))
    return DeskGeometry(compact=compact, column=column, head=head, ring=ring, turns=turns, chips=chips, ask=ask,
                        seam=seam, view=view, frames_x=view_x + PAD, frames_width=frames_width,
                        drawer=frames_width < DRAWER_BELOW, big=big)


# --------------------------------------------------------------------------
# Plain text, laid out and painted (no markup is ever read)
# --------------------------------------------------------------------------

def _font(family: str, px: float, weight: int = 400, tracking_em: float = 0.0, caps: bool = False) -> QFont:
    font = QFont()
    font.setFamilies([family, *hud._FALLBACKS.get(family, ())])  # noqa: SLF001 - hud's own fallbacks
    font.setPixelSize(max(1, round(px)))
    font.setWeight(QFont.Weight(weight))
    if tracking_em:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px * tracking_em)
    if caps:
        font.setCapitalization(QFont.Capitalization.AllUppercase)
    return font


def _label_font() -> QFont:
    """A turn's label ("JARVIS · 9:41 AM"): JetBrains Mono 10/12 500, 0.18em, caps."""
    return _font(hud.MONO_FAMILY, 10, 500, 0.18, caps=True)


def _body_font(px: float = 13) -> QFont:
    return _font(hud.BODY_FAMILY, px)


# A time in the current turn (home's caption rule: "10:30 AM", the hour in "at 10" ...) and the phrase that says
# something waits on the owner ("a reply waits for your OK"): worded, so never colour alone.
_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm)\b|\b\d{1,2}:\d{2}\b"
                   r"|(?:(?<=\bat )|(?<=\bby )|(?<=\bfrom )|(?<=\buntil )|(?<=\btill ))"
                   r"(?:[01]?\d|2[0-3]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)\b"
                   r"(?![:.]\d)(?!\s*(?:%|percent|minutes?|mins?|hours?|days?|weeks?|months?|years?|people|of)\b)",
                   re.IGNORECASE)
_WAITING = re.compile(r"\b(?:(?:a|an|the|one|two|three|four|five|\d+)\s+(?:[\w'-]+\s+){0,3}?)?"
                      r"(?:waits?|is waiting|are waiting|needs?)\s+(?:for\s+)?your\s+(?:OK|okay|approval|go-ahead)\b"
                      r"|\b(?:is |are )?waiting on Approvals\b",
                      re.IGNORECASE)


def highlights(text: str) -> list[tuple[int, int, str]]:
    """(start, length, colour) of the current turn's marks: its first three times in HOLO_HI and every phrase that
    says something waits on the owner in AMBER_HI (a waiting phrase wins where they meet)."""
    marks: list[tuple[int, int, str]] = []
    waits = [(match.start(), match.end()) for match in _WAITING.finditer(text)]
    for start, end in waits:
        marks.append((start, end - start, holo.AMBER_HI))
    for match in list(_TIME.finditer(text))[:3]:
        if any(start < match.end() and match.start() < end for start, end in waits):
            continue
        marks.append((match.start(), match.end() - match.start(), holo.HOLO_HI))
    return sorted(marks)


def _units_of(text: str) -> list[int] | None:
    """Each place of ``text`` (0 .. len) as Qt counts it - in UTF-16 code units, where a character outside the BMP (an
    emoji) is two -; None when every place is its own (no such character in it)."""
    if not text or max(text) <= "￿":
        return None
    places, at = [0], 0
    for char in text:
        at += 2 if ord(char) > 0xFFFF else 1
        places.append(at)
    return places


def _unit(places: list[int] | None, index: int) -> int:
    """Character place ``index`` of a paragraph as Qt's place in it (_units_of)."""
    if places is None:
        return index
    return places[max(0, min(int(index), len(places) - 1))]


class TextBlock:
    """Plain text laid out at a width (QTextLayout per paragraph, wrapped at words - inside a word only when it is
    longer than the line), ``line`` px a line; ``marks`` colour ranges of it. Measures and paints; never clips. Every
    place it is given or gives (marks, overlays, point_at) is a character place of ``text`` (Python's); Qt's own (UTF-16
    units) only inside."""

    def __init__(self, text: str, font: QFont, line: float, marks: Sequence[tuple[int, int, str]] = ()) -> None:
        self.text = str(text or "")
        self.font = font
        self.line = float(line)
        self.marks = tuple(marks)
        self._width = -1.0
        self._layouts: list[tuple[QTextLayout, int]] = []
        self._starts: list[int] = []     # each paragraph's first character in ``text``
        self._units: list[list[int] | None] = []   # each paragraph's places as Qt counts them (None: the same)
        self.lines = 0
        self.natural = 0.0           # the widest line laid out

    def layout(self, width: float) -> float:
        """Lay it out at ``width``; its height."""
        width = max(1.0, float(width))
        if width == self._width:
            return self.lines * self.line
        self._width = width
        self._layouts = []
        self._starts = []
        self._units = []
        self.lines = 0
        self.natural = 0.0
        offset = 0
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        metrics = QFontMetricsF(self.font)
        for paragraph in self.text.split("\n"):
            layout = QTextLayout(paragraph, self.font)
            layout.setTextOption(option)
            units = _units_of(paragraph)
            ranges = []
            for start, length, color in self.marks:
                lo, hi = max(start, offset), min(start + length, offset + len(paragraph))
                if hi > lo:
                    item = QTextLayout.FormatRange()
                    begin = _unit(units, lo - offset)
                    item.start, item.length = begin, _unit(units, hi - offset) - begin
                    char = QTextCharFormat()
                    char.setForeground(QColor(color))
                    item.format = char
                    ranges.append(item)
            if ranges:
                layout.setFormats(ranges)
            layout.beginLayout()
            count = 0
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(width)
                top = (self.lines + count) * self.line + (self.line - metrics.height()) / 2
                line.setPosition(QPointF(0.0, top))
                self.natural = max(self.natural, line.naturalTextWidth())
                count += 1
            layout.endLayout()
            if count == 0:
                count = 1
            self._layouts.append((layout, count))
            self._starts.append(offset)
            self._units.append(units)
            self.lines += count
            offset += len(paragraph) + 1
        return self.lines * self.line

    def point_at(self, offset: int, *, line_end: bool = False) -> QPointF | None:
        """Where the character place ``offset`` of ``text`` is once laid out: x at it (the end of the character before
        it), y the middle of its line; ``line_end``: x at the end of the words on that line instead, when more words
        follow it there (a line drawn from it never crosses them). None when there is no such place (lay it out
        first)."""
        if offset < 0 or offset > len(self.text) or not self._layouts:
            return None
        index = 0
        for position, start in enumerate(self._starts):
            if start <= offset:
                index = position
        layout, _count = self._layouts[index]
        local = min(max(0, offset - self._starts[index]), len(layout.text()))
        place = _unit(self._units[index], local)          # Qt's own place in the paragraph (an emoji is two)
        line = layout.lineForTextPosition(place)
        if not line.isValid() or (place > 0 and place == line.textStart() and line.lineNumber() > 0):
            # the place right after a line's last word is the next line's start: it belongs to the line before
            number = line.lineNumber() - 1 if line.isValid() else layout.lineCount() - 1
            if number < 0:
                return None
            line = layout.lineAt(number)
        found = line.cursorToX(place)
        x = float(found[0] if isinstance(found, tuple) else found)
        if line_end:
            x = max(x, float(line.x() + line.naturalTextWidth()))
        metrics = QFontMetricsF(self.font)
        return QPointF(x, line.y() + metrics.height() / 2)

    def paint(self, painter: QPainter, origin: QPointF, color: str | QColor,
              overlay: Sequence[tuple[int, int, QColor]] = ()) -> None:
        """Paint it at ``origin`` in ``color`` (its marks in theirs); ``overlay``: (start, length, colour) ranges of
        ``text`` painted in that colour over their own (the current turn's words already said, part 5c fixes) - laid
        out as they are, never again."""
        painter.save()
        try:
            painter.setFont(self.font)
            painter.setPen(QColor(color))
            for (layout, _count), first, units in zip(self._layouts, self._starts, self._units):
                ranges = []
                last = first + len(layout.text())
                for start, length, ink in overlay:
                    lo, hi = max(start, first), min(start + length, last)
                    if hi > lo:
                        item = QTextLayout.FormatRange()
                        begin = _unit(units, lo - first)
                        item.start, item.length = begin, _unit(units, hi - first) - begin
                        char = QTextCharFormat()
                        char.setForeground(QColor(ink))
                        # A clear ground on each: QTextLayout.draw skips a selection that sets only a foreground when it
                        # touches one drawn before it (each one's area rounded out to whole pixels: runs that meet
                        # between two pixels do), so an ease's runs would keep their old colours; with a ground (here
                        # one that paints nothing) every one is drawn.
                        char.setBackground(QBrush(QColor(0, 0, 0, 0)))
                        item.format = char
                        ranges.append(item)
                if ranges:
                    layout.draw(painter, origin, ranges)
                else:
                    layout.draw(painter, origin)
        finally:
            painter.restore()


# The words already said (part 5c fixes; the mock-up's ``.turn.now .tx .read``): while Jarvis says the current turn,
# every sentence before the one being said reads in INK_3 - its times and its waiting phrase too -, easing in over
# READ_EASE_MS in lively motion (calm and off: at once); the sentence being said and the rest keep their colours.
READ_EASE_MS = 160


def _base_runs(start: int, end: int, marks: Sequence[tuple[int, int, str]], ink: str | QColor) \
        -> list[tuple[int, int, QColor]]:
    """[start, end) of a turn's words cut where its marks begin and end: (from, to, its own colour) each."""
    runs: list[tuple[int, int, QColor]] = []
    at = start
    for first, length, color in sorted(marks):
        lo, hi = max(first, at), min(first + length, end)
        if hi <= lo:
            continue
        if lo > at:
            runs.append((at, lo, QColor(ink)))
        runs.append((lo, hi, QColor(color)))
        at = hi
    if at < end:
        runs.append((at, end, QColor(ink)))
    return runs


def read_overlay(length: int, marks: Sequence[tuple[int, int, str]], ink: str | QColor, read_from: int, read_to: int,
                 progress: float = 1.0) -> list[tuple[int, int, QColor]]:
    """The overlay (TextBlock.paint) of a turn whose first ``read_to`` characters are said: [0, read_to) in INK_3; while
    it eases (``progress`` under 1) from ``read_from``, the part between them blends from its own colour to INK_3
    (more said) or back (less: the speech ended)."""
    read_from = max(0, min(int(read_from), int(length)))
    read_to = max(0, min(int(read_to), int(length)))
    progress = max(0.0, min(1.0, float(progress)))
    dim = QColor(holo.INK_3)
    low, high = min(read_from, read_to), max(read_from, read_to)
    overlay: list[tuple[int, int, QColor]] = [(0, low, dim)] if low > 0 else []
    if high > low and (read_to > read_from or progress < 1.0):
        for start, end, own in _base_runs(low, high, marks, ink):
            color = holo.mix(own, dim, progress) if read_to > read_from else holo.mix(dim, own, progress)
            overlay.append((start, end - start, color))
    return overlay


# --------------------------------------------------------------------------
# The ring head
# --------------------------------------------------------------------------

EARLIER = "Earlier · {count} turns"
EARLIER_ONE = "Earlier · 1 turn"


def earlier_text(count: int) -> str:
    return EARLIER_ONE if count == 1 else EARLIER.format(count=max(0, int(count)))


class _EarlierButton(QAbstractButton):
    """"Earlier · 2 turns" (the compact head): opens today's transcript drawer."""

    HEIGHT = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._font = _body_font(12)
        self._hover = False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = 9 + 13 + 6 + QFontMetricsF(self._font).horizontalAdvance(self.text()) + 11
        return QSize(math.ceil(width) + 2, self.HEIGHT)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.animateClick()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(holo.LINE_STRONG if self._hover else holo.LINE, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            holo.paint_icon(painter, QRectF(9, (self.height() - 13) / 2, 13, 13), "transcript", holo.INK_3)
            painter.setFont(self._font)
            painter.setPen(QColor(holo.INK_1 if self._hover else holo.INK_2))
            painter.drawText(QRectF(9 + 13 + 6, 0, self.width(), self.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
            if self.hasFocus():
                holo.paint_focus_ring(painter, rect, rect.height() / 2)
        finally:
            painter.end()


class RingHead(QWidget):
    """The column's head: the ring and its hairlines (or, compact, the ring with the state word and Earlier)."""

    earlierClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskRingHead")
        self.orb = orb_mod.OrbWidget(RING, self)
        self.orb.setObjectName("deskOrb")
        self._compact = False
        self._word = ""
        self._amber = False
        self.earlier = _EarlierButton(self)
        self.earlier.clicked.connect(self.earlierClicked)
        self.earlier.hide()
        self._earlier_count = 0

    def set_form(self, compact: bool, ring: int) -> None:
        self._compact = bool(compact)
        if self.orb.orb_size() != int(ring):
            self.orb.set_orb_size(int(ring))
        self._place()
        self.update()

    def compact(self) -> bool:
        return self._compact

    def set_state_word(self, word: str, *, amber: bool = False) -> None:
        if (word, amber) != (self._word, self._amber):
            self._word, self._amber = str(word or ""), bool(amber)
            self.update()

    def state_word(self) -> str:
        return self._word if self._compact else ""

    def set_earlier(self, count: int) -> None:
        """"Earlier · N turns" (compact only; none at 0)."""
        self._earlier_count = max(0, int(count))
        self.earlier.setText(earlier_text(self._earlier_count))
        self.earlier.setAccessibleName(f"{earlier_text(self._earlier_count)}: open the transcript")
        self._place()

    def ring_rect(self) -> QRect:
        """The ring's own box (its ``px``), in this widget's px."""
        side = self.orb.width()
        px = self.orb.orb_size()
        inset = (side - px) // 2
        return QRect(self.orb.x() + inset, self.orb.y() + inset, px, px)

    def _place(self) -> None:
        side = orb_mod.widget_side(self.orb.orb_size())
        if self.orb.size() != QSize(side, side):
            self.orb.resize(side, side)
        if self._compact:
            px = self.orb.orb_size()
            x = -(side - px) // 2                     # the ring's box at the column's left edge
            self.orb.move(x, (self.height() - side) // 2)
            hint = self.earlier.sizeHint()
            text_x = px + 14
            word_h = holo.line_height("label")
            total = word_h + 8 + hint.height()
            top = (self.height() - total) // 2
            self.earlier.setGeometry(text_x, top + word_h + 8, min(hint.width(), max(0, self.width() - text_x)),
                                     hint.height())
            self.earlier.setVisible(self._earlier_count > 0)
        else:
            self.orb.move((self.width() - side) // 2, (self.height() - side) // 2)
            self.earlier.hide()

    def word_rect(self) -> QRectF:
        """Where the compact head's state word is drawn."""
        px = self.orb.orb_size()
        word_h = holo.line_height("label")
        total = word_h + 8 + (_EarlierButton.HEIGHT if self._earlier_count else 0)
        top = (self.height() - total) / 2
        return QRectF(px + 14, top, max(0, self.width() - px - 14), word_h)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            if self._compact:
                if self._word:
                    painter.setFont(holo.font("label"))
                    painter.setPen(QColor(holo.AMBER_HI if self._amber else holo.HOLO_HI))
                    painter.drawText(self.word_rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                     holo.role_text("label", self._word))
                return
            # The hairlines: from the column's edges toward the ring, cyan 0.22 fading out to the edges.
            mid = self.height() / 2
            reach = self.width() / 2 - (self.orb.orb_size() / 2 + HAIRLINE_GAP)
            if reach <= 0:
                return
            for left, right, toward in ((0.0, reach, 1), (self.width() - reach, float(self.width()), -1)):
                gradient = QLinearGradient(left, 0, right, 0)
                gradient.setColorAt(0.0 if toward > 0 else 1.0, holo.alpha(holo.HOLO, 0.0))
                gradient.setColorAt(1.0 if toward > 0 else 0.0, holo.alpha(holo.HOLO, 0.22))
                painter.fillRect(QRectF(left, math.floor(mid), right - left, 1.0), gradient)
        finally:
            painter.end()


# --------------------------------------------------------------------------
# The turns
# --------------------------------------------------------------------------

JARVIS_WORD = "Jarvis"
YOU_WORD = "You"
LABEL_DOT = " · "
TOKEN_ONE = "1 frame on the board"
TOKEN_MANY = "{count} frames on the board"
FADE = 64                   # the turns' top fade
TURN_GAP = 14
TURN_TOP = 8


def token_text(count: int) -> str:
    return TOKEN_ONE if count == 1 else TOKEN_MANY.format(count=max(0, int(count)))


@dataclass(frozen=True)
class Turn:
    """One turn as the desk shows it (from a Transcript entry): its entry id, "jarvis" or "you", its time, its words,
    its tone (an error's coral) and its link (words, id)."""

    entry_id: int
    role: str
    stamp: str
    text: str
    tone: str = ""
    link_text: str = ""
    link_id: str = ""


class FramesToken(QAbstractButton):
    """"4 frames on the board" at the end of a turn that put frames up: the board scrolls to that group."""

    HEIGHT = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._font = _body_font(12)
        self._old = False
        self._hover = False

    def set_old(self, old: bool) -> None:
        if bool(old) != self._old:
            self._old = bool(old)
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = 9 + 14 + 7 + QFontMetricsF(self._font).horizontalAdvance(self.text()) + 11
        return QSize(math.ceil(width) + 2, self.HEIGHT)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.animateClick()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        faded_paint(self, self._paint)

    def _paint(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = holo.LINE_STRONG if self._hover else (holo.LINE_FAINT if self._old else holo.LINE)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        ink = QColor(holo.INK_3 if self._old and not self._hover else holo.HOLO_HI)
        holo.paint_icon(painter, QRectF(9, (self.height() - 14) / 2, 14, 14), "board", ink)
        painter.setFont(self._font)
        painter.setPen(ink)
        painter.drawText(QRectF(9 + 14 + 7, 0, self.width(), self.height()),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
        if self.hasFocus():
            holo.paint_focus_ring(painter, rect, rect.height() / 2)


class TurnView(QWidget):
    """One turn (see the module doc): its label and words painted as plain text, then its frames token and its link."""

    tokenClicked = Signal(int)
    linkClicked = Signal(str)

    LABEL_H = 12
    LABEL_GAP = 4
    TOKEN_GAP = 9
    BUBBLE_PAD = (7, 12, 9, 12)        # top, right, bottom, left
    BUBBLE_SHARE = 0.9

    def __init__(self, turn: Turn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.turn = turn
        self._current = False
        self._compact = False
        self._frames = 0
        self._block: TextBlock | None = None
        self._label = _label_font()
        self.token = FramesToken(self)
        self.token.hide()
        self.token.clicked.connect(lambda: self.tokenClicked.emit(self.turn.entry_id))
        self.link = holo.HoloButton("", holo.LINK_CYAN, size=holo.SIZE_SM, parent=self)
        self.link.hide()
        self.link.clicked.connect(lambda: self.linkClicked.emit(self.turn.link_id))
        self._heights: dict[int, int] = {}
        self._read_from = 0                    # part 5c fixes: the words already said (set_read), easing from ...
        self._read_to = 0                      # ... to this many characters
        self._read_p = 1.0
        self._read_anim: QVariantAnimation | None = None
        self.set_turn(turn)

    # ---- content -------------------------------------------------------------------------------------------------

    def you(self) -> bool:
        return self.turn.role == hud.ROLE_YOU

    def set_turn(self, turn: Turn, *, current: bool | None = None, compact: bool | None = None,
                 frames: int | None = None) -> None:
        changed = turn != self.turn or self._block is None
        self.turn = turn
        if current is not None and bool(current) != self._current:
            self._current, changed = bool(current), True
        if compact is not None and bool(compact) != self._compact:
            self._compact, changed = bool(compact), True
        if frames is not None and int(frames) != self._frames:
            self._frames = max(0, int(frames))
            changed = True
        if not changed:
            return
        if not self._current or self.you():
            self._end_read()                   # no longer the turn being said: its own colours at once
        elif self._read_to > len(turn.text) or self._read_from > len(turn.text):
            self._read_to = min(self._read_to, len(turn.text))
            self._read_from = min(self._read_from, len(turn.text))
        if self._current and not self.you():
            px, line = (15, 23) if self._compact else (16, 25)
            self._block = TextBlock(turn.text, _body_font(px), line, highlights(turn.text))
        else:
            self._block = TextBlock(turn.text, _body_font(13), 20)
        self.token.setText(token_text(self._frames))
        self.token.setAccessibleName(f"{token_text(self._frames)}: show them")
        self.token.set_old(not self._current)
        self.token.setVisible(self._frames > 0 and not self.you())
        self.link.setText(turn.link_text)
        self.link.setVisible(bool(turn.link_text) and not self.you())
        if turn.link_text:
            self.link.setAccessibleName(f"{turn.link_text}: {turn.text.splitlines()[0][:120] if turn.text else ''}")
        self.setAccessibleName(f"{self.label()}: {turn.text}")
        self._heights.clear()
        self.updateGeometry()
        self._place()
        self.update()

    def label(self) -> str:
        """"JARVIS · 9:41 AM" / "YOU · 9:41 AM" (as painted: caps)."""
        who = YOU_WORD if self.you() else JARVIS_WORD
        return (f"{who}{LABEL_DOT}{self.turn.stamp}" if self.turn.stamp else who).upper()

    def current(self) -> bool:
        return self._current

    def frames(self) -> int:
        return self._frames

    def block(self) -> TextBlock | None:
        return self._block

    # ---- the words already said (part 5c fixes) -------------------------------------------------------------------

    def set_read(self, end: int, *, at_once: bool = False) -> None:
        """While Jarvis says this, the current turn: its first ``end`` characters - every sentence before the one being
        said - read in INK_3, its times and waiting phrase too; 0 none (the speech ended, was stopped or muted: the turn
        in its own colours again). The change eases over READ_EASE_MS in lively motion; at once in calm and off, with
        ``at_once``, and out of sight."""
        end = max(0, min(int(end), len(self.turn.text))) if self._current and not self.you() else 0
        if end == self._read_to:
            return
        if self._read_anim is not None:
            self._read_anim.stop()
        self._read_from, self._read_to = self._read_to, end      # (an ease cut short: the next starts where it went)
        if at_once or holo.motion_level() != holo.MOTION_LIVELY or not self.isVisible():
            self._read_from, self._read_p = end, 1.0
        else:
            self._read_p = 0.0
            self._read_animation().start()
        self.update()

    def read_end(self) -> int:
        """How many of its characters read as said now (the target, when it eases)."""
        return self._read_to

    def read_easing(self) -> bool:
        return self._read_p < 1.0

    def read_overlay(self) -> list[tuple[int, int, QColor]]:
        """What set_read paints over the turn's own colours now (TextBlock.paint's overlay)."""
        if self._block is None or not self._current or self.you() or not (self._read_to or self._read_from):
            return []
        ink = holo.ALERT_TX if self.turn.tone == hud.TONE_ERROR else holo.INK_1
        return read_overlay(len(self.turn.text), self._block.marks, ink, self._read_from, self._read_to, self._read_p)

    def _end_read(self) -> None:
        if self._read_anim is not None:
            self._read_anim.stop()
        self._read_from = self._read_to = 0
        self._read_p = 1.0

    def _read_animation(self) -> QVariantAnimation:
        if self._read_anim is None:
            animation = QVariantAnimation(self)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setDuration(READ_EASE_MS)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(self._on_read_step)
            animation.finished.connect(self._on_read_done)
            self._read_anim = animation
        return self._read_anim

    def _on_read_step(self, value: Any) -> None:
        self._read_p = max(0.0, min(1.0, float(value)))
        self.update()

    def _on_read_done(self) -> None:
        self._read_from, self._read_p = self._read_to, 1.0
        self.update()

    # ---- geometry ------------------------------------------------------------------------------------------------

    def _text_width(self, width: int) -> float:
        if self.you():
            top, right, bottom, left = self.BUBBLE_PAD
            return max(1.0, width * self.BUBBLE_SHARE - left - right)
        return float(max(1, width))

    def _bubble_rect(self, width: int) -> QRectF:
        assert self._block is not None
        top, right, bottom, left = self.BUBBLE_PAD
        text_h = self._block.layout(self._text_width(width))
        label_w = QFontMetricsF(self._label).horizontalAdvance(self.label())
        inner = max(self._block.natural, label_w)
        bubble_w = min(width * self.BUBBLE_SHARE, inner + left + right + 1)
        height = top + self.LABEL_H + self.LABEL_GAP + text_h + bottom
        return QRectF(width - bubble_w, 0.0, bubble_w, height)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        width = max(1, int(width))
        found = self._heights.get(width)
        if found is not None:
            return found
        if self._block is None:
            return 0
        if self.you():
            height = self._bubble_rect(width).height()
        else:
            height = self.LABEL_H + self.LABEL_GAP + self._block.layout(self._text_width(width))
            if self._frames > 0:
                height += self.TOKEN_GAP + FramesToken.HEIGHT
            if self.turn.link_text:
                height += 4 + self.link.sizeHint().height()
        found = math.ceil(height)
        self._heights = {width: found}
        return found

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = max(1, self.width())
        return QSize(width, self.heightForWidth(width))

    def _place(self) -> None:
        if self._block is None or self.you():
            return
        y = self.LABEL_H + self.LABEL_GAP + self._block.layout(self._text_width(max(1, self.width())))
        if self._frames > 0:
            hint = self.token.sizeHint()
            self.token.setGeometry(0, round(y + self.TOKEN_GAP), min(hint.width(), max(1, self.width())), hint.height())
            y += self.TOKEN_GAP + FramesToken.HEIGHT
        if self.turn.link_text:
            hint = self.link.sizeHint()
            self.link.setGeometry(-holo.HoloButton.MARGIN, round(y + 4), hint.width(), hint.height())

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place()

    def text_point(self, offset: int, *, line_end: bool = False) -> QPointF | None:
        """Where character place ``offset`` of a Jarvis turn's words is painted (x at it, y its line's middle; with
        ``line_end`` the end of the words on that line), in this widget's px: the cue line starts beside a sentence's
        end (part 5c). None for the owner's turns."""
        if self._block is None or self.you():
            return None
        self._block.layout(self._text_width(max(1, self.width())))
        point = self._block.point_at(offset, line_end=line_end)
        if point is None:
            return None
        return QPointF(point.x(), point.y() + self.LABEL_H + self.LABEL_GAP)

    def text_rect(self) -> QRectF:
        """Where the words are painted (tests measure that nothing is clipped)."""
        if self._block is None:
            return QRectF()
        width = max(1, self.width())
        if self.you():
            bubble = self._bubble_rect(width)
            top, _right, _bottom, left = self.BUBBLE_PAD
            return QRectF(bubble.left() + left, top + self.LABEL_H + self.LABEL_GAP, self._text_width(width),
                          self._block.lines * self._block.line)
        return QRectF(0.0, self.LABEL_H + self.LABEL_GAP, float(width), self._block.lines * self._block.line)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._block is not None:
            faded_paint(self, self._paint)

    def _paint(self, painter: QPainter) -> None:
        assert self._block is not None
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        width = max(1, self.width())
        if self.you():
            bubble = self._bubble_rect(width)
            path = QPainterPath()
            _rounded(path, bubble.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12, 4, 12)
            painter.fillPath(path, QColor(holo.FIELD))
            painter.setPen(QPen(holo.LINE_FAINT, 1.0))
            painter.drawPath(path)
            top, _right, _bottom, left = self.BUBBLE_PAD
            painter.setFont(self._label)
            painter.setPen(QColor(holo.INK_4))
            painter.drawText(QRectF(bubble.left() + left, top, bubble.width(), self.LABEL_H),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.label())
            self._block.layout(self._text_width(width))
            self._block.paint(painter, QPointF(bubble.left() + left, top + self.LABEL_H + self.LABEL_GAP), holo.INK_2)
            return
        painter.setFont(self._label)
        painter.setPen(QColor(holo.HOLO_LO))
        painter.drawText(QRectF(0, 0, width, self.LABEL_H),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.label())
        self._block.layout(self._text_width(width))
        if self.turn.tone == hud.TONE_ERROR:
            ink = holo.ALERT_TX
        else:
            ink = holo.INK_1 if self._current else holo.INK_3
        self._block.paint(painter, QPointF(0.0, self.LABEL_H + self.LABEL_GAP), ink, self.read_overlay())


def _rounded(path: QPainterPath, rect: QRectF, tl: float, tr: float, br: float, bl: float) -> None:
    """A rectangle with its own radius at each corner (the owner's bubble: 12 12 4 12)."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    path.quadTo(x + w, y, x + w, y + tr)
    path.lineTo(x + w, y + h - br)
    path.quadTo(x + w, y + h, x + w - br, y + h)
    path.lineTo(x + bl, y + h)
    path.quadTo(x, y + h, x, y + h - bl)
    path.lineTo(x, y + tl)
    path.quadTo(x, y, x + tl, y)
    path.closeSubpath()


def _turns_of(widget: QWidget) -> DeskTurns | None:
    hops = 0
    parent = widget.parentWidget()
    while parent is not None and hops < 8:
        if isinstance(parent, DeskTurns):
            return parent
        parent = parent.parentWidget()
        hops += 1
    return None


def _fade_top(widget: QWidget) -> float | None:
    """``widget``'s top in its turns' view (None when it is in none): the top FADE px of that view fade out."""
    turns = _turns_of(widget)
    if turns is None:
        return None
    return float(widget.mapTo(turns.viewport(), QPoint(0, 0)).y())


def faded_paint(widget: QWidget, paint: Callable[[QPainter], None]) -> None:
    """Paint ``widget`` with ``paint(painter)``; where it is inside its turns' top FADE px it fades out toward the
    top (an alpha mask on what it painted: the window's ground under it stays as it is)."""
    top = _fade_top(widget)
    if top is None or top >= FADE:
        painter = QPainter(widget)
        try:
            paint(painter)
        finally:
            painter.end()
        return
    ratio = max(1.0, widget.devicePixelRatioF())
    image = QImage(max(1, round(widget.width() * ratio)), max(1, round(widget.height() * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint(painter)
        mask = QLinearGradient(0, -top, 0, FADE - top)       # the view's 0 .. FADE in this widget's px
        mask.setColorAt(0.0, QColor(0, 0, 0, 0))
        mask.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(QRectF(0, 0, widget.width(), widget.height()), mask)
    finally:
        painter.end()
    out = QPainter(widget)
    try:
        out.drawImage(QPointF(0, 0), image)
    finally:
        out.end()


class _TurnsColumn(QWidget):
    """The turns' content: each turn placed by hand, the newest at the bottom (DeskTurns lays it out)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskTurnsContent")
        self.setAutoFillBackground(False)


class DeskTurns(QScrollArea):
    """The turns (see the module doc). ``set_turns(turns, frames_for=...)`` from the Transcript's entries;
    ``set_live(text)`` what Jarvis is saying now, as the current turn until the answer's own turn comes;
    ``set_compact`` (the last exchange only). The newest turn is at the bottom; older ones scroll up under the
    64 px top fade (the wheel scrolls them; the newest comes back into view with the next turn). Part 5b: F6 gives it
    the keyboard focus (not Tab: Home's Tab order is as it was) - the arrows and Page Up / Down scroll it, Tab goes on to
    its tokens and links, Esc back to the ask bar (``escaped``); the desk paints its focus ring."""

    tokenClicked = Signal(int)
    linkClicked = Signal(str)
    escaped = Signal()
    turnsChanged = Signal()           # the turns shown were built again (the current one may be another now)
    SCROLL_HOLD_S = 4.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskTurns")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QScrollArea#deskTurns, QScrollArea#deskTurns > QWidget > QWidget#deskTurnsContent "
                           "{ background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._content = _TurnsColumn()
        self.setWidget(self._content)
        self._content.setAutoFillBackground(False)     # setWidget turned it on: the window's ground shows through
        self.viewport().setAutoFillBackground(False)
        self._views: dict[Any, TurnView] = {}
        self._order: list[TurnView] = []
        self._turns: tuple[Turn, ...] = ()
        self._live = ""
        self._live_after = 0
        self._read: tuple[Any, int] = (None, 0)    # part 5c fixes: the turn whose words are said so far, and how many
        self._compact = False
        self._earlier = 0
        self._frames_for: Callable[[int], int] = lambda _entry: 0
        self._user_scrolled_at = -math.inf
        bar = self.verticalScrollBar()
        bar.actionTriggered.connect(self._on_user_scroll)
        bar.valueChanged.connect(lambda _value: self.viewport().update())   # the fade follows what scrolled

    # ---- what it shows ---------------------------------------------------------------------------------------------

    def set_compact(self, compact: bool) -> None:
        if bool(compact) != self._compact:
            self._compact = bool(compact)
            self._rebuild()

    def set_turns(self, turns: Sequence[Turn], frames_for: Callable[[int], int] | None = None) -> None:
        self._turns = tuple(turns)
        if frames_for is not None:
            self._frames_for = frames_for
        newest = max((turn.entry_id for turn in self._turns if turn.role != hud.ROLE_YOU), default=0)
        if self._live and newest > self._live_after:
            self._live = ""                    # the answer's own turn came: it stands for what was being said
        self._rebuild()

    def set_live(self, text: str) -> None:
        """What Jarvis is saying now (Home's caption), shown as the current turn until the next of his own turns
        comes; "" none."""
        text = " ".join(str(text or "").split())
        if text and not self._live:
            self._live_after = max((turn.entry_id for turn in self._turns if turn.role != hud.ROLE_YOU), default=0)
        if text == self._live:
            return
        newest = self._answer_since_owner()
        if text and newest is not None and " ".join(newest.text.split()).startswith(text[:40]):
            text = ""                          # it is there already as his turn (spoken after it came, or before)
        self._live = text
        self._rebuild()

    def set_read(self, entry_id: Any, end: int, *, at_once: bool = False) -> None:
        """Part 5c fixes: while Jarvis says the current turn (``entry_id``: its entry, -1 for what he says now), its
        first ``end`` characters are said (TurnView.set_read: they read in INK_3); every other turn none. ``None``: no
        turn (he is done, stopped or muted: the turn eases back to its own colours)."""
        self._read = (entry_id, max(0, int(end)) if entry_id is not None else 0)
        self._apply_read(at_once=at_once)

    def read(self) -> tuple[Any, int]:
        return self._read

    def _apply_read(self, *, at_once: bool) -> None:
        entry_id, end = self._read
        for view in self._order:
            mine = entry_id is not None and view.turn.entry_id == entry_id
            view.set_read(end if mine else 0, at_once=at_once)

    def _answer_since_owner(self) -> Turn | None:
        """Jarvis's newest turn when it comes after the owner's latest one (None: he has not answered it yet - an older
        answer that starts with the same words is never the one being said now)."""
        last = self._turns[-1] if self._turns else None
        return last if last is not None and last.role != hud.ROLE_YOU else None

    def refresh_frames(self) -> None:
        """The frames' counts changed (a group came, grew or left): the tokens follow."""
        self._rebuild()

    def earlier(self) -> int:
        """Turns not shown (compact: before the last exchange)."""
        return self._earlier

    def views(self) -> list[TurnView]:
        """The turns shown, oldest first."""
        return list(self._order)

    def current_view(self) -> TurnView | None:
        """The current turn (Jarvis's newest, Sora 16/25): the one a sentence being said is found in (part 5c)."""
        return next((view for view in reversed(self._order) if view.current()), None)

    def live_text(self) -> str:
        return self._live

    def _shown(self) -> list[Turn]:
        turns = list(self._turns)
        if self._live:
            turns.append(Turn(-1, hud.ROLE_JARVIS, "", self._live))
        if not self._compact:
            self._earlier = 0
            return turns
        last_you = max((index for index, turn in enumerate(turns) if turn.role == hud.ROLE_YOU), default=-1)
        start = last_you if last_you >= 0 else max(0, len(turns) - 1)
        self._earlier = start
        return turns[start:]

    def _rebuild(self) -> None:
        shown = self._shown()
        current = next((turn.entry_id for turn in reversed(shown) if turn.role != hud.ROLE_YOU), None)
        keep: dict[Any, TurnView] = {}
        order: list[TurnView] = []
        for turn in shown:
            key = turn.entry_id
            view = self._views.get(key)
            if view is None:
                view = TurnView(turn, self._content)
                view.tokenClicked.connect(self.tokenClicked)
                view.linkClicked.connect(self.linkClicked)
            frames = self._frames_for(turn.entry_id) if turn.entry_id > 0 and turn.role != hud.ROLE_YOU else 0
            view.set_turn(turn, current=key == current, compact=self._compact, frames=frames)
            keep[key] = view
            order.append(view)
        for key, view in self._views.items():
            if key not in keep:
                view.hide()
                view.deleteLater()
        self._views = keep
        self._order = order
        self._apply_read(at_once=True)            # (a turn no longer the one said: its own colours)
        self._relayout()
        self.turnsChanged.emit()

    def _relayout(self) -> None:
        """Each turn at its height for the view's width, the newest at the bottom (older ones above it)."""
        width = max(1, self.viewport().width())
        heights = [view.heightForWidth(width) for view in self._order]
        total = TURN_TOP + sum(heights) + TURN_GAP * max(0, len(heights) - 1)
        content = max(total, self.viewport().height())
        y = content - total + TURN_TOP
        for view, height in zip(self._order, heights):
            view.setGeometry(0, y, width, height)
            view.show()
            y += height + TURN_GAP
        if self._content.size() != QSize(width, content):
            self._content.resize(width, content)
        self._follow()
        self.viewport().update()

    # ---- following the newest ----------------------------------------------------------------------------------

    def _on_user_scroll(self, _action: int) -> None:
        self._user_scrolled_at = time.monotonic()

    def _follow(self) -> None:
        if time.monotonic() - self._user_scrolled_at < self.SCROLL_HOLD_S:
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def fade_alpha(self, y: float) -> float:
        """The fade's opacity at ``y`` px from the top of the turns (0 at the top, 1 from FADE on)."""
        return max(0.0, min(1.0, float(y) / FADE))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.escaped.emit()
            return
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self._on_user_scroll(0)              # the owner scrolls: the newest stays where it is a while
        super().keyPressEvent(event)

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self._ring_changed()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self._ring_changed()

    def _ring_changed(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.update()


# --------------------------------------------------------------------------
# Empty Home's hint line (DESIGN-SPEC 2.4)
# --------------------------------------------------------------------------

HINT_TEXT = "When I find something to show you, it appears beside our conversation."
HINT_KEY = "Ctrl B"


class BoardHint(QWidget):
    """Empty Home's one line under the greeting: the board's glyph, the words and a CTRL B key cap, centred."""

    HEIGHT = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("boardHint")
        self.setFixedHeight(self.HEIGHT)
        self.setAccessibleName(f"{HINT_TEXT} Control B")
        self._font = _body_font(13)
        self._key_font = _font(hud.MONO_FAMILY, 10, 500, 0.16, caps=True)

    def parts(self) -> tuple[float, float, float]:
        """(glyph, words, key cap) widths."""
        words = QFontMetricsF(self._font).horizontalAdvance(HINT_TEXT)
        key = QFontMetricsF(self._key_font).horizontalAdvance(HINT_KEY.upper()) + 10 + 2
        return 15.0, words, key

    def natural_width(self) -> float:
        glyph, words, key = self.parts()
        return glyph + 8 + words + 8 + key

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(math.ceil(self.natural_width()), self.HEIGHT)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            glyph, words, key = self.parts()
            x = max(0.0, (self.width() - self.natural_width()) / 2)
            mid = self.height() / 2
            holo.paint_icon(painter, QRectF(x, mid - 7.5, 15, 15), "board", holo.HOLO_LO)
            x += glyph + 8
            painter.setFont(self._font)
            painter.setPen(QColor(holo.INK_3))
            painter.drawText(QRectF(x, 0, words + 2, self.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), HINT_TEXT)
            x += words + 8
            cap = QRectF(x, mid - 8, key, 16)
            painter.setPen(QPen(holo.LINE, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), holo.RADIUS_BADGE, holo.RADIUS_BADGE)
            painter.setFont(self._key_font)
            painter.setPen(QColor(holo.INK_3))
            painter.drawText(cap, int(Qt.AlignmentFlag.AlignCenter), HINT_KEY)
        finally:
            painter.end()


# --------------------------------------------------------------------------
# The ring's flight to the column head (DESIGN-SPEC 5: "Desk forms")
# --------------------------------------------------------------------------

FLIGHT_MS = 420
CALM = 1.6                  # calm stretches every time by this


def _expo_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1.0 if p >= 1.0 else 1.0 - math.pow(2.0, -10.0 * p)


class RingFlight(QWidget):
    """A snapshot of the ring flying from the stage to the column head over its parent (never the mouse's, never the
    focus): ``fly(pixmap, start, end, done)``; 420 ms ease-out-expo, x1.6 calm; False (and nothing flies) with motion
    off or a hidden parent."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pixmap: QPixmap | None = None
        self._start = QRectF()
        self._end = QRectF()
        self._began = 0.0
        self._length = FLIGHT_MS / 1000.0
        self._done: Callable[[], None] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def fly(self, pixmap: QPixmap, start: QRectF, end: QRectF, done: Callable[[], None] | None = None) -> bool:
        level = holo.motion_level()
        parent = self.parentWidget()
        if level == holo.MOTION_OFF or parent is None or not parent.isVisible() or pixmap.isNull() \
                or start.isEmpty() or end.isEmpty():
            return False
        self.stop(finish=True)
        self._pixmap, self._start, self._end = pixmap, QRectF(start), QRectF(end)
        self._length = FLIGHT_MS * (CALM if level == holo.MOTION_CALM else 1.0) / 1000.0
        self._done = done
        self._began = time.monotonic()
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self._timer.start()
        self.update()
        return True

    def running(self) -> bool:
        return self._timer.isActive()

    def length_ms(self) -> int:
        return round(self._length * 1000)

    def rect_now(self, now: float | None = None) -> QRectF:
        p = (((time.monotonic() if now is None else now) - self._began) / self._length) if self._length else 1.0
        e = _expo_out(p)
        a, b = self._start, self._end
        return QRectF(a.x() + (b.x() - a.x()) * e, a.y() + (b.y() - a.y()) * e,
                      a.width() + (b.width() - a.width()) * e, a.height() + (b.height() - a.height()) * e)

    def stop(self, *, finish: bool = True) -> None:
        if not self._timer.isActive() and self._done is None:
            return
        self._timer.stop()
        self.hide()
        done, self._done = self._done, None
        self._pixmap = None
        if finish and done is not None:
            done()

    def _tick(self) -> None:
        if not shiboken6.isValid(self):
            return
        if time.monotonic() - self._began >= self._length:
            self.stop(finish=True)
            return
        self.update()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        if self._timer.isActive():
            QTimer.singleShot(0, self, lambda: self.stop(finish=True))

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._pixmap is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(self.rect_now(), self._pixmap, QRectF(self._pixmap.rect()))
        finally:
            painter.end()


# --------------------------------------------------------------------------
# The cue line's native part (part 5c, DESIGN-SPEC 5 and 8)
# --------------------------------------------------------------------------

CUE_DRAW_MS = 200           # the native part draws in (cubic-out), then the page's 15 px stub (to 240 ms)
CUE_FADE_MS = 400           # it fades this long after its sentence
CUE_PEN = 1.2               # px
CUE_DOT = 3.0               # the dot beside the sentence's end (6 px; the page's at the frame is 2.5 px)
CUE_DOT_GAP = 6             # between the sentence's last letter and that dot ...
CUE_LINE_GAP = 4            # ... and between the dot and the line
CUE_TICK_MS = 16


def _cubic_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1.0 - (1.0 - p) ** 3


@dataclass(frozen=True)
class CuePath:
    """Where the native part runs, in the desk's px: from beside the sentence's end (``start``: its last letter's right
    edge, its line's middle) along to the seam (``seam_x``, its middle) and along it to ``end_y`` (the lit frame's
    anchor, where the page's stub takes over); ``amber`` for a frame that waits (CUE_AMBER, else CUE)."""

    start: QPointF
    seam_x: float
    end_y: float
    amber: bool = False

    def points(self) -> tuple[QPointF, QPointF, QPointF, QPointF]:
        """(the dot's centre, the line's start, the corner at the seam, the end)."""
        dot = QPointF(min(self.start.x() + CUE_DOT_GAP + CUE_DOT, self.seam_x - CUE_DOT), self.start.y())
        first = QPointF(min(dot.x() + CUE_DOT + CUE_LINE_GAP, self.seam_x), self.start.y())
        return dot, first, QPointF(self.seam_x, self.start.y()), QPointF(self.seam_x, self.end_y)


class CueLine(QWidget):
    """The cue line's native part (DESIGN-SPEC 8): a dot beside the end of the sentence being said, then a 1.2 px line
    to the seam and along it, in the frame's colour; it stops at the seam and is never drawn over the board's view
    (its geometry ends at the seam: the desk sets it). ``show_path(path, draw)``: drawn in over CUE_DRAW_MS
    (cubic-out) when ``draw``, else at once (calm and motion off: always at once); ``move_path(path)``: the same line
    elsewhere, at once; ``set_hidden(hidden)``: out of sight while the board scrolls, back at once; ``fade()``: it goes
    over CUE_FADE_MS (calm x1.6; motion off: at once), and a new line may draw meanwhile. Never the mouse's or the
    focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskCueLine")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._path: CuePath | None = None
        self._drawn_at = 0.0               # when its draw-in began (monotonic s; 0: at once)
        self._draw_s = 0.0
        self._leaving: CuePath | None = None
        self._leave_at = 0.0
        self._leave_s = 0.0
        self._hidden = False
        self._timer = QTimer(self)
        self._timer.setInterval(CUE_TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ---- what it shows -------------------------------------------------------------------------------------------

    def path(self) -> CuePath | None:
        return self._path

    def shown(self) -> bool:
        """A line is drawn now (or drawing in): not faded, not hidden."""
        return self._path is not None and not self._hidden

    def drawing(self) -> bool:
        return self._path is not None and self._progress() < 1.0

    def show_path(self, path: CuePath, *, draw: bool = True) -> None:
        level = holo.motion_level()
        if self._path is not None and self._path != path:
            self.fade()                        # the line before goes as this one comes
        self._path = path
        self._draw_s = CUE_DRAW_MS / 1000.0 if draw and level == holo.MOTION_LIVELY else 0.0
        self._drawn_at = time.monotonic()
        self._run()
        self.update()

    def move_path(self, path: CuePath) -> None:
        if self._path is None:
            self.show_path(path, draw=False)
            return
        if path != self._path:
            self._path = path
            self.update()

    def set_hidden(self, hidden: bool) -> None:
        if bool(hidden) != self._hidden:
            self._hidden = bool(hidden)
            if not hidden:
                self._draw_s = 0.0             # back after the board scrolled: without the draw-in
                self._run()
            self.update()

    def fade(self) -> None:
        """The line goes (DESIGN-SPEC 5: 400 ms after its sentence; calm x1.6; off: removed at once)."""
        if self._path is None:
            return
        level = holo.motion_level()
        self._leaving = self._path if level != holo.MOTION_OFF and not self._hidden else None
        self._leave_s = CUE_FADE_MS * (CALM if level == holo.MOTION_CALM else 1.0) / 1000.0
        self._leave_at = time.monotonic()
        self._path = None
        self._run()
        self.update()

    def clear(self) -> None:
        """Nothing at once (open big covers the column; the desk folded)."""
        self._path = self._leaving = None
        self._timer.stop()
        self.update()

    # ---- motion --------------------------------------------------------------------------------------------------

    def _progress(self, now: float | None = None) -> float:
        if self._draw_s <= 0.0:
            return 1.0
        return _cubic_out(((time.monotonic() if now is None else now) - self._drawn_at) / self._draw_s)

    def _leave_alpha(self, now: float | None = None) -> float:
        if self._leaving is None or self._leave_s <= 0.0:
            return 0.0
        return max(0.0, 1.0 - ((time.monotonic() if now is None else now) - self._leave_at) / self._leave_s)

    def _run(self) -> None:
        if self.drawing() or self._leaving is not None:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        if not shiboken6.isValid(self):
            return
        if self._leaving is not None and self._leave_alpha() <= 0.0:
            self._leaving = None
        self._run()
        self.update()

    # ---- painting ------------------------------------------------------------------------------------------------

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._hidden or (self._path is None and self._leaving is None):
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._leaving is not None:
                self._draw(painter, self._leaving, self._leave_alpha(), 1.0)
            if self._path is not None:
                self._draw(painter, self._path, 1.0, self._progress())
        finally:
            painter.end()

    @staticmethod
    def _draw(painter: QPainter, path: CuePath, alpha: float, progress: float) -> None:
        if alpha <= 0.0:
            return
        line = holo.CUE_AMBER if path.amber else holo.CUE
        hi = QColor(holo.AMBER_HI if path.amber else holo.HOLO_HI)
        dot, first, corner, end = path.points()
        painter.save()
        try:
            painter.setOpacity(alpha)
            glow = QRadialGradient(dot, CUE_DOT * 2.6)          # the sentence's dot, softly lit
            glow.setColorAt(0.0, holo.alpha(hi, 0.35))
            glow.setColorAt(1.0, holo.alpha(hi, 0.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(dot, CUE_DOT * 2.6, CUE_DOT * 2.6)
            painter.setBrush(hi)
            painter.drawEllipse(dot, CUE_DOT, CUE_DOT)
            across = abs(corner.x() - first.x())
            down = abs(end.y() - corner.y())
            reach = (across + down) * max(0.0, min(1.0, progress))
            if reach <= 0.0:
                return
            pen = QPen(QColor(line), CUE_PEN)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            trace = QPainterPath(first)
            if reach <= across:
                trace.lineTo(first.x() + reach, first.y())
            else:
                trace.lineTo(corner)
                step = reach - across
                trace.lineTo(corner.x(), corner.y() + (step if end.y() >= corner.y() else -step))
            painter.drawPath(trace)
        finally:
            painter.restore()


class Veil(QWidget):
    """A still of the window where the board's view is about to be set (open big, part 5c): over the view for the
    moment its geometry changes, until the page has laid itself out at the new size, so nothing jumps; then gone.
    Never the mouse's or the focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskVeil")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pixmap: QPixmap | None = None
        self.hide()

    def show_still(self, pixmap: QPixmap, rect: QRect) -> None:
        self._pixmap = pixmap
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self.hide()

    def shown(self) -> bool:
        return self._pixmap is not None and not self.isHidden()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._pixmap is None:
            return
        painter = QPainter(self)
        try:
            painter.drawPixmap(QRectF(self.rect()), self._pixmap, QRectF(self._pixmap.rect()))
        finally:
            painter.end()


# --------------------------------------------------------------------------
# The desk
# --------------------------------------------------------------------------

class DeskPage(QWidget):
    """The desk (see the module doc): Home's page while the board holds frames."""

    tokenClicked = Signal(int)
    linkClicked = Signal(str)
    earlierClicked = Signal()
    geometryChanged = Signal()        # the board's place moved (its view's geometry was set again)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskPage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.head = RingHead(self)
        self.turns = DeskTurns(self)
        self.turns.tokenClicked.connect(self.tokenClicked)
        self.turns.linkClicked.connect(self.linkClicked)
        self.head.earlierClicked.connect(self.earlierClicked)
        self.chip_slot = QWidget(self)          # Home's chip row comes in here (the app moves it)
        self.chip_slot.setObjectName("deskChips")
        self.ask_slot = QWidget(self)           # ... and its command bar
        self.ask_slot.setObjectName("deskAsk")
        self.cue_line = CueLine(self)           # part 5c: the cue line's native part, from the column to the seam
        self.veil = Veil(self)                  # ... and open big's still over the view while its geometry is set
        self._board: QWidget | None = None
        self._folded_dock = False
        self._big = False                       # open big: the board's view spans both columns (geometry.big)
        self._geometry = desk_geometry(0, 0, folded_dock=False)
        self._view_rect = QRect()

    def set_folded_dock(self, folded: bool) -> None:
        if bool(folded) != self._folded_dock:
            self._folded_dock = bool(folded)
            self.apply_geometry()

    def geometry_now(self) -> DeskGeometry:
        return self._geometry

    def set_board(self, view: QWidget | None) -> None:
        """The board's one view goes in its place (made once; its geometry set once per layout, never animated)."""
        if view is self._board:
            return
        self._board = view
        if view is not None:
            if view.parentWidget() is not self:
                view.setParent(self)
            view.show()
        self.apply_geometry()

    def board(self) -> QWidget | None:
        return self._board

    def set_big(self, big: bool) -> bool:
        """Open big (part 5c, DESIGN-SPEC 3.5): the board's view spans both columns (``geometry.big``), or is back in
        its column - set once, never animated. True when the geometry changed."""
        if bool(big) == self._big:
            return False
        self._big = bool(big)
        before = QRect(self._view_rect)
        self.apply_geometry()
        self._cover(self._big)
        return self._view_rect != before

    def _cover(self, covered: bool) -> None:
        """Open big covers the conversation (part 5c fixes): its ring head and turns take no focus - out of the Tab chain
        - and no click while covered (disabled: a turn that comes meanwhile too), and are as they were after."""
        for part in (self.head, self.turns):
            part.setEnabled(not covered)

    def big_shown(self) -> bool:
        return self._big

    def view_target(self) -> QRect:
        """Where the board's view goes in this layout: its column, or both columns while big."""
        return QRect(self._geometry.big if self._big else self._geometry.view)

    def apply_geometry(self) -> None:
        geometry = desk_geometry(self.width(), self.height(), folded_dock=self._folded_dock)
        self._geometry = geometry
        self.head.setGeometry(geometry.head)
        self.head.set_form(geometry.compact, geometry.ring)
        self.turns.setGeometry(geometry.turns)
        self.turns.set_compact(geometry.compact)
        # The chips (holo.ActionChip: 32 drawn in 40) and the ask bar (52 drawn in 60) keep their drawn edges on the
        # column's: their widgets reach their margins past it.
        chip_margin = 4
        self.chip_slot.setGeometry(geometry.chips.adjusted(-chip_margin, -chip_margin - 1, chip_margin,
                                                           chip_margin + 1))
        bar_margin = 4
        self.ask_slot.setGeometry(geometry.ask.adjusted(-bar_margin, -bar_margin, bar_margin, bar_margin))
        # The cue line's native part: from the column's left edge to the seam, never over the view (it starts past it);
        # while big the wide view covers the column, so it stays hidden there (never drawn over the web view).
        self.cue_line.setGeometry(0, 0, geometry.seam.x() + 1, max(0, self.height()))
        if self._big:
            self.cue_line.hide()
        else:
            self.cue_line.show()
            self.cue_line.raise_()
        board = self._board
        target = geometry.big if self._big else geometry.view
        if board is not None and shiboken6.isValid(board) and board.parentWidget() is self \
                and board.geometry() != target:
            board.setGeometry(target)              # set once for this layout (never animated)
            self._view_rect = QRect(target)
            self.geometryChanged.emit()
        if not self.veil.isHidden():
            self.veil.raise_()
        self.update()

    def column_image(self, rect: QRect, ground: Callable[[QPainter, QWidget], None] | None = None,
                     dpr: float | None = None) -> QImage:
        """The conversation column as it looks in ``rect`` (the desk's px) - the window's ground (``ground``: the
        window's ground painter), the ring head, the turns, the chips and the seam; never the board's view, never the
        cue line - at ``dpr`` (default the screen's): open big's backdrop (part 5c), made while the view may already
        cover the column."""
        ratio = float(dpr or self.devicePixelRatioF() or 1.0)
        image = QImage(max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio)),
                       QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(ratio)
        image.fill(QColor(holo.VOID))
        covered = self._big
        if covered:
            self._cover(False)                  # painted as it looks, never in a disabled look
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.translate(-rect.left(), -rect.top())
            if ground is not None:
                try:
                    ground(painter, self)
                except Exception:  # noqa: BLE001 - the void under it instead
                    pass
            painter.fillRect(QRectF(self._geometry.seam), holo.LINE_FAINT)
            for child in (self.head, self.turns, self.chip_slot):
                if child.isVisible() and child.geometry().intersects(rect):
                    child.render(painter, child.pos(), QRegion(), QWidget.RenderFlag.DrawChildren)
        finally:
            painter.end()
            if covered:
                self._cover(True)
        return image

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.apply_geometry()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(QRectF(self._geometry.seam), holo.LINE_FAINT)
            if self.turns.hasFocus():            # F6 brought the keyboard here: the conversation's ring
                holo.paint_focus_ring(painter, QRectF(self.turns.geometry()), holo.RADIUS_FRAME)
        finally:
            painter.end()


__all__ = ["BIG_GAP", "BoardHint", "CueLine", "CuePath", "DeskGeometry", "DeskPage", "DeskTurns", "FramesToken",
           "HINT_KEY", "HINT_TEXT", "READ_EASE_MS", "RingFlight", "RingHead", "TextBlock", "Turn", "TurnView", "Veil",
           "desk_geometry", "earlier_text", "highlights", "read_overlay", "token_text"]
