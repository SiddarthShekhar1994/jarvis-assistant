"""Jarvis on your desktop, P2.5, in the app: the HUD's instant chips (one per kind of change made at once, each with
its Undo, and Undo all - newest first, partial results said plainly), focus mode (Jarvis's offers, toasts and voice
held and let go when it ends - at its end, by the focus chip's Undo or by a Turn off -, the music paused and the
volume turned down and put back on the owner's click only, the owner's own timers still ending, asked again it starts
again, a restart of Jarvis stays quiet) and the "Do Not Disturb - Turn on" chip (Windows' notification settings
opened through the fake seam on his click; "On" only after a read-back says so). The chips are NoFocus and click-only;
they go with the HUD and come back with it.

Qt runs offscreen in this process and shows nothing. No real OS call: the HUD's native seam is a recording fake, the
desktop's backend a fake over a temporary folder, the PC's controls tests.desktop_fakes.FakePc (no volume, no media
key, no screen, no settings page, no sound), the timers' and focus's folder the harness's temporary data folder and
their clock a fake one; claude.exe is never started.
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import desktop_ui, hud, live, ui
from briefing_reader.desktop import fileops as dfo
from briefing_reader.desktop import pcctl
from briefing_reader.speech import KIND_ACK, KIND_NOTICE
from tests.test_desktop_pcctl_app import CANARY, PcApp
from tests.ui_fakes import settle, wait_for

DOT = " \u00b7 "
HELD_TOAST = ("Your AM briefing is ready, sir.", "View", ui.TOAST_VIEW)
HELD_SPEECH = (KIND_NOTICE, "Your check-in starts in 15 minutes, sir.", "offer:7")
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_desktop_focus_app", "-platform", "offscreen"])


class FocusApp(PcApp):
    """PcApp with the instant row's clicks, Jarvis's words as answered (and their tone) and the volume's 300 ms cap
    waited out between two volume changes."""

    def make_pc(self, **harness: Any) -> None:
        super().make_pc(**harness)
        self.said: list[tuple[str, str]] = []
        self.desktop.answered.connect(lambda said, tone, _words, _taken: self.said.append((said, tone)))

    def row(self) -> list[str]:
        return self.hud().instant_texts()

    @staticmethod
    def cap() -> None:
        QTest.qWait(int(pcctl.VOLUME_CAP_S * 1000) + 60)

    def click(self, widget: QWidget) -> None:
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        settle()

    def undo(self, key: str) -> str:
        self.cap()
        self.click(self.hud().instant[key].undo)
        self.answered()
        return self.hud().caption_text()

    def undo_all(self) -> str:
        self.cap()
        self.click(self.hud().undo_all)
        self.answered()
        return self.hud().caption_text()

    def focus_on(self, text: str = "focus for 30 minutes") -> str:
        self.cap()
        said = self.say(text)
        self.assertTrue(self.desktop.in_focus())
        return said

    def hold_things(self) -> None:
        """What Jarvis would bring up by himself while focus runs: a toast and a sentence."""
        self.c._post_toast(*HELD_TOAST)
        self.c._say(HELD_SPEECH[0], HELD_SPEECH[1], key=HELD_SPEECH[2])
        self.c._say(KIND_ACK, "One moment, sir.")
        settle()

    def toast_line(self) -> str:
        toast = self.c._toast_window
        return toast.line() if toast is not None and toast.isVisible() else ""

    def tasks_titled(self, start: str) -> list[Any]:
        return [task for task in self.desktop_tasks() if task.title.startswith(start)]


class InstantChipTests(FocusApp):
    def test_each_change_gets_its_chip_and_its_own_undo(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.media = pcctl.PLAYING
        self.assertEqual(self.row(), [])                                       # none until a change
        self.assertEqual(self.say("volume 30"), "Volume 30, sir.")
        self.assertEqual(self.row(), [f"Volume{DOT}30%", "Undo all"])
        self.say("pause")
        self.say("timer 10 minutes")
        self.say("screenshot")
        self.assertEqual(self.row(), [f"Music{DOT}paused", f"Volume{DOT}30%", f"Timer set{DOT}10 min",
                                      f"Screenshot{DOT}saved", "Undo all"])
        self.assertEqual([cut for cut in self.hud().cut_texts() if cut.startswith("instant")], [])
        self.assertEqual(self.undo("volume"), "Back as it was, sir: volume 50%.")
        self.assertEqual((self.pc.level, self.pc.muted), (50, False))
        self.assertEqual(self.undo("music"), "Back as it was, sir: music playing.")
        self.assertEqual(self.pc.media, pcctl.PLAYING)
        self.assertEqual(self.undo("timers"), "Back as it was, sir: timer cancelled.")
        self.assertEqual((self.desktop.timers.running(), self.timer_chip()), ((), ""))
        self.assertEqual(self.undo("shots"), "Nothing was put back, sir. The screenshot stays - the Recycle Bin "
                                             "can't be reached right now.")       # no bin while the tests run
        self.assertEqual(self.row(), [f"Screenshot{DOT}saved", "Undo all"])   # it stays: still there, said so
        self.assertEqual([tone for _said, tone in self.said[-4:]], [hud.TONE_DONE] * 3 + [hud.TONE_ERROR])
        task = self.tasks_titled("Undo on the volume chip")[-1]
        items = [item.text for step in task.steps if step.kind == live.DESKTOP_UNDO for item in step.items]
        self.assertEqual(items, ["volume - put back"])
        self.assertEqual(self.runs(), [])                                      # no planner run: nothing left the PC
        self.assert_on_hud()

    def test_undo_all_puts_back_newest_first_and_says_what_cant_be(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.media = pcctl.PLAYING
        self.say("volume 30")
        self.cap()
        self.say("louder")
        self.say("pause")
        self.pc.media = None                                                   # winrt missing: the media key
        self.say("next song")
        self.say("screenshot")
        self.pc.media = pcctl.PAUSED                                           # the player as "pause" left it
        self.pc.calls.clear()
        said = self.undo_all()
        self.assertEqual(said, "Back as it was, sir: music playing, volume 50%. The screenshot stays - the Recycle "
                               "Bin can't be reached right now. What the media key did can't be put back.")
        self.assertEqual(self.pc.methods(), ["audio", "set_level", "media_state", "media_control"])   # ONE each
        self.assertEqual((self.pc.level, self.pc.media), (50, pcctl.PLAYING))
        self.assertEqual(self.row(), [f"Screenshot{DOT}saved", "Undo all"])
        task = self.tasks_titled(desktop_ui.UNDO_ALL_BUTTON)[-1]
        items = [item.text for step in task.steps if step.kind == live.DESKTOP_UNDO for item in step.items]
        self.assertEqual(items, [f"screenshot - left: {dfo.R_NO_BIN}", "media - left: cant_undo",
                                 "media - put back", "volume - put back", "volume - put back"])   # newest first

    def test_one_changed_since_is_left_as_it_is(self) -> None:
        self.make_pc()
        self.enter()
        self.say("volume 30")
        self.pc.level = 70                                                     # the owner's own volume key since
        self.assertEqual(self.undo("volume"), "Nothing was put back, sir. The volume changed since - left as it is.")
        self.assertEqual(self.pc.level, 70)
        self.assertEqual(self.row(), [])                                       # settled: its chip goes

    def test_the_chips_go_with_the_hud_and_come_back_with_it_and_never_take_the_focus(self) -> None:
        self.make_pc()
        self.enter()
        self.say("volume 30")
        self.say("timer 5 minutes")
        hud_win = self.hud()
        for widget in (*hud_win.instant.values(), hud_win.dnd_chip, hud_win.undo_all,
                       *(chip.undo for chip in hud_win.instant.values())):
            self.assertEqual(widget.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.native.keys.clear()
        focused = QApplication.focusWidget()
        self.undo("timers")
        self.assertEqual(self.native.keys, [])                                 # nothing activated
        self.assertIs(QApplication.focusWidget(), focused)
        self.assertFalse(any(window.isActiveWindow() for window in self.desktop.overlay.windows()
                             if window is not hud_win))
        self.back()
        self.assertEqual(hud_win.instant_texts(), [])                         # no hidden chip left behind
        self.assertFalse(self.desktop._dnd_timer.isActive())
        self.enter()
        self.assertEqual(self.row(), [f"Volume{DOT}30%", "Undo all"])          # back with the HUD


class FocusModeAppTests(FocusApp):
    def test_focus_holds_jarvis_and_its_undo_ends_it_early_and_puts_back(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.level, self.pc.media = 60, pcctl.PLAYING
        self.assertEqual(self.focus_on(), "Focus mode is on, sir. I've paused the music and turned the volume "
                                          "down to 20. I'll tell you when the 30 minutes are up.")
        self.assertEqual(self.row(), [f"Focus{DOT}ends 10:30 AM", f"Do Not Disturb{DOT}Turn on", f"Music{DOT}paused",
                                      f"Volume{DOT}20%", "Undo all"])
        said_before = list(self.harness.voice.said)
        self.hold_things()
        self.assertEqual(self.toast_line(), "")                               # held, not shown
        self.assertEqual(self.harness.voice.said, said_before)                 # held, not said
        self.assertEqual(self.c._prod_activity(), "off")                      # the offers wait
        self.assertEqual(self.undo("focus"), "Focus ended, sir. Back as it was: music playing, volume 60%.")
        self.assertFalse(self.desktop.in_focus())
        self.assertEqual((self.pc.level, self.pc.media), (60, pcctl.PLAYING))
        self.assertEqual(self.row(), [])
        self.assertEqual(self.toast_line(), HELD_TOAST[0])                     # back now
        self.assertEqual(self.harness.voice.said[len(said_before):], [HELD_SPEECH])    # the ack was dropped
        self.assertEqual(self.c._prod_activity(), "minimized")                # the offers' toast path again
        self.assertFalse(self.desktop._timer_check.isActive())

    def test_its_end_says_so_leaves_the_pc_as_it_is_and_lets_go(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.level, self.pc.media = 60, pcctl.PLAYING
        self.focus_on("focus for 1 minute")
        self.hold_things()
        self.pc.calls.clear()
        self.clock.advance(61)
        with mock.patch.object(ui, "FOCUS_TOAST_AFTER_MS", 50):
            self.check_timers()
            self.assertEqual(self.toasts, ["Your 1-minute focus is up, sir."])
            self.assertEqual(self.toast_line(), "Your 1-minute focus is up, sir.")
            self.assertTrue(wait_for(lambda: self.toast_line() == HELD_TOAST[0]), "the held toast comes after it")
        self.assertTrue(wait_for(lambda: self.pc.chimes == 1))
        self.assertFalse(self.desktop.in_focus())
        self.assertEqual(self.pc.methods(), ["chime"])                        # nothing else on the PC: hard rule 1
        self.assertEqual((self.pc.level, self.pc.media), (20, pcctl.PAUSED))
        self.assertEqual(self.row(), [f"Music{DOT}paused", f"Volume{DOT}20%", "Undo all"])   # his click puts back
        self.assertIn(HELD_SPEECH, self.harness.voice.said)
        self.assertEqual(self.undo_all(), "Back as it was, sir: music playing, volume 60%.")

    def test_his_own_timers_still_end_during_focus(self) -> None:
        self.make_pc()
        self.enter()
        self.focus_on()
        self.say("timer 1 minute")
        self.clock.advance(61)
        self.check_timers()
        self.assertEqual(self.toasts, ["Your 1-minute timer is up, sir."])
        self.assertEqual(self.toast_line(), "Your 1-minute timer is up, sir.")    # a reminder he set: not held
        self.assertTrue(wait_for(lambda: self.pc.chimes == 1))
        self.assertTrue(self.desktop.in_focus())

    def test_asked_again_it_starts_again_with_one_chip_and_its_undo_goes_back_to_before_the_first(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.level, self.pc.media = 60, pcctl.PLAYING
        self.focus_on()
        self.clock.advance(10 * 60)
        self.assertEqual(self.focus_on("focus mode for 1 hour"),
                         "Focus mode starts again, sir. I'll tell you when the 60 minutes are up.")
        self.assertEqual(self.row()[:2], [f"Focus{DOT}ends 11:10 AM", f"Do Not Disturb{DOT}Turn on"])
        self.assertEqual(sum(text.startswith("Focus") for text in self.row()), 1)
        self.clock.advance(31 * 60)
        self.check_timers()                                                   # the first end has gone by
        self.assertTrue(self.desktop.in_focus())
        self.assertEqual(self.undo("focus"), "Focus ended, sir. Back as it was: music playing, volume 60%.")
        self.assertEqual((self.pc.level, self.pc.media), (60, pcctl.PLAYING))

    def test_a_restart_of_jarvis_during_focus_stays_quiet_until_its_end(self) -> None:
        def saved(config: Any) -> None:
            folder = config.data_dir / "desktop"
            folder.mkdir(parents=True, exist_ok=True)
            entry = {"id": "0123abcd", "minutes": 30, "set_at": "2030-01-01T09:55:00", "ends_at": "2030-01-01T10:25:00"}
            (folder / pcctl.FOCUS_FILE).write_text(json.dumps({"version": 1, "focus": entry}), encoding="utf-8")

        self.make_pc(prepare=saved)
        self.assertTrue(self.desktop.in_focus())
        self.hold_things()
        self.assertEqual(self.toast_line(), "")
        self.enter()
        self.assertEqual(self.row(), [f"Focus{DOT}ends 10:25 AM", f"Do Not Disturb{DOT}Turn on", "Undo all"])
        self.assertEqual(self.undo("focus"), "Focus ended, sir.")
        self.assertEqual(self.toast_line(), HELD_TOAST[0])
        self.assertEqual([method for method in self.pc.methods() if method != "dnd_state"], [])   # read only

    def test_a_turn_off_ends_focus_lets_go_and_drops_the_chips(self) -> None:
        self.make_pc()
        self.enter()
        self.pc.level, self.pc.media = 60, pcctl.PLAYING
        self.focus_on()
        self.hold_things()
        self.pc.calls.clear()
        self.desktop.turn_off()
        settle()
        self.assertFalse(self.desktop.in_focus())
        self.assertEqual(self.toast_line(), HELD_TOAST[0])
        self.assertIn(HELD_SPEECH, self.harness.voice.said)
        self.assertEqual((self.desktop.instant_changes(), self.row()), ((), []))
        self.assertEqual(self.pc.calls, [])                                   # the PC is left as it is
        self.assertEqual((self.pc.level, self.pc.media), (20, pcctl.PAUSED))
        self.desktop._on_undo_all()                                          # nothing left, and not turned on
        settle()
        self.assertEqual(self.pc.calls, [])


class DoNotDisturbAppTests(FocusApp):
    def test_the_chip_opens_windows_settings_and_says_on_only_after_a_read_back(self) -> None:
        self.make_pc()
        self.enter()
        self.focus_on()
        chip = self.hud().dnd_chip
        self.assertEqual(chip.text(), f"Do Not Disturb{DOT}Turn on")
        self.native.keys.clear()
        self.click(chip)
        self.assertTrue(wait_for(lambda: self.pc.settings_opened == 1), "opened through the fake seam")
        settle()
        self.assertEqual(self.hud().caption_text(), "Windows' notification settings are open, sir - Do Not Disturb "
                                                    "is your switch there.")
        self.assertEqual(self.native.keys, [])
        self.assertTrue(self.desktop._dnd_timer.isActive())                  # read back 2, 6 and 15 s after
        self.assertEqual(len(self.tasks_titled(desktop_ui.DND_TASK_TITLE)), 1)
        for read, shown in (("unknown", "Turn on"), ("off", "Turn on"), ("on", "On"), ("off", "Turn on")):
            with self.subTest(read=read):
                self.pc.dnd = read
                reads = self.pc.methods().count("dnd_state")
                self.desktop._on_dnd_timer()
                self.assertTrue(wait_for(lambda reads=reads: self.pc.methods().count("dnd_state") > reads))
                settle()
                self.assertEqual(chip.text(), f"Do Not Disturb{DOT}{shown}")
        self.pc.dnd = "on"
        self.desktop._read_dnd()
        self.assertTrue(wait_for(lambda: chip.text().endswith("On")))
        self.undo_all()                                                       # Undo all leaves it alone
        self.assertEqual((self.pc.settings_opened, self.pc.methods().count("open_notifications")), (1, 1))
        self.assertEqual(self.row(), [])

    def test_without_a_read_back_it_never_says_on(self) -> None:
        self.make_ask()                                        # the real seam: it refuses to be made in a test
        self.enter()
        self.assertEqual(self.say("focus for 30 minutes"), "Focus mode is on, sir. I'll tell you when the 30 minutes "
                                                           "are up.")
        chip = self.hud().dnd_chip
        QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for(lambda: self.hud().caption_text() == "Windows couldn't open its notification "
                                                                      "settings, sir."))
        self.desktop._read_dnd()
        QTest.qWait(150)
        settle()
        self.assertEqual(chip.text(), f"Do Not Disturb{DOT}Turn on")


class FocusCanaryTests(FocusApp):
    def test_a_label_never_reaches_the_chips_the_logs_or_live(self) -> None:
        self.make_pc()
        self.enter()
        with self.assertLogs("briefing_reader", "INFO") as logs:
            self.say(f"remind me in 5 minutes to call {CANARY}")
            self.focus_on()
            self.undo_all()
        for line in logs.output:
            self.assertNotIn(CANARY, line)
        self.assertNotIn(CANARY, " ".join(self.row()))
        for task in self.desktop_tasks():
            for step in task.steps:
                for item in step.items:
                    self.assertNotIn(CANARY, item.text)


if __name__ == "__main__":
    unittest.main()
