"""The board's messages, both ways (the new look, part 3; PLAN section 3, DESIGN-SPEC section 8). Qt-free and pure: no
file, no log, no network; nothing here raises but Refused.

In (app -> page): check_message(message, known, groups=()) -> Checked
    A message is a dict of known fields of the right types, rebuilt here field by field (never the caller's object), and
    its JSON (json.dumps, ASCII only, no NaN or Infinity) is at most MESSAGE_CAP bytes. A "picture" message may also carry
    ONE image: a data:image/jpeg;base64 URL of at most PICTURE_CAP bytes that decodes to a JPEG of at most PICTURE_BYTES.
    Strings are kept exactly as given, control and bidi characters too: the page sets every one as text (textContent),
    never as markup. A string's cap counts what the page counts (JavaScript's length: UTF-16 code units, so a character
    outside the BMP counts two). The types:
        frame    {"id", "kind", "title", "meta", "body", "status", "status_text", "status_meta", "waits", "changed",
                  "pinned", "closed"}: adds a frame (it draws in) or updates the one with that id in place. "body" is the
                  kind's own (KINDS: one checker per kind, as the page has one renderer per kind); "status" ok / failed
                  (status_text says what failed: its warning, no button) / empty (status_text, status_meta "Checked
                  9:44 AM"); "waits" the words of its amber chip ("" = it waits on nothing).
                  The bodies (DESIGN-SPEC 3.3; every string capped, every list at most ROWS_CAP rows, no other key):
                    text         {"text", "rows"?: [{"lead", "text", "meta"}]}        (rows only when given)
                    calendar     {"first", "last" (the day bar's minutes after midnight; equal: no bar), "now_min" (its
                                  now tick, -1 none), "ticks": [{"at", "text"}], "spans": [{"start", "end", "kind" busy /
                                  next / free}], "now" (the horizon's pill, "" none), "rows": [{"time", "title", "meta",
                                  "state" CALENDAR_STATES}]}
                    mail_thread  {"sender", "time", "text"}       (the text clamped to 3 lines by the page's style only)
                    mail_list    {"rows": [{"sender", "time", "subject", "meta", "unread"}]}
                    todos        {"rows": [{"text", "meta", "state" open / waits / done}]}
                    capture      {"rows": [{"text", "meta", "state" CAPTURE_STATES, "detail"}], "note"}   (build plan
                                  item 3: what a capture found - each item WAITS, ON YOUR CALENDAR, DECIDED, PAST or
                                  CAN'T ADD; "meta" its day and time, "detail" a second line (WORDS_CAP: a series' repeat
                                  words, a can't-add row's reason; never on the "+N MORE" row); "note" WORDS_CAP, ""
                                  none ("Not checked against your calendar"); every key present in every row)
                    picture      {"caption"}                      (its JPEG comes in its own picture message)
                  Part 6, Ask's typed frames (board.ask_frames; every key present, every list 1 to its cap, every
                  number finite and under NUMBER_LIMIT, never a boolean):
                    table        {"columns" (1-6), "rows" (1-12, each as many cells as columns: words), "unit",
                                  "changed" (a row, -1 none), "change", "source"}
                    chart        {"bars" (1-8: {"label", "value", "text" (its words: "3.4k")}), "unit", "changed" (a bar,
                                  -1 none), "was" (its old value, null none), "change" (the delta line)}
                    timeline     {"nodes" (1-8: {"when", "words", "state" NODE_STATES}), "today" (-1 none, 0 to the
                                  number of nodes: the TODAY marker stands before that node)}
                    maths        {"steps" (1-8: {"name", "expr"}), "answer" ({"name", "expr"})}: an expression is
                                  ["num", words], ["name", 1-12 letters] or [op, expression, ...] (MATHS_OPS), at most
                                  MATHS_DEPTH levels and MATHS_NODES nodes in the frame - built by the page with
                                  createElement from this tree, never from markup
                  A row whose main words are empty (a text row's lead and text, a calendar row's time and title, a mail
                  row's sender and subject, a to-do's text) is the "+N MORE" row: only its meta shows.
                  "group" (part 5a, only when given): the group (one answer) the frame belongs to - a group the app sent
                  (``groups``); the page puts the frame in that group's place on the board.
                  "fact" (part 5b, only when given; at most FACT_CAP): the one line a satellite row shows ("REPLY
                  WAITS", "3 EVENTS · NEXT 10:00 AM"), made by the app from the frame's data (board.facts).
                  "pinned" / "closed": the app's own flags (it is the source of truth: it sends them again whenever
                  the page's own optimistic change and its decision differ).
        lit      {"id"}: the frame Jarvis is talking about ("" = none)
        remove   {"id"}; clear {}
        motion   {"level": lively / calm / off}
        picture  {"id", "src"}: the frame's one picture
      Part 5a, the board on Home (DESIGN-SPEC 3.1, 3.5):
        group    {"id", "time", "topic", "frames", "older"}: one answer's group - its header (the time, "9:41 AM", and
                 the topic, at most TOPIC_CAP), its frames in board order (ids the app sent), "older" (INK_4) for every
                 group but the newest. A new group goes on top of the board; past MAX_GROUPS it is refused
        remove_group {"id"}: a group the app sent leaves whole, with its frames
        bar      {"frames", "waits", "pinned", "compact"}: the board bar's counts ("9 FRAMES", "1 WAITS", "Pinned 1");
                 "compact" (the folded Dock's narrow column) drops the frames count
        desk     {"state": enter / leave}: the desk formed (the board fades and slides in) or folded
        reveal   {"group"}: the board scrolls to that group (a conversation turn's "4 frames on the board")
      Part 5b, the board's ways (DESIGN-SPEC 3.1, 3.3, 3.5, 3.6):
        group    + "parked" (only when given): the answer came while the owner was reading the board - the page keeps
                 it out of sight with the pill "New answer above · Show" and its dashed entries on top of the index
                 strip, and the scroll position holds; the same group without it lands (on top, the others older)
        cleared  {"frames"}: the frames the bar's Clear took away (ids the app sent; out of sight, with the row "Board
                 cleared · 8 FRAMES · Bring back"); [] none (the row goes)
        focus_part {"part": board / strip / next / prev}: F6's cycle - the keyboard focus to the frames or the index
                 strip; next / prev from where it is (past the strip the page asks for focus_out "ask", before the
                 frames for focus_out "conversation")
      Part 5c, the lit frame and open big (DESIGN-SPEC 3.4, 3.5, 8):
        lit      + "cue" (only when given): the app draws the cue line's native part to this frame, so the page draws
                 its 15 px stub (from the view's left edge to the frame, a 2.5 px dot 6 px from it); false or absent:
                 the brackets only
        backdrop {"src"}: the conversation column as the app grabbed it, ONE JPEG data: URL under the picture's caps
                 (at most PICTURE_CAP characters, PICTURE_BYTES of JPEG): the page paints it where the column was
                 while the view is wide for open big
        big      {"state": open / close, "id", "x", "y", "w", "h"}: open - that frame grows to the whole view; the view
                 is wide now and the board keeps its own box in it (x, y, w, h: px, BIG_LIMIT at most); close - it
                 goes back to its place ("id" "" or the frame's; no box)
      The canvas, part 1 (PLAN-canvas 3: Home is the board's page, the orb is drawn in it):
        orb      {"state", "form", "muted", "offline", "dim", "away", "level", "voice"}: the orb's state word
                 (ORB_STATES), its form (ORB_FORMS: rings, the badge, the corner), the two modifiers the native ring
                 has (muted, offline voice), "dim" (quiet hours), "away" (Jarvis's window is not the one the owner works
                 in: at rest the orb is drawn 10 times a second, not 30) and two levels, whole numbers 0 to LEVEL_MAX:
                 the microphone's (listening) and Jarvis's voice's (speaking). Nothing else of the app's state goes
                 to the orb
        canvas   {"clock", "word", "tone", "approvals", "buttons", "briefing_new", "hold", "rec", "wake", "meter"}: the
                 corners' words - the date and time, the state word and its tone (CANVAS_TONES), how many cards wait
                 on Approvals, which of the page's own buttons show (CANVAS_BUTTONS, each once: a button is there only
                 while pressing it works), "Briefing ready", a Send's hold word ("Sent": its Undo shows), REC's words
                 (its Stop shows), "Hey Jarvis"'s badge and whether the microphone's meter shows. Every label of a
                 button is the page's own; none comes from here. Part 3B: "now" - the resting group "Now" can come
                 back (its corner button shows only while the group is not on the canvas). The window brief: + "hear"
                 (HEAR_STATES: "Hey Jarvis" listening, paused, can't hear you, "other" - its few words in "wake" -
                 or "" off), "deaf" (Jarvis can't hear the owner: the note "Jarvis can't hear you" with what to do)
                 and "mic" (the microphone's name for that note, at most MIC_CAP; shown on screen, never logged); the
                 button "mic_settings" (the note's one button: Settings > Talk, where a microphone is picked).
                 Canvas part 5B: + "side" ([width, height] in px, each 0 to SIDE_MAX: the offer's or the error's tile
                 at the top left of the frames' area - the page keeps every frame, the task card and every group's
                 head clear of it; [0, 0] none) and the HEY JARVIS state "hearing" (a talk "Hey Jarvis" started is
                 listening: the page's own words "Hearing you")
        chrome   {"hover"}: the window brief - the pointer is on the maximise button, which Windows hit-tests as its
                 own (HTMAXBUTTON: Snap Layouts), so the page lights it as if it were hovered
      The canvas, part 2 (frames across the screen):
        frame    + the kind "number" {"figure", "text", "source"}: a big number the app builds from a chart's or a
                 table's change - the figure large ("+500": NUMBER_TEXT_CAP at most, a signed number's words), what it
                 was and is (at most CHANGE_CAP) and where it comes from (at most SOURCE_CAP)
        task     {"state", "title", "words", "seconds", "steps", "sources", "frames", "group", "stop"}: the task card
                 - "working" (what Jarvis is doing in the app's own words, never the owner's: "title" at most
                 TOPIC_CAP, "words" at most TASK_WORDS_CAP; the seconds it has run, the steps finished, the sources
                 read and the frames put up so far, each a whole number; "stop": the app's Stop works now), "done" (the
                 answer arrived: "group" is the group it heads - one the app sent - and the card shrinks to that
                 group's head tile; "" none: it goes) or "gone" (stopped, or it failed: the card goes)
      The canvas, part 3 (the resting frames; board.resting builds them from what the app already holds):
        frame    + the kind "approvals" {"count", "rows": [{"kind", "title"}]}: the "waits for your OK" frame - how many
                 cards wait on Approvals (1 to APPROVALS_MAX: it is there only while something waits) and the first
                 few, each its kind label and title as the Approvals screen words them; amber; its one button (the
                 page's own words) asks go "approvals". Never an approve, send or undo control
        group    + "below" (only when given): a new group goes under the groups the page has, never on top (the
                 resting group "Now": it never takes an answer's best places)
        group    + "rest" (only when given): the resting group "Now" - on the canvas its frames go in even rows (as
                 many across as the width gives room, no row with a lone frame, a row's frames equally tall; placed
                 one by one instead on a small window where only that shows every frame whole)
        frame    + the kind "agents" {"rows": [{"name", "state", "last", "came", "next", "waits"}], "more", "waits"}:
                 the owner's standing agents (1 to ROWS_CAP rows; "more": agents past them) - each its name, its state
                 (AGENT_STATES), when it last ran ("" never) and what came of it (its proposals; -1 not known),
                 when it runs next ("" not planned) and how many of its proposals wait; "waits" all of them that wait
                 (at least the rows' sum). Its one button (the page's own words) asks go "agents"
    The ids of lit, remove and picture must be frames the app sent (``known``), and a group's those of groups it sent
    (``groups``); a new frame past MAX_FRAMES is refused.

Out (page -> app): check_request(kind, payload, known) -> Request
    kind one of REQUEST_KINDS; payload a small JSON object (at most PAYLOAD_CAP characters, no NaN or Infinity) of that
    kind's fields only; every frame id one the app sent (``known``):
        ready {"v"?} | layout {"frames": [{"id", "top", "height"}, ...], "scrolling"} | pin {"id", "on"}
        | focus, open, close, bring_back, go_approvals {"id"}   (canvas part 5A: no retry - no frame offers it)
        | hold {"id", "on"}   a closed frame's row has the pointer over it or the keyboard focus in it (on: its Bring
                          back stays offered, the app holds its leaving) or has neither any more (off: its CLOSE_S
                          starts again, on the page and in the app)
      Part 5b:
        clear {}          the bar's Clear (the app takes every unpinned frame away, for CLEAR_S with Bring back)
        restore {}        the cleared row's Bring back
        show_new {}       the pill's Show: a parked answer lands now
        reading {"pointer", "focus"}   the owner is at the board: the pointer over it, the focus in it (both bools);
                          sent when either changes and on a wheel, a key or a press in the page (a sign of reading)
        focus_out {"to": ask / conversation}   Esc, or F6 past the board: the keyboard focus to the ask bar or the
                          conversation
      Part 5c:
        landed {"id", "step": outline / done}   a frame drawing in: its outline is complete (a frame lands), then its
                          whole draw-in is done (at once when nothing animates) - the board's sounds
        big {"step": back / laid / shut}        open big: the owner asked to go back (Esc, a click beside the big
                          frame), the page is laid out at the view's new size, the frame's scale-down has finished
      The canvas, part 1:
        go {"to": one of GO_WORDS}              a corner button of the page: a screen, History, the Day view, the
                          switch to the desktop HUD, the window's minimise / maximise / close / drag, Mute, a hold's
                          Undo, REC's Stop, the folded board back. A word that is not on the list is refused
      The canvas, part 2:
        go {"to": "tidy"}                       the corner's Tidy: the page packed its frames again (counted)
        go {"to": "stop"}                       the task card's Stop: the app's own Stop of the running request
      The window brief:
        chrome {"w", "bar", "holes", "max"}     where the page's top band is, for Windows' hit-test: the view's width
                          the page measured, the band's height, the boxes in it that are the page's own controls
                          ([x, y, w, h] each, at most CHROME_HOLES) and the maximise button's box ([] none)
    Anything else is refused. A log line may carry kind_word(kind) and the reason word, never the payload.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

MESSAGE_CAP = 32 * 1024          # bytes of JSON in one message (a picture's image apart)
PICTURE_CAP = 410 * 1024         # bytes of the one data: URL a picture message may carry
PICTURE_BYTES = 300 * 1024       # bytes of the JPEG in it (the app re-encodes to at most this)
PICTURE_PREFIX = "data:image/jpeg;base64,"
PAYLOAD_CAP = 4096               # characters of a request's payload
MAX_FRAMES = 24                  # frames on the board at once (DESIGN-SPEC 3.5)
MAX_GROUPS = 8                   # groups (one answer each) on the board at once (DESIGN-SPEC 3.5)
MAX_PINS = 6                     # pinned frames at most (DESIGN-SPEC 3.5; the app refuses a 7th)
TOPIC_CAP = 40                   # a group's topic (DESIGN-SPEC 3.1)
TIME_CAP = 16                    # a group's time ("9:41 AM", "13:05")
ID_CAP = 40
TITLE_CAP = 60                   # characters (DESIGN-SPEC 3.5: title 60, text 400)
META_CAP = 40
FACT_CAP = META_CAP              # a satellite's one fact: a meta's length (the page checks it as a meta)
TEXT_CAP = 400
WORDS_CAP = 160                  # what failed, or what is empty
WAITS_CAP = 60
ROWS_CAP = 8                     # rows of a frame's body (DESIGN-SPEC 3.5)
SPANS_CAP = 32                   # a calendar's day bar: its events and free gaps ...
TICKS_CAP = 13                   # ... its hour labels (every two hours of a whole day) ...
TICK_CAP = 8                     # ... each at most this long ("12")
PILL_CAP = 16                    # the calendar's now pill ("9:41")
DAY_MINUTES = 24 * 60
PLACE_LIMIT = 1_000_000          # px: a layout's top / height beyond this is not a place
BIG_LIMIT = 100_000              # px: the board's box in the wide view (open big) is at most this
# Part 6, Ask's typed frames (DESIGN-SPEC 3.3; 3.5: a table 6 x 12). board.ask_frames checks the model's own fields
# against the same caps first.
TABLE_COLUMNS_CAP = 6
TABLE_ROWS_CAP = 12
COLUMN_CAP = 24                  # a table's header words
CELL_CAP = 40
UNIT_CAP = 12                    # a table's or a chart's unit ("USD"): its meta
CHANGE_CAP = 80                  # a changed row's or bar's words: the delta line ("+500 Software · was 2.9k on Friday")
SOURCE_CAP = 60                  # a table's source footer
BARS_CAP = 8
LABEL_CAP = 24
NUMBER_TEXT_CAP = 24             # a number's words ("1,234,567.5", "3.4k")
NODES_CAP = 8
WHEN_CAP = 24
NODE_WORDS_CAP = 40
NODE_STATES = ("done", "next", "due", "")
STEPS_CAP = 8
STEP_NAME_CAP = 24
NAME_LETTERS = 12                # a maths name: 1 to 12 letters
MATHS_DEPTH = 6                  # levels in one expression
MATHS_NODES = 64                 # nodes in one frame's expressions
NUMBER_LIMIT = 10 ** 15          # a number on the board: under this (and finite)
# A maths op and how many arguments it takes: + and × 2 to 8, − 1 (negation) or 2, ÷ / frac / power 2, a root 1 or 2
# (the radicand, then its index), brackets 1, = and "or" 2 to 4.
MATHS_OPS = {"+": (2, 8), "-": (1, 2), "*": (2, 8), "/": (2, 2), "=": (2, 4), "frac": (2, 2), "pow": (2, 2),
             "root": (1, 2), "()": (1, 1), "or": (2, 4)}

MESSAGE_TYPES = ("frame", "lit", "remove", "clear", "motion", "picture", "group", "remove_group", "bar", "desk",
                 "reveal", "cleared", "focus_part", "backdrop", "big", "orb", "canvas", "task", "chrome")
# The canvas, part 1: the orb's states (the native ring's looks: orb.IDLE ...), its three forms, its levels' range, the
# corners' words' caps and tones, the page's own buttons the app may show, and the words a button may send back.
ORB_STATES = ("idle", "listening", "thinking", "speaking", "needs", "waiting")
ORB_FORMS = ("rings", "badge", "corner")
LEVEL_MAX = 100
CLOCK_CAP = 40                   # "Tue 17 Nov · 9:41 AM"
STATE_WORD_CAP = 40              # "QUIET HOURS UNTIL 8:00 AM"
HOLD_CAP = 24                    # "Sent" / "Cancelled"
REC_CAP = 40                     # "REC 12:03 · No call audio"
WAKE_CAP = 32                    # "Not listening: add-on missing" (wake_ui.SHORT_CAP: its few words, whole)
APPROVALS_MAX = 999
CANVAS_TONES = ("holo", "amber", "steel")
CANVAS_BUTTONS = ("history", "board", "briefing", "approvals", "followups", "meetings", "agents", "documents", "live",
                  "memory", "settings", "desktop", "minimize", "maximize", "close", "mute", "day", "now", "mic_settings")
# The window brief: "Hey Jarvis" on the canvas (wake_ui.WakeController.hearing) and the can't-hear note's microphone.
HEAR_STATES = ("", "listening", "paused", "cant_hear", "other", "hearing")
SIDE_MAX = 4000                  # px: the side tile's box at most (canvas part 5B)
MIC_CAP = 60                     # a microphone's name ("Microphone (Steam Streaming Microphone)")
# The window brief: the page's top band for Windows' hit-test (its controls' boxes, the maximise button's).
CHROME_HOLES = 40
CHROME_BAR_MAX = 400             # px: the band's height at most
GO_WORDS = (*CANVAS_BUTTONS, "undo", "stop_rec", "drag", "tidy", "stop")
# The canvas, part 2: the task card's states, its words' caps and its counts' range.
TASK_STATES = ("working", "done", "gone")
TASK_WORDS_CAP = 60              # "Reading your calendar"
TASK_SECONDS_MAX = 86_400
TASK_COUNT_MAX = 99
# The canvas, part 3: the resting frames' states and counts.
AGENT_STATES = ("on", "paused", "off")              # an agent on its frame: on, paused, or not on yet (a draft)
RESTING_COUNT_MAX = 999                             # an agent's proposals, the agents past the rows
BIG_STATES = ("open", "close")                      # big's "state"
LANDED_STEPS = ("outline", "done")                  # landed's "step"
BIG_STEPS = ("back", "laid", "shut")                # the big request's "step"
STATUSES = ("ok", "failed", "empty")
DESK_STATES = ("enter", "leave")
FOCUS_PARTS = ("board", "strip", "next", "prev", "controls")   # focus_part (F6's cycle; the canvas's buttons)
FOCUS_OUT = ("ask", "conversation")                  # focus_out's "to"
# A calendar row: past (dim), now (on now), next (the next event: its meta "IN 19 MIN"), free (a dashed gap), new (what
# a card adds or moves to), struck (where it was, or what is cancelled), today (a week's row for today).
CALENDAR_STATES = ("", "past", "now", "next", "free", "new", "struck", "today")
SPAN_KINDS = ("busy", "next", "free")
TODO_STATES = ("open", "waits", "done")
CAPTURE_STATES = ("waits", "on_calendar", "decided", "past", "cant_add")
MOTION_LEVELS = ("lively", "calm", "off")
REQUEST_KINDS = ("ready", "layout", "focus", "open", "close", "pin", "bring_back", "go_approvals",
                 "clear", "restore", "show_new", "reading", "focus_out", "landed", "big", "go", "chrome", "hold")
_ID_KINDS = frozenset({"focus", "open", "close", "bring_back", "go_approvals"})
_BARE_KINDS = frozenset({"clear", "restore", "show_new"})     # a payload of no field at all
_ID = re.compile(r"[A-Za-z0-9_-]{1,%d}" % ID_CAP)
_BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_JPEG = b"\xff\xd8\xff"
# A number's words as board.ask_frames writes them ("3,400", "0.25", "−4"; a chart's value also "3.4k") and a maths name.
_NUMBER_TEXT = re.compile(r"−?(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)(?:\.[0-9]+)?")
_VALUE_TEXT = re.compile(r"−?(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)(?:\.[0-9]+)?[kMBT]?")
_FIGURE_TEXT = re.compile(r"[+\-−]?[$€£]?[0-9][0-9,.]*[kMBT%]?")     # a big number's figure ("+500", "−1.2k")
_MATHS_NAME = re.compile(r"[A-Za-zΑ-ΡΣ-Ωα-ω]{1,%d}" % NAME_LETTERS)

# Reason words (a log line may carry them; never a value).
R_SHAPE = "shape"                # not a dict / not the right type of field
R_FIELD = "field"                # a field the type does not have, or one it needs is missing
R_TYPE = "type"                  # an unknown message type
R_KIND = "kind"                  # an unknown frame kind / request kind
R_ID = "id"                      # not an id, or not a frame the app sent
R_TOO_LONG = "too_long"          # a string over its cap
R_SIZE = "size"                  # the JSON over MESSAGE_CAP / the payload over PAYLOAD_CAP
R_PICTURE = "picture"            # not one JPEG data: URL within the caps
R_JSON = "json"                  # a payload that is not plain JSON
R_FULL = "full"                  # a new frame past MAX_FRAMES


class Refused(ValueError):
    """A message or request that does not pass; ``reason`` is one of the R_* words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Checked:
    """A message that passed: its type, the JSON text the page gets, the frame id it is about ("" = none) and the group
    it names ("" = none: a frame without a group; a group, remove_group or reveal message's own)."""

    type: str
    text: str
    frame_id: str = ""
    group_id: str = ""

    def __repr__(self) -> str:   # never the text
        return f"Checked(type={self.type!r}, bytes={len(self.text)}, id={self.frame_id!r}, group={self.group_id!r})"


