"""Tests for briefing_reader.tts (offline; the live class needs BRIEFING_LIVE_TTS=1)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import unittest
import unittest.mock
import wave
from pathlib import Path

from edge_tts.exceptions import NoAudioReceived

from briefing_reader import tts
from briefing_reader.models import (
    ITEM_ENTRY,
    ITEM_HEADING,
    ITEM_PAUSE,
    ITEM_TEXT,
    Section,
    SectionAudio,
    ScriptItem,
)
from briefing_reader.tts import (
    EDGE,
    SAPI,
    SpeechSynthesizer,
    TtsError,
    TtsWorker,
    align_marks,
    build_segment_text,
    parse_percent,
    proportional_marks,
    split_segments,
)

WAIT_S = 5.0

# Expected fallback warnings would otherwise go to logging's last-resort stderr handler.
logging.getLogger("briefing_reader.tts").addHandler(logging.NullHandler())


def entry(spoken: str, display: str | None = None) -> ScriptItem:
    return ScriptItem(ITEM_ENTRY, spoken, spoken if display is None else display)


def pause() -> ScriptItem:
    return ScriptItem(ITEM_PAUSE, "", "")


def code(display: str) -> ScriptItem:
    return ScriptItem(ITEM_TEXT, "", display)


def make_section(*items: ScriptItem, key: str = "s1", title: str = "Work inbox") -> Section:
    return Section(key=key, title=title, items=tuple(items))


def two_segment_section(key: str = "s1") -> Section:
    return make_section(
        ScriptItem(ITEM_HEADING, "Work inbox. 2 items.", "Work inbox (2)"),
        entry("Reply to Dave about the Q3 budget."),
        pause(),
        entry("Review the pull request from Sam before noon."),
        key=key,
    )


def fake_words(text: str, step_ms: int = 300) -> tuple[tuple[int, str], ...]:
    return tuple((i * step_ms, w) for i, w in enumerate(re.findall(r"[A-Za-z0-9]+", text)))


def assert_sane_marks(case: unittest.TestCase, marks, expected_items, duration_ms=None) -> None:
    case.assertEqual([idx for _, idx in marks], list(expected_items))
    case.assertEqual(marks[0][0], 0)
    times = [ms for ms, _ in marks]
    case.assertEqual(times, sorted(times))
    if duration_ms is not None:
        case.assertTrue(all(0 <= ms <= duration_ms for ms in times), marks)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class FakeSynth(SpeechSynthesizer):
    """SpeechSynthesizer with the network/SAPI calls replaced by stubs."""

    def __init__(self, out_dir: Path, clock: FakeClock, **kwargs) -> None:
        super().__init__(voice="en-US-GuyNeural", rate="+0%", volume="+0%", offline_voice="",
                         out_dir=out_dir, clock=clock, **kwargs)
        self.edge_error: BaseException | None = None
        self.sapi_error: BaseException | None = None
        self.edge_calls: list[str] = []
        self.sapi_calls: list[str] = []

    def _edge_to_file(self, text, path):
        self.edge_calls.append(text)
        if self.edge_error is not None:
            raise self.edge_error
        path.write_bytes(b"mp3")
        words = fake_words(text)
        return len(words) * 300, words

    def _sapi_to_file(self, text, path):
        self.sapi_calls.append(text)
        if self.sapi_error is not None:
            raise self.sapi_error
        path.write_bytes(b"wav")
        return 1000 + len(text) * 10


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

class ParsePercentTest(unittest.TestCase):
    def test_valid_values(self) -> None:
        self.assertEqual(parse_percent("+15%"), 15)
        self.assertEqual(parse_percent("-10%"), -10)
        self.assertEqual(parse_percent("+0%"), 0)
        self.assertEqual(parse_percent("25%"), 25)
        self.assertEqual(parse_percent(" -5% "), -5)

    def test_junk_is_zero(self) -> None:
        for junk in ("", "fast", "+%", "10", "1.5%", "%10", "+10%%", None, 15):
            with self.subTest(junk=junk):
                self.assertEqual(parse_percent(junk), 0)  # type: ignore[arg-type]

    def test_sapi_rate_and_volume_mapping(self) -> None:
        self.assertEqual(tts._sapi_rate_wpm("+0%"), 200)
        self.assertEqual(tts._sapi_rate_wpm("+50%"), 300)
        self.assertEqual(tts._sapi_rate_wpm("-80%"), 80)
        self.assertEqual(tts._sapi_rate_wpm("+150%"), 400)
        self.assertEqual(tts._sapi_volume_level("+0%"), 1.0)
        self.assertEqual(tts._sapi_volume_level("-50%"), 0.5)
        self.assertEqual(tts._sapi_volume_level("+20%"), 1.0)
        self.assertEqual(tts._sapi_volume_level("-150%"), 0.0)


class SplitSegmentsTest(unittest.TestCase):
    def test_no_pauses_is_one_segment(self) -> None:
        section = make_section(entry("One."), entry("Two."))
        self.assertEqual(split_segments(section, 900), [([(0, "One."), (1, "Two.")], 0)])

    def test_pauses_at_start_middle_and_end(self) -> None:
        section = make_section(pause(), entry("A."), entry("B."), pause(), entry("C."), pause())
        self.assertEqual(split_segments(section, 900),
                         [([(1, "A."), (2, "B.")], 900), ([(4, "C.")], 0)])

    def test_empty_spoken_items_are_skipped(self) -> None:
        section = make_section(entry("A."), code("x = 1"), pause(), code("y = 2"), pause(),
                               entry("   "), entry("B."))
        self.assertEqual(split_segments(section, 750), [([(0, "A.")], 750), ([(6, "B.")], 0)])

    def test_nothing_to_say(self) -> None:
        self.assertEqual(split_segments(make_section(pause(), code("x"), pause()), 900), [])
        self.assertEqual(split_segments(make_section(), 900), [])


class BuildSegmentTextTest(unittest.TestCase):
    def test_offsets(self) -> None:
        text, starts = build_segment_text([(0, "Work inbox."), (2, "Reply to Dave."), (5, "Done.")])
        self.assertEqual(text, "Work inbox.\nReply to Dave.\nDone.")
        self.assertEqual(starts, [(0, 0), (12, 2), (27, 5)])
        for char_start, item_index in starts:
            self.assertTrue(text[char_start:].startswith({0: "Work", 2: "Reply", 5: "Done"}[item_index]))

    def test_empty(self) -> None:
        self.assertEqual(build_segment_text([]), ("", []))


class AlignMarksTest(unittest.TestCase):
    ITEMS = [
        (0, "Work inbox. 2 items."),
        (1, "Reply to Dave about the Q3 budget."),
        (3, "Check example.com/x before noon."),
    ]

    def setUp(self) -> None:
        self.text, self.starts = build_segment_text(self.ITEMS)

    def test_realistic_word_boundaries(self) -> None:
        words = [
            (100, "Work"), (400, "inbox"), (900, "2"), (1100, "items"),
            (1800, "Reply"), (2100, "to"), (2300, "Dave"), (2600, "about"), (2800, "the"),
            (3000, "Q3"), (3300, "budget"),
            (4200, "Check"), (4500, "example.com/x"), (5200, "before"), (5500, "noon"),
        ]
        marks = align_marks(self.text, self.starts, words, 6000)
        self.assertEqual(marks, ((0, 0), (1800, 1), (4200, 3)))

    def test_unknown_words_and_case_differences(self) -> None:
        words = [
            (100, "WORK"), (400, "zzz-not-there"), (500, "inbox"),
            (1800, "reply"), (1900, "nonsense"), (2100, "to"),
            (4200, "CHECK"), (4500, "example.com/x"),
        ]
        marks = align_marks(self.text, self.starts, words, 6000)
        self.assertEqual(marks, ((0, 0), (1800, 1), (4200, 3)))

    def test_item_without_words_is_interpolated(self) -> None:
        words = [(100, "Work"), (400, "inbox"), (4200, "Check"), (4500, "example.com/x")]
        marks = align_marks(self.text, self.starts, words, 6000)
        assert_sane_marks(self, marks, [0, 1, 3])
        self.assertEqual(marks[2], (4200, 3))
        self.assertGreater(marks[1][0], 100)
        self.assertLess(marks[1][0], 4200)

    def test_interpolation_is_by_character_position(self) -> None:
        text, starts = build_segment_text([(0, "a" * 9), (1, "b" * 9), (2, "c" * 9)])
        marks = align_marks(text, starts, [(0, "a" * 9), (2000, "c" * 9)], 3000)
        self.assertEqual(marks, ((0, 0), (1000, 1), (2000, 2)))

    def test_repeated_words_across_items(self) -> None:
        text, starts = build_segment_text([(4, "Call Sam."), (7, "Call Sam again."), (9, "Call Sam.")])
        words = [(0, "Call"), (300, "Sam"), (1000, "Call"), (1300, "Sam"), (1600, "again"),
                 (2500, "Call"), (2800, "Sam")]
        self.assertEqual(align_marks(text, starts, words, 3200), ((0, 4), (1000, 7), (2500, 9)))

    def test_first_mark_is_forced_to_zero(self) -> None:
        text, starts = build_segment_text([(2, "Hello there."), (3, "Second.")])
        marks = align_marks(text, starts, [(350, "Hello"), (700, "there"), (1500, "Second")], 2000)
        self.assertEqual(marks, ((0, 2), (1500, 3)))

    def test_unsorted_starts_and_no_words(self) -> None:
        text, starts = build_segment_text(self.ITEMS)
        marks = align_marks(text, list(reversed(starts)), [], 6000)
        assert_sane_marks(self, marks, [0, 1, 3], 6000)
        self.assertEqual(marks, proportional_marks(len(text), starts, 6000))

    def test_non_monotonic_offsets_are_clamped(self) -> None:
        words = [(100, "Work"), (1800, "Reply"), (1500, "Check")]
        marks = align_marks(self.text, self.starts, words, 6000)
        assert_sane_marks(self, marks, [0, 1, 3])

    def test_marks_past_reported_duration_are_kept_in_order(self) -> None:
        words = [(0, "Work"), (1800, "Reply"), (7000, "Check")]
        marks = align_marks(self.text, self.starts, words, 5000)
        self.assertEqual(marks, ((0, 0), (1800, 1), (7000, 3)))

    def test_empty_starts(self) -> None:
        self.assertEqual(align_marks("", [], [], 0), ())


class ProportionalMarksTest(unittest.TestCase):
    def test_proportional(self) -> None:
        marks = proportional_marks(100, [(0, 0), (50, 2), (75, 3)], 2000)
        self.assertEqual(marks, ((0, 0), (1000, 2), (1500, 3)))

    def test_degenerate_inputs(self) -> None:
        self.assertEqual(proportional_marks(0, [(0, 1), (0, 2)], 1000), ((0, 1), (0, 2)))
        self.assertEqual(proportional_marks(10, [], 1000), ())
        self.assertEqual(proportional_marks(10, [(20, 5), (5, 4)], 1000), ((0, 4), (1000, 5)))


# --------------------------------------------------------------------------
# SpeechSynthesizer engine choice (stubbed engines)
# --------------------------------------------------------------------------

class SpeechSynthesizerFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name) / "audio"
        self.clock = FakeClock()
        self.synth = FakeSynth(self.out_dir, self.clock, divider_pause_ms=800, edge_retry_after_s=120.0)

    def tearDown(self) -> None:
        self.synth.close()
        self._tmp.cleanup()

    def test_edge_success(self) -> None:
        audio = self.synth.synthesize_section(two_segment_section())
        self.assertIsInstance(audio, SectionAudio)
        self.assertEqual(audio.section_key, "s1")
        self.assertEqual(audio.engine, EDGE)
        self.assertEqual(self.synth.last_engine, EDGE)
        self.assertEqual(len(audio.segments), 2)
        first, second = audio.segments
        self.assertEqual((first.pause_after_ms, second.pause_after_ms), (800, 0))
        for segment in audio.segments:
            self.assertTrue(segment.path.is_file())
            self.assertEqual(segment.path.suffix, ".mp3")
            self.assertEqual(segment.path.parent, self.out_dir)
            self.assertRegex(segment.path.stem, r"^[0-9a-f]{16}$")
            self.assertFalse(Path(str(segment.path) + ".part").exists())
        assert_sane_marks(self, first.marks, [0, 1])
        self.assertEqual(first.marks[1], (4 * 300, 1))  # "Reply" is the 5th fake word
        assert_sane_marks(self, second.marks, [3])
        self.assertEqual(self.synth.sapi_calls, [])

    def test_edge_failure_falls_back_and_respects_cooldown(self) -> None:
        self.synth.edge_error = OSError("Cannot connect to host")
        audio = self.synth.synthesize_section(two_segment_section())
        self.assertEqual(audio.engine, SAPI)
        self.assertEqual(self.synth.last_engine, SAPI)
        self.assertEqual(len(self.synth.edge_calls), 1)  # second segment skips edge (cooldown)
        self.assertEqual(len(self.synth.sapi_calls), 2)
        for segment in audio.segments:
            self.assertEqual(segment.path.suffix, ".wav")
        text, starts = build_segment_text([(0, "Work inbox. 2 items."), (1, "Reply to Dave about the Q3 budget.")])
        self.assertEqual(audio.segments[0].marks,
                         proportional_marks(len(text), starts, audio.segments[0].duration_ms))

        self.clock.now += 119
        self.synth.synthesize_section(make_section(entry("Still offline."), key="s2"))
        self.assertEqual(len(self.synth.edge_calls), 1)
        self.assertEqual(len(self.synth.sapi_calls), 3)

    def test_edge_recovers_after_cooldown(self) -> None:
        self.synth.edge_error = OSError("offline")
        self.synth.synthesize_section(make_section(entry("First."), key="s1"))
        self.synth.edge_error = None
        self.clock.now += 60
        self.assertEqual(self.synth.synthesize_section(make_section(entry("Second."), key="s2")).engine, SAPI)
        self.clock.now += 61
        audio = self.synth.synthesize_section(make_section(entry("Third."), key="s3"))
        self.assertEqual(audio.engine, EDGE)
        self.assertEqual(self.synth.last_engine, EDGE)
        self.assertEqual(self.synth.edge_calls, ["First.", "Third."])
        # Recovery clears the cooldown: the next segment goes straight to edge.
        self.synth.synthesize_section(make_section(entry("Fourth."), key="s4"))
        self.assertEqual(self.synth.edge_calls[-1], "Fourth.")

    def test_edge_still_failing_after_cooldown_restarts_it(self) -> None:
        self.synth.edge_error = OSError("offline")
        self.synth.synthesize_section(make_section(entry("One."), key="s1"))
        self.clock.now += 121
        self.synth.synthesize_section(make_section(entry("Two."), key="s2"))
        self.clock.now += 60
        self.synth.synthesize_section(make_section(entry("Three."), key="s3"))
        self.assertEqual(self.synth.edge_calls, ["One.", "Two."])

    def test_cache_reuse(self) -> None:
        section = two_segment_section()
        first = self.synth.synthesize_section(section)
        second = self.synth.synthesize_section(section)
        self.assertEqual(len(self.synth.edge_calls), 2)
        self.assertEqual([s.path for s in first.segments], [s.path for s in second.segments])
        self.assertEqual(first, second)
        # A deleted file is synthesized again.
        first.segments[0].path.unlink()
        self.synth.synthesize_section(section)
        self.assertEqual(len(self.synth.edge_calls), 3)

    def test_same_text_different_item_indices_recomputes_marks(self) -> None:
        a = self.synth.synthesize_section(make_section(entry("One."), entry("Two.")))
        b = self.synth.synthesize_section(make_section(code("x"), entry("One."), entry("Two.")))
        self.assertEqual(len(self.synth.edge_calls), 1)
        self.assertEqual(a.segments[0].path, b.segments[0].path)
        self.assertEqual([i for _, i in b.segments[0].marks], [1, 2])

    def test_cached_sapi_is_used_when_edge_still_fails(self) -> None:
        section = make_section(entry("Cached offline."))
        self.synth.edge_error = OSError("offline")
        first = self.synth.synthesize_section(section)
        self.clock.now += 200  # cooldown over: edge is tried first, then the cached SAPI file
        second = self.synth.synthesize_section(section)
        self.assertEqual(len(self.synth.edge_calls), 2)
        self.assertEqual(len(self.synth.sapi_calls), 1)
        self.assertEqual(second.engine, SAPI)
        self.assertEqual(first.segments[0].path, second.segments[0].path)
        # During the cooldown the cached SAPI file is reused without trying edge.
        third = self.synth.synthesize_section(section)
        self.assertEqual(len(self.synth.edge_calls), 2)
        self.assertEqual(len(self.synth.sapi_calls), 1)
        self.assertEqual(third.segments[0].path, first.segments[0].path)

    def test_cached_sapi_is_replaced_by_edge_once_edge_works(self) -> None:
        section = make_section(entry("Upgrade me."))
        self.synth.edge_error = OSError("offline")
        self.synth.synthesize_section(section)
        self.synth.edge_error = None
        self.clock.now += 121
        audio = self.synth.synthesize_section(section)
        self.assertEqual(audio.engine, EDGE)
        self.assertEqual(audio.segments[0].path.suffix, ".mp3")
        self.synth.synthesize_section(section)
        self.assertEqual(len(self.synth.edge_calls), 2)  # now served from the edge cache

    def test_both_engines_failing_raises(self) -> None:
        self.synth.edge_error = OSError("offline")
        self.synth.sapi_error = RuntimeError("no SAPI voices")
        with self.assertRaises(TtsError) as ctx:
            self.synth.synthesize_section(two_segment_section())
        self.assertIn("RuntimeError", str(ctx.exception))
        self.assertNotIn("Dave", str(ctx.exception))

    def test_no_audio_received_does_not_start_cooldown(self) -> None:
        self.synth.edge_error = NoAudioReceived("No audio was received.")
        audio = self.synth.synthesize_section(make_section(entry("..."), key="s1"))
        self.assertEqual(audio.engine, SAPI)
        self.synth.edge_error = None
        audio = self.synth.synthesize_section(make_section(entry("Real words."), key="s2"))
        self.assertEqual(audio.engine, EDGE)

    def test_section_without_speech(self) -> None:
        audio = self.synth.synthesize_section(make_section(pause(), code("x = 1"), key="s9"))
        self.assertEqual(audio.section_key, "s9")
        self.assertEqual(audio.segments, ())
        self.assertEqual(self.synth.edge_calls + self.synth.sapi_calls, [])

    def test_logs_never_contain_text_at_info(self) -> None:
        self.synth.edge_error = OSError("Cannot connect to wss://example.invalid/tts?TrustedClientToken=abc123")
        with self.assertLogs("briefing_reader.tts", level=logging.INFO) as logs:
            self.synth.synthesize_section(two_segment_section())
        output = "\n".join(logs.output)
        self.assertNotIn("Dave", output)
        self.assertNotIn("abc123", output)
        self.assertIn("WARNING", output)


# --------------------------------------------------------------------------
# Real engine plumbing against fake pyttsx3 / edge-tts objects
# --------------------------------------------------------------------------

class FakePyttsx3Engine:
    """Mimics pyttsx3: commands queue until the first runAndWait, then run synchronously."""

    def __init__(self, fail_saves: int = 0) -> None:
        self.busy = True
        self.queue: list[tuple[str, str]] = []
        self.callbacks: dict[str, list] = {}
        self.run_calls = 0
        self.fail_saves = fail_saves

    def isBusy(self) -> bool:
        return self.busy

    def connect(self, topic, cb):
        self.callbacks.setdefault(topic, []).append(cb)
        return (topic, cb)

    def disconnect(self, token) -> None:
        self.callbacks[token[0]].remove(token[1])

    def save_to_file(self, text: str, filename: str) -> None:
        if self.busy:
            self.queue.append((text, filename))
        else:
            self._write(text, filename)

    def runAndWait(self) -> None:
        self.run_calls += 1
        if not self.busy:
            raise AssertionError("real pyttsx3 would never return here")
        for text, filename in self.queue:
            self._write(text, filename)
        self.queue.clear()
        self.busy = False

    def _write(self, text: str, filename: str) -> None:
        if self.fail_saves:
            self.fail_saves -= 1
            for cb in self.callbacks.get("error", []):
                cb(name=None, exception=OSError("disk full"))
            return
        with wave.open(filename, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16 * len(text) * 10)  # 10 ms per character


class FakeSapiSynth(SpeechSynthesizer):
    """Real SAPI file handling; edge disabled and pyttsx3 replaced by fakes."""

    def __init__(self, out_dir: Path, engines: list[FakePyttsx3Engine]) -> None:
        super().__init__(voice="en-US-GuyNeural", rate="+0%", volume="+0%", offline_voice="",
                         out_dir=out_dir)
        self.spare_engines = engines
        self.created: list[FakePyttsx3Engine] = []

    def _edge_to_file(self, text, path):
        raise OSError("offline")

    def _get_sapi_engine(self):
        if self._sapi_engine is None:
            self._sapi_engine = self.spare_engines.pop(0)
            self.created.append(self._sapi_engine)
        return self._sapi_engine


class SapiPlumbingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_and_wait_only_for_a_fresh_engine(self) -> None:
        engine = FakePyttsx3Engine()
        synth = FakeSapiSynth(self.out_dir, [engine])
        audio = synth.synthesize_section(two_segment_section())
        synth.synthesize_section(make_section(entry("Third call on the same engine."), key="s2"))
        self.assertEqual(engine.run_calls, 1)
        self.assertEqual(synth.created, [engine])
        first_text = "Work inbox. 2 items.\nReply to Dave about the Q3 budget."
        self.assertEqual(audio.segments[0].duration_ms, len(first_text) * 10)
        for segment in audio.segments:
            self.assertEqual(segment.path.suffix, ".wav")
            self.assertTrue(segment.path.is_file())
        self.assertEqual(list(self.out_dir.glob("*.part")), [])

    def test_failure_reinitialises_the_engine_once(self) -> None:
        broken, fresh = FakePyttsx3Engine(fail_saves=1), FakePyttsx3Engine()
        synth = FakeSapiSynth(self.out_dir, [broken, fresh])
        with self.assertLogs("briefing_reader.tts", level=logging.WARNING) as logs:
            audio = synth.synthesize_section(make_section(entry("Hello.")))
        self.assertEqual(synth.created, [broken, fresh])
        self.assertEqual(audio.engine, SAPI)
        self.assertTrue(audio.segments[0].path.is_file())
        self.assertTrue(any("re-initialising" in line for line in logs.output))

    def test_repeated_failure_raises(self) -> None:
        synth = FakeSapiSynth(self.out_dir, [FakePyttsx3Engine(fail_saves=1), FakePyttsx3Engine(fail_saves=1)])
        with self.assertRaises(TtsError):
            synth.synthesize_section(make_section(entry("Hello.")))
        self.assertEqual(list(self.out_dir.glob("*")), [])


class FakeCommunicate:
    """Stands in for edge_tts.Communicate."""

    instances: list["FakeCommunicate"] = []
    fail_after_audio = False
    last_word_ticks = 3_000_000

    def __init__(self, text, voice, **kwargs) -> None:
        self.text, self.voice, self.kwargs = text, voice, kwargs
        FakeCommunicate.instances.append(self)

    async def stream(self):
        yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "Hello"}
        yield {"type": "audio", "data": b"\xff" * 3000}
        if FakeCommunicate.fail_after_audio:
            raise ConnectionResetError("connection lost")
        yield {"type": "WordBoundary", "offset": 5_000_000, "duration": FakeCommunicate.last_word_ticks,
               "text": "there"}
        yield {"type": "audio", "data": b"\xff" * 3000}


class EdgePlumbingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        FakeCommunicate.instances = []
        FakeCommunicate.fail_after_audio = False
        FakeCommunicate.last_word_ticks = 3_000_000
        patcher = unittest.mock.patch.object(tts.edge_tts, "Communicate", FakeCommunicate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self) -> SpeechSynthesizer:
        return SpeechSynthesizer(voice="en-US-GuyNeural", rate="+10%", volume="junk", offline_voice="",
                                 out_dir=self.out_dir)

    def test_stream_to_file(self) -> None:
        synth = self.make()
        audio = synth.synthesize_section(make_section(entry("Hello there.")))
        self.assertEqual(audio.engine, EDGE)
        segment = audio.segments[0]
        self.assertEqual(segment.path.read_bytes(), b"\xff" * 6000)
        self.assertEqual(segment.duration_ms, 1000)  # 6000 bytes at 48 kbit/s
        self.assertEqual(segment.marks, ((0, 0),))
        self.assertEqual(list(self.out_dir.glob("*.part")), [])
        call = FakeCommunicate.instances[0]
        self.assertEqual((call.text, call.voice), ("Hello there.", "en-US-GuyNeural"))
        self.assertEqual(call.kwargs["rate"], "+10%")
        self.assertEqual(call.kwargs["volume"], "+0%")
        self.assertEqual(call.kwargs["boundary"], "WordBoundary")
        self.assertEqual((call.kwargs["connect_timeout"], call.kwargs["receive_timeout"]), (6, 30))

    def test_duration_covers_the_last_word(self) -> None:
        FakeCommunicate.last_word_ticks = 8_000_000  # "there" ends at 1300 ms, after the bytes estimate
        duration_ms, words = self.make()._edge_to_file("Hello there.", self.out_dir / "x.mp3")
        self.assertEqual(words, ((100, "Hello"), (500, "there")))
        self.assertEqual(duration_ms, 1300)
        self.assertTrue((self.out_dir / "x.mp3").is_file())

    def test_broken_stream_leaves_no_partial_file(self) -> None:
        FakeCommunicate.fail_after_audio = True
        with self.assertRaises(ConnectionResetError):
            self.make()._edge_to_file("Hello there.", self.out_dir / "x.mp3")
        self.assertEqual(list(self.out_dir.iterdir()), [])


# --------------------------------------------------------------------------
# TtsWorker
# --------------------------------------------------------------------------

class ScriptedSynth:
    """Minimal synthesizer double for the worker; behaviour chosen per section key."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gates: dict[str, threading.Event] = {}
        self.started: dict[str, threading.Event] = {}
        self.closed = threading.Event()
        self.thread_ids: set[int] = set()

    def synthesize_section(self, section: Section) -> SectionAudio:
        self.thread_ids.add(threading.get_ident())
        self.calls.append(section.key)
        if section.key in self.started:
            self.started[section.key].set()
        if section.key in self.gates:
            self.gates[section.key].wait(WAIT_S)
        if section.key.startswith("tts-error"):
            raise TtsError("Speech synthesis failed: both engines are down")
        if section.key.startswith("crash"):
            raise ValueError("boom")
        return SectionAudio(section_key=section.key)

    def close(self) -> None:
        self.closed.set()


class TtsWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.synth = ScriptedSynth()
        self.results: list[tuple[int, int, SectionAudio | None, str | None]] = []
        self.got_result = threading.Condition()
        self.factory_threads: list[int] = []

    def factory(self) -> ScriptedSynth:
        self.factory_threads.append(threading.get_ident())
        return self.synth

    def on_result(self, generation, index, audio, error) -> None:
        with self.got_result:
            self.results.append((generation, index, audio, error))
            self.got_result.notify_all()

    def wait_results(self, count: int) -> None:
        with self.got_result:
            ok = self.got_result.wait_for(lambda: len(self.results) >= count, WAIT_S)
        self.assertTrue(ok, f"expected {count} results, got {self.results}")

    def start_worker(self, factory=None, on_result=None) -> TtsWorker:
        worker = TtsWorker(factory or self.factory, on_result or self.on_result)
        worker.start()
        self.addCleanup(worker.join, WAIT_S)
        self.addCleanup(worker.stop)
        return worker

    def test_processes_jobs_in_order_on_its_own_thread(self) -> None:
        worker = self.start_worker()
        self.assertTrue(worker.daemon)
        for i in range(3):
            worker.submit(1, i, make_section(entry("x"), key=f"k{i}"))
        self.wait_results(3)
        self.assertEqual([(g, i, a.section_key, e) for g, i, a, e in self.results],
                         [(1, 0, "k0", None), (1, 1, "k1", None), (1, 2, "k2", None)])
        self.assertEqual(self.factory_threads, [worker.ident])
        self.assertEqual(self.synth.thread_ids, {worker.ident})

    def test_drops_stale_generations(self) -> None:
        self.synth.gates["a"] = threading.Event()
        self.synth.started["a"] = threading.Event()
        worker = self.start_worker()
        worker.submit(1, 0, make_section(key="a"))
        self.assertTrue(self.synth.started["a"].wait(WAIT_S))
        worker.submit(1, 1, make_section(key="b"))
        worker.submit(1, 2, make_section(key="c"))
        worker.set_generation(2)
        worker.submit(2, 0, make_section(key="d"))
        worker.submit(1, 3, make_section(key="late-old"))
        worker.submit(2, 1, make_section(key="e"))
        self.synth.gates["a"].set()
        self.wait_results(2)
        time.sleep(0.05)
        self.assertEqual([(g, i) for g, i, _, _ in self.results], [(2, 0), (2, 1)])
        self.assertEqual(self.synth.calls, ["a", "d", "e"])

    def test_submit_with_newer_generation_makes_it_current(self) -> None:
        self.synth.gates["a"] = threading.Event()
        self.synth.started["a"] = threading.Event()
        worker = self.start_worker()
        worker.submit(1, 0, make_section(key="a"))
        self.assertTrue(self.synth.started["a"].wait(WAIT_S))
        worker.submit(1, 1, make_section(key="b"))
        worker.submit(3, 0, make_section(key="c"))
        self.synth.gates["a"].set()
        self.wait_results(1)
        time.sleep(0.05)
        self.assertEqual([(g, i) for g, i, _, _ in self.results], [(3, 0)])
        self.assertEqual(self.synth.calls, ["a", "c"])

    def test_errors_are_reported_and_worker_survives(self) -> None:
        worker = self.start_worker()
        with self.assertLogs("briefing_reader.tts", level=logging.WARNING):
            worker.submit(1, 0, make_section(key="tts-error"))
            worker.submit(1, 1, make_section(key="crash"))
            worker.submit(1, 2, make_section(key="fine"))
            self.wait_results(3)
        (_, _, a0, e0), (_, _, a1, e1), (_, _, a2, e2) = self.results
        self.assertIsNone(a0)
        self.assertIn("both engines", e0)
        self.assertIsNone(a1)
        self.assertIn("ValueError", e1)
        self.assertIsNotNone(a2)
        self.assertIsNone(e2)

    def test_on_result_exceptions_are_caught(self) -> None:
        calls: list[int] = []

        def bad_callback(generation, index, audio, error) -> None:
            calls.append(index)
            if index == 0:
                raise RuntimeError("UI went away")
            self.on_result(generation, index, audio, error)

        worker = self.start_worker(on_result=bad_callback)
        with self.assertLogs("briefing_reader.tts", level=logging.ERROR):
            worker.submit(1, 0, make_section(key="x"))
            worker.submit(1, 1, make_section(key="y"))
            self.wait_results(1)
        self.assertEqual(calls, [0, 1])

    def test_factory_failure_reports_errors(self) -> None:
        def broken_factory():
            raise OSError("no audio folder")

        with self.assertLogs("briefing_reader.tts", level=logging.ERROR):
            worker = self.start_worker(factory=broken_factory)
            worker.submit(1, 0, make_section(key="x"))
            self.wait_results(1)
        generation, index, audio, error = self.results[0]
        self.assertEqual((generation, index, audio), (1, 0, None))
        self.assertIn("speech engine", error)

    def test_stop_does_not_block_and_finishes_current_job(self) -> None:
        self.synth.gates["slow"] = threading.Event()
        self.synth.started["slow"] = threading.Event()
        worker = self.start_worker()
        worker.submit(1, 0, make_section(key="slow"))
        worker.submit(1, 1, make_section(key="never"))
        self.assertTrue(self.synth.started["slow"].wait(WAIT_S))
        t0 = time.monotonic()
        worker.stop()
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertTrue(worker.is_alive())
        self.synth.gates["slow"].set()
        worker.join(WAIT_S)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.synth.calls, ["slow"])
        self.assertEqual([i for _, i, _, _ in self.results], [0])
        self.assertTrue(self.synth.closed.is_set())

    def test_stop_when_idle(self) -> None:
        worker = self.start_worker()
        worker.stop()
        worker.join(WAIT_S)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.synth.closed.is_set())


