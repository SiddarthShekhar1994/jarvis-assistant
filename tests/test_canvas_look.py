"""The canvas look (PLAN-canvas part 4, checkpoint 1): while Home is the canvas, the other screens (Briefing, Approvals,
Agents, Live, Memory, Settings, the History and Day drawers) wear the canvas's frame style - black ground, square tiles
with a bright edge and an inner glow, corner ticks, amber only for what needs the owner, the mock's mono caps buttons, a
boot where a panel appears - and without the canvas everything is exactly as before.

Qt runs offscreen in this process with the bundled fonts and shows nothing.
"""
from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import alive, holo, hud, ui
from briefing_reader.memory_view import PanelFrame
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas_look", "-platform", "offscreen"])
    fonts()


def paint(fn, size=(120, 80)) -> QImage:
    image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        fn(painter, QRectF(0, 0, *size))
    finally:
        painter.end()
    return image


def rendered(widget) -> QImage:
    """The widget's own pixels on a transparent image (no window background under them)."""
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def amberish(color: QColor) -> bool:
    return color.alpha() > 60 and color.red() > 150 and 90 < color.green() < 215 and color.blue() < 150 \
        and color.red() > color.blue() + 80


def colors(image: QImage) -> list[QColor]:
    return [image.pixelColor(x, y) for y in range(0, image.height(), 2) for x in range(0, image.width(), 2)]


class LookSwitch(unittest.TestCase):
    def tearDown(self) -> None:
        holo.set_canvas_look(False)

    def on(self) -> None:
        holo.set_canvas_look(True)


class GroundAndTileTests(LookSwitch):

    def test_the_look_is_off_until_the_canvas_turns_it_on(self) -> None:
        self.assertFalse(holo.canvas_look())
        self.assertNotIn("rgba(175, 240, 255, 204)", hud.stylesheet())

    def test_the_ground_is_the_canvas_black_and_the_cache_follows_the_look(self) -> None:
        cache = holo.GroundCache()
        before = cache.pixmap(QSize(200, 120)).toImage().pixelColor(100, 54)
        self.assertNotEqual(before.name(), "#000000")            # the hologram's void and grid
        self.on()
        after = cache.pixmap(QSize(200, 120)).toImage()
        self.assertEqual({after.pixelColor(x, y).name() for x in (0, 50, 100, 199) for y in (0, 54, 119)},
                         {"#000000"})

    def test_a_tile_is_square_with_a_bright_edge_and_an_inner_glow(self) -> None:
        old = paint(lambda p, r: holo.paint_tile(p, r))
        self.assertEqual(old.pixelColor(0, 0).alpha(), 0)       # the hologram's tile: a rounded corner
        self.on()
        tile = paint(lambda p, r: holo.paint_tile(p, r))
        corner, edge = tile.pixelColor(0, 0), tile.pixelColor(60, 0)
        self.assertGreater(corner.alpha(), 200)                  # square
        self.assertGreater(edge.blue(), 150)                     # the bright cyan edge
        near, middle = tile.pixelColor(60, 3), tile.pixelColor(60, 40)
        self.assertGreater(near.blue(), middle.blue())           # the glow falls off inward

    def test_amber_only_for_what_needs_the_owner(self) -> None:
        self.on()
        for state in (holo.TILE_DEFAULT, holo.TILE_FOCUSED, holo.TILE_INFO, holo.TILE_DONE, holo.TILE_STEEL):
            image = paint(lambda p, r, s=state: holo.paint_tile(p, r, s))
            self.assertFalse(any(amberish(c) for c in colors(image)), state)
        for state in (holo.TILE_NEEDS, holo.TILE_NEEDS_FOCUS, holo.TILE_NEEDS_SELECTED):
            image = paint(lambda p, r, s=state: holo.paint_tile(p, r, s))
            self.assertTrue(amberish(image.pixelColor(60, 0)), state)

    def test_corner_ticks_on_the_tile_in_focus(self) -> None:
        self.on()
        rest = paint(lambda p, r: holo.paint_tile(p, r, holo.TILE_DEFAULT))
        focus = paint(lambda p, r: holo.paint_tile(p, r, holo.TILE_FOCUSED))
        # a tick's leg runs along the edge from the corner, 1.5 px wide: inside it the focused tile is near white
        self.assertGreater(focus.pixelColor(0, 6).lightness(), rest.pixelColor(0, 6).lightness() + 40)
        self.assertGreater(focus.pixelColor(0, 6).lightness(), 130)

    def test_the_paint_tile_result_is_still_the_tiles_text_style(self) -> None:
        self.on()
        styles: list = []
        paint(lambda p, r: styles.append(holo.paint_tile(p, r, holo.TILE_NEEDS)))
        self.assertEqual(styles, [holo.TILE_STYLES[holo.TILE_NEEDS]])   # the callers' kicker and title colours

    def test_a_screen_panel_is_a_canvas_frame(self) -> None:
        panel = PanelFrame()
        panel.resize(160, 100)
        old = rendered(panel)
        self.on()
        new = rendered(panel)
        self.assertLess(old.pixelColor(0, 0).alpha(), 60)        # the hologram's rounded panel
        self.assertGreater(new.pixelColor(0, 0).alpha(), 200)    # square, its tick on the corner
        self.assertGreater(new.pixelColor(80, 0).blue(), 150)
        panel.deleteLater()


