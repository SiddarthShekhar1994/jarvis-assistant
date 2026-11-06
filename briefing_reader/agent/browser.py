"""The agent's own browser: the window the owner watches (SPEC D6, D7, section 8; part A2). Qt, GUI thread only, and the
one file of this package that imports Qt; BrowserClient is how the endpoint's threads reach it.

    register_scheme()     Jarvis's own address scheme for a task's files: before the QApplication, like the web engine's
                          other setup, and only while tasks are on (__main__)
    AgentBrowser          a real page in a visible window, one at a time. It IS snapshot_engine.PageCamera, whose every
                          rule stands: a fresh off-the-record profile and page for each page opened, the page's window
                          sandboxed for life with no downloads, modals, forms or top navigation, the request
                          interceptor, permissions, pop-ups, dialogs, file choosers, sign-in prompts and client
                          certificates refused, every connection through the network gate (public IPv4, port 443),
                          Chromium's flags, and no network page at all on a test platform. What differs: the view lives
                          in the window, a page stays open until the next one, and the shot's "picture" is the moment it
                          is ready to read
    WatchWindow           the window: frameless, the HUD's flags and tokens, never activated and never the focus, a thin
                          strip with the tag "Jarvis is reading", the host, the last few pages and a small Hide, over
                          the page. On a second screen if there is one, else the right part of the main screen, clear of
                          the HUD, kept on screen. Mouse and keyboard off: it is Jarvis's to drive. A page that did not
                          come leaves words on its dark page, not emptiness: a refusal (HTTP 401, 403, 429) says the
                          site keeps automated browsers out
    BrowserClient         agent.web's Browser seam, for any thread: hands work to the GUI thread and waits, with a
                          timeout; a Stop or Turn off releases a waiter at once and a late result is dropped (R8)

A page the agent wrote (show_file) opens on Jarvis's scheme with a profile of its own and may reach nothing outside this
task's folder. Only there is the scheme's handler installed, with this run's token in every address; its interceptor
allows that scheme and that token and nothing else; the web engine itself keeps a local scheme's page from remote
addresses; and while it is open the network gate is sealed (snapshot_gate.ConnectGate.seal), which also stops what no
interceptor sees, a pre-connection or WebRTC's TURN over TCP (measured: both reach the gate from such a page, a
Content-Security-Policy does not stop them, and this engine does not honour one sent on a custom scheme, so none is
sent). Otherwise a page holding private data could carry it out. A Live snapshot taken in those moments fails, and says
so.

When the run ends the page and its profile go at once. The window keeps a still of it for about 10 s, then goes too, or
for as long as the result card stays when the app says so; after a Stop or a Turn off it goes at once. shutdown() leaves
nothing: no page, no renderer, no window, also with no event loop left.

Hide (on the strip) and the app's "away" (the owner brought the Jarvis window back) take the window off the screen; the
page goes on behind it, and a picture that comes out blank meanwhile is said to be one (R_BLANK). The owner's Show on
the result card opens a task's file in the same sealed way, and only then does the wheel scroll the page.

Never a disguise: the user agent and every header stay the engine's own (nothing here sets one), a refused page is not
tried again with another identity, and no captcha is answered.

Logs: counts, kinds and times only, never an address, a host, a title, a name or a word of a page.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject, QRect, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QGuiApplication, QPainter, QPen, QPixmap, QScreen
from PySide6.QtWidgets import QWidget

from .. import holo, hud, hud_overlay, snapshot_engine, snapshots
from ..actions import link_host
from ..snapshot_engine import PageCamera, encode_picture
from ..snapshots import ShotResult
from .web import NO_PAGE, SCHEME, workspace_address, workspace_name
from .workspace import TaskFolder, WorkspaceRefused

logger = logging.getLogger(__name__)

_SCHEME_BYTES = SCHEME.encode("ascii")
_FILE_KINDS = {".html": "text/html;charset=utf-8", ".htm": "text/html;charset=utf-8", ".svg": "image/svg+xml",
               ".css": "text/css;charset=utf-8"}
_PLAIN_KIND = "text/plain;charset=utf-8"    # .md .txt .csv .tsv .json .xml .yaml .yml .log: shown as the text they are

SETTLE_MS = 400                   # after a page finished loading, before it counts as ready
BLANK_WAIT_MS = 500               # a window's own about:blank gets this long to finish before the page is asked for
LINGER_S = 10.0                   # the window stays this long when the run ends
COVER_OFF_PROGRESS = 15           # the last page's still goes when the next one has got this far
CLEAR_MS = 20                     # a sealed gate waits for the page the agent wrote to be really gone
CLEAR_TRIES = 150
LOAD_MARGIN_S = 2.0               # a waiter allows the GUI thread this long past a load's own time limit
MIN_PICTURE_WIDTH = 480
PICTURE_STEP = 0.75

# The window (logical px).
READING_TAG = "Jarvis is reading"
FINISHED_TAG = "Jarvis has finished reading"
SHOWING_TAG = "Your file"
OTHER_WAY_TAG = "Reading another way - not shown here"
HIDE_TEXT = "Hide"
HIDE_SIZE = 26                    # the strip's Hide (its drawn height: the strip is 36 high)
REFUSED_WORDS = "{host} keeps automated browsers out"
REFUSED_NOTE = "Jarvis doesn't try to get past that. It will look elsewhere."
NOT_LOADED_WORDS = "{host} did not load"
NOT_LOADED_NOTE = "It may be gone, or be something Jarvis doesn't open. It will look elsewhere."
REFUSAL_STATUSES = frozenset({401, 403, 429})
FOLDER_WORDS = "Task folder"
STRIP_HEIGHT = 36
PAGE_INSET = 6
PAD = 14
DOT = 6
DOT_GAP = 8
TAG_GAP = 16
MARGIN = 16
GAP = 16                          # from the HUD
MIN_WIDTH, MAX_WIDTH = 640, 1280
MAX_WIDE = 1500                   # on a screen of its own
MAX_TALL = 1050
MIN_HEIGHT = 360
SHARE_OF_SCREEN = 0.5
OLDER_PAGES = 3
HOST_CAP = 80
WHEEL_STEP = 100                  # px the owner's wheel scrolls a task's file, a notch
_SEPARATOR = "  \u00b7  "


# --------------------------------------------------------------------------
# The scheme
# --------------------------------------------------------------------------

def register_scheme() -> bool:
    """Jarvis's scheme, known to the web engine. Only before the QApplication exists; idempotent; never raises. False:
    show_file says the window can't do it, and web pages work as ever."""
    try:
        if scheme_ready():
            return True
        if QCoreApplication.instance() is not None or not snapshot_engine.webengine_available():
            return False
        from PySide6.QtWebEngineCore import QWebEngineUrlScheme

        scheme = QWebEngineUrlScheme(_SCHEME_BYTES)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
        flag = QWebEngineUrlScheme.Flag
        # Local: a web page can't reach it and it can't reach the web. Local access: only so a page shows its own
        # folder's pictures; the interceptor refuses every other local address (file: too).
        scheme.setFlags(flag.SecureScheme | flag.LocalScheme | flag.LocalAccessAllowed)
        QWebEngineUrlScheme.registerScheme(scheme)
        return scheme_ready()
    except Exception as exc:  # noqa: BLE001 - Jarvis works without it
        logger.info("Agent browser: the scheme was not registered (%s)", type(exc).__name__)
        return False


