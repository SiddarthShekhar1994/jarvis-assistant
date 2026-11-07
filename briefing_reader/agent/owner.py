"""The owner's things, for a task (SPEC 7, R3 to R7; part A4): the private tools and the change tools. No Qt.

    OwnerAccess(things, firewall, run_id=, workspace=)     one run's: its refs, the Ask index of what it read, its caps
        calendar_find, contacts_find, memory_read, conversation_read, mail_search, mail_read,
        files_find, files_list, file_read                  they read through the existing paths and go back as data
        propose_files, propose_action, open_thing, pc_control     changes: only through the existing cards and checks
    card_message(state, ending)    a file card's end, as the next message to the model

What it holds to:

* Every private result tells the firewall a private tool answered (note_private: its checks are on from then on, for
  good) and feeds its taint set before it goes back (addresses, numbers, words of 4+ letters of names, titles, subjects,
  and the names of files, apps and saved sites). A mail's body, a file's contents or the conversation make the run strict (agent.firewall). All of it
  goes back inside untrusted blocks (R5), its strong signs of an order flagged. A mail's and a file's words are kept as
  what a line in Jarvis's voice may not repeat (firewall.read_words).
* Turn off stops them at once: ``things.consent()`` (version 3 of the card) is asked at every call.
* A mail search must name what the owner said in this task, exactly as Ask refuses other searches (ask.mail.check_query,
  over this run's own index: the owner's words, and people the calendar supplied whose name they said). What only mail,
  a note or the conversation supplied never grounds one.
* A file is reached only by a ref issued in this run (desktop.refs, one index a run) and re-checked right before each
  read (desktop.service.read_text): scope, identity, the NEVER list, a name or a folder named for secrets, type,
  online-only. No parser: other types can't be read yet, and it says so.
* What plainly is a key or a password goes back as guard.SECRET_MARK and never as itself, the rest as written: in a
  mail's subject or body, a part of a file, a note, an earlier turn or an event's title (guard.scrub_secrets). The model
  is told a value was left out, Live warns of it with no word of it, and the taint set and the voice's check see only
  what is left. A value is looked for in the text as it came, up to what is kept plus a margin, then cut: a cut leaves no
  part of one.
* Changes are licensed by the owner's own words only: ``firewall.said()``, the goal and their typed messages; never the
  model's reasoning, a clicked choice the model wrote, a file's name, a page or a mail. A later message takes back what
  an earlier one licensed (dv.withdrawn_skills). Everything goes through the desktop's validator
  (desktop.validate.check_plan) or Ask's (ask.validate.to_cards) as it stands, and their tiers stand: never lowered but
  by the owner's click; only ever raised, where a task's model has read other people's text first and the keyword alone
  would act at once:
  - A change at once comes only from a plain request of the owner's (_pieces): a message of PLAIN_CAP characters or
    fewer that is no answer to the model's own question, with no ':' before it (what follows one is brought in: a
    paste), in a sentence with no ':', no condition word and no '?' but after "can you" and the like, and no question,
    how-to, reported or negating word before it ("how", "she wrote", "can't"), not taken back since, in the same
    message ("mute. no, pause") or a later one ("no", "scratch that").
  - open_thing opens at once only what such a request names, in a clause that opens with an opening word ("open the
    Bramblemoor letter", "find the report, then open https://..."); a paste, a how-to, a question or someone else's
    words naming it wait on the one-click card, and so does anything else. A site the firewall's card asked about first
    has had that click when the owner allowed it (firewall.clicked): it opens then, so one site takes one click.
  - pc_control acts at once only on what the local routes' own parser (and a task's own plainer wordings of the same)
    reads in such a request: as a chain from its start ("mute and pause"), straight after a then, an after that or a
    next, or a whole request with its verb first after "and", "also", a comma or a new sentence ("find the report and
    set the volume to 30"). "the trading volume" is a noun, and a bare keyword after "and" or in a list is none ("find
    the report and mute"). The same kind and the same direction, level, minutes or window, a step no bigger
    (_same_ask), a timer's label only from the request's own, and once for each time they asked: a repeat takes a card.
    A licence with no such words goes on the firewall's card first (firewall.ask_once), which states a volume step
    other than the usual one.
* propose_files is a wait (D5): the card is the desktop's own (frozen plan, ticks, journal, Undo) and its end comes back
  as a message (``card_message``). propose_action is not: its cards wait in Approvals and the task goes on. Nothing here
  claims anything was sent or done.

Threads: a tool's handler runs on its own thread (agent.endpoint). Calendar and mail are read there as Ask reads them
(their objects hold their own locks); files and PC controls go to the "desktop" thread and are waited for within the
tool's time; what shows goes through the GUI thread (``things``). Past the time, or on a Stop, a job's stop is set and
it does nothing more.

Logs: kinds, counts and reason codes only. Live may show results, on screen and in memory only.
"""

from __future__ import annotations

import logging
import ntpath
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta
from datetime import timezone as fixed_zone
from typing import Any, Protocol

from ..ask import desktop_mode
from ..ask.context import (MAIL_NOTE, SOURCE_BRIEFING, SOURCE_CONVERSATION, SOURCE_GUEST, SOURCE_MEMORY, SOURCE_OWNER,
                           ContextIndex, addresses_in, conversation_block, conversation_exchanges, data, day_window,
                           event_row, event_start, index_event, index_thread, memory_block, memory_notes_shown,
                           person_row, recipients_named, thread_text)
from ..ask.mail import (CUT_MARK, MAX_THREADS, MailThread, QueryRefused, check_query, search_words, thread_from_api,
                        trim_threads)
from ..ask.validate import LINE_CARD, to_cards
from ..desktop import executor as dx
from ..desktop import cardplan, guard, journal, pcctl, tidy
from ..desktop import routes as desktop_routes
from ..desktop import service as desktop_service
from ..desktop import validate as dv
from ..desktop.context import FileItem, Place, SiteItem
from ..desktop.refs import APP, FILE, PLACE, SITE, RefIndex, parse_ref
from ..gcal import zone_offset
from . import firewall as agent_firewall
from . import web
from .tools import ERROR_CODES, ToolResult, _page_flags, error, untrusted

logger = logging.getLogger(__name__)

# Caps per task (and per call): plain errors past them.
MAIL_READ_CAP = 10
MAIL_SEARCH_CAP = 10
FILE_READ_CAP = 30
FILE_PART_CHARS = 12_000
FIND_ROWS = 60
LIST_ROWS = 150
PLACE_ROWS = 40
CALENDAR_ROWS = 80
CALENDAR_SPAN_DAYS = 400
CONTACT_ROWS = 20
CONVERSATION_EXCHANGES = 4            # "the last few turns" of the Ask conversation
FILE_CARDS_CAP = 5                    # propose_files' cards
ACTION_CARDS_CAP = 8                  # propose_action's cards
OPENS_CAP, URLS_CAP = dv.MAX_OPENS, dv.MAX_URLS
PC_CHANGES_CAP = 10
WAIT_STEP_S = 0.02

# Result codes (the logs' and Live's: fixed words).
OFF, CAP, UNKNOWN, MAIL_REFUSED, MAIL_FAILED, UNREADABLE, DESK_GONE, NOT_LICENSED, CARD_BUSY = (
    "off", "cap", "unknown", "mail_refused", "mail_failed", "unreadable", "desk_gone", "not_licensed", "card_busy")
WAITING = agent_firewall.WAITING      # Live's WAITING: the owner has it on a card
# How a file card ended (DesktopController.tidyEnded).
CARD_CARRIED, CARD_NOT_NOW, CARD_EXPIRED, CARD_DROPPED = (tidy.ENDED_CARRIED, tidy.ENDED_NOT_NOW, tidy.ENDED_EXPIRED,
                                                          tidy.ENDED_DROPPED)

OFF_TEXT = "The owner has turned this off, so that was not done. Say so in your report."
CAP_TEXT = "That is the most {what} for one task ({cap}). Use what you have, and say so in your report if it matters."
DESK_GONE_TEXT = "Jarvis can't reach this PC's files right now. Say so in your report."
NOT_ASKED = "The owner didn't ask for that: their own words in this task don't ask for it"
# What Jarvis tells the model of a refused step (its words for the owner are desktop.validate's).
REASONS = {dv.UNKNOWN_REF: "it names something Jarvis has not listed in this task (files_find or files_list list it)",
           dv.SHAPE: "it isn't a step Jarvis can carry out as written",
           dv.PATH_OUTSIDE: "it is outside the folders the owner allows",
           dv.EXCLUDED: "it is in a place Jarvis never touches",
           dv.BLOCKED_TYPE: "Jarvis never opens that type of file",
           dv.APP_NOT_APPROVED: "it is an app Jarvis never opens",
           dv.SITE_NOT_APPROVED: "it isn't an ordinary https web address",
           dv.INTENT_NOT_LICENSED: NOT_ASKED,
           dv.SCOPE: "it is outside the places the owner named",
           dv.SUSPECT_ITEM: "its name reads like instructions and the owner didn't name it",
           dv.NAME_INVALID: "that new name isn't allowed",
           dv.NOT_YET: "Jarvis can't do that yet",
           dv.CAP: "it is more than one request may do at once",
           dv.CROSS_VOLUME: "it would go to another drive, and Jarvis moves files within one drive only"}
RETRY_NOTE = "Do not try it again another way: carry on without it, or say so in your report."
PLAN_REFUSED = "That plan was refused, so no card was made. {reasons} " + RETRY_NOTE
NOT_READ_PLAN = "Jarvis could not read that plan: each step needs its skill and the fields of that skill."
NO_ROWS_TEXT = "No card was made: nothing of that can go as it stands. {reasons} " + RETRY_NOTE
CARD_WAIT_TEXT = ("It is on a card for the owner now, and nothing has changed: {rows}. End your turn now with one "
                  "short sentence. What they decide comes to you as your next message.")
ANOTHER_CARD_TEXT = ("A card waits for the owner already, so no second one was made. End your turn now; what they "
                     "decide comes as your next message.")
CARD_NOT_SHOWN_TEXT = ("The card could not be shown just now: another card waits for the owner, or this PC is busy with "
                       "another change. Try once more later, or say so in your report.")
