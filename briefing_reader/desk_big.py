"""Open big (the new look, part 5c; DESIGN-SPEC 3.5): a frame grows to the whole of both columns. A CSS transform
can't leave the board's view and an animated view geometry re-rasters Chromium on every frame, so it runs in two steps:

1. **The geometry, once.** The app paints the conversation column as it looks (desk_view.DeskPage.column_image: the
   window's ground, the ring head, the turns, the chips; never the view) and sends it as a ``backdrop`` - one JPEG
   within the picture's caps -, sends ``big`` open (so the page keeps its board's layout as it was while the view is
   wide), then sets the view's geometry ONCE, with no animation, to span both columns (from the column's left edge to
   the board's right edge, from the body's top to 12 px above the ask bar: the ask bar stays live outside it). A still
   of the window (desk_view.Veil) stands over that place until the page says it is laid out at the new size (``big``
   "laid", or VEIL_MS), so nothing jumps. The Dock and the header dim to 20 % natively; the cue line's native part goes
   at once, and the covered conversation takes no focus and no click (desk_view.DeskPage.set_big) until it is back.
2. **The scale, in the page.** The frame grows from its rectangle to the whole view (260 ms expo-out, transform and
   opacity only) while the backdrop and the other frames dim to 20 %; its footer says "ESC BACK TO THE BOARD" and
   "OPEN BIG" joins its meta.

Esc (or a click beside the big frame) reverses it: the page's scale-down first (220 ms; it posts ``big`` "back" as it
starts and "shut" when it ends), then the geometry is set back once, then the native dim lifts. Motion off: both steps
at once; calm x1.6. A resize of the window while big keeps it spanning both columns (the backdrop is painted again).
It ends at once (no animation) when the desk leaves Home, the window goes to another screen, or the page restarts or
falls back; and goes back by itself when its frame leaves the board.

``BigController(desk)`` is desk_ui.DeskController's (``desk.big``): ``open(frame_id)`` (the page's Enter, a double
click, the frame's Open big button), ``on_step(step)`` (the page's big request), ``on_layout()`` (the desk's layout
changed), ``check()`` (the board changed), ``close()``, ``end_now()``; ``frame()`` / ``state()``.

Logs: ids and counts only ("Board: frame f3 open big"), never a frame's words.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from typing import Any

import shiboken6
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QImage

from . import holo
from .board import protocol

logger = logging.getLogger(__name__)

DIM = 0.2                    # the Dock and the header while big (DESIGN-SPEC 3.5), and the page's backdrop and frames
OPEN_MS = 260                # the page's scale up (the native dim eases with it) ...
CLOSE_MS = 220               # ... and down
LIFT_MS = 160                # the native dim lifts after the geometry is back
VEIL_MS = 400                # the still goes when the page is laid out at its new size, or after this
SHUT_GRACE_MS = 400          # the page's "shut" comes within its scale-down and this, else the app goes back itself
JPEG_QUALITIES = (82, 70, 56, 42)
SHRINK = 0.75                # a backdrop over the caps at every quality is painted this much smaller, then again
CALM = 1.6


def _ms(ms: int) -> int:
    level = holo.motion_level()
    if level == holo.MOTION_OFF:
        return 0
    return round(ms * (CALM if level == holo.MOTION_CALM else 1.0))


def jpeg_url(image: QImage) -> str:
    """``image`` as ONE JPEG data: URL within the board's picture caps (protocol.jpeg_src passes it), trying lower
    qualities, then a smaller image; "" when it can't be made."""
    work = image
    for _round in range(4):
        for quality in JPEG_QUALITIES:
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            ok = work.save(buffer, "JPG", quality)
            buffer.close()
            jpeg = bytes(data.data())
            if not ok or len(jpeg) > protocol.PICTURE_BYTES:
                continue
            src = protocol.PICTURE_PREFIX + base64.b64encode(jpeg).decode("ascii")
            try:
                return protocol.jpeg_src(src)
            except protocol.Refused:
                continue
        width, height = max(1, round(work.width() * SHRINK)), max(1, round(work.height() * SHRINK))
        work = work.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    return ""


