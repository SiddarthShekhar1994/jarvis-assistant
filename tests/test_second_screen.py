"""The new look, part 7: the second-screen display (briefing_reader.second_screen), Settings > Look's switch, and the
placement broker the LIVE pop-out and the agent's watch window share it through. Offscreen only: no window on a real
screen, no real monitor layout read (fake screen lists), no Win32 call (tests.second_screen_fakes.FakeNative), no
sound, no model.

Covered: off by default ([look] second_screen the default, the owner's choice saved over it); greyed with one screen
and enabled with two, following monitors plugged in and out while Settings is open; on shows it at once, off hides it
at once; nothing else in Settings changes; the screen choice (2 and 3 screens, the main window moving); the layout at
1920x1080, 2560x1440, 3440x1440 (a centred 16:9 box) and 1440x900 (the strip only), by geometry, with one left edge
for the ring's box and every line; privacy (a mail thread, a mail list and a waiting reply whose bodies hold a marker,
the owner's question and the spoken line: none ever drawn, the mail's text never even held); the window's flags, the
capture exclusion through the fake Win32 layer and a refusal logged; never the focus; each hide rule and its return,
checked at the drift's pace; the drift lattice, the side swap and its 60 s guard, the dim stages and the ring's frame
rates on a fake clock; OLED care (true black, no glow, the core at half, whites capped at OLED_INK, never the desk's
lit glow); the broker's order, the strip fold, the 2 s return, and the watch window and the pop-out exactly as before
with the display off; what the app pushes (the look, the counts, the board's frames as the page has them - Ask's typed
frames as private tiles, a changed frame's new headline - never open big's backdrop or the lit frame); the board off
(the counts' row alone); a picture over 4 MP never decoded; the next event.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import holo, live, look_ui, pictures, prefs, ui
from briefing_reader import orb as orb_mod
from briefing_reader import second_screen as ss
from briefing_reader.actions import parse_action_line
from briefing_reader.agent import browser as agent_browser
from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
from briefing_reader.board import feed as feed_mod
from briefing_reader.board import glance
from briefing_reader.gcal import CalendarEvent
from tests import board_fakes
from tests import second_screen_fakes as fakes
from tests.test_ask_frames import maths, table
from tests.ui_fakes import AppHarness, delete_now, fonts, settle, wait_for

_app: QApplication | None = None
MARK_MAIL = "zq7mailbody"            # unique markers: a mail's text, a waiting reply's, a list row's own field ...
MARK_REPLY = "zq7replybody"
MARK_LIST = "zq7listbody"
MARK_ASK = "zq7question"             # ... the owner's question and the spoken line
MARK_SAY = "zq7spokenline"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_second_screen", "-platform", "offscreen"])
    fonts()


def recorded(window: ss.SecondScreen) -> list[fakes.RecordingPainter]:
    """Every painter the window's own paintEvent makes from now on (a recording one)."""
    painters: list[fakes.RecordingPainter] = []

    def make(device: object) -> fakes.RecordingPainter:
        painter = fakes.RecordingPainter(device)
        painters.append(painter)
        return painter

    window.painter_class = make
    return painters


def drawn(window: ss.SecondScreen) -> list[fakes.RecordingPainter]:
    """The window painted through its paintEvent (a grab) with recording painters."""
    painters = recorded(window)
    window.grab()
    return painters


def texts_of(painters: list[fakes.RecordingPainter]) -> list[str]:
    return [text for painter in painters for text in painter.texts]


