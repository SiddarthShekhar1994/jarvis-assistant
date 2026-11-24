"""An agent's run (no Qt): the sources Jarvis read for it, one run with no tools, a report and its cards.

    agent_prompt(agent, items, events, notes, *, now, time_zone_name, own) -> (prompt, index)
        the run's stdin: <now>; <owner> (the owner's own addresses: never a recipient); <accounts>; <job> (its name,
        the card kinds it may propose, when it reports); <calendar> (the days it reads: titles, times, places);
        <memory> (the owner's notes, when it reads them); <mail> (each matched mail an untrusted block of its own:
        Jarvis's header lines with its account, thread, Message-ID and Gmail id, then its text); <command> (the owner's
        own words for the job). The ContextIndex knows exactly the threads and senders of that mail.
    read_agent(event, init) -> AgentAnswer | AskFailure     the answer, read strictly
    agent_cards(agent, answer, index, items, *, now, notes, own) -> list[(card, line, key)]
        each line through ask.validate.to_cards (the agent's kinds only), then Jarvis's own checks: a reply goes to the
        sender of the mail it answers and nobody else, repeats no note (memory.note_leak) and no other mail of the run
        (5 words in a row); every line written again by Jarvis (standing.cards.rebuild: the model's link dropped, a
        reply's own thread link); a to-do's or event's id stable from the agent and its title and day.
    needs_owner(agent, answer, cards) -> bool     Jarvis's own judgement, which the model may raise but never clear
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from ..actions import CALENDAR, REPLY, TODO, ProposedAction, parse_action_line
from ..ask.context import MAIL_NOTE, MEMORY_NOTE, SOURCE_MAIL, SOURCE_OWNER, ContextIndex, data
from ..ask.stream import AskFailure, GARBLED, failure
from ..ask.validate import to_cards
from ..gmail import thread_link
from ..memory import note_leak
from . import cards
from .engine import structured
from .model import PROPOSE_CALENDAR, PROPOSE_REPLY, PROPOSE_TODO, REPORT_EVERY, Agent
from .rules import Item, mail_block, repeats_other

KIND_OF = {PROPOSE_TODO: TODO, PROPOSE_CALENDAR: CALENDAR, PROPOSE_REPLY: REPLY}
FIXED_COMMAND = ("Do the job below for the owner from <calendar>, <memory> and <mail> as they are now: write the "
                 "report, and line up only the cards it calls for. The owner's own words for this job: ")
MAX_LINES = 4
REPORT_CAP = 3000
SUMMARY_CAP = 120
# Build plan item 5: an agent whose reports land in a sheet is shown its columns (never its rows) and may give rows.
MAX_ROWS = 20
CELL_CAP = 500
SHEET_COMMAND = (" Its rows go into the sheet in <sheet>: give each new thing it found as one row in \"rows\", its "
                 "cells in the columns' order, as text.")


def rows_property(columns: int) -> dict[str, Any]:
    """The schema's "rows" for a sheet of ``columns`` columns (at most MAX_ROWS rows)."""
    return {"type": "array", "maxItems": MAX_ROWS,
            "items": {"type": "array", "maxItems": max(1, columns),
                      "items": {"type": "string", "maxLength": CELL_CAP}},
            "description": "New rows for <sheet>, each a list of cells in its columns' order (empty: \"\")."}


def sheet_block(name: str, columns: Sequence[Any]) -> str:
    """<sheet name="..."> with its columns' names and types only: an agent never sees a sheet's rows (Jarvis removes
    duplicates on this PC)."""
    heads = []
    for column in columns:
        choices = getattr(column, "choices", ()) or ()
        extra = f" ({', '.join(data(choice, 40) for choice in choices)})" if choices else ""
        heads.append(f"{data(column.name, 40)} [{column.type}{extra}]")
    title = data(name, 60).replace('"', "'")
    return f'<sheet name="{title}">\ncolumns: ' + " | ".join(heads) + "\n</sheet>"


