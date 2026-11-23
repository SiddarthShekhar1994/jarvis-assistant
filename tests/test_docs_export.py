"""Documents and datasheets, export (part 1): .md is the kept text only, .csv is UTF-8 with a BOM, each cell by its
type, formula-looking cells guarded with a leading ', what waits for Keep left out, the .csv marked from the internet,
a OneDrive folder recognised as synced, and file names Windows accepts. Temp folders only; no Qt."""

from __future__ import annotations

import csv
import io
import os
import tempfile
import unittest
from pathlib import Path

from briefing_reader import docs_ui
from briefing_reader.docs import model
from briefing_reader.docs import store as st


def col(name: str, type_: str, choices: str = "") -> model.Column:
    column, _ = model.check_column(name, type_, choices)
    assert column is not None
    return column


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = st.DocsStore(self.root / "data")

    def test_md_is_the_kept_text_only(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "# Kept\n\nmine\n")
        self.store.set_doc_pending(item_id, [{"id": "p-1", "op": "add", "text": "## Waiting\nnot kept yet"}])
        data = self.store.export_md(item_id)
        self.assertEqual(data, b"# Kept\n\nmine\n")

    def test_csv_by_type_with_bom_and_guard(self) -> None:
        name, pay, when, done, page, status = (col("Name", "text"), col("Pay", "number"), col("When", "date"),
                                               col("Done", "checkbox"), col("Page", "link"),
                                               col("Status", "choice", "Open, Applied"))
        item_id, _ = self.store.create_sheet("=Sheet", [name, pay, when, done, page, status])
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        sheet.rows = [st.Row(model.new_row_id(), {name.id: "=HYPERLINK(\"x\")", pay.id: 1250.5, when.id: "2026-12-01",
                                                  done.id: True, page.id: "https://example.com/a,b",
                                                  status.id: "Open"}),
                      st.Row(model.new_row_id(), {name.id: "+1", pay.id: -3}),
                      st.Row(model.new_row_id(), {name.id: "@me"})]
        sheet.pending = [{"id": "p-1", "kind": "row", "cells": {name.id: "Pending Co"}}]
        self.store.save_sheet(item_id, sheet)
        data = self.store.export_csv(item_id)
        assert data is not None
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Name", "Pay", "When", "Done", "Page", "Status"])
        self.assertEqual(rows[1], ["'=HYPERLINK(\"x\")", "1250.5", "2026-12-01", "TRUE", "https://example.com/a,b",
                                   "Open"])
        self.assertEqual(rows[2], ["'+1", "-3", "", "FALSE", "", ""])     # a checked number stays a number
        self.assertEqual(rows[3][0], "'@me")
        self.assertEqual(len(rows), 4)                             # the pending row is not in it
        self.assertNotIn(b"Pending Co", data)

    def test_nothing_is_exported_from_a_file_that_cant_be_read(self) -> None:   # fix round 1, finding 7
        doc_id, _ = self.store.create_doc("Big", "# Big\n")
        (self.root / "data" / "docs" / doc_id / "doc.md").write_bytes(b"a" * (model.MAX_DOC_BYTES + 5))
        self.assertIsNone(self.store.export_md(doc_id))
        sheet_id, _ = self.store.create_sheet("S", [col("A", "text")])
        (self.root / "data" / "docs" / sheet_id / "sheet.json").write_bytes(b"{bad")
        self.assertIsNone(self.store.export_csv(sheet_id))

    def test_guard_cell(self) -> None:
        for first in ("=", "+", "-", "@", "\t", "\r"):
            self.assertEqual(st.guard_cell(first + "x"), "'" + first + "x")
        self.assertEqual(st.guard_cell("plain"), "plain")

    def test_write_export_marks_the_csv(self) -> None:
        path = self.root / "out" / "sheet.csv"
        self.assertEqual(st.write_export(path, b"a,b\r\n", mark=True), "")
        self.assertEqual(path.read_bytes(), b"a,b\r\n")
        if os.name == "nt":
            try:
                mark = Path(str(path) + ":Zone.Identifier").read_bytes()
            except OSError:
                self.skipTest("this drive keeps no streams")
            self.assertIn(b"ZoneId=3", mark)
        md = self.root / "out" / "notes.md"
        self.assertEqual(st.write_export(md, b"# x\n"), "")
        if os.name == "nt":
            self.assertFalse(Path(str(md) + ":Zone.Identifier").exists())

    def test_synced_folder(self) -> None:
        environ = {"OneDrive": str(self.root / "Sync")}
        self.assertTrue(st.synced_folder(self.root / "Sync" / "Docs", environ))
        self.assertTrue(st.synced_folder(self.root / "OneDrive - Example" / "x", {}))
        self.assertFalse(st.synced_folder(self.root / "Exports", environ))

    def test_default_folder(self) -> None:
        self.assertEqual(st.default_export_folder("", {"USERPROFILE": str(self.root)}),
                         self.root / "Jarvis" / "Exports")
        self.assertEqual(st.default_export_folder(str(self.root / "Mine"), {}), self.root / "Mine")
        self.assertIsNone(st.default_export_folder("", {}))

    def test_file_names(self) -> None:
        self.assertEqual(docs_ui.file_name('Plan: "Q4"/draft?', ".md", "doc"), "Plan_ _Q4__draft_.md")
        self.assertEqual(docs_ui.file_name("...", ".csv", "sheet"), "sheet.csv")


if __name__ == "__main__":
    unittest.main()