def render(window: ss.SecondScreen, *, at: float | None = None) -> fakes.RecordingPainter:
    image = QImage(window.width(), window.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#ff00ff"))
    painter = fakes.RecordingPainter(image)
    window.paint_display(painter, None, now=fakes.NOW, at=at)
    painter.end()
    painter.image = image          # type: ignore[attr-defined]
    return painter


def demo_feed(found: list[tuple[dict, str]] | None = None) -> feed_mod.BoardFeed:
    """A BoardFeed holding the still's frames (one answer's group), as the desk would."""
    board = feed_mod.BoardFeed()
    for index, (frame, src) in enumerate(found if found is not None else fakes.demo_frames()):
        messages = [dict(frame)]
        if src:
            messages.append({"type": "picture", "id": frame["id"], "src": src})
        board.put(1, f"s{index}", messages, when="9:41 AM", topic="Demo")
    return board


class Rig:
    """A controller fed by hand (no app): fake screens side by side, the main window on one of them, the fake OS seam,
    fake hide probes, a fake clock and a broker of its own."""

    def __init__(self, test: unittest.TestCase, *sizes: str, main: int = 0, on: bool = True, board: bool = True,
                 native: fakes.FakeNative | None = None, return_ms: int = ss.RETURN_MS, probes: bool = True) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        test.addCleanup(tmp.cleanup)
        self.prefs = prefs.AssistantPrefs(Path(tmp.name) / prefs.PREFS_FILE)
        self.screens = fakes.spots(*(sizes or ("1920x1080", "1920x1080")))
        self.main = main
        self.native = native or fakes.FakeNative()
        self.probes = fakes.FakeProbes()
        self.clock = fakes.FakeClock()
        self.broker = ss.PlacementBroker(return_ms=return_ms)
        self.feed = demo_feed()
        self.locked = False
        host = fakes.stand_in_host(board=board)
        host._prefs_store = lambda: self.prefs
        host._locked_now = lambda: self.locked
        self.host = host
        self.c = ss.SecondScreenController(host, screens=lambda: self.screens, main_rect=self.main_rect,
                                           native=self.native, probes=self.probes if probes else None,
                                           clock=self.clock, now=lambda: fakes.NOW, board=lambda: self.feed,
                                           placement=self.broker)
        test.addCleanup(self.close)
        if on:
            self.c.set_enabled(True)
            settle()

    def main_rect(self) -> QRect:
        screen = self.screens[self.main].geometry
        return QRect(screen.x() + 100, screen.y() + 100, 1120, 700)

    @property
    def window(self) -> ss.SecondScreen:
        assert self.c.window is not None
        return self.c.window

    def close(self) -> None:
        self.c.shutdown()
        settle()
        delete_now(self.c)
        delete_now(self.broker)
        settle()


# --------------------------------------------------------------------------
# Which screen, and the layout
# --------------------------------------------------------------------------

class ScreenChoiceTests(unittest.TestCase):
    def test_two_and_three_screens_and_the_main_window_moving(self) -> None:
        two = fakes.spots("1920x1080", "2560x1440")
        self.assertEqual(ss.choose_screen(two, two[0]).key, two[1].key)
        self.assertEqual(ss.choose_screen(two, two[1]).key, two[0].key)          # the main window moved there
        three = fakes.spots("1920x1080", "2560x1440", "1920x1200")
        self.assertEqual(ss.choose_screen(three, three[0]).key, three[1].key)    # the largest other one
        self.assertEqual(ss.choose_screen(three, three[1]).key, three[2].key)    # 1920x1200 is larger than 1920x1080
        self.assertIsNone(ss.choose_screen(three[:1], three[0]))                 # one screen: none
        self.assertEqual(ss.far_edge(two[1].geometry, two[0].geometry), ss.RIGHT)
        self.assertEqual(ss.far_edge(two[0].geometry, two[1].geometry), ss.LEFT)

    def test_the_controller_follows_the_main_window(self) -> None:
        rig = Rig(self, "1920x1080", "2560x1440", "1920x1200")
        self.assertTrue(rig.c.shown())
        self.assertEqual(rig.window.geometry(), rig.screens[1].geometry)          # the largest other screen
        rig.main = 1                                                               # the main window moves there ...
        rig.c.sync()
        self.assertEqual(rig.window.geometry(), rig.screens[2].geometry)          # ... the display moves on
        self.assertEqual(rig.c.target().key, rig.screens[2].key)
        rig.screens = rig.screens[:2]                                              # a monitor is unplugged
        rig.c.sync()
        self.assertEqual(rig.window.geometry(), rig.screens[0].geometry)          # the screen the main window left
        rig.screens = rig.screens[1:]                                              # one screen left
        rig.main = 0
        rig.c.sync()
        self.assertFalse(rig.c.shown())
        self.assertFalse(rig.window.isVisible())
        self.assertEqual(rig.c.reason(), ss.R_ONE_SCREEN)


class LayoutTests(unittest.TestCase):
    def test_1920x1080(self) -> None:
        layout = ss.display_layout(1920, 1080)
        self.assertEqual(layout.scale, 1.0)
        self.assertEqual((layout.ring.left(), layout.ring.top()), (96.0, 72.0))  # the ring's box (its brackets)
        self.assertEqual(layout.column.left(), 96.0)                             # one left edge
        self.assertEqual(layout.ring_px, 320)
        self.assertEqual(layout.wall, QRectF(640, 72, 1200, 936))                # x 640 to 1840
        self.assertEqual(len(layout.tiles), 9)
        self.assertEqual(layout.tiles[0], QRectF(640, 120, 384, 280))            # 3 x 3, 24 px gaps
        self.assertEqual(layout.tiles[4], QRectF(1048, 424, 384, 280))
        self.assertEqual(layout.tiles[8], QRectF(1456, 728, 384, 280))
        self.assertAlmostEqual(layout.word_base, 72 + ss.ring_box(320) + 24, 3)
        self.assertAlmostEqual(layout.clock_base - layout.word_base, 144, 3)

    def test_2560x1440_scales_by_s(self) -> None:
        layout = ss.display_layout(2560, 1440)
        s = 2560 / 1920
        self.assertAlmostEqual(layout.scale, s, 6)
        self.assertEqual(layout.ring_px, 427)                                    # the ring 427
        self.assertAlmostEqual(layout.ring.left(), 96 * s, 3)
        self.assertAlmostEqual(layout.tiles[0].width(), 384 * s, 3)              # tiles about 512 x 373
        self.assertAlmostEqual(layout.tiles[0].height(), 280 * s, 3)
        font, _metrics = ss.role_font("clock", s)
        self.assertEqual(font.pixelSize(), 160)                                   # the clock 160

    def test_3440x1440_centres_a_16_9_box(self) -> None:
        layout = ss.display_layout(3440, 1440)
        self.assertEqual(layout.box, QRectF(440, 0, 2560, 1440))
        self.assertAlmostEqual(layout.ring.left(), 440 + 128, 3)
        for rect in (layout.ring, layout.wall, *layout.tiles):
            self.assertTrue(layout.box.contains(rect), rect)
        self.assertAlmostEqual(layout.wall.right(), 440 + 1840 * 2560 / 1920, 3)

    def test_1440x900_shows_only_the_strip(self) -> None:
        self.assertTrue(ss.strip_only(QRect(0, 0, 1440, 900)))
        self.assertFalse(ss.strip_only(QRect(0, 0, 1600, 900)))
        rig = Rig(self, "1920x1080", "1440x900")
        window = rig.window
        self.assertEqual(window.mode(), ss.STRIP)
        strip = ss.strip_rect(rig.screens[1].geometry, ss.RIGHT)
        self.assertEqual(window.geometry(), strip)                               # on the far edge, full height
        self.assertEqual((strip.right(), strip.height()), (1920 + 1440 - 1, 900))
        self.assertEqual(strip.width(), round(360 * 900 / 1080))
        layout = window.layout_now()
        self.assertTrue(layout.strip)
        self.assertIsNone(layout.wall)
        self.assertEqual(layout.ring_px, round(200 * 900 / 1080))               # the ring at 200 x s
        window.set_state(fakes.demo_state())
        texts = render(window).texts
        self.assertIn("9:41", texts)
        self.assertIn("1 needs your OK", texts)
        self.assertNotIn("NEXT", texts)                                           # the strip: ring, word, clock, counts
        self.assertFalse(any(text.startswith("BOARD") for text in texts))

    def test_one_left_edge_for_the_ring_and_every_line(self) -> None:
        rig = Rig(self)
        window = rig.window
        window.set_state(fakes.demo_state())
        window.freeze(3.0)
        painter = render(window, at=rig.clock())
        column = [(x, y, text) for x, y, text in painter.placed if x < 600]
        lefts = {text: x for x, _y, text in column}
        self.assertEqual(window.layout_now().ring.left(), 96.0)
        for text in ("9:41", "TUESDAY 17 NOVEMBER", "NEXT", "10:00", "In 19 minutes · video call"):
            self.assertAlmostEqual(lefts[text], 96.0, 3, text)                  # a line starts at x 96
        self.assertAlmostEqual(lefts["NEEDS YOU"], 116.0, 3)                     # after its 10 px mark at x 96
        self.assertAlmostEqual(lefts["1 needs your OK"], 124.0, 3)               # after its shape at x 96
        self.assertAlmostEqual(min(x for x, _y, _text in column), 96.0, 3)


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------

class PrivacyTests(unittest.TestCase):
    def test_no_mail_text_is_ever_drawn_or_even_held(self) -> None:
        found = fakes.demo_frames(MARK_MAIL)                                      # the thread's text
        found = [found[1], (fakes.outgoing_frame(MARK_REPLY), ""),                # a reply that waits on Approvals
                 (fakes.followups_frame(MARK_LIST), ""), found[0], found[2]]      # a mail list (its items' own words)
        self.assertIn(MARK_MAIL, json.dumps(found[0][0]))                         # the board's frame has the text ...
        self.assertIn(MARK_REPLY, json.dumps(found[1][0]))
        rig = Rig(self)
        rig.feed = demo_feed(found)
        rig.c.on_board(rig.feed)
        held = json.dumps(rig.c.state.tiles)
        for marker in (MARK_MAIL, MARK_REPLY, MARK_LIST):
            self.assertNotIn(marker, held)                                        # ... the display never holds it
        self.assertIn("mail_list", held)
        texts = texts_of(drawn(rig.window))
        self.assertTrue(texts)
        for marker in (MARK_MAIL, MARK_REPLY, MARK_LIST, "Q4 numbers", "numbers look right"):
            self.assertFalse(any(marker in text for text in texts), marker)
        self.assertIn(glance.HIDDEN, texts)                                       # "Text hidden on this screen"
        self.assertIn("Dana Whitfield", texts)                                    # senders and subjects stay
        self.assertIn("Lab section moved to Thursday", texts)
        self.assertIn("Prof. Ana Ruiz", texts)
        self.assertIn("Reply waits on Approvals", texts)
        self.assertIn(ss.MAIL_HIDDEN, texts)

    def test_a_list_row_with_a_body_of_its_own_never_reaches_the_wall(self) -> None:
        listed = fakes.demo_frames()[6][0]
        self.assertEqual(listed["kind"], "mail_list")
        listed["body"]["rows"][0]["text"] = f"{MARK_LIST} the mail's own words"   # a field no list row has
        board = demo_feed([(listed, "")])
        frames, _pictures = ss.wall_frames(board)
        self.assertEqual(frames, [])                                              # refused as the page refuses it
        self.assertNotIn(MARK_LIST, json.dumps(ss.private_frame(listed)))         # and private_frame keeps 5 fields

    def test_even_frames_given_by_hand_never_show_a_mail_s_text(self) -> None:
        rig = Rig(self)
        frame = fakes.outgoing_frame(MARK_REPLY)
        rig.window.set_state(replace(fakes.demo_state(), tiles=(frame,), pictures={}))   # not through private_frame
        texts = render(rig.window).texts
        self.assertFalse(any(MARK_REPLY in text for text in texts))               # glance's private mode holds too
        self.assertIn(glance.HIDDEN, texts)

    def test_the_app_never_brings_the_question_the_spoken_line_or_mail_text(self) -> None:
        boards: list = []
        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(boards))
        harness.reading(autoplay=False)
        c = harness.c
        c.window.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        second = c.look.second
        self.addCleanup(ss.broker().reset)
        self.addCleanup(second.shutdown)
        second.use(screens=lambda: fakes.spots("1920x1080", "1920x1080"), main_rect=lambda: QRect(100, 100, 1120, 700),
                   probes=fakes.FakeProbes(), native=fakes.FakeNative())
        second.set_enabled(True)
        settle()
        desk = c.look.desk
        thread_id = "18c0ffee00000077"
        task = c.live.task(live.TASK_ASK, "Ask")
        step = task.step(live.MAIL_THREAD, "Read email thread", key=f"work:{thread_id}")
        step.picture(pictures.email_picture(MailThread("work", thread_id, "Re: Budget numbers", (MailMessage(
            "g1", sender=MailPerson("dana@example.edu", "Dana Whitfield"), sent_at=fakes.at(8, 12),
            subject="Re: Budget numbers", text=f"The numbers moved {MARK_MAIL}."),))))
        reply = parse_action_line(f"Reply: acct=work | thread={thread_id} | msgid=<abc@mail.example.edu> | gmid= | "
                                  "to=dana@example.edu | cc= | subject=Re: Budget numbers | replied=no | due= | link= | "
                                  f"body=Thanks Dana {MARK_REPLY}.")
        question = f"Did Dana send the numbers? {MARK_ASK}"
        said = f"Dana sent the numbers, sir. {MARK_SAY}"
        c._you(question)
        desk.on_answer_started(task.id, question)
        c._live_feed.drain()
        c._jarvis(said)
        desk.on_ask_outcome(SimpleNamespace(ok=True, say=said, cards=(reply,), research=None, calendar_read=None,
                                            mode=""), task.id)
        settle(4)
        window = second.window
        self.assertIsNotNone(window)
        self.assertTrue(second.state.tiles)                                       # the board reached it
        self.assertEqual(second.state.tiles[0]["waits"], "Reply waits on Approvals")   # the reply's chip, merged
        texts = texts_of(drawn(window))
        for marker in (MARK_MAIL, MARK_REPLY, MARK_ASK, MARK_SAY):
            self.assertFalse(any(marker in text for text in texts), marker)
            self.assertNotIn(marker, json.dumps(second.state.tiles))
        self.assertIn("Re: Budget numbers", texts)
        self.assertIn("Reply waits on Approvals", texts)
        self.assertIn(glance.HIDDEN, texts)


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

