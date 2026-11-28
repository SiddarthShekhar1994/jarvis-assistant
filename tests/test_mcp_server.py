"""Other AI apps reading the board (mcp_server, ``--mcp``): the stdio protocol over in-memory pipes, off by default,
each read-only tool on fake stores, the size caps, hostile arguments, no tool that sends or changes anything, no
network, nothing written to the data folder, and logs without content. Every name, address, id and text is invented."""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from briefing_reader import mcp_server
from briefing_reader.actions import STATUS_DENIED, ActionStore, parse_action_line
from briefing_reader.capture import CapturedItem, to_card
from briefing_reader.capture_store import CaptureStore, new_capture_id
from briefing_reader.config import load_config
from briefing_reader.conversation import CONVERSATION_FILE, ConversationStore, Turn
from briefing_reader.docs import model as docs_model
from briefing_reader.docs.store import DocsStore, Row
from briefing_reader.mcp_server import BoardReader, McpServer, serve
from briefing_reader.standing import cards
from briefing_reader.standing.model import agent_from, rule_from
from briefing_reader.standing.store import AppState, Definitions, RunFiles, StandingFiles, new_run_id, stamp

PDT = timezone(timedelta(hours=-7))
NOW = datetime(2026, 10, 4, 13, 52, tzinfo=PDT)
RULE_ID = "r-0000000000a1"
AGENT_ID = "a-0000000000b1"
PAT = "pat.example@example.org"
THREAD, GMID, MSGID = "thr00000a1", "msg00000a1", "invoice-1043@example.org"
SUBJECT = "Invoice 1043 from Example Billing"
BODY = "Thanks Pat, the venue works. BODY-SECRET-7731"
REPORT = "Two mails from Pat: the venue is booked. REPORT-SECRET-5512"
SENTENCE = "When Example Billing sends an invoice, make me a to-do to pay it"
DOC_TEXT = "# Trip notes\n\nPack the charger. DOC-SECRET-0091\n"
OWNER_SAID = "what is waiting for me OWNER-SECRET-4410"
RESEARCH_SAID = "web: best tents RESEARCH-SECRET-2207"
SECRETS = ("BODY-SECRET", "REPORT-SECRET", "DOC-SECRET", "OWNER-SECRET", "Pay $40.00", "Lecture", PAT, "Trip notes",
           "Invoice watch", "Morning mail", "Packing list", "term.ics", SENTENCE)


def rule_data() -> dict:
    return {"id": RULE_ID, "name": "Invoice watch", "sentence": SENTENCE, "account": "work",
            "query": "from:billing@example.org subject:invoice", "question": "Is this an invoice to pay?",
            "fields": [{"name": "amount", "type": "money", "hint": "the amount due"}],
            "action": {"kind": "todo", "title": "Pay {amount}"}, "status": "live",
            "dry_run": {"at": "2026-10-03T10:00:00-07:00", "matched": 12, "window_days": 30, "judged": 12, "yes": 3},
            "created": "2026-10-03T10:00:00-07:00"}


def agent_data() -> dict:
    return {"id": AGENT_ID, "name": "Morning mail", "words": "every morning tell me what Pat sent and draft replies",
            "trigger": {"kind": "time", "times": ["07:30"], "weekdays": []},
            "mail": [{"account": "work", "query": f"from:{PAT}"}], "proposes": ["todo", "reply"],
            "report": "every_run", "status": "live", "created": "2026-10-03T10:00:00-07:00"}


def todo_record(key: str = GMID, title: str = "Pay $40.00") -> dict:
    line = cards.todo_line(title, "2026-10-08", account="work")
    card = cards.with_job(parse_action_line(line), job_kind="rule", job_id=RULE_ID, key=key)
    return cards.record(card, line=line, job={"kind": "rule", "id": RULE_ID}, key=key, source={})


def reply_record() -> dict:
    line = cards.reply_line("work", THREAD, MSGID, GMID, [PAT], SUBJECT, BODY)
    card = cards.with_job(parse_action_line(line), job_kind="agent", job_id=AGENT_ID, key=GMID)
    return cards.record(card, line=line, job={"kind": "agent", "id": AGENT_ID}, key=GMID, source={})


