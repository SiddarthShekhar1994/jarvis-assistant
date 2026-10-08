"""Fakes for the Ask Jarvis tests: a runner that never starts claude.exe, recorded stream-json runs,
a fake clock, fake Google sources and a fake Gmail reader. Every value is invented."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from briefing_reader.ask import cli
from briefing_reader.ask.context import AccountInfo
from briefing_reader.gcal import EventBrief, Guest
from briefing_reader.gmail import MailReadError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ask"
HELP_TEXT = (FIXTURES / "help_2.1.293.txt").read_text(encoding="ascii")
PDT = timezone(timedelta(hours=-7), "PDT")
NOW = datetime(2026, 10, 7, 9, 30, tzinfo=PDT)        # a Wednesday
AUTH_OK = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                      "email": "owner@example.edu", "orgName": "Example", "configDirectory": "C:/Users/example/.claude"})


def stream(name: str) -> list[str]:
    return (FIXTURES / f"{name}.jsonl").read_text(encoding="ascii").splitlines()


def init_event(**changes: Any) -> dict[str, Any]:
    return json.loads(stream("success")[0]) | changes


def result_event(structured: Any = None, **changes: Any) -> dict[str, Any]:
    event = json.loads(stream("success")[-1])
    event.pop("structured_output", None)
    if structured is not None:
        event["structured_output"] = structured
    event.update(changes)
    return event


def plan_stream(plan: Mapping[str, Any]) -> list[str]:
    """A recorded-shape run whose StructuredOutput is ``plan``."""
    lines = stream("success")
    call = json.loads(lines[1])
    call["message"]["content"][0]["input"] = dict(plan)
    return [lines[0], json.dumps(call), lines[2], json.dumps(result_event(dict(plan)))]


class FakeClock:
    """time.monotonic stand-in that moves only when a fake process waits."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeProcess:
    def __init__(self, lines: Sequence[str], *, clock: FakeClock | None = None, exit_code: int = 0,
                 stderr: str = "", hang: bool = False, cancel_after: int | None = None,
                 cancel: threading.Event | None = None, too_long_at: int | None = None) -> None:
        self.lines = list(lines)
        self.clock = clock
        self.exit_code = exit_code
        self.stderr = stderr
        self.hang = hang
        self.killed = False
        self.closed = False
        self.reads = 0
        self.cancel_after = cancel_after
        self.cancel = cancel
        self.too_long_at = too_long_at

    def read_line(self, timeout: float) -> str | None:
        self.reads += 1
        if self.cancel_after is not None and self.reads > self.cancel_after and self.cancel is not None:
            self.cancel.set()
        if self.killed:
            return None
        if self.too_long_at is not None and self.reads > self.too_long_at:
            raise cli.OutputTooLong()
        if self.lines:
            return self.lines.pop(0)
        if self.hang:
            if self.clock is not None:
                self.clock.t += max(timeout, 0.01)
            return ""
        return None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float) -> int | None:
        return -9 if self.killed else self.exit_code

    def stderr_tail(self) -> str:
        return self.stderr

    def close(self) -> None:
        self.closed = True


@dataclass
class Started:
    argv: list[str]
    stdin: str
    env: dict[str, str]
    cwd: Path


class FakeRunner:
    """Answers --version / --help / auth status like claude.exe 2.1.293; each start() takes the next
    queued process (FakeProcess or a list of lines). Records everything; never runs anything."""

    def __init__(self, *runs: Any, help_text: str = HELP_TEXT, version: str = "2.1.293 (Claude Code)",
                 auth: str = AUTH_OK, clock: FakeClock | None = None) -> None:
        self.runs = list(runs)
        self.help_text = help_text
        self.version = version
        self.auth = auth
        self.clock = clock
        self.started: list[Started] = []
        self.quick: list[tuple[list[str], dict[str, str]]] = []
        self.processes: list[FakeProcess] = []

    def start(self, argv: Sequence[str], *, stdin_text: str, env: Mapping[str, str], cwd: Path) -> FakeProcess:
        self.started.append(Started(list(argv), stdin_text, dict(env), Path(cwd)))
        if not self.runs:
            raise AssertionError("an unexpected planner run")
        item = self.runs.pop(0)
        process = item if isinstance(item, FakeProcess) else FakeProcess(item, clock=self.clock)
        self.processes.append(process)
        return process

    def run(self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout: float) -> cli.Completed:
        self.quick.append((list(argv), dict(env)))
        tail = list(argv[1:])
        if tail == ["--version"]:
            return cli.Completed(0, self.version + "\n", "")
        if tail == ["--help"]:
            return cli.Completed(0, self.help_text, "")
        if tail == ["auth", "status", "--json"]:
            return cli.Completed(0 if '"loggedIn": true' in self.auth else 1, self.auth, "")
        raise AssertionError(f"unexpected quick run {tail}")


