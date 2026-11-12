"""Lines that may be private are said while Jarvis's voice is made on this PC (owner decision 2026-11-12): the event an
instant answer is about by its title, reminder and timer labels, a task's lines, and the consent card says so. Places,
declined events and everything on the online voice stay as they were. Nothing here speaks, reaches Google or the
network."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from briefing_reader import desktop_view, instant, instant_calendar, instant_ui
from briefing_reader.gcal import CalendarEvent
from briefing_reader.instant import CAL_DAY, CAL_FREE, CAL_MORE, CAL_NEXT, CalendarAsk
from tests.test_instant_calendar import ASKS, NOW, SECRETS, TODAY, a_read, at, timed
from tests.test_instant_ui import PACIFIC, Clock

PLACES = ("Cafe Rosa", "Room 4", "Main St", "meet.google", "Skipped party")
_app = None


def setUpModule() -> None:
    global _app
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(["test_local_voice_private", "-platform", "offscreen"])


def said(ask: CalendarAsk, read=None, *, titles: bool = True, now=NOW) -> str:
    return instant_calendar.reply(ask, read or a_read(), now=now, address="sir", say_titles=titles).speech


class CalendarTitlesTests(unittest.TestCase):
    def test_the_next_meeting_by_its_title(self) -> None:
        self.assertEqual(said(CalendarAsk(CAL_NEXT)), "Your next meeting, Design review, is at 2 PM, sir, in 40 minutes.")

    def test_what_is_left_the_day_and_tomorrow_name_the_one_they_are_about(self) -> None:
        self.assertIn("the next is Design review, at 2 PM", said(CalendarAsk(CAL_MORE)))
        self.assertIn("the next is Design review, at 2 PM", said(CalendarAsk(CAL_DAY)))
        self.assertIn("the first is Gym, at 7 AM", said(CalendarAsk(CAL_DAY, day=1)))
        self.assertIn("you have Design review, ", said(CalendarAsk(CAL_FREE, at=at(TODAY, 14, 15))))

    def test_places_and_declined_events_are_never_said(self) -> None:
        for ask in ASKS:
            with self.subTest(ask=ask):
                for place in PLACES:
                    self.assertNotIn(place.casefold(), said(ask).casefold())

    def test_a_title_s_links_and_addresses_stay_off_the_voice(self) -> None:
        busy = [timed("Call https://zoom.us/j/123 with bob@example.com", TODAY, (14, 0), (15, 0))]
        speech = said(CalendarAsk(CAL_NEXT), a_read(personal=busy, work=[]))
        self.assertNotIn("zoom", speech)
        self.assertNotIn("@", speech)
        self.assertIn("Call", speech)

    def test_the_online_voice_still_says_no_title(self) -> None:
        for ask in ASKS:
            with self.subTest(ask=ask):
                speech = said(ask, titles=False)
                for secret in SECRETS:
                    self.assertNotIn(secret.casefold(), speech.casefold())

    def test_several_at_once_are_named_together(self) -> None:
        both = [timed("Standup", TODAY, (14, 0), (14, 30)), timed("Review", TODAY, (14, 0), (15, 0))]
        speech = said(CalendarAsk(CAL_NEXT), a_read(personal=both, work=[]))
        self.assertTrue(speech.startswith("You have 2 meetings next, at 2 PM, sir, in 40 minutes: "), speech)
        self.assertTrue(speech.endswith(("Standup and Review.", "Review and Standup.")), speech)

    def test_the_one_on_now(self) -> None:
        evening = at(TODAY, 20, 0)
        late = [timed("Late call", TODAY, (19, 30), (21, 0))]
        self.assertIn("Your meeting now, Late call, ends at 9 PM",
                      said(CalendarAsk(CAL_NEXT), a_read(personal=late, work=[], now=evening), now=evening))
        self.assertIn("Just the one on now, sir: Late call, until 9 PM",
                      said(CalendarAsk(CAL_MORE), a_read(personal=late, work=[], now=evening), now=evening))


class LabelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.clock = Clock()
        self.ended: list[tuple[str, str]] = []

    def make(self, say_labels: bool) -> instant_ui.TimerController:
        timers = instant_ui.TimerController(self.folder, now=self.clock.now, chime=lambda: None, say_labels=say_labels)
        timers.ended.connect(lambda text, spoken: self.ended.append((text, spoken)))
        self.addCleanup(timers.shutdown)
        timers.start()
        return timers

    def remind(self, timers, words: str):
        found = instant.understand(words, now=self.clock.now().astimezone(PACIFIC), flags=instant.InstantFlags())
        self.assertIsInstance(found, instant.ReminderAsk, words)
        return timers.handle_reminder(found)

    def test_a_reminder_says_its_label_with_the_local_voice(self) -> None:
        timers = self.make(True)
        self.remind(timers, "remind me at 5 pm to call mum")
        self.clock.at += timedelta(minutes=78)
        timers.check()
        self.assertEqual(self.ended, [("It's 5:00 PM: call mum.", "Sir, your 5 PM reminder: call mum.")])

    def test_a_timer_says_its_label_with_the_local_voice_and_not_without(self) -> None:
        for say_labels, expected in ((True, "Your 1 minute 30 seconds timer for the tea is done, sir."),
                                     (False, "Your 1 minute 30 seconds timer is done, sir.")):
            with self.subTest(say_labels=say_labels):
                self.ended.clear()
                timers = self.make(say_labels)
                timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=90, label="the tea"))
                self.clock.at += timedelta(seconds=91)
                timers.check()
                self.assertEqual(self.ended[-1][1], expected)
                timers.shutdown()

    def test_a_label_s_link_stays_off_the_voice(self) -> None:
        timers = self.make(True)
        timers.handle(instant.TimerAsk(instant.TIMER_SET, seconds=60, label="https://example.com/x"))
        self.clock.at += timedelta(seconds=61)
        timers.check()
        self.assertNotIn("example.com", self.ended[-1][1])


class ConsentRowTests(unittest.TestCase):
    def test_the_row_follows_where_the_voice_is_made(self) -> None:
        local = desktop_view.consent_task_row("DuckDuckGo", "Bing and Google", local=True)
        online = desktop_view.consent_task_row("DuckDuckGo", "Bing and Google", local=False)
        self.assertEqual(online, desktop_view.CONSENT_TASK_ROW.format(engine="DuckDuckGo", others="Bing and Google"))
        self.assertIn(desktop_view.CONSENT_VOICE_LOCAL, local)
        self.assertNotIn("Microsoft", local)
        self.assertIn("waits for your click", local)


class AgentGateTests(unittest.TestCase):
    def controller(self, engine: str, speak_private: bool = False):
        from briefing_reader import agent_ui

        controller = agent_ui.AgentController.__new__(agent_ui.AgentController)
        controller.config = SimpleNamespace(voice=SimpleNamespace(engine=engine),
                                            agent=SimpleNamespace(speak_private=speak_private))
        controller.stopped_by = ""
        controller.firewall = SimpleNamespace(speakable=lambda line: False)     # the online gate says no
        controller._words = lambda text: text
        return controller

    def test_the_local_voice_says_a_line_the_online_gate_would_keep(self) -> None:
        self.assertEqual(self.controller("local").spoken("Ana's figures are 42.", "It's on the card."),
                         "Ana's figures are 42.")
        self.assertEqual(self.controller("online").spoken("Ana's figures are 42.", "It's on the card."),
                         "It's on the card.")
        self.assertEqual(self.controller("online", speak_private=True).spoken("Ana's figures.", ""), "Ana's figures.")

    def test_after_a_stop_nothing_is_said_even_locally(self) -> None:
        controller = self.controller("local")
        controller.stopped_by = "owner"
        self.assertEqual(controller.spoken("Anything.", "Fixed."), "")


if __name__ == "__main__":
    unittest.main()
