"""Sites Jarvis may use signed in (site_signins, site_signins_ui, and their seams in agent.browser, agent.web,
agent.drive and agent.tools).

Everything here uses fakes for the web engine's pieces, but for one check that runs the real engine in a process of its
own, offscreen, with the dead proxy: it makes the sign-in profile in a temporary folder, lends and forgets it, and
opens no site at all (no network). Skipped when this PySide6 has no QtWebEngine 6.8+.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication

from briefing_reader import site_signins, site_signins_ui, snapshot_engine, snapshots
from briefing_reader.agent import browser as agent_browser
from briefing_reader.agent import drive, tools, web
from briefing_reader.site_signins import (FOLDER, FORGET_MARK, LIST_FILE, SiteList, host_listed, host_problem,
                                          listed_host, navigation_refused, request_refused)
from tests.agent_fakes import FakeBrowser, FakeControl, FakeDriveBrowser, FakePage
from tests.test_agent_drive import DriveCase
from tests.ui_fakes import fonts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_site_signins", "-platform", "offscreen"])
    fonts()


def _spin(times: int = 5) -> None:
    for _ in range(times):
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # deleteLater too


def _folder(test: unittest.TestCase) -> Path:
    made = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    test.addCleanup(made.cleanup)
    return Path(made.name)


# --------------------------------------------------------------------------
# Fakes for the engine's pieces
# --------------------------------------------------------------------------

class FakeCookie:
    def __init__(self, domain: str) -> None:
        self._domain = domain

    def domain(self) -> str:
        return self._domain


class FakeCookieStore(QObject):
    cookieAdded = Signal(object)

    def __init__(self, cookies: list[FakeCookie]) -> None:
        super().__init__()
        self.cookies = cookies
        self.deleted: list[str] = []
        self.all_deleted = 0

    def loadAllCookies(self) -> None:  # noqa: N802 - Qt's name
        for cookie in list(self.cookies):
            self.cookieAdded.emit(cookie)

    def deleteCookie(self, cookie: FakeCookie) -> None:  # noqa: N802
        self.deleted.append(cookie.domain())
        self.cookies.remove(cookie)

    def deleteAllCookies(self) -> None:  # noqa: N802
        self.all_deleted += 1
        self.cookies.clear()


class FakeProfile(QObject):
    """A persistent profile's face: the interceptor it holds, its cookie store; deleteLater counted."""

    def __init__(self, folder: str, parent: QObject | None = None, cookies: list[FakeCookie] | None = None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.interceptor: Any = "none"
        self.store = FakeCookieStore(cookies if cookies is not None else [])
        self.deletes = 0
        self.cache_cleared = 0

    def isOffTheRecord(self) -> bool:  # noqa: N802
        return False

    def setUrlRequestInterceptor(self, interceptor: Any) -> None:  # noqa: N802
        self.interceptor = interceptor

    def cookieStore(self) -> FakeCookieStore:  # noqa: N802
        return self.store

    def clearHttpCache(self) -> None:  # noqa: N802
        self.cache_cleared += 1

    def deleteLater(self) -> None:  # noqa: N802
        self.deletes += 1
        super().deleteLater()


BLOCK_ALL = "block-all"


class Lender:
    """A SignInProfile with the engine faked: ``made`` counts profiles made; ``ready`` is the engine's answer."""

    def __init__(self, test: unittest.TestCase, *, hosts: tuple[str, ...] = ("example.com",), ready: str = "",
                 cookies: list[FakeCookie] | None = None, data: Path | None = None) -> None:
        self.data = data if data is not None else _folder(test)
        self.store = SiteList(self.data)
        for host in hosts:
            self.store.add(host)
        self.made: list[FakeProfile] = []
        self.cookies = cookies

        def make(folder: str, parent: QObject) -> FakeProfile:
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / "Cookies").write_bytes(b"cookie jar")
            profile = FakeProfile(folder, parent, self.cookies)
            self.made.append(profile)
            return profile

        self.profile = site_signins_ui.SignInProfile(self.data, self.store, make_profile=make, ready=lambda: ready,
                                                     blocker=lambda _profile: BLOCK_ALL, retry_ms=1, tries=3)
        test.addCleanup(self.profile.deleteLater)


# --------------------------------------------------------------------------
# The owner's typed site
# --------------------------------------------------------------------------

class HostTests(unittest.TestCase):
    def test_a_public_host_is_taken_lower_case(self) -> None:
        for typed, host in (("example.com", "example.com"), ("Mail.Example.COM", "mail.example.com"),
                            ("  example.com.  ", "example.com"), ("https://example.com", "example.com"),
                            ("https://example.com/", "example.com"), ("bücher.de", "xn--bcher-kva.de")):
            self.assertEqual(host_problem(typed), (host, ""), typed)

    def test_a_path_a_port_a_user_or_another_scheme_is_refused(self) -> None:
        for typed in ("example.com/inbox", "https://example.com/inbox", "example.com:8443", "ada@example.com",
                      "http://example.com", "example.com?x=1", "exa mple.com", "*.example.com", "-bad-.com",
                      "a..b.com", "example.com#top", "x" * 300 + ".com"):
            host, problem = host_problem(typed)
            self.assertEqual(host, "", typed)
            self.assertEqual(problem, site_signins.P_SHAPE, typed)

    def test_this_pc_its_network_a_number_address_and_shared_names_are_refused(self) -> None:
        for typed in ("localhost", "intranet", "printer.local", "192.168.1.1", "8.8.8.8", "router.home.arpa"):
            self.assertEqual(host_problem(typed), ("", site_signins.P_LOCAL), typed)
        for typed in ("co.uk", "github.io", "herokuapp.com"):
            self.assertEqual(host_problem(typed), ("", site_signins.P_SHARED), typed)
        self.assertEqual(host_problem("ada.github.io"), ("ada.github.io", ""))
        self.assertEqual(host_problem(""), ("", site_signins.P_EMPTY))
        self.assertEqual(host_problem(None), ("", site_signins.P_EMPTY))


