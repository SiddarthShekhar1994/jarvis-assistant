"""Ask Jarvis in the app: the command bar's controller and the thread its planner runs on.

    AskController(config, planner, bar, *, context, blocked, calendars, parent)
        start()           checks Claude Code (no model request) and which accounts' mail Ask can read,
                          on the "ask" thread; the header chip and the bar follow
        submit(text)      one Ask (the bar's Enter / Ask): planner.plan() on the "ask" thread
        cancel()          ends a running Ask (the CLI is gone within a second); nothing is proposed
        refresh(full)     checks again: Claude Code and the mail after a failed run, only the mail
                          (full=False) after a Google sign-in
        shutdown()        cancels a running Ask and stops the thread (a daemon: never waited for)
    Signals (GUI thread):
        busyChanged(bool)            an Ask's planning really started (its first stage: the checks
                                     passed; the app pauses the briefing, the orb plans) / ended. An
                                     Ask refused before that (Claude Code missing or signed out, a
                                     limit) never pauses anything
        outcomeReady(object)         planner.AskOutcome of a finished Ask that was not cancelled
        chipChanged(str, str)        the header's ``claude`` chip: planner.CHIP_* and its tooltip
        mailSignInRequested(str)     the bar's link: sign this account in again so Ask may read its mail

The planner only proposes: its cards reach the app through ``outcomeReady`` and then go through the
same click, undo countdown and executor.run_action path as the briefing's. ``context()`` (GUI thread)
gives the briefing as Ask may see it and the briefing cards' ids; ``blocked()`` a reason not to start
now (a Google sign-in is open). Nothing here logs the request, the planner's text, the context or an
address: counts, kinds and durations only. One Ask at a time; a run that is cancelled or ends after
shutdown proposes nothing.
"""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from . import hud
from .ask import planner as ask_planner
from .ask.stream import CANCELLED, LIMIT
from .config import Config
from .google_auth import PROBLEM_EXPIRED, PROBLEM_SCOPE, PROBLEM_SIGNED_OUT, logged_alias

logger = logging.getLogger(__name__)

DOT = hud.MIDDLE_DOT
STAGE_TEXTS = {
    ask_planner.STAGE_CONTEXT: "Reading your calendar...",
    ask_planner.STAGE_PLANNING: "Planning...",
    ask_planner.STAGE_MAIL: "Searching your mail...",
    ask_planner.STAGE_PLANNING_AGAIN: "Planning with your mail...",
}
_TIMED_STAGES = (ask_planner.STAGE_PLANNING, ask_planner.STAGE_PLANNING_AGAIN)
RUNNING_META = f"uses your Claude plan {DOT} Esc cancels"
IDLE_HINT = ("Type a request: Jarvis plans it with your calendar and today's briefing, and every "
             "proposal waits under NEEDS YOUR OK")
CHECKING_TEXT = "Checking Claude Code..."
STARTING_TEXT = "Starting..."
CANCELLING_TEXT = "Cancelling..."
CANCELLED_TEXT = "Cancelled; nothing was proposed"
EMPTY_TEXT = "Type what you'd like Jarvis to do"
BUSY_TEXT = "Jarvis is still finishing the last request; try again in a moment"
NOTHING_TEXT = "Nothing to propose for that"
MAIL_LINK_TEXT = "Allow {alias} mail"
MAIL_LINK_TIP = ("{message}. Opens Google's sign-in for the {alias} account in your browser: tick the box for "
                 "reading email (read only; Ask uses it only for the request you type)")
_TICK_MS = 1000
# The header chip's colour for each planner.CHIP_* state.
CHIP_STATUSES = {ask_planner.CHIP_OK: hud.STATUS_OK, ask_planner.CHIP_SIGN_IN: hud.STATUS_WARN,
                 ask_planner.CHIP_LIMIT: hud.STATUS_WARN, ask_planner.CHIP_OFF: hud.STATUS_OFF,
                 ask_planner.CHIP_ERR: hud.STATUS_ERROR}
# What a mail reader's problem means for the bar: these are fixed by one more Google sign-in.
_SIGN_IN_FIXES = (PROBLEM_SIGNED_OUT, PROBLEM_SCOPE, PROBLEM_EXPIRED)
_SOFT_KINDS = (ask_planner.EMPTY, ask_planner.CAPS, "busy", CANCELLED)   # amber, not red


