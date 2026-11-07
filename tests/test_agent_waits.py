"""Jarvis, a true agent: what the behaviour review found. The waits, each from the first event to the task's end through the
real runner and a fake process: none may be left without an end, and what the owner and the model are told on the way is
true. The words typed at each kind of wait; the owner's words in the order they said them; Stop in everyday words; what a
stopped task's voice does; the cards on a short screen and against the watch window; Live's stamps and its tokens; what a
later Ask hears of a task that ended.

Qt runs offscreen and shows nothing. The real Claude Code is never started: tests.agent_fakes.FakeEngine really POSTs to
the real endpoint on loopback. Every value is invented; files go only to temporary folders.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import agent_feed, agent_ui, desktop_view, holo, hud, hud_overlay, live, ui
from briefing_reader import orb as orb_mod
from briefing_reader.agent import browser as agent_browser
from briefing_reader.agent import owner, web
from briefing_reader.agent import runner as ar
from briefing_reader.agent.firewall import WebFirewall, holds_something_private
from briefing_reader.ask.stream import (LIMIT, NOT_SIGNED_IN, SERVER_BUSY, limit_reset_time, limit_reset_words,
                                        task_failure)
from briefing_reader.conversation import CONVERSATION_FILE, ROLE_JARVIS, ConversationStore, Turn
from briefing_reader.desktop import results as card_results
from briefing_reader.desktop import tidy as desktop_tidy
from tests.agent_fakes import FAST, FakeBrowser, FakeEngine, FakePage, rate_limit_event, result_event
from tests.ask_fakes import plan_stream
from tests.desktop_fakes import DOWNLOADS
from tests.owner_fakes import LETTER, SECRET_WORD
from tests.test_agent_app import SAY, SUMMARY, TASK_PLAN, TYPED, AgentApp, whole_task
from tests.test_agent_feed import STEPS, FeedApp, Stages, plan
from tests.test_agent_run import GOAL, WHEN, RunCase
from tests.test_agent_things import DONE, Script, ThingsApp, ref_in
from tests.ui_fakes import fonts, settle, wait_for

_app: QApplication | None = None
RESET = WHEN + timedelta(hours=2)
LIMIT_TEXT = "You've hit your limit - resets 3pm (Europe/London)"
URL = "https://www.example.org/quiet"
OPENED_WORDS = "open https://www.example.org/news"     # the owner's words: an open, of that address
SUGGESTED = "https://other.example.net/"               # an address they did not type: it waits for a click
SUGGESTED_TOO = "https://another.example.net/"
PATIENT = replace(FAST, tool_s=30.0, wrap_up_s=30.0, after_finish_s=30.0)
CLICK_SENT = "Passed on, sir, but only a click answers that card."


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_agent_waits", "-platform", "offscreen"])
    fonts()


class StopWordsTests(unittest.TestCase):
    def test_everyday_ways_of_saying_stop_stop_it(self) -> None:
        for words in ("stop", "Stop.", "Stop!", "stop please", "Stop, please.", "please stop", "Jarvis stop",
                      "Jarvis, stop.", "stop it", "Stop that.", "cancel", "Cancel.", "cancel it", "Cancel the task.",
                      "cancel this task",
                      "stop now", "Stop now.", "stop the task now", "ok stop", "Okay, stop.", "stop, jarvis", "Stop Jarvis",
                      "hey jarvis stop", "stop stop", "Stop, stop, stop!", "no, stop", "Wait, stop.", "stop right now",
                      "stop here", "cancel that please", "Stop\u2026", "  STOP  ", "stop, thanks"):
            with self.subTest(words=words):
                self.assertTrue(agent_ui.stop_words(words))

    def test_words_that_merely_hold_the_word_go_to_the_model(self) -> None:
        for words in ("stop at the second shop", "don't stop looking", "Don\u2019t stop", "don't stop",
                      "stop writing it in French", "cancel my 2 PM", "stop the car first",
                      "please stop at the second shop", "okay stop the second search", "non-stop", "unstoppable",
                      "stopping", "abort", "never mind", "that's enough", "", "stop?", "Should I stop?",
                      "keep going, don't cancel", "stop " * 40):
            with self.subTest(words=words):
                self.assertFalse(agent_ui.stop_words(words))


class ResetTimeTests(unittest.TestCase):
    def at(self, text: str) -> datetime | None:
        return limit_reset_time(text, now=lambda: WHEN)         # 18:30 on 4 November, UTC: London's time then too

    def test_a_time_the_clock_can_read(self) -> None:
        for text, day, hour, minute in ((LIMIT_TEXT, 5, 15, 0), ("resets 7pm", 4, 19, 0), ("Resets at 3:30 PM", 5, 15, 30),
                                        ("resets 15:30", 5, 15, 30), ("resets 12am", 5, 0, 0), ("resets 12pm", 5, 12, 0),
                                        ("resets 6:45 p.m.", 4, 18, 45), ("resets 3pm (Mars/Olympus)", 5, 15, 0)):
            with self.subTest(text=text):
                self.assertEqual(self.at(text), WHEN.replace(day=day, hour=hour, minute=minute))

    def test_the_old_form_is_a_moment(self) -> None:
        self.assertEqual(self.at("Claude usage limit reached|1762300000"),
                         datetime.fromtimestamp(1762300000, tz=timezone.utc))

    def test_none_when_no_clock_can_read_it(self) -> None:
        for text in ("You've hit your limit", "", "resets 3", "resets 25:00", "resets 13pm", "resets 3:99 pm",
                     "resets soon"):
            with self.subTest(text=text):
                self.assertIsNone(self.at(text))

    def test_none_with_a_day_or_a_date_beside_the_time(self) -> None:
        """X2: never the next 5am for a limit that resets on Monday: a pause must never be shorter than the real one."""
        for text in ("resets 5am Mon", "You've hit your limit - resets 5am Mon", "Resets at 5am Monday",
                     "resets 5am on Monday",
                     "resets 5am Mon (Europe/London)", "resets 5am (Europe/London) Mon", "resets 3pm Nov 10",
                     "resets 3pm, Nov 10", "resets 3pm 10 Nov", "resets 3:30 pm tomorrow", "resets 15:30 2026-11-10",
                     "resets 3pm 11/10", "resets 3pm on the 10th", "resets Mon 5am", "resets Nov 10, 5am (Europe/London)",
                     "resets 5 Nov", "resets 2026-08-08 00:00 UTC", "On Monday it resets 5am"):
            with self.subTest(text=text):
                self.assertIsNone(self.at(text))

    def test_a_zone_or_a_word_that_only_looks_like_a_day_stays_a_time(self) -> None:
        for text, hour in (("resets 3pm (America/Marigot)", 15), ("resets 3pm (Asia/Monday_Town)", 15),
                           ("a sunny day: resets 3pm", 15), ("You've reached your limit - resets 3pm", 15),
                           ("5-hour limit reached - resets 3pm", 15)):
            with self.subTest(text=text):
                self.assertEqual(self.at(text).hour, hour)

    def test_ask_s_own_words_for_it_stay_as_they_were(self) -> None:
        self.assertEqual(limit_reset_words("resets 5am Mon"), "5am")

    def test_none_with_anything_but_its_zone_after_the_time(self) -> None:
        """run-6: a day counted, a next, a week, a zone outside brackets (the time is not read in it) or any other word
        after the time in its clause: the next such time may be far too soon, so the task waits as with no time."""
        for text in ("Your weekly limit resets 5am in 3 days", "Your weekly limit resets 5am next week",
                     "limit reached - resets 00:00 UTC", "resets 5pm GMT", "resets 3pm in 2 weeks",
                     "resets 5am, next week", "resets at 00:00, UTC", "resets 3pm (UTC+05:30)", "resets 3pm or later",
                     "Your limit resets 5am. It resets weekly, in 1 week"):
            with self.subTest(text=text):
                self.assertIsNone(self.at(text))

    def test_the_real_words_seen_today_read_a_time(self) -> None:
        """Claude Code's own words for the plan's limit, as seen on this PC: a time in its zone (this PC's own when there
        is no zone data here), and the end of its clause after it changes nothing."""
        for text in ("You've hit your 5-hour limit \u00b7 resets 6pm (America/Los_Angeles)",
                     "resets 6pm (America/Los_Angeles)", "You've hit your limit \u00b7 resets 6pm (America/Los_Angeles). "
                     "/upgrade to increase your usage limit.", "resets 6pm (America/Los_Angeles) \u00b7 /upgrade",
                     "resets 6pm - try again then", "resets 6pm\n"):
            with self.subTest(text=text):
                moment = self.at(text)
                self.assertIsNotNone(moment)
                self.assertEqual((moment.hour, moment.minute), (18, 0))
        self.assertIsNone(self.at("resets 00:00 UTC"))
        self.assertIsNotNone(self.at("resets 00:00 (UTC)"))          # a zone in brackets is its zone


def limit_by_text(engine: FakeEngine, text: str = LIMIT_TEXT) -> None:
    engine.wait_message()
    engine.call("write_file", name="a.md", text="a")
    engine.emit(result_event(text, is_error=True))


class LimitInItsTextTests(RunCase):
    """arf-1: a limit said only in the failed result's text, from the first event to the end."""

    def test_it_asks_at_that_time_and_a_click_carries_on(self) -> None:
        now = [WHEN]

        def script(engine: FakeEngine) -> None:
            limit_by_text(engine)
            told = engine.wait_message()
            assert told == ar.RESUME_YES
            engine.call("finish", summary="Done after the wait.", outcome="done", outputs=["a.md"])
            engine.end_turn()

        run = self.go(script, now=lambda: now[0])
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertIn("it resets 3pm (Europe/London), and I'll ask you then.", self.of(ar.E_NOTICE)[0].text)
        self.assertEqual(self.of(ar.E_NOTICE)[0].code, "limit")
        self.until(lambda: run._idle)
        self.assertEqual(self.of(ar.E_QUESTION), [])            # not before its time
        now[0] = WHEN + timedelta(days=1)
        question = self.question()
        self.assertEqual(question.kind, ar.Q_RESUME)
        self.assertTrue(run.answer(question.id, "Keep going"))
        end = self.end()
        self.assertEqual((end.outcome, end.summary), ("done", "Done after the wait."))
        self.assert_clean()

    def test_the_old_form_too(self) -> None:
        now = [WHEN]
        run = self.go(lambda engine: (limit_by_text(engine, "Claude usage limit reached|1762300000"), time.sleep(30)),
                      now=lambda: now[0], timing=replace(FAST, keep_going_s=0.05))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.until(lambda: run._idle)
        now[0] = datetime.fromtimestamp(1762300001, tz=timezone.utc) + timedelta(days=365)
        self.assertEqual(self.end().code, ar.C_NO_ANSWER)

    def test_no_answer_when_it_asks_ends_the_task(self) -> None:
        now = [WHEN]
        run = self.go(lambda engine: (limit_by_text(engine), time.sleep(30)), now=lambda: now[0],
                      timing=replace(FAST, keep_going_s=0.05))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.until(lambda: run._idle)
        now[0] = WHEN + timedelta(days=1)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_NO_ANSWER, ar.NO_ANSWER_WORDS))
        self.assert_clean()

    def test_with_no_time_named_it_ends_after_its_long_wait_and_says_so(self) -> None:
        self.go(lambda engine: (limit_by_text(engine, "You've hit your limit"), time.sleep(30)),
                timing=replace(FAST, limit_wait_s=0.2))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertEqual(self.of(ar.E_NOTICE)[0].text, ar.LIMIT_NO_TIME_WORDS)
        self.assertIn("With no word in 6 hours I'll stop it there.", ar.LIMIT_NO_TIME_WORDS)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_LIMIT_WAIT, ar.LIMIT_WAITED_WORDS))
        self.assert_clean()

    def test_its_wait_is_long_as_it_says(self) -> None:
        self.assertEqual(ar.Timing().limit_wait_s, 6 * 3600.0)
        self.assertIn("6 hours", ar.LIMIT_WAITED_WORDS)

    def test_a_day_beside_the_time_waits_as_with_no_time_named(self) -> None:
        """X2: the run, on the words: never "it resets 5am, and I'll ask you then" of a limit that resets on Monday."""
        now = [WHEN]
        run = self.go(lambda engine: (limit_by_text(engine, "You've hit your limit - resets 5am Mon"), time.sleep(30)),
                      now=lambda: now[0], timing=replace(FAST, limit_wait_s=1e9))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertEqual(self.of(ar.E_NOTICE)[0].text, ar.LIMIT_NO_TIME_WORDS)
        self.assertIsNone(run._paused_until)
        now[0] = WHEN + timedelta(hours=11)                     # the next 5am has come and gone
        time.sleep(0.1)
        self.assertEqual(self.of(ar.E_QUESTION), [])            # no Resume card at the wrong time
        run.stop("test")

    def test_words_that_say_when_mean_a_time_it_asks_at(self) -> None:
        """A time the words name but no clock can read ("resets 3") is said as no time at all: never "I'll ask you then"."""
        self.go(lambda engine: (limit_by_text(engine, "You've hit your limit - resets 3"), time.sleep(30)))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertEqual(self.of(ar.E_NOTICE)[0].text, ar.LIMIT_NO_TIME_WORDS)
        self.assertIsNone(self.run_._paused_until)

    def test_a_signal_with_no_time_then_a_text_that_has_it(self) -> None:
        now = [WHEN]

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event())
            engine.emit(result_event(LIMIT_TEXT, is_error=True))
            time.sleep(30)

        run = self.go(script, now=lambda: now[0], timing=replace(FAST, keep_going_s=30.0))
        self.until(lambda: len(self.of(ar.E_NOTICE)) == 2)
        self.assertEqual(self.of(ar.E_NOTICE)[0].text, ar.LIMIT_NO_TIME_WORDS)
        self.assertIn("I'll ask you then", self.of(ar.E_NOTICE)[1].text)
        now[0] = WHEN + timedelta(days=1)
        self.assertEqual(self.question().kind, ar.Q_RESUME)
        run.stop()


class NotThePlansLimitTests(RunCase):
    """run-3: a failure that is not the plan's limit was told as the plan's limit, with a 6-hour wait: a context, budget or
    subagent limit, a bare "limit reached", a server limiting requests (Claude Code's own words: "not your usage limit")
    or any 429. A task pauses only on the plan's own words now (stream.task_failure), and Ask's reading is its own."""

    PLAN = ("You've hit your 5-hour limit \u00b7 resets 6pm (America/Los_Angeles)", "You've hit your limit",
            "You've hit your limit \u00b7 resets 5am Mon", "Claude usage limit reached|1762300000", "Usage limit reached",
            "Your weekly limit resets 5am in 3 days", "Lower-priority mode ended \u00b7 you have reached your weekly usage "
            "limit", "5-hour limit reached - resets 3pm", "Session limit reached", "You've reached your limit",
            "You're out of extra usage")
    NOT_A_LIMIT = ("Context limit reached \u00b7 /compact or /clear to continue",
                   "Budget limit reached ($5.00 spent of the $5.00 maximum). New agents cannot be started.",
                   "Concurrent subagent limit reached. You can run 3 subagents at once.", "limit reached",
                   "Subagent nesting limit reached (depth 3 of 3).", "Fast mode disabled \u00b7 usage credit limit reached",
                   "Prompt is too long", "")
    SERVER = ("Server is temporarily limiting requests (not your usage limit)",
              "API Error: 429 rate_limit_error: slow down")

    def test_what_a_task_reads_as_the_plan_s_limit(self) -> None:
        for text in self.PLAN:
            with self.subTest(text=text):
                self.assertEqual(task_failure(text, now=lambda: WHEN).kind, LIMIT)
                self.assertEqual(task_failure(text, now=lambda: WHEN, status=429).kind, LIMIT)
        for text in self.NOT_A_LIMIT:
            with self.subTest(text=text):
                self.assertIsNone(task_failure(text, now=lambda: WHEN))
                self.assertEqual(task_failure(text, now=lambda: WHEN, status=429).kind, SERVER_BUSY)   # a bare 429
        for text in self.SERVER:
            with self.subTest(text=text):
                self.assertEqual(task_failure(text, now=lambda: WHEN).kind, SERVER_BUSY)
        self.assertEqual(task_failure("Please run /login", now=lambda: WHEN).kind, NOT_SIGNED_IN)
        self.assertEqual(task_failure("", now=lambda: WHEN, status=401).kind, NOT_SIGNED_IN)

    def failing(self, text: str, status: Any = None) -> ar.AgentEnd | None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event(text, is_error=True, api_error_status=status))
            time.sleep(30)

        run = self.go(script, timing=replace(FAST, limit_wait_s=1e9))
        self.until(lambda: run.ended is not None or run._paused)
        return run.ended

    def test_another_limit_ends_the_task_as_any_failure_does(self) -> None:
        for text in self.NOT_A_LIMIT[:4]:
            with self.subTest(text=text):
                self.setUp()
                end = self.failing(text)
                self.assertIsNotNone(end)
                self.assertEqual((end.outcome, end.code, end.summary), ("failed", ar.ERROR, ar.EXIT_WORDS))
                self.assertEqual([event for event in self.of(ar.E_NOTICE) if event.code == LIMIT], [])
                self.assertFalse(self.run_._paused)

    def test_the_server_limiting_requests_ends_it_in_words_that_say_so(self) -> None:
        for text, status in ((self.SERVER[0], None), (self.SERVER[0], 429), ("", 429),
                             ("API Error: Request rejected (429) \u00b7 this may be a temporary capacity issue", 429)):
            with self.subTest(text=text, status=status):
                self.setUp()
                end = self.failing(text, status)
                self.assertIsNotNone(end)
                self.assertEqual((end.outcome, end.code, end.summary), ("failed", ar.C_SERVER_BUSY, ar.SERVER_BUSY_WORDS))
                self.assertEqual(self.of(ar.E_NOTICE), [])
        self.assertIn("isn't your plan's limit", ar.SERVER_BUSY_WORDS)
        self.assertNotIn("reached", ar.SERVER_BUSY_WORDS)

    def test_the_real_words_seen_today_pause_it_until_their_time(self) -> None:
        self.assertIsNone(self.failing("You've hit your 5-hour limit \u00b7 resets 6pm (America/Los_Angeles)"))
        self.assertIsNotNone(self.run_._paused_until)
        self.assertEqual(self.run_._paused_until.hour, 18)
        self.assertIn("it resets 6pm (America/Los_Angeles), and I'll ask you then.", self.of(ar.E_NOTICE)[0].text)

    def test_a_process_that_ends_by_itself_reads_its_words_the_same_way(self) -> None:
        for tail, code, words in (("Context limit reached \u00b7 /compact or /clear to continue", ar.C_EXIT, ar.EXIT_WORDS),
                                  ("You've hit your limit \u00b7 resets 6pm", LIMIT, ar.LIMIT_ENDED_WORDS),
                                  ("Server is temporarily limiting requests (not your usage limit)", ar.C_SERVER_BUSY,
                                   ar.SERVER_BUSY_WORDS)):
            with self.subTest(tail=tail):
                self.setUp()

                def script(engine: FakeEngine) -> None:
                    engine.wait_message()
                    raise SystemExit

                self.go(script, engine_kwargs={"stderr": tail})
                end = self.end()
                self.assertEqual((end.code, end.summary), (code, words))


