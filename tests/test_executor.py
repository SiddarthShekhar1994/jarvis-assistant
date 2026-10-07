"""Tests for briefing_reader.executor: backend choice, readiness, Calendar kinds, edits, one job
of the action worker (running before the call, unknown after a crash, never retried), accounts
and calendars from config.toml, and what the cards say.

Every Google call goes to an in-memory FakeCalendar; nothing touches the network, a browser or
the owner's data folder. Names, addresses and ids are invented.
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from briefing_reader import actions, executor, gcal
from briefing_reader.actions import (
    CALENDAR,
    INTERRUPTED_MESSAGE,
    STATUS_CREATED,
    STATUS_EXISTS,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SENT,
    STATUS_UNKNOWN,
    ActionStore,
    ProposedAction,
    parse_action_line,
)
from briefing_reader.config import AccountConfig, load_config
from briefing_reader.executor import (
    CALENDAR_SETUP_NOTE,
    COMPOSIO_NOTE,
    NOT_SAVED_MESSAGE,
    NOTHING_TO_DO,
    OUTCOME_FAILED,
    OUTCOME_UNKNOWN,
    STAGE_SIGNED_IN,
    STAGE_SIGNIN,
    STAGE_WORKING,
    ActionEdit,
    CalendarBackend,
    EditError,
    EventCheck,
    ExecError,
    ExecResult,
    Executor,
    RunOutcome,
    account_of,
    apply_edit,
    build_accounts,
    build_calendars,
    check_event,
    check_failure,
    run_action,
    sign_in_note,
)
from briefing_reader.gcal import (
    CalendarAuthError,
    CalendarError,
    CalendarNotSignedIn,
    CalendarUnknownOutcome,
    ChangeResult,
    EventDetails,
    EventGone,
    EventResult,
    GoogleCalendar,
    NotAllowed,
)
from briefing_reader.google_auth import (
    PROBLEM_BLOCKED,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
)

DOT = " " + chr(0xB7) + " "
TODAY = date(2026, 10, 4)
EDT = timezone(timedelta(hours=-4), "EDT")
EVENT_LINK = "https://www.google.com/calendar/event?eid=ZmFrZQ"
EXECUTOR_LOGGER = "briefing_reader.executor"

PAIRS = {
    "RSVP": (("acct", "work"), ("event", "abc123def456ghi789"), ("cal", "primary"), ("answer", "yes"),
             ("notify", "all"), ("title", "Speaker series"), ("at", "2026-10-06 17:00-18:00"),
             ("due", "2026-10-06"), ("link", "https://calendar.google.com/calendar/event?eid=ZXhhbXBsZQ"),
             ("body", "")),
    "Move": (("acct", "work"), ("event", "abc123def456ghi789_20261008T190000Z"), ("cal", "primary"),
             ("when", "2026-10-08 14:00-15:00"), ("notify", "all"), ("title", "Project sync"),
             ("at", "2026-10-08 12:00-13:00"), ("link", ""), ("body", "Moving to 2 PM so everyone can join.")),
    "Cancel": (("acct", "personal"), ("event", "zyx987wvu654tsr321"), ("cal", "primary"), ("notify", "all"),
               ("title", "Study group"), ("at", "2026-10-09 18:00-19:00"), ("link", ""), ("body", "")),
    "Todo": (("title", "Work on Problem set 3"), ("due", "2026-10-07 23:59"), ("block", "2026-10-06 19:00-21:00"),
             ("acct", "work"), ("link", "https://example.instructure.com/courses/1/assignments/2")),
    "Reply": (("acct", "work"), ("thread", "18c0ffee00000001"), ("msgid", "CAExample0001@mail.example.com"),
              ("gmid", ""), ("to", "ana@example.edu, ben@example.edu"), ("cc", ""),
              ("subject", "Re: Thursday noon meeting"), ("replied", "no"), ("due", ""), ("link", ""),
              ("body", "Hi both")),
}
CHESS = "Calendar: Chess Club Weekly Meeting | 2026-10-09 15:00-16:00"


def example(label: str, **changes: str) -> ProposedAction:
    items = [f"{key}={changes.get(key, value)}" for key, value in PAIRS[label]]
    action = parse_action_line(f"{label}: " + " | ".join(items))
    assert not action.error, action.error
    return action


def details(*, organizer_self: bool = True, me: str = "needsAction", guests_can_modify: bool = False,
            series: bool = False, instance: bool = False, all_day: bool = False,
            organizer: str = "", title: str = "Project sync",
            start: datetime | None = datetime(2026, 10, 8, 12, 0, tzinfo=EDT)) -> EventDetails:
    start = None if all_day else start
    end = None if start is None else start + timedelta(hours=1)
    return EventDetails(
        event_id="abc123def456ghi789", calendar_id="primary", title=title, start=start, end=end,
        all_day_start=date(2026, 10, 8) if all_day else None, all_day_end=date(2026, 10, 8) if all_day else None,
        status="confirmed", organizer_self=organizer_self, guests_can_modify=guests_can_modify,
        self_email="you@example.edu" if me else "", self_response=me, attendee_count=3,
        recurring_instance=instance, series=series, time_zone="America/New_York", link=EVENT_LINK,
        organizer=organizer, organizer_email="" if organizer_self else "ana@example.edu")


SPEAKER = {"title": "Speaker Series: Dr. Example", "start": datetime(2026, 10, 6, 17, 0, tzinfo=EDT)}


class FakeCalendar:
    """gcal.GoogleCalendar's surface as used by CalendarBackend; records every call and, with
    ``store``, what actions.json said (on disk) at the moment of each change."""

    def __init__(self, *, configured: bool = True, signed_in: bool = True, store: ActionStore | None = None,
                 watch: str = "") -> None:
        self.configured = configured
        self.signed = signed_in
        self.store = store
        self.watch = watch
        self.calls: list[tuple[Any, ...]] = []
        self.on_disk_at_change: list[str | None] = []
        self.sign_in_error: BaseException | None = None
        self.outcomes: dict[str, Any] = {}

    # -- no network --
    def is_configured(self) -> bool:
        self.calls.append(("is_configured",))
        return self.configured

    def is_signed_in(self) -> bool:
        self.calls.append(("is_signed_in",))
        return self.signed

    # -- the browser --
    def sign_in(self) -> None:
        self.calls.append(("sign_in", self._on_disk()))
        if self.sign_in_error is not None:
            raise self.sign_in_error
        self.signed = True

    # -- Google --
    def _call(self, name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        if name != "get_event":
            self.on_disk_at_change.append(self._on_disk())
        outcome = self.outcomes.get(name, default)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def create_event(self, action: ProposedAction) -> EventResult:
        return self._call("create_event", EventResult("evt1", EVENT_LINK, existed=False), action)

    def respond(self, event_id: str, answer: str, **kwargs: Any) -> ChangeResult:
        return self._call("respond", ChangeResult(event_id, EVENT_LINK, already=False), event_id, answer, **kwargs)

    def move(self, event_id: str, start: datetime, end: datetime, **kwargs: Any) -> ChangeResult:
        return self._call("move", ChangeResult(event_id, EVENT_LINK, already=False), event_id, start, end, **kwargs)

    def cancel(self, event_id: str, **kwargs: Any) -> ChangeResult:
        return self._call("cancel", ChangeResult(event_id, "", already=False), event_id, **kwargs)

    def get_event(self, event_id: str, **kwargs: Any) -> EventDetails:
        return self._call("get_event", details(), event_id, **kwargs)

    # -- helpers --
    def _on_disk(self) -> str | None:
        if self.store is None or not self.store.path.exists():
            return None
        entry = json.loads(self.store.path.read_text(encoding="utf-8")).get(self.watch)
        return entry["status"] if entry else None

    def changes(self) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] in ("create_event", "respond", "move", "cancel")]

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


ACCOUNTS = {"personal": AccountConfig("personal"), "work": AccountConfig("work")}


class ExecutorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ActionStore(self.root / "actions.json")
        self.personal = FakeCalendar(store=self.store)
        self.work = FakeCalendar(store=self.store)
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=EDT)
        self.stages: list[str] = []

    def executor(self, accounts: dict[str, AccountConfig] | None = None,
                 calendars: dict[str, Any] | None = None) -> Executor:
        if calendars is None:
            calendars = {"personal": self.personal, "work": self.work}
        backend = CalendarBackend(calendars, now=lambda: self.now)
        return Executor([backend], ACCOUNTS if accounts is None else accounts)

    def watch(self, action: ProposedAction) -> ProposedAction:
        self.personal.watch = self.work.watch = action.id
        return action


# --------------------------------------------------------------------------
# Choosing the backend, readiness
# --------------------------------------------------------------------------

class SelectionTests(ExecutorTestCase):
    def test_account_of(self) -> None:
        self.assertEqual(account_of(example("RSVP")), "work")
        self.assertEqual(account_of(example("Cancel")), "personal")
        self.assertEqual(account_of(parse_action_line(CHESS)), "personal")
        self.assertEqual(account_of(example("Todo")), "personal")   # acct= of a to-do does not count yet

    def test_backend_by_kind_and_account(self) -> None:
        run = self.executor({"work": AccountConfig("work"), "lab": AccountConfig("lab", backend="composio")})
        self.assertIsInstance(run.backend_for(example("RSVP")), CalendarBackend)
        self.assertIsInstance(run.backend_for(parse_action_line(CHESS)), CalendarBackend)   # no [accounts] needed
        self.assertIsNone(run.backend_for(example("RSVP", acct="lab")))       # no Composio backend built
        self.assertIsNone(run.backend_for(example("RSVP", acct="school")))    # no such account
        self.assertIsNone(run.backend_for(example("Reply")))                  # nothing sends email yet

    def test_ready(self) -> None:
        run = self.executor()
        for action in (example("RSVP"), example("Move"), example("Cancel"), example("Todo"),
                       parse_action_line(CHESS)):
            with self.subTest(kind=action.kind):
                self.assertEqual(run.readiness(action), "")
        self.assertEqual(self.personal.names() + self.work.names(), ["is_configured"] * 5)

    def test_readiness_messages(self) -> None:
        accounts = {"work": AccountConfig("work", features=()), "lab": AccountConfig("lab", backend="composio"),
                    "personal": AccountConfig("personal")}
        run = self.executor(accounts)
        self.assertEqual(run.readiness(example("RSVP", acct="school")),
                         'No account named "school" in config.toml [accounts]')
        self.assertEqual(run.readiness(example("RSVP", acct="lab")), COMPOSIO_NOTE)
        self.assertEqual(run.readiness(example("Move")),
                         "The work account is not set up for calendar actions (config.toml [accounts.work] features)")
        self.assertEqual(run.readiness(example("Reply")), NOTHING_TO_DO)
        self.assertEqual(run.readiness(example("Todo", block="")), NOTHING_TO_DO)
        self.assertEqual(run.readiness(parse_action_line("Calendar: Dentist | sometime")), NOTHING_TO_DO)

    def test_calendar_not_set_up(self) -> None:
        self.assertEqual(self.executor(calendars={"personal": self.personal}).readiness(example("RSVP")),
                         CALENDAR_SETUP_NOTE)
        self.work.configured = False
        self.assertEqual(self.executor().readiness(example("RSVP")), CALENDAR_SETUP_NOTE)
        self.assertEqual(self.executor(calendars={}).readiness(parse_action_line(CHESS)), CALENDAR_SETUP_NOTE)

    def test_not_ready_is_never_sent(self) -> None:
        run = self.executor({"personal": AccountConfig("personal")})
        for call in (run.execute, run.prepare, run.peek):
            with self.subTest(call=call.__name__), self.assertRaises(ExecError) as ctx:
                call(example("RSVP"))
            self.assertEqual((ctx.exception.problem, ctx.exception.outcome), (PROBLEM_SETUP, OUTCOME_FAILED))
        self.assertEqual(self.work.calls, [])

    def test_signed_in(self) -> None:
        run = self.executor()
        self.work.signed = False
        self.assertFalse(run.signed_in(example("RSVP")))
        self.assertTrue(run.signed_in(example("Cancel")))
        self.assertFalse(run.signed_in(example("RSVP", acct="school")))

    def test_a_broken_check_counts_as_not_ready(self) -> None:
        self.work.is_configured = mock.Mock(side_effect=OSError("disk"))   # type: ignore[method-assign]
        with self.assertLogs(EXECUTOR_LOGGER, level="DEBUG"):
            self.assertEqual(self.executor().readiness(example("RSVP")), CALENDAR_SETUP_NOTE)


# --------------------------------------------------------------------------
# Carrying out each kind
# --------------------------------------------------------------------------

class CalendarAndTodoTests(ExecutorTestCase):
    """A Calendar event and a Todo's block give the same results as before the executor."""

    def test_calendar_event(self) -> None:
        action = parse_action_line(CHESS)
        result = self.executor().execute(action, on_stage=self.stages.append)
        self.assertEqual(result, ExecResult(STATUS_CREATED, EVENT_LINK))
        self.assertEqual(self.personal.changes(), [("create_event", (action,), {})])
        self.assertEqual(self.stages, [STAGE_WORKING])
        self.personal.outcomes["create_event"] = EventResult("old1", EVENT_LINK, existed=True)
        self.assertEqual(self.executor().execute(action).status, STATUS_EXISTS)
        self.assertEqual(self.work.calls, [])

    def test_todo_block_goes_to_the_personal_calendar_with_the_todo_id(self) -> None:
        todo = example("Todo")
        self.executor().execute(todo)
        ((_, (event,), _),) = self.personal.changes()
        self.assertEqual((event.kind, event.id, event.start, event.end),
                         (CALENDAR, todo.id, datetime(2026, 10, 6, 19, 0), datetime(2026, 10, 6, 21, 0)))
        self.assertEqual(self.work.changes(), [])

    def test_signs_in_first_when_needed(self) -> None:
        self.personal.signed = False
        self.executor().execute(parse_action_line(CHESS), on_stage=self.stages.append)
        self.assertEqual(self.stages, [STAGE_SIGNIN, STAGE_SIGNED_IN, STAGE_WORKING])
        self.assertLess(self.personal.names().index("sign_in"), self.personal.names().index("create_event"))

    def test_failures(self) -> None:
        self.personal.outcomes["create_event"] = CalendarError("Google Calendar error (500)", status=500)
        with self.assertRaises(ExecError) as ctx:
            self.executor().execute(parse_action_line(CHESS))
        self.assertEqual((str(ctx.exception), ctx.exception.status, ctx.exception.outcome),
                         ("Google Calendar error (500)", 500, OUTCOME_FAILED))
        self.personal.signed = False
        self.personal.sign_in_error = CalendarAuthError("Google sign-in was cancelled", problem=PROBLEM_DENIED)
        with self.assertRaises(ExecError) as ctx:
            self.executor().execute(parse_action_line(CHESS))
        self.assertEqual(ctx.exception.problem, PROBLEM_DENIED)
        self.assertEqual(len(self.personal.changes()), 1)


