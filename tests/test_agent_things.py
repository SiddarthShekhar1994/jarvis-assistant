"""Jarvis, a true agent, part A4, whole tasks in the app: the fake engine over the real endpoint, the real controller,
the real desktop service and validators on a pretend disk, Ask's real checks and cards. Each private tool; taint and
strict through to the card; allow once; decline; each change tool end to end with its card and the outcome message; Undo
after a task's move; Stop and Turn off during each kind of wait; nothing offered without its connection or consent; and
a canary name in fake mail, calendar and files that never reaches a log line.

Qt runs offscreen and shows nothing. The real Claude Code is never started. Every value is invented; files go only to
temporary folders. Nothing real is read, opened or reached.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import agent_ui, desktop_ui, live, ui
from briefing_reader.actions import SOURCE_ASK
from briefing_reader.agent import firewall as fw
from briefing_reader.agent import owner, policy
from briefing_reader.agent import runner as ar
from briefing_reader.conversation import ROLE_YOU, Turn
from briefing_reader.desktop import cardplan, fileops
from briefing_reader.desktop import tidy as desktop_tidy
from briefing_reader.desktop import validate as dv
from tests.agent_fakes import FAST, FakeBrowser, FakeEngine, FakePage
from tests.ask_fakes import FakeReader
from tests.desktop_fakes import DOCS, DOWNLOADS, FakePc, FakeRecycleBin, FakeStopKeys, desktop_disk
from tests.owner_fakes import (BODY_WORD, CONTENT_WORD, EVENT_ID, FILE_WORD, GUEST_ADDRESS, LETTER, NOTE_WORD, NOW,
                               SAID_WORD,
                               SECRET_WORD, SUBJECT_WORD, THREAD_ID, invented_event, invented_thread)
from tests.test_agent_app import SAY, AgentApp
from tests.ui_fakes import fonts, settle, wait_for

_app: QApplication | None = None
DONE = "Done, and I checked it."
MOVED = f"{DOCS}\\{FILE_WORD} letter.txt"
CANARY = "Quetzalcoatlus"                  # invented: in fake mail, calendar, notes, file names and contents


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_agent_things", "-platform", "offscreen"])
    fonts()


class Script:
    """A task played by the fake engine, step by step. ``got``: what each named call came back with; ``told``: the
    messages that came in. It never asserts on its own thread: the test does, afterwards."""

    def __init__(self, *steps: tuple[Any, ...]) -> None:
        self.steps = steps
        self.got: dict[str, Any] = {}
        self.told: list[str] = []
        self.gate = threading.Event()

    def __call__(self, engine: FakeEngine) -> None:
        self.told.append(engine.wait_message())
        for step in self.steps:
            kind = step[0]
            if kind == "wait":                                       # its turn ends; what comes next is a message
                engine.end_turn("I'll wait.")
                self.told.append(engine.wait_message(step[1] if len(step) > 1 else 15.0))
            elif kind == "hold":                                     # the test lets it go on
                self.gate.wait(15.0)
            else:
                key, name, args = step
                args = args(self.got) if callable(args) else args
                self.got[key] = engine.call(name, **args)
        engine.call("finish", summary=DONE, outcome="done")
        engine.end_turn("Done.")

    def text(self, key: str) -> str:
        return FakeEngine.text_of(self.got[key])

    def ref(self, key: str, name: str) -> str:
        return next(row.split()[0] for row in self.text(key).splitlines() if name in row)


def text_of(result: Any) -> str:
    return FakeEngine.text_of(result)


def ref_in(result: Any, name: str) -> str:
    return next(row.split()[0] for row in text_of(result).splitlines() if name in row)


class ThingsApp(AgentApp):
    """A task in the app with the owner's things connected: a calendar, a mailbox, notes, a pretend disk."""

    GOAL = "summarise my week"

    def make_things(self, script: Any, *, readers: Any = None, agent: str = "",
                    notes: tuple[str, ...] | None = (f"Sister {NOTE_WORD}.",), **harness: Any) -> FakeEngine:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.fake = desktop_disk(tmp.name)
        self.fake.make_file(LETTER, f"Dear {SAID_WORD}. The {CONTENT_WORD} papers.\n".encode())
        self.reader = FakeReader("personal", {THREAD_ID: invented_thread()}, found=[THREAD_ID])
        engine = FakeEngine(script)
        harness.setdefault("browser", FakeBrowser())
        self.fake_browser = harness["browser"]
        harness.setdefault("desktop_consent", desktop_ui.AGENT_CONSENT_VERSION)
        harness.setdefault("timing", replace(FAST,
                                             tool_s=30.0))     # a search takes its time, even with no page to find
        self.make_agent(engine, agent=agent, desktop_backend=lambda: self.fake,
                        readers=readers if readers is not None else {"personal": self.reader}, **harness)
        soon = self.harness.clock.now() + timedelta(days=1)     # the harness's own day: Ask's window (contacts_find)
        self.harness.calendars["personal"].briefs = [
            invented_event(), invented_event(event_id="evt0000002", start=soon, end=soon + timedelta(hours=1))]
        if notes is not None:
            self.c.ask.planner.memory = lambda: notes
        self.journal = os.path.join(tmp.name, "journal")
        self.desktop.journal_folder = lambda: self.journal
        self.keys = FakeStopKeys()
        self.desktop.stop_keys = self.keys
        self.pc = FakePc()
        self.desktop.pc_factory = lambda: self.pc
        self.bin = FakeRecycleBin(self.fake)
        self.desktop.recycle_factory = lambda: self.bin
        self.desktop.task_card_arm_s = 0.0                           # a click at once (ArmedCardTests: the real wait)
        self.c.agent._things._conversation = lambda: (Turn(ROLE_YOU, f"remember {SAID_WORD}", NOW),)
        return engine

    def start(self, goal: str = "") -> None:
        """The owner's words start the task, as a first planner run hands them over (tests.test_agent_app: that way)."""
        self.assertTrue(self.c.agent.start(goal or self.GOAL))
        self.started_task()
        settle()

    def go(self, script: Any, goal: str = "", **harness: Any) -> FakeEngine:
        engine = self.make_things(script, **harness)
        self.start(goal)
        return engine

    @property
    def feed(self) -> Any:
        return self.c.task_feed

    def tidy_entry(self) -> Any:
        self.assertTrue(wait_for(lambda: self.desktop.results.tidy() is not None, timeout=10), "the tidy card shows")
        settle()
        return self.desktop.results.tidy()

    def press_tidy(self, key: str, *, wait: bool = True) -> None:
        QTest.mouseClick(self.tidy_entry().window.buttons()[key], Qt.MouseButton.LeftButton)
        settle()
        if wait:
            self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10), "the card's job ended")
            settle()

    def press_card(self, key: str) -> None:
        self.feed._card_at -= 5
        QTest.mouseClick(self.feed.card.buttons()[key], Qt.MouseButton.LeftButton)
        settle()

    def wait_card(self) -> Any:
        self.assertTrue(wait_for(lambda: self.feed.card is not None and self.feed.waits(), timeout=10), "a card waits")
        settle()
        return self.feed.card

    def wait_waiting(self) -> None:
        self.assertTrue(wait_for(lambda: self.feed.waits() == 1, timeout=10), "the panel waits")
        settle()

    def on_disk(self, path: str) -> bool:
        return os.path.lexists(self.fake.real(path))

    def ask_cards(self) -> list[Any]:
        return list(self.c._source_lists[SOURCE_ASK])

    def allowed(self) -> str:
        argv = self.runs()[-1].argv
        return argv[argv.index("--allowedTools") + 1]