class Board:
    """A data folder with every store filled: two standing jobs and their runs, a capture, a document and a sheet, the
    conversation, one decided card and one withdrawn card."""

    def __init__(self, root: Path) -> None:
        self.root = root
        files = StandingFiles(root)
        Definitions(files).save([agent_from(agent_data())], [rule_from(rule_data())])
        self.todo = todo_record()
        self.decided = todo_record(key="msg00000a2", title="Pay $55.00")
        self.withdrawn = todo_record(key="msg00000a3", title="Pay $66.00")
        self.reply = reply_record()
        self.write_run("rule", RULE_ID, "Invoice watch", [self.todo, self.decided, self.withdrawn],
                       NOW - timedelta(minutes=30))
        self.write_run("agent", AGENT_ID, "Morning mail", [self.reply], NOW - timedelta(minutes=10), report=REPORT)
        self.write_run("agent", "a-00000000dead", "Gone job", [todo_record(key="msg00000a9", title="Gone")],
                       NOW - timedelta(minutes=5))
        ActionStore(root / mcp_server.ACTIONS_FILE).set(self.decided["id"], STATUS_DENIED, kind="todo")
        AppState(files).mark("withdrawn", [self.withdrawn["id"]], NOW)
        store = CaptureStore(root, clock=lambda: NOW)
        self.capture_id = new_capture_id()
        lecture = CapturedItem("event", "Lecture 1", start=datetime(2026, 12, 1, 9, 0), end=datetime(2026, 12, 1, 10, 0))
        essay = CapturedItem("todo", "Essay draft", due=date(2026, 10, 20))
        store.add(self.capture_id, label="term.ics", kind="ics", counts={"found": 2},
                  cards=[(lecture, to_card(lecture, "term.ics", today=NOW.date())),
                         (essay, to_card(essay, "term.ics", today=NOW.date()))])
        docs = DocsStore(root)
        self.doc_id, _ = docs.create_doc("Trip notes", DOC_TEXT)
        columns = [docs_model.Column("c-00000a", "Item"), docs_model.Column("c-00000b", "Packed",
                                                                            docs_model.TYPE_CHECKBOX)]
        self.sheet_id, _ = docs.create_sheet("Packing list", columns)
        sheet = docs.read_sheet(self.sheet_id)
        sheet.rows = [Row(f"r-{n:08x}", {"c-00000a": f"Thing {n}", "c-00000b": n % 2 == 0}) for n in range(3)]
        docs.save_sheet(self.sheet_id, sheet)
        ConversationStore(root / CONVERSATION_FILE, clock=lambda: NOW).save([
            Turn("you", OWNER_SAID, NOW - timedelta(minutes=3)),
            Turn("jarvis", "Two cards wait, sir.", NOW - timedelta(minutes=2), "done"),
            Turn("you", RESEARCH_SAID, NOW - timedelta(minutes=1), context=False),
            Turn("jarvis", "A note for Ask only", NOW - timedelta(seconds=30), note=True)])

    def write_run(self, kind: str, job_id: str, name: str, run_cards: list, started: datetime, report: str = "") -> None:
        record = {"id": new_run_id(), "job": {"kind": kind, "id": job_id, "name": name}, "trigger": "mail",
                  "origin": "task", "started": stamp(started), "ended": stamp(started + timedelta(seconds=40)),
                  "status": "ok", "cards": run_cards, "receipts": [], "needs_owner": True, "counts": {"cards": 1}}
        if report:
            record.update(report=report, summary="Two mails from Pat")
        assert RunFiles(StandingFiles(self.root)).write(record, started) is not None

    def snapshot(self) -> dict[str, bytes]:
        return {str(path.relative_to(self.root)): path.read_bytes() for path in sorted(self.root.rglob("*"))
                if path.is_file()}


class Session:
    """Lines in, lines out: what a client sends over stdin and gets back over stdout."""

    def __init__(self, server: McpServer) -> None:
        self.server = server
        self.next_id = 0

    def exchange(self, *messages: object, raw: bytes = b"") -> list[dict]:
        lines = b"".join(json.dumps(message).encode("utf-8") + b"\n" for message in messages) + raw
        out = io.BytesIO()
        self.assert_ok(serve(io.BytesIO(lines), out, self.server))
        text = out.getvalue()
        assert b"\r" not in text
        return [json.loads(line) for line in text.split(b"\n") if line]

    @staticmethod
    def assert_ok(code: int) -> None:
        assert code == 0

    def call(self, name: str, arguments: object = None) -> dict:
        self.next_id += 1
        params: dict = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        (answer,) = self.exchange({"jsonrpc": "2.0", "id": self.next_id, "method": "tools/call", "params": params})
        return answer


