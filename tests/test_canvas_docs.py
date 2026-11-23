"""Documents on the canvas (part 3): the frames (a document as a text frame, a sheet as a table, the amber chip while
changes wait) pass board.protocol's checks with injected text kept as text; the one page change ("documents" in the
canvas's buttons, the same in protocol.py and the page); the pane beside the frames (its geometry, the side box as the
union with the side tile, wide over the frames' area, hidden while a frame is open big, the keyboard left in the ask
line); a document's frame opens the item, never open big; the frame drawn again in its place after a Keep. Offscreen;
the board is tests.board_fakes' stand-in; every name and text is invented."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import canvas_ui, canvas_view, ui
from briefing_reader import dock as dock_mod
from briefing_reader.board import frames, protocol
from briefing_reader.docs import model, proposals
from briefing_reader.docs.store import Row, SheetData
from tests.test_canvas import CanvasApp
from tests.ui_fakes import fonts, settle

_app: QApplication | None = None
BOARD_JS = Path(frames.__file__).resolve().parent / "assets" / "board.js"
DOC = "# Trip plan\n\nFly out **Friday**.\n\n## Packing\n\n- passport\n"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas_docs", "-platform", "offscreen"])
    fonts()


def column(name: str, type_: str, choices: str = "") -> model.Column:
    found, _ = model.check_column(name, type_, choices)
    assert found is not None
    return found


class FrameTests(unittest.TestCase):
    def test_a_document_frame(self) -> None:
        pending = [proposals.doc_add("## Budget\n\n$200", "Packing"), proposals.doc_replace("Packing", "## Packing\nx")]
        built = frames.from_doc("Trip plan", DOC, pending, frame_id="doc-1")
        (message,) = built.messages
        checked = protocol.check_message(message)
        self.assertEqual(checked.type, "frame")
        self.assertEqual((message["kind"], message["title"], message["meta"], message["waits"]),
                         ("text", "Trip plan", "DOC · 7 WORDS", "2 changes wait on you"))
        self.assertIn("Fly out Friday.", message["body"]["text"])
        self.assertNotIn("**", message["body"]["text"])
        rows = {(row["text"], row["meta"]) for row in message["body"]["rows"]}
        self.assertIn(("Packing", frames.CHANGED_WORD), rows)
        self.assertIn(("Budget", frames.NEW_WORD), rows)
        self.assertEqual(frames.from_doc("x", "", (), frame_id="doc-2").messages[0]["waits"], "")

    def test_injected_and_odd_text_stays_text_and_is_cut(self) -> None:
        nasty = "# <script>alert(1)</script>‮\ud800" + "x" * 900
        (message,) = frames.from_doc("<img src=x onerror=1>" + "n" * 90, nasty, (), frame_id="doc-3").messages
        protocol.check_message(message)
        self.assertLessEqual(len(message["title"]), protocol.TITLE_CAP)
        self.assertLessEqual(len(message["body"]["text"]), protocol.TEXT_CAP)
        self.assertIn("<U+202E>", message["body"]["rows"][0]["text"] + message["body"]["text"])
        self.assertNotIn("\ud800", message["body"]["text"])

    def test_a_sheet_frame_pending_rows_first(self) -> None:
        company, due, done = column("Company", "text"), column("Due", "date"), column("Done", "checkbox")
        many = [column(f"Extra {n}", "text") for n in range(5)]
        sheet = SheetData("s-000000000001", "Internships", [company, due, done, *many],
                          rows=[Row(f"r-{n:08x}", {company.id: f"Co {n}", due.id: "2026-12-01"}) for n in range(20)],
                          pending=[proposals.sheet_row({company.id: "Globex", done.id: True}),
                                   proposals.sheet_row({company.id: "Initech"})])
        (message,) = frames.from_sheet("Internships", sheet, frame_id="sheet-1").messages
        protocol.check_message(message)
        body = message["body"]
        self.assertEqual(message["kind"], "table")
        self.assertEqual(len(body["columns"]), protocol.TABLE_COLUMNS_CAP)
        self.assertEqual(len(body["rows"]), protocol.TABLE_ROWS_CAP)
        self.assertEqual([row[0] for row in body["rows"][:3]], ["Globex", "Initech", "Co 0"])
        self.assertEqual(body["rows"][0][2], "Yes")
        self.assertEqual(body["source"], "SHEET · 20 ROWS")
        self.assertEqual(message["waits"], "2 rows wait on you")

    def test_a_sheet_without_columns_or_rows(self) -> None:
        empty = frames.from_sheet("New", SheetData("s-000000000002", "New"), frame_id="sheet-2").messages[0]
        protocol.check_message(empty)
        self.assertEqual((empty["kind"], empty["body"]["text"]), ("text", frames.SHEET_NO_COLUMNS))
        no_rows = SheetData("s-000000000003", "New", [column("A", "text")])
        message = frames.from_sheet("New", no_rows, frame_id="sheet-3").messages[0]
        protocol.check_message(message)
        self.assertEqual(message["body"]["text"], frames.SHEET_NO_ROWS)
        waiting = SheetData("s-000000000004", "New", pending=[proposals.sheet_columns([column("A", "text")]),
                                                             proposals.sheet_row({})])
        message = frames.from_sheet("New", waiting, frame_id="sheet-4").messages[0]
        protocol.check_message(message)


class PageListTests(unittest.TestCase):
    def js_list(self, name: str) -> list[str]:
        text = BOARD_JS.read_text(encoding="utf-8")
        found = re.search(rf"var {name} = \[(.*?)\];", text, re.DOTALL)
        assert found is not None, name
        return re.findall(r"'([a-z_]+)'", found.group(1))

    def test_the_page_s_lists_match_protocol(self) -> None:
        self.assertEqual(self.js_list("CANVAS_BUTTONS"), list(protocol.CANVAS_BUTTONS))
        self.assertIn("documents", protocol.CANVAS_BUTTONS)
        self.assertIn("documents", protocol.GO_WORDS)
        for name in ("SCREEN_BUTTONS", "TOP_ROW", "TOP_FOLD"):
            self.assertIn("documents", self.js_list(name), name)
        self.assertIn("documents: 'Documents'", BOARD_JS.read_text(encoding="utf-8"))
        self.assertEqual(canvas_ui._SCREEN_SLOTS["documents"], dock_mod.DOCS)
        protocol.check_message({"type": "canvas", "clock": "", "word": "", "tone": "holo", "approvals": 0,
                                "buttons": ["documents"], "briefing_new": False, "hold": "", "rec": "", "wake": "",
                                "meter": False})
        self.assertTrue(protocol.check_request("go", '{"to": "documents"}', set()))


class _Tile(QWidget):
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(canvas_view.SIDE_WIDTH, 120)


class PaneGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = canvas_view.CanvasPage()
        self.page.resize(1389, 868)
        self.page.show()
        self.addCleanup(self.page.deleteLater)
        self.pane = QWidget()
        settle()

    def test_beside_the_frames_and_wide(self) -> None:
        self.assertEqual(self.page.side_box(), (0, 0))
        self.page.set_doc_pane(self.pane)
        self.assertEqual(self.page.side_box(), (0, 0))                   # not shown yet
        self.page.show_doc_pane()
        rect = self.page.doc_rect()
        self.assertEqual((rect.x(), rect.y()), (canvas_view.EDGE, canvas_view.SIDE_TOP))
        self.assertEqual(rect.width(), round(1389 * canvas_view.DOC_SHARE))
        self.assertLess(rect.bottom(), self.page.column_rect().top())     # stops above the ask column
        self.assertEqual(self.page.side_box(), (rect.width(), rect.bottom() + 1 - canvas_view.SIDE_TOP))
        self.page.resize(800, 700)
        self.assertEqual(self.page.doc_rect().width(), canvas_view.DOC_MIN)
        self.page.resize(2400, 1300)
        self.assertEqual(self.page.doc_rect().width(), canvas_view.DOC_MAX)
        self.page.show_doc_pane(wide=True)
        self.assertEqual(self.page.doc_rect().width(), 2400 - 2 * canvas_view.EDGE)
        self.page.hide_doc_pane()
        self.assertEqual(self.page.side_box(), (0, 0))

    def test_under_the_side_tile_and_the_union(self) -> None:
        tile = _Tile()
        self.page.set_side_tile(tile)
        tile.show()
        self.page.place_side_tile()
        self.page.set_doc_pane(self.pane)
        self.page.show_doc_pane()
        rect = self.page.doc_rect()
        self.assertEqual(rect.y(), canvas_view.SIDE_TOP + 120 + canvas_view.DOC_GAP)
        width, height = self.page.side_box()
        self.assertEqual(width, max(rect.width(), canvas_view.SIDE_WIDTH))
        self.assertEqual(height, rect.bottom() + 1 - canvas_view.SIDE_TOP)

    def test_hidden_while_open_big(self) -> None:
        self.page.set_doc_pane(self.pane)
        self.page.show_doc_pane()
        self.assertTrue(self.page.doc_pane_shown())
        self.page.set_side_covered(True)
        self.assertTrue(self.page.doc_holder.isHidden())
        self.assertNotEqual(self.page.side_box(), (0, 0))                 # its room is kept
        self.page.set_side_covered(False)
        self.assertFalse(self.page.doc_holder.isHidden())


class PaneAppTests(CanvasApp):
    def test_a_document_frame_opens_the_pane_never_open_big(self) -> None:
        self.make(size=(1389, 868))
        board = self.started()
        docs = self.c.docs
        item_id, _ = docs.store.create_doc("Trip plan", DOC)
        docs.store.set_doc_pending(item_id, [proposals.doc_add("## Budget\n\n$200", "Packing")])
        docs.refresh_list()
        docs._show_frame(item_id)
        settle(4)
        sent = [m for m in board.of_type("frame") if m.get("title") == "Trip plan"]
        self.assertTrue(sent)
        frame_id = sent[-1]["id"]
        self.assertEqual(sent[-1]["waits"], "Change waits on you")
        bar = self.reading.command_bar
        bar.focus_input()
        settle()
        self.assertTrue(board.request("open", id=frame_id))
        settle(4)
        page = self.reading.canvas_page()
        self.assertTrue(docs.pane_shown())
        self.assertTrue(page.doc_pane_wide())
        self.assertEqual(self.c.look.desk.big.state(), "", "a document never opens big")
        self.assertTrue(docs.pane.holds_editors())
        self.assertEqual(self.reading.docs_stage.doc_editor.item_id, item_id)
        focused = QApplication.focusWidget()
        self.assertFalse(focused is not None and docs.pane.isAncestorOf(focused))   # the pane never took the keyboard
        self.assertTrue(bar.input.hasFocus() or focused is None)                    # ... it stays in the ask line
        self.assertNotEqual(self.last("canvas")["side"], [0, 0])
        # Keep in the pane: the frame is drawn again in its place, its chip gone
        before = len(board.of_type("frame"))
        self.reading.docs_stage.doc_editor.proposals_box.tiles[0].keep_button.click()
        settle(4)
        again = [m for m in board.of_type("frame")[before:] if m.get("id") == frame_id]
        self.assertTrue(again)
        self.assertEqual(again[-1]["waits"], "")
        # the pane's "Open in Documents": the Documents screen, the editors back on it
        docs.pane.screen_button.click()
        settle(4)
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_DOCS)
        self.assertTrue(self.reading.docs_stage.holds_editors())
        self.assertFalse(docs.pane_shown())

    def test_the_chip_opens_the_item_and_close_puts_the_editors_back(self) -> None:
        self.make(size=(1389, 868))
        board = self.started()
        docs = self.c.docs
        company = column("Company", "text")
        sheet_id, _ = docs.store.create_sheet("Internships", [company])
        sheet = docs.store.read_sheet(sheet_id)
        sheet.pending = [proposals.sheet_row({company.id: "Globex"})]
        docs.store.save_sheet(sheet_id, sheet)
        docs._show_frame(sheet_id)
        settle(4)
        frame_id = [m for m in board.of_type("frame") if m.get("title") == "Internships"][-1]["id"]
        self.assertTrue(board.request("go_approvals", id=frame_id))
        settle(4)
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_HOME)
        self.assertTrue(docs.pane_shown())
        self.assertEqual(self.reading.docs_stage.sheet_editor.item_id, sheet_id)
        docs.pane.close_button.click()
        settle(4)
        self.assertFalse(docs.pane_shown())
        self.assertTrue(self.reading.docs_stage.holds_editors())
        self.assertEqual(self.last("canvas")["side"], [0, 0])

    def test_the_documents_button_on_the_canvas(self) -> None:
        self.make(size=(1389, 868))
        self.started()
        canvas = self.canvas()
        canvas.sync()
        self.assertIn("documents", self.last("canvas")["buttons"])
        self.assertTrue(canvas.on_go("documents"))
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_DOCS)


if __name__ == "__main__":
    unittest.main()