class EachPrivateToolTests(ThingsApp):
    GOAL = f"read the {SUBJECT_WORD} mail and my {FILE_WORD} letter"

    def test_all_of_them_over_the_real_endpoint(self) -> None:
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("contacts", "contacts_find", {"name": "Priya"}), ("memory", "memory_read", {}),
                        ("said", "conversation_read", {}), ("search", "mail_search", {"words": SUBJECT_WORD}),
                        ("read", "mail_read", {"thread": THREAD_ID}), ("find", "files_find", {"words": FILE_WORD}),
                        ("roots", "files_list", {}),
                        ("file", "file_read", lambda got: {"ref": ref_in(got["find"], "letter.txt")}))
        self.go(script)
        end = self.ended()
        self.assertEqual((end.outcome, end.code), ("done", "finished"))
        for key in script.got:
            self.assertFalse(script.got[key].get("isError"), key)
        self.assertIn(f"event={EVENT_ID}", script.text("calendar"))
        self.assertIn(GUEST_ADDRESS, script.text("contacts"))
        self.assertIn(NOTE_WORD, script.text("memory"))
        self.assertIn(SAID_WORD, script.text("said"))
        self.assertNotIn(BODY_WORD, script.text("search"))
        self.assertIn(BODY_WORD, script.text("read"))
        self.assertIn(CONTENT_WORD, script.text("file"))
        for key in script.got:
            self.assertIn("<untrusted", script.text(key), key)
        self.assertEqual(set(self.fake.threads), {"desktop"})
        task = self.agent_tasks()[-1]
        titles = [step.title for step in task.steps]
        self.assertIn("Looked at your calendar", titles)
        self.assertIn("Read an email", titles)
        self.assertIn("Read one of your files", titles)

    def test_the_first_message_holds_the_owners_words_only(self) -> None:
        script = Script()
        self.go(script)
        self.ended()
        self.assertIn(self.GOAL, script.told[0])
        self.assertNotIn(SECRET_WORD, script.told[0])
        self.assertNotIn(SAY, script.told[0])


