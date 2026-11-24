"""The second-screen display (the new look, part 7; DESIGN-SPEC 6, its still second_1920x1080.png and second.html): Jarvis
on the owner's other monitor - the ring at its live look, the state word, the clock, the date, the next event, the counts
and the board as glance tiles - painted natively with QPainter (the board's one web view never leaves the main window).
Off until the owner turns it on in Settings > Look ("Show Jarvis on my second screen": prefs.LookPrefs.second_screen,
[look] second_screen the default).

    SecondScreen(QWidget)            the display: ONE top-level, frameless, OPAQUE native window, never focused
                                     (WA_ShowWithoutActivating, WindowDoesNotAcceptFocus, WS_EX_NOACTIVATE on its own
                                     window), never topmost, never in the taskbar or Alt+Tab (a tool window) and passed
                                     through by the mouse; excluded from capture (WDA_EXCLUDEFROMCAPTURE on its own
                                     window; if Windows refuses, it still shows and the log says so).
    SecondScreenController(QObject)  look_ui.LookController's: the setting, the screen it shows on, what feeds it (the app
                                     pushes: the look and the counts on ReadingView.glanceChanged, the board through
                                     on_board(feed), the calendar's events through set_events, the owner's input), when it
                                     hides, and OLED care's clocks. It never reads the network, a mail's body or a file.
    PlacementBroker / broker()       "the other screen" (Sharing): 1. the LIVE pop-out the owner opened there, 2. the
                                     agent's watch window, 3. this display. When 1 or 2 is on the display's screen the
                                     display folds to a strip on the far edge and room() gives them the rest of the
                                     screen; it comes back RETURN_MS after they leave. With the display off room() is the
                                     work area as it was: both windows go exactly where they went before part 7.

What it shows (DESIGN-SPEC 6, at 1920x1080; everything scales by s = min(W/1920, H/1080), other aspect ratios centre a
16:9 box, a screen under STRIP_BELOW px wide (logical) shows only the strip). One left edge at x 96 for the ring's box
(its brackets) and every line. The left column: the ring at 320 (its live look; OLED care: no glow, the core at 50 %,
no white over OLED_INK), the state word, the clock (Chakra 120, OLED_INK), the date, NEXT with the next event (its time
and title) and its line ("In 19 minutes · video call"), the counts as a shape and words (a diamond: "1 needs your OK" /
"Waiting on Approvals, on the main screen"; a dot: "Briefing ready" / "Read it on the main screen"; the board glyph:
"9 frames on the board" / "1 pinned"). The right wall: "BOARD · UPDATED 9:41 AM" and "Mail text hidden on this screen"
over a 3x3 grid of outline-only glance tiles (board.glance, private and in the SECOND face: mail text hidden, page
pictures at 62 %, a picture over tile_picture's 4 MP never decoded) - the board's first nine frames in its order, as
the page has them (board.protocol checked; parked, closed and cleared frames left out, and web searches - titled with
the model's query - too; a mail's text and a text frame's paragraph - a web research answer is the line Jarvis speaks
- taken out before they reach the window, its title and sources kept). With [look] board off the wall holds only the
counts' row. The strip (beside a LIVE pop-out or a watch window, and on a narrow screen): the ring at 200, the state
word, the clock and the counts. Never on this screen: the owner's question, the spoken line, a mail's text, the lit
frame, the cue line, open big's backdrop, any sound. Lines break at spaces only, never after a hyphen.

While Home is the canvas (holo.canvas_look) the ring is the canvas orb's flat JARVIS badge (orb.set_badge_form), in
OLED care's form: no glow, nothing brighter than OLED_INK; the counts-only rule is the same.

OLED care: true black, no grid, no glows; a DRIFT_PX drift on a 3x3 lattice every DRIFT_S; the sides swap every SWAP_S,
never within SWAP_GUARD_S of a board change; after DIM_AFTER_S quiet seconds (no board change, no state change, no owner
input to Jarvis) it dims to DIM_LEVEL, after DEEP_AFTER_S only the still ring and the clock remain at DEEP_LEVEL; the
ring at 10 fps, 2 fps dimmed, still in the deep dim (and with motion off).

It hides (HideProbes, injectable; asked at the drift timer's pace and when a notification comes - a screen plugged in or
out, the main window going to another screen, the setting, the broker, the console display's state Windows sends the
display's own window - never a busy loop) while the session is locked, the display is off, battery saver is on, or a
full-screen app is on THAT screen (a foreground window covering the display's whole monitor, or Windows' presentation
/ full-screen Direct3D state); it comes back when the rule lifts (at the next drift step at the latest).

Logs hold reason words, counts and sizes only: never a frame's words, an event's title, a sender or an address.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import weakref
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QWidget

from . import holo, hud
from . import orb as orb_mod
from .board import glance

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Sizes (px at 1920x1080; x s) and OLED care's clock
# --------------------------------------------------------------------------

BASE_W, BASE_H = 1920, 1080
STRIP_BELOW = 1600             # a screen narrower than this (logical px) shows only the strip
EDGE = 96                      # the one left edge: the ring's box and every line of the column
TOP = 72                       # the column's and the wall's top; the wall's bottom margin too
COLUMN_W = 440                 # the column: x 96 to 536
RING = 320                     # the ring (its px, as Home's hero), its brackets' box at x 96, y 72
STRIP_RING = 200               # ... in the strip
WALL_X, WALL_RIGHT = 640, 80   # the wall: x 640 to 1840
HEAD_BASE = 17                 # the wall's header line: its baseline under the wall's top
GRID_TOP = 48                  # the tiles under the wall's top
TILE_GAP = 24
GRID = 3                       # 3 x 3 tiles
STRIP_W = 360                  # the strip
STRIP_PAD = 40                 # ... its content's inset
# The column's rhythm (baselines, after second.html's still): the state word 24 under the ring's box, the clock 144 under
# it, the date 46, NEXT's label 57, its title 36 (each more line 30), its line 28 (each more 22), the counts' first rule
# 46 under NEXT (60 under the date without it); a count's title 34 under its rule (each more line 26), its words 20.5
# under the title (each more 20), the next rule 20.5 under them.
WORD_GAP, CLOCK_GAP, DATE_GAP, NEXT_GAP = 24, 144, 46, 57
NEXT_TITLE_GAP, NEXT_TITLE_LINE, NEXT_LINE_GAP, NEXT_LINE_LINE = 36, 30, 28, 22
COUNTS_GAP, COUNTS_GAP_NO_NEXT = 46, 60
COUNT_TITLE, COUNT_TITLE_LINE, COUNT_SUB, COUNT_SUB_LINE, COUNT_AFTER = 34, 26, 20.5, 20, 20.5
COUNT_SHAPE = 28               # a count's words after its shape
NEXT_TITLE_LINES = 2           # an event's title wraps to at most this many lines ("…" after)

DRIFT_PX = 6                   # OLED care: the drift (x s), on a 3 x 3 lattice ...
DRIFT_S = 30.0                 # ... a step every 30 s
SWAP_S = 60.0 * 60.0           # the sides swap every hour ...
SWAP_GUARD_S = 60.0            # ... never within a minute of a board change
DIM_AFTER_S = 5.0 * 60.0       # quiet this long: dimmed ...
DEEP_AFTER_S = 20.0 * 60.0     # ... this long: the deep dim
DIM_LEVEL, DEEP_LEVEL = 0.6, 0.3
RING_MS, RING_DIM_MS = 100, 500    # the ring at 10 fps, 2 fps dimmed (still in the deep dim)
RETURN_MS = 2000               # the display comes back this long after the pop-out or the watch window left
CORE_SHARE = 0.5               # the ring's core at 50 %, no glow
_LATTICE = ((0, 0), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))

LIT, DIM, DEEP = "lit", "dim", "deep"
FULL, STRIP = "full", "strip"
LEFT, RIGHT = "left", "right"
LIVE, WATCH = "live", "watch"
TILES_MAX = GRID * GRID

# Every word it shows of its own (DESIGN-SPEC 9).
BOARD_HEAD = "Board · updated {time}"          # the label role: caps
MAIL_HIDDEN = "Mail text hidden on this screen"
NEEDS_SUB = "Waiting on Approvals, on the main screen"
BRIEFING_TITLE = "Briefing ready"
BRIEFING_SUB = "Read it on the main screen"
NEXT_LABEL = "Next"
VIDEO_CALL = "video call"
SETTING_LABEL = "Show Jarvis on my second screen"
NEEDS_MONITOR = "Needs a second monitor"
WINDOW_TITLE = "Jarvis on the second screen"

AMBER_WORD = "#E9C88E"         # NEEDS YOU's word on true black (second.html)
COUNT_AMBER = holo.AMBER_TX2   # "1 needs your OK"
RULE_INK = QColor(207, 230, 235, 31)    # the counts' rules: OLED_INK at 0.12

# Hide rules' reason words (the log's).
R_OFF, R_ONE_SCREEN, R_LOCKED, R_DISPLAY_OFF, R_BATTERY, R_FULL_SCREEN = (
    "off", "one_screen", "locked", "display_off", "battery_saver", "full_screen")

_VIDEO_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "webex.com", "whereby.com",
                "gotomeeting.com", "chime.aws", "bluejeans.com")


def needs_text(count: int) -> str:
    """"1 needs your OK" / "3 need your OK" (Home's chip and the HUD's words)."""
    count = max(0, int(count))
    return f"{count} needs your OK" if count == 1 else f"{count} need your OK"


def frames_text(count: int) -> str:
    count = max(0, int(count))
    return f"{count} frame on the board" if count == 1 else f"{count} frames on the board"


def pinned_text(count: int) -> str:
    return f"{max(0, int(count))} pinned"


# --------------------------------------------------------------------------
# Screens: which one it shows on (pure)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenSpot:
    """A screen as the display sees it: its key (the QScreen's name; a test's own), its whole geometry and its work
    area (logical px, the virtual desktop's)."""

    key: str
    geometry: QRect
    available: QRect | None = None

    def area(self) -> int:
        return self.geometry.width() * self.geometry.height()

    def work(self) -> QRect:
        return QRect(self.available) if self.available is not None else QRect(self.geometry)


def spots_of(screens: Iterable[Any]) -> list[ScreenSpot]:
    """The app's QScreens as ScreenSpots (in Qt's order)."""
    found: list[ScreenSpot] = []
    for index, screen in enumerate(screens):
        try:
            name = str(screen.name() or "") or f"screen{index}"
            found.append(ScreenSpot(f"{index}:{name}", QRect(screen.geometry()), QRect(screen.availableGeometry())))
        except RuntimeError:          # a screen being removed
            continue
    return found


def spot_at(spots: Sequence[ScreenSpot], rect: QRect | None) -> ScreenSpot | None:
    """The screen under ``rect``'s centre (a window's), else the one it overlaps most, else None."""
    if rect is None or not rect.isValid():
        return None
    centre = rect.center()
    for spot in spots:
        if spot.geometry.contains(centre):
            return spot
    best = max(spots, key=lambda spot: _overlap(spot.geometry, rect), default=None)
    return best if best is not None and _overlap(best.geometry, rect) > 0 else None


def _overlap(a: QRect, b: QRect) -> int:
    both = a.intersected(b)
    return both.width() * both.height() if both.isValid() else 0


def choose_screen(spots: Sequence[ScreenSpot], main: ScreenSpot | None) -> ScreenSpot | None:
    """The screen the display shows on: one that is not the main window's - with three or more, the largest of the
    others (the first of them on a tie) - or None with one screen. A main window on no known screen counts as on the
    first."""
    if len(spots) < 2:
        return None
    main_key = main.key if main is not None else spots[0].key
    others = [spot for spot in spots if spot.key != main_key]
    if not others:
        return None
    return max(others, key=lambda spot: spot.area())           # max keeps the first of equals


def far_edge(display: QRect, main: QRect | None) -> str:
    """The display's edge away from the main window's screen: RIGHT when it is to the right of it (or below or above
    it and not to its left), else LEFT."""
    if main is None or not main.isValid():
        return RIGHT
    return RIGHT if display.center().x() >= main.center().x() else LEFT


def screen_scale(width: float, height: float) -> float:
    """s = min(W / 1920, H / 1080)."""
    return max(0.25, min(float(width) / BASE_W, float(height) / BASE_H))


def strip_scale(width: float, height: float) -> float:
    """The strip's s: the screen's, but never under min(H / 1080, 1) - a narrow screen's strip needs no width, so its
    words keep the size a 1080 px tall screen gives them (a 1440 x 900 screen: 0.833, not 0.75)."""
    return max(screen_scale(width, height), min(float(height) / BASE_H, 1.0))


def strip_only(geometry: QRect) -> bool:
    return geometry.width() < STRIP_BELOW


def strip_rect(geometry: QRect, edge: str) -> QRect:
    """The strip on ``geometry``'s ``edge``: STRIP_W x the strip's s wide, the screen's whole height."""
    width = max(1, round(STRIP_W * strip_scale(geometry.width(), geometry.height())))
    width = min(width, geometry.width())
    x = geometry.x() + geometry.width() - width if edge == RIGHT else geometry.x()
    return QRect(x, geometry.y(), width, geometry.height())


# --------------------------------------------------------------------------
# The next event (pure): today's events still to start; the display picks the next one by its own clock
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class NextEvent:
    """A timed event still to start: its title, start, end and its place's words ("video call", a room, "")."""

    title: str
    start: datetime
    end: datetime | None = None
    place: str = ""

    def __repr__(self) -> str:          # never its title or place
        return f"NextEvent(start={self.start.isoformat(timespec='minutes')})"


def place_words(location: str) -> str:
    """"video call" for a location that is a meeting link, else its words without links (agenda.display_location)."""
    from .agenda import display_location

    text = str(location or "")
    shown = display_location(text)
    if any(host in text.casefold() for host in _VIDEO_HOSTS) and not shown:
        return VIDEO_CALL
    return shown


def upcoming_events(events: Iterable[Any], now: datetime, limit: int = 8) -> tuple[NextEvent, ...]:
    """The calendar's timed events (gcal.CalendarEvent) that start after ``now`` and on its day, soonest first (never
    one declined), at most ``limit``."""
    found: list[NextEvent] = []
    for event in events or ():
        start = getattr(event, "start", None)
        if start is None or getattr(event, "declined", False):
            continue
        try:
            local = start.astimezone(now.tzinfo) if now.tzinfo is not None and start.tzinfo is not None else start
            if local.tzinfo is None and now.tzinfo is not None:
                continue
            if local <= now or local.date() != now.date():
                continue
            end = getattr(event, "end", None)
            found.append(NextEvent(str(getattr(event, "title", "") or ""), local, end,
                                   place_words(str(getattr(event, "location", "") or ""))))
        except (TypeError, ValueError, OverflowError):
            continue
    found.sort(key=lambda item: item.start)
    return tuple(found[:max(0, int(limit))])


def pick_next(events: Sequence[NextEvent], now: datetime) -> NextEvent | None:
    for event in events:
        try:
            if event.start > now and event.start.date() == now.date():
                return event
        except TypeError:
            continue
    return None


def in_words(start: datetime, now: datetime) -> str:
    """"In 19 minutes", "In 1 minute", "In 1 hour", "In 2 h 15 min"."""
    minutes = max(0, math.ceil((start - now).total_seconds() / 60))
    if minutes < 60:
        return f"In {minutes} minute" if minutes == 1 else f"In {minutes} minutes"
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return f"In {hours} hour" if hours == 1 else f"In {hours} hours"
    return f"In {hours} h {rest} min"


def next_line(event: NextEvent, now: datetime) -> str:
    """"In 19 minutes · video call"."""
    parts = [in_words(event.start, now)]
    if event.place:
        parts.append(event.place)
    return " · ".join(parts)


# --------------------------------------------------------------------------
# The wall's frames (pure): the board's own frame dicts, checked as the page gets them, a mail's text taken out
# --------------------------------------------------------------------------

_HIDDEN_TEXT = "-"             # what a mail's or a text frame's words become before they reach the window (glance says
                               # they are hidden)


def private_frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    """``frame`` as the second screen may hold it: a mail thread's text and a text frame's paragraph (a web research
    answer: the line Jarvis speaks) replaced by a mark (the glance painter's private mode then says "Text hidden on
    this screen"; a text frame's title and source rows stay), a mail list's rows with no field but the ones it shows
    (sender, time, subject, meta, unread); part 3B's resting frames as counts only - "waits for your OK" with no card
    (no kind, no title: its count stays), Agents with no name and no times (each agent's state and its count of
    proposals that wait stay, and the counts in all); a to-dos frame's rows that wait for the owner's OK with no title
    (canvas part 5B, the ship-gate review's 1: its state stays - "WAITS" -, its words go, as on "waits for your OK");
    everything else as it is."""
    copy = dict(frame)
    body = frame.get("body")
    if not isinstance(body, Mapping):
        return copy
    kind = frame.get("kind")
    if kind in ("mail_thread", "text"):
        body = dict(body)
        if body.get("text"):
            body["text"] = _HIDDEN_TEXT
        copy["body"] = body
    elif kind == "approvals":
        copy["body"] = {"count": body.get("count"), "rows": []}
    elif kind == "todos":
        rows = body.get("rows")
        body = dict(body)
        body["rows"] = [dict(row, text=_HIDDEN_TEXT) if row.get("state") == "waits" else dict(row)
                        for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
        copy["body"] = body
    elif kind == "agents":
        rows = body.get("rows")
        copy["body"] = {"rows": [{"name": "", "state": row.get("state"), "last": "", "came": -1, "next": "",
                                  "waits": row.get("waits")} for row in rows if isinstance(row, Mapping)]
                        if isinstance(rows, list) else [],
                        "more": body.get("more"), "waits": body.get("waits")}
    elif kind == "mail_list":
        rows = body.get("rows")
        keep = ("sender", "time", "subject", "meta", "unread")
        body = dict(body)
        body["rows"] = [{key: row.get(key) for key in keep if key in row} for row in rows
                        if isinstance(row, Mapping)] if isinstance(rows, list) else []
        copy["body"] = body
    return copy


def wall_frames(feed: Any, limit: int = TILES_MAX) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The board's first ``limit`` frames in its order (board.feed.BoardFeed: the newest group first) that are on the
    board for the owner to see - not parked, closed or taken by a Clear - each as board.protocol rebuilds it for the
    page and made private (private_frame), and their pictures' data: URLs (protocol-checked), by frame id. A web
    search's frame (titled with the model's query) never comes here: told by the key it was built from
    (board.frames.SEARCH_KEY), never by its words. Never the lit frame, a backdrop or open big's state: only "frame"
    and "picture" messages exist here."""
    from .board import frames as board_frames
    from .board import protocol

    frames: list[dict[str, Any]] = []
    pictures: dict[str, str] = {}
    if feed is None:
        return frames, pictures
    try:
        ids = list(feed.frame_ids())
    except Exception:  # noqa: BLE001 - no board: nothing on the wall
        return frames, pictures
    for frame_id in ids:
        if len(frames) >= limit:
            break
        try:
            group = feed.group_of(frame_id)
            flags = feed.flags(frame_id)
            if not group or feed.is_parked(group) or flags.get("closed") or flags.get("cleared"):
                continue
            if str(feed.key_of(frame_id) or "").startswith(board_frames.SEARCH_KEY):
                continue                         # a web search: off this screen
            message = feed.frame_message(frame_id)
            if message is None:
                continue
            clean = protocol.check_message(message, {frame_id}, {group} if message.get("group") else ())
            frame = json.loads(clean.text)
        except Exception:  # noqa: BLE001 - one frame the page would refuse is not shown here either
            continue
        frames.append(private_frame(frame))
        try:
            picture = feed.picture_message(frame_id)
            if picture is not None:
                pictures[frame_id] = json.loads(protocol.check_message(picture, {frame_id}).text)["src"]
        except Exception:  # noqa: BLE001 - no picture on the tile rather than an error
            pass
    return frames, pictures


# --------------------------------------------------------------------------
# OLED care (pure: a clock passed in)
# --------------------------------------------------------------------------

class Care:
    """OLED care's state: quiet (since the last board change, state change or owner input), the dim stage, the drift's
    lattice step and the sides' swap. Times are seconds of the monotonic clock the caller passes."""

    def __init__(self, now: float) -> None:
        self.started = float(now)
        self.quiet_since = float(now)
        self.board_at = -math.inf
        self.swapped = False
        self.swap_due = float(now) + SWAP_S

    def touch(self, now: float, *, board: bool = False) -> None:
        """A board change (``board``), a state change or the owner's input: no longer quiet."""
        self.quiet_since = max(self.quiet_since, float(now))
        if board:
            self.board_at = float(now)

    def quiet_for(self, now: float) -> float:
        return max(0.0, float(now) - self.quiet_since)

    def stage(self, now: float) -> str:
        quiet = self.quiet_for(now)
        return DEEP if quiet >= DEEP_AFTER_S else DIM if quiet >= DIM_AFTER_S else LIT

    def level(self, now: float) -> float:
        return {LIT: 1.0, DIM: DIM_LEVEL, DEEP: DEEP_LEVEL}[self.stage(now)]

    def ring_interval(self, now: float, *, still: bool = False) -> int | None:
        """The ring's frame interval now (ms): 10 fps, 2 fps dimmed, None (still) in the deep dim or with motion
        off."""
        stage = self.stage(now)
        if still or stage == DEEP:
            return None
        return RING_DIM_MS if stage == DIM else RING_MS

    def drift_step(self, now: float) -> int:
        return int(max(0.0, float(now) - self.started) // DRIFT_S)

    def drift(self, now: float, scale: float = 1.0) -> tuple[int, int]:
        """The content's offset now (px): a point of the 3 x 3 lattice of DRIFT_PX x s, the next one every DRIFT_S."""
        dx, dy = _LATTICE[self.drift_step(now) % len(_LATTICE)]
        step = round(DRIFT_PX * max(0.25, float(scale)))
        return dx * step, dy * step

    def check_swap(self, now: float) -> bool:
        """True when the sides swapped now: every SWAP_S, never within SWAP_GUARD_S of a board change (then as soon as
        that minute is over)."""
        now = float(now)
        if now < self.swap_due:
            return False
        if now - self.board_at < SWAP_GUARD_S:
            self.swap_due = self.board_at + SWAP_GUARD_S
            return False
        self.swapped = not self.swapped
        self.swap_due = now + SWAP_S
        return True


# --------------------------------------------------------------------------
# The layout (pure geometry)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DisplayLayout:
    """Where everything goes in the window (its own px, before the drift). ``box``: the 16:9 box (FULL) or the strip
    itself (STRIP); ``ring``: the ring's box, its brackets included; ``column``: the left edge every line starts at and
    the column's width; the baselines of the state word, the clock and the date; ``counts_top`` (the strip: its counts'
    first rule; FULL: computed under NEXT); ``wall`` and its ``tiles`` (FULL with the board on; ``count_cells`` with it
    off: the counts' row)."""

    size: QSize
    mode: str
    scale: float
    box: QRectF
    ring: QRectF
    ring_px: int
    column: QRectF
    word_base: float
    clock_base: float
    date_base: float
    wall: QRectF | None = None
    head_base: float = 0.0
    tiles: tuple[QRectF, ...] = ()
    count_cells: tuple[QRectF, ...] = ()
    swapped: bool = False
    edge: str = RIGHT

    @property
    def strip(self) -> bool:
        return self.mode == STRIP


def ring_box(px: float) -> float:
    """The side of a ring's box with its open brackets (half-side 184 of the 360-unit box) and their 1.5 px pens."""
    return px * 368.0 / 360.0 + 1.5


def display_layout(width: int, height: int, *, mode: str = FULL, swapped: bool = False, board_on: bool = True,
                   edge: str = RIGHT, scale: float | None = None) -> DisplayLayout:
    """See DisplayLayout. FULL: the 16:9 box of 1920 x 1080 x s centred in ``width`` x ``height``; the column at x 96
    (mirrored when ``swapped``: the column on the right, the wall on the left). STRIP: the window is the strip itself,
    its content STRIP_PAD x s in (``scale``: the screen's s, else the strip's height's)."""
    width, height = max(1, int(width)), max(1, int(height))
    if mode == STRIP:
        s = float(scale) if scale is not None else strip_scale(BASE_W, height)
        box = QRectF(0.0, 0.0, float(width), float(height))
        px = max(36, round(STRIP_RING * s))
        side = ring_box(px)
        left = STRIP_PAD * s
        ring = QRectF(left, TOP * s, side, side)
        word = ring.bottom() + WORD_GAP * s
        clock = word + CLOCK_GAP * s
        return DisplayLayout(QSize(width, height), STRIP, s, box, ring, px,
                             QRectF(left, TOP * s, max(1.0, width - 2 * left), height - 2 * TOP * s), word, clock,
                             clock, edge=edge)
    s = screen_scale(width, height)
    box = QRectF((width - BASE_W * s) / 2, (height - BASE_H * s) / 2, BASE_W * s, BASE_H * s)

    def x_of(x: float, w: float) -> float:            # a rect's x in the box, mirrored when the sides swapped
        return box.x() + ((BASE_W - x - w) if swapped else x) * s

    px = round(RING * s)
    side = ring_box(px)
    column = QRectF(x_of(EDGE, COLUMN_W), box.y() + TOP * s, COLUMN_W * s, (BASE_H - 2 * TOP) * s)
    ring = QRectF(column.x(), column.y(), side, side)
    word = ring.bottom() + WORD_GAP * s
    clock = word + CLOCK_GAP * s
    date = clock + DATE_GAP * s
    wall_w = BASE_W - WALL_X - WALL_RIGHT
    wall = QRectF(x_of(WALL_X, wall_w), box.y() + TOP * s, wall_w * s, (BASE_H - 2 * TOP) * s)
    grid_top = wall.y() + GRID_TOP * s
    gap = TILE_GAP * s
    tile_w = (wall.width() - gap * (GRID - 1)) / GRID
    tile_h = (wall.bottom() - grid_top - gap * (GRID - 1)) / GRID
    cells = tuple(QRectF(wall.x() + col * (tile_w + gap), grid_top + row * (tile_h + gap), tile_w, tile_h)
                  for row in range(GRID) for col in range(GRID))
    count_cells = tuple(QRectF(wall.x() + col * (tile_w + gap), wall.y(), tile_w, tile_h) for col in range(GRID))
    return DisplayLayout(QSize(width, height), FULL, s, box, ring, px, column, word, clock, date,
                         wall=wall, head_base=wall.y() + HEAD_BASE * s, tiles=cells if board_on else (),
                         count_cells=() if board_on else count_cells, swapped=swapped, edge=edge)


# --------------------------------------------------------------------------
# Type and words
# --------------------------------------------------------------------------

_D, _B, _M = hud.DISPLAY_FAMILY, hud.BODY_FAMILY, hud.MONO_FAMILY
_ROLES: dict[str, tuple[str, float, int, float, bool]] = {    # family, px, weight, tracking (em), caps
    "word": (_M, 14, 500, 0.20, True),
    "clock": (_D, 120, 500, 0.01, False),
    "ampm": (_D, 28, 500, 0.0, False),
    "date": (_M, 15, 500, 0.30, True),
    "label": (_M, 14, 500, 0.20, True),
    "next_time": (_M, 23, 500, 0.0, False),
    "next_title": (_B, 23, 400, 0.0, False),
    "next_line": (_B, 16, 400, 0.0, False),
    "count": (_B, 20, 400, 0.0, False),
    "count_sub": (_B, 14, 400, 0.0, False),
    "note": (_B, 14, 400, 0.06, False),
}
_FONTS: dict[tuple[str, float], tuple[QFont, QFontMetricsF]] = {}


def role_font(role: str, scale: float, px: float | None = None) -> tuple[QFont, QFontMetricsF]:
    family, size, weight, tracking, _caps = _ROLES[role]
    pixel = max(1, round((px if px is not None else size) * scale))
    key = (f"{role}:{pixel}", 0.0)
    found = _FONTS.get(key)
    if found is None:
        font = QFont()
        font.setFamilies([family, *hud._FALLBACKS.get(family, ())])      # noqa: SLF001 - hud's own fallbacks
        font.setPixelSize(pixel)
        font.setWeight(QFont.Weight(weight))
        if tracking:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, pixel * tracking)
        if family == _M or role == "clock":
            try:
                font.setFeature(QFont.Tag("tnum"), 1)
            except (AttributeError, TypeError, ValueError):
                pass
        found = _FONTS[key] = (font, QFontMetricsF(font))
    return found


def shown(role: str, text: str) -> str:
    return text.upper() if _ROLES[role][4] else text


def wrap(text: str, metrics: QFontMetricsF, width: float) -> list[str]:
    """``text`` in lines at most ``width`` px wide, broken at spaces only; a word longer than a line breaks inside it,
    never right after a hyphen (DESIGN-SPEC 6)."""
    room = max(1.0, float(width))
    lines: list[str] = []
    current = ""
    for word in " ".join(str(text or "").split()).split(" "):
        trial = f"{current} {word}" if current else word
        if metrics.horizontalAdvance(trial) <= room:
            current = trial
            continue
        if current:
            lines.append(current)
        while len(word) > 1 and metrics.horizontalAdvance(word) > room:
            low, high = 1, len(word) - 1
            while low < high:
                middle = (low + high + 1) // 2
                if metrics.horizontalAdvance(word[:middle]) <= room:
                    low = middle
                else:
                    high = middle - 1
            if low > 1 and word[low - 1] in "-‐‑":
                low -= 1
            lines.append(word[:low])
            word = word[low:]
        current = word
    lines.append(current)
    return [line for line in lines if line] or [""]


def shorten(lines: list[str], most: int, metrics: QFontMetricsF, width: float) -> list[str]:
    """At most ``most`` lines, the last ending with "…" when lines were left out."""
    if len(lines) <= most:
        return lines
    kept = lines[:most]
    last = kept[-1].rstrip()
    while last and metrics.horizontalAdvance(last + "…") > width:
        last = last[:-1].rstrip()
    kept[-1] = last + "…"
    return kept


# --------------------------------------------------------------------------
# The OS seam (Win32 only on the real Windows platform outside the tests)
# --------------------------------------------------------------------------

class DisplayNative(Protocol):
    def no_activate(self, hwnd: int) -> bool:
        """WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW on the display's own window (never WS_EX_APPWINDOW, never topmost)."""

    def exclude_from_capture(self, hwnd: int) -> tuple[bool, str]:
        """WDA_EXCLUDEFROMCAPTURE on the display's own window: (done, a reason word when not)."""

    def watch_power(self, hwnd: int) -> None:
        """Hear the console display's state (on, off, dimmed) at the display's own window."""

    def native_message(self, message: int) -> bool:
        """A Windows message the display's window got (its MSG's address): True when it changed what the probes say."""

    def display_off(self) -> bool: ...

    def battery_saver(self) -> bool: ...

    def full_screen(self, hwnd: int) -> bool:
        """A full-screen app on the display window's monitor: a foreground window (not Jarvis's, not the desktop's)
        covering that whole monitor, or Windows' presentation or full-screen Direct3D state."""

    def release(self) -> None: ...


class NoNative:
    """Offscreen and in the tests: no OS call at all."""

    def no_activate(self, hwnd: int) -> bool:
        return False

    def exclude_from_capture(self, hwnd: int) -> tuple[bool, str]:
        return False, "no_native"

    def watch_power(self, hwnd: int) -> None:
        return None

    def native_message(self, message: int) -> bool:
        return False

    def display_off(self) -> bool:
        return False

    def battery_saver(self) -> bool:
        return False

    def full_screen(self, hwnd: int) -> bool:
        return False

    def release(self) -> None:
        return None


_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW, _WS_EX_TOPMOST, _WS_EX_NOACTIVATE = 0x80, 0x40000, 0x8, 0x08000000
_WDA_EXCLUDEFROMCAPTURE = 0x11
_WM_POWERBROADCAST, _PBT_POWERSETTINGCHANGE = 0x0218, 0x8013
_MONITOR_DEFAULTTONEAREST = 2
_SHELL_CLASSES = frozenset({"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"})
# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}
_DISPLAY_GUID = (0x6FE69556, 0x704A, 0x47A0, (0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))


class Win32Native:
    """user32's and kernel32's few calls on the display's OWN window (ctypes); refused while the desktop test guard is
    on. Nothing here changes anything outside Jarvis's windows: the ex-style and the capture setting are its window's,
    the power notification is sent to its window, and the rest only reads."""

    def __init__(self) -> None:
        if os.environ.get("JARVIS_DESKTOP_TEST_GUARD") == "1":
            raise RuntimeError("the second screen's OS calls are off while the tests run")
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
        user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        class PowerStatus(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

        class Guid(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(PowerStatus)]
        kernel32.GetSystemPowerStatus.restype = wintypes.BOOL
        kernel32.GetCurrentProcessId.argtypes = []
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        self._user32, self._kernel32 = user32, kernel32
        self._MonitorInfo, self._PowerStatus, self._Guid = MonitorInfo, PowerStatus, Guid
        data1, data2, data3, data4 = _DISPLAY_GUID
        self._guid = Guid(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
        self._power: Any = None
        self._display = 1                 # GUID_CONSOLE_DISPLAY_STATE: 0 off, 1 on, 2 dimmed (on until told)

    def no_activate(self, hwnd: int) -> bool:
        style = int(self._user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE))
        wanted = (style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW & ~_WS_EX_TOPMOST
        if wanted != style:
            self._user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, wanted)
        return True

    def exclude_from_capture(self, hwnd: int) -> tuple[bool, str]:
        if self._user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE):
            return True, ""
        return False, f"refused (winerror {self._ctypes.get_last_error()})"

    def watch_power(self, hwnd: int) -> None:
        if self._power is None:
            self._power = self._user32.RegisterPowerSettingNotification(hwnd, self._ctypes.byref(self._guid), 0) or None

    def native_message(self, message: int) -> bool:
        msg = self._wintypes.MSG.from_address(int(message))
        if msg.message != _WM_POWERBROADCAST or int(msg.wParam or 0) != _PBT_POWERSETTINGCHANGE or not msg.lParam:
            return False
        guid = self._Guid.from_address(int(msg.lParam))
        same = (guid.Data1, guid.Data2, guid.Data3, tuple(guid.Data4)) == (
            self._guid.Data1, self._guid.Data2, self._guid.Data3, tuple(self._guid.Data4))
        if not same:
            return False
        length = self._wintypes.DWORD.from_address(int(msg.lParam) + 16).value
        if length < 4:
            return False
        state = self._wintypes.DWORD.from_address(int(msg.lParam) + 20).value
        changed = state != self._display
        self._display = int(state)
        return changed

    def display_off(self) -> bool:
        return self._display == 0

    def battery_saver(self) -> bool:
        status = self._PowerStatus()
        if not self._kernel32.GetSystemPowerStatus(self._ctypes.byref(status)):
            return False
        return status.SystemStatusFlag == 1

    def full_screen(self, hwnd: int) -> bool:
        if _presentation_state() in (3, 4):           # full-screen Direct3D, presentation mode
            return True
        user32 = self._user32
        foreground = user32.GetForegroundWindow()
        if not foreground or int(foreground) == int(hwnd):
            return False
        pid = self._wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(foreground, self._ctypes.byref(pid))
        if int(pid.value) == int(self._kernel32.GetCurrentProcessId()):
            return False                               # one of Jarvis's own windows
        if user32.IsIconic(foreground) or not user32.IsWindowVisible(foreground):
            return False
        name = self._ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(foreground, name, 64)
        if name.value.casefold() in _SHELL_CLASSES:
            return False                               # the desktop or the taskbar
        info = self._MonitorInfo()
        info.cbSize = self._ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if not monitor or not user32.GetMonitorInfoW(monitor, self._ctypes.byref(info)):
            return False
        rect = self._wintypes.RECT()
        if not user32.GetWindowRect(foreground, self._ctypes.byref(rect)):
            return False
        screen = info.rcMonitor
        return (rect.left <= screen.left and rect.top <= screen.top and rect.right >= screen.right
                and rect.bottom >= screen.bottom)

    def release(self) -> None:
        if self._power is not None:
            self._user32.UnregisterPowerSettingNotification(self._power)
            self._power = None


def _presentation_state() -> int:
    """SHQueryUserNotificationState's answer (3 full-screen Direct3D, 4 presentation mode ...); 0 when unknown."""
    try:
        import ctypes

        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return int(state.value)
    except Exception:  # noqa: BLE001 - unknown: none
        pass
    return 0


def default_native() -> DisplayNative:
    """``Win32Native`` on the real Windows platform outside the tests; ``NoNative`` offscreen and in the tests."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows" \
            or os.environ.get("JARVIS_DESKTOP_TEST_GUARD") == "1":
        return NoNative()
    try:
        return Win32Native()
    except (OSError, AttributeError, RuntimeError) as exc:
        logger.info("Second screen: no native window calls (%s)", type(exc).__name__)
        return NoNative()


@dataclass
class HideProbes:
    """When the display hides (each answers now; any that fails counts as no)."""

    locked: Callable[[], bool] = lambda: False
    display_off: Callable[[], bool] = lambda: False
    battery_saver: Callable[[], bool] = lambda: False
    full_screen: Callable[[], bool] = lambda: False

    def reason(self) -> str:
        """The first rule that holds now ("" none): locked, display_off, battery_saver, full_screen."""
        for word, probe in ((R_LOCKED, self.locked), (R_DISPLAY_OFF, self.display_off),
                            (R_BATTERY, self.battery_saver), (R_FULL_SCREEN, self.full_screen)):
            try:
                if probe():
                    return word
            except Exception as exc:  # noqa: BLE001 - a probe that can't answer never hides it
                logger.debug("Second screen: a probe failed (%s)", type(exc).__name__)
        return ""


# --------------------------------------------------------------------------
# The placement broker: "the other screen"
# --------------------------------------------------------------------------

class PlacementBroker(QObject):
    """See the module doc. The display tells it where it is (set_display); the LIVE pop-out and the watch window are
    followed (watch) or say so themselves (claim); room() is what a window of theirs gets on a screen."""

    changed = Signal()                 # the display folds (or comes back)

    def __init__(self, parent: QObject | None = None, *, return_ms: int = RETURN_MS) -> None:
        super().__init__(parent)
        self._display: QRect | None = None
        self._edge = RIGHT
        self._held: dict[str, QRect] = {}
        self._folded = False
        self._watched: dict[str, weakref.ref[QWidget]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(return_ms)))
        self._timer.timeout.connect(self._settle)

    # ---- the display's side ----------------------------------------------------------------------------------------

    def set_display(self, screen: QRect | None, edge: str = RIGHT) -> None:
        """The display is on ``screen`` (that screen's whole geometry), its strip on ``edge``; None: it is off (room()
        is the work area as it was)."""
        screen = QRect(screen) if screen is not None and screen.isValid() else None
        if screen == self._display and edge == self._edge:
            return
        self._display, self._edge = screen, edge if edge in (LEFT, RIGHT) else RIGHT
        self._refresh(at_once=True)

    def display_screen(self) -> QRect | None:
        return QRect(self._display) if self._display is not None else None

    def folded(self) -> bool:
        """The display shows only its strip: the pop-out or the watch window is on its screen (or left less than
        RETURN_MS ago)."""
        return self._folded

    def holders(self) -> dict[str, QRect]:
        """Who is on the display's screen now (LIVE, WATCH) and where."""
        if self._display is None:
            return {}
        return {who: QRect(rect) for who, rect in self._held.items() if self._display.contains(rect.center())}

    # ---- the windows' side -----------------------------------------------------------------------------------------

    def room(self, screen: QRect, area: QRect, who: str = WATCH) -> QRect:
        """The work ``area`` a window of ``who`` gets on the screen whose whole geometry is ``screen``: without the
        display's strip when the display is on that screen; ``area`` as it is otherwise."""
        area = QRect(area)
        display = self._display
        if display is None or not screen.isValid() or not display.contains(QRect(screen).center()):
            return area
        strip = strip_rect(display, self._edge)
        if self._edge == RIGHT:
            right = min(area.x() + area.width(), strip.x())
            return QRect(area.x(), area.y(), max(1, right - area.x()), area.height())
        left = max(area.x(), strip.x() + strip.width())
        return QRect(left, area.y(), max(1, area.x() + area.width() - left), area.height())

    def claim(self, who: str, rect: QRect | None) -> None:
        """``who``'s window is at ``rect`` (shown, not minimised); None: it is gone (hidden, closed, minimised)."""
        if rect is None or not rect.isValid():
            if self._held.pop(who, None) is None:
                return
        else:
            if self._held.get(who) == rect:
                return
            self._held[who] = QRect(rect)
        self._refresh()

    def watch(self, window: QWidget, who: str) -> None:
        """Follow ``window`` (the pop-out, the watch window): where it is while it shows (once per window)."""
        known = self._watched.get(who)
        if known is not None and known() is window:
            self._follow(who)
            return
        self._watched[who] = weakref.ref(window)
        window.installEventFilter(self)
        self._follow(who)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize,
                            QEvent.Type.Close, QEvent.Type.WindowStateChange):
            for who, ref in list(self._watched.items()):
                if ref() is watched:
                    QTimer.singleShot(0, self, lambda who=who: self._follow(who))
        return False

    def _follow(self, who: str) -> None:
        ref = self._watched.get(who)
        window = ref() if ref is not None else None
        try:
            if window is None or not shiboken6.isValid(window) or not window.isVisible() or window.isMinimized():
                self.claim(who, None)
                return
            self.claim(who, window.frameGeometry())
        except RuntimeError:
            self.claim(who, None)

    def _refresh(self, *, at_once: bool = False) -> None:
        wanted = bool(self.holders())
        if wanted:
            self._timer.stop()
            if not self._folded:
                self._folded = True
                logger.info("Second screen: folded to its strip (%s)", ", ".join(sorted(self.holders())))
                self.changed.emit()
        elif self._folded:
            if at_once:
                self._timer.stop()
                self._folded = False
                self.changed.emit()
            elif not self._timer.isActive():
                self._timer.start()              # it comes back RETURN_MS after they left

    def _settle(self) -> None:
        if self._folded and not self.holders():
            self._folded = False
            logger.info("Second screen: back in full")
            self.changed.emit()

    def reset(self) -> None:
        """Nothing held and no display (tests; the app never needs it)."""
        self._timer.stop()
        self._held.clear()
        self._display = None
        self._folded = False