# --------------------------------------------------------------------------
# Which pages use the profile
# --------------------------------------------------------------------------

class ChoiceTests(unittest.TestCase):
    HOSTS = ("example.com", "news.example.org")

    def test_the_listed_host_and_its_subdomains_only(self) -> None:
        self.assertEqual(listed_host("https://example.com/inbox", self.HOSTS), "example.com")
        self.assertEqual(listed_host("https://mail.example.com/u/0?x=1", self.HOSTS), "example.com")
        self.assertEqual(listed_host("https://EXAMPLE.com./", self.HOSTS), "example.com")
        self.assertEqual(listed_host("https://news.example.org/today", self.HOSTS), "news.example.org")
        self.assertEqual(listed_host("https://example.org/", self.HOSTS), "")   # the parent of a listed host is not

    def test_lookalike_hosts_and_other_pages_are_refused(self) -> None:
        for url in ("https://example.com.evil.net/", "https://myexample.com/", "https://example.co/",
                    "https://evil.net/?example.com", "https://evil.net/example.com", "http://example.com/",
                    "https://example.com:8443/", "https://ada@example.com/", "https://example.com@evil.net/",
                    "ftp://example.com/", "", None, "https://exa mple.com/"):
            self.assertEqual(listed_host(url, self.HOSTS), "", url)

    def test_the_longest_listed_entry_wins_and_empty_lists_choose_nothing(self) -> None:
        self.assertEqual(host_listed("a.mail.example.com", ("example.com", "mail.example.com")), "mail.example.com")
        self.assertEqual(listed_host("https://example.com/", ()), "")


# --------------------------------------------------------------------------
# The list file
# --------------------------------------------------------------------------

class ListFileTests(unittest.TestCase):
    def test_a_round_trip_in_lf_and_in_order(self) -> None:
        data = _folder(self)
        store = SiteList(data)
        self.assertEqual(store.hosts(), ())                       # the default: nothing is used signed in
        self.assertEqual(store.add("Example.com"), ("example.com", ""))
        self.assertEqual(store.add("mail.example.org"), ("mail.example.org", ""))
        self.assertEqual(store.add("example.com"), ("", site_signins.P_LISTED))
        raw = (data / LIST_FILE).read_bytes()
        self.assertEqual(raw.count(b"\r"), 0)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(SiteList(data).hosts(), ("example.com", "mail.example.org"))

    def test_a_damaged_file_means_no_sites(self) -> None:
        for body in (b"{not json", b"[]", b'{"sites": "example.com"}', b'{"sites": ["example.com", "localhost"]}',
                     b'{"sites": ["example.com/path"]}', b'{"sites": [3]}', b"\xff\xfe", b" " * 70_000,
                     b'{"sites": ["Example.com"]}', json.dumps({"sites": [f"s{n}.example.com" for n in range(60)]})
                     .encode()):
            data = _folder(self)
            (data / LIST_FILE).write_bytes(body)
            with self.assertLogs("briefing_reader.site_signins", "WARNING"):
                self.assertEqual(SiteList(data).hosts(), (), body[:40])

    def test_remove_keeps_the_cookies_to_delete_and_adding_again_clears_them(self) -> None:
        data = _folder(self)
        store = SiteList(data)
        store.add("example.com")
        self.assertTrue(store.remove("example.com"))
        self.assertEqual(store.hosts(), ())
        again = SiteList(data)
        self.assertEqual(again.removed(), ("example.com",))
        self.assertFalse(again.remove("example.com"))                # not on the list
        again.add("example.com")
        self.assertEqual(again.removed(), ())

    def test_at_most_max_sites(self) -> None:
        store = SiteList(_folder(self))
        for number in range(site_signins.MAX_SITES):
            self.assertEqual(store.add(f"s{number}.example.com")[1], "")
        self.assertEqual(store.add("one-more.example.com"), ("", site_signins.P_FULL))

    def test_an_unsaved_add_is_undone(self) -> None:
        store = SiteList(_folder(self))
        with mock.patch("briefing_reader.prefs._write_atomic", return_value=False):
            self.assertEqual(store.add("example.com"), ("", site_signins.P_UNSAVED))
        self.assertEqual(store.hosts(), ())


# --------------------------------------------------------------------------
# Read only
# --------------------------------------------------------------------------

