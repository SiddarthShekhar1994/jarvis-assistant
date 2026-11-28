"""The agent's own browser: the window the owner watches (SPEC D6, D7, section 8; part A2). Qt, GUI thread only, and the
one file of this package that imports Qt; BrowserClient is how the endpoint's threads reach it.

    register_scheme()     Jarvis's own address scheme for a task's files: before the QApplication, like the web engine's
                          other setup, and only while tasks are on (__main__)
    AgentBrowser          a real page in a visible window, one at a time. It IS snapshot_engine.PageCamera, whose every
                          rule stands: a fresh off-the-record profile and page for each page opened (but a listed site's
                          page of open_page's: Jarvis's sign-in profile, read only - below), the page's window
                          sandboxed for life with no downloads, modals, forms or top navigation, the request
                          interceptor, permissions, pop-ups, dialogs, file choosers, sign-in prompts and client
                          certificates refused, every connection through the network gate (public IPv4, port 443),
                          Chromium's flags, and no network page at all on a test platform. What differs: the view lives
                          in the window, a page stays open until the next one, and the shot's "picture" is the moment it
                          is ready to read
    WatchWindow           the window: frameless, the HUD's flags and tokens, never activated and never the focus, a thin
                          strip with the tag "Jarvis is reading", the host, the last few pages and a small Hide, over
                          the page. On a second screen if there is one, else the right part of the main screen, clear of
                          the HUD, kept on screen (the new look, part 7: on a second screen the display's placement
                          broker follows it and keeps the display's strip clear; with the display off it goes where it
                          always did). Mouse and keyboard off: it is Jarvis's to drive. A page that did not
                          come leaves words on its dark page, not emptiness: a refusal (HTTP 401, 403, 429) says the
                          site keeps automated browsers out
    BrowserClient         agent.web's Browser seam, for any thread: hands work to the GUI thread and waits, with a
                          timeout; a Stop or Turn off releases a waiter at once and a late result is dropped (R8). It
                          is agent.drive's DriveBrowser too: tabs, act, the seal and the owner's approved press

Filling in pages (build plan item 7; AgentBrowser(drive=True), only for a task the owner let fill in pages). Up to
TAB_LIMIT tabs, each its own page and off-the-record profile (a new page in a tab is a new profile, as ever), one view
on the window showing the active one; the strip names them as chips, the active one lit. Only a drive tab's window gets
snapshot_engine.DRIVE_SANDBOX (forms may be sent); research pictures and a task's own files keep SANDBOX. act() runs a
drive script in that tab's isolated world and then waits for a navigation it started, or a moment; a ring drawn by Qt
over the view (never put into the page) shows the element acted on. seal(cut) seals the network gate for its own
reason (snapshot_engine.seal_drive) and ends every connection open then (cut_gate); from then every request and
navigation of every page of the window is refused here too ("held" in hook_counts), and no web page loads, until the
owner's click on the Send card (press_approved: the seal lifts, then the same element is pressed) or until the page is
destroyed (close_tab with unseal, a Stop, the run's end, Jarvis closing): its page and profile go first, and the seal
lifts only once they are really deleted. At the run's end with a result to show, the window may "open big" (end_run
with big): ~80% of its screen, never activated, nothing of the page loading meanwhile, then its still as ever.
The owner's mouse and keyboard stay off the window (no take-over); instead a filling-in window's strip has "Open in my
browser" while a public page is shown and nothing typed is held: on a click, that page's address alone (no value, no
cookie, nothing of the profile) goes to the owner's own browser, through the same link check as a research card's Open
(actions.web_link_problem, a strict https address on the host it names). It is ``open_url`` (tests fake it; under the
test guard the real one opens nothing).

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

Sites the owner listed (site_signins; Settings > LIVE and privacy, where he signs in himself in a window of Jarvis's
own): open_page's load (load_signed_in) of a page on one of them opens with Jarvis's sign-in profile instead of a fresh
one, lent by its lender (``signins``: site_signins_ui.SignInProfile) to one shot at a time and given back, never
deleted, when that page goes; anything else (a search, a research picture, a task's file, a second tab while one holds
it) stays off the record. Such a page is read only: a main or inline frame asked for with anything but GET or HEAD,
and any form submit, is refused unless the owner's click on its Send card presses it now (press_approved), and its
main frame never leaves the listed sites. Its ShotResult says ``signed_in``. Downloads stay refused as on every page.

Never a disguise: the user agent and every header stay the engine's own (nothing here sets one), a refused page is not
tried again with another identity, and no captcha is answered.

Logs: counts, kinds and times only, never an address, a host, a title, a name or a word of a page.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject, QRect, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QGuiApplication, QPainter, QPen, QPixmap, QScreen
from PySide6.QtWidgets import QWidget

from .. import holo, hud, hud_overlay, site_signins, snapshot_engine, snapshots
from ..actions import link_host, web_link_problem
from ..snapshot_engine import PageCamera, encode_picture
from ..snapshots import ShotResult
from .drive import MAX_TABS, STORE, ActResult
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
OPEN_TEXT = "Open in my browser"   # a filling-in window's strip: the page's address in the owner's browser, on a click
OPEN_GUARD_S = 1.0                # a second Open this soon after the first does nothing
HIDE_SIZE = 26                    # the strip's Hide (its drawn height: the strip is 36 high)
REFUSED_WORDS = "{host} keeps automated browsers out"
REFUSED_NOTE = "Jarvis doesn't try to get past that. It will look elsewhere."
NOT_LOADED_WORDS = "{host} did not load"
NOT_LOADED_NOTE = "It may be gone, or be something Jarvis doesn't open. It will look elsewhere."
REFUSAL_STATUSES = frozenset({401, 403, 429})
# The engine's word for a response a sandboxed window may not open: an opener policy, as a site's bot check sends and some
# ordinary pages do. No page and no status come of it, and a refusal is what it is: Jarvis doesn't lift the sandbox for it.
TURNED_AWAY_ERROR = "ERR_BLOCKED_BY_RESPONSE"
TURNED_AWAY_WORDS = "{host} doesn't open in this window"
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

# Filling in pages (drive tabs).
TAB_LIMIT = MAX_TABS              # tabs at once, each its own page and profile
ACT_POLL_MS = 25                  # how often a drive script's wait looks at its page
ACT_MARGIN_S = 1.0                # a waiter allows the GUI thread this long past an act's own time limit
NAV_SETTLE_S = SETTLE_MS / 1000   # after a navigation an act started finished, before it counts as arrived
EMPTY_TAB_WORDS = "A new tab"
NEW_TAB_CHIP = "New tab"
CHIP_HEIGHT = 22
CHIP_PAD = 10
CHIP_GAP = 6
CHIP_MAX = 200
RING_MS = 1600                    # the ring stays this long over the element acted on
RING_PAD = 4
RING_RADIUS = 6
BIG_SHARE = 0.8                   # "opens big": of its screen's work area
BIG_SETTLE_MS = 600               # the page draws itself at the new size before its still is taken
_RING_OPS = frozenset({"type", "choose", "click", "press", "scroll"})
_DRIVE_MARK = re.compile(r"/\*drive:(\w+)\*/\nvar ARGS = (.*);\n")
_LOCAL_SCHEMES = frozenset({"about", "data", "blob"})
_PAGE_OPS = frozenset({"load", "load_signed_in", "load_file", "script", "picture"})
_ACT_OPS = frozenset({"act", "press_approved"})
# The element a drive script acted on, read in Jarvis's own world (the page's scripts can't see or change it).
_RING_SCRIPT = ("(function () { var store = window[STORE]; if (!store || store.gen !== GEN) { return null; }"
                " var el = store.els[N - 1]; if (!el || !el.isConnected) { return null; }"
                " var box = Element.prototype.getBoundingClientRect.call(el);"
                " return JSON.stringify([box.left, box.top, box.width, box.height]); })()"
                ).replace("STORE", json.dumps(STORE))


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


def big_rect(area: QRect, now: QRect | None = None) -> QRect:
    """"Opens big": BIG_SHARE of the work area ``area`` in its middle, never smaller in either way than the window is
    ``now`` (on a screen of its own it nearly fills it already); always inside the area."""
    width = round(area.width() * BIG_SHARE)
    height = round(area.height() * BIG_SHARE)
    if now is not None and now.isValid():
        width, height = max(width, now.width()), max(height, now.height())
    width, height = max(1, min(area.width(), width)), max(1, min(area.height(), height))
    return QRect(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2, width, height)


class _Ring(QWidget):
    """The ring over the element Jarvis acts on: drawn here, over the view, never put into the page."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._rect: QRectF | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_ring(self, rect: QRectF | None) -> None:
        self._rect = QRectF(rect) if rect is not None and rect.isValid() and not rect.isEmpty() else None
        self.setVisible(self._rect is not None)
        if self._rect is not None:
            self.raise_()
            self.update()

    def ring_rect(self) -> QRectF | None:
        return QRectF(self._rect) if self._rect is not None else None

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if self._rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(holo.HOLO), 2.0))
        painter.setBrush(holo.alpha(holo.HOLO, 0.08))
        painter.drawRoundedRect(self._rect.adjusted(-RING_PAD, -RING_PAD, RING_PAD, RING_PAD), RING_RADIUS, RING_RADIUS)


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


