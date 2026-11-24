"""Reading what the owner dropped in, on this PC (QtGui and QtPdf only, no widgets; called on a worker thread).

    file_problem(path, data_dir)  why a file is refused before anything is read ("" when it is not): a secret-shaped
                                 name (desktop.guard.sensitive_file_name: secret_file and more), an extension outside
                                 capture.EXTENSIONS, a file inside Jarvis's own data folder, a folder, a missing
                                 file, over capture.MAX_FILE_BYTES
    read_file(path, data_dir, *, document=)
                                 -> FileRead: the kind (picture, pdf, text, ics), the bytes (kept as the capture's
                                 copy), and what goes to Claude: a picture's PNG (JPEG 85 when the PNG is over 3.5 MB),
                                 its long edge at most LONG_EDGE_PX; a PDF's text per page (at most capture.MAX_PAGES
                                 pages and capture.MAX_TEXT_CHARS characters) with the pages that have no text layer
                                 rendered at 144 dpi as pictures (at most capture.MAX_PICTURES), a password PDF refused;
                                 a text file as UTF-8 (a BOM allowed), else Windows-1252. An .ics is only read here: it
                                 is parsed on this PC (ics.parse_ics) and never goes to Claude.
    picture_from_image(image) / picture_from_bytes(data)
                                 a QImage (a pasted picture) or bytes as the PNG / JPEG Claude is sent

Nothing here is logged but kinds, counts and reason words.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage

from .ask.capture_read import KIND_PDF, KIND_PICTURE, KIND_TEXT, PageText, Picture
from .capture import EXTENSIONS, MAX_FILE_BYTES, MAX_PAGES, MAX_PICTURES, MAX_TEXT_CHARS, PICTURE_EXTENSIONS
from .desktop.guard import sensitive_file_name

logger = logging.getLogger(__name__)

KIND_ICS = "ics"
LONG_EDGE_PX = 1568
PNG_LIMIT = 3_500_000
JPEG_QUALITY = 85
RENDER_DPI = 144
SECRET, EXTENSION, DATA_FOLDER, NOT_A_FILE, TOO_LARGE, UNREADABLE, PASSWORD, EMPTY = (
    "secret", "extension", "data_folder", "not_a_file", "too_large", "unreadable", "password", "empty")
PROBLEM_WORDS = {
    SECRET: "Jarvis doesn't read files named like passwords or keys",
    EXTENSION: "Jarvis reads pictures, PDFs, calendar files and text files only",
    DATA_FOLDER: "Jarvis doesn't read his own data folder",
    NOT_A_FILE: "That isn't a file Jarvis can open",
    TOO_LARGE: "The file is over 2 MB",
    UNREADABLE: "Jarvis couldn't read that file",
    PASSWORD: "That PDF has a password, so Jarvis didn't read it",
    EMPTY: "There was nothing to read in it",
}


@dataclass(frozen=True)
class FileRead:
    kind: str = ""
    ext: str = ""
    content: bytes = field(default=b"", repr=False)
    pages: tuple[PageText, ...] = ()
    pictures: tuple[Picture, ...] = ()
    page_count: int = 0
    problem: str = ""

    @property
    def size(self) -> int:
        return len(self.content)


def kind_of(ext: str) -> str:
    ext = ext.casefold()
    if ext in PICTURE_EXTENSIONS:
        return KIND_PICTURE
    return {".pdf": KIND_PDF, ".ics": KIND_ICS, ".txt": KIND_TEXT}.get(ext, "")


def file_problem(path: Path | str, data_dir: Path | str | None) -> str:
    """Why ``path`` is refused before it is read ("" when it may be read)."""
    path = Path(path)
    if sensitive_file_name(path.name):
        return SECRET
    if path.suffix.casefold() not in EXTENSIONS:
        return EXTENSION
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return NOT_A_FILE
    if data_dir:
        try:
            folder = Path(data_dir).resolve()
            if resolved == folder or folder in resolved.parents:
                return DATA_FOLDER
        except (OSError, RuntimeError):
            return DATA_FOLDER
    if not resolved.is_file():
        return NOT_A_FILE
    try:
        if resolved.stat().st_size > MAX_FILE_BYTES:
            return TOO_LARGE
    except OSError:
        return NOT_A_FILE
    return ""


def _encode(image: QImage, fmt: str, quality: int = -1) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, fmt, quality)
    buffer.close()
    return bytes(data.data())


def picture_from_image(image: QImage, page: int = 0) -> Picture | None:
    """A QImage as the picture Claude is sent (None for an empty image)."""
    if image is None or image.isNull() or image.width() < 1 or image.height() < 1:
        return None
    if max(image.width(), image.height()) > LONG_EDGE_PX:
        image = image.scaled(LONG_EDGE_PX, LONG_EDGE_PX, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    if image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)
        from PySide6.QtGui import QPainter

        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    png = _encode(image, "PNG")
    if png and len(png) <= PNG_LIMIT:
        return Picture("image/png", png, page)
    jpeg = _encode(image.convertToFormat(QImage.Format.Format_RGB32), "JPEG", JPEG_QUALITY)
    return Picture("image/jpeg", jpeg, page) if jpeg else None


def picture_from_bytes(content: bytes, page: int = 0) -> Picture | None:
    image = QImage.fromData(content)
    return picture_from_image(image, page)


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def read_text_bytes(content: bytes) -> tuple[PageText, ...]:
    text = _decode_text(content)[:MAX_TEXT_CHARS]
    return (PageText(0, text),) if text.strip() else ()


def read_pdf(path: Path | str, *, document: Any = None) -> tuple[tuple[PageText, ...], tuple[Picture, ...], int, str]:
    """(text pages, pictures of the pages without text, page count, problem) of the PDF at ``path``."""
    from PySide6.QtPdf import QPdfDocument

    doc = document if document is not None else QPdfDocument()
    error = doc.load(str(path))
    if error in (QPdfDocument.Error.IncorrectPassword, QPdfDocument.Error.UnsupportedSecurityScheme):
        return (), (), 0, PASSWORD
    if error != QPdfDocument.Error.None_:
        return (), (), 0, UNREADABLE
    count = int(doc.pageCount())
    pages: list[PageText] = []
    pictures: list[Picture] = []
    chars = 0
    try:
        for index in range(min(count, MAX_PAGES)):
            text = doc.getAllText(index).text() or ""
            if text.strip():
                text = text[:max(0, MAX_TEXT_CHARS - chars)]
                if text:
                    pages.append(PageText(index + 1, text))
                    chars += len(text)
            elif len(pictures) < MAX_PICTURES:
                points = doc.pagePointSize(index)
                size = QSize(max(1, round(points.width() * RENDER_DPI / 72)),
                             max(1, round(points.height() * RENDER_DPI / 72)))
                picture = picture_from_image(doc.render(index, size), page=index + 1)
                if picture is not None:
                    pictures.append(picture)
            if chars >= MAX_TEXT_CHARS:
                break
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001 - a fake or a closed document
            pass
    return tuple(pages), tuple(pictures), count, ""


def read_file(path: Path | str, data_dir: Path | str | None, *, document: Any = None) -> FileRead:
    """The file at ``path`` read for a capture (see the module docs); FileRead.problem says why not."""
    path = Path(path)
    problem = file_problem(path, data_dir)
    ext = path.suffix.casefold()
    if problem:
        logger.info("Capture: a file was refused (%s)", problem)
        return FileRead(ext=ext if ext in EXTENSIONS else "", problem=problem)
    kind = kind_of(ext)
    try:
        with open(path, "rb") as fh:
            content = fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return FileRead(kind=kind, ext=ext, problem=UNREADABLE)
    if len(content) > MAX_FILE_BYTES:
        return FileRead(kind=kind, ext=ext, problem=TOO_LARGE)
    if not content:
        return FileRead(kind=kind, ext=ext, problem=EMPTY)
    if kind == KIND_ICS:
        return FileRead(kind, ext, content)
    if kind == KIND_TEXT:
        pages = read_text_bytes(content)
        return FileRead(kind, ext, content, pages=pages, problem="" if pages else EMPTY)
    if kind == KIND_PICTURE:
        picture = picture_from_bytes(content)
        if picture is None:
            return FileRead(kind, ext, content, problem=UNREADABLE)
        return FileRead(kind, ext, content, pictures=(picture,))
    pages, pictures, count, problem = read_pdf(path, document=document)
    if problem:
        logger.info("Capture: a PDF was not read (%s)", problem)
        return FileRead(kind, ext, content, problem=problem)
    if not pages and not pictures:
        return FileRead(kind, ext, content, page_count=count, problem=EMPTY)
    return FileRead(kind, ext, content, pages=pages, pictures=pictures, page_count=count)
