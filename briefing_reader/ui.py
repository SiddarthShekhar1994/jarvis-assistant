"""PySide6 user interface: the HUD prompt and reading screens and the app controller.

The module is organised in three parts:

* theme and helpers: the Jarvis HUD theme (widgets and tokens live in
  :mod:`briefing_reader.hud`), the app icon (drawn at runtime) and small Win32
  helpers for focus;
* views: ``PromptView`` ("Hear it now?" beside the status orb),
  ``ReadingView`` (STATUS and the TODAY / DEADLINES agenda on the left; orb,
  current line, controls and the transcript, with a SECTIONS popover, in the
  middle; approvals and activity on the right) and ``BriefingWindow``, the
  frameless HUD window that stacks the two;
* ``AppController``: the state machine ("prompt", "snoozed", "reading",
  "quitting") that owns the window, tray icon, fetch thread, TTS worker,
  player and the action worker (approved proposals, the agenda, the cards'
  event checks and the Google sign-in of each account).

Threading: Qt objects are only touched on the GUI thread. The fetch thread,
the TTS worker and the action worker only emit ``_Bridge`` signals, which
are queued to the GUI thread where all state lives. Every worker is a daemon
thread, so a stuck network call or a browser sign-in can never block exit
(only a change already on its way to Google is waited for, at most 5 s). The
action worker runs one call at a time, so reading the agenda never overlaps a
sign-in; while an approval, its undo countdown or a sign-in runs, the other
cards' Deny / Approve are locked.
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import hud
from .actions import (
    CALENDAR,
    CANCEL,
    DONE_TEXT,
    MOVE,
    RETRY_TEXT,
    RSVP,
    STATUS_CREATED,
    STATUS_DENIED,
    STATUS_DONE,
    STATUS_EXISTS,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SENT,
    STATUS_UNKNOWN,
    TODO,
    ActionStore,
    CardView,
    EditInvalid,
    ProposedAction,
    card_view,
    copied_text,
    copy_text,
    extract_actions,
    kind_label,
    link_allowed,
    link_host,
    parse_time_range,
    result_text,
    when_text,
)
from .agenda import (
    Deadline,
    agenda_window,
    deadlines_from_events,
    due_label,
    event_rows,
    events_in_window,
    extract_deadlines,
    merge_deadlines,
)
from .config import DEFAULT_ACCOUNT, AgendaConfig, Config
from .executor import (
    STAGE_SIGNED_IN,
    STAGE_SIGNIN,
    STAGE_WORKING,
    ActionEdit,
    CalendarBackend,
    EditError,
    EventCheck,
    ExecError,
    ExecResult,
    Executor,
    account_of,
    apply_edit,
    build_calendars,
    check_event,
    check_failure,
    run_action,
    sign_in_note,
)
from .gcal import (
    CalendarAuthError,
    CalendarError,
    CalendarEvent,
    CalendarNotSignedIn,
    CalendarSetupError,
    GoogleCalendar,
)
from .google_auth import (
    PROBLEM_BLOCKED,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SCOPE,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    logged_alias,
)
from .models import (
    ITEM_ENTRY,
    ITEM_HEADING,
    ITEM_INTRO,
    ITEM_NOTE,
    ITEM_OUTRO,
    ITEM_SUBHEADING,
    Briefing,
    BriefingHeader,
    Freshness,
    Script,
    ScriptItem,
    Section,
    SectionAudio,
)
from .notion_client import (
    NotionClient,
    NotionError,
    PollResult,
    check_freshness,
    fetch_briefing,
    poll_for_briefing,
)
from .player import FINISHED, IDLE, PAUSED, PLAYING, WAITING, BriefingPlayer
from .runstate import RunState, handled_slot_key, slot_for
from .text_prep import ACTIONS_KEY, build_script, describe_updated, format_time, updated_label
from .tts import SAPI, SpeechSynthesizer, TtsWorker

logger = logging.getLogger(__name__)

DOT = hud.MIDDLE_DOT
_QWIDGETSIZE_MAX = 16777215

# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------


def apply_dark_theme(app: QApplication) -> None:
    """The HUD theme: Fusion, the dark HUD palette, Sora 13 px and the HUD stylesheet.

    Call ``hud.load_fonts`` first so the bundled typefaces are used.
    """
    hud.apply_theme(app)


def app_icon() -> QIcon:
    """The window and tray icon: the HUD logo (a cyan diamond) on the dark ground."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(hud.chamfer_path(QRectF(2, 2, 60, 60), 12), QColor(hud.GROUND))
        painter.setPen(QPen(hud.rgba(hud.ACCENT, 0.45), 1.5))
        painter.drawPath(hud.chamfer_path(QRectF(2.75, 2.75, 58.5, 58.5), 11.5))
        painter.translate(32, 32)
        painter.rotate(45)
        painter.setPen(QPen(QColor(hud.ACCENT), 5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(-12, -12, 24, 24))
    finally:
        painter.end()
    return QIcon(pixmap)


# --------------------------------------------------------------------------
# Windows helpers (no-ops elsewhere and under the offscreen test platform)
# --------------------------------------------------------------------------

_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_user32_dll: Any = None


def _native_windows() -> bool:
    """True only for real Win32 windows (never for the offscreen platform used in tests)."""
    return sys.platform == "win32" and QGuiApplication.platformName() == "windows"


def _user32() -> Any:
    global _user32_dll
    if _user32_dll is None:
        from ctypes import wintypes
        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.GetForegroundWindow.restype = wintypes.HWND
        dll.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        dll.GetWindowThreadProcessId.restype = wintypes.DWORD
        dll.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        dll.BringWindowToTop.argtypes = [wintypes.HWND]
        dll.SetForegroundWindow.argtypes = [wintypes.HWND]
        dll.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        dll.keybd_event.restype = None
        _user32_dll = dll
    return _user32_dll


def force_foreground(widget: QWidget) -> None:
    """Show, raise and focus ``widget``, even when launched in the background by Task Scheduler."""
    if widget.isMinimized():
        widget.showNormal()
    else:
        widget.show()
    widget.raise_()
    widget.activateWindow()
    if not _native_windows():
        return
    try:
        _win32_bring_to_front(int(widget.winId()))
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.debug("Could not bring the window to the foreground: %s", exc)


def _win32_bring_to_front(hwnd: int) -> None:
    user32 = _user32()
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return
    if _set_foreground_attached(user32, hwnd):
        return
    # Last resort: a synthetic Alt press counts as recent input, which lifts
    # Windows' focus-stealing lock for this process.
    user32.keybd_event(_VK_MENU, 0, 0, 0)
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    logger.debug("Foreground via the Alt key: %s", int(user32.GetForegroundWindow() or 0) == hwnd)


def _set_foreground_attached(user32: Any, hwnd: int) -> bool:
    """SetForegroundWindow while attached to the foreground thread's input queue."""
    foreground = user32.GetForegroundWindow()
    ours = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(theirs and theirs != ours and user32.AttachThreadInput(theirs, ours, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(theirs, ours, False)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def show_without_activating(widget: QWidget) -> None:
    """Show ``widget`` on top without taking keyboard focus from the current app.

    Once the native window exists, Qt's Windows plugin reads the QWindow
    property ``_q_showWithoutActivating`` (during show()), not the widget
    attribute; the attribute covers a first show before the window exists.
    """
    handle = widget.windowHandle()
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    if handle is not None:
        handle.setProperty("_q_showWithoutActivating", True)
    try:
        if widget.isMinimized():
            widget.showNormal()
        else:
            widget.show()
    finally:
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        if handle is not None:
            handle.setProperty("_q_showWithoutActivating", False)


# --------------------------------------------------------------------------
# Small widget helpers
# --------------------------------------------------------------------------

def _label(font: QFont, color: str, *, wrap: bool = False, name: str = "") -> QLabel:
    """A plain-text label (page text is never interpreted as HTML)."""
    return hud.make_label("", font, color, wrap=wrap, name=name)


def _caps(font: QFont) -> QFont:
    """``font`` drawn in capitals; the label's text() keeps the original case."""
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    return font


def _caps_meta(panel: hud.ChamferPanel) -> None:
    """Draw the panel's right-hand meta in capitals ("8 SECTIONS"); its text() keeps the case."""
    panel.meta_label.setFont(_caps(panel.meta_label.font()))


def _set_status_style(label: QLabel, is_error: bool, normal: str) -> None:
    """Amber for problems; the "error" property stays readable for anyone inspecting the label."""
    hud.set_label_color(label, hud.AMBER if is_error else normal)
    label.setProperty("error", is_error)


def _screen_area(point: QPoint) -> QRect:
    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def _short(text: str, limit: int = 90) -> str:
    """One line of at most ``limit`` characters (activity sub-lines)."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 3].rstrip() + "..."


def _section_title(section: Section) -> str:
    if section.title:
        return section.title
    return {"intro": "Intro", "outro": "Outro"}.get(section.key, "Section")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _needs_ok(count: int) -> str:
    """"1 needs your OK" / "2 need your OK" (prompt chip and calendar chip tooltip)."""
    return f"{count} needs your OK" if count == 1 else f"{count} need your OK"


# --------------------------------------------------------------------------
# Views: prompt
# --------------------------------------------------------------------------

_PROMPT_ORB_PX = 112
_DETAIL_CAPS_MAX = 48     # longer details (errors, setup hints) read better as prose than mono caps


class PromptView(QWidget):
    """The "Your AM briefing is ready. Hear it now?" screen: status orb, question and answers."""

    readNow = Signal()
    later = Signal(int)
    dismiss = Signal()

    def __init__(self, short_minutes: int, long_minutes: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.orb = hud.Orb(_PROMPT_ORB_PX, hud.ORB_WAITING)
        self.state_label = hud.StateLabel(hud.ORB_WAITING)
        self.headline = hud.SpeechLabel()
        self.headline.setObjectName("headline")
        self.detail = _label(_caps(hud.mono_font(11, 400, 0.08)), hud.TEXT_MUTED, wrap=True, name="detail")
        self._detail_prose = False
        self.status = _label(hud.mono_font(11, 400, 0.02), hud.TEXT_SOFT, wrap=True, name="status")
        self.pending_chip = hud.HudChip("")
        self.pending_chip.hide()
        self.countdown = _label(hud.mono_font(11, 400, 0.04), hud.TEXT_DIM, name="countdown")
        self.read_button = hud.HudButton("Read now", hud.PRIMARY)
        self.short_button = hud.HudButton(f"Later ({short_minutes} min)")
        self.long_button = hud.HudButton(f"Later ({long_minutes} min)")
        self.dismiss_button = hud.HudButton("Dismiss", hud.LINK)
        self.read_button.clicked.connect(lambda: self.readNow.emit())
        self.short_button.clicked.connect(lambda: self.later.emit(short_minutes))
        self.long_button.clicked.connect(lambda: self.later.emit(long_minutes))
        self.dismiss_button.clicked.connect(lambda: self.dismiss.emit())
        self._build_layout()

    def _build_layout(self) -> None:
        texts = QVBoxLayout()
        texts.setSpacing(6)
        for widget in (self.state_label, self.headline, self.detail, self.status):
            texts.addWidget(widget)
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 2, 0, 0)
        chip_row.addWidget(self.pending_chip)
        chip_row.addStretch(1)
        texts.addLayout(chip_row)
        texts.addStretch(1)
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 0)
        body.setSpacing(18)
        body.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(texts, 1)
        # Room around the buttons: the Read now glow is drawn outside the button.
        buttons = QHBoxLayout()
        buttons.setContentsMargins(14, 0, 14, 0)
        buttons.setSpacing(6)
        for button in (self.read_button, self.short_button, self.long_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 0, 14, 0)
        footer.addWidget(self.dismiss_button)
        footer.addStretch(1)
        footer.addWidget(self.countdown)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(body, 1)
        layout.addSpacing(14)
        layout.addLayout(buttons)
        layout.addSpacing(8)
        layout.addLayout(footer)

    def set_content(self, headline: str, detail: str, status: str, status_is_error: bool,
                    can_read: bool) -> None:
        self.headline.setText(headline)
        self._set_detail(detail)
        self.status.setText(status)
        self.status.setVisible(bool(status))
        _set_status_style(self.status, status_is_error, hud.TEXT_SOFT)
        for button in (self.read_button, self.short_button, self.long_button):
            button.setEnabled(can_read)

    def _set_detail(self, text: str) -> None:
        prose = len(text) > _DETAIL_CAPS_MAX or "\n" in text
        if prose != self._detail_prose:
            self._detail_prose = prose
            self.detail.setFont(hud.body_font(13) if prose else _caps(hud.mono_font(11, 400, 0.08)))
            hud.set_label_color(self.detail, hud.TEXT_SOFT if prose else hud.TEXT_MUTED)
        self.detail.setText(text)
        self.detail.setVisible(bool(text))

    def set_countdown(self, text: str) -> None:
        self.countdown.setText(text)
        self.countdown.setVisible(bool(text))

    def set_state(self, state: str) -> None:
        """Orb and state label: waiting on the user, working (first fetch) or standby (problem)."""
        self.orb.set_state(state)
        if state != self.state_label.state():
            self.state_label.set_state(state)

    def set_pending(self, count: int) -> None:
        """The amber "2 NEED YOUR OK" chip (hidden when nothing waits for a decision)."""
        if count > 0:
            self.pending_chip.set_text(_needs_ok(count))
        self.pending_chip.setVisible(count > 0)


# --------------------------------------------------------------------------
# Views: reading
# --------------------------------------------------------------------------

_PROPORTIONAL = QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
_FIXED_HEIGHT = QTextBlockFormat.LineHeightTypes.FixedHeight.value
_PAUSE_GAP_PX = 8.0
_HEADING_TOP_PX = 18.0
_ROW_GAP_PX = 4.0
_ROW_PAD_PX = 3.0             # highlight band above and below the item's text
_INDENT_PX = 18
_DOC_MARGIN_PX = 14
_SCROLL_ANCHOR = 0.35          # highlighted block sits this far down the panel
_MANUAL_SCROLL_HOLD_S = 4.0
_BULLET = "\u2022 "   # typographic bullet before list entries
_LIST_PREFIX_RE = re.compile(r"^(?:\d+\.\s|\[[ xX]\]\s)")
_APPROVALS_MAX_PX = 340        # the cards scroll beyond this, leaving room for the activity log
_SECTIONS_POPOVER_WIDTH = 300  # the SECTIONS list (the artboard's left-column width)
_SECTIONS_POPOVER_MIN_PX = 220  # it may reach past the window bottom rather than get smaller
_ITEM_COLORS = {ITEM_NOTE: hud.AMBER, ITEM_INTRO: hud.TEXT_SOFT, ITEM_OUTRO: hud.TEXT_SOFT,
                ITEM_SUBHEADING: hud.TEXT_BRIGHT}
_NOT_READ_SUFFIX = "  (not read aloud)"


def _line_percent(font: QFont, css_line_height: float) -> int:
    """Qt's proportional line height for a CSS ``line-height`` (Qt's is relative to the font's own height)."""
    natural = QFontMetricsF(font).height()
    return round(100 * css_line_height * font.pixelSize() / natural) if natural > 0 else 100


class _TranscriptStyle:
    """Fonts and line heights of the transcript (built once the fonts are loaded)."""

    def __init__(self) -> None:
        self.body = hud.body_font(14)
        self.heading = _caps(hud.mono_font(11, 500, 0.16))
        self.subheading = hud.body_font(14, 600)
        self.body_metrics = QFontMetricsF(self.body)
        self.body_line = _line_percent(self.body, 1.6)
        self.heading_line = _line_percent(self.heading, 1.5)


class _Transcript(QTextBrowser):
    """The reading panel's text on a transparent ground.

    The current item is drawn like the artboard's agenda rows (a glowing 3 px
    accent bar on the left and a left-to-right cyan gradient) under the text,
    plus an ExtraSelection that brightens the item's own text.
    """

    def __init__(self, style: _TranscriptStyle) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setUndoRedoEnabled(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.document().setDefaultFont(style.body)
        self.document().setDocumentMargin(_DOC_MARGIN_PX)
        self.document().setIndentWidth(_INDENT_PX)
        self._row = -1

    def set_row(self, block_number: int) -> None:
        """Draw the agenda-row highlight behind this block (-1: none)."""
        if block_number != self._row:
            self._row = block_number
            self.viewport().update()

    def row(self) -> int:
        return self._row

    def row_rect(self) -> QRectF | None:
        """The highlighted row in viewport coordinates, or None."""
        if self._row < 0:
            return None
        block = self.document().findBlockByNumber(self._row)
        layout = block.layout() if block.isValid() and block.text() else None
        if layout is None or layout.lineCount() == 0:
            return None
        first, last = layout.lineAt(0), layout.lineAt(layout.lineCount() - 1)
        top = layout.position().y() + first.y()
        bottom = layout.position().y() + last.y() + last.height()
        offset = self.verticalScrollBar().value()
        return QRectF(2, top - offset - _ROW_PAD_PX, self.viewport().width() - 4,
                      bottom - top + 2 * _ROW_PAD_PX)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        rect = self.row_rect()
        if rect is not None:
            painter = QPainter(self.viewport())
            try:
                hud.paint_row_highlight(painter, rect, hud.ACCENT, dpr=self.devicePixelRatioF())
            finally:
                painter.end()
        super().paintEvent(event)


class _DashedRule(QWidget):
    """A 1 px dashed line (the artboard's ``border-bottom: 1px dashed``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            pen = QPen(hud.rgba(hud.ACCENT, 0.2), 1)
            pen.setDashPattern([4.0, 3.0])
            painter.setPen(pen)
            painter.drawLine(0, 0, self.width(), 0)
        finally:
            painter.end()


@dataclasses.dataclass(frozen=True)
class _Tier:
    """Column widths and orb size for reading views at least ``min_width`` wide."""

    min_width: int
    left: int
    right: int
    orb: int


# Wide (artboard), default 1120 px window, minimum 900 px window, and screens too small for that.
_TIERS = (_Tier(1320, 300, 372, 168), _Tier(1040, 260, 340, 168), _Tier(860, 230, 300, 112),
          _Tier(0, 190, 250, 96))


class ReadingView(QWidget):
    """The reading screen.

    Left: compact STATUS telemetry, then one panel with the TODAY / TOMORROW
    agenda and the DEADLINES list (its Connect link emits
    ``connectCalendar``). Middle: the orb with its state, the line being
    spoken, the controls and the transcript (with a moving highlight); the
    SECTIONS button in the transcript's header strip opens the section list
    (click a row to jump). Right: the NEEDS YOUR OK cards and the ACTIVITY log.
    """

    playPause = Signal()
    skip = Signal()
    readEverything = Signal()
    openNotion = Signal()
    done = Signal()
    sectionClicked = Signal(int)
    approveClicked = Signal(str)
    denyClicked = Signal(str)
    lockedClicked = Signal(str)
    openLink = Signal(str)
    openSource = Signal(str)     # a card's Open (tools row): the action id, never the URL
    copyText = Signal(str)       # a card's Copy (tools row): the action id
    undoAction = Signal(str)     # a card's Undo during its countdown: the action id
    editAction = Signal(str)     # a card's Edit (tools row): the action id
    signInAccount = Signal(str)  # a card's Sign in (tools row): the action id
    connectCalendar = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._style = _TranscriptStyle()
        self._block_numbers: dict[tuple[int, int], int] = {}
        self._current = (-1, -1)
        self._user_scrolled_at = -math.inf
        self._speech_fallback = ""
        self._rows: tuple[hud.SectionRowInfo, ...] = ()
        self._steps: list[tuple[str, str]] = []
        self._shown_section: int | None = None
        self._tier: _Tier | None = None
        self._make_status_column()
        self._make_center_column()
        self._make_sections_popover()
        self._make_side_column()
        self._scroll_animation = QPropertyAnimation(self.text.verticalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(220)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.text.verticalScrollBar().actionTriggered.connect(self._on_user_scroll)
        # Space plays/pauses unless a button has focus (BriefingWindow.event lets the button have it).
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.setAutoRepeat(False)   # holding Space must not toggle over and over
        shortcut.activated.connect(lambda: self.playPause.emit())
        self._build_layout()

    # ---- construction ----------------------------------------------------

    def _make_status_column(self) -> None:
        # Compact (tighter than the artboard), so the agenda below gets the room.
        self.status_panel = hud.ChamferPanel("Status", padding=(16, 12, 16, 12), spacing=8)
        self.progress_bar = hud.TelemetryBar("Progress", "0 / 0", 0.0, hud.ACCENT)
        self.voice_bar = hud.TelemetryBar("Voice", "--", 0.0, hud.TEXT_DIM)
        self.updated_bar = hud.TelemetryBar("Updated", "--", 0.0, hud.TEXT_DIM)
        self.approvals_bar = hud.TelemetryBar("Needs your OK", "0", 0.0, hud.AMBER)
        self.status = _label(hud.mono_font(11, 400, 0.02), hud.TEXT_SOFT, wrap=True, name="status")
        for widget in (self.progress_bar, self.voice_bar, self.updated_bar, self.approvals_bar,
                       self.status):
            self.status_panel.body_layout.addWidget(widget)
        self.agenda_panel = hud.AgendaPanel("Today")
        self.agenda_panel.linkClicked.connect(self.connectCalendar)

    def _make_sections_popover(self) -> None:
        """SECTIONS: a button in the transcript strip and the list it opens (rows keep their states)."""
        self.sections_button = hud.ChipButton("Sections")
        self.sections_button.setToolTip("The briefing's sections: click one to play from there")
        self.sections_button.setEnabled(False)
        self.sections_button.toggled.connect(self._on_sections_toggled)
        self.sections_popover = hud.Popover("Sections", "", parent=self)
        self.sections_popover.closed.connect(self._on_sections_closed)
        self.sections_panel = self.sections_popover.panel
        _caps_meta(self.sections_panel)
        self.sections = hud.SectionList()
        self.sections.rowClicked.connect(self._on_section_row)
        self.sections_panel.body_layout.addWidget(self.sections, 1)

    def _make_center_column(self) -> None:
        self.orb = hud.Orb(168, hud.ORB_WORKING)
        self.state_label = hud.StateLabel(hud.ORB_WORKING)
        self.speech = hud.SpeechLabel(max_lines=3)
        self.speech.setObjectName("speech")
        self._make_buttons()
        self.title = _label(_caps(hud.display_font(15, 600, 0.12)), hud.TEXT_BRIGHT, name="title")
        self._title_dot = _label(hud.mono_font(11), hud.TEXT_DIM)
        self._title_dot.setText(DOT)
        self.subtitle = _label(_caps(hud.mono_font(11, 400, 0.08)), hud.TEXT_MUTED, wrap=True,
                               name="subtitle")
        self.stale = _label(hud.mono_font(11, 400, 0.04), hud.AMBER, wrap=True, name="stale")
        self.stale.hide()
        self.text = _Transcript(self._style)
        self.transcript_panel = hud.MainPanel()
        self.transcript_panel.steps.installEventFilter(self)   # re-fit the chips when it resizes
        self._step_meter = hud.StepStrip(self)                # measures chips, never shown
        self._step_meter.setVisible(False)

    def _make_buttons(self) -> None:
        self.play_button = hud.HudButton("", hud.PRIMARY)
        # "Pause" / "Play" / "Replay" / "Retry" without the row jumping around.
        widths = []
        for text in ("Play", "Replay", "Retry", "Pause"):
            self.play_button.setText(text)
            widths.append(self.play_button.sizeHint().width())
        self.play_button.setMinimumWidth(max(widths))
        self.skip_button = hud.HudButton("Skip section")
        self.everything_button = hud.HudButton("Read everything")
        self.notion_button = hud.HudButton("Open in Notion")
        self.done_button = hud.HudButton("Done")
        self.play_button.clicked.connect(lambda: self.playPause.emit())
        self.skip_button.clicked.connect(lambda: self.skip.emit())
        self.everything_button.clicked.connect(lambda: self.readEverything.emit())
        self.notion_button.clicked.connect(lambda: self.openNotion.emit())
        self.done_button.clicked.connect(lambda: self.done.emit())

    def _make_side_column(self) -> None:
        self.approvals_panel = hud.ChamferPanel("Needs your OK", "0 pending", variant=hud.PANEL_AMBER)
        _caps_meta(self.approvals_panel)   # "2 PENDING", as on the artboard
        self.approvals = hud.ActionList(max_height=_APPROVALS_MAX_PX)
        self.approvals_panel.body_layout.addWidget(self.approvals)
        self.activity_panel = hud.ChamferPanel("Activity", "live", border_alpha=0.18, spacing=8)
        self.activity = hud.ActivityLog()
        self.activity_panel.body_layout.addWidget(self.activity, 1)

    def _build_layout(self) -> None:
        self._left = QWidget()
        left = QVBoxLayout(self._left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addWidget(self.status_panel)
        left.addWidget(self.agenda_panel, 1)
        self._right = QWidget()
        right = QVBoxLayout(self._right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        right.addWidget(self.approvals_panel)
        right.addWidget(self.activity_panel, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._left)
        layout.addWidget(self._build_center(), 1)
        layout.addWidget(self._right)
        self._apply_tier(READING_SIZE.width())

    def _build_center(self) -> QWidget:
        voice = QVBoxLayout()
        voice.setSpacing(8)
        voice.addStretch(1)
        voice.addWidget(self.state_label)
        voice.addWidget(self.speech)
        voice.addStretch(1)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(14, 4, 14, 0)
        top_layout.setSpacing(20)
        top_layout.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignVCenter)
        top_layout.addLayout(voice, 1)
        # The controls wrap onto a second row in a narrow column; Done stays bottom-right.
        # Margins leave room for the Pause glow, which is drawn outside the button.
        flow_holder = QWidget()
        flow = hud.FlowLayout(flow_holder, 6, 6)
        flow.setContentsMargins(14, 8, 0, 10)
        for button in (self.play_button, self.skip_button, self.everything_button, self.notion_button):
            flow.addWidget(button)
        done_column = QVBoxLayout()
        done_column.setContentsMargins(0, 8, 0, 10)
        done_column.addStretch(1)
        done_column.addWidget(self.done_button)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 14, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(flow_holder, 1)
        controls_layout.addLayout(done_column)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._title_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.subtitle, 1, Qt.AlignmentFlag.AlignVCenter)
        doc_header = QWidget()
        doc_layout = QVBoxLayout(doc_header)
        doc_layout.setContentsMargins(_DOC_MARGIN_PX, 10, _DOC_MARGIN_PX, 2)
        doc_layout.setSpacing(4)
        doc_layout.addLayout(title_row)
        doc_layout.addWidget(self.stale)
        doc_layout.addSpacing(4)
        doc_layout.addWidget(_DashedRule())
        self.transcript_panel.strip_layout.addWidget(self.sections_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.transcript_panel.body_layout.addWidget(doc_header)
        self.transcript_panel.body_layout.addWidget(self.text, 1)
        center = QWidget()
        column = QVBoxLayout(center)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(top)
        column.addWidget(controls)
        column.addWidget(self.transcript_panel, 1)
        return center

    def _apply_tier(self, width: int) -> None:
        tier = next(tier for tier in _TIERS if width >= tier.min_width)
        if tier == self._tier:
            return
        self._tier = tier
        self._left.setFixedWidth(tier.left)
        self._right.setFixedWidth(tier.right)
        self.orb.setFixedSize(tier.orb, tier.orb)
        # A narrow transcript strip still needs room for a step chip beside SECTIONS.
        side = 20 if tier.orb >= 168 else 12
        self.transcript_panel.strip_layout.setContentsMargins(side, 12, side, 12)

    # ---- header, status and controls ---------------------------------------

    def set_header(self, title: str, subtitle: str, stale_text: str = "") -> None:
        """Briefing title and "Updated ..." above the transcript; the stale warning under them."""
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self._title_dot.setVisible(bool(subtitle))
        self.stale.setText(stale_text)
        self.stale.setVisible(bool(stale_text))
        self._speech_fallback = title

    def set_status(self, text: str, is_error: bool = False) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        _set_status_style(self.status, is_error, hud.TEXT_SOFT)

    def set_controls(self, primary: str, primary_enabled: bool, can_skip: bool) -> None:
        self.play_button.setText(primary)
        self.play_button.setEnabled(primary_enabled)
        self.skip_button.setEnabled(can_skip)

    def set_read_everything(self, enabled: bool, tooltip: str) -> None:
        self.everything_button.setEnabled(enabled)
        self.everything_button.setToolTip(tooltip)

    def set_speech(self, text: str) -> None:
        """The line being spoken (Sora Light 20 px); "" shows the briefing title."""
        self.speech.setText(text or self._speech_fallback)

    def set_activity_state(self, state: str, live: bool) -> None:
        """Orb and state label (SPEAKING, STANDBY, WORKING) and the transcript's LIVE marker."""
        self.orb.set_state(state)
        if state != self.state_label.state():
            self.state_label.set_state(state)
        self.transcript_panel.live.set_live(live)

    def set_sections(self, rows: Sequence[hud.SectionRowInfo], meta: str, current: int | None = None) -> None:
        """Section rows; the list scrolls to ``current`` when it changes (not on every update)."""
        rows = tuple(rows)
        if rows != self._rows:
            self._rows = rows
            self.sections.set_rows(rows)
        self.sections_panel.set_meta(meta)
        self.sections_button.set_meta(str(len(rows)) if rows else "")
        self.sections_button.setEnabled(bool(rows))
        if not rows:
            self.sections_popover.hide()
        if current is not None and current != self._shown_section:
            self.sections.ensure_visible(current)
        self._shown_section = current

    # ---- sections popover ------------------------------------------------------

    def open_sections(self) -> None:
        """Open the SECTIONS list under its button, with the current section in view."""
        if not self.sections_button.isChecked():
            self.sections_button.setChecked(True)   # toggled() comes back here
            return
        if self.sections_popover.isVisible() or not self._rows:
            return
        panel = self.sections_panel
        margins = panel.body_layout.contentsMargins()
        chrome = (margins.top() + margins.bottom() + panel.title_label.sizeHint().height()
                  + panel.body_layout.spacing() + 4)
        # A drop-down over the transcript: it ends at the bottom of this view and scrolls beyond.
        below = self.mapToGlobal(QPoint(0, self.height())).y() - self.sections_button.mapToGlobal(
            QPoint(0, self.sections_button.height())).y() - 2 * hud.Popover.MARGIN
        height = min(self.sections.content_height() + chrome, max(_SECTIONS_POPOVER_MIN_PX, below))
        self.sections_popover.open_below(self.sections_button, _SECTIONS_POPOVER_WIDTH, height)
        target = self._shown_section if self._shown_section is not None else 0
        self.sections.ensure_visible(target)
        if 0 <= target < self.sections.row_count() and self.sections.row(target).isEnabled():
            self.sections.row(target).setFocus(Qt.FocusReason.PopupFocusReason)

    def close_sections(self) -> None:
        self.sections_popover.hide()

    def _on_sections_toggled(self, checked: bool) -> None:
        if checked:
            self.open_sections()
        else:
            self.sections_popover.hide()

    def _on_sections_closed(self) -> None:
        self.sections_button.setChecked(False)

    def _on_section_row(self, index: int) -> None:
        self.sections_popover.hide()
        self.sectionClicked.emit(index)

    def set_steps(self, steps: Sequence[tuple[str, str]]) -> None:
        """Section chips above the transcript: as many as fit, always with the active one."""
        self._steps = list(steps)
        self._fit_steps()

    def _fit_steps(self) -> None:
        steps = list(self._steps)
        room = self.transcript_panel.steps.width()
        states = [state for _, state in steps]
        # Keep the active chip; with none (finished), the last one that was read.
        if hud.STEP_ACTIVE in states:
            active = states.index(hud.STEP_ACTIVE)
        else:
            active = max((i for i, state in enumerate(states) if state == hud.STEP_DONE), default=0)
        while len(steps) > 1:
            self._step_meter.set_steps(steps)
            if self._step_meter.sizeHint().width() <= room:
                break
            if active > len(steps) - 1 - active:   # drop the end farther from the active chip
                steps.pop(0)
                active -= 1
            else:
                steps.pop()
        if steps != self.transcript_panel.steps.steps():
            self.transcript_panel.steps.set_steps(steps)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.transcript_panel.steps and event.type() == QEvent.Type.Resize:
            self._fit_steps()
        return super().eventFilter(watched, event)

    def set_pending(self, pending: int, total: int) -> None:
        """Pending proposals: the panel's meta and the STATUS bar."""
        self.approvals_panel.set_meta(f"{pending} pending")
        self.approvals_bar.set_value(str(pending), pending / total if total else 0.0)

    # ---- approval cards ------------------------------------------------------

    def clear_action_cards(self) -> None:
        self.approvals.clear()

    def add_action_card(self, action_id: str, kind: str, title: str, detail: str,
                        actionable: bool, **options: Any) -> hud.ActionCard:
        """A NEEDS YOUR OK card; ``options`` are ActionCard's keyword arguments
        (approve_text, body, title_lines, open_text, copy_text, edit_text, sign_in_text,
        check_line)."""
        card = hud.ActionCard(action_id, kind, title, detail, actionable=actionable, **options)
        card.approveClicked.connect(self.approveClicked)
        card.denyClicked.connect(self.denyClicked)
        card.lockedClicked.connect(self.lockedClicked)
        card.openClicked.connect(self.openLink)
        card.sourceClicked.connect(self.openSource)
        card.copyClicked.connect(self.copyText)
        card.undoClicked.connect(self.undoAction)
        card.editClicked.connect(self.editAction)
        card.signInClicked.connect(self.signInAccount)
        return self.approvals.add_card(card)

    def action_card(self, action_id: str) -> hud.ActionCard | None:
        return self.approvals.card(action_id)

    # ---- panel content ------------------------------------------------------

    def show_message(self, text: str, is_error: bool = False) -> None:
        """Replace the panel with one line (loading text or an error)."""
        self._block_numbers = {}
        self._current = (-1, -1)
        self.text.set_row(-1)
        self.text.setExtraSelections([])
        document = self.text.document()
        document.clear()
        char_format = QTextCharFormat()
        char_format.setFont(self._style.body)
        char_format.setForeground(QColor(hud.AMBER if is_error else hud.TEXT_SOFT))
        block_format = QTextBlockFormat()
        block_format.setLineHeight(self._style.body_line, _PROPORTIONAL)
        cursor = QTextCursor(document)
        cursor.setBlockFormat(block_format)
        cursor.insertText(text, char_format)

    def set_script(self, script: Script, include_ignored: bool) -> None:
        """Render one text block per script item; keeps the scroll position and highlight."""
        bar = self.text.verticalScrollBar()
        scroll = bar.value()
        document = self.text.document()
        document.clear()
        cursor = QTextCursor(document)
        self._block_numbers = {}
        for section_index, section in enumerate(script.sections):
            dim = section.ignored and not include_ignored
            amber = section.key == ACTIONS_KEY
            for item_index, item in enumerate(section.items):
                first = not self._block_numbers
                block_format, char_format, text = _item_format(item, dim, first, self._style, amber)
                if first:
                    cursor.setBlockFormat(block_format)
                    cursor.setBlockCharFormat(char_format)
                else:
                    cursor.insertBlock(block_format, char_format)
                _insert_item_text(cursor, item, text, char_format, dim)
                self._block_numbers[(section_index, item_index)] = cursor.blockNumber()
        # The browser's own cursor is left at the end by the inserts, and showing the
        # panel scrolls to it. Move it to the start first: setTextCursor scrolls too,
        # so the saved position (kept when re-rendering) is restored after it.
        self.text.setTextCursor(QTextCursor(document))
        bar.setValue(scroll)
        self._apply_highlight(scroll_to=False)

    # ---- highlight and auto-scroll -----------------------------------------

    def highlight(self, section_index: int, item_index: int) -> None:
        """Highlight the spoken item; (-1, -1) clears the highlight."""
        self._current = (section_index, item_index)
        self._apply_highlight(scroll_to=True)

    def highlighted_block(self) -> int:
        """Block number of the agenda-row highlight (-1 when none)."""
        return self.text.row()

    def _apply_highlight(self, *, scroll_to: bool) -> None:
        number = self._block_numbers.get(self._current)
        block = self.text.document().findBlockByNumber(number) if number is not None else None
        if block is None or not block.isValid() or not block.text():
            self.text.set_row(-1)
            self.text.setExtraSelections([])
            return
        self.text.document().documentLayout().blockBoundingRect(block)   # make sure it is laid out
        self.text.setExtraSelections(_highlight_selections(block))
        self.text.set_row(number)
        if scroll_to:
            self._auto_scroll(block)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_tier(event.size().width())
        self._apply_highlight(scroll_to=False)   # line wrapping changed

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._apply_highlight(scroll_to=True)    # an item highlighted while hidden comes into view

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self.sections_popover.hide()

    def _auto_scroll(self, block: QTextBlock) -> None:
        if time.monotonic() - self._user_scrolled_at < _MANUAL_SCROLL_HOLD_S:
            return
        bar = self.text.verticalScrollBar()
        top = self.text.document().documentLayout().blockBoundingRect(block).top()
        target = int(top - self.text.viewport().height() * _SCROLL_ANCHOR)
        target = max(bar.minimum(), min(bar.maximum(), target))
        if abs(target - bar.value()) < 4:
            return
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def _on_user_scroll(self, _action: int) -> None:
        # actionTriggered fires for wheel, drag, clicks and keys, never for setValue().
        self._user_scrolled_at = time.monotonic()
        self._scroll_animation.stop()


def _item_format(item: ScriptItem, dim: bool, first: bool, style: _TranscriptStyle,
                 amber: bool) -> tuple[QTextBlockFormat, QTextCharFormat, str]:
    """Block format, character format and text for one script item."""
    block_format = QTextBlockFormat()
    block_format.setLineHeight(style.body_line, _PROPORTIONAL)
    block_format.setBottomMargin(_ROW_GAP_PX)
    char_format = QTextCharFormat()
    char_format.setFont(style.body)
    text = item.display
    if not text:   # a pause: a small visual gap, never a text line
        block_format.setLineHeight(_PAUSE_GAP_PX, _FIXED_HEIGHT)
        block_format.setBottomMargin(0)
        return block_format, char_format, ""
    color = _ITEM_COLORS.get(item.kind, hud.TEXT_BODY)
    if item.kind == ITEM_HEADING:
        char_format.setFont(style.heading)
        block_format.setLineHeight(style.heading_line, _PROPORTIONAL)
        block_format.setTopMargin(0 if first else _HEADING_TOP_PX)
        block_format.setBottomMargin(6)
        color = hud.AMBER if amber else hud.ACCENT
        text += _NOT_READ_SUFFIX if dim else ""
    elif item.kind == ITEM_SUBHEADING:
        char_format.setFont(style.subheading)
        block_format.setTopMargin(6)
        block_format.setIndent(item.depth)
    elif item.kind == ITEM_ENTRY:
        block_format.setIndent(item.depth + 1)
        text = _entry_text(text)
        _hang_first_line(block_format, text, style.body_metrics)
    else:
        block_format.setIndent(item.depth)
    char_format.setForeground(QColor(hud.TEXT_DIM if dim else color))
    return block_format, char_format, text


def _insert_item_text(cursor: QTextCursor, item: ScriptItem, text: str, char_format: QTextCharFormat,
                      dim: bool) -> None:
    """Insert ``text``; a list entry's bullet is drawn in a quiet accent."""
    if item.kind == ITEM_ENTRY and text.startswith(_BULLET):
        bullet_format = QTextCharFormat(char_format)
        bullet_format.setForeground(hud.rgba(hud.TEXT_DIM if dim else hud.ACCENT, 0.7))
        cursor.insertText(_BULLET, bullet_format)
        cursor.insertText(text[len(_BULLET):], char_format)
    else:
        cursor.insertText(text, char_format)


def _entry_text(display: str) -> str:
    return display if _LIST_PREFIX_RE.match(display) else _BULLET + display


def _hang_first_line(block_format: QTextBlockFormat, text: str, metrics: QFontMetricsF) -> None:
    """Wrapped lines of a list entry line up with its text, not with its bullet or number."""
    match = _LIST_PREFIX_RE.match(text)
    prefix = match.group(0) if match else _BULLET
    width = metrics.horizontalAdvance(prefix)
    block_format.setLeftMargin(width)
    block_format.setTextIndent(-width)


def _highlight_selections(block: QTextBlock) -> list[QTextEdit.ExtraSelection]:
    """Brighter text for the whole highlighted block (the row itself is painted by _Transcript)."""
    cursor = QTextCursor(block)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    char_format = QTextCharFormat()
    char_format.setForeground(QColor(hud.TEXT_BRIGHT))
    selection = QTextEdit.ExtraSelection()
    selection.cursor = cursor
    selection.format = char_format
    return [selection]


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

PROMPT_SIZE = QSize(560, 300)
READING_SIZE = QSize(1120, 700)
READING_MIN_SIZE = QSize(900, 600)
SCREEN_MARGIN = 24
_PROMPT_MARGINS = (10, 4, 10, 10)
_READING_MARGINS = (12, 4, 12, 12)


class _PageSwitch(QWidget):
    """Shows one page at a time and sizes to that page only.

    Not a QStackedWidget: its layout reports the largest minimum and
    height-for-width of ALL pages, so the hidden reading view (whose wrapped
    text grows tall at the prompt's width) would prop the fixed-size prompt
    window open. Real Windows applies height-for-width to top-level windows
    (offscreen tests never do), which left the prompt 668 px tall with its
    buttons cut off. A hidden widget takes no part in layout, so hiding the
    other pages keeps every size hint to the current page.
    """

    currentChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._pages: list[QWidget] = []
        self._current: QWidget | None = None

    def addWidget(self, page: QWidget) -> None:  # noqa: N802 - mirrors QStackedWidget
        self._layout.addWidget(page)
        self._pages.append(page)
        if self._current is None:
            self._current = page
        else:
            page.hide()

    def currentWidget(self) -> QWidget | None:  # noqa: N802 - mirrors QStackedWidget
        return self._current

    def widget(self, index: int) -> QWidget | None:
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def setCurrentWidget(self, page: QWidget) -> None:  # noqa: N802 - mirrors QStackedWidget
        changed = page is not self._current
        for other in self._pages:
            if other is not page:
                other.hide()
        self._current = page
        page.show()
        self.updateGeometry()
        if changed:
            self.currentChanged.emit(self._pages.index(page))


class BriefingWindow(hud.HudWindowFrame):
    """Frameless, always-on-top HUD window holding the prompt and reading views.

    The header bar is the title bar (drag to move); its close button and
    Alt+F4 go through ``closeRequested`` so the controller decides what
    closing means.
    """

    closeRequested = Signal()
    visibilityChanged = Signal()   # shown, hidden, minimized or restored

    def __init__(self, short_minutes: int, long_minutes: int) -> None:
        super().__init__(margins=_PROMPT_MARGINS)
        self.setObjectName("BriefingWindow")
        self.setWindowTitle("Briefing")
        self.setWindowIcon(app_icon())
        assert self.header is not None
        self.header.set_subtitle(f"briefing {DOT} desktop")
        for name in ("notion", "voice", "calendar"):
            self.header.set_service(name, hud.STATUS_OFF)
        self.prompt = PromptView(short_minutes, long_minutes)
        self.reading = ReadingView()
        self._stack = _PageSwitch()
        self._stack.addWidget(self.prompt)
        self._stack.addWidget(self.reading)
        self.body_layout.addWidget(self._stack)
        self.set_glow_anchor(self.prompt.orb)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        event.ignore()   # the controller decides what closing means
        self.closeRequested.emit()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.visibilityChanged.emit()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self.visibilityChanged.emit()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.visibilityChanged.emit()

    def event(self, event: QEvent) -> bool:
        # A Tab-focused button keeps Space (pressed, then clicked on release, as
        # everywhere else); the reading view's Space shortcut only acts when no
        # button has focus.
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(self.focusWidget(), QAbstractButton)):
            event.accept()
            return True
        return super().event(event)

    def is_reading_view(self) -> bool:
        return self._stack.currentWidget() is self.reading

    def set_hour24(self, hour24: bool) -> None:
        """The header clock and the activity times: "13:05" (True) or "1:05 PM" (False)."""
        assert self.header is not None
        self.header.set_hour24(hour24)
        self.reading.activity.set_hour24(hour24)

    # ---- views and sizes ------------------------------------------------------

    def show_prompt_view(self) -> None:
        self._stack.setCurrentWidget(self.prompt)
        self.layout().setContentsMargins(*_PROMPT_MARGINS)
        self.set_resizable(False)
        self.set_glow_anchor(self.prompt.orb)
        self.fit_prompt(force=True)

    def fit_prompt(self, *, force: bool = False) -> None:
        """Fixed width; tall enough for the current text, at least the nominal height."""
        if self.is_reading_view():
            return
        left, top, right, bottom = _PROMPT_MARGINS
        body = self.prompt.heightForWidth(PROMPT_SIZE.width() - left - right)
        header = self.header.height() if self.header is not None else 0
        height = max(PROMPT_SIZE.height(), top + header + body + bottom)
        size = QSize(PROMPT_SIZE.width(), height)
        if force or size != self.size() or self.maximumSize() != size:
            self._apply_size(size)

    def show_reading_view(self) -> None:
        self._stack.setCurrentWidget(self.reading)
        self.layout().setContentsMargins(*_READING_MARGINS)
        self.set_resizable(True)
        self.set_glow_anchor(self.reading.orb)
        room = self._screen_room()
        minimum = READING_MIN_SIZE.boundedTo(room)   # a small screen gets a smaller window
        self._apply_size(READING_SIZE.boundedTo(room).expandedTo(minimum), minimum)
        self.reading.setFocus(Qt.FocusReason.OtherFocusReason)

    def _screen_room(self) -> QSize:
        area = _screen_area(self.geometry().center() if self.isVisible() else QCursor.pos())
        return area.size() - QSize(2 * SCREEN_MARGIN, 2 * SCREEN_MARGIN)

    def _apply_size(self, size: QSize, minimum: QSize | None = None) -> None:
        """Resize (fixed when ``minimum`` is None), keeping a visible window's bottom-right corner."""
        corner = self.geometry().bottomRight() if self.isVisible() else None
        self.setMinimumSize(0, 0)
        self.setMaximumSize(_QWIDGETSIZE_MAX, _QWIDGETSIZE_MAX)
        if minimum is None:
            self.setFixedSize(size)
        else:
            self.setMinimumSize(minimum)
            self.resize(size)
        if corner is not None:
            self.move(corner.x() - self.width() + 1, corner.y() - self.height() + 1)
            self.keep_on_screen()

    # ---- placement --------------------------------------------------------------

    def place(self) -> None:
        """Bottom-right of the available area of the screen under the cursor."""
        area = _screen_area(QCursor.pos())
        x = area.x() + area.width() - self.width() - SCREEN_MARGIN
        y = area.y() + area.height() - self.height() - SCREEN_MARGIN
        self.move(max(area.x(), x), max(area.y(), y))

    def keep_on_screen(self) -> None:
        """Move the window fully inside the available area of its screen."""
        frame = QRect(self.pos(), self.size())
        area = _screen_area(frame.center())
        x = max(area.x(), min(frame.x(), area.x() + area.width() - frame.width()))
        y = max(area.y(), min(frame.y(), area.y() + area.height() - frame.height()))
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)


# --------------------------------------------------------------------------
# Controller
# --------------------------------------------------------------------------

STATE_IDLE = "idle"
STATE_PROMPT = "prompt"
STATE_SNOOZED = "snoozed"
STATE_READING = "reading"
STATE_QUITTING = "quitting"

LOADING_TEXT = "Fetching your briefing from Notion..."
ACTIONS_FILE = "actions.json"
CALENDAR_SETUP_NOTE = "Google Calendar is not set up yet - see README step 8"
_SESSION_MAX_AGE_S = 12 * 3600
_AUDIO_CLEANUP_WAIT_S = 15.0
_AUDIO_RELEASE_WAIT_S = 3.0
_NOTE_DURATION_MS = 8000
# Read now on a briefing that is not fresh asks Notion once more, but waits at
# most this long before reading the copy it already has.
_RECHECK_WAIT_MS = 5000
_SAME_RUN_WINDOW = timedelta(hours=12)   # a relaunch this soon is the same run, not the next day's
_WARMUP_INDEX = -1                       # TTS job that only fills the cache (noted intro)
_PRIMARY_LABELS = {PLAYING: "Pause", WAITING: "Pause", PAUSED: "Play", FINISHED: "Replay"}
_STATUS_LABELS = {PLAYING: "Playing", PAUSED: "Paused", FINISHED: "Finished"}
_ORB_FOR_PLAYER = {PLAYING: hud.ORB_SPEAKING, WAITING: hud.ORB_WORKING}
_STEP_WINDOW = 5                         # section chips shown above the transcript
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Action worker stages (the card shows WORKING / WAITING FOR GOOGLE SIGN-IN), and the undo
# countdown before an RSVP, Move or Cancel goes to Google.
_STAGE_WORKING = STAGE_WORKING
_STAGE_SIGNIN = STAGE_SIGNIN
_STAGE_SIGNED_IN = STAGE_SIGNED_IN
_STAGE_COUNTDOWN = "countdown"           # "SENDING IN 9 S" + Undo: nothing has been sent yet
_SIGN_IN_CHECK = "sign-in check"         # action worker job: report which accounts are signed in
_CARD_FOR_STATUS = {STATUS_CREATED: hud.CARD_ADDED, STATUS_EXISTS: hud.CARD_EXISTS,
                    STATUS_DENIED: hud.CARD_DENIED, STATUS_FAILED: hud.CARD_FAILED,
                    STATUS_DONE: hud.CARD_DONE, STATUS_SENT: hud.CARD_SENT,
                    STATUS_UNKNOWN: hud.CARD_UNKNOWN, STATUS_RUNNING: hud.CARD_WORKING}
# The undo countdown ([actions] undo_seconds): how often the card's "SENDING IN n S" is updated.
_COUNTDOWN_TICK_MS = 200
# At exit, a change already on its way to Google is waited for this long (the window is hidden).
_QUIT_SEND_WAIT_S = 5.0
SIGN_IN_LINK_TEXT = "Sign in"
EDIT_LINK_TEXT = "Edit"
EDITED_MARK = f" {DOT} edited"
_EVENT_LINK_TEXT = "Open event"          # the result link of an answered, moved or cancelled event
UNKNOWN_CARD_TEXT = "check the calendar before retrying"   # after "UNKNOWN: " on the card
CHECK_SOON_TEXT = "Checking the event with Google..."
CHECK_LATER_TEXT = "Not checked with Google yet"
CHECK_FIRST_NOTE = "Checking the event with Google first - click {button} again once it shows above"
SIGN_IN_FIRST_NOTE = "Finish the Google sign-in in your browser, then check the event above"
SIGNED_IN_NOTE = "Signed in - check the event above, then click {button} again"
NOT_READY_NOTE = "Nothing was sent - see the line above"
# The first Approve on a card whose event Google shows with another title or time than the line
# only says so (the line above shows Google's); the next click goes ahead.
MISMATCH_NOTE = "Google's event has {what} than the briefing - check the line above, then click {button} again"
_MISMATCH_WHAT = {"title": "another title", "time": "another time", "title+time": "another title and time"}
# After a change Google's view is what the change made (not the check from before it).
AFTER_PREFIX = "Google now: "
_ANSWERED = {"yes": "you accepted", "no": "you declined", "maybe": "you said maybe"}
# The end of gcal's unknown-outcome message: the card's UNKNOWN line already says it.
_CHECK_BEFORE_RETRY_RE = re.compile(r"\s*[-;:,]\s*check (?:the calendar )?before retrying\.?\s*$", re.IGNORECASE)
# The check line's short form of an account's sign-in problem (the whole message is its tooltip).
_PROBLEM_LINES = {
    PROBLEM_BLOCKED: "Sign-in blocked by the {alias} account's administrator",
    PROBLEM_EXPIRED: "The {alias} account's Google sign-in expired - Sign in again",
    PROBLEM_DENIED: "Google sign-in for the {alias} account was cancelled or denied",
    PROBLEM_SCOPE: "The {alias} account did not allow Calendar access - Sign in again",
    PROBLEM_TIMEOUT: "Google sign-in for the {alias} account was not finished",
}
_PROBLEM_LINE_OTHER = "Google sign-in for the {alias} account failed - Sign in to try again"
_VERBS = {RSVP: "answer", MOVE: "move", CANCEL: "cancel"}
# Sign-in problems of an account (google_auth PROBLEM_*): the card offers Sign in again.
_SIGN_IN_PROBLEMS = (PROBLEM_SIGNED_OUT, PROBLEM_EXPIRED, PROBLEM_BLOCKED, PROBLEM_DENIED, PROBLEM_SCOPE,
                     PROBLEM_TIMEOUT)
# A second click on the same card's Open this soon after the first opens nothing (no double tabs).
_SOURCE_OPEN_GUARD_S = 1.0
# The link next to BLOCK ADDED: the tools row may already have an "Open" (the Todo's own link).
_BLOCK_LINK_TEXT = "Open event"
# A key=value proposal's title (a subject can be 250 characters) in an ACTIVITY row; the card's
# tooltip has all of it.
_ACTIVITY_TITLE_CHARS = 80
# An Approve / Deny click this soon after the previous one (on any card) is
# ignored: a double click, or repeated clicks while the cards are updating,
# must never decide a second proposal.
_DECISION_CLICK_GUARD_S = 1.0
# While an approval (or a Connect sign-in) runs, the other cards are locked.
LOCK_TIP = "Finishing the previous approval..."
SIGN_IN_LOCK_TIP = "Finishing the Google sign-in..."
_LOCKED_MESSAGE = "Ignored an approval click: the previous approval is still running"
_SIGN_IN_LOCKED_MESSAGE = "Ignored an approval click: the Google sign-in is still running"

# The TODAY / TOMORROW agenda and DEADLINES (left column of the reading screen).
_AGENDA_TICK_MS = 60_000            # re-check "now" / "in 20 min" while the reading screen is visible
_AGENDA_REFRESH_S = 600.0           # ask Google again after this long (only while visible)
_AGENDA_INIT = "init"
_AGENDA_LOADING = "loading"
_AGENDA_OK = "ok"
_AGENDA_OFF = "off"                 # [calendar] enabled = false
_AGENDA_SETUP = "setup"             # no client secret file or Google packages
_AGENDA_SIGNED_OUT = "signedout"
_AGENDA_ERROR = "error"
AGENDA_OFF_TEXT = "Google Calendar is turned off (config.toml)"
AGENDA_SETUP_TEXT = "Google Calendar is not set up (README step 8)"
AGENDA_CONNECT_TEXT = "Connect Google Calendar to see your day"
AGENDA_CONNECT_LINK = "Connect"
AGENDA_SIGN_IN_TEXT = "Waiting for Google sign-in..."
AGENDA_LOADING_TEXT = "Loading..."
AGENDA_ERROR_TEXT = "Couldn't load the calendar"
AGENDA_EMPTY_TEXT = "Nothing on the calendar"
# Worker answers for the agenda ("" = the events came back).
_PROBLEM_SETUP = "setup"
_PROBLEM_SIGNED_OUT = "signedout"
_PROBLEM_ERROR = "error"
# Calendar worker job and stages for the agenda's Connect link.
_CONNECT_JOB = "connect"
_CONNECT_SIGN_IN = "signin"         # the browser sign-in is open
_CONNECT_SIGNED_IN = "signedin"     # signed in just now
_CONNECT_READY = "ready"            # was signed in already
_CONNECT_FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class _AgendaJob:
    """One list_events call on the action worker (the range covers the agenda and the deadlines)."""

    request_id: int
    start: datetime
    end: datetime
    calendar_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class _ExecJob:
    """Carry out one approved proposal (executor.run_action). ``writes_running``: an RSVP, Move or
    Cancel whose undo countdown ran out; "running" is saved right before its call to Google and
    the result right after, by the worker."""

    action: ProposedAction
    writes_running: bool


@dataclasses.dataclass(frozen=True)
class _PeekJob:
    """Read the events of these cards for their check lines (never signs in)."""

    request_id: int
    actions: tuple[ProposedAction, ...]


@dataclasses.dataclass(frozen=True)
class _ConnectJob:
    """The browser sign-in of one account (the agenda's Connect, a card's Sign in or Approve)."""

    alias: str


class _Bridge(QObject):
    """Signals emitted by worker threads; connected queued, so slots run on the GUI thread."""

    fetchAttempt = Signal(int, object, object, int)   # fetch_id, Briefing | None, NotionError | None, attempt
    fetchDone = Signal(int, object)                   # fetch_id, PollResult
    ttsResult = Signal(int, int, object, object)      # generation, section index, SectionAudio | None, str | None
    calendarStatus = Signal(bool)                     # the personal account is signed in to Google Calendar
    accountStatus = Signal(object)                    # {alias: (signed in, PROBLEM_* or "", message)}
    actionProgress = Signal(str, str)                 # action id, _STAGE_*
    actionResult = Signal(str, object, object, str)   # action id, ExecResult | None, ExecError | None, status
    eventPeek = Signal(int, object)                   # request id, {action id: (EventDetails | None, error | None)}
    agendaResult = Signal(int, object, str, str)      # request id, events | None, _PROBLEM_* or "", message
    accountConnect = Signal(str, str, str, str)       # alias, _CONNECT_* stage, failure message, PROBLEM_*


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _emit_from_worker(bridge: _Bridge, signal_name: str, stop: threading.Event, *args: Any) -> None:
    """Emit a bridge signal from a worker thread unless stopped or the bridge is already gone."""
    if stop.is_set():
        return
    try:
        getattr(bridge, signal_name).emit(*args)
    except RuntimeError:
        pass   # the bridge was deleted during shutdown


def _run_fetch(bridge: _Bridge, fetch_id: int, stop: threading.Event,
               fetch: Callable[[], Briefing], poll_options: dict[str, Any]) -> None:
    """Body of a fetch thread: poll Notion and report only through the bridge."""
    def on_attempt(briefing: Briefing | None, _freshness: Any, error: NotionError | None,
                   attempt: int) -> None:
        _emit_from_worker(bridge, "fetchAttempt", stop, fetch_id, briefing, error, attempt)

    try:
        result = poll_for_briefing(fetch, wait=stop.wait, on_attempt=on_attempt, **poll_options)
    except Exception as exc:  # noqa: BLE001 - poll_for_briefing guards fetch(); this is a last resort
        logger.exception("Fetching the briefing failed unexpectedly")
        error = NotionError(f"Unexpected error while fetching the briefing ({type(exc).__name__}).")
        result = PollResult(briefing=None, freshness=None, error=error, attempts=0,
                            timed_out=False, stopped=False)
    _emit_from_worker(bridge, "fetchDone", stop, fetch_id, result)


class _ActionWorker:
    """Runs Google calls one at a time on a daemon thread named "calendar".

    Jobs: a sign-in check (the header chip and the cards' Sign in links), an
    approved proposal (``_ExecJob`` through executor.run_action: a Calendar
    event or a Todo's block signs in first when needed, as before; an RSVP,
    Move or Cancel has "running" saved right before its one call and the
    result right after, on this thread, so the result is kept even when the
    app quits meanwhile), reading the agenda and the cards' events (never a
    sign-in), and the browser sign-in of one account. One at a time, so
    nothing is read while a sign-in runs. Results only go through the bridge.
    """

    def __init__(self, executor: Executor, calendars: Mapping[str, Any], store: ActionStore,
                 bridge: _Bridge, stop: threading.Event) -> None:
        self._executor = executor
        self._calendars = dict(calendars)
        self._calendar = self._calendars.get(DEFAULT_ACCOUNT)
        self._store = store
        self._bridge = bridge
        self._stop = stop
        self._idle = threading.Event()
        self._idle.set()
        self._taking = threading.Lock()   # taking a job and marking the worker busy happen together
        self._jobs: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="calendar", daemon=True)
        self._thread.start()

    def check_sign_in(self) -> None:
        self._jobs.put(_SIGN_IN_CHECK)

    def approve(self, action: ProposedAction) -> None:
        """A Calendar event or a Todo's block: no countdown, nothing saved before the call."""
        self._jobs.put(_ExecJob(action, writes_running=False))

    def execute(self, action: ProposedAction) -> None:
        """An RSVP, Move or Cancel whose countdown ran out: "running", the one call, the result."""
        self._jobs.put(_ExecJob(action, writes_running=True))

    def list_agenda(self, job: _AgendaJob) -> None:
        self._jobs.put(job)

    def peek(self, job: _PeekJob) -> None:
        self._jobs.put(job)

    def connect(self, alias: str = DEFAULT_ACCOUNT) -> None:
        self._jobs.put(_ConnectJob(alias))

    def stop(self) -> None:
        self._jobs.put(None)

    def wait_idle(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the job that is running (if any) to finish.

        Call it after ``stop`` was set: a job taken from the queue before that is then
        either running (and waited for) or never started.
        """
        with self._taking:
            pass
        return self._idle.wait(timeout)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            with self._taking:
                if job is None or self._stop.is_set():
                    return
                self._idle.clear()
            try:
                self._dispatch(job)
            except Exception as exc:  # noqa: BLE001 - a bug must not end the worker
                logger.error("The action worker failed unexpectedly (%s)", type(exc).__name__)
                logger.debug("Where:\n%s", "".join(traceback.format_tb(exc.__traceback__)))
            finally:
                self._idle.set()

    def _dispatch(self, job: Any) -> None:
        if isinstance(job, _AgendaJob):
            self._list(job)
        elif isinstance(job, _ExecJob):
            self._exec(job)
        elif isinstance(job, _PeekJob):
            self._peek(job)
        elif isinstance(job, _ConnectJob):
            self._connect(job.alias)
        elif job == _SIGN_IN_CHECK:
            self._check_sign_ins()

    def _check_sign_ins(self) -> None:
        """Which accounts have a usable saved sign-in, and why not (files only, no network)."""
        statuses: dict[str, tuple[bool, str, str]] = {}
        for alias, calendar in self._calendars.items():
            problem, message = "", ""
            getter = getattr(calendar, "sign_in_problem", None)
            if callable(getter):
                try:
                    problem, message = getter()
                except Exception as exc:  # noqa: BLE001 - only decides what a card says
                    logger.debug("Could not read the sign-in problem (%s)", type(exc).__name__)
            statuses[alias] = (self._signed_in(calendar), problem or "", message or "")
        self._emit("accountStatus", statuses)
        if self._calendar is not None:
            self._emit("calendarStatus", statuses[DEFAULT_ACCOUNT][0])

    def _list(self, job: _AgendaJob) -> None:
        """Read the events for the agenda; a missing sign-in is reported, never started."""
        problem, message, events = "", "", None
        try:
            if self._calendar is None:
                raise CalendarSetupError("Google Calendar is not set up")
            events = list(self._calendar.list_events(job.start, job.end, job.calendar_ids))
        except CalendarSetupError as exc:
            problem, message = _PROBLEM_SETUP, str(exc)
        except CalendarAuthError as exc:   # CalendarNotSignedIn included
            problem, message = _PROBLEM_SIGNED_OUT, str(exc)
        except CalendarError as exc:   # gcal already logged it, with a safe message
            problem, message = _PROBLEM_ERROR, str(exc) or type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - a bug must not end the worker
            logger.warning("Reading the calendar failed unexpectedly (%s)", type(exc).__name__)
            logger.debug("Where:\n%s", "".join(traceback.format_tb(exc.__traceback__)))
            problem, message = _PROBLEM_ERROR, f"unexpected error ({type(exc).__name__})"
        self._emit("agendaResult", job.request_id, events, problem, message)

    def _peek(self, job: _PeekJob) -> None:
        """Google's own view of each card's event, for its check line (never signs in)."""
        results: dict[str, tuple[Any, BaseException | None]] = {}
        for action in job.actions:
            if self._stop.is_set():
                return
            try:
                results[action.id] = (self._executor.peek(action), None)
            except (CalendarError, ExecError) as exc:   # a safe message (gcal logged a failure)
                results[action.id] = (None, exc)
            except Exception as exc:  # noqa: BLE001 - a bug must not end the worker
                logger.warning("Checking an event failed unexpectedly (%s)", type(exc).__name__)
                logger.debug("Where:\n%s", "".join(traceback.format_tb(exc.__traceback__)))
                results[action.id] = (None, CalendarError(f"unexpected error ({type(exc).__name__})"))
        self._emit("eventPeek", job.request_id, results)

    def _connect(self, alias: str) -> None:
        """One account's browser sign-in, unless a usable sign-in is saved already."""
        calendar = self._calendars.get(alias)
        if calendar is None:
            self._emit("accountConnect", alias, _CONNECT_FAILED, CALENDAR_SETUP_NOTE, "")
            return
        if self._signed_in(calendar):
            self._emit("accountConnect", alias, _CONNECT_READY, "", "")
            return
        self._emit("accountConnect", alias, _CONNECT_SIGN_IN, "", "")
        try:
            calendar.sign_in()
        except CalendarError as exc:   # gcal already logged it, with a safe message
            self._emit("accountConnect", alias, _CONNECT_FAILED, str(exc) or type(exc).__name__,
                       getattr(exc, "problem", "") or "")
            return
        except Exception as exc:  # noqa: BLE001 - a bug must not end the worker
            logger.error("Google sign-in failed unexpectedly (%s)", type(exc).__name__)
            logger.debug("Where:\n%s", "".join(traceback.format_tb(exc.__traceback__)))
            self._emit("accountConnect", alias, _CONNECT_FAILED, f"unexpected error ({type(exc).__name__})", "")
            return
        self._emit("accountConnect", alias, _CONNECT_SIGNED_IN, "", "")

    @staticmethod
    def _signed_in(calendar: Any) -> bool:
        try:
            return bool(calendar.is_signed_in())
        except Exception as exc:  # noqa: BLE001 - only decides whether to sign in first
            logger.debug("Could not check the Google sign-in (%s)", type(exc).__name__)
            return False

    def _exec(self, job: _ExecJob) -> None:
        action = job.action

        def on_stage(stage: str) -> None:
            self._emit("actionProgress", action.id, stage)

        outcome = run_action(self._executor, action, store=self._store if job.writes_running else None,
                             writes_running=job.writes_running, on_stage=on_stage)
        self._emit("actionResult", action.id, outcome.result, outcome.error, outcome.status)

    def _emit(self, signal_name: str, *args: Any) -> None:
        _emit_from_worker(self._bridge, signal_name, self._stop, *args)


_CalendarWorker = _ActionWorker   # its name before it also answered, moved and cancelled events


def _default_calendars(config: Config) -> dict[str, GoogleCalendar]:
    """One GoogleCalendar per account (executor.build_calendars): "personal" (the agenda,
    Calendar proposals, to-do blocks) and every [accounts.<alias>] with the calendar feature.
    The single google_token.json of older versions becomes the personal account's sign-in."""
    return build_calendars(config)


def _default_calendar(config: Config) -> GoogleCalendar:
    """The personal account's calendar."""
    return _default_calendars(config)[DEFAULT_ACCOUNT]


def _agenda_fetch_range(now: datetime, agenda: AgendaConfig) -> tuple[datetime, datetime]:
    """One list_events range for both panels: today 00:00 to the end of the agenda day or the
    deadline horizon, whichever is later."""
    _, start, end = agenda_window(now, agenda.evening_from_hour)
    _, today, _ = agenda_window(now, 24)   # 24: always today's bounds
    return min(start, today), max(end, now + timedelta(days=agenda.deadline_days))


def _agenda_rows(events: Sequence[CalendarEvent], now: datetime, start: datetime,
                 end: datetime, hour24: bool = False) -> list[hud.AgendaRowInfo]:
    return [hud.AgendaRowInfo(row.time_text, row.title, row.meta, row.state)
            for row in event_rows(events_in_window(events, start, end), now, hour24=hour24)]


def _deadline_rows(deadlines: Sequence[Deadline], now: datetime) -> list[hud.DeadlineRowInfo]:
    rows = []
    for item in deadlines:
        text, urgency = due_label(item.due, now)
        rows.append(hud.DeadlineRowInfo(item.title, item.source, text, urgency))
    return rows


def _next_days(days: int) -> str:
    """"next 14 days" / "next day" (the empty DEADLINES line)."""
    return "next day" if days == 1 else f"next {days} days"


def _normalize_run(value: Any) -> str | None:
    text = str(value).strip().upper() if value else ""
    return text if text in ("AM", "PM") else None


def _ready_headline(run: str | None) -> str:
    return f"Your {run} briefing is ready. Hear it now?" if run else "Your briefing is ready. Hear it now?"


def _split_startup_error(error: str) -> tuple[str, str]:
    """(headline, detail): the first sentence of a startup error and the rest.

    "Notion page ID missing. Add BRIEFING_PAGE_ID ..." -> ("Notion page ID missing.", "Add BRIEFING_PAGE_ID ...").
    """
    head, separator, rest = error.partition(". ")
    if not separator:
        return error, ""
    return head + ".", rest.strip()


def _startup_error_chip(error: str) -> str:
    """"Notion: token missing (see README)" for the header's notion chip."""
    headline = _split_startup_error(error)[0].rstrip(".")
    return f"Notion: {headline.removeprefix('Notion ')} (see README)"


def _describe_poll(result: PollResult) -> str:
    if result.stopped:
        return "stopped"
    if result.timed_out:
        return "gave up waiting for a fresh briefing"
    if result.error is not None:
        return f"error: {result.error}"
    return "fresh" if result.freshness is not None and result.freshness.fresh else "done"


def _clock_text(moment: datetime, now: datetime, hour24: bool = False) -> str:
    """"10:04 AM" (or "10:04" with ``hour24``) in ``now``'s time zone, like the HUD clock."""
    try:
        local = moment.astimezone(now.tzinfo) if now.tzinfo is not None else moment
    except (TypeError, ValueError, OverflowError):
        local = moment
    return format_time(local, hour24=hour24)


def _short_updated(header: BriefingHeader, now: datetime, hour24: bool = False, *,
                   compact: bool = False) -> str:
    """"today 10:04 AM", "yesterday 11:31 PM", "Oct 2 9:00 AM" or "not yet" (STATUS telemetry).

    ``hour24``: "today 10:04", "yesterday 23:31", "Oct 2 09:00". ``compact``
    names yesterday by its weekday ("Mon 11:31 PM") and an older day by its
    date alone ("Oct 2"), for a narrow STATUS column.
    """
    updated = header.updated_at
    if updated is None:
        return "not yet"
    try:
        local = updated.astimezone(now.tzinfo) if now.tzinfo is not None else updated
        days = (now.date() - local.date()).days
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    clock = format_time(local, hour24=hour24)
    if days == 0:
        return f"today {clock}"
    if days == 1:
        return f"{_WEEKDAYS[local.weekday()]} {clock}" if compact else f"yesterday {clock}"
    day = f"{_MONTHS[local.month - 1]} {local.day}"
    return day if compact else f"{day} {clock}"


def _activity_title(action: ProposedAction) -> str:
    """``action``'s title for an ACTIVITY row: cut to one line for key=value kinds (Calendar as before)."""
    return _short(action.title, _ACTIVITY_TITLE_CHARS) if action.structured else action.title


def _card_options(action: ProposedAction, view: CardView) -> dict[str, Any]:
    """ActionCard keyword arguments for ``view`` (a CardView of ``action``)."""
    options: dict[str, Any] = {"approve_text": view.approve_text or "Approve", "body": view.body,
                               "open_text": view.open_text, "copy_text": view.copy_text,
                               "title_lines": 2 if action.structured else 0, "deny_text": view.deny_text}
    if view.editable:
        # An RSVP, Move or Cancel: the check line, and Edit in a place that shows Sign in instead
        # while its account is blocked, or Google's own "Open event" when the line has no link.
        options.update(edit_text=EDIT_LINK_TEXT, check_line=view.check, sign_in_text=SIGN_IN_LINK_TEXT,
                       event_text="" if view.open_text else hud.OPEN_EVENT_TEXT)
    return options


@dataclasses.dataclass
class _Countdown:
    """The undo countdown of one approved card; nothing is sent before it runs out."""

    action: ProposedAction       # exactly what will be carried out (the Edit dialog's changes included)
    deadline: float              # time.monotonic() when it runs out
    started: float               # time.monotonic() of the click that started it (Undo's double-click guard)
    shown: int = -1              # the seconds the card shows


def _action_phrase(action: ProposedAction) -> str:
    """"Accept Speaker series", "Move Project sync", "Cancel Study group" (activity rows)."""
    title = _activity_title(action)
    if action.kind == RSVP:
        verb = {"yes": "Accept", "no": "Decline", "maybe": "Answer maybe to"}.get(action.field("answer"), "Answer")
        return f"{verb} {title or 'an invitation'}"
    verb = "Move" if action.kind == MOVE else "Cancel"
    return f"{verb} {title or 'a meeting'}"


def _kind_title(action: ProposedAction) -> str:
    """``action``'s title for an ACTIVITY row, or what it is when the line has no title."""
    title = _activity_title(action)
    if title:
        return title
    return {RSVP: "an invitation", MOVE: "a meeting", CANCEL: "a meeting"}.get(action.kind, action.kind)


def _time_texts(action: ProposedAction, hour24: bool = False) -> tuple[str, str, str]:
    """A Move's new time for the Edit dialog, on the app's clock: ("2026-10-08", "2:00 PM",
    "3:00 PM"), or "14:00" / "15:00" with ``hour24``; empty otherwise."""
    if action.kind != MOVE or action.start is None or action.end is None:
        return "", "", ""

    def clock(moment: datetime) -> str:
        if hour24:
            return moment.strftime("%H:%M")
        return f"{(moment.hour % 12) or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"

    return action.start.date().isoformat(), clock(action.start), clock(action.end)


def _remove_audio_dir_after(worker: threading.Thread | None, audio_dir: Path) -> None:
    """Delete ``audio_dir`` again once the TTS worker has finished the job it is running.

    After stop() the worker still completes its current section, which writes
    into (and may recreate) the folder after shutdown removed it, and the media
    player can keep the last file open for a moment after it stopped. This
    non-daemon thread keeps the process alive for at most
    _AUDIO_CLEANUP_WAIT_S + _AUDIO_RELEASE_WAIT_S so no file outlives the app.
    """
    def run() -> None:
        if worker is not None:
            worker.join(_AUDIO_CLEANUP_WAIT_S)
        deadline = time.monotonic() + _AUDIO_RELEASE_WAIT_S
        shutil.rmtree(audio_dir, ignore_errors=True)
        while audio_dir.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
            shutil.rmtree(audio_dir, ignore_errors=True)
        if audio_dir.exists():
            logger.debug("Could not remove the audio folder %s; the next start cleans it up",
                         audio_dir.name)

    threading.Thread(target=run, name="audio-cleanup", daemon=False).start()


def _remove_old_sessions(root: Path, keep: Path) -> None:
    """Best-effort removal of audio folders left behind by earlier runs (older than 12 h)."""
    try:
        candidates = [path for path in root.glob("session-*") if path.is_dir() and path != keep]
        for path in candidates:
            if time.time() - path.stat().st_mtime > _SESSION_MAX_AGE_S:
                shutil.rmtree(path, ignore_errors=True)
                logger.debug("Removed old audio folder %s", path.name)
    except OSError as exc:
        logger.debug("Could not clean old audio folders: %s", exc)


class AppController(QObject):
    """State machine behind the window: prompt -> (snoozed ->) reading -> quit.

    A launch for a newer run takes the reading screen back to the prompt once
    nothing is playing there. Proposed actions are shown as cards; only an
    explicit Approve creates an event, or Add block a Todo's block (on the
    action worker). Accept / Decline / Maybe, Move and Cancel event answer an
    invitation, move or cancel an event, and only after the card shows
    Google's own view of that event (its check line, read by id) and that view
    allows it; then an undo countdown runs (``[actions] undo_seconds``; Undo
    sends nothing) and only its end queues the one call. "running" is saved
    before that call; a call that may or may not have happened shows UNKNOWN
    with Retry and is never retried by itself. Each account signs in only on a
    click (a card's Sign in or Approve, the agenda's Connect). Edit changes
    such a card in memory, and what the card shows is what is carried out.
    The other proposals are hand-offs: Open (an allowlisted link, only on a
    click), Copy (the drafted text to the clipboard), Done and Deny; nothing
    is sent.
    ``calendar_factory(config)`` builds the calendar clients (tests inject a
    fake): one calendar (the personal account's) or a mapping alias ->
    calendar; it is only called when ``[calendar] enabled`` is true. An Approve
    or Deny within ``_DECISION_CLICK_GUARD_S`` of the previous one is ignored;
    ``click_clock`` (seconds, monotonic; default ``time.monotonic``, looked up
    at each click so tests may also patch it) times that. While an approval
    (or the agenda's Connect sign-in) runs, the other cards are locked.

    The reading screen's agenda (TODAY / TOMORROW and the calendar part of
    DEADLINES) is read on the calendar worker when the reading screen opens,
    every 10 minutes while it is visible, after a sign-in and after an
    Approve; it never signs in by itself. ``slots`` and ``run_state`` record
    which scheduled briefing was shown and answered (for the catch-up launch).
    """

    def __init__(self, config: Config, client: NotionClient, *, expected_run: str | None,
                 now_mode: bool, startup_error: str | None = None, volume: float = 1.0,
                 now_func: Callable[[], datetime] = _local_now,
                 calendar_factory: Callable[[Config], Any] | None = None,
                 action_store: ActionStore | None = None,
                 click_clock: Callable[[], float] | None = None,
                 slots: Mapping[str, str] | None = None,
                 run_state: RunState | None = None,
                 on_shutdown: Callable[[], Any] | None = None) -> None:
        super().__init__()
        self.config = config
        self.client = client
        self._on_shutdown = on_shutdown   # e.g. an exit watchdog (the app passes one; tests do not)
        self.expected_run = _normalize_run(expected_run)
        self.now_mode = bool(now_mode)
        self.startup_error = startup_error
        self.state = STATE_IDLE
        self._now = now_func
        self._click_clock = click_clock
        # Scheduled run times ({"AM": "10:12", "PM": "23:42"}) and where answers are
        # remembered for the catch-up launch; without a run state nothing is recorded.
        self.slots: dict[str, str] = dict(slots or {})
        self.run_state = run_state
        self._closing = threading.Event()
        self._shut_down = False
        self._bridge = _Bridge(self)
        self._volume = volume
        self.window = BriefingWindow(config.prompt.later_short_minutes, config.prompt.later_long_minutes)
        self.window.set_hour24(config.display.hour24)
        self.window.header.set_clock(now_func)
        self.player = self._create_player()
        self.tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_quit_action: Any = None
        self._tts: TtsWorker | None = None
        self._audio_dir: Path | None = None
        if action_store is None:
            action_store = ActionStore(config.data_dir / ACTIONS_FILE)
        self._store = action_store
        self._calendars: dict[str, Any] = self._create_calendars(calendar_factory)
        self.calendar = self._calendars.get(DEFAULT_ACCOUNT)   # the agenda's and Calendar proposals'
        self._executor = Executor([CalendarBackend(self._calendars, now=now_func)], config.accounts)
        self._calendar_worker: _ActionWorker | None = None
        self._init_fetch_state()
        self._init_script_state()
        self._init_action_state()
        self._init_agenda_state()
        self._init_timers()
        self._connect_signals()

    def _init_fetch_state(self) -> None:
        self._fetch_id = 0
        self._fetch_stop: threading.Event | None = None
        self._fetch_poll_run: str | None = None
        self._fetch_attempts = 0                 # attempts finished by the current fetch
        self._poll_deadline = self._now()
        self._briefing: Briefing | None = None
        self._fetch_error: NotionError | None = None
        self._checked_at = -math.inf             # time.monotonic() of the last successful fetch
        # When the current run began waiting: freshness is judged against that day
        # too, so a PM briefing that was fresh at 23:42 is still fresh at 00:12.
        self._run_started = self._now()
        self._logged_fetch = ""                  # content key / error last written to the activity log
        self._logged_error = ""

    def _init_script_state(self) -> None:
        self._prefetch: Script | None = None     # script prepared while the prompt shows
        self._script: Script | None = None       # frozen script on the reading screen
        self._generation = 0
        self._audio: dict[int, SectionAudio] = {}
        self._failed: set[int] = set()
        self._engine: str | None = None
        self._include_ignored = False
        self._has_played = False
        self._note = ""
        self._pending_run: str | None = None    # a newer run that launched during a reading
        self._reading_error = False              # the reading screen shows a load error
        self._audio_ready_logged = False
        self._sections_started: set[int] = set()
        self._last_section: int | None = None    # for "Read <section>" in the activity log
        self._skip_pending = False               # the next section start follows a skip or jump

    def _init_action_state(self) -> None:
        self._actions: list[ProposedAction] = []
        self._actions_key = ""                   # content key of the page the actions came from
        self._actions_lines: tuple = ()
        self._jobs: dict[str, str] = {}            # action id -> _STAGE_* while counting down, queued or running
        self._running: dict[str, tuple[ProposedAction, bool]] = {}   # id -> (what was sent, writes_running)
        self._countdown: _Countdown | None = None  # at most one: the other cards are locked meanwhile
        self._calendar_signed_in = False
        self._decision_clicked_at = -math.inf      # click clock of the last Approve / Deny click
        self._source_opened_at: dict[str, float] = {}   # action id -> click clock of its last Open
        self._connect_alias: str | None = None     # the account whose sign-in is queued or running
        self._connect_card = ""                    # the card whose Sign in / Approve started it
        self._connect_note = ""                    # why the last personal sign-in failed (the agenda line)
        self._accounts_state: dict[str, tuple[bool, str, str]] = {}   # alias -> (signed in, PROBLEM_*, message)
        self._checks: dict[str, EventCheck] = {}   # action id -> Google's view of its event (the check line)
        self._peek_seq = 0
        self._peeking: set[str] = set()            # action ids whose check is on its way
        self._hints: dict[str, str] = {}           # action id -> what its last Approve click needs next
        self._edits: dict[str, ActionEdit] = {}    # action id -> the Edit dialog's changes (memory only)
        self._carried: dict[str, ProposedAction] = {}   # action id -> what Jarvis carried out (this run)
        self._mismatch_seen: dict[str, EventCheck] = {}   # action id -> the check whose mismatch was shown
        self._dialog: hud.EditDialog | None = None
        self._read_slot: str | None = None         # the scheduled slot this reading settled

    @property
    def _calendar_jobs(self) -> dict[str, str]:
        """The older name of ``_jobs``."""
        return self._jobs

    @property
    def _connect_running(self) -> bool:
        """A sign-in (the agenda's Connect, a card's Sign in or Approve) is queued or running."""
        return self._connect_alias is not None

    def _init_agenda_state(self) -> None:
        self._agenda_status = _AGENDA_INIT
        self._agenda_events: list[CalendarEvent] | None = None   # from the last successful read
        self._agenda_range: tuple[datetime, datetime] | None = None   # what that read covered
        self._agenda_problem = ""                  # reason of the last failure (tooltips only)
        self._agenda_job: _AgendaJob | None = None   # the read on the calendar worker
        self._agenda_again = False                 # read again once that one answers
        self._agenda_seq = 0
        self._agenda_requested_at = -math.inf     # time.monotonic() of the last read
        self._page_deadlines: list[Deadline] = []  # the page's "Deadlines" section

    def _init_timers(self) -> None:
        self._ignore_timer = self._make_timer(self._on_prompt_ignored, single_shot=True)
        self._tick_timer = self._make_timer(self._update_countdown, single_shot=False, interval_ms=1000)
        self._snooze_timer = self._make_timer(self._on_snooze_timeout, single_shot=True)
        self._note_timer = self._make_timer(self._clear_note, single_shot=True)
        self._recheck_timer = self._make_timer(self._on_recheck_timeout, single_shot=True)
        # Only while the reading screen is visible (no timer at all while hidden).
        self._agenda_timer = self._make_timer(self._on_agenda_tick, single_shot=False,
                                              interval_ms=_AGENDA_TICK_MS)
        # Only while a card counts down to its call to Google.
        self._countdown_timer = self._make_timer(self._on_countdown_tick, single_shot=False,
                                                 interval_ms=_COUNTDOWN_TICK_MS)

    def _make_timer(self, slot: Callable[[], None], *, single_shot: bool, interval_ms: int = 0) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(single_shot)
        timer.setInterval(interval_ms)
        if single_shot:
            timer.setTimerType(Qt.TimerType.PreciseTimer)   # coarse timers may be 5% late
        timer.timeout.connect(slot)
        return timer

    def _connect_signals(self) -> None:
        queued = Qt.ConnectionType.QueuedConnection
        bridge = self._bridge
        bridge.fetchAttempt.connect(self._on_fetch_attempt, queued)
        bridge.fetchDone.connect(self._on_fetch_done, queued)
        bridge.ttsResult.connect(self._on_tts_result, queued)
        bridge.calendarStatus.connect(self._on_calendar_status, queued)
        bridge.accountStatus.connect(self._on_account_status, queued)
        bridge.actionProgress.connect(self._on_action_progress, queued)
        bridge.actionResult.connect(self._on_action_result, queued)
        bridge.eventPeek.connect(self._on_event_peek, queued)
        bridge.agendaResult.connect(self._on_agenda_result, queued)
        bridge.accountConnect.connect(self._on_account_connect, queued)
        prompt, reading = self.window.prompt, self.window.reading
        prompt.readNow.connect(self.read_now)
        prompt.later.connect(self.later)
        prompt.dismiss.connect(self.dismiss)
        reading.playPause.connect(self.toggle_play)
        reading.skip.connect(self.skip_section)
        reading.readEverything.connect(self.read_everything)
        reading.openNotion.connect(self.open_in_notion)
        reading.done.connect(self.done)
        reading.sectionClicked.connect(self.jump_to_section)
        reading.approveClicked.connect(self.approve_action)
        reading.denyClicked.connect(self.deny_action)
        reading.lockedClicked.connect(self._on_locked_click)
        reading.openLink.connect(self.open_action_link)
        reading.openSource.connect(self.open_action_source)
        reading.copyText.connect(self.copy_action_text)
        reading.undoAction.connect(self.undo_action)
        reading.editAction.connect(self.edit_action)
        reading.signInAccount.connect(self.sign_in_account)
        reading.connectCalendar.connect(self.connect_calendar)
        self.window.closeRequested.connect(self._on_close_requested)
        self.window.visibilityChanged.connect(self._sync_agenda_timer)

    def _create_player(self) -> BriefingPlayer:
        player = BriefingPlayer(self, section_gap_ms=self.config.voice.section_gap_ms,
                                volume=self._volume)
        player.itemChanged.connect(self._on_item_changed)
        player.sectionStarted.connect(self._on_section_started)
        player.stateChanged.connect(self._on_player_state)
        player.errorOccurred.connect(self._on_player_error)
        return player

    def _replace_player(self) -> None:
        """A new player for a new frozen script; the old one keeps the old audio registered."""
        old = self.player
        old.blockSignals(True)
        old.stop()
        old.deleteLater()
        self.player = self._create_player()

    def _create_calendars(self, factory: Callable[[Config], Any] | None) -> dict[str, Any]:
        """alias -> calendar ({} when [calendar] enabled = false or it could not be set up).

        ``factory`` may return one calendar (the personal account's) or a mapping. Building
        them opens nothing and talks to no one; the single sign-in of older versions becomes
        the personal account's here (executor.build_calendars).
        """
        if not self.config.calendar.enabled:
            return {}
        try:
            built = (factory or _default_calendars)(self.config)
        except Exception as exc:  # noqa: BLE001 - the briefing works without a calendar
            logger.warning("Could not set up Google Calendar (%s)", type(exc).__name__)
            return {}
        if built is None:
            return {}
        if isinstance(built, Mapping):
            return {str(alias): calendar for alias, calendar in built.items() if calendar is not None}
        return {DEFAULT_ACCOUNT: built}

    # ---- startup ------------------------------------------------------------------

    def start(self) -> None:
        logger.info("Controller starting (expected run: %s, read immediately: %s)",
                    self.expected_run or "any", self.now_mode)
        self._run_started = self._scheduled_start(self.expected_run)
        self._audio_dir = self._prepare_audio_dir()
        self._store.prune()
        self._start_tts()
        self._create_tray()
        self._start_calendar()
        self._update_service_chips()
        if self.now_mode:
            self.enter_reading()
            force_foreground(self.window)
            return
        if not self.startup_error:
            self._start_fetch(self.expected_run)
        self.show_prompt(take_focus=True)

    def _prepare_audio_dir(self) -> Path:
        root = self.config.audio_root
        audio_dir = root / f"session-{os.getpid()}"
        try:
            audio_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create the audio folder %s: %s", audio_dir, exc)
        _remove_old_sessions(root, keep=audio_dir)
        return audio_dir

    def _start_tts(self) -> None:
        voice, out_dir = self.config.voice, self._audio_dir
        bridge, closing = self._bridge, self._closing

        def factory() -> SpeechSynthesizer:   # runs on the worker thread (SAPI/COM is thread-bound)
            return SpeechSynthesizer(voice=voice.voice, rate=voice.rate, volume=voice.volume,
                                     offline_voice=voice.offline_voice, out_dir=out_dir,
                                     divider_pause_ms=voice.divider_pause_ms)

        def on_result(generation: int, index: int, audio: SectionAudio | None, error: str | None) -> None:
            _emit_from_worker(bridge, "ttsResult", closing, generation, index, audio, error)

        self._tts = TtsWorker(factory, on_result)
        self._tts.start()

    def _start_calendar(self) -> _ActionWorker | None:
        """The action worker, started once Google Calendar is set up (it checks the sign-ins first)."""
        if self._calendar_worker is None and self._any_calendar_configured() and not self._closing.is_set():
            self._calendar_worker = _ActionWorker(self._executor, self._calendars, self._store, self._bridge,
                                                  self._closing)
            self._calendar_worker.check_sign_in()
        return self._calendar_worker

    def _calendar_configured(self) -> bool:
        """The personal account's calendar (the agenda, Calendar proposals) is set up."""
        return self._configured(self.calendar)

    def _any_calendar_configured(self) -> bool:
        return any(self._configured(calendar) for calendar in self._calendars.values())

    @staticmethod
    def _configured(calendar: Any) -> bool:
        try:
            return calendar is not None and bool(calendar.is_configured())
        except Exception as exc:  # noqa: BLE001 - treated as not set up
            logger.debug("Could not check the Google Calendar setup (%s)", type(exc).__name__)
            return False

    def _create_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("No system tray available; continuing without a tray icon")
            return
        menu = QMenu()
        menu.addAction("Show briefing").triggered.connect(self._on_tray_show)
        self._tray_quit_action = menu.addAction("Dismiss")
        self._tray_quit_action.triggered.connect(self._on_tray_quit)
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip("briefing-reader")
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray, self._tray_menu = tray, menu

    # ---- tray -------------------------------------------------------------------------

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_tray_show()

    def _on_tray_show(self) -> None:
        if self.state == STATE_SNOOZED:
            self.show_prompt(take_focus=True)
        elif self.state in (STATE_PROMPT, STATE_READING):
            force_foreground(self.window)

    def _on_tray_quit(self) -> None:
        logger.info("Quit from the tray menu")
        if self.state == STATE_READING:
            self._record_done()
        elif self.state in (STATE_PROMPT, STATE_SNOOZED):
            self.record_answer("dismissed")   # the menu item says Dismiss there
        self.quit()

    def _update_tray(self, tooltip: str = "briefing-reader") -> None:
        if self.tray is not None:
            self.tray.setToolTip(tooltip)
        if self._tray_quit_action is not None:
            self._tray_quit_action.setText("Quit" if self.state == STATE_READING else "Dismiss")

    # ---- activity log and header chips -------------------------------------------------

    def _activity(self, tag: str, message: str, sub: str = "") -> None:
        self.window.reading.activity.add(tag, message, sub, when=self._now())

    def _update_service_chips(self) -> None:
        header = self.window.header
        header.set_service("notion", *self._notion_chip())
        header.set_service("voice", *self._voice_chip())
        header.set_service("calendar", *self._calendar_chip())

    def _notion_chip(self) -> tuple[str, str]:
        if self.startup_error:
            return hud.STATUS_ERROR, _startup_error_chip(self.startup_error)
        if self._fetch_error is not None:
            if self.fetching:
                return hud.STATUS_WARN, f"Notion: retrying - {_short(str(self._fetch_error))}"
            return hud.STATUS_ERROR, f"Notion: {_short(str(self._fetch_error))}"
        if self._briefing is not None:
            return hud.STATUS_OK, "Notion: connected"
        return hud.STATUS_OFF, "Notion: checking..."

    def _voice_chip(self) -> tuple[str, str]:
        if self._uses_offline_voice():
            return hud.STATUS_WARN, "Voice: offline (Windows voice)"
        if self._engine is not None:
            return hud.STATUS_OK, "Voice: online (edge-tts)"
        if self._failed and not self._audio:
            return hud.STATUS_ERROR, "Voice: speech synthesis failed"
        return hud.STATUS_OFF, "Voice: no audio prepared yet"

    def _calendar_chip(self) -> tuple[str, str]:
        pending = len(self._pending_actions())
        waiting = f" - {_needs_ok(pending)}" if pending else ""
        if not self.config.calendar.enabled:
            return hud.STATUS_OFF, "Google Calendar: turned off in config.toml" + waiting
        if not self._calendar_configured():
            return hud.STATUS_OFF, "Google Calendar: not set up (README step 8)" + waiting
        others = self._other_accounts_text()
        if self._calendar_signed_in:
            return hud.STATUS_OK, "Google Calendar: signed in" + others + waiting
        return (hud.STATUS_WARN, "Google Calendar: not signed in (Approve opens the sign-in)" + others
                + waiting)

    def _other_accounts_text(self) -> str:
        """"; work account: not signed in" for each account besides the personal one."""
        parts = []
        for alias in self._calendars:
            if alias == DEFAULT_ACCOUNT:
                continue
            signed_in, problem, _message = self._accounts_state.get(alias, (False, "", ""))
            if signed_in:
                state = "signed in"
            elif self._connect_alias == alias:
                state = "signing in"
            elif problem == PROBLEM_BLOCKED:
                state = "blocked by its administrator"
            elif problem in (PROBLEM_EXPIRED, PROBLEM_DENIED, PROBLEM_SCOPE, PROBLEM_TIMEOUT, PROBLEM_FAILED):
                state = "sign in again"
            else:
                state = "not signed in"
            parts.append(f"; {alias} account: {state}")
        return "".join(parts)

    # ---- fetching ----------------------------------------------------------------------

    @property
    def fetching(self) -> bool:
        return self._fetch_stop is not None

    @property
    def _polling(self) -> bool:
        """Waiting for the expected run's briefing (not a single fetch)."""
        return self.fetching and self._fetch_poll_run is not None

    def _freshness(self, briefing: Briefing) -> Freshness:
        return check_freshness(briefing.header, self.expected_run, self._now(),
                               run_started=self._run_started)

    def _cached_is_fresh(self) -> bool:
        """The briefing we have is today's (and the expected run's, if one is expected)."""
        return self._briefing is not None and self._freshness(self._briefing).fresh

    def _checked_recently(self) -> bool:
        """Notion was read successfully less than one polling interval ago."""
        return time.monotonic() - self._checked_at < self.config.polling.interval_seconds

    def _needs_recheck(self) -> bool:
        """Nothing is fetching and the briefing we have is not fresh: worth asking Notion again."""
        return (not self.startup_error and not self.fetching and self._briefing is not None
                and not self._cached_is_fresh())

    def _start_fetch(self, poll_run: str | None) -> None:
        """Start a fetch thread; ``poll_run`` set means keep polling until that run is fresh."""
        self._stop_fetch()
        self._fetch_id += 1
        stop = threading.Event()
        self._fetch_stop = stop
        self._fetch_poll_run = poll_run
        self._fetch_error = None
        self._fetch_attempts = 0
        polling = self.config.polling
        self._poll_deadline = self._now() + timedelta(minutes=polling.timeout_minutes)
        client, page_id = self.client, self.config.page_id
        options = {"expected_run": poll_run, "interval_s": float(polling.interval_seconds),
                   "timeout_s": polling.timeout_minutes * 60.0, "now": self._now,
                   "run_started": self._run_started}
        thread = threading.Thread(
            target=_run_fetch, name=f"fetch-{self._fetch_id}", daemon=True,
            args=(self._bridge, self._fetch_id, stop, lambda: fetch_briefing(client, page_id), options))
        thread.start()
        logger.info("Fetching the briefing (%s)",
                    f"waiting for the {poll_run} run" if poll_run else "first successful fetch")

    def _stop_fetch(self) -> None:
        if self._fetch_stop is not None:
            self._fetch_stop.set()
            self._fetch_stop = None

    def _on_fetch_attempt(self, fetch_id: int, briefing: Briefing | None, error: NotionError | None,
                          attempt: int) -> None:
        if fetch_id != self._fetch_id or self.state == STATE_QUITTING:
            return
        self._fetch_attempts += 1
        if briefing is None:
            logger.info("Fetch attempt %d failed: %s", attempt, error)
            self._fetch_error = error
            self._log_fetch_error(error)
            self._on_loading_error()
        else:
            logger.info("Fetch attempt %d: updated=%r run=%s fresh=%s", attempt,
                        briefing.header.updated_raw, briefing.header.run or "-",
                        self._freshness(briefing).fresh)
            self._fetch_error = None
            self._checked_at = time.monotonic()
            self._log_fetch(briefing)
            if self._fetch_attempts == 1 and self._polling and not self._freshness(briefing).fresh:
                self._activity(hud.TAG_WAIT, f"Waiting for the {self._fetch_poll_run} briefing",
                               f"checking Notion {self._interval_text()}")
            self._accept_briefing(briefing)
        self._refresh_views()

    def _log_fetch(self, briefing: Briefing) -> None:
        """One activity row per new page content (polling re-reads the same page every minute)."""
        key = briefing.content_key
        if key == self._logged_fetch:
            return
        first = not self._logged_fetch
        self._logged_fetch, self._logged_error = key, ""
        run = briefing.header.run
        sub = _plural(len(briefing.lines), "line") + (f" {DOT} {run} run" if run else "")
        self._activity(hud.TAG_DONE, "Fetched briefing" if first else "Briefing updated", sub)

    def _log_fetch_error(self, error: NotionError | None) -> None:
        text = _short(str(error)) if error is not None else ""
        if text and text != self._logged_error:
            self._logged_error = text
            self._activity(hud.TAG_STOP, "Couldn't load the briefing", text)

    def _accept_briefing(self, briefing: Briefing) -> None:
        if self._script is not None:
            logger.info("Ignoring a newer briefing: the reading screen already shows one")
            return
        self._briefing = self._take_actions(briefing)
        if self.state == STATE_READING:
            self._begin_reading()
        else:
            self._refresh_prefetch()

    def _on_fetch_done(self, fetch_id: int, result: PollResult) -> None:
        if fetch_id != self._fetch_id or self.state == STATE_QUITTING:
            return
        self._fetch_stop = None
        logger.info("Fetching finished after %d attempt(s): %s", result.attempts, _describe_poll(result))
        if result.briefing is None and result.error is not None:
            self._fetch_error = result.error
        if result.timed_out and self.expected_run:
            self._activity(hud.TAG_WAIT, f"No new {self.expected_run} briefing yet",
                           "Read now plays the last one")
        if self.state == STATE_READING and self._script is None:
            if self._briefing is not None:
                self._begin_reading()
            else:
                self._on_loading_error()
            self._update_service_chips()
            return
        self._refresh_prefetch()
        self._refresh_views()

    # ---- proposed actions ------------------------------------------------------------------

    def _take_actions(self, briefing: Briefing) -> Briefing:
        """Move the page's "Proposed actions" section into the approval cards and its
        "Deadlines" section into DEADLINES; neither is shown in the transcript or spoken.

        Polling reads the same page every minute, so the sections are only
        parsed again when the page content changed.
        """
        key = briefing.content_key
        if key != self._actions_key:
            # Deadlines first, ending at a "Proposed actions" heading: either section may be
            # nested inside the other and both still land where they belong.
            headings = self.config.actions.headings
            deadlines, lines = extract_deadlines(briefing.lines, stop_names=headings)
            actions, lines = extract_actions(lines, headings, link_hosts=self.config.actions.link_hosts)
            self._actions_key, self._actions_lines = key, tuple(lines)
            self._page_deadlines = deadlines
            self._set_actions(actions)
            self._render_agenda()
        if len(self._actions_lines) == len(briefing.lines):
            return briefing
        return dataclasses.replace(briefing, lines=self._actions_lines)

    def _pending_actions(self) -> list[ProposedAction]:
        """Proposals that take a decision and have not been decided yet (as their cards show
        them: with the Edit dialog's changes)."""
        return [self._effective(action) for action in self._actions
                if action.decidable and not self._store.is_decided(action.id)]

    def _action(self, action_id: str) -> ProposedAction | None:
        """The proposal as the page has it."""
        return next((action for action in self._actions if action.id == action_id), None)

    def _current(self, action_id: str) -> ProposedAction | None:
        """The proposal as its card shows it (and as Approve carries it out): the page's line
        with the Edit dialog's changes."""
        action = self._action(action_id)
        return self._effective(action) if action is not None else None

    def _shown(self, action_id: str) -> ProposedAction | None:
        """The proposal as its card shows it: while its countdown ran out and the call is queued or
        on its way, and after Jarvis carried it out, exactly what was sent (a newer page with the
        same id never changes that card); otherwise as _current."""
        running = self._running.get(action_id)
        if running is not None and running[1]:
            return running[0]
        if action_id in self._carried and self._store.get(action_id):
            return self._carried[action_id]
        return self._current(action_id)

    def _effective(self, action: ProposedAction) -> ProposedAction:
        edit = self._edits.get(action.id)
        if edit is None:
            return action
        try:
            return apply_edit(action, edit)
        except EditError:
            # The page changed under the edit (same id, other content): the card shows the page.
            logger.info("Dropped the edit of action %s: it no longer fits the briefing's line", action.id)
            self._edits.pop(action.id, None)
            return action

    def _set_actions(self, actions: list[ProposedAction]) -> None:
        if actions == self._actions:
            return
        self._actions = list(actions)
        ids = {action.id for action in self._actions}
        for kept in (self._edits, self._checks, self._hints, self._carried, self._mismatch_seen):
            for action_id in [key for key in kept if key not in ids]:
                del kept[action_id]
        countdown = self._countdown
        if countdown is not None and self._current(countdown.action.id) != countdown.action:
            self._cancel_countdown("the briefing changed")   # never send what the card no longer shows
        if self._dialog is not None and self._dialog.action_id not in ids:
            self._dialog.reject()
        reading = self.window.reading
        reading.clear_action_cards()
        today = self._now().date()
        for page_action in self._actions:
            action = self._shown(page_action.id) or self._effective(page_action)
            view = card_view(action, today)
            label = view.kind_label + (EDITED_MARK if action.id in self._edits else "")
            reading.add_action_card(action.id, label, view.title, view.detail,
                                    view.decidable, **_card_options(action, view))
            self._show_card_state(action.id)   # also sets the note (warnings) while it waits
        self._sync_card_locks()
        self._refresh_actions_ui()
        self._request_peeks()

    def _show_card_state(self, action_id: str) -> None:
        """The card's status from the countdown, the running job or the saved decision.

        While the card waits for a decision (pending, or failed / unknown and kept for
        a retry) its note is shown again: WORKING... clears the note. An RSVP, Move or
        Cancel card also gets its check line, its Edit / Copy links and the text of its
        right-hand button (Accept, Sign in, Retry, or Done when Jarvis may not change
        the event) here.
        """
        card = self.window.reading.action_card(action_id)
        if card is None or not card.actionable:
            return
        action = self._shown(action_id)
        stage = self._jobs.get(action_id)
        entry = self._store.get(action_id) or {}
        status = entry.get("status", "")
        countdown = action is not None and action.countdown
        if countdown:
            self._sync_countdown_card(card, action, status, stage)
        if stage == _STAGE_COUNTDOWN:
            card.set_status(hud.CARD_COUNTDOWN, self._countdown_text())
            return
        if stage is not None:
            if stage == _STAGE_SIGNIN:
                card.set_status(hud.CARD_SIGNIN,
                                f"Waiting for Google sign-in ({action.account})" if countdown else "")
            else:
                card.set_status(hud.CARD_WORKING)
            return
        if status == STATUS_FAILED:
            message = entry.get("message", "")
        elif status == STATUS_UNKNOWN and countdown:
            message = UNKNOWN_CARD_TEXT
        elif status == STATUS_SENT:
            message = entry.get("message", "") or (result_text(action, status) if action is not None else "")
        else:
            message = result_text(action, status) if action is not None else ""
        shown = _CARD_FOR_STATUS.get(status, hud.CARD_PENDING)
        block = action is not None and action.kind == TODO
        link_text = _BLOCK_LINK_TEXT if block else (_EVENT_LINK_TEXT if countdown else "")
        card.set_status(shown, message, entry.get("link", ""), link_text)
        if action is None or shown not in (hud.CARD_PENDING, hud.CARD_FAILED, hud.CARD_UNKNOWN):
            return
        if countdown:
            card.set_note(self._countdown_note(action, status, entry))
        elif card.note() != CALENDAR_SETUP_NOTE:
            note = card_view(action, self._now().date()).note
            if note:
                card.set_note(note)

    def _sync_countdown_card(self, card: hud.ActionCard, action: ProposedAction, status: str,
                             stage: str | None) -> None:
        """An RSVP, Move or Cancel card's check line, tools row (Edit / Sign in / Open event,
        Copy) and button text."""
        decided = self._store.is_decided(action.id)
        idle = stage is None and not decided and self.state != STATE_QUITTING
        text, tooltip, warn = self._check_view(action, status)
        card.set_check_line(text, tooltip, warn=warn)
        card.set_tool_slot(self._tool_slot(action, status))
        card.set_edit_enabled(self._tools_enabled(action.id))
        card.set_copy_text(self._copy_label(action))
        if idle:
            card.set_approve_text(self._approve_text(action, status))

    def _approve_text(self, action: ProposedAction, status: str) -> str:
        """Done while Jarvis can't act for the card's account (not set up for it, or its
        administrator blocks the app: the card is a hand-off, as in earlier versions); Sign in
        while the account is signed out (that click opens Google's sign-in); Done when Google's
        view of the event does not let Jarvis carry the card out; Retry after a failure or an
        unknown outcome; else Accept / Decline / Maybe, Move or Cancel event. A click does what
        the button says (approve_action asks this same function)."""
        if self._hand_off(action):
            return DONE_TEXT
        if self._needs_sign_in(action):
            return SIGN_IN_LINK_TEXT
        if self._blocked(action.id):
            return DONE_TEXT
        if status in (STATUS_FAILED, STATUS_UNKNOWN):
            return RETRY_TEXT
        return card_view(action, self._now().date()).approve_text or "Approve"

    def _hand_off(self, action: ProposedAction) -> bool:
        """Jarvis can't carry out cards of this account here: it is not set up for them
        (executor.readiness) or its administrator blocks the app (until a new Sign in works)."""
        if self._executor.readiness(action):
            return True
        state = self._accounts_state.get(action.account)
        return (state is not None and not state[0] and state[1] == PROBLEM_BLOCKED
                and self._connect_alias != action.account)

    def _tool_slot(self, action: ProposedAction, status: str) -> str:
        """The tools row's last place: Sign in while the account's administrator blocks the app
        (its right-hand button is Done), Google's own "Open event" when the line has no link and
        Jarvis won't act on the event or its outcome is unknown (to check it), else Edit."""
        if not self._executor.readiness(action) and self._hand_off(action):
            return hud.TOOL_SIGN_IN
        if (not action.link and self._google_link(action.id)
                and (self._blocked(action.id) or status == STATUS_UNKNOWN)):
            return hud.TOOL_OPEN
        return hud.TOOL_EDIT

    def _tools_enabled(self, action_id: str) -> bool:
        """Edit and Sign in open a dialog or the browser: only while nothing else runs (no
        countdown, job or sign-in on any card), so nothing ever covers a running card's Undo."""
        return (self.state != STATE_QUITTING and not self._jobs and self._countdown is None
                and not self._connect_running and not self._store.is_decided(action_id))

    def _copy_label(self, action: ProposedAction) -> str:
        """The tools row's Copy: a Move's or Cancel's note (never sent); an RSVP's note too when
        Jarvis won't send the answer (the card is a hand-off or Done)."""
        label = copy_text(action)
        if not label and action.kind == RSVP and action.body and (self._hand_off(action) or self._blocked(action.id)):
            return "Copy note"
        return label

    def _google_link(self, action_id: str) -> str:
        """Google's own page of the card's event (from the check), when it is an allowed https link."""
        check = self._checks.get(action_id)
        link = check.link if check is not None else ""
        return link if link.startswith("https://") and link_allowed(link, self.config.actions.link_hosts) else ""

    def _blocked(self, action_id: str) -> bool:
        """Google's view of the card's event says Jarvis may not carry it out (not the
        organizer, not a guest, a series, all-day, not found)."""
        check = self._checks.get(action_id)
        return check is not None and not check.allowed and bool(check.reason)

    def _account_signed_in(self, alias: str) -> bool:
        state = self._accounts_state.get(alias)
        return state is not None and state[0]

    def _needs_sign_in(self, action: ProposedAction) -> bool:
        """The card's account is known to be signed out (its right-hand button reads Sign in)."""
        if self._executor.readiness(action):
            return False
        state = self._accounts_state.get(action.account)
        return state is not None and not state[0] and self._connect_alias != action.account

    def _check_view(self, action: ProposedAction, status: str = "") -> tuple[str, str, bool]:
        """The check line (text, tooltip, amber): Google's own view of the event (in amber when
        its title or time is not the line's), why Jarvis won't act on it, or why it is not
        shown (setup, sign-in); after Jarvis carried the card out, what Google has now.
        Everything that arrives by itself (a check, a sign-in state) goes here, in the room the
        card reserved for it, so it never moves a card; the amber note below the buttons only
        changes after a click on that card."""
        if status == STATUS_SENT:
            return self._after_view(action)
        reason = self._executor.readiness(action)
        if reason:
            return reason, "", True
        alias = action.account
        if self._connect_alias == alias:
            return f"Waiting for the Google sign-in of the {alias} account...", "", False
        signed_in, problem, message = self._accounts_state.get(alias, (True, "", ""))
        if not signed_in and problem and problem != PROBLEM_SIGNED_OUT:
            line = _PROBLEM_LINES.get(problem, _PROBLEM_LINE_OTHER).format(alias=alias)
            return line, sign_in_note(alias, problem=problem, message=message), True
        check = self._checks.get(action.id)
        if check is not None:
            if check.reason:   # Jarvis won't act on this event: the reason, Google's view on hover
                return check.reason, f"{check.reason}\n{check.tooltip or check.text}", True
            return check.text, check.tooltip, not check.allowed or bool(check.mismatch)
        if action.id in self._peeking:
            return CHECK_SOON_TEXT, "", False
        if alias in self._accounts_state and not signed_in:
            return check_failure(action, CalendarNotSignedIn(problem=PROBLEM_SIGNED_OUT)).text, "", False
        return CHECK_LATER_TEXT, "", False

    def _after_view(self, action: ProposedAction) -> tuple[str, str, bool]:
        """The check line once Jarvis carried the card out: what Google has now, by the answer
        Google gave ("Google now: Speaker series \u00b7 you accepted")."""
        check = self._checks.get(action.id)
        title = check.title if check is not None else ""
        if action.kind == RSVP:
            what = _ANSWERED.get(action.field("answer"), "answered")
        elif action.kind == MOVE:
            what = when_text(action, self._now().date())
        else:
            what = "cancelled"
        text = AFTER_PREFIX + f" {DOT} ".join(part for part in (title, what) if part)
        return text, "", False

    def _countdown_note(self, action: ProposedAction, status: str, entry: Mapping[str, Any]) -> str:
        """The amber note of an RSVP, Move or Cancel card: what the last click on it needs next,
        why its outcome is unknown, then the line's own warnings."""
        parts = [self._hints.get(action.id, "")]
        if status == STATUS_UNKNOWN:   # why (the line above the note already says to check first)
            parts.append(_CHECK_BEFORE_RETRY_RE.sub("", entry.get("message", "")))
        parts.append(card_view(action, self._now().date()).note)
        return f" {DOT} ".join(dict.fromkeys(part for part in parts if part))

    def _hint(self, action_id: str, text: str) -> None:
        """What the card's last Approve click needs next (its amber note until the next click)."""
        if text:
            self._hints[action_id] = text
        else:
            self._hints.pop(action_id, None)
        self._show_card_state(action_id)

    def _refresh_countdown_cards(self) -> None:
        """Check lines, links, buttons and notes of every RSVP, Move and Cancel card."""
        for action in self._actions:
            if action.countdown:
                self._show_card_state(action.id)

    def _refresh_actions_ui(self) -> None:
        """Pending count on the prompt chip, the STATUS bar, the panel meta and the calendar chip."""
        pending = len(self._pending_actions())
        total = sum(1 for action in self._actions if action.decidable)
        self.window.prompt.set_pending(pending)
        self.window.reading.set_pending(pending, total)
        self._update_service_chips()

    def _decision_click_too_soon(self) -> bool:
        """True (and logged) for an Approve / Deny within 1 s of the previous one, on any card.

        Every click counts, also an ignored one, so a burst of clicks decides
        at most one proposal until the mouse rests for a second.
        """
        now = self._click_now()
        previous, self._decision_clicked_at = self._decision_clicked_at, now
        if now - previous < _DECISION_CLICK_GUARD_S:
            logger.info("Ignored a second approval click within 1 s")
            return True
        return False

    def _click_now(self) -> float:
        return self._click_clock() if self._click_clock is not None else time.monotonic()

    # ---- approval lock ----------------------------------------------------------------------

    def _lock_tip(self) -> str:
        """Why the cards are locked ("" when they are not)."""
        if self._connect_running:
            return SIGN_IN_LOCK_TIP
        return LOCK_TIP if self._jobs else ""

    def _sync_card_locks(self) -> None:
        """Lock every card but the one being approved while an approval (its countdown included)
        or a sign-in runs."""
        tip = self._lock_tip()
        for card in self.window.reading.approvals.cards():
            card.set_locked(bool(tip) and card.action_id not in self._jobs, tip)
            action = self._action(card.action_id)
            if card.actionable and action is not None and action.countdown:
                card.set_edit_enabled(self._tools_enabled(card.action_id))   # never a dialog beside Undo

    def _locked_out(self, action_id: str) -> bool:
        """True (and logged, without titles) for an Approve / Deny on a locked card."""
        if self._connect_running:
            logger.info(_SIGN_IN_LOCKED_MESSAGE)
            return True
        if self._jobs and action_id not in self._jobs:
            logger.info(_LOCKED_MESSAGE)
            return True
        return False

    def _on_locked_click(self, action_id: str) -> None:
        """A click on a dimmed Deny / Approve: ignored, but it counts for the 1 s guard."""
        if not self._decision_click_too_soon() and not self._locked_out(action_id):
            logger.info(_LOCKED_MESSAGE)

    def approve_action(self, action_id: str) -> None:
        """The card's right-hand button.

        Approve creates the event on the action worker (signing in first when
        needed) and Add block adds a Todo's block the same way. Accept /
        Decline / Maybe, Move and Cancel event (and Retry) start the undo
        countdown once the card shows Google's own view of its event and that
        view allows it; before that the click signs the account in or checks
        the event, and the card says to click again. Done (cards Jarvis does
        not carry out, or whose event Jarvis may not change) records that you
        handled it.
        """
        if self._decision_click_too_soon() or self._locked_out(action_id):
            return
        action = self._current(action_id)
        if (action is None or not action.decidable or self.state == STATE_QUITTING
                or action_id in self._jobs or self._store.is_decided(action_id)):
            return
        if not action.actionable:
            self._mark_done(action)
            return
        if action.countdown:
            status = (self._store.get(action_id) or {}).get("status", "")
            if self._approve_text(action, status) == DONE_TEXT:   # what the button says
                self._mark_done(action)
                return
            self._approve_countdown(action)
            return
        if action.kind == TODO and action.block_event() is None:
            return
        card = self.window.reading.action_card(action_id)
        worker = self._start_calendar()
        if worker is None:
            logger.info("Approve %s: Google Calendar is not set up", action_id)
            if card is not None:
                card.set_note(CALENDAR_SETUP_NOTE)
            self._activity(hud.TAG_WAIT, "Google Calendar is not set up", "README step 8")
            return
        logger.info("Approved action %s", action_id)
        self._jobs[action_id] = _STAGE_WORKING
        self._running[action_id] = (action, False)
        self._show_card_state(action_id)
        self._sync_card_locks()
        adding = f"Adding a block for {_activity_title(action)}" if action.kind == TODO else f"Adding {action.title}"
        self._activity(hud.TAG_RUN, adding, "Google Calendar")
        worker.approve(action)   # a Todo's block keeps the Todo's id, so the result lands on its card

    def _approve_countdown(self, action: ProposedAction) -> None:
        """Accept / Move / Cancel event / Retry: sign in or check first when needed, else count down."""
        action_id = action.id
        card = self.window.reading.action_card(action_id)
        button = card.approve_text() if card is not None else "Approve"
        reason = self._executor.readiness(action)
        if not reason and self._start_calendar() is None:
            reason = CALENDAR_SETUP_NOTE
        if reason:
            logger.info("Approve %s: Jarvis can't carry it out here (%s, %s)", action_id, action.kind,
                        logged_alias(action.account))
            self._hint(action_id, NOT_READY_NOTE if reason == self._check_view(action)[0] else reason)
            self._activity(hud.TAG_WAIT, f"Can't {_VERBS.get(action.kind, 'do')} this yet: {_kind_title(action)}",
                           _short(reason))
            return
        if not self._account_signed_in(action.account):
            logger.info("Approve %s: signing in to the %s account first", action_id, logged_alias(action.account))
            self._hint(action_id, SIGN_IN_FIRST_NOTE)
            self._start_sign_in(action.account, card_id=action_id)
            return
        check = self._checks.get(action_id)
        if check is None or not check.allowed:
            logger.info("Approve %s: checking the event with Google first", action_id)
            self._hint(action_id, CHECK_FIRST_NOTE.format(button=button))
            self._request_peeks([action], force=True)
            return
        if check.mismatch and self._mismatch_seen.get(action_id) != check:
            # Google's title or time is not the line's: say so once; the next click goes ahead.
            logger.info("Approve %s: Google's event differs from the line (%s); asking again", action_id,
                        check.mismatch)
            self._mismatch_seen[action_id] = check
            self._hint(action_id, MISMATCH_NOTE.format(what=_MISMATCH_WHAT.get(check.mismatch, "other details"),
                                                       button=button))
            return
        self._start_countdown(action)

    # ---- the undo countdown --------------------------------------------------------------

    def _start_countdown(self, action: ProposedAction) -> None:
        seconds = int(self.config.actions.undo_seconds)
        now = time.monotonic()
        self._hints.pop(action.id, None)
        self._countdown = _Countdown(action, deadline=now + seconds, started=now)
        self._jobs[action.id] = _STAGE_COUNTDOWN
        logger.info("Approved action %s (%s, %s): sending in %d s unless undone", action.id, action.kind,
                    logged_alias(action.account), seconds)
        self._show_card_state(action.id)
        self._sync_card_locks()
        self._activity(hud.TAG_RUN, f"Sending in {seconds} s: {_action_phrase(action)}", kind_label(action).upper())
        self._countdown_timer.start()

    def _countdown_text(self) -> str:
        countdown = self._countdown
        if countdown is None:
            return ""
        seconds = max(0, math.ceil(countdown.deadline - time.monotonic()))
        countdown.shown = seconds
        return f"Sending in {seconds} s"

    def _on_countdown_tick(self) -> None:
        countdown = self._countdown
        if countdown is None:
            self._countdown_timer.stop()
            return
        remaining = countdown.deadline - time.monotonic()
        if remaining <= 0:
            self._finish_countdown()
            return
        if math.ceil(remaining) != countdown.shown:
            card = self.window.reading.action_card(countdown.action.id)
            if card is not None:
                card.set_status(hud.CARD_COUNTDOWN, self._countdown_text())

    def _finish_countdown(self) -> None:
        """The countdown ran out: queue the one call (the worker saves "running" right before it)."""
        countdown, self._countdown = self._countdown, None
        self._countdown_timer.stop()
        if countdown is None:
            return
        action = countdown.action
        worker = self._calendar_worker
        if self.state == STATE_QUITTING or worker is None:
            self._jobs.pop(action.id, None)
            return
        self._jobs[action.id] = _STAGE_WORKING
        self._running[action.id] = (action, True)
        self._show_card_state(action.id)
        self._activity(hud.TAG_RUN, f"Sending: {_action_phrase(action)}", kind_label(action).upper())
        worker.execute(action)

    def undo_action(self, action_id: str) -> None:
        """Undo during the countdown: nothing is sent and nothing is saved; the card waits again.

        A click in the first double-click interval after the approval is
        ignored, so a double click on Accept never undoes it at once.
        """
        countdown = self._countdown
        if countdown is None or countdown.action.id != action_id or self.state == STATE_QUITTING:
            return
        if (time.monotonic() - countdown.started) * 1000 < QApplication.doubleClickInterval():
            logger.info("Ignored an Undo click right after the approval")
            return
        action = countdown.action
        self._cancel_countdown("undone")
        self._activity(hud.TAG_STOP, f"Undone: {_action_phrase(action)}",
                       f"{kind_label(action).upper()} {DOT} nothing was sent")
        self._refresh_actions_ui()

    def _cancel_countdown(self, reason: str) -> None:
        countdown, self._countdown = self._countdown, None
        self._countdown_timer.stop()
        if countdown is None:
            return
        action_id = countdown.action.id
        self._jobs.pop(action_id, None)
        logger.info("Action %s undone (%s); nothing was sent", action_id, reason)
        self._show_card_state(action_id)
        self._sync_card_locks()

    def _send_in_flight(self) -> bool:
        """An RSVP, Move or Cancel call is queued or on its way to Google."""
        return any(writes and action_id in self._jobs for action_id, (_action, writes) in self._running.items())

    # ---- other decisions and the tools row -----------------------------------------------------

    def _mark_done(self, action: ProposedAction) -> None:
        """Done on a card Jarvis does not carry out: you handled it yourself."""
        logger.info("Marked action %s done", action.id)
        self._hints.pop(action.id, None)
        self._store.set(action.id, STATUS_DONE, kind=action.kind, account=action.account)
        self._show_card_state(action.id)
        self._activity(hud.TAG_DONE, f"Done: {_activity_title(action)}", kind_label(action).upper())
        self._refresh_actions_ui()

    def deny_action(self, action_id: str) -> None:
        if self._decision_click_too_soon() or self._locked_out(action_id):
            return
        action = self._current(action_id)
        if (action is None or not action.decidable or action_id in self._jobs
                or self._store.is_decided(action_id)):
            return
        logger.info("Denied action %s", action_id)
        self._hints.pop(action_id, None)
        self._store.set(action_id, STATUS_DENIED, kind=action.kind, account=action.account)
        self._show_card_state(action_id)
        if action.kind == CALENDAR:
            self._activity(hud.TAG_STOP, f"Denied {action.title}", "nothing was created")
        elif action.kind == TODO:
            self._activity(hud.TAG_STOP, f"Dismissed {_activity_title(action)}", "nothing was created")
        elif action.countdown:   # Skip: its card reads SKIPPED
            self._activity(hud.TAG_STOP, f"Skipped {_kind_title(action)}", "nothing was sent")
        else:
            self._activity(hud.TAG_STOP, f"Dismissed {_kind_title(action)}", "nothing was sent")
        self._refresh_actions_ui()

    def open_action_source(self, action_id: str) -> None:
        """A card's Open (tools row): its own link, checked again, in the browser; never by itself."""
        action = self._action(action_id)
        if action is None or self.state == STATE_QUITTING:
            return
        link = action.link or (self._google_link(action_id) if action.countdown else "")
        if not link:
            return
        now = self._click_now()
        previous, self._source_opened_at[action_id] = self._source_opened_at.get(action_id, -math.inf), now
        if now - previous < _SOURCE_OPEN_GUARD_S:
            logger.info("Ignored a second Open of action %s within 1 s", action_id)
            return
        url = QUrl(link, QUrl.ParsingMode.StrictMode)
        host = url.host(QUrl.ComponentFormattingOption.FullyEncoded).rstrip(".").casefold()
        if (not link_allowed(link, self.config.actions.link_hosts) or not url.isValid()
                or url.scheme() != "https" or host != link_host(link)):
            logger.info("Not opening the link of action %s: not an allowed https link", action_id)
            return
        logger.info("Open the link of action %s (%s)", action_id, action.kind)
        QDesktopServices.openUrl(url)

    def copy_action_text(self, action_id: str) -> None:
        """A card's Copy (tools row): the drafted text as the card shows it on the clipboard;
        decides nothing."""
        action = self._current(action_id)
        if action is None or not action.body or self.state == STATE_QUITTING:
            return
        if not (self._copy_label(action) if action.countdown else copy_text(action)):
            return
        QGuiApplication.clipboard().setText(action.body)
        card = self.window.reading.action_card(action_id)
        if card is not None:
            card.show_copied()
        self._activity(hud.TAG_DONE, copied_text(action), kind_label(action).upper())
        logger.info("Copied the text of action %s (%s, %d characters)", action_id, action.kind,
                    len(action.body))

    def open_action_link(self, link: str) -> None:
        if not link.startswith("https://"):
            logger.info("Not opening an event link that is not https")
            return
        logger.info("Open the calendar event")
        QDesktopServices.openUrl(QUrl(link))

    # ---- the Edit dialog --------------------------------------------------------------------------

    def edit_action(self, action_id: str) -> None:
        """A card's Edit: the window-modal Edit dialog of an RSVP, Move or Cancel. Save keeps
        the changes on the card (memory only, same id); nothing is sent."""
        if self.state == STATE_QUITTING or self._dialog is not None:
            return
        if self._countdown is not None or self._jobs or self._connect_running:
            # The dialog is window-modal: beside another card's countdown it would cover that
            # card's Undo while the countdown runs out.
            logger.info("Ignored Edit of action %s: another card's approval or sign-in is running", action_id)
            return
        action = self._current(action_id)
        if action is None or not action.countdown or self._store.is_decided(action_id):
            return
        hour24 = self.config.display.hour24
        date_text, start_text, end_text = _time_texts(action, hour24)
        view = card_view(action, self._now().date())
        check_text, check_tip, _warn = self._check_view(action)
        dialog = hud.EditDialog(action_id, action.kind, kind_label(action), view.title,
                                answer=action.field("answer", "yes"), notify=action.field("notify", "all"),
                                date_text=date_text, start_text=start_text, end_text=end_text,
                                note=action.body, check_text=check_tip or check_text, hour24=hour24,
                                parent=self.window)
        dialog.saved.connect(self._on_edit_saved)
        dialog.finished.connect(self._on_edit_closed)
        self._dialog = dialog
        logger.info("Edit action %s (%s)", action_id, action.kind)
        dialog.open()
        self._sync_card_locks()

    def _on_edit_saved(self, action_id: str, values: Any) -> None:
        """Save in the Edit dialog: checked like a line; a problem keeps the dialog open."""
        dialog = self._dialog
        if dialog is None or dialog.action_id != action_id:
            return
        page = self._action(action_id)
        if page is None or self.state == STATE_QUITTING:
            dialog.reject()
            return
        if action_id in self._jobs or self._store.is_decided(action_id):
            dialog.show_error("This card is already being carried out or decided; nothing was changed")
            return
        values = dict(values) if isinstance(values, Mapping) else {}
        try:
            start = end = None
            if page.kind == MOVE:
                start, end = parse_time_range(values.get("date", ""), values.get("start", ""), values.get("end", ""))
            edit = ActionEdit(answer=values.get("answer") if page.kind == RSVP else None,
                              notify=values.get("notify"), start=start, end=end, body=values.get("note", ""))
            edited = apply_edit(page, edit)
        except (EditInvalid, EditError) as exc:
            dialog.show_error(str(exc))
            return
        if edited == page:
            self._edits.pop(action_id, None)
        else:
            self._edits[action_id] = edit
        dialog.accept()
        self._hints.pop(action_id, None)
        card = self.window.reading.action_card(action_id)
        if card is not None:
            view = card_view(edited, self._now().date())
            card.set_texts(view.kind_label + (EDITED_MARK if action_id in self._edits else ""), view.title,
                           view.detail)
            card.set_body(view.body)
        self._show_card_state(action_id)
        logger.info("Edited action %s (%s)", action_id, edited.kind)
        self._activity(hud.TAG_DONE, f"Edited: {_kind_title(edited)}",
                       f"{kind_label(edited).upper()} {DOT} nothing was sent")
        self._refresh_actions_ui()

    def _on_edit_closed(self, _result: int = 0) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.deleteLater()
        if self.state != STATE_QUITTING:
            self._sync_card_locks()

    # ---- results from the action worker ------------------------------------------------------------

    def _on_calendar_status(self, signed_in: bool) -> None:
        if self.state == STATE_QUITTING:
            return
        self._calendar_signed_in = bool(signed_in)
        self._update_service_chips()

    def _on_account_status(self, statuses: Any) -> None:
        """Which accounts are signed in (and why not): the cards' check lines and buttons."""
        if self.state == STATE_QUITTING or not isinstance(statuses, Mapping):
            return
        for alias, (signed_in, problem, message) in statuses.items():
            previous = self._accounts_state.get(alias)
            if not signed_in and not problem and previous is not None and not previous[0]:
                problem, message = previous[1], previous[2]   # keep why the last sign-in failed
            self._accounts_state[alias] = (bool(signed_in), problem or "", message or "")
        self._refresh_countdown_cards()
        self._update_service_chips()
        self._request_peeks()

    def _request_peeks(self, actions: Sequence[ProposedAction] | None = None, *, force: bool = False) -> None:
        """Read the events of RSVP / Move / Cancel cards for their check lines: only signed-in
        accounts (never a sign-in), only cards still waiting, one read job at a time per card."""
        worker = self._calendar_worker
        if worker is None or self.state == STATE_QUITTING:
            return
        if actions is None:
            actions = [self._effective(action) for action in self._actions]
        todo = []
        for action in actions:
            if (not action.countdown or action.id in self._peeking or action.id in self._jobs
                    or self._store.is_decided(action.id) or (action.id in self._checks and not force)):
                continue
            if self._executor.readiness(action) or not self._account_signed_in(action.account):
                continue
            todo.append(action)
        if not todo:
            return
        self._peek_seq += 1
        self._peeking.update(action.id for action in todo)
        worker.peek(_PeekJob(self._peek_seq, tuple(todo)))
        for action in todo:
            self._show_card_state(action.id)

    def _on_event_peek(self, _request_id: int, results: Any) -> None:
        """Google's view of the cards' events came back: their check lines and buttons."""
        if self.state == STATE_QUITTING or not isinstance(results, Mapping):
            return
        today = self._now().date()
        accounts_changed = False
        for action_id, (details, error) in results.items():
            self._peeking.discard(action_id)
            action = self._current(action_id)
            if action is None:
                continue
            if details is not None:
                self._checks[action_id] = check_event(action, details, today)
                logger.info("Checked the event of action %s with Google", action_id)
                continue
            problem = getattr(error, "problem", "") or ""
            if isinstance(error, CalendarNotSignedIn) or problem in _SIGN_IN_PROBLEMS:
                # The account's saved sign-in is missing or was rejected: Sign in again.
                self._checks.pop(action_id, None)
                text = "" if problem in ("", PROBLEM_SIGNED_OUT) else str(error)
                previous = self._accounts_state.get(action.account)
                if not text and previous is not None and not previous[0] and previous[1]:
                    problem, text = previous[1], previous[2]   # keep why it was rejected (expired, ...)
                self._accounts_state[action.account] = (False, "" if problem == PROBLEM_SIGNED_OUT else problem,
                                                        text)
                accounts_changed = True
            else:
                self._checks[action_id] = check_failure(action, error)
            logger.info("Could not check the event of action %s (%s)", action_id, type(error).__name__)
        self._refresh_countdown_cards()
        if accounts_changed:
            self._update_service_chips()

    def _on_action_progress(self, action_id: str, stage: str) -> None:
        if self.state == STATE_QUITTING or action_id not in self._jobs:
            return
        running = self._running.get(action_id)
        alias = account_of(running[0]) if running is not None else DEFAULT_ACCOUNT
        if stage == _STAGE_SIGNIN:
            self._jobs[action_id] = _STAGE_SIGNIN
            self._activity(hud.TAG_WAIT, "Google sign-in", "finish in your browser")
        elif stage == _STAGE_SIGNED_IN:
            self._jobs[action_id] = _STAGE_WORKING
            self._accounts_state[alias] = (True, "", "")
            if alias == DEFAULT_ACCOUNT:
                self._calendar_signed_in = True
                self._connect_note = ""
                self._activity(hud.TAG_DONE, "Signed in to Google Calendar")
                self._request_agenda()   # queued behind this approval
            else:
                self._activity(hud.TAG_DONE, f"Signed in to Google ({alias} account)")
            self._update_service_chips()
        else:
            return   # the call itself is on its way: the card already reads WORKING...
        self._show_card_state(action_id)
        self._render_agenda()

    def _on_action_result(self, action_id: str, result: ExecResult | None, error: ExecError | None,
                          status: str) -> None:
        if self.state == STATE_QUITTING:
            return
        self._jobs.pop(action_id, None)
        running = self._running.pop(action_id, None)
        action = running[0] if running is not None else self._current(action_id)
        writes_running = running[1] if running is not None else bool(action is not None and action.countdown)
        if writes_running and action is not None:
            self._countdown_result(action, result, error, status)
        else:
            self._calendar_result(action_id, action, result, error, status)
        self._show_card_state(action_id)
        self._sync_card_locks()
        if self._calendar_worker is not None:
            self._calendar_worker.check_sign_in()   # a revoked sign-in turns the chip amber again
        self._refresh_actions_ui()
        self._render_agenda()   # a sign-in that waited may be over

    def _calendar_result(self, action_id: str, action: ProposedAction | None, result: ExecResult | None,
                         error: ExecError | None, status: str) -> None:
        """A Calendar event or a Todo's block: saved here (created / exists / failed), as before."""
        title = _activity_title(action) if action is not None else "the event"
        today = self._now().date()
        block = action is not None and action.kind == TODO
        event = action.block_event() if block else action   # a Todo's block: its time is the detail
        detail = event.describe(today) if event is not None else ""
        kind = action.kind if action is not None else ""
        account = action.account if action is not None else ""
        if result is not None and error is None:
            self._store.set(action_id, status, link=result.link, kind=kind, account=account)
            if status == STATUS_EXISTS:
                self._activity(hud.TAG_DONE, f"Already on the calendar: {title}", detail)
            elif block:
                self._activity(hud.TAG_DONE, f"Added a block for {title}", detail)
            else:
                self._activity(hud.TAG_DONE, f"Added {title}", detail)
            self._request_agenda()   # the new event may be on today's agenda
        else:
            message = (str(error) if error is not None else "") or "unknown error"
            self._store.set(action_id, STATUS_FAILED, message=message, kind=kind, account=account)
            failed = f"Couldn't add a block for {title}" if block else f"Couldn't add {title}"
            self._activity(hud.TAG_STOP, failed, _short(message))

    def _countdown_result(self, action: ProposedAction, result: ExecResult | None, error: ExecError | None,
                          status: str) -> None:
        """An RSVP, Move or Cancel: the worker saved sent / failed / unknown over "running"."""
        self._hints.pop(action.id, None)
        label = kind_label(action).upper()
        if result is not None and error is None:
            self._carried[action.id] = action   # the card keeps showing what was sent
            text = result.result_text or result_text(action, STATUS_SENT) or "Done"
            self._activity(hud.TAG_DONE, f"{text}: {_kind_title(action)}", label)
            if account_of(action) == DEFAULT_ACCOUNT:
                self._request_agenda()   # the agenda may show the change
            return
        message = (str(error) if error is not None else "") or "unknown error"
        problem = getattr(error, "problem", "") or ""
        if problem in _SIGN_IN_PROBLEMS:
            self._accounts_state[action.account] = (False, "" if problem == PROBLEM_SIGNED_OUT else problem,
                                                    "" if problem == PROBLEM_SIGNED_OUT else message)
            self._update_service_chips()
            self._refresh_countdown_cards()   # every card of that account: Sign in, and why
        if status == STATUS_UNKNOWN:
            self._activity(hud.TAG_WAIT, f"Unknown: {_action_phrase(action)}", UNKNOWN_CARD_TEXT)
        else:
            self._activity(hud.TAG_STOP, f"Couldn't {_VERBS.get(action.kind, 'do')} {_kind_title(action)}",
                           _short(message))
        self._request_peeks([action], force=True)   # Google's view now, before any Retry

    # ---- sign-in per account ---------------------------------------------------------------------

    def _signing_in(self, alias: str | None = None) -> bool:
        """A browser sign-in is queued or open (``alias``: that account's)."""
        if self._connect_alias is not None and alias in (None, self._connect_alias):
            return True
        for action_id, stage in self._jobs.items():
            if stage != _STAGE_SIGNIN:
                continue
            running = self._running.get(action_id)
            if alias is None or running is None or account_of(running[0]) == alias:
                return True
        return False

    def _set_signed_in(self, signed_in: bool) -> None:
        if signed_in != self._calendar_signed_in:
            self._calendar_signed_in = signed_in
            self._update_service_chips()

    def connect_calendar(self) -> None:
        """The agenda's Connect link: the personal account's Google sign-in, only on this click."""
        if self.state != STATE_READING or self._signing_in() or self._countdown is not None:
            return
        if self._start_calendar() is None:
            self._render_agenda()
            return
        logger.info("Connect Google Calendar")
        self._start_sign_in(DEFAULT_ACCOUNT)

    def sign_in_account(self, action_id: str) -> None:
        """A card's Sign in link: that account's Google sign-in in the browser, only on this click."""
        if self.state == STATE_QUITTING or self._signing_in() or self._countdown is not None or self._jobs:
            return
        action = self._current(action_id)
        if action is None or not action.countdown or self._store.is_decided(action_id):
            return
        reason = self._executor.readiness(action)
        if not reason and self._start_calendar() is None:
            reason = CALENDAR_SETUP_NOTE
        if reason:
            self._hint(action_id, NOT_READY_NOTE if reason == self._check_view(action)[0] else reason)
            return
        logger.info("Sign in to Google for action %s (%s account)", action_id, logged_alias(action.account))
        self._start_sign_in(action.account, card_id=action_id)

    def _start_sign_in(self, alias: str, *, card_id: str = "") -> None:
        worker = self._start_calendar()
        if worker is None:
            return
        self._connect_alias = alias
        self._connect_card = card_id
        if alias == DEFAULT_ACCOUNT:
            self._connect_note = ""
        self._sync_card_locks()
        self._refresh_countdown_cards()
        self._update_service_chips()
        worker.connect(alias)
        self._render_agenda()

    def _on_account_connect(self, alias: str, stage: str, message: str, problem: str) -> None:
        if self.state == STATE_QUITTING or self._connect_alias != alias:
            return
        personal = alias == DEFAULT_ACCOUNT
        if stage == _CONNECT_SIGN_IN:
            self._activity(hud.TAG_WAIT, "Google sign-in",
                           "finish in your browser" if personal else f"{alias} account {DOT} finish in your browser")
            return
        card_id = self._connect_card
        self._connect_alias, self._connect_card = None, ""
        if stage == _CONNECT_FAILED:
            note = message or "the sign-in did not finish"
            self._accounts_state[alias] = (False, problem or PROBLEM_FAILED, note)
            if personal:
                self._connect_note = note
            if card_id:   # the card whose click started it says why, in full
                self._hints[card_id] = sign_in_note(alias, problem=problem or PROBLEM_FAILED, message=note)
            self._activity(hud.TAG_STOP, "Google sign-in did not finish" if personal
                           else f"Google sign-in did not finish ({alias} account)", _short(note))
            if self._calendar_worker is not None:
                self._calendar_worker.check_sign_in()
        else:
            self._accounts_state[alias] = (True, "", "")
            if stage == _CONNECT_SIGNED_IN:
                self._activity(hud.TAG_DONE, "Signed in to Google Calendar" if personal
                               else f"Signed in to Google ({alias} account)")
            if personal:
                self._set_signed_in(True)
                self._request_agenda()
            card = self.window.reading.action_card(card_id) if card_id else None
            if card is not None:
                self._hints[card_id] = SIGNED_IN_NOTE.format(button=card.approve_text())
            self._request_peeks([self._effective(action) for action in self._actions
                                 if action.account == alias], force=True)
        self._sync_card_locks()
        self._refresh_countdown_cards()
        self._update_service_chips()
        self._render_agenda()

    # ---- agenda: TODAY / TOMORROW and DEADLINES -----------------------------------------------

    def _agenda_active(self) -> bool:
        """The reading screen is up and visible: the only time the agenda refreshes by itself."""
        window = self.window
        return (self.state == STATE_READING and window.is_reading_view() and window.isVisible()
                and not window.isMinimized())

    def _sync_agenda_timer(self) -> None:
        """The agenda timer runs only while the reading screen is visible; it catches up when shown."""
        if not self._agenda_active():
            self._agenda_timer.stop()
            return
        if not self._agenda_timer.isActive():
            self._agenda_timer.start()
            self._on_agenda_tick()

    def _on_agenda_tick(self) -> None:
        """Every minute while visible: read again when 10 minutes have passed, re-draw the times."""
        if not self._agenda_active():
            self._agenda_timer.stop()
            return
        if time.monotonic() - self._agenda_requested_at >= _AGENDA_REFRESH_S:
            self._request_agenda()
        self._render_agenda()

    def _request_agenda(self) -> None:
        """Read the agenda on the calendar worker: one read at a time, never a sign-in."""
        if self.state != STATE_READING or not self.config.calendar.enabled:
            return
        worker = self._start_calendar()
        if worker is None:
            return
        if self._agenda_job is not None:
            self._agenda_again = True   # once the read on its way answers
            return
        agenda = self.config.agenda
        start, end = _agenda_fetch_range(self._now(), agenda)
        self._agenda_seq += 1
        self._agenda_job = _AgendaJob(self._agenda_seq, start, end, tuple(agenda.calendars))
        self._agenda_requested_at = time.monotonic()
        if self._agenda_events is None and (self._agenda_status == _AGENDA_INIT or (
                self._agenda_status == _AGENDA_SIGNED_OUT and self._calendar_signed_in)):
            self._agenda_status = _AGENDA_LOADING
        worker.list_agenda(self._agenda_job)

    def _on_agenda_result(self, request_id: int, events: list[CalendarEvent] | None, problem: str,
                          message: str) -> None:
        job = self._agenda_job
        if self.state == STATE_QUITTING or job is None or job.request_id != request_id:
            return   # stale: not the read this controller waits for
        self._agenda_job = None
        if not problem:
            self._agenda_events, self._agenda_range = list(events or ()), (job.start, job.end)
            self._agenda_status, self._agenda_problem, self._connect_note = _AGENDA_OK, "", ""
            logger.debug("Agenda: %d event(s)", len(self._agenda_events))
            self._set_signed_in(True)
        elif problem == _PROBLEM_ERROR:
            self._agenda_status, self._agenda_problem = _AGENDA_ERROR, message   # older rows stay
        else:
            self._agenda_events = self._agenda_range = None
            self._agenda_status = _AGENDA_SETUP if problem == _PROBLEM_SETUP else _AGENDA_SIGNED_OUT
            self._agenda_problem = message
            if problem == _PROBLEM_SIGNED_OUT and not self._signing_in(DEFAULT_ACCOUNT):
                self._set_signed_in(False)
        if self._agenda_again:
            self._agenda_again = False
            self._request_agenda()
        self._render_agenda()

    def _agenda_covers(self, start: datetime, end: datetime) -> bool:
        """The last read covers the agenda day from ``start`` to ``end``."""
        if self._agenda_range is None:
            return False
        first, last = self._agenda_range
        try:
            return first <= start and end <= last
        except TypeError:   # naive and aware times mixed: never ask again because of it
            return True

    def _render_agenda(self) -> None:
        """TODAY / TOMORROW (rows or one state line) and DEADLINES, as of now."""
        panel = self.window.reading.agenda_panel
        agenda = self.config.agenda
        now = self._now()
        label, start, end = agenda_window(now, agenda.evening_from_hour)
        events = self._agenda_events
        if events is not None and not self._agenda_covers(start, end):
            events = None   # the day moved past what was read (after midnight, at 18:00): read again
            if self._agenda_job is None:
                self._request_agenda()
        panel.set_title(label)
        if events is None:
            text, color, link, tip = self._agenda_line()
            panel.set_meta("")
            # Google's error text and sign-in messages: never read as HTML.
            panel.set_message(text, color, link=link, tooltip=hud.plain_tooltip(tip))
        else:
            self._show_agenda_rows(panel, _agenda_rows(events, now, start, end, self.config.display.hour24))
        self._show_deadlines(panel, now)

    def _agenda_line(self) -> tuple[str, str, str, str]:
        """(text, colour, link, tooltip) of TODAY when there are no rows to show."""
        status = self._agenda_status
        if not self.config.calendar.enabled:
            return AGENDA_OFF_TEXT, hud.TEXT_DIM, "", ""
        if status == _AGENDA_SETUP or not self._calendar_configured():
            return AGENDA_SETUP_TEXT, hud.TEXT_DIM, "", self._agenda_problem
        if self._signing_in(DEFAULT_ACCOUNT):
            return AGENDA_SIGN_IN_TEXT, hud.TEXT_SOFT, "", "Finish the Google sign-in in your browser"
        if status == _AGENDA_SIGNED_OUT:
            if self._connect_note:
                return _short(self._connect_note, 70), hud.AMBER, AGENDA_CONNECT_LINK, self._connect_note
            return (AGENDA_CONNECT_TEXT, hud.TEXT_SOFT, AGENDA_CONNECT_LINK,
                    "Opens the Google sign-in in your browser")
        if status == _AGENDA_ERROR:
            return AGENDA_ERROR_TEXT, hud.AMBER, "", self._agenda_problem
        return AGENDA_LOADING_TEXT, hud.TEXT_DIM, "", ""

    def _show_agenda_rows(self, panel: hud.AgendaPanel, rows: list[hud.AgendaRowInfo]) -> None:
        stale = self._agenda_status == _AGENDA_ERROR   # the last read failed: older rows
        if stale:
            panel.set_meta("not updated", hud.AMBER)
        else:
            panel.set_meta(_plural(len(rows), "block") if rows else "", hud.TEXT_DIM)
        panel.meta_label.setToolTip(hud.plain_tooltip(self._agenda_problem) if stale else "")
        if rows:
            panel.set_events(rows)
        else:
            panel.set_message(AGENDA_EMPTY_TEXT, hud.TEXT_DIM)

    def _show_deadlines(self, panel: hud.AgendaPanel, now: datetime) -> None:
        """The page's deadlines and calendar events named like deadlines, soonest first."""
        agenda = self.config.agenda
        from_calendar = deadlines_from_events(self._agenda_events or (), now, agenda.deadline_days,
                                              agenda.deadline_keywords)
        items = merge_deadlines(self._page_deadlines, from_calendar, now, agenda.deadline_days)
        panel.set_deadlines(_deadline_rows(items, now), f"No deadlines in the {_next_days(agenda.deadline_days)}")
        panel.set_deadlines_meta(_plural(agenda.deadline_days, "day"))   # the horizon: "14 DAYS"

    # ---- scripts and speech -------------------------------------------------------------

    def _build_script(self, include_note: bool) -> Script:
        briefing = self._briefing
        assert briefing is not None
        return build_script(
            briefing, now=self._now(), expected_run=self.expected_run,
            freshness=self._freshness(briefing),
            include_note=include_note, ignore_names=self.config.sections.ignore,
            announce=self.config.sections.announce, actions=self._pending_actions())

    def _refresh_prefetch(self) -> None:
        """Prepare audio for the briefing while the prompt shows (rebuilt when it changes)."""
        if self.state in (STATE_READING, STATE_QUITTING) or self._briefing is None:
            return
        # No stale note while still waiting for the run; a one-off re-check keeps it.
        include_note = not self._polling or self.expected_run is None
        script = self._build_script(include_note)
        unchanged = self._prefetch is not None and script.sections == self._prefetch.sections
        self._prefetch = script
        if not unchanged:
            self._submit_all(script)
            if not include_note:
                self._warm_noted_intro(script)

    def _warm_noted_intro(self, script: Script) -> None:
        """Also synthesize the intro with the stale note, so Read now during the wait starts at once.

        Audio files are content-addressed, so the reading script's intro is then a cache hit.
        """
        noted = self._build_script(include_note=True)
        if self._tts is None or not noted.stale or noted.sections[0] == script.sections[0]:
            return
        self._tts.submit(self._generation, _WARMUP_INDEX, noted.sections[0])

    def _submit_all(self, script: Script) -> None:
        """New TTS generation for ``script``: non-ignored sections first, ignored ones last."""
        self._generation += 1
        self._audio, self._failed = {}, set()
        self._audio_ready_logged = False
        if self._tts is None:
            return
        self._tts.set_generation(self._generation)
        order = script.section_indices(False)
        order += [index for index in range(len(script.sections)) if index not in order]
        for index in order:
            self._tts.submit(self._generation, index, script.sections[index])
        logger.info("Preparing audio for %d section(s)", len(order))
        self._activity(hud.TAG_RUN, "Preparing audio", _plural(len(order), "section"))

    def _on_tts_result(self, generation: int, index: int, audio: SectionAudio | None,
                       error: str | None) -> None:
        if generation != self._generation or self.state == STATE_QUITTING:
            return
        if index == _WARMUP_INDEX:   # only warmed the synthesizer's cache
            return
        if audio is None:
            self._on_tts_failed(index, error)
        else:
            self._on_tts_audio(index, audio)
        self._log_audio_ready()
        self._refresh_views()

    def _on_tts_audio(self, index: int, audio: SectionAudio) -> None:
        self._audio[index] = audio
        if audio.segments and audio.engine != self._engine:
            logger.info("Speech engine: %s", "Windows offline voice (SAPI)" if audio.engine == SAPI
                        else "edge-tts online voice")
            self._engine = audio.engine
        if self._script is not None:
            self.player.set_audio(index, audio)

    def _on_tts_failed(self, index: int, error: str | None) -> None:
        logger.warning("No audio for section %d: %s", index, error)
        self._failed.add(index)
        script = self._script or self._prefetch
        if script is not None and 0 <= index < len(script.sections):
            self._activity(hud.TAG_STOP, "Couldn't generate audio", _section_title(script.sections[index]))
        if self._script is not None:
            self.player.mark_failed(index)
            self._flash_note("Couldn't generate audio for one section")

    def _log_audio_ready(self) -> None:
        script = self._script or self._prefetch
        if script is None or self._audio_ready_logged:
            return
        needed = script.section_indices(False)
        if any(index not in self._audio and index not in self._failed for index in needed):
            return
        self._audio_ready_logged = True
        if all(index in self._failed for index in needed):
            return
        voice = "offline voice" if self._uses_offline_voice() else "edge voice"
        self._activity(hud.TAG_DONE, "Audio ready", f"{voice} {DOT} {_plural(len(needed), 'section')}")

    def _uses_offline_voice(self) -> bool:
        return any(audio.engine == SAPI and audio.segments for audio in self._audio.values())

    # ---- prompt ------------------------------------------------------------------------

    def show_prompt(self, take_focus: bool) -> None:
        if self.state in (STATE_READING, STATE_QUITTING):
            return
        self._snooze_timer.stop()
        self.state = STATE_PROMPT
        if not self.startup_error and self._briefing is None and not self.fetching:
            self._start_fetch(self.expected_run)   # retry after an earlier failure
        elif self._needs_recheck():
            # One look, not a new 15-minute wait: the headline stays "may be stale"
            # until a fresh page arrives (e.g. a briefing that landed after the poll).
            logger.info("Re-checking Notion: the cached briefing is stale")
            self._start_fetch(None)
        self.window.show_prompt_view()
        self._refresh_prompt()
        self.window.place()
        if take_focus:
            force_foreground(self.window)
        else:
            show_without_activating(self.window)
        self.window.keep_on_screen()
        self.window.prompt.setFocus(Qt.FocusReason.OtherFocusReason)
        self._start_prompt_timers()
        self._update_tray()
        self._sync_agenda_timer()
        logger.info("Prompt shown (%s)", "taking focus" if take_focus else "without taking focus")
        self.record_shown()   # only the first show of a slot is kept

    def _start_prompt_timers(self) -> None:
        if self.startup_error:   # Later is disabled, so there is nothing to count down to
            self._stop_prompt_timers()
            return
        self._ignore_timer.start(self.config.prompt.ignore_after_seconds * 1000)
        self._tick_timer.start()
        self._update_countdown()

    def _stop_prompt_timers(self) -> None:
        self._ignore_timer.stop()
        self._tick_timer.stop()
        self.window.prompt.set_countdown("")

    def _update_countdown(self) -> None:
        if not self._ignore_timer.isActive():
            self.window.prompt.set_countdown("")
            return
        seconds = math.ceil(max(0, self._ignore_timer.remainingTime()) / 1000)
        minutes = self.config.prompt.later_short_minutes
        self.window.prompt.set_countdown(
            f"Asking again in {minutes} min if there is no answer ({seconds // 60}:{seconds % 60:02d})")

    def later(self, minutes: int) -> None:
        """Hide the prompt and bring it back after ``minutes``."""
        if self.state != STATE_PROMPT or self.startup_error:
            return
        minutes = max(1, int(minutes))
        self._stop_prompt_timers()
        self.window.hide()
        self.state = STATE_SNOOZED
        self._snooze_timer.start(minutes * 60_000)
        at = format_time(self._now() + timedelta(minutes=minutes))
        self._update_tray(f"Briefing: asking again at {at}")
        logger.info("Later (%d min): asking again at %s", minutes, at)

    def _on_prompt_ignored(self) -> None:
        if self.state != STATE_PROMPT:
            return
        logger.info("Prompt ignored for %d s", self.config.prompt.ignore_after_seconds)
        self.later(self.config.prompt.later_short_minutes)

    def _on_snooze_timeout(self) -> None:
        if self.state == STATE_SNOOZED:
            self.show_prompt(take_focus=self.config.prompt.refocus_on_reprompt)

    def read_now(self) -> None:
        if self.state != STATE_PROMPT or self.startup_error:
            return
        logger.info("Read now")
        self.enter_reading()

    def dismiss(self) -> None:
        logger.info("Dismissed")
        self.record_answer("dismissed")
        self.quit()

    def _on_close_requested(self) -> None:
        if self.state == STATE_READING:
            logger.info("Window closed on the reading screen")
            self.done()
        elif self.state == STATE_PROMPT and self.startup_error:
            self.dismiss()
        elif self.state == STATE_PROMPT:
            logger.info("Window closed on the prompt")
            self.later(self.config.prompt.later_short_minutes)

    # ---- prompt text ----------------------------------------------------------------------

    def _refresh_views(self) -> None:
        if self.state in (STATE_PROMPT, STATE_SNOOZED):
            self._refresh_prompt()
        elif self.state == STATE_READING:
            self._update_reading_controls()
        self._update_service_chips()

    def _refresh_prompt(self) -> None:
        headline, detail, status, is_error = self._prompt_text()
        prompt = self.window.prompt
        prompt.set_content(headline, detail, status, is_error, can_read=not self.startup_error)
        prompt.set_state(self._prompt_orb_state())
        prompt.set_pending(len(self._pending_actions()))
        if self.state == STATE_PROMPT:
            self.window.fit_prompt()

    def _prompt_orb_state(self) -> str:
        """Waiting on the user; working while the first fetch runs; standby when there is a problem."""
        if self.startup_error:
            return hud.ORB_STANDBY
        if self._briefing is None:
            return hud.ORB_WORKING if self._fetch_error is None else hud.ORB_STANDBY
        return hud.ORB_WAITING

    def _prompt_text(self) -> tuple[str, str, str, bool]:
        """(headline, detail, status, status_is_error) for the current situation."""
        if self.startup_error:   # "Notion token missing." / "Notion page ID missing." and what to do
            headline, detail = _split_startup_error(self.startup_error)
            return headline, detail, "", False
        briefing = self._briefing
        run = self.expected_run or (briefing.header.run if briefing else None)
        if briefing is not None:
            return self._prompt_text_for(briefing, _ready_headline(run))
        if self._fetch_error is None:
            return _ready_headline(run), "Checking Notion...", "", False
        retry = f"Retrying {self._interval_text()}." if self.fetching else ""
        return "Couldn't load your briefing.", str(self._fetch_error), retry, True

    def _prompt_text_for(self, briefing: Briefing, ready: str) -> tuple[str, str, str, bool]:
        now = self._now()
        fresh = self._freshness(briefing).fresh
        label = updated_label(briefing.header, now) + self._run_suffix(briefing)
        audio_status, audio_error = self._audio_status()
        if not self.expected_run or fresh:
            return ready, label, audio_status, audio_error
        if self._polling:
            status = (f"Checking {self._interval_text()} until {format_time(self._poll_deadline)}. "
                      "Read now plays the last one.")
            return (f"Your {self.expected_run} briefing hasn't arrived yet.",
                    self._last_update_text(briefing, now), status, False)
        return (f"Your {self.expected_run} briefing may be stale. Hear it anyway?",
                label, audio_status, audio_error)

    def _run_suffix(self, briefing: Briefing) -> str:
        run = briefing.header.run
        return f" - {run} run" if self.expected_run and run and run != self.expected_run else ""

    @staticmethod
    def _last_update_text(briefing: Briefing, now: datetime) -> str:
        when = describe_updated(briefing.header.updated_at, now)
        if not when:
            return updated_label(briefing.header, now)
        run = f" ({briefing.header.run} run)" if briefing.header.run else ""
        return f"Last update: {when}{run}"

    def _audio_status(self) -> tuple[str, bool]:
        script = self._prefetch
        needed = script.section_indices(False) if script is not None else []
        if script is None or any(i not in self._audio and i not in self._failed for i in needed):
            return "Preparing audio...", False
        failed = [i for i in needed if i in self._failed]
        if failed:
            whole = len(failed) == len(needed)
            return ("Couldn't prepare the audio" if whole else "Audio ready, but part of it failed"), True
        return ("Audio ready (offline voice)" if self._uses_offline_voice() else "Audio ready"), False

    def _interval_text(self) -> str:
        seconds = self.config.polling.interval_seconds
        if seconds == 60:
            return "every minute"
        if seconds % 60 == 0:
            return f"every {seconds // 60} minutes"
        return f"every {seconds} seconds"

    # ---- reading -------------------------------------------------------------------------

    def enter_reading(self) -> None:
        """Switch to the reading screen (Read now, --now, or an activation with now=true)."""
        if self.state in (STATE_READING, STATE_QUITTING):
            return
        self._stop_prompt_timers()
        self._snooze_timer.stop()
        was_visible = self.window.isVisible()
        self.state = STATE_READING
        if not self.startup_error:
            self._read_slot = self.record_answer("read")   # Read now, --now, or the hotkey
        self.window.show_reading_view()
        if not was_visible:
            self.window.place()
        self._update_tray()
        self._render_agenda()
        self._request_agenda()
        self._sync_agenda_timer()
        if self.startup_error:
            self._show_reading_error(self.startup_error, can_retry=False)
        elif self._briefing is None:
            self._start_loading(restart=False)
        elif self._worth_rechecking_first():
            self._recheck_before_reading()
        else:
            self._begin_reading()

    def _worth_rechecking_first(self) -> bool:
        """The briefing we have is not fresh and Notion may have a newer one by now."""
        if self._cached_is_fresh():
            return False
        if self.fetching:
            return self._fetch_attempts == 0   # its first answer is on the way
        return not self._checked_recently()

    def _recheck_before_reading(self) -> None:
        """Ask Notion once more before freezing a stale copy, but never wait long for it."""
        logger.info("Re-checking Notion before reading: the cached briefing is stale")
        self._show_loading()
        if not self.fetching:
            self._start_fetch(None)
        self._recheck_timer.start(_RECHECK_WAIT_MS)

    def _on_recheck_timeout(self) -> None:
        if self.state == STATE_READING and self._script is None and self._briefing is not None:
            logger.info("Notion did not answer in time; reading the briefing fetched earlier")
            self._begin_reading()

    def _start_loading(self, *, restart: bool) -> None:
        self._show_loading()
        if restart or not self.fetching or self._fetch_error is not None:
            # Take the first successful fetch now (freshness is still judged for the
            # note) instead of waiting out a polling interval after an error. A fetch
            # whose first attempt is still running (no briefing, no error) is left to
            # finish: that attempt hands over whatever it finds.
            self._start_fetch(None)

    def _show_loading(self) -> None:
        reading = self.window.reading
        self._reading_error = False
        reading.set_header(f"{self.expected_run} briefing" if self.expected_run else "Briefing", "")
        reading.show_message(LOADING_TEXT)
        reading.set_status("Loading")
        reading.set_speech("")
        reading.set_controls("Play", False, False)
        reading.set_read_everything(False, "Available once the briefing has loaded")
        self._update_reading_controls()

    def _on_loading_error(self) -> None:
        """A fetch attempt failed while the reading screen waits for a briefing."""
        if self.state != STATE_READING or self._script is not None or self._fetch_error is None:
            return
        if self._briefing is not None:
            logger.info("Notion could not be reached; reading the briefing fetched earlier")
            self._begin_reading()
            return
        message = str(self._fetch_error)
        if self.fetching:
            message += f" Retrying {self._interval_text()}."
        self._show_reading_error(message, can_retry=True)

    def _show_reading_error(self, message: str, *, can_retry: bool) -> None:
        reading = self.window.reading
        self._reading_error = True
        if not reading.title.text():
            reading.set_header("Briefing", "")
        reading.show_message(message, is_error=True)
        reading.set_status("Couldn't load the briefing", is_error=True)
        reading.set_speech("")
        reading.set_controls("Retry", can_retry, False)
        reading.set_read_everything(False, "Available once the briefing has loaded")
        self._update_reading_controls()

    def retry(self) -> None:
        if self.state != STATE_READING or self._script is not None or self.startup_error:
            return
        logger.info("Retry loading the briefing")
        self._start_loading(restart=True)

    def _begin_reading(self) -> None:
        """Freeze the briefing, render it and start playback."""
        self._stop_fetch()
        self._recheck_timer.stop()
        script = self._build_script(include_note=True)
        if self._prefetch is None or script.sections != self._prefetch.sections:
            self._submit_all(script)
        self._script = script
        self._include_ignored = False
        self._reading_error = False
        self._sections_started = set()
        self._last_section = None
        self._skip_pending = False
        logger.info("Reading the briefing: %d section(s)%s", len(script.section_indices(False)),
                    ", with a stale note" if script.stale else "")
        self._render_reading()
        self._start_playback(script.section_indices(False))

    def _render_reading(self) -> None:
        script, reading = self._script, self.window.reading
        assert script is not None
        title = f"{script.run_label} briefing" if script.run_label else "Briefing"
        stale = f"May be stale - {script.updated_label}" if script.stale else ""
        reading.set_header(title, script.updated_label, stale)
        reading.set_script(script, self._include_ignored)
        reading.set_speech("")
        self._update_read_everything()
        self._update_reading_controls()

    def _start_playback(self, queue_: list[int]) -> None:
        for index, audio in self._audio.items():
            self.player.set_audio(index, audio)
        for index in self._failed:
            self.player.mark_failed(index)
        self._has_played = False
        self.player.start(queue_)
        # Sections whose audio already failed while the prompt showed are skipped
        # at once; say so instead of silently jumping ahead (or straight to "Finished").
        failed = [index for index in queue_ if index in self._failed]
        if len(failed) == len(queue_) and failed:
            self._flash_note("Couldn't generate the audio", sticky=True)
        elif failed:
            self._flash_note("Couldn't generate audio for one section" if len(failed) == 1
                             else f"Couldn't generate audio for {len(failed)} sections")

    def _update_read_everything(self) -> None:
        script = self._script
        if script is None:
            enabled, tip = False, "Available once the briefing has loaded"
        elif not script.has_ignored:
            enabled, tip = False, "Nothing was skipped in this briefing"
        elif self._include_ignored:
            enabled, tip = False, "The skipped sections are already included"
        else:
            names = ", ".join(section.title for section in script.sections if section.ignored)
            enabled, tip = True, f"Also read the sections that are normally skipped: {names}"
        self.window.reading.set_read_everything(enabled, tip)

    def _update_reading_controls(self) -> None:
        if self.state != STATE_READING:
            return
        reading = self.window.reading
        if self._script is None:
            reading.set_activity_state(hud.ORB_STANDBY if self._reading_error else hud.ORB_WORKING, False)
            self._update_sections()
            self._update_telemetry()
            return
        state = self.player.state
        reading.set_controls(_PRIMARY_LABELS.get(state, "Play"), True, state in (PLAYING, PAUSED, WAITING))
        reading.set_status(self._reading_status(state), is_error=bool(self._note))
        reading.set_activity_state(_ORB_FOR_PLAYER.get(state, hud.ORB_STANDBY), state == PLAYING)
        self._update_sections()
        self._update_telemetry()

    def _reading_status(self, state: str) -> str:
        if state == WAITING:
            text = "Waiting for audio..." if self._has_played else "Preparing audio..."
        else:
            text = _STATUS_LABELS.get(state, "")
        if text and state in (PLAYING, PAUSED) and self._uses_offline_voice():
            text += " (offline voice)"
        if self._note:
            text = f"{text} - {self._note}" if text else self._note
        return text

    def _flash_note(self, note: str, *, sticky: bool = False) -> None:
        """Show ``note`` after the reading status (for a few seconds unless ``sticky``)."""
        self._note = note
        if sticky:
            self._note_timer.stop()
        else:
            self._note_timer.start(_NOTE_DURATION_MS)
        self._update_reading_controls()

    def _clear_note(self) -> None:
        self._note = self._pending_note()
        self._update_reading_controls()

    def _pending_note(self) -> str:
        return f"Your {self._pending_run} briefing is due" if self._pending_run else ""

    # ---- sections, steps and telemetry ---------------------------------------------------

    def _current_section(self) -> int | None:
        """The section being read (also while paused or waiting for its audio)."""
        if self.player.state in (PLAYING, PAUSED, WAITING):
            return self.player.current_section
        return None

    def _update_sections(self) -> None:
        """Section rows on the left and the step chips above the transcript."""
        script, reading = self._script, self.window.reading
        if script is None:
            reading.set_sections([], "")
            reading.set_steps([])
            return
        current = self._current_section()
        order = script.section_indices(self._include_ignored)
        rows = [self._section_row(index, section, current, index in order)
                for index, section in enumerate(script.sections)]
        reading.set_sections(rows, _plural(len(script.sections), "section"), current)
        reading.set_steps(self._steps(script, order, current))

    def _section_row(self, index: int, section: Section, current: int | None,
                     included: bool) -> hud.SectionRowInfo:
        title = _section_title(section)
        if not included:
            return hud.SectionRowInfo(title, "not read", hud.ROW_IGNORED, clickable=False)
        meta = "no audio" if index in self._failed else self._section_meta(section)
        if index == current:
            return hud.SectionRowInfo(title, meta, hud.ROW_CURRENT, flag="now")
        state = hud.ROW_PLAYED if index in self._sections_started else hud.ROW_UPCOMING
        return hud.SectionRowInfo(title, meta, state)

    def _section_meta(self, section: Section) -> str:
        if section.key == "intro":
            # Short enough for the narrowest section column: "AM run · 10:04 AM" / "updated 10:04 AM".
            run = self._script.run_label if self._script is not None else ""
            updated = self._briefing.header.updated_at if self._briefing is not None else None
            clock = _clock_text(updated, self._now(), self.config.display.hour24) if updated is not None else ""
            if run:
                return f"{run} run {DOT} {clock}" if clock else f"{run} run"
            return f"updated {clock}" if clock else ""
        entries = sum(1 for item in section.items if item.kind == ITEM_ENTRY)
        if section.key == ACTIONS_KEY:
            return _plural(entries, "invite")
        return _plural(entries, "item") if entries else ""

    def _steps(self, script: Script, order: list[int], current: int | None) -> list[tuple[str, str]]:
        """The current section and its neighbours as step chips."""
        if not order:
            return []
        if current in order:
            position = order.index(current)
        elif self.player.state == FINISHED:
            position = len(order) - 1
        else:
            position = 0
        start = max(0, min(position - 2, len(order) - _STEP_WINDOW))
        steps = []
        for index in order[start:start + _STEP_WINDOW]:
            if index == current:
                state = hud.STEP_ACTIVE
            elif index in self._sections_started:
                state = hud.STEP_DONE
            else:
                state = hud.STEP_TODO
            steps.append((_section_title(script.sections[index]), state))
        return steps

    def _update_telemetry(self) -> None:
        reading, script = self.window.reading, self._script
        order = script.section_indices(self._include_ignored) if script is not None else []
        current = self._current_section()
        if current in order:
            position = order.index(current) + 1
        elif script is not None and self.player.state == FINISHED:
            position = len(order)
        else:
            position = 0
        reading.progress_bar.set_value(f"{position} / {len(order)}",
                                       position / len(order) if order else 0.0)
        if self._uses_offline_voice():
            reading.voice_bar.set_value("offline", 1.0, hud.AMBER)
        elif self._engine is not None:
            reading.voice_bar.set_value("online", 1.0, hud.GREEN)
        else:
            reading.voice_bar.set_value("--", 0.0, hud.TEXT_DIM)
        briefing = self._briefing
        if briefing is None:
            reading.updated_bar.set_value("--", 0.0, hud.TEXT_DIM)
        else:
            stale = script.stale if script is not None else not self._cached_is_fresh()
            now, hour24 = self._now(), self.config.display.hour24
            # In a narrow column the longer 12-hour values give way to "Mon 11:31 PM" / "Oct 2" (24-hour ones fit).
            short = "" if hour24 else _short_updated(briefing.header, now, compact=True)
            reading.updated_bar.set_value(_short_updated(briefing.header, now, hour24), 1.0,
                                          hud.AMBER if stale else hud.TEXT_DIM, short=short)

    # ---- player signals --------------------------------------------------------------------

    def _on_item_changed(self, section_index: int, item_index: int) -> None:
        if self.state != STATE_READING:
            return
        reading = self.window.reading
        reading.highlight(section_index, item_index)
        reading.set_speech(self._item_text(section_index, item_index))

    def _item_text(self, section_index: int, item_index: int) -> str:
        script = self._script
        if script is None or not 0 <= section_index < len(script.sections):
            return ""
        items = script.sections[section_index].items
        if not 0 <= item_index < len(items):
            return ""
        return items[item_index].display or items[item_index].spoken

    def _on_section_started(self, section_index: int) -> None:
        self._has_played = True
        logger.debug("Section %d started", section_index)
        script = self._script
        if script is None or self.state != STATE_READING or not 0 <= section_index < len(script.sections):
            return
        previous = self._last_section
        if (previous is not None and previous != section_index and not self._skip_pending
                and 0 <= previous < len(script.sections)):
            self._activity(hud.TAG_DONE, f"Read {_section_title(script.sections[previous])}")
        self._skip_pending = False
        self._last_section = section_index
        self._sections_started.add(section_index)
        order = script.section_indices(self._include_ignored)
        position = order.index(section_index) + 1 if section_index in order else len(order)
        self._activity(hud.TAG_RUN, f"Reading {_section_title(script.sections[section_index])}",
                       f"section {position} of {len(order)}")
        self._update_reading_controls()

    def _on_player_state(self, state: str) -> None:
        if state == FINISHED and self.state == STATE_READING and self._last_section is not None:
            self._last_section = None
            self._activity(hud.TAG_DONE, "Finished the briefing",
                           _plural(len(self._sections_started), "section") + " read")
        self._update_reading_controls()
        if state == FINISHED and self._pending_run and self.state == STATE_READING:
            QTimer.singleShot(0, self._offer_pending_run)   # outside the player's signal

    def _offer_pending_run(self) -> None:
        """The reading ended while a newer run was due: ask about that one now."""
        if self.state == STATE_READING and self._pending_run and self.player.state == FINISHED:
            self._back_to_prompt(self._pending_run, take_focus=False)

    def _on_player_error(self, message: str) -> None:
        logger.warning("Playback problem: %s", message)
        self._flash_note("Could not play part of the audio")

    # ---- reading controls --------------------------------------------------------------------

    def toggle_play(self) -> None:
        """Play/Pause; Replay when finished; Retry when loading failed."""
        if self.state != STATE_READING:
            return
        if self._script is None:
            # Retry is offered while an error is shown, also between polling attempts.
            if self._fetch_error is not None or not self.fetching:
                self.retry()
            return
        if self.player.state in (FINISHED, IDLE):
            logger.info("Replay")
            self._start_playback(self._script.section_indices(self._include_ignored))
        else:
            self.player.toggle()

    def skip_section(self) -> None:
        if self.state == STATE_READING and self._script is not None:
            logger.info("Skip section")
            self._skip_pending = self.player.state in (PLAYING, PAUSED, WAITING)
            self.player.skip_section()

    def jump_to_section(self, index: int) -> None:
        """Play from section ``index`` (a click on the section list)."""
        script = self._script
        if self.state != STATE_READING or script is None:
            return
        order = script.section_indices(self._include_ignored)
        if index not in order:
            return
        logger.info("Jump to section %d", index)
        self._skip_pending = self._current_section() is not None
        self.player.start([i for i in order if i >= index])

    def read_everything(self) -> None:
        """Also read the ignored sections, in document order from where playback is."""
        script = self._script
        if (self.state != STATE_READING or script is None or not script.has_ignored
                or self._include_ignored):
            return
        logger.info("Read everything")
        self._include_ignored = True
        self.window.reading.set_script(script, include_ignored=True)
        self._update_read_everything()
        self._activity(hud.TAG_RUN, "Reading everything", "skipped sections included")
        ignored = [index for index, section in enumerate(script.sections) if section.ignored]
        current = self.player.current_section
        if self.player.state in (FINISHED, IDLE) or current is None:
            self.player.start(ignored)
        else:
            upcoming = [index for index in ignored if index < current]
            upcoming += list(range(current + 1, len(script.sections)))
            self.player.replace_upcoming(upcoming)
        self._update_reading_controls()

    def open_in_notion(self) -> None:
        logger.info("Open in Notion")
        QDesktopServices.openUrl(QUrl(self.config.notion_url))

    def done(self) -> None:
        logger.info("Done")
        if self.state == STATE_READING and self._pending_run:
            self._back_to_prompt(self._pending_run, take_focus=True)   # it said "briefing is due"
            return
        self._record_done()
        self.quit()

    # ---- run state (catch-up) -------------------------------------------------------------------

    def record_shown(self) -> str | None:
        """The prompt was shown: remember it for the current slot. The slot key, or None."""
        if self.run_state is None:
            return None
        return self.run_state.record_shown(self._now(), self.slots)

    def record_answer(self, how: str) -> str | None:
        """Read now / Dismiss / Done ("read", "dismissed", "done") settles the current slot."""
        if self.run_state is None:
            return None
        return self.run_state.record_answer(self._now(), self.slots, how)

    def _record_done(self) -> str | None:
        """Done on the reading screen settles the slot it read; a newer slot that came up
        meanwhile (its prompt was never shown here) stays open for the catch-up."""
        if self.run_state is None:
            return None
        if self.state == STATE_READING and self._read_slot is not None:
            key = handled_slot_key(self._now(), self.slots)
            if key is not None and key != self._read_slot:
                logger.info("Done: the %s slot was not read here; leaving it open", key)
                return None
        return self.record_answer("done")

    # ---- activation and shutdown ----------------------------------------------------------------

    def handle_activation(self, message: dict) -> None:
        """A second launch asked this instance to come forward."""
        message = message if isinstance(message, dict) else {}
        run = _normalize_run(message.get("run"))
        now = message.get("now") is True
        logger.info("Another launch asked this instance to come forward (run=%s, now=%s, state=%s)",
                    run or "-", now, self.state)
        if self.state in (STATE_QUITTING, STATE_IDLE):
            return
        if self.state == STATE_READING:
            self._activation_while_reading(run)
            return
        if run and run != self.expected_run:
            self._switch_expected_run(run)
        elif run:
            self._restart_run(poll=not now)
        # show_prompt / enter_reading look at Notion again when the copy we have is stale.
        if now:
            self.enter_reading()
            force_foreground(self.window)
        else:
            self.show_prompt(take_focus=True)

    def _switch_expected_run(self, run: str) -> None:
        logger.info("Now waiting for the %s briefing", run)
        self.expected_run = run
        self._run_started = self._scheduled_start(run)
        self._prefetch = None
        if not self.startup_error:
            self._start_fetch(run)
        self._refresh_prefetch()

    def _run_start_for(self, run: str | None) -> datetime:
        """When ``run`` began: a relaunch of the same run within hours keeps the original start,
        so last night's PM briefing is not called stale after midnight; anything else starts at
        its scheduled slot (when that passed within the catch-up window) or now."""
        now = self._now()
        if run == self.expected_run and now - self._run_started < _SAME_RUN_WINDOW:
            return self._run_started
        return self._scheduled_start(run)

    def _scheduled_start(self, run: str | None) -> datetime:
        """The time ``run``'s scheduled slot passed, when that was within the catch-up window
        (3 hours) before now; else now.

        A PM catch-up at 00:30 (or a PM task that started late) then waits for the PM
        briefing of the evening before, as the 23:42 task would have, instead of asking
        for one dated today. Without slots (tests, harnesses) it is always now.
        """
        now = self._now()
        if not run or run not in self.slots:
            return now
        slot = slot_for(now, {run: self.slots[run]})
        return slot.at if slot is not None else now

    def _restart_run(self, *, poll: bool) -> None:
        """The expected run was launched again (next day's task or by hand): wait for it anew."""
        self._run_started = self._run_start_for(self.expected_run)
        if poll and not self.startup_error and not self.fetching and not self._cached_is_fresh():
            logger.info("Waiting for the %s briefing again", self.expected_run)
            self._start_fetch(self.expected_run)

    def _activation_while_reading(self, run: str | None) -> None:
        """A launch while the reading screen is open: a newer run's briefing is offered."""
        if not run or not self._reading_is_outdated(run):
            force_foreground(self.window)
            return
        if self._script is not None and self.player.state in (PLAYING, WAITING, PAUSED):
            # Never cut off a listen in progress: ask about the new run when it ends
            # (or on Done).
            logger.info("The %s briefing is due; asking about it after this reading", run)
            self._pending_run = run
            self._flash_note(self._pending_note(), sticky=True)
            force_foreground(self.window)
            return
        self._back_to_prompt(run, take_focus=True)

    def _reading_is_outdated(self, run: str) -> bool:
        """Whether a launch for ``run`` asks for something other than what is on screen."""
        if self._script is None:   # still loading or showing an error
            return run != self.expected_run
        briefing = self._briefing
        return briefing is None or not check_freshness(
            briefing.header, run, self._now(), run_started=self._run_start_for(run)).fresh

    def _back_to_prompt(self, run: str, *, take_focus: bool) -> None:
        """Close the reading screen (nothing is playing) and ask about ``run``'s briefing."""
        logger.info("Leaving the reading screen to ask about the %s briefing", run)
        self._cancel_countdown("the reading screen closed")   # never send from a hidden card
        if self._dialog is not None:
            self._dialog.reject()
        self._recheck_timer.stop()
        self._note_timer.stop()
        self._replace_player()
        self._script = None
        self._include_ignored = False
        self._has_played = False
        self._pending_run = None
        self._note = ""
        self._sections_started = set()
        self._last_section = None
        self._read_slot = None
        reading = self.window.reading
        reading.show_message("")   # the next briefing renders from the top
        reading.set_sections([], "")
        reading.set_steps([])
        reading.set_speech("")
        self.state = STATE_PROMPT
        self._switch_expected_run(run)
        self.show_prompt(take_focus=take_focus)

    def quit(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        """Stop everything and quit the event loop. Safe to call more than once.

        A running Google sign-in or event insert is not waited for: its thread
        is a daemon and its result is dropped. An undo countdown is cancelled
        (nothing is sent). An answer, move or cancel already on its way to
        Google is waited for at most 5 s, with the window already hidden, so
        its result is saved; after that the app exits regardless and the next
        start shows that card as UNKNOWN.
        """
        if self._shut_down:
            return
        self._shut_down = True
        logger.info("Shutting down")
        if self._on_shutdown is not None:
            self._on_shutdown()
        self.state = STATE_QUITTING
        self._closing.set()
        for timer in (self._ignore_timer, self._tick_timer, self._snooze_timer, self._note_timer,
                      self._recheck_timer, self._agenda_timer, self._countdown_timer):
            timer.stop()
        countdown, self._countdown = self._countdown, None
        if countdown is not None:
            self._jobs.pop(countdown.action.id, None)
            logger.info("Action %s undone (app closed); nothing was sent", countdown.action.id)
        if self._dialog is not None:
            self._dialog.reject()
        # Hide first: the media player has (rarely) hung in stop(), and the window
        # should be gone either way.
        self.window.hide()
        self.player.stop()
        self._stop_fetch()
        if self._tts is not None:
            self._tts.stop()
        if self._calendar_worker is not None:
            if self._send_in_flight():
                logger.info("Waiting up to %d s for a change on its way to Google", int(_QUIT_SEND_WAIT_S))
                finished = self._calendar_worker.wait_idle(_QUIT_SEND_WAIT_S)
                logger.info("The change on its way to Google %s", "finished" if finished
                            else "did not finish in time; it shows as unknown at the next start")
            self._calendar_worker.stop()
        if self.tray is not None:
            self.tray.hide()
        if self._audio_dir is not None:
            shutil.rmtree(self._audio_dir, ignore_errors=True)
            busy = self._tts if self._tts is not None and self._tts.is_alive() else None
            if busy is not None or self._audio_dir.exists():
                _remove_audio_dir_after(busy, self._audio_dir)
        QCoreApplication.quit()