class ButtonTests(LookSwitch):

    def test_buttons_take_the_mocks_mono_caps_words_and_keep_their_text(self) -> None:
        button = holo.HoloButton("Open config.toml", holo.GHOST)
        before = button.sizeHint()
        self.assertEqual(button.shown_text(), "Open config.toml")
        self.on()
        self.assertEqual(button.text(), "Open config.toml")       # what the button is (and says to a reader)
        self.assertEqual(button.shown_text(), "OPEN CONFIG.TOML")
        self.assertEqual(button._font().family(), hud.MONO_FAMILY)  # noqa: SLF001
        self.assertNotEqual(button.sizeHint(), before)             # measured again in its new words
        link = holo.HoloButton("How", holo.LINK_CYAN)
        self.assertEqual(link.shown_text(), "How")                 # a link stays a link
        button.deleteLater()
        link.deleteLater()

    def test_a_button_is_square_with_its_glow(self) -> None:
        button = holo.HoloButton("Deny", holo.GHOST)
        button.resize(button.sizeHint())
        m = holo.HoloButton.MARGIN
        old = rendered(button).pixelColor(m + 1, m)
        self.on()
        button.resize(button.sizeHint())
        image = rendered(button)
        self.assertLess(old.alpha(), 120)                          # rounded before
        self.assertGreater(image.pixelColor(m + 1, m).alpha(), 120)    # square now
        self.assertGreater(image.pixelColor(m - 2, button.height() // 2).alpha(), 0)   # the glow just outside it
        button.deleteLater()

    def test_the_commit_button_is_amber_and_a_plain_one_is_not(self) -> None:
        self.on()
        approve = paint(lambda p, r: holo.paint_button_face(p, r.adjusted(4, 4, -4, -4), holo.AMBER_COMMIT,
                                                            text="Approve"))
        deny = paint(lambda p, r: holo.paint_button_face(p, r.adjusted(4, 4, -4, -4), holo.GHOST, text="Deny"))
        self.assertTrue(amberish(approve.pixelColor(60, 4)))
        self.assertFalse(any(amberish(c) for c in colors(deny)))
        self.assertGreater(approve.pixelColor(5, 4).alpha(), 120)  # square

    def test_the_native_widgets_follow_in_the_stylesheet(self) -> None:
        self.on()
        sheet = hud.stylesheet()
        canvas_part = sheet[len(sheet) - len(hud._CANVAS_STYLESHEET):]       # noqa: SLF001 - after the base rules
        self.assertEqual(canvas_part, hud._CANVAS_STYLESHEET)                 # noqa: SLF001
        for rule in ("QPushButton {", "QMenu {", "QScrollBar::handle:vertical {", "QToolTip {"):
            self.assertIn(rule, canvas_part)
        self.assertIn("border-radius: 1px", canvas_part)
        holo.set_canvas_look(False)
        self.assertNotIn("QPushButton {", hud.stylesheet())


class BootTests(LookSwitch):

    def setUp(self) -> None:
        holo.set_reduced_motion(False)
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.addCleanup(holo.set_reduced_motion, None)

    def test_a_panel_boots_as_a_frame_its_outline_traced_then_its_light_fades(self) -> None:
        self.on()
        from PySide6.QtWidgets import QWidget
        stage = QWidget()
        stage.resize(400, 300)
        transition = alive.ScreenTransition(stage)
        transition._level = holo.MOTION_LIVELY                     # noqa: SLF001 - as play() sets it
        transition._elapsed = 0.0                                  # noqa: SLF001
        self.assertEqual(transition.boot_state(0)[2], 0.0)          # not yet begun
        traces, lights = [], []
        for t in range(0, transition.total_ms() + 1, 40):
            transition._elapsed = float(t)                         # noqa: SLF001
            trace, _scan, light = transition.boot_state(0)
            traces.append(trace)
            lights.append(light)
        self.assertEqual(traces, sorted(traces))                   # the outline only ever grows
        self.assertAlmostEqual(traces[-1], 1.0)
        self.assertGreater(max(lights), 0.99)
        self.assertEqual(lights[-1], 0.0)                          # ... and its light is gone when the screen is
        transition._elapsed = float(holo.ASSEMBLE_STAGGER_MS)      # noqa: SLF001
        self.assertGreater(transition.boot_state(0)[0], transition.boot_state(1)[0])   # one after another
        stage.deleteLater()

    def test_the_look_lengthens_the_assembly_by_the_fade_and_motion_off_is_a_plain_cut(self) -> None:
        from PySide6.QtWidgets import QWidget
        stage = QWidget()
        transition = alive.ScreenTransition(stage)
        plain = transition.total_ms(holo.MOTION_LIVELY)
        self.on()
        self.assertEqual(transition.total_ms(holo.MOTION_LIVELY), plain + alive.BOOT_FADE_MS)
        self.assertEqual(transition.total_ms(holo.MOTION_OFF), 0)
        holo.set_reduced_motion(True)                              # Windows' "Show animations" off
        stage.resize(300, 200)
        stage.show()
        page = QWidget(stage)
        page.resize(300, 200)
        page.show()
        self.assertFalse(transition.play(page))                    # nothing plays: the screen just changes
        stage.deleteLater()

    def test_what_a_boot_traces_is_what_a_panel_shows(self) -> None:
        from PySide6.QtWidgets import QLabel, QWidget
        page = QWidget()
        page.resize(400, 400)
        column = QWidget(page)
        column.setGeometry(20, 20, 200, 360)
        label = QLabel("A row", column)
        label.setGeometry(10, 10, 120, 20)
        self.assertEqual(alive.boot_rect(column, page), QRectF(30, 30, 120, 20))
        empty = QWidget(page)
        empty.setGeometry(240, 20, 100, 50)
        self.assertEqual(alive.boot_rect(empty, page), QRectF(240, 20, 100, 50))
        page.deleteLater()


class AppTests(unittest.TestCase):

    def test_the_canvas_turns_the_look_on_and_the_fallback_turns_it_off(self) -> None:
        boards: list = []
        harness = AppHarness(self, lines=(), board_factory=board_fakes.stand_in_factory(boards, canvas=True))
        harness.reading(autoplay=False)
        reading = harness.c.window.reading
        settle(4)
        self.assertTrue(reading.canvas_on())
        self.assertTrue(holo.canvas_look())
        reading.set_tab(ui.SCREEN_SETTINGS)                       # a screen over the canvas wears it
        settle(2)
        self.assertTrue(holo.canvas_look())
        reading.set_canvas(False)                                  # the page could not start: the old look
        self.assertFalse(holo.canvas_look())
        self.assertNotIn("QPushButton {", QApplication.instance().styleSheet())

    def test_without_the_canvas_nothing_changes(self) -> None:
        harness = AppHarness(self, lines=())
        harness.reading(autoplay=False)
        settle(2)
        self.assertFalse(harness.c.window.reading.canvas_on())
        self.assertFalse(holo.canvas_look())

    def test_the_harness_leaves_no_look_behind(self) -> None:
        holo.set_canvas_look(True)
        harness = AppHarness(self, lines=())
        harness.close()
        self.assertFalse(holo.canvas_look())


if __name__ == "__main__":
    unittest.main()