class BigController(QObject):
    """See the module doc."""

    def __init__(self, desk: Any) -> None:
        super().__init__(desk)
        self.desk = desk
        self.reading = desk.reading
        self._frame = ""
        self._state = ""                     # "" none, "open", "closing" (the page's scale-down runs)
        self._box: tuple[int, int, int, int] | None = None
        self._laid_for = ""                  # the page's "laid" the still waits for: "open" (the frame grown in the
                                             # wide view), "close" (the board back at its size) or "" (none: ignored)
        self._veil_timer = QTimer(self)
        self._veil_timer.setSingleShot(True)
        self._veil_timer.timeout.connect(self._lift_veil)
        self._shut_timer = QTimer(self)
        self._shut_timer.setSingleShot(True)
        self._shut_timer.timeout.connect(self._on_shut_late)
        self.counts: Counter[str] = Counter()

    # ---- reading -------------------------------------------------------------------------------------------------

    def frame(self) -> str:
        return self._frame

    def state(self) -> str:
        return self._state

    def _page(self) -> Any:
        page = self.reading.desk_page()
        return page if page is not None and shiboken6.isValid(page) else None

    def _shown(self, frame_id: str) -> bool:
        feed = self.desk.feed
        flags = feed.flags(frame_id)
        return bool(flags) and not flags.get("closed") and not flags.get("cleared") \
            and not feed.is_parked(feed.group_of(frame_id))

    # ---- opening -------------------------------------------------------------------------------------------------

    def open(self, frame_id: str) -> bool:
        """The page asked for ``frame_id`` big: step 1 here (the backdrop, the still, the geometry once, the native
        dim), then ``big`` open for the page's step 2. False when it can't be now (counted)."""
        board = self.desk.board
        page = self._page()
        if self._state:
            self.counts["refused:busy"] += 1
            return False
        if board is None or not shiboken6.isValid(board) or not board.is_ready() or page is None \
                or page.board() is not board or not self.desk.on_screen():
            self.counts["refused:not_shown"] += 1
            return False
        if not self._shown(frame_id):
            self.counts["refused:frame"] += 1
            return False
        geometry = page.geometry_now()
        src = self._backdrop(page, geometry)
        if src:
            self.desk._send([{"type": "backdrop", "src": src}])
        else:
            self.counts["backdrop:none"] += 1
        self._veil(page, geometry.big.united(geometry.view))
        self._laid_for = "open"                          # the still goes with the page's "laid" for this open
        self.desk.cue.clear(now=True)                    # the column is covered: nothing lit, the native part gone at once
        self._frame, self._state = frame_id, "open"
        self.reading.set_board_big(True)
        page.chip_slot.hide()                            # the chips' bottom edge would show under the wide view
        self._send_open(geometry)                        # the page knows before its view is wide (its board keeps its
        page.set_big(True)                               # layout) - then the geometry, once (never animated)
        self.reading.set_chrome_dim(DIM, _ms(OPEN_MS))
        self.desk.sound("on_big", True)
        self.counts["opened"] += 1
        logger.info("Board: frame %s open big", frame_id)
        return True

    def _box_of(self, geometry: Any) -> tuple[int, int, int, int]:
        big, view = geometry.big, geometry.view
        return view.x() - big.x(), view.y() - big.y(), view.width(), view.height()

    def _send_open(self, geometry: Any) -> None:
        x, y, w, h = self._box = self._box_of(geometry)
        self.desk._send([{"type": "big", "state": "open", "id": self._frame, "x": x, "y": y, "w": w, "h": h}])

    def _backdrop(self, page: Any, geometry: Any) -> str:
        big, view = geometry.big, geometry.view
        rect = QRect(big.x(), big.y(), max(1, view.x() - big.x()), max(1, big.height()))
        chips = page.chip_slot
        hidden = chips.isHidden()
        if hidden:
            chips.show()                                 # (while big they hide: the backdrop has them all the same)
        try:
            image = page.column_image(rect, self.reading.ground_painter())
        except Exception as exc:  # noqa: BLE001 - no backdrop rather than no open big
            logger.debug("Board: the backdrop was not painted (%s)", type(exc).__name__)
            return ""
        finally:
            if hidden:
                chips.hide()
        return jpeg_url(image)

    def _veil(self, page: Any, rect: QRect) -> None:
        """A still of the window over ``rect`` (the desk's px) until the page is laid out at its new size."""
        window = page.window()
        if window is None or not window.isVisible():
            return
        try:
            origin = page.mapTo(window, rect.topLeft())
            still = window.grab(QRect(origin, rect.size()))
        except Exception as exc:  # noqa: BLE001 - no still: the page's own first frame then
            logger.debug("Board: no still for open big (%s)", type(exc).__name__)
            return
        if still.isNull():
            return
        page.veil.show_still(still, rect)
        self._veil_timer.start(VEIL_MS)

    def _lift_veil(self) -> None:
        self._veil_timer.stop()
        self._laid_for = ""
        page = self._page()
        if page is not None:
            page.veil.clear()

    # ---- the page's steps ----------------------------------------------------------------------------------------

    def on_step(self, step: str) -> None:
        """The page's big request. Its "laid" answers the step the still waits for - the open (the frame grown in the
        wide view) or the close (the board back at its size): a "laid" while none is awaited is ignored (counted). The
        page sends a "laid" only for its step still going on (never one for an open it left, or a close an open
        followed), and none for the open after its "back", so each one answers the step awaited then."""
        self.counts[f"step:{step}"] += 1
        if step == "laid":
            if self._laid_for:
                self._lift_veil()
            else:
                self.counts["laid:ignored"] += 1
        elif step == "back":
            if self._laid_for == "open":
                self._laid_for = ""                      # the open's "laid" never comes now (the still: VEIL_MS)
            if self._state == "open":
                self._closing()
        elif step == "shut" and self._state:
            self._finish(animate=True)

    def _closing(self) -> None:
        self._state = "closing"
        self.desk.sound("on_big", False)
        self._shut_timer.start(_ms(CLOSE_MS) + SHUT_GRACE_MS)

    def _on_shut_late(self) -> None:
        if self._state:
            self.counts["shut_late"] += 1
            self._finish(animate=True)

    def close(self) -> None:
        """Back to the board with the page's scale-down (the app's own reason: its frame left the board)."""
        if self._state != "open":
            return
        self.desk._send([{"type": "big", "state": "close", "id": ""}])
        self._closing()

    def end_now(self) -> None:
        """Back at once, no motion (the desk left Home, the page restarted or fell back)."""
        if not self._state:
            return
        self.counts["ended"] += 1
        board = self.desk.board
        if board is not None and shiboken6.isValid(board) and board.is_ready():
            self.desk._send([{"type": "big", "state": "close", "id": ""}])
        self._finish(animate=False)

    def _finish(self, *, animate: bool) -> None:
        """The page's scale-down is over: the geometry is set back once, then the native dim lifts."""
        self._shut_timer.stop()
        self._laid_for = ""
        page = self._page()
        if page is not None:
            if animate:
                geometry = page.geometry_now()
                self._veil(page, geometry.big.united(geometry.view))
                self._laid_for = "close"                 # the still goes with the page's "laid" at its own size
            page.set_big(False)
            page.chip_slot.show()
        self.reading.set_board_big(False)
        self.reading.set_chrome_dim(1.0, _ms(LIFT_MS) if animate else 0)
        logger.info("Board: frame %s back on the board", self._frame or "-")
        self._frame, self._state, self._box = "", "", None
        self.desk.cue.refresh()

    # ---- the desk's changes --------------------------------------------------------------------------------------

    def on_layout(self) -> None:
        """The desk's layout changed while big (the window resized, the Dock folded): it keeps spanning both columns
        (the desk set the geometry once for the new layout); the page gets the new backdrop and box, no animation."""
        if self._state != "open":
            return
        page = self._page()
        if page is None:
            return
        geometry = page.geometry_now()
        if self._box_of(geometry) == self._box:
            return
        src = self._backdrop(page, geometry)
        if src:
            self.desk._send([{"type": "backdrop", "src": src}])
        self._send_open(geometry)
        self.counts["relaid"] += 1

    def check(self) -> None:
        """The board changed: a big frame that left the board (closed, cleared, gone) goes back."""
        if self._state == "open" and not self._shown(self._frame):
            self.close()

    def laid_for(self) -> str:
        """The page's "laid" the still waits for now ("open", "close"; "" none)."""
        return self._laid_for

    def shutdown(self) -> None:
        self._veil_timer.stop()
        self._shut_timer.stop()


__all__ = ["BigController", "CLOSE_MS", "DIM", "LIFT_MS", "OPEN_MS", "SHUT_GRACE_MS", "VEIL_MS", "jpeg_url"]
