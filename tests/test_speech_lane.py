"""Tests for speech.SpeechLane: the queue policy of Jarvis's own voice (pure, no Qt, no audio).

First in, first out; at most four waiting (the oldest acknowledgement or greeting goes first, then
the oldest of any kind); too old to start -> dropped (ack 8 s, greeting/announce 20 s, reply 30 s,
result/notice 60 s); a reply drops a waiting acknowledgement; cancel_key drops waiting ones only;
nothing starts while an undo countdown holds the lane; nothing is queued while muted; and the log
names kinds only, never the text.
"""

from __future__ import annotations

import logging
import unittest

from briefing_reader import speech
from briefing_reader.speech import (
    KIND_ACK,
    KIND_ANNOUNCE,
    KIND_GREETING,
    KIND_NOTICE,
    KIND_REPLY,
    KIND_RESULT,
    QUEUE_CAP,
    STALE_AFTER_S,
    SpeechLane,
)

AUDIO = object()   # stands in for a SectionAudio


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class LaneTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.lane = SpeechLane(clock=self.clock)

    def push(self, kind: str, text: str, key: str = "", *, ready: bool = True) -> speech.Utterance:
        utterance, _dropped = self.lane.push(kind, text, key)
        assert utterance is not None
        if ready:
            self.lane.set_audio(utterance.seq, AUDIO)
        return utterance

    def texts(self) -> list[str]:
        return [item.text for item in self.lane.pending]


class OrderTests(LaneTestCase):
    def test_first_in_first_out_one_at_a_time(self) -> None:
        self.push(KIND_GREETING, "one")
        self.push(KIND_RESULT, "two")
        first = self.lane.start_next()
        self.assertEqual(first.text, "one")
        self.assertIs(self.lane.current, first)
        self.assertIsNone(self.lane.start_next())          # one plays: nothing else starts
        self.lane.finish(first.seq)
        self.assertEqual(self.lane.start_next().text, "two")
        self.lane.finish()
        self.assertTrue(self.lane.idle)

    def test_the_head_waits_for_its_audio(self) -> None:
        slow = self.push(KIND_REPLY, "slow", ready=False)
        self.push(KIND_RESULT, "fast")
        self.assertIsNone(self.lane.start_next())           # never out of order
        self.assertTrue(self.lane.set_audio(slow.seq, AUDIO))
        self.assertEqual(self.lane.start_next().text, "slow")

    def test_a_failed_synthesis_is_dropped(self) -> None:
        broken = self.push(KIND_REPLY, "broken", ready=False)
        self.push(KIND_RESULT, "next")
        self.assertIs(self.lane.fail(broken.seq), broken)
        self.assertIsNone(self.lane.fail(broken.seq))
        self.assertEqual(self.lane.start_next().text, "next")

    def test_audio_for_a_dropped_one_is_ignored(self) -> None:
        item = self.push(KIND_ACK, "gone", key="k", ready=False)
        self.lane.cancel_key("k")
        self.assertFalse(self.lane.set_audio(item.seq, AUDIO))
        self.assertIsNone(self.lane.find(item.seq))

    def test_sequence_numbers_are_unique_and_kinds_known(self) -> None:
        a = self.push(KIND_RESULT, "a")
        b = self.push("something else", "b")
        self.assertLess(a.seq, b.seq)
        self.assertEqual(b.kind, KIND_NOTICE)

    def test_empty_text_is_not_queued(self) -> None:
        self.assertEqual(self.lane.push(KIND_RESULT, ""), (None, []))
        self.assertTrue(self.lane.idle)


class CapTests(LaneTestCase):
    def test_at_most_four_wait_and_ack_or_greeting_go_first(self) -> None:
        self.assertEqual(QUEUE_CAP, 4)
        self.push(KIND_RESULT, "r1")
        self.push(KIND_GREETING, "g1")
        self.push(KIND_RESULT, "r2")
        self.push(KIND_ACK, "a1")
        _new, dropped = self.lane.push(KIND_RESULT, "r3")
        self.assertEqual([item.text for item in dropped], ["g1"])   # the oldest ack/greeting
        self.assertEqual(self.texts(), ["r1", "r2", "a1", "r3"])
        _new, dropped = self.lane.push(KIND_RESULT, "r4")
        self.assertEqual([item.text for item in dropped], ["a1"])
        _new, dropped = self.lane.push(KIND_RESULT, "r5")
        self.assertEqual([item.text for item in dropped], ["r1"])   # then the oldest of any kind
        self.assertEqual(self.texts(), ["r2", "r3", "r4", "r5"])

    def test_the_one_playing_does_not_count(self) -> None:
        self.push(KIND_RESULT, "playing")
        self.lane.start_next()
        for n in range(4):
            self.push(KIND_RESULT, f"r{n}")
        self.assertEqual(len(self.lane.pending), 4)
        self.assertEqual(self.lane.current.text, "playing")