def scheme_ready() -> bool:
    """The scheme was registered in this process (never imports the web engine for it)."""
    import sys

    core = sys.modules.get("PySide6.QtWebEngineCore")
    if core is None:
        return False
    try:
        return bytes(core.QWebEngineUrlScheme.schemeByName(_SCHEME_BYTES).name().data()) == _SCHEME_BYTES
    except Exception:  # noqa: BLE001 - unknown: not ready
        return False


_HANDLER: list[Any] = []


def _handler_class() -> Any:
    """The scheme handler's class, made on first use: nothing here imports QtWebEngine at import."""
    if not _HANDLER:
        from PySide6.QtWebEngineCore import QWebEngineUrlSchemeHandler

        class Handler(QWebEngineUrlSchemeHandler):
            """Answers for one page's profile, through ``serve(job)``; anything that goes wrong refuses the request."""

            def __init__(self, serve: Callable[[Any], None], parent: QObject) -> None:
                super().__init__(parent)
                self._serve = serve

            def requestStarted(self, job: Any) -> None:  # noqa: N802 - Qt's name
                try:
                    self._serve(job)
                except Exception as exc:  # noqa: BLE001 - never raise into Chromium
                    logger.debug("Agent browser: a task's file was not served (%s)", type(exc).__name__)
                    _refuse_job(job)

        _HANDLER.append(Handler)
    return _HANDLER[0]


def _refuse_job(job: Any) -> None:
    try:
        job.fail(type(job).Error.RequestDenied)
    except Exception:  # noqa: BLE001 - the request is gone already
        pass


def file_kind(name: str) -> str:
    """The content type a task's file is served as. Anything that is not a page, a picture or a style sheet is shown as
    the plain text it is: an .xml can't pull in a style sheet of its own, and nothing is a download."""
    for suffix, kind in _FILE_KINDS.items():
        if name.casefold().endswith(suffix):
            return kind
    return _PLAIN_KIND


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

def watch_rect(area: QRect, avoid: QRect | None, whole: bool) -> QRect:
    """Where the window goes in the work area ``area``: a screen of its own (``whole``) nearly fills it, else the right
    part, under ``avoid`` (the HUD) when it would cross it. Always inside the area (ui._keep_on_screen's rule)."""
    room_wide, room_tall = max(1, area.width() - 2 * MARGIN), max(1, area.height() - 2 * MARGIN)
    if whole:
        width, height = min(room_wide, MAX_WIDE), min(room_tall, MAX_TALL)
        return QRect(area.x() + area.width() - MARGIN - width, area.y() + MARGIN, width, height)
    width = min(room_wide, max(MIN_WIDTH, min(MAX_WIDTH, round(area.width() * SHARE_OF_SCREEN))))
    x = area.x() + area.width() - MARGIN - width
    top = area.y() + MARGIN
    if avoid is not None and avoid.isValid() and avoid.intersects(QRect(x, top, width, room_tall)):
        top = max(top, avoid.y() + avoid.height() + GAP)
    bottom = area.y() + area.height() - MARGIN
    if bottom - top < MIN_HEIGHT:
        top = max(area.y(), bottom - MIN_HEIGHT)
    return QRect(x, top, width, max(1, bottom - top))


class _Cover(QWidget):
    """A still of the last page over the view: between two pages, and when the run has ended."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._picture: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_picture(self, picture: QPixmap | None) -> None:
        self._picture = picture if picture is not None and not picture.isNull() else None
        self.setVisible(self._picture is not None)
        if self._picture is not None:
            self.raise_()
            self.update()

    def showing(self) -> bool:
        return self._picture is not None

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._picture is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._picture)


class _Notice(QWidget):
    """Words on the dark page: a page that did not come, never emptiness. Other people's words never get here: only
    Jarvis's own and a host."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._title = ""
        self._note = ""
        self._title_font = hud.body_font(18, 500)
        self._note_font = hud.body_font(13)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_words(self, title: str, note: str = "") -> None:
        self._title, self._note = title, note
        self.setVisible(bool(title))
        if title:
            self.raise_()
            self.update()

    def words(self) -> tuple[str, str]:
        return self._title, self._note

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(holo.TILE))
        wide = max(40, self.width() - 2 * MARGIN)
        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWrapAnywhere)
        middle = self.height() // 2
        painter.setFont(self._title_font)
        painter.setPen(QColor(holo.INK_1))
        painter.drawText(QRect(MARGIN, middle - 44, wide, 56), flags, self._title)
        painter.setFont(self._note_font)
        painter.setPen(QColor(holo.INK_3))
        painter.drawText(QRect(MARGIN, middle + 16, wide, 40), flags, self._note)


