"""Jarvis on your desktop, P2.5, without Qt: focus mode (desktop.pcctl.PcControl.focus and FocusBook), the Undo of
the HUD's instant chips (PcControl.undo_changes: newest first, a run of volume changes put back with ONE change, a run
of the player's with one call, each checked against the PC as it is now - "changed since - left as it is" -, plainly
"can't be put back" for the media key's), the words (focus's caption, the chips', Undo's summary) and Windows' Do
Not Disturb: its settings page opened through the seam only, its read-back read-only and never "On" without Windows
saying so (SHQueryUserNotificationState / FocusSessionManager.IsFocusActive, each faked here - never the real one).

No real OS call: the PC's controls are tests.desktop_fakes.FakePc, the disk a pretend one in a temporary folder, the
clock a fake one; the read-back's own helper runs only over fake modules and a fake shell32.
"""

from __future__ import annotations

import ctypes
import importlib
import json
import threading
import types
import unittest
from typing import Any
from unittest import mock

from briefing_reader.desktop import fileops as dfo
from briefing_reader.desktop import guard, pcctl
from briefing_reader.desktop import routes as dr
from briefing_reader.desktop import validate as dv
from tests.desktop_fakes import FakePc
from tests.test_desktop_pcctl import PcBase

PLAYING, PAUSED, UNKNOWN = pcctl.PLAYING, pcctl.PAUSED, pcctl.UNKNOWN


class FocusBase(PcBase):
    """PcBase with focus mode's book in the same temporary data folder."""

    def setUp(self) -> None:
        super().setUp()
        self.focus_book = pcctl.FocusBook(self.folder, now=self.clock.now)
        self.pc = self.control(focus=self.focus_book)

    def focus(self, text: str = "focus for 30 minutes", **kwargs: Any) -> pcctl.PcChange:
        change = self.ask(text, **kwargs)
        self.later(1.0)                                    # past the volume's 300 ms cap
        return change


# --------------------------------------------------------------------------
# Focus mode
# --------------------------------------------------------------------------

