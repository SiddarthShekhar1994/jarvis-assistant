"""Jarvis writes in documents and sheets from an Ask (part 2): a first run hands the request over (the owner's own words
go on to one document run), the changes wait in the item (the list's "N WAIT", the Dock's amber dot, the tiles with
their before and after), Keep / Discard / Keep all and Undo after, a stale change offered "Add at the end", a new sheet's
columns kept with its rows, the editor's own "Ask Jarvis to change this..." (one run), an ambiguous name asked about,
voice never keeps, and the logs hold no text. AppHarness: a fake Claude Code that only records, Qt offscreen; every name
and text is invented."""

from __future__ import annotations

import json
import logging
import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import dock as dock_mod
from briefing_reader import docs_ui, docs_view, ui
from briefing_reader.docs import model
from briefing_reader.docs import store as st
from tests.ask_fakes import plan_stream
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
DOC = "# Trip plan\n\nFly out Friday.\n\n## Packing\n\n- passport\n"
ASKED = "add a budget section after Packing to my trip plan"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_docs_app", "-platform", "offscreen"])
    fonts()


def handoff(kind: str = "doc", name: str = "Trip plan") -> list:
    return plan_stream({"say": "I'll write that.", "question": "", "lines": [],
                        "doc_job": {"kind": kind, "name": name, "why": "write it somewhere else"}})


def doc_answer(changes, say: str = "Added a budget after Packing.") -> list:  # type: ignore[no-untyped-def]
    return plan_stream({"say": say, "question": "", "doc": {"title": "", "changes": changes}, "sheet": None})


ADD_BUDGET = {"op": "add", "heading": "", "after": "Packing", "text": "## Budget\n\nHotel: 200"}
NEW_PACKING = {"op": "replace", "heading": "Packing", "after": "", "text": "## Packing\n\n- passport\n- adapter"}


class DocsAppCase(unittest.TestCase):
    runs: tuple = ()

    def setUp(self) -> None:
        self.h = AppHarness(self, ask=True, runs=list(self.make_runs()))
        self.h.reading(autoplay=False)
        self.c = self.h.c
        self.reading = self.c.window.reading
        self.docs = self.c.docs
        self.stage = self.reading.docs_stage
        self.store = self.docs.store

    def make_runs(self) -> list:
        return []

    def submit(self, text: str) -> None:
        self.reading.command_bar.set_text(text)
        self.reading.command_bar.input.returnPressed.emit()
        settle()

    def finish(self, runs: int) -> None:
        self.assertTrue(wait_for(lambda: len(self.h.factory.runner.started) >= runs and not self.c.ask.busy
                                 and not self.docs._running, 10))
        settle(4)

    def pending(self, item_id: str) -> list:
        if model.kind_of(item_id) == model.KIND_DOC:
            return self.store.read_doc(item_id).pending  # type: ignore[union-attr]
        return self.store.read_sheet(item_id).pending  # type: ignore[union-attr]