_BROKER: PlacementBroker | None = None


def broker() -> PlacementBroker:
    """The one placement broker of this run (the pop-out's and the watch window's placement ask it)."""
    global _BROKER
    if _BROKER is None or not shiboken6.isValid(_BROKER):
        _BROKER = PlacementBroker()
    return _BROKER


# --------------------------------------------------------------------------
# What the display shows (pushed by the app)
# --------------------------------------------------------------------------

@dataclass
class DisplayState:
    """Everything the window paints, pushed by the controller (never read from the network, mail or files)."""

    look: tuple[str, bool, bool] = (orb_mod.IDLE, False, False)
    voice: bool = False
    recording: bool = False
    word: str = orb_mod.READOUTS[orb_mod.IDLE]
    tone: str = "holo"
    needs: int = 0
    briefing: bool = False
    frames: int = 0
    pinned: int = 0
    board_on: bool = True
    events: tuple[NextEvent, ...] = ()
    tiles: tuple[dict[str, Any], ...] = ()
    pictures: dict[str, str] = field(default_factory=dict)
    updated: str = ""
    hour24: bool = False

    def __repr__(self) -> str:          # counts and kinds only
        return (f"DisplayState(look={self.look[0]!r}, needs={self.needs}, frames={self.frames}, "
                f"tiles={len(self.tiles)}, events={len(self.events)})")


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

