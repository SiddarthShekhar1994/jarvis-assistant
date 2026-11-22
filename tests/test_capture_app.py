"""Capture in the app (part D): drops and pastes fill the tray and nothing runs before Read it; a calendar file gives
the CAPTURE group and the Capture frame; a series Approve is one insert with its RRULE, EXDATE and capture UID, and Undo
deletes it; duplicates are skipped and a re-import shows all handled; the cards survive a restart; Source opens Jarvis's
own viewer; a picture goes to Claude in the message; logs hold no title, file name or host. Offscreen; fakes only."""

from __future__ import annotations

import json
import logging
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import capture, capture_ui, gcal, ui
from briefing_reader.actions import SOURCE_CAPTURE, TODO
from briefing_reader.capture_store import CaptureStore, new_capture_id
from briefing_reader.gcal import CalendarEvent
from tests.ask_fakes import plan_stream
from tests.ui_fakes import PDT, AppHarness, fonts, settle, wait_for

_app = None
ZONE = "America/Los_Angeles"
TERM = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:chem-lab@example.org\r\nSUMMARY:Chem lab\r\n"
        "DTSTART;TZID=America/Los_Angeles:20261013T150000\r\nDTEND;TZID=America/Los_Angeles:20261013T163000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261211T075959Z\r\n"
        "EXDATE;TZID=America/Los_Angeles:20261126T150000\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:midterm@example.org\r\nSUMMARY:Midterm\r\nDTSTART:20261021T170000Z\r\n"
        "DTEND:20261021T183000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:old@example.org\r\nSUMMARY:Orientation\r\nDTSTART:20260901T170000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:berlin@example.org\r\nSUMMARY:Berlin call\r\n"
        "DTSTART;TZID=Europe/Berlin:20261014T180000\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
        "BEGIN:VTODO\r\nUID:essay@example.org\r\nSUMMARY:Essay draft\r\nDUE;VALUE=DATE:20261030\r\nEND:VTODO\r\n"
        "END:VCALENDAR\r\n")
SECRET_WORDS = ("Chem", "Midterm", "Orientation", "Berlin", "Essay", "fall-term", "example.org")


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_capture_app", "-platform", "offscreen"])
    fonts()


class RecordingCalendar:
    """Records on the harness's personal FakeCalendar itself (every holder of it sees the same): what create_event was
    handed, and what list_events answers (None: not signed in)."""

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self.inner = inner
        self.created: list = []
        self.listed: list = []
        self.answer: list | None = []
        create = inner.create_event

        def create_event(action, interactive: bool = True, **kwargs):  # type: ignore[no-untyped-def]
            self.created.append((action, kwargs.get("footer", "")))
            return create(action, interactive=interactive, **kwargs)

        def list_events(start, end, calendar_ids=("primary",)):  # type: ignore[no-untyped-def]
            self.listed.append((start, end))
            if self.answer is None:
                raise gcal.CalendarNotSignedIn("not signed in")
            return list(self.answer)

        inner.create_event = create_event
        inner.list_events = list_events


