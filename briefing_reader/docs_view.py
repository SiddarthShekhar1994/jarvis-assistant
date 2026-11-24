"""The Documents screen (Ctrl+0; build plan item 5): documents and datasheets kept on this PC, in the hologram look and
the canvas look while the canvas is on.

Public API
==========

``DocsStage``
    The screen: at the left the list panel (DOCUMENTS, New document, New sheet, one ``ItemRow`` each with its meta and
    an amber "N WAIT" while Jarvis's changes wait for Keep, then DELETED with Restore for 30 days), at the right the
    editor (``doc_editor`` or ``sheet_editor``, or the empty line). ``set_items(rows, deleted)``, ``select(item_id)``,
    ``show_doc(DocView)``, ``show_sheet(sheet)``, ``show_empty()``, ``set_notice(text, undo=False)`` (the line under
    the list: "Deleted." with Undo), ``set_read_only(text)``. Signals: ``newDocRequested``, ``newSheetRequested``,
    ``itemPicked(id)``, ``deletedRestoreRequested(id)``, ``undoRequested()``, ``hidden()`` (the screen went: the
    controller saves at once).

``DocEditor``
    A document: its name (rename on Enter or leaving the field: ``renameRequested``), Read / Edit, Versions, Export .md,
    Delete. Read: a read-only rich text view of the markdown (``SafeDocument``: no HTML, no picture or file is ever
    loaded, links are not clickable). Edit: the markdown source in a plain text field (what he types is his:
    ``textEdited(id, text)`` on every change; Ctrl+Z undoes). The proposals' place (``proposals_box``; part 2) sits
    above the text. The versions list (``set_versions``: Restore on each, ``versionRestoreRequested(id, name)``).

``SheetEditor``
    A datasheet: its name, Add row, Add column (a name, a type and a choice column's choices; refused with Jarvis's
    own words), Open link (the current cell's link, only through ``openLinkRequested(url)``: the controller checks it),
    Versions, Export .csv, Delete; the pending-rows band (amber: "3 rows wait on you", Keep all / Discard all; part 2);
    a QTableView over ``SheetModel`` with typed editors (``SheetDelegate``: a list for a choice column, a tick for a
    checkbox); a typed value that doesn't fit its column is refused under the table ("That isn't a date ..."). Every
    change goes on the sheet's own QUndoStack (Ctrl+Z / Ctrl+Y) and emits ``sheetEdited(id)``.

Nothing here reads or writes a file: docs_ui.DocsController does. Every text (a name, a heading, a cell) is plain text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QRectF, QSize,
                            Qt, QUrl, Signal)
from PySide6.QtGui import (QColor, QFontMetricsF, QKeySequence, QPainter, QPalette, QPen, QShortcut,
                           QTextDocument, QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableView, QTextEdit, QVBoxLayout, QWidget)

from . import holo, hud
from .docs import model
from .docs.store import Row, SheetData, VersionInfo
from .memory_view import PanelFrame, Rule, SectionHead, body_label

DOCS_TITLE = "DOCUMENTS"
DELETED_GROUP = "DELETED"
VERSIONS_GROUP = "VERSIONS"
NEW_DOC_TEXT = "New document"
NEW_SHEET_TEXT = "New sheet"
READ_TEXT = "Read"
EDIT_TEXT = "Edit"
VERSIONS_TEXT = "Versions"
EXPORT_MD_TEXT = "Export .md"
EXPORT_CSV_TEXT = "Export .csv"
DELETE_TEXT = "Delete"
RESTORE_TEXT = "Restore"
UNDO_TEXT = "Undo"
ADD_ROW_TEXT = "Add row"
ADD_COLUMN_TEXT = "Add column"
ADD_TEXT = "Add"
CANCEL_TEXT = "Cancel"
OPEN_LINK_TEXT = "Open link"
OPEN_LINK_TIP = "Open the link in the selected cell in your browser"
KEEP_ALL_TEXT = "Keep all"
DISCARD_ALL_TEXT = "Discard all"
SAVE_HERE_TEXT = "Save here"
CHOOSE_ANOTHER_TEXT = "Choose another"
EMPTY_TEXT = "Pick a document or a sheet, or make a new one. Everything here stays on this PC."
LIST_EMPTY_TEXT = "Nothing yet."
NAME_PLACEHOLDER = "Name"
COLUMN_NAME_PLACEHOLDER = "Column name"
CHOICES_PLACEHOLDER = "Choices, comma between them"
DOC_PLACEHOLDER = "Write here. Markdown works: # a heading, - a list, **bold**."
WAIT_CHIP = "{count} WAIT"
DOC_META = "DOC · {words}"
SHEET_META = "SHEET · {rows}"
PANEL_PAD = (16, 16, 16, 14)
LIST_WIDTH = 288
ROW_HEIGHT = 56
PENDING_ROWS_SHOWN = 4
PENDING_ROW_TEXT = "{count} rows wait on you"
PENDING_ONE_TEXT = "1 row waits on you"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def words_text(count: int) -> str:
    return f"{count:,} {_plural(count, 'WORD', 'WORDS')}"


def rows_text(count: int) -> str:
    return f"{count:,} {_plural(count, 'ROW', 'ROWS')}"


def item_meta(kind: str, words: int, rows: int) -> str:
    """"DOC · 1,240 WORDS" / "SHEET · 42 ROWS" (the list's meta and the frames')."""
    if kind == model.KIND_DOC:
        return DOC_META.format(words=words_text(words))
    return SHEET_META.format(rows=rows_text(rows))


def _field_style(edit: QWidget) -> None:
    """A text field in the screen's look (square in the canvas look)."""
    radius = holo.CANVAS_RADIUS if holo.canvas_look() else 6
    ground = "#03090C" if holo.canvas_look() else holo.FIELD
    edge = "rgba(143, 235, 255, 153)" if holo.canvas_look() else "rgba(60, 211, 238, 110)"
    focus = "#FFFFFF" if holo.canvas_look() else holo.HOLO
    name = type(edit).__name__
    edit.setStyleSheet(f"{name} {{ background: {ground}; border: 1px solid {edge}; border-radius: {radius:g}px;"
                       f" padding: 4px 8px; color: {holo.INK_1}; selection-background-color: rgba(53, 211, 240, 82);"
                       f" selection-color: #FFFFFF; }} {name}:focus {{ border: 1px solid {focus}; }}")


def _ink(widget: QWidget, color: str = holo.INK_1) -> None:
    palette = widget.palette()
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(holo.INK_4))
    palette.setColor(QPalette.ColorRole.Highlight, holo.alpha(holo.HOLO, 0.35))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(holo.INK_1))
    widget.setPalette(palette)


def _button(text: str, variant: str = holo.GHOST, tip: str = "") -> holo.HoloButton:
    button = holo.HoloButton(text, variant, size=holo.SIZE_SM)
    if tip:
        button.setToolTip(tip)
    return button


@dataclass(frozen=True)
class ItemRowInfo:
    """One document or sheet as the list shows it."""

    item_id: str
    kind: str
    name: str
    meta: str
    pending: int = 0
    when: str = ""            # the deleted list: "DELETED NOV 22"


