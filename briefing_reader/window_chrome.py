"""Jarvis's main window as a Windows window (the window brief): the canvas keeps its own look - no title bar drawn, the
page's own corner buttons - and gets Windows' own window behaviour, the way custom-chrome apps do it.

While the canvas is what the window shows (ui.BriefingWindow.canvas_window) on real Windows, the window has the styles
of an ordinary window (``chrome_style``: WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX, not
WS_POPUP) and :class:`NativeChrome` answers Windows for it:

* WM_NCCALCSIZE: the whole window is client area (no caption, no frame is drawn: the page draws everything);
* WM_GETMINMAXINFO: maximised fills the work area of the window's own monitor (never over the taskbar, never 8 px past
  the screen's edges as a framed window would be);
* WM_NCHITTEST (``hit_test``): the window's edges resize it (the system's cursors), the page's top band is the caption
  - drag to move, to the screen's top to maximise or a side to snap, double click to maximise or restore, right click
  for the system menu - but for the page's own controls in it, and the page's maximise button is Windows' own
  (HTMAXBUTTON: Snap Layouts on Windows 11); its press is the app's own maximise (``toggle_maximized``) and its hover
  lights the page's button;
* Alt+Space opens the system menu; Alt alone never leaves the window in an invisible menu mode.

Full screen stays Qt's (F11 and the page's control): Qt gives the window the full-screen style (no frame, over the
taskbar) and gives it these styles back after. Everything else - the taskbar button, Alt+Tab, Win+Arrows, Win+D,
minimise and restore with Windows' own animations, Alt+F4 - is Windows' own once the window has these styles. With the
canvas off (the prompt, the fallback) the window is Qt's frameless one, as before. Nothing here runs off Windows or
under the offscreen platform the tests use: the tests drive :class:`NativeChrome` with a stand-in for :class:`Win32`.

Logs: none (a message this cannot read changes nothing).
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSizeF

# ---- Win32's numbers ----------------------------------------------------------------------------------------------
GWL_STYLE = -16
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
CHROME_STYLE = WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

WM_GETMINMAXINFO = 0x0024
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCRBUTTONUP = 0x00A5
WM_SYSKEYDOWN = 0x0104
WM_SYSCOMMAND = 0x0112
WM_MOUSEMOVE = 0x0200
WM_NCMOUSELEAVE = 0x02A2
VK_SPACE = 0x20
# Remote Desktop (2026-11-24): there a layered window shows nothing (Windows sends a layered window's picture as a
# layered picture, and Qt draws this window through its GPU swap chain, not into that picture), so the window is drawn
# solid while the session is remote.
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
SM_REMOTESESSION = 0x1000
WM_DISPLAYCHANGE = 0x007E
WM_WTSSESSION_CHANGE = 0x02B1
REMOTE_CHECK_MS = 2000   # how often a shown window looks again (a fade or a new flag makes Qt layer it again)

HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17

SC_SIZE, SC_MOVE, SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE, SC_KEYMENU, SC_RESTORE = (0xF000, 0xF010, 0xF020, 0xF030, 0xF060,
                                                                               0xF100, 0xF120)

EDGE = 6.0            # px: the window's edges that resize it (its frame's margins: ui._READING_MARGINS)
CORNER = 16.0         # px along an edge from a corner: the corner (both ways)


# ---- the styles ---------------------------------------------------------------------------------------------------

def chrome_style(style: int) -> int:
    """``style`` (GWL_STYLE) as an ordinary window's: the caption, the system menu, the thick frame, minimise and
    maximise; never a popup."""
    return ((style & 0xFFFFFFFF) & ~WS_POPUP) | CHROME_STYLE


def plain_style(style: int, saved: int) -> int:
    """``style`` back to what Qt gave the window (``saved``: before chrome_style) in the bits chrome_style changes."""
    bits = CHROME_STYLE | WS_POPUP
    return ((style & 0xFFFFFFFF) & ~bits) | (saved & bits)


def has_chrome(style: int) -> bool:
    """The window has an ordinary window's frame styles now (not while Qt holds it full screen)."""
    return (style & (WS_CAPTION | WS_THICKFRAME)) == (WS_CAPTION | WS_THICKFRAME)


