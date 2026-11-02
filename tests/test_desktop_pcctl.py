"""Jarvis on your desktop, P2.4: the PC's controls (desktop.pcctl; SPEC 2.1, 2.2, 6.1, 14, 15 and 16) with fakes only.

Volume and mute (the level and mute found kept for Undo all, at most one change per 300 ms), music through the
player's controls (the state known: undoable) and without winrt (the media keys, honestly "can't be put back"),
Jarvis's timers (set, cancel, bounds, restored after a restart and after a resume, the end's words), screenshots
(the folder from config - never OneDrive -, the HUD's windows left out, named and saved on a pretend disk, never
sent; Undo through the Recycler seam: not yet), the executor's PC step (its Live line and Jarvis's words), the real
seam's guard (every public method raises under the test guard, before any OS entry point) and that importing pcctl
needs no winrt. Nothing reaches the real PC: tests.desktop_fakes.FakePc and FakeDesktopBackend over a temporary
folder; timers live in temporary folders.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from briefing_reader.desktop import backend as dbk
from briefing_reader.desktop import executor as dx
from briefing_reader.desktop import fileops as dfo
from briefing_reader.desktop import paths, pcctl
from briefing_reader.desktop import routes as dr
from briefing_reader.desktop import validate as dv
from tests.desktop_fakes import OWNER, FakeClock, FakePc, FakeRecycleBin, desktop_disk

PROJECT = Path(__file__).resolve().parents[1]
SHOTS = OWNER + "\\Jarvis\\Screenshots"
FIRST_SHOT = SHOTS + "\\Screenshot 2026-10-20 10.21.00.png"


class PcBase(unittest.TestCase):
    """A PcControl over FakePc, a pretend disk (tests.desktop_fakes.desktop_disk) and a timer book in a temporary
    folder, on a fake clock (2026-10-20 10:21)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.disk = desktop_disk(tmp.name)
        self.fake = FakePc()
        self.clock = FakeClock()
        self.folder = self.tmp / "data" / "desktop"
        self.book = pcctl.TimerBook(self.folder, now=self.clock.now)
        self.pc = self.control()

    def control(self, **kwargs: Any) -> pcctl.PcControl:
        return pcctl.PcControl(lambda: self.fake, disk=lambda: self.disk, timers=self.book,
                               clock=self.clock.monotonic, now=self.clock.now, **kwargs)

    def ask(self, text: str, **kwargs: Any) -> pcctl.PcChange:
        asked = dr.pc_request(text)
        self.assertIsNotNone(asked, text)
        return self.pc.carry_out(asked, **kwargs)

    def later(self, seconds: float = 1.0) -> None:
        self.clock.advance(seconds)

    @staticmethod
    def words(change: pcctl.PcChange) -> str:
        return pcctl.change_words(change)


# --------------------------------------------------------------------------
# Volume and mute
# --------------------------------------------------------------------------