class ItemRow(QAbstractButton):
    """A list row: the item's name, its meta ("DOC · 1,240 WORDS") and an amber "3 WAIT" while changes wait."""

    def __init__(self, info: ItemRowInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self._selected = False
        self._hover = False
        self._keyboard = False
        self.setFixedHeight(ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_info(info)

    def set_info(self, info: ItemRowInfo) -> None:
        self.info = info
        waits = f", {info.pending} {_plural(info.pending, 'change waits', 'changes wait')} on you" if info.pending \
            else ""
        self.setAccessibleName(f"{info.name}, {info.meta.lower()}{waits}")
        self.update()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def chip_text(self) -> str:
        return WAIT_CHIP.format(count=self.info.pending) if self.info.pending else ""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(LIST_WIDTH - 32, ROW_HEIGHT)

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._keyboard = holo.keyboard_focus(event.reason(), self._keyboard)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._keyboard = False
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            if self._selected:
                if holo.canvas_look():
                    holo.paint_canvas_frame(painter, rect, holo.CANVAS_SELECTED)
                else:
                    painter.setPen(QPen(holo.alpha(holo.HOLO, 0.45), 1.0))
                    painter.setBrush(holo.alpha(holo.HOLO, 0.08))
                    painter.drawRoundedRect(rect, 6, 6)
            elif self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(holo.alpha(holo.HOLO, 0.05))
                painter.drawRect(rect) if holo.canvas_look() else painter.drawRoundedRect(rect, 6, 6)
            chip = self.chip_text()
            chip_w = 0.0
            if chip:
                font = holo.font("badge")
                chip_w = QFontMetricsF(font).horizontalAdvance(chip) + 14
                box = QRectF(rect.right() - 10 - chip_w, rect.top() + 10, chip_w, 18)
                amber = QColor(holo.CANVAS_AMBER if holo.canvas_look() else holo.AMBER)
                painter.setPen(QPen(amber, 1.0))
                painter.setBrush(holo.alpha(amber, 0.12))
                painter.drawRect(box) if holo.canvas_look() else painter.drawRoundedRect(box, 4, 4)
                painter.setFont(font)
                painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), chip)
            name_font = holo.font("body")
            painter.setFont(name_font)
            painter.setPen(QColor(holo.INK_1))
            name_box = QRectF(rect.left() + 12, rect.top() + 7, rect.width() - 24 - (chip_w + 8 if chip else 0), 22)
            painter.drawText(name_box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             QFontMetricsF(name_font).elidedText(self.info.name, Qt.TextElideMode.ElideRight,
                                                                 name_box.width()))
            meta_font = holo.font("readout")
            painter.setFont(meta_font)
            painter.setPen(QColor(holo.INK_3))
            meta = self.info.when or self.info.meta
            meta_box = QRectF(rect.left() + 12, rect.top() + 30, rect.width() - 24, 18)
            painter.drawText(meta_box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             QFontMetricsF(meta_font).elidedText(meta.upper(), Qt.TextElideMode.ElideRight,
                                                                 meta_box.width()))
            if self._keyboard and self.hasFocus():
                holo.paint_focus_ring(painter, rect, 0 if holo.canvas_look() else 6)
        finally:
            painter.end()


class DeletedRow(QWidget):
    """A deleted item: its name, "DELETED NOV 22" and Restore."""

    def __init__(self, info: ItemRowInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.name_label = body_label(info.name, "body_sm", holo.INK_2, wrap=False)
        self.when_label = body_label(info.when.upper(), "readout", holo.INK_4, wrap=False)
        self.restore_button = _button(RESTORE_TEXT, holo.LINK_CYAN)
        self.restore_button.setAccessibleName(f"Restore {info.name}")
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addWidget(self.name_label)
        texts.addWidget(self.when_label)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 0, 4)
        row.setSpacing(8)
        row.addLayout(texts, 1)
        row.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignVCenter)


class SafeDocument(QTextDocument):
    """The read view's document: markdown without HTML, and nothing is ever loaded for it (no picture, no file, no
    link target): loadResource gives nothing back."""

    def loadResource(self, _kind: int, _name: QUrl) -> Any:  # noqa: N802 - Qt override
        return None


class ReadView(QTextEdit):
    """The read view: the markdown drawn as text, read only; selecting text works, links are not clickable."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc = SafeDocument(self)
        self.setDocument(self._doc)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setFont(holo.font("body"))
        _ink(self)
        self._source = ""

    def set_markdown(self, text: str) -> None:
        self._source = text
        features = (QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                    | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        self._doc.setDefaultFont(holo.font("body"))
        self._doc.setMarkdown(text, features)

    def source(self) -> str:
        return self._source

    def createMimeDataFromSelection(self) -> Any:  # noqa: N802 - Qt override
        mime = super().createMimeDataFromSelection()
        mime.setHtml("")                         # a copy carries plain text only
        return mime


class SourceEdit(QPlainTextEdit):
    """The edit view: the markdown as he writes it (no conversion back and forth)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(holo.font("body"))
        self.setPlaceholderText(DOC_PLACEHOLDER)
        self.setTabChangesFocus(False)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        _field_style(self)
        _ink(self)