class FirewallThroughToTheCardTests(ThingsApp):
    def test_a_private_word_goes_to_the_card_and_allow_lets_that_one_call_go(self) -> None:
        pages_query = f"{SECRET_WORD} shop"
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": pages_query}), ("wait",),
                        ("again", "web_search", {"query": pages_query}),
                        ("other", "web_search", {"query": f"{GUEST_ADDRESS} shop"}), ("wait",))
        self.go(script)
        card = self.wait_card()
        self.assertEqual(card.task().kicker, "Needs your OK")
        self.assertIn(pages_query, card.task().body)
        self.assertEqual(self.fake_browser.loads, [])
        self.assertIn("End your turn", script.text("asked"))
        self.press_card("choice:0")                                  # Allow, by a click
        self.assertTrue(wait_for(lambda: len(script.told) >= 2 and "other" in script.got, timeout=10), "it went on")
        self.assertIn("allowed that one web call", script.told[1])
        self.assertTrue(self.fake_browser.loads)
        self.assertIn(pages_query.split()[0], self.fake_browser.loads[0])
        card = self.wait_card()
        self.assertIn(GUEST_ADDRESS, card.task().body)               # a word cleared; the address not
        self.press_card("choice:1")                                  # Don't
        self.ended()
        self.assertIn("said no", script.told[2])

    def test_typing_never_allows(self) -> None:
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script)
        self.wait_card()
        self.type_it("Allow")
        settle()
        self.assertEqual(self.fake_browser.loads, [])
        self.assertTrue(self.feed.waits())
        self.c.agent.stop("test")
        self.ended()

    def test_strict_every_foreign_word_asks_and_a_seen_address_goes(self) -> None:
        site = "https://shop.example.org/deals"
        browser = FakeBrowser({site: FakePage("A shop", "Deals.")})
        script = Script(("page", "open_page", {"url": site}), ("search", "mail_search", {"words": SUBJECT_WORD}),
                        ("read", "mail_read", {"thread": THREAD_ID}),
                        ("seen", "open_page", {"url": site}),
                        ("unseen", "open_page", {"url": site + "x"}), ("wait",),
                        ("ok", "web_search", {"query": f"the {SUBJECT_WORD} mail"}),
                        ("asked", "web_search", {"query": f"{SUBJECT_WORD} Dell"}), ("wait",))
        self.go(script, goal=f"read the {SUBJECT_WORD} mail and {site}", browser=browser)
        card = self.wait_card()
        self.assertIn(site + "x", card.task().body)
        self.assertEqual(browser.loads, [site, site])
        self.press_card("choice:1")
        card = self.wait_card()
        self.assertIn("Dell", card.task().body)
        self.assertTrue(any(SUBJECT_WORD in url for url in browser.loads))
        self.press_card("choice:1")
        self.ended()
        self.assertIn("said no", script.told[-1])

    def test_a_card_that_times_out_is_said_plainly(self) -> None:
        timing = ar.Timing(poll_s=0.01, connect_s=2.0, init_s=10.0, question_s=30.0, keep_going_s=30.0, web_card_s=0.3,
                           wrap_up_s=5.0, after_finish_s=2.0, replay_s=0.5, tool_s=5.0, resume_margin_s=0.0)
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script, timing=timing)
        self.ended()
        self.assertIn("did not answer", script.told[-1])
        self.assertEqual(self.fake_browser.loads, [])


