"""Talking to Jarvis, the parts with no Qt and no device: when a talk ends (talk.Endpointer), what was heard
(talk.clean_text), the names the model expects (talk.hint_prompt), [talk] in config.toml, Space in a hotkey and the
talk models' entries. Every sound here is made up (tones and noise); no microphone is opened."""

from __future__ import annotations

import logging
import math
import random
import tempfile
import unittest
from array import array
from pathlib import Path

from briefing_reader import config, hotkey, meeting_speech, talk
from briefing_reader.talk import FRAME_BYTES, FRAME_MS, SAMPLE_RATE, Endpointer


def tone(ms: int, db: float, freq: float = 220.0) -> bytes:
    """A sine of ``ms`` milliseconds whose loudness is ``db`` below full scale."""
    count = SAMPLE_RATE * ms // 1000
    amplitude = 32768.0 * 10 ** (db / 20) * math.sqrt(2)
    return array("h", (max(-32768, min(32767, round(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))))
                       for i in range(count))).tobytes()


def noise(ms: int, db: float, seed: int = 7) -> bytes:
    """Even noise of ``ms`` milliseconds at ``db`` below full scale (a room, a fan)."""
    rng = random.Random(seed)
    reach = 32768.0 * 10 ** (db / 20) * math.sqrt(3)
    return array("h", (max(-32768, min(32767, int(rng.uniform(-reach, reach))))
                       for _ in range(SAMPLE_RATE * ms // 1000))).tobytes()


def zeros(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * 2)


ROOM = -62.0
VOICE = -24.0


def run(*pieces: bytes, **kwargs) -> tuple[Endpointer, str, int]:
    """Feed the pieces in order; (endpointer, end reason, frames read when it ended)."""
    ep = Endpointer(**kwargs)
    reason = ""
    for piece in pieces:
        reason = ep.feed(piece)
        if reason:
            break
    return ep, reason, ep.frames


class LoudnessTests(unittest.TestCase):
    def test_a_tone_s_loudness_is_its_level_in_db(self) -> None:
        self.assertAlmostEqual(talk.frame_db(tone(30, -24.0)), -24.0, delta=0.6)
        self.assertAlmostEqual(talk.frame_db(noise(30, -50.0)), -50.0, delta=1.5)

    def test_silence_and_nothing_are_the_floor_value(self) -> None:
        self.assertEqual(talk.frame_db(zeros(30)), talk.SILENCE_DB)
        self.assertEqual(talk.frame_db(b""), talk.SILENCE_DB)
        self.assertEqual(talk.frame_db(b"\x01"), talk.SILENCE_DB)    # half a sample is nothing

    def test_the_orb_level_runs_zero_to_one(self) -> None:
        self.assertEqual(talk.level_of(-100.0), 0.0)
        self.assertEqual(talk.level_of(0.0), 1.0)
        self.assertAlmostEqual(talk.level_of(-35.0), 0.5)


class EndpointerTests(unittest.TestCase):
    def test_half_a_second_of_quiet_after_speech_ends_the_talk(self) -> None:
        ep, reason, frames = run(noise(300, ROOM), tone(1200, VOICE), noise(2000, ROOM))
        self.assertEqual(reason, talk.END_QUIET)
        self.assertTrue(ep.heard_speech)
        speech_end = (300 + 1200) // FRAME_MS
        self.assertEqual(frames - speech_end, math.ceil(talk.QUIET_MS / FRAME_MS))

    def test_a_short_start_waits_a_little_longer(self) -> None:
        _ep, reason, frames = run(noise(300, ROOM), tone(300, VOICE), noise(2000, ROOM))
        self.assertEqual(reason, talk.END_QUIET)
        waited = (frames - (300 + 300) // FRAME_MS) * FRAME_MS
        self.assertGreaterEqual(waited, talk.QUIET_MS + talk.SHORT_QUIET_EXTRA_MS)

    def test_pauses_between_words_do_not_end_it(self) -> None:
        words = b"".join(tone(400, VOICE) + noise(300, ROOM, seed=i) for i in range(6))
        ep, reason, frames = run(noise(300, ROOM), words, noise(2000, ROOM))
        self.assertEqual(reason, talk.END_QUIET)
        self.assertGreater(frames * FRAME_MS, 300 + 6 * 700)        # it heard every word out

    def test_quiet_ms_follows_the_setting(self) -> None:
        _ep, reason, frames = run(noise(300, ROOM), tone(1200, VOICE), noise(3000, ROOM), quiet_ms=1500)
        self.assertEqual(reason, talk.END_QUIET)
        self.assertEqual(frames - (300 + 1200) // FRAME_MS, math.ceil(1500 / FRAME_MS))

    def test_nothing_said_ends_with_no_speech_after_eight_seconds(self) -> None:
        ep, reason, frames = run(noise(10_000, ROOM))
        self.assertEqual(reason, talk.END_NO_SPEECH)
        self.assertFalse(ep.heard_speech)
        self.assertEqual(frames, round(talk.NO_SPEECH_S * 1000 / FRAME_MS))

    def test_a_click_is_not_speech(self) -> None:
        ep, reason, _frames = run(noise(300, ROOM), tone(60, -6.0), noise(9000, ROOM))
        self.assertEqual(reason, talk.END_NO_SPEECH)
        self.assertFalse(ep.heard_speech)

    def test_a_talk_that_goes_on_stops_at_the_cap(self) -> None:
        ep, reason, frames = run(tone(31_000, VOICE))
        self.assertEqual(reason, talk.END_CAP)
        self.assertEqual(frames, round(talk.MAX_TALK_S * 1000 / FRAME_MS))

    def test_a_fan_sets_the_room_and_speech_over_it_still_ends(self) -> None:
        fan = -38.0
        ep, reason, _frames = run(noise(1500, fan), tone(1500, -12.0), noise(2000, fan, seed=3))
        self.assertEqual(reason, talk.END_QUIET)
        self.assertTrue(ep.heard_speech)
        self.assertGreater(ep.floor_db, -45.0)

    def test_a_loud_fan_from_the_first_frame_starts_nothing(self) -> None:
        fan = -34.0
        ep, reason, _frames = run(noise(9000, fan))
        self.assertEqual(reason, talk.END_NO_SPEECH)
        self.assertFalse(ep.heard_speech)
        over, reason, _frames = run(noise(1500, fan), tone(1200, -10.0), noise(2000, fan, seed=4))
        self.assertEqual(reason, talk.END_QUIET)
        self.assertTrue(over.heard_speech)

    def test_speech_from_the_first_frame_still_starts_once_a_pause_shows_the_room(self) -> None:
        ep, reason, _frames = run(tone(600, VOICE), noise(150, ROOM), tone(900, VOICE), noise(2000, ROOM))
        self.assertEqual(reason, talk.END_QUIET)
        self.assertTrue(ep.heard_speech)
        self.assertEqual(ep.span()[0], 0)              # a second before the start reaches the first word

    def test_a_muted_microphone_is_told_apart(self) -> None:
        ep, reason, _frames = run(zeros(9000))
        self.assertEqual(reason, talk.END_NO_SPEECH)
        self.assertTrue(ep.muted)
        loud, _reason, _f = run(noise(300, ROOM), tone(900, VOICE), noise(1000, ROOM))
        self.assertFalse(loud.muted)

    def test_the_owner_ends_it_now_and_it_stays_ended(self) -> None:
        ep = Endpointer()
        ep.feed(noise(300, ROOM) + tone(600, VOICE))
        self.assertEqual(ep.end(talk.END_OWNER), talk.END_OWNER)
        self.assertEqual(ep.feed(noise(2000, ROOM)), talk.END_OWNER)
        self.assertEqual(ep.end(talk.END_CANCEL), talk.END_OWNER)      # the first end stands

    def test_any_piece_size_reads_the_same(self) -> None:
        audio = noise(300, ROOM) + tone(1100, VOICE) + noise(1500, ROOM)
        whole, reason, frames = run(audio)
        ep = Endpointer()
        for start in range(0, len(audio), 1001):
            if ep.feed(audio[start:start + 1001]):
                break
        self.assertEqual((ep.ended, ep.frames, ep.span()), (reason, frames, whole.span()))

    def test_the_span_keeps_a_second_before_and_a_little_after(self) -> None:
        ep, _reason, _frames = run(noise(3000, ROOM), tone(900, VOICE), noise(2000, ROOM))
        first, last = ep.span()
        start_byte = ep.start_frame * FRAME_BYTES
        self.assertEqual(start_byte - first, talk.BEFORE_SPEECH_MS * SAMPLE_RATE // 1000 * 2)
        self.assertEqual(last - (ep.last_speech_frame + 1) * FRAME_BYTES, talk.AFTER_SPEECH_MS * SAMPLE_RATE // 1000 * 2)

    def test_with_no_speech_found_the_span_is_everything(self) -> None:
        ep = Endpointer()
        ep.feed(noise(2000, ROOM))
        ep.end(talk.END_OWNER)
        self.assertEqual(ep.span(), (0, ep.frames * FRAME_BYTES))

    def test_the_level_follows_the_voice(self) -> None:
        ep = Endpointer()
        ep.feed(noise(300, ROOM))
        quiet = ep.level
        ep.feed(tone(300, -12.0))
        self.assertLess(quiet, 0.1)
        self.assertGreater(ep.level, 0.9)


class CleanTextTests(unittest.TestCase):
    def test_sure_words_are_kept_and_joined(self) -> None:
        segments = [{"text": " What's on my", "no_speech_prob": 0.01, "avg_logprob": -0.2},
                    {"text": "calendar today? ", "no_speech_prob": 0.02, "avg_logprob": -0.3}]
        self.assertEqual(talk.clean_text(segments), "What's on my calendar today?")

    def test_unsure_or_no_speech_segments_are_dropped(self) -> None:
        segments = [{"text": "Thank you.", "no_speech_prob": 0.8, "avg_logprob": -0.9},
                    {"text": "mumble", "no_speech_prob": 0.1, "avg_logprob": -1.3},
                    {"text": "Open Spotify.", "no_speech_prob": 0.1, "avg_logprob": -0.4}]
        self.assertEqual(talk.clean_text(segments), "Open Spotify.")

    def test_sound_tags_and_music_go(self) -> None:
        segments = [{"text": "[Music]", "no_speech_prob": 0.1, "avg_logprob": -0.3},
                    {"text": "(upbeat music) Pause the music ♪", "no_speech_prob": 0.1, "avg_logprob": -0.3},
                    {"text": " ... ", "no_speech_prob": 0.1, "avg_logprob": -0.3}]
        self.assertEqual(talk.clean_text(segments), "Pause the music")

    def test_objects_like_the_model_s_segments_work_too(self) -> None:
        class Segment:
            text, no_speech_prob, avg_logprob = " Stop. ", 0.05, -0.1
        self.assertEqual(talk.clean_text([Segment()]), "Stop.")
        self.assertEqual(talk.clean_text([]), "")


class HintPromptTests(unittest.TestCase):
    def test_jarvis_comes_first_and_each_name_once(self) -> None:
        self.assertEqual(talk.hint_prompt(["Spotify", "jarvis", "Discord", "spotify"]), "Jarvis, Spotify, Discord.")

    def test_it_stops_before_its_length(self) -> None:
        prompt = talk.hint_prompt([f"Name{i:03d}" for i in range(100)])
        self.assertLessEqual(len(prompt), talk.HINT_PROMPT_CHARS)
        self.assertTrue(prompt.startswith("Jarvis, ") and prompt.endswith("."))


class TalkConfigTests(unittest.TestCase):
    def parse(self, table: dict | None = None, environ: dict | None = None, *,
              window: str = "ctrl+alt+j", hud: str = "ctrl+alt+h") -> config.TalkConfig:
        doc = {} if table is None else {"talk": table}
        return config._parse_talk(doc, environ or {}, window, hud)

    def test_off_by_default_with_the_owner_s_choices(self) -> None:
        talk_config = self.parse()
        self.assertEqual((talk_config.enabled, talk_config.key, talk_config.model, talk_config.quiet_ms,
                          talk_config.keep_warm_minutes), (False, "ctrl+alt+space", "small.en", 500, 10))

    def test_the_environment_turns_it_on_and_off(self) -> None:
        self.assertTrue(self.parse(environ={config.TALK_ENABLED_ENV: "true"}).enabled)
        self.assertFalse(self.parse({"enabled": True}, {config.TALK_ENABLED_ENV: "false"}).enabled)

    def test_a_key_that_is_the_window_or_hud_key_is_refused(self) -> None:
        with self.assertLogs("briefing_reader.config", logging.WARNING):
            self.assertEqual(self.parse({"key": "Alt+Ctrl+J"}).key, "ctrl+alt+space")
        with self.assertLogs("briefing_reader.config", logging.WARNING):
            self.assertEqual(self.parse({"key": "ctrl+alt+h"}).key, "ctrl+alt+space")
        with self.assertLogs("briefing_reader.config", logging.WARNING):
            self.assertEqual(self.parse({"key": "ctrl+alt+space"}, window="ctrl+alt+space").key, "")

    def test_bad_values_fall_back(self) -> None:
        with self.assertLogs("briefing_reader.config", logging.WARNING):
            talk_config = self.parse({"model": "large-v3", "quiet_ms": 50, "keep_warm_minutes": "ten"})
        # a number outside its range is held to the range, as everywhere in config.toml
        self.assertEqual((talk_config.model, talk_config.quiet_ms, talk_config.keep_warm_minutes), ("small.en", 300, 10))

    def test_hints_keep_short_names_once_and_never_echo_one(self) -> None:
        long_name = "x" * 41
        with self.assertLogs("briefing_reader.config", logging.WARNING) as logs:
            talk_config = self.parse({"hints": ["Spotify", "spotify", long_name, 3, "Notion"]})
        self.assertEqual(talk_config.hints, ("Spotify", "Notion"))
        self.assertFalse(any(long_name in line for line in logs.output))

    def test_load_config_reads_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text('[talk]\nenabled = true\nkey = "ctrl+alt+k"\n', encoding="utf-8")
            loaded = config.load_config(root, environ={"LOCALAPPDATA": folder})
        self.assertEqual((loaded.talk.enabled, loaded.talk.key), (True, "ctrl+alt+k"))

    def test_the_repo_s_config_toml_has_the_table_off(self) -> None:
        loaded = config.load_config(config.PROJECT_ROOT, environ={"LOCALAPPDATA": tempfile.gettempdir()})
        self.assertFalse(loaded.talk.enabled)
        self.assertEqual(loaded.talk.key, "ctrl+alt+space")


class SpaceKeyTests(unittest.TestCase):
    def test_ctrl_alt_space(self) -> None:
        self.assertEqual(hotkey.parse_combo("Ctrl+Alt+Space"), (hotkey.MOD_CONTROL | hotkey.MOD_ALT, hotkey.VK_SPACE))
        self.assertEqual(hotkey.normalize_combo(" alt + CTRL + space "), "ctrl+alt+space")
        self.assertEqual(hotkey.describe_combo("ctrl+alt+space"), "Ctrl+Alt+Space")

    def test_space_alone_or_with_shift_is_refused(self) -> None:
        for combo in ("space", "shift+space"):
            with self.subTest(combo=combo), self.assertRaises(ValueError):
                hotkey.parse_combo(combo)


class TalkModelTests(unittest.TestCase):
    def test_the_talk_models_and_the_meetings_one(self) -> None:
        small = meeting_speech.SPEECH_MODELS["small.en"]
        self.assertEqual((small.repo, small.folder), ("Systran/faster-whisper-small.en", "faster-whisper-small.en"))
        self.assertIs(meeting_speech.SPEECH_MODELS["medium.en"], meeting_speech.MEETING_MODEL)
        self.assertEqual(set(meeting_speech.SPEECH_MODELS), set(config.TALK_MODELS))

    def test_meetings_keep_their_folder_and_talk_gets_its_own(self) -> None:
        root = Path("C:/models")
        self.assertEqual(meeting_speech.model_dir(root), root / "faster-whisper-medium.en")
        self.assertEqual(meeting_speech.model_dir(root, meeting_speech.SPEECH_MODELS["small.en"]),
                         root / "faster-whisper-small.en")

    def test_a_downloader_for_a_model(self) -> None:
        small = meeting_speech.SPEECH_MODELS["small.en"]
        downloader = meeting_speech.ModelDownloader.for_model(Path("C:/models"), small)
        self.assertEqual((downloader.target, downloader.repo, downloader.files),
                         (Path("C:/models") / small.folder, small.repo, small.files))

    def test_ready_checks_the_model_s_own_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            sizes = {}
            for name in ("config.json", "model.bin"):
                (target / name).write_bytes(b"x" * 5)
                sizes[name] = 5
            (target / meeting_speech.MANIFEST).write_text(
                '{"files": {"config.json": 5, "model.bin": 5}}', encoding="utf-8")
            tiny = meeting_speech.SpeechModel("t", "r/t", "t", "small", files=("config.json", "model.bin"))
            self.assertTrue(meeting_speech.model_ready(target, tiny))
            self.assertFalse(meeting_speech.model_ready(target))    # the meetings' model needs all four files


if __name__ == "__main__":
    unittest.main()
