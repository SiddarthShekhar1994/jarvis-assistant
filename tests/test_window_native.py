"""The window brief: Jarvis's main window behaves as any Windows window in canvas mode (window_chrome) and the canvas says
when Jarvis can't hear the owner. Offscreen: no real window is touched and no Win32 call is made - NativeChrome is
driven with a stand-in for its Win32 calls and Windows' messages built in memory; the board is tests.board_fakes'
stand-in (the real page's cases are tests/board_probe.py "canvas_hear", run by test_board_lockdown).

What is checked: the styles asked for (an ordinary window's: caption, system menu, thick frame, minimise, maximise; no
popup) and back; the hit-test map for points (edges, corners, the page's top band as the caption but for its controls,
the maximise button as Windows' own, a band whose width is stale, a screen's header); the WM_NCCALCSIZE answer (the
whole window is client area; maximised, inside the work area) and WM_GETMINMAXINFO's (the monitor's work area); each
message NativeChrome answers and each it leaves to Qt; the system menu; the window's hit-test in the real app with the
stand-in page's report; the maximise button's hover reaching the page; nothing of it under the offscreen platform.
"Can't hear you": WakeController.hearing and TalkController.unheard; the canvas message's "hear", "deaf" and "mic"
checked and sent; the note's button only while it can work, opening Settings at Meetings; the microphone's name never
in a log.
"""

from __future__ import annotations

import ctypes
import json
import logging
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtWidgets import QApplication

from briefing_reader import talk_ui, ui, wake_ui, window_chrome as wc
from briefing_reader.board import protocol
from tests import test_canvas, test_talk_ui, test_wake_ui
from tests.test_talk import ROOM, VOICE, noise, tone
from tests.ui_fakes import settle

APP = QApplication.instance() or QApplication(["test_window_native", "-platform", "offscreen"])
STEAM = "Microphone (Steam Streaming Microphone)"
QT_FRAMELESS = wc.WS_POPUP | wc.WS_MINIMIZEBOX | 0x10000000 | 0x02000000 | 0x04000000   # + VISIBLE, CLIPCHILDREN, ...


# --------------------------------------------------------------------------
# The styles, the hit-test, Windows' answers (pure)
# --------------------------------------------------------------------------

class StyleTests(unittest.TestCase):
    def test_an_ordinary_windows_styles_and_never_a_popup(self) -> None:
        style = wc.chrome_style(QT_FRAMELESS)
        for bit in (wc.WS_CAPTION, wc.WS_SYSMENU, wc.WS_THICKFRAME, wc.WS_MINIMIZEBOX, wc.WS_MAXIMIZEBOX):
            self.assertEqual(style & bit, bit, hex(bit))
        self.assertEqual(style & wc.WS_POPUP, 0)
        self.assertEqual(style & 0x16000000, 0x16000000)                  # Qt's own bits (visible, clip) kept
        self.assertTrue(wc.has_chrome(style))
        self.assertFalse(wc.has_chrome(QT_FRAMELESS))

    def test_back_to_qts_own_style(self) -> None:
        style = wc.chrome_style(QT_FRAMELESS) | 0x01000000                # (maximised meanwhile: WS_MAXIMIZE kept)
        self.assertEqual(wc.plain_style(style, QT_FRAMELESS), QT_FRAMELESS | 0x01000000)

    def test_full_screens_style_is_not_the_chromes(self) -> None:
        self.assertFalse(wc.has_chrome(wc.WS_POPUP | wc.WS_SYSMENU | 0x10000000))

    def test_the_system_menu_follows_the_state(self) -> None:
        self.assertEqual(wc.menu_items(False), {wc.SC_RESTORE: False, wc.SC_MOVE: True, wc.SC_SIZE: True,
                                                wc.SC_MINIMIZE: True, wc.SC_MAXIMIZE: True, wc.SC_CLOSE: True})
        self.assertEqual(wc.menu_items(True), {wc.SC_RESTORE: True, wc.SC_MOVE: False, wc.SC_SIZE: False,
                                               wc.SC_MINIMIZE: True, wc.SC_MAXIMIZE: False, wc.SC_CLOSE: True})