class ProposeFilesTests(ThingsApp):
    GOAL = "move my letter from Downloads into Documents"

    def steps(self) -> Callable[[dict], dict]:
        def make(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "why": "as asked", "files": [ref_in(got["down"], "letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}
        return make

    def script(self, *then: tuple[Any, ...]) -> Script:
        return Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                      ("propose", "propose_files", self.steps()), ("wait",), *then)

    def test_the_owners_click_moves_it_and_the_outcome_comes_as_a_message(self) -> None:
        script = self.script()
        self.go(script)
        self.tidy_entry()
        self.wait_waiting()
        self.assertTrue(self.on_disk(LETTER))
        self.assertIn("End your turn", script.text("propose"))
        self.assertIn(self.feed.panel().content().head.split(" - ")[0], ("WAITING",))
        self.assertEqual(self.desktop.waiting_count(), 1)            # the card counts itself, once
        self.assertIsNone(self.feed.card)                            # no second card of the feed's own
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertFalse(self.on_disk(LETTER))
        self.assertTrue(self.on_disk(MOVED))
        self.assertIn("Done 1 of 1", script.told[-1])
        self.assertNotIn(FILE_WORD, script.told[-1])
        self.assertEqual(self.desktop.waiting_count(), 0)

    def test_undo_after_a_tasks_move(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertTrue(self.on_disk(MOVED))
        self.press_tidy(desktop_tidy.UNDO_KEY)
        self.assertTrue(self.on_disk(LETTER))
        self.assertFalse(self.on_disk(MOVED))

    def test_the_undo_route_finds_a_tasks_move(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.desktop._set_tidy(None)                                 # the card is gone: the words bring it back
        self.feed._dismiss()
        self.assertTrue(self.desktop.request("undo"))
        self.assertTrue(wait_for(lambda: self.desktop.tidy() is not None, timeout=10), "its stamped card")
        settle()
        self.press_tidy(desktop_tidy.UNDO_KEY)
        self.assertTrue(self.on_disk(LETTER))

    def test_not_now(self) -> None:
        script = self.script()
        self.go(script)
        self.tidy_entry()
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.ended()
        self.assertTrue(self.on_disk(LETTER))
        self.assertIn("Not now", script.told[-1])
        self.assertIn("nothing was changed", script.told[-1])
        self.assertIsNone(self.desktop.tidy())

    def test_a_card_that_times_out(self) -> None:
        script = self.script()
        self.go(script)
        self.tidy_entry()
        state = self.desktop.tidy()
        real = cardplan.CardPlan.verify
        cardplan.CardPlan.verify = lambda _self, *_a, **_k: cardplan.CARD_EXPIRED
        self.addCleanup(setattr, cardplan.CardPlan, "verify", real)
        self.desktop._on_card_expiry()
        self.ended()
        self.assertEqual(state.stage, desktop_tidy.REFUSED)
        self.assertIn("timed out", script.told[-1])
        self.assertTrue(self.on_disk(LETTER))

    def test_past_the_runs_own_time_the_card_goes_too(self) -> None:
        script = self.script()
        self.go(script, timing=replace(FAST, tool_s=30.0, card_s=0.5))
        self.ended()
        self.assertIn("nobody clicked", script.told[-1])
        self.assertIsNone(self.desktop.tidy())
        self.assertTrue(self.on_disk(LETTER))

    def test_a_plan_changed_after_it_was_frozen_runs_nothing(self) -> None:
        script = self.script()
        self.go(script)
        self.tidy_entry()
        real = cardplan.CardPlan.verify
        cardplan.CardPlan.verify = lambda _self, *_a, **_k: cardplan.CARD_CHANGED
        self.addCleanup(setattr, cardplan.CardPlan, "verify", real)
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertTrue(self.on_disk(LETTER))
        self.assertIn("expired or changed", script.told[-1])

    def test_a_file_changed_after_the_card_is_left_as_it_is(self) -> None:
        script = self.script()
        self.go(script)
        self.tidy_entry()
        with open(self.fake.real(LETTER), "ab") as handle:
            handle.write(b"more words")
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertTrue(self.on_disk(LETTER))
        self.assertIn("Done 0 of 1", script.told[-1])

    def test_a_refused_plan_says_why_at_once_and_no_card_shows(self) -> None:
        def recycle(got: dict) -> dict:
            files = [ref_in(got["down"], "letter.txt")]
            return {"steps": [{"skill": "recycle_files", "why": "the owner says delete", "files": files}]}

        script = Script(("down", "files_list", {"place": "Downloads"}), ("propose", "propose_files", recycle))
        self.go(script)
        self.ended()
        self.assertTrue(script.got["propose"]["isError"])
        self.assertIn("The owner didn't ask for that", script.text("propose"))
        self.assertIsNone(self.desktop.tidy())
        self.assertEqual(self.feed.waits(), 0)

    def test_the_bin_on_the_owners_own_words(self) -> None:
        def recycle(got: dict) -> dict:
            return {"steps": [{"skill": "recycle_files", "files": [ref_in(got["down"], "letter.txt")]}]}

        script = Script(("down", "files_list", {"place": "Downloads"}), ("propose", "propose_files", recycle), ("wait",
                                                                                                                ))
        self.go(script, goal="delete my letter from Downloads")
        self.tidy_entry()
        self.assertTrue(self.on_disk(LETTER))
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertFalse(self.on_disk(LETTER))
        self.assertIn("Done 1 of 1", script.told[-1])
        self.assertTrue(self.bin.binned)

    def test_finish_waits_for_a_card(self) -> None:
        script = self.script()

        def play(engine: FakeEngine) -> None:
            script.told.append(engine.wait_message())
            down = engine.call("files_list", place="Downloads")
            roots = engine.call("files_list")
            script.got.update(down=down, roots=roots)
            script.got["propose"] = engine.call("propose_files", **self.steps()(script.got))
            script.got["early"] = engine.call("finish", summary="All moved!", outcome="done")
            engine.end_turn("I'll wait.")
            script.told.append(engine.wait_message())
            engine.call("finish", summary=DONE, outcome="done")
            engine.end_turn("Done.")

        self.go(play)
        self.tidy_entry()
        self.assertTrue(wait_for(lambda: "early" in script.got, timeout=10))
        self.assertIn("card waits", script.text("early"))
        self.assertIsNone(self.c.agent.last_end)
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.ended()
        self.assertEqual(self.c.agent.last_end.summary, DONE)

    def test_its_card_stays_clear_of_the_panel_and_the_result_card(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        self.wait_waiting()
        self.assertFalse(self.tidy_entry().window.geometry().intersects(self.feed.panel().geometry()))
        self.assertFalse(self.tidy_entry().window.geometry().intersects(self.hud().occupied_rect()))
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        settle()
        box = self.tidy_entry().window.geometry()
        self.assertFalse(box.intersects(self.feed.panel().geometry()))
        self.assertFalse(box.intersects(self.feed.card.geometry()))
        self.assertFalse(box.intersects(self.hud().occupied_rect()))

    def test_only_one_card_waits_at_a_time(self) -> None:
        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            down = engine.call("files_list", place="Downloads")
            roots = engine.call("files_list")
            steps = self.steps()({"down": down, "roots": roots})
            self.first = engine.call("propose_files", **steps)
            self.second = engine.call("propose_files", **steps)
            engine.end_turn("I'll wait.")
            engine.wait_message()
            engine.call("finish", summary=DONE, outcome="done")
            engine.end_turn("Done.")

        self.go(play)
        self.tidy_entry()
        self.assertTrue(wait_for(lambda: hasattr(self, "second"), timeout=10))
        self.assertIn("A card waits", text_of(self.second))
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.ended()

    def test_the_tasks_card_keeps_the_desktops_own_guards(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        blocked = {"id": ""}
        self.desktop._click_guard = lambda click_id: blocked.update(id=click_id) or True
        self.press_tidy(desktop_tidy.GO_KEY)
        self.assertTrue(blocked["id"].startswith("dsk:"))
        self.assertTrue(self.on_disk(LETTER))                        # the guard said no: nothing ran
        self.c.agent.stop("test")
        self.ended()


class ArmedCardTests(ThingsApp):
    """A task's card can come at any moment, under the pointer."""

    GOAL = ProposeFilesTests.GOAL
    steps = ProposeFilesTests.steps
    script = ProposeFilesTests.script

    def test_a_click_right_after_it_comes_up_is_ignored(self) -> None:
        script = self.script()
        self.go(script)
        self.desktop.task_card_arm_s = 60.0
        self.tidy_entry()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.assertTrue(self.on_disk(LETTER))
        self.assertIsNotNone(self.desktop.tidy())
        self.desktop.task_card_arm_s = 0.0
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertFalse(self.on_disk(LETTER))

    def test_coming_back_with_the_hud_arms_it_again(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        self.desktop.task_card_arm_s = 60.0
        self.assertTrue(self.desktop._just_up(self.desktop.tidy().card.run_id))
        self.desktop.task_card_arm_s = 0.0
        self.assertFalse(self.desktop._just_up(self.desktop.tidy().card.run_id))
        self.c.agent.stop("test")
        self.ended()

    def test_a_desktop_requests_own_cards_are_not_held_back(self) -> None:
        self.go(self.script())
        self.desktop.task_card_arm_s = 60.0
        self.assertFalse(self.desktop._just_up("d7f3"))
        self.c.agent.stop("test")
        self.ended()


class ProposeActionTests(ThingsApp):
    GOAL = f"read the {SUBJECT_WORD} mail and draft an answer to Priya"
    LINE = (f"Reply: acct=personal | thread={THREAD_ID} | msgid=<CAB7@mail.example.edu> | gmid= | to={GUEST_ADDRESS} | "
            f"cc= | subject=Re: {SUBJECT_WORD} | replied=no | due= | link= | body=Thanks, Priya.")

    def test_its_card_waits_in_approvals_and_the_task_goes_on_and_finishes(self) -> None:
        script = Script(("search", "mail_search", {"words": SUBJECT_WORD}), ("read", "mail_read",
                                                                             {"thread": THREAD_ID}),
                        ("draft", "propose_action", {"lines": [self.LINE]}))
        self.go(script)
        end = self.ended()
        self.assertEqual(end.code, "finished")
        self.assertFalse(script.got["draft"].get("isError"))
        words = script.text("draft")
        self.assertIn("Nothing has been sent", words)
        self.assertIn("say in your report what is waiting", words)
        cards = self.ask_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].source, SOURCE_ASK)
        self.assertEqual(cards[0].kind, "reply")
        self.assertEqual(cards[0].error, "")
        self.assertEqual(cards[0].mail_recipients(), (GUEST_ADDRESS,))
        self.assertEqual(self.harness.senders["personal"].sent, [])

    def test_the_hud_chip_counts_it(self) -> None:
        script = Script(("todo", "propose_action",
                         {"lines": ["Todo: title=Buy stamps | due=2026-11-06 10:00 | block= | acct= | link="]}))
        self.go(script)
        self.ended()
        self.assertEqual(len(self.ask_cards()), 1)
        settle()
        self.assertIn(ui.needs_ok_chip(3), self.hud().chip_texts())   # the briefing's two, and this

    def test_a_refused_line_says_why_and_makes_no_card(self) -> None:
        script = Script(("mail", "propose_action", {"lines": [
            "Email: acct=personal | to=stranger@evil.example | cc= | subject=Hi | due= | link= | body=Hello."]}))
        self.go(script)
        self.ended()
        self.assertTrue(script.got["mail"]["isError"])
        self.assertIn("didn't find", script.text("mail"))
        self.assertEqual(self.ask_cards(), [])


class OpenThingTests(ThingsApp):
    def test_a_document_opens_at_once_on_the_owners_word(self) -> None:
        script = Script(("find", "files_find", {"words": "Budget"}),
                        ("open", "open_thing", lambda got: {"ref": ref_in(got["find"], "Budget 2026.xlsx")}))
        self.go(script, goal="open my Budget")
        self.ended()
        self.assertEqual(self.fake.opened_methods(), ["open_document"])
        self.assertIn("It is open", script.text("open"))

    def test_a_macro_document_waits_on_its_card_above_all(self) -> None:
        script = Script(("find", "files_find", {"words": "Grades"}),
                        ("open", "open_thing", lambda got: {"ref": ref_in(got["find"], "Grades macro.xlsm")}))
        self.go(script, goal="open my Grades")
        self.ended()
        self.assertEqual(self.fake.opened, [])
        self.assertIn("waits on a card", script.text("open"))
        self.assertTrue(self.desktop.results.entries())

    def test_a_suggested_site_waits_for_a_click_and_a_typed_one_opens(self) -> None:
        typed = "https://www.example.org/news"
        script = Script(("typed", "open_thing", {"site": typed}), ("other", "open_thing",
                                                                   {"site": "https://other.example.net/"}))
        self.go(script, goal=f"open {typed}")
        self.ended()
        self.assertEqual(self.fake.opened, [("open_url", typed)])
        self.assertIn("waits on a card", script.text("other"))

    def test_not_licensed_is_refused(self) -> None:
        script = Script(("find", "files_find", {"words": "Budget"}),
                        ("open", "open_thing", lambda got: {"ref": ref_in(got["find"], "Budget 2026.xlsx")}))
        self.go(script, goal="summarise the Budget")
        self.ended()
        self.assertIn("The owner didn't ask for that", script.text("open"))
        self.assertEqual(self.fake.opened, [])

    def test_a_task_output_never_goes_this_way(self) -> None:
        script = Script(("write", "write_file", {"name": "page.html", "text": "<p>Hello</p>"}), ("list", "files_find",
                                                                                                 {"words": "page"}))
        self.go(script, goal="write a page and open it")
        self.ended()
        self.assertNotIn("page.html", script.text("list"))
        self.assertEqual(self.fake.opened, [])


class PcControlTests(ThingsApp):
    def test_licensed_it_acts_with_its_chip_and_undo(self) -> None:
        script = Script(("mute", "pc_control", {"kind": "volume", "action": "set", "level": 30}))
        self.go(script, goal="set the volume to 30 and then read my notes")
        self.ended()
        self.assertEqual(self.pc.level, 30)
        changes = self.desktop.instant_changes()
        self.assertEqual([change.kind for change in changes], [dv.VOLUME])
        QTest.qWait(350)                                             # the volume's own rate cap
        self.desktop._on_undo_all()
        self.assertTrue(wait_for(lambda: self.pc.level == 50, timeout=10), "Undo all put it back")

    def test_not_licensed_is_the_plain_refusal(self) -> None:
        script = Script(("loud", "pc_control", {"kind": "volume", "action": "set", "level": 100}),
                        ("pause", "pc_control", {"kind": "media", "action": "pause"}),
                        ("shot", "pc_control", {"kind": "screenshot"}))
        self.go(script, goal="summarise my notes")
        self.ended()
        for key in script.got:
            self.assertIn("The owner didn't ask for that", script.text(key), key)
        self.assertEqual(self.pc.level, 50)
        self.assertEqual(self.desktop.instant_changes(), ())

    def test_a_screenshot_leaves_the_hud_out_and_never_comes_back_to_the_model(self) -> None:
        script = Script(("shot", "pc_control", {"kind": "screenshot"}))
        self.go(script, goal="take a screenshot")
        self.ended()
        self.assertTrue(self.pc.captures)
        self.assertEqual([block["type"] for block in script.got["shot"]["content"]], ["text"])

    def test_a_bad_shape_is_refused(self) -> None:
        script = Script(("odd", "pc_control", {"kind": "volume", "action": "set", "level": 500}))
        self.go(script, goal="set the volume")
        self.ended()
        self.assertTrue(script.got["odd"]["isError"])
        self.assertEqual(self.pc.level, 50)

    def test_a_timers_label_stays_only_in_the_owners_words(self) -> None:
        script = Script(("timer", "pc_control", {"kind": "timer", "minutes": 10, "label": "tea"}),
                        ("other", "pc_control", {"kind": "timer", "minutes": 11, "label": f"{SECRET_WORD} visits"}))
        self.go(script, goal="set a timer for tea")
        self.ended()
        labels = [timer.label for timer in self.desktop.timers.running()]
        self.assertIn("tea", labels)
        self.assertNotIn(f"{SECRET_WORD} visits", labels)


class ClickedChoicesTests(ThingsApp):
    GOAL = "tidy my Downloads"

    def test_a_choice_the_model_wrote_licenses_nothing_and_clears_nothing(self) -> None:
        script = Script(("down", "files_list", {"place": "Downloads"}),
                        ("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("ask", "ask_owner", {"question": "Shall I?", "choices": [f"Yes, delete {SECRET_WORD}", "No"]}),
                        ("wait",),
                        ("bin", "propose_files", lambda got: {"steps": [{"skill": "recycle_files",
                                                                         "files": [ref_in(got["down"],
                                                                                          "letter.txt")]}]}),
                        ("search", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script)
        self.wait_card()
        self.press_card("choice:0")
        self.assertTrue(wait_for(lambda: "search" in script.got, timeout=10), "it went on")
        self.assertIn("The owner didn't ask for that", script.text("bin"))
        self.assertIn("End your turn", script.text("search"))        # the word still taints
        self.assertEqual(self.fake_browser.loads, [])
        self.c.agent.stop("test")
        self.ended()

    def test_words_typed_do_count(self) -> None:
        script = Script(("down", "files_list", {"place": "Downloads"}),
                        ("ask", "ask_owner", {"question": "Which?", "choices": ["The old ones", "None"]}), ("wait",),
                        ("bin", "propose_files", lambda got: {"steps": [{"skill": "recycle_files",
                                                                         "files": [ref_in(got["down"],
                                                                                          "letter.txt")]}]}),
                        ("wait",))
        self.go(script)
        self.wait_card()
        self.type_it("delete the letter")
        self.tidy_entry()
        self.assertEqual(self.desktop.tidy().card.plan.rows[0].kind, fileops.RECYCLE)
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.ended()


class StopAndTurnOffTests(ThingsApp):
    """Each kind of wait: a card of file steps, a firewall's card, a question, Ask's cards, and a batch at work."""

    GOAL = "move my letter from Downloads into Documents and draft a note"
    TODO = "Todo: title=Buy stamps | due=2026-11-06 10:00 | block= | acct= | link="

    def to_card(self, *more: tuple[Any, ...]) -> Script:
        def steps(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}

        return Script(("todo", "propose_action", {"lines": [self.TODO]}), ("roots", "files_list", {}),
                      ("down", "files_list", {"place": "Downloads"}), ("propose", "propose_files", steps), ("wait",),
                      *more)

    def assert_nothing_left_of_it(self) -> None:
        self.assertIsNone(self.desktop.tidy())
        self.assertEqual(self.ask_cards(), [])
        self.assertTrue(self.on_disk(LETTER))
        self.assertEqual(self.desktop.waiting_count(), 0)

    def test_stop_while_a_file_card_waits(self) -> None:
        self.go(self.to_card())
        self.tidy_entry()
        self.assertEqual(len(self.ask_cards()), 1)
        self.c.agent.stop("stop")
        self.assert_nothing_left_of_it()                             # at once, before the stop's event
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)
        self.assert_nothing_left_of_it()
        self.assertIsNone(self.desktop.results.tidy())

    def test_turn_off_while_a_file_card_waits(self) -> None:
        self.go(self.to_card())
        self.tidy_entry()
        self.c._on_desktop_turn_off()
        self.assert_nothing_left_of_it()
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)
        self.assertFalse(self.desktop.consent_given())

    def test_a_click_after_stop_does_nothing(self) -> None:
        self.go(self.to_card())
        state = self.tidy_entry().state
        self.c.agent.stop("stop")
        self.desktop._on_tidy_click(desktop_tidy.GO_KEY)             # a click on its way
        settle()
        self.assertEqual(self.fake.asked("move_item"), [])
        self.assertEqual(state.stage, desktop_tidy.WAITING)
        self.ended()

    def test_stop_while_its_batch_is_at_work_stops_after_the_row_and_keeps_the_stamp(self) -> None:
        release = threading.Event()
        in_call = threading.Event()

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                in_call.set()
                release.wait(10.0)

        self.go(self.to_card())
        self.tidy_entry()
        self.fake.on_call = slow
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10), "the batch is at work")
        self.c.agent.stop("stop")
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10))
        settle()
        self.ended()
        state = self.desktop.tidy()
        self.assertIsNotNone(state)                                  # what was done keeps its stamp and Undo
        self.assertEqual(state.stage, desktop_tidy.DONE)
        self.assertTrue(state.undoable())
        self.fake.on_call = None
        self.press_tidy(desktop_tidy.UNDO_KEY)
        self.assertTrue(self.on_disk(LETTER))

    def test_stop_while_a_firewall_card_waits(self) -> None:
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script)
        self.wait_card()
        self.c.agent.stop("stop")
        settle()
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)
        self.assertFalse(self.feed.waits())
        self.assertEqual(self.fake_browser.loads, [])

    def test_turn_off_while_a_firewall_card_waits(self) -> None:
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script)
        self.wait_card()
        self.c._on_desktop_turn_off()
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)
        self.assertEqual(self.fake_browser.loads, [])

    def test_stop_while_a_question_waits_withdraws_its_ask_cards(self) -> None:
        script = Script(("todo", "propose_action", {"lines": [self.TODO]}), ("ask", "ask_owner",
                                                                             {"question": "Which?"}), ("wait",))
        self.go(script)
        self.wait_card()
        self.assertEqual(len(self.ask_cards()), 1)
        self.c.agent.stop("stop")
        self.assertEqual(self.ask_cards(), [])
        self.ended()

    def test_a_card_decided_before_the_stop_keeps_its_result(self) -> None:
        script = Script(("todo", "propose_action", {"lines": [self.TODO]}), ("ask", "ask_owner",
                                                                             {"question": "Which?"}), ("wait",))
        self.go(script)
        self.wait_card()
        card_id = self.ask_cards()[0].id
        real = self.c._store.is_decided
        self.c._store.is_decided = lambda action_id: action_id == card_id or real(action_id)
        self.c.agent.stop("stop")
        self.assertEqual([card.id for card in self.ask_cards()], [card_id])
        self.ended()

    def test_a_natural_end_keeps_ask_cards_and_withdraws_a_file_card_nothing_waits_on(self) -> None:
        script = Script(("todo", "propose_action", {"lines": [self.TODO]}))
        self.go(script)
        self.ended()
        self.assertEqual(len(self.ask_cards()), 1)

    def test_a_tool_after_turn_off_does_nothing(self) -> None:
        got: dict[str, Any] = {}
        hold = threading.Event()

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            hold.wait(10.0)
            try:
                got["read"] = engine.call("memory_read")
            except Exception as exc:  # noqa: BLE001 - killed with the run: that is the point
                got["killed"] = type(exc).__name__

        self.go(play)
        self.desktop.turn_off()                                      # the store says no, whatever the run thinks
        self.assertFalse(self.c.agent._owner.things.consent())
        result = self.c.agent._owner.memory_read({}, type("Ctx", (), {"cancelled": staticmethod(lambda: False)})())
        self.assertEqual(result.code, owner.OFF)
        hold.set()
        self.c.agent.stop("test")
        self.ended()