class WatchWindow(hud_overlay._OverlayWindow):  # noqa: SLF001 - the HUD's own window behaviour, on purpose
    """See the module docs. Holds the view (``page_area``); knows nothing of what the page is."""

    hideClicked = Signal()             # the strip's Hide (the task carries on behind it)
    wheeled = Signal(int)              # the mouse wheel over it (angle delta): only a task's file shown to the owner

    def __init__(self, *, native: Any = None, hide_from_capture: bool = False) -> None:
        super().__init__(hud_overlay.HUD_FLAGS, native, hide_from_capture)
        self.setObjectName("JarvisWatch")
        self.setWindowTitle("Jarvis")
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._tag = READING_TAG
        self._host = ""
        self._older: list[str] = []
        self._tag_font = hud.mono_font(10, 500, 0.12)
        self._host_font = hud.body_font(13)
        self._older_font = hud.body_font(12)
        self.page_area = QWidget(self)
        self.page_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._view: QWidget | None = None
        self._cover = _Cover(self.page_area)
        self._notice = _Notice(self.page_area)
        self.hide_button = holo.HoloButton(HIDE_TEXT, holo.GHOST, size=HIDE_SIZE, parent=self)
        self.hide_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide_button.clicked.connect(self.hideClicked)
        self.resize(MIN_WIDTH, MIN_HEIGHT)

    # ---- what it shows -------------------------------------------------------------------------------

    def set_view(self, view: QWidget) -> None:
        self._view = view
        view.setGeometry(self.page_area.rect())
        self._cover.raise_()
        self._notice.raise_()

    def set_notice(self, title: str = "", note: str = "") -> None:
        """Words over the page area ("": none): a page that did not come."""
        self._notice.set_words(title, note)

    def notice(self) -> tuple[str, str]:
        return self._notice.words()

    def set_page(self, host: str) -> None:
        """The page it shows now. The one before joins the last few."""
        host = " ".join((host or "").split())[:HOST_CAP]
        if host == self._host:
            return
        if self._host:
            self._older = [self._host, *(item for item in self._older if item != self._host and item != host)]
            self._older = self._older[:OLDER_PAGES]
        self._host = host
        self.update()

    def update_host(self, host: str) -> None:
        """The page it shows ended up on this host (a redirect): no new entry in the last few."""
        host = " ".join((host or "").split())[:HOST_CAP]
        if host and host != self._host:
            self._host = host
            self.update()

    def set_tag(self, tag: str) -> None:
        if tag != self._tag:
            self._tag = tag
            self.update()

    def cover(self, picture: QPixmap | None) -> None:
        self._cover.set_picture(picture)

    def covered(self) -> bool:
        return self._cover.showing()

    def strip_texts(self) -> tuple[str, str, tuple[str, ...]]:
        """(the tag, the host, the last few pages), for the tests."""
        return self._tag, self._host, tuple(self._older)

    def cut_texts(self) -> list[str]:
        """Words of the strip's tag that don't fit (none at the sizes it is built for). The host and the last few pages
        are cut with an ellipsis, on purpose."""
        room = self._strip_right() - PAD - DOT - DOT_GAP
        return [self._tag] if QFontMetricsF(self._tag_font).horizontalAdvance(self._tag) > room else []

    def _strip_right(self) -> int:
        """Where the strip's words end: before the Hide."""
        return self.width() - PAD // 2 - self.hide_button.sizeHint().width() - TAG_GAP // 2

    # ---- where it goes -----------------------------------------------------------------------------------

    def place(self, avoid: QRect | None = None, screens: Sequence[QScreen] | None = None) -> None:
        """On a screen of its own if there is one, else the right part of the screen ``avoid`` (the HUD) is on."""
        known = list(screens) if screens is not None else list(QGuiApplication.screens())
        if not known:
            return
        here = QGuiApplication.screenAt(avoid.center()) if avoid is not None and avoid.isValid() else None
        here = here if here in known else (QGuiApplication.primaryScreen() if QGuiApplication.primaryScreen() in known
                                           else known[0])
        others = [screen for screen in known if screen is not here]
        target = others[0] if others else here
        self.setGeometry(watch_rect(target.availableGeometry(), avoid if target is here else None, bool(others)))

    # ---- Qt ------------------------------------------------------------------------------------------------

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.page_area.setGeometry(PAGE_INSET, STRIP_HEIGHT, max(1, self.width() - 2 * PAGE_INSET),
                                   max(1, self.height() - STRIP_HEIGHT - PAGE_INSET))
        if self._view is not None:
            self._view.setGeometry(self.page_area.rect())
        self._cover.setGeometry(self.page_area.rect())
        self._notice.setGeometry(self.page_area.rect())
        hint = self.hide_button.sizeHint()
        self.hide_button.setGeometry(self.width() - PAD // 2 - hint.width(), (STRIP_HEIGHT - 1 - hint.height()) // 2,
                                     hint.width(), hint.height())

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self.wheeled.emit(int(event.angleDelta().y()))
        event.accept()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(holo.alpha(holo.HOLO, 0.38), 1.0))
        painter.setBrush(QColor(holo.TILE))
        painter.drawRoundedRect(panel, hud_overlay.CARD_RADIUS, hud_overlay.CARD_RADIUS)
        painter.setPen(QPen(holo.LINE, 1.0))
        painter.drawLine(1, STRIP_HEIGHT - 1, self.width() - 2, STRIP_HEIGHT - 1)
        middle = (STRIP_HEIGHT - 1) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(holo.HOLO))
        painter.drawEllipse(QRectF(PAD, middle - DOT / 2, DOT, DOT))
        x = PAD + DOT + DOT_GAP
        strip = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        right = self._strip_right()
        painter.setFont(self._tag_font)
        painter.setPen(QColor(holo.HOLO))
        tag = QFontMetricsF(self._tag_font).horizontalAdvance(self._tag)
        painter.drawText(QRectF(x, 0, tag + 2, STRIP_HEIGHT - 1), strip, self._tag)
        x += tag + TAG_GAP
        older = _SEPARATOR.join(self._older)
        host = self._host
        if older:
            metrics = QFontMetricsF(self._older_font)
            wide = min(metrics.horizontalAdvance(older) + 2, max(0.0, (right - x) * 0.45))
            shown = metrics.elidedText(older, Qt.TextElideMode.ElideRight, wide)
            painter.setFont(self._older_font)
            painter.setPen(QColor(holo.INK_3))
            painter.drawText(QRectF(right - wide, 0, wide, STRIP_HEIGHT - 1),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, shown)
            right -= wide + TAG_GAP
        if host and right > x:
            metrics = QFontMetricsF(self._host_font)
            painter.setFont(self._host_font)
            painter.setPen(QColor(holo.INK_1))
            painter.drawText(QRectF(x, 0, right - x, STRIP_HEIGHT - 1), strip,
                             metrics.elidedText(host, Qt.TextElideMode.ElideRight, right - x))


# --------------------------------------------------------------------------
# The browser
# --------------------------------------------------------------------------

@dataclass
class _Request:
    """One page asked for (GUI thread). ``html``: local HTML (tests and harnesses). ``folder``: a task's file."""

    url: str
    html: str | None
    final: str
    folder: TaskFolder | None
    token: str
    done: Callable[[Any], None]
    label: str
    timeout_ms: int
    delivered: bool = False

    def __repr__(self) -> str:        # never the address
        return f"_Request(file={self.folder is not None}, delivered={self.delivered})"


# Pages the agent wrote whose profile is still there (GUI thread). The gate stays sealed until the last one is really
# deleted: by then the page's renderer can ask for nothing.
_HELD = [0]


