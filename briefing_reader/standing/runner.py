"""One standing check (no Qt): what is due, run it, keep what it made, tell the owner. ``--standing-run`` makes one.

    run_check(config, deps, *, origin) -> CheckResult
        1. Only when [standing] and [ask] are on, the owner consented (the Agents screen's card) and Pause all is
           off; anything else ends at once with nothing read.
        2. The live jobs (standing.json), the runner's own state, the Run now requests.
        3. What is due: a timed job's slot (a first sight is a baseline; a slot older than catch_up_hours is
           missed, once), an interval, new mail every poll_minutes (one probe per account first, then each mail
           job's filter: headers only, free), Run now.
        4. Holds: the shared extra-usage hold (ask_usage.json), the standing hold (a plan limit until its reset, 6
           hours when none is named; Claude Code's near-limit warning until its reset): no Claude run meanwhile.
        5. Each due job, until the deadline: the caps (every job together, the job's own, a rule's checks a day),
           its sources read by Jarvis (the matched messages, scrubbed; an agent's calendar days and notes), one run,
           what it made (cards checked and never proposed twice, receipts), one run file. A job with nothing new
           makes no Claude run and no file. A rule's mail mark moves only after its run went through.
           A rule's tidy-up (star, its label, archive: only the matched message) happens for each "yes" that passes
           every gate (``_tidy``): the rule's own holds (orders in the batch, a yes rate far above the dry run's, a
           failed DMARC check, the owner's own or sent mail, starred or important mail for an archive), then Pause
           all read again, the account's gmail_tidy sign-in still the one saved and still the Google account Turn
           on recorded, a filter that names a sender and a dry run of at most 90 matches, never archiving mail from
           someone the owner has written to (recipients.json, read only), and the rule's tidy-ups a day. Each
           change is journalled before the call (standing_tidy.jsonl) and its receipt says what happened.
        6. Old run files pruned, the state saved, the heartbeat written.
        7. Telling the owner, for runs that need them: a running Jarvis gets the activation {"cmd": "standing"};
           a closed one is started with --standing-show (shown without focus, announced once) unless it is quiet
           hours or [standing] open_when_closed is false; what waits is told at the first check after.
    Deps: what a check uses of the world (readers, calendars, the engine, the notes, the clock, the app), so the
    tests run it with fakes; real_deps(config) builds the real ones (Google sign-ins in memory only).

Logs: one line per check and per run with ids, kinds, counts, durations and statuses only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..ask import stream
from . import agents, rules, schedule
from .mailwatch import Batch, anything_new, new_mail, read_message
from .model import (KIND_AGENT, KIND_RULE, TRIGGER_EVERY, TRIGGER_MAIL, TRIGGER_MANUAL, TRIGGER_TIME, Agent, Rule,
                    live)
from .rules import Item
from . import tidy as tidy_mod
from .dryrun import NARROW_FOR_TIDY
from .filters import senders as filter_senders
from .model import TIDY_ARCHIVE, TIDY_NONE
from .store import (AppState, Definitions, RunFiles, RunnerState, RunRequests, StandingFiles, Usage, new_run_id,
                    parse_stamp, stamp)

logger = logging.getLogger(__name__)

ORIGIN_TASK, ORIGIN_APP, ORIGIN_NOW = "task", "app", "now"
DEADLINE_S = 15 * 60
PLAIN_LIMIT_HOLD = timedelta(hours=6)
WARNING_HOLD = timedelta(hours=2)
RULE_RUNS_PER_DAY = 48            # a rule's runs are capped by its checks a day; this is a backstop
# A check's and a run's statuses (logged, kept in the run file, shown on the Agents screen).
OFF, NO_CONSENT, PAUSED, BUSY, DONE = "off", "no_consent", "paused", "busy", "done"
RUN_OK, RUN_NOTHING, RUN_CAPPED, RUN_HELD, RUN_FAILED, RUN_SIGNED_OUT, RUN_MISSED, RUN_LATE = (
    "ok", "nothing", "capped", "held", "failed", "signed_out", "missed", "late")


@dataclass
class Deps:
    readers: Mapping[str, Any]                       # alias -> a GmailReader (list_messages, message_metadata, ...)
    engine: Any                                      # a StandingEngine (run(...))
    calendars: Mapping[str, Any] = field(default_factory=dict)   # alias -> list_events(start, end)
    notes: Callable[[], Sequence[str]] = lambda: ()
    own: Callable[[], Sequence[str]] = lambda: ()
    clock: Callable[[], datetime] = lambda: datetime.now().astimezone()
    app_running: Callable[[], bool] = lambda: False
    tell_app: Callable[[Sequence[str]], bool] = lambda runs: False
    start_app: Callable[[], bool] = lambda: False
    extra_usage_held: Callable[[], bool] = lambda: False
    hold_extra_usage: Callable[[datetime], None] = lambda until: None   # Ask's shared hold (ask_usage.json)
    tidy: Mapping[str, Any] = field(default_factory=dict)      # alias -> its tidy-up client (tidy.GmailTidy)
    written_to: Callable[[str], bool] = lambda address: True   # the owner has written to this address (read only)
    time_zone_name: str = ""
    deadline_s: float = DEADLINE_S


@dataclass
class CheckResult:
    status: str
    runs: list[str] = field(default_factory=list)            # the run files written
    told: list[str] = field(default_factory=list)            # the runs the owner was told of
    counts: dict[str, int] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"CheckResult({self.status}, {len(self.runs)} run(s), told {len(self.told)})"


class _Check:
    def __init__(self, config: Any, deps: Deps, origin: str) -> None:
        self.config = config
        self.cfg = config.standing
        self.deps = deps
        self.origin = origin
        self.files = StandingFiles(config.data_dir)
        self.now = deps.clock()
        self.state = RunnerState(self.files)
        self.usage = Usage(self.files, clock=deps.clock)
        self.run_files = RunFiles(self.files)
        self.result = CheckResult(DONE)
        self.cancel = threading.Event()
        self.started = time.monotonic()
        self._own: list[str] | None = None
        self._probed: dict[str, bool] = {}

    # ---- helpers ------------------------------------------------------------------------------------------------

    def own(self) -> list[str]:
        if self._own is None:
            try:
                self._own = [address.casefold() for address in self.deps.own()]
            except Exception as exc:  # noqa: BLE001 - a run without them refuses replies to the owner anyway
                logger.info("Standing: the owner's addresses could not be read (%s)", type(exc).__name__)
                self._own = []
        return self._own

    def late(self) -> bool:
        return time.monotonic() - self.started >= self.deps.deadline_s

    def held(self) -> bool:
        if self.deps.extra_usage_held():
            return True
        until, _why = self.state.hold_until()
        return until is not None and self.now < until

    def note_limit(self, outcome: Any) -> None:
        """A run's limit or warning: the standing hold until it resets."""
        limit = getattr(outcome, "limit", None)
        failure = getattr(outcome, "failure", None)
        if limit is not None or (failure is not None and failure.kind == stream.LIMIT):
            resets = getattr(limit, "resets_at", None) if limit is not None else None
            overage = bool(getattr(limit, "overage", False)) or (failure is not None and failure.detail == stream.OVERAGE)
            until = resets or self.now + PLAIN_LIMIT_HOLD
            self.state.set_hold(until, "overage" if overage else "limit")
            if overage:
                # Usage credits would pay for runs: Ask pauses too, as its own runs do (ask.usage.hold).
                try:
                    self.deps.hold_extra_usage(until)
                except Exception as exc:  # noqa: BLE001 - the standing hold holds anyway
                    logger.info("Standing: Ask's extra-usage pause could not be set (%s)", type(exc).__name__)
        elif getattr(outcome, "warning", False):
            self.state.set_hold(getattr(outcome, "warning_resets", None) or self.now + WARNING_HOLD, "warning")

    def write(self, job: Agent | Rule, *, trigger: str, status: str, started: datetime, **parts: Any) -> str:
        run_id = new_run_id()
        record = {"id": run_id, "job": {"kind": job.kind, "id": job.id, "name": job.name}, "trigger": trigger,
                  "origin": self.origin, "started": stamp(started), "ended": stamp(self.deps.clock()),
                  "status": status, **parts}
        try:
            path = self.run_files.write(record, started)
        except (ValueError, FileExistsError):
            path = None
        if path is not None:
            self.result.runs.append(run_id)
            if parts.get("needs_owner"):
                self.state.to_announce(run_id)
        return run_id

    def reader(self, alias: str) -> Any | None:
        reader = self.deps.readers.get(alias)
        if reader is None:
            return None
        available = getattr(reader, "available", None)
        if callable(available):
            problem, _message = available()
            if problem:
                self.state.data.setdefault("problems", {})[alias] = problem
                return None
        return reader

    def mail_due(self, job_id: str, alias: str) -> bool:
        entry = self.state.mail(job_id, alias)
        checked = parse_stamp(entry.get("checked"))
        return checked is None or self.now - checked >= timedelta(minutes=self.cfg.poll_minutes) - timedelta(seconds=30)

    def probe(self, alias: str, reader: Any, after_ms: int) -> bool:
        """Is there any new mail at all on ``alias`` (once per check, from the oldest mark asked about)?"""
        if alias not in self._probed:
            try:
                self._probed[alias] = anything_new(reader, after_ms)
            except Exception as exc:  # noqa: BLE001 - the per-filter lists then decide
                logger.info("Standing: the new-mail probe failed (%s)", type(exc).__name__)
                self._probed[alias] = True
        return self._probed[alias]

    def batch(self, job_id: str, alias: str, query: str, max_new: int) -> Batch | None:
        reader = self.reader(alias)
        if reader is None:
            return None
        entry = self.state.mail(job_id, alias)
        mark = entry.get("mark_ms")
        if isinstance(mark, int) and mark > 0 and not self.probe(alias, reader, mark):
            entry["checked"] = stamp(self.now)
            return Batch(mark_ms=mark, seen=tuple(entry.get("seen") or ()))
        try:
            found = new_mail(reader, query, entry, self.now, max_new=max_new)
        except Exception as exc:  # noqa: BLE001 - one filter's failure never stops the others
            logger.info("Standing: a filter's check failed (%s)", type(exc).__name__)
            return None
        entry["checked"] = stamp(self.now)
        if found.baseline:
            entry["mark_ms"] = found.mark_ms
        return found

    def keep_mark(self, job_id: str, alias: str, found: Batch) -> None:
        entry = self.state.mail(job_id, alias)
        entry["mark_ms"] = found.mark_ms
        entry["seen"] = list(found.seen)

    def items(self, refs: Sequence[Any]) -> list[Item]:
        made: list[Item] = []
        for ref in refs:
            reader = self.deps.readers.get(ref.account)
            if reader is None:
                continue
            try:
                message, _left = read_message(reader, ref, chars=self.cfg.mail_chars)
            except Exception as exc:  # noqa: BLE001 - that one mail is left out
                logger.info("Standing: a matched mail could not be read (%s)", type(exc).__name__)
                continue
            if message is not None:
                made.append(Item(ref, message))
        return made

    # ---- rules --------------------------------------------------------------------------------------------------

    def rule(self, rule: Rule, *, forced: bool = False) -> None:
        if not forced and not self.mail_due(rule.id, rule.account):
            return
        left = rule.checks_per_day - self.usage.today().checks_of(rule.id)
        if left <= 0:
            self.capped(rule, "checks")
            return
        found = self.batch(rule.id, rule.account, rule.query, min(self.cfg.rule_batch, left))
        if found is None or found.baseline or not found.refs:
            return
        if self.held():
            self.state.checked(self.origin, RUN_HELD, self.now)
            return
        if not self.usage.try_run(rule.id, total_cap=self.cfg.max_runs_per_day, job_cap=RULE_RUNS_PER_DAY):
            self.capped(rule, "runs")
            return
        started = self.deps.clock()
        items = self.items(found.refs)
        if not items:
            self.keep_mark(rule.id, rule.account, found)
            return
        prompt = rules.rule_prompt(rule, items, now=self.now, time_zone_name=self.deps.time_zone_name)
        outcome = self.deps.engine.run(prompt, kind=KIND_RULE, model=self.cfg.rule_model,
                                       timeout_s=self.cfg.timeout_seconds, read=rules.read_rule, cancel=self.cancel)
        self.note_limit(outcome)
        if outcome.failure is not None:
            self.write(rule, trigger=TRIGGER_MAIL, status=RUN_FAILED, started=started,
                       problem=outcome.failure.kind, counts={"mails": len(items)})
            return                                          # the mark stays: the mail is checked again next time
        self.usage.add("checks", rule.id, len(items))
        checked = rules.outcomes(rule, items, outcome.answer, now=self.now, own=self.own())
        cards: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        for outcome_ in checked.outcomes:
            if not outcome_.yes:
                continue
            receipt = _receipt(len(receipts), outcome_.ref)
            if outcome_.card is not None and not self.state.proposed(rule.id, outcome_.key):
                from .cards import record

                cards.append(record(outcome_.card, line=outcome_.line, job={"kind": KIND_RULE, "id": rule.id},
                                    key=outcome_.key, source=_source(outcome_.ref)))
                self.state.note_proposed(rule.id, outcome_.key, self.now.date())
                receipt.update(kind="card", card=outcome_.card.id, title=outcome_.card.title,
                               card_kind=outcome_.card.kind)
            else:
                receipt.update(kind="tell", values=dict(outcome_.values), refused=outcome_.refused)
            if rule.tidy.kind:
                receipt["tidy"] = self._tidy(rule, outcome_)
            receipts.append(receipt)
        self.keep_mark(rule.id, rule.account, found)
        needs = bool(cards) or any(receipt["kind"] == "tell" for receipt in receipts)
        self.write(rule, trigger=TRIGGER_MAIL, status=RUN_OK, started=started, cards=cards, receipts=receipts,
                   needs_owner=needs, batch_held=checked.batch_held, more=found.more, flood=found.full,
                   counts={"mails": len(items), "yes": checked.yes, "cards": len(cards)})
        logger.info("Standing: rule %s checked %d mail(s): %d yes, %d card(s)%s", rule.id, len(items), checked.yes,
                    len(cards), " (a flood: more than one check takes)" if found.full else "")

    def _tidy_held(self, rule: Rule, outcome: Any) -> str:
        """Why this "yes" gets no tidy-up now ("" when every gate passes; see the module docstring)."""
        if outcome.tidy_held:
            return outcome.tidy_held
        if AppState(self.files).paused_all:
            return tidy_mod.HELD_PAUSED
        account = (getattr(self.config, "accounts", None) or {}).get(rule.account)
        if tidy_mod.TIDY_FEATURE not in tuple(getattr(account, "features", ()) or ()):
            return tidy_mod.HELD_NOT_SET_UP
        client = self.deps.tidy.get(rule.account)
        if client is None or not AppState(self.files).tidy_consented(rule.account):
            return tidy_mod.HELD_NOT_SIGNED_IN
        google = client.account
        try:
            problem, _message = google.change_problem()
            saved = google.still_saved()
            binding = google.binding()
        except Exception:  # noqa: BLE001 - unknown: no change
            return tidy_mod.HELD_ACCOUNT
        recorded = dict(rule.bindings).get(rule.account)
        if problem or not saved or binding is None or not recorded or getattr(binding, "sub", "") != recorded:
            return tidy_mod.HELD_ACCOUNT
        if not filter_senders(rule.query) or rule.dry_run is None or rule.dry_run.matched > NARROW_FOR_TIDY:
            return tidy_mod.HELD_BROAD
        if rule.tidy.kind == TIDY_ARCHIVE:
            sender = outcome.ref.sender.email if outcome.ref.sender is not None else ""
            try:
                written = bool(self.deps.written_to(sender))
            except Exception:  # noqa: BLE001 - unknown: never archived
                written = True
            if not sender or written:
                return tidy_mod.HELD_WRITTEN
        if self.usage.today().tidy_of(rule.id) >= self.cfg.tidy_per_day:
            return tidy_mod.HELD_CAP
        return ""

    def _tidy(self, rule: Rule, outcome: Any) -> dict[str, Any]:
        """The rule's tidy-up of one matched message, every gate first; what its receipt keeps of it."""
        kind = rule.tidy.kind
        made: dict[str, Any] = {"kind": kind, "held": "", "done": False}
        if not outcome.yes or kind == TIDY_NONE:
            made["held"] = outcome.tidy_held or "no"
            return made
        held = self._tidy_held(rule, outcome)
        if held:
            made["held"] = held
            return made
        client = self.deps.tidy[rule.account]
        ref = outcome.ref
        try:
            change = tidy_mod.change_for(rule)
            before = client.labels_of(ref.message_id)
        except Exception as exc:  # noqa: BLE001 - nothing changed
            logger.info("Standing: rule %s: the tidy-up could not read the message (%s)", rule.id, type(exc).__name__)
            made["held"] = tidy_mod.HELD_FAILED
            return made
        if change.done_in(before):
            made["held"] = tidy_mod.HELD_ALREADY
            return made
        journal = tidy_mod.TidyJournal(self.files.tidy_journal)
        try:
            entry = journal.begin(rule=rule.id, account=rule.account, message=ref.message_id, thread=ref.thread_id,
                                  kind=kind, change=change, before=before, now=self.deps.clock())
        except OSError:
            made["held"] = tidy_mod.HELD_FAILED           # no journal line, no change
            return made
        try:
            after = client.modify(ref.message_id, add=change.add, remove=change.remove)
        except Exception as exc:  # noqa: BLE001 - Gmail refused it: the journal says failed
            logger.info("Standing: rule %s: a tidy-up was refused (%s)", rule.id, type(exc).__name__)
            try:
                journal.failed(entry, self.deps.clock())
            except OSError:
                pass
            made.update(held=tidy_mod.HELD_FAILED, tidy=entry)
            return made
        try:
            journal.done(entry, after, self.deps.clock())
        except OSError:
            pass                                          # read back as unknown: the receipt still says done
        self.usage.add("tidy", rule.id, 1)
        stays = False
        if kind == TIDY_ARCHIVE:
            try:
                stays = client.thread_in_inbox(ref.thread_id, but=ref.message_id)
            except Exception:  # noqa: BLE001 - not known: not said
                stays = False
        made.update(done=True, tidy=entry, add=list(change.add), remove=list(change.remove), before=sorted(before),
                    after=sorted(after), thread_stays=stays, at=stamp(self.deps.clock()))
        return made

    # ---- agents -------------------------------------------------------------------------------------------------

    def agent(self, agent: Agent, *, forced: bool = False) -> None:
        entry = self.state.job(agent.id)
        trigger = agent.trigger
        if forced:
            due_kind = TRIGGER_MANUAL
        elif trigger.kind == TRIGGER_TIME:
            due = schedule.due_time(trigger, self.now, str(entry.get("due_key") or ""), self.cfg.catch_up_hours)
            if due.state in (schedule.BASELINE, schedule.MISSED):
                entry["due_key"] = due.key
            if due.state == schedule.MISSED:
                self.write(agent, trigger=TRIGGER_TIME, status=RUN_MISSED, started=self.now, slot=due.key)
            if not due.runs:
                return
            entry["due_key"] = due.key
            due_kind = TRIGGER_TIME
        elif trigger.kind == TRIGGER_EVERY:
            due = schedule.due_every(trigger, self.now, parse_stamp(entry.get("last_run")))
            if due.state == schedule.BASELINE:
                entry["last_run"] = stamp(self.now)
            if not due.runs:
                return
            due_kind = TRIGGER_EVERY
        elif trigger.kind == TRIGGER_MAIL:
            due_kind = TRIGGER_MAIL
        else:
            return
        found: dict[str, Batch] = {}
        refs: list[Any] = []
        for source in agent.mail:
            if due_kind == TRIGGER_MAIL and not self.mail_due(agent.id, source.account):
                continue
            batch = self.batch(agent.id, source.account, source.query, self.cfg.max_mail_per_run)
            if batch is None or batch.baseline:
                continue
            found[source.account] = batch
            refs.extend(batch.refs)
        if due_kind == TRIGGER_MAIL and not refs:
            return
        events = self.events(agent) if agent.calendar_days else []
        notes = list(self.deps.notes()) if agent.notes else []
        if not refs and not events and not notes:
            entry["last_run"] = stamp(self.now)
            return
        if self.held():
            self.state.checked(self.origin, RUN_HELD, self.now)
            return
        if not self.usage.try_run(agent.id, total_cap=self.cfg.max_runs_per_day, job_cap=agent.runs_per_day):
            self.capped(agent, "runs")
            return
        started = self.deps.clock()
        items = self.items(refs[:self.cfg.max_mail_per_run])
        aliases = sorted(getattr(self.config, "accounts", {}) or {"personal": None})
        prompt, index = agents.agent_prompt(agent, items, events, notes, now=self.now,
                                            time_zone_name=self.deps.time_zone_name, own=self.own(), aliases=aliases)
        outcome = self.deps.engine.run(prompt, kind=KIND_AGENT, model=self.cfg.model,
                                       timeout_s=self.cfg.timeout_seconds, read=agents.read_agent, cancel=self.cancel)
        self.note_limit(outcome)
        if outcome.failure is not None:
            self.write(agent, trigger=due_kind, status=RUN_FAILED, started=started, problem=outcome.failure.kind,
                       counts={"mails": len(items)})
            return
        made = agents.agent_cards(agent, outcome.answer, index, items, now=self.now, notes=notes, own=self.own())
        from .cards import record

        cards = []
        for card, line, key in made:
            if self.state.proposed(agent.id, key):
                continue
            cards.append(record(card, line=line, job={"kind": KIND_AGENT, "id": agent.id}, key=key))
            self.state.note_proposed(agent.id, key, self.now.date())
        for alias, batch in found.items():
            self.keep_mark(agent.id, alias, batch)
        entry["last_run"] = stamp(self.now)
        answer = outcome.answer
        self.write(agent, trigger=due_kind, status=RUN_OK, started=started, report=answer.report,
                   summary=answer.summary, needs_owner=agents.needs_owner(agent, answer, cards), cards=cards,
                   receipts=[{"id": f"{len(cards)}-{n}", "kind": "card", "card": item["id"], "title": "",
                              "card_kind": item["kind"]} for n, item in enumerate(cards)],
                   counts={"mails": len(items), "events": len(events), "cards": len(cards)})
        logger.info("Standing: agent %s ran (%s): %d mail(s), %d event(s), %d card(s)", agent.id, due_kind,
                    len(items), len(events), len(cards))

    def events(self, agent: Agent) -> list[agents.EventRow]:
        rows: list[agents.EventRow] = []
        start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(1, agent.calendar_days))
        for alias, calendar in self.deps.calendars.items():
            try:
                found = calendar.list_events(start, end)
            except Exception as exc:  # noqa: BLE001 - the run goes on without that calendar
                logger.info("Standing: a calendar could not be read (%s)", type(exc).__name__)
                continue
            for event in list(found)[:60]:
                rows.append(agents.EventRow(alias, str(getattr(event, "title", "") or getattr(event, "summary", "")),
                                            getattr(event, "start", None), getattr(event, "end", None),
                                            bool(getattr(event, "all_day", False)),
                                            str(getattr(event, "location", "") or "")))
        return rows

    def capped(self, job: Agent | Rule, what: str) -> None:
        entry = self.state.job(job.id)
        today = self.now.date().isoformat()
        if entry.get("capped_day") == today:
            return
        entry["capped_day"] = today
        self.write(job, trigger=job.trigger.kind, status=RUN_CAPPED, started=self.now, problem=what)
        logger.info("Standing: %s %s reached its cap for today (%s)", job.kind, job.id, what)

    # ---- telling the owner ------------------------------------------------------------------------------------

    def tell(self) -> None:
        waiting = self.state.take_announce()
        if not waiting:
            return
        if self.deps.app_running():
            if self.deps.tell_app(waiting):
                self.result.told = waiting
                return
        elif (not schedule.in_quiet(self.now, self.cfg.quiet_from, self.cfg.quiet_until)
              and self.cfg.open_when_closed and self.deps.start_app()):
            self.result.told = waiting
            return
        for run_id in waiting:                     # kept for the first check after quiet hours (or a later start)
            self.state.to_announce(run_id)


