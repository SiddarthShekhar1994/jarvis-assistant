"""Canvas part 5B: the review's and the first-use walk's fixes, in the app (offscreen; the board is tests.board_fakes'
stand-in - tests/board_probe.py's "canvas_5b" case checks the real page's side of them).

What is checked, by the brief's numbers: (1) the offer's tile on the canvas - whole at 1120 x 700, told to the page as
"side", out of open big's way; (2) the corner's clock on its own minute; (3) Settings > Talk's Microphone row without
[meetings], the note's button opening it in view and focused, "(silent)" marks; (5) the ask line's words; (6) Today
never twice; (7, 8) the fallback's window and keyboard; (11) the wall's to-dos; (12) the page's units; (13) the
proposed rect's monitor; (14) the Now group up late; (15) the badge's dpr, "can't hear you" cleared, the timers'
rest, the restarts forgiven; (16) Esc and full screen; (18) "Hearing you"; (20) Decided today; (21) the group's source
count; (23) a stale offer sentence taken from the voice; (24) the calendar card's calendar; (25) the toast above the
orb.
"""

from __future__ import annotations

import ctypes
import json
import time
import unittest
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import (actions, approvals_view, canvas_ui, canvas_view, hud, meetings, meetings_view, offer_view,
                             orb as orb_mod, productivity_ui, second_screen, states_view, talk_ui, ui, wake_ui,
                             window_chrome as wc)
from briefing_reader.board import frames as board_frames
from briefing_reader.board import protocol, view
from briefing_reader.meeting_audio import DeviceInfo
from tests import test_canvas, test_talk_ui, test_wake_ui
from tests.ui_fakes import fonts, settle

_app: QApplication | None = None

STEAM = "Microphone (Steam Streaming Microphone)"
DESK = "Microphone (USB Desk Microphone)"
TALK = '\n[talk]\nenabled = true\nkey = "ctrl+alt+t"\n'
PLAN_OFFER = offer_view.OfferInfo(1, "plan", "offer:1", offer_view.PLAN_KICKER, offer_view.PLAN_TITLE, "Plan my day",
                                  (offer_view.PLAN_META,), "Shall I plan your day, sir?")


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas_fixes", "-platform", "offscreen"])
    fonts()


class Devices:
    """The talk's device library as a stand-in: listed, never opened."""

    def __init__(self, names=(STEAM, DESK)) -> None:
        self.ins = [DeviceInfo(str(index), name, index == 0) for index, name in enumerate(names)]

    def inputs(self):
        return list(self.ins)

    def default_input(self):
        return self.ins[0] if self.ins else None

    def close(self) -> None:
        pass


class FakeWake:
    def __init__(self, state="listening", mic="") -> None:
        self.state, self.mic = state, mic

    def hearing(self):
        return self.state, self.mic


class Fixes(test_canvas.CanvasApp):
    def talk_app(self, size=(1280, 800)):
        self.make(extra=TALK, size=size)
        talk = self.c.talk
        self.assertIsNotNone(talk)
        talk._backend_loader = lambda: (Devices(), "")       # never the real device library
        return talk


# ---- (1) the offer's tile -------------------------------------------------------------------------------------------

