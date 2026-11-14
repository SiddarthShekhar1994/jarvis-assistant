"""Standing agents and rules in the app (Qt, no widgets of its own): their cards, the Agents screen (Ctrl+9), the one
spoken line, and a standing Reply's check at click time.

    StandingController(host)   made by ui.AppController while [standing] and [ask] are on; ``host`` is the controller
        start()                the first load, then a folder check every 30 seconds (the run files, standing.json,
                               standing_app.json and standing_state.json: what changed is read again), and the
                               fallback: while no scheduled check ran in 15 minutes (install-schedule.ps1 not rerun),
                               Jarvis starts --standing-run --origin app himself every 5 minutes while he is open
        reload(announce=True)  the definitions, the app's state and the run files read again: the cards go to
                               Approvals (host._take_standing_cards), the screen gets its AgentsInfo, and the runs that
                               need the owner and were not told yet are announced
        on_activation(message) {"cmd": "standing", "runs": [...]} from a background check: read again now, announce
        has_news() / announce()   --standing-show: anything to tell, and telling it
        on_screen_shown()      the Agents screen was seen: its Dock dot goes, the runs on it count as told
        reply_wait(action)     True while a standing Reply's thread is being checked again (Send goes on by itself
                               when it passes; the card says why when it does not)
        shutdown()

Loading run files
    Files no older than keep_days. Reports and receipts show only for runs started after the last Clear; a cleared
    run holding a card still waiting keeps that card (and its file) until the card is decided. Each card goes through
    standing.cards.card_from (its line parsed again, its kind limited, its account one Jarvis has, its id rebuilt,
    every recipient unverified); a decided card (actions.json), a withdrawn one (Undo, Pause, Delete) and one whose
    job recorded another Google account for that alias than the app's are left out.

The click-time Reply check
    Before a standing Reply's hidden hold, the thread is read again on a worker with the app's own Gmail reader for
    the card's account: the message must still be there, the owner must not have written in it since, and the
    recipients the run proposed must be on that message (its From, Reply-To, To or Cc). It passes once for two
    minutes; nothing is sent on a refusal, and the card says why.

Tidy-up (a rule may star, label or archive the mail it matched, without a click)
    Each receipt's tidy-up has its own ACTIVITY row: what Jarvis did, or why he did not. Its Undo (for UNDO_DAYS) takes
    off exactly Jarvis's change, through the account's own tidy-up sign-in, and only when the message's labels are
    still exactly what he left; a change a crash left unknown (the journal's "doing" with nothing after it) shows as
    such. A rule with a tidy-up on an account with the gmail_tidy feature shows the tidy-up's own card first: its
    words are the consent, and its button starts that sign-in (the owner signs in, in the browser).

The spoken line
    A fixed line with counts (never the model's words); the job's name only with the local voice ([voice] engine
    "local": nothing leaves the PC). It waits while the PC is locked, during [standing] quiet hours, while a meeting
    records, during focus mode, or while Windows says the owner is busy (presentation mode, a full-screen program);
    the conversation shows it at once. Each run is told once (AppState "announced"), never older than 12 hours.

Logs: run ids, job ids, kinds, counts, statuses and problem codes only; never a name, sentence, report, subject or
address.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from . import holo, standing_setup
from .actions import CALENDAR, REPLY, SOURCE_STANDING, STANDING_MODES, TODO, ProposedAction
from .standing import cards as standing_cards
from .standing import schedule
from .standing import tidy as tidy_mod
from .standing.model import (KIND_AGENT, KIND_RULE, STATUS_DRAFT, STATUS_LIVE, STATUS_PAUSED, STATUS_TRIED,
                             TRIGGER_EVERY, TRIGGER_MAIL, TRIGGER_TIME, WEEKDAYS, Agent,
                             DefinitionError, Rule, with_status)
from .standing.store import (AppState, Definitions, RunFiles, RunRequests, StandingFiles, Usage, parse_stamp,
                             read_runner_state)
from .standing.setup import SetupRefused, check_words
from .standing_view import (LOOK_AMBER, LOOK_DANGER, LOOK_GHOST, LOOK_GO, ActivityRow, AgentsInfo, CardButton,
                            JobRow, NoticeCard, ReportRow)
from .text_prep import format_time

logger = logging.getLogger(__name__)

TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"     # "1" while the tests run: no runner process is ever started
CHECK_MS = 30_000                 # the folder check
FALLBACK_MS = 5 * 60_000          # the app starts a check itself this often while no scheduled check is fresh
HELD_RECHECK_MS = 60_000          # a held line is tried again
TELL_WITHIN = timedelta(hours=12)   # an older run that needs the owner is shown, never said
FRESH_CHECK = timedelta(minutes=15)  # a background check this recent: background runs are on
REPLY_OK_FOR_S = 120.0            # a passed Reply check holds this long
REPLY_RESUME_S = 30.0             # Send goes on by itself only this soon after its click
DELETE_UNDO = timedelta(minutes=10)
VIEW_LINK = "standing:view"       # the conversation entry's "View agents" (ui handles the link)
VIEW_TEXT = "View agents"
UNDO_DELETE_LINK = "standing:undo-delete"
INSTALLER_LINK = "standing:installer"
ANNOUNCE_KEY = "standing:"        # + the run id: the spoken line's key (KIND_NOTICE)

# What the screen says.
EMPTY_TEXT = ("No agents or rules yet. Type agent: or rule: in the bar with what you want, and Jarvis sets it up "
              "for you to check first.")
REPLY_CHECKING_NOTE = "Checking that conversation again before it goes..."
REPLY_AGAIN_NOTE = "Checked: click Send again."
REPLY_PROBLEMS = {
    "gone": "That message is not in the conversation any more: nothing was sent.",
    "replied": "You wrote in that conversation since this draft was made: nothing was sent.",
    "not_on_message": "A recipient of this draft is not on that message: nothing was sent.",
    "unreadable": "Jarvis couldn't read that conversation again: nothing was sent. Try again in a moment.",
    "no_reader": "Jarvis can't read that account's mail here, so this draft can't be checked: nothing was sent.",
}
UNDO_TIP = "Withdraw this card from Approvals (nothing was sent)"
NEITHER_TEXT = ("That doesn't sound like something to do again and again. Ask it as a normal request, or say when it "
                "should happen.")
SETUP_BUSY_TEXT = "Jarvis is already setting one up; a moment."
SETUP_FAILED_TEXT = "Jarvis couldn't set that up just now; try again."
CONSENT_CARD, SETUP_CARD, START_CARD = "consent", "setup", "start"
START_LINES = (
    "A rule watches new mail from one sender and does one thing with each match: a to-do, an event, a reply draft, "
    "or a star, label or archive.",
    "An agent wakes at a time, every few hours or on new mail, reads only what you allow, and lines up cards for "
    "your OK.",
    "For example - rule: when billing@acme.com sends an invoice, make me a to-do to pay it",
    "Or - agent: every weekday at 8, tell me what my professors sent and list their deadlines",
    "Jarvis sets it up from your words for you to check, try and turn on.",
)
TIDY_CARD = "tidy:"               # + the alias: a tidy-up's own sign-in card
UNDO_FILE = "standing_tidy_undo.jsonl"   # the app's Undo journal (the runner's is standing_tidy.jsonl)
TIDY_DONE_WORDS = {"star": "Starred", "label": "Labelled", "archive": "Archived"}
TIDY_NOT_WORDS = {"star": "Not starred", "label": "Not labelled", "archive": "Not archived"}
TIDY_HELD_WORDS = {
    "orders": "a mail in that batch read like instructions to Jarvis",
    "jump": "far more of that batch said yes than in its dry run",
    "dmarc": "the mail did not pass Gmail's sender check",
    "own": "it is your own mail",
    "sent": "it is sent mail or a draft",
    "starred": "starred or important mail is never archived",
    "broad": "its filter must name a sender and match few mails",
    "not_set_up": "the account has no gmail_tidy feature in config.toml",
    "not_signed_in": "the tidy-up's own sign-in is not done yet",
    "account": "the account's tidy-up sign-in changed since Turn on",
    "written": "you have written to that sender",
    "cap": "the rule's tidy-ups for today are used up",
    "paused": "everything was paused",
    "failed": "Gmail refused it",
    "already": "it was already so",
}
UNDO_PROBLEMS = {"changed": "Not undone: the mail's labels changed since Jarvis's change; nothing was changed.",
                 "too_old": f"Not undone: a tidy-up can be undone for {tidy_mod.UNDO_DAYS} days.",
                 "not_done": "There is nothing of Jarvis's to undo.",
                 "signed_out": "Sign in for tidy-up first (the card on the Agents screen).",
                 "failed": "Gmail refused the Undo; nothing changed."}
TIDY_CARD_LINES = (
    "A rule of yours may star, label or archive the mail it matched, without a click. Jarvis does that through a "
    "sign-in of its own for this account, with Gmail's permission to change labels.",
    "He changes only the one message a rule matched, and only star, the one label you picked, or archive. He never "
    "sends, deletes, marks as read or touches any other label, and never archives mail from people you write to.",
    "Every tidy-up has an Undo on the Agents screen for 7 days. Pause all stops them at once.",
)
STATUS_WORDS = {STATUS_LIVE: "LIVE", STATUS_PAUSED: "PAUSED", STATUS_DRAFT: "DRAFT", STATUS_TRIED: "TRIED"}
RUN_LINES = {   # a run that made nothing to show as a receipt: its activity line
    "capped": "Reached its runs for today; it waits until tomorrow.",
    "missed": "Missed a run while the PC was off or asleep.",
    "failed": "Couldn't finish a run.",
    "signed_out": "Couldn't read its account in the background.",
    "held": "Held: your Claude plan's limit.",
}
_HOLD_WHY = {"limit": "your Claude plan's limit", "overage": "extra usage would pay for it",
             "warning": "Claude Code said you are near your limit"}
_CARD_WORDS = {TODO: "To-do", CALENDAR: "Event", REPLY: "Reply draft"}
DOT = "\u00b7"


# ---------------------------------------------------------------------------------------------------------------
# What a run file holds, as the app reads it
# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RunView:
    id: str
    name: str                      # the file's name
    job_kind: str
    job_id: str
    job_name: str
    status: str
    started: datetime | None
    ended: datetime | None
    trigger: str = ""
    origin: str = ""
    report: str = ""
    summary: str = ""
    needs_owner: bool = False
    cards: tuple[Mapping[str, Any], ...] = ()
    receipts: tuple[Mapping[str, Any], ...] = ()
    problem: str = ""
    counts: Mapping[str, Any] = field(default_factory=dict)


def run_view(record: Mapping[str, Any], name: str) -> RunView | None:
    """A run file's record as the app reads it (None when its job is not one: a damaged or foreign file)."""
    job = record.get("job")
    if not isinstance(job, Mapping):
        return None
    kind, job_id = job.get("kind"), job.get("id")
    if kind not in (KIND_AGENT, KIND_RULE) or not isinstance(job_id, str):
        return None

    def text(key: str, limit: int) -> str:
        value = record.get(key)
        return value[:limit] if isinstance(value, str) else ""

    def items(key: str) -> tuple[Mapping[str, Any], ...]:
        value = record.get(key)
        return tuple(item for item in value if isinstance(item, Mapping))[:200] if isinstance(value, list) else ()

    counts = record.get("counts")
    return RunView(id=str(record.get("id")), name=name, job_kind=kind, job_id=job_id,
                   job_name=str(job.get("name") or "")[:60], status=text("status", 20),
                   started=parse_stamp(record.get("started")), ended=parse_stamp(record.get("ended")),
                   trigger=text("trigger", 20), origin=text("origin", 20), report=text("report", 4000),
                   summary=text("summary", 200), needs_owner=record.get("needs_owner") is True,
                   cards=items("cards"), receipts=items("receipts"), problem=text("problem", 40),
                   counts=dict(counts) if isinstance(counts, Mapping) else {})


