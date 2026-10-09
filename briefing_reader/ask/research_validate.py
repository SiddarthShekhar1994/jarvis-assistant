"""Web research's answer -> NEEDS YOUR OK cards (source="ask"), each checked by Jarvis's own rules.

    validate(answer, web, ...)    (cards, ResearchReport): one web Open card per source the research
                                  saw (any public https site, the domain shown, opened only on the
                                  card's click), then the Todo / one-off Calendar / Open suggestions
                                  that pass; everything else left out or dropped with its reason
    entry_text(report)            the JARVIS tab's entry: the answer, the numbered sources, what was
                                  left out
    notes(report)                 the bar's note ("1 source left out", ...)
    cards_words(sources, proposals), steps_words(report)   the counts every view uses ("2 source
                                  cards + 1 proposal", "2 searches, 1 page read")

Sources (in the model's order; the first failing rule wins): an https link on a public host
(actions.web_link_problem); not the same page as an earlier source; a page the research found
(a search listed it) or read (a page read returned it) - when the search results could not be read,
it is kept with a warning instead; at most ``max_sources``; a clean title (the domain when empty);
the card ``Open: title=[n] <title> | link=<url>``, numbered as the model numbered it so the
answer's [n] marks stay right.

Suggestions (at most 3): only Todo:, Calendar: and Open: lines (an Email, Reply, RSVP, Move,
Cancel, Slack or Share is dropped); a Todo names no account, links only to one of the sources (its
link as parsed, after invisible characters are removed) and has no block in the past; a Calendar
line is one new event for the owner alone (no repeat, no email address anywhere, no link, web
address or phone number in its title or place, not in the past; over a year away gets a warning)
and keeps only what its card shows: the title, the time and the place (its notes are dropped, so
nothing the owner did not see reaches the calendar); an Open line's page must be one the research
saw and not already a source. A card already on the page is information only
(ALREADY_LISTED); a repeat is dropped. Suggestion cards come first, then the source cards, at most
``max_cards`` (the last source cards are left out).

Pure; nothing is logged here (the planner logs counts; the report's repr is counts only).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..actions import (
    CALENDAR,
    CANCEL,
    EMAIL,
    IDN_WARNING,
    MOVE,
    OPEN,
    REPLY,
    RSVP,
    SHARE,
    SLACK,
    SOURCE_ASK,
    TODO,
    ProposedAction,
    idn_host,
    link_host,
    parse_action_line,
    _clean_structured,
    web_link_problem,
    with_error,
)
from .context import _ADDRESS_RE, data
from .research import ResearchAnswer, WebLog, normalize_url
from .validate import ALREADY_LISTED

SOURCE_TITLE_CAP = 120
MAX_SUGGESTIONS = 3
FAR_DAYS = 365
UNCHECKED_WARNING = "Jarvis couldn't check this page against the search results"
NOT_SEEN = "not a page the research found or read"
FAR_WARNING = "more than a year away - check the date"
NOT_A_KIND = "not a Todo:, Calendar: or Open: line"
NO_ACCOUNT = "web research can't name an account"
LINK_NOT_SOURCE = "its link is not one of the sources"
BLOCK_PAST = "the block is in the past"
ONE_OFF_ONLY = "web research proposes one-off events only"
NO_GUESTS = "web research can't invite anyone"
NO_LINKS = "web research can't put a link, web address or phone number in an event"
TIME_PAST = "the time is in the past"
REPEAT_SUGGESTION = "a repeat of an earlier suggestion"
REFUSED_TOOLS_NOTE = "Claude Code refused its web tools (README: Troubleshooting)"
NO_ANSWER = "I couldn't find an answer to that on the web."
_KIND_NAMES = {EMAIL: "Email", REPLY: "Reply", RSVP: "RSVP", MOVE: "Move", CANCEL: "Cancel", SLACK: "Slack",
               SHARE: "Share"}
_LINK_FIELD_RE = re.compile(r"(?:^|\|)\s*link\s*=\s*([^|]*)", re.IGNORECASE)
# What an event from the web may not carry (a card shows the title, the time and the place; Approve
# would write them into the owner's calendar): a link or web address, or a phone number.
_EVENT_LINK_RE = re.compile(r"://|\bwww\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,63}(?:/|\b)", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d(?:[\s().-]*\d){6,}")
_NOT_PHONE_RE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
                           r"|\b(?:19|20)\d{2}\b")


def same_page_reason(number: int) -> str:
    return f"the same page as source [{number}]"


def cant_propose(kind: str) -> str:
    return f"Web research can't propose {_KIND_NAMES.get(kind, kind.capitalize())}"


@dataclass(frozen=True)
class WebSource:
    """One source that became a web Open card (shown; never logged)."""

    number: int                     # the source's number in the research's answer ([1] is the first)
    title: str                      # cleaned; the domain when the research gave none
    url: str
    domain: str
    checked: bool = True            # False: provenance could not be checked against the search results
    card_id: str = ""

    def __repr__(self) -> str:
        return f"WebSource(number={self.number}, checked={self.checked})"


@dataclass(frozen=True)
class ResearchReport:
    """What one research answered and what became of it (AskOutcome.research): shown, never logged
    (the repr is counts only)."""

    answer: str                                   # one line, [n] marks kept
    question: str = ""                            # the question sent (the owner's words)
    hint: str = ""                                # the search hint sent ("" when none)
    forced: bool = False                          # started with web: (else handed over by the planner)
    sources: tuple[WebSource, ...] = ()
    left_out: tuple[tuple[int, str], ...] = ()    # (source number, reason)
    dropped: tuple[tuple[str, str], ...] = ()     # (suggestion line, reason)
    searches: int = 0
    fetches: int = 0                              # page reads Claude Code asked for
    denied: int = 0
    hits: int = 0
    suggested: tuple[tuple[str, str], ...] = ()   # (suggestion line, its card's id) for each that became a card
    reads: int = 0                                # page reads that returned the page

    def __repr__(self) -> str:
        return (f"ResearchReport(sources={len(self.sources)}, left_out={len(self.left_out)}, "
                f"dropped={len(self.dropped)}, searches={self.searches}, fetches={self.fetches}, "
                f"denied={self.denied}, forced={self.forced})")


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def _source_card(number: int, title: str, url: str, domain: str, warnings: tuple[str, ...]) -> ProposedAction:
    card = parse_action_line(f"Open: title=[{number}] {title} | link={url}", link_hosts=(domain,))
    if card.error:
        return card
    return replace(card, source=SOURCE_ASK, web=True, warnings=warnings)


def _sources(answer: ResearchAnswer, web: WebLog, max_sources: int
             ) -> tuple[list[tuple[WebSource, ProposedAction]], list[tuple[int, str]]]:
    accepted: list[tuple[WebSource, ProposedAction]] = []
    left_out: list[tuple[int, str]] = []
    pages: dict[str, int] = {}
    for number, (raw_title, raw_url) in enumerate(answer.sources, 1):
        url = (raw_url or "").strip()
        problem = web_link_problem(url)
        if problem:
            left_out.append((number, problem))
            continue
        page = normalize_url(url)
        if page in pages:
            left_out.append((number, same_page_reason(pages[page])))
            continue
        checked = True
        if page not in web.seen:
            if web.checkable:
                left_out.append((number, NOT_SEEN))
                continue
            checked = False
        if len(accepted) >= max_sources:
            left_out.append((number, f"more than {max_sources} sources"))
            continue
        domain = link_host(url)
        title = data(raw_title, SOURCE_TITLE_CAP) or domain
        warnings = tuple(warning for warning, applies in ((UNCHECKED_WARNING, not checked),
                                                         (IDN_WARNING, idn_host(url))) if applies)
        card = _source_card(number, title, url, domain, warnings)
        if card.error:
            left_out.append((number, card.error))
            continue
        pages[page] = number
        accepted.append((WebSource(number, title, url, domain, checked, card.id), card))
    return accepted, left_out


# --------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------

def _raw_link(line: str) -> str:
    """The link= value as the line's parser reads it (invisible characters removed first, so a
    "li\u00adnk=" key cannot hide a link from the checks below)."""
    found = _LINK_FIELD_RE.search(_clean_structured(line or ""))
    return found.group(1).strip() if found else ""


def _names_a_link_or_phone(*texts: str) -> bool:
    """A link or web address, or a phone number (seven digits or more; dates, times and years are
    not phone numbers), in any of ``texts``."""
    return any(_EVENT_LINK_RE.search(text or "") or _PHONE_RE.search(_NOT_PHONE_RE.sub(" ", text or ""))
               for text in texts)


def _suggestion(line: str, sources: dict[str, int], web: WebLog, now: datetime) -> ProposedAction | str:
    """The card a suggestion becomes, or why it is dropped."""
    first = parse_action_line(line)
    kind = first.kind
    if kind in _KIND_NAMES:
        return cant_propose(kind)
    if kind not in (CALENDAR, TODO, OPEN) or (kind in (TODO, OPEN) and not first.structured):
        return NOT_A_KIND
    # A Todo's or Open's link is read on its own host here (so the line's other problems come
    # first); the web rule and the provenance are checked below.
    link = _raw_link(line) if kind in (TODO, OPEN) else ""
    host = link_host(link) if link else ""
    card = parse_action_line(line, link_hosts=(host,) if host else ())
    if card.error:
        return card.error
    warnings: tuple[str, ...] = ()
    if kind == TODO:
        if card.account:
            return NO_ACCOUNT
        # The parsed link decides (it is what Open page opens); a line whose link the checks could
        # not see, or whose parsed link is not a source, is dropped.
        if (link or card.link) and (not card.link or web_link_problem(card.link)
                                    or normalize_url(card.link) not in sources):
            return LINK_NOT_SOURCE
        if card.start is not None and card.start.replace(tzinfo=now.tzinfo) < now:
            return BLOCK_PAST
        return replace(card, source=SOURCE_ASK, web=bool(card.link))
    if kind == CALENDAR:
        if card.rrule or card.repeat:
            return ONE_OFF_ONLY
        if any(_ADDRESS_RE.search(text or "") for text in (card.title, card.where, card.notes, card.raw)):
            return NO_GUESTS
        if _names_a_link_or_phone(card.title, card.where):
            return NO_LINKS
        if card.start is not None:
            start = card.start.replace(tzinfo=now.tzinfo)
            if start < now:
                return TIME_PAST
            far = start > now + timedelta(days=FAR_DAYS)
        else:
            day = card.all_day_start
            if day is None or day < now.date():
                return TIME_PAST
            far = day > (now + timedelta(days=FAR_DAYS)).date()
        if far:
            warnings = (FAR_WARNING,)
        # Only what the card shows reaches the calendar: never notes the owner did not see.
        return replace(card, source=SOURCE_ASK, notes="", warnings=card.warnings + warnings)
    # OPEN
    problem = web_link_problem(card.link)
    if problem:
        return f"its link is {problem}"
    page = normalize_url(card.link)
    if page in sources:
        return same_page_reason(sources[page])
    if page not in web.seen:
        if web.checkable:
            return f"its link is {NOT_SEEN}"
        warnings = (UNCHECKED_WARNING,)
    if idn_host(card.link):
        warnings += (IDN_WARNING,)
    return replace(card, source=SOURCE_ASK, web=True, warnings=card.warnings + warnings)


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def validate(answer: ResearchAnswer, web: WebLog, *, now: datetime, max_sources: int, max_cards: int,
             page_ids: Collection[str], question: str, hint: str,
             forced: bool) -> tuple[tuple[ProposedAction, ...], ResearchReport]:
    """The research's answer as cards and a report (see the module docs). ``now`` is aware (a
    suggested time is a wall time in the owner's zone); ``page_ids`` are the cards on the page."""
    accepted, left_out = _sources(answer, web, max(1, int(max_sources)))
    by_page = {normalize_url(source.url): source.number for source, _card in accepted}
    dropped: list[tuple[str, str]] = []
    suggestion_cards: list[tuple[str, ProposedAction]] = []
    seen_ids: set[str] = set()
    for index, line in enumerate(answer.suggestions):
        if index >= MAX_SUGGESTIONS:
            dropped.append((line, f"more than {MAX_SUGGESTIONS} suggestions"))
            continue
        found = _suggestion(line, by_page, web, now)
        if isinstance(found, str):
            dropped.append((line, found))
            continue
        if found.id in seen_ids:
            dropped.append((line, REPEAT_SUGGESTION))
            continue
        seen_ids.add(found.id)
        suggestion_cards.append((line, found))
    cards: list[ProposedAction] = []
    suggested: list[tuple[str, str]] = []
    limit = max(1, int(max_cards))
    for line, card in suggestion_cards:
        if len(cards) >= limit:
            dropped.append((line, f"more than {limit} cards"))
            continue
        cards.append(_listed(card, page_ids))
        suggested.append((line, cards[-1].id))
    kept_sources: list[WebSource] = []
    for source, card in accepted:
        if len(cards) >= limit:
            left_out.append((source.number, f"more than {limit} cards"))
            continue
        cards.append(_listed(card, page_ids))
        kept_sources.append(source)
    left_out.sort(key=lambda item: item[0])
    report = ResearchReport(answer=answer.answer, question=question, hint=hint, forced=forced,
                            sources=tuple(kept_sources), left_out=tuple(left_out), dropped=tuple(dropped),
                            searches=web.searches, fetches=web.fetches, denied=web.denied, hits=web.hits,
                            suggested=tuple(suggested), reads=web.reads)
    return tuple(cards), report