@dataclasses.dataclass(frozen=True)
class MailNeed:
    """An account whose mail Ask could read after one more Google sign-in (the message names no address)."""

    alias: str
    message: str


@dataclasses.dataclass(frozen=True)
class _CheckJob:
    seq: int
    full: bool = True          # Claude Code too (False: only which accounts' mail can be read)


@dataclasses.dataclass(frozen=True)
class _PlanJob:
    seq: int
    text: str
    briefing: Any
    page_ids: tuple[str, ...]
    cancel: threading.Event


@dataclasses.dataclass
class _Running:
    seq: int
    cancel: threading.Event
    started: float                      # time.monotonic() of the submit
    stage: str = ""                     # "" until the planner's first stage (its checks passed)
    cancel_requested: bool = False
    busy_sent: bool = False             # busyChanged(True) went out (on the first stage)


class _AskBridge(QObject):
    """Signals the ask thread emits; connected queued, so the slots run on the GUI thread."""

    checked = Signal(int, object, object, object)   # seq, Readiness, CapCheck, [MailNeed]
    stage = Signal(int, str)                        # seq, planner.STAGE_*
    planned = Signal(int, object)                   # seq, AskOutcome


class _AskWorker:
    """One daemon thread named "ask": readiness checks and planner runs, one at a time."""

    def __init__(self, planner: Any, bridge: _AskBridge, stop: threading.Event,
                 mail_needs: Callable[[], list[MailNeed]]) -> None:
        self._planner = planner
        self._bridge = bridge
        self._stop = stop
        self._mail_needs = mail_needs
        self._jobs: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="ask", daemon=True)
        self._thread.start()

    def put(self, job: Any) -> None:
        self._jobs.put(job)

    def stop(self) -> None:
        self._jobs.put(None)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None or self._stop.is_set():
                return
            try:
                if isinstance(job, _PlanJob):
                    self._plan(job)
                elif isinstance(job, _CheckJob):
                    self._check(job)
            except Exception as exc:  # noqa: BLE001 - a bug must not end the thread (or take the app down)
                logger.error("The Ask thread failed unexpectedly (%s)", type(exc).__name__)
                if isinstance(job, _PlanJob):
                    self._emit("planned", job.seq, ask_planner.AskOutcome(
                        False, "error", "Ask stopped because of an unexpected error; nothing was proposed"))

    def _check(self, job: _CheckJob) -> None:
        ready = self._planner.engine.readiness() if job.full else None   # None: keep the last answer
        caps = self._planner.usage.check()
        self._emit("checked", job.seq, ready, caps, self._mail_needs())

    def _plan(self, job: _PlanJob) -> None:
        def on_stage(stage: str) -> None:
            self._emit("stage", job.seq, stage)

        outcome = self._planner.plan(job.text, briefing=job.briefing, page_ids=job.page_ids, cancel=job.cancel,
                                     on_stage=on_stage)
        self._emit("planned", job.seq, outcome)

    def _emit(self, name: str, *args: Any) -> None:
        if self._stop.is_set():
            return
        try:
            getattr(self._bridge, name).emit(*args)
        except RuntimeError:
            pass   # the bridge was deleted during shutdown


