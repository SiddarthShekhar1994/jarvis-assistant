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
from unittest import mock

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

    def test_live_captions_auto_by_default_on_off_and_a_bad_value_falls_back(self) -> None:
        self.assertEqual(self.parse().live_captions, "auto")
        self.assertEqual(self.parse({"live_captions": " ON "}).live_captions, "on")
        self.assertEqual(self.parse({"live_captions": "off"}).live_captions, "off")
        with self.assertLogs("briefing_reader.config", logging.WARNING):
            self.assertEqual(self.parse({"live_captions": "always"}).live_captions, "auto")

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



# --------------------------------------------------------------------------
# Live captions (smaller items part 8): talk.CaptionPump, TalkSession.partial_clip
# --------------------------------------------------------------------------

class PieceStream:
    """A talk's audio in 30 ms pieces, one per read (the poll's pace); "" once it is all read."""

    def __init__(self, audio: bytes) -> None:
        self._audio = audio
        self._at = 0
        self.closed = False

    def read_available(self) -> bytes:
        piece = self._audio[self._at:self._at + FRAME_BYTES]
        self._at += len(piece)
        return piece

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Peeks:
    """The speech client's peek: says how much audio it was given (its words are made up: "word" per 100 ms),
    recording when each was asked for; ``later`` keeps the work to run by hand (a partial in flight)."""

    def __init__(self, clock: FakeClock, later: bool = False) -> None:
        self.clock = clock
        self.asked: list[tuple[float, int]] = []
        self.delivered: list[tuple[int, str]] = []
        self.later = later
        self.waiting: list = []

    def peek(self, pcm: bytes, cancelled=None) -> str:
        self.cancelled = cancelled
        self.asked.append((self.clock.now, len(pcm)))
        return " ".join(["word"] * max(1, len(pcm) // (SAMPLE_RATE * 2 // 10)))

    def start(self, work) -> None:
        if self.later:
            self.waiting.append(work)
        else:
            work()

    def deliver(self, generation: int, text: str) -> None:
        self.delivered.append((generation, text))


def captioned_talk(audio: bytes, *, wanted=lambda: True, later: bool = False, quiet_ms: int = 500, preroll: bytes = b"",
                   stop_at_ms: int | None = None):
    """A talk polled every 30 ms of a fake clock with a pump ticking after each poll, to its end (or ``stop_at_ms``)."""
    clock = FakeClock()
    peeks = Peeks(clock, later=later)
    session = talk.TalkSession(PieceStream(audio), quiet_ms=quiet_ms, clock=clock, preroll=preroll)
    pump = talk.CaptionPump(peeks.peek, peeks.deliver, wanted=wanted, clock=clock, start=peeks.start)
    began = clock.now
    while not session.poll():
        pump.tick(session)
        clock.now += FRAME_MS / 1000
        if stop_at_ms is not None and (clock.now - began) * 1000 >= stop_at_ms:
            break
    return session, pump, peeks, began


class CaptionSettingTests(unittest.TestCase):
    def test_auto_follows_the_gpu_on_and_off_are_kept(self) -> None:
        for setting, device, on in (("auto", "cuda", True), ("auto", "cpu", False), ("auto", "", False),
                                    ("on", "cpu", True), ("on", "", True), ("off", "cuda", False)):
            with self.subTest(setting=setting, device=device):
                self.assertEqual(talk.captions_on(setting, device), on)

    def test_the_newest_words_without_sound_tags(self) -> None:
        self.assertEqual(talk.caption_text("open spotify"), "open spotify")
        self.assertEqual(talk.caption_text("one two three four five six seven eight nine ten"),
                         "...three four five six seven eight nine ten")
        self.assertEqual(talk.caption_text(" [Music] (upbeat) "), "")
        self.assertEqual(talk.caption_text("..."), "")


class CaptionPumpTests(unittest.TestCase):
    def test_the_pump_s_timing(self) -> None:
        # 300 ms of room, 6 s of speech, then quiet: a partial only after 600 ms of speech, at most every 700 ms
        session, pump, peeks, began = captioned_talk(noise(300, ROOM) + tone(6000, VOICE) + noise(1500, ROOM))
        self.assertEqual(session.ended, talk.END_QUIET)
        times = [round((when - began) * 1000) for when, _size in peeks.asked]
        self.assertGreaterEqual(len(times), 6)
        start_ms = session.endpointer.start_frame * FRAME_MS
        self.assertGreaterEqual(times[0], start_ms + talk.CAPTION_AFTER_MS - talk.SPEECH_START_MS - FRAME_MS)
        self.assertLess(times[0], start_ms + talk.CAPTION_AFTER_MS + 2 * FRAME_MS)
        self.assertTrue(all(later - earlier >= talk.CAPTION_EVERY_MS for earlier, later in zip(times, times[1:])))
        self.assertTrue(all(later - earlier < talk.CAPTION_EVERY_MS + 2 * FRAME_MS
                            for earlier, later in zip(times, times[1:])))
        sizes = [size for _when, size in peeks.asked]
        self.assertEqual(sizes, sorted(sizes))                      # each the talk so far
        self.assertEqual([generation for generation, _text in peeks.delivered], [pump.generation] * len(times))

    def test_past_twelve_seconds_the_newest_twelve_go_on_and_nothing_is_copied_between(self) -> None:
        # Fix round LOW 3: a long request keeps its captions (the newest CAPTION_MAX_S), and the audio is copied only
        # for a partial asked for, never on every poll.
        copies: list[int] = []
        real = talk.TalkSession.partial_clip

        def counted(session, max_bytes=None):
            copies.append(1)
            return real(session, max_bytes)

        with mock.patch.object(talk.TalkSession, "partial_clip", counted):
            session, _pump, peeks, began = captioned_talk(noise(300, ROOM) + tone(20_000, VOICE) + noise(1500, ROOM))
        self.assertEqual(session.ended, talk.END_QUIET)
        self.assertTrue(all(size <= talk.CAPTION_MAX_BYTES for _when, size in peeks.asked))
        self.assertEqual(peeks.asked[-1][1], talk.CAPTION_MAX_BYTES)
        self.assertGreater(round((peeks.asked[-1][0] - began) * 1000), 19_000)   # still captioned near its end
        self.assertEqual(len(copies), len(peeks.asked))

    def test_after_one_slow_partial_none_for_the_rest_of_the_talk(self) -> None:
        # Fix round MEDIUM: on the processor a partial can take seconds; the final must not wait on one after another.
        clock = FakeClock()
        asked: list[float] = []

        def peek(_pcm: bytes, _cancelled=None) -> str:
            asked.append(clock.now)
            clock.now += (talk.CAPTION_SLOW_MS + 100) / 1000
            return "open"

        session = talk.TalkSession(PieceStream(noise(300, ROOM) + tone(9000, VOICE)), clock=clock)
        pump = talk.CaptionPump(peek, lambda _g, _t: None, clock=clock, start=lambda work: work())
        with self.assertLogs("briefing_reader.talk", logging.INFO) as logs:
            for _ in range(250):
                session.poll()
                pump.tick(session)
                clock.now += FRAME_MS / 1000
            pump.stop()
        self.assertEqual(len(asked), 1)
        self.assertTrue(pump.slow)
        self.assertIn("(stopped: slow)", logs.output[0])

    def test_the_next_partial_waits_from_when_the_last_came_back(self) -> None:
        clock = FakeClock()
        asked: list[float] = []

        def peek(_pcm: bytes, _cancelled=None) -> str:
            asked.append(clock.now)
            clock.now += 0.5
            return "open"

        session = talk.TalkSession(PieceStream(noise(300, ROOM) + tone(9000, VOICE)), clock=clock)
        pump = talk.CaptionPump(peek, lambda _g, _t: None, clock=clock, start=lambda work: work())
        for _ in range(200):
            session.poll()
            pump.tick(session)
            clock.now += FRAME_MS / 1000
        pump.stop()
        self.assertGreaterEqual(len(asked), 3)
        self.assertTrue(all(later - earlier >= 0.5 + talk.CAPTION_EVERY_MS / 1000 - 1e-6
                            for earlier, later in zip(asked, asked[1:])))

    def test_a_pump_s_generations_are_never_another_pump_s(self) -> None:
        # Fix round LOW 2: a partial queued by one talk never matches the next talk's pump.
        first = talk.CaptionPump(lambda _p, _c=None: "a", lambda _g, _t: None)
        generation = first.generation
        first.stop()
        second = talk.CaptionPump(lambda _p, _c=None: "b", lambda _g, _t: None)
        self.assertNotEqual(second.generation, generation)
        self.assertIsNone(second.take(generation, "open the"))
        self.assertEqual(second.take(second.generation, "open the"), "open the")

    def test_stop_cancels_a_peek_not_yet_sent(self) -> None:
        # Fix round LOW 4: a peek whose thread starts just before the end is told so (it sends nothing).
        _session, pump, peeks, _began = captioned_talk(noise(300, ROOM) + tone(6000, VOICE), later=True,
                                                       stop_at_ms=2000)
        pump.stop()
        peeks.waiting.pop()()
        self.assertTrue(peeks.cancelled.is_set())

    def test_never_while_one_is_in_flight(self) -> None:
        _session, pump, peeks, _began = captioned_talk(noise(300, ROOM) + tone(6000, VOICE), later=True,
                                                       stop_at_ms=4000)
        self.assertEqual(len(peeks.waiting), 1)                     # the first is still out: no second asked
        self.assertTrue(pump.in_flight)
        peeks.waiting.pop()()
        self.assertFalse(pump.in_flight)
        self.assertEqual(len(peeks.delivered), 1)

    def test_a_late_partial_after_the_end_or_a_cancel_is_dropped(self) -> None:
        for reason in (talk.END_OWNER, talk.END_CANCEL):
            with self.subTest(reason=reason):
                session, pump, peeks, _began = captioned_talk(noise(300, ROOM) + tone(6000, VOICE), later=True,
                                                              stop_at_ms=2000)
                generation = pump.generation
                session.end(reason)
                pump.stop()
                peeks.waiting.pop()()                               # its answer comes back after the end
                self.assertEqual(peeks.delivered, [])
                self.assertIsNone(pump.take(generation, "open the"))
                self.assertFalse(pump.tick(session))

    def test_a_delivered_partial_is_not_shown_once_the_talk_ended(self) -> None:
        _session, pump, peeks, _began = captioned_talk(noise(300, ROOM) + tone(3000, VOICE), stop_at_ms=1500)
        generation, text = peeks.delivered[0]
        self.assertEqual(pump.take(generation, "open the calendar"), "open the calendar")
        pump.stop()
        self.assertIsNone(pump.take(generation, text))              # queued to the screen, but the talk ended

    def test_not_wanted_asks_nothing(self) -> None:
        _session, _pump, peeks, _began = captioned_talk(noise(300, ROOM) + tone(3000, VOICE) + noise(1500, ROOM),
                                                        wanted=lambda: False)
        self.assertEqual(peeks.asked, [])

    def test_nothing_logged_but_counts_and_ms(self) -> None:
        clock = FakeClock()
        said = "please open my secret diary"

        def peek(_pcm: bytes, _cancelled=None) -> str:
            clock.now += 0.25
            return said

        session = talk.TalkSession(PieceStream(noise(300, ROOM) + tone(6000, VOICE)), clock=clock)
        pump = talk.CaptionPump(peek, lambda _g, _t: None, clock=clock, start=lambda work: work())
        with self.assertLogs("briefing_reader.talk", logging.INFO) as logs:
            for _ in range(80):
                session.poll()
                pump.tick(session)
                clock.now += FRAME_MS / 1000
            pump.stop()
            pump.stop()                                             # once only
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], r"Talk: \d+ caption\(s\), 250 ms each$")
        self.assertNotIn("secret", logs.output[0])

    def test_a_failing_peek_is_no_caption(self) -> None:
        clock = FakeClock()

        def peek(_pcm: bytes, _cancelled=None) -> str:
            raise RuntimeError("boom")

        delivered = []
        session = talk.TalkSession(PieceStream(noise(300, ROOM) + tone(3000, VOICE)), clock=clock)
        pump = talk.CaptionPump(peek, lambda g, t: delivered.append(t), clock=clock, start=lambda work: work())
        for _ in range(60):
            session.poll()
            pump.tick(session)
            clock.now += FRAME_MS / 1000
        self.assertEqual((delivered, pump.count, pump.in_flight), ([], 0, False))


