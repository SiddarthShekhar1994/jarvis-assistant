"""The new look's controller (build-plan item 2): the soft sounds (part 1), Settings > Look, (part 5a) the board on
Home (``desk``: desk_ui.DeskController while ``[look] board`` is on) and (part 7) the second-screen display
(``second``: second_screen.SecondScreenController, made with the main window; it shows nothing until the owner turns
it on).

The app (``ui.AppController``) makes one ``LookController`` and calls it through ``_look_call(name, *args)``, which
never lets it stop the app. Its hooks:

- ``on_talk_state(state)``: the talk key's or "Hey Jarvis"'s listening (HEARD as it opens);
- ``on_wake_chime()``: "Hey Jarvis"'s own chime played (HEARD is skipped for a second);
- ``on_ask_started()`` / ``on_ask_answer()``: THINKING when nothing was said 400 ms after a request went in (and the
  board starts with the first Ask);
- ``on_answer_started(task_id, text)`` / ``on_ask_outcome(outcome, task_id)``: an answer's group on the board;
- ``on_instant_answer(reply, words)``: part 6, an instant sum's working as a maths frame on the board (when it has one);
- ``on_pending(count)``: WAITS when the number of cards waiting for the owner goes up (never at the first count);
- ``on_action_result(status, failed)``: DONE when an approved card's action finished, FAILED when it failed;
- ``on_speaking()``: Jarvis's voice or a briefing started or stopped (part 5c: his utterance goes on to the desk, whose
  sentence being said lights the frame it is about); ``on_muted(muted)``: his voice was muted (nothing lit).
- part 7: ``on_board_changed(feed)`` (the desk's every send: the board's frames to the second screen),
  ``on_events(events)`` (the calendar's events as the app read them: its NEXT), ``shutdown()`` (Jarvis closes: the
  second screen's window goes).

Part 5c, the board's sounds (the desk calls them; SoundBoard's gate and this controller's quiet rules decide, so they
are never heard while Jarvis speaks - dropped, never queued -, muted, in quiet hours, focus mode, while a meeting
records, locked, while Windows says busy or full screen, with sounds off, or under the test guard):

- ``on_frame_landed(place)``: FRAME, the variant by the frame's place in its group (frame0-frame3);
- ``on_group_settled()``: SETTLED, once, after a group's last frame;
- ``on_frame_changed()``: CHANGED, as the changed light starts;
- ``on_big(opened)``: OPEN_BIG / CLOSE_BIG.

Settings > Look shows the switches as they are and has the two things it changes on this PC: the sounds on or off,
and (part 7) "Show Jarvis on my second screen" - a switch, greyed with "Needs a second monitor" while there is one
monitor, following monitors plugged in or out while Settings is open (assistant.json's "look" table). [look] in
config.toml sets the defaults; ``sounds = "off"`` there wins; the second screen's switch, once used, wins over
``[look] second_screen``.

The canvas (PLAN-canvas part 1): with ``[look] board`` on and a board that can be made, Home is the canvas
(``canvas``: canvas_ui.CanvasController, made with the desk) - always with the real board; a test's factory asks for
it with ``factory.canvas = True`` (without it the factory's board stands beside the conversation, as the desk's own
tests have it). ``on_ask_outcome`` and ``on_instant_answer`` then also put the keyboard back in the ask line.

Logs hold event names and reasons only.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import holo, sounds
from .config import SOUNDS_OFF
from .memory_view import body_label

logger = logging.getLogger(__name__)

THINKING_AFTER_MS = 400           # a request that has said nothing by then gets the thinking sound

SETTINGS_KEY = "look"
SETTINGS_TITLE = "Look"
SETTINGS_NOTE = "How Jarvis looks and sounds."
SETTINGS_HOW = ("The sounds and the second screen can be turned off or on here, on this PC. [look] in config.toml "
                "sets the defaults; restart Jarvis after editing it.")
SOUNDS_LABEL = "Sounds"
SOUNDS_SOFT_VALUE = "Soft"
SOUNDS_OFF_VALUE = "Off"
SOUNDS_OFF_CONFIG_VALUE = "Off in config.toml"
SOUNDS_ON_TEXT = ("Soft sounds mark a few moments. They stay silent while Jarvis is muted, in quiet hours, in "
                  "focus mode, while a meeting records and while the PC is locked.")
SOUNDS_OFF_TEXT = "The soft sounds are off on this PC."
SOUNDS_CONFIG_TEXT = "The soft sounds are off in config.toml ([look] sounds)."
TURN_OFF_TEXT = "Turn sounds off"
TURN_ON_TEXT = "Turn sounds on"
SAVE_FAILED_TEXT = "That couldn't be saved; it lasts until Jarvis closes."
BOARD_LABEL = "Board beside the conversation"
CANVAS_LABEL = "Home as the canvas"                    # the same switch while Home is the canvas (PLAN-canvas part 1)
SECOND_LABEL = "Show Jarvis on my second screen"       # part 7 (DESIGN-SPEC 6 and 9)
SECOND_NEEDS = "Needs a second monitor"


class SwitchButton(QAbstractButton):
    """A switch with its words (Settings > Look's "Show Jarvis on my second screen"): the words at the left, a 36 x 20
    track at the right whose knob sits at its right end while on (cyan) and its left end while off; greyed and not
    clickable while disabled. Keyboard: Tab reaches it, Space toggles it; its focus ring shows for the keyboard only."""

    TRACK_W, TRACK_H, KNOB = 36, 20, 14

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setAccessibleName(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._font = holo.font("body")
        self._focus_visible = False

    def track_rect(self) -> QRectF:
        return QRectF(self.width() - self.TRACK_W - 2, (self.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        words = QFontMetricsF(self._font).horizontalAdvance(self.text())
        return QSize(math.ceil(words + 24 + self.TRACK_W + 4), 34)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def focusInEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        self._focus_visible = holo.keyboard_focus(event.reason(), self._focus_visible)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            enabled, on = self.isEnabled(), self.isChecked()
            hover = enabled and self.underMouse()
            if not enabled:
                painter.setOpacity(0.45)
            painter.setFont(self._font)
            painter.setPen(QColor(holo.INK_1 if enabled else holo.INK_3))
            track = self.track_rect()
            painter.drawText(QRectF(0, 0, track.left() - 16, self.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
            radius = track.height() / 2
            if on:
                painter.setPen(QPen(QColor(holo.HOLO_HI if hover else holo.HOLO), 1.0))
                painter.setBrush(holo.alpha(holo.HOLO, 0.28 if hover else 0.22))
            else:
                painter.setPen(QPen(holo.LINE_FOCUS if hover else holo.LINE_STRONG, 1.0))
                painter.setBrush(QColor(holo.FIELD))
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            knob = self.KNOB
            x = track.right() - 3 - knob if on else track.left() + 3
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(holo.HOLO_HI if on else holo.INK_3))
            painter.drawEllipse(QPointF(x + knob / 2, track.center().y()), knob / 2, knob / 2)
            if self.hasFocus() and self._focus_visible:
                painter.setOpacity(1.0)
                holo.paint_focus_ring(painter, track, radius)
        finally:
            painter.end()


class LookSettingsPanel(QWidget):
    """Settings > Look's own part: the sounds line and its one button; (part 7) the second screen's switch."""

    soundsToggled = Signal(bool)           # True: turn the sounds on; False: off
    secondScreenToggled = Signal(bool)     # True: show Jarvis on the second screen; False: not

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line: QLabel = body_label(SOUNDS_ON_TEXT, "body_sm", holo.INK_2, name="lookSoundsLine")
        self.problem: QLabel = body_label("", "body_sm", holo.ALERT_TX, name="lookSoundsProblem")
        self.problem.hide()
        self.button = holo.HoloButton(TURN_OFF_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.button.clicked.connect(self._clicked)
        self._on = True
        self.second = SwitchButton(SECOND_LABEL)
        self.second.setObjectName("lookSecondScreen")
        self.second.clicked.connect(self._second_clicked)
        self.second_note: QLabel = body_label(SECOND_NEEDS, "body_sm", holo.INK_4, name="lookSecondNote")
        self.second_note.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self.line, 1)
        row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(row)
        column.addWidget(self.problem)
        column.addSpacing(10)
        column.addWidget(self.second)
        column.addWidget(self.second_note)

    def set_state(self, on: bool, *, config_off: bool = False, problem: str = "") -> None:
        """The sounds as they are now; ``config_off``: config.toml turned them off (no button then)."""
        self._on = bool(on)
        if config_off:
            self.line.setText(SOUNDS_CONFIG_TEXT)
        else:
            self.line.setText(SOUNDS_ON_TEXT if on else SOUNDS_OFF_TEXT)
        self.button.setText(TURN_OFF_TEXT if on else TURN_ON_TEXT)
        self.button.setAccessibleName(TURN_OFF_TEXT if on else TURN_ON_TEXT)
        self.button.setVisible(not config_off)
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def _clicked(self) -> None:
        self.soundsToggled.emit(not self._on)

    def set_second(self, on: bool, available: bool) -> None:
        """The second screen's switch as it is: on or off, greyed with "Needs a second monitor" without one."""
        switch = self.second
        switch.blockSignals(True)
        switch.setChecked(bool(on))
        switch.blockSignals(False)
        switch.setEnabled(bool(available))
        switch.setAccessibleDescription("" if available else SECOND_NEEDS)
        self.second_note.setVisible(not available)
        switch.update()

    def _second_clicked(self) -> None:
        self.secondScreenToggled.emit(self.second.isChecked())


class LookController(QObject):
    """See the module docstring."""

    def __init__(self, host: Any, *, player_factory: Any = None, test_guard: bool | None = None,
                 start_sounds: bool = True) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.config = host.config
        self.sounds = sounds.SoundBoard(Path(self.config.data_dir) / sounds.FOLDER, quiet=self._quiet,
                                        enabled=self.sounds_on, visible=self._visible, motion_off=self._motion_off,
                                        player_factory=player_factory, test_guard=test_guard, parent=self)
        self._talk_listening = False
        self._pending: int | None = None
        self._answered = True
        self._thinking = QTimer(self)
        self._thinking.setSingleShot(True)
        self._thinking.setInterval(THINKING_AFTER_MS)
        self._thinking.timeout.connect(self._thinking_due)
        self._save_problem = ""
        self.second: Any = None              # part 7 (made below, once the desk is)
        self.panel: LookSettingsPanel | None = None
        self._install_settings()
        self.canvas: Any = None              # PLAN-canvas part 1: Home as the canvas (made with the desk), or None
        self.desk = self._make_desk()        # part 5a: the board on Home ([look] board), or None
        self.second = self._make_second()    # part 7: the second-screen display (shown only once turned on), or None
        if self.second is not None and self.panel is not None:
            self.panel.secondScreenToggled.connect(self.set_second_screen_on)
            self.second.availabilityChanged.connect(self._on_screens)
        if start_sounds:
            self.sounds.start()

    def _make_desk(self) -> Any:
        """The board on Home (desk_ui.DeskController) while ``[look] board`` is on and a board can be here; None with
        it off (Home exactly as it was), without a main window, or on a test platform without a factory."""
        if not getattr(getattr(self.config, "look", None), "board", True):
            return None
        window = getattr(self.host, "window", None)
        if getattr(window, "reading", None) is None:
            return None
        from . import desk_ui

        factory = getattr(self.host, "_board_factory", None)
        if not desk_ui.can_have_board(factory):
            return None
        desk = desk_ui.DeskController(self.host, factory=factory, look=self)
        if factory is None or bool(getattr(factory, "canvas", False)):
            # Home is the canvas: the real board always; a test's factory when it says so.
            try:
                from . import canvas_ui

                self.canvas = desk.canvas = canvas_ui.CanvasController(desk)
            except Exception as exc:  # noqa: BLE001 - no canvas: the board beside the conversation, as before
                logger.warning("Canvas: not started (%s)", type(exc).__name__)
                self.canvas = desk.canvas = None
        return desk

    def _make_second(self) -> Any:
        """The second-screen display's controller (second_screen), made with the main window; None without one. It
        shows nothing until the owner turns it on (or [look] second_screen does) and a second monitor is there."""
        window = getattr(self.host, "window", None)
        if getattr(window, "reading", None) is None:
            return None
        from . import second_screen

        return second_screen.SecondScreenController(self.host, look=self, board=self._board_feed)

    def _board_feed(self) -> Any:
        desk = self.desk
        return getattr(desk, "feed", None) if desk is not None else None

    # ---- the switches ---------------------------------------------------------------------------

    def _config_sounds_off(self) -> bool:
        return getattr(getattr(self.config, "look", None), "sounds", "") == SOUNDS_OFF

    def _prefs(self) -> Any:
        try:
            return self.host._prefs_store()
        except Exception:  # noqa: BLE001 - no prefs: the config alone decides
            return None

    def sounds_on(self) -> bool:
        """The soft sounds are on: config.toml does not turn them off and Settings > Look did not on this PC."""
        if self._config_sounds_off():
            return False
        store = self._prefs()
        try:
            return not (store is not None and store.look.sounds_off)
        except Exception:  # noqa: BLE001
            return True

    def second_screen_on(self) -> bool:
        """Jarvis is to show on the second screen (Settings > Look on this PC, else [look] second_screen)."""
        second = self.second
        return bool(second is not None and second.is_on())

    def set_second_screen_on(self, on: bool) -> bool:
        """Settings > Look's switch: the second screen on or off on this PC, at once; False when it could not be
        saved (it holds until Jarvis closes)."""
        second = self.second
        saved = bool(second is not None and second.set_enabled(bool(on)))
        self._save_problem = "" if saved else SAVE_FAILED_TEXT
        self._sync_panel()
        return saved

    def _on_screens(self, _available: bool) -> None:
        """A monitor was plugged in or out: Settings > Look's switch follows (greyed with one)."""
        self._sync_panel()

    def set_sounds_on(self, on: bool) -> bool:
        """Settings > Look's button: the sounds on or off on this PC; False when it could not be saved."""
        store = self._prefs()
        saved = bool(store is not None and store.set_sounds_off(not on))
        self._save_problem = "" if saved else SAVE_FAILED_TEXT
        logger.info("Look: sounds turned %s on this PC%s", "on" if on else "off", "" if saved else " (not saved)")
        self._sync_panel()
        return saved

    # ---- what keeps the sounds quiet -------------------------------------------------------------

    def _quiet(self) -> str:
        """Why the sounds keep quiet now ("" when they may play)."""
        host = self.host
        voice = getattr(host, "voice", None)
        if bool(getattr(voice, "muted", False)):
            return "muted"
        if getattr(host, "productivity", None) is not None and host._quiet_until_text():
            return "quiet hours"
        if host._focus_quiet():
            return "focus mode"
        if host._meeting_recording():
            return "a meeting records"
        if host._locked_now():
            return "locked"
        from .standing import schedule

        if schedule.windows_busy():
            return "Windows says busy"
        return ""

    def _visible(self) -> bool:
        window = getattr(self.host, "window", None)
        return bool(window is not None and window.isVisible() and not window.isMinimized())

    @staticmethod
    def _motion_off() -> bool:
        return holo.motion_level() == holo.MOTION_OFF

    # ---- the app's hooks ------------------------------------------------------------------------

    def on_speaking(self) -> None:
        """Jarvis's voice or a briefing started or stopped."""
        host = self.host
        from .player import PLAYING, WAITING

        speaking = bool(getattr(host, "_utterance", "")) or bool(getattr(getattr(host, "voice", None), "speaking",
                                                                           False))
        player = getattr(host, "player", None)
        if isinstance(player, QObject) and not shiboken6.isValid(player):
            player = None                       # a player being replaced or deleted is not playing
        playing = player is not None and getattr(player, "state", "") in (PLAYING, WAITING)
        self.sounds.set_speaking(speaking or playing)
        if self.desk is not None:               # part 5c: the sentence he says lights the frame it is about
            self.desk.on_speech(str(getattr(host, "_utterance", "") or ""))

    def on_muted(self, muted: bool) -> None:
        """His voice was muted (or unmuted): a lit frame and its cue line go at once."""
        if muted and self.desk is not None:
            self.desk.on_muted()

    # ---- the second screen (part 7) -------------------------------------------------------------------------------

    def on_board_changed(self, feed: Any) -> None:
        """The board changed (the desk's every send): its frames, as the page has them, to the second screen."""
        if self.second is not None:
            self.second.on_board(feed)

    def on_events(self, events: Any) -> None:
        """The calendar's events as the app last read them: the second screen's NEXT."""
        if self.second is not None:
            self.second.set_events(events or ())

    def shutdown(self) -> None:
        """Jarvis closes: the second screen's window goes now."""
        if self.second is not None:
            self.second.shutdown()

    # ---- the board's sounds (part 5c) -----------------------------------------------------------------------------

    def on_frame_landed(self, place: int) -> str:
        """A frame finished drawing in, the ``place``-th of its group (only the first four sound)."""
        if not 0 <= int(place) < len(sounds.FRAME_STEPS):
            return sounds.R_UNKNOWN
        return self.sounds.play(sounds.FRAME, int(place))

    def on_group_settled(self) -> str:
        return self.sounds.play(sounds.SETTLED)

    def on_frame_changed(self) -> str:
        return self.sounds.play(sounds.CHANGED)

    def on_big(self, opened: bool) -> str:
        return self.sounds.play(sounds.OPEN_BIG if opened else sounds.CLOSE_BIG)

    def on_talk_state(self, state: str) -> None:
        """The talk key's or "Hey Jarvis"'s listening: HEARD as it opens."""
        from . import talk_ui

        listening = state == talk_ui.LISTENING
        if listening and not self._talk_listening:
            self.sounds.play(sounds.HEARD)
        self._talk_listening = listening

    def on_wake_chime(self) -> None:
        self.sounds.note_wake_chime()

    def on_ask_started(self) -> None:
        self._answered = False
        self._thinking.start()
        if self.desk is not None:
            self.desk.on_ask_started()      # the board starts with the first Ask (never at the app's start)

    def on_answer_started(self, task_id: int, text: str) -> None:
        """An Ask shown on Home started (its LIVE task id; the owner's words): its group-to-be on the board."""
        if self.desk is not None:
            self.desk.on_answer_started(task_id, text)

    def on_ask_outcome(self, outcome: Any, task_id: int = 0) -> None:
        """A finished answer: its frames go up on the board as one group."""
        if self.desk is not None:
            self.desk.on_ask_outcome(outcome, task_id)
        if self.canvas is not None:
            self.canvas.on_answer()          # the canvas: the keyboard is in the ask line after an answer

    def on_instant_answer(self, reply: Any, words: str) -> None:
        """Part 6: Jarvis's own instant answer; a sum's working, when it has one, goes up as a maths frame."""
        if self.desk is not None:
            self.desk.on_instant_answer(reply, words)
        if self.canvas is not None:
            self.canvas.on_answer()

    def on_ask_answer(self) -> None:
        self._answered = True
        self._thinking.stop()

    def _thinking_due(self) -> None:
        ask = getattr(self.host, "ask", None)
        if not self._answered and ask is not None and bool(getattr(ask, "busy", False)):
            self.sounds.play(sounds.THINKING)

    def on_pending(self, count: int) -> None:
        """The cards waiting for the owner: WAITS when their number goes up (the first count is the baseline)."""
        count = max(0, int(count))
        if self._pending is not None and count > self._pending:
            self.sounds.play(sounds.WAITS)
        self._pending = count

    def on_action_result(self, status: str, failed: bool) -> None:
        """An approved card's action came back: DONE, or FAILED."""
        from .actions import STATUS_CREATED, STATUS_EXISTS, STATUS_FAILED, STATUS_SENT

        if failed or status == STATUS_FAILED:
            self.sounds.play(sounds.FAILED)
        elif status in (STATUS_CREATED, STATUS_EXISTS, STATUS_SENT):
            self.sounds.play(sounds.DONE)

    # ---- Settings > Look --------------------------------------------------------------------------

    def settings_rows(self) -> list[tuple[str, str]]:
        if self._config_sounds_off():
            value = SOUNDS_OFF_CONFIG_VALUE
        else:
            value = SOUNDS_SOFT_VALUE if self.sounds_on() else SOUNDS_OFF_VALUE
        board = "On" if getattr(getattr(self.config, "look", None), "board", True) else "Off"
        canvas = self.canvas
        if canvas is not None and not canvas.fallen():
            return [(SOUNDS_LABEL, value), (CANVAS_LABEL, board)]      # Home is the canvas (the same [look] board)
        return [(SOUNDS_LABEL, value), (BOARD_LABEL, board)]

    def _install_settings(self) -> None:
        window = getattr(self.host, "window", None)
        page = getattr(getattr(window, "reading", None), "settings_page", None)
        if page is None:
            return
        self.panel = LookSettingsPanel()
        self.panel.soundsToggled.connect(self.set_sounds_on)
        page.add_group(SETTINGS_KEY, SETTINGS_TITLE, note=SETTINGS_NOTE, how=SETTINGS_HOW, rows=self.settings_rows,
                       extra=self.panel)
        # assistant.json is read only when the group opens (the app opens it on first use, never at start).
        changed = getattr(page, "groupChanged", None)
        if changed is not None:
            changed.connect(self._on_group)

    def _on_group(self, key: str) -> None:
        if key == SETTINGS_KEY:
            self._sync_panel()

    def _sync_panel(self) -> None:
        if self.panel is None:
            return
        self.panel.set_state(self.sounds_on(), config_off=self._config_sounds_off(), problem=self._save_problem)
        second = self.second
        self.panel.set_second(self.second_screen_on(), bool(second is not None and second.available()))
        self.panel.second.setVisible(second is not None)
        window = getattr(self.host, "window", None)
        page = getattr(getattr(window, "reading", None), "settings_page", None)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
