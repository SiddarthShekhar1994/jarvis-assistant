"""Settings > Voice's own part for the voice made on this PC (owner decisions 2026-11-12), under the Voice rows while
[voice] engine = "local": whether the local voice can run, the Download - only on the owner's click: the three pinned
models in turn (local_voice.MODELS, about 346 MB from Hugging Face; checked and resumable by
meeting_speech.ModelDownloader) - and, while add-ons are missing, the owner's pip lines (Jarvis never runs pip).

``VoiceSettingsPanel(parent=None)``: a status line, Download (``downloadClicked``), the add-ons' lines (selectable).
``VoiceDownload(models_root, voice=, on_ready=, downloader_factory=, addons=, threaded=True, parent=None)``:
``attach_panel(panel, refresh=)``, ``download()``, ``ready()``, ``downloading()``, ``shutdown()``. ``on_ready()`` runs on
the GUI thread when a download ends with every model whole (the app then warms the voice). The log says only that the
owner clicked, sizes and how it ended.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from . import hud
from . import local_voice as lv
from .meeting_speech import ModelDownloader

logger = logging.getLogger(__name__)

DOWNLOAD_TEXT = "Download the local voice"
ADDONS_TEXT = "The add-ons (you install them; Jarvis never does), then restart Jarvis:"


class VoiceSettingsPanel(QWidget):
    """See the module docstring."""

    downloadClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(hud.body_font(13))
        hud.set_label_color(self.status, hud.TEXT_SOFT)
        self.download = QPushButton(DOWNLOAD_TEXT)
        self.download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download.clicked.connect(self.downloadClicked)
        self.addons = QLabel(ADDONS_TEXT)
        self.addons.setWordWrap(True)
        self.addons.setFont(hud.body_font(13))
        hud.set_label_color(self.addons, hud.TEXT_SOFT)
        self.pip = QLabel("")
        self.pip.setWordWrap(True)
        self.pip.setFont(hud.mono_font(11))
        self.pip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.status, self.download, self.addons, self.pip):
            layout.addWidget(widget)

    def show_state(self, *, ready: bool, downloading: bool, percent: int, error: str, voice: str,
                   pip_lines: list[str]) -> None:
        if ready:
            text = (f"The local voice is on this PC: {lv.voice_label(voice)}, made on the CPU. Nothing he says "
                    "leaves this PC.")
        elif downloading:
            text = f"Downloading the local voice ({lv.SIZE_TEXT}) from Hugging Face: {percent}%."
        elif error:
            text = f"The download stopped ({error}). Click Download to try again; it carries on where it stopped."
        else:
            text = (f"The local voice ({lv.SIZE_TEXT}, from Hugging Face) is downloaded only when you click Download. "
                    "Until it is here, the Windows voice speaks - never the online one.")
        self.status.setText(text)
        self.download.setVisible(not ready)
        self.download.setEnabled(not downloading)
        self.download.setText(f"Downloading... {percent}%" if downloading else DOWNLOAD_TEXT)
        self.addons.setVisible(bool(pip_lines))
        self.pip.setText("\n".join(pip_lines))
        self.pip.setVisible(bool(pip_lines))


class VoiceDownload(QObject):
    """See the module docstring. ``threaded`` False (tests): the download runs in the caller."""

    _progress = Signal(int, int)
    _done = Signal(str)

    def __init__(self, models_root: Path, *, voice: str = lv.DEFAULT_VOICE, on_ready: Callable[[], Any] | None = None,
                 downloader_factory: Callable[..., Any] | None = None, addons: Callable[[], list[str]] | None = None,
                 threaded: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = Path(models_root)
        self._voice = voice
        self._on_ready = on_ready or (lambda: None)
        self._factory = downloader_factory or (
            lambda root, model, progress, cancel: ModelDownloader.for_model(root, model, progress=progress,
                                                                            cancel=cancel))
        self._addons = addons or lv.addons_missing
        self._threaded = threaded
        self._downloading = False
        self._percent = 0
        self._error = ""
        self._cancel = threading.Event()
        self._panel: VoiceSettingsPanel | None = None
        self._refresh: Callable[[], Any] = lambda: None
        self._progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    def attach_panel(self, panel: VoiceSettingsPanel, refresh: Callable[[], Any] | None = None) -> None:
        self._panel = panel
        self._refresh = refresh or (lambda: None)
        panel.downloadClicked.connect(self.download)
        self._show()

    def ready(self) -> bool:
        return lv.models_ready(self._root)

    def downloading(self) -> bool:
        return self._downloading

    def _show(self) -> None:
        if self._panel is not None:
            self._panel.show_state(ready=self.ready(), downloading=self._downloading, percent=self._percent,
                                   error=self._error, voice=self._voice, pip_lines=lv.pip_lines(self._addons()))

    def download(self) -> None:
        """The owner clicked Download: the models still missing come from Hugging Face, one after another."""
        if self._downloading or self.ready():
            return
        missing = lv.models_missing(self._root)
        total = sum(lv.SIZE_BYTES.values())
        have = sum(size for name, size in lv.SIZE_BYTES.items() if name not in {model.name for model in missing})
        self._downloading, self._percent, self._error = True, 0, ""
        self._cancel.clear()
        logger.info("Voice: the owner clicked Download (%d of %d MB to fetch)", (total - have) // 1_000_000,
                    total // 1_000_000)

        def emit_progress(done: int) -> None:
            if self._threaded:
                self._progress.emit(int(done), int(total))
            else:
                self._on_progress(int(done), int(total))

        def work() -> None:
            error, base = "", have
            for model in missing:
                weight = lv.SIZE_BYTES[model.name]

                def progress(done: int, size: int, base: int = base, weight: int = weight) -> None:
                    emit_progress(base + (min(weight, int(weight * done / size)) if size > 0 else 0))

                try:
                    error = self._factory(self._root, model, progress, self._cancel).run()
                except Exception as exc:  # noqa: BLE001 - a download never dies silently
                    error = type(exc).__name__
                if error:
                    break
                base += weight
            if self._threaded:
                self._done.emit(error)
            else:
                self._on_done(error)

        self._show()
        if self._threaded:
            threading.Thread(target=work, name="voice-download", daemon=True).start()
        else:
            work()

    def _on_progress(self, done: int, total: int) -> None:
        percent = min(100, int(done * 100 / total)) if total > 0 else 0
        if percent != self._percent:
            self._percent = percent
            self._show()

    def _on_done(self, error: str) -> None:
        self._downloading = False
        self._error = error or ""
        logger.info("Voice: the download ended (%s)", error or "ready")
        self._show()
        self._refresh()
        if not error and self.ready():
            try:
                self._on_ready()
            except Exception as exc:  # noqa: BLE001 - the voice still loads at its first line
                logger.info("Voice: could not warm the new voice (%s)", type(exc).__name__)

    def shutdown(self) -> None:
        """A download under way stops (its .part files stay; the next click carries on)."""
        self._cancel.set()
