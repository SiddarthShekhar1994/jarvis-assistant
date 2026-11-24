"""The documents and datasheets in the app (build plan item 5; Qt): the Documents screen's controller.

    DocsController(reading, store, *, export_folder="", picker=None, open_url=None, clock=None, parent=None)
        made by the app while [docs] is on: the Dock's Documents (Ctrl+0) and its screen show. What the owner types is
        his and is written directly: a document 800 ms after he stops typing, a sheet's change at once after the same
        pause, and everything when the screen goes (``flush``). ``open(item_id)`` opens one; ``new_doc`` /
        ``new_sheet``; ``delete`` moves to Deleted with Undo (30 days); Versions lists what was kept, Restore brings one
        back (what was there becomes a version first); Export .md / .csv is the owner's click: ``picker(kind,
        suggested_path) -> path`` (QFileDialog in the app, a fake in the tests - never a real dialog under the test
        guard); a folder OneDrive syncs is asked about first ("Save here" / "Choose another"); the .csv carries the Mark
        of the Web. A link cell opens only through Open link, checked with actions.web_link_allowed.

Logs hold item ids, kinds, counts and reason words only: never a name, a heading, a cell or a path.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, QUrl

from . import docs_view
from .docs import model, proposals
from .docs import store as store_mod
from .docs.store import DocsStore, Row

logger = logging.getLogger(__name__)

SAVE_DELAY_MS = 800
READ_ONLY_TEXT = ("Jarvis can't safely read the documents file in the data folder, so nothing here can change "
                  "this run (the file is left as it is)")
ITEM_READ_ONLY_TEXT = "Jarvis can't safely read this one's file, so it is shown read-only (the file is left as it is)"
DELETED_TEXT = "Deleted. It stays in Deleted for 30 days."
RESTORED_TEXT = "Restored the version from {when}."
EXPORTED_TEXT = "Saved {name}."
SYNCED_TEXT = "This folder is synced, so the file will leave this PC. Save here?"
LINK_REFUSED_TEXT = "Jarvis opens only https links that pass his link check; this one doesn't"
KEPT_TEXT = "Kept."
HELD_TEXT = "Kept. A change marked \u201cChanged since Jarvis read it\u201d still waits for its own Keep."
HELD_ONLY_TEXT = "A change marked \u201cChanged since Jarvis read it\u201d needs its own Keep; Keep all leaves it."
NOTHING_TO_EXPORT_TEXT = "Jarvis can't read this one's file safely, so there is nothing to export (the file is left as it is)"
DISCARDED_TEXT = "Discarded."
GONE_CELL_TEXT = "Kept; a change whose row has gone was let go."
LET_GO_TEXT = "{count} older changes were let go: an item keeps at most 30 sections or 200 rows waiting."
AMBIGUOUS_TEXT = "Which one do you mean: {names}? Ask again with its whole name."
NEW_DOC_NAME = "Untitled document"
NEW_SHEET_NAME = "Untitled sheet"
_UNSAFE_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _when(moment: datetime) -> str:
    """"NOV 22 · 10:41 AM"."""
    hour = moment.hour % 12 or 12
    return f"{moment:%b} {moment.day} · {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}".upper()


def _day(text: str) -> str:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return ""
    return f"{moment:%b} {moment.day}".upper()


def _size(count: int) -> str:
    return f"{max(count, 1) / 1024:.1f} KB" if count < 1024 * 1024 else f"{count / (1024 * 1024):.1f} MB"


def file_name(name: str, suffix: str, fallback: str) -> str:
    """A file name from an item's name: characters Windows refuses become "_", at most 60 characters."""
    base = _UNSAFE_FILE_CHARS.sub("_", model.clean_name(name)).strip(" .")[:60].strip(" .") or fallback
    return base + suffix


def real_picker(window: Any) -> Callable[[str, Path], str]:
    """The app's Export picker: QFileDialog's Save dialog starting at the suggested path. Never opened under the test
    guard (the tests pass their own picker)."""

    def pick(kind: str, suggested: Path) -> str:
        from .desktop.backend import real_backend_blocked

        if real_backend_blocked():
            logger.info("Docs: no file dialog under the test guard")
            return ""
        from PySide6.QtWidgets import QFileDialog

        try:
            suggested.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        wanted = "Markdown (*.md)" if kind == model.KIND_DOC else "CSV (*.csv)"
        path, _filter = QFileDialog.getSaveFileName(window, "Export", str(suggested), wanted)
        return path

    return pick


def real_open_url(url: str) -> None:
    from .desktop.backend import real_backend_blocked

    if real_backend_blocked():
        logger.info("Docs: no browser under the test guard")
        return
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url, QUrl.ParsingMode.StrictMode))


