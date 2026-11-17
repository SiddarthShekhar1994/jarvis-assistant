"""The new look, part 4: the glance painter (briefing_reader.board.glance) and the board's fallback showing the frames
as read-only tiles. Offscreen; nothing here starts the web engine.

Covered: every kind and state painted at scale 1 and 1.5, 448 and 300 px wide, into a QImage: no text past its tile
(measured: every op inside the tile, every line no wider than its box, no two words over each other) and nothing painted
outside it; a tile given too little height leaves rows out and says "+N MORE", still inside; private hides a mail's
text ("Text hidden on this screen") and keeps senders and subjects, and hides a text frame's paragraph (a web research
answer) and keeps its title and sources; the waits chip; pictures at 80 % (62 % private);
GlanceTiles stacks them (closed frames left out, never the focus); and BoardView's fallback: frames sent before and
after it fell back show as tiles, removes and clears follow, pictures come as images, the tile says READ-ONLY TILES
BELOW only while a tile shows.
"""

from __future__ import annotations

import base64
import itertools
import json
import math
import unittest
from datetime import date, datetime, timedelta, timezone

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import holo, pictures, snapshots
from briefing_reader.actions import parse_action_line
from briefing_reader.agenda import Deadline
from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
from briefing_reader.ask.research import ResearchAnswer
from briefing_reader.board import frames, glance, protocol, view
from briefing_reader.followups import OWED, WAITING, FollowupItem
from briefing_reader.gcal import EventBrief
from briefing_reader.gmail import MailView
from tests.ui_fakes import fonts, settle, tiny_jpeg, wait_for

PDT = timezone(timedelta(hours=-7))
NOW = datetime(2026, 11, 17, 9, 41, tzinfo=PDT)
MAIL_TEXT = "Here are the Q4 numbers - line 14 changed since Friday, the rest is the same."
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_glance", "-platform", "offscreen"])
    fonts()


def at(hour: int, minute: int = 0, day: int = 17) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=PDT)


def checked(message: dict, known: set[str] | None = None) -> dict:
    return json.loads(protocol.check_message(message, known or set()).text)


