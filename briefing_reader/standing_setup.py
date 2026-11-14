"""Setting standing jobs up in the app (no widgets): the setup run from the owner's own sentence, a rule's dry run,
Turn on's checks, and the words of the setup and consent cards.

    app_engine(config)           the app's StandingEngine: its own empty work folder (standing-app), never the
                                 background check's (standing-run), so the two never share one
    run_setup(engine, words, config, usage, *, now, time_zone_name, labels) -> SetupOutcome
        one setup run (KIND_SETUP: the owner's sentence alone, no mail), counted like any standing run, and Jarvis's
        own check of what it made (standing.setup.definition): a draft agent or rule, "neither", or why not
    run_dry(engine, rule, reader, config, usage, *, now, time_zone_name, own) -> DryOutcome
        the rule's dry run over the last 30 days: an account not signed in says so first (sign_in_first, nothing
        asked of Gmail), then Gmail's own count (headers, free; a filter that would read nearly everything stops
        there, before any Claude run), then the newest matches read (one message each, scrubbed) and judged in one
        run, counted like any other. Nothing is proposed, starred, labelled or archived.
    turn_on_problem(job, config, accounts, consented, *, edit=True) -> str
        why Turn on can't happen yet ("" when it can): consent, then each account the job reads signed in (a rule's
        dry run reads that mail too), then a rule's dry run; ``edit``: the setup card's words (a row has no Edit)
    signed_out(job, accounts) -> tuple     the accounts the job reads that are not signed in to Jarvis: each gets
                                 its Sign in <alias> button on the job's row and setup card
    sign_in_first(aliases, *, edit=True) -> str  what to do while those accounts are not signed in to Jarvis
    bindings_of(job, accounts) -> tuple    the (alias, Google account id) pairs Turn on records
    job_lines(job, config, hour24) / dry_lines(rule, outcome)    what a setup card says (plain text)
    CONSENT_*                    the one-time consent card's words

Nothing of a sentence, a filter, a mail or an answer is logged: kinds, counts and outcomes only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .google_auth import PROBLEM_EXPIRED, PROBLEM_SIGNED_OUT
from .standing import dryrun, rules, setup
from .standing.engine import KIND_RULE, KIND_SETUP, StandingEngine
from .standing.model import (ACTION_EVENT, ACTION_REPLY, ACTION_TELL, ACTION_TODO, PROPOSE_CALENDAR,
                             PROPOSE_REPLY, PROPOSE_TODO, REPORT_EVERY, STATUS_DRAFT, STATUS_TRIED, TIDY_ARCHIVE,
                             TIDY_LABEL, TIDY_NONE, TIDY_STAR, Agent, Rule, with_status)
from .standing.store import StandingFiles

logger = logging.getLogger(__name__)

APP_WORK_FOLDER = "standing-app"      # the app's setup and dry runs (the background check's is standing-run)
SETUP_JOB = "setup"                   # how setup runs are counted in standing_usage.json
_SIGNED_OUT = (PROBLEM_SIGNED_OUT, PROBLEM_EXPIRED)   # a reader's problems a sign-in is what helps
SETUP_RUNS_PER_DAY = 20
RULE_RUNS_PER_DAY = 48                # as standing.runner's backstop
TIDY_FEATURE = "gmail_tidy"
DOT = "·"

CONSENT_KICKER = "Before any agent runs"
CONSENT_TITLE = "Agents and rules read the mail they watch, on their own"
CONSENT_LINES = (
    "When a job runs, the text of each mail its filter matches goes to your Claude plan, without a click at that "
    "moment. Only the matched message is read, never its whole conversation, and passwords and codes are taken out "
    "first.",
    "Jarvis reads the mail himself; the run has no tools and no web, so a mail can't change what is read.",
    "Cards (to-dos, events, reply drafts) wait for your click as always. A rule may star, label or archive the mail it "
    "matched only if you set it up to, each with an Undo.",
    "At most {runs} runs a day for everything. Reports and receipts stay on this PC for {days} days; Clear deletes "
    "them.",
)


def consent_lines(settings: Any) -> tuple[str, ...]:
    """The consent card's words with this PC's caps ([standing] max_runs_per_day, keep_days)."""
    return tuple(line.format(runs=getattr(settings, "max_runs_per_day", 30), days=getattr(settings, "keep_days", 7))
                 for line in CONSENT_LINES)
CONSENT_YES = "Yes, let them run"
CONSENT_LATER = "Not now"
PRIVACY_LINE = ("When it runs, the text of the mail its filter matches goes to your Claude plan without a click; "
                "anything it lines up waits for yours.")


@dataclass(frozen=True)
class SetupOutcome:
    job: Agent | Rule | None = None
    say: str = ""                     # the setup run's short note (it saw only the owner's sentence)
    refusal: str = ""                 # why no job was made (Jarvis's own words, none of the sentence)
    neither: bool = False             # the sentence is not a standing job


@dataclass(frozen=True)
class DryOutcome:
    rule: Rule | None = None          # the rule with its dry run kept (status tried), when it ran
    result: dryrun.DryResult | None = None
    refusal: str = ""


class _RefusingEngine:
    """While the tests run (JARVIS_DESKTOP_TEST_GUARD=1) the app's engine starts nothing: every run is refused."""

    def run(self, *_args: Any, **_kwargs: Any) -> Any:
        from .ask.stream import failure
        from .standing.engine import StandingResult

        return StandingResult(failure=failure("missing", "the tests run"), started=False)