class FocusModeTests(FocusBase):
    def test_it_pauses_what_plays_turns_a_loud_volume_down_and_starts_its_session(self) -> None:
        self.fake.level, self.fake.media = 60, PLAYING
        change = self.focus()
        self.assertEqual((change.kind, change.action, change.code, change.changed, change.undoable),
                         (dv.FOCUS_MODE, pcctl.SET, "", True, True))
        done = change.after
        self.assertIsInstance(done, pcctl.FocusDone)
        self.assertEqual((done.session.minutes, done.music, done.restarted), (30, PAUSED, False))
        self.assertEqual(done.session.ends_at - done.session.set_at, pcctl.timedelta(minutes=30))
        self.assertEqual(self.focus_book.active(), done.session)
        self.assertEqual([(sub.kind, sub.action, sub.group) for sub in done.changes],
                         [(dv.MEDIA, "pause", done.session.focus_id), (dv.VOLUME, "set", done.session.focus_id)])
        self.assertEqual(change.group, done.session.focus_id)
        self.assertEqual((self.fake.media, self.fake.level, self.fake.muted), (PAUSED, pcctl.FOCUS_VOLUME, False))
        self.assertEqual(self.fake.methods(), ["media_state", "media_control", "audio", "set_level"])
        self.assertEqual(self.words(change), "Focus mode is on, sir. I've paused the music and turned the volume down "
                                             "to 20. I'll tell you when the 30 minutes are up.")
        self.assertEqual(pcctl.live_note(change), "30 minutes, Jarvis quiet, the player paused, level 60 -> 20")

    def test_a_quiet_volume_and_paused_music_are_left_as_they_are(self) -> None:
        for level, muted in ((20, False), (12, False), (80, True)):
            with self.subTest(level=level, muted=muted):
                self.focus_book.clear()
                self.fake.calls.clear()
                self.fake.level, self.fake.muted, self.fake.media = level, muted, PAUSED
                change = self.focus("focus mode for 1 hour")
                self.assertEqual((change.after.changes, change.after.music, change.after.session.minutes),
                                 ((), "", 60))
                self.assertNotIn("set_level", self.fake.methods())
                self.assertNotIn("media_control", self.fake.methods())
                self.assertEqual(self.words(change), "Focus mode is on, sir. I'll tell you when the 60 minutes are up.")

    def test_with_the_players_state_unknown_nothing_is_pressed(self) -> None:
        self.fake.media = None                                          # winrt missing: only the media key could
        change = self.focus("focus for 1 minute")
        self.assertEqual(self.fake.keys, [])                            # a blind play/pause could START the music
        self.assertNotIn("media_key", self.fake.methods())
        self.assertEqual(change.after.music, UNKNOWN)
        self.assertEqual(self.words(change), "Focus mode is on, sir. I've turned the volume down to 20. I can't "
                                             "tell if music is playing, so I left it. I'll tell you when the "
                                             "minute is up.")
        self.assertIn("the player's state unknown - left alone", pcctl.live_note(change))

    def test_a_player_that_does_not_pause_is_said(self) -> None:
        self.fake.media, self.fake.takes, self.fake.level = PLAYING, False, 10
        change = self.focus()
        self.assertEqual((change.after.music, change.after.changes), (PLAYING, ()))
        self.assertIn("The player didn't pause, so the music is still on.", self.words(change))

    def test_without_the_controls_focus_still_starts(self) -> None:
        def refuse() -> Any:
            raise pcctl.RealBackendInTests("never in a test")

        pc = pcctl.PcControl(refuse, timers=self.book, focus=self.focus_book, clock=self.clock.monotonic,
                             now=self.clock.now)
        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING"):
            change = pc.focus(30)
        self.assertEqual((change.code, change.after.changes, change.after.music), ("", (), ""))
        self.assertIsNotNone(self.focus_book.active())                  # Jarvis's own quiet needs no controls

    def test_refusals(self) -> None:
        for minutes in (0, 241, 300, None, True, "30"):
            with self.subTest(minutes=minutes):
                change = self.pc.focus(minutes)                         # type: ignore[arg-type]
                self.assertEqual(change.code, dv.SHAPE)
        self.assertEqual(self.words(self.pc.focus(300)),
                         "Focus mode runs from 1 to 240 minutes, sir - nothing was changed.")
        no_book = self.control()
        self.assertEqual(no_book.focus(30).code, pcctl.UNAVAILABLE)
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.pc.focus(30, cancel=stop).code, pcctl.STOPPED)
        self.assertIsNone(self.focus_book.active())
        self.assertEqual(self.fake.calls, [])

    def test_a_turn_off_right_before_the_volume_leaves_it_and_starts_nothing(self) -> None:
        self.fake.level = 70
        stop = threading.Event()
        reading = self.fake.audio

        def audio() -> Any:
            stop.set()                                                  # the owner's Turn off, right then
            return reading()

        self.fake.audio = audio                                         # type: ignore[method-assign]
        change = self.pc.focus(30, cancel=stop)
        self.assertEqual(change.code, pcctl.STOPPED)
        self.assertEqual(self.fake.level, 70)
        self.assertNotIn("set_level", self.fake.methods())
        self.assertIsNone(self.focus_book.active())

    def test_asked_again_while_it_runs_it_starts_again_from_now_and_keeps_its_id(self) -> None:
        self.fake.level, self.fake.media = 60, PLAYING
        first = self.focus()
        self.later(10 * 60)
        self.fake.media = PLAYING                                       # the owner played his music again meanwhile
        again = self.focus("focus mode for 1 hour")
        session = again.after.session
        self.assertTrue(again.after.restarted)
        self.assertEqual(session.focus_id, first.after.session.focus_id)
        self.assertEqual((session.minutes, session.ends_at - session.set_at), (60, pcctl.timedelta(minutes=60)))
        self.assertEqual(session.set_at, self.clock.now() - pcctl.timedelta(seconds=1))
        self.assertEqual([(sub.kind, sub.group) for sub in again.after.changes], [(dv.MEDIA, session.focus_id)])
        self.assertIs(again.before, first.after.session)
        self.assertEqual(self.words(again), "Focus mode starts again, sir. I've paused the music. I'll tell you when "
                                            "the 60 minutes are up.")

    def test_routes_and_the_executor_carry_it_out(self) -> None:
        asked = dr.pc_request("focus mode for half an hour")
        self.assertEqual((asked.kind, asked.minutes), (dv.FOCUS_MODE, 30))
        self.assertIn(dv.FOCUS_MODE, pcctl.PC_SKILLS)
        self.assertEqual(self.pc.carry_out(asked).after.session.minutes, 30)


