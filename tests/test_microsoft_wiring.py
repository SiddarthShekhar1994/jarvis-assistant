"""Outlook accounts in the app: config.toml's [microsoft] and backend "microsoft", the executor's builders and its
refusal of every card for them, Ask's account list, Settings > Accounts, the Connect without a client id, and the
agenda read (the Day view) with an Outlook account's events - all with fakes; nothing reaches the network."""

from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PySide6.QtWidgets import QApplication

from briefing_reader import config as config_module
from briefing_reader import executor, ms_auth, outlook, outlook_ui, settings_view, ui
from briefing_reader.actions import parse_action_line
from briefing_reader.ask.planner import GoogleSources
from briefing_reader.gcal import CalendarError, CalendarEvent
from tests.test_microsoft_auth import CLIENT_ID, NoNetwork

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_microsoft_wiring", "-platform", "offscreen"])


TOML = f"""
[microsoft]
client_id = "{CLIENT_ID}"
tenant = "consumers"

[accounts.personal]
features = ["calendar"]

[accounts.school]
backend = "microsoft"
"""


class ConfigTests(NoNetwork):
    def load(self, text: str) -> Any:
        root = Path(tempfile.mkdtemp(prefix="ms-config-"))
        (root / "config.toml").write_text(text, encoding="utf-8")
        return config_module.load_config(root, environ={"LOCALAPPDATA": str(root)})

    def test_microsoft_table_and_backend(self) -> None:
        cfg = self.load(TOML)
        self.assertEqual((cfg.microsoft.client_id, cfg.microsoft.tenant), (CLIENT_ID, "consumers"))
        school = cfg.accounts["school"]
        self.assertEqual(school.backend, "microsoft")
        self.assertEqual(school.features, ("calendar", "mail_read"))

    def test_defaults_have_no_client_id(self) -> None:
        cfg = self.load("")
        self.assertEqual((cfg.microsoft.client_id, cfg.microsoft.tenant), ("", "common"))

    def test_a_bad_client_id_is_left_out_without_repeating_it(self) -> None:
        with self.assertLogs("briefing_reader.config", "WARNING") as logs:
            cfg = self.load('[microsoft]\nclient_id = "paste-here-secretish"\ntenant = "contoso"\n')
        self.assertEqual((cfg.microsoft.client_id, cfg.microsoft.tenant), ("", "common"))
        self.assertNotIn("paste-here-secretish", "\n".join(logs.output))

    def test_google_features_do_not_apply_to_a_microsoft_account(self) -> None:
        with self.assertLogs("briefing_reader.config", "WARNING"):
            cfg = self.load('[accounts.school]\nbackend = "microsoft"\nfeatures = ["calendar", "gmail_send"]\n')
        self.assertEqual(cfg.accounts["school"].features, ("calendar",))

    def test_personal_stays_google(self) -> None:
        with self.assertLogs("briefing_reader.config", "WARNING"):
            cfg = self.load('[accounts.personal]\nbackend = "microsoft"\n')
        self.assertEqual(cfg.accounts["personal"].backend, "google")


class ExecutorTests(ConfigTests):
    def test_builders_keep_outlook_apart_from_google(self) -> None:
        cfg = self.load(TOML)
        self.assertEqual(executor.google_aliases(cfg), ["personal"])
        self.assertEqual(executor.microsoft_aliases(cfg), ["school"])
        accounts = executor.build_microsoft_accounts(cfg)
        self.assertEqual(list(accounts), ["school"])
        account = accounts["school"]
        self.assertIsInstance(account, ms_auth.MicrosoftAccount)
        self.assertEqual((account.client_id, account.tenant), (CLIENT_ID, "consumers"))
        self.assertEqual(account.token_path, cfg.data_dir / "ms_token_school.json")
        self.assertFalse(account.is_signed_in())
        self.assertIsInstance(executor.build_outlook_calendars(cfg, accounts)["school"], outlook.OutlookCalendar)
        self.assertIsInstance(executor.build_outlook_readers(cfg, accounts)["school"], outlook.OutlookMailReader)
        self.assertNotIn("school", executor.build_accounts(cfg, persist=False))

    def test_every_card_for_an_outlook_account_is_refused(self) -> None:
        cfg = self.load(TOML)
        action = parse_action_line("RSVP: acct=school | event=abc123def456ghi789 | cal=primary | answer=yes | "
                                   "notify=all | title=Speaker series | at=2026-10-06 17:00-18:00")
        self.assertFalse(action.error, action.error)
        reason = executor.Executor([], cfg.accounts).readiness(action)
        self.assertEqual(reason, executor.OUTLOOK_NOTE.format(alias="school"))

    def test_ask_is_never_told_of_an_outlook_account(self) -> None:
        cfg = self.load(TOML)
        self.assertEqual(GoogleSources(cfg, {}).aliases(), ["personal"])


