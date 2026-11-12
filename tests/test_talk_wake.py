"""A talk "Hey Jarvis" started: the endpointer's deaf window (the chime), the session's pre-roll (a request said in
one breath with the wake words), and TalkController.listen_from_wake on the listener's audio - the same talk as the
talk key's, with 5 s to start, the chime only when nothing was said, the wake words taken off what goes in, a quiet
end when nothing was heard, and how each one ended. Every sound is made up; no microphone or process is used."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import config, hud, talk, talk_ui
from briefing_reader.talk import FRAME_MS, Endpointer, TalkSession
from briefing_reader.talk_speech import Heard
from tests.test_talk import ROOM, VOICE, noise, tone
from tests.test_talk_speech import FakeBackend, FakeStream
from tests.test_talk_ui import FakeClient, FakeStopKeys

APP = QApplication.instance() or QApplication([])
WAKE_WORDS = tone(1200, VOICE, freq=180.0)          # "Hey Jarvis" (before the wake: the pre-roll)


def feed_all(endpointer: Endpointer, *pieces: bytes) -> str:
    reason = ""
    for piece in pieces:
        reason = endpointer.feed(piece)
        if reason:
            break
    return reason


class DeafWindowTests(unittest.TestCase):
    def test_a_chime_in_the_deaf_window_never_starts_the_request_nor_moves_the_room(self) -> None:
        ep = Endpointer(no_speech_s=2.0)
        ep.feed(noise(300, ROOM))
        floor = ep.floor_db
        ep.deafen(talk.CHIME_DEAF_MS)
        ep.feed(tone(200, -20.0, freq=880.0))                       # the chime, loud
        self.assertFalse(ep.heard_speech)
        self.assertEqual(ep.loud_frames, 0)
        self.assertEqual(ep.floor_db, floor)
        self.assertEqual(feed_all(ep, noise(3000, ROOM)), talk.END_NO_SPEECH)

    def test_speech_going_on_past_the_window_starts_it_and_keeps_what_was_said_in_it(self) -> None:
        ep = Endpointer()
        ep.feed(noise(300, ROOM))
        ep.deafen(talk.CHIME_DEAF_MS)
        deaf_end = ep.frames + talk.CHIME_DEAF_MS // FRAME_MS
        ep.feed(tone(1500, VOICE))
        self.assertTrue(ep.heard_speech)
        self.assertGreaterEqual(ep.start_frame, deaf_end)
        first, _last = ep.span()
        self.assertLess(first, 300 * 32)                             # a second before: the words said in the window

    def test_loud_frames_count_speech_too_short_to_start_a_request(self) -> None:
        ep = Endpointer()
        ep.feed(noise(300, ROOM) + tone(60, VOICE) + noise(300, ROOM))
        self.assertFalse(ep.heard_speech)
        self.assertGreaterEqual(ep.loud_frames, 1)


class PrerollTests(unittest.TestCase):
    def session(self, audio: bytes, **kwargs) -> TalkSession:
        kwargs.setdefault("preroll", WAKE_WORDS)
        kwargs.setdefault("deaf_ms", talk.WAKE_START_DEAF_MS)
        kwargs.setdefault("no_speech_s", talk.WAKE_NO_SPEECH_S)
        return TalkSession(FakeStream(audio), **kwargs)

    def through(self, session: TalkSession) -> str:
        for _ in range(3000):
            reason = session.poll()
            if reason:
                return reason
        return ""

    def test_a_request_said_in_the_same_breath_keeps_the_wake_words_in_its_clip(self) -> None:
        said = tone(900, VOICE)
        session = self.session(said + noise(1500, ROOM))
        self.assertEqual(self.through(session), talk.END_QUIET)
        clip = session.clip()
        self.assertTrue(clip.startswith(WAKE_WORDS + said))

    def test_a_request_after_a_pause_also_starts_at_the_pre_roll(self) -> None:
        after = noise(1600, ROOM) + tone(1000, VOICE)
        session = self.session(after + noise(1500, ROOM))
        self.assertEqual(self.through(session), talk.END_QUIET)
        self.assertTrue(session.clip().startswith(WAKE_WORDS + after))   # the wake words come off the words

    def test_the_end_of_jarvis_never_starts_the_request(self) -> None:
        session = self.session(tone(150, VOICE) + noise(2000, ROOM) + tone(900, VOICE) + noise(1500, ROOM))
        self.assertEqual(self.through(session), talk.END_QUIET)
        self.assertGreaterEqual(session.endpointer.start_frame * FRAME_MS, 2000)

    def test_speech_since_hears_the_owner_but_not_the_room(self) -> None:
        session = self.session(noise(300, ROOM) + tone(200, VOICE) + noise(3000, ROOM))
        for _ in range(4):
            session.poll()
        self.assertTrue(session.speech_since(250))
        session = self.session(tone(200, VOICE) + noise(3000, ROOM))
        for _ in range(4):
            session.poll()
        self.assertFalse(session.speech_since(talk.WAKE_START_DEAF_MS))

    def test_nothing_said_ends_after_five_seconds(self) -> None:
        session = self.session(noise(9000, ROOM))
        self.assertEqual(self.through(session), talk.END_NO_SPEECH)
        self.assertEqual(session.endpointer.frames * FRAME_MS, round(talk.WAKE_NO_SPEECH_S * 1000 / FRAME_MS) * FRAME_MS)
        self.assertEqual(session.clip(), b"")

    def test_forget_drops_the_pre_roll_too(self) -> None:
        session = self.session(tone(900, VOICE) + noise(1500, ROOM))
        self.through(session)
        session.forget()
        self.assertEqual(session._preroll, b"")
        self.assertEqual(session.clip(), b"")

    def test_a_talk_key_talk_has_no_pre_roll(self) -> None:
        session = TalkSession(FakeStream(tone(900, VOICE) + noise(1500, ROOM)))
        self.through(session)
        self.assertFalse(session.clip().startswith(WAKE_WORDS[:3200]))


class ListenFromWakeTests(unittest.TestCase):
    def make(self, *, heard: Heard | None = None, ready: bool = True, missing_speech: str = ""):
        self.backend = FakeBackend(FakeStream(b""))
        self.client = FakeClient(heard or Heard(True, "Hey Jarvis, open Spotify.", ms=200))
        self.calls = SimpleNamespace(forward=0, before=0, statuses=[], submitted=[], looks=[], chimes=0, ended=[])
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        cfg = SimpleNamespace(talk=config.TalkConfig(enabled=True), data_dir=Path(folder))
        calls = self.calls

        def forward():
            calls.forward += 1

        def before():
            calls.before += 1

        def chime():
            calls.chimes += 1

        controller = talk_ui.TalkController(
            cfg, bring_forward=forward, before_talk=before,
            show_status=lambda text, tone, hud_text=None: calls.statuses.append((text, tone)),
            submit=calls.submitted.append, set_look=calls.looks.append, models_root=Path(folder),
            picked_mic=lambda: "", backend_loader=lambda: (self.backend, ""),
            client_factory=lambda _folder, _hints, _idle: self.client, audio_missing=lambda: "",
            speech_missing=lambda: missing_speech, threaded=False, stopkeys=FakeStopKeys(), chime=chime)
        controller._model_ready = lambda: ready
        controller.wakeTalkEnded.connect(calls.ended.append)
        self.addCleanup(controller.shutdown)
        return controller

    def through(self, controller: talk_ui.TalkController, polls: int = 1000) -> None:
        for _ in range(polls):
            if controller.state != talk_ui.LISTENING:
                return
            controller._poll()

    def test_hey_jarvis_starts_the_talk_on_the_listener_s_audio_and_the_wake_words_come_off(self) -> None:
        controller = self.make()
        stream = FakeStream(tone(900, VOICE) + noise(1500, ROOM))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO) as logs:
            self.assertTrue(controller.listen_from_wake(stream, preroll=WAKE_WORDS, device_name="Desk mic",
                                                        follows_default=True, speaking=True))
            self.assertEqual(controller.state, talk_ui.LISTENING)
            self.through(controller)
        self.assertEqual(self.backend.opened, [])                   # never a microphone of its own
        self.assertEqual((self.calls.forward, self.calls.before), (1, 1))
        self.assertEqual(self.calls.submitted, ["Open Spotify."])
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_REQUEST])
        self.assertEqual(self.client.clips, [len(WAKE_WORDS) + len(tone(900, VOICE)) + 200 * 32])
        self.assertTrue(stream.closed)                              # the hand-off ends with the talk
        self.assertTrue(any("after \"Hey Jarvis\"" in line for line in logs.output))
        self.assertFalse(any("Spotify" in line for line in logs.output))

    def test_nothing_heard_ends_quietly_and_counts_as_nothing(self) -> None:
        controller = self.make()
        controller.listen_from_wake(FakeStream(noise(9000, ROOM)), preroll=WAKE_WORDS, speaking=True)
        self.through(controller)
        self.assertEqual(self.calls.statuses[-1], (talk_ui.WAKE_NOTHING, hud.TONE_IDLE))
        self.assertEqual((self.calls.submitted, self.calls.ended), ([], [talk_ui.WAKE_HEARD_NOTHING]))
        self.assertEqual(self.client.clips, [])

    def test_only_the_wake_words_understood_is_nothing_too(self) -> None:
        controller = self.make(heard=Heard(True, "Hey Jarvis.", ms=100))
        controller.listen_from_wake(FakeStream(tone(600, VOICE) + noise(1500, ROOM)), preroll=WAKE_WORDS,
                                    speaking=True)
        self.through(controller)
        self.assertEqual(self.calls.statuses[-1], (talk_ui.WAKE_NOTHING, hud.TONE_IDLE))
        self.assertEqual((self.calls.submitted, self.calls.ended), ([], [talk_ui.WAKE_HEARD_NOTHING]))

    def test_the_chime_plays_only_when_nothing_was_said_and_is_never_the_request(self) -> None:
        controller = self.make()
        controller.listen_from_wake(FakeStream(noise(9000, ROOM)), preroll=WAKE_WORDS, speaking=False)
        self.assertTrue(controller._chime_timer.isActive())
        for _ in range(3):
            controller._poll()
        frames = controller._session.endpointer.frames
        controller._maybe_chime()
        self.assertEqual(self.calls.chimes, 1)
        self.assertEqual(controller._session.endpointer._deaf_until, frames + round(talk.CHIME_DEAF_MS / FRAME_MS))
        controller.cancel()

        controller = self.make()
        controller.listen_from_wake(FakeStream(tone(1500, VOICE)), preroll=WAKE_WORDS, speaking=False)
        for _ in range(3):
            controller._poll()
        controller._maybe_chime()
        self.assertEqual(self.calls.chimes, 0)                      # already speaking: no chime over the request
        controller.cancel()

        controller = self.make()
        controller.listen_from_wake(FakeStream(noise(9000, ROOM)), preroll=WAKE_WORDS, speaking=True)
        self.assertFalse(controller._chime_timer.isActive())        # still talking at the wake: no chime at all
        controller.cancel()

    def test_the_chime_check_after_the_talk_ended_does_nothing(self) -> None:
        controller = self.make()
        controller.listen_from_wake(FakeStream(noise(9000, ROOM)), preroll=WAKE_WORDS, speaking=False)
        controller.cancel()
        self.assertFalse(controller._chime_timer.isActive())
        controller._maybe_chime()
        self.assertEqual(self.calls.chimes, 0)

    def test_not_while_a_talk_is_under_way_nor_when_something_is_missing(self) -> None:
        controller = self.make()
        controller.toggle()
        self.assertEqual(controller.state, talk_ui.LISTENING)
        self.assertFalse(controller.listen_from_wake(FakeStream(b""), preroll=WAKE_WORDS))
        controller.cancel()
        controller = self.make(ready=False)
        stream = FakeStream(b"")
        self.assertFalse(controller.listen_from_wake(stream, preroll=WAKE_WORDS))
        self.assertIn("download the speech model", self.calls.statuses[-1][0])
        self.assertFalse(stream.closed)                              # the caller ends the hand-off
        self.assertEqual(self.calls.ended, [])

    def test_esc_cancels_it_and_a_stopped_stream_is_said(self) -> None:
        controller = self.make()
        stream = FakeStream(tone(3000, VOICE))
        controller.listen_from_wake(stream, preroll=WAKE_WORDS, speaking=True)
        controller._poll()
        self.assertTrue(controller.cancel())
        self.assertTrue(stream.closed)
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_CANCELLED])
        controller = self.make()
        controller.listen_from_wake(FakeStream(tone(3000, VOICE), fail_after=2), preroll=WAKE_WORDS, speaking=True)
        self.through(controller)
        self.assertEqual(self.calls.statuses[-1], (talk_ui.DEVICE_STOPPED, hud.TONE_WARN))
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_STOPPED])

    def test_the_talk_key_ends_it_with_what_was_said(self) -> None:
        controller = self.make(heard=Heard(True, "what's the time", ms=100))
        controller.listen_from_wake(FakeStream(noise(200, ROOM) + tone(4000, VOICE)), preroll=WAKE_WORDS,
                                    speaking=True)
        for _ in range(20):
            controller._poll()
        controller.toggle()
        self.assertEqual(self.calls.submitted, ["what's the time"])
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_REQUEST])

    def test_nothing_understood_after_a_wake_ends_quietly(self) -> None:
        controller = self.make(heard=Heard(True, "", ms=100))
        controller.listen_from_wake(FakeStream(tone(900, VOICE) + noise(1500, ROOM)), preroll=WAKE_WORDS,
                                    speaking=True)
        self.through(controller)
        self.assertEqual(self.calls.statuses[-1], (talk_ui.WAKE_NOTHING, hud.TONE_IDLE))
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_HEARD_NOTHING])

    def test_understanding_that_failed_is_said_and_counted_as_stopped(self) -> None:
        controller = self.make(heard=Heard(False, error="failed"))
        controller.listen_from_wake(FakeStream(tone(900, VOICE) + noise(1500, ROOM)), preroll=WAKE_WORDS,
                                    speaking=True)
        self.through(controller)
        self.assertEqual(self.calls.statuses[-1], (talk_ui.NOT_UNDERSTOOD, hud.TONE_WARN))
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_STOPPED])

    def test_a_talk_key_talk_after_a_wake_talk_is_its_own(self) -> None:
        controller = self.make(heard=Heard(True, "Jarvis, open Notion", ms=100))
        controller.listen_from_wake(FakeStream(tone(900, VOICE) + noise(1500, ROOM)), preroll=WAKE_WORDS,
                                    speaking=True)
        self.through(controller)
        self.backend.stream = FakeStream(tone(900, VOICE) + noise(1500, ROOM))
        controller.toggle()
        self.through(controller)
        self.assertEqual(self.calls.submitted, ["Open Notion", "Jarvis, open Notion"])   # only a wake talk strips
        self.assertEqual(self.calls.ended, [talk_ui.WAKE_REQUEST])

    def test_closing_the_app_mid_wake_talk_ends_the_hand_off(self) -> None:
        controller = self.make()
        stream = FakeStream(tone(3000, VOICE))
        controller.listen_from_wake(stream, preroll=WAKE_WORDS, speaking=False)
        controller.shutdown()
        self.assertTrue(stream.closed)
        self.assertFalse(controller._chime_timer.isActive())


if __name__ == "__main__":
    unittest.main()