def mime_files(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drop(widget, mime: QMimeData) -> bool:  # type: ignore[no-untyped-def]
    point = QPointF(widget.width() / 2, widget.height() / 2)
    enter = QDragEnterEvent(QPoint(int(point.x()), int(point.y())), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    accepted = enter.isAccepted()
    event = QDropEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)
    settle()
    return accepted


class _Base(unittest.TestCase):
    runs: tuple = ()

    def setUp(self) -> None:
        self.app = AppHarness(self, ask=True, runs=self.runs)
        self.c = self.app.c
        self.c.window.resize(1120, 700)
        self.c.window.show()
        settle()
        self.reading = self.c.window.reading
        self.bar = self.reading.command_bar
        self.capture = self.c.capture
        self.assertIsNotNone(self.capture)
        self.capture._run_async = False
        self.capture._zone = lambda: ZONE
        self.capture._windows_zone = lambda _name: None
        self.frames: list = []
        self.capture._show_frame = lambda summary, approvals, words: self.frames.append((summary, approvals, words))
        self.calendar = RecordingCalendar(self.app.calendars["personal"])
        self.app.calendars["personal"].stamps = True
        self.term = self.app.root / "fall-term.ics"
        self.term.write_bytes(TERM.encode("utf-8"))      # as a calendar file is: CRLF, never text mode

    def capture_cards(self) -> list:
        return [action for action in self.c._actions if action.source == SOURCE_CAPTURE]

    def read_term(self, words: str = "") -> None:
        self.assertTrue(drop(self.c.window, mime_files(self.term)))
        self.bar.input.setText(words)
        self.bar.tray_read.click()
        settle()


class WaysInTests(_Base):
    def test_a_drop_fills_the_tray_and_nothing_runs(self) -> None:
        self.assertTrue(drop(self.c.window, mime_files(self.term)))
        self.assertTrue(self.bar.tray_active())
        self.assertIn("fall-term.ics", self.bar.tray_label.full_text())
        self.assertIn("Calendar file", self.bar.tray_label.full_text())
        self.assertEqual(self.capture.store.captures(), [])          # nothing read, nothing kept
        self.assertEqual(self.calendar.listed, [])                    # the calendar was not read
        self.assertEqual(self.app.factory.runner.started if self.app.factory.runner else [], [])
        self.bar.tray_remove.click()
        settle()
        self.assertFalse(self.bar.tray_active())
        self.assertIsNone(self.capture.pending)
        self.assertEqual(self.capture.store.captures(), [])

    def test_refused_drops(self) -> None:
        secret = self.app.root / ".env"
        secret.write_text("x", encoding="utf-8")
        self.assertTrue(drop(self.c.window, mime_files(secret)))
        self.bar.tray_read.click()
        settle()
        self.assertEqual(self.capture.store.captures(), [])
        self.assertEqual(self.capture.counts["refused"], 1)
        empty = QMimeData()
        self.assertFalse(drop(self.c.window, empty))
        self.assertFalse(self.bar.tray_active())

    def test_pastes(self) -> None:
        field = self.bar.input
        field.setFocus()
        picture = QMimeData()
        image = QImage(64, 32, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        picture.setImageData(image)
        field.clipboard_mime = lambda: picture
        QTest.keyClick(field, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertTrue(self.bar.tray_active())
        self.assertEqual(self.bar.tray_label.full_text(), "Pasted picture")
        self.assertEqual(field.text(), "")
        self.bar.tray_remove.click()
        text = QMimeData()
        text.setText("Lab report due Friday")
        field.clipboard_mime = lambda: text
        self.assertIsNone(field._capture_paste(False))                # a plain text Ctrl+V pastes as ever (Qt's own)
        QTest.keyClick(field, Qt.Key.Key_V,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        settle()
        self.assertTrue(self.bar.tray_active())
        self.assertIn("Pasted text", self.bar.tray_label.full_text())
        files = mime_files(self.term)
        field.clipboard_mime = lambda: files
        self.bar.tray_remove.click()
        QTest.keyClick(field, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertIn("fall-term.ics", self.bar.tray_label.full_text())
        self.assertEqual(self.calendar.listed, [])


class CalendarFileTests(_Base):
    def test_an_ics_capture_gives_the_capture_group_and_the_frame(self) -> None:
        self.read_term()
        self.assertEqual(len(self.calendar.listed), 1)                # one read of the calendar
        cards = self.capture_cards()
        self.assertEqual(sorted(card.title for card in cards), ["Berlin call", "Chem lab", "Essay draft", "Midterm"])
        summary, approvals, _words = self.frames[-1]
        states = {row.title: row.status for row in summary.rows}
        self.assertEqual(states, {"Chem lab": capture.WAITS, "Midterm": capture.WAITS, "Essay draft": capture.WAITS,
                                  "Berlin call": capture.CANT_ADD, "Orientation": capture.PAST})
        self.assertIn(approvals, {card.id for card in cards})
        card = self.reading.action_card(next(c.id for c in cards if c.title == "Chem lab"))
        self.assertEqual(card.kind_label.full_text(), "CAPTURE · CALENDAR")
        todo = next(c for c in cards if c.kind == TODO)
        self.assertEqual(self.reading.action_card(todo.id).kind_label.full_text(), "CAPTURE · TODO")
        self.assertTrue(any(c.id == todo.id for c in self.c._actions))
        berlin = next(c for c in cards if c.title == "Berlin call")
        self.assertFalse(berlin.decidable)
        self.assertEqual(berlin.error, capture.CANT_REPEAT_ZONE)
        self.assertTrue((self.c.config.data_dir / "captures.json").is_file())

    def test_the_frame_is_built_on_the_board(self) -> None:
        from briefing_reader.board import frames, protocol

        self.read_term()
        summary = self.frames[-1][0]
        built = frames.from_capture(summary, frame_id="cap-1")
        checked = json.loads(protocol.check_message(built.frame, set()).text)
        self.assertEqual(checked["kind"], "capture")
        self.assertEqual(checked["title"], "Capture · fall-term.ics")
        self.assertEqual(checked["waits"], "3 wait on Approvals")
        self.assertEqual([row["state"] for row in checked["body"]["rows"]],
                         ["waits", "waits", "waits", "cant_add", "past"])

    def test_a_series_approve_is_one_insert_and_undo_deletes_it(self) -> None:
        self.read_term()
        series = next(card for card in self.capture_cards() if card.title == "Chem lab")
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.reading.action_card(series.id).approve_button.click()
        self.assertTrue(wait_for(lambda: series.id not in self.c._jobs and series.id not in self.c._running, 10))
        settle()
        self.assertEqual(len(self.calendar.created), 1)
        action, footer = self.calendar.created[0]
        self.assertEqual(footer, gcal.CAPTURE_EVENT_FOOTER)
        body = gcal.build_event_body(action, ZONE, footer=footer)
        self.assertEqual(body["recurrence"][0].split(";UNTIL=")[0], "RRULE:FREQ=WEEKLY;BYDAY=TU,TH")
        self.assertEqual(body["recurrence"][1], f"EXDATE;TZID={ZONE}:20261126T150000")
        self.assertEqual(body["extendedProperties"], {"private": {"jarvisCaptureUid": "chem-lab@example.org"}})
        self.assertIn("From fall-term.ics", body["description"])
        self.assertEqual(self.c._store.get(series.id)["status"], "created")
        offer = self.c._undos.get(series.id)
        self.assertIsNotNone(offer)
        offer.shown -= 5
        self.reading.action_card(series.id).undo_button.click()
        self.assertTrue(wait_for(lambda: any(call[0] == "undo_create" for call in self.app.calendars["personal"].calls),
                                 10))

    def test_duplicates_are_skipped_and_a_reimport_shows_all_handled(self) -> None:
        self.calendar.answer = [
            CalendarEvent("Chem lab", start=datetime(2026, 10, 15, 15, 0, tzinfo=PDT),
                          end=datetime(2026, 10, 15, 16, 30, tzinfo=PDT), capture_uid="chem-lab@example.org"),
            CalendarEvent("midterm", start=datetime(2026, 10, 21, 10, 0, tzinfo=PDT),
                          end=datetime(2026, 10, 21, 11, 30, tzinfo=PDT))]
        self.read_term()
        states = {row.title: row.status for row in self.frames[-1][0].rows}
        self.assertEqual(states["Chem lab"], capture.ON_CALENDAR)
        self.assertEqual(states["Midterm"], capture.ON_CALENDAR)
        self.assertEqual(states["Essay draft"], capture.WAITS)
        essay = next(card for card in self.capture_cards() if card.title == "Essay draft")
        self.c._store.set(essay.id, "done")
        self.read_term()
        states = {row.title: row.status for row in self.frames[-1][0].rows}
        self.assertEqual(states["Essay draft"], capture.DECIDED)
        self.assertNotIn(capture.WAITS, states.values())
        self.assertEqual(len(self.capture.store.captures()), 1)       # the re-import kept nothing new

    def test_not_checked_when_the_calendar_cant_be_read(self) -> None:
        self.calendar.answer = None
        self.read_term()
        summary = self.frames[-1][0]
        self.assertFalse(summary.checked)
        self.assertEqual(summary.note, capture.NOT_CHECKED)
        self.assertEqual(len(self.capture_cards()), 4)

    def test_source_opens_jarvis_own_viewer_and_clear_takes_it_away(self) -> None:
        self.read_term()
        card = next(card for card in self.capture_cards() if card.title == "Midterm")
        self.c.open_action_source(card.id)
        settle()
        viewer = self.capture._viewer
        self.assertIsNotNone(viewer)
        self.assertTrue(viewer.isVisible())
        self.assertEqual(viewer.kind, "text")
        self.assertIn("BEGIN:VCALENDAR", viewer.text.toPlainText())
        self.assertEqual(viewer.caption.text(), "From fall-term.ics")
        capture_id = self.capture.capture_of(card.id)
        folder = self.c.config.data_dir / "captures" / capture_id
        self.assertTrue(folder.is_dir())
        viewer.clear_button.click()
        settle()
        self.assertFalse(folder.exists())
        self.assertEqual(self.capture_cards(), [])
        self.assertEqual(self.capture.store.captures(), [])

    def test_logs_hold_no_title_file_name_or_host(self) -> None:
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            self.read_term("only the labs")
            card = next(card for card in self.capture_cards() if card.title == "Midterm")
            self.c.open_action_source(card.id)
            settle()
        text = "\n".join(logs.output)
        for word in (*SECRET_WORDS, "only the labs"):
            self.assertNotIn(word, text)
        self.assertIn("Capture ", text)
        self.capture._viewer.close()


class RestartTests(unittest.TestCase):
    def test_the_cards_survive_a_restart(self) -> None:
        kept: dict = {}

        def prepare(config) -> None:  # type: ignore[no-untyped-def]
            store = CaptureStore(config.data_dir)
            item = capture.CapturedItem("event", "Lab", start=datetime(2026, 10, 20, 15), end=datetime(2026, 10, 20, 16))
            card = capture.to_card(item, "term.ics", today=datetime(2026, 10, 4).date())
            kept["id"] = card.id
            store.add(new_capture_id(), label="term.ics", kind="ics", cards=[(item, card)])

        app = AppHarness(self, ask=False, prepare=prepare)
        ids = [action.id for action in app.c._actions if action.source == SOURCE_CAPTURE]
        self.assertEqual(ids, [kept["id"]])


class PictureTests(_Base):
    runs = (plan_stream({"say": "Two labs.", "question": "", "items": [
        {"kind": "event", "title": "Bio lab", "date": "2026-10-14", "start": "09:00", "end": "11:00",
         "all_day_end": "", "where": "", "notes": "", "due": "", "page": 0, "quote": "Bio lab Wed 9-11",
         "repeat": {"freq": "weekly", "interval": 1, "days": ["WE"], "day_of_month": 0, "until": "2026-12-09", "count": 0},
         "skip_dates": []}]}),)

    def test_a_pasted_picture_goes_to_claude_in_the_message(self) -> None:
        field = self.bar.input
        field.setFocus()
        picture = QMimeData()
        image = QImage(80, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#224466"))
        picture.setImageData(image)
        field.clipboard_mime = lambda: picture
        QTest.keyClick(field, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertEqual(self.app.factory.runner.started if self.app.factory.runner else [], [])   # not before Read it
        self.bar.tray_read.click()
        self.assertTrue(wait_for(lambda: bool(self.capture_cards()), 10))
        settle()
        started = self.app.factory.runner.started[0]
        content = json.loads(started.raw)["message"]["content"]
        self.assertEqual([block["type"] for block in content], ["image", "text"])
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        card = self.capture_cards()[0]
        self.assertEqual(card.title, "Bio lab")
        self.assertIn("FREQ=WEEKLY;BYDAY=WE", card.rrule)
        self.assertEqual(card.notes, "From pasted picture")



class ReviewAppTests(_Base):
    def test_text_with_a_picture_beside_it_pastes_as_text(self) -> None:
        """Review item 1: a spreadsheet's or a slide's Ctrl+V (text and a bitmap of it) is text, never a capture."""
        field = self.bar.input
        mixed = QMimeData()
        mixed.setText("When is the Q3 budget review?")
        image = QImage(60, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#ffffff"))
        mixed.setImageData(image)
        field.clipboard_mime = lambda: mixed
        self.assertIsNone(field._capture_paste(False))
        self.assertEqual(field._capture_paste(True).kind, "text")              # Ctrl+Shift+V still captures it

    def test_a_bar_nobody_captures_for_never_swallows_a_paste(self) -> None:
        """Review item 1: on a bar without a capture controller (the desktop HUD's) every paste is Qt's own."""
        from briefing_reader import hud

        bar = hud.CommandBar()
        self.assertFalse(bar.input.capture_on)
        self.assertTrue(self.bar.input.capture_on)
        got: list = []
        bar.capturePasted.connect(got.append)
        picture = QMimeData()
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        picture.setImageData(image)
        bar.input.clipboard_mime = lambda: picture
        bar.show()
        bar.input.setFocus()
        QTest.keyClick(bar.input, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertEqual(got, [])
        bar.deleteLater()

    def test_busy_puts_it_back_in_the_tray(self) -> None:
        """Review item 5: when the reading could not start, what was dropped waits in the tray again."""
        self.capture._submit_mode = lambda _request, _words: False
        self.capture.offer(capture_ui.Pending(capture_ui.KIND_PASTED_TEXT, "pasted text", text="Lab Friday 3 PM"))
        self.bar.tray_read.click()
        settle()
        self.assertTrue(self.bar.tray_active())
        self.assertEqual(self.capture.pending.text, "Lab Friday 3 PM")


class CanvasTrayTests(unittest.TestCase):
    def test_the_tray_sits_above_the_canvas_ask_line(self) -> None:
        """The canvas draws the ask line's column bottom to top: the tray still shows right above the line."""
        from briefing_reader import hud

        bar = hud.CommandBar()
        bar.set_holo(True)
        bar.set_canvas(True)
        bar.resize(720, 200)
        bar.set_tray("syllabus.pdf · PDF · 120 KB")
        bar.show()
        settle()
        self.assertTrue(bar.tray.isVisible())
        self.assertLessEqual(bar.tray.geometry().bottom(), bar.field.geometry().top())
        bar.set_canvas(False)
        settle()
        self.assertLessEqual(bar.tray.geometry().bottom(), bar.field.geometry().top())
        bar.deleteLater()


class ReadOnlyStoreTests(unittest.TestCase):
    def test_a_read_only_captures_file_keeps_no_cards_and_no_copy(self) -> None:
        """Review item 4: captures.json damaged: no cards, no kept copy, and the owner is told."""
        def prepare(config) -> None:  # type: ignore[no-untyped-def]
            config.data_dir.mkdir(parents=True, exist_ok=True)
            (config.data_dir / "captures.json").write_text("{damaged", encoding="utf-8")

        app = AppHarness(self, ask=False, prepare=prepare)
        controller = app.c.capture
        controller._run_async = False
        controller._zone = lambda: ZONE
        controller._windows_zone = lambda _name: None
        said: list = []
        controller._status = lambda text, tone: said.append(text)
        controller._show_frame = lambda *args: None
        term = app.root / "fall-term.ics"
        term.write_bytes(TERM.encode("utf-8"))
        controller.offer(capture_ui.Pending(capture_ui.KIND_FILE, "fall-term.ics", path=str(term)))
        controller.read("")
        self.assertEqual([a for a in app.c._actions if a.source == SOURCE_CAPTURE], [])
        self.assertFalse((app.c.config.data_dir / "captures").exists())
        self.assertEqual(said[-1], capture_ui.NOT_KEPT_TEXT)


class BoardFrameTests(unittest.TestCase):
    """The Capture frame goes up on the board through the app's own path (the stand-in board: no web engine)."""

    def test_the_capture_frame_goes_up_with_its_chip(self) -> None:
        from tests import board_fakes

        boards: list = []
        app = AppHarness(self, ask=False, board_factory=board_fakes.stand_in_factory(boards))
        app.reading(autoplay=False)
        c = app.c
        c.window.resize(1120, 700)
        c.window.reading.set_tab(ui.SCREEN_HOME)
        settle(6)
        controller = c.capture
        controller._run_async = False
        controller._zone = lambda: ZONE
        controller._windows_zone = lambda _name: None
        term = app.root / "fall-term.ics"
        term.write_bytes(TERM.encode("utf-8"))
        self.assertTrue(drop(c.window, mime_files(term)))
        c.window.reading.command_bar.tray_read.click()
        settle(6)
        sent = [message for board in boards for message in board.sent if message.get("kind") == "capture"]
        self.assertTrue(sent)
        frame = sent[-1]
        self.assertEqual(frame["title"], "Capture · fall-term.ics")
        self.assertEqual(frame["waits"], "3 wait on Approvals")
        self.assertTrue(c.look.desk.feed.approvals_for(frame["id"]))
        self.assertIn(c.look.desk.feed.approvals_for(frame["id"]),
                      {action.id for action in c._actions if action.source == SOURCE_CAPTURE})


if __name__ == "__main__":
    unittest.main()
