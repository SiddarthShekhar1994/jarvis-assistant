"""Builders for the app-side standing tests: definitions, run files as the runner writes them, a fake Gmail reader
whose threads a test sets, and an account's sign-in as the app holds it. Every name, address, id and text is
invented."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from briefing_reader.actions import parse_action_line
from briefing_reader.standing import cards
from briefing_reader.standing.model import agent_from, rule_from
from briefing_reader.standing.store import AppState, Definitions, RunFiles, StandingFiles, new_run_id, stamp
from tests.ask_fakes import gmail_message, gmail_thread
from tests.ui_fakes import NOW

STANDING_ON = "\n[standing]\nenabled = true\n"
RULE_ID = "r-0000000000a1"
AGENT_ID = "a-0000000000b1"
PAT = "pat.example@example.org"
PAT_NAME = "Pat Example"
OWN_WORK = "me@example.edu"
THREAD = "thr00000a1"
GMID = "msg00000a1"
MSGID = "invoice-1043@example.org"
SUBJECT = "Invoice 1043 from Example Billing"
RULE_NAME = "Invoice watch"
AGENT_NAME = "Morning mail"
SENTENCE = "When Example Billing sends an invoice, make me a to-do to pay it"
REPORT = "Two mails from Pat: the venue is booked and the invoice is due Friday."


def rule_data(**changes: Any) -> dict[str, Any]:
    data = {"id": RULE_ID, "name": RULE_NAME, "sentence": SENTENCE, "account": "work",
            "query": "from:billing@example.org subject:invoice", "question": "Is this an invoice to pay?",
            "fields": [{"name": "amount", "type": "money", "hint": "the amount due"}],
            "action": {"kind": "todo", "title": "Pay {amount}"}, "status": "live",
            "dry_run": {"at": "2026-10-03T10:00:00-07:00", "matched": 12, "window_days": 30, "judged": 12, "yes": 3},
            "created": "2026-10-03T10:00:00-07:00"}
    data.update(changes)
    return data


def agent_data(**changes: Any) -> dict[str, Any]:
    data = {"id": AGENT_ID, "name": AGENT_NAME, "words": "every morning tell me what Pat sent and draft replies",
            "trigger": {"kind": "time", "times": ["07:30"], "weekdays": []},
            "mail": [{"account": "work", "query": f"from:{PAT}"}], "proposes": ["todo", "reply"],
            "report": "every_run", "status": "live", "created": "2026-10-03T10:00:00-07:00"}
    data.update(changes)
    return data


def files_of(config: Any) -> StandingFiles:
    return StandingFiles(config.data_dir)


def save_jobs(config: Any, agents: Sequence[Mapping[str, Any]] = (), rules: Sequence[Mapping[str, Any]] = ()) -> None:
    Definitions(files_of(config)).save([agent_from(item) for item in agents], [rule_from(item) for item in rules])


def consent(config: Any) -> AppState:
    state = AppState(files_of(config))
    state.set_consent(True, NOW)
    return state


def source(**changes: Any) -> dict[str, Any]:
    data = {"account": "work", "message": GMID, "thread": THREAD, "msgid": MSGID, "from": PAT, "name": PAT_NAME,
            "subject": SUBJECT, "received": stamp(NOW - timedelta(minutes=30))}
    data.update(changes)
    return data


def todo_record(job_id: str = RULE_ID, job_kind: str = "rule", key: str = GMID, *, title: str = "Pay $40.00",
                due: str = "2026-10-08") -> dict[str, Any]:
    line = cards.todo_line(title, due, account="work")
    card = cards.with_job(parse_action_line(line), job_kind=job_kind, job_id=job_id, key=key)
    return cards.record(card, line=line, job={"kind": job_kind, "id": job_id}, key=key, source=source())


def reply_record(job_id: str = AGENT_ID, job_kind: str = "agent", *, to: Sequence[str] = (PAT,),
                 body: str = "Thanks Pat, the venue works for us.") -> dict[str, Any]:
    line = cards.reply_line("work", THREAD, MSGID, GMID, list(to), SUBJECT, body)
    card = cards.with_job(parse_action_line(line), job_kind=job_kind, job_id=job_id, key=GMID)
    return cards.record(card, line=line, job={"kind": job_kind, "id": job_id}, key=GMID,
                        source=source() if job_kind == "rule" else {})


def card_receipt(record: Mapping[str, Any], number: int = 0, **changes: Any) -> dict[str, Any]:
    receipt = {"id": f"{GMID}-{number}", "kind": "card", "card": record["id"], "title": "Pay $40.00",
               "card_kind": record["kind"], **source()}
    receipt.update(changes)
    return receipt


def tell_receipt(number: int = 0, **values: str) -> dict[str, Any]:
    return {"id": f"{GMID}-{number}", "kind": "tell", "values": dict(values or {"amount": "$40.00"}),
            "refused": "", **source()}


def write_run(config: Any, *, job_kind: str = "rule", job_id: str = RULE_ID, job_name: str = RULE_NAME,
              started: datetime | None = None, status: str = "ok", run_cards: Sequence[Mapping[str, Any]] = (),
              receipts: Sequence[Mapping[str, Any]] = (), report: str = "", needs_owner: bool = True,
              run_id: str | None = None, **parts: Any) -> str:
    started = started or (NOW - timedelta(minutes=10))
    run_id = run_id or new_run_id()
    record = {"id": run_id, "job": {"kind": job_kind, "id": job_id, "name": job_name}, "trigger": "mail",
              "origin": "task", "started": stamp(started), "ended": stamp(started + timedelta(seconds=40)),
              "status": status, "cards": list(run_cards), "receipts": list(receipts), "needs_owner": needs_owner,
              "counts": {"mails": 1, "cards": len(run_cards)}, **parts}
    if report:
        record["report"] = report
        record["summary"] = "Two mails from Pat"
    path = RunFiles(files_of(config)).write(record, started)
    assert path is not None
    return run_id


def run_paths(config: Any) -> list[Path]:
    return RunFiles(files_of(config)).paths()


class ThreadReader:
    """A Gmail reader with only what the click-time Reply check uses: thread(id) and own_addresses()."""

    def __init__(self, threads: Mapping[str, dict[str, Any]] | None = None, own: Sequence[str] = (OWN_WORK,),
                 error: Exception | None = None) -> None:
        self.threads = dict(threads or {})
        self.own = list(own)
        self.error = error
        self.calls: list[str] = []

    def thread(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(thread_id)
        if self.error is not None:
            raise self.error
        if thread_id not in self.threads:
            raise LookupError("not found")
        return self.threads[thread_id]

    def own_addresses(self) -> list[str]:
        return list(self.own)


def pat_message(gmail_id: str = GMID, *, at_ms: int = 1_791_000_000_000) -> dict[str, Any]:
    return gmail_message(gmail_id, sender=f"{PAT_NAME} <{PAT}>", to=OWN_WORK, subject=SUBJECT,
                         body="The venue is booked. Invoice attached.", msgid=f"<{MSGID}>", at_ms=at_ms)


def own_message(gmail_id: str = "msg00000a2", *, at_ms: int = 1_791_000_100_000) -> dict[str, Any]:
    return gmail_message(gmail_id, sender=f"Me <{OWN_WORK}>", to=PAT, subject=f"Re: {SUBJECT}",
                         body="Thanks, will pay Friday.", msgid="<reply-1@example.edu>", at_ms=at_ms,
                         labels=("SENT",))


def thread_of(*messages: dict[str, Any]) -> dict[str, Any]:
    return gmail_thread(THREAD, list(messages))


class CalendarAccount:
    """The app's Google account for an alias (google_auth.GoogleAccount's surface standing uses), signed in when the
    alias's fake calendar (tests.ui_fakes.FakeCalendar) is: in the app the two share one sign-in."""

    def __init__(self, calendar: Any, sub: str, email: str = OWN_WORK) -> None:
        self.calendar, self.sub, self.email = calendar, sub, email

    def is_signed_in(self, feature: str | None = None) -> bool:
        return bool(self.calendar.signed_in)

    def binding(self) -> Any:
        return type("Binding", (), {"sub": self.sub, "email": self.email})() if self.calendar.signed_in else None


def hold_sign_in(calendar: Any, error: Exception | None = None) -> threading.Event:
    """``calendar``'s browser sign-in waits for the event (the owner in the browser), then signs in, or raises
    ``error`` as gcal does (cancelled, failed). It runs on the app's action worker, as every sign-in does; no browser
    opens."""
    done = threading.Event()
    sign_in = calendar.sign_in

    def held() -> None:
        done.wait(5)
        if error is None:
            sign_in()
            return
        calendar._note("sign_in")
        raise error

    calendar.sign_in = held
    return done
