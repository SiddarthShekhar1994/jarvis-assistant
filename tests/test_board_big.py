"""The new look, part 5c: open big (DESIGN-SPEC 3.5). Offscreen; the web engine never starts here: the board is
tests.board_fakes' stand-in (a real BoardView whose page is a plain widget: every message checked and kept; the page's
steps are its requests). tests/test_board_lockdown.py's "big" cases run it in the real page, with a real double click and
real Esc, at 1x and 1.5x.

Covered: the wide view's rectangle at 1120 x 700, 1232 x 640 and 900 x 600 (the column's left edge to the board's right
edge, the body's top to 12 px above the ask bar); step 1 - the backdrop (one JPEG within the picture's caps, painted from
the column and never the view, at 1x and 1.5x), the still over the view while its geometry is set (gone when the page
is laid out, or after 400 ms), the geometry set ONCE with no animation (its changes counted), the Dock and the header
dimmed to 20 % natively, the chips' edge kept from showing under the wide view; the page's step 2 asked with the
board's own box; Esc's order (the page's scale-down, then the geometry set back once, then the native dim lifts; a page
that never says "shut" is gone back from after its grace); a resize while big (still over both columns, set once, the
page told the new box); open big ending at once off Home and going back when its frame leaves; what can't open big; F6
skipping the covered conversation; no words in the logs. Part 5c fixes: the page told before its view is wide; a
"laid" taken only for the step the still waits for; the covered conversation out of the Tab chain and back as it was;
the Dock's and the header's own opacity effects; a resize's backdrop counted; the shut grace's own timer; no eased dim
left behind as an animation.
"""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from briefing_reader import desk_big, desk_view, holo, ui
from briefing_reader.board import protocol
from briefing_reader.board.frames import Built
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
PAGE = {"kind": "picture", "title": "City Hack schedule", "meta": "9:05 AM", "body": {"caption": "city-hack.example.org"}}
DAY = {"kind": "calendar", "title": "Today", "meta": "", "body": {"rows": [
    {"time": "10:00-10:30", "title": "AI-Trader check-in", "meta": "", "state": "next"}]}}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_big", "-platform", "offscreen"])
    fonts()


class Moves(QObject):
    """Every geometry change of the board's view, with the native dim at that moment."""

    def __init__(self, board, reading) -> None:
        super().__init__()
        self.board, self.reading = board, reading
        self.events: list[tuple[str, QRect, float]] = []

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.events.append((event.type().name, QRect(self.board.geometry()), self.reading.chrome_dim()))
        return False

    def geometries(self) -> list[QRect]:
        """The distinct geometries set, in order (a Move and a Resize of one setGeometry are one)."""
        found: list[QRect] = []
        for _kind, rect, _dim in self.events:
            if not found or found[-1] != rect:
                found.append(rect)
        return found


# --------------------------------------------------------------------------
# The wide view's rectangle (desk_view.desk_geometry)
# --------------------------------------------------------------------------

class GeometryTests(unittest.TestCase):
    def test_from_the_column_s_left_edge_to_the_board_s_right_edge_and_above_the_ask_bar(self) -> None:
        for (width, height, folded), big in (((908, 636, False), QRect(24, 0, 860, 548)),     # 1120 x 700
                                             ((1020, 576, False), QRect(24, 0, 972, 488)),    # 1232 x 640
                                             ((824, 536, True), QRect(20, 0, 780, 452))):     # 900 x 600
            with self.subTest(size=(width, height)):
                g = desk_view.desk_geometry(width, height, folded_dock=folded)
                self.assertEqual(g.big, big)
                self.assertEqual(g.big.left(), g.column.left())
                self.assertEqual(g.big.right(), g.view.right())
                self.assertEqual(g.big.top(), 0)                          # the body's top
                self.assertEqual(g.ask.top() - (g.big.bottom() + 1), desk_view.BIG_GAP)   # 12 px above the ask bar
                self.assertFalse(g.big.intersects(g.ask))                 # the ask bar stays outside, live


