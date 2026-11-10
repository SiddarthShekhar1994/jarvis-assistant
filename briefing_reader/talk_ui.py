"""Talking to Jarvis in the app (Qt): the talk key's toggle, what the owner sees and what was heard going in
(owner decisions 2026-11-09).

The app makes a :class:`TalkController` while [talk] and [ask] are on (AppController._create_talk); the talk key's
message ("talk": true) calls :meth:`TalkController.toggle`: listen when idle; while listening, end the talk now
(what was said goes in); while it is being understood, nothing. :meth:`cancel` (Esc, while listening) ends a talk
with nothing going in. Before the microphone opens, the add-ons and the speech model are checked: what is missing
is said on the bar with its fix, and nothing opens.

A talk: Jarvis comes forward in the mode he is in (``bring_forward``), stops talking and pauses a playing briefing
(``before_talk``); the microphone opens (talk.TalkSession: the one picked in Settings > Meetings, else the Windows
default); the orb listens (``set_look``) with the microphone's level (``set_level``) and the bar says so
(``show_status``). When it ends, the microphone closes at once; the audio (memory only) goes to the warm speech
process (talk_speech.SpeechClient) on a worker thread, and the words go in through ``submit``
(AskController.submit_spoken: the same routes and gates as typed words, so cards still need a click - voice never
approves or undoes). Logs hold kinds, counts and times only: never what was said.

Settings > Talk (:class:`TalkSettingsPanel`, :meth:`TalkController.settings_rows`): the key, the model and whether
it is downloaded (Download, only on the owner's click: meeting_speech.ModelDownloader into its own folder), the
add-ons and the command that installs them (the owner runs it; Jarvis never runs pip).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from . import hud, talk
from .hotkey import describe_combo

logger = logging.getLogger(__name__)

IDLE, LISTENING, UNDERSTANDING = "idle", "listening", "understanding"
POLL_MS = 30
ADDONS = ("faster-whisper==1.2.1", "PyAudioWPatch==0.2.12.9")   # the laptop's meetings versions (pinned)

SETTINGS_KEY = "talk"
SETTINGS_TITLE = "Talk"
SETTINGS_NOTE = ("Tap the talk key, say what you want, and Jarvis takes it after about half a second of quiet: tap "
                 "again to end sooner, Esc to cancel. He hears you only from the tap to the end, and shows that he is "
                 "listening. Your speech becomes text on this PC and the sound is never saved; what he heard goes in as "
                 "if you had typed it, so cards still need your click.")
SETTINGS_HOW = ("Turn it on with JARVIS_TALK_ENABLED=true in .env, then restart the \"Briefing hotkey\" task. The key, "
                "the model and the quiet that ends a talk are [talk] in config.toml.")

NOT_CAUGHT = "I didn't catch that."
MUTED = "I heard nothing at all: is your microphone muted?"
CANCELLED = "Cancelled."
DEVICE_STOPPED = "The microphone stopped; try again."
UNDERSTANDING_TEXT = "Understanding..."
NOT_UNDERSTOOD = "I couldn't make that out; try again."
MIC_TEXTS = {
    talk.ERR_NO_AUDIO_ADDON: "To talk to me, install the add-ons: Settings > Talk shows how.",
    talk.ERR_NO_MICROPHONE: "I can't find a microphone.",
    talk.ERR_MIC_BUSY: "Your microphone wouldn't open: another app may be using it, or Windows' privacy settings "
                       "block it.",
}


def pip_command(python: str | None = None) -> str:
    """The command that installs the add-ons, for the owner to run (python.exe, never pythonw.exe)."""
    path = Path(python or sys.executable)
    if path.name.lower() == "pythonw.exe":
        path = path.with_name("python.exe")
    text = str(path).replace("\\", "/")
    head = f'& "{text}"' if " " in text else text
    return f"{head} -m pip install {' '.join(ADDONS)}"


class TalkSettingsPanel(QWidget):
    """Settings > Talk's own part: the model's state with Download (only on a click) and the add-ons' command."""

    downloadClicked = Signal()

    def __init__(self, pip_line: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(hud.body_font(13))
        hud.set_label_color(self.status, hud.TEXT_SOFT)
        self.download = QPushButton("Download the speech model")
        self.download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download.clicked.connect(self.downloadClicked)
        self.addons = QLabel("The add-ons (you install them; Jarvis never does):")
        self.addons.setFont(hud.body_font(13))
        hud.set_label_color(self.addons, hud.TEXT_SOFT)
        self.pip = QLabel(pip_line)
        self.pip.setWordWrap(True)
        self.pip.setFont(hud.mono_font(11))
        self.pip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.status, self.download, self.addons, self.pip):
            layout.addWidget(widget)

    def show_state(self, *, ready: bool, downloading: bool, percent: int, error: str, size_text: str,
                   addons_missing: bool) -> None:
        if ready:
            text = "The speech model is on this PC."
        elif downloading:
            text = f"Downloading the speech model ({size_text}) from Hugging Face: {percent}%."
        elif error:
            text = f"The download stopped ({error}). Click Download to try again; it carries on where it stopped."
        else:
            text = f"The speech model ({size_text}) is downloaded only when you click Download."
        self.status.setText(text)
        self.download.setVisible(not ready)
        self.download.setEnabled(not downloading)
        self.download.setText(f"Downloading... {percent}%" if downloading else "Download the speech model")
        self.addons.setVisible(addons_missing)
        self.pip.setVisible(addons_missing)