def chrome(width=1268, bar=64, holes=((300, 16, 80, 28),), maximize=(1210, 16, 30, 28)) -> protocol.Request:
    return protocol.check_request("chrome", json.dumps({"w": width, "bar": bar, "holes": [list(h) for h in holes],
                                                        "max": list(maximize)}), ())


class HitTestTests(unittest.TestCase):
    SIZE = QSizeF(1280, 800)

    def band(self, **kwargs) -> wc.Band | None:
        return wc.band_from(chrome(**kwargs), QPointF(6, 4), 1268)

    def hit(self, x: float, y: float, *, edges: bool = True, band=None, header: bool = False) -> int:
        return wc.hit_test(QPointF(x, y), self.SIZE, edges=edges, band=band, header=header)

    def test_the_edges_and_corners_resize(self) -> None:
        band = self.band()
        cases = {(2, 400): wc.HTLEFT, (1278, 400): wc.HTRIGHT, (600, 1): wc.HTTOP, (600, 798): wc.HTBOTTOM,
                 (1, 1): wc.HTTOPLEFT, (10, 2): wc.HTTOPLEFT, (2, 12): wc.HTTOPLEFT, (1279, 0): wc.HTTOPRIGHT,
                 (1270, 3): wc.HTTOPRIGHT, (0, 799): wc.HTBOTTOMLEFT, (1279, 799): wc.HTBOTTOMRIGHT,
                 (1277, 790): wc.HTBOTTOMRIGHT}
        for (x, y), want in cases.items():
            self.assertEqual(self.hit(x, y, band=band), want, (x, y))

    def test_the_band_is_the_caption_but_for_the_pages_controls_and_the_maximise_button_is_windows_own(self) -> None:
        band = self.band()
        self.assertEqual(self.hit(60, 30, band=band), wc.HTCAPTION)          # the wordmark's row
        self.assertEqual(self.hit(700, 60, band=band), wc.HTCAPTION)
        self.assertEqual(self.hit(320, 30, band=band), wc.HTCLIENT)          # a button of the page's own
        self.assertEqual(self.hit(1225, 30, band=band), wc.HTMAXBUTTON)      # (6 + 1210 .. 6 + 1240)
        self.assertEqual(self.hit(700, 69, band=band), wc.HTCLIENT)          # under the band: the frames
        self.assertEqual(self.hit(700, 400, band=band), wc.HTCLIENT)

    def test_maximised_no_edges_and_the_band_reaches_the_screens_top(self) -> None:
        band = wc.band_from(chrome(width=1280), QPointF(0, 0), 1280)
        self.assertEqual(self.hit(2, 400, edges=False, band=band), wc.HTCLIENT)
        self.assertEqual(self.hit(1, 1, edges=False, band=band), wc.HTCAPTION)   # drag down to restore
        self.assertEqual(self.hit(1220, 20, edges=False, band=band), wc.HTMAXBUTTON)

    def test_a_band_the_page_measured_at_another_width_is_not_used(self) -> None:
        self.assertIsNone(wc.band_from(chrome(width=1100), QPointF(6, 4), 1268))
        self.assertIsNotNone(wc.band_from(chrome(width=1269), QPointF(6, 4), 1268))   # (a rounding apart)
        self.assertIsNone(wc.band_from(None, QPointF(6, 4), 1268))
        self.assertEqual(self.hit(60, 30, band=None), wc.HTCLIENT)           # the page's own (its wordmark drags)

    def test_no_maximise_button_and_a_screens_header(self) -> None:
        band = self.band(maximize=())
        self.assertIsNone(band.maximize)
        self.assertEqual(self.hit(1225, 30, band=band), wc.HTCAPTION)
        self.assertEqual(self.hit(400, 30, header=True), wc.HTCAPTION)
        self.assertEqual(self.hit(400, 30), wc.HTCLIENT)

    def test_the_page_coordinates_are_placed_at_its_origin(self) -> None:
        band = self.band()
        self.assertEqual(band.rect, QRectF(6, 4, 1268, 64))
        self.assertEqual(band.holes, (QRectF(306, 20, 80, 28),))
        self.assertEqual(band.maximize, QRectF(1216, 20, 30, 28))