# --------------------------------------------------------------------------
# The backdrop: one JPEG within the caps, painted from the column (never the view)
# --------------------------------------------------------------------------

class BackdropTests(unittest.TestCase):
    def test_a_jpeg_within_the_picture_s_caps(self) -> None:
        small = QImage(356, 548, QImage.Format.Format_RGB32)
        small.fill(QColor(holo.VOID))
        src = desk_big.jpeg_url(small)
        self.assertEqual(protocol.jpeg_src(src), src)
        import random

        rand = random.Random(3)
        noisy = QImage(1600, 1200, QImage.Format.Format_RGB32)      # far too big at any quality: painted smaller
        for y in range(0, 1200, 2):
            for x in range(0, 1600, 2):
                noisy.setPixelColor(x, y, QColor(rand.randrange(256), rand.randrange(256), rand.randrange(256)))
        src = desk_big.jpeg_url(noisy)
        self.assertTrue(src)
        self.assertLessEqual(len(src), protocol.PICTURE_CAP)
        self.assertEqual(protocol.jpeg_src(src), src)
        self.assertEqual(protocol.check_message({"type": "backdrop", "src": src}).type, "backdrop")


class BigApp(unittest.TestCase):
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

    def answer(self, key: int = 1) -> list[str]:
        desk = self.desk
        self.c._you("What's new with City Hack?")
        desk.on_answer_started(key, "What's new with City Hack?")
        self.c._jarvis("The schedule moved, sir.")
        answer = desk._answers[key]
        answer.done = True
        answer.say = "The schedule moved, sir."
        entries = [(source, lambda fid, data=data: Built(({**data, "type": "frame", "id": fid},), 0), "", "", "")
                   for source, data in (("page", PAGE), ("day", DAY))]
        desk._put(answer, entries)
        desk.feed.set_turn(key, desk._answer_entry())
        desk._sync_counts()
        settle(3)
        return [desk.feed.frame_id(key, "page"), desk.feed.frame_id(key, "day")]

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def watch(self) -> Moves:
        moves = Moves(self.board(), self.reading)
        self.board().installEventFilter(moves)
        self.addCleanup(self.board().removeEventFilter, moves)
        return moves

    def open(self, frame_id: str) -> None:
        self.board().request("open", id=frame_id)                     # the page's Enter, double click or button
        settle()