ACTION_WAIT_TEXT = ("{count} on a card for the owner's OK, in Approvals and on the HUD. Nothing has been sent or "
                    "changed, and nothing will be unless the owner clicks. Do not wait for it: carry on, and say in "
                    "your report what is waiting.")
ACTION_NONE_TEXT = "No card was made. "
LINE_REFUSED_TEXT = "Line {number} was refused: {reason}."
UNSEEN_LINK_TEXT = "its link is not an address seen in this task"
OPENED_TEXT = "It is open on the owner's PC now, and its card says so."
OPEN_WAIT_TEXT = ("It has not opened: it waits on a card for the owner's click, which you need not wait for. Say in "
                  "your report that it is waiting.")
OPEN_NOT_DONE_TEXT = "It did not open: {reason}. " + RETRY_NOTE
OPEN_ONE_TEXT = 'Give either "ref" or "site", one of them.'
NOT_A_REF_TEXT = "That is not something Jarvis listed in this task. files_find or files_list list what you can name."
WORKSPACE_TEXT = "That is in this task's own folder or another's: Jarvis never hands those to the owner's other apps."
PC_REFUSED_TEXT = NOT_ASKED + ". " + RETRY_NOTE
PC_SHAPE_TEXT = "That isn't something the PC's controls can do as written."
PC_CARD_TEXT = "Jarvis wants to {what}"
PC_NOT_DONE_TEXT = "It was not done: {reason}."
MAIL_NOT_RUN_TEXT = ("That search was not run: {reason}. Mail is searched only with the owner's own words from this "
                     "task. " + RETRY_NOTE)
UNKNOWN_THREAD_TEXT = ("Jarvis reads only a thread a mail_search of this task found. Search first, with the owner's "
                       "words.")
MAIL_FAILED_TEXT = "Jarvis couldn't read that mail right now. Try once more later, or say so in your report."
SECRET_NOTE = "{count} left out: what looks like a key or a password"     # Live's: never a word of it
SECRET_TOLD = ("(Jarvis took out {count} here, marked {mark}: what looks like a key or a password, never read. The "
               "rest is as written. It is not missing, and the owner need not give it to you: carry on, and if it "
               "matters say in your report that it was left out.)")
NO_MAIL_TEXT = "No mail is connected for reading. Say so in your report."
NOTHING_FOUND_TEXT = "Nothing matched."
BAD_DAY_TEXT = '"from" and "to" must be days written YYYY-MM-DD, "from" first, at most {days} days apart.'
READ_WORDS = {desktop_service.READ_TYPE:
              "Jarvis can't read that type of file yet: only plain text-like files ({types})",
              desktop_service.READ_ONLINE: "it is online-only, and reading it would download it",
              desktop_service.READ_SECRET: "its name or its folder's says secrets or sign-ins, and Jarvis never reads "
                                           "those",
              desktop_service.READ_BINARY: "it doesn't read as text",
              desktop_service.READ_STOPPED: "the task was stopped",
              desktop_service.READ_UNAVAILABLE: "Jarvis can't reach this PC's files right now",
              desktop_service.READ_FAILED: "Windows couldn't read it",
              dx.CHANGED: "it changed since it was listed: look for it again",
              dv.PATH_OUTSIDE: "it is outside the folders the owner allows",
              dv.EXCLUDED: "it is in a place Jarvis never touches"}
READ_NOT_DONE_TEXT = "That was not read: {reason}."
READ_MORE_NOTE = "(There is more: call file_read with this ref and offset {offset}.)"
READ_CUT_NOTE = "(Jarvis reads only the first 8 MB of a file.)"
READ_END_TEXT = "That was all of this file."
# How the owner's words are read for a change at once, a PC control or an open (_pieces): clause by clause, a PC control
# through the local routes' own parser (desktop.routes.pc_request, a full match), the words around a request and a
# task's own plainer wordings aside, an open as a clause that opens with an opening word. A run of marks is tried once,
# not from each of its marks: this runs on whatever was pasted, and re holds the GIL throughout.
_CLAUSE_RE = re.compile(r"(?<![.,;:!?])[.,;:!?]+(?:\s+|$)|\s+(?:and then|and|then|but|also|after that|afterwards)\s+",
                        re.IGNORECASE)
CLAUSE_CAP = 200                      # no PC request is this long: a longer clause is none, and is not read
ASKS_CAP = 8                          # a message's requests that are read: no one asks for more at once
PLAIN_CAP = 300                       # a longer message asks for nothing at once: a paste, whatever it says (pc-2)
_THEN_BREAKS = frozenset({"and then", "then", "after that", "afterwards"})
_THEN_RE = re.compile(r"(?:and\s+)?(?:then|after\s+that|afterwards)(?:[,:]?\s+|$)|next\s+", re.IGNORECASE)
_NO_WORDS = r"jarvis|please|kindly|now|just|ok|okay|hey|hi|yes|yeah|right|sure|well|actually|thanks|thank you"
_NO_CLAUSE_RE = re.compile(rf"(?:{_NO_WORDS})(?:\s+(?:{_NO_WORDS}))*", re.IGNORECASE)  # said around a request, between
#                                                                     commas, no clause of its own ("hey jarvis, mute")
_CONDITION_WORDS = frozenset({"if", "when", "whenever", "while", "until", "unless", "once", "whether"})
# pc-2: a word before a piece in its sentence that makes it no request of theirs: a question, a how-to, someone else's
# words, a supposition, a negation dv.negated does not know ("can't": "can" and "t").
_VOID_WORDS = frozenset({"how", "what", "why", "should", "explain", "suppose", "imagine", "said", "wrote", "told",
                         "says", "steps", "manual", "guide", "couldn", "wouldn", "isn", "cannot", "cant", "couldnt",
                         "wouldnt", "isnt"})
_POLITE_RE = re.compile(r"(?:(?:jarvis|hey|hi|ok|okay|please)[,:]?\s+)*(?:can|could|would|will)\s+you\b", re.IGNORECASE)
# A whole plain request, its verb first with its object ("set the volume to 30", "pause the music", "take a
# screenshot"): it counts after "and", "also", a comma or a new sentence too (_WHOLE_AFTER), where a bare keyword does
# not ("find the report and mute").
_REQUEST_VERBS = frozenset({"set", "turn", "put", "change", "lower", "raise", "make", "mute", "unmute", "pause", "play",
                            "resume", "skip", "stop", "start", "take", "cancel", "end", "go", "switch", "remind"})
_WHOLE_AFTER = frozenset({"and", "also"})
_CORRECTIONS = frozenset({"no", "not", "actually"})     # pc-6: a clause opening with one takes back the piece before it
_OPENING_WORDS = next(licence.phrases for licence in dv.LICENCES if dv.OPEN_URL in licence.skills)
_OPEN_KINDS = dv.OPEN_SKILLS | {dv.FIND_FILES}
_LEAD_IN_RE = re.compile(r"(?:(?:jarvis|please|kindly|now|just|first|and then|and|then|also|but|could you|can you|"
                         r"would you|will you)[,:]?\s+)+", re.IGNORECASE)
_TAIL_RE = re.compile(r"(?:\s+(?:please|now|a bit|a little|for me|right now|straight away|again))+$", re.IGNORECASE)
_LABEL_RE = re.compile(r"\b(?:minutes?|mins?|hour)(?:\s+timer)?\s+(\S.*)$", re.IGNORECASE)     # a timer's label, as said
# A whole later message that says no, as guard.words reads it: it takes back the last message's asks.
_SAYS_NO_WORDS = (r"no|nope|wait|don t|dont|do not|not that(?: one)?|not this one|not yet|not now|no need|hold on|hang on"
                  r"|on second thoughts?|(?:i (?:ve |have )?)?changed my mind|scratch that|scratch it")
_SAYS_NO_RE = re.compile(rf"(?:(?:actually|sorry|oh|please|hold on|hang on) )*(?:{_SAYS_NO_WORDS})"
                         rf"(?: (?:{_SAYS_NO_WORDS}|actually|sorry|thanks|please))*")
SAYS_NO_CAP = 40
# A task's own wordings of what the local routes know, each plainly that request and a full match: routes._PC stays as it
# is, for the app with no task.
_WORDINGS = tuple((re.compile(pattern, re.IGNORECASE), form) for pattern, form in (
    (r"turn\s+(?:the\s+)?sound\s+(up|down)", r"volume \1"),
    (r"turn\s+(?:the\s+)?volume\s+(up|down)", r"volume \1"),
    (r"turn\s+(up|down)\s+(?:the\s+)?(?:volume|sound)", r"volume \1"),
    (r"lower\s+(?:the\s+)?(?:volume|sound)", "volume down"),
    (r"raise\s+(?:the\s+)?(?:volume|sound)", "volume up"),
    (r"make\s+it\s+(louder|quieter)", r"\1"),
    (r"mute\s+(?:it|the\s+sound|the\s+volume)", "mute"),
    (r"unmute\s+(?:it|the\s+sound|the\s+volume)", "unmute"),
    (r"(?:set|put|change|turn)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?", r"volume \1"),
    (r"set\s+(?:the\s+)?(?:volume|sound)\s+(\d{1,3})\s*(?:%|percent)?", r"volume \1"),
    (r"(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?", r"volume \1"),
    (r"(?:volume|sound)\s+(\d{1,3})\s*percent", r"volume \1"),
    (r"stop\s+(?:the\s+)?(?:music|song|track|video)", "pause"),
    (r"play\s+some\s+music", "play"),
    (r"skip\s+this\s+(?:song|track|video)", "next"),
    (r"go\s+back\s+a\s+(?:song|track)", "previous"),
    (r"(?:(?:set|start)\s+)?(?:a\s+)?(\d{1,3})[\s-]*(?:minute|min)\s+timer(?:\s+(\S.*))?", r"timer \1 minutes \2"),
    (r"(?:set|start)\s+timer\s+(?:for\s+)?(\d{1,3})\s*(?:minutes?|mins?)(?:\s+(\S.*))?", r"timer \1 minutes \2"),
    (r"(?:(?:set|start)\s+)?(?:a\s+)?timer\s+for\s+an?\s+hour(?:\s+(\S.*))?", r"timer 60 minutes \1"),
    (r"(?:(?:set|start)\s+)?(?:a\s+)?timer\s+for\s+half\s+an\s+hour(?:\s+(\S.*))?", r"timer 30 minutes \1"),
    (r"(?:turn|switch)\s+on\s+(focus(?:\s+mode)?\s+for\s+\S.*)", r"\1")))