class ReadOnlyRuleTests(unittest.TestCase):
    def test_a_frame_with_a_write_method_is_refused_without_the_card(self) -> None:
        for method in ("POST", b"POST", "PUT", "PATCH", "DELETE", "post", ""):
            self.assertEqual(request_refused(method, True, False), site_signins.R_WRITE, method)
            self.assertEqual(request_refused(method, True, True), "", method)    # the owner's Send presses it
        for method in ("GET", b"GET", "HEAD"):
            self.assertEqual(request_refused(method, True, False), "")
        self.assertEqual(request_refused("POST", False, False), "")               # a script's own read

    def test_a_form_submit_is_refused_without_the_card_and_the_main_frame_never_leaves(self) -> None:
        hosts = ("example.com",)
        self.assertEqual(navigation_refused("FormSubmittedNavigation", True, "example.com", hosts, False),
                         site_signins.R_FORM)
        self.assertEqual(navigation_refused(SimpleNamespace(name="FormSubmittedNavigation"), False, "x.net", hosts,
                                            False), site_signins.R_FORM)
        self.assertEqual(navigation_refused("FormSubmittedNavigation", True, "example.com", hosts, True), "")
        self.assertEqual(navigation_refused("LinkClickedNavigation", True, "mail.example.com", hosts, False), "")
        self.assertEqual(navigation_refused("RedirectNavigation", True, "example.com.evil.net", hosts, False),
                         site_signins.R_OFF_LIST)
        self.assertEqual(navigation_refused("LinkClickedNavigation", True, "evil.net", hosts, True),
                         site_signins.R_OFF_LIST)                                   # not even with the card
        self.assertEqual(navigation_refused("LinkClickedNavigation", False, "ads.evil.net", hosts, False), "")

    def test_links_that_would_change_the_account(self) -> None:
        for words in ("Sign out", "Log out", "logout", "Delete this message", "Unsubscribe", "Close my account",
                      "Change your password"):
            self.assertTrue(site_signins.signs_out(words), words)
        for words in ("Inbox", "Next page", "Settings", "Out of office replies"):
            self.assertFalse(site_signins.signs_out(words), words)


# --------------------------------------------------------------------------
# The agent's window: which profile, and its gate
# --------------------------------------------------------------------------

class FakeLender:
    def __init__(self, hosts: tuple[str, ...] = ("example.com",), give: bool = True) -> None:
        self._hosts = hosts
        self.give = give
        self.profile = FakeProfile("x")
        self.lent: list[Any] = []
        self.back: list[Any] = []

    def hosts(self) -> tuple[str, ...]:
        return self._hosts

    def lend(self, holder: Any) -> Any:
        self.lent.append(holder)
        return self.profile if self.give else None

    def give_back(self, holder: Any, profile: Any = None) -> None:
        self.back.append((holder, profile))


class OffTheRecord:
    def __init__(self, _parent: Any = None) -> None:
        self.off = True


def _classes() -> Any:
    return SimpleNamespace(QWebEngineProfile=OffTheRecord, main_types=frozenset({"main"}), sub_frame="sub")


def _request(url: str, *, signed_in: bool = True, html: str | None = None) -> Any:
    return agent_browser._Request(url, html, url, None, "", lambda _result: None, "", 1000, signed_in=signed_in)


def _shot(url: str = "https://example.com/") -> Any:
    shot = snapshot_engine._Shot(1, url, None, "")
    shot.opened = True
    shot.offline = False
    return shot


class AgentProfileTests(unittest.TestCase):
    def browser(self, lender: Any = None) -> agent_browser.AgentBrowser:
        made = agent_browser.AgentBrowser(signins=lender, decide=lambda *_args, **_kw: "")
        self.addCleanup(made.deleteLater)
        return made

    def test_a_listed_host_s_page_borrows_the_profile_and_others_stay_off_the_record(self) -> None:
        lender = FakeLender()
        browser = self.browser(lender)
        for url in ("https://example.com/inbox", "https://mail.example.com/"):
            browser._request = _request(url)
            shot = _shot(url)
            self.assertIs(browser._new_profile(shot, _classes()), lender.profile, url)
            self.assertTrue(shot.lent)
            browser._lent_shot = None
        for url in ("https://example.com.evil.net/", "https://myexample.com/", "https://other.org/"):
            browser._request = _request(url)
            shot = _shot(url)
            self.assertIsInstance(browser._new_profile(shot, _classes()), OffTheRecord, url)
            self.assertFalse(shot.lent)
        self.assertEqual(len(lender.lent), 2)

    def test_a_search_a_file_local_html_and_no_lender_never_borrow_it(self) -> None:
        lender = FakeLender()
        browser = self.browser(lender)
        for request in (_request("https://example.com/?q=x", signed_in=False),
                        _request("https://example.com/", html="<p>local</p>")):
            browser._request = request
            self.assertIsInstance(browser._new_profile(_shot(), _classes()), OffTheRecord)
        browser._request = agent_browser._Request("jarvis-task://x", "", "x", object(), "t", lambda _r: None, "",
                                                  100, signed_in=True)
        self.assertIsInstance(browser._new_profile(_shot(), _classes()), OffTheRecord)
        self.assertEqual(lender.lent, [])
        plain = self.browser(None)
        plain._request = _request("https://example.com/")
        self.assertIsInstance(plain._new_profile(_shot(), _classes()), OffTheRecord)

    def test_one_shot_at_a_time_and_an_unlent_profile_means_off_the_record(self) -> None:
        lender = FakeLender()
        browser = self.browser(lender)
        browser._request = _request("https://example.com/")
        first = _shot()
        browser._new_profile(first, _classes())
        second = _shot()
        self.assertIsInstance(browser._new_profile(second, _classes()), OffTheRecord)   # another tab holds it
        self.assertEqual(browser.hook_counts().get("signed_in_busy"), 1)
        refusing = self.browser(FakeLender(give=False))
        refusing._request = _request("https://example.com/")
        shot = _shot()
        self.assertIsInstance(refusing._new_profile(shot, _classes()), OffTheRecord)
        self.assertFalse(shot.lent)

    def test_the_page_going_gives_the_profile_back_and_never_deletes_it(self) -> None:
        lender = FakeLender()
        browser = self.browser(lender)
        browser._request = _request("https://example.com/")
        shot = _shot()
        shot.profile = browser._new_profile(shot, _classes())
        interceptor = QObject()
        shot.interceptor = interceptor
        browser._teardown(shot)
        self.assertEqual(lender.back, [(browser, lender.profile)])
        self.assertEqual(lender.profile.deletes, 0)
        self.assertIsNone(browser._lent_shot)
        off = _shot()
        fresh = off.profile = FakeProfile("off")
        browser._teardown(off)                                  # an off-the-record page's profile goes, as ever
        self.assertEqual(fresh.deletes, 1)
        self.assertEqual(len(lender.back), 1)

    def test_the_lent_profile_gets_only_the_shot_s_judge(self) -> None:
        lender = FakeLender()
        browser = self.browser(lender)
        shot = _shot()
        shot.lent = True
        classes = SimpleNamespace(Interceptor=lambda judge, parent: ("judge", parent))
        browser._configure(lender.profile, classes, shot)
        self.assertEqual(lender.profile.interceptor, ("judge", lender.profile))