class TheOwnersWordAtAPauseTests(RunCase):
    """run-1: the plan's limit holds with no time named while a card is up. The owner's click on it, their answer, or the
    words they typed at it waited unseen for a typed line that might never come, and after 6 hours the end said "with no
    word". Now the card's end lets what waited go in, as a typed line does then, and the task tries again. A card that only
    ran out of time leaves the pause waiting for their word, and "with no word" stays true."""

    NO_TIME = "You've hit your limit \u00b7 resets 5am Mon"

    def fails_on_the_limit(self, told: list[str], up: threading.Event, more: int = 1) -> Any:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            up.wait(10)                                         # the test puts its card up
            engine.emit(result_event(self.NO_TIME, is_error=True))
            for _message in range(more):
                told.append(engine.wait_message())
            engine.call("finish", summary="Done after the wait.", outcome="done")
            engine.end_turn()

        return script

    def paused(self, run: ar.AgentRun) -> None:
        self.until(lambda: run._paused and run._idle)
        self.assertIsNone(run._paused_until)

    def test_a_click_on_the_firewall_s_card_carries_on_with_the_words_typed_at_it(self) -> None:
        told: list[str] = []
        up = threading.Event()
        self.addCleanup(up.set)
        run = self.go(self.fails_on_the_limit(told, up, more=2))
        self.until(lambda: self.kinds(ar.E_STARTED))
        heard: list[bool] = []
        self.assertTrue(run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), heard.append))
        question = run.waiting
        up.set()
        self.paused(run)
        self.assertEqual(run.owner_says("carry on please"), "held")
        self.assertEqual(told, [])
        self.assertTrue(run.answer(question.id, "Allow"))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, ["The owner says: carry on please", ar.WEB_ALLOWED_TEXT])
        self.assertEqual(heard, [True])
        self.assertIn(ar.E_LIMIT_GONE, self.kinds())

    def test_a_click_alone_on_keep_going_carries_on(self) -> None:
        told: list[str] = []

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")     # held: Keep going asks
            engine.emit(result_event(self.NO_TIME, is_error=True))
            told.append(engine.wait_message())
            engine.call("finish", summary="Done after the wait.", outcome="done")
            engine.end_turn()

        run = self.go(script, agent={"max_turns": 1})
        question = self.question()
        self.assertEqual(question.kind, ar.Q_KEEP_GOING)
        self.paused(run)
        self.assertTrue(run.answer(question.id, "Keep going"))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, [ar.KEEP_GOING_YES])

    def test_a_choice_clicked_on_the_model_s_question_carries_on(self) -> None:
        told: list[str] = []
        up = threading.Event()
        self.addCleanup(up.set)
        run = self.go(self.fails_on_the_limit(told, up))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_question("Blue or Red?", ("Blue", "Red")))
        question = run.waiting
        up.set()
        self.paused(run)
        self.assertTrue(run.answer(question.id, "Blue"))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, ["The owner answers your question: Blue"])

    def test_words_at_a_batch_at_work_go_in_as_it_ends(self) -> None:
        told: list[str] = []
        up = threading.Event()
        self.addCleanup(up.set)
        run = self.go(self.fails_on_the_limit(told, up, more=2), timing=replace(FAST, init_s=1e9, at_work_s=1000.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        self.assertTrue(run.card_at_work("card1"))
        up.set()
        self.paused(run)
        self.assertEqual(run.owner_says("carry on please"), "held")
        self.assertEqual(self.of(ar.E_NOTICE)[-1].text, ar.HELD_AT_WORK_WORDS)     # never "when the limit has reset"
        self.assertTrue(run.card_ended("card1", "Jarvis: the owner clicked the card. Done 1 of 1."))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, ["The owner says: carry on please", "Jarvis: the owner clicked the card. Done 1 of 1."])

    def test_a_file_card_clicked_carries_on_and_one_that_ran_out_does_not(self) -> None:
        for clicked in (True, False):
            with self.subTest(clicked=clicked):
                self.setUp()
                told: list[str] = []
                up = threading.Event()
                self.addCleanup(up.set)
                run = self.go(self.fails_on_the_limit(told, up), timing=replace(FAST, init_s=1e9, limit_wait_s=1e9))
                self.until(lambda: self.kinds(ar.E_STARTED))
                self.assertTrue(run._tool_card("card1"))
                up.set()
                self.paused(run)
                self.assertTrue(run.card_ended("card1", "Jarvis: the owner chose Not now.", clicked=clicked))
                if clicked:
                    self.assertEqual(self.end().outcome, "done")
                    self.assertEqual(told, ["Jarvis: the owner chose Not now."])
                else:
                    time.sleep(0.2)
                    self.assertTrue(run._paused)
                    self.assertEqual(told, [])
                    self.assertEqual(run._held_owner, ["Jarvis: the owner chose Not now."])
                    run.stop("test")

    def test_words_typed_at_a_card_that_then_runs_out_go_in_as_it_ends(self) -> None:
        told: list[str] = []
        up = threading.Event()
        self.addCleanup(up.set)
        run = self.go(self.fails_on_the_limit(told, up, more=2), timing=replace(FAST, web_card_s=0.6, limit_wait_s=1e9))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), lambda _allowed: None))
        up.set()
        self.paused(run)
        self.assertEqual(run.owner_says("carry on please"), "held")
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, ["The owner says: carry on please", ar.WEB_NO_CLICK_TEXT])

    def test_a_word_counts_for_its_own_pause_only(self) -> None:
        """A click let the first pause go; the next pause, with a card that only runs out, waits for a word of its own."""
        told: list[str] = []
        up, again = threading.Event(), threading.Event()
        self.addCleanup(up.set)
        self.addCleanup(again.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            up.wait(10)
            engine.emit(result_event(self.NO_TIME, is_error=True))
            told.append(engine.wait_message())
            again.wait(10)                                      # the test puts a second card up
            engine.emit(result_event(self.NO_TIME, is_error=True))
            time.sleep(30)

        run = self.go(script, timing=replace(FAST, web_card_s=0.6, limit_wait_s=2.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), lambda _allowed: None))
        first = run.waiting
        up.set()
        self.paused(run)
        self.assertTrue(run.answer(first.id, "Allow"))
        self.until(lambda: told)
        self.assertTrue(run.ask_web("Jarvis wants to open that", ("Allow", "Don't"), lambda _allowed: None))
        again.set()
        self.until(lambda: run._paused and run._idle)
        end = self.end()
        self.assertEqual((end.code, end.summary), (ar.C_LIMIT_WAIT, ar.LIMIT_WAITED_WORDS))
        self.assertEqual(told, [ar.WEB_ALLOWED_TEXT])
        self.assertEqual(len(self.engine.sent), 2)

    def test_a_card_that_only_ran_out_leaves_the_pause_and_with_no_word_stays_true(self) -> None:
        up = threading.Event()
        self.addCleanup(up.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            up.wait(10)
            engine.emit(result_event(self.NO_TIME, is_error=True))
            time.sleep(30)

        run = self.go(script, timing=replace(FAST, web_card_s=0.6, limit_wait_s=2.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), lambda _allowed: None))
        up.set()
        self.paused(run)
        self.assertIsNotNone(run.waiting)                       # the card is still up as the limit holds
        self.until(lambda: [event for event in self.of(ar.E_QUESTION_GONE) if event.code == ar.C_NO_ANSWER])
        time.sleep(0.1)
        self.assertTrue(run._paused)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_LIMIT_WAIT, ar.LIMIT_WAITED_WORDS))
        self.assertEqual(len(self.engine.sent), 1)              # the goal: nothing went in with no word from the owner
        self.assertEqual([event for event in self.of(ar.E_NOTICE) if event.code == LIMIT][1:], [])   # one notice


class WordsInOrderTests(RunCase):
    """arf-2, arf-4: the owner's words reach the model once and in the order they were said."""

    def test_an_answer_and_a_correction_right_after_it(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            release.wait(10)                                    # the model is still writing its closing sentence
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        self.question()
        self.assertEqual(run.owner_says("Blue"), "answered")
        self.assertEqual(run.owner_says("no wait, make that Red"), "sent")
        self.assertEqual(len(self.engine.sent), 1)              # nothing has jumped ahead
        release.set()
        self.end()
        self.assertEqual(self.engine.messages[1:], ["The owner answers your question: Blue",
                                                    "The owner says: no wait, make that Red"])

    def test_never_past_keep_going_s_click_either(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.call("write_file", name="b.md", text="b")    # held: Keep going asks
            release.wait(10)
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, agent={"max_turns": 2})
        question = self.question()
        self.assertTrue(run.answer(question.id, "Keep going"))
        self.assertEqual(run.owner_says("and keep it short"), "sent")
        release.set()
        self.end()
        self.assertEqual(self.engine.messages[1:], [ar.KEEP_GOING_YES, "The owner says: and keep it short"])

    def test_with_nothing_waiting_it_goes_at_once_as_ever(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            release.wait(10)
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        self.until(lambda: self.of(ar.E_TOOL_END))
        self.assertEqual(run.owner_says("use the blue one"), "sent")
        self.assertEqual(len(self.engine.sent), 2)              # mid-turn: Claude Code delivers it after the tool
        release.set()
        self.end()

    def test_words_held_for_the_limit_go_first_when_more_come_after_the_reset(self) -> None:
        now = [WHEN]

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(resets_at=RESET.timestamp()))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, now=lambda: now[0], timing=replace(FAST, resume_margin_s=30.0))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("use the blue one, not the red"), "held")
        now[0] = RESET + timedelta(seconds=5)                   # past the reset, inside the margin before the card
        self.assertEqual(run.owner_says("and keep it short"), "sent")
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(run._held_owner, [])
        self.assertEqual(self.engine.messages[1:], ["The owner says: use the blue one, not the red",
                                                    "The owner says: and keep it short"])

    def test_an_answer_while_the_limit_holds_goes_in_with_the_resume(self) -> None:
        now = [WHEN]

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.emit(rate_limit_event(resets_at=RESET.timestamp()))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, now=lambda: now[0])
        self.question()
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("Blue"), "answered")
        self.assertEqual(len(self.engine.sent), 1)              # nothing could answer it now
        now[0] = RESET + timedelta(seconds=1)
        self.until(lambda: self.of(ar.E_QUESTION)[-1].question.kind == ar.Q_RESUME)
        self.assertTrue(run.answer(self.of(ar.E_QUESTION)[-1].question.id, "Keep going"))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1:], [ar.RESUME_YES, "The owner answers your question: Blue"])

    def test_an_answer_with_no_reset_time_tries_again(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.emit(result_event("You've hit your limit", is_error=True, api_error_status=429))
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        self.question()
        self.until(lambda: run._paused and run._idle)
        self.assertEqual(run.owner_says("Blue"), "answered")
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1:], ["The owner answers your question: Blue"])


class ResumeCardRoadTests(RunCase):
    """arf-3: at the resume card, from the limit to the end."""

    def to_the_card(self, script: Any, **timing: Any) -> tuple[ar.AgentRun, ar.OwnerQuestion]:
        now = [WHEN]
        run = self.go(script, now=lambda: now[0], timing=replace(FAST, **timing))
        self.until(lambda: self.of(ar.E_NOTICE))
        self.until(lambda: run._idle)
        now[0] = RESET + timedelta(seconds=1)
        return run, self.question()

    @staticmethod
    def limit(engine: FakeEngine) -> None:
        engine.wait_message()
        engine.emit(rate_limit_event(resets_at=RESET.timestamp()))
        engine.emit(result_event("limit", is_error=True, api_error_status=429))

    def test_words_typed_wait_for_the_click_and_then_go_in(self) -> None:
        def script(engine: FakeEngine) -> None:
            self.limit(engine)
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run, question = self.to_the_card(script)
        notices = len(self.of(ar.E_NOTICE))
        self.assertEqual(run.owner_says("yes carry on"), "held")
        self.assertEqual(run.click_awaited(), question)
        self.assertEqual(len(self.of(ar.E_NOTICE)), notices)    # never "when the plan's limit has reset": it has
        self.assertEqual(run.waiting, question)
        self.assertEqual(len(self.engine.sent), 1)              # words alone carry nothing on
        self.assertTrue(run.answer(question.id, "Keep going"))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1:], [ar.RESUME_YES, "The owner says: yes carry on"])

    def test_words_typed_and_no_click_end_it_with_words_that_say_so(self) -> None:
        run, _question = self.to_the_card(lambda engine: (self.limit(engine), time.sleep(30)), keep_going_s=3.0)
        self.assertEqual(run.owner_says("yes carry on"), "held")
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_NO_CLICK, ar.NO_CLICK_WORDS))
        self.assertNotIn("No answer", end.summary)
        self.assertEqual(len(self.engine.sent), 1)
        gone = [event for event in self.of(ar.E_QUESTION_GONE) if event.code == ar.C_NO_ANSWER]
        self.assertTrue(gone[0].question.typed)
        self.assert_clean()

    def test_no_words_no_click_is_no_answer(self) -> None:
        self.to_the_card(lambda engine: (self.limit(engine), time.sleep(30)), keep_going_s=0.05)
        end = self.end()
        self.assertEqual((end.code, end.summary), (ar.C_NO_ANSWER, ar.NO_ANSWER_WORDS))
        self.assert_clean()


class KeepGoingRoadTests(RunCase):
    """arf-8: Keep going, from the first event to the end, with words typed at it."""

    @staticmethod
    def to_the_card(engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("say", text="One.")
        engine.call("write_file", name="a.md", text="a")
        engine.end_turn("Waiting.")

    def test_words_and_no_click_the_model_hears_what_happened(self) -> None:
        def script(engine: FakeEngine) -> None:
            self.to_the_card(engine)
            engine.wait_message()
            engine.end_turn("Noted.")
            engine.wait_message()
            engine.call("finish", summary="Written so far.", outcome="done")
            engine.end_turn()

        run = self.go(script, agent={"max_turns": 2}, timing=replace(FAST, keep_going_s=3.0))
        question = self.question()
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("yes keep going"), "sent")
        self.assertEqual(run.click_awaited(), question)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_FINISHED, "Written so far."))
        self.assertEqual(self.engine.messages[1:], ["The owner says: yes keep going", ar.WRAP_UP_NO_CLICK])
        self.assertNotIn("did not answer", " ".join(self.engine.messages))
        self.assert_clean()

    def test_and_a_model_that_never_reports(self) -> None:
        def script(engine: FakeEngine) -> None:
            self.to_the_card(engine)
            engine.wait_message()
            engine.end_turn("Noted.")
            time.sleep(30)

        run = self.go(script, agent={"max_turns": 2}, timing=replace(FAST, keep_going_s=3.0, wrap_up_s=0.1))
        self.question()
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("yes keep going"), "sent")
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_NO_CLICK, ar.NO_CLICK_WORDS))
        self.assert_clean()

    def test_ask_owner_s_time_running_out_is_no_answer_whatever_came_before(self) -> None:
        """Words answer ask_owner's: when its time runs out, no words came."""
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?")
            engine.end_turn("Asked.")
            time.sleep(30)

        self.go(script, timing=replace(FAST, question_s=0.1, wrap_up_s=0.1))
        self.assertEqual(self.end().code, ar.C_NO_ANSWER)

    def test_no_keep_going_while_another_wait_is_on(self) -> None:
        """A tool at the budget while a question waits: the question stays, and only one was asked. (rf-8: the call itself
        is held now, with words saying why, and "Keep going?" comes when the question has gone.)"""
        got: dict[str, Any] = {}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            got["written"] = engine.call("write_file", name="a.md", text="a")     # the second message: past the budget
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.end_turn("Done.")

        run = self.go(script, agent={"max_turns": 1})
        question = self.question()
        self.until(lambda: "written" in got)
        self.assertEqual(run.waiting, question)
        self.assertEqual([event.question.kind for event in self.of(ar.E_QUESTION)], [ar.Q_TASK])
        self.assertEqual(self.engine.text_of(got["written"]), ar.BUDGET_AT_A_WAIT_TEXT)
        self.assertEqual(self.folder.listing(), [])
        self.assertEqual(run.owner_says("Blue"), "answered")
        run.stop()