class NoticeLine(QWidget):
    """A line of Jarvis's own words with up to two buttons (Undo; Save here / Choose another)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = body_label("", "body_sm", holo.INK_2)
        self.first = _button("", holo.LINK_CYAN)
        self.second = _button("", holo.LINK_CYAN)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(6)
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.first, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.second, 0, Qt.AlignmentFlag.AlignVCenter)
        self.show_text("")

    def show_text(self, text: str, first: str = "", second: str = "", *, tone: str = holo.INK_2) -> None:
        self.label.setText(text)
        hud.set_label_color(self.label, tone)
        self.first.setText(first)
        self.first.setVisible(bool(first))
        self.second.setText(second)
        self.second.setVisible(bool(second))
        self.setVisible(bool(text))

    def text(self) -> str:
        return self.label.text() if not self.isHidden() else ""


class VersionRow(QWidget):
    def __init__(self, info: VersionInfo, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.label = body_label(label, "body_sm", holo.INK_2, wrap=False)
        self.restore_button = _button(RESTORE_TEXT, holo.LINK_CYAN)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignVCenter)


class VersionsBox(QWidget):
    """The VERSIONS list (newest first), each with Restore."""

    restoreRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 8)
        self._layout.setSpacing(2)
        self.head = body_label(holo.role_text("label", VERSIONS_GROUP), "label", holo.INK_KICKER, wrap=False)
        self._layout.addWidget(self.head)
        self.empty_label = body_label("No versions yet: one is kept before every Keep and Restore, and while you "
                                      "edit.", "body_sm", holo.INK_3)
        self._layout.addWidget(self.empty_label)
        self.rows: list[VersionRow] = []
        self.hide()

    def set_versions(self, rows: Sequence[tuple[VersionInfo, str]]) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        for info, label in rows:
            row = VersionRow(info, label)
            row.restore_button.clicked.connect(lambda _c=False, n=info.name: self.restoreRequested.emit(n))
            self._layout.addWidget(row)
            self.rows.append(row)
        self.empty_label.setVisible(not self.rows)


# ---- what Jarvis proposes (part 2) ------------------------------------------------------------------------------

ASK_PLACEHOLDER = "Ask Jarvis to change this..."
ASK_NAME = "Ask Jarvis"
KEEP_TEXT = "Keep"
DISCARD_TEXT = "Discard"
AT_END_TEXT = "Add at the end"
STALE_TEXT = "Its section has gone since Jarvis wrote this."
CHANGED_TEXT = "Changed since Jarvis read it. Keep it on its own if you still want it (Keep all leaves it)."
CHANGED_WORDS = "CHANGED SINCE JARVIS READ IT"
PROPOSES = "JARVIS PROPOSES"
WAITS_TEXT = "{count} changes wait on you"
WAITS_ONE_TEXT = "1 change waits on you"


class AskLine(QWidget):
    """The editor's own "Ask Jarvis to change this..." line (one run for this item; Enter or the arrow asks)."""

    asked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.input = QLineEdit()
        self.input.setFrame(False)
        self.input.setPlaceholderText(ASK_PLACEHOLDER)
        self.input.setAccessibleName(ASK_PLACEHOLDER)
        self.input.setMaxLength(2000)
        self.input.setFont(holo.font("body_sm"))
        self.input.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; }")
        _ink(self.input)
        self.input.installEventFilter(self)
        self.send_button = holo.HoloButton(ASK_NAME, holo.ICON, icon="send_arrow", diameter=32)
        self.send_button.setToolTip(ASK_NAME)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 4, 0)
        row.setSpacing(6)
        row.addWidget(self.input, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.input.returnPressed.connect(self._ask)
        self.send_button.clicked.connect(self._ask)

    def _ask(self) -> None:
        text = " ".join(self.input.text().split())
        if text and self.input.isEnabled():
            self.asked.emit(text)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.input and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return super().eventFilter(watched, event)

    def set_running(self, running: bool) -> None:
        self.input.setEnabled(not running)
        self.send_button.setEnabled(not running)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not self.input.isEnabled():
                painter.setOpacity(0.5)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            focus = self.input.hasFocus()
            if holo.canvas_look():
                painter.fillRect(rect, QColor("#03090C"))
                holo.paint_canvas_glow(painter, rect, holo.CANVAS_GLOW, 0.9 if focus else 0.5, depth=10.0)
                painter.setPen(QPen(holo.CANVAS_EDGE_HI if focus else QColor(143, 235, 255, 153), 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                return
            painter.setPen(QPen(QColor(holo.HOLO) if focus else holo.LINE_INPUT, 1.0))
            painter.setBrush(QColor(holo.FIELD))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        finally:
            painter.end()


@dataclass(frozen=True)
class ProposalInfo:
    """One pending change of a document as its tile shows it."""

    pending_id: str
    head: str                                   # "CHANGE “Plan”", "ADD AFTER “Plan”", "ADD AT THE END"
    segments: tuple[tuple[str, str], ...]        # docs.proposals.diff: ("same" | "removed" | "added", text)
    source: str = ""                            # "FROM ASK", "FROM INTERNSHIP WATCH"
    stale: bool = False                         # its section has gone: "Add at the end"
    changed: bool = False                       # its section changed since Jarvis read it: Keep on its own only


class DiffView(QTextEdit):
    """A change's before and after, word by word: what goes struck through and dim, what comes in cyan (plain text
    inserted with formats: nothing in it is read as HTML or markdown)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDocument(SafeDocument(self))
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMaximumHeight(220)
        self.setFont(holo.font("body_sm"))
        _ink(self, holo.INK_2)

    def set_segments(self, segments: Sequence[tuple[str, str]]) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        self.clear()
        cursor = QTextCursor(self.document())
        for kind, text in segments:
            fmt = QTextCharFormat()
            fmt.setFont(holo.font("body_sm"))
            if kind == "removed":
                fmt.setFontStrikeOut(True)
                fmt.setForeground(QColor(holo.INK_4))
            elif kind == "added":
                fmt.setForeground(QColor(holo.HOLO))
            else:
                fmt.setForeground(QColor(holo.INK_2))
            cursor.insertText(text, fmt)
        height = int(self.document().size().height()) + 8
        self.setFixedHeight(max(28, min(220, height)))

    def plain(self) -> str:
        return self.toPlainText()


class ProposalTile(QWidget):
    """A pending change: the needs-you tile (amber), its head, its before and after, Discard and Keep (or "Add at the
    end" once its section has gone)."""

    keepRequested = Signal(str)
    discardRequested = Signal(str)
    atEndRequested = Signal(str)

    def __init__(self, info: ProposalInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.head_label = body_label("", "label", holo.AMBER, wrap=False)
        self.source_label = body_label("", "readout", holo.INK_3, wrap=False)
        self.diff = DiffView()
        self.stale_label = body_label(STALE_TEXT, "body_sm", holo.INK_2)
        self.discard_button = _button(DISCARD_TEXT, holo.AMBER_GHOST)
        self.keep_button = _button(KEEP_TEXT, holo.AMBER_COMMIT)
        self.at_end_button = _button(AT_END_TEXT, holo.AMBER_COMMIT)
        self.discard_button.clicked.connect(lambda: self.discardRequested.emit(self.info.pending_id))
        self.keep_button.clicked.connect(lambda: self.keepRequested.emit(self.info.pending_id))
        self.at_end_button.clicked.connect(lambda: self.atEndRequested.emit(self.info.pending_id))
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.head_label, 1)
        top.addWidget(self.source_label, 0)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addStretch(1)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.at_end_button)
        buttons.addWidget(self.keep_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self.diff)
        layout.addWidget(self.stale_label)
        layout.addLayout(buttons)
        self.set_info(info)

    def set_info(self, info: ProposalInfo) -> None:
        self.info = info
        self.head_label.setText(f"{PROPOSES} \u00b7 {info.head}")
        self.source_label.setText(info.source.upper())
        self.source_label.setVisible(bool(info.source))
        self.diff.set_segments(info.segments)
        self.stale_label.setText(STALE_TEXT if info.stale else CHANGED_TEXT)
        self.stale_label.setVisible(info.stale or info.changed)
        self.keep_button.setVisible(not info.stale)
        self.at_end_button.setVisible(info.stale)
        self.setAccessibleName(f"Jarvis proposes: {info.head.lower()}")

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            holo.paint_tile(painter, QRectF(self.rect()), holo.TILE_NEEDS)
        finally:
            painter.end()


class ProposalsBox(QWidget):
    """The document's pending changes: "2 changes wait on you" with Keep all, then one tile each."""

    keepRequested = Signal(str)
    discardRequested = Signal(str)
    atEndRequested = Signal(str)
    keepAllRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_label = body_label("", "label", holo.AMBER, wrap=False)
        self.keep_all_button = _button(KEEP_ALL_TEXT, holo.AMBER_COMMIT)
        self.keep_all_button.clicked.connect(self.keepAllRequested)
        self.more_label = body_label("", "body_sm", holo.INK_3)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.keep_all_button)
        self._tiles_layout = QVBoxLayout()
        self._tiles_layout.setContentsMargins(0, 0, 0, 0)
        self._tiles_layout.setSpacing(8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addLayout(self._tiles_layout)
        layout.addWidget(self.more_label)
        self.tiles: list[ProposalTile] = []
        self.hide()

    def set_proposals(self, infos: Sequence[ProposalInfo], *, shown: int = 3) -> None:
        for tile in self.tiles:
            tile.setParent(None)
            tile.deleteLater()
        self.tiles = []
        for info in list(infos)[:shown]:
            tile = ProposalTile(info)
            tile.keepRequested.connect(self.keepRequested)
            tile.discardRequested.connect(self.discardRequested)
            tile.atEndRequested.connect(self.atEndRequested)
            self._tiles_layout.addWidget(tile)
            self.tiles.append(tile)
        total = len(infos)
        self.title_label.setText(holo.role_text("label", WAITS_ONE_TEXT if total == 1 else
                                                WAITS_TEXT.format(count=total)))
        self.keep_all_button.setVisible(total > 1)
        more = total - len(self.tiles)
        self.more_label.setText(f"+{more} more: Keep or Discard these first" if more > 0 else "")
        self.more_label.setVisible(more > 0)
        self.setVisible(total > 0)

    def tile(self, pending_id: str) -> ProposalTile | None:
        return next((tile for tile in self.tiles if tile.info.pending_id == pending_id), None)


class _Head(QWidget):
    """An editor's head: the name field and the buttons right of it."""

    def __init__(self, buttons: Sequence[QWidget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(NAME_PLACEHOLDER)
        self.name_edit.setMaxLength(model.MAX_NAME)
        self.name_edit.setFont(holo.font("tile_title"))
        self.name_edit.setAccessibleName("Name")
        self.name_edit.setFrame(False)
        self.name_edit.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; }")
        _ink(self.name_edit)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.name_edit, 1)
        self.meta_label = body_label("", "readout", holo.INK_3, wrap=False)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        for widget in buttons:
            bar.addWidget(widget, 0)
        bar.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(top)
        column.addWidget(self.meta_label)
        column.addLayout(bar)


@dataclass(frozen=True)
class DocView:
    """A document as the editor shows it."""

    item_id: str
    name: str
    text: str
    meta: str = ""
    read_only: bool = False
    read_only_text: str = ""


class DocEditor(PanelFrame):
    """One document; see the module docstring."""

    textEdited = Signal(str, str)
    renameRequested = Signal(str, str)
    deleteRequested = Signal(str)
    exportRequested = Signal(str)
    versionsRequested = Signal(str)
    versionRestoreRequested = Signal(str, str)
    noticeFirst = Signal()
    noticeSecond = Signal()
    askRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id = ""
        self.read_button = _button(READ_TEXT)
        self.edit_button = _button(EDIT_TEXT)
        for button in (self.read_button, self.edit_button):
            button.setCheckable(True)
        self.versions_button = _button(VERSIONS_TEXT)
        self.export_button = _button(EXPORT_MD_TEXT, tip="Save the kept text as a .md file (what waits for Keep "
                                                           "is not in it)")
        self.delete_button = _button(DELETE_TEXT, holo.DANGER_GHOST, tip="Move it to Deleted (kept 30 days)")
        self.head = _Head((self.read_button, self.edit_button, self.versions_button, self.export_button,
                           self.delete_button))
        self.name_edit = self.head.name_edit
        self.banner = body_label("", "body_sm", holo.ALERT_TX)
        self.banner.hide()
        self.notice = NoticeLine()
        self.notice.first.clicked.connect(self.noticeFirst)
        self.notice.second.clicked.connect(self.noticeSecond)
        self.proposals_box = ProposalsBox()        # Jarvis's changes, each waiting for Keep
        self.ask_line = AskLine()
        self.ask_line.asked.connect(lambda text: self.askRequested.emit(self.item_id, text))
        self.ask_line.hide()
        self.versions_box = VersionsBox()
        self.versions_box.restoreRequested.connect(lambda name: self.versionRestoreRequested.emit(self.item_id, name))
        self.read_view = ReadView()
        self.source_edit = SourceEdit()
        self.views = QStackedWidget()
        self.views.addWidget(self.read_view)
        self.views.addWidget(self.source_edit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_PAD)
        layout.setSpacing(8)
        layout.addWidget(self.head)
        layout.addWidget(self.banner)
        layout.addWidget(self.notice)
        layout.addWidget(self.versions_box)
        layout.addWidget(self.proposals_box)
        layout.addWidget(Rule())
        layout.addWidget(self.views, 1)
        layout.addWidget(self.ask_line)
        self.read_button.clicked.connect(lambda: self.set_mode(READ_TEXT))
        self.edit_button.clicked.connect(lambda: self.set_mode(EDIT_TEXT))
        self.versions_button.clicked.connect(lambda: self.versionsRequested.emit(self.item_id))
        self.export_button.clicked.connect(lambda: self.exportRequested.emit(self.item_id))
        self.delete_button.clicked.connect(lambda: self.deleteRequested.emit(self.item_id))
        self.name_edit.editingFinished.connect(self._rename)
        self.source_edit.textChanged.connect(self._typed)
        self._loading = False
        self._read_only = False
        self.set_mode(READ_TEXT)

    def show_doc(self, view: DocView, *, keep_mode: bool = False, focus: bool = True) -> None:
        """``view``'s text in both views; another document starts in Read (an empty one in Edit). ``focus`` False
        (the canvas's pane): the keyboard stays where it is."""
        same = view.item_id == self.item_id
        self.item_id = view.item_id
        self._loading = True
        try:
            if self.name_edit.text() != view.name and not self.name_edit.hasFocus():
                self.name_edit.setText(view.name)
            self.head.meta_label.setText(view.meta.upper())
            self.read_view.set_markdown(view.text)
            if self.source_edit.toPlainText() != view.text:
                self.source_edit.setPlainText(view.text)
        finally:
            self._loading = False
        self.set_read_only(view.read_only, view.read_only_text)
        if not same:
            self.versions_box.hide()
            self.notice.show_text("")
        if not (same and keep_mode):
            self.set_mode(EDIT_TEXT if not view.text and not view.read_only else READ_TEXT, focus=focus)

    def set_mode(self, mode: str, *, focus: bool = True) -> None:
        if mode == EDIT_TEXT and self._read_only:
            mode = READ_TEXT
        edit = mode == EDIT_TEXT
        if not edit:
            self.read_view.set_markdown(self.source_edit.toPlainText())
        self.views.setCurrentWidget(self.source_edit if edit else self.read_view)
        self.read_button.setChecked(not edit)
        self.edit_button.setChecked(edit)
        if edit and focus:
            self.source_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def mode(self) -> str:
        return EDIT_TEXT if self.views.currentWidget() is self.source_edit else READ_TEXT

    def set_read_only(self, read_only: bool, text: str = "") -> None:
        self._read_only = bool(read_only)
        self.source_edit.setReadOnly(self._read_only)
        self.edit_button.setEnabled(not self._read_only)
        self.name_edit.setReadOnly(self._read_only)
        self.delete_button.setEnabled(not self._read_only)
        self.versions_button.setEnabled(not self._read_only)
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        if self._read_only and self.mode() == EDIT_TEXT:
            self.set_mode(READ_TEXT)

    def text(self) -> str:
        return self.source_edit.toPlainText()

    def _typed(self) -> None:
        if not self._loading and self.item_id:
            self.textEdited.emit(self.item_id, self.source_edit.toPlainText())

    def _rename(self) -> None:
        if self.item_id and not self._read_only:
            self.renameRequested.emit(self.item_id, self.name_edit.text())

    def set_versions(self, rows: Sequence[tuple[VersionInfo, str]], *, shown: bool = True) -> None:
        self.versions_box.set_versions(rows)
        self.versions_box.setVisible(shown)

    def versions_shown(self) -> bool:
        return not self.versions_box.isHidden()


# ---- sheets ------------------------------------------------------------------------------------------------------

class SheetModel(QAbstractTableModel):
    """The kept rows of a sheet (a copy the editor changes; the controller saves it)."""

    refused = Signal(str)           # a reason word: what was typed doesn't fit its column
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sheet = SheetData("", "")
        self.undo = QUndoStack(self)
        self.read_only = False

    def set_sheet(self, sheet: SheetData) -> None:
        self.beginResetModel()
        self.sheet = sheet
        self.read_only = sheet.read_only
        self.endResetModel()
        self.undo.clear()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.sheet.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.sheet.columns)

    def column(self, index: int) -> model.Column:
        return self.sheet.columns[index]

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.read_only:
            return base
        if self.column(index.column()).type == model.TYPE_CHECKBOX:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column = self.column(index.column())
        value = self.sheet.rows[index.row()].cells.get(column.id)
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if column.type == model.TYPE_CHECKBOX else model.show_value(column, value)
        if role == Qt.ItemDataRole.EditRole:
            return model.edit_value(column, value)
        if role == Qt.ItemDataRole.CheckStateRole and column.type == model.TYPE_CHECKBOX:
            return Qt.CheckState.Checked if value is True else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.TextAlignmentRole and column.type == model.TYPE_NUMBER:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column.type == model.TYPE_LINK:
            return QColor(holo.HOLO)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802 - Qt override
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.sheet.columns):
                column = self.sheet.columns[section]
                return f"{column.name}  · {column.type_name.upper()}"
            return None
        return str(section + 1)

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any,  # noqa: N802 - Qt override
                role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or self.read_only:
            return False
        column = self.column(index.column())
        if role == Qt.ItemDataRole.CheckStateRole:
            raw: Any = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value, 2)
        elif role == Qt.ItemDataRole.EditRole:
            raw = value
        else:
            return False
        parsed, reason = model.parse_value(column, raw)
        if reason:
            self.refused.emit(reason)
            return False
        row = self.sheet.rows[index.row()]
        old = row.cells.get(column.id)
        if old == parsed and type(old) is type(parsed):
            return False
        self.undo.push(_SetCell(self, row.id, column.id, old, parsed))
        return True

    # ---- the changes (each one an undo step) ----------------------------------------------------------------

    def _row_index(self, row_id: str) -> int:
        return next((i for i, row in enumerate(self.sheet.rows) if row.id == row_id), -1)

    def apply_cell(self, row_id: str, column_id: str, value: Any) -> None:
        r = self._row_index(row_id)
        c = next((i for i, col in enumerate(self.sheet.columns) if col.id == column_id), -1)
        if r < 0 or c < 0:
            return
        if value is None:
            self.sheet.rows[r].cells.pop(column_id, None)
        else:
            self.sheet.rows[r].cells[column_id] = value
        index = self.index(r, c)
        self.dataChanged.emit(index, index)
        self.changed.emit()

    def add_row(self, made: str = "") -> bool:
        if self.read_only or len(self.sheet.rows) >= model.MAX_ROWS:
            self.refused.emit(model.REASON_TOO_MANY)
            return False
        self.undo.push(_AddRow(self, Row(model.new_row_id(), {}, "owner", made)))
        return True

    def insert_row(self, row: Row, at: int) -> None:
        at = max(0, min(at, len(self.sheet.rows)))
        self.beginInsertRows(QModelIndex(), at, at)
        self.sheet.rows.insert(at, row)
        self.endInsertRows()
        self.changed.emit()

    def remove_row(self, row_id: str) -> None:
        r = self._row_index(row_id)
        if r < 0:
            return
        self.beginRemoveRows(QModelIndex(), r, r)
        del self.sheet.rows[r]
        self.endRemoveRows()
        self.changed.emit()

    def add_column(self, column: model.Column) -> bool:
        if self.read_only or len(self.sheet.columns) >= model.MAX_COLUMNS:
            self.refused.emit(model.REASON_TOO_MANY)
            return False
        self.undo.push(_AddColumn(self, column))
        return True

    def insert_column(self, column: model.Column, at: int) -> None:
        at = max(0, min(at, len(self.sheet.columns)))
        self.beginInsertColumns(QModelIndex(), at, at)
        self.sheet.columns.insert(at, column)
        self.endInsertColumns()
        self.changed.emit()

    def remove_column(self, column_id: str) -> None:
        c = next((i for i, col in enumerate(self.sheet.columns) if col.id == column_id), -1)
        if c < 0:
            return
        self.beginRemoveColumns(QModelIndex(), c, c)
        del self.sheet.columns[c]
        for row in self.sheet.rows:
            row.cells.pop(column_id, None)
        self.sheet.keys = [key for key in self.sheet.keys if key != column_id]
        self.endRemoveColumns()
        self.changed.emit()


