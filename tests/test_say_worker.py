"""Tests for speech.SayWorker: the "tts-say" thread of Jarvis's own voice.

A fake synthesizer only (never edge-tts or Windows SAPI): it is made on the worker's thread, gets
one section keyed "say-<seq>" with the text as its one item, and the result comes back through the
callback; a failing synthesizer, a failing factory or a failing callback never ends the thread; a
forgotten job is skipped; and nothing the worker logs holds the text.
"""

from __future__ import annotations

import threading
import unittest

from briefing_reader.models import ITEM_TEXT, SectionAudio
from briefing_reader.speech import SayWorker, say_section

MARKER = "MARKER-SPOKEN-TEXT sam.example@example.com"


class FakeSynth:
    def __init__(self, *, fail_on: tuple[str, ...] = ()) -> None:
        self.thread = threading.current_thread().name
        self.sections = []
        self.fail_on = fail_on
        self.closed = False

    def synthesize_section(self, section):
        self.sections.append(section)
        if section.items[0].spoken in self.fail_on:
            raise RuntimeError(f"cannot say {section.items[0].spoken}")
        return SectionAudio(section_key=section.key, segments=(), engine="edge")

    def close(self) -> None:
        self.closed = True


class Results:
    def __init__(self) -> None:
        self.items: list[tuple[int, object, object, str]] = []
        self.event = threading.Event()
        self.expected = 1

    def __call__(self, seq, audio, error) -> None:
        self.items.append((seq, audio, error, threading.current_thread().name))
        if len(self.items) >= self.expected:
            self.event.set()


class SayWorkerTests(unittest.TestCase):
    def run_worker(self, jobs, *, synth: FakeSynth | None = None, factory=None, on_result=None,
                   expected: int | None = None, forget: tuple[int, ...] = ()):
        made: list[FakeSynth] = []

        def default_factory() -> FakeSynth:
            made.append(synth or FakeSynth())
            made[-1].thread = threading.current_thread().name
            return made[-1]

        results = Results()
        results.expected = len(jobs) - len(forget) if expected is None else expected
        worker = SayWorker(factory or default_factory, on_result or results)
        for seq in forget:
            worker.forget(seq)
        for seq, text in jobs:
            worker.submit(seq, text)
        worker.start()
        if results.expected:
            self.assertTrue(results.event.wait(5), "results arrived")
        worker.stop()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        return results, made

    def test_the_thread_name_and_the_section(self) -> None:
        worker = SayWorker(FakeSynth, lambda *_: None)
        self.assertEqual(worker.name, "tts-say")
        self.assertTrue(worker.daemon)
        section = say_section(7, "Done, sir.")
        self.assertEqual((section.key, section.title), ("say-7", ""))
        self.assertEqual(len(section.items), 1)
        self.assertEqual((section.items[0].kind, section.items[0].spoken, section.items[0].display),
                         (ITEM_TEXT, "Done, sir.", "Done, sir."))

    def test_a_result_per_job_made_on_the_worker_thread(self) -> None:
        results, made = self.run_worker([(1, "Good morning, sir."), (2, "Done, sir.")])
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].thread, "tts-say")                   # the factory ran on the thread
        self.assertEqual([s.key for s in made[0].sections], ["say-1", "say-2"])
        self.assertEqual([(seq, audio.section_key, error, thread) for seq, audio, error, thread in results.items],
                         [(1, "say-1", None, "tts-say"), (2, "say-2", None, "tts-say")])
        self.assertTrue(made[0].closed)

    def test_a_failure_is_an_error_and_the_worker_goes_on(self) -> None:
        with self.assertLogs("briefing_reader.speech", level="WARNING") as logs:
            results, _made = self.run_worker([(1, MARKER), (2, "Done, sir.")],
                                             synth=FakeSynth(fail_on=(MARKER,)))
        (seq1, audio1, error1, _), (seq2, audio2, error2, _) = results.items
        self.assertEqual((seq1, audio1), (1, None))
        self.assertIn("RuntimeError", error1)
        self.assertNotIn("MARKER", error1)
        self.assertEqual((seq2, audio2.section_key, error2), (2, "say-2", None))
        self.assertTrue(any("say-1" in line for line in logs.output))
        self.assertFalse(any("MARKER" in line or "@" in line for line in logs.output))

    def test_a_factory_that_fails_reports_every_job(self) -> None:
        def broken():
            raise OSError("no engine")

        with self.assertLogs("briefing_reader.speech", level="WARNING") as logs:
            results, _made = self.run_worker([(1, "a"), (2, "b")], factory=broken)
        self.assertIn("Could not start Jarvis's speech engine (OSError)", "\n".join(logs.output))
        self.assertEqual([(seq, audio) for seq, audio, _error, _thread in results.items], [(1, None), (2, None)])
        self.assertTrue(all("speech engine" in error for _seq, _audio, error, _thread in results.items))

    def test_a_broken_callback_does_not_end_the_thread(self) -> None:
        seen = []
        done = threading.Event()

        def on_result(seq, audio, error):
            seen.append(seq)
            if seq == 1:
                raise ValueError("bad callback")
            done.set()

        with self.assertLogs("briefing_reader.speech", level="WARNING"):
            self.run_worker([(1, "a"), (2, "b")], on_result=on_result, expected=0)
            self.assertTrue(done.wait(5) or seen == [1, 2])
        self.assertEqual(seen, [1, 2])

    def test_a_forgotten_job_is_skipped(self) -> None:
        results, made = self.run_worker([(1, "a"), (2, "b"), (3, "c")], forget=(2,))
        self.assertEqual([seq for seq, *_rest in results.items], [1, 3])
        self.assertEqual([s.key for s in made[0].sections], ["say-1", "say-3"])

    def test_stop_before_any_job_ends_quietly(self) -> None:
        worker = SayWorker(FakeSynth, lambda *_: None)
        worker.start()
        worker.stop()
        worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_nothing_logged_holds_the_text(self) -> None:
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            import logging
            logging.getLogger("briefing_reader.speech").debug("marker line")
            self.run_worker([(1, MARKER), (2, MARKER + " again")], synth=FakeSynth(fail_on=(MARKER,)))
        self.assertFalse(any("MARKER" in line or "example.com" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