class WindowTests(unittest.TestCase):
    def test_never_activated_never_on_top_never_in_the_taskbar_and_out_of_capture(self) -> None:
        rig = Rig(self)
        window = rig.window
        self.assertTrue(window.isVisible())
        flags = window.windowFlags()
        self.assertEqual(window.windowType(), Qt.WindowType.Tool)                # a tool window: no taskbar, no Alt+Tab
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowDoesNotAcceptFocus,
                     Qt.WindowType.WindowTransparentForInput):
            self.assertTrue(flags & flag, flag)
        self.assertFalse(flags & Qt.WindowType.WindowStaysOnTopHint)            # never topmost
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        self.assertEqual(window.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertIsNone(window.parent())
        handle = window.windowHandle()
        self.assertTrue(handle.flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertIs(handle.property("_q_showWithoutActivating"), True)
        hwnd = int(window.winId())
        self.assertEqual(rig.native.calls[:3], [("no_activate", hwnd), ("exclude_from_capture", hwnd),
                                                ("watch_power", hwnd)])            # on its OWN window, once
        self.assertTrue(window.excluded)
        rig.c.sync()
        window.hide()
        window.show_quietly()
        settle()
        self.assertEqual(rig.native.names().count("exclude_from_capture"), 1)
        self.assertTrue(ss.Win32Native.__doc__)                                   # (the real seam exists: never run)

    def test_a_refused_capture_exclusion_is_logged_and_it_still_shows(self) -> None:
        with self.assertLogs("briefing_reader.second_screen", "WARNING") as logs:
            rig = Rig(self, native=fakes.FakeNative(refuse=True))
        self.assertTrue(any("refused" in line for line in logs.output), logs.output)
        self.assertTrue(rig.window.isVisible())
        self.assertFalse(rig.window.excluded)
        self.assertTrue(rig.window.refusal.startswith("refused"))

    def test_it_never_takes_the_focus(self) -> None:
        other = QWidget()
        self.addCleanup(lambda: delete_now(other))
        self.addCleanup(other.hide)
        other.resize(300, 200)
        other.show()
        other.activateWindow()
        settle(4)
        focus = QGuiApplication.focusWindow()
        rig = Rig(self)
        settle(4)
        self.assertTrue(rig.window.isVisible())
        self.assertIs(QGuiApplication.focusWindow(), focus)
        self.assertIsNot(QApplication.activeWindow(), rig.window)
        rig.c.on_board(rig.feed)
        rig.window.care_tick(rig.clock.advance(31))
        settle(2)
        self.assertIs(QGuiApplication.focusWindow(), focus)

    def test_the_default_native_layer_is_none_under_the_test_guard(self) -> None:
        self.assertIsInstance(ss.default_native(), ss.NoNative)
        with self.assertRaises(RuntimeError):
            ss.Win32Native()


class HideTests(unittest.TestCase):
    RULES = (("locked", ss.R_LOCKED), ("display_off", ss.R_DISPLAY_OFF), ("battery_saver", ss.R_BATTERY),
             ("full_screen", ss.R_FULL_SCREEN))

    def test_each_rule_hides_it_and_it_returns(self) -> None:
        rig = Rig(self)
        for flag, word in self.RULES:
            with self.subTest(rule=word):
                rig.probes.flags[flag] = True
                rig.c._on_care()                                                  # the drift timer's tick
                self.assertFalse(rig.c.shown())
                self.assertFalse(rig.window.isVisible())
                self.assertEqual(rig.c.reason(), word)
                self.assertTrue(rig.c._care.isActive())                           # checked again at the drift's pace
                self.assertEqual(rig.c._care.interval(), int(ss.DRIFT_S * 1000))
                rig.probes.flags[flag] = False
                rig.c._on_care()
                self.assertTrue(rig.c.shown())
                self.assertTrue(rig.window.isVisible())
                self.assertEqual(rig.c.reason(), "")

    def test_the_probes_ask_the_app_and_the_native_layer(self) -> None:
        rig = Rig(self, probes=False)                                             # the controller's own probes
        cases = (("locked", lambda on: setattr(rig, "locked", on)),
                 ("display_off", lambda on: setattr(rig.native, "display", not on)),
                 ("battery_saver", lambda on: setattr(rig.native, "saver", on)),
                 ("full_screen", lambda on: setattr(rig.native, "covered", on)))
        for word, switch in cases:
            with self.subTest(rule=word):
                switch(True)
                rig.c.sync()
                self.assertEqual(rig.c.reason(), word)
                switch(False)
                rig.c.sync()
                self.assertTrue(rig.c.shown())

    def test_off_or_one_screen_stops_its_clocks(self) -> None:
        rig = Rig(self)
        self.assertTrue(rig.c._care.isActive())
        rig.c.set_enabled(False)
        self.assertFalse(rig.c.shown())
        self.assertEqual(rig.c.reason(), ss.R_OFF)
        self.assertFalse(rig.c._care.isActive())
        self.assertFalse(rig.window.ring_timer().isActive())


# --------------------------------------------------------------------------
# OLED care
# --------------------------------------------------------------------------

class CareTests(unittest.TestCase):
    def test_the_drift_lattice(self) -> None:
        care = ss.Care(0.0)
        points = [care.drift(30.0 * step) for step in range(9)]
        self.assertEqual(len(set(points)), 9)                                     # every point of the 3 x 3 lattice
        self.assertEqual({dx for dx, _dy in points}, {-6, 0, 6})
        self.assertEqual({dy for _dx, dy in points}, {-6, 0, 6})
        self.assertEqual(care.drift(29.9), care.drift(0.0))                       # a step every 30 s
        self.assertEqual(care.drift(270.0), care.drift(0.0))                      # and round again
        self.assertEqual(ss.Care(0.0).drift(30.0, scale=4 / 3), (8, 0))           # 6 px x s

    def test_the_sides_swap_hourly_never_within_a_minute_of_a_board_change(self) -> None:
        care = ss.Care(0.0)
        self.assertFalse(care.check_swap(3599.0))
        self.assertTrue(care.check_swap(3600.0))
        self.assertTrue(care.swapped)
        care.touch(7190.0, board=True)
        self.assertFalse(care.check_swap(7200.0))                                 # 10 s after a board change: held
        self.assertFalse(care.check_swap(7249.0))
        self.assertTrue(care.check_swap(7250.0))                                  # a minute after it: now
        self.assertFalse(care.swapped)
        swapped = ss.display_layout(1920, 1080, swapped=True)
        self.assertAlmostEqual(swapped.column.left(), 1920 - 96 - 440, 3)         # the column on the right ...
        self.assertAlmostEqual(swapped.wall.left(), 80, 3)                        # ... the wall on the left

    def test_dim_stages_and_frame_rates(self) -> None:
        care = ss.Care(0.0)
        for at, stage, level, interval in ((0.0, ss.LIT, 1.0, 100), (299.0, ss.LIT, 1.0, 100),
                                           (300.0, ss.DIM, 0.6, 500), (1199.0, ss.DIM, 0.6, 500),
                                           (1200.0, ss.DEEP, 0.3, None)):
            with self.subTest(at=at):
                self.assertEqual(care.stage(at), stage)
                self.assertEqual(care.level(at), level)
                self.assertEqual(care.ring_interval(at), interval)
        self.assertIsNone(care.ring_interval(0.0, still=True))                    # motion off: still
        care.touch(1300.0)
        self.assertEqual(care.stage(1300.0), ss.LIT)

    def test_the_window_s_ring_runs_at_10_fps_2_fps_dimmed_still_in_the_deep_dim(self) -> None:
        holo.set_motion_level(holo.MOTION_LIVELY)
        rig = Rig(self)
        window = rig.window
        timer = window.ring_timer()
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 100)
        window.care_tick(rig.clock.advance(301))
        self.assertEqual(timer.interval(), 500)
        window.care_tick(rig.clock.advance(900))
        self.assertFalse(timer.isActive())
        first = window.ring.sample().t
        time.sleep(0.05)
        self.assertEqual(window.ring.sample().t, first)                           # the deep dim: the ring stands still
        rig.c.note_input()                                                         # the owner's input: lit again
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 100)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        window.care_tick(rig.clock())
        self.assertFalse(timer.isActive())                                         # motion off: still

    def test_quiet_ends_with_a_board_change_or_input(self) -> None:
        rig = Rig(self)
        window = rig.window
        rig.clock.advance(301)
        self.assertEqual(window.care.stage(rig.clock()), ss.DIM)
        found = fakes.demo_frames()
        rig.feed = demo_feed(found[:4])
        rig.c.on_board(rig.feed)                                                   # a board change
        self.assertEqual(window.care.stage(rig.clock()), ss.LIT)
        self.assertEqual(window.care.board_at, rig.clock())
        rig.clock.advance(1300)
        self.assertEqual(window.care.stage(rig.clock()), ss.DEEP)
        rig.c.note_input()
        self.assertEqual(window.care.stage(rig.clock()), ss.LIT)
        rig.clock.advance(301)
        rig.c.on_board(rig.feed)                                                   # nothing new on the wall
        self.assertEqual(window.care.stage(rig.clock()), ss.DIM)

    def test_the_dims_and_the_deep_dim_s_picture(self) -> None:
        rig = Rig(self)
        window = rig.window
        window.set_state(fakes.demo_state())
        window.freeze(3.0)

        def brightest(painter: fakes.RecordingPainter) -> int:
            image = painter.image                                                  # type: ignore[attr-defined]
            return max(max(image.pixelColor(x, y).red(), image.pixelColor(x, y).green(),
                           image.pixelColor(x, y).blue()) for x in range(96, 420, 3) for y in range(470, 580, 3))

        lit = render(window, at=rig.clock())
        dim = render(window, at=rig.clock() + ss.DIM_AFTER_S + 1)
        deep = render(window, at=rig.clock() + ss.DEEP_AFTER_S + 1)
        self.assertAlmostEqual(brightest(dim) / brightest(lit), 0.6, delta=0.03)
        self.assertAlmostEqual(brightest(deep) / brightest(lit), 0.3, delta=0.03)
        self.assertEqual(deep.texts, ["9:41", "AM"])                              # only the still ring and the clock
        self.assertIn("BOARD · UPDATED 9:41 AM".upper(), [text.upper() for text in dim.texts])

    def test_true_black_no_glow_the_core_at_half_and_whites_capped(self) -> None:
        rig = Rig(self)
        window = rig.window
        frames: list = []
        real = window.ring.paint_into

        def keep(painter, origin, frame=None, **kwargs):
            frames.append(frame)
            return real(painter, origin, frame, **kwargs)

        window.ring.paint_into = keep
        painter = render(window, at=rig.clock())
        image = painter.image                                                      # type: ignore[attr-defined]
        for x, y in ((0, 0), (1919, 1079), (1036, 300), (600, 900), (1900, 60)):
            self.assertEqual(image.pixelColor(x, y).name(), "#000000", (x, y))    # true black, no grid
        cap = glance._luma(QColor(holo.OLED_INK))                                 # noqa: SLF001
        brightest = max(glance._luma(image.pixelColor(x, y))                      # noqa: SLF001
                        for x in range(0, 1920, 2) for y in range(0, 1080, 2))
        self.assertLessEqual(brightest, cap + 1.0)                                # no white over OLED_INK
        self.assertTrue(frames)
        frame = frames[-1]
        self.assertEqual(frame.glow, 0.0)                                          # no glow
        self.assertLessEqual(frame.core_op, ss.CORE_SHARE + 1e-9)                  # the core at 50 %
        self.assertFalse(any(color == orb_mod.C_WHITE for _look, _w, _a, colors in frame.bands for color in colors))


