"""Standing agents and rules as data (no Qt, no network): what the owner set up, checked every time it is read.

    Trigger                 when a job wakes: TRIGGER_TIME (times on chosen weekdays; none = every day),
                            TRIGGER_EVERY (every N minutes), TRIGGER_MAIL (new mail matching its filter) or
                            TRIGGER_MANUAL (Run now only)
    MailSource              one account and its Gmail filter (a query checked by standing.filters before use)
    Agent                   a job set up from a chat: the owner's own words (the run's <command>, never a
                            restatement), its trigger, its sources (mail filters, calendar days, notes), the card
                            kinds it may propose, when it reports, its own daily cap and its status
    Field / Action / Tidy   a rule's typed values, what a "yes" makes (a to-do, an event, a reply draft or a
                            line in its report) and what it may do to the matched mail at Google (star, one
                            label, archive: the owner's choice of 2026-11-12)
    DryRun                  what a rule's dry run found (counts only)
    Rule                    one sentence compiled once: account, filter, one yes/no question, typed values,
                            action, tidy-up, status and dry run
    agent_from(data) / rule_from(data)   a stored or compiled definition, checked (DefinitionError: why not)
    to_data(job)            JSON-ready data (agent_from / rule_from read it back)
    new_id(kind)            "a-<12 hex>" or "r-<12 hex>"
    render(template, values)    a rule's title with its typed values (each value already checked)

A definition's texts are the owner's (names, words, a sentence) or a rule's compiled parts; each is one line of
plain text with a length limit. A DefinitionError's message names the part, never its text, so it can be logged.
Nothing here reads a file or the network; the reprs name ids, kinds and statuses only.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..format_chars import has_format

SCHEMA_VERSION = 1

KIND_AGENT, KIND_RULE = "agent", "rule"
KINDS = (KIND_AGENT, KIND_RULE)

TRIGGER_TIME, TRIGGER_EVERY, TRIGGER_MAIL, TRIGGER_MANUAL = "time", "every", "mail", "manual"
TRIGGERS = (TRIGGER_TIME, TRIGGER_EVERY, TRIGGER_MAIL, TRIGGER_MANUAL)

REPORT_EVERY, REPORT_NEEDED = "every_run", "when_needed"
REPORTS = (REPORT_EVERY, REPORT_NEEDED)

# draft: set up, not on; tried: a rule's dry run is done (only then may it go live); live; paused.
STATUS_DRAFT, STATUS_TRIED, STATUS_LIVE, STATUS_PAUSED = "draft", "tried", "live", "paused"
STATUSES = (STATUS_DRAFT, STATUS_TRIED, STATUS_LIVE, STATUS_PAUSED)

# What an agent may propose (card kinds: actions.TODO, CALENDAR, REPLY).
PROPOSE_TODO, PROPOSE_CALENDAR, PROPOSE_REPLY = "todo", "calendar", "reply"
PROPOSALS = (PROPOSE_TODO, PROPOSE_CALENDAR, PROPOSE_REPLY)

# What a rule's "yes" makes.
ACTION_TODO, ACTION_EVENT, ACTION_REPLY, ACTION_TELL = "todo", "event", "reply", "tell"
ACTIONS = (ACTION_TODO, ACTION_EVENT, ACTION_REPLY, ACTION_TELL)

# What a rule may do at Google to the matched mail without a click (owner answer 1, 2026-11-12).
TIDY_NONE, TIDY_STAR, TIDY_LABEL, TIDY_ARCHIVE = "", "star", "label", "archive"
TIDIES = (TIDY_NONE, TIDY_STAR, TIDY_LABEL, TIDY_ARCHIVE)

TYPE_TEXT, TYPE_DATE, TYPE_DATETIME, TYPE_MONEY, TYPE_NUMBER = "text", "date", "datetime", "money", "number"
FIELD_TYPES = (TYPE_TEXT, TYPE_DATE, TYPE_DATETIME, TYPE_MONEY, TYPE_NUMBER)

MAX_NAME = 40
MAX_WORDS = 600            # an agent's words (the owner's own)
MAX_SENTENCE = 400         # a rule's sentence (the owner's own)
MAX_QUESTION = 200
MAX_TEMPLATE = 120
MAX_HINT = 80
MAX_FIELDS = 5
MAX_TIMES = 4
MAX_MAIL_SOURCES = 4
MAX_QUERY = 200            # as ask.mail.MAX_QUERY_CHARS
MAX_CALENDAR_DAYS = 14
MIN_EVERY_MINUTES = 30     # the owner's shortest interval (answer 3); [standing] min_every_minutes may raise it
MAX_EVERY_MINUTES = 7 * 24 * 60
MAX_RUNS_PER_DAY = 48
EVENT_MINUTES = (15, 480)
MAX_STAMP = 40

_ID_RES = {KIND_AGENT: re.compile(r"a-[0-9a-f]{12}"), KIND_RULE: re.compile(r"r-[0-9a-f]{12}")}
_ALIAS_RE = re.compile(r"[a-z][a-z0-9_-]{0,23}")
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_FIELD_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,19}")
_LABEL_ID_RE = re.compile(r"Label_[0-9A-Za-z_-]{1,64}")      # a Gmail user label's id; never a system label
_BINDING_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")             # the Google account id (sub) recorded at Turn on
_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?")
_REF_RE = re.compile(r"\{([^{}]*)\}")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class DefinitionError(ValueError):
    """Why a definition can't be used. The message names the part, never its text (safe to log)."""