class SideTileTests(Fixes):
    def test_its_buttons_are_whole_at_1120_and_the_page_is_told_its_box(self) -> None:
        self.make(size=(1120, 700))
        self.started()
        self.reading.set_offers([PLAN_OFFER])
        settle(4)
        tile = self.reading.offer_tile
        self.assertTrue(tile.isVisible())
        not_now = tile.not_now_button
        self.assertGreaterEqual(not_now.width(), not_now.sizeHint().width())          # "T NO" before
        self.assertGreaterEqual(tile.width(), tile.fit_width())
        self.assertLessEqual(not_now.geometry().right(), tile.commit_button.geometry().left())
        self.canvas().sync()
        side = self.last("canvas")["side"]
        self.assertEqual(side, [tile.width(), tile.height()])
        page = self.reading.canvas_page()
        self.assertEqual(page.side_holder.geometry().topLeft(), QPoint(canvas_view.EDGE, canvas_view.SIDE_TOP))
        self.reading.set_offers([])
        settle(4)
        self.canvas().sync()
        self.assertEqual(self.last("canvas")["side"], [0, 0])

    def test_it_steps_aside_while_a_frame_is_open_big(self) -> None:
        self.make()
        self.started()
        self.reading.set_offers([PLAN_OFFER])
        settle(4)
        tile = self.reading.offer_tile
        self.assertTrue(tile.isVisible())
        self.reading.set_board_big(True)
        settle()
        self.assertFalse(tile.isVisible())                                # never over the big frame
        self.assertNotEqual(self.reading.canvas_page().side_box(), (0, 0))    # (its room is kept)
        self.reading.set_board_big(False)
        settle()
        self.assertTrue(tile.isVisible())

    def test_the_side_box_is_checked(self) -> None:
        good = dict(test_canvas.CANVAS, side=[300, 180])
        self.assertEqual(json.loads(protocol.check_message(good).text)["side"], [300, 180])
        self.assertNotIn("side", json.loads(protocol.check_message(test_canvas.CANVAS).text))   # only when given
        for bad in ([300], [300, 180, 1], [-1, 0], [protocol.SIDE_MAX + 1, 0], [True, 0], [1.5, 2], "300x180"):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_message(dict(test_canvas.CANVAS, side=bad))


# ---- (2) the clock --------------------------------------------------------------------------------------------------

class ClockTests(Fixes):
    def test_the_corner_clock_moves_on_while_the_native_header_is_hidden(self) -> None:
        harness = self.make()
        self.started()
        self.assertFalse(self.reading.header.isVisible())
        self.canvas().sync()
        before = self.last("canvas")["clock"]
        harness.clock.advance(3)
        self.canvas()._minute.timeout.emit()                              # the canvas's own minute
        self.assertNotEqual(self.last("canvas")["clock"], before)
        self.assertTrue(self.canvas()._minute.isActive())                 # armed again for the next one


# ---- (3) the microphone ---------------------------------------------------------------------------------------------

class MicrophoneTests(Fixes):
    def test_settings_talk_has_the_microphone_row_without_meetings(self) -> None:
        talk = self.talk_app()
        self.assertIsNone(getattr(self.c, "meetings", None))
        self.assertIn(talk_ui.SETTINGS_KEY, self.reading.settings_page.group_buttons)
        row = talk.mic_row
        self.assertIsInstance(row, meetings_view.DeviceRow)
        self.assertEqual(row.note_label.text(), meetings_view.MIC_NOTE)
        self.assertIn("Hey Jarvis", meetings_view.MIC_NOTE)
        self.reading.settings_page.select_group(talk_ui.SETTINGS_KEY)
        settle()
        self.assertEqual(row.value_label.text(), f"Follows the Windows default ({STEAM})")
        self.assertEqual(row.menu_items(), [meetings_view.FOLLOW_DEFAULT, STEAM, DESK])
        row.choose(DESK)                                                # the owner's pick, never Jarvis's
        saved = meetings.DevicePrefs(meetings.meetings_root(self.c.config)).microphone
        self.assertEqual(saved, DESK)
        self.assertEqual(talk._picked(), DESK)
        self.assertEqual(row.value_label.text(), DESK)

    def test_the_meetings_row_says_the_same_in_plain_words(self) -> None:
        panel = meetings_view.MeetingsSettingsPanel()
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.mic_row.note_label.text(), meetings_view.MIC_NOTE)
        self.assertTrue(panel.call_row.note_label.isHidden())

    def test_a_microphone_heard_silent_is_marked_and_still_picked_by_its_name(self) -> None:
        talk = self.talk_app()
        talk.silent_mics = lambda: [STEAM]
        talk.refresh_mic_row()
        row = talk.mic_row
        self.assertEqual(row.menu_items(), [meetings_view.FOLLOW_DEFAULT, STEAM + " (silent)", DESK])
        picked: list[str] = []
        row.picked.disconnect()
        row.picked.connect(picked.append)
        row.choose(STEAM + " (silent)")
        self.assertEqual(picked, [STEAM])

    def opens_in_view(self, size) -> None:
        talk = self.talk_app(size=size)
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        self.started()
        canvas = self.canvas()
        self.c.wake = FakeWake("cant_hear", STEAM)
        canvas.sync()
        self.assertIn("mic_settings", self.last("canvas")["buttons"])
        self.assertTrue(canvas.on_go("mic_settings"))
        settle(6)
        settings = self.reading.settings_page
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_SETTINGS)
        self.assertEqual(settings.current_group(), talk_ui.SETTINGS_KEY)
        button = talk.mic_row.change_button
        self.assertTrue(button.hasFocus())
        viewport = settings.scroll.viewport()
        box = QRect(button.mapTo(viewport, QPoint(0, 0)), button.size())
        self.assertTrue(viewport.rect().contains(box), f"{box} in {viewport.rect()}")

    def test_the_notes_button_opens_settings_talk_with_the_row_in_view_and_focused_at_1389(self) -> None:
        self.opens_in_view((1389, 868))

    def test_and_at_1120(self) -> None:
        self.opens_in_view((1120, 700))


