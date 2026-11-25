"""The voiceprint in the app (Qt): Settings > Talk's "Recognise my voice", the recording of the owner's voice and the
check of each "Hey Jarvis" (owner decisions 2026-11-24; the maths is voiceprint.py's).

The app makes a :class:`VoiceprintController` beside "Hey Jarvis" (AppController._create_wake) and hands it to the
WakeController, which asks it about each wake (``will_check``, ``check``). Its part of Settings > Talk
(:class:`VoicePanel`) sits under the "Hey Jarvis" part:

- no speaker model: Download (only on the owner's click; its size and its licence credit shown);
- the model here, no voice recorded: Record my voice;
- a voice recorded: Record again, Forget my voice and the switch Only my voice wakes Jarvis (on after a recording:
  ``[wake] only_my_voice``, true by default; the switch saves the owner's choice in settings.json, at once).

Recording: TAKES takes of the owner saying "Hey Jarvis", each through the talk's own microphone path
(talk.TalkSession on the microphone picked in Settings > Talk, else the Windows default), the audio in memory only.
"Hey Jarvis"'s listener is paused meanwhile (``recording``; the app's holds). A take that is too short, too quiet or
silent is asked again (at most MAX_TRIES in all). When the takes do not agree (any two below voiceprint.AGREE_MIN),
nothing is kept; else the template is saved (voiceprint.TemplateStore). Cancel stops it and keeps nothing.

Each take is kept as a wake is heard: the last voiceprint.WAKE_CLIP_S ending where its speech ended. A talk starting,
or Jarvis starting to speak, stops a recording at once (nothing is kept). The template is saved on the Qt thread, only
for the recording still under way: a Cancel (or the app closing) while it learns keeps nothing.

A check: the wake clip is embedded on a worker thread, never on the Qt thread; the verdict comes back on the Qt thread
(``done``), or after VERDICT_TIMEOUT_MS as unchecked ("slow") - the wake then goes through, and a verdict that comes
later is let go. Anything that keeps the check from running lets the wake through, and Settings says why the check is
off. The embedding time is logged once per wake. A speaker model that will not load is offered for Download again
(its folder is deleted first).

It never approves anything: it only keeps a "Hey Jarvis" from another voice out.

Privacy: no audio is ever written; the template holds numbers only. Logs hold counts, durations, statuses, scores
rounded to 2 places and reason words - never audio, embeddings or names.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import hud, talk, voiceprint

logger = logging.getLogger(__name__)

SWITCH_KEY = "wake.only_my_voice"
MAX_TRIES = 6                       # takes asked again at most this many times in one recording
VERDICT_TIMEOUT_MS = 1500           # a check slower than this: unchecked ("slow"), the wake goes through
WHY_SLOW = "slow"
TAKE_GAP_MS = 700                   # a breath between takes
PHRASE = "“Hey Jarvis”"
TITLE = "Recognise my voice"
SWITCH_TEXT = "Only my voice wakes Jarvis"
KEEPS_NOTE = "Jarvis keeps a voice template on this PC (numbers, not a recording). Forget deletes it."
SCOPE_NOTE = (f"Once your voice is recorded, a {PHRASE} in another voice is let go. It checks {PHRASE} only - the "
              "talk key is never checked - and it never approves anything.")

STATE_NO_MODEL, STATE_READY, STATE_ENROLLED = "no_model", "ready", "enrolled"
STATE_BROKEN = "broken"             # downloaded, but it would not load: Download again
BUSY_TALK, BUSY_SPEAKING = "talk", "speaking"     # what stops a recording (the app's ``busy``)
_BUSY_TEXTS = {BUSY_TALK: "a talk started", BUSY_SPEAKING: "Jarvis started speaking"}
OFF_SWITCH = "switch_off"           # (a reason the check is off: the owner turned the switch off)

_TAKE_TEXTS = {
    voiceprint.TAKE_SILENT: "Nothing came from the microphone (is it muted?).",
    voiceprint.TAKE_QUIET: "I didn't hear that.",
    voiceprint.TAKE_SHORT: "That was too short.",
    voiceprint.TAKE_LONG: "That was too long: just the two words.",
}
_OFF_TEXTS = {
    voiceprint.WHY_MODEL: "the speaker model isn't downloaded",
    voiceprint.WHY_RUNTIME: "it needs the add-on onnxruntime (it comes with faster-whisper: Settings > Talk shows how)",
    voiceprint.WHY_FAILED: "the speaker model wouldn't load",
    voiceprint.WHY_NO_TEMPLATE: "your voice isn't recorded yet",
    OFF_SWITCH: "you turned Only my voice off",
}


def _address(config: Any) -> str:
    return str(getattr(getattr(config, "assistant", None), "address", "") or "sir")


# --------------------------------------------------------------------------
# Settings > Talk: Recognise my voice
# --------------------------------------------------------------------------

class VoicePanel(QWidget):
    """Settings > Talk's "Recognise my voice" part: what it does now, the buttons of its state and what is kept."""

    downloadClicked = Signal()
    recordClicked = Signal()
    cancelClicked = Signal()
    forgetClicked = Signal()
    switchClicked = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .wake_ui import CHECKED_STYLE as checked

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(8)
        self.title = QLabel(TITLE)
        self.title.setFont(hud.body_font(14, 600))
        hud.set_label_color(self.title, hud.TEXT_BODY)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(hud.body_font(13))
        hud.set_label_color(self.status, hud.TEXT_SOFT)
        self.scope = QLabel(SCOPE_NOTE)
        self.scope.setWordWrap(True)
        self.scope.setFont(hud.body_font(13))
        hud.set_label_color(self.scope, hud.TEXT_SOFT)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.download = QPushButton(f"Download the speaker model ({voiceprint.MODEL_SIZE_TEXT})")
        self.record = QPushButton("Record my voice")
        self.cancel = QPushButton("Cancel")
        self.forget = QPushButton("Forget my voice")
        self.switch = QPushButton(SWITCH_TEXT)
        self.switch.setCheckable(True)
        self.switch.setStyleSheet(checked)          # on stands out (the theme leaves :checked plain)
        for button in (self.download, self.record, self.cancel, self.forget, self.switch):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch(1)
        self.download.clicked.connect(self.downloadClicked)
        self.record.clicked.connect(self.recordClicked)
        self.cancel.clicked.connect(self.cancelClicked)
        self.forget.clicked.connect(self.forgetClicked)
        self.switch.clicked.connect(self._on_switch)
        self.keeps = QLabel(KEEPS_NOTE)
        self.keeps.setWordWrap(True)
        self.keeps.setFont(hud.body_font(12))
        hud.set_label_color(self.keeps, hud.TEXT_SOFT)
        self.credit = QLabel(f"{voiceprint.CREDIT}. Downloaded ({voiceprint.MODEL_SIZE_TEXT}) only when you click.")
        self.credit.setWordWrap(True)
        self.credit.setFont(hud.body_font(12))
        hud.set_label_color(self.credit, hud.TEXT_SOFT)
        for widget in (self.title, self.status, self.scope):
            layout.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self.keeps)
        layout.addWidget(self.credit)
        self._switch_on = False

    def _on_switch(self) -> None:
        self.switchClicked.emit(not self._switch_on)

    def show_state(self, *, state: str, status: str, downloading: bool, percent: int, recording: bool,
                   can_record: bool, switch_on: bool) -> None:
        self._switch_on = bool(switch_on)
        self.status.setText(status)
        self.download.setVisible(state in (STATE_NO_MODEL, STATE_BROKEN))
        self.download.setEnabled(not downloading)
        again = " again" if state == STATE_BROKEN else ""
        self.download.setText(f"Downloading... {percent}%" if downloading
                              else f"Download the speaker model{again} ({voiceprint.MODEL_SIZE_TEXT})")
        self.record.setVisible(state not in (STATE_NO_MODEL, STATE_BROKEN) and not recording and can_record)
        self.record.setText("Record again" if state == STATE_ENROLLED else "Record my voice")
        self.cancel.setVisible(recording)
        self.forget.setVisible(state == STATE_ENROLLED and not recording)
        self.switch.setVisible(state == STATE_ENROLLED and not recording)
        self.switch.setChecked(self._switch_on)
        self.switch.setText(f"{SWITCH_TEXT}: {'on' if self._switch_on else 'off'}")
        self.keeps.setVisible(state != STATE_NO_MODEL)


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------