def menu_items(zoomed: bool) -> dict[int, bool]:
    """The system menu's items and whether each works now (maximised: Restore; else Move, Size and Maximise)."""
    return {SC_RESTORE: zoomed, SC_MOVE: not zoomed, SC_SIZE: not zoomed, SC_MINIMIZE: True, SC_MAXIMIZE: not zoomed,
            SC_CLOSE: True}


# ---- the hit-test -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Band:
    """The page's top band in the window's px: its box, the boxes of the page's own controls in it, and the maximise
    button's (None: not shown)."""

    rect: QRectF
    holes: tuple[QRectF, ...] = ()
    maximize: QRectF | None = None


def band_from(report: Any, origin: QPointF, width: float) -> Band | None:
    """The page's report (board.protocol's "chrome" Request: its px) placed in the window, the page's top left at
    ``origin``. None when there is no report or the page measured another width than the view has now (a resize the
    page has not reported yet: its buttons, anchored at the right, have moved)."""
    if report is None or getattr(report, "kind", "") != "chrome":
        return None
    if abs(int(report.width) - round(width)) > 1:
        return None
    x, y = origin.x(), origin.y()

    def place(box: tuple[int, ...]) -> QRectF:
        return QRectF(x + box[0], y + box[1], box[2], box[3])

    maximize = place(report.maximize) if len(report.maximize) == 4 else None
    return Band(QRectF(x, y, width, report.bar), tuple(place(box) for box in report.holes), maximize)


def hit_test(pos: QPointF, size: QSizeF, *, edges: bool, band: Band | None = None, header: bool = False) -> int:
    """What the point ``pos`` (the window's px) is, as Windows asks it (HT*): an edge or a corner (``edges``: the
    window is resizable and not maximised), the maximise button, the page's own control, the caption (the page's top
    band; ``header``: the point is on the empty part of a screen's own header) or the client area."""
    x, y, w, h = pos.x(), pos.y(), size.width(), size.height()
    if edges:
        left, right, top, bottom = x < EDGE, x >= w - EDGE, y < EDGE, y >= h - EDGE
        if left or right or top or bottom:
            near_left, near_right = x < CORNER, x >= w - CORNER
            near_top, near_bottom = y < CORNER, y >= h - CORNER
            if (top and near_left) or (left and near_top):
                return HTTOPLEFT
            if (top and near_right) or (right and near_top):
                return HTTOPRIGHT
            if (bottom and near_left) or (left and near_bottom):
                return HTBOTTOMLEFT
            if (bottom and near_right) or (right and near_bottom):
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            return HTTOP if top else HTBOTTOM
    if band is not None and band.rect.contains(pos):
        if band.maximize is not None and band.maximize.contains(pos):
            return HTMAXBUTTON
        if any(hole.contains(pos) for hole in band.holes):
            return HTCLIENT
        return HTCAPTION
    if header:
        return HTCAPTION
    return HTCLIENT


# ---- Windows' answers -----------------------------------------------------------------------------------------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", POINT), ("ptMaxSize", POINT), ("ptMaxPosition", POINT), ("ptMinTrackSize", POINT),
                ("ptMaxTrackSize", POINT)]


class NCCALCSIZE_PARAMS(ctypes.Structure):  # noqa: N801 - Win32's name
    _fields_ = [("rgrc", RECT * 3), ("lppos", ctypes.c_void_p)]


def answer_minmax(address: int, monitor: tuple[int, int, int, int], work: tuple[int, int, int, int]) -> None:
    """WM_GETMINMAXINFO: maximised, the window is the work area of its monitor (``monitor`` and ``work``: left, top,
    right, bottom in physical px) - ptMaxPosition relative to the monitor, as Windows reads it."""
    info = MINMAXINFO.from_address(address)
    info.ptMaxPosition.x = work[0] - monitor[0]
    info.ptMaxPosition.y = work[1] - monitor[1]
    info.ptMaxSize.x = work[2] - work[0]
    info.ptMaxSize.y = work[3] - work[1]


def answer_nccalcsize(calc_valid: bool, address: int, work: tuple[int, int, int, int] | None) -> int:
    """WM_NCCALCSIZE: the client area is the whole window (no caption or frame of Windows' own). ``work``: the window
    is maximised - its client area stays inside its monitor's work area (WM_GETMINMAXINFO already puts it there; a
    maximise that did not ask it would leave the frame's 8 px past the screen)."""
    if work is not None:
        rect = NCCALCSIZE_PARAMS.from_address(address).rgrc[0] if calc_valid else RECT.from_address(address)
        rect.left, rect.top = max(rect.left, work[0]), max(rect.top, work[1])
        rect.right, rect.bottom = min(rect.right, work[2]), min(rect.bottom, work[3])
    return 0


