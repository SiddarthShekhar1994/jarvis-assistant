"""An Ask's first words in the app: the caption shows them as they are written, the voice starts as soon as the say is
whole (it does not wait for the cards' lines), and the final validated plan stays the source of truth; the spoken
acknowledgement never delays the reply; Stop, Turn off and a new request stop the words at once; on the desktop HUD
the words are the caption line; Claude Code started ahead from the controller, killed at quit.

AppController offscreen with the real planner over tests.ask_fakes' runner: claude.exe is never started.
"""

from __future__ import annotations

import json
import threading
import unittest
from typing import Any
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import ask_ui, home, hud, orb, ui
from briefing_reader.actions import SOURCE_ASK
from briefing_reader.ask import planner as ask_planner
from briefing_reader.ask.prestart import SLOT_ASK, SLOT_DESKTOP, Prestarter
from briefing_reader.speech import KIND_REPLY
from briefing_reader.voice_ui import AssistantVoice
from tests.ask_fakes import (
    FakeProcess,
    FakeReader,
    StagedProcess,
    gmail_message,
    gmail_thread,
    plan_stream,
    result_event,
    tool_chunks,
    words_stream,
)
from tests.test_desktop_hud import HudApp
from tests.test_voice_ui import FakeUtterancePlayer, FakeWorker, audio
from tests.ui_fakes import COMMAND, MOVE_LINE, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
SAY = "Right, sir. Friday has your Jarvis test sync at two. It is waiting for your OK."
PLAN = {"say": SAY, "question": "", "lines": [MOVE_LINE]}
SIZE = 8


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_ask_words_app", "-platform", "offscreen"])
    fonts()
    ask_planner.cli.forget_probes()


def chunk_line(plan: dict, words: int | None) -> int:
    """The line (of words_stream(plan, size=SIZE)) right after the chunk that ends the say's first ``words`` words
    (None: the whole say, its closing quote)."""
    say = plan["say"]
    encoded = json.dumps(say)[1:-1]
    start = len('{"say": "')
    if words is None:
        offset = start + len(encoded)
    else:
        at = -1
        for _ in range(words):
            at = encoded.index(" ", at + 1)
        offset = start + at
    return 3 + offset // SIZE + 1


def staged(plan: dict = PLAN, *, first_words: int = 3) -> tuple[StagedProcess, threading.Event, threading.Event,
                                                                threading.Event]:
    """A run stopped after the say's first words, after the whole say, and before the whole message: three gates."""
    lines = words_stream(plan, size=SIZE)
    first, whole, rest = threading.Event(), threading.Event(), threading.Event()
    stops = {chunk_line(plan, first_words): first, chunk_line(plan, None): whole, len(lines) - 3: rest}
    return StagedProcess(lines, stops), first, whole, rest


def submit(c: ui.AppController, text: str = COMMAND) -> None:
    c.window.reading.command_bar.set_text(text)
    c.window.reading.command_bar.input.returnPressed.emit()
    settle()


class WordsTestCase(unittest.TestCase):
    def make(self, *runs: Any, **kwargs: Any) -> AppHarness:
        app = AppHarness(self, runs=runs, **kwargs)
        app.reading(autoplay=False)
        app.c.ask.start()
        self.assertTrue(wait_for(lambda: app.c.ask.ready is not None), "readiness checked")
        self.app, self.c = app, app.c
        self.reading = app.c.window.reading
        self.captions = self.reading.captions
        self.voice = app.voice
        return app

    def gates(self, *gates: threading.Event) -> None:
        for gate in gates:
            self.addCleanup(gate.set)

    def replies(self) -> list[tuple[str, str]]:
        return [(text, key) for kind, text, key in self.voice.said if kind == KIND_REPLY]

    def ask_cards(self) -> list[Any]:
        return [action for action in self.c._actions if action.source == SOURCE_ASK]

    def done(self) -> None:
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()


