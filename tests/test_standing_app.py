"""Tests for the standing agents and rules in the app (standing_ui, standing_view, part 6 of the build).

The run files the background check writes become cards on Approvals (each card read again, decided and withdrawn
ones left out, a job's recorded Google account checked); the Agents screen (Ctrl+9) shows the jobs, what they did and
their reports, with Run now, Pause, Delete (and its Undo), Pause all, a receipt's Undo and Clear; the one spoken line
is fixed, said once, held while locked, in quiet hours, while a meeting records, in focus mode or while Windows says
busy; the job's name only with the local voice; a standing Reply's thread is read again at the click; --standing-show
shows Jarvis without the focus. Qt runs offscreen; nothing reaches Google, Notion or claude.exe, and no process is
started.
"""

from __future__ import annotations

import time
import unittest
from datetime import timedelta
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import dock as dock_mod
from briefing_reader import hud, standing_ui, ui
from briefing_reader.actions import SOURCE_STANDING, STATUS_DENIED
from briefing_reader.speech import KIND_NOTICE
from briefing_reader.standing.model import STATUS_LIVE, STATUS_PAUSED
from briefing_reader.standing.store import AppState, Definitions, RunRequests
from tests.standing_fakes import (AGENT_ID, AGENT_NAME, PAT, REPORT, RULE_ID, RULE_NAME, SENTENCE, STANDING_ON,
                                  SUBJECT, THREAD, ThreadReader, agent_data, card_receipt, consent, files_of,
                                  own_message, pat_message, reply_record, rule_data, run_paths, save_jobs,
                                  tell_receipt, thread_of, todo_record, write_run)
from tests.ui_fakes import NOW, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_app", "-platform", "offscreen"])
    fonts()


def standing_cards(c: ui.AppController) -> list:
    return [action for action in c._actions if action.source == SOURCE_STANDING]


class _Base(unittest.TestCase):
    extra = ""
    readers: dict | None = None

    def setUp(self) -> None:
        self.app = AppHarness(self, config_extra=STANDING_ON + self.extra, readers=self.readers,
                              prepare=self.prepare)
        self.c = self.app.c
        self.s = self.c.standing
        self.assertIsNotNone(self.s)
        self.c._locked = lambda: False                      # never the real session's lock state
        self.s.busy_probe = lambda: False
        self.started: list[bool] = []
        self.s.start_runner = lambda: self.started.append(True) or True
        self.app.reading(autoplay=False)
        self.s.reload()

    def prepare(self, config) -> None:
        save_jobs(config, agents=[agent_data()], rules=[rule_data()])
        consent(config)

    def said(self) -> list[tuple[str, str, str]]:
        return [item for item in self.app.voice.said if item[2].startswith(standing_ui.ANNOUNCE_KEY)]