class TalkController(QObject):
    """See the module docstring. ``threaded`` False (tests): understanding and downloads run in the caller."""

    stateChanged = Signal(str)
    _heard = Signal(object, int)
    _download_progress = Signal(int, int)
    _downloaded = Signal(str)

    def __init__(self, config: Any, *, bring_forward: Callable[[], Any], before_talk: Callable[[], Any],
                 show_status: Callable[..., Any], submit: Callable[[str], Any],
                 set_look: Callable[[str], Any] | None = None, set_level: Callable[[float | None], Any] | None = None,
                 parent: QObject | None = None, models_root: Path | None = None,
                 picked_mic: Callable[[], str] | None = None, backend_loader: Callable[[], tuple[Any, str]] | None = None,
                 client_factory: Callable[[Path, str, float], Any] | None = None,
                 audio_missing: Callable[[], str] | None = None, speech_missing: Callable[[], str] | None = None,
                 downloader_factory: Callable[..., Any] | None = None, threaded: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        from . import meeting_audio, meeting_speech, meetings

        self._cfg = config.talk
        self._model = meeting_speech.SPEECH_MODELS[self._cfg.model]
        self._models_root = Path(models_root) if models_root is not None else meetings.models_root(config)
        self._folder = meeting_speech.model_dir(self._models_root, self._model)
        self._model_ready = lambda: meeting_speech.model_ready(self._folder, self._model)
        self._bring_forward, self._before_talk, self._show_status, self._submit = (bring_forward, before_talk,
                                                                                    show_status, submit)
        self._set_look = set_look or (lambda _state: None)
        self._set_level = set_level or (lambda _level: None)
        self._picked_mic = picked_mic or (lambda: meetings.DevicePrefs(meetings.meetings_root(config)).microphone)
        self._backend_loader = backend_loader or meeting_audio.load_backend
        self._client_factory = client_factory or self._new_client
        self._audio_missing = audio_missing or meeting_audio.backend_missing
        self._speech_missing = speech_missing or meeting_speech.speech_missing
        self._downloader_factory = downloader_factory or (
            lambda root, model, progress, cancel: meeting_speech.ModelDownloader.for_model(
                root, model, progress=progress, cancel=cancel))
        self._threaded = threaded
        self._clock = clock
        self._key = describe_combo(self._cfg.key) if self._cfg.key else "the talk key"
        self._state = IDLE
        self._session: talk.TalkSession | None = None
        self._backend: Any = None
        self._client: Any = None
        self._began = 0.0
        self._watching_escape = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._heard.connect(self._on_heard, Qt.ConnectionType.QueuedConnection)
        self._download_progress.connect(self._on_download_progress, Qt.ConnectionType.QueuedConnection)
        self._downloaded.connect(self._on_downloaded, Qt.ConnectionType.QueuedConnection)
        self._downloading = False
        self._download_percent = 0
        self._download_error = ""
        self._download_cancel = threading.Event()
        self._panel: TalkSettingsPanel | None = None
        self._refresh_settings: Callable[[], Any] = lambda: None
        self._closing = False

    # ---- state ----------------------------------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self._set_look(state)
            self.stateChanged.emit(state)

    def _show(self, text: str, tone: str, hud_text: str | None = None) -> None:
        if not self._closing:
            self._show_status(text, tone, hud_text=hud_text)

    def missing(self) -> list[str]:
        """What a talk needs that is not here: "audio_addon", "speech_addon", "model" (in that order)."""
        pieces = []
        if self._audio_missing():
            pieces.append("audio_addon")
        if self._speech_missing():
            pieces.append("speech_addon")
        if not self._model_ready():
            pieces.append("model")
        return pieces

    def _missing_text(self, missing: list[str]) -> str:
        if "audio_addon" in missing or "speech_addon" in missing:
            return "To talk to me, install the add-ons faster-whisper and PyAudioWPatch: Settings > Talk shows how."
        return f"To talk to me, download the speech model ({self._model.size_text}) in Settings > Talk."

    # ---- a talk ---------------------------------------------------------------------------------------------

    def toggle(self) -> None:
        if self._state == LISTENING:
            self._finish(talk.END_OWNER)
            return
        if self._state == UNDERSTANDING:
            logger.info("Talk: the talk key while understanding; nothing")
            return
        self._listen()

    def cancel(self) -> bool:
        """Esc or Stop while listening: the talk ends with nothing going in. True when there was one."""
        if self._state != LISTENING:
            return False
        self._finish(talk.END_CANCEL)
        return True

    def _listen(self) -> None:
        self._bring_forward()
        missing = self.missing()
        if missing:
            logger.info("Talk: not ready (%s)", ", ".join(missing))
            self._show(self._missing_text(missing), hud.TONE_WARN)
            return
        self._before_talk()
        backend, _missing = self._backend_loader()
        try:
            session = talk.TalkSession.start(backend, self._picked_mic(), quiet_ms=self._cfg.quiet_ms,
                                             clock=self._clock)
        except talk.TalkError as exc:
            self._close_backend(backend)
            logger.info("Talk: the microphone did not open (%s)", exc.code)
            self._show(MIC_TEXTS.get(exc.code, MIC_TEXTS[talk.ERR_MIC_BUSY]), hud.TONE_WARN)
            return
        self._backend, self._session = backend, session
        self._began = self._clock()
        self._set_state(LISTENING)
        self._show(f"Listening... tap {self._key} again when you're done, or Esc to cancel.", hud.TONE_WORKING,
                   hud_text="Listening...")
        self._watch_escape(True)
        self._timer.start()
        logger.info("Talk: listening")

    def _poll(self) -> None:
        session = self._session
        if session is None:
            self._timer.stop()
            return
        reason = session.poll()
        self._set_level(session.level)
        if reason:
            self._finish(reason)

    def _finish(self, reason: str) -> None:
        session, self._session = self._session, None
        self._timer.stop()
        self._watch_escape(False)
        self._set_level(None)
        if session is None:
            return
        if not session.ended:
            session.end(reason)
        backend, self._backend = self._backend, None
        self._close_backend(backend)
        clip, ended, muted = session.clip(), session.ended, session.muted
        speech_ms = session.endpointer.speech_frames * talk.FRAME_MS
        session.forget()
        logger.info("Talk: ended (%s) after %d ms, %d ms of speech", ended,
                    int((self._clock() - self._began) * 1000), speech_ms)
        if not clip:
            self._set_state(IDLE)
            if ended == talk.END_CANCEL:
                self._show(CANCELLED, hud.TONE_IDLE)
            elif ended == talk.END_DEVICE:
                self._show(DEVICE_STOPPED, hud.TONE_WARN)
            else:
                self._show(MUTED if muted else NOT_CAUGHT, hud.TONE_WARN)
            return
        self._set_state(UNDERSTANDING)
        self._show(UNDERSTANDING_TEXT, hud.TONE_WORKING, hud_text="One moment...")
        self._understand(clip)

    def _new_client(self, folder: Path, hints: str, idle_s: float) -> Any:
        from .talk_speech import SpeechClient

        return SpeechClient(folder, hints=hints, idle_s=idle_s)

    def _understand(self, clip: bytes) -> None:
        if self._client is None:
            self._client = self._client_factory(self._folder, talk.hint_prompt(self._cfg.hints),
                                                float(self._cfg.keep_warm_minutes * 60))
        client = self._client

        def work() -> None:
            began = time.monotonic()
            heard = client.understand(clip)
            took = int((time.monotonic() - began) * 1000)
            if self._threaded:
                self._heard.emit(heard, took)
            else:
                self._on_heard(heard, took)

        if self._threaded:
            threading.Thread(target=work, name="talk-understand", daemon=True).start()
        else:
            work()

    def _on_heard(self, heard: Any, took_ms: int) -> None:
        if self._state != UNDERSTANDING:
            return                                  # shut down meanwhile
        self._set_state(IDLE)
        text = str(getattr(heard, "text", "") or "").strip()
        logger.info("Talk: understood in %d ms (model load %d ms): %s, %d characters", took_ms,
                    int(getattr(heard, "load_ms", 0) or 0), "ok" if heard.ok else heard.error, len(text))
        if not heard.ok:
            from . import talk_speech

            if heard.error in (talk_speech.ERR_ADDON, talk_speech.ERR_MODEL):
                self._show(self._missing_text(["speech_addon" if heard.error == talk_speech.ERR_ADDON else "model"]),
                           hud.TONE_WARN)
            else:
                self._show(NOT_UNDERSTOOD, hud.TONE_WARN)
            return
        if not text:
            self._show(NOT_CAUGHT, hud.TONE_WARN)
            return
        self._submit(text)

    def _close_backend(self, backend: Any) -> None:
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - a device library that is already gone
                pass

    # ---- Esc ------------------------------------------------------------------------------------------------

    def _watch_escape(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None or on == self._watching_escape:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._watching_escape = on

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if (event.type() == QEvent.Type.KeyPress and getattr(event, "key", lambda: None)() == Qt.Key.Key_Escape
                and self._state == LISTENING):
            self.cancel()
            return True
        return False

    # ---- Settings > Talk ------------------------------------------------------------------------------------

    def attach_panel(self, panel: TalkSettingsPanel, refresh: Callable[[], Any] | None = None) -> None:
        self._panel = panel
        self._refresh_settings = refresh or (lambda: None)
        panel.downloadClicked.connect(self.download)
        self._show_panel()

    def settings_rows(self) -> list[tuple[str, str]]:
        missing = [name for name in (self._audio_missing(), self._speech_missing()) if name]
        ready = self._model_ready()
        model = f"{self._model.name} ({self._model.size_text}): " + (
            "on this PC" if ready else f"downloading, {self._download_percent}%" if self._downloading
            else "not downloaded yet")
        return [("Talk key", self._key if self._cfg.key else "None"),
                ("Speech model", model),
                ("Add-ons", "installed" if not missing else "missing: " + ", ".join(missing)),
                ("Microphone", "the one picked in Settings > Meetings, else the Windows default"),
                ("Quiet that ends a talk", f"{self._cfg.quiet_ms} ms"),
                ("Model kept loaded", f"{self._cfg.keep_warm_minutes} min after a talk")]

    def _show_panel(self) -> None:
        if self._panel is not None:
            self._panel.show_state(ready=self._model_ready(), downloading=self._downloading,
                                   percent=self._download_percent, error=self._download_error,
                                   size_text=self._model.size_text,
                                   addons_missing=bool(self._audio_missing() or self._speech_missing()))

    def download(self) -> None:
        """The owner clicked Download: the model comes from Hugging Face into its own folder (checked, resumable)."""
        if self._downloading or self._model_ready():
            return
        self._downloading, self._download_percent, self._download_error = True, 0, ""
        self._download_cancel.clear()
        logger.info("Talk: the owner clicked Download (%s)", self._model.name)

        def progress(done: int, total: int) -> None:
            if self._threaded:
                self._download_progress.emit(int(done), int(total))
            else:
                self._on_download_progress(int(done), int(total))

        def work() -> None:
            downloader = self._downloader_factory(self._models_root, self._model, progress, self._download_cancel)
            error = downloader.run()
            if self._threaded:
                self._downloaded.emit(error)
            else:
                self._on_downloaded(error)

        self._show_panel()
        if self._threaded:
            threading.Thread(target=work, name="talk-download", daemon=True).start()
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
        logger.info("Talk: the model download ended (%s)", error or "ready")
        self._show_panel()
        self._refresh_settings()

    # ---- the app closing ------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """The microphone closes (a talk under way is cancelled), a download stops and the speech process ends."""
        self._closing = True
        self._download_cancel.set()
        if self._session is not None:
            self._finish(talk.END_CANCEL)
        self._state = IDLE
        client, self._client = self._client, None
        if client is not None:
            threading.Thread(target=client.close, name="talk-speech-close", daemon=True).start()