def _hold_gate(profile: Any) -> None:
    """The network gate is sealed for as long as this profile exists."""
    _HELD[0] += 1
    profile.destroyed.connect(_let_gate_go)
    snapshot_engine.seal_gate(True)


def _let_gate_go(*_args: Any) -> None:
    _HELD[0] = max(0, _HELD[0] - 1)
    if not _HELD[0]:
        snapshot_engine.seal_gate(False)


def gate_held() -> bool:
    return _HELD[0] > 0


class AgentBrowser(PageCamera):
    """See the module docs. GUI thread only. ``done`` callables get their answer once, never inside a Qt signal of a
    page that is going away; a request that is replaced or stopped gets R_CANCELLED."""

    hideAsked = Signal()               # the strip's Hide was clicked
    windowPlaced = Signal()            # it came or moved

    def __init__(self, parent: QObject | None = None, *, html_for: Callable[[str], Any] | None = None,
                 avoid: Callable[[], QRect | None] | None = None, native: Any = None, hide_from_capture: bool = False,
                 screens: Callable[[], Sequence[QScreen]] | None = None,
                 decide: Callable[..., str] = snapshots.request_problem, settle_ms: int = SETTLE_MS,
                 linger_s: float = LINGER_S) -> None:
        super().__init__(parent, decide=decide, settle_ms=settle_ms)
        self._html_for = html_for
        self._avoid = avoid
        self._native = native
        self._hide_from_capture = bool(hide_from_capture)
        self._screens = screens
        self._linger_s = float(linger_s)
        self._window: WatchWindow | None = None
        self._request: _Request | None = None
        self._waiting: _Request | None = None    # a web page waits for a page the agent wrote to be really gone
        self._clear_tries = 0
        self._token = secrets.token_hex(8)
        self._shut = False
        self._clear = self._timer(self._on_clear)
        self._linger = self._timer(self.close_all)
        self._blank_wait = self._timer(self._on_blank_wait)
        self._blank_shot: Any = None       # a page waiting for its window's own about:blank to finish
        self._screen_hooks = False
        self._followed: list[QScreen] = []
        self._http_status = 0              # the status the page in hand answered with, when it is an error's
        self._status_address = ""          # ... and the address that answered it
        self._owner_hid = False            # the strip's Hide: off the screen until the panel's Show
        self._away = False                 # the owner brought the Jarvis window back: off the screen meanwhile
        self._rest = False                 # a file card waits: off the screen meanwhile (agent_feed)
        self._showable = False             # it has been shown, or would have been: it may come back
        self._tag = READING_TAG
        self._owner_view = False           # a task's file the owner asked to see: the wheel scrolls it
        self._other_way: list[str] = []    # hosts being read another way (fetch_text): the strip says so

    def __repr__(self) -> str:
        return f"AgentBrowser(page={self._shot is not None}, window={self._window is not None})"

    @property
    def token(self) -> str:
        """This run's part of a task's file's address: an address with another one is not this task's."""
        return self._token

    def window(self) -> WatchWindow | None:
        return self._window

    def has_page(self) -> bool:
        shot = self._shot
        return shot is not None and shot.page is not None and not shot.done

    def sealed(self) -> bool:
        return gate_held()

    # ---- asked for by the client ------------------------------------------------------------------------

    def load(self, url: str, timeout_s: float, done: Callable[[Any], None]) -> None:
        """A web page (on a test platform only local HTML: the ``html_for`` seam)."""
        html, final = self._local_page(url)
        self._start(_Request(url, html, final, None, "", done, link_host(url), _milliseconds(timeout_s)))

    def load_file(self, name: str, folder: TaskFolder, timeout_s: float, done: Callable[[Any], None]) -> None:
        """The file ``name`` of this task's folder, at an address with this run's token."""
        url = workspace_address(self._token, name)
        self._start(_Request(url, "", url, folder, self._token, done, f"{FOLDER_WORDS}{_SEPARATOR}{name}",
                             _milliseconds(timeout_s)))

    def script(self, source: str, done: Callable[[Any], None]) -> None:
        """``source`` in the isolated world of the page in the window: its answer, or NO_PAGE."""
        shot = self._shot
        if shot is None or shot.done or shot.page is None or not shot.opened:
            done(NO_PAGE)
            return
        shot.page.runJavaScript(source, snapshot_engine._classes().app_world, done)

    def picture(self, max_width: int, max_bytes: int, done: Callable[[Any], None]) -> None:
        """What the window shows, as JPEG, within the caps (made smaller when it must): R_LOAD_FAILED with no page,
        R_TOO_LARGE when even the smallest does not fit."""
        started = time.monotonic()
        view = self._view
        if view is None or not self.has_page():
            done(ShotResult(False, snapshots.R_LOAD_FAILED))
            return
        image = view.grab().toImage()
        if self.is_held() and snapshot_engine._uniform(image):
            done(ShotResult(False, snapshots.R_BLANK))     # off the screen, it may draw nothing: said plainly
            return
        if image.isNull() or image.width() < 2:
            done(ShotResult(False, snapshots.R_LOAD_FAILED))
            return
        width = min(int(max_width), image.width())
        while True:
            scaled = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(1.0)
            body, kind = encode_picture(scaled, int(max_bytes))
            if body:
                done(ShotResult(True, image=body, image_format=kind, width=scaled.width(), height=scaled.height(),
                                final_url=self._final_url(self._shot),
                                took_ms=int((time.monotonic() - started) * 1000)))
                return
            if width <= MIN_PICTURE_WIDTH:
                done(ShotResult(False, snapshots.R_TOO_LARGE))
                return
            width = max(MIN_PICTURE_WIDTH, int(width * PICTURE_STEP))

    def show_for_owner(self, name: str, folder: TaskFolder, done: Callable[[Any], None] | None = None) -> None:
        """The owner's Show on the result card: the file ``name`` of the task's folder, sealed as any task's page is, in
        this window - back on the screen - where the wheel scrolls it. Never the owner's browser or a default app."""
        self._owner_view = True
        self._tag = SHOWING_TAG
        self._owner_hid = self._away = self._rest = False
        self.load_file(name, folder, snapshots.TIMEOUT_S, done or (lambda _result: None))

    def set_hidden(self, *, owner: bool | None = None, away: bool | None = None) -> None:
        """Off the screen, or back on it: the owner's Hide and Show (``owner``), the Jarvis window coming back and going
        again (``away``). The page goes on either way."""
        if owner is not None:
            self._owner_hid = bool(owner)
        if away is not None:
            self._away = bool(away)
        self._follow_held()

    def set_resting(self, resting: bool) -> None:
        """Off the screen while a file card waits, or back on it (the owner's own Hide still holds then)."""
        self._rest = bool(resting)
        self._follow_held()

    def _follow_held(self) -> None:
        window = self._window
        if window is None:
            return
        if self.is_held():
            if window.isVisible():
                _quietly(window.hide)
        elif self._showable and not window.isVisible():
            self._show_window(window)

    def is_held(self) -> bool:
        return self._owner_hid or self._away or self._rest

    def other_way(self, host: str, reading: bool) -> None:
        """fetch_text starts or ends: the strip says this page is being read another way, not shown here."""
        if reading:
            self._other_way.append(host)
        elif host in self._other_way:
            self._other_way.remove(host)
        window = self._window
        if window is not None:
            window.set_tag(OTHER_WAY_TAG if self._other_way else self._tag)

    def keep_clear(self) -> None:
        """The HUD grew under the window (a chip, a longer caption): placed again when it now crosses it."""
        window = self._window
        if window is None or not window.isVisible() or self._avoid is None:
            return
        try:
            avoid = self._avoid()
        except Exception:  # noqa: BLE001 - only where it goes
            return
        if avoid is not None and avoid.isValid() and avoid.intersects(window.geometry()):
            self._place()

    def stop_now(self) -> None:
        """The page goes, and whoever waits for it gets R_CANCELLED. The window stays until end_run."""
        self._cancel_request()
        self.abort()

    def end_run(self, linger: bool, hold: bool = False) -> None:
        """The run ended: no page after this. ``linger``: the window keeps a still of it for a while, then goes; with
        ``hold`` until close_all (it stays while the result card stays)."""
        self._linger.stop()
        window = self._window
        still = self._still() if linger else None
        self._cancel_request()
        self.abort()
        self._other_way.clear()
        if window is not None and linger and window.isVisible() and (hold or self._linger_s > 0):
            self._tag = FINISHED_TAG
            window.set_tag(FINISHED_TAG)
            if still is not None:
                window.cover(still)
            if not hold:
                self._linger.start(int(self._linger_s * 1000))
            return
        self.close_all()

    def close_all(self) -> None:
        """Nothing is left: no page, no renderer, no window, and the gate open again once they are deleted. Also with no
        event loop left (Jarvis closing), when everything is deleted now."""
        self._linger.stop()
        self._cancel_request()
        window, self._window = self._window, None
        self.close_view()
        if window is not None:
            _quietly(window.hide)
            if snapshot_engine._event_loop_running():
                _quietly(window.deleteLater)
            else:
                _quietly(lambda: snapshot_engine._delete_now(window))

    def shutdown(self) -> None:
        self._shut = True
        self.close_all()

    # ---- starting a page ------------------------------------------------------------------------------------

    def _local_page(self, url: str) -> tuple[str | None, str]:
        if self._html_for is None:
            return None, ""
        try:
            page = self._html_for(url)
        except Exception:  # noqa: BLE001 - no such page
            return None, ""
        if isinstance(page, tuple) and len(page) == 2 and isinstance(page[0], str):
            return page[0], page[1] if isinstance(page[1], str) else ""
        return (page, "") if isinstance(page, str) else (None, "")

    def _cancel_request(self) -> None:
        """Whoever still waits gets R_CANCELLED."""
        waiting, self._waiting = self._waiting, None
        request = self._request
        for item in (waiting, request):
            if item is not None and not item.delivered:
                item.delivered = True
                _answer(item.done, ShotResult(False, snapshots.R_CANCELLED))

    def _start(self, request: _Request) -> None:
        if self._shut:
            _answer(request.done, ShotResult(False, snapshots.R_SHUTDOWN))
            return
        self._linger.stop()
        self._cancel_request()
        still = self._still()
        self.abort()                       # one page at a time: the one before goes, its profile with it
        if self._window is not None:
            self._window.set_tag(OTHER_WAY_TAG if self._other_way else self._tag)
            if still is not None:
                self._window.cover(still)
        self._request = None
        if request.folder is None and gate_held():
            self._waiting = request        # a web page opens only once the page the agent wrote is really gone
            self._clear_tries = 0
            self._clear.start(CLEAR_MS)
            return
        self._go(request)

    def _go(self, request: _Request) -> None:
        self._request = request
        self._timeout_ms = max(100, request.timeout_ms)
        self._http_status, self._status_address = 0, ""
        if self._window is not None:
            self._window.set_notice()
            self._window.set_page(request.label)
        if request.html is None:
            self.shoot(request.url)
        else:
            self.shoot(request.url, request.html, final_url=request.final)

    def _on_clear(self) -> None:
        """A web page waits while the gate is sealed. If the page the agent wrote never goes, it stays sealed and what
        waited fails, closed."""
        waiting = self._waiting
        if waiting is None:
            return
        if not gate_held():
            self._waiting = None
            self._go(waiting)
        elif self._clear_tries >= CLEAR_TRIES:
            self._waiting = None
            logger.warning("Agent browser: a page of the task's did not go in time; the next page was not opened")
            _answer(waiting.done, ShotResult(False, snapshots.R_LOAD_FAILED))
        else:
            self._clear_tries += 1
            self._clear.start(CLEAR_MS)

    def _still(self) -> QPixmap | None:
        view = self._view
        if view is None or not self.has_page() or not view.isVisible():
            return None
        try:
            return view.grab()
        except Exception:  # noqa: BLE001 - only a nicety
            return None

    # ---- PageCamera's seams ------------------------------------------------------------------------------------

    def _new_view(self, classes: Any) -> Any:
        """The view, in a window that shows it: never the focus, no mouse, no keyboard, nothing dropped on it."""
        window = self._window
        if window is None:
            window = self._window = WatchWindow(native=self._native, hide_from_capture=self._hide_from_capture)
            window.hideClicked.connect(self._on_hide_clicked)
            window.wheeled.connect(self._on_wheel)
            window.set_tag(self._tag)
            if self._request is not None:
                window.set_page(self._request.label)
        view = classes.QWebEngineView(window.page_area)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setEnabled(False)             # also its inner widgets, whenever they come: no focus can land there
        view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAcceptDrops(False)
        view.setAccessibleName("")
        window.set_view(view)
        return view

    def _on_hide_clicked(self) -> None:
        self.set_hidden(owner=True)
        self.hideAsked.emit()

    def _on_wheel(self, delta: int) -> None:
        """Only a task's file the owner asked to see scrolls, in Jarvis's own script world: while Jarvis reads, the
        window is his to drive."""
        shot = self._shot
        if self._owner_view and delta and shot is not None and shot.page is not None and not shot.done and shot.opened:
            step = round(-delta / 120 * WHEEL_STEP)
            shot.page.runJavaScript(f"window.scrollBy(0, {step});", snapshot_engine._classes().app_world)

    def _connect(self, page: Any, shot: Any) -> None:
        super()._connect(page, shot)
        page.loadingChanged.connect(lambda info: self._on_loading(shot, info))

    def _on_loading(self, shot: Any, info: Any) -> None:
        """The main frame's load: its HTTP status when it answered with an error. It stands through what the engine loads
        in that address's place (an empty answer's blank page, measured: it may even "succeed"), until another address
        loads. Nothing is tried again here."""
        if shot is not self._shot or shot.done or not shot.opened:
            return
        try:
            status, domain, address = info.status().name, info.errorDomain().name, info.url().toString()
            if domain == "HttpStatusCodeDomain":
                self._http_status, self._status_address = int(info.errorCode()), address
            elif status in ("LoadStartedStatus", "LoadSucceededStatus") and address != self._status_address                     and not address.startswith("chrome-error:"):
                self._http_status = 0
        except Exception as exc:  # noqa: BLE001 - only how a failure is told apart
            logger.debug("Agent browser: a load's state could not be read (%s)", type(exc).__name__)

    def _configure(self, profile: Any, classes: Any, shot: Any) -> None:
        super()._configure(profile, classes, shot)
        request = self._request
        if request is not None and request.folder is not None:
            handler = _handler_class()(lambda job: self._serve(request, job), profile)
            self._retired.append(handler)  # kept until Qt deleted it with its profile
            profile.installUrlSchemeHandler(_SCHEME_BYTES, handler)

    def _serve(self, request: _Request, job: Any) -> None:
        """A request for a task's file: only that task's, read for this request (workspace.TaskFolder's rules)."""
        name = workspace_name(job.requestUrl().path(QUrl.ComponentFormattingOption.FullyEncoded), request.token)
        if not name or bytes(job.requestMethod().data()).upper() != b"GET" or request.folder is None:
            _refuse_job(job)
            return
        try:
            body = request.folder.body(name)
        except WorkspaceRefused:
            job.fail(type(job).Error.UrlNotFound)
            return
        buffer = QBuffer(job)
        buffer.setData(body)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(file_kind(name).encode("ascii"), buffer)

    def _problem(self, shot: Any, url: QUrl, main_frame: bool) -> str:
        request = self._request
        if request is None or request.folder is None:
            return super()._problem(shot, url, main_frame)
        scheme = url.scheme().casefold()
        if scheme == "about":
            return "" if url.path().casefold() in ("blank", "srcdoc") else snapshots.BLOCKED
        if scheme == SCHEME and workspace_name(url.path(QUrl.ComponentFormattingOption.FullyEncoded), request.token):
            return ""
        return snapshots.BLOCKED

    def _load_target(self, shot: Any) -> None:
        """The page, once its window's own about:blank has finished: an about:blank that finishes after the page was asked
        for takes the one load signal the engine gives, and the page's HTTP status goes unheard (measured)."""
        request = self._request
        if request is not None and request.folder is not None:
            _hold_gate(shot.profile)
        if not shot.page.isLoading():
            self._ask_for_page(shot)
            return
        shot.opened = False                # as before the sandbox check: nothing loads or counts until it is asked for
        shot.page.loadFinished.connect(lambda _ok: self._blank_done(shot))
        self._blank_wait.start(BLANK_WAIT_MS)
        self._blank_shot = shot

    def _blank_done(self, shot: Any) -> None:
        if self._blank_shot is shot:
            self._blank_shot = None
            self._blank_wait.stop()
            shot.opened = True
            if shot is self._shot and not shot.done:
                self._ask_for_page(shot)

    def _on_blank_wait(self) -> None:
        if self._blank_shot is not None:
            self._blank_done(self._blank_shot)

    def _ask_for_page(self, shot: Any) -> None:
        request = self._request
        try:
            if request is not None and request.folder is not None:
                shot.page.load(QUrl(request.url))
            else:
                super()._load_target(shot)
        except Exception as exc:  # noqa: BLE001 - as the camera's own load
            logger.debug("Agent browser: the page was not loaded (%s)", type(exc).__name__)
            self._end(shot, snapshots.R_ENGINE_FAILED)

    def _on_adopted(self, shot: Any) -> None:
        window = self._window
        if window is not None and shot is self._shot and not shot.done:
            self._show_window(window)
        super()._on_adopted(shot)
        self._no_focus_inside()

    def _no_focus_inside(self) -> None:
        """The view's inner widgets, whichever the engine has made by now, as the HUD's: never the focus."""
        view = self._view
        for child in view.findChildren(QWidget) if view is not None else ():
            if child.focusPolicy() != Qt.FocusPolicy.NoFocus:
                child.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _show_window(self, window: WatchWindow) -> None:
        """Quietly, never activated. It appears at the first web or show step and stays where it is put, unless the
        owner hid it or is back in the Jarvis window (``set_hidden``)."""
        self._showable = True
        if window.isVisible() or self.is_held():
            return
        self._place()
        if not self._screen_hooks:
            self._screen_hooks = True
            app = QGuiApplication.instance()
            if app is not None:
                app.screenAdded.connect(self._on_screens)
                app.screenRemoved.connect(self._on_screens)
            self._follow_work_areas()
        window.show_quietly()
        self.windowPlaced.emit()

    def _follow_work_areas(self) -> None:
        for screen in QGuiApplication.screens():
            if screen not in self._followed:
                self._followed.append(screen)
                screen.availableGeometryChanged.connect(self._on_screens)

    def _on_screens(self, *_args: Any) -> None:
        """Screens came or went, or a work area changed: still on screen."""
        self._followed = [screen for screen in self._followed if screen in QGuiApplication.screens()]
        self._follow_work_areas()
        self._place()

    def _place(self) -> None:
        window = self._window
        if window is None:
            return
        avoid = None
        if self._avoid is not None:
            try:
                avoid = self._avoid()
            except Exception:  # noqa: BLE001 - only where it goes
                avoid = None
        window.place(avoid, self._screens() if self._screens is not None else None)
        self.windowPlaced.emit()

    def _on_progress(self, shot: Any, value: int) -> None:
        super()._on_progress(shot, value)
        window = self._window
        if window is not None and shot.opened and int(value) >= COVER_OFF_PROGRESS and window.covered():
            window.cover(None)

    def _grab(self, shot: Any, *, final: bool, still_loading: bool = False) -> None:
        """Where the camera takes its picture, the page is ready to read: whoever waits for it is told. The page
        stays."""
        request = self._request
        if request is None or request.delivered or shot is not self._shot or shot.done:
            return
        if self._http_status in REFUSAL_STATUSES:
            self._end(shot, snapshots.R_LOAD_FAILED)     # it came with a page of its own: a refusal all the same
            return
        try:
            if not final and shot.page is not None and shot.page.isLoading():
                self._settle.start(self._settle_ms)   # a redirect by script: the page it leads to is the one to read
                return
            view = self._view
            # (A view off the screen grabs blank every time, and its text is what is read: no second for a picture.)
            if not final and shot.tries < snapshots.BLANK_RETRIES and view is not None and not self.is_held() \
                    and snapshot_engine._uniform(view.grab().toImage()):
                shot.tries += 1            # nothing drawn yet: a moment more, as the camera gives one
                self._settle.start(self._retry_ms)
                return
        except Exception as exc:  # noqa: BLE001 - it can still be read
            logger.debug("Agent browser: the first look failed (%s)", type(exc).__name__)
        for timer in (self._timeout, self._settle, self._fail_check):
            timer.stop()
        request.delivered = True
        final_url = self._final_url(shot)
        self._no_focus_inside()
        window = self._window
        if window is not None:
            window.cover(None)
            if request.folder is None:
                window.update_host(link_host(final_url))
        took = int((time.monotonic() - shot.started) * 1000)
        logger.info("Agent browser: a page is up in %.1f s (%d request(s) blocked%s)", took / 1000, shot.blocked,
                    ", still loading" if still_loading else "")
        _answer(request.done, ShotResult(True, final_url=final_url, took_ms=took, still_loading=still_loading,
                                         blocked=shot.blocked))

    def _end(self, shot: Any, reason: str, **data: Any) -> None:
        if shot.done:
            return
        status = self._http_status if reason == snapshots.R_LOAD_FAILED and self._http_status in REFUSAL_STATUSES else 0
        reason = snapshots.R_SITE_REFUSED if status else reason
        blocked, took = shot.blocked, int((time.monotonic() - shot.started) * 1000)
        super()._end(shot, reason, **data)
        request, self._request = self._request, None
        if request is not None and not request.delivered:
            request.delivered = True
            logger.info("Agent browser: no page (%s%s)", snapshots.reason_kind(reason),
                        f", HTTP {status}" if status else "")
            self._say_why(request, reason)
            _answer(request.done, ShotResult(False, reason or snapshots.R_ENGINE_FAILED, took_ms=took, blocked=blocked,
                                             http_status=status))
        elif request is not None and reason and reason != snapshots.R_CANCELLED:
            logger.info("Agent browser: the page went (%s)", snapshots.reason_kind(reason))

    def _say_why(self, request: _Request, reason: str) -> None:
        """A web page that did not come leaves words on the dark page, not emptiness (hosts only)."""
        window = self._window
        if window is None or request.folder is not None or not request.label:
            return
        kind = snapshots.reason_kind(reason)
        if kind == "site_refused":
            window.cover(None)
            window.set_notice(REFUSED_WORDS.format(host=request.label), REFUSED_NOTE)
        elif kind in ("load_failed", "timeout", "crashed", "blank"):
            window.cover(None)
            window.set_notice(NOT_LOADED_WORDS.format(host=request.label), NOT_LOADED_NOTE)

    @staticmethod
    def _final_url(shot: Any) -> str:
        if shot is None:
            return ""
        if not shot.local and shot.page is not None:
            return snapshot_engine._encoded(shot.page.url()) or shot.url
        return shot.final_hint or shot.url