def run_check(config: Any, deps: Deps, *, origin: str = ORIGIN_TASK) -> CheckResult:
    files = StandingFiles(config.data_dir)
    standing = getattr(config, "standing", None)
    if standing is None or not standing.enabled or not getattr(getattr(config, "ask", None), "enabled", False):
        return CheckResult(OFF)
    app = AppState(files)
    if not app.consented():
        return CheckResult(NO_CONSENT)
    if app.paused_all:
        return CheckResult(PAUSED)
    check = _Check(config, deps, origin)
    definitions = Definitions(files, min_every=standing.min_every_minutes)
    all_agents, all_rules = definitions.load()
    jobs = live([*all_agents, *all_rules])
    check.state.forget([job.id for job in [*all_agents, *all_rules]])
    asked = set(RunRequests(files).take())
    timer = threading.Timer(max(1.0, deps.deadline_s), check.cancel.set)
    timer.daemon = True
    timer.start()
    try:
        for job in jobs:
            if check.late():
                check.result.status = RUN_LATE
                break
            if app.paused_all:
                break
            try:
                if isinstance(job, Rule):
                    check.rule(job, forced=job.id in asked)
                else:
                    check.agent(job, forced=job.id in asked)
            except Exception as exc:  # noqa: BLE001 - one job's failure never stops the others
                logger.warning("Standing: %s %s stopped by an unexpected error (%s)", job.kind, job.id,
                               type(exc).__name__)
        check.run_files.prune(standing.keep_days, check.now)
        check.state.checked(origin, check.result.status, check.now)
        check.tell()
        check.state.save()
    finally:
        timer.cancel()
    logger.info("Standing: check (%s) %s: %d job(s) live, %d run(s), %d told", origin, check.result.status,
                len(jobs), len(check.result.runs), len(check.result.told))
    return check.result