class ThePcSleepsTests(RunCase):
    """Nothing works and nobody can answer while the PC sleeps: no clock of a task counts that time. In the app ``clock`` and
    ``real_clock`` are one clock, which counts through a sleep."""

    def clocks(self) -> tuple[list[float], Any]:
        now = [1000.0]

        def jump(seconds: float, *, real: bool = True) -> None:
            now[0] += seconds
            if real:
                real_now[0] += seconds
            time.sleep(0.15)                                         # the run looks at its clocks

        real_now = [5000.0]
        self.real_now = real_now
        return now, jump

    def test_a_card_does_not_run_out_while_the_pc_sleeps(self) -> None:
        now, jump = self.clocks()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Which town?")
            engine.end_turn("Asked.")
            threading.Event().wait(30)

        run = self.go(script, clock=lambda: now[0],
                      timing=replace(FAST, init_s=1e9, question_s=60.0, real_clock=lambda: self.real_now[0]))
        self.question()
        jump(2000.0)                                                 # past its time, all of it asleep
        self.assertIsNotNone(run.waiting)
        self.assertEqual([event for event in self.of(ar.E_QUESTION_GONE) if event.code == ar.C_NO_ANSWER], [])
        jump(61.0, real=False)                                       # awake: its time runs on, and ends as ever
        self.until(lambda: [event for event in self.of(ar.E_QUESTION_GONE) if event.code == ar.C_NO_ANSWER])
        run.stop("test")

    def test_the_budget_does_not_count_a_sleep(self) -> None:
        now, jump = self.clocks()
        gate = threading.Event()
        self.addCleanup(gate.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            gate.wait(15)

        run = self.go(script, clock=lambda: now[0], timing=replace(FAST, init_s=1e9, real_clock=lambda: self.real_now[0]))
        self.until(lambda: self.kinds(ar.E_SAY))
        before = run.budget.worked_s
        jump(2000.0)
        self.assertLess(run.budget.worked_s, before + 1.0)
        jump(30.0, real=False)                                       # awake: this counts
        self.assertGreaterEqual(run.budget.worked_s, before + 30.0)
        run.stop("test")
        gate.set()

    def test_a_short_gap_is_no_sleep(self) -> None:
        now, jump = self.clocks()
        gate = threading.Event()
        self.addCleanup(gate.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            gate.wait(15)

        run = self.go(script, clock=lambda: now[0], timing=replace(FAST, init_s=1e9, real_clock=lambda: self.real_now[0]))
        self.until(lambda: self.kinds(ar.E_SAY))
        before = run.budget.worked_s
        jump(30.0)
        self.assertGreaterEqual(run.budget.worked_s, before + 30.0)
        run.stop("test")
        gate.set()


class FileCardClockTests(RunCase):
    """arf-7 at the run: what the click does to its clock. (FileCardRoadTests: through the real desktop.)"""

    def waits_for_a_message(self, told: list[str], gate: threading.Event) -> Any:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            gate.wait(10)
            engine.end_turn("Waiting.")
            told.append(engine.wait_message())
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        return script

    def test_from_the_click_on_only_the_card_s_end_speaks(self) -> None:
        """Far past the card's own time, with the batch at work and its rows ending, nothing false is said. (rf-8: this
        pinned no end at all: it now moves the clock to just inside the watch, and the watch has its own test.)"""
        told: list[str] = []
        gate = threading.Event()
        self.addCleanup(gate.set)
        clock = [1000.0]
        run = self.go(self.waits_for_a_message(told, gate), clock=lambda: clock[0],
                      timing=replace(FAST, init_s=1e9, card_s=60.0, at_work_s=1000.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        gate.set()
        self.until(lambda: run._idle)
        self.assertTrue(run.card_at_work("card1"))
        self.assertIsNone(run.click_awaited())                  # no click is awaited now: words are only words
        self.assertFalse(run.card_at_work("another"))
        clock[0] += 999.0                                       # far past its time, with the batch at work
        time.sleep(0.1)
        self.assertEqual(told, [])
        self.assertIsNotNone(run.waiting)
        self.assertEqual([event for event in self.of(ar.E_QUESTION_GONE) if event.code == ar.C_NO_ANSWER], [])
        self.assertTrue(run.card_ended("card1", "Jarvis: the owner clicked the card. Done 1 of 1."))
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(told, ["Jarvis: the owner clicked the card. Done 1 of 1."])

    def at_work(self, told: list[str], *, at_work_s: float = 1000.0, **agent: Any) -> tuple[ar.AgentRun, list[float]]:
        gate = threading.Event()
        self.addCleanup(gate.set)
        clock = [1000.0]
        run = self.go(self.waits_for_a_message(told, gate), clock=lambda: clock[0],
                      timing=replace(FAST, init_s=1e9, card_s=60.0, at_work_s=at_work_s), **agent)
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        gate.set()
        self.until(lambda: run._idle)
        self.assertTrue(run.card_at_work("card1"))
        return run, clock

    def test_the_watch_on_a_batch_is_thirty_minutes_by_default(self) -> None:
        self.assertEqual(ar.BATCH_QUIET_MINUTES, 30)
        self.assertEqual(ar.Timing().at_work_s, 30 * 60.0)

    def test_no_row_ended_for_its_while_the_task_goes_on_in_true_words(self) -> None:
        """rf-8: a batch that does not come back: the wait ends, the model and the owner hear the truth, and nothing here
        touches the batch or says what it changed. No limbo, and no "nothing was changed"."""
        told: list[str] = []
        run, clock = self.at_work(told)
        clock[0] += 999.0
        time.sleep(0.1)
        self.assertIsNotNone(run.waiting)
        clock[0] += 2.0
        self.until(lambda: told)
        self.assertEqual(told, [ar.BATCH_QUIET_TEXT])
        self.assertIsNone(run.waiting)
        self.assertIn("still at work", told[0])
        self.assertIn("card has the truth", told[0])
        self.assertNotIn("nothing was changed", told[0].casefold())
        notices = [event for event in self.of(ar.E_NOTICE) if event.code == ar.C_BATCH_QUIET]
        self.assertEqual([event.text for event in notices], [ar.BATCH_QUIET_WORDS])
        self.assertIn("still at work", ar.BATCH_QUIET_WORDS)
        self.assertNotIn("nothing was changed", ar.BATCH_QUIET_WORDS.casefold())
        gone = self.of(ar.E_QUESTION_GONE)[-1]
        self.assertEqual(gone.text, ar.HOW_BATCH_QUIET)
        self.assertEqual(gone.code, "")                         # no expired card: the owner's card stays up
        self.assertFalse(run.card_ended("card1", "Jarvis: the owner clicked the card. Done 1 of 1."))   # late: heard by none
        self.assertEqual(self.end().outcome, "done")            # the model finishes: its turn goes on
        self.assertEqual(told, [ar.BATCH_QUIET_TEXT])

    def test_a_row_that_ended_starts_the_watch_again(self) -> None:
        told: list[str] = []
        run, clock = self.at_work(told)
        for _row in range(5):                                   # five rows, each a long while apart: well past the watch
            clock[0] += 900.0
            time.sleep(0.05)
            self.assertTrue(run.card_progress("card1"))
        self.assertFalse(run.card_progress("another"))
        self.assertIsNotNone(run.waiting)
        self.assertEqual(told, [])
        clock[0] += 1001.0
        self.until(lambda: told)
        self.assertEqual(told, [ar.BATCH_QUIET_TEXT])
        self.assertFalse(run.card_progress("card1"))            # nothing waits on it now

    def test_progress_means_nothing_until_a_batch_is_at_work(self) -> None:
        told: list[str] = []
        gate = threading.Event()
        self.addCleanup(gate.set)
        run = self.go(self.waits_for_a_message(told, gate), timing=replace(FAST, init_s=1e9, card_s=60.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        self.assertFalse(run.card_progress("card1"))

    def calling_all_along(self, calls: int) -> tuple[Any, list[dict[str, Any]], threading.Event, threading.Event]:
        results: list[dict[str, Any]] = []
        go, again = threading.Event(), threading.Event()
        self.addCleanup(go.set)
        self.addCleanup(again.set)

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            go.wait(10)
            for _call in range(calls):
                results.append(engine.call("update_plan", found="", steps=[{"text": "A step", "status": "doing"}]))
            engine.end_turn("I'll wait.")
            engine.wait_message()                               # what came of the wait
            again.set()
            results.append(engine.call("update_plan", found="", steps=[{"text": "A step", "status": "doing"}]))
            engine.end_turn("Waiting.")
            engine.wait_message()                               # Keep going
            results.append(engine.call("finish", summary="Done.", outcome="done"))
            engine.end_turn("Done.")

        return play, results, go, again

    def test_the_budget_is_asked_while_a_batch_is_at_work(self) -> None:
        """rf-8: a model that goes on working while a card or a batch is up reaches its turns: what it calls then is held,
        with words to end its turn and wait; "Keep going?" comes when the wait ends, not over the card."""
        play, results, go, again = self.calling_all_along(6)
        run = self.go(play, agent={"max_turns": 2}, timing=replace(FAST, keep_going_s=30.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        self.assertTrue(run.card_at_work("card1"))
        go.set()
        self.until(lambda: len(results) == 6)
        self.assertFalse(results[0].get("isError"))
        self.assertEqual(FakeEngine.text_of(results[-1]), ar.BUDGET_AT_A_WAIT_TEXT)
        held = [event for event in self.of(ar.E_TOOL_END) if event.result.code == ar.HELD]
        self.assertGreaterEqual(len(held), 3)
        self.assertEqual(held[-1].result.note, ar.BUDGET_HELD_NOTE)
        self.assertEqual([event.question.kind for event in self.of(ar.E_QUESTION)], [ar.Q_CARD])   # no Keep going yet
        self.assertIsNotNone(run.waiting)
        self.assertTrue(run.card_ended("card1", "Jarvis: the owner clicked the card. Done 1 of 1."))
        self.assertTrue(again.wait(10))
        self.until(lambda: len(self.of(ar.E_QUESTION)) == 2)
        question = self.of(ar.E_QUESTION)[-1].question
        self.assertEqual(question.kind, ar.Q_KEEP_GOING)
        self.until(lambda: len(results) == 7)
        self.assertEqual(FakeEngine.text_of(results[-1]), ar.PAUSED_TEXT)
        self.assertTrue(run.answer(question.id, ar.KEEP_GOING_CHOICES[0]))
        self.assertEqual(self.end().outcome, "done")

    def test_the_budget_at_a_firewall_card_and_a_question_too(self) -> None:
        for ask in (lambda run: run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), lambda _allowed: None),
                    lambda run: run._tool_question("Which one?", ())):
            with self.subTest():
                self.setUp()
                play, results, go, _again = self.calling_all_along(6)
                run = self.go(play, agent={"max_turns": 2})
                self.until(lambda: self.kinds(ar.E_STARTED))
                self.assertTrue(ask(run))
                go.set()
                self.until(lambda: len(results) == 6)
                self.assertEqual(FakeEngine.text_of(results[-1]), ar.BUDGET_AT_A_WAIT_TEXT)
                self.assertEqual(len(self.of(ar.E_QUESTION)), 1)
                run.stop("test")
                self.end()

    def test_the_minutes_too_and_finish_is_never_held_by_it(self) -> None:
        told: list[str] = []
        results: list[dict[str, Any]] = []
        go = threading.Event()
        self.addCleanup(go.set)
        clock = [1000.0]

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            go.wait(10)
            results.append(engine.call("update_plan", found="", steps=[{"text": "A step", "status": "doing"}]))
            results.append(engine.call("finish", summary="Done.", outcome="partial"))
            engine.end_turn("Done.")
            told.append("over")

        run = self.go(play, agent={"max_minutes": 1}, clock=lambda: clock[0], timing=replace(FAST, init_s=1e9))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run.ask_web("Jarvis wants to open this", ("Allow", "Don't"), lambda _allowed: None))
        clock[0] += 120.0
        go.set()
        self.until(lambda: told)
        self.assertEqual(FakeEngine.text_of(results[0]), ar.BUDGET_AT_A_WAIT_TEXT)
        self.assertNotIn(ar.HELD, FakeEngine.text_of(results[1]))
        self.assertEqual(self.end().outcome, "partial")

    def test_a_batch_that_ran_nothing_and_the_card_waits_on_has_its_time_again(self) -> None:
        told: list[str] = []
        gate = threading.Event()
        self.addCleanup(gate.set)
        clock = [1000.0]
        run = self.go(self.waits_for_a_message(told, gate), clock=lambda: clock[0],
                      timing=replace(FAST, init_s=1e9, card_s=60.0, at_work_s=200_000.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        gate.set()
        self.until(lambda: run._idle)
        self.assertTrue(run.card_at_work("card1"))
        clock[0] += 100_000.0
        time.sleep(0.05)
        self.assertTrue(run.card_at_work("card1", False))
        self.assertIsNotNone(run.click_awaited())
        time.sleep(0.05)
        self.assertIsNotNone(run.waiting)                       # 60 s from now
        clock[0] += 61.0
        self.until(lambda: told)
        self.assertEqual(told, [ar.CARD_UNANSWERED_TEXT])
        self.end()

    def test_words_under_the_limit_while_its_batch_works_are_told_they_wait(self) -> None:
        """No click is awaited then, so the app says nothing of a card: the run's own words say what came of them."""
        gate = threading.Event()
        self.addCleanup(gate.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            gate.wait(10)
            engine.emit(rate_limit_event(resets_at=RESET.timestamp()))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            time.sleep(30)

        run = self.go(script, now=lambda: WHEN, timing=replace(FAST, card_s=60.0))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        gate.set()
        self.until(lambda: run._paused and run._idle)
        held = [event for event in self.of(ar.E_NOTICE) if event.text == ar.HELD_WORDS]
        self.assertEqual(run.owner_says("while it waits"), "held")
        self.assertEqual([event for event in self.of(ar.E_NOTICE) if event.text == ar.HELD_WORDS], held)   # the app says it
        self.assertTrue(run.card_at_work("card1"))
        self.assertEqual(run.owner_says("while it works"), "held")
        self.assertEqual(len([event for event in self.of(ar.E_NOTICE) if event.text == ar.HELD_WORDS]), 1)
        run.stop()

    def test_a_card_nobody_clicked_keeps_its_time(self) -> None:
        told: list[str] = []
        gate = threading.Event()
        self.addCleanup(gate.set)
        run = self.go(self.waits_for_a_message(told, gate), timing=replace(FAST, card_s=0.2))
        self.until(lambda: self.kinds(ar.E_STARTED))
        self.assertTrue(run._tool_card("card1"))
        gate.set()
        self.end()
        self.assertEqual(told, [ar.CARD_UNANSWERED_TEXT])


class UsageTests(RunCase):
    def test_every_kind_of_input_is_counted_and_shown(self) -> None:
        usage = {"input_tokens": 24, "cache_read_input_tokens": 90_000, "cache_creation_input_tokens": 4_000,
                 "output_tokens": 700}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.", usage=usage)

        self.go(script)
        end = self.end()
        self.assertEqual((end.input_tokens, end.cache_read_tokens, end.cache_write_tokens, end.output_tokens),
                         (24, 90_000, 4_000, 700))
        self.assertEqual(agent_ui.tokens_text(end), "94,024 in (90,000 from cache) / 700 out")

    def test_each_turn_adds_up_as_the_real_stream_reports_it(self) -> None:
        """The real stream's usage is per result, as tests/fixtures' sample shows: it adds up over a task's turns."""
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.end_turn("One.", usage={"input_tokens": 8, "cache_read_input_tokens": 7658,
                                           "cache_creation_input_tokens": 1168, "output_tokens": 530})
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Two.", usage={"input_tokens": 6, "cache_read_input_tokens": 5199,
                                           "cache_creation_input_tokens": 201, "output_tokens": 213})

        self.go(script)
        self.assertEqual(agent_ui.tokens_text(self.end()), "14,240 in (12,857 from cache) / 743 out")

    def test_no_cache_says_nothing_of_one(self) -> None:
        end = ar.AgentEnd("done", "finished", input_tokens=24, output_tokens=700)
        self.assertEqual(agent_ui.tokens_text(end), "24 in / 700 out")

    def test_the_end_event_and_a_turn_carry_them(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.end_turn("One.", usage={"input_tokens": 1, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3,
                                           "output_tokens": 4})
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        self.go(script)
        self.end()
        last = self.of(ar.E_USAGE)[-1]
        self.assertEqual((last.cache_read_tokens, last.cache_write_tokens), (2, 3))


class StopAfterFinishRunTests(RunCase):
    def reported(self) -> ar.AgentRun:
        hold = threading.Event()
        self.addCleanup(hold.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="The whole report, every word of it.", outcome="done", outputs=[])
            hold.wait(10)

        run = self.go(script)
        self.until(lambda: run.reported)
        return run

    def test_the_report_and_its_outcome_stand(self) -> None:
        run = self.reported()
        run.stop("stop")
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary, end.own_words),
                         ("done", ar.C_FINISHED, "The whole report, every word of it.", False))
        self.assertTrue(self.engine.killed)                     # the process goes all the same
        run.stop("again")
        self.assertEqual(run.ended, end)

    def test_a_turn_off_keeps_no_report(self) -> None:
        run = self.reported()
        run.stop("turned off", keep_report=False)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.own_words), (ar.OUTCOME_STOPPED, ar.C_STOPPED, True))
        self.assertNotIn("whole report", end.summary)
        self.assertEqual(end.outputs, ())
        self.assertTrue(self.engine.killed)


class AnswersToTheModelsQuestionTests(RunCase):
    """rf-3 at the run: words typed in answer to a question the model wrote turn the firewall on and see no address, by
    either way in; a click on one of its choices does neither, and words with no question waiting neither."""

    EVIL = "https://evil.example/collect?c=493021"

    def asked(self) -> tuple[ar.AgentRun, WebFirewall, ar.OwnerQuestion]:
        wall = WebFirewall(web.AddressBook())

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Which town?", choices=["Leeds", "York"])
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, firewall=wall)
        return run, wall, self.question()

    def test_words_typed_to_the_run(self) -> None:
        run, wall, _question = self.asked()
        self.assertFalse(wall.private)
        self.assertEqual(run.owner_says(f"493021, and see {self.EVIL}"), "answered")
        self.assertTrue(wall.private)
        self.assertFalse(wall.book.seen(self.EVIL))
        self.assertEqual(self.end().outcome, "done")

    def test_words_that_come_as_its_answer(self) -> None:
        run, wall, question = self.asked()
        self.assertTrue(run.answer(question.id, f"493021, and see {self.EVIL}"))
        self.assertTrue(wall.private)
        self.assertFalse(wall.book.seen(self.EVIL))
        self.assertEqual(self.end().outcome, "done")

    def test_a_click_on_a_choice_does_not(self) -> None:
        run, wall, question = self.asked()
        self.assertTrue(run.answer(question.id, "York"))
        self.assertFalse(wall.private)
        self.assertEqual(self.end().outcome, "done")

    def after_a_line(self, line: str) -> tuple[Any, Any]:
        """A run that has said ``line``, nobody having typed anything, and waits for a message."""
        wall = WebFirewall(web.AddressBook())
        said = threading.Event()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text=line)
            said.set()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, firewall=wall)
        self.assertTrue(said.wait(15))
        return run, wall

    def test_an_ordinary_follow_up_with_nothing_asked_does_not(self) -> None:
        run, wall = self.after_a_line("Reading three reviews now.")
        address = "https://www.example.org/hours"
        self.assertEqual(run.owner_says(f"only ones open on Sundays, and see {address}"), "sent")
        self.assertFalse(wall.private)
        self.assertTrue(wall.book.seen(address))
        self.assertEqual(self.end().outcome, "done")

    def test_a_message_after_a_line_that_asks_does(self) -> None:
        """A line the model said can ask as well as a question can."""
        for line in ("What was the reference on that letter?", "Please type the reference on that letter."):
            with self.subTest(line=line):
                run, wall = self.after_a_line(line)
                self.assertEqual(run.owner_says(f"AB-12, and see {self.EVIL}"), "sent")
                self.assertTrue(wall.private)
                self.assertFalse(wall.book.seen(self.EVIL))
                self.assertEqual(self.end().outcome, "done")

    def test_a_message_after_the_model_s_own_text_that_asks_does(self) -> None:
        """Live shows that text, so it can ask too."""
        wall = WebFirewall(web.AddressBook())

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.assistant([{"type": "text", "text": "What was the reference on that letter?"}])
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, firewall=wall)
        self.until(lambda: self.kinds(ar.E_TEXT))
        self.assertEqual(run.owner_says(f"AB-12, and see {self.EVIL}"), "sent")
        self.assertTrue(wall.private)
        self.assertFalse(wall.book.seen(self.EVIL))
        self.assertEqual(self.end().outcome, "done")

    def asking_then_plain_words(self, way: str) -> None:
        """What turns the firewall on is the asking, by a line it said or by its own text: these words plainly hold
        nothing private (EVIL's number in them would turn it on by itself)."""
        address = "https://evil.example/collect"
        words = f"AB-12, and see {address}"
        self.assertFalse(holds_something_private(words))
        wall = WebFirewall(web.AddressBook())

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            if way == "say":
                engine.call("say", text="What was the reference on that letter?")
            else:
                engine.assistant([{"type": "text", "text": "What was the reference on that letter?"}])
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, firewall=wall)
        self.until(lambda: self.kinds(ar.E_SAY if way == "say" else ar.E_TEXT))
        self.assertEqual(run.owner_says(words), "sent")
        self.assertTrue(wall.private)
        self.assertFalse(wall.book.seen(address))
        self.assertEqual(self.end().outcome, "done")

    def test_after_a_line_that_asks_words_holding_nothing_private_do_too(self) -> None:
        self.asking_then_plain_words("say")

    def test_after_the_model_s_own_text_that_asks_words_holding_nothing_private_do_too(self) -> None:
        self.asking_then_plain_words("text")

    def test_a_message_that_plainly_holds_something_private_does(self) -> None:
        run, wall = self.after_a_line("Reading three reviews now.")
        self.assertEqual(run.owner_says(f"my code is 482913, and see {self.EVIL}"), "sent")
        self.assertTrue(wall.private)
        self.assertFalse(wall.book.seen(self.EVIL))
        self.assertEqual(self.end().outcome, "done")