class VolumeTests(PcBase):
    def test_a_change_keeps_the_level_and_mute_it_found(self) -> None:
        self.fake.level, self.fake.muted = 60, True
        change = self.ask("volume 30")
        self.assertEqual((change.kind, change.action, change.code, change.changed, change.undoable),
                         (dv.VOLUME, "set", "", True, True))
        self.assertEqual((change.before, change.after), (pcctl.AudioState(60, True), pcctl.AudioState(30, False)))
        self.assertEqual((self.fake.level, self.fake.muted), (30, False))     # the sound back on, as Windows' keys do
        self.assertEqual(self.words(change), "Volume 30, sir.")
        self.assertEqual(pcctl.live_note(change), "level 60 -> 30, unmuted")
        self.assertEqual(self.pc.changes(), (change,))                       # kept for Undo all (P2.5)

    def test_up_down_mute_and_unmute(self) -> None:
        cases = (("louder", 50, False, (60, False), "Volume 60, sir."),
                 ("volume down", 60, False, (50, False), "Volume 50, sir."),
                 ("turn it up", 95, False, (100, False), "Volume 100, sir."),
                 ("quieter", 4, False, (0, False), "Volume 0, sir."),
                 ("volume up", 40, True, (50, False), "Volume 50, sir."),
                 ("mute", 40, False, (40, True), "Muted, sir."),
                 ("unmute", 40, True, (40, False), "Unmuted, sir."))
        for text, level, muted, after, said in cases:
            with self.subTest(text=text):
                self.later()
                self.fake.level, self.fake.muted = level, muted
                change = self.ask(text)
                self.assertEqual((change.code, change.before, change.after),
                                 ("", pcctl.AudioState(level, muted), pcctl.AudioState(*after)))
                self.assertEqual((self.fake.level, self.fake.muted), after)
                self.assertEqual(self.words(change), said)

    def test_what_is_so_already_changes_nothing(self) -> None:
        cases = (("volume 30", 30, False, "The volume is already 30, sir."),
                 ("louder", 100, False, "The volume is already at 100, sir."),
                 ("quieter", 0, False, "The volume is already at 0, sir."),
                 ("mute", 30, True, "It's already muted, sir."),
                 ("unmute", 30, False, "The sound is already on, sir."))
        for text, level, muted, said in cases:
            with self.subTest(text=text):
                self.fake.level, self.fake.muted = level, muted
                change = self.ask(text)
                self.assertEqual((change.code, change.changed, change.undoable), ("", False, False))
                self.assertEqual(self.words(change), said)
        self.assertEqual(self.fake.methods(), ["audio"] * 5)                  # nothing was set
        self.assertEqual(self.pc.changes(), ())
        self.assertEqual(self.ask("volume 70").code, "")                      # ... and the cap was not used

    def test_at_most_one_change_per_300_ms(self) -> None:
        self.assertEqual(self.ask("volume 30").code, "")
        self.later(0.29)
        change = self.ask("volume 40")
        self.assertEqual(change.code, pcctl.TOO_SOON)
        self.assertEqual(self.words(change),
                         "That was too soon after the last volume change, sir - nothing was changed.")
        self.assertEqual(self.fake.level, 30)
        self.assertEqual(self.fake.methods().count("audio"), 1)               # refused before Windows was asked
        self.later(0.02)
        self.assertEqual(self.ask("volume 40").code, "")
        self.assertEqual(self.fake.level, 40)
        self.assertEqual(pcctl.VOLUME_CAP_S, 0.3)

    def test_what_cannot_be_done(self) -> None:
        self.assertEqual(self.words(self.ask("volume 150")),
                         "The volume goes from 0 to 100, sir - nothing was changed.")
        self.fake.device = False
        change = self.ask("volume 30")
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.NO_DEVICE, "I can't find your speakers, sir - nothing was changed."))
        self.fake.device = True
        self.fake.errors["set_level"] = OSError(0, "denied", None, 5)
        change = self.ask("volume 30")
        self.assertEqual((change.code, change.winerror, self.words(change)),
                         (pcctl.OS_ERROR, 5, "Windows didn't change the volume, sir."))
        stop = threading.Event()
        stop.set()
        self.later()
        self.fake.errors.clear()
        self.assertEqual(self.ask("volume 20", cancel=stop).code, pcctl.STOPPED)
        self.assertEqual(self.fake.level, 50)
        self.assertEqual(self.fake.methods().count("set_level"), 1)          # only the one Windows refused
        self.assertEqual(self.pc.changes(), ())

    def test_no_controls_no_change(self) -> None:
        def refused() -> Any:
            raise dbk.RealBackendInTests("the real one")

        pc = pcctl.PcControl(refused, disk=lambda: self.disk, timers=self.book)
        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING") as logs:
            change = pc.volume("set", level=30)
        self.assertEqual(change.code, pcctl.UNAVAILABLE)
        self.assertEqual(self.words(change), "I can't reach this PC's controls right now, sir - nothing was changed.")
        self.assertIn("RealBackendInTests", "\n".join(logs.output))

    def test_undo_puts_the_level_and_mute_back_unless_changed_since(self) -> None:
        self.fake.level, self.fake.muted = 60, True
        change = self.ask("volume 30")
        back = self.pc.undo(change)
        self.assertEqual(back.code, pcctl.TOO_SOON)                           # the cap holds for an Undo too
        self.later()
        back = self.pc.undo(change)
        self.assertEqual((back.code, self.fake.level, self.fake.muted), ("", 60, True))
        self.assertEqual(pcctl.undo_words(back), "Put back, sir.")
        self.assertEqual(self.pc.changes(), (change,))                       # an Undo is not a change to put back
        self.later()
        self.ask("volume 30")
        self.later()
        self.fake.level = 45                                                 # the owner moved it since
        later = self.pc.changes()[-1]
        self.assertEqual(self.pc.undo(later).code, pcctl.CHANGED)
        self.assertEqual(self.fake.level, 45)


# --------------------------------------------------------------------------
# Music: the player's controls (winrt), else the media keys
# --------------------------------------------------------------------------

