"""Tests for a standing rule's tidy-up at Google (part 9 of the build).

A "yes" may star, label or archive the ONE message a rule matched, without a click, only when every gate passes: the
rule's own holds (orders in the batch, a yes rate far above its dry run's, a failed DMARC check, the owner's own or sent
mail, starred or important mail for an archive), Pause all read again, gmail_tidy set up and its own sign-in still the
one saved and still the Google account Turn on recorded, the tidy-up's own consent, a filter naming a sender and a dry run
of at most 90 matches, never archiving mail from someone the owner has written to, and the rule's tidy-ups a day. Each
change is journalled before the call ("doing", then "done" or "failed"; a crash between reads "unknown"). The app's
Undo is exact: only Jarvis's change, only while the labels are as he left them, for 7 days. Gmail, Google sign-ins and
Claude Code are fakes; everything is in a temporary folder.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PySide6.QtWidgets import QApplication

from briefing_reader import standing_ui
from briefing_reader.config import AskConfig, StandingConfig
from briefing_reader.standing import model, runner, tidy
from briefing_reader.standing.store import (AppState, Definitions, RunFiles, RunnerState, StandingFiles, Usage,
                                            stamp)
from tests.standing_fakes import GMID, STANDING_ON, consent, files_of, rule_data, save_jobs, write_run
from tests.test_standing_google import message
from tests.test_standing_model import RULE
from tests.test_standing_runner import T0, Engine, Reader
from tests.ui_fakes import NOW, AppHarness, fonts, settle

_app: QApplication | None = None
TIDY_FEATURES = ("gmail_read", "gmail_tidy")


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_tidy", "-platform", "offscreen"])
    fonts()


class FakeGoogle:
    alias = "personal"

    def __init__(self, sub: str = "sub-1") -> None:
        self.sub = sub
        self.problem = ("", "")
        self.saved = True
        self.signed_in = True
        self.sign_ins = 0
        self.held = False                                   # credentials() loaded the saved sign-in
        self.disconnects = 0

    def credentials(self, *, interactive: bool, need: str | None = None) -> object:
        self.held = True
        return object()

    def disconnect(self) -> None:
        self.disconnects += 1

    def change_problem(self) -> tuple[str, str]:
        return self.problem

    def still_saved(self) -> bool:
        return self.saved and self.held                     # as GoogleAccount.still_saved: nothing held, False

    def binding(self) -> Any:
        return SimpleNamespace(sub=self.sub, email="owner@example.com")

    def is_signed_in(self, feature: str | None = None) -> bool:
        return self.signed_in

    def sign_in(self) -> None:
        self.sign_ins += 1
        self.signed_in = True


class FakeTidy:
    def __init__(self, google: FakeGoogle | None = None) -> None:
        self.account = google or FakeGoogle()
        self.labels: dict[str, set[str]] = {}
        self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        self.fail = False
        self.in_inbox = False

    def labels_of(self, message_id: str) -> frozenset[str]:
        return frozenset(self.labels.setdefault(message_id, {"INBOX", "UNREAD"}))

    def modify(self, message_id: str, *, add=(), remove=()) -> frozenset[str]:
        self.calls.append((message_id, tuple(add), tuple(remove)))
        if self.fail:
            raise tidy.TidyRefused("Gmail refused it")
        now = (set(self.labels_of(message_id)) | set(add)) - set(remove)
        self.labels[message_id] = now
        return frozenset(now)

    def thread_in_inbox(self, thread_id: str, *, but: str) -> bool:
        return self.in_inbox


class SentReader(Reader):
    """The runner's fake reader, which also answers "in:sent to:<address>" (the owner's own Sent mail)."""

    sent_to: set[str] = set()

    def list_messages(self, query: str, *, max_results: int):
        if query.startswith("in:sent to:"):
            address = query.rpartition("to:")[2]
            return ([("s0000001", "t0000099")], 1) if address in self.sent_to else ([], 0)
        return super().list_messages(query, max_results=max_results)


class TidyRunnerTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.files = StandingFiles(self.data_dir)
        self.now = T0
        self.config = SimpleNamespace(data_dir=self.data_dir, standing=StandingConfig(enabled=True),
                                      ask=AskConfig(enabled=True),
                                      accounts={"personal": SimpleNamespace(features=TIDY_FEATURES)})
        self.reader = SentReader([])
        self.engine = Engine()
        self.tidy = FakeTidy()
        self.written: set[str] = set()
        self.save_rule()
        AppState(self.files).set_consent(True)
        AppState(self.files).set_tidy_consent("personal", True)

    def save_rule(self, **changes: Any) -> None:
        rule = dict(RULE, bindings=[["personal", "sub-1"]])
        rule.update(changes)
        Definitions(self.files).save([], [model.rule_from(rule)])

    def check(self, **changes: Any) -> runner.CheckResult:
        values = dict(readers={"personal": self.reader}, engine=self.engine, clock=lambda: self.now,
                      own=lambda: ["owner@example.com"], tidy={"personal": self.tidy},
                      written_to=lambda address: address.casefold() in self.written)
        values.update(changes)
        return runner.run_check(self.config, runner.Deps(**values), origin=runner.ORIGIN_TASK)

    def run_with(self, *mails: dict, **changes: Any) -> dict:
        self.check(**changes)                                          # the first sight: a baseline
        self.now += timedelta(minutes=6)
        for item in mails:
            self.reader.messages[item["id"]] = item
        self.check(**changes)
        paths = RunFiles(self.files).paths()
        return RunFiles.read(paths[-1]) if paths else {}

    def invoice(self, n: int = 1, **changes: Any) -> dict:
        return message(n, self.now + timedelta(minutes=5), body="Invoice 42 from Acme. Please pay $120.00 by "
                                                                "November 20.", **changes)

    def tidy_of(self, record: dict) -> dict:
        receipt, = [item for item in record["receipts"] if "tidy" in item]
        return receipt["tidy"]

    def test_a_yes_is_starred_journalled_and_kept_on_its_receipt(self) -> None:
        record = self.run_with(self.invoice())
        self.assertEqual(self.tidy.calls, [("m0000001", ("STARRED",), ())])
        made = self.tidy_of(record)
        self.assertEqual((made["kind"], made["held"], made["done"]), ("star", "", True))
        self.assertIn("STARRED", made["after"])
        entries = tidy.TidyJournal(self.files.tidy_journal).entries()
        self.assertEqual([entry.state for entry in entries.values()], [tidy.DONE])
        self.assertEqual(entries[made["tidy"]].message, "m0000001")
        self.assertEqual(Usage(self.files, clock=lambda: self.now).today().tidy_of(RULE["id"]), 1)

    def test_every_gate_holds_the_tidy_up(self) -> None:
        cases = {
            "not_set_up": lambda: setattr(self.config, "accounts", {"personal": SimpleNamespace(features=("gmail_read",))}),
            "not_signed_in": lambda: AppState(self.files).set_tidy_consent("personal", False),
            "account": lambda: setattr(self.tidy.account, "saved", False),
            "broad": lambda: self.save_rule(query="subject:invoice"),
            "cap": lambda: Usage(self.files, clock=lambda: self.now).add("tidy", RULE["id"], 40),
        }
        for held, change in cases.items():
            with self.subTest(held=held):
                self.setUp()
                change()
                record = self.run_with(self.invoice())
                self.assertEqual(self.tidy_of(record)["held"], held)
                self.assertEqual(self.tidy.calls, [])
        for held, change in (("account", lambda: setattr(self.tidy.account, "problem", ("identity", "x"))),
                             ("account", lambda: setattr(self.tidy.account, "sub", "sub-another")),
                             ("account", lambda: self.save_rule(bindings=[])),
                             ("broad", lambda: self.save_rule(dry_run=dict(RULE["dry_run"], matched=120)))):
            with self.subTest(held=held):
                self.setUp()
                change()
                self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], held)
                self.assertEqual(self.tidy.calls, [])

    def test_no_client_and_a_failed_sender_check_hold_it(self) -> None:
        record = self.run_with(self.invoice(), tidy={})
        self.assertEqual(self.tidy_of(record)["held"], tidy.HELD_NOT_SIGNED_IN)
        self.setUp()
        failed = self.invoice(auth=["mx.google.com; dkim=fail; spf=fail; dmarc=fail (p=NONE)"])
        self.assertEqual(self.tidy_of(self.run_with(failed))["held"], "dmarc")
        self.assertEqual(self.tidy.calls, [])

    def test_archive_never_takes_mail_from_someone_the_owner_wrote_to(self) -> None:
        self.save_rule(tidy={"kind": "archive"})
        self.written.add("billing@acme.example")
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_WRITTEN)
        self.setUp()
        self.save_rule(tidy={"kind": "archive"})
        self.tidy.in_inbox = True
        made = self.tidy_of(self.run_with(self.invoice()))
        self.assertEqual((made["done"], made["remove"], made["thread_stays"]), (True, ["INBOX"], True))

    def test_pause_all_during_the_check_holds_it(self) -> None:
        files = self.files

        class Pausing(Engine):
            def run(self, *args: Any, **kwargs: Any) -> Any:
                AppState(files).set_paused_all(True)               # the owner paused while the run ran
                return super().run(*args, **kwargs)

        self.engine = Pausing()
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_PAUSED)
        self.assertEqual(self.tidy.calls, [])

    def test_a_refused_change_is_journalled_failed_and_not_counted(self) -> None:
        self.tidy.fail = True
        made = self.tidy_of(self.run_with(self.invoice()))
        self.assertEqual((made["held"], made["done"]), (tidy.HELD_FAILED, False))
        entries = tidy.TidyJournal(self.files.tidy_journal).entries()
        self.assertEqual([entry.state for entry in entries.values()], [tidy.FAILED])
        self.assertEqual(Usage(self.files, clock=lambda: self.now).today().tidy_of(RULE["id"]), 0)

    def test_a_display_name_alone_never_earns_a_tidy_up(self) -> None:
        self.save_rule(query="from:acme subject:invoice")              # a bare word: Gmail matches display names
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_BROAD)
        self.setUp()
        lookalike = self.invoice(sender="Acme Billing <billing@acme.example.evil.test>")
        self.assertEqual(self.tidy_of(self.run_with(lookalike))["held"], tidy.HELD_BROAD)
        self.assertEqual(self.tidy.calls, [])
        self.assertTrue(tidy.sender_named("billing@acme.example", ["acme.example"]))
        self.assertTrue(tidy.sender_named("billing@mail.acme.example", ["@acme.example"]))
        self.assertFalse(tidy.sender_named("x@evil.test", ["billing@acme.example", "acme"]))

    def test_archive_holds_for_someone_in_the_owners_sent_mail(self) -> None:
        self.save_rule(tidy={"kind": "archive"})
        SentReader.sent_to = {"billing@acme.example"}
        self.addCleanup(setattr, SentReader, "sent_to", set())
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_WRITTEN)
        self.assertEqual(self.tidy.calls, [])

    def test_a_paused_rule_or_unreadable_own_addresses_hold_it(self) -> None:
        files, rule = self.files, model.rule_from(dict(RULE, bindings=[["personal", "sub-1"]]))

        class PausingRule(Engine):
            def run(self, *args: Any, **kwargs: Any) -> Any:
                Definitions(files).save([], [model.with_status(rule, "paused")])   # paused during its run
                return super().run(*args, **kwargs)

        self.engine = PausingRule()
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_PAUSED)
        self.setUp()

        def broken() -> list[str]:
            raise OSError("no addresses")

        self.assertEqual(self.tidy_of(self.run_with(self.invoice(), own=broken))["held"], "own")
        self.assertEqual(self.tidy.calls, [])

    def test_a_change_without_an_answer_stays_unknown(self) -> None:
        def no_answer(*_args: Any, **_kwargs: Any) -> Any:
            raise TimeoutError("no answer")

        self.tidy.modify = no_answer
        made = self.tidy_of(self.run_with(self.invoice()))
        self.assertEqual(made["held"], tidy.HELD_UNKNOWN)
        entries = tidy.TidyJournal(self.files.tidy_journal).entries()
        self.assertEqual([entry.state for entry in entries.values()], [tidy.UNKNOWN])

    def test_a_job_whose_account_changed_is_not_read(self) -> None:
        self.check(binding=lambda alias: "sub-another")
        self.now += timedelta(minutes=6)
        self.reader.messages["m0000001"] = self.invoice()
        self.check(binding=lambda alias: "sub-another")
        self.assertEqual(self.engine.prompts, [])                      # nothing of it went to Claude
        state = RunnerState(self.files)
        self.assertEqual(state.data.get("problems"), {"personal": "binding"})

    def test_paused_or_without_consent_the_task_still_beats(self) -> None:
        AppState(self.files).set_paused_all(True)
        self.assertEqual(self.check().status, runner.PAUSED)
        self.assertIsNotNone(RunnerState(self.files).data.get("task_check"))

    def test_already_starred_is_left_alone(self) -> None:
        self.tidy.labels["m0000001"] = {"INBOX", "STARRED"}
        self.assertEqual(self.tidy_of(self.run_with(self.invoice()))["held"], tidy.HELD_ALREADY)
        self.assertEqual(self.tidy.calls, [])


class JournalAndClientTests(unittest.TestCase):

    def test_a_crash_between_doing_and_done_reads_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            journal = tidy.TidyJournal(Path(folder) / "standing_tidy.jsonl")
            first = journal.begin(rule="r-0123456789ab", account="personal", message="m0000001", thread="t0000001",
                                  kind="star", change=tidy.Change(add=("STARRED",)), before={"INBOX"}, now=T0)
            second = journal.begin(rule="r-0123456789ab", account="personal", message="m0000002", thread="t0000002",
                                   kind="star", change=tidy.Change(add=("STARRED",)), before={"INBOX"}, now=T0)
            journal.done(second, {"INBOX", "STARRED"}, T0)
            with open(journal.path, "a", encoding="utf-8") as fh:
                fh.write('{"id": "cut-off')                           # a line the crash cut
            entries = journal.entries()
        self.assertEqual((entries[first].state, entries[second].state), (tidy.UNKNOWN, tidy.DONE))
        self.assertEqual(entries[second].after, ("INBOX", "STARRED"))

    def test_the_client_names_only_star_inbox_and_one_user_label(self) -> None:
        sent: list[dict] = []

        class Request:
            def __init__(self, answer: dict) -> None:
                self.answer = answer

            def execute(self, num_retries: int = 0) -> dict:
                return self.answer

        class Messages:
            def modify(self, userId: str, id: str, body: dict) -> Request:  # noqa: N803 - Google's names
                sent.append(body)
                return Request({"labelIds": ["INBOX", "STARRED"]})

        service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: Messages()))
        account = SimpleNamespace(alias="personal", generation=0, credentials=lambda **_kw: object())
        client = tidy.GmailTidy(account, service_factory=lambda creds: service)
        self.assertEqual(client.modify("m0000001", add=("STARRED",)), frozenset({"INBOX", "STARRED"}))
        self.assertEqual(sent, [{"addLabelIds": ["STARRED"], "removeLabelIds": []}])
        for bad in (("TRASH",), ("SPAM",), ("UNREAD",), ("Label_1", "Label_2"), ("IMPORTANT",)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                client.modify("m0000001", add=bad)
        with self.assertRaises(ValueError):
            client.modify("not an id!", add=("STARRED",))
        self.assertEqual(len(sent), 1)


class SignInSplitTests(unittest.TestCase):

    def test_gmail_tidy_never_joins_the_main_sign_in_and_has_its_own_file(self) -> None:
        from briefing_reader import executor, google_auth
        from briefing_reader.config import AccountConfig, CalendarConfig

        with tempfile.TemporaryDirectory() as folder:
            config = SimpleNamespace(
                data_dir=Path(folder), calendar=CalendarConfig(enabled=True, client_secret_path=Path(folder) / "x.json"),
                accounts={"work": AccountConfig("work", features=("gmail_read", "gmail_tidy"))})
            self.assertEqual(executor.account_features(config, "work"), ("gmail_read",))
            main = executor.build_accounts(config, persist=False)["work"]
            tidy_account = executor.build_tidy_accounts(config, persist=False)["work"]
        modify = "https://www.googleapis.com/auth/gmail.modify"
        self.assertNotIn(modify, main.requested_scopes())
        self.assertIn(modify, tidy_account.requested_scopes())
        self.assertFalse(any("gmail.readonly" in scope or "gmail.send" in scope
                             for scope in tidy_account.requested_scopes()))
        self.assertEqual(tidy_account.token_path.name, "google_token_work.tidy.json")
        self.assertNotEqual(tidy_account.token_path, main.token_path)
        self.assertEqual(google_auth.tidy_token_path(Path("d"), "work").name, "google_token_work.tidy.json")


class AppUndoTests(unittest.TestCase):

    def setUp(self) -> None:
        def prepare(config) -> None:
            save_jobs(config, rules=[rule_data(tidy={"kind": "star"}, bindings=[["work", "sub-1"]])])
            consent(config)

        self.app = AppHarness(self, config_extra=STANDING_ON, prepare=prepare)
        self.c = self.app.c
        self.s = self.c.standing
        self.c._locked = lambda: False
        self.s.busy_probe = lambda: False
        self.s.thread_runner = lambda work: work()
        self.client = FakeTidy()
        self.client.labels[GMID] = {"INBOX", "STARRED"}
        self.s.tidy_for = lambda alias: self.client
        self.app.reading(autoplay=False)

    def receipt(self, *, at: datetime | None = None) -> dict:
        return {"id": f"{GMID}-0", "kind": "tell", "values": {"amount": "$40.00"}, "refused": "", "account": "work",
                "message": GMID, "thread": "thr00000a1", "msgid": "x@example.org", "from": "billing@example.org",
                "name": "Example Billing", "subject": "Invoice 1043", "received": stamp(NOW),
                "tidy": {"kind": "star", "held": "", "done": True, "tidy": "0123456789ab", "add": ["STARRED"],
                         "remove": [], "before": ["INBOX"], "after": ["INBOX", "STARRED"],
                         "at": stamp(at or NOW - timedelta(minutes=9))}}

    def tidy_row(self) -> Any:
        rows = [row for row in self.c.window.reading.agents_stage.activity_rows() if row.info().key.endswith(":tidy")]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_undo_takes_off_exactly_jarviss_star(self) -> None:
        run_id = write_run(self.app.config, receipts=[self.receipt()])
        self.s.reload()
        row = self.tidy_row()
        self.assertEqual(row.info().state_text, "Starred")
        self.assertTrue(row.info().can_undo)
        row.undo_button.click()
        settle()
        self.assertEqual(self.client.calls, [(GMID, (), ("STARRED",))])
        self.assertTrue(AppState(files_of(self.app.config)).is_undone(f"{run_id}:{GMID}-0:tidy"))
        self.assertEqual(self.tidy_row().info().state_text, "Undone")
        self.assertFalse(self.tidy_row().info().can_undo)

    def test_labels_changed_since_leave_it_alone(self) -> None:
        write_run(self.app.config, receipts=[self.receipt()])
        self.s.reload()
        self.client.labels[GMID] = {"INBOX"}                           # the owner took the star off since
        self.tidy_row().undo_button.click()
        settle()
        self.assertEqual(self.client.calls, [])
        self.assertFalse(AppState(files_of(self.app.config)).is_undone(self.tidy_row().info().key))
        self.assertEqual(self.tidy_row().info().state_text, "Changed since")
        self.assertFalse(self.tidy_row().info().can_undo)

    def test_reading_the_mail_since_does_not_block_the_undo(self) -> None:
        write_run(self.app.config, receipts=[self.receipt()])
        self.s.reload()
        self.client.labels[GMID] = {"INBOX", "STARRED", "CATEGORY_UPDATES"}   # UNREAD went, a category came
        self.tidy_row().undo_button.click()
        settle()
        self.assertEqual(self.client.calls, [(GMID, (), ("STARRED",))])

    def test_clear_keeps_a_run_whose_tidy_up_can_still_be_undone(self) -> None:
        write_run(self.app.config, receipts=[self.receipt()])
        self.s.reload()
        self.c.window.reading.agents_stage.clear_button.click()
        settle()
        self.assertEqual(len(RunFiles(StandingFiles(self.app.config.data_dir)).paths()), 1)
        self.assertTrue(self.tidy_row().info().can_undo)

    def test_after_seven_days_there_is_no_undo(self) -> None:
        write_run(self.app.config, started=NOW - timedelta(days=6, hours=23), receipts=[
            self.receipt(at=NOW - timedelta(days=7, hours=1))])
        self.s.reload()
        self.assertFalse(self.tidy_row().info().can_undo)


class TidySignInTests(unittest.TestCase):

    def test_a_tidy_rule_asks_for_its_own_sign_in_and_keeps_the_yes(self) -> None:
        import dataclasses

        from briefing_reader.config import AccountConfig

        def prepare(config) -> None:
            save_jobs(config, rules=[rule_data(tidy={"kind": "star"})])
            consent(config)

        app = AppHarness(self, config_extra=STANDING_ON, prepare=prepare)
        s = app.c.standing
        s.thread_runner = lambda work: work()
        s.config = dataclasses.replace(app.c.config, accounts={
            **app.c.config.accounts, "work": AccountConfig("work", features=("calendar", "gmail_send", "gmail_read",
                                                                              "gmail_tidy"))})
        google = FakeGoogle()
        google.signed_in = False
        s.tidy_accounts = lambda: {"work": google}
        app.reading(autoplay=False)
        s.reload()
        stage = app.c.window.reading.agents_stage
        card = stage.notice_card(standing_ui.TIDY_CARD + "work")
        self.assertIsNotNone(card)
        self.assertTrue(any("never sends, deletes, marks as read" in line for line in card.line_texts()))
        card.button("signin").click()
        settle()
        self.assertEqual(google.sign_ins, 1)
        self.assertTrue(AppState(files_of(app.config)).tidy_consented("work"))
        self.assertIsNone(stage.notice_card(standing_ui.TIDY_CARD + "work"))


if __name__ == "__main__":
    unittest.main()
