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

Part 2 (frames across the screen):

* the task card (``task_message``; board.protocol's "task"): while an Ask shown on Home runs, what Jarvis is doing in
  the app's own words - the request's stage as the ask line words it (ask_ui.STAGE_TEXTS; a planner mode's own:
  MODE_STATUS), never the owner's words -, the seconds it has run, the steps finished (the stages before the one
  running), the sources read so far (the answer's LIVE pictures) and whether Stop works now. When the answer puts
  frames up (``on_answer``) the card is told "done" with their group: the page shrinks it to that group's head tile;
  an answer with nothing for the canvas, a Stop or a failure: "gone". Sent only when it differs (the page counts the
  seconds itself);
* two more of the page's words (``on_go``): "stop" - the card's Stop, exactly the ask line's own Cancel (only while a
  request runs) - and "tidy" (the page packed its frames again: counted);
* the orb goes back to the centre once Clear view left nothing on the canvas, also while its Bring back is still
  offered (``form``: the corner only while frames show).

The window brief:

* HEY JARVIS on the canvas (``hearing``; the canvas message's "hear", "deaf" and "mic"): "Hey Jarvis" listening,
  paused or can't hear you (wake_ui.WakeController.hearing: its listener gets nothing from its microphone), and the
  note "Jarvis can't hear you" - also when the last talk got nothing at all from the microphone (talk_ui
  .TalkController.unheard) - with the microphone's name (on screen only, never logged) and its one button
  "mic_settings": Settings at Talk with its Microphone row in view and the keyboard on it (``open_mic_settings``;
  part 5B: Settings > Talk is there whenever talking or "Hey Jarvis" is; nothing is picked for the owner and the
  Windows default is never changed);
* the page's top band for Windows' hit-test (its "chrome" request) goes to the window (ui.BriefingWindow
  .set_page_chrome), and Windows' own maximise button's hover (the window's maxHoverChanged) to the page.

Canvas part 5B:

* the offer's or the error's tile (canvas_view.CanvasPage's side tile) is told to the page as the canvas message's
  "side" (its box): the page keeps every frame clear of it, and it hides while a frame is open big;
* the corner's clock is the canvas's own: read on the minute (``_minute``), while the window is up;
* the ask line names the real talk key: "Type, or tap <key> and talk" ([talk] key), "Type to ask" without talking;
* Esc in the empty ask line leaves full screen (the window's own F11 state);
* every word sent is cut by the page's units (board.frames.cut: never inside a surrogate pair);
* the 500 ms sync and the Now group's timer rest while the window is not on the screen (minimised, hidden), and start
  again with a sync at once when it is back.

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
from .canvas_now import NowController
from . import dock as dock_mod
from . import orb as orb_mod
from .board import frames as board_frames
from .board import protocol

logger = logging.getLogger(__name__)

START_AFTER_MS = 60               # the board starts this long after the window is up (it paints first)
SYNC_MS = 500                     # the corners and the orb are compared with what the page has this often ...
LEVEL_MS = 50                     # ... and the levels this often while he listens or speaks
BADGE_LINGER_MS = 2600            # the badge stays this long after he stops speaking
PLACEHOLDER_TALK = "Type, or tap {key} and talk"   # part 5B: the real talk key ([talk] key), tapped not held
PLACEHOLDER_TALK_ANY = "Type, or tap the talk key and talk"   # (no key set in [talk] key)
PLACEHOLDER_TYPE = "Type to ask"
TASK_TITLE = "Working on it"      # the task card's heading while he works (the answer's topic takes its place)
TASK_STARTING = "Starting"
TASK_STOPPING = "Stopping"
TASK_GRACE_S = 1.5                # a request that ended without an answer's hook: its card goes this long after
MIC_GROUP = "talk"                # Settings > Talk (talk_ui.SETTINGS_KEY): where a microphone is picked (part 5B)

# A page button's word -> the Dock's slot it presses.
_SCREEN_SLOTS = {"briefing": dock_mod.BRIEFING, "approvals": dock_mod.APPROVALS, "followups": dock_mod.FOLLOWUPS,
                 "meetings": dock_mod.MEETINGS, "agents": dock_mod.AGENTS, "documents": dock_mod.DOCS,
                 "live": dock_mod.LIVE, "memory": dock_mod.MEMORY, "settings": dock_mod.SETTINGS}
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
        self._task: dict[str, Any] | None = None        # the task card's request: its key, start, stages (part 2)
        self._task_sent: tuple[Any, ...] | None = None  # the card as the page has it (its seconds apart)
        self._ask_hooked: Any = None                    # the Ask controller whose Stop and end the card follows
        self.now = NowController(self)                  # part 3B: the resting group "Now"
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
        self._minute = QTimer(self)                     # part 5B: the corner's clock, on the minute (window up)
        self._minute.setSingleShot(True)
        self._minute.timeout.connect(self._on_minute)
        reading = self.reading
        reading.set_canvas(True)
        self._sync_placeholder()
        reading.glanceChanged.connect(self.sync)
        reading.deskShown.connect(self._on_desk_shown)
        reading.tabChanged.connect(self._on_tab)
        page = reading.canvas_page() if hasattr(reading, "canvas_page") else None
        if page is not None and hasattr(page, "sideChanged"):
            page.sideChanged.connect(self.sync)       # part 5B: the side tile came, went or changed size
        bar = reading.command_bar
        if hasattr(bar, "statusDismissed"):
            bar.statusDismissed.connect(self._on_empty_escape)   # part 5B: Esc in the empty ask line
        self.window.visibilityChanged.connect(self._on_visibility)
        self.window.activeChanged.connect(lambda _active: self.sync())     # away or back: the orb's rate at rest
        hover = getattr(self.window, "maxHoverChanged", None)
        if hover is not None:
            hover.connect(self._on_max_hover)            # the window brief: Windows' maximise button hovered
        self._on_visibility()

    # ---- the page's start: with the window ---------------------------------------------------------------------

    def _window_up(self) -> bool:
        window = self.window
        return bool(_alive(window) and window.isVisible() and not window.isMinimized() and window.is_reading_view())

    def _window_shown(self) -> bool:
        """The window is on the screen (shown, not minimised) - whatever view it shows."""
        window = self.window
        return bool(_alive(window) and window.isVisible() and not window.isMinimized())

    def _on_visibility(self) -> None:
        up = self._window_up()
        if not self._started and not self._fallen and up:
            self._start_timer.start()
        self._rest(not self._window_shown())
        self._sync_timers()
        if up:
            self.focus_ask()

    def _rest(self, resting: bool) -> None:
        """The resilience review's L3: while the window is not on the screen (minimised, hidden: the desktop HUD) the
        500 ms sync, the clock's minute and the Now group's timer rest; when it is back they start with a sync at
        once. (Another view in the window keeps the sync: the reading view coming up has no window event of its own.)"""
        if self._fallen:
            return
        if resting:
            self._sync_timer.stop()
            self._minute.stop()
            self.now.rest(True)
            return
        was = self._sync_timer.isActive()
        if not was:
            self._sync_timer.start()
        self._arm_minute()
        self.now.rest(False)
        if not was:
            self.sync()

    def _arm_minute(self) -> None:
        """Part 5B (the walk's 2): the corner's clock is read again now and on the next minute - its own timer, never
        the hidden native header's (which stops while it is hidden)."""
        header = self.reading.header
        refresh = getattr(header, "refresh_clock", None)
        now = refresh() if callable(refresh) else None
        if now is None:
            self._minute.start(60_000)
            return
        ms_left = (60 - now.second) * 1000 - now.microsecond // 1000
        self._minute.start(max(250, ms_left + 50))

    def _on_minute(self) -> None:
        if self._fallen or not self._window_shown():
            return
        self._arm_minute()
        self.sync()

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
        chrome = getattr(view, "chromeReported", None)
        if chrome is not None:
            chrome.connect(self._on_chrome)              # the window brief: the top band for Windows' hit-test
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
        self._task_sent = None                   # (a card still working is drawn again)
        self.now.start()                         # part 3B: the resting group, the first time the page is up
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
        self._minute.stop()
        self.now.shutdown()
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
        return canvas_view.orb_form(state, self._frames_shown(), self._lingering)

    def _frames_shown(self) -> bool:
        """The canvas shows frames now (part 2): the board is up and holds some - not while only a Clear's Bring back
        is offered (the orb is back at the centre then)."""
        reading = self.reading
        try:
            return bool(reading.desk_shown()) and int(reading.board_count()) > 0
        except Exception:  # noqa: BLE001 - unknown: as the board says
            return bool(reading.desk_shown())

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

    def buttons(self, heard: tuple[str, bool, str] | None = None) -> list[str]:
        """The page's buttons that can work now, in the protocol's order (``heard``: hearing() as just read)."""
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
        if self.now.can_show():
            shown.add("now")                     # part 3B: the resting group back, only while it is not up
        if (heard if heard is not None else self.hearing())[1] and self._mic_group():
            shown.add("mic_settings")            # the window brief: the can't-hear note's Settings > Talk (part 5B)
        return [word for word in protocol.CANVAS_BUTTONS if word in shown]

    def _mic_group(self) -> bool:
        """Settings > Talk is there (its Microphone row): the can't-hear note's button can work."""
        try:
            return MIC_GROUP in self.reading.settings_page.group_buttons
        except Exception:  # noqa: BLE001 - unknown: no button
            return False

    def hearing(self) -> tuple[str, bool, str]:
        """The window brief: (the HEY JARVIS state - board.protocol.HEAR_STATES -, Jarvis can't hear the owner, the
        microphone's name for the note). Can't hear: "Hey Jarvis"'s listener gets nothing from its microphone (one that
        gives only silence, or a virtual one), or the last talk ended with nothing at all from it (its level at zero)
        and no microphone was picked since. The name is shown on screen only: it is never logged."""
        state, mic = "", ""
        wake = getattr(self.host, "wake", None)
        if wake is not None:
            try:
                state, mic = wake.hearing()
            except Exception:  # noqa: BLE001 - unknown: as off
                state, mic = "", ""
        deaf = state == "cant_hear"
        talk = getattr(self.host, "talk", None)
        if not deaf and talk is not None:
            try:
                unheard = talk.unheard()
            except Exception:  # noqa: BLE001 - unknown: heard
                unheard = None
            if unheard is not None:
                deaf, mic = True, str(unheard)
        state = state if state in protocol.HEAR_STATES else "other"
        return state, deaf, (board_frames.cut(str(mic or ""), protocol.MIC_CAP) if deaf else "")

    def canvas_message(self) -> dict[str, Any]:
        reading = self.reading
        time_text, date_text = reading.header.clock_texts()
        elapsed, warning = reading.recording_state()
        rec = ""
        if elapsed:
            rec = f"Rec {elapsed}" + (f" · {warning}" if warning else "")
        _sentence, wake_look = reading.wake_state()
        wake_words = reading.wake_words()                 # part 3: its few words, whole (never a cut sentence)
        heard = self.hearing()                             # the window brief: HEY JARVIS's state, can't hear you
        hear, deaf, mic = heard                            # (read once a sync: the resilience review's L3)
        from .wake_ui import LOOK_OFF

        cut = board_frames.cut                             # part 5B: cut by the page's units, never inside a pair
        return {"type": "canvas", "clock": cut(f"{date_text} · {time_text}", protocol.CLOCK_CAP),
                "word": cut(str(reading.state_word() or ""), protocol.STATE_WORD_CAP),
                "tone": reading.header.state_tone(),
                "approvals": max(0, min(protocol.APPROVALS_MAX, reading.needs_ok_count())),
                "buttons": self.buttons(heard), "briefing_new": bool(reading.is_new_briefing()),
                "hold": cut(str(reading.hold_word() or ""), protocol.HOLD_CAP), "rec": cut(rec, protocol.REC_CAP),
                "wake": cut(str(wake_words or ""), protocol.WAKE_CAP) if wake_look != LOOK_OFF else "",
                "meter": self._has_meter(), "hear": hear, "deaf": deaf, "mic": mic, "side": self._side()}

    def _side(self) -> list[int]:
        """Part 5B: the side tile's box ([width, height] px; [0, 0] none) - the page keeps its frames clear of it."""
        page = self.reading.canvas_page() if hasattr(self.reading, "canvas_page") else None
        box = page.side_box() if page is not None and hasattr(page, "side_box") else (0, 0)
        return [max(0, min(protocol.SIDE_MAX, int(box[0]))), max(0, min(protocol.SIDE_MAX, int(box[1])))]

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
            self._sync_task()
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

    def placeholder(self) -> str:
        """Part 5B (the walk's 5): the ask line says how to ask - the real talk key, tapped ("Type, or tap Ctrl+Alt+T
        and talk"); "Type to ask" while talking is off. Ctrl+K stays the line's own cue at its right."""
        if getattr(self.host, "talk", None) is None:
            return PLACEHOLDER_TYPE
        key = str(getattr(getattr(getattr(self.host, "config", None), "talk", None), "key", "") or "")
        if not key:
            return PLACEHOLDER_TALK_ANY
        from .hotkey import describe_combo

        return PLACEHOLDER_TALK.format(key=describe_combo(key))

    def _sync_placeholder(self) -> None:
        self.reading.command_bar.set_canvas_placeholder(self.placeholder())

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

    def on_answer(self, task_id: int = 0, *, card: bool = True) -> None:
        """An answer arrived (the look controller's hook, after its frames went up): the task card becomes the head
        of its group of frames - or goes, when it put nothing on the canvas - and the keyboard is in the ask line for
        the next request. ``card`` False: an answer that was no Ask's (Jarvis's own instant one): the card stays."""
        if card:
            self._end_task(task_id)
        QTimer.singleShot(0, self, self.focus_ask)

    # ---- the task card (part 2) --------------------------------------------------------------------------------

    def _ask(self) -> Any:
        ask = getattr(self.host, "ask", None)
        return ask if _alive(ask) else None

    def _hook_ask(self) -> None:
        """The running request's Stop and its end without an answer reach the card at once (hooked once)."""
        ask = self._ask()
        if ask is None or ask is self._ask_hooked:
            return
        self._ask_hooked = ask
        try:
            ask.askStopping.connect(self._on_ask_stopping)
            ask.askCancelled.connect(self._on_ask_cancelled)
        except Exception as exc:  # noqa: BLE001 - no such signals: the card follows the 500 ms sync alone
            logger.debug("Canvas: the Ask's end is not followed (%s)", type(exc).__name__)

    def on_answer_started(self, task_id: int = 0) -> None:
        """An Ask shown on Home started (the look controller's hook): its task card."""
        if self._fallen:
            return
        self._hook_ask()
        self._task = {"key": int(task_id or 0), "began": self._clock(), "stages": [], "stopping": False, "idle": None}
        self._task_sent = None
        self.counts["task:started"] += 1
        self._sync_task()

    def _on_ask_stopping(self, _seq: int = 0) -> None:
        if self._task is not None:
            self._task["stopping"] = True
            self._sync_task()

    def _on_ask_cancelled(self, _seq: int = 0) -> None:
        self._gone_task("cancelled")

    def _task_words(self, ask: Any, task: dict[str, Any]) -> tuple[str, str]:
        """The card's heading and what Jarvis is doing, in the app's own words (the ask line's own: never the
        owner's)."""
        from . import ask_ui

        mode = str(getattr(ask, "running_mode", "") or "") if ask is not None else ""
        stage = str(ask.stage() or "") if ask is not None else ""
        if stage and stage not in task["stages"]:
            task["stages"].append(stage)
        if task["stopping"]:
            words = TASK_STOPPING
        else:
            words = ask_ui.STAGE_TEXTS.get(stage, TASK_STARTING).rstrip(". ")
        title = ask_ui.MODE_STATUS.get(mode, TASK_TITLE).rstrip(". ")
        return board_frames.cut(title, protocol.TOPIC_CAP), board_frames.cut(words, protocol.TASK_WORDS_CAP)

    def task_message(self) -> dict[str, Any] | None:
        """The card while the request runs (None: no card)."""
        task = self._task
        if task is None:
            return None
        ask = self._ask()
        title, words = self._task_words(ask, task)
        bar = self.reading.command_bar
        try:
            sources = int(self.desk.answer_sources(task["key"]))
        except Exception:  # noqa: BLE001 - unknown: none counted
            sources = 0
        return {"type": "task", "state": "working", "title": title, "words": words,
                "seconds": max(0, min(protocol.TASK_SECONDS_MAX, int(self._clock() - task["began"]))),
                "steps": max(0, min(protocol.TASK_COUNT_MAX, len(task["stages"]) - 1)),
                "sources": max(0, min(protocol.TASK_COUNT_MAX, sources)), "frames": 0, "group": "",
                "stop": bool(bar.is_running()) and not task["stopping"]}

    def _sync_task(self) -> None:
        """The card as it is now, when it differs from what the page has (its seconds apart: the page counts them).
        A request that is no longer running and whose answer's hook never came: the card goes after TASK_GRACE_S."""
        task = self._task
        if task is None or self._fallen:
            return
        ask = self._ask()
        if ask is None or not bool(getattr(ask, "busy", False)):
            now = self._clock()
            if task["idle"] is None:
                task["idle"] = now
            elif now - task["idle"] >= TASK_GRACE_S:
                self._gone_task("ended")
            return
        task["idle"] = None
        message = self.task_message()
        if message is None:
            return
        mark = tuple(value for key, value in message.items() if key != "seconds")
        if mark == self._task_sent:
            return
        board = self._board()
        if board is not None and board.apply(message):
            self._task_sent = mark
            self.counts["sent:task"] += 1

    def _end_task(self, task_id: int = 0) -> None:
        """The answer arrived: "done" with the group its frames went to (the card shrinks to that group's head tile),
        or "gone" when it put nothing on the canvas."""
        task = self._task
        if task is None:
            return
        try:
            group = str(self.desk.answer_group(task_id or task["key"]) or "")
            frames = len(self.desk.feed.group_order(group)) if group else 0
            sources = int(self.desk.answer_sources(task_id or task["key"]))
        except Exception:  # noqa: BLE001 - unknown: the card goes
            group, frames, sources = "", 0, 0
        if not group:
            self._gone_task("no_frames")
            return
        self._task = None
        self._task_sent = None
        board = self._board()
        message = {"type": "task", "state": "done", "title": "", "words": "",
                   "seconds": max(0, min(protocol.TASK_SECONDS_MAX, int(self._clock() - task["began"]))),
                   "steps": max(0, min(protocol.TASK_COUNT_MAX, len(task["stages"]))),
                   "sources": max(0, min(protocol.TASK_COUNT_MAX, sources)),
                   "frames": max(0, min(protocol.MAX_FRAMES, frames)), "group": group, "stop": False}
        if board is not None and board.apply(message):
            self.counts["task:done"] += 1

    def _gone_task(self, reason: str) -> None:
        if self._task is None:
            return
        self._task = None
        self._task_sent = None
        board = self._board()
        if board is not None and board.apply({"type": "task", "state": "gone"}):
            self.counts[f"task:gone:{reason}"] += 1

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
        if reading.command_bar.is_running():
            allowed.add("stop")                  # part 2: the task card's Stop, while a request runs
        if reading.desk_shown():
            allowed.add("tidy")                  # ... and Tidy, while the canvas shows frames
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
        elif word == "stop":
            reading.command_bar.cancelRequested.emit()     # exactly the ask line's own Cancel
        elif word == "now":
            self.now.show()                                # part 3B: the resting group back
        elif word == "mic_settings":
            self.open_mic_settings()                       # the window brief: Settings > Meetings
        # ("tidy": the page packed its frames again by itself - counted above, nothing more to do here)
        self.sync()
        return True

    def open_mic_settings(self) -> None:
        """The can't-hear note's button: Settings (the Dock's own button) at Talk, with its Microphone row scrolled into
        view and the keyboard on its Change button (part 5B). Nothing is picked for the owner and the Windows default
        is never changed."""
        reading = self.reading
        reading.dock.activate(_SCREEN_SLOTS["settings"])
        settings = reading.settings_page
        settings.select_group(MIC_GROUP)
        row = getattr(getattr(self.host, "talk", None), "mic_row", None)
        if row is not None and hasattr(settings, "reveal"):
            settings.reveal(row, getattr(row, "change_button", None))

    # ---- the window brief: Windows' hit-test of the page's top band -------------------------------------------

    def _on_chrome(self, request: Any) -> None:
        """The page said where its top band's controls are (board.protocol "chrome"): the window's hit-test uses it."""
        window = self.window
        if _alive(window) and hasattr(window, "set_page_chrome"):
            window.set_page_chrome(request)
            self.counts["chrome:report"] += 1

    def _on_max_hover(self, hover: bool) -> None:
        """Windows' own maximise button is under the pointer (or not): the page lights its button as hovered."""
        board = self._board()
        if board is not None and not self._fallen and board.apply({"type": "chrome", "hover": bool(hover)}):
            self.counts["sent:chrome"] += 1

    def _on_empty_escape(self) -> None:
        """Part 5B (the walk's 11): Esc in the empty ask line on the canvas leaves full screen (F11 goes back too)."""
        window = self.window
        if self._fallen or not self.reading.canvas_shown() or not _alive(window):
            return
        leave = getattr(window, "leave_fullscreen", None)
        if callable(leave) and leave():
            self.counts["fullscreen:esc"] += 1

    # ---- teardown ----------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        for timer in (self._sync_timer, self._level_timer, self._linger, self._start_timer, self._minute):
            timer.stop()
        self.now.shutdown()


__all__ = ["BADGE_LINGER_MS", "CanvasController", "LEVEL_MS", "PLACEHOLDER_TALK", "PLACEHOLDER_TALK_ANY",
           "PLACEHOLDER_TYPE", "START_AFTER_MS", "SYNC_MS"]