class MediaTests(PcBase):
    def test_with_the_players_controls_the_state_is_known(self) -> None:
        self.fake.media = pcctl.PLAYING
        change = self.ask("pause the music")
        self.assertEqual((change.code, change.changed, change.undoable, change.via, change.before, change.after),
                         ("", True, True, pcctl.VIA_PLAYER, pcctl.PLAYING, pcctl.PAUSED))
        self.assertEqual(self.words(change), "Paused, sir.")
        self.assertEqual(pcctl.live_note(change), "the player's controls: playing -> paused")
        self.assertEqual(self.fake.keys, [])
        already = self.ask("pause")
        self.assertEqual((already.changed, self.words(already)), (False, "It's already paused, sir."))
        for text, said, undoable in (("resume", "Playing, sir.", True), ("play", "It's already playing, sir.", False),
                                     ("next song", "Next track, sir.", False),
                                     ("previous", "Previous track, sir.", False)):
            with self.subTest(text=text):
                change = self.ask(text)
                self.assertEqual((self.words(change), change.undoable), (said, undoable))
        self.assertEqual([value for method, value in self.fake.calls if method == "media_control"],
                         ["pause", "play", "next", "previous"])
        self.assertEqual(self.fake.keys, [])

    def test_nothing_playing_or_not_taken(self) -> None:
        self.fake.media = pcctl.NO_SESSION
        change = self.ask("pause")
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.NO_PLAYER, "Nothing is playing, sir - nothing was changed."))
        self.fake.media, self.fake.takes = pcctl.PAUSED, False
        change = self.ask("play")
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.NOT_TAKEN, "The player didn't respond, sir - nothing was changed."))
        self.assertEqual(self.pc.changes(), ())

    def test_without_winrt_the_media_key_says_it_cant_be_put_back(self) -> None:
        self.fake.media = None                            # winrt missing: the player's state can't be asked
        either = "Pressed play/pause, sir - I can't tell what was playing, so it can't be put back."
        cases = (("pause", "pause", either), ("play", "play", either),
                 ("skip", "next", "Pressed next track, sir - it can't be put back."),
                 ("previous track", "previous", "Pressed previous track, sir - it can't be put back."))
        for text, key, said in cases:
            with self.subTest(text=text):
                change = self.ask(text)
                self.assertEqual((change.code, change.changed, change.undoable, change.via),
                                 ("", True, False, pcctl.VIA_KEYS))
                self.assertEqual(self.fake.keys[-1], key)
                self.assertEqual(self.words(change), said)
                self.assertEqual(pcctl.live_note(change), "a media key: the player's state unknown - can't be put back")
                back = self.pc.undo(change)
                self.assertEqual(back.code, pcctl.CANT_UNDO)
                self.assertEqual(pcctl.undo_words(back), "Not put back, sir - it can't be put back.")
        self.assertNotIn("media_control", self.fake.methods())
        self.assertEqual(pcctl.MEDIA_KEYS, {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1})

    def test_the_players_controls_failing_falls_back_to_the_key(self) -> None:
        self.fake.errors["media_state"] = TimeoutError("the player did not answer")
        change = self.ask("pause")
        self.assertEqual((change.via, self.fake.keys), (pcctl.VIA_KEYS, ["pause"]))

    def test_undo_puts_the_player_back_unless_changed_since(self) -> None:
        self.fake.media = pcctl.PLAYING
        change = self.ask("pause")
        back = self.pc.undo(change)
        self.assertEqual((back.code, self.fake.media), ("", pcctl.PLAYING))
        change = self.ask("pause")
        self.fake.media = pcctl.PLAYING                   # the owner pressed play himself meanwhile
        self.assertEqual(self.pc.undo(change).code, pcctl.CHANGED)