def app_engine(config: Any) -> Any:
    import os

    if os.environ.get("JARVIS_DESKTOP_TEST_GUARD") == "1":
        return _RefusingEngine()
    files = StandingFiles(config.data_dir)
    engine = StandingEngine(config.ask, files)
    engine.claude.work_folder = files.data_dir / APP_WORK_FOLDER
    return engine


def accounts_caps(config: Any) -> dict[str, list[str]]:
    """alias -> what it can do for a standing job: "read" (gmail_read) and "calendar"."""
    caps: dict[str, list[str]] = {}
    for alias, account in sorted((getattr(config, "accounts", None) or {}).items()):
        features = tuple(getattr(account, "features", ()) or ())
        caps[alias] = [name for name, feature in (("read", "gmail_read"), ("calendar", "calendar"))
                       if feature in features]
    return caps


def readable(config: Any) -> list[str]:
    return [alias for alias, can in accounts_caps(config).items() if "read" in can]


def can_tidy(config: Any, alias: str) -> bool:
    account = (getattr(config, "accounts", None) or {}).get(alias)
    return TIDY_FEATURE in tuple(getattr(account, "features", ()) or ())


def _failure_words(kind: str) -> str:
    return {"signed_out": "Claude Code isn't signed in: sign it in to your claude.ai plan, then try again.",
            "missing": "Jarvis can't find Claude Code on this PC.",
            "limit": "Your Claude plan's limit is reached for now; try again after it resets.",
            "timeout": "That took too long; try again.",
            "cancelled": "Stopped."}.get(kind, "Jarvis couldn't get an answer just now; try again.")


def run_setup(engine: Any, words: str, config: Any, usage: Any, *, now: datetime, time_zone_name: str,
              labels: Callable[[str], Sequence[tuple[str, str]]] | None = None,
              accounts: Mapping[str, Any] | None = None) -> SetupOutcome:
    """One setup run on the owner's own sentence (see the module docstring). Runs on a worker thread."""
    try:
        words = setup.check_words(words)
    except setup.SetupRefused as refused:
        return SetupOutcome(refusal=str(refused))
    settings = config.standing
    if not usage.try_run(SETUP_JOB, total_cap=settings.max_runs_per_day, job_cap=SETUP_RUNS_PER_DAY):
        return SetupOutcome(refusal=f"Today's {settings.max_runs_per_day} runs for agents and rules are used up; "
                                    "set it up tomorrow.")
    prompt = setup.setup_prompt(words, accounts_caps(config), now=now, time_zone_name=time_zone_name)
    result = engine.run(prompt, kind=KIND_SETUP, model=settings.model, timeout_s=settings.timeout_seconds,
                        read=setup.read_setup)
    if result.failure is not None:
        logger.info("Standing: a setup run failed (%s)", result.failure.kind)
        return SetupOutcome(refusal=_failure_words(result.failure.kind))
    answer = result.answer
    say = " ".join(str(answer.get("say") or "").split())[:300]
    try:
        job = setup.definition(answer, words, aliases=sorted(accounts_caps(config)), readable=readable(config),
                               labels=labels, min_every=settings.min_every_minutes, now=now)
    except setup.SetupRefused as refused:
        logger.info("Standing: a setup answer was refused")
        return SetupOutcome(say=say, refusal=str(refused))
    if job is None:
        return SetupOutcome(say=say, neither=True)
    # A new job's own cap is this PC's ([standing] agent_runs_per_day / rule_checks_per_day), never the model's.
    job = (replace(job, runs_per_day=settings.agent_runs_per_day) if isinstance(job, Agent)
           else replace(job, checks_per_day=settings.rule_checks_per_day))
    if accounts:
        preferred = prefer_signed_in(job, words, accounts, readable(config))
        if isinstance(preferred, str):
            logger.info("Standing: a setup named no account Jarvis can read now")
            return SetupOutcome(say=say, refusal=preferred)
        job = preferred
    logger.info("Standing: set up a draft %s %s", job.kind, job.id)
    return SetupOutcome(job=job, say=say)


