"""Documents and datasheets on the screen (part 1): the Documents screen's list and editors, the read view's lockdown
(no HTML, nothing loaded, links not clickable), typed cells with Jarvis's own refusals and Undo, and the app: the Dock's
Documents (Ctrl+0), what he types saved after the pause and when the screen goes, Delete with Undo, versions, Export
with the picker passed in (a synced folder asked about first), Open link checked, the canvas look, logs without text.
Offscreen; temp folders only; never a real dialog or browser."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import dock as dock_mod
from briefing_reader import docs_ui, docs_view, holo, ui
from briefing_reader.docs import model
from briefing_reader.docs import store as st
from tests.ui_fakes import AppHarness, delete_now, fonts, settle, wait_for

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_docs_view", "-platform", "offscreen"])
    fonts()


def col(name: str, type_: str, choices: str = "") -> model.Column:
    column, _ = model.check_column(name, type_, choices)
    assert column is not None
    return column


class StageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stage = docs_view.DocsStage()
        self.addCleanup(delete_now, self.stage)
        self.stage.resize(1200, 800)
        self.stage.show()
        settle()

    def test_list_rows_meta_and_wait_chip(self) -> None:
        rows = [docs_view.ItemRowInfo("d-000000000001", model.KIND_DOC, "Trip plan",
                                      docs_view.item_meta(model.KIND_DOC, 1240, 0), 2),
                docs_view.ItemRowInfo("s-000000000002", model.KIND_SHEET, "Internships",
                                      docs_view.item_meta(model.KIND_SHEET, 0, 42))]
        picked: list[str] = []
        self.stage.itemPicked.connect(picked.append)
        self.stage.set_items(rows, [docs_view.ItemRowInfo("d-000000000003", model.KIND_DOC, "Old", "", 0,
                                                          "DELETED NOV 22")])
        first = self.stage.item_row("d-000000000001")
        assert first is not None
        self.assertEqual(first.info.meta, "DOC \u00b7 1,240 WORDS")
        self.assertEqual(first.chip_text(), "2 WAIT")
        self.assertIn("2 changes wait on you", first.accessibleName())
        self.assertEqual(self.stage.item_row("s-000000000002").info.meta, "SHEET \u00b7 42 ROWS")  # type: ignore
        self.assertEqual(self.stage.item_row("s-000000000002").chip_text(), "")  # type: ignore[union-attr]
        QTest.mouseClick(first, Qt.MouseButton.LeftButton)
        self.assertEqual(picked, ["d-000000000001"])
        restored: list[str] = []
        self.stage.deletedRestoreRequested.connect(restored.append)
        self.stage.deleted_row("d-000000000003").restore_button.click()  # type: ignore[union-attr]
        self.assertEqual(restored, ["d-000000000003"])
        self.assertFalse(self.stage.deleted_head.isHidden())

    def test_read_view_is_locked_down(self) -> None:
        editor = self.stage.doc_editor
        self.stage.show_doc(docs_view.DocView("d-000000000001", "N", "# Head\n\n<b>raw</b> [link](https://x.example)"
                                              "\n\n![p](file:///C:/Windows/win.ini)\n"))
        view = editor.read_view
        self.assertIsInstance(view.document(), docs_view.SafeDocument)
        self.assertIsNone(view.document().loadResource(int(QTextDocument.ResourceType.ImageResource),
                                                         QUrl("file:///C:/Windows/win.ini")))
        flags = view.textInteractionFlags()
        self.assertFalse(flags & Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.assertFalse(flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.assertTrue(view.isReadOnly())
        self.assertIn("<b>raw</b>", view.toPlainText())          # HTML stays text
        self.assertEqual(editor.mode(), docs_view.READ_TEXT)

    def test_edit_view_is_the_source(self) -> None:
        editor = self.stage.doc_editor
        typed: list[tuple[str, str]] = []
        editor.textEdited.connect(lambda i, t: typed.append((i, t)))
        self.stage.show_doc(docs_view.DocView("d-000000000001", "N", "# A\n"))
        editor.edit_button.click()
        self.assertEqual(editor.mode(), docs_view.EDIT_TEXT)
        self.assertEqual(editor.source_edit.toPlainText(), "# A\n")
        QTest.keyClicks(editor.source_edit, "x")
        self.assertTrue(typed and typed[-1][0] == "d-000000000001")
        editor.read_button.click()
        self.assertIn("A", editor.read_view.toPlainText())

    def test_an_empty_doc_opens_in_edit_and_read_only_cannot(self) -> None:
        editor = self.stage.doc_editor
        self.stage.show_doc(docs_view.DocView("d-000000000001", "N", ""))
        self.assertEqual(editor.mode(), docs_view.EDIT_TEXT)
        self.stage.show_doc(docs_view.DocView("d-000000000002", "N", "x", read_only=True, read_only_text="Can't"))
        self.assertEqual(editor.mode(), docs_view.READ_TEXT)
        self.assertFalse(editor.edit_button.isEnabled())
        self.assertTrue(editor.source_edit.isReadOnly())
        self.assertEqual(editor.banner.text(), "Can't")


class SheetEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.editor = docs_view.SheetEditor()
        self.addCleanup(delete_now, self.editor)
        self.editor.resize(900, 600)
        self.editor.show()
        self.company, self.when, self.done, self.status = (col("Company", "text"), col("Deadline", "date"),
                                                           col("Done", "checkbox"),
                                                           col("Status", "choice", "Open, Applied"))
        self.sheet = st.SheetData("s-000000000001", "Internships", [self.company, self.when, self.done, self.status],
                                  rows=[st.Row("r-00000001", {self.company.id: "Acme"})])
        self.edited: list[str] = []
        self.editor.sheetEdited.connect(self.edited.append)
        self.editor.show_sheet(self.sheet)
        settle()

    def test_typed_cells_refuse_with_words_and_undo(self) -> None:
        m = self.editor.model
        self.assertEqual(m.headerData(1, Qt.Orientation.Horizontal), "Deadline  \u00b7 DATE")
        when = m.index(0, 1)
        self.assertFalse(m.setData(when, "next week"))
        self.assertEqual(self.editor.reason_text(), model.REASON_WORDS[model.REASON_NOT_DATE])
        self.assertTrue(m.setData(when, "12/1/2026"))
        self.assertEqual(m.data(when), "2026-12-01")
        self.assertEqual(self.edited, ["s-000000000001"])
        self.assertEqual(self.editor.reason_text(), "")
        self.assertTrue(m.setData(m.index(0, 3), "applied"))
        self.assertEqual(m.data(m.index(0, 3)), "Applied")
        done = m.index(0, 2)
        self.assertTrue(m.flags(done) & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(m.setData(done, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole))
        self.assertEqual(m.data(done, Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Checked)
        m.undo.undo()
        m.undo.undo()
        m.undo.undo()
        self.assertEqual(self.editor.sheet().rows[0].cells, {self.company.id: "Acme"})

    def test_add_row_and_column(self) -> None:
        self.editor.add_row_button.click()
        self.assertEqual(len(self.editor.sheet().rows), 2)
        self.editor.add_column_button.click()
        form = self.editor.column_form
        self.assertFalse(form.isHidden())
        form.name_edit.setText("company")
        form.add_button.click()
        self.assertEqual(self.editor.reason_text(), model.REASON_WORDS[model.REASON_SAME_NAME])
        form.name_edit.setText("Stage")
        form.type_box.setCurrentIndex(model.TYPES.index(model.TYPE_CHOICE))
        self.assertFalse(form.choices_edit.isHidden())
        form.add_button.click()
        self.assertEqual(self.editor.reason_text(), model.REASON_WORDS[model.REASON_NO_CHOICES])
        form.choices_edit.setText("Round 1, Round 2")
        form.add_button.click()
        self.assertTrue(form.isHidden())
        self.assertEqual([c.name for c in self.editor.sheet().columns][-1], "Stage")
        self.editor.model.undo.undo()
        self.assertEqual(len(self.editor.sheet().columns), 4)

    def test_open_link_only_for_a_link_cell(self) -> None:
        page = col("Page", "link")
        sheet = st.SheetData("s-000000000002", "S", [page], rows=[st.Row("r-00000002", {page.id: "https://x.example"})])
        self.editor.show_sheet(sheet)
        asked: list[str] = []
        self.editor.openLinkRequested.connect(asked.append)
        self.assertFalse(self.editor.open_link_button.isEnabled())
        self.editor.table.setCurrentIndex(self.editor.model.index(0, 0))
        self.assertTrue(self.editor.open_link_button.isEnabled())
        self.editor.open_link_button.click()
        self.assertEqual(asked, ["https://x.example"])

    def test_read_only_sheet(self) -> None:
        sheet = st.SheetData("s-000000000003", "S", [self.company], rows=[st.Row("r-00000003", {})], read_only=True)
        self.editor.show_sheet(sheet, read_only_text="Can't")
        self.assertFalse(self.editor.model.setData(self.editor.model.index(0, 0), "x"))
        self.assertFalse(self.editor.add_row_button.isEnabled())
        self.assertFalse(self.editor.model.flags(self.editor.model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.picked: list[tuple[str, Path]] = []
        self.answer: list[str] = []
        self.opened: list[str] = []
        self.h = AppHarness(self, ask=False)
        self.c = self.h.c
        self.docs = self.c.docs
        assert self.docs is not None
        self.docs._picker = self._pick
        self.docs._open_url = self.opened.append
        self.h.reading(autoplay=False)
        self.reading = self.c.window.reading
        self.stage = self.reading.docs_stage
        self.data = self.h.config.data_dir

    def _pick(self, kind: str, suggested: Path) -> str:
        self.picked.append((kind, suggested))
        return self.answer.pop(0) if self.answer else ""

    def test_the_dock_slot_and_its_key(self) -> None:
        self.assertTrue(self.reading.docs_available())
        self.assertEqual((dock_mod.LABELS[dock_mod.DOCS], dock_mod.SHORTCUTS[dock_mod.DOCS]), ("Documents", "Ctrl+0"))
        self.assertIn(dock_mod.DOCS, self.reading.dock.visible_slots())
        self.reading.set_tab(ui.SCREEN_DOCS)
        self.assertIs(self.reading._screens.currentWidget(), self.stage)
        names = [item.label for item in self.reading.find_items("docu")]
        self.assertIn("Documents", names)
        zero = [shortcut for shortcut in self.reading._shortcuts if shortcut.key() == QKeySequence("Ctrl+0")]
        self.assertEqual(len(zero), 1)
        self.reading.set_tab(ui.SCREEN_HOME)
        zero[0].activated.emit()                                  # Ctrl+0 opens Documents
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_DOCS)

    def test_off_hides_it(self) -> None:
        h = AppHarness(self, ask=False, config_extra="\n[docs]\nenabled = false\n")
        self.assertIsNone(h.c.docs)
        self.assertFalse(h.c.window.reading.docs_available())

    def test_new_doc_typed_saved_after_the_pause_and_on_leaving(self) -> None:
        self.reading.set_tab(ui.SCREEN_DOCS)
        self.stage.new_doc_button.click()
        item_id = self.docs.current()
        self.assertEqual(model.kind_of(item_id), model.KIND_DOC)
        editor = self.stage.doc_editor
        self.assertEqual(editor.mode(), docs_view.EDIT_TEXT)
        editor.name_edit.setText("Trip plan")
        editor.name_edit.editingFinished.emit()
        editor.source_edit.setPlainText("# Trip\n\nPack")
        path = self.data / "docs" / item_id / "doc.md"
        self.assertTrue(wait_for(lambda: path.read_text(encoding="utf-8") == "# Trip\n\nPack", 3))
        editor.source_edit.setPlainText("# Trip\n\nPack the bags")
        self.reading.set_tab(ui.SCREEN_HOME)                    # the screen goes: saved at once
        self.assertEqual(path.read_text(encoding="utf-8"), "# Trip\n\nPack the bags")
        self.assertEqual(self.docs.store.info(item_id).name, "Trip plan")  # type: ignore[union-attr]
        row = self.stage.item_row(item_id)
        assert row is not None
        self.assertEqual(row.info.meta, "DOC \u00b7 4 WORDS")

    def test_sheet_edit_saved_and_undo(self) -> None:
        self.reading.set_tab(ui.SCREEN_DOCS)
        self.stage.new_sheet_button.click()
        item_id = self.docs.current()
        editor = self.stage.sheet_editor
        self.assertFalse(editor.empty_label.isHidden())
        editor.add_row_button.click()                            # no column yet: the column form opens
        self.assertFalse(editor.column_form.isHidden())
        editor.column_form.name_edit.setText("Company")
        editor.column_form.add_button.click()
        editor.add_row_button.click()
        editor.model.setData(editor.model.index(0, 0), "Acme")
        self.docs.flush()
        sheet = self.docs.store.read_sheet(item_id)
        assert sheet is not None
        self.assertEqual([row.cells for row in sheet.rows], [{sheet.columns[0].id: "Acme"}])

    def test_delete_with_undo_and_the_deleted_list(self) -> None:
        item_id, _ = self.docs.store.create_doc("Old notes", "x\n")
        self.docs.refresh_list()
        self.docs.open(item_id)
        self.stage.doc_editor.delete_button.click()
        self.assertIsNone(self.stage.item_row(item_id))
        self.assertEqual(self.stage.notice.text(), docs_ui.DELETED_TEXT)
        self.assertIsNotNone(self.stage.deleted_row(item_id))
        self.stage.notice.first.click()                           # Undo
        self.assertIsNotNone(self.stage.item_row(item_id))
        self.assertEqual(self.docs.current(), item_id)
        self.stage.doc_editor.delete_button.click()
        self.stage.deleted_row(item_id).restore_button.click()  # type: ignore[union-attr]
        self.assertIsNotNone(self.stage.item_row(item_id))

    def test_versions_and_restore(self) -> None:
        item_id, _ = self.docs.store.create_doc("Notes", "first\n")
        self.docs.store.save_doc(item_id, "second\n")
        self.docs.open(item_id)
        editor = self.stage.doc_editor
        editor.versions_button.click()
        self.assertTrue(editor.versions_shown())
        self.assertEqual(len(editor.versions_box.rows), 1)
        self.assertIn("While you edited", editor.versions_box.rows[0].label.text())
        editor.versions_box.rows[0].restore_button.click()
        self.assertEqual(self.docs.store.read_doc(item_id).text, "first\n")  # type: ignore[union-attr]
        self.assertIn("Restored the version", editor.notice.text())
        self.assertIn("first", editor.read_view.toPlainText())

    def test_export_through_the_picker(self) -> None:
        item_id, _ = self.docs.store.create_doc("Plan: Q4", "# Q4\n")
        self.docs.open(item_id)
        target = self.h.root / "out" / "plan"
        self.answer.append(str(target))
        self.stage.doc_editor.export_button.click()
        self.assertEqual(self.picked[0][0], model.KIND_DOC)
        self.assertEqual(self.picked[0][1].name, "Plan_ Q4.md")
        self.assertEqual((self.h.root / "out" / "plan.md").read_bytes(), b"# Q4\n")
        self.assertEqual(self.stage.doc_editor.notice.text(), "Saved plan.md.")
        self.picked.clear()
        self.stage.doc_editor.export_button.click()               # the picker cancelled: nothing
        self.assertEqual(len(self.picked), 1)

    def test_a_synced_folder_is_asked_about_first(self) -> None:
        sheet_id, _ = self.docs.store.create_sheet("S", [col("A", "text")])
        self.docs.open(sheet_id)
        synced = self.h.root / "OneDrive" / "s.csv"
        elsewhere = self.h.root / "local" / "s.csv"
        self.answer.extend([str(synced), str(elsewhere)])
        editor = self.stage.sheet_editor
        editor.export_button.click()
        self.assertEqual(editor.notice.text(), docs_ui.SYNCED_TEXT)
        self.assertFalse(synced.exists())
        editor.notice.second.click()                                 # Choose another
        self.assertTrue(elsewhere.exists())
        self.assertTrue(elsewhere.read_bytes().startswith(b"\xef\xbb\xbfA"))
        self.answer.append(str(synced))
        editor.export_button.click()
        editor.notice.first.click()                                  # Save here
        self.assertTrue(synced.exists())

    def test_a_file_that_cant_be_read_exports_nothing_and_says_so(self) -> None:   # fix round 1, finding 7
        item_id, _ = self.docs.store.create_doc("Big", "# Big\n")
        (self.data / "docs" / item_id / "doc.md").write_bytes(b"a" * (model.MAX_DOC_BYTES + 5))
        self.docs.open(item_id)
        self.answer.append(str(self.h.root / "out" / "big.md"))
        self.docs.export(item_id)
        self.assertEqual(self.picked, [])
        self.assertFalse((self.h.root / "out" / "big.md").exists())
        self.assertEqual(self.stage.doc_editor.notice.text(), docs_ui.NOTHING_TO_EXPORT_TEXT)

    def test_a_failed_save_is_tried_again(self) -> None:                   # fix round 1, finding 10
        item_id, _ = self.docs.store.create_doc("Notes", "a\n")
        self.docs.open(item_id)
        real = self.docs.store.save_doc
        self.docs.store.save_doc = lambda *args, **kwargs: st.R_NOT_SAVED   # type: ignore[method-assign]
        self.stage.doc_editor.source_edit.setPlainText("typed\n")
        self.docs.flush()
        self.assertIsNotNone(self.docs._doc_text)                            # still marked unsaved
        self.docs.store.save_doc = real                                      # type: ignore[method-assign]
        self.docs.shutdown()                                                 # Jarvis closes: saved now
        self.assertEqual(self.docs.store.read_doc(item_id).text, "typed\n")  # type: ignore[union-attr]

    def test_open_link_is_checked(self) -> None:
        page = col("Page", "link")
        sheet_id, _ = self.docs.store.create_sheet("Links", [page])
        self.docs.open(sheet_id)
        self.docs.open_link("https://example.com/job")
        self.docs.open_link("http://127.0.0.1/x")
        self.assertEqual(self.opened, ["https://example.com/job"])
        self.assertEqual(self.stage.sheet_editor.reason_text(), docs_ui.LINK_REFUSED_TEXT)

    def test_canvas_look(self) -> None:
        item_id, _ = self.docs.store.create_doc("Notes", "# Head\nbody\n")
        holo.set_canvas_look(True)
        self.docs.open(item_id)
        settle()
        image = self.c.window.grab().toImage()
        self.assertFalse(image.isNull())
        self.assertIn("#03090C".lower(), self.stage.doc_editor.source_edit.styleSheet().lower())

    def test_logs_hold_no_text(self) -> None:
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            self.reading.set_tab(ui.SCREEN_DOCS)
            self.stage.new_doc_button.click()
            self.stage.doc_editor.name_edit.setText("Secret plan")
            self.stage.doc_editor.name_edit.editingFinished.emit()
            self.stage.doc_editor.source_edit.setPlainText("# Hidden heading\nprivate words")
            self.docs.flush()
            self.answer.append(str(self.h.root / "out" / "x.md"))
            self.stage.doc_editor.export_button.click()
            self.stage.doc_editor.delete_button.click()
        text = "\n".join(logs.output)
        for word in ("Secret", "Hidden", "private", str(self.h.root)):
            self.assertNotIn(word, text)


class StandaloneControllerTests(unittest.TestCase):
    def test_a_damaged_index_shows_the_banner_and_no_new(self) -> None:
        h = AppHarness(self, ask=False, prepare=lambda cfg: _bad_index(cfg.data_dir))
        stage = h.c.window.reading.docs_stage
        self.assertEqual(stage.banner.text(), docs_ui.READ_ONLY_TEXT)
        self.assertFalse(stage.new_doc_button.isEnabled())


def _bad_index(data_dir: Path) -> None:
    (data_dir / "docs").mkdir(parents=True, exist_ok=True)
    (data_dir / "docs" / "index.json").write_bytes(b"{bad")


if __name__ == "__main__":
    unittest.main()
