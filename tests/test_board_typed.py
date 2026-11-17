"""The new look, part 6 on the board: Ask's typed frames (table, chart, timeline, maths) from the answer to the page and
the glance tiles. Offscreen; the web engine never starts here (tests/test_board_lockdown.py's "typed" case runs the real
page): the board is tests.board_fakes' stand-in (a real BoardView whose page is a plain widget: every message checked
and kept).

Covered: each kind's satellite fact (board.facts); what each kind says for "changed" (board.feed.substance: its own
data, never the clock's - a timeline's "today" or "tomorrow", nor where TODAY stands) and a frame built again from its
key changed only when what it says differs; the glance tiles of each kind (their words, inside the tile, the rows a
short tile leaves out, the answer never; private: the table's headline and maths' answer with its working in a line);
the page's lists and caps the same as Python's; an Ask answer with frames forming one group headed by its topic (the
first frame's title without one), each frame keyed by its kind and title, asking again lighting only what changed;
an instant sum's working as a maths frame of its own group (none for "12 × 7"); the logs holding no words.
"""

from __future__ import annotations

import json
import logging
import re
import unittest
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from briefing_reader import holo, instant, instant_maths
from briefing_reader.board import ask_frames, facts, feed, glance, protocol
from tests import test_desk
from tests.test_ask_frames import chart, checked, maths, table, timeline
from tests.test_desk import DeskApp
from tests.ui_fakes import settle

ASSETS = Path(protocol.__file__).resolve().parent / "assets"


def setUpModule() -> None:
    test_desk.setUpModule()          # the QApplication and the fonts


def message(frame: dict, frame_id: str = "f1") -> dict:
    """The frame message as the page gets it (built by board.ask_frames, checked by board.protocol)."""
    built = ask_frames.build(checked(frame), frame_id=frame_id).messages[0]
    return json.loads(protocol.check_message(built).text)


class FactTests(unittest.TestCase):
    def test_each_kind_s_fact(self) -> None:
        self.assertEqual(facts.fact_of(message(table())), "4 rows · 1 changed")
        self.assertEqual(facts.fact_of(message(table(changed=-1))), "4 rows")
        self.assertEqual(facts.fact_of(message(chart())), "+500 Software")
        self.assertEqual(facts.fact_of(message(chart(changed=-1))), "Top: Software 3.4k")
        self.assertEqual(facts.fact_of(message(timeline())), "Next: Full draft Wed")
        self.assertEqual(facts.fact_of(message(timeline(nodes=[{"when": "Thu", "words": "Essay", "state": "due"}],
                                                        today=0))), "Due: Essay Thu")
        self.assertEqual(facts.fact_of(message(timeline(nodes=[{"when": "Thu", "words": "Essay"}] * 2, today=-1))),
                         "2 dates")
        self.assertEqual(facts.fact_of(message(maths())), "x = 1 or x = 3")
        self.assertEqual(facts.fact_of(message(maths(answer={"expr": ["=", ["+", 2900, 500], 3400]}))), "= 3,400")
        changed = dict(message(table()), changed=True)
        self.assertEqual(facts.fact_of(changed), "Changed · 4 rows · 1 changed")