# A file card's end, as the model hears it (card_message): done N, failed M, not now, timed out.
CARD_DONE_TEXT = "Jarvis: the owner clicked the card. {what}."
CARD_REFUSED_TEXT = ("Jarvis: the owner clicked the card, but its plan had expired or changed, so nothing was changed. "
                     "Carry on without it, or say so in your report.")
CARD_NOT_NOW_TEXT = ("Jarvis: the owner chose Not now on the card, so nothing was changed. Do not propose it again "
                     "or try it another way: carry on without it, or say so in your report.")
CARD_EXPIRED_TEXT = ("Jarvis: the card timed out with no click, so nothing was changed. Do not propose it again: "
                     "carry on without it, or say so in your report.")
CARD_DROPPED_TEXT = ("Jarvis: the card was taken away before any click, so nothing was changed. Carry on without it, "
                     "or say so in your report.")
CARD_HOW_DONE = "You clicked it: "     # Live's words for how a file card went (card_how)


def card_message(state: Any, ending: str) -> str:
    """What the model hears when a file card ended (DesktopController.tidyEnded on the GUI thread): counts in plain
    words; never a name."""
    if ending == CARD_NOT_NOW:
        return CARD_NOT_NOW_TEXT
    if ending == CARD_EXPIRED:
        return CARD_EXPIRED_TEXT
    if ending != CARD_CARRIED:
        return CARD_DROPPED_TEXT
    outcome = getattr(state, "outcome", None)
    if outcome is None or getattr(outcome, "code", ""):
        return CARD_REFUSED_TEXT
    return CARD_DONE_TEXT.format(what=_card_counts(state, outcome))


def _card_counts(state: Any, outcome: Any) -> str:
    done = len(state.done_numbers())
    chosen = len(state.chosen)
    counts = outcome.counts()
    failed = counts.get(journal.FAILED, 0)
    left = max(0, chosen - done - failed)
    parts = [f"Done {done} of {chosen}"]
    if failed:
        parts.append(f"{failed} failed")
    if left:
        parts.append(f"{left} left as they were")
    if state.stopped():
        parts.append("the owner stopped it part-way")
    return ", ".join(parts)


def card_how(state: Any, ending: str) -> str:
    """Live's words for how a file card went, for the owner to read: counts only; never a name."""
    if ending == CARD_NOT_NOW:
        return "You chose Not now, so nothing was changed"
    if ending == CARD_EXPIRED:
        return "No click came, so nothing was changed"
    if ending != CARD_CARRIED:
        return "It was taken away, so nothing was changed"
    outcome = getattr(state, "outcome", None)
    if outcome is None or getattr(outcome, "code", ""):
        return "You clicked it, but its plan had expired or changed, so nothing was changed"
    return CARD_HOW_DONE + _card_counts(state, outcome)


class Things(Protocol):
    """What the app gives a task of the owner's things (agent_ui.AppThings; the tests' fakes)."""

    config: Any
    sources: Any

    def consent(self) -> bool: ...                # version 3 of the card, asked at every call (any thread)
    def now(self) -> datetime: ...
    def memory(self) -> Sequence[str]: ...        # the owner's notes (any thread)
    def conversation(self) -> Sequence[Any]: ...  # conversation.Turn, oldest first (it goes to the GUI thread itself)
    def briefing(self) -> Any: ...                # ask.context.BriefingContext or None
    def job(self, method: str, *args: Any, stop: threading.Event, **kwargs: Any) -> Future: ...   # the desktop thread
    def before_open(self) -> None: ...            # GUI: what the window an open brings is not one of
    # GUI: its cards show; False: they can't now, or ``cancelled()`` says the run was stopped on the way to the GUI
    # thread
    def show_run(self, run: Any, cancelled: Callable[[], bool] = lambda: False) -> bool: ...
    def hud_windows(self) -> Sequence[int]: ...   # GUI: left out of a screenshot
    def pc_done(self, asked: Any, run: Any) -> None: ...      # GUI: chips and Undo
    # GUI: Ask's cards, in Approvals and on the HUD; False, as show_run
    def take_cards(self, cards: Sequence[Any], cancelled: Callable[[], bool] = lambda: False) -> bool: ...


def _day(text: Any) -> date | None:
    try:
        return date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None


def _midnight(day: date, zone: str) -> datetime:
    """As planner._day_bounds: midnight of ``day`` in the IANA ``zone``; this PC's own rules when it can't be known."""
    probe = datetime.combine(day, clock_time(12)).replace(tzinfo=fixed_zone.utc)
    offset = zone_offset(zone, probe)
    wall = datetime.combine(day, clock_time())
    return wall.replace(tzinfo=fixed_zone(offset)) if offset is not None else wall.astimezone()


def _people(thread: MailThread) -> list[str]:
    """What a thread's headers name: its subject, and each person's name and address."""
    found = [thread.subject]
    for message in thread.messages:
        found.append(message.subject)
        for person in message.people():
            found += [person.name, person.email]
    return found


def _brief_people(brief: Any) -> list[str]:
    found = [brief.title, getattr(brief, "organizer_name", ""), getattr(brief, "organizer_email", "")]
    for guest in brief.guests:
        found += [guest.name, guest.email]
    return found


def _count_of(count: int, one: str, many: str = "") -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _left_out(count: int) -> tuple[str, tuple[tuple[str, str], ...]]:
    """What a result says of the values taken out of it: (the words for the model, the field for Live). No word of
    them."""
    if not count:
        return "", ()
    values = _count_of(count, "value")
    return (SECRET_TOLD.format(count=values, mark=guard.SECRET_MARK), (("Left out", SECRET_NOTE.format(count=values)),))


def _unmarked(text: str) -> str:
    """``text`` without Jarvis's own marks: no word of the owner's, for the taint set or for a line to repeat."""
    return text.replace(guard.SECRET_MARK, " ")


def _marks_in(texts: Iterable[Any]) -> int:
    return sum(text.count(guard.SECRET_MARK) for text in texts if isinstance(text, str))


def _subjects_scrubbed(thread: MailThread) -> tuple[MailThread, int]:
    """The thread with its subjects as guard.scrub_secrets gives them, and how many values the one it shows held."""
    subject, count = guard.scrub_secrets(thread.subject)
    messages = tuple(replace(message, subject=guard.scrub_secrets(message.subject)[0]) for message in thread.messages)
    return replace(thread, subject=subject, messages=messages), count


def _kept_text(whole: str, kept: str) -> tuple[str, int]:
    """A message's text as trim_threads kept it (``kept``), from the text as it came (``whole``), its values taken out.
    One it cut is looked at past the cut as well (service.SECRET_MARGIN), then cut in the same place: no part of a value
    stays, and what stands far beyond it, which can be 200,000 characters, is never read."""
    if whole == kept or not kept.endswith(CUT_MARK):
        return guard.scrub_secrets(kept)
    keep = len(kept) - len(CUT_MARK)
    if whole[:keep] != kept[:keep]:
        return guard.scrub_secrets(kept)
    shown, count = guard.scrub_part(whole, 0, keep, desktop_service.SECRET_MARGIN)
    return shown + CUT_MARK, count


def _without_secrets(thread: MailThread, fit: MailThread) -> tuple[MailThread, int]:
    """``fit`` (trim_threads' of ``thread``) with what plainly is a key or a password taken out of its subject and its
    messages' text. How many values went."""
    scrubbed, count = _subjects_scrubbed(fit)
    wholes = {message.gmail_id: message.text for message in thread.messages}
    messages = []
    for message in scrubbed.messages:
        text, found = _kept_text(wholes.get(message.gmail_id, message.text), message.text)
        messages.append(replace(message, text=text))
        count += found
    return replace(scrubbed, messages=tuple(messages)), count


def _scrubbed_turn(turn: Any) -> Any:
    text, found = guard.scrub_secrets(getattr(turn, "text", ""))
    return replace(turn, text=text) if found else turn


def _plain(clause: str) -> str:
    """A clause with the words around a request taken off: "please", "can you", "and then" before it, "now", "for me"
    after it."""
    lead = _LEAD_IN_RE.match(clause)
    return _TAIL_RE.sub("", clause[lead.end():] if lead is not None else clause)


def _clause_ask(clause: str) -> pcctl.PcAsk | None:
    """What one clause asks for, as the local routes' own parser reads it, the words around a request aside. A timer
    with a label counts only when its label says what it is for, with no negation ("for tea", "to stretch"): "5 minute
    timer apps" and "timer for 5 minutes isn't long enough" are no request (pc-4; routes._PC stays as it is)."""
    plain = _plain(clause)
    asked = None
    for pattern, form in _WORDINGS:
        match = pattern.fullmatch(plain)
        if match is not None:
            asked = desktop_routes.pc_request(match.expand(form))
            break
    else:
        asked = desktop_routes.pc_request(plain)
    if asked is not None and asked.label:
        said = _LABEL_RE.search(plain)
        label = said.group(1).casefold() if said is not None else ""
        if not label.startswith(("for ", "to ")) or dv.negated(label):
            return None
    return asked


def _whole_request(clause: str) -> bool:
    """The clause is a whole plain request, its verb first with its object ("set the volume to 30", "pause the
    music"), not a bare keyword ("mute", "volume up")."""
    words = _plain(clause).casefold().split()
    return len(words) >= 2 and words[0] in _REQUEST_VERBS


def _opening(clause: str) -> bool:
    """The clause opens with an opening word ("open", "show", "go to" ...: the validator's own), the words around a
    request aside: what it names is what the owner asked to open."""
    words = guard.words(_plain(clause), camel=False)
    return any(tuple(words[:len(phrase)]) == phrase for phrase in _OPENING_WORDS)


def _clauses(text: str) -> list[tuple[int, int, str]]:
    """Where each clause of ``text`` starts and ends, and the break before it (no spaces, lower case)."""
    found: list[tuple[int, int, str]] = []
    at, before = 0, ""
    for cut in _CLAUSE_RE.finditer(text):
        found.append((at, cut.start(), before))
        at, before = cut.end(), cut.group().strip().casefold()
    found.append((at, len(text), before))
    return found


