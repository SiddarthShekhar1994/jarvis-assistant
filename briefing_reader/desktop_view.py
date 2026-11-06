"""Jarvis on your desktop, in the window: the first-run "Jarvis on your desktop" card and Settings > Desktop's own
part (hard rule 15; SPEC sections 10.3 and 11; Phase 1, part 9; hologram look, British spelling, amber only because
a decision waits).

Nothing here turns anything on, stores anything or goes onto the desktop: every click only emits a signal, and the app
(ui.AppController with desktop_ui.DesktopController) stores the owner's OK and goes on from there.

``ConsentCard(parent)``
    The first-run card, over the window's screen (the Find list's way: a ``void`` scrim and a panel in the middle):
    the kicker "DESKTOP <middle dot> NEEDS YOUR OK", the title "Jarvis on your desktop", what Jarvis will and won't do
    in this phase (``CONSENT_ROWS``: SPEC 10.3's rows that are live in Phase 2), where it can be turned off again, and
    "Not now" (ghost, left) / "Turn on" (amber, right; DesktopB's order). ``open_card(entry, updated=)`` shows it for an
    entry (the Dock's Desktop, the HUD key, a --hud start, a typed request or a Phase 2 skill asked for under Phase 1's
    OK - desktop_ui.ENTRIES); ``updated``: the owner turned Phase 1's card on, so Phase 2's rows (``CONSENT_NEW``) are
    marked "NEW" (``new_rows()``). ``close_card()``, ``is_open()``,
    ``entry()``, ``set_problem(text)`` (a line in coral: the OK could not be saved). The keyboard: the focus starts on
    Not now - Enter or Space there turns nothing on -, Tab and Shift+Tab stay between the two buttons, Esc and a click
    on the scrim are Not now; when it closes the focus goes back where it was. Signals ``turnOnClicked(entry)`` and
    ``notNowClicked(entry)``.

``WaitingRow()``
    P2.3: Approvals' reminder while a NEEDS YOUR OK card of file steps waits on the desktop HUD - "Waiting on your
    desktop" with an amber dot and "Show me" (an amber link; its click: ``showClicked()`` - the app goes onto the HUD,
    where the card is). ``set_count(n)``: hidden at 0 ("2 waiting on your desktop" for more than one). Made only while
    [desktop] is on.

``DesktopSettingsPanel()``
    Under Settings > Desktop's rows: while the owner's OK is stored, a line saying so and "Turn off" (ghost; it takes
    the OK back - nothing desktop-side works until he turns it on again from the card); before that, how to turn it on
    (Desktop in the Dock). ``set_state(turned_on, problem="")``; signal ``turnOffClicked()``.
"""

from __future__ import annotations

from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import holo, hud
from .memory_view import body_label

CONSENT_KICKER = "DESKTOP \u00b7 NEEDS YOUR OK"
CONSENT_TITLE = "Jarvis on your desktop"
# SPEC 10.3's first-run rows, those live in Phase 2 (work inside apps and its control come in Phase 3); Phase 2's two
# (p2r-6) are new since Phase 1's card: marked "NEW" for an owner who turned that one on.
CONSENT_ROWS = ("I open files, apps, folders and sites you name.",
                "Music, volume, timers and screenshots happen at once - Undo puts them back.",
                "Moving, renaming, zipping or putting files in the Recycle Bin waits for your click on a card, with "
                "Undo. Nothing is deleted for good.",
                "I only work in the folders and apps you allow.",
                "To plan, file, folder and app names go to your Claude plan. They're never kept in logs.")
CONSENT_NEW = (1, 2)                   # the rows of CONSENT_ROWS new since Phase 1's card
# The agent's row (version 3 of the card): only while [agent] is on; off, the card is as it was.
CONSENT_TASK_ROW = ("Give me a task and I work out the steps myself. I search and read public pages in a window you "
                    "can watch, and the words of a search go to {engine}, or to {others} if it gives nothing. When a "
                    "task needs them, I read your calendar, mail, contacts, notes and allowed folders; what a task "
                    "reads goes to your Claude plan, as an Ask's does, and is never kept in logs. What I say aloud "
                    "goes to Microsoft's online voice, as an Ask's answers do, and a line that may hold something "
                    "private stays on screen. Anything that changes things or gets sent still waits for your click.")