class HandOffTests(DocsAppCase):
    def make_runs(self) -> list:
        return [handoff(), doc_answer([ADD_BUDGET, NEW_PACKING])]

    def test_the_handoff_then_one_run_and_the_changes_wait(self) -> None:
        item_id, _ = self.store.create_doc("Trip plan", DOC)
        self.docs.refresh_list()
        with self.assertLogs("briefing_reader", level="INFO") as logs:
            self.submit(ASKED)
            self.finish(2)
        runner = self.h.factory.runner
        first = runner.started[0].argv
        self.assertIn("doc_job", json.loads(first[first.index("--json-schema") + 1])["properties"])
        second = runner.started[1]
        self.assertIn('<doc name="Trip plan">', second.stdin)
        self.assertIn(f"The owner's words: {ASKED}", second.stdin)         # his words, never the planner's "why"
        self.assertNotIn("somewhere else", second.stdin)
        self.assertEqual(len(self.pending(item_id)), 2)
        doc = self.store.read_doc(item_id)
        self.assertEqual(doc.text, DOC)                                        # nothing kept yet
        self.assertEqual(self.reading.dock.docs_waiting(), 1)
        self.assertEqual(self.reading.dock.signal_words(dock_mod.DOCS), "1 waiting on you")
        self.assertEqual(self.stage.item_row(item_id).chip_text(), "2 WAIT")  # type: ignore[union-attr]
        text = "\n".join(logs.output)
        for secret in ("Trip", "Packing", "budget", "Hotel", "passport", "adapter"):
            self.assertNotIn(secret, text)

        self.docs.open(item_id)
        box = self.stage.doc_editor.proposals_box
        self.assertEqual(len(box.tiles), 2)
        self.assertFalse(box.keep_all_button.isHidden())
        change = box.tile(self.pending(item_id)[1]["id"])
        assert change is not None
        self.assertIn("CHANGE", change.head_label.text())
        self.assertIn("adapter", change.diff.plain())
        self.assertIn("FROM ASK", change.source_label.text())

        add = box.tiles[0]
        add.keep_button.click()
        settle()
        kept = self.store.read_doc(item_id)
        self.assertIn("## Budget\n\nHotel: 200", kept.text)
        self.assertTrue(kept.text.startswith(DOC))
        self.assertEqual(len(kept.pending), 1)
        self.assertEqual(self.store.versions(item_id)[0].reason, st.VERSION_KEEP)
        self.assertEqual(self.stage.doc_editor.notice.text(), docs_ui.KEPT_TEXT)
        self.stage.doc_editor.notice.first.click()                         # Undo
        settle()
        undone = self.store.read_doc(item_id)
        self.assertEqual((undone.text, len(undone.pending)), (DOC, 2))
        box.keep_all_button.click()
        settle()
        both = self.store.read_doc(item_id)
        self.assertEqual(both.pending, [])
        self.assertIn("- adapter", both.text)
        self.assertIn("## Budget", both.text)
        self.assertEqual(self.reading.dock.docs_waiting(), 0)


class AmbiguousTests(DocsAppCase):
    def make_runs(self) -> list:
        return [handoff(name="trip")]

    def test_two_could_be_meant_a_question_and_no_run(self) -> None:
        self.store.create_doc("Trip plan", DOC)
        self.store.create_doc("Trip budget", "x\n")
        self.docs.refresh_list()
        self.submit("add hotels to my trip doc")
        self.finish(1)
        self.assertEqual(len(self.h.factory.runner.started), 1)
        self.assertIn("Which one do you mean", self.reading.command_bar.status())
        self.assertEqual(sum(len(self.pending(info.id)) for info in self.store.items()), 0)


class NewSheetTests(DocsAppCase):
    def make_runs(self) -> list:
        answer = {"say": "Started it.", "question": "", "doc": None,
                  "sheet": {"title": "Internships", "columns": [
                      {"name": "Company", "type": "text", "choices": []},
                      {"name": "Deadline", "type": "date", "choices": []},
                      {"name": "Status", "type": "choice", "choices": ["Open", "Applied"]}],
                      "add_rows": [["Acme", "2026-12-01", "Open"], ["Globex", "", "Applied"]], "set_cells": []}}
        return [handoff("sheet", ""), plan_stream(answer)]

    def test_a_new_sheet_its_columns_and_rows_kept_together(self) -> None:
        self.submit("start a sheet of internships I'm looking at")
        self.finish(2)
        sheets = [info for info in self.store.items() if info.kind == model.KIND_SHEET]
        self.assertEqual(len(sheets), 1)
        sheet_id = sheets[0].id
        self.assertEqual(sheets[0].name, "Internships")                     # the run's title for the new sheet
        self.assertEqual(len(self.pending(sheet_id)), 3)
        self.docs.open(sheet_id)
        band = self.stage.sheet_editor.band
        self.assertFalse(band.isHidden())
        self.assertIn("Columns: Company", band.rows[0][0].text)
        band.keep_all_button.click()
        settle()
        sheet = self.store.read_sheet(sheet_id)
        self.assertEqual([c.name for c in sheet.columns], ["Company", "Deadline", "Status"])
        self.assertEqual(len(sheet.rows), 2)
        self.assertEqual(sheet.pending, [])
        self.assertEqual(self.stage.sheet_editor.model.rowCount(), 2)


