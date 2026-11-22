"""Home as the canvas (PLAN-canvas part 1): the native half. The board's page is the whole of Home - one black surface
with the orb and the corners' controls drawn in it (board/assets) - and three native things stand over it:

    CanvasPage              Home's page in canvas mode (ui.ReadingView shows it in Home's place). It holds
                              * the board's one view (board.view.BoardView), full-bleed, under everything else;
                              * a column at the bottom centre, bottom up: the ask line (hud.CommandBar in its canvas
                                look, BAR_WIDTH wide), what Jarvis says (home.CaptionView in its canvas type) and the
                                chip row (a task's chip and its Stop, a timer, the answer just given);
                              * ONE tile at the left (an open offer's, else an error's), at the top of the frames'
                                area in a holder of its own: the page is told its box (``side_box``, canvas_ui's
                                "side") and never puts a frame, the task card or a group's head under it; while a
                                frame is open big the holder hides (``set_side_covered``): it never covers it.
                            Until the page is up it is plain black with the ask line: the window shows at once and the
                            canvas takes over when its page is ready, with nothing flashing (the view's own ground is
                            the same black).
    orb_form(state, frames, lingering)
                            the orb's form (board.protocol.ORB_FORMS) from what the app knows: the corner while the
                            board shows frames; the badge while Jarvis speaks (and a moment after: ``lingering``); the
                            rings otherwise. Qt-free.
    orb_levels(mic, voice)  the two levels as the page takes them: whole numbers 0 to LEVEL_MAX. Qt-free.

Nothing here talks to the page: canvas_ui.CanvasController does, with board.protocol's checked messages.
"""

from __future__ import annotations

from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .board import protocol

GROUND = "#000000"            # the canvas (the mock's ``.cv``)
EDGE = 28                     # its side gutter, as the page's --edge
BOTTOM = 20                   # under the ask line (the mock: 22 px to the line; the field keeps a 2 px halo)
BAR_WIDTH = 524               # the ask line: the mock's 520 px and its halo
COLUMN_WIDTH = 640            # the caption's width (the mock's ``.cap`` box)
COLUMN_GAP = 10               # between the caption and the ask line
COLUMN_HEIGHT = 196           # room for the chip row, two caption lines, a status line and the ask line
ORB_CORNER_INSET = 112        # the orb's centre in its corner, from the page's bottom right (board.js CORNER_INSET) ...
ORB_CORNER_REACH = 104        # ... and its reach from there, glow included (the toast keeps clear of it)
SIDE_TOP = 64                 # the one tile at the left: at the top of the frames' area (the page's BOARD_TOP) ...
SIDE_WIDTH = 290              # ... this wide at least (its buttons' words always whole: ``fit_width``)

_SPEAKING = "speaking"


def orb_form(state: str, frames: bool, lingering: bool = False) -> str:
    """The orb's form: "corner" while the board shows frames, "badge" while Jarvis speaks (and while ``lingering``:
    the moment after, so it never snaps back mid-thought), else "rings"."""
    if frames:
        return "corner"
    if state == _SPEAKING or lingering:
        return "badge"
    return "rings"


def orb_corner(width: int, height: int) -> QRect:
    """The orb's box in its corner on a page ``width`` x ``height`` (page px; canvas part 5B: what the toast avoids)."""
    side = 2 * ORB_CORNER_REACH
    return QRect(width - ORB_CORNER_INSET - ORB_CORNER_REACH, height - ORB_CORNER_INSET - ORB_CORNER_REACH, side, side)


def orb_levels(mic: float | None, voice: float | None) -> tuple[int, int]:
    """The microphone's and the voice's levels (0..1, None unknown) as the page takes them: 0 to LEVEL_MAX."""
    def whole(value: float | None) -> int:
        try:
            number = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0
        if number != number:                      # not a number
            return 0
        return max(0, min(protocol.LEVEL_MAX, round(number * protocol.LEVEL_MAX)))

    return whole(mic), whole(voice)


class _Column(QWidget):
    """The bottom-centre column: see-through, laid out bottom up; it never paints and never takes the focus."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("canvasColumn")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)


class _SideHolder(QWidget):
    """The tile's own holder: see-through; hidden while a frame is open big (the tile never covers it)."""

    def __init__(self, page: "CanvasPage") -> None:
        super().__init__(page)
        self.setObjectName("canvasSide")
        self._page = page
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def place_side_tile(self) -> None:          # (states_view.ErrorTile asks its parent after its help folds)
        self._page.place_side_tile()