class SubstanceTests(unittest.TestCase):
    def test_what_each_kind_says(self) -> None:
        same = feed.substance
        self.assertEqual(same(message(table())), same(message(table(source="From Dana's mail, 9:02 AM",
                                                                     change="up 500"))))   # its own data only
        self.assertNotEqual(same(message(table())), same(message(table(rows=[["Travel", 1200, 1300, 100]] * 4))))
        self.assertNotEqual(same(message(table())), same(message(table(changed=2))))
        self.assertEqual(same(message(chart())), same(message(chart(change="+500 Software, was 2.9k yesterday"))))
        self.assertNotEqual(same(message(chart())), same(message(chart(was=2800))))
        self.assertNotEqual(same(message(chart())), same(message(chart(bars=[{"label": "Travel", "value": 1300}]
                                                                            * 4))))
        # A timeline: never the clock's words, never where TODAY stands; its own words and states do count.
        tomorrow = timeline(nodes=[{"when": "Tomorrow 5 PM", "words": "Essay", "state": "due"}], today=0)
        today = timeline(nodes=[{"when": "Today 5 PM", "words": "Essay", "state": "due"}], today=1)
        self.assertEqual(same(message(tomorrow)), same(message(today)))
        self.assertNotEqual(same(message(today)), same(message(timeline(
            nodes=[{"when": "Today 5 PM", "words": "Essay", "state": "done"}], today=1))))
        self.assertNotEqual(same(message(timeline())), same(message(timeline(
            nodes=[{"when": "Mon", "words": "Outline", "state": "done"}]))))
        self.assertEqual(same(message(maths())), same(message(maths())))
        self.assertNotEqual(same(message(maths())), same(message(maths(answer={"expr": ["=", "x", 2]}))))

    def test_what_the_owner_sees_is_what_counts(self) -> None:
        # Part 6 fixes (board 2): a typed frame's title as its key folds it, and a chart's values and old value as the
        # words it shows them in (3400.0 is "3.4k" as 3400 is); a real change still counts.
        same = feed.substance
        for make in (table, chart, timeline, maths):
            plain = make()
            with self.subTest(kind=plain["kind"]):
                self.assertEqual(same(message(plain)), same(message(make(title=plain["title"].upper()))))
                self.assertEqual(same(message(plain)), same(message(make(title=plain["title"].title()))))
                self.assertNotEqual(same(message(plain)), same(message(make(title=plain["title"] + " too"))))
        floats = chart(bars=[{"label": bar["label"], "value": float(bar["value"])} for bar in chart()["bars"]],
                       was=2900.0)
        self.assertEqual(same(message(chart())), same(message(floats)))
        moved = chart(bars=[{**bar, "value": 3600} if bar["label"] == "Software" else bar for bar in chart()["bars"]])
        self.assertNotEqual(same(message(chart())), same(message(moved)))                   # 3.4k to 3.6k
        self.assertNotEqual(same(message(chart())), same(message(chart(was=2700.0))))        # was 2.9k, now 2.7k

    def test_the_clock_s_own_words_in_cells_columns_and_labels_count_for_nothing(self) -> None:
        # Part 6 fixes (board 3): as in a timeline, "Tomorrow" that became "Today", or "2 days ago" that became "3 days
        # ago", in a table's cell or column or a chart's label is the clock moving, not the frame; its own words count.
        same = feed.substance

        def bills(due: str, last: str, rent: str = "1,200") -> dict:
            return table(title="Bills", unit="", columns=["Bill", "Amount", "Due", "Last reminder"], changed=-1,
                         rows=[["Rent", rent, due, last], ["Power", "80", "Fri 20 Nov", "never"]])

        self.assertEqual(same(message(bills("Tomorrow", "2 days ago"))), same(message(bills("Today", "3 days ago"))))
        self.assertEqual(same(message(bills("Due tomorrow", "in 2 h"))), same(message(bills("Due today", "in 1 h"))))
        self.assertNotEqual(same(message(bills("Tomorrow", "2 days ago"))), same(message(bills("Sat 21 Nov",
                                                                                               "2 days ago"))))
        self.assertNotEqual(same(message(bills("Today", "2 days ago"))), same(message(bills("Today", "2 days ago",
                                                                                            rent="1,250"))))
        yesterday = table(columns=["Line", "Yesterday", "Today", "Change"])
        self.assertEqual(same(message(yesterday)), same(message(table(columns=["Line", "2 days ago", "Tomorrow",
                                                                               "Change"]))))
        self.assertNotEqual(same(message(yesterday)), same(message(table(columns=["Line", "Before", "After",
                                                                                 "Change"]))))
        labels = [{"label": "Dana, in 2 h", "value": 1200}, {"label": "Sam, tomorrow", "value": 3400}]
        later = [{"label": "Dana, in 1 h", "value": 1200}, {"label": "Sam, today", "value": 3400}]
        other = [{"label": "Ana, in 1 h", "value": 1200}, {"label": "Sam, today", "value": 3400}]
        self.assertEqual(same(message(chart(bars=labels, changed=-1))), same(message(chart(bars=later, changed=-1))))
        self.assertNotEqual(same(message(chart(bars=labels, changed=-1))), same(message(chart(bars=other, changed=-1))))

    def test_a_frame_built_again_from_its_key_changes_only_when_what_it_says_differs(self) -> None:
        board = feed.BoardFeed()
        first = checked(table())
        key = first.key
        frame_id = board.frame_id(1, "ask:0", "", key)
        board.put(1, "ask:0", ask_frames.build(first, frame_id=frame_id).messages, when="9:41 AM", topic="Dana's",
                  key=key)
        again = board.frame_id(2, "ask:0", "", key)
        self.assertEqual(again, frame_id)                                    # the same frame: built again in place
        self.assertEqual(board.put(2, "ask:0", ask_frames.build(checked(table(source="Elsewhere")),
                                                                frame_id=again).messages, key=key), [])
        sent = board.put(2, "ask:0", ask_frames.build(checked(table(rows=[["Travel", 1, 2, 3]] * 4)),
                                                      frame_id=again).messages, key=key)
        self.assertEqual([item["changed"] for item in sent if item.get("type") == "frame"], [True])
        self.assertEqual(board.take_changed(), [frame_id])
        self.assertEqual(board.group_header(board.group_of(frame_id))[1], "Dana's")


