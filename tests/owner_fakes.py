"""Fakes for part A4's tests of the owner's things: invented mail, calendar, notes and files, the real desktop service
over a pretend disk in a temporary folder (tests.desktop_fakes), the real validators, the real firewall. Nothing real is
read: every name below is made up, and no OS call or network is made.

    OwnerBench(test)    one run's tools without the endpoint: ``call(name, **args)`` goes through the gate and the
                        registry as a real call does; ``cards``: what the firewall asked the owner; ``allow()``,
                        ``decline()``
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import TestCase

from briefing_reader.agent import firewall as fw
from briefing_reader.agent import owner, policy, tools, web
from briefing_reader.ask.context import AccountInfo
from briefing_reader.conversation import ROLE_JARVIS, ROLE_YOU, Turn
from briefing_reader.desktop.service import DesktopService
from briefing_reader.desktop.worker import DesktopWorker
from briefing_reader.gcal import EventBrief, Guest
from tests.agent_fakes import FakeBrowser
from tests.ask_fakes import FakeReader, FakeSources, gmail_message, gmail_thread
from tests.desktop_fakes import DOWNLOADS, FakePc, FakeRecycleBin, desktop_disk, pretend_path

ZONE = timezone(timedelta(hours=-7))
NOW = datetime(2026, 11, 5, 9, 30, tzinfo=ZONE)
EVENT_ID, THREAD_ID, CAL = "evt0000001", "thr0000777", "primary"
SECRET_WORD = "Zephyrine"                  # a private name: in the calendar
SUBJECT_WORD = "Orionhold"                 # a private subject word: in mail
BODY_WORD = "Quillfeather"                 # a word only a mail's body holds
NOTE_WORD = "Larkspurring"                 # a note's
FILE_WORD = "Bramblemoor"                  # a file's name
CONTENT_WORD = "Tamarindus"                # only a file's contents hold it
SAID_WORD = "Peregrinate"                  # only the earlier conversation holds it
GUEST = "Priya Zephyrine"
GUEST_ADDRESS = "priya@example.edu"
ATTACKER = "mallory@evil.example"
LETTER = f"{DOWNLOADS}\\{FILE_WORD} letter.txt"
PROJECT_DIR = pretend_path("OneDrive", "Desktop", "Projects", "Jarvis 1.0")
DATA_DIR = pretend_path("JarvisData")
ALL_CONNECTED = frozenset({"calendar", "contacts", "memory", "mail", "files", "desk", "actions", "conversation"})


def invented_event(**changes: Any) -> EventBrief:
    values = {"event_id": EVENT_ID, "calendar_id": CAL, "title": f"Dinner with {SECRET_WORD}",
              "start": NOW + timedelta(days=1), "end": NOW + timedelta(days=1, hours=1), "organizer_self": False,
              "organizer_email": GUEST_ADDRESS, "organizer_name": GUEST,
              "guests": (Guest(GUEST_ADDRESS, GUEST, "accepted"),)}
    return EventBrief(**{**values, **changes})


def invented_thread(body: str = f"The {BODY_WORD} figures are 42. Call me.", *, thread_id: str = THREAD_ID,
                    sender: str = f"{GUEST} <{GUEST_ADDRESS}>", subject: str = f"{SUBJECT_WORD} budget") -> dict:
    return gmail_thread(thread_id, [gmail_message(
        "msg" + thread_id[3:], sender=sender, to="me@example.com", subject=subject, body=body,
        msgid="<CAB7@mail.example.edu>", at_ms=1791200000000)])


class StubThings:
    """agent.owner.Things over the real service, a real desktop thread and invented sources."""

    def __init__(self, base: str, *, threads: dict | None = None, found: Sequence[str] = (THREAD_ID)) -> None:
        self.base = base
        self.fake = desktop_disk(base)
        self.fake.make_file(LETTER, f"Dear {SAID_WORD}. The {CONTENT_WORD} papers.\n".encode())
        self.fake.make_dir(PROJECT_DIR)
        self.fake.make_dir(DATA_DIR)
        self.config = SimpleNamespace(ask=SimpleNamespace(calendars=("primary",), days_back=1, days_ahead=14,
                                                          max_cards=8),
                                      actions=SimpleNamespace(link_hosts=()))
        threads = threads if threads is not None else {THREAD_ID: invented_thread()}
        self.reader = FakeReader("personal", threads, found=[found] if isinstance(found, str) else list(found))
        self.sources = FakeSources(
            accounts_list=[AccountInfo("personal", "me@example.com", "yes", True, "yes", "America/Los_Angeles")],
            briefs={"personal": [invented_event()]}, readers={"personal": self.reader})
        self.notes: tuple[str, ...] = (f"My sister {NOTE_WORD} prefers mornings.",)
        self.turns: tuple[Turn, ...] = (Turn(ROLE_YOU, f"remember {SAID_WORD} from Friday", NOW - timedelta(hours=1)),
                                        Turn(ROLE_JARVIS, "Noted.", NOW - timedelta(minutes=59)))
        self.pc = FakePc()
        self.journal_dir = Path(base) / "journal"
        self.bin = FakeRecycleBin(self.fake)
        self.service = DesktopService(
            folders=("documents", "downloads"), excluded=(PROJECT_DIR, DATA_DIR), backend_factory=lambda: self.fake,
            journal_folder=lambda: self.journal_dir, pc_factory=lambda: self.pc, recycle_factory=lambda: self.bin)
        self.worker = DesktopWorker()
        self.on = True
        self.shown: list[Any] = []
        self.cards: list[Any] = []
        self.opened: list[Any] = []
        self.pc_changes: list[Any] = []
        self.show_ok = True

    def consent(self) -> bool:
        return self.on

    def now(self) -> datetime:
        return NOW

    def memory(self) -> Sequence[str]:
        return self.notes

    def conversation(self) -> Sequence[Any]:
        return self.turns

    def briefing(self) -> Any:
        return None

    def job(self, method: str, *args: Any, stop: threading.Event, **kwargs: Any) -> Any:
        return self.worker.submit(getattr(self.service, method), *args, cancel=stop, **kwargs)

    def before_open(self) -> None:
        pass

    def show_run(self, run: Any) -> bool:
        self.shown.append(run)
        return self.show_ok

    def hud_windows(self) -> Sequence[int]:
        return (77,)

    def pc_done(self, asked: Any, run: Any) -> None:
        self.pc_changes.append((asked, run))

    def take_cards(self, cards: Sequence[Any]) -> bool:
        self.cards.extend(cards)
        return True


class OwnerBench:
    """One run's tools, gate and firewall, with no process and no endpoint (the whole road is tests.test_agent_things'),
    the owner's goal in the owner's own words, and a web window over FakeBrowser."""

    def __init__(self, test: TestCase, goal: str = "summarise my week", *, pages: dict | None = None,
                 things: StubThings | None = None, **kwargs: Any) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        test.addCleanup(tmp.cleanup)
        self.things = things or StubThings(tmp.name, **kwargs)
        test.addCleanup(self.things.worker.stop)
        self.browser = FakeBrowser(pages or {})
        self.session = web.WebSession(self.browser, "duckduckgo")
        self.firewall = fw.WebFirewall(self.session.book)
        self.firewall.owner_said(goal)
        self.cards: list[str] = []
        self._answers: list[Any] = []
        self.firewall.attach(self._ask)
        self.owner = owner.OwnerAccess(self.things, self.firewall, run_id="r1")
        self.registry = tools.Registry(policy.offered(SimpleNamespace(), connected=ALL_CONNECTED))
        self.waiting: list[str] = []
        self.stopped = False
        self.fetched: list[tuple[str, str]] = []
        self.ctx = tools.RunContext(run_id="r1", cancelled=lambda: self.stopped, web=self.session, tool_s=10.0,
                                    owner=self.owner,
                                    on_card=self._on_card, card_gone=self.waiting.remove, fetch=self._fetch)

    def _ask(self, text: str, _choices: Any, on_answer: Any) -> bool:
        self.cards.append(text)
        self._answers.append(on_answer)
        return True

    def _on_card(self, card_id: str) -> bool:
        if self.waiting:
            return False
        self.waiting.append(card_id)
        return True

    def _fetch(self, url: str, question: str, _call: Any) -> str:
        self.fetched.append((url, question))
        return "Fetched words."

    def call(self, name: str, /, **args: Any) -> tools.ToolResult:
        tool = self.registry.tool(name)
        assert tool is not None, name
        stopped = policy.gate(tool, args, self.firewall)
        return stopped if stopped is not None else self.registry.call(name, args, self.ctx)

    def allow(self) -> None:
        self._answers.pop()(True)

    def decline(self) -> None:
        self._answers.pop()(False)

    def owner_says(self, words: str) -> None:
        self.firewall.owner_said(words)

    def searched(self) -> list[str]:
        return [url for url in self.browser.loads if "duckduckgo" in url or "bing" in url or "google" in url]

    def loaded(self) -> list[str]:
        return list(self.browser.loads)


__all__ = ["ALL_CONNECTED", "ATTACKER", "BODY_WORD", "CONTENT_WORD", "DATA_DIR", "EVENT_ID", "FILE_WORD", "GUEST",
           "GUEST_ADDRESS", "LETTER", "NOTE_WORD", "NOW", "OwnerBench", "PROJECT_DIR", "SAID_WORD", "SECRET_WORD",
           "SUBJECT_WORD", "StubThings", "THREAD_ID", "invented_event", "invented_thread"]