class WhatIsOfferedTests(ThingsApp):
    def started_with(self, **harness: Any) -> str:
        self.go(Script(), **harness)
        self.ended()
        return self.allowed()

    def test_everything_connected_is_offered(self) -> None:
        allowed = self.started_with()
        for name in ("calendar_find", "contacts_find", "memory_read", "conversation_read", "mail_search", "mail_read",
                     "files_find",
                     "files_list", "file_read", "propose_files", "propose_action", "open_thing", "pc_control"):
            self.assertIn(f"mcp__jarvis__{name}", allowed)

    def test_no_mail_without_mail_connected(self) -> None:
        allowed = self.started_with(readers={"personal": FakeReader("personal", problem=("signed_out",
                                                                                         "not signed in"))})
        self.assertNotIn("mail_search", allowed)
        self.assertNotIn("mail_read", allowed)
        self.assertIn("calendar_find", allowed)

    def test_no_notes_without_them(self) -> None:
        allowed = self.started_with(notes=None)
        self.assertNotIn("memory_read", allowed)
        self.assertIn("conversation_read", allowed)

    def test_no_calendar_tools_when_signed_out(self) -> None:
        engine = self.make_things(Script(), readers={})
        for calendar in self.harness.calendars.values():
            calendar.signed_in = False
        self.start()
        self.ended()
        allowed = self.allowed()
        for name in ("calendar_find", "contacts_find", "mail_search"):
            self.assertNotIn(name, allowed)
        self.assertIn("files_find", allowed)
        self.assertIsNotNone(engine)

    def test_private_narrows_what_is_offered(self) -> None:
        allowed = self.started_with(agent='private = ["calendar"]\n')
        self.assertIn("calendar_find", allowed)
        for name in ("contacts_find", "memory_read", "conversation_read", "mail_search", "files_find", "file_read",
                     "propose_files"):
            self.assertNotIn(name, allowed)
        self.assertIn("web_search", allowed)

    def test_a_bad_value_is_the_default_and_an_unknown_entry_grants_nothing(self) -> None:
        self.assertIn("mail_search", self.started_with(agent='private = "mail"\n'))

    def test_no_consent_no_task_at_all(self) -> None:
        self.make_things(Script(), desktop_consent=2)
        self.assertFalse(self.c.agent.start(self.GOAL))
        settle()
        self.assertIsNone(self.c.agent.run())
        self.assertEqual(self.runs(), [])
        self.assertIsNone(self.c.agent._owner)