class GlanceTests(unittest.TestCase):
    def layout(self, frame: dict, width: float = 448, height: float | None = None, **kwargs) -> glance.TileLayout:
        return glance.tile_layout(message(frame), width, height, **kwargs)

    def test_each_kind_s_words_inside_its_tile(self) -> None:
        for frame, words in ((table(), ["Budget lines", "USD", "Line", "Friday", "Software", "2,900", "3,400", "+500",
                                        "+500", "since Friday", "From Dana's mail, 8:12 AM"]),
                             (chart(), ["Q4 budget by line", "Travel", "Software", "3.4k", "0.8k", "+500",
                                        "Software · was 2.9k on Friday"]),
                             (timeline(), ["LING essay", "Due Thu 11:59 PM", "Mon", "Outline", "Done", "Wed",
                                           "Full draft", "Next", "Thu 11:59 PM", "Essay", "Due", "Today"]),
                             (maths(), ["Solve 2x² − 8x + 6 = 0", "3 steps", "Divide by 2", "x² − 4x + 3 = 0",
                                        "Factor", "(x − 1)(x − 3) = 0", "So", "x = 1 or x = 3", "1", "2", "3"])):
            for width in (448, 320 if frame["kind"] == "table" else 260):    # a table is full width: never paired
                with self.subTest(kind=frame["kind"], width=width):
                    layout = self.layout(frame, width)
                    shown = " ".join(layout.words().split())              # wrapped lines read on
                    shown += "\n" + layout.words()
                    for word in words:
                        self.assertIn(word.upper() if word.upper() in shown else word, shown)
                    for op in layout.ops:
                        self.assertGreaterEqual(op.rect.left(), 0, op.kind)
                        self.assertLessEqual(op.rect.right(), layout.width + 0.5, (op.kind, op.lines))
                        self.assertLessEqual(op.rect.bottom(), layout.height + 0.5, op.kind)
                    self.assertFalse(layout.clipped)

    def test_a_short_tile_leaves_rows_out_and_never_the_answer(self) -> None:
        full = self.layout(table(rows=[["Line", 1, 2, 3]] * 12, changed=-1))
        short = self.layout(table(rows=[["Line", 1, 2, 3]] * 12, changed=-1), height=200)
        self.assertGreater(short.more, 0)
        self.assertIn(f"+{short.more} MORE", short.words())
        self.assertLess(short.height, full.height)
        steps = maths(steps=[{"name": f"Step {n}", "expr": ["=", "x", n]} for n in range(1, 8)])
        cut = self.layout(steps, height=190)
        self.assertGreater(cut.more, 0)
        self.assertIn("x = 1 or x = 3", cut.words())                       # the answer stays

    def test_private_the_table_s_headline_and_maths_answer(self) -> None:
        headline = self.layout(table(change="+500 · the other 3 lines unchanged"), private=True)
        words = " ".join(headline.words().split())
        self.assertIn("2,900", words)
        self.assertIn("3,400", words)
        self.assertIn("the other 3 lines unchanged", words)
        self.assertNotIn("Travel", words)                                    # the changed row only
        rows = self.layout(table(changed=-1), private=True)
        self.assertIn("Travel", rows.words())                                # no changed row: the rows
        answer = self.layout(maths(), private=True)
        said = " ".join(answer.words().split())
        self.assertIn("x = 1 or x = 3", said)
        self.assertIn("Divide by 2, then factor: (x − 1)(x − 3) = 0", said)
        self.assertNotIn("x² − 4x + 3 = 0", said)

    def test_private_the_table_s_headline_takes_its_figures_by_column(self) -> None:
        # Part 6 fixes (review 6, board 4): the last two plain figures made an unsigned change cell the "now" figure -
        # "3,400 › 500", "3,400 › 17%". The two columns before the change column (signed, or headed Change) are the
        # figures; when that is not without doubt the rows show.
        def headline(frame: dict, **kwargs) -> list[str]:
            return [line for op in self.layout(frame, private=True, **kwargs).ops if op.kind == "text"
                    and op.font == "headline" for line in op.lines]

        for change in ("+500", "500", "17%"):
            rows = [["Travel", 1200, 1200, 0], ["Software", 2900, 3400, change], ["Events", 2100, 2100, 0]]
            for screen in (glance.BOARD, glance.SECOND):
                with self.subTest(change=change, screen=screen):
                    self.assertEqual(headline(table(rows=rows), screen=screen), ["2,900", "3,400"])
        unheaded = table(columns=["Line", "Friday", "Today", "Diff"], rows=[["Software", 2900, 3400, "500"]], changed=0)
        self.assertEqual(headline(unheaded), ["2,900", "3,400"])                 # "Diff" heads a change column too
        signed = table(columns=["Line", "Friday", "Today", "Moved"], rows=[["Software", 2900, 3400, "−500"]],
                       changed=0)
        self.assertEqual(headline(signed), ["2,900", "3,400"])                   # a signed cell names it
        two = table(columns=["Line", "Before", "After"], rows=[["Software", 2900, 3400]], changed=0)
        self.assertEqual(headline(two), ["2,900", "3,400"])                      # the only two figures
        three = table(columns=["Line", "Q1", "Q2", "Q3"], rows=[["Software", 100, 200, 300]], changed=0)
        self.assertEqual(headline(three), [])                                     # which two? the rows show
        self.assertIn("Q2", self.layout(three, private=True).words().upper())
        near = table(columns=["Line", "Change", "Friday", "Today"], rows=[["Software", "+500", 2900, 3400]], changed=0)
        self.assertEqual(headline(near), [])                                      # no two figures before it
        self.assertEqual(glance.headline_figures(["Line", "Friday", "Today", "Change"],
                                                 ["Software", "2,900", "3,400", "17%"]), ("2,900", "3,400"))
        self.assertIsNone(glance.headline_figures(["Line", "Friday", "Today", "Change"],
                                                  ["Software", "two", "3,400", "+500"]))

    def test_a_timeline_in_a_row_or_one_node_a_row(self) -> None:
        row = self.layout(timeline(), 448)
        rows = self.layout(timeline(), 260)
        many = self.layout(timeline(nodes=[{"when": f"Day {n}", "words": f"Step {n}"} for n in range(6)], today=2), 448)
        def tops(layout: glance.TileLayout, word: str) -> list[float]:
            return [rect.top() for rect, text in layout.texts() if text == word]
        self.assertEqual(len({tops(row, "MON")[0], tops(row, "WED")[0]}), 1)    # side by side
        self.assertLess(tops(rows, "MON")[0], tops(rows, "WED")[0])              # one under the other
        self.assertLess(tops(many, "DAY 0")[0], tops(many, "DAY 5")[0])

    def test_painted(self) -> None:
        image = QImage(488, 1200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(holo.VOID))
        painter = QPainter(image)
        y = 10.0
        for frame in (table(), chart(), timeline(), maths()):
            height = glance.tile_height(message(frame), 448)
            glance.paint_tile(painter, QRectF(20, y, 448, height), message(frame), ground=True)
            y += height + 12
        painter.end()
        hot = QColor(holo.HOLO)
        found = any(QColor(image.pixel(x, yy)).name() == hot.name() for x in range(20, 468, 2)
                    for yy in range(10, int(y), 2))
        self.assertTrue(found)                                               # the changed bar, painted