class _SetCell(QUndoCommand):
    def __init__(self, sheet_model: SheetModel, row_id: str, column_id: str, old: Any, new: Any) -> None:
        super().__init__("Change a cell")
        self._m, self._row, self._col, self._old, self._new = sheet_model, row_id, column_id, old, new

    def redo(self) -> None:
        self._m.apply_cell(self._row, self._col, self._new)

    def undo(self) -> None:
        self._m.apply_cell(self._row, self._col, self._old)


class _AddRow(QUndoCommand):
    def __init__(self, sheet_model: SheetModel, row: Row) -> None:
        super().__init__("Add a row")
        self._m, self._row = sheet_model, row
        self._at = len(sheet_model.sheet.rows)

    def redo(self) -> None:
        self._m.insert_row(Row(self._row.id, dict(self._row.cells), self._row.source, self._row.made), self._at)

    def undo(self) -> None:
        self._m.remove_row(self._row.id)


class _AddColumn(QUndoCommand):
    def __init__(self, sheet_model: SheetModel, column: model.Column) -> None:
        super().__init__("Add a column")
        self._m, self._column = sheet_model, column
        self._at = len(sheet_model.sheet.columns)
        self._cells: dict[str, Any] = {}

    def redo(self) -> None:
        self._m.insert_column(self._column, self._at)
        for row in self._m.sheet.rows:
            if row.id in self._cells:
                row.cells[self._column.id] = self._cells[row.id]

    def undo(self) -> None:
        self._cells = {row.id: row.cells[self._column.id] for row in self._m.sheet.rows if self._column.id in row.cells}
        self._m.remove_column(self._column.id)