class TheFirstSecondsTests(RunCase):
    """rf-7: words typed before the goal has gone in go in right after it, in order; none lost; and "Passing that on" is
    true. The run's own first seconds: a real `claude auth status`, then the process connecting."""

    @staticmethod
    def finishing_after(messages: int, script_ended: list[str] | None = None) -> Any:
        def script(engine: FakeEngine) -> None:
            for _message in range(messages):
                engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.")

        return script

    def test_words_typed_while_the_preflight_runs(self) -> None:
        from briefing_reader.agent.runner import AgentEngine

        began, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        real = AgentEngine.preflight

        def slow_preflight(engine: Any) -> Any:
            began.set()
            gate.wait(10)
            return real(engine)

        with mock.patch.object(AgentEngine, "preflight", slow_preflight):
            run = self.go(self.finishing_after(3))
            self.assertTrue(began.wait(5))
            self.assertIsNone(run._process)                     # there is no process to send anything to yet
            self.assertEqual(run.owner_says("and keep it to one page"), "sent")
            self.assertEqual(run.owner_says("in plain words"), "sent")
            gate.set()
            end = self.end()
        self.assertEqual(end.outcome, "done")
        self.assertIn(GOAL, self.engine.messages[0])
        self.assertEqual(self.engine.messages[1:], ["The owner says: and keep it to one page",
                                                    "The owner says: in plain words"])

    def test_words_typed_while_it_connects(self) -> None:
        class Slow(FakeEngine):
            hold = threading.Event()

            def connect(self) -> None:
                self.hold.wait(10)
                super().connect()

        process = Slow(self.finishing_after(3))
        self.addCleanup(process.hold.set)
        run = self.go(process)
        self.until(lambda: run._process is process)
        self.assertEqual(run.owner_says("and keep it to one page"), "sent")
        self.assertEqual(run.owner_says("in plain words"), "sent")
        self.assertEqual(process.sent, [])                      # nothing before the goal
        process.hold.set()
        end = self.end()
        self.assertEqual(end.outcome, "done")
        self.assertIn(GOAL, process.messages[0])
        self.assertEqual(process.messages[1:], ["The owner says: and keep it to one page",
                                                "The owner says: in plain words"])
        self.assertEqual(run._unreplayed, 0)                    # each went in and showed as delivered, the goal's too

    def test_the_licence_of_those_words_stands_like_any_message(self) -> None:
        wall = WebFirewall(web.AddressBook())
        began, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        class Slow(FakeEngine):
            def connect(self) -> None:
                began.set()
                gate.wait(10)
                super().connect()

        run = self.go(Slow(self.finishing_after(2)), firewall=wall)
        self.assertTrue(began.wait(5))
        run.owner_says("and mute")
        self.assertIn("and mute", wall.messages())
        gate.set()
        self.end()

    def test_a_stop_in_those_seconds_ends_it_with_nothing_sent(self) -> None:
        from briefing_reader.agent.runner import AgentEngine

        began, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        real = AgentEngine.preflight

        def slow_preflight(engine: Any) -> Any:
            began.set()
            gate.wait(10)
            return real(engine)

        with mock.patch.object(AgentEngine, "preflight", slow_preflight):
            run = self.go(self.finishing_after(2))
            self.assertTrue(began.wait(5))
            run.owner_says("and keep it to one page")
            run.stop("stop")
            self.assertEqual(run.owner_says("too late"), "refused")
            gate.set()
            self.assertEqual(self.end().outcome, ar.OUTCOME_STOPPED)
        self.assertEqual(self.engine.messages, [])


class FileCardRoadTests(ThingsApp):
    """arf-7 through the real desktop: the owner's click late in the card's time, its batch running past the run's."""

    GOAL = "move my letter from Downloads into Documents"

    def script(self) -> Script:
        def steps(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}

        return Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                      ("propose", "propose_files", steps), ("wait",))

    @staticmethod
    def until_its_end(told: list[str]) -> Any:
        """A model that proposes the card, says it heard what it is told meanwhile, and reports at the card's end."""
        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            roots = engine.call("files_list")
            down = engine.call("files_list", place="Downloads")
            engine.call("propose_files", steps=[{"skill": "move_files", "files": [ref_in(down, "letter.txt")],
                                                 "to": ref_in(roots, "Documents")}])
            engine.end_turn("I'll wait.")
            while True:
                told.append(engine.wait_message())
                if told[-1].startswith("Jarvis:"):
                    break
                engine.end_turn("Noted.")
            engine.call("finish", summary=DONE, outcome="done")
            engine.end_turn("Done.")

        return play

    def test_its_time_passes_while_the_batch_is_at_work_and_nothing_false_is_said(self) -> None:
        release, in_call = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                in_call.set()
                release.wait(15.0)

        told: list[str] = []
        expired: list[Any] = []
        at_work: list[bool] = []
        self.go(self.until_its_end(told), timing=replace(PATIENT, card_s=1.0))
        self.c.agent.questionExpired.connect(expired.append)
        self.c.agent.cardAtWork.connect(at_work.append)
        self.tidy_entry()
        self.wait_waiting()
        self.fake.on_call = slow
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10), "the batch is at work")
        settle()
        self.assertEqual(at_work, [True])
        self.assertEqual(self.feed.waits(), 0)                       # no decision waits now
        self.assertEqual(self.feed.activity(), agent_feed.AT_WORK_ACTIVITY)
        self.assertRegex(self.feed.panel().content().head, r"^WORKING")
        self.assertEqual(self.feed.panel().content().tone, hud_overlay.TONE_CYAN)
        self.type_it("how is it going?")                             # no card is awaited: only words
        self.assertEqual(self.c.ask.bar.status(), "Passing that on, sir.")
        time.sleep(1.5)                                              # past the run's time for the card
        settle()
        self.assertTrue(self.desktop.busy())
        self.assertEqual(expired, [])
        self.assertNotIn(ar.CARD_UNANSWERED_TEXT, told)
        self.assertNotIn("nothing was changed", self.c.ask.bar.status())
        self.assertIsNotNone(self.c.agent.run().waiting)
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10))
        end = self.ended()
        self.assertEqual((end.outcome, end.summary), ("done", DONE))
        self.assertEqual(told, ["The owner says: how is it going?", "Jarvis: the owner clicked the card. Done 1 of 1."])
        self.assertFalse(self.on_disk(LETTER))
        self.assertEqual(self.desktop.tidy().stage, desktop_tidy.DONE)

    def test_a_batch_that_does_not_come_back_the_task_goes_on_in_true_words(self) -> None:
        """rf-8, the whole road: the move's OS call does not come back. Each other time is short, and the watch ends the
        wait in true words; the batch is not touched, its card still has the truth, and its Stop still works."""
        release, in_call = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                in_call.set()
                release.wait(30.0)

        told: list[str] = []
        said: list[str] = []
        self.go(self.until_its_end(told), timing=replace(PATIENT, card_s=1.0, at_work_s=1.0))
        self.c.agent.noticed.connect(lambda text, _tone: said.append(text))
        expired: list[Any] = []
        self.c.agent.questionExpired.connect(expired.append)
        self.tidy_entry()
        self.wait_waiting()
        self.fake.on_call = slow
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10), "the batch is at work")
        end = self.ended()
        settle()
        self.assertEqual((end.outcome, end.summary), ("done", DONE))
        self.assertEqual(told, [ar.BATCH_QUIET_TEXT])
        self.assertEqual(said, [ar.BATCH_QUIET_WORDS.replace("{sir}", "sir")])
        self.assertEqual(expired, [])
        for words in (*told, *said, self.c.ask.bar.status()):
            self.assertNotIn("nothing was changed", words.casefold())
        self.assertTrue(self.desktop.busy())                         # the batch itself was not killed
        self.assertFalse(self.desktop.tidy().stopped())
        self.assertTrue(self.on_disk(LETTER))
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10))
        settle()
        self.assertFalse(self.on_disk(LETTER))                       # it carried on to its end, with its stamp
        self.assertEqual(self.desktop.tidy().stage, desktop_tidy.DONE)

    def test_a_row_that_ended_restarts_the_watch_on_the_batch(self) -> None:
        """The real desktop's rows: tidyProgress reaches the run."""
        self.go(self.until_its_end([]), timing=replace(PATIENT, card_s=1.0, at_work_s=600.0))
        run = self.c.agent.run()
        self.tidy_entry()
        self.wait_waiting()
        heard = mock.Mock(wraps=run.card_progress)
        with mock.patch.object(run, "card_progress", heard):
            self.press_tidy(desktop_tidy.GO_KEY)
        self.assertEqual(self.ended().summary, DONE)
        heard.assert_called()

    def test_a_batch_that_ran_nothing_leaves_the_card_waiting_with_its_clock(self) -> None:
        self.go(self.script())
        self.tidy_entry()
        self.wait_waiting()
        heard: list[bool] = []
        self.c.agent.cardAtWork.connect(heard.append)
        state = self.desktop.tidy()
        run = self.c.agent.run()
        self.desktop.tidyAtWork.emit(state, True)
        settle()
        self.assertEqual(self.feed.waits(), 0)
        self.desktop.tidyAtWork.emit(state, False)
        settle()
        self.assertEqual(heard, [True, False])
        self.assertEqual(self.feed.waits(), 1)
        self.assertEqual(self.feed.activity(), agent_feed.WAITING_ACTIVITY)
        self.assertIsNotNone(run.click_awaited())
        self.c.agent.stop("test")
        self.ended()

    def test_stopped_before_anything_ran_the_card_waits_on_with_its_clock(self) -> None:
        """The click starts the batch, and its Stop comes before the desktop thread got to the first row."""
        told: list[str] = []
        self.go(self.until_its_end(told), timing=replace(PATIENT, card_s=4.0))
        self.tidy_entry()
        self.wait_waiting()
        heard: list[bool] = []
        self.c.agent.cardAtWork.connect(heard.append)
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.desktop._worker.submit(lambda: hold.wait(15.0))         # the desktop thread is busy for the moment
        self.desktop._start_tidy(undo=False)
        self.assertEqual(heard, [True])
        self.desktop._stop_tidy("stop")
        hold.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10))
        settle()
        self.assertEqual(self.desktop.tidy().stage, desktop_tidy.WAITING)
        self.assertEqual(heard, [True, False])
        self.assertEqual(self.feed.waits(), 1)                       # it waits for the owner again, in amber
        self.assertIsNotNone(self.c.agent.run().click_awaited())
        self.assertEqual(self.fake.asked("move_item"), [])
        self.assertEqual(self.ended().summary, DONE)                 # and its time came: it did not stick
        self.assertEqual(told, [ar.CARD_UNANSWERED_TEXT])
        self.assertTrue(self.on_disk(LETTER))

    def test_a_stop_while_it_works_stops_it_after_the_row_on_its_way(self) -> None:
        """Two files: the second row never starts, and what was done keeps its stamp."""
        second = f"{DOWNLOADS}\\second letter.txt"
        release, in_call = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                in_call.set()
                release.wait(15.0)

        def steps(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "our letter.txt"),
                                                                ref_in(got["down"], "second letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}

        script = Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                        ("propose", "propose_files", steps), ("wait",))
        self.make_things(script, timing=PATIENT)
        self.fake.make_file(second, b"more words")
        self.fake.make_file(f"{DOWNLOADS}\\our letter.txt", b"words")
        self.start()
        self.tidy_entry()
        self.fake.on_call = slow
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10), "the batch is at work")
        self.c.agent.stop("stop")
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy(), timeout=10))
        settle()
        self.ended()
        self.assertEqual(len(self.fake.asked("move_item")), 1)       # the second never started
        self.assertTrue(self.on_disk(second))
        state = self.desktop.tidy()
        self.assertTrue(state.stopped())
        self.assertEqual(len(state.done_numbers()), 1)
        self.assertTrue(state.undoable())

    @staticmethod
    def fails_on_the_limit_at_the_card(told: list[str], more: int = 1) -> Any:
        """A model whose turn fails on the plan's limit, with no time named, right after it proposed the card."""
        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            roots = engine.call("files_list")
            down = engine.call("files_list", place="Downloads")
            engine.call("propose_files", steps=[{"skill": "move_files", "files": [ref_in(down, "letter.txt")],
                                                 "to": ref_in(roots, "Documents")}])
            engine.emit(result_event("You've hit your limit \u00b7 resets 5am Mon", is_error=True))
            for _message in range(more):
                told.append(engine.wait_message(20.0))
            engine.call("finish", summary=DONE, outcome="done")
            engine.end_turn("Done.")

        return play

    def test_the_limit_with_no_time_and_the_owner_s_not_now_carries_on(self) -> None:
        """run-1, the whole road: Not now is the owner's click, and it lets the task try again."""
        told: list[str] = []
        self.go(self.fails_on_the_limit_at_the_card(told), timing=replace(PATIENT, limit_wait_s=1e9))
        self.tidy_entry()
        run = self.c.agent.run()
        self.assertTrue(wait_for(lambda: run._paused and run._idle, timeout=10), "the limit holds")
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        end = self.ended()
        self.assertEqual((end.outcome, end.summary), ("done", DONE))
        self.assertEqual(len(told), 1)
        self.assertIn("Not now", told[0])
        self.assertTrue(self.on_disk(LETTER))

    def test_the_limit_with_no_time_and_words_at_the_batch_go_in_as_it_ends(self) -> None:
        """run-1, the whole road: the owner's click starts the batch, the limit holds, they type; the batch's end lets
        their words and its end go in, in that order, and the bar never said "when the plan's limit has reset"."""
        release, in_call = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                in_call.set()
                release.wait(15.0)

        told: list[str] = []
        said: list[str] = []
        self.go(self.fails_on_the_limit_at_the_card(told, more=2), timing=replace(PATIENT, limit_wait_s=1e9))
        self.c.agent.noticed.connect(lambda text, _tone: said.append(text))
        self.tidy_entry()
        run = self.c.agent.run()
        self.assertTrue(wait_for(lambda: run._paused and run._idle, timeout=10), "the limit holds")
        self.fake.on_call = slow
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10), "the batch is at work")
        settle()
        self.type_it("carry on once it has moved")
        self.assertIn(ar.HELD_AT_WORK_WORDS.replace("{sir}", "sir"), said)
        self.assertNotIn(ar.HELD_WORDS.replace("{sir}", "sir"), said)
        self.assertEqual(told, [])
        release.set()
        end = self.ended()
        self.assertEqual((end.outcome, end.summary), ("done", DONE))
        self.assertEqual(told, ["The owner says: carry on once it has moved",
                                "Jarvis: the owner clicked the card. Done 1 of 1."])
        self.assertFalse(self.on_disk(LETTER))

    def test_a_card_that_could_not_show_after_the_model_s_turn_ended_gets_it_going_again(self) -> None:
        """run-2, the whole road: the card's hop to the GUI thread comes back "not shown" only after the endpoint gave up
        on the tool and the model ended its turn. Nothing waits and nothing is on its way: it gets its one prompt."""
        told: list[str] = []
        turned = threading.Event()
        self.addCleanup(turned.set)

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            roots = engine.call("files_list")
            down = engine.call("files_list", place="Downloads")
            told.append(engine.text_of(engine.call("propose_files", steps=[{
                "skill": "move_files", "files": [ref_in(down, "letter.txt")], "to": ref_in(roots, "Documents")}])))
            engine.end_turn("I'll carry on without it.")
            turned.set()
            told.append(engine.wait_message(20.0))
            engine.call("finish", summary=DONE, outcome="partial")
            engine.end_turn("Done.")

        def busy_gui(_run: Any, _cancelled: Any = lambda: False) -> bool:
            """The GUI thread is busy past the tool's time and the model's turn: "not shown" comes after both."""
            turned.wait(15.0)
            run = self.c.agent.run()
            deadline = time.monotonic() + 10.0                           # (this is the tool's thread: no Qt here)
            while run is not None and not run._idle and time.monotonic() < deadline:
                time.sleep(0.01)
            return False

        self.make_things(play, timing=replace(PATIENT, tool_s=1.0))
        self.c.agent._things.show_run = busy_gui
        self.start()
        end = self.ended()
        self.assertEqual((end.outcome, end.summary), ("partial", DONE))
        self.assertEqual(told[1], ar.CONTINUE_TEXT)
        self.assertIn("too long", told[0])
        self.assertTrue(self.on_disk(LETTER))