class LoadingTests(_Base):

    def test_a_runs_cards_come_to_approvals_under_agents(self) -> None:
        todo = todo_record()
        write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        settle()
        mine = standing_cards(self.c)
        self.assertEqual([card.id for card in mine], [todo["id"]])
        self.assertEqual(mine[0].title, "Pay $40.00")
        self.assertIn(("AGENTS", 1), self.c.window.reading.approvals.group_headers())

    def test_a_decided_or_withdrawn_card_is_left_out(self) -> None:
        first = todo_record(key="msg00000b1", title="Pay $10.00")
        second = todo_record(key="msg00000b2", title="Pay $20.00")
        third = todo_record(key="msg00000b3", title="Pay $30.00")
        write_run(self.app.config, run_cards=[first, second, third])
        self.app.store.set(first["id"], STATUS_DENIED, kind="todo", account="work")
        AppState(files_of(self.app.config)).mark("withdrawn", [second["id"]])
        self.s.reload()
        self.assertEqual([card.id for card in standing_cards(self.c)], [third["id"]])

    def test_a_card_decided_while_it_shows_keeps_its_place(self) -> None:
        todo = todo_record()
        write_run(self.app.config, run_cards=[todo])
        self.s.reload()
        self.app.store.set(todo["id"], STATUS_DENIED, kind="todo", account="work")
        write_run(self.app.config, run_cards=[todo_record(key="msg00000d1", title="Pay $50.00")])
        self.s.reload()                                                  # its result (and Undo) stays in view
        self.assertIn(todo["id"], [card.id for card in standing_cards(self.c)])
        self.assertNotIn(todo["id"], self.s._waiting_ids())

    def test_a_changed_line_or_a_foreign_kind_is_dropped(self) -> None:
        moved = dict(reply_record(), line=reply_record()["line"].replace("msgid=invoice-1043@", "msgid=other-9@"))
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, run_cards=[moved])   # another message's id
        rekeyed = dict(todo_record(), key="msg00000x8")                 # its id is not its key's any more
        write_run(self.app.config, run_cards=[rekeyed])
        move = dict(todo_record(), line="Move: acct=work | event=jts0001aa | cal=primary | when=2026-10-09 14:00-"
                                        "15:00 | notify=all | title=x | at=2026-10-05 14:00-15:00 | link= | body=")
        write_run(self.app.config, run_cards=[move])
        self.s.reload()
        self.assertEqual(standing_cards(self.c), [])

    def test_a_deleted_jobs_cards_and_old_files_are_left_out(self) -> None:
        write_run(self.app.config, job_id="r-0000000000ff", run_cards=[todo_record(job_id="r-0000000000ff")])
        write_run(self.app.config, started=NOW - timedelta(days=9), run_cards=[todo_record(key="msg00000c1")])
        self.s.reload()
        self.assertEqual(standing_cards(self.c), [])

    def test_a_damaged_run_file_is_skipped(self) -> None:
        todo = todo_record()
        write_run(self.app.config, run_cards=[todo])
        runs = files_of(self.app.config).runs
        (runs / "20261004-134500-0123456789ab.json").write_text("{not json", encoding="utf-8")
        self.s.reload()
        self.assertEqual([card.id for card in standing_cards(self.c)], [todo["id"]])

    def test_a_reply_card_marks_every_recipient_unverified(self) -> None:
        reply = reply_record()
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, run_cards=[reply])
        self.s.reload()
        card, = standing_cards(self.c)
        self.assertEqual(card.unverified, frozenset({PAT}))

    def test_another_google_account_for_the_alias_drops_its_cards(self) -> None:
        save_jobs(self.app.config, agents=[agent_data(bindings=[["work", "sub-recorded-1"]])], rules=[rule_data()])
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME,
                  run_cards=[reply_record()])

        class Account:
            def __init__(self, sub: str) -> None:
                self.sub = sub

            def binding(self):
                return type("Binding", (), {"sub": self.sub})()

        self.c._google_accounts = {"work": Account("sub-another-2")}
        self.s.reload()
        self.assertEqual(standing_cards(self.c), [])
        self.c._google_accounts = {"work": Account("sub-recorded-1")}
        self.s.reload()
        self.assertEqual(len(standing_cards(self.c)), 1)

    def test_the_folder_check_reads_a_new_run(self) -> None:
        self.s.start()
        self.assertEqual(standing_cards(self.c), [])
        write_run(self.app.config, run_cards=[todo_record()])
        self.s._tick()
        self.assertEqual(len(standing_cards(self.c)), 1)