def payload(answer: dict) -> dict:
    result = answer["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


class BoardCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.board = Board(self.root)
        self.reader = BoardReader(self.root, aliases=("personal", "work"), clock=lambda: NOW)
        self.session = Session(McpServer(self.reader, enabled=True))

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ProtocolTests(BoardCase):
    def test_round_trip_over_pipes(self) -> None:
        answers = self.session.exchange(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "two", "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_todos", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 6, "result": {}},
            raw=b"\n{not json\n[1, 2]\n")
        self.assertEqual([answer.get("id") for answer in answers], [1, "two", 3, 4, 5, None, None])
        init = answers[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-03-26")
        self.assertEqual(init["serverInfo"]["name"], "jarvis")
        self.assertEqual(init["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(len(answers[1]["result"]["tools"]), len(mcp_server.TOOLS))
        self.assertEqual(answers[2]["result"], {})
        self.assertEqual(payload(answers[3])["count"], 2)              # the rule's to-do and the capture's
        self.assertEqual(answers[4]["error"]["code"], -32601)
        self.assertEqual(answers[5]["error"]["code"], -32700)
        self.assertEqual(answers[6]["error"]["code"], -32600)
        self.assertTrue(all(answer["jsonrpc"] == "2.0" for answer in answers))

    def test_protocol_version_negotiated(self) -> None:
        for wanted, given in (("2024-11-05", "2024-11-05"), ("2025-06-18", "2025-06-18"), ("1999-01-01", "2025-06-18"),
                              (None, "2025-06-18"), (12, "2025-06-18")):
            (answer,) = self.session.exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                               "params": {"protocolVersion": wanted}})
            self.assertEqual(answer["result"]["protocolVersion"], given)

    def test_end_of_input_and_a_gone_client_end_quietly(self) -> None:
        self.assertEqual(serve(io.BytesIO(b""), io.BytesIO(), self.session.server), 0)

        class Gone(io.BytesIO):
            def write(self, _data: bytes) -> int:
                raise BrokenPipeError

        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n"
        self.assertEqual(serve(io.BytesIO(line * 3), Gone(), self.session.server), 0)

    def test_a_line_too_long_is_refused_unread_and_the_next_one_answered(self) -> None:
        with mock.patch.object(mcp_server, "MAX_LINE_BYTES", 64):
            long_line = b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "' + b"x" * 300 + b'"}\n'
            answers = self.session.exchange({"jsonrpc": "2.0", "id": 2, "method": "ping"}, raw=long_line)
        self.assertEqual(answers[0], {"jsonrpc": "2.0", "id": 2, "result": {}})
        self.assertEqual(answers[1]["error"]["code"], -32600)
        self.assertIsNone(answers[1]["id"])

    def test_answers_are_ascii_lines(self) -> None:
        out = io.BytesIO()
        serve(io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "read_recent_conversation"}}).encode() + b"\n"), out,
              self.session.server)
        out.getvalue().decode("ascii")
        self.assertEqual(out.getvalue().count(b"\n"), 1)


class OffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def config(self, toml: str):
        (self.root / "config.toml").write_text(toml, encoding="utf-8")
        return load_config(self.root, environ={"LOCALAPPDATA": str(self.root / "local")})

    def test_off_by_default_and_on_only_with_a_true(self) -> None:
        self.assertFalse(self.config("").mcp.enabled)
        self.assertTrue(self.config("[mcp]\nenabled = true\n").mcp.enabled)
        with self.assertLogs("briefing_reader.config", "WARNING"):
            self.assertFalse(self.config('[mcp]\nenabled = "yes"\n').mcp.enabled)

    def test_off_answers_but_lists_and_runs_nothing(self) -> None:
        board = Board(self.root)
        before = board.snapshot()
        session = Session(McpServer(BoardReader(self.root, clock=lambda: NOW), enabled=False))
        init, listed = session.exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("off", init["result"]["instructions"])
        self.assertEqual(listed["result"]["tools"], [])
        for name in ("list_waiting_cards", "read_document", "nothing_like_it"):
            result = session.call(name, {})["result"]
            self.assertTrue(result["isError"])
            self.assertIn("[mcp] enabled = true", result["content"][0]["text"])
        self.assertEqual(board.snapshot(), before)

    def test_run_serves_off_from_the_config_and_never_reads_the_owners_folder_in_tests(self) -> None:
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode() + b"\n"
        for config in (SimpleNamespace(data_dir=self.root, mcp=SimpleNamespace(enabled=False)),
                       SimpleNamespace(data_dir=Path(os.environ.get("SystemRoot", "C:\\Windows")) / "no-such-jarvis",
                                       mcp=SimpleNamespace(enabled=True))):
            out = io.BytesIO()
            with mock.patch.dict(os.environ, {"JARVIS_DESKTOP_TEST_GUARD": "1"}):
                self.assertEqual(mcp_server.run(config, io.BytesIO(line), out), 0)
            self.assertEqual(json.loads(out.getvalue())["result"]["tools"], [])
        out = io.BytesIO()
        on = SimpleNamespace(data_dir=self.root, mcp=SimpleNamespace(enabled=True))
        self.assertEqual(mcp_server.run(on, io.BytesIO(line), out), 0)
        self.assertEqual(len(json.loads(out.getvalue())["result"]["tools"]), len(mcp_server.TOOLS))