class DocsController(QObject):
    """See the module docstring."""

    def __init__(self, reading: Any, store: DocsStore, *, export_folder: str = "",
                 picker: Callable[[str, Path], str] | None = None, open_url: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] | None = None, submit_mode: Callable[[Any, str], bool] | None = None,
                 ask_on: Callable[[], bool] | None = None, conversation: Callable[[], Any] | None = None,
                 tables: Callable[[], Any] | None = None, status: Callable[[str], None] | None = None,
                 board: Callable[[str, Callable[[str], Any], bool], Any] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.reading = reading
        self.stage: docs_view.DocsStage = reading.docs_stage
        self.store = store
        self._export_folder = export_folder
        self._picker = picker or real_picker(reading.window())
        self._open_url = open_url or real_open_url
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._current = ""
        self._doc_text: tuple[str, str] | None = None     # (id, text) typed and not saved yet
        self._sheet_dirty = ""
        self._last_deleted = ""
        self._export: tuple[str, str, bytes] | None = None   # (id, path, data) waiting for "Save here"
        self._submit_mode = submit_mode
        self._ask_on = ask_on or (lambda: submit_mode is not None)
        self._conversation = conversation or (lambda: ())
        self._tables = tables or (lambda: ())
        self._status = status
        self._running = ""                                    # the item a document run is for ("" none)
        self._new_items: set[str] = set()                     # made for a hand-off: its run may name it, add columns
        self._undo: tuple[str, str, Any, list[dict[str, Any]]] | None = None   # the last Keep or Discard
        self._let_go: dict[str, int] = {}                     # older changes let go past the caps, by item
        self._board = board                                   # part 3: (key, build, refresh) -> the frame on the board
        self.pane = docs_view.DocsPane()                      # part 3: the canvas's document pane
        self.pane.hide()
        self.pane.closeRequested.connect(self.close_pane)
        self.pane.screenRequested.connect(self.to_screen)
        if hasattr(reading, "tabChanged"):
            reading.tabChanged.connect(self._on_tab)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)
        stage = self.stage
        stage.newDocRequested.connect(self.new_doc)
        stage.newSheetRequested.connect(self.new_sheet)
        stage.itemPicked.connect(self.open)
        stage.deletedRestoreRequested.connect(self.restore_deleted)
        stage.undoRequested.connect(self.undo_delete)
        stage.hidden.connect(self.flush)
        for editor in (stage.doc_editor, stage.sheet_editor):
            editor.renameRequested.connect(self.rename)
            editor.deleteRequested.connect(self.delete)
            editor.exportRequested.connect(self.export)
            editor.versionsRequested.connect(self.toggle_versions)
            editor.versionRestoreRequested.connect(self.restore_version)
            editor.noticeFirst.connect(self._notice_first)
            editor.noticeSecond.connect(self._notice_second)
        for editor in (stage.doc_editor, stage.sheet_editor):
            editor.askRequested.connect(self.ask_change)
        doc_box = stage.doc_editor.proposals_box
        doc_box.keepRequested.connect(self.keep)
        doc_box.discardRequested.connect(self.discard)
        doc_box.atEndRequested.connect(lambda pending_id: self.keep(pending_id, at_end=True))
        doc_box.keepAllRequested.connect(lambda: self.keep_all(stage.doc_editor.item_id))
        band = stage.sheet_editor.band
        band.keepOne.connect(self.keep)
        band.discardOne.connect(self.discard)
        band.keepAll.connect(lambda: self.keep_all(stage.sheet_editor.item_id))
        band.discardAll.connect(lambda: self.discard_all(stage.sheet_editor.item_id))
        stage.doc_editor.textEdited.connect(self._typed)
        stage.sheet_editor.sheetEdited.connect(self._sheet_edited)
        stage.sheet_editor.openLinkRequested.connect(self.open_link)
        reading.set_docs_available(True)
        stage.set_banner(READ_ONLY_TEXT if store.read_only else "")
        self.refresh_list()

    # ---- the list --------------------------------------------------------------------------------------------

    def refresh_list(self) -> None:
        rows = [docs_view.ItemRowInfo(info.id, info.kind, info.name, docs_view.item_meta(info.kind, info.words,
                                                                                         info.rows), info.pending)
                for info in self.store.items()]
        deleted = [docs_view.ItemRowInfo(info.id, info.kind, info.name, "", 0, f"DELETED {_day(info.trashed)}")
                   for info in self.store.deleted()]
        self.stage.set_items(rows, deleted)
        self.stage.select(self._current)
        self.reading.dock.set_docs_waiting(sum(1 for info in self.store.items() if info.pending))

    def current(self) -> str:
        return self._current

    # ---- open, new ---------------------------------------------------------------------------------------------

    def open(self, item_id: str, *, wide: bool = False, screen: bool = False) -> bool:
        """The item: on the canvas (Home is the canvas and shows) in the pane beside the frames - ``wide`` over the
        frames' whole area - without taking the keyboard from the ask line; else (and with ``screen``) on the Documents
        screen. False when it is gone."""
        from .ui import SCREEN_DOCS, SCREEN_HOME

        info = self.store.info(item_id)
        if info is None or info.trashed:
            return False
        page = self._canvas_page()
        if page is not None and not screen and self.reading.current_tab() == SCREEN_HOME:
            self._to_pane(page, wide)
            self.show_item(item_id, focus=False)
            logger.info("Docs: %s opened in the pane (%s)", item_id, "wide" if wide else "beside the frames")
            return True
        self._to_stage()
        if self.reading.current_tab() != SCREEN_DOCS:
            self.reading.set_tab(SCREEN_DOCS)
        self.show_item(item_id)
        return True

    def _canvas_page(self) -> Any:
        try:
            if not self.reading.canvas_on():
                return None
            return self.reading.canvas_page()
        except Exception:  # noqa: BLE001 - no canvas: the screen
            return None

    def _to_pane(self, page: Any, wide: bool) -> None:
        if not self.pane.holds_editors():
            self.pane.take_editors(self.stage.editors)
        if page.doc_pane() is not self.pane:
            page.set_doc_pane(self.pane)
        page.show_doc_pane(wide)

    def _to_stage(self) -> None:
        """The editors go back to the Documents screen (the pane closes)."""
        page = self._canvas_page()
        if page is not None and page.doc_pane() is self.pane:
            page.hide_doc_pane()
        elif self.pane.parentWidget() is not None:
            self.pane.hide()
        if not self.stage.holds_editors():
            self.stage.take_back_editors()

    def pane_shown(self) -> bool:
        page = self._canvas_page()
        return page is not None and page.doc_pane() is self.pane and page.doc_pane_shown()

    def close_pane(self) -> None:
        """The pane's Close: what he typed is saved; the editors go back to the Documents screen."""
        self.flush()
        self._to_stage()

    def to_screen(self) -> None:
        """"Open in Documents": the item on the Documents screen."""
        item_id = self._current
        self.flush()
        self._to_stage()
        if item_id:
            self.open(item_id, screen=True)

    def _on_tab(self, index: int) -> None:
        from .ui import SCREEN_DOCS, SCREEN_HOME

        if index != SCREEN_HOME and self.pane.holds_editors():
            self.flush()
            self._to_stage()
        if index == SCREEN_DOCS and self._current:
            self.show_item(self._current, keep_mode=True)

    # ---- the board's frame (part 3) --------------------------------------------------------------------------

    def frame_key(self, item_id: str) -> str:
        kind = model.kind_of(item_id)
        return f"{kind}:{item_id}" if kind else ""

    def build_frame(self, item_id: str) -> Callable[[str], Any]:
        """The item's frame on the board (board.frames.from_doc / from_sheet), for a frame id."""
        from .board import frames as board_frames

        def build(frame_id: str) -> Any:
            info = self.store.info(item_id)
            if info is None:
                return board_frames.Built(())
            if info.kind == model.KIND_DOC:
                doc = self.store.read_doc(item_id)
                return board_frames.from_doc(info.name, doc.text if doc else "", doc.pending if doc else (),
                                             frame_id=frame_id)
            return board_frames.from_sheet(info.name, self.store.read_sheet(item_id), frame_id=frame_id)

        return build

    def _show_frame(self, item_id: str, *, refresh: bool = False) -> None:
        """The item's frame goes up (an answer about it), or is drawn again in its place (``refresh``: a Keep, a
        Discard, an agent's rows) when it is on the board."""
        if self._board is None or not self.frame_key(item_id):
            return
        try:
            self._board(self.frame_key(item_id), self.build_frame(item_id), refresh)
        except Exception as exc:  # noqa: BLE001 - the board is only a view of it
            logger.info("Docs: %s frame not drawn (%s)", item_id, type(exc).__name__)

    def show_item(self, item_id: str, *, keep_mode: bool = False, focus: bool = True) -> None:
        if item_id != self._current:
            self.flush()
        info = self.store.info(item_id)
        if info is None:
            self._current = ""
            self.stage.show_empty()
            return
        self._current = item_id
        meta = docs_view.item_meta(info.kind, info.words, info.rows)
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            if doc is None:
                self.stage.show_empty()
                return
            self.stage.show_doc(docs_view.DocView(item_id, doc.name, doc.text, meta, doc.read_only,
                                                  self._read_only_text(doc.read_only)), keep_mode=keep_mode,
                                focus=focus)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None:
                self.stage.show_empty()
                return
            self.stage.show_sheet(sheet, meta=meta, read_only_text=self._read_only_text(sheet.read_only))
        self._show_pending(item_id)
        self._sync_ask_lines()
        self.stage.restyle()
        logger.info("Docs: %s opened (%s)", item_id, info.kind)

    def _read_only_text(self, read_only: bool) -> str:
        if not read_only:
            return ""
        return READ_ONLY_TEXT if self.store.read_only else ITEM_READ_ONLY_TEXT

    def new_doc(self) -> str:
        self.flush()
        item_id, reason = self.store.create_doc(NEW_DOC_NAME)
        if not item_id:
            self.stage.set_notice(store_mod.REFUSAL_WORDS.get(reason, store_mod.REFUSAL_WORDS[store_mod.R_NOT_SAVED]))
            return ""
        self.refresh_list()
        self.open(item_id)
        name = self.stage.doc_editor.name_edit
        name.setFocus(Qt.FocusReason.OtherFocusReason)
        name.selectAll()
        return item_id

    def new_sheet(self) -> str:
        self.flush()
        item_id, reason = self.store.create_sheet(NEW_SHEET_NAME)
        if not item_id:
            self.stage.set_notice(store_mod.REFUSAL_WORDS.get(reason, store_mod.REFUSAL_WORDS[store_mod.R_NOT_SAVED]))
            return ""
        self.refresh_list()
        self.open(item_id)
        name = self.stage.sheet_editor.name_edit
        name.setFocus(Qt.FocusReason.OtherFocusReason)
        name.selectAll()
        return item_id

    # ---- what he types ---------------------------------------------------------------------------------------

    def _typed(self, item_id: str, text: str) -> None:
        self._doc_text = (item_id, text)
        self._undo = None                       # a Keep's or Discard's Undo lasts until the next change
        self._timer.start()

    def _sheet_edited(self, item_id: str) -> None:
        self._sheet_dirty = item_id
        self._undo = None
        self._timer.start()

    def flush(self) -> None:
        """Whatever he typed and is not saved yet is saved now (the pause ended, the screen went, Jarvis closes)."""
        self._timer.stop()
        self.stage.sheet_editor.commit_editing()       # a cell being typed in goes in first (as Enter would)
        typed, self._doc_text = self._doc_text, None
        if typed is not None:
            item_id, text = typed
            reason = self.store.save_doc(item_id, text)
            if reason == store_mod.R_NOT_SAVED and self._doc_text is None:
                self._doc_text = typed                  # still unsaved: tried again at the next save or at quit
            self._after_save(item_id, reason, model.KIND_DOC)
        dirty, self._sheet_dirty = self._sheet_dirty, ""
        if dirty and dirty == self.stage.sheet_editor.item_id:
            sheet = self.stage.sheet_editor.sheet()
            on_disk = self.store.read_sheet(dirty)
            if on_disk is not None and not on_disk.read_only:
                sheet.pending = on_disk.pending     # what waits is the store's (an agent's rows may have come since)
            reason = self.store.save_sheet(dirty, sheet)
            if reason == store_mod.R_NOT_SAVED and not self._sheet_dirty:
                self._sheet_dirty = dirty               # still unsaved: tried again at the next save or at quit
            self._after_save(dirty, reason, model.KIND_SHEET)

    def _after_save(self, item_id: str, reason: str, kind: str) -> None:
        editor = self.stage.doc_editor if kind == model.KIND_DOC else self.stage.sheet_editor
        if reason:
            logger.info("Docs: %s not saved (%s)", item_id, reason)
            if editor.item_id == item_id:
                editor.notice.show_text(store_mod.REFUSAL_WORDS.get(reason, store_mod.REFUSAL_WORDS[
                    store_mod.R_NOT_SAVED]), tone=docs_view.holo.ALERT_TX)
            return
        info = self.store.info(item_id)
        if info is not None and editor.item_id == item_id:
            editor.head.meta_label.setText(docs_view.item_meta(info.kind, info.words, info.rows))
        self.refresh_list()

    def shutdown(self) -> None:
        self.flush()

    # ---- rename, delete -----------------------------------------------------------------------------------------

    def rename(self, item_id: str, name: str) -> None:
        reason = self.store.rename(item_id, name)
        if reason == store_mod.R_NO_NAME:
            info = self.store.info(item_id)
            for editor in (self.stage.doc_editor, self.stage.sheet_editor):
                if editor.item_id == item_id and info is not None:
                    editor.name_edit.setText(info.name)
            return
        if reason:
            logger.info("Docs: %s not renamed (%s)", item_id, reason)
        self.refresh_list()

    def delete(self, item_id: str) -> None:
        self.flush()
        reason = self.store.delete(item_id)
        if reason:
            logger.info("Docs: %s not deleted (%s)", item_id, reason)
            return
        self._last_deleted = item_id
        if self._current == item_id:
            self._current = ""
            self.stage.show_empty()
        self.stage.set_notice(DELETED_TEXT, undo=True)
        self.refresh_list()

    def undo_delete(self) -> None:
        item_id, self._last_deleted = self._last_deleted, ""
        self.stage.set_notice("")
        if item_id and not self.store.restore(item_id):
            self.refresh_list()
            self.open(item_id)

    def restore_deleted(self, item_id: str) -> None:
        if self.store.restore(item_id):
            return
        if item_id == self._last_deleted:
            self._last_deleted = ""
            self.stage.set_notice("")
        self.refresh_list()
        self.open(item_id)

    # ---- versions -------------------------------------------------------------------------------------------

    def _editor(self, item_id: str) -> Any:
        if self.stage.doc_editor.item_id == item_id:
            return self.stage.doc_editor
        if self.stage.sheet_editor.item_id == item_id:
            return self.stage.sheet_editor
        return None

    def toggle_versions(self, item_id: str) -> None:
        editor = self._editor(item_id)
        if editor is None:
            return
        if editor.versions_shown():
            editor.versions_box.hide()
            return
        self.flush()
        rows = [(info, f"{_when(info.when)} · {store_mod.VERSION_WORDS.get(info.reason, '')} · "
                       f"{_size(info.size)}") for info in self.store.versions(item_id)]
        editor.set_versions(rows)

    def restore_version(self, item_id: str, name: str) -> None:
        self.flush()
        found = next((info for info in self.store.versions(item_id) if info.name == name), None)
        reason = self.store.restore_version(item_id, name)
        editor = self._editor(item_id)
        if reason:
            logger.info("Docs: %s version not restored (%s)", item_id, reason)
            if editor is not None:
                editor.notice.show_text(store_mod.REFUSAL_WORDS.get(reason, ""), tone=docs_view.holo.ALERT_TX)
            return
        logger.info("Docs: %s version restored", item_id)
        self.show_item(item_id)
        if editor is not None:
            editor.versions_box.hide()
            editor.notice.show_text(RESTORED_TEXT.format(when=_when(found.when).split(" · ")[-1] if found else ""))
        self.refresh_list()

    # ---- export -------------------------------------------------------------------------------------------------

    def export(self, item_id: str) -> None:
        """Export .md / .csv: the picker (his click chooses where), a synced folder asked about first."""
        self.flush()
        info = self.store.info(item_id)
        if info is None:
            return
        data = self.store.export_md(item_id) if info.kind == model.KIND_DOC else self.store.export_csv(item_id)
        if data is None:
            editor = self._editor(item_id)
            if editor is not None:
                editor.notice.show_text(NOTHING_TO_EXPORT_TEXT, tone=docs_view.holo.ALERT_TX)
            logger.info("Docs: %s not exported (read_only)", item_id)
            return
        self._pick_and_save(item_id, info, data)

    def _pick_and_save(self, item_id: str, info: Any, data: bytes) -> None:
        suffix = ".md" if info.kind == model.KIND_DOC else ".csv"
        folder = store_mod.default_export_folder(self._export_folder) or Path.home()
        path = self._picker(info.kind, folder / file_name(info.name, suffix, info.kind))
        if not path:
            return
        if not path.casefold().endswith(suffix):
            path += suffix
        editor = self._editor(item_id)
        if store_mod.synced_folder(Path(path).parent):
            self._export = (item_id, path, data)
            if editor is not None:
                editor.notice.show_text(SYNCED_TEXT, docs_view.SAVE_HERE_TEXT, docs_view.CHOOSE_ANOTHER_TEXT,
                                        tone=docs_view.holo.AMBER)
            logger.info("Docs: %s export waits (a synced folder)", item_id)
            return
        self._write(item_id, path, data)

    def _write(self, item_id: str, path: str, data: bytes) -> None:
        kind = model.kind_of(item_id)
        reason = store_mod.write_export(path, data, mark=kind == model.KIND_SHEET)
        editor = self._editor(item_id)
        logger.info("Docs: %s exported (%s, %d bytes, %s)", item_id, kind, len(data), reason or "ok")
        if editor is not None:
            text = (store_mod.REFUSAL_WORDS[reason] if reason else EXPORTED_TEXT.format(name=Path(path).name))
            editor.notice.show_text(text, tone=docs_view.holo.ALERT_TX if reason else docs_view.holo.INK_2)

    def _notice_first(self) -> None:
        """The notice's first button: "Save here" while an export waits on a synced folder, else Undo of the last Keep
        or Discard."""
        pending, self._export = self._export, None
        if pending is not None:
            self._write(*pending)
            return
        self.undo_last()

    def _notice_second(self) -> None:
        """"Choose another": the picker again."""
        pending, self._export = self._export, None
        if pending is None:
            return
        item_id, _path, data = pending
        editor = self._editor(item_id)
        if editor is not None:
            editor.notice.show_text("")
        info = self.store.info(item_id)
        if info is not None:
            self._pick_and_save(item_id, info, data)


    # ---- what Jarvis writes (part 2): one run, its changes wait for Keep ---------------------------------------

    def ask_available(self) -> bool:
        if self._submit_mode is None:
            return False
        try:
            return bool(self._ask_on())
        except Exception:  # noqa: BLE001 - unknown: no asking
            return False

    def _sync_ask_lines(self) -> None:
        shown = self.ask_available() and not self.store.read_only
        for editor in (self.stage.doc_editor, self.stage.sheet_editor):
            editor.ask_line.setVisible(shown)
            editor.ask_line.set_running(bool(self._running))

    def ask_change(self, item_id: str, words: str) -> bool:
        """The editor's own "Ask Jarvis to change this..." (one run for this item, no planner)."""
        if self._submit(item_id, words):
            for editor in (self.stage.doc_editor, self.stage.sheet_editor):
                if editor.item_id == item_id:
                    editor.ask_line.input.clear()
            return True
        return False

    def find_for_words(self, kind: str, name: str) -> tuple[str, list[str]]:
        """The item a hand-off names: (id, []) - the exact name after folding, else the only one that shares a word
        with it -, ("", names) when more than one could be meant, ("", []) when none (a new one is made)."""
        wanted = model.fold(name)
        items = [info for info in self.store.items() if info.kind == kind]
        if not wanted:
            return "", []
        exact = [info for info in items if model.fold(info.name) == wanted]
        if len(exact) == 1:
            return exact[0].id, []
        if len(exact) > 1:
            return "", [info.name for info in exact]
        words = {word for word in wanted.split() if len(word) > 2}
        sharing = [info for info in items if words & {word for word in model.fold(info.name).split()}]
        if len(sharing) == 1:
            return sharing[0].id, []
        return "", [info.name for info in sharing]

    def from_words(self, kind: str, name: str, words: str) -> str:
        """An Ask's first run handed the request to a document or a sheet: the item it names (or a new one), then one
        document run with the owner's own words. Ambiguous: a question back, nothing runs. "" when nothing started."""
        if kind not in model.KINDS or self.store.read_only:
            return ""
        item_id, names = self.find_for_words(kind, name)
        if names:
            shown = " or ".join(names[:3])
            self._say_status(AMBIGUOUS_TEXT.format(names=shown))
            logger.info("Docs: a hand-off names %d items; asked which", len(names))
            return ""
        new = False
        if not item_id:
            default = NEW_DOC_NAME if kind == model.KIND_DOC else NEW_SHEET_NAME
            if kind == model.KIND_DOC:
                item_id, reason = self.store.create_doc(model.clean_name(name) or default)
            else:
                item_id, reason = self.store.create_sheet(model.clean_name(name) or default)
            if not item_id:
                self._say_status(store_mod.REFUSAL_WORDS.get(reason, store_mod.REFUSAL_WORDS[store_mod.R_NOT_SAVED]))
                return ""
            new = True
            self._new_items.add(item_id)
            self.refresh_list()
        QTimer.singleShot(0, self, lambda: self._submit(item_id, words, new=new))
        return item_id

    def _snapshot(self, item_id: str) -> tuple[str, Any, str]:
        """(kind, what the item holds now, its digest)."""
        import hashlib
        import json as _json

        info = self.store.info(item_id)
        if info is None:
            return "", None, ""
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            text = doc.text if doc is not None else ""
            return info.kind, text, hashlib.sha256(text.encode("utf-8")).hexdigest()
        sheet = self.store.read_sheet(item_id)
        if sheet is None:
            return "", None, ""
        sheet = sheet.copy()
        sheet.pending = []
        raw = _json.dumps([[c.id for c in sheet.columns], [[r.id, sorted(r.cells.items(), key=str)]
                                                           for r in sheet.rows]], default=str)
        return info.kind, sheet, hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _submit(self, item_id: str, words: str, *, new: bool = False) -> bool:
        if not self.ask_available() or not words.strip():
            return False
        info = self.store.info(item_id)
        if info is None or info.read_only:
            return False
        self.flush()
        kind, snapshot, digest = self._snapshot(item_id)
        if not kind:
            return False
        from .ask.modes import MODE_DOC, ORIGIN_TYPED, DocJob, ModeRequest

        try:
            tables = tuple(self._tables())[:3]
        except Exception:  # noqa: BLE001 - the run goes on without the board's tables
            tables = ()
        try:
            turns = tuple(self._conversation())
        except Exception:  # noqa: BLE001 - and without the conversation
            turns = ()
        job = DocJob(item_id, kind, info.name, snapshot, digest, new=new or item_id in self._new_items,
                     tables=tables, conversation=turns)
        request = ModeRequest(MODE_DOC, ORIGIN_TYPED, typed=words, target=item_id, payload=job)
        assert self._submit_mode is not None
        if not self._submit_mode(request, words):
            return False
        self._running = item_id
        self._sync_ask_lines()
        logger.info("Docs: %s run started (%s)", item_id, kind)
        return True

    def on_outcome(self, outcome: Any) -> None:
        """A document run's outcome: its changes wait in the item (the list's "N WAIT", the Dock's dot, the tiles)."""
        item_id = str(getattr(outcome, "target", "") or "")
        self._running = ""
        self._sync_ask_lines()
        proposal = getattr(outcome, "doc_proposal", None)
        if not getattr(outcome, "ok", False) or proposal is None:
            return
        self.flush()                        # what he typed is saved first: the item is read again below
        info = self.store.info(item_id)
        if info is None:
            logger.info("Docs: %s is gone; its changes were let go", item_id)
            return
        let_go = 0
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            if doc is None or doc.read_only:
                return
            kept, let_go = proposals.add_pending(doc.pending, proposal.items, model.MAX_PENDING_SECTIONS)
            reason = self.store.set_doc_pending(item_id, kept)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None or sheet.read_only:
                return
            sheet.pending, let_go = proposals.add_pending(sheet.pending, proposal.items, model.MAX_PENDING_ROWS)
            reason = self.store.save_sheet(item_id, sheet)
        if reason:
            logger.info("Docs: %s changes not kept waiting (%s)", item_id, reason)
            return
        if proposal.title and item_id in self._new_items:
            self.store.rename(item_id, proposal.title)
        self._new_items.discard(item_id)
        logger.info("Docs: %s has %d change(s) waiting (%d let go)", item_id, len(proposal.items), let_go)
        self._let_go[item_id] = self._let_go.get(item_id, 0) + let_go
        self.refresh_list()
        if self._current == item_id:
            self.show_item(item_id, keep_mode=True, focus=not self.pane.holds_editors())
        self._show_frame(item_id)

    # ---- the changes that wait: shown, kept, discarded ---------------------------------------------------------------

    def _source_words(self, item: dict[str, Any]) -> str:
        source = str(item.get("from") or proposals.FROM_ASK)
        if source == proposals.FROM_ASK:
            return "FROM ASK"
        by = model.clean_name(item.get("by") or "", 40)
        return f"FROM {by}" if by else "FROM AN AGENT"

    def _show_pending(self, item_id: str) -> None:
        info = self.store.info(item_id)
        if info is None:
            return
        editor = self._editor(item_id)
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            if doc is None or editor is None:
                return
            infos = []
            for item in doc.pending:
                _text, reason = proposals.apply_doc(item, doc.text)
                body = str(item.get("text") or "")
                infos.append(docs_view.ProposalInfo(str(item.get("id")), proposals.describe(item),
                                                    tuple(proposals.diff(proposals.doc_before(item, doc.text), body)),
                                                    self._source_words(item), reason == proposals.REASON_STALE,
                                                    proposals.changed_since(item, doc.text)))
            editor.proposals_box.set_proposals(infos)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None or editor is None:
                return
            rows = [docs_view.PendingRowInfo(str(item.get("id")), proposals.row_words(item, sheet),
                                             self._source_words(item), proposals.changed_since(item, sheet))
                    for item in sheet.pending if item.get("kind") != proposals.KIND_COLUMNS]
            columns = proposals.needs_columns(sheet)
            if columns is not None:
                rows.insert(0, docs_view.PendingRowInfo(str(columns.get("id")), proposals.row_words(columns, sheet),
                                                        self._source_words(columns)))
            editor.band.set_rows(rows, len(rows))
        let_go = self._let_go.get(item_id, 0)
        if let_go and editor is not None and not editor.notice.text():
            editor.notice.show_text(LET_GO_TEXT.format(count=let_go))

    def _remember_undo(self, item_id: str, kind: str, body: Any, removed: list[dict[str, Any]], text: str) -> None:
        """What one Keep or Discard changed, for its Undo: the body it replaced (the document's text, or the sheet's
        columns, keys and rows; None for a Discard) and the waiting changes it took away. Nothing else."""
        self._undo = (item_id, kind, body, [dict(item) for item in removed])
        self._export = None
        editor = self._editor(item_id)
        if editor is not None:
            editor.notice.show_text(text, docs_view.UNDO_TEXT)

    def _refused_note(self, item_id: str, reason: str) -> None:
        editor = self._editor(item_id)
        if editor is not None:
            editor.notice.show_text(store_mod.REFUSAL_WORDS.get(reason, store_mod.REFUSAL_WORDS[store_mod.R_NOT_SAVED]),
                                    tone=docs_view.holo.ALERT_TX)
        logger.info("Docs: %s Keep not done (%s)", item_id, reason)

    def keep(self, pending_id: str, *, at_end: bool = False) -> None:
        """Keep (the owner's click) on one change: it goes into the item; a version of what it was is kept first. A
        change marked "Changed since Jarvis read it" is kept only this way."""
        self.keep_many(self._current, [pending_id], at_end=at_end, single=True)

    def keep_all(self, item_id: str = "") -> None:
        """Keep all: every change that waits, except those changed since Jarvis read them (each needs its own Keep)."""
        item_id = item_id or self._current
        info = self.store.info(item_id)
        if info is None:
            return
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            ids = [str(item.get("id")) for item in (doc.pending if doc is not None else [])]
        else:
            sheet = self.store.read_sheet(item_id)
            ids = [str(item.get("id")) for item in (sheet.pending if sheet is not None else [])]
        self.keep_many(item_id, ids)

    def keep_many(self, item_id: str, pending_ids: list[str], *, at_end: bool = False, single: bool = False) -> None:
        info = self.store.info(item_id)
        if info is None or not pending_ids:
            return
        self.flush()
        wanted = set(pending_ids)
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            if doc is None or doc.read_only:
                return
            text, removed, kept, held = doc.text, [], 0, 0
            for item in doc.pending:
                if str(item.get("id")) not in wanted:
                    continue
                if not single and proposals.changed_since(item, doc.text):
                    held += 1                            # changed since Jarvis read it: its own Keep only
                    continue
                change = proposals.at_the_end(item) if at_end else item
                new_text, reason = proposals.apply_doc(change, text)
                if reason:
                    continue                             # its section has gone: it waits, with "Add at the end"
                text, kept = new_text, kept + 1
                removed.append(item)
            if not kept:
                self._show_pending(item_id)
                if held:
                    self._held_note(item_id)
                return
            reason = self.store.save_doc(item_id, text, version=store_mod.VERSION_KEEP, burst=False)
            if reason:
                self._refused_note(item_id, reason)
                return
            gone = {str(item.get("id")) for item in removed}
            self.store.set_doc_pending(item_id, [item for item in doc.pending if str(item.get("id")) not in gone])
            self._remember_undo(item_id, model.KIND_DOC, doc.text, removed, HELD_TEXT if held else KEPT_TEXT)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None or sheet.read_only:
                return
            before = (list(sheet.columns), list(sheet.keys),
                      [Row(row.id, dict(row.cells), row.source, row.made) for row in sheet.rows])
            columns = proposals.needs_columns(sheet)
            order = sorted(sheet.pending, key=lambda item: item.get("kind") != proposals.KIND_COLUMNS)
            removed, kept, gone, held, full = [], 0, 0, 0, 0
            for item in order:
                chosen = str(item.get("id")) in wanted or (item is columns and wanted)
                if not chosen:
                    continue
                if not single and proposals.changed_since(item, sheet):
                    held += 1
                    continue
                reason = proposals.apply_sheet(item, sheet)
                if reason == proposals.REASON_STALE:
                    gone += 1
                    removed.append(item)
                    continue
                if reason == model.REASON_TOO_MANY:
                    full += 1                            # past the sheet's 2,000 rows: it waits, and he is told
                    continue
                if reason:
                    continue
                kept += 1
                removed.append(item)
            if not kept and not gone:
                if full:
                    self._refused_note(item_id, store_mod.R_TOO_LARGE)
                elif held:
                    self._held_note(item_id)
                return
            taken = {str(item.get("id")) for item in removed}
            sheet.pending = [item for item in sheet.pending if str(item.get("id")) not in taken]
            reason = self.store.save_sheet(item_id, sheet, version=store_mod.VERSION_KEEP)
            if reason:
                self._refused_note(item_id, reason)
                return
            words = GONE_CELL_TEXT if gone else HELD_TEXT if held else KEPT_TEXT
            if full:
                words = store_mod.REFUSAL_WORDS[store_mod.R_TOO_LARGE]
            self._remember_undo(item_id, model.KIND_SHEET, before, removed, words)
        logger.info("Docs: %s kept %d change(s)", item_id, len(wanted))
        self.refresh_list()
        self.show_item(item_id, keep_mode=True, focus=not self.pane.holds_editors())
        self._show_frame(item_id, refresh=True)

    def _held_note(self, item_id: str) -> None:
        editor = self._editor(item_id)
        if editor is not None:
            editor.notice.show_text(HELD_ONLY_TEXT, tone=docs_view.holo.AMBER)

    def discard(self, pending_id: str) -> None:
        self.discard_many(self._current, [pending_id])

    def discard_all(self, item_id: str = "") -> None:
        item_id = item_id or self._current
        sheet = self.store.read_sheet(item_id) if model.kind_of(item_id) == model.KIND_SHEET else None
        doc = self.store.read_doc(item_id) if model.kind_of(item_id) == model.KIND_DOC else None
        pending = (sheet.pending if sheet is not None else doc.pending if doc is not None else [])
        self.discard_many(item_id, [str(item.get("id")) for item in pending])

    def discard_many(self, item_id: str, pending_ids: list[str]) -> None:
        info = self.store.info(item_id)
        if info is None or not pending_ids:
            return
        self.flush()
        wanted = set(pending_ids)
        if info.kind == model.KIND_DOC:
            doc = self.store.read_doc(item_id)
            if doc is None or doc.read_only:
                return
            removed = [item for item in doc.pending if str(item.get("id")) in wanted]
            if self.store.set_doc_pending(item_id, [item for item in doc.pending if item not in removed]):
                return
            self._remember_undo(item_id, model.KIND_DOC, None, removed, DISCARDED_TEXT)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None or sheet.read_only:
                return
            removed = [item for item in sheet.pending if str(item.get("id")) in wanted]
            sheet.pending = [item for item in sheet.pending if item not in removed]
            if self.store.save_sheet(item_id, sheet):
                return
            self._remember_undo(item_id, model.KIND_SHEET, None, removed, DISCARDED_TEXT)
        self._let_go.pop(item_id, None)
        logger.info("Docs: %s discarded %d change(s)", item_id, len(wanted))
        self.refresh_list()
        self.show_item(item_id, keep_mode=True, focus=not self.pane.holds_editors())
        self._show_frame(item_id, refresh=True)

    def undo_last(self) -> None:
        """Undo the last Keep or Discard (until the next change he makes): only what it changed comes back - the text,
        or the sheet's columns and rows, it replaced, and the changes it took away. Every change that arrived since (an
        agent's rows, an Ask's answer) stays waiting."""
        undo, self._undo = self._undo, None
        if undo is None:
            return
        item_id, kind, body, removed = undo
        if kind == model.KIND_DOC:
            if body is not None:
                reason = self.store.save_doc(item_id, body, version=store_mod.VERSION_RESTORE, burst=False)
                if reason:
                    self._refused_note(item_id, reason)
                    return
            doc = self.store.read_doc(item_id)
            current = list(doc.pending) if doc is not None else []
            have = {str(item.get("id")) for item in current}
            self.store.set_doc_pending(item_id, [item for item in removed if str(item.get("id")) not in have] + current)
        else:
            sheet = self.store.read_sheet(item_id)
            if sheet is None or sheet.read_only:
                return
            if body is not None:
                sheet.columns, sheet.keys, sheet.rows = list(body[0]), list(body[1]), list(body[2])
            have = {str(item.get("id")) for item in sheet.pending}
            sheet.pending = [item for item in removed if str(item.get("id")) not in have] + list(sheet.pending)
            reason = self.store.save_sheet(item_id, sheet, version=store_mod.VERSION_RESTORE)
            if reason:
                self._refused_note(item_id, reason)
                return
        logger.info("Docs: %s Keep or Discard undone", item_id)
        editor = self._editor(item_id)
        if editor is not None:
            editor.notice.show_text("")
        self.refresh_list()
        self.show_item(item_id, keep_mode=True, focus=not self.pane.holds_editors())
        self._show_frame(item_id, refresh=True)

    def _say_status(self, text: str) -> None:
        if self._status is not None:
            try:
                self._status(text)
            except Exception:  # noqa: BLE001 - only words
                pass

    # ---- standing agents' runs (part 4) ------------------------------------------------------------------------

    def make_for_agent(self, kind: str, name: str, columns: Any = (), keys: int = 1) -> str:
        """Turn on of an agent that writes to a new document or sheet: it is made now, empty (a sheet with its
        columns; the first ``keys`` (1..3) are the keys a later run's duplicates are matched by). "" when it can't
        be."""
        if kind == model.KIND_DOC:
            item_id, reason = self.store.create_doc(name or NEW_DOC_NAME)
        else:
            made: list[model.Column] = []
            for column_name, type_, choices in columns or ():
                column, _why = model.check_column(column_name, type_, choices, taken=tuple(c.name for c in made))
                if column is not None:
                    made.append(column)
            if not made:
                return ""
            count = max(1, min(keys if isinstance(keys, int) and not isinstance(keys, bool) else 1,
                               model.MAX_KEY_COLUMNS))
            item_id, reason = self.store.create_sheet(name or NEW_SHEET_NAME, made,
                                                      keys=[column.id for column in made[:count]])
        if not item_id:
            logger.info("Docs: an agent's %s not made (%s)", kind, reason)
            return ""
        logger.info("Docs: %s made for an agent", item_id)
        self.refresh_list()
        return item_id

    def import_runs(self, runs: Any, jobs: Any) -> int:
        """Each agent run that lands in a document or a sheet, taken in once (its id marked imported only once what
        it wrote is stored, or it was let go on purpose: its item is gone, or it holds nothing new): a document gets
        the report as the section "## <date> · <agent name>" (control and invisible characters dropped), a sheet the
        run's rows - each cell typed by its column (a blank cell stays empty; one that doesn't fit is left empty and
        counted), a row whose key columns match a kept row's becomes cell changes, one already there or waiting is
        dropped. All of it waits for Keep. A file that can't be read now is tried again later. How many runs were
        taken in."""
        from .format_chars import drop_format

        done = self.store.imported()
        if any(run.id not in done and run.job_kind == "agent" and run.status == "ok" for run in runs):
            self.flush()                    # what he typed is saved first: the item is read again below
        taken: list[str] = []
        touched: set[str] = set()
        bad = 0
        for run in sorted(runs, key=lambda item: item.started or datetime.min.replace(tzinfo=self._clock().tzinfo)):
            if run.id in done or run.job_kind != "agent" or run.status != "ok":
                continue
            job = jobs.get(run.job_id) if hasattr(jobs, "get") else None
            lands = tuple(run.lands) or (tuple(job.lands) if job is not None and getattr(job, "lands", ()) else ())
            if len(lands) != 2 or not lands[1]:
                continue
            kind, item_id = lands
            info = self.store.info(item_id)
            if info is None or info.trashed or info.kind != kind:
                logger.info("Docs: run %s lands nowhere (%s); let go", run.id, "gone" if info is None else "moved")
                taken.append(run.id)
                continue
            by = model.clean_name(getattr(job, "name", "") or run.job_name, 40)
            made = (run.started or self._clock()).isoformat(timespec="seconds")
            source = {"source": run.job_id, "run": run.id, "made": made}
            if kind == model.KIND_DOC:
                report = drop_format(model.clean_doc_text(run.report)).strip()
                if not report:
                    taken.append(run.id)
                    continue
                doc = self.store.read_doc(item_id)
                if doc is None or doc.read_only:
                    continue                                       # tried again at the next reload
                day = (run.started or self._clock())
                section = f"## {day:%b} {day.day} · {by}\n\n{report}"
                item = proposals.doc_add(section, **source)
                item["by"] = by
                kept, _let_go = proposals.add_pending(doc.pending, [item], model.MAX_PENDING_SECTIONS)
                if not self.store.set_doc_pending(item_id, kept):
                    touched.add(item_id)
                    taken.append(run.id)
                continue
            sheet = self.store.read_sheet(item_id)
            if sheet is None or sheet.read_only:
                continue                                           # tried again at the next reload
            if not sheet.columns:
                taken.append(run.id)
                continue
            typed: list[dict[str, Any]] = []
            for raw in run.rows[:20]:
                cells: dict[str, Any] = {}
                for column, text in zip(sheet.columns, raw):
                    if not str(text).strip():
                        continue                                   # a blank cell: empty, never a checkbox's "no"
                    value, reason = model.parse_value(column, text)
                    if reason:
                        bad += 1
                    elif value is not None:
                        cells[column.id] = value
                if cells:
                    typed.append(cells)
            keyed = sheet.copy()
            if not keyed.keys:
                keyed.keys = [sheet.columns[0].id]
            items = proposals.merge_rows(keyed, typed, **source) if typed else []
            for item in items:
                item["by"] = by
            if not items:
                taken.append(run.id)                               # nothing new in it
                continue
            sheet.pending, let_go = proposals.add_pending(sheet.pending, items, model.MAX_PENDING_ROWS)
            if not self.store.save_sheet(item_id, sheet, version=None):
                self._let_go[item_id] = self._let_go.get(item_id, 0) + let_go
                touched.add(item_id)
                taken.append(run.id)
        if taken:
            self.store.mark_imported(taken)
            logger.info("Docs: %d agent run(s) taken in, %d item(s) changed, %d cell(s) left empty", len(taken),
                        len(touched), bad)
        if touched:
            self.refresh_list()
            for item_id in touched:
                if self._current == item_id:
                    self.show_item(item_id, keep_mode=True, focus=not self.pane.holds_editors())
                self._show_frame(item_id, refresh=True)
        return len(taken)

    # ---- links ------------------------------------------------------------------------------------------------

    def open_link(self, url: str) -> None:
        """A link cell's Open link (the owner's click): only a link actions.web_link_allowed passes."""
        from .actions import web_link_allowed

        if not web_link_allowed(url):
            self.stage.sheet_editor.reason_label.setText(LINK_REFUSED_TEXT)
            self.stage.sheet_editor.reason_label.show()
            logger.info("Docs: a link was not opened (not allowed)")
            return
        logger.info("Docs: a link cell opened")
        self._open_url(url)