class CountdownKindTests(ExecutorTestCase):
    def test_rsvp_answers_with_the_note_and_explicit_notify(self) -> None:
        for answer, text in (("yes", "Accepted"), ("no", "Declined"), ("maybe", "Answered maybe")):
            for notify, send_updates in (("all", "all"), ("external", "externalOnly"), ("none", "none")):
                with self.subTest(answer=answer, notify=notify):
                    self.work.calls.clear()
                    action = example("RSVP", answer=answer, notify=notify, body="Running late", cal="team-cal")
                    result = self.executor().execute(action, on_stage=self.stages.append)
                    self.assertEqual(result, ExecResult(STATUS_SENT, EVENT_LINK, text))
                    self.assertEqual(self.work.changes(), [("respond", ("abc123def456ghi789", answer), {
                        "comment": "Running late", "send_updates": send_updates, "calendar_id": "team-cal",
                        "interactive": True})])
        self.assertEqual(self.personal.changes(), [])

    def test_move_and_cancel(self) -> None:
        move = example("Move", notify="none")
        self.assertEqual(self.executor().execute(move), ExecResult(STATUS_SENT, EVENT_LINK, "Moved"))
        self.assertEqual(self.work.changes(), [("move", ("abc123def456ghi789_20261008T190000Z",
                                                         datetime(2026, 10, 8, 14, 0), datetime(2026, 10, 8, 15, 0)),
                                                {"send_updates": "none", "calendar_id": "primary", "now": self.now,
                                                 "interactive": True})])
        cancel = example("Cancel")
        self.assertEqual(self.executor().execute(cancel), ExecResult(STATUS_SENT, "", "Cancelled"))
        self.assertEqual(self.personal.changes(), [("cancel", ("zyx987wvu654tsr321",), {
            "send_updates": "all", "calendar_id": "primary", "interactive": True})])

    def test_already_done(self) -> None:
        self.work.outcomes["respond"] = ChangeResult("abc123def456ghi789", EVENT_LINK, already=True)
        self.assertEqual(self.executor().execute(example("RSVP")).result_text, "Already accepted")
        self.personal.outcomes["cancel"] = ChangeResult("zyx987wvu654tsr321", "", already=True)
        self.assertEqual(self.executor().execute(example("Cancel")).result_text, "Already cancelled")
        self.work.outcomes["move"] = ChangeResult("x", EVENT_LINK, already=True)
        self.assertEqual(self.executor().execute(example("Move")).result_text, "Already at that time")

    def test_outcomes(self) -> None:
        cases = ((CalendarUnknownOutcome("Google Calendar answered 503", status=503), OUTCOME_UNKNOWN, ""),
                 (NotAllowed("You don't organize this event - decline it instead"), OUTCOME_FAILED, ""),
                 (EventGone("The event was cancelled"), OUTCOME_FAILED, ""),
                 (CalendarError("Google Calendar error (400)", status=400), OUTCOME_FAILED, ""),
                 (CalendarAuthError("Google sign-in for the work account expired", problem=PROBLEM_EXPIRED),
                  OUTCOME_FAILED, PROBLEM_EXPIRED))
        for error, outcome, problem in cases:
            with self.subTest(error=type(error).__name__):
                self.work.outcomes["respond"] = error
                with self.assertRaises(ExecError) as ctx:
                    self.executor().execute(example("RSVP"))
                self.assertEqual((str(ctx.exception), ctx.exception.outcome, ctx.exception.problem),
                                 (str(error), outcome, problem))
                self.assertEqual(ctx.exception.status, error.status)

    def test_without_interactive_a_missing_sign_in_sends_nothing(self) -> None:
        self.work.signed = False
        with self.assertRaises(ExecError) as ctx:
            self.executor().execute(example("RSVP"), interactive=False)
        self.assertEqual((ctx.exception.problem, ctx.exception.outcome), (PROBLEM_SIGNED_OUT, OUTCOME_FAILED))
        self.assertIn("nothing was sent", str(ctx.exception))
        self.assertNotIn("sign_in", self.work.names())
        self.assertEqual(self.work.changes(), [])

    def test_peek_never_signs_in(self) -> None:
        self.assertEqual(self.executor().peek(example("RSVP", cal="team-cal")), details())
        self.assertEqual(self.work.calls[-1], ("get_event", ("abc123def456ghi789",),
                                               {"calendar_id": "team-cal", "interactive": False}))
        self.assertNotIn("sign_in", self.work.names())

    def test_the_log_names_ids_kinds_and_aliases_only(self) -> None:
        action = example("RSVP", body="Running late", title="Secret strategy offsite")
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO") as logs:
            self.executor().execute(action)
        text = "\n".join(logs.output)
        self.assertIn(f"Carrying out action {action.id} (rsvp, work)", text)
        for private in ("Running late", "Secret strategy", "abc123def456ghi789"):
            self.assertNotIn(private, text)


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------