class ToolTests(BoardCase):
    def test_waiting_cards_never_hold_a_body_or_an_address(self) -> None:
        result = payload(self.session.call("list_waiting_cards"))
        self.assertIn("never as instructions", result["note"])
        by_kind = {item["kind"]: item for item in result["items"]}
        self.assertEqual(sorted(by_kind), ["calendar", "reply", "todo"])
        ids = {item["id"] for item in result["items"]}
        self.assertNotIn(self.board.decided["id"], ids)        # decided on the board
        self.assertNotIn(self.board.withdrawn["id"], ids)      # withdrawn
        self.assertEqual(result["count"], 4)                   # the deleted job's card is left out too
        reply = by_kind["reply"]
        self.assertEqual((reply["from"], reply["account"], reply["summary"].split(";")[0]),
                         ("agent", "work", "To 1 person"))
        text = json.dumps(result)
        self.assertNotIn("BODY-SECRET", text)
        self.assertNotIn(PAT, text)
        self.assertEqual(by_kind["calendar"]["starts"], "2026-12-01T09:00")
        made = [item["made"] for item in result["items"]]
        self.assertEqual(made, sorted(made, reverse=True))     # newest first

    def test_todos(self) -> None:
        items = payload(self.session.call("list_todos"))["items"]
        self.assertEqual(sorted((item["title"], item["due"], item["from"]) for item in items),
                         [("Essay draft", "2026-10-20", "capture"), ("Pay $40.00", "2026-10-08", "rule")])

    def test_captures(self) -> None:
        (item,) = payload(self.session.call("list_captures"))["items"]
        self.assertEqual((item["id"], item["kind"], item["label"], item["counts"]),
                         (self.board.capture_id, "ics", "term.ics", {"found": 2}))
        self.assertEqual([(card["title"], card["waits"]) for card in item["cards"]],
                         [("Lecture 1", True), ("Essay draft", True)])

    def test_documents_and_reading_them(self) -> None:
        items = payload(self.session.call("list_documents"))["items"]
        self.assertEqual(sorted((item["kind"], item["name"]) for item in items),
                         [("doc", "Trip notes"), ("sheet", "Packing list")])
        doc = payload(self.session.call("read_document", {"id": self.board.doc_id}))
        self.assertEqual(doc["text"], DOC_TEXT)
        self.assertNotIn("next_offset", doc)
        sheet = payload(self.session.call("read_document", {"id": self.board.sheet_id}))
        self.assertEqual(sheet["columns"], ["Item", "Packed"])
        self.assertEqual(sheet["rows"], [["Thing 0", "yes"], ["Thing 1", "no"], ["Thing 2", "yes"]])
        self.assertEqual(payload(self.session.call("read_document", {"id": self.board.sheet_id, "offset": 2}))["rows"],
                         [["Thing 2", "yes"]])

    def test_a_document_changed_outside_jarvis_is_read_without_a_version(self) -> None:
        (self.root / "docs" / self.board.doc_id / "doc.md").write_text("Changed by hand\n", encoding="utf-8")
        before = self.board.snapshot()
        self.assertEqual(payload(self.session.call("read_document", {"id": self.board.doc_id}))["text"],
                         "Changed by hand\n")
        self.assertEqual(self.board.snapshot(), before)

    def test_agents_and_rules_with_their_last_run(self) -> None:
        items = {item["id"]: item for item in payload(self.session.call("list_agents_and_rules"))["items"]}
        self.assertEqual(sorted(items), [AGENT_ID, RULE_ID])
        self.assertEqual((items[RULE_ID]["kind"], items[RULE_ID]["does"], items[RULE_ID]["trigger"]),
                         ("rule", "todo", "mail"))
        self.assertEqual(items[AGENT_ID]["trigger"], "time")
        self.assertEqual(items[AGENT_ID]["last_run"]["summary"], "Two mails from Pat")
        self.assertEqual(items[RULE_ID]["accounts"], ["work"])

    def test_reports(self) -> None:
        result = payload(self.session.call("read_agent_reports", {"limit": 3}))
        (report,) = result["items"]
        self.assertEqual((report["job"], report["report"]), ("Morning mail", REPORT))
        self.assertIn("mail", result["written_by"])

    def test_conversation_leaves_out_web_research_and_notes(self) -> None:
        items = payload(self.session.call("read_recent_conversation"))["items"]
        self.assertEqual([(item["who"], item["text"]) for item in items],
                         [("owner", OWNER_SAID), ("jarvis", "Two cards wait, sir.")])
        self.assertEqual(len(payload(self.session.call("read_recent_conversation", {"limit": 1}))["items"]), 1)

    def test_empty_folder_gives_empty_lists(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            session = Session(McpServer(BoardReader(empty, clock=lambda: NOW), enabled=True))
            for tool in mcp_server.TOOLS:
                if tool.name != "read_document":
                    self.assertEqual(payload(session.call(tool.name))["items"], [], tool.name)
            self.assertEqual(os.listdir(empty), [])           # nothing made, not even a folder

    def test_damaged_files_give_empty_lists_not_a_dead_server(self) -> None:
        for name in ("captures.json", "standing.json", "conversation.json", "actions.json", "docs/index.json"):
            (self.root / name).write_bytes(b"\x00{damaged")
        with self.assertLogs("briefing_reader", "WARNING"):
            for tool in mcp_server.TOOLS:
                if tool.name != "read_document":
                    self.assertIn("items", payload(self.session.call(tool.name)), tool.name)


class CapTests(BoardCase):
    def test_a_long_document_comes_a_page_at_a_time(self) -> None:
        text = "".join(f"Line {n} of the long notes.\n" for n in range(3000))
        doc_id, _ = DocsStore(self.root).create_doc("Long notes", text)
        first = payload(self.session.call("read_document", {"id": doc_id}))
        self.assertEqual((len(first["text"]), first["next_offset"], first["characters"]),
                         (mcp_server.PAGE_CHARS, mcp_server.PAGE_CHARS, len(text)))
        pages, offset = [first["text"]], first["next_offset"]
        while offset is not None:
            page = payload(self.session.call("read_document", {"id": doc_id, "offset": offset}))
            pages.append(page["text"])
            offset = page.get("next_offset")
        self.assertEqual("".join(pages), text)

    def test_every_result_stays_under_the_cap(self) -> None:
        store = CaptureStore(self.root, clock=lambda: NOW)
        for n in range(60):
            items = [CapturedItem("todo", f"Task {n}-{k} " + "w" * 150, due=date(2026, 10, 20)) for k in range(20)]
            store.add(new_capture_id(), label="L" * 200, kind="text", cards=[(item, to_card(item, "x", today=NOW.date()))
                                                                             for item in items])
        for n in range(30):
            self.board.write_run("agent", AGENT_ID, "Morning mail", [], NOW - timedelta(minutes=40 + n),
                                 report="R" * 9000)
        with mock.patch.object(mcp_server, "RESULT_CAP", 8000):
            for tool in mcp_server.TOOLS:
                args = {"id": self.board.sheet_id} if tool.name == "read_document" else {}
                text = self.session.call(tool.name, args)["result"]["content"][0]["text"]
                self.assertLessEqual(len(text), 8000, tool.name)
                json.loads(text)
            self.assertGreater(payload(self.session.call("list_captures"))["left_out"], 0)
            self.assertGreater(payload(self.session.call("read_agent_reports"))["left_out"], 0)
        for tool in mcp_server.TOOLS:
            args = {"id": self.board.sheet_id} if tool.name == "read_document" else {}
            self.assertLessEqual(len(self.session.call(tool.name, args)["result"]["content"][0]["text"]),
                                 mcp_server.RESULT_CAP, tool.name)
        reports = payload(self.session.call("read_agent_reports", {"limit": 20}))["items"]
        self.assertTrue(all(len(item["report"]) <= mcp_server.REPORT_CAP for item in reports))

    def test_fit_keeps_valid_json(self) -> None:
        big = {"note": "n", "items": [{"t": "x" * 1000} for _ in range(200)]}
        fitted = json.loads(mcp_server._fit(big))
        self.assertLess(len(fitted["items"]), 200)
        self.assertEqual(len(fitted["items"]) + fitted["left_out"], 200)
        self.assertTrue(json.loads(mcp_server._fit({"text": "y" * 100_000}))["too_large"])


class HostileTests(BoardCase):
    def error_text(self, name: str, arguments: object) -> str:
        result = self.session.call(name, arguments)["result"]
        self.assertTrue(result.get("isError"), (name, arguments))
        return result["content"][0]["text"]

    def test_bad_arguments_are_refused_in_jarvis_words(self) -> None:
        cases = [("read_document", {}), ("read_document", {"id": 5}), ("read_document", {"id": "../../.env"}),
                 ("read_document", {"id": "d-" + "0" * 12}), ("read_document", {"id": "x" * 5000}),
                 ("read_document", {"id": self.board.doc_id, "offset": -1}),
                 ("read_document", {"id": self.board.doc_id, "offset": True}),
                 ("read_document", {"id": self.board.doc_id, "extra": 1}),
                 ("read_agent_reports", {"limit": 0}), ("read_agent_reports", {"limit": 10**9}),
                 ("read_agent_reports", {"limit": "5"}), ("read_recent_conversation", {"limit": 41}),
                 ("list_waiting_cards", {"path": "C:\\"}), ("list_todos", ["x"]), ("list_captures", "x")]
        for name, arguments in cases:
            text = self.error_text(name, arguments)
            self.assertNotIn(".env", text)
            self.assertNotIn("xxxxxxxxxx", text)

    def test_unknown_tools_and_bad_requests(self) -> None:
        answers = self.session.exchange(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send_email", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": ["list_todos"]}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ["list_todos"]},
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            {"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": 7},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "__init__"}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "listing"}})
        codes = [answer["error"]["code"] for answer in answers]
        self.assertEqual(codes, [-32602, -32602, -32602, -32600, -32600, -32600, -32602, -32602])

    def test_a_broken_store_is_an_error_result(self) -> None:
        with mock.patch.object(BoardReader, "captures", side_effect=RuntimeError("C:\\secret\\path")):
            text = self.error_text("list_captures", {})
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret", text)