@dataclass(frozen=True)
class AgentAnswer:
    report: str
    summary: str
    needs_owner: bool
    lines: tuple[str, ...] = field(default=())
    rows: tuple[tuple[str, ...], ...] = field(default=())   # a sheet's new rows (only when its run was offered them)

    def __repr__(self) -> str:
        return f"AgentAnswer({len(self.report)} chars, {len(self.lines)} line(s), needs {self.needs_owner})"


@dataclass(frozen=True)
class EventRow:
    """A calendar row an agent reads (Jarvis's own read of the owner's calendar)."""
    account: str
    title: str
    start: datetime | None
    end: datetime | None = None
    all_day: bool = False
    where: str = ""

    def __repr__(self) -> str:
        return f"EventRow({self.account})"


def _mail_header(number: int, item: Item) -> str:
    ref = item.ref
    return (f"mail m{number} | acct={data(ref.account, 24)} | thread={ref.thread_id} | msgid={data(ref.msgid, 300)} | "
            f"gmid={ref.message_id}")


def agent_prompt(agent: Agent, items: Sequence[Item], events: Sequence[EventRow], notes: Sequence[str], *,
                 now: datetime, time_zone_name: str, own: Sequence[str],
                 aliases: Sequence[str], sheet: tuple[str, Sequence[Any]] | None = None) -> tuple[str, ContextIndex]:
    index = ContextIndex(aliases=frozenset(aliases), own_addresses=frozenset(address.casefold() for address in own),
                         command=agent.words)
    blocks = [f"<now>{now.strftime('%A %Y-%m-%d %H:%M')} (time zone {data(time_zone_name, 60) or 'unknown'})</now>",
              "<owner>" + ", ".join(data(address, 254) for address in own) + "</owner>",
              "<accounts>" + ", ".join(data(alias, 24) for alias in aliases) + "</accounts>",
              f"<job>name: {data(agent.name, 40)}\nmay propose: "
              f"{', '.join(agent.proposes) or 'nothing (report only)'}\nreports: {agent.report}</job>"]
    if events:
        rows = []
        for event in events:
            when = ("all day " + event.start.strftime("%Y-%m-%d")) if event.all_day and event.start else (
                f"{event.start:%Y-%m-%d %H:%M}" + (f"-{event.end:%H:%M}" if event.end else "") if event.start else "")
            rows.append(f"{data(event.account, 24)} | {when} | {data(event.title, 200)}"
                        + (f" | {data(event.where, 200)}" if event.where else ""))
        blocks.append("<calendar>\n" + "\n".join(rows) + "\n</calendar>")
    if notes:
        blocks.append("<memory>\n" + MEMORY_NOTE + "\n" + "\n".join(f"- {data(note, 200)}" for note in notes)
                      + "\n</memory>")
    if items:
        parts = [MAIL_NOTE]
        for number, item in enumerate(items, 1):
            parts.append(_mail_header(number, item))
            parts.append(mail_block(number, item))
            ids = index.threads.setdefault((item.ref.account, item.ref.thread_id), set())
            if item.ref.msgid:
                ids.add(f"<{item.ref.msgid}>")          # as a parsed line's msgid= reads it
            ids.add(f"gmid:{item.ref.message_id}")
            if item.ref.sender is not None:
                index.add_address(item.ref.sender.email, SOURCE_MAIL)
        blocks.append("<mail>\n" + "\n".join(parts) + "\n</mail>")
    else:
        blocks.append("<mail>No new mail for this job.</mail>")
    for address in own:
        index.add_address(address, SOURCE_OWNER)
    command = FIXED_COMMAND
    if sheet is not None:
        blocks.append(sheet_block(sheet[0], sheet[1]))
        command = FIXED_COMMAND.replace("The owner's own words", SHEET_COMMAND.strip() + " The owner's own words")
    blocks.append(f"<command>{command}{data(agent.words, 600)}</command>")
    return "\n\n".join(blocks) + "\n", index


def agent_reader(columns: int) -> Callable[[Mapping[str, Any], Any], AgentAnswer | AskFailure]:
    """read_agent for a run that was offered "rows" for a sheet of ``columns`` columns (0: none offered)."""
    return lambda event, init: read_agent(event, init, columns=columns)