@dataclass(frozen=True)
class Request:
    """A request of the page that passed. ``places``: a layout's (id, top, height) per frame, in the view's px;
    ``pointer`` / ``focused``: a reading request's flags; ``to``: where focus_out sends the keyboard focus; ``step``:
    a landed or big request's step."""

    kind: str
    frame_id: str = ""
    on: bool = False
    places: tuple[tuple[str, int, int], ...] = ()
    scrolling: bool = False
    pointer: bool = False
    focused: bool = False
    to: str = ""
    step: str = ""
    width: int = 0                                   # chrome: the view's width the page measured (px) ...
    bar: int = 0                                     # ... its top band's height ...
    holes: tuple[tuple[int, int, int, int], ...] = ()     # ... its own controls' boxes there ...
    maximize: tuple[int, ...] = ()                   # ... and the maximise button's box (() none)


def kind_word(kind: Any) -> str:
    """What a log line may say a request was: its kind when it is on the list, else "unknown"."""
    return kind if isinstance(kind, str) and kind in REQUEST_KINDS else "unknown"


def units(text: str) -> int:
    """The length of ``text`` as the page counts it (JavaScript's String length: UTF-16 code units; a character outside
    the Basic Multilingual Plane counts two)."""
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def is_frame_id(value: Any) -> bool:
    """A frame id: 1 to ID_CAP letters, digits, "-" or "_" (a builder's caller gives it; never the page's words)."""
    return type(value) is str and _ID.fullmatch(value) is not None


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------

