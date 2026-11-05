"""An Ask's words as they are written: the tolerant extractor (stream.SayWatch), the run's new stdin and output
(stream.run_cli with --input-format stream-json and --include-partial-messages: same guards), what may be shown and
said of a say still being written (persona.ask_say_shown, ask_say_speech, ask_reply_rest), and the caption that shows it
(home.CaptionView.stream): no flicker and no re-typing when the voice begins.

Every run is an invented stream played by a fake process; claude.exe is never started.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import holo, home, persona, voice_fx
from briefing_reader.ask import cli
from briefing_reader.ask import stream as st
from briefing_reader.ask.stream import GUARD, LIMIT, SAY_CAP, GuardTrip, SayWatch, check_event, run_cli, run_planner
from tests.ask_fakes import (
    NOW,
    FakeClock,
    FakeProcess,
    FakeRunner,
    init_event,
    partial_event,
    plan_stream,
    result_event,
    stream,
    tool_chunks,
    words_stream,
)

_app: QApplication | None = None
STREAM_ARGV = ["claude.exe", "-p", cli.INPUT_FORMAT_FLAG, "stream-json", "--include-partial-messages"]
PLAIN_ARGV = ["claude.exe", "-p"]
SAY = "Right, sir. Friday has your Project sync at two."
PLAN = {"say": SAY, "question": "", "lines": ["Todo: title=Read chapter 4 | due=2026-10-09 18:00 | block= | acct= | link="]}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_ask_words", "-platform", "offscreen"])


def heard_from(json_text: str, *, size: object = 1, **tool: object) -> list[tuple[str, bool]]:
    """What SayWatch hears of ``json_text`` written in chunks of ``size`` (see ask_fakes.tool_chunks)."""
    heard: list[tuple[str, bool]] = []
    watch = SayWatch(lambda text, complete: heard.append((text, complete)))
    for line in tool_chunks(json_text, size=size, **tool):
        watch.feed(json.loads(line))
    return heard


class ExtractorTests(unittest.TestCase):
    def test_say_first_grows_and_ends_complete(self) -> None:
        heard = heard_from(json.dumps(PLAN), size=3)
        self.assertEqual(heard[-1], (SAY, True))
        self.assertEqual(sum(1 for _text, complete in heard if complete), 1)
        for (earlier, _), (later, _) in zip(heard, heard[1:]):
            self.assertTrue(later.startswith(earlier.rstrip()), (earlier, later))

    def test_a_space_at_the_end_says_the_last_word_has_ended(self) -> None:
        heard = [text for text, _complete in heard_from('{"say":"One two three"}', size=1)]
        self.assertIn("One ", heard)
        self.assertIn("One tw", heard)
        self.assertNotIn("One tw ", heard)

    def test_escapes_split_anywhere_decode_as_the_final_json_does(self) -> None:
        text = r'{"say":"He said \"hi\"\nthen \\ left \uD83D\uDE00 caf\u00e9 \/ ok.","lines":[]}'
        whole = " ".join(json.loads(text)["say"].split())
        for at in range(1, len(text)):
            with self.subTest(split=at):
                heard = heard_from(text, size=[at])
                self.assertEqual(heard[-1], (whole, True))
        for size in (1, 2, 3, 5):
            self.assertEqual(heard_from(text, size=size)[-1], (whole, True))

    def test_no_half_of_a_pair_or_escape_is_ever_heard(self) -> None:
        for text, _complete in heard_from(r'{"say":"A \uD83D\uDE00 b \"c\" d"}', size=1):
            self.assertNotIn("\\", text)
            self.assertFalse(any(0xD800 <= ord(char) <= 0xDFFF for char in text), text)

    def test_a_raw_pair_split_between_chunks_is_one_character(self) -> None:
        heard = []
        watch = SayWatch(lambda text, complete: heard.append((text, complete)))
        for line in tool_chunks('{"say":"A \ud83d\ude00 b"}', size=[11]):   # the chunk ends between the two halves
            watch.feed(json.loads(line))
        self.assertEqual(heard[-1], ("A \U0001f600 b", True))

    def test_say_not_first_and_other_keys_of_any_shape(self) -> None:
        text = ('{"lines":["say","a\\"b",{"say":"no"}],"gmail_search":{"say":"no","x":[1,{"say":"no"}]},'
                '"n":1.5,"t":true,"z":null , "say" : "Yes, sir." , "question":"no"}')
        self.assertEqual(heard_from(text)[-1], ("Yes, sir.", True))
        self.assertEqual(heard_from(text, size=4)[-1], ("Yes, sir.", True))

    def test_no_say_or_not_a_string_is_never_heard(self) -> None:
        for text in ('{"lines":[],"question":"Which one?"}', '{"a":{"say":"no"}}', '{"say":5,"lines":[]}', '{"say":null}',
                     '{"say":["x"]}', '{"say":{"say":"x"}}', "[1,2]", '"say"', '{"say" 1}', "{{{", ""):
            with self.subTest(text=text):
                self.assertEqual(heard_from(text), [])

    def test_a_key_written_with_an_escape_is_still_say(self) -> None:
        self.assertEqual(heard_from(r'{"say":"Yes."}')[-1], ("Yes.", True))

    def test_only_structured_outputs_input_counts(self) -> None:
        secret = json.dumps({"say": "MARKER", "command": "rm"})
        self.assertEqual(heard_from(secret, name="Bash"), [])
        self.assertEqual(heard_from(secret, name="WebFetch"), [])
        self.assertEqual(heard_from(secret, parent="toolu_parent"), [])          # a sub-agent's

    def test_a_text_block_a_delta_of_another_block_or_no_start_are_never_heard(self) -> None:
        heard = []
        watch = SayWatch(lambda text, complete: heard.append(text))
        delta = {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "input_json_delta", "partial_json": '{"say":"MARKER"}'}}
        watch.feed({"type": "stream_event", "event": delta, "parent_tool_use_id": None})   # no block started
        for inner in ({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "MARKER"}},
                      delta):
            watch.feed({"type": "stream_event", "event": inner, "parent_tool_use_id": None})
        block = {"type": "tool_use", "name": st.STRUCTURED_TOOL, "input": {}}
        watch.feed({"type": "stream_event", "parent_tool_use_id": None,
                    "event": {"type": "content_block_start", "index": 1, "content_block": block}})
        watch.feed({"type": "stream_event", "event": delta, "parent_tool_use_id": None})   # index 0, not 1
        watch.feed({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "StructuredOutput",
                                                                  "input": {"say": "MARKER"}}]}})
        self.assertEqual(heard, [])

    def test_the_tool_called_again_with_the_same_words_adds_nothing(self) -> None:
        heard = []
        watch = SayWatch(lambda text, complete: heard.append((text, complete)))
        for _attempt in range(2):
            for line in tool_chunks(json.dumps(PLAN), size=4):
                watch.feed(json.loads(line))
        self.assertEqual(sum(1 for _text, complete in heard if complete), 1)
        self.assertEqual(heard[-1], (SAY, True))

    def test_the_tool_called_again_with_other_words_is_heard_from_where_they_differ(self) -> None:
        heard = []
        watch = SayWatch(lambda text, complete: heard.append((text, complete)))
        for say in (SAY, "Right, sir. Thursday, in fact."):
            for line in tool_chunks(json.dumps({"say": say, "lines": []}), size=2):
                watch.feed(json.loads(line))
        self.assertEqual(heard[-1], ("Right, sir. Thursday, in fact.", True))
        second = heard[[complete for _text, complete in heard].index(True) + 1:]
        self.assertTrue(second[0][0].startswith("Right, sir. T"), second[0])       # nothing before they differ

    def test_more_than_a_plan_may_hold_is_not_heard(self) -> None:
        heard = heard_from(json.dumps({"say": "word " * 100, "lines": []}), size=20)
        self.assertTrue(heard)
        self.assertTrue(all(len(text) <= SAY_CAP + 1 and not complete for text, complete in heard))

    def test_a_callback_that_raises_changes_nothing(self) -> None:
        def broken(_text: str, _complete: bool) -> None:
            raise RuntimeError("broken")

        watch = SayWatch(broken)
        for line in tool_chunks(json.dumps(PLAN), size=5):
            watch.feed(json.loads(line))
        self.assertIsNotNone(watch.complete_at)

    def test_it_says_when_the_words_came(self) -> None:
        clock = iter(range(100, 1000)).__next__
        watch = SayWatch(lambda _text, _complete: None, clock=clock)
        for line in tool_chunks(json.dumps(PLAN), size=5):
            watch.feed(json.loads(line))
        self.assertLess(watch.first_at, watch.complete_at)


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()

    def run_with(self, process: FakeProcess | list[str], *, argv: list[str] = STREAM_ARGV, **kwargs: object) -> st.RunResult:
        self.runner = FakeRunner(process, clock=self.clock)
        return run_planner(self.runner, argv, "the prompt", env={"PATH": "x"}, cwd=Path("."), timeout_s=90.0,
                           allow_search=False, clock=self.clock, now=lambda: NOW, **kwargs)

    def test_the_prompt_is_one_stream_json_message_and_stdin_closes_at_the_result(self) -> None:
        process = FakeProcess(words_stream(PLAN))
        result = self.run_with(process)
        self.assertIsNone(result.failure)
        started = self.runner.started[0]
        self.assertEqual(started.stdin, "the prompt")
        self.assertEqual(started.raw, cli.stream_message("the prompt"))
        self.assertNotIn("the prompt", " ".join(started.argv))                     # never in argv
        self.assertTrue(process.stdin_closed)
        self.assertTrue(process.closed)

    def test_stdin_stays_open_until_the_result(self) -> None:
        seen: list[bool] = []

        class Watching(FakeProcess):
            def read_line(self, timeout: float) -> str | None:
                seen.append(self.stdin_closed)
                return super().read_line(timeout)

        self.run_with(Watching(words_stream(PLAN)))
        self.assertTrue(seen)
        self.assertFalse(any(seen))

    def test_a_plain_command_line_gets_plain_stdin(self) -> None:
        result = self.run_with(plan_stream(PLAN), argv=PLAIN_ARGV)
        self.assertIsNone(result.failure)
        self.assertEqual(self.runner.started[0].raw, "the prompt")

    def test_words_come_before_the_plan_and_the_plan_is_the_final_word(self) -> None:
        heard: list[tuple[str, bool]] = []
        result = self.run_with(words_stream(PLAN, size=6), on_words=lambda text, complete: heard.append((text, complete)))
        self.assertEqual(heard[-1], (SAY, True))
        self.assertEqual(result.plan.say, SAY)
        self.assertIsNotNone(result.first_words_s)
        self.assertIsNotNone(result.say_complete_s)

    def test_a_run_without_partial_events_still_answers(self) -> None:
        heard: list[str] = []
        result = self.run_with(plan_stream(PLAN), on_words=lambda text, _complete: heard.append(text))
        self.assertEqual(heard, [])
        self.assertEqual(result.plan.say, SAY)
        self.assertIsNone(result.first_words_s)

    def test_stream_events_are_normal_to_the_guard(self) -> None:
        check_event(json.loads(partial_event({"type": "message_start"})))
        block = {"type": "tool_use", "name": "StructuredOutput", "input": {}}
        check_event(json.loads(partial_event({"type": "content_block_start", "index": 0, "content_block": block})))

    def test_everything_the_guard_refused_it_still_refuses(self) -> None:
        with self.assertRaises(GuardTrip):
            check_event({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}})
        # A tool other than StructuredOutput in the middle of a stream: its words never show, its call stops the run.
        heard: list[str] = []
        bash = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}
        lines = [stream("success")[0], *tool_chunks('{"say":"MARKER","command":"rm"}', size=4, name="Bash"),
                 json.dumps(bash), *plan_stream(PLAN)[1:]]
        process = FakeProcess(lines)
        result = self.run_with(process, on_words=lambda text, _complete: heard.append(text))
        self.assertEqual((result.failure.kind, result.failure.detail), (GUARD, "tool_use"))
        self.assertTrue(process.killed)
        self.assertEqual(heard, [])

    def test_the_init_guard_stands(self) -> None:
        lines = [json.dumps(init_event(apiKeySource="ANTHROPIC_API_KEY")), *words_stream(PLAN)[1:]]
        heard: list[str] = []
        result = self.run_with(lines, on_words=lambda text, _complete: heard.append(text))
        self.assertEqual((result.failure.kind, result.failure.detail), (GUARD, "api_key_source"))
        self.assertEqual(heard, [])

    def test_a_partial_event_before_the_init_is_not_an_init(self) -> None:
        lines = words_stream(PLAN)
        result = self.run_with([lines[1], *lines])
        self.assertEqual((result.failure.kind, result.failure.detail), (GUARD, "not_init"))

    def test_a_rate_limit_still_stops_it_mid_words(self) -> None:
        limit = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "overageStatus": "rejected"}}
        lines = words_stream(PLAN)
        process = FakeProcess([*lines[:5], json.dumps(limit), *lines[5:]])
        result = self.run_with(process)
        self.assertEqual(result.failure.kind, LIMIT)
        self.assertTrue(process.killed)

    def test_cancel_kills_it_mid_words(self) -> None:
        cancel = threading.Event()
        process = FakeProcess(words_stream(PLAN), cancel=cancel, cancel_after=5)
        result = self.run_with(process, cancel=cancel)
        self.assertEqual(result.failure.kind, st.CANCELLED)
        self.assertTrue(process.killed)
        self.assertTrue(process.stdin_closed)

    def test_the_final_plan_is_read_from_the_result_not_the_words(self) -> None:
        lines = plan_stream(PLAN)
        wrote = tool_chunks(json.dumps({"say": "Something else entirely.", "lines": []}), size=6)
        heard: list[str] = []
        result = self.run_with([lines[0], *wrote, *lines[1:]], on_words=lambda text, _complete: heard.append(text))
        self.assertEqual(heard[-1], "Something else entirely.")
        self.assertEqual(result.plan.say, SAY)

    def test_a_cli_that_does_not_go_after_the_result_is_killed_soon(self) -> None:
        class Lingers(FakeProcess):
            def __init__(self, lines: list[str]) -> None:
                super().__init__(lines)
                self.waits: list[float] = []

            def wait(self, timeout: float) -> int | None:
                self.waits.append(timeout)
                return None if not self.killed else -9

        process = Lingers(words_stream(PLAN))
        result = self.run_with(process)
        self.assertIsNone(result.failure)
        self.assertEqual(process.waits[0], st.STREAM_EXIT_S)
        self.assertTrue(process.killed)

    def test_run_cli_tells_whether_it_was_started_ahead(self) -> None:
        heard: list[tuple[str, object]] = []
        runner = FakeRunner(clock=self.clock)
        process = FakeProcess(words_stream(PLAN))
        result = run_cli(runner, STREAM_ARGV, "the prompt", env={}, cwd=Path("."), timeout_s=90.0,
                         read=lambda event, init: st.read_result(event, init, allow_search=False), clock=self.clock,
                         progress=lambda what, value: heard.append((what, value)), process=process)
        self.assertIsNone(result.failure)
        self.assertEqual(runner.started, [])                                       # nothing new started
        self.assertEqual(process.sent, [cli.stream_message("the prompt")])
        self.assertIn(("started", True), heard)

    def test_process_ended_with_no_output_says_so_only_when_started_ahead(self) -> None:
        def go(process: FakeProcess, ahead: bool) -> st.RunResult:
            runner = FakeRunner(process, clock=self.clock)
            return run_cli(runner, STREAM_ARGV, "p", env={}, cwd=Path("."), timeout_s=90.0,
                           read=lambda event, init: st.read_result(event, init, allow_search=False), clock=self.clock,
                           process=process if ahead else None)

        self.assertEqual(go(FakeProcess([]), True).failure.detail, st.PRESTART_DIED)
        self.assertEqual(go(FakeProcess(stream("success")[:1]), True).failure.detail, st.PRESTART_DIED)   # only its init
        self.assertNotEqual(go(FakeProcess([]), False).failure.detail, st.PRESTART_DIED)
        dead = FakeProcess([])
        dead.killed = True
        self.assertEqual(go(dead, True).failure.detail, st.PRESTART_DIED)                 # nothing could be sent
        some_output = FakeProcess(words_stream(PLAN)[:6])
        self.assertNotEqual(go(some_output, True).failure.detail, st.PRESTART_DIED)       # it began to answer
        self.assertEqual(result_event().get("subtype"), "success")


class Shown(unittest.TestCase):
    def test_whole_words_only_until_it_is_whole(self) -> None:
        shown = persona.ask_say_shown
        self.assertEqual(shown("Right", complete=False), "")
        self.assertEqual(shown("Right, si", complete=False), "Right,")
        self.assertEqual(shown("Right, sir ", complete=False), "Right, sir")
        self.assertEqual(shown("Right, sir", complete=True), "Right, sir.")        # as it will be said

    def test_nothing_unsafe_to_say_shows(self) -> None:
        shown = persona.ask_say_shown
        self.assertEqual(shown("Write to ana@example.edu now", complete=False), "Write to that address")
        self.assertEqual(shown("See https://example.com/x today", complete=False), "See the link")

    def test_a_say_that_claims_something_was_done_shows_nothing(self) -> None:
        shown = persona.ask_say_shown
        self.assertEqual(shown("Done - I've moved it ", complete=False), "")
        self.assertEqual(shown("Moved Project sync to Friday.", complete=True), "")
        self.assertEqual(shown("I've lined up a move ", complete=False), "I've lined up a move")

    def test_after_mail_a_quote_never_shows_while_it_is_written(self) -> None:
        shown = persona.ask_say_shown
        text = 'Ana wrote about the budget: "Please confirm the Q4 figures by Friday'
        self.assertEqual(shown(text + " ", complete=False, read_mail=True), "Ana wrote about the budget:")
        closed = 'Ana wrote: "Please confirm the Q4 figures by Friday." She is waiting.'
        self.assertEqual(shown(closed, complete=True, read_mail=True), "She is waiting.")
        self.assertIn("Please confirm", shown(text + " ", complete=False))          # no mail read: only a say

    def test_after_mail_at_most_the_short_summary_shows(self) -> None:
        long_say = "Ana asked about the budget. " * 12
        self.assertLessEqual(len(persona.ask_say_shown(long_say, complete=True, read_mail=True)),
                             persona.MAIL_SAY_LIMIT)


class EarlySpeech(unittest.TestCase):
    def outcome(self, say: str, question: str = "", threads: int = 0, cards: tuple = ()) -> SimpleNamespace:
        return SimpleNamespace(say=say, question=question, cards=cards, mail=SimpleNamespace(threads=threads, messages=0))

    def test_it_is_what_the_final_reply_would_say_of_it(self) -> None:
        for say in (SAY, "No sentence end", "Write to ana@example.edu now."):
            with self.subTest(say=say):
                self.assertEqual(persona.ask_say_speech(say), persona.ask_reply_speech(self.outcome(say)).text)
        self.assertEqual(persona.ask_say_speech("One. " * 20, read_mail=True),
                         persona.ask_reply_speech(self.outcome("One. " * 20, threads=1)).text)

    def test_what_must_wait_for_the_whole_answer(self) -> None:
        self.assertEqual(persona.ask_say_speech("I've sent the note to Ana."), "")
        self.assertEqual(persona.ask_say_speech("Ana is the project lead for Northwind.",
                                                notes=("Ana is the project lead for Northwind",)), "")
        self.assertEqual(persona.ask_say_speech('Ana wrote: "Please confirm the Q4 figures by Friday."', read_mail=True), "")
        self.assertEqual(persona.ask_say_speech(""), "")

    def test_the_rest_is_only_the_question(self) -> None:
        said = persona.ask_say_speech(SAY)
        self.assertEqual(persona.ask_reply_rest(self.outcome(SAY), said), "")
        self.assertEqual(persona.ask_reply_rest(self.outcome(SAY, "Which Friday?"), said), "Which Friday?")
        self.assertEqual(persona.ask_reply_rest(self.outcome(SAY, "I've told Ana, haven't I?"), said), "")

    def test_a_question_that_repeats_a_note_is_only_mentioned(self) -> None:
        said = persona.ask_say_speech(SAY)
        note = "Ana is the project lead for Northwind"
        rest = persona.ask_reply_rest(self.outcome(SAY, "Ana is the project lead for Northwind, right?"), said, notes=(note,))
        self.assertEqual(rest, "I have a question for you, sir; it's on screen.")

    def test_a_final_say_that_differs_gets_no_rest(self) -> None:
        said = persona.ask_say_speech(SAY)
        self.assertIsNone(persona.ask_reply_rest(self.outcome("Thursday, in fact."), said))
        self.assertIsNone(persona.ask_reply_rest(self.outcome("I've moved it."), said))


def wait(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)


LONG_LINE = ("Right, sir. Friday has your Project sync at two and the budget review with Ana at four in the afternoon, "
             "and nothing clashes. The evening is clear.")


class StreamPagesTests(unittest.TestCase):
    def test_a_page_never_changes_as_more_words_come(self) -> None:
        words = LONG_LINE.split()
        final = home.stream_pages(LONG_LINE)
        for count in range(1, len(words) + 1):
            pages = home.stream_pages(" ".join(words[:count]))
            self.assertEqual(pages[:-1], final[:len(pages) - 1], count)
            self.assertTrue(final[len(pages) - 1].startswith(pages[-1]), count)

    def test_pages_are_never_too_long_and_lose_nothing(self) -> None:
        pages = home.stream_pages(LONG_LINE)
        self.assertEqual(" ".join(pages), LONG_LINE)
        self.assertTrue(all(len(page.split()) <= home.CAPTION_WORDS for page in pages))
        self.assertEqual(pages[0], "Right, sir.")
        self.assertEqual(home.stream_pages(""), [])


class CaptionStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        holo.set_reduced_motion(False)
        self.addCleanup(holo.set_reduced_motion, None)
        self.view = home.CaptionView()
        self.view.resize(760, 80)
        self.addCleanup(self.view.deleteLater)
        self.view.show()
        self.view.set_resting("Good evening, sir.")

    def test_words_show_as_they_come_with_no_pace_of_their_own(self) -> None:
        view = self.view
        view.stream("Friday has")
        self.assertEqual((view.state(), view.page_text(), view.revealed_words()), (home.SPEAKING, "Friday has", 2))
        view.stream("Friday has your Project")
        self.assertEqual((view.page_text(), view.revealed_words()), ("Friday has your Project", 4))
        self.assertEqual(view.accessibleDescription(), "Friday has your Project")
        self.assertTrue(view.waits_for_words())

    def test_the_words_already_shown_are_never_shown_again(self) -> None:
        view = self.view
        view.stream("Friday has your")
        first = list(view._revealed_at)
        view.stream("Friday has your Project sync")
        self.assertEqual(view._revealed_at[:3], first)

    def test_when_the_voice_begins_nothing_is_typed_again(self) -> None:
        view = self.view
        view.stream(SAY, complete=True)
        wait(10)
        page, revealed = view.page_index(), list(view._revealed_at)
        pages = view.pages()
        timeline = voice_fx.VoiceTimeline(words=(), envelope=(), duration_ms=3000)
        position = mock.Mock(return_value=0)
        view.say(SAY, timeline=timeline, position=position)
        self.assertEqual((view.page_index(), view.pages()), (page, pages))
        self.assertEqual(view._revealed_at, revealed)
        self.assertEqual(view.state(), home.SPEAKING)
        self.assertTrue(view.is_timed())
        self.assertFalse(view.waits_for_words())
        states: list[str] = []
        view.stateChanged.connect(states.append)
        view.finish()                                                # the voice ended: it holds, as any line does
        self.assertEqual(view.state(), home.HOLDING)
        self.assertEqual(states, [home.HOLDING])

    def test_the_later_pages_follow_the_voice_once_it_has_begun(self) -> None:
        view = self.view
        view.stream(LONG_LINE, complete=True)
        self.assertEqual(view.page_index(), 0)
        heard = [0]
        timeline = voice_fx.VoiceTimeline(words=(), envelope=(), duration_ms=20000)
        view.say(LONG_LINE, timeline=timeline, position=lambda: heard[0])
        wait(30)
        self.assertEqual(view.page_index(), 0)                       # the voice is at its start
        second = view.word_times()[len(view.pages()[0].split())]
        heard[0] = second + 1
        view._page_timer.start(0)
        wait(30)
        self.assertEqual(view.page_index(), 1)

    def test_a_whole_page_turns_after_its_reading_time_if_the_voice_is_late(self) -> None:
        view = self.view
        with mock.patch.object(home, "SPOKEN_WPS", 200.0):
            view.stream("Right, sir. Friday has your sync at two.", complete=True)
            self.assertEqual(view.page_index(), 0)
            wait(60)
        self.assertEqual(view.page_index(), 1)
        self.assertEqual(view.state(), home.SPEAKING)                # it goes on waiting for its voice

    def test_another_line_ending_leaves_a_line_being_written_alone(self) -> None:
        view = self.view
        view.stream("Friday has your")
        view.finish()
        self.assertEqual(view.state(), home.SPEAKING)
        view.stream("Friday has your sync.", complete=True)
        view.finish()
        self.assertEqual(view.state(), home.SPEAKING)                # ... and one waiting for its own voice

    def test_no_voice_comes_it_holds_then_rests(self) -> None:
        view = self.view
        view._voice_wait.setInterval(10)
        view.stream(SAY, complete=True)
        with mock.patch.object(home, "HOLD_SPOKEN_MS", 10), mock.patch.object(home, "FADE_OUT_MS", 10):
            deadline = time.monotonic() + 5
            while view.state() != home.RESTING and time.monotonic() < deadline:
                wait(10)
        self.assertEqual(view.state(), home.RESTING)
        self.assertFalse(view.streamed())

    def test_muted_it_goes_at_the_muted_pace_and_holds(self) -> None:
        view = self.view
        with mock.patch.object(home, "MUTED_WPS", 200.0):
            view.stream(LONG_LINE)
            view.stream(LONG_LINE, complete=True, spoken=False)
            self.assertFalse(view.waits_for_words())
            deadline = time.monotonic() + 3
            while view.state() != home.HOLDING and time.monotonic() < deadline:
                wait(10)
        self.assertEqual(view.state(), home.HOLDING)
        self.assertEqual(view.page_index(), len(view.pages()) - 1)
        view.say(LONG_LINE, spoken=False)                            # _say's note that it is silent: nothing starts over
        self.assertEqual(view.page_index(), len(view.pages()) - 1)

    def test_a_longer_line_extends_it(self) -> None:
        view = self.view
        view.stream(SAY, complete=True, spoken=False)
        view.say(f"{SAY} Which Friday?", spoken=False)
        self.assertEqual(view.full_text(), f"{SAY} Which Friday?")

    def test_a_correction_replaces_it_at_once_and_its_voice_takes_over(self) -> None:
        view = self.view
        view.stream("Friday has your")
        view.correct("Thursday, in fact, sir.")
        self.assertEqual(view.revealed_words(), 4)                   # whole, no reveal
        self.assertEqual(view.full_text(), "Thursday, in fact, sir.")
        self.assertFalse(view.is_alert())
        revealed = list(view._revealed_at)
        view.say("Thursday, in fact, sir.")
        self.assertEqual(view._revealed_at, revealed)

    def test_a_failure_is_coral_until_the_line_goes(self) -> None:
        view = self.view
        view.stream("Friday has your")
        view.correct("Sorry, sir - that took too long. Nothing was proposed.", alert=True)
        self.assertTrue(view.is_alert())
        view.grab()                                                  # it paints in coral
        view.say("Sorry, sir - that took too long. Nothing was proposed.")
        self.assertTrue(view.is_alert())                             # its voice keeps it
        view.say("Something else.")
        self.assertFalse(view.is_alert())

    def test_withdrawn_at_once(self) -> None:
        view = self.view
        view.stream("Friday has your")
        view.stream("")
        self.assertEqual((view.state(), view.shown_text()), (home.RESTING, "Good evening, sir."))
        view.say("Another line.")
        view.stream("")                                              # not a streamed line: left alone
        self.assertEqual(view.state(), home.SPEAKING)

    def test_a_line_that_is_not_more_of_the_same_starts_afresh(self) -> None:
        view = self.view
        view.stream("Friday has your sync")
        view.stream("Thursday")
        self.assertEqual((view.page_text(), view.revealed_words()), ("Thursday", 1))

    def test_the_last_word_may_get_its_full_stop(self) -> None:
        view = self.view
        view.stream("Friday has your sync")
        revealed = list(view._revealed_at)
        view.stream("Friday has your sync.", complete=True)
        self.assertEqual(view._revealed_at, revealed)
        self.assertEqual(view.page_text(), "Friday has your sync.")

    def test_where_captions_show_whole_pages_it_shows_a_page_only_when_whole(self) -> None:
        holo.set_reduced_motion(True)
        view = self.view
        view.stream("Right, sir. Friday")
        self.assertEqual(view.revealed_words(), 2)                   # its first page is whole
        view.clear()
        view.stream("Friday has your")
        self.assertEqual(view.revealed_words(), 0)                   # it may still grow
        view.stream("Friday has your sync.", complete=True)
        self.assertEqual(view.revealed_words(), 4)

    def test_a_growing_line_paints_without_error(self) -> None:
        view = self.view
        for count in range(1, len(LONG_LINE.split()) + 1):
            view.stream(" ".join(LONG_LINE.split()[:count]))
            view.grab()
        wait(50)
        self.assertEqual(view.state(), home.SPEAKING)