# ---- (5) the ask line's words ---------------------------------------------------------------------------------------

class PlaceholderTests(Fixes):
    def test_it_names_the_real_talk_key_and_says_tap(self) -> None:
        self.talk_app()
        self.assertEqual(self.canvas().placeholder(), "Type, or tap Ctrl+Alt+T and talk")
        self.assertEqual(self.reading.command_bar.input.placeholderText(), "Type, or tap Ctrl+Alt+T and talk")

    def test_without_talking_type_to_ask(self) -> None:
        self.make()
        self.assertEqual(self.reading.command_bar.input.placeholderText(), "Type to ask")


# ---- (6) Today never twice; (14) the Now group up late ---------------------------------------------------------------

class NowTests(Fixes):
    def group_kinds(self) -> list[str]:
        desk = self.c.look.desk
        now = self.canvas().now
        group = desk.feed.group_for(now._answer)                          # noqa: SLF001
        return [desk.feed.frame_message(frame_id)["kind"] for frame_id in desk.feed.group_order(group)] if group else []

    def test_an_answer_showing_today_takes_the_groups_today_away_until_it_goes(self) -> None:
        self.make(resting=True, lines=test_canvas.CARDS)
        self.started()
        desk, now = self.c.look.desk, self.canvas().now
        self.assertIn("calendar", self.group_kinds())
        day = next(desk.feed.frame_message(frame_id) for frame_id in desk.feed.group_order(desk.feed.group_for(now._answer))
                   if desk.feed.frame_message(frame_id)["kind"] == "calendar")
        self.put()                                                        # an answer ...
        answer = desk._answers[desk._current]
        frame_id = desk.feed.frame_id(answer.key, "day", "", "")
        mine = dict(day, id=frame_id, title="Your day")                   # ... that shows today too
        mine.pop("group", None)
        desk._send(desk.feed.put(answer.key, "day", [mine], when="9:41 AM", rank=9.0))
        settle(2)
        now.refresh()
        settle(2)
        self.assertNotIn("calendar", self.group_kinds())                  # never twice
        desk._send(desk.feed.remove(frame_id))
        settle(2)
        now.refresh()
        settle(2)
        self.assertIn("calendar", self.group_kinds())                     # back once the answer's has gone

    def test_the_group_goes_up_as_soon_as_something_is_there(self) -> None:
        self.make(resting=True, lines=())
        self.c._agenda_events = None                                      # nothing read yet
        self.c._actions = []
        self.started()
        now = self.canvas().now
        now.refresh()
        self.assertEqual(now._answer, "")                                 # nothing there: no group
        self.c._agenda_events = []                                        # the agenda arrives a moment later
        now.refresh()
        settle(2)
        self.assertNotEqual(now._answer, "")
        self.assertEqual(self.group_kinds(), ["calendar"])


