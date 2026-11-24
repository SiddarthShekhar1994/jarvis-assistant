"""Documents and datasheets, the store (part 1): the docs folder in a temp data folder - index, doc.md, sheet.json,
versions (bursts, Keep, Restore, an outside edit), the trash and its 30 days, read-only on damage (never overwritten),
the caps, the read-only looks the standing parts use, and logs without a name or a cell. No Qt."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from briefing_reader.docs import model
from briefing_reader.docs import store as st

PST = timezone(timedelta(hours=-8))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 22, 10, 0, tzinfo=PST)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def col(name: str, type_: str, choices: str = "") -> model.Column:
    column, _ = model.check_column(name, type_, choices)
    assert column is not None
    return column


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.clock = Clock()
        self.store = st.DocsStore(self.data, clock=self.clock)

    def reopen(self) -> st.DocsStore:
        self.store = st.DocsStore(self.data, clock=self.clock)
        return self.store


class DocTests(StoreCase):
    def test_create_read_save_and_index(self) -> None:
        item_id, reason = self.store.create_doc("  Trip   plan ", "# Trip\r\n\r\nPack\r\n")
        self.assertEqual(reason, "")
        folder = self.data / "docs" / item_id
        self.assertEqual((folder / "doc.md").read_bytes(), b"# Trip\n\nPack\n")      # LF only
        doc = self.store.read_doc(item_id)
        assert doc is not None
        self.assertEqual((doc.name, doc.text, doc.read_only), ("Trip plan", "# Trip\n\nPack\n", False))
        self.assertEqual(self.store.save_doc(item_id, "# Trip\n\nPack the bags\n"), "")
        info = self.reopen().info(item_id)
        assert info is not None
        self.assertEqual((info.kind, info.words), (model.KIND_DOC, 4))
        index = json.loads((self.data / "docs" / "index.json").read_text(encoding="ascii"))
        self.assertEqual(index["version"], 1)
        self.assertEqual([entry["id"] for entry in index["items"]], [item_id])

    def test_versions_once_per_burst_and_before_a_keep(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "one\n")
        self.store.save_doc(item_id, "two\n")
        self.clock.tick(30)
        self.store.save_doc(item_id, "three\n")                 # the same burst: no new version
        self.assertEqual(len(self.store.versions(item_id)), 1)
        self.clock.tick(121)
        self.store.save_doc(item_id, "four\n")
        self.assertEqual(len(self.store.versions(item_id)), 2)
        self.clock.tick(1)
        self.store.save_doc(item_id, "five\n", version=st.VERSION_KEEP, burst=False)
        found = self.store.versions(item_id)
        self.assertEqual([info.reason for info in found], ["keep", "edit", "edit"])
        self.assertEqual(self.store.read_version(item_id, found[0].name), "four\n")

    def test_restore_makes_a_version_first(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "first\n")
        self.store.save_doc(item_id, "second\n")
        old = self.store.versions(item_id)[0]
        self.clock.tick(5)
        self.assertEqual(self.store.restore_version(item_id, old.name), "")
        doc = self.store.read_doc(item_id)
        assert doc is not None
        self.assertEqual(doc.text, "first\n")
        self.assertEqual(self.store.versions(item_id)[0].reason, st.VERSION_RESTORE)
        self.assertEqual(self.store.read_version(item_id, self.store.versions(item_id)[0].name), "second\n")
        self.assertEqual(self.store.restore_version(item_id, "nope.md"), st.R_NOT_FOUND)

    def test_versions_are_capped(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "0\n")
        for n in range(1, 36):
            self.clock.tick(1)
            self.store.save_doc(item_id, f"{n}\n", version=st.VERSION_KEEP, burst=False)
        self.assertEqual(len(self.store.versions(item_id)), st.KEEP_VERSIONS)

    def test_an_outside_edit_becomes_a_version(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "mine\n")
        path = self.data / "docs" / item_id / "doc.md"
        path.write_bytes(b"edited by hand\n")
        doc = self.store.read_doc(item_id)
        assert doc is not None
        self.assertEqual(doc.text, "edited by hand\n")
        found = self.store.versions(item_id)
        self.assertEqual([info.reason for info in found], [st.VERSION_OUTSIDE])
        self.assertEqual(self.store.read_version(item_id, found[0].name), "edited by hand\n")
        self.store.read_doc(item_id)                           # read again: nothing new
        self.assertEqual(len(self.store.versions(item_id)), 1)

    def test_caps(self) -> None:
        self.assertEqual(self.store.create_doc("Big", "x" * (model.MAX_DOC_CHARS + 1)), ("", st.R_TOO_LONG))
        item_id, _ = self.store.create_doc("Ok")
        self.assertEqual(self.store.save_doc(item_id, "x" * (model.MAX_DOC_CHARS + 1)), st.R_TOO_LONG)
        for _ in range(model.MAX_ITEMS - 1):
            self.store.create_doc("n")
        self.assertEqual(self.store.create_doc("one more"), ("", st.R_TOO_MANY))

    def test_a_too_large_doc_is_read_only_and_left_alone(self) -> None:
        item_id, _ = self.store.create_doc("Notes", "a\n")
        path = self.data / "docs" / item_id / "doc.md"
        path.write_bytes(b"x" * (model.MAX_DOC_BYTES + 5))
        doc = self.store.read_doc(item_id)
        assert doc is not None
        self.assertTrue(doc.read_only)
        self.assertEqual(self.store.save_doc(item_id, "b\n"), st.R_READ_ONLY)
        self.assertEqual(path.stat().st_size, model.MAX_DOC_BYTES + 5)


class SheetTests(StoreCase):
    def test_create_and_save_rows(self) -> None:
        company, deadline = col("Company", "text"), col("Deadline", "date")
        item_id, reason = self.store.create_sheet("Internships", [company, deadline], keys=[company.id])
        self.assertEqual(reason, "")
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        self.assertEqual(([c.name for c in sheet.columns], sheet.keys), (["Company", "Deadline"], [company.id]))
        sheet.rows.append(st.Row(model.new_row_id(), {company.id: "Acme", deadline.id: "2026-12-01"}))
        self.assertEqual(self.store.save_sheet(item_id, sheet), "")
        again = self.reopen().read_sheet(item_id)
        assert again is not None
        self.assertEqual(again.rows[0].cells, {company.id: "Acme", deadline.id: "2026-12-01"})
        self.assertEqual(self.store.info(item_id).rows, 1)  # type: ignore[union-attr]

    def test_a_cell_of_the_wrong_type_on_disk_is_an_empty_cell(self) -> None:
        deadline = col("Deadline", "date")
        item_id, _ = self.store.create_sheet("S", [deadline])
        path = self.data / "docs" / item_id / "sheet.json"
        data = json.loads(path.read_text(encoding="ascii"))
        data["rows"] = [{"id": "r-00000001", "cells": {deadline.id: "soon"}, "from": "owner", "made": ""}]
        path.write_text(json.dumps(data), encoding="ascii")
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        self.assertEqual(sheet.rows[0].cells, {})

    def test_a_damaged_sheet_is_read_only_and_never_overwritten(self) -> None:
        item_id, _ = self.store.create_sheet("S", [col("A", "text")])
        path = self.data / "docs" / item_id / "sheet.json"
        path.write_bytes(b"{not json")
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        self.assertTrue(sheet.read_only)
        self.assertEqual(self.store.save_sheet(item_id, st.SheetData(item_id, "S")), st.R_READ_ONLY)
        self.assertEqual(path.read_bytes(), b"{not json")

    def test_a_sheet_changed_outside_jarvis_is_kept_as_a_version(self) -> None:   # fix round 1, finding 6
        column = col("A", "text")
        item_id, _ = self.store.create_sheet("S", [column])
        sheet = self.store.read_sheet(item_id)
        sheet.rows.append(st.Row("r-0000aaaa", {column.id: "one"}))
        self.store.save_sheet(item_id, sheet)
        held = self.store.read_sheet(item_id)
        path = self.data / "docs" / item_id / "sheet.json"
        data = json.loads(path.read_text(encoding="ascii"))
        data["rows"].append({"id": "r-0000bbbb", "cells": {column.id: "outside"}, "from": "owner", "made": ""})
        path.write_text(json.dumps(data), encoding="ascii")
        self.clock.tick(30)                                    # within the editing burst
        held.rows[0].cells[column.id] = "two"
        self.assertEqual(self.store.save_sheet(item_id, held), "")
        latest = self.store.versions(item_id)[0]
        self.assertEqual(latest.reason, st.VERSION_OUTSIDE)
        kept = self.store.read_version(item_id, latest.name)
        self.assertIsNotNone(kept.row("r-0000bbbb"))  # type: ignore[union-attr]

    def test_sheet_caps(self) -> None:
        many = [col(f"c{i}", "text") for i in range(model.MAX_COLUMNS + 1)]
        self.assertEqual(self.store.create_sheet("S", many), ("", st.R_TOO_LARGE))
        item_id, _ = self.store.create_sheet("S", many[:2])
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        sheet.rows = [st.Row(model.new_row_id(), {}) for _ in range(model.MAX_ROWS + 1)]
        self.assertEqual(self.store.save_sheet(item_id, sheet), st.R_TOO_LARGE)


class IndexTests(StoreCase):
    def test_a_damaged_index_makes_everything_read_only(self) -> None:
        self.store.create_doc("A", "a\n")
        path = self.data / "docs" / "index.json"
        path.write_bytes(b"[broken")
        store = self.reopen()
        self.assertTrue(store.read_only)
        self.assertEqual(store.items(), [])
        self.assertEqual(store.create_doc("B"), ("", st.R_READ_ONLY))
        self.assertEqual(path.read_bytes(), b"[broken")

    def test_a_newer_index_is_left_alone(self) -> None:
        path = self.data / "docs" / "index.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": 9, "items": []}), encoding="ascii")
        store = self.reopen()
        self.assertEqual(store.state, st.STATE_NEWER)
        self.assertEqual(store.create_sheet("S"), ("", st.R_READ_ONLY))
        self.assertEqual(json.loads(path.read_text())["version"], 9)

    def test_too_large_index(self) -> None:
        path = self.data / "docs" / "index.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b" " * (st.MAX_INDEX_BYTES + 10))
        self.assertEqual(self.reopen().state, st.STATE_TOO_LARGE)

    def test_bad_entries_are_dropped(self) -> None:
        path = self.data / "docs" / "index.json"
        path.parent.mkdir(parents=True)
        good = model.new_id(model.KIND_DOC)
        path.write_text(json.dumps({"version": 1, "items": [
            {"id": "../../evil", "kind": "doc", "name": "x"}, {"id": good, "kind": "sheet", "name": "kind wrong"},
            {"id": good, "kind": "doc", "name": "ok", "created": "", "changed": ""}]}), encoding="ascii")
        self.assertEqual([info.id for info in self.reopen().items()], [good])


class TrashTests(StoreCase):
    def test_delete_restore_and_30_days(self) -> None:
        item_id, _ = self.store.create_doc("A", "a\n")
        self.assertEqual(self.store.delete(item_id), "")
        self.assertEqual(self.store.items(), [])
        self.assertEqual([info.id for info in self.store.deleted()], [item_id])
        self.assertTrue((self.data / "docs" / "trash" / item_id / "doc.md").exists())
        self.assertIsNone(self.store.read_doc(item_id))
        self.assertEqual(self.store.restore(item_id), "")
        self.assertEqual(self.store.read_doc(item_id).text, "a\n")  # type: ignore[union-attr]
        self.store.delete(item_id)
        self.clock.tick(29 * 86400)
        self.assertEqual(len(self.reopen().deleted()), 1)
        self.clock.tick(2 * 86400)
        self.assertEqual(self.reopen().deleted(), [])
        self.assertFalse((self.data / "docs" / "trash" / item_id).exists())


class LookTests(StoreCase):
    def test_find_by_name_and_read_columns_write_nothing(self) -> None:
        status = col("Status", "choice", "Open, Applied")
        item_id, _ = self.store.create_sheet("Internships", [col("Company", "text"), status])
        sheet = self.store.read_sheet(item_id)
        assert sheet is not None
        sheet.rows.append(st.Row(model.new_row_id(), {status.id: "Open"}))
        self.store.save_sheet(item_id, sheet)
        before = {p: p.stat().st_mtime_ns for p in (self.data / "docs").rglob("*") if p.is_file()}
        found = st.find_by_name(self.data, " internships ")
        assert found is not None
        self.assertEqual((found.id, found.kind), (item_id, model.KIND_SHEET))
        self.assertIsNone(st.find_by_name(self.data, "Internships", model.KIND_DOC))
        columns, keys = st.read_columns(self.data, item_id)  # type: ignore[misc]
        self.assertEqual([c.name for c in columns], ["Company", "Status"])
        self.assertEqual(keys, [])
        after = {p: p.stat().st_mtime_ns for p in (self.data / "docs").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertIsNone(st.read_columns(self.data, "s-000000000000"))
        self.assertIsNone(st.read_columns(self.data, "../x"))

    def test_imported_run_ids(self) -> None:
        self.store.mark_imported(["run-1", "run-2", "run-1"])
        self.assertEqual(self.reopen().imported(), frozenset({"run-1", "run-2"}))
        self.store.mark_imported([f"r{n}" for n in range(model.MAX_IMPORTED + 5)])
        self.assertEqual(len(self.store.imported()), model.MAX_IMPORTED)


class LogTests(StoreCase):
    def test_logs_hold_no_name_or_text(self) -> None:
        with self.assertLogs("briefing_reader.docs.store", logging.DEBUG) as logs:
            logging.getLogger("briefing_reader.docs.store").debug("start")
            item_id, _ = self.store.create_doc("Secret project", "# Hidden heading\nprivate words\n")
            sheet_id, _ = self.store.create_sheet("Secret sheet", [col("Hidden column", "text")])
            self.store.delete(item_id)
            (self.data / "docs" / sheet_id / "sheet.json").write_bytes(b"x")
            self.store.read_sheet(sheet_id)
        text = "\n".join(logs.output)
        for word in ("Secret", "Hidden", "private"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