NEW_TAG_TEXT = "NEW"
CONSENT_FOOT = "You can turn it off again in Settings \u203a Desktop."
NOT_NOW_TEXT = "Not now"
TURN_ON_TEXT = "Turn on"
NOT_SAVED_TEXT = "Your OK couldn't be saved, so nothing was turned on. Try again."
TURN_OFF_TEXT = "Turn off"
WAITING_TEXT = "Waiting on your desktop"
WAITING_MANY_TEXT = "{count} waiting on your desktop"
SHOW_ME_TEXT = "Show me"
SETTINGS_ON_TEXT = ("On, because you clicked Turn on. Turn off takes your OK back - nothing on your desktop works "
                    "until you turn it on again.")
SETTINGS_OFF_TEXT = ("To turn it on, click Desktop in the Dock and read its card - nothing on your desktop works until "
                     "you click Turn on.")
TURN_OFF_NOT_SAVED_TEXT = ("Turned off for now - it couldn't be saved, so it will be on again when Jarvis restarts.")
PANEL_WIDTH = 540
PANEL_PAD = (20, 22)                   # vertical, horizontal
PANEL_RADIUS = 12
BRACKET_LEG = 14
ROW_GAP = 6


class _Panel(QWidget):
    """The amber NEEDS YOUR OK card (DesktopB .glass.amb): opaque ``tile-amber``, an amber line and corner brackets,
    the faint scan lines."""

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect())
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), PANEL_RADIUS, PANEL_RADIUS)
            painter.fillPath(path, QColor(holo.TILE_AMBER))
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(rect, holo.scan_brush(holo.SCAN_AMBER))
            painter.restore()
            painter.setPen(QPen(holo.alpha(holo.AMBER, 0.60), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            holo.paint_brackets(painter, rect, QColor(holo.AMBER), leg=BRACKET_LEG, radius=PANEL_RADIUS, width=2.0)
        finally:
            painter.end()


class _Kicker(QWidget):
    """The kicker with its amber dot (DesktopB .kick .kd)."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._font = hud.mono_font(10, 500, 0.22)
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            dot = QPointF(3, self.height() / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(holo.alpha(holo.AMBER, 0.3))
            painter.drawEllipse(dot, 5.0, 5.0)
            painter.setBrush(QColor(holo.AMBER))
            painter.drawEllipse(dot, 3.0, 3.0)
            painter.setFont(self._font)
            painter.setPen(QColor(holo.AMBER))
            room = QRectF(14, 0, self.width() - 14, self.height())
            painter.drawText(room, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             QFontMetricsF(self._font).elidedText(self._text, Qt.TextElideMode.ElideRight,
                                                                  room.width()))
        finally:
            painter.end()


class _Dot(QWidget):
    """A line's small amber dot, on its first line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(6, 20)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(holo.alpha(holo.AMBER, 0.85))
            painter.drawEllipse(QPointF(3, 10), 2.5, 2.5)
        finally:
            painter.end()


class _NewTag(QWidget):
    """p2r-6: "NEW" before a row the owner has not turned on yet - small amber caps in a hairline outline, on the
    row's first line."""

    PAD = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = hud.mono_font(9, 500, 0.16)
        metrics = QFontMetricsF(self._font)
        self.setFixedSize(int(metrics.horizontalAdvance(NEW_TAG_TEXT) + 2 * self.PAD + 2), 20)
        self.setAccessibleName(NEW_TAG_TEXT)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            box = QRectF(0.5, 3.5, self.width() - 1.0, 13.0)
            painter.setPen(QPen(holo.alpha(holo.AMBER, 0.70), 1.0))
            painter.setBrush(holo.alpha(holo.AMBER, 0.12))
            painter.drawRoundedRect(box, 3, 3)
            painter.setFont(self._font)
            painter.setPen(QColor(holo.AMBER))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), NEW_TAG_TEXT)
        finally:
            painter.end()