class WinrtImportTests(unittest.TestCase):
    """winrt is optional (SPEC 14): importing pcctl never needs it, and the player's controls are found only when it
    is there - else None (the media keys then)."""

    WINRT = ("winrt", "winrt.windows", "winrt.windows.media", "winrt.windows.media.control")

    def test_without_winrt_there_are_no_players_controls(self) -> None:
        with mock.patch.dict(sys.modules, dict.fromkeys(self.WINRT)):          # None: a missing import
            self.assertIsNone(pcctl.gsmtc())

    def test_with_winrt_the_players_controls_are_found(self) -> None:
        stand_in = types.ModuleType("winrt.windows.media.control")
        with mock.patch.dict(sys.modules, {"winrt.windows.media.control": stand_in}):
            self.assertIs(pcctl.gsmtc(), stand_in)

    def test_importing_pcctl_needs_no_winrt_nor_comtypes(self) -> None:
        code = ("import sys\n"
                f"for name in {self.WINRT!r}:\n"
                "    sys.modules[name] = None\n"
                "import briefing_reader.desktop.pcctl as pcctl\n"
                "first = 'comtypes' in sys.modules\n"
                "import briefing_reader.desktop.routes, briefing_reader.desktop.service, briefing_reader.desktop_ui\n"
                "print(first, pcctl.gsmtc() is None, any(sys.modules.get(name) for name in list(sys.modules)"
                " if name.startswith('winrt')))\n")
        env = dict(os.environ, JARVIS_DESKTOP_TEST_GUARD="1", QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT, capture_output=True, text=True,
                                timeout=120, env=env)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "False True False"), result.stderr[-800:])


# --------------------------------------------------------------------------
# Jarvis's timers
# --------------------------------------------------------------------------