class CanvasPage(QWidget):
    """See the module doc."""

    is_canvas = True
    sideChanged = Signal()                       # the tile's box changed (shown, gone, resized): the page is told

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("canvasPage")
        self.setAccessibleName("Home")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._board: QWidget | None = None
        self._side: QWidget | None = None
        self._side_covered = False
        self._side_sent: tuple[int, int] = (0, 0)
        self.side_holder = _SideHolder(self)
        self.side_holder.hide()
        self.column = _Column(self)
        layout = QVBoxLayout(self.column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)
        self.chip_slot = QWidget(self.column)
        self.chip_slot.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        chips = QHBoxLayout(self.chip_slot)
        chips.setContentsMargins(0, 0, 0, 0)
        self.caption_slot = QWidget(self.column)
        self.caption_slot.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.caption_slot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        caption = QVBoxLayout(self.caption_slot)
        caption.setContentsMargins(0, 0, 0, 0)
        self.ask_slot = QWidget(self.column)
        self.ask_slot.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        ask = QHBoxLayout(self.ask_slot)
        ask.setContentsMargins(0, 0, 0, 0)
        ask.setSpacing(0)
        ask.addStretch(1)                        # the ask line goes between the two (take_bar)
        ask.addStretch(1)
        layout.addWidget(self.chip_slot)
        layout.addWidget(self.caption_slot)
        layout.addSpacing(COLUMN_GAP)
        layout.addWidget(self.ask_slot)

    # ---- what stands on the canvas ---------------------------------------------------------------------------------

    def set_board(self, view: QWidget | None) -> None:
        """The board's one view: the whole page, under the column and the tile. None: it leaves (the page could not
        start: Home goes back to its native ring)."""
        old = self._board
        if old is not None and old is not view and shiboken6.isValid(old) and old.parentWidget() is self:
            old.hide()
        self._board = view
        if view is None:
            return
        if view.parentWidget() is not self:
            view.setParent(self)
        view.setGeometry(self.rect())
        view.lower()
        view.show()

    def board(self) -> QWidget | None:
        return self._board

    def take_bar(self, bar: QWidget) -> None:
        """The ask line, centred, BAR_WIDTH wide at most."""
        bar.setMaximumWidth(BAR_WIDTH)
        self.ask_slot.layout().insertWidget(1, bar, 1000)
        bar.show()

    def take_captions(self, captions: QWidget) -> None:
        self.caption_slot.layout().addWidget(captions)
        captions.show()

    def take_chips(self, row: QWidget) -> None:
        self.chip_slot.layout().addWidget(row)
        row.show()

    def set_side_tile(self, tile: QWidget | None) -> None:
        """The one tile at the left (an offer's, an error's), in its holder at the top of the frames' area; None:
        none."""
        if self._side is not None and self._side is not tile and shiboken6.isValid(self._side):
            self._side.removeEventFilter(self)
        self._side = tile
        if tile is not None:
            if tile.parentWidget() is not self.side_holder:
                tile.setParent(self.side_holder)     # (a new parent hides it; its owner shows it when it has something)
            tile.installEventFilter(self)
        self.place_side_tile()

    def side_tile(self) -> QWidget | None:
        return self._side

    def _side_width(self, tile: QWidget) -> int:
        """SIDE_WIDTH, or what its buttons need to stay whole (``fit_width``), never wider than the page's room."""
        fit = getattr(tile, "fit_width", None)
        need = int(fit()) if callable(fit) else 0
        return max(1, min(max(SIDE_WIDTH, need), max(1, self.width() - 2 * EDGE)))

    def place_side_tile(self) -> None:
        tile = self._side
        holder = self.side_holder
        if tile is None or not shiboken6.isValid(tile) or tile.parentWidget() is not holder:
            holder.hide()
            self._say_side()
            return
        width = self._side_width(tile)
        height = tile.heightForWidth(width) if tile.hasHeightForWidth() else tile.sizeHint().height()
        height = max(1, min(height, self.height() - SIDE_TOP - COLUMN_HEIGHT))
        holder.setGeometry(EDGE, SIDE_TOP, width, height)
        tile.setGeometry(0, 0, width, height)
        holder.setVisible(not tile.isHidden() and not self._side_covered)
        holder.raise_()
        self._say_side()

    def side_box(self) -> tuple[int, int]:
        """(width, height) of the tile the page must keep its frames clear of, in px from the frames' area's top left;
        (0, 0) while no tile shows (also while it is covered by open big: its room is kept)."""
        tile = self._side
        if tile is None or not shiboken6.isValid(tile) or tile.parentWidget() is not self.side_holder:
            return 0, 0
        if tile.isHidden():
            return 0, 0
        geometry = self.side_holder.geometry()
        return max(0, geometry.width()), max(0, geometry.height())

    def set_side_covered(self, covered: bool) -> None:
        """A frame is open big (True): the tile's holder hides - it never covers the big frame - and comes back
        after."""
        covered = bool(covered)
        if covered != self._side_covered:
            self._side_covered = covered
            self.place_side_tile()

    def side_covered(self) -> bool:
        return self._side_covered

    def _say_side(self) -> None:
        box = self.side_box()
        if box != self._side_sent:
            self._side_sent = box
            self.sideChanged.emit()

    # ---- geometry --------------------------------------------------------------------------------------------------

    def column_rect(self) -> QRect:
        width = max(240, min(COLUMN_WIDTH, self.width() - 2 * EDGE))
        height = min(COLUMN_HEIGHT, max(80, self.height() - 2 * EDGE))
        return QRect((self.width() - width) // 2, self.height() - BOTTOM - height, width, height)

    def place(self) -> None:
        board = self._board
        if board is not None and shiboken6.isValid(board) and board.parentWidget() is self:
            board.setGeometry(self.rect())
            board.lower()
        self.column.setGeometry(self.column_rect())
        self.column.raise_()
        self.place_side_tile()

    # ---- Qt --------------------------------------------------------------------------------------------------------

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override
        if watched is self._side and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.LayoutRequest):
            QTimer.singleShot(0, self._place_if_valid)
        return super().eventFilter(watched, event)

    def _place_if_valid(self) -> None:
        if shiboken6.isValid(self):
            self.place_side_tile()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.place()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.place()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(GROUND))      # the canvas before its page is up, and under it
        finally:
            painter.end()


__all__ = ["BAR_WIDTH", "BOTTOM", "COLUMN_HEIGHT", "COLUMN_WIDTH", "CanvasPage", "EDGE", "GROUND", "SIDE_TOP",
           "SIDE_WIDTH", "orb_form", "orb_levels"]
