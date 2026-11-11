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
from zoneinfo import ZoneInfo

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
                                       "Your 1 minute 30 seconds timer is done, sir.")])     # round 2: label never said
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


PACIFIC = ZoneInfo("America/Los_Angeles")      # START is 3:42 PM here, a Tuesday


class ReminderTests(unittest.TestCase):
    """Round 2: reminders at a clock time in the one timer book - set, listed, cancelled, said at their time (the
    label on screen only), restored after a restart, and every older timer file still loading."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.clock = Clock()
        self.chimes: list[int] = []
        self.ended: list[tuple[str, str]] = []

    def make(self) -> instant_ui.TimerController:
        timers = instant_ui.TimerController(self.folder, now=self.clock.now, chime=lambda: self.chimes.append(1),
                                            local=lambda moment: moment.astimezone(PACIFIC))
        timers.ended.connect(lambda text, said: self.ended.append((text, said)))
        self.addCleanup(timers.shutdown)
        timers.start()
        return timers

    def remind(self, timers: instant_ui.TimerController, text: str):
        found = instant.understand(text, now=self.clock.now().astimezone(PACIFIC), flags=instant.InstantFlags())
        self.assertIsInstance(found, instant.ReminderAsk, text)
        return timers.handle_reminder(found)

    def test_set_listed_and_said_at_its_time_with_no_label(self) -> None:
        timers = self.make()
        reply = self.remind(timers, "remind me at 5 pm to call mum")
        self.assertEqual((reply.text, reply.speech), ("Reminder set for 5:00 PM: call mum.", "Reminder set for 5 PM, sir."))
        self.assertEqual((timers.chip_text(), timers.ticking()), ("Reminder 5:00 PM", False))
        tomorrow = self.remind(timers, "remind me tomorrow at 9 am to stretch")
        self.assertEqual(tomorrow.text, "Reminder set for 9:00 AM tomorrow: stretch.")
        self.assertEqual(tomorrow.speech, "Reminder set for 9 AM tomorrow, sir.")
        listed = self.remind(timers, "what reminders do I have")
        self.assertEqual(listed.text, "2 reminders:\n• 5:00 PM - call mum\n• 9:00 AM tomorrow - stretch")
        self.assertEqual(listed.speech, "You have 2 reminders, sir; the next is at 5 PM.")
        self.clock.at += timedelta(minutes=77)
        timers.check()
        self.assertEqual(self.ended, [])                                 # 4:59 PM: not yet
        self.clock.at += timedelta(minutes=1)
        timers.check()
        self.assertEqual(self.ended, [("It's 5:00 PM: call mum.", "Sir, your 5 PM reminder.")])
        self.assertEqual(self.chimes, [1])
        self.assertEqual(timers.chip_text(), "Reminder 9:00 AM tomorrow")
        for reply_text in (reply.speech, tomorrow.speech, listed.speech, self.ended[0][1]):
            self.assertNotIn("mum", reply_text)
            self.assertNotIn("stretch", reply_text)

    def test_a_timer_that_comes_first_shows_its_countdown(self) -> None:
        timers = self.make()
        self.remind(timers, "remind me at 5 pm")
        timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=600))
        self.assertEqual((timers.chip_text(), timers.ticking()), ("Timer 10:00", True))
        timers.handle(instant.TimerAsk(instant.TIMER_CANCEL, every=True))      # "cancel all timers": timers only
        self.assertEqual(len(timers.reminders()), 1)
        self.assertEqual(timers.chip_text(), "Reminder 5:00 PM")
        left = timers.handle(instant.TimerAsk(instant.TIMER_LEFT, bare=True))
        self.assertIsNone(left)                                           # no timer runs: Claude's, as before

    def test_cancel_by_time_one_or_all(self) -> None:
        timers = self.make()
        self.assertEqual(self.remind(timers, "cancel my reminder").text, "No reminder is set; nothing was cancelled.")
        self.remind(timers, "remind me at 5 pm to call mum")
        self.remind(timers, "remind me at 6 pm")
        which = self.remind(timers, "cancel my reminder")
        self.assertIn("2 reminders are set; say \"cancel the 5 PM reminder\"", which.text)
        self.assertEqual(len(timers.reminders()), 2)                     # nothing on a guess
        self.assertEqual(self.remind(timers, "cancel the 7 pm reminder").text,
                         "No reminder is set for 7 PM; nothing was cancelled.")
        gone = self.remind(timers, "cancel the 5 pm reminder")
        self.assertEqual((gone.text, gone.speech), ("Cancelled your 5:00 PM reminder (call mum).",
                                                    "Cancelled your 5 PM reminder, sir."))
        self.assertEqual(self.remind(timers, "cancel the 6 reminder").text, "Cancelled your 6:00 PM reminder.")
        self.remind(timers, "remind me at 8 pm")
        self.remind(timers, "remind me tomorrow at 8 am")
        self.assertEqual(self.remind(timers, "cancel all reminders").text, "Cancelled all 2 reminders.")
        self.assertEqual(timers.reminders(), ())

    def test_a_restart_restores_them_and_one_missed_is_said(self) -> None:
        first = self.make()
        self.remind(first, "remind me at 5 pm to call mum")
        self.remind(first, "remind me tomorrow at 9 am")
        first.handle(instant.TimerAsk(instant.TIMER_SET, seconds=60))
        first.shutdown()
        saved = json.loads((self.folder / pcctl.TIMERS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(sorted(entry.get("kind", "timer") for entry in saved["timers"]), ["reminder", "reminder",
                                                                                          "timer"])
        self.clock.at += timedelta(hours=2)                     # Jarvis was closed at 5 PM
        second = self.make()
        self.assertEqual(len(second.reminders()), 1)
        texts = sorted(text for text, _said in self.ended)
        self.assertIn("Your 5:00 PM reminder, while Jarvis was away: call mum.", texts)
        self.assertIn(("Your 5:00 PM reminder, while Jarvis was away: call mum.",
                       "Sir, your 5 PM reminder came while I was away."), self.ended)
        second.shutdown()
        self.clock.at += timedelta(hours=34)                    # closed again until Thursday 3:42 AM: Wednesday's 9 AM
        self.ended.clear()
        self.make()
        self.assertEqual(self.ended, [("Your 9:00 AM reminder yesterday came while Jarvis was away.",
                                       "Sir, your 9 AM reminder from yesterday came while I was away.")])

    def test_a_reminder_missed_by_more_than_two_days_goes_quietly(self) -> None:
        first = self.make()
        self.remind(first, "remind me at 5 pm to call mum")
        first.shutdown()
        self.clock.at += timedelta(hours=51)
        second = self.make()
        self.assertEqual((self.ended, second.reminders()), ([], ()))

    def test_the_file_both_ways_and_the_limits(self) -> None:
        self.folder.mkdir(exist_ok=True)
        ends = START + timedelta(hours=30)
        entries = [{"id": "0123abcd", "minutes": 10, "label": "", "set_at": START.isoformat(),
                    "ends_at": (START + timedelta(minutes=10)).isoformat()},
                   {"id": "0123abce", "minutes": 1800, "label": "pay rent", "set_at": START.isoformat(),
                    "ends_at": ends.isoformat(), "seconds": 108000, "kind": "reminder"},
                   {"id": "0123abcf", "minutes": 1800, "label": "", "set_at": START.isoformat(),
                    "ends_at": ends.isoformat(), "seconds": 108000},                # a timer can't be 30 hours
                   {"id": "0123abd0", "minutes": 5, "label": "", "set_at": START.isoformat(),
                    "ends_at": ends.isoformat(), "kind": "alarm"}]                  # no such kind
        (self.folder / pcctl.TIMERS_FILE).write_text(json.dumps({"version": 1, "timers": entries}), encoding="utf-8")
        timers = self.make()
        self.assertEqual([(timer.kind, timer.label) for timer in timers.book.running()],
                         [("timer", ""), ("reminder", "pay rent")])
        book = timers.book
        with self.assertRaises(ValueError):
            book.add_until(START + timedelta(hours=49))
        with self.assertRaises(ValueError):
            book.add_until(START - timedelta(minutes=1))
        for hour in range(pcctl.MAX_REMINDERS - 1):
            book.add_until(START + timedelta(hours=hour + 1))
        full = self.remind(timers, "remind me at 11 pm")
        self.assertIn(f"{pcctl.MAX_REMINDERS} reminders are set already", full.text)
        for _ in range(pcctl.MAX_TIMERS - 1):                   # timers keep their own five
            book.add_seconds(60)
        with self.assertRaises(pcctl.TimersFull):
            book.add_seconds(60)
        self.assertEqual(len(book.cancel_all(pcctl.TIMER_KIND)), pcctl.MAX_TIMERS)
        self.assertEqual(len(book.running()), pcctl.MAX_REMINDERS)

    def test_the_desktop_leaves_reminders_alone_and_its_chip_knows_them(self) -> None:
        book = pcctl.TimerBook(self.folder, now=self.clock.now)
        reminder = book.add_until(START + timedelta(hours=1), "call mum")
        self.assertEqual(repr(reminder), f"Timer({reminder.timer_id}, reminder)")
        self.assertIn("Reminder · ", pcctl.chip_text(book.running()))
        timer = book.add(5)
        self.assertEqual(pcctl.chip_text(book.running()), pcctl.chip_text([timer]) + " · 1 reminder")
        from tests.desktop_fakes import FakePc

        control = pcctl.PcControl(lambda: FakePc(), timers=book)
        change = control.cancel_timers()
        self.assertEqual(change.code, "")
        self.assertEqual(book.running(), (reminder,))                  # the desktop's "cancel the timers"
        self.assertEqual(control.cancel_timers().code, pcctl.NO_TIMER)
        self.assertTrue(pcctl.end_words(reminder).endswith(" reminder, sir - call mum."))
        self.assertEqual(book.clear(), 1)


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


class TownTests(unittest.TestCase):
    """Round 2: the weather's town by voice or typing - looked up, confirmed, kept in instant.json (never
    assistant.json), said, forgotten, and shown in Settings > Instant answers."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.answers: list[tuple] = []
        self.waits: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self.geocode: object = {"results": [{"name": "San Jose", "latitude": 37.34, "longitude": -121.89,
                                             "country": "United States", "country_code": "US", "admin1": "California",
                                             "population": 1013240}]}

    def get(self, url, params, _timeout):
        from briefing_reader import instant_net

        self.calls.append((url, dict(params)))
        if url == instant_net.GEOCODE_URL:
            if isinstance(self.geocode, Exception):
                raise self.geocode
            return self.geocode
        return {"current": {"temperature_2m": 18.0, "apparent_temperature": 18.0, "weather_code": 0},
                "daily": {"weather_code": [0, 1], "temperature_2m_max": [21.0, 22.0], "temperature_2m_min": [9.0, 10.0],
                          "precipitation_probability_max": [0, 5]}}

    def make(self, **instant_values: object) -> instant_ui.InstantController:
        store = instant_ui.TownStore(self.folder / instant_ui.INSTANT_FILE)
        controller = instant_ui.InstantController(a_config(**instant_values), timers=None, get=self.get,
                                                  fahrenheit=False, threaded=False, store=store)
        controller.answered.connect(lambda *args: self.answers.append(args))
        controller.waiting.connect(self.waits.append)
        self.addCleanup(controller.shutdown)
        return controller

    def say(self, controller: instant_ui.InstantController, text: str):
        self.assertTrue(controller.take(text), text)
        settle()
        return self.answers[-1][0]

    def test_set_said_used_for_the_weather_and_forgotten(self) -> None:
        controller = self.make(town="Oslo")
        changes: list[int] = []
        controller.townChanged.connect(lambda: changes.append(1))
        got = self.say(controller, "my town is San Jose")
        self.assertEqual(self.waits, ["Looking the place up..."])
        self.assertEqual((got.text, got.speech), ("Got it: San Jose, California, United States.",
                                                  "Got it, sir: San Jose, California."))
        self.assertEqual(changes, [1])
        self.assertEqual(controller.flags().town, "San Jose, California")       # it wins over [instant] town
        saved = json.loads((self.folder / instant_ui.INSTANT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["town"]["full"], "San Jose, California, United States")
        weather = self.say(controller, "what's the weather")
        self.assertIn("In San Jose, California:", weather.text)
        self.assertEqual(sum(1 for url, _params in self.calls if "geocoding" in url), 1)   # no second search
        said = self.say(controller, "what's my town")
        self.assertEqual(said.text, "Your weather town is San Jose, California, United States (you set it).")
        again = self.make(town="Oslo")                                         # Jarvis started again
        self.assertEqual(again.flags().town, "San Jose, California")
        gone = self.say(controller, "forget my town")
        self.assertEqual(gone.text, "Forgot your weather town. The weather now uses Oslo from config.toml.")
        self.assertEqual(gone.speech, "Done, sir: I've forgotten your town.")
        self.assertEqual(controller.flags().town, "Oslo")
        self.assertNotIn("town", json.loads((self.folder / instant_ui.INSTANT_FILE).read_text(encoding="utf-8")))
        self.assertEqual(self.say(controller, "forget my town").text,
                         "You hadn't set a weather town. The weather now uses Oslo from config.toml.")
        none = self.make()
        self.assertIn("No weather town is set", self.say(none, "what's my town").text)

    def test_several_none_or_no_service_leave_it_as_it_was(self) -> None:
        controller = self.make()
        self.geocode = {"results": [
            {"name": "Springfield", "latitude": 39.8, "longitude": -89.6, "country": "United States",
             "country_code": "US", "admin1": "Illinois", "population": 114394},
            {"name": "Springfield", "latitude": 37.2, "longitude": -93.3, "country": "United States",
             "country_code": "US", "admin1": "Missouri", "population": 169176}]}
        several = self.say(controller, "my town is Springfield")
        self.assertEqual(several.text, "There's more than one Springfield: Springfield, Missouri; Springfield, "
                                       "Illinois. Say the one you mean, like \"my town is Springfield, Missouri\". "
                                       "Your weather town wasn't changed.")
        self.assertEqual(several.speech, "There's more than one Springfield, sir. Which state or country?")
        suggested = instant.understand("my town is Springfield, Missouri", now=datetime.now().astimezone(),
                                       flags=instant.InstantFlags(weather=True))
        self.assertEqual(suggested.town, "Springfield, Missouri")       # what it suggests is understood
        self.geocode = {"generationtime_ms": 0.3}
        self.assertEqual(self.say(controller, "my town is Xyzzy").text,
                         "I couldn't find a place called Xyzzy. Your weather town wasn't changed.")
        self.geocode = OSError("no network")
        self.assertIn("couldn't be reached", self.say(controller, "use Paris for the weather").text)
        self.assertIsNone(controller.store.place())
        self.assertFalse((self.folder / instant_ui.INSTANT_FILE).exists())

    def test_i_live_in_is_kept_only_for_a_place_found(self) -> None:
        controller = self.make()
        fallbacks: list[tuple] = []
        controller.fallback.connect(lambda *args: fallbacks.append(args))
        self.geocode = {"generationtime_ms": 0.3}
        self.assertTrue(controller.take("I live in fear"))
        settle()
        self.assertEqual((fallbacks, self.answers), ([("I live in fear", False)], []))   # Claude's, as before
        self.assertFalse(controller.take("I live in London and work in Paris"))       # not a town at all
        self.geocode = {"results": [{"name": "San Jose", "latitude": 37.34, "longitude": -121.89,
                                     "country": "United States", "country_code": "US", "admin1": "California",
                                     "population": 1013240}]}
        self.assertEqual(self.say(controller, "I live in San Jose").text, "Got it: San Jose, California, United States.")

    def test_only_the_latest_town_asked_is_kept(self) -> None:
        controller = self.make()
        self.geocode = {"results": [{"name": "Oslo", "latitude": 59.9, "longitude": 10.7, "country": "Norway",
                                     "country_code": "NO", "admin1": "Oslo", "population": 580000},
                                    {"name": "San Jose", "latitude": 37.34, "longitude": -121.89,
                                     "country": "United States", "country_code": "US", "admin1": "California",
                                     "population": 1013240}]}
        self.assertTrue(controller.take("my town is Oslo"))
        self.assertTrue(controller.take("my town is San Jose"))        # asked before the first search answered
        settle()
        self.assertEqual([args[0].text for args in self.answers], ["Got it: San Jose, California, United States."])
        self.assertEqual(controller.store.place().shown, "San Jose, California")

    def test_typed_without_commas(self) -> None:
        controller = self.make()
        geocodes = []

        def get(url, params, timeout):
            geocodes.append(params.get("name"))
            if str(params.get("name")).casefold() == "san jose":
                return self.get(url, params, timeout)
            return {"generationtime_ms": 0.2}

        controller._get = get
        reply = self.say(controller, "my town is san jose california")
        self.assertEqual(reply.text, "Got it: San Jose, California, United States.")
        self.assertEqual(geocodes, ["san jose california", "san jose"])
        self.assertEqual(self.say(controller, "my town is San Jose, California, United States").text,
                         "Got it: San Jose, California, United States.")

    def test_a_bad_file_the_real_data_folder_and_the_logs(self) -> None:
        (self.folder / instant_ui.INSTANT_FILE).write_text('{"version": 1, "town": {"name": "x"}}', encoding="utf-8")
        with self.assertLogs("briefing_reader.instant_ui", level="WARNING"):
            self.assertIsNone(instant_ui.TownStore(self.folder / instant_ui.INSTANT_FILE).place())
        from briefing_reader.config import default_data_dir

        self.assertIsNone(instant_ui.instant_file(default_data_dir()))      # never the real one in the tests
        self.assertEqual(instant_ui.instant_file(self.folder), self.folder / "instant.json")
        controller = self.make()
        with self.assertLogs("briefing_reader", level="INFO") as logs:
            self.say(controller, "my town is San Jose")
        self.assertNotIn("San Jose", "\n".join(logs.output))

    def test_the_settings_rows(self) -> None:
        controller = self.make(town="Oslo")
        rows = dict(controller.settings_rows())
        self.assertEqual(rows["Weather town"], "Oslo - from config.toml")
        self.assertEqual(rows["Your calendars"], "Off - Google Calendar is off or not set up")
        self.assertIn("Open-Meteo", rows["Weather"])
        self.assertIn("Frankfurter", rows["Currencies"])
        self.say(controller, "my town is San Jose")
        self.assertEqual(dict(controller.settings_rows())["Weather town"],
                         "San Jose, California, United States - you set it")
        calendar = instant_ui.InstantController(a_config(), timers=None, threaded=False, calendar_read=lambda: True)
        self.addCleanup(calendar.shutdown)
        self.assertTrue(dict(calendar.settings_rows())["Your calendars"].startswith("On - every account's main"))
        off = dict(self.make(weather=False, currency=False, calendar=False).settings_rows())
        self.assertEqual((off["Weather"], off["Currencies"], off["Your calendars"]),
                         ("Off - [instant] weather", "Off - [instant] currency", "Off - [instant] calendar"))


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

    def test_an_account_not_read_is_said_and_read_again_next_time(self) -> None:
        controller = self.make()
        controller.take("am I free at 2:30 pm")
        controller.calendar_done(self.a_read(problems={"work": "signedout"}))
        said = self.answers[0][0].speech
        self.assertIn("on your personal calendar", said)
        self.assertIn("your work calendar couldn't be read", said)
        self.ticks.at += 1
        self.assertTrue(controller.take("what's my next meeting"))   # signed in meanwhile: read again, not the copy
        self.assertEqual(self.reads, 2)
        controller.calendar_done(self.a_read(problems={"work": "signedout", "personal": "signedout"}))
        self.assertIn("I couldn't read your personal and work calendars", self.answers[-1][0].speech)
        self.ticks.at += 1
        self.assertTrue(controller.take("what's my next meeting"))
        self.assertEqual(self.reads, 3)                                # nothing kept: the next question reads again
        controller.calendar_done(self.a_read())
        self.ticks.at += 1
        self.assertTrue(controller.take("what's my next meeting"))
        self.assertEqual(self.reads, 3)                                # every account read: kept 2 minutes

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
        self.submit(app, "am I free at 3 pm")           # the work calendar wasn't read: read again, never the copy
        self.assertTrue(wait_for(lambda: len(app.calendars["personal"].calls) == reads + 1))
        self.assertFalse(app.factory.runner.started)
        # One read at a time: a second ask while one is on its way waits for it.
        settle()
        self.assertTrue(wait_for(lambda: c._instant_calendar_job is None))
        seq = c._instant_calendar_seq
        self.assertTrue(c._instant_calendar_read())
        self.assertTrue(c._instant_calendar_read())
        self.assertEqual(c._instant_calendar_seq, seq + 1)

    def test_a_reminder_on_home_and_at_its_time(self) -> None:
        app = AppHarness(self)                                           # the app's clock: Sunday 1:52 PM
        app.reading()
        c = app.c
        self.submit(app, "remind me at 3 pm to stretch")
        self.assertFalse(app.factory.runner.started)
        self.assertEqual(len(c.instant.timers.reminders()), 1)
        reading = c.window.reading
        self.assertEqual(reading.conversation.entries()[-1][2], "Reminder set for 3:00 PM: stretch.")
        self.assertEqual(reading.timer_chip_text(), "Reminder 3:00 PM")
        self.assertEqual(reading.timer_chip.accessibleName(), "Reminder")
        self.assertEqual(c._timer_chip_tick.interval(), 30_000)          # no second-by-second ticking for it
        app.clock.advance(minutes=68)
        c.instant.timers.check()
        settle()
        self.assertEqual(reading.conversation.entries()[-1][2], "It's 3:00 PM: stretch.")
        said = [text for _kind, text, _key in app.voice.said]
        self.assertIn("Sir, your 3 PM reminder.", said)
        self.assertFalse(any("stretch" in text for text in said))
        self.assertEqual(reading.timer_chip_text(), "")

    def test_settings_has_the_instant_answers_group(self) -> None:
        app = AppHarness(self)
        app.reading()
        settings = app.c.window.reading.settings_page
        self.assertIn(instant_ui.SETTINGS_KEY, settings.group_buttons)
        settings.select_group(instant_ui.SETTINGS_KEY)
        rows = dict(settings.rows())
        self.assertTrue(rows["Your calendars"].startswith("On - every account's main calendar"))
        self.assertEqual(rows["Weather town"], "None - say or type \"my town is\" and its name")
        self.assertIn("\"my town is\"", settings.text_label.text())
        prefs = app.config.data_dir / "assistant.json"
        before = prefs.read_bytes() if prefs.exists() else None
        app.c.instant.store.set(instant_ui.instant_net.Place("Oslo", "", "Norway", 59.9, 10.7, "Oslo, Norway",
                                                             "Oslo, Norway", "Oslo, Norway"))
        app.c.instant.townChanged.emit()
        self.assertEqual(dict(settings.rows())["Weather town"], "Oslo, Norway - you set it")
        self.assertTrue((app.config.data_dir / instant_ui.INSTANT_FILE).is_file())
        self.assertEqual(prefs.read_bytes() if prefs.exists() else None, before)   # never assistant.json

    def test_calendar_answers_are_off_without_the_calendar(self) -> None:
        app = AppHarness(self, config_extra="\n[instant]\ncalendar = false\n")
        app.reading()
        self.assertFalse(app.c.instant.flags().calendar)


if __name__ == "__main__":
    unittest.main()