# ---- (7, 8) the fallback's window and keyboard; (16) Esc --------------------------------------------------------------

class WindowTests(Fixes):
    def activate(self) -> None:
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        settle(4)

    def test_after_the_fallback_the_window_leaves_full_screen_at_the_old_size(self) -> None:
        self.make()
        self.started()
        self.window.toggle_fullscreen()
        settle(2)
        self.assertTrue(self.window.windowState() & Qt.WindowState.WindowFullScreen)
        self.canvas().fall_back("crashed")
        settle(2)
        self.assertFalse(self.window.windowState() & (Qt.WindowState.WindowFullScreen | Qt.WindowState.WindowMaximized))

    def test_the_ask_line_keeps_the_keyboard_through_the_fallback(self) -> None:
        self.make()
        self.activate()
        self.started()
        bar = self.reading.command_bar
        bar.focus_input()
        settle()
        self.assertTrue(bar.input.hasFocus())
        self.canvas().fall_back("crashed")
        settle(4)
        self.assertTrue(bar.input.hasFocus())

    def test_esc_in_the_empty_ask_line_leaves_full_screen(self) -> None:
        self.make()
        self.activate()
        self.started()
        self.window.toggle_fullscreen()
        settle(2)
        self.assertTrue(self.window.windowState() & Qt.WindowState.WindowFullScreen)
        QTest.keyClick(self.reading.command_bar.input, Qt.Key.Key_Escape)
        settle(2)
        self.assertFalse(self.window.windowState() & Qt.WindowState.WindowFullScreen)
        self.assertFalse(self.window.leave_fullscreen())                  # not full screen: Esc does what it did


# ---- (12) the page's units; (15 L3) hearing once, the timers' rest -------------------------------------------------------

class SyncTests(Fixes):
    def test_a_name_outside_the_bmp_is_cut_by_the_pages_units_and_the_message_still_goes(self) -> None:
        self.make()
        self.started()
        canvas = self.canvas()
        self.c.wake = FakeWake("cant_hear", "\U0001F3A4" * 60)          # 120 units
        message = canvas.canvas_message()
        self.assertLessEqual(protocol.units(message["mic"]), protocol.MIC_CAP)
        protocol.check_message(message)                                   # never refused (the canvas froze)
        canvas.sync()
        self.assertEqual(canvas.counts["refused:canvas"], 0)
        self.assertEqual(self.last("canvas")["mic"], message["mic"])

    def test_hearing_is_read_once_a_sync(self) -> None:
        self.make()
        self.started()
        canvas = self.canvas()
        with mock.patch.object(canvas, "hearing", wraps=canvas.hearing) as hearing:
            canvas.canvas_message()
        self.assertEqual(hearing.call_count, 1)

    def test_the_sync_and_the_now_timer_rest_while_minimised(self) -> None:
        self.make(resting=True, lines=test_canvas.CARDS)
        self.started()
        canvas = self.canvas()
        self.assertTrue(canvas._sync_timer.isActive())
        self.assertTrue(canvas.now._timer.isActive())
        self.window.showMinimized()
        settle(2)
        self.assertFalse(canvas._sync_timer.isActive())
        self.assertFalse(canvas.now._timer.isActive())
        self.assertFalse(canvas._minute.isActive())
        self.window.showNormal()
        settle(2)
        self.assertTrue(canvas._sync_timer.isActive())
        self.assertTrue(canvas.now._timer.isActive())


# ---- (11) the wall's to-dos ------------------------------------------------------------------------------------------

