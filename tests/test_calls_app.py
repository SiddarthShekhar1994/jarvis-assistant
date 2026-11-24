"""Tests for quiet during calls in the app (smaller items part 7; Qt offscreen, the test guard on). The call is a fake
probe (``AppController.call_probe``), never the registry. During a call his words are captioned, not said; a playing
line stops; Home's resting line says so; "Hey Jarvis" pauses; a timer's end sound keeps quiet while its toast still
shows; a scheduled briefing's arrival waits for the call's end (past the catch-up window nothing is shown); with
[voice] quiet_in_calls off nothing is watched and he speaks as today; the logs hold no app's name; Settings > Voice
has the row and Change sets the key."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from briefing_reader import instant_ui, overrides, settings_view, states_view, ui, wake_ui
from tests.test_launch_flow import KEY, READY, Flow, setUpModule  # noqa: F401 - the module's QApplication
from tests.ui_fakes import settle

CALL_LINE = "You're on a call, sir: I'll keep to captions."


class CallFlow(Flow):
    def __init__(self, test: unittest.TestCase, *, quiet_in_calls: bool = True, **kwargs) -> None:
        super().__init__(test, **kwargs)
        self.on_call = False
        self.asked = 0
        if not quiet_in_calls:
            self.config = dataclasses.replace(self.config, voice=dataclasses.replace(self.config.voice,
                                                                                    quiet_in_calls=False))
            self.c.config = self.config
        self.c.call_probe = self._probe
        test.addCleanup(self._stop_poll)

    def _probe(self) -> bool:
        self.asked += 1
        return self.on_call

    def _stop_poll(self) -> None:
        if self.c._call_timer is not None:
            self.c._call_timer.stop()

    def poll(self) -> None:
        watch = self.c._call_watch
        if watch is not None:
            watch.hold_s = 0.0                      # the 6 s hold is calls.py's (test_calls)
        self.c._poll_call()
        settle()


class ArrivalTests(unittest.TestCase):
    def test_a_scheduled_briefing_waits_for_the_call_to_end(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        c = flow.c
        flow.on_call = True
        with self.assertLogs("briefing_reader", "INFO") as logs:
            c.start()
            flow.attempt(flow.page)
            flow.done(timed_out=False)              # the fetch ended with the briefing in: still waits, no quit
        self.assertEqual(c.state, ui.STATE_WAITING)
        self.assertFalse(c.window.isVisible())
        self.assertEqual(flow.voice.said, [])
        self.assertTrue(any("waits for the call to end" in line for line in logs.output))
        self.assertTrue(flow.reading.on_call())
        self.assertEqual(flow.reading.captions.resting(), CALL_LINE)
        with mock.patch.object(ui, "force_foreground") as forced:
            flow.on_call = False
            flow.poll()
        forced.assert_not_called()
        self.assertEqual(c.state, ui.STATE_READING)
        self.assertTrue(c.window.isVisible())
        self.assertEqual(len(flow.voice.said), 1)
        kind, text, key = flow.voice.said[0]
        self.assertEqual((kind, key), ("greeting", f"announce:{KEY}"))
        self.assertTrue(text.endswith(READY))
        self.assertFalse(flow.reading.on_call())
        self.assertNotEqual(flow.reading.captions.resting(), CALL_LINE)
        for line in logs.output:
            self.assertNotIn("microphone entr", line)

    def test_past_the_catch_up_window_nothing_is_shown(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        c = flow.c
        flow.on_call = True
        c.start()
        flow.attempt(flow.page)
        flow.now += timedelta(hours=4)
        with mock.patch.object(ui.QCoreApplication, "quit"), self.assertLogs("briefing_reader.ui", "INFO") as logs:
            flow.on_call = False
            flow.poll()
        self.assertEqual(c.state, ui.STATE_QUITTING)
        self.assertFalse(c.window.isVisible())
        self.assertEqual(flow.voice.said, [])
        self.assertTrue(any("past its catch-up window" in line for line in logs.output))

    def test_a_call_that_outlasts_the_catch_up_window_ends_the_hidden_run(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        c = flow.c
        flow.on_call = True
        c.start()
        flow.attempt(flow.page)
        deadline = c._call_deadline
        self.assertIsNotNone(deadline)
        self.assertTrue(deadline.isActive() and deadline.isSingleShot())
        self.assertTrue(0 < deadline.interval() <= 3 * 3600 * 1000)
        with mock.patch.object(ui.QCoreApplication, "quit"), self.assertLogs("briefing_reader.ui", "INFO") as logs:
            deadline.timeout.emit()                     # the call never ended
            settle()
        self.assertEqual(c.state, ui.STATE_QUITTING)
        self.assertFalse(c.window.isVisible())
        self.assertEqual(flow.voice.said, [])
        self.assertTrue(any("past its catch-up window" in line for line in logs.output))

    def test_the_deadline_goes_when_the_call_ends_in_time(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        c = flow.c
        flow.on_call = True
        c.start()
        flow.attempt(flow.page)
        deadline = c._call_deadline
        flow.on_call = False
        flow.poll()
        self.assertIsNotNone(deadline)
        self.assertIsNone(c._call_deadline)            # stopped and let go
        self.assertEqual(c.state, ui.STATE_READING)

    def test_a_setup_error_is_shown_at_once_during_a_call(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        c = flow.c
        c.startup_error = "NOTION_TOKEN is not set. Put it in .env."
        flow.on_call = True
        c.start()
        self.assertTrue(c.window.isVisible())         # text only, without the focus: never held
        self.assertFalse(c._call_arrival_held)
        self.assertTrue(any("I can't reach your briefing" in " ".join(map(str, entry)) for entry in flow.entries()))
        self.assertEqual(flow.voice.said, [])

    def test_no_call_arrives_as_today(self) -> None:
        flow = CallFlow(self, expected_run="AM")
        flow.c.start()
        flow.attempt(flow.page)
        self.assertEqual(flow.c.state, ui.STATE_READING)
        self.assertEqual(len(flow.voice.said), 1)
        self.assertGreater(flow.asked, 0)


class VoiceTests(unittest.TestCase):
    def open(self, **kwargs) -> CallFlow:
        flow = CallFlow(self, expected_run=None, **kwargs)
        flow.c.start()
        flow.attempt(flow.page)
        flow.voice.said.clear()
        return flow

    def test_his_words_are_captioned_not_said_during_a_call(self) -> None:
        flow = self.open()
        c = flow.c
        flow.on_call = True
        stops = []
        with mock.patch.object(c, "_voice_call", side_effect=lambda name, *a: stops.append(name)):
            flow.poll()
        self.assertIn("stop", stops)                   # a line under way stops at once
        self.assertTrue(c._voice_silent())
        self.assertFalse(c._say(ui.KIND_REPLY, "Three meetings today, sir."))
        self.assertEqual(flow.voice.said, [])
        self.assertEqual(flow.reading.captions.resting(), CALL_LINE)
        self.assertNotIn(CALL_LINE, [entry[2] for entry in flow.entries()])   # shown on Home, never an entry
        flow.on_call = False
        flow.poll()
        self.assertFalse(c._voice_silent())
        self.assertTrue(c._say(ui.KIND_REPLY, "Three meetings today, sir."))
        self.assertEqual(len(flow.voice.said), 1)

    def test_hey_jarvis_holds_on_a_call(self) -> None:
        flow = self.open()
        self.assertNotIn(wake_ui.HOLD_CALL, flow.c._wake_holds())
        flow.on_call = True
        flow.poll()
        self.assertIn(wake_ui.HOLD_CALL, flow.c._wake_holds())
        flow.on_call = False
        flow.poll()
        self.assertNotIn(wake_ui.HOLD_CALL, flow.c._wake_holds())

    def test_a_timer_s_end_sound_keeps_quiet_its_toast_still_shows(self) -> None:
        flow = self.open()
        c = flow.c
        with mock.patch.object(instant_ui, "_chime") as chime:
            c._timer_chime()
            self.assertEqual(chime.call_count, 1)
            flow.on_call = True
            flow.poll()
            c._timer_chime()
            self.assertEqual(chime.call_count, 1)        # dropped, not kept for later
        source = Path(ui.__file__).read_text(encoding="utf-8")
        self.assertIn("chime=self._timer_chime", source)    # the app's timers end with this sound

    def test_the_desktop_timer_sound_follows_the_call(self) -> None:
        from briefing_reader import desktop_ui

        submitted: list = []
        stub = SimpleNamespace(chime_quiet=lambda: True,
                               _ensure_worker=lambda: (SimpleNamespace(submit=submitted.append),
                                                       SimpleNamespace(chime="chime")))
        desktop_ui.DesktopController._chime(stub)
        self.assertEqual(submitted, [])
        stub.chime_quiet = lambda: False
        desktop_ui.DesktopController._chime(stub)
        self.assertEqual(submitted, ["chime"])

    def test_the_standing_line_and_the_sounds_wait_on_a_call(self) -> None:
        from briefing_reader import look_ui, standing_ui

        host = SimpleNamespace(_locked_now=lambda: False, _meeting_recording=lambda: False,
                               _focus_quiet=lambda: False, _call_quiet=lambda: True, voice=None, productivity=None)
        self.assertEqual(look_ui.LookController._quiet(SimpleNamespace(host=host)), "call")
        stand = SimpleNamespace(host=host, _now=lambda: flow_now(),
                                settings=SimpleNamespace(quiet_from="22:00", quiet_until="22:00"),
                                busy_probe=lambda: False)
        self.assertEqual(standing_ui.StandingController._holds(stand), "on a call")
        host._call_quiet = lambda: False
        self.assertEqual(standing_ui.StandingController._holds(stand), "")

    def test_switched_off_nothing_is_watched_and_he_speaks(self) -> None:
        flow = CallFlow(self, expected_run=None, quiet_in_calls=False)
        flow.on_call = True
        with self.assertLogs("briefing_reader.ui", "INFO") as logs:
            flow.c.start()
        flow.attempt(flow.page)
        flow.poll()
        self.assertIsNone(flow.c._call_watch)
        self.assertEqual(flow.asked, 0)                   # never read
        self.assertTrue(any("Calls: not watched" in line for line in logs.output))
        self.assertFalse(flow.c._call_quiet())
        self.assertTrue(flow.c._say(ui.KIND_REPLY, "Three meetings today, sir."))
        self.assertNotEqual(flow.reading.captions.resting(), CALL_LINE)


def flow_now():
    from tests.ui_fakes import NOW

    return NOW


class SettingsTests(unittest.TestCase):
    def test_the_voice_row_and_its_key(self) -> None:
        self.assertIn("voice.quiet_in_calls", overrides.EDITABLE)
        self.assertEqual(overrides.EDITABLE["voice.quiet_in_calls"].kind, overrides.KIND_BOOL)
        self.assertIs(overrides.default("voice.quiet_in_calls"), True)
        self.assertEqual(settings_view.EDIT_ROWS["voice"]["Quiet during calls"], ("voice.quiet_in_calls",))
        voice = SimpleNamespace(engine="online", voice="x", rate="+0%", volume="+0%", offline_voice="",
                                quiet_in_calls=True)
        config = SimpleNamespace(voice=voice, assistant=SimpleNamespace(speak=True, address="sir"))
        rows = dict(settings_view.settings_rows(config, "voice"))
        self.assertEqual(rows["Quiet during calls"], "Yes - captions only while another app uses the microphone")
        voice.quiet_in_calls = False
        self.assertEqual(dict(settings_view.settings_rows(config, "voice"))["Quiet during calls"],
                         "No - speaks as usual")
        self.assertEqual(states_view.call_line("sir"), CALL_LINE)


if __name__ == "__main__":
    unittest.main()