class ScreenTests(_Base):

    def test_the_dock_has_agents_on_ctrl_9_and_the_screen_shows_the_jobs(self) -> None:
        reading = self.c.window.reading
        self.assertTrue(reading.agents_available())
        self.assertEqual(dock_mod.SHORTCUTS[dock_mod.AGENTS], "Ctrl+9")
        reading.set_tab(ui.SCREEN_AGENTS)
        settle()
        self.assertEqual(reading.current_tab(), ui.SCREEN_AGENTS)
        stage = reading.agents_stage
        agent, rule = stage.job_row(AGENT_ID), stage.job_row(RULE_ID)
        self.assertEqual((agent.name_label.text(), agent.badge.text()), (AGENT_NAME, "LIVE"))
        self.assertIn("Every day at 7:30 AM", agent.detail_label.text())
        self.assertFalse(agent.run_button.isHidden())
        self.assertEqual(rule.detail_label.text(), SENTENCE)
        self.assertIn("DRY RUN: 3 OF 12 YES", rule.meta_label.text())
        self.assertTrue(rule.run_button.isHidden())                       # a rule runs on new mail only

    def test_texts_are_plain_text(self) -> None:
        save_jobs(self.app.config, agents=[agent_data(name="<b>Bold</b> & co")], rules=[rule_data()])
        self.s.reload()
        row = self.c.window.reading.agents_stage.job_row(AGENT_ID)
        self.assertEqual(row.name_label.textFormat(), ui.Qt.TextFormat.PlainText)
        self.assertEqual(row.name_label.text(), "<b>Bold</b> & co")

    def test_run_now_asks_and_starts_a_check(self) -> None:
        self.c.window.reading.agents_stage.job_row(AGENT_ID).run_button.click()
        settle()
        self.assertEqual(RunRequests(files_of(self.app.config)).pending(), [AGENT_ID])
        self.assertEqual(self.started, [True])
        self.assertIn("RUN NOW ASKED", self.c.window.reading.agents_stage.job_row(AGENT_ID).meta_label.text())

    def test_run_now_needs_consent_and_no_pause_all(self) -> None:
        AppState(files_of(self.app.config)).set_paused_all(True)
        self.s.reload()
        self.s.run_now(AGENT_ID)
        self.assertEqual(RunRequests(files_of(self.app.config)).pending(), [])
        self.assertTrue(self.c.window.reading.agents_stage.job_row(AGENT_ID).run_button.isHidden())

    def test_pause_withdraws_its_waiting_cards_and_resume_brings_the_job_back(self) -> None:
        todo = todo_record()
        write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        self.assertEqual(len(standing_cards(self.c)), 1)
        self.c.window.reading.agents_stage.job_row(RULE_ID).pause_button.click()
        settle()
        _agents, rules = Definitions(files_of(self.app.config)).load()
        self.assertEqual(rules[0].status, STATUS_PAUSED)
        self.assertEqual(standing_cards(self.c), [])
        self.assertTrue(AppState(files_of(self.app.config)).is_withdrawn(todo["id"]))
        row = self.c.window.reading.agents_stage.job_row(RULE_ID)
        self.assertEqual((row.pause_button.text(), row.badge.text()), ("Resume", "PAUSED"))
        row.pause_button.click()
        settle()
        self.assertEqual(Definitions(files_of(self.app.config)).load()[1][0].status, STATUS_LIVE)

    def test_delete_withdraws_and_its_undo_brings_both_back(self) -> None:
        todo = todo_record()
        write_run(self.app.config, run_cards=[todo])
        self.s.reload()
        self.c.window.reading.agents_stage.job_row(RULE_ID).delete_button.click()
        settle()
        self.assertEqual(Definitions(files_of(self.app.config)).load()[1], [])
        self.assertEqual(standing_cards(self.c), [])
        stage = self.c.window.reading.agents_stage
        self.assertIn(f"Deleted {RULE_NAME}.", stage.notes())
        self.s._on_link(standing_ui.UNDO_DELETE_LINK)
        settle()
        self.assertEqual([rule.id for rule in Definitions(files_of(self.app.config)).load()[1]], [RULE_ID])
        self.assertEqual([card.id for card in standing_cards(self.c)], [todo["id"]])

    def test_pause_all_is_kept_for_the_runner(self) -> None:
        stage = self.c.window.reading.agents_stage
        stage.pause_all_button.click()
        settle()
        self.assertTrue(AppState(files_of(self.app.config)).paused_all)
        self.assertEqual(stage.pause_all_button.text(), "Resume all")
        self.assertIn("PAUSED", stage.agents_meta.text().upper())
        stage.pause_all_button.click()
        settle()
        self.assertFalse(AppState(files_of(self.app.config)).paused_all)

    def test_activity_lists_receipts_newest_first_and_undo_withdraws_a_waiting_card(self) -> None:
        todo = todo_record()
        write_run(self.app.config, started=NOW - timedelta(minutes=30), receipts=[tell_receipt()])
        run_id = write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        rows = self.c.window.reading.agents_stage.activity_rows()
        self.assertEqual([row.info().state_text for row in rows], ["Waiting", "Told"])
        self.assertEqual(rows[0].info().text, "To-do: Pay $40.00")
        self.assertIn(SUBJECT, rows[0].info().detail)
        self.assertIn("amount: $40.00", rows[1].info().text)
        rows[0].undo_button.click()
        settle()
        self.assertEqual(standing_cards(self.c), [])
        state = AppState(files_of(self.app.config))
        self.assertTrue(state.is_undone(f"{run_id}:{card_receipt(todo)['id']}"))
        self.assertEqual(self.c.window.reading.agents_stage.activity_rows()[0].info().state_text, "Withdrawn")

    def test_a_run_that_could_not_run_says_so(self) -> None:
        write_run(self.app.config, status="capped", needs_owner=False, problem="runs")
        self.s.reload()
        row, = self.c.window.reading.agents_stage.activity_rows()
        self.assertEqual(row.info().state_text, "CAPPED")
        self.assertFalse(row.info().can_undo)

    def test_reports_show_under_written_by_claude_and_clear_deletes(self) -> None:
        todo = todo_record()
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, report=REPORT,
                  started=NOW - timedelta(minutes=20))
        write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        stage = self.c.window.reading.agents_stage
        report, = stage.report_rows()
        self.assertEqual((report.job_label.text(), report.text_label.text()), (AGENT_NAME, REPORT))
        self.assertEqual(report.kicker_label.text(), "WRITTEN BY CLAUDE FROM YOUR MAIL")
        stage.clear_button.click()
        settle()
        self.assertEqual(len(run_paths(self.app.config)), 1)           # the run whose card still waits stays
        self.assertEqual(stage.report_rows(), [])
        self.assertEqual(stage.activity_rows(), [])
        self.assertEqual([card.id for card in standing_cards(self.c)], [todo["id"]])
        self.app.store.set(todo["id"], STATUS_DENIED, kind="todo", account="work")
        self.s.reload()
        self.assertEqual(run_paths(self.app.config), [])                # its card was decided: its file goes

    def test_without_jobs_the_screen_says_how_to_start(self) -> None:
        save_jobs(self.app.config)
        self.s.reload()
        stage = self.c.window.reading.agents_stage
        self.assertTrue(stage.agents_empty.isVisibleTo(stage))
        self.assertIn("agent:", stage.agents_empty.text())

    def test_background_runs_off_says_what_to_do(self) -> None:
        self.assertTrue(any("install-schedule.ps1" in note for note in self.c.window.reading.agents_stage.notes()))
        meta = self.c.window.reading.agents_stage.agents_meta.text().upper()
        self.assertIn("BACKGROUND RUNS OFF", meta)
        self.assertIn("0 OF 30 RUNS TODAY", meta)