class SheetDelegate(QStyledItemDelegate):
    """Typed editors: a list for a choice column (its choices and an empty one), a field for the rest (a date's
    placeholder says how to write it). A checkbox needs no editor: its tick is the cell."""

    def createEditor(self, parent: QWidget, _option: QStyleOptionViewItem,  # noqa: N802 - Qt override
                     index: QModelIndex | QPersistentModelIndex) -> QWidget:
        sheet_model = index.model()
        column = sheet_model.column(index.column()) if isinstance(sheet_model, SheetModel) else None
        if column is not None and column.type == model.TYPE_CHOICE:
            box = QComboBox(parent)
            box.addItem("")
            box.addItems(list(column.choices))
            box.setFont(holo.font("body_sm"))
            self.open_editor = box
            return box
        edit = QLineEdit(parent)
        edit.setFont(holo.font("body_sm"))
        edit.setMaxLength(model.MAX_CELL_CHARS)
        if column is not None and column.type == model.TYPE_DATE:
            edit.setPlaceholderText("2026-11-22")
        elif column is not None and column.type == model.TYPE_LINK:
            edit.setPlaceholderText("https://")
        self.open_editor = edit
        return edit

    open_editor: QWidget | None = None

    def commit_open(self, view: QAbstractItemView) -> bool:
        """The cell being typed in goes into the model now (as Enter would) and its editor closes."""
        import shiboken6
        from PySide6.QtWidgets import QAbstractItemDelegate

        editor, self.open_editor = self.open_editor, None
        if editor is None or not shiboken6.isValid(editor) or view.state() != QAbstractItemView.State.EditingState:
            return False
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
        return True

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:  # noqa: N802
        text = index.data(Qt.ItemDataRole.EditRole) or ""
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findText(str(text))))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(text))

    def setModelData(self, editor: QWidget, sheet_model: Any,  # noqa: N802 - Qt override
                     index: QModelIndex | QPersistentModelIndex) -> None:
        if isinstance(editor, QComboBox):
            sheet_model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
        elif isinstance(editor, QLineEdit):
            sheet_model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)