class AnswerTests(unittest.TestCase):
    def test_nccalcsize_the_whole_window_is_client_area(self) -> None:
        params = wc.NCCALCSIZE_PARAMS()
        params.rgrc[0].left, params.rgrc[0].top, params.rgrc[0].right, params.rgrc[0].bottom = 100, 50, 1380, 850
        self.assertEqual(wc.answer_nccalcsize(True, ctypes.addressof(params), None), 0)
        rect = params.rgrc[0]
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (100, 50, 1380, 850))   # no frame taken off
        plain = wc.RECT(100, 50, 1380, 850)
        self.assertEqual(wc.answer_nccalcsize(False, ctypes.addressof(plain), None), 0)
        self.assertEqual((plain.left, plain.top, plain.right, plain.bottom), (100, 50, 1380, 850))

    def test_nccalcsize_maximised_stays_inside_the_work_area(self) -> None:
        params = wc.NCCALCSIZE_PARAMS()
        params.rgrc[0].left, params.rgrc[0].top, params.rgrc[0].right, params.rgrc[0].bottom = -8, -8, 1928, 1040
        wc.answer_nccalcsize(True, ctypes.addressof(params), (0, 0, 1920, 1032))
        rect = params.rgrc[0]
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (0, 0, 1920, 1032))   # never over the taskbar

    def test_minmax_is_the_work_area_of_the_windows_own_monitor(self) -> None:
        info = wc.MINMAXINFO()
        wc.answer_minmax(ctypes.addressof(info), (0, 0, 1920, 1080), (0, 0, 1920, 1032))
        self.assertEqual((info.ptMaxPosition.x, info.ptMaxPosition.y, info.ptMaxSize.x, info.ptMaxSize.y),
                         (0, 0, 1920, 1032))
        wc.answer_minmax(ctypes.addressof(info), (1920, 0, 4480, 1440), (1920, 48, 4480, 1440))   # a taskbar on top
        self.assertEqual((info.ptMaxPosition.x, info.ptMaxPosition.y, info.ptMaxSize.x, info.ptMaxSize.y),
                         (0, 48, 2560, 1392))

    def test_a_messages_point_is_signed(self) -> None:
        self.assertEqual(wc.point_of((200 << 16) | 100), (100, 200))
        self.assertEqual(wc.point_of(((-30 & 0xFFFF) << 16) | (-1910 & 0xFFFF)), (-1910, -30))   # a monitor to the left


# --------------------------------------------------------------------------
# NativeChrome with a stand-in for Win32 and messages built in memory
# --------------------------------------------------------------------------

class FakeApi:
    def __init__(self) -> None:
        self.styles = {7: QT_FRAMELESS}
        self.set: list[tuple[int, int]] = []
        self.zoomed = False
        self.screens = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))
        self.tracked = 0
        self.menus: list[tuple[int, int, bool]] = []
        self.pick = wc.SC_MAXIMIZE
        self.posted: list[int] = []

    def style(self, hwnd):
        return self.styles[hwnd]

    def set_style(self, hwnd, style):
        self.styles[hwnd] = style
        self.set.append((hwnd, style))

    def is_zoomed(self, hwnd):
        return self.zoomed

    def monitor(self, hwnd):
        return self.screens

    def screen_to_client(self, hwnd, x, y):
        return x - 100, y - 50

    def client_origin(self, hwnd):
        return 100, 50

    def track_leave(self, hwnd):
        self.tracked += 1

    def system_menu(self, hwnd, x, y, zoomed):
        self.menus.append((x, y, zoomed))
        return self.pick

    def post_syscommand(self, hwnd, command):
        self.posted.append(command)


class FakeWindow:
    def __init__(self) -> None:
        self.ratio = 1.5
        self.points: list[tuple[float, float]] = []
        self.answer = wc.HTCAPTION
        self.toggled = 0
        self.hovers: list[bool] = []
        self.later: list = []

    def devicePixelRatioF(self) -> float:  # noqa: N802 - Qt's name
        return self.ratio

    def chrome_hit(self, pos):
        self.points.append((pos.x(), pos.y()))
        return self.answer

    def toggle_maximized(self) -> None:
        self.toggled += 1

    def set_max_hover(self, hover: bool) -> None:
        self.hovers.append(hover)

    def call_soon(self, fn) -> None:
        self.later.append(fn)

    def run_later(self) -> None:
        while self.later:
            self.later.pop(0)()


