"""The new look, part 5c: the lit frame and its cue line (DESIGN-SPEC 1, 3.4 "Lit", 4.2, 5 and 8). Offscreen; the web
engine never starts here: the board is tests.board_fakes' stand-in (a real BoardView whose page is a plain widget: every
message checked and kept). tests/test_board_lockdown.py's "lit" case runs the same flow in the real page.

Covered: which frame a sentence names (board.lit: its own words, whole words, case-insensitive; a distinctive word of 4+
letters no other frame shares; shared, common and short words never count; a tie or no match lights nothing), the
sentences of an utterance and the one being said from the voice's timeline, where a sentence ends in the current turn;
the lit and backdrop and big messages and the landed and big requests with their hostile shapes, and the page's lists
matching Python's; the lit state's life in the app (the sentence being said lights its frame, the next one moves it,
the speech ending, a stop, a mute, another screen and the frame leaving clear it), every ring's core glow halving and
the HUD's following, the cue line's native part (from beside the sentence's end to the seam and along it to the
frame's anchor, never over the view; hidden while the board scrolls and back without the draw-in; its motion lively,
calm and off), the ring's halved glow and its paint budget; no words in the logs. Part 5c fixes: a short form's full
stop ending its sentence before a capital, a word said otherwise never found inside another, open big dropping the
native part at once, the HUD's ring and the Dock's painted 36 px ring following the lit state, and the sentences already
said reading in INK_3 in the current turn (eased when lively, back on each way the speech ends). Round 3: a "?" or "!"
after a short form always ends its sentence, and a short form glued to a dash or a bracket is still one. Round 4: an
ellipsis, a closing quote or a bracket after a short form's full stop changes nothing (only a "?" or "!" ends it there).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import desk_cue, desk_view, holo, ui, voice_fx
from briefing_reader import orb as orb_mod
from briefing_reader.board import lit, protocol
from briefing_reader.board.frames import Built
from tests import board_fakes
from tests.test_desktop_hud import HudApp
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
ASSETS = Path(__file__).resolve().parent.parent / "briefing_reader" / "board" / "assets"
SAY = ("Three meetings today, sir - the first at ten. Dana sent the budget numbers at 8:12, and a reply waits for your "
       "OK. The budget sheet has one line changed.")
MAIL = {"kind": "mail_thread", "title": "Re: Budget numbers", "meta": "Work · 8:12 AM",
        "body": {"sender": "Dana Whitfield", "time": "8:12 AM", "text": "Here are the Q4 numbers."},
        "waits": "Reply waits on Approvals"}
DAY = {"kind": "calendar", "title": "Today", "meta": "Tue 17 Nov",
       "body": {"first": 480, "last": 1080, "now_min": 581, "ticks": [], "spans": [], "now": "9:41", "rows": [
           {"time": "10:00-10:30", "title": "AI-Trader check-in", "meta": "in 19 min", "state": "next"},
           {"time": "10:30-1:00", "title": "Free", "meta": "2 h 30", "state": "free"},
           {"time": "1:00-2:00", "title": "Study group", "meta": "Library 2F", "state": ""}]}}
BUDGET = {"kind": "text", "title": "Q4 budget by line", "meta": "2 sources",
          "body": {"text": "Software rose by 500.", "rows": [{"lead": "1", "text": "Q4 sheet", "meta": "sheets.example.edu"}]}}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_lit", "-platform", "offscreen"])
    fonts()


def jpeg_url(width: int = 64, height: int = 40, *, noise: bool = False) -> str:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#22303f"))
    if noise:                                       # a picture that does not compress much (a bigger JPEG)
        import random

        rand = random.Random(7)
        for y in range(height):
            for x in range(width):
                image.setPixelColor(x, y, QColor(rand.randrange(256), rand.randrange(256), rand.randrange(256)))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 80)
    buffer.close()
    return protocol.PICTURE_PREFIX + base64.b64encode(bytes(data.data())).decode("ascii")


def on_void(widget: QWidget) -> QImage:
    """``widget`` as it paints itself (its own paintEvent and its children's) over the window's VOID, never a palette's
    ground."""
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(holo.VOID))
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def timeline_for(text: str, sentence: int) -> tuple[voice_fx.VoiceTimeline, int]:
    """The voice's timeline for ``text`` (a word every 300 ms) and a position inside its ``sentence``-th sentence."""
    spans = lit.sentence_spans(text)
    words, at, position = [], 0, -1
    for index, (start, end) in enumerate(spans):
        for word, _a, _b in lit.tokens(text[start:end]):
            words.append((at, at + 280, word))
            if index == sentence and position < 0:
                position = at + 140
            at += 300
    return voice_fx.VoiceTimeline(words=tuple(words), duration_ms=at), position


# --------------------------------------------------------------------------
# Which frame a sentence names (board.lit, pure)
# --------------------------------------------------------------------------

class SentenceTests(unittest.TestCase):
    def test_the_sentences_of_an_utterance(self) -> None:
        spans = lit.sentence_spans(SAY)
        self.assertEqual([SAY[a:b] for a, b in spans], [
            "Three meetings today, sir - the first at ten.",
            "Dana sent the budget numbers at 8:12, and a reply waits for your OK.",
            "The budget sheet has one line changed."])
        text = "Dr. Ruiz moved it to 2.9k at 8:12 a.m. today. Next one?\nAnd this"
        self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)],
                         ["Dr. Ruiz moved it to 2.9k at 8:12 a.m. today.", "Next one?", "And this"])
        self.assertEqual(lit.sentence_spans(""), [])
        self.assertEqual(lit.sentence_at(spans, -1), -1)
        self.assertEqual(lit.sentence_at(spans, 0), 0)
        self.assertEqual(lit.sentence_at(spans, spans[1][0]), 1)
        self.assertEqual(lit.sentence_at(spans, 10_000), 2)

    def test_a_short_form_ends_its_sentence_before_a_capital(self) -> None:
        # Part 5c fixes: "no.", "Inc.", "etc." ... ended no sentence, so the next one's frame lit early (or nothing).
        for text, sentences in (
                ("Dana's reply says no. The study group moved to noon.",
                 ["Dana's reply says no.", "The study group moved to noon."]),
                ("It came from Acme Inc. The study group moved.", ["It came from Acme Inc.", "The study group moved."]),
                ("Pens, paper etc. The study group moved.", ["Pens, paper etc.", "The study group moved."]),
                ("Ask Acme Co. Then lunch.", ["Ask Acme Co.", "Then lunch."]),
                ("Send it to Acme Ltd. The rest can wait.", ["Send it to Acme Ltd.", "The rest can wait."]),
                ("Meet me on Main St. The study group moved.", ["Meet me on Main St.", "The study group moved."]),
                ("Meet at 8 a.m. The bus leaves at nine.", ["Meet at 8 a.m.", "The bus leaves at nine."]),
                ("Room No. 5 is free. Next one?", ["Room No. 5 is free.", "Next one?"]),          # never an end
                ("Acme Inc. shipped it, etc. and more.", ["Acme Inc. shipped it, etc. and more."]),
                ("Red vs. blue at 8 a.m. today.", ["Red vs. blue at 8 a.m. today."]),
                ("Ask Dr. Ruiz. Mr. Lee too.", ["Ask Dr. Ruiz.", "Mr. Lee too."])):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], sentences)
        said = "Dana's reply says no. The study group moved to noon."
        self.assertEqual([lit.match(said[a:b], {"f1": MAIL, "f2": DAY}) for a, b in lit.sentence_spans(said)],
                         ["f1", "f2"])                                  # the mail first, then the calendar

    def test_e_g_i_e_and_a_short_form_before_a_symbol_or_a_day_never_end_a_sentence(self) -> None:
        # Part 5c fixes, round 2 (lit-read 2): round 1's rule ended a sentence after any short form before anything but
        # a lower-case word or a number - "e.g. Dana", "9 a.m. Tuesday", "Inc. - the client" were split in two.
        for text in ("Ask someone, e.g. Dana, to check the budget numbers.",
                     "Send it to the team, i.e. Dana and Priya.",
                     "Pick a slot (e.g. Tuesday at 10) and tell Dana.",
                     "Your call is at 9 a.m. Tuesday with Dana.",
                     "The review moved to 3 p.m. Nov 18, sir.",
                     "Acme Inc. — the client — sent the budget.",
                     "Acme Inc. - the client - sent the budget.",
                     "It came from Acme Inc. & Co. last night.",
                     "Pens, paper etc. — all on your list."):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], [text])
        for text, sentences in (("It came at 9 a.m. Dana sent it.", ["It came at 9 a.m.", "Dana sent it."]),
                                ("Meet at 8 a.m. The bus leaves at nine.", ["Meet at 8 a.m.", "The bus leaves at nine."]),
                                ("Fly to St. Louis on Friday.", ["Fly to St.", "Louis on Friday."])):   # (St.: decided)
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], sentences)

    def test_a_question_or_an_exclamation_after_a_short_form_always_ends_its_sentence(self) -> None:
        # Part 5c fixes, round 3 (lit-read 0): round 2's day-and-month rule took ".?" and ".!" after "a.m" / "p.m" for a
        # full stop, so the question and the next sentence were one ("9 a.m.? Tuesday's ...": the mail stayed lit).
        for text, sentences in (
                ("Shall I answer Dana by 9 a.m.? Tuesday's study group can wait.",
                 ["Shall I answer Dana by 9 a.m.?", "Tuesday's study group can wait."]),
                ("Was it 3 p.m.! May was busy.", ["Was it 3 p.m.!", "May was busy."]),
                ("Is it Acme Inc.? the client says so.", ["Is it Acme Inc.?", "the client says so."]),
                ("Ask Dr.? Ruiz knows.", ["Ask Dr.?", "Ruiz knows."])):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], sentences)
        said = "Shall I answer Dana by 9 a.m.? Tuesday's study group can wait."
        self.assertEqual([lit.match(said[a:b], {"f1": MAIL, "f2": DAY}) for a, b in lit.sentence_spans(said)],
                         ["f1", "f2"])                                  # the mail, then the calendar

    def test_an_ellipsis_after_a_short_form_s_full_stop_changes_nothing(self) -> None:
        # Part 5c fixes, round 4 (lit-read 0): round 3 kept the short-form rules for an end made of full stops only, so
        # ".…" (a full stop, then the ellipsis character) always ended the sentence - "Ask Dr.… Ruiz" split after
        # "Dr.…", where the same text with three dots stays one. Only a "?" or "!" takes an end out of the rules.
        for text in ("Ask Dr.… Ruiz knows.", "Pens, paper, etc.… and more.", "Ask Dr... Ruiz knows."):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], [text])
        text = "I see.… Then we go."                                         # a plain word before the stop: it ends
        self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], ["I see.…", "Then we go."])
        for text, sentences in (("Ask Dr.? Ruiz knows.", ["Ask Dr.?", "Ruiz knows."]),   # round 3's: still two
                                ("Was it 3 p.m.! May was busy.", ["Was it 3 p.m.!", "May was busy."])):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], sentences)

    def test_a_quote_or_a_bracket_after_a_short_form_s_full_stop_changes_nothing(self) -> None:
        # Part 5c fixes, round 4 (lit-read 1): nothing pinned the closing quotes and brackets after a short form's full
        # stop - an end such as '."' or '.)' must still go through the short-form rules.
        for text in ('He said "Dr." Ruiz knows.', "Pick a slot (e.g.) Tuesday."):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], [text])
        text = 'Is it 9 a.m.?" Tuesday is clear.'                            # a "?" before the quote: it ends
        self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], ['Is it 9 a.m.?"', "Tuesday is clear."])

    def test_a_short_form_glued_to_a_dash_or_a_bracket_is_still_one(self) -> None:
        # Part 5c fixes, round 3 (lit-read 1): the word before a stop was the whole token after the last space, so a dash
        # glued before "e.g." or "Dr." hid them and the sentence ended there.
        for text in ("Pick a slot—e.g. Tuesday at 10—and tell Dana.",
                     "Call him—Dr. Ruiz—today.",
                     "Pick a slot/i.e. Tuesday, and tell Dana.",
                     "Ask the lab [Dr. Ruiz] first.",
                     "Meet at 8—a.m. today, sir."):
            with self.subTest(text=text):
                self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)], [text])
        text = "Lunch is at noon—sharp. The study group moved."              # a word that is no short form still ends it
        self.assertEqual([text[a:b] for a, b in lit.sentence_spans(text)],
                         ["Lunch is at noon—sharp.", "The study group moved."])

    def test_each_sentence_is_found_after_the_one_before_it(self) -> None:
        # Part 5c fixes, round 2 (lit-read 0): each sentence was looked for from the turn's start, so one that repeats
        # earlier words ("That's all.") was found inside the first sentence.
        said = "That's all I found in your mail, sir. The calendar is clear after 3. That's all."
        spans = lit.sentence_spans(said)
        self.assertEqual(lit.sentence_places(said, said), spans)            # word for word: its own spans
        self.assertEqual(lit.sentence_places(said, "Earlier: " + said), [(a + 9, b + 9) for a, b in spans])
        short = "That's all. That's all I found, sir."                       # the turn holds it after words of its own
        before = "Earlier I said that's all. "
        self.assertEqual(lit.sentence_places(short, before + short.replace(", sir", " — sir")),
                         [(len(before), len(before) + 11), (len(before) + 12, len(before) + len(short) + 1)])
        turn = said.replace("after 3", "after three")                       # said otherwise: found in order
        places = lit.sentence_places(said, turn)
        self.assertEqual(places[0], (0, turn.index(" The")))
        self.assertEqual(places[1], (-1, -1))                               # not there as said
        self.assertEqual(places[2], (turn.rindex("That's all."), len(turn)))   # after the first, never inside it
        self.assertEqual(lit.sentence_places("", turn), [])
        self.assertEqual(lit.sentence_places(said, ""), [(-1, -1)] * 3)

    def test_a_word_said_otherwise_is_never_found_inside_another(self) -> None:
        # Part 5c fixes: the voice's "eight" for "8" was found inside "eighteen", a sentence too far on.
        text = "Your call with Dana is at 8. The study group has eighteen people."
        said = [(0, 100, "Your"), (100, 200, "call"), (200, 300, "with"), (300, 400, "Dana"), (400, 500, "is"),
                (500, 600, "at"), (600, 700, "eight"), (700, 800, "The")]
        offset = lit.spoken_offset(text, said, 800, 650)
        self.assertEqual(offset, text.index("at 8"))                    # still on "at": the word before it
        self.assertEqual(lit.sentence_at(lit.sentence_spans(text), offset), 0)
        self.assertEqual(lit.spoken_offset(text, said, 800, 750), text.index("The"))   # and on from there
        self.assertEqual(lit.spoken_offset("Lunch at 8:12 today.", [(0, 100, "Lunch"), (100, 200, "at"),
                                                                    (200, 300, "8:12")], 300, 250),
                         "Lunch at 8:12 today.".index("8:12"))          # its own words, whole ("8", "12")

    def test_how_far_the_voice_is(self) -> None:
        timeline, position = timeline_for(SAY, 1)
        offset = lit.spoken_offset(SAY, timeline.words, timeline.duration_ms, position)
        self.assertEqual(offset, SAY.index("Dana"))                    # the word being said, found in the words
        self.assertEqual(lit.spoken_offset(SAY, timeline.words, 0, 0), 0)
        self.assertEqual(lit.spoken_offset(SAY, (), 10_000, 5_000), round(len(SAY) / 2))   # the duration's share
        self.assertEqual(lit.spoken_offset(SAY, (), 0, 5_000), -1)      # nothing known: no sentence (never a guess)
        self.assertEqual(lit.spoken_offset(SAY, timeline.words, timeline.duration_ms, -1), -1)
        said = [(0, 200, "Dana"), (200, 400, "sent"), (400, 600, "eight"), (600, 800, "twelve"), (800, 900, "and")]
        self.assertEqual(lit.spoken_offset("Dana sent at 8:12, and more.", said, 900, 850),
                         "Dana sent at 8:12, and more.".index("and"))   # a word said otherwise is passed by

    def test_where_a_sentence_ends_in_the_turn(self) -> None:
        turn = "Here is your day. " + SAY + " Anything else?"
        second = "Dana sent the budget numbers at 8:12, and a reply waits for your OK."
        end = lit.locate(second, turn)
        self.assertEqual(turn[:end], turn[:turn.index("OK.") + 3])      # past its own full stop
        self.assertEqual(lit.locate("dana  sent the BUDGET numbers at 8:12 and a reply waits for your ok", turn), end)
        spoken = "Dana sent them at 8:12, and a reply waits for your OK."   # said otherwise: its last words
        self.assertEqual(lit.locate(spoken, turn), end)
        self.assertEqual(lit.locate("Nothing of this is there.", turn), -1)
        self.assertEqual(lit.locate("", turn), -1)
        self.assertEqual(lit.locate("Anything else?", turn, after=end), len(turn))


