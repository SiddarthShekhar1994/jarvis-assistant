"""The board on Home (the new look, part 5a; DESIGN-SPEC 2, 3.1, 3.5, 3.6 and 5): the controller that makes the board's
one view, feeds it from what Jarvis already makes, and forms and folds the desk (ui.ReadingView shows it).

``DeskController(host, factory=None)`` is made by look_ui.LookController while ``[look] board`` is on, and only then
(with it off Home is exactly as it was), and where a board can be (``can_have_board``). ``host`` is the app
(ui.AppController).

The board (board.view.BoardView, through ``factory(parent)``) is made once for the window's life, and started when the
first Ask starts (typed or voice) or the first group arrives, whichever comes first: never at the app's start, never on
its first paint (Chromium's first start holds the GUI thread for about a second). On a test platform (offscreen,
minimal) there is no board unless a factory is given (``can_have_board``): the web engine never starts inside a test
process. Its requests: ``go_approvals`` opens Approvals at that card (Ctrl+3's path) and does nothing else; part 5b's
ways (DESIGN-SPEC 3.5, 3.6), the app the source of truth (board.feed decides, the page shows it at once and gets a
frame's flags again when the app decided otherwise): pin (6 at most), close (the frame leaves CLOSE_S later unless
brought back), Clear (every unpinned frame out of sight with Bring back for CLEAR_S, then gone; nothing left: the desk
folds through ReadingView.set_board_count), restore (that Bring back), show_new and reading (parking: a group that comes
while the owner reads the board - the pointer over it, the focus in it, a scroll or key in it within QUIET_S - parks
under the pill "New answer above · Show" and lands after QUIET_S quiet seconds or on Show; only then do older groups
fold), focus_out (Esc and F6 back to the ask bar or the conversation); F6 into the board (``focus_board``). Focus is
counted only (canvas part 5A: a failed frame offers no Try again, and the page has no retry request).

Part 5c (DESIGN-SPEC 1, 3.4, 3.5, 7): ``cue`` (desk_cue.CueController: while Jarvis speaks, the frame the sentence names
is lit, every ring's glow halves, and the cue line runs to it; ``on_speech`` / ``on_muted`` from the app), ``big``
(desk_big.BigController: open big's two steps on the page's open request - Enter, a double click, its button), the
page's layout (the cue line's other end), a Clear with nothing pinned keeps the desk for its Bring back
(ReadingView.set_board_held), each frame's source key (``key``: the same thread, day, page or list; a later answer's
frame built from it is the same frame, "changed" when what it says differs), and the board's sounds through the look
controller (``look``): a frame lands (FRAME, by its place in its group: the first four), a group settles (SETTLED, once,
after its last frame), a changed light (CHANGED), open big and back (OPEN_BIG / CLOSE_BIG) - only while the desk is on
screen; SoundBoard's gate and LookController's quiet rules hold them otherwise.

What feeds it (board.feed.BoardFeed keeps the groups; board.frames builds the frames), one answer = one group (its time;
its topic: Ask's ``topic`` field, part 6, else the first frame's title):

- a LIVE step's picture of the answer's task once ready (``apply``: the app's LIVE feed hands every change here) - a
  mail thread Jarvis read, a web search, a page picture (also one that comes ready after the answer), a day plan's
  day - and the calendar Jarvis read when the answer is about the calendar (today's day, else the week);
- the answer (``on_ask_outcome``): a web research's answer with its sources, the cards that wait on Approvals - a Reply
  or Email as the mail it sends (a reply and the thread it answers are ONE frame: the mail as read with the reply's
  amber chip, whose go_approvals opens the reply's card), a calendar card's change on its day -, the to-dos the
  answer proposes or speaks of, and the follow-ups it speaks of;
- part 6: the answer's typed frames (``outcome.frames``: a table, a chart, a timeline, maths, as board.ask_frames
  checked them), each keyed by its kind and title; and an instant maths answer's working (``on_instant_answer``).

In a group a frame that waits on the owner comes first, then the order Jarvis speaks of them (board.feed.spoken_rank).
Limits: 24 frames and 8 groups (the oldest group without a pinned frame leaves whole). Frames last for the session and
are never saved. The desk forms with a group's first frame (ReadingView: the ring flies to the column head, the view's
geometry is set once, then ``desk: enter``); the first time in a session the empty Home's hint is counted
(prefs.note_board_hint; it stops after three).

Part 7: after every send that changed the board, the look controller's ``on_board_changed(feed)`` gives the second
screen the board as it is (its frames as the page has them; never the lit frame, a backdrop or open big).

The canvas (PLAN-canvas part 1; ``canvas``: canvas_ui.CanvasController, set by the look controller when Home is the
canvas): the board's view is Home itself and starts with the window, not at the first Ask (the canvas calls
``ensure_board``); the view is handed to the canvas as it is made (``on_board``: its black ground, the page's ``go``
requests, its ready and unavailable). No desk forms there: ReadingView's ``desk_shown()`` then says the board shows
frames on the canvas, and everything here that asks it works as before. Part 2: on the canvas a chart or a table that
carries a change also puts up its big-number frame (board.ask_frames.number_frame: built from the same checked data,
right after it); ``answer_group`` / ``answer_sources`` say what the task card needs; the canvas is told at once when
the board's count changed (the orb's form follows Clear view and its Bring back without waiting for its own timer).

Logs: ids, kinds, counts, durations and statuses only - never a frame's words, a turn's words or an address.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from . import holo, hud
from . import live as live_events
from . import pictures
from .board import ask_frames
from .board import feed as feed_mod
from .board import frames as board_frames
from .board import protocol as board_protocol
from .board.feed import BoardFeed, ReadingWatch, spoken_rank
from .desk_big import BigController
from .desk_cue import CueController
from .text_prep import format_time

logger = logging.getLogger(__name__)

CHANGED_PASS_MS = 2500            # a resting frame kept current: its CHANGED tag goes this long after its light starts
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
    "table": ("table", "rows", "lines", "columns"),                         # part 6's typed frames
    "chart": ("chart", "bars", "compare", "most", "least"),
    "timeline": ("timeline", "first", "then", "next", "due by"),
    "maths": ("works out", "comes to", "equals", "answer", "solve"),
}


def about_calendar(words: str) -> bool:
    return bool(_CALENDAR_WORDS.search(words or ""))


def about_today(words: str) -> bool:
    return bool(_TODAY_WORDS.search(words or ""))


def about_todos(words: str) -> bool:
    return bool(_TODO_WORDS.search(words or ""))


def about_followups(words: str) -> bool:
    return bool(_FOLLOWUP_WORDS.search(words or ""))


def picture_key(picture: Any, merge: str = "", plan: str = "") -> str:
    """A LIVE picture's source key (part 5c): the same mail thread (its merge key, "mail:work:t1"), the same page
    (its address), the same web search (its query) or the same day plan's picture; "" for none (never rebuilt across
    answers). A day picture has a key only in a day plan's answer (``plan``: the part it plans - "day", "morning" ...)
    and the key names what it is, never only its day: the day as Jarvis read it ("plan:Tue 17 Nov:read") or the blocks
    proposed for that part ("plan:Tue 17 Nov:blocks:morning"); any other day picture (a meeting brief's day on its own
    calendar) has none."""
    kind = getattr(picture, "kind", "")
    if kind == pictures.KIND_EMAIL:
        return merge
    if kind == pictures.KIND_PAGE:
        url = str(getattr(getattr(picture, "page", None), "url", "") or "")
        return f"page:{url}" if url else ""
    if kind == pictures.KIND_SEARCH:
        query = " ".join(str(getattr(getattr(picture, "search", None), "query", "") or "").split()).casefold()
        return f"{board_frames.SEARCH_KEY}{query}" if query else ""
    if kind == pictures.KIND_DAY:
        day = getattr(picture, "day", None)
        days = "|".join(str(item) for item in (getattr(day, "days", ()) or ()))
        if not days or not plan or getattr(day, "change", "") != pictures.CHANGE_PLAN:
            return ""
        proposed = any(getattr(block, "role", "") == pictures.ROLE_NEW for block in (getattr(day, "blocks", ()) or ()))
        return f"plan:{days}:blocks:{plan}" if proposed else f"plan:{days}:read"
    return ""


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
    plan: str = ""                  # a day plan's answer: the part it plans ("day", "morning" ...; its day pictures' key)
    topic: str = ""                 # part 6: Ask's topic, the group's header words ("" - its first frame's title)
    cited: int = 0                  # part 5B: the sources its research frame lists (the group's count never says fewer)

    def __repr__(self) -> str:      # ids and counts only
        return f"_Answer(key={self.key}, pending={len(self.pending)}, done={self.done})"


class DeskController(QObject):
    """See the module doc."""

    # How long a closed frame and a Clear offer Bring back, and how much quiet lands a parked answer (seconds; a probe
    # may shorten them on the class before it makes the controller).
    CLOSE_S = feed_mod.CLOSE_S
    CLEAR_S = feed_mod.CLEAR_S
    QUIET_S = feed_mod.QUIET_S

    def __init__(self, host: Any, *, factory: Callable[[QWidget], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, look: Any = None) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.look = look                    # look_ui.LookController: the board's sounds (part 5c)
        self.canvas: Any = None             # canvas_ui.CanvasController while Home is the canvas (PLAN-canvas part 1)
        self.reading = host.window.reading
        self.feed = BoardFeed(close_s=self.CLOSE_S, clear_s=self.CLEAR_S)
        self.board: Any = None
        self._factory = factory or default_factory
        self._clock = clock
        self._landed: set[str] = set()      # frames whose landing was heard of (a page that restarts: no sound again)
        self._done: dict[str, set[str]] = {}    # group id -> its frames whose draw-in is done
        self._settled: set[str] = set()     # groups that settled (SETTLED once each)
        self.quiet_groups: set[str] = set()     # part 3B: groups whose frames land without a sound (the canvas's "Now")
        self._made = False                  # the factory ran (once: one board for the window's life)
        self._answers: dict[int, _Answer] = {}
        self._current = 0
        self._synthetic = 0
        self._formed = False                # the desk formed in this session (the hint counts once)
        self._readies = 0
        self._started_at = 0.0
        self._watch = ReadingWatch(self.QUIET_S)     # is the owner reading the board? (a new answer parks)
        self._due_timer = QTimer(self)      # the next closed frame (or Clear) whose Bring back is over
        self._due_timer.setSingleShot(True)
        self._due_timer.timeout.connect(self._on_due)
        self._land_timer = QTimer(self)     # a parked answer lands when the owner has been quiet QUIET_S
        self._land_timer.setSingleShot(True)
        self._land_timer.timeout.connect(self._schedule_landing)
        self._pass_timer = QTimer(self)     # canvas part 5A: a resting frame's CHANGED goes once its light has played
        self._pass_timer.setSingleShot(True)
        self._pass_timer.setInterval(CHANGED_PASS_MS)
        self._pass_timer.timeout.connect(lambda: self._send(self.feed.pass_changed()))
        self.counts: Counter[str] = Counter()
        self.first_frame_ms: list[int] = []     # answer -> its first frame sent, ms (measured, not assumed)
        reading = self.reading
        reading.set_board_available(True)
        reading.set_board_hint(self._hints_left())
        reading.set_turn_frames(self._turn_frames)
        self.cue = CueController(self)      # part 5c: the lit frame and the cue line
        self.big = BigController(self)      # ... and open big
        reading.deskShown.connect(self._on_desk_shown)
        reading.boardTokenClicked.connect(self._on_token)
        reading.deskLayoutChanged.connect(self._sync_compact)
        reading.deskLayoutChanged.connect(self.big.on_layout)
        reading.boardFocusRequested.connect(self.focus_board)
        reading.tabChanged.connect(self._on_tab)
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
        view.frameFocused.connect(lambda _frame_id: self._count_request("frameFocused"))
        view.openRequested.connect(self._on_open)
        view.layoutChanged.connect(self._on_layout)
        view.frameLanded.connect(self._on_landed)
        view.bigStep.connect(self.big.on_step)
        view.pinRequested.connect(self._on_pin)
        view.closeRequested.connect(self._on_close)
        view.bringBackRequested.connect(self._on_bring_back)
        view.clearRequested.connect(self._on_clear)
        view.restoreRequested.connect(self._on_restore)
        view.showNewRequested.connect(self._on_show_new)
        view.readingChanged.connect(self._on_reading)
        view.focusOutRequested.connect(self._on_focus_out)
        canvas = self.canvas
        if canvas is not None and shiboken6.isValid(canvas):
            try:
                canvas.on_board(view)            # the canvas: this view is Home itself
            except Exception as exc:  # noqa: BLE001 - the canvas never stops the board
                logger.warning("Canvas: the board was not taken (%s)", type(exc).__name__)
        self.reading.set_board_view(view)
        self._started_at = time.monotonic()
        view.start()
        logger.info("Board: made and starting")
        return view

    def _count_request(self, word: str) -> None:
        self.counts[f"request:{word}"] += 1      # counts only

    # ---- part 5c: the desk on screen, the cue line's layout, open big, the board's sounds --------------------------

    def on_screen(self) -> bool:
        """The desk is what the owner sees now: Home, the desk shown, the window up (not minimised)."""
        reading = self.reading
        if not reading.desk_shown() or reading.current_tab() != hud.TAB_JARVIS or not reading.isVisible():
            return False
        window = reading.window()
        return not (window is not None and window.isMinimized())

    def _look_call(self, name: str, *args: Any) -> None:
        """A look controller's hook (part 7: on_board_changed, the board as it is now to the second screen, which
        never sounds); never stops the board."""
        look = self.look
        if look is None or (isinstance(look, QObject) and not shiboken6.isValid(look)):
            return
        try:
            getattr(look, name)(*args)
        except Exception as exc:  # noqa: BLE001 - the second screen never stops the board
            logger.debug("Board: the look controller's %s failed (%s)", name, type(exc).__name__)

    def sound(self, name: str, *args: Any) -> None:
        """One of the look controller's board sounds (its gate and SoundBoard's decide); never stops the board."""
        look = self.look
        if look is None or (isinstance(look, QObject) and not shiboken6.isValid(look)):
            return
        try:
            getattr(look, name)(*args)
        except Exception as exc:  # noqa: BLE001 - a sound never stops the board
            logger.debug("Board: a sound failed (%s)", type(exc).__name__)

    def on_speech(self, text: str) -> None:
        """An utterance of Jarvis's started ("": he is done, stopped or muted): the lit frame and the cue line."""
        self.cue.on_speech(text)

    def on_muted(self) -> None:
        self.cue.on_muted()

    def _on_layout(self, layout: Any) -> None:
        self._count_request("layoutChanged")
        self.cue.on_layout(layout)

    def _on_open(self, frame_id: str) -> None:
        self._count_request("openRequested")
        self.big.open(frame_id)

    def _on_tab(self, index: int) -> None:
        if index != hud.TAB_JARVIS:
            self.big.end_now()                   # another screen: the Dock and the header are never left dim
            self.cue.clear()

    def _on_landed(self, frame_id: str, step: str) -> None:
        """A frame drew in on the page: its outline complete (FRAME, the first four of its group, by its place), then
        its draw-in done (SETTLED once its group's last frame is done, groups of two or more). A frame heard of before
        (a page that came back after its renderer stopped) never sounds again."""
        self._count_request(f"landed:{step}")
        group = self.feed.group_of(frame_id)
        if not group or group in self.quiet_groups:
            return
        if step == "outline":
            if frame_id in self._landed:
                return
            self._landed.add(frame_id)
            order = self.feed.group_order(group)
            place = order.index(frame_id) if frame_id in order else -1
            if 0 <= place < 4 and self.on_screen():
                self.sound("on_frame_landed", place)
            return
        self._landed.add(frame_id)
        done = self._done.setdefault(group, set())
        done.add(frame_id)
        members = [member for member in self.feed.group_order(group)
                   if not self.feed.flags(member).get("closed") and not self.feed.flags(member).get("cleared")]
        if group in self._settled or len(members) < 2 or not all(member in done for member in members):
            return
        self._settled.add(group)
        if self.on_screen():
            self.sound("on_group_settled")

    # ---- the owner's own ways (part 5b, DESIGN-SPEC 3.5): the feed decides, the page gets what differs -------------

    def _on_pin(self, frame_id: str, on: bool) -> None:
        self._count_request("pinRequested")
        before = self.feed.counts()["pins_refused"]
        self._send(self.feed.pin(frame_id, on))
        if self.feed.counts()["pins_refused"] > before:
            logger.info("Board: a pin was refused (%d pinned at most)", board_protocol.MAX_PINS)

    def _on_close(self, frame_id: str) -> None:
        self._count_request("closeRequested")
        self._send(self.feed.close(frame_id, self._clock()))
        self._schedule_due()

    def _on_bring_back(self, frame_id: str) -> None:
        self._count_request("bringBackRequested")
        self._send(self.feed.bring_back(frame_id))
        self._schedule_due()

    def _on_clear(self) -> None:
        """The bar's Clear: every unpinned frame out of sight (Bring back for CLEAR_S); nothing left, the desk folds."""
        self._count_request("clearRequested")
        messages = self.feed.clear_unpinned(self._clock())
        self._send(messages)
        self._schedule_due()
        logger.info("Board: cleared (%d frame(s) kept)", self.feed.frames())

    def _on_restore(self) -> None:
        self._count_request("restoreRequested")
        self._send(self.feed.restore())
        self._schedule_due()

    def _schedule_due(self) -> None:
        """The timer for the next closed frame (or Clear) whose Bring back is over."""
        at = self.feed.next_due()
        if at is None:
            self._due_timer.stop()
            return
        self._due_timer.start(max(0, math.ceil((at - self._clock()) * 1000)) + 5)

    def _on_due(self) -> None:
        self._send(self.feed.due(self._clock()))
        self._schedule_due()

    # ---- parking: a new answer waits while the owner reads the board (DESIGN-SPEC 3.5 "Reading is safe") ----------

    def _reading_now(self) -> bool:
        """The owner reads the board: the desk shows (Home, unfolded, the window not minimised) and the page says the
        pointer is over it, the focus in it, or there was a scroll or a key in it within QUIET_S. Never while the
        page is not up (it fell back to the read-only tiles, or it loads again after its renderer stopped): only a page
        that is up reports that the owner left it (the final review's resilience 1)."""
        reading = self.reading
        board = self.board
        if board is None or not shiboken6.isValid(board) or not board.is_ready():
            return False
        if not reading.desk_shown() or not reading.isVisible():
            return False
        window = reading.window()
        if window is not None and window.isMinimized():
            return False
        return self._watch.reading(self._clock())

    def _on_reading(self, pointer: bool, focus: bool) -> None:
        self._watch.note(pointer, focus, self._clock())
        self._schedule_landing()

    def _schedule_landing(self) -> None:
        """A parked answer lands as soon as the owner is no longer reading (after QUIET_S quiet seconds)."""
        if not self.feed.parked_groups():
            self._land_timer.stop()
            return
        if not self._reading_now():
            self._land()
            return
        at = self._watch.quiet_at(self._clock())
        if at is None:
            self._land_timer.stop()              # the pointer or the focus is there: its leaving says so
            return
        self._land_timer.start(max(0, math.ceil((at - self._clock()) * 1000)) + 5)

    def _land(self) -> None:
        self._land_timer.stop()
        messages = self.feed.land()
        if messages:
            logger.info("Board: a parked answer landed")
            self._send(messages)

    def _on_show_new(self) -> None:
        """The pill's Show (or a parked answer's dashed entry): it lands now."""
        self._count_request("showNewRequested")
        self._land()

    # ---- the keyboard (DESIGN-SPEC 3.6): F6 into the board, Esc and F6 out of it ------------------------------------

    def focus_board(self, part: str) -> None:
        """F6's cycle reached the board (ReadingView.boardFocusRequested): the page takes it, or - no page to (it can't
        start, not ready yet) - the next part of the cycle does."""
        board = self.board
        if board is not None and shiboken6.isValid(board) and board.focus_part(part):
            self.counts[f"focus:{part}"] += 1
            return
        if part in ("board", "next"):
            self.reading.focus_ask_bar()
        else:
            self.reading.focus_conversation()

    def _on_focus_out(self, to: str) -> None:
        """Esc in the board (the ask bar), or F6 past its strip (the ask bar) or back before its frames (the
        conversation)."""
        self._count_request("focusOutRequested")
        if to == "conversation":
            self.reading.focus_conversation()
        else:
            self.reading.focus_ask_bar()

    def _on_ready(self) -> None:
        self._readies += 1
        if self._readies == 1:
            logger.info("Board: ready %d ms after it started", int((time.monotonic() - self._started_at) * 1000))
            return
        # The page came back after its renderer stopped: it holds nothing - the board as it is, again (open big ends
        # at once: the page lost it; the lit frame is sent again with the cue). Part 5c fixes, round 2: a big frame that
        # had the keyboard gives it to the board (its frame in view), never to the new page's <body>.
        keys_here = bool(self.big.state()) and self._keys_in_board()
        self.big.end_now()
        self.cue.reset_page()
        self._watch.reset()                      # the old page's last "reading" report ended with it
        self._send(self.feed.resend())
        if self.reading.desk_shown():
            self._send([{"type": "desk", "state": "enter"}])
        if keys_here:
            self.focus_board("board")
        self._schedule_landing()                 # what parked meanwhile lands (nobody reads the new page yet)

    def _keys_in_board(self) -> bool:
        """The window's keyboard focus is in the board's view."""
        board = self.board
        focus = QApplication.focusWidget()
        return board is not None and shiboken6.isValid(board) and focus is not None \
            and (focus is board or board.isAncestorOf(focus))

    def _on_unavailable(self, reason: str) -> None:
        self.counts[f"fallback:{reason}"] += 1
        self.big.end_now()
        self.cue.clear()
        self._watch.reset()                      # the dead page can never say the owner left it
        logger.info("Board: read-only tiles in its place (%s)", reason)
        self._land()                             # a parked answer shows as read-only tiles

    def _send(self, messages: list[dict[str, Any]]) -> None:
        board = self.board
        if board is None or not shiboken6.isValid(board):
            return
        for message in messages:
            if board.apply(message):
                self.counts[f"sent:{message.get('type', '')}"] += 1
            else:
                self.counts["refused"] += 1
        if not messages or any(message.get("type") not in ("lit", "backdrop", "big", "focus_part", "motion")
                               for message in messages):
            self._sync_counts()                  # (also when the feed changed and the page has it already)
            self._after_board_change()

    def _after_board_change(self) -> None:
        """The board changed: the changed light's sound, the lit frame matched again, a big frame that left goes."""
        changed = self.feed.take_changed()
        if changed and holo.motion_level() == holo.MOTION_LIVELY and self.on_screen():
            self.sound("on_frame_changed")       # the page runs its one light (calm: the badge only, no light)
        if changed:
            logger.info("Board: %d frame(s) changed", len(changed))
        if self.feed.passing() and not self._pass_timer.isActive():
            self._pass_timer.start()             # a resting frame's light plays (quietly), then its tag goes
        self.cue.refresh()
        self.big.check()
        self._look_call("on_board_changed", self.feed)    # part 7: the board as it is now, to the second screen

    def _sync_counts(self) -> None:
        # A Clear's Bring back keeps the desk (part 5c) even with nothing left: held before the count when it starts, so
        # the count's 0 never folds it while the row is offered, and let go only after the count when it ends (Bring
        # back, or a new answer making room), so the desk never folds and forms again between the two.
        clearing = self.feed.clearing()
        if clearing:
            self.reading.set_board_held(True)
        self.reading.set_board_count(self.feed.frames())
        if not clearing:
            self.reading.set_board_held(False)
        self.reading.refresh_turn_frames()
        canvas = self.canvas
        if canvas is not None and shiboken6.isValid(canvas):
            canvas.sync()                        # the canvas: the orb's form follows the frames at once

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
            self.big.end_now()                  # part 5c: open big ends at once, the Dock and the header undimmed
            self.cue.clear()
            self._watch.reset()                 # out of sight: nobody reads it (a parked answer lands)
            self._send([{"type": "desk", "state": "leave"}])
            self._land()

    def _sync_compact(self) -> None:
        self._send(self.feed.set_compact(self.reading.desk_compact()))

    def _on_token(self, entry_id: int) -> None:
        """A turn's "4 frames on the board": the board (back on Home if folded) scrolls to that group."""
        group, count = self.feed.turn_frames(entry_id)
        if not group or not count:
            return
        if not self.reading.desk_shown():
            self.reading.show_board(False)
        if self.feed.is_parked(group):
            self._land()                        # the owner asked for it: it lands, then the board goes to it
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
                item.merge, "", picture_key(item.picture, item.merge, answer.plan))

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
        answer.topic = str(getattr(outcome, "topic", "") or "")     # part 6: the group's header words
        words = f"{answer.request} {answer.say}"
        planned = getattr(outcome, "mode", "") == "plan"      # a day plan: its own day picture shows its blocks
        if planned:
            answer.plan = str(getattr(getattr(outcome, "window", None), "part", "") or "day")
        entries: list[tuple[Any, ...]] = [self._picture_entry(answer, item)
                                          for item in sorted(answer.pending.values(), key=lambda item: item.seq)]
        answer.pending.clear()
        if not planned:
            entries += self._calendar_entries(answer, outcome, words)
        report = getattr(outcome, "research", None)
        answer.cited = len(tuple(getattr(report, "sources", ()) or ())) if report is not None else 0
        entries += self._research_entries(outcome)
        entries += self._frame_entries(outcome)
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
        # The owner reads the board: a new answer parks under the pill (its group, when it is a new one), the scroll
        # holds, and it lands after QUIET_S quiet seconds or on Show. Not reading: one parked before lands first.
        park = self._reading_now()
        if not park and self.feed.parked_groups():
            self._land()
        for entry in entries:
            source, build, merge, approvals = entry[:4]
            key = str(entry[4] or "") if len(entry) > 4 else ""      # part 5c: the frame's source key
            frame_id = self.feed.frame_id(answer.key, source, merge, key)
            try:
                built = build(frame_id)
            except Exception as exc:  # noqa: BLE001 - one frame that can't be built never stops the others
                logger.debug("Board: a frame was not built (%s)", type(exc).__name__)
                continue
            if not built.messages:
                continue
            frame = built.messages[0]
            rank = spoken_rank(answer.say, self._rank_words(frame))
            messages += self.feed.put(answer.key, source, built.messages, when=answer.when, topic=answer.topic,
                                      merge=merge, approvals=approvals, rank=rank, park=park, key=key,
                                      not_ready=bool(getattr(built, "not_ready", False)))   # a LIVE picture's own state
            self.counts[f"frame:{frame.get('kind', '')}"] += 1
        self._send(messages)
        if self.feed.parked_groups():
            self._schedule_landing()

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

            return [("calendar:today", today, "", "", f"day:{now.date().isoformat()}")]
        if answer.week is not None:
            week = answer.week
            return [("calendar:week", lambda frame_id: board_frames.from_picture(week, frame_id=frame_id,
                                                                                 now=self._now(),
                                                                                 hour24=self._hour24()), "", "",
                     f"week:{self._now().date().isoformat()}")]
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

    def _on_canvas(self) -> bool:
        """Home is the canvas now (never once it fell back to the native ring and the read-only tiles)."""
        canvas = self.canvas
        try:
            return canvas is not None and shiboken6.isValid(canvas) and not canvas.fallen()
        except Exception:  # noqa: BLE001 - unknown: not the canvas
            return False

    def _frame_entries(self, outcome: Any) -> list[tuple[Any, ...]]:
        """Part 6: the answer's typed frames (a table, a chart, a timeline, maths: board.ask_frames checked them), each
        with its source key - its kind and its title folded ("table:budget lines") - so a later answer with the same
        frame builds it again in its place (changed only when what it says differs). The canvas, part 2: a chart or a
        table that carries a change is followed by its big-number frame (ask_frames.number_frame: the same checked
        data, keyed "number:" and its frame's key)."""
        entries: list[tuple[Any, ...]] = []
        numbers = self._on_canvas()
        for index, frame in enumerate(getattr(outcome, "frames", ()) or ()):
            if isinstance(frame, ask_frames.AskFrame):
                entries.append((f"ask:{index}",
                                lambda frame_id, frame=frame: ask_frames.build(frame, frame_id=frame_id), "", "",
                                frame.key))
                number = ask_frames.number_frame(frame) if numbers else None
                if number is not None:
                    entries.append((f"ask:{index}:number",
                                    lambda frame_id, frame=number: ask_frames.build(frame, frame_id=frame_id), "", "",
                                    number.key))
        return entries

    # ---- what the canvas's task card asks (part 2) --------------------------------------------------------------

    def answer_group(self, task_id: int = 0) -> str:
        """The group an answer's frames went to ("" - it put none on the board)."""
        key = int(task_id or 0) or self._current
        group = self.feed.group_for(key)
        return group if group and self.feed.group_order(group) else ""

    def answer_sources(self, task_id: int = 0) -> int:
        """How many sources Jarvis read for an answer so far: its LIVE task's pictures (a mail thread, a page, a
        web search, a day) - and once it is done, never fewer than its research frame lists (canvas part 5B, the walk's
        15: the group's head said "1 source" over a frame of 2)."""
        answer = self._answers.get(int(task_id or 0) or self._current)
        return max(len(answer.seen), answer.cited) if answer is not None else 0

    def on_instant_answer(self, reply: Any, words: str) -> None:
        """Part 6: Jarvis's own instant answer (instant.InstantReply). A sum whose working has a step between the sum as
        read and its result (instant_maths) gets a maths frame of that working, checked by board.ask_frames as Ask's
        frames are, in an answer's group of its own (its turn's "1 frame on the board"). Anything else puts nothing up.
        The log says a reason word only."""
        working = getattr(reply, "working", None)
        if not working:
            return
        frame = ask_frames.check_frame(working)
        if isinstance(frame, str):
            logger.info("Board: an instant working was not shown (%s)", frame)
            return
        self._synthetic -= 1
        answer = _Answer(self._synthetic, str(words or ""), format_time(self._now(), hour24=self._hour24()), done=True,
                         say=str(getattr(reply, "speech", "") or ""))
        self._answers[answer.key] = answer
        while len(self._answers) > ANSWERS_KEPT:
            self._answers.pop(next(iter(self._answers)))
        self._put(answer, [("instant", lambda frame_id: ask_frames.build(frame, frame_id=frame_id), "", "",
                            frame.key)])
        self.feed.set_turn(answer.key, self._answer_entry())
        self._sync_counts()

    def on_capture(self, summary: Any, *, approvals: str = "", words: str = "") -> str:
        """Build plan item 3: what a capture found as a Capture frame (board.frames.from_capture), in an answer's group
        of its own; its amber chip's go_approvals opens Approvals at ``approvals`` (the first card that waits) and
        approves nothing. Returns the frame's id ("" when nothing went up). The log says counts only."""
        self._synthetic -= 1
        answer = _Answer(self._synthetic, str(words or ""), format_time(self._now(), hour24=self._hour24()), done=True,
                         say="", topic="Capture")
        self._answers[answer.key] = answer
        while len(self._answers) > ANSWERS_KEPT:
            self._answers.pop(next(iter(self._answers)))
        made: list[str] = []

        def build(frame_id: str) -> Any:
            made.append(frame_id)
            return board_frames.from_capture(summary, frame_id=frame_id)

        self._put(answer, [("capture", build, "", approvals, "")])
        self._sync_counts()
        logger.info("Board: a capture's frame went up (%d row(s))", len(getattr(summary, "rows", ()) or ()))
        return made[0] if made else ""

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

                entries.append((f"card:{card.id}", outgoing, merge, card.id, merge or f"card:{card.id}"))
            elif card.kind in (actions.CALENDAR, actions.MOVE, actions.CANCEL, actions.RSVP):
                def change(frame_id: str, card: Any = card) -> Any:
                    picture = host._live_day_picture(card)
                    return board_frames.from_picture(picture, frame_id=frame_id, now=self._now(),
                                                     hour24=self._hour24())

                entries.append((f"card:{card.id}", change, "", "", f"card:{card.id}"))
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
        the deadlines as Jarvis shows them. With none left, a to-do list still on the board is built again empty (its
        last item left its sources: a rule paused, the cards' cap, a briefing update) - never a new empty frame, also
        when an earlier frame of the same answer made room by taking the list's group, or when what is left is only
        to-dos the owner dismissed (they stay among the cards, but are no rows): decided on the list as it is built.
        The app's own DEADLINES and Approvals say "none" the same way whether or not the briefing or the calendar was
        read, so an empty list here is the app's own "none" too."""
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
        if not todos and not deadlines and not self.feed.holds("todos"):
            return []

        def build(frame_id: str) -> Any:
            built = board_frames.from_todos(todos, deadlines, frame_id=frame_id, now=self._now(), statuses=statuses)
            if (built.frame or {}).get("status") == "empty" and not self.feed.holds("todos"):
                return board_frames.Built()      # empty, and its frame is not on the board (or left for this answer's
            return built                         # room): no new empty one

        return [("todos", build, "", "", "todos")]

    def _followup_entries(self, words: str) -> list[tuple[Any, ...]]:
        """The follow-ups, when the answer speaks of them (the productivity bundle's list, waiting first). With none
        left, a follow-ups list still on the board is built again empty (a scan dropped its rows, a row was hidden) -
        only when the list is known to be empty by the app's own rule (a scan ran and none runs now, no account's note,
        not turned off on this PC: never "Nothing waits on you" from a list nobody read), and never as a new empty
        frame, also when an earlier frame of the same answer made room by taking the list's group (checked again as it
        is built)."""
        controller = getattr(self.host, "productivity", None)
        if controller is None or not about_followups(words):
            return []
        try:
            view = controller.followups()
            rows = list(view.rows)
            known_empty = (getattr(view, "scanned_at", None) is not None and not getattr(view, "scanning", False)
                           and not getattr(view, "notes", ()) and not getattr(view, "off", False))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Board: the follow-ups were not read (%s)", type(exc).__name__)
            return []
        if not rows and (not known_empty or not self.feed.holds("followups")):
            return []
        now = self._now()

        def build(frame_id: str) -> Any:
            if not rows and not self.feed.holds("followups"):
                return board_frames.Built()      # its frame left for this answer's room: no new empty one
            return board_frames.from_followups(rows, frame_id=frame_id, now=now)

        return [("followups", build, "", "", "followups")]

    # ---- teardown --------------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Jarvis closes: the board's page, renderer and profile go now."""
        self._due_timer.stop()
        self._land_timer.stop()
        self._pass_timer.stop()
        self.cue.shutdown()
        self.big.shutdown()
        canvas = self.canvas
        if canvas is not None and shiboken6.isValid(canvas):
            canvas.shutdown()
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