def msg(kind: int, wparam: int = 0, lparam: int = 0, hwnd: int = 7) -> wintypes.MSG:
    message = wintypes.MSG()
    message.hWnd, message.message, message.wParam, message.lParam = hwnd, kind, wparam, lparam
    return message


class NativeChromeTests(unittest.TestCase):
    def make(self, wanted: bool = True) -> tuple[wc.NativeChrome, FakeApi, FakeWindow]:
        api, window = FakeApi(), FakeWindow()
        native = wc.NativeChrome(window, api)
        native.sync(7, wanted)
        return native, api, window

    def test_on_and_off_the_styles_once_each(self) -> None:
        native, api, _window = self.make()
        self.assertEqual(api.set, [(7, wc.chrome_style(QT_FRAMELESS))])
        self.assertFalse(native.sync(7, True))                            # already so: nothing asked again
        api.styles[7] = QT_FRAMELESS                                       # Qt set its own style again (a flag change)
        self.assertTrue(native.sync(7, True))
        self.assertTrue(native.sync(7, False))
        self.assertEqual(api.styles[7], QT_FRAMELESS)                     # back to Qt's own
        self.assertFalse(native.sync(7, False))

    def test_full_screen_keeps_qts_style_and_only_the_wish(self) -> None:
        native, api, _window = self.make(wanted=False)
        api.styles[7] = wc.WS_POPUP | 0x10000000                          # Qt's full-screen style
        self.assertFalse(native.sync(7, True, apply=False))
        self.assertEqual(api.set, [])
        self.assertTrue(native.wanted())
        self.assertFalse(native.active(7))
        self.assertIsNone(native.handle(msg(wc.WM_NCHITTEST)))            # Qt's own: the page's buttons work
        self.assertIsNone(native.handle(msg(wc.WM_NCCALCSIZE, 1, 0)))

    def test_not_wanted_nothing_is_answered(self) -> None:
        native, api, _window = self.make(wanted=False)
        for kind in (wc.WM_NCHITTEST, wc.WM_NCCALCSIZE, wc.WM_GETMINMAXINFO, wc.WM_NCLBUTTONDOWN, wc.WM_SYSKEYDOWN):
            self.assertIsNone(native.handle(msg(kind, wc.HTMAXBUTTON)))
        self.assertEqual(api.set, [])

    def test_nchittest_asks_the_window_in_its_own_px(self) -> None:
        native, _api, window = self.make()
        window.answer = wc.HTMAXBUTTON
        self.assertEqual(native.handle(msg(wc.WM_NCHITTEST, 0, (80 << 16) | 400)), (True, wc.HTMAXBUTTON))
        self.assertEqual(window.points, [(200.0, 20.0)])                  # (400 - 100, 80 - 50) / 1.5

    def test_nccalcsize_and_minmax(self) -> None:
        native, api, _window = self.make()
        params = wc.NCCALCSIZE_PARAMS()
        params.rgrc[0].right, params.rgrc[0].bottom = 1280, 800
        self.assertEqual(native.handle(msg(wc.WM_NCCALCSIZE, 1, ctypes.addressof(params))), (True, 0))
        self.assertEqual((params.rgrc[0].right, params.rgrc[0].bottom), (1280, 800))
        api.zoomed = True
        params.rgrc[0].left, params.rgrc[0].top, params.rgrc[0].right, params.rgrc[0].bottom = -8, -8, 1928, 1088
        native.handle(msg(wc.WM_NCCALCSIZE, 1, ctypes.addressof(params)))
        self.assertEqual((params.rgrc[0].left, params.rgrc[0].top, params.rgrc[0].right, params.rgrc[0].bottom),
                         (0, 0, 1920, 1040))
        info = wc.MINMAXINFO()
        self.assertIsNone(native.handle(msg(wc.WM_GETMINMAXINFO, 0, ctypes.addressof(info))))   # Qt adds its sizes
        self.assertEqual((info.ptMaxSize.x, info.ptMaxSize.y), (1920, 1040))

    def test_the_maximise_button_is_the_apps_own_press(self) -> None:
        native, _api, window = self.make()
        self.assertEqual(native.handle(msg(wc.WM_NCLBUTTONDOWN, wc.HTMAXBUTTON)), (True, 0))   # never Windows' own
        self.assertEqual(native.handle(msg(wc.WM_NCLBUTTONUP, wc.HTMAXBUTTON)), (True, 0))
        window.run_later()
        self.assertEqual(window.toggled, 1)
        self.assertEqual(native.handle(msg(wc.WM_NCLBUTTONUP, wc.HTMAXBUTTON)), (True, 0))     # no press first
        window.run_later()
        self.assertEqual(window.toggled, 1)
        self.assertIsNone(native.handle(msg(wc.WM_NCLBUTTONDOWN, wc.HTCAPTION)))   # the caption: Windows' move
        self.assertIsNone(native.handle(msg(wc.WM_NCLBUTTONDBLCLK, wc.HTCAPTION)))  # ... and its maximise

    def test_the_maximise_buttons_hover(self) -> None:
        native, api, window = self.make()
        native.handle(msg(wc.WM_NCMOUSEMOVE, wc.HTMAXBUTTON))
        native.handle(msg(wc.WM_NCMOUSEMOVE, wc.HTMAXBUTTON))
        self.assertEqual((window.hovers, api.tracked), ([True], 1))
        native.handle(msg(wc.WM_NCMOUSELEAVE))
        self.assertEqual(window.hovers, [True, False])
        native.handle(msg(wc.WM_NCMOUSEMOVE, wc.HTMAXBUTTON))
        native.handle(msg(wc.WM_NCMOUSEMOVE, wc.HTCAPTION))
        native.handle(msg(wc.WM_NCMOUSEMOVE, wc.HTMAXBUTTON))
        native.handle(msg(wc.WM_MOUSEMOVE))
        self.assertEqual(window.hovers, [True, False, True, False, True, False])
        self.assertFalse(native.hovered())

    def test_alt_space_and_the_captions_right_click_open_the_system_menu(self) -> None:
        native, api, window = self.make()
        self.assertEqual(native.handle(msg(wc.WM_SYSKEYDOWN, wc.VK_SPACE)), (True, 0))
        window.run_later()
        self.assertEqual(api.menus, [(100, 50, False)])                    # at the window's top left
        self.assertEqual(api.posted, [wc.SC_MAXIMIZE])                     # the pick goes to Windows as its own
        api.zoomed, api.pick = True, 0
        self.assertEqual(native.handle(msg(wc.WM_NCRBUTTONUP, wc.HTCAPTION, (300 << 16) | 640)), (True, 0))
        window.run_later()
        self.assertEqual(api.menus[-1], (640, 300, True))
        self.assertEqual(api.posted, [wc.SC_MAXIMIZE])                     # nothing picked: nothing posted
        self.assertIsNone(native.handle(msg(wc.WM_NCRBUTTONUP, wc.HTCLIENT)))
        self.assertIsNone(native.handle(msg(wc.WM_SYSKEYDOWN, 0x73)))      # Alt+F4: Windows' own close

    def test_alt_alone_never_leaves_an_invisible_menu_mode(self) -> None:
        native, _api, _window = self.make()
        self.assertEqual(native.handle(msg(wc.WM_SYSCOMMAND, wc.SC_KEYMENU, 0)), (True, 0))
        self.assertIsNone(native.handle(msg(wc.WM_SYSCOMMAND, wc.SC_KEYMENU, ord(" "))))
        self.assertIsNone(native.handle(msg(wc.WM_SYSCOMMAND, wc.SC_MAXIMIZE)))   # Win+Up and the rest: Windows'

    def test_other_messages_are_qts(self) -> None:
        native, _api, _window = self.make()
        self.assertIsNone(native.handle(msg(0x0005)))                      # WM_SIZE
        self.assertIsNone(native.handle(msg(0x0100, 0x20)))                # WM_KEYDOWN Space: the ask line's


