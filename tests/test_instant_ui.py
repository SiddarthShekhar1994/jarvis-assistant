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


class Ticks:
    def __init__(self) -> None:
        self.at = 1000.0

    def __call__(self) -> float:
        return self.at


def calendar_events(now: datetime) -> dict:
    """Two accounts' events around ``now``: the personal one's next at +40 minutes, the work one's at +2 hours."""
    from briefing_reader.gcal import CalendarEvent

    soon, later = now + timedelta(minutes=40), now + timedelta(hours=2)
    return {"personal": [CalendarEvent("Physio", soon, soon + timedelta(minutes=30), location="Room 9")],
            "work": [CalendarEvent("Design review", later, later + timedelta(hours=1))]}


class CalendarControllerTests(unittest.TestCase):
    """Round 2: the calendar questions - a read, the 2-minute copy, a read too slow, an account not read."""

    NOW = datetime(2026, 11, 10, 13, 20).astimezone()

    def setUp(self) -> None:
        self.answers: list[tuple] = []
        self.waits: list[str] = []
        self.fallbacks: list[tuple] = []
        self.reads = 0
        self.ticks = Ticks()

    def read(self) -> bool:
        self.reads += 1
        return True

    def make(self, reader=None, **instant_values: object) -> instant_ui.InstantController:
        controller = instant_ui.InstantController(a_config(**instant_values), timers=None, now=lambda: self.NOW,
                                                  get=lambda *_args: {}, fahrenheit=False, threaded=False,
                                                  calendar_read=reader or self.read, monotonic=self.ticks)
        controller.answered.connect(lambda *args: self.answers.append(args))
        controller.waiting.connect(self.waits.append)
        controller.fallback.connect(lambda *args: self.fallbacks.append(args))
        self.addCleanup(controller.shutdown)
        return controller

    def a_read(self, problems=None):
        from briefing_reader import instant_calendar

        start, end = instant_calendar.read_range(self.NOW)
        return instant_calendar.CalendarRead(events=calendar_events(self.NOW), problems=dict(problems or {}),
                                             start=start, end=end)

    def test_a_read_then_two_minutes_of_instant_follow_ups(self) -> None:
        controller = self.make()
        self.assertTrue(controller.take("what's my next meeting", spoken=True))
        self.assertTrue(controller.take("am I free at 3 pm"))            # waits for the same read
        self.assertEqual((self.reads, self.waits), (1, ["Checking your calendar...", "Checking your calendar..."]))
        self.assertEqual(self.answers, [])
        controller.calendar_done(self.a_read())
        self.assertEqual([args[1] for args in self.answers], ["what's my next meeting", "am I free at 3 pm"])
        reply = self.answers[0][0]
        self.assertEqual(reply.speech, "Your next meeting is at 2 PM, sir, in 40 minutes.")
        self.assertIn("Physio", reply.text)
        self.assertNotIn("Physio", reply.speech)
        self.ticks.at += 119
        self.assertTrue(controller.take("how many meetings do I have today"))
        self.assertEqual((self.reads, len(self.answers), len(self.waits)), (1, 3, 2))   # from the copy: at once
        self.ticks.at += 2
        self.assertTrue(controller.take("what's my next meeting"))
        self.assertEqual(self.reads, 2)                                   # 2 minutes old: read again
        controller.forget_calendar()

    def test_off_or_not_set_up_goes_to_claude(self) -> None:
        self.assertFalse(self.make(reader=lambda: False).take("what's my next meeting"))
        self.assertFalse(self.make(calendar=False).take("what's my next meeting"))
        no_reader = instant_ui.InstantController(a_config(), timers=None, threaded=False)
        self.addCleanup(no_reader.shutdown)
        self.assertFalse(no_reader.flags().calendar)
        self.assertFalse(no_reader.take("what's my next meeting"))

    def test_a_read_too_slow_or_not_covering_goes_to_claude(self) -> None:
        controller = self.make()
        controller.take("what's my next meeting")
        self.assertTrue(controller._calendar_timer.isActive())
        controller._calendar_late()
        self.assertEqual(self.fallbacks, [("what's my next meeting", False)])
        controller.calendar_done(self.a_read())                          # it came later: kept for the next question
        self.assertEqual(len(self.answers), 0)
        self.assertTrue(controller.take("am I free at 3 pm"))
        self.assertEqual((self.reads, len(self.answers)), (1, 1))
        stale = self.make()
        stale.take("what's my next meeting")
        from briefing_reader import instant_calendar

        stale.calendar_done(instant_calendar.CalendarRead(events={"personal": []}))   # no range: not for sure
        self.assertEqual(self.fallbacks[-1], ("what's my next meeting", False))

    def test_an_account_not_read_is_said_and_not_kept_when_none_was(self) -> None:
        controller = self.make()
        controller.take("am I free at 2:30 pm")
        controller.calendar_done(self.a_read(problems={"work": "signedout"}))
        said = self.answers[0][0].speech
        self.assertIn("on your personal calendar", said)
        self.assertIn("your work calendar couldn't be read", said)
        self.ticks.at += instant_ui.CALENDAR_KEEP_S + 1
        controller.take("what's my next meeting")
        controller.calendar_done(self.a_read(problems={"work": "signedout", "personal": "signedout"}))
        self.assertIn("I couldn't read your personal and work calendars", self.answers[-1][0].speech)
        self.ticks.at += 1
        self.assertTrue(controller.take("what's my next meeting"))
        self.assertEqual(self.reads, 3)            # a read that read nothing is not kept: the next question reads again

    def test_the_logs_hold_no_titles(self) -> None:
        controller = self.make()
        with self.assertLogs("briefing_reader.instant_ui", level="INFO") as logs:
            controller.take("what's my next meeting")
            controller.calendar_done(self.a_read())
        text = "\n".join(logs.output)
        self.assertIn("a calendar answer", text)
        for secret in ("Physio", "Room 9", "Design review"):
            self.assertNotIn(secret, text)


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

    def test_a_calendar_question_reads_both_accounts_on_the_worker(self) -> None:
        from briefing_reader.gcal import CalendarNotSignedIn

        app = AppHarness(self)
        app.reading()
        c = app.c
        events = calendar_events(app.clock.now())
        app.calendars["personal"].list_events = lambda start, end, ids=("primary",): (
            app.calendars["personal"]._note("list") or list(events["personal"]))

        def signed_out(start, end, ids=("primary",)):
            app.calendars["work"]._note("list")
            raise CalendarNotSignedIn("Not signed in to Google Calendar (work account)")

        app.calendars["work"].list_events = signed_out
        self.assertTrue(c.instant.flags().calendar)
        with self.assertLogs(level=logging.INFO) as logs:
            self.submit(app, "what's my next meeting")
            self.assertTrue(wait_for(lambda: any("Physio" in entry[2] for entry in
                                                 c.window.reading.conversation.entries())))
        self.assertFalse(app.factory.runner.started)                    # no Claude run
        self.assertIn("calendar", app.calendars["personal"].threads)     # read on the calendar worker
        self.assertIn("calendar", app.calendars["work"].threads)
        entries = c.window.reading.conversation.entries()
        self.assertEqual(entries[-2][2], "what's my next meeting")
        self.assertIn("Physio", entries[-1][2])
        self.assertIn("Your work calendar couldn't be read", entries[-1][2])
        self.assertTrue(entries[-1][3].startswith("From Google Calendar"))
        self.assertIn("Next: Physio", c.window.reading.command_bar.status())
        said = " ".join(text for _kind, text, _key in app.voice.said)
        self.assertIn("Your next meeting on your personal calendar is at", said)
        self.assertNotIn("Physio", said)
        self.assertNotIn("Room 9", said)
        log_text = "\n".join(logs.output)
        self.assertIn("1 calendar(s) read, 1 not", log_text)
        self.assertNotIn("Physio", log_text)
        reads = len(app.calendars["personal"].calls)
        self.submit(app, "am I free at 3 pm")                            # within 2 minutes: from the copy
        self.assertEqual(len(app.calendars["personal"].calls), reads)
        self.assertFalse(app.factory.runner.started)

    def test_calendar_answers_are_off_without_the_calendar(self) -> None:
        app = AppHarness(self, config_extra="\n[instant]\ncalendar = false\n")
        app.reading()
        self.assertFalse(app.c.instant.flags().calendar)


if __name__ == "__main__":
    unittest.main()