class SettingsTests(ConfigTests):
    def test_accounts_rows_say_outlook_and_read_only(self) -> None:
        cfg = self.load(TOML)
        rows = dict(settings_view.settings_rows(cfg, "accounts", {"personal": True, "school": True}))
        self.assertEqual(rows["personal"], "Connected")
        self.assertEqual(rows["school"], "Outlook - connected (read only)")
        rows = dict(settings_view.settings_rows(cfg, "accounts", {"school": False}))
        self.assertEqual(rows["school"], "Outlook - not connected")

    def test_connect_without_a_client_id_says_so_and_starts_nothing(self) -> None:
        calls: list[str] = []
        account = SimpleNamespace(is_configured=lambda: False, is_signed_in=lambda: False,
                                  sign_in=lambda: calls.append("sign_in"), disconnect=lambda: calls.append("off"))
        connect = outlook_ui.OutlookConnect({"school": account}, threaded=False)
        panel = outlook_ui.OutlookAccountsPanel(["school"])
        connect.attach_panel(panel)
        self.assertEqual(panel.notes["school"].text(), ms_auth.NO_CLIENT_ID_MESSAGE)   # said before any click
        panel.buttons["school"].click()
        self.assertEqual(calls, [])
        self.assertEqual(connect.note("school"), ms_auth.NO_CLIENT_ID_MESSAGE)
        self.assertEqual(panel.buttons["school"].text(), outlook_ui.CONNECT_TEXT)

    def test_connect_and_disconnect_report_back(self) -> None:
        state = {"in": False}
        calls: list[str] = []

        def sign_in() -> None:
            calls.append("sign_in")
            state["in"] = True

        def disconnect() -> None:
            calls.append("off")
            state["in"] = False

        account = SimpleNamespace(is_configured=lambda: True, is_signed_in=lambda: state["in"], sign_in=sign_in,
                                  disconnect=disconnect)
        connect = outlook_ui.OutlookConnect({"school": account}, threaded=False)
        panel = outlook_ui.OutlookAccountsPanel(["school"])
        connect.attach_panel(panel)
        heard: list[tuple[str, str]] = []
        connect.finished.connect(lambda alias, outcome, _message: heard.append((alias, outcome)))
        panel.buttons["school"].click()
        self.assertEqual(panel.states["school"].text(), outlook_ui.STATE_WORDS[True])
        self.assertEqual(panel.buttons["school"].text(), outlook_ui.DISCONNECT_TEXT)
        panel.buttons["school"].click()
        self.assertEqual(calls, ["sign_in", "off"])
        self.assertEqual(heard, [("school", outlook_ui.CONNECTED), ("school", outlook_ui.DISCONNECTED)])

    def test_a_failed_sign_in_shows_its_message(self) -> None:
        def sign_in() -> None:
            raise ms_auth.AccountAuthError("The Outlook sign-in for the school account was cancelled")

        account = SimpleNamespace(is_configured=lambda: True, is_signed_in=lambda: False, sign_in=sign_in,
                                  disconnect=lambda: None)
        connect = outlook_ui.OutlookConnect({"school": account}, threaded=False)
        panel = outlook_ui.OutlookAccountsPanel(["school"])
        connect.attach_panel(panel)
        panel.buttons["school"].click()
        self.assertIn("cancelled", panel.notes["school"].text())
        self.assertFalse(connect.busy("school"))


class Emitted:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def emit(self, *args: Any) -> None:
        self.calls.append(args)


class FakeCalendar:
    def __init__(self, events: list[Any] | Exception, signed_in: bool = True) -> None:
        self.events = events
        self.signed_in = signed_in
        self.asked = 0
        self.calendar_id = "primary"

    def is_signed_in(self) -> bool:
        return self.signed_in

    def is_configured(self) -> bool:
        return True

    def list_events(self, start: datetime, end: datetime, ids: Any = ()) -> list[Any]:
        self.asked += 1
        if isinstance(self.events, Exception):
            raise self.events
        return list(self.events)


START = datetime(2026, 11, 30, 0, 0, tzinfo=timezone.utc)


def event(title: str, hour: int) -> CalendarEvent:
    at = START + timedelta(hours=hour)
    return CalendarEvent(title, start=at, end=at + timedelta(hours=1))