class MatchTests(unittest.TestCase):
    FRAMES = {"f1": MAIL, "f2": DAY, "f3": BUDGET}

    def test_the_frame_each_sentence_names(self) -> None:
        names = [lit.match(SAY[a:b], self.FRAMES) for a, b in lit.sentence_spans(SAY)]
        self.assertEqual(names, ["f2", "f1", "f3"])                     # today; Dana, numbers; line

    def test_shared_words_never_count(self) -> None:
        own = lit.distinctive(self.FRAMES)
        self.assertNotIn("budget", own["f1"])                           # the mail and the chart both say it
        self.assertNotIn("budget", own["f3"])
        self.assertEqual(lit.match("The budget is fine.", self.FRAMES), "")

    def test_common_and_short_words_never_count(self) -> None:
        frames = {"a": {"kind": "text", "title": "Lunch with Ana", "body": {"text": ""}},
                  "b": {"kind": "text", "title": "Room 2F", "body": {"text": ""}}}
        self.assertEqual(lit.match("I will be with you shortly.", frames), "")   # "with": a common word
        self.assertEqual(lit.match("Ana called.", frames), "")                   # 3 letters: never enough
        self.assertEqual(lit.match("Your lunch is at noon.", frames), "a")
        self.assertEqual(lit.match("It is in 2F.", frames), "")

    def test_whole_words_case_insensitive(self) -> None:
        self.assertEqual(lit.match("DANA wrote back.", self.FRAMES), "f1")
        self.assertEqual(lit.match("Dana's numbers came in.", self.FRAMES), "f1")
        self.assertEqual(lit.match("Danae wrote back.", self.FRAMES), "")
        self.assertEqual(lit.match("Lines are fine.", self.FRAMES), "")          # "lines" is not "line"

    def test_a_tie_or_no_match_lights_nothing(self) -> None:
        self.assertEqual(lit.match("The study and the line both moved.", self.FRAMES), "")   # one word each
        self.assertEqual(lit.match("The study group and Q4 line moved.", self.FRAMES), "f2")  # two beat one
        self.assertEqual(lit.match("Anything else, sir?", self.FRAMES), "")
        self.assertEqual(lit.match("", self.FRAMES), "")
        self.assertEqual(lit.match("Dana wrote.", {}), "")

    def test_each_kind_s_own_words(self) -> None:
        self.assertEqual(lit.frame_words(MAIL), {"re", "budget", "numbers", "dana", "whitfield"})
        self.assertEqual(lit.frame_words({"kind": "mail_thread", "title": "Hi", "body": {"sender": "To ana@example.edu"}}),
                         {"hi", "ana", "example", "edu"})
        self.assertEqual(lit.frame_words(DAY), {"today", "ai", "trader", "check", "in", "study", "group"})   # no "free"
        self.assertEqual(lit.frame_words({"kind": "mail_list", "title": "Follow-ups", "body": {"rows": [
            {"sender": "Ana Ruiz", "subject": "Lab moved", "meta": "WAITING"}]}}),
            {"follow", "ups", "ana", "ruiz", "lab", "moved"})
        self.assertEqual(lit.frame_words({"kind": "todos", "title": "To-dos", "body": {"rows": [
            {"text": "Book the dentist", "meta": "THU", "state": "open"}]}}), {"to", "dos", "book", "the", "dentist"})
        self.assertEqual(lit.frame_words({"kind": "picture", "title": "City Hack", "body": {
            "caption": "city-hack.example.org"}}), {"city", "hack", "example", "org"})
        self.assertEqual(lit.frame_words(BUDGET), {"q4", "budget", "by", "line", "sheets", "example", "edu"})
        self.assertEqual(lit.frame_words("not a frame"), frozenset())

    def test_part_6_s_kinds_own_words(self) -> None:
        """Ask's typed frames: a table's title and column words, a chart's title and bar labels, a timeline's title
        and node words, maths' title alone (never a cell, a value, a when or an expression)."""
        table = {"kind": "table", "title": "Budget lines", "body": {
            "columns": ["Line", "Friday", "Today"], "rows": [["Travel", "1,200", "1,300"]], "unit": "USD",
            "changed": 0, "change": "+100 Travel", "source": "From Dana's mail"}}
        chart = {"kind": "chart", "title": "Q4 spend", "body": {
            "bars": [{"label": "Software", "value": 3400, "text": "3.4k"}, {"label": "Hardware", "value": 800,
                                                                           "text": "0.8k"}],
            "unit": "", "changed": -1, "was": None, "change": ""}}
        timeline = {"kind": "timeline", "title": "LING essay", "body": {
            "nodes": [{"when": "Mon", "words": "Outline", "state": "done"}, {"when": "Thursday", "words": "Essay",
                                                                            "state": "due"}], "today": 1}}
        maths = {"kind": "maths", "title": "Solve for x", "body": {
            "steps": [{"name": "Factor", "expr": ["name", "quartic"]}], "answer": {"name": "", "expr": ["num", "3"]}}}
        self.assertEqual(lit.frame_words(table), {"budget", "lines", "line", "friday", "today"})
        self.assertEqual(lit.frame_words(chart), {"q4", "spend", "software", "hardware"})
        self.assertEqual(lit.frame_words(timeline), {"ling", "essay", "outline"})
        self.assertEqual(lit.frame_words(maths), {"solve", "for", "x"})
        frames = {"t": table, "c": chart, "l": timeline, "m": maths}
        self.assertEqual(lit.match("Software went up by five hundred.", frames), "c")
        self.assertEqual(lit.match("The outline is done.", frames), "l")
        self.assertEqual(lit.match("Friday's lines are in.", frames), "t")
        self.assertEqual(lit.match("Solve it with the quadratic.", frames), "m")
        self.assertEqual(lit.match("Travel went up.", frames), "")             # a cell's words never light it

    def test_nothing_here_is_qt(self) -> None:
        self.assertNotIn("PySide6", Path(lit.__file__).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The new messages and requests (board.protocol) and the page's lists
# --------------------------------------------------------------------------

class ProtocolTests(unittest.TestCase):
    def refused(self, message: object, known: set[str] | None = None) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(message, known if known is not None else {"f1"})
        return caught.exception.reason

    def refused_request(self, kind: str, payload: str) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_request(kind, payload, {"f1"})
        return caught.exception.reason

    def test_lit_says_whether_the_cue_line_comes(self) -> None:
        self.assertEqual(json.loads(protocol.check_message({"type": "lit", "id": "f1", "cue": True}, {"f1"}).text),
                         {"type": "lit", "id": "f1", "cue": True})
        self.assertEqual(json.loads(protocol.check_message({"type": "lit", "id": "f1"}, {"f1"}).text),
                         {"type": "lit", "id": "f1"})                    # as before when not given
        self.assertEqual(json.loads(protocol.check_message({"type": "lit", "id": "", "cue": True}).text)["cue"], False)
        self.assertEqual(self.refused({"type": "lit", "id": "f1", "cue": "yes"}), protocol.R_SHAPE)
        self.assertEqual(self.refused({"type": "lit", "id": "f1", "cue": True, "x": 1}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "lit", "id": "f9"}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "lit"}), protocol.R_FIELD)

    def test_a_backdrop_is_one_jpeg_under_the_picture_s_caps(self) -> None:
        src = jpeg_url()
        checked = protocol.check_message({"type": "backdrop", "src": src})
        self.assertEqual(json.loads(checked.text), {"type": "backdrop", "src": src})
        big = protocol.PICTURE_PREFIX + "A" * (protocol.PICTURE_CAP - len(protocol.PICTURE_PREFIX) - 4) + "/9j/"
        self.assertEqual(self.refused({"type": "backdrop", "src": big + "AAAA"}), protocol.R_PICTURE)   # over 410 KB
        jpeg = b"\xff\xd8\xff" + b"\x00" * (protocol.PICTURE_BYTES + 3)
        self.assertEqual(self.refused({"type": "backdrop", "src": protocol.PICTURE_PREFIX
                                       + base64.b64encode(jpeg).decode("ascii")}), protocol.R_PICTURE)  # over 300 KB
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        for src_ in (png, "https://example.com/x.jpg", protocol.PICTURE_PREFIX + "!!!!",
                     protocol.PICTURE_PREFIX + base64.b64encode(b"GIF89a").decode("ascii"), 7):
            self.assertEqual(self.refused({"type": "backdrop", "src": src_}), protocol.R_PICTURE, src_)
        self.assertEqual(self.refused({"type": "backdrop", "src": src, "id": "f1"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "backdrop"}), protocol.R_FIELD)
        large = jpeg_url(260, 200, noise=True)
        self.assertGreater(len(large), protocol.MESSAGE_CAP)
        protocol.check_message({"type": "backdrop", "src": large})      # the image never counts against 32 KB

    def test_big_opens_a_frame_the_app_sent_with_the_board_s_box_or_closes(self) -> None:
        good = {"type": "big", "state": "open", "id": "f1", "x": 356, "y": 12, "w": 503, "h": 624}
        self.assertEqual(json.loads(protocol.check_message(good, {"f1"}).text), good)
        self.assertEqual(json.loads(protocol.check_message({"type": "big", "state": "close", "id": ""}).text),
                         {"type": "big", "state": "close", "id": ""})
        self.assertEqual(protocol.check_message({"type": "big", "state": "close"}).frame_id, "")
        self.assertEqual(self.refused({**good, "id": "f9"}), protocol.R_ID)
        self.assertEqual(self.refused({**good, "id": "<b>"}), protocol.R_ID)
        for name, value in (("x", -1), ("y", "12"), ("w", 0), ("h", True), ("x", protocol.BIG_LIMIT + 1),
                            ("w", 1.5)):
            self.assertEqual(self.refused({**good, name: value}), protocol.R_SHAPE, name)
        self.assertEqual(self.refused({key: value for key, value in good.items() if key != "h"}), protocol.R_FIELD)
        self.assertEqual(self.refused({**good, "then": 1}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "big", "state": "close", "x": 0}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "big", "state": "half"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "big"}), protocol.R_FIELD)

    def test_the_landed_and_big_requests(self) -> None:
        self.assertEqual(protocol.check_request("landed", '{"id":"f1","step":"outline"}', {"f1"}),
                         protocol.Request("landed", "f1", step="outline"))
        self.assertEqual(protocol.check_request("landed", '{"id":"f1","step":"done"}', {"f1"}).step, "done")
        for step in ("back", "laid", "shut"):
            self.assertEqual(protocol.check_request("big", json.dumps({"step": step}), {"f1"}),
                             protocol.Request("big", step=step))
        self.assertEqual(self.refused_request("landed", '{"id":"f9","step":"done"}'), protocol.R_ID)
        self.assertEqual(self.refused_request("landed", '{"id":"f1","step":"half"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("landed", '{"id":"f1"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("big", '{"step":"open"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("big", '{"step":"back","id":"f1"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("big", "[]"), protocol.R_SHAPE)
        self.assertEqual(self.refused_request("big", "nope"), protocol.R_JSON)
        self.assertEqual(protocol.kind_word("landed"), "landed")

    def test_the_page_checks_with_the_same_lists(self) -> None:
        js = (ASSETS / "board.js").read_text(encoding="utf-8")
        steps = re.search(r"var LANDED_STEPS = \[([^\]]*)\]", js).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", steps)), protocol.LANDED_STEPS)
        self.assertIn(f"BIG_LIMIT = {protocol.BIG_LIMIT}", js)
        for name in ("HANDLERS.backdrop = function", "HANDLERS.big = function", "TRIOS_FROM = 756", "PAIRS_FROM = 500",
                     "OUTLINE_MS = 260", "SCALE_MS = 260", "UNSCALE_MS = 220", "CUE_FADE_MS = 400", "MIN_SEEN = 4",
                     "DOT_GAP = 6"):
            self.assertIn(name, js)
        for step in protocol.BIG_STEPS:
            self.assertIn(f"post('big', {{step: '{step}'}})", js)
        self.assertEqual(desk_cue.MIN_SEEN, 4)
        css = (ASSETS / "board.css").read_text(encoding="utf-8")
        self.assertIn("top: 44px; bottom: 24px", css)                  # the frames area desk_cue.anchor clips to
        self.assertEqual((desk_cue.BOARD_TOP, desk_cue.BOARD_BOTTOM), (44, 24))
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)


# --------------------------------------------------------------------------
# The lit state's life in the app (the stand-in board)
# --------------------------------------------------------------------------

class LitApp(unittest.TestCase):
    def make(self, *, size: tuple[int, int] = (1120, 700)) -> AppHarness:
        self.boards: list = []
        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(self.boards))
        harness.reading(autoplay=False)
        self.c = harness.c
        self.window = self.c.window
        self.reading = self.window.reading
        self.window.resize(*size)
        self.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.desk = self.c.look.desk
        settle(6)
        return harness

    def answer(self, key: int, say: str, frames: list[tuple[str, dict, str]]) -> list[str]:
        """An answer shown on Home whose turn says ``say``: (source, frame data, source key) each; its frame ids."""
        desk = self.desk
        self.c._you(f"Question {key}?")
        desk.on_answer_started(key, f"Question {key}?")
        self.c._jarvis(say)
        answer = desk._answers[key]
        answer.done = True
        answer.say = say
        entries = [(source, lambda fid, data=data: Built(({**data, "type": "frame", "id": fid},), 0), "", "", key_)
                   for source, data, key_ in frames]
        desk._put(answer, entries)
        desk.feed.set_turn(key, desk._answer_entry())
        desk._sync_counts()
        settle(3)
        return [desk.feed.frame_id(key, source) for source, _data, _key in frames]

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def layout(self, places: list[tuple[str, int, int]], *, scrolling: bool = False) -> None:
        self.board().request("layout", frames=[{"id": frame_id, "top": top, "height": height}
                                               for frame_id, top, height in places], scrolling=scrolling)
        settle()

    def speak(self, text: str, sentence: int) -> None:
        timeline, position = timeline_for(text, sentence)
        self.c._voice_timeline = lambda: timeline
        self.c._voice_position = lambda: position
        self.c._on_voice_speaking(text)
        settle()

    def at(self, sentence: int) -> None:
        """His voice moves on to the ``sentence``-th sentence of what he says; the next tick sees it."""
        timeline, position = timeline_for(self.c._utterance, sentence)
        self.c._voice_position = lambda: position
        self.desk.cue._tick()
        settle()

    def lit_sent(self) -> dict:
        return self.board().of_type("lit")[-1]

    def rings(self) -> list[float]:
        reading = self.reading
        return [ring.glow_share() for ring in (reading.hero, reading.dock.mini_orb(), reading.briefing_orb,
                                               reading.plan_stage.orb, reading.desk_page().head.orb)]

    def demo(self) -> list[str]:
        ids = self.answer(1, SAY, [("mail", MAIL, "mail:work:t1"), ("day", DAY, "day:2026-11-17"),
                                   ("budget", BUDGET, "")])
        self.layout([(ids[0], 56, 160), (ids[1], 228, 240), (ids[2], 480, 120)])
        return ids


class LitLifeTests(LitApp):
    def test_the_sentence_being_said_lights_the_frame_it_names(self) -> None:
        self.make()
        mail, day, budget = self.demo()
        glances: list[int] = []
        self.reading.glanceChanged.connect(lambda: glances.append(1))
        self.speak(SAY, 1)
        self.assertEqual(self.desk.cue.lit(), mail)
        self.assertEqual(self.lit_sent(), {"type": "lit", "id": mail, "cue": True})
        self.assertTrue(self.reading.frame_lit())
        self.assertEqual(self.rings(), [orb_mod.LIT_GLOW] * 5)          # every ring's core glow halves ...
        self.assertTrue(glances)                                        # ... and the HUD's mirror hears of it
        self.at(0)
        self.assertEqual(self.lit_sent()["id"], day)                     # the next sentence moves it
        self.at(2)
        self.assertEqual(self.lit_sent()["id"], budget)

    def test_a_sentence_naming_nothing_lights_nothing(self) -> None:
        self.make()
        self.demo()
        self.speak("Anything else, sir? The budget looks fine.", 0)
        self.assertEqual(self.desk.cue.lit(), "")
        self.assertEqual(self.board().of_type("lit"), [])               # nothing was ever lit
        self.at(1)                                                       # "budget": the mail and the chart share it
        self.assertEqual(self.desk.cue.lit(), "")
        self.assertEqual(self.rings(), [1.0] * 5)

    def test_it_clears_when_the_speech_ends_is_stopped_or_muted(self) -> None:
        self.make()
        mail = self.demo()[0]
        for how in ("ends", "stopped", "muted"):
            with self.subTest(how=how):
                self.speak(SAY + " " * len(how), 1)                     # a new utterance each time
                self.assertEqual(self.desk.cue.lit(), mail)
                if how == "muted":
                    self.c._on_mute_toggled(True)                       # his voice muted: at once
                    self.addCleanup(self.c._on_mute_toggled, False)
                else:
                    self.c._on_voice_speaking("")                        # done, or stopped (the voice says "")
                settle()
                self.assertEqual(self.lit_sent(), {"type": "lit", "id": "", "cue": False})
                self.assertFalse(self.reading.frame_lit())
                self.assertEqual(self.rings(), [1.0] * 5)
                self.c._on_mute_toggled(False)

    def test_off_the_desk_nothing_is_lit_and_back_it_is_again(self) -> None:
        self.make()
        mail = self.demo()[0]
        self.speak(SAY, 1)
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.assertEqual(self.desk.cue.lit(), "")                       # another screen: the desk is not seen
        self.desk.cue._tick()
        self.assertEqual(self.desk.cue.lit(), "")
        self.reading.set_tab(ui.SCREEN_HOME)
        settle()
        self.desk.cue._tick()
        self.assertEqual(self.desk.cue.lit(), mail)

    def test_the_lit_frame_leaving_the_board_moves_or_clears_it(self) -> None:
        self.make()
        mail = self.demo()[0]
        self.speak(SAY, 1)
        self.board().request("close", id=mail)
        settle()
        self.assertEqual(self.desk.cue.lit(), "")                       # closed: not in sight, never guessed ...
        self.assertEqual(self.lit_sent()["id"], "")                     # ... and "budget" is still the mail's too
        self.board().request("bring_back", id=mail)
        settle()
        self.assertEqual(self.desk.cue.lit(), mail)                     # back in sight: lit again


class CueLineTests(LitApp):
    def test_from_beside_the_sentence_s_end_to_the_seam_and_along_it(self) -> None:
        self.make()
        mail = self.demo()[0]
        self.speak(SAY, 1)
        path = self.desk.cue.path()
        self.assertIsNotNone(path)
        desk = self.reading.desk_page()
        geometry = desk.geometry_now()
        self.assertTrue(path.amber)                                     # the mail waits: CUE_AMBER
        self.assertEqual(path.seam_x, geometry.seam.x() + 0.5)
        self.assertEqual(path.end_y, geometry.view.y() + desk_cue.anchor(56, 160, self.board().height()) + 0.5)
        view = desk.turns.current_view()
        self.assertIn("reply waits for your OK.", view.turn.text)
        turn_box = view.geometry()
        start = view.mapFrom(desk, path.start.toPoint())
        self.assertTrue(0 <= start.x() <= turn_box.width() + 1)          # the current turn, beside its words
        self.assertTrue(0 <= start.y() <= turn_box.height())
        line = desk.cue_line
        self.assertTrue(line.shown())
        self.assertLessEqual(line.geometry().right() + 1, geometry.view.x())   # it never reaches over the view
        self.assertLessEqual(path.points()[2].x(), line.geometry().right() + 1)

    def test_a_sentence_ending_inside_its_line_starts_after_that_line_s_words(self) -> None:
        self.make()
        self.demo()
        self.speak(SAY, 0)                                              # "... the first at ten." then "Dana ..."
        path = self.desk.cue.path()
        view = self.reading.desk_page().turns.current_view()
        end = lit.locate(SAY[:SAY.index("ten.") + 4], view.turn.text)
        at_end = view.mapTo(self.reading.desk_page(), view.text_point(end))
        self.assertGreaterEqual(path.start.x(), at_end.x())              # never across the next sentence's words

    def test_it_starts_beside_the_sentence_being_said_never_an_earlier_one_with_its_words(self) -> None:
        # Part 5c fixes, round 2 (lit-read 0): the sentence's end was looked for from the turn's start, so a sentence
        # that repeats an earlier one's words drew the line from that earlier one.
        self.make()
        said = "Dana sent the budget numbers, sir. The calendar is clear after 3. Dana sent the budget numbers."
        ids = self.answer(1, said, [("mail", MAIL, "mail:work:t1"), ("day", DAY, "day:2026-11-17")])
        self.layout([(ids[0], 56, 160), (ids[1], 228, 240)])
        self.speak(said, 2)
        self.assertEqual(self.desk.cue.lit(), ids[0])
        desk = self.reading.desk_page()
        view = desk.turns.current_view()
        end = view.mapTo(desk, view.text_point(len(said), line_end=True))  # beside the last sentence's end
        path = self.desk.cue.path()
        self.assertIsNotNone(path)
        self.assertAlmostEqual(path.start.y(), end.y(), delta=0.5)
        self.assertAlmostEqual(path.start.x(), end.x(), delta=0.5)

    def test_the_anchor(self) -> None:
        self.assertEqual(desk_cue.anchor(56, 160, 626), (56 + 216) // 2)    # the frame's top clipped to 44 px
        self.assertEqual(desk_cue.anchor(100, 100, 626), 150)
        self.assertEqual(desk_cue.anchor(560, 200, 626), (560 + 602) // 2)  # its foot clipped to 24 px above the end
        self.assertIsNone(desk_cue.anchor(-300, 200, 626))              # scrolled out of sight: no cue line
        self.assertIsNone(desk_cue.anchor(600, 200, 626))

    def test_scrolling_hides_it_and_it_comes_back_without_the_draw_in(self) -> None:
        self.make()
        mail, day, budget = self.demo()
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.speak(SAY, 1)
        line = self.reading.desk_page().cue_line
        self.assertTrue(line.drawing())                                 # a new lit frame: it draws in (lively)
        self.layout([(mail, 30, 160), (day, 202, 240), (budget, 454, 120)], scrolling=True)
        self.assertFalse(line.shown())                                  # while the board scrolls: hidden
        self.layout([(mail, 10, 160), (day, 182, 240), (budget, 434, 120)])
        self.assertTrue(line.shown())
        self.assertFalse(line.drawing())                                # back at once
        self.assertEqual(line.path().end_y, self.reading.desk_page().geometry_now().view.y()
                         + desk_cue.anchor(10, 160, self.board().height()) + 0.5)
        self.assertTrue(self.lit_sent()["cue"])

    def test_open_big_drops_the_native_part_at_once_and_keeps_it_hidden(self) -> None:
        # Part 5c fixes: its 400 ms fade (640 calm) went on over the wide view after open big (DESIGN-SPEC 8).
        self.make()
        mail = self.demo()[0]
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.speak(SAY, 1)
        line = self.reading.desk_page().cue_line
        self.assertTrue(line.shown())
        self.board().request("open", id=mail)
        settle()
        self.assertEqual(self.desk.big.state(), "open")
        self.assertIsNone(line.path())
        self.assertIsNone(line._leaving)                                # noqa: SLF001 - no fade over the view ...
        self.assertTrue(line.isHidden())                                # ... and out of sight while big
        self.board().request("big", step="back")
        self.board().request("big", step="shut")
        settle()
        self.assertFalse(line.isHidden())                               # back with the board

    def test_its_motion_lively_calm_and_off(self) -> None:
        line = desk_view.CueLine()
        self.addCleanup(line.deleteLater)
        path = desk_view.CuePath(QPointF(100, 300), 380.5, 120.5)
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        line.show_path(path)
        self.assertTrue(line.drawing())
        line.fade()
        self.assertIsNone(line.path())
        self.assertIsNotNone(line._leaving)                             # noqa: SLF001 - fading 400 ms
        self.assertGreater(line._leave_s, 0.39)                         # noqa: SLF001
        holo.set_motion_level(holo.MOTION_CALM)
        line.show_path(path)
        self.assertFalse(line.drawing())                                # calm: it appears at once
        line.fade()
        self.assertAlmostEqual(line._leave_s, 0.64, places=2)          # noqa: SLF001 - x1.6
        holo.set_motion_level(holo.MOTION_OFF)
        line.show_path(path)
        self.assertFalse(line.drawing())                                # off: still while read ...
        line.fade()
        self.assertIsNone(line._leaving)                                # noqa: SLF001 - ... removed at once

    def test_it_paints_only_up_to_the_seam_in_the_frame_s_colour(self) -> None:
        line = desk_view.CueLine()
        self.addCleanup(line.deleteLater)
        line.resize(381, 600)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        line.show_path(desk_view.CuePath(QPointF(100, 300), 380.5, 120.5, amber=True))
        image = QImage(381, 600, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        line.render(image)
        along = image.pixelColor(380, 200)                              # on the seam's column, between its ends
        across = image.pixelColor(250, 300)                             # along the sentence's line
        self.assertGreater(along.alpha(), 100)
        self.assertGreater(across.alpha(), 100)
        self.assertGreater(across.red(), across.blue())                 # CUE_AMBER, never cyan for a waiting frame
        self.assertEqual(image.pixelColor(380, 60).alpha(), 0)          # it stops at the frame's anchor


# --------------------------------------------------------------------------
# The ring: the core glow halves (orb.py) and the paint budget holds
# --------------------------------------------------------------------------

class RingGlowTests(unittest.TestCase):
    def test_the_core_glow_halves_and_eases(self) -> None:
        ring = orb_mod.OrbWidget(orb_mod.HERO)
        self.addCleanup(ring.deleteLater)
        ring.set_state(orb_mod.SPEAKING)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        ring.settle()
        full = ring.sample().glow
        self.assertGreater(full, 0.0)
        ring.set_glow_share(orb_mod.LIT_GLOW)
        self.assertAlmostEqual(ring.sample().glow, full * 0.5, places=4)   # off: at once
        ring.set_glow_share(1.0)
        self.assertAlmostEqual(ring.sample().glow, full, places=4)
        holo.set_motion_level(holo.MOTION_LIVELY)
        ring.set_phase(None)
        ring.set_glow_share(orb_mod.LIT_GLOW)
        midway = ring.sample(ring._now() + orb_mod.GLOW_SHARE_S / 2).glow   # noqa: SLF001 - eased over 200 ms
        self.assertTrue(full * 0.5 < midway < full, midway)
        self.assertAlmostEqual(ring.sample(ring._now() + 1.0).glow, full * 0.5, places=4)   # noqa: SLF001
        self.assertEqual(ring.glow_share(), 0.5)

    def test_the_36_px_ring_paints_its_glow_at_its_share(self) -> None:
        def lit_pixels(share: float) -> int:
            pixmap = QPixmap(60, 60)
            pixmap.fill(QColor(holo.VOID))
            painter = QPainter(pixmap)
            orb_mod.paint_mini_orb(painter, QPointF(30, 30), orb_mod.SPEAKING, glow_share=share)
            painter.end()
            image = pixmap.toImage()
            return sum(image.pixelColor(x, y).green() for x in range(14, 46) for y in range(14, 46))

        self.assertLess(lit_pixels(0.5), lit_pixels(1.0))
        mini = orb_mod.MiniOrb()
        self.addCleanup(mini.deleteLater)
        mini.set_glow_share(0.5)
        self.assertEqual(mini.glow_share(), 0.5)

    def test_the_dock_s_36_px_ring_paints_its_own_share(self) -> None:
        # Part 5c fixes: the widget's own paint (paintEvent) passes its share on, not only the free painter.
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        mini = orb_mod.MiniOrb()
        self.addCleanup(mini.deleteLater)
        mini.set_state(orb_mod.SPEAKING)
        mini.settle()

        def painted(share: float) -> int:
            mini.set_glow_share(share)
            image = on_void(mini)
            side = image.width()
            return sum(image.pixelColor(x, y).green() for x in range(side // 3, 2 * side // 3)
                       for y in range(side // 3, 2 * side // 3))

        self.assertLess(painted(orb_mod.LIT_GLOW), painted(1.0))

    def test_the_paint_budget_holds_with_a_lit_frame(self) -> None:
        """The ring's median paint with its glow halved, speaking (its brightest look): at most 3 ms at 320 px, and
        x2.25 at 1.5x (DESIGN-SPEC 4.4); the measured medians are in the part's report."""
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        for px, dpr, budget in ((orb_mod.HERO, 1.0, 3.0), (orb_mod.HERO, 1.5, 6.75), (orb_mod.COMPACT, 1.0, 3.0)):
            with self.subTest(px=px, dpr=dpr):
                ring = orb_mod.OrbWidget(px)
                self.addCleanup(ring.deleteLater)
                ring.set_state(orb_mod.SPEAKING)
                ring.set_glow_share(orb_mod.LIT_GLOW)
                ring.settle()
                side = ring.width()
                best = None
                for _attempt in range(3):                               # the best of three medians (a busy machine)
                    times = []
                    for _ in range(41):
                        pixmap = QPixmap(round(side * dpr), round(side * dpr))
                        pixmap.setDevicePixelRatio(dpr)
                        pixmap.fill(QColor(holo.VOID))
                        frame = ring.sample()
                        painter = QPainter(pixmap)
                        started = time.perf_counter()
                        ring._paint(painter, frame, dpr)                 # noqa: SLF001 - the paint itself
                        times.append((time.perf_counter() - started) * 1000)
                        painter.end()
                    times.sort()
                    best = times[20] if best is None else min(best, times[20])
                    if best <= budget:
                        break
                self.assertLessEqual(best, budget)


class ReadTests(LitApp):
    """Part 5c fixes, the sentences already said (the mock-up's ``.turn.now .tx .read``): while Jarvis says the current
    turn, every sentence before the one being said reads in INK_3 - found from the same sentence the lit frame comes
    from -, eased in lively motion; the turn's own colours again when he is done, stopped or muted."""

    def current(self) -> desk_view.TurnView:
        return self.reading.desk_page().turns.current_view()

    @staticmethod
    def brightest(view: desk_view.TurnView, offset: int) -> int:
        """The brightest pixel (r + g + b) of the letter at ``offset`` of the turn's words, as painted."""
        color = ReadTests.ink(view, offset)
        return color.red() + color.green() + color.blue()

    @staticmethod
    def ink(view: desk_view.TurnView, offset: int) -> QColor:
        """The brightest pixel of the letter at ``offset`` of the turn's words, as painted: its ink."""
        image = on_void(view)
        ratio = image.devicePixelRatio()
        left, right = view.text_point(offset), view.text_point(offset + 1)
        best, found = -1, QColor(holo.VOID)
        for x in range(int(left.x() * ratio), int(right.x() * ratio) + 1):
            for y in range(int((left.y() - 7) * ratio), int((left.y() + 7) * ratio) + 1):
                if 0 <= x < image.width() and 0 <= y < image.height():
                    color = image.pixelColor(x, y)
                    if color.red() + color.green() + color.blue() > best:
                        best, found = color.red() + color.green() + color.blue(), color
        return found

    def hold(self, view: desk_view.TurnView, progress: float) -> None:
        """Its ease stopped ``progress`` of the way (a paint mid-ease)."""
        self.assertTrue(view.read_easing())
        view._read_anim.stop()                                              # noqa: SLF001
        view._read_p = progress                                             # noqa: SLF001
        view.update()

    def test_the_sentences_already_said_read_in_ink_3(self) -> None:
        self.make()
        self.demo()
        self.speak(SAY, 0)
        view = self.current()
        self.assertEqual(view.turn.text, SAY)
        self.assertEqual((view.read_end(), view.read_overlay()), (0, []))   # the first sentence: nothing before it
        second, third = SAY.index("Dana"), SAY.index("The budget")
        self.at(1)
        self.assertEqual(view.read_end(), second)
        self.assertEqual(view.read_overlay(), [(0, second, QColor(holo.INK_3))])
        self.assertLess(self.brightest(view, 0) + 80, self.brightest(view, second))   # painted: "Three" dim, "Dana"
        self.at(2)                                                              # bright
        self.assertEqual(view.read_overlay(), [(0, third, QColor(holo.INK_3))])   # "8:12" and what waits too
        self.assertLess(self.brightest(view, SAY.index("8:12")) + 80, self.brightest(view, third))
        older = [turn for turn in self.reading.desk_page().turns.views() if turn is not view]
        self.assertTrue(older)
        self.assertTrue(all(turn.read_end() == 0 and turn.read_overlay() == [] for turn in older))   # as they were

    def test_it_eases_in_lively_motion_and_is_at_once_in_calm_and_off(self) -> None:
        self.make()
        self.demo()
        second, third = SAY.index("Dana"), SAY.index("The budget")
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.speak(SAY, 1)
        view = self.current()
        self.assertFalse(view.read_easing())                                # the turn first marked: at once
        self.at(2)
        self.assertTrue(view.read_easing())                                 # the sentence just said eases ...
        overlay = view.read_overlay()
        self.assertEqual(overlay[0], (0, second, QColor(holo.INK_3)))
        self.assertTrue(all(start >= second and ink != QColor(holo.INK_3) for start, _length, ink in overlay[1:]))
        self.assertEqual(desk_view.READ_EASE_MS, 160)                       # ... over 160 ms at most
        self.assertTrue(wait_for(lambda: not view.read_easing(), 1.0))
        self.assertEqual(view.read_overlay(), [(0, third, QColor(holo.INK_3))])
        for level in (holo.MOTION_CALM, holo.MOTION_OFF):
            with self.subTest(level=level):
                holo.set_motion_level(level)
                self.speak(SAY + " " * len(level), 1)
                self.at(2)
                self.assertFalse(view.read_easing())                        # at once
                self.assertEqual(view.read_overlay(), [(0, third, QColor(holo.INK_3))])

    def test_the_turn_is_its_own_colours_again_when_he_is_done_stopped_or_muted(self) -> None:
        self.make()
        self.demo()
        for how in ("ends", "stopped", "muted"):
            for level in (holo.MOTION_LIVELY, holo.MOTION_OFF):
                with self.subTest(how=how, level=level):
                    holo.set_motion_level(level)
                    self.speak(SAY + " " * len(how + level), 2)
                    view = self.current()
                    self.assertGreater(view.read_end(), 0)
                    if how == "muted":
                        self.c._on_mute_toggled(True)                        # his voice muted: back at once
                    else:
                        self.c._on_voice_speaking("")                        # done, or stopped (the voice says "")
                    settle()
                    self.assertEqual(view.read_end(), 0)
                    if level == holo.MOTION_LIVELY:
                        self.assertTrue(view.read_easing())                  # eased back (lively) ...
                        self.assertTrue(wait_for(lambda: not view.read_easing(), 1.0))
                    self.assertEqual(view.read_overlay(), [])                # ... to the turn's own colours
                    self.c._on_mute_toggled(False)

    def test_the_words_said_move_with_the_turn_being_said(self) -> None:
        # What he says now shows as the live turn until the answer's own turn comes: it takes the said words at once.
        self.make()
        desk = self.desk
        self.c._you("Question 1?")
        desk.on_answer_started(1, "Question 1?")
        answer = desk._answers[1]
        answer.done, answer.say = True, SAY
        desk._put(answer, [("mail", lambda fid: Built(({**MAIL, "type": "frame", "id": fid},), 0), "", "", "")])
        desk._sync_counts()
        settle(3)
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.speak(SAY, 1)
        live = self.current()
        self.assertEqual(live.turn.entry_id, -1)                            # what he says now
        self.assertEqual(live.read_end(), SAY.index("Dana"))
        self.c._jarvis(SAY)                                                 # his own turn comes meanwhile
        settle()
        own = self.current()
        self.assertNotEqual(own.turn.entry_id, -1)
        self.assertEqual(own.read_end(), SAY.index("Dana"))                 # at once, no flash of the said words
        self.assertFalse(own.read_easing())

    def test_older_turns_and_the_classic_home_are_unchanged(self) -> None:
        self.make()
        self.demo()
        self.speak(SAY, 2)
        first = self.current()
        self.assertGreater(first.read_end(), 0)
        self.answer(2, "Anything else, sir?", [("day", DAY, "day:2026-11-17")])   # a newer turn: the first is older
        self.assertIsNot(self.current(), first)
        self.assertEqual((first.read_end(), first.read_overlay()), (0, []))
        harness = AppHarness(self, config_extra="\n[look]\nboard = false\n")     # the classic Home: no desk at all
        harness.reading(autoplay=False)
        harness.c._on_voice_speaking(SAY)
        settle()
        self.assertIsNone(harness.c.window.reading.desk_page())
        harness.c._on_voice_speaking("")

    # ---- part 5c fixes, round 2 ---------------------------------------------------------------------------------------

    def test_a_sentence_that_repeats_earlier_words_is_found_in_its_own_place(self) -> None:
        # lit-read 0: "That's all." was looked for from the turn's start and found inside the first sentence, so every
        # word already said went back to its own colours as he said it.
        said = "That's all I found in your mail, sir. The calendar is clear after 3. That's all."
        for turn in (said, said.replace("after 3", "after three")):        # word for word, and said otherwise
            with self.subTest(turn=turn):
                self.make()
                self.answer(1, turn, [("mail", MAIL, "mail:work:t1")])
                self.speak(said, 1)
                view = self.current()
                self.assertEqual(view.turn.text, turn)
                self.at(2)
                self.assertEqual(view.read_end(), turn.rindex("That's all."))   # the last one, never the first
                self.assertEqual(view.read_overlay(), [(0, turn.rindex("That's all."), QColor(holo.INK_3))])

    @staticmethod
    def painted(block: desk_view.TextBlock, overlay: list, origin: QPointF) -> QImage:
        """``block`` painted as TurnView paints it (TextBlock.paint, its overlay), at ``origin``, over the VOID."""
        image = QImage(int(block._width) + 4, int(block.lines * block.line) + 4,   # noqa: SLF001
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(holo.VOID))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            block.paint(painter, origin, holo.INK_1, overlay)
        finally:
            painter.end()
        return image

    @staticmethod
    def ink_in(image: QImage, block: desk_view.TextBlock, offset: int, origin: QPointF) -> QColor:
        left, right = block.point_at(offset), block.point_at(offset + 1)
        best, found = -1, QColor(holo.VOID)
        for x in range(int(left.x() + origin.x()), int(right.x() + origin.x()) + 1):
            for y in range(int(left.y() + origin.y() - 7), int(left.y() + origin.y() + 7) + 1):
                if 0 <= x < image.width() and 0 <= y < image.height():
                    color = image.pixelColor(x, y)
                    if color.red() + color.green() + color.blue() > best:
                        best, found = color.red() + color.green() + color.blue(), color
        return found

    def test_the_ease_is_what_is_painted(self) -> None:
        # lit-read 1: QTextLayout.draw skips a selection that sets only a foreground when it touches one drawn before
        # it (each one's region rounded out to whole pixels), so most of the just-said sentence snapped to INK_3 at the
        # ease's end instead of easing, and the times and the waiting phrase snapped back after the speech. Runs meet
        # between two pixels with fractional glyph advances (DirectWrite, 1.5x): painted here where they do (a
        # fractional origin; at 1x offscreen the advances are whole pixels), and as the turn paints itself.
        self.make()
        self.demo()
        dana, both, reply = SAY.index("Dana"), SAY.index("and a"), SAY.index("reply")
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.speak(SAY, 1)
        view = self.current()
        block = view.block()
        origin = QPointF(0.37, 0.0)

        def colours() -> dict[int, QColor]:
            image = self.painted(block, view.read_overlay(), origin)
            return {offset: self.ink_in(image, block, offset, origin) for offset in (dana, both, reply)}

        def between(value: int, one: int, other: int) -> bool:
            low, high = min(one, other), max(one, other)
            margin = (high - low) / 5
            return high - low > 40 and low + margin < value < high - margin

        own = colours()                                                     # being said: its own colours
        self.at(2)
        self.hold(view, 0.5)
        half, widget = colours(), self.brightest(view, dana)
        view._on_read_done()                                                # noqa: SLF001 - the ease at its end
        read = colours()
        for offset in (dana, both):                                        # INK_1 to INK_3: their brightness
            with self.subTest(offset=offset):
                self.assertTrue(between(sum(half[offset].getRgb()[:3]), sum(own[offset].getRgb()[:3]),
                                        sum(read[offset].getRgb()[:3])), (own[offset], half[offset], read[offset]))
        self.assertTrue(between(half[reply].blue(), own[reply].blue(), read[reply].blue()))   # AMBER_HI to INK_3
        self.assertTrue(between(widget, sum(own[dana].getRgb()[:3]), sum(read[dana].getRgb()[:3])))   # the turn too
        self.c._on_voice_speaking("")                                      # the speech ends: it eases back ...
        settle()
        self.hold(view, 0.5)
        back = colours()
        for offset in (dana, both):
            with self.subTest(offset=offset, back=True):
                self.assertTrue(between(sum(back[offset].getRgb()[:3]), sum(own[offset].getRgb()[:3]),
                                        sum(read[offset].getRgb()[:3])), (own[offset], back[offset], read[offset]))
        self.assertTrue(between(back[reply].blue(), own[reply].blue(), read[reply].blue()),
                        (own[reply], back[reply], read[reply]))             # ... a blend too

    def test_after_two_emoji_the_said_words_and_the_marks_are_where_they_should_be(self) -> None:
        # lit-read 3: Python's places (characters) went into Qt as they were, where Qt counts UTF-16 units (an emoji is
        # two), so after two emoji the dimming stopped two places short (the full stop before "Dana" stayed bright),
        # the times' colour fell two places early and a place's point (the cue line's start) was two places off.
        from PySide6.QtGui import QTextLayout, QTextOption

        said = 'Two mails came in: "\U0001F389 Launch day" and "\U0001F680 Q4 plan". Dana sent the numbers at 8:12.'
        self.make()
        self.answer(1, said, [("mail", MAIL, "mail:work:t1")])
        self.speak(said, 0)
        view = self.current()
        stop, dana, two = said.index('". Dana') + 1, said.index("Dana"), said.index("8:12") + 3
        unread = self.brightest(view, stop)                                 # the first sentence being said
        self.at(1)
        self.assertEqual(view.read_end(), dana)
        block = view.block()
        layout = QTextLayout(said, block.font)                              # Qt's own layout of the same words ...
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(block._width)                                 # noqa: SLF001 - its laid-out width
        layout.endLayout()
        for offset in (stop, dana, two):
            with self.subTest(offset=offset):
                units = len(said[:offset].encode("utf-16-le")) // 2         # ... in its own units
                found = layout.lineForTextPosition(units).cursorToX(units)
                x = float(found[0] if isinstance(found, tuple) else found)
                self.assertAlmostEqual(view.text_point(offset).x(), x, delta=0.5)
        self.assertLess(self.brightest(view, stop) + 60, unread)           # the full stop said: INK_3
        cyan = self.ink(view, two)                                          # the time's last digit: HOLO_HI
        self.assertLess(cyan.red() + 60, cyan.green(), cyan)


class HudRingTests(HudApp):
    """Part 5c fixes: the HUD's ring follows the window's lit state (DesktopController.sync_glance)."""

    def test_the_hud_ring_s_core_glow_halves_while_a_frame_is_lit(self) -> None:
        self.make()
        self.enter()                                                     # the owner's Desktop: the HUD shows
        orb = self.hud().orb
        self.assertEqual(orb.glow_share(), 1.0)
        self.reading.set_frame_lit(True)                                 # as the cue line's controller lights a frame
        settle()
        self.assertEqual(orb.glow_share(), orb_mod.LIT_GLOW)
        self.reading.set_frame_lit(False)                                # the speech ended: nothing lit
        settle()
        self.assertEqual(orb.glow_share(), 1.0)


class LogTests(LitApp):
    def test_the_logs_hold_no_words(self) -> None:
        self.make()
        secret = "Zanzibar quartz meeting"
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            ids = self.answer(1, f"{secret}. Nothing else.", [("x", {"kind": "text", "title": "Zanzibar quartz",
                                                                    "body": {"text": secret}}, "")])
            self.layout([(ids[0], 60, 120)])
            self.speak(f"{secret}. Nothing else.", 0)
            self.c._on_voice_speaking("")
            logging.getLogger("briefing_reader").debug("marker")
        self.assertFalse(any("Zanzibar" in line or "quartz" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