class WallTodosTests(unittest.TestCase):
    def test_a_waiting_to_dos_title_never_reaches_the_wall(self) -> None:
        frame = {"type": "frame", "id": "f1", "kind": "todos", "title": "To-dos", "meta": "2 · 1 waits",
                 "body": {"rows": [{"text": "Print the boarding pass", "meta": "", "state": "waits"},
                                   {"text": "Buy milk", "meta": "Due today", "state": "open"}]}}
        rows = second_screen.private_frame(frame)["body"]["rows"]
        self.assertEqual(rows[0], {"text": "-", "meta": "", "state": "waits"})   # its state stays
        self.assertEqual(rows[1]["text"], "Buy milk")
        self.assertEqual(frame["body"]["rows"][0]["text"], "Print the boarding pass")   # (a copy)


# ---- (13) the proposed rect's monitor --------------------------------------------------------------------------------

class MonitorTests(unittest.TestCase):
    PRIMARY = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    SECOND = ((1920, 0, 3840, 1080), (1920, 0, 3840, 1040))

    class Api:
        def __init__(self, now, screens):
            self.now, self.screens = now, screens

        def style(self, _hwnd):
            return wc.CHROME_STYLE

        def is_zoomed(self, _hwnd):
            return True

        def monitor(self, _hwnd):
            return self.now                      # MonitorFromWindow: still the monitor it leaves

        def monitor_at(self, rect):
            left = rect[0] + (rect[2] - rect[0]) // 2
            return next(screen for screen in self.screens if screen[0][0] <= left < screen[0][2])

    def test_win_shift_right_while_maximised_keeps_the_whole_second_monitor(self) -> None:
        window = SimpleNamespace(devicePixelRatioF=lambda: 1.0, chrome_hit=lambda _p: wc.HTCLIENT,
                                 toggle_maximized=lambda: None, set_max_hover=lambda _h: None,
                                 call_soon=lambda fn: None)
        params = wc.NCCALCSIZE_PARAMS()
        r = params.rgrc[0]
        r.left, r.top, r.right, r.bottom = 1920 - 8, -8, 3840 + 8, 1040 + 8      # the second monitor, maximised
        chrome = wc.NativeChrome(window, self.Api(self.PRIMARY, (self.PRIMARY, self.SECOND)))
        chrome._wanted = True                                                    # noqa: SLF001 - canvas mode on
        msg = SimpleNamespace(hWnd=1, message=wc.WM_NCCALCSIZE, wParam=1, lParam=ctypes.addressof(params))
        chrome.handle(msg)
        self.assertEqual((r.left, r.top, r.right, r.bottom), (1920, 0, 3840, 1040))   # never 8 px wide


# ---- (15 L1) the badge's dpr; (L2) can't hear you cleared; (L4) restarts forgiven ------------------------------------