class VoiceprintController(QObject):
    """See the module docstring. ``threaded`` False (tests): checks, the takes' embedding and downloads run in the
    caller. ``session_loader(folder)`` makes the model's session (tests: a fake onnxruntime session)."""

    changed = Signal()                     # what Settings shows changed
    recordingChanged = Signal(bool)        # a recording started / ended ("Hey Jarvis" pauses meanwhile)
    _verdict = Signal(object, object, object)  # (the check, Verdict, embedding ms or None), to the Qt thread
    _enrolled = Signal(object, str, object)  # (the recording's mark, outcome, template)
    _download_progress = Signal(int, int)
    _downloaded = Signal(str)

    def __init__(self, config: Any, *, data_dir: Path | None = None, models_root: Path | None = None,
                 store: voiceprint.TemplateStore | None = None, settings: Any = None,
                 session_loader: Callable[[Path], Any] | None = None, runtime_missing: Callable[[], str] | None = None,
                 downloader_factory: Callable[..., Any] | None = None,
                 backend_loader: Callable[[], tuple[Any, str]] | None = None,
                 picked_mic: Callable[[], str] | None = None, busy: Callable[[], str] = lambda: "",
                 threaded: bool = True, clock: Callable[[], float] = time.monotonic,
                 start_work: Callable[[Callable[[], Any]], Any] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        from . import meetings, overrides

        folder = Path(data_dir) if data_dir is not None else Path(config.data_dir)
        self._models_root = Path(models_root) if models_root is not None else meetings.models_root(config)
        self._folder = voiceprint.model_dir(self._models_root)
        self._store = store or voiceprint.TemplateStore(folder)
        self._settings = settings if settings is not None else overrides.SettingsStore(overrides.settings_file(folder))
        self._session_loader = session_loader or (lambda where: voiceprint.load_session(where))
        self._runtime_missing = runtime_missing or voiceprint.runtime_missing
        self._downloader_factory = downloader_factory or (
            lambda root, progress, cancel: voiceprint.VoiceprintModelDownloader(root, progress=progress, cancel=cancel))
        self._backend_loader = backend_loader
        self._picked_mic = picked_mic or (lambda: meetings.DevicePrefs(meetings.meetings_root(config)).microphone)
        self._busy = busy                  # BUSY_TALK / BUSY_SPEAKING / "": a talk or his voice stops a recording
        self._threaded = threaded
        # How a check's work starts (tests: held, to time it out): a daemon thread when threaded, else in place.
        self._start_work = start_work or (
            (lambda work: threading.Thread(target=work, name="voiceprint-check", daemon=True).start())
            if threaded else (lambda work: work()))
        self._clock = clock
        self._address = _address(config)
        wake_cfg = getattr(config, "wake", None)
        self._default_on = bool(getattr(wake_cfg, "only_my_voice", True))
        toml_values = getattr(config, "toml_values", {}) or {}
        if SWITCH_KEY in (getattr(config, "overridden", ()) or ()) and isinstance(toml_values.get(SWITCH_KEY), bool):
            self._toml_on = bool(toml_values[SWITCH_KEY])   # what holds again after a recording: config.toml's
        else:
            self._toml_on = self._default_on
        self._only_mine = self._default_on
        self._template, why = self._store.load()
        self._load_problem = "" if self._template is not None or why == voiceprint.WHY_NO_TEMPLATE else why
        self._lock = threading.Lock()
        self._embedder: Any = None
        self._broken = ""                  # the model would not load (a WHY_* word): checks are off until a restart
        self._last_unchecked = ""          # the last wake that went unchecked, and why (Settings)
        self._short = 0                    # wakes too short to check, this run (Settings)
        self._too_long = False             # the take under way was ended for going on too long
        self._message = ""                 # what the recording (or Forget) last said
        # the recording
        self._recording: object | None = None
        self._session: talk.TalkSession | None = None
        self._backend: Any = None
        self._takes: list[bytes] = []
        self._tries = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        # The check under way's timeout: one timer of its own, on a bound method (a timer made per check, with a
        # closure holding this controller, could be the last thing keeping it alive while that timer is deleted)
        self._pending: dict | None = None
        self._verdict_timer = QTimer(self)
        self._verdict_timer.setSingleShot(True)
        self._verdict_timer.timeout.connect(self._time_out)
        self._gap = QTimer(self)
        self._gap.setSingleShot(True)
        self._gap.setInterval(TAKE_GAP_MS)
        self._gap.timeout.connect(self._open_take)
        # the download
        self._downloading = False
        self._download_percent = 0
        self._download_error = ""
        self._download_cancel = threading.Event()
        self._panel: VoicePanel | None = None
        self._refresh_settings: Callable[[], Any] = lambda: None
        self._closing = False
        self._verdict.connect(self._on_verdict, Qt.ConnectionType.QueuedConnection)
        self._enrolled.connect(self._on_enrolled, Qt.ConnectionType.QueuedConnection)
        self._download_progress.connect(self._on_download_progress, Qt.ConnectionType.QueuedConnection)
        self._downloaded.connect(self._on_downloaded, Qt.ConnectionType.QueuedConnection)

    # ---- what it is -------------------------------------------------------------------------------------------

    def model_ready(self) -> bool:
        return voiceprint.model_ready(self._folder)

    def enrolled(self) -> bool:
        return self._template is not None

    def only_mine(self) -> bool:
        return self._only_mine

    def recording(self) -> bool:
        return self._recording is not None

    def state(self) -> str:
        if not self.model_ready():
            return STATE_NO_MODEL
        if self._broken == voiceprint.WHY_FAILED:
            return STATE_BROKEN
        return STATE_ENROLLED if self.enrolled() else STATE_READY

    def off_reason(self) -> str:
        """"" while each "Hey Jarvis" is checked, else why not (a WHY_* word, or OFF_SWITCH)."""
        if self._template is not None and not self._only_mine:
            return OFF_SWITCH
        if self._runtime_missing():
            return voiceprint.WHY_RUNTIME
        if not self.model_ready():
            return voiceprint.WHY_MODEL
        if self._broken:
            return self._broken
        if self._template is None:
            return voiceprint.WHY_NO_TEMPLATE
        return ""

    def will_check(self) -> bool:
        """Each "Hey Jarvis" is checked now: the switch is on, a voice is recorded and the model can run."""
        return not self.off_reason()

    # ---- a check (WakeController) -------------------------------------------------------------------------------

    def _get_embedder(self) -> Any:
        with self._lock:
            if self._embedder is None:
                self._embedder = voiceprint.Embedder(self._session_loader(self._folder))
            return self._embedder

    def warm(self) -> None:
        """Loads the model ahead of the first wake (on a worker thread) while checks are on."""
        if not self.will_check() or not self._threaded:
            return
        threading.Thread(target=self._warm, name="voiceprint-warm", daemon=True).start()

    def _warm(self) -> None:
        try:
            self._get_embedder()
        except voiceprint.VoiceprintError as exc:
            self._broken = self._why(exc.code)
            logger.info("Voiceprint: the speaker model did not load (%s)", self._broken)
        except Exception as exc:  # noqa: BLE001 - reported by its kind only
            self._broken = voiceprint.WHY_FAILED
            logger.info("Voiceprint: the speaker model did not load (%s)", type(exc).__name__)

    @staticmethod
    def _why(code: str) -> str:
        return {voiceprint.ERR_ADDON: voiceprint.WHY_RUNTIME, voiceprint.ERR_MODEL: voiceprint.WHY_MODEL}.get(
            code, voiceprint.WHY_FAILED)

    def check(self, pcm: bytes, done: Callable[[voiceprint.Verdict], Any]) -> None:
        """The wake clip ``pcm`` against the template, on a worker thread; ``done(verdict)`` on the Qt thread."""
        template = self._template
        job: dict[str, Any] = {"done": done, "answered": False}
        if not self.will_check() or template is None:
            self._on_verdict(job, voiceprint.unchecked(self.off_reason() or voiceprint.WHY_NO_TEMPLATE), None)
            return
        clip = bytes(pcm)
        if self._pending is not None:
            self._time_out()                   # a newer wake: the older one is answered (its stream was let go)
        self._pending = job
        self._verdict_timer.start(VERDICT_TIMEOUT_MS)

        def work() -> None:
            try:
                embedder = self._get_embedder()
            except voiceprint.VoiceprintError as exc:      # only a model that will not load breaks the check
                self._broken = self._why(exc.code)
                verdict, took = voiceprint.unchecked(self._broken), None
            except Exception:  # noqa: BLE001 - a model that will not load lets the wake through
                self._broken = voiceprint.WHY_FAILED
                verdict, took = voiceprint.unchecked(self._broken), None
            else:
                verdict, took = voiceprint.check(clip, template, embedder)   # one failed run: unchecked, not broken
            if self._threaded:
                self._verdict.emit(job, verdict, took)
            else:
                self._on_verdict(job, verdict, took)

        self._start_work(work)

    def _time_out(self) -> None:
        """VERDICT_TIMEOUT_MS passed with no verdict: unchecked ("slow"); the wake goes through."""
        job = self._pending
        if job is not None:
            self._on_verdict(job, voiceprint.unchecked(WHY_SLOW), None)

    def _on_verdict(self, job: dict, verdict: voiceprint.Verdict, took: int | None) -> None:
        """A check's answer on the Qt thread: the first of its verdict and its timeout counts; the other is let go."""
        if job.get("answered"):
            logger.info("Voiceprint: a late verdict was let go")
            return
        job["answered"] = True
        if self._pending is job:
            self._pending = None
            self._verdict_timer.stop()
        if took is not None:                              # an embedding was made (a fast one is 0 ms)
            logger.info("Voiceprint: embedded in %d ms", int(took))
        if verdict.kind == voiceprint.UNCHECKED:
            logger.info("Voiceprint: unchecked (%s)", verdict.reason)
            self._last_unchecked = verdict.reason
            if verdict.reason == voiceprint.WHY_SHORT:
                self._short += 1
        elif verdict.kind == voiceprint.MATCH:
            self._last_unchecked = ""
        try:
            job["done"](verdict)
        finally:
            self._changed()

    # ---- the switch and Forget -------------------------------------------------------------------------------------

    def set_only_mine(self, on: bool) -> None:
        """The owner's switch: saved in settings.json and used at once. Turning it off when that could not be saved
        is undone (it stays on); turning it on counts for this run even unsaved."""
        on = bool(on)
        if on == self._only_mine:
            return
        try:
            saved = bool(self._settings.update({SWITCH_KEY: on}))
        except (OSError, ValueError):
            saved = False
        if saved or on:
            self._only_mine = on
        self._message = "" if saved else "That couldn't be saved" + ("; it stays on." if not on else " (this run only).")
        logger.info("Voiceprint: only my voice turned %s%s", "on" if self._only_mine else "off",
                    "" if saved else " (could not be saved)")
        self._changed()
        self._refresh_settings()

    def forget(self) -> None:
        """Forget my voice: the template file is deleted (checks stop)."""
        if self.recording():
            return
        if self._store.forget():
            self._template = None
            self._load_problem = ""
            self._last_unchecked = ""
            self._message = "Forgotten: the voice template is deleted from this PC."
            logger.info("Voiceprint: forgotten")
        else:
            self._message = "The voice template couldn't be deleted. Try Forget again."
        self._changed()
        self._refresh_settings()

    # ---- the recording -----------------------------------------------------------------------------------------

    def can_record(self) -> bool:
        return (self.model_ready() and not self._runtime_missing() and not self._broken and not self.recording()
                and not self._closing)

    def _busy_now(self) -> str:
        try:
            return str(self._busy() or "")
        except Exception:  # noqa: BLE001 - unknown: as a talk (nothing is recorded over it)
            return BUSY_TALK

    def start_recording(self) -> None:
        """Record my voice / Record again: TAKES takes of "Hey Jarvis" (the listener pauses meanwhile)."""
        if not self.can_record():
            return
        busy = self._busy_now()
        if busy:
            self._message = ("Finish the talk first, then record your voice." if busy == BUSY_TALK
                             else "Wait until Jarvis stops speaking, then record your voice.")
            self._changed()
            return
        self._recording = object()
        self._takes, self._tries = [], 0
        logger.info("Voiceprint: recording started")
        self.recordingChanged.emit(True)
        self._open_take()

    def _say(self, text: str) -> None:
        self._message = text
        self._changed()

    def _ask_text(self, lead: str = "") -> str:
        take = len(self._takes) + 1
        ask = f"Say {PHRASE}, {self._address} ({take} of {voiceprint.TAKES})."
        return f"{lead} {ask}" if lead else ask

    def _open_take(self) -> None:
        if self._recording is None or self._closing:
            return
        if self._interrupted():
            return
        self._too_long = False
        loader = self._backend_loader
        if loader is None:
            from .meeting_audio import load_backend as loader
        backend = None
        try:
            backend, _missing = loader()
            session = talk.TalkSession.start(backend, self._picked(), clock=self._clock)
        except talk.TalkError as exc:
            self._close_backend(backend)
            logger.info("Voiceprint: the microphone did not open (%s)", exc.code)
            self._stop("The microphone didn't open, so nothing was kept. Check Settings > Talk > Microphone.")
            return
        except Exception as exc:  # noqa: BLE001 - the microphone never stays open behind a failure
            self._close_backend(backend)
            logger.info("Voiceprint: the microphone did not open (%s)", type(exc).__name__)
            self._stop("The microphone didn't open, so nothing was kept. Check Settings > Talk > Microphone.")
            return
        self._session, self._backend = session, backend
        if not self._takes and not self._tries:
            self._say(self._ask_text())                    # the first take (the later ones were asked already)
        self._timer.start()

    def _picked(self) -> str:
        try:
            return str(self._picked_mic() or "")
        except Exception:  # noqa: BLE001 - the Windows default then
            return ""

    def _interrupted(self) -> bool:
        """A talk started, or Jarvis started speaking: the recording stops at once (as a hold), nothing kept."""
        busy = self._busy_now()
        if not busy:
            return False
        logger.info("Voiceprint: recording stopped (%s)", busy)
        what = _BUSY_TEXTS.get(busy, _BUSY_TEXTS[BUSY_TALK])
        self._stop(f"Stopped: {what}, so nothing was kept. Press Record my voice to try again, {self._address}.")
        return True

    def _poll(self) -> None:
        session = self._session
        if session is None:
            self._timer.stop()
            return
        if self._interrupted():
            return
        try:
            reason = session.poll()
            ep = session.endpointer
            if not reason and ep.heard_speech and (ep.frames - ep.start_frame) * talk.FRAME_MS > \
                    voiceprint.TAKE_MAX_S * 1000:
                self._too_long = True
                reason = session.end(talk.END_OWNER)    # still talking: that take is too long
        except Exception as exc:  # noqa: BLE001 - a failing read closes the microphone
            logger.info("Voiceprint: reading the microphone failed (%s)", type(exc).__name__)
            reason = session.end(talk.END_DEVICE)
        if reason:
            self._end_take(reason)

    def _end_take(self, reason: str) -> None:
        self._timer.stop()
        session, self._session = self._session, None
        backend, self._backend = self._backend, None
        self._close_backend(backend)
        if session is None:
            return
        if reason == talk.END_DEVICE:
            session.forget()
            self._stop("The microphone stopped, so nothing was kept.")
            return
        clip = session.clip()
        muted = session.muted
        ep = session.endpointer
        if clip and ep.heard_speech:
            first, _last = ep.span()                       # where the clip begins in the take's audio
            clip = voiceprint.take_window(clip, (ep.last_speech_frame + 1) * talk.FRAME_BYTES - first)
        session.forget()                                   # the audio is now only in ``clip``
        if muted:
            problem = voiceprint.TAKE_SILENT
        elif not clip:
            problem = voiceprint.TAKE_QUIET
        elif self._too_long:
            problem = voiceprint.TAKE_LONG
        else:
            problem = voiceprint.take_problem(clip)
        logger.info("Voiceprint: take %d of %d (%s)", len(self._takes) + 1, voiceprint.TAKES, problem or "ok")
        if problem:
            self._tries += 1
            if self._tries >= MAX_TRIES:
                self._stop(f"I couldn't hear a clear {PHRASE}, so nothing was kept. Check the microphone in Settings > "
                           "Talk, then try again.")
                return
            self._message = _TAKE_TEXTS[problem]
            self._say(self._ask_text(self._message))
            self._gap.start()
            return
        self._takes.append(clip)
        if len(self._takes) < voiceprint.TAKES:
            self._message = "Got it."
            self._say(self._ask_text(self._message))
            self._gap.start()
            return
        self._learn()

    def _learn(self) -> None:
        mark, takes = self._recording, list(self._takes)
        self._takes = []
        self._say("Learning your voice...")

        def work() -> None:
            """Only the maths, off the Qt thread: the template is saved by _on_enrolled (never after a Cancel)."""
            template = None
            embedder = None
            try:
                embedder = self._get_embedder()
            except voiceprint.VoiceprintError as exc:      # only a model that will not load breaks the check
                self._broken = self._why(exc.code)
            except Exception:  # noqa: BLE001 - reported by its outcome only
                self._broken = voiceprint.WHY_FAILED
            outcome = "failed"
            if embedder is not None:
                try:
                    embeddings = [embedder.embed(clip) for clip in takes]
                    if not voiceprint.takes_agree(embeddings):
                        outcome = "disagree"
                    else:
                        template = voiceprint.Template.from_embeddings(embeddings)
                        outcome = "learnt"
                except Exception:  # noqa: BLE001 - one failed run: nothing kept, the model not marked broken
                    outcome = "failed"
            takes.clear()                                  # no audio outlives the recording
            if self._threaded:
                self._enrolled.emit(mark, outcome, template)
            else:
                self._on_enrolled(mark, outcome, template)

        if self._threaded:
            threading.Thread(target=work, name="voiceprint-learn", daemon=True).start()
        else:
            work()

    def _on_enrolled(self, mark: object, outcome: str, template: Any) -> None:
        if mark is None or mark is not self._recording or self._closing:
            return                                         # cancelled (or closing) meanwhile: nothing is written
        if outcome == "learnt" and template is not None:
            outcome = "saved" if self._store.save(template) else "unsaved"     # on the Qt thread, still wanted
        logger.info("Voiceprint: recording ended (%s)", outcome)
        if outcome == "saved" and template is not None:
            self._template = template
            self._load_problem = ""
            self._last_unchecked = ""
            self._short = 0
            text = "Done: your voice is recorded."
            if not self._reset_switch():
                text += " The switch's saved setting couldn't be reset (Only my voice holds for this run)."
        elif outcome == "disagree":
            text = f"Those didn't match each other. Press Record my voice to try again, {self._address}."
        elif outcome == "unsaved":
            text = "Your voice template couldn't be saved, so nothing was kept."
        else:
            text = "The speaker model wouldn't run, so nothing was kept."
        self._stop(text)
        self._refresh_settings()                           # Settings > Talk's rows say what holds now

    def _reset_switch(self) -> bool:
        """A new recording: the switch is config.toml's again ([wake] only_my_voice, true by default). False when
        settings.json could not be changed (config.toml's value holds for this run all the same)."""
        try:
            saved = bool(self._settings.reset([SWITCH_KEY]))
        except (OSError, ValueError):
            saved = False
        if not saved:
            logger.info("Voiceprint: the switch's saved setting could not be reset")
        self._only_mine = self._toml_on
        return saved

    def cancel_recording(self) -> None:
        if self._recording is None:
            return
        logger.info("Voiceprint: recording cancelled")
        session = self._session
        if session is not None:
            session.end(talk.END_CANCEL)
            session.forget()
        self._stop("Stopped. Nothing was kept.")

    def _stop(self, text: str) -> None:
        self._timer.stop()
        self._gap.stop()
        session, self._session = self._session, None
        if session is not None:
            session.end(talk.END_CANCEL)
            session.forget()
        backend, self._backend = self._backend, None
        self._close_backend(backend)
        self._takes = []
        was = self._recording is not None
        self._recording = None
        self._message = text
        if was:
            self.recordingChanged.emit(False)
        self._changed()

    @staticmethod
    def _close_backend(backend: Any) -> None:
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - a device library that is already gone
                pass

    # ---- Settings > Talk ---------------------------------------------------------------------------------------

    def status_text(self) -> str:
        """The panel's line: what the check does now, and what the last action said."""
        if self.recording():
            return self._message or self._ask_text()
        if self._downloading:
            return f"Downloading the speaker model ({voiceprint.MODEL_SIZE_TEXT}): {self._download_percent}%."
        if self._download_error and not self.model_ready():
            return f"The download stopped ({self._download_error}). Click Download to try again."
        why = self.off_reason()
        if not why:
            text = f"On: only your voice wakes Jarvis with {PHRASE}."
        elif why == voiceprint.WHY_NO_TEMPLATE and self.model_ready():
            text = f"Record your voice saying {PHRASE} three times, and only your voice wakes Jarvis."
        else:
            text = f"Off: {_OFF_TEXTS.get(why, why)}."
        if why == voiceprint.WHY_FAILED:
            text = "Off: the speaker model wouldn't load. Click Download to get it again."
        if self._short and not why:
            text += f" {self._short_text()}, so {'they were' if self._short != 1 else 'it was'} let through."
        return f"{self._message} {text}" if self._message else text

    def _short_text(self) -> str:
        """M2: the wakes too short to check this run ("3 wakes were too short to check")."""
        return f"{self._short} wake{'s were' if self._short != 1 else ' was'} too short to check"

    def settings_rows(self) -> list[tuple[str, str]]:
        why = self.off_reason()
        if not why:
            check = "on: only your voice wakes Jarvis"
            if self._short:
                check += f"; {self._short_text()}"
            if self._last_unchecked and self._last_unchecked != voiceprint.WHY_SHORT:
                check += f" (the last wake went unchecked: {self._last_unchecked})"
        else:
            check = f"off: {_OFF_TEXTS.get(why, why)}"
            if why == voiceprint.WHY_NO_TEMPLATE and self._load_problem:
                check += " (the saved one could not be used)"
        model = (f"downloading, {self._download_percent}%" if self._downloading
                 else "on this PC, but it wouldn't load" if self.state() == STATE_BROKEN
                 else "on this PC" if self.model_ready()
                 else f"not downloaded ({voiceprint.MODEL_SIZE_TEXT}; downloaded only when you click)")
        return [(TITLE, check), ("Speaker model", f"{voiceprint.CREDIT}: {model}")]

    def attach_panel(self, panel: VoicePanel, refresh: Callable[[], Any] | None = None) -> None:
        self._panel = panel
        self._refresh_settings = refresh or (lambda: None)
        panel.downloadClicked.connect(self.download)
        panel.recordClicked.connect(self.start_recording)
        panel.cancelClicked.connect(self.cancel_recording)
        panel.forgetClicked.connect(self.forget)
        panel.switchClicked.connect(self.set_only_mine)
        self._show_panel()

    def _show_panel(self) -> None:
        if self._panel is not None:
            self._panel.show_state(state=self.state(), status=self.status_text(), downloading=self._downloading,
                                   percent=self._download_percent, recording=self.recording(),
                                   can_record=self.can_record() or self.recording(), switch_on=self._only_mine)

    def _changed(self) -> None:
        if self._closing:
            return
        self._show_panel()
        self.changed.emit()

    def download(self) -> None:
        """The owner clicked Download: the speaker model comes from its GitHub release, checked."""
        broken = self.state() == STATE_BROKEN
        if self._downloading or (self.model_ready() and not broken):
            return
        if broken:
            self._delete_model_folder()
        self._downloading, self._download_percent, self._download_error = True, 0, ""
        self._download_cancel.clear()
        logger.info("Voiceprint: the owner clicked Download")

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

        self._changed()
        if self._threaded:
            threading.Thread(target=work, name="voiceprint-download", daemon=True).start()
        else:
            work()

    def _delete_model_folder(self) -> None:
        """A model that would not load: its own folder goes before it is downloaded again (only that folder)."""
        import shutil

        folder = self._folder
        if folder.name != voiceprint.MODEL_FOLDER or folder.parent != self._models_root:
            return
        with self._lock:
            self._embedder = None
        self._broken = ""
        shutil.rmtree(folder, ignore_errors=True)
        logger.info("Voiceprint: the speaker model's folder was deleted before downloading it again")

    def _on_download_progress(self, done: int, total: int) -> None:
        percent = min(100, int(done * 100 / total)) if total > 0 else 0
        if percent != self._download_percent:
            self._download_percent = percent
            self._show_panel()

    def _on_downloaded(self, error: str) -> None:
        self._downloading = False
        self._download_error = error or ""
        logger.info("Voiceprint: the model download ended (%s)", error or "ready")
        if not error and self._broken in (voiceprint.WHY_MODEL, voiceprint.WHY_FAILED):
            self._broken = ""
        self._changed()
        self._refresh_settings()

    def shutdown(self) -> None:
        if self._recording is not None:
            self._stop("")
        self._closing = True
        self._download_cancel.set()
        self._timer.stop()
        self._gap.stop()
        self._verdict_timer.stop()
        self._pending = None