class PartialClipTests(unittest.TestCase):
    def test_it_starts_where_the_clip_starts_and_the_final_clip_is_unchanged(self) -> None:
        audio = noise(2000, ROOM) + tone(2500, VOICE) + noise(1500, ROOM)
        captioned, _pump, peeks, _began = captioned_talk(audio)
        plain = talk.TalkSession(PieceStream(audio), clock=FakeClock())
        while not plain.poll():
            pass
        self.assertTrue(peeks.asked)
        self.assertEqual(captioned.clip(), plain.clip())             # what goes in is the same with captions on
        self.assertEqual(captioned.partial_clip(), b"")              # ended: nothing more to caption
        first, _last = captioned.endpointer.span()
        self.assertGreater(first, 0)                                 # a second before the speech, as the clip

    def test_only_the_newest_audio_when_limited(self) -> None:
        session = talk.TalkSession(PieceStream(noise(300, ROOM) + tone(3000, VOICE)), clock=FakeClock())
        for _ in range(80):
            session.poll()
        whole = session.partial_clip()
        self.assertEqual(session.partial_clip(3200), whole[-3200:])
        self.assertEqual(session.partial_clip(3201), whole[-3200:])          # whole samples only
        preroll = tone(400, VOICE)
        woke = talk.TalkSession(PieceStream(tone(1500, VOICE)), clock=FakeClock(), preroll=preroll)
        for _ in range(20):
            woke.poll()
        heard = woke.partial_clip()
        self.assertEqual(woke.partial_clip(len(heard) - 1000), heard[1000:])  # the start of the preroll goes first

    def test_nothing_before_speech_and_a_wake_talk_keeps_its_preroll(self) -> None:
        session = talk.TalkSession(PieceStream(noise(600, ROOM)), clock=FakeClock())
        for _ in range(10):
            session.poll()
        self.assertEqual(session.partial_clip(), b"")
        preroll = tone(400, VOICE)
        woke = talk.TalkSession(PieceStream(tone(1500, VOICE)), clock=FakeClock(), preroll=preroll)
        for _ in range(20):
            woke.poll()
        self.assertTrue(woke.partial_clip().startswith(preroll))


if __name__ == "__main__":
    unittest.main()