class TimerTests(PcBase):
    def saved(self) -> list[dict[str, Any]]:
        return json.loads((self.folder / pcctl.TIMERS_FILE).read_text(encoding="utf-8"))["timers"]

    def test_a_timer_is_set_and_saved(self) -> None:
        change = self.ask("remind me in 20 minutes to stretch")
        timer = change.after
        self.assertEqual((change.code, change.changed, change.undoable), ("", True, True))
        self.assertEqual((timer.minutes, timer.label, timer.ends_at),
                         (20, "stretch", self.clock.now() + timedelta(minutes=20)))
        self.assertEqual(self.words(change), "Timer set for 20 minutes, sir.")        # the label never said
        self.assertEqual([(entry["id"], entry["minutes"], entry["label"]) for entry in self.saved()],
                         [(timer.timer_id, 20, "stretch")])
        self.assertEqual(pcctl.chip_text(self.book.running()), "Timer \u00b7 ends 10:41 AM")
        self.assertEqual(pcctl.chip_text(self.book.running(), hour24=True), "Timer \u00b7 ends 10:41")
        self.assertEqual(self.words(self.ask("timer 1 minute")), "Timer set for 1 minute, sir.")
        self.assertEqual(pcctl.chip_text(self.book.running()), "2 timers \u00b7 next ends 10:22 AM")
        self.assertEqual(pcctl.chip_text(()), "")
        self.assertEqual(self.book.seconds_left(), 60.0)

    def test_the_bounds(self) -> None:
        for text in ("timer 0 minutes", "timer 241 minutes", "remind me in 999 minutes"):
            with self.subTest(text=text):
                change = self.ask(text)
                self.assertEqual((change.code, self.words(change)),
                                 (dv.SHAPE, "Timers run from 1 to 240 minutes, sir - nothing was set."))
        for minutes in (1, 240):
            self.assertEqual(self.pc.timer(minutes).code, "")
        long = self.pc.timer(5, "to " + "x" * 60)
        self.assertEqual(len(long.after.label), pcctl.LABEL_MAX)
        self.assertEqual(self.pc.timer(5, "call \u202eAna").after.label, "call Ana")      # hidden characters dropped
        self.assertEqual(self.pc.timer(5).code, "")                                       # the fifth
        change = self.pc.timer(5)
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.TIMERS_FULL, "5 timers are running already, sir - nothing was set."))
        self.assertEqual(len(self.book.running()), pcctl.MAX_TIMERS)

    def test_cancelling(self) -> None:
        change = self.ask("cancel the timer")
        self.assertEqual((change.code, self.words(change)), (pcctl.NO_TIMER, "There's no timer running, sir."))
        self.ask("timer 10 minutes")
        self.assertEqual(self.words(self.ask("stop the timer")), "Timer cancelled, sir.")
        self.ask("timer 10 minutes")
        self.ask("timer 20 minutes")
        change = self.ask("cancel my timers")
        self.assertEqual((self.words(change), pcctl.live_note(change)), ("2 timers cancelled, sir.", "2 cancelled"))
        self.assertEqual((self.book.running(), self.saved()), ((), []))

    def test_restored_after_a_restart(self) -> None:
        first = self.ask("timer 10 minutes").after
        second = self.ask("remind me in 30 minutes to call Ana").after
        restarted = pcctl.TimerBook(self.folder, now=self.clock.now)
        self.assertEqual(restarted.load(), 2)
        self.assertEqual([(timer.timer_id, timer.minutes, timer.ends_at, timer.label) for timer in restarted.running()],
                         [(first.timer_id, 10, first.ends_at, ""), (second.timer_id, 30, second.ends_at, "call Ana")])
        (self.folder / pcctl.TIMERS_FILE).write_text("{not json", encoding="utf-8")
        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING") as logs:
            self.assertEqual(restarted.load(), 0)
        self.assertIn("JSONDecodeError", "\n".join(logs.output))

    def test_an_end_its_words_and_after_a_resume(self) -> None:
        self.ask("timer 10 minutes")
        self.later(9 * 60)
        self.assertEqual(self.book.take_due(), ([], 0))
        self.later(60)
        ended, dropped = self.book.take_due()
        self.assertEqual((len(ended), dropped, ended[0][1]), (1, 0, 0.0))
        self.assertEqual(pcctl.end_words(*ended[0]), "Your 10-minute timer is up, sir.")
        self.assertEqual(self.book.running(), ())
        labelled = self.ask("remind me in 5 minutes to stretch").after
        self.assertEqual(pcctl.end_words(labelled, 0.0, "boss"), "Your 5-minute timer is up, boss - stretch.")
        self.book.clear()
        self.ask("timer 10 minutes")
        self.later(2 * 3600)                              # the PC slept through its end; the first check after
        ended, _dropped = self.book.take_due()
        self.assertEqual(pcctl.end_words(*ended[0]), "Your 10-minute timer ended at 10:41 AM, sir.")
        self.assertEqual(pcctl.end_words(ended[0][0], ended[0][1], hour24=True),
                         "Your 10-minute timer ended at 10:41, sir.")
        self.ask("timer 10 minutes")
        self.later(13 * 3600)                             # Jarvis closed for long: dropped quietly
        self.assertEqual(self.book.take_due(), ([], 1))

    def test_undo(self) -> None:
        set_one = self.ask("timer 10 minutes")
        self.assertEqual(self.pc.undo(set_one).code, "")
        self.assertEqual(self.book.running(), ())
        self.assertEqual(self.pc.undo(set_one).code, pcctl.GONE)
        self.ask("timer 10 minutes")
        self.ask("timer 20 minutes")
        cancelled = self.ask("cancel the timers")
        self.later(15 * 60)
        back = self.pc.undo(cancelled)
        self.assertEqual([timer.minutes for timer in back.after], [20])  # the 10-minute one would have ended
        self.assertEqual([timer.minutes for timer in self.book.running()], [20])

    def test_a_label_is_never_logged(self) -> None:
        with self.assertLogs("briefing_reader.desktop.pcctl", "INFO") as logs:
            change = self.ask("remind me in 5 minutes to call CANARY9f3")
            kept = self.saved()                           # the data folder keeps it (a restart, Undo)
            self.ask("cancel the timer")
        self.assertEqual(kept[0]["label"], "call CANARY9f3")
        self.assertNotIn("CANARY9f3", "\n".join(logs.output))
        self.assertNotIn("CANARY9f3", self.words(change) + pcctl.live_note(change) + repr(change) + repr(change.after))

    def test_the_real_data_folder_is_never_used_while_the_tests_run(self) -> None:
        from briefing_reader.config import default_data_dir

        with self.assertRaises(dbk.RealBackendInTests):
            pcctl.timers_folder(default_data_dir())
        self.assertEqual(pcctl.timers_folder(self.tmp), self.tmp / "desktop")
        self.assertIsNone(pcctl.timers_folder(None))

    def test_a_folder_that_cant_be_written_keeps_them_in_memory(self) -> None:
        blocked = self.tmp / "blocked"
        blocked.write_text("a file, not a folder", encoding="utf-8")
        book = pcctl.TimerBook(blocked / "desktop", now=self.clock.now)
        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING"):
            book.add(10)
        self.assertEqual((len(book.running()), book.saved), (1, False))


# --------------------------------------------------------------------------
# Screenshots
# --------------------------------------------------------------------------