DISPLAY_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.WindowTransparentForInput)


class SecondScreen(QWidget):
    """See the module doc. ``now()`` is the wall clock the clock and NEXT read; ``clock()`` OLED care's monotonic one."""

    def __init__(self, *, native: DisplayNative | None = None, now: Callable[[], datetime] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(None, DISPLAY_FLAGS)
        self.setObjectName("secondScreen")
        self.setWindowTitle(WINDOW_TITLE)
        self.setAccessibleName(WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        self._native: DisplayNative = native if native is not None else default_native()
        self._now = now or (lambda: datetime.now().astimezone())
        self._clock = clock
        self.state = DisplayState()
        self.care = Care(clock())
        self._mode = FULL
        self._edge = RIGHT
        self._scale_hint: float | None = None
        self._layout: DisplayLayout | None = None
        self._prepared = 0               # the native window last given its ex-style and capture setting
        self.excluded = False            # Windows left it out of capture
        self.refusal = ""                # ... else why not (a reason word)
        self._images: dict[str, tuple[str, QImage | None]] = {}
        self._frozen: float | None = None
        self.painter_class: type[QPainter] = QPainter     # tests: a painter that records what is drawn
        self.paint_ms: deque[float] = deque(maxlen=120)   # the ring's own paints (ms)
        self.full_ms: deque[float] = deque(maxlen=60)     # whole paints (ms)
        # The ring: a hidden OrbWidget is its clock and its cached layers; it is never shown (the window paints it).
        self.ring = orb_mod.OrbWidget(RING, self)
        self.ring.hide()
        self.ring.set_animated(False)
        self.ring.set_badge_form(True)    # while Home is the canvas: the canvas orb's flat JARVIS badge (OLED care's)
        self._ring_timer = QTimer(self)
        self._ring_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ring_timer.timeout.connect(self._ring_frame)

    # ---- where and how it shows ----------------------------------------------------------------------------------

    def mode(self) -> str:
        return self._mode

    def edge(self) -> str:
        return self._edge

    def place(self, geometry: QRect, *, mode: str = FULL, edge: str = RIGHT, scale: float | None = None) -> None:
        """The window on ``geometry`` (the screen's, or the strip's), in ``mode``; ``scale``: the screen's s (the
        strip keeps the screen's type)."""
        changed = (mode, edge, scale) != (self._mode, self._edge, self._scale_hint)
        self._mode, self._edge, self._scale_hint = mode, edge, scale
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        if changed:
            self._layout = None
            self.update()

    def show_quietly(self) -> None:
        """Shown without taking the focus or bringing anything forward (never a raise, never topmost)."""
        handle = self.windowHandle()
        if handle is not None:
            handle.setProperty("_q_showWithoutActivating", True)
        if not self.isVisible():
            focus = QGuiApplication.focusWindow()
            self._focus_before = weakref.ref(focus) if focus is not None else None
            self.show()
        self._sync_ring()

    _focus_before: weakref.ref[Any] | None = None

    def event(self, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.WindowActivate:
            QTimer.singleShot(0, self, self._decline_focus)
        return super().event(event)

    def _decline_focus(self) -> None:
        """It never keeps the focus. Windows never gives it (WS_EX_NOACTIVATE, shown without activating); a platform
        that gives every window it shows the focus anyway (Qt's offscreen one) gets it handed back to the window that
        had it."""
        if not shiboken6.isValid(self) or QGuiApplication.focusWindow() is not self.windowHandle():
            return
        previous = self._focus_before() if self._focus_before is not None else None
        if previous is not None and shiboken6.isValid(previous) and previous.isVisible():
            previous.requestActivate()

    def layout_now(self) -> DisplayLayout:
        if self._layout is None or self._layout.size != self.size():
            self._layout = display_layout(self.width(), self.height(), mode=self._mode, swapped=self.care.swapped,
                                          board_on=self.state.board_on, edge=self._edge, scale=self._scale_hint)
        return self._layout

    def set_state(self, state: DisplayState) -> None:
        """What it shows now (the controller decides what changed counts as quiet's end). The decoded pictures it keeps
        are the wall's own now: one that left the wall (or changed) is let go."""
        board_changed = state.board_on != self.state.board_on
        self.state = state
        self._images = {frame_id: cached for frame_id, cached in self._images.items()
                        if state.pictures.get(frame_id) == cached[0]}
        if board_changed:
            self._layout = None
        look, muted, offline = state.look
        self.ring.set_voice_listening(state.voice)
        self.ring.set_look(look, muted=muted, offline=offline)
        self.ring.set_recording(state.recording)
        self.update()
        self._sync_ring()

    def freeze(self, seconds: float | None) -> None:
        """The ring still at ``seconds`` of its own clock (stills, the deep dim); None: it moves again."""
        self._frozen = seconds
        self.ring.set_phase(seconds)
        self._sync_ring()
        self.update()

    _seen: tuple[tuple[int, int], bool, str] | None = None     # the drift, the swap and the stage last painted

    def care_tick(self, now: float | None = None) -> bool:
        """OLED care's step (the controller's timer, every DRIFT_S): the drift, the swap, the dim stage - the ring
        still from the deep dim on, moving again when it ends. True when the picture changed."""
        now = self._clock() if now is None else float(now)
        if self.care.check_swap(now) and self._mode == FULL:
            self._layout = None
        return self._settle_care(now)

    def _settle_care(self, now: float) -> bool:
        seen = (self.care.drift(now, self.layout_now().scale), self.care.swapped, self.care.stage(now))
        before = self._seen
        self._seen = seen
        was_deep = before is not None and before[2] == DEEP
        if self._frozen is None:
            if seen[2] == DEEP and not was_deep:
                self.ring.set_phase(self.ring.sample().t)     # the deep dim: the ring stands still as it is
            elif seen[2] != DEEP and was_deep:
                self.ring.set_phase(None)
        self._sync_ring(now)
        if seen != before:
            self.update()
            return True
        return False

    def touch(self, now: float | None = None, *, board: bool = False) -> None:
        """A board change, a state change or the owner's input: lit again at once."""
        now = self._clock() if now is None else float(now)
        self.care.touch(now, board=board)
        self._settle_care(now)

    # ---- the ring's clock --------------------------------------------------------------------------------------------

    def ring_interval(self, now: float | None = None) -> int | None:
        still = holo.motion_level() == holo.MOTION_OFF or self._frozen is not None
        return self.care.ring_interval(self._clock() if now is None else now, still=still)

    def _sync_ring(self, now: float | None = None) -> None:
        interval = self.ring_interval(now) if self.isVisible() else None
        if interval is None:
            self._ring_timer.stop()
            return
        if self._ring_timer.interval() != interval:
            self._ring_timer.setInterval(interval)
        if not self._ring_timer.isActive():
            self._ring_timer.start()

    def ring_timer(self) -> QTimer:
        return self._ring_timer

    def _ring_frame(self) -> None:
        if not self.isVisible():
            self._ring_timer.stop()
            return
        if self._settle_care(self._clock()):
            return          # a drift step or a dim stage came: the whole display was asked for, and the ring re-timed
        self.update(self._ring_dirty())

    def _ring_dirty(self) -> QRect:
        layout = self.layout_now()
        dx, dy = self.care.drift(self._clock(), layout.scale)
        return layout.ring.translated(dx, dy).toAlignedRect().adjusted(-2, -2, 2, 2)

    # ---- painting -----------------------------------------------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        started = time.perf_counter()
        area = QRectF(event.rect())
        painter = self.painter_class(self)
        try:
            ring_only = self.paint_display(painter, area)
        finally:
            painter.end()
        took = (time.perf_counter() - started) * 1000.0
        (self.paint_ms if ring_only else self.full_ms).append(took)

    def paint_display(self, painter: QPainter, area: QRectF | None = None, *, now: datetime | None = None,
                      at: float | None = None) -> bool:
        """Everything in ``area`` (None: the whole window) on true black; True when only the ring was painted (a ring
        frame). ``now``, ``at``: the wall clock and OLED care's (stills, tests)."""
        layout = self.layout_now()
        at = self._clock() if at is None else float(at)
        wall_now = self._now() if now is None else now
        dx, dy = self.care.drift(at, layout.scale)
        full = QRectF(self.rect())
        area = QRectF(area) if area is not None else full
        painter.fillRect(area, QColor(holo.OLED_BLACK))
        dirty = QRectF(layout.ring.translated(dx, dy).toAlignedRect().adjusted(-2, -2, 2, 2))
        ring_only = area != full and dirty.adjusted(-1, -1, 1, 1).contains(area)
        stage = self.care.stage(at)
        painter.save()
        try:
            painter.setClipRect(area)
            painter.translate(dx, dy)
            self._paint_ring(painter, layout)
            if not ring_only:
                if stage == DEEP:
                    self._paint_clock(painter, layout, wall_now)
                else:
                    self._paint_column(painter, layout, wall_now)
                    if layout.mode == FULL:
                        self._paint_wall(painter, layout)
        finally:
            painter.restore()
        level = self.care.level(at)
        if level < 1.0:                              # dimmed: black laid over it (black stays black)
            painter.fillRect(area, QColor(0, 0, 0, round(255 * (1.0 - level))))
        return ring_only

    def _paint_ring(self, painter: QPainter, layout: DisplayLayout) -> None:
        ring = self.ring
        if ring.orb_size() != layout.ring_px:
            ring.set_orb_size(layout.ring_px)
        side = ring.width()
        # The ring's widget box is centred on its brackets' box (whose left and top edges are the column's).
        origin = QPointF(layout.ring.center().x() - side / 2, layout.ring.center().y() - side / 2)
        frame = ring.sample()
        frame.glow = 0.0                              # OLED care: no glow ...
        frame.oled = True                             # (the badge: no glow, nothing over OLED_INK)
        frame.core_op *= CORE_SHARE                   # ... the core at 50 % ...
        frame.bands = tuple((look, weight, alphas, tuple(orb_mod.C_HI if color == orb_mod.C_WHITE else color
                                                         for color in colors))
                            for look, weight, alphas, colors in frame.bands)   # ... and no white over OLED_INK
        ring.paint_into(painter, origin, frame, dpr=self.devicePixelRatioF() or 1.0)

    def _text(self, painter: QPainter, role: str, x: float, baseline: float, text: str, color: Any,
              px: float | None = None, scale: float = 1.0) -> float:
        """One line of ``role`` with its left edge at ``x`` on ``baseline``; returns its advance."""
        font, metrics = role_font(role, scale, px)
        words = shown(role, text)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QPointF(x, baseline), words)
        return metrics.horizontalAdvance(words)

    def _word_tone(self) -> tuple[str, str]:
        tone = self.state.tone
        if tone == "amber":
            return AMBER_WORD, holo.AMBER
        if tone == "steel":
            return holo.INK_4, holo.STEEL
        return holo.INK_3, holo.HOLO

    def _paint_clock(self, painter: QPainter, layout: DisplayLayout, now: datetime) -> float:
        """The clock (and AM / PM): returns its baseline."""
        from .text_prep import clock_parts

        s = layout.scale
        digits, meridiem = clock_parts(now, hour24=self.state.hour24)
        x = layout.column.x()
        px = 120.0
        room = layout.column.width()
        font, metrics = role_font("clock", s, px)
        small, small_metrics = role_font("ampm", s)
        need = metrics.horizontalAdvance(digits) + (12 * s + small_metrics.horizontalAdvance(meridiem)
                                                    if meridiem else 0.0)
        if need > room:                                # the strip: smaller, never cut
            px = max(48.0, px * room / need)
        width = self._text(painter, "clock", x, layout.clock_base, digits, holo.OLED_INK, px=px, scale=s)
        if meridiem:
            self._text(painter, "ampm", x + width + 12 * s, layout.clock_base, meridiem, holo.INK_3, scale=s)
        return layout.clock_base

    def _paint_column(self, painter: QPainter, layout: DisplayLayout, now: datetime) -> None:
        s = layout.scale
        x = layout.column.x()
        word_color, mark_color = self._word_tone()
        mark = 10 * s
        if self.state.tone == "amber":
            holo.paint_icon(painter, QRectF(x, layout.word_base - 9.5 * s, mark, mark), "diamond", mark_color)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(mark_color))
            painter.drawEllipse(QPointF(x + 4 * s, layout.word_base - 4.5 * s), 4 * s, 4 * s)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        self._text(painter, "word", x + mark + 10 * s, layout.word_base, self.state.word, word_color, scale=s)
        self._paint_clock(painter, layout, now)
        if layout.mode == STRIP:
            top = layout.clock_base + COUNTS_GAP_NO_NEXT * s
            self._paint_counts(painter, layout, top, layout.column.width())
            return
        self._text(painter, "date", x, layout.date_base, _date_words(now), holo.INK_3, scale=s)
        bottom = layout.date_base
        event = pick_next(self.state.events, now)
        if event is not None:
            bottom = self._paint_next(painter, layout, event, now)
            top = bottom + COUNTS_GAP * s
        else:
            top = layout.date_base + COUNTS_GAP_NO_NEXT * s
        if self.state.board_on:
            self._paint_counts(painter, layout, top, layout.column.width())

    def _paint_next(self, painter: QPainter, layout: DisplayLayout, event: NextEvent, now: datetime) -> float:
        """NEXT, the event's time and title, and its line: returns the last line's baseline."""
        from .text_prep import format_time

        s = layout.scale
        x = layout.column.x()
        room = layout.column.width()
        label = layout.date_base + NEXT_GAP * s
        self._text(painter, "label", x, label, NEXT_LABEL, holo.INK_4, scale=s)
        baseline = label + NEXT_TITLE_GAP * s
        when = format_time(event.start, hour24=self.state.hour24).split(" ")[0]
        width = self._text(painter, "next_time", x, baseline, when, holo.HOLO, scale=s) + 12 * s
        font, metrics = role_font("next_title", s)
        first = wrap(event.title, metrics, room - width)
        lines = [first[0]]
        rest = " ".join(first[1:])
        if rest:
            lines += wrap(rest, metrics, room)
        lines = shorten(lines, NEXT_TITLE_LINES, metrics, room)
        for index, line in enumerate(lines):
            self._text(painter, "next_title", x + (width if index == 0 else 0.0), baseline, line, holo.OLED_INK,
                       scale=s)
            if index < len(lines) - 1:
                baseline += NEXT_TITLE_LINE * s
        baseline += NEXT_LINE_GAP * s
        _font, line_metrics = role_font("next_line", s)
        for index, line in enumerate(shorten(wrap(next_line(event, now), line_metrics, room), 2, line_metrics, room)):
            if index:
                baseline += NEXT_LINE_LINE * s
            self._text(painter, "next_line", x, baseline, line, holo.INK_3, scale=s)
        return baseline

    def counts_rows(self) -> list[tuple[str, str, str, bool]]:
        """(shape, title, words, amber) of each count that has something to say, in the column's order."""
        state = self.state
        rows: list[tuple[str, str, str, bool]] = []
        if state.needs > 0:
            rows.append(("diamond", needs_text(state.needs), NEEDS_SUB, True))
        if state.briefing:
            rows.append(("dot", BRIEFING_TITLE, BRIEFING_SUB, False))
        if state.board_on and state.frames > 0:
            rows.append(("board", frames_text(state.frames), pinned_text(state.pinned) if state.pinned else "", False))
        return rows

    def _paint_counts(self, painter: QPainter, layout: DisplayLayout, top: float, width: float) -> None:
        rows = self.counts_rows()
        if not rows:
            return
        s = layout.scale
        x = layout.column.x()
        limit = layout.box.y() + layout.box.height() - TOP * s * (0.5 if layout.mode == STRIP else 1.0)
        self._rule(painter, x, top, width)
        for shape, title, words, amber in rows:
            end = self._paint_count(painter, s, x, top, width, shape, title, words, amber, limit)
            if end is None:
                break
            top = end
            self._rule(painter, x, top, width)

    def _rule(self, painter: QPainter, x: float, y: float, width: float) -> None:
        painter.fillRect(QRectF(x, round(y), width, 1.0), RULE_INK)

    def _paint_count(self, painter: QPainter, s: float, x: float, top: float, width: float, shape: str, title: str,
                     words: str, amber: bool, limit: float) -> float | None:
        """One count under the rule at ``top``: its shape and title, then its words; returns the next rule's y (None
        when it would pass ``limit``: left out)."""
        indent = COUNT_SHAPE * s
        _font, title_metrics = role_font("count", s)
        _font, sub_metrics = role_font("count_sub", s)
        titles = wrap(title, title_metrics, width - indent)
        subs = wrap(words, sub_metrics, width - indent) if words else []
        base = top + COUNT_TITLE * s
        end = base + (len(titles) - 1) * COUNT_TITLE_LINE * s
        if subs:
            end += COUNT_SUB * s + (len(subs) - 1) * COUNT_SUB_LINE * s
        end += COUNT_AFTER * s
        if end > limit:
            return None
        middle = base - 6.5 * s
        if shape == "diamond":
            holo.paint_icon(painter, QRectF(x, middle - 6 * s, 12 * s, 12 * s), "diamond", holo.AMBER)
        elif shape == "dot":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(holo.HOLO))
            painter.drawEllipse(QPointF(x + 4 * s, middle), 4 * s, 4 * s)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            holo.paint_icon(painter, QRectF(x, middle - 8 * s, 16 * s, 16 * s), "board", holo.INK_3)
        y = base
        for index, line in enumerate(titles):
            if index:
                y += COUNT_TITLE_LINE * s
            self._text(painter, "count", x + indent, y, line, COUNT_AMBER if amber else holo.OLED_INK, scale=s)
        if subs:
            y += COUNT_SUB * s
            for index, line in enumerate(subs):
                if index:
                    y += COUNT_SUB_LINE * s
                self._text(painter, "count_sub", x + indent, y, line, holo.INK_4, scale=s)
        return end

    def _paint_wall(self, painter: QPainter, layout: DisplayLayout) -> None:
        state = self.state
        wall = layout.wall
        if wall is None:
            return
        s = layout.scale
        if not state.board_on:                       # the board off: the counts' row alone
            for cell, (shape, title, words, amber) in zip(layout.count_cells, self.counts_rows()):
                self._paint_count(painter, s, cell.x(), cell.y() - COUNT_TITLE * s + 20 * s, cell.width(), shape,
                                  title, words, amber, cell.bottom())
            return
        if not state.tiles:
            return
        head = BOARD_HEAD.format(time=state.updated) if state.updated else "Board"
        self._text(painter, "label", wall.x(), layout.head_base, head, holo.INK_3, scale=s)
        _font, metrics = role_font("note", s)
        note_w = metrics.horizontalAdvance(MAIL_HIDDEN)
        right = wall.right()
        self._text(painter, "note", right - note_w, layout.head_base, MAIL_HIDDEN, holo.INK_3, scale=s)
        glyph = 16 * s
        holo.paint_icon(painter, QRectF(right - note_w - 8 * s - glyph, layout.head_base - 12.5 * s, glyph, glyph),
                        "hidden", holo.INK_3)
        for frame, cell in zip(state.tiles, layout.tiles):
            picture = self._picture(str(frame.get("id", "")))
            glance.paint_tile(painter, cell, frame, scale=s, private=True, picture=picture, ground=False,
                              screen=glance.SECOND)

    def drop_pictures(self) -> None:
        """Let every decoded picture go (switched off, one screen left, Jarvis closing): the next paint decodes what
        the wall has then."""
        self._images.clear()

    def _picture(self, frame_id: str) -> QImage | None:
        """A tile's picture, decoded once per picture (tile_picture: a JPEG of at most its 4 MP, else none)."""
        src = self.state.pictures.get(frame_id, "")
        if not src:
            return None
        cached = self._images.get(frame_id)
        if cached is None or cached[0] != src:
            from .board.view import tile_picture

            cached = (src, tile_picture(src))
            self._images[frame_id] = cached
        return cached[1]

    # ---- Qt ------------------------------------------------------------------------------------------------------------

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            handle.setProperty("_q_showWithoutActivating", True)
        self._prepare_native()
        self._sync_ring()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._ring_timer.stop()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._layout = None

    def _prepare_native(self) -> None:
        """Once per native window: never activated (its ex-style), never in capture (if Windows agrees), its power
        state heard."""
        hwnd = int(self.winId())
        if not hwnd or hwnd == self._prepared:
            return
        self._prepared = hwnd
        try:
            self._native.no_activate(hwnd)
            done, why = self._native.exclude_from_capture(hwnd)
            self._native.watch_power(hwnd)
        except Exception as exc:  # noqa: BLE001 - the display still shows
            done, why = False, type(exc).__name__
        self.excluded, self.refusal = bool(done), "" if done else str(why)
        if not done and why != "no_native":
            logger.warning("Second screen: Windows did not leave it out of capture (%s); it shows anyway", why)

    def nativeEvent(self, event_type: Any, message: Any) -> Any:  # noqa: N802 - Qt override
        try:
            if bytes(event_type) == b"windows_generic_MSG" and self._native.native_message(int(message)):
                QTimer.singleShot(0, self, self._native_changed)
        except Exception:  # noqa: BLE001 - a message this cannot read changes nothing
            pass
        return super().nativeEvent(event_type, message)

    _changed_hook: Callable[[], None] | None = None

    def _native_changed(self) -> None:
        hook = self._changed_hook
        if hook is not None:
            hook()

    def shutdown(self) -> None:
        self._ring_timer.stop()
        self.drop_pictures()
        try:
            self._native.release()
        except Exception:  # noqa: BLE001
            pass
        self.hide()