# --------------------------------------------------------------------------
# The real window (offscreen) with the stand-in page
# --------------------------------------------------------------------------

class WindowAppTests(test_canvas.CanvasApp):
    def origin(self) -> QPointF:
        board = self.reading.board_view()
        return QPointF(board.mapTo(self.window, QPoint(0, 0)))

    def report(self, board, **fields) -> None:
        payload = {"w": self.reading.board_view().width(), "bar": 64, "holes": [[300, 16, 80, 28]],
                   "max": [self.reading.board_view().width() - 70, 16, 30, 28]}
        payload.update(fields)
        self.assertTrue(board.request("chrome", **payload))

    def test_the_pages_report_reaches_the_windows_hit_test(self) -> None:
        self.make()
        board = self.started()
        o = self.origin()
        hit = self.window.chrome_hit
        self.assertEqual(hit(o + QPointF(40, 30)), wc.HTCLIENT)            # no report yet: the page's own
        self.report(board)
        width = self.reading.board_view().width()
        self.assertEqual(hit(o + QPointF(40, 30)), wc.HTCAPTION)
        self.assertEqual(hit(o + QPointF(320, 30)), wc.HTCLIENT)
        self.assertEqual(hit(o + QPointF(width - 60, 30)), wc.HTMAXBUTTON)
        self.assertEqual(hit(o + QPointF(600, 300)), wc.HTCLIENT)
        self.assertEqual(hit(QPointF(2, 300)), wc.HTLEFT)                  # the frame's margin
        self.assertEqual(hit(QPointF(self.window.width() - 1, self.window.height() - 1)), wc.HTBOTTOMRIGHT)
        self.assertEqual(self.canvas().counts["chrome:report"], 1)

    def test_a_resize_the_page_has_not_reported_leaves_the_band_to_the_page(self) -> None:
        self.make()
        board = self.started()
        self.report(board)
        self.window.resize(1100, 760)
        settle(4)
        self.assertEqual(self.window.chrome_hit(self.origin() + QPointF(40, 30)), wc.HTCLIENT)
        self.report(board)
        self.assertEqual(self.window.chrome_hit(self.origin() + QPointF(40, 30)), wc.HTCAPTION)

    def test_maximised_no_edges(self) -> None:
        self.make()
        board = self.started()
        self.window.setWindowState(Qt.WindowState.WindowMaximized)
        settle(4)
        self.report(board)
        self.assertEqual(self.origin(), QPointF(0, 0))                     # no margins
        self.assertEqual(self.window.chrome_hit(QPointF(2, 300)), wc.HTCLIENT)
        self.assertEqual(self.window.chrome_hit(QPointF(2, 30)), wc.HTCAPTION)

    def test_another_screen_its_headers_empty_part_is_the_caption(self) -> None:
        self.make()
        board = self.started()
        self.report(board)
        self.reading.set_tab(ui.SCREEN_SETTINGS)
        settle(4)
        header = self.reading.header
        self.assertTrue(header.isVisible())
        found = {}
        for x in range(4, header.width() - 4, 6):
            point = QPointF(header.mapTo(self.window, QPoint(x, header.height() // 2)))
            child = self.window.childAt(point.toPoint())
            found.setdefault("empty" if child is header else "control", point)
        self.assertEqual(self.window.chrome_hit(found["empty"]), wc.HTCAPTION)
        self.assertEqual(self.window.chrome_hit(found["control"]), wc.HTCLIENT)

    def test_the_maximise_buttons_hover_reaches_the_page(self) -> None:
        self.make()
        board = self.started()
        self.window.set_max_hover(True)
        self.assertEqual(board.of_type("chrome")[-1], {"type": "chrome", "hover": True})
        self.window.set_max_hover(False)
        self.assertEqual(board.of_type("chrome")[-1], {"type": "chrome", "hover": False})

    def test_offscreen_no_win32_call_is_ever_made(self) -> None:
        with mock.patch.object(wc, "Win32", side_effect=AssertionError("a Win32 call offscreen")):
            self.make()
            self.started()
            self.window.show_prompt_view()
            self.window.show_reading_view()
            self.window.setWindowState(Qt.WindowState.WindowMaximized)
            settle(4)
        self.assertFalse(self.window.native_chrome().wanted())            # (never asked: not native here)

    def test_without_the_canvas_the_window_is_as_before(self) -> None:
        self.make(extra="\n[look]\nboard = false\n")
        self.assertFalse(self.window.canvas_window())
        self.assertEqual(self.window.chrome_hit(QPointF(60, 30)), wc.HTCLIENT)


# --------------------------------------------------------------------------
# "Can't hear you"
# --------------------------------------------------------------------------

class HearingMessageTests(unittest.TestCase):
    def test_the_three_fields_are_checked(self) -> None:
        base = dict(test_canvas.CANVAS)
        sent = json.loads(protocol.check_message(dict(base, hear="cant_hear", deaf=True, mic=STEAM)).text)
        self.assertEqual((sent["hear"], sent["deaf"], sent["mic"]), ("cant_hear", True, STEAM))
        sent = json.loads(protocol.check_message(base).text)
        self.assertEqual((sent["hear"], sent["deaf"], sent["mic"]), ("", False, ""))
        for bad in (dict(base, hear="deaf"), dict(base, hear=None), dict(base, deaf=1), dict(base, deaf="yes"),
                    dict(base, mic="x" * (protocol.MIC_CAP + 1)), dict(base, mic=3)):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_message(bad)
        self.assertIn("mic_settings", protocol.CANVAS_BUTTONS)

    def test_the_chrome_message_and_request_are_checked(self) -> None:
        self.assertEqual(json.loads(protocol.check_message({"type": "chrome", "hover": True}).text),
                         {"type": "chrome", "hover": True})
        for bad in ({"type": "chrome"}, {"type": "chrome", "hover": 1}, {"type": "chrome", "hover": True, "x": 1}):
            with self.assertRaises(protocol.Refused):
                protocol.check_message(bad)
        request = chrome()
        self.assertEqual((request.kind, request.width, request.bar, request.holes, request.maximize),
                         ("chrome", 1268, 64, ((300, 16, 80, 28),), (1210, 16, 30, 28)))
        self.assertEqual(protocol.check_request("chrome", json.dumps({"w": 10, "bar": 0, "holes": [], "max": []}),
                                                ()).maximize, ())
        good = {"w": 1268, "bar": 64, "holes": [], "max": []}
        for bad in (dict(good, w=-1), dict(good, bar=401), dict(good, bar=True), dict(good, holes=[[1, 2, 3]]),
                    dict(good, holes=[[1, 2, 3, 4.5]]), dict(good, holes=[[0, 0, 1, 1]] * 41), dict(good, max=[1]),
                    dict(good, max=[-1, 0, 1, 1]), dict(good, extra=1), {"w": 1268}):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_request("chrome", json.dumps(bad), ())
        self.assertEqual(protocol.kind_word("chrome"), "chrome")


class WakeHearingTests(unittest.TestCase):
    make = test_wake_ui.ControllerTests.make
    started = test_wake_ui.ControllerTests.started
    client = test_wake_ui.ControllerTests.client

    def test_each_state(self) -> None:
        controller = self.started()
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_LISTENING, ""))
        cases = {"silent_mic": (wake_ui.HEAR_CANT_HEAR, test_wake_ui.MIC), "virtual_mic": (wake_ui.HEAR_CANT_HEAR, STEAM),
                 "no_mic": (wake_ui.HEAR_OTHER, ""), "mic_busy": (wake_ui.HEAR_OTHER, ""),
                 "audio_missing": (wake_ui.HEAR_OTHER, "")}
        for state, want in cases.items():
            with self.subTest(state):
                mic = STEAM if state == "virtual_mic" else test_wake_ui.MIC
                self.client.on_line({"state": state, "mic": mic, "default": True})
                self.assertEqual(controller.hearing(), want)
        self.holds.append(wake_ui.HOLD_SPEAKING)
        controller.refresh_holds()
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_PAUSED, ""))
        controller.set_on(False)
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_OFF, ""))
        self.assertEqual((wake_ui.HEAR_OFF, wake_ui.HEAR_LISTENING, wake_ui.HEAR_PAUSED, wake_ui.HEAR_CANT_HEAR,
                          wake_ui.HEAR_OTHER, wake_ui.HEAR_HEARING), protocol.HEAR_STATES)   # (part 5B: + hearing)