def _fields(value: Any, allowed: Collection[str], required: Collection[str] = ()) -> dict[str, Any]:
    if type(value) is not dict:
        raise Refused(R_SHAPE)
    for key in value:
        if type(key) is not str or key not in allowed:
            raise Refused(R_FIELD)
    if any(key not in value for key in required):
        raise Refused(R_FIELD)
    return value


def _text(value: Any, cap: int) -> str:
    if type(value) is not str:
        raise Refused(R_SHAPE)
    if len(value) > cap or units(value) > cap:     # the first test first: a huge string is never walked
        raise Refused(R_TOO_LONG)
    return value


def _list(value: Any, cap: int) -> list[Any]:
    if type(value) is not list:
        raise Refused(R_SHAPE)
    if len(value) > cap:
        raise Refused(R_TOO_LONG)
    return value


def _number(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:     # a bool is not a number here; nor is 1.0
        raise Refused(R_SHAPE)
    return value


def _flag(value: Any) -> bool:
    if type(value) is not bool:
        raise Refused(R_SHAPE)
    return value


def _choice(value: Any, choices: Collection[str]) -> str:
    if type(value) is not str or value not in choices:
        raise Refused(R_FIELD)
    return value


def _frame_id(value: Any) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise Refused(R_ID)
    return value


def _known_id(value: Any, known: Collection[str]) -> str:
    frame_id = _frame_id(value)
    if frame_id not in known:
        raise Refused(R_ID)
    return frame_id


# --------------------------------------------------------------------------
# The kinds: one body checker per kind (the page has one renderer per kind)
# --------------------------------------------------------------------------

def _rows(value: Any, row: Callable[[Any], dict[str, Any]]) -> list[dict[str, Any]]:
    return [row(item) for item in _list(value, ROWS_CAP)]


def _text_row(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("lead", "text", "meta"))
    return {"lead": _text(fields.get("lead", ""), META_CAP), "text": _text(fields.get("text", ""), TITLE_CAP),
            "meta": _text(fields.get("meta", ""), META_CAP)}


def _text_body(body: Any) -> dict[str, Any]:
    """The generic kind: a frame of plain text (title and meta are the frame's own), and rows of a lead, words and a
    meta when it has them (a research answer's sources, a web search's results: the host as words, never a link)."""
    fields = _fields(body, ("text", "rows"))
    clean: dict[str, Any] = {"text": _text(fields.get("text", ""), TEXT_CAP)}
    if "rows" in fields:
        clean["rows"] = _rows(fields["rows"], _text_row)
    return clean


def _tick(value: Any, first: int, last: int) -> dict[str, Any]:
    fields = _fields(value, ("at", "text"), ("at", "text"))
    return {"at": _number(fields["at"], first, last), "text": _text(fields["text"], TICK_CAP)}


def _span(value: Any, first: int, last: int) -> dict[str, Any]:
    fields = _fields(value, ("start", "end", "kind"), ("start", "end", "kind"))
    start = _number(fields["start"], first, last)
    return {"start": start, "end": _number(fields["end"], start, last), "kind": _choice(fields["kind"], SPAN_KINDS)}


def _calendar_row(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("time", "title", "meta", "state"))
    return {"time": _text(fields.get("time", ""), META_CAP), "title": _text(fields.get("title", ""), TITLE_CAP),
            "meta": _text(fields.get("meta", ""), META_CAP),
            "state": _choice(fields.get("state", ""), CALENDAR_STATES)}


def _calendar_body(body: Any) -> dict[str, Any]:
    """A day (its bar from the first to the last hour, the now tick, the now-line horizon, rows with time running down)
    or a week (rows only: one per day)."""
    fields = _fields(body, ("first", "last", "now_min", "ticks", "spans", "now", "rows"))
    first = _number(fields.get("first", 0), 0, DAY_MINUTES)
    last = _number(fields.get("last", 0), first, DAY_MINUTES)
    ticks = [_tick(item, first, last) for item in _list(fields.get("ticks", []), TICKS_CAP)]
    spans = [_span(item, first, last) for item in _list(fields.get("spans", []), SPANS_CAP)]
    now_min = fields.get("now_min", -1)
    if not (type(now_min) is int and (now_min == -1 or (last > first and first <= now_min <= last))):
        raise Refused(R_SHAPE)
    if last == first and (ticks or spans):         # no bar: nothing on it
        raise Refused(R_SHAPE)
    return {"first": first, "last": last, "now_min": now_min, "ticks": ticks, "spans": spans,
            "now": _text(fields.get("now", ""), PILL_CAP), "rows": _rows(fields.get("rows", []), _calendar_row)}


def _mail_thread_body(body: Any) -> dict[str, Any]:
    """A mail: who (600), when, and its plain text (the page clamps it to 3 lines with its style; never cut in the
    page's words). A reply that waits on Approvals is the frame's own "waits"."""
    fields = _fields(body, ("sender", "time", "text"))
    return {"sender": _text(fields.get("sender", ""), TITLE_CAP), "time": _text(fields.get("time", ""), META_CAP),
            "text": _text(fields.get("text", ""), TEXT_CAP)}


def _mail_row(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("sender", "time", "subject", "meta", "unread"))
    return {"sender": _text(fields.get("sender", ""), TITLE_CAP), "time": _text(fields.get("time", ""), META_CAP),
            "subject": _text(fields.get("subject", ""), TITLE_CAP), "meta": _text(fields.get("meta", ""), META_CAP),
            "unread": _flag(fields.get("unread", False))}


def _mail_list_body(body: Any) -> dict[str, Any]:
    """Mails, one row each: an unread dot, the sender, the time at the right and the subject below."""
    fields = _fields(body, ("rows",))
    return {"rows": _rows(fields.get("rows", []), _mail_row)}


def _todo_row(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("text", "meta", "state"))
    return {"text": _text(fields.get("text", ""), TITLE_CAP), "meta": _text(fields.get("meta", ""), META_CAP),
            "state": _choice(fields.get("state", "open"), TODO_STATES)}


def _todos_body(body: Any) -> dict[str, Any]:
    """To-dos: a box each; one that waits on the owner's OK an amber diamond and WAITS; a done one a check and DONE."""
    fields = _fields(body, ("rows",))
    return {"rows": _rows(fields.get("rows", []), _todo_row)}


def _capture_row(value: Any) -> dict[str, Any]:
    keys = ("text", "meta", "state", "detail")
    fields = _fields(value, keys, keys)
    state = _choice(fields["state"], CAPTURE_STATES)
    detail = _text(fields["detail"], WORDS_CAP)
    text = _text(fields["text"], TITLE_CAP)
    if detail and not text:
        raise Refused(R_SHAPE)
    return {"text": text, "meta": _text(fields["meta"], META_CAP), "state": state, "detail": detail}


def _capture_body(body: Any) -> dict[str, Any]:
    """What a capture found (build plan item 3): a row per item with its state, a can't-add row's reason, the note."""
    fields = _fields(body, ("rows", "note"), ("rows", "note"))
    return {"rows": _rows(fields["rows"], _capture_row), "note": _text(fields["note"], WORDS_CAP)}


def _picture_body(body: Any) -> dict[str, Any]:
    """A page picture: its caption (the host, as words) under the JPEG of its own picture message; it never navigates."""
    fields = _fields(body, ("caption",))
    return {"caption": _text(fields.get("caption", ""), TITLE_CAP)}


# Part 6, Ask's typed frames (board.ask_frames builds them from the model's own, already checked): every key present,
# every list non-empty and within its cap, every number finite and within the board's range, never a boolean.

def _some(value: Any, cap: int) -> list[Any]:
    found = _list(value, cap)
    if not found:
        raise Refused(R_SHAPE)
    return found


def _value(value: Any) -> int | float:
    """A finite number of the board's range (an int is compared as it is: a huge one is never made a float)."""
    if type(value) is int:
        if -NUMBER_LIMIT < value < NUMBER_LIMIT:
            return value
    elif type(value) is float and math.isfinite(value) and abs(value) < NUMBER_LIMIT:
        return value
    raise Refused(R_SHAPE)


def _pattern(value: Any, cap: int, pattern: re.Pattern[str]) -> str:
    text = _text(value, cap)
    if not pattern.fullmatch(text):
        raise Refused(R_SHAPE)
    return text


def _table_body(body: Any) -> dict[str, Any]:
    """A table: Mono caps headers, rows of cells (every row as many as there are headers: words, a number's words
    included), the changed row (-1 none) and its words, a unit (the meta) and a source footer. Never paired."""
    keys = ("columns", "rows", "unit", "changed", "change", "source")
    fields = _fields(body, keys, keys)
    columns = [_text(item, COLUMN_CAP) for item in _some(fields["columns"], TABLE_COLUMNS_CAP)]
    rows = []
    for row in _some(fields["rows"], TABLE_ROWS_CAP):
        cells = [_text(cell, CELL_CAP) for cell in _list(row, TABLE_COLUMNS_CAP)]
        if len(cells) != len(columns):
            raise Refused(R_SHAPE)
        rows.append(cells)
    return {"columns": columns, "rows": rows, "unit": _text(fields["unit"], UNIT_CAP),
            "changed": _number(fields["changed"], -1, len(rows) - 1), "change": _text(fields["change"], CHANGE_CAP),
            "source": _text(fields["source"], SOURCE_CAP)}


def _chart_bar(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("label", "value", "text"), ("label", "value", "text"))
    return {"label": _text(fields["label"], LABEL_CAP), "value": _value(fields["value"]),
            "text": _pattern(fields["text"], NUMBER_TEXT_CAP, _VALUE_TEXT)}


def _chart_body(body: Any) -> dict[str, Any]:
    """Bars (a label, a value and its words), the changed bar (-1 none) with its old value (the ghost tick; null none)
    and its delta line, and a unit (the meta)."""
    keys = ("bars", "unit", "changed", "was", "change")
    fields = _fields(body, keys, keys)
    bars = [_chart_bar(item) for item in _some(fields["bars"], BARS_CAP)]
    changed = _number(fields["changed"], -1, len(bars) - 1)
    was = fields["was"]
    if was is not None:
        was = _value(was)
        if changed < 0:
            raise Refused(R_SHAPE)
    return {"bars": bars, "unit": _text(fields["unit"], UNIT_CAP), "changed": changed, "was": was,
            "change": _text(fields["change"], CHANGE_CAP)}


def _node(value: Any) -> dict[str, Any]:
    fields = _fields(value, ("when", "words", "state"), ("when", "words", "state"))
    return {"when": _text(fields["when"], WHEN_CAP), "words": _text(fields["words"], NODE_WORDS_CAP),
            "state": _choice(fields["state"], NODE_STATES)}


def _timeline_body(body: Any) -> dict[str, Any]:
    """Nodes (when, words, and done / next / due / "" - the state's word is its non-colour cue) and where the TODAY
    marker stands: before that node, 0 to the number of nodes (-1 none)."""
    fields = _fields(body, ("nodes", "today"), ("nodes", "today"))
    nodes = [_node(item) for item in _some(fields["nodes"], NODES_CAP)]
    return {"nodes": nodes, "today": _number(fields["today"], -1, len(nodes))}


def _expr(value: Any, depth: int, budget: list[int]) -> list[Any]:
    """One checked maths expression: ["num", words], ["name", letters] or [op, expression, ...] (MATHS_OPS and their
    arguments); at most MATHS_DEPTH levels, and the frame's MATHS_NODES nodes counted in ``budget``."""
    if depth > MATHS_DEPTH:
        raise Refused(R_SHAPE)
    budget[0] -= 1
    if budget[0] < 0:
        raise Refused(R_TOO_LONG)
    if type(value) is not list or not value or type(value[0]) is not str:
        raise Refused(R_SHAPE)
    head = value[0]
    if head in ("num", "name"):
        if len(value) != 2:
            raise Refused(R_SHAPE)
        if head == "num":
            return ["num", _pattern(value[1], NUMBER_TEXT_CAP, _NUMBER_TEXT)]
        return ["name", _pattern(value[1], NAME_LETTERS, _MATHS_NAME)]
    arity = MATHS_OPS.get(head)
    if arity is None or not arity[0] <= len(value) - 1 <= arity[1]:
        raise Refused(R_SHAPE)
    return [head, *(_expr(item, depth + 1, budget) for item in value[1:])]


def _step(value: Any, budget: list[int]) -> dict[str, Any]:
    fields = _fields(value, ("name", "expr"), ("name", "expr"))
    return {"name": _text(fields["name"], STEP_NAME_CAP), "expr": _expr(fields["expr"], 1, budget)}


def _maths_body(body: Any) -> dict[str, Any]:
    """Numbered steps (a name and an expression) and the answer (boxed): expressions from the restricted tree only."""
    fields = _fields(body, ("steps", "answer"), ("steps", "answer"))
    budget = [MATHS_NODES]
    steps = [_step(item, budget) for item in _some(fields["steps"], STEPS_CAP)]
    return {"steps": steps, "answer": _step(fields["answer"], budget)}


def _number_body(body: Any) -> dict[str, Any]:
    """A big number (the canvas, part 2; the app builds it from a chart's or a table's change): the figure - a signed
    number's words -, what it was and is, and where it comes from. Every key present."""
    keys = ("figure", "text", "source")
    fields = _fields(body, keys, keys)
    return {"figure": _pattern(fields["figure"], NUMBER_TEXT_CAP, _FIGURE_TEXT),
            "text": _text(fields["text"], CHANGE_CAP), "source": _text(fields["source"], SOURCE_CAP)}


def _approval_row(value: Any) -> dict[str, Any]:
    keys = ("kind", "title")
    fields = _fields(value, keys, keys)
    return {"kind": _text(fields["kind"], META_CAP), "title": _text(fields["title"], TITLE_CAP)}


def _approvals_body(body: Any) -> dict[str, Any]:
    """The "waits for your OK" frame (the canvas, part 3): how many cards wait (at least one, and at least its rows)
    and the first few, each its kind label and title. Every key present."""
    keys = ("count", "rows")
    fields = _fields(body, keys, keys)
    rows = [_approval_row(item) for item in _list(fields["rows"], ROWS_CAP)]
    return {"count": _number(fields["count"], max(1, len(rows)), APPROVALS_MAX), "rows": rows}


def _agent_row(value: Any) -> dict[str, Any]:
    keys = ("name", "state", "last", "came", "next", "waits")
    fields = _fields(value, keys, keys)
    return {"name": _text(fields["name"], TITLE_CAP), "state": _choice(fields["state"], AGENT_STATES),
            "last": _text(fields["last"], WHEN_CAP), "came": _number(fields["came"], -1, RESTING_COUNT_MAX),
            "next": _text(fields["next"], WHEN_CAP), "waits": _number(fields["waits"], 0, RESTING_COUNT_MAX)}


def _agents_body(body: Any) -> dict[str, Any]:
    """The Agents frame (the canvas, part 3): one row per standing agent (1 to ROWS_CAP), the agents past them, and how
    many of their proposals wait in all. Every key present."""
    keys = ("rows", "more", "waits")
    fields = _fields(body, keys, keys)
    rows = [_agent_row(item) for item in _some(fields["rows"], ROWS_CAP)]
    return {"rows": rows, "more": _number(fields["more"], 0, RESTING_COUNT_MAX),
            "waits": _number(fields["waits"], min(RESTING_COUNT_MAX, sum(row["waits"] for row in rows)),
                             RESTING_COUNT_MAX)}


KINDS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "text": _text_body, "calendar": _calendar_body, "mail_thread": _mail_thread_body, "mail_list": _mail_list_body,
    "todos": _todos_body, "picture": _picture_body, "capture": _capture_body,
    "table": _table_body, "chart": _chart_body, "timeline": _timeline_body, "maths": _maths_body,
    "number": _number_body,
    "approvals": _approvals_body, "agents": _agents_body,
}