def _no_clause(clause: str) -> bool:
    """Nothing of a request or against one: empty, or only what is said around a request ("Jarvis, mute, please",
    "hey jarvis, mute")."""
    return not clause or (len(clause) <= CLAUSE_CAP and _NO_CLAUSE_RE.fullmatch(clause) is not None)


def _opens_with_next(text: str, clauses: Sequence[tuple[int, int, str]], number: int, sentence: int) -> bool:
    """That clause is a bare "next" that opens its sentence with more to come ("Next, mute"), so a sequencing word.
    Never in a list ("play, pause, next, previous"), and never by itself ("next"): that is the request."""
    start, end, _before = clauses[number]
    if start != sentence or text[start:end].strip().casefold() != "next":
        return False
    for index in range(number + 1, len(clauses)):
        later, stop, _break = clauses[index]
        if not _no_clause(text[later:stop].strip()):
            return True
    return False


def _sentences_void(text: str, clauses: Sequence[tuple[int, int, str]]) -> list[tuple[int, bool]]:
    """For each clause: where its sentence starts in ``text``, and whether the whole sentence says it is no request
    (pc-2, pc-3): a ':' anywhere in it ("summarise this: ... then mute", "Resume and cover letter: proofread them"), a
    condition word anywhere in it ("Mute, if it gets loud") or a '?' ("Mute? What does that do?") unless it opens with
    can you, could you, would you or will you ("can you mute?")."""
    found: list[tuple[int, bool]] = []
    first = 0
    for number in range(len(clauses) + 1):
        if number < len(clauses) and (number == 0 or not any(mark in clauses[number][2] for mark in ".!?")):
            continue
        if number > first:                    # clauses[first:number] are one sentence; clauses[number]'s break ends it
            begin, finish = clauses[first][0], clauses[number - 1][1]
            breaks = [clauses[index][2] for index in range(first + 1, min(number + 1, len(clauses)))]
            words = text[begin:finish]
            void = (any(":" in mark for mark in breaks)
                    or not _CONDITION_WORDS.isdisjoint(guard.words(words, camel=False))
                    or (any("?" in mark for mark in breaks) and _POLITE_RE.match(words) is None))
            found += [(begin, void)] * (number - first)
        first = number
    return found


def _void_before(words: str) -> bool:
    """A negation (dv.negated) or a word of _VOID_WORDS stands in ``words``, what comes before a piece in its sentence:
    "don't mute, then pause", "how do I open ...", "she wrote ... then set the volume to 100", "I can't mute"."""
    if dv.negated(words):
        return True
    tokens = guard.words(words, camel=False)
    return not _VOID_WORDS.isdisjoint(tokens) or any(word == "can" and after == "t"
                                                     for word, after in zip(tokens, tokens[1:]))


@dataclass(frozen=True)
class _Piece:
    """A plain request of one message: a PC control (``asked``), or an open (``asked`` None: ``words`` is its clause,
    which names what opens); where its clause starts and ends, and its number."""

    asked: pcctl.PcAsk | None
    words: str
    start: int
    end: int
    number: int


def _pieces(message: str) -> list[_Piece]:
    """The plain requests of one of the owner's messages, as a change at once may be read from them. Nothing in a
    message longer than PLAIN_CAP (a paste, whatever it says), nothing after a ':' anywhere before it in the message
    (what it brings in: "Summarise this mail for me: Hi Sam ... Please open ..."), and nothing in a sentence that says
    it is no request (_sentences_void) or after a word that says so (_void_before). An open: a clause that opens with
    an opening word (_opening), wherever it stands ("find the report, then open https://..."). A PC control: as a chain
    from the message's start, every clause before it a whole request too ("mute and pause"); straight after a
    sequencing word ("find the report, then mute"); or, when it is a whole plain request, its verb first with its
    object, after "and", "also", a comma or a new sentence too ("find the report and set the volume to 30"). So a
    keyword in a list asks for nothing ("compare the play, pause and next buttons", "find the report and mute"). What
    the message goes on to say may take one back (_kept). Bounded: each clause is read once, none past CLAUSE_CAP, at
    most ASKS_CAP are found."""
    text = " ".join((message or "").split())
    if not text or len(text) > PLAIN_CAP:
        return []
    clauses = _clauses(text)
    voids = _sentences_void(text, clauses)
    found: list[_Piece] = []
    chain, then, pasted = True, False, False
    for number, (start, end, before) in enumerate(clauses):
        sentence, void = voids[number]
        pasted = pasted or ":" in before      # what follows a ':' is what it brings in, to the message's end
        void = void or pasted
        then = then or before in _THEN_BREAKS
        clause = text[start:end].strip()
        if _no_clause(clause):
            continue
        if len(clause) > CLAUSE_CAP:
            chain = then = False
            continue
        if _opens_with_next(text, clauses, number, sentence):
            then = True
            continue
        word = _THEN_RE.match(clause)
        if word is not None and word.group()[0] in "nN" and _clause_ask(clause) is not None:
            word = None                       # "next song": the request itself
        if word is not None:
            then, clause = True, clause[word.end():]
            if _no_clause(clause):            # on its own: "then, mute"
                continue
        void = void or _void_before(text[sentence:start])
        asked = _clause_ask(clause)
        if asked is None:
            if not void and _opening(clause):
                found.append(_Piece(None, clause, start, end, number))
            chain = then = False
        else:
            whole = (before in _WHOLE_AFTER or any(mark in before for mark in ",.!?")) and _whole_request(clause)
            if (chain or then or whole) and not void:
                found.append(_Piece(asked, "", start, end, number))
            then = False
        if len(found) >= ASKS_CAP:
            break
    return _kept(text, clauses, found)


def _kept(text: str, clauses: Sequence[tuple[int, int, str]], found: Sequence[_Piece]) -> list[_Piece]:
    """``found`` without what the message goes on to take back: what it names after a piece (dv.taken_back: "pause,
    never mind", "mute and pause. Actually, don't touch the music"), a whole no after it ("mute. no, actually don't",
    "mute. hold on"), or a correction: the clause after it opens with no, not or actually ("mute. no, pause", "set the
    volume to 50, no, 30"; pc-6). The text after each is read once, the last first."""
    kept: list[_Piece] = []
    gone: frozenset[str] = frozenset()
    stop = len(text)
    for index in range(len(found) - 1, -1, -1):
        piece = found[index]
        gone |= dv.taken_back(text[piece.end:stop], task=True)
        stop = piece.end
        upto = found[index + 1].start if index + 1 < len(found) else len(text)
        after = next((text[start:end].strip() for start, end, _break in clauses[piece.number + 1:]
                      if text[start:end].strip()), "")
        first = guard.words(after, camel=False)[:1]
        if (piece.asked.kind in gone if piece.asked is not None else not gone.isdisjoint(_OPEN_KINDS)) \
                or _says_no(text[piece.end:upto]) or (first and first[0] in _CORRECTIONS):
            continue
        kept.insert(0, piece)
    return kept


def _pc_asks(message: str) -> list[pcctl.PcAsk]:
    """The PC controls one of the owner's messages asks for at once (_pieces)."""
    return [piece.asked for piece in _pieces(message) if piece.asked is not None]


def _says_no(message: str) -> bool:
    """A whole message of no: no wait, don't, not that, actually no, hold on, not yet, I changed my mind ... (pc-7). It
    takes back the last message's asks."""
    if len(message or "") > SAYS_NO_CAP:
        return False
    return _SAYS_NO_RE.fullmatch(" ".join(guard.words(message, camel=False))) is not None


def _wanted(heard: Sequence[tuple[str, bool]]) -> tuple[list[tuple[tuple[int, int], pcctl.PcAsk]], list[str]]:
    """(the PC controls the owner's messages ask for at once, each keyed by (its message, its place there); the
    clauses of the opens they ask for at once), less what was taken back since: by naming it (dv.taken_back, a task's
    reading), or by a whole message of no, which takes back what the last message that asked for any asked. A message
    that answered the model's own question asks for nothing at once (pc-8: the question was the model's); it may still
    take back."""
    asks: list[tuple[tuple[int, int], pcctl.PcAsk]] = []
    opens: list[tuple[int, str]] = []
    last = -1
    for number, (message, answered) in enumerate(heard):
        if number:
            gone = dv.taken_back(message, task=True)
            asks = [(key, asked) for key, asked in asks if asked.kind not in gone]
            if not gone.isdisjoint(_OPEN_KINDS):
                opens = []
            if _says_no(message):
                asks = [(key, asked) for key, asked in asks if key[0] != last]
                opens = [(said, words) for said, words in opens if said != last]
                last = -1
        pieces = [] if answered else _pieces(message)
        for place, piece in enumerate(pieces):
            if piece.asked is not None:
                asks.append(((number, place), piece.asked))
            else:
                opens.append((number, piece.words))
        last = number if pieces else last
    return asks, [words for _said, words in opens]


def _asked_to_open(step: Any, opens: Sequence[str]) -> bool:
    """One of the owner's opening clauses names what ``step`` opens: its item (dv.names_item) or its address, typed
    word for word (dv.typed_url)."""
    if step.targets:
        label = dv.item_label(step.targets[0])
        return any(dv.names_item(words, label) for words in opens)
    url = getattr(step.planned, "url", "") if step.planned is not None else ""
    return bool(url) and any(dv.typed_url(words, url) for words in opens)


def _same_ask(asked: pcctl.PcAsk, wanted: pcctl.PcAsk) -> bool:
    """What the model asks for is what the owner's words ask for: the same kind and, as those words have it, the same
    direction and level or a step no bigger (the routes' own), the same minutes, the same window. Anything else goes on
    a card."""
    if asked.kind != wanted.kind:
        return False
    if asked.kind in (dv.TIMER, dv.FOCUS_MODE):
        return asked.minutes == wanted.minutes
    if asked.kind not in (dv.VOLUME, dv.MEDIA, dv.SCREENSHOT):
        return True
    if asked.action != wanted.action:
        return False
    if asked.action == "set":
        return asked.level == wanted.level
    return asked.step is None or wanted.step is None or asked.step <= wanted.step


def _label_within(asked: pcctl.PcAsk, wanted: pcctl.PcAsk) -> pcctl.PcAsk:
    """At once, a timer keeps the model's label only when its words all stand in the label the request itself gave
    ("a 5 minute timer for tea": "tea"); else it goes with none, for only its end's toast would show it (pc-14)."""
    if asked.kind != dv.TIMER or not asked.label:
        return asked
    theirs = set(agent_firewall.call_words(wanted.label))
    words = agent_firewall.call_words(asked.label)
    return asked if words and all(word in theirs for word in words) else replace(asked, label="")