def source_card_ids(report: ResearchReport) -> frozenset[str]:
    """The ids of the source cards (the web Open cards of the sources; not suggestions)."""
    return frozenset(source.card_id for source in report.sources)


def _listed(card: ProposedAction, page_ids: Collection[str]) -> ProposedAction:
    return with_error(card, ALREADY_LISTED) if card.id in page_ids else card


# --------------------------------------------------------------------------
# What the owner reads
# --------------------------------------------------------------------------

def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def entry_text(report: ResearchReport) -> str:
    """The JARVIS tab's entry for a research: the answer ([n] marks kept), the numbered sources with
    their sites, what was left out and why."""
    lines = [report.answer or NO_ANSWER, ""]
    if report.sources:
        lines.append("Sources:")
        lines += [f"[{source.number}] {source.title} ({source.domain})" for source in report.sources]
    else:
        lines.append("Sources: none")
    lines += [f"Left out: [{number}] {reason}" for number, reason in report.left_out]
    if report.dropped:
        reasons = "; ".join(reason for _line, reason in report.dropped)
        noun = "suggestion" if len(report.dropped) == 1 else "suggestions"
        lines.append(f"{len(report.dropped)} {noun} left out: {reasons}")
    return "\n".join(lines)


def cards_words(sources: int, proposals: int) -> str:
    """How many cards one research put under NEEDS YOUR OK, the same words everywhere: "2 source
    cards + 1 proposal", "1 source card", "no sources, 1 proposal", "no sources"."""
    words = _count(sources, "source card") if sources else "no sources"
    if proposals:
        words += (" + " if sources else ", ") + _count(proposals, "proposal")
    return words


