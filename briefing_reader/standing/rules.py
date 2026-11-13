"""A rule's check (no Qt): up to rule_batch matched mails judged in one run, then what each "yes" makes.

    rule_prompt(rule, items, *, now, time_zone) -> str
        the run's stdin: <now>; <rule> (its question, its values and a reply's hint: the rule's own compiled parts,
        never a mail's words); <mail>, each mail an untrusted block of its own with a random closing mark (agent.tools
        .untrusted), named m1..mN (the model never sees a Gmail id), its sender, subject and date as Jarvis read
        them, then its text (quoted history stripped, secrets taken out); <command> (fixed words)
    read_rule(event, init) -> RuleAnswer | AskFailure      the answer, read strictly (one result per mail at most)
    outcomes(rule, items, answer, *, now, own) -> Checked
        per mail its yes or no, its checked values (standing.values: each must appear in that mail), and what Jarvis
        builds of a yes: a to-do or an event from the rule's title (every value through data()), a reply to that
        mail's sender only, or a "tell" line; the draft of a reply may not repeat another mail of the batch (5 words in
        a row). And whether a tidy-up may happen at once: never for a batch in which any mail reads like orders
        (guard.strong_order) or whose yes rate jumps well above the dry run's; never for a mail that failed Gmail's
        DMARC check, the owner's own, sent mail, a draft, or (archive) a starred or important one. Whether the rule
        itself may tidy (a filter naming its sender, a narrow dry run) is the runner's to check.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..actions import CALENDAR, REPLY, TODO, ProposedAction, parse_action_line
from ..agent.tools import untrusted
from ..ask.context import MAIL_NOTE, data
from ..ask.mail import MailMessage
from ..ask.stream import AskFailure, GARBLED, failure
from ..desktop import guard
from ..gmail import thread_link
from . import cards, values
from .engine import structured
from .mailwatch import MailRef
from .model import (ACTION_EVENT, ACTION_REPLY, ACTION_TELL, ACTION_TODO, TIDY_ARCHIVE, TIDY_NONE, TYPE_DATE,
                    TYPE_DATETIME, Rule, render)

COMMAND = ("Answer <rule>'s question for each email in <mail> on its own, m1 to the last, and give the values "
           "<rule> names for each yes, as that email states them.")
LEAK_WORDS = 5
JUMP_MIN_BATCH = 4                 # a batch this big or bigger is checked for a yes rate far above the dry run's
JUMP_OVER = 0.5                    # ... "far above": this much over it (and at least JUMP_FLOOR)
JUMP_FLOOR = 0.75
HELD_ORDERS, HELD_JUMP, HELD_DMARC, HELD_OWN, HELD_SENT, HELD_STARRED, HELD_NO = (
    "orders", "jump", "dmarc", "own", "sent", "starred", "no")


@dataclass(frozen=True)
class Item:
    """One mail of a check: its ref (headers) and its message (the text Jarvis read)."""
    ref: MailRef
    message: MailMessage

    def __repr__(self) -> str:
        return "Item()"


@dataclass(frozen=True)
class Result:
    yes: bool
    values: Mapping[str, str] = field(default_factory=dict)
    draft: str = ""


@dataclass(frozen=True)
class RuleAnswer:
    results: Mapping[int, Result]      # by the mail's number (1..N)

    def __repr__(self) -> str:
        return f"RuleAnswer({sum(1 for result in self.results.values() if result.yes)} yes)"


@dataclass(frozen=True)
class Outcome:
    ref: MailRef
    yes: bool
    values: Mapping[str, str] = field(default_factory=dict)
    card: ProposedAction | None = None      # what Jarvis built of a yes (None: a tell, a no, or nothing to build)
    line: str = ""
    key: str = ""                           # what the card's stable id comes from (the mail)
    tell: bool = False
    refused: str = ""                       # why a yes made no card (a code; never a value)
    tidy_held: str = ""                     # why a tidy-up may not happen at once ("" = it may)

    def __repr__(self) -> str:
        return f"Outcome(yes {self.yes}, card {self.card is not None}, held {self.tidy_held or '-'})"


@dataclass(frozen=True)
class Checked:
    outcomes: tuple[Outcome, ...]
    batch_held: str = ""                    # why no tidy-up of this batch may happen at once ("" = none)

    @property
    def yes(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.yes)


def _rule_block(rule: Rule) -> str:
    rows = [f"question: {data(rule.question, 200)}"]
    if rule.fields:
        rows.append("values:")
        rows += [f"- {item.name} ({item.type}){': ' + data(item.hint, 80) if item.hint else ''}" for item in rule.fields]
    if rule.action.kind == ACTION_REPLY:
        rows.append(f"reply: a reply draft to the sender of each yes, saying: {data(rule.action.reply_hint, 200) or 'a short acknowledgement'}")
    return "\n".join(rows)


def _person(person: Any) -> str:
    if person is None:
        return "unknown"
    name = data(getattr(person, "name", ""), 60)
    address = data(getattr(person, "email", ""), 254)
    return f"{name} <{address}>".strip() if name else address


def mail_block(number: int, item: Item, *, time_zone: Any = None) -> str:
    when = item.ref.received.astimezone(time_zone) if time_zone is not None else item.ref.received.astimezone()
    head = (f"from: {_person(item.ref.sender)}\nsubject: {data(item.message.subject or item.ref.subject, 200)}\n"
            f"date: {when.strftime('%Y-%m-%d %H:%M')}")
    # Each line of the text as data(): no "<" or ">" can look like a block of Jarvis's, no "|" like a field.
    text = "\n".join(data(line) for line in str(item.message.text or "").split("\n"))
    return untrusted(f"mail m{number}", f"{head}\n\n{text}")


def rule_prompt(rule: Rule, items: Sequence[Item], *, now: datetime, time_zone_name: str) -> str:
    blocks = [f"<now>{now.strftime('%A %Y-%m-%d %H:%M')} (time zone {data(time_zone_name, 60) or 'unknown'})</now>",
              f"<rule>\n{_rule_block(rule)}\n</rule>",
              "<mail>\n" + MAIL_NOTE + "\n" + "\n".join(mail_block(number, item) for number, item in
                                                         enumerate(items, 1)) + "\n</mail>",
              f"<command>{COMMAND}</command>"]
    return "\n\n".join(blocks) + "\n"


def read_rule(event: Mapping[str, Any], init: Any) -> RuleAnswer | AskFailure:
    data_ = structured(event, init)
    if isinstance(data_, AskFailure):
        return data_
    results = data_.get("results")
    if not isinstance(results, list) or len(results) > 8:
        return failure(GARBLED, "shape")
    found: dict[int, Result] = {}
    for item in results:
        if not isinstance(item, dict):
            return failure(GARBLED, "shape")
        mail, answer = item.get("mail"), item.get("answer")
        if not isinstance(mail, str) or not re.fullmatch(r"m[1-8]", mail) or answer not in ("yes", "no"):
            return failure(GARBLED, "shape")
        number = int(mail[1:])
        if number in found:
            return failure(GARBLED, "repeat")
        raw_values = item.get("values") or {}
        if not isinstance(raw_values, dict) or len(raw_values) > 5 or not all(
                isinstance(key, str) and isinstance(value, str) and len(value) <= 120 for key, value in raw_values.items()):
            return failure(GARBLED, "values")
        draft = item.get("draft") or ""
        if not isinstance(draft, str) or len(draft) > 2000:
            return failure(GARBLED, "draft")
        found[number] = Result(answer == "yes", dict(raw_values) if answer == "yes" else {},
                               draft if answer == "yes" else "")
    return RuleAnswer(found)


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", str(text or "").casefold())


def repeats_other(draft: str, own: str, others: Sequence[str], run: int = LEAK_WORDS) -> bool:
    """``draft`` holds ``run`` words in a row of another text (``others``) that its own mail (``own``) does not."""
    words = _words(draft)
    if len(words) < run:
        return False
    grams = {tuple(words[index:index + run]) for index in range(len(words) - run + 1)}
    own_words = _words(own)
    own_grams = {tuple(own_words[index:index + run]) for index in range(len(own_words) - run + 1)}
    for other in others:
        other_words = _words(other)
        other_grams = {tuple(other_words[index:index + run]) for index in range(len(other_words) - run + 1)}
        if (grams & other_grams) - own_grams:
            return True
    return False


def reads_like_orders(item: Item) -> bool:
    text = f"{item.message.subject}\n{item.message.text}"
    return any(guard.strong_order(line) for line in text.splitlines() if line.strip())


def _mail_text(item: Item) -> str:
    sender = getattr(item.ref.sender, "name", "") or ""
    return f"{item.message.subject}\n{sender}\n{item.message.text}"


def _when(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None


def _build(rule: Rule, item: Item, checked: Mapping[str, str], draft: str, others: Sequence[str],
           own: Collection[str], now: datetime) -> tuple[ProposedAction | None, str, str]:
    """(the card, its line, why none) for one "yes"."""
    action = rule.action
    link = thread_link("", item.ref.thread_id)
    if action.kind == ACTION_TODO:
        title = render(action.title, checked)
        if not title:
            return None, "", "no_title"
        due_raw = checked.get(action.due, "") if action.due else ""
        if action.due and not due_raw:
            return None, "", "no_due"
        due = due_raw or item.ref.received.astimezone().date().isoformat()
        line = cards.todo_line(title, due, link=link)
    elif action.kind == ACTION_EVENT:
        start_raw = checked.get(action.start, "")
        start = _when(start_raw)
        if start is None or len(start_raw) != 16:
            return None, "", "no_start"
        if start < now.replace(tzinfo=None) - timedelta(hours=1):
            return None, "", "past"
        title = render(action.title, checked)
        if not title:
            return None, "", "no_title"
        line = cards.calendar_line(title, start, start + timedelta(minutes=action.minutes))
    elif action.kind == ACTION_REPLY:
        sender = item.ref.sender.email if item.ref.sender is not None else ""
        if not sender or sender.casefold() in own:
            return None, "", "no_sender"
        if not draft.strip():
            return None, "", "no_draft"
        if repeats_other(draft, _mail_text(item), others):
            return None, "", "leak"
        if not item.ref.msgid:
            return None, "", "no_msgid"
        line = cards.reply_line(rule.account, item.ref.thread_id, item.ref.msgid, item.ref.message_id, [sender],
                                item.message.subject or item.ref.subject, draft, link)
    else:
        return None, "", ""
    card = parse_action_line(line)
    if card.error or card.kind not in (TODO, CALENDAR, REPLY):
        return None, "", "unreadable"
    if card.kind == REPLY and [address.casefold() for address in card.mail_recipients()] != [sender.casefold()]:
        return None, "", "recipients"
    card = cards.with_job(card, job_kind="rule", job_id=rule.id, key=item.ref.message_id)
    return card, line, ""


def outcomes(rule: Rule, items: Sequence[Item], answer: RuleAnswer, *, now: datetime,
             own: Collection[str] = ()) -> Checked:
    own_set = {address.casefold() for address in own}
    texts = [_mail_text(item) for item in items]
    built: list[Outcome] = []
    for number, item in enumerate(items, 1):
        result = answer.results.get(number, Result(False))
        if not result.yes:
            built.append(Outcome(item.ref, False, tidy_held=HELD_NO))
            continue
        checked: dict[str, str] = {}
        for spec in rule.fields:
            value = values.check(spec.type, result.values.get(spec.name), texts[number - 1], received=item.ref.received)
            if value is not None:
                checked[spec.name] = value
        others = texts[:number - 1] + texts[number:]
        if rule.action.kind == ACTION_TELL:
            built.append(Outcome(item.ref, True, checked, tell=True))
            continue
        card, line, why = _build(rule, item, checked, result.draft, others, own_set, now)
        built.append(Outcome(item.ref, True, checked, card=card, line=line, key=item.ref.message_id, refused=why,
                             tell=card is None))
    batch_held = ""
    if any(reads_like_orders(item) for item in items):
        batch_held = HELD_ORDERS
    else:
        yes = sum(1 for outcome in built if outcome.yes)
        dry = rule.dry_run.yes_rate if rule.dry_run is not None else 0.0
        if len(items) >= JUMP_MIN_BATCH and yes / len(items) > max(JUMP_FLOOR, dry + JUMP_OVER):
            batch_held = HELD_JUMP
    final: list[Outcome] = []
    for outcome in built:
        held = outcome.tidy_held
        if outcome.yes and rule.tidy.kind != TIDY_NONE:
            held = batch_held or _mail_held(rule, outcome.ref, own_set)
        final.append(Outcome(outcome.ref, outcome.yes, outcome.values, outcome.card, outcome.line, outcome.key,
                             outcome.tell, outcome.refused, held))
    return Checked(tuple(final), batch_held)


def _mail_held(rule: Rule, ref: MailRef, own: Collection[str]) -> str:
    if not ref.dmarc_pass:
        return HELD_DMARC
    sender = ref.sender.email.casefold() if ref.sender is not None else ""
    if not sender or sender in own:
        return HELD_OWN
    if ref.labels & {"SENT", "DRAFT"}:
        return HELD_SENT
    if rule.tidy.kind == TIDY_ARCHIVE and ref.labels & {"STARRED", "IMPORTANT"}:
        return HELD_STARRED
    return ""