def _source(ref: Any) -> dict[str, Any]:
    return {"account": ref.account, "message": ref.message_id, "thread": ref.thread_id, "msgid": ref.msgid,
            "from": getattr(ref.sender, "email", "") if ref.sender is not None else "",
            "name": getattr(ref.sender, "name", "") if ref.sender is not None else "",
            "subject": ref.subject, "received": ref.received.astimezone().isoformat(timespec="seconds")}


def _receipt(number: int, ref: Any) -> dict[str, Any]:
    return {"id": f"{ref.message_id}-{number}", "kind": "", **_source(ref)}


# --------------------------------------------------------------------------
# The real world
# --------------------------------------------------------------------------

def real_deps(config: Any, instance: str) -> Deps:
    """The check's real readers, calendars and engine (Google sign-ins in memory only: nothing is saved, deleted or
    moved), the owner's notes (read only), and the app: running is its single-instance mutex; told is the activation
    {"cmd": "standing"}; started is a detached ``--standing-show``."""
    from .. import executor, hotkey
    from ..ask.usage import USAGE_FILE, UsageLog
    from .engine import StandingEngine

    accounts = executor.build_accounts(config, persist=False)
    readers = executor.build_readers(config, accounts)
    tidy_clients = {alias: tidy_mod.GmailTidy(account)
                    for alias, account in executor.build_tidy_accounts(config, persist=False).items()}
    calendars = executor.build_calendars(config, accounts) if getattr(config.calendar, "enabled", True) else {}
    usage_log = UsageLog(config.data_dir / USAGE_FILE)

    def notes() -> list[str]:
        productivity = getattr(config, "productivity", None)
        if not getattr(productivity, "enabled", False) or not getattr(productivity, "memory", False):
            return []
        from ..memory import MEMORY_FILE, MemoryStore

        return [entry.text for entry in MemoryStore(config.data_dir / MEMORY_FILE, read_only=True).entries()][:40]

    def own() -> list[str]:
        found: list[str] = []
        for reader in readers.values():
            try:
                found.extend(reader.own_addresses())
            except Exception:  # noqa: BLE001 - that account's addresses are left out
                continue
        return found

    def start_app() -> bool:
        from .. import __main__ as entry

        return entry._start_detached(["--standing-show"], executable=entry._windowless_python())

    def held() -> bool:
        until = usage_log.hold_until()
        return until is not None

    from ..recipients import RECIPIENTS_FILE, RecipientHistory

    history = RecipientHistory(config.data_dir / RECIPIENTS_FILE)

    return Deps(readers=readers, engine=StandingEngine(config.ask, StandingFiles(config.data_dir)),
                calendars=calendars, notes=notes, own=own, tidy=tidy_clients, written_to=history.knows,
                app_running=lambda: hotkey.mutex_exists("Local\\" + instance),
                tell_app=lambda runs: hotkey.send_activation(instance, {"cmd": "standing", "runs": list(runs)[:50]}),
                start_app=start_app, extra_usage_held=held, hold_extra_usage=usage_log.hold,
                time_zone_name=datetime.now().astimezone().tzname() or "")


