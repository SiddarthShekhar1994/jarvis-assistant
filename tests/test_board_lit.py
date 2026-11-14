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
calm and off), the ring's halved glow and its paint budget; no words in the logs.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from briefing_reader import desk_cue, desk_view, holo, ui, voice_fx
from briefing_reader import orb as orb_mod
from briefing_reader.board import lit, protocol
from briefing_reader.board.frames import Built
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle

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