class AnnounceTests(_Base):

    def test_a_standing_activation_reads_again_and_says_the_fixed_line_once(self) -> None:
        todo = todo_record()
        run_id = write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        with self.assertLogs("briefing_reader.standing_ui", "INFO") as logs:
            self.c.handle_activation({"cmd": "standing", "runs": [run_id]})
        settle()
        said, = self.said()
        self.assertEqual(said[0], KIND_NOTICE)
        self.assertEqual(said[2], standing_ui.ANNOUNCE_KEY + run_id)
        self.assertIn("left 1 card for your OK", said[1])
        self.assertTrue(AppState(files_of(self.app.config)).is_announced(run_id))
        texts = [entry.text_label.text() for entry in self.c.window.reading.conversation.entry_widgets()]
        self.assertTrue(any("left 1 card for your OK" in text for text in texts))
        for line in logs.output:                                        # ids and counts only
            for private in (SUBJECT, PAT, RULE_NAME, "Pay $40.00", SENTENCE):
                self.assertNotIn(private, line)
        self.c.handle_activation({"cmd": "standing", "runs": [run_id]})
        self.s.announce()
        self.assertEqual(len(self.said()), 1)                           # told once

    def test_the_jobs_name_is_said_only_with_the_local_voice(self) -> None:
        write_run(self.app.config, receipts=[tell_receipt()])
        self.s.reload()
        said, = self.said()
        self.assertIn(RULE_NAME, said[1])                               # [voice] engine "local" (the default)

    def test_nothing_held_says_it_when_the_hold_ends(self) -> None:
        for name, hold in (("locked", lambda: setattr(self.c, "_locked", lambda: True)),
                           ("meeting", lambda: setattr(self.c, "_meeting_recording", lambda: True)),
                           ("focus", lambda: setattr(self.c, "_focus_quiet", lambda: True)),
                           ("busy", lambda: setattr(self.s, "busy_probe", lambda: True))):
            with self.subTest(name):
                self.app.voice.said.clear()
                hold()
                run_id = write_run(self.app.config, receipts=[tell_receipt()])
                self.s.reload()
                self.assertEqual(self.said(), [])
                self.assertFalse(AppState(files_of(self.app.config)).is_announced(run_id))
                self.c._locked = lambda: False
                self.c.__dict__.pop("_meeting_recording", None)
                self.c.__dict__.pop("_focus_quiet", None)
                self.s.busy_probe = lambda: False
                self.s._say_held()
                self.assertEqual(len(self.said()), 1)
                self.assertTrue(AppState(files_of(self.app.config)).is_announced(run_id))

    def test_quiet_hours_hold_the_line(self) -> None:
        self.app.clock.set(NOW.replace(hour=23, minute=10))
        write_run(self.app.config, started=self.app.clock.at - timedelta(minutes=5), receipts=[tell_receipt()])
        self.s.reload()
        self.assertEqual(self.said(), [])
        self.app.clock.set(NOW.replace(hour=8, minute=5) + timedelta(days=1))
        self.s._say_held()
        self.assertEqual(len(self.said()), 1)

    def test_seeing_the_agents_screen_counts_as_told(self) -> None:
        self.c._locked = lambda: True
        run_id = write_run(self.app.config, receipts=[tell_receipt()])
        self.s.reload()
        self.assertTrue(self.c.window.reading.dock.signal_words(dock_mod.AGENTS) == "new")
        self.s.on_screen_shown()
        self.c._locked = lambda: False
        self.s._say_held()
        self.assertEqual(self.said(), [])
        self.assertTrue(AppState(files_of(self.app.config)).is_announced(run_id))
        self.assertEqual(self.c.window.reading.dock.signal_words(dock_mod.AGENTS), "")

    def test_an_old_run_is_shown_never_said(self) -> None:
        run_id = write_run(self.app.config, started=NOW - timedelta(hours=20), receipts=[tell_receipt()])
        self.s.reload()
        self.assertEqual(self.said(), [])
        self.assertTrue(AppState(files_of(self.app.config)).is_announced(run_id))

    def test_a_run_that_does_not_need_the_owner_is_not_said(self) -> None:
        write_run(self.app.config, status="capped", needs_owner=False)
        self.s.reload()
        self.assertEqual(self.said(), [])


