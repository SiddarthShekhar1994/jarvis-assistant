"""The board on Home (the new look, part 5a; DESIGN-SPEC 2, 3.1, 3.5, 3.6 and 5): the controller that makes the board's
one view, feeds it from what Jarvis already makes, and forms and folds the desk (ui.ReadingView shows it).

``DeskController(host, factory=None)`` is made by look_ui.LookController while ``[look] board`` is on, and only then
(with it off Home is exactly as it was), and where a board can be (``can_have_board``). ``host`` is the app
(ui.AppController).

The board (board.view.BoardView, through ``factory(parent)``) is made once for the window's life, and started when the
first Ask starts (typed or voice) or the first group arrives, whichever comes first: never at the app's start, never on
its first paint (Chromium's first start holds the GUI thread for about a second). On a test platform (offscreen,
minimal) there is no board unless a factory is given (``can_have_board``): the web engine never starts inside a test
process. Its requests: ``go_approvals`` opens Approvals at that card (Ctrl+3's path); focus, open, close,
pin, bring back, retry and layout are counted only (part 5b wires them).

What feeds it (board.feed.BoardFeed keeps the groups; board.frames builds the frames), one answer = one group (its time;
its topic: Ask's coming ``topic`` field, else the first frame's title):

- a LIVE step's picture of the answer's task once ready (``apply``: the app's LIVE feed hands every change here) - a
  mail thread Jarvis read, a web search, a page picture (also one that comes ready after the answer), a day plan's
  day - and the calendar Jarvis read when the answer is about the calendar (today's day, else the week);
- the answer (``on_ask_outcome``): a web research's answer with its sources, the cards that wait on Approvals - a Reply
  or Email as the mail it sends (a reply and the thread it answers are ONE frame: the mail as read with the reply's
  amber chip, whose go_approvals opens the reply's card), a calendar card's change on its day -, the to-dos the
  answer proposes or speaks of, and the follow-ups it speaks of.

In a group a frame that waits on the owner comes first, then the order Jarvis speaks of them (board.feed.spoken_rank).
Limits: 24 frames and 8 groups (the oldest group without a pinned frame leaves whole). Frames last for the session and
are never saved. The desk forms with a group's first frame (ReadingView: the ring flies to the column head, the view's
geometry is set once, then ``desk: enter``); the first time in a session the empty Home's hint is counted
(prefs.note_board_hint; it stops after three).

Logs: ids, kinds, counts, durations and statuses only - never a frame's words, a turn's words or an address.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from . import live as live_events
from . import pictures
from .board import frames as board_frames
from .board.feed import BoardFeed, spoken_rank
from .text_prep import format_time

logger = logging.getLogger(__name__)

ANSWERS_KEPT = 16                 # the answers whose late pictures may still join their group
HINT_SESSIONS = 3                 # empty Home's hint line until the owner has seen the board this many times

# What an answer is about, from the owner's words and Jarvis's (a calendar read is put up only for these; to-dos and
# follow-ups only when spoken of or proposed).
_CALENDAR_WORDS = re.compile(
    r"\b(?:calendar|schedule|agenda|meetings?|events?|appointments?|busy|free time|free|day look|my day|today|"
    r"tonight|this (?:morning|afternoon|evening|week)|tomorrow|next week|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|check-in)\b", re.IGNORECASE)
_TODAY_WORDS = re.compile(r"\b(?:today|my day|this (?:morning|afternoon|evening)|tonight|right now|next meeting|"
                          r"rest of the day)\b", re.IGNORECASE)
_TODO_WORDS = re.compile(r"\b(?:to-?dos?|deadlines?|due|homework|assignments?|on my (?:list|plate)|my list|tasks?)\b",
                         re.IGNORECASE)
_FOLLOWUP_WORDS = re.compile(r"\b(?:follow[- ]?ups?|waiting on|waiting for (?:a )?(?:reply|answer)|owe|owed|"
                             r"haven't (?:heard|replied)|not replied|reply to)\b", re.IGNORECASE)
_KIND_WORDS = {
    "calendar": ("today", "tomorrow", "meeting", "meetings", "calendar", "schedule", "free", "event", "o'clock",
                 "first at", "check-in"),
    "mail_thread": ("mail", "email", "reply", "wrote", "sent", "message"),
    "mail_list": ("follow-up", "follow up", "waiting", "owe", "replied"),
    "todos": ("to-do", "todo", "due", "deadline", "list", "task"),
    "text": ("found", "search", "source", "according"),
    "picture": ("page", "picture", "site", "website"),
}


def about_calendar(words: str) -> bool:
    return bool(_CALENDAR_WORDS.search(words or ""))


def about_today(words: str) -> bool:
    return bool(_TODAY_WORDS.search(words or ""))


def about_todos(words: str) -> bool:
    return bool(_TODO_WORDS.search(words or ""))


def about_followups(words: str) -> bool:
    return bool(_FOLLOWUP_WORDS.search(words or ""))


def default_factory(parent: QWidget) -> Any:
    """The board as the app makes it: board.view.BoardView (its web engine started through snapshot_engine)."""
    from .board import view

    return view.BoardView(parent)


def can_have_board(factory: Callable[[QWidget], Any] | None) -> bool:
    """A board can be made here: a factory was given (tests, probes, stills), or Qt is not on a test platform
    (offscreen, minimal) - the web engine never starts inside a test process, and there Home stays as it was."""
    if factory is not None:
        return True
    from . import snapshot_engine

    return not snapshot_engine.on_test_platform()


@dataclass
class _Item:
    """One picture of an answer's LIVE task: its source, the picture, its merge key and its place among the steps."""

    source: str
    picture: Any
    merge: str
    seq: int