def _milliseconds(seconds: float) -> int:
    return max(100, int(float(seconds) * 1000))


def _answer(done: Callable[[Any], None], value: Any) -> None:
    try:
        done(value)
    except Exception as exc:  # noqa: BLE001 - whoever asked has gone: nothing to tell
        logger.debug("Agent browser: an answer went nowhere (%s)", type(exc).__name__)


def _quietly(step: Callable[[], Any]) -> None:
    try:
        step()
    except Exception:  # noqa: BLE001 - it is gone already
        pass


# --------------------------------------------------------------------------
# From any thread
# --------------------------------------------------------------------------

class _Job:
    """One piece of work for the GUI thread and the thread that waits for it. A result after ``dropped`` goes
    nowhere."""

    __slots__ = ("op", "args", "done", "value", "dropped")

    def __init__(self, op: str, args: tuple[Any, ...]) -> None:
        self.op = op
        self.args = args
        self.done = threading.Event()
        self.value: Any = None
        self.dropped = False

    def __repr__(self) -> str:        # never its arguments
        return f"_Job({self.op}, done={self.done.is_set()})"

    def finish(self, value: Any = None) -> None:
        self.value = value
        self.done.set()


_GAVE_UP, _CANCELLED = object(), object()


class BrowserClient(QObject):
    """agent.web's Browser seam: made and ended on the GUI thread, called from any. One per run; the page and window
    come only with the first web or show step, so a task that never reaches the web starts nothing. See the module
    docs."""

    _post = Signal(object)
    _drop = Signal(object)
    _note = Signal(str, bool)
    hideAsked = Signal()               # the strip's Hide was clicked
    windowPlaced = Signal()            # the window came or moved

    def __init__(self, parent: QObject | None = None, *, factory: Callable[[QObject], AgentBrowser] | None = None,
                 poll_s: float = 0.02, **options: Any) -> None:
        super().__init__(parent)
        self._factory = factory
        self._options = options
        self._poll_s = float(poll_s)
        self._browser: AgentBrowser | None = None
        self._ended = threading.Event()
        self._owner_hid = False
        self._away = False
        self._rest = False
        self._post.connect(self._run, Qt.ConnectionType.QueuedConnection)
        self._drop.connect(self._dropped, Qt.ConnectionType.QueuedConnection)
        self._note.connect(self._on_note, Qt.ConnectionType.QueuedConnection)

    def __repr__(self) -> str:
        return f"BrowserClient(browser={self._browser is not None}, ended={self._ended.is_set()})"

    # ---- any thread ---------------------------------------------------------------------------------------

    def scheme_ready(self) -> bool:
        return scheme_ready()

    def other_way(self, host: str, reading: bool) -> None:
        """fetch_text starts or ends (any thread; never waits). A task with no window yet makes none for it."""
        try:
            self._note.emit(host, bool(reading))
        except RuntimeError:           # the client went with the app
            pass

    def load(self, url: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> ShotResult:
        return self._loaded(self._ask("load", (url, timeout_s), timeout_s + LOAD_MARGIN_S, cancelled))

    def load_file(self, name: str, folder: TaskFolder, *, timeout_s: float,
                  cancelled: Callable[[], bool]) -> ShotResult:
        return self._loaded(self._ask("load_file", (name, folder, timeout_s), timeout_s + LOAD_MARGIN_S, cancelled))

    def script(self, source: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> Any:
        """The script's answer. None: it did not answer in time, and the page was closed (a hung page); NO_PAGE."""
        value = self._ask("script", (source,), timeout_s, cancelled)
        return None if value is _GAVE_UP or value is _CANCELLED else value

    def picture(self, max_width: int, max_bytes: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> ShotResult:
        value = self._ask("picture", (max_width, max_bytes), timeout_s, cancelled)
        return value if isinstance(value, ShotResult) else ShotResult(False, snapshots.R_LOAD_FAILED)

    @staticmethod
    def _loaded(value: Any) -> ShotResult:
        if isinstance(value, ShotResult):
            return value
        return ShotResult(False, snapshots.R_TIMEOUT if value is _GAVE_UP else snapshots.R_CANCELLED)

    def _ask(self, op: str, args: tuple[Any, ...], wait_s: float, cancelled: Callable[[], bool]) -> Any:
        """The work on the GUI thread; this thread waits ``wait_s`` at most, and not a moment after a Stop."""
        if self._ended.is_set() or cancelled():
            return _CANCELLED
        job = _Job(op, args)
        try:
            self._post.emit(job)
        except RuntimeError:           # the client went with the app
            return _CANCELLED
        deadline = time.monotonic() + max(0.0, wait_s)
        while not job.done.wait(self._poll_s):
            if self._ended.is_set() or cancelled():
                self._give_up(job)
                return _CANCELLED
            if time.monotonic() >= deadline:
                self._give_up(job)
                return _GAVE_UP
        return job.value

    def _give_up(self, job: _Job) -> None:
        job.dropped = True
        try:
            self._drop.emit(job)
        except RuntimeError:
            pass

    # ---- GUI thread -----------------------------------------------------------------------------------------

    def browser(self) -> AgentBrowser | None:
        return self._browser

    def _ensure(self) -> AgentBrowser:
        if self._browser is None:
            make = self._factory or (lambda parent: AgentBrowser(parent, **self._options))
            self._browser = make(self)
            for name, slot in (("hideAsked", self._on_hide_asked), ("windowPlaced", self.windowPlaced)):
                signal = getattr(self._browser, name, None)
                if signal is not None:
                    signal.connect(slot)
            hidden = getattr(self._browser, "set_hidden", None)
            if callable(hidden):
                hidden(owner=self._owner_hid, away=self._away)
            resting = getattr(self._browser, "set_resting", None)
            if callable(resting):
                resting(self._rest)
        return self._browser

    def _on_hide_asked(self) -> None:
        self._owner_hid = True
        self.hideAsked.emit()

    def _on_note(self, host: str, reading: bool) -> None:
        if self._browser is not None:
            self._browser.other_way(host, reading)

    def set_hidden(self, *, owner: bool | None = None, away: bool | None = None) -> None:
        """Off the screen or back on it (AgentBrowser.set_hidden); it holds for the window yet to come too."""
        if owner is not None:
            self._owner_hid = bool(owner)
        if away is not None:
            self._away = bool(away)
        if self._browser is not None:
            self._browser.set_hidden(owner=owner, away=away)

    def set_resting(self, resting: bool) -> None:
        """AgentBrowser.set_resting; it holds for the window yet to come too."""
        self._rest = bool(resting)
        if self._browser is not None:
            self._browser.set_resting(self._rest)

    def _has_window(self) -> bool:
        return self._browser is not None and self._browser.window() is not None

    def resting(self) -> bool:
        """A window that is there is off the screen for a file card."""
        return self._rest and self._has_window()

    def owner_hid(self) -> bool:
        """The owner's Hide holds: while the run goes on (for the window yet to come too), and after it for as long as
        there is a window to bring back: a hidden window goes with the run's end, and Show would bring nothing."""
        return self._owner_hid and (not self._ended.is_set() or self._has_window())

    def window_rect(self) -> QRect | None:
        """Where the window is, while it shows."""
        window = self._browser.window() if self._browser is not None else None
        return window.geometry() if window is not None and window.isVisible() else None

    def keep_clear(self) -> None:
        if self._browser is not None:
            self._browser.keep_clear()

    def show_for_owner(self, name: str, folder: TaskFolder, done: Callable[[Any], None] | None = None) -> None:
        """The owner's Show on the result card, after the run (GUI thread): AgentBrowser.show_for_owner."""
        self._owner_hid = self._away = self._rest = False
        self._ensure().show_for_owner(name, folder, done)

    def _run(self, job: _Job) -> None:
        if job.dropped or self._ended.is_set():
            job.finish(_CANCELLED)
            return
        try:
            getattr(self._ensure(), job.op)(*job.args, done=job.finish)
        except Exception as exc:  # noqa: BLE001 - a broken step is a plain answer, never a waiter left hanging
            logger.warning("Agent browser: a step failed (%s)", type(exc).__name__)
            shot_op = job.op in ("load", "load_file", "picture")
            job.finish(ShotResult(False, snapshots.R_ENGINE_FAILED) if shot_op else None)

    def _dropped(self, job: _Job) -> None:
        """Nobody waits for that any more: a page that kept it waiting goes (a hung renderer spins no longer)."""
        if self._browser is not None and not self._ended.is_set() and not job.done.is_set():
            self._browser.stop_now()

    def stop(self) -> None:
        """A Stop or a Turn off: waiters are released now, the page and the window go at once (one that lingers too)."""
        self._ended.set()
        if self._browser is not None:
            self._browser.end_run(False)

    def end_run(self, *, linger: bool = True, hold: bool = False) -> None:
        """The run ended: waiters are released, no page after this. Once: what came first stands. ``hold``: a window
        that lingers stays until close_now (the result card's)."""
        if self._ended.is_set():
            return
        self._ended.set()
        if self._browser is not None:
            self._browser.end_run(linger, hold)

    def close_now(self) -> None:
        """A new run starts: the last run's window, if it still lingers, goes first (one window)."""
        self._ended.set()
        browser, self._browser = self._browser, None
        if browser is not None:
            browser.close_all()

    def shutdown(self) -> None:
        """Jarvis closes: nothing is left."""
        self._ended.set()
        browser, self._browser = self._browser, None
        if browser is not None:
            browser.shutdown()


__all__ = ["AgentBrowser", "BrowserClient", "FINISHED_TAG", "READING_TAG", "WatchWindow", "file_kind", "gate_held",
           "register_scheme", "scheme_ready", "watch_rect"]