# --------------------------------------------------------------------------
# The placement broker
# --------------------------------------------------------------------------

class _Screen:
    """A QScreen's surface for WatchWindow.place (a test's own geometry; never a real monitor)."""

    def __init__(self, spot: ss.ScreenSpot) -> None:
        self._spot = spot

    def geometry(self) -> QRect:
        return QRect(self._spot.geometry)

    def availableGeometry(self) -> QRect:  # noqa: N802 - QScreen's name
        return self._spot.work()


class BrokerTests(unittest.TestCase):
    SCREEN = QRect(1920, 0, 1920, 1080)
    AREA = QRect(1920, 0, 1920, 1032)

    def test_the_pop_out_and_the_watch_window_go_first_and_it_folds_to_its_strip(self) -> None:
        broker = ss.PlacementBroker(return_ms=60)
        self.addCleanup(lambda: delete_now(broker))
        self.assertEqual(broker.room(self.SCREEN, self.AREA), self.AREA)          # no display: as it was
        broker.set_display(self.SCREEN, ss.RIGHT)
        room = broker.room(self.SCREEN, self.AREA, ss.WATCH)
        self.assertEqual(room, QRect(1920, 0, 1920 - 360, 1032))                 # the rest, the strip kept
        self.assertEqual(broker.room(self.SCREEN, self.AREA, ss.LIVE), room)
        self.assertEqual(broker.room(QRect(0, 0, 1920, 1080), QRect(0, 0, 1920, 1032)), QRect(0, 0, 1920, 1032))
        broker.set_display(self.SCREEN, ss.LEFT)
        self.assertEqual(broker.room(self.SCREEN, self.AREA), QRect(1920 + 360, 0, 1920 - 360, 1032))
        broker.set_display(self.SCREEN, ss.RIGHT)
        for who in (ss.LIVE, ss.WATCH):
            with self.subTest(who=who):
                broker.claim(who, QRect(0, 0, 800, 600))                          # on the main screen: nothing folds
                self.assertFalse(broker.folded())
                broker.claim(who, QRect(1960, 40, 1200, 800))                     # on the display's screen: it folds
                self.assertTrue(broker.folded())
                broker.claim(who, None)
                self.assertTrue(broker.folded())                                  # ... and comes back RETURN_MS later
                self.assertTrue(wait_for(lambda: not broker.folded(), 2.0))
        broker.claim(ss.LIVE, QRect(1960, 40, 1200, 800))
        broker.claim(ss.WATCH, QRect(1960, 40, 1200, 800))
        broker.claim(ss.LIVE, None)
        settle(3)
        self.assertTrue(broker.folded())                                          # the watch window is still there
        broker.claim(ss.WATCH, None)
        self.assertTrue(wait_for(lambda: not broker.folded(), 2.0))

    def test_it_comes_back_two_seconds_after_they_close(self) -> None:
        broker = ss.PlacementBroker()
        self.addCleanup(lambda: delete_now(broker))
        broker.set_display(self.SCREEN, ss.RIGHT)
        broker.claim(ss.WATCH, QRect(1960, 40, 1200, 800))
        broker.claim(ss.WATCH, None)
        self.assertTrue(broker.folded())
        self.assertTrue(broker._timer.isActive())                                 # noqa: SLF001
        self.assertEqual(broker._timer.interval(), 2000)                          # noqa: SLF001

    def test_the_display_folds_beside_them_and_returns(self) -> None:
        rig = Rig(self, return_ms=60)
        window = rig.window
        self.assertEqual(window.mode(), ss.FULL)
        display = rig.screens[1].geometry
        rig.broker.claim(ss.WATCH, QRect(display.x() + 40, 40, 1200, 800))
        settle()
        self.assertEqual(window.mode(), ss.STRIP)
        self.assertEqual(window.geometry(), ss.strip_rect(display, ss.RIGHT))     # 360 px on the far edge
        self.assertEqual(window.layout_now().ring_px, 200)                        # the ring at 200
        window.set_state(fakes.demo_state())
        texts = render(window).texts
        self.assertIn("9:41", texts)
        self.assertIn("1 needs your OK", texts)
        rig.broker.claim(ss.WATCH, None)
        self.assertTrue(wait_for(lambda: window.mode() == ss.FULL, 2.0))
        self.assertEqual(window.geometry(), display)

    def test_with_the_display_off_the_watch_window_goes_where_it_went(self) -> None:
        ss.broker().reset()
        self.addCleanup(ss.broker().reset)
        main, other = (_Screen(spot) for spot in fakes.spots("1920x1080", "2560x1440"))
        window = agent_browser.WatchWindow(native=agent_browser.hud_overlay.NoNative())
        self.addCleanup(lambda: delete_now(window))
        self.addCleanup(window.hide)
        window.place(None, [main, other])
        self.assertEqual(window.geometry(), agent_browser.watch_rect(other.availableGeometry(), None, True))
        window.place(None, [main])
        self.assertEqual(window.geometry(), agent_browser.watch_rect(main.availableGeometry(), None, False))

    def test_with_the_display_on_the_watch_window_keeps_clear_of_its_strip(self) -> None:
        broker = ss.broker()
        broker.reset()
        self.addCleanup(broker.reset)
        main, other = (_Screen(spot) for spot in fakes.spots("1920x1080", "1920x1080"))
        broker.set_display(other.geometry(), ss.RIGHT)
        window = agent_browser.WatchWindow(native=agent_browser.hud_overlay.NoNative())
        self.addCleanup(lambda: delete_now(window))
        self.addCleanup(window.hide)
        window.place(None, [main, other])
        strip = ss.strip_rect(other.geometry(), ss.RIGHT)
        self.assertFalse(window.geometry().intersects(strip))
        self.assertEqual(window.geometry(),
                         agent_browser.watch_rect(broker.room(other.geometry(), other.availableGeometry()), None, True))
        self.assertFalse(broker.folded())
        window.show_quietly()
        settle(3)
        self.assertTrue(broker.folded())                                          # it showed there: the display folds
        window.hide()
        settle(3)
        self.assertTrue(broker.folded())
        self.assertTrue(broker._timer.isActive())                                 # noqa: SLF001 - back 2 s later

    def test_the_pop_out_goes_where_it_went_with_the_display_off_and_clear_of_its_strip_with_it_on(self) -> None:
        ss.broker().reset()
        self.addCleanup(ss.broker().reset)
        harness = AppHarness(self)
        harness.reading(autoplay=False)
        c = harness.c
        main_screen = c.window.screen()
        other = _Screen(fakes.spots("1920x1080", "2560x1440")[1])
        def screen_at(point):
            return other if other.geometry().contains(point) else main_screen

        with mock.patch.object(ui.QGuiApplication, "screens", staticmethod(lambda: [main_screen, other])), \
                mock.patch.object(ui.QGuiApplication, "screenAt", staticmethod(screen_at)):
            window = c._ensure_live_window()
            c._live_placed = False
            c._place_live_window(window)
            area = other.availableGeometry()
            expected = window.size()
            x = area.x() + (area.width() - expected.width()) // 2
            y = area.y() + (area.height() - expected.height()) // 2
            self.assertEqual((window.x(), window.y()), (x, y))                    # as before part 7: centred there
            ss.broker().set_display(other.geometry(), ss.RIGHT)
            c._live_placed = False
            c._place_live_window(window)
            strip = ss.strip_rect(other.geometry(), ss.RIGHT)
            self.assertFalse(window.frameGeometry().intersects(strip))            # clear of the display's strip
        self.assertIn(ss.LIVE, ss.broker()._watched)                             # noqa: SLF001 - it is followed

    def test_it_never_sounds(self) -> None:
        harness = AppHarness(self)
        c = harness.c
        second = c.look.second
        self.addCleanup(ss.broker().reset)
        self.addCleanup(second.shutdown)
        played: list = []
        with mock.patch.object(c.look.sounds, "play", side_effect=lambda *args: played.append(args)):
            second.use(screens=lambda: fakes.spots("1920x1080", "1920x1080"), main_rect=lambda: QRect(0, 0, 900, 600),
                       probes=fakes.FakeProbes(), native=fakes.FakeNative())
            second.set_enabled(True)
            second.on_board(demo_feed())
            second._on_care()
            ss.broker().claim(ss.WATCH, QRect(1960, 40, 1200, 800))
            ss.broker().claim(ss.WATCH, None)
            settle()
        self.assertEqual(played, [])