class InTheWindowTests(FeedApp):
    """arf-8: the owner back in the Jarvis window, where no card shows."""

    def keep_going(self, **harness: Any) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", found="", steps=plan("doing", "todo", "todo"))
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            told = engine.wait_message()
            while told not in (ar.KEEP_GOING_YES, ar.WRAP_UP_NO_CLICK):
                engine.end_turn("Noted.")
                told = engine.wait_message()
            engine.call("finish", summary="All of it.", outcome="done")
            engine.end_turn()

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script), **harness)
        self.c.agent._engine.agent = mock.Mock(max_turns=1, max_minutes=20, model="opus", effort="")

    def reach_card_in_the_window(self) -> None:
        self.keep_going()
        self.type_it(TYPED)
        self.started_task()
        self.asked_card()
        self.back()
        settle()

    def test_typed_yes_says_it_needs_a_click_and_how_to_reach_it(self) -> None:
        self.reach_card_in_the_window()
        self.assertFalse(self.feed.card.isVisible())
        self.assertEqual(self.reading.task_chip_text(), agent_feed.CHIP_WAITING)
        self.assertEqual(self.c._desktop_waiting.count(), 1)         # Approvals' reminder: Show me
        with mock.patch.object(agent_feed, "effective_hud_key", return_value="ctrl+alt+h"):
            self.type_it("yes keep going")
        words = "Passed on, sir, but that card needs a click: Show me in Approvals, or Ctrl+Alt+H."
        self.assertEqual(self.c.ask.bar.status(), words)
        self.assertEqual(self.c.ask.bar.status_label.text(), words)  # all of it shows: the status line cuts what is long
        self.assertIsNotNone(self.feed._question)                    # it still waits
        with mock.patch.object(agent_feed, "effective_hud_key", return_value=""):
            self.type_it("yes please")                               # no key: no word of one
        self.assertEqual(self.c.ask.bar.status(), "Passed on, sir, but that card needs a click: Show me in Approvals.")
        self.c.agent.stop("test")
        self.ended()

    def test_its_chip_goes_to_the_card_while_one_waits(self) -> None:
        self.reach_card_in_the_window()
        self.click(self.reading.task_chip)
        self.assertTrue(self.desktop.is_hud())
        self.assertTrue(self.feed.card.isVisible())
        self.press("choice:0")
        settle()
        self.assertEqual(self.ended().outcome, "done")

    def test_said_aloud_only_the_question_and_only_the_screen_says_the_way_there(self) -> None:
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", found="", steps=plan("doing", "todo", "todo"))
            stages.reach(1)
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.call("finish", summary="All of it.", outcome="done")
            engine.end_turn()

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script))
        self.c.agent._engine.agent = mock.Mock(max_turns=2, max_minutes=20, model="opus", effort="")
        self.type_it(TYPED)
        self.reached(stages, 1)
        self.back()
        settle()
        with mock.patch.object(agent_feed, "effective_hud_key", return_value="ctrl+alt+h"):
            stages.go(1)
            self.asked_card()
        reach = "Show me in Approvals, or Ctrl+Alt+H."
        question = self.feed._question.text
        self.assertIn(question, self.said())
        self.assertFalse(any(reach in line for line in self.said()))
        self.assertIn(f"{question} {reach}", self.talk())
        self.assertEqual(self.c.ask.bar.status(), f"{question} {reach}")
        self.c.agent.stop("test")
        self.ended()

    def test_on_the_hud_the_card_is_there_and_nothing_says_the_way(self) -> None:
        self.keep_going()
        self.type_it(TYPED)
        self.started_task()
        self.asked_card()
        self.assertEqual(self.feed.reach_words(), "")
        self.assertTrue(self.feed.card.isVisible())
        self.type_it("yes")
        self.assertEqual(self.c.ask.bar.status(), CLICK_SENT)
        self.c.agent.stop("test")
        self.ended()


class TypedChoiceTests(FeedApp):
    """arf-11: a question's own word is its answer, whatever the word."""

    def question(self, *choices: str, **harness: Any) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Shall I keep the old table or cancel it?", choices=list(choices))
            engine.end_turn("Asked.")
            answer = engine.wait_message()
            engine.call("finish", summary=answer, outcome="done")
            engine.end_turn()

        self.start(script, **harness)
        self.asked_card()

    def test_its_own_choice_answers_it(self) -> None:
        for typed in ("Cancel", "cancel", "Cancel.", "  CANCEL  "):
            with self.subTest(typed=typed):
                self.question("Keep", "Cancel")
                self.type_it(typed)
                end = self.ended()
                self.assertEqual(end.outcome, "done")
                self.assertEqual(end.summary, f"The owner answers your question: {' '.join(typed.split())}")

    def test_a_choice_of_stop_here_too(self) -> None:
        self.question("Stop here", "Go on")
        self.type_it("stop here")
        self.assertEqual(self.ended().summary, "The owner answers your question: stop here")

    def test_only_its_own_words_a_stop_is_still_a_stop(self) -> None:
        self.question("Keep", "Cancel")
        self.type_it("stop")
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)

    def test_cancel_with_no_such_choice_stops_it(self) -> None:
        self.question("Blue", "Red")
        self.type_it("Cancel")
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)

    def asks_twice(self, told: list[str], *choices: str) -> Any:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            for _question in range(2):
                engine.call("ask_owner", question="Shall I go on with the laptops?", choices=list(choices))
                engine.end_turn("Asked.")
                told.append(engine.wait_message())
            engine.call("finish", summary="Went on regardless.", outcome="done")
            engine.end_turn()

        return script

    def next_question(self, before: str) -> str:
        self.assertTrue(wait_for(lambda: self.c.agent.run() is not None and self.c.agent.run().waiting is not None
                                 and self.c.agent.run().waiting.id != before, timeout=10), "the next question")
        settle()
        return self.c.agent.run().waiting.id

    def test_a_second_stop_word_stops_the_task_whatever_is_offered(self) -> None:
        """run-7: a page may steer the model to offer "Stop" as a choice, to swallow every stop the owner types or says.
        The first answers the question, as its own choice; the next stops the task, whatever the question offers."""
        told: list[str] = []
        self.start(self.asks_twice(told, "Stop", "Cancel", "Carry on"))
        first = self.next_question("")
        self.type_it("Stop!")
        self.next_question(first)
        self.assertEqual(told, ["The owner answers your question: Stop!"])
        self.assertIsNone(self.c.agent.last_end)
        self.type_it("Cancel.")                                     # offered too: it stops the task all the same
        end = self.ended()
        self.assertEqual(end.outcome, ar.OUTCOME_STOPPED)
        self.assertEqual(told, ["The owner answers your question: Stop!"])

    def test_the_next_task_s_first_stop_word_answers_its_question_again(self) -> None:
        told: list[str] = []
        self.start(self.asks_twice(told, "Stop", "Carry on"))
        self.next_question("")
        self.type_it("stop")                                        # this task's own choice: its answer
        self.assertTrue(wait_for(lambda: told, timeout=10))
        self.c.agent.stop("test")
        self.ended()
        self.feed._dismiss()
        again: list[str] = []
        self.harness.factory.runner.runs.append(FakeEngine(self.asks_twice(again, "Stop", "Carry on")))
        self.assertTrue(self.c.agent.start("another task"))
        self.started_task()
        self.next_question("")
        self.type_it("Stop")
        self.assertTrue(wait_for(lambda: again, timeout=10))
        self.assertEqual(again, ["The owner answers your question: Stop"])
        self.c.agent.stop("test")

    def test_at_a_card_only_a_click_answers_its_words_are_nobody_s_answer(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            threading.Event().wait(30)

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script))
        self.c.agent._engine.agent = mock.Mock(max_turns=1, max_minutes=20, model="opus", effort="")
        self.type_it(TYPED)
        self.asked_card()
        self.type_it("Stop here")
        self.assertEqual(self.ended().outcome, ar.OUTCOME_STOPPED)   # a plain stop, never Keep going's own answer


class VoiceOnStopTests(FeedApp):
    """arf-10: a stopped task's voice stops, and nothing else the voice is at."""

    def test_a_stop_takes_what_it_says_and_nothing_else(self) -> None:
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="Reading the first review now.")
            stages.reach(1)

        self.start(script)
        self.reached(stages, 1)
        voice = self.harness.voice
        said = {text: key for _kind, text, key in voice.said}
        self.assertEqual(said["Reading the first review now."], ui_key())
        self.assertEqual(said[SAY], ui_key())                         # its spoken start too
        owner_acted, stops = voice.owner_actions, voice.stops
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertIn(ui_key(), voice.stopped_keys)
        self.assertEqual(voice.stops, stops)                          # never the whole voice: a briefing reads on
        self.assertEqual(voice.owner_actions, owner_acted)            # ... and may come back when it paused for a line
        stages.go(1)

    def test_a_task_that_ends_by_itself_says_its_report(self) -> None:
        self.start(whole_for_voice)
        self.ended()
        settle()
        self.assertNotIn(ui_key(), self.harness.voice.stopped_keys)
        self.assertIn(SUMMARY, self.said())

    def test_focus_held_lines_of_a_stopped_task_go_too(self) -> None:
        self.start(lambda engine: (engine.wait_message(), threading.Event().wait(30)))
        settle()
        self.c._focus_speech[:] = [("reply", "A task's line.", ui_key()), ("notice", "Someone else's.", "other")]
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertEqual([item[1] for item in self.c._focus_speech], ["Someone else's."])

    def test_the_real_voice_drops_only_that_key(self) -> None:
        from briefing_reader.speech import KIND_ANNOUNCE, KIND_REPLY, SpeechLane

        lane = SpeechLane()
        lane.push(KIND_REPLY, "A task's line.", ui_key())
        lane.push(KIND_ANNOUNCE, "Your briefing is ready.", "announce:x")
        lane.push(KIND_REPLY, "Another of its lines.", ui_key())
        lane.cancel_key(ui_key())
        self.assertEqual([item.text for item in lane.pending], ["Your briefing is ready."])


def ui_key() -> str:
    from briefing_reader import ui

    return ui.TASK_SAY_KEY


def whole_for_voice(engine: FakeEngine) -> None:
    engine.wait_message()
    engine.call("finish", summary=SUMMARY, outcome="done")
    engine.end_turn("Done.")


class NothingPromisedThatWontHappenTests(FeedApp):
    """arf-9, arf-12: a task that can't start says so in words, and nothing says it started."""

    def assert_refused(self, words: str, tone: str = hud.TONE_ERROR) -> None:
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        self.assertFalse(self.c.agent.running)
        self.assertEqual(self.c.ask.bar.text(), TYPED)                # the owner's words wait in the bar
        self.assertEqual(self.c.ask.bar.status(), words)
        self.assertNotIn("a task", self.c.ask.bar.meta())             # (its meta line, as its sub line)
        talk = self.reading.conversation.entries()
        self.assertEqual(talk[-1][2], words)                          # the conversation has the true words
        self.assertFalse(any("a task: Live has its steps" in sub for _who, _when, _text, sub in talk))
        self.assertNotIn(SAY, [text for _who, _when, text, _sub in talk])
        self.assertNotIn(SAY, self.said())

    def test_the_day_s_tasks_used_up_the_handover_reaches_its_refusal(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(whole), plan_stream(TASK_PLAN), agent="tasks_per_day = 1\n")
        self.type_it(TYPED)
        self.ended()
        settle()
        self.feed._dismiss()
        self.assertTrue(self.c.agent.offered())                       # only the day's count stops it: start() says so
        said = len(self.said())
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 3, timeout=10))
        words = "That's the most tasks for one day (1), sir; nothing was started."
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        third = self.runs()[2].argv
        self.assertIn("agent_task", third[third.index("--json-schema") + 1])
        self.assertFalse(self.c.agent.running)
        self.assertEqual(self.c.ask.bar.status(), words)
        self.assertEqual(self.c.ask.bar.text(), TYPED)
        self.assertEqual(self.said()[said:], [words])                 # said aloud, and nothing of a start
        self.assertEqual(self.reading.conversation.entries()[-1][2], words)

    def test_a_claude_code_too_old_for_tasks_says_so(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN))
        base = self.c.agent._engine.base
        real = base.readiness()
        with mock.patch.object(base, "readiness", return_value=replace(real, agent_missing=("--allowedTools",))):
            self.type_it(TYPED)
            self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        self.assert_refused("This Claude Code is too old for tasks, sir: update it and ask again. Nothing was started.")

    def test_a_workspace_refused_says_nothing_of_a_start(self) -> None:
        from briefing_reader.agent import workspace

        self.make_agent(plan_stream(TASK_PLAN))
        refused = workspace.WorkspaceRefused(workspace.R_ONEDRIVE, workspace.ONEDRIVE_TEXT)
        with mock.patch.object(agent_ui, "workspace_root", side_effect=refused):
            self.type_it(TYPED)
            self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
            settle()
        self.assert_refused(workspace.ONEDRIVE_TEXT)

    def test_the_old_card_says_nothing_of_a_start(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN), desktop_consent=2)
        self.type_it(TYPED)
        words = "Tasks need your OK on the updated card first, sir - nothing was started."
        self.assert_refused(words)
        self.assertTrue(self.c._desktop_consent_card.is_open())

    def test_a_task_that_may_start_still_says_it_does(self) -> None:
        self.start(whole)
        self.ended()
        self.assertIn(SAY, self.said())
        self.assertTrue(any("a task: Live has its steps" in sub for _who, _when, _text, sub
                            in self.reading.conversation.entries()))


def whole(engine: FakeEngine) -> None:
    engine.wait_message()
    engine.call("finish", summary=SUMMARY, outcome="done")
    engine.end_turn("Done.")


NEXT_ASK = {"say": "Ok.", "question": "", "lines": []}


class AfterATaskTests(AgentApp):
    """arf-23: the next Ask hears that the task ended, in Jarvis's own words, and not a word of its report."""

    def asked_next(self) -> str:
        self.type_it("which of them was the quietest?")
        self.assertTrue(wait_for(lambda: len(self.runs()) == 3, timeout=10))
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        run = self.runs()[2]
        return run.raw or run.stdin

    @staticmethod
    def line(how: str) -> str:
        return f"That task has ended ({how}). Its report is on the owner's screen and not here: say so if asked about it."

    def test_it_hears_the_line_and_nothing_of_the_report(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(whole_task), plan_stream(NEXT_ASK))
        self.type_it(TYPED)
        self.ended()
        settle()
        told = self.asked_next()
        self.assertIn(self.line("done"), told)
        self.assertLess(told.index(SAY), told.index(self.line("done")))
        self.assertNotIn(SUMMARY, told)
        self.assertNotIn("table.md", told)
        self.assertNotIn("Looking at the three now.", told)

    def test_how_it_ended_in_each_word(self) -> None:
        for outcome, how in (("done", "done"), ("partial", "partly done"), ("failed", "failed")):
            with self.subTest(outcome=outcome):
                def script(engine: FakeEngine, outcome: str = outcome) -> None:
                    engine.wait_message()
                    engine.call("finish", summary="A secret word of its report.", outcome=outcome)
                    engine.end_turn()

                self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script), plan_stream(NEXT_ASK))
                self.type_it(TYPED)
                self.ended()
                settle()
                told = self.asked_next()
                self.assertIn(self.line(how), told)
                self.assertNotIn("secret word", told)

    def test_a_stop_says_stopped(self) -> None:
        engine = FakeEngine(lambda engine: (engine.wait_message(), threading.Event().wait(30)))
        self.make_agent(plan_stream(TASK_PLAN), engine, plan_stream(NEXT_ASK))
        self.type_it(TYPED)
        self.started_task()
        self.assertTrue(wait_for(lambda: engine.messages, timeout=10))
        self.type_it("stop")
        self.ended()
        settle()
        self.assertIn(self.line("stopped"), self.asked_next())

    def test_never_on_screen_and_kept_with_the_conversation_for_a_day(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(whole_task))
        self.type_it(TYPED)
        self.ended()
        settle()
        line = self.line("done")
        self.assertNotIn(line, self.talk())                           # for Ask only: the owner has the report
        self.assertIn(line, [turn.text for turn in self.c._conversation_turns()])
        self.c._save_conversation()
        path = self.harness.config.data_dir / CONVERSATION_FILE
        kept = json.loads(path.read_text(encoding="ascii"))["turns"]
        self.assertEqual([row.get("note") for row in kept if row["text"] == line], [True])
        self.assertNotIn(SUMMARY, path.read_text(encoding="ascii"))   # (the report stays keep=False)
        self.reading.conversation.clear()
        self.c._task_notes = []
        self.c._init_conversation()                                   # as a start reads it
        self.assertNotIn(line, self.talk())
        self.assertIn(line, [turn.text for turn in self.c._conversation_turns()])
        self.c.clear_conversation()                                   # Clear conversation takes it too
        self.assertNotIn(line, [turn.text for turn in self.c._conversation_turns()])

    def test_closing_with_a_task_running_says_stopped_in_what_is_kept(self) -> None:
        self.make_agent(plan_stream(TASK_PLAN),
                        FakeEngine(lambda engine: (engine.wait_message(), threading.Event().wait(30))))
        self.type_it(TYPED)
        self.started_task()
        settle()
        self.c.shutdown()
        path = self.harness.config.data_dir / CONVERSATION_FILE
        self.assertIn(self.line("stopped"), path.read_text(encoding="ascii"))

    def test_a_later_task_reading_the_conversation_hears_it_too(self) -> None:
        got: dict[str, Any] = {}

        def later(engine: FakeEngine) -> None:
            engine.wait_message()
            got["read"] = engine.call("conversation_read")
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(whole_task), FakeEngine(later))
        self.type_it(TYPED)
        self.ended()
        settle()
        self.c.agent.last_end = None
        self.c.agent._tools = None
        self.assertTrue(self.c.agent.start("add prices to that table"))
        self.ended()
        self.assertIn("conversation_read", self.runs()[-1].argv[self.runs()[-1].argv.index("--allowedTools") + 1])

    def test_with_the_agent_off_nothing_changes(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.make("", runs=(plan_stream(NEXT_ASK), plan_stream(NEXT_ASK)))
        self.assertIsNone(self.c.agent)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None), "readiness checked")
        self.type_it("what is on today?")
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        self.type_it("and tomorrow?")
        self.assertTrue(wait_for(lambda: len(self.runs()) == 2, timeout=10))
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        told = self.runs()[1].raw or self.runs()[1].stdin
        self.assertIn("what is on today?", told)
        self.assertNotIn("That task has ended", told)
        self.assertEqual(self.c._task_notes, [])
        self.c._save_conversation()
        path = self.harness.config.data_dir / CONVERSATION_FILE
        self.assertNotIn('"note"', path.read_text(encoding="ascii"))  # the file as it was