class AskController(QObject):
    """The command bar's state machine: idle -> running (context, planning, mail, planning again)
    -> idle with the answer (or the reason nothing was proposed). See the module docs."""

    busyChanged = Signal(bool)
    outcomeReady = Signal(object)
    chipChanged = Signal(str, str)
    mailSignInRequested = Signal(str)

    def __init__(self, config: Config, planner: Any, bar: hud.CommandBar, *,
                 context: Callable[[], tuple[Any, Sequence[str]]],
                 blocked: Callable[[], str] = lambda: "",
                 calendars: Mapping[str, Any] | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.planner = planner
        self.bar = bar
        self._context = context
        self._blocked = blocked
        self._calendars = dict(calendars or {})
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._bridge = _AskBridge(self)
        queued = Qt.ConnectionType.QueuedConnection
        self._bridge.checked.connect(self._on_checked, queued)
        self._bridge.stage.connect(self._on_stage, queued)
        self._bridge.planned.connect(self._on_planned, queued)
        self._worker: _AskWorker | None = None
        self._seq = 0
        self._running: _Running | None = None
        self.ready: Any = None                 # planner.Readiness of the last check (None before it)
        self.caps: Any = None                  # usage.CapCheck after the last check or Ask
        self.mail_needs: list[MailNeed] = []
        self._limit_message = ""               # the Claude plan's own limit, until a run succeeds
        self._answered = False                 # the status line shows an answer (not the idle hint)
        self._tick = QTimer(self)
        self._tick.setInterval(_TICK_MS)
        self._tick.timeout.connect(self._show_stage)
        bar.submitted.connect(self.submit)
        bar.cancelRequested.connect(self.cancel)
        bar.linkClicked.connect(self._on_link)
        bar.statusDismissed.connect(self._show_idle)
        bar.set_status(CHECKING_TEXT, hud.TONE_IDLE)
        bar.set_meta(f"uses your Claude plan {DOT} nothing happens without your OK")

    # ---- lifecycle ----------------------------------------------------------------------------

    def start(self) -> None:
        """The ask thread, then a readiness check on it (no model request)."""
        if self._worker is None and not self._stop.is_set():
            self._worker = _AskWorker(self.planner, self._bridge, self._stop, self._read_mail_needs)
            logger.info("Ask Jarvis is on (model %s; at most %d planner runs an hour, %d a day)",
                        self.config.ask.model, self.config.ask.max_per_hour, self.config.ask.max_per_day)
        self.refresh()

    def refresh(self, full: bool = True) -> None:
        """Check Claude Code (``full``; it runs only --version, --help and auth status, and only when the
        last check failed) and which accounts' mail can be read, on the ask thread after anything queued."""
        if self._worker is not None and not self._stop.is_set():
            self._worker.put(_CheckJob(self._seq, full))

    def shutdown(self) -> None:
        running, self._running = self._running, None
        if running is not None:
            running.cancel.set()   # the CLI is killed within a second; its Job Object goes with the app
            logger.info("Ask cancelled (app closed); nothing was proposed")
        self._stop.set()
        self._tick.stop()
        if self._worker is not None:
            self._worker.stop()

    @property
    def busy(self) -> bool:
        return self._running is not None

    def stage(self) -> str:
        """The running Ask's planner.STAGE_* ("" when idle)."""
        return self._running.stage if self._running is not None else ""

    def join(self, timeout: float) -> None:
        """Tests: wait for the ask thread to end after shutdown()."""
        if self._worker is not None:
            self._worker.join(timeout)

    # ---- one Ask --------------------------------------------------------------------------------

    def submit(self, text: str) -> None:
        text = " ".join((text or "").split())
        if self._stop.is_set():
            return
        if self._running is not None:
            self._status(BUSY_TEXT if self._running.cancel_requested else
                         "Jarvis is already planning; wait or Cancel it", hud.TONE_WARN)
            return
        if not text:
            self._status(EMPTY_TEXT, hud.TONE_WARN)
            return
        reason = self._blocked()
        if reason:
            self._status(reason, hud.TONE_WARN)
            return
        if self._worker is None:
            self.start()
        assert self._worker is not None
        briefing, page_ids = self._context()
        self._seq += 1
        running = _Running(self._seq, threading.Event(), self._monotonic())
        self._running = running
        logger.info("Ask %d started", running.seq)
        self._worker.put(_PlanJob(running.seq, text, briefing, tuple(page_ids), running.cancel))
        self.bar.set_running(True)
        self.bar.set_meta(RUNNING_META)
        self._show_stage()
        self._tick.start()
        # busyChanged(True) waits for the first stage: an Ask the planner refuses (Claude Code
        # missing or signed out, a limit) never pauses the briefing.

    def cancel(self) -> None:
        running = self._running
        if running is None or running.cancel_requested:
            return
        running.cancel_requested = True
        running.cancel.set()
        logger.info("Ask %d: Cancel clicked", running.seq)
        self.bar.set_cancel_enabled(False)
        self._status(CANCELLING_TEXT, hud.TONE_WORKING)

    def _on_stage(self, seq: int, stage: str) -> None:
        running = self._running
        if running is None or seq != running.seq or running.cancel_requested:
            return
        running.stage = stage
        if not running.busy_sent:
            running.busy_sent = True
            self.busyChanged.emit(True)
        self._show_stage()

    def _show_stage(self) -> None:
        running = self._running
        if running is None or running.cancel_requested:
            return
        text = STAGE_TEXTS.get(running.stage, STARTING_TEXT)
        if running.stage in _TIMED_STAGES:
            text += f" {round(self._monotonic() - running.started)} s"
        self._status(text, hud.TONE_WORKING)

    def _on_planned(self, seq: int, outcome: Any) -> None:
        running = self._running
        if running is None or seq != running.seq:
            return   # cancelled and replaced, or the app is closing
        self._running = None
        self._tick.stop()
        self.bar.set_running(False)
        if outcome.caps is not None:
            self.caps = outcome.caps
        if running.cancel_requested or outcome.kind == CANCELLED:
            logger.info("Ask %d: cancelled; nothing was proposed", seq)
            self._status(CANCELLED_TEXT, hud.TONE_IDLE)
            self._show_meta()
            if running.busy_sent:
                self.busyChanged.emit(False)
            self._update_chip()
            return
        if outcome.ok:
            self._limit_message = ""
            keep_link = bool(outcome.mail_needed) and any(need.alias == outcome.mail_needed for need in self.mail_needs)
            self._status(self._answer(outcome), hud.TONE_WARN if outcome.question and not outcome.cards
                         else hud.TONE_DONE, keep_link=keep_link)
            if any(not card.error for card in outcome.cards):
                self.bar.clear_input()   # a question back or nothing to propose keeps the draft to edit
        else:
            if outcome.kind == LIMIT:
                self._limit_message = outcome.message
            tone = hud.TONE_WARN if outcome.kind in _SOFT_KINDS else hud.TONE_ERROR
            self._status(outcome.message or "Ask failed; nothing was proposed", tone)
        self._show_meta(outcome)
        if running.busy_sent:
            self.busyChanged.emit(False)
        self.outcomeReady.emit(outcome)
        self._update_chip()
        if not outcome.ok and outcome.kind not in (ask_planner.EMPTY, "busy"):
            self.refresh()   # a failed run checks Claude Code again (the planner forgot its readiness)
        elif outcome.mail.threads or outcome.search or outcome.message:
            self.refresh(full=False)   # mail was read (or could not be): which accounts may read now

    @staticmethod
    def _answer(outcome: Any) -> str:
        """The planner's words and Jarvis's own note as a sentence of its own (never in brackets).
        With nothing to decide the note comes first: it says what to do (and survives a cut)."""
        parts = [part for part in (outcome.say, outcome.question) if part]
        if not parts and not outcome.cards:
            parts.append(NOTHING_TEXT + ("." if outcome.message else ""))
        note = (outcome.message or "").strip()
        if note:
            note = note[:1].upper() + note[1:]
            note = note if note.endswith((".", "!", "?", ")")) else note + "."
            first = not any(not card.error for card in outcome.cards)
            parts = [note, *parts] if first else [*parts, note]
        return " ".join(parts)

    # ---- checks, chip and the idle bar -------------------------------------------------------

    def _on_checked(self, _seq: int, ready: Any, caps: Any, needs: Any) -> None:
        if self._stop.is_set():
            return
        if ready is not None:
            self.ready = ready
        self.caps, self.mail_needs = caps, list(needs or [])
        self._update_chip()
        self._update_link()
        if self._running is None and not self._answered:
            self._show_idle()

    def _show_idle(self) -> None:
        """The bar with nothing to report: why Ask can't run (Claude Code, a limit), or the hint,
        which folds away (the last answer is put away)."""
        if self._running is not None or self._stop.is_set():
            return
        self._answered = False
        ready = self.ready
        caps = self.caps
        if ready is None:
            self.bar.set_status(CHECKING_TEXT, hud.TONE_IDLE)
        elif not ready.ok:
            self.bar.set_status(ready.message, hud.TONE_WARN)
        elif caps is not None and not caps.allowed:
            self.bar.set_status(caps.message, hud.TONE_WARN)
        elif self._limit_message:
            self.bar.set_status(self._limit_message, hud.TONE_WARN)
        else:
            self.bar.set_hint(IDLE_HINT)
        self._show_meta()

    def _status(self, text: str, tone: str, *, keep_link: bool = False) -> None:
        self._answered = True
        self.bar.set_status(text, tone, keep_link=keep_link)

    def _show_meta(self, outcome: Any = None) -> None:
        parts: list[str] = []
        if outcome is not None and outcome.ok:
            cards = sum(1 for card in outcome.cards if not card.error)
            parts.append(f"{cards} proposal{'' if cards == 1 else 's'} under ASK" if cards else "no proposals")
            if outcome.refused:
                parts.append(f"{outcome.refused} not doable")
        if outcome is not None and outcome.runs:
            parts.append(f"{outcome.duration_ms / 1000:.1f} s")
        caps = self.caps
        if caps is not None:
            parts.append(f"{caps.left_hour} run{'' if caps.left_hour == 1 else 's'} left this hour")
        if not parts:
            parts.append("uses your Claude plan")
        self.bar.set_meta(f" {DOT} ".join(parts))

    def chip(self) -> tuple[str, str]:
        """The ``claude`` chip now: (planner.CHIP_*, tooltip)."""
        if self.ready is None:
            return ask_planner.CHIP_OFF, "Ask Jarvis: checking Claude Code..."
        if self._limit_message and self.ready.ok:
            return ask_planner.CHIP_LIMIT, self._limit_message
        state, tip = ask_planner.chip_state(self.config.ask.enabled, self.ready, self.caps)
        return state, tip

    def _update_chip(self) -> None:
        state, tip = self.chip()
        self.chipChanged.emit(state, f"Ask Jarvis: {state} - {tip}" if tip else f"Ask Jarvis: {state}")

    def _read_mail_needs(self) -> list[MailNeed]:
        """Ask thread: the accounts whose mail one more Google sign-in would let Ask read (files only)."""
        if not self.config.ask.read_mail:
            return []
        sources = getattr(self.planner, "sources", None)
        reader_of = getattr(sources, "reader", None)
        if not callable(reader_of):
            return []
        needs = []
        for alias in self.config.accounts:
            reader = reader_of(alias)
            if reader is None:
                continue
            try:
                problem, message = reader.available()
            except Exception as exc:  # noqa: BLE001 - only decides whether the bar offers a sign-in
                logger.debug("Ask: could not check the %s account's mail (%s)", logged_alias(alias), type(exc).__name__)
                continue
            if problem not in _SIGN_IN_FIXES:
                continue
            calendar = self._calendars.get(alias)
            if calendar is None:
                continue   # the sign-in goes through the account's calendar (every feature at once)
            if problem == PROBLEM_SIGNED_OUT and not _signed_in(calendar):
                continue   # not signed in at all: the account's own Sign in / Connect is the way
            needs.append(MailNeed(alias, message))
        return needs

    def _update_link(self) -> None:
        need = self.mail_needs[0] if self.mail_needs else None
        if need is None:
            self.bar.set_link("")
            return
        self.bar.set_link(MAIL_LINK_TEXT.format(alias=need.alias),
                          MAIL_LINK_TIP.format(message=need.message.rstrip("."), alias=need.alias))

    def _on_link(self) -> None:
        need = self.mail_needs[0] if self.mail_needs else None
        if need is not None:
            logger.info("Ask: sign-in to read the %s account's mail requested", logged_alias(need.alias))
            self.mailSignInRequested.emit(need.alias)


def _signed_in(calendar: Any) -> bool:
    try:
        return bool(calendar.is_signed_in())
    except Exception:  # noqa: BLE001
        return False


def default_planner(config: Config, calendars: Mapping[str, Any], senders: Mapping[str, Any],
                    accounts: Mapping[str, Any] | None = None) -> Any:
    """The app's planner: the owner's Claude Code (ClaudeEngine) and the app's own Google objects,
    read only (GoogleSources). ``accounts`` are executor.build_accounts' (shared with the calendars
    and senders, one sign-in each); the Gmail readers of the "gmail_read" accounts are built on them."""
    from .executor import build_readers

    readers = build_readers(config, accounts) if accounts and config.ask.read_mail else {}
    engine = ask_planner.ClaudeEngine(config.ask, config.data_dir)
    sources = ask_planner.GoogleSources(config, calendars, senders, readers)
    return ask_planner.AskPlanner(config, engine, sources)