class CaptionAndVoiceTests(WordsTestCase):
    def test_words_then_voice_at_say_complete_then_cards(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        self.assertTrue(wait_for(lambda: self.captions.page_text().startswith("Right, sir.")), "the first words show")
        self.assertEqual(self.captions.state(), home.SPEAKING)
        self.assertTrue(self.captions.waits_for_words())
        self.assertEqual(self.replies(), [])                         # the say is not whole: not said yet
        self.assertTrue(self.c.ask.busy)
        self.assertEqual(self.reading.hero.state(), orb.THINKING)    # it is still thinking
        first.set()
        whole.set()                                                  # the say is whole; the cards' lines are not
        self.assertTrue(wait_for(lambda: bool(self.replies())), "the voice starts at say-complete")
        self.assertEqual(self.replies(), [(SAY, ui.ASK_SAY_KEY)])
        self.assertTrue(self.c.ask.busy)
        self.assertEqual(self.ask_cards(), [])                       # it did not wait for them
        self.assertEqual(self.captions.full_text(), SAY)
        rest.set()
        self.done()
        self.assertEqual(self.replies(), [(SAY, ui.ASK_SAY_KEY)])    # said once: the final plan is what was written
        self.assertEqual(len(self.ask_cards()), 1)
        self.assertEqual(self.captions.full_text(), SAY)
        self.assertEqual(self.reading.conversation.entries()[-1][2], SAY)
        self.assertEqual(self.reading.command_bar.status(), SAY)

    def test_when_the_voice_begins_nothing_flickers_or_is_typed_again(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: bool(self.replies())))
        page, revealed = self.captions.page_index(), list(self.captions._revealed_at)
        states: list[str] = []
        self.captions.stateChanged.connect(states.append)
        self.c._on_voice_speaking(SAY)                               # the voice's speakingChanged
        self.assertEqual((self.captions.page_index(), self.captions._revealed_at), (page, revealed))
        self.assertEqual(states, [])
        self.assertFalse(self.captions.waits_for_words())
        self.c._on_voice_speaking("")                                # it ended: the line holds, then rests
        self.assertEqual(self.captions.state(), home.HOLDING)
        rest.set()
        self.done()

    def test_another_utterance_leaves_the_words_alone(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        self.assertTrue(wait_for(self.captions.waits_for_words))
        line = self.captions.full_text()
        self.c._on_voice_speaking("One moment, sir.")
        self.assertEqual(self.captions.full_text(), line)
        self.c._on_voice_speaking("")
        self.assertTrue(self.captions.waits_for_words())
        first.set()
        whole.set()
        rest.set()
        self.done()

    def test_a_final_say_that_differs_corrects_the_caption_and_the_voice(self) -> None:
        wrote = tool_chunks(json.dumps({"say": "Friday has your sync at two.", "lines": []}), size=SIZE)
        final = {"say": "Thursday has your sync at two.", "question": "", "lines": []}
        lines = plan_stream(final)
        self.make([lines[0], *wrote, *lines[1:]])
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [("Friday has your sync at two.", ui.ASK_SAY_KEY),
                                          ("Thursday has your sync at two.", "")])
        self.assertIn(ui.ASK_SAY_KEY, self.voice.stopped_keys)
        self.assertEqual(self.captions.full_text(), "Thursday has your sync at two.")
        self.assertEqual(self.reading.conversation.entries()[-1][2], "Thursday has your sync at two.")

    def test_with_nothing_spoken_a_retry_with_other_words_still_reaches_the_band(self) -> None:
        import dataclasses

        first = tool_chunks(json.dumps({"say": "Friday has your sync at two.", "lines": []}), size=SIZE)
        final = {"say": "Thursday has your sync at two.", "question": "", "lines": []}
        lines = words_stream(final, size=SIZE)
        self.make([lines[0], *first, *lines[1:]])
        self.c.config = dataclasses.replace(self.c.config, assistant=dataclasses.replace(self.c.config.assistant,
                                                                                         speak_replies=False))
        submit(self.c)
        self.done()
        self.assertEqual(self.voice.said, [])
        self.assertEqual(self.captions.full_text(), "Thursday has your sync at two.")

    def test_the_tool_called_again_with_other_words_starts_over(self) -> None:
        first = tool_chunks(json.dumps({"say": "Friday has your sync at two.", "lines": []}), size=SIZE)
        final = {"say": "Thursday has your sync at two.", "question": "", "lines": []}
        lines = words_stream(final, size=SIZE)
        self.make([lines[0], *first, *lines[1:]])
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [("Friday has your sync at two.", ui.ASK_SAY_KEY),
                                          ("Thursday has your sync at two.", ui.ASK_SAY_KEY)])
        self.assertEqual(self.voice.stopped_keys, [ui.ASK_SAY_KEY])  # what it said first goes
        self.assertEqual(self.captions.full_text(), "Thursday has your sync at two.")

    def test_the_same_words_written_twice_are_said_once(self) -> None:
        lines = words_stream(PLAN, size=SIZE)
        self.make([lines[0], *tool_chunks(json.dumps(PLAN), size=SIZE), *lines[1:]])
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [(SAY, ui.ASK_SAY_KEY)])

    def test_a_run_that_fails_after_its_words_is_corrected_in_coral(self) -> None:
        lines = words_stream(PLAN, size=SIZE)
        failed = json.dumps(result_event(subtype="error_during_execution", is_error=True, result="boom"))
        self.make([*lines[:chunk_line(PLAN, None) + 1], failed])
        submit(self.c)
        self.done()
        spoken = "Sorry, sir - something went wrong. Nothing was proposed."
        self.assertEqual(self.replies(), [(SAY, ui.ASK_SAY_KEY), (spoken, "")])
        self.assertIn(ui.ASK_SAY_KEY, self.voice.stopped_keys)
        self.assertTrue(self.captions.is_alert())
        self.assertEqual(self.captions.full_text(), spoken)
        self.assertEqual(self.reading.command_bar.tone(), hud.TONE_ERROR)
        self.assertEqual(self.reading.conversation.entry_widgets()[-1].tone(), hud.TONE_ERROR)
        self.assertEqual(self.ask_cards(), [])

    def test_a_process_that_dies_mid_words_is_corrected_too(self) -> None:
        lines = words_stream(PLAN, size=SIZE)
        self.make(lines[:chunk_line(PLAN, 3) + 1])
        submit(self.c)
        self.done()
        self.assertTrue(self.captions.is_alert())
        self.assertNotIn("Right", self.captions.full_text())
        self.assertEqual(self.reading.command_bar.tone(), hud.TONE_ERROR)

    def test_the_question_is_said_after_the_say(self) -> None:
        self.make(words_stream({"say": "Friday looks clear, sir.", "question": "Which Friday do you mean?", "lines": []},
                               size=SIZE))
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [("Friday looks clear, sir.", ui.ASK_SAY_KEY),
                                          ("Which Friday do you mean?", ui.ASK_SAY_KEY)])

    def test_a_say_that_claims_something_was_done_never_shows_or_is_said_as_written(self) -> None:
        self.make(words_stream({"say": "Done - I've moved Jarvis test sync to Friday.", "question": "",
                                "lines": [MOVE_LINE]}, size=SIZE))
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [("I've lined up one proposal for your OK, sir.", "")])
        self.assertNotIn("moved", self.captions.full_text())

    def test_a_say_that_repeats_a_note_waits_for_the_final_words(self) -> None:
        say = "Ana is the project lead for Northwind, sir."
        self.make(words_stream({"say": say, "question": "", "lines": []}, size=SIZE))
        self.c._memory_spoken_guard = lambda: ("Ana is the project lead for Northwind",)
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [("Here's what I found, sir.", "")])
        self.assertEqual(self.captions.full_text(), "Here's what I found, sir.")

    def test_a_stream_with_no_partial_events_is_as_before(self) -> None:
        self.make(plan_stream(PLAN))
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [(SAY, "")])
        self.assertFalse(self.captions.streamed())

    def test_muted_the_words_show_at_the_muted_pace_and_nothing_is_said(self) -> None:
        self.make(words_stream({"say": SAY, "question": "Which Friday?", "lines": [MOVE_LINE]}, size=SIZE))
        self.voice.set_muted(True)
        submit(self.c)
        self.done()
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.captions.full_text(), f"{SAY} Which Friday?")
        self.assertFalse(self.captions.waits_for_words())

    def test_a_voice_that_fails_lets_the_caption_finish(self) -> None:
        self.make(words_stream(PLAN, size=SIZE))
        self.voice.speaks = False
        submit(self.c)
        self.done()
        self.assertFalse(self.captions.waits_for_words())
        self.assertEqual(self.captions.full_text(), SAY)

    def test_speak_replies_off_shows_the_words_and_says_nothing(self) -> None:
        import dataclasses

        wrote = tool_chunks(json.dumps({"say": "Friday has your sync.", "lines": []}), size=SIZE)
        lines = plan_stream({"say": "Thursday has your sync.", "question": "", "lines": []})
        self.make([lines[0], *wrote, *lines[1:]])
        self.c.config = dataclasses.replace(self.c.config, assistant=dataclasses.replace(self.c.config.assistant,
                                                                                         speak_replies=False))
        submit(self.c)
        self.done()
        self.assertEqual(self.voice.said, [])
        self.assertEqual(self.captions.full_text(), "Thursday has your sync.")   # corrected all the same
        self.assertFalse(self.captions.waits_for_words())

    def test_a_meetings_summary_starting_leaves_the_last_answer_alone(self) -> None:
        self.make(words_stream(PLAN, size=SIZE))
        submit(self.c)
        self.done()
        self.assertTrue(self.captions.streamed())
        with mock.patch.object(type(self.c.ask), "running_mode", new_callable=mock.PropertyMock, return_value=ui.MODE_MEETING):
            self.c._on_ask_started(self.c.ask._seq + 1, "summarise")
        self.assertEqual(self.voice.stopped_keys, [])
        self.assertTrue(self.captions.streamed())

    def test_the_logs_never_hold_the_words(self) -> None:
        self.make(words_stream(PLAN, size=SIZE))
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            submit(self.c)
            self.done()
        text = "\n".join(logs.output)
        self.assertIn("first words", text)
        for secret in (SAY, "Friday has", "Right, sir", COMMAND):
            self.assertNotIn(secret, text)