class FakeInfo:
    def __init__(self, url: str, method: bytes = b"GET", kind: str = "main") -> None:
        self._url, self._method, self._kind = QUrl(url), method, kind

    def requestUrl(self) -> QUrl:  # noqa: N802
        return self._url

    def requestMethod(self) -> Any:  # noqa: N802
        from PySide6.QtCore import QByteArray

        return QByteArray(self._method)

    def resourceType(self) -> str:  # noqa: N802
        return self._kind


class FakeNavigation:
    def __init__(self, url: str, kind: str = "LinkClickedNavigation", main: bool = True) -> None:
        self._url, self._kind, self._main = QUrl(url), SimpleNamespace(name=kind), main
        self.rejected = False

    def url(self) -> QUrl:
        return self._url

    def navigationType(self) -> Any:  # noqa: N802
        return self._kind

    def isMainFrame(self) -> bool:  # noqa: N802
        return self._main

    def reject(self) -> None:
        self.rejected = True


class AgentGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = agent_browser.AgentBrowser(signins=FakeLender(), decide=lambda *_args, **_kw: "", drive=True)
        self.addCleanup(self.browser.deleteLater)
        self.shot = _shot()
        self.shot.lent = True
        patcher = mock.patch.object(snapshot_engine, "_classes", _classes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def refused(self, info: FakeInfo) -> str:
        return self.browser._signed_in_request(self.shot, info)

    def test_a_write_or_a_main_frame_off_the_list_is_refused(self) -> None:
        self.assertEqual(self.refused(FakeInfo("https://example.com/send", b"POST")), site_signins.R_WRITE)
        self.assertEqual(self.refused(FakeInfo("https://example.com/x", b"PUT", "sub")), site_signins.R_WRITE)
        self.assertEqual(self.refused(FakeInfo("https://example.com.evil.net/")), site_signins.R_OFF_LIST)
        self.assertEqual(self.refused(FakeInfo("https://mail.example.com/inbox")), "")
        self.assertEqual(self.refused(FakeInfo("https://cdn.other.net/app.js", b"GET", "script")), "")
        self.assertEqual(self.refused(FakeInfo("https://example.com/api", b"POST", "xhr")), "")   # its own reads
        self.assertTrue(self.browser._judge(self.shot, FakeInfo("https://example.com/send", b"DELETE")))
        self.assertEqual(self.browser.hook_counts().get("signed_in_write"), 1)

    def test_with_the_owner_s_send_pressing_that_page_may_send(self) -> None:
        self.browser._pressing_shot = self.shot
        self.assertEqual(self.refused(FakeInfo("https://example.com/send", b"POST")), "")
        self.assertEqual(self.refused(FakeInfo("https://evil.net/", b"POST")), site_signins.R_OFF_LIST)
        other = _shot()
        other.lent = True
        self.assertEqual(self.browser._signed_in_request(other, FakeInfo("https://example.com/send", b"POST")),
                         site_signins.R_WRITE)                     # only the page the card presses

    def test_a_form_submit_is_rejected_and_a_link_on_the_site_goes(self) -> None:
        submit = FakeNavigation("https://example.com/send", "FormSubmittedNavigation")
        self.browser._on_navigation(self.shot, submit)
        self.assertTrue(submit.rejected)
        away = FakeNavigation("https://evil.net/")
        self.browser._on_navigation(self.shot, away)
        self.assertTrue(away.rejected)
        link = FakeNavigation("https://mail.example.com/inbox")
        self.browser._on_navigation(self.shot, link)
        self.assertFalse(link.rejected)
        self.browser._pressing_shot = self.shot
        pressed = FakeNavigation("https://example.com/send", "FormSubmittedNavigation")
        self.browser._on_navigation(self.shot, pressed)
        self.assertFalse(pressed.rejected)
        self.assertEqual(self.browser.hook_counts().get("signed_in_form"), 1)

    def test_an_off_the_record_page_has_no_such_rule(self) -> None:
        plain = _shot("https://other.org/")
        submit = FakeNavigation("https://other.org/send", "FormSubmittedNavigation")
        self.browser._on_navigation(plain, submit)
        self.assertFalse(submit.rejected)

    def test_the_client_has_the_signed_in_load(self) -> None:
        self.assertIn("load_signed_in", agent_browser._PAGE_OPS)
        self.assertTrue(callable(getattr(agent_browser.BrowserClient, "load_signed_in", None)))


# --------------------------------------------------------------------------
# The run's side: open_page, the firewall, LIVE's note, the drive's buttons
# --------------------------------------------------------------------------

class SignedInBrowser(FakeBrowser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.signed_loads: list[str] = []
        self.order: list[str] = []

    def load_signed_in(self, url: str, *, timeout_s: float, cancelled: Any) -> snapshots.ShotResult:
        self.signed_loads.append(url)
        result = self.load(url, timeout_s=timeout_s, cancelled=cancelled)
        return snapshots.ShotResult(True, final_url=result.final_url, signed_in=True) if result.ok else result

    def script(self, source: str, **kwargs: Any) -> Any:
        self.order.append("read")
        return super().script(source, **kwargs)


INBOX = "https://mail.example.com/inbox"
OTHER = "https://news.other.org/today"


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = SignedInBrowser({INBOX: FakePage(title="Inbox", text="Your mail."),
                                        OTHER: FakePage(title="News", text="Today.")})
        self.session = web.WebSession(self.browser)
        self.strict: list[str] = []
        self.session.use_signins(lambda: ("example.com",), lambda: (self.strict.append("strict"),
                                                                    self.browser.order.append("strict")))

    def call(self) -> web.Call:
        return web.Call(lambda: False, 45.0)

    def test_a_listed_page_opens_signed_in_and_the_run_goes_strict_before_it_is_read(self) -> None:
        opened = self.session.open_page(INBOX, self.call())
        self.assertEqual(self.browser.signed_loads, [INBOX])
        self.assertEqual(opened.signed_in, "mail.example.com")
        self.assertEqual(self.strict, ["strict"])
        self.assertEqual(self.browser.order[:2], ["strict", "read"])

    def test_other_pages_and_searches_never_ask_for_it(self) -> None:
        opened = self.session.open_page(OTHER, self.call())
        self.assertEqual(opened.signed_in, "")
        self.assertEqual(self.browser.signed_loads, [])
        self.assertEqual(self.strict, [])
        self.assertEqual(self.session.signed_in_host("https://example.com.evil.net/"), "")
        bare = web.WebSession(self.browser)                       # no list given: never
        self.assertEqual(bare.signed_in_host(INBOX), "")

    def test_a_page_whose_care_fails_is_not_read(self) -> None:
        def broken() -> None:
            raise RuntimeError("no")

        self.session.use_signins(lambda: ("example.com",), broken)
        with self.assertRaises(web.WebRefused) as caught:
            self.session.open_page(INBOX, self.call())
        self.assertEqual(caught.exception.code, web.W_SIGNED_IN)
        self.assertNotIn("read", self.browser.order)

    def test_live_s_note_names_the_host_only_and_the_model_is_told_the_rules(self) -> None:
        opened = web.Opened("Inbox", INBOX, "Your mail.", 1, 10, 10, signed_in="mail.example.com")
        result = tools._opened_result(opened, "a web page", "10 characters, 0 link(s)", ())
        self.assertEqual(result.note, "signed in as you on mail.example.com; 10 characters, 0 link(s)")
        self.assertTrue(result.text.startswith(tools.SIGNED_IN_TEXT))
        self.assertNotIn("/inbox", result.note)


FORM = "https://mail.example.com/compose"


class SignedInDriveBrowser(FakeDriveBrowser):
    def load_signed_in(self, url: str, *, timeout_s: float, cancelled: Any) -> snapshots.ShotResult:
        result = self.load(url, timeout_s=timeout_s, cancelled=cancelled)
        return snapshots.ShotResult(True, final_url=result.final_url, signed_in=True) if result.ok else result


class DriveTests(DriveCase):
    def setUp(self) -> None:
        super().setUp()
        page = FakePage(title="Compose", text="Write a message.", final=FORM,
                        forms=[("post", "https://mail.example.com/send")],
                        controls=[FakeControl("button", "Show more"), FakeControl("link", "Sign out",
                                                                                   href="https://mail.example.com/out"),
                                  FakeControl("link", "Older", href="https://mail.example.com/older"),
                                  FakeControl("submit", "Send", form=1)])
        self.browser = SignedInDriveBrowser({FORM: page, "https://mail.example.com/older":
                                             FakePage(title="Older", text="Older mail.",
                                                      final="https://mail.example.com/older")})
        self.session = drive.DriveSession(self.browser, caps=drive.DriveCaps())
        self.session.use_signins(lambda: ("example.com",), lambda: None)
        self.context.web = self.session

    def test_signed_in_every_button_goes_to_the_send_card(self) -> None:
        self.opened(FORM)
        self.listed()
        result = self.call("click", ref="e1")                       # an ordinary button, free on any other page
        self.assertEqual(result.code, "waiting")
        self.assertEqual(self.browser.clicks, [])
        self.assertEqual(len(self.plans), 1)

    def test_signed_in_a_link_that_would_sign_out_is_refused_and_another_opens(self) -> None:
        self.opened(FORM)
        self.listed()
        result = self.call("click", ref="e2")
        self.assertTrue(result.is_error)
        self.assertEqual(result.code, drive.D_SIGNED_IN)
        opened = self.call("click", ref="e3")
        self.assertFalse(opened.is_error, opened.text)
        self.assertIn("signed in as you on mail.example.com", opened.note)


# --------------------------------------------------------------------------
# The lender: lend, give back, remove, forget
# --------------------------------------------------------------------------

class LenderTests(unittest.TestCase):
    def test_lent_to_one_holder_at_a_time_and_refused_meanwhile(self) -> None:
        made = Lender(self)
        holder, other = QObject(), QObject()
        profile = made.profile.lend(holder)
        self.assertIsInstance(profile, FakeProfile)
        self.assertEqual(profile.interceptor, BLOCK_ALL)            # set before anyone uses it
        self.assertIsNone(made.profile.lend(other))
        self.assertEqual(made.profile.refusal, site_signins_ui.R_BUSY)
        made.profile.give_back(other)                               # not the holder: nothing
        self.assertIs(made.profile.held_by(), holder)
        profile.interceptor = "the page's judge"
        made.profile.give_back(holder)
        self.assertIsNone(made.profile.held_by())
        self.assertEqual(profile.interceptor, BLOCK_ALL)            # every request refused until the next lend
        self.assertIs(made.profile.lend(other), profile)            # one profile in the process
        self.assertEqual(len(made.made), 1)

    def test_a_holder_that_goes_releases_it(self) -> None:
        made = Lender(self)
        holder = QObject()
        made.profile.lend(holder)
        holder.deleteLater()
        _spin()
        self.assertIsNone(made.profile.held_by())

    def test_no_sites_or_no_engine_lends_nothing_and_makes_nothing(self) -> None:
        empty = Lender(self, hosts=())
        self.assertIsNone(empty.profile.lend(QObject()))
        self.assertEqual(empty.profile.refusal, site_signins_ui.R_NO_SITES)
        broken = Lender(self, ready=snapshots.R_NO_ENGINE)
        self.assertIsNone(broken.profile.lend(QObject()))
        self.assertEqual(broken.profile.refusal, site_signins_ui.R_ENGINE)
        self.assertEqual(empty.made + broken.made, [])
        self.assertFalse((empty.data / FOLDER).exists())

    def test_remove_deletes_that_site_s_cookies_and_its_subdomains_only(self) -> None:
        cookies = [FakeCookie(".example.com"), FakeCookie("mail.example.com"), FakeCookie(".other.org"),
                   FakeCookie("example.com.evil.net"), FakeCookie(".accounts.idp.net")]
        made = Lender(self, hosts=("example.com", "other.org"), cookies=cookies)
        holder = QObject()
        made.profile.lend(holder)
        made.profile.give_back(holder)
        made.profile.remove_site("example.com")
        self.assertEqual(made.store.hosts(), ("other.org",))
        store = made.made[0].store
        self.assertEqual(store.deleted, [".example.com", "mail.example.com"])
        self.assertEqual([cookie.domain() for cookie in store.cookies],
                         [".other.org", "example.com.evil.net", ".accounts.idp.net"])

    def test_remove_before_the_profile_is_made_deletes_at_its_next_making(self) -> None:
        data = _folder(self)
        (data / FOLDER).mkdir()
        cookies = [FakeCookie(".example.com"), FakeCookie(".other.org")]
        made = Lender(self, hosts=("example.com", "other.org"), cookies=cookies, data=data)
        with mock.patch.object(made.profile, "_ready", lambda: snapshots.R_NO_ENGINE):
            made.profile.remove_site("example.com")                 # no engine now: it waits, in the file
        self.assertEqual(SiteList(data).removed(), ("example.com",))
        made.profile.lend(QObject())
        self.assertEqual(made.made[0].store.deleted, [".example.com"])

    def test_remove_with_nothing_kept_needs_no_profile(self) -> None:
        made = Lender(self, hosts=("example.com",))
        made.profile.remove_site("example.com")
        self.assertEqual(made.made, [])
        self.assertEqual(made.store.removed(), ())

    def test_forget_deletes_every_cookie_then_the_whole_folder(self) -> None:
        made = Lender(self, cookies=[FakeCookie(".example.com")])
        holder = QObject()
        profile = made.profile.lend(holder)
        self.assertEqual(made.profile.forget(), site_signins_ui.R_BUSY)   # held: close it first
        made.profile.give_back(holder)
        heard: list[bool] = []
        made.profile.forgotten.connect(heard.append)
        self.assertEqual(made.profile.forget(), "")
        self.assertEqual(profile.store.all_deleted, 1)
        self.assertEqual(profile.deletes, 1)
        self.assertTrue(made.profile.forgetting())
        self.assertIsNone(made.profile.lend(QObject()))
        for _ in range(20):
            _spin()
            if heard:
                break
        self.assertEqual(heard, [True])
        self.assertFalse((made.data / FOLDER).exists())
        self.assertFalse((made.data / FORGET_MARK).exists())
        self.assertEqual(made.store.hosts(), ("example.com",))      # the list stays

    def test_a_folder_still_in_use_is_deleted_at_the_next_start(self) -> None:
        made = Lender(self)
        made.profile.lend(QObject())
        made.profile.give_back(made.profile.held_by())
        heard: list[bool] = []
        made.profile.forgotten.connect(heard.append)
        real = site_signins_ui.shutil.rmtree
        with mock.patch.object(site_signins_ui.shutil, "rmtree", side_effect=OSError("in use")):
            made.profile.forget()
            for _ in range(40):
                _spin()
                if heard:
                    break
        self.assertEqual(heard, [False])
        self.assertTrue((made.data / FORGET_MARK).exists())
        self.assertTrue((made.data / FOLDER).exists())
        self.assertTrue(callable(real))
        again = site_signins_ui.SignInProfile(made.data, SiteList(made.data), make_profile=lambda *_a: None,
                                              ready=lambda: "")
        self.addCleanup(again.deleteLater)
        self.assertFalse((made.data / FOLDER).exists())             # before any profile of the new run
        self.assertFalse((made.data / FORGET_MARK).exists())


# --------------------------------------------------------------------------
# Research pictures, the board and snapshots never get it
# --------------------------------------------------------------------------

class NeverElsewhereTests(unittest.TestCase):
    def test_a_page_picture_s_camera_always_makes_a_fresh_off_the_record_profile(self) -> None:
        camera = snapshot_engine.PageCamera()
        self.addCleanup(camera.deleteLater)
        shot = _shot()
        self.assertIsInstance(camera._new_profile(shot, _classes()), OffTheRecord)
        self.assertFalse(shot.lent)
        self.assertFalse(hasattr(camera, "_signins"))

    def test_a_lent_profile_is_never_deleted_by_the_camera(self) -> None:
        camera = snapshot_engine.PageCamera()
        self.addCleanup(camera.deleteLater)
        shot = _shot()
        shot.lent = True
        shot.profile = FakeProfile("x")
        camera._teardown(shot)
        self.assertEqual(camera._doomed, [])

    def test_only_the_agent_s_window_and_the_owner_s_window_take_the_profile(self) -> None:
        root = PROJECT_ROOT / "briefing_reader"
        users = sorted(str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*.py")
                       if "signin_profile(" in path.read_text(encoding="utf-8") or
                       "lend(self)" in path.read_text(encoding="utf-8"))
        self.assertEqual(users, ["agent/browser.py", "site_signins_ui.py", "snapshot_engine.py"])
        board = (root / "board" / "view.py").read_text(encoding="utf-8")
        self.assertIn("private_profile(", board)
        self.assertNotIn("signin", board)
        engine = (root / "snapshot_engine.py").read_text(encoding="utf-8")
        self.assertIn("else PageCamera(self)", engine)               # LIVE's research pictures: the plain camera

    def test_the_board_s_profile_stays_off_the_record_with_no_storage_name(self) -> None:
        made: list[Any] = []

        class Profile:
            class HttpCacheType:
                NoCache = "none"

            class PersistentCookiesPolicy:
                NoPersistentCookies = "no"

            class PersistentPermissionsPolicy:
                AskEveryTime = "ask"

            def __init__(self, *args: Any) -> None:
                made.append(args)

            def isOffTheRecord(self) -> bool:  # noqa: N802
                return True

            def __getattr__(self, _name: str) -> Any:
                return lambda *_args: None

        with mock.patch.object(snapshot_engine, "_classes", lambda: SimpleNamespace(QWebEngineProfile=Profile)), \
                mock.patch.object(snapshot_engine._ENGINE, "started", True):
            self.assertIsNotNone(snapshot_engine.private_profile("parent"))
        self.assertEqual(made, [("parent",)])                         # no storage name: never the sign-in one


# --------------------------------------------------------------------------
# Settings > LIVE and privacy
# --------------------------------------------------------------------------

class FakeWindow(QObject):
    closed = Signal()

    def __init__(self, host: str, words: str = "") -> None:
        super().__init__()
        self.host = host
        self.words = words
        self.shown = 0

    def start(self) -> str:
        return self.words

    def show(self) -> None:
        self.shown += 1

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:  # noqa: N802
        pass

    def close(self) -> None:
        self.closed.emit()


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.made = Lender(self, hosts=())
        self.panel = site_signins_ui.SitesPanel()
        self.addCleanup(self.panel.deleteLater)
        self.windows: list[FakeWindow] = []
        self.window_words = ""

        def factory(_lender: Any, host: str) -> FakeWindow:
            window = FakeWindow(host, self.window_words)
            self.windows.append(window)
            return window

        self.control = site_signins_ui.SitesControl(self.made.profile, self.panel, window_factory=factory)
        self.addCleanup(self.control.deleteLater)

    def test_empty_by_default_with_the_owner_s_words(self) -> None:
        self.assertEqual(self.panel.site_rows(), [])
        self.assertFalse(self.panel.empty.isHidden())
        self.assertEqual(self.panel.title.text(), site_signins_ui.TITLE)
        self.assertIn("nothing is taken from your own web browser or any other", self.panel.intro.text())

    def test_add_validates_and_lists_a_row_with_sign_in_and_remove(self) -> None:
        self.panel.field.setText("example.com/inbox")
        self.panel.add_button.click()
        self.assertEqual(self.panel.site_rows(), [])
        self.assertEqual(self.panel.note.text(), site_signins.P_SHAPE)
        self.assertEqual(self.panel.field.text(), "example.com/inbox")     # kept to fix
        self.panel.field.setText("Example.com")
        self.panel.add_button.click()
        self.assertEqual(self.panel.site_rows(), [("example.com", "Sign in…", "Remove")])
        self.assertEqual(self.panel.field.text(), "")
        self.assertTrue(self.panel.empty.isHidden())
        self.assertEqual(SiteList(self.made.data).hosts(), ("example.com",))

    def test_sign_in_opens_jarvis_s_window_once_and_says_why_not(self) -> None:
        self.control.add("example.com")
        sign_in, _remove = self.panel.row_buttons("example.com")
        sign_in.click()
        self.assertEqual(len(self.windows), 1)
        self.assertEqual(self.windows[0].shown, 1)
        self.assertEqual(self.panel.note.text(), site_signins_ui.WINDOW_NOTE)
        self.assertFalse(self.panel.row_buttons("example.com")[0].isEnabled())
        self.control.sign_in("example.com")                    # brought forward, no second window
        self.assertEqual(len(self.windows), 1)
        self.windows[0].close()
        self.assertIsNone(self.control.window)
        self.assertTrue(self.panel.row_buttons("example.com")[0].isEnabled())
        self.window_words = site_signins_ui.BUSY_WORDS
        self.control.sign_in("example.com")
        self.assertIsNone(self.control.window)
        self.assertEqual(self.panel.note.text(), site_signins_ui.BUSY_WORDS)

    def test_remove_takes_the_row_away_and_closes_its_window(self) -> None:
        self.control.add("example.com")
        self.control.add("other.org")
        self.control.sign_in("example.com")
        self.panel.row_buttons("example.com")[1].click()
        self.assertIsNone(self.control.window)
        self.assertEqual([row[0] for row in self.panel.site_rows()], ["other.org"])
        self.assertEqual(self.panel.note.text(), site_signins_ui.REMOVED_NOTE.format(host="example.com"))

    def test_forget_takes_two_clicks(self) -> None:
        self.control.add("example.com")
        self.panel.forget_button.click()
        self.assertEqual(self.panel.note.text(), site_signins_ui.FORGET_CONFIRM)
        self.assertFalse(self.made.profile.forgetting())
        heard: list[bool] = []
        self.made.profile.forgotten.connect(heard.append)
        self.panel.forget_button.click()
        for _ in range(20):
            _spin()
            if heard:
                break
        self.assertEqual(heard, [True])
        self.assertEqual(self.panel.note.text(), site_signins_ui.FORGOT_NOTE)
        self.assertEqual([row[0] for row in self.panel.site_rows()], ["example.com"])

    def test_the_window_opens_no_site_in_a_test_run(self) -> None:
        self.control.add("example.com")
        window = site_signins_ui.SignInWindow(self.made.profile, "example.com")
        self.addCleanup(window.deleteLater)
        self.assertEqual(window.start(), site_signins_ui.TEST_RUN_WORDS)
        self.assertIsNone(self.made.profile.held_by())
        self.assertEqual(self.made.made, [])
        self.assertEqual(window.windowTitle(), "Sign in to example.com - Jarvis")


# --------------------------------------------------------------------------
# The real engine, offscreen and offline, in a process of its own
# --------------------------------------------------------------------------

_REAL = r'''
import json, os, sys, time
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from briefing_reader import snapshot_engine
snapshot_engine.prepare_application()
from PySide6.QtWidgets import QApplication
app = QApplication(["probe", "-platform", "offscreen"])
from briefing_reader import site_signins, site_signins_ui
data = sys.argv[1]
store = site_signins.SiteList(data)
store.add("example.com")
lender = site_signins_ui.SignInProfile(data, store)
holder = QObject()
profile = lender.lend(holder)
report = {"lent": profile is not None}
if profile is not None:
    folder = os.path.join(data, "site-signins")
    report.update(off_record=profile.isOffTheRecord(),
                  path_ok=os.path.normcase(os.path.abspath(profile.persistentStoragePath())) ==
                  os.path.normcase(os.path.abspath(folder)),
                  cache=profile.httpCacheType().name, cookies=profile.persistentCookiesPolicy().name,
                  name=profile.storageName(), refused=profile is not None)
lender.give_back(holder)
report["held_after"] = lender.held_by() is not None
window = site_signins_ui.SignInWindow(lender, "example.com")
report["window"] = window.start()
heard = []
lender.forgotten.connect(heard.append)
report["forget"] = lender.forget()
deadline = time.monotonic() + 20
while not heard and time.monotonic() < deadline:
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    time.sleep(0.02)
report["forgot"] = heard
report["folder_left"] = os.path.exists(os.path.join(data, "site-signins"))
print(json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


class RealEngineTests(unittest.TestCase):
    @unittest.skipUnless(snapshot_engine.webengine_available(), "no QtWebEngine 6.8+ here")
    def test_the_sign_in_profile_is_persistent_in_its_own_folder_and_forget_deletes_it(self) -> None:
        data = _folder(self)
        env = {key: value for key, value in os.environ.items() if key not in ("QTWEBENGINE_DISABLE_SANDBOX",
                                                                              "QTWEBENGINE_CHROMIUM_FLAGS")}
        env.update(QT_QPA_PLATFORM="offscreen", JARVIS_DESKTOP_TEST_GUARD="1", PYTHONIOENCODING="utf-8")
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
        done = subprocess.run([sys.executable, "-B", "-c", _REAL, str(data)], cwd=PROJECT_ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=170)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stderr[-2000:])
        report = json.loads(lines[-1])
        self.assertTrue(report["lent"], report)
        self.assertFalse(report["off_record"])
        self.assertTrue(report["path_ok"])
        self.assertEqual(report["cache"], "MemoryHttpCache")
        self.assertEqual(report["cookies"], "ForcePersistentCookies")
        self.assertEqual(report["name"], snapshot_engine.SIGNIN_STORAGE)
        self.assertFalse(report["held_after"])
        self.assertEqual(report["window"], site_signins_ui.TEST_RUN_WORDS)   # no site opens offscreen
        self.assertEqual(report["forget"], "")
        self.assertIn(report["forgot"], ([True], [False]))
        if report["forgot"] == [True]:
            self.assertFalse(report["folder_left"])
        else:
            self.assertTrue((data / FORGET_MARK).exists())


if __name__ == "__main__":
    unittest.main()
