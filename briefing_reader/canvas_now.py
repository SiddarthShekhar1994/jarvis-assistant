"""The canvas's resting group "Now" (PLAN-canvas part 3, "the Hedwig mix"; brief part 3B item 9).

``NowController(canvas)`` is made by canvas_ui.CanvasController. Once the board's page is ready it puts up one group,
topic "Now", built only from what the app already holds (no read, no run, no model, no network):

* the day (``day``): today as the Day view knows it - the agenda Jarvis last read (``host._agenda_events``; None
  while the calendar is not signed in or not read: then no day frame, never one about an account that is not
  signed in), drawn as a day plan with no proposed blocks (pictures.plan_picture, board.frames.from_picture);
* the to-dos (``todos``): the to-do cards and deadlines as an answer's to-do frame shows them (board.frames.from_todos),
  only while there are some;
* "waits for your OK" (``approvals``): the Approvals screen's waiting cards (board.resting), only while some wait;
* Agents (``agents``): the standing agents (standing_ui.StandingController.agent_lines), only while the owner has one;
* mail: the app holds no list of unread mail for an account (the briefing's mail is spoken words, follow-ups are
  waiting / owed threads, not unread ones), so the group has no mail frame: ``mail_entries`` is where it would come
  from, and it gives nothing.

While the group is on the canvas it is kept current (every SYNC_MS): a frame whose source says something else is sent
again with the changed light (board.feed's ``again``; its sound is kept quiet), a source that has something new adds
its frame, one with nothing left takes its frame away, and a frame the owner closed stays away. Clear view clears it
like any other group; while it is not on the canvas (cleared, closed, made room for) and something could be in it,
``can_show()`` says the corner's "Now" button can work, and ``show()`` (its ``go now``) brings it back. A group made
while answers are on the board goes under them (feed ``below``): it never takes an answer's best places; an answer
that comes later is the newest, and the Now group's frames fold as any older group's do.

Logs: counts and kinds only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer

from . import pictures
from .board import frames as board_frames
from .board import resting
from .text_prep import format_time

logger = logging.getLogger(__name__)

SYNC_MS = 1500                    # the group is compared with what the app holds this often while it is up
AGENTS_FRESH_S = 30.0             # the agents' lines are read again this often (and whenever the standing agents change)
TOPIC = "Now"
DAY_TITLE = "Today"
SOURCES = ("approvals", "day", "todos", "agents")     # the group's order (what waits on the owner first)


NOTHING_BOOKED = "Nothing booked today"


def quiet_day(built: Any, frame_id: str) -> Any:
    """Today with no event (only free time, or nothing at all): a short frame that says so - its date as its meta, the
    words NOTHING_BOOKED - never a bare day bar with "0 events". A day with events is left as it is."""
    frame = built.frame if built is not None else None
    if not frame or frame.get("status", "ok") != "ok" or frame.get("kind") != "calendar":
        return built
    rows = (frame.get("body") or {}).get("rows") or []
    if any(row.get("state") != "free" for row in rows if isinstance(row, dict)):
        return built
    date_words = str(frame.get("meta", "")).split(board_frames.DOT)[0]
    return board_frames.Built((board_frames._frame(  # noqa: SLF001 - the builders' own frame shape
        frame_id, "calendar", str(frame.get("title", DAY_TITLE)), meta=date_words, status="empty",
        status_text=NOTHING_BOOKED),))


def _alive(item: Any) -> bool:
    try:
        return item is not None and shiboken6.isValid(item)
    except Exception:  # noqa: BLE001
        return False


class NowController(QObject):
    """See the module doc."""

    def __init__(self, canvas: Any) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.desk = canvas.desk
        self.host = canvas.host
        self.reading = canvas.reading
        self._round = 0                                 # each time the group is made: a new answer key
        self._answer: str = ""
        self._ids: dict[str, str] = {}                  # source -> the frame id put up for it (this round)
        self._closed: set[str] = set()                  # sources whose frame the owner closed (this round)
        self._started = False
        self.counts: dict[str, int] = {}
        self._clock = time.monotonic
        self._agents_at: float | None = None            # the agents' lines as last read, and when
        self._agent_lines: list[Any] = []
        self._has_at: float | None = None               # whether anything could be in the group, and when it was asked
        self._has = False
        standing = getattr(self.host, "standing", None)
        if standing is not None and hasattr(standing, "changed"):
            standing.changed.connect(self._agents_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(SYNC_MS)
        self._timer.timeout.connect(self.refresh)

    # ---- what the group holds ---------------------------------------------------------------------------------------

    def _now(self) -> Any:
        return self.desk._now()  # noqa: SLF001 - the desk's own clock (the app's)

    def _hour24(self) -> bool:
        return bool(self.desk._hour24())  # noqa: SLF001

    def entries(self) -> dict[str, Callable[[str], Any]]:
        """source -> build(frame_id) -> Built | None, for each source that has something to show now."""
        found: dict[str, Callable[[str], Any]] = {}
        builders = {"approvals": self._approvals, "day": self._day, "todos": self._todos, "agents": self._agents}
        for source in SOURCES:
            try:
                build = builders[source]()
            except Exception as exc:  # noqa: BLE001 - one source that can't be read never stops the others
                logger.debug("Now: %s was not read (%s)", source, type(exc).__name__)
                build = None
            if build is not None:
                found[source] = build
        for source, build in self.mail_entries().items():
            found[source] = build
        return found

    def mail_entries(self) -> dict[str, Callable[[str], Any]]:
        """Mail, only while the app holds unread mail for a signed-in account: it holds none (see the module doc)."""
        return {}

    def _approvals(self) -> Callable[[str], Any] | None:
        from .approvals_view import WAITING

        reading = self.reading
        count = int(reading.needs_ok_count())
        if count <= 0:
            return None
        rows = resting.approval_rows(reading.approvals.cards(), WAITING)
        return lambda frame_id: resting.from_approvals(count, rows, frame_id=frame_id)

    def _day(self) -> Callable[[str], Any] | None:
        from .config import DEFAULT_ACCOUNT

        events = getattr(self.host, "_agenda_events", None)
        if events is None:                              # not read, or not signed in: no frame about it
            return None
        now = self._now()
        hour24 = self._hour24()
        events = list(events)

        def build(frame_id: str) -> Any:
            picture = pictures.plan_picture({DEFAULT_ACCOUNT: events}, (), day=now.date(), now=now, hour24=hour24)
            built = board_frames.from_picture(picture, frame_id=frame_id, now=now, hour24=hour24, title=DAY_TITLE)
            return quiet_day(built, frame_id)

        return build

    def _todos(self) -> Callable[[str], Any] | None:
        from . import actions

        host = self.host
        todos = [action for action in getattr(host, "_actions", ()) or () if action.kind == actions.TODO]
        deadlines = list(getattr(host, "_merged_deadlines", ()) or ())
        store = getattr(host, "_store", None)
        statuses = {}
        for action in todos:
            record = store.get(action.id) if store is not None else None
            if record:
                statuses[action.id] = str(record.get("status", ""))
        now = self._now()
        built = board_frames.from_todos(todos, deadlines, frame_id="now-todos", now=now, statuses=statuses)
        if (built.frame or {}).get("status") != "ok":
            return None                                 # nothing to do: no frame at rest
        return lambda frame_id: board_frames.from_todos(todos, deadlines, frame_id=frame_id, now=now,
                                                        statuses=statuses)

    def _agents_changed(self) -> None:
        self._agents_at = None

    def _agents(self) -> Callable[[str], Any] | None:
        standing = getattr(self.host, "standing", None)
        if standing is None:
            return None
        now = self._clock()
        if self._agents_at is None or now - self._agents_at >= AGENTS_FRESH_S:
            self._agent_lines, self._agents_at = list(standing.agent_lines()), now
        lines = list(self._agent_lines)
        if not lines:
            return None
        return lambda frame_id: resting.from_agents(lines, frame_id=frame_id)

    # ---- the group on the canvas -------------------------------------------------------------------------------------

    def _feed(self) -> Any:
        return self.desk.feed

    def _group(self) -> str:
        return self._feed().group_for(self._answer) if self._answer else ""

    def on_canvas(self) -> bool:
        """The group is on the canvas: there, landed, and at least one of its frames neither closed nor cleared."""
        feed = self._feed()
        group = self._group()
        if not group or feed.is_parked(group):
            return False
        return any(not feed.flags(frame_id).get("closed") and not feed.flags(frame_id).get("cleared")
                   for frame_id in feed.group_order(group))

    def can_show(self) -> bool:
        """The corner's "Now" button can work: the page is up, the group is not on the canvas, no Clear's Bring back
        is offered (that brings it back), and something could be in it."""
        if not self._started or self.canvas.fallen() or self.on_canvas() or self._feed().clearing():
            return False
        now = self._clock()
        if self._has_at is None or now - self._has_at >= SYNC_MS / 1000:    # (the corners ask twice a second)
            self._has, self._has_at = bool(self.entries()), now
        return self._has

    def start(self) -> None:
        """The page is ready (the first time): the group goes up, then is kept current."""
        if self._started:
            return
        self._started = True
        self.show()
        self._timer.start()

    def show(self) -> bool:
        """The group, made again (a new round: a frame the owner closed comes back too). False: nothing to show."""
        entries = self.entries()
        if not entries or self.on_canvas():
            return False
        self._round += 1
        self._answer = f"now:{self._round}"
        self._ids, self._closed = {}, set()
        self.counts["shown"] = self.counts.get("shown", 0) + 1
        self._put(entries, first=True)
        logger.info("Now: up with %d frame(s)", len(self._ids))
        return bool(self._ids)

    def refresh(self) -> None:
        """While the group is on the canvas: each source as it is now (see the module doc)."""
        if not self._started or self.canvas.fallen() or not self.on_canvas() or self._feed().clearing():
            return
        feed = self._feed()
        on_board = set(feed.frame_ids())
        for source, frame_id in list(self._ids.items()):
            if frame_id not in on_board or feed.flags(frame_id).get("closed"):
                # the owner closed it (or it left for room): it stays away until the group comes back
                self._closed.add(source)
                del self._ids[source]
        entries = {source: build for source, build in self.entries().items() if source not in self._closed}
        messages: list[dict[str, Any]] = []
        for source in [source for source in self._ids if source not in entries]:
            messages += feed.remove(self._ids.pop(source))     # nothing left to say: its frame goes
        if messages:
            self.desk._send(messages)  # noqa: SLF001 - the desk's own send (its counts, the canvas, the wall)
        if self.on_canvas():
            self._put(entries, first=False)

    def _put(self, entries: dict[str, Callable[[str], Any]], *, first: bool) -> None:
        desk, feed = self.desk, self._feed()
        if desk.ensure_board() is None:
            return
        when = format_time(self._now(), hour24=self._hour24())
        below = first and bool(feed.groups())
        messages: list[dict[str, Any]] = []
        for rank, source in enumerate(SOURCES + tuple(source for source in entries if source not in SOURCES)):
            build = entries.get(source)
            if build is None:
                continue
            frame_id = feed.frame_id(self._answer, source)
            try:
                built = build(frame_id)
            except Exception as exc:  # noqa: BLE001 - one frame that can't be built never stops the others
                logger.debug("Now: a frame was not built (%s)", type(exc).__name__)
                continue
            if built is None or not built.messages:
                continue
            put = feed.put(self._answer, source, built.messages, when=when, topic=TOPIC, rank=float(rank),
                           again=not first, below=below)
            if frame_id in feed.frame_ids():
                self._ids[source] = frame_id
            messages += put
        feed.quiet_changed(self._ids.values())          # the changed light, never its sound
        group = self._group()
        if group:
            desk.quiet_groups.add(group)                # nor the landing sounds of a group nobody asked for
        if messages:
            desk._send(messages)  # noqa: SLF001

    def shutdown(self) -> None:
        self._timer.stop()


__all__ = ["DAY_TITLE", "NowController", "SOURCES", "SYNC_MS", "TOPIC"]