def built_frames() -> dict[str, tuple[dict, QImage | None]]:
    """One frame of each kind (and the states), as the builders make them and the protocol rebuilds them."""
    found: dict[str, tuple[dict, QImage | None]] = {}

    def add(name: str, built: frames.Built) -> None:
        frame = checked(built.messages[0])
        picture = None
        if len(built.messages) > 1:
            src = checked(built.messages[1], {frame["id"]})["src"]
            picture = QImage.fromData(base64.b64decode(src[len(protocol.PICTURE_PREFIX):]))
        found[name] = (frame, picture)

    thread = MailThread("work", "t1", "Re: Budget numbers", (MailMessage(
        "g1", sender=MailPerson("dana@example.edu", "Dana Whitfield"), sent_at=at(8, 12), subject="Re: Budget numbers",
        text=MAIL_TEXT),))
    add("mail", frames.from_picture(pictures.email_picture(thread, tz=PDT), frame_id="m1", now=NOW))
    view_ = MailView("me@example.edu", "dana@example.edu", "", "Re: Budget numbers", "<abc@example.com>", "", "t1",
                     MAIL_TEXT)
    add("outgoing", frames.from_picture(pictures.outgoing_picture(view_, account="work"), frame_id="m2", now=NOW))
    day = {"work": [EventBrief("e0", "primary", "Breakfast", start=at(8), end=at(8, 30)),
                    EventBrief("e1", "primary", "AI-Trader check-in", start=at(10), end=at(10, 30)),
                    EventBrief("e2", "primary", "Study group", start=at(13), end=at(14)),
                    EventBrief("e3", "primary", "Project Development check-in", start=at(16), end=at(16, 45))]}
    add("day", frames.from_picture(pictures.plan_picture(day, (), day=date(2026, 11, 17), now=NOW, hour24=True),
                                   frame_id="c1", now=NOW, title="Today"))
    add("day12", frames.from_picture(pictures.plan_picture(day, (), day=date(2026, 11, 17), now=NOW), frame_id="c2",
                                     now=NOW))
    add("week", frames.from_picture(pictures.week_picture(day, at(0), at(0, day=24), now=NOW), frame_id="w1",
                                    now=NOW))
    add("page", frames.from_picture(pictures.page_ready("https://city-hack.example.org/schedule",
                                                        image=tiny_jpeg(320, 180), width=320, height=180),
                                    frame_id="p1", now=NOW))
    add("failed", frames.from_picture(pictures.page_unavailable("https://x.example.org/", "it timed out"),
                                      frame_id="p2", now=NOW))
    add("pending", frames.from_picture(pictures.page_pending("https://x.example.org/"), frame_id="p3", now=NOW))
    hits = [(f"Result number {n} with a longer title", f"https://site{n}.example.org/x") for n in range(10)]
    add("search", frames.from_picture(pictures.search_picture("city hack schedule", hits), frame_id="s1", now=NOW))
    add("research", frames.from_research(ResearchAnswer(
        "The City Hack schedule moved to Friday; registration closes at noon and teams of up to four may enter.",
        (("City Hack schedule", "https://city-hack.example.org/s"), ("Registration rules", "https://rules.example.org/"))),
        frame_id="r1"))
    todo = parse_action_line("Todo: title=Send budget comments to Dana | due=2026-11-19 | acct=work | link=")
    done = parse_action_line("Todo: title=Print boarding pass | due=2026-11-18 | acct=work | link=")
    add("todos", frames.from_todos([todo, done], [Deadline("LING essay draft", date(2026, 11, 19)),
                                                  Deadline("Book the dentist", date(2026, 11, 22))],
                                   frame_id="t1", now=NOW, statuses={done.id: "done"}))
    add("followups", frames.from_followups([
        FollowupItem("r1", "k", WAITING, "work", thread_id="t", last_gmail_id="g", who="Prof. Ana Ruiz",
                     subject="Lab section moved to Thursday", since=NOW - timedelta(hours=30)),
        FollowupItem("r2", "k", OWED, "work", thread_id="t", last_gmail_id="g", who="City Hack",
                     subject="City Hack 2026: registration closes Friday", since=NOW - timedelta(days=3))],
        frame_id="f1", now=NOW))
    found["list"] = (checked({"type": "frame", "id": "l1", "kind": "mail_list", "title": "Personal mail",
                              "meta": "3 unread", "body": {"rows": [
                                  {"sender": "Northwind Outfitters", "time": "7:02 AM", "subject": "Your order shipped",
                                   "meta": "", "unread": True},
                                  {"sender": "", "time": "", "subject": "", "meta": "+4 MORE", "unread": False}]}}),
                     None)
    found["empty"] = (checked({"type": "frame", "id": "e1", "kind": "mail_list", "title": "Work mail", "meta": "9:44 AM",
                               "status": "empty", "status_text": "Nothing unread, sir.",
                               "status_meta": "Checked 9:44 AM"}), None)
    found["pinned"] = (checked({"type": "frame", "id": "x1", "kind": "text", "title": "A title of sixty characters "
                                "that has to wrap onto two lines", "meta": "Tue 17 Nov · 3 meetings",
                                "pinned": True, "changed": True, "body": {"text": "word " * 60}}), None)
    found["hostile"] = (checked({"type": "frame", "id": "h1", "kind": "text", "title": "W" * 60, "meta": "M" * 40,
                                 "body": {"text": "x" * 400, "rows": [{"lead": "L" * 40, "text": "T" * 60,
                                                                       "meta": "E" * 40}] * 8}}), None)
    return found


FRAMES = None


def all_frames() -> dict[str, tuple[dict, QImage | None]]:
    global FRAMES
    if FRAMES is None:
        FRAMES = built_frames()
    return FRAMES