class CanaryTests(ThingsApp):
    GOAL = f"read the {SUBJECT_WORD} mail"

    def test_a_canary_name_never_reaches_a_log_line(self) -> None:
        lines: list[str] = []

        class Keep(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                lines.append(record.getMessage())

        keep = Keep(logging.DEBUG)
        root = logging.getLogger()
        before = root.level
        root.addHandler(keep)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, keep)
        self.addCleanup(root.setLevel, before)
        secret_mail = invented_thread(f"{CANARY} says hello", sender=f"{CANARY} <canary@example.edu>",
                                      subject=f"{SUBJECT_WORD} {CANARY}")
        reader = FakeReader("personal", {THREAD_ID: secret_mail}, found=[THREAD_ID])
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("contacts", "contacts_find", {"name": CANARY}), ("memory", "memory_read", {}),
                        ("search", "mail_search", {"words": SUBJECT_WORD}), ("read", "mail_read",
                                                                             {"thread": THREAD_ID}),
                        ("said", "conversation_read", {}),
                        ("find", "files_find", {"words": CANARY}),
                        ("file", "file_read", lambda got: {"ref": ref_in(got["find"], f"{CANARY}.txt")}),
                        ("refused", "mail_search", {"words": CANARY}),
                        ("todo", "propose_action",
                         {"lines": [f"Todo: title={CANARY} | due=2026-11-06 10:00 | block= | acct= | link="]}),
                        ("move", "propose_files", lambda got: {"steps": [{"skill": "rename_file",
                                                                          "files": [ref_in(got["find"],
                                                                                           f"{CANARY}.txt")],
                                                                          "name": "x.txt"}]}),
                        ("web", "web_search", {"query": CANARY}), ("wait",))
        self.make_things(script, readers={"personal": reader}, notes=(f"{CANARY} likes tea.",))
        self.harness.calendars["personal"].briefs = [invented_event(title=f"Dinner with {CANARY}",
                                                                    organizer_name=CANARY)]
        self.fake.make_file(f"{DOWNLOADS}\\{CANARY}.txt", f"{CANARY} text".encode())
        self.c.agent._things._conversation = lambda: (Turn(ROLE_YOU, f"ask {CANARY}", NOW),)
        self.start()
        self.assertTrue(wait_for(lambda: self.feed.waits() == 1, timeout=15), "the firewall's card")
        settle()
        self.press_card("choice:1")
        self.ended()
        self.assertTrue(lines)
        self.assertIn(CANARY, script.text("read"))                  # the tools really saw it
        self.assertIn(CANARY, script.text("file"))
        leaked = [line for line in lines if CANARY in line or CANARY.lower() in line.lower()]
        self.assertEqual(leaked, [])


