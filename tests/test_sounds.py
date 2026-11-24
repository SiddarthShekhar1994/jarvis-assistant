"""Tests for briefing_reader.sounds: the soft sounds made in code, and every rule that keeps them quiet.

No test touches an audio device: the board is given a recording player, and the sound files are written into a
temporary folder only.
"""

from __future__ import annotations

import io
import math
import tempfile
import time
import unittest
import wave
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from briefing_reader import config, prefs, sounds
from briefing_reader.sounds import (BOARD_EVENTS, CHANGED, DONE, EVENTS, FAILED, FRAME, HEARD, MAX_PEAK, RATE,
                                    RECIPES, SETTLED, THINKING, WAITS, SoundBoard)

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_sounds", "-platform", "offscreen"])


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class RecordingPlayer:
    def __init__(self, ok: bool = True) -> None:
        self.played: list[str] = []
        self.ok = ok

    def play(self, name: str) -> bool:
        if not self.ok:
            return False
        self.played.append(name)
        return True


def _dominant_hz(samples: list[float]) -> float:
    """The pitch by counting rising zero crossings over the first 40 ms."""
    window = samples[: RATE * 40 // 1000]
    rising = sum(1 for a, b in zip(window, window[1:]) if a <= 0.0 < b)
    return rising / (len(window) / RATE)


class RecipeTests(unittest.TestCase):
    def test_every_sound_peaks_at_its_level_and_never_above_the_cap(self) -> None:
        for name, recipe in RECIPES.items():
            with self.subTest(name=name):
                samples = sounds.render(recipe)
                peak = max(abs(s) for s in samples)
                self.assertLessEqual(peak, MAX_PEAK + 1e-9)
                self.assertAlmostEqual(peak, recipe.peak, places=6)

    def test_every_sound_starts_and_ends_quietly(self) -> None:
        for name, recipe in RECIPES.items():
            with self.subTest(name=name):
                samples = sounds.render(recipe)
                self.assertLess(abs(samples[0]), 0.01)       # an attack, not a click
                self.assertLess(abs(samples[-1]), 0.002)     # a fade, not a click

    def test_lengths_follow_the_design(self) -> None:
        expected_ms = {HEARD: 110, THINKING: 160, WAITS: 240, SETTLED: 900, DONE: 600, FAILED: 700}
        for name, ms in expected_ms.items():
            with self.subTest(name=name):
                self.assertEqual(len(sounds.render(RECIPES[name])), RATE * ms // 1000)

    def test_frame_steps_rise_up_the_scale(self) -> None:
        pitches = [_dominant_hz(sounds.render(RECIPES[f"frame{step}"])) for step in range(4)]
        self.assertEqual(pitches, sorted(pitches))
        self.assertAlmostEqual(pitches[0], 1760.0, delta=60.0)

    def test_sound_name_maps_the_frame_step_and_clamps_it(self) -> None:
        self.assertEqual(sounds.sound_name(FRAME, 0), "frame0")
        self.assertEqual(sounds.sound_name(FRAME, 3), "frame3")
        self.assertEqual(sounds.sound_name(FRAME, 9), "frame3")
        self.assertEqual(sounds.sound_name(FRAME, -2), "frame0")
        self.assertEqual(sounds.sound_name(DONE, 5), DONE)
        self.assertEqual(set(sounds.sound_names()) >= set(EVENTS) - {FRAME}, True)

    def test_wav_is_48k_mono_16_bit(self) -> None:
        data = sounds.wav_bytes(sounds.render(RECIPES[HEARD]))
        with wave.open(io.BytesIO(data)) as fh:
            self.assertEqual((fh.getframerate(), fh.getnchannels(), fh.getsampwidth()), (RATE, 1, 2))
            self.assertEqual(fh.getnframes(), RATE * 110 // 1000)

    def test_glide_moves_the_pitch(self) -> None:
        opening = sounds.render(RECIPES[sounds.OPEN_BIG])
        early = _dominant_hz(opening)
        late = _dominant_hz(opening[RATE * 100 // 1000:])
        self.assertLess(early, late)


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "data" / sounds.FOLDER

    def test_files_are_written_once_and_old_versions_go(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / "sound_heard_v0.wav").write_bytes(b"old")
        (self.folder / "notes.txt").write_text("not ours", encoding="utf-8")
        paths = sounds.build_files(self.folder)
        self.assertEqual(set(paths), set(RECIPES))
        self.assertFalse((self.folder / "sound_heard_v0.wav").exists())
        self.assertTrue((self.folder / "notes.txt").exists())
        stamp = paths[HEARD].stat().st_mtime_ns
        time.sleep(0.02)
        sounds.build_files(self.folder)
        self.assertEqual(paths[HEARD].stat().st_mtime_ns, stamp)

    # The final review's resilience 4 (canvas part 5A): a normal start renders nothing.

    def test_each_file_s_size_is_known_from_its_recipe(self) -> None:
        for name, recipe in RECIPES.items():
            with self.subTest(sound=name):
                self.assertEqual(sounds.file_size(recipe), len(sounds.wav_bytes(sounds.render(recipe))))

    def test_a_second_start_renders_nothing_and_a_wrong_size_is_rendered_again(self) -> None:
        first: list[str] = []
        paths = sounds.build_files(self.folder, rendered=first)
        self.assertEqual(sorted(first), sorted(RECIPES))                  # 13: the nine moments, the frame's four
        self.assertEqual(len(first), 13)
        again: list[str] = []
        real = sounds.render
        calls: list[int] = []
        sounds.render = lambda recipe: (calls.append(1), real(recipe))[1]
        try:
            sounds.build_files(self.folder, rendered=again)
            self.assertEqual((again, calls), ([], []))                    # nothing rendered at all
            paths[HEARD].write_bytes(b"cut short")
            sounds.build_files(self.folder, rendered=again)
        finally:
            sounds.render = real
        self.assertEqual((again, len(calls)), ([HEARD], 1))
        self.assertEqual(paths[HEARD].stat().st_size, sounds.file_size(RECIPES[HEARD]))


class BoardTests(unittest.TestCase):
    def make(self, **kw) -> tuple[SoundBoard, RecordingPlayer, FakeClock]:
        clock = FakeClock()
        player = RecordingPlayer()
        board = SoundBoard(Path("unused"), clock=clock, test_guard=False,
                           player_factory=lambda _paths, _owner: player, **kw)
        board.use_files({})
        return board, player, clock

    def test_plays_when_nothing_holds_it(self) -> None:
        board, player, _clock = self.make()
        self.assertEqual(board.play(DONE), "")
        self.assertEqual(player.played, [DONE])

    def test_never_under_the_test_guard(self) -> None:
        player = RecordingPlayer()
        board = SoundBoard(Path("unused"), test_guard=True, player_factory=lambda _p, _o: player)
        board.start()                                   # builds nothing either
        self.assertFalse(board.ready)
        board.use_files({})
        self.assertEqual(board.play(DONE), sounds.R_TESTS)
        self.assertEqual(player.played, [])

    def test_off_and_loading(self) -> None:
        board, player, _clock = self.make(enabled=lambda: False)
        self.assertEqual(board.play(DONE), sounds.R_OFF)
        fresh = SoundBoard(Path("unused"), test_guard=False)
        self.assertEqual(fresh.play(DONE), sounds.R_LOADING)
        self.assertEqual(player.played, [])

    def test_the_hosts_quiet_reason_holds_every_event(self) -> None:
        for reason in ("muted", "quiet hours", "focus mode", "a meeting records", "locked", "Windows says busy"):
            with self.subTest(reason=reason):
                board, player, _clock = self.make(quiet=lambda reason=reason: reason)
                for event in EVENTS:
                    self.assertEqual(board.play(event), reason)
                self.assertEqual(player.played, [])

    def test_a_failing_quiet_check_counts_as_quiet(self) -> None:
        def broken() -> str:
            raise RuntimeError("probe")
        board, player, _clock = self.make(quiet=broken)
        self.assertEqual(board.play(DONE), "quiet")
        self.assertEqual(player.played, [])

    def test_dropped_while_speaking_and_for_250_ms_after(self) -> None:
        board, player, clock = self.make()
        board.set_speaking(True)
        self.assertEqual(board.play(DONE), sounds.R_SPEAKING)
        clock.tick(5.0)
        board.set_speaking(False)
        clock.tick(0.2)
        self.assertEqual(board.play(DONE), sounds.R_SPEAKING)
        clock.tick(0.1)
        self.assertEqual(board.play(DONE), "")
        self.assertEqual(player.played, [DONE])           # dropped, never queued

    def test_board_sounds_wait_for_a_visible_window(self) -> None:
        board, player, _clock = self.make(visible=lambda: False)
        for event in BOARD_EVENTS:
            self.assertEqual(board.play(event), sounds.R_HIDDEN)
        self.assertEqual(board.play(WAITS), "")
        self.assertEqual(player.played, [WAITS])

    def test_motion_off_drops_the_frame_and_changed_sounds(self) -> None:
        board, player, clock = self.make(motion_off=lambda: True)
        self.assertEqual(board.play(FRAME), sounds.R_MOTION)
        self.assertEqual(board.play(CHANGED), sounds.R_MOTION)
        self.assertEqual(board.play(SETTLED), "")
        self.assertEqual(player.played, [SETTLED])

    def test_gate_and_repeat(self) -> None:
        board, player, clock = self.make()
        self.assertEqual(board.play(DONE), "")
        clock.tick(0.05)
        self.assertEqual(board.play(WAITS), sounds.R_GATE)
        clock.tick(0.1)
        self.assertEqual(board.play(WAITS), "")
        clock.tick(0.5)
        self.assertEqual(board.play(DONE), sounds.R_REPEAT)
        clock.tick(2.0)
        self.assertEqual(board.play(DONE), "")
        self.assertEqual(player.played, [DONE, WAITS, DONE])

    def test_heard_waits_a_second_after_the_wake_chime(self) -> None:
        board, player, clock = self.make()
        board.note_wake_chime()
        self.assertEqual(board.play(HEARD), sounds.R_REPEAT)
        clock.tick(1.1)
        self.assertEqual(board.play(HEARD), "")

    def test_frame_plays_its_step(self) -> None:
        board, player, clock = self.make()
        board.play(FRAME, 2)
        self.assertEqual(player.played, ["frame2"])

    def test_a_player_that_cannot_play_is_not_counted(self) -> None:
        clock = FakeClock()
        player = RecordingPlayer(ok=False)
        board = SoundBoard(Path("unused"), clock=clock, test_guard=False, player_factory=lambda _p, _o: player)
        board.use_files({})
        self.assertEqual(board.play(DONE), sounds.R_LOADING)
        player.ok = True
        self.assertEqual(board.play(DONE), "")             # no repeat hold from the failed try

    def test_unknown_event(self) -> None:
        board, player, _clock = self.make()
        self.assertEqual(board.play("fanfare"), sounds.R_UNKNOWN)
        self.assertEqual(player.played, [])

    def test_start_builds_on_a_worker_thread_and_becomes_ready(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / sounds.FOLDER
        seen: list[dict] = []

        def factory(paths, _owner):
            seen.append(dict(paths))
            return RecordingPlayer()

        board = SoundBoard(folder, test_guard=False, player_factory=factory)
        board.start()
        deadline = time.monotonic() + 20.0
        while not board.ready and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertTrue(board.ready)
        self.assertEqual(set(seen[0]), set(RECIPES))
        self.assertTrue(all(path.is_file() for path in seen[0].values()))


class ConfigTests(unittest.TestCase):
    def parse(self, table: dict) -> config.LookConfig:
        return config._parse_look({"look": table})

    def test_defaults(self) -> None:
        look = config._parse_look({})
        self.assertEqual((look.sounds, look.board, look.second_screen), ("soft", True, False))

    def test_sounds_values(self) -> None:
        self.assertEqual(self.parse({"sounds": "OFF"}).sounds, "off")
        self.assertEqual(self.parse({"sounds": False}).sounds, "off")
        self.assertEqual(self.parse({"sounds": True}).sounds, "soft")
        with self.assertLogs("briefing_reader.config", "WARNING"):
            self.assertEqual(self.parse({"sounds": "loud"}).sounds, "soft")

    def test_switches(self) -> None:
        look = self.parse({"board": False, "second_screen": True})
        self.assertEqual((look.board, look.second_screen), (False, True))


class PrefsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / prefs.PREFS_FILE

    def test_look_round_trip(self) -> None:
        store = prefs.AssistantPrefs(self.path)
        self.assertEqual(store.look, prefs.LookPrefs())
        self.assertTrue(store.set_sounds_off(True))
        self.assertTrue(store.set_second_screen(True))
        self.assertTrue(store.note_board_hint())
        again = prefs.AssistantPrefs(self.path)
        self.assertEqual(again.look, prefs.LookPrefs(sounds_off=True, second_screen=True, board_hints=1))

    def test_defaults_write_no_look_table(self) -> None:
        store = prefs.AssistantPrefs(self.path)
        store.set_muted(True)
        self.assertNotIn('"look"', self.path.read_text(encoding="utf-8"))

    def test_malformed_look_table_is_the_default(self) -> None:
        self.path.write_text('{"look": {"sounds_off": "yes", "second_screen": 1, "board_hints": 500}}',
                             encoding="utf-8")
        self.assertEqual(prefs.AssistantPrefs(self.path).look, prefs.LookPrefs())

    def test_hint_count_stops_at_its_cap(self) -> None:
        self.path.write_text('{"look": {"board_hints": 99}}', encoding="utf-8")
        store = prefs.AssistantPrefs(self.path)
        store.note_board_hint()
        self.assertEqual(store.look.board_hints, prefs.BOARD_HINTS_MAX)


if __name__ == "__main__":
    unittest.main()
