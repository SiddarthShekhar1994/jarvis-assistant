"""The Agents screen (Ctrl+9) in the hologram look: the standing agents and rules, what they did, and their reports.

Public API
==========

``AgentsStage``
    A frameless scroll area whose column (at most COLUMN_MAX wide, centred) is the quiet panel of memory_view, as on
    Follow-ups. Top to bottom:

    - the AGENTS head with Pause all / Resume all (``pause_all_button``: ``pauseAllRequested()``);
    - the status readout (``agents_meta``, in capitals: "BACKGROUND RUNS ON - 4 OF 30 RUNS TODAY - NEXT 7:30 AM")
      and the fix lines, each with its link (``linkClicked(link_id)``);
    - the cards Jarvis puts first (the consent card, a setup card: ``AgentsInfo.cards``), each with its buttons
      (``cardAction(card_id, action)``);
    - AGENTS: one row per agent with Run now, Pause / Resume and Delete (``runNowRequested``, ``pauseRequested``,
      ``deleteRequested``: the job id);
    - RULES: one row per rule with Dry run (``dryRunRequested``), Turn on (``turnOnRequested``), Pause / Resume and
      Delete;
    - ACTIVITY: what the jobs did, newest first (the first ``shown`` rows, then "+N more"), each with Undo while it
      can be undone (``undoRequested(row key)``);
    - REPORTS: what the agents wrote, each under "Written by Claude from your mail", with Clear (``clearRequested()``).

    ``set_agents(info)`` (an AgentsInfo; None: nothing shows) fills it; ``job_row(id)``, ``activity_row(key)``,
    ``report_rows()``, ``notice_card(id)`` find what it shows.

Nothing here runs, pauses, deletes or undoes anything: the buttons only say which job or row, and standing_ui does
the rest. Every text (a job's name, a sentence, a report, a sender, a subject) is shown as plain text, never as HTML.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from . import holo, hud
from .memory_view import COLUMN_MAX, PANEL_PAD, PanelFrame, SectionHead, body_label

AGENTS_TITLE = "AGENTS"
AGENTS_GROUP = "AGENTS"
RULES_GROUP = "RULES"
ACTIVITY_GROUP = "ACTIVITY"
REPORTS_GROUP = "REPORTS"
WRITTEN_BY = "Written by Claude from your mail"
RUN_NOW_TEXT = "Run now"
PAUSE_TEXT = "Pause"
RESUME_TEXT = "Resume"
DELETE_TEXT = "Delete"
DRY_RUN_TEXT = "Dry run"
TURN_ON_TEXT = "Turn on"
UNDO_TEXT = "Undo"
CLEAR_TEXT = "Clear"
CLEAR_TIP = "Delete the reports and receipts kept on this PC (a card still waiting for your OK stays)"
PAUSE_ALL_TEXT = "Pause all"
RESUME_ALL_TEXT = "Resume all"
PAUSE_ALL_TIP = "Stop every agent and rule until you resume them (nothing runs, nothing is tidied)"
MORE_TEXT = "+{count} more"
FEWER_TEXT = "Show fewer"
ACTIVITY_SHOWN = 6
REPORTS_SHOWN = 3

# Status badges and the row's left bar.
STATUS_TONES = {"live": holo.TONE_CYAN, "paused": holo.TONE_STEEL, "draft": holo.TONE_AMBER, "tried": holo.TONE_AMBER}
_BARS = {holo.TONE_CYAN: holo.HOLO, holo.TONE_STEEL: holo.STEEL, holo.TONE_AMBER: holo.AMBER,
         holo.TONE_OK: holo.HOLO, holo.TONE_ALERT: holo.ALERT}

# A notice card's button looks (cardAction's action names are the card's own).
LOOK_GO, LOOK_GHOST, LOOK_AMBER, LOOK_DANGER = "go", "ghost", "amber", "danger"
_LOOKS = {LOOK_GO: holo.GO, LOOK_GHOST: holo.GHOST, LOOK_AMBER: holo.AMBER_COMMIT, LOOK_DANGER: holo.DANGER_GHOST}


@dataclass(frozen=True)
class JobRow:
    """One agent or rule as its row shows it (every text the owner's or Jarvis's own, shown as plain text)."""

    job_id: str
    kind: str                    # "agent" | "rule"
    name: str
    detail: str = ""             # what it does: the trigger and sources, or the rule's sentence
    meta: str = ""               # "NEXT 7:30 AM - 2 RUNS TODAY", "DRY RUN: 3 OF 12 YES"
    status: str = "live"         # model.STATUS_*
    status_text: str = "LIVE"
    can_run: bool = False
    can_pause: bool = True
    paused: bool = False         # Pause reads Resume
    can_dry_run: bool = False
    can_turn_on: bool = False
    turn_on_tip: str = ""        # why Turn on can't go yet (its button is off and says why), "" when it can
    can_delete: bool = True
    tooltip: str = ""


@dataclass(frozen=True)
class ActivityRow:
    """One thing a job did (a receipt, or a run that could not run), newest first."""

    key: str                     # "<run id>:<receipt id>" (unique)
    when: str                    # "9:41 AM", "YESTERDAY 9:41 PM", "MON 9:41 AM"
    job: str                     # the job's name
    text: str                    # "To-do: Pay the Acme invoice", "Matched: an invoice from Acme"
    detail: str = ""             # "Acme Billing - Invoice 1043"
    state_text: str = ""         # "WAITING", "DECIDED", "WITHDRAWN", "UNDONE", "CAPPED", "MISSED"
    tone: str = holo.TONE_STEEL
    can_undo: bool = False
    undo_text: str = UNDO_TEXT
    undo_tip: str = ""


@dataclass(frozen=True)
class ReportRow:
    key: str                     # the run id
    when: str
    job: str
    text: str                    # the report, plain text
    summary: str = ""


@dataclass(frozen=True)
class CardButton:
    action: str                  # what cardAction says
    text: str
    look: str = LOOK_GHOST
    enabled: bool = True
    tip: str = ""


@dataclass(frozen=True)
class NoticeCard:
    """A card at the top of the screen (the consent card, a setup card): a kicker, a title, lines and buttons."""

    card_id: str
    kicker: str
    title: str
    lines: tuple[str, ...] = ()
    buttons: tuple[CardButton, ...] = ()
    tone: str = holo.TONE_AMBER
    note: str = ""               # a line under the buttons (why a button is off, what happened)


@dataclass(frozen=True)
class AgentsInfo:
    meta: str = ""
    notes: tuple[tuple[str, str, str], ...] = ()     # (text, link text, link id)
    paused_all: bool = False
    can_pause_all: bool = True
    cards: tuple[NoticeCard, ...] = ()
    agents: tuple[JobRow, ...] = ()
    rules: tuple[JobRow, ...] = ()
    activity: tuple[ActivityRow, ...] = ()
    reports: tuple[ReportRow, ...] = ()
    empty_text: str = ""
    can_clear: bool = False


class _Badge(QWidget):
    """A status badge (holo.paint_status_badge) as a widget of its own width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text, self._tone = "", holo.TONE_STEEL
        self.setFixedHeight(22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_badge(self, text: str, tone: str) -> None:
        self._text, self._tone = str(text or ""), tone
        self.setFixedWidth(int(holo.status_badge_width(self._text)) + 2 if self._text else 0)
        self.setVisible(bool(self._text))
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if not self._text:
            return
        painter = QPainter(self)
        try:
            holo.paint_status_badge(painter, QPointF(0.5, self.height() / 2), self._text, self._tone)
        finally:
            painter.end()


class _Tile(QWidget):
    """A row's tile: ``tile-focus`` fill, a 1 px line and a 3 px bar at the left in the row's tone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bar = holo.HOLO
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bar(self, tone: str) -> None:
        self._bar = _BARS.get(tone, holo.STEEL)
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(holo.LINE_FAINT, 1.0))
            painter.setBrush(QColor(holo.TILE_FOCUS))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._bar))
            painter.drawRoundedRect(QRectF(0, 10, 3, max(0.0, self.height() - 20)), 1.5, 1.5)
        finally:
            painter.end()


def _button(text: str, variant: str = holo.GHOST) -> holo.HoloButton:
    return holo.HoloButton(text, variant, size=holo.SIZE_SM)


def _buttons_row(buttons: Sequence[QWidget], *, left: int = 12) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(left - holo.HoloButton.MARGIN, 0, 6, 0)
    row.setSpacing(holo.BUTTON_GAP - 2 * holo.HoloButton.MARGIN)
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


class JobTile(_Tile):
    """One agent's or rule's row: its name and status badge, what it does, a readout, and its buttons."""

    runNow = Signal(str)
    pause = Signal(str)
    delete = Signal(str)
    dryRun = Signal(str)
    turnOn = Signal(str)

    def __init__(self, info: JobRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self.name_label = body_label("", "tile_title", holo.INK_1, wrap=False)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.badge = _Badge()
        self.detail_label = body_label("", "body_sm", holo.INK_3)
        self.meta_label = body_label("", "readout", holo.INK_4, wrap=True)
        self.run_button = _button(RUN_NOW_TEXT, holo.GO)
        self.dry_run_button = _button(DRY_RUN_TEXT, holo.GO)
        self.turn_on_button = _button(TURN_ON_TEXT, holo.AMBER_COMMIT)
        self.pause_button = _button(PAUSE_TEXT)
        self.delete_button = _button(DELETE_TEXT, holo.DANGER_GHOST)
        self.run_button.clicked.connect(lambda: self.runNow.emit(self._info.job_id))
        self.dry_run_button.clicked.connect(lambda: self.dryRun.emit(self._info.job_id))
        self.turn_on_button.clicked.connect(lambda: self.turnOn.emit(self._info.job_id))
        self.pause_button.clicked.connect(lambda: self.pause.emit(self._info.job_id))
        self.delete_button.clicked.connect(lambda: self.delete.emit(self._info.job_id))
        head = QHBoxLayout()
        head.setContentsMargins(16, 10, 12, 0)
        head.setSpacing(8)
        head.addWidget(self.name_label, 1)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(16, 2, 12, 6)
        text.setSpacing(2)
        text.addWidget(self.detail_label)
        text.addWidget(self.meta_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)
        layout.addLayout(head)
        layout.addLayout(text)
        layout.addLayout(_buttons_row((self.run_button, self.dry_run_button, self.turn_on_button, self.pause_button,
                                       self.delete_button), left=16))
        self.set_info(info)

    def info(self) -> JobRow:
        return self._info

    def buttons(self) -> list[holo.HoloButton]:
        return [button for button in (self.run_button, self.dry_run_button, self.turn_on_button, self.pause_button,
                                      self.delete_button) if not button.isHidden()]

    def set_info(self, info: JobRow) -> None:
        self._info = info
        self.name_label.setText(info.name)
        self.name_label.setToolTip(hud.plain_tooltip(info.tooltip or info.name))
        tone = STATUS_TONES.get(info.status, holo.TONE_STEEL)
        self.badge.set_badge(info.status_text, tone)
        self.set_bar(tone)
        self.detail_label.setText(info.detail)
        self.detail_label.setVisible(bool(info.detail))
        self.meta_label.setText(holo.role_text("readout", info.meta))
        self.meta_label.setVisible(bool(info.meta))
        self.run_button.setVisible(info.can_run)
        self.dry_run_button.setVisible(info.can_dry_run)
        self.turn_on_button.setVisible(info.can_turn_on)
        self.turn_on_button.setEnabled(not info.turn_on_tip)
        self.turn_on_button.setToolTip(hud.plain_tooltip(info.turn_on_tip))
        self.pause_button.setVisible(info.can_pause)
        self.pause_button.setText(RESUME_TEXT if info.paused else PAUSE_TEXT)
        self.delete_button.setVisible(info.can_delete)
        for button in (self.run_button, self.dry_run_button, self.turn_on_button, self.pause_button,
                       self.delete_button):
            button.setAccessibleName(f"{button.text()}: {info.name}")
            button.updateGeometry()
        self.updateGeometry()
        self.update()


class ActivityTile(_Tile):
    """One receipt: when, the job, what it did and from which mail, its state, and Undo while it can be undone."""

    undo = Signal(str)

    def __init__(self, info: ActivityRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self.when_label = body_label("", "readout", holo.INK_3, wrap=False)
        self.job_label = body_label("", "body_sm", holo.INK_2, wrap=False)
        self.job_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.badge = _Badge()
        self.undo_button = _button(UNDO_TEXT)
        self.undo_button.clicked.connect(lambda: self.undo.emit(self._info.key))
        self.text_label = body_label("", "body_sm", holo.INK_1)
        self.detail_label = body_label("", "body_sm", holo.INK_3)
        head = QHBoxLayout()
        head.setContentsMargins(16, 8, 8, 0)
        head.setSpacing(8)
        head.addWidget(self.when_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.job_label, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(16, 2, 12, 10)
        text.setSpacing(2)
        text.addWidget(self.text_label)
        text.addWidget(self.detail_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(head)
        layout.addLayout(text)
        self.set_info(info)

    def info(self) -> ActivityRow:
        return self._info

    def set_info(self, info: ActivityRow) -> None:
        self._info = info
        self.when_label.setText(holo.role_text("readout", info.when))
        self.job_label.setText(info.job)
        self.badge.set_badge(info.state_text, info.tone)
        self.set_bar(info.tone)
        self.text_label.setText(info.text)
        self.detail_label.setText(info.detail)
        self.detail_label.setVisible(bool(info.detail))
        self.undo_button.setVisible(info.can_undo)
        self.undo_button.setText(info.undo_text or UNDO_TEXT)
        self.undo_button.setToolTip(info.undo_tip)
        self.undo_button.setAccessibleName(f"{info.undo_text or UNDO_TEXT}: {info.text}")
        self.undo_button.updateGeometry()
        self.updateGeometry()
        self.update()


class ReportTile(_Tile):
    """One agent's report: its job and time, "Written by Claude from your mail", and the report as plain text."""

    def __init__(self, info: ReportRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self.set_bar(holo.TONE_CYAN)
        self.job_label = body_label("", "tile_title", holo.INK_1, wrap=False)
        self.job_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.when_label = body_label("", "readout", holo.INK_3, wrap=False)
        self.kicker_label = body_label(holo.role_text("label", WRITTEN_BY), "label", holo.INK_4, wrap=True)
        self.text_label = body_label("", "body_sm", holo.INK_2, selectable=True)
        head = QHBoxLayout()
        head.setContentsMargins(16, 10, 12, 0)
        head.setSpacing(8)
        head.addWidget(self.job_label, 1)
        head.addWidget(self.when_label, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(16, 4, 12, 12)
        text.setSpacing(4)
        text.addWidget(self.kicker_label)
        text.addWidget(self.text_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(head)
        layout.addLayout(text)
        self.set_info(info)

    def info(self) -> ReportRow:
        return self._info

    def set_info(self, info: ReportRow) -> None:
        self._info = info
        self.job_label.setText(info.job)
        self.when_label.setText(holo.role_text("readout", info.when))
        self.text_label.setText(info.text or info.summary)
        self.text_label.setAccessibleName(f"{WRITTEN_BY}: {info.job}")
        self.updateGeometry()


class NoticeTile(_Tile):
    """A card Jarvis puts first on the screen (consent, setup): kicker, title, lines, buttons and a note."""

    action = Signal(str, str)

    def __init__(self, info: NoticeCard, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._buttons: list[holo.HoloButton] = []
        kicker_font = holo.font("label")
        kicker_font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.kicker_label = hud.make_label("", kicker_font, holo.AMBER, wrap=True)
        self.title_label = body_label("", "tile_title", holo.INK_1)
        self._lines_box = QVBoxLayout()
        self._lines_box.setContentsMargins(0, 0, 0, 0)
        self._lines_box.setSpacing(4)
        self._buttons_box = QHBoxLayout()
        self._buttons_box.setContentsMargins(16 - holo.HoloButton.MARGIN, 4, 6, 0)
        self._buttons_box.setSpacing(holo.BUTTON_GAP - 2 * holo.HoloButton.MARGIN)
        self.note_label = body_label("", "body_sm", holo.INK_3)
        text = QVBoxLayout()
        text.setContentsMargins(16, 12, 12, 4)
        text.setSpacing(4)
        text.addWidget(self.kicker_label)
        text.addWidget(self.title_label)
        text.addLayout(self._lines_box)
        note = QVBoxLayout()
        note.setContentsMargins(16, 4, 12, 12)
        note.addWidget(self.note_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(text)
        layout.addLayout(self._buttons_box)
        layout.addLayout(note)
        self.set_info(info)

    def info(self) -> NoticeCard:
        return self._info

    def buttons(self) -> list[holo.HoloButton]:
        return list(self._buttons)

    def button(self, action: str) -> holo.HoloButton | None:
        return next((button for button in self._buttons if button.property("action") == action), None)

    def line_texts(self) -> list[str]:
        texts = []
        for index in range(self._lines_box.count()):
            widget = self._lines_box.itemAt(index).widget()
            if isinstance(widget, QLabel):
                texts.append(widget.text())
        return texts

    def set_info(self, info: NoticeCard) -> None:
        self._info = info
        self.set_bar(info.tone)
        self.kicker_label.setText(info.kicker)
        hud.set_label_color(self.kicker_label, _BARS.get(info.tone, holo.AMBER))
        self.title_label.setText(info.title)
        self.title_label.setVisible(bool(info.title))
        while self._lines_box.count():
            widget = self._lines_box.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for line in info.lines:
            self._lines_box.addWidget(body_label(line, "body_sm", holo.INK_2))
        while self._buttons_box.count():
            item = self._buttons_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._buttons = []
        for spec in info.buttons:
            button = _button(spec.text, _LOOKS.get(spec.look, holo.GHOST))
            button.setProperty("action", spec.action)
            button.setEnabled(spec.enabled)
            if spec.tip:
                button.setToolTip(spec.tip)
            button.setAccessibleName(f"{spec.text}: {info.title or info.kicker}")
            button.clicked.connect(lambda _checked=False, which=spec.action: self.action.emit(self._info.card_id,
                                                                                           which))
            self._buttons_box.addWidget(button)
            self._buttons.append(button)
        self._buttons_box.addStretch(1)
        self.note_label.setText(info.note)
        self.note_label.setVisible(bool(info.note))
        self.updateGeometry()


class AgentsStage(QScrollArea):
    """The Agents screen; see the module docstring."""

    runNowRequested = Signal(str)
    pauseRequested = Signal(str)
    deleteRequested = Signal(str)
    dryRunRequested = Signal(str)
    turnOnRequested = Signal(str)
    undoRequested = Signal(str)
    clearRequested = Signal()
    pauseAllRequested = Signal()
    linkClicked = Signal(str)
    cardAction = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        content = hud._transparent_scroll(self)
        self.panel = PanelFrame()
        self.panel.setMaximumWidth(COLUMN_MAX)
        column = QHBoxLayout(content)
        column.setContentsMargins(holo.GUTTER_SIDE, holo.GUTTER_TOP, holo.GUTTER_SIDE, 16)
        column.setSpacing(0)
        column.addStretch(1)
        column.addWidget(self.panel, 1000)
        column.addStretch(1)
        self.head = SectionHead("agents", AGENTS_TITLE)
        self.pause_all_button = _button(PAUSE_ALL_TEXT)
        self.pause_all_button.setToolTip(PAUSE_ALL_TIP)
        self.pause_all_button.clicked.connect(self.pauseAllRequested.emit)
        self.head.layout().addWidget(self.pause_all_button, 0, Qt.AlignmentFlag.AlignVCenter)
        meta_font = holo.font("label")
        meta_font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.agents_meta = hud.make_label("", meta_font, holo.INK_4, wrap=True, name="agentsMeta")
        self._notes_box = QWidget()
        self._notes = QVBoxLayout(self._notes_box)
        self._notes.setContentsMargins(0, 6, 0, 0)
        self._notes.setSpacing(2)
        self._cards_box = QWidget()
        self._cards = QVBoxLayout(self._cards_box)
        self._cards.setContentsMargins(0, 14, 0, 0)
        self._cards.setSpacing(8)
        self._card_tiles: list[NoticeTile] = []
        self.agents_empty = body_label("", "body_sm", holo.INK_3, name="agentsEmpty")
        self.agents_empty.hide()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(*PANEL_PAD)
        layout.setSpacing(0)
        layout.addWidget(self.head)
        layout.addSpacing(2)
        layout.addWidget(self.agents_meta)
        layout.addWidget(self._notes_box)
        layout.addWidget(self._cards_box)
        self._groups: dict[str, QWidget] = {}
        self._labels: dict[str, QLabel] = {}
        self._layouts: dict[str, QVBoxLayout] = {}
        self._rows: dict[str, list[QWidget]] = {}
        self._more: dict[str, holo.HoloButton] = {}
        self._show_all = {ACTIVITY_GROUP: False, REPORTS_GROUP: False}
        self.clear_button = _button(CLEAR_TEXT)
        self.clear_button.setToolTip(CLEAR_TIP)
        self.clear_button.clicked.connect(self.clearRequested.emit)
        for name in (AGENTS_GROUP, RULES_GROUP, ACTIVITY_GROUP, REPORTS_GROUP):
            group = QWidget()
            box = QVBoxLayout(group)
            box.setContentsMargins(0, 18, 0, 0)
            box.setSpacing(0)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            label = body_label(holo.role_text("label", name), "label", holo.INK_KICKER, wrap=False)
            head.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            head.addStretch(1)
            if name == REPORTS_GROUP:
                head.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignVCenter)
            rows = QVBoxLayout()
            rows.setContentsMargins(0, 6, 0, 0)
            rows.setSpacing(8)
            box.addLayout(head)
            box.addLayout(rows)
            if name in self._show_all:
                more = holo.HoloButton("", holo.LINK_CYAN)
                more.clicked.connect(lambda _checked=False, which=name: self._toggle_more(which))
                box.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
                self._more[name] = more
            layout.addWidget(group)
            self._groups[name], self._labels[name], self._layouts[name], self._rows[name] = group, label, rows, []
        layout.addSpacing(12)
        layout.addWidget(self.agents_empty)
        layout.addStretch(1)
        self._info: AgentsInfo | None = None
        self.set_agents(None)

    # ---- what the app shows -----------------------------------------------------------------

    def set_agents(self, info: AgentsInfo | None) -> None:
        """The screen (None: nothing; the Dock slot is hidden then)."""
        if info is not None and info == self._info:
            return
        self._info = info
        self.panel.setVisible(info is not None)
        if info is None:
            for name in self._rows:
                self._sync(name, ())
                self._groups[name].hide()
            self._sync_notes(())
            self._sync_cards(())
            self.agents_meta.setText("")
            self.agents_empty.hide()
            return
        self.agents_meta.setText(info.meta)
        self.agents_meta.setVisible(bool(info.meta))
        self.pause_all_button.setText(RESUME_ALL_TEXT if info.paused_all else PAUSE_ALL_TEXT)
        self.pause_all_button.setVisible(info.can_pause_all)
        self.pause_all_button.updateGeometry()
        self._sync_notes(info.notes)
        self._sync_cards(info.cards)
        self._sync(AGENTS_GROUP, info.agents)
        self._sync(RULES_GROUP, info.rules)
        for name, rows, shown in ((ACTIVITY_GROUP, info.activity, ACTIVITY_SHOWN),
                                  (REPORTS_GROUP, info.reports, REPORTS_SHOWN)):
            count = len(rows) if self._show_all[name] else shown
            self._sync(name, rows[:count])
            more = self._more[name]
            hidden = len(rows) - count
            more.setVisible(hidden > 0 or (self._show_all[name] and len(rows) > shown))
            more.setText(FEWER_TEXT if self._show_all[name] else MORE_TEXT.format(count=max(0, hidden)))
            more.updateGeometry()
        for name, rows in ((AGENTS_GROUP, info.agents), (RULES_GROUP, info.rules), (ACTIVITY_GROUP, info.activity),
                           (REPORTS_GROUP, info.reports)):
            self._groups[name].setVisible(bool(rows))
        self.clear_button.setVisible(info.can_clear)
        anything = bool(info.agents or info.rules or info.activity or info.reports or info.cards)
        self.agents_empty.setText(info.empty_text)
        self.agents_empty.setVisible(not anything and bool(info.empty_text))
        self._chain_tab_order()

    def agents_info(self) -> AgentsInfo | None:
        return self._info

    def job_rows(self) -> list[JobTile]:
        return [row for name in (AGENTS_GROUP, RULES_GROUP) for row in self._rows[name]]  # type: ignore[misc]

    def job_row(self, job_id: str) -> JobTile | None:
        return next((row for row in self.job_rows() if row.info().job_id == job_id), None)

    def activity_rows(self) -> list[ActivityTile]:
        return list(self._rows[ACTIVITY_GROUP])  # type: ignore[arg-type]

    def activity_row(self, key: str) -> ActivityTile | None:
        return next((row for row in self.activity_rows() if row.info().key == key), None)

    def report_rows(self) -> list[ReportTile]:
        return list(self._rows[REPORTS_GROUP])  # type: ignore[arg-type]

    def notice_cards(self) -> list[NoticeTile]:
        return list(self._card_tiles)

    def notice_card(self, card_id: str) -> NoticeTile | None:
        return next((tile for tile in self._card_tiles if tile.info().card_id == card_id), None)

    def notes(self) -> list[str]:
        texts = []
        for index in range(self._notes.count()):
            widget = self._notes.itemAt(index).widget()
            label = widget.findChild(QLabel) if widget is not None else None
            if label is not None:
                texts.append(label.text())
        return texts

    def group_visible(self, name: str) -> bool:
        return not self._groups[name].isHidden()

    def more_button(self, name: str) -> holo.HoloButton:
        return self._more[name]

    def reveal(self) -> None:
        self.verticalScrollBar().setValue(0)

    # ---- inside -------------------------------------------------------------------------------

    def _make(self, name: str, info: Any) -> QWidget:
        if name in (AGENTS_GROUP, RULES_GROUP):
            row: Any = JobTile(info)
            row.runNow.connect(self.runNowRequested)
            row.pause.connect(self.pauseRequested)
            row.delete.connect(self.deleteRequested)
            row.dryRun.connect(self.dryRunRequested)
            row.turnOn.connect(self.turnOnRequested)
        elif name == ACTIVITY_GROUP:
            row = ActivityTile(info)
            row.undo.connect(self.undoRequested)
        else:
            row = ReportTile(info)
        return row

    def _sync(self, name: str, infos: Sequence[Any]) -> None:
        rows, layout = self._rows[name], self._layouts[name]
        while len(rows) > len(infos):
            row = rows.pop()
            layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for index, info in enumerate(infos):
            if index < len(rows):
                if rows[index].info() != info:  # type: ignore[attr-defined]
                    rows[index].set_info(info)  # type: ignore[attr-defined]
                continue
            row = self._make(name, info)
            layout.addWidget(row)
            rows.append(row)

    def _sync_cards(self, cards: Sequence[NoticeCard]) -> None:
        while len(self._card_tiles) > len(cards):
            tile = self._card_tiles.pop()
            self._cards.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        for index, info in enumerate(cards):
            if index < len(self._card_tiles):
                if self._card_tiles[index].info() != info:
                    self._card_tiles[index].set_info(info)
                continue
            tile = NoticeTile(info)
            tile.action.connect(self.cardAction)
            self._cards.addWidget(tile)
            self._card_tiles.append(tile)
        self._cards_box.setVisible(bool(cards))

    def _sync_notes(self, notes: Sequence[tuple[str, str, str]]) -> None:
        while self._notes.count():
            widget = self._notes.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for text, link_text, link_id in notes:
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(body_label(text, "body_sm", holo.INK_2), 1, Qt.AlignmentFlag.AlignVCenter)
            if link_text and link_id:
                button = holo.HoloButton(link_text, holo.LINK_CYAN)
                button.clicked.connect(lambda _checked=False, target=link_id: self.linkClicked.emit(target))
                row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self._notes.addWidget(line)
        self._notes_box.setVisible(bool(notes))

    def _toggle_more(self, name: str) -> None:
        self._show_all[name] = not self._show_all[name]
        info, self._info = self._info, None
        self.set_agents(info)

    def _chain_tab_order(self) -> None:
        """Tab goes down the screen: Pause all, the cards' buttons, then each row's buttons."""
        chain: list[QWidget] = [self.pause_all_button]
        for tile in self._card_tiles:
            chain += tile.buttons()
        for row in self.job_rows():
            chain += row.buttons()
        for row in self.activity_rows():
            if not row.undo_button.isHidden():
                chain.append(row.undo_button)
        chain.append(self.clear_button)
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(COLUMN_MAX + 2 * holo.GUTTER_SIDE, 480)