class AgendaTests(NoNetwork):
    def worker(self, calendars: dict[str, Any], outlook_calendars: dict[str, Any]) -> tuple[Any, Emitted, Emitted]:
        agenda, instant = Emitted(), Emitted()
        bridge = SimpleNamespace(agendaResult=agenda, instantCalendar=instant)
        worker = ui._ActionWorker(None, calendars, None, bridge, threading.Event(), outlook=outlook_calendars)
        self.addCleanup(worker.stop)
        return worker, agenda, instant

    def job(self) -> Any:
        return ui._AgendaJob(1, START, START + timedelta(days=1), ("primary",))

    def test_outlook_events_join_the_google_ones_in_order(self) -> None:
        google = FakeCalendar([event("Google late", 15)])
        school = FakeCalendar([event("Outlook early", 9)])
        worker, agenda, _instant = self.worker({"personal": google}, {"school": school})
        worker._list(self.job())
        (request_id, events, problem, message), = agenda.calls
        self.assertEqual((request_id, problem, message), (1, "", ""))
        self.assertEqual([item.title for item in events], ["Outlook early", "Google late"])

    def test_an_outlook_account_not_signed_in_is_never_asked(self) -> None:
        school = FakeCalendar([event("Outlook", 9)], signed_in=False)
        worker, agenda, _instant = self.worker({"personal": FakeCalendar([event("Google", 10)])}, {"school": school})
        worker._list(self.job())
        self.assertEqual([item.title for item in agenda.calls[0][1]], ["Google"])
        self.assertEqual(school.asked, 0)

    def test_an_outlook_failure_keeps_the_google_rows(self) -> None:
        school = FakeCalendar(CalendarError("Outlook could not be read just now (500)"))
        worker, agenda, _instant = self.worker({"personal": FakeCalendar([event("Google", 10)])}, {"school": school})
        worker._list(self.job())
        _request, events, problem, _message = agenda.calls[0]
        self.assertEqual(([item.title for item in events], problem), (["Google"], ""))

    def test_outlook_alone_fills_the_day_without_a_google_calendar(self) -> None:
        worker, agenda, _instant = self.worker({}, {"school": FakeCalendar([event("Outlook", 9)])})
        worker._list(self.job())
        _request, events, problem, _message = agenda.calls[0]
        self.assertEqual(([item.title for item in events], problem), (["Outlook"], ""))

    def test_a_failed_google_read_keeps_the_older_rows_as_before(self) -> None:
        worker, agenda, _instant = self.worker({"personal": FakeCalendar(CalendarError("Google answered 500"))},
                                               {"school": FakeCalendar([event("Outlook", 9)])})
        worker._list(self.job())
        _request, events, problem, _message = agenda.calls[0]
        self.assertIsNone(events)                   # the app keeps its older rows (status "not updated")
        self.assertEqual(problem, ui._PROBLEM_ERROR)

    def test_instant_answers_read_the_outlook_calendar_too(self) -> None:
        worker, _agenda, instant = self.worker({"personal": FakeCalendar([event("Google", 10)])},
                                               {"school": FakeCalendar([event("Outlook", 9)])})
        worker._read_calendars(ui._InstantCalendarJob(2, START, START + timedelta(days=1)))
        (_request, events, problems), = instant.calls
        self.assertEqual([item.title for item in events["school"]], ["Outlook"])
        self.assertEqual(problems, {})

    def test_the_day_view_rows_show_an_outlook_event(self) -> None:
        from briefing_reader.outlook import event_from

        item = {"id": "x", "subject": "Seminar", "isAllDay": False,
                "start": {"dateTime": "2026-11-30T17:00:00.0000000", "timeZone": "UTC"},
                "end": {"dateTime": "2026-11-30T18:00:00.0000000", "timeZone": "UTC"}, "location": {}}
        found = event_from(item)
        now = found.start - timedelta(hours=2)
        _label, start, end = ui.agenda_window(now, 18)
        rows = ui._agenda_rows([found], now, start, end, True)
        self.assertTrue(any("Seminar" in str(row) for row in rows))


UNREAD = {"id": "m1", "threadId": "c1", "labelIds": ["INBOX", "UNREAD"], "snippet": "secret plan details",
          "internalDate": str(int((START - timedelta(hours=3)).timestamp() * 1000)),
          "payload": {"headers": [{"name": "From", "value": "Ana Lee <ana@example.edu>"},
                                  {"name": "Subject", "value": "Lab results"}]}}


class FakeReader:
    def __init__(self, messages: list[Any] | Exception, problem: str = "") -> None:
        self.messages, self.problem, self.asked = messages, problem, 0

    def available(self) -> tuple[str, str]:
        return (self.problem, "why") if self.problem else ("", "")

    def unread(self) -> list[Any]:
        self.asked += 1
        if isinstance(self.messages, Exception):
            raise self.messages
        return list(self.messages)