# --------------------------------------------------------------------------
# In: the app's messages
# --------------------------------------------------------------------------

_FRAME_FIELDS = ("type", "id", "kind", "title", "meta", "body", "status", "status_text", "status_meta", "waits",
                 "changed", "pinned", "closed", "fact", "group")


def _frame(value: dict[str, Any], known: Collection[str], groups: Collection[str]) -> tuple[dict[str, Any], str, str]:
    fields = _fields(value, _FRAME_FIELDS, ("type", "id", "kind", "title"))
    frame_id = _frame_id(fields["id"])
    if frame_id not in known and len(known) >= MAX_FRAMES:
        raise Refused(R_FULL)
    kind = fields["kind"]
    if type(kind) is not str or kind not in KINDS:
        raise Refused(R_KIND)
    clean = {
        "type": "frame", "id": frame_id, "kind": kind,
        "title": _text(fields["title"], TITLE_CAP),
        "meta": _text(fields.get("meta", ""), META_CAP),
        "body": KINDS[kind](fields.get("body", {})),
        "status": _choice(fields.get("status", "ok"), STATUSES),
        "status_text": _text(fields.get("status_text", ""), WORDS_CAP),
        "status_meta": _text(fields.get("status_meta", ""), META_CAP),
        "waits": _text(fields.get("waits", ""), WAITS_CAP),
        "changed": _flag(fields.get("changed", False)),
        "pinned": _flag(fields.get("pinned", False)),
        "closed": _flag(fields.get("closed", False)),
    }
    if "fact" in fields:        # part 5b: a satellite's one fact (only when given)
        clean["fact"] = _text(fields["fact"], FACT_CAP)
    group_id = ""
    if "group" in fields:       # part 5a: the answer it belongs to (only when given: a group the app sent)
        group_id = clean["group"] = _known_id(fields["group"], groups)
    return clean, frame_id, group_id