def fake_exe(root: Path) -> cli.ClaudeExe:
    path = root / "bin" / "claude.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ fake")
    return cli.ClaudeExe(path, cli.SOURCE_STANDALONE)


def brief(event_id: str, title: str, start: datetime, minutes: int = 60, *, organizer_self: bool = True,
          guests: Sequence[tuple[str, str]] = (), calendar_id: str = "primary", recurring: bool = False,
          organizer: tuple[str, str] = ("", "")) -> EventBrief:
    return EventBrief(event_id, calendar_id, title, start=start, end=start + timedelta(minutes=minutes),
                      organizer_self=organizer_self, organizer_name=organizer[0], organizer_email=organizer[1],
                      guests=tuple(Guest(email, name, "needsAction") for name, email in guests), recurring=recurring)


def all_day(event_id: str, title: str, first: date, last: date | None = None) -> EventBrief:
    return EventBrief(event_id, "primary", title, all_day_start=first, all_day_end=last or first)


class FakeReader:
    """GmailReader stand-in: ``threads`` maps thread id -> users.threads.get JSON."""

    def __init__(self, alias: str, threads: Mapping[str, dict] | None = None, *, problem: tuple[str, str] = ("", ""),
                 found: Sequence[str] = (), error: Exception | None = None) -> None:
        self.alias = alias
        self.threads = dict(threads or {})
        self.problem = problem
        self.found = list(found)
        self.error = error
        self.searches: list[str] = []
        self.reads: list[str] = []

    def available(self) -> tuple[str, str]:
        return self.problem

    def search(self, query: str, *, max_threads: int = 3) -> list[str]:
        self.searches.append(query)
        if self.error is not None:
            raise self.error
        return self.found[:max_threads]

    def thread(self, thread_id: str) -> dict:
        self.reads.append(thread_id)
        if thread_id not in self.threads:
            raise MailReadError("Gmail has no such thread", status=404)
        return self.threads[thread_id]


@dataclass
class FakeSources:
    accounts_list: list[AccountInfo] = field(default_factory=list)
    briefs: dict[str, list[EventBrief]] = field(default_factory=dict)
    readers: dict[str, FakeReader] = field(default_factory=dict)
    zone: str = "America/Los_Angeles"
    calls: list[tuple[str, datetime, datetime, tuple[str, ...]]] = field(default_factory=list)
    failing: dict[str, str] = field(default_factory=dict)

    def accounts(self) -> list[AccountInfo]:
        return list(self.accounts_list)

    def time_zone(self) -> str:
        return self.zone

    def event_briefs(self, alias: str, start: datetime, end: datetime,
                     calendars: Sequence[str]) -> tuple[list[EventBrief], str]:
        self.calls.append((alias, start, end, tuple(calendars)))
        if alias in self.failing:
            return [], self.failing[alias]
        return list(self.briefs.get(alias, [])), "yes"

    def reader(self, alias: str) -> FakeReader | None:
        return self.readers.get(alias)


def b64(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_message(gmail_id: str, *, sender: str, to: str, subject: str, body: str, msgid: str, at_ms: int,
                  html: str | None = None, cc: str = "", labels: Sequence[str] = ("INBOX",),
                  attachment: str | None = None) -> dict[str, Any]:
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to}, {"name": "Subject", "value": subject},
               {"name": "Message-ID", "value": msgid}, {"name": "Date", "value": "Mon, 5 Oct 2026 14:03:00 -0700"}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    parts: list[dict[str, Any]] = [{"mimeType": "text/plain", "headers": [{"name": "Content-Type",
                                                                          "value": "text/plain; charset=utf-8"}],
                                    "body": {"data": b64(body), "size": len(body)}}]
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    if attachment is not None:
        parts.append({"mimeType": "text/plain", "filename": "notes.txt",
                      "headers": [{"name": "Content-Disposition", "value": "attachment; filename=notes.txt"}],
                      "body": {"data": b64(attachment), "attachmentId": "att1"}})
    payload = {"mimeType": "multipart/alternative", "headers": headers, "parts": parts}
    return {"id": gmail_id, "threadId": "thr0000001", "labelIds": list(labels), "internalDate": str(at_ms),
            "payload": payload}


def gmail_thread(thread_id: str, messages: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"id": thread_id, "messages": [dict(message, threadId=thread_id) for message in messages]}


Clock = Callable[[], datetime]