class ResilienceTests(unittest.TestCase):
    def test_the_badges_pixmaps_are_kept_per_device_pixel_ratio(self) -> None:
        layers = orb_mod.BadgeLayers()
        one = layers.get(("x",), 10, 10, 1.0, lambda _painter: None)
        two = layers.get(("x",), 10, 10, 2.0, lambda _painter: None)
        self.assertEqual(layers.builds, 2)
        self.assertEqual((one.devicePixelRatio(), two.devicePixelRatio()), (1.0, 2.0))

    def test_the_listener_hearing_on_that_microphone_clears_cant_hear_you(self) -> None:
        case = test_wake_ui.ControllerTests("test_nothing_listens_before_start_nor_while_off")
        case.setUp = lambda: None
        controller = test_wake_ui.ControllerTests.make(case)
        self.addCleanup(case.doCleanups)
        cleared: list[str] = []
        case.talk.clear_unheard = cleared.append
        controller.start()
        case.client.on_line({"ready": True, "load_ms": 250})
        case.client.on_line({"state": "silent_mic", "mic": STEAM, "default": True})
        self.assertEqual(controller.silent_mics(), [STEAM])
        self.assertEqual(cleared, [])
        case.client.on_line({"state": "listening", "mic": STEAM, "default": True})
        self.assertEqual(cleared, [STEAM])
        self.assertEqual(controller.silent_mics(), [])

    def test_the_talks_cant_hear_you_goes_with_its_microphone(self) -> None:
        case = test_talk_ui.TalkControllerTests("test_a_talk_listens_ends_by_quiet_and_goes_in_as_spoken_words")
        controller = test_talk_ui.TalkControllerTests.make(case)
        self.addCleanup(case.doCleanups)
        controller._unheard = ("Desk mic", "")
        self.assertFalse(controller.clear_unheard("Headset"))
        self.assertEqual(controller.unheard(), "Desk mic")
        panel = talk_ui.TalkSettingsPanel("pip")
        self.addCleanup(panel.deleteLater)
        controller.attach_panel(panel)
        controller._backend_loader = lambda: (Devices(("Headset",)), "")    # unplugged
        controller.refresh_mic_row()
        self.assertIsNone(controller.unheard())

    def test_a_renderer_stop_after_ten_minutes_ready_is_not_one_of_a_run(self) -> None:
        stub = SimpleNamespace(_phase=view._READY, _page=object(), _counts=Counter(), _restarts=view.MAX_RESTARTS,
                               _ready_since=time.monotonic() - view.RESTARTS_FORGIVEN_S - 1, _fall_back=mock.Mock(),
                               _ready_timer=mock.Mock(), _check_proxy=mock.Mock(), _reload=mock.Mock())
        with mock.patch.object(view.QTimer, "singleShot"):
            view.BoardView._on_renderer_gone(stub, SimpleNamespace(name="Crashed"))
        stub._fall_back.assert_not_called()
        self.assertEqual(stub._restarts, 1)
        stub = SimpleNamespace(_phase=view._READY, _page=object(), _counts=Counter(), _restarts=view.MAX_RESTARTS,
                               _ready_since=time.monotonic() - 5, _fall_back=mock.Mock(),
                               _ready_timer=mock.Mock(), _check_proxy=mock.Mock())
        view.BoardView._on_renderer_gone(stub, SimpleNamespace(name="Crashed"))
        stub._fall_back.assert_called_once_with(view.CRASHED)             # three in a row still fall back


# ---- (18) "Hearing you" ---------------------------------------------------------------------------------------------

class HearingYouTests(unittest.TestCase):
    def test_a_talk_hey_jarvis_started_reads_hearing_you_not_paused(self) -> None:
        case = test_wake_ui.ControllerTests("test_nothing_listens_before_start_nor_while_off")
        controller = test_wake_ui.ControllerTests.started(case)
        self.addCleanup(case.doCleanups)
        case.talk.wake_listening = lambda: True
        case.holds.append(wake_ui.HOLD_SPEAKING)                          # (the talk holds the listener)
        controller.refresh_holds()
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_HEARING, ""))
        self.assertIn(wake_ui.HEAR_HEARING, protocol.HEAR_STATES)
        case.talk.wake_listening = lambda: False
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_PAUSED, ""))


# ---- (17) the listening words; (19) Memory's box; (20) Decided today; (24) the calendar card --------------------------