class ApplyEditTests(ExecutorTestCase):
    def test_rsvp_edit_keeps_the_id_and_changes_what_is_shown_and_done(self) -> None:
        original = example("RSVP")
        edited = apply_edit(original, ActionEdit(answer="Decline", notify="none", body="Can't make it\r\nsorry"))
        self.assertEqual(edited.id, original.id)
        self.assertEqual((edited.field("answer"), edited.field("notify"), edited.body),
                         ("no", "none", "Can't make it\nsorry"))
        self.assertEqual(edited.approve_label(), "Decline")
        self.assertTrue(edited.describe(TODAY).startswith("Answer: no"))
        self.assertEqual([name for name, _ in edited.fields], [name for name, _ in original.fields])
        self.executor().execute(edited)
        ((_, args, kwargs),) = self.work.changes()
        self.assertEqual((args[1], kwargs["comment"], kwargs["send_updates"]), ("no", "Can't make it\nsorry", "none"))

    def test_move_edit(self) -> None:
        original = example("Move")
        start, end = actions.parse_time_range("2026-10-09", "9:30 AM", "10:15")
        edited = apply_edit(original, ActionEdit(start=start, end=end, notify="external"))
        self.assertEqual(edited.id, original.id)
        self.assertEqual((edited.start, edited.end), (datetime(2026, 10, 9, 9, 30), datetime(2026, 10, 9, 10, 15)))
        self.assertEqual(edited.field("when"), "2026-10-09T09:30/2026-10-09T10:15")
        self.assertTrue(edited.describe(TODAY).startswith(f"New time: Fri Oct 9{DOT}9:30-10:15 AM"))
        self.assertEqual(edited.body, original.body)
        self.executor().execute(edited)
        ((_, args, kwargs),) = self.work.changes()
        self.assertEqual((args[1], args[2], kwargs["send_updates"]),
                         (datetime(2026, 10, 9, 9, 30), datetime(2026, 10, 9, 10, 15), "externalOnly"))

    def test_aware_times_are_read_as_wall_times(self) -> None:
        edited = apply_edit(example("Move"), ActionEdit(start=datetime(2026, 10, 9, 9, 0, 59, tzinfo=EDT),
                                                        end=datetime(2026, 10, 9, 10, 0, tzinfo=EDT)))
        self.assertEqual((edited.start, edited.end), (datetime(2026, 10, 9, 9, 0), datetime(2026, 10, 9, 10, 0)))

    def test_cancel_edit_and_an_empty_edit(self) -> None:
        cancel = example("Cancel")
        self.assertEqual(apply_edit(cancel, ActionEdit(notify="externalOnly")).field("notify"), "external")
        self.assertEqual(apply_edit(cancel, ActionEdit()), cancel)
        self.assertEqual(apply_edit(cancel, ActionEdit(body="  ")).body, "")

    def test_bad_edits(self) -> None:
        start = datetime(2026, 10, 9, 9, 0)
        cases = (
            (example("RSVP"), ActionEdit(answer="perhaps"), "answer=: use yes, no or maybe"),
            (example("RSVP"), ActionEdit(answer=3), "the value must be text"),   # type: ignore[arg-type]
            (example("Cancel"), ActionEdit(answer="yes"), "only an invitation has an answer"),
            (example("RSVP"), ActionEdit(notify="everyone"), "notify=: use all, external or none"),
            (example("Move"), ActionEdit(start=start), "only a move has a new time (start and end)"),
            (example("RSVP"), ActionEdit(start=start, end=start + timedelta(hours=1)),
             "only a move has a new time (start and end)"),
            (example("Move"), ActionEdit(start=start, end=start + timedelta(minutes=4)), "5 minutes to 12 hours"),
            (example("Move"), ActionEdit(start=start, end=start - timedelta(hours=1)), "5 minutes to 12 hours"),
            (example("Move"), ActionEdit(start=start, end=start + timedelta(hours=13)), "5 minutes to 12 hours"),
            (example("Cancel"), ActionEdit(body="x" * 1001), "the note is too long (1001 characters, at most 1000)"),
            (example("RSVP"), ActionEdit(to=("ana@example.edu",)), "can't be edited in this version"),
            (example("RSVP"), ActionEdit(subject="Hi"), "can't be edited in this version"),
            (example("RSVP"), ActionEdit(confirmed_new=frozenset({"ana@example.edu"})), "can't be edited"),
            (example("Reply"), ActionEdit(body="x"), "This card cannot be edited"),
            (parse_action_line(CHESS), ActionEdit(notify="all"), "This card cannot be edited"),
        )
        for action, edit, words in cases:
            with self.subTest(words=words, kind=action.kind), self.assertRaises(EditError) as ctx:
                apply_edit(action, edit)
            self.assertIn(words, str(ctx.exception))

    def test_note_cleanup(self) -> None:
        edited = apply_edit(example("Cancel"), ActionEdit(body="\tline one\x00\x07\rline two\u2028 "))
        self.assertEqual(edited.body, "line one\nline two")
        self.assertEqual(len(apply_edit(example("Cancel"), ActionEdit(body="y" * 1000)).body), 1000)