def describe(config: Any, now: datetime | None = None) -> list[str]:
    """``--standing-check``: what is set up and how the checks went (counts, times and statuses only)."""
    now = now or datetime.now().astimezone()
    files = StandingFiles(config.data_dir)
    standing = config.standing
    lines = [f"Standing agents and rules: {'on' if standing.enabled else 'off'} ([standing] enabled / "
             "JARVIS_STANDING_ENABLED)"]
    if not getattr(config.ask, "enabled", False):
        lines.append("  Ask Jarvis is off: standing jobs need it ([ask] enabled).")
    app = AppState(files)
    lines.append(f"  Consent: {'given' if app.consented() else 'not yet (the Agents screen asks first)'}; "
                 f"Pause all: {'on' if app.paused_all else 'off'}")
    agents_, rules_ = Definitions(files, min_every=standing.min_every_minutes).load()
    jobs = [*agents_, *rules_]
    lines.append(f"  Jobs: {len(agents_)} agent(s), {len(rules_)} rule(s); live: {len(live(jobs))}")
    state = RunnerState(files)
    at, origin, status = state.last_check()
    lines.append(f"  Last check: {at.strftime('%Y-%m-%d %H:%M') if at else 'never'}"
                 + (f" ({origin}, {status})" if at else ""))
    until, why = state.hold_until()
    if until is not None and until > now:
        lines.append(f"  Held until {until.strftime('%Y-%m-%d %H:%M')} ({why})")
    today = Usage(files).peek()
    lines.append(f"  Today: {today.runs} of {standing.max_runs_per_day} Claude runs")
    for job in live(jobs):
        entry = state.job(job.id)
        trigger = job.trigger
        nxt = (schedule.next_time(trigger, now) if trigger.kind == TRIGGER_TIME
               else schedule.next_every(trigger, parse_stamp(entry.get("last_run")), now)
               if trigger.kind == TRIGGER_EVERY else None)
        lines.append(f"  - {job.kind} {job.id}: {trigger.kind}"
                     + (f", next {nxt.strftime('%a %H:%M')}" if nxt else "")
                     + f", {today.job(job.id)} run(s) today")
    problems = state.data.get("problems") or {}
    for alias, problem in sorted(problems.items()):
        lines.append(f"  The {alias} account can't be read in the background now ({problem}): open Jarvis")
    return lines