class ScreenshotTests(PcBase):
    def test_saved_in_the_default_folder_with_the_hud_left_out(self) -> None:
        change = self.pc.screenshot(pcctl.SCREEN, hud=(101, 102))
        self.assertEqual((change.code, change.changed, change.undoable), ("", True, True))
        self.assertEqual(self.fake.captures, [(pcctl.SCREEN, (101, 102))])   # the HUD's windows handed over
        shot = change.after
        self.assertEqual(shot.path, FIRST_SHOT)                              # %USERPROFILE%\Jarvis\Screenshots (D8)
        data = Path(self.disk.real(shot.path)).read_bytes()
        self.assertTrue(data.startswith(dbk.PNG_SIGNATURE))
        self.assertEqual((shot.sha256, shot.size), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(self.disk.asked("save_png"), [FIRST_SHOT])
        self.assertEqual(self.words(change), "Screenshot saved to your screenshots folder, sir.")
        self.assertEqual(pcctl.live_note(change), "the screen, 1 file saved on this PC")
        again = self.ask("take a screenshot of this window")
        self.assertEqual(again.after.path, SHOTS + "\\Screenshot 2026-10-20 10.21.00 (2).png")   # never over one
        self.assertEqual(self.words(again), "Screenshot of the window saved to your screenshots folder, sir.")
        self.assertEqual(self.fake.captures[-1], (pcctl.ACTIVE_WINDOW, ()))

    def test_the_folder_from_config_but_never_onedrive_nor_another_drive(self) -> None:
        mine = OWNER + "\\Shots here"
        self.assertEqual(self.control(screenshots_dir=mine).shots_folder(), (mine, ""))
        change = self.control(screenshots_dir=mine).screenshot()
        self.assertEqual(change.after.path, mine + "\\Screenshot 2026-10-20 10.21.00.png")
        self.disk.set_drive("E:\\", dbk.DRIVE_REMOVABLE)
        for configured in (OWNER + "\\OneDrive\\Pictures\\Shots", "E:\\Shots", "Shots", "\\\\server\\share\\Shots"):
            with self.subTest(configured=configured), self.assertLogs("briefing_reader.desktop.pcctl", "INFO") as logs:
                self.assertEqual(self.control(screenshots_dir=configured).shots_folder(), (SHOTS, ""))
                self.assertNotIn(configured, "\n".join(logs.output))          # never a path in the log
        disk = desktop_disk(self.tmp / "nobody")
        disk.known.pop("profile", None)
        pc = pcctl.PcControl(lambda: self.fake, disk=lambda: disk, timers=self.book, now=self.clock.now)
        change = pc.screenshot()
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.NO_FOLDER, "Your screenshots folder can't be used, sir - nothing was saved."))

    def test_nothing_is_taken_when_the_hud_cant_be_left_out(self) -> None:
        self.fake.hud_refused = True
        change = self.pc.screenshot(hud=(101,))
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.HUD_SHOWS, "I couldn't keep Jarvis out of the picture, sir - nothing was saved."))
        self.assertEqual(self.disk.asked("save_png"), [])
        self.fake.hud_refused, self.fake.no_window = False, True
        change = self.ask("screenshot of the active window")
        self.assertEqual((change.code, self.words(change)),
                         (pcctl.NO_WINDOW, "There's no window to capture, sir - nothing was saved."))
        self.fake.no_window = False
        self.fake.errors["capture"] = OSError(0, "no", None, 8)
        change = self.ask("screenshot")
        self.assertEqual((change.code, change.winerror, self.words(change)),
                         (pcctl.OS_ERROR, 8, "Windows couldn't take the screenshot, sir."))
        self.assertEqual(self.pc.changes(), ())

    def test_never_sent_and_never_logged(self) -> None:
        with self.assertLogs("briefing_reader.desktop.pcctl", "INFO") as logs:
            change = self.ask("screenshot")
        text = "\n".join(logs.output)
        self.assertNotIn("Screenshot 2026", text)
        self.assertNotIn(OWNER, text)
        shown = pcctl.live_note(change) + self.words(change) + repr(change) + repr(change.after)
        self.assertNotIn("Screenshots", shown)

    def test_undo_recycles_it_once_the_bin_is_there(self) -> None:
        change = self.ask("screenshot")
        back = self.pc.undo(change)                       # no Recycle Bin to reach: said plainly, left as it is
        self.assertEqual((back.code, pcctl.undo_words(back)),
                         (dfo.R_NO_BIN, "The Recycle Bin can't be reached right now - left as it is."))
        self.assertTrue(os.path.exists(self.disk.real(change.after.path)))
        bin_ = FakeRecycleBin(self.disk)
        pc = self.control(recycler=bin_)
        bin_.states["C:\\"] = replace(bin_.state(), on=False)       # P2.6: the bin's own pre-checks come first
        off = pc.undo(change)
        self.assertEqual((off.code, pcctl.undo_words(off)),
                         (dfo.R_BIN_OFF, "The Recycle Bin is off for that drive - left as it is."))
        self.assertEqual(bin_.asked_to_bin(), [])
        bin_.states.clear()
        self.assertEqual(pc.undo(change).code, "")
        self.assertFalse(os.path.exists(self.disk.real(change.after.path)))
        self.assertEqual(bin_.recycled, [change.after.path])
        self.assertEqual(pc.undo(change).code, pcctl.GONE)
        changed = self.ask("screenshot")
        Path(self.disk.real(changed.after.path)).write_bytes(b"edited")
        self.assertEqual(pc.undo(changed).code, pcctl.CHANGED)

    def test_the_png(self) -> None:
        data = pcctl.png_bytes(2, 1, bytes([1, 2, 3, 255, 4, 5, 6, 255]))
        self.assertTrue(data.startswith(dbk.PNG_SIGNATURE))
        length, kind = struct.unpack(">I4s", data[8:16])
        self.assertEqual((length, kind), (13, b"IHDR"))
        self.assertEqual(struct.unpack(">IIBBBBB", data[16:29]), (2, 1, 8, 2, 0, 0, 0))
        start = data.index(b"IDAT")
        size = struct.unpack(">I", data[start - 4:start])[0]
        self.assertEqual(zlib.decompress(data[start + 4:start + 4 + size]), bytes([0, 3, 2, 1, 6, 5, 4]))
        self.assertTrue(data.endswith(b"IEND\xaeB`\x82"))
        with self.assertRaises(ValueError):
            pcctl.png_bytes(2, 2, b"\x00" * 4)
        self.assertEqual(pcctl.shot_name(datetime(2026, 10, 29, 9, 5, 7)), "Screenshot 2026-10-29 09.05.07.png")
        self.assertEqual(pcctl.shot_name(datetime(2026, 10, 29, 9, 5, 7), 3), "Screenshot 2026-10-29 09.05.07 (3).png")