class ColumnForm(QWidget):
    """Add column: a name, a type and (a choice column) its choices; Add / Cancel."""

    submitted = Signal(str, str, str)     # name, type, choices
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(COLUMN_NAME_PLACEHOLDER)
        self.name_edit.setMaxLength(model.MAX_COLUMN_NAME)
        self.type_box = QComboBox()
        for type_ in model.TYPES:
            self.type_box.addItem(model.TYPE_NAMES[type_], type_)
        self.choices_edit = QLineEdit()
        self.choices_edit.setPlaceholderText(CHOICES_PLACEHOLDER)
        self.choices_edit.setMaxLength(model.MAX_CHOICES * (model.MAX_CHOICE_CHARS + 2))
        for widget in (self.name_edit, self.type_box, self.choices_edit):
            widget.setFont(holo.font("body_sm"))
            _field_style(widget)
            _ink(widget)
        self.add_button = _button(ADD_TEXT, holo.GO)
        self.cancel_button = _button(CANCEL_TEXT)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.name_edit, 2)
        row.addWidget(self.type_box, 1)
        row.addWidget(self.choices_edit, 3)
        row.addWidget(self.add_button)
        row.addWidget(self.cancel_button)
        self.type_box.currentIndexChanged.connect(self._sync)
        self.add_button.clicked.connect(self._submit)
        self.name_edit.returnPressed.connect(self._submit)
        self.choices_edit.returnPressed.connect(self._submit)
        self.cancel_button.clicked.connect(self.cancelled)
        self._sync()

    def _sync(self) -> None:
        self.choices_edit.setVisible(self.type_box.currentData() == model.TYPE_CHOICE)

    def _submit(self) -> None:
        self.submitted.emit(self.name_edit.text(), str(self.type_box.currentData()), self.choices_edit.text())

    def reset(self) -> None:
        self.name_edit.clear()
        self.choices_edit.clear()
        self.type_box.setCurrentIndex(0)


@dataclass(frozen=True)
class PendingRowInfo:
    """A proposed row or cell change as the band lists it."""

    pending_id: str
    text: str            # "Acme · Intern · 2026-12-01" or "Row 3 · Status: Applied (was Open)"
    source: str = ""     # "FROM ASK" / "FROM INTERNSHIP WATCH"
    changed: bool = False   # its cell changed since Jarvis read it: its own Keep only


