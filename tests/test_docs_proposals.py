"""Pending changes (part 2): a document change spliced in by heading (every other byte stays), "stale" when its
section has gone and "Add at the end" then, the word-by-word before/after, a sheet's columns, rows and cells applied to
a copy, an agent's duplicate rows by key columns as cell changes, the caps letting the oldest go. No Qt, no files."""

from __future__ import annotations

import unittest

from briefing_reader.docs import model, proposals as pr
from briefing_reader.docs.store import Row, SheetData

DOC = "# Plan\n\nStep one.\n\n## Packing\n\n- passport\n\n# Notes\n\nLast.\n"


def column(name: str, type_: str, choices: str = "") -> model.Column:
    found, _ = model.check_column(name, type_, choices)
    assert found is not None
    return found


class DocTests(unittest.TestCase):
    def test_add_after_and_replace(self) -> None:
        text, reason = pr.apply_doc(pr.doc_add("## Budget\n\n$200", "Packing"), DOC)
        self.assertEqual(reason, "")
        self.assertTrue(text.startswith(DOC[:DOC.index("# Notes")]))
        self.assertTrue(text.endswith(DOC[DOC.index("# Notes"):]))
        text, reason = pr.apply_doc(pr.doc_replace("Packing", "## Packing\n\n- passport\n- adapter"), DOC)
        self.assertEqual(reason, "")
        self.assertIn("- adapter", text)
        self.assertEqual(text.replace("\n- adapter", ""), DOC)

    def test_stale_then_at_the_end(self) -> None:
        item = pr.doc_replace("Gone", "## Gone\nnew")
        self.assertEqual(pr.apply_doc(item, DOC), (DOC, pr.REASON_STALE))
        self.assertEqual(pr.apply_doc(pr.doc_add("x", "Gone"), DOC)[1], pr.REASON_STALE)
        text, reason = pr.apply_doc(pr.at_the_end(item), DOC)
        self.assertEqual(reason, "")
        self.assertTrue(text.startswith(DOC) and text.endswith("## Gone\nnew\n"))

    def test_before_and_diff(self) -> None:
        item = pr.doc_replace("Packing", "## Packing\n\n- passport\n- adapter\n")
        before = pr.doc_before(item, DOC)
        self.assertEqual(before, "## Packing\n\n- passport\n\n")
        parts = pr.diff(before, item["text"])
        self.assertIn((pr.ADDED, "adapter"), [(kind, text.strip("- \n")) for kind, text in parts if kind == pr.ADDED])
        self.assertEqual("".join(text for kind, text in parts if kind != pr.REMOVED), item["text"])
        self.assertEqual("".join(text for kind, text in parts if kind != pr.ADDED), before)
        self.assertEqual(pr.diff("", "new"), [(pr.ADDED, "new")])
        self.assertEqual(pr.describe(item), "CHANGE “Packing”")
        self.assertEqual(pr.describe(pr.doc_replace("Packing", "")), "REMOVE “Packing”")
        self.assertEqual(pr.describe(pr.doc_add("x")), "ADD AT THE END")


class SheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company, self.status = column("Company", "text"), column("Status", "choice", "Open, Applied")
        self.sheet = SheetData("s-000000000001", "S", [self.company, self.status], [self.company.id],
                               [Row("r-00000001", {self.company.id: "Acme", self.status.id: "Open"})])

    def test_columns_rows_cells(self) -> None:
        blank = SheetData("s-000000000002", "New")
        cols = [column("Company", "text"), column("Due", "date")]
        self.assertEqual(pr.apply_sheet(pr.sheet_columns(cols, [cols[0].id]), blank), "")
        self.assertEqual([c.name for c in blank.columns], ["Company", "Due"])
        self.assertEqual(blank.keys, [cols[0].id])
        self.assertEqual(pr.apply_sheet(pr.sheet_row({cols[0].id: "Acme", cols[1].id: "nope", "c-ffffff": 1}),
                                        blank), "")
        self.assertEqual(blank.rows[0].cells, {cols[0].id: "Acme"})          # the bad and unknown cells left out
        cell = pr.sheet_cell("r-00000001", self.status.id, "Applied", "Open")
        self.assertEqual(pr.apply_sheet(cell, self.sheet), "")
        self.assertEqual(self.sheet.rows[0].cells[self.status.id], "Applied")
        self.assertEqual(pr.apply_sheet(pr.sheet_cell("r-0000ffff", self.status.id, "Open"), self.sheet),
                         pr.REASON_STALE)
        self.assertEqual(pr.apply_sheet({"kind": "delete"}, self.sheet), pr.REASON_BAD)

    def test_row_words(self) -> None:
        row = pr.sheet_row({self.company.id: "Globex", self.status.id: "Open"})
        self.assertEqual(pr.row_words(row, self.sheet), "Globex · Open")
        cell = pr.sheet_cell("r-00000001", self.status.id, "Applied", "Open")
        self.assertEqual(pr.row_words(cell, self.sheet), "Row 1 · Status: Applied (was Open)")

    def test_merge_rows_by_key(self) -> None:
        rows = [{self.company.id: "acme", self.status.id: "Applied"},       # the same company: a cell change
                {self.company.id: "Acme", self.status.id: "Open"},          # ... saying nothing new: nothing
                {self.company.id: "Globex", self.status.id: "Open"},        # a new row
                {self.company.id: "Globex"}]                                # ... once
        out = pr.merge_rows(self.sheet, rows, source="a-1", run="run-1", made="")
        self.assertEqual([item["kind"] for item in out], ["cell", "row"])
        self.assertEqual((out[0]["row"], out[0]["value"], out[0]["was"]), ("r-00000001", "Applied", "Open"))
        self.assertEqual((out[1]["from"], out[1]["run"]), ("a-1", "run-1"))

    def test_add_pending_lets_the_oldest_go(self) -> None:
        old = [pr.sheet_row({}) for _ in range(5)]
        new = [pr.sheet_row({}) for _ in range(3)]
        kept, let_go = pr.add_pending(old, new, 6)
        self.assertEqual((len(kept), let_go), (6, 2))
        self.assertEqual(kept[-3:], new)
        self.assertNotEqual(pr.new_pending_id(), pr.new_pending_id())


if __name__ == "__main__":
    unittest.main()
