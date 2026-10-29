"""Jarvis on your desktop, P2.4, in the app: the PC's controls from the HUD's bar (SPEC 2.1, 2.2, 10.1 and 16; hard
rules 1, 14 and 15) - Jarvis's own words on the caption, Live's step (levels, states and counts only), no planner
run; a screenshot hands the HUD's own windows over to be left out of the picture; Jarvis's timers (the HUD chip
while one runs, the toast and the sound at the end - also when the end came while the PC slept -, restored at
start); THE CONSENT GATE and Turn off exactly as for the opens (nothing before the Turn on; a Turn off stops a
control right before its OS call and drops the timers).

Qt runs offscreen in this process and shows nothing. No real OS call: the HUD's native seam is a recording fake, the
desktop's backend a fake over a temporary folder, the PC's controls tests.desktop_fakes.FakePc (no volume, no media
key, no screen, no sound), the timers' folder the harness's temporary data folder and their clock a fake one;
claude.exe is never started.
"""

from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import live, ui
from briefing_reader.desktop import pcctl
from tests.desktop_fakes import FakeClock, FakePc
from tests.test_desktop_requests import DesktopRequestApp
from tests.ui_fakes import fonts, settle, wait_for

CANARY = "CANARY9f3"
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_desktop_pcctl_app", "-platform", "offscreen"])
    fonts()


class PcApp(DesktopRequestApp):
    """DesktopRequestApp with the PC's controls a FakePc and the timers on a fake clock (2030-01-01 10:00: later
    than any real clock in these tests, so no real-time check ever ends a timer here)."""

    START = datetime(2030, 1, 1, 10, 0)

    def make_pc(self, **harness: Any) -> None:
        self.pc = FakePc()
        self.clock = FakeClock(self.START)
        self.make_ask(**harness)
        self.desktop.pc_factory = lambda: self.pc
        self.desktop.timer_clock = self.clock.now
        self.toasts: list[str] = []
        self.desktop.timerEnded.connect(self.toasts.append)

    def say(self, typed: str) -> str:
        self.type_on_hud(typed)
        self.answered()
        return self.hud().caption_text()

    def step_items(self) -> list[str]:
        return [item.text for task in self.desktop_tasks() for step in task.steps if step.kind == live.DESKTOP_STEP
                for item in step.items]

    def timer_chip(self) -> str:
        texts = [text for text in self.hud().chip_texts() if text.startswith(("Timer", "2 timers"))]
        return texts[0] if texts else ""

    def check_timers(self) -> None:
        """What the timers' QTimer does when it fires (at an end, or at the latest TIMER_CHECK_S later)."""
        self.desktop._on_timer_check()
        settle()


