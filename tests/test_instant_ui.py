"""Instant answers in the app: instant_ui.InstantController (answers on this PC at once, network answers on a thread
of their own, Claude's when no answer can be had), instant_ui.TimerController (the one timer book: set, asked about,
cancelled, ended, restored after a restart, the window hidden or not) and the app's wiring (AskController.submit asks
Jarvis first: the conversation, the bar and no planner run). Offscreen Qt, fake clocks, no network."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from briefing_reader import config as config_module
from briefing_reader import hud, instant, instant_ui, ui
from briefing_reader.desktop import pcctl
from tests.ui_fakes import AppHarness, settle, wait_for

APP = QApplication.instance() or QApplication([])
START = datetime(2026, 11, 10, 23, 42, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at


def a_config(**instant_values: object) -> SimpleNamespace:
    return SimpleNamespace(instant=config_module.InstantConfig(**instant_values),
                           assistant=SimpleNamespace(address="sir"), display=SimpleNamespace(hour24=False))


class TimerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.clock = Clock()
        self.chimes: list[int] = []
        self.ended: list[tuple[str, str]] = []

    def make(self) -> instant_ui.TimerController:
        timers = instant_ui.TimerController(self.folder, now=self.clock.now, chime=lambda: self.chimes.append(1))
        timers.ended.connect(lambda text, said: self.ended.append((text, said)))
        self.addCleanup(timers.shutdown)
        timers.start()
        return timers

    def test_set_count_down_end_and_say_it(self) -> None:
        timers = self.make()
        reply = timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=90, label="the tea"))
        self.assertEqual(reply.speech, "Timer set for 1 minute 30 seconds, sir.")
        self.assertIn("for the tea; it ends at", reply.text)
        self.assertEqual(timers.chip_text(), "Timer 1:30")
        self.clock.at += timedelta(seconds=31)
        self.assertEqual(timers.chip_text(), "Timer 0:59")
        left = timers.handle(instant.TimerAsk(instant.TIMER_LEFT))
        self.assertIn("59 seconds left on your 1 minute 30 seconds timer for the tea", left.text)
        timers.check()
        self.assertEqual(self.ended, [])                   # not yet
        self.clock.at += timedelta(seconds=60)
        timers.check()
        self.assertEqual(self.ended, [("Your 1 minute 30 seconds timer for the tea is done.",
                                       "Your 1 minute 30 seconds timer for the tea is done, sir.")])
        self.assertEqual(self.chimes, [1])
        self.assertEqual(timers.chip_text(), "")
        self.assertIsNone(timers.handle(instant.TimerAsk(instant.TIMER_LEFT, bare=True)))   # no timer: Claude's
        self.assertEqual(timers.handle(instant.TimerAsk(instant.TIMER_LEFT)).text, "No timer is running.")

    def test_cancel_one_by_its_length_and_all(self) -> None:
        timers = self.make()
        for seconds in (600, 300):
            timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=seconds))
        self.assertEqual(timers.chip_text(), "2 timers · 5:00")
        which = timers.handle(instant.TimerAsk(instant.TIMER_CANCEL))
        self.assertIn("2 timers are running", which.text)
        self.assertEqual(len(timers.running()), 2)                # nothing cancelled on a guess
        gone = timers.handle(instant.TimerAsk(instant.TIMER_CANCEL, seconds=600))
        self.assertEqual(gone.text, "Cancelled your 10-minute timer.")
        self.assertEqual([timer.length_s for timer in timers.running()], [300])
        self.assertEqual(timers.handle(instant.TimerAsk(instant.TIMER_CANCEL, seconds=60)).text,
                         "No 1-minute timer is running; nothing was cancelled.")
        timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=60))
        self.assertEqual(timers.handle(instant.TimerAsk(instant.TIMER_CANCEL, every=True)).text,
                         "Cancelled all 2 timers.")
        self.assertEqual(timers.running(), ())
        self.assertEqual(timers.handle(instant.TimerAsk(instant.TIMER_CANCEL)).text,
                         "No timer is running; nothing was cancelled.")

    def test_at_most_five_and_never_a_label_in_the_log(self) -> None:
        timers = self.make()
        with self.assertLogs("briefing_reader.instant_ui", level="INFO") as logs:
            for _ in range(pcctl.MAX_TIMERS):
                timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=60, label="call Ana about rent"))
            full = timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=60))
        self.assertIn("5 timers are running already", full.text)
        self.assertNotIn("Ana", "\n".join(logs.output))

    def test_a_restart_restores_them_and_says_one_that_ended_meanwhile(self) -> None:
        first = self.make()
        first.handle(instant.TimerAsk(instant.TIMER_SET, seconds=120))
        first.handle(instant.TimerAsk(instant.TIMER_SET, seconds=3600))
        first.shutdown()
        saved = json.loads((self.folder / pcctl.TIMERS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(sorted(entry["seconds"] for entry in saved["timers"]), [120, 3600])
        self.clock.at += timedelta(minutes=10)              # Jarvis was closed when the 2-minute one ended
        second = self.make()
        self.assertEqual(len(second.running()), 1)
        self.assertEqual(len(self.ended), 1)
        self.assertIn("ended at", self.ended[0][0])
        self.assertIn("while Jarvis was away", self.ended[0][0])

    def test_restored_timers_show_and_a_timer_set_elsewhere_is_checked(self) -> None:
        first = self.make()
        first.handle(instant.TimerAsk(instant.TIMER_SET, seconds=300))
        first.shutdown()
        second = instant_ui.TimerController(self.folder, now=self.clock.now, chime=lambda: None)
        self.addCleanup(second.shutdown)
        changes: list[int] = []
        second.changed.connect(lambda: changes.append(1))
        second.start()
        self.assertEqual(changes, [1])                       # Home's chip shows the restored timer
        second.book.cancel_all()
        second._check.stop()
        second.book.add(2)                                   # the desktop's HUD set one in the shared book
        second.poke()
        self.assertTrue(second._check.isActive())
        self.assertEqual(changes, [1, 1])

    def test_an_older_file_with_whole_minutes_still_loads(self) -> None:
        self.folder.mkdir(exist_ok=True)
        ends = START + timedelta(minutes=10)
        (self.folder / pcctl.TIMERS_FILE).write_text(json.dumps({"version": 1, "timers": [
            {"id": "0123abcd", "minutes": 10, "label": "", "set_at": START.isoformat(), "ends_at": ends.isoformat()}]}),
            encoding="utf-8")
        timers = self.make()
        self.assertEqual([timer.length_s for timer in timers.running()], [600])


class InstantControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.answers: list[tuple] = []
        self.waits: list[str] = []
        self.fallbacks: list[tuple] = []

    def make(self, get=None, **instant_values: object) -> instant_ui.InstantController:
        controller = instant_ui.InstantController(a_config(**instant_values), timers=None,
                                                  now=lambda: datetime(2026, 11, 10, 15, 42).astimezone(),
                                                  get=get or self.no_network, fahrenheit=False, threaded=False)
        controller.answered.connect(lambda *args: self.answers.append(args))
        controller.waiting.connect(self.waits.append)
        controller.fallback.connect(lambda *args: self.fallbacks.append(args))
        self.addCleanup(controller.shutdown)
        return controller

    def no_network(self, *_args):
        raise OSError("no network in the tests")

    def test_an_answer_on_this_pc_comes_at_once(self) -> None:
        controller = self.make()
        self.assertTrue(controller.take("what's 12 x 7", spoken=True))
        reply, words, spoken, ms = self.answers[0]
        self.assertEqual((reply.text, words, spoken), ("12 × 7 = 84", "what's 12 x 7", True))
        self.assertLess(ms, 500)
        self.assertFalse(controller.take("draft an email to Ana"))
        self.assertFalse(controller.take("set a timer for 5 minutes"))        # no timers: Claude's, as before

    def test_a_network_answer_and_one_that_comes_back_to_claude(self) -> None:
        def get(url, params, _timeout):
            return {"amount": 1.0, "base": "USD", "date": "2026-11-10", "rates": {"EUR": 0.9}}

        controller = self.make(get=get)
        self.assertTrue(controller.take("100 dollars in euros"))
        settle()
        self.assertEqual(self.waits, ["Checking the exchange rate..."])
        self.assertEqual(self.answers[0][0].text, "100.00 US dollars = 90.00 euros (ECB rate of 10 November).")
        failing = self.make()
        self.assertTrue(failing.take("weather in paris"))
        settle()
        self.assertEqual(self.fallbacks, [("weather in paris", False)])
        off = self.make(weather=False, currency=False)
        self.assertFalse(off.take("weather in paris"))


class AppWiringTests(unittest.TestCase):
    def submit(self, app: AppHarness, text: str) -> None:
        bar = app.c.window.reading.command_bar
        bar.set_text(text)
        bar.input.returnPressed.emit()
        settle()

    def test_a_typed_question_is_answered_without_a_planner_run(self) -> None:
        app = AppHarness(self)
        app.reading()
        c = app.c
        self.assertIsNotNone(c.instant)
        self.submit(app, "what's 12 x 7")
        self.assertFalse(app.factory.runner.started)                    # no Claude run
        self.assertFalse(c.ask.busy)
        entries = c.window.reading.conversation.entries()
        self.assertEqual([entry[2] for entry in entries[-2:]], ["what's 12 x 7", "12 × 7 = 84"])
        self.assertTrue(entries[-1][3].startswith("on this PC"))
        bar = c.window.reading.command_bar
        self.assertEqual(bar.status(), "12 × 7 = 84")
        self.assertEqual(bar.text(), "")                                # the words left the bar

    def test_a_timer_shows_on_home_and_survives_the_window_hiding(self) -> None:
        app = AppHarness(self)
        app.reading()
        c = app.c
        self.assertTrue(wait_for(lambda: True))
        self.submit(app, "set a timer for 10 minutes")
        self.assertEqual(len(c.instant.timers.running()), 1)
        self.assertTrue(c.window.reading.timer_chip_text().startswith("Timer 9:5")
                        or c.window.reading.timer_chip_text() == "Timer 10:00")
        c.window.hide()
        settle()
        self.assertEqual(len(c.instant.timers.running()), 1)            # the timer lives in the app, not the window
        self.submit(app, "cancel the timer")
        self.assertEqual(c.instant.timers.running(), ())
        self.assertEqual(c.window.reading.timer_chip_text(), "")

    def test_off_everything_goes_to_claude_as_before(self) -> None:
        app = AppHarness(self, config_extra="\n[instant]\nenabled = false\n")
        app.reading()
        self.assertIsNone(app.c.instant)
        self.assertIsNone(app.c.ask._instant)

    def test_web_research_and_shortcuts_still_win(self) -> None:
        app = AppHarness(self)
        app.reading()
        take = app.c.ask._instant
        seen: list[str] = []
        app.c.ask._instant = lambda words, spoken: seen.append(words) or take(words, spoken)
        self.submit(app, "web: what's 12 x 7")
        self.assertEqual(seen, [])                                      # "web:" is research's

    def test_the_logs_hold_no_words(self) -> None:
        app = AppHarness(self)
        app.reading()
        with self.assertLogs(level=logging.INFO) as logs:
            self.submit(app, "what's 4321 x 2")
        text = "\n".join(logs.output)
        self.assertIn("a maths answer", text)
        self.assertNotIn("4321", text)
        self.assertNotIn("8,642", text)


if __name__ == "__main__":
    unittest.main()