class ConversationNoteTests(unittest.TestCase):
    def test_a_note_survives_the_file_and_a_turn_without_one_is_as_it_was(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / CONVERSATION_FILE
            store = ConversationStore(path, clock=lambda: WHEN)
            plain = Turn(ROLE_JARVIS, "Words.", WHEN, "done")
            store.save([plain, Turn(ROLE_JARVIS, "A note.", WHEN, "done", note=True)])
            rows = json.loads(path.read_text(encoding="ascii"))["turns"]
            self.assertEqual(sorted(rows[0]), ["at", "role", "text", "tone"])
            self.assertIs(rows[1]["note"], True)
            again = ConversationStore(path, clock=lambda: WHEN).turns()
            self.assertEqual([turn.note for turn in again], [False, True])

    def test_a_note_in_a_shape_jarvis_never_writes_is_left_out(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / CONVERSATION_FILE
            path.write_text(json.dumps({"version": 1, "turns": [
                {"role": "jarvis", "text": "A.", "at": WHEN.isoformat(), "tone": "done", "note": "yes"}]}), encoding="ascii")
            self.assertEqual(ConversationStore(path, clock=lambda: WHEN).turns(), ())


class ArmedOnReturnTests(FeedApp):
    """arf-16: a card that comes up with the HUD has only just appeared."""

    def waiting_in_the_window(self) -> list[float]:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.end_turn("Asked.")
            answer = engine.wait_message()
            engine.call("finish", summary=answer, outcome="done")
            engine.end_turn()

        self.start(script)
        self.asked_card()
        now = [1000.0]
        self.feed._clock = lambda: now[0]
        self.back()
        now[0] += 3600.0                                              # it came long ago
        return now

    def test_a_click_as_it_comes_up_does_nothing_and_one_after_counts(self) -> None:
        now = self.waiting_in_the_window()
        self.enter()
        card = self.feed.card
        self.assertTrue(card.isVisible())
        self.click(card.buttons()["choice:0"])                        # at once: under the pointer that just clicked
        self.assertEqual(self.desktop.waiting_count(), 1)
        now[0] += 0.5
        self.click(card.buttons()["choice:0"])                        # 0.5 s: still its guard
        self.assertEqual(self.desktop.waiting_count(), 1)
        now[0] += 0.2
        self.click(card.buttons()["choice:0"])                        # 0.7 s: that one counts
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.ended().summary, "The owner answers your question: Blue")

    def test_a_firewall_card_likewise(self) -> None:
        told: list[str] = []
        tainted = threading.Event()
        self.addCleanup(tainted.set)
        query = "Halberd contract terms"

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            tainted.wait(10)
            engine.call("web_search", query=query)
            engine.end_turn("Waiting.")
            told.append(engine.wait_message())
            engine.end_turn("Noted.")
            threading.Event().wait(30)

        browser = FakeBrowser({web.ENGINES[0].url(query): FakePage(results=[("Terms", URL, "")])})
        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script), browser=browser,
                        timing=replace(FAST, tool_s=30.0))
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: self.c.agent.firewall is not None, 10))
        self.c.agent.firewall.note_private()
        self.c.agent.firewall.taint.add(["Project Halberd"])
        self.started_task()
        settle()
        self.back()
        tainted.set()
        self.assertTrue(wait_for(lambda: self.feed.waits() == 1, timeout=10))
        settle()
        now = [1000.0]
        self.feed._clock = lambda: now[0]
        self.feed._card_at = -10_000.0                                # made while away, long ago
        self.enter()
        self.click(self.feed.card.buttons()["choice:0"])              # Allow, in that very moment: nothing
        self.assertEqual(told, [])
        self.assertEqual(self.desktop.waiting_count(), 1)
        now[0] += 0.7
        self.click(self.feed.card.buttons()["choice:0"])
        self.assertTrue(wait_for(lambda: told, timeout=10))
        self.assertEqual(told, [ar.WEB_ALLOWED_TEXT])
        self.c.agent.stop("test")
        self.ended()


class ArmedInFlightTests(FeedApp):
    """With the default motion the HUD shows well after ``hudChanged``: it is armed as it lands. arf-21 too: the watch window
    placed on the way keeps clear of the bar then."""

    MOTION = holo.MOTION_LIVELY

    def test_as_it_lands(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.end_turn("Asked.")
            threading.Event().wait(30)

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script), timing=PATIENT)
        desk = self.desktop
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: self.c.agent.running and desk.is_hud() and desk.overlay.is_shown()
                                 and not desk.is_switching(), timeout=20), "on the HUD")
        self.assertTrue(wait_for(lambda: self.feed.waits() == 1, timeout=10))
        settle()
        self.assertTrue(desk.leave_hud())
        self.assertTrue(wait_for(lambda: not desk.is_hud() and not desk.is_switching(), timeout=20), "in the window")
        now = [1000.0]
        self.feed._clock = lambda: now[0]
        cleared = self.fake_browser.kept_clear
        self.assertTrue(desk.enter_hud())
        self.assertIsNotNone(self.c._hud_reach())                     # on its way: its place is known already
        self.assertEqual(self.c._hud_reach(), self.hud().occupied_rect())
        self.assertFalse(desk.overlay.is_shown())
        now[0] += 3600.0                                              # a long flight, whatever it takes
        self.assertTrue(wait_for(lambda: desk.overlay.is_shown() and not desk.is_switching(), timeout=20), "landed")
        self.assertGreater(self.fake_browser.kept_clear, cleared)
        self.assertEqual(self.feed._card_at, now[0])                  # armed as it landed, not as it set off
        self.c.agent.stop("test")
        self.ended()


class ShortScreenTests(FeedApp):
    """arf-14: nothing of a question's card runs off the bottom; a panel's height never reaches "no limit"."""

    QUESTION = ("Which of these should I use for the table, given that the quiet ones cost more and the cheap ones were "
                "reviewed badly by the people who bought them last year, and that some of them are out of stock in the "
                "shops near you, while the others can only be ordered from abroad at a price I can't quite make out? ")[:300]
    SIZES = ((1093, 582), (1024, 600), (1280, 688), (1366, 728), (1536, 816), (1920, 1032))

    def card_up(self, choices: list[str], steps: list[dict] | None = None) -> hud_overlay.TaskCard:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", found="", steps=steps or STEPS)
            engine.call("ask_owner", question=self.QUESTION, choices=choices)
            engine.end_turn("Asked.")
            threading.Event().wait(60)

        self.start(script)
        return self.asked_card()

    def check_all_sizes(self, card: hud_overlay.TaskCard, label: str) -> None:
        overlay = self.desktop.overlay
        hud_window = overlay.hud
        panel = self.feed.panel()
        for width, height in self.SIZES:
            area = QRect(0, 0, width, height)
            hud_window.place_in(area)
            for watch in (None, QRect(width - 656, 200, 640, height - 216)):
                self.fake_browser.rect = watch
                self.feed._place()
                settle()
                where = f"{label} {width}x{height} {watch.getRect() if watch else None}"
                box = card.geometry()
                self.assertTrue(area.contains(box), f"{where}: the card {box.getRect()}")
                self.assertTrue(area.contains(panel.geometry()), f"{where}: the panel {panel.geometry().getRect()}")
                self.assertFalse(panel.geometry().intersects(box), where)
                self.assertFalse(box.intersects(hud_window.occupied_rect()), where)
                self.assertGreater(panel._max_height, 0, where)      # 0 would be no limit
                self.assertLessEqual(panel.height(), max(panel._max_height, hud_overlay.PANEL_COMPACT + 43), where)
                self.assertGreater(card.scroll.height(), 0, where)
                scroll = card.scroll.verticalScrollBar()
                column = card._column.height()
                for button in card.buttons().values():
                    if button.parent() is card:                       # in the card itself: all of it shows
                        self.assertTrue(card.rect().contains(button.geometry().adjusted(
                            holo.HoloButton.MARGIN, holo.HoloButton.MARGIN, -holo.HoloButton.MARGIN,
                            -holo.HoloButton.MARGIN)), where)
                    else:                                             # with its words: the scroll bar reaches it
                        self.assertIs(button.parent(), card._column, where)
                        self.assertGreaterEqual(column, button.geometry().bottom() - holo.HoloButton.MARGIN, where)
                        self.assertGreater(scroll.maximum(), 0, where)
                self.assertEqual(card.cut_texts(), [], where)
                self.assertTrue(all(not button.isHidden() for button in card.buttons().values()), where)

    def test_five_choices_of_ordinary_length(self) -> None:
        card = self.card_up([f"The quiet one number {n}" for n in range(1, 6)])
        self.check_all_sizes(card, "ordinary")
        self.c.agent.stop("test")
        self.ended()

    def test_five_long_choices(self) -> None:
        card = self.card_up([f"A long choice number {n} of nearly sixty characters, all told" for n in range(1, 6)])
        self.check_all_sizes(card, "long")
        self.c.agent.stop("test")
        self.ended()

    def test_a_long_plan_above_it(self) -> None:
        many = [{"text": f"Step number {n} of a long plan that wraps onto a second line of the panel, as some do",
                 "status": "todo"} for n in range(1, 11)]
        card = self.card_up([f"The quiet one number {n}" for n in range(1, 6)], many)
        self.check_all_sizes(card, "long plan")
        self.c.agent.stop("test")
        self.ended()

    def test_a_click_on_a_choice_that_scrolls_with_its_words_still_answers(self) -> None:
        card = self.card_up([f"A long choice number {n} of nearly sixty characters, all told" for n in range(1, 6)])
        self.hud().place_in(QRect(0, 0, 1024, 600))
        self.feed._place()
        settle()
        self.assertIs(card.buttons()["choice:4"].parent(), card._column)
        with mock.patch.object(self.c.agent, "answer", return_value=True) as answer:
            self.feed._card_at -= 5
            card.buttons()["choice:4"].click()
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[1], "A long choice number 5 of nearly sixty characters, all told")
        self.c.agent.stop("test")
        self.ended()

    def test_with_no_room_at_all_the_panel_s_limit_never_becomes_none(self) -> None:
        """0 means no limit: a card that takes all the room must not give the panel all its rows back, over the card."""
        many = [{"text": f"Step number {n} of a long plan", "status": "todo"} for n in range(1, 11)]
        card = self.card_up([f"The quiet one number {n}" for n in range(1, 6)], many)
        panel = self.feed.panel()
        self.hud().place_in(QRect(0, 0, 800, 330))
        self.feed._place()
        settle()
        self.assertGreaterEqual(panel._max_height, hud_overlay.PANEL_COMPACT)
        self.assertEqual(len(panel.shown_rows()), 1)                  # the least it takes, whatever is left
        self.assertGreater(card._max_height, 0)
        self.c.agent.stop("test")
        self.ended()

    def choices_card(self, count: int, limit: int) -> hud_overlay.TaskCard:
        card = hud_overlay.TaskCard(native=hud_overlay.NoNative(), hide_from_capture=False)
        self.addCleanup(card.deleteLater)
        buttons = tuple(hud_overlay.CardButton(f"choice:{n}", f"The quiet one {n}", holo.AMBER_GHOST) for n in range(count))
        card.set_task(hud_overlay.TaskContent("A question for you", "Which?", hud_overlay.TONE_AMBER, buttons))
        card.set_limits(hud_overlay.TASK_CARD_WIDTH, limit)
        return card

    def test_with_the_room_a_stack_stays_as_it_was(self) -> None:
        card = self.choices_card(5, 900)
        self.assertTrue(all(button.parent() is card for button in card.buttons().values()))
        self.assertEqual(len({button.geometry().top() for button in card.buttons().values()}), 5)    # one under another

    def test_short_of_room_they_go_two_to_a_row_before_they_scroll(self) -> None:
        card = self.choices_card(5, 240)
        self.assertTrue(all(button.parent() is card for button in card.buttons().values()))
        self.assertEqual(len({button.geometry().top() for button in card.buttons().values()}), 3)    # 2, 2 and 1
        self.assertLessEqual(card.height(), 240)
        self.assertTrue(all(card.rect().contains(button.geometry().adjusted(
            holo.HoloButton.MARGIN, holo.HoloButton.MARGIN, -holo.HoloButton.MARGIN, -holo.HoloButton.MARGIN))
            for button in card.buttons().values()))


def desk_rows(count: int) -> hud_overlay.DeskContent:
    rows = tuple(hud_overlay.DeskRow(n, f"letter {n}.txt", "Documents", mark=hud_overlay.MARK_TICK, ticked=True)
                 for n in range(1, count + 1))
    return hud_overlay.DeskContent("NEEDS YOUR OK", "Move these files to Documents", hud_overlay.TONE_AMBER, rows,
                                   "Nothing moves until you click.", hud_overlay.TONE_AMBER,
                                   (hud_overlay.DeskButton("dismiss", "Not now", "quiet"),
                                    hud_overlay.DeskButton("go", "Move 8 files", "commit", right=True)))


class TallFileCardTests(unittest.TestCase):
    """arf-15: a tall card's rows scroll: its title and its buttons stay in view."""

    def card(self, count: int = 8) -> hud_overlay.DesktopCard:
        card = hud_overlay.DesktopCard(native=hud_overlay.NoNative(), hide_from_capture=False)
        self.addCleanup(card.deleteLater)
        card.set_desk(desk_rows(count))
        return card

    def test_no_room_given_it_is_as_it_was(self) -> None:
        card = self.card()
        whole = card.height()
        self.assertIsNone(card.rows_scroll())
        self.assertTrue(card._row_clip.isEmpty())
        self.assertTrue(all(not tick.isHidden() for tick in card.ticks().values()))
        card.set_room(whole)                                          # room enough: nothing changes
        self.assertEqual(card.height(), whole)
        self.assertIsNone(card.rows_scroll())

    def test_short_of_room_its_rows_scroll_and_its_buttons_stay(self) -> None:
        card = self.card()
        whole = card.height()
        card.set_room(360)
        self.assertEqual(card.height(), 360)
        bar = card.rows_scroll()
        self.assertIsNotNone(bar)
        self.assertEqual(bar.maximum(), whole - 360)
        self.assertLess(card.geometry().height(), whole)
        for button in card.buttons().values():
            self.assertLessEqual(button.geometry().bottom() - holo.HoloButton.MARGIN, card.height())
        shown = [number for number, tick in card.ticks().items() if not tick.isHidden()]
        self.assertEqual(shown[0], 1)
        self.assertLess(len(shown), 8)
        self.assertEqual(card.cut_texts(), [])

    def test_the_wheel_brings_the_last_row_up_with_its_tick(self) -> None:
        card = self.card()
        card.set_room(360)
        bar = card.rows_scroll()
        bar.setValue(bar.maximum())
        shown = [number for number, tick in card.ticks().items() if not tick.isHidden()]
        self.assertEqual(shown[-1], 8)
        self.assertNotIn(1, shown)
        last = card.row_rect("row:8")
        self.assertLessEqual(last.bottom(), card._row_clip.bottom() + 0.5)
        bar.setValue(0)
        centre = card.rect().center()
        event = QWheelEvent(centre.toPointF(), card.mapToGlobal(centre).toPointF(), QPoint(0, 0), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        card.wheelEvent(event)
        self.assertEqual(bar.value(), hud_overlay.DESK_ROW)

    def test_the_row_on_its_way_stays_in_view(self) -> None:
        card = self.card()
        card.set_room(360)
        rows = list(card.desk().rows)
        rows[7] = replace(rows[7], mark=hud_overlay.MARK_NOW)
        card.set_desk(replace(card.desk(), rows=tuple(rows)))
        self.assertEqual(card.rows_scroll().value(), card.rows_scroll().maximum())

    def test_room_again_it_is_whole_again(self) -> None:
        card = self.card()
        whole = card.height()
        card.set_room(360)
        card.set_room(0)
        self.assertEqual(card.height(), whole)
        self.assertIsNone(card.rows_scroll())
        self.assertTrue(all(not tick.isHidden() for tick in card.ticks().values()))


class FileCardBesideTheWindowTests(ThingsApp):
    """arf-15: while a file card waits or works, the watch window is off the screen; the owner's own Show or Hide wins."""

    GOAL = "move my letter from Downloads into Documents"

    def script(self) -> Script:
        def steps(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}

        return Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                      ("propose", "propose_files", steps), ("wait",))

    def card_up(self, *, opened: bool = True) -> Script:
        script = self.script()
        self.go(script)
        self.fake_browser.rect = QRect(624, 159, 640, 505) if opened else None
        self.tidy_entry()
        self.wait_waiting()
        return script

    def test_it_rests_while_the_card_waits_and_comes_back_at_its_end(self) -> None:
        self.card_up()
        self.assertTrue(self.fake_browser.rest)
        self.assertIsNone(self.c.agent.window_rect())
        self.press_tidy(desktop_tidy.GO_KEY)
        self.assertFalse(self.fake_browser.rest)
        self.assertEqual(self.c.agent.window_rect(), self.fake_browser.rect)
        self.ended()

    def test_it_rests_through_the_batch_too(self) -> None:
        release, in_call = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.card_up()
        self.fake.on_call = lambda method, _argument: (in_call.set(), release.wait(15.0)) if method == "move_item" else None
        self.press_tidy(desktop_tidy.GO_KEY, wait=False)
        self.assertTrue(wait_for(in_call.is_set, timeout=10))
        settle()
        self.assertEqual(self.feed.waits(), 0)
        self.assertTrue(self.fake_browser.rest)
        release.set()
        self.ended()
        self.assertFalse(self.fake_browser.rest)

    def test_the_panel_offers_show_while_it_rests_and_show_wins(self) -> None:
        self.card_up()
        buttons = self.feed.buttons()
        self.assertFalse(buttons.show_button.isHidden())
        QTest.mouseClick(buttons.show_button, Qt.MouseButton.LeftButton)
        settle()
        self.assertFalse(self.fake_browser.rest)
        self.assertEqual(self.c.agent.window_rect(), self.fake_browser.rect)
        self.assertTrue(buttons.show_button.isHidden())
        self.c.agent.stop("test")
        self.ended()

    def test_the_owner_s_hide_holds_after_its_rest(self) -> None:
        script = self.script()
        self.go(script)
        self.fake_browser.rect = QRect(624, 159, 640, 505)
        self.c.agent.windowHidden.emit(True)
        self.fake_browser.owner_hidden = True
        self.tidy_entry()
        self.wait_waiting()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        self.assertFalse(self.fake_browser.rest)
        self.assertTrue(self.fake_browser.owner_hidden)              # only their Show brings it back

    def test_with_no_window_yet_there_is_nothing_to_show(self) -> None:
        self.card_up(opened=False)
        self.assertTrue(self.fake_browser.rest)
        self.assertTrue(self.feed.buttons().show_button.isHidden())  # a Show here would do nothing
        self.c.agent.stop("test")
        self.ended()

    def test_a_stop_while_it_rests(self) -> None:
        self.card_up()
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertFalse(self.fake_browser.rest)
        self.assertGreaterEqual(self.fake_browser.stopped, 1)

    def test_a_question_s_card_does_not_rest_it(self) -> None:
        script = Script(("ask", "ask_owner", {"question": "Which?", "choices": ["Blue", "Red"]}), ("wait",), ("hold",))
        self.go(script)
        self.wait_card()
        self.assertFalse(self.fake_browser.rest)
        self.c.agent.stop("stop")
        self.ended()

    def test_a_tall_card_of_a_task_stays_under_the_bar_and_on_screen(self) -> None:
        self.card_up()
        window = self.tidy_entry().window
        window.set_desk(desk_rows(8))
        for width, height in ((1280, 680), (1093, 582), (1366, 728), (1920, 1032)):
            area = QRect(0, 0, width, height)
            self.hud().place_in(area)
            self.feed._place()
            self.desktop.results.place_again()
            settle()
            where = f"{width}x{height} card {window.geometry().getRect()}"
            self.assertTrue(area.contains(window.geometry()), where)
            self.assertFalse(window.geometry().intersects(self.hud().occupied_rect()), where)
            self.assertFalse(window.geometry().intersects(self.feed.panel().geometry()), where)
        self.c.agent.stop("stop")
        self.ended()

    def test_a_desktop_request_s_own_card_keeps_all_its_height(self) -> None:
        self.make_things(lambda engine: None)
        overlay = self.desktop.overlay
        self.assertFalse(overlay._feed_shown)
        card = overlay.new_desk_card()
        card.set_desk(desk_rows(8))
        whole = card.height()
        self.hud().place_in(QRect(0, 0, 1280, 680))
        overlay.show_card(card, self.hud().occupied_rect(), QRect(0, 0, 1280, 680))
        self.assertEqual(card.height(), whole)
        self.assertIsNone(card.rows_scroll())
        overlay.remove_card(card)


