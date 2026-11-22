"""The board's host (the new look, part 3; PLAN section 3, DESIGN-SPEC sections 3, 5 and 8): ONE locked-down web view
inside the main window, on the page snapshots' web engine, showing Jarvis's own page of frames, and the native tile in
its place when it can't be.

    BoardView(parent)     the board. start() (also on the first show) starts it START_DELAY_MS later, never on the
                          window's first paint: Chromium's first start holds the GUI thread for a second or more. In:
                          apply(message) (board.protocol.check_message: typed, size-capped, checked again by the page;
                          queued until the page is ready), set_motion(level). Out: one typed signal per request the page
                          made and Python accepted - ready, layoutChanged(BoardLayout), frameFocused(id),
                          openRequested(id), closeRequested(id), pinRequested(id, on), bringBackRequested(id),
                          approvalsRequested(id). When the web engine is missing (no QtWebEngine 6.8+,
                          no scheme), refuses to start (a sandbox turned off, unsafe flags), the page does not say it is
                          ready within READY_TIMEOUT_MS, or its renderer stops more than MAX_RESTARTS times, it shows
                          BoardFallback by itself and emits unavailable(reason): no web view and no renderer are left.
                          Fallen back, apply() still checks each message, and the frames the app sent (also those sent
                          before, kept as the page had them) show as read-only tiles (glance.GlanceTiles) under the
                          fallback tile, which then says READ-ONLY TILES BELOW. Part 5a: the groups (one answer each:
                          their headers and order, the newest on top) and, once the app sent the bar's counts (the
                          desk), the native bar "Board READ-ONLY 9 FRAMES" over the tile; the tiles then keep the
                          frames' 448 px (no index strip). Part 5b: clearRequested(), restoreRequested(),
                          showNewRequested(), readingChanged(pointer, focus), focusOutRequested(to); focus_part(part)
                          (F6: the keyboard focus to the page, then its frames or its index strip); the read-only tiles
                          leave out a parked answer and what a Clear took away. Part 5c: frameLanded(id, step) (a frame
                          drawing in: "outline", then "done"), bigStep(step) (open big: "back", "laid", "shut"); the
                          lit, backdrop and big messages (the fallback's tiles have neither). The canvas, part 1:
                          ``canvas=True`` (or set_canvas before it starts): the page is Home itself - an opaque black
                          ground, so nothing of the window shows through and nothing flashes while it loads; the orb
                          and canvas messages (the newest of each waits for the page, never the queue's room);
                          goRequested(word) (a corner button of the page: board.protocol.GO_WORDS). The window
                          brief: chromeReported(request) (the page's top band: its controls' boxes and the maximise
                          button's, for Windows' hit-test) and the chrome message (the maximise button's hover)
    BoardBridge           the one object the page sees on its web channel: post(kind, payload), checked against
                          board.protocol (the kinds on the list, the frame ids the app sent); anything else dropped and
                          counted (a log line names the kind only when it is on the list, else "unknown")
    BoardFallback         the native tile: "The board can't start on this PC. Answers still show here as text."
    WindowKeysFilter      on the view's focus proxy (re-installed on every focus and when the renderer restarts): the
                          window's keys (WINDOW_KEYS) and F6 / Shift+F6 (CYCLE_KEY: the desk's focus cycle) still fire
                          with the focus in the page
    keeps_space(widget)   True inside the board: Space scrolls it, as in a text field, and never plays or pauses the
                          briefing (ui._keeps_space)

The lockdown, on top of the web engine's own (snapshot_engine.ensure_engine: Chromium's sandbox, every name resolving to
nothing, its proxy at Jarvis's network gate or a dead address, audio muted, GPU off): a profile of its own, off the
record (no cache, no cookie kept, nothing on disk); a request interceptor that lets only the board's own files through
(jarvis-board://board/...: the page itself as the main frame only, and its styles, scripts, fonts and pictures) and the
page's data: pictures, and blocks everything else, counted by scheme; the page's own content security policy (the <meta>
in board.html); web settings from snapshot_engine's lists with local storage off; navigation only to the board when this
view asks for it; no window, dialog, file picker, download, permission (every one denied), clipboard, context menu,
drop or dev tools. The page's console text is counted, never kept or logged.

Logging: kinds, counts, durations and reason words only; never a frame's words, a payload or an address.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, deque
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QRectF, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QVBoxLayout, QWidget

from .. import holo, hud, snapshot_engine, snapshots
from . import protocol, scheme
from .glance import GlanceTiles

logger = logging.getLogger(__name__)

START_DELAY_MS = 300          # after start() (the first show): the window paints before Chromium starts
READY_TIMEOUT_MS = 10_000     # the page says it is ready within this, from its load, else the fallback
MAX_RESTARTS = 2              # renderer stops the board loads the page again after, before it falls back ...
RESTARTS_FORGIVEN_S = 600.0   # ... counted again from none once the page has been ready this long (canvas part 5B)
QUEUE_CAP = 64                # messages kept while the page is not ready yet (the oldest go first)
POSTS_PER_S = 40              # requests of the page a second, at most ...
LAYOUTS_PER_S = 12            # ... layout ones among them (the page sends at most 10; a little slack for timing)
PAD = 15                      # the view's inner pad (DESIGN-SPEC 2.1): the fallback tile keeps the frames' left edge
STRIP = 40                    # the index strip and its gap at the frames' right (the desk; the fallback has no strip)
BAR_HEIGHT = 32               # the board bar on the desk (DESIGN-SPEC 2.1: frames start 44 px down) ...
BAR_ROOM = 50                 # ... and the room it takes over a board that can't start: the tile where the frames start
TILES_GAP = 12                # between the fallback tile and the read-only tiles under it
TILE_PICTURE_PIXELS = 4_000_000   # a read-only tile's picture bigger than this is never decoded (the app's are 960 wide)

CANT_START = "The board can't start on this PC. Answers still show here as text."
READ_ONLY_BELOW = "Read-only tiles below"     # badge role (caps), only with read-only tiles under the tile
BAR_TITLE = "Board"
READ_ONLY = "Read-only"                       # badge role (caps): the bar of a board that can't start

# Why the board fell back (unavailable(reason)), besides snapshots.reason_kind's words.
NO_SCHEME = "no_scheme"       # jarvis-board: was not registered before the QApplication
NO_READY = "no_ready"         # the page did not say it was ready in time
LOAD_FAILED = "load_failed"   # the page did not load
CRASHED = "crashed"           # its renderer stopped more than MAX_RESTARTS times

# The window's keys (the Dock's Ctrl+1 .. Ctrl+9 and Ctrl+, , Find Ctrl+K, Ask Ctrl+Space, mute Ctrl+M, the board's
# Ctrl+B; Ctrl+8 is the Desktop switch): with the focus in the page they still go to the window.
WINDOW_KEYS = (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4, Qt.Key.Key_5, Qt.Key.Key_6, Qt.Key.Key_7,
               Qt.Key.Key_8, Qt.Key.Key_9, Qt.Key.Key_Comma, Qt.Key.Key_K, Qt.Key.Key_Space, Qt.Key.Key_M, Qt.Key.Key_B)
_WINDOW_KEY_CODES = frozenset(int(key.value) for key in WINDOW_KEYS)
# F6 (and Shift+F6 back) cycles the desk's parts: the ask bar, the conversation, the board and its index strip
# (DESIGN-SPEC 3.6). The window's shortcut takes it from the page too; the page is told where the focus goes.
CYCLE_KEY = Qt.Key.Key_F6
# The canvas (PLAN-canvas part 1): F11 is the window's full screen; with the focus in the page it still goes to it.
FULL_KEY = Qt.Key.Key_F11

_IDLE, _WAITING, _LOADING, _READY, _FALLBACK, _SHUT = "idle", "waiting", "loading", "ready", "fallback", "shut"
_OWN_REASONS = frozenset({NO_SCHEME, NO_READY, LOAD_FAILED, CRASHED})
# Address schemes as a log line may name them (anything else is "other": a scheme is the page's choice of words).
_SCHEME_WORDS = frozenset({"http", "https", "ws", "wss", "ftp", "file", "data", "blob", "about", "javascript",
                           "filesystem", "chrome", "qrc", "jarvis-task", scheme.SCHEME})
_REFUSALS = (("fileSystemAccessRequested", "reject"), ("desktopMediaRequested", "cancel"),
             ("webAuthUxRequested", "cancel"), ("registerProtocolHandlerRequested", "reject"),
             ("fullScreenRequested", "reject"), ("certificateError", "rejectCertificate"),
             ("selectClientCertificate", "selectNone"))


@dataclass(frozen=True)
class BoardLayout:
    """Where the frames are in the view (layoutChanged): (id, top, height) each, in px; ``scrolling`` while the board
    scrolls (the cue line hides), then once more when it has stopped."""

    places: tuple[tuple[str, int, int], ...]
    scrolling: bool = False


def is_window_key(key: Any, modifiers: Any) -> bool:
    """True for Ctrl and one of WINDOW_KEYS (the keypad's digits too), nothing else held."""
    try:
        code = int(key.value) if hasattr(key, "value") else int(key)
        held = Qt.KeyboardModifier(modifiers) & ~Qt.KeyboardModifier.KeypadModifier
        return held == Qt.KeyboardModifier.ControlModifier and code in _WINDOW_KEY_CODES
    except Exception:  # noqa: BLE001 - not a key this knows: not the window's
        return False


def is_cycle_key(key: Any, modifiers: Any) -> bool:
    """True for F6 alone or with Shift (the desk's focus cycle, forward and back), nothing else held."""
    try:
        code = int(key.value) if hasattr(key, "value") else int(key)
        held = Qt.KeyboardModifier(modifiers) & ~Qt.KeyboardModifier.KeypadModifier
        return code == int(CYCLE_KEY.value) and held in (Qt.KeyboardModifier.NoModifier,
                                                         Qt.KeyboardModifier.ShiftModifier)
    except Exception:  # noqa: BLE001 - not a key this knows
        return False


def is_full_key(key: Any, modifiers: Any) -> bool:
    """True for F11 alone (the canvas window's full screen)."""
    try:
        code = int(key.value) if hasattr(key, "value") else int(key)
        held = Qt.KeyboardModifier(modifiers) & ~Qt.KeyboardModifier.KeypadModifier
        return code == int(FULL_KEY.value) and held == Qt.KeyboardModifier.NoModifier
    except Exception:  # noqa: BLE001 - not a key this knows
        return False


def _scheme_word(text: Any) -> str:
    word = str(text or "").casefold()
    return word if word in _SCHEME_WORDS else "other"


def _reason_word(reason: str) -> str:
    return reason if reason in _OWN_REASONS else snapshots.reason_kind(reason)


def _alive(item: Any) -> bool:
    try:
        import shiboken6

        return item is not None and bool(shiboken6.isValid(item))
    except Exception:  # noqa: BLE001
        return False


def _event_loop_running() -> bool:
    try:
        return QThread.currentThread().loopLevel() > 0
    except Exception:  # noqa: BLE001
        return True


def _quietly(step: Callable[[], Any]) -> None:
    try:
        step()
    except Exception:  # noqa: BLE001 - an object already gone never stops the next step
        pass


def tile_picture(src: str) -> QImage | None:
    """A picture message's data: URL (checked by board.protocol: one JPEG of at most PICTURE_BYTES) as the image a
    read-only tile draws; None when it is not one, or is bigger than TILE_PICTURE_PIXELS (never decoded then)."""
    try:
        if not isinstance(src, str) or not src.startswith(protocol.PICTURE_PREFIX):
            return None
        import base64

        data = base64.b64decode(src[len(protocol.PICTURE_PREFIX):], validate=True)
        source = QBuffer()
        source.setData(QByteArray(data))
        source.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(source, b"jpeg")
        size = reader.size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0 or \
                size.width() * size.height() > TILE_PICTURE_PIXELS:
            return None
        image = reader.read()
        return None if image.isNull() else image
    except Exception:  # noqa: BLE001 - no picture on the tile rather than an error
        return None


# --------------------------------------------------------------------------
# The bridge: the page's one way back
# --------------------------------------------------------------------------

class BoardBridge(QObject):
    """The one object the page sees on its web channel, as "bridge": one slot, post(kind, payload).

    Each post is checked (board.protocol.check_request: a kind on the list, a small JSON payload of its fields, frame ids
    the app sent - ``known()``) and handed to ``deliver`` as a protocol.Request; anything else is dropped and counted, as
    are posts past POSTS_PER_S a second (LAYOUTS_PER_S for layout). The web channel offers every QObject's public slots,
    deleteLater among them: a deleteLater the page asks for is refused (counted) until release(). Nothing else of
    Jarvis's is on the channel: no property or signal of its own; ``counts`` and ``deliver`` are plain Python."""

    def __init__(self, parent: QObject | None = None, *, known: Callable[[], Collection[str]] = frozenset,
                 deliver: Callable[[protocol.Request], None] | None = None, counts: Counter[str] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._known = known
        self._deliver = deliver
        self._clock = clock
        self._counts: Counter[str] = counts if counts is not None else Counter()
        self._posts: deque[float] = deque()
        self._layouts: deque[float] = deque()
        self._logged: set[tuple[str, str]] = set()
        self._may_delete = False

    def counts(self) -> dict[str, int]:
        """accepted:<kind> / dropped:<kind or unknown> / why:<reason> / refused:delete (counts only)."""
        return dict(self._counts)

    def release(self) -> None:
        """Jarvis's own teardown: from now on the bridge may be deleted later."""
        self._may_delete = True

    def event(self, event: QEvent) -> bool:  # noqa: D102 - Qt override
        if event.type() == QEvent.Type.DeferredDelete and not self._may_delete:
            self._counts["refused:delete"] += 1    # the page called bridge.deleteLater(): the bridge stays
            return True
        return super().event(event)

    @Slot(str, str)
    def post(self, kind: str, payload: str) -> None:
        """A request of the page: checked, then delivered, or dropped and counted. Never raises."""
        word = protocol.kind_word(kind)
        try:
            if not self._admit(kind):
                self._drop(word, "rate")
                return
            request = protocol.check_request(kind, payload, self._known())
        except protocol.Refused as refused:
            self._drop(word, refused.reason)
            return
        except Exception as exc:  # noqa: BLE001 - a post never raises into the channel
            self._drop(word, "error")
            logger.debug("Board: a request was not read (%s)", type(exc).__name__)
            return
        self._counts[f"accepted:{request.kind}"] += 1
        if self._deliver is not None:
            try:
                self._deliver(request)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Board: a request was not handled (%s)", type(exc).__name__)

    def _admit(self, kind: Any) -> bool:
        now = self._clock()
        windows = [(self._posts, POSTS_PER_S)]
        if kind == "layout":
            windows.append((self._layouts, LAYOUTS_PER_S))
        for window, cap in windows:
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= cap:
                return False
        for window, _cap in windows:
            window.append(now)
        return True

    def _drop(self, word: str, reason: str) -> None:
        self._counts[f"dropped:{word}"] += 1
        self._counts[f"why:{reason}"] += 1
        if (word, reason) not in self._logged:   # once each: a page that keeps trying never floods the log
            self._logged.add((word, reason))
            logger.info("Board: a request of the page was dropped (%s, %s)", word, reason)


# --------------------------------------------------------------------------
# The fallback tile
# --------------------------------------------------------------------------

class BoardFallback(QWidget):
    """The native tile in the board's place when its web view can't start (DESIGN-SPEC 3.6): a warning glyph and
    CANT_START, and READ_ONLY_BELOW under it while read-only tiles show below (set_tiles_below; a later part draws
    them). Plain text, painted from holo's tokens; never the focus."""

    GLYPH = 18
    PAD_X, PAD_Y, GAP = 14, 12, 12

    def __init__(self, parent: QWidget | None = None, *, tiles_below: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("boardFallback")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(CANT_START)
        self._words = hud.make_label(CANT_START, holo.font("body_sm"), holo.INK_1, wrap=True, name="boardCantStart")
        self._below = hud.make_label(holo.role_text("badge", READ_ONLY_BELOW), holo.font("badge"), holo.INK_4,
                                     name="boardReadOnlyBelow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.PAD_X + self.GLYPH + self.GAP, self.PAD_Y, self.PAD_X, self.PAD_Y)
        layout.setSpacing(4)
        layout.addWidget(self._words)
        layout.addWidget(self._below)
        self._below.setVisible(bool(tiles_below))

    def words(self) -> tuple[str, str]:
        """(the sentence, the line under it or "" while it is hidden)."""
        return self._words.text(), self._below.text() if not self._below.isHidden() else ""

    def set_tiles_below(self, shown: bool) -> None:
        self._below.setVisible(bool(shown))

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(rect, holo.RADIUS_FRAME, holo.RADIUS_FRAME)
            painter.fillPath(path, QColor(holo.RAISED))
            painter.setPen(QPen(holo.LINE, 1.0))
            painter.drawPath(path)
            holo.paint_icon(painter, QRectF(self.PAD_X, self.PAD_Y + 1, self.GLYPH, self.GLYPH), "warning", holo.STEEL)
        finally:
            painter.end()


class BoardBar(QWidget):
    """The board bar of a board that can't start (DESIGN-SPEC 3.6, ``home-cantstart``): "Board", a READ-ONLY badge and
    "9 FRAMES" (left out in the compact column), painted natively in the page bar's place; nothing in it is a control."""

    GAP = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("boardBar")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(BAR_ROOM)                  # its words in the top BAR_HEIGHT px, as the page's bar
        self._frames = 0
        self._compact = False
        self._sync_name()

    def set_counts(self, frames: int, *, compact: bool = False) -> None:
        self._frames, self._compact = max(0, int(frames)), bool(compact)
        self._sync_name()
        self.update()

    def words(self) -> list[str]:
        """What it says, left to right (as painted: the badges in caps)."""
        shown = [BAR_TITLE, holo.role_text("badge", READ_ONLY)]
        if not self._compact:
            shown.append(holo.role_text("badge", self._count_words()))
        return shown

    def _count_words(self) -> str:
        return f"{self._frames} frame" if self._frames == 1 else f"{self._frames} frames"

    def _sync_name(self) -> None:
        self.setAccessibleName(", ".join([BAR_TITLE, READ_ONLY, self._count_words()]))

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        from PySide6.QtGui import QFontMetricsF

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            mid = BAR_HEIGHT / 2
            title = holo.font("button", 600)
            title.setPixelSize(15)
            painter.setFont(title)
            painter.setPen(QColor(holo.INK_1))
            width = QFontMetricsF(title).horizontalAdvance(BAR_TITLE)
            painter.drawText(QRectF(0, 0, width + 2, BAR_HEIGHT),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), BAR_TITLE)
            x = width + self.GAP
            badge = holo.font("badge")
            metrics = QFontMetricsF(badge)
            painter.setFont(badge)
            words = holo.role_text("badge", READ_ONLY)
            box = QRectF(x, mid - 8, metrics.horizontalAdvance(words) + 12, 16)
            painter.setPen(QPen(holo.LINE, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), holo.RADIUS_BADGE, holo.RADIUS_BADGE)
            painter.setPen(QColor(holo.INK_3))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), words)
            if not self._compact:
                count = holo.role_text("badge", self._count_words())
                painter.drawText(QRectF(box.right() + self.GAP, 0, metrics.horizontalAdvance(count) + 4, BAR_HEIGHT),
                                 int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), count)
        finally:
            painter.end()