def _group(value: dict[str, Any], known: Collection[str], groups: Collection[str]) -> tuple[dict[str, Any], str]:
    """One answer's group: its header's time and topic, its frames in board order, whether it is older than the
    newest (its header in INK_4) and, when given, whether it is parked (part 5b: out of sight until it lands)."""
    fields = _fields(value, ("type", "id", "time", "topic", "frames", "older", "parked", "below", "rest"),
                     ("type", "id", "time", "topic", "frames"))
    group_id = _frame_id(fields["id"])
    if group_id not in groups and len(groups) >= MAX_GROUPS:
        raise Refused(R_FULL)
    order = [_known_id(item, known) for item in _list(fields["frames"], MAX_FRAMES)]
    if len(set(order)) != len(order):
        raise Refused(R_ID)
    clean = {"type": "group", "id": group_id, "time": _text(fields["time"], TIME_CAP),
             "topic": _text(fields["topic"], TOPIC_CAP), "frames": order,
             "older": _flag(fields.get("older", False))}
    if "parked" in fields:
        clean["parked"] = _flag(fields["parked"])
    if "below" in fields:       # part 3B: a new group goes under the groups the page has (the resting group)
        clean["below"] = _flag(fields["below"])
    if "rest" in fields:        # the resting group "Now": its frames in even rows on the canvas
        clean["rest"] = _flag(fields["rest"])
    return clean, group_id