# --------------------------------------------------------------------------
# Settings > Look and the app's feed
# --------------------------------------------------------------------------

class SettingsTests(unittest.TestCase):
    def app(self, **kwargs) -> AppHarness:
        harness = AppHarness(self, **kwargs)
        self.addCleanup(ss.broker().reset)
        self.addCleanup(harness.c.look.second.shutdown)
        return harness

    def test_off_by_default(self) -> None:
        harness = self.app()
        second = harness.c.look.second
        self.assertFalse(second.is_on())
        self.assertIsNone(second.window)
        self.assertEqual(second.reason(), ss.R_OFF)
        self.assertIsNone(harness.c._prefs_store().look.second_screen)

    def test_config_s_default_and_the_owner_s_choice_saved_over_it(self) -> None:
        harness = self.app(config_extra="\n[look]\nsecond_screen = true\n")
        second = harness.c.look.second
        self.assertTrue(second.is_on())
        self.assertTrue(second.set_enabled(False))
        self.assertFalse(second.is_on())
        again = prefs.AssistantPrefs(harness.config.data_dir / prefs.PREFS_FILE)
        self.assertIs(again.look.second_screen, False)                            # Off sticks over [look]'s true

    def test_greyed_with_one_monitor_and_following_monitors_plugged_in_and_out(self) -> None:
        harness = self.app()
        c = harness.c
        page = c.window.reading.settings_page
        page.select_group(look_ui.SETTINGS_KEY)                                   # Settings open on Look
        panel = c.look.panel
        second = c.look.second
        screens = fakes.spots("1920x1080")
        second.use(screens=lambda: screens, main_rect=lambda: QRect(100, 100, 1120, 700), probes=fakes.FakeProbes(),
                   native=fakes.FakeNative())
        c.look._sync_panel()
        self.assertFalse(panel.second.isEnabled())                                # greyed ...
        self.assertTrue(panel.second_note.isVisibleTo(panel))                     # ... "Needs a second monitor"
        self.assertEqual(panel.second_note.text(), look_ui.SECOND_NEEDS)
        self.assertEqual(panel.second.text(), look_ui.SECOND_LABEL)
        screens.extend(fakes.spots("1920x1080", "2560x1440")[1:])                # plugged in
        second.sync()
        self.assertTrue(panel.second.isEnabled())
        self.assertFalse(panel.second_note.isVisibleTo(panel))
        del screens[1:]                                                            # and out again
        second.sync()
        self.assertFalse(panel.second.isEnabled())
        self.assertTrue(panel.second_note.isVisibleTo(panel))

    def test_on_shows_it_at_once_and_off_hides_it_at_once(self) -> None:
        harness = self.app()
        c = harness.c
        c.window.reading.settings_page.select_group(look_ui.SETTINGS_KEY)
        panel = c.look.panel
        second = c.look.second
        second.use(screens=lambda: fakes.spots("1920x1080", "1920x1080"), main_rect=lambda: QRect(100, 100, 1120, 700),
                   probes=fakes.FakeProbes(), native=fakes.FakeNative())
        c.look._sync_panel()
        self.assertTrue(panel.second.isEnabled())
        self.assertFalse(panel.second.isChecked())
        panel.second.click()
        self.assertTrue(second.shown())
        self.assertTrue(second.window.isVisible())
        self.assertTrue(panel.second.isChecked())
        self.assertIs(c._prefs_store().look.second_screen, True)
        panel.second.click()
        self.assertFalse(second.shown())
        self.assertFalse(second.window.isVisible())
        self.assertIs(c._prefs_store().look.second_screen, False)

    def test_nothing_else_in_settings_changes(self) -> None:
        harness = self.app()
        page = harness.c.window.reading.settings_page
        page.select_group(look_ui.SETTINGS_KEY)
        self.assertEqual(page.rows(), [(look_ui.SOUNDS_LABEL, look_ui.SOUNDS_SOFT_VALUE), (look_ui.BOARD_LABEL, "On")])
        self.assertEqual(harness.c.look.panel.button.text(), look_ui.TURN_OFF_TEXT)


