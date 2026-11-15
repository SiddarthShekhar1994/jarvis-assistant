"""The lit frame and the cue line (the new look, part 5c; DESIGN-SPEC 1 "the lock-on", 3.4 "Lit", 5 and 8): while
Jarvis speaks, the sentence being said lights the frame it is about - named by that frame's own words, never guessed
(board.lit) - every ring's core glow halves, and a thin cue line runs from the end of that sentence in the conversation's
current turn to the seam and along it (the native part: desk_view.CueLine, never over the board's view); the page draws
the last 15 px, from the view's left edge to the frame, from the frame's own place.

``CueController(desk)`` is desk_ui.DeskController's (``desk.cue``):

    on_speech(text)      an utterance started ("": he is done, was stopped or muted); LookController.on_speaking hands
                         it on. Every TICK_MS while he speaks the controller reads how far his voice is (the app's
                         _voice_timeline() and _voice_position()), finds the sentence being said and the frame it names
    on_muted()           his voice was muted: nothing is lit
    refresh()            the board changed (frames came, left, folded): the sentence is matched again
    on_layout(layout)    the page's layout (board.view.BoardLayout): where the frames are; while it scrolls the native
                         part hides, and it is back at once (no draw-in) with the layout after the scroll
    clear(now=False)     nothing lit now (the desk left Home, open big covers the column); it is matched again later.
                         ``now``: the native part goes at once, no fade (open big: it is never drawn over the wide view)
    lit() / path()       the frame lit ("" none) and the native part's path (None when it is not drawn)

Part 5c fixes: the same sentence (never a timer of its own) also dims the words already said - in the conversation's
current turn, every sentence before the one being said reads in INK_3 (desk_view.DeskTurns.set_read) while the desk is
seen; when the speech ends, is stopped or muted, the turn eases back to its own colours.

The page gets ``{"type": "lit", "id", "cue"}``: the brackets, its TILE_FOCUS / TILE_AMBER_LIT ground and, when "cue",
its stub (drawn in after the native part's 200 ms; at once when the cue comes back for the same frame). The lit frame
clears when the speech ends, is stopped or muted, when the desk is not on screen (another screen, folded, the window
down), while a frame is open big, and when its frame leaves the board.

Logs: none here; ``counts`` holds counts only (lit, unlit, matched sentences).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QPoint, QPointF, QTimer

from .board import lit as lit_mod
from .desk_view import CuePath

TICK_MS = 100               # how often the voice's progress is read while he speaks
BOARD_TOP = 44              # the page's frames area: from 44 px down (its bar) ...
BOARD_BOTTOM = 24           # ... to 24 px above the view's bottom ("N more below"): board.css's #board on the desk
MIN_SEEN = 4                # px of a frame in sight for the cue line to reach it


def anchor(top: int, height: int, view_height: int) -> int | None:
    """Where the cue line meets a frame, in the view's px: the middle of the part of it in the frames area (the page's
    cueY() does the same with the same whole numbers); None when less than MIN_SEEN px of it is in sight."""
    low = max(int(top), BOARD_TOP)
    high = min(int(top) + int(height), int(view_height) - BOARD_BOTTOM)
    if high - low < MIN_SEEN:
        return None
    return (low + high) // 2


class CueController(QObject):
    """See the module doc."""

    def __init__(self, desk: Any) -> None:
        super().__init__(desk)
        self.desk = desk
        self.reading = desk.reading
        self.host = desk.host
        self._text = ""
        self._spans: list[tuple[int, int]] = []
        self._index = -1
        self._dirty = True
        self._lit = ""
        self._sent: tuple[str, bool] | None = None      # the lit message the page has: (id, cue)
        self._shown_for = ""                            # the frame the native part is drawn to now
        self._places: dict[str, tuple[int, int]] = {}
        self._scrolling = False
        self._read_entry: Any = None                    # the turn whose words said so far are dimmed (its entry id)
        self._watched: Any = None                       # the turns whose rebuilds it follows
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.counts: Counter[str] = Counter()

    # ---- what the app tells it -----------------------------------------------------------------------------------

    def on_speech(self, text: str) -> None:
        text = str(text or "")
        if text == self._text:
            return
        self._text = text
        self._spans = lit_mod.sentence_spans(text)
        self._index = -1
        self._dirty = True
        if text:
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()
            self._set_lit("")
            self._unmark_read()                         # the whole turn back to its own colours (eased when lively)

    def on_muted(self) -> None:
        self.on_speech("")

    def refresh(self) -> None:
        self._dirty = True
        if self._text:
            self._tick()

    def clear(self, *, now: bool = False) -> None:
        self._dirty = True
        self._set_lit("")
        if now:
            line = self._line()
            if line is not None:
                line.clear()                            # at once: a fade would be drawn over the wide view

    def reset_page(self) -> None:
        """The page came back empty (its renderer stopped): it has no lit frame; matched and sent again."""
        self._sent = None
        self._dirty = True
        self._set_lit("")

    def on_layout(self, layout: Any) -> None:
        self._places = {frame_id: (int(top), int(height)) for frame_id, top, height in getattr(layout, "places", ())}
        self._scrolling = bool(getattr(layout, "scrolling", False))
        line = self._line()
        if line is not None:
            line.set_hidden(self._scrolling)
        if self._lit:
            self._place()

    # ---- reading -------------------------------------------------------------------------------------------------

    def lit(self) -> str:
        return self._lit

    def path(self) -> CuePath | None:
        line = self._line()
        return line.path() if line is not None else None

    def sentence(self) -> str:
        """The sentence being said now ("" none or not known)."""
        if 0 <= self._index < len(self._spans):
            start, end = self._spans[self._index]
            return self._text[start:end]
        return ""

    def _line(self) -> Any:
        desk = self.reading.desk_page()
        return desk.cue_line if desk is not None and shiboken6.isValid(desk) else None

    def on_screen(self) -> bool:
        """The desk is what the owner sees: Home, the desk shown, the window up, the board's page ready, no frame open
        big."""
        if not self.desk.on_screen() or self.reading.board_big():
            return False
        board = self.desk.board
        return board is not None and shiboken6.isValid(board) and board.is_ready()

    # ---- each tick -----------------------------------------------------------------------------------------------

    def _tick(self) -> None:
        if not self._text:
            return
        if not self.desk.on_screen():                    # the desk is not seen: nothing lit, the turn as it is
            if self._lit:
                self._set_lit("")
            self._unmark_read(at_once=True)
            self._dirty = True
            return
        index = self._sentence_now()
        if not self.on_screen():                          # open big, or the board not ready: nothing lit, while the
            if self._lit:                                 # turn's words said so far still follow his voice
                self._set_lit("")
            self._dirty = True
            self._index = index
            self._mark_read()
            return
        if index != self._index or self._dirty:
            self._index = index
            self._dirty = False
            self._choose()
        elif self._lit:
            self._place()
        self._mark_read()

    def _sentence_now(self) -> int:
        host = self.host
        try:
            timeline = host._voice_timeline()
            position = host._voice_position()
        except Exception:  # noqa: BLE001 - not known: nothing lit
            return -1
        words = getattr(timeline, "words", ()) or ()
        duration = getattr(timeline, "duration_ms", 0) or 0
        offset = lit_mod.spoken_offset(self._text, words, duration, position)
        return lit_mod.sentence_at(self._spans, offset)

    def frames(self) -> dict[str, dict[str, Any]]:
        """Every frame on the board, by id: a closed one until it leaves, one a Clear took away until its Bring back
        ends, a parked one - each still shares its words (never a guess), though only one in sight is ever lit."""
        feed = self.desk.feed
        found: dict[str, dict[str, Any]] = {}
        for frame_id in feed.frame_ids():
            message = feed.frame_message(frame_id)
            if message is not None:
                found[frame_id] = message
        return found

    def in_sight(self, frame_id: str) -> bool:
        feed = self.desk.feed
        flags = feed.flags(frame_id)
        return bool(flags) and not flags.get("closed") and not flags.get("cleared") \
            and not feed.is_parked(feed.group_of(frame_id))

    def _choose(self) -> None:
        sentence = self.sentence()
        frame_id = lit_mod.match(sentence, self.frames()) if sentence else ""
        if frame_id and not self.in_sight(frame_id):
            frame_id = ""                                # named, but out of sight: nothing lit
        self.counts["matched" if frame_id else "unmatched"] += 1 if sentence else 0
        if frame_id != self._lit:
            self._set_lit(frame_id)
        else:
            self._place()

    # ---- lighting ------------------------------------------------------------------------------------------------

    def _set_lit(self, frame_id: str) -> None:
        old, self._lit = self._lit, frame_id
        if frame_id == old:
            return
        self.counts["lit" if frame_id else "unlit"] += 1
        self.reading.set_frame_lit(bool(frame_id))
        line = self._line()
        if line is not None and self._shown_for:
            line.fade()                                  # the line before goes (400 ms; off: at once)
        self._shown_for = ""
        if frame_id:
            self._place(new=True)
        else:
            self._send(("", False))

    def _place(self, *, new: bool = False) -> None:
        """The native part for the lit frame now (drawn in when ``new``), and the page's lit message when it differs."""
        frame_id = self._lit
        if not frame_id:
            return
        line = self._line()
        path = self._path_now()
        if line is not None:
            if path is None:
                if self._shown_for:
                    line.fade()
                self._shown_for = ""
            elif self._shown_for == frame_id:
                line.move_path(path)
            else:
                line.show_path(path, draw=new)
                self._shown_for = frame_id
            line.set_hidden(self._scrolling)
        self._send((frame_id, bool(path is not None and self._shown_for == frame_id)))

    # ---- the words already said (part 5c fixes) --------------------------------------------------------------------

    def _turns(self) -> Any:
        """The desk's turns (DeskTurns), followed: when they are built again the current turn's said words are found
        again at once (the turn being said may be another one now)."""
        desk = self.reading.desk_page()
        if desk is None or not shiboken6.isValid(desk):
            return None
        turns = desk.turns
        if turns is not self._watched:
            self._watched = turns
            turns.turnsChanged.connect(self._on_turns_changed)
        return turns

    def _on_turns_changed(self) -> None:
        if self._text and self._index >= 0 and self.desk.on_screen():
            self._mark_read()

    def read_end(self, text: str) -> int:
        """Where the sentence being said begins in ``text`` (the current turn's words): everything before it is said;
        -1 when it is not known there (the turn stays as it is)."""
        sentence = self.sentence()
        if not sentence:
            return -1
        start = lit_mod.locate_start(sentence, text)
        if start >= 0:
            return start
        if self._index > 0:                               # said otherwise: just past the sentence before it
            first, last = self._spans[self._index - 1]
            end = lit_mod.locate(self._text[first:last], text)
            if end >= 0:
                while end < len(text) and text[end].isspace():
                    end += 1
                return end
        return -1

    def _mark_read(self) -> None:
        """The current turn's words before the sentence being said read in INK_3 (DeskTurns.set_read)."""
        turns = self._turns()
        view = turns.current_view() if turns is not None else None
        if view is None or view.you():
            return
        end = self.read_end(view.turn.text)
        if end < 0:
            return
        entry = view.turn.entry_id
        turns.set_read(entry, end, at_once=entry != self._read_entry)   # a turn first marked (or another): at once
        self._read_entry = entry

    def _unmark_read(self, *, at_once: bool = False) -> None:
        turns = self._turns()
        if turns is not None and turns.read()[0] is not None:
            turns.set_read(None, 0, at_once=at_once)
        self._read_entry = None

    def _send(self, state: tuple[str, bool]) -> None:
        if state == self._sent:
            return
        self._sent = state
        frame_id, cue = state
        self.desk._send([{"type": "lit", "id": frame_id, "cue": cue}])

    def _path_now(self) -> CuePath | None:
        """Where the native part runs now: from beside the sentence's end in the current turn (in sight in the
        turns) to the seam, then to the lit frame's anchor (from the page's layout); None when either end is out of
        sight or not known."""
        desk = self.reading.desk_page()
        board = self.desk.board
        if desk is None or board is None or not shiboken6.isValid(board):
            return None
        place = self._places.get(self._lit)
        if place is None:
            return None
        meet = anchor(place[0], place[1], board.height())
        if meet is None:
            return None
        turns = desk.turns
        view = turns.current_view()
        sentence = self.sentence()
        if view is None or not sentence:
            return None
        end = lit_mod.locate(sentence, view.turn.text)
        point = view.text_point(end, line_end=True) if end >= 0 else None   # never across the next sentence's words
        if point is None:
            return None
        seen = view.mapTo(turns.viewport(), point)
        if not (0.0 <= seen.y() <= turns.viewport().height()):
            return None                                  # the sentence is scrolled out of the conversation's sight
        start = view.mapTo(desk, point)
        geometry = desk.geometry_now()
        message = self.desk.feed.frame_message(self._lit) or {}
        top = board.mapTo(desk, QPoint(0, 0)).y()
        return CuePath(QPointF(start), geometry.seam.x() + 0.5, top + meet + 0.5, bool(message.get("waits")))

    def shutdown(self) -> None:
        self._timer.stop()


__all__ = ["BOARD_BOTTOM", "BOARD_TOP", "CueController", "MIN_SEEN", "TICK_MS", "anchor"]