class _Bullet(QWidget):
    """One of the card's lines: a small amber dot and the line, wrapping; "NEW" before it when marked
    (``set_new``)."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = body_label(text, "body_sm", holo.AMBER_TX2)
        self.tag = _NewTag()
        self.tag.hide()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(_Dot(), 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)

    def is_new(self) -> bool:
        return not self.tag.isHidden()

    def set_new(self, new: bool) -> None:
        self.tag.setVisible(bool(new))
        self.setAccessibleName(f"{NEW_TAG_TEXT}: {self.label.text()}" if new else self.label.text())


class ConsentCard(QWidget):
    """The first-run card; see the module docstring."""

    turnOnClicked = Signal(str)
    notNowClicked = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("desktopConsentCard")
        self.setAccessibleName(CONSENT_TITLE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._entry = ""
        self._opener: QWidget | None = None
        self._task_row: _Bullet | None = None
        self.panel = _Panel(self)
        self.kicker = _Kicker(CONSENT_KICKER)
        self.title = body_label(CONSENT_TITLE, "screen_title", holo.AMBER_TX, wrap=False, name="desktopConsentTitle")
        self.rows = [_Bullet(text) for text in CONSENT_ROWS]
        self.foot = body_label(CONSENT_FOOT, "body_sm", holo.AMBER_TX2, name="desktopConsentFoot")
        self.problem = body_label("", "body_sm", holo.ALERT_TX, name="desktopConsentProblem")
        self.problem.hide()
        self.not_now = holo.HoloButton(NOT_NOW_TEXT, holo.AMBER_GHOST)
        self.not_now.clicked.connect(self._on_not_now)
        self.turn_on = holo.HoloButton(TURN_ON_TEXT, holo.AMBER_COMMIT)
        self.turn_on.clicked.connect(self._on_turn_on)
        for button in (self.not_now, self.turn_on):
            button.installEventFilter(self)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(holo.BUTTON_GAP)
        buttons.addWidget(self.not_now)
        buttons.addStretch(1)
        buttons.addWidget(self.turn_on)
        column = QVBoxLayout(self.panel)
        column.setContentsMargins(PANEL_PAD[1], PANEL_PAD[0], PANEL_PAD[1], PANEL_PAD[0])
        column.setSpacing(0)
        column.addWidget(self.kicker)
        column.addSpacing(8)
        column.addWidget(self.title)
        column.addSpacing(12)
        for index, row in enumerate(self.rows):
            if index:
                column.addSpacing(ROW_GAP)
            column.addWidget(row)
        column.addSpacing(12)
        column.addWidget(self.foot)
        column.addWidget(self.problem)
        column.addSpacing(16)
        column.addLayout(buttons)
        parent.installEventFilter(self)
        self.hide()

    # ---- open / close ---------------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.isVisible()

    def entry(self) -> str:
        """What the owner asked for when it opened (desktop_ui.ENTRIES)."""
        return self._entry

    def open_card(self, entry: str, *, updated: bool = False, task_row: str = "", task_new: bool = False) -> None:
        """Show the card for ``entry`` over the window's screen, the focus on Not now (the one before kept);
        ``updated``: Phase 1's card was turned on, so its new rows are marked "NEW". ``task_row``: the agent's row,
        while [agent] is on ("": none, and the card is as it was), marked "NEW" with ``task_new``."""
        if not self.isVisible():
            focus = self.window().focusWidget()
            self._opener = focus if focus is not None and not self.isAncestorOf(focus) else None
        self._entry = str(entry or "")
        for index, row in enumerate(self.rows):
            row.set_new(updated and index in CONSENT_NEW)
        self._set_task_row(task_row, task_new)
        self.set_problem("")
        self.setGeometry(self.parentWidget().rect())
        self._place_panel()
        self.show()
        self.raise_()
        self.not_now.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_task_row(self, text: str, new: bool) -> None:
        """The agent's row after the others: made when it first shows."""
        if self._task_row is None and text:
            self._task_row = _Bullet(text)
            column = self.panel.layout()
            at = column.indexOf(self.rows[-1]) + 1
            column.insertSpacing(at, ROW_GAP)
            column.insertWidget(at + 1, self._task_row)
        if self._task_row is not None:
            self._task_row.label.setText(text)
            self._task_row.setVisible(bool(text))
            self._task_row.set_new(bool(text) and new)

    def close_card(self, restore_focus: bool = True) -> None:
        if not self.isVisible():
            return
        opener, self._opener = self._opener, None
        self.hide()
        if restore_focus and opener is not None and shiboken6.isValid(opener) and opener.isVisible():
            opener.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_problem(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.setVisible(bool(text))
        if self.isVisible():
            self._place_panel()

    def new_rows(self) -> list[str]:
        """The rows marked "NEW" (tests and the harness)."""
        return [row.label.text() for row in self._all_rows() if row.is_new()]

    def _all_rows(self) -> list[_Bullet]:
        shown = self._task_row is not None and not self._task_row.isHidden()
        return [*self.rows, *([self._task_row] if shown else [])]

    def texts(self) -> list[str]:
        """Every text the card shows (tests and the harness)."""
        return [self.kicker.text(), self.title.text(), *(row.label.text() for row in self._all_rows()),
                self.foot.text(),
                *([self.problem.text()] if self.problem.isVisible() else []), self.not_now.text(),
                self.turn_on.text()]

    def _place_panel(self) -> None:
        width = min(PANEL_WIDTH, max(300, self.width() - 32))
        self.panel.setFixedWidth(width)
        self.panel.adjustSize()
        height = self.panel.sizeHint().height()
        if self._task_row is not None and not self._task_row.isHidden():    # a long row: its wrapped lines all show
            height = max(height, self.panel.layout().totalHeightForWidth(width))
        self.panel.setGeometry(int((self.width() - width) / 2), int(max(16, (self.height() - height) / 2)),
                               int(width), int(height))

    # ---- clicks and keys ----------------------------------------------------------------------------

    def _on_turn_on(self) -> None:
        self.turnOnClicked.emit(self._entry)

    def _on_not_now(self) -> None:
        entry = self._entry
        self.close_card()
        self.notNowClicked.emit(entry)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            self._place_panel()
        elif watched in (self.not_now, self.turn_on) and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._on_not_now()
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):     # trapped between the two buttons
                other = self.turn_on if watched is self.not_now else self.not_now
                other.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                watched.click()
                return True
        return False

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self._on_not_now()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if not self.panel.geometry().contains(event.position().toPoint()):
            self._on_not_now()                           # the scrim: Not now
        event.accept()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_panel()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), holo.alpha(holo.VOID, 0.6))
        finally:
            painter.end()