# --------------------------------------------------------------------------
# The window's keys
# --------------------------------------------------------------------------

class WindowKeysFilter(QObject):
    """Installed on the board's view and its focus proxy: refuses ShortcutOverride for the window's keys (so their
    shortcuts fire with the focus in the page, whatever Chromium would keep), and asks for the proxy to be checked when
    the focus comes in or the view's children change (a renderer that restarts brings a new proxy)."""

    def __init__(self, parent: QObject, check: Callable[[], None]) -> None:
        super().__init__(parent)
        self._check = check
        self.view: QObject | None = None   # the view itself: the override goes on up to it too, counted once
        self.refused = 0                   # window keys sent on to the window from the page
        self.cycled = 0                    # F6 / Shift+F6 sent on to the window from the page

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        try:
            kind = event.type()
            if kind == QEvent.Type.ShortcutOverride:
                window_key = is_window_key(event.key(), event.modifiers())
                if window_key or is_cycle_key(event.key(), event.modifiers())                         or is_full_key(event.key(), event.modifiers()):
                    event.ignore()                 # not the page's: the window's shortcut fires
                    if watched is not self.view:
                        if window_key:
                            self.refused += 1
                        else:
                            self.cycled += 1
                    return True
            elif kind in (QEvent.Type.FocusIn, QEvent.Type.ChildPolished):
                self._check()
            elif kind == QEvent.Type.ChildAdded:
                QTimer.singleShot(0, self, self._check)   # the view sets its new proxy right after adding it
        except Exception as exc:  # noqa: BLE001 - a filter never raises into Qt
            logger.debug("Board: the key filter failed (%s)", type(exc).__name__)
        return False


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------