class FeedTests(unittest.TestCase):
    def app(self, **kwargs) -> tuple[AppHarness, list]:
        boards: list = []
        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(boards), **kwargs)
        harness.reading(autoplay=False)
        c = harness.c
        c.window.resize(1120, 700)
        c.window.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        second = c.look.second
        self.addCleanup(ss.broker().reset)
        self.addCleanup(second.shutdown)
        second.use(screens=lambda: fakes.spots("1920x1080", "1920x1080"), main_rect=lambda: QRect(100, 100, 1120, 700),
                   probes=fakes.FakeProbes(), native=fakes.FakeNative())
        second.set_enabled(True)
        settle(4)
        return harness, boards

    def answer(self, harness: AppHarness, frames: tuple, *, key: int, say: str = "Here it is, sir.") -> None:
        c = harness.c
        desk = c.look.desk
        c._you("Show me the numbers")
        desk.on_answer_started(key, "Show me the numbers")
        c._jarvis(say)
        from briefing_reader.board import ask_frames

        checked = tuple(ask_frames.check_frame(frame) for frame in frames)
        desk.on_ask_outcome(SimpleNamespace(ok=True, say=say, cards=(), research=None, calendar_read=None, mode="",
                                            topic="Numbers", frames=checked), key)
        settle(4)

    def test_the_app_pushes_the_look_the_counts_and_the_board(self) -> None:
        harness, _boards = self.app()
        c = harness.c
        second = c.look.second
        reading = c.window.reading
        self.answer(harness, (table(), maths()), key=701)
        state = second.state
        self.assertEqual([tile["kind"] for tile in state.tiles], ["table", "maths"])
        self.assertEqual(state.frames, 2)
        self.assertTrue(state.updated)
        reading.set_pending(2, 3)                                                  # two cards wait
        settle()
        self.assertEqual(second.state.needs, 2)
        self.assertEqual(second.state.look[0], orb_mod.NEEDS)
        self.assertEqual(second.state.word, reading.state_word())
        self.assertEqual(second.window.ring.look(), (orb_mod.NEEDS, False, False))
        texts = texts_of(drawn(second.window))
        self.assertIn("2 need your OK", texts)
        self.assertIn("2 frames on the board", texts)
        self.assertIn("x = 1 or x = 3", texts)                                    # Ask's typed frames: private tiles
        self.assertIn("2,900", texts)
        self.assertIn("3,400", texts)

    def test_a_changed_frame_shows_its_new_headline(self) -> None:
        harness, _boards = self.app()
        second = harness.c.look.second
        self.answer(harness, (table(),), key=702)
        rows = [["Travel", 1200, 1200, 0], ["Software", 2900, 3600, "+700"], ["Events", 2100, 2100, 0],
                ["Hardware", 800, 800, 0]]
        self.answer(harness, (table(rows=rows, change="+700 since Friday"),), key=703)
        (tile,) = second.state.tiles
        self.assertTrue(tile["changed"])
        texts = texts_of(drawn(second.window))
        self.assertIn("3,600", texts)
        self.assertNotIn("3,400", texts)

    def test_open_big_s_backdrop_and_the_lit_frame_never_reach_it(self) -> None:
        harness, boards = self.app()
        c = harness.c
        second = c.look.second
        self.answer(harness, (table(), maths()), key=704)
        board = boards[0]
        calls: list = []
        real = c.look.on_board_changed
        c.look.on_board_changed = lambda feed: (calls.append(feed), real(feed))[1]
        desk = c.look.desk
        frame_id = desk.feed.frame_ids()[0]
        desk._on_open(frame_id)                                                    # open big: a backdrop is sent
        settle(4)
        c.window.reading.set_frame_lit(True)                                       # the lit frame halves the rings' glow
        settle()
        backdrops = board.of_type("backdrop")
        self.assertTrue(backdrops)
        sources = {message["src"] for message in backdrops}
        self.assertFalse(sources & set(second.state.pictures.values()))           # the backdrop never reached it
        self.assertFalse(any(tile.get("type") != "frame" for tile in second.state.tiles))
        self.assertTrue(all(feed is desk.feed for feed in calls))                  # only the board's feed comes here
        self.assertEqual(second.state.pictures, {})                                # (no picture frame on this board)
        self.assertEqual(second.window.ring.glow_share(), 1.0)                     # its own OLED rules, never the desk's

    def test_with_the_board_off_the_wall_shows_only_the_counts_row(self) -> None:
        rig = Rig(self, board=False)
        rig.window.set_state(fakes.demo_state(board_on=False))
        painter = render(rig.window)
        texts = painter.texts
        self.assertFalse(any(text.upper().startswith("BOARD") for text in texts))
        self.assertNotIn("Re: Budget numbers", texts)                             # no tiles
        self.assertNotIn("9 frames on the board", texts)
        placed = {text: (x, y) for x, y, text in painter.placed}
        self.assertAlmostEqual(placed["1 needs your OK"][0], 640 + 28, 3)          # in the wall, its first cell
        self.assertAlmostEqual(placed["Briefing ready"][0], 1048 + 28, 3)
        self.assertLess(placed["1 needs your OK"][1], 200)
        self.assertEqual(sum(1 for text in texts if text == "1 needs your OK"), 1)  # once: not in the column too

    def test_a_picture_over_4_mp_is_never_decoded(self) -> None:
        rig = Rig(self)
        window = rig.window
        small, big = fakes.tile_picture_src(960, 540), fakes.tile_picture_src(2100, 2000)
        window.set_state(fakes.demo_state(pictures={"f8": small}))
        self.assertIsNotNone(window._picture("f8"))                                 # noqa: SLF001
        window.set_state(fakes.demo_state(pictures={"f8": big}))
        self.assertIsNone(window._picture("f8"))                                    # noqa: SLF001
        render(window)                                                               # and the tile still paints

    def test_the_next_event(self) -> None:
        events = fakes.demo_events() + [
            CalendarEvent("Declined thing", start=fakes.at(11), end=fakes.at(12), declined=True),
            CalendarEvent("Tomorrow's", start=fakes.NOW + timedelta(days=1), end=fakes.NOW + timedelta(days=1, hours=1))]
        upcoming = ss.upcoming_events(events, fakes.NOW)
        self.assertEqual([event.title for event in upcoming],
                         ["AI-Trader check-in", "Study group", "Project Development check-in"])
        first = ss.pick_next(upcoming, fakes.NOW)
        self.assertEqual(ss.next_line(first, fakes.NOW), "In 19 minutes · video call")
        self.assertEqual(ss.next_line(upcoming[1], fakes.NOW), "In 3 h 19 min · Library, room 2")
        self.assertEqual(ss.pick_next(upcoming, fakes.at(10, 5)).title, "Study group")   # its own clock moves on
        self.assertEqual(ss.in_words(fakes.at(10, 41), fakes.NOW), "In 1 hour")
        self.assertEqual(ss.in_words(fakes.at(9, 42), fakes.NOW), "In 1 minute")
        self.assertNotIn("abc", repr(first))                                        # never its place or title in a log
        self.assertNotIn("AI-Trader", repr(first))

    def test_the_app_pushes_the_calendar_s_events(self) -> None:
        harness, _boards = self.app()
        c = harness.c
        c._agenda_events = fakes.demo_events()
        c._agenda_range = (fakes.NOW - timedelta(days=1), fakes.NOW + timedelta(days=2))
        harness.clock.set(fakes.NOW)
        c._render_agenda()
        self.assertEqual([event.title for event in c.look.second.state.events][:1], ["AI-Trader check-in"])

    def test_logs_hold_no_words(self) -> None:
        with self.assertLogs("briefing_reader.second_screen", "DEBUG") as logs:
            rig = Rig(self)
            rig.feed = demo_feed()
            rig.c.on_board(rig.feed)
            rig.probes.flags["locked"] = True
            rig.c.sync()
        joined = "\n".join(logs.output)
        for words in ("Dana", "Budget", "AI-Trader", "dana@", "Northwind", "City Hack"):
            self.assertNotIn(words, joined)


if __name__ == "__main__":
    unittest.main()