class WaitingRow(QWidget):
    """See the module docstring: "Waiting on your desktop <middle dot> Show me" over Approvals' cards."""

    showClicked = Signal()

    HEIGHT = 44

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DesktopWaitingRow")
        self._count = 0
        self.label = body_label(WAITING_TEXT, "body_sm", holo.AMBER_TX, wrap=False)
        self.dot = body_label("\u00b7", "body_sm", holo.AMBER_TX2)
        self.show_me = holo.HoloButton(SHOW_ME_TEXT, holo.LINK, size=holo.SIZE_SM)
        self.show_me.setAccessibleName(SHOW_ME_TEXT + ": " + WAITING_TEXT)
        self.show_me.clicked.connect(self.showClicked.emit)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(8)
        row.addWidget(_Dot(), 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.show_me, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.hide()

    def count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        self._count = max(0, int(count))
        text = WAITING_TEXT if self._count <= 1 else WAITING_MANY_TEXT.format(count=self._count)
        if self.label.text() != text:
            self.label.setText(text)
        self.setVisible(self._count > 0)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 4.5, -0.5, -4.5)
            path = QPainterPath()
            path.addRoundedRect(rect, 10, 10)
            painter.fillPath(path, QColor(holo.TILE_AMBER))
            painter.setPen(QPen(holo.alpha(holo.AMBER, 0.45), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        finally:
            painter.end()


class DesktopSettingsPanel(QWidget):
    """Settings > Desktop's own part; see the module docstring."""

    turnOffClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line: QLabel = body_label(SETTINGS_OFF_TEXT, "body_sm", holo.INK_2, name="desktopSettingsLine")
        self.problem: QLabel = body_label("", "body_sm", holo.ALERT_TX, name="desktopSettingsProblem")
        self.problem.hide()
        self.turn_off = holo.HoloButton(TURN_OFF_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.turn_off.setAccessibleName("Turn off Jarvis on your desktop")
        self.turn_off.clicked.connect(self.turnOffClicked)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self.line, 1)
        row.addWidget(self.turn_off, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(row)
        column.addWidget(self.problem)
        self.set_state(False)

    def set_state(self, turned_on: bool, problem: str = "") -> None:
        """The owner's OK as stored now (``turned_on``) and a problem line (coral; "" none)."""
        self.line.setText(SETTINGS_ON_TEXT if turned_on else SETTINGS_OFF_TEXT)
        self.turn_off.setVisible(bool(turned_on))
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
