"""Standing agents' reports land in a document or a sheet (part 4): an agent's "lands" (loaded from older files too),
the setup finding the item by name read-only or planning a new sheet ("Writes to: ..."), Turn on making it; the runner
giving a sheet's column names but never its rows, reading at most 20 rows strictly, writing them into the run file and
never into the docs folder; consent and Pause all still stopping runs; the app taking each run in once (typed cells,
a bad cell left empty and counted, duplicates by the key column as cell changes, a report as a section), the Dock's
dot and the Agents screen's line. Fakes only; temporary folders; every name and text is invented."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLabel

from briefing_reader import standing_setup, standing_ui
from briefing_reader.ask import stream
from briefing_reader.config import AskConfig, StandingConfig
from briefing_reader.docs import model as docs_model
from briefing_reader.docs import proposals
from briefing_reader.docs import store as docs_store
from briefing_reader.standing import agents, engine, model, runner, setup
from briefing_reader.standing.agents import AgentAnswer
from briefing_reader.standing.engine import StandingResult
from briefing_reader.standing.store import AppState, Definitions, RunFiles, RunRequests, StandingFiles
from tests.ask_fakes import result_event
from tests.standing_fakes import (AGENT_ID, AGENT_NAME, STANDING_ON, agent_data, consent, files_of, save_jobs,
                                  write_run)
from tests.test_standing_runner import T0, Reader
from tests.ui_fakes import NOW, AppHarness, fonts, settle

_app: QApplication | None = None
COLUMNS = [{"name": "Company", "type": "text", "choices": []}, {"name": "Role", "type": "text", "choices": []},
           {"name": "Deadline", "type": "date", "choices": []},
           {"name": "Status", "type": "choice", "choices": ["Open", "Applied"]}]


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_lands", "-platform", "offscreen"])
    fonts()


def snapshot(folder: Path) -> dict:
    return {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in folder.rglob("*") if path.is_file()}


class ModelTests(unittest.TestCase):
    def test_lands_round_trip_and_older_files(self) -> None:
        data = agent_data(lands={"kind": "sheet", "id": "", "name": "Internships", "columns": COLUMNS})
        agent = model.agent_from(data)
        self.assertEqual((agent.lands, agent.lands_name), (("sheet", ""), "Internships"))
        self.assertEqual(agent.lands_columns[3], ("Status", "choice", ("Open", "Applied")))
        again = model.agent_from(model.to_data(agent))
        self.assertEqual(again, agent)
        old = model.agent_from(agent_data())
        self.assertEqual((old.lands, old.lands_name, old.lands_columns), ((), "", ()))
        self.assertNotIn("lands", model.to_data(old))

    def test_bad_lands_are_refused(self) -> None:
        for bad in ({"kind": "table", "id": "", "name": "x"}, {"kind": "doc", "id": "s-000000000001", "name": "x"},
                    {"kind": "sheet", "id": "", "name": "x", "columns": []},
                    {"kind": "doc", "id": "", "name": "x", "columns": COLUMNS[:1]},
                    {"kind": "sheet", "id": "", "name": "x", "columns": [{"name": "A", "type": "formula"}]},
                    {"kind": "sheet", "id": "", "name": "x", "columns": [dict(COLUMNS[0], name=f"c{n}")
                                                                         for n in range(9)]}):
            with self.subTest(bad=bad), self.assertRaises(model.DefinitionError):
                model.agent_from(agent_data(lands=bad))


def setup_answer(lands) -> dict:  # type: ignore[no-untyped-def]
    return {"kind": "agent", "say": "Set up.", "name": "Internship watch",
            "agent": {"trigger": {"kind": "time", "times": ["07:30"]},
                      "mail": [{"account": "work", "query": "from:jobs@example.org"}], "proposes": [],
                      "report": "every_run", "lands": lands}}


WORDS = "every morning put new internships from jobs@example.org in my Internships sheet"


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.store = docs_store.DocsStore(self.data)

    def define(self, lands, docs_dir="here"):  # type: ignore[no-untyped-def]
        return setup.definition(setup_answer(lands), WORDS, aliases=["work"], readable=["work"],
                                docs_dir=self.data if docs_dir == "here" else docs_dir)

    def test_an_existing_sheet_is_found_read_only(self) -> None:
        column, _ = docs_model.check_column("Company", "text")
        sheet_id, _ = self.store.create_sheet("Internships", [column])
        before = snapshot(self.data / "docs")
        job = self.define({"kind": "sheet", "name": "internships"})
        self.assertEqual((job.lands, job.lands_name), (("sheet", sheet_id), "Internships"))
        self.assertEqual(snapshot(self.data / "docs"), before)
        self.assertEqual(standing_setup.writes_to(job),
                         "Writes to: Internships (your sheet; what it writes waits for your Keep)")

    def test_a_new_sheet_is_planned(self) -> None:
        job = self.define({"kind": "sheet", "name": "Internships", "columns": COLUMNS})
        self.assertEqual(job.lands, ("sheet", ""))
        line = standing_setup.writes_to(job)
        self.assertEqual(line, "Writes to: Internships (a new sheet: Company · Role · Deadline · Status)")
        self.assertIn(line, standing_setup.job_lines(job, SimpleNamespace()))
        self.assertFalse((self.data / "docs" / "index.json").exists() and
                         json.loads((self.data / "docs" / "index.json").read_text())["items"])
        with self.assertRaises(setup.SetupRefused):
            self.define({"kind": "sheet", "name": "Other"})                   # a new sheet needs its columns
        doc = self.define({"kind": "doc", "name": "Job notes"})
        self.assertEqual(standing_setup.writes_to(doc), "Writes to: Job notes (a new document)")

    def test_docs_off_no_lands(self) -> None:
        job = self.define({"kind": "sheet", "name": "Internships", "columns": COLUMNS}, docs_dir=None)
        self.assertEqual(job.lands, ())


class ReadTests(unittest.TestCase):
    INIT = stream.InitSummary("none", ("StructuredOutput",), 0, "claude-sonnet-4-5", "2.1.293", True)

    def test_rows_only_when_offered_and_capped(self) -> None:
        answer = {"report": "Two new.", "summary": "Two", "needs_owner": False, "rows": [["Acme", "Intern"]]}
        self.assertEqual(agents.read_agent(result_event(answer), self.INIT).kind, stream.GARBLED)  # type: ignore
        found = agents.agent_reader(4)(result_event(answer), self.INIT)
        self.assertEqual(found.rows, (("Acme", "Intern"),))  # type: ignore[union-attr]
        for bad in ([["a"] * 5], [["x"]] * 21, [["y" * 501]], [[1]]):
            got = agents.agent_reader(4)(result_event(dict(answer, rows=bad)), self.INIT)
            self.assertEqual(getattr(got, "kind", ""), stream.GARBLED, bad)
        schema = json.loads(engine.schema_text("agent", {"rows": agents.rows_property(4)}))
        self.assertEqual(schema["properties"]["rows"]["maxItems"], agents.MAX_ROWS)
        self.assertNotIn("rows", json.loads(engine.schema_text("agent"))["properties"])


class SheetEngine:
    def __init__(self, rows) -> None:  # type: ignore[no-untyped-def]
        self.calls: list[dict] = []
        self.rows = rows

    def run(self, prompt, *, kind, model, timeout_s, read, cancel=None, **extra):  # type: ignore[no-untyped-def]
        self.calls.append({"prompt": prompt, "kind": kind, **extra})
        return StandingResult(answer=AgentAnswer("Two internships came in.", "Two new", False, (), tuple(self.rows)))


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.files = StandingFiles(self.data)
        self.now = T0
        self.config = SimpleNamespace(data_dir=self.data, standing=StandingConfig(enabled=True),
                                      ask=AskConfig(enabled=True), accounts={"personal": None})
        self.store = docs_store.DocsStore(self.data)
        company, _ = docs_model.check_column("Company", "text")
        due, _ = docs_model.check_column("Deadline", "date")
        self.sheet_id, _ = self.store.create_sheet("Internships", [company, due], keys=[company.id])
        sheet = self.store.read_sheet(self.sheet_id)
        sheet.rows.append(docs_store.Row(docs_model.new_row_id(), {company.id: "Secretco", due.id: "2026-12-24"}))
        self.store.save_sheet(self.sheet_id, sheet)
        agent = model.agent_from(dict(agent_data(), mail=[], calendar_days=0, notes=True,
                                      trigger={"kind": "manual"},
                                      lands={"kind": "sheet", "id": self.sheet_id, "name": "Internships"}))
        Definitions(self.files).save([agent], [])
        AppState(self.files).set_consent(True)
        self.engine = SheetEngine([["Acme", "2026-12-01"], ["Globex", ""]])

    def check(self) -> runner.CheckResult:
        RunRequests(self.files).ask(AGENT_ID, self.now)
        deps = runner.Deps(readers={"personal": Reader([])}, engine=self.engine, clock=lambda: self.now,
                           app_running=lambda: False, tell_app=lambda runs: True, start_app=lambda: True,
                           hold_extra_usage=lambda until: None, own=lambda: ["owner@example.com"],
                           notes=lambda: ["Prefers remote roles."])
        return runner.run_check(self.config, deps, origin=runner.ORIGIN_TASK)

    def test_columns_never_rows_and_nothing_written_to_docs(self) -> None:
        before = snapshot(self.data / "docs")
        self.check()
        self.assertEqual(len(self.engine.calls), 1)
        call = self.engine.calls[0]
        self.assertIn('<sheet name="Internships">', call["prompt"])
        self.assertIn("columns: Company [text] | Deadline [date]", call["prompt"])
        self.assertNotIn("Secretco", call["prompt"])                       # never a row
        self.assertIn("rows", call["schema_extra"])
        self.assertEqual(snapshot(self.data / "docs"), before)            # the runner never writes there
        (path,) = RunFiles(self.files).paths()
        record = RunFiles.read(path)
        self.assertEqual(record["lands"], {"kind": "sheet", "id": self.sheet_id})
        self.assertEqual(record["rows"], [["Acme", "2026-12-01"], ["Globex", ""]])

    def test_consent_and_pause_all_still_stop_runs(self) -> None:
        AppState(self.files).set_consent(False)
        self.assertEqual(self.check().status, "no_consent")
        AppState(self.files).set_consent(True)
        AppState(self.files).set_paused_all(True)
        self.assertEqual(self.check().status, "paused")
        self.assertEqual(self.engine.calls, [])


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = AppHarness(self, config_extra=STANDING_ON, prepare=self.prepare)
        self.c = self.app.c
        self.s = self.c.standing
        self.docs = self.c.docs
        self.c._locked = lambda: False
        self.s.busy_probe = lambda: False
        self.s.start_runner = lambda: True
        self.app.reading(autoplay=False)

    def prepare(self, config) -> None:  # type: ignore[no-untyped-def]
        store = docs_store.DocsStore(config.data_dir)
        company, _ = docs_model.check_column("Company", "text")
        due, _ = docs_model.check_column("Deadline", "date")
        self.company, self.due = company, due
        self.sheet_id, _ = store.create_sheet("Internships", [company, due], keys=[company.id])
        sheet = store.read_sheet(self.sheet_id)
        sheet.rows.append(docs_store.Row("r-0000aaaa", {company.id: "Acme", due.id: "2026-12-01"}))
        store.save_sheet(self.sheet_id, sheet)
        self.doc_id, _ = store.create_doc("Job notes", "# Job notes\n")
        save_jobs(config, agents=[agent_data(lands={"kind": "sheet", "id": self.sheet_id, "name": "Internships"}),
                                  agent_data(id="a-0000000000b2", name="Notes agent",
                                             lands={"kind": "doc", "id": self.doc_id, "name": "Job notes"})])
        consent(config)

    def test_runs_taken_in_once_typed_and_deduplicated(self) -> None:
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, report="Three came in.",
                  lands={"kind": "sheet", "id": self.sheet_id},
                  rows=[["acme", "2026-12-05"], ["Globex", "soon"], ["Initech", "2026-12-09"], ["Acme", "2026-12-01"]])
        write_run(self.app.config, job_kind="agent", job_id="a-0000000000b2", job_name="Notes agent",
                  report="Pat moved the interview to Friday.", started=NOW - timedelta(minutes=5))
        self.s.reload()
        settle()
        sheet = self.docs.store.read_sheet(self.sheet_id)
        kinds = [item["kind"] for item in sheet.pending]
        self.assertEqual(kinds, ["cell", "row", "row"])                     # Acme's new date; Globex; Initech
        cell = sheet.pending[0]
        self.assertEqual((cell["row"], cell["value"], cell["was"]), ("r-0000aaaa", "2026-12-05", "2026-12-01"))
        self.assertEqual(sheet.pending[1]["cells"], {self.company.id: "Globex"})   # "soon" left empty
        self.assertEqual(sheet.pending[1]["by"], AGENT_NAME)
        self.assertEqual(len(sheet.rows), 1)                                # nothing kept without Keep
        doc = self.docs.store.read_doc(self.doc_id)
        self.assertEqual(len(doc.pending), 1)
        self.assertTrue(doc.pending[0]["text"].startswith("## "))
        self.assertIn("Notes agent", doc.pending[0]["text"])
        self.assertIn("Pat moved the interview to Friday.", doc.pending[0]["text"])
        self.assertEqual(doc.text, "# Job notes\n")
        self.s.reload()                                                     # taken in once
        self.assertEqual(len(self.docs.store.read_sheet(self.sheet_id).pending), 3)
        self.assertEqual(len(self.docs.store.read_doc(self.doc_id).pending), 1)
        self.assertEqual(self.c.window.reading.dock.docs_waiting(), 2)
        notes = self.s.agents_info().notes
        self.assertIn(("3 rows wait in Internships", standing_ui.DOCS_OPEN_TEXT,
                       f"{standing_ui.DOCS_LINK}{self.sheet_id}"), notes)
        self.s._on_link(f"{standing_ui.DOCS_LINK}{self.sheet_id}")
        settle()
        self.assertEqual(self.docs.current(), self.sheet_id)
        self.assertEqual(len(self.c.window.reading.docs_stage.sheet_editor.band.rows), 3)

    def test_his_typing_never_drops_rows_that_came_meanwhile(self) -> None:
        self.docs.open(self.sheet_id, screen=True)
        editor = self.c.window.reading.docs_stage.sheet_editor
        self.assertTrue(editor.model.setData(editor.model.index(0, 1), "2026-12-03"))   # typed, not saved yet
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, report="One came in.",
                  lands={"kind": "sheet", "id": self.sheet_id}, rows=[["Globex", "2026-12-09"]])
        self.s.reload()
        self.docs.flush()
        sheet = self.docs.store.read_sheet(self.sheet_id)
        self.assertEqual(sheet.rows[0].cells[self.due.id], "2026-12-03")          # his edit
        self.assertEqual([item["kind"] for item in sheet.pending], ["row"])        # ... and the agent's row waits

    def test_turn_on_makes_the_new_sheet(self) -> None:
        draft = model.agent_from(agent_data(id="a-0000000000c3", name="Watch", status="draft",
                                            lands={"kind": "sheet", "id": "", "name": "Hackathons",
                                                   "columns": COLUMNS}))
        Definitions(files_of(self.app.config)).save([*self.s.agents, draft], list(self.s.rules))
        self.s.reload()
        self.s._turn_on_problem = lambda job, edit=True: ""                 # its sign-ins are not what is tested
        self.s.turn_on("a-0000000000c3")
        on = next(agent for agent in self.s.agents if agent.id == "a-0000000000c3")
        self.assertEqual(on.status, model.STATUS_LIVE)
        kind, item_id = on.lands
        self.assertEqual(kind, "sheet")
        sheet = self.docs.store.read_sheet(item_id)
        self.assertEqual([c.name for c in sheet.columns], ["Company", "Role", "Deadline", "Status"])
        self.assertEqual(sheet.rows, [])
        self.assertEqual(sheet.keys, [sheet.columns[0].id])


class FixRoundTests(AppTests):
    """Fix round 1 (the review's findings 1-5, 8, 9): each fails without its fix."""

    test_runs_taken_in_once_typed_and_deduplicated = None  # type: ignore[assignment]
    test_his_typing_never_drops_rows_that_came_meanwhile = None  # type: ignore[assignment]
    test_turn_on_makes_the_new_sheet = None  # type: ignore[assignment]

    def land(self, rows, minutes: int = 10, report: str = "Rows.") -> str:  # type: ignore[no-untyped-def]
        return write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, report=report,
                         lands={"kind": "sheet", "id": self.sheet_id}, rows=rows,
                         started=NOW - timedelta(minutes=minutes))

    def test_undo_of_a_keep_keeps_what_arrived_since(self) -> None:       # finding 1, a sheet
        self.land([["Globex", "2026-12-09"]], minutes=20)
        self.s.reload()
        self.docs.open(self.sheet_id, screen=True)
        first = self.docs.store.read_sheet(self.sheet_id).pending
        self.docs.keep(str(first[0]["id"]))
        self.land([["Initech", "2026-12-10"], ["Hooli", ""]], minutes=5)
        self.s.reload()                                                     # two more rows wait now
        self.docs.undo_last()
        sheet = self.docs.store.read_sheet(self.sheet_id)
        self.assertEqual(len(sheet.rows), 1)                                # Globex's Keep undone ...
        self.assertEqual(sorted(item["cells"][self.company.id] for item in sheet.pending),
                         ["Globex", "Hooli", "Initech"])                    # ... and the new rows still wait

    def test_undo_in_a_document_keeps_an_agent_section(self) -> None:     # finding 1, a document
        store = self.docs.store
        store.set_doc_pending(self.doc_id, [proposals.doc_add("## Mine\n\nx")])
        self.docs.open(self.doc_id, screen=True)
        self.docs.keep(str(store.read_doc(self.doc_id).pending[0]["id"]))
        write_run(self.app.config, job_kind="agent", job_id="a-0000000000b2", job_name="Notes agent",
                  report="Pat moved the interview.", lands={"kind": "doc", "id": self.doc_id})
        self.s.reload()
        self.docs.undo_last()
        doc = store.read_doc(self.doc_id)
        self.assertEqual(doc.text, "# Job notes\n")
        self.assertEqual([item["text"].split("\n")[0] for item in doc.pending][0], "## Mine")
        self.assertTrue(any("Pat moved the interview." in item["text"] for item in doc.pending))

    def test_a_blank_checkbox_cell_never_unticks(self) -> None:          # finding 2
        store = self.docs.store
        done, _ = docs_model.check_column("Applied", "checkbox")
        sheet = store.read_sheet(self.sheet_id)
        sheet.columns.append(done)
        sheet.rows[0].cells[done.id] = True
        store.save_sheet(self.sheet_id, sheet)
        self.land([["Acme", "2026-12-01", ""], ["Globex", "", ""]])
        self.s.reload()
        pending = store.read_sheet(self.sheet_id).pending
        self.assertEqual([item["kind"] for item in pending], ["row"])      # no "untick Applied" for Acme
        self.assertNotIn(done.id, pending[0]["cells"])

    def test_a_cell_being_typed_survives_rows_arriving(self) -> None:    # finding 3
        from PySide6.QtWidgets import QLineEdit

        self.docs.open(self.sheet_id, screen=True)
        self.c.window.show()
        settle()
        editor = self.c.window.reading.docs_stage.sheet_editor
        index = editor.model.index(0, 0)
        editor.table.setCurrentIndex(index)
        editor.table.edit(index)
        settle()
        line = next(w for w in editor.table.findChildren(QLineEdit) if w.isVisible())
        line.setText("Acme Corporation")                                   # typed, no Enter yet
        self.land([["Globex", "2026-12-09"]])
        self.s.reload()
        sheet = self.docs.store.read_sheet(self.sheet_id)
        self.assertEqual(sheet.rows[0].cells[self.company.id], "Acme Corporation")
        self.assertEqual(len(sheet.pending), 1)

    def test_a_cell_changed_since_is_marked_and_kept_only_on_its_own(self) -> None:   # finding 4, a sheet
        from briefing_reader import docs_view
        from briefing_reader.ask import docs_mode, modes

        store = self.docs.store
        sheet = store.read_sheet(self.sheet_id)
        job = modes.DocJob(self.sheet_id, "sheet", "Internships", sheet.copy(), "x")
        answer = docs_mode.DocAnswer("ok", "", None, {"title": "", "columns": [], "add_rows": [["Globex", ""]],
                                                      "set_cells": [{"row": "r-0000aaaa", "column": "Deadline",
                                                                     "value": "2026-12-15"}]})
        proposal, reason = docs_mode.check_doc_answer(answer, job, "move Acme's deadline")
        self.assertEqual(reason, "")
        sheet.rows[0].cells[self.due.id] = "2026-12-20"                    # he changed it meanwhile
        store.save_sheet(self.sheet_id, sheet)
        self.docs.on_outcome(SimpleNamespace(target=self.sheet_id, ok=True, doc_proposal=proposal))
        self.docs.open(self.sheet_id, screen=True)
        band = self.c.window.reading.docs_stage.sheet_editor.band
        marks = {info.text: info.changed for info, _widget in band.rows}
        self.assertEqual(sorted(marks.values()), [False, True])
        self.assertTrue(any(isinstance(w, QLabel) and w.text() == docs_view.CHANGED_WORDS
                            for _info, row in band.rows for w in row.findChildren(QLabel)))
        self.docs.keep_all(self.sheet_id)
        after = store.read_sheet(self.sheet_id)
        self.assertEqual(after.rows[0].cells[self.due.id], "2026-12-20")    # Keep all left it
        self.assertEqual([item["kind"] for item in after.pending], ["cell"])
        self.docs.keep(str(after.pending[0]["id"]))                         # its own Keep takes it
        self.assertEqual(store.read_sheet(self.sheet_id).rows[0].cells[self.due.id], "2026-12-15")

    def test_a_section_changed_since_is_marked(self) -> None:            # finding 4, a document
        from briefing_reader import docs_view
        from briefing_reader.ask import docs_mode, modes

        store = self.docs.store
        store.save_doc(self.doc_id, "# Job notes\n\n## Plan\n\napply\n")
        job = modes.DocJob(self.doc_id, "doc", "Job notes", store.read_doc(self.doc_id).text, "x")
        answer = docs_mode.DocAnswer("ok", "", {"title": "", "changes": [
            {"op": "replace", "heading": "Plan", "after": "", "text": "## Plan\n\napply twice"}]}, None)
        proposal, _ = docs_mode.check_doc_answer(answer, job, "change the plan")
        store.save_doc(self.doc_id, "# Job notes\n\n## Plan\n\napply on Monday\n")   # he edited it meanwhile
        self.docs.on_outcome(SimpleNamespace(target=self.doc_id, ok=True, doc_proposal=proposal))
        self.docs.open(self.doc_id, screen=True)
        tile = self.c.window.reading.docs_stage.doc_editor.proposals_box.tiles[0]
        self.assertTrue(tile.info.changed)
        self.assertEqual(tile.stale_label.text(), docs_view.CHANGED_TEXT)
        self.assertFalse(tile.keep_button.isHidden())
        self.docs.keep_all(self.doc_id)
        self.assertIn("apply on Monday", store.read_doc(self.doc_id).text)
        self.docs.keep(tile.info.pending_id)
        self.assertIn("apply twice", store.read_doc(self.doc_id).text)

    def test_a_run_is_marked_taken_in_only_once_stored(self) -> None:    # finding 5
        path = self.app.config.data_dir / "docs" / self.sheet_id / "sheet.json"
        good = path.read_bytes()
        path.write_bytes(good[:-20])
        run_id = self.land([["Globex", "2026-12-09"]])
        self.s.reload()
        self.assertNotIn(run_id, self.docs.store.imported())
        path.write_bytes(good)
        self.s.reload()
        self.assertIn(run_id, self.docs.store.imported())
        self.assertEqual(len(self.docs.store.read_sheet(self.sheet_id).pending), 1)

    def test_an_agent_report_loses_hidden_characters(self) -> None:      # finding 8
        write_run(self.app.config, job_kind="agent", job_id="a-0000000000b2", job_name="Notes agent",
                  report="Pay \u202eolleh\u202c today\u200b.", lands={"kind": "doc", "id": self.doc_id})
        self.s.reload()
        text = self.docs.store.read_doc(self.doc_id).pending[0]["text"]
        for char in ("\u202e", "\u202c", "\u200b"):
            self.assertNotIn(char, text)
        self.assertIn("Pay olleh today.", text)

    def test_a_keep_past_the_cap_says_why(self) -> None:                # finding 9
        store = self.docs.store
        sheet = store.read_sheet(self.sheet_id)
        sheet.rows = [docs_store.Row(docs_model.new_row_id(), {self.company.id: f"Co {n}"})
                      for n in range(docs_model.MAX_ROWS)]
        store.save_sheet(self.sheet_id, sheet)
        self.land([["Newco", "2026-12-09"]])
        self.s.reload()
        self.docs.open(self.sheet_id, screen=True)
        pending = store.read_sheet(self.sheet_id).pending
        self.docs.keep(str(pending[0]["id"]))
        editor = self.c.window.reading.docs_stage.sheet_editor
        self.assertEqual(editor.notice.text(), docs_store.REFUSAL_WORDS[docs_store.R_TOO_LARGE])
        self.assertEqual(len(store.read_sheet(self.sheet_id).pending), 1)   # it still waits


if __name__ == "__main__":
    unittest.main()