class ControlsFromTheHudTests(PcApp):
    def test_volume_music_and_a_screenshot_from_the_huds_bar(self) -> None:
        self.make_pc()
        self.enter()
        self.assertEqual(self.say("volume 30"), "Volume 30, sir.")
        self.assertEqual((self.pc.level, self.pc.muted), (30, False))
        self.pc.media = pcctl.PLAYING
        self.assertEqual(self.say("pause"), "Paused, sir.")
        self.pc.media = None                                                  # winrt missing: the media key
        self.assertEqual(self.say("next song"), "Pressed next track, sir - it can't be put back.")
        self.assertEqual(self.pc.keys, ["next"])
        self.assertEqual(self.say("screenshot"), "Screenshot saved to your screenshots folder, sir.")
        mode, hud_windows = self.pc.captures[-1]
        self.assertEqual(mode, pcctl.SCREEN)
        self.assertIn(int(self.hud().internalWinId()), hud_windows)          # the HUD left out of the picture
        self.assertEqual(self.fake.asked("save_png")[0].rsplit("\\", 1)[0], "C:\\Users\\Owner\\Jarvis\\Screenshots")
        self.assertEqual(self.runs(), [])                                     # no planner run: nothing left the PC
        self.assertEqual(set(self.pc.threads), {"desktop"})                   # all on the desktop thread
        items = self.step_items()
        self.assertIn("On this PC - volume - instant - done (level 50 -> 30)", items)
        self.assertIn("On this PC - media - instant - done (the player's controls: playing -> paused)", items)
        self.assertIn("On this PC - media - instant - done (a media key: the player's state unknown - can't be put "
                      "back)", items)
        self.assertIn("On this PC - screenshot - instant - done (the screen, 1 file saved on this PC)", items)
        self.assert_on_hud()

    def test_a_refusal_is_said_plainly(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.device = False
        self.assertEqual(self.say("mute"), "I can't find your speakers, sir - nothing was changed.")
        self.assertEqual(self.say("volume 150"), "The volume goes from 0 to 100, sir - nothing was changed.")
        self.assertEqual(self.pc.methods(), ["audio"])

    def test_without_the_controls_nothing_happens(self) -> None:
        self.make_ask()                                       # the real seam: it refuses to be made in a test
        self.enter()
        self.type_on_hud("volume 30")
        self.answered()
        self.assertEqual(self.hud().caption_text(),
                         "I can't reach this PC's controls right now, sir - nothing was changed.")


class TimerAppTests(PcApp):
    def test_a_timer_shows_its_chip_and_ends_with_the_toast_and_a_sound(self) -> None:
        self.make_pc()
        self.enter()
        self.assertEqual(self.say(f"remind me in 1 minute to {CANARY}"), "Timer set for 1 minute, sir.")
        self.assertEqual(self.timer_chip(), "Timer \u00b7 ends 10:01 AM")
        self.assertTrue(self.desktop._timer_check.isActive())
        self.clock.advance(30)
        self.check_timers()
        self.assertEqual(self.toasts, [])                                     # not yet
        self.clock.advance(31)
        self.check_timers()
        self.assertEqual(self.toasts, [f"Your 1-minute timer is up, sir - {CANARY}."])
        toast = self.c._toast_window
        self.assertEqual((toast.line(), toast.action_text(), toast.key()),
                         (self.toasts[0], ui.TOAST_TIMER_TEXT, ui.TOAST_TIMER))
        self.assertTrue(wait_for(lambda: self.pc.chimes == 1), "the end's sound, on the desktop thread")
        self.assertEqual(self.timer_chip(), "")
        self.assertFalse(self.desktop._timer_check.isActive())
        QTest.mouseClick(toast.action_button, Qt.MouseButton.LeftButton)      # its OK only puts it away
        settle()
        self.assertFalse(toast.isVisible())
        self.assert_on_hud()

    def test_an_end_that_came_while_the_pc_slept_ends_at_the_first_check(self) -> None:
        self.make_pc()
        self.enter()
        self.say("timer 10 minutes")
        self.assertLessEqual(self.desktop._timer_check.interval(), int(pcctl.TIMER_CHECK_S * 1000) + 20)
        self.clock.advance(2 * 3600)                                          # asleep through its end
        self.check_timers()
        self.assertEqual(self.toasts, ["Your 10-minute timer ended at 10:10 AM, sir."])
        self.assertTrue(wait_for(lambda: self.pc.chimes == 1))

    def test_cancel_takes_the_chip_away(self) -> None:
        self.make_pc()
        self.enter()
        self.say("timer 10 minutes")
        self.say("timer 20 minutes")
        self.assertEqual(self.timer_chip(), "2 timers \u00b7 next ends 10:10 AM")
        self.assertEqual(self.say("cancel the timers"), "2 timers cancelled, sir.")
        self.assertEqual(self.timer_chip(), "")
        self.clock.advance(3600)
        self.check_timers()
        self.assertEqual((self.toasts, self.pc.chimes), ([], 0))

    def test_restored_at_start_only_after_the_turn_on(self) -> None:
        def saved(config: Any) -> None:
            folder = config.data_dir / "desktop"
            folder.mkdir(parents=True, exist_ok=True)
            entry = {"id": "0123abcd", "minutes": 30, "label": "", "set_at": "2030-01-01T09:45:00",
                     "ends_at": "2030-01-01T10:15:00"}
            (folder / pcctl.TIMERS_FILE).write_text(json.dumps({"version": 1, "timers": [entry]}), encoding="utf-8")

        self.make_pc(prepare=saved)
        self.assertEqual([timer.timer_id for timer in self.desktop.timers.running()], ["0123abcd"])
        self.enter()
        self.assertEqual(self.timer_chip(), "Timer \u00b7 ends 10:15 AM")
        self.clock.advance(15 * 60)
        self.check_timers()
        self.assertEqual(self.toasts, ["Your 30-minute timer is up, sir."])

    def test_nothing_is_restored_before_the_turn_on(self) -> None:
        def saved(config: Any) -> None:
            folder = config.data_dir / "desktop"
            folder.mkdir(parents=True, exist_ok=True)
            entry = {"id": "0123abcd", "minutes": 30, "label": "", "set_at": "2030-01-01T09:45:00",
                     "ends_at": "2030-01-01T10:15:00"}
            (folder / pcctl.TIMERS_FILE).write_text(json.dumps({"version": 1, "timers": [entry]}), encoding="utf-8")

        self.make_pc(prepare=saved, desktop_consent=False)
        self.assertEqual(self.desktop.timers.running(), ())
        self.assertFalse(self.desktop._timer_check.isActive())


class ConsentTests(PcApp):
    def test_nothing_before_the_turn_on(self) -> None:
        self.make_pc(desktop_consent=False)
        self.assertFalse(self.desktop.request("volume 30"))
        self.assertFalse(self.desktop.request("screenshot"))
        settle()
        self.assertEqual(self.pc.calls, [])

    def test_a_turn_off_stops_a_control_right_before_its_os_call_and_drops_the_timers(self) -> None:
        self.make_pc()
        self.enter()
        self.say("timer 10 minutes")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        reading = self.pc.audio

        def held() -> Any:
            entered.set()
            release.wait(5)
            return reading()

        self.pc.audio = held                                                   # type: ignore[method-assign]
        self.type_on_hud("volume 30")
        self.assertTrue(wait_for(entered.is_set), "the control is on its way")
        self.desktop.turn_off()
        release.set()
        self.answered()
        self.assertNotIn("set_level", self.pc.methods())                      # stopped right before Windows
        self.assertEqual(self.pc.level, 50)
        self.assertEqual(self.desktop.timers.running(), ())
        self.clock.advance(3600)
        self.check_timers()
        self.assertEqual((self.toasts, self.pc.chimes), ([], 0))


class CanaryTests(PcApp):
    def test_a_label_and_a_shots_path_never_reach_the_logs_or_live(self) -> None:
        self.make_pc()
        self.enter()
        timers = self.c.config.data_dir / "desktop" / pcctl.TIMERS_FILE
        with self.assertLogs("briefing_reader", "INFO") as logs:
            self.say(f"remind me in 5 minutes to call {CANARY}")
            self.assertIn(CANARY, timers.read_text(encoding="utf-8"))       # kept locally: a restart, Undo
            self.say("screenshot")
            self.clock.advance(5 * 60)
            self.check_timers()
        for line in logs.output:
            self.assertNotIn(CANARY, line)
            self.assertNotIn("Screenshot 20", line)
            self.assertNotIn("Owner", line)
        self.assertNotIn(CANARY, timers.read_text(encoding="utf-8"))        # it ended: gone from the file too
        for item in self.step_items():
            self.assertNotIn(CANARY, item)
            self.assertNotIn("Screenshots", item)


if __name__ == "__main__":
    unittest.main()