def owner_browser(url: str) -> None:
    """The owner's own browser opens ``url`` ("Open in my browser"; the address is checked before this). Under the test
    guard it opens nothing: no test ever reaches a real browser."""
    from ..desktop.backend import real_backend_blocked

    if real_backend_blocked():
        logger.info("Agent browser: no browser under the test guard")
        return
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url, QUrl.ParsingMode.StrictMode))


class WatchWindow(hud_overlay._OverlayWindow):  # noqa: SLF001 - the HUD's own window behaviour, on purpose
    """See the module docs. Holds the view (``page_area``); knows nothing of what the page is."""

    hideClicked = Signal()             # the strip's Hide (the task carries on behind it)
    openClicked = Signal()             # the strip's "Open in my browser" (a filling-in window's, when it shows)
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
        self._chip_font = hud.body_font(12)
        self._tabs: tuple[tuple[str, bool], ...] = ()
        self.page_area = QWidget(self)
        self.page_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._view: QWidget | None = None
        self._ring = _Ring(self.page_area)
        self._cover = _Cover(self.page_area)
        self._notice = _Notice(self.page_area)
        self.hide_button = holo.HoloButton(HIDE_TEXT, holo.GHOST, size=HIDE_SIZE, parent=self)
        self.hide_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide_button.clicked.connect(self.hideClicked)
        self.open_button = holo.HoloButton(OPEN_TEXT, holo.GHOST, size=HIDE_SIZE, parent=self)
        self.open_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_button.setAccessibleName("Open this page in your own browser")
        self.open_button.clicked.connect(self.openClicked)
        self.open_button.hide()
        self.resize(MIN_WIDTH, MIN_HEIGHT)

    # ---- what it shows -------------------------------------------------------------------------------

    def set_view(self, view: QWidget) -> None:
        self._view = view
        view.setGeometry(self.page_area.rect())
        self._ring.raise_()
        self._cover.raise_()
        self._notice.raise_()

    def set_tabs(self, chips: Sequence[tuple[str, bool]]) -> None:
        """The tabs as (host, shown) chips, in their order (with one tab or none the strip names the page as ever)."""
        tabs = tuple((" ".join((host or "").split())[:HOST_CAP], bool(shown)) for host, shown in chips)
        if tabs != self._tabs:
            self._tabs = tabs
            self.update()

    def tab_chips(self) -> tuple[tuple[str, bool], ...]:
        return self._tabs

    def set_open_shown(self, shown: bool) -> None:
        """"Open in my browser" on the strip, or not (only while there is a page it can hand over)."""
        if bool(shown) == self.open_button.isHidden():
            self.open_button.setVisible(bool(shown))
            self._place_buttons()
            self.update()

    def open_shown(self) -> bool:
        return not self.open_button.isHidden()

    def ring(self, rect: QRectF | None) -> None:
        """The ring over an element of the page area (its own px), or none."""
        self._ring.set_ring(rect)

    def ring_rect(self) -> QRectF | None:
        return self._ring.ring_rect()

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
        """Where the strip's words end: before the Hide (and the Open when it shows)."""
        right = self.width() - PAD // 2 - self.hide_button.sizeHint().width() - TAG_GAP // 2
        if not self.open_button.isHidden():
            right -= self.open_button.sizeHint().width() - 2 * holo.HoloButton.MARGIN + holo.BUTTON_GAP
        return right

    def _place_buttons(self) -> None:
        hint = self.hide_button.sizeHint()
        self.hide_button.setGeometry(self.width() - PAD // 2 - hint.width(), (STRIP_HEIGHT - 1 - hint.height()) // 2,
                                     hint.width(), hint.height())
        wide = self.open_button.sizeHint()
        self.open_button.setGeometry(self.hide_button.x() - wide.width() + 2 * holo.HoloButton.MARGIN - holo.BUTTON_GAP,
                                     (STRIP_HEIGHT - 1 - wide.height()) // 2, wide.width(), wide.height())

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
        area = target.availableGeometry()
        if others:          # the new look: the second screen's broker follows it there and keeps the display's strip
            from .. import second_screen

            room = second_screen.broker()
            room.watch(self, second_screen.WATCH)
            area = room.room(target.geometry(), area, second_screen.WATCH)
        self.setGeometry(watch_rect(area, avoid if target is here else None, bool(others)))

    # ---- Qt ------------------------------------------------------------------------------------------------

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.page_area.setGeometry(PAGE_INSET, STRIP_HEIGHT, max(1, self.width() - 2 * PAGE_INSET),
                                   max(1, self.height() - STRIP_HEIGHT - PAGE_INSET))
        if self._view is not None:
            self._view.setGeometry(self.page_area.rect())
        self._ring.setGeometry(self.page_area.rect())
        self._cover.setGeometry(self.page_area.rect())
        self._notice.setGeometry(self.page_area.rect())
        self._place_buttons()

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
        if len(self._tabs) > 1:
            self._paint_chips(painter, x, right)
            return
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

    def _paint_chips(self, painter: QPainter, x: float, right: float) -> None:
        """A chip per tab with its host (cut with an ellipsis), the one shown lit. Words only: the owner's mouse stays
        off the window, so a chip is no control."""
        count = len(self._tabs)
        each = min(float(CHIP_MAX), max(0.0, right - x - CHIP_GAP * (count - 1)) / count)
        if each < 2 * CHIP_PAD + 12:
            return
        metrics = QFontMetricsF(self._chip_font)
        top = (STRIP_HEIGHT - 1 - CHIP_HEIGHT) / 2
        painter.setFont(self._chip_font)
        for host, shown in self._tabs:
            words = host or NEW_TAB_CHIP
            wide = min(each, metrics.horizontalAdvance(words) + 2 * CHIP_PAD + 2)
            box = QRectF(x, top, wide, CHIP_HEIGHT)
            painter.setPen(QPen(holo.alpha(holo.HOLO, 0.7) if shown else holo.LINE, 1.0))
            if shown:
                painter.setBrush(holo.alpha(holo.HOLO, 0.10))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), CHIP_HEIGHT / 2, CHIP_HEIGHT / 2)
            painter.setPen(QColor(holo.INK_1 if shown else holo.INK_3))
            painter.drawText(box.adjusted(CHIP_PAD, 0, -CHIP_PAD, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             metrics.elidedText(words, Qt.TextElideMode.ElideRight, max(0.0, wide - 2 * CHIP_PAD)))
            x += wide + CHIP_GAP


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
    signed_in: bool = False           # open_page's: it may open with Jarvis's sign-in profile on a listed site

    def __repr__(self) -> str:        # never the address
        return f"_Request(file={self.folder is not None}, delivered={self.delivered}, signed_in={self.signed_in})"


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


class _Act:
    """One drive script in a tab and the wait after it (GUI thread)."""

    __slots__ = ("tab", "shot", "done", "settle_s", "deadline", "starts", "good", "url", "ring", "answered", "answer",
                 "answered_at", "arrived_at", "finished")

    def __init__(self, tab: int, shot: Any, done: Callable[[Any], None], settle_s: float, deadline: float,
                 loads: Sequence[int], url: str, ring: tuple[str, int] | None) -> None:
        self.tab = tab
        self.shot = shot
        self.done = done
        self.settle_s = settle_s
        self.deadline = deadline
        self.starts, self.good = int(loads[0]), int(loads[2])
        self.url = url
        self.ring = ring
        self.answered = False
        self.answer: Any = None
        self.answered_at = 0.0
        self.arrived_at: float | None = None
        self.finished = False

    def __repr__(self) -> str:        # never its script, answer or address
        return f"_Act(tab={self.tab}, answered={self.answered}, finished={self.finished})"


def _ring_target(source: str) -> tuple[str, int] | None:
    """(the list's gen, the element's number) a drive script acts on, from its op mark and ARGS line (agent.drive's
    scripts); None for one that acts on no element."""
    found = _DRIVE_MARK.search(source or "")
    if found is None or found.group(1) not in _RING_OPS:
        return None
    try:
        args = json.loads(found.group(2))
    except ValueError:
        return None
    n, gen = args.get("n") if isinstance(args, dict) else None, args.get("gen") if isinstance(args, dict) else None
    if isinstance(n, int) and not isinstance(n, bool) and n > 0 and isinstance(gen, str):
        return gen, n
    return None


class AgentBrowser(PageCamera):
    """See the module docs. GUI thread only. ``done`` callables get their answer once, never inside a Qt signal of a
    page that is going away; a request that is replaced or stopped gets R_CANCELLED."""

    hideAsked = Signal()               # the strip's Hide was clicked
    windowPlaced = Signal()            # it came or moved

    def __init__(self, parent: QObject | None = None, *, html_for: Callable[[str], Any] | None = None,
                 avoid: Callable[[], QRect | None] | None = None, native: Any = None, hide_from_capture: bool = False,
                 screens: Callable[[], Sequence[QScreen]] | None = None,
                 decide: Callable[..., str] = snapshots.request_problem, settle_ms: int = SETTLE_MS,
                 linger_s: float = LINGER_S, drive: bool = False,
                 open_url: Callable[[str], None] | None = None, signins: Any = None) -> None:
        super().__init__(parent, decide=decide, settle_ms=settle_ms)
        self._signins = signins            # the sign-in profile's lender (site_signins_ui.SignInProfile; None: none)
        self._lent_shot: Any = None        # the one shot that holds it now
        self._pressing_shot: Any = None    # the shot the owner's Send presses now (a signed-in page may send then)
        self._drive = bool(drive)          # this task may fill in pages: its web pages get DRIVE_SANDBOX
        self._open_url = open_url if open_url is not None else owner_browser   # "Open in my browser" (tests fake it)
        self._addresses: dict[int, str] = {}   # each tab's public page as it last arrived (for "Open in my browser")
        self._opened_at = -1e9
        self._tab = 1                      # the tab shown (0: none, until the session opens or shows one)
        self._tabs: list[int] = [1]        # the tabs, in the order they opened (the session's ids)
        self._parked: dict[int, Any] = {}  # the shots of the tabs not shown
        self._dead: set[int] = set()       # tabs whose page crashed
        self._hosts: dict[int, str] = {}   # each tab's host, for its chip
        self._loads: dict[int, list[int]] = {}         # a shot's loads: [started, finished, finished well]
        self._shot_request: dict[int, _Request] = {}   # what each shot was asked for (a tab's, shown or not)
        self._acts: list[_Act] = []
        self._act_timer = self._timer(self._on_act_poll)
        self._sealed_drive = False         # a page of this window holds what Jarvis typed: sealed (seal())
        self._send_hosts: frozenset[str] = frozenset()   # while the owner's Send presses: the only hosts it may reach
        self._no_open: set[int] = set()    # tabs an approved press took to a page: never "Open in my browser" (its
        #                                    address may hold what was sent) until Jarvis loads another page there
        self._seal_gen = 0
        self._sealed_doomed: list[Any] = []   # pages and profiles that went while sealed, until really deleted
        self._unsealing: list[tuple[int, Callable[[Any], None] | None]] = []
        self._unseal_tries = 0
        self._unseal_timer = self._timer(self._check_unseal)
        self._frozen = False               # "opens big": the run is over, nothing of the page may load meanwhile
        self._big_hold = False
        self._big_timer = self._timer(self._on_big)
        self._ring_timer = self._timer(self._ring_off)
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
        self._turned_away = False          # the load in hand ended in TURNED_AWAY_ERROR
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

    def drive_sealed(self) -> bool:
        """A page of this window holds what Jarvis typed: the gate is sealed for it (seal())."""
        return self._sealed_drive

    def tabs(self) -> tuple[int, ...]:
        return tuple(self._tabs)

    def tab_shown(self) -> int:
        return self._tab

    # ---- tabs, acts and the seal (agent.drive's DriveBrowser, through the client; GUI thread) -------------

    def new_tab(self, tab: int, done: Callable[[Any], None]) -> None:
        """A fresh, empty tab with the session's id ``tab``, now the one shown (its page comes with the next load, a
        profile of its own as every page). False: that id is in use, TAB_LIMIT tabs are open, or Jarvis closes."""
        if self._shut or not isinstance(tab, int) or tab <= 0 or tab in self._tabs or len(self._tabs) >= TAB_LIMIT:
            logger.info("Agent browser: no new tab (%d open)", len(self._tabs))
            _answer(done, False)
            return
        self._park()
        self._tabs.append(tab)
        self._tab = tab
        self._empty_view()
        self._show_tabs()
        logger.info("Agent browser: a new tab (%d open)", len(self._tabs))
        _answer(done, True)

    def switch_tab(self, tab: int, done: Callable[[Any], None]) -> None:
        """That tab is the one shown now (its page as it was). False: there is no such tab."""
        if tab not in self._tabs:
            _answer(done, False)
            return
        if tab != self._tab:
            self._park()
            self._show(tab)
        _answer(done, True)

    def close_tab(self, tab: int, unseal: bool, done: Callable[[Any], None]) -> None:
        """That tab's page and profile go. With ``unseal``, the seal lifts once they are really deleted (the answer
        comes then: False when they never went, and the window stays sealed). The last tab left is shown."""
        gen = self._seal_gen
        if tab in self._tabs:
            if tab == self._tab:
                self._cancel_request()
                self.abort()
            else:
                shot = self._parked.pop(tab, None)
                if shot is not None:
                    self._end_parked(shot)
            self._tabs.remove(tab)
            self._hosts.pop(tab, None)
            self._addresses.pop(tab, None)
            self._no_open.discard(tab)
            self._dead.discard(tab)
            if tab == self._tab:
                self._tab = 0
                if self._tabs:
                    self._show(self._tabs[-1])
                else:
                    self._empty_view(words="")
            self._show_tabs()
            logger.info("Agent browser: a tab closed (%d open)", len(self._tabs))
        if unseal:
            self._unseal_when_gone(done, gen)
        else:
            _answer(done, True)

    def seal(self, cut: bool, done: Callable[[Any], None]) -> None:
        """The gate refuses every new connection (its own reason), with ``cut`` every one open now ends, and every
        request and navigation of this window's pages is refused here; no web page loads. True once that holds."""
        if self._shut:
            _answer(done, False)
            return
        self._seal_gen += 1
        self._sealed_drive = True
        snapshot_engine.seal_drive(True)
        ended = snapshot_engine.cut_gate() if cut else 0
        logger.info("Agent browser: the window is sealed (%d connection(s) cut)", ended)
        self._sync_open()
        _answer(done, snapshot_engine.gate_sealed())

    def act(self, tab: int, source: str, settle_s: float, timeout_s: float, done: Callable[[Any], None]) -> None:
        """``source`` in that tab's isolated world; then a navigation it started is waited for (until it arrived), or
        ``settle_s``; an ActResult within ``timeout_s``. NO_PAGE: the tab has no page; ok False: there is no such tab,
        its page crashed, or it went (a page that never answers in time goes, as script()'s does)."""
        shot = self._shot if tab == self._tab else self._parked.get(tab)
        if tab not in self._tabs or tab in self._dead:
            _answer(done, ActResult(ok=False))
            return
        request = self._request if shot is self._shot else None
        if shot is None or shot.done or shot.page is None or not shot.opened \
                or (request is not None and not request.delivered):
            _answer(done, ActResult(NO_PAGE))
            return
        loads = self._loads.setdefault(shot.serial, [0, 0, 0])
        act = _Act(tab, shot, done, max(0.0, float(settle_s)), time.monotonic() + max(0.1, float(timeout_s)), loads,
                   snapshot_engine._encoded(shot.page.url()), _ring_target(source))
        self._acts.append(act)
        try:
            shot.page.runJavaScript(source, snapshot_engine._classes().app_world,
                                    lambda value, act=act: self._on_act_answer(act, value))
        except Exception as exc:  # noqa: BLE001 - a plain answer, never a waiter left hanging
            logger.debug("Agent browser: a drive script did not run (%s)", type(exc).__name__)
            self._finish_act(act, ActResult(ok=False))
            return
        if not self._act_timer.isActive():
            self._act_timer.start(ACT_POLL_MS)

    def press_approved(self, tab: int, source: str, hosts: Any, settle_s: float, timeout_s: float,
                       done: Callable[[Any], None]) -> None:
        """Only after the owner's click on the Send card (agent.drive): the seal lifts for ``hosts`` alone (the gate and
        this window's own check refuse every other host meanwhile), then ``source`` (the press) runs in that tab as
        act() runs it. A new page arrived: the seal lifts whole (the page that held the values is gone). None did: the
        window is sealed again as before, every connection cut."""
        self._seal_gen += 1
        self._no_open.add(tab)
        self._pressing_shot = self._shot if tab == self._tab else self._parked.get(tab)
        sealed = self._sealed_drive
        if sealed:
            self._send_hosts = frozenset(str(host).casefold() for host in hosts or () if host)
            snapshot_engine.seal_drive_only(self._send_hosts)
        logger.info("Agent browser: the owner's Send; the window is unsealed for %d host(s)", len(self._send_hosts))

        def pressed(result: Any) -> None:
            self._send_hosts = frozenset()
            self._pressing_shot = None
            if sealed:
                snapshot_engine.seal_drive_only(())
                if isinstance(result, ActResult) and result.ok and result.navigated:
                    self._lift_seal()
                elif self._sealed_drive:
                    ended = snapshot_engine.cut_gate()
                    logger.info("Agent browser: no new page after the Send; sealed again (%d connection(s) cut)", ended)
            self._sync_open()
            _answer(done, result)

        self.act(tab, source, settle_s, timeout_s, pressed)

    # ---- the tabs' pages ------------------------------------------------------------------------------------

    def _park(self) -> None:
        """The tab shown goes behind: its page stays as it is, off the view. A page still on its way, and a task's own
        file (a page the agent wrote: it is shown, never kept), go instead."""
        shot = self._shot
        request = self._request
        if self._waiting is not None or (request is not None and not request.delivered) or (
                shot is not None and self._request_of(shot) is not None and self._request_of(shot).folder is not None):
            self._cancel_request()
            self.abort()
            shot = None
        self._request = None
        self._ring_off()
        if shot is None:
            return
        for timer in (self._timeout, self._settle, self._fail_check):
            timer.stop()
        page, view = shot.page, self._view
        if page is not None:
            # The view deletes a page that is its own child when it lets go of it (Qt): this one is the browser's now.
            _quietly(lambda: page.setParent(self))
            if shot.on_view and view is not None:
                _quietly(lambda: view.setPage(None))
        shot.on_view = False
        self._parked[self._tab] = shot
        self._shot = None

    def _show(self, tab: int) -> None:
        """``tab`` (parked, or empty) is the one on the view now."""
        self._tab = tab
        shot = self._parked.pop(tab, None)
        view, window = self._view, self._window
        if shot is None or shot.done or shot.page is None or view is None:
            self._empty_view()
            self._show_tabs()
            return
        self._shot = shot
        try:
            view.setPage(shot.page)
            shot.on_view = True
            if not view.isVisible():
                view.show()
        except Exception as exc:  # noqa: BLE001 - the tab is there; its page may be shown again
            logger.debug("Agent browser: a tab was not shown (%s)", type(exc).__name__)
        if window is not None:
            window.set_notice()
            window.cover(None)
        self._no_focus_inside()
        self._show_tabs()

    def _empty_view(self, words: str = EMPTY_TAB_WORDS) -> None:
        view, window = self._view, self._window
        if view is not None:
            _quietly(view.hide)
        if window is not None:
            window.cover(None)
            window.ring(None)
            window.set_notice(words)

    def _show_tabs(self) -> None:
        window = self._window
        if window is not None:
            window.set_tabs([(self._hosts.get(tab, ""), tab == self._tab) for tab in self._tabs])
        self._sync_open()

    # ---- "Open in my browser" (a filling-in window's strip) -------------------------------------------------

    def open_address(self) -> str:
        """The address "Open in my browser" would hand over now, "" when none: only in a filling-in window, a public
        page of the tab shown (never a task's file, never an empty tab), never while the window is sealed (a page that
        holds what Jarvis typed may have put it into its own address)."""
        if not self._drive or self._shut or self._sealed_drive or self._tab in self._no_open:
            return ""
        address = self._addresses.get(self._tab, "")
        return address if address and not web_link_problem(address) else ""

    def _sync_open(self) -> None:
        window = self._window
        if window is not None:
            window.set_open_shown(bool(self.open_address()))

    def _on_open_clicked(self) -> None:
        """The owner's click: the page's address, and only that, in their own browser (never twice within a moment)."""
        address = self.open_address()
        now = time.monotonic()
        if not address or now - self._opened_at < OPEN_GUARD_S:
            return
        url = QUrl(address, QUrl.ParsingMode.StrictMode)
        host = url.host(QUrl.ComponentFormattingOption.FullyEncoded).rstrip(".").casefold()
        if not url.isValid() or url.scheme() != "https" or host != link_host(address):
            logger.info("Agent browser: not handed to the owner's browser (not a plain https address)")
            return
        self._opened_at = now
        logger.info("Agent browser: the page's address goes to the owner's browser")
        try:
            self._open_url(address)
        except Exception as exc:  # noqa: BLE001 - only a nicety
            logger.warning("Agent browser: the owner's browser could not be asked (%s)", type(exc).__name__)

    def _request_of(self, shot: Any) -> _Request | None:
        found = self._shot_request.get(shot.serial) if shot is not None else None
        return found if found is not None else (self._request if shot is self._shot else None)

    def _tab_of(self, shot: Any) -> int:
        if shot is self._shot:
            return self._tab
        return next((tab for tab, parked in self._parked.items() if parked is shot), 0)

    def _end_parked(self, shot: Any) -> None:
        """A tab not shown: its page and profile go (no answer is owed: its load was answered long ago)."""
        if not shot.done:
            shot.done = True
            try:
                self._teardown(shot)
            except Exception as exc:  # noqa: BLE001 - it ends all the same
                logger.debug("Agent browser: a tab's teardown failed (%s)", type(exc).__name__)

    def _end_tabs(self) -> None:
        """Every tab not shown goes; the window is one tab again."""
        for shot in list(self._parked.values()):
            self._end_parked(shot)
        self._parked.clear()
        shown = "" if self._tab in self._no_open else self._addresses.get(self._tab, "")
        self._no_open.clear()
        self._tabs, self._tab = [1], 1
        self._addresses = {1: shown} if shown else {}    # the still shown is that page's: its Open stays
        self._dead.clear()
        self._hosts.clear()
        self._ring_off()
        if self._window is not None:
            self._window.set_tabs(())

    # ---- the seal's end ---------------------------------------------------------------------------------------

    def _unseal_when_gone(self, done: Callable[[Any], None] | None, gen: int) -> None:
        """The seal lifts once every page and profile that went while sealed is really deleted (their renderer can ask
        for nothing then) and no newer seal came; ``done`` hears whether they went."""
        self._unsealing.append((gen, done))
        self._unseal_tries = 0
        self._check_unseal()

    def _check_unseal(self) -> None:
        self._prune()
        self._sealed_doomed = [item for item in self._sealed_doomed if snapshot_engine._alive(item)]
        if self._sealed_doomed and self._unseal_tries < CLEAR_TRIES:
            self._unseal_tries += 1
            self._unseal_timer.start(CLEAR_MS)
            return
        waiting, self._unsealing = self._unsealing, []
        gone = not self._sealed_doomed
        if not gone:
            logger.warning("Agent browser: a filled-in page did not go in time; the window stays sealed")
        elif self._sealed_drive and any(gen == self._seal_gen for gen, _done in waiting):
            self._lift_seal()
        for _gen, done in waiting:
            if done is not None:
                _answer(done, gone)

    def _lift_seal(self) -> None:
        self._sealed_drive = False
        snapshot_engine.seal_drive(False)
        logger.info("Agent browser: the window is unsealed")
        self._sync_open()

    def _pages_gone(self) -> None:
        """Every page went (the run's end, a Stop, Jarvis closing): a seal lifts once they are really deleted."""
        if self._sealed_drive:
            self._unseal_when_gone(None, self._seal_gen)

    # ---- an act's wait ----------------------------------------------------------------------------------------

    def _on_act_answer(self, act: _Act, value: Any) -> None:
        if act.finished or act.answered:
            return
        act.answered, act.answer, act.answered_at = True, value, time.monotonic()
        self._ring_for(act)

    def _on_act_poll(self) -> None:
        now = time.monotonic()
        for act in list(self._acts):
            if act.finished:
                continue
            shot = act.shot
            if shot.done or shot.page is None:
                self._finish_act(act, ActResult(act.answer, ok=False))
                continue
            started, finished, good = self._loads.get(shot.serial, [0, 0, 0])
            if not act.answered:
                if now >= act.deadline:
                    self._hung(act)
                    self._finish_act(act, ActResult(ok=False))
                continue
            if started > act.starts:
                if finished >= started:
                    act.arrived_at = act.arrived_at if act.arrived_at is not None else now
                    if now - act.arrived_at >= NAV_SETTLE_S or now >= act.deadline:
                        url = snapshot_engine._encoded(shot.page.url())
                        self._arrived(act, good > act.good or url != act.url)
                elif now >= act.deadline:
                    act.arrived_at = None
                    self._arrived(act, True)
                else:
                    act.arrived_at = None
            elif now - act.answered_at >= act.settle_s or now >= act.deadline:
                self._finish_act(act, ActResult(act.answer, False, self._url_of(shot, False)))
        self._acts = [act for act in self._acts if not act.finished]
        if self._acts:
            self._act_timer.start(ACT_POLL_MS)

    def _arrived(self, act: _Act, navigated: bool) -> None:
        shot = act.shot
        final = self._url_of(shot, navigated)
        if navigated:
            host = link_host(final) if final.casefold().startswith(("https://", "http://")) else ""
            self._hosts[act.tab] = host
            self._addresses[act.tab] = final if host else ""
            if act.tab == self._tab and self._window is not None:
                self._window.update_host(host)
                self._window.ring(None)
            self._show_tabs()
        self._finish_act(act, ActResult(act.answer, navigated, final))

    def _url_of(self, shot: Any, navigated: bool) -> str:
        if shot is None or shot.page is None:
            return ""
        if navigated or not shot.local:
            return snapshot_engine._encoded(shot.page.url()) or shot.url
        return shot.final_hint or shot.url

    def _finish_act(self, act: _Act, result: ActResult) -> None:
        if not act.finished:
            act.finished = True
            _answer(act.done, result)

    def _hung(self, act: _Act) -> None:
        """A page that never answered a drive script in time goes (a hung renderer spins no longer)."""
        logger.info("Agent browser: a page did not answer in time; it was closed")
        if act.shot is self._shot:
            self._cancel_request()
            self.abort()
        elif self._parked.get(act.tab) is act.shot:
            self._parked.pop(act.tab, None)
            self._end_parked(act.shot)

    def _cancel_acts(self) -> None:
        acts, self._acts = self._acts, []
        self._act_timer.stop()
        for act in acts:
            self._finish_act(act, ActResult(ok=False))

    # ---- the ring --------------------------------------------------------------------------------------------

    def _ring_for(self, act: _Act) -> None:
        """Where the element the script acted on is now, read in Jarvis's world: the ring goes over it."""
        shot, window = act.shot, self._window
        if act.ring is None or act.tab != self._tab or shot is not self._shot or window is None or shot.page is None:
            return
        gen, n = act.ring
        source = _RING_SCRIPT.replace("GEN", json.dumps(gen)).replace("N - 1", f"{int(n)} - 1")
        try:
            shot.page.runJavaScript(source, snapshot_engine._classes().app_world,
                                    lambda value, shot=shot: self._on_ring(shot, value))
        except Exception as exc:  # noqa: BLE001 - only a nicety
            logger.debug("Agent browser: no ring (%s)", type(exc).__name__)

    def _on_ring(self, shot: Any, value: Any) -> None:
        window = self._window
        if window is None or shot is not self._shot or shot.done or shot.page is None:
            return
        try:
            value = json.loads(value) if isinstance(value, str) else None
        except ValueError:
            value = None
        if not (isinstance(value, list) and len(value) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return
        try:
            zoom = float(shot.page.zoomFactor()) or 1.0
        except Exception:  # noqa: BLE001
            zoom = 1.0
        left, top, wide, tall = (float(v) * zoom for v in value)
        window.ring(QRectF(left, top, wide, tall))
        self._ring_timer.start(RING_MS)

    def _ring_off(self) -> None:
        self._ring_timer.stop()
        if self._window is not None:
            self._window.ring(None)

    # ---- "opens big" -----------------------------------------------------------------------------------------

    def _grow(self, window: WatchWindow) -> None:
        """~80% of the screen the window is on, in its middle (never smaller than it is); never activated."""
        screen = window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        window.setGeometry(big_rect(screen.availableGeometry(), window.geometry()))
        self.windowPlaced.emit()

    def _on_big(self) -> None:
        self.end_run(True, self._big_hold)

    # ---- asked for by the client ------------------------------------------------------------------------

    def load(self, url: str, timeout_s: float, done: Callable[[Any], None]) -> None:
        """A web page (on a test platform only local HTML: the ``html_for`` seam)."""
        html, final = self._local_page(url)
        self._start(_Request(url, html, final, None, "", done, link_host(url), _milliseconds(timeout_s)))

    def load_signed_in(self, url: str, timeout_s: float, done: Callable[[Any], None]) -> None:
        """open_page's web page: on a site the owner listed it opens with Jarvis's sign-in profile when that is free
        (its ShotResult says ``signed_in``), else off the record as any page."""
        html, final = self._local_page(url)
        self._start(_Request(url, html, final, None, "", done, link_host(url), _milliseconds(timeout_s),
                             signed_in=True))

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

    def end_run(self, linger: bool, hold: bool = False, big: bool = False) -> None:
        """The run ended: no page after this. ``linger``: the window keeps a still of it for a while, then goes; with
        ``hold`` until close_all (it stays while the result card stays). ``big``: it "opens big" first (BIG_SHARE of its
        screen, never activated), and the still is taken once the page drew itself at that size; meanwhile nothing of
        the page loads or goes."""
        self._linger.stop()
        self._big_timer.stop()
        window = self._window
        self._cancel_request()
        self._cancel_acts()
        if big and linger and window is not None and window.isVisible() and self.has_page() and not self._shut:
            self._frozen = True
            self._big_hold = bool(hold)
            self._ring_off()
            self._grow(window)
            self._big_timer.start(BIG_SETTLE_MS)
            return
        still = self._still() if linger else None
        self.abort()
        self._end_tabs()
        self._frozen = False
        self._pages_gone()
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
        self._big_timer.stop()
        self._cancel_request()
        self._cancel_acts()
        self._end_tabs()
        window, self._window = self._window, None
        self.close_view()
        self._frozen = False
        self._pages_gone()
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
        if request.folder is None and (self._sealed_drive or self._frozen):
            # A page holds what Jarvis typed (agent.drive refuses loads then too): no web page opens, the page stays.
            self._count("held")
            logger.info("Agent browser: no page while the window is sealed")
            _answer(request.done, ShotResult(False, snapshots.R_LOAD_FAILED))
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
        self._turned_away = False
        self._dead.discard(self._tab)
        self._hosts[self._tab] = request.label
        self._addresses.pop(self._tab, None)
        self._no_open.discard(self._tab)
        if self._window is not None:
            self._window.set_notice()
            self._window.set_page(request.label)
            self._show_tabs()
        if request.html is None:
            self.shoot(request.url)
        else:
            self.shoot(request.url, request.html, final_url=request.final)
        if self._shot is not None:
            self._shot_request[self._shot.serial] = request

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
        window = self._window if self._window is not None else self._make_window()
        view = classes.QWebEngineView(window.page_area)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setEnabled(False)             # also its inner widgets, whenever they come: no focus can land there
        view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAcceptDrops(False)
        view.setAccessibleName("")
        window.set_view(view)
        return view

    def _make_window(self) -> WatchWindow:
        """The window, wired: its Hide, its Open (a filling-in window's) and the wheel."""
        window = self._window = WatchWindow(native=self._native, hide_from_capture=self._hide_from_capture)
        window.hideClicked.connect(self._on_hide_clicked)
        window.openClicked.connect(self._on_open_clicked)
        window.wheeled.connect(self._on_wheel)
        window.set_tag(self._tag)
        if self._request is not None:
            window.set_page(self._request.label)
        self._show_tabs()
        return window

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
        self._loads[shot.serial] = [0, 0, 0]
        page.loadStarted.connect(lambda: self._count_load(shot, 0))
        page.loadFinished.connect(lambda ok: self._count_load(shot, 1, ok))

    def _count_load(self, shot: Any, which: int, ok: bool = False) -> None:
        """A shot's main-frame loads once its page was asked for (an act waits for the one it started)."""
        loads = self._loads.get(shot.serial)
        if loads is None or not shot.opened or shot.done:
            return
        loads[which] += 1
        if which == 1 and ok:
            loads[2] += 1

    def _sandbox(self, shot: Any) -> str:
        """A web page of a task that may fill in pages: DRIVE_SANDBOX (forms may be sent; nothing typed leaves before
        the owner's Send). Anything else, a task's own file included: SANDBOX."""
        request = self._request_of(shot)
        if self._drive and (request is None or request.folder is None):
            return snapshot_engine.DRIVE_SANDBOX
        return snapshot_engine.SANDBOX

    def _held_out(self, url: Any) -> bool:
        """While sealed (or the run's last page opens big) nothing of the window's pages goes: only about:, data: and
        blob:, which reach no network."""
        if not (self._sealed_drive or self._frozen):
            return False
        try:
            scheme = url.scheme().casefold()
            if scheme in _LOCAL_SCHEMES:
                return False
            # The owner's Send: the card's hosts alone (the gate passes only https to them in any case).
            return not (self._send_hosts and not self._frozen and scheme in ("https", "http")
                        and url.host().casefold().rstrip(".") in self._send_hosts)
        except Exception:  # noqa: BLE001 - unsure: refused
            return True

    def _judge(self, shot: Any, info: Any) -> bool:
        if not shot.done and shot.opened and self._held_out(info.requestUrl()):
            shot.blocked += 1
            self._count("held")
            return True
        if shot.lent and not shot.done and shot.opened:
            why = self._signed_in_request(shot, info)
            if why:
                shot.blocked += 1
                self._count(f"signed_in_{why}")
                return True
        return super()._judge(shot, info)

    def _signed_in_request(self, shot: Any, info: Any) -> str:
        """Read only, on a page signed in as the owner (site_signins): a main or inline frame asked for with anything
        but GET or HEAD is refused unless the owner's click on its Send card presses it now, and a main frame never
        leaves the listed sites. Unsure: refused."""
        try:
            classes = snapshot_engine._classes()
            kind = info.resourceType()
            main = kind in classes.main_types
            frame = main or (classes.sub_frame is not None and kind == classes.sub_frame)
            why = site_signins.request_refused(bytes(info.requestMethod().data()), frame,
                                               self._pressing_shot is shot)
            if not why and main and not site_signins.host_listed(info.requestUrl().host(), self._signin_hosts()):
                why = site_signins.R_OFF_LIST
            return why
        except Exception:  # noqa: BLE001 - unsure: refused
            return site_signins.R_WRITE

    def _signin_hosts(self) -> tuple[str, ...]:
        try:
            return tuple(self._signins.hosts()) if self._signins is not None else ()
        except Exception:  # noqa: BLE001 - unknown: no site
            return ()

    def _on_navigation(self, shot: Any, request: Any) -> None:
        try:
            if not shot.done and shot.opened and self._held_out(request.url()):
                shot.blocked += 1
                self._count("held")
                request.reject()
                return
            if shot.lent and not shot.done and shot.opened:
                why = site_signins.navigation_refused(request.navigationType(), bool(request.isMainFrame()),
                                                      request.url().host(), self._signin_hosts(),
                                                      self._pressing_shot is shot)
                if why:
                    shot.blocked += 1
                    self._count(f"signed_in_{why}")
                    request.reject()
                    return
        except Exception:  # noqa: BLE001 - unsure: refuse
            _quietly(request.reject)
            return
        super()._on_navigation(shot, request)

    def _on_crash(self, shot: Any) -> None:
        tab = self._tab_of(shot)
        if tab:
            self._dead.add(tab)
        if shot is not self._shot:
            if tab and self._parked.get(tab) is shot:
                self._parked.pop(tab, None)
                self._end_parked(shot)
            return
        super()._on_crash(shot)

    def _teardown(self, shot: Any) -> None:
        """As the camera's; what goes while sealed is waited for before the seal lifts. A lent sign-in profile is not
        deleted: once its page is on its way out it goes back to its lender, which refuses every request of it until
        it is lent again."""
        lent = shot.profile if shot.lent else None
        interceptor = shot.interceptor if shot.lent else None
        items = [item for item in (shot.page, None if shot.lent else shot.profile) if item is not None]
        self._loads.pop(shot.serial, None)
        self._shot_request.pop(shot.serial, None)
        super()._teardown(shot)
        if self._sealed_drive:
            self._sealed_doomed.extend(items)
        if shot.lent:
            if self._lent_shot is shot:
                self._lent_shot = None
            if self._pressing_shot is shot:
                self._pressing_shot = None
            if lent is not None and self._signins is not None:
                _quietly(lambda: self._signins.give_back(self, lent))
            if interceptor is not None:
                _quietly(interceptor.deleteLater)   # the lender's own refusal is on the profile first

    def _on_loading(self, shot: Any, info: Any) -> None:
        """The main frame's load: its HTTP status when it answered with an error. It stands through what the engine loads
        in that address's place (an empty answer's blank page, measured: it may even "succeed"), until another address
        loads. Nothing is tried again here."""
        if shot is not self._shot or shot.done or not shot.opened:
            return
        try:
            status, domain, address = info.status().name, info.errorDomain().name, info.url().toString()
            if status == "LoadStartedStatus":
                self._turned_away = False
            elif status == "LoadFailedStatus" and TURNED_AWAY_ERROR in info.errorString():
                self._turned_away = True
            if domain == "HttpStatusCodeDomain":
                self._http_status, self._status_address = int(info.errorCode()), address
            elif status in ("LoadStartedStatus", "LoadSucceededStatus") and address != self._status_address                     and not address.startswith("chrome-error:"):
                self._http_status = 0
        except Exception as exc:  # noqa: BLE001 - only how a failure is told apart
            logger.debug("Agent browser: a load's state could not be read (%s)", type(exc).__name__)

    def _new_profile(self, shot: Any, classes: Any) -> Any:
        """open_page's page on a site the owner listed: Jarvis's sign-in profile, lent while this shot lives (one shot
        at a time); every other page, and that one when the profile is not free, a fresh off-the-record one."""
        request = self._request
        lender = self._signins
        if request is not None and request.signed_in and request.folder is None and request.html is None \
                and lender is not None:
            if self._lent_shot is not None and not self._lent_shot.done:
                self._count("signed_in_busy")          # another tab holds it: this one opens off the record
            elif site_signins.listed_host(request.url, self._signin_hosts()):
                try:
                    profile = lender.lend(self)
                except Exception as exc:  # noqa: BLE001 - off the record instead
                    logger.debug("Agent browser: the sign-in profile was not lent (%s)", type(exc).__name__)
                    profile = None
                if profile is not None:
                    shot.lent = True
                    self._lent_shot = shot
                    logger.info("Agent browser: a page opens signed in")
                    return profile
                self._count("signed_in_unlent")
        return super()._new_profile(shot, classes)

    def _configure(self, profile: Any, classes: Any, shot: Any) -> None:
        if shot.lent:
            # The lender set the profile up once (site_signins_ui); here only this shot's judge goes on it.
            shot.interceptor = classes.Interceptor(lambda info: self._judge(shot, info), profile)
            profile.setUrlRequestInterceptor(shot.interceptor)
            return
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
        request = self._request_of(shot)       # that shot's own, also for a tab not shown
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
        if request.folder is None:
            self._hosts[self._tab] = link_host(final_url)
            self._addresses[self._tab] = final_url
        else:
            self._addresses.pop(self._tab, None)
        if window is not None:
            window.cover(None)
            if request.folder is None:
                window.update_host(link_host(final_url))
            self._show_tabs()
        took = int((time.monotonic() - shot.started) * 1000)
        logger.info("Agent browser: a page is up in %.1f s (%d request(s) blocked%s)", took / 1000, shot.blocked,
                    ", still loading" if still_loading else "")
        _answer(request.done, ShotResult(True, final_url=final_url, took_ms=took, still_loading=still_loading,
                                         blocked=shot.blocked, signed_in=bool(shot.lent)))

    def _end(self, shot: Any, reason: str, **data: Any) -> None:
        if shot.done:
            return
        status = self._http_status if reason == snapshots.R_LOAD_FAILED and self._http_status in REFUSAL_STATUSES else 0
        turned_away = reason == snapshots.R_LOAD_FAILED and not status and self._turned_away
        reason = snapshots.R_SITE_REFUSED if status or turned_away else reason
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
            words = TURNED_AWAY_WORDS if self._turned_away else REFUSED_WORDS
            window.set_notice(words.format(host=request.label), REFUSED_NOTE)
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

    def load_signed_in(self, url: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> ShotResult:
        """open_page's load: signed in on a site the owner listed when the sign-in profile is free (``signed_in``)."""
        return self._loaded(self._ask("load_signed_in", (url, timeout_s), timeout_s + LOAD_MARGIN_S, cancelled))

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

    # agent.drive's DriveBrowser: tabs, acts, the seal and the owner's approved press (any thread; each waits)

    def new_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        return self._ask("new_tab", (int(tab),), timeout_s, cancelled) is True

    def switch_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        return self._ask("switch_tab", (int(tab),), timeout_s, cancelled) is True

    def close_tab(self, tab: int, *, unseal: bool, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        return self._ask("close_tab", (int(tab), bool(unseal)), timeout_s, cancelled) is True

    def seal(self, cut: bool, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        return self._ask("seal", (bool(cut),), timeout_s, cancelled) is True

    def act(self, tab: int, source: str, *, settle_s: float, timeout_s: float,
            cancelled: Callable[[], bool]) -> ActResult:
        value = self._ask("act", (int(tab), source, float(settle_s), float(timeout_s)), timeout_s + ACT_MARGIN_S,
                          cancelled)
        return value if isinstance(value, ActResult) else ActResult(ok=False)

    def press_approved(self, tab: int, source: str, hosts: Any = (), *, settle_s: float, timeout_s: float,
                       cancelled: Callable[[], bool]) -> ActResult:
        value = self._ask("press_approved", (int(tab), source, tuple(str(host) for host in hosts), float(settle_s),
                                             float(timeout_s)),
                          timeout_s + ACT_MARGIN_S, cancelled)
        return value if isinstance(value, ActResult) else ActResult(ok=False)

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
            shot_op = job.op in ("load", "load_signed_in", "load_file", "picture")
            if shot_op:
                job.finish(ShotResult(False, snapshots.R_ENGINE_FAILED))
            else:
                job.finish(ActResult(ok=False) if job.op in _ACT_OPS else None)

    def _dropped(self, job: _Job) -> None:
        """Nobody waits for that any more: a page that kept it waiting goes (a hung renderer spins no longer). A drive
        step's own time limit already ends a page that never answers (AgentBrowser.act)."""
        if self._browser is not None and not self._ended.is_set() and not job.done.is_set() and job.op in _PAGE_OPS:
            self._browser.stop_now()

    def stop(self) -> None:
        """A Stop or a Turn off: waiters are released now, the page and the window go at once (one that lingers too)."""
        self._ended.set()
        if self._browser is not None:
            self._browser.end_run(False)

    def end_run(self, *, linger: bool = True, hold: bool = False, big: bool = False) -> None:
        """The run ended: waiters are released, no page after this. Once: what came first stands. ``hold``: a window
        that lingers stays until close_now (the result card's). ``big``: the result's page "opens big" first
        (AgentBrowser.end_run)."""
        if self._ended.is_set():
            return
        self._ended.set()
        if self._browser is not None:
            if big:
                self._browser.end_run(linger, hold, True)
            else:
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


__all__ = ["AgentBrowser", "BrowserClient", "FINISHED_TAG", "READING_TAG", "TAB_LIMIT", "WatchWindow", "big_rect",
           "file_kind", "gate_held", "register_scheme", "scheme_ready", "watch_rect"]
