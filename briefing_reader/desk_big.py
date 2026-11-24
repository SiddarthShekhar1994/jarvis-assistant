"""Open big (the new look, part 5c; DESIGN-SPEC 3.5): a frame grows to the whole of both columns. A CSS transform
can't leave the board's view and an animated view geometry re-rasters Chromium on every frame, so it runs in two steps:

1. **The geometry, once.** The app paints the conversation column as it looks (desk_view.DeskPage.column_image: the
   window's ground, the ring head, the turns, the chips; never the view) and sends it as a ``backdrop`` - one JPEG
   within the picture's caps -, sends ``big`` open (so the page keeps its board's layout as it was while the view is
   wide), then sets the view's geometry ONCE, with no animation, to span both columns (from the column's left edge to
   the board's right edge, from the body's top to 12 px above the ask bar: the ask bar stays live outside it). A still
   of the window (desk_view.Veil) stands over that place until the board's view itself shows the page laid out at the
   new size - its own pixels (view_shows), checked after each of its renders, on each of the page's ``big`` "laid" and
   on a 16 ms beat - or VEIL_MS: Qt keeps showing the view's old frame at its new size for a while after the page is
   laid out. A step asked while the view still holds the other step's frame (an open right after a close's "shut", a
   close while the open's still is up: the view already shows this step's end before its geometry is set) is judged
   only from a render of its own start on, so that old frame never lifts it (counted "armed:<step>"; the cap when that
   render never comes). With no web view (a stand-in page) or no backdrop sent, the page's "laid" lifts it. Nothing
   jumps. The Dock and the header dim to 20 % natively; the cue line's native part goes at once, and the covered
   conversation takes no focus and no click (desk_view.DeskPage.set_big) until it is back.
2. **The scale, in the page.** The frame grows from its rectangle to the whole view (260 ms expo-out, transform and
   opacity only) while the backdrop and the other frames dim to 20 % - held, unseen, until then, so the view never
   shows it at its full size first; its footer says "ESC BACK TO THE BOARD" and "OPEN BIG" joins its meta.

Esc (or a click beside the big frame) reverses it: the page's scale-down first (220 ms; it posts ``big`` "back" as it
starts and "shut" when it ends), then the geometry is set back once, then the native dim lifts. Motion off: both steps
at once; calm x1.6. A resize of the window while big keeps it spanning both columns (the backdrop is painted again).
It ends at once (no animation) when the desk leaves Home, the window goes to another screen, or the page restarts or
falls back; and goes back by itself when its frame leaves the board.

The canvas (PLAN-canvas part 1): the board's view is the whole of Home already, so step 1 falls away - no backdrop, no
still, no geometry, no native dim (the page dims its own orb and corners with its board): ``big`` open is sent with the
view's own size (the page keeps the board's box it has), and the page's scale and its way back are step 2 as above.

``BigController(desk)`` is desk_ui.DeskController's (``desk.big``): ``open(frame_id)`` (the page's Enter, a double
click, the frame's Open big button), ``on_step(step)`` (the page's big request), ``on_layout()`` (the desk's layout
changed), ``check()`` (the board changed), ``close()``, ``end_now()``; ``frame()`` / ``state()``. ``view_shows(view,
wide=...)``: the still's check of the view's own pixels.

Logs: ids and counts only ("Board: frame f3 open big"), never a frame's words.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from typing import Any

import shiboken6
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QRegion
from PySide6.QtWidgets import QWidget

from . import holo
from .board import protocol

logger = logging.getLogger(__name__)

DIM = 0.2                    # the Dock and the header while big (DESIGN-SPEC 3.5), and the page's backdrop and frames
OPEN_MS = 260                # the page's scale up (the native dim eases with it) ...
CLOSE_MS = 220               # ... and down
LIFT_MS = 160                # the native dim lifts after the geometry is back
VEIL_MS = 400                # the still goes when the view shows the page laid out at its new size, or after this
EDGE_ROWS = 8                # view_shows: the view's own pixels at x = 1, this many rows ...
EDGE_NEED = 6                # ... this many as the new layout has them (a cue stub, a lit bracket or its shadow may cross
                             # a row or two of the board's pad)
EDGE_OPAQUE = 250            # wide (open big): the backdrop there, at least this alpha ...
EDGE_CLEAR = 16              # ... at its own size (back): the board's pad, at most this
EDGE_POLL_MS = 16            # the check's own beat while a still is up (beside the view's renders)
SHUT_GRACE_MS = 400          # the page's "shut" comes within its scale-down and this, else the app goes back itself
JPEG_QUALITIES = (82, 70, 56, 42)
SHRINK = 0.75                # a backdrop over the caps at every quality is painted this much smaller, then again
CALM = 1.6


def _ms(ms: int) -> int:
    level = holo.motion_level()
    if level == holo.MOTION_OFF:
        return 0
    return round(ms * (CALM if level == holo.MOTION_CALM else 1.0))


def view_shows(view: QWidget, *, wide: bool) -> bool:
    """The board's web view shows the page laid out at its new size: its OWN pixels - QWidget.render with its children
    into a clear image, never a window grab (that paints the window's ground and the column's widgets under the
    translucent view, and the clear parts of an old frame would look like the backdrop) - at x = 1, EDGE_ROWS rows.
    ``wide`` (open big): the backdrop is there (opaque); else (its own size again) the board's pad is clear there.
    EDGE_NEED rows of them must say so."""
    height = view.height()
    if height < EDGE_ROWS or view.width() < 2:
        return False
    ratio = view.devicePixelRatioF() or 1.0
    image = QImage(max(1, round(ratio)), max(1, round(height * ratio)), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    view.render(image, QPoint(), QRegion(QRect(1, 0, 1, height)), QWidget.RenderFlag.DrawChildren)
    alphas = [image.pixelColor(0, min(image.height() - 1, int((row + 0.5) * image.height() / EDGE_ROWS))).alpha()
              for row in range(EDGE_ROWS)]
    if wide:
        return sum(1 for alpha in alphas if alpha >= EDGE_OPAQUE) >= EDGE_NEED
    return sum(1 for alpha in alphas if alpha <= EDGE_CLEAR) >= EDGE_NEED


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
        self._laid_for = ""                  # the page's "laid" still to come: "open" (the frame grown in the wide
                                             # view), "close" (the board back at its size) or "" (none: one is ignored)
        self._still = ""                     # the step the still stands for while it is up ("open", "close")
        self._judged = False                 # it goes by the view's own pixels (view_shows), else by the page's "laid"
        self._armed = True                   # the view's check counts: False while the view may still show the other
                                             # step's frame - until a render shows this step's own start
        self._backdrop_sent = False          # the page has a backdrop (what tells the wide layout from the old frame)
        self._watched: Any = None            # the view's Qt Quick window whose renders the check follows
        self._veil_timer = QTimer(self)
        self._veil_timer.setSingleShot(True)
        self._veil_timer.timeout.connect(self._veil_late)
        self._edge_timer = QTimer(self)      # the check's own beat while a still is up
        self._edge_timer.setInterval(EDGE_POLL_MS)
        self._edge_timer.timeout.connect(self._check_view)
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

    def _canvas(self) -> bool:
        """Home is the canvas (ui.ReadingView.canvas_on): the board's view is the whole of Home, no desk page."""
        canvas_on = getattr(self.reading, "canvas_on", None)
        return bool(canvas_on()) if callable(canvas_on) else False

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
        canvas = self._canvas()
        if self._state:
            self.counts["refused:busy"] += 1
            return False
        if board is None or not shiboken6.isValid(board) or not board.is_ready() or not self.desk.on_screen() \
                or (not canvas and (page is None or page.board() is not board)):
            self.counts["refused:not_shown"] += 1
            return False
        if not self._shown(frame_id):
            self.counts["refused:frame"] += 1
            return False
        if canvas:
            # The canvas: the view is the whole of Home already - only the page's own step (see the module doc).
            self._backdrop_sent = False
            self._laid_for, self._still, self._judged = "open", "", False
            self.desk.cue.clear(now=True)
            self._frame, self._state = frame_id, "open"
            self.reading.set_board_big(True)
            self._box = (0, 0, max(1, int(board.width())), max(1, int(board.height())))
            x, y, w, h = self._box
            self.desk._send([{"type": "big", "state": "open", "id": frame_id, "x": x, "y": y, "w": w, "h": h}])
            self.desk.sound("on_big", True)
            self.counts["opened"] += 1
            logger.info("Board: frame %s open big", frame_id)
            return True
        geometry = page.geometry_now()
        src = self._backdrop(page, geometry)
        if src:
            self.desk._send([{"type": "backdrop", "src": src}])
        else:
            self.counts["backdrop:none"] += 1
        self._backdrop_sent = bool(src)
        self._hold("open", page, geometry.big.united(geometry.view))   # the still, until the view shows it wide
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

    def _veil(self, page: Any, rect: QRect) -> bool:
        """A still of the window over ``rect`` (the desk's px) until the page is laid out at its new size; False when
        none could be made (the window not shown)."""
        window = page.window()
        if window is None or not window.isVisible():
            return False
        try:
            origin = page.mapTo(window, rect.topLeft())
            still = window.grab(QRect(origin, rect.size()))
        except Exception as exc:  # noqa: BLE001 - no still: the page's own first frame then
            logger.debug("Board: no still for open big (%s)", type(exc).__name__)
            return False
        if still.isNull():
            return False
        page.veil.show_still(still, rect)
        self._veil_timer.start(VEIL_MS)
        return True

    def _hold(self, step: str, page: Any, rect: QRect) -> None:
        """``step`` (open, close) begins: the page's "laid" for it is to come, and a still stands over ``rect`` until
        the view shows the page laid out at its new size - judged by the view's own pixels when it can be (a web view,
        a backdrop sent), else lifted by that "laid". Called before the geometry is set: a view that already shows
        this step's end (it still holds the other step's frame - an open asked right after a close's "shut", a close
        while the open's still is up) is armed only once a render shows this step's own start (the opposite edge), so
        that old frame never lifts the still; a normal step is armed at once."""
        self._laid_for = step
        self._edge_timer.stop()
        self._still, self._judged = "", False
        view = self._web_view() if self._backdrop_sent else None
        self._armed = view is None or not view_shows(view, wide=step == "open")
        if self._veil(page, rect):
            self._still = step
            self._judged = self._backdrop_sent and self._watch()
        if not self._judged:
            self._unhook()

    def _web_view(self) -> Any:
        """The board's real web view (None for a stand-in page or a board that fell back)."""
        board = self.desk.board
        view = board.web_view() if board is not None and shiboken6.isValid(board) else None
        if view is None or not shiboken6.isValid(view) or not view.inherits("QWebEngineView"):
            return None
        return view

    def _watch(self) -> bool:
        """The still is judged by the view's own pixels after each of its renders (its focus proxy is a QQuickWidget:
        that window's afterRendering, queued: the check reads what Qt just made) and on EDGE_POLL_MS, the safety net.
        False with no web view: the page's "laid" lifts the still, as before."""
        view = self._web_view()
        if view is None:
            return False
        window = None
        try:
            from PySide6.QtQuickWidgets import QQuickWidget

            proxy = view.focusProxy()
            if isinstance(proxy, QQuickWidget):
                window = proxy.quickWindow()
        except Exception:  # noqa: BLE001 - its beat alone then (at most EDGE_POLL_MS later)
            window = None
        if window is not None and window is not self._watched:
            self._unhook()
            window.afterRendering.connect(self._check_view, Qt.ConnectionType.QueuedConnection)
            self._watched = window
        self._edge_timer.start()
        return True

    def _unhook(self) -> None:
        window, self._watched = self._watched, None
        if window is not None and shiboken6.isValid(window):
            try:
                window.afterRendering.disconnect(self._check_view)
            except (RuntimeError, TypeError):
                pass

    def _check_view(self) -> None:
        """The still goes once the board's view itself shows the page laid out at its new size: wide for the open (the
        backdrop at its left edge), its own size for the close (the board's pad clear there) - view_shows. Not armed
        yet (the view showed this step's end before its geometry was set: the other step's frame), it first waits for
        a render of this step's own start."""
        if not self._judged:
            return
        page = self._page()
        view = self._web_view()
        if page is None or view is None or not page.veil.shown():
            self._lift_veil()                            # nothing to judge any more: nothing left running
            return
        wide = self._still == "open"
        if not self._armed:
            if view_shows(view, wide=not wide):          # this step's own start: from here a pass counts
                self._armed = True
                self.counts[f"armed:{self._still}"] += 1
            return
        if view_shows(view, wide=wide):
            self.counts[f"shown:{self._still}"] += 1
            self._lift_veil()

    def _veil_late(self) -> None:
        """VEIL_MS passed with the still up: it goes all the same (counted, by its step)."""
        step = self._still or self._laid_for
        if step:
            self.counts[f"veil_late:{step}"] += 1
        self._lift_veil()

    def _lift_veil(self) -> None:
        """The still goes, and its check with it: its beat, its time limit and its link to the view's renders."""
        self._veil_timer.stop()
        self._edge_timer.stop()
        self._unhook()
        self._still, self._judged, self._armed = "", False, True
        page = self._page()
        if page is not None:
            page.veil.clear()

    # ---- the page's steps ----------------------------------------------------------------------------------------

    def on_step(self, step: str) -> None:
        """The page's big request. Its "laid" answers the step awaited - the open (the frame grown in the wide view)
        or the close (the board back at its size), once each: a "laid" while none is awaited is ignored (counted). The
        page sends a "laid" only for its step still going on (never one for an open it left, or for a close an open
        was asked after), and none for the open after its "back". A still judged by the view's own pixels is checked
        again at once on it (the page is laid out; Qt shows it a little later); else the "laid" lifts it."""
        self.counts[f"step:{step}"] += 1
        if step == "laid":
            if not self._laid_for:
                self.counts["laid:ignored"] += 1
            else:
                self._laid_for = ""
                if self._judged:
                    self._check_view()
                else:
                    self._lift_veil()
        elif step == "back":
            if self._laid_for == "open":
                self._laid_for = ""                      # the open's "laid" never comes now: a still judged by the view
                                                         # goes once it shows the wide layout (the scale-down is seen),
                                                         # else after VEIL_MS
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
        if page is not None and animate:
            geometry = page.geometry_now()
            self._hold("close", page, geometry.big.united(geometry.view))   # until the view shows its own size again
        else:
            self._lift_veil()                            # at once: nothing to hide, nothing left running
        if page is not None:
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
            self._backdrop_sent = True
        self._send_open(geometry)
        self.counts["relaid"] += 1

    def check(self) -> None:
        """The board changed: a big frame that left the board (closed, cleared, gone) goes back."""
        if self._state == "open" and not self._shown(self._frame):
            self.close()

    def laid_for(self) -> str:
        """The page's "laid" still to come ("open", "close"; "" none)."""
        return self._laid_for

    def still_judged(self) -> bool:
        """The still up now goes by the view's own pixels (view_shows), not by the page's word."""
        return self._judged

    def shutdown(self) -> None:
        self._veil_timer.stop()
        self._shut_timer.stop()
        self._edge_timer.stop()
        self._unhook()
        self._still, self._judged, self._armed = "", False, True


__all__ = ["BigController", "CLOSE_MS", "DIM", "EDGE_CLEAR", "EDGE_NEED", "EDGE_OPAQUE", "EDGE_POLL_MS", "EDGE_ROWS",
           "LIFT_MS", "OPEN_MS", "SHUT_GRACE_MS", "VEIL_MS", "jpeg_url", "view_shows"]