class ThroughTheRealVoiceTests(WordsTestCase):
    """voice_ui.AssistantVoice over its fake worker and player: the utterances as the app really gets them."""

    def real_voice(self) -> None:
        self.worker, self.player = FakeWorker(), FakeUtterancePlayer()
        voice = AssistantVoice(worker=self.worker, player=self.player, briefing_player=lambda: None)
        voice.speakingChanged.connect(self.c._on_voice_speaking)
        self.addCleanup(voice.shutdown)
        self.c.voice = self.real = voice

    def audio_for(self, text: str) -> int:
        seq = next(seq for seq, said in self.worker.submitted if said == text)
        self.real.on_audio(seq, audio(seq), None)
        return seq

    def test_the_voice_takes_the_line_over_and_says_it_once(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        self.real_voice()
        submit(self.c)
        self.assertTrue(wait_for(self.captions.waits_for_words))
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: bool(self.worker.submitted)), "it is on its way to the voice")
        self.assertEqual([text for _seq, text in self.worker.submitted], [SAY])
        self.assertTrue(self.c.ask.busy)                             # before the cards' lines
        self.assertTrue(self.captions.waits_for_words())
        revealed = list(self.captions._revealed_at)
        page = self.captions.page_index()
        seq = self.audio_for(SAY)
        self.assertEqual(self.player.played, [seq])
        self.assertEqual(self.c._utterance, SAY)
        self.assertEqual((self.captions.page_index(), self.captions._revealed_at[:len(revealed)]), (page, revealed))
        self.assertFalse(self.captions.waits_for_words())
        rest.set()
        self.done()
        self.assertEqual([text for _seq, text in self.worker.submitted], [SAY])   # never said again
        self.assertEqual(self.player.played, [seq])
        self.player.end()
        self.assertEqual(self.c._utterance, "")
        self.assertIn(self.captions.state(), (home.HOLDING, home.SPEAKING))
        self.assertEqual(self.captions.full_text(), SAY)

    def test_stop_ends_the_voice_there_and_then(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        self.real_voice()
        submit(self.c)
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: bool(self.worker.submitted)))
        self.audio_for(SAY)
        self.assertTrue(self.player.playing)
        self.reading.command_bar.button.click()
        self.assertFalse(self.player.playing)
        self.assertEqual(self.c._utterance, "")
        self.assertFalse(self.captions.streamed())
        self.done()
        self.assertEqual(self.reading.conversation.entries()[-1][2], ui.ASK_CANCELLED_ENTRY)

    def test_stop_before_its_audio_came_nothing_is_said(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        self.real_voice()
        submit(self.c)
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: bool(self.worker.submitted)))
        self.reading.command_bar.button.click()
        seq = self.worker.submitted[0][0]
        self.assertIn(seq, self.worker.forgotten)
        self.real.on_audio(seq, audio(seq), None)                    # it arrives too late
        self.assertEqual(self.player.played, [])
        self.done()

    def test_an_ack_already_playing_gives_way_to_the_answer(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        gate = threading.Event()
        self.gates(gate)
        process.stops[1] = gate                                      # no words yet: a slow request
        self.make(process)
        self.real_voice()
        with mock.patch.object(ui, "ASK_ACK_HOLD_MS", 1):
            submit(self.c)
            self.assertTrue(wait_for(lambda: bool(self.worker.submitted)), "the acknowledgement")
        ack_text = self.worker.submitted[0][1]
        self.assertIn(ack_text, ("Right away, sir.", "One moment, sir.", "On it, sir.", "Let me see, sir."))
        ack = self.audio_for(ack_text)
        self.assertEqual(self.player.played, [ack])
        self.assertEqual(self.captions.full_text(), ack_text)        # his words on the band, as always
        gate.set()
        first.set()
        self.assertTrue(wait_for(self.captions.waits_for_words))     # the answer's words replace them ...
        self.assertTrue(self.captions.full_text().startswith("Right, sir."))
        whole.set()
        self.assertTrue(wait_for(lambda: len(self.worker.submitted) == 2))
        self.assertEqual(self.player.played, [ack])                  # its audio is not here: nothing to say yet
        revealed = list(self.captions._revealed_at)
        seq = self.audio_for(SAY)
        self.assertEqual(self.player.played, [ack, seq])             # ... and the ack never delays their voice
        self.assertEqual(self.captions._revealed_at[:len(revealed)], revealed)
        self.assertEqual(self.c._utterance, SAY)
        rest.set()
        self.done()


class SecondRunTests(WordsTestCase):
    def make_mail(self, *runs: Any) -> None:
        thread = gmail_thread("thr0000777", [gmail_message(
            "msg0000777", sender="Sam Example <sam.example@example.com>", to="me@example.edu",
            subject="Budget", body="Can you confirm the budget by Friday?", msgid="<CAB7@mail.example.edu>",
            at_ms=1791200000000)])
        self.make(*runs, readers={"work": FakeReader("work", {"thr0000777": thread}, found=["thr0000777"])})

    SEARCH = {"say": "Let me look for that email, sir.", "question": "", "lines": [],
              "gmail_search": {"account": "work", "query": "from:sam subject:budget", "why": "the budget"}}

    def test_a_second_run_streams_its_own_say_the_same_way(self) -> None:
        answer = {"say": "Sam wants it confirmed by Friday, sir.", "question": "", "lines": []}
        self.make_mail(words_stream(self.SEARCH, size=SIZE), words_stream(answer, size=SIZE))
        submit(self.c, "what did Sam say in the budget email")
        self.done()
        self.assertEqual(self.replies(), [("Let me look for that email, sir.", ui.ASK_SAY_KEY),
                                          ("Sam wants it confirmed by Friday, sir.", ui.ASK_SAY_KEY)])
        self.assertEqual(self.voice.stopped_keys, [])                # the first run's words were right when said
        self.assertEqual(self.captions.full_text(), "Sam wants it confirmed by Friday, sir.")
        self.assertEqual(self.reading.conversation.entries()[-1][2], "Sam wants it confirmed by Friday, sir.")

    def test_an_email_is_never_shown_or_said_as_it_is_written(self) -> None:
        quote = 'Sam wrote: "Can you confirm the budget by Friday please?" He is waiting, sir.'
        answer = {"say": quote, "question": "", "lines": []}
        seen: list[str] = []
        self.make_mail(words_stream(self.SEARCH, size=SIZE), words_stream(answer, size=SIZE))
        original = self.captions.stream

        def recording(text: str, **kwargs: Any) -> None:
            seen.append(text)
            original(text, **kwargs)

        self.captions.stream = recording
        submit(self.c, "what did Sam say in the budget email")
        self.done()
        self.assertFalse(any("confirm the budget" in text for text in seen), seen)
        self.assertEqual(self.replies()[-1], ("He is waiting, sir.", ui.ASK_SAY_KEY))

    def test_an_answer_that_is_the_first_runs_say_is_said_once(self) -> None:
        self.make(words_stream(self.SEARCH, size=SIZE),
                  readers={"work": FakeReader("work", found=[])})             # nothing found: no second run
        submit(self.c, "what did Sam say in the budget email")
        self.done()
        self.assertEqual(self.replies(), [("Let me look for that email, sir.", ui.ASK_SAY_KEY)])


class AckTests(WordsTestCase):
    def test_the_thinking_state_starts_at_once_and_a_slow_request_gets_its_ack(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        gate = threading.Event()
        self.gates(gate)
        process.stops[1] = gate                                      # no words yet
        self.make(process)
        with mock.patch.object(ui, "ASK_ACK_HOLD_MS", 1000):
            submit(self.c)
            self.assertTrue(wait_for(lambda: self.c._ask_busy))
            self.assertEqual(self.reading.hero.state(), orb.THINKING)   # at once
            self.assertNotIn("ack", self.voice.kinds())              # held
            self.assertTrue(self.c._ack_timer.isActive())
            self.assertTrue(wait_for(lambda: "ack" in self.voice.kinds()))
        self.assertEqual(self.voice.said[0][2], ui.ASK_ACK_KEY)
        gate.set()
        first.set()
        self.assertTrue(wait_for(lambda: ui.ASK_ACK_KEY in self.voice.cancelled), "the words drop one still waiting")
        whole.set()
        rest.set()
        self.done()
        self.assertEqual(self.voice.kinds(), ["ack", "reply"])

    def test_first_words_within_the_hold_skip_it(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        with mock.patch.object(ui, "ASK_ACK_HOLD_MS", 1000):
            submit(self.c)
            self.assertTrue(wait_for(lambda: self.captions.waits_for_words()))
            self.assertFalse(self.c._ack_timer.isActive())
            QTest.qWait(1100)
        self.assertNotIn("ack", self.voice.kinds())
        first.set()
        whole.set()
        rest.set()
        self.done()
        self.assertEqual(self.voice.kinds(), ["reply"])

    def test_an_answer_within_the_hold_skips_it(self) -> None:
        self.make(plan_stream(PLAN))
        with mock.patch.object(ui, "ASK_ACK_HOLD_MS", 1000):
            submit(self.c)
            self.done()
            QTest.qWait(1100)
        self.assertEqual(self.voice.kinds(), ["reply"])

    def test_the_default_hold(self) -> None:
        self.assertEqual(ui.ASK_ACK_HOLD_MS, 1500)

    def test_a_cancel_within_the_hold_skips_it(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        gate = threading.Event()
        self.gates(gate)
        process.stops[1] = gate
        self.make(process)
        with mock.patch.object(ui, "ASK_ACK_HOLD_MS", 1000):
            submit(self.c)
            self.assertTrue(wait_for(lambda: self.c._ask_busy))
            self.reading.command_bar.button.click()
            QTest.qWait(1100)
        self.assertNotIn("ack", self.voice.kinds())
        self.assertFalse(self.c._ack_timer.isActive())
        gate.set()
        self.done()


class StopTests(WordsTestCase):
    def late_words(self, seq: int) -> None:
        self.c.ask._bridge.words.emit(seq, 1, "Late words that must not show ", False, False)
        settle()

    def test_stop_ends_the_words_at_once_and_nothing_shows_afterwards(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        self.assertTrue(wait_for(self.captions.waits_for_words))
        seq = self.c.ask._running.seq
        self.reading.command_bar.button.click()                      # Stop on the bar
        self.assertEqual(self.voice.stopped_keys, [ui.ASK_SAY_KEY])
        self.assertFalse(self.captions.streamed())                   # at once: before the run has ended
        self.assertTrue(self.c.ask.busy)
        self.late_words(seq)
        self.assertFalse(self.captions.streamed())
        self.done()
        self.late_words(seq)
        self.assertFalse(self.captions.streamed())
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.reading.conversation.entries()[-1][2], ui.ASK_CANCELLED_ENTRY)
        self.assertTrue(process.killed)

    def test_stop_while_the_say_is_being_said_stops_the_voice(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: bool(self.replies())))
        self.c._on_voice_speaking(SAY)
        self.reading.command_bar.button.click()
        self.assertEqual(self.voice.stopped_keys, [ui.ASK_SAY_KEY])
        self.assertFalse(self.captions.streamed())
        self.done()
        self.assertEqual(self.reading.conversation.entries()[-1][2], ui.ASK_CANCELLED_ENTRY)
        self.assertEqual(self.ask_cards(), [])

    def test_a_new_request_stops_what_the_last_answer_was_still_saying(self) -> None:
        second, first, whole, rest = staged({"say": "Saturday is clear, sir.", "question": "", "lines": []}, first_words=2)
        no_words_yet = threading.Event()
        second.stops[1] = no_words_yet
        self.gates(first, whole, rest, no_words_yet)
        self.make(words_stream(PLAN, size=SIZE), second)
        submit(self.c)
        self.done()
        old_seq = self.c.ask._seq
        self.assertTrue(self.captions.streamed())
        submit(self.c, "and Saturday?")
        self.assertEqual(self.voice.stopped_keys, [ui.ASK_SAY_KEY])  # the last answer's voice goes at once
        self.assertFalse(self.captions.streamed())
        self.late_words(old_seq)                                     # what the last run still had on its way
        self.assertFalse(self.captions.streamed())
        no_words_yet.set()
        self.assertTrue(wait_for(lambda: self.captions.page_text().startswith("Saturday")))
        first.set()
        whole.set()
        rest.set()
        self.done()
        self.assertEqual(self.replies()[-1], ("Saturday is clear, sir.", ui.ASK_SAY_KEY))

    def test_jarvis_closing_shows_nothing_more(self) -> None:
        process, first, whole, rest = staged()
        self.gates(first, whole, rest)
        self.make(process)
        submit(self.c)
        self.assertTrue(wait_for(self.captions.waits_for_words))
        seq = self.c.ask._running.seq
        self.c.ask.shutdown()
        line = self.captions.full_text()
        self.late_words(seq)
        self.assertEqual(self.captions.full_text(), line)


class HudWordsTests(HudApp):
    def setUp(self) -> None:
        ask_planner.cli.forget_probes()

    def make_ask(self, *runs: Any) -> None:
        self.make(runs=runs)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None), "readiness checked")
        settle()

    def type_on_hud(self, text: str = COMMAND) -> None:
        bar = self.hud().bar
        QTest.mouseClick(bar.input, Qt.MouseButton.LeftButton)
        QTest.keyClicks(bar.input, text)
        QTest.keyClick(bar.input, Qt.Key.Key_Return)
        settle()

    def test_the_words_are_the_caption_line_and_a_plain_answer_stays(self) -> None:
        plan = {"say": SAY, "question": "", "lines": []}
        process, first, whole, rest = staged(plan)
        for gate in (first, whole, rest):
            self.addCleanup(gate.set)
        self.make_ask(process)
        self.enter()
        self.type_on_hud("what's on Friday?")
        self.assertTrue(wait_for(lambda: self.hud().caption_text().startswith("Right, sir.")), self.hud().caption_text())
        shown = self.hud().caption_text()
        self.assertTrue(SAY.startswith(shown))
        first.set()
        whole.set()
        self.assertTrue(wait_for(lambda: self.hud().caption_text() == SAY))
        self.assertTrue(self.c.ask.busy)
        QTest.qWait(1100)                                            # the seconds tick does not bring "Planning..." back
        self.assertEqual(self.hud().caption_text(), SAY)
        rest.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertEqual(self.hud().caption_text(), SAY)             # the answer stays where it was read

    def test_proposals_say_where_they_are(self) -> None:
        self.make_ask(words_stream(PLAN, size=SIZE))
        self.enter()
        self.type_on_hud("move my sync")
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertEqual(self.hud().caption_text(), ask_ui.with_address(ask_ui.HUD_IN_APPROVALS, "sir"))

    def test_a_question_back_is_in_the_window(self) -> None:
        self.make_ask(words_stream({"say": "Friday looks clear, sir.", "question": "Which one?", "lines": []}, size=SIZE))
        self.enter()
        self.type_on_hud("what's on Friday?")
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertEqual(self.hud().caption_text(), ask_ui.with_address(ask_ui.HUD_IN_WINDOW, "sir"))

    def test_a_final_say_that_differs_is_what_stays(self) -> None:
        wrote = tool_chunks(json.dumps({"say": "Friday has your sync.", "lines": []}), size=SIZE)
        lines = plan_stream({"say": "Thursday has your sync.", "question": "", "lines": []})
        self.make_ask([lines[0], *wrote, *lines[1:]])
        self.enter()
        self.type_on_hud("what's on Friday?")
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertEqual(self.hud().caption_text(), "Thursday has your sync.")

    def test_a_failure_after_words_is_coral(self) -> None:
        lines = words_stream(PLAN, size=SIZE)
        failed = json.dumps(result_event(subtype="error_during_execution", is_error=True, result="boom"))
        self.make_ask([*lines[:chunk_line(PLAN, None) + 1], failed])
        self.enter()
        self.type_on_hud("what's on Friday?")
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertNotIn("Right", self.hud().caption_text())
        self.assertEqual(self.hud().bar.tone(), hud.TONE_ERROR)

    def test_stop_takes_the_words_away(self) -> None:
        process, first, whole, rest = staged()
        for gate in (first, whole, rest):
            self.addCleanup(gate.set)
        self.make_ask(process)
        self.enter()
        self.type_on_hud("what's on Friday?")
        self.assertTrue(wait_for(lambda: self.hud().caption_text().startswith("Right, sir.")))
        QTest.keyClick(self.hud().bar.input, Qt.Key.Key_Escape)
        settle()
        self.assertEqual(self.hud().caption_text(), ask_ui.CANCELLING_TEXT)
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertNotIn("Right", self.hud().caption_text())

    def test_turn_off_stops_the_words_at_once(self) -> None:
        process, first, whole, rest = staged()
        for gate in (first, whole, rest):
            self.addCleanup(gate.set)
        self.make_ask(process)
        submit(self.c)
        captions = self.reading.captions
        self.assertTrue(wait_for(captions.waits_for_words))
        seq = self.c.ask._running.seq
        self.c._on_desktop_turn_off()
        voice = self.harness.voice
        self.assertEqual(voice.stopped_keys, [ui.ASK_SAY_KEY])
        self.assertFalse(captions.streamed())
        self.assertTrue(self.c.ask._running.cancel_requested)
        self.c.ask._bridge.words.emit(seq, 1, "Late words that must not show ", False, False)
        settle()
        self.assertFalse(captions.streamed())
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle()
        self.assertTrue(process.killed)
        self.assertNotIn(KIND_REPLY, voice.kinds())


class AheadFromTheControllerTests(WordsTestCase):
    def seamed(self, *runs: Any, **kwargs: Any) -> Prestarter:
        """An app whose planner has the seam, as default_planner's has (over the fake runner)."""
        app = AppHarness(self, runs=runs, **kwargs)
        app.reading(autoplay=False)
        engine = app.factory.planner.engine
        self.pool = Prestarter(engine.runner)
        engine.prestart = self.pool
        self.app, self.c = app, app.c
        self.reading, self.voice, self.captions = app.c.window.reading, app.voice, app.c.window.reading.captions
        self.runner = engine.runner
        return self.pool

    def test_started_once_ask_is_ready_used_replaced_and_killed_at_quit(self) -> None:
        idle, spare = FakeProcess(words_stream(PLAN, size=SIZE)), FakeProcess([])
        pool = self.seamed(idle, spare)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: pool.holds(SLOT_ASK)), "started ahead after the readiness check")
        self.assertEqual(self.runner.started[0].raw, "")
        self.assertEqual(idle.sent, [])
        self.assertTrue(self.c.ask._sweep.isActive())
        submit(self.c, "what's on Friday?")
        self.done()
        self.assertEqual(len(idle.sent), 1)
        self.assertEqual(self.replies(), [(SAY, ui.ASK_SAY_KEY)])
        self.assertTrue(wait_for(lambda: pool.holds(SLOT_ASK)))      # its replacement waits
        self.assertEqual(spare.sent, [])
        self.c.ask.shutdown()                                        # the app's quit path (AppController.shutdown)
        self.assertTrue(spare.killed and spare.closed)
        self.assertFalse(self.c.ask._sweep.isActive())
        self.assertFalse(pool.holds(SLOT_ASK))

    def test_the_apps_shutdown_kills_what_waits(self) -> None:
        idle = FakeProcess([])
        pool = self.seamed(idle)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: pool.holds(SLOT_ASK)))
        with mock.patch.object(ui, "_remove_audio_dir_after"):
            self.c.shutdown()
        self.assertTrue(idle.killed and idle.closed)
        self.assertFalse(pool.holds(SLOT_ASK))
        self.assertFalse(pool.fill(SLOT_ASK, ["claude.exe"], {}, "x"))   # nothing starts after it

    def test_not_started_while_ask_is_off(self) -> None:
        app = AppHarness(self, ask=False)
        app.reading(autoplay=False)
        self.assertIsNone(app.c.ask)
        self.assertIsNone(app.factory.runner)                        # the planner was never built

    def test_not_started_when_not_ready(self) -> None:
        auth = json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})
        pool = self.seamed(FakeProcess([]), auth=auth)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None))
        settle()
        self.assertFalse(pool.holds(SLOT_ASK))
        self.assertEqual(self.runner.started, [])

    def test_turning_desktop_off_lets_its_process_go(self) -> None:
        idle, desktop = FakeProcess([]), FakeProcess([])
        pool = self.seamed(idle, desktop)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: pool.holds(SLOT_ASK)))
        planner = self.app.factory.planner
        pool.fill(SLOT_DESKTOP, ["claude.exe", "--input-format"], {}, "x")
        self.assertTrue(pool.holds(SLOT_DESKTOP))
        self.c.ask.forget_desktop_ahead()
        self.assertTrue(wait_for(lambda: not pool.holds(SLOT_DESKTOP)))
        self.assertTrue(desktop.killed)
        self.assertTrue(pool.holds(SLOT_ASK))
        self.assertIs(planner.engine.prestart, pool)

    def test_the_sweep_goes_to_the_ask_thread(self) -> None:
        pool = self.seamed(FakeProcess([]), FakeProcess([]))
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: pool.holds(SLOT_ASK)))
        with mock.patch.object(pool, "sweep") as sweep:
            self.c.ask._on_sweep()
            self.assertTrue(wait_for(lambda: sweep.called))

    def test_typing_reads_ahead_at_most_every_so_often(self) -> None:
        self.seamed(FakeProcess([]), FakeProcess([]))
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None))
        planner = self.app.factory.planner
        with mock.patch.object(planner, "read_ahead") as read:
            self.c.ask._on_typing(True)
            self.assertTrue(wait_for(lambda: read.call_count == 1))
            self.c.ask._on_typing(False)
            self.c.ask._on_typing(True)
            settle()
            QTest.qWait(30)
            self.assertEqual(read.call_count, 1)
            self.c.ask._read_ahead_at -= ask_ui.READ_AHEAD_EVERY_S + 1
            self.c.ask._on_typing(True)
            self.assertTrue(wait_for(lambda: read.call_count == 2))

    def test_without_the_seam_typing_reads_nothing(self) -> None:
        self.make()
        planner = self.app.factory.planner
        with mock.patch.object(planner, "read_ahead") as read:
            self.c.ask._on_typing(True)
            QTest.qWait(30)
        read.assert_not_called()

    def test_the_apps_planner_has_the_seam(self) -> None:
        app = AppHarness(self, ask=False)
        planner = ask_ui.default_planner(app.config, {}, {}, {})
        self.assertIsInstance(planner.engine.prestart, Prestarter)
        self.assertIs(planner.engine.prestart._runner, planner.engine.runner)
        self.assertTrue(any(path.name == "config.toml" for path in planner.engine.prestart._extra))
        self.assertIs(planner.streams_words, True)