class OpenTests(BigApp):
    def test_step_one_the_backdrop_then_the_geometry_once_and_the_native_dim(self) -> None:
        for size in ((1120, 700), (900, 600)):
            with self.subTest(size=size):
                self.make(size=size)
                page_id = self.answer()[0]
                desk = self.reading.desk_page()
                geometry = desk.geometry_now()
                moves = self.watch()
                sent_before = len(self.board().sent)
                self.open(page_id)
                self.assertEqual(self.desk.big.state(), "open")
                self.assertEqual(moves.geometries(), [geometry.big])          # set ONCE, never frame by frame
                self.assertEqual(self.board().geometry(), geometry.big)
                sent = [message["type"] for message in self.board().sent[sent_before:]]
                self.assertEqual(sent[sent.index("backdrop"):sent.index("big") + 1], ["backdrop", "big"])
                big = self.board().of_type("big")[-1]
                view = geometry.view
                self.assertEqual(big, {"type": "big", "state": "open", "id": page_id, "x": view.x() - geometry.big.x(),
                                       "y": view.y(), "w": view.width(), "h": view.height()})
                self.assertEqual(self.reading.chrome_dim(), desk_big.DIM)       # the Dock and the header at 20 %
                self.assertTrue(desk.veil.shown())                               # a still while it is set ...
                self.assertTrue(desk.chip_slot.isHidden())                       # (the chips' edge never shows)
                self.board().request("big", step="laid")
                settle()
                self.assertFalse(desk.veil.shown())                              # ... gone once the page is laid out
                self.assertTrue(self.reading.board_big())

    def test_the_backdrop_is_the_column_never_the_view_at_1x_and_1_5x(self) -> None:
        self.make()
        self.answer()
        desk = self.reading.desk_page()
        geometry = desk.geometry_now()
        rect = QRect(geometry.big.x(), 0, geometry.view.x() - geometry.big.x(), geometry.big.height())
        for dpr in (1.0, 1.5):
            with self.subTest(dpr=dpr):
                image = desk.column_image(rect, self.reading.ground_painter(), dpr=dpr)
                self.assertEqual((image.width(), image.height()), (round(rect.width() * dpr),
                                                                   round(rect.height() * dpr)))
                self.assertEqual(image.devicePixelRatio(), dpr)
                seam = round((geometry.seam.x() - rect.x()) * dpr)
                self.assertEqual(QColor(image.pixel(seam, round(200 * dpr))).name(),
                                 QColor(image.pixel(seam, round(201 * dpr))).name())   # the seam's line, drawn
                src = desk_big.jpeg_url(image)
                self.assertEqual(protocol.jpeg_src(src), src)                  # one JPEG within the caps
        self.assertLess(rect.right(), geometry.view.x())                       # left of the view: never the view

    def test_esc_the_scale_down_then_the_geometry_once_then_the_dim_lifts(self) -> None:
        self.make()
        page_id = self.answer()[0]
        geometry = self.reading.desk_page().geometry_now()
        self.open(page_id)
        self.board().request("big", step="laid")
        moves = self.watch()
        self.board().request("big", step="back")                            # Esc in the page: its scale-down starts
        settle()
        self.assertEqual(self.desk.big.state(), "closing")
        self.assertEqual(self.board().geometry(), geometry.big)              # the view still wide meanwhile ...
        self.assertEqual(self.reading.chrome_dim(), desk_big.DIM)            # ... and the dim still on
        self.assertEqual(moves.events, [])
        self.board().request("big", step="shut")                            # the scale-down is over
        settle()
        self.assertEqual(moves.geometries(), [geometry.view])                # the geometry back, once ...
        self.assertTrue(all(dim == desk_big.DIM for _kind, _rect, dim in moves.events))   # ... before the dim lifts
        self.assertEqual(self.reading.chrome_dim(), 1.0)
        self.assertEqual(self.desk.big.state(), "")
        self.assertFalse(self.reading.board_big())
        self.assertFalse(self.reading.desk_page().chip_slot.isHidden())

    def test_a_page_that_never_says_shut_goes_back_after_its_grace(self) -> None:
        # Part 5c fixes: the real timer goes back (it was called by hand, so a timer never connected passed too).
        self.make()
        page_id = self.answer()[0]
        self.open(page_id)
        self.board().request("big", step="back")
        settle()
        timer = self.desk.big._shut_timer                                     # noqa: SLF001
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), desk_big._ms(desk_big.CLOSE_MS) + desk_big.SHUT_GRACE_MS)
        self.assertTrue(wait_for(lambda: self.desk.big.state() == "", 3.0))   # its own timeout, nothing else
        self.assertEqual(self.desk.big.counts["shut_late"], 1)
        self.assertEqual(self.board().geometry(), self.reading.desk_page().geometry_now().view)

    def test_a_resize_while_big_keeps_it_over_both_columns(self) -> None:
        self.make()
        page_id = self.answer()[0]
        self.open(page_id)
        moves = self.watch()
        backdrops = len(self.board().of_type("backdrop"))
        self.window.resize(1232, 640)
        settle(6)
        geometry = self.reading.desk_page().geometry_now()
        self.assertEqual(self.desk.big.state(), "open")
        self.assertEqual(self.board().geometry(), geometry.big)               # both columns, the new layout ...
        self.assertEqual(moves.geometries()[-1], geometry.big)
        self.assertEqual(len(moves.geometries()), 1)                          # ... set once for it
        big = self.board().of_type("big")[-1]
        self.assertEqual((big["x"], big["w"], big["h"]), (geometry.view.x() - geometry.big.x(), geometry.view.width(),
                                                          geometry.view.height()))
        self.assertEqual(len(self.board().of_type("backdrop")), backdrops + 1)   # painted again for it (part 5c
        self.assertEqual(self.desk.big.counts["relaid"], 1)                      # fixes: counted, not just there)

    def test_the_page_is_told_before_its_view_is_wide(self) -> None:
        # Part 5c fixes: the view went wide first, so a page that took the resize first laid its board out across the
        # whole wide view (pairs, three across) under the card.
        self.make()
        page_id = self.answer()[0]
        geometry = self.reading.desk_page().geometry_now()
        board = self.board()
        at_send: list[QRect] = []
        run = board._run

        def recording(text: str) -> None:
            if '"type":"big"' in text:
                at_send.append(QRect(board.geometry()))
            run(text)

        board._run = recording                                                # noqa: SLF001 - what the page gets
        self.addCleanup(setattr, board, "_run", run)
        self.open(page_id)
        self.assertEqual(at_send, [geometry.view])                            # still its column when told
        self.assertEqual(board.geometry(), geometry.big)                      # then wide, once

    def test_a_laid_answers_only_the_step_the_still_waits_for(self) -> None:
        # Part 5c fixes: any "laid" lifted the still, also one for a step already over.
        self.make()
        page_id = self.answer()[0]
        desk = self.reading.desk_page()
        big = self.desk.big
        with mock.patch.object(desk_big.BigController, "_veil", lambda self_, page, rect: page.veil.show_still(
                QPixmap(4, 4), rect)):
            self.open(page_id)
            self.assertEqual(big.laid_for(), "open")
            self.board().request("big", step="back")                          # Esc before the page was laid out
            settle()
            self.assertEqual(big.laid_for(), "")                              # its open's "laid" never comes now
            self.board().request("big", step="laid")                          # so a "laid" now answers nothing
            settle()
            self.assertTrue(desk.veil.shown())
            self.assertEqual(big.counts["laid:ignored"], 1)
            self.board().request("big", step="shut")                          # the scale-down is over: back, a still
            settle()
            self.assertEqual(big.laid_for(), "close")
            self.board().request("big", step="laid")                          # the board at its own size
            settle()
            self.assertFalse(desk.veil.shown())
            self.board().request("big", step="laid")                          # nothing awaited: ignored
            settle()
            self.assertEqual(big.counts["laid:ignored"], 2)

    def test_the_covered_conversation_takes_no_focus_and_comes_back_as_it_was(self) -> None:
        # Part 5c fixes: the turns' tokens and links stayed in the Tab chain under the wide view (Enter acted unseen).
        self.make()
        page_id = self.answer()[0]
        desk = self.reading.desk_page()
        covered = [widget for widget in desk.turns.findChildren(QWidget) + desk.head.findChildren(QWidget)
                   if widget.focusPolicy() & Qt.FocusPolicy.TabFocus and widget.isVisibleTo(desk)]
        self.assertTrue(covered)                                              # a frames token at least

        def chain() -> list[QWidget]:
            found, widget = [], self.window.nextInFocusChain()
            while widget is not None and widget is not self.window and len(found) < 2000:
                if widget.isEnabled() and widget.isVisible() and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                    found.append(widget)
                widget = widget.nextInFocusChain()
            return found

        before = chain()
        self.open(page_id)
        self.assertFalse(any(widget in chain() for widget in covered))       # nothing covered takes focus
        token = covered[0]
        token.setFocus(Qt.FocusReason.TabFocusReason)
        self.assertFalse(token.hasFocus())
        self.board().request("big", step="back")
        self.board().request("big", step="shut")
        settle()
        self.assertEqual(chain(), before)                                     # the order exactly as before

    def test_the_dock_and_the_header_are_dimmed_by_their_own_effects(self) -> None:
        # Part 5c fixes: only the controller's own number was read; the effects on the widgets themselves now.
        self.make()
        page_id = self.answer()[0]
        reading = self.reading
        self.open(page_id)
        for widget in (reading.dock, reading.header):
            effect = widget.graphicsEffect()
            self.assertIsInstance(effect, QGraphicsOpacityEffect)
            self.assertAlmostEqual(effect.opacity(), desk_big.DIM)
        self.board().request("big", step="back")
        self.board().request("big", step="shut")
        settle()
        self.assertIsNone(reading.dock.graphicsEffect())                      # gone after the close ...
        self.assertIsNone(reading.header.graphicsEffect())
        self.open(page_id)
        self.desk.big.end_now()
        self.assertIsNone(reading.dock.graphicsEffect())                      # ... and after end_now
        self.assertIsNone(reading.header.graphicsEffect())

    def test_its_eased_dims_leave_no_animation_behind(self) -> None:
        # Part 5c fixes: each eased change left its QVariantAnimation on the window for its life.
        self.make()
        reading = self.reading
        holo.set_motion_level(holo.MOTION_LIVELY)

        def animations() -> int:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            return len(reading.findChildren(QVariantAnimation, options=Qt.FindChildOption.FindDirectChildrenOnly))

        baseline = animations()
        for _round in range(3):
            reading.set_chrome_dim(desk_big.DIM, 200)
            reading.set_chrome_dim(1.0, 100)                                  # one cut short ...
            self.assertTrue(wait_for(lambda: reading.chrome_dim() == 1.0, 2.0))
        settle()
        self.assertEqual(animations(), baseline)                              # ... and none kept

    def test_it_ends_at_once_off_home(self) -> None:
        self.make()
        page_id = self.answer()[0]
        self.open(page_id)
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.assertEqual(self.desk.big.state(), "")
        self.assertEqual(self.reading.chrome_dim(), 1.0)                     # the Dock and the header never left dim
        self.assertEqual(self.board().of_type("big")[-1], {"type": "big", "state": "close", "id": ""})
        self.assertEqual(self.board().geometry(), self.reading.desk_page().geometry_now().view)

    def test_its_frame_leaving_the_board_sends_it_back(self) -> None:
        self.make()
        page_id = self.answer()[0]
        self.open(page_id)
        self.board().request("close", id=page_id)
        settle()
        self.assertEqual(self.desk.big.state(), "closing")
        self.assertEqual(self.board().of_type("big")[-1]["state"], "close")

    def test_what_can_t_open_big(self) -> None:
        self.make()
        page_id, day_id = self.answer()
        big = self.desk.big
        self.board().request("close", id=day_id)                             # a closed frame
        settle()
        self.open(day_id)
        self.assertEqual(big.counts["refused:frame"], 1)
        self.reading.set_tab(ui.SCREEN_APPROVALS)                            # not on Home
        settle()
        self.desk._on_open(page_id)                                          # noqa: SLF001 - as the page's request
        self.assertEqual(big.counts["refused:not_shown"], 1)
        self.reading.set_tab(ui.SCREEN_HOME)
        settle()
        self.open(page_id)
        self.open(page_id)                                                   # already big
        self.assertEqual(big.counts["refused:busy"], 1)
        self.assertEqual(big.counts["opened"], 1)

    def test_f6_skips_the_covered_conversation(self) -> None:
        self.make()
        page_id = self.answer()[0]
        self.open(page_id)
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        self.reading.focus_ask_bar()
        settle()
        self.reading.cycle_focus(True)                                        # from the ask bar: the board, not the
        settle()                                                              # conversation under the wide view
        self.assertEqual(self.board().sent[-1], {"type": "focus_part", "part": "board"})
        self.board().page_widget.setFocus()
        settle()
        self.reading.cycle_focus(True)
        settle(2)
        self.assertTrue(self.reading.command_bar.isAncestorOf(QApplication.focusWidget()))

    def test_the_logs_hold_no_words(self) -> None:
        self.make()
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            page_id = self.answer()[0]
            self.open(page_id)
            self.board().request("big", step="back")
            self.board().request("big", step="shut")
            logging.getLogger("briefing_reader").debug("marker")
        self.assertFalse(any("City Hack" in line or "city-hack" in line for line in logs.output))
        self.assertTrue(any(f"frame {page_id} open big" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