_PAGE_CLASS: list[Any] = []


def _page_class() -> Any:
    """The board's QWebEnginePage (made on first use, once the engine is ready): snapshot_engine's Page (no dialog, no
    file picker) that navigates only where the view lets it, opens no window and counts its console messages."""
    if not _PAGE_CLASS:
        base = snapshot_engine.engine_classes().Page

        class BoardPage(base):  # type: ignore[valid-type, misc]
            """See _page_class."""

            def __init__(self, profile: Any, parent: QObject, navigation: Callable[[QUrl, bool], bool],
                         note: Callable[[str], None]) -> None:
                super().__init__(profile, parent)
                self._navigation = navigation
                self._note = note

            def acceptNavigationRequest(self, url: QUrl, _type: Any, is_main_frame: bool) -> bool:  # noqa: N802
                try:
                    return bool(self._navigation(url, bool(is_main_frame)))
                except Exception:  # noqa: BLE001 - unsure: refused
                    return False

            def createWindow(self, _type: Any) -> Any:  # noqa: N802
                _quietly(lambda: self._note("refused:window"))
                return None

            def javaScriptConsoleMessage(self, level: Any, *_text: Any) -> None:  # noqa: N802 - counted, never kept
                word = str(getattr(level, "name", "") or "").replace("MessageLevel", "").casefold() or "message"
                _quietly(lambda: self._note(f"console:{word}"))

        _PAGE_CLASS.append(BoardPage)
    return _PAGE_CLASS[0]