def reads_words(report: ResearchReport | WebLog) -> str:
    """The page reads: "1 page read"; one that did not return the page (refused, stopped, failed,
    redirected) is counted apart: "0 page reads, 1 not read"."""
    fetches = int(getattr(report, "fetches", 0) or 0)
    reads = min(int(getattr(report, "reads", 0) or 0), fetches)
    return _count(reads, "page read") + (f", {fetches - reads} not read" if fetches > reads else "")


def steps_words(report: ResearchReport | WebLog) -> str:
    """The research's web steps: "2 searches, 1 page read" ("1 search, 0 page reads, 1 not read")."""
    searches = int(getattr(report, "searches", 0) or 0)
    return f"{searches} search{'' if searches == 1 else 'es'}, {reads_words(report)}"


def notes(report: ResearchReport) -> list[str]:
    """The bar's note: "1 source left out", "1 suggestion left out", and that Claude Code refused
    its web tools when it did and nothing was found."""
    found: list[str] = []
    if report.left_out:
        found.append(f"{_count(len(report.left_out), 'source')} left out")
    if report.dropped:
        found.append(f"{_count(len(report.dropped), 'suggestion')} left out")
    if report.denied > 0 and not report.sources:
        found.append(REFUSED_TOOLS_NOTE)
    return found