class PaneAskTests(DocsAppCase):
    def make_runs(self) -> list:
        return [doc_answer([NEW_PACKING])]

    def test_the_editor_s_own_line_is_one_run(self) -> None:
        item_id, _ = self.store.create_doc("Trip plan", DOC)
        self.docs.refresh_list()
        self.docs.open(item_id)
        line = self.stage.doc_editor.ask_line
        self.assertFalse(line.isHidden())
        line.input.setText("add an adapter to packing")
        line.input.returnPressed.emit()
        settle()
        self.finish(1)
        started = self.h.factory.runner.started
        self.assertEqual(len(started), 1)
        self.assertIn("--system-prompt-file", started[0].argv)
        self.assertEqual(len(self.pending(item_id)), 1)
        self.assertEqual(line.input.text(), "")
        self.assertEqual(len(self.stage.doc_editor.proposals_box.tiles), 1)

    def test_a_stale_change_is_added_at_the_end(self) -> None:
        item_id, _ = self.store.create_doc("Trip plan", DOC)
        self.docs.refresh_list()
        self.docs.open(item_id)
        self.docs.ask_change(item_id, "add an adapter to packing")
        self.finish(1)
        self.store.save_doc(item_id, "# Trip plan\n\nFly out Friday.\n")      # he removed Packing meanwhile
        self.docs.show_item(item_id)
        tile = self.stage.doc_editor.proposals_box.tiles[0]
        self.assertTrue(tile.info.stale)
        self.assertTrue(tile.keep_button.isHidden())
        self.assertFalse(tile.at_end_button.isHidden())
        tile.at_end_button.click()
        settle()
        doc = self.store.read_doc(item_id)
        self.assertTrue(doc.text.endswith("## Packing\n\n- passport\n- adapter\n"))
        self.assertEqual(doc.pending, [])

    def test_discard_and_undo(self) -> None:
        item_id, _ = self.store.create_doc("Trip plan", DOC)
        self.docs.refresh_list()
        self.docs.open(item_id)
        self.docs.ask_change(item_id, "add an adapter to packing")
        self.finish(1)
        self.stage.doc_editor.proposals_box.tiles[0].discard_button.click()
        settle()
        self.assertEqual(self.pending(item_id), [])
        self.assertEqual(self.store.read_doc(item_id).text, DOC)  # type: ignore[union-attr]
        self.assertEqual(self.stage.doc_editor.notice.text(), docs_ui.DISCARDED_TEXT)
        self.stage.doc_editor.notice.first.click()
        settle()
        self.assertEqual(len(self.pending(item_id)), 1)


class VoiceTests(DocsAppCase):
    def make_runs(self) -> list:
        return [doc_answer([NEW_PACKING]), plan_stream({"say": "Nothing to do.", "question": "", "lines": []})]

    def test_voice_never_keeps(self) -> None:
        item_id, _ = self.store.create_doc("Trip plan", DOC)
        self.docs.refresh_list()
        self.docs.ask_change(item_id, "add an adapter to packing")
        self.finish(1)
        self.c.ask.submit_spoken("keep all of Jarvis's changes")
        self.finish(2)
        self.assertEqual(len(self.pending(item_id)), 1)                      # still waiting: only a click keeps
        self.assertEqual(self.store.read_doc(item_id).text, DOC)  # type: ignore[union-attr]


class SheetBandTests(DocsAppCase):
    def make_runs(self) -> list:
        return [plan_stream({"say": "Updated.", "question": "", "doc": None, "sheet": {
            "title": "", "columns": [], "add_rows": [["Globex", "Applied"]],
            "set_cells": []}})]

    def test_one_row_kept_from_the_band(self) -> None:
        company, _ = model.check_column("Company", "text")
        status, _ = model.check_column("Status", "choice", "Open, Applied")
        sheet_id, _ = self.store.create_sheet("Internships", [company, status])
        self.docs.refresh_list()
        self.docs.open(sheet_id)
        self.docs.ask_change(sheet_id, "add Globex, applied")
        self.finish(1)
        band = self.stage.sheet_editor.band
        self.assertEqual(band.title_label.text(), "1 ROW WAITS ON YOU")
        self.assertEqual(band.rows[0][0].text, "Globex · Applied")
        band.keepOne.emit(band.rows[0][0].pending_id)
        settle()
        sheet = self.store.read_sheet(sheet_id)
        self.assertEqual([row.cells for row in sheet.rows], [{company.id: "Globex", status.id: "Applied"}])
        self.assertTrue(band.isHidden())


if __name__ == "__main__":
    unittest.main()
