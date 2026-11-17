"""The canvas's controller (PLAN-canvas part 1): Home is the board's page, with the orb in it.

``CanvasController(desk)`` is made by look_ui.LookController with the desk (desk_ui.DeskController) while ``[look]
board`` is on, a board can be made here and it is the canvas's (the real board always; a test's factory says so with
``factory.canvas = True``). It

* turns canvas mode on (ui.ReadingView.set_canvas) before the window first shows, and starts the board's page WITH the
  window (never at the first Ask): START_AFTER_MS after the window is up, so the window paints first - the canvas is
  plain black with the ask line until its page is ready, then the orb assembles; nothing flashes;
* tells the page two things, as board.protocol's checked messages and never anything else: the orb (``orb_message``:
  the state word, the form - canvas_view.orb_form: the corner while the board shows frames, the badge while Jarvis
  speaks and BADGE_LINGER_MS after, else the rings - the native ring's muted / offline, quiet hours' dim, "away" while
  Jarvis's window is not the active one (the orb at rest is then drawn 10 times a second, not 30), and the two levels: the microphone's, which the app measures only while the talk key or "Hey Jarvis" listens, and his voice's,
  which the app already computes for its ring) and the corners (``canvas_message``: the date and time, the state word
  and its tone, how many cards wait, which of the page's own buttons can work now, Briefing ready, a Send's hold, REC,
  "Hey Jarvis", whether the meter shows). A message goes only when it differs from the last one sent; the levels at
  most every LEVEL_MS, and only while the owner can see the canvas;
* takes the page's ``go`` requests (``on_go``): each word does exactly what its native counterpart does - a screen is
  the Dock's button, History the Transcript, the clock the Day view, Desktop the Dock's switch, Mute the header's
  speaker button, Undo the hold's, Stop REC's, the window's three its own - and a word whose control is not available
  now does nothing (counted);
* keeps the keyboard in the ask line when the window opens, when Home comes back and after an answer arrives (never
  while the owner's keyboard is in the board);
* falls back when the page can't start or its renderer dies for good (the board's ``unavailable``): canvas mode off -
  Home's native ring, the Dock's buttons to every screen, the ask line, and the read-only tiles in the desk.

Logs: reason words, counts and durations only.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from . import canvas_view
from . import dock as dock_mod
from . import orb as orb_mod
from .board import protocol

logger = logging.getLogger(__name__)

START_AFTER_MS = 60               # the board starts this long after the window is up (it paints first)
SYNC_MS = 500                     # the corners and the orb are compared with what the page has this often ...
LEVEL_MS = 50                     # ... and the levels this often while he listens or speaks
BADGE_LINGER_MS = 2600            # the badge stays this long after he stops speaking
PLACEHOLDER_TALK = "Type, or hold the key and talk"
PLACEHOLDER_TYPE = "Type a request"

# A page button's word -> the Dock's slot it presses.
_SCREEN_SLOTS = {"briefing": dock_mod.BRIEFING, "approvals": dock_mod.APPROVALS, "followups": dock_mod.FOLLOWUPS,
                 "meetings": dock_mod.MEETINGS, "agents": dock_mod.AGENTS, "live": dock_mod.LIVE,
                 "memory": dock_mod.MEMORY, "settings": dock_mod.SETTINGS}
_ORB_STATES = {orb_mod.IDLE: "idle", orb_mod.LISTENING: "listening", orb_mod.THINKING: "thinking",
               orb_mod.SPEAKING: "speaking", orb_mod.NEEDS: "needs", orb_mod.WAITING: "waiting"}


def _alive(item: Any) -> bool:
    return item is not None and (not isinstance(item, QObject) or shiboken6.isValid(item))


class CanvasController(QObject):
    """See the module doc."""

    def __init__(self, desk: Any, *, clock: Any = time.monotonic) -> None:
        super().__init__(desk)
        self.desk = desk
        self.host = desk.host
        self.window = desk.host.window
        self.reading = desk.reading
        self._clock = clock
        self.counts: Counter[str] = Counter()
        self._sent: dict[str, dict[str, Any]] = {}      # the last orb and canvas message the page was sent
        self._lingering = False
        self._was_speaking = False
        self._fallen = False
        self._started = False
        self._began = 0.0
        self.ready_ms: int | None = None                # the page's start, from the board's start to its ready
        self._linger = QTimer(self)
        self._linger.setSingleShot(True)
        self._linger.setInterval(BADGE_LINGER_MS)
        self._linger.timeout.connect(self._linger_over)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self.sync)
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(LEVEL_MS)
        self._level_timer.timeout.connect(self._sync_orb)
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.setInterval(START_AFTER_MS)
        self._start_timer.timeout.connect(self._start_board)
        reading = self.reading
        reading.set_canvas(True)
        self._sync_placeholder()
        reading.glanceChanged.connect(self.sync)
        reading.deskShown.connect(self._on_desk_shown)
        reading.tabChanged.connect(self._on_tab)
        self.window.visibilityChanged.connect(self._on_visibility)
        self.window.activeChanged.connect(lambda _active: self.sync())     # away or back: the orb's rate at rest
        self._sync_timer.start()
        self._on_visibility()

    # ---- the page's start: with the window ---------------------------------------------------------------------

    def _window_up(self) -> bool:
        window = self.window
        return bool(_alive(window) and window.isVisible() and not window.isMinimized() and window.is_reading_view())

    def _on_visibility(self) -> None:
        if not self._started and not self._fallen and self._window_up():
            self._start_timer.start()
        self._sync_timers()
        if self._window_up():
            self.focus_ask()

    def _start_board(self) -> None:
        if self._started or self._fallen:
            return
        self._started = True
        self._began = self._clock()
        if self.desk.ensure_board() is None:
            self.fall_back("no_board")

    def on_board(self, view: Any) -> None:
        """The desk made the board's one view: it is the canvas's page (its black ground), and what it asks and says
        comes here."""
        if hasattr(view, "set_canvas"):
            view.set_canvas(True)
        view.ready.connect(self._on_ready)
        view.unavailable.connect(self._on_unavailable)
        view.goRequested.connect(self.on_go)
        if not self._started:
            self._started = True                 # an Ask got there first: the board is starting already
            self._began = self._clock()

    def _on_ready(self) -> None:
        """The page is up (also again, after its renderer stopped): it holds nothing - the corners and the orb as
        they are now, whatever was sent before."""
        if self.ready_ms is None:
            self.ready_ms = int((self._clock() - self._began) * 1000)
            logger.info("Canvas: page ready %d ms after its start", self.ready_ms)
        self._sent.clear()
        self.sync()
        self.focus_ask()

    def _on_unavailable(self, reason: str) -> None:
        self.fall_back(str(reason or "unavailable"))

    def fall_back(self, reason: str) -> None:
        """The page can't be (or stopped for good): Home's native ring, the Dock, the ask line, the read-only tiles."""
        if self._fallen:
            return
        self._fallen = True
        self.counts[f"fallback:{reason}"] += 1
        self._sync_timer.stop()
        self._level_timer.stop()
        self._linger.stop()
        self._start_timer.stop()
        logger.info("Canvas: Home goes back to its native ring (%s)", reason)
        if _alive(self.reading):
            self.reading.set_canvas(False)
            self.reading.command_bar.set_canvas_placeholder("")

    def fallen(self) -> bool:
        return self._fallen

    # ---- what the page is told ---------------------------------------------------------------------------------

    def _board(self) -> Any:
        board = self.desk.board
        return board if _alive(board) else None

    def _state(self) -> tuple[str, bool, bool]:
        base, muted, offline = self.reading.look()
        return _ORB_STATES.get(base, "idle"), bool(muted), bool(offline)

    def form(self) -> str:
        """The orb's form now (canvas_view.orb_form)."""
        state, _muted, _offline = self._state()
        return canvas_view.orb_form(state, self.reading.desk_shown(), self._lingering)

    def _voice(self, state: str) -> float | None:
        """His voice's level while he speaks: the app's own (voice_fx, as the native ring reads it), else a slow
        even pulse (an utterance with no timing: the Windows voice)."""
        if state != "speaking":
            return 0.0
        level = self.reading.voice_level()
        if level is not None:
            return level
        return 0.45 + 0.3 * math.sin(self._clock() * 5.2)

    def orb_message(self) -> dict[str, Any]:
        reading = self.reading
        state, muted, offline = self._state()
        mic, voice = canvas_view.orb_levels(reading.mic_level() if state == "listening" else 0.0, self._voice(state))
        return {"type": "orb", "state": state, "form": self.form(), "muted": muted, "offline": offline,
                "dim": bool(reading.quiet_until()) and state == "idle" and not muted, "away": self._away(),
                "level": mic, "voice": voice}

    def _away(self) -> bool:
        """Jarvis's window is not the one the owner works in: the orb at rest is drawn 10 times a second, not 30."""
        window = self.window
        try:
            return not bool(window.isActiveWindow())
        except Exception:  # noqa: BLE001 - unknown: here
            return False

    def buttons(self) -> list[str]:
        """The page's buttons that can work now, in the protocol's order."""
        reading = self.reading
        slots = set(reading.dock.visible_slots())
        shown = {"history", "briefing", "approvals", "settings", "minimize", "maximize", "close", "day"}
        shown.update(word for word, slot in _SCREEN_SLOTS.items() if slot in slots)
        if reading.desktop_available():
            shown.add("desktop")
        if not reading.header.mute_button.isHidden():
            shown.add("mute")
        if reading.board_folded() and reading.board_count() > 0:
            shown.add("board")
        return [word for word in protocol.CANVAS_BUTTONS if word in shown]

    def canvas_message(self) -> dict[str, Any]:
        reading = self.reading
        time_text, date_text = reading.header.clock_texts()
        elapsed, warning = reading.recording_state()
        rec = ""
        if elapsed:
            rec = f"Rec {elapsed}" + (f" · {warning}" if warning else "")
        wake_words, wake_look = reading.wake_state()
        from .wake_ui import LOOK_OFF

        return {"type": "canvas", "clock": f"{date_text} · {time_text}"[:protocol.CLOCK_CAP],
                "word": str(reading.state_word() or "")[:protocol.STATE_WORD_CAP],
                "tone": reading.header.state_tone(),
                "approvals": max(0, min(protocol.APPROVALS_MAX, reading.needs_ok_count())),
                "buttons": self.buttons(), "briefing_new": bool(reading.is_new_briefing()),
                "hold": str(reading.hold_word() or "")[:protocol.HOLD_CAP], "rec": rec[:protocol.REC_CAP],
                "wake": str(wake_words or "")[:protocol.WAKE_CAP] if wake_look != LOOK_OFF else "",
                "meter": self._has_meter()}

    def _has_meter(self) -> bool:
        """The app measures the microphone's input (the talk key's listening or "Hey Jarvis"): else no meter."""
        return getattr(self.host, "talk", None) is not None or getattr(self.host, "wake", None) is not None

    def _send(self, message: dict[str, Any]) -> bool:
        board = self._board()
        kind = message["type"]
        if board is None or self._fallen or self._sent.get(kind) == message:
            return False
        if not board.apply(message):
            self.counts[f"refused:{kind}"] += 1
            return False
        self._sent[kind] = message
        self.counts[f"sent:{kind}"] += 1
        return True

    def sync(self) -> None:
        """The corners and the orb as they are now (sent only when they differ from what the page has)."""
        if self._fallen or not _alive(self.reading):
            return
        if not self._started and self._window_up():
            self._start_timer.start()            # the reading view came up with no window event (from the prompt)
        try:
            self._track_speaking()
            self._send(self.canvas_message())
            self._send(self.orb_message())
        except Exception as exc:  # noqa: BLE001 - the canvas never stops the app
            logger.debug("Canvas: not synced (%s)", type(exc).__name__)
        self._sync_timers()

    def _sync_orb(self) -> None:
        if self._fallen or not _alive(self.reading):
            return
        try:
            self._send(self.orb_message())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Canvas: the levels were not sent (%s)", type(exc).__name__)

    def _track_speaking(self) -> None:
        """The badge stays BADGE_LINGER_MS after he stops speaking (a new request, or a frame, ends it at once)."""
        state, _muted, _offline = self._state()
        speaking = state == "speaking"
        if speaking:
            self._linger.stop()
            self._lingering = False
        elif self._was_speaking and state in ("idle", "needs", "waiting"):
            self._lingering = True
            self._linger.start()
        elif state in ("listening", "thinking"):
            self._linger.stop()
            self._lingering = False
        self._was_speaking = speaking

    def _linger_over(self) -> None:
        self._lingering = False
        self.sync()

    def _sync_timers(self) -> None:
        """The levels run only while the owner can see the canvas and he listens or speaks."""
        if self._fallen or not _alive(self.reading):
            return
        state, _muted, _offline = self._state()
        seen = self._window_up() and self.reading.canvas_shown()
        wanted = seen and state in ("listening", "speaking") and self._board() is not None
        if wanted != self._level_timer.isActive():
            if wanted:
                self._level_timer.start()
            else:
                self._level_timer.stop()

    def _sync_placeholder(self) -> None:
        talk = getattr(self.host, "talk", None) is not None
        self.reading.command_bar.set_canvas_placeholder(PLACEHOLDER_TALK if talk else PLACEHOLDER_TYPE)

    # ---- the desk's changes, the screens ---------------------------------------------------------------------

    def _on_desk_shown(self, _shown: bool) -> None:
        self._linger.stop()
        self._lingering = False                  # frames came (or left): the corner (or the centre) at once
        self.sync()

    def _on_tab(self, _index: int) -> None:
        self.sync()
        if self.reading.canvas_shown():
            self.focus_ask()

    # ---- the keyboard: in the ask line -----------------------------------------------------------------------

    def focus_ask(self) -> bool:
        """The keyboard to the ask line - when the window opens, when Home comes back, after an answer arrives -
        while the canvas is what the owner sees, the window is the active one and his keyboard is not in the board
        (he reads it) nor in another field. False when it stayed where it was."""
        reading = self.reading
        if self._fallen or not _alive(reading) or not reading.canvas_shown() or not reading.ask_available():
            return False
        board = self._board()
        focus = QApplication.focusWidget()
        if focus is not None and board is not None and (focus is board or board.isAncestorOf(focus)):
            return False
        if reading.transcript_drawer.is_open() or reading.day_drawer.is_open():
            return False
        find = getattr(reading, "find_overlay", None)
        if find is not None and find.isVisible():
            return False
        reading.command_bar.focus_input()
        self.counts["focus:ask"] += 1
        return True

    def on_answer(self) -> None:
        """An answer arrived (the look controller's hook): the keyboard is in the ask line for the next request."""
        QTimer.singleShot(0, self, self.focus_ask)

    # ---- the page's buttons ----------------------------------------------------------------------------------

    def on_go(self, word: str) -> bool:
        """A corner button of the page (a word of board.protocol.GO_WORDS, checked by the bridge): exactly what its
        native counterpart does. False (counted) for a word whose control can't work now."""
        reading, window = self.reading, self.window
        if self._fallen or not _alive(reading) or not _alive(window) or word not in protocol.GO_WORDS:
            self.counts["go:refused"] += 1
            return False
        allowed = set(self.buttons()) | {"drag"}
        if reading.hold_word():
            allowed.add("undo")
        if reading.recording_state()[0]:
            allowed.add("stop_rec")
        if word not in allowed:
            self.counts["go:unavailable"] += 1
            return False
        self.counts[f"go:{word}"] += 1
        if word in _SCREEN_SLOTS:
            reading.dock.activate(_SCREEN_SLOTS[word])
        elif word == "history":
            reading.open_transcript()
        elif word == "day":
            reading.open_day_view()
        elif word == "board":
            reading.toggle_board()
        elif word == "desktop":
            reading.dock.activate(dock_mod.DESKTOP)
        elif word == "mute":
            window._on_holo_mute()  # noqa: SLF001 - the header's mute button's own path
        elif word == "undo":
            reading.holdUndo.emit()
        elif word == "stop_rec":
            reading.stopRecording.emit()
        elif word == "minimize":
            window.showMinimized()
        elif word == "maximize":
            window.toggle_maximized()
        elif word == "close":
            window.close()
        elif word == "drag":
            window.start_drag()
        self.sync()
        return True

    # ---- teardown ----------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        for timer in (self._sync_timer, self._level_timer, self._linger, self._start_timer):
            timer.stop()


__all__ = ["BADGE_LINGER_MS", "CanvasController", "LEVEL_MS", "PLACEHOLDER_TALK", "PLACEHOLDER_TYPE", "START_AFTER_MS",
           "SYNC_MS"]
