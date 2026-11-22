"""The finish of the canvas look (PLAN-canvas part 4, checkpoint 3): the second screen's wall in the canvas frame style
(OLED care's: no fill, no glow; its counts-only rule unchanged), square status badges, screen titles in caps, Memory's
note field and note editor, Follow-ups' rows without the thick coloured bar, and the desktop HUD's bar, chips, buttons
and glows and the toast's frame as canvas frames. Without the canvas look every one of them is exactly as before.

Qt runs offscreen in this process with the bundled fonts and shows nothing.
"""
from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import config, followups_view, holo, hud, hud_overlay, memory_view, second_screen, states_view
from briefing_reader.board import glance
from tests import second_screen_fakes as ssf

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas_finish", "-platform", "offscreen"])
    hud.load_fonts(config.PROJECT_ROOT / "fonts")


def black(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#000000"))
    return image


def rendered(widget: QWidget) -> QImage:
    """The widget's own pixels on a transparent image."""
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def luma(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def amberish(color: QColor) -> bool:
    return color.red() > 150 and 90 < color.green() < 215 and color.blue() < 150 and color.red() > color.blue() + 80


class LookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(holo.set_canvas_look, False)

    def on(self) -> None:
        holo.set_canvas_look(True)


class WallTests(LookCase):
    """The second screen's wall."""

    def tile(self, frame: dict) -> QImage:
        image = black(300, 200)
        painter = QPainter(image)
        try:
            glance.paint_tile(painter, QRectF(0, 0, 300, 200), frame, private=True, screen=glance.SECOND)
        finally:
            painter.end()
        return image

    def frames(self) -> tuple[dict, dict]:
        found = [frame for frame, _src in ssf.demo_frames()]
        plain = next(frame for frame in found if not frame.get("waits") and frame.get("status", "ok") == "ok")
        waits = next(frame for frame in found if frame.get("waits"))
        return plain, waits

    def test_a_wall_tile_is_square_with_the_frames_edge_and_no_glow(self) -> None:
        plain, _waits = self.frames()
        old = self.tile(plain)
        self.assertLess(luma(old.pixelColor(0, 0)), 10)                     # the rounded corner
        self.on()
        new = self.tile(plain)
        self.assertGreater(luma(new.pixelColor(0, 0)), 100)                 # square
        self.assertGreater(luma(new.pixelColor(150, 0)), luma(old.pixelColor(150, 0)) + 60)   # the bright edge
        self.assertLessEqual(max(luma(new.pixelColor(150, y)) for y in range(3, 8)), 1)        # no glow inside
        cap = luma(QColor(holo.OLED_INK))
        self.assertLessEqual(max(luma(new.pixelColor(x, 0)) for x in range(0, 300, 7)), cap)   # OLED care

    def test_what_waits_is_amber_with_its_two_corner_ticks(self) -> None:
        plain, waits = self.frames()
        self.on()
        image = self.tile(waits)
        self.assertTrue(amberish(image.pixelColor(150, 0)))
        self.assertTrue(amberish(image.pixelColor(0, 6)))                    # a tick's leg on the top-left corner
        self.assertFalse(amberish(self.tile(plain).pixelColor(150, 0)))

    def test_the_board_s_own_fallback_tiles_keep_their_look(self) -> None:
        plain, _waits = self.frames()
        self.on()
        image = black(300, 200)
        painter = QPainter(image)
        try:
            glance.paint_tile(painter, QRectF(0, 0, 300, 200), plain)        # screen BOARD: the fallback's tiles
        finally:
            painter.end()
        self.assertLess(luma(image.pixelColor(0, 0)), 10)

    def test_the_wall_still_shows_counts_only(self) -> None:
        marker = "zq9canvasmark"
        found = ssf.demo_frames(marker)

        def texts() -> list[str]:
            display = second_screen.SecondScreen(native=ssf.FakeNative(), now=lambda: ssf.NOW)
            self.addCleanup(display.deleteLater)
            display.resize(1920, 1080)
            display.set_state(ssf.demo_state(found))
            display.freeze(5.0)
            image = black(1920, 1080)
            painter = ssf.RecordingPainter(image)
            try:
                display.paint_display(painter, None, now=ssf.NOW, at=None)
            finally:
                painter.end()
            return painter.texts

        before = texts()
        self.on()
        after = texts()
        self.assertEqual(after, before)                                        # the same words, nothing more
        self.assertFalse(any(marker in text for text in after))
        self.assertIn(glance.HIDDEN, after)


class ScreenTests(LookCase):

    def test_a_status_badge_is_square_in_the_look(self) -> None:
        def badge() -> QImage:
            image = black(120, 40)
            painter = QPainter(image)
            try:
                holo.paint_status_badge(painter, QPointF(0, 20), "Live", holo.TONE_CYAN)
            finally:
                painter.end()
            return image

        old = badge()
        self.on()
        new = badge()
        self.assertGreater(luma(new.pixelColor(0, 10)), luma(old.pixelColor(0, 10)) + 20)   # its corner, square

    def test_a_screen_title_is_in_caps_and_comes_back(self) -> None:
        label = memory_view.body_label("Voice", "screen_title", holo.INK_1)
        self.addCleanup(label.deleteLater)
        self.assertEqual(label.font().capitalization(), QFont.Capitalization.MixedCase)
        self.on()
        self.assertEqual(label.font().capitalization(), QFont.Capitalization.AllUppercase)
        self.assertEqual(label.font().weight(), QFont.Weight.Bold)
        self.assertEqual(label.text(), "Voice")                                # its words are the same
        holo.set_canvas_look(False)
        self.assertEqual(label.font().capitalization(), QFont.Capitalization.MixedCase)
        self.assertEqual(label.font().weight(), QFont.Weight(holo.ROLES["screen_title"].weight))
        body = memory_view.body_label("Kept only on this PC.", "body_sm", holo.INK_3)
        self.addCleanup(body.deleteLater)
        self.on()
        self.assertEqual(body.font().capitalization(), QFont.Capitalization.MixedCase)   # only titles

    def test_a_title_made_while_the_look_is_on_takes_it_at_once(self) -> None:
        self.on()
        label = memory_view.body_label("Look", "screen_title", holo.INK_1)
        self.addCleanup(label.deleteLater)
        self.assertEqual(label.font().capitalization(), QFont.Capitalization.AllUppercase)

    def test_memory_s_note_field_is_square(self) -> None:
        field = memory_view.AddField("Add a note...")
        self.addCleanup(field.deleteLater)
        field.resize(400, memory_view.FIELD_HEIGHT)
        old = rendered(field)
        self.on()
        new = rendered(field)
        self.assertLess(old.pixelColor(0, 0).alpha(), 40)                      # the pill's round end
        self.assertGreater(new.pixelColor(0, 0).alpha(), 120)                  # square (its corner pixel half lit)
        self.assertGreater(new.pixelColor(200, 0).blue(), 140)                 # its cyan edge

    def test_a_note_s_editor_made_in_the_look_is_square(self) -> None:
        self.assertIn(f"border-radius: {holo.RADIUS_BUTTON}px", memory_view.NoteEdit().styleSheet())
        self.on()
        self.assertIn("border-radius: 1px", memory_view.NoteEdit().styleSheet())

    def test_a_follow_up_row_has_no_thick_bar_and_the_focal_row_is_a_frame_in_its_tone(self) -> None:
        info = hud.FollowupRowInfo("w1", hud.FOLLOWUP_WAITING, "Dana Whitfield", "4d", "Q4 budget numbers")
        row = followups_view.FollowupTile(info)
        self.addCleanup(row.deleteLater)
        row.resize(500, 60)
        old = rendered(row)
        self.assertTrue(amberish(old.pixelColor(2, 30)))                       # the 3 px amber bar
        self.on()
        new = rendered(row)
        self.assertFalse(amberish(new.pixelColor(2, 30)))                      # 1 px at most
        row.set_expanded(True)
        row.resize(500, 120)
        focal = rendered(row)
        self.assertTrue(amberish(focal.pixelColor(250, 0)))                    # the frame's edge in the row's tone


    def test_the_meetings_column_is_centred_over_the_canvas_and_left_without_it(self) -> None:
        from briefing_reader import meetings_view

        stage = meetings_view.MeetingsStage()
        self.addCleanup(stage.deleteLater)
        stage.resize(1920, 900)
        stage.show()
        self.addCleanup(stage.hide)
        QApplication.processEvents()
        holder = stage.pages.parentWidget()
        self.assertLess(holder.geometry().left(), 100)                         # the hologram look: at the left
        stage.hide()
        self.on()
        stage.show()
        QApplication.processEvents()
        middle = holder.geometry().center().x()
        self.assertLess(abs(middle - stage.width() / 2), 40)                   # the canvas look: centred


class HudAndToastTests(LookCase):

    def test_the_hud_bar_is_a_square_canvas_frame_and_its_glows_go_square(self) -> None:
        win = hud_overlay.HudWindow(native=hud_overlay.NoNative())
        self.addCleanup(win.deleteLater)
        bar = win.bar_rect_local()
        old = win.background().toImage()
        self.assertEqual(win.glows()[0].radius, hud_overlay.BAR_RADIUS)
        self.on()
        new = win.background().toImage()
        dpr = new.devicePixelRatio()
        corner = (round((bar.right() - 0.5) * dpr), round((bar.bottom() - 0.5) * dpr))
        self.assertLess(old.pixelColor(*corner).alpha(), 60)                   # the rounded corner
        self.assertEqual(new.pixelColor(*corner).alpha(), 255)                 # square and opaque
        self.assertEqual(win.glows()[0].radius, holo.CANVAS_RADIUS)
        middle = new.pixelColor(round(bar.center().x() * dpr), round((bar.top() + 0.5) * dpr))
        self.assertGreater(middle.blue(), 150)                                 # the frames' bright edge

    def test_a_chip_is_square_and_amber_only_for_what_waits(self) -> None:
        chip = hud_overlay.HudChip(hud_overlay.CHIP_APPROVALS)
        self.addCleanup(chip.deleteLater)
        chip.setText("2 need your OK")
        chip.resize(chip.sizeHint())
        old = rendered(chip)
        self.on()
        new = rendered(chip)
        self.assertLess(old.pixelColor(0, 0).alpha(), 40)
        self.assertGreater(new.pixelColor(0, 0).alpha(), 200)
        self.assertTrue(amberish(new.pixelColor(chip.width() // 2, 0)))
        cyan = hud_overlay.HudChip(hud_overlay.CHIP_BRIEFING)
        self.addCleanup(cyan.deleteLater)
        cyan.setText("Briefing ready")
        cyan.resize(cyan.sizeHint())
        self.assertFalse(amberish(rendered(cyan).pixelColor(cyan.width() // 2, 0)))

    def test_every_hud_fill_stays_opaque(self) -> None:
        self.on()
        chip = hud_overlay.HudChip(hud_overlay.CHIP_BRIEFING)
        self.addCleanup(chip.deleteLater)
        chip.setText("Briefing ready")
        chip.resize(chip.sizeHint())
        image = rendered(chip)
        inside = [image.pixelColor(x, y) for x in range(2, chip.width() - 2, 5) for y in range(2, chip.height() - 2, 5)]
        self.assertTrue(all(color.alpha() == 255 for color in inside))

    def test_the_toast_is_a_canvas_frame(self) -> None:
        toast = states_view.Toast()
        self.addCleanup(toast.deleteLater)
        old = rendered(toast)
        self.on()
        new = rendered(toast)
        self.assertLess(old.pixelColor(0, 0).alpha(), 40)
        self.assertGreater(new.pixelColor(0, 0).alpha(), 200)                  # square, its tick on the corner
        self.assertGreater(new.pixelColor(toast.width() // 2, 0).blue(), 150)

    # Canvas part 5A: the HUD's result cards and the HUD key's keycaps (part 4's leftovers).

    def card(self, tone: str) -> hud_overlay.ResultCard:
        card = hud_overlay.ResultCard(native=hud_overlay.NoNative())
        self.addCleanup(card.deleteLater)
        card.set_content(hud_overlay.CardContent("FILE WORD DOCUMENT", "Essay draft.docx", "Documents", tone=tone))
        return card

    def test_a_result_card_is_a_square_opaque_canvas_frame_and_its_glow_goes_square(self) -> None:
        card = self.card(hud_overlay.TONE_CYAN)
        old = rendered(card)
        self.assertEqual(card.glow().radius, hud_overlay.CARD_RADIUS)
        self.on()
        card.update()
        new = rendered(card)
        self.assertLess(old.pixelColor(1, 1).alpha(), 60)                     # the rounded corner
        self.assertEqual(new.pixelColor(1, 1).alpha(), 255)                    # square and opaque
        self.assertEqual(card.glow().radius, holo.CANVAS_RADIUS)
        self.assertGreater(new.pixelColor(card.width() // 2, 0).blue(), 150)   # the frames' bright edge
        inside = [new.pixelColor(x, y) for x in range(2, card.width() - 2, 9) for y in range(2, card.height() - 2, 9)]
        self.assertTrue(all(color.alpha() == 255 for color in inside))         # a window that takes clicks: opaque
        holo.set_canvas_look(False)
        card.update()
        self.assertEqual(rendered(card), old)                                  # off: exactly as before

    def test_an_amber_card_keeps_its_amber_edge(self) -> None:
        card = self.card(hud_overlay.TONE_AMBER)
        self.on()
        new = rendered(card)
        self.assertTrue(amberish(new.pixelColor(card.width() // 2, 0)))
        self.assertFalse(amberish(rendered(self.card(hud_overlay.TONE_CYAN)).pixelColor(card.width() // 2, 0)))

    def test_briefing_s_and_the_plan_s_120_px_rings_wear_the_badge_in_the_look(self) -> None:
        # Canvas part 5A: part 4's leftover rings, where the badge fits their box (its compact form).
        from briefing_reader import briefing_view, plan_view

        def button(text: str):
            return briefing_view.TransportButton(text, holo.GO)

        briefing = briefing_view.BriefingStage(hud.SpeechLabel(), button("Play"), button("Skip"),
                                               holo.HoloButton("Everything"), button("Notion"),
                                               briefing_view.SectionsButton())
        plan = plan_view.PlanStage()
        for stage in (briefing, plan):
            self.addCleanup(stage.deleteLater)
            with self.subTest(stage=type(stage).__name__):
                orb = stage.orb
                self.assertTrue(orb.badge_form())
                self.assertFalse(orb.badge_shown())                            # without the look: the old ring
                self.on()
                self.assertTrue(orb.badge_shown())
                self.assertEqual(orb.badge_tier(), "compact")
                holo.set_canvas_look(False)

    def test_the_hud_key_s_keycaps_are_square(self) -> None:
        caps = hud_overlay._KeyCaps("Ctrl+Space")                              # noqa: SLF001
        self.addCleanup(caps.deleteLater)
        caps.resize(caps.sizeHint())
        old = rendered(caps)
        self.on()
        new = rendered(caps)
        self.assertLess(old.pixelColor(0, 0).alpha(), 40)                     # the rounded corner
        self.assertGreater(new.pixelColor(0, 0).alpha(), 40)                  # a square cap's corner
        holo.set_canvas_look(False)
        self.assertEqual(rendered(caps), old)


if __name__ == "__main__":
    unittest.main()