class OnlineVoiceTests(_Base):
    extra = '\n[voice]\nengine = "online"\n'

    def test_the_online_voice_never_says_the_jobs_name(self) -> None:
        write_run(self.app.config, receipts=[tell_receipt()])
        self.s.reload()
        said, = self.said()
        self.assertNotIn(RULE_NAME, said[1])
        self.assertIn("your agents have 1 new item for you", said[1])


class ReplyCheckTests(_Base):

    def setUp(self) -> None:
        self.reader = ThreadReader({THREAD: thread_of(pat_message())})
        self.readers = {"work": self.reader}
        super().setUp()
        self.s.thread_runner = lambda work: work()                     # the check runs at once (no thread)
        reply = reply_record()
        write_run(self.app.config, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME, run_cards=[reply])
        self.s.reload()
        self.c.window.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.card_id = reply["id"]
        self.card = self.c.window.reading.action_card(self.card_id)
        self.card.ticks.tick(PAT).click()                               # the NEW RECIPIENT tick
        settle()

    def send(self) -> None:
        self.card.approve_button.click()
        settle()

    def test_a_passing_check_lets_send_go_on_by_itself(self) -> None:
        self.send()
        self.assertEqual(self.reader.calls, [THREAD])
        self.assertIsNotNone(self.c._send_hold)
        self.assertEqual(self.c._send_hold.action.id, self.card_id)
        self.assertEqual(self.app.senders["work"].sent, [])            # the hidden hold: nothing yet

    def test_the_owner_replied_since_nothing_is_sent(self) -> None:
        self.reader.threads[THREAD] = thread_of(pat_message(), own_message())
        self.send()
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.c._hints.get(self.card_id), standing_ui.REPLY_PROBLEMS["replied"])

    def test_the_message_is_gone_nothing_is_sent(self) -> None:
        self.reader.threads[THREAD] = thread_of(pat_message("msg00000zz"))
        self.send()
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.c._hints.get(self.card_id), standing_ui.REPLY_PROBLEMS["gone"])

    def test_an_unreadable_thread_nothing_is_sent(self) -> None:
        self.reader.error = OSError("network down")
        self.send()
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.c._hints.get(self.card_id), standing_ui.REPLY_PROBLEMS["unreadable"])

    def test_a_late_answer_asks_for_another_click(self) -> None:
        pending: list = []
        self.s.thread_runner = pending.append
        self.send()
        self.assertEqual(self.c._hints.get(self.card_id), standing_ui.REPLY_CHECKING_NOTE)
        self.assertIsNone(self.c._send_hold)
        with mock.patch.object(standing_ui.time, "monotonic", return_value=time.monotonic() + 60):
            pending.pop()()
            settle()
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.c._hints.get(self.card_id), standing_ui.REPLY_AGAIN_NOTE)
        self.send()                                                     # passed two minutes ago at most: it goes
        self.assertIsNotNone(self.c._send_hold)

    def test_a_check_in_its_own_thread_answers_on_the_gui_thread(self) -> None:
        self.s.thread_runner = standing_ui.StandingController._in_thread
        self.send()
        self.assertTrue(wait_for(lambda: self.c._send_hold is not None, 5.0))