def _cleared(value: dict[str, Any], known: Collection[str]) -> dict[str, Any]:
    """The frames the bar's Clear took away (out of sight while Bring back is offered): ids the app sent, each once."""
    fields = _fields(value, ("type", "frames"), ("type", "frames"))
    ids = [_known_id(item, known) for item in _list(fields["frames"], MAX_FRAMES)]
    if len(set(ids)) != len(ids):
        raise Refused(R_ID)
    return {"type": "cleared", "frames": ids}


def _bar(value: dict[str, Any]) -> dict[str, Any]:
    """The board bar's counts: frames (at most MAX_FRAMES), how many wait on the owner and how many are pinned."""
    fields = _fields(value, ("type", "frames", "waits", "pinned", "compact"), ("type", "frames"))
    frames = _number(fields["frames"], 0, MAX_FRAMES)
    return {"type": "bar", "frames": frames, "waits": _number(fields.get("waits", 0), 0, frames),
            "pinned": _number(fields.get("pinned", 0), 0, frames), "compact": _flag(fields.get("compact", False))}


def jpeg_src(src: Any) -> str:
    """``src`` when it is ONE JPEG as a data:image/jpeg;base64 URL within the picture's caps (at most PICTURE_CAP
    characters, a JPEG of at most PICTURE_BYTES), else Refused(R_PICTURE). A picture's and a backdrop's."""
    if type(src) is not str or len(src) > PICTURE_CAP or not src.startswith(PICTURE_PREFIX):
        raise Refused(R_PICTURE)
    data = src[len(PICTURE_PREFIX):]
    if not data or len(data) % 4 or not _BASE64.fullmatch(data):
        raise Refused(R_PICTURE)
    try:
        jpeg = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise Refused(R_PICTURE) from None
    if len(jpeg) > PICTURE_BYTES or not jpeg.startswith(_JPEG):
        raise Refused(R_PICTURE)
    return src