class LayoutTests(unittest.TestCase):
    def assert_inside(self, layout: glance.TileLayout, width: float, height: float, label: str,
                      scale: float = 1.0) -> None:
        for op in layout.ops:                      # (a rule's box is 0 px tall: compared edge by edge)
            box = op.rect
            inside = (box.left() >= -0.5 and box.top() >= -0.5 and box.right() <= width + 0.5
                      and box.bottom() <= height + 0.5)
            self.assertTrue(inside, (label, op.kind, op.lines, box))
        types = glance._type(scale)                                                        # noqa: SLF001
        texts = [op for op in layout.ops if op.kind == "text"]
        for op in texts:
            metrics = types.metrics[op.font]
            for line in op.lines:
                self.assertLessEqual(metrics.horizontalAdvance(line), op.rect.width() + 0.5, (label, line))
        for first, second in itertools.combinations(texts, 2):
            overlap = first.rect.intersected(second.rect)
            self.assertLess(overlap.width() * overlap.height(), 0.5, (label, first.lines, second.lines))

    def test_every_kind_at_both_scales_and_widths_stays_inside_its_tile(self) -> None:
        for (name, (frame, picture)), scale, base in itertools.product(all_frames().items(), (1.0, 1.5), (448, 300)):
            for private in (False, True):
                width = base * scale
                layout = glance.tile_layout(frame, width, scale=scale, private=private, picture=picture)
                with self.subTest(name=name, scale=scale, width=base, private=private):
                    self.assertFalse(layout.clipped)
                    self.assertEqual(layout.more, 0)
                    self.assert_inside(layout, width, layout.height, name, scale)

    def test_painted_into_an_image_nothing_lands_outside_the_tile(self) -> None:
        margin = 12
        for (name, (frame, picture)), scale in itertools.product(all_frames().items(), (1.0, 1.5)):
            width = 448 * scale
            height = glance.tile_height(frame, width, scale=scale, picture=picture)
            image = QImage(int(width) + 2 * margin, math.ceil(height) + 2 * margin,
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            glance.paint_tile(painter, QRectF(margin, margin, width, height), frame, scale=scale, picture=picture,
                              ground=True)
            painter.end()
            right, bottom = math.ceil(margin + width) + 1, math.ceil(margin + height) + 1
            bands = {"top": QRect(0, 0, image.width(), margin - 1),
                     "bottom": QRect(0, bottom, image.width(), image.height() - bottom),
                     "left": QRect(0, 0, margin - 1, image.height()),
                     "right": QRect(right, 0, image.width() - right, image.height())}
            with self.subTest(name=name, scale=scale):
                for band, area in bands.items():
                    part = image.copy(area)                            # kept: its bits are read below
                    pixels = bytes(part.constBits())
                    self.assertFalse(any(pixels), band)               # transparent: nothing painted there
                inside = image.pixelColor(margin + int(width / 2), margin + int(height) - 3)
                self.assertGreater(inside.alpha(), 0)                  # the ground was painted

    def test_the_words_are_the_frames_own(self) -> None:
        words = {name: glance.tile_layout(frame, 448, picture=picture).words()
                 for name, (frame, picture) in all_frames().items()}
        self.assertIn("Re: Budget numbers", words["mail"])
        self.assertIn("Dana Whitfield", words["mail"])
        self.assertIn("WORK · 8:12 AM", words["mail"])                     # the badge role is caps
        self.assertIn("Reply waits on Approvals", words["outgoing"])
        self.assertIn(glance.KEY_CAP.upper(), words["outgoing"])
        for expected in ("09:41", "IN 19 MIN", "Free", "2 h 30", "AI-Trader check-in", "16:00-16:45"):
            self.assertIn(expected, words["day"])
        self.assertIn("PICTURE ONLY", words["page"])
        self.assertIn("city-hack.example.org", words["page"])
        self.assertIn("Picture unavailable: it timed out", words["failed"])
        self.assertIn("+3 MORE", words["search"])
        self.assertIn("WAITS", words["todos"])
        self.assertIn("DONE", words["todos"])
        self.assertIn("WAITING · 30H", words["followups"])
        self.assertIn("+4 MORE", words["list"])
        self.assertIn("CHECKED 9:44 AM", words["empty"])
        self.assertIn("CHANGED", words["pinned"])
        page, picture = all_frames()["page"]
        image = [op for op in glance.tile_layout(page, 448, picture=picture).ops if op.kind == "image"]
        self.assertEqual([op.level for op in image], [glance.PICTURE_LEVEL])

    def test_private_hides_a_mails_text_and_keeps_senders_and_subjects(self) -> None:
        for name in ("mail", "outgoing"):
            frame, _picture = all_frames()[name]
            shown = glance.tile_layout(frame, 448).words()
            hidden = glance.tile_layout(frame, 448, private=True).words()
            self.assertIn(MAIL_TEXT[:20], shown.replace("\n", " "))
            self.assertNotIn(MAIL_TEXT[:20], hidden.replace("\n", " "))
            self.assertNotIn("Q4 numbers", hidden)
            self.assertIn(glance.HIDDEN, hidden)
        self.assertIn(glance.ON_MAIN, glance.tile_layout(all_frames()["outgoing"][0], 448, private=True).words())
        listed = glance.tile_layout(all_frames()["followups"][0], 448, private=True).words()
        self.assertIn("Lab section moved to Thursday", listed)
        self.assertIn("Prof. Ana Ruiz", listed)
        frame, picture = all_frames()["page"]
        levels = [op.level for op in glance.tile_layout(frame, 448, private=True, picture=picture).ops
                  if op.kind == "image"]
        self.assertEqual(levels, [glance.PRIVATE_PICTURE_LEVEL])

    def test_private_hides_a_text_frame_s_paragraph_and_keeps_its_title_and_sources(self) -> None:
        # Part 7 fixes (review 7, privacy 0): a web research answer is the line Jarvis speaks - never on the second
        # screen; the frame's title and its source rows stay.
        frame, _picture = all_frames()["research"]
        answer = "The City Hack schedule moved to Friday"
        self.assertIn(answer, " ".join(glance.tile_layout(frame, 448).words().split()))
        for screen in (glance.BOARD, glance.SECOND):
            with self.subTest(screen=screen):
                layout = glance.tile_layout(frame, 384, private=True, screen=screen)
                words = " ".join(layout.words().split())
                self.assertNotIn(answer[:20], words)
                self.assertNotIn("registration closes", words)
                self.assertIn(glance.HIDDEN, layout.words())
                self.assertIn("Web research", words)                              # its title ...
                self.assertIn("City Hack schedule", words)                        # ... and its sources stay
                self.assertIn("Registration rules", words)
                self.assertEqual([op.name for op in layout.ops if op.kind == "glyph" and op.name == "hidden"],
                                 ["hidden"])

    def test_too_little_height_leaves_rows_out_and_says_how_many(self) -> None:
        for name in ("day", "search", "todos", "followups", "hostile", "week"):
            frame, picture = all_frames()[name]
            natural = glance.tile_height(frame, 448)
            short = natural * 0.6
            layout = glance.tile_layout(frame, 448, short, picture=picture)
            with self.subTest(name=name):
                self.assertGreater(layout.more, 0)
                self.assertIn(f"+{layout.more} MORE", layout.words())
                self.assertFalse(layout.clipped)
                self.assert_inside(layout, 448, short, name)
        frame, _picture = all_frames()["hostile"]
        tiny = glance.tile_layout(frame, 448, 40)
        self.assertTrue(tiny.clipped)                              # not even its header fits: clipped to the tile
        image = QImage(470, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        glance.paint_tile(painter, QRectF(10, 10, 448, 40), frame)
        painter.end()
        self.assertEqual([y for y in range(52, 80) if any(image.pixelColor(x, y).alpha() for x in range(470))], [])

    def test_a_picture_shrinks_into_a_short_tile_and_the_bar_leaves_before_rows(self) -> None:
        frame, picture = all_frames()["page"]
        natural = glance.tile_layout(frame, 448, picture=picture)
        short = glance.tile_layout(frame, 448, natural.height - 80, picture=picture)
        (big,) = [op for op in natural.ops if op.kind == "image"]
        (small,) = [op for op in short.ops if op.kind == "image"]
        self.assertLess(small.rect.height(), big.rect.height() - 70)
        self.assertAlmostEqual(small.rect.width() / small.rect.height(), big.rect.width() / big.rect.height(), 2)
        self.assertFalse(short.clipped)
        self.assertIn("PICTURE ONLY", short.words())                    # its caption stays
        self.assert_inside(short, 448, natural.height - 80, "page")
        day, _ = all_frames()["day"]
        full = glance.tile_layout(day, 448)
        bar = sum(1 for op in full.ops if op.font == "axis")
        squeezed = glance.tile_layout(day, 448, full.height - 30)
        self.assertGreater(bar, 0)
        self.assertEqual(sum(1 for op in squeezed.ops if op.font == "axis"), 0)    # the bar left ...
        self.assertEqual(squeezed.more, 0)                                       # ... and every row stayed

    def test_outline_only_unless_given_its_ground(self) -> None:
        frame, _picture = all_frames()["mail"]
        height = glance.tile_height(frame, 448)
        for ground, alpha in ((False, 0), (True, 255)):
            image = QImage(468, int(height) + 20, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            glance.paint_tile(painter, QRectF(10, 10, 448, height), frame, ground=ground)
            painter.end()
            inside = image.pixelColor(10 + 448 - 4, 10 + int(height / 2))      # in the right padding: no words
            self.assertEqual(inside.alpha(), alpha, ground)
            if ground:
                self.assertEqual(inside.name(), QColor(holo.TILE).name())

    def test_a_long_word_breaks_inside_itself_and_spaces_break_lines(self) -> None:
        types = glance._type(1.0)                                                         # noqa: SLF001
        lines = glance._wrap("x" * 300, "body", 200, types)                               # noqa: SLF001
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), "x" * 300)
        lines = glance._wrap("well-known half-baked co-op words here", "body", 90, types)    # noqa: SLF001
        self.assertTrue(all(not line.endswith("-") for line in lines[:-1]), lines)       # never after a hyphen

    def test_the_outline_follows_the_state(self) -> None:
        self.assertEqual(glance._outline(all_frames()["outgoing"][0])[2], holo.RADIUS_TILE_AMBER)   # noqa: SLF001
        self.assertEqual(glance._outline(all_frames()["failed"][0])[1].name(), QColor(holo.TILE_ALERT).name())  # noqa
        self.assertEqual(glance._outline(all_frames()["mail"][0])[0].alphaF(), QColor(holo.FRAME_EDGE).alphaF())  # noqa


class GlanceTilesTests(unittest.TestCase):
    def test_tiles_stack_as_tall_as_they_need_and_never_take_the_focus(self) -> None:
        tiles = glance.GlanceTiles()
        self.addCleanup(tiles.deleteLater)
        tiles.resize(448, 300)
        chosen = [all_frames()[name][0] for name in ("mail", "day", "todos")]
        closed = dict(all_frames()["search"][0], closed=True)
        tiles.set_frames([*chosen, closed])
        self.assertEqual([frame["id"] for frame in tiles.frames()], ["m1", "c1", "t1"])      # closed: left out
        rects = tiles.tile_rects()
        self.assertEqual(len(rects), 3)
        for first, second in zip(rects, rects[1:]):
            self.assertAlmostEqual(second.top() - first.bottom(), glance.GAP)
        self.assertEqual(tiles.heightForWidth(448), int(rects[-1].bottom()))
        self.assertEqual(tiles.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertIn("Re: Budget numbers", tiles.accessibleDescription())
        tiles.resize(448, tiles.heightForWidth(448))
        image = tiles.grab().toImage()
        self.assertGreater(image.pixelColor(30, int(rects[0].top()) + 30).alpha(), 0)
        tiles.set_frames([])
        self.assertEqual(tiles.heightForWidth(448), 0)


class FallbackTilesTests(unittest.TestCase):
    def board(self) -> tuple[QWidget, view.BoardView]:
        host = QWidget()
        host.resize(503, 600)
        board = view.BoardView(host, scheme_ready=lambda: True, engine=lambda: snapshots.R_NO_ENGINE,
                               start_delay_ms=0)
        board.setGeometry(0, 0, 503, 600)
        self.addCleanup(host.deleteLater)
        self.addCleanup(board.shutdown)
        return host, board

    def test_frames_sent_before_and_after_the_fall_back_show_as_read_only_tiles(self) -> None:
        host, board = self.board()
        mail, _ = all_frames()["mail"]
        page, _ = all_frames()["page"]
        self.assertTrue(board.apply(mail))                                 # queued: the board has not started
        reasons: list[str] = []
        board.unavailable.connect(reasons.append)
        host.show()
        self.assertTrue(wait_for(lambda: reasons, 3))
        tiles = board.tiles()
        self.assertIsNotNone(tiles)
        self.assertEqual([frame["id"] for frame in tiles.frames()], ["m1"])
        self.assertEqual(board.fallback().words(), (view.CANT_START, "READ-ONLY TILES BELOW"))
        self.assertTrue(board.apply(page))
        jpeg = tiny_jpeg(320, 180)
        self.assertTrue(board.apply({"type": "picture", "id": "p1",
                                     "src": protocol.PICTURE_PREFIX + base64.b64encode(jpeg).decode("ascii")}))
        self.assertEqual([frame["id"] for frame in tiles.frames()], ["m1", "p1"])
        self.assertEqual(sorted(tiles._pictures), ["p1"])                   # noqa: SLF001 - decoded once, for p1
        self.assertTrue(board.apply(dict(mail, title="Re: Budget numbers (2)")))       # an update keeps its place
        self.assertEqual([frame["title"] for frame in tiles.frames()],
                         ["Re: Budget numbers (2)", "Page: city-hack.example.org"])
        self.assertFalse(board.apply({"type": "frame", "id": "x", "kind": "calendar", "title": "x",
                                      "body": {"rows": "nope"}}))
        self.assertTrue(board.apply({"type": "remove", "id": "p1"}))
        self.assertEqual([frame["id"] for frame in tiles.frames()], ["m1"])
        self.assertTrue(board.apply({"type": "lit", "id": "m1"}))
        self.assertTrue(board.apply({"type": "clear"}))
        self.assertEqual(tiles.frames(), ())
        self.assertEqual(board.fallback().words(), (view.CANT_START, ""))
        counts = board.counts()
        self.assertEqual((counts["tiles:frame"], counts["tiles:picture"], counts["tiles:clear"]), (2, 1, 1))
        self.assertEqual(counts["refused:shape"], 1)

    def test_the_tiles_area_is_see_through_and_under_the_tile(self) -> None:
        host, board = self.board()
        reasons: list[str] = []
        board.unavailable.connect(reasons.append)
        host.show()
        self.assertTrue(wait_for(lambda: reasons, 3))
        for name in ("mail", "day", "todos"):
            board.apply(all_frames()[name][0])
        settle()
        tiles = board.tiles()
        self.assertGreater(tiles.mapTo(board, tiles.rect().topLeft()).y(), board.fallback().geometry().bottom())
        self.assertEqual(len(tiles.tile_rects()), 3)
        self.assertEqual(tiles.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_a_picture_is_decoded_only_when_it_is_a_small_jpeg(self) -> None:
        jpeg = tiny_jpeg(96, 54)
        image = view.tile_picture(protocol.PICTURE_PREFIX + base64.b64encode(jpeg).decode("ascii"))
        self.assertEqual((image.width(), image.height()), (96, 54))
        self.assertIsNone(view.tile_picture("data:image/png;base64,AAAA"))
        self.assertIsNone(view.tile_picture(protocol.PICTURE_PREFIX + "%%%%"))
        huge = (b"\xff\xd8\xff\xc0\x00\x11\x08" + (40_000).to_bytes(2, "big") + (40_000).to_bytes(2, "big")
                + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9")
        self.assertIsNone(view.tile_picture(protocol.PICTURE_PREFIX + base64.b64encode(huge).decode("ascii")))


if __name__ == "__main__":
    unittest.main()