class _Window:
    def __init__(self) -> None:
        self.shown = True

    def isVisible(self) -> bool:  # noqa: N802 - Qt's name
        return self.shown

    def geometry(self) -> QRect:
        return QRect(0, 0, 10, 10)


class _Inner(QObject):
    """AgentBrowser as BrowserClient sees it, with no web engine."""

    hideAsked = Signal()
    windowPlaced = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.win: _Window | None = _Window()
        self.held: dict[str, Any] = {}
        self.rest: list[bool] = []

    def window(self) -> Any:
        return self.win

    def load(self, _url: str, _timeout: float, done: Any) -> None:
        done(None)

    def set_hidden(self, **held: Any) -> None:
        self.held.update(held)

    def set_resting(self, resting: bool) -> None:
        self.rest.append(resting)

    def end_run(self, linger: bool, hold: bool = False) -> None:
        if not (linger and self.win is not None and not self.held.get("owner")):
            self.win = None                                           # as the real one: a hidden window goes

    def stop_now(self) -> None:
        pass

    def keep_clear(self) -> None:
        pass

    def close_all(self) -> None:
        self.win = None

    shutdown = close_all


class ShowAfterTheEndTests(unittest.TestCase):
    """arf-13, over BrowserClient: Show is offered only while it would bring something back."""

    def client(self) -> tuple[agent_browser.BrowserClient, _Inner]:
        inner = _Inner()
        client = agent_browser.BrowserClient(factory=lambda _parent: inner, poll_s=0.01)
        self.addCleanup(client.deleteLater)
        client._ensure()
        return client, inner

    def test_hidden_as_the_run_ends_the_window_went_and_show_with_it(self) -> None:
        client, inner = self.client()
        client.set_hidden(owner=True)
        self.assertTrue(client.owner_hid())
        client.end_run(linger=True, hold=True)
        self.assertIsNone(inner.win)
        self.assertFalse(client.owner_hid())

    def test_hidden_with_no_window_yet_it_holds_for_the_one_to_come(self) -> None:
        client, inner = self.client()
        inner.win = None
        client.set_hidden(owner=True)
        self.assertTrue(client.owner_hid())

    def test_hidden_after_the_end_the_window_that_lingers_can_come_back(self) -> None:
        client, inner = self.client()
        client.end_run(linger=True, hold=True)
        self.assertIsNotNone(inner.win)
        client.set_hidden(owner=True)
        self.assertTrue(client.owner_hid())

    def test_its_rest_holds_for_the_window_to_come_and_says_so_only_with_one(self) -> None:
        inner = _Inner()
        client = agent_browser.BrowserClient(factory=lambda _parent: inner, poll_s=0.01)
        self.addCleanup(client.deleteLater)
        client.set_resting(True)
        self.assertFalse(client.resting())                            # nothing to bring back yet
        client._ensure()
        self.assertEqual(inner.rest, [True])
        self.assertTrue(client.resting())
        client.set_resting(False)
        self.assertEqual(inner.rest, [True, False])
        self.assertFalse(client.resting())


class ShowOnTheStampedPanelTests(FeedApp):
    """arf-13, over the real feed: the panel's Show is a dead button no more."""

    def test_hidden_as_it_ends_the_panel_offers_no_show(self) -> None:
        inner = _Inner()
        client = agent_browser.BrowserClient(factory=lambda _parent: inner, poll_s=0.01)
        self.addCleanup(client.deleteLater)
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            stages.reach(1)
            engine.call("finish", summary=SUMMARY, outcome="done")
            engine.end_turn("Done.")

        self.start(script, browser=client)
        self.reached(stages, 1)
        client._ensure()
        inner.hideAsked.emit()                                       # the strip's Hide
        client.set_hidden(owner=True)
        client.hideAsked.emit()
        settle()
        show = self.feed.buttons().show_button
        self.assertFalse(show.isHidden())                            # while it runs, Show brings it back
        stages.go(1)
        self.ended()
        settle()
        self.assertTrue(show.isHidden())
        self.assertFalse(self.c.agent.window_hidden())
        self.assertIsNotNone(self.feed.card)                         # the result card's own Show still works


class WaitingBannerTests(unittest.TestCase):
    def test_its_words_stay_on_one_line(self) -> None:
        row = desktop_view.WaitingRow()
        self.addCleanup(row.deleteLater)
        self.assertFalse(row.label.wordWrap())
        row.resize(838, row.HEIGHT)
        row.set_count(1)
        row.layout().activate()
        self.assertGreaterEqual(row.label.width(), row.label.fontMetrics().horizontalAdvance(desktop_view.WAITING_TEXT))
        row.set_count(3)
        row.layout().activate()
        self.assertGreaterEqual(row.label.width(), row.label.fontMetrics().horizontalAdvance(row.label.text()))


class TonesOnTheHudTests(FeedApp):
    """What must be read on the HUD is in a tone its caption shows: an idle one it leaves out."""

    def test_the_plan_s_limit_shows_on_the_caption_and_is_no_amber(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(resets_at=time.time() + 3600.0))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            threading.Event().wait(30)

        self.start(script)
        self.assertTrue(wait_for(lambda: "limit is reached" in self.hud().caption_text(), timeout=10),
                        self.hud().caption_text())
        self.assertNotEqual(self.feed.panel().content().tone, hud_overlay.TONE_AMBER)
        self.c.agent.stop("test")
        self.ended()

    def state_words(self) -> tuple[str, str]:
        return self.hud().state_word(), self.reading.header.state_word()

    def test_the_state_word_says_it_waits_through_the_plan_s_limit(self) -> None:
        """X4: not THINKING while nothing thinks: at rest, in the bar's own kind of word, never amber. It goes on as ever
        when the limit lets go."""
        gate = threading.Event()
        self.addCleanup(gate.set)

        def script(engine: FakeEngine) -> None:
            limit_by_text(engine, "You've hit your limit")
            engine.wait_message()
            engine.call("say", text="Going on.")
            gate.wait(15)

        self.start(script, timing=PATIENT)
        self.assertEqual(self.reading.task_word(orb_mod.IDLE, False), "")
        self.assertTrue(wait_for(lambda: self.state_words() == ("WAITING", "WAITING"), timeout=10), self.state_words())
        self.assertEqual(self.reading.look()[0], orb_mod.IDLE)
        self.assertNotEqual(self.hud().caption.word(), orb_mod.READOUTS[orb_mod.NEEDS])
        self.assertEqual(self.feed.waits(), 0)
        self.assertNotEqual(self.feed.panel().content().tone, hud_overlay.TONE_AMBER)
        self.assertEqual(self.feed.activity(), agent_ui.LIMIT_ACTIVITY)
        self.assertRegex(self.feed.panel().content().head, r"^WAITING")
        self.assertEqual(self.reading.task_chip_text(), agent_feed.CHIP_LIMIT)
        self.type_it("carry on")                                     # no time given: what is typed tries again
        self.assertTrue(wait_for(lambda: self.state_words()[0] == "THINKING", timeout=10), self.state_words())
        self.assertNotEqual(self.reading.task_chip_text(), agent_feed.CHIP_LIMIT)
        self.assertNotEqual(self.feed.activity(), agent_ui.LIMIT_ACTIVITY)
        self.assertRegex(self.feed.panel().content().head, r"^WORKING")
        self.c.agent.stop("test")
        gate.set()
        self.ended()
        settle()
        self.assertNotIn("WAITING", self.state_words())

    def test_muted_wins_and_a_card_keeps_its_own_look(self) -> None:
        self.start(lambda engine: (limit_by_text(engine, "You've hit your limit"), threading.Event().wait(30)),
                   timing=PATIENT)
        self.assertTrue(wait_for(lambda: self.state_words()[0] == "WAITING", timeout=10))
        self.assertEqual(self.reading.task_word(orb_mod.IDLE, True), "")
        self.assertEqual(self.reading.task_word(orb_mod.SPEAKING, False), "")
        self.feed._on_question(ar.OwnerQuestion("q1", ar.Q_RESUME, "Shall I?", ar.KEEP_GOING_CHOICES))
        settle()
        self.assertEqual(self.state_words()[0], orb_mod.READOUTS[orb_mod.NEEDS])
        self.feed._settle_question()
        settle()
        self.assertEqual(self.state_words()[0], "WAITING")           # it still holds
        self.assertEqual(self.feed.activity(), agent_ui.LIMIT_ACTIVITY)
        self.feed._on_quiet()
        self.assertEqual(self.feed.activity(), agent_ui.LIMIT_ACTIVITY)
        self.c.agent.stop("test")
        self.ended()

    def test_a_card_that_timed_out_says_so_on_the_caption(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.end_turn("Asked.")
            threading.Event().wait(30)

        self.start(script, timing=replace(PATIENT, question_s=0.3))
        self.assertTrue(wait_for(lambda: "No answer came" in self.hud().caption_text(), timeout=10))
        self.c.agent.stop("test")
        self.ended()


class HudReachTests(AgentApp):
    def test_on_the_hud_in_flight_its_place_counts_though_it_is_not_shown_yet(self) -> None:
        self.make_agent()
        self.assertIsNone(self.c._hud_reach())
        self.enter()
        with mock.patch.object(self.desktop.overlay, "is_shown", return_value=False):
            self.assertEqual(self.c._hud_reach(), self.hud().occupied_rect())


class OpenCardTests(ThingsApp):
    """arf-22: a task's one-click card that waits for a click lives through a visit to the window."""

    def opened(self, *sites: str) -> tuple[threading.Event, dict[str, str]]:
        told: dict[str, str] = {}
        hold = threading.Event()
        self.addCleanup(hold.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            for site in sites:
                told[site] = engine.text_of(engine.call("open_thing", site=site))
            told["ready"] = "yes"
            hold.wait(30)
            engine.call("finish", summary="The page waits on a card for your click.", outcome="done")
            engine.end_turn()

        self.go(script, goal=OPENED_WORDS)
        return hold, told

    def waiting_entries(self) -> list[Any]:
        return [entry for entry in self.desktop.results.entries() if entry.card.kind == "waiting"]

    def test_it_comes_back_with_the_hud(self) -> None:
        hold, told = self.opened(SUGGESTED)
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.assertIn("It has not opened", told[SUGGESTED])
        self.assertEqual(len(self.waiting_entries()), 1)
        self.back()
        settle()
        self.assertEqual(self.desktop.results.entries(), [])
        self.assertEqual(self.desktop.waiting_count(), 1)            # the window's reminder counts it
        self.assertEqual(self.c._desktop_waiting.count(), 1)
        self.enter()
        settle()
        self.assertEqual(len(self.waiting_entries()), 1)
        self.assertEqual(self.desktop.waiting_count(), 0)            # on the HUD it is there, as ever
        hold.set()
        self.assertEqual(self.ended().outcome, "done")
        self.back()
        settle()
        self.enter()
        settle()
        entries = self.waiting_entries()
        self.assertEqual(len(entries), 1)                            # the report said it waits: still so
        self.assertEqual(self.fake.opened, [])
        QTest.mouseClick(entries[0].window.buttons()[card_results.OPEN_KEY], Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for(lambda: self.fake.opened, timeout=10), "its click still opens it")
        self.assertEqual(self.fake.opened, [("open_url", SUGGESTED)])

    def test_back_with_the_hud_it_is_guarded_as_a_card_that_has_just_come_up(self) -> None:
        """rf-14: a click within the guard's time, as it comes back, does nothing; one after it counts."""
        hold, told = self.opened(SUGGESTED)
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.back()
        settle()
        self.desktop.task_card_arm_s = 60.0                          # (the shown moment is older than this only by far)
        run_id = self.desktop._task_held[0][0].run_id
        self.desktop._task_shown[run_id] -= 600.0                    # it came up long ago, before the visit to the window
        self.enter()
        settle()
        entry = self.waiting_entries()[0]
        QTest.mouseClick(entry.window.buttons()[card_results.OPEN_KEY], Qt.MouseButton.LeftButton)
        settle()
        self.assertEqual(self.fake.opened, [])                       # it has only just come back
        self.assertTrue(self.waiting_entries())
        self.desktop.task_card_arm_s = 0.0
        QTest.mouseClick(entry.window.buttons()[card_results.OPEN_KEY], Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for(lambda: self.fake.opened, timeout=10), "a later click counts")
        hold.set()
        self.ended()

    def test_a_handover_that_finds_the_hud_gone_again_leaves_it_waiting(self) -> None:
        hold, told = self.opened(SUGGESTED)
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.back()
        settle()
        self.desktop._show_held()                                    # the flight lands, but the window is back by then
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.enter()
        settle()
        self.assertEqual(len(self.waiting_entries()), 1)
        hold.set()
        self.assertEqual(self.ended().outcome, "done")

    def test_two_that_came_while_away_both_wait(self) -> None:
        hold = threading.Event()
        self.addCleanup(hold.set)
        told: dict[str, str] = {}
        go = threading.Event()
        self.addCleanup(go.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            go.wait(15)
            for site in (SUGGESTED, SUGGESTED_TOO):
                told[site] = engine.text_of(engine.call("open_thing", site=site))
            told["ready"] = "yes"
            hold.wait(30)

        self.go(script, goal=OPENED_WORDS)
        self.back()
        settle()
        go.set()
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.assertEqual(self.desktop.waiting_count(), 2)
        self.enter()
        settle()
        self.assertEqual(len(self.waiting_entries()), 2)
        self.c.agent.stop("test")
        self.ended()

    def test_a_stop_takes_it_while_away(self) -> None:
        _hold, told = self.opened(SUGGESTED)
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.back()
        settle()
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.enter()
        settle()
        self.assertEqual(self.waiting_entries(), [])

    def test_a_turn_off_takes_it_too(self) -> None:
        _hold, told = self.opened(SUGGESTED)
        self.assertTrue(wait_for(lambda: "ready" in told, timeout=10))
        settle()
        self.back()
        settle()
        self.c._on_desktop_turn_off()
        settle()
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.desktop._task_held, [])
        self.ended()


def empty_run(run_id: str) -> Any:
    return SimpleNamespace(run_id=run_id, shown=(), waiting=lambda: (), found=lambda: ())


class OwnerCardTests(ThingsApp):
    """arf-24: a card the owner has not decided yet is never replaced by a task's."""

    GOAL = "move my letter from Downloads into Documents"

    @staticmethod
    def steps(got: dict) -> dict:
        return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "letter.txt")],
                           "to": ref_in(got["roots"], "Documents")}]}

    def test_task_show_says_no_and_the_model_hears_it_and_the_card_stays(self) -> None:
        first = Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                       ("propose", "propose_files", self.steps), ("wait",))
        self.go(first)
        self.tidy_entry()
        self.wait_waiting()
        owners = self.desktop.tidy()                                 # built by the same code as a desktop request's
        self.c.agent.stop("x")
        self.ended()
        settle()
        self.feed._dismiss()
        self.c.agent.last_end = None
        self.desktop._set_tidy(owners)
        settle()
        ended: list[str] = []
        self.desktop.tidyEnded.connect(lambda _state, ending: ended.append(ending))
        second = Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                        ("propose", "propose_files", self.steps))
        self.harness.factory.runner.runs.append(FakeEngine(second))
        self.assertTrue(self.c.agent.start(self.GOAL))
        end = self.ended()
        self.assertEqual((end.outcome, end.summary), ("done", DONE))  # it was told and went on
        self.assertIs(self.desktop.tidy(), owners)
        self.assertTrue(owners.waiting())
        self.assertEqual(ended, [])
        self.assertTrue(second.got["propose"]["isError"])
        self.assertIn("another card waits for the owner", second.text("propose"))
        self.assertEqual(self.desktop.waiting_count(), 1)

    def test_this_run_s_own_stamped_card_gives_way_as_ever(self) -> None:
        first = Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                       ("propose", "propose_files", self.steps), ("wait",))
        self.go(first)
        self.tidy_entry()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        state = self.desktop.tidy()
        self.assertEqual(state.stage, desktop_tidy.DONE)
        self.assertFalse(state.waiting())
        self.assertTrue(self.desktop.task_show(empty_run("another")))   # nothing of the kind to replace

    def test_the_task_consent_is_asked_every_time(self) -> None:
        self.make_things(lambda engine: None)
        self.assertTrue(self.desktop.consent_given())
        run = empty_run("r1")
        with mock.patch.object(self.desktop, "consent_for_tasks", return_value=False):
            self.assertFalse(self.desktop.task_show(run))
        self.assertNotIn("r1", self.desktop._task_shown)
        self.assertTrue(self.desktop.task_show(run))
        self.assertIn("r1", self.desktop._task_shown)