def proposed_rect(calc_valid: bool, address: int) -> tuple[int, int, int, int] | None:
    """WM_NCCALCSIZE's proposed window rect (rgrc[0], or the RECT itself), physical px; None without one."""
    if not address:
        return None
    rect = NCCALCSIZE_PARAMS.from_address(address).rgrc[0] if calc_valid else RECT.from_address(address)
    return rect.left, rect.top, rect.right, rect.bottom


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def solid_ex_style(ex_style: int, remote: bool) -> int | None:
    """The window's extended style in a Remote Desktop session: without WS_EX_LAYERED (layered, it shows nothing
    there). None when nothing changes: not remote, or not layered."""
    if not remote or not ex_style & WS_EX_LAYERED:
        return None
    return ex_style & ~WS_EX_LAYERED


def point_of(lparam: int) -> tuple[int, int]:
    """A message's screen point (GET_X_LPARAM, GET_Y_LPARAM: signed, for monitors left of or above the main one)."""
    return _signed16(int(lparam)), _signed16(int(lparam) >> 16)


class Win32:
    """The Win32 calls NativeChrome makes (the real window only; tests use a stand-in)."""

    def __init__(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wintypes.UINT]
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromRect.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        user32.MonitorFromRect.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        user32.TrackMouseEvent.argtypes = [ctypes.c_void_p]
        user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
        user32.GetSystemMenu.restype = wintypes.HMENU
        user32.EnableMenuItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, ctypes.c_void_p]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, wintypes.UINT]
        self._user32 = user32

    def remote_session(self) -> bool:
        """This app's session is a Remote Desktop one."""
        return bool(self._user32.GetSystemMetrics(SM_REMOTESESSION))

    def ex_style(self, hwnd: int) -> int:
        return int(self._user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF

    def set_ex_style(self, hwnd: int, ex_style: int) -> None:
        """The new extended style, and the window drawn again at once."""
        self._user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style & 0x7FFFFFFF)
        rdw_invalidate, rdw_erase, rdw_allchildren, rdw_updatenow = 0x0001, 0x0004, 0x0080, 0x0100
        self._user32.RedrawWindow(hwnd, None, None, rdw_invalidate | rdw_erase | rdw_allchildren | rdw_updatenow)

    def style(self, hwnd: int) -> int:
        return int(self._user32.GetWindowLongPtrW(hwnd, GWL_STYLE)) & 0xFFFFFFFF

    def set_style(self, hwnd: int, style: int) -> None:
        """The new style, and Windows told the frame changed (it asks WM_NCCALCSIZE again; nothing moves)."""
        style &= 0xFFFFFFFF
        self._user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style - 0x100000000 if style & 0x80000000 else style)
        swp_nosize, swp_nomove, swp_nozorder, swp_noactivate, swp_framechanged, swp_noownerzorder = (
            0x0001, 0x0002, 0x0004, 0x0010, 0x0020, 0x0200)
        self._user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, swp_nosize | swp_nomove | swp_nozorder | swp_noactivate
                                  | swp_framechanged | swp_noownerzorder)

    def is_zoomed(self, hwnd: int) -> bool:
        return bool(self._user32.IsZoomed(hwnd))

    def monitor(self, hwnd: int) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
        """(the monitor's box, its work area) of the window's monitor, physical px; None if unknown."""

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        handle = self._user32.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not handle or not self._user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        m, w = info.rcMonitor, info.rcWork
        return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)

    def monitor_at(self, rect: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int],
                                                                    tuple[int, int, int, int]] | None:
        """(the monitor's box, its work area) of the monitor a proposed window rect is on (MonitorFromRect, the nearest),
        physical px; None if unknown. Canvas part 5B (the resilience review's M1): mid-move, MonitorFromWindow still
        names the monitor the window is leaving."""

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        box = RECT(*rect)
        handle = self._user32.MonitorFromRect(ctypes.byref(box), 2)    # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not handle or not self._user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        m, w = info.rcMonitor, info.rcWork
        return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)

    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        point = POINT(x, y)
        self._user32.ScreenToClient(hwnd, ctypes.byref(point))
        return point.x, point.y

    def client_origin(self, hwnd: int) -> tuple[int, int]:
        point = POINT(0, 0)
        self._user32.ClientToScreen(hwnd, ctypes.byref(point))
        return point.x, point.y

    def track_leave(self, hwnd: int) -> None:
        """WM_NCMOUSELEAVE when the pointer leaves the window's non-client part (the maximise button)."""

        class TRACKMOUSEEVENT(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong), ("hwndTrack", ctypes.c_void_p),
                        ("dwHoverTime", ctypes.c_ulong)]

        event = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x00000002 | 0x00000010, hwnd, 0)   # TME_LEAVE | NONCLIENT
        self._user32.TrackMouseEvent(ctypes.byref(event))

    def system_menu(self, hwnd: int, x: int, y: int, zoomed: bool) -> int:
        """The window's system menu at (x, y) (screen px), its items as menu_items says; the command picked (0 none)."""
        menu = self._user32.GetSystemMenu(hwnd, False)
        if not menu:
            return 0
        mf_bycommand, mf_enabled, mf_grayed = 0x0000, 0x0000, 0x0001
        for command, works in menu_items(zoomed).items():
            self._user32.EnableMenuItem(menu, command, mf_bycommand | (mf_enabled if works else mf_grayed))
        self._user32.SetMenuDefaultItem(menu, SC_CLOSE, 0)
        tpm_returncmd, tpm_rightbutton = 0x0100, 0x0002
        return int(self._user32.TrackPopupMenu(menu, tpm_returncmd | tpm_rightbutton, x, y, 0, hwnd, None))

    def post_syscommand(self, hwnd: int, command: int) -> None:
        self._user32.PostMessageW(hwnd, WM_SYSCOMMAND, command, 0)