def _picture(value: dict[str, Any], known: Collection[str]) -> tuple[dict[str, Any], str]:
    fields = _fields(value, ("type", "id", "src"), ("type", "id", "src"))
    frame_id = _known_id(fields["id"], known)
    return {"type": "picture", "id": frame_id, "src": jpeg_src(fields["src"])}, frame_id


def _lit(value: dict[str, Any], known: Collection[str]) -> tuple[dict[str, Any], str]:
    """The frame Jarvis speaks of ("" none) and, when given, whether the app draws the cue line's native part to it
    (the page then draws its 15 px stub)."""
    fields = _fields(value, ("type", "id", "cue"), ("type", "id"))
    clean: dict[str, Any] = {"type": "lit", "id": ""}
    frame_id = ""
    if fields["id"] != "":
        frame_id = clean["id"] = _known_id(fields["id"], known)
    if "cue" in fields:
        clean["cue"] = _flag(fields["cue"]) and bool(frame_id)
    return clean, frame_id


def _big(value: dict[str, Any], known: Collection[str]) -> tuple[dict[str, Any], str]:
    """Open big (DESIGN-SPEC 3.5): open - the frame (one the app sent) and the board's own box in the wide view;
    close - back to the board (its "id" "" or the frame's, no box)."""
    state = value.get("state") if type(value) is dict else None
    if state == "open":
        fields = _fields(value, ("type", "state", "id", "x", "y", "w", "h"), ("type", "state", "id", "x", "y", "w", "h"))
        frame_id = _known_id(fields["id"], known)
        box = {name: _number(fields[name], 0, BIG_LIMIT) for name in ("x", "y", "w", "h")}
        if box["w"] < 1 or box["h"] < 1:
            raise Refused(R_SHAPE)
        return {"type": "big", "state": "open", "id": frame_id, **box}, frame_id
    fields = _fields(value, ("type", "state", "id"), ("type", "state"))
    _choice(fields["state"], BIG_STATES)
    frame_id = ""
    if fields.get("id", "") != "":
        frame_id = _known_id(fields["id"], known)
    return {"type": "big", "state": "close", "id": frame_id}, frame_id


def _orb(value: dict[str, Any]) -> dict[str, Any]:
    """The orb (the canvas, part 1): a state word, a form, the native ring's two modifiers, quiet hours' dim and the
    two levels - whole numbers from 0 to LEVEL_MAX, never a boolean, never a fraction."""
    fields = _fields(value, ("type", "state", "form", "muted", "offline", "dim", "away", "level", "voice"),
                     ("type", "state", "form"))
    return {"type": "orb", "state": _choice(fields["state"], ORB_STATES), "form": _choice(fields["form"], ORB_FORMS),
            "muted": _flag(fields.get("muted", False)), "offline": _flag(fields.get("offline", False)),
            "dim": _flag(fields.get("dim", False)), "away": _flag(fields.get("away", False)),
            "level": _number(fields.get("level", 0), 0, LEVEL_MAX),
            "voice": _number(fields.get("voice", 0), 0, LEVEL_MAX)}


def _canvas(value: dict[str, Any]) -> dict[str, Any]:
    """The canvas's corners (part 1): words the page sets as text, a count, and which of its own buttons show (words
    of CANVAS_BUTTONS, each once). A button's label is never among them."""
    fields = _fields(value, ("type", "clock", "word", "tone", "approvals", "buttons", "briefing_new", "hold", "rec",
                             "wake", "meter", "hear", "deaf", "mic", "side"), ("type", "buttons"))
    buttons = [_choice(item, CANVAS_BUTTONS) for item in _list(fields["buttons"], len(CANVAS_BUTTONS))]
    if len(set(buttons)) != len(buttons):
        raise Refused(R_FIELD)
    clean = {"type": "canvas", "clock": _text(fields.get("clock", ""), CLOCK_CAP),
            "word": _text(fields.get("word", ""), STATE_WORD_CAP),
            "tone": _choice(fields.get("tone", "holo"), CANVAS_TONES),
            "approvals": _number(fields.get("approvals", 0), 0, APPROVALS_MAX), "buttons": buttons,
            "briefing_new": _flag(fields.get("briefing_new", False)),
            "hold": _text(fields.get("hold", ""), HOLD_CAP), "rec": _text(fields.get("rec", ""), REC_CAP),
            "wake": _text(fields.get("wake", ""), WAKE_CAP), "meter": _flag(fields.get("meter", False)),
            "hear": _choice(fields.get("hear", ""), HEAR_STATES), "deaf": _flag(fields.get("deaf", False)),
            "mic": _text(fields.get("mic", ""), MIC_CAP)}
    if "side" in fields:
        clean["side"] = _side(fields["side"])
    return clean


def _side(value: Any) -> list[int]:
    """The side tile's box (canvas part 5B): [width, height], whole numbers 0 to SIDE_MAX."""
    pair = _list(value, 2)
    if len(pair) != 2:
        raise Refused(R_SHAPE)
    return [_number(pair[0], 0, SIDE_MAX), _number(pair[1], 0, SIDE_MAX)]


def _task(value: dict[str, Any], groups: Collection[str]) -> tuple[dict[str, Any], str]:
    """The task card (the canvas, part 2): its state, the app's own words for what Jarvis is doing, the seconds and
    the counts so far, the group it heads once done (one the app sent; "" none) and whether Stop works now."""
    fields = _fields(value, ("type", "state", "title", "words", "seconds", "steps", "sources", "frames", "group",
                             "stop"), ("type", "state"))
    group = fields.get("group", "")
    group_id = _known_id(group, groups) if group != "" else ""
    return {"type": "task", "state": _choice(fields["state"], TASK_STATES),
            "title": _text(fields.get("title", ""), TOPIC_CAP), "words": _text(fields.get("words", ""), TASK_WORDS_CAP),
            "seconds": _number(fields.get("seconds", 0), 0, TASK_SECONDS_MAX),
            "steps": _number(fields.get("steps", 0), 0, TASK_COUNT_MAX),
            "sources": _number(fields.get("sources", 0), 0, TASK_COUNT_MAX),
            "frames": _number(fields.get("frames", 0), 0, MAX_FRAMES), "group": group_id,
            "stop": _flag(fields.get("stop", False))}, group_id