class ParseTimeRangeTests(unittest.TestCase):
    def test_reads_like_a_when_value(self) -> None:
        parse = actions.parse_time_range
        self.assertEqual(parse("2026-10-08", "14:00", "15:00"),
                         (datetime(2026, 10, 8, 14, 0), datetime(2026, 10, 8, 15, 0)))
        self.assertEqual(parse(" 2026-10-08 ", "2 pm", "3:30 PM"),
                         (datetime(2026, 10, 8, 14, 0), datetime(2026, 10, 8, 15, 30)))
        self.assertEqual(parse("2026-10-08", "23:00", "01:00"),
                         (datetime(2026, 10, 8, 23, 0), datetime(2026, 10, 9, 1, 0)))

    def test_errors(self) -> None:
        cases = ((("", "14:00", "15:00"), "give the new date"), (("2026-10-08", " ", "15:00"), "give the new start"),
                 (("Oct 8", "14:00", "15:00"), "YYYY-MM-DD"), (("2026-02-30", "14:00", "15:00"), "not a real date"),
                 (("2026-10-08", "14:00", "14:03"), "5 minutes to 12 hours"),
                 (("2026-10-08", "14:00-15:00", "16:00"), "as times"),
                 (("2026-10-08", "14:00 | x", "15:00"), "can't be read"),
                 (("2026-10-08", "1" * 41, "15:00"), "can't be read"),
                 (("2026-10-08", None, "15:00"), "give the new start"),
                 (("2026-10-08", "25:00", "26:00"), "25:00"))
        for args, words in cases:
            with self.subTest(args=args), self.assertRaises(actions.EditInvalid) as ctx:
                actions.parse_time_range(*args)   # type: ignore[arg-type]
            self.assertIn(words, str(ctx.exception))
            self.assertNotIn("when=", str(ctx.exception))