# --------------------------------------------------------------------------
# Live integration (network + Windows SAPI); opt-in
# --------------------------------------------------------------------------

class _ForcedOfflineSynth(SpeechSynthesizer):
    def _edge_to_file(self, text, path):
        raise OSError("edge disabled for this test")


@unittest.skipUnless(os.environ.get("BRIEFING_LIVE_TTS") == "1",
                     "set BRIEFING_LIVE_TTS=1 to run live edge-tts / SAPI tests")
class LiveTtsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        self.section = two_segment_section()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, cls=SpeechSynthesizer) -> SpeechSynthesizer:
        return cls(voice="en-US-GuyNeural", rate="+0%", volume="+0%", offline_voice="",
                   out_dir=self.out_dir, divider_pause_ms=900)

    def check_audio(self, audio: SectionAudio, engine: str, suffix: str) -> None:
        self.assertEqual(audio.engine, engine)
        self.assertEqual(len(audio.segments), 2)
        self.assertEqual([s.pause_after_ms for s in audio.segments], [900, 0])
        for segment, items in zip(audio.segments, ([0, 1], [3])):
            self.assertTrue(segment.path.is_file(), segment.path)
            self.assertEqual(segment.path.suffix, suffix)
            self.assertGreater(segment.path.stat().st_size, 1000)
            self.assertGreater(segment.duration_ms, 500)
            assert_sane_marks(self, segment.marks, items, segment.duration_ms)
        self.assertEqual(list(self.out_dir.glob("*.part")), [])

    def test_edge_online(self) -> None:
        synth = self.make()
        try:
            t0 = time.monotonic()
            audio = synth.synthesize_section(self.section)
            elapsed = time.monotonic() - t0
        finally:
            synth.close()
        self.check_audio(audio, EDGE, ".mp3")
        first = audio.segments[0]
        # "Reply ..." starts after the heading has been spoken, well inside the clip.
        self.assertGreater(first.marks[1][0], 500)
        self.assertLess(first.marks[1][0], first.duration_ms - 500)
        print(f"\n  edge: {elapsed:.1f} s, durations {[s.duration_ms for s in audio.segments]} ms, "
              f"marks {[s.marks for s in audio.segments]}")

    def test_sapi_forced_direct(self) -> None:
        synth = self.make(_ForcedOfflineSynth)
        try:
            t0 = time.monotonic()
            audio = synth.synthesize_section(self.section)
            elapsed = time.monotonic() - t0
        finally:
            synth.close()
        self.check_audio(audio, SAPI, ".wav")
        for segment in audio.segments:
            with wave.open(str(segment.path), "rb") as wav:
                self.assertGreater(wav.getnframes(), 0)
        # Guards against pyttsx3's runAndWait stall (would cost the 30 s watchdog).
        self.assertLess(elapsed, 10)
        print(f"\n  sapi: {elapsed:.1f} s, durations {[s.duration_ms for s in audio.segments]} ms, "
              f"marks {[s.marks for s in audio.segments]}")

    def test_sapi_forced_in_worker_thread(self) -> None:
        results: list[tuple[int, int, SectionAudio | None, str | None]] = []
        done = threading.Event()

        def on_result(generation, index, audio, error) -> None:
            results.append((generation, index, audio, error))
            if len(results) == 3:
                done.set()

        worker = TtsWorker(lambda: self.make(_ForcedOfflineSynth), on_result)
        worker.start()
        t0 = time.monotonic()
        try:
            worker.submit(1, 0, self.section)
            worker.submit(1, 1, make_section(entry("A second section for the same engine."), key="s2"))
            worker.submit(1, 2, make_section(entry("And a third one, to reuse the engine again."), key="s3"))
            self.assertTrue(done.wait(60), results)
            elapsed = time.monotonic() - t0
        finally:
            worker.stop()
            worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual([error for _, _, _, error in results], [None, None, None])
        self.check_audio(results[0][2], SAPI, ".wav")
        self.assertEqual([audio.engine for _, _, audio, _ in results], [SAPI, SAPI, SAPI])
        self.assertLess(elapsed, 10)
        print(f"\n  sapi worker thread: 3 sections (4 files) in {elapsed:.1f} s")


if __name__ == "__main__":
    unittest.main()