# --------------------------------------------------------------------------
# Through the executor (the local route's step, its Live line and Jarvis's words)
# --------------------------------------------------------------------------

class ExecutorStepTests(PcBase):
    def executor(self, pc: Any = "mine") -> dx.Executor:
        return dx.Executor(self.disk, scope=lambda: paths.Scope(), apps=lambda: (), clock=self.clock.monotonic,
                           pc=self.pc if pc == "mine" else pc)

    def test_each_outcome(self) -> None:
        executor = self.executor()
        run = executor.run_route(dr.route("volume 30"))
        result = run.results[0]
        self.assertEqual((result.skill, result.tier, result.status, result.code), (dv.VOLUME, dv.INSTANT, dx.DONE, ""))
        self.assertEqual(dx.run_words(run), "Volume 30, sir.")
        self.assertEqual(dx.describe(result), "On this PC - volume - instant - done (level 50 -> 30)")
        self.assertEqual((dx.run_soft(run), dx.run_waits(run)), (False, False))
        run = executor.run_route(dr.route("volume 40"))                     # within 300 ms
        self.assertEqual(dx.describe(run.results[0]), "On this PC - volume - instant - refused: too_soon")
        self.assertTrue(dx.run_soft(run))
        self.assertEqual(dx.code_words(pcctl.TOO_SOON), "it came too soon after the last volume change")
        self.later()
        self.fake.errors["set_level"] = OSError(0, "denied", None, 5)
        run = executor.run_route(dr.route("volume 40"))
        self.assertEqual(dx.describe(run.results[0]),
                         "On this PC - volume - instant - failed: os_error (Windows error 5)")
        stop = threading.Event()
        stop.set()
        run = executor.run_route(dr.route("pause"), cancel=stop)
        self.assertEqual((run.results[0].status, dx.run_words(run)),
                         (dx.CANCELLED, "Stopped - nothing was changed, sir."))
        run = executor.run_route(dr.route("screenshot"), hud=(7, 8))
        self.assertEqual(self.fake.captures, [(pcctl.SCREEN, (7, 8))])
        self.assertEqual(dx.run_words(run, address="boss"), "Screenshot saved to your screenshots folder, boss.")

    def test_without_the_controls_nothing_is_done(self) -> None:
        run = self.executor(pc=None).run_route(dr.route("pause"))
        self.assertEqual((run.results[0].status, run.results[0].code), (dx.REFUSED, pcctl.UNAVAILABLE))
        self.assertEqual(dx.run_words(run), "I can't reach this PC's controls right now, sir - nothing was changed.")
        self.assertEqual(self.fake.calls, [])

    def test_the_live_summary_says_done(self) -> None:
        from briefing_reader.ask import desktop_mode

        run = self.executor().run_route(dr.route("mute"))
        self.assertEqual(desktop_mode.run_summary(run), "1 done")


