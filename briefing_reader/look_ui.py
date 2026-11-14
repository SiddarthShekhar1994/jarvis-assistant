"""The new look's controller (build-plan item 2): the soft sounds (part 1) and Settings > Look; the board and the
second screen join it in later parts.

The app (``ui.AppController``) makes one ``LookController`` and calls it through ``_look_call(name, *args)``, which
never lets it stop the app. Its hooks:

- ``on_talk_state(state)``: the talk key's or "Hey Jarvis"'s listening (HEARD as it opens);
- ``on_wake_chime()``: "Hey Jarvis"'s own chime played (HEARD is skipped for a second);
- ``on_ask_started()`` / ``on_ask_answer()``: THINKING when nothing was said 400 ms after a request went in;
- ``on_pending(count)``: WAITS when the number of cards waiting for the owner goes up (never at the first count);
- ``on_action_result(status, failed)``: DONE when an approved card's action finished, FAILED when it failed;
- ``on_speaking()``: Jarvis's voice or a briefing started or stopped.

Settings > Look shows the switches as they are and has the one thing it changes on this PC: the sounds on or off
(assistant.json's "look" table). [look] in config.toml sets the defaults; ``sounds = "off"`` there wins.

Logs hold event names and reasons only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import holo, sounds
from .config import SOUNDS_OFF
from .memory_view import body_label

logger = logging.getLogger(__name__)

THINKING_AFTER_MS = 400           # a request that has said nothing by then gets the thinking sound

SETTINGS_KEY = "look"
SETTINGS_TITLE = "Look"
SETTINGS_NOTE = "How Jarvis looks and sounds."
SETTINGS_HOW = ("The sounds can be turned off or on here, on this PC. [look] in config.toml sets the defaults; "
                "restart Jarvis after editing it.")
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


class LookSettingsPanel(QWidget):
    """Settings > Look's own part: the sounds line and its one button."""

    soundsToggled = Signal(bool)           # True: turn the sounds on; False: off

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line: QLabel = body_label(SOUNDS_ON_TEXT, "body_sm", holo.INK_2, name="lookSoundsLine")
        self.problem: QLabel = body_label("", "body_sm", holo.ALERT_TX, name="lookSoundsProblem")
        self.problem.hide()
        self.button = holo.HoloButton(TURN_OFF_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.button.clicked.connect(self._clicked)
        self._on = True
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
        self.panel: LookSettingsPanel | None = None
        self._install_settings()
        if start_sounds:
            self.sounds.start()

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
        window = getattr(self.host, "window", None)
        page = getattr(getattr(window, "reading", None), "settings_page", None)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