class BoardView(QWidget):
    """The board: one web view on Jarvis's own page, or BoardFallback (see the module doc). GUI thread only."""

    ready = Signal()
    layoutChanged = Signal(object)         # BoardLayout
    frameFocused = Signal(str)
    openRequested = Signal(str)
    closeRequested = Signal(str)
    pinRequested = Signal(str, bool)
    bringBackRequested = Signal(str)
    approvalsRequested = Signal(str)
    # Part 5b: the bar's Clear, the cleared row's Bring back, the pill's Show, the owner at the board (the pointer over
    # it, the focus in it), and the keyboard focus out of it (Esc, F6: "ask" / "conversation").
    clearRequested = Signal()
    restoreRequested = Signal()
    showNewRequested = Signal()
    readingChanged = Signal(bool, bool)
    focusOutRequested = Signal(str)
    # Part 5c: a frame drawing in (its id; "outline" complete, then its draw-in "done") and open big's steps ("back",
    # "laid", "shut").
    frameLanded = Signal(str, str)
    bigStep = Signal(str)
    # The canvas, part 1: a corner button of the page (a word of board.protocol.GO_WORDS).
    goRequested = Signal(str)
    # The window brief: the page's top band for Windows' hit-test (a protocol.Request of kind "chrome").
    chromeReported = Signal(object)
    unavailable = Signal(str)              # the reason word (no_engine, sandbox, unsafe_flags, no_ready, ...)

    def __init__(self, parent: QWidget | None = None, *, start_delay_ms: int = START_DELAY_MS,
                 ready_timeout_ms: int = READY_TIMEOUT_MS, engine: Callable[[], str] | None = None,
                 scheme_ready: Callable[[], bool] | None = None, canvas: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("board")
        self._canvas = bool(canvas)          # the page is Home itself (an opaque black ground)
        self._latest: dict[str, str] = {}    # the newest orb and canvas message while the page is not ready
        self._engine = engine or snapshot_engine.ensure_engine
        self._scheme_ready = scheme_ready or scheme.scheme_ready
        self._start_delay_ms = max(0, int(start_delay_ms))
        self._ready_timeout_ms = max(1, int(ready_timeout_ms))
        self._phase = _IDLE
        self._reason = ""
        self._known: set[str] = set()
        # The app's frames as the page has them, in the order they came: each frame's last message and its picture's
        # data: URL. The read-only tiles show them if the board falls back (also after messages were queued or sent).
        self._frames: dict[str, dict[str, Any]] = {}
        self._pictures: dict[str, str] = {}
        self._images: dict[str, tuple[str, QImage | None]] = {}
        # Part 5a: the groups the app sent (id -> its last group message, newest last) and each frame's group; the bar's
        # last counts (None until the desk formed: no bar on the page or over the read-only tiles before).
        self._groups: dict[str, dict[str, Any]] = {}
        self._frame_group: dict[str, str] = {}
        self._cleared: frozenset[str] = frozenset()      # part 5b: what the bar's Clear took away (out of sight)
        self._bar: dict[str, Any] | None = None
        self._bar_widget: BoardBar | None = None
        self._tiles: GlanceTiles | None = None
        self._tiles_area: QScrollArea | None = None
        self._queue: deque[str] = deque()
        self._motion = holo.motion_level()
        self._counts: Counter[str] = Counter()
        self._requests: Counter[str] = Counter()
        self._allowed: Counter[str] = Counter()
        self._blocked: Counter[str] = Counter()
        self._served: Counter[str] = Counter()
        self._logged: set[str] = set()
        self._restarts = 0
        self._ready_since: float | None = None      # when the page last came ready (the restarts' count resets after)
        self._load_pending = False
        self._began = 0.0
        self._was_ready = False
        self._types: dict[str, Any] = {}
        self._main_world = 0
        self._view: Any = None
        self._page: Any = None
        self._profile: Any = None
        self._channel: Any = None
        self._bridge: BoardBridge | None = None
        self._interceptor: Any = None
        self._handler: Any = None
        self._proxy: Any = None
        self._fallback: BoardFallback | None = None
        self._fallback_box: QWidget | None = None
        self._keys = WindowKeysFilter(self, self._check_proxy)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._begin)
        self._ready_timer = QTimer(self)
        self._ready_timer.setSingleShot(True)
        self._ready_timer.timeout.connect(self._on_ready_timeout)
        holo.motion_settings().changed.connect(self._on_reduced_motion)

    def __repr__(self) -> str:
        return f"BoardView(phase={self._phase!r}, frames={len(self._known)}, reason={self._reason!r})"

    # ---- public --------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Start the board START_DELAY_MS from now (idempotent; the first show calls it too)."""
        if self._phase == _IDLE:
            self._phase = _WAITING
            self._start_timer.start(self._start_delay_ms)

    def is_ready(self) -> bool:
        """The page is up and takes messages at once (before, they wait in a short queue)."""
        return self._phase == _READY

    def unavailable_reason(self) -> str:
        """Why the board fell back to BoardFallback ("" while it can work)."""
        return self._reason

    def fallback(self) -> BoardFallback | None:
        return self._fallback

    def tiles(self) -> GlanceTiles | None:
        """The read-only tiles under the fallback tile (None while the board can work)."""
        return self._tiles

    def web_view(self) -> Any:
        """The one QWebEngineView (None before it starts and after it fell back or shut down)."""
        return self._view

    def motion(self) -> str:
        return self._motion

    def set_canvas(self, canvas: bool) -> None:
        """The page is Home itself (the canvas): its ground opaque black, so the window never shows through it and
        nothing flashes while it loads; else see-through, as beside the conversation. Takes effect at once."""
        self._canvas = bool(canvas)
        page = self._page
        if page is not None and _alive(page):
            _quietly(lambda: page.setBackgroundColor(self._ground()))

    def is_canvas(self) -> bool:
        return self._canvas

    def _ground(self) -> QColor:
        return QColor(0, 0, 0, 255) if self._canvas else QColor(0, 0, 0, 0)

    def known_ids(self) -> frozenset[str]:
        """The ids of the frames the app sent (the page's requests must name one of them)."""
        return frozenset(self._known)

    def known_groups(self) -> frozenset[str]:
        """The ids of the groups the app sent (part 5a)."""
        return frozenset(self._groups)

    def bar_widget(self) -> BoardBar | None:
        """The native bar over the read-only tiles (fallen back, once the app sent the bar's counts)."""
        return self._bar_widget

    def counts(self) -> dict[str, Any]:
        """Counts only: messages sent / refused, the page's requests (accepted / dropped), the requests the interceptor
        allowed and blocked by scheme, the files served and refused, the refusal hooks, the page's console messages and
        the window's keys sent on."""
        data: dict[str, Any] = dict(self._counts)
        data.update(self._requests)
        data["allowed"] = dict(self._allowed)
        data["blocked"] = dict(self._blocked)
        data["served"] = dict(self._served)
        data["keys_sent_on"] = self._keys.refused
        data["cycle_keys_sent_on"] = self._keys.cycled
        return data

    def apply(self, message: Any) -> bool:
        """Send ``message`` to the page (board.protocol.check_message; the page checks it again). Fallen back, it is
        still checked, and the frames show as read-only tiles under the fallback tile. False when it was refused
        (counted, with the reason word in the log) or the board is shut down. Before the page is ready, messages wait
        (at most QUEUE_CAP)."""
        if self._phase == _SHUT:
            self._counts["dropped:unavailable"] += 1
            return False
        try:
            checked = protocol.check_message(message, self._known, self._groups)
        except protocol.Refused as refused:
            self._counts[f"refused:{refused.reason}"] += 1
            self._log_once(f"refused:{refused.reason}", "Board: a message was refused (%s)", refused.reason)
            return False
        self._remember(checked)
        if self._phase == _FALLBACK:
            self._counts[f"tiles:{checked.type}"] += 1
            if checked.type in ("frame", "remove", "clear", "picture", "group", "remove_group", "bar", "cleared"):
                self._show_tiles()
            elif checked.type == "reveal":
                self._reveal_tiles(checked.group_id)
            return True
        self._counts[f"sent:{checked.type}"] += 1
        if self._phase == _READY:
            self._run(checked.text)
        elif checked.type in ("orb", "canvas", "task", "chrome"):
            self._latest[checked.type] = checked.text    # the newest of each: a level never takes a frame's room
        else:
            if len(self._queue) >= QUEUE_CAP:
                self._queue.popleft()
                self._counts["dropped:queue"] += 1
            self._queue.append(checked.text)
        return True

    def _remember(self, checked: protocol.Checked) -> None:
        """The known ids and the frames' mirror follow a message that passed (``checked.text`` is its clean JSON)."""
        kind, frame_id, group_id = checked.type, checked.frame_id, checked.group_id
        if kind == "frame":
            self._known.add(frame_id)
            self._frames[frame_id] = json.loads(checked.text)     # an update keeps its place
            if group_id:
                self._frame_group.setdefault(frame_id, group_id)
        elif kind == "remove":
            self._forget(frame_id)
        elif kind == "clear":
            self._known.clear()
            self._frames.clear()
            self._pictures.clear()
            self._groups.clear()
            self._frame_group.clear()
            self._cleared = frozenset()
        elif kind == "cleared":
            self._cleared = frozenset(json.loads(checked.text)["frames"])
        elif kind == "picture":
            self._pictures[frame_id] = json.loads(checked.text)["src"]
        elif kind == "group":
            self._groups[group_id] = json.loads(checked.text)    # a known group keeps its place (the newest last)
        elif kind == "remove_group":
            for member, owner in list(self._frame_group.items()):
                if owner == group_id:
                    self._forget(member)
            self._groups.pop(group_id, None)
        elif kind == "bar":
            self._bar = json.loads(checked.text)

    def _forget(self, frame_id: str) -> None:
        self._known.discard(frame_id)
        self._frames.pop(frame_id, None)
        self._pictures.pop(frame_id, None)
        self._frame_group.pop(frame_id, None)
        if frame_id in self._cleared:
            self._cleared = self._cleared - {frame_id}

    def _tile_order(self) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str, bool]]]:
        """The frames as the board shows them: each group's (the newest group first, in its order, a frame not in it
        yet after; a parked group not yet), then the frames of no group; and the header drawn above each group's first
        tile. Closed frames and what a Clear took away are left out."""
        shown: list[dict[str, Any]] = []
        headers: dict[str, tuple[str, str, bool]] = {}
        placed: set[str] = set()
        for group_id, group in reversed(list(self._groups.items())):
            members = [frame_id for frame_id in group.get("frames", []) if self._frame_group.get(frame_id) == group_id]
            members += [frame_id for frame_id, owner in self._frame_group.items()
                        if owner == group_id and frame_id not in members]
            if group.get("parked"):
                placed.update(members)             # it lands later: not shown yet, nor among the frames of no group
                continue
            first = True
            for frame_id in members:
                frame = self._frames.get(frame_id)
                if frame is None or frame.get("closed") or frame_id in self._cleared:
                    continue
                if first:
                    headers[frame_id] = (str(group.get("time", "")), str(group.get("topic", "")),
                                         bool(group.get("older", False)))
                    first = False
                shown.append(frame)
                placed.add(frame_id)
            placed.update(members)                 # a group's own frames never show among the frames of no group
        shown += [frame for frame_id, frame in self._frames.items()
                  if frame_id not in placed and frame_id not in self._cleared]
        return shown, headers

    def _reveal_tiles(self, group_id: str) -> None:
        """A turn's "N frames on the board" over the read-only tiles (the final review's resilience 5): the tiles
        scroll so that group's header is at the top (as far as they can scroll); nothing when it shows no tile."""
        tiles, area = self._tiles, self._tiles_area
        if tiles is None or area is None or not (_alive(tiles) and _alive(area)):
            return
        heads = tiles.head_rects()
        first = next((frame_id for frame_id in heads if self._frame_group.get(frame_id) == group_id), "")
        if not first:
            return
        bar = area.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(bar.maximum(), int(heads[first].top()))))

    def _show_tiles(self) -> None:
        """The read-only tiles show the frames as they are now (with their groups' headers); the fallback tile says
        READ-ONLY TILES BELOW while a frame shows, and the native bar counts them once the desk formed."""
        tiles = self._tiles
        if tiles is None or not _alive(tiles):
            return
        images: dict[str, QImage] = {}
        kept: dict[str, tuple[str, QImage | None]] = {}
        for frame_id, src in self._pictures.items():
            cached = self._images.get(frame_id)
            if cached is None or cached[0] is not src:          # decoded once per picture message
                cached = (src, tile_picture(src))
                if cached[1] is None:
                    self._counts["tiles:picture_left_out"] += 1
            kept[frame_id] = cached
            if cached[1] is not None:
                images[frame_id] = cached[1]
        self._images = kept
        shown, headers = self._tile_order()
        tiles.set_frames(shown, images, headers=headers)
        if self._fallback is not None and _alive(self._fallback):
            self._fallback.set_tiles_below(bool(tiles.frames()))
        self._sync_fallback_bar()

    def _sync_fallback_bar(self) -> None:
        """Fallen back on the desk (the app sent the bar's counts): the native bar over the tile, and the tiles in the
        frames' place (the strip's room kept at the right, as on the page)."""
        bar, box = self._bar_widget, self._fallback_box
        if self._bar is None or bar is None or box is None or not (_alive(bar) and _alive(box)):
            return
        bar.set_counts(int(self._bar.get("frames", 0)), compact=bool(self._bar.get("compact", False)))
        bar.show()
        layout = box.layout()
        if layout is not None:
            layout.setContentsMargins(PAD, 0, STRIP, 0)

    def focus_part(self, part: str) -> bool:
        """F6's cycle (DESIGN-SPEC 3.6): the window's keyboard focus into the page, then to its frames or its index
        strip, or on from where it is ("next" / "prev": protocol.FOCUS_PARTS). False when there is no page to take it
        (not ready yet, fallen back, shut down): the caller sends the focus elsewhere."""
        view = self._view
        if self._phase != _READY or view is None or not _alive(view) or part not in protocol.FOCUS_PARTS:
            return False
        try:
            view.setFocus(Qt.FocusReason.ShortcutFocusReason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Board: the focus did not move (%s)", type(exc).__name__)
            return False
        return self.apply({"type": "focus_part", "part": part})

    def set_motion(self, level: str) -> bool:
        """The motion the page plays: "lively", "calm" (x1.6) or "off" (each moment's end state at once), as holo's
        ``[ui] motion`` with reduced motion (anything else is lively). Sent again whenever the page comes up."""
        text = str(level or "").strip().casefold()
        self._motion = text if text in protocol.MOTION_LEVELS else holo.MOTION_LIVELY
        if self._phase == _READY:
            self._run(protocol.check_message({"type": "motion", "level": self._motion}).text)
        return self._phase not in (_FALLBACK, _SHUT)

    def shutdown(self) -> None:
        """Jarvis closes (also without an event loop): the page, its renderer, profile and channel go now."""
        if self._phase == _SHUT:
            return
        self._phase = _SHUT
        self._start_timer.stop()
        self._ready_timer.stop()
        self._queue.clear()
        self._latest.clear()
        self._frames.clear()
        self._pictures.clear()
        self._images.clear()
        self._groups.clear()
        self._frame_group.clear()
        self._teardown()
        if self._blocked:
            logger.info("Board: closed (requests blocked: %s)",
                        ", ".join(f"{word} {count}" for word, count in sorted(self._blocked.items())))

    # ---- Qt ------------------------------------------------------------------------------------------------------

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.start()

    # ---- starting ------------------------------------------------------------------------------------------------

    def _begin(self) -> None:
        if self._phase != _WAITING:
            return
        if not self._scheme_ready():
            self._fall_back(NO_SCHEME)
            return
        reason = self._engine()
        if reason:
            self._fall_back(reason)
            return
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 - the engine said yes but the page could not be made
            logger.warning("Board: the page could not be set up (%s)", type(exc).__name__)
            self._fall_back(snapshots.R_ENGINE_FAILED)
            return
        self._load()

    def _build(self) -> None:
        """The profile, page, view and channel (the engine is ready)."""
        # First, before the view's focus proxy is ever wrapped: PySide6 then hands it out as the QQuickWidget it is (else
        # as a plain QWidget), so open big's still can follow its renders (desk_big.BigController._watch).
        from PySide6 import QtQuickWidgets  # noqa: F401
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings, QWebEngineUrlRequestInfo

        classes = snapshot_engine.engine_classes()
        kinds = QWebEngineUrlRequestInfo.ResourceType
        self._types = {
            "main": frozenset(getattr(kinds, name) for name in ("ResourceTypeMainFrame",
                                                                  "ResourceTypeNavigationPreloadMainFrame")
                              if hasattr(kinds, name)),
            "files": frozenset((kinds.ResourceTypeStylesheet, kinds.ResourceTypeScript, kinds.ResourceTypeFontResource,
                                kinds.ResourceTypeImage)),
            "image": kinds.ResourceTypeImage,
        }
        self._main_world = int(QWebEngineScript.ScriptWorldId.MainWorld.value)
        profile = snapshot_engine.private_profile(None)
        if profile is None:
            raise RuntimeError("the profile is not off the record")
        self._profile = profile
        # Held here: Qt only points at the interceptor, and an unreferenced Python one would check nothing.
        self._interceptor = classes.Interceptor(self._judge, profile)
        profile.setUrlRequestInterceptor(self._interceptor)
        self._handler = scheme.handler(profile)
        self._served = self._handler.counts
        profile.installUrlSchemeHandler(scheme.SCHEME_BYTES, self._handler)
        # Nothing on the page downloads (no link, no navigation but the board's own): a download that started anyway
        # is cancelled. No download folder is made: nothing of the board is ever on disk.
        profile.downloadRequested.connect(lambda download: self._refuse("downloads", download, "cancel"))
        snapshot_engine.apply_web_settings(profile.settings(), local_storage=False)
        # Part 6 fixes: the page's scrollbars are its own to show. board.css hides every one (the board scrolls under
        # its fades and "N more below") but open big's body's: a thin bar in the palette's colours, there only while
        # the body holds more than its card can show.
        profile.settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        view = classes.QWebEngineView(self)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAcceptDrops(False)
        view.setAccessibleName("Board")
        page = _page_class()(profile, view, self._accept_navigation, self._count)
        profile.setParent(page)        # the profile goes with its page, after it (Qt: never before)
        page.setBackgroundColor(self._ground())       # the window's ground shows through between the frames (the
                                                      # canvas: opaque black, the page is Home itself)
        page.setAudioMuted(True)
        self._connect(page, profile)
        channel = QWebChannel(page)
        bridge = BoardBridge(page, known=lambda: self._known, deliver=self._on_request, counts=self._requests)
        channel.registerObject("bridge", bridge)
        page.setWebChannel(channel, self._main_world)
        view.setPage(page)
        self._view, self._page, self._channel, self._bridge = view, page, channel, bridge
        self._layout.addWidget(view)
        self._keys.view = view
        view.installEventFilter(self._keys)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    def _connect(self, page: Any, profile: Any) -> None:
        page.renderProcessTerminated.connect(self._on_renderer_gone)
        page.loadFinished.connect(self._on_loaded)
        if hasattr(page, "permissionRequested"):          # Qt 6.8+
            page.permissionRequested.connect(lambda permission: self._refuse("permissions", permission, "deny"))
        if hasattr(page, "featurePermissionRequested"):   # the older API, while it is there
            page.featurePermissionRequested.connect(lambda origin, feature: self._deny_feature(page, origin, feature))
        for signal_name, method in _REFUSALS:
            signal = getattr(page, signal_name, None)
            if signal is not None:
                signal.connect(lambda request, name=signal_name, how=method: self._refuse(name, request, how))
        # Sign-in prompts: the authenticator is left empty, so the request is refused.
        page.authenticationRequired.connect(lambda *_args: self._count("refused:authentication"))
        page.proxyAuthenticationRequired.connect(lambda *_args: self._count("refused:authentication"))
        page.newWindowRequested.connect(lambda _request: self._count("refused:new_window"))

    def _load(self) -> None:
        self._phase = _LOADING
        self._began = time.monotonic()
        self._load_pending = True
        self._ready_timer.start(self._ready_timeout_ms)
        self._page.load(QUrl(scheme.BOARD_URL))

    # ---- the page's hooks (GUI thread; never raise) ----------------------------------------------------------------

    def _count(self, name: str) -> None:
        self._counts[name] += 1

    def _refuse(self, name: str, request: Any, method: str) -> None:
        self._counts[f"refused:{name}"] += 1
        _quietly(lambda: getattr(request, method)())

    def _deny_feature(self, page: Any, origin: Any, feature: Any) -> None:
        self._counts["refused:features"] += 1
        _quietly(lambda: page.setFeaturePermission(origin, feature, type(page).PermissionPolicy.PermissionDeniedByUser))

    def _judge(self, info: Any) -> bool:
        """The interceptor: True blocks the request. Allowed: the board's own files in scheme.FILES (the page itself
        only as the main frame; the others only as a style, script, font or picture) and the page's data: pictures.
        Counted by scheme."""
        try:
            url = info.requestUrl()
            word = _scheme_word(url.scheme())
            kind = info.resourceType()
            allowed = False
            if not info.isDownload():
                if word == scheme.SCHEME and url.host() == scheme.HOST:
                    name = scheme.board_file(url)
                    if kind in self._types["main"]:
                        allowed = name == scheme.PAGE
                    else:
                        allowed = bool(name) and name != scheme.PAGE and kind in self._types["files"]
                elif word == "data":
                    allowed = kind == self._types["image"]
            (self._allowed if allowed else self._blocked)[word] += 1
            return not allowed
        except Exception:  # noqa: BLE001 - unsure: blocked
            self._blocked["other"] += 1
            return True

    def _accept_navigation(self, url: QUrl, main_frame: bool) -> bool:
        """Only the board, as the main frame, when this view asked for it (its load, or the reload after the renderer
        stopped): never the page's own navigation, not even to itself."""
        if main_frame and self._load_pending and scheme.board_file(url) == scheme.PAGE:
            self._load_pending = False
            return True
        self._counts["refused:navigation"] += 1
        return False

    def _on_loaded(self, ok: bool) -> None:
        self._check_proxy()
        if not ok and self._phase == _LOADING and not self._load_pending:
            self._fall_back(LOAD_FAILED)

    def _on_ready_timeout(self) -> None:
        if self._phase == _LOADING:
            self._fall_back(NO_READY)

    def _on_renderer_gone(self, status: Any, _code: Any = 0) -> None:
        if self._phase not in (_LOADING, _READY) or self._page is None:
            return
        word = str(getattr(status, "name", "") or "").replace("TerminationStatus", "").casefold() or "stopped"
        self._counts["renderer_stopped"] += 1
        since = self._ready_since
        if self._phase == _READY and since is not None and time.monotonic() - since >= RESTARTS_FORGIVEN_S:
            self._restarts = 0                       # the resilience review's L4: ready for 10 minutes - a stop now is
            self._counts["restarts_forgiven"] += 1   # not one of a run of them
        self._ready_since = None
        self._restarts += 1
        if self._restarts > MAX_RESTARTS:
            self._fall_back(CRASHED)
            return
        logger.info("Board: the page's renderer stopped (%s); loading the board again (%d of %d)", word,
                    self._restarts, MAX_RESTARTS)
        self._phase = _LOADING
        self._ready_timer.stop()
        self._check_proxy()                          # and again once the new renderer brings its new proxy
        QTimer.singleShot(0, self, self._reload)

    def _reload(self) -> None:
        if self._phase == _LOADING and self._page is not None and _alive(self._page):
            self._load()
            self._check_proxy()

    # ---- the page's requests ---------------------------------------------------------------------------------------

    def _on_request(self, request: protocol.Request) -> None:
        if self._phase in (_FALLBACK, _SHUT):
            self._counts["dropped:closed"] += 1      # a page on its way out (its deletion is deferred) asks nothing
            return
        kind = request.kind
        if kind == "ready":
            self._on_ready()
        elif kind == "layout":
            self.layoutChanged.emit(BoardLayout(request.places, request.scrolling))
        elif kind == "focus":
            self.frameFocused.emit(request.frame_id)
        elif kind == "open":
            self.openRequested.emit(request.frame_id)
        elif kind == "close":
            self.closeRequested.emit(request.frame_id)
        elif kind == "pin":
            self.pinRequested.emit(request.frame_id, request.on)
        elif kind == "bring_back":
            self.bringBackRequested.emit(request.frame_id)
        elif kind == "go_approvals":
            self.approvalsRequested.emit(request.frame_id)
        elif kind == "clear":
            self.clearRequested.emit()
        elif kind == "restore":
            self.restoreRequested.emit()
        elif kind == "show_new":
            self.showNewRequested.emit()
        elif kind == "reading":
            self.readingChanged.emit(request.pointer, request.focused)
        elif kind == "focus_out":
            self.focusOutRequested.emit(request.to)
        elif kind == "landed":
            self.frameLanded.emit(request.frame_id, request.step)
        elif kind == "big":
            self.bigStep.emit(request.step)
        elif kind == "go":
            self.goRequested.emit(request.to)
        elif kind == "chrome":
            self.chromeReported.emit(request)

    def _on_ready(self) -> None:
        if self._phase != _LOADING or self._load_pending:
            self._counts["dropped:ready_again"] += 1     # one ready per load of the page
            return
        self._ready_timer.stop()
        took = int((time.monotonic() - self._began) * 1000)
        again = self._was_ready
        self._phase = _READY
        self._was_ready = True
        self._ready_since = time.monotonic()
        logger.info("Board: page ready in %d ms%s", took, " (after its renderer stopped)" if again else "")
        self._run(protocol.check_message({"type": "motion", "level": self._motion}).text)
        while self._queue:
            self._run(self._queue.popleft())
        for kind in ("canvas", "orb"):
            text = self._latest.pop(kind, "")
            if text:
                self._run(text)
        self._check_proxy()
        self.ready.emit()

    def _run(self, text: str) -> None:
        """One checked message into the page, in its main world (where board.js and the web channel are)."""
        page = self._page
        if page is None:
            return
        try:
            page.runJavaScript(f"board.apply({json.dumps(text)});", self._main_world)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Board: a message was not delivered (%s)", type(exc).__name__)

    def _on_reduced_motion(self, _reduced: bool) -> None:
        self.set_motion(holo.motion_level())

    # ---- keys ------------------------------------------------------------------------------------------------------

    def _check_proxy(self) -> None:
        """The view's focus proxy has the key filter (a renderer that restarts brings a new one): installed again each
        time (Qt keeps one copy)."""
        view = self._view
        if view is None or not _alive(view):
            return
        try:
            view.installEventFilter(self._keys)
            proxy = view.focusProxy()
            if proxy is not None and proxy is not self._proxy:
                old, self._proxy = self._proxy, proxy
                if _alive(old):
                    old.removeEventFilter(self._keys)
                self._counts["key_filter:proxies"] += 1
            if proxy is not None:
                proxy.installEventFilter(self._keys)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Board: the key filter was not installed (%s)", type(exc).__name__)

    def _on_focus_changed(self, _old: Any, now: Any) -> None:
        view = self._view
        if view is not None and now is not None and _alive(view) and (now is view or view.isAncestorOf(now)):
            self._check_proxy()

    # ---- falling back, tearing down --------------------------------------------------------------------------------

    def _fall_back(self, reason: str) -> None:
        if self._phase in (_FALLBACK, _SHUT):
            return
        word = _reason_word(reason)
        self._phase = _FALLBACK
        self._reason = word
        self._start_timer.stop()
        self._ready_timer.stop()
        self._queue.clear()
        self._latest.clear()
        self._teardown()
        box = QWidget(self)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(PAD, 0, PAD, 0)
        box_layout.setSpacing(0)
        # On the desk (the app sent the bar's counts) the bar says READ-ONLY over the tile (DESIGN-SPEC 3.6).
        self._bar_widget = BoardBar(box)
        self._bar_widget.hide()
        box_layout.addWidget(self._bar_widget)
        self._fallback = BoardFallback(box)
        box_layout.addWidget(self._fallback)
        box_layout.addSpacing(TILES_GAP)
        # The frames as read-only tiles under it (DESIGN-SPEC 3.6), in a see-through area that scrolls when they are
        # taller than the board: nothing is cut off. Nothing in them is clickable; they never take the focus.
        area = QScrollArea(box)
        area.setObjectName("boardTiles")
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # the wheel scrolls it, as the page
        area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        area.viewport().setAutoFillBackground(False)
        area.setStyleSheet("QScrollArea#boardTiles { background: transparent; border: none; }")
        self._tiles = GlanceTiles(area)
        self._tiles_area = area
        area.setWidget(self._tiles)
        self._tiles.setAutoFillBackground(False)    # setWidget turned it on: the window's ground shows between tiles
        box_layout.addWidget(area, 1)
        self._fallback_box = box
        self._layout.addWidget(box)
        box.show()
        self._show_tiles()
        logger.info("Board: it can't start on this PC (%s)", word)
        self.unavailable.emit(word)

    def _teardown(self) -> None:
        """No page, renderer, profile or channel is left (deleted now when no event loop runs any more)."""
        view, page, bridge, proxy = self._view, self._page, self._bridge, self._proxy
        self._view = self._page = self._profile = self._channel = self._bridge = None
        self._interceptor = self._handler = self._proxy = None
        self._keys.view = None
        self._load_pending = False
        app = QApplication.instance()
        if app is not None and view is not None:
            _quietly(lambda: app.focusChanged.disconnect(self._on_focus_changed))
        if _alive(proxy):
            _quietly(lambda: proxy.removeEventFilter(self._keys))
        if _alive(bridge):
            bridge.release()
        if _alive(page):
            _quietly(lambda: page.triggerAction(type(page).WebAction.Stop))
        if _alive(view):
            _quietly(lambda: view.removeEventFilter(self._keys))
            self._layout.removeWidget(view)
            view.hide()
            if _event_loop_running():
                view.deleteLater()        # its page with it, and the page's profile and channel after the page
            else:
                import shiboken6

                shiboken6.delete(view)

    def _log_once(self, key: str, text: str, *args: Any) -> None:
        if key in self._logged:
            logger.debug(text, *args)
        else:
            self._logged.add(key)
            logger.info(text, *args)


def keeps_space(widget: QWidget | None) -> bool:
    """True for the board and anything inside it (its web view's focus proxy): Space there scrolls the board, as in a
    text field, and never plays or pauses the briefing (ui._keeps_space)."""
    hops = 0
    while widget is not None and hops < 64:
        if isinstance(widget, BoardView):
            return True
        widget = widget.parentWidget()
        hops += 1
    return False


__all__ = ["BAR_TITLE", "BoardBar", "BoardBridge", "BoardFallback", "BoardLayout", "BoardView", "CANT_START",
           "CYCLE_KEY", "FULL_KEY", "MAX_RESTARTS", "READY_TIMEOUT_MS", "READ_ONLY", "READ_ONLY_BELOW",
           "START_DELAY_MS", "STRIP", "TILE_PICTURE_PIXELS", "WINDOW_KEYS", "WindowKeysFilter", "is_cycle_key",
           "is_full_key", "is_window_key", "keeps_space", "tile_picture"]