class LiveTests(ThingsApp):
    def test_private_results_stay_on_screen_in_memory_and_its_steps_say_so(self) -> None:
        script = Script(("find", "files_find", {"words": FILE_WORD}))
        self.go(script)
        self.ended()
        steps = [step for task in self.agent_tasks() for step in task.steps
                 if step.title == "Looked through your folders"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].kind, live.AGENT_TOOL)


class HopTests(unittest.TestCase):
    """What a tool asks of the GUI thread runs there; past its time it does not run at all."""

    @staticmethod
    def from_a_thread(call: Callable[[], Any]) -> list[Any]:
        got: list[Any] = []
        thread = threading.Thread(target=lambda: got.append(call()))
        thread.start()
        while thread.is_alive():
            QApplication.processEvents()
            thread.join(0.005)
        return got

    def test_it_runs_on_the_gui_thread(self) -> None:
        hop = agent_ui._Hop()
        got = self.from_a_thread(lambda: hop.run(threading.current_thread))
        self.assertIs(got[0], threading.main_thread())
        self.assertIs(hop.run(threading.current_thread), threading.main_thread())

    def test_past_its_time_it_never_runs(self) -> None:
        hop = agent_ui._Hop()
        ran: list[int] = []
        got: list[Any] = []
        original = agent_ui.HOP_S
        agent_ui.HOP_S = 0.01
        self.addCleanup(setattr, agent_ui, "HOP_S", original)
        thread = threading.Thread(target=lambda: got.append(hop.run(lambda: ran.append(1) or "ran", "default")))
        thread.start()
        thread.join(5.0)                                             # the GUI thread takes no events meanwhile
        QApplication.processEvents()
        self.assertEqual(got, ["default"])
        self.assertEqual(ran, [])

    def test_what_fails_there_comes_back_to_the_thread_that_asked(self) -> None:
        hop = agent_ui._Hop()
        errors: list[str] = []

        def ask() -> None:
            try:
                hop.run(lambda: 1 / 0)
            except ZeroDivisionError as exc:
                errors.append(type(exc).__name__)

        self.from_a_thread(ask)
        self.assertEqual(errors, ["ZeroDivisionError"])


class PolicyFloorTests(unittest.TestCase):
    def test_a_card_tool_with_no_firewall_is_refused(self) -> None:
        tool = next(tool for tool in policy.offered(connected={"desk"}) if tool.name == "open_thing")
        self.assertEqual(policy.gate(tool, {}, None).code, policy.NO_FIREWALL)
        self.assertEqual(fw.WAITING, owner.WAITING)


if __name__ == "__main__":
    unittest.main()
