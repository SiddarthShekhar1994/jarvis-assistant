"""Radar (build plan item 6): ready-made standing agents for internships, hackathons, deadlines and subscriptions
(renewals, trials ending). No Qt, no model run, no network: a preset is fixed data, and Add makes its draft directly
through the same checks a setup's answer goes through (the words as setup takes them, each filter through
standing.filters grounded in the preset's own words, where it writes through setup.lands_of, the definition through
model.agent_from). Off by default: a draft, on only by the owner's Turn on.

    PRESETS                      the four presets, in the order the Radar card offers them
    preset_of(key) -> Preset | None
    missing(agents) -> tuple[Preset, ...]     the presets not added yet (by the agent's name)
    preset_agent(preset, aliases, *, calendar_ok, docs_dir, now, runs_per_day, min_every) -> Agent
        its draft: mail only (no web: an agent run has no tools), two filter terms on each account in ``aliases``
        (at most two accounts, so at most four mail sources); Deadlines also reads the calendar for 14 days when
        ``calendar_ok``; its sheet found by name read only, or planned for Turn on to make. SetupRefused: why not.

Nothing here is logged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .filters import FilterRefused, check_filter
from .model import (KIND_AGENT, MAX_MAIL_SOURCES, PROPOSE_CALENDAR, PROPOSE_TODO, REPORT_NEEDED, STATUS_DRAFT,
                    Agent, DefinitionError, agent_from, new_id)
from .setup import SetupRefused, check_words, lands_of

MAX_ACCOUNTS = 2                 # two filter terms on each: within model.MAX_MAIL_SOURCES


@dataclass(frozen=True)
class Preset:
    key: str                                     # "internships" | "hackathons" | "deadlines" | "subscriptions"
    label: str                                   # its button on the Radar card
    name: str                                    # the agent's name (how missing() knows it was added)
    words: str                                   # the run's <command>: shown on the card as "Does: ..."
    trigger: tuple[tuple[str, Any], ...]         # model.trigger_from's data
    terms: tuple[str, ...]                       # one mail source per term on each account
    calendar: int                                # calendar days when some account has the calendar feature
    proposes: tuple[str, ...]
    sheet: str
    columns: tuple[tuple[str, str, tuple[str, ...]], ...]
    keys: int


def _text(name: str) -> tuple[str, str, tuple[str, ...]]:
    return (name, "text", ())


PRESETS: tuple[Preset, ...] = (
    Preset("internships", "Internships", "Internships radar",
           "Watch my internship mail: for each internship opening, application or deadline add a row with the "
           "company, the role, the deadline, the link and the status New; line up a to-do for a deadline within 7 "
           "days. Leave out internships newsletters that name no role.",
           (("kind", "mail"),), ("internship", "internships"), 0, (PROPOSE_TODO,), "Internships",
           (_text("Company"), _text("Role"), ("Deadline", "date", ()), ("Link", "link", ()),
            ("Status", "choice", ("New", "Applied", "Interview", "Offer", "Closed"))), 2),
    Preset("hackathons", "Hackathons", "Hackathons radar",
           "Watch my hackathon mail: for each hackathon I could join add a row with its name, who runs it, the "
           "registration deadline, the link and the status New; line up a to-do for a registration deadline within 7 "
           "days. Leave out hackathons I only hear about after they close.",
           (("kind", "mail"),), ("hackathon", "hackathons"), 0, (PROPOSE_TODO,), "Hackathons",
           (_text("Hackathon"), _text("Organizer"), ("Deadline", "date", ()), ("Link", "link", ()),
            ("Status", "choice", ("New", "Registered", "Submitted", "Skipped"))), 1),
    Preset("deadlines", "Deadlines", "Deadlines radar",
           "Every morning, from my deadline mail and my calendar for the next 14 days, list each deadline or due "
           "date: add a row with what is due, the due date, who set it, the link and the status Open; line up a "
           "to-do for anything due within 3 days.",
           (("kind", "time"), ("times", ("08:00",))), ("deadline", '"due date"'), 14, (PROPOSE_TODO,), "Deadlines",
           (_text("What"), ("Due", "date", ()), _text("From"), ("Link", "link", ()),
            ("Status", "choice", ("Open", "Done"))), 2),
    Preset("subscriptions", "Subscriptions", "Subscriptions radar",
           "Watch my subscription and trial mail, receipts and notices: for each renewal or free trial that ends add "
           "a row with the service, the amount, the date it renews or ends, the kind (Renewal or Trial end) and the "
           "link; line up a calendar reminder 3 days before that date.",
           (("kind", "mail"),), ("subscription", "trial"), 0, (PROPOSE_CALENDAR,), "Subscriptions",
           (_text("Service"), _text("Amount"), ("Renews on", "date", ()),
            ("Kind", "choice", ("Renewal", "Trial end")), ("Link", "link", ())), 2),
)


def preset_of(key: Any) -> Preset | None:
    return next((preset for preset in PRESETS if preset.key == key), None)


def missing(agents: Iterable[Agent]) -> tuple[Preset, ...]:
    names = {" ".join(str(getattr(agent, "name", "")).split()).casefold() for agent in agents}
    return tuple(preset for preset in PRESETS if preset.name.casefold() not in names)


def preset_agent(preset: Preset, aliases: Sequence[str], *, calendar_ok: bool, docs_dir: Any, now: datetime,
                 runs_per_day: int, min_every: int = 30) -> Agent:
    words = check_words(preset.words)
    picked = list(dict.fromkeys(aliases))[:MAX_ACCOUNTS]
    try:
        mail = [{"account": alias, "query": check_filter(term, words)} for alias in picked for term in preset.terms]
    except FilterRefused as refused:
        raise SetupRefused(str(refused)) from None
    if len(mail) > MAX_MAIL_SOURCES:
        raise SetupRefused("Jarvis could not make that job (too many mail sources)")
    lands = lands_of({"kind": "sheet", "name": preset.sheet,
                      "columns": [{"name": name, "type": type_, "choices": list(choices)}
                                  for name, type_, choices in preset.columns]}, docs_dir)
    if lands is None:
        raise SetupRefused("Documents are off on this PC")
    if not lands["id"] and preset.keys != 1:       # a sheet found by name keeps its own keys
        lands["keys"] = preset.keys
    trigger = {name: list(value) if isinstance(value, tuple) else value for name, value in preset.trigger}
    try:
        return agent_from({"id": new_id(KIND_AGENT), "name": preset.name, "words": words, "trigger": trigger,
                           "mail": mail, "calendar_days": preset.calendar if calendar_ok else 0, "notes": False,
                           "proposes": list(preset.proposes), "report": REPORT_NEEDED, "runs_per_day": runs_per_day,
                           "status": STATUS_DRAFT, "created": now.isoformat(timespec="seconds"), "lands": lands},
                          min_every=min_every)
    except DefinitionError as error:
        raise SetupRefused(f"Jarvis could not make that job ({error})") from None