class PendingBand(QWidget):
    """The amber band over the table: what waits for Keep (part 2 and 4), Keep all / Discard all and each row's own
    Keep / Discard."""

    keepAll = Signal()
    discardAll = Signal()
    keepOne = Signal(str)
    discardOne = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_label = body_label("", "label", holo.AMBER, wrap=False)
        self.keep_all_button = _button(KEEP_ALL_TEXT, holo.AMBER_COMMIT)
        self.discard_all_button = _button(DISCARD_ALL_TEXT, holo.AMBER_GHOST)
        self.keep_all_button.clicked.connect(self.keepAll)
        self.discard_all_button.clicked.connect(self.discardAll)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.discard_all_button)
        top.addWidget(self.keep_all_button)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self.more_label = body_label("", "body_sm", holo.INK_3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addLayout(self._rows_layout)
        layout.addWidget(self.more_label)
        self.rows: list[tuple[PendingRowInfo, QWidget]] = []
        self.hide()

    def set_rows(self, rows: Sequence[PendingRowInfo], total: int) -> None:
        for _info, widget in self.rows:
            widget.setParent(None)
            widget.deleteLater()
        self.rows = []
        for info in rows[:PENDING_ROWS_SHOWN]:
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            text = body_label(info.text, "body_sm", holo.INK_1, wrap=False)
            row.addWidget(text, 1)
            if info.changed:
                row.addWidget(body_label(CHANGED_WORDS, "readout", holo.AMBER, wrap=False))
            if info.source:
                row.addWidget(body_label(info.source.upper(), "readout", holo.INK_3, wrap=False))
            discard = _button("Discard", holo.LINK)
            keep = _button("Keep", holo.LINK_CYAN)
            discard.clicked.connect(lambda _c=False, p=info.pending_id: self.discardOne.emit(p))
            keep.clicked.connect(lambda _c=False, p=info.pending_id: self.keepOne.emit(p))
            row.addWidget(discard)
            row.addWidget(keep)
            self._rows_layout.addWidget(line)
            self.rows.append((info, line))
        self.title_label.setText(holo.role_text("label", PENDING_ONE_TEXT if total == 1 else
                                                PENDING_ROW_TEXT.format(count=total)))
        more = total - len(self.rows)
        self.more_label.setText(f"+{more} more" if more > 0 else "")
        self.more_label.setVisible(more > 0)
        self.setVisible(total > 0)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            holo.paint_tile(painter, QRectF(self.rect()), holo.TILE_NEEDS)
        finally:
            painter.end()


class SheetTable(QTableView):
    """The sheet's grid in the screen's look."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setDefaultSectionSize(160)
        self.horizontalHeader().setMinimumSectionSize(80)
        self.verticalHeader().setDefaultSectionSize(32)
        self.setFont(holo.font("body_sm"))
        self.horizontalHeader().setFont(holo.font("badge"))
        self.verticalHeader().setFont(holo.font("badge"))
        self.restyle()

    def restyle(self) -> None:
        canvas = holo.canvas_look()
        line = "rgba(143, 235, 255, 60)" if canvas else "rgba(60, 211, 238, 40)"
        head = "#000000" if canvas else holo.TILE
        self.setStyleSheet(
            f"QTableView {{ background: transparent; color: {holo.INK_1}; gridline-color: {line};"
            f" border: 1px solid {line}; selection-background-color: rgba(53, 211, 240, 60);"
            f" selection-color: #FFFFFF; }}"
            f" QHeaderView::section {{ background: {head}; color: {holo.INK_KICKER}; border: none;"
            f" border-right: 1px solid {line}; border-bottom: 1px solid {line}; padding: 4px 8px; }}"
            f" QTableCornerButton::section {{ background: {head}; border: none; }}"
            f" QLineEdit, QComboBox {{ background: #03090C; color: {holo.INK_1}; border: 1px solid #FFFFFF; }}")


class SheetEditor(PanelFrame):
    """One datasheet; see the module docstring."""

    sheetEdited = Signal(str)
    renameRequested = Signal(str, str)
    deleteRequested = Signal(str)
    exportRequested = Signal(str)
    versionsRequested = Signal(str)
    versionRestoreRequested = Signal(str, str)
    openLinkRequested = Signal(str)
    noticeFirst = Signal()
    noticeSecond = Signal()
    askRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id = ""
        self.add_row_button = _button(ADD_ROW_TEXT)
        self.add_column_button = _button(ADD_COLUMN_TEXT)
        self.open_link_button = _button(OPEN_LINK_TEXT, tip=OPEN_LINK_TIP)
        self.versions_button = _button(VERSIONS_TEXT)
        self.export_button = _button(EXPORT_CSV_TEXT, tip="Save the kept rows as a .csv file (what waits for Keep "
                                                           "is not in it)")
        self.delete_button = _button(DELETE_TEXT, holo.DANGER_GHOST, tip="Move it to Deleted (kept 30 days)")
        self.head = _Head((self.add_row_button, self.add_column_button, self.open_link_button, self.versions_button,
                           self.export_button, self.delete_button))
        self.name_edit = self.head.name_edit
        self.banner = body_label("", "body_sm", holo.ALERT_TX)
        self.banner.hide()
        self.notice = NoticeLine()
        self.notice.first.clicked.connect(self.noticeFirst)
        self.notice.second.clicked.connect(self.noticeSecond)
        self.column_form = ColumnForm()
        self.column_form.hide()
        self.versions_box = VersionsBox()
        self.versions_box.restoreRequested.connect(lambda name: self.versionRestoreRequested.emit(self.item_id, name))
        self.band = PendingBand()
        self.model = SheetModel(self)
        self.table = SheetTable()
        self.table.setModel(self.model)
        self.table.setItemDelegate(SheetDelegate(self.table))
        self.reason_label = body_label("", "body_sm", holo.ALERT_TX)
        self.reason_label.hide()
        self.empty_label = body_label("No columns yet: Add column names one and picks its type.", "body_sm",
                                      holo.INK_3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_PAD)
        layout.setSpacing(8)
        layout.addWidget(self.head)
        layout.addWidget(self.banner)
        layout.addWidget(self.notice)
        layout.addWidget(self.column_form)
        layout.addWidget(self.versions_box)
        layout.addWidget(self.band)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.reason_label)
        self.ask_line = AskLine()
        self.ask_line.asked.connect(lambda text: self.askRequested.emit(self.item_id, text))
        self.ask_line.hide()
        layout.addWidget(self.ask_line)
        self.add_row_button.clicked.connect(self._add_row)
        self.add_column_button.clicked.connect(self._open_form)
        self.column_form.submitted.connect(self._add_column)
        self.column_form.cancelled.connect(self.column_form.hide)
        self.open_link_button.clicked.connect(self._open_link)
        self.versions_button.clicked.connect(lambda: self.versionsRequested.emit(self.item_id))
        self.export_button.clicked.connect(lambda: self.exportRequested.emit(self.item_id))
        self.delete_button.clicked.connect(lambda: self.deleteRequested.emit(self.item_id))
        self.name_edit.editingFinished.connect(self._rename)
        self.model.refused.connect(self._refused)
        self.model.changed.connect(self._changed)
        self.table.selectionModel().currentChanged.connect(lambda *_: self._sync_link())
        self._undo_keys = [QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self),
                           QShortcut(QKeySequence(QKeySequence.StandardKey.Redo), self)]
        for shortcut in self._undo_keys:
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_keys[0].activated.connect(self.model.undo.undo)
        self._undo_keys[1].activated.connect(self.model.undo.redo)
        self._sync_link()

    def show_sheet(self, sheet: SheetData, *, meta: str = "", read_only_text: str = "") -> None:
        same = sheet.id == self.item_id
        self.item_id = sheet.id
        if self.name_edit.text() != sheet.name and not self.name_edit.hasFocus():
            self.name_edit.setText(sheet.name)
        self.head.meta_label.setText(meta.upper())
        current = self.table.currentIndex()
        self.model.set_sheet(sheet)
        if same and current.isValid():
            self.table.setCurrentIndex(self.model.index(current.row(), current.column()))
        self.banner.setText(read_only_text)
        self.banner.setVisible(bool(read_only_text))
        for widget in (self.add_row_button, self.add_column_button, self.delete_button, self.versions_button):
            widget.setEnabled(not sheet.read_only)
        self.name_edit.setReadOnly(sheet.read_only)
        if not same:
            self.column_form.hide()
            self.versions_box.hide()
            self.notice.show_text("")
            self.reason_label.hide()
        self._sync_empty()
        self._sync_link()

    def sheet(self) -> SheetData:
        return self.model.sheet

    def commit_editing(self) -> bool:
        """A cell being typed in is put in the sheet now (before it is saved or read again)."""
        delegate = self.table.itemDelegate()
        return isinstance(delegate, SheetDelegate) and delegate.commit_open(self.table)

    def _sync_empty(self) -> None:
        self.empty_label.setVisible(not self.model.sheet.columns)

    def _changed(self) -> None:
        self.reason_label.hide()
        self._sync_empty()
        self._sync_link()
        if self.item_id:
            self.sheetEdited.emit(self.item_id)

    def _refused(self, reason: str) -> None:
        self.reason_label.setText(model.REASON_WORDS.get(reason, "That doesn't fit this column"))
        self.reason_label.show()

    def reason_text(self) -> str:
        return self.reason_label.text() if not self.reason_label.isHidden() else ""

    def _add_row(self) -> None:
        if not self.model.sheet.columns:
            self._open_form()
            return
        if self.model.add_row():
            index = self.model.index(len(self.model.sheet.rows) - 1, 0)
            self.table.setCurrentIndex(index)
            self.table.scrollTo(index)
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def _open_form(self) -> None:
        self.column_form.reset()
        self.column_form.show()
        self.column_form.name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _add_column(self, name: str, type_: str, choices: str) -> None:
        taken = tuple(column.name for column in self.model.sheet.columns)
        column, reason = model.check_column(name, type_, choices, taken=taken)
        if column is None:
            self._refused(reason)
            return
        if self.model.add_column(column):
            self.column_form.hide()
            self.reason_label.hide()

    def current_link(self) -> str:
        index = self.table.currentIndex()
        if not index.isValid():
            return ""
        column = self.model.column(index.column())
        if column.type != model.TYPE_LINK:
            return ""
        value = self.model.sheet.rows[index.row()].cells.get(column.id)
        return value if isinstance(value, str) else ""

    def _sync_link(self) -> None:
        self.open_link_button.setEnabled(bool(self.current_link()))

    def _open_link(self) -> None:
        link = self.current_link()
        if link:
            self.openLinkRequested.emit(link)

    def _rename(self) -> None:
        if self.item_id and not self.model.read_only:
            self.renameRequested.emit(self.item_id, self.name_edit.text())

    def set_versions(self, rows: Sequence[tuple[VersionInfo, str]], *, shown: bool = True) -> None:
        self.versions_box.set_versions(rows)
        self.versions_box.setVisible(shown)

    def versions_shown(self) -> bool:
        return not self.versions_box.isHidden()


# ---- the screen ----------------------------------------------------------------------------------------------------

class DocsStage(QWidget):
    """The Documents screen; see the module docstring."""

    newDocRequested = Signal()
    newSheetRequested = Signal()
    itemPicked = Signal(str)
    deletedRestoreRequested = Signal(str)
    undoRequested = Signal()
    hidden = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list_panel = PanelFrame()
        self.list_panel.setFixedWidth(LIST_WIDTH)
        self.head = SectionHead("documents", DOCS_TITLE)
        self.new_doc_button = _button(NEW_DOC_TEXT, holo.GO)
        self.new_sheet_button = _button(NEW_SHEET_TEXT)
        self.new_doc_button.clicked.connect(self.newDocRequested)
        self.new_sheet_button.clicked.connect(self.newSheetRequested)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addWidget(self.new_doc_button)
        buttons.addWidget(self.new_sheet_button)
        buttons.addStretch(1)
        self.notice = NoticeLine()
        self.notice.first.clicked.connect(self.undoRequested)
        self.banner = body_label("", "body_sm", holo.ALERT_TX)
        self.banner.hide()
        self._rows_box = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_box)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        self.empty_label = body_label(LIST_EMPTY_TEXT, "body_sm", holo.INK_3)
        self.deleted_head = body_label(holo.role_text("label", DELETED_GROUP), "label", holo.INK_KICKER, wrap=False)
        self._deleted_box = QWidget()
        self._deleted_layout = QVBoxLayout(self._deleted_box)
        self._deleted_layout.setContentsMargins(0, 0, 0, 0)
        self._deleted_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll_content = hud._transparent_scroll(scroll, QWidget())
        inner = QVBoxLayout(scroll_content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        inner.addWidget(self.empty_label)
        inner.addWidget(self._rows_box)
        inner.addSpacing(10)
        inner.addWidget(self.deleted_head)
        inner.addWidget(self._deleted_box)
        inner.addStretch(1)
        self.list_scroll = scroll
        list_layout = QVBoxLayout(self.list_panel)
        list_layout.setContentsMargins(*PANEL_PAD)
        list_layout.setSpacing(10)
        list_layout.addWidget(self.head)
        list_layout.addLayout(buttons)
        list_layout.addWidget(self.banner)
        list_layout.addWidget(self.notice)
        list_layout.addWidget(Rule())
        list_layout.addWidget(scroll, 1)
        self.doc_editor = DocEditor()
        self.sheet_editor = SheetEditor()
        self.empty_panel = PanelFrame()
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(*PANEL_PAD)
        self.empty_text = body_label(EMPTY_TEXT, "body", holo.INK_3)
        empty_layout.addWidget(self.empty_text, 0, Qt.AlignmentFlag.AlignTop)
        self.editors = QStackedWidget()
        for widget in (self.empty_panel, self.doc_editor, self.sheet_editor):
            self.editors.addWidget(widget)
        row = QHBoxLayout(self)
        row.setContentsMargins(holo.GUTTER_SIDE, holo.GUTTER_TOP, holo.GUTTER_SIDE, holo.GUTTER_BOTTOM)
        row.setSpacing(16)
        row.addWidget(self.list_panel, 0)
        row.addWidget(self.editors, 1)
        self._row = row
        self.rows: list[ItemRow] = []
        self.deleted_rows: list[DeletedRow] = []
        self._selected = ""
        self.set_items((), ())

    # ---- the list -------------------------------------------------------------------------------------------

    def set_items(self, rows: Sequence[ItemRowInfo], deleted: Sequence[ItemRowInfo]) -> None:
        by_id = {row.info.item_id: row for row in self.rows}
        kept: list[ItemRow] = []
        for info in rows:
            row = by_id.pop(info.item_id, None)
            if row is None:
                row = ItemRow(info)
                row.clicked.connect(lambda _c=False, r=row: self.itemPicked.emit(r.info.item_id))
            else:
                row.set_info(info)
            kept.append(row)
        for row in by_id.values():
            row.setParent(None)
            row.deleteLater()
        for index, row in enumerate(kept):
            self._rows_layout.insertWidget(index, row)
            row.set_selected(row.info.item_id == self._selected)
        self.rows = kept
        for row in self.deleted_rows:
            row.setParent(None)
            row.deleteLater()
        self.deleted_rows = []
        for info in deleted:
            line = DeletedRow(info)
            line.restore_button.clicked.connect(lambda _c=False, i=info.item_id: self.deletedRestoreRequested.emit(i))
            self._deleted_layout.addWidget(line)
            self.deleted_rows.append(line)
        self.deleted_head.setVisible(bool(self.deleted_rows))
        self.empty_label.setVisible(not self.rows)

    def item_row(self, item_id: str) -> ItemRow | None:
        return next((row for row in self.rows if row.info.item_id == item_id), None)

    def deleted_row(self, item_id: str) -> DeletedRow | None:
        return next((row for row in self.deleted_rows if row.info.item_id == item_id), None)

    def select(self, item_id: str) -> None:
        self._selected = item_id
        for row in self.rows:
            row.set_selected(row.info.item_id == item_id)

    def selected(self) -> str:
        return self._selected

    def set_notice(self, text: str, *, undo: bool = False) -> None:
        self.notice.show_text(text, UNDO_TEXT if undo else "")

    def set_banner(self, text: str) -> None:
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        for button in (self.new_doc_button, self.new_sheet_button):
            button.setEnabled(not text)

    # ---- the editor -------------------------------------------------------------------------------------------

    def show_doc(self, view: DocView, *, keep_mode: bool = False, focus: bool = True) -> None:
        self.doc_editor.show_doc(view, keep_mode=keep_mode, focus=focus)
        self.editors.setCurrentWidget(self.doc_editor)
        self.select(view.item_id)

    def show_sheet(self, sheet: SheetData, *, meta: str = "", read_only_text: str = "") -> None:
        self.sheet_editor.show_sheet(sheet, meta=meta, read_only_text=read_only_text)
        self.editors.setCurrentWidget(self.sheet_editor)
        self.select(sheet.id)

    def show_empty(self) -> None:
        self.editors.setCurrentWidget(self.empty_panel)
        self.select("")

    def current_editor(self) -> QWidget:
        return self.editors.currentWidget()

    def holds_editors(self) -> bool:
        return self.editors.parentWidget() is self

    def take_back_editors(self) -> None:
        """The editors come back from the canvas's pane (docs_ui moves them there while it shows)."""
        if self.editors.parentWidget() is not self:
            self._row.addWidget(self.editors, 1)
            self.editors.show()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self.hidden.emit()

    def changeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self.restyle()

    def restyle(self) -> None:
        """The canvas look came or went: the fields and the grid take it (the panels and buttons paint it)."""
        for widget in (self.doc_editor.source_edit, self.sheet_editor.column_form.name_edit,
                       self.sheet_editor.column_form.type_box, self.sheet_editor.column_form.choices_edit):
            _field_style(widget)
        self.sheet_editor.table.restyle()


# ---- the canvas's document pane (part 3) ---------------------------------------------------------------------

PANE_SCREEN_TEXT = "Open in Documents"
PANE_CLOSE_TEXT = "Close the document"


class DocsPane(QWidget):
    """The document beside the canvas: the Documents screen's own editors, lent while it shows (docs_ui moves them),
    with "Open in Documents" and Close above them. It never takes the keyboard by itself."""

    closeRequested = Signal()
    screenRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("docsPane")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.screen_button = _button(PANE_SCREEN_TEXT, holo.LINK_CYAN)
        self.close_button = holo.HoloButton(PANE_CLOSE_TEXT, holo.ICON, icon="close", diameter=32)
        self.close_button.setToolTip(PANE_CLOSE_TEXT)
        self.screen_button.clicked.connect(self.screenRequested)
        self.close_button.clicked.connect(self.closeRequested)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(body_label(holo.role_text("label", DOCS_TITLE), "label", holo.INK_KICKER, wrap=False), 0)
        top.addStretch(1)
        top.addWidget(self.screen_button)
        top.addWidget(self.close_button)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addLayout(top)
        self.editors: QWidget | None = None

    def take_editors(self, editors: QWidget) -> None:
        if editors.parentWidget() is not self:
            self._layout.addWidget(editors, 1)
            editors.show()
        self.editors = editors

    def holds_editors(self) -> bool:
        return self.editors is not None and self.editors.parentWidget() is self

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(holo.CANVAS_BLACK))   # opaque: the frames under it never show through
        finally:
            painter.end()


__all__ = ["DocsPane", "DocsStage", "DocEditor", "SheetEditor", "SheetModel", "ItemRowInfo", "DocView", "PendingRowInfo",
           "item_meta"]
