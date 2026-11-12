""""Hey Jarvis" in the app (Qt): the switch, the listener, the pauses and what shows it (owner decisions 2026-11-12).

The app makes a :class:`WakeController` beside the talk (AppController._create_wake, only while the talk exists: [talk]
and Ask on). "Hey Jarvis" is off until the owner turns it on - Settings > Talk's Turn on, or the tray menu's
"Listen for "Hey Jarvis"" - and only a saved "on" counts (wake.json in the data folder, :class:`WakeStore`). While it
is on and everything it needs is here (the wake model, downloaded only on the owner's click; onnxruntime; the talk's
own add-ons and speech model), it runs the listener process (wake_listen.WakeClient) from the app's start to its end:
it works while Jarvis runs - his window open, minimized or on the desktop HUD - and stops when he is closed.

The listener pauses (a hold, from the app's ``holds()``) while a talk listens or is understood, while Jarvis speaks or
reads the briefing, while a meeting records and in focus mode; it listens again HOLD_TAIL_MS after the last hold went
(his last word's echo). While Jarvis is in control on the desktop a wake is ignored, as the talk key is.

A wake: the listener's reader thread gathers the audio from before the wake (the pre-roll) into a
:class:`HandoffStream`, then the talk takes over on it (talk_ui.TalkController.listen_from_wake: Jarvis comes forward,
the chime when nothing was said yet, the same routes and gates as typed words - cards still need a click). The talk's
end closes the stream, which ends the hand-off. A listener that ends by itself is started again (RESTART_DELAYS_S),
and after too many failures it stops, saying so.

What shows it: the HEY JARVIS mark (:class:`WakeBadge`) in the window's header and on the desktop HUD (cyan while it
listens, dim while paused, amber when something is wrong; ``ReadingView.set_wake``), the tray icon's mark and
tooltip, and Settings > Talk (:class:`WakePanel` and :meth:`WakeController.settings_rows`).

Privacy: the audio stays in the two processes' memory; nothing is written, logged or sent. Logs and wake.json hold
statuses, counts and scores only - never words, audio or device names.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import threading
import time
import wave as wave_mod
from collections.abc import Callable
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import holo, hud, talk, wake, wake_listen

logger = logging.getLogger(__name__)

WAKE_FILE = "wake.json"
CHIME_FILE = "wake_chime.wav"
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"       # "1" while the tests run: no sound is played
HOLD_TAIL_MS = 700                # the listener listens again this long after the last hold went
RESTART_DELAYS_S = (2.0, 10.0, 60.0, 300.0)       # a listener that ended by itself is started again after these ...
RESTART_RESET_S = 600.0           # ... counted afresh once one ran this long
BADGE_WORDS = "HEY JARVIS"
PHRASE = "“Hey Jarvis”"

LOOK_OFF, LOOK_LISTENING, LOOK_PAUSED, LOOK_PROBLEM = "off", "listening", "paused", "problem"
HOLD_TALK, HOLD_SPEAKING, HOLD_MEETING, HOLD_FOCUS = "talk", "speaking", "meeting", "focus"
HOLD_TEXTS = {HOLD_TALK: "while you talk to him", HOLD_SPEAKING: "while he speaks",
              HOLD_MEETING: "while a meeting records", HOLD_FOCUS: "in focus mode"}
PROBLEM_STOPPED = "stopped"       # the listener kept failing: it is not started again until the owner turns it on
PROBLEM_UNSAVED = "unsaved"       # Turn on could not be saved (fail-closed: it stays off)
PROBLEM_UNSAVED_OFF = "unsaved_off"   # Turn off could not be saved: off for this run, on again at the next start

SETTINGS_NOTE = (f"Say {PHRASE} and Jarvis listens as if you had tapped the talk key - in one breath (“Hey "
                 "Jarvis, open Spotify”) or after his soft chime. A small listener on this PC hears only for those "
                 "words: nothing is saved or sent while it waits. It works while Jarvis runs - minimize him rather "
                 "than closing him - and pauses while he speaks, a meeting records, a talk listens and in focus mode. "
                 "Saying “Jarvis” alone, or words that sound like it, can wake him too.")
LICENCE_NOTE = (f"The wake model is openWakeWord's “hey jarvis” ({wake.MODEL_SIZE_TEXT} from GitHub; "
                f"{wake.MODEL_LICENCE}). It is downloaded only when you click.")


# --------------------------------------------------------------------------
# The switch and today's counts (wake.json)
# --------------------------------------------------------------------------

class WakeStore:
    """``wake.json`` in the data folder: ``on`` (only a saved true: fail-closed), ``sensitivity`` and today's counts
    (wakes that started a talk, and those that heard nothing). Statuses and numbers only."""

    WAKES, NOTHING = "wakes", "nothing"

    def __init__(self, folder: Path | str, *, today: Callable[[], date] = date.today) -> None:
        self.path = Path(folder) / WAKE_FILE
        self._today = today
        self._lock = threading.Lock()
        data = self._load()
        self._on = data.get("on") is True
        self._sensitivity = wake.sensitivity_of(data.get("sensitivity")).name
        counts = data.get("today") if isinstance(data.get("today"), dict) else {}
        self._day = str(counts.get("day") or "")
        self._counts = {key: _count(counts.get(key)) for key in (self.WAKES, self.NOTHING)}

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Wake: %s could not be read (%s); \"Hey Jarvis\" stays off", WAKE_FILE, type(exc).__name__)
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def on(self) -> bool:
        with self._lock:
            return self._on

    @property
    def sensitivity(self) -> str:
        with self._lock:
            return self._sensitivity

    def set_on(self, on: bool) -> bool:
        """The owner's Turn on / off; True when saved. A Turn on that could not be saved is undone (never on unsaved);
        a Turn off counts for this run even unsaved."""
        with self._lock:
            before = self._on
            self._on = bool(on)
            saved = self._save_locked()
            if not saved and self._on:
                self._on = before
            return saved

    def set_sensitivity(self, name: str) -> bool:
        with self._lock:
            self._sensitivity = wake.sensitivity_of(name).name
            return self._save_locked()

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts) if self._day == self._today().isoformat() else {self.WAKES: 0, self.NOTHING: 0}

    def count(self, key: str) -> None:
        with self._lock:
            day = self._today().isoformat()
            if self._day != day:
                self._day, self._counts = day, {self.WAKES: 0, self.NOTHING: 0}
            self._counts[key] = self._counts.get(key, 0) + 1
            self._save_locked()

    def _save_locked(self) -> bool:
        from .prefs import _write_atomic

        values = {"on": self._on, "sensitivity": self._sensitivity, "today": {"day": self._day, **self._counts}}
        return _write_atomic(self.path, json.dumps(values, indent=2) + "\n")


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1_000_000 else 0


# --------------------------------------------------------------------------
# The hand-off: the listener's audio as a talk's stream
# --------------------------------------------------------------------------

class HandoffStream:
    """A talk's stream on the listener's audio (thread-safe). The listener's reader thread ``push``es what it hands
    over - the first ``preroll_bytes`` are the pre-roll (``preroll``), the rest the audio as it comes - and ``end``s it
    (the listener stopped it, or went away). The talk reads it (``read_available``: what came, b"" for now, OSError
    once it ended and is drained) and ``close``s it, which ends the hand-off (``on_close``)."""

    def __init__(self, preroll_bytes: int, on_close: Callable[[HandoffStream], Any]) -> None:
        self._lock = threading.Lock()
        self._need = max(0, int(preroll_bytes))
        self._preroll = bytearray()
        self._data = bytearray()
        self._ended = False
        self.closed = False
        self._on_close = on_close

    @property
    def preroll(self) -> bytes:
        with self._lock:
            return bytes(self._preroll)

    def preroll_complete(self) -> bool:
        with self._lock:
            return len(self._preroll) >= self._need

    @property
    def ended(self) -> bool:
        with self._lock:
            return self._ended

    def push(self, pcm: bytes) -> bool:
        """Audio from the listener; True when this piece completed the pre-roll."""
        with self._lock:
            if self.closed:
                return False
            had = len(self._preroll) >= self._need
            if not had:
                take = min(len(pcm), self._need - len(self._preroll))
                self._preroll += pcm[:take]
                pcm = pcm[take:]
            self._data += pcm
            return not had and len(self._preroll) >= self._need

    def end(self) -> None:
        with self._lock:
            self._ended = True

    def read_available(self) -> bytes:
        with self._lock:
            if self._data:
                piece = bytes(self._data)
                self._data = bytearray()
                return piece
            if self._ended or self.closed:
                raise OSError("the listener's audio ended")
            return b""

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._data = bytearray()
            self._preroll = bytearray()
        try:
            self._on_close(self)
        except Exception as exc:  # noqa: BLE001 - the listener ends a hand-off by itself too (HANDOFF_MAX_S)
            logger.info("Wake: ending the hand-off failed (%s)", type(exc).__name__)


# --------------------------------------------------------------------------
# The chime
# --------------------------------------------------------------------------

CHIME_RATE = 44_100
CHIME_NOTES = ((660.0, 75), (990.0, 85))          # two soft rising notes (Hz, ms), 10 ms apart: 170 ms
CHIME_PEAK = 0.16                                 # about 16 dB below full scale


def chime_wav() -> bytes:
    """The chime as a 44.1 kHz mono 16-bit WAV file (made here: no sound file in the repository)."""
    samples: list[int] = []
    fade = CHIME_RATE * 10 // 1000
    for index, (freq, ms) in enumerate(CHIME_NOTES):
        if index:
            samples += [0] * fade
        count = CHIME_RATE * ms // 1000
        for i in range(count):
            envelope = min(1.0, i / fade, (count - 1 - i) / fade)
            samples.append(round(32767 * CHIME_PEAK * envelope * math.sin(2 * math.pi * freq * i / CHIME_RATE)))
    buffer = io.BytesIO()
    with wave_mod.open(buffer, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(CHIME_RATE)
        fh.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples))
    return buffer.getvalue()


def play_chime(folder: Path | str) -> None:
    """The chime, not waited for (written once to ``folder``/CHIME_FILE). Never while the tests run."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        return
    path = Path(folder) / CHIME_FILE
    try:
        data = chime_wav()
        if not path.is_file() or path.stat().st_size != len(data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception as exc:  # noqa: BLE001 - the window still shows that he listens
        logger.info("Wake: no chime (%s)", type(exc).__name__)


# --------------------------------------------------------------------------
# What shows it
# --------------------------------------------------------------------------

_BADGE_LOOKS = {   # dot, words
    LOOK_LISTENING: (holo.HOLO, holo.INK_2),
    LOOK_PAUSED: (holo.STEEL, holo.INK_4),
    LOOK_PROBLEM: (holo.AMBER, holo.AMBER_TX),
}
BADGE_DOT = 4.0


class WakeBadge(QWidget):
    """HEY JARVIS in a header (every screen; the frame header over the prompt view): a dot and the words - cyan while
    the listener listens, dim while it is paused, amber when something is wrong; the tooltip says which. Hidden while
    "Hey Jarvis" is off."""

    ROLE = "readout"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._look = LOOK_OFF
        self._text = ""
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Hey Jarvis")
        self.hide()

    def set_state(self, text: str, look: str) -> None:
        look = look if look in _BADGE_LOOKS else LOOK_OFF
        if (text, look) == (self._text, self._look):
            return
        self._text, self._look = text, look
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.setVisible(look != LOOK_OFF)
        self.update()

    def look(self) -> str:
        return self._look

    def text(self) -> str:
        return BADGE_WORDS

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        metrics = QFontMetricsF(holo.font(self.ROLE))
        margins = self.contentsMargins()
        width = BADGE_DOT * 2 + 7 + metrics.horizontalAdvance(BADGE_WORDS) + 4 + margins.left() + margins.right()
        return QSize(math.ceil(width), int(max(20, metrics.height() + 4)))

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        dot, ink = _BADGE_LOOKS.get(self._look, _BADGE_LOOKS[LOOK_PAUSED])
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = QPointF(BADGE_DOT + 1, self.height() / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(holo.alpha(dot, 0.25))
            painter.drawEllipse(center, BADGE_DOT + 2, BADGE_DOT + 2)
            painter.setBrush(QColor(dot))
            painter.drawEllipse(center, BADGE_DOT - 1, BADGE_DOT - 1)
            painter.setFont(holo.font(self.ROLE))
            painter.setPen(QColor(ink))
            left = BADGE_DOT * 2 + 7
            right = self.contentsMargins().right()
            painter.drawText(QRectF(left, 0, max(0.0, self.width() - left - right), self.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), BADGE_WORDS)
        finally:
            painter.end()


CHECKED_STYLE = f"QPushButton:checked {{ color: {holo.HOLO}; border: 1px solid {holo.HOLO}; padding: 3px 7px; }}"


class WakePanel(QWidget):
    """Settings > Talk's "Hey Jarvis" part: what it is doing, Turn on / off, the wake model's Download (only on a click)
    and the sensitivity."""

    switchClicked = Signal(bool)           # True: Turn on; False: Turn off
    downloadClicked = Signal()
    sensitivityChosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(8)
        self.title = QLabel("Hey Jarvis")
        self.title.setFont(hud.body_font(14, 600))
        hud.set_label_color(self.title, hud.TEXT_BODY)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(hud.body_font(13))
        hud.set_label_color(self.status, hud.TEXT_SOFT)
        self.switch = QPushButton("Turn on “Hey Jarvis”")
        self.switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.switch.clicked.connect(lambda: self.switchClicked.emit(not self._on))
        self.download = QPushButton("Download the wake model")
        self.download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download.clicked.connect(self.downloadClicked)
        self.sensitivity_label = QLabel("Sensitivity")
        self.sensitivity_label.setFont(hud.body_font(13))
        hud.set_label_color(self.sensitivity_label, hud.TEXT_SOFT)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.sensitivity_buttons: dict[str, QPushButton] = {}
        for name, preset in wake.SENSITIVITIES.items():
            button = QPushButton(preset.label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(CHECKED_STYLE)        # the chosen one stands out (the theme leaves :checked plain)
            button.clicked.connect(lambda _checked=False, which=name: self.sensitivityChosen.emit(which))
            self._group.addButton(button)
            self.sensitivity_buttons[name] = button
            row.addWidget(button)
        row.addStretch(1)
        self.licence = QLabel(LICENCE_NOTE)
        self.licence.setWordWrap(True)
        self.licence.setFont(hud.body_font(12))
        hud.set_label_color(self.licence, hud.TEXT_SOFT)
        for widget in (self.title, self.status, self.switch, self.download, self.sensitivity_label):
            layout.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self.licence)
        self._on = False

    def show_state(self, *, on: bool, status: str, model_ready: bool, downloading: bool, percent: int, error: str,
                   sensitivity: str) -> None:
        self._on = bool(on)
        if downloading:
            status = f"Downloading the wake model ({wake.MODEL_SIZE_TEXT}): {percent}%."
        elif error and not model_ready:
            status = f"The download stopped ({error}). Click Download to try again."
        self.status.setText(status)
        self.switch.setText(f"Turn {'off' if on else 'on'} {PHRASE}")
        self.download.setVisible(on and not model_ready)
        self.download.setEnabled(not downloading)
        self.download.setText(f"Downloading... {percent}%" if downloading
                              else f"Download the wake model ({wake.MODEL_SIZE_TEXT})")
        for name, button in self.sensitivity_buttons.items():
            button.setChecked(name == sensitivity)


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------

class WakeController(QObject):
    """See the module docstring. ``holds()`` names what pauses the listener now (HOLD_*); ``in_control()``: Jarvis is
    in control on the desktop. ``threaded`` False (tests): the listener's lines and downloads run in the caller."""

    changed = Signal()                       # what shows it changed (the marks, the tray, Settings)
    _event = Signal(object, object, object)  # (a listener's line, its hand-off stream, which listener), to the GUI
    _download_progress = Signal(int, int)
    _downloaded = Signal(str)

    def __init__(self, config: Any, talk_controller: Any, *, holds: Callable[[], list[str]],
                 in_control: Callable[[], bool] = lambda: False, picked_mic: Callable[[], str] | None = None,
                 store: WakeStore | None = None, models_root: Path | None = None,
                 client_factory: Callable[..., Any] | None = None, downloader_factory: Callable[..., Any] | None = None,
                 runtime_missing: Callable[[], str] | None = None, threaded: bool = True,
                 clock: Callable[[], float] = time.monotonic, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from . import meetings

        self._talk = talk_controller
        self._holds_now = holds
        self._in_control = in_control
        self._picked_mic = picked_mic or (lambda: meetings.DevicePrefs(meetings.meetings_root(config)).microphone)
        self._store = store or WakeStore(config.data_dir)
        self._models_root = Path(models_root) if models_root is not None else meetings.models_root(config)
        self._folder = wake.model_dir(self._models_root)
        self._client_factory = client_factory or (
            lambda folder, on_line, sensitivity: wake_listen.WakeClient(folder, on_line, sensitivity=sensitivity))
        self._downloader_factory = downloader_factory or (
            lambda root, progress, cancel: wake.WakeModelDownloader(root, progress=progress, cancel=cancel))
        self._runtime_missing = runtime_missing or wake.runtime_missing
        self._threaded = threaded
        self._clock = clock
        self._lock = threading.Lock()
        self._client: Any = None
        self._token: object | None = None               # the running listener's own mark: a replaced one's lines don't count
        self._ready_at = 0.0                            # when the running listener said it was ready (0: not yet)
        self._stream: HandoffStream | None = None       # the hand-off being gathered or under way (reader thread)
        self._heard: dict | None = None
        self._started = False
        self._closing = False
        self._listener_state = ""
        self._mic: tuple[str, bool] = ("", False)
        self._problem = ""
        self._holds: tuple[str, ...] = ()
        self._held = False
        self._failures = 0
        self._downloading = False
        self._download_percent = 0
        self._download_error = ""
        self._download_cancel = threading.Event()
        self._panel: WakePanel | None = None
        self._refresh_settings: Callable[[], Any] = lambda: None
        self._tail_timer = QTimer(self)
        self._tail_timer.setSingleShot(True)
        self._tail_timer.setInterval(HOLD_TAIL_MS)
        self._tail_timer.timeout.connect(self._release_hold)
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._apply)
        self._event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._download_progress.connect(self._on_download_progress, Qt.ConnectionType.QueuedConnection)
        self._downloaded.connect(self._on_downloaded, Qt.ConnectionType.QueuedConnection)
        talk_controller.wakeTalkEnded.connect(self._on_wake_talk_ended)
        talk_controller.stateChanged.connect(lambda _state: self.refresh_holds())

    # ---- the switch ---------------------------------------------------------------------------------------------

    @property
    def on(self) -> bool:
        return self._store.on

    def start(self) -> None:
        """The app started: the listener runs from now while "Hey Jarvis" is on and ready (never before: a controller
        that is never started never opens a microphone)."""
        self._started = True
        self._apply()

    def set_on(self, on: bool) -> None:
        """The owner's Turn on / off (Settings > Talk, the tray menu)."""
        if bool(on) == self._store.on:
            return
        saved = self._store.set_on(on)
        self._problem = "" if saved else PROBLEM_UNSAVED if on else PROBLEM_UNSAVED_OFF
        self._failures = 0
        logger.info("Wake: turned %s by the owner%s", "on" if self._store.on else "off",
                    "" if saved else " (could not be saved)")
        self._apply()
        self._changed()

    def set_sensitivity(self, name: str) -> None:
        self._store.set_sensitivity(name)
        logger.info("Wake: sensitivity %s", self._store.sensitivity)
        if self._client is not None:
            self._client.send({"sensitivity": self._store.sensitivity})
        self._changed()

    def missing(self) -> list[str]:
        """What "Hey Jarvis" needs that is not here: "runtime", "wake_model", then the talk's own (talk_ui.missing)."""
        pieces = []
        if self._runtime_missing():
            pieces.append("runtime")
        if not wake.model_ready(self._folder):
            pieces.append("wake_model")
        return pieces + list(self._talk.missing())

    def _should_run(self) -> bool:
        return (self._started and not self._closing and self._store.on
                and self._problem not in (PROBLEM_STOPPED, wake.ERR_MODEL, wake.ERR_ADDON) and not self.missing())

    def _apply(self) -> None:
        """The listener runs exactly while it should (started, on, everything here)."""
        if self._should_run():
            if self._client is None and not self._restart_timer.isActive():
                self._spawn()
        else:
            self._restart_timer.stop()
            self._stop_client()
        self._changed()

    def _spawn(self) -> None:
        with self._lock:
            self._stream, self._heard = None, None
        self._listener_state, self._mic = "", ("", False)
        token = object()
        self._token, self._ready_at = token, 0.0
        client = self._client_factory(self._folder, partial(self._on_line, token), self._store.sensitivity)
        if not client.start():
            logger.info("Wake: the listener could not be started")
            self._client, self._token = None, None
            self._retry()
            return
        self._client = client
        logger.info("Wake: the listener started (%s)", self._store.sensitivity)

    def _stop_client(self) -> None:
        client, self._client = self._client, None
        self._token = None                                # what it still says from now on is let go
        self._end_stream()
        self._listener_state, self._held, self._holds = "", False, ()
        self._tail_timer.stop()
        if client is None:
            return
        logger.info("Wake: the listener stopped")
        self._reap(client)

    def _reap(self, client: Any) -> None:
        """Make sure a listener's process is gone (asked to quit, then ended)."""
        if self._threaded:
            threading.Thread(target=client.close, name="wake-listen-close", daemon=True).start()
        else:
            client.close()

    def _retry(self) -> None:
        """The listener ended by itself (or would not start): started again after a while; after too many, stopped. A
        listener that was ready for RESTART_RESET_S counts the failures afresh."""
        if self._ready_at and self._clock() - self._ready_at >= RESTART_RESET_S:
            self._failures = 0
        if self._failures >= len(RESTART_DELAYS_S):
            self._problem = PROBLEM_STOPPED
            logger.warning("Wake: the listener kept failing; stopped until \"Hey Jarvis\" is turned on again")
            self._changed()
            return
        delay = RESTART_DELAYS_S[self._failures]
        self._failures += 1
        logger.info("Wake: starting the listener again in %d s", int(delay))
        self._restart_timer.start(int(delay * 1000))

    # ---- the listener's lines --------------------------------------------------------------------------------------

    def _on_line(self, token: object, message: dict | None) -> None:
        """On a listener's reader thread: a wake's audio is gathered here, the rest goes to the GUI thread. Only the
        running listener counts (``token``): one that was replaced or stopped may still be ending."""
        if token is not self._token:
            return
        if message is None:
            self._end_stream()
            self._post(None, None, token)
            return
        if "heard" in message:
            stream = HandoffStream(int(message.get("preroll_bytes") or 0), self._handoff_closed)
            with self._lock:
                old, self._stream, self._heard = self._stream, stream, message
            if old is not None:
                old.end()
            if stream.preroll_complete():
                self._post(message, stream, token)
            return
        if "audio" in message:
            with self._lock:
                stream, heard = self._stream, self._heard
            if stream is None:
                return
            try:
                pcm = base64.b64decode(message["audio"])
            except (ValueError, TypeError):
                return
            if stream.push(pcm):
                self._post(heard, stream, token)         # the pre-roll is here: the talk can take over
            return
        if message.get("audio_end") is True:
            with self._lock:
                stream, heard = self._stream, self._heard
            if stream is not None:
                stream.end()
                if not stream.preroll_complete():
                    self._post(heard, stream, token)     # it ended early: the GUI thread lets it go
            return
        self._post(message, None, token)

    def _post(self, message: dict | None, stream: HandoffStream | None, token: object) -> None:
        if self._threaded:
            self._event.emit(message, stream, token)
        else:
            self._on_event(message, stream, token)

    def _on_event(self, message: dict | None, stream: HandoffStream | None, token: object) -> None:
        if self._closing or token is not self._token:      # closing, or from a listener that was replaced meanwhile
            if stream is not None:
                stream.close()
            return
        if message is None:
            self._on_exit()
            return
        if stream is not None:
            self._on_heard(message, stream)
            return
        if "ready" in message:
            self._on_ready(message)
        elif "state" in message:
            self._listener_state = str(message.get("state") or "")
            self._mic = (str(message.get("mic") or ""), bool(message.get("default")))
            logger.info("Wake: the listener is %s", self._listener_state)
            self._changed()

    def _on_ready(self, message: dict) -> None:
        client = self._client
        if client is None:
            return
        if message.get("ready") is True:
            self._ready_at = self._clock()
            logger.info("Wake: the listener is ready (%d ms)", int(message.get("load_ms") or 0))
            self.refresh_holds()                       # held first: a listener started while he speaks never opens
            if self._held:                             # the microphone to listen (refresh_holds sends a hold only as
                client.send({"hold": True})            # it starts)
            client.send({"listen": {"mic": self._picked()}})
            client.send({"sensitivity": self._store.sensitivity})
            return
        error = str(message.get("error") or wake.ERR_FAILED)
        logger.info("Wake: the listener could not load (%s)", error)
        if error in (wake.ERR_MODEL, wake.ERR_ADDON):
            self._problem = error                       # the owner's to fix: never started again by itself
            self._stop_client()
            self._changed()

    def _on_exit(self) -> None:
        """The running listener's output ended: it exited (asked to, or by itself)."""
        client = self._client
        if client is None:
            return
        self._client, self._token = None, None
        self._reap(client)                              # its process is gone, or made to go
        self._listener_state, self._held, self._holds = "", False, ()
        if self._problem in (wake.ERR_MODEL, wake.ERR_ADDON) or not self._should_run():
            self._changed()
            return
        logger.info("Wake: the listener ended by itself")
        self._retry()
        self._changed()

    def _picked(self) -> str:
        try:
            return str(self._picked_mic() or "")
        except Exception:  # noqa: BLE001 - the Windows default then
            return ""

    def mic_picked(self) -> None:
        """The microphone picked in Settings > Meetings changed: the listener moves to it."""
        if self._client is not None and self._listener_state:
            self._client.send({"listen": {"mic": self._picked()}})

    # ---- a wake ------------------------------------------------------------------------------------------------

    def _on_heard(self, message: dict, stream: HandoffStream) -> None:
        score = float(message.get("score") or 0.0)
        why = self._refused()
        if why or stream.ended:
            logger.info("Wake: heard (score %.2f) but %s; ignored", score, why or "its audio ended")
            stream.close()
            return
        logger.info("Wake: heard (score %.2f)", score)
        started = self._talk.listen_from_wake(stream, preroll=stream.preroll, device_name=self._mic[0],
                                              follows_default=self._mic[1], speaking=bool(message.get("speaking")))
        if not started:
            stream.close()
            return
        self._store.count(WakeStore.WAKES)
        self._changed()

    def _refused(self) -> str:
        if not self._store.on:
            return "it was turned off"
        if self._holds:
            return "it is paused"
        try:
            if self._in_control():
                return "Jarvis is in control on the desktop"
        except Exception:  # noqa: BLE001 - unknown: not in control
            pass
        return ""

    def _handoff_closed(self, stream: HandoffStream) -> None:
        """A hand-off stream was closed (the talk ended, or the wake was let go): the listener listens again - only for
        the hand-off under way (an older stream closing never ends a newer one)."""
        with self._lock:
            current = self._stream is stream
            if current:
                self._stream, self._heard = None, None
        client = self._client
        if current and client is not None:
            client.send({"handoff_end": True})

    def _end_stream(self) -> None:
        with self._lock:
            stream, self._stream, self._heard = self._stream, None, None
        if stream is not None:
            stream.end()                                  # a talk on it ends ("the microphone stopped")

    def _on_wake_talk_ended(self, outcome: str) -> None:
        from .talk_ui import WAKE_HEARD_NOTHING

        if outcome == WAKE_HEARD_NOTHING:
            self._store.count(WakeStore.NOTHING)
        logger.info("Wake: the talk it started ended (%s)", outcome)
        self._changed()

    # ---- pauses --------------------------------------------------------------------------------------------------

    def refresh_holds(self) -> None:
        """What pauses the listener changed (or may have): it pauses at once, and listens again HOLD_TAIL_MS after
        the last hold went."""
        if self._closing:
            return
        try:
            holds = tuple(self._holds_now()) if self._store.on else ()
        except Exception as exc:  # noqa: BLE001 - unknown: paused (nothing is heard)
            logger.info("Wake: what pauses it could not be read (%s)", type(exc).__name__)
            holds = (HOLD_TALK,)
        if holds:
            self._tail_timer.stop()
            if not self._held:
                self._held = True
                self._send({"hold": True})
                logger.info("Wake: paused (%s)", ", ".join(holds))
            if holds != self._holds:
                self._holds = holds
                self._changed()
        elif self._held and not self._tail_timer.isActive():
            self._tail_timer.start()

    def _release_hold(self) -> None:
        try:
            holds = tuple(self._holds_now()) if self._store.on else ()
        except Exception:  # noqa: BLE001 - checked again at the next change
            return
        if holds:
            self.refresh_holds()
            return
        self._held, self._holds = False, ()
        self._send({"hold": False})
        logger.info("Wake: listening again")
        self._changed()

    def _send(self, payload: dict) -> None:
        client = self._client
        if client is not None:
            client.send(payload)

    # ---- what shows it -------------------------------------------------------------------------------------------

    def status(self) -> tuple[str, str]:
        """(what it is doing, in words; LOOK_*) for the marks, the tray and Settings."""
        if not self._store.on:
            if self._problem == PROBLEM_UNSAVED_OFF:
                return (f"{PHRASE} is off for now, but that could not be saved: it listens again when Jarvis next "
                        "starts.", LOOK_OFF)
            return (f"{PHRASE} is off" + (": turning it on could not be saved" if self._problem == PROBLEM_UNSAVED
                                          else ""), LOOK_OFF)
        missing = self.missing()
        if missing:
            return self._missing_text(missing), LOOK_PROBLEM
        if self._problem == PROBLEM_STOPPED:
            return (f"{PHRASE} stopped: its listener kept failing. Turn it off and on in Settings > Talk to try again.",
                    LOOK_PROBLEM)
        if self._problem in (wake.ERR_MODEL, wake.ERR_ADDON):
            return self._missing_text(["wake_model" if self._problem == wake.ERR_MODEL else "runtime"]), LOOK_PROBLEM
        if self._holds:
            return f"{PHRASE} paused " + " and ".join(HOLD_TEXTS.get(hold, hold) for hold in self._holds), LOOK_PAUSED
        name, follows = self._mic
        where = "the Windows default" if follows else "picked in Settings > Meetings"
        state = self._listener_state
        if state == wake_listen.STATE_LISTENING:
            return f"Listening for {PHRASE}", LOOK_LISTENING
        if state == wake_listen.STATE_VIRTUAL:
            return (f"Not listening: “{name}” ({where}) is a virtual microphone, silent unless its app runs. "
                    "Pick your real one in Settings > Meetings > Microphone.", LOOK_PROBLEM)
        if state == wake_listen.STATE_NO_MIC:
            return "Not listening: no microphone found.", LOOK_PROBLEM
        if state == wake_listen.STATE_BUSY:
            return ("Not listening: the microphone wouldn't open (another app, or Windows' privacy settings); "
                    "trying again.", LOOK_PROBLEM)
        if state == wake_listen.STATE_SILENT:
            return f"Listening, but “{name}” gives only silence: is it muted?", LOOK_PROBLEM
        if state == wake_listen.STATE_NO_AUDIO:
            return "Not listening: the add-on PyAudioWPatch is missing (Settings > Talk shows how).", LOOK_PROBLEM
        if self._restart_timer.isActive():
            return f"{PHRASE}'s listener stopped; starting it again.", LOOK_PAUSED
        return f"{PHRASE} is starting...", LOOK_PAUSED

    def _missing_text(self, missing: list[str]) -> str:
        if "runtime" in missing:
            return f"{PHRASE} needs the add-on onnxruntime (it comes with faster-whisper: Settings > Talk shows how)."
        if "wake_model" in missing:
            return f"{PHRASE} needs the wake model ({wake.MODEL_SIZE_TEXT}): click Download in Settings > Talk."
        return f"{PHRASE} needs what talking needs first: Settings > Talk shows what is missing."

    def tray_text(self) -> str:
        """The tray tooltip's line ("" while off)."""
        text, look = self.status()
        if look == LOOK_OFF:
            return ""
        if look == LOOK_LISTENING:
            return f"Jarvis: listening for {PHRASE}"
        if look == LOOK_PAUSED:
            return f"Jarvis: {text}" if text.startswith(PHRASE) else f"Jarvis: {PHRASE} paused"
        return f"Jarvis: {PHRASE} can't hear you - see Settings > Talk"

    def look(self) -> str:
        return self.status()[1]

    def _changed(self) -> None:
        self._show_panel()
        self.changed.emit()

    # ---- Settings > Talk -----------------------------------------------------------------------------------------

    def attach_panel(self, panel: WakePanel, refresh: Callable[[], Any] | None = None) -> None:
        self._panel = panel
        self._refresh_settings = refresh or (lambda: None)
        panel.switchClicked.connect(self.set_on)
        panel.downloadClicked.connect(self.download)
        panel.sensitivityChosen.connect(self.set_sensitivity)
        self._show_panel()

    def _show_panel(self) -> None:
        if self._panel is not None:
            self._panel.show_state(on=self._store.on, status=self.status()[0], model_ready=wake.model_ready(self._folder),
                                   downloading=self._downloading, percent=self._download_percent,
                                   error=self._download_error, sensitivity=self._store.sensitivity)

    def settings_rows(self) -> list[tuple[str, str]]:
        ready = wake.model_ready(self._folder)
        model = ("on this PC" if ready else f"downloading, {self._download_percent}%" if self._downloading
                 else f"not downloaded ({wake.MODEL_SIZE_TEXT}; downloaded only when you click)")
        rows = [("Hey Jarvis", self._status_row()),
                ("Wake model", f"openWakeWord's “hey jarvis”: {model}"),
                ("Sensitivity", wake.sensitivity_of(self._store.sensitivity).label)]
        if self._store.on:
            counts = self._store.counts()
            rows.append(("Today", f"{counts[WakeStore.WAKES]} wake{'s' if counts[WakeStore.WAKES] != 1 else ''}, "
                                  f"{counts[WakeStore.NOTHING]} of them heard nothing"))
        return rows

    def _status_row(self) -> str:
        text, look = self.status()
        if look == LOOK_LISTENING and self._mic[0]:
            where = "the Windows default" if self._mic[1] else "picked in Settings > Meetings"
            return f"{text} on “{self._mic[0]}” ({where})"
        return text

    def download(self) -> None:
        """The owner clicked Download: the wake model's files come from GitHub, each checked."""
        if self._downloading or wake.model_ready(self._folder):
            return
        self._downloading, self._download_percent, self._download_error = True, 0, ""
        self._download_cancel.clear()
        logger.info("Wake: the owner clicked Download")

        def progress(done: int, total: int) -> None:
            if self._threaded:
                self._download_progress.emit(int(done), int(total))
            else:
                self._on_download_progress(int(done), int(total))

        def work() -> None:
            error = self._downloader_factory(self._models_root, progress, self._download_cancel).run()
            if self._threaded:
                self._downloaded.emit(error)
            else:
                self._on_downloaded(error)

        self._show_panel()
        if self._threaded:
            threading.Thread(target=work, name="wake-download", daemon=True).start()
        else:
            work()

    def _on_download_progress(self, done: int, total: int) -> None:
        percent = min(100, int(done * 100 / total)) if total > 0 else 0
        if percent != self._download_percent:
            self._download_percent = percent
            self._show_panel()

    def _on_downloaded(self, error: str) -> None:
        self._downloading = False
        self._download_error = error or ""
        logger.info("Wake: the model download ended (%s)", error or "ready")
        if not error and self._problem == wake.ERR_MODEL:
            self._problem = ""
        self._apply()
        self._refresh_settings()

    # ---- the app closing ---------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """The listener ends (its microphone closes); a download stops."""
        self._closing = True
        self._download_cancel.set()
        self._tail_timer.stop()
        self._restart_timer.stop()
        self._stop_client()