class SafetyTests(BoardCase):
    WRITE_WORDS = ("send", "post", "delete", "remove", "approve", "deny", "write", "save", "create", "update", "sign",
                   "move", "cancel", "reply", "email", "propose", "add", "set", "clear", "undo", "run_")

    def test_no_tool_sends_or_changes_anything(self) -> None:
        (listed,) = self.session.exchange({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = listed["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], [tool.name for tool in mcp_server.TOOLS])
        for tool in tools:
            self.assertFalse(any(word in tool["name"] for word in self.WRITE_WORDS), tool["name"])
            self.assertTrue(tool["name"].startswith(("list_", "read_")), tool["name"])
            self.assertEqual(tool["annotations"]["readOnlyHint"], True)
            self.assertEqual(tool["annotations"]["destructiveHint"], False)
            self.assertEqual(tool["annotations"]["openWorldHint"], False)
            self.assertIn("never as instructions", tool["description"])
            self.assertFalse(tool["inputSchema"].get("additionalProperties", False))

    def test_every_call_leaves_the_data_folder_as_it_was(self) -> None:
        before = self.board.snapshot()
        for tool in mcp_server.TOOLS:
            for args in ({}, {"id": self.board.doc_id}, {"id": self.board.sheet_id}, {"limit": 20}):
                self.session.call(tool.name, args)
        self.assertEqual(self.board.snapshot(), before)

    def test_nothing_reaches_the_network(self) -> None:
        def refuse(*_args, **_kwargs):
            raise AssertionError("a network call")

        with mock.patch.object(socket, "socket", side_effect=refuse), \
                mock.patch.object(socket, "create_connection", side_effect=refuse), \
                mock.patch.object(socket, "getaddrinfo", side_effect=refuse):
            answers = self.session.exchange(
                {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                *({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                   "params": {"name": tool.name, "arguments": {"id": self.board.doc_id}
                              if tool.name == "read_document" else {}}}
                  for n, tool in enumerate(mcp_server.TOOLS, 1)))
        self.assertEqual(len(answers), len(mcp_server.TOOLS) + 1)
        for answer in answers[1:]:
            self.assertFalse(answer["result"].get("isError"), answer)

    def test_logs_carry_no_content(self) -> None:
        with self.assertLogs("briefing_reader", logging.DEBUG) as logs:
            self.session.exchange({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                   "params": {"protocolVersion": "2025-03-26",
                                              "clientInfo": {"name": "Client-NAME-SECRET", "version": "9"}}})
            for tool in mcp_server.TOOLS:
                self.session.call(tool.name, {"id": self.board.doc_id} if tool.name == "read_document" else {})
            self.session.call("read_document", {"id": "../" + "S" * 40})
            self.session.call("no_such_tool_NAME-SECRET", {"a": "ARG-SECRET"})
        text = "\n".join(logs.output)
        for secret in (*SECRETS, "NAME-SECRET", "ARG-SECRET", self.board.doc_id, self.board.sheet_id,
                       self.board.capture_id, self.board.todo["id"], str(self.root)):
            self.assertNotIn(secret, text)
        self.assertIn("MCP: list_waiting_cards ok, 4 item(s)", text)
        self.assertIn("MCP: unknown refused", text)

    def test_the_mode_imports_no_qt_and_no_network_library(self) -> None:
        code = ("import sys, briefing_reader.mcp_server; "
                "print(sorted({m.split('.')[0] for m in sys.modules} & {'PySide6', 'requests', 'urllib3', 'google', "
                "'httpx', 'edge_tts'}))")
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120,
                             cwd=str(Path(__file__).resolve().parent.parent),
                             env={**os.environ, "JARVIS_DESKTOP_TEST_GUARD": "1"})
        self.assertEqual(out.stdout.strip(), "[]", out.stderr[-500:])


class MainTests(unittest.TestCase):
    def test_the_mode_is_a_command_line_option_alone(self) -> None:
        from briefing_reader import __main__ as entry

        self.assertTrue(entry.build_parser().parse_args(["--mcp"]).mcp)
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            entry.main(["--mcp", "--open"])
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            entry.main(["--mcp", "--standing-run"])

    def test_main_serves_without_reading_dotenv_or_taking_the_lock(self) -> None:
        from briefing_reader import __main__ as entry

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp, "JARVIS_DESKTOP_TEST_GUARD": "1"}), \
                mock.patch("briefing_reader.config.setup_logging", return_value=Path("x.log")), \
                mock.patch("briefing_reader.config._load_dotenv") as dotenv, \
                mock.patch.object(entry, "_claim_single_instance") as lock, \
                mock.patch.object(mcp_server, "run", return_value=0) as run:
            self.assertEqual(entry.main(["--mcp"]), 0)
        dotenv.assert_not_called()
        lock.assert_not_called()
        (config,), _kwargs = run.call_args
        self.assertFalse(config.mcp.enabled)


class SettingsRowTests(unittest.TestCase):
    def test_live_and_privacy_says_whether_other_apps_read_the_board(self) -> None:
        from briefing_reader import overrides, settings_view

        off = dict(settings_view.settings_rows(SimpleNamespace(mcp=SimpleNamespace(enabled=False)), "live"))
        on = dict(settings_view.settings_rows(SimpleNamespace(mcp=SimpleNamespace(enabled=True)), "live"))
        self.assertEqual(off[settings_view.MCP_LABEL], settings_view.MCP_OFF)
        self.assertIn("can't send", on[settings_view.MCP_LABEL])
        self.assertIn("mcp", settings_view.GROUP_TABLES["live"])
        self.assertNotIn("mcp.enabled", overrides.EDITABLE)    # never a Change: config.toml only


if __name__ == "__main__":
    unittest.main()