def run_dry(engine: Any, rule: Rule, reader: Any, config: Any, usage: Any, *, now: datetime, time_zone_name: str,
            own: Collection[str] = ()) -> DryOutcome:
    """A rule's dry run (see the module docstring). Runs on a worker thread."""
    settings = config.standing
    if reader is None:
        return DryOutcome(refusal=f"Jarvis can't read the {rule.account} account's mail here: sign it in first.")
    try:
        problem, why = reader.available() if callable(getattr(reader, "available", None)) else ("", "")
    except Exception:  # noqa: BLE001 - unknown: the count below says what it finds
        problem, why = "", ""
    if problem in _SIGNED_OUT:                       # "try again" would only fail again: say what to do
        return DryOutcome(refusal=sign_in_first(rule.account))
    if problem:
        return DryOutcome(refusal=f"{str(why).rstrip('.')}." if why else
                          f"Jarvis can't read the {rule.account} account's mail here.")
    try:
        preview = dryrun.estimate(rule, reader, now=now, batch=settings.rule_batch)
    except Exception as exc:  # noqa: BLE001 - signed out or offline: nothing ran
        logger.info("Standing: a dry run's count failed (%s)", type(exc).__name__)
        return DryOutcome(refusal=f"Jarvis couldn't read the {rule.account} account's mail just now; try again.")
    if preview.refusal:
        return DryOutcome(refusal=preview.refusal)
    if not preview.refs:
        result = dryrun.DryResult(preview.matched, narrow=preview.matched <= dryrun.NARROW_FOR_TIDY)
        return DryOutcome(_tried(rule, result, now), result)
    if not usage.try_run(rule.id, total_cap=settings.max_runs_per_day, job_cap=RULE_RUNS_PER_DAY):
        return DryOutcome(refusal=f"Today's {settings.max_runs_per_day} runs for agents and rules are used up; try "
                                  "the dry run tomorrow.")
    failures: list[str] = []

    def check(rule_: Rule, items: Sequence[rules.Item]) -> Any:
        prompt = rules.rule_prompt(rule_, items, now=now, time_zone_name=time_zone_name)
        outcome = engine.run(prompt, kind=KIND_RULE, model=settings.rule_model, timeout_s=settings.timeout_seconds,
                             read=rules.read_rule)
        if outcome.failure is not None:
            failures.append(outcome.failure.kind)
            return outcome.failure
        usage.add("checks", rule_.id, len(items))
        return outcome.answer

    try:
        result = dryrun.judge(rule, preview, reader, check, now=now, chars=settings.mail_chars, own=own)
    except Exception as exc:  # noqa: BLE001 - a mail could not be read: nothing was kept
        logger.info("Standing: a dry run stopped (%s)", type(exc).__name__)
        return DryOutcome(refusal="Jarvis couldn't finish the dry run; try again.")
    if result.failure:
        return DryOutcome(refusal=_failure_words(failures[0] if failures else result.failure))
    logger.info("Standing: rule %s dry run: %d matched, %d judged, %d yes", rule.id, result.matched,
                len(result.rows), result.yes)
    return DryOutcome(_tried(rule, result, now), result)