def _card_what(asked: pcctl.PcAsk) -> str:
    """The firewall's card's words for a PC control: the local routes' own, with a volume step other than theirs
    ("turn the volume up by 30"; pc-13). A timer's label is never on it."""
    what = desktop_routes.pc_what(asked)
    if asked.kind == dv.VOLUME and asked.action in ("up", "down") and asked.step not in (None, pcctl.VOLUME_STEP):
        what = f"{what} by {asked.step}"
    return what


class OwnerAccess:
    """See the module docs. Thread-safe: tool calls may come side by side; its lock is never held across a read, a job
    or a hop to the GUI thread."""

    def __init__(self, things: Things, firewall: agent_firewall.WebFirewall, *, run_id: str = "",
                 workspace: Any = None) -> None:
        self.things = things
        self.firewall = firewall
        self.run_id = run_id
        self._workspace = ntpath.normcase(str(workspace)).rstrip("\\") if workspace else ""
        self._lock = threading.RLock()
        self._refs = RefIndex(run_id)
        self._index = ContextIndex()
        self._threads: dict[str, MailThread] = {}
        self._counts: dict[str, int] = {}
        self._people: dict[str, tuple[str, str]] | None = None  # the people the calendar and the briefing name
        self._spent: set[tuple[int, int]] = set()  # the owner's asks a PC control has used at once (pc-5: _wanted's keys)
        self.card_ids: list[str] = []          # propose_action's cards (withdrawn on a Stop; decided ones stay)
        self.run_ids: list[str] = []           # open_thing's and propose_files' runs (their result cards)
        self.waiting_card = ""                 # the file card a wait hangs on (GUI thread's business)

    def __repr__(self) -> str:                 # counts only
        return f"OwnerAccess({len(self._refs)} refs, {len(self._threads)} threads, {self._counts})"

    # ---- what every call goes through --------------------------------------------------------------------------

    def _refused_first(self, ctx: Any) -> ToolResult | None:
        """Turn off stops them at once, and a stopped run reads nothing."""
        if ctx.cancelled():
            return error(web.STOPPED_TEXT, web.W_STOPPED)
        try:
            if self.things.consent() is True:
                return None
        except Exception as exc:  # noqa: BLE001 - unknown: no
            logger.warning("Agent %s: the owner's OK could not be read (%s)", self.run_id or "-", type(exc).__name__)
        return error(OFF_TEXT, OFF)

    def _taint(self, fragments: Iterable[Any]) -> None:
        """A private tool is answering, whatever this holds: the firewall's checks are on from now on (note_private)."""
        self.firewall.note_private()
        self.firewall.taint.add(_unmarked(text) for text in fragments if isinstance(text, str) and text)

    def _take(self, what: str, cap: int, words: str) -> ToolResult | None:
        """One of ``what`` is used, or none is left: a plain error."""
        with self._lock:
            used = self._counts.get(what, 0)
            if used >= cap:
                return error(CAP_TEXT.format(what=words, cap=cap), CAP)
            self._counts[what] = used + 1
        return None

    def _job(self, ctx: Any, method: str, *args: Any, **kwargs: Any) -> Any:
        """A job on the desktop thread, waited for within the tool's time: a Stop, or past it, sets its stop and raises
        web.WebRefused with Jarvis's own words."""
        stop = threading.Event()
        call = web.Call(ctx.cancelled, ctx.tool_s, time.monotonic)
        future = self.things.job(method, *args, stop=stop, **kwargs)
        while True:
            try:
                return future.result(timeout=WAIT_STEP_S)
            except CancelledError:
                raise web.WebRefused(DESK_GONE, DESK_GONE_TEXT) from None
            except FutureTimeout:
                try:
                    call.check()
                except web.WebRefused:
                    stop.set()
                    raise

    def _sync(self) -> str:
        """The index, with the owner's words as they stand: what a mail search and a line may name. (Lock held.)"""
        said = self.firewall.said()
        self._index.command = said
        for found in addresses_in(said):
            self._index.add_address(found, SOURCE_OWNER)
        return said

    def _accounts(self) -> tuple[Any, ...]:
        accounts = tuple(self.things.sources.accounts())
        with self._lock:
            self._index.aliases = frozenset(account.alias for account in accounts)
            self._index.own_addresses = frozenset(account.address.casefold() for account in accounts if account.address)
        return accounts

    # ---- the calendar, people, notes, what was said -------------------------------------------------------------

    def calendar_find(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        first, last = _day(args.get("from")), _day(args.get("to"))
        if first is None or last is None or last < first or (last - first).days > CALENDAR_SPAN_DAYS:
            return error(BAD_DAY_TEXT.format(days=CALENDAR_SPAN_DAYS), "bad_input")
        sources = self.things.sources
        zone = sources.time_zone()
        start, end = _midnight(first, zone), _midnight(last + timedelta(days=1), zone)
        wanted = search_words(str(args.get("words", "")))
        rows: list[tuple[datetime, str, Any]] = []
        notes = []
        for account in self._accounts():
            if account.calendar != "yes":
                continue
            briefs, status = sources.event_briefs(account.alias, start, end, self.things.config.ask.calendars)
            if status != "yes":
                notes.append(f"The {data(account.alias, 24)} calendar could not be read.")
                continue
            for brief in briefs:
                title, found = guard.scrub_secrets(brief.title)
                brief = replace(brief, title=title) if found else brief
                if not wanted or wanted & search_words(" ".join(text for text in _brief_people(brief) if text)):
                    rows.append((event_start(brief), account.alias, brief))
        rows.sort(key=lambda row: (row[0], row[1]))
        shown = rows[:CALENDAR_ROWS]
        self._taint(text for _at, _alias, brief in shown for text in _brief_people(brief))
        with self._lock:
            for _at, alias, brief in shown:
                index_event(self._index, alias, brief)
        lines = [event_row(alias, brief) for _at, alias, brief in shown]
        if len(rows) > len(shown):
            lines.append(f"[{len(rows) - len(shown)} later event(s) left out: ask for fewer days]")
        text = untrusted("the owner's calendar", "\n".join(lines) or "No events there.")
        left = _marks_in(brief.title for _at, _alias, brief in shown)
        told, field = _left_out(left)
        logger.info("Agent %s: calendar read, %d event(s), %d value(s) left out", self.run_id or "-", len(shown), left)
        return ToolResult("\n".join([text, *notes, *([told] if told else [])]), note=_count_of(len(shown), "event"),
                          warn=bool(left), fields=(("Days", f"{first.isoformat()} to {last.isoformat()}"), *field))

    def _known_people(self, ctx: Any) -> dict[str, tuple[str, str]]:
        """The people Ask's <contacts> names: the calendar's guests and organisers, the briefing's recipients. Read once
        a run. Address -> (name, where it came from). The mail read in this run is added at each ask."""
        with self._lock:
            if self._people is not None:
                return self._people
        people: dict[str, tuple[str, str]] = {}
        things = self.things
        ask = things.config.ask
        start, end = day_window(things.now(), ask.days_back, ask.days_ahead)
        for account in self._accounts():
            if account.calendar != "yes" or ctx.cancelled():
                continue
            briefs, status = things.sources.event_briefs(account.alias, start, end, ask.calendars)
            for brief in briefs if status == "yes" else ():
                for guest in brief.guests:
                    if guest.email:
                        people.setdefault(guest.email.casefold(), (guest.name, SOURCE_GUEST))
                if brief.organizer_email and not brief.organizer_self:
                    people.setdefault(brief.organizer_email.casefold(), (brief.organizer_name, SOURCE_GUEST))
        briefing = things.briefing()
        for action in getattr(briefing, "pending", ()) or ():
            for address, name in recipients_named(action).items():
                people.setdefault(address, (name, SOURCE_BRIEFING))
        with self._lock:
            if self._people is None:
                self._people = people
            return self._people

    def contacts_find(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        wanted = {word for word in search_words(str(args["name"])) if len(word) >= 2}
        people = dict(self._known_people(ctx))
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            for message in thread.messages:
                for person in message.people():
                    people.setdefault(person.email.casefold(), (person.name, "mail"))
        found = [(address, name, source) for address, (name, source) in people.items()
                 if any(word in f"{name} {address}".casefold() for word in wanted)]
        found.sort(key=lambda row: (row[1].casefold(), row[0]))
        shown = found[:CONTACT_ROWS]
        self._taint(text for address, name, _source in shown for text in (name, address))
        with self._lock:
            for address, name, source in shown:
                self._index.add_address(address, source, name)
        lines = [person_row(name, address) for address, name, _source in shown]
        logger.info("Agent %s: contacts read, %d found", self.run_id or "-", len(shown))
        return ToolResult(untrusted("people in the owner's calendar, briefing and mail",
                                    "\n".join(lines) or NOTHING_FOUND_TEXT),
                          note=_count_of(len(shown), "person", "people"))

    def memory_read(self, _args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        with self._lock:
            said = self._sync()
        notes = tuple(guard.scrub_secrets(note)[0] for note in self.things.memory() if isinstance(note, str) and note)
        shown = memory_notes_shown(notes, said)
        self._taint(shown)
        with self._lock:
            for note in shown:
                for found in addresses_in(note):
                    self._index.add_address(found, SOURCE_MEMORY)
        left = _marks_in(shown)
        told, field = _left_out(left)
        logger.info("Agent %s: notes read, %d, %d value(s) left out", self.run_id or "-", len(shown), left)
        return ToolResult("\n".join([untrusted("the owner's notes", memory_block(notes, command=said)),
                                     *([told] if told else [])]),
                          note=_count_of(len(shown), "note"), warn=bool(left), fields=field)

    def conversation_read(self, _args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        turns = [_scrubbed_turn(turn) for turn in self.things.conversation()]
        exchanges = list(conversation_exchanges(turns))[-CONVERSATION_EXCHANGES:]
        said = [turn for exchange in exchanges for turn in exchange]
        self.firewall.go_strict()             # before anything of it goes back
        self._taint(getattr(turn, "text", "") for turn in said)
        with self._lock:
            for turn in said:
                for found in addresses_in(data(getattr(turn, "text", ""))):
                    self._index.add_address(found, SOURCE_CONVERSATION)
        left = _marks_in(getattr(turn, "text", "") for turn in said)
        told, field = _left_out(left)
        logger.info("Agent %s: the conversation read, %d turn(s), %d value(s) left out", self.run_id or "-", len(said),
                    left)
        if not said:
            return ToolResult("Nothing was said before this task.", note="no turns")
        zone = self.things.now().tzinfo
        return ToolResult("\n".join([untrusted("what was said earlier", conversation_block(exchanges, zone=zone)),
                                     *([told] if told else [])]),
                          note=_count_of(len(said), "turn"), warn=bool(left), fields=field)

    # ---- mail -----------------------------------------------------------------------------------------------------

    def mail_search(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        words = " ".join(str(args["words"]).split())
        accounts = self._accounts()
        with self._lock:
            self._sync()
            grounding = self._index.search_grounding()
        fields = (("Asked to search mail for", words),)
        try:
            query = check_query(words, grounding)
        except QueryRefused as why:
            logger.info("Agent %s: a mail search was refused", self.run_id or "-")
            reason = str(why)
            return ToolResult(MAIL_NOT_RUN_TEXT.format(reason=reason[:1].lower() + reason[1:]), is_error=True,
                              code=MAIL_REFUSED, fields=fields)
        readable = [account.alias for account in accounts if account.read_mail == "yes"]
        if not readable:
            return error(NO_MAIL_TEXT, MAIL_FAILED)
        over = self._take("mail searches", MAIL_SEARCH_CAP, "mail searches")
        if over is not None:
            return over
        found: list[MailThread] = []
        for alias in readable:
            reader = self.things.sources.reader(alias)
            if reader is None or ctx.cancelled():
                continue
            try:
                for thread_id in reader.search(query, max_threads=MAX_THREADS)[:MAX_THREADS]:
                    if ctx.cancelled():
                        break
                    thread = self._threads.get(thread_id) or thread_from_api(reader.thread(thread_id), alias)
                    if thread is not None:
                        found.append(thread)
            except Exception as exc:  # noqa: BLE001 - said plainly; its words may name something
                logger.info("Agent %s: a mail search failed (%s)", self.run_id or "-", type(exc).__name__)
                return ToolResult(MAIL_FAILED_TEXT, is_error=True, code=MAIL_FAILED, fields=fields)
        scrubbed = [_subjects_scrubbed(thread) for thread in found]
        self._taint(text for thread, _left in scrubbed for text in _people(thread))
        with self._lock:
            for thread in found:
                self._threads[thread.thread_id] = thread
                index_thread(self._index, thread)
        left = sum(count for _thread, count in scrubbed)
        told, field = _left_out(left)
        logger.info("Agent %s: a mail search found %d thread(s), %d value(s) left out", self.run_id or "-", len(found),
                    left)
        block = "\n".join(thread_text(thread, redact=True) for thread, _left in scrubbed) or NOTHING_FOUND_TEXT
        return ToolResult("\n".join(["Their text is not here: mail_read reads one by its thread id.",
                                     untrusted("the owner's mail: subjects and people", block),
                                     *([told] if told else [])]),
                          note=_count_of(len(found), "thread"), warn=bool(left), fields=(*fields, *field))

    def mail_read(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        with self._lock:
            thread = self._threads.get(str(args["thread"]).strip())
        if thread is None:
            return error(UNKNOWN_THREAD_TEXT, UNKNOWN)
        over = self._take("mail reads", MAIL_READ_CAP, "emails read")
        if over is not None:
            return over
        fit, left = _without_secrets(thread, trim_threads([thread])[0])
        read = [_unmarked(message.text) for message in fit.messages]
        self.firewall.go_strict()             # before anything of it goes back
        self._taint([*_people(fit), *read])
        for words in read:
            self.firewall.read_words.feed(words)
        text = thread_text(fit)
        flag, flagged, warn = _page_flags("\n".join(read))
        told, field = _left_out(left)
        logger.info("Agent %s: a thread read, %d message(s), %d value(s) left out", self.run_id or "-",
                    len(fit.messages), left)
        return ToolResult("\n".join([f"{MAIL_NOTE}\n{untrusted('an email', text)}{flag}", *([told] if told else [])]),
                          warn=warn or bool(left), note=f"{fit.chars:,} characters", fields=(*flagged, *field))

    # ---- files ----------------------------------------------------------------------------------------------------

    def _in_workspace(self, path: Any) -> bool:
        key = ntpath.normcase(str(path or "")).rstrip("\\")
        return bool(self._workspace) and (key == self._workspace or key.startswith(self._workspace + "\\"))

    def _place_rows(self, places: Iterable[Place], cap: int = PLACE_ROWS) -> tuple[list[str], dict[str, str]]:
        rows, ids = [], {}
        with self._lock:
            for place in places:
                if self._in_workspace(place.path):
                    continue
                suspect = any(guard.suspect_text(part) for part in place.parts)
                ref = self._refs.issue(PLACE, place, desktop_mode.place_label(place), suspect=suspect)
                if not ref:
                    continue
                ids[ntpath.normcase(place.path).rstrip("\\")] = ref
                if len(rows) < cap:
                    rows.append(f"{ref} {desktop_mode.place_label(place)}" + (" [suspect]" if suspect else ""))
        return rows, ids

    def _unreadable(self, ctx: Any, files: Sequence[Any]) -> frozenset[str] | None:
        """Of these listed files, those in a folder named for secrets; None when it can't be asked: then none is marked
        [readable]. file_read asks again right before any read."""
        try:
            return self._job(ctx, "in_secret_folders", [item.path for item in files if isinstance(item, FileItem)])
        except web.WebRefused:
            return None

    def _file_rows(self, files: Sequence[Any], places: Mapping[str, str], cap: int,
                   ctx: Any) -> tuple[list[str], int]:
        rows: list[str] = []
        left = 0
        unreadable = self._unreadable(ctx, files)
        with self._lock:
            for item in files:
                if not isinstance(item, FileItem) or self._in_workspace(item.path):
                    continue
                if len(rows) >= cap:
                    left += 1
                    continue
                suspect = guard.suspect_text(item.name)
                ref = self._refs.issue(FILE, item, item.name, suspect=suspect)
                if not ref:
                    continue
                place = places.get(ntpath.normcase(item.place or ntpath.dirname(item.path)).rstrip("\\"), "")
                row = desktop_mode.file_row(ref, item, place, suspect)
                readable = (guard.suffix(item.name) in desktop_service.READABLE_TYPES and not item.online_only
                            and not guard.sensitive_file_name(item.name)
                            and unreadable is not None and item.path not in unreadable)
                rows.append(row + (" [readable]" if readable else ""))
        return rows, left

    def files_find(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        words = " ".join(str(args["words"]).split())
        try:
            found = self._job(ctx, "find_for_task", words)
        except web.WebRefused as why:
            return error(why.text, why.code)
        shown = [item for item in found.files if not self._in_workspace(item.path)][:FIND_ROWS]
        place_rows, ids = self._place_rows(found.places)
        file_rows, left = self._file_rows(found.files, ids, FIND_ROWS, ctx)
        self._taint(item.name for item in shown)
        self._taint(part for place in found.places for part in place.parts)
        sites = [site for site in found.sites
                 if isinstance(site, SiteItem) and search_words(words) & search_words(site.title)]
        self._taint(app.name for app in found.apps)          # an app's or a saved site's name is the owner's too
        self._taint(site.title for site in sites)
        with self._lock:
            app_rows = [f"{self._refs.issue(APP, app, app.name)} {desktop_mode._quoted(app.name, desktop_mode.APP_CAP)}"
                        for app in found.apps]
            site_rows = [f"{self._refs.issue(SITE, site, site.title)} "
                         f"{desktop_mode._quoted(site.title, desktop_mode.TITLE_CAP)}" for site in sites]
        blocks = []
        if place_rows:
            blocks.append("Places:\n" + "\n".join(place_rows))
        if file_rows:
            blocks.append("Files:\n" + "\n".join(file_rows) + (f"\n[{left} more left out]" if left else ""))
        if app_rows or site_rows:
            blocks.append("Apps and sites:\n" + "\n".join([*app_rows, *site_rows]))
        logger.info("Agent %s: files found, %d listed", self.run_id or "-", len(file_rows))
        text = "\n\n".join(blocks) if file_rows or app_rows or site_rows else NOTHING_FOUND_TEXT
        return ToolResult(untrusted("names in the owner's folders", text), note=_count_of(len(file_rows), "file"),
                          fields=(("Looked for", words),))

    def _place_of(self, named: str) -> tuple[Place | None, ToolResult | None]:
        """The place a files_list names: a ref of this run, or a root by its name in what the roots list gave."""
        text = named.strip()
        with self._lock:
            ref = self._refs.resolve(text, PLACE)
            if ref is not None and isinstance(ref.target, Place):
                return ref.target, None
            for ref in self._refs.refs(PLACE):
                if isinstance(ref.target, Place) and len(ref.target.parts) == 1 \
                        and ref.target.parts[0].casefold() == text.casefold():
                    return ref.target, None
        return None, error(NOT_A_REF_TEXT, UNKNOWN)

    def files_list(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        place: Place | None = None
        named = str(args.get("place", "")).strip()
        if named:
            place, problem = self._place_of(named)
            if place is None:
                roots = self._job_roots(ctx, named)
                if roots is None:
                    return problem
                place = roots
        if place is not None and self._in_workspace(place.path):
            return error(WORKSPACE_TEXT, UNKNOWN)
        try:
            listing = self._job(ctx, "list_for_task", place, cap=LIST_ROWS)
        except web.WebRefused as why:
            return error(why.text, why.code)
        if listing.code:
            return error(READ_NOT_DONE_TEXT.format(reason=self._read_words(listing.code)), listing.code)
        self._taint(part for item in listing.places for part in item.parts)
        self._taint(item.name for item in listing.files)
        place_rows, ids = self._place_rows(listing.places, LIST_ROWS)
        if place is not None:
            with self._lock:
                ids[ntpath.normcase(place.path).rstrip("\\")] = self._refs.issue(
                    PLACE, place, desktop_mode.place_label(place),
                    suspect=any(guard.suspect_text(part) for part in place.parts))
        file_rows, left = self._file_rows(listing.files, ids, LIST_ROWS, ctx)
        blocks = []
        if place_rows:
            blocks.append("Places:\n" + "\n".join(place_rows))
        if file_rows:
            blocks.append("Files:\n" + "\n".join(file_rows))
        if listing.left_out or left:
            blocks.append(f"[{listing.left_out + left} more left out: files_find finds by name]")
        logger.info("Agent %s: a place listed, %d folder(s), %d file(s)", self.run_id or "-", len(place_rows),
                    len(file_rows))
        return ToolResult(untrusted("names in the owner's folders", "\n\n".join(blocks) or "It is empty."),
                          note=f"{_count_of(len(place_rows), 'folder')}, {_count_of(len(file_rows), 'file')}")

    def _job_roots(self, ctx: Any, named: str) -> Place | None:
        """A root named before any listing of this run: the roots' listing gives its ref."""
        try:
            listing = self._job(ctx, "list_for_task", None)
        except web.WebRefused:
            return None
        self._taint(part for item in listing.places for part in item.parts)
        self._place_rows(listing.places, LIST_ROWS)
        return next((item for item in listing.places if len(item.parts) == 1
                     and item.parts[0].casefold() == named.casefold()), None)

    @staticmethod
    def _read_words(code: str) -> str:
        words = READ_WORDS.get(code, READ_WORDS[desktop_service.READ_FAILED])
        return words.format(types=" ".join(f".{kind}" for kind in sorted(desktop_service.READABLE_TYPES)))

    def file_read(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        offset = args.get("offset", 0)
        if offset < 0:
            return error('"offset" must not be negative.', "bad_input")
        with self._lock:
            ref = self._refs.resolve(str(args["ref"]).strip(), FILE)
        if ref is None or not isinstance(ref.target, FileItem):
            return error(NOT_A_REF_TEXT, dv.UNKNOWN_REF)
        item = ref.target
        if self._in_workspace(item.path):
            return error(WORKSPACE_TEXT, UNKNOWN)
        over = self._take("file reads", FILE_READ_CAP, "file reads")
        if over is not None:
            return over
        try:
            part = self._job(ctx, "read_text", item, start=offset, cap=FILE_PART_CHARS)
        except web.WebRefused as why:
            return error(why.text, why.code)
        fields = (("File", data(item.name, 120)),)
        if part.code:
            logger.info("Agent %s: a file was not read (%s)", self.run_id or "-", part.code)
            return ToolResult(READ_NOT_DONE_TEXT.format(reason=self._read_words(part.code)), is_error=True,
                              code=UNREADABLE, fields=fields)
        self.firewall.go_strict()             # before anything of it goes back
        self._taint([part.text])
        self.firewall.read_words.feed(_unmarked(part.text))
        told, field = _left_out(part.left_out)
        logger.info("Agent %s: a file read, %d characters, %d value(s) left out", self.run_id or "-",
                    part.last - part.first, part.left_out)
        if not part.text:
            return ToolResult(READ_END_TEXT, note="the end of the file", fields=fields)
        clean = web.clean_text(part.text)
        flag, flagged, warn = _page_flags(clean, hidden=part.hidden)
        notes = [f"Characters {part.first:,} to {part.last:,}.", untrusted("one of the owner's files", clean)]
        if part.more:
            notes.append(READ_MORE_NOTE.format(offset=part.last))
        elif part.cut:
            notes.append(READ_CUT_NOTE)
        if told:
            notes.append(told)
        return ToolResult("\n".join(notes) + flag, warn=warn or bool(part.left_out),
                          note=f"{part.last - part.first:,} characters", fields=(*fields, *flagged, *field))

    # ---- changes: the desktop's validator and its cards ------------------------------------------------------------

    def _listed_names(self) -> list[str]:
        """The names of what this run listed: in the owner's words they are names, never a licence. (Lock held.)"""
        return [dv.item_label(ref) for each in (FILE, PLACE, APP, SITE) for ref in self._refs.refs(each)]

    def _checked(self, steps: Sequence[Mapping[str, Any]], run_id: str) -> tuple[Any, str]:
        """(the plan as desktop.validate checked it against this run's refs, the owner's own words and what they took
        back since, those words). None for a plan that can't be read."""
        answer = desktop_mode.plan_from({"say": "", "steps": [{"why": "", **step} for step in steps]})
        if answer is None:
            return None, ""
        with self._lock:
            said = self._sync()
            names = self._listed_names()
            refs = self._refs.snapshot()
        # Read with the lock free: a long paste's words take a while, and every other tool of the run waits on it. A
        # task's reading of a take-back ("scratch that", "no, open the report": pc-7, pc-10).
        withdrawn = dv.withdrawn_skills(self.firewall.messages(), names, task=True)
        return dv.check_plan(answer, refs, said, run_id=run_id, withdrawn=withdrawn), said

    @staticmethod
    def _refusals(plan: Any) -> str:
        return " ".join(f"Step {step.number} ({step.skill}): {REASONS.get(step.code, step.code)}."
                        for step in plan.refused())

    def propose_files(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        """D5: a wait. It returns at once with "end your turn"; the card is the desktop's own; its end comes back as the
        model's next message (card_message)."""
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        run_id = desktop_mode.new_run_id()
        plan, said = self._checked(args["steps"], run_id)
        if plan is None:
            return error(NOT_READ_PLAN, "bad_input")
        if plan.refused():
            logger.info("Agent %s: a file plan was refused (%s)", self.run_id or "-", plan.codes_text())
            code = plan.refused()[0].code
            return ToolResult(PLAN_REFUSED.format(reasons=self._refusals(plan)), is_error=True, code=code,
                              note=REASONS.get(code, ""))
        if not plan.accepted():
            return error(NOT_READ_PLAN, "bad_input")
        over = self._take("file cards", FILE_CARDS_CAP, "cards of file steps")
        if over is not None:
            return over
        with self._lock:
            self.run_ids.append(run_id)        # before anything can show: a Stop withdraws it from here on
        try:
            run = self._job(ctx, "carry_out", plan, said, None, organise=True)
        except web.WebRefused as why:
            return error(why.text, why.code)
        card = next((shown for shown in getattr(run, "shown", ()) if isinstance(shown, tidy.TidyCard)), None)
        rows = card.ok_rows() if card is not None else ()
        if card is None or not rows:
            reasons = {cardplan.row_words(row.code) or row.code for row in (card.rows if card is not None else ())
                       if row.code}
            reasons |= {dx.code_words(result.code) or result.code for result in run.results if result.code}
            text = ". ".join(sorted(word for word in reasons if word)) + "." if reasons else ""
            return error(NO_ROWS_TEXT.format(reasons=text), CAP if not reasons else UNREADABLE)
        if not ctx.on_card(card.run_id):
            return ToolResult(ANOTHER_CARD_TEXT, code=CARD_BUSY, note="a card waits already")
        if ctx.cancelled() or not self.things.show_run(run, ctx.cancelled):
            ctx.card_gone(card.run_id)
            return error(CARD_NOT_SHOWN_TEXT, DESK_GONE)
        logger.info("Agent %s: a card of file steps waits (%d row(s))", self.run_id or "-", len(rows))
        return ToolResult(CARD_WAIT_TEXT.format(rows=_count_of(len(rows), "row")), code=WAITING,
                          note=f"{_count_of(len(rows), 'row')} waiting for your OK")

    def open_thing(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        """The desktop's open skills at their own tiers (validate decides them, from the resolved target): at once, or
        on a card for the owner's click, which the task need not wait for. A task's model has read other people's text,
        so at once only what a plain request of the owner's names (_named_or_click). A site whose firewall card the
        owner has just allowed has had its click (_clicked_at_once): one click for one site."""
        clicked = self.firewall.clicked("open_thing", args.get("site"))    # taken first: one click is one call
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        ref, site = args.get("ref"), args.get("site")
        if bool(ref) == bool(site):
            return error(OPEN_ONE_TEXT, "bad_input")
        step = self._open_step(str(ref).strip(), bool(args.get("show_in_folder"))) if ref else (
            {"skill": dv.OPEN_URL, "url": str(site)})
        if step is None:
            return error(NOT_A_REF_TEXT, dv.UNKNOWN_REF)
        if isinstance(step, ToolResult):
            return step
        run_id = desktop_mode.new_run_id()
        plan, said = self._checked([step], run_id)
        if plan is None:
            return error(NOT_READ_PLAN, "bad_input")
        if plan.refused():
            logger.info("Agent %s: an open was refused (%s)", self.run_id or "-", plan.codes_text())
            code = plan.refused()[0].code
            return ToolResult(OPEN_NOT_DONE_TEXT.format(reason=REASONS.get(code, code)), is_error=True, code=code,
                              note=REASONS.get(code, ""))
        over = self._take("opens", OPENS_CAP, "opens") or (
            self._take("web pages", URLS_CAP, "web pages opened") if step["skill"] == dv.OPEN_URL else None)
        if over is not None:
            return over
        plan = self._named_or_click(plan)
        if clicked:
            plan = self._clicked_at_once(plan)
        with self._lock:
            self.run_ids.append(run_id)
        self.things.before_open()
        try:
            run = self._job(ctx, "carry_out", plan, said, None, organise=False)
        except web.WebRefused as why:
            return error(why.text, why.code)
        result = run.results[0] if run.results else None
        self.things.show_run(run, ctx.cancelled)
        status = result.status if result is not None else dx.REFUSED
        logger.info("Agent %s: an open came to %s", self.run_id or "-", status)
        if status == dx.DONE:
            return ToolResult(OPENED_TEXT, note="opened")
        if status == dx.WAITING:
            return ToolResult(OPEN_WAIT_TEXT, code=WAITING, note="waiting for your click")
        code = result.code if result is not None else ""
        reason = dx.code_words(code) or REASONS.get(code, "") or "Jarvis's checks stopped it"
        return ToolResult(OPEN_NOT_DONE_TEXT.format(reason=reason), is_error=True, code=code or dx.REFUSED)

    def _named_or_click(self, plan: Any) -> Any:
        """A file, place, app or site opens at once only from a plain request of the owner's that names it (_pieces: a
        clause that opens with an opening word, in a message of PLAIN_CAP characters or fewer, in no sentence that says
        it is no request, not taken back since, not an answer to the model's question); else on a one-click card. A
        paste, a how-to, a question or someone else's words naming it take that card. Only ever raises a tier
        (dv.promote), so what the validator asks a card for stays on its card."""
        _asks, opens = _wanted(self.firewall.heard())
        steps = []
        for step in plan.steps:
            if step.ok and step.tier == dv.INSTANT and not _asked_to_open(step, opens):
                step = replace(step, tier=dv.promote(step.tier, dv.ONE_CLICK),
                               flags=step.flags | {dv.FLAG_TASK_ASKED})
            steps.append(step)
        return replace(plan, steps=tuple(steps))

    @staticmethod
    def _clicked_at_once(plan: Any) -> Any:
        """The owner's Allow on the firewall's card for this very site, which showed its address, is the click its
        one-click card would wait for: such a step goes at once. One that needs more than a click (dv.CARD) stays."""
        return replace(plan, steps=tuple(replace(step, tier=dv.INSTANT) if step.ok and step.tier == dv.ONE_CLICK
                                         else step for step in plan.steps))

    def _open_step(self, ref_id: str, show: bool) -> dict[str, Any] | ToolResult | None:
        parsed = parse_ref(ref_id)
        with self._lock:
            ref = self._refs.resolve(ref_id) if parsed is not None else None
        if ref is None:
            return None
        if self._in_workspace(getattr(ref.target, "path", "")):
            return error(WORKSPACE_TEXT, UNKNOWN)
        if ref.kind == FILE:
            return {"skill": dv.SHOW_IN_FOLDER if show else dv.OPEN_FILE, "files": [ref_id]}
        if ref.kind == PLACE:
            return {"skill": dv.OPEN_FOLDER, "place": ref_id}
        if ref.kind == APP:
            return {"skill": dv.OPEN_APP, "app": ref_id}
        if ref.kind == SITE:
            return {"skill": dv.OPEN_URL, "site": ref_id}
        return None

    def _own_label(self, label: Any) -> str:
        """A timer's label stays only when it is the owner's own words: it is shown, and said at its end."""
        text = pcctl.clean_label(label) if isinstance(label, str) else ""
        owner = self.firewall.taint.owner_words()
        words = agent_firewall.call_words(text)
        return text if text and all(word in owner for word in words) else ""

    def _pc_ask(self, args: Mapping[str, Any]) -> pcctl.PcAsk | None:
        kind = args["kind"]
        keys = frozenset(args) - {"kind", "window"}
        step = dv.PlannedStep(skill=kind, keys=keys | {"skill", "why"}, action=str(args.get("action", "")),
                              level=args.get("level"), step=args.get("step"), minutes=args.get("minutes"),
                              label=str(args.get("label", "")))
        if dv.step_shape(step):
            return None
        if kind == dv.VOLUME:
            moves = step.action in ("up", "down")
            return pcctl.PcAsk(kind, step.action, level=step.level,
                               step=(step.step or pcctl.VOLUME_STEP) if moves else None)
        if kind == dv.MEDIA:
            return pcctl.PcAsk(kind, step.action)
        if kind == dv.TIMER:
            return pcctl.PcAsk(kind, pcctl.SET, minutes=step.minutes, label=self._own_label(step.label))
        if kind == dv.CANCEL_TIMER:
            return pcctl.PcAsk(kind, pcctl.CANCEL)
        if kind == dv.FOCUS_MODE:
            return pcctl.PcAsk(kind, pcctl.SET, minutes=step.minutes)
        if kind == dv.SCREENSHOT:
            return pcctl.PcAsk(kind, pcctl.ACTIVE_WINDOW if args.get("window") else pcctl.SCREEN)
        return None

    def pc_control(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        """The existing skills, licensed by the owner's own words as a desktop request's are, with chips and Undo. At
        once only when the local routes' parser reads the same thing in a plain request of theirs (the module docs),
        once for each time they asked (pc-5); anything else their words license goes on the firewall's card first."""
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        kind = args["kind"]
        with self._lock:
            said = self._sync()
            names = self._listed_names()
        messages = self.firewall.messages()
        asked = self._pc_ask(args)
        asks, _opens = _wanted(self.firewall.heard()) if asked is not None else ([], [])
        with self._lock:                       # an ask is used by one call at once: a repeat goes to the card
            fits = [(key, wanted) for key, wanted in asks if key not in self._spent and _same_ask(asked, wanted)]
            match = next((fit for fit in fits if _label_within(asked, fit[1]) == asked), fits[0] if fits else None)
            if match is not None:
                self._spent.add(match[0])
        routed = match is not None
        # A repeat of what they asked, used up, goes to the card whatever licence words they used ("turn it down").
        mine = routed or (asked is not None and any(_same_ask(asked, wanted) for _key, wanted in asks))
        if not mine and kind not in (dv.licensed_skills(said, names, dv.TASK_LICENCES, task=True)
                                     - dv.withdrawn_skills(messages, names, dv.TASK_LICENCES, task=True)):
            logger.info("Agent %s: a PC control was refused (not licensed)", self.run_id or "-")
            return ToolResult(PC_REFUSED_TEXT, is_error=True, code=dv.INTENT_NOT_LICENSED, note=NOT_ASKED)
        if asked is None:
            return error(PC_SHAPE_TEXT, "bad_input")
        if match is not None:
            asked = _label_within(asked, match[1])
        else:
            held = self.firewall.ask_once("pc_control", repr((asked.kind, asked.action, asked.level, asked.step,
                                                              asked.minutes, asked.label)),
                                          PC_CARD_TEXT.format(what=_card_what(asked)))
            if held is not None:
                logger.info("Agent %s: a PC control waits on a card", self.run_id or "-")
                return held
        over = self._take("PC changes", PC_CHANGES_CAP, "changes to the PC")
        if over is not None:
            return over
        hud = self.things.hud_windows() if kind == dv.SCREENSHOT else ()
        try:
            run = self._job(ctx, "pc_for_task", asked, words=said, run_id=desktop_mode.new_run_id(), hud=hud)
        except web.WebRefused as why:
            return error(why.text, why.code)
        result = run.results[0] if run.results else None
        if result is None:
            return error(DESK_GONE_TEXT, DESK_GONE)
        self.things.pc_done(asked, run)
        logger.info("Agent %s: a PC control came to %s", self.run_id or "-", result.status)
        words = dx.run_words(run, None, "") or "It was not done."
        if result.status == dx.DONE:
            return ToolResult(f"{words} The owner has its chip, with Undo.", note=words)
        return ToolResult(PC_NOT_DONE_TEXT.format(reason=words), is_error=True, code=result.code or result.status)

    # ---- changes: Ask's lines and cards -----------------------------------------------------------------------------

    def _link_refused(self, card: Any) -> bool:
        """A card's link leaves the PC on its click, so it is under the address rule once the run holds anything
        private: an address this run saw, unchanged, or one the owner wrote themselves (no card of the firewall's is
        needed for that: this card is the click, and the link shows on it)."""
        link = getattr(card, "link", "")
        wall = self.firewall
        return bool(link) and (wall.private or wall.strict) and not wall.book.seen(link) and not wall.owner_wrote(link)

    def propose_action(self, args: Mapping[str, Any], ctx: Any) -> ToolResult:
        """Ask's line formats and validators, its cards in Approvals and on the HUD. Not a wait: nothing is sent or done
        here, and the task can finish while they wait."""
        refused = self._refused_first(ctx)
        if refused is not None:
            return refused
        self._accounts()
        config = self.things.config
        with self._lock:
            self._sync()
            left = ACTION_CARDS_CAP - self._counts.get("action cards", 0)
            cards = to_cards(args["lines"], self._index, now=self.things.now(),
                             link_hosts=config.actions.link_hosts, max_cards=max(0, left))
        reasons = []
        valid = []
        for number, entry in enumerate(cards.report, 1):
            if entry.outcome == LINE_CARD:
                card = next(item for item in cards.cards if item.id == entry.card_id)
                if self._link_refused(card):
                    reasons.append(LINE_REFUSED_TEXT.format(number=number, reason=UNSEEN_LINK_TEXT))
                else:
                    valid.append(card)
            elif entry.reason:
                reasons.append(LINE_REFUSED_TEXT.format(number=number, reason=entry.reason))
            elif left <= 0:
                reasons.append(CAP_TEXT.format(what="cards", cap=ACTION_CARDS_CAP))
        if not valid:
            logger.info("Agent %s: no card came of %d line(s)", self.run_id or "-", len(cards.report))
            return ToolResult(ACTION_NONE_TEXT + " ".join(reasons), is_error=True, code=dv.SHAPE,
                              note="no card was made")
        with self._lock:
            self._counts["action cards"] = self._counts.get("action cards", 0) + len(valid)
            self.card_ids.extend(card.id for card in valid)   # before anything can show: a Stop withdraws them now
        if ctx.cancelled() or not self.things.take_cards(valid, ctx.cancelled):
            return error(CARD_NOT_SHOWN_TEXT, DESK_GONE)
        logger.info("Agent %s: %d card(s) wait in Approvals (%d refused)", self.run_id or "-", len(valid), len(reasons))
        text = ACTION_WAIT_TEXT.format(count=_count_of(len(valid), "line is", "lines are"))
        return ToolResult(" ".join([text, *reasons]), code=WAITING, note=f"{_count_of(len(valid), 'card')} waiting")


ERROR_CODES.update({OFF: "the owner turned this off", CAP: "that was the most for one task",
                    UNKNOWN: "Jarvis did not list that",
                    MAIL_REFUSED: "refused: only the owner's own words search mail",
                    MAIL_FAILED: "the mail could not be read", UNREADABLE: "it could not be read",
                    DESK_GONE: "this PC could not be reached",
                    NOT_LICENSED: "you did not ask for that", CARD_BUSY: "a card waits already"})
ERROR_CODES.update({code: words for code, words in dv.REASON_WORDS.items()})

__all__ = ["ACTION_CARDS_CAP", "CARD_CARRIED", "CARD_DROPPED", "CARD_EXPIRED", "CARD_NOT_NOW", "FILE_CARDS_CAP",
           "FILE_PART_CHARS", "FILE_READ_CAP", "MAIL_READ_CAP", "MAIL_SEARCH_CAP", "OwnerAccess", "Things",
           "card_message"]
