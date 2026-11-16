"""The new look, part 6: Ask's typed frames - the optional "topic" and "frames" of an Ask's or a web research's answer
(board.ask_frames, board.protocol, ask/assets' schemas and prompts, ask.stream, ask.research, ask.planner). Qt-free;
claude.exe is never started (tests.ask_fakes' recorded and hand-written runs only).

Covered, hostile input first: the model's frames with markup, <script>, javascript: and http: addresses in every
string (words, shown as words), control and bidi characters (shown as "<U+202E>"), 10 000-character strings, NaN,
Infinity, 1e309, a 400-digit number, numbers as strings and booleans as numbers, extra keys, wrong types, 50 rows, a
maths tree 7 deep or of 65 nodes, a flat list of 10 000 arguments, a list nested 10 000 deep and a frame list of 100:
each one dropped or cut as the rules say, never the answer, and what is kept is only text and numbers; each kind's
checker (table, chart, timeline, maths: fields, caps, the number's words, the meta, the key); maths' words (brackets,
signs side by side, powers, fractions, roots); the frame message board.protocol passes and the wire bodies it refuses;
both schema files (valid draft-07, only the keywords Ask's schemas already use, "topic" and "frames" optional, after
"say" or "answer", one block in both, caps as the board's, passed by cli as they are); the prompts' FRAMES sections and
the agent prompt still the plain prompt then its own words; a planner run's and a research run's answer carrying them to
AskOutcome, with one log line of counts, kinds and reason words only.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import unittest
from pathlib import Path

from briefing_reader import live
from briefing_reader.ask import cli, planner, research, stream
from briefing_reader.board import ask_frames, protocol
from tests.ask_fakes import HOURS_URL, RESEARCH_ANSWER, VISIT_URL, plan_stream, result_event
from tests.ask_fakes import stream as recorded
from tests.test_ask_planner import COMMAND, FORCED_COMMAND, MOVE_LINE, SAY, PlannerTestCase

MINUS = ask_frames.MINUS
HOSTILE_WORDS = ("<script>window.pwned=1</script>", "<img src=x onerror=alert(1)>", "javascript:alert(2)",
                 "http://example.com/x.png", "<a href=\"https://example.org\">here</a>")
CONTROL = "‮evil⁦ \x00\x07 tab\tend ​"


def table(**changes: object) -> dict:
    frame = {"kind": "table", "title": "Budget lines", "unit": "USD", "columns": ["Line", "Friday", "Today", "Change"],
             "rows": [["Travel", 1200, 1200, 0], ["Software", 2900, 3400, "+500"], ["Events", 2100, 2100, 0],
                      ["Hardware", 800, 800, 0]], "changed": 1, "change": "+500 since Friday",
             "source": "From Dana's mail, 8:12 AM"}
    frame.update(changes)
    return frame


def chart(**changes: object) -> dict:
    frame = {"kind": "chart", "title": "Q4 budget by line", "unit": "USD",
             "bars": [{"label": "Travel", "value": 1200}, {"label": "Software", "value": 3400},
                      {"label": "Events", "value": 2100}, {"label": "Hardware", "value": 800}],
             "changed": 1, "was": 2900, "change": "+500 Software · was 2.9k on Friday"}
    frame.update(changes)
    return frame


def timeline(**changes: object) -> dict:
    frame = {"kind": "timeline", "title": "LING essay",
             "nodes": [{"when": "Mon", "words": "Outline", "state": "done"},
                       {"when": "Wed", "words": "Full draft", "state": "next"},
                       {"when": "Thu 11:59 PM", "words": "Essay", "state": "due"}], "today": 1}
    frame.update(changes)
    return frame


def maths(**changes: object) -> dict:
    frame = {"kind": "maths", "title": "Solve 2x² − 8x + 6 = 0",
             "steps": [{"name": "Divide by 2", "expr": ["=", ["+", ["pow", "x", 2], ["*", -4, "x"], 3], 0]},
                       {"name": "Factor", "expr": ["=", ["*", ["()", ["-", "x", 1]], ["()", ["-", "x", 3]]], 0]}],
             "answer": {"name": "So", "expr": ["or", ["=", "x", 1], ["=", "x", 3]]}}
    frame.update(changes)
    return frame


def deep(levels: int) -> object:
    """An expression ``levels`` deep: ["-", ["-", ... "x"]]."""
    node: object = "x"
    for _ in range(levels - 1):
        node = ["-", node]
    return node


def leaves(value: object) -> list[object]:
    """Every leaf of a JSON-like value (dict values and list items, all the way down)."""
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in leaves(item)]
    return [value]


def strings(value: object) -> list[str]:
    return [leaf for leaf in leaves(value) if isinstance(leaf, str)]


def checked(value: dict) -> ask_frames.AskFrame:
    found = ask_frames.check_frame(value)
    if isinstance(found, str):
        raise AssertionError(f"dropped: {found}")
    return found


# --------------------------------------------------------------------------
# Hostile input first
# --------------------------------------------------------------------------

def string_paths(frame: object, path: tuple = ()) -> list[tuple]:
    """The path of every string the model writes in ``frame`` (maths names apart: a name is letters or it fails)."""
    found: list[tuple] = []
    if isinstance(frame, dict):
        for key, value in frame.items():
            if key in ("kind", "state", "expr"):
                continue
            found += string_paths(value, (*path, key))
    elif isinstance(frame, list):
        for index, value in enumerate(frame):
            found += string_paths(value, (*path, index))
    elif isinstance(frame, str):
        found.append(path)
    return found


def put(frame: dict, path: tuple, value: object) -> dict:
    copy = json.loads(json.dumps(frame))
    node = copy
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return copy


ALL = (table, chart, timeline, maths)


class HostileTests(unittest.TestCase):
    def test_markup_scripts_and_addresses_in_every_string_stay_words(self) -> None:
        for make in ALL:
            good = make()
            for path in string_paths(good):
                for words in HOSTILE_WORDS:
                    with self.subTest(kind=good["kind"], path=path, words=words[:16]):
                        found = ask_frames.check_frame(put(good, path, words))
                        if isinstance(found, str):           # longer than that field's cap: the frame is dropped
                            self.assertEqual(found, ask_frames.D_TOO_LONG, path)
                            continue
                        shown = strings(found.body) + [found.title, found.meta]
                        self.assertTrue(any(words in text for text in shown), (path, shown))   # as words, whole

    def test_control_and_bidi_characters_are_shown_escaped(self) -> None:
        for make in ALL:
            good = make()
            for path in string_paths(good):
                with self.subTest(kind=good["kind"], path=path):
                    found = ask_frames.check_frame(put(good, path, CONTROL[:20]))
                    if isinstance(found, str):
                        self.assertEqual(found, ask_frames.D_TOO_LONG)
                        continue
                    for text in strings(found.body) + [found.title, found.meta]:
                        self.assertFalse(any(ord(char) < 32 or char in "‮⁦​" for char in text), text)
                    self.assertTrue(any("<U+202E>" in text for text in strings(found.body) + [found.title]))

    def test_ten_thousand_characters_drop_the_frame(self) -> None:
        for make in ALL:
            good = make()
            for path in string_paths(good):
                with self.subTest(kind=good["kind"], path=path):
                    self.assertEqual(ask_frames.check_frame(put(good, path, "x" * 10_000)), ask_frames.D_TOO_LONG)

    def test_numbers_that_are_not_numbers_drop_the_frame(self) -> None:
        bad = (float("nan"), float("inf"), float("-inf"), 1e309, 10 ** 400, -(10 ** 400), 10 ** 15, 1e15, 1e-9,
               "12", "3,400", True, False, None, [1], {"v": 1})
        for value in bad:
            with self.subTest(value=repr(value)[:20]):
                self.assertEqual(ask_frames.check_frame(chart(bars=[{"label": "A", "value": value}], changed=-1)),
                                 ask_frames.D_NUMBER)
                if value is not None:                                # (no "was": no ghost tick)
                    self.assertEqual(ask_frames.check_frame(chart(was=value)), ask_frames.D_NUMBER)
                if not isinstance(value, str):                       # (words are a cell's own)
                    self.assertIn(ask_frames.check_frame(table(rows=[["A", value]], columns=["x", "y"], changed=-1)),
                                  (ask_frames.D_NUMBER, ask_frames.D_SHAPE))
                self.assertIn(ask_frames.check_frame(maths(answer={"expr": ["=", "x", value]})),
                              (ask_frames.D_NUMBER, ask_frames.D_MATHS))
        # The board's numbers: up to its limit and down to its floor, and zero.
        self.assertIsInstance(ask_frames.check_frame(chart(bars=[{"label": "A", "value": 10 ** 15 - 1}], changed=-1)),
                              ask_frames.AskFrame)
        self.assertIsInstance(ask_frames.check_frame(chart(bars=[{"label": "A", "value": 1e-6}], changed=-1)),
                              ask_frames.AskFrame)

    def test_indexes_that_are_not_indexes_drop_the_frame(self) -> None:
        for value in (True, 1.0, "1", 4, -2, 10 ** 20, float("nan")):
            with self.subTest(value=repr(value)):
                self.assertEqual(ask_frames.check_frame(table(changed=value)), ask_frames.D_INDEX)
                self.assertEqual(ask_frames.check_frame(chart(changed=value)), ask_frames.D_INDEX)
                if value != 4:                                     # 3 nodes: TODAY may stand before 0 to 3
                    self.assertEqual(ask_frames.check_frame(timeline(today=value)), ask_frames.D_INDEX)
        self.assertEqual(checked(table(changed=None)).body["changed"], -1)
        self.assertEqual(checked(timeline(today=3)).body["today"], 3)

    def test_extra_keys_drop_the_frame(self) -> None:
        cases = (table(onclick="x"), table(html="<b>"), chart(bars=[{"label": "A", "value": 1, "href": "x"}]),
                 timeline(nodes=[{"when": "Mon", "words": "x", "state": "done", "style": "x"}]),
                 maths(steps=[{"name": "x", "expr": "x", "tex": "\\frac{1}{2}"}]),
                 maths(answer={"expr": "x", "markup": "<b>"}), maths(colour="red", unit="GBP"),
                 chart(bars=[{"label": "A", "value": 1, "state": "done"}]), {**table(), "__proto__": {"x": 1}},
                 {**timeline(), 7: "x"})
        for frame in cases:
            with self.subTest(frame=repr(frame)[:60]):
                self.assertEqual(ask_frames.check_frame(frame), ask_frames.D_FIELD)

    def test_another_kind_s_field_is_left_out_unread(self) -> None:
        # The schema's one frame item offers every kind's fields to each kind: one that carries another kind's field is
        # kept without it (never checked, never shown); only a key no kind has drops it (above).
        cases = ((maths(unit="GBP"), ("unit",)), (timeline(change="moved from Tuesday"), ("change",)),
                 (chart(source="From Dana's mail"), ("source",)), (table(today=2), ("today",)),
                 (timeline(unit="", changed=-1, change="", source=""), ("unit", "changed", "change", "source")),
                 (maths(unit=["<script>x</script>"], bars="x" * 10_000, today=float("nan")), ("unit", "bars", "today")),
                 (table(bars=[]), ("bars",)), (chart(columns=["x"]), ("columns",)), (timeline(steps=[]), ("steps",)),
                 (maths(today=1), ("today",)))
        for frame, foreign in cases:
            with self.subTest(kind=frame["kind"], foreign=foreign):
                found = checked(frame)
                plain = checked({key: value for key, value in frame.items() if key not in foreign})
                self.assertEqual((found.kind, found.title, found.meta, found.key, found.body),
                                 (plain.kind, plain.title, plain.meta, plain.key, plain.body))
                self.assertNotIn("Tuesday", json.dumps(found.body))
                self.assertNotIn("script", json.dumps(found.body))

    def test_wrong_types_drop_the_frame(self) -> None:
        cases = {ask_frames.D_SHAPE: (table(columns="Line, Friday"), table(rows={"a": 1}), table(rows=[["A", ["x"]]]),
                                      chart(bars="a lot"), chart(bars=[["Travel", 1200]]), timeline(nodes={"x": 1}),
                                      maths(steps="x = 1"), maths(answer="x = 1"), table(title=7), table(unit=3),
                                      chart(change=["+500"]), "a frame", 7, None, ["table"]),
                 ask_frames.D_KIND: (table(kind="graph"), table(kind="Table"), table(kind=["table"]), {"title": "x"}),
                 ask_frames.D_STATE: (timeline(nodes=[{"when": "Mon", "words": "x", "state": "late"}]),
                                      timeline(nodes=[{"when": "Mon", "words": "x", "state": True}])),
                 ask_frames.D_EMPTY: (table(rows=[]), table(columns=[]), chart(bars=[]), timeline(nodes=[]),
                                      maths(steps=[]), table(title=""), table(title="   "),
                                      table(rows=[["", ""]], columns=["a", "b"], changed=-1),
                                      timeline(nodes=[{"when": "", "words": ""}]))}
        for reason, frames in cases.items():
            for frame in frames:
                with self.subTest(reason=reason, frame=json.dumps(frame)[:60]):
                    self.assertEqual(ask_frames.check_frame(frame), reason)
        self.assertEqual(ask_frames.check_frame(table(rows=[["a", "b", "c", "d", "e"]])), ask_frames.D_TOO_LONG)
        self.assertEqual(ask_frames.check_frame(maths(answer={"name": "So"})), ask_frames.D_FIELD)
        self.assertEqual(ask_frames.check_frame({k: v for k, v in maths().items() if k != "answer"}),
                         ask_frames.D_FIELD)

    def test_too_many_items_drop_the_frame(self) -> None:
        self.assertEqual(ask_frames.check_frame(table(rows=[["a", 1, 2, 3]] * 50)), ask_frames.D_TOO_LONG)
        self.assertEqual(ask_frames.check_frame(table(rows=[["a", 1, 2, 3]] * 13)), ask_frames.D_TOO_LONG)
        self.assertIsInstance(ask_frames.check_frame(table(rows=[["a", 1, 2, 3]] * 12, changed=-1)), ask_frames.AskFrame)
        self.assertEqual(ask_frames.check_frame(table(columns=["c"] * 7)), ask_frames.D_TOO_LONG)
        self.assertEqual(ask_frames.check_frame(chart(bars=[{"label": "a", "value": 1}] * 9, changed=-1)),
                         ask_frames.D_TOO_LONG)
        self.assertEqual(ask_frames.check_frame(timeline(nodes=[{"when": "Mon", "words": "x"}] * 9, today=-1)),
                         ask_frames.D_TOO_LONG)
        self.assertEqual(ask_frames.check_frame(maths(steps=[{"expr": "x"}] * 9)), ask_frames.D_TOO_LONG)

    def test_maths_depth_nodes_and_huge_lists(self) -> None:
        self.assertIsInstance(ask_frames.check_frame(maths(steps=[{"expr": deep(6)}])), ask_frames.AskFrame)
        self.assertEqual(ask_frames.check_frame(maths(steps=[{"expr": deep(7)}])), ask_frames.D_DEPTH)
        self.assertEqual(ask_frames.check_frame(maths(steps=[{"expr": deep(10_000)}])), ask_frames.D_DEPTH)   # no
        # recursion error: the depth is read on the way down, never past MATHS_DEPTH
        many = ["+", *([1] * 8)]                                                        # 9 nodes a line
        self.assertIsInstance(ask_frames.check_frame(maths(steps=[{"expr": many}] * 7, answer={"expr": 1})),
                              ask_frames.AskFrame)                                       # 63 + 1 = 64
        self.assertEqual(ask_frames.check_frame(maths(steps=[{"expr": many}] * 7, answer={"expr": ["-", 1]})),
                         ask_frames.D_NODES)                                             # 65
        self.assertEqual(ask_frames.check_frame(maths(steps=[{"expr": ["+", *([1] * 10_000)]}])), ask_frames.D_MATHS)
        for bad in (["?", 1, 2], ["+", 1], ["root"], ["()", 1, 2], ["=", "x"], ["or", *(["x"] * 5)], [], [["+", 1, 2]],
                    ["+", 1, "x1"], ["+", 1, "abcdefghijklm"], ["+", 1, "javascript:x"], ["+", 1, "<b>"], ["+", 1, ""],
                    ["+", 1, "x y"], ["+", 1, {"num": 1}], ["frac", 1, None], ["__proto__", 1, 2], ["constructor", 1]):
            with self.subTest(bad=json.dumps(bad)[:40]):
                self.assertEqual(ask_frames.check_frame(maths(answer={"expr": bad})), ask_frames.D_MATHS)

    def test_a_frame_list_of_a_hundred(self) -> None:
        found = ask_frames.check_answer({"topic": "Dana's numbers", "frames": [table()] * 100})
        self.assertEqual(len(found.frames), 1)                     # the first; the next two repeat its kind and title
        self.assertEqual(dict(found.dropped), {ask_frames.D_REPEAT: 2, ask_frames.D_TOO_MANY: 97})
        mixed = ask_frames.check_answer({"frames": [table(), chart(), maths(), timeline()]})
        self.assertEqual([frame.kind for frame in mixed.frames], ["table", "chart", "maths"])
        self.assertEqual(dict(mixed.dropped), {ask_frames.D_TOO_MANY: 1})

    def test_the_answer_itself_never_fails(self) -> None:
        for frames in ("a frame", 7, {"kind": "table"}, [None], [[]], [table(rows=[[float("nan")]])]):
            with self.subTest(frames=repr(frames)[:30]):
                found = ask_frames.check_answer({"say": "x", "frames": frames})
                self.assertEqual(found.frames, ())
                self.assertTrue(found.dropped)
        for data in (None, "x", [], 7):
            self.assertEqual(ask_frames.check_answer(data), ask_frames.AskFrames())
        self.assertFalse(ask_frames.AskFrames().given)

    def test_the_topic(self) -> None:
        self.assertEqual(ask_frames.check_answer({"topic": "Dana's numbers"}).topic, "Dana's numbers")
        self.assertEqual(ask_frames.check_answer({"topic": " Dana's\n numbers "}).topic, "Dana's ↵ numbers")
        for bad in ("x" * 41, "x" * 10_000, 7, ["x"], {"x": 1}, True):
            found = ask_frames.check_answer({"topic": bad})
            self.assertEqual((found.topic, found.topic_dropped), ("", True))
        self.assertEqual(ask_frames.check_answer({"topic": CONTROL}).topic[:8], "<U+202E>")

    def test_only_text_and_numbers_ever_reach_the_board(self) -> None:
        hostile = [table(title=HOSTILE_WORDS[0], unit="<b>", columns=["<i>a</i>", "b"], changed=0,
                         rows=[[HOSTILE_WORDS[2], float(12.5)]], change=HOSTILE_WORDS[3], source=HOSTILE_WORDS[4]),
                   chart(title=HOSTILE_WORDS[1], bars=[{"label": HOSTILE_WORDS[3], "value": -0.5}], changed=-1),
                   timeline(nodes=[{"when": HOSTILE_WORDS[3][:24], "words": HOSTILE_WORDS[0][:40], "state": ""}],
                            today=0),
                   maths(title=HOSTILE_WORDS[0], steps=[{"name": "<b>x</b>", "expr": ["frac", 1, 3]}])]
        for frame in hostile:
            with self.subTest(kind=frame["kind"]):
                built = ask_frames.build(checked(frame), frame_id="f1").messages[0]
                for leaf in leaves(built):
                    self.assertIn(type(leaf), (str, int, float, bool, type(None)))
                    if isinstance(leaf, float):
                        self.assertTrue(math.isfinite(leaf))
                text = protocol.check_message(built).text                 # the page's JSON: ASCII, no NaN
                self.assertTrue(text.isascii())


# --------------------------------------------------------------------------
# Each kind's checker
# --------------------------------------------------------------------------

class KindTests(unittest.TestCase):
    def test_a_table(self) -> None:
        frame = checked(table())
        self.assertEqual((frame.kind, frame.title, frame.meta, frame.key), ("table", "Budget lines", "USD",
                                                                            "table:budget lines"))
        self.assertEqual(frame.body, {"columns": ["Line", "Friday", "Today", "Change"],
                                      "rows": [["Travel", "1,200", "1,200", "0"], ["Software", "2,900", "3,400", "+500"],
                                               ["Events", "2,100", "2,100", "0"], ["Hardware", "800", "800", "0"]],
                                      "unit": "USD", "changed": 1, "change": "+500 since Friday",
                                      "source": "From Dana's mail, 8:12 AM"})
        short = checked(table(rows=[["Travel"]], changed=-1, change="ignored"))
        self.assertEqual(short.body["rows"], [["Travel", "", "", ""]])                # filled to the columns
        self.assertEqual(short.body["change"], "")                                     # no changed row: no words
        bare = checked({"kind": "table", "title": "T", "columns": ["a"], "rows": [["x"]]})
        self.assertEqual(bare.body, {"columns": ["a"], "rows": [["x"]], "unit": "", "changed": -1, "change": "",
                                     "source": ""})
        self.assertEqual(bare.meta, "")

    def test_a_chart(self) -> None:
        frame = checked(chart())
        self.assertEqual((frame.meta, frame.key), ("USD", "chart:q4 budget by line"))
        self.assertEqual([bar["text"] for bar in frame.body["bars"]], ["1.2k", "3.4k", "2.1k", "0.8k"])
        self.assertEqual((frame.body["changed"], frame.body["was"]), (1, 2900))
        self.assertEqual(frame.body["change"], "+500 Software · was 2.9k on Friday")
        plain = checked(chart(bars=[{"label": "A", "value": 3}, {"label": "B", "value": -5.5}], changed=-1, was=4))
        self.assertEqual([bar["text"] for bar in plain.body["bars"]], ["3", f"{MINUS}5.5"])
        self.assertEqual((plain.body["was"], plain.body["change"]), (None, ""))       # no changed bar: no ghost

    def test_a_timeline(self) -> None:
        frame = checked(timeline())
        self.assertEqual((frame.meta, frame.key), ("Due Thu 11:59 PM", "timeline:ling essay"))
        self.assertEqual(frame.body["today"], 1)
        self.assertEqual(frame.body["nodes"][0], {"when": "Mon", "words": "Outline", "state": "done"})
        no_due = checked(timeline(nodes=[{"when": "Wed", "words": "Draft", "state": "next"}, {"when": "Fri",
                                                                                             "words": "Essay"}]))
        self.assertEqual((no_due.meta, no_due.body["nodes"][1]["state"]), ("Next Wed", ""))
        self.assertEqual(checked(timeline(nodes=[{"when": "Fri", "words": "Essay"}], today=None)).meta, "")

    def test_maths(self) -> None:
        frame = checked(maths())
        self.assertEqual((frame.meta, frame.key), ("3 steps", "maths:solve 2x² − 8x + 6 = 0"))
        self.assertEqual(frame.body["steps"][0]["expr"],
                         ["=", ["+", ["pow", ["name", "x"], ["num", "2"]], ["*", ["num", f"{MINUS}4"], ["name", "x"]],
                                ["num", "3"]], ["num", "0"]])
        self.assertEqual(frame.body["answer"]["name"], "So")
        self.assertEqual(checked(maths(answer={"expr": 3400})).body["answer"], {"name": "", "expr": ["num", "3,400"]})
        self.assertEqual(checked(maths(steps=[{"expr": "θ"}], answer={"expr": "π"})).body["answer"]["expr"],
                         ["name", "π"])

    def test_keys_name_what_a_frame_is(self) -> None:
        self.assertEqual(ask_frames.frame_key("table", "  Budget   LINES "), "table:budget lines")
        self.assertNotEqual(checked(table()).key, checked(chart(title="Budget lines")).key)

    def test_the_frame_message(self) -> None:
        frame = checked(table())
        built = ask_frames.build(frame, frame_id="ask-1")
        message = built.messages[0]
        self.assertEqual({key: message[key] for key in ("type", "id", "kind", "title", "meta")},
                         {"type": "frame", "id": "ask-1", "kind": "table", "title": "Budget lines", "meta": "USD"})
        message["body"]["rows"][0][0] = "changed by the feed"
        self.assertEqual(frame.body["rows"][0][0], "Travel")                         # a copy: the answer's stays
        with self.assertRaises(ValueError):
            ask_frames.build(frame, frame_id="not an id!")
        for make in ALL:
            sent = ask_frames.build(checked(make()), frame_id="f1").messages[0]
            again = json.loads(protocol.check_message(sent).text)
            self.assertEqual(again["body"], sent["body"], make.__name__)

    def test_repr_holds_no_words(self) -> None:
        found = ask_frames.check_answer({"topic": "Zanzibar quartz", "frames": [table(title="Zanzibar quartz")]})
        self.assertNotIn("Zanzibar", repr(found))
        self.assertNotIn("Zanzibar", repr(found.frames[0]))


# --------------------------------------------------------------------------
# Numbers and maths' words
# --------------------------------------------------------------------------

class NumberTests(unittest.TestCase):
    def test_a_number_s_words(self) -> None:
        for value, words in ((0, "0"), (-0.0, "0"), (3400, "3,400"), (-4, f"{MINUS}4"), (0.25, "0.25"),
                             (0.1 + 0.2, "0.3"), (1 / 3, "0.3333333333"), (1234567.891, "1,234,567.891"),
                             (2.0, "2"), (1e-6, "0.000001"), (999_999_999_999_999, "999,999,999,999,999"),
                             (2.5, "2.5"), (0.125, "0.125")):
            self.assertEqual(ask_frames.format_number(value), words, value)
        for bad in (float("nan"), float("inf"), 10 ** 15, 1e-7, True, "1"):
            with self.assertRaises(ValueError):
                ask_frames.format_number(bad)

    def test_a_number_keeps_every_whole_digit(self) -> None:
        # Only a fraction is ever cut to SIGNIFICANT digits: a figure with cents never loses its last whole digits.
        for value, words in ((12345678901.5, "12,345,678,902"), (1234567890123.45, "1,234,567,890,123"),
                             (-12345678901.25, f"{MINUS}12,345,678,901"), (1234567890.5, "1,234,567,891"),
                             (123456789.125, "123,456,789.1"), (31940416666.67, "31,940,416,667")):
            self.assertEqual(ask_frames.format_number(value), words, value)
        # The review's sum: 383,285,000,000 ÷ 12 = 31,940,416,666.67, every whole digit kept in its line and its fact.
        frame = checked(maths(steps=[{"expr": ["=", "m", ["/", 383285000000, 12]]}],
                              answer={"expr": ["=", "m", 31940416666.67]}))
        self.assertEqual(ask_frames.maths_text(frame.body["steps"][0]["expr"]), "m = 383,285,000,000 ÷ 12")
        self.assertEqual(ask_frames.maths_text(frame.body["answer"]["expr"]), "m = 31,940,416,667")
        self.assertEqual(ask_frames.answer_fact(frame.body["answer"]["expr"]), "= 31,940,416,667")
        # Every number under the board's limit fits the page's words.
        limit = protocol.NUMBER_LIMIT
        values = [limit - 1, -(limit - 1), float(limit) - 0.125, 999_999_999_999_999.9, -999_999_999_999_999.9,
                  1e-6, -1e-6, 0.1 + 0.2, 1 / 3, -2 / 3]
        values += [sign * (10 ** power + fraction) for power in range(15) for fraction in (0.5, 0.123456789, 0.999999)
                   for sign in (1, -1) if 10 ** power + fraction < limit]
        for value in values:
            words = ask_frames.format_number(value)
            self.assertLessEqual(len(words), protocol.NUMBER_TEXT_CAP, (value, words))
            protocol.check_message(wire("maths", {"steps": [{"name": "", "expr": ["num", words]}],
                                                  "answer": {"name": "", "expr": ["num", words]}}))

    def test_a_chart_s_values_in_one_scale(self) -> None:
        self.assertEqual(ask_frames.chart_texts([1200, 3400, 2100, 800]), ["1.2k", "3.4k", "2.1k", "0.8k"])
        self.assertEqual(ask_frames.chart_texts([2_500_000, 750_000]), ["2.5M", "0.8M"])     # half up
        self.assertEqual(ask_frames.chart_texts([3, 5.5, 0.25]), ["3", "5.5", "0.25"])
        self.assertEqual(ask_frames.chart_texts([-1500, 500]), [f"{MINUS}1.5k", "0.5k"])

    def test_the_scale_is_the_one_the_largest_needs_as_shown(self) -> None:
        # Chosen after rounding: a largest bar that rounds up to a thousand of its scale reads in the next one.
        self.assertEqual(ask_frames.chart_texts([999_950, 10]), ["1.0M", "0.0M"])
        self.assertEqual(ask_frames.chart_texts([999_999.5]), ["1.0M"])
        self.assertEqual(ask_frames.chart_texts([-999_999, 100]), [f"{MINUS}1.0M", "0.0M"])
        self.assertEqual(ask_frames.chart_texts([999_949, 420_000]), ["999.9k", "420.0k"])      # still under: k
        self.assertEqual(ask_frames.chart_texts([999_950_000, 1]), ["1.0B", "0.0B"])
        self.assertEqual(ask_frames.chart_texts([999_960_000_000]), ["1.0T"])
        self.assertEqual(ask_frames.chart_texts([999_960_000_000_000]), ["1,000.0T"])          # T is the top
        frame = checked(chart(bars=[{"label": "Software", "value": 999_950}, {"label": "Travel", "value": 420_000}],
                              changed=-1))
        self.assertEqual([bar["text"] for bar in frame.body["bars"]], ["1.0M", "0.4M"])


class MathsWordsTests(unittest.TestCase):
    def words(self, raw: object) -> str:
        return ask_frames.maths_text(checked(maths(answer={"expr": raw})).body["answer"]["expr"])

    def test_the_design_s_working(self) -> None:
        frame = checked(maths())
        self.assertEqual([ask_frames.maths_text(step["expr"]) for step in frame.body["steps"]],
                         ["x² − 4x + 3 = 0", "(x − 1)(x − 3) = 0"])
        self.assertEqual(ask_frames.maths_text(frame.body["answer"]["expr"]), "x = 1 or x = 3")
        self.assertEqual(ask_frames.working_line(frame.body), "Divide by 2, then factor: (x − 1)(x − 3) = 0")

    def test_brackets_where_they_must_be_and_nowhere_else(self) -> None:
        for raw, words in ((["*", 2, ["+", "x", 1]], "2(x + 1)"), (["-", "a", ["+", "b", "c"]], "a − (b + c)"),
                           (["-", ["+", "a", "b"]], "−(a + b)"), (["pow", ["+", "x", 1], 2], "(x + 1)²"),
                           (["pow", -2, 2], f"({MINUS}2)²"), (["/", ["*", 6, "x"], ["*", 2, "x"]], "6x ÷ (2x)"),
                           (["frac", ["+", "x", 1], 2], "(x + 1)/2"), (["root", ["-", 64, 48]], "√(64 − 48)"),
                           (["-", ["-", "x"]], f"{MINUS}({MINUS}x)"), (["=", "x", ["or", 1, 2]], "x = (1 or 2)"),
                           (["+", "a", ["-", ["-", "b"]]], f"a − ({MINUS}b)"), (["*", 2, -3], f"2({MINUS}3)")):
            self.assertEqual(self.words(raw), words, raw)

    def test_signs_side_by_side_only_when_nothing_can_be_misread(self) -> None:
        for raw, words in ((["*", "x", "y"], "xy"), (["*", 2, "x"], "2x"), (["*", "rate", "time"], "rate × time"),
                           (["*", 2, 3], "2 × 3"), (["*", "x", 2], "x × 2"), (["*", 2, ["root", 3]], "2√3"),
                           (["*", ["root", 2], "x"], "√2 × x"), (["*", ["frac", 1, 2], "x"], "1/2 × x"),
                           (["*", ["pow", "x", 2], "y"], "x²y")):
            self.assertEqual(self.words(raw), words, raw)

    def test_a_negative_term_reads_as_a_take_away(self) -> None:
        self.assertEqual(self.words(["+", ["pow", "x", 2], ["*", -4, "x"], 3]), "x² − 4x + 3")
        self.assertEqual(self.words(["+", ["pow", "x", 2], ["-", ["*", 4, "x"]], 3]), "x² − 4x + 3")
        self.assertEqual(self.words(["+", "a", -2]), "a − 2")
        self.assertEqual(self.words(["+", -2, "a"]), f"{MINUS}2 + a")

    def test_powers_and_roots(self) -> None:
        self.assertEqual(self.words(["pow", "x", ["+", "n", 1]]), "x^(n + 1)")
        self.assertEqual(self.words(["pow", "x", "n"]), "xⁿ")
        self.assertEqual(self.words(["pow", "x", 10]), "x¹⁰")
        self.assertEqual(self.words(["root", 8, 3]), "³√8")

    def test_an_answer_s_fact(self) -> None:
        for raw, fact in ((3400, "= 3,400"), (["=", ["*", 12, 7], 84], "= 84"), (["or", ["=", "x", 1], ["=", "x", 3]],
                                                                                "x = 1 or x = 3"),
                          (["=", "x", "y"], "x = y")):
            self.assertEqual(ask_frames.answer_fact(checked(maths(answer={"expr": raw})).body["answer"]["expr"]),
                             fact)
        self.assertEqual(ask_frames.maths_text("not a tree"), "")


# --------------------------------------------------------------------------
# The wire: board.protocol's checkers for the four kinds
# --------------------------------------------------------------------------

def wire(kind: str, body: object, **changes: object) -> dict:
    message = {"type": "frame", "id": "f1", "kind": kind, "title": "x", "meta": "", "body": body}
    message.update(changes)
    return message


class WireTests(unittest.TestCase):
    def refused(self, kind: str, body: object) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(wire(kind, body))
        return caught.exception.reason

    def good(self, kind: str) -> dict:
        return ask_frames.build(checked({"table": table, "chart": chart, "timeline": timeline,
                                         "maths": maths}[kind]()), frame_id="f1").messages[0]["body"]

    def test_every_body_as_ask_frames_builds_it_passes(self) -> None:
        for kind in ask_frames.KINDS:
            body = self.good(kind)
            self.assertEqual(json.loads(protocol.check_message(wire(kind, body)).text)["body"], body)

    def test_what_python_refuses_on_the_wire(self) -> None:
        table_body, chart_body = self.good("table"), self.good("chart")
        timeline_body, maths_body = self.good("timeline"), self.good("maths")
        bad = {
            "table": [{**table_body, "rows": [["a", "b"]]}, {**table_body, "columns": []}, {**table_body, "rows": []},
                      {**table_body, "changed": 4}, {**table_body, "changed": True}, {**table_body, "html": ""},
                      {k: v for k, v in table_body.items() if k != "source"}, {**table_body, "rows": [[1, 2, 3, 4]]},
                      {**table_body, "rows": [["x" * 41, "", "", ""]]}, {**table_body, "rows": [["a"] * 4] * 13}],
            "chart": [{**chart_body, "bars": [{"label": "a", "value": float("nan"), "text": "1"}]},
                      {**chart_body, "bars": [{"label": "a", "value": 10 ** 15, "text": "1"}]},
                      {**chart_body, "bars": [{"label": "a", "value": True, "text": "1"}]},
                      {**chart_body, "bars": [{"label": "a", "value": 1, "text": "<b>1</b>"}]},
                      {**chart_body, "bars": [{"label": "a", "value": "1", "text": "1"}]},
                      {**chart_body, "changed": -1}, {**chart_body, "was": "2.9k"}, {**chart_body, "bars": []},
                      {**chart_body, "changed": 9}],
            "timeline": [{**timeline_body, "today": 4}, {**timeline_body, "nodes": []},
                         {**timeline_body, "nodes": [{"when": "", "words": "", "state": "late"}]},
                         {"nodes": timeline_body["nodes"]}],
            "maths": [{**maths_body, "answer": {"name": "", "expr": ["num", "1e5"]}},
                      {**maths_body, "answer": {"name": "", "expr": ["num", "<b>"]}},
                      {**maths_body, "answer": {"name": "", "expr": ["name", "x1"]}},
                      {**maths_body, "answer": {"name": "", "expr": ["frac", ["num", "1"]]}},
                      {**maths_body, "answer": {"name": "", "expr": ["script", ["num", "1"]]}},
                      {**maths_body, "answer": {"name": "", "expr": "x"}},
                      {**maths_body, "answer": {"name": "", "expr": json.loads(json.dumps(deep(7)).replace(
                          '"x"', '["name", "x"]'))}},
                      {**maths_body, "steps": []}, {"steps": maths_body["steps"]}],
        }
        for kind, bodies in bad.items():
            for body in bodies:
                with self.subTest(kind=kind, body=json.dumps(body)[:80]):
                    self.assertIn(self.refused(kind, body), (protocol.R_SHAPE, protocol.R_FIELD, protocol.R_TOO_LONG))

    def test_a_frame_of_65_nodes_is_refused_on_the_wire_too(self) -> None:
        line = ["+", *([["num", "1"]] * 8)]
        body = {"steps": [{"name": "", "expr": line}] * 7, "answer": {"name": "", "expr": ["num", "1"]}}
        protocol.check_message(wire("maths", body))                                  # 64
        body["answer"]["expr"] = ["-", ["num", "1"]]
        self.assertEqual(self.refused("maths", body), protocol.R_TOO_LONG)          # 65


# --------------------------------------------------------------------------
# The schema files, as cli passes them
# --------------------------------------------------------------------------

ASSETS = cli.ASSETS
# The keywords Ask's schemas used before part 6 (schema.json, research_schema.json and the properties cli adds): the
# Claude CLI takes them today, so the new fields use nothing else (no $ref, no anyOf / oneOf, no format or pattern).
KNOWN_KEYWORDS = {"$schema", "title", "type", "additionalProperties", "required", "properties", "items", "maxItems",
                  "maxLength", "description", "enum"}


def keywords(schema: object, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            if key == "properties":
                for child in value.values():
                    keywords(child, found)
            elif key == "items":
                keywords(value, found)
    return found


class SchemaTests(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((ASSETS / name).read_text(encoding="ascii"))

    def test_topic_and_frames_are_optional_and_come_last(self) -> None:
        for name, first in (("schema.json", "say"), ("research_schema.json", "answer")):
            schema = self.load(name)
            properties = list(schema["properties"])
            self.assertEqual(properties[0], first)                                   # still written first
            self.assertEqual(properties[-2:], ["topic", "frames"], name)
            self.assertNotIn("topic", schema["required"])
            self.assertNotIn("frames", schema["required"])
            self.assertFalse(schema["additionalProperties"])
        self.assertEqual(self.load("schema.json")["required"], ["say", "lines"])
        self.assertEqual(self.load("research_schema.json")["required"], ["answer", "sources", "suggestions"])

    def test_one_block_in_both_files(self) -> None:
        plain, web = self.load("schema.json")["properties"], self.load("research_schema.json")["properties"]
        self.assertEqual(plain["frames"], web["frames"])
        self.assertEqual(plain["topic"], web["topic"])

    def test_the_caps_are_the_board_s(self) -> None:
        topic = self.load("schema.json")["properties"]["topic"]
        frames = self.load("schema.json")["properties"]["frames"]
        item = frames["items"]
        props = item["properties"]
        self.assertEqual(topic["maxLength"], protocol.TOPIC_CAP)
        self.assertEqual(frames["maxItems"], ask_frames.FRAMES_CAP)
        self.assertEqual((item["additionalProperties"], item["required"]), (False, ["kind", "title"]))
        self.assertEqual(props["kind"]["enum"], list(ask_frames.KINDS))
        self.assertEqual(props["title"]["maxLength"], protocol.TITLE_CAP)
        self.assertEqual(props["unit"]["maxLength"], protocol.UNIT_CAP)
        self.assertEqual((props["columns"]["maxItems"], props["columns"]["items"]["maxLength"]),
                         (protocol.TABLE_COLUMNS_CAP, protocol.COLUMN_CAP))
        rows = props["rows"]
        self.assertEqual((rows["maxItems"], rows["items"]["maxItems"], rows["items"]["items"]["maxLength"]),
                         (protocol.TABLE_ROWS_CAP, protocol.TABLE_COLUMNS_CAP, protocol.CELL_CAP))
        bar = props["bars"]["items"]
        self.assertEqual((props["bars"]["maxItems"], bar["properties"]["label"]["maxLength"],
                          bar["properties"]["value"]["type"], bar["additionalProperties"]),
                         (protocol.BARS_CAP, protocol.LABEL_CAP, "number", False))
        node = props["nodes"]["items"]
        self.assertEqual((props["nodes"]["maxItems"], node["properties"]["when"]["maxLength"],
                          node["properties"]["words"]["maxLength"], node["properties"]["state"]["enum"]),
                         (protocol.NODES_CAP, protocol.WHEN_CAP, protocol.NODE_WORDS_CAP, list(protocol.NODE_STATES)))
        step = props["steps"]["items"]
        self.assertEqual((props["steps"]["maxItems"], step["properties"]["name"]["maxLength"], step["required"]),
                         (protocol.STEPS_CAP, protocol.STEP_NAME_CAP, ["expr"]))
        self.assertEqual(props["answer"]["required"], ["expr"])
        self.assertEqual((props["change"]["maxLength"], props["source"]["maxLength"]),
                         (protocol.CHANGE_CAP, protocol.SOURCE_CAP))
        self.assertEqual((props["changed"]["type"], props["today"]["type"], props["was"]["type"]),
                         ("integer", "integer", "number"))

    def test_a_cell_is_text_and_the_model_is_told_so(self) -> None:
        # The schema keeps a cell a string (no new schema feature: a type list was never tried with the CLI), and says
        # so in words, so the model writes "3,400" rather than 3400 and its answer is never sent back for a retry.
        for name in ("schema.json", "research_schema.json"):
            rows = self.load(name)["properties"]["frames"]["items"]["properties"]["rows"]
            self.assertEqual(rows["items"]["items"]["type"], "string", name)
            self.assertIn('each is short words or a figure written as text, like "3,400"', rows["description"], name)
        self.assertEqual(checked(table()).body["rows"][1][1:3], ["2,900", "3,400"])   # Python still takes numbers

    def test_only_keywords_ask_s_schemas_already_use(self) -> None:
        for name in ("schema.json", "research_schema.json"):
            self.assertLessEqual(keywords(self.load(name)), KNOWN_KEYWORDS, name)
            self.assertTrue((ASSETS / name).read_bytes().isascii())
            self.assertNotIn(b"\r", (ASSETS / name).read_bytes())

    def test_cli_passes_them_as_they_are(self) -> None:
        for allow_search in (False, True):
            for allow_web in (False, True):
                text = cli.schema_text(allow_search=allow_search, allow_web=allow_web)
                data = json.loads(text)
                self.assertEqual(data["properties"]["frames"], self.load("schema.json")["properties"]["frames"])
                self.assertNotIn(" ", text.split('"description"')[0])               # compact
        research_text = json.loads(cli.research_schema_text())
        self.assertEqual(research_text["properties"]["frames"], self.load("research_schema.json")["properties"]["frames"])
        for mode in ("plan", "prep", "nudge", "meeting", "desktop"):
            self.assertNotIn("frames", cli.mode_schema_text(mode))                 # the modes are as they were

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema is not installed here")
    def test_valid_draft_07_that_takes_a_good_answer_and_refuses_a_capped_one(self) -> None:
        import jsonschema

        good = {"say": "Software went up by 500, sir.", "question": "", "lines": [], "topic": "Dana's numbers",
                "frames": [table(), chart(), maths()]}
        good["frames"][0]["rows"] = [[str(cell) for cell in row] for row in good["frames"][0]["rows"]]
        for text in (cli.schema_text(allow_search=True, allow_web=True), cli.research_schema_text()):
            schema = json.loads(text)
            jsonschema.Draft7Validator.check_schema(schema)
        validator = jsonschema.Draft7Validator(json.loads(cli.schema_text(allow_search=False)))
        self.assertEqual(list(validator.iter_errors(good)), [])
        too_many = json.loads(json.dumps(good))
        too_many["frames"][0]["rows"] = [["a"]] * 13
        self.assertTrue(list(validator.iter_errors(too_many)))
        web = jsonschema.Draft7Validator(json.loads(cli.research_schema_text()))
        self.assertEqual(list(web.iter_errors({"answer": "x [1].", "sources": [], "suggestions": [],
                                               "topic": "Museum hours", "frames": [timeline()]})), [])


# --------------------------------------------------------------------------
# The prompts
# --------------------------------------------------------------------------

class PromptTests(unittest.TestCase):
    def section(self, path: Path) -> str:
        text = path.read_bytes().decode("ascii")
        self.assertNotIn("\r", text)
        part = text.split("\nFRAMES (topic, frames)\n")[1].split("\n\n")[0]
        self.assertLess(text.index("FRAMES (topic, frames)"), text.index("EXAMPLE (invented)"))
        return " ".join(part.split())

    def test_when_a_frame_helps_and_never_otherwise(self) -> None:
        for path, never in ((cli.PROMPT_FILE, 'never instead of the answer in "say"'),
                            (cli.RESEARCH_PROMPT_FILE, "never instead of the answer")):
            words = self.section(path)
            for rule in ('"frames": usually empty.', "numbers to compare (a table", "or a chart of one measure)",
                         "dates in order (a timeline)", "a worked sum (maths", "At most 3", "never for one number",
                         never, "never markup, links", 'Table cells are text: a figure too ("3,400").'):
                self.assertIn(rule, words, (path.name, rule))
        self.assertIn("only facts from the context", self.section(cli.PROMPT_FILE))
        self.assertIn("only facts from your sources", self.section(cli.RESEARCH_PROMPT_FILE))
        self.assertIn('x^2 - 4x + 3 = 0 is ["=", ["+", ["pow", "x", 2], ["*", -4, "x"], 3], 0]',
                      self.section(cli.PROMPT_FILE))

    def test_the_example_expression_is_one_the_board_takes(self) -> None:
        raw = ["=", ["+", ["pow", "x", 2], ["*", -4, "x"], 3], 0]
        frame = checked(maths(steps=[{"expr": raw}]))
        self.assertEqual(ask_frames.maths_text(frame.body["steps"][0]["expr"]), "x² − 4x + 3 = 0")

    def test_small(self) -> None:
        for path, cap in ((cli.PROMPT_FILE, 640), (cli.RESEARCH_PROMPT_FILE, 480)):
            text = path.read_bytes().decode("ascii")
            part = text.split("FRAMES (topic, frames)")[1].split("EXAMPLE (invented)")[0]
            self.assertLessEqual(len(part), cap, path.name)

    def test_the_agent_prompt_is_the_plain_prompt_then_its_own_words(self) -> None:
        plain, agent = cli.PROMPT_FILE.read_bytes(), cli.PROMPT_FILE_AGENT.read_bytes()
        self.assertTrue(agent.startswith(plain))
        self.assertTrue(agent[len(plain):].startswith(b"\nAGENT TASKS (agent_task)\n"))
        self.assertNotIn(b"FRAMES", agent[len(plain):])


# --------------------------------------------------------------------------
# Ask's reading of them
# --------------------------------------------------------------------------

PLAN = {"say": "Software went up by 500, sir; the rest is the same.", "question": "", "lines": [],
        "topic": "Dana's numbers", "frames": [table(), chart(), maths(steps=[{"expr": deep(7)}])]}


class ReadingTests(unittest.TestCase):
    INIT = stream.InitSummary("none", ("StructuredOutput",), 0, "m", "v", True)

    def test_a_plan_carries_them_checked(self) -> None:
        plan = stream.read_result(result_event(PLAN), self.INIT, allow_search=False)
        self.assertIsInstance(plan, stream.Plan)
        self.assertEqual(plan.say, PLAN["say"])
        self.assertEqual(plan.frames.topic, "Dana's numbers")
        self.assertEqual([frame.kind for frame in plan.frames.frames], ["table", "chart"])
        self.assertEqual(dict(plan.frames.dropped), {ask_frames.D_DEPTH: 1})
        self.assertNotIn("Dana", repr(plan))

    def test_a_bad_frame_never_garbles_the_plan(self) -> None:
        for frames in ("x" * 10_000, [{"kind": "table", "rows": [[float("nan")]]}], [None] * 100, {"a": 1}):
            with self.subTest(frames=repr(frames)[:30]):
                plan = stream.read_result(result_event({**PLAN, "frames": frames, "topic": 7}), self.INIT,
                                          allow_search=False)
                self.assertIsInstance(plan, stream.Plan)
                self.assertEqual(plan.frames.frames, ())
                self.assertTrue(plan.frames.topic_dropped)
        plain = stream.read_result(result_event({"say": "x", "question": "", "lines": []}), self.INIT,
                                   allow_search=False)
        self.assertFalse(plain.frames.given)

    def test_a_research_answer_carries_them_checked(self) -> None:
        data = {"answer": "Opens at 10 [1].", "sources": [], "suggestions": [], "topic": "Museum hours",
                "frames": [timeline(), "junk"]}
        answer = research.answer_from(data)
        self.assertEqual(answer.answer, "Opens at 10 [1].")
        self.assertEqual(answer.frames.topic, "Museum hours")
        self.assertEqual([frame.kind for frame in answer.frames.frames], ["timeline"])
        self.assertEqual(dict(answer.frames.dropped), {ask_frames.D_SHAPE: 1})
        self.assertNotIn("Museum", repr(answer))
        self.assertIsNotNone(research.answer_from({**data, "frames": 7, "topic": ["x"]}))


class PlannerFramesTests(PlannerTestCase):
    def test_an_answer_s_frames_reach_its_outcome_and_the_log_holds_counts_only(self) -> None:
        secret = "Zanzibar"
        plan = dict(PLAN, topic=f"{secret} numbers", frames=[table(title=f"{secret} lines"), chart(),
                                                             maths(steps=[{"expr": deep(7)}])])
        ask = self.make(plan_stream(plan))
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan("did Dana send the numbers?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.topic, f"{secret} numbers")
        self.assertEqual([frame.kind for frame in outcome.frames], ["table", "chart"])
        self.assertEqual(outcome.frames[0].title, f"{secret} lines")
        text = "\n".join(logs.output)
        self.assertIn("Ask: 2 frame(s) to show (table, chart), 1 dropped (depth 1), topic given", text)
        self.assertNotIn(secret, text)
        self.assertNotIn(secret, repr(outcome))
        self.assertNotIn(secret, outcome.summary())

    def test_an_answer_without_them_logs_nothing_of_them(self) -> None:
        ask = self.make(plan_stream({"say": "It's at two.", "question": "", "lines": []}))
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan("what's on Friday?")
        self.assertEqual((outcome.topic, outcome.frames), ("", ()))
        self.assertNotIn("frame(s) to show", "\n".join(logs.output))

    def test_a_web_research_answer_s_frames_reach_its_outcome(self) -> None:
        lines = recorded("research_success")
        last = json.loads(lines[-1])
        last["structured_output"] = dict(last["structured_output"], topic="Museum hours",
                                         frames=[timeline(title="Opening days"), {"kind": "chart"}])
        lines = [*lines[:-1], json.dumps(last)]
        ask = self.make(lines)
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan(FORCED_COMMAND)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.topic, "Museum hours")
        self.assertEqual([frame.title for frame in outcome.frames], ["Opening days"])
        self.assertIn("Research: 1 frame(s) to show (timeline), 1 dropped (field 1), topic given",
                      "\n".join(logs.output))

    def test_a_frame_nested_ten_thousand_deep_as_json_text_loses_only_itself(self) -> None:
        plan = dict(PLAN, say=SAY, lines=[MOVE_LINE], frames=[table(), chart(), maths(steps=[{"expr": "DEEP"}])])
        lines = deep_lines(plan_stream(plan))
        self.assertEqual(sum(DEEP_TEXT in line for line in lines), 2)        # the assistant line and the result line
        ask = self.make(lines)
        stream_view = live.LiveStream(keep_text=True)
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan(COMMAND, live=stream_view.task(live.TASK_ASK, "request"))
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.say, SAY)
        self.assertEqual(len(outcome.cards), 1)                                     # the line's card
        self.assertEqual([frame.kind for frame in outcome.frames], ["table", "chart"])
        self.assertIn("Ask: 2 frame(s) to show (table, chart), 1 dropped (depth 1), topic given",
                      "\n".join(logs.output))
        steps = stream_view.snapshot()[0].steps
        blocks = [block for step in steps for block in step.blocks if block.label == "Planner's reply (raw)"]
        self.assertEqual(len(blocks), 1)
        self.assertLess(blocks[0].chars, 20_000)                                    # the LIVE block stays small

    def test_a_web_research_frame_nested_ten_thousand_deep_loses_only_itself(self) -> None:
        lines = recorded("research_success")
        call, last = json.loads(lines[5]), json.loads(lines[-1])
        frames = [timeline(title="Opening days"), maths(steps=[{"expr": "DEEP"}])]
        call["message"]["content"][0]["input"] = dict(call["message"]["content"][0]["input"], frames=frames)
        last["structured_output"] = dict(last["structured_output"], topic="Museum hours", frames=frames)
        lines = deep_lines([*lines[:5], json.dumps(call), lines[6], json.dumps(last)])
        self.assertEqual(sum(DEEP_TEXT in line for line in lines), 2)
        ask = self.make(lines)
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan(FORCED_COMMAND)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.say, RESEARCH_ANSWER["answer"])
        self.assertEqual([source.url for source in outcome.research.sources], [VISIT_URL, HOURS_URL])
        self.assertEqual([frame.title for frame in outcome.frames], ["Opening days"])
        self.assertIn("Research: 1 frame(s) to show (timeline), 1 dropped (depth 1), topic given",
                      "\n".join(logs.output))


# The model's own JSON text for a maths expr 10 000 deep, as the CLI prints it on a line (never a Python object: the
# real reading path, stream.parse_event, is the one that must not lose the answer).
DEEP_TEXT = '["-", ' * 10_000 + '"x"' + "]" * 10_000


def deep_lines(lines: list[str]) -> list[str]:
    return [line.replace('"DEEP"', DEEP_TEXT) for line in lines]


if __name__ == "__main__":
    unittest.main()