def _tried(rule: Rule, result: dryrun.DryResult, now: datetime) -> Rule:
    tried = replace(rule, dry_run=dryrun.summary(result, now))
    return with_status(tried, STATUS_TRIED) if rule.status == STATUS_DRAFT else tried


def bindings_of(job: Agent | Rule, accounts: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs = []
    for alias in job.accounts:
        account = accounts.get(alias)
        binding = account.binding() if account is not None and callable(getattr(account, "binding", None)) else None
        sub = getattr(binding, "sub", "") if binding is not None else ""
        if sub:
            pairs.append((alias, sub))
    return tuple(pairs)


DRY_RUN_FIRST = "Try it first: Dry run shows what it would have done with your recent mail."


def signed_in(account: Any) -> bool:
    """The app's Google account for an alias has a usable sign-in (files only; unknown: not signed in)."""
    try:
        return account is not None and bool(account.is_signed_in())
    except Exception:  # noqa: BLE001 - unknown: not signed in
        return False


def named(alias: str, words: str) -> bool:
    """The owner's own words name this account ("my work mail", "personal")."""
    return re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", words, re.IGNORECASE) is not None


def prefer_signed_in(job: Agent | Rule, words: str, accounts: Mapping[str, Any],
                     can_read: Sequence[str]) -> Agent | Rule | str:
    """Setup never picks an account the owner's words did not name and that is not signed in to Jarvis: an agent keeps
    only its mail on signed-in (or named) accounts; a rule moves to the one signed-in account that reads mail. A named
    account that is not signed in stays (its Sign in button is on the draft). A str: why nothing is left."""
    def keep(alias: str) -> bool:
        return named(alias, words) or signed_in(accounts.get(alias))

    if isinstance(job, Agent):
        mail = tuple(source for source in job.mail if keep(source.account))
        if mail == job.mail:
            return job
        if not mail and (job.trigger.kind == "mail" or not (job.calendar_days or job.notes)):
            return ("None of the accounts it would read is signed in to Jarvis. Say which account in your sentence "
                    "(for example: my personal mail), or sign one in first.")
        return replace(job, mail=mail)
    if keep(job.account):
        return job
    ready = [alias for alias in can_read if signed_in(accounts.get(alias))]
    if len(ready) == 1:
        return replace(job, account=ready[0])
    return "Say which account the rule watches (for example: my personal mail)."


def signed_out(job: Agent | Rule, accounts: Mapping[str, Any]) -> tuple[str, ...]:
    """The accounts ``job`` reads that are not signed in to Jarvis, in its order (each one's Sign in button)."""
    return tuple(alias for alias in job.accounts if not signed_in(accounts.get(alias)))


def turn_on_problem(job: Agent | Rule, config: Any, accounts: Mapping[str, Any], consented: bool, *,
                    edit: bool = True) -> str:
    if not consented:
        return "First say yes on the card above: it explains what agents read."
    missing = signed_out(job, accounts)
    if missing:                                   # before a rule's dry run, which reads that account's mail too
        return sign_in_first(missing, edit=edit)
    if isinstance(job, Rule) and job.dry_run is None:
        return DRY_RUN_FIRST
    return ""     # an older sign-in that names no Google account is let through: Turn on records what it can


def _joined(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def sign_in_first(aliases: str | Sequence[str], *, edit: bool = True) -> str:
    """What to do while ``aliases`` (one alias, or several) are not signed in to Jarvis: the Sign in <alias> button
    beside these words (Turn on's and a dry run's words). ``edit``: the setup card's, whose Edit can name another
    account; a row has no Edit."""
    names = [aliases] if isinstance(aliases, str) else list(aliases)
    buttons = _joined([f"Sign in {alias}" for alias in names])
    which = f"the {names[0]} account" if len(names) == 1 else f"the {_joined(names)} accounts"
    return f"Sign {which} in first: click {buttons}." + (" Or Edit and name another account." if edit else "")


def _trigger(job: Agent, hour24: bool) -> str:
    from .standing_ui import trigger_text

    return trigger_text(job, hour24)


def job_lines(job: Agent | Rule, config: Any, hour24: bool = False) -> tuple[str, ...]:
    """What a setup card says a draft does (plain text; the filters and words as the definition holds them)."""
    if isinstance(job, Agent):
        lines = [f"When: {_trigger(job, hour24)}"]
        reads = [f"{source.account} mail matching {source.query}" for source in job.mail]
        if job.calendar_days:
            reads.append("your calendar" if job.calendar_days == 1 else f"your calendar, {job.calendar_days} days")
        if job.notes:
            reads.append("your notes")
        lines.append("Reads: " + "; ".join(reads))
        if job.mail:
            lines.append("New mail only: it reads mail that comes after you turn it on, each mail once")
        kinds = {PROPOSE_TODO: "to-dos", PROPOSE_CALENDAR: "events", PROPOSE_REPLY: "reply drafts"}
        offers = [kinds[kind] for kind in job.proposes if kind in kinds]
        if offers:
            lines.append(f"May line up: {', '.join(offers)} (each waits for your click)")
        lines.append("Reports: every run" if job.report == REPORT_EVERY else
                     "Reports: only when something needs you")
        lines.append(f"At most {job.runs_per_day} runs a day")
        return tuple(lines)
    lines = [f"Watches: new {job.account} mail matching {job.query}", f"Asks: {job.question}"]
    if job.fields:
        lines.append("Finds: " + ", ".join(f"{item.name} ({item.type})" for item in job.fields))
    action = job.action
    then = {ACTION_TODO: f"a to-do: {action.title}", ACTION_EVENT: f"an event: {action.title}",
            ACTION_REPLY: "a reply draft to the sender", ACTION_TELL: "a line in its report"}.get(action.kind, "")
    lines.append(f"Then: {then}" + ("" if action.kind == ACTION_TELL else " (it waits for your click)"))
    if job.tidy.kind != TIDY_NONE:
        what = {TIDY_STAR: "stars", TIDY_ARCHIVE: "archives",
                TIDY_LABEL: f"labels as {job.tidy.label_name or 'your label'}"}.get(job.tidy.kind, "")
        tidy = f"Tidy-up: {what} the mail it matched, without a click, with an Undo"
        if not can_tidy(config, job.account):
            tidy += (f" - waits until the {job.account} account has the gmail_tidy feature in config.toml and you "
                     "sign in for it")
        lines.append(tidy)
    lines.append(f"Checks at most {job.checks_per_day} mails a day")
    return tuple(lines)


def dry_lines(rule: Rule, outcome: DryOutcome, limit: int = 8) -> tuple[str, ...]:
    """A dry run's rows on its card: the count, then what a live rule would have done with each judged mail."""
    result = outcome.result
    if result is None:
        return ()
    lines = [f"Dry run: about {result.matched} matching mails in {dryrun.WINDOW_DAYS} days; judged "
             f"{len(result.rows)}, {result.yes} yes."]
    if rule.tidy.kind != TIDY_NONE and not result.narrow:
        lines.append(f"Its filter matches more than {dryrun.NARROW_FOR_TIDY} in {dryrun.WINDOW_DAYS} days: it would "
                     "make cards only, never a tidy-up.")
    for row in result.rows[:limit]:
        ref = row.ref
        sender = getattr(ref.sender, "name", "") or getattr(ref.sender, "email", "") if ref.sender else ""
        lines.append(f"{sender} {DOT} {ref.subject}: {dryrun.would(rule, row)}" if sender else
                     f"{ref.subject}: {dryrun.would(rule, row)}")
    if result.unread:
        lines.append(f"{result.unread} could not be read again.")
    return tuple(lines)


@dataclass
class SetupDraft:
    """What the setup card shows (memory only): the owner's words and the kind they typed, the job made of them (None
    while the setup runs or when it made none), the run's note, the latest dry run, and a line of Jarvis's own."""

    words: str
    kind: str = ""                    # "agent" | "rule": what the owner typed ("" from the planner's hand-off)
    job: Agent | Rule | None = None
    say: str = ""
    dry: DryOutcome | None = None
    note: str = ""
    busy: str = ""                    # "setup" while its setup runs, "dry" while its dry run runs
