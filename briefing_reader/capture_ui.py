"""Capture on the canvas (build plan item 3; Qt): the ways in, the tray, Read it, the cards and Jarvis's own viewer.

    CaptureController(bar, window, ...)
        made with Ask by the app. Ways in: a drop on the main window (an event filter: local files, dragged text, a
        webcal or https .ics link; the board's page keeps refusing drops), the ask line's capture paste (Ctrl+V of a
        picture or of copied files, Ctrl+Shift+V of text: hud.CommandBar.capturePasted; a plain text Ctrl+V stays an
        Ask) and the typed "capture: <text>" (ask.routes.ROUTE_CAPTURE: its Enter is the click, so it is read at once).
        A drop or a paste only fills the tray, one line above the ask line ("syllabus.pdf · PDF · 120 KB",
        Read it, Remove): NOTHING is read, sent or fetched before Read it or Enter, and the words typed then go along as
        the owner's words. Read it: a calendar file is parsed on this PC (ics.parse_ics, never sent to Claude); a link
        is fetched once (capture_net.fetch_calendar: https only, public hosts only; the tray names the host); a
        picture, PDF or text goes to Claude as ask.modes.MODE_CAPTURE (one Ask run). Then the duplicate check
        (capture.summarize: one read of the personal calendar, never a sign-in), the cards (CAPTURE in Approvals,
        kept in captures.json with a copy of the source) and the Capture frame on the board.
    SourceViewer
        a card's Source: the kept copy in Jarvis's own viewer - a picture, the PDF page the item is on (rendered with
        QtPdf) or the text, read only - with Clear capture (its waiting cards and the kept copy go) and Close. Never
        another app.

Logs hold capture ids, kinds, size buckets, pages, counts, durations and reason words only: never a file name, a title,
a quote, a link or a host.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from . import capture, capture_files, capture_net, hud, ics
from .ask.capture_read import KIND_PDF, KIND_PICTURE, KIND_TEXT, CaptureJob, PageText, size_bucket
from .ask.modes import MODE_CAPTURE, ORIGIN_CAPTURE, ModeRequest
from .capture_store import CaptureStore, new_capture_id

logger = logging.getLogger(__name__)

KIND_FILE, KIND_PASTED_PICTURE, KIND_PASTED_TEXT, KIND_LINK = "file", "pasted_picture", "pasted_text", "link"
PASTED_PICTURE_LABEL = "pasted picture"
PASTED_TEXT_LABEL = "pasted text"
TYPED_TEXT_LABEL = "typed text"
READING_TEXT = "Reading it on this PC..."
FETCHING_TEXT = "Fetching the calendar once..."
ASK_OFF_TEXT = "Jarvis reads pictures, PDFs and text with Claude, and Ask is off; calendar files still work"
NOT_STARTED_TEXT = "Jarvis is busy; Read it again in a moment"
NOT_KEPT_TEXT = "Jarvis couldn't keep this capture: its file in the data folder can't be written; nothing was kept"
NOTHING_FOUND_TEXT = "Nothing to add: no events or to-dos found"
FOUND_TEXT = "Found {found}: {new} new - see the Capture frame; each waits for your OK in Approvals"
ALL_HANDLED_TEXT = "Found {found}: every one is on your calendar, decided or past already"
GONE_TEXT = "The kept copy of this capture is gone"
CLEAR_TEXT = "Clear capture"
CLOSE_TEXT = "Close"
VIEWER_MAX = QSize(900, 640)
_KIND_WORDS = {".pdf": "PDF", ".ics": "Calendar file", ".txt": "Text"}


@dataclass
class Pending:
    """What waits in the tray (nothing of it read yet)."""

    kind: str
    label: str = field(repr=False)
    path: str = field(default="", repr=False)
    image: Any = field(default=None, repr=False, compare=False)
    text: str = field(default="", repr=False)
    url: str = field(default="", repr=False)

    def tray_text(self) -> str:
        if self.kind == KIND_FILE:
            ext = Path(self.path).suffix.casefold()
            what = "Picture" if ext in capture.PICTURE_EXTENSIONS else _KIND_WORDS.get(ext, "File")
            try:
                size = Path(self.path).stat().st_size
                return f"{self.label} · {what} · {_size_words(size)}"
            except OSError:
                return f"{self.label} · {what}"
        if self.kind == KIND_LINK:
            return f"Calendar link · {self.label} · fetched once on Read it"
        if self.kind == KIND_PASTED_PICTURE:
            return "Pasted picture"
        return f"Pasted text · {len(self.text):,} characters"


def _size_words(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _calendar_link(text: str) -> str:
    """``text`` when it is one webcal or https calendar link ("" otherwise)."""
    words = str(text or "").strip()
    if not words or any(char.isspace() for char in words):
        return ""
    lowered = words.casefold()
    if lowered.startswith("webcal://"):
        return words
    if lowered.startswith("https://") and urlsplit(words).path.casefold().endswith(".ics"):
        return words
    return ""


def pending_from_mime(mime: Any) -> Pending | None:
    """What a drop or a paste offers: the first local file, a calendar link, a picture or text (None: nothing)."""
    if mime is None:
        return None
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile():
                path = url.toLocalFile()
                return Pending(KIND_FILE, Path(path).name, path=path)
        for url in mime.urls():
            link = _calendar_link(url.toString())
            if link:
                return Pending(KIND_LINK, capture_net.link_host(link) or "a link", url=link)
    if mime.hasImage():
        image = QImage(mime.imageData())
        if not image.isNull():
            return Pending(KIND_PASTED_PICTURE, PASTED_PICTURE_LABEL, image=image)
    if mime.hasText() and mime.text().strip():
        link = _calendar_link(mime.text())
        if link:
            return Pending(KIND_LINK, capture_net.link_host(link) or "a link", url=link)
        return Pending(KIND_PASTED_TEXT, PASTED_TEXT_LABEL, text=mime.text())
    return None


def pending_from_paste(pasted: Any) -> Pending | None:
    """hud.PastedCapture -> Pending."""
    kind = getattr(pasted, "kind", "")
    if kind == "picture" and getattr(pasted, "image", None) is not None:
        return Pending(KIND_PASTED_PICTURE, PASTED_PICTURE_LABEL, image=pasted.image)
    if kind == "files" and getattr(pasted, "paths", ()):
        path = pasted.paths[0]
        return Pending(KIND_FILE, Path(path).name, path=path)
    if kind == "text" and str(getattr(pasted, "text", "")).strip():
        link = _calendar_link(pasted.text)
        if link:
            return Pending(KIND_LINK, capture_net.link_host(link) or "a link", url=link)
        return Pending(KIND_PASTED_TEXT, PASTED_TEXT_LABEL, text=pasted.text)
    return None


@dataclass
class _Inflight:
    label: str = field(repr=False)
    kind: str
    content: bytes = field(default=b"", repr=False)
    ext: str = ""
    words: str = field(default="", repr=False)
    started: float = 0.0


class _Bridge(QObject):
    done = Signal(object, object)       # (callback, result): a worker's result back on the GUI thread


class CaptureController(QObject):
    """See the module doc. Every outside piece is handed in, so the tests fake each one."""

    def __init__(self, bar: Any, window: QWidget | None, *, store: CaptureStore, data_dir: Path,
                 submit_mode: Callable[[ModeRequest, str], bool] | None, ask_enabled: Callable[[], bool],
                 decided: Callable[[str], bool], take_cards: Callable[[list[tuple[str, Any]]], None],
                 show_frame: Callable[[Any, str, str], None], events: Callable[[datetime, datetime], list | None],
                 zone: Callable[[], str], now: Callable[[], datetime], status: Callable[[str, str], None],
                 decided_at: Callable[[str], datetime | None] | None = None,
                 fetch: Callable[[str], Any] = capture_net.fetch_calendar,
                 windows_zone: Callable[[str], str | None] = ics.windows_zone_id, run_async: bool = True,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bar = bar
        self.window = window
        self.store = store
        self.data_dir = Path(data_dir)
        self._submit_mode = submit_mode
        self._ask_enabled = ask_enabled
        self._decided = decided
        self._decided_at_hook = decided_at
        self._take_cards = take_cards
        self._show_frame = show_frame
        self._events = events
        self._zone = zone
        self._now = now
        self._status = status
        self._fetch = fetch
        self._windows_zone = windows_zone
        self._run_async = run_async
        self.pending: Pending | None = None
        self._inflight: dict[str, _Inflight] = {}
        self._card_capture: dict[str, str] = {}
        self._viewer: SourceViewer | None = None
        self._last_pending: Pending | None = None
        self._bridge = _Bridge()
        self._bridge.done.connect(lambda callback, result: callback(result))
        self.counts: dict[str, int] = {"offered": 0, "read": 0, "refused": 0}
        if bar is not None:
            bar.input.capture_on = True
            bar.capturePasted.connect(self.on_paste)
            bar.trayRead.connect(self.read)
            bar.trayRemoved.connect(self.remove)
        if window is not None:
            window.setAcceptDrops(True)
            window.installEventFilter(self)

    # ---- start ---------------------------------------------------------------------------------------------------

    def load(self) -> None:
        """The kept capture cards into Approvals (Jarvis started), after the old captures were pruned."""
        try:
            self.store.prune(self._now(), self._decided_at)
        except Exception as exc:  # noqa: BLE001 - pruning never stops the cards
            logger.info("Capture: pruning skipped (%s)", type(exc).__name__)
        self._publish()

    def _decided_at(self, card_id: str) -> datetime | None:
        """When the card was decided (the app's ActionStore keeps it), else None."""
        if self._decided_at_hook is None:
            return None
        try:
            return self._decided_at_hook(card_id)
        except Exception:  # noqa: BLE001
            return None

    def _publish(self) -> None:
        pairs = self.store.cards(self._now().date())
        self._card_capture = {card.id: capture_id for capture_id, card in pairs}
        self._take_cards(pairs)

    # ---- ways in ---------------------------------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if pending_from_mime(event.mimeData()) is not None:
                event.acceptProposedAction()
                return True
            return False
        if kind == QEvent.Type.Drop:
            pending = pending_from_mime(event.mimeData())
            if pending is None:
                return False
            event.acceptProposedAction()
            self.offer(pending)
            return True
        return False

    def on_paste(self, pasted: Any) -> None:
        pending = pending_from_paste(pasted)
        if pending is not None:
            self.offer(pending)

    def offer(self, pending: Pending) -> None:
        """Into the tray; nothing is read."""
        self.pending = pending
        self.counts["offered"] += 1
        logger.info("Capture: offered a %s (nothing read)", pending.kind)
        if self.bar is not None:
            self.bar.set_tray(pending.tray_text())

    def remove(self) -> None:
        self.pending = None
        if self.bar is not None and self.bar.tray_active():
            self.bar.clear_tray()

    def typed(self, text: str) -> None:
        """"capture: <text>": read at once (its Enter was the click)."""
        if str(text or "").strip():
            self.pending = Pending(KIND_PASTED_TEXT, TYPED_TEXT_LABEL, text=str(text))
            self.read("")

    # ---- Read it ---------------------------------------------------------------------------------------------------

    def read(self, words: str = "") -> None:
        """Read what waits in the tray (see the module doc); ``words`` go along as the owner's words."""
        pending, self.pending = self.pending, None
        if self.bar is not None and self.bar.tray_active():
            self.bar.clear_tray()
        if pending is None:
            return
        words = " ".join(str(words or "").split())
        if words and self.bar is not None:
            self.bar.take_request(words)
        self._last_pending = pending            # back into the tray when the read could not start (busy)
        capture_id = new_capture_id()
        self.counts["read"] += 1
        started = time.monotonic()
        if pending.kind == KIND_FILE:
            self._status(READING_TEXT, hud.TONE_WORKING)
            self._spawn(lambda: capture_files.read_file(pending.path, self.data_dir),
                        lambda read: self._file_read(capture_id, pending, read, words, started))
        elif pending.kind == KIND_LINK:
            self._status(FETCHING_TEXT, hud.TONE_WORKING)
            self._spawn(lambda: self._fetch(pending.url),
                        lambda fetched: self._link_fetched(capture_id, pending, fetched, words, started))
        elif pending.kind == KIND_PASTED_PICTURE:
            picture = capture_files.picture_from_image(pending.image)
            if picture is None:
                self._refused(capture_id, "unreadable", capture_files.PROBLEM_WORDS[capture_files.UNREADABLE])
                return
            job = CaptureJob(capture_id, KIND_PICTURE, pending.label, pictures=(picture,), words=words,
                             size=len(picture.content))
            self._ask(job, _Inflight(pending.label, "picture", picture.content,
                                     ".png" if picture.media_type == "image/png" else ".jpg", words, started))
        else:
            text = pending.text[:capture.MAX_TEXT_CHARS]
            content = text.encode("utf-8")
            job = CaptureJob(capture_id, KIND_TEXT, pending.label, pages=(PageText(0, text),), words=words,
                             size=len(content))
            self._ask(job, _Inflight(pending.label, "text", content, ".txt", words, started))

    def _spawn(self, work: Callable[[], Any], then: Callable[[Any], None]) -> None:
        if not self._run_async:
            then(work())
            return

        def run() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - a worker never takes the app down
                logger.warning("Capture: a read stopped by an unexpected error (%s)", type(exc).__name__)
                result = exc
            self._bridge.done.emit(then, result)

        threading.Thread(target=run, name="capture", daemon=True).start()

    def _refused(self, capture_id: str, reason: str, words: str) -> None:
        self.counts["refused"] += 1
        logger.info("Capture %s: refused (%s)", capture_id, reason)
        self._status(words, hud.TONE_WARN)

    def _file_read(self, capture_id: str, pending: Pending, read: Any, words: str, started: float) -> None:
        if isinstance(read, Exception):
            self._refused(capture_id, "error", capture_files.PROBLEM_WORDS[capture_files.UNREADABLE])
            return
        if read.problem:
            self._refused(capture_id, read.problem, capture_files.PROBLEM_WORDS.get(read.problem, "Not read"))
            return
        logger.info("Capture %s: a %s read on this PC (%s, %d page(s), %d picture(s))", capture_id, read.kind,
                    size_bucket(read.size), read.page_count, len(read.pictures))
        inflight = _Inflight(pending.label, read.kind, read.content, read.ext, words, started)
        if read.kind == capture_files.KIND_ICS:
            self._parse_ics(capture_id, inflight)
            return
        job = CaptureJob(capture_id, KIND_PDF if read.kind == "pdf" else read.kind, pending.label, pages=read.pages,
                         pictures=read.pictures, words=words, page_count=read.page_count, size=read.size)
        self._ask(job, inflight)

    def _link_fetched(self, capture_id: str, pending: Pending, fetched: Any, words: str, started: float) -> None:
        if isinstance(fetched, Exception) or fetched.content is None:
            problem = getattr(fetched, "problem", "") or capture_net.UNREACHABLE
            self._refused(capture_id, problem, capture_net.PROBLEM_WORDS.get(problem, "Not fetched"))
            return
        self._parse_ics(capture_id, _Inflight(pending.label, "link", fetched.content, ".ics", words, started))

    def _parse_ics(self, capture_id: str, inflight: _Inflight) -> None:
        zone = self._zone()
        wall = self._wall()

        def work() -> Any:
            return ics.parse_ics(inflight.content, zone=zone, now=wall, windows_zone=self._windows_zone)

        def then(result: Any) -> None:
            if isinstance(result, Exception):
                self._refused(capture_id, "error", "Jarvis couldn't read that calendar file")
                return
            if result.problem:
                self._refused(capture_id, result.problem, ics.PROBLEM_WORDS.get(result.problem, "Not read"))
                return
            kind = "link" if inflight.kind == "link" else "ics"
            self._finish(capture_id, replace_kind(inflight, kind), list(result.items), list(result.past),
                         cancelled=result.cancelled, more=result.more, unreadable=result.unreadable)

        self._spawn(work, then)

    def _wall(self) -> datetime:
        now = self._now()
        return now.replace(tzinfo=None) if now.tzinfo is not None else now

    def _ask(self, job: CaptureJob, inflight: _Inflight) -> None:
        if self._submit_mode is None or not self._ask_enabled():
            self._refused(job.capture_id, "ask_off", ASK_OFF_TEXT)
            return
        self._inflight[job.capture_id] = inflight
        request = ModeRequest(MODE_CAPTURE, ORIGIN_CAPTURE, typed=inflight.words, payload=job)
        if not self._submit_mode(request, inflight.words or f"Read {inflight.label}"):
            self._inflight.pop(job.capture_id, None)
            self._refused(job.capture_id, "not_started", NOT_STARTED_TEXT)
            pending = self._last_pending
            if pending is not None and self.pending is None:
                self.offer(pending)             # nothing was read: it waits in the tray again for Read it

    def on_outcome(self, outcome: Any) -> None:
        """A capture's Claude run ended (the app hands every MODE_CAPTURE outcome here)."""
        capture_id = str(getattr(outcome, "target", "") or "")
        inflight = self._inflight.pop(capture_id, None)
        if inflight is None:
            return
        if not getattr(outcome, "ok", False) or not isinstance(getattr(outcome, "brief", None), tuple):
            words = str(getattr(outcome, "message", "") or "") or "Jarvis couldn't read it; nothing was kept"
            self._refused(capture_id, str(getattr(outcome, "kind", "") or "error"), words)
            return
        items, counts = outcome.brief
        question = str(getattr(outcome, "question", "") or "")
        self._finish(capture_id, inflight, list(items), [], past_count=int(getattr(counts, "past", 0) or 0),
                     question=question)

    # ---- the summary ---------------------------------------------------------------------------------------------

    def _finish(self, capture_id: str, inflight: _Inflight, items: list[Any], past: list[Any], *,
                cancelled: int = 0, more: int = 0, unreadable: int = 0, past_count: int = 0,
                question: str = "") -> None:
        span = capture.check_span(items, self._wall())

        def work() -> Any:
            if span is None:
                return []
            try:
                return self._events(*span)
            except Exception as exc:  # noqa: BLE001 - not checked: the cards are still made
                logger.info("Capture %s: the calendar was not read (%s)", capture_id, type(exc).__name__)
                return None

        def then(events: Any) -> None:
            if isinstance(events, Exception):
                events = None
            today = self._wall().date()
            summary = capture.summarize(inflight.label, inflight.kind, items, past=past, events=events,
                                        decided=self._decided, zone=self._zone(), today=today, cancelled=cancelled,
                                        more=more, unreadable=unreadable)
            if past_count:
                summary.counts[capture.PAST] += past_count
                summary.counts["found"] += past_count
            known = {card.id for _capture, card in self.store.cards(today)}
            fresh = [(item, card) for item, card in summary.cards if card.id not in known]
            not_kept = False
            if fresh:      # a capture whose every card is kept already (a re-import's can't-add ones) keeps nothing new
                saved = self.store.add(capture_id, label=inflight.label, kind=inflight.kind, cards=fresh,
                                       counts=summary.counts, ext=inflight.ext if inflight.content else "")
                if saved:
                    if inflight.content and inflight.ext:
                        self.store.keep_source(capture_id, inflight.content, inflight.ext)
                    self._publish()
                else:      # captures.json is read-only (damaged or newer) or can't be written: no cards, no copy
                    not_kept = True
                    logger.info("Capture %s: not kept (%s)", capture_id, self.store.problem or "write")
            waiting = next((row.card_id for row in summary.rows if row.status == capture.WAITS), "")
            try:
                self._show_frame(summary, waiting, inflight.words)
            except Exception as exc:  # noqa: BLE001 - the cards are there without the frame
                logger.info("Capture %s: no frame (%s)", capture_id, type(exc).__name__)
            counts = summary.counts
            logger.info("Capture %s: %s done in %d ms (found %d, cards %d, new %d, on calendar %d, decided %d, past "
                        "%d, can't add %d, cancelled %d, not shown %d; calendar %s)", capture_id, inflight.kind,
                        int((time.monotonic() - inflight.started) * 1000) if inflight.started else 0,
                        counts.get("found", 0), counts.get("cards", 0), counts.get(capture.WAITS, 0),
                        counts.get(capture.ON_CALENDAR, 0), counts.get(capture.DECIDED, 0),
                        counts.get(capture.PAST, 0), counts.get(capture.CANT_ADD, 0), counts.get("cancelled", 0),
                        counts.get("more", 0), "checked" if summary.checked else "not checked")
            if not_kept:
                self._status(NOT_KEPT_TEXT, hud.TONE_WARN)
            elif question and not items:
                self._status(question, hud.TONE_DONE)
            elif not summary.rows:
                self._status(NOTHING_FOUND_TEXT, hud.TONE_DONE)
            elif counts.get(capture.WAITS, 0):
                self._status(FOUND_TEXT.format(found=counts.get("found", 0), new=counts.get(capture.WAITS, 0)),
                             hud.TONE_DONE)
            else:
                self._status(ALL_HANDLED_TEXT.format(found=counts.get("found", 0)), hud.TONE_DONE)

        self._spawn(work, then)

    # ---- Source and Clear ------------------------------------------------------------------------------------------

    def capture_of(self, card_id: str) -> str:
        return self._card_capture.get(card_id, "")

    def show_source(self, card_id: str) -> SourceViewer | None:
        """A card's Source: the kept copy in Jarvis's own viewer (never another app)."""
        capture_id = self.capture_of(card_id)
        entry = self.store.capture(capture_id) if capture_id else None
        path = self.store.source_path(capture_id) if entry is not None else None
        if entry is None or path is None:
            self._status(GONE_TEXT, hud.TONE_WARN)
            return None
        page = 0
        for record in entry.get("cards") or ():
            if isinstance(record, dict) and record.get("id") == card_id:
                where = str((record.get("item") or {}).get("where_in_source", ""))
                if where.startswith("p. ") and where[3:].isdigit():
                    page = int(where[3:])
        if self._viewer is not None:
            self._viewer.close()
        viewer = SourceViewer(self.window)
        viewer.show_source(path, label=str(entry.get("label", "")), page=page)
        viewer.clearRequested.connect(lambda: self.clear(capture_id))
        self._viewer = viewer
        viewer.show()
        logger.info("Capture %s: Source opened (%s)", capture_id, entry.get("kind", ""))
        return viewer

    def clear(self, capture_id: str) -> None:
        """Clear: the capture's cards and its kept copy go now (the decisions stay in actions.json by id)."""
        if self.store.remove(capture_id):
            logger.info("Capture %s: cleared", capture_id)
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None
        self._publish()


def replace_kind(inflight: _Inflight, kind: str) -> _Inflight:
    return _Inflight(inflight.label, kind, inflight.content, inflight.ext, inflight.words, inflight.started)


class SourceViewer(QDialog):
    """A capture's kept copy, read only, inside Jarvis (non-modal): its label on top, the picture, the PDF page or the
    text, then Clear capture and Close. Nothing in it opens a link or another app."""

    clearRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog)
        self.setObjectName("CaptureSource")
        self.setModal(False)
        self.setWindowTitle("Source")
        self.setStyleSheet(
            f"QDialog#CaptureSource {{ background: {hud.GROUND}; border: 1px solid {hud.ACCENT}; }}"
            f"QScrollBar:vertical {{ background: {hud.GROUND}; width: 8px; margin: 0; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: {hud.TEXT_DIM}; min-height: 24px; border-radius: 3px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        self.caption = hud.make_label("", hud.mono_font(11, 600, 0.04), hud.TEXT_BRIGHT, wrap=True)
        self.note = hud.make_label("", hud.mono_font(10), hud.TEXT_SUB, wrap=True)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("hudScroll")
        self.scroll.setWidget(self.picture)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(hud.mono_font(12))
        self.text.setStyleSheet(f"QPlainTextEdit {{ background: {hud.GROUND}; color: {hud.TEXT_BODY}; "
                                f"border: 1px solid {hud.TEXT_DIM}; padding: 8px; }}")
        from . import holo

        # The app's own buttons: the canvas look while it is on (holo.button_look), the hologram's else.
        self.clear_button = holo.HoloButton(CLEAR_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.clear_button.setToolTip("Its cards that wait and the kept copy go now")
        self.close_button = holo.HoloButton(CLOSE_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.clear_button.clicked.connect(self.clearRequested)
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.close_button)
        layout.addWidget(self.caption)
        layout.addWidget(self.note)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.text, 1)
        layout.addLayout(buttons)
        self.kind = ""

    def show_source(self, path: Path, *, label: str, page: int = 0) -> None:
        ext = path.suffix.casefold()
        self.caption.setText(f"From {label}" + (f", p. {page}" if page else ""))
        self.note.setText("Jarvis's kept copy, on this PC only. Read only.")
        if ext in capture.PICTURE_EXTENSIONS:
            self._show_image(QImage(str(path)), "picture")
        elif ext == ".pdf":
            self._show_image(_pdf_page(path, page or 1), "pdf")
        else:
            raw = path.read_bytes()[:capture.MAX_FILE_BYTES]
            try:
                words = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                words = raw.decode("cp1252", errors="replace")
            self.kind = "text"
            self.scroll.hide()
            self.text.show()
            self.text.setPlainText("\n".join(words.splitlines())[:capture.MAX_TEXT_CHARS])
        self.resize(VIEWER_MAX if self.kind != "text" else QSize(720, 560))

    def _show_image(self, image: QImage, kind: str) -> None:
        self.kind = kind
        self.text.hide()
        self.scroll.show()
        if image.isNull():
            self.picture.setText("Jarvis couldn't draw the kept copy")
            return
        if image.width() > VIEWER_MAX.width() - 40:
            image = image.scaledToWidth(VIEWER_MAX.width() - 40, Qt.TransformationMode.SmoothTransformation)
        self.picture.setPixmap(QPixmap.fromImage(image))


def _pdf_page(path: Path, page: int) -> QImage:
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    if doc.load(str(path)) != QPdfDocument.Error.None_ or not 1 <= page <= doc.pageCount():
        return QImage()
    points = doc.pagePointSize(page - 1)
    image = doc.render(page - 1, QSize(max(1, round(points.width() * 1.5)), max(1, round(points.height() * 1.5))))
    doc.close()
    return image