class FocusBookTests(FocusBase):
    def test_its_file_keeps_ids_minutes_and_times_only_and_a_restart_stays_quiet(self) -> None:
        session, again = self.focus_book.start(45)
        self.assertFalse(again)
        saved = json.loads((self.folder / pcctl.FOCUS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(saved["focus"]), {"id", "minutes", "set_at", "ends_at"})
        restarted = pcctl.FocusBook(self.folder, now=self.clock.now)    # Jarvis started again
        self.assertEqual(restarted.load(), session)
        self.assertEqual(restarted.seconds_left(), 45 * 60)
        self.later(45 * 60)
        self.assertEqual(pcctl.FocusBook(self.folder, now=self.clock.now).load(), None)   # it ended while closed
        self.assertIsNone(json.loads((self.folder / pcctl.FOCUS_FILE).read_text(encoding="utf-8"))["focus"])

    def test_its_end_is_taken_once_and_an_end_by_id_only_ends_that_one(self) -> None:
        session, _again = self.focus_book.start(1)
        self.assertEqual(self.focus_book.take_due(), (None, 0.0))
        self.assertIsNone(self.focus_book.end("ffffffff"))              # not this session: nothing ends
        self.later(61)
        ended, late = self.focus_book.take_due()
        self.assertEqual((ended, late), (session, 1.0))
        self.assertEqual(self.focus_book.take_due(), (None, 0.0))
        self.assertIsNone(self.focus_book.active())

    def test_a_bad_file_is_dropped(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for text in ("{", json.dumps({"focus": {"id": "x", "minutes": 30}}),
                     json.dumps({"focus": {"id": "0123abcd", "minutes": 900, "set_at": "2026-10-20T10:00:00",
                                           "ends_at": "2026-10-21T01:00:00"}})):
            with self.subTest(text=text):
                (self.folder / pcctl.FOCUS_FILE).write_text(text, encoding="utf-8")
                self.assertIsNone(pcctl.FocusBook(self.folder, now=self.clock.now).load())


# --------------------------------------------------------------------------
# Undo of the instant chips (Undo all)
# --------------------------------------------------------------------------

class UndoTests(FocusBase):
    def undo(self, changes: Any, **kwargs: Any) -> list[tuple[pcctl.PcChange, pcctl.PcChange]]:
        self.later(1.0)
        return list(self.pc.undo_changes(changes, **kwargs))

    def test_a_run_of_volume_changes_is_put_back_with_one_change(self) -> None:
        self.fake.level = 60
        made = [self.focus("volume 50"), self.focus("louder"), self.focus("volume 30")]
        self.fake.calls.clear()
        results = self.undo(made)
        self.assertEqual([change for change, _result in results], made[::-1])      # newest first
        self.assertEqual([result.code for _change, result in results], ["", "", ""])
        self.assertEqual(self.fake.methods(), ["audio", "set_level"])               # ONE change, not three
        self.assertEqual(self.fake.level, 60)
        self.assertEqual(pcctl.undo_summary(results), "Back as it was, sir: volume 60%.")
        self.assertEqual(self.pc.changes(), tuple(made))                            # an Undo is not a change

    def test_one_changed_since_is_left_and_so_are_the_older_ones(self) -> None:
        self.fake.level = 60
        older = self.focus("volume 50")
        self.fake.level = 45                                                        # the owner's own volume key
        newer = self.focus("volume 30")
        results = self.undo([older, newer])
        self.assertEqual([(change, result.code) for change, result in results],
                         [(newer, ""), (older, pcctl.CHANGED)])
        self.assertEqual(self.fake.level, 45)                                       # back to what was before Jarvis's
        self.assertEqual(pcctl.undo_summary(results),
                         "Back as it was, sir: volume 45%. The volume changed since - left as it is.")

    def test_the_players_run_needs_one_call_and_the_media_key_cant_be_put_back(self) -> None:
        self.fake.media = PLAYING
        paused = self.focus("pause")
        played = self.focus("play")
        self.fake.media = None
        key = self.focus("next song")                                               # by the media key
        self.fake.media = PLAYING
        self.fake.calls.clear()
        results = self.undo([paused, played, key])
        self.assertEqual([(change, result.code) for change, result in results],
                         [(key, pcctl.CANT_UNDO), (played, ""), (paused, "")])
        self.assertEqual(self.fake.methods(), ["media_state"])                      # it plays: as it was - no call
        self.assertEqual(pcctl.undo_summary(results),
                         "Back as it was, sir: music playing. What the media key did can't be put back.")

    def test_focus_puts_back_the_music_and_the_volume_and_ends_its_session(self) -> None:
        self.fake.level, self.fake.media = 60, PLAYING
        change = self.focus()
        made = [*change.after.changes, change]
        results = self.undo(made)
        self.assertEqual([result.code for _change, result in results], ["", "", ""])
        self.assertEqual((self.fake.media, self.fake.level), (PLAYING, 60))
        self.assertIsNone(self.focus_book.active())
        self.assertEqual(pcctl.undo_summary(results), "Focus ended, sir. Back as it was: music playing, volume 60%.")
        self.assertEqual([pcctl.undo_note(change, result) for change, result in results],
                         ["focus_mode - put back", "volume (focus) - put back", "media (focus) - put back"])

    def test_timers_screenshots_and_the_plain_words_for_what_stays(self) -> None:
        timer = self.focus("timer 10 minutes")
        shot = self.focus("screenshot")
        results = self.undo([timer, shot])
        self.assertEqual([(change, result.code) for change, result in results],
                         [(shot, dfo.R_NO_BIN), (timer, "")])
        self.assertEqual(self.book.running(), ())
        self.assertEqual(pcctl.undo_summary(results), "Back as it was, sir: timer cancelled. The screenshot stays - "
                                                      "the Recycle Bin can't be reached right now.")
        self.book.add(10)
        cancelled = self.focus("cancel the timers")
        results = self.undo([cancelled])
        self.assertEqual((results[0][1].code, len(self.book.running())), ("", 1))
        self.assertEqual(pcctl.undo_summary(results), "Back as it was, sir: timer back.")

    def test_too_soon_unavailable_and_stopped_put_nothing_back(self) -> None:
        self.fake.level = 60
        change = self.focus("volume 30")
        self.pc.volume("up")                                                        # just now: the cap
        too_soon = self.pc.undo_changes([change])
        self.assertEqual(too_soon[0][1].code, pcctl.TOO_SOON)
        self.assertEqual(pcctl.undo_summary(too_soon),
                         "Nothing was put back, sir. The volume changed a moment ago - try Undo again.")
        self.assertEqual(self.undo([change])[0][1].code, pcctl.CHANGED)          # 40 now: up since, left as it is
        fresh = self.focus("volume 25")
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.undo([fresh], cancel=stop)[0][1].code, pcctl.STOPPED)
        self.assertEqual(self.fake.level, 25)

        def refuse() -> Any:
            raise pcctl.RealBackendInTests("never in a test")

        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING"):
            results = pcctl.PcControl(refuse).undo_changes([change])
        self.assertEqual(results[0][1].code, pcctl.UNAVAILABLE)
        self.assertEqual(pcctl.undo_summary(results),
                         "Nothing was put back, sir. I can't reach this PC's controls right now.")


# --------------------------------------------------------------------------
# The chips' words
# --------------------------------------------------------------------------

class ChipWordsTests(FocusBase):
    def test_one_chip_per_kind_says_what_its_newest_change_did(self) -> None:
        self.fake.level, self.fake.media = 60, PLAYING
        focus = self.focus()
        made = [*focus.after.changes, focus, self.focus("volume 35"), self.focus("timer 10 minutes"),
                self.focus("screenshot"), self.focus("screenshot")]
        self.fake.media = None
        made.append(self.focus("next song"))
        words = {kind: pcctl.chip_words(kind, made) for kind in pcctl.INSTANT_KINDS}
        self.assertEqual(words, {pcctl.CHIP_FOCUS: "Focus \u00b7 ends 10:51 AM",
                                 pcctl.CHIP_MUSIC: "Music \u00b7 next track",
                                 pcctl.CHIP_VOLUME: "Volume \u00b7 35%",
                                 pcctl.CHIP_TIMERS: "Timer set \u00b7 10 min",
                                 pcctl.CHIP_SHOTS: "2 screenshots \u00b7 saved"})
        self.assertEqual(pcctl.chip_words(pcctl.CHIP_FOCUS, made, hour24=True), "Focus \u00b7 ends 10:51")
        self.assertEqual(pcctl.chip_words(pcctl.CHIP_MUSIC, made[:1]), "Music \u00b7 paused")
        self.assertEqual(pcctl.chip_words(pcctl.CHIP_VOLUME, [self.pc.volume("mute")]), "Volume \u00b7 muted")
        self.assertEqual(pcctl.chip_words(pcctl.CHIP_SHOTS, []), "")
        self.assertEqual(tuple(pcctl.INSTANT_KINDS), ("focus", "music", "volume", "timers", "shots"))

    def test_the_focus_end_toast(self) -> None:
        session, _again = self.focus_book.start(30)
        self.assertEqual(pcctl.focus_end_words(session), "Your 30-minute focus is up, sir.")
        self.assertEqual(pcctl.focus_end_words(session, 3600, "boss"), "Your 30-minute focus ended at 10:51 AM, boss.")


# --------------------------------------------------------------------------
# Windows' Do Not Disturb: opened for the owner's switch, read back read-only
# --------------------------------------------------------------------------

class DoNotDisturbTests(FocusBase):
    def test_the_chip_never_says_on_without_windows_saying_so(self) -> None:
        self.assertEqual(pcctl.dnd_chip_text(pcctl.DND_ON), "Do Not Disturb \u00b7 On")
        for state in (pcctl.DND_OFF, pcctl.DND_UNKNOWN, "", "maybe", None):
            with self.subTest(state=state):
                self.assertEqual(pcctl.dnd_chip_text(state), "Do Not Disturb \u00b7 Turn on")  # type: ignore

    def test_the_read_back_through_the_seam(self) -> None:
        for read, state in (("on", pcctl.DND_ON), ("off", pcctl.DND_OFF), ("unknown", pcctl.DND_UNKNOWN),
                            ("weird", pcctl.DND_UNKNOWN)):
            with self.subTest(read=read):
                self.fake.dnd = read
                self.assertEqual(self.pc.dnd_state(), state)
        self.fake.errors["dnd_state"] = OSError(5, "denied")
        self.assertEqual(self.pc.dnd_state(), pcctl.DND_UNKNOWN)

        def refuse() -> Any:
            raise pcctl.RealBackendInTests("never in a test")

        with self.assertLogs("briefing_reader.desktop.pcctl", "WARNING"):
            self.assertEqual(pcctl.PcControl(refuse).dnd_state(), pcctl.DND_UNKNOWN)   # unreadable: never "On"

    def test_the_opener_is_the_one_fixed_call(self) -> None:
        self.assertEqual(self.pc.open_dnd().code, "")
        self.assertEqual((self.fake.settings_opened, self.fake.methods()), (1, ["open_notifications"]))
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.pc.open_dnd(cancel=stop).code, pcctl.STOPPED)
        self.fake.errors["open_notifications"] = OSError(0, "x", None, 1155)
        failed = self.pc.open_dnd()
        self.assertEqual((failed.code, failed.winerror), (pcctl.OS_ERROR, 1155))
        self.assertEqual(self.fake.settings_opened, 1)
        self.assertEqual(guard.DND_OPENER_URI, "ms-settings:notifications")
        with mock.patch("briefing_reader.desktop.backend._shell_open") as shell_open, \
                self.assertRaises(pcctl.RealBackendInTests):
            object.__new__(pcctl.Win32Pc).open_notifications()       # the guard first: the shell is never reached
        shell_open.assert_not_called()

    def test_the_read_back_helper_reads_and_maps_only(self) -> None:
        """pcctl._dnd_read over fakes: a Windows focus session through winrt when it answers, else
        SHQueryUserNotificationState's quiet time (on) or notifications accepted (off); anything else unknown."""

        class Shell32:
            def __init__(self, state: int, result: int = 0) -> None:
                self.state, self.result = state, result

            def SHQueryUserNotificationState(self, pointer: Any) -> int:  # noqa: N802 - the Win32 name
                pointer._obj.value = self.state
                return self.result

        def run(shell: Any, focus_active: Any = None) -> str:
            def importer(name: str) -> Any:
                if focus_active is None:
                    raise ImportError(name)
                manager = types.SimpleNamespace(is_focus_active=focus_active)
                kind = types.SimpleNamespace(is_supported=True, get_default=lambda: manager)
                return types.SimpleNamespace(FocusSessionManager=kind)

            with mock.patch.object(importlib, "import_module", importer), \
                    mock.patch.object(ctypes, "WinDLL", lambda name: shell):
                return pcctl._dnd_read()

        self.assertEqual(run(Shell32(6)), pcctl.DND_ON)                   # QUNS_QUIET_TIME
        self.assertEqual(run(Shell32(5)), pcctl.DND_OFF)                  # QUNS_ACCEPTS_NOTIFICATIONS
        for state in (1, 2, 3, 4, 7):
            with self.subTest(state=state):
                self.assertEqual(run(Shell32(state)), pcctl.DND_UNKNOWN)  # busy, full screen ...: says nothing of DND
        self.assertEqual(run(Shell32(6, result=-2147467259)), pcctl.DND_UNKNOWN)     # the call failed
        self.assertEqual(run(Shell32(5), focus_active=True), pcctl.DND_ON)            # a Windows focus session
        self.assertEqual(run(Shell32(5), focus_active=False), pcctl.DND_OFF)          # none: the next way decides

        def broken(name: str) -> Any:
            raise OSError("no shell32")

        with mock.patch.object(importlib, "import_module", side_effect=ImportError("x")), \
                mock.patch.object(ctypes, "WinDLL", broken):
            self.assertEqual(pcctl._dnd_read(), pcctl.DND_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