def _date_words(now: datetime) -> str:
    """"Tuesday 17 November" (the date role makes it caps)."""
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December")
    return f"{days[now.weekday()]} {now.day} {months[now.month - 1]}"


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------

class _InputWatch(QObject):
    """The owner's input to Jarvis's window (keys, clicks, the wheel, a move of the pointer): quiet ends."""

    _TYPES = frozenset({QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                        QEvent.Type.MouseMove})

    def __init__(self, note: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._note = note

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() in self._TYPES:
            self._note()
        return False


class SecondScreenController(QObject):
    """See the module doc. ``host``: the app (ui.AppController); ``look``: its LookController. Injectable for the
    tests: ``screens()`` (ScreenSpots), ``main_rect()`` (the main window's frame), ``native``, ``probes``, ``clock``
    (monotonic), ``now`` (the wall clock), ``board()`` (the board's feed or None), ``broker``."""

    availabilityChanged = Signal(bool)     # a second monitor came or went: Settings > Look's switch follows it
    shownChanged = Signal(bool)            # the display showed or hid

    def __init__(self, host: Any, *, look: Any = None, screens: Callable[[], Sequence[ScreenSpot]] | None = None,
                 main_rect: Callable[[], QRect | None] | None = None, native: DisplayNative | None = None,
                 probes: HideProbes | None = None, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None, board: Callable[[], Any] | None = None,
                 placement: PlacementBroker | None = None, care_ms: int = int(DRIFT_S * 1000)) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.look = look
        self._screens = screens or (lambda: spots_of(QGuiApplication.screens()))
        self._main_rect = main_rect or self._default_main_rect
        self._native = native
        self._clock = clock
        self._now = now or self._default_now
        self._board = board or (lambda: None)
        self.broker = placement if placement is not None else broker()
        self.probes = probes if probes is not None else self._default_probes()
        self.window: SecondScreen | None = None
        self.state = DisplayState(hour24=self._hour24())
        self._available = len(self._screens()) >= 2
        self._target: ScreenSpot | None = None
        self._reason = R_OFF
        self._shown = False
        self._events: tuple[NextEvent, ...] = ()
        self._shut = False
        self._care = QTimer(self)
        self._care.setInterval(max(1, int(care_ms)))
        self._care.timeout.connect(self._on_care)
        self._minute = QTimer(self)
        self._minute.setSingleShot(True)
        self._minute.timeout.connect(self._on_minute)
        self._input = _InputWatch(self.note_input, self)
        self._watched_window: Any = None
        self.broker.changed.connect(self.sync)
        app = QGuiApplication.instance()
        if app is not None:
            for signal in (app.screenAdded, app.screenRemoved, app.primaryScreenChanged):
                signal.connect(self._on_screens)
        reading = getattr(getattr(host, "window", None), "reading", None)
        if reading is not None and hasattr(reading, "glanceChanged"):
            reading.glanceChanged.connect(self.sync_glance)
        self._watch_main()
        self.sync_glance()
        # The first decision waits for the event loop: assistant.json is the app's to open first (at its start), never
        # read while the app is being made.
        QTimer.singleShot(0, self, self.sync)

    def __repr__(self) -> str:
        return f"SecondScreenController(shown={self._shown}, reason={self._reason!r})"

    def use(self, *, screens: Callable[[], Sequence[ScreenSpot]] | None = None,
            main_rect: Callable[[], QRect | None] | None = None, probes: HideProbes | None = None,
            native: DisplayNative | None = None) -> None:
        """Tests and the screen harness: fake screens, the main window's place, hide probes or the OS seam (a window
        made after this uses ``native``). Then decides again (sync)."""
        if screens is not None:
            self._screens = screens
        if main_rect is not None:
            self._main_rect = main_rect
        if probes is not None:
            self.probes = probes
        if native is not None:
            self._native = native
        self.sync()

    # ---- the setting --------------------------------------------------------------------------------------------------

    def _prefs(self) -> Any:
        try:
            return self.host._prefs_store()
        except Exception:  # noqa: BLE001 - no prefs: [look] decides
            return None

    def enabled(self) -> bool:
        """The owner's choice in Settings > Look on this PC, else [look] second_screen (off by default)."""
        store = self._prefs()
        try:
            chosen = store.look.second_screen if store is not None else None
        except Exception:  # noqa: BLE001
            chosen = None
        if chosen is not None:
            return bool(chosen)
        return bool(getattr(getattr(getattr(self.host, "config", None), "look", None), "second_screen", False))

    def set_enabled(self, on: bool) -> bool:
        """Settings > Look's switch: saved on this PC; the display shows (or hides) at once. False when it could not
        be saved (it holds until Jarvis closes)."""
        store = self._prefs()
        saved = False
        if store is not None:
            try:
                saved = bool(store.set_second_screen(bool(on)))
            except Exception:  # noqa: BLE001
                saved = False
        self._override = None if saved else bool(on)
        logger.info("Second screen: turned %s%s", "on" if on else "off", "" if saved else " (not saved)")
        self.sync()
        return saved

    _override: bool | None = None

    def is_on(self) -> bool:
        return self._override if self._override is not None else self.enabled()

    def available(self) -> bool:
        """A second monitor is there (two screens or more)."""
        return self._available

    def shown(self) -> bool:
        return self._shown

    def reason(self) -> str:
        """Why it is not shown ("" while it shows): off, one_screen, locked, display_off, battery_saver, full_screen."""
        return self._reason

    def target(self) -> ScreenSpot | None:
        return self._target

    # ---- where it goes, and whether it shows --------------------------------------------------------------------------

    def _default_main_rect(self) -> QRect | None:
        window = getattr(self.host, "window", None)
        try:
            return QRect(window.frameGeometry()) if window is not None and shiboken6.isValid(window) else None
        except RuntimeError:
            return None

    def _default_now(self) -> datetime:
        now = getattr(self.host, "_now", None)
        try:
            return now() if callable(now) else datetime.now().astimezone()
        except Exception:  # noqa: BLE001
            return datetime.now().astimezone()

    def _hour24(self) -> bool:
        return bool(getattr(getattr(getattr(self.host, "config", None), "display", None), "hour24", False))

    def _default_probes(self) -> HideProbes:
        def locked() -> bool:
            probe = getattr(self.host, "_locked_now", None)
            return bool(probe()) if callable(probe) else False

        def hwnd() -> int:                 # its own window, also while a rule holds it hidden
            window = self.window
            return int(window.winId()) if window is not None and shiboken6.isValid(window) else 0

        def native() -> DisplayNative:
            return self.window._native if self.window is not None else (self._native or NoNative())   # noqa: SLF001

        return HideProbes(locked=locked, display_off=lambda: native().display_off(),
                          battery_saver=lambda: native().battery_saver(),
                          full_screen=lambda: bool(hwnd()) and native().full_screen(hwnd()))

    def _on_screens(self, *_args: Any) -> None:
        QTimer.singleShot(0, self, self.sync)

    def _watch_main(self) -> None:
        """The main window going to another screen (its QWindow's screenChanged) and the owner's input in it."""
        window = getattr(self.host, "window", None)
        if not isinstance(window, QWidget) or not shiboken6.isValid(window):
            return
        handle = window.windowHandle()
        if handle is None or handle is self._watched_window:
            return
        self._watched_window = handle
        handle.installEventFilter(self._input)
        handle.screenChanged.connect(self._on_screens)

    def sync(self) -> None:
        """Decide now: the screen it shows on, its mode (full, the strip), whether a hide rule holds; show or hide."""
        if self._shut:
            return
        self._watch_main()
        spots = list(self._screens())
        available = len(spots) >= 2
        if available != self._available:
            self._available = available
            logger.info("Second screen: %d screen(s)", len(spots))
            self.availabilityChanged.emit(available)
        main = spot_at(spots, self._main_rect()) if spots else None
        target = choose_screen(spots, main) if self.is_on() else None
        self._target = target
        if target is None:
            self.broker.set_display(None)
            self._hide(R_OFF if not self.is_on() else R_ONE_SCREEN)
            return
        edge = far_edge(target.geometry, main.geometry if main is not None else None)
        self.broker.set_display(target.geometry, edge)
        window = self._ensure_window()
        width, height = target.geometry.width(), target.geometry.height()
        if strip_only(target.geometry) or self.broker.folded():
            window.place(strip_rect(target.geometry, edge), mode=STRIP, edge=edge, scale=strip_scale(width, height))
        else:
            window.place(QRect(target.geometry), mode=FULL, edge=edge, scale=screen_scale(width, height))
        reason = self.probes.reason()
        if reason:
            self._hide(reason)
            return
        self._refresh_wall()          # shown again after it was off or had one screen: the board as it is now
        window.show_quietly()
        if not self._care.isActive():
            self._care.start()
        self._schedule_minute()
        if not self._shown:
            self._shown = True
            self._reason = ""
            logger.info("Second screen: shown (%s, %dx%d)", window.mode(), window.width(), window.height())
            self.shownChanged.emit(True)

    def _hide(self, reason: str) -> None:
        window = self.window
        if window is not None and window.isVisible():
            window.hide()
        if reason in (R_OFF, R_ONE_SCREEN):
            self._care.stop()
            self._minute.stop()
            if window is not None and shiboken6.isValid(window):
                window.drop_pictures()           # nothing decoded is kept for a display that is not there
        elif not self._care.isActive():
            self._care.start()                 # the rule is checked again at the drift's pace
        changed = self._shown or reason != self._reason
        self._reason = reason
        if self._shown:
            self._shown = False
            self.shownChanged.emit(False)
        if changed:
            logger.info("Second screen: hidden (%s)", reason)

    def _ensure_window(self) -> SecondScreen:
        window = self.window
        if window is None or not shiboken6.isValid(window):
            window = self.window = SecondScreen(native=self._native, now=self._now, clock=self._clock)
            window._changed_hook = self.sync                # noqa: SLF001 - its power state changed
            self.destroyed.connect(lambda *_args, w=window: _discard(w))
            self._push()
        return window

    def _on_care(self) -> None:
        """The drift timer: OLED care's step, the counts again, and the hide rules (never faster than this)."""
        self.sync_glance()
        if self.window is not None and self._shown:
            self.window.care_tick()
        self.sync()

    def _schedule_minute(self) -> None:
        now = self._now()
        ms = max(200, int((60 - now.second - now.microsecond / 1e6) * 1000) + 50)
        self._minute.start(ms)

    def _on_minute(self) -> None:
        """A new minute: the clock and NEXT's "In 19 minutes" (no other repaint)."""
        if self.window is not None and self._shown:
            self.window.update()
            self._schedule_minute()

    # ---- what feeds it ------------------------------------------------------------------------------------------------

    def sync_glance(self) -> None:
        """ReadingView.glanceChanged (and the drift's pace): the ring's look, the state word and the counts, as the
        window has them. The desk's lit-frame glow is never taken: this ring keeps its own OLED rules."""
        reading = getattr(getattr(self.host, "window", None), "reading", None)
        if reading is None:
            return
        try:
            look = tuple(reading.look())
            voice = bool(reading.voice_listening())
            recording = bool(reading.recording_state()[0])
            word = str(reading.state_word() or "")
            readout = getattr(getattr(reading, "header", None), "readout", None)
            tone = str(readout.tone()) if readout is not None else "holo"
            desktop = getattr(self.host, "desktop", None)
            waiting = int(desktop.waiting_count()) if desktop is not None and hasattr(desktop, "waiting_count") else 0
            needs = int(reading.pending_count()) + waiting
            briefing = bool(reading.is_new_briefing())
            frames = int(reading.board_count()) if hasattr(reading, "board_count") else 0
        except Exception as exc:  # noqa: BLE001 - the display never stops the app
            logger.debug("Second screen: the glance was not read (%s)", type(exc).__name__)
            return
        feed = self._board()
        pinned = 0
        if feed is not None:
            try:
                pinned = int(feed.pinned())
            except Exception:  # noqa: BLE001
                pinned = 0
        state = replace(self.state, look=(str(look[0]), bool(look[1]), bool(look[2])), voice=voice,
                        recording=recording, word=word, tone=tone, needs=needs, briefing=briefing, frames=frames,
                        pinned=pinned, board_on=self._board_on(), hour24=self._hour24())
        moved = ((state.look, state.voice, state.word, state.needs, state.briefing, state.frames)
                 != (self.state.look, self.state.voice, self.state.word, self.state.needs, self.state.briefing,
                     self.state.frames))
        self._set(state, touch=moved)

    def _board_on(self) -> bool:
        return bool(getattr(getattr(getattr(self.host, "config", None), "look", None), "board", True))

    _board_mark: Any = None                 # the board as last noted (board.feed.BoardFeed.mark())
    _board_at: datetime | None = None       # when it last changed: the wall's "UPDATED"
    _wall_mark: Any = None                  # the board the wall was last built from

    def on_board(self, feed: Any = None) -> None:
        """The board may have changed (the desk's every send; never the lit frame, a backdrop or open big: only "frame"
        and "picture" data come here). It is noted - with its time, only when it did change: the desk's resize sends
        change nothing - and the wall built from it, except while the display is off or on one screen or has no
        window: then nothing is built (sync() fills the wall from the board as it is when it shows)."""
        feed = feed if feed is not None else self._board()
        self._note_board(feed)
        window = self.window
        if self._reason in (R_OFF, R_ONE_SCREEN) or window is None or not shiboken6.isValid(window):
            return
        self._fill_wall(feed)

    def _note_board(self, feed: Any) -> None:
        """The board as it is now (cheap: BoardFeed.mark()); when it differs from the last one noted, when."""
        try:
            mark = feed.mark() if feed is not None else None
        except Exception:  # noqa: BLE001 - a board that can't say: taken as changed
            mark = object()
        if mark != self._board_mark:
            self._board_mark = mark
            self._board_at = self._now()

    def _refresh_wall(self) -> None:
        """The wall from the board as it is now (a new window; a display shown again after it was off or had one
        screen)."""
        feed = self._board()
        self._note_board(feed)
        self._fill_wall(feed)

    def _fill_wall(self, feed: Any) -> None:
        """The wall from ``feed`` - its first nine frames as the page has them, private, and their pictures - unless it
        was built from the board as it is now already; "UPDATED" says when the board last changed."""
        if self._wall_mark is not None and self._wall_mark == self._board_mark:
            return                              # nothing on the board changed since: nothing built
        self._wall_mark = self._board_mark
        frames, pictures = wall_frames(feed)
        tiles = tuple(frames)
        if tiles == self.state.tiles and pictures == self.state.pictures:
            return
        from .text_prep import format_time

        stamp = format_time(self._board_at or self._now(), hour24=self._hour24()) if tiles else ""
        pinned = 0
        try:
            pinned = int(feed.pinned()) if feed is not None else 0
        except Exception:  # noqa: BLE001
            pinned = 0
        self._set(replace(self.state, tiles=tiles, pictures=dict(pictures), updated=stamp, pinned=pinned),
                  touch=True, board=True)

    def set_events(self, events: Iterable[Any], now: datetime | None = None) -> None:
        """The calendar's events as the app last read them (gcal.CalendarEvent): today's still to come, soonest
        first; the display picks the next one by its own clock."""
        upcoming = upcoming_events(events or (), now or self._now())
        if upcoming == self.state.events:
            return
        self._set(replace(self.state, events=upcoming), touch=False)

    def note_input(self) -> None:
        """The owner's input to Jarvis: quiet ends."""
        window = self.window
        if window is not None and self._shown and window.care.stage(self._clock()) != LIT:
            window.touch()
        elif window is not None:
            window.care.touch(self._clock())

    def _set(self, state: DisplayState, *, touch: bool, board: bool = False) -> None:
        if state == self.state:
            return                              # nothing new to paint (glanceChanged fires for more than this shows)
        self.state = state
        window = self.window
        if window is None or not shiboken6.isValid(window):
            return
        window.set_state(state)
        if touch:
            window.touch(board=board)

    def _push(self) -> None:
        """A new window: everything as it is now."""
        self.sync_glance()
        self._refresh_wall()
        events = getattr(self.host, "_agenda_events", None)
        if events:
            self.set_events(events)
        if self.window is not None:
            self.window.set_state(self.state)

    # ---- teardown -------------------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Jarvis closes: the window goes now (the broker forgets it)."""
        if self._shut:
            return
        self._shut = True
        self._care.stop()
        self._minute.stop()
        try:
            self.broker.set_display(None)
            self.broker.changed.disconnect(self.sync)
        except (RuntimeError, TypeError):
            pass
        window, self.window = self.window, None
        _discard(window)


def _discard(window: Any) -> None:
    """The display's window, gone now (on the GUI thread)."""
    if window is None or not shiboken6.isValid(window):
        return
    try:
        window.shutdown()
        window.deleteLater()
    except RuntimeError:
        pass


__all__ = ["BOARD_HEAD", "BRIEFING_SUB", "BRIEFING_TITLE", "Care", "DisplayLayout", "DisplayState", "FULL", "HideProbes",
           "LIVE", "MAIL_HIDDEN", "NEEDS_MONITOR", "NEEDS_SUB", "NextEvent", "NoNative", "PlacementBroker", "STRIP",
           "SETTING_LABEL", "ScreenSpot", "SecondScreen", "SecondScreenController", "WATCH", "broker",
           "choose_screen", "display_layout", "far_edge", "frames_text", "in_words", "needs_text", "next_line",
           "pick_next", "private_frame", "screen_scale", "spots_of", "strip_only", "strip_rect", "strip_scale",
           "upcoming_events", "wall_frames"]