def check_message(message: Any, known: Collection[str] = (), groups: Collection[str] = ()) -> Checked:
    """``message`` as the page gets it (see the module doc), or Refused. ``known``: the ids of the frames the app sent;
    ``groups``: those of the groups it sent."""
    if type(message) is not dict:
        raise Refused(R_SHAPE)
    kind = message.get("type")
    if type(kind) is not str or kind not in MESSAGE_TYPES:
        raise Refused(R_TYPE)
    image = 0
    frame_id = group_id = ""
    if kind == "frame":
        clean, frame_id, group_id = _frame(message, known, groups)
    elif kind == "group":
        clean, group_id = _group(message, known, groups)
    elif kind == "remove_group":
        fields = _fields(message, ("type", "id"), ("type", "id"))
        group_id = _known_id(fields["id"], groups)
        clean = {"type": "remove_group", "id": group_id}
    elif kind == "reveal":
        fields = _fields(message, ("type", "group"), ("type", "group"))
        group_id = _known_id(fields["group"], groups)
        clean = {"type": "reveal", "group": group_id}
    elif kind == "bar":
        clean = _bar(message)
    elif kind == "desk":
        fields = _fields(message, ("type", "state"), ("type", "state"))
        clean = {"type": "desk", "state": _choice(fields["state"], DESK_STATES)}
    elif kind == "cleared":
        clean = _cleared(message, known)
    elif kind == "focus_part":
        fields = _fields(message, ("type", "part"), ("type", "part"))
        clean = {"type": "focus_part", "part": _choice(fields["part"], FOCUS_PARTS)}
    elif kind == "picture":
        clean, frame_id = _picture(message, known)
        image = len(clean["src"])
    elif kind == "backdrop":
        fields = _fields(message, ("type", "src"), ("type", "src"))
        clean = {"type": "backdrop", "src": jpeg_src(fields["src"])}
        image = len(clean["src"])
    elif kind == "big":
        clean, frame_id = _big(message, known)
    elif kind == "lit":
        clean, frame_id = _lit(message, known)
    elif kind == "remove":
        fields = _fields(message, ("type", "id"), ("type", "id"))
        frame_id = _known_id(fields["id"], known)
        clean = {"type": kind, "id": frame_id}
    elif kind == "clear":
        _fields(message, ("type",))
        clean = {"type": "clear"}
    elif kind == "orb":
        clean = _orb(message)
    elif kind == "canvas":
        clean = _canvas(message)
    elif kind == "task":
        clean, group_id = _task(message, groups)
    elif kind == "chrome":
        fields = _fields(message, ("type", "hover"), ("type", "hover"))
        clean = {"type": "chrome", "hover": _flag(fields["hover"])}
    else:   # motion
        fields = _fields(message, ("type", "level"), ("type", "level"))
        clean = {"type": "motion", "level": _choice(fields["level"], MOTION_LEVELS)}
    try:
        text = json.dumps(clean, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise Refused(R_SHAPE) from None
    if len(text) - image > MESSAGE_CAP:
        raise Refused(R_SIZE)
    return Checked(kind, text, frame_id, group_id)


# --------------------------------------------------------------------------
# Out: the page's requests
# --------------------------------------------------------------------------

def _no_constants(name: str) -> Any:
    raise Refused(R_JSON)   # NaN, Infinity, -Infinity: not JSON


def _place(value: Any, low: int) -> int:
    if type(value) is not int or not low <= value <= PLACE_LIMIT:
        raise Refused(R_SHAPE)
    return value


def _box(value: Any) -> tuple[int, int, int, int]:
    """[x, y, w, h]: whole px, the place inside BIG_LIMIT and a size of at least 0."""
    if type(value) is not list or len(value) != 4:
        raise Refused(R_SHAPE)
    for item in value:
        if type(item) is not int or not 0 <= item <= BIG_LIMIT:
            raise Refused(R_SHAPE)
    return value[0], value[1], value[2], value[3]


def _chrome_request(value: Any) -> Request:
    """The page's top band for Windows' hit-test (the window brief): the width it measured, the band's height, its
    controls' boxes (at most CHROME_HOLES) and the maximise button's box ([] none)."""
    fields = _fields(value, ("w", "bar", "holes", "max"), ("w", "bar", "holes", "max"))
    width, bar = fields["w"], fields["bar"]
    if type(width) is not int or not 0 <= width <= BIG_LIMIT or type(bar) is not int or not 0 <= bar <= CHROME_BAR_MAX:
        raise Refused(R_SHAPE)
    holes = fields["holes"]
    if type(holes) is not list or len(holes) > CHROME_HOLES:
        raise Refused(R_SHAPE)
    box = fields["max"]
    maximize: tuple[int, ...] = () if box == [] else _box(box)
    return Request("chrome", width=width, bar=bar, holes=tuple(_box(item) for item in holes), maximize=maximize)


def check_request(kind: Any, payload: Any, known: Collection[str]) -> Request:
    """The page's ``post(kind, payload)`` as a Request (see the module doc), or Refused."""
    if type(kind) is not str or kind not in REQUEST_KINDS:
        raise Refused(R_KIND)
    if type(payload) is not str:
        raise Refused(R_SHAPE)
    if len(payload) > PAYLOAD_CAP:
        raise Refused(R_SIZE)
    try:
        value = json.loads(payload, parse_constant=_no_constants)
    except Refused:
        raise
    except (ValueError, RecursionError):
        raise Refused(R_JSON) from None
    if kind == "ready":
        fields = _fields(value, ("v",))
        if "v" in fields and (type(fields["v"]) is not int or fields["v"] != 1):
            raise Refused(R_FIELD)
        return Request("ready")
    if kind == "layout":
        fields = _fields(value, ("frames", "scrolling"), ("frames",))
        frames = fields["frames"]
        if type(frames) is not list or len(frames) > MAX_FRAMES:
            raise Refused(R_SHAPE)
        places: list[tuple[str, int, int]] = []
        for item in frames:
            place = _fields(item, ("id", "top", "height"), ("id", "top", "height"))
            places.append((_known_id(place["id"], known), _place(place["top"], -PLACE_LIMIT),
                           _place(place["height"], 0)))
        if len({place[0] for place in places}) != len(places):
            raise Refused(R_ID)
        return Request("layout", places=tuple(places), scrolling=_flag(fields.get("scrolling", False)))
    if kind in ("pin", "hold"):
        fields = _fields(value, ("id", "on"), ("id", "on"))
        return Request(kind, _known_id(fields["id"], known), on=_flag(fields["on"]))
    if kind in _BARE_KINDS:
        _fields(value, ())
        return Request(kind)
    if kind == "reading":
        fields = _fields(value, ("pointer", "focus"), ("pointer", "focus"))
        return Request("reading", pointer=_flag(fields["pointer"]), focused=_flag(fields["focus"]))
    if kind == "focus_out":
        fields = _fields(value, ("to",), ("to",))
        return Request("focus_out", to=_choice(fields["to"], FOCUS_OUT))
    if kind == "landed":
        fields = _fields(value, ("id", "step"), ("id", "step"))
        return Request("landed", _known_id(fields["id"], known), step=_choice(fields["step"], LANDED_STEPS))
    if kind == "big":
        fields = _fields(value, ("step",), ("step",))
        return Request("big", step=_choice(fields["step"], BIG_STEPS))
    if kind == "go":
        fields = _fields(value, ("to",), ("to",))
        return Request("go", to=_choice(fields["to"], GO_WORDS))
    if kind == "chrome":
        return _chrome_request(value)
    if kind not in _ID_KINDS:
        raise Refused(R_KIND)
    fields = _fields(value, ("id",), ("id",))
    return Request(kind, _known_id(fields["id"], known))