# --------------------------------------------------------------------------
# The real seam and its test guard
# --------------------------------------------------------------------------

_PC_CALLS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "audio": ((), {}), "set_level": ((30,), {}), "set_mute": ((True,), {}), "media_state": ((), {}),
    "media_control": (("pause",), {}), "media_key": (("pause",), {}), "capture": ((pcctl.SCREEN,), {"hud": (1,)}),
    "chime": ((), {}), "dnd_state": ((), {}), "open_notifications": ((), {}),
}
_HELPERS = ("_endpoint", "_audio_types", "gsmtc", "_gsmtc_run", "_press_media_key", "_beep", "_grab", "_gdi",
            "_dnd_read")


def _public(cls: type) -> set[str]:
    return {name for name in vars(cls) if not name.startswith("_") and callable(getattr(cls, name))}


class RealPcGuardTests(unittest.TestCase):
    """Every public method of the real seam raises RealBackendInTests while the test guard is on - before any OS
    entry point it could reach (each booby-trapped for this thread meanwhile). One test per method; nothing is ever
    constructed (object.__new__)."""

    def call_refused(self, name: str) -> None:
        pc = object.__new__(pcctl.Win32Pc)
        args, kwargs = _PC_CALLS[name]
        caller = threading.current_thread()
        reached: list[str] = []

        def trap(label: str, original: Any) -> Any:
            def trapped(*a: Any, **k: Any) -> Any:
                if threading.current_thread() is caller:
                    reached.append(label)
                    raise AssertionError(f"the real PC controls reached {label}")
                return original(*a, **k)
            return trapped

        patches = [mock.patch.object(pcctl, helper, trap(helper, getattr(pcctl, helper))) for helper in _HELPERS]
        patches += [mock.patch.object(ctypes, "WinDLL", trap("ctypes.WinDLL", ctypes.WinDLL)),
                    mock.patch.object(importlib, "import_module", trap("import_module", importlib.import_module))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with self.assertRaises(dbk.RealBackendInTests):
            getattr(pc, name)(*args, **kwargs)
        for patch in reversed(patches):
            patch.stop()
        self.assertEqual(reached, [])

    def test_every_public_method_is_guarded_and_tested(self) -> None:
        methods = _public(pcctl.Win32Pc)
        self.assertEqual(methods, set(_PC_CALLS))
        self.assertEqual(methods, _public(pcctl.PcBackend))                    # the Protocol: the same methods
        self.assertLessEqual(methods, _public(FakePc))                         # ... and the fake has every one
        for name in methods:
            with self.subTest(method=name):
                self.assertTrue(getattr(getattr(pcctl.Win32Pc, name), "reaches_os", False))
                self.assertTrue(callable(getattr(self, f"test_{name}_raises_under_the_guard", None)))

    def test_the_real_one_is_never_constructed_in_a_test(self) -> None:
        with self.assertRaises(dbk.RealBackendInTests):
            pcctl.Win32Pc()
        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING"):
            self.assertIsNone(pcctl.PcControl(pcctl.Win32Pc).backend())       # the app's default: no controls here

    def test_called_unbound_it_raises_too(self) -> None:
        for name, (args, kwargs) in _PC_CALLS.items():
            with self.subTest(method=name), self.assertRaises(dbk.RealBackendInTests):
                getattr(pcctl.Win32Pc, name)(None, *args, **kwargs)

    def test_audio_raises_under_the_guard(self) -> None:
        self.call_refused("audio")

    def test_set_level_raises_under_the_guard(self) -> None:
        self.call_refused("set_level")

    def test_set_mute_raises_under_the_guard(self) -> None:
        self.call_refused("set_mute")

    def test_media_state_raises_under_the_guard(self) -> None:
        self.call_refused("media_state")

    def test_media_control_raises_under_the_guard(self) -> None:
        self.call_refused("media_control")

    def test_media_key_raises_under_the_guard(self) -> None:
        self.call_refused("media_key")

    def test_capture_raises_under_the_guard(self) -> None:
        self.call_refused("capture")

    def test_chime_raises_under_the_guard(self) -> None:
        self.call_refused("chime")

    def test_dnd_state_raises_under_the_guard(self) -> None:
        self.call_refused("dnd_state")

    def test_open_notifications_raises_under_the_guard(self) -> None:
        self.call_refused("open_notifications")


if __name__ == "__main__":
    unittest.main()
