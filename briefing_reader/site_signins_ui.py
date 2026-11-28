"""Sites Jarvis may use signed in, in the app (Qt, GUI thread): Jarvis's sign-in profile, the owner's sign-in window and
Settings > LIVE and privacy's part for them. The list and the rules are site_signins'.

``SignInProfile(data_dir, store=None, parent=None)``
    The one sign-in profile of the process (snapshot_engine.signin_profile: its cookies and site storage in the data
    folder's ``site-signins`` folder alone, never a browser's own; nothing is imported from Chrome, Edge or any other
    browser). Made only when it is first needed: the owner's Sign in..., a task's page on a listed site, a Remove that
    has cookies to delete. It is lent to one holder at a time - the agent's window for one page (agent.browser) or the
    owner's sign-in window - ``lend(holder)`` (None when it is in use, being forgotten, the list is empty or the engine
    can't make it; ``refusal`` says which) and ``give_back(holder)``; while nobody holds it, every request of it is
    refused. ``hosts()`` the owner's list. ``remove_site(host)``: off the list, and that site's cookies (its subdomains'
    too) deleted from the profile - now, or the next time the profile is made. ``forget()``: every cookie deleted, the
    profile released, then the whole folder deleted (``forgotten(ok)``; a folder that stays locked is deleted at the
    next start, before anything uses it: FORGET_MARK; never a cache clear meanwhile - the cache is in memory, and
    clearing it while the profile goes crashed the engine, measured). ``shutdown()`` when Jarvis closes. Never used by a research page
    picture, the board or a snapshot: only agent.browser and SignInWindow take it.

``SignInWindow(lender, host)``
    A plain window of Jarvis's own on ``https://<host>/`` with the sign-in profile, where the owner signs in himself and
    then clicks Done (or closes it). A strip says where he is now (the host only). Its pages may open a pop-up (a
    sign-in with another site), shown in the same window until it closes itself; downloads, file pickers, permissions,
    full screen, client certificates and bad certificates are refused; every request is public https only
    (snapshots.request_problem) and goes through Jarvis's network gate as every page does. ``start()``: "" when the
    site is opening, else Jarvis's words why not (on a test platform no site ever opens).

``SitesPanel()``
    The Settings part: "Sites Jarvis may use signed in", what it does, a row per site (its name, Sign in..., Remove), a
    field and Add for a new site (validated: a host such as example.com, no page), and Forget every sign-in (two
    clicks). Signals addAsked(text), signInAsked(host), removeAsked(host), forgetAsked(); ``show_sites(...)``.

``SitesControl(lender, panel, window_factory=None)``
    Wires them: Add, Sign in..., Remove, Forget, and the panel's words after each.

Logs: counts and reason words only, never a host, an address or anything of a page.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from . import holo, hud, snapshot_engine, snapshots
from .site_signins import FOLDER, FORGET_MARK, SiteList, cookie_on, page_host

logger = logging.getLogger(__name__)

# Why the profile was not lent (SignInProfile.refusal; logs and the panel's words).
R_BUSY, R_FORGETTING, R_NO_SITES, R_ENGINE = "busy", "forgetting", "no_sites", "engine"
FORGET_TRIES = 25                 # the folder is tried this many times after the profile went ...
FORGET_RETRY_MS = 200             # ... this far apart; then it is deleted at the next start
FORGET_ARM_MS = 8000              # Forget every sign-in's second click counts this long after the first
MAX_WINDOW_PAGES = 3              # the sign-in window's page and the pop-ups it opened, at most

TITLE = "Sites Jarvis may use signed in"
INTRO = ("A task can read these sites signed in as you, read only: Jarvis never sends, posts or changes anything there "
         "without your click on a Send card. You sign in yourself, in a window of Jarvis's own - nothing is taken from "
         "your own web browser or any other. Every other page he opens stays off the record.")
EMPTY_TEXT = "No sites yet: every page Jarvis opens is off the record, signed in nowhere."
FIELD_HINT = "example.com"
ADD_TEXT = "Add"
SIGN_IN_TEXT = "Sign in…"
REMOVE_TEXT = "Remove"
FORGET_TEXT = "Forget every sign-in"
ADDED_NOTE = "{host} is on the list. Click Sign in… to sign in to it in Jarvis's window."
REMOVED_NOTE = "{host} is off the list, and Jarvis deletes its sign-in cookies from his window."
FORGET_CONFIRM = ("This signs Jarvis out of every site and deletes his sign-in folder; your list stays. Click Forget "
                  "every sign-in again to do it.")
FORGETTING_NOTE = "Forgetting every sign-in…"
FORGOT_NOTE = "Every sign-in is forgotten: Jarvis's sign-in folder is deleted."
FORGOT_LATER_NOTE = "Every site is signed out; the folder itself is deleted the next time Jarvis starts."
WINDOW_NOTE = "Sign in in the window that opened, then click Done there."
BUSY_WORDS = "A task is reading a site signed in right now. Try again when it ends."
FORGETTING_WORDS = "Jarvis is forgetting every sign-in. Try again in a moment."
ENGINE_WORDS = "Jarvis's own window can't open sites on this PC or in this run."
TEST_RUN_WORDS = "No site opens in a test run."
SEALED_WORDS = "Jarvis's window holds what he typed for a task. Try again when the task ends."
NOT_LISTED_WORDS = "That site is not on the list."
REFUSAL_WORDS = {R_BUSY: BUSY_WORDS, R_FORGETTING: FORGETTING_WORDS, R_NO_SITES: NOT_LISTED_WORDS,
                 R_ENGINE: ENGINE_WORDS}
WINDOW_TITLE = "Sign in to {host} - Jarvis"
WINDOW_WORDS = "Sign in to {host} here, then click Done. This window is Jarvis's own."
NOW_ON_WORDS = "Now on {host}"
DONE_TEXT = "Done"


def _quietly(step: Callable[[], Any]) -> None:
    try:
        step()
    except Exception:  # noqa: BLE001 - an object already gone
        pass


def _refuse_all(profile: Any) -> Any:
    """An interceptor that refuses every request: on the profile while nobody holds it."""
    return snapshot_engine.engine_classes().Interceptor(lambda _info: True, profile)


# --------------------------------------------------------------------------
# The profile
# --------------------------------------------------------------------------

class SignInProfile(QObject):
    """See the module docs. ``make_profile(folder, parent)``, ``ready()`` and ``blocker(profile)`` are the engine's
    seams (tests fake them)."""

    changed = Signal()                 # lent, given back, made or forgotten: the panel looks again
    forgotten = Signal(bool)           # Forget every sign-in ended: True when the folder is gone now

    def __init__(self, data_dir: Path | str, store: SiteList | None = None, *, parent: QObject | None = None,
                 make_profile: Callable[[str, QObject], Any] | None = None, ready: Callable[[], str] | None = None,
                 blocker: Callable[[Any], Any] | None = None, retry_ms: int = FORGET_RETRY_MS,
                 tries: int = FORGET_TRIES) -> None:
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.folder = self.data_dir / FOLDER
        self.store = store if store is not None else SiteList(self.data_dir)
        self._make = make_profile or snapshot_engine.signin_profile
        self._ready = ready or snapshot_engine.signin_ready
        self._blocker = blocker or _refuse_all
        self._profile: Any = None
        self._refusal_interceptor: Any = None
        self._holder: Any = None
        self._token: object | None = None
        self._forgetting = False
        self._tries = 0
        self._max_tries = max(1, int(tries))
        self._deleted_cookies = 0
        self.refusal = ""                  # why the last lend() gave nothing (R_*)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(max(0, int(retry_ms)))
        self._retry.timeout.connect(self._remove_folder)
        self._finish_earlier_forget()

    def __repr__(self) -> str:        # counts and states only
        return (f"SignInProfile({len(self.store.hosts())} site(s), made={self._profile is not None}, "
                f"held={self._holder is not None})")

    # ---- what the others ask --------------------------------------------------------------------------------

    def hosts(self) -> tuple[str, ...]:
        return self.store.hosts()

    def made(self) -> bool:
        return self._profile is not None

    def held_by(self) -> Any:
        return self._holder

    def forgetting(self) -> bool:
        return self._forgetting

    def deleted_cookies(self) -> int:
        """Cookies of removed sites deleted in this run (a count)."""
        return self._deleted_cookies

    def lend(self, holder: QObject) -> Any:
        """The profile for ``holder`` alone until give_back (or until ``holder`` is destroyed); None when it is held,
        being forgotten, nothing is listed, or the engine can't make it (``refusal`` says which)."""
        if self._holder is not None:
            self.refusal = R_BUSY
        elif self._forgetting:
            self.refusal = R_FORGETTING
        elif not self.store.hosts():
            self.refusal = R_NO_SITES
        else:
            profile = self._ensure()
            if profile is None:
                self.refusal = R_ENGINE
            else:
                self.refusal = ""
                self._holder = holder
                token = self._token = object()
                destroyed = getattr(holder, "destroyed", None)
                if destroyed is not None:
                    _quietly(lambda: destroyed.connect(lambda *_args, mine=token: self._holder_gone(mine)))
                logger.info("Signed-in sites: the sign-in profile is lent")
                self.changed.emit()
                return profile
        logger.info("Signed-in sites: the sign-in profile was not lent (%s)", self.refusal)
        return None

    def give_back(self, holder: Any, _profile: Any = None) -> None:
        """``holder`` is done with it: every request of it is refused again until the next lend."""
        if holder is None or holder is not self._holder:
            return
        self._release()

    def _holder_gone(self, token: object) -> None:
        if not shiboken6.isValid(self):    # the lender went first (Jarvis closing): nothing to give back to
            return
        if token is self._token:
            self._release()

    def _release(self) -> None:
        self._holder, self._token = None, None
        self._refuse_everything()
        logger.info("Signed-in sites: the sign-in profile was given back")
        self.changed.emit()

    # ---- the profile ----------------------------------------------------------------------------------------

    def _ensure(self) -> Any:
        if self._profile is not None:
            return self._profile
        if self._forgetting:
            return None
        try:
            why = self._ready()
            if why:
                logger.info("Signed-in sites: the web engine is not ready (%s)", snapshots.reason_kind(why))
                return None
            self.folder.mkdir(parents=True, exist_ok=True)
            profile = self._make(str(self.folder), self)
        except Exception as exc:  # noqa: BLE001 - no profile: every page stays off the record
            logger.warning("Signed-in sites: the sign-in profile could not be made (%s)", type(exc).__name__)
            return None
        if profile is None:
            logger.warning("Signed-in sites: the engine gave no usable sign-in profile")
            return None
        self._profile = profile
        self._refuse_everything()
        try:
            cookies = profile.cookieStore()
            cookies.cookieAdded.connect(self._on_cookie)
            if self.store.removed():
                cookies.loadAllCookies()   # the removed sites' cookies are deleted as they come (_on_cookie)
        except Exception as exc:  # noqa: BLE001 - the profile works; removals wait for the next time
            logger.debug("Signed-in sites: the cookie store was not reached (%s)", type(exc).__name__)
        logger.info("Signed-in sites: the sign-in profile is ready")
        return profile

    def _refuse_everything(self) -> None:
        profile = self._profile
        if profile is None:
            return
        try:
            if self._refusal_interceptor is None:
                self._refusal_interceptor = self._blocker(profile)
            profile.setUrlRequestInterceptor(self._refusal_interceptor)
        except Exception as exc:  # noqa: BLE001 - its pages are gone or going; their shots refuse what is late
            logger.debug("Signed-in sites: the profile's refusal was not set (%s)", type(exc).__name__)

    def _on_cookie(self, cookie: Any) -> None:
        """A cookie the profile has (loaded or set): one of a removed site is deleted."""
        removed = self.store.removed()
        if not removed or self._profile is None:
            return
        try:
            if cookie_on(cookie.domain(), removed):
                self._profile.cookieStore().deleteCookie(cookie)
                self._deleted_cookies += 1
        except Exception as exc:  # noqa: BLE001 - tried again the next time the profile is made
            logger.debug("Signed-in sites: a cookie was not deleted (%s)", type(exc).__name__)

    # ---- the owner's Remove and Forget -------------------------------------------------------------------------

    def remove_site(self, host: str) -> bool:
        """Off the list now (never used again in this run, also when the list could not be saved), and that site's
        cookies deleted from the profile: now when it is made or its folder exists, else nothing is there to delete.
        True when the list was saved."""
        saved = self.store.remove(host)
        if self._profile is None and not self.folder.exists():
            self.store.forget_removed([host])     # nothing was ever kept for it
        elif not self._forgetting:
            profile = self._ensure()
            if profile is not None:
                _quietly(lambda: profile.cookieStore().loadAllCookies())
        self.changed.emit()
        return saved

    def forget(self) -> str:
        """Forget every sign-in: "" when it started (``forgotten`` tells the end), else a refusal (R_BUSY while a page
        or a window holds the profile: close it first; R_FORGETTING while one runs)."""
        if self._holder is not None:
            return R_BUSY
        if self._forgetting:
            return R_FORGETTING
        self._forgetting = True
        self._tries = 0
        self._write_mark()                        # if this run ends before the folder goes, the next start does it
        self.store.forget_removed(self.store.removed())
        profile, self._profile = self._profile, None
        interceptor, self._refusal_interceptor = self._refusal_interceptor, None
        logger.info("Signed-in sites: the owner asked to forget every sign-in")
        self.changed.emit()
        if profile is None:
            self._retry.start(0)
            return ""
        _quietly(lambda: profile.cookieStore().deleteAllCookies())   # (its HTTP cache is in memory only)
        try:
            profile.destroyed.connect(lambda *_args: self._retry.start(0))
            profile.deleteLater()               # its refusal with it
        except Exception:  # noqa: BLE001 - gone already
            self._retry.start(0)
        del interceptor
        return ""

    def _remove_folder(self) -> None:
        try:
            if self.folder.exists():
                shutil.rmtree(self.folder)
        except OSError:
            pass                               # a file still open: tried again
        if not self.folder.exists():
            self._drop_mark()
            self._forget_done(True)
        elif self._tries < self._max_tries:
            self._tries += 1
            self._retry.start()
        else:
            logger.warning("Signed-in sites: the sign-in folder is still in use; it is deleted at the next start")
            self._forget_done(False)

    def _forget_done(self, gone: bool) -> None:
        self._forgetting = False
        logger.info("Signed-in sites: every sign-in is forgotten (%s)", "folder_gone" if gone else "next_start")
        self.forgotten.emit(gone)
        self.changed.emit()

    def _mark(self) -> Path:
        return self.data_dir / FORGET_MARK

    def _write_mark(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._mark().write_bytes(b"forget\n")
        except OSError as exc:
            logger.warning("Signed-in sites: the forget mark was not written (%s)", type(exc).__name__)

    def _drop_mark(self) -> None:
        try:
            self._mark().unlink(missing_ok=True)
        except OSError:
            pass

    def _finish_earlier_forget(self) -> None:
        """A Forget the last run could not finish: the folder goes now, before any profile of this run is made."""
        if not self._mark().exists():
            return
        try:
            if self.folder.exists():
                shutil.rmtree(self.folder)
        except OSError as exc:
            logger.warning("Signed-in sites: an earlier Forget could not finish (%s)", type(exc).__name__)
            return
        self._drop_mark()
        logger.info("Signed-in sites: an earlier Forget finished")

    def shutdown(self) -> None:
        """Jarvis closes: the profile goes (its pages went first: the agent's window and the sign-in window close
        before this)."""
        self._retry.stop()
        profile, self._profile = self._profile, None
        self._holder, self._token = None, None
        if profile is None:
            return
        if snapshot_engine._event_loop_running():
            _quietly(profile.deleteLater)
        else:
            _quietly(lambda: snapshot_engine._delete_now(profile))


# --------------------------------------------------------------------------
# The owner's sign-in window
# --------------------------------------------------------------------------

_PAGE_CLASS: list[Any] = []


def _page_class(classes: Any) -> Any:
    """The sign-in window's page: its pop-ups go to the window; console text dropped; no file picker."""
    if _PAGE_CLASS:
        return _PAGE_CLASS[0]

    class SignInPage(classes.QWebEnginePage):
        def __init__(self, profile: Any, parent: QObject, opener: Callable[[], Any]) -> None:
            super().__init__(profile, parent)
            self._opener = opener

        def createWindow(self, _type: Any) -> Any:  # noqa: N802 - Qt's name
            try:
                return self._opener()
            except Exception:  # noqa: BLE001 - never raise into Chromium: no window
                return None

        def javaScriptConsoleMessage(self, *_args: Any) -> None:  # noqa: N802 - the text is dropped
            return None

        def chooseFiles(self, *_args: Any) -> list[str]:  # noqa: N802
            return []

    _PAGE_CLASS.append(SignInPage)
    return SignInPage


class SignInWindow(QWidget):
    """See the module docs. ``classes``: the engine's classes (tests fake them)."""

    closed = Signal()

    def __init__(self, lender: SignInProfile, host: str, *, classes: Any = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.host = host
        self._lender = lender
        self._classes = classes
        self._profile: Any = None
        self._interceptor: Any = None
        self._view: Any = None
        self._pages: list[Any] = []
        self._hooks: dict[str, int] = {}
        self._ended = False
        self.setWindowTitle(WINDOW_TITLE.format(host=host))
        self.setAccessibleName(WINDOW_TITLE.format(host=host))
        self.resize(1040, 760)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(holo.VOID))
        self.setPalette(palette)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        strip = QWidget(self)
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(12)
        self.words = QLabel(WINDOW_WORDS.format(host=host))
        self.words.setFont(hud.body_font(13))
        hud.set_label_color(self.words, hud.TEXT_BODY)
        self.now_on = QLabel(NOW_ON_WORDS.format(host=host))
        self.now_on.setFont(hud.body_font(12))
        hud.set_label_color(self.now_on, hud.TEXT_SOFT)
        self.done_button = holo.HoloButton(DONE_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.done_button.setAccessibleName("Done signing in: close this window")
        self.done_button.clicked.connect(self.close)
        row.addWidget(self.words, 1)
        row.addWidget(self.now_on)
        row.addWidget(self.done_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(strip)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setFont(hud.body_font(14))
        hud.set_label_color(self.notice, hud.TEXT_SOFT)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.hide()
        layout.addWidget(self.notice, 1)
        self._body = layout

    def __repr__(self) -> str:        # never the host
        return f"SignInWindow(pages={len(self._pages)}, ended={self._ended})"

    def hook_counts(self) -> dict[str, int]:
        return dict(self._hooks)

    def pages(self) -> int:
        return len(self._pages)

    def start(self) -> str:
        """Open the site: "" when it is on its way, else Jarvis's words why not (nothing opened, nothing held)."""
        if snapshot_engine.on_test_platform() and self._classes is None:
            self._say(TEST_RUN_WORDS)
            return TEST_RUN_WORDS
        if self.host not in self._lender.hosts():
            return NOT_LISTED_WORDS
        profile = self._lender.lend(self)
        if profile is None:
            return REFUSAL_WORDS.get(self._lender.refusal, ENGINE_WORDS)
        if self._classes is None and snapshot_engine.gate_sealed():
            self._lender.give_back(self)
            return SEALED_WORDS
        try:
            classes = self._classes if self._classes is not None else snapshot_engine.engine_classes()
            self._profile = profile
            self._interceptor = classes.Interceptor(lambda info: self._judge(classes, info), profile)
            profile.setUrlRequestInterceptor(self._interceptor)
            self._view = classes.QWebEngineView(self)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self._body.addWidget(self._view, 1)
            page = self._new_page(classes)
            self._view.setPage(page)
            page.load(QUrl(f"https://{self.host}/"))
        except Exception as exc:  # noqa: BLE001 - nothing half open is left
            logger.warning("Signed-in sites: the sign-in window did not open (%s)", type(exc).__name__)
            self._end()
            return ENGINE_WORDS
        logger.info("Signed-in sites: the owner's sign-in window opened")
        return ""

    def _say(self, words: str) -> None:
        self.notice.setText(words)
        self.notice.show()

    def _count(self, name: str) -> None:
        self._hooks[name] = self._hooks.get(name, 0) + 1

    def _judge(self, classes: Any, info: Any) -> bool:
        """Every request of the window's pages: public https only (as a page picture's), no download."""
        if self._ended:
            return True
        url = info.requestUrl()
        scheme = url.scheme().casefold()
        if info.isDownload():
            self._count("downloads")
            return True
        text = f"{scheme}:" if scheme in ("data", "blob") else snapshot_engine._encoded(url)
        if snapshots.request_problem(text, main_frame=info.resourceType() in classes.main_types):
            self._count("blocked")
            return True
        return False

    def _new_page(self, classes: Any) -> Any:
        page = _page_class(classes)(self._profile, self, lambda: self._pop_up(classes))
        self._wire(page)
        self._pages.append(page)
        return page

    def _pop_up(self, classes: Any) -> Any:
        """A page of the window opened a pop-up (a sign-in with another site): it shows here until it closes itself."""
        if self._ended or len(self._pages) >= MAX_WINDOW_PAGES:
            self._count("windows")
            return None
        page = self._new_page(classes)
        self._view.setPage(page)
        return page

    def _wire(self, page: Any) -> None:
        page.urlChanged.connect(self._on_url)
        page.windowCloseRequested.connect(lambda which=page: self._close_page(which))
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(lambda permission: self._refuse("permissions", permission, "deny"))
        for signal_name, method in (("fileSystemAccessRequested", "reject"), ("desktopMediaRequested", "cancel"),
                                    ("webAuthUxRequested", "cancel"), ("registerProtocolHandlerRequested", "reject"),
                                    ("fullScreenRequested", "reject"), ("certificateError", "rejectCertificate"),
                                    ("selectClientCertificate", "selectNone")):
            signal = getattr(page, signal_name, None)
            if signal is not None:
                signal.connect(lambda request, name=signal_name, how=method: self._refuse(name, request, how))

    def _refuse(self, name: str, request: Any, method: str) -> None:
        self._count(name)
        _quietly(lambda: getattr(request, method)())

    def _on_url(self, url: Any) -> None:
        try:
            host = page_host(snapshot_engine._encoded(url)) if not isinstance(url, str) else page_host(url)
        except Exception:  # noqa: BLE001 - only the strip's words
            host = ""
        if host:
            self.now_on.setText(NOW_ON_WORDS.format(host=host))

    def _close_page(self, page: Any) -> None:
        """A page asked to close itself: a pop-up goes and the page under it shows again; the first page closes the
        window."""
        if page not in self._pages:
            return
        if page is self._pages[0]:
            self.close()
            return
        self._pages.remove(page)
        if self._view is not None:
            _quietly(lambda: self._view.setPage(self._pages[-1]))
        _quietly(page.deleteLater)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._end()
        super().closeEvent(event)

    def _end(self) -> None:
        """The pages go, then the profile goes back to its lender (which refuses its requests from then on)."""
        if self._ended:
            return
        self._ended = True
        view, pages, self._pages = self._view, list(self._pages), []
        if view is not None:
            _quietly(lambda: view.setPage(None))
        for page in reversed(pages):
            _quietly(lambda page=page: page.triggerAction(type(page).WebAction.Stop))
            _quietly(page.deleteLater)
        if self._profile is not None:
            self._lender.give_back(self)
        if self._interceptor is not None:
            _quietly(self._interceptor.deleteLater)
        self._profile = None
        logger.info("Signed-in sites: the owner's sign-in window closed")
        self.closed.emit()


# --------------------------------------------------------------------------
# Settings > LIVE and privacy
# --------------------------------------------------------------------------

def _label(text: str, px: int, color: str, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setFont(hud.body_font(px))
    hud.set_label_color(label, color)
    return label


class SitesPanel(QWidget):
    """See the module docs."""

    addAsked = Signal(str)
    signInAsked = Signal(str)
    removeAsked = Signal(str)
    forgetAsked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .settings_edit import _field_style

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        self.title = _label(TITLE, 14, hud.TEXT_BRIGHT, wrap=False)
        self.title.setFont(hud.body_font(14, 600))
        layout.addWidget(self.title)
        self.intro = _label(INTRO, 13, hud.TEXT_SOFT)
        layout.addWidget(self.intro)
        self.empty = _label(EMPTY_TEXT, 13, hud.TEXT_DIM)
        layout.addWidget(self.empty)
        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(6)
        layout.addLayout(self._rows_box)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 4, 0, 0)
        add_row.setSpacing(10)
        self.field = QLineEdit()
        self.field.setPlaceholderText(FIELD_HINT)
        self.field.setAccessibleName("A site Jarvis may use signed in, such as example.com")
        self.field.setMaxLength(260)
        self.field.setFont(hud.body_font(13))
        _field_style(self.field)
        self.field.returnPressed.connect(self._add_clicked)
        self.add_button = holo.HoloButton(ADD_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.add_button.setAccessibleName("Add this site")
        self.add_button.clicked.connect(self._add_clicked)
        add_row.addWidget(self.field, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)
        self.note = _label("", 12, hud.TEXT_DIM)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note.hide()
        layout.addWidget(self.note)
        forget_row = QHBoxLayout()
        forget_row.setContentsMargins(0, 4, 0, 0)
        self.forget_button = holo.HoloButton(FORGET_TEXT, holo.DANGER_GHOST, size=holo.SIZE_SM)
        self.forget_button.setAccessibleName("Forget every sign-in: delete Jarvis's sign-in folder")
        self.forget_button.clicked.connect(lambda _checked=False: self.forgetAsked.emit())
        forget_row.addWidget(self.forget_button)
        forget_row.addStretch(1)
        layout.addLayout(forget_row)
        self._rows: list[tuple[str, QLabel, Any, Any]] = []

    def _add_clicked(self, *_args: Any) -> None:
        self.addAsked.emit(self.field.text())

    def show_sites(self, hosts: Sequence[str], *, note: str = "", signing_in: str = "", forgetting: bool = False,
                   clear_field: bool = False) -> None:
        """A row per site (its name, Sign in..., Remove: off for the one whose window is open and while forgetting),
        the empty line when there is none, the note under the field."""
        while self._rows:
            _host, label, sign_in, remove = self._rows.pop()
            for widget in (label, sign_in, remove):
                widget.hide()
                widget.deleteLater()
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            if item.layout() is not None:
                item.layout().deleteLater()
        for host in hosts:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            label = _label(host, 13, hud.TEXT_BODY, wrap=False)
            sign_in = holo.HoloButton(SIGN_IN_TEXT, holo.GHOST, size=holo.SIZE_SM)
            sign_in.setAccessibleName(f"Sign in to {host} in Jarvis's window")
            sign_in.clicked.connect(lambda _checked=False, which=host: self.signInAsked.emit(which))
            remove = holo.HoloButton(REMOVE_TEXT, holo.GHOST, size=holo.SIZE_SM)
            remove.setAccessibleName(f"Remove {host} and delete its sign-in")
            remove.clicked.connect(lambda _checked=False, which=host: self.removeAsked.emit(which))
            sign_in.setEnabled(not forgetting and host != signing_in)
            remove.setEnabled(not forgetting)
            row.addWidget(label, 1)
            row.addWidget(sign_in)
            row.addWidget(remove)
            self._rows_box.addLayout(row)
            self._rows.append((host, label, sign_in, remove))
        self.empty.setVisible(not hosts)
        self.forget_button.setEnabled(not forgetting)
        if clear_field:
            self.field.clear()
        self.note.setText(note)
        self.note.setVisible(bool(note))

    def site_rows(self) -> list[tuple[str, str, str]]:
        """(site, its sign-in button's words, its remove button's words) per row (tests)."""
        return [(host, sign_in.text(), remove.text()) for host, _label_, sign_in, remove in self._rows]

    def row_buttons(self, host: str) -> tuple[Any, Any] | None:
        for name, _label_, sign_in, remove in self._rows:
            if name == host:
                return sign_in, remove
        return None


class SitesControl(QObject):
    """See the module docs. ``window_factory(lender, host)`` makes the sign-in window (tests fake it)."""

    def __init__(self, lender: SignInProfile, panel: SitesPanel, *,
                 window_factory: Callable[[SignInProfile, str], Any] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.lender = lender
        self.panel = panel
        self._factory = window_factory or (lambda owner, host: SignInWindow(owner, host))
        self.window: Any = None
        self._note = ""
        self._armed = QTimer(self)
        self._armed.setSingleShot(True)
        self._armed.setInterval(FORGET_ARM_MS)
        panel.addAsked.connect(self.add)
        panel.signInAsked.connect(self.sign_in)
        panel.removeAsked.connect(self.remove)
        panel.forgetAsked.connect(self.forget)
        lender.changed.connect(self.refresh)
        lender.forgotten.connect(self._on_forgotten)
        self.refresh()

    def note(self) -> str:
        return self._note

    def refresh(self, *, clear_field: bool = False) -> None:
        window = self.window
        self.panel.show_sites(self.lender.hosts(), note=self._note,
                              signing_in=window.host if window is not None else "",
                              forgetting=self.lender.forgetting(), clear_field=clear_field)

    def add(self, text: str) -> None:
        host, problem = self.lender.store.add(text)
        self._note = problem or ADDED_NOTE.format(host=host)
        self.refresh(clear_field=not problem)

    def sign_in(self, host: str) -> None:
        """The owner's Sign in...: Jarvis's window on that site (one at a time; a second click brings it forward)."""
        if self.window is not None:
            _quietly(self.window.raise_)
            _quietly(self.window.activateWindow)
            return
        window = self._factory(self.lender, host)
        why = window.start()
        if why:
            self._note = why
            _quietly(window.deleteLater)
            self.refresh()
            return
        self.window = window
        window.closed.connect(self._on_window_closed)
        window.show()
        self._note = WINDOW_NOTE
        self.refresh()

    def _on_window_closed(self) -> None:
        window, self.window = self.window, None
        if window is not None:
            _quietly(window.deleteLater)
        self._note = ""
        self.refresh()

    def close_window(self) -> None:
        if self.window is not None:
            _quietly(self.window.close)

    def remove(self, host: str) -> None:
        if self.window is not None and self.window.host == host:
            self.close_window()
        self.lender.remove_site(host)
        self._note = REMOVED_NOTE.format(host=host)
        self.refresh()

    def forget(self) -> None:
        """Two clicks: the first says what it does; the second, within FORGET_ARM_MS, does it."""
        if not self._armed.isActive():
            self._armed.start()
            self._note = FORGET_CONFIRM
            self.refresh()
            return
        self._armed.stop()
        self.close_window()
        why = self.lender.forget()
        self._note = {"": FORGETTING_NOTE, R_BUSY: BUSY_WORDS, R_FORGETTING: FORGETTING_WORDS}.get(why, BUSY_WORDS)
        self.refresh()

    def _on_forgotten(self, gone: bool) -> None:
        self._note = FORGOT_NOTE if gone else FORGOT_LATER_NOTE
        self.refresh()

    def shutdown(self) -> None:
        self._armed.stop()
        if self.window is not None:
            window, self.window = self.window, None
            _quietly(window.close)
        self.lender.shutdown()


__all__ = ["SignInProfile", "SignInWindow", "SitesControl", "SitesPanel"]