def read_agent(event: Mapping[str, Any], init: Any, *, columns: int = 0) -> AgentAnswer | AskFailure:
    data_ = structured(event, init)
    if isinstance(data_, AskFailure):
        return data_
    rows_ = data_.get("rows")
    if rows_ is not None:
        if (not columns or not isinstance(rows_, list) or len(rows_) > MAX_ROWS
                or not all(isinstance(row, list) and len(row) <= columns
                           and all(isinstance(cell, str) and len(cell) <= CELL_CAP for cell in row) for row in rows_)):
            return failure(GARBLED, "shape")
    rows = tuple(tuple(row) for row in rows_ or ())
    report, summary, needs = data_.get("report"), data_.get("summary"), data_.get("needs_owner")
    lines = data_.get("lines") or []
    if (not isinstance(report, str) or len(report) > REPORT_CAP or not isinstance(summary, str)
            or len(summary) > SUMMARY_CAP or not isinstance(needs, bool) or not isinstance(lines, list)
            or len(lines) > MAX_LINES or not all(isinstance(line, str) and len(line) <= 6000 for line in lines)):
        return failure(GARBLED, "shape")
    return AgentAnswer(report.strip(), " ".join(summary.split()), needs, tuple(lines), rows)


def _sender_of(items: Sequence[Item], account: str, thread: str, msgid: str, gmid: str) -> str:
    msgid = msgid.strip().strip("<>")
    for item in items:
        ref = item.ref
        if ref.account == account and ref.thread_id == thread and (
                (msgid and ref.msgid == msgid) or (gmid and ref.message_id == gmid)):
            return ref.sender.email.casefold() if ref.sender is not None else ""
    return ""


def agent_cards(agent: Agent, answer: AgentAnswer, index: ContextIndex, items: Sequence[Item], *, now: datetime,
                notes: Sequence[str], own: Collection[str]) -> list[tuple[ProposedAction, str, str]]:
    kinds = frozenset(KIND_OF[kind] for kind in agent.proposes)
    texts = {item.ref.message_id: f"{item.message.subject}\n{item.message.text}" for item in items}
    own_set = {address.casefold() for address in own}

    def extra(card: ProposedAction) -> str:
        if card.kind == REPLY:
            sender = _sender_of(items, card.account, card.field("thread"), card.field("msgid"), card.field("gmid"))
            recipients = [address.casefold() for address in card.mail_recipients()]
            if not sender or sender in own_set or recipients != [sender]:
                return "A standing agent's reply goes to the sender of the mail it answers, and nobody else"
            if note_leak(card.body, notes, ""):
                return "The draft repeats one of your notes"
            replied = next((item for item in items if item.ref.message_id == card.field("gmid")
                            or item.ref.msgid == card.field("msgid").strip("<>")), None)
            own_text = texts.get(replied.ref.message_id, "") if replied is not None else ""
            others = [text for key, text in texts.items() if replied is None or key != replied.ref.message_id]
            if repeats_other(card.body, own_text, others):
                return "The draft repeats another mail"
        elif note_leak(card.title, notes, ""):
            return "The title repeats one of your notes"
        return ""

    checked = to_cards(list(answer.lines)[:MAX_LINES], index, now=now, max_cards=MAX_LINES, kinds=kinds,
                       extra=extra)
    made: list[tuple[ProposedAction, str, str]] = []
    for card in checked.cards:
        if card.error:
            continue
        link = thread_link("", card.field("thread")) if card.kind == REPLY else ""
        line = cards.rebuild(card, gmail_link=link)
        again = parse_action_line(line)
        if again.error or again.kind != card.kind:
            continue
        key = (f"{card.account}|{card.field('msgid') or card.field('gmid')}" if card.kind == REPLY
               else f"{data(card.title, 200).casefold()}|{card.field('due') or (card.start or card.all_day_start)}")
        again = replace(again, unverified=card.unverified)
        made.append((cards.with_job(again, job_kind="agent", job_id=agent.id, key=key), line, key))
    return made


def needs_owner(agent: Agent, answer: AgentAnswer | None, made: Sequence[Any]) -> bool:
    """Something needs the owner when the run made cards, the job reports every run, or the model says so."""
    return bool(made) or agent.report == REPORT_EVERY or bool(answer is not None and answer.needs_owner)