# ---------------------------------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------------------------------

def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _clock(moment: datetime, hour24: bool) -> str:
    return format_time(moment, hour24=hour24)


def when_text(moment: datetime | None, now: datetime, hour24: bool = False) -> str:
    """"9:41 AM" today, "YESTERDAY 9:41 PM", "MON 9:41 AM" this week, else "NOV 3 9:41 AM"."""
    if moment is None:
        return ""
    local = moment.astimezone(now.tzinfo) if now.tzinfo is not None else moment
    clock = _clock(local, hour24)
    days = (now.date() - local.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"Yesterday {clock}"
    if days == -1:
        return f"Tomorrow {clock}"
    if 1 < days < 7 or -7 < days < -1:
        return f"{local.strftime('%a')} {clock}"
    return f"{local.strftime('%b')} {local.day} {clock}"


def trigger_text(job: Agent | Rule, hour24: bool = False) -> str:
    """"Every day at 7:30 AM", "Mon, Wed at 7:30 AM and 5:00 PM", "Every 2 hours", "On new mail (work)"."""
    trigger = job.trigger
    if trigger.kind == TRIGGER_TIME:
        clocks = []
        for value in trigger.times:
            hour, minute = (int(part) for part in value.split(":"))
            clocks.append(_clock(datetime(2000, 1, 1, hour, minute), hour24))
        days = ("Every day" if not trigger.weekdays else
                "Weekdays" if tuple(trigger.weekdays) == (0, 1, 2, 3, 4) else
                ", ".join(WEEKDAYS[day].capitalize() for day in trigger.weekdays))
        return f"{days} at {' and '.join(clocks)}"
    if trigger.kind == TRIGGER_EVERY:
        minutes = trigger.every_minutes
        if minutes % 60 == 0:
            return "Every hour" if minutes == 60 else f"Every {minutes // 60} hours"
        return f"Every {minutes} minutes"
    if trigger.kind == TRIGGER_MAIL:
        accounts = ", ".join(job.accounts)
        return f"On new mail ({accounts})" if accounts else "On new mail"
    return "Only when you click Run now"


def agent_detail(agent: Agent, hour24: bool = False) -> str:
    sources = []
    if agent.mail:
        sources.append(f"mail ({', '.join(agent.accounts)})")
    if agent.calendar_days:
        sources.append("calendar" if agent.calendar_days == 1 else f"calendar, {agent.calendar_days} days")
    if agent.notes:
        sources.append("your notes")
    reads = f" {DOT} reads " + ", ".join(sources) if sources else ""
    return trigger_text(agent, hour24) + reads


def announcement_line(cards: int, items: int, *, address: str = "sir", name: str = "") -> str:
    """The fixed spoken line ("Sir, your agents left 2 cards for your OK."): counts only, the job's ``name`` only
    when the caller may say it (the local voice)."""
    from .persona import with_address

    who = name or "your agents"
    if cards and items:
        text = f"{{Sir}}, {who} left {_plural(cards, 'card', 'cards')} for your OK and {_plural(items, 'other item', 'other items')}."
    elif cards:
        text = f"{{Sir}}, {who} left {_plural(cards, 'card', 'cards')} for your OK."
    elif items:
        verb = "has" if name else "have"
        text = f"{{Sir}}, {who} {verb} {_plural(items, 'new item', 'new items')} for you."
    else:
        verb = "has" if name else "have"
        text = f"{{Sir}}, {who} {verb} something for you."
    return with_address(text, address)


def reply_problem(thread: Any, gmid: str, recipients: Iterable[str], own: Iterable[str]) -> str:
    """Why a standing Reply may not go now ("" when it may): the message it answers is gone from the thread
    ("gone"), the owner wrote in the thread after it ("replied"; an RSVP or auto-reply of his does not count), or a
    recipient the run proposed is not one of that message's people ("not_on_message": From, Reply-To, To, Cc)."""
    from .followups import from_owner, owner_automated

    own = tuple(own)
    messages = list(getattr(thread, "messages", ()) or ())
    ids = [message.gmail_id for message in messages]
    if not gmid or gmid not in ids:
        return "gone"
    index = ids.index(gmid)
    for message in messages[index + 1:]:
        if from_owner(message, own) and not owner_automated(message):
            return "replied"
    target = messages[index]
    people = set()
    for person in [target.sender, *target.reply_to, *target.to, *target.cc]:
        if person is not None and person.email:
            people.add(person.email.casefold())
    if any(address.casefold() not in people for address in recipients):
        return "not_on_message"
    return ""


def windows_busy() -> bool:
    """Windows says the owner is busy (standing.schedule.windows_busy; the background check asks it too)."""
    return schedule.windows_busy()


def _start_runner(origin: str = "now") -> bool:
    """One --standing-run in the background (windowless, outside Jarvis's process): Run now ("now"), or the app's
    fallback ("app"). Never while the tests run."""
    if os.environ.get(TEST_GUARD_ENV) == "1":
        logger.info("Standing: not starting a check while the tests run")
        return False
    from . import __main__ as entry

    return bool(entry._start_detached(["--standing-run", "--origin", origin], executable=entry._windowless_python()))


# ---------------------------------------------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------------------------------------------

class StandingController(QObject):
    """See the module docstring."""

    changed = Signal()
    _replyChecked = Signal(str, str, int)      # action id, problem ("" passed), sequence (from the worker)
    _setupDone = Signal(object, int)           # SetupOutcome, sequence (from the worker)
    _dryDone = Signal(str, object, int)        # rule id, DryOutcome, sequence (from the worker)
    _tidyUndone = Signal(str, str)             # receipt key, problem ("" undone) (from the worker)
    _tidySigned = Signal(str, str)             # alias, problem ("" signed in) (from the worker)

    def __init__(self, host: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.config = host.config
        self.settings = host.config.standing
        self.files = StandingFiles(host.config.data_dir)
        self.definitions = Definitions(self.files, min_every=self.settings.min_every_minutes)
        self.app_state = AppState(self.files)
        self.run_files = RunFiles(self.files)
        self.requests = RunRequests(self.files)
        self.usage = Usage(self.files, clock=host._now)
        self.agents: list[Agent] = []
        self.rules: list[Rule] = []
        self.runs: list[RunView] = []                       # oldest first
        self._cache: dict[str, RunView | None] = {}         # file name -> what it holds (files are written once)
        self._loaded: dict[str, ProposedAction] = {}        # card id -> the card as its run file made it
        self._card_runs: dict[str, str] = {}                # card id -> its run id
        self._signature: tuple[Any, ...] = ()
        self._timer: QTimer | None = None
        self._held_timer: QTimer | None = None
        self._held: list[str] = []                          # run ids whose line waits (shown already)
        self._shown_runs: set[str] = set()                  # run ids whose entry is in the conversation
        self._deleted: dict[str, tuple[Agent | Rule, tuple[str, ...], datetime]] = {}   # job id -> its Undo
        self._reply_ok: dict[str, float] = {}               # card id -> monotonic time its check passed
        self._reply_checking: dict[str, tuple[int, float]] = {}   # card id -> (sequence, monotonic click time)
        self._reply_seq = 0
        self._closed = False
        # Seams the tests replace: Windows' busy state, starting a check, building a reader for an account.
        self.busy_probe: Callable[[], bool] = windows_busy
        self.start_runner: Callable[[], bool] = _start_runner
        self.start_app_check: Callable[[], bool] = lambda: _start_runner("app")
        self._fallback: QTimer | None = None
        self.reader_for: Callable[[str], Any] = self._reader
        self.thread_runner: Callable[[Callable[[], None]], None] = self._in_thread
        self.engine_factory: Callable[[], Any] = lambda: standing_setup.app_engine(self.config)
        self._engine: Any = None
        self._setup: standing_setup.SetupDraft | None = None   # the setup card (memory only)
        self._busy = ""                                      # "setup" or "dry:<rule id>" while one runs
        self._work_seq = 0
        self._consent_later = False
        self.tidy_accounts: Callable[[], Mapping[str, Any]] = self._tidy_accounts
        self.tidy_for: Callable[[str], Any] = self._tidy_client
        self._tidy_built: dict[str, Any] | None = None
        self._tidy_later: set[str] = set()
        self._tidy_busy: set[str] = set()
        self._tidy_refused: dict[str, str] = {}               # receipt key -> why its Undo was refused (memory)
        self._tidyUndone.connect(self._on_tidy_undone)
        self._tidySigned.connect(self._on_tidy_signed)
        self._replyChecked.connect(self._on_reply_checked)
        self._setupDone.connect(self._on_setup_done)
        self._dryDone.connect(self._on_dry_done)
        stage = self.stage
        stage.cardAction.connect(self._on_card)
        stage.dryRunRequested.connect(self.dry_run)
        stage.turnOnRequested.connect(self.turn_on)
        stage.runNowRequested.connect(self.run_now)
        stage.pauseRequested.connect(self.toggle_pause)
        stage.deleteRequested.connect(self.delete_job)
        stage.undoRequested.connect(self.undo_receipt)
        stage.clearRequested.connect(self.clear_reports)
        stage.pauseAllRequested.connect(self.toggle_pause_all)
        stage.linkClicked.connect(self._on_link)
        host.window.reading.set_agents_available(True)

    # ---- the host's parts -------------------------------------------------------------------------------------

    @property
    def stage(self) -> Any:
        return self.host.window.reading.agents_stage

    def _now(self) -> datetime:
        now = self.host._now()
        return now if now.tzinfo is not None else now.astimezone()

    def _hour24(self) -> bool:
        return bool(self.config.display.hour24)

    def _aliases(self) -> list[str]:
        return sorted(getattr(self.config, "accounts", {}) or {"personal": None})

    # ---- start, the folder check, shutdown ---------------------------------------------------------------------

    def start(self) -> None:
        if self._closed:
            return
        self.reload(announce=True)
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(CHECK_MS)
            self._timer.timeout.connect(self._tick)
        self._timer.start()
        if self._fallback is None:
            self._fallback = QTimer(self)
            self._fallback.setInterval(FALLBACK_MS)
            self._fallback.timeout.connect(self.fallback_tick)
        self._fallback.start()

    def fallback_tick(self) -> bool:
        """Every 5 minutes while Jarvis is open: no check from the scheduled task in 15 minutes (the owner has not
        rerun install-schedule.ps1, or the PC skipped it) and something is on: Jarvis starts one check himself. True
        when one was started."""
        if self._closed or not self.app_state.consented() or self.app_state.paused_all:
            return False
        if not any(job.status == STATUS_LIVE for job in [*self.agents, *self.rules]):
            return False
        task_at = parse_stamp(read_runner_state(self.files).get("task_check"))
        if task_at is not None and self._now() - task_at <= FRESH_CHECK:
            return False
        started = bool(self.start_app_check())
        logger.info("Standing: no scheduled check lately; %s", "Jarvis started one" if started else "none started")
        return started

    def shutdown(self) -> None:
        self._closed = True
        for timer in (self._timer, self._held_timer, self._fallback):
            if timer is not None:
                timer.stop()

    def _files_signature(self) -> tuple[Any, ...]:
        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        names = tuple(path.name for path in self.run_files.paths())
        return (names, mtime(self.files.definitions), mtime(self.files.app), mtime(self.files.state),
                mtime(self.files.usage))

    def _tick(self) -> None:
        """Every 30 seconds: read again what changed (the screen's times and counts are refreshed anyway while it
        shows)."""
        if self._closed:
            return
        if self._files_signature() != self._signature:
            self.reload(announce=True)
        elif self._on_screen():
            self._show()

    def _on_screen(self) -> bool:
        from .ui import SCREEN_AGENTS

        try:
            return self.host.window.reading.current_tab() == SCREEN_AGENTS and self.host.window.isVisible()
        except Exception:  # noqa: BLE001 - unknown: not shown
            return False

    # ---- reading ------------------------------------------------------------------------------------------------

    def reload(self, *, announce: bool = True) -> None:
        if self._closed:
            return
        self._signature = self._files_signature()
        self.agents, self.rules = self.definitions.load()
        self.app_state.reload()
        self.runs = self._read_runs()
        self._sweep_cleared()
        self.host._take_standing_cards(self._cards())
        self._show()
        if announce:
            self._announce_new()
        self.changed.emit()

    def _read_runs(self) -> list[RunView]:
        now = self._now()
        oldest = now - timedelta(days=max(1, int(self.settings.keep_days)))
        found: list[RunView] = []
        names = set()
        for path in self.run_files.paths():
            names.add(path.name)
            started = RunFiles.started_of(path)
            if started is None or started < oldest:
                continue
            if path.name not in self._cache:
                record = RunFiles.read(path)
                self._cache[path.name] = run_view(record, path.name) if record is not None else None
            view = self._cache[path.name]
            if view is not None:
                found.append(view)
        for name in [name for name in self._cache if name not in names]:
            del self._cache[name]
        return found

    def _jobs(self) -> dict[str, Agent | Rule]:
        return {job.id: job for job in [*self.agents, *self.rules]}

    def _binding_ok(self, job: Agent | Rule | None, alias: str) -> bool:
        """The job recorded this alias's Google account at Turn on, and the app's account for the alias is still
        that one (unknown here - no sign-in yet - lets it through: the card's own checks come at the click)."""
        if job is None or not alias:
            return True
        recorded = dict(job.bindings).get(alias)
        if not recorded:
            return True
        account = (getattr(self.host, "_google_accounts", None) or {}).get(alias)
        binding = account.binding() if account is not None and callable(getattr(account, "binding", None)) else None
        sub = getattr(binding, "sub", "") if binding is not None else ""
        return not sub or sub == recorded

    def _cards(self) -> list[ProposedAction]:
        """The cards, newest run first (the cap drops the oldest): the waiting ones, and the ones decided while they
        showed (a card keeps its result and its Undo, as an Ask's does; decided before, it is left out)."""
        jobs = self._jobs()
        check_jobs = not self.definitions.problem
        aliases = self._aliases()
        store = self.host._store
        shown = {action.id for action in self.host._source_lists.get(SOURCE_STANDING, ())}
        cards: list[ProposedAction] = []
        seen: set[str] = set()
        dropped = 0
        self._loaded, self._card_runs = {}, {}
        for run in reversed(self.runs):
            job = jobs.get(run.job_id)
            if check_jobs and job is None:
                continue                                  # its job was deleted: its cards went with it
            for record in run.cards:
                card = standing_cards.card_from(record, aliases=aliases)
                if card is None:
                    dropped += 1
                    continue
                if card.id in seen or self.app_state.is_withdrawn(card.id):
                    continue
                if store.is_decided(card.id) and card.id not in shown:
                    continue
                if not self._binding_ok(job, card.account):
                    dropped += 1
                    continue
                seen.add(card.id)
                cards.append(card)
                self._loaded[card.id] = card
                self._card_runs[card.id] = run.id
        if dropped:
            logger.info("Standing: %d kept card(s) left out (unreadable, changed or another account)", dropped)
        return cards

    def _pending_ids(self) -> set[str]:
        """The standing cards a run file must stay for: still waiting, on their way (a hidden hold, queued,
        running) or still offering their Undo."""
        host = self.host
        pending = set(self._waiting_ids()) | set(getattr(host, "_jobs", {}) or {}) | set(getattr(host, "_undos", {}) or {})
        hold = getattr(host, "_send_hold", None)
        if hold is not None and getattr(hold, "action", None) is not None:
            pending.add(hold.action.id)
        return pending

    def _keeps(self, run: RunView, pending: set[str]) -> bool:
        """A run whose file must stay: a card of it still pending, or a tidy-up of it that can still be undone."""
        if any(str(record.get("id") or "") in pending for record in run.cards):
            return True
        now = self._now()
        for receipt in run.receipts:
            made = receipt.get("tidy")
            if isinstance(made, Mapping) and made.get("done") and not self.app_state.is_undone(
                    f"{run.id}:{receipt.get('id')}:tidy"):
                at = parse_stamp(made.get("at")) or run.ended
                if at is not None and now - at <= timedelta(days=tidy_mod.UNDO_DAYS):
                    return True
        return False

    def _waiting_ids(self) -> set[str]:
        """The standing cards still waiting for the owner (shown, undecided)."""
        store = self.host._store
        return {action.id for action in self.host._source_lists.get(SOURCE_STANDING, ())
                if not store.is_decided(action.id)}

    def _sweep_cleared(self) -> None:
        """Clear's runs whose last waiting card was decided since: their files go now."""
        cleared = self.app_state.cleared_before()
        if cleared is None:
            return
        pending = self._pending_ids()
        for run in list(self.runs):
            ended = run.ended or run.started
            if ended is None or ended >= cleared:
                continue
            if self._keeps(run, pending):
                continue
            try:
                (self.files.runs / run.name).unlink()
            except OSError:
                continue
            self._cache.pop(run.name, None)
            self.runs.remove(run)

    # ---- what the screen shows ------------------------------------------------------------------------------------

    def _visible_runs(self) -> list[RunView]:
        """The runs shown: those that ended after the last Clear, and the older ones kept for what still waits."""
        cleared = self.app_state.cleared_before()
        if cleared is None:
            return list(self.runs)
        pending = self._pending_ids()
        return [run for run in self.runs if ((run.ended or run.started) is not None
                                             and (run.ended or run.started) >= cleared) or self._keeps(run, pending)]

    def _cleared(self, run: RunView) -> bool:
        cleared = self.app_state.cleared_before()
        ended = run.ended or run.started
        return cleared is not None and (ended is None or ended < cleared)

    def _show(self) -> None:
        try:
            info = self.agents_info()
        except Exception as exc:  # noqa: BLE001 - the screen keeps what it showed
            logger.warning("Standing: the Agents screen could not be built (%s)", type(exc).__name__)
            return
        self.stage.set_agents(info)
        self.host.window.reading.dock.set_agents_new(bool(self._untold()))

    def agents_info(self) -> AgentsInfo:
        now = self._now()
        hour24 = self._hour24()
        state = read_runner_state(self.files)
        today = self.usage.peek()
        paused_all = self.app_state.paused_all
        consented = self.app_state.consented()
        meta, notes = self._status(now, state, today, paused_all, consented)
        jobs_rows = []
        runs_by_job: dict[str, int] = {}
        for agent in self.agents:
            runs_by_job[agent.id] = today.job(agent.id)
            jobs_rows.append(self._agent_row(agent, now, state, today, paused_all, consented, hour24))
        rule_rows = [self._rule_row(rule, now, today, hour24) for rule in self.rules]
        activity, reports = self._activity(now, hour24)
        return AgentsInfo(meta=meta, notes=tuple(notes), paused_all=paused_all, cards=tuple(self._notice_cards()),
                          agents=tuple(jobs_rows), rules=tuple(rule_rows), activity=tuple(activity), reports=tuple(reports),
                          empty_text="" if not (self.agents or self.rules) else EMPTY_TEXT,
                          can_clear=bool(reports or activity),
                          can_pause_all=bool(self.agents or self.rules or paused_all))

    def _status(self, now: datetime, state: Mapping[str, Any], today: Any, paused_all: bool,
                consented: bool) -> tuple[str, list[tuple[str, str, str]]]:
        dot = f" {DOT} "
        parts: list[str] = []
        notes: list[tuple[str, str, str]] = []
        if paused_all:
            parts.append("Paused")
        check = state.get("check") if isinstance(state.get("check"), Mapping) else {}
        at = parse_stamp(check.get("at")) if check else None
        task_at = parse_stamp(state.get("task_check"))
        if task_at is not None and now - task_at <= FRESH_CHECK:
            parts.append("Background runs on")
        else:
            parts.append("Checks while Jarvis is open" if at is not None and now - at <= FRESH_CHECK
                         else "Background runs off")
            if self.agents or self.rules:
                notes.append(("Background runs need the scheduled task: run install-schedule.ps1 again. Until then "
                              "Jarvis checks every 5 minutes while he is open.", "How", INSTALLER_LINK))
        parts.append(f"{today.runs} of {self.settings.max_runs_per_day} runs today")
        upcoming = self._next_run(now, state)
        if upcoming is not None and not paused_all:
            parts.append(f"Next {when_text(upcoming, now, self._hour24())}")
        holds = state.get("holds") if isinstance(state.get("holds"), Mapping) else {}
        until = parse_stamp(holds.get("until")) if holds else None
        if until is not None and until > now:
            why = _HOLD_WHY.get(str(holds.get("why") or ""), "a limit")
            notes.append((f"No Claude runs until {when_text(until, now, self._hour24())} ({why}).", "", ""))
        problems = state.get("problems") if isinstance(state.get("problems"), Mapping) else {}
        for alias in sorted(problems):
            if isinstance(alias, str) and alias in self._aliases():
                notes.append((f"The {alias} account can't be read in the background: sign it in again in Jarvis.",
                              "", ""))
        if self.definitions.problem:
            notes.append(("standing.json can't be read as it is: the agents and rules are kept as they are, "
                          "read only.", "", ""))
        for job_id, (job, _cards, at_) in list(self._deleted.items()):
            if now - at_ <= DELETE_UNDO:
                notes.append((f"Deleted {job.name}.", "Undo", f"{UNDO_DELETE_LINK}:{job_id}"))
            else:
                del self._deleted[job_id]
        if not consented and (self.agents or self.rules):
            notes.append(("Nothing runs until you say yes on the card that explains what agents read.", "", ""))
        return dot.join(parts), notes

    def _next_run(self, now: datetime, state: Mapping[str, Any]) -> datetime | None:
        jobs_state = state.get("jobs") if isinstance(state.get("jobs"), Mapping) else {}
        soonest: datetime | None = None
        for agent in self.agents:
            if agent.status != STATUS_LIVE:
                continue
            entry = jobs_state.get(agent.id) if isinstance(jobs_state.get(agent.id), Mapping) else {}
            nxt = None
            if agent.trigger.kind == TRIGGER_TIME:
                nxt = schedule.next_time(agent.trigger, now)
            elif agent.trigger.kind == TRIGGER_EVERY:
                nxt = schedule.next_every(agent.trigger, parse_stamp(entry.get("last_run")), now)
            if nxt is not None and (soonest is None or nxt < soonest):
                soonest = nxt
        return soonest

    def _agent_row(self, agent: Agent, now: datetime, state: Mapping[str, Any], today: Any, paused_all: bool,
                   consented: bool, hour24: bool) -> JobRow:
        jobs_state = state.get("jobs") if isinstance(state.get("jobs"), Mapping) else {}
        entry = jobs_state.get(agent.id) if isinstance(jobs_state.get(agent.id), Mapping) else {}
        meta = []
        if agent.status == STATUS_LIVE and not paused_all:
            nxt = None
            if agent.trigger.kind == TRIGGER_TIME:
                nxt = schedule.next_time(agent.trigger, now)
            elif agent.trigger.kind == TRIGGER_EVERY:
                nxt = schedule.next_every(agent.trigger, parse_stamp(entry.get("last_run")), now)
            if nxt is not None:
                meta.append(f"Next {when_text(nxt, now, hour24)}")
        last = parse_stamp(entry.get("last_run")) if entry else None
        if last is not None:
            meta.append(f"Last run {when_text(last, now, hour24)}")
        meta.append(f"{today.job(agent.id)} of {agent.runs_per_day} runs today")
        if agent.id in self.requests.pending():
            meta.append("Run now asked")
        live_or_paused = agent.status in (STATUS_LIVE, STATUS_PAUSED)
        detail = agent_detail(agent, hour24)
        if agent.status == STATUS_DRAFT:
            meta.append("Not on yet")
            detail = self._why_not_on(agent, detail)
        return JobRow(agent.id, KIND_AGENT, agent.name, detail=detail,
                      meta=f" {DOT} ".join(meta), status=agent.status, status_text=STATUS_WORDS.get(agent.status, ""),
                      can_run=agent.status == STATUS_LIVE and consented and not paused_all,
                      can_pause=live_or_paused, paused=agent.status == STATUS_PAUSED,
                      can_turn_on=agent.status == STATUS_DRAFT, tooltip=agent.words)

    def _rule_row(self, rule: Rule, now: datetime, today: Any, hour24: bool) -> JobRow:
        meta = []
        run = rule.dry_run
        if run is not None:
            meta.append(f"Dry run: {run.yes} of {run.judged} yes")
        checks = today.checks_of(rule.id)
        if checks:
            meta.append(f"{_plural(checks, 'mail', 'mails')} checked today")
        if rule.tidy.kind:
            meta.append({"star": "Stars", "label": f"Labels {rule.tidy.label_name or ''}".rstrip(),
                         "archive": "Archives"}.get(rule.tidy.kind, "") + " what it matches")
        live_or_paused = rule.status in (STATUS_LIVE, STATUS_PAUSED) and self._busy != f"dry:{rule.id}"
        if rule.status == STATUS_DRAFT:
            meta.append("Try it with Dry run before it can go on")
        detail = self._why_not_on(rule, rule.sentence) if rule.status == STATUS_TRIED else rule.sentence
        return JobRow(rule.id, KIND_RULE, rule.name, detail=detail,
                      meta=f" {DOT} ".join(part for part in meta if part),
                      status=rule.status, status_text=STATUS_WORDS.get(rule.status, ""), can_pause=live_or_paused,
                      paused=rule.status == STATUS_PAUSED, can_dry_run=not self._busy,
                      can_turn_on=rule.status == STATUS_TRIED and self._busy != f"dry:{rule.id}",
                      tooltip=rule.sentence)

    def _activity(self, now: datetime, hour24: bool) -> tuple[list[ActivityRow], list[ReportRow]]:
        rows: list[ActivityRow] = []
        reports: list[ReportRow] = []
        jobs = self._jobs()
        waiting = self._waiting_ids()
        store = self.host._store
        for run in reversed(self._visible_runs()):
            job = jobs.get(run.job_id)
            name = job.name if job is not None else (run.job_name or "A deleted job")
            when = when_text(run.ended or run.started, now, hour24)
            if run.report and not self._cleared(run):
                reports.append(ReportRow(run.id, when, name, run.report, run.summary))
            if not run.receipts and run.status in RUN_LINES:
                rows.append(ActivityRow(f"{run.id}:run", when, name, RUN_LINES[run.status],
                                        state_text=run.status.replace("_", " ").upper(),
                                        tone=holo.TONE_ALERT if run.status in ("failed", "signed_out")
                                        else holo.TONE_STEEL))
                continue
            for receipt in run.receipts:
                row = self._receipt_row(run, receipt, name, when, waiting, store)
                if row is not None:
                    rows.append(row)
                tidy_row = self._tidy_row(run, receipt, name, when, now)
                if tidy_row is not None:
                    rows.append(tidy_row)
        rows += self._unknown_rows(now, hour24)
        return rows, reports

    def _tidy_row(self, run: RunView, receipt: Mapping[str, Any], name: str, when: str,
                  now: datetime) -> ActivityRow | None:
        made = receipt.get("tidy")
        receipt_id = receipt.get("id")
        if not isinstance(made, Mapping) or not isinstance(receipt_id, str):
            return None
        kind = str(made.get("kind") or "")
        key = f"{run.id}:{receipt_id}:tidy"
        subject = str(receipt.get("subject") or "a matched mail")
        sender = str(receipt.get("name") or receipt.get("from") or "")
        detail = f" {DOT} ".join(part for part in (sender, subject) if part)
        if made.get("done"):
            what = TIDY_DONE_WORDS.get(kind, "Tidied")
            if made.get("thread_stays"):
                detail += f" {DOT} its conversation stays in the inbox for its other messages"
            if self.app_state.is_undone(key):
                return ActivityRow(key, when, name, f"{what}, then undone: {subject}", detail, "Undone",
                                   holo.TONE_STEEL)
            refused = self._tidy_refused.get(key)
            if refused in ("changed", "too_old"):
                return ActivityRow(key, when, name, f"{what}: {subject}", detail,
                                   "Changed since" if refused == "changed" else what, holo.TONE_STEEL,
                                   undo_tip=UNDO_PROBLEMS[refused])
            done_at = parse_stamp(made.get("at")) or run.ended
            fresh = done_at is not None and now - done_at <= timedelta(days=tidy_mod.UNDO_DAYS)
            busy = key in self._tidy_busy
            return ActivityRow(key, when, name, f"{what} the matched mail", detail, what, holo.TONE_CYAN,
                               can_undo=fresh and not busy, undo_text="Undo",
                               undo_tip="Take off exactly Jarvis's change, if the mail is still as he left it")
        held = str(made.get("held") or "")
        if held in ("", "no"):
            return None
        why = TIDY_HELD_WORDS.get(held, held.replace("_", " "))
        return ActivityRow(key, when, name, f"{TIDY_NOT_WORDS.get(kind, 'Not tidied')}: {why}", detail,
                           "Gmail refused" if held == "failed" else "Not tidied",
                           holo.TONE_ALERT if held == "failed" else holo.TONE_STEEL)

    def _unknown_rows(self, now: datetime, hour24: bool) -> list[ActivityRow]:
        """Tidy-ups a crash left between "doing" and "done": Jarvis may or may not have changed that message."""
        rows: list[ActivityRow] = []
        oldest = now - timedelta(days=max(1, int(self.settings.keep_days)))
        held = {str((receipt.get("tidy") or {}).get("tidy") or "") for run in self.runs for receipt in run.receipts
                if isinstance(receipt.get("tidy"), Mapping)}
        for entry in tidy_mod.TidyJournal(self.files.tidy_journal).entries().values():
            at = parse_stamp(entry.at)
            if at is None or at < oldest or entry.id in held:
                continue
            what = TIDY_DONE_WORDS.get(entry.kind, "Tidied")
            key = f"journal:{entry.id}"
            if entry.state == tidy_mod.UNKNOWN:
                rows.append(ActivityRow(key, when_text(at, now, hour24), entry.account,
                                        f"Not known: Jarvis may have {what.lower()} one message (the check stopped "
                                        "midway). Look at it in Gmail.", state_text="Not known",
                                        tone=holo.TONE_AMBER))
            elif entry.state == tidy_mod.DONE and now - at > timedelta(minutes=20):
                undone = self.app_state.is_undone(key)
                rows.append(ActivityRow(key, when_text(at, now, hour24), entry.account,
                                        f"{what} one message; its receipt was lost (the check stopped midway).",
                                        state_text="Undone" if undone else what,
                                        tone=holo.TONE_STEEL if undone else holo.TONE_CYAN,
                                        can_undo=not undone and now - at <= timedelta(days=tidy_mod.UNDO_DAYS),
                                        undo_text="Undo"))
        return rows

    def _receipt_row(self, run: RunView, receipt: Mapping[str, Any], name: str, when: str, waiting: set[str],
                     store: Any) -> ActivityRow | None:
        receipt_id = receipt.get("id")
        if not isinstance(receipt_id, str) or not receipt_id:
            return None
        key = f"{run.id}:{receipt_id}"
        sender = str(receipt.get("name") or receipt.get("from") or "")
        subject = str(receipt.get("subject") or "")
        detail = f" {DOT} ".join(part for part in (sender, subject) if part)
        kind = receipt.get("kind")
        undone = self.app_state.is_undone(key)
        if kind == "card":
            card_id = str(receipt.get("card") or "")
            card_kind = str(receipt.get("card_kind") or "")
            title = str(receipt.get("title") or "")
            loaded = self._loaded.get(card_id)
            if not title and loaded is not None:
                title = loaded.title
            text = f"{_CARD_WORDS.get(card_kind, 'Card')}: {title}" if title else _CARD_WORDS.get(card_kind, "Card")
            if card_id in waiting:
                return ActivityRow(key, when, name, text, detail, "Waiting", holo.TONE_AMBER, can_undo=True,
                                   undo_tip=UNDO_TIP)
            if undone or self.app_state.is_withdrawn(card_id):
                return ActivityRow(key, when, name, text, detail, "Withdrawn", holo.TONE_STEEL)
            if store.is_decided(card_id):
                return ActivityRow(key, when, name, text, detail, "Decided", holo.TONE_OK)
            return ActivityRow(key, when, name, text, detail, "Gone", holo.TONE_STEEL)
        values = receipt.get("values") if isinstance(receipt.get("values"), Mapping) else {}
        found = ", ".join(f"{field_}: {value}" for field_, value in list(values.items())[:5]
                          if isinstance(field_, str) and isinstance(value, str) and value)
        text = f"Matched: {found}" if found else "Matched a mail"
        refused = str(receipt.get("refused") or "")
        return ActivityRow(key, when, name, text, detail, "Not made" if refused else "Told",
                           holo.TONE_STEEL if refused else holo.TONE_CYAN)

    # ---- the owner's clicks on the screen -------------------------------------------------------------------------

    def _save(self, agents: Sequence[Agent], rules: Sequence[Rule]) -> bool:
        if not self.definitions.save(agents, rules):
            self.host._flash_note("Jarvis couldn't save the agents and rules (standing.json); nothing changed.")
            return False
        return True

    def _withdraw(self, card_ids: Iterable[str]) -> tuple[str, ...]:
        ids = tuple(card_id for card_id in card_ids if card_id and card_id not in self.host._jobs)
        if ids:
            self.app_state.mark("withdrawn", ids, self._now())
        return ids

    def _why_not_on(self, job: Agent | Rule, detail: str) -> str:
        """A draft's row says on itself why Turn on can't go yet (its button's click would only say it elsewhere)."""
        accounts = getattr(self.host, "_google_accounts", None) or {}
        problem = standing_setup.turn_on_problem(job, self.config, accounts, self.app_state.consented())
        return f"{detail}\n{problem}" if problem else detail

    def _cards_of(self, job_id: str) -> list[str]:
        waiting = self._waiting_ids()
        runs = {run.id: run for run in self.runs}
        return [card_id for card_id, run_id in self._card_runs.items()
                if card_id in waiting and runs.get(run_id) is not None and runs[run_id].job_id == job_id]

    def run_now(self, job_id: str) -> str:
        """Run now (an agent, live): asked through standing_requests, and a check started at once. Why not, in Jarvis's
        own words ("" when it was asked for)."""
        agent = next((item for item in self.agents if item.id == job_id), None)
        if agent is None or agent.status != STATUS_LIVE:
            return "That agent isn't on."
        if self.app_state.paused_all:
            return "Every agent is paused: Resume all on the Agents screen first."
        if not self.app_state.consented():
            return "First say yes on the Agents screen's card that explains what agents read."
        if not self.requests.ask(job_id, self._now()):
            self.host._flash_note("Jarvis couldn't ask for that run; try again.")
            return "Jarvis couldn't ask for that run; try again."
        started = self.start_runner()
        logger.info("Standing: Run now for %s (%s)", job_id, "a check started" if started else "the next check takes it")
        self._show()
        return ""

    def toggle_pause(self, job_id: str) -> None:
        """Pause (its waiting cards are withdrawn) or Resume a live or paused job."""
        agents, rules = list(self.agents), list(self.rules)
        for group in (agents, rules):
            for index, job in enumerate(group):
                if job.id != job_id or job.status not in (STATUS_LIVE, STATUS_PAUSED):
                    continue
                pausing = job.status == STATUS_LIVE
                try:
                    group[index] = with_status(job, STATUS_PAUSED if pausing else STATUS_LIVE)
                except DefinitionError:
                    return
                if not self._save(agents, rules):
                    return
                withdrawn = self._withdraw(self._cards_of(job_id)) if pausing else ()
                if pausing and withdrawn:
                    self.app_state.keep_paused_cards(job_id, withdrawn)
                elif not pausing:
                    back = self.app_state.take_paused_cards(job_id)
                    if back:
                        self.app_state.unmark("withdrawn", back)
                logger.info("Standing: %s %s %s (%d card(s) withdrawn)", job.kind, job_id,
                            "paused" if pausing else "resumed", len(withdrawn))
                self.reload(announce=False)
                return

    def delete_job(self, job_id: str) -> None:
        """Delete a job (its waiting cards are withdrawn); Undo on the screen for ten minutes brings both back."""
        job = self._jobs().get(job_id)
        if job is None:
            return
        agents = [item for item in self.agents if item.id != job_id]
        rules = [item for item in self.rules if item.id != job_id]
        if not self._save(agents, rules):
            return
        withdrawn = self._withdraw(self._cards_of(job_id))
        self._deleted[job_id] = (job, withdrawn, self._now())
        logger.info("Standing: %s %s deleted (%d card(s) withdrawn)", job.kind, job_id, len(withdrawn))
        self.reload(announce=False)

    def undo_delete(self, job_id: str = "") -> None:
        if not self._deleted:
            return
        job_id = job_id or next(reversed(self._deleted))
        if job_id not in self._deleted:
            return
        job, withdrawn, _at = self._deleted.pop(job_id)
        agents, rules = list(self.agents), list(self.rules)
        if job.id not in self._jobs():
            (agents if isinstance(job, Agent) else rules).append(job)
            if not self._save(agents, rules):
                return
        if withdrawn:
            self.app_state.unmark("withdrawn", withdrawn)
        logger.info("Standing: %s %s brought back", job.kind, job.id)
        self.reload(announce=False)

    def toggle_pause_all(self) -> None:
        paused = not self.app_state.paused_all
        if not self.app_state.set_paused_all(paused):
            self.host._flash_note("Jarvis couldn't save that (standing_app.json); nothing changed.")
            return
        logger.info("Standing: Pause all %s", "on" if paused else "off")
        self.reload(announce=False)

    def undo_receipt(self, key: str) -> None:
        """A receipt's Undo: its card, still waiting, is withdrawn from Approvals (nothing was sent); a tidy-up's row
        takes off Jarvis's change (undo_tidy)."""
        if key.endswith(":tidy"):
            self.undo_tidy(key)
            return
        if key.startswith("journal:"):
            entry = tidy_mod.TidyJournal(self.files.tidy_journal).entries().get(key.partition(":")[2])
            if entry is not None and entry.state == tidy_mod.DONE and not self.app_state.is_undone(key):
                self._undo_change(key, entry.account, entry.message, entry.thread,
                                  {"done": True, "kind": entry.kind, "add": list(entry.add),
                                   "remove": list(entry.remove)}, parse_stamp(entry.at), entry.rule)
            return
        run_id, _, receipt_id = key.partition(":")
        run = next((item for item in self.runs if item.id == run_id), None)
        receipt = next((item for item in (run.receipts if run is not None else ())
                        if item.get("id") == receipt_id), None)
        if receipt is None or receipt.get("kind") != "card":
            return
        card_id = str(receipt.get("card") or "")
        if card_id not in self._waiting_ids() or card_id in self.host._jobs:
            return
        self._withdraw([card_id])
        self.app_state.mark("undone", [key], self._now())
        logger.info("Standing: a card of run %s withdrawn from its receipt", run_id)
        self.reload(announce=False)

    def clear_reports(self) -> None:
        """Clear: the reports and receipts on this PC go now (a run still holding a waiting card keeps its file until
        that card is decided; its report and receipts no longer show)."""
        now = self._now()
        if not self.app_state.clear_runs(now):
            self.host._flash_note("Jarvis couldn't clear them (standing_app.json); nothing changed.")
            return
        pending = self._pending_ids()
        kept = 0
        gone = 0
        for run in list(self.runs):
            if self._keeps(run, pending):
                kept += 1
                continue
            try:
                (self.files.runs / run.name).unlink()
                gone += 1
            except OSError:
                continue
        logger.info("Standing: cleared %d run file(s); %d kept for what still waits", gone, kept)
        self.reload(announce=False)

    def _on_link(self, link_id: str) -> None:
        if link_id == UNDO_DELETE_LINK or link_id.startswith(UNDO_DELETE_LINK + ":"):
            self.undo_delete(link_id.partition(":undo-delete:")[2] if ":undo-delete:" in link_id else "")
        elif link_id == INSTALLER_LINK:
            self.host._flash_note("Background runs: run install-schedule.ps1 again (README, step 6). It adds the "
                                  "\"Briefing agents\" task, which checks every 5 minutes.")

    # ---- tidy-up: its Undo and its own sign-in --------------------------------------------------------------------

    def _tidy_accounts(self) -> Mapping[str, Any]:
        """The app's tidy-up sign-ins (executor.build_tidy_accounts; the app saves them, as it does every sign-in)."""
        if self._tidy_built is None:
            if not getattr(getattr(self.config, "calendar", None), "enabled", True):
                self._tidy_built = {}
            else:
                from .executor import build_tidy_accounts

                self._tidy_built = dict(build_tidy_accounts(self.config, persist=True))
        return self._tidy_built

    def _tidy_client(self, alias: str) -> Any:
        account = self.tidy_accounts().get(alias)
        return tidy_mod.GmailTidy(account) if account is not None else None

    def undo_tidy(self, key: str) -> None:
        """Take off exactly Jarvis's change (on a worker): the message's labels read again, and changed back only when
        they are still exactly what he left, within UNDO_DAYS; journalled before the call (standing_tidy_undo.jsonl)."""
        run_id, _, rest = key.partition(":")
        receipt_id = rest[:-len(":tidy")] if rest.endswith(":tidy") else ""
        run = next((item for item in self.runs if item.id == run_id), None)
        receipt = next((item for item in (run.receipts if run is not None else ()) if item.get("id") == receipt_id),
                       None)
        made = receipt.get("tidy") if receipt is not None else None
        if (receipt is None or not isinstance(made, Mapping) or self.app_state.is_undone(key)
                or key in self._tidy_busy):
            return
        self._undo_change(key, str(receipt.get("account") or ""), str(receipt.get("message") or ""),
                          str(receipt.get("thread") or ""), made,
                          parse_stamp(made.get("at")) or (run.ended if run is not None else None),
                          run.job_id if run is not None else "")

    def _undo_change(self, key: str, alias: str, message: str, thread: str, made: Mapping[str, Any],
                     done_at: datetime | None, rule_id: str) -> None:
        try:
            change = tidy_mod.Change(tuple(str(item) for item in list(made.get("add") or ())),
                                     tuple(str(item) for item in list(made.get("remove") or ()))).reverse()
        except TypeError:
            self.host._flash_note(UNDO_PROBLEMS["changed"])
            return
        client = self.tidy_for(alias)
        if client is None:
            self.host._flash_note(UNDO_PROBLEMS["signed_out"])
            return
        self._tidy_busy.add(key)
        self._show()
        now = self._now()
        journal = tidy_mod.TidyJournal(self.files.data_dir / UNDO_FILE)
        logger.info("Standing: undoing a tidy-up of rule %s", rule_id)

        def work() -> None:
            problem = ""
            try:
                labels_now = client.labels_of(message)
                problem = tidy_mod.undo_problem(made, labels_now, now, done_at)
                if not problem:
                    entry = journal.begin(rule=rule_id, account=alias, message=message,
                                          thread=thread, kind=f"undo-{made.get('kind')}",
                                          change=change, before=labels_now, now=now)
                    try:
                        after = client.modify(message, add=change.add, remove=change.remove)
                    except Exception:  # noqa: BLE001 - Gmail refused it: journalled
                        journal.failed(entry, now)
                        raise
                    journal.done(entry, after, now)
            except Exception as exc:  # noqa: BLE001 - nothing (more) changed
                logger.info("Standing: a tidy-up's Undo failed (%s)", type(exc).__name__)
                problem = problem or "failed"
            self._tidyUndone.emit(key, problem)

        self.thread_runner(work)

    def _on_tidy_undone(self, key: str, problem: str) -> None:
        self._tidy_busy.discard(key)
        if self._closed:
            return
        if problem:
            self.host._flash_note(UNDO_PROBLEMS.get(problem, UNDO_PROBLEMS["failed"]))
            if problem in ("changed", "too_old"):
                self._tidy_refused[key] = problem          # not offered again: it is not Jarvis's change any more
        else:
            self.app_state.mark("undone", [key], self._now())
            logger.info("Standing: a tidy-up was undone")
        self.reload(announce=False)

    def _tidy_wanted(self) -> list[str]:
        """Accounts a rule of the owner's would tidy whose tidy-up sign-in (or its yes) is still missing."""
        wanted: list[str] = []
        for rule in self.rules:
            alias = rule.account
            if not rule.tidy.kind or alias in wanted or alias in self._tidy_later:
                continue
            if not standing_setup.can_tidy(self.config, alias):
                continue                                  # the setup card says how to add the feature
            account = self.tidy_accounts().get(alias)
            signed = False
            try:
                signed = account is not None and bool(account.is_signed_in(tidy_mod.TIDY_FEATURE))
            except Exception:  # noqa: BLE001 - unknown: ask
                signed = False
            if not signed or not self.app_state.tidy_consented(alias):
                wanted.append(alias)
        return wanted

    def tidy_sign_in(self, alias: str) -> None:
        """The tidy-up card's button: the owner's yes kept, then the account's tidy-up sign-in on a worker (the owner
        signs in, in the browser; nothing is asked for but gmail.modify and who the account is)."""
        account = self.tidy_accounts().get(alias)
        if account is None or alias in self._tidy_busy:
            return
        try:
            bound = account.binding() is not None
        except Exception:  # noqa: BLE001 - unknown: not bound
            bound = False
        if not bound:
            self.host._flash_note(f"Sign the {alias} account in first (a card's Sign in, or Connect in the Day view); "
                                  "the tidy-up's sign-in must be the same Google account.")
            return
        if not self.app_state.set_tidy_consent(alias, True, self._now()):
            self.host._flash_note("Jarvis couldn't save that (standing_app.json); nothing changed.")
            return
        self._tidy_busy.add(alias)
        self._show()
        logger.info("Standing: the tidy-up sign-in of the %s account", alias)

        def work() -> None:
            problem = ""
            try:
                if not account.is_signed_in(tidy_mod.TIDY_FEATURE):
                    account.sign_in()
                if account.change_problem()[0] == "identity":
                    account.disconnect()                  # another Google account: its sign-in is not kept
                    problem = "identity"
            except Exception as exc:  # noqa: BLE001 - cancelled, denied or not set up: the card says so
                problem = problem or type(exc).__name__
            self._tidySigned.emit(alias, problem)

        self.thread_runner(work)

    def _on_tidy_signed(self, alias: str, problem: str) -> None:
        self._tidy_busy.discard(alias)
        if self._closed:
            return
        if problem == "identity":
            self.host._flash_note(f"That was another Google account than the {alias} account's: sign in for tidy-up "
                                  "with the same one. Nothing was kept.")
        elif problem:
            self.host._flash_note("The tidy-up sign-in did not finish; nothing was changed. Try again from the card.")
        self.reload(announce=False)

    def _tidy_cards(self) -> list[NoticeCard]:
        cards = []
        for alias in self._tidy_wanted():
            busy = alias in self._tidy_busy
            cards.append(NoticeCard(TIDY_CARD + alias, "Tidy-up needs its own sign-in",
                                    f"Let Jarvis tidy {alias} mail for your rules", TIDY_CARD_LINES,
                                    (CardButton("signin", "Sign in for tidy-up", LOOK_AMBER, enabled=not busy),
                                     CardButton("later", "Not now", LOOK_GHOST, enabled=not busy)),
                                    note="Finish the sign-in in your browser..." if busy else ""))
        return cards

    # ---- setting a job up ([standing]: "agent:" / "rule:", the planner's hand-off) --------------------------------

    def live_agent_names(self) -> tuple[str, ...]:
        """The live agents' names, for "run <name>" (ask.routes)."""
        return tuple(agent.name for agent in self.agents if agent.status == STATUS_LIVE)

    def handoff_allowed(self) -> bool:
        """An Ask's first run may hand a standing job's request to the setup (AskPlanner.standing_allowed)."""
        return not self._closed

    def on_route(self, kind: str, payload: str) -> None:
        """A typed shortcut (ask.routes ROUTE_STANDING): "run" with an agent's name, or "agent" / "rule" with the
        owner's own sentence."""
        if kind == "run":
            self.run_by_name(payload)
        else:
            self.setup_from_words(kind, payload)

    def run_by_name(self, name: str) -> None:
        agent = next((item for item in self.agents if item.status == STATUS_LIVE
                      and item.name.casefold() == " ".join(name.split()).casefold()), None)
        if agent is None:
            self.host._flash_note("No agent of that name is on.")
            return
        problem = self.run_now(agent.id)
        if problem:
            self.host._jarvis(problem, tone=_warn_tone(), context=False)
            return
        self.host._jarvis(f"Running {agent.name} now; what it finds comes to the Agents screen.",
                          link=(VIEW_TEXT, VIEW_LINK), context=False)

    def _engine_obj(self) -> Any:
        if self._engine is None:
            self._engine = self.engine_factory()
        return self._engine

    def _open_screen(self) -> None:
        from .ui import SCREEN_AGENTS

        self.host.window.reading.set_tab(SCREEN_AGENTS)

    def setup_from_words(self, kind: str, words: str) -> None:
        """Set a job up from the owner's own sentence: one setup run on a worker, then a draft on the setup card (a
        rule needs its dry run before it can go on). Never words a planner wrote."""
        if self._closed:
            return
        if self._busy:
            self.host._flash_note(SETUP_BUSY_TEXT)
            return
        try:
            words = check_words(words)
        except SetupRefused as refused:
            self._setup = standing_setup.SetupDraft(" ".join(str(words or "").split())[:200], kind, note=str(refused))
            self._show()
            self._open_screen()
            return
        self._work_seq += 1
        seq = self._work_seq
        self._busy = "setup"
        self._setup = standing_setup.SetupDraft(words, kind if kind in (KIND_AGENT, KIND_RULE) else "", busy="setup")
        self._show()
        self._open_screen()
        engine, now = self._engine_obj(), self._now()
        logger.info("Standing: setting up a %s from the owner's sentence", kind or "job")

        def work() -> None:
            try:
                outcome = standing_setup.run_setup(engine, words, self.config, self.usage, now=now,
                                                   time_zone_name=now.tzname() or "", labels=self._labels)
            except Exception as exc:  # noqa: BLE001 - the card says so; nothing was saved
                logger.warning("Standing: a setup stopped (%s)", type(exc).__name__)
                outcome = standing_setup.SetupOutcome(refusal=SETUP_FAILED_TEXT)
            self._setupDone.emit(outcome, seq)

        self.thread_runner(work)

    def _labels(self, alias: str) -> list[tuple[str, str]]:
        """The account's own Gmail labels (a label tidy-up's label must be one of them): read on the worker."""
        reader = self.reader_for(alias)
        try:
            return list(reader.labels()) if reader is not None and callable(getattr(reader, "labels", None)) else []
        except Exception:  # noqa: BLE001 - none found: the setup says to make the label first
            return []

    def _on_setup_done(self, outcome: Any, seq: int) -> None:
        if seq != self._work_seq or self._closed:
            return
        self._busy = ""
        draft = self._setup or standing_setup.SetupDraft("")
        if outcome.job is None:
            self._setup = replace(draft, busy="", say=outcome.say,
                                  note=NEITHER_TEXT if outcome.neither else (outcome.refusal or SETUP_FAILED_TEXT))
            self._show()
            return
        job = outcome.job
        agents, rules = list(self.agents), list(self.rules)
        (agents if isinstance(job, Agent) else rules).append(job)
        if not self._save(agents, rules):
            self._setup = replace(draft, busy="", note="Jarvis couldn't save it (standing.json); nothing was kept.")
            self._show()
            return
        self._setup = replace(draft, busy="", job=job, say=outcome.say, note="")
        self.host._jarvis(f"I set up {job.name} as a draft: check it on the Agents screen before it goes on.",
                          link=(VIEW_TEXT, VIEW_LINK), context=False)
        self.reload(announce=False)

    def dry_run(self, rule_id: str) -> None:
        """A rule's dry run over the last 30 days of its mail, on a worker: nothing is proposed or tidied."""
        rule = next((item for item in self.rules if item.id == rule_id), None)
        if rule is None or self._closed:
            return
        if self._busy:
            self.host._flash_note(SETUP_BUSY_TEXT)
            return
        self._work_seq += 1
        seq = self._work_seq
        self._busy = f"dry:{rule_id}"
        if self._setup is None or self._setup.job is None or self._setup.job.id != rule_id:
            self._setup = standing_setup.SetupDraft(rule.sentence, KIND_RULE, job=rule)
        self._setup = replace(self._setup, busy="dry", note="")
        self._show()
        self._open_screen()
        engine, now = self._engine_obj(), self._now()
        reader = self.reader_for(rule.account)
        logger.info("Standing: a dry run of rule %s", rule_id)

        def work() -> None:
            try:
                own = list(reader.own_addresses()) if reader is not None else []
            except Exception:  # noqa: BLE001 - without them a mail of the owner's is judged like any other
                own = []
            try:
                outcome = standing_setup.run_dry(engine, rule, reader, self.config, self.usage, now=now,
                                                 time_zone_name=now.tzname() or "", own=own)
            except Exception as exc:  # noqa: BLE001 - the card says so; nothing was kept
                logger.warning("Standing: a dry run stopped (%s)", type(exc).__name__)
                outcome = standing_setup.DryOutcome(refusal="Jarvis couldn't finish the dry run; try again.")
            self._dryDone.emit(rule_id, outcome, seq)

        self.thread_runner(work)

    def _on_dry_done(self, rule_id: str, outcome: Any, seq: int) -> None:
        if seq != self._work_seq or self._closed:
            return
        self._busy = ""
        draft = self._setup
        self.agents, self.rules = self.definitions.load()          # as it is now: a click may have come meanwhile
        current = next((item for item in self.rules if item.id == rule_id), None)
        if outcome.rule is not None and current is not None and outcome.rule.dry_run is not None:
            merged = replace(current, dry_run=outcome.rule.dry_run)      # only the dry run is the run's
            if merged.status == STATUS_DRAFT:
                merged = with_status(merged, STATUS_TRIED)
            rules = [merged if item.id == rule_id else item for item in self.rules]
            if self._save(self.agents, rules):
                self.rules = rules
                current = merged
        job = current
        if draft is not None and draft.job is not None and draft.job.id == rule_id:
            self._setup = replace(draft, job=job, busy="", dry=outcome, note=outcome.refusal)
        self.reload(announce=False)

    def turn_on(self, job_id: str) -> None:
        """Turn on a draft agent or a tried rule: consent given, a rule's dry run done, and each of its accounts'
        Google account recorded (the background check refuses a changed one)."""
        job = self._jobs().get(job_id)
        if job is None or job.status not in (STATUS_DRAFT, STATUS_TRIED, STATUS_PAUSED):
            return
        accounts = getattr(self.host, "_google_accounts", None) or {}
        problem = standing_setup.turn_on_problem(job, self.config, accounts, self.app_state.consented())
        if problem:
            if self._setup is not None and self._setup.job is not None and self._setup.job.id == job_id:
                self._setup = replace(self._setup, note=problem)
                self._show()
            else:
                self.host._flash_note(problem)
            return
        try:
            on = with_status(replace(job, bindings=standing_setup.bindings_of(job, accounts)), STATUS_LIVE)
        except DefinitionError:
            return
        agents = [on if item.id == job_id else item for item in self.agents]
        rules = [on if item.id == job_id else item for item in self.rules]
        if not self._save(agents, rules):
            return
        if self._setup is not None and self._setup.job is not None and self._setup.job.id == job_id:
            self._setup = None
        logger.info("Standing: %s %s turned on", job.kind, job_id)
        self.host._jarvis(f"{job.name} is on.", link=(VIEW_TEXT, VIEW_LINK), context=False)
        self.reload(announce=False)

    def discard_setup(self) -> None:
        draft, self._setup = self._setup, None
        job = draft.job if draft is not None else None
        if job is not None and job.status in (STATUS_DRAFT, STATUS_TRIED):
            agents = [item for item in self.agents if item.id != job.id]
            rules = [item for item in self.rules if item.id != job.id]
            self._save(agents, rules)
            logger.info("Standing: draft %s %s discarded", job.kind, job.id)
        self.reload(announce=False)

    def prefill(self, kind: str) -> None:
        """New rule / New agent: Home, with "rule: " or "agent: " in the bar and the cursor after it."""
        from .ui import SCREEN_HOME

        reading = self.host.window.reading
        reading.set_tab(SCREEN_HOME)
        reading.command_bar.set_text(f"{kind}: ")
        reading.command_bar.focus_input()

    def edit_setup(self) -> None:
        """Edit: the owner's sentence back in the bar ("agent: ..." / "rule: ..."), to reword and set up again; the
        draft goes."""
        draft = self._setup
        if draft is None:
            return
        kind = draft.kind or (draft.job.kind if draft.job is not None else KIND_AGENT)
        self.discard_setup()
        bar = self.host.window.reading.command_bar
        bar.set_text(f"{kind}: {draft.words}")
        bar.focus_input()

    def _on_card(self, card_id: str, action: str) -> None:
        if card_id == START_CARD and action in (KIND_RULE, KIND_AGENT):
            self.prefill(action)
            return
        if card_id.startswith(TIDY_CARD):
            alias = card_id[len(TIDY_CARD):]
            if action == "signin":
                self.tidy_sign_in(alias)
            elif action == "later":
                self._tidy_later.add(alias)
                self._show()
            return
        if card_id == CONSENT_CARD:
            if action == "yes":
                if self.app_state.set_consent(True, self._now()):
                    logger.info("Standing: the owner said yes to the consent card")
                else:
                    self.host._flash_note("Jarvis couldn't save that (standing_app.json); nothing changed.")
            elif action == "later":
                self._consent_later = True
            self.reload(announce=False)
            return
        if card_id != SETUP_CARD or self._setup is None:
            return
        job = self._setup.job
        if action == "dry" and job is not None:
            self.dry_run(job.id)
        elif action == "on" and job is not None:
            self.turn_on(job.id)
        elif action == "edit":
            self.edit_setup()
        elif action in ("discard", "dismiss"):
            self.discard_setup()

    def _notice_cards(self) -> list[NoticeCard]:
        cards: list[NoticeCard] = []
        consented = self.app_state.consented()
        if not self.agents and not self.rules and self._setup is None:
            cards.append(NoticeCard(START_CARD, "Set one up", "Tell Jarvis what to keep an eye on", START_LINES,
                                    (CardButton("rule", "New rule", LOOK_GO),
                                     CardButton("agent", "New agent", LOOK_GHOST)), tone=holo.TONE_CYAN))
        if not consented and not self._consent_later and (self.agents or self.rules or self._setup is not None):
            cards.append(NoticeCard(CONSENT_CARD, standing_setup.CONSENT_KICKER, standing_setup.CONSENT_TITLE,
                                    standing_setup.consent_lines(self.settings),
                                    (CardButton("yes", standing_setup.CONSENT_YES, LOOK_AMBER),
                                     CardButton("later", standing_setup.CONSENT_LATER, LOOK_GHOST))))
        if consented:
            cards += self._tidy_cards()
        draft = self._setup
        if draft is None:
            return cards
        if draft.busy == "setup":
            cards.append(NoticeCard(SETUP_CARD, "Setting it up", draft.words,
                                    ("Jarvis is reading your sentence; this takes a few seconds.",), (),
                                    tone=holo.TONE_CYAN))
            return cards
        job = draft.job
        if job is None:
            cards.append(NoticeCard(SETUP_CARD, "Couldn't set that up", draft.words, (draft.note,),
                                    (CardButton("edit", "Edit", LOOK_GO), CardButton("dismiss", "Dismiss", LOOK_GHOST)),
                                    tone=holo.TONE_STEEL))
            return cards
        accounts = getattr(self.host, "_google_accounts", None) or {}
        lines = list(standing_setup.job_lines(job, self.config, self._hour24()))
        if draft.say:
            lines.append(f"Jarvis's note: {draft.say}")
        if draft.dry is not None:
            lines += list(standing_setup.dry_lines(job, draft.dry)) if isinstance(job, Rule) else []
        lines.append(standing_setup.PRIVACY_LINE)
        problem = standing_setup.turn_on_problem(job, self.config, accounts, consented)
        busy = draft.busy == "dry"
        buttons = []
        if isinstance(job, Rule):
            buttons.append(CardButton("dry", "Dry run again" if job.dry_run is not None else "Dry run", LOOK_GO,
                                      enabled=not busy))
        if job.status in (STATUS_DRAFT, STATUS_TRIED):
            buttons.append(CardButton("on", "Turn on", LOOK_AMBER, enabled=not problem and not busy, tip=problem))
        buttons += [CardButton("edit", "Edit", LOOK_GHOST, enabled=not busy),
                    CardButton("discard", "Discard", LOOK_DANGER, enabled=not busy)]
        kicker = ("New rule" if isinstance(job, Rule) else "New agent") + f" {DOT} check it first"
        note = ("Trying it on your recent mail..." if busy else draft.note or
                (problem if job.status in (STATUS_DRAFT, STATUS_TRIED) else ""))
        cards.append(NoticeCard(SETUP_CARD, kicker, job.name, tuple(lines), tuple(buttons), note=note))
        return cards

    # ---- telling the owner ----------------------------------------------------------------------------------------

    def _untold(self, run_ids: Iterable[str] | None = None) -> list[RunView]:
        """Runs that need the owner, not told yet and not cleared (``run_ids``: only those)."""
        wanted = set(run_ids) if run_ids is not None else None
        return [run for run in self._visible_runs() if run.needs_owner and not self.app_state.is_announced(run.id)
                and (wanted is None or run.id in wanted)]

    def has_news(self) -> bool:
        now = self._now()
        return any(run.ended is None or now - run.ended <= TELL_WITHIN for run in self._untold())

    def on_activation(self, message: Mapping[str, Any]) -> None:
        """A background check's {"cmd": "standing", "runs": [...]}: read again now and tell those runs."""
        runs = message.get("runs") if isinstance(message, Mapping) else None
        count = len(runs) if isinstance(runs, list) else 0
        logger.info("Standing: a background check says %d run(s) need you", count)
        self.reload(announce=True)

    def on_screen_shown(self) -> None:
        """The Agents screen was seen: the runs on it count as told (their line, if held, is not said)."""
        untold = [run.id for run in self._untold()]
        if untold:
            self.app_state.mark("announced", untold, self._now())
        self._held = []
        self.host.window.reading.dock.set_agents_new(False)

    def _announce_new(self) -> None:
        if self._on_screen() and self.host._window_active():
            self.on_screen_shown()
            return
        self.announce()

    def announce(self) -> None:
        """The runs that need the owner: one conversation entry for those not shown yet, and the fixed line said once
        (now, or when nothing holds it any more)."""
        if self.host.state != _reading_state():
            return
        now = self._now()
        untold = self._untold()
        old = [run.id for run in untold if run.ended is not None and now - run.ended > TELL_WITHIN]
        if old:
            self.app_state.mark("announced", old, now)            # too old to say: shown on the screen only
        fresh = [run for run in untold if run.id not in old]
        new = [run for run in fresh if run.id not in self._shown_runs]
        if new:
            self.host._jarvis(self._line(new), link=(VIEW_TEXT, VIEW_LINK), context=False)
            self._shown_runs.update(run.id for run in new)
        for run in fresh:
            if run.id not in self._held:
                self._held.append(run.id)
        self._say_held()

    def _holds(self) -> str:
        """Why the line waits now ("" when it may be said)."""
        host = self.host
        if host._locked_now():
            return "locked"
        if schedule.in_quiet(self._now(), self.settings.quiet_from, self.settings.quiet_until):
            return "quiet hours"
        if host._meeting_recording():
            return "a meeting records"
        if host._focus_quiet():
            return "focus mode"
        try:
            if self.busy_probe():
                return "Windows says busy"
        except Exception:  # noqa: BLE001 - unknown: not busy
            pass
        return ""

    def _say_held(self) -> None:
        held = self._untold(self._held)
        self._held = [run.id for run in held]
        if not held:
            return
        why = self._holds()
        if why:
            logger.info("Standing: the line for %d run(s) waits (%s)", len(held), why)
            if self._held_timer is None:
                self._held_timer = QTimer(self)
                self._held_timer.setSingleShot(True)
                self._held_timer.timeout.connect(self._say_held)
            self._held_timer.start(HELD_RECHECK_MS)
            return
        from .speech import KIND_NOTICE

        line = self._line(held, spoken=True)
        said = self.host._say(KIND_NOTICE, line, key=ANNOUNCE_KEY + held[-1].id)
        ids = [run.id for run in held]
        self.app_state.mark("announced", ids, self._now())
        self._held = []
        if not self.host._window_active():
            from .ui import TOAST_AGENTS

            self.host._post_toast(self._line(held), VIEW_TEXT, TOAST_AGENTS)
        logger.info("Standing: told %d run(s) (%s)", len(ids), "spoken" if said else "text only")

    def _line(self, runs: Sequence[RunView], *, spoken: bool = False) -> str:
        waiting = self._waiting_ids()
        run_ids = {run.id for run in runs}
        cards = sum(1 for card_id, run_id in self._card_runs.items() if run_id in run_ids and card_id in waiting)
        items = sum(1 for run in runs for receipt in run.receipts if receipt.get("kind") == "tell")
        items += sum(1 for run in runs if run.report)
        name = ""
        jobs = {run.job_id for run in runs}
        if spoken and len(jobs) == 1 and getattr(self.config.voice, "engine", "") == "local":
            job = self._jobs().get(next(iter(jobs)))
            name = job.name if job is not None else ""
        return announcement_line(cards, items, address=self.config.assistant.address, name=name)

    # ---- the click-time Reply check -------------------------------------------------------------------------------

    def reply_wait(self, action: ProposedAction) -> bool:
        """For Send on a standing Reply, just before its hidden hold: True while its thread is checked again (the
        check starts now if it has not passed in the last two minutes); False when it passed and Send may go on."""
        if action.kind != REPLY or action.mode not in STANDING_MODES:
            return False
        passed = self._reply_ok.get(action.id)
        if passed is not None and time.monotonic() - passed < REPLY_OK_FOR_S:
            return False
        if action.id in self._reply_checking:
            self.host._hint(action.id, REPLY_CHECKING_NOTE)
            return True
        original = self._loaded.get(action.id)
        if original is None:
            self.host._hint(action.id, REPLY_PROBLEMS["gone"])
            return True
        self._reply_seq += 1
        seq = self._reply_seq
        self._reply_checking[action.id] = (seq, time.monotonic())
        self.host._hint(action.id, REPLY_CHECKING_NOTE)
        alias = original.account
        thread_id, gmid = original.field("thread"), original.field("gmid")
        recipients = tuple(original.mail_recipients())
        reader = self.reader_for(alias)
        logger.info("Standing: checking the conversation of reply %s again before it goes", action.id)

        def work() -> None:
            self._replyChecked.emit(action.id, self._check_thread(reader, alias, thread_id, gmid, recipients), seq)

        self.thread_runner(work)
        return True

    @staticmethod
    def _in_thread(work: Callable[[], None]) -> None:
        threading.Thread(target=work, name="standing-reply-check", daemon=True).start()

    def _reader(self, alias: str) -> Any:
        """The app's own Gmail reader for ``alias``: Ask's, else one made on the app's Google account."""
        sources = getattr(getattr(self.host.ask, "planner", None), "sources", None)
        readers = getattr(sources, "readers", None)
        if isinstance(readers, Mapping) and readers.get(alias) is not None:
            return readers[alias]
        account = (getattr(self.host, "_google_accounts", None) or {}).get(alias)
        if account is None:
            return None
        try:
            from .executor import build_readers

            return build_readers(self.config, {alias: account}).get(alias)
        except Exception:  # noqa: BLE001 - no reader: the check refuses
            return None

    @staticmethod
    def _check_thread(reader: Any, alias: str, thread_id: str, gmid: str, recipients: Sequence[str]) -> str:
        if reader is None or not callable(getattr(reader, "thread", None)):
            return "no_reader"
        from .ask.mail import thread_from_api

        try:
            data = reader.thread(thread_id)
            thread = thread_from_api(data, alias)
        except Exception as exc:  # noqa: BLE001 - gone (404) or unreadable: nothing goes
            text = str(getattr(exc, "status", "") or "")
            return "gone" if "404" in text or "not found" in str(exc).casefold() else "unreadable"
        if thread is None:
            return "gone"
        try:
            own = list(reader.own_addresses())
        except Exception:  # noqa: BLE001 - without them a reply of the owner's can't be seen: refuse
            return "unreadable"
        return reply_problem(thread, gmid, recipients, own)

    def _on_reply_checked(self, action_id: str, problem: str, seq: int) -> None:
        checking = self._reply_checking.get(action_id)
        if checking is None or checking[0] != seq or self._closed:
            return
        del self._reply_checking[action_id]
        clicked = checking[1]
        host = self.host
        logger.info("Standing: reply %s checked again: %s", action_id, problem or "it may go")
        if problem:
            host._hint(action_id, REPLY_PROBLEMS.get(problem, REPLY_PROBLEMS["unreadable"]))
            return
        self._reply_ok[action_id] = time.monotonic()
        action = host._current(action_id)
        if (action is None or host._store.is_decided(action_id) or action_id in host._jobs
                or host.state != _reading_state() or host._send_hold is not None
                or time.monotonic() - clicked > REPLY_RESUME_S):
            host._hint(action_id, REPLY_AGAIN_NOTE)
            return
        host._hint(action_id, "")
        host._approve_mail(action)


def _warn_tone() -> str:
    from . import hud

    return hud.TONE_WARN


def _reading_state() -> str:
    from .ui import STATE_READING

    return STATE_READING