def new_id(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    return f"{kind[0]}-{secrets.token_hex(6)}"


def _line(value: Any, what: str, limit: int, *, empty: bool = False) -> str:
    """One line of plain text: spaces collapsed; no control or invisible characters; at most ``limit``."""
    if not isinstance(value, str):
        raise DefinitionError(f"{what} is not text")
    text = " ".join(value.split())
    if not text and not empty:
        raise DefinitionError(f"{what} is empty")
    if len(text) > limit:
        raise DefinitionError(f"{what} is longer than {limit} characters")
    if any((ord(char) < 32 and char not in "\t\n\r") or 127 <= ord(char) <= 159 for char in value):
        raise DefinitionError(f"{what} has control characters")
    if has_format(text):
        raise DefinitionError(f"{what} has invisible characters")
    return text


def _choice(value: Any, what: str, choices: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise DefinitionError(f"{what} is not one of {', '.join(repr(choice) for choice in choices)}")
    return value


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DefinitionError(f"{what} is not a whole number")
    if not low <= value <= high:
        raise DefinitionError(f"{what} is outside {low}..{high}")
    return value


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise DefinitionError(f"{what} is not true or false")
    return value


def _stamp(value: Any, what: str, *, empty: bool = True) -> str:
    if value in (None, "") and empty:
        return ""
    if not isinstance(value, str) or len(value) > MAX_STAMP or not _STAMP_RE.fullmatch(value):
        raise DefinitionError(f"{what} is not a time stamp")
    return value


def _alias(value: Any, what: str = "the account") -> str:
    if not isinstance(value, str) or not _ALIAS_RE.fullmatch(value):
        raise DefinitionError(f"{what} is not an account name")
    return value


def _items(value: Any, what: str, limit: int) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise DefinitionError(f"{what} is not a list")
    if len(value) > limit:
        raise DefinitionError(f"{what} has more than {limit} entries")
    return list(value)


def _mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DefinitionError(f"{what} is not a table")
    return value


def _binding(value: Any) -> tuple[tuple[str, str], ...]:
    """The Google accounts' ids recorded at Turn on: (alias, sub) pairs (the run refuses a changed one)."""
    pairs: list[tuple[str, str]] = []
    for item in _items(value, "the account bindings", 8):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise DefinitionError("an account binding is not a pair")
        alias = _alias(item[0], "a binding's account")
        sub = item[1]
        if not isinstance(sub, str) or not _BINDING_RE.fullmatch(sub):
            raise DefinitionError("a binding's account id is not usable")
        if alias not in {known for known, _sub in pairs}:
            pairs.append((alias, sub))
    return tuple(pairs)


# --------------------------------------------------------------------------
# Triggers and sources
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Trigger:
    kind: str
    times: tuple[str, ...] = ()        # TRIGGER_TIME: "HH:MM", at most MAX_TIMES
    weekdays: tuple[int, ...] = ()     # TRIGGER_TIME: 0 = Monday .. 6 = Sunday; () = every day
    every_minutes: int = 0             # TRIGGER_EVERY: MIN_EVERY_MINUTES..MAX_EVERY_MINUTES

    def __repr__(self) -> str:
        return f"Trigger({self.kind})"


def trigger_from(data: Any, *, min_every: int = MIN_EVERY_MINUTES) -> Trigger:
    data = _mapping(data, "the trigger")
    kind = _choice(data.get("kind"), "the trigger", TRIGGERS)
    if kind == TRIGGER_TIME:
        times = []
        for item in _items(data.get("times"), "the times", MAX_TIMES):
            if not isinstance(item, str) or not _TIME_RE.fullmatch(item):
                raise DefinitionError("a time is not a 24-hour HH:MM time")
            if item not in times:
                times.append(item)
        if not times:
            raise DefinitionError("a timed trigger names no time")
        days: list[int] = []
        for item in _items(data.get("weekdays"), "the weekdays", 7):
            if isinstance(item, str) and item.casefold()[:3] in WEEKDAYS:
                item = WEEKDAYS.index(item.casefold()[:3])
            if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 6:
                raise DefinitionError("a weekday is not Monday to Sunday")
            if item not in days:
                days.append(item)
        if len(days) == 7:
            days = []
        return Trigger(kind, times=tuple(sorted(times)), weekdays=tuple(sorted(days)))
    if kind == TRIGGER_EVERY:
        minutes = _integer(data.get("every_minutes"), "the interval", max(MIN_EVERY_MINUTES, int(min_every)),
                           MAX_EVERY_MINUTES)
        return Trigger(kind, every_minutes=minutes)
    return Trigger(kind)


def trigger_data(trigger: Trigger) -> dict[str, Any]:
    data: dict[str, Any] = {"kind": trigger.kind}
    if trigger.kind == TRIGGER_TIME:
        data["times"] = list(trigger.times)
        data["weekdays"] = list(trigger.weekdays)
    elif trigger.kind == TRIGGER_EVERY:
        data["every_minutes"] = trigger.every_minutes
    return data


@dataclass(frozen=True)
class MailSource:
    account: str
    query: str = ""        # the Gmail filter (standing.filters checks it, grounded in the owner's words)

    def __repr__(self) -> str:
        return f"MailSource({self.account})"


def _mail_source(data: Any) -> MailSource:
    data = _mapping(data, "a mail source")
    return MailSource(_alias(data.get("account")), _line(data.get("query"), "a mail filter", MAX_QUERY))


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    words: str                                   # the owner's own words: the run's <command>
    trigger: Trigger
    mail: tuple[MailSource, ...] = ()
    calendar_days: int = 0                       # 0: no calendar; else today and the next N-1 days
    notes: bool = False                          # the owner's memory notes
    proposes: tuple[str, ...] = (PROPOSE_TODO,)  # PROPOSALS it may propose
    report: str = REPORT_NEEDED
    runs_per_day: int = 6
    status: str = STATUS_DRAFT
    created: str = ""
    bindings: tuple[tuple[str, str], ...] = ()   # (alias, Google account id) recorded at Turn on
    # Build plan item 5: where its reports land - ("doc" | "sheet", the item's id; "" until Turn on makes a new one) -
    # its name, and a new sheet's columns ((name, type, choices), at most 8) for Turn on to make it with.
    lands: tuple[str, str] = ()
    lands_name: str = ""
    lands_columns: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    lands_keys: int = 1                          # a new sheet's key columns: its first 1..3 (build plan item 6)

    kind = KIND_AGENT

    def __repr__(self) -> str:
        return f"Agent({self.id}, {self.trigger.kind}, {self.status})"

    @property
    def accounts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(source.account for source in self.mail))


def agent_from(data: Any, *, min_every: int = MIN_EVERY_MINUTES) -> Agent:
    data = _mapping(data, "the agent")
    agent_id = data.get("id")
    if not isinstance(agent_id, str) or not _ID_RES[KIND_AGENT].fullmatch(agent_id):
        raise DefinitionError("the agent's id is not usable")
    trigger = trigger_from(data.get("trigger"), min_every=min_every)
    mail = tuple(_mail_source(item) for item in _items(data.get("mail"), "the mail sources", MAX_MAIL_SOURCES))
    if trigger.kind == TRIGGER_MAIL and not mail:
        raise DefinitionError("a new-mail trigger needs a mail filter")
    proposes = []
    for item in _items(data.get("proposes"), "what it may propose", len(PROPOSALS)):
        item = _choice(item, "a proposal kind", PROPOSALS)
        if item not in proposes:
            proposes.append(item)
    calendar_days = _integer(data.get("calendar_days", 0), "the calendar days", 0, MAX_CALENDAR_DAYS)
    notes = _flag(data.get("notes", False), "the notes")
    if not mail and not calendar_days and not notes:
        raise DefinitionError("the agent reads nothing")
    return Agent(id=agent_id, name=_line(data.get("name"), "the name", MAX_NAME),
                 words=_line(data.get("words"), "the words", MAX_WORDS), trigger=trigger, mail=mail,
                 calendar_days=calendar_days, notes=notes,
                 proposes=tuple(sorted(proposes, key=PROPOSALS.index)),
                 report=_choice(data.get("report", REPORT_NEEDED), "when it reports", REPORTS),
                 runs_per_day=_integer(data.get("runs_per_day", 6), "the runs a day", 1, MAX_RUNS_PER_DAY),
                 status=_choice(data.get("status", STATUS_DRAFT), "the status",
                                (STATUS_DRAFT, STATUS_LIVE, STATUS_PAUSED)),
                 created=_stamp(data.get("created"), "the creation time"),
                 bindings=_binding(data.get("bindings")), **_lands(data.get("lands")))


MAX_LANDS_COLUMNS = 8


def _lands(value: Any) -> dict[str, Any]:
    """An agent's "lands" (build plan item 5): {} for none (an older file has none)."""
    from ..docs import model as docs_model

    if value is None or value == {}:
        return {}
    value = _mapping(value, "where it writes")
    kind = _choice(value.get("kind"), "where it writes", (docs_model.KIND_DOC, docs_model.KIND_SHEET))
    item_id = value.get("id") or ""
    if item_id and docs_model.kind_of(item_id) != kind:
        raise DefinitionError("where it writes is not usable")
    name = docs_model.clean_name(value.get("name") or "")
    columns: list[tuple[str, str, tuple[str, ...]]] = []
    taken: list[str] = []
    for raw in _items(value.get("columns"), "the columns it fills", MAX_LANDS_COLUMNS):
        raw = _mapping(raw, "a column it fills")
        column, reason = docs_model.check_column(raw.get("name"), raw.get("type"), raw.get("choices") or (),
                                                 taken=tuple(taken))
        if column is None:
            raise DefinitionError(f"a column it fills is not usable ({reason})")
        taken.append(column.name)
        columns.append((column.name, column.type, column.choices))
    if kind == docs_model.KIND_DOC and columns:
        raise DefinitionError("a document has no columns")
    if not item_id and not name:
        raise DefinitionError("where it writes has no name")
    if not item_id and kind == docs_model.KIND_SHEET and not columns:
        raise DefinitionError("a new sheet needs its columns")
    # A new sheet's key columns (build plan item 6): its first 1..3 columns; an older file has none (1).
    keys = _integer(value.get("keys", 1), "the key columns", 1, docs_model.MAX_KEY_COLUMNS)
    if keys > 1 and (kind == docs_model.KIND_DOC or (columns and keys > len(columns))):
        raise DefinitionError("the key columns are more than its columns")
    return {"lands": (kind, item_id), "lands_name": name, "lands_columns": tuple(columns), "lands_keys": keys}


def lands_data(agent: Agent) -> dict[str, Any] | None:
    if not agent.lands:
        return None
    kind, item_id = agent.lands
    data: dict[str, Any] = {"kind": kind, "id": item_id, "name": agent.lands_name,
                            "columns": [{"name": name, "type": type_, "choices": list(choices)}
                                        for name, type_, choices in agent.lands_columns]}
    if agent.lands_keys != 1:                    # written only when not 1: an older file reads the same
        data["keys"] = agent.lands_keys
    return data


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    name: str          # [a-z][a-z0-9_]{0,19}: what a template names
    type: str          # FIELD_TYPES
    hint: str = ""     # what to find ("the amount due"): the rule's own words

    def __repr__(self) -> str:
        return f"Field({self.name}, {self.type})"


@dataclass(frozen=True)
class Action:
    kind: str                      # ACTIONS
    title: str = ""                # a to-do's or event's title: a template of {field} names and plain words
    due: str = ""                  # ACTION_TODO: the date / datetime field it is due by ("" none)
    start: str = ""                # ACTION_EVENT: the datetime field it starts at
    minutes: int = 60              # ACTION_EVENT: how long
    reply_hint: str = ""           # ACTION_REPLY: what the reply should say (the rule's own words)

    def __repr__(self) -> str:
        return f"Action({self.kind})"


@dataclass(frozen=True)
class Tidy:
    kind: str = TIDY_NONE          # TIDIES
    label_id: str = ""             # TIDY_LABEL: the user label picked at Turn on (Label_...)
    label_name: str = ""           # ... its name as Gmail showed it then (display only)

    def __repr__(self) -> str:
        return f"Tidy({self.kind or 'none'})"


@dataclass(frozen=True)
class DryRun:
    at: str = ""
    matched: int = 0               # mails the filter matched in the window (Gmail's estimate)
    window_days: int = 30
    judged: int = 0                # of them, how many the dry run judged
    yes: int = 0                   # ... and answered yes

    def __repr__(self) -> str:
        return f"DryRun({self.judged} judged, {self.yes} yes)"

    @property
    def yes_rate(self) -> float:
        return self.yes / self.judged if self.judged else 0.0


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    sentence: str                  # the owner's own sentence
    account: str
    query: str                     # the hard filter (standing.filters checks it, grounded in the sentence)
    question: str                  # the one yes/no question
    fields: tuple[Field, ...] = ()
    action: Action = Action(ACTION_TELL)
    tidy: Tidy = Tidy()
    checks_per_day: int = 40
    status: str = STATUS_DRAFT
    dry_run: DryRun | None = None
    created: str = ""
    bindings: tuple[tuple[str, str], ...] = ()

    kind = KIND_RULE

    def __repr__(self) -> str:
        return f"Rule({self.id}, {self.action.kind}, {self.tidy.kind or 'no tidy'}, {self.status})"

    @property
    def accounts(self) -> tuple[str, ...]:
        return (self.account,)

    @property
    def trigger(self) -> Trigger:
        return Trigger(TRIGGER_MAIL)

    @property
    def mail(self) -> tuple[MailSource, ...]:
        return (MailSource(self.account, self.query),)

    def field(self, name: str) -> Field | None:
        return next((item for item in self.fields if item.name == name), None)


def template_refs(template: str) -> list[str]:
    """The {field} names a template uses, in order; DefinitionError for a brace that is not one."""
    refs = []
    for match in _REF_RE.finditer(template):
        name = match.group(1)
        if not _FIELD_NAME_RE.fullmatch(name):
            raise DefinitionError("a title names a value that is not a field name")
        refs.append(name)
    rest = _REF_RE.sub("", template)
    if "{" in rest or "}" in rest:
        raise DefinitionError("a title has a stray brace")
    return refs


def render(template: str, values: Mapping[str, str]) -> str:
    """``template`` with each {name} replaced by ``values[name]`` ("" when it has none), spaces collapsed. The values
    are checked before (standing.values); this only joins text."""
    text = _REF_RE.sub(lambda match: str(values.get(match.group(1), "") or ""), template)
    return " ".join(text.split())


def _field(data: Any) -> Field:
    data = _mapping(data, "a field")
    name = data.get("name")
    if not isinstance(name, str) or not _FIELD_NAME_RE.fullmatch(name):
        raise DefinitionError("a field's name is not usable")
    return Field(name, _choice(data.get("type"), "a field's type", FIELD_TYPES),
                 _line(data.get("hint", ""), "a field's hint", MAX_HINT, empty=True))


def _action(data: Any, fields: Sequence[Field]) -> Action:
    data = _mapping(data, "the action")
    kind = _choice(data.get("kind"), "the action", ACTIONS)
    types = {item.name: item.type for item in fields}
    title = _line(data.get("title", ""), "the title", MAX_TEMPLATE, empty=True)
    for ref in template_refs(title):
        if ref not in types:
            raise DefinitionError("the title names a value the rule does not find")
    due = data.get("due", "") or ""
    start = data.get("start", "") or ""
    if kind in (ACTION_TODO, ACTION_EVENT) and not title:
        raise DefinitionError("a to-do or an event needs a title")
    if kind == ACTION_TODO and due:
        if types.get(due) not in (TYPE_DATE, TYPE_DATETIME):
            raise DefinitionError("a to-do's due date is not a date the rule finds")
    elif due:
        raise DefinitionError("only a to-do has a due date")
    if kind == ACTION_EVENT:
        if types.get(start) != TYPE_DATETIME:
            raise DefinitionError("an event's start is not a date and time the rule finds")
    elif start:
        raise DefinitionError("only an event has a start")
    minutes = _integer(data.get("minutes", 60), "the event's length", *EVENT_MINUTES)
    hint = _line(data.get("reply_hint", ""), "what the reply says", MAX_QUESTION, empty=True)
    if kind != ACTION_REPLY and hint:
        raise DefinitionError("only a reply says what to write")
    return Action(kind, title=title, due=due, start=start, minutes=minutes, reply_hint=hint)


def _tidy(data: Any) -> Tidy:
    if data is None:
        return Tidy()
    data = _mapping(data, "the tidy-up")
    kind = _choice(data.get("kind", TIDY_NONE) or TIDY_NONE, "the tidy-up", TIDIES)
    label_id = data.get("label_id", "") or ""
    label_name = _line(data.get("label_name", ""), "the label's name", 100, empty=True)
    if kind == TIDY_LABEL:
        if not isinstance(label_id, str) or not _LABEL_ID_RE.fullmatch(label_id):
            raise DefinitionError("the label is not one of your own Gmail labels")
    elif label_id or label_name:
        raise DefinitionError("only a label tidy-up names a label")
    return Tidy(kind, label_id=label_id, label_name=label_name)


def _dry_run(data: Any) -> DryRun | None:
    if data is None:
        return None
    data = _mapping(data, "the dry run")
    judged = _integer(data.get("judged", 0), "the dry run's count", 0, 100)
    yes = _integer(data.get("yes", 0), "the dry run's yes count", 0, judged)
    return DryRun(at=_stamp(data.get("at"), "the dry run's time", empty=False),
                  matched=_integer(data.get("matched", 0), "the dry run's matches", 0, 1_000_000),
                  window_days=_integer(data.get("window_days", 30), "the dry run's window", 1, 90),
                  judged=judged, yes=yes)


def rule_from(data: Any) -> Rule:
    data = _mapping(data, "the rule")
    rule_id = data.get("id")
    if not isinstance(rule_id, str) or not _ID_RES[KIND_RULE].fullmatch(rule_id):
        raise DefinitionError("the rule's id is not usable")
    fields: list[Field] = []
    for item in _items(data.get("fields"), "the values", MAX_FIELDS):
        found = _field(item)
        if any(known.name == found.name for known in fields):
            raise DefinitionError("two values have the same name")
        fields.append(found)
    action = _action(data.get("action"), fields)
    tidy = _tidy(data.get("tidy"))
    dry_run = _dry_run(data.get("dry_run"))
    status = _choice(data.get("status", STATUS_DRAFT), "the status", STATUSES)
    if status in (STATUS_TRIED, STATUS_LIVE, STATUS_PAUSED) and dry_run is None:
        raise DefinitionError("a rule goes live only after its dry run")
    return Rule(id=rule_id, name=_line(data.get("name"), "the name", MAX_NAME),
                sentence=_line(data.get("sentence"), "the sentence", MAX_SENTENCE),
                account=_alias(data.get("account")),
                query=_line(data.get("query"), "the filter", MAX_QUERY),
                question=_line(data.get("question"), "the question", MAX_QUESTION),
                fields=tuple(fields), action=action, tidy=tidy,
                checks_per_day=_integer(data.get("checks_per_day", 40), "the checks a day", 1, 200),
                status=status, dry_run=dry_run, created=_stamp(data.get("created"), "the creation time"),
                bindings=_binding(data.get("bindings")))


# --------------------------------------------------------------------------
# Back to data
# --------------------------------------------------------------------------

def to_data(job: Agent | Rule) -> dict[str, Any]:
    if isinstance(job, Agent):
        return {"id": job.id, "name": job.name, "words": job.words, "trigger": trigger_data(job.trigger),
                "mail": [{"account": source.account, "query": source.query} for source in job.mail],
                "calendar_days": job.calendar_days, "notes": job.notes, "proposes": list(job.proposes),
                "report": job.report, "runs_per_day": job.runs_per_day, "status": job.status,
                "created": job.created, "bindings": [list(pair) for pair in job.bindings],
                **({"lands": lands_data(job)} if job.lands else {})}
    data: dict[str, Any] = {
        "id": job.id, "name": job.name, "sentence": job.sentence, "account": job.account, "query": job.query,
        "question": job.question,
        "fields": [{"name": item.name, "type": item.type, "hint": item.hint} for item in job.fields],
        "action": {"kind": job.action.kind, "title": job.action.title, "due": job.action.due,
                   "start": job.action.start, "minutes": job.action.minutes, "reply_hint": job.action.reply_hint},
        "tidy": {"kind": job.tidy.kind, "label_id": job.tidy.label_id, "label_name": job.tidy.label_name},
        "checks_per_day": job.checks_per_day, "status": job.status, "created": job.created,
        "bindings": [list(pair) for pair in job.bindings]}
    if job.dry_run is not None:
        run = job.dry_run
        data["dry_run"] = {"at": run.at, "matched": run.matched, "window_days": run.window_days,
                           "judged": run.judged, "yes": run.yes}
    return data


def with_status(job: Agent | Rule, status: str) -> Agent | Rule:
    """``job`` with another status (a rule may go live only once its dry run is done)."""
    if isinstance(job, Rule):
        if status not in STATUSES:
            raise DefinitionError("unknown status")
        if status in (STATUS_TRIED, STATUS_LIVE, STATUS_PAUSED) and job.dry_run is None:
            raise DefinitionError("a rule goes live only after its dry run")
    elif status not in (STATUS_DRAFT, STATUS_LIVE, STATUS_PAUSED):
        raise DefinitionError("unknown status")
    return replace(job, status=status)


def live(jobs: Iterable[Agent | Rule]) -> list[Agent | Rule]:
    return [job for job in jobs if job.status == STATUS_LIVE]