class StalenessTests(LaneTestCase):
    def test_limits_per_kind(self) -> None:
        self.assertEqual((STALE_AFTER_S[KIND_ACK], STALE_AFTER_S[KIND_GREETING], STALE_AFTER_S[KIND_ANNOUNCE],
                          STALE_AFTER_S[KIND_REPLY], STALE_AFTER_S[KIND_RESULT], STALE_AFTER_S[KIND_NOTICE]),
                         (8.0, 20.0, 20.0, 30.0, 60.0, 60.0))

    def test_each_kind_is_dropped_only_after_its_limit(self) -> None:
        for kind, limit in STALE_AFTER_S.items():
            with self.subTest(kind=kind):
                lane = SpeechLane(clock=self.clock)
                item, _ = lane.push(kind, "x")
                lane.set_audio(item.seq, AUDIO)
                self.clock.t += limit            # exactly at the limit: still fine
                self.assertEqual(lane.drop_stale(), [])
                self.clock.t += 0.01
                self.assertEqual([gone.kind for gone in lane.drop_stale()], [kind])
                self.clock.t = 1000.0

    def test_a_stale_head_is_dropped_when_it_would_start(self) -> None:
        self.push(KIND_GREETING, "late greeting")
        self.push(KIND_RESULT, "result")
        self.clock.t += 21
        with self.assertLogs("briefing_reader.speech", level="INFO") as logs:
            started = self.lane.start_next()
        self.assertEqual(started.text, "result")
        self.assertIn("Say dropped: stale (greeting)", "\n".join(logs.output))
        self.assertNotIn("late greeting", "\n".join(logs.output))

    def test_a_greeting_waiting_for_its_audio_too_long_is_dropped(self) -> None:
        self.push(KIND_GREETING, "never synthesized", ready=False)
        self.clock.t += 25
        self.assertIsNone(self.lane.start_next())
        self.assertTrue(self.lane.idle)


class RuleTests(LaneTestCase):
    def test_a_reply_drops_an_ack_that_has_not_started(self) -> None:
        self.push(KIND_ACK, "One moment, sir.")
        _reply, dropped = self.lane.push(KIND_REPLY, "Right, sir.")
        self.assertEqual([item.kind for item in dropped], [KIND_ACK])
        self.assertEqual(self.texts(), ["Right, sir."])

    def test_a_reply_keeps_an_ack_that_is_playing(self) -> None:
        self.push(KIND_ACK, "One moment, sir.")
        ack = self.lane.start_next()
        _reply, dropped = self.lane.push(KIND_REPLY, "Right, sir.")
        self.assertEqual(dropped, [])
        self.assertIs(self.lane.current, ack)

    def test_cancel_key_drops_waiting_ones_only(self) -> None:
        playing = self.push(KIND_GREETING, "Good morning, sir.", key="announce:K")
        self.lane.start_next()
        self.push(KIND_ANNOUNCE, "Sir, your AM briefing is ready to view.", key="announce:K")
        self.push(KIND_RESULT, "Done, sir.", key="")
        dropped = self.lane.cancel_key("announce:K")
        self.assertEqual([item.kind for item in dropped], [KIND_ANNOUNCE])
        self.assertIs(self.lane.current, playing)
        self.assertEqual(self.texts(), ["Done, sir."])
        self.assertEqual(self.lane.cancel_key(""), [])   # an empty key matches nothing

    def test_the_countdown_holds_everything(self) -> None:
        self.push(KIND_RESULT, "Done, sir.")
        self.assertIsNone(self.lane.start_next(hold=True))
        self.assertEqual(self.texts(), ["Done, sir."])
        self.assertEqual(self.lane.start_next(hold=False).text, "Done, sir.")

    def test_a_held_one_still_goes_stale(self) -> None:
        self.push(KIND_ACK, "On it, sir.")
        self.clock.t += 9
        self.assertIsNone(self.lane.start_next(hold=True))
        self.assertTrue(self.lane.idle)

    def test_nothing_is_queued_while_muted(self) -> None:
        self.push(KIND_RESULT, "waiting")
        self.lane.start_next()
        self.push(KIND_RESULT, "queued")
        dropped = self.lane.set_muted(True)
        self.assertEqual([item.text for item in dropped], ["waiting", "queued"])
        self.assertTrue(self.lane.muted and self.lane.idle)
        self.assertEqual(self.lane.push(KIND_RESULT, "Done, sir."), (None, []))
        self.assertTrue(self.lane.idle)
        self.assertEqual(self.lane.set_muted(False), [])
        self.assertIsNotNone(self.lane.push(KIND_RESULT, "Done, sir.")[0])

    def test_clear_drops_the_one_playing_too(self) -> None:
        self.push(KIND_RESULT, "a")
        self.lane.start_next()
        self.push(KIND_RESULT, "b")
        self.assertEqual([item.text for item in self.lane.clear()], ["a", "b"])
        self.assertTrue(self.lane.idle)

    def test_finish_with_another_seq_keeps_the_current(self) -> None:
        item = self.push(KIND_RESULT, "a")
        self.lane.start_next()
        self.lane.finish(item.seq + 99)
        self.assertIs(self.lane.current, item)

    def test_the_log_never_holds_the_text(self) -> None:
        with self.assertLogs("briefing_reader.speech", level="DEBUG") as logs:
            logging.getLogger("briefing_reader.speech").debug("marker")
            self.push(KIND_ACK, "MARKER-ACK")
            self.lane.push(KIND_REPLY, "MARKER-REPLY")
            for n in range(5):
                self.lane.push(KIND_RESULT, f"MARKER-{n}")
            self.lane.cancel_key("x")
            self.clock.t += 100
            self.lane.start_next()
        self.assertFalse(any("MARKER-" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