class LiveStampTests(ThingsApp):
    """arf-25: Live says how each wait went, and no finished task's step says WAITING."""

    GOAL = "move my letter from Downloads into Documents"

    def steps_of(self) -> list[Any]:
        task = self.agent_tasks()[-1]
        return list(task.steps)

    def step(self, title: str) -> Any:
        return next(step for step in self.steps_of() if step.title == title)

    def firewall_task(self, **harness: Any) -> None:
        script = Script(("calendar", "calendar_find", {"from": "2026-11-05", "to": "2026-11-12"}),
                        ("asked", "web_search", {"query": SECRET_WORD}), ("wait",))
        self.go(script, **harness)

    def assert_nothing_waits(self) -> None:
        self.assertEqual([step.title for step in self.steps_of() if step.status_text == "WAITING"], [])

    def test_a_firewall_card_said_no(self) -> None:
        self.firewall_task()
        self.wait_card()
        self.press_card("choice:1")
        self.ended()
        step = self.step("Searched the web")
        self.assertEqual((step.status, step.status_text, step.summary), (live.STATUS_WARN, "", ar.HOW_DECLINED))
        self.assert_nothing_waits()

    def test_a_firewall_card_allowed(self) -> None:
        self.firewall_task()
        self.wait_card()
        self.press_card("choice:0")
        self.ended()
        step = self.step("Searched the web")
        self.assertEqual((step.status, step.summary), (live.STATUS_OK, ar.HOW_ALLOWED))
        self.assert_nothing_waits()

    def test_a_firewall_card_with_no_click(self) -> None:
        self.firewall_task(timing=replace(PATIENT, web_card_s=0.3))
        self.ended()
        self.assertEqual(self.step("Searched the web").summary, ar.HOW_NO_ANSWER)
        self.assert_nothing_waits()

    def test_keep_going_and_stop_here(self) -> None:
        for choice, how, status in (("choice:0", ar.HOW_KEEP_GOING, live.STATUS_OK),
                                    ("choice:1", ar.HOW_STOP_HERE, live.STATUS_WARN)):
            with self.subTest(how=how):
                def play(engine: FakeEngine) -> None:
                    engine.wait_message()
                    engine.call("say", text="One.")
                    engine.call("write_file", name="a.md", text="a")
                    engine.end_turn("Waiting.")
                    engine.wait_message()
                    engine.call("finish", summary="All of it.", outcome="partial")
                    engine.end_turn()

                self.make_things(play, timing=PATIENT)
                self.c.agent._engine.agent = mock.Mock(max_turns=1, max_minutes=20, model="opus", effort="")
                self.start()
                self.wait_card()
                self.press_card(choice)
                self.ended()
                step = self.step("Asked you")
                self.assertEqual((step.status, step.status_text, step.summary), (status, "", how))
                self.assert_nothing_waits()

    def test_a_question_says_so_in_a_note(self) -> None:
        script = Script(("ask", "ask_owner", {"question": "Which?", "choices": ["Blue", "Red"]}), ("wait",))
        self.go(script)
        self.wait_card()
        self.press_card("choice:0")
        self.ended()
        self.assertIn(ar.HOW_ANSWERED, [note.text for step in self.steps_of() for note in step.notes])
        self.assert_nothing_waits()

    def file_task(self) -> Script:
        def steps(got: dict) -> dict:
            return {"steps": [{"skill": "move_files", "files": [ref_in(got["down"], "letter.txt")],
                               "to": ref_in(got["roots"], "Documents")}]}

        script = Script(("roots", "files_list", {}), ("down", "files_list", {"place": "Downloads"}),
                        ("propose", "propose_files", steps), ("wait",))
        self.go(script)
        self.tidy_entry()
        self.wait_waiting()
        return script

    def test_a_file_card_clicked_says_how_many(self) -> None:
        self.file_task()
        self.press_tidy(desktop_tidy.GO_KEY)
        self.ended()
        step = self.step("Proposed a change to your files")
        self.assertEqual((step.status, step.summary), (live.STATUS_OK, owner.CARD_HOW_DONE + "Done 1 of 1"))
        self.assert_nothing_waits()

    def test_a_file_card_not_now(self) -> None:
        self.file_task()
        self.press_tidy(desktop_tidy.DISMISS_KEY, wait=False)
        self.ended()
        self.assertEqual(self.step("Proposed a change to your files").summary, "You chose Not now, so nothing was changed")
        self.assert_nothing_waits()

    def test_a_task_that_ended_with_a_card_up_says_so_too(self) -> None:
        """A Stop drops what is on its way, its wait's end too: the step that waited says so all the same."""
        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            threading.Event().wait(30)

        self.make_things(play, timing=PATIENT)
        self.c.agent._engine.agent = mock.Mock(max_turns=1, max_minutes=20, model="opus", effort="")
        self.start()
        self.wait_card()
        self.assertEqual(self.step("Asked you").status_text, "WAITING")
        self.c.agent.stop("stop")
        self.ended()
        self.assertEqual(self.step("Asked you").summary, ar.HOW_ENDED)
        self.assert_nothing_waits()

    def test_a_call_held_by_a_wait_says_it_was_not_made(self) -> None:
        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.call("write_file", name="b.md", text="b")
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.call("finish", summary="All of it.", outcome="done")
            engine.end_turn()

        def held() -> list[Any]:
            return [step for step in self.steps_of() if step.status_text == agent_ui.HELD_STATUS]

        self.make_things(play, timing=PATIENT)
        self.c.agent._engine.agent = mock.Mock(max_turns=2, max_minutes=20, model="opus", effort="")
        self.start()
        self.wait_card()
        # (Keep going comes up as the third call's event is read, while that call is still on its way here: a click before
        # it lands lets it through, as it should. So the call's end comes first: on a busy PC the click could win.)
        self.assertTrue(wait_for(held, timeout=10), "the call was held")
        self.press_card("choice:0")
        self.ended()
        self.assertEqual({step.summary for step in held()}, {"not made: the task was waiting for you"})


class TokensRowTests(AgentApp):
    def test_the_row_shows_all_of_it_in_plain_words(self) -> None:
        usage = {"input_tokens": 24, "cache_read_input_tokens": 90_000, "cache_creation_input_tokens": 4_000,
                 "output_tokens": 700}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary=SUMMARY, outcome="done")
            engine.end_turn("Done.", usage=usage)

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script))
        self.type_it(TYPED)
        self.ended()
        last = {item.label: item.value for item in self.agent_tasks()[0].steps[-1].fields}
        self.assertEqual(last["Tokens"], "94,024 in (90,000 from cache) / 700 out")
        self.assertLessEqual(len(last["Tokens"]), 48)
        self.assertNotIn("$", last["Tokens"])


class StopAfterFinishTests(ThingsApp):
    """arf-6 in the app: a Stop in the seconds after finish keeps the report, and what it says waits."""

    TODO = "Todo: title=Buy stamps | due=2026-11-06 10:00 | block= | acct= | link="

    def finished(self) -> None:
        hold = threading.Event()
        self.addCleanup(hold.set)

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("propose_action", lines=[self.TODO])
            engine.call("finish", summary="The whole report: the todo waits on a card for you.", outcome="done")
            hold.wait(30)

        self.go(play, timing=PATIENT)
        self.assertTrue(wait_for(lambda: self.c.agent.run() is not None and self.c.agent.run().reported, timeout=10))
        settle()

    def test_the_panel_s_stop(self) -> None:
        self.finished()
        self.assertEqual(len(self.ask_cards()), 1)
        QTest.mouseClick(self.feed.buttons().stop_button, Qt.MouseButton.LeftButton)
        end = self.ended()
        self.assertEqual((end.outcome, end.code), ("done", ar.C_FINISHED))
        self.assertEqual(self.feed.card.task().body, "The whole report: the todo waits on a card for you.")
        self.assertRegex(self.feed.panel().content().head, r"^DONE")
        self.assertEqual(len(self.ask_cards()), 1)                   # what the report says waits, waits
        self.assertEqual(self.fake_browser.stopped, 0)
        self.assertIn("The whole report", self.talk())

    def test_a_typed_stop(self) -> None:
        self.finished()
        self.type_it("stop")
        self.assertEqual(self.ended().outcome, "done")

    def test_a_turn_off_leaves_nothing_all_the_same(self) -> None:
        self.finished()
        self.c._on_desktop_turn_off()
        end = self.ended()
        settle()
        self.assertEqual(self.ask_cards(), [])
        self.assertIsNone(self.feed.card)
        self.assertGreaterEqual(self.fake_browser.stopped, 1)
        self.assertEqual((end.outcome, end.own_words), (ar.OUTCOME_STOPPED, True))   # rf-6: as the code says, nothing stays
        self.assertNotIn("The whole report", self.talk())
        self.assertNotIn("Stopped", self.talk())

    def said_after(self, press: Any) -> list[str]:
        """What the voice says from the moment of the owner's ``press``, with a line of the task's on its way to it."""
        voice = self.harness.voice
        before = len(voice.said)
        press()
        self.ended()
        settle()
        return [text for _kind, text, _key in voice.said[before:]]

    def stopped_quietly(self, press: Any) -> None:
        """rf-6: nothing more is said, what it was saying goes, and its report stands on its card and in the conversation."""
        self.finished()
        voice = self.harness.voice
        stops = voice.stops
        self.assertEqual(self.said_after(press), [])
        self.assertIn(ui.TASK_SAY_KEY, voice.stopped_keys)
        self.assertIn(ui.ASK_SAY_KEY, voice.stopped_keys)
        self.assertEqual(voice.stops, stops)                         # never the whole voice: a briefing reads on
        self.assertIn("The whole report", self.talk())
        self.assertEqual(self.feed.card.task().body, "The whole report: the todo waits on a card for you.")

    def test_the_panel_s_stop_says_nothing_more(self) -> None:
        self.stopped_quietly(lambda: QTest.mouseClick(self.feed.buttons().stop_button, Qt.MouseButton.LeftButton))

    def test_a_typed_stop_says_nothing_more(self) -> None:
        self.stopped_quietly(lambda: self.type_it("stop"))

    def test_esc_says_nothing_more(self) -> None:
        self.stopped_quietly(lambda: self.c.agent.stop("esc"))

    def test_a_turn_off_in_those_seconds_says_nothing_more_too(self) -> None:
        self.finished()
        voice = self.harness.voice
        stops = voice.stops
        self.assertEqual(self.said_after(self.c._on_desktop_turn_off), [])
        self.assertIn(ui.TASK_SAY_KEY, voice.stopped_keys)
        self.assertIn(ui.ASK_SAY_KEY, voice.stopped_keys)
        self.assertEqual(voice.stops, stops)

    def test_a_line_on_its_way_when_the_stop_comes_is_not_said(self) -> None:
        self.finished()
        self.c.agent.stopped_by = agent_ui.STOPPED_BY_OWNER          # the Stop has come; this line left before it
        self.assertEqual(self.c.agent.spoken("A line of the model's."), "")
        self.assertEqual(self.c.agent.spoken("A line of the model's.", "Jarvis's own."), "")
        self.c.agent.stop("test")
        self.ended()

    def test_a_turn_off_just_after_it_ended_by_itself_leaves_no_report_either(self) -> None:
        self.finished()
        self.c.agent.run().stop("its own time")                      # it has ended: that is on its way to this thread
        self.c._on_desktop_turn_off()
        end = self.ended()
        settle()
        self.assertFalse(end.own_words)
        self.assertNotIn("The whole report", self.talk())
        self.assertNotIn("The whole report", self.c.ask.bar.status())
        self.assertEqual(self.c._task_notes[-1].text, ui.TASK_TURNED_OFF_LINE)

    def test_a_turn_off_mid_run_leaves_no_entry_either(self) -> None:
        self.go(lambda engine: (engine.wait_message(), threading.Event().wait(30)), timing=PATIENT)
        settle()
        talk = self.talk()
        self.c._on_desktop_turn_off()
        end = self.ended()
        settle()
        self.assertEqual(end.outcome, ar.OUTCOME_STOPPED)
        self.assertEqual(self.talk(), talk)
        self.assertIn(ui.TASK_SAY_KEY, self.harness.voice.stopped_keys)
        self.assertEqual(self.c._task_notes[-1].text, ui.TASK_TURNED_OFF_LINE)   # a later Ask hears only that much

    def test_a_stop_mid_run_keeps_its_own_words_in_the_conversation(self) -> None:
        self.go(lambda engine: (engine.wait_message(), threading.Event().wait(30)), timing=PATIENT)
        settle()
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertIn("Stopped, sir.", self.talk())
        self.assertIn("(stopped)", self.c._task_notes[-1].text)

    REPORT = "The whole report: Quillfeather's letter is drafted. A second sentence of it. A third sentence."

    def ended_by_itself(self, *, line: str = "", hold: threading.Event | None = None, focus: bool = False) -> None:
        begun = threading.Event()                                    # the task is seen running before it ends
        self.addCleanup(begun.set)

        def play(engine: FakeEngine) -> None:
            engine.wait_message()
            begun.wait(10)
            if line:
                engine.call("say", text=line)
            if hold is not None:
                hold.wait(25)
            engine.call("finish", summary=self.REPORT, outcome="done")
            engine.end_turn("Done.")

        self.make_things(play, timing=PATIENT)
        if focus:
            self.desktop.focus.start(60)
        self.start()
        begun.set()
        if hold is None:
            self.ended()
            settle()

    def test_a_turn_off_after_it_ended_by_itself_stops_what_it_says(self) -> None:
        """run-4: no run is left for a Turn off to stop, so it stops the task's voice itself: the report's opening goes,
        and nothing else the voice is at."""
        self.ended_by_itself()
        voice = self.harness.voice
        keys, stops, said = len(voice.stopped_keys), voice.stops, len(voice.said)
        self.c._on_desktop_turn_off()
        settle()
        self.assertIn(ui.TASK_SAY_KEY, voice.stopped_keys[keys:])
        self.assertIn(ui.ASK_SAY_KEY, voice.stopped_keys[keys:])
        self.assertEqual(voice.stops, stops)                         # never the whole voice: a briefing reads on
        self.assertEqual(voice.said[said:], [])

    def test_a_turn_off_in_focus_mode_hands_none_of_the_task_to_the_voice(self) -> None:
        """run-4: focus mode held the report's opening; the Turn off ends focus mode, which would have said it."""
        self.ended_by_itself(focus=True)
        self.assertIn(ui.TASK_SAY_KEY, [key for _kind, _text, key in self.c._focus_speech])
        voice = self.harness.voice
        said = len(voice.said)
        self.c._on_desktop_turn_off()
        settle()
        self.assertFalse(self.desktop.in_focus())
        self.assertEqual([text for _kind, text, _key in voice.said[said:]], [])
        self.assertNotIn(ui.TASK_SAY_KEY, [key for _kind, _text, key in self.c._focus_speech])

    def test_a_turn_off_in_focus_mode_mid_task_hands_none_of_it_to_the_voice(self) -> None:
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.ended_by_itself(line="Reading the first review of Quillfeather now.", hold=hold, focus=True)
        self.assertTrue(wait_for(lambda: bool(self.c._focus_speech), timeout=10), "focus mode holds its line")
        voice = self.harness.voice
        said = len(voice.said)
        self.c._on_desktop_turn_off()
        self.ended()
        settle()
        self.assertEqual([text for _kind, text, _key in voice.said[said:]], [])

    def live_texts(self) -> list[str]:
        texts: list[str] = []
        for task in self.agent_tasks():
            for step in task.steps:
                texts += [step.title, step.summary, *(field.value for field in step.fields),
                          *(note.text for note in step.notes), *(item.text for item in step.items)]
        return texts

    def test_a_turn_off_in_those_seconds_leaves_no_report_in_live(self) -> None:
        """run-5: the finish step's own field held the whole report, so Live kept it after a Turn off though nothing of
        the task stays. Live's "How it ended" opens with the report at a natural end, and says Jarvis's words after it."""
        self.finished()
        self.assertFalse([text for text in self.live_texts() if "The whole report" in text])
        self.c._on_desktop_turn_off()
        self.ended()
        settle()
        self.assertFalse([text for text in self.live_texts() if "The whole report" in text])

    def test_at_a_natural_end_live_opens_its_end_with_the_report(self) -> None:
        self.ended_by_itself()
        self.assertTrue([text for text in self.live_texts() if text.startswith("The whole report")])

    def test_the_next_task_starts_with_a_clean_slate(self) -> None:
        self.finished()
        self.c.agent.stop("stop")
        self.ended()
        settle()
        self.assertEqual(self.c.agent.stopped_by, agent_ui.STOPPED_BY_OWNER)
        self.feed._dismiss()
        self.harness.factory.runner.runs.append(FakeEngine(lambda engine: engine.wait_message()))
        self.assertTrue(self.c.agent.start("another task"))
        self.assertEqual(self.c.agent.stopped_by, "")
        self.c.agent.stop("stop")
        self.assertTrue(wait_for(lambda: self.c.agent.run() is None, timeout=10))


class GuardsThatCouldNotFailTests(ThingsApp):
    """arf-26: guards no test could catch being taken out."""

    GOAL = "move my letter from Downloads into Documents and draft a note"
    TODO = StopAfterFinishTests.TODO

    def test_a_card_on_a_send_s_hold_keeps_its_result_when_a_task_is_stopped(self) -> None:
        script = Script(("todo", "propose_action", {"lines": [self.TODO]}), ("ask", "ask_owner", {"question": "Which?"}),
                        ("wait",), ("hold",))
        self.go(script)
        self.wait_card()
        card_id = self.ask_cards()[0].id
        self.c._send_hold = SimpleNamespace(action=SimpleNamespace(id=card_id))
        self.addCleanup(setattr, self.c, "_send_hold", None)
        self.c.agent.stop("stop")
        self.assertEqual([card.id for card in self.ask_cards()], [card_id])
        self.c._send_hold = None
        self.ended()

    def test_quitting_waits_for_the_process_to_be_gone(self) -> None:
        class SlowToGo(FakeEngine):
            def wait(self, timeout: float) -> int | None:
                time.sleep(0.4)
                return super().wait(timeout)

        hold = threading.Event()
        self.addCleanup(hold.set)
        engine = SlowToGo(lambda fake: (fake.wait_message(), hold.wait(30)))
        self.make_agent(engine, desktop_consent=3)
        self.assertTrue(self.c.agent.start("read about quiet laptops"))
        self.started_task()
        self.assertTrue(wait_for(lambda: engine.sent, timeout=10))
        self.c.agent.shutdown()
        self.assertTrue(engine.closed)                               # killed, waited for and closed before it returns
        self.assertEqual(self.c.agent.last_end.outcome, ar.OUTCOME_STOPPED)


if __name__ == "__main__":
    unittest.main()