class PageListsTests(unittest.TestCase):
    def test_the_page_checks_with_the_same_caps_and_lists(self) -> None:
        js = (ASSETS / "board.js").read_text(encoding="utf-8")

        def caps(name: str) -> dict[str, int]:
            found = re.search(r"var %s = \{([^}]*)\}" % name, js).group(1)
            return {key: int(value) for key, value in re.findall(r"(\w+): (\d+)", found)}

        self.assertEqual(caps("TABLE"), {"columns": protocol.TABLE_COLUMNS_CAP, "rows": protocol.TABLE_ROWS_CAP,
                                         "column": protocol.COLUMN_CAP, "cell": protocol.CELL_CAP,
                                         "unit": protocol.UNIT_CAP, "change": protocol.CHANGE_CAP,
                                         "source": protocol.SOURCE_CAP})
        self.assertEqual(caps("CHART"), {"bars": protocol.BARS_CAP, "label": protocol.LABEL_CAP,
                                         "text": protocol.NUMBER_TEXT_CAP})
        self.assertEqual(caps("TIMELINE"), {"nodes": protocol.NODES_CAP, "when": protocol.WHEN_CAP,
                                            "words": protocol.NODE_WORDS_CAP})
        self.assertEqual(caps("MATHS"), {"steps": protocol.STEPS_CAP, "name": protocol.STEP_NAME_CAP,
                                         "depth": protocol.MATHS_DEPTH, "nodes": protocol.MATHS_NODES,
                                         "text": protocol.NUMBER_TEXT_CAP})
        self.assertIn("var NUMBER_LIMIT = 1e15;", js)
        self.assertEqual(protocol.NUMBER_LIMIT, 10 ** 15)
        states = re.search(r"var NODE_STATES = \[([^\]]*)\]", js).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w*)'", states)), protocol.NODE_STATES)
        ops = re.findall(r"\['([^']+)', (\d), (\d)\]", re.search(r"MATHS_OPS = Object.create\(null\);(.*?)forEach",
                                                                  js, re.DOTALL).group(1))
        self.assertEqual({op: (int(low), int(high)) for op, low, high in ops}, protocol.MATHS_OPS)
        for name, pattern in (("NUMBER_TEXT", protocol._NUMBER_TEXT), ("VALUE_TEXT", protocol._VALUE_TEXT),  # noqa: SLF001
                              ("MATHS_NAME", protocol._MATHS_NAME)):  # noqa: SLF001
            source = re.search(r"var %s = /\^(.*?)\$/;" % name, js).group(1)
            self.assertEqual(source.replace("\\/", "/"), pattern.pattern.replace("{1,%d}" % protocol.NAME_LETTERS,
                                                                                "{1,12}"), name)
        for kind in ask_frames.KINDS:
            self.assertIn(f"KINDS.{kind} = function", js)
        self.assertIn("var SMALL_KINDS = ['todos', 'timeline', 'chart', 'maths'];", js)     # a table never pairs
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("outerHTML", js)
        for glyph in ("table", "chart", "timeline", "maths"):
            self.assertIn(glyph, holo.ICONS)
            self.assertEqual(glance.GLYPHS[glyph], glyph)
        self.assertIn("delta", holo.FILLED_ICONS)

    def test_no_class_of_the_typed_frames_is_one_of_the_frame_s_own(self) -> None:
        """A class the four renderers make is never a class of the frame's chrome (its corners are "tl", "tr", "bl",
        "br": a timeline once called "tl" took the top-left bracket's place in the flow)."""
        js = (ASSETS / "board.js").read_text(encoding="utf-8")

        def classes(part: str) -> set[str]:
            found = re.findall(r"make\('[a-z0-9]+', '([^']*)'", part) + re.findall(r"glyph\('[a-z-]+', '([^']*)'", part)
            return {word for words in found for word in words.split()}

        typed = js.split("// ---- part 6, Ask's typed frames (DESIGN-SPEC 3.3)")[1].split("\n  var root = null;")[0]
        typed += js.split("// ---- part 6's helpers")[1].split("function placePicture(rec)")[0]
        chrome = js.split("function build(id) {")[1].split("function makeBody(message) {")[0]
        corners = set(re.findall(r"'(\w\w)'", re.search(r"\[('tl', 'tr', 'bl', 'br')\]", chrome).group(1)))
        mine = {word for word in classes(typed) if not word.startswith("t-")}    # (a type role is meant to be shared)
        self.assertIn("tline", mine)
        self.assertEqual(mine & (classes(chrome) | corners), set())
        css = (ASSETS / "board.css").read_text(encoding="utf-8")
        rules = re.findall(r"([^{}\n]*\.tl(?![\w-])[^{]*)\{", css)
        self.assertEqual(len(rules), 3)                                       # (the canvas, part 2: its tick's place)
        for rule in rules:                                                    # only the corners' own rules
            self.assertRegex(rule.strip(), r"^(:root\.canvas #board \.frame )?\.fx-(bracket|tick)\.tl$")

    def test_one_block_for_the_under_300_px_rules(self) -> None:
        css = (ASSETS / "board.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("@container frame (max-width: 299px)"), 1)
        block = css.split("@container frame (max-width: 299px)")[1].split("\n}\n")[0]
        for rule in (".mx {", ".mx-k {", ".mx-e {", ".ch-bars {", ".td-row {", ".f-title {"):
            self.assertIn(rule, block)


class InstantWorkingTests(unittest.TestCase):
    def test_a_sum_with_a_step_between_gets_its_working(self) -> None:
        for words, lines, answer in (("(3+4)^2", ["(3 + 4)²", "7²"], "49"),
                                     ("2 + 3 * 4", ["2 + 3 × 4", "2 + 12"], "14"),
                                     ("15% of 80", ["15/100 × 80", "0.15 × 80"], "12"),
                                     ("1/3 + 1/6", ["1 ÷ 3 + 1 ÷ 6", "1/3 + 1/6"], "0.5"),
                                     ("-(3 + 4) * 2", ["−(3 + 4) × 2", "−7 × 2"], "−14")):
            with self.subTest(words=words):
                calc = instant_maths.calculate(words)
                frame = checked(calc.working)
                self.assertEqual([ask_frames.maths_text(step["expr"]) for step in frame.body["steps"]], lines)
                self.assertEqual(ask_frames.maths_text(frame.body["answer"]["expr"]), answer)
        self.assertEqual(checked(instant_maths.calculate("(3+4)^2").working).title, "(3 + 4)²")
        up = checked(instant_maths.percent("increase 80 by 15%").working)
        self.assertEqual([ask_frames.maths_text(step["expr"]) for step in up.body["steps"]],
                         ["80(1 + 15/100)", "80(1 + 0.15)", "80 × 1.15"])

    def test_no_working_without_a_step_between_or_when_rounded(self) -> None:
        for words in ("12 x 7", "2 + -5", "square root of 144", "1+2+3+4+5+6+7+8+9", "2^0.5", "√2 + 1"):
            calc = instant_maths.calculate(words)
            self.assertIsNotNone(calc, words)
            self.assertIsNone(calc.working, words)
        self.assertIsNone(instant_maths.percent("80 is what percent of 200").working)
        self.assertEqual(instant_maths.calculate("12 x 7"), instant_maths.Calc("12 × 7", "84", spoken="84"))

    def test_no_working_for_a_decimal_the_board_would_round(self) -> None:
        # A decimal that ends past the board's significant digits gets no working: shown as a fraction it would not
        # read as the owner's own number or as Jarvis's answer (which the conversation still says as before).
        self.assertEqual(instant_maths.WORKING_DIGITS, ask_frames.SIGNIFICANT)
        for words, said in (("12345.678901 + 2 x 3", "12,351.678901"), ("12500.75 x 1.0825 + 40", "13,572.061875"),
                            ("250000 x 1.045 x 1.045 x 1.045 - 250000", "35,291.53125"),
                            ("123456789.012 + 1 x 2", "123,456,791.012")):
            with self.subTest(words=words):
                calc = instant_maths.calculate(words)
                self.assertIsNotNone(calc)
                self.assertEqual((calc.value, calc.exact), (said, True))
                self.assertIsNone(calc.working)
        within = instant_maths.calculate("12345.67891 + 2 x 3")              # ten significant digits: its working
        frame = checked(within.working)
        self.assertEqual([ask_frames.maths_text(step["expr"]) for step in frame.body["steps"]],
                         ["12,345.67891 + 2 × 3", "12,345.67891 + 6"])
        self.assertEqual(ask_frames.maths_text(frame.body["answer"]["expr"]), "12,351.67891")

    def test_the_reply_carries_it_and_says_nothing_of_it(self) -> None:
        from datetime import datetime

        reply = instant.understand("what's (3+4)^2", now=datetime(2026, 11, 17, 9, 41))
        self.assertIsInstance(reply, instant.InstantReply)
        self.assertEqual(reply.working["kind"], "maths")
        self.assertEqual(repr(reply), "InstantReply(maths)")
        plain = instant.understand("what's 12 x 7", now=datetime(2026, 11, 17, 9, 41))
        self.assertIsNone(plain.working)


class DeskFramesTests(DeskApp):
    @staticmethod
    def frames_outcome(**fields) -> object:
        values = dict(topic="Dana's numbers", frames=tuple(checked(frame) for frame in (table(), chart(), maths())),
                      say="Software went up by 500, sir; the rest is the same.")
        values.update(fields)
        return DeskApp.outcome(**values)

    def test_an_answer_with_frames_forms_a_group_with_its_topic(self) -> None:
        self.make()
        self.answer("Did Dana's numbers change?", self.frames_outcome())
        board = self.board()
        groups = board.groups_sent()
        self.assertEqual(len(groups), 1)
        (group,) = groups.values()
        self.assertEqual(group["topic"], "Dana's numbers")
        frames = board.frames_sent()
        self.assertEqual(sorted(frame["kind"] for frame in frames.values()), ["chart", "maths", "table"])
        self.assertEqual(set(board.board_order()), set(frames))
        self.assertEqual({frame["group"] for frame in frames.values()}, set(groups))
        keys = {self.desk().feed._frames[frame_id].key for frame_id in frames}    # noqa: SLF001
        self.assertEqual(keys, {"table:budget lines", "chart:q4 budget by line", "maths:solve 2x² − 8x + 6 = 0"})
        self.assertEqual(self.reading.board_count(), 3)

    def test_without_a_topic_the_first_frame_s_title(self) -> None:
        self.make()
        self.answer("Plot it.", self.frames_outcome(topic="", frames=(checked(chart()),)))
        (group,) = self.board().groups_sent().values()
        self.assertEqual(group["topic"], "Q4 budget by line")

    def test_asking_again_lights_only_what_changed(self) -> None:
        self.make()
        self.answer("Did Dana's numbers change?", self.frames_outcome())
        board = self.board()
        before = dict(board.frames_sent())
        changed = (checked(table(rows=[["Travel", 1200, 1200, 0], ["Software", 2900, 3600, "+700"],
                                       ["Events", 2100, 2100, 0], ["Hardware", 800, 800, 0]])),
                   checked(chart()), checked(maths()))
        self.answer("And now?", self.frames_outcome(frames=changed))
        after = board.frames_sent()
        self.assertEqual(set(after), set(before))                          # the same frames, in their places
        lit = sorted(frame["kind"] for frame in after.values() if frame["changed"])
        self.assertEqual(lit, ["table"])

    def test_asking_again_in_other_capitals_or_as_floats_lights_nothing(self) -> None:
        # Part 6 fixes (board 2): the same chart asked again as "Q4 Budget by Line" with its values written 3400.0 is
        # what the owner already sees: nothing is sent again (no CHANGED, no "Changed ·" fact) and nothing is heard. A
        # real change of a value still lights, with its sound.
        self.make()
        heard: list[tuple] = []
        self.c.look.on_frame_changed = lambda *args: heard.append(args)
        self.answer("How's the Q4 budget?", self.frames_outcome(frames=(checked(chart()),)))
        board = self.board()
        (frame_id,) = board.frames_sent()
        holo.set_motion_level(holo.MOTION_LIVELY)                          # (the harness's cleanup sets it back)
        start = len(board.sent)
        floats = chart(title="Q4 Budget by Line", was=2900.0,
                       bars=[{"label": bar["label"], "value": float(bar["value"])} for bar in chart()["bars"]])
        self.answer("And now?", self.frames_outcome(frames=(checked(floats),)))
        self.assertEqual([message for message in board.sent[start:] if message.get("type") == "frame"], [])
        self.assertEqual(heard, [])
        self.assertFalse(board.frames_sent()[frame_id]["changed"])
        moved = chart(bars=[{**bar, "value": 3600} if bar["label"] == "Software" else bar for bar in chart()["bars"]])
        self.answer("And now?", self.frames_outcome(frames=(checked(moved),)))
        sent = [message for message in board.sent[start:] if message.get("type") == "frame"]
        self.assertEqual([(message["id"], message["changed"]) for message in sent], [(frame_id, True)])
        self.assertTrue(sent[0]["fact"].startswith("Changed · "))
        self.assertEqual(heard, [()])

    def test_an_instant_sum_s_working_goes_up_in_a_group_of_its_own(self) -> None:
        from datetime import datetime

        self.make()
        reply = instant.understand("what's (3+4)^2", now=datetime(2026, 11, 17, 9, 41))
        self.c._on_instant(reply, "what's (3+4)^2", False, 12)
        settle(4)
        frames = self.board().frames_sent()
        (frame,) = frames.values()
        self.assertEqual((frame["kind"], frame["title"]), ("maths", "(3 + 4)²"))
        self.assertEqual(len(self.board().groups_sent()), 1)
        plain = instant.understand("what's 12 x 7", now=datetime(2026, 11, 17, 9, 41))
        self.c._on_instant(plain, "what's 12 x 7", False, 9)
        settle(4)
        self.assertEqual(len(self.board().frames_sent()), 1)                 # no working: nothing more

    def test_the_logs_hold_no_words(self) -> None:
        self.make()
        secret = "Zanzibar"
        frames = (checked(table(title=f"{secret} lines", columns=[secret, "b"], rows=[[secret, 1]], changed=-1)),
                  checked(maths(title=secret)))
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            self.answer(f"{secret}?", self.frames_outcome(topic=secret, frames=frames, say=f"{secret}, sir."))
            logging.getLogger("briefing_reader").debug("marker")
        self.assertEqual(len(self.board().frames_sent()), 2)
        self.assertFalse(any(secret in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