class RecipientCheckTests(unittest.TestCase):

    def test_reply_problem_reads_the_thread_as_the_click_needs(self) -> None:
        from briefing_reader.ask.mail import thread_from_api

        thread = thread_from_api(thread_of(pat_message()), "work")
        self.assertEqual(standing_ui.reply_problem(thread, "msg00000a1", [PAT], ["me@example.edu"]), "")
        self.assertEqual(standing_ui.reply_problem(thread, "msg00000a1", ["eve@example.net"], []), "not_on_message")
        self.assertEqual(standing_ui.reply_problem(thread, "nope", [PAT], []), "gone")
        both = thread_from_api(thread_of(pat_message(), own_message()), "work")
        self.assertEqual(standing_ui.reply_problem(both, "msg00000a1", [PAT], ["me@example.edu"]), "replied")


class StandingShowTests(unittest.TestCase):

    def test_standing_show_opens_without_the_focus_and_tells(self) -> None:
        def prepare(config) -> None:
            save_jobs(config, rules=[rule_data()])
            consent(config)
            write_run(config, receipts=[tell_receipt()])

        app = AppHarness(self, config_extra=STANDING_ON, launch="standing", prepare=prepare)
        app.c._locked = lambda: False
        app.c.standing.busy_probe = lambda: False
        with mock.patch.object(ui, "force_foreground") as forward, \
                mock.patch.object(ui, "show_without_activating") as quietly:
            app.c.start()
            settle()
        forward.assert_not_called()
        quietly.assert_called()
        self.assertEqual(app.c.state, ui.STATE_READING)
        self.assertEqual(len([item for item in app.voice.said if item[2].startswith(standing_ui.ANNOUNCE_KEY)]), 1)

    def test_standing_show_with_nothing_to_tell_ends(self) -> None:
        def prepare(config) -> None:
            save_jobs(config, rules=[rule_data()])
            consent(config)

        app = AppHarness(self, config_extra=STANDING_ON, launch="standing", prepare=prepare)
        with mock.patch.object(ui.AppController, "quit") as quit_:
            app.c.start()
        quit_.assert_called_once()


class OffTests(unittest.TestCase):

    def test_without_standing_there_is_no_agents_surface(self) -> None:
        app = AppHarness(self)
        self.assertIsNone(app.c.standing)
        self.assertFalse(app.c.window.reading.agents_available())
        app.c.handle_activation({"cmd": "standing", "runs": []})        # dropped, nothing happens

    def test_standing_without_ask_stays_hidden(self) -> None:
        app = AppHarness(self, ask=False, config_extra=STANDING_ON)
        self.assertIsNone(app.c.standing)
        self.assertFalse(app.c.window.reading.agents_available())


if __name__ == "__main__":
    unittest.main()