@dataclass
class _Answer:
    key: int
    request: str
    when: str
    seen: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    pending: dict[str, _Item] = field(default_factory=dict)
    week: Any = None
    done: bool = False
    say: str = ""

    def __repr__(self) -> str:      # ids and counts only
        return f"_Answer(key={self.key}, pending={len(self.pending)}, done={self.done})"


class DeskController(QObject):
    """See the module doc."""

    def __init__(self, host: Any, *, factory: Callable[[QWidget], Any] | None = None) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.reading = host.window.reading
        self.feed = BoardFeed()
        self.board: Any = None
        self._factory = factory or default_factory
        self._made = False                  # the factory ran (once: one board for the window's life)
        self._answers: dict[int, _Answer] = {}
        self._current = 0
        self._synthetic = 0
        self._formed = False                # the desk formed in this session (the hint counts once)
        self._readies = 0
        self._started_at = 0.0
        self.counts: Counter[str] = Counter()
        self.first_frame_ms: list[int] = []     # answer -> its first frame sent, ms (measured, not assumed)
        reading = self.reading
        reading.set_board_available(True)
        reading.set_board_hint(self._hints_left())
        reading.set_turn_frames(self._turn_frames)
        reading.deskShown.connect(self._on_desk_shown)
        reading.boardTokenClicked.connect(self._on_token)
        reading.deskLayoutChanged.connect(self._sync_compact)
        feed = getattr(host, "_live_feed", None)
        if feed is not None:
            feed.attach(self)               # every LIVE change, on the GUI thread (apply)

    # ---- the board -----------------------------------------------------------------------------------------------

    def _hints_left(self) -> bool:
        store = self._prefs()
        try:
            return store is None or store.look.board_hints < HINT_SESSIONS
        except Exception:  # noqa: BLE001 - no prefs: the hint shows
            return True

    def _prefs(self) -> Any:
        try:
            return self.host._prefs_store()
        except Exception:  # noqa: BLE001
            return None

    def ensure_board(self) -> Any:
        """The board, made and started now if it was not (once for the window's life); None when it can't be."""
        if self._made:
            return self.board
        self._made = True
        try:
            view = self._factory(self.reading)
        except Exception as exc:  # noqa: BLE001 - the board never stops the app
            logger.warning("Board: not made (%s)", type(exc).__name__)
            view = None
        if view is None:
            self.reading.set_board_available(False)
            return None
        self.board = view
        view.ready.connect(self._on_ready)
        view.unavailable.connect(self._on_unavailable)
        view.approvalsRequested.connect(self._on_go_approvals)
        for name in ("frameFocused", "openRequested", "closeRequested", "bringBackRequested", "retryRequested"):
            getattr(view, name).connect(lambda _frame_id, word=name: self._count_request(word))
        view.pinRequested.connect(lambda _frame_id, _on: self._count_request("pinRequested"))
        view.layoutChanged.connect(lambda _layout: self._count_request("layoutChanged"))
        self.reading.set_board_view(view)
        self._started_at = time.monotonic()
        view.start()
        logger.info("Board: made and starting")
        return view

    def _count_request(self, word: str) -> None:
        self.counts[f"request:{word}"] += 1      # part 5b gives these their work

    def _on_ready(self) -> None:
        self._readies += 1
        if self._readies == 1:
            logger.info("Board: ready %d ms after it started", int((time.monotonic() - self._started_at) * 1000))
            return
        # The page came back after its renderer stopped: it holds nothing - the board as it is, again.
        self._send(self.feed.resend())
        if self.reading.desk_shown():
            self._send([{"type": "desk", "state": "enter"}])

    def _on_unavailable(self, reason: str) -> None:
        self.counts[f"fallback:{reason}"] += 1
        logger.info("Board: read-only tiles in its place (%s)", reason)

    def _send(self, messages: list[dict[str, Any]]) -> None:
        board = self.board
        if board is None or not shiboken6.isValid(board):
            return
        for message in messages:
            if board.apply(message):
                self.counts[f"sent:{message.get('type', '')}"] += 1
            else:
                self.counts["refused"] += 1
        self._sync_counts()

    def _sync_counts(self) -> None:
        self.reading.set_board_count(self.feed.frames())
        self.reading.refresh_turn_frames()

    def _turn_frames(self, entry_id: int) -> int:
        return self.feed.turn_frames(entry_id)[1]

    # ---- the desk ------------------------------------------------------------------------------------------------

    def _on_desk_shown(self, shown: bool) -> None:
        """The desk formed (``desk: enter``: the board's fade and slide; the bar again) or folded (``leave``)."""
        if shown:
            self._sync_compact()
            board = self.board
            if board is not None and shiboken6.isValid(board):
                from . import holo

                board.set_motion(holo.motion_level())   # [ui] motion and reduced motion, as they are now
            self._send(self.feed.bar_again() + [{"type": "desk", "state": "enter"}])
            if not self._formed:
                self._formed = True             # seen once more this session: empty Home's hint counts it
                store = self._prefs()
                if store is not None:
                    try:
                        store.note_board_hint()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Board: the hint count was not saved (%s)", type(exc).__name__)
                self.reading.set_board_hint(self._hints_left())
        else:
            self._send([{"type": "desk", "state": "leave"}])

    def _sync_compact(self) -> None:
        self._send(self.feed.set_compact(self.reading.desk_compact()))

    def _on_token(self, entry_id: int) -> None:
        """A turn's "4 frames on the board": the board (back on Home if folded) scrolls to that group."""
        group, count = self.feed.turn_frames(entry_id)
        if not group or not count:
            return
        if not self.reading.desk_shown():
            self.reading.show_board(False)
        self._send([{"type": "reveal", "group": group}])

    def _on_go_approvals(self, frame_id: str) -> None:
        """The amber chip's go_approvals: Approvals at that card (Ctrl+3's path); nothing else."""
        action_id = self.feed.approvals_for(frame_id)
        self.counts["go_approvals"] += 1
        self.reading.open_card(action_id)

    # ---- answers ---------------------------------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = getattr(self.host, "_now", None)
        try:
            return now() if callable(now) else datetime.now()
        except Exception:  # noqa: BLE001
            return datetime.now()

    def _hour24(self) -> bool:
        return bool(getattr(getattr(getattr(self.host, "config", None), "display", None), "hour24", False))

    def on_ask_started(self) -> None:
        """An Ask started (typed or voice): the board starts now if it has not."""
        self.ensure_board()

    def on_answer_started(self, task_id: int, text: str) -> None:
        """An Ask shown on Home started: its group-to-be (the LIVE task's pictures join it)."""
        self.ensure_board()
        key = int(task_id or 0)
        if key <= 0:
            self._synthetic -= 1
            key = self._synthetic
        self._answers[key] = _Answer(key, str(text or ""), format_time(self._now(), hour24=self._hour24()))
        self._current = key
        while len(self._answers) > ANSWERS_KEPT:
            self._answers.pop(next(iter(self._answers)))

    def apply(self, changes: Any) -> None:
        """The app's LIVE feed: what changed (LiveChanges). A picture of an answer's task, once ready, joins its
        group (or waits for the answer when it is not in yet)."""
        try:
            for task in getattr(changes, "tasks", ()):
                answer = self._answers.get(getattr(task, "id", 0))
                if answer is None:
                    continue
                for step in getattr(task, "steps", ()):
                    if getattr(step, "picture", None) is not None:
                        self._take_picture(answer, step)
        except Exception as exc:  # noqa: BLE001 - the board never stops the LIVE view
            logger.debug("Board: a LIVE change was not read (%s)", type(exc).__name__)

    def _take_picture(self, answer: _Answer, step: Any) -> None:
        picture = step.picture
        kind = getattr(picture, "kind", "")
        state = getattr(picture, "state", "")
        if kind not in (pictures.KIND_EMAIL, pictures.KIND_WEEK, pictures.KIND_DAY, pictures.KIND_PAGE,
                        pictures.KIND_SEARCH) or state == live_events.PICTURE_DROPPED:
            return
        source = f"step:{step.id}"
        mark = (getattr(step, "revision", 0), state, id(picture))
        if answer.seen.get(source) == mark:
            return
        answer.seen[source] = mark
        if kind == pictures.KIND_WEEK:
            answer.week = picture              # put up only when the answer is about the calendar
            return
        merge = f"mail:{step.key}" if getattr(step, "kind", "") == live_events.MAIL_THREAD and step.key else ""
        item = _Item(source, picture, merge, int(getattr(step, "seq", 0)))
        if answer.done:
            self._put(answer, [self._picture_entry(answer, item)])
        else:
            answer.pending[source] = item

    def _picture_entry(self, answer: _Answer, item: _Item) -> tuple[Any, ...]:
        return (item.source, lambda frame_id: board_frames.from_picture(item.picture, frame_id=frame_id,
                                                                       now=self._now(), hour24=self._hour24()),
                item.merge, "")

    def on_ask_outcome(self, outcome: Any, task_id: int = 0) -> None:
        """A finished answer: its frames go up as one group (see the module doc)."""
        began = time.monotonic()
        feed = getattr(self.host, "_live_feed", None)
        if feed is not None:
            try:
                feed.drain()                    # the answer's pictures the LIVE feed has not handed over yet
            except Exception as exc:  # noqa: BLE001
                logger.debug("Board: the LIVE feed was not drained (%s)", type(exc).__name__)
        key = int(task_id or 0) or self._current
        answer = self._answers.get(key)
        if answer is None:
            self.on_answer_started(key, "")
            answer = self._answers[self._current]
        answer.done = True
        if not getattr(outcome, "ok", False):
            return
        answer.say = str(getattr(outcome, "say", "") or "")
        words = f"{answer.request} {answer.say}"
        entries: list[tuple[Any, ...]] = [self._picture_entry(answer, item)
                                          for item in sorted(answer.pending.values(), key=lambda item: item.seq)]
        answer.pending.clear()
        planned = getattr(outcome, "mode", "") == "plan"      # a day plan: its own day picture shows its blocks
        if not planned:
            entries += self._calendar_entries(answer, outcome, words)
        entries += self._research_entries(outcome)
        if not planned:
            entries += self._card_entries(outcome)
        entries += self._todo_entries(outcome, words)
        entries += self._followup_entries(words)
        if not entries:
            return
        self._put(answer, entries)
        self.feed.set_turn(answer.key, self._answer_entry())
        self._sync_counts()
        took = int((time.monotonic() - began) * 1000)
        self.first_frame_ms.append(took)
        logger.info("Board: an answer put up %d frame(s) in %d ms", len(entries), took)

    def _answer_entry(self) -> int:
        """The conversation turn of the answer just given (Jarvis's newest)."""
        from . import hud

        for entry in reversed(self.reading.conversation.entry_widgets()):
            if entry.role == hud.ROLE_JARVIS:
                return entry.entry_id
        return 0

    def _put(self, answer: _Answer, entries: list[tuple[Any, ...]]) -> None:
        """Each (source, build(frame_id) -> Built, merge key, approvals action id) into the answer's group."""
        messages: list[dict[str, Any]] = []
        if self.ensure_board() is None:
            return
        for source, build, merge, approvals in entries:
            frame_id = self.feed.frame_id(answer.key, source, merge)
            try:
                built = build(frame_id)
            except Exception as exc:  # noqa: BLE001 - one frame that can't be built never stops the others
                logger.debug("Board: a frame was not built (%s)", type(exc).__name__)
                continue
            if not built.messages:
                continue
            frame = built.messages[0]
            rank = spoken_rank(answer.say, self._rank_words(frame))
            messages += self.feed.put(answer.key, source, built.messages, when=answer.when, merge=merge,
                                      approvals=approvals, rank=rank)
            self.counts[f"frame:{frame.get('kind', '')}"] += 1
        self._send(messages)

    @staticmethod
    def _rank_words(frame: dict[str, Any]) -> list[str]:
        """The words that say Jarvis speaks of a frame: its kind's own, its sender's first name, its title's first
        word."""
        kind = str(frame.get("kind", ""))
        words = list(_KIND_WORDS.get(kind, ()))
        body = frame.get("body") or {}
        sender = str(body.get("sender", "") or "") if isinstance(body, dict) else ""
        if sender and not sender.startswith("To "):
            words.append(sender.split()[0])
        title = [word for word in re.split(r"\W+", str(frame.get("title", ""))) if len(word) > 3]
        if title:
            words.append(title[0])
        return words

    # ---- what an answer puts up -------------------------------------------------------------------------------------

    def _calendar_entries(self, answer: _Answer, outcome: Any, words: str) -> list[tuple[Any, ...]]:
        """The calendar Jarvis read, when the answer is about it: today's day (with its now line) when it speaks of
        today, else the week as read."""
        if not about_calendar(words):
            return []
        read = getattr(outcome, "calendar_read", None)
        if about_today(words) and read is not None:
            events = dict(getattr(read, "events", ()) or ())
            now = self._now()

            def today(frame_id: str) -> Any:
                picture = pictures.plan_picture(events, (), day=now.date(), now=now, hour24=self._hour24())
                return board_frames.from_picture(picture, frame_id=frame_id, now=now, hour24=self._hour24(),
                                                 title="Today")

            return [("calendar:today", today, "", "")]
        if answer.week is not None:
            week = answer.week
            return [("calendar:week", lambda frame_id: board_frames.from_picture(week, frame_id=frame_id,
                                                                                 now=self._now(),
                                                                                 hour24=self._hour24()), "", "")]
        return []

    def _research_entries(self, outcome: Any) -> list[tuple[Any, ...]]:
        report = getattr(outcome, "research", None)
        if report is None:
            return []
        sources = tuple((getattr(item, "title", ""), getattr(item, "url", ""))
                        for item in getattr(report, "sources", ()))
        numbers = [int(getattr(item, "number", 0) or 0) for item in getattr(report, "sources", ())]

        def research(frame_id: str) -> Any:
            built = board_frames.from_research(SimpleNamespace(answer=report.answer, sources=sources),
                                               frame_id=frame_id)
            rows = built.messages[0]["body"].get("rows", []) if built.messages else []
            for row, number in zip(rows, numbers):           # the answer's [1] marks: its rows numbered
                if number > 0 and (row.get("text") or row.get("meta")):
                    row["lead"] = str(number)
            return built

        return [("research", research, "", "")]

    def _card_entries(self, outcome: Any) -> list[tuple[Any, ...]]:
        """The answer's cards that wait on Approvals: a Reply or Email as the mail it sends (a reply merges with the
        thread it answers), a calendar card's change on its day."""
        from . import actions

        entries: list[tuple[Any, ...]] = []
        host = self.host
        for card in getattr(outcome, "cards", ()):
            if getattr(card, "error", "") or not getattr(card, "decidable", False):
                continue
            if card.kind in (actions.REPLY, actions.EMAIL):
                merge = f"mail:{card.account}:{card.field('thread')}" if card.kind == actions.REPLY else ""

                def outgoing(frame_id: str, card: Any = card) -> Any:
                    picture = pictures.outgoing_picture(self._mail_of(card), account=card.account)
                    return board_frames.from_picture(picture, frame_id=frame_id, now=self._now(),
                                                     hour24=self._hour24())

                entries.append((f"card:{card.id}", outgoing, merge, card.id))
            elif card.kind in (actions.CALENDAR, actions.MOVE, actions.CANCEL, actions.RSVP):
                def change(frame_id: str, card: Any = card) -> Any:
                    picture = host._live_day_picture(card)
                    return board_frames.from_picture(picture, frame_id=frame_id, now=self._now(),
                                                     hour24=self._hour24())

                entries.append((f"card:{card.id}", change, "", ""))
        return entries

    def _mail_of(self, card: Any) -> SimpleNamespace:
        """A Reply or Email card's message as its card shows it while it waits (the To, Cc, subject and text it
        carries; the account's own address when Jarvis knows it). Nothing is built to send: the card's own checks
        (new recipients ticked, the account ready) still stand before its Send."""
        from . import actions

        current = None
        try:
            current = self.host._current(card.id)
        except Exception:  # noqa: BLE001 - the answer's own card then
            current = None
        card = current or card
        reply = card.kind == actions.REPLY
        sender = getattr(self.host, "_senders", {}).get(card.account)
        try:
            from_addr = str(sender.from_address() or "") if sender is not None else ""
        except Exception:  # noqa: BLE001 - unknown: "(Gmail fills it in)"
            from_addr = ""
        return SimpleNamespace(from_addr=from_addr, to=card.field("to"), cc=card.field("cc"),
                               subject=card.field("subject") or getattr(card, "title", ""),
                               in_reply_to=card.field("msgid") if reply else "",
                               thread_id=card.field("thread") if reply else "", body=getattr(card, "body", ""))

    def _todo_entries(self, outcome: Any, words: str) -> list[tuple[Any, ...]]:
        """The to-dos the answer proposes or speaks of: the to-do cards (waiting on the owner's OK, done, open) and
        the deadlines as Jarvis shows them."""
        from . import actions

        proposed = [card for card in getattr(outcome, "cards", ()) if getattr(card, "kind", "") == actions.TODO]
        if not proposed and not about_todos(words):
            return []
        host = self.host
        todos = [action for action in getattr(host, "_actions", ()) if action.kind == actions.TODO]
        known = {action.id for action in todos}
        todos += [card for card in proposed if card.id not in known]
        store = getattr(host, "_store", None)
        statuses = {}
        for action in todos:
            record = store.get(action.id) if store is not None else None
            if record:
                statuses[action.id] = str(record.get("status", ""))
        deadlines = list(getattr(host, "_merged_deadlines", ()) or ())
        if not todos and not deadlines:
            return []
        return [("todos", lambda frame_id: board_frames.from_todos(todos, deadlines, frame_id=frame_id, now=self._now(),
                                                                   statuses=statuses), "", "")]

    def _followup_entries(self, words: str) -> list[tuple[Any, ...]]:
        """The follow-ups, when the answer speaks of them (the productivity bundle's list, waiting first)."""
        controller = getattr(self.host, "productivity", None)
        if controller is None or not about_followups(words):
            return []
        try:
            rows = list(controller.followups().rows)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Board: the follow-ups were not read (%s)", type(exc).__name__)
            return []
        if not rows:
            return []
        now = self._now()
        return [("followups", lambda frame_id: board_frames.from_followups(rows, frame_id=frame_id, now=now), "", "")]

    # ---- teardown --------------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Jarvis closes: the board's page, renderer and profile go now."""
        feed = getattr(self.host, "_live_feed", None)
        if feed is not None:
            try:
                feed.detach(self)
            except Exception:  # noqa: BLE001
                pass
        board = self.board
        if board is not None and shiboken6.isValid(board):
            board.shutdown()


__all__ = ["DeskController", "about_calendar", "about_followups", "about_today", "about_todos", "can_have_board",
           "default_factory"]