class TalkUnheardTests(unittest.TestCase):
    make = test_talk_ui.TalkControllerTests.make
    talk_through = test_talk_ui.TalkControllerTests.talk_through

    def talk(self, controller, picked=None) -> None:
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO) as logs:
            controller.toggle()
            self.talk_through(controller)
        self.assertFalse(any("Desk mic" in line for line in logs.output))   # the name: never in a log

    def test_a_talk_that_got_nothing_at_all_until_one_hears_something(self) -> None:
        picked = [""]
        controller = self.make(audio=bytes(16_000 * 2 * 9))
        controller._picked_mic = lambda: picked[0]
        self.assertIsNone(controller.unheard())
        self.talk(controller)
        self.assertEqual(controller.unheard(), "Desk mic")
        self.stream.__init__(noise(300, ROOM) + tone(1200, VOICE) + noise(2000, ROOM))
        self.talk(controller)
        self.assertIsNone(controller.unheard())

    def test_a_microphone_picked_since_and_a_talk_that_heard_only_quiet(self) -> None:
        picked = [""]
        controller = self.make(audio=bytes(16_000 * 2 * 9))
        controller._picked_mic = lambda: picked[0]
        self.talk(controller)
        picked[0] = "Headset"                                              # Settings > Meetings: another one
        self.assertIsNone(controller.unheard())
        picked[0] = ""
        self.assertEqual(controller.unheard(), "Desk mic")
        self.stream.__init__(noise(9000, ROOM))                           # quiet, but not nothing at all
        self.talk(controller)
        self.assertIsNone(controller.unheard())