class WordsTests(unittest.TestCase):
    def test_the_listening_line_fits_the_canvas_status_line_whole(self) -> None:
        text = talk_ui.LISTENING_TEXT.format(key="Ctrl+Alt+T")
        self.assertEqual(text, "Listening: tap Ctrl+Alt+T to end, Esc to cancel.")
        from PySide6.QtGui import QFontMetricsF
        from briefing_reader import holo

        room = canvas_view.BAR_WIDTH - 2 * 24
        self.assertLessEqual(QFontMetricsF(holo.font("body_sm")).horizontalAdvance(text), room)

    def test_memory_names_the_box_its_screen_has(self) -> None:
        self.assertIn("Find or ask anything", hud.MEMORY_EMPTY_TEXT)
        self.assertNotIn("in the bar", hud.MEMORY_EMPTY_TEXT)

    def test_decided_today_is_no_button_while_nothing_was_decided(self) -> None:
        toggle = approvals_view._DecidedToggle()
        self.addCleanup(toggle.deleteLater)
        toggle.set_count(0)
        self.assertFalse(toggle.isEnabled())
        self.assertEqual(toggle.focusPolicy(), Qt.FocusPolicy.NoFocus)
        toggle.set_count(1)
        self.assertTrue(toggle.isEnabled())
        toggle.resize(240, 34)
        closed = QImage(toggle.size(), QImage.Format.Format_ARGB32)
        closed.fill(0)
        toggle.render(closed)
        toggle.setChecked(True)
        opened = QImage(toggle.size(), QImage.Format.Format_ARGB32)
        opened.fill(0)
        toggle.render(opened)
        self.assertNotEqual(closed, opened)                               # a click shows where it was clicked

    def test_a_calendar_card_says_which_calendar_it_goes_to(self) -> None:
        card = actions.parse_action_line("Calendar: Dentist check-up | 2026-11-25 15:00-15:45 | | Bright Smile Dental |")
        view_ = actions.card_view(card, card.start.date() if card.start else None)
        self.assertTrue(view_.detail.endswith(actions.CALENDAR_TARGET), view_.detail)
        self.assertEqual(actions.CALENDAR_TARGET, "Personal calendar")


# ---- (21) the group's source count ----------------------------------------------------------------------------------

class SourcesTests(Fixes):
    def test_the_groups_count_is_never_fewer_than_its_research_frame_lists(self) -> None:
        self.make()
        self.started()
        desk = self.c.look.desk
        desk.on_answer_started(7, "Is the trail open?")
        report = SimpleNamespace(answer="Yes, sir.", sources=(SimpleNamespace(title="Parks", url="https://a.example/x",
                                                                               number=1),
                                                              SimpleNamespace(title="News", url="https://b.example/y",
                                                                              number=2)))
        desk.on_ask_outcome(SimpleNamespace(ok=True, say="Yes, sir.", cards=(), research=report, calendar_read=None,
                                            mode="", frames=()), 7)
        self.assertEqual(desk.answer_sources(7), 2)


# ---- (23) a stale offer sentence; (25) the toast --------------------------------------------------------------------

class OfferSpeechTests(unittest.TestCase):
    def test_a_sentence_dropped_as_stale_is_taken_from_the_voice_too(self) -> None:
        offer = SimpleNamespace(speech_state=productivity_ui.QUEUED, queued_at=None, id=1)
        unsaid: list = []
        stub = SimpleNamespace(_offers={1: offer}, offerUnsaid=SimpleNamespace(emit=unsaid.append),
                               _back_to_held=lambda item: setattr(item, "speech_state", productivity_ui.HELD))
        from datetime import datetime

        now = datetime(2026, 11, 23, 11, 30)
        offer.queued_at = now - productivity_ui.QUEUED_FOR - timedelta(seconds=1)
        productivity_ui.ProductivityController._requeue_dropped(stub, now)
        self.assertEqual(offer.speech_state, productivity_ui.HELD)
        self.assertEqual(unsaid, [offer])                                 # the voice's copy goes: never said twice


class ToastTests(Fixes):
    def test_the_toast_goes_above_the_canvas_orb(self) -> None:
        self.make(size=(1389, 868))
        self.started()
        self.window.showMaximized()
        settle(4)
        toast = self.c._toast()
        toast.post("Your AM briefing is ready, sir.", "View", "briefing")
        settle()
        orb = self.c._toast_avoid()
        self.assertIsNotNone(orb)
        self.assertFalse(toast.frameGeometry().intersects(orb), f"{toast.frameGeometry()} over {orb}")
        toast.dismiss()


if __name__ == "__main__":
    unittest.main()