class FollowUpTests(NoNetwork):
    """The unread frame, Outlook rows with Google signed out, the LIVE title."""

    worker = AgendaTests.worker
    job = AgendaTests.job

    def mail_worker(self, readers: dict[str, Any]) -> tuple[Any, Emitted]:
        agenda, mail = Emitted(), Emitted()
        bridge = SimpleNamespace(agendaResult=agenda, outlookMail=mail)
        worker = ui._ActionWorker(None, {"personal": FakeCalendar([])}, None, bridge, threading.Event(),
                                  outlook={}, outlook_mail=readers)
        self.addCleanup(worker.stop)
        return worker, mail

    def test_the_agenda_read_brings_the_unread_headers(self) -> None:
        signed_out = FakeReader([UNREAD], problem="signedout")
        broken = FakeReader(CalendarError("Outlook could not be read just now (500)"))
        worker, mail = self.mail_worker({"school": FakeReader([UNREAD]), "lab": signed_out, "old": broken})
        worker._list(self.job())
        (found,), = mail.calls
        self.assertEqual(found["school"], [UNREAD])
        self.assertEqual(found["lab"], [])          # not connected: nothing held, never asked
        self.assertEqual(signed_out.asked, 0)
        self.assertIsNone(found["old"])             # a failed read keeps what the app holds

    def test_the_unread_frame_shows_sender_subject_and_age_only(self) -> None:
        from briefing_reader.board import frames, protocol

        built = frames.from_unread("school", [UNREAD], frame_id="f1", now=START)
        frame = built.frame
        protocol.check_message(frame)
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]), ("mail_list", "Unread · school", "1 unread"))
        (row,) = frame["body"]["rows"]
        self.assertEqual((row["sender"], row["subject"], row["time"], row["unread"]),
                         ("Ana Lee", "Lab results", "3h", True))
        self.assertNotIn("secret plan", str(frame))
        self.assertNotIn("ana@example.edu", str(frame))
        self.assertEqual(frames.from_unread("school", [], frame_id="f2").messages, ())

    def test_the_now_group_has_one_mail_frame_per_account_with_unread_mail(self) -> None:
        from briefing_reader import canvas_now

        host = SimpleNamespace(_outlook_unread={"school": [UNREAD], "lab": []})
        entries = canvas_now.NowController.mail_entries(SimpleNamespace(host=host, _now=lambda: START))
        self.assertEqual(list(entries), ["mail-school"])
        self.assertEqual(entries["mail-school"]("f9").frame["title"], "Unread · school")
        self.assertEqual(canvas_now.NowController.mail_entries(
            SimpleNamespace(host=SimpleNamespace(), _now=lambda: START)), {})

    def test_google_signed_out_still_brings_the_outlook_rows_with_its_problem(self) -> None:
        from briefing_reader.gcal import CalendarNotSignedIn

        worker, agenda, _instant = self.worker({"personal": FakeCalendar(CalendarNotSignedIn("Not signed in"))},
                                               {"school": FakeCalendar([event("Outlook", 9)])})
        worker._list(self.job())
        _request, events, problem, _message = agenda.calls[0]
        self.assertEqual([item.title for item in events], ["Outlook"])
        self.assertEqual(problem, ui._PROBLEM_SIGNED_OUT)

    def test_the_day_view_shows_rows_and_googles_connect_line_together(self) -> None:
        from briefing_reader import hud

        panel = hud.AgendaPanel()
        panel.set_events([hud.AgendaRowInfo("9:00 AM", "Outlook")])
        panel.show_note("Connect Google Calendar to see your day", link="Connect")
        self.assertFalse(panel._message_box.isHidden())
        self.assertFalse(panel.link_button.isHidden())
        self.assertEqual(len(panel._event_rows), 1)
        self.assertFalse(panel._event_rows[0].isHidden())

    def test_the_live_title_names_the_calendars_read(self) -> None:
        self.assertEqual(ui.agenda_read_title(["personal"]), "Agenda: read the personal calendar (read only)")
        self.assertEqual(ui.agenda_read_title(["personal", "school"]),
                         "Agenda: read the calendars (personal, school) (read only)")
        self.assertEqual(ui.agenda_read_title([]), "Agenda: read the personal calendar (read only)")


class UnreadReadTests(NoNetwork):
    def test_unread_asks_graph_for_unread_inbox_headers_newest_first(self) -> None:
        from tests.test_microsoft_auth import FakeResponse, FakeSession
        from tests.test_microsoft_readers import MESSAGE

        session = FakeSession(FakeResponse(200, {"value": [MESSAGE, dict(MESSAGE, id="read", isRead=True)]}))
        self.write_token()
        reader = outlook.OutlookMailReader(self.account(session))
        self.assertEqual([item["id"] for item in reader.unread()], [MESSAGE["id"]])
        (_url, params, _headers), = session.gets
        self.assertRegex(params["$filter"], r"^receivedDateTime ge \S+Z and isRead eq false$")
        self.assertEqual(params["$orderby"], "receivedDateTime desc")
        self.assertEqual(params["$top"], str(outlook.UNREAD_MAX))


if __name__ == "__main__":
    unittest.main()