class NativeChrome:
    """Windows' answers for one window (see the module doc). ``window`` gives ``chrome_hit(QPointF) -> HT*`` (its own
    px), ``devicePixelRatioF()``, ``toggle_maximized()``, ``set_max_hover(bool)`` and ``call_soon(fn)`` (after the
    message: a menu or a state change never runs inside Windows' own call); ``api`` the Win32 calls (made at the first
    use)."""

    def __init__(self, window: Any, api: Any = None) -> None:
        self._window = window
        self._api = api
        self._wanted = False
        self._saved: int | None = None        # Qt's style before chrome_style (the bits it changes go back to it)
        self._hwnd = 0
        self._max_down = False
        self._hover = False
        self._tracking = False

    def api(self) -> Any:
        if self._api is None:
            self._api = Win32()
        return self._api

    # ---- on and off ---------------------------------------------------------------------------------------------

    def sync(self, hwnd: int, wanted: bool, *, apply: bool = True) -> bool:
        """The window ``hwnd`` has an ordinary window's styles exactly while ``wanted`` (``apply`` False: full screen -
        Qt holds the style and gives it back after; only the wish is kept). True when its style changed."""
        self._wanted = bool(wanted)
        self._hwnd = int(hwnd)
        if not hwnd or not apply:
            return False
        api = self.api()
        style = api.style(hwnd)
        if wanted:
            new = chrome_style(style)
            if new != style and self._saved is None:
                self._saved = style
        else:
            if self._saved is None:
                return False
            new = plain_style(style, self._saved)
            self._saved = None
            self._set_hover(False)
        if new == style:
            return False
        api.set_style(hwnd, new)
        return True

    def wanted(self) -> bool:
        return self._wanted

    def keep_solid(self, hwnd: int) -> bool:
        """In a Remote Desktop session the window ``hwnd`` is drawn solid (solid_ex_style). True when its style
        changed."""
        if not hwnd:
            return False
        api = self.api()
        new = solid_ex_style(api.ex_style(hwnd), api.remote_session())
        if new is None:
            return False
        api.set_ex_style(hwnd, new)
        return True

    def active(self, hwnd: int) -> bool:
        """Windows' answers are the app's now: wanted, and the window has the styles (not full screen)."""
        return self._wanted and bool(hwnd) and has_chrome(self.api().style(hwnd))

    # ---- Windows' messages ----------------------------------------------------------------------------------------

    def handle(self, msg: Any) -> tuple[bool, int] | None:
        """One message (``msg``: hWnd, message, wParam, lParam, as wintypes.MSG): (True, result) when this answers it,
        None to let Qt and Windows answer."""
        kind = int(msg.message)
        if kind not in _HANDLED or not self._wanted:
            return None
        hwnd = int(msg.hWnd or 0)
        if kind == WM_MOUSEMOVE:
            self._set_hover(False)                 # the pointer is in the client area again
            return None
        if kind == WM_NCMOUSELEAVE:
            self._tracking = False
            self._set_hover(False)
            return None
        if not self.active(hwnd):
            return None
        wparam, lparam = int(msg.wParam or 0), int(msg.lParam or 0)
        api = self.api()
        if kind == WM_NCCALCSIZE:
            work = None
            if api.is_zoomed(hwnd):
                # part 5B (the resilience review's M1): the monitor the proposed rect is on - mid-move (Win+Shift+Right
                # while maximised) the window's own is still the one it leaves, and its work area cut it to 8 px
                proposed = proposed_rect(bool(wparam), lparam)
                at = getattr(api, "monitor_at", None)
                screens = at(proposed) if callable(at) and proposed is not None else api.monitor(hwnd)
                work = screens[1] if screens is not None else None
            return True, answer_nccalcsize(bool(wparam), lparam, work)
        if kind == WM_GETMINMAXINFO:
            screens = api.monitor(hwnd)
            if screens is not None and lparam:
                answer_minmax(lparam, *screens)
            return None                            # Qt still sets the smallest size the window takes
        if kind == WM_NCHITTEST:
            x, y = api.screen_to_client(hwnd, *point_of(lparam))
            ratio = float(self._window.devicePixelRatioF() or 1.0)
            return True, int(self._window.chrome_hit(QPointF(x / ratio, y / ratio)))
        if kind == WM_NCMOUSEMOVE:
            hover = wparam == HTMAXBUTTON
            if hover and not self._tracking:
                api.track_leave(hwnd)
                self._tracking = True
            self._set_hover(hover)
            return None
        if kind in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK):
            if wparam != HTMAXBUTTON:
                self._max_down = False
                return None
            self._max_down = True                  # Windows' own button is never drawn or pressed: the page's is
            return True, 0
        if kind == WM_NCLBUTTONUP:
            if wparam != HTMAXBUTTON:
                self._max_down = False
                return None
            if self._max_down:
                self._max_down = False
                self._window.call_soon(self._window.toggle_maximized)
            return True, 0
        if kind == WM_NCRBUTTONUP:
            if wparam != HTCAPTION:
                return None
            x, y = point_of(lparam)
            self._window.call_soon(lambda: self._menu(hwnd, x, y))
            return True, 0
        if kind == WM_SYSKEYDOWN:
            if wparam != VK_SPACE:
                return None
            x, y = api.client_origin(hwnd)
            self._window.call_soon(lambda: self._menu(hwnd, x, y))
            return True, 0
        if kind == WM_SYSCOMMAND:
            if (wparam & 0xFFF0) == SC_KEYMENU and lparam == 0:
                return True, 0                     # Alt alone: no invisible menu mode taking the next key
            return None
        return None

    def _menu(self, hwnd: int, x: int, y: int) -> None:
        """The system menu (Restore, Move, Size, Minimise, Maximise, Close): the command picked goes to Windows as if
        its own menu had been used (Close: the window's close, as the app decides it)."""
        if not self.active(hwnd):
            return
        api = self.api()
        command = api.system_menu(hwnd, x, y, api.is_zoomed(hwnd))
        if command:
            api.post_syscommand(hwnd, command)

    def _set_hover(self, hover: bool) -> None:
        if hover != self._hover:
            self._hover = hover
            self._window.set_max_hover(hover)

    def hovered(self) -> bool:
        return self._hover


_HANDLED = frozenset({WM_NCCALCSIZE, WM_GETMINMAXINFO, WM_NCHITTEST, WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP,
                      WM_NCLBUTTONDBLCLK, WM_NCRBUTTONUP, WM_SYSKEYDOWN, WM_SYSCOMMAND, WM_MOUSEMOVE, WM_NCMOUSELEAVE})


def call_soon(fn: Callable[[], Any]) -> None:
    """``fn`` right after the message being answered (Qt's event loop)."""
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, fn)


__all__ = ["Band", "CHROME_STYLE", "NativeChrome", "Win32", "answer_minmax", "answer_nccalcsize", "band_from",
           "chrome_style", "has_chrome", "hit_test", "menu_items", "plain_style", "point_of"]