class FakeWake:
    def __init__(self, state="listening", mic="") -> None:
        self.state, self.mic = state, mic

    def hearing(self):
        return self.state, self.mic


class FakeTalkUnheard:
    def __init__(self, name=None) -> None:
        self.name = name

    def unheard(self):
        return self.name


class CanvasHearingTests(test_canvas.CanvasApp):
    def talk_group(self) -> None:
        # canvas part 5B: the microphone is picked in Settings > Talk (there whenever talking or "Hey Jarvis" is)
        self.reading.settings_page.add_group("talk", "Talk", note="", how="", rows=lambda: [])

    def test_the_badges_state_and_the_note_reach_the_page(self) -> None:
        self.make()
        self.started()
        canvas = self.canvas()
        sent = self.last("canvas")
        self.assertEqual((sent["hear"], sent["deaf"], sent["mic"]), ("", False, ""))      # no "Hey Jarvis" here
        self.c.wake = FakeWake("listening")
        canvas.sync()
        self.assertEqual((self.last("canvas")["hear"], self.last("canvas")["deaf"]), ("listening", False))
        self.c.wake = FakeWake("paused")
        canvas.sync()
        self.assertEqual(self.last("canvas")["hear"], "paused")
        self.c.wake = FakeWake("cant_hear", STEAM)
        canvas.sync()
        sent = self.last("canvas")
        self.assertEqual((sent["hear"], sent["deaf"], sent["mic"]), ("cant_hear", True, STEAM))
        self.assertNotIn("mic_settings", sent["buttons"])                 # no Settings > Talk here: no button
        self.c.wake = FakeWake("listening")
        self.c.talk = FakeTalkUnheard("Desk mic")                          # the last talk got nothing at all
        canvas.sync()
        sent = self.last("canvas")
        self.assertEqual((sent["hear"], sent["deaf"], sent["mic"]), ("listening", True, "Desk mic"))
        self.c.talk = FakeTalkUnheard(None)
        canvas.sync()
        self.assertEqual((self.last("canvas")["deaf"], self.last("canvas")["mic"]), (False, ""))

    def test_the_notes_button_opens_settings_at_talk_and_only_while_it_can(self) -> None:
        self.make()
        self.started()
        canvas = self.canvas()
        self.talk_group()
        self.assertFalse(canvas.on_go("mic_settings"))                     # nothing to say: no button
        self.c.wake = FakeWake("cant_hear", STEAM)
        canvas.sync()
        self.assertIn("mic_settings", self.last("canvas")["buttons"])
        self.assertTrue(canvas.on_go("mic_settings"))
        settle(4)
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_SETTINGS)
        self.assertEqual(self.reading.settings_page.current_group(), "talk")

    def test_the_microphones_name_is_never_logged(self) -> None:
        self.make()
        self.started()
        self.talk_group()
        logger = logging.getLogger("briefing_reader")
        with self.assertLogs("briefing_reader", logging.DEBUG) as logs:
            logger.debug("(a line, so the capture is never empty)")
            self.c.wake = FakeWake("cant_hear", STEAM)
            self.canvas().sync()
            self.canvas().on_go("mic_settings")
            settle(2)
        self.assertFalse(any("Steam" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
