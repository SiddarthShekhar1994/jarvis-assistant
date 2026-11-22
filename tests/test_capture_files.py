"""Reading dropped files on this PC (part C): pictures, PDFs made here with QPdfWriter, text; the refusals."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPageSize, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication

from briefing_reader import capture, capture_files as cf

FONTS = Path(__file__).resolve().parent.parent / "fonts"


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def font() -> QFont:
    family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(FONTS / "ChakraPetch-Medium.ttf")))
    return QFont(family[0], 14)


def make_pdf(path: Path, pages: list[str | None]) -> Path:
    """One page per entry: its text, or None for a page that is only a picture (no text layer)."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A5))
    writer.setResolution(72)
    painter = QPainter(writer)
    painter.setFont(font())
    for index, text in enumerate(pages):
        if index:
            writer.newPage()
        if text is None:
            image = QImage(300, 120, QImage.Format.Format_RGB32)
            image.fill(QColor("#3366aa"))
            painter.drawImage(40, 40, image)
        else:
            painter.drawText(40, 60, text)
    painter.end()
    return path


class FakePdf:
    def __init__(self, error) -> None:  # type: ignore[no-untyped-def]
        self.error = error

    def load(self, path: str):  # type: ignore[no-untyped-def]
        return self.error

    def close(self) -> None:
        pass


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        app()
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.data = self.dir / "data"
        self.data.mkdir()

    def test_pdf_text_and_a_page_without_text(self) -> None:
        path = make_pdf(self.dir / "syllabus.pdf", ["Midterm 2026-12-10 09:00 Hall 4", None, "Final 2026-12-17"])
        read = cf.read_file(path, self.data)
        self.assertEqual(read.problem, "")
        self.assertEqual((read.kind, read.page_count), ("pdf", 3))
        self.assertEqual([(page.number, page.text.strip()) for page in read.pages],
                         [(1, "Midterm 2026-12-10 09:00 Hall 4"), (3, "Final 2026-12-17")])
        self.assertEqual([(picture.media_type, picture.page) for picture in read.pictures], [("image/png", 2)])
        rendered = QImage.fromData(read.pictures[0].content)
        self.assertEqual(rendered.width(), round(QPageSize(QPageSize.PageSizeId.A5).sizePoints().width() * 2))
        self.assertEqual(read.content, path.read_bytes())

    def test_pdf_limits(self) -> None:
        path = make_pdf(self.dir / "long.pdf", [f"Page {n}" for n in range(25)])
        read = cf.read_file(path, self.data)
        self.assertEqual(read.page_count, 25)
        self.assertEqual(len(read.pages), capture.MAX_PAGES)
        path = make_pdf(self.dir / "pictures.pdf", [None] * 7)
        self.assertEqual(len(cf.read_file(path, self.data).pictures), capture.MAX_PICTURES)
        with mock.patch.object(cf, "MAX_TEXT_CHARS", 12):
            read = cf.read_file(make_pdf(self.dir / "chars.pdf", ["0123456789", "abcdefghij", "klm"]), self.data)
        self.assertEqual("".join(page.text for page in read.pages), "0123456789ab")

    def test_password_and_broken_pdfs(self) -> None:
        from PySide6.QtPdf import QPdfDocument

        path = make_pdf(self.dir / "locked.pdf", ["x"])
        for error, problem in ((QPdfDocument.Error.IncorrectPassword, cf.PASSWORD),
                               (QPdfDocument.Error.UnsupportedSecurityScheme, cf.PASSWORD),
                               (QPdfDocument.Error.InvalidFileFormat, cf.UNREADABLE)):
            with self.subTest(problem=problem):
                self.assertEqual(cf.read_file(path, self.data, document=FakePdf(error)).problem, problem)
        broken = self.dir / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 not really")
        self.assertEqual(cf.read_file(broken, self.data).problem, cf.UNREADABLE)

    def test_pictures(self) -> None:
        big = self.dir / "board.png"
        image = QImage(3200, 1000, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        image.save(str(big))
        read = cf.read_file(big, self.data)
        picture = QImage.fromData(read.pictures[0].content)
        self.assertEqual((read.kind, read.pictures[0].media_type), ("picture", "image/png"))
        self.assertEqual((picture.width(), picture.height()), (1568, 490))
        gif = self.dir / "shot.gif"     # Qt reads GIFs but does not write them: a 1x1 GIF made by hand
        gif.write_bytes(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,\x00\x00\x00\x00\x01\x00"
                        b"\x01\x00\x00\x02\x02D\x01\x00;")
        self.assertEqual(len(cf.read_file(gif, self.data).pictures), 1)
        for ext in (".jpg", ".bmp", ".webp"):
            with self.subTest(ext=ext):
                path = self.dir / f"shot{ext}"
                QImage(40, 30, QImage.Format.Format_RGB32).save(str(path))
                self.assertEqual(len(cf.read_file(path, self.data).pictures), 1)
        with mock.patch.object(cf, "PNG_LIMIT", 10):
            self.assertEqual(cf.read_file(big, self.data).pictures[0].media_type, "image/jpeg")
        garbage = self.dir / "not.png"
        garbage.write_bytes(b"not a picture")
        self.assertEqual(cf.read_file(garbage, self.data).problem, cf.UNREADABLE)

    def test_text_files(self) -> None:
        utf8 = self.dir / "notes.txt"
        utf8.write_bytes("﻿Café at 3".encode("utf-8"))
        self.assertEqual(cf.read_file(utf8, self.data).pages[0].text, "Café at 3")
        legacy = self.dir / "old.txt"
        legacy.write_bytes("Café at 3".encode("cp1252"))
        self.assertEqual(cf.read_file(legacy, self.data).pages[0].text, "Café at 3")
        blank = self.dir / "blank.txt"
        blank.write_bytes(b"   \n")
        self.assertEqual(cf.read_file(blank, self.data).problem, cf.EMPTY)

    def test_ics_is_only_read(self) -> None:
        path = self.dir / "term.ics"
        path.write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
        read = cf.read_file(path, self.data)
        self.assertEqual((read.kind, read.pages, read.pictures), ("ics", (), ()))
        self.assertEqual(read.content, path.read_bytes())

    def test_refusals(self) -> None:
        cases = {".env": cf.SECRET, "passwords.txt": cf.SECRET, "google_token_work.json": cf.SECRET,
                 "id_rsa": cf.SECRET, "notes.docx": cf.EXTENSION, "run.exe": cf.EXTENSION, "missing.png": cf.NOT_A_FILE}
        for name, problem in cases.items():
            with self.subTest(name=name):
                path = self.dir / name
                if name != "missing.png":
                    path.write_bytes(b"x")
                self.assertEqual(cf.read_file(path, self.data).problem, problem)
        inside = self.data / "captures" / "abc" / "source.png"
        inside.parent.mkdir(parents=True)
        QImage(10, 10, QImage.Format.Format_RGB32).save(str(inside))
        self.assertEqual(cf.read_file(inside, self.data).problem, cf.DATA_FOLDER)
        folder = self.dir / "folder.png"
        folder.mkdir()
        self.assertEqual(cf.read_file(folder, self.data).problem, cf.NOT_A_FILE)
        huge = self.dir / "huge.txt"
        huge.write_bytes(b"x" * (capture.MAX_FILE_BYTES + 1))
        self.assertEqual(cf.read_file(huge, self.data).problem, cf.TOO_LARGE)

    def test_logs_hold_no_file_name(self) -> None:
        path = self.dir / "my-secret-plans.docx"
        path.write_bytes(b"x")
        with self.assertLogs(cf.logger, "INFO") as logs:
            cf.read_file(path, self.data)
        self.assertNotIn("secret-plans", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