# --------------------------------------------------------------------------
# One job of the action worker
# --------------------------------------------------------------------------

class RunActionTests(ExecutorTestCase):
    def run_job(self, action: ProposedAction, **kwargs: Any) -> RunOutcome:
        kwargs.setdefault("store", self.store)
        kwargs.setdefault("writes_running", True)
        return run_action(self.executor(), self.watch(action), on_stage=self.stages.append, **kwargs)

    def saved(self, action: ProposedAction) -> dict[str, str] | None:
        return ActionStore(self.store.path).get(action.id)

    def test_running_is_on_disk_before_the_call_and_sent_after(self) -> None:
        action = example("RSVP")
        outcome = self.run_job(action)
        self.assertEqual(outcome, RunOutcome(STATUS_SENT, ExecResult(STATUS_SENT, EVENT_LINK, "Accepted"), None))
        self.assertEqual(self.work.on_disk_at_change, [STATUS_RUNNING])
        saved = self.saved(action)
        self.assertEqual({key: saved[key] for key in ("status", "link", "message", "kind", "account")},
                         {"status": STATUS_SENT, "link": EVENT_LINK, "message": "Accepted", "kind": "rsvp",
                          "account": "work"})
        self.assertTrue(self.store.is_decided(action.id))
        ((_, _, kwargs),) = self.work.changes()
        self.assertIs(kwargs["interactive"], False)   # no sign-in once "running" is saved
        self.assertEqual(self.stages, [STAGE_WORKING])

    def test_the_sign_in_comes_before_running(self) -> None:
        self.work.signed = False
        action = example("Move")
        self.run_job(action)
        sign_in = next(call for call in self.work.calls if call[0] == "sign_in")
        self.assertEqual(sign_in[1], None)   # nothing saved yet while the browser was open
        self.assertEqual(self.work.on_disk_at_change, [STATUS_RUNNING])
        self.assertEqual(self.stages, [STAGE_SIGNIN, STAGE_SIGNED_IN, STAGE_WORKING])

    def test_an_unknown_outcome_is_saved_and_never_retried(self) -> None:
        action = example("Cancel")
        self.personal.outcomes["cancel"] = CalendarUnknownOutcome(
            "No answer from Google Calendar while cancelling the event (TimeoutError)")
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO") as logs:
            outcome = self.run_job(action)
        self.assertEqual(outcome.status, STATUS_UNKNOWN)
        self.assertEqual(outcome.error.outcome, OUTCOME_UNKNOWN)
        self.assertEqual(len(self.personal.changes()), 1)
        saved = self.saved(action)
        self.assertEqual((saved["status"], saved["kind"], saved["account"]), (STATUS_UNKNOWN, "cancel", "personal"))
        self.assertEqual(saved["message"], "No answer from Google Calendar while cancelling the event (TimeoutError)")
        self.assertFalse(self.store.is_decided(action.id))   # the card keeps Deny / Retry
        self.assertIn(f"Action {action.id} (cancel, personal) not carried out: unknown", "\n".join(logs.output))

    def test_a_server_error_logs_the_status_code_only(self) -> None:
        action = example("RSVP")
        self.work.outcomes["respond"] = CalendarUnknownOutcome("Google Calendar answered 503: Backend gone",
                                                               status=503)
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO") as logs:
            self.run_job(action)
        text = "\n".join(logs.output)
        self.assertIn("unknown (HTTP 503)", text)
        self.assertNotIn("Backend gone", text)

    def test_a_refusal_after_running_is_failed(self) -> None:
        for error in (NotAllowed("You don't organize this event"), CalendarError("Bad Request (400)", status=400),
                      EventGone("The event was cancelled")):
            with self.subTest(error=type(error).__name__):
                self.work.calls.clear()
                action = example("Move")
                self.work.outcomes["move"] = error
                outcome = self.run_job(action)
                self.assertEqual((outcome.status, outcome.error.outcome), (STATUS_FAILED, OUTCOME_FAILED))
                self.assertEqual((self.saved(action)["status"], self.saved(action)["message"]),
                                 (STATUS_FAILED, str(error)))
                self.assertEqual(len(self.work.changes()), 1)

    def test_a_bug_after_running_is_unknown_and_logged_by_type_only(self) -> None:
        action = example("RSVP")
        self.work.outcomes["respond"] = TypeError("ya29.fake-token-in-a-message")
        with self.assertLogs(EXECUTOR_LOGGER, level="DEBUG") as logs:
            outcome = self.run_job(action)
        self.assertEqual((outcome.status, str(outcome.error)), (STATUS_UNKNOWN, "unexpected error (TypeError)"))
        self.assertEqual(self.saved(action)["status"], STATUS_UNKNOWN)
        self.assertNotIn("ya29.fake-token-in-a-message", "\n".join(logs.output))

    def test_a_failed_sign_in_sends_nothing_and_saves_failed(self) -> None:
        self.work.signed = False
        self.work.sign_in_error = CalendarAuthError(
            "Google blocked the sign-in: the work account's administrator does not allow this app "
            "(admin_policy_enforced)", problem=PROBLEM_BLOCKED)
        action = example("RSVP")
        outcome = self.run_job(action)
        self.assertEqual((outcome.status, outcome.error.problem), (STATUS_FAILED, PROBLEM_BLOCKED))
        self.assertEqual(self.work.changes(), [])
        self.assertEqual(self.saved(action)["status"], STATUS_FAILED)

    def test_a_bug_before_running_is_failed(self) -> None:
        self.work.signed = False
        self.work.sign_in_error = RuntimeError("boom")
        with self.assertLogs(EXECUTOR_LOGGER, level="ERROR"):
            outcome = self.run_job(example("RSVP"))
        self.assertEqual((outcome.status, outcome.error.outcome), (STATUS_FAILED, OUTCOME_FAILED))
        self.assertEqual(self.work.changes(), [])

    def test_nothing_is_sent_when_running_cannot_be_saved(self) -> None:
        action = example("RSVP")
        with mock.patch.object(actions, "_write_entries", return_value=False), \
                self.assertLogs(EXECUTOR_LOGGER, level="INFO"):
            outcome = self.run_job(action)
        self.assertEqual((outcome.status, str(outcome.error)), (STATUS_FAILED, NOT_SAVED_MESSAGE))
        self.assertEqual(self.work.changes(), [])
        self.assertEqual(self.store.get(action.id)["status"], STATUS_FAILED)
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO"):
            outcome = run_action(self.executor(), action, writes_running=True)   # no store at all
        self.assertEqual((outcome.status, str(outcome.error)), (STATUS_FAILED, NOT_SAVED_MESSAGE))
        self.assertEqual(self.work.changes(), [])

    def test_not_ready_saves_failed_without_any_call(self) -> None:
        action = example("RSVP", acct="school")
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO"):
            outcome = self.run_job(action)
        self.assertEqual((outcome.status, outcome.error.problem), (STATUS_FAILED, PROBLEM_SETUP))
        self.assertEqual(self.work.calls + self.personal.calls, [])

    def test_a_crash_mid_call_leaves_running_which_the_next_start_reads_as_unknown(self) -> None:
        class Crash(BaseException):
            pass

        action = example("RSVP")
        self.work.outcomes["respond"] = Crash()
        with self.assertRaises(Crash):
            self.run_job(action)
        on_disk = json.loads(self.store.path.read_text(encoding="utf-8"))[action.id]
        self.assertEqual(on_disk["status"], STATUS_RUNNING)   # what a crashed app leaves
        with self.assertLogs("briefing_reader.actions", level="WARNING"):
            restarted = ActionStore(self.store.path)
        entry = restarted.get(action.id)
        self.assertEqual((entry["status"], entry["message"], entry["kind"], entry["account"]),
                         (STATUS_UNKNOWN, INTERRUPTED_MESSAGE, "rsvp", "work"))
        self.assertFalse(restarted.is_decided(action.id))

    def test_calendar_and_todo_jobs_save_nothing_themselves(self) -> None:
        for action in (parse_action_line(CHESS), example("Todo")):
            with self.subTest(kind=action.kind):
                outcome = run_action(self.executor(), action, store=self.store)
                self.assertEqual(outcome.status, STATUS_CREATED)
                self.assertIsNone(self.store.get(action.id))
        self.personal.outcomes["create_event"] = CalendarError("Google Calendar error (500)", status=500)
        with self.assertLogs(EXECUTOR_LOGGER, level="INFO"):
            outcome = run_action(self.executor(), parse_action_line(CHESS), store=self.store)
        self.assertEqual(outcome.status, STATUS_FAILED)
        self.assertFalse(self.store.path.exists())

    def test_every_failure_makes_exactly_one_call(self) -> None:
        errors = (CalendarUnknownOutcome("x"), NotAllowed("x"), CalendarError("x", status=500), TypeError("x"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.work.calls.clear()
                self.work.outcomes["respond"] = error
                with self.assertLogs(EXECUTOR_LOGGER, level="INFO"):
                    self.run_job(example("RSVP"))
                self.assertEqual(len(self.work.changes()), 1)


# --------------------------------------------------------------------------
# Accounts and calendars from config.toml
# --------------------------------------------------------------------------

class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.environ = {"NOTION_TOKEN": "ntn_FakeTokenForExecutorTests0123456789abcdef",
                        "LOCALAPPDATA": str(self.root / "localappdata")}

    def config(self, text: str) -> Any:
        (self.root / "config.toml").write_text(text, encoding="utf-8")
        return load_config(self.root, environ=self.environ)

    def test_one_account_per_google_alias_and_the_legacy_token_moves_to_personal(self) -> None:
        cfg = self.config('[calendar]\ncalendar_id = "team@group.calendar.google.com"\n'
                          '[accounts.personal]\nfeatures = []\n[accounts.work]\n'
                          '[accounts.lab]\nbackend = "composio"\n')
        cfg.data_dir.mkdir(parents=True)
        legacy = cfg.data_dir / "google_token.json"
        legacy.write_text('{"token": "fake"}', encoding="utf-8")
        accounts = build_accounts(cfg)
        self.assertEqual(list(accounts), ["personal", "work"])
        self.assertFalse(legacy.exists())
        self.assertTrue((cfg.data_dir / "google_token_personal.json").exists())
        self.assertEqual(accounts["personal"].token_path, cfg.data_dir / "google_token_personal.json")
        self.assertEqual(accounts["work"].token_path, cfg.data_dir / "google_token_work.json")
        self.assertEqual(accounts["personal"].features, ("calendar",))   # Calendar proposals and the agenda
        self.assertEqual(accounts["personal"].client_secret_path, cfg.calendar.client_secret_path)
        calendars = build_calendars(cfg, accounts)
        self.assertEqual(list(calendars), ["personal", "work"])
        self.assertIsInstance(calendars["work"], GoogleCalendar)
        self.assertEqual((calendars["personal"].calendar_id, calendars["work"].calendar_id),
                         ("team@group.calendar.google.com", "primary"))
        self.assertIs(calendars["work"].account, accounts["work"])
        self.assertEqual(calendars["work"].alias, "work")

    def test_an_account_without_the_calendar_feature_has_no_calendar(self) -> None:
        cfg = self.config("[accounts.work]\nfeatures = []\n")
        self.assertEqual(list(build_accounts(cfg)), ["personal"])
        self.assertEqual(list(build_calendars(cfg)), ["personal"])

    def test_defaults_without_accounts(self) -> None:
        cfg = self.config("")
        calendars = build_calendars(cfg)
        self.assertEqual(list(calendars), ["personal"])
        self.assertFalse(calendars["personal"].is_signed_in())
        self.assertFalse(cfg.data_dir.exists())   # building opens nothing and writes nothing


# --------------------------------------------------------------------------
# What the cards say
# --------------------------------------------------------------------------

class CheckEventTests(unittest.TestCase):
    def test_rsvp(self) -> None:
        check = check_event(example("RSVP"), details(organizer_self=False, organizer="Ana Example", **SPEAKER),
                            TODAY)
        line = (f"Google: Speaker Series: Dr. Example{DOT}Tue Oct 6{DOT}5:00-6:00 PM{DOT}organized by Ana Example"
                f"{DOT}you haven't answered{DOT}as you@example.edu")
        self.assertEqual(check, EventCheck(   # Google's own title and time first: is it the right event?
            line, allowed=True, title="Speaker Series: Dr. Example", identity="you@example.edu", link=EVENT_LINK,
            tooltip=line))
        accepted = check_event(example("RSVP"), details(me="accepted", **SPEAKER), TODAY)
        self.assertIn(f"{DOT}you organize{DOT}you accepted{DOT}", accepted.text)
        refused = check_event(example("RSVP"), details(organizer_self=False, me="", **SPEAKER), TODAY)
        self.assertFalse(refused.allowed)
        self.assertIn("not on this event's guest list", refused.reason)
        self.assertIn("organized by someone else", refused.text)
        self.assertEqual(refused.identity, "")   # no attendee entry, not the organizer

    def test_move(self) -> None:
        line = f"Google: Project sync{DOT}Thu Oct 8{DOT}12:00-1:00 PM{DOT}you organize{DOT}as you@example.edu"
        self.assertEqual(check_event(example("Move"), details(), TODAY),
                         EventCheck(line, allowed=True, title="Project sync", identity="you@example.edu",
                                    link=EVENT_LINK, tooltip=line))
        guest = check_event(example("Move"), details(organizer_self=False, guests_can_modify=True), TODAY)
        self.assertTrue(guest.allowed)
        self.assertIn(f"{DOT}organized by someone else{DOT}guests may change it{DOT}", guest.text)
        self.assertIn(f"12:00-1:00 PM{DOT}one occurrence{DOT}",
                      check_event(example("Move"), details(instance=True), TODAY).text)
        for item, words in ((details(organizer_self=False), "don't organize"),
                            (details(series=True), "whole repeating series"),
                            (details(all_day=True), "All-day")):
            with self.subTest(words=words):
                check = check_event(example("Move"), item, TODAY)
                self.assertFalse(check.allowed)
                self.assertIn(words, check.reason)

    def test_cancel(self) -> None:
        study = details(title="Study group", start=datetime(2026, 10, 9, 18, 0, tzinfo=EDT))
        self.assertTrue(check_event(example("Cancel"), study, TODAY).allowed)
        self.assertEqual(check_event(example("Cancel"), study, TODAY).mismatch, "")
        for item, words in ((details(organizer_self=False, guests_can_modify=True), "decline instead"),
                            (details(series=True), "whole repeating series")):
            with self.subTest(words=words):
                check = check_event(example("Cancel"), item, TODAY)
                self.assertFalse(check.allowed)
                self.assertIn(words, check.reason)

    def test_long_title_is_shortened_on_the_line_only(self) -> None:
        long_title = "Quarterly planning review with the whole department and guests from the lab"
        check = check_event(example("Move", title=long_title), details(title=long_title), TODAY)
        self.assertTrue(check.text.startswith(f"Google: Quarterly planning review with the\u2026{DOT}Thu Oct 8"),
                        check.text)
        self.assertIn(long_title, check.tooltip)
        self.assertEqual((check.title, check.mismatch), (long_title, ""))

    def test_mismatch_with_the_line(self) -> None:
        move = example("Move")   # title=Project sync, at=2026-10-08 12:00-13:00
        same = (details(), details(title="project-sync (weekly)"), details(title="Sync"),
                details(start=datetime(2026, 10, 8, 12, 0).astimezone()))   # the same instant on this PC
        for item in same:
            with self.subTest(title=item.title, start=item.start):
                self.assertEqual(check_event(move, item, TODAY).mismatch, "")
        title = check_event(move, details(title="Dentist"), TODAY)
        self.assertEqual(title.mismatch, executor.MISMATCH_TITLE)
        self.assertTrue(title.allowed)   # a warning on the card, not a refusal
        self.assertTrue(title.text.startswith(f"Google (other title): Dentist{DOT}Thu Oct 8"), title.text)
        self.assertIn(f"The briefing says: Project sync{DOT}Thu Oct 8{DOT}12:00-1:00 PM", title.tooltip)
        moved = check_event(move, details(start=datetime(2026, 10, 8, 12, 30, tzinfo=EDT)), TODAY)   # :30 anywhere
        self.assertEqual(moved.mismatch, executor.MISMATCH_TIME)
        self.assertTrue(moved.text.startswith("Google (other time): Project sync"), moved.text)
        both = check_event(example("RSVP"), details(), TODAY)   # the RSVP line is the Speaker series
        self.assertEqual(both.mismatch, executor.MISMATCH_BOTH)
        self.assertTrue(both.text.startswith("Google (other title and time): "), both.text)
        self.assertEqual(check_event(move, details(all_day=True), TODAY).mismatch, executor.MISMATCH_TIME)
        self.assertEqual(check_event(move, details(title=gcal.NO_TITLE), TODAY).mismatch, "")
        bare = example("Cancel", title="", at="")
        self.assertEqual(check_event(bare, details(title="Anything", start=datetime(2027, 1, 1, tzinfo=EDT)),
                                     TODAY).mismatch, "")
        day = example("Cancel", at="2026-10-08")
        self.assertEqual(check_event(day, details(title="Study group"), TODAY).mismatch, "")
        self.assertEqual(check_event(day, details(title="Study group", all_day=True), TODAY).mismatch, "")
        self.assertEqual(check_event(day, details(title="Study group",
                                                  start=datetime(2026, 10, 9, 9, 0, tzinfo=EDT)), TODAY).mismatch,
                         executor.MISMATCH_TIME)

    def test_failures(self) -> None:
        rsvp = example("RSVP")
        self.assertEqual(check_failure(rsvp, CalendarNotSignedIn("Not signed in", problem=PROBLEM_SIGNED_OUT)),
                         EventCheck("Not signed in to the work account - Sign in to check this event", allowed=False))
        gone = check_failure(rsvp, EventGone("Google Calendar has no such event (404)", status=404))
        self.assertEqual((gone.allowed, gone.text), (False, "Google: event not found (deleted, cancelled or a wrong id)"))
        expired = check_failure(rsvp, CalendarAuthError("expired", problem=PROBLEM_EXPIRED))
        self.assertEqual(expired.text, sign_in_note("work", problem=PROBLEM_EXPIRED))
        refused = check_failure(rsvp, NotAllowed("Google Calendar refused to change this event (403: Forbidden)"))
        self.assertEqual((refused.allowed, refused.reason),
                         (False, "Google Calendar refused to change this event (403: Forbidden)"))
        setup = check_failure(rsvp, ExecError(CALENDAR_SETUP_NOTE, problem=PROBLEM_SETUP))
        self.assertEqual(setup.text, CALENDAR_SETUP_NOTE)
        other = check_failure(rsvp, CalendarError("Could not reach Google Calendar (TimeoutError)"))
        self.assertEqual(other.text, "Couldn't check the event with Google: Could not reach Google Calendar "
                                     "(TimeoutError)")
        self.assertFalse(other.allowed)


class SignInNoteTests(unittest.TestCase):
    def test_per_problem(self) -> None:
        self.assertEqual(sign_in_note("work", signed_in=True, problem=PROBLEM_EXPIRED), "")
        self.assertEqual(sign_in_note("work"), "Not signed in to the work account - click Sign in")
        self.assertEqual(sign_in_note("work", problem=PROBLEM_EXPIRED),
                         "The work account's Google sign-in expired or was revoked - click Sign in to sign in again")
        blocked = "Google blocked the sign-in: the work account's administrator does not allow this app (x)"
        self.assertEqual(sign_in_note("work", problem=PROBLEM_BLOCKED, message=blocked), blocked)
        self.assertIn("administrator", sign_in_note("work", problem=PROBLEM_BLOCKED))
        self.assertIn("cancelled or access was denied", sign_in_note("work", problem=PROBLEM_DENIED))
        self.assertIn("tick every box", sign_in_note("work", problem=PROBLEM_SCOPE))
        self.assertIn("not finished in time", sign_in_note("work", problem=PROBLEM_TIMEOUT))
        self.assertEqual(sign_in_note("work", problem=PROBLEM_SETUP), CALENDAR_SETUP_NOTE)
        self.assertEqual(sign_in_note("work", problem=PROBLEM_FAILED, message="Google sign-in failed (x)"),
                         "Google sign-in failed (x) - click Sign in to try again")


class ImportTests(unittest.TestCase):
    def test_qt_free_and_lazy(self) -> None:
        import subprocess
        import sys

        code = ("import sys, briefing_reader.executor; "
                "print(sorted({m.split('.')[0] for m in sys.modules "
                "if m.split('.')[0] in ('PySide6', 'googleapiclient', 'google_auth_oauthlib', "
                "'oauthlib', 'httplib2')}))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parent.parent, timeout=60, check=True)
        self.assertEqual(result.stdout.strip(), "[]")

    def test_sources_are_ascii(self) -> None:
        for name in ("executor.py", "google_auth.py", "gcal.py"):
            with self.subTest(name=name):
                self.assertTrue((Path(executor.__file__).parent / name).read_bytes().isascii())


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
