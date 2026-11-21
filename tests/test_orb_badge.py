"""The orb's flat JARVIS badge outside the canvas (PLAN-canvas part 4, checkpoint 2): while Home is the canvas, the
desktop HUD's ring, the toast's and the second screen's wear the canvas orb's badge - its rings face on, the name in the
middle, the amber needs-you arc on the outer ring, each state as the canvas draws it - cheaply (still parts made once),
and OLED care's on the second screen. Without the canvas look, or on a ring that did not ask for it, nothing changes.

Qt runs offscreen in this process with the bundled fonts and shows nothing.
"""
from __future__ import annotations

import math
import statistics
import time
import unittest

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from briefing_reader import config, holo, hud, hud_overlay, orb, second_screen, states_view
from tests import second_screen_fakes as ssf

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_orb_badge", "-platform", "offscreen"])
    hud.load_fonts(config.PROJECT_ROOT / "fonts")


def picture(ring: orb.OrbWidget, frame: orb.OrbFrame | None = None) -> QImage:
    """The ring on black, as paint_into draws it (the second screen's way, and the widget's own paint)."""
    image = QImage(ring.width(), ring.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    try:
        ring.paint_into(painter, QPointF(0.0, 0.0), frame)
    finally:
        painter.end()
    return image


def mini_picture(mini: orb.MiniOrb) -> QImage:
    image = QImage(mini.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#000000"))
    mini.render(image)
    return image


def luma(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def amberish(color: QColor) -> bool:
    return color.red() > 150 and 90 < color.green() < 215 and color.blue() < 150 and color.red() > color.blue() + 80


def cyanish(color: QColor) -> bool:
    return color.blue() > 170 and color.green() > 150 and color.red() < 110


def pixels(image: QImage, step: int = 1):
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            yield x, y, image.pixelColor(x, y)


def lit_in_ring(image: QImage, centre: QPointF, inner: float, outer: float, floor: float = 60.0) -> int:
    """Pixels brighter than ``floor`` between ``inner`` and ``outer`` px from ``centre``."""
    return sum(1 for x, y, c in pixels(image)
               if inner <= math.hypot(x + 0.5 - centre.x(), y + 0.5 - centre.y()) <= outer and luma(c) > floor)


class BadgeCase(unittest.TestCase):
    """The canvas look on, motion off (every ring at its own place) unless a test says otherwise."""

    def setUp(self) -> None:
        holo.set_reduced_motion(False)
        holo.set_motion_level(holo.MOTION_OFF)
        holo.set_canvas_look(True)
        self.addCleanup(holo.set_canvas_look, False)
        self.addCleanup(holo.set_reduced_motion, None)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)

    def ring(self, px: int = hud_overlay.ORB_PX, state: str = orb.IDLE, **look) -> orb.OrbWidget:
        ring = orb.OrbWidget(px)
        self.addCleanup(ring.deleteLater)
        ring.set_badge_form(True)
        ring.set_look(state, **look)
        ring.settle()
        return ring


class WhoWearsItTests(BadgeCase):

    def test_the_hud_the_toast_and_the_second_screen_ask_for_the_badge(self) -> None:
        win = hud_overlay.HudWindow(native=hud_overlay.NoNative())
        toast = states_view.Toast()
        display = second_screen.SecondScreen(native=ssf.FakeNative(), now=lambda: ssf.NOW)
        for widget in (win, toast, display):
            self.addCleanup(widget.deleteLater)
        self.assertTrue(win.orb.badge_shown())
        self.assertTrue(toast.mini.badge_shown())
        self.assertTrue(display.ring.badge_shown())

    def test_without_the_canvas_look_the_ring_is_exactly_as_it_was(self) -> None:
        holo.set_canvas_look(False)
        asked, plain = self.ring(), orb.OrbWidget(hud_overlay.ORB_PX)
        self.addCleanup(plain.deleteLater)
        plain.settle()
        self.assertFalse(asked.badge_shown())
        self.assertEqual(picture(asked), picture(plain))
        mini, old = orb.MiniOrb(), orb.MiniOrb()
        mini.set_badge_form(True)
        self.assertEqual(mini_picture(mini), mini_picture(old))

    def test_a_ring_that_did_not_ask_keeps_the_ring_on_the_canvas(self) -> None:
        plain = orb.OrbWidget(orb.COMPACT)              # Briefing's and the plan's rings
        self.addCleanup(plain.deleteLater)
        self.assertFalse(plain.badge_shown())
        self.assertFalse(plain.sample().badge)

    def test_the_look_turning_off_brings_the_ring_back(self) -> None:
        ring = self.ring()
        self.assertTrue(ring.sample().badge)
        holo.set_canvas_look(False)
        self.assertFalse(ring.sample().badge)


class FormTests(BadgeCase):

    def test_the_name_is_in_the_middle_bright_and_readable(self) -> None:
        ring = self.ring()
        image = picture(ring)
        centre = ring.orb_rect().center()
        middle = [c for x, y, c in pixels(image) if abs(x - centre.x()) < 18 and abs(y - centre.y()) < 5]
        self.assertGreater(max(luma(c) for c in middle), 225)   # the name's white
        font = orb._badge_name_font(ring.badge_scale(), ring.badge_tier())
        self.assertGreaterEqual(font.pixelSize(), orb.BADGE_NAME_MIN)
        inner = next(r for r in orb.BADGE_RINGS[ring.badge_tier()] if r.name == "inner")
        from PySide6.QtGui import QFontMetricsF
        width = QFontMetricsF(font).horizontalAdvance(orb.BADGE_NAME)
        self.assertLess(width, 2 * (inner.r - inner.w / 2) * ring.badge_scale())   # inside the inner ring

    def test_the_badge_stays_inside_its_widget_and_the_second_screens_ring_box(self) -> None:
        for px in (hud_overlay.ORB_PX, second_screen.RING):
            ring = self.ring(px, orb.LISTENING)
            ring.set_voice_listening(True)
            ring.set_level(1.0)                     # the ticks at their widest
            image = picture(ring)
            centre = ring.orb_rect().center()
            far = max(math.hypot(x + 0.5 - centre.x(), y + 0.5 - centre.y()) for x, y, c in pixels(image, 2)
                      if luma(c) > 8)
            self.assertLessEqual(far, ring.width() / 2, px)
            if px == second_screen.RING:            # the display repaints only the ring's box (its dirty rect)
                self.assertLessEqual(far, second_screen.ring_box(px) / 2 + 1)

    def test_amber_only_while_something_needs_the_owner_on_the_outer_ring_above(self) -> None:
        for state in (orb.IDLE, orb.THINKING, orb.SPEAKING, orb.WAITING):
            self.assertFalse(any(amberish(c) for _x, _y, c in pixels(picture(self.ring(state=state)), 2)), state)
        ring = self.ring(state=orb.NEEDS)
        image = picture(ring)
        centre = ring.orb_rect().center()
        outer = orb.BADGE_RINGS[orb.BADGE_COMPACT][1].r * ring.badge_scale()
        amber = [(x, y) for x, y, c in pixels(image) if amberish(c)]
        self.assertGreater(len(amber), 20)
        self.assertTrue(all(y < centre.y() for _x, y in amber))            # the arc at the top (-150 to -30 deg)
        self.assertTrue(all(abs(math.hypot(x + 0.5 - centre.x(), y + 0.5 - centre.y()) - outer) < 5 for x, y in amber))
        muted = picture(self.ring(state=orb.NEEDS, muted=True))
        self.assertFalse(any(amberish(c) for _x, _y, c in pixels(muted, 2)))   # muted: no arc, as the canvas

    def test_the_offline_voice_is_steel(self) -> None:
        live = picture(self.ring())
        steel = picture(self.ring(offline=True))
        self.assertTrue(any(cyanish(c) for _x, _y, c in pixels(live, 2)))
        self.assertFalse(any(cyanish(c) for _x, _y, c in pixels(steel, 2)))

    def test_muted_is_dim_with_a_slash(self) -> None:
        idle, muted = self.ring(), self.ring(muted=True)
        light = sum(luma(c) for _x, _y, c in pixels(picture(idle), 2))
        dim = sum(luma(c) for _x, _y, c in pixels(picture(muted), 2))
        self.assertLess(dim, 0.7 * light)
        centre = muted.orb_rect().center()
        r = 120 * muted.badge_scale()               # on the slash (-45 deg), between the rings

        def near(image: QImage) -> float:
            x = round(centre.x() + r * math.cos(math.radians(-45)))
            y = round(centre.y() + r * math.sin(math.radians(-45)))
            return max(luma(image.pixelColor(x + dx, y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

        self.assertGreater(near(picture(muted)), near(picture(idle)) + 30)

    def test_listening_the_ticks_follow_the_mic(self) -> None:
        ring = self.ring(second_screen.RING, orb.LISTENING)
        ring.set_voice_listening(True)
        k = ring.badge_scale()
        centre = ring.orb_rect().center()
        ring.set_level(0.0)
        quiet = lit_in_ring(picture(ring), centre, 190 * k, 206 * k)
        ring.set_level(1.0)
        loud = lit_in_ring(picture(ring), centre, 190 * k, 206 * k)
        self.assertLess(quiet, 10)
        self.assertGreater(loud, 150)

    def test_speaking_the_main_ring_pulses_with_the_voice(self) -> None:
        ring = self.ring(second_screen.RING, orb.SPEAKING)
        k = ring.badge_scale()
        centre = ring.orb_rect().center()
        frame = ring.sample()
        frame.level = 0.0
        soft = lit_in_ring(picture(ring, frame), centre, 136 * k, 142 * k, floor=120)
        frame.level = 1.0
        loud = lit_in_ring(picture(ring, frame), centre, 136 * k, 142 * k, floor=120)
        self.assertGreater(loud, soft + 100)

    def test_recording_takes_the_outer_ring(self) -> None:
        ring = self.ring()
        ring.set_recording(True)
        image = QImage(ring.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#000000"))
        ring.render(image)
        centre = ring.orb_rect().center()
        outer = orb.BADGE_RINGS[orb.BADGE_COMPACT][0].r * ring.badge_scale()
        top = image.pixelColor(round(centre.x()), round(centre.y() - outer))
        self.assertGreater(top.red(), top.blue() + 60)                     # the coral dot at 12 o'clock
        self.assertIn("recording a meeting", ring.accessibleDescription())

    def test_the_switch_flies_the_badge(self) -> None:
        win = hud_overlay.HudWindow(native=hud_overlay.NoNative())
        self.addCleanup(win.deleteLater)
        snap = win.orb.snapshot().toImage()
        centre = win.orb.orb_rect().center()
        dpr = snap.devicePixelRatio()
        bright = [snap.pixelColor(round((centre.x() + dx) * dpr), round(centre.y() * dpr)) for dx in range(-15, 16)]
        self.assertGreater(max(luma(c) for c in bright), 200)             # the name is in the flown picture

    def test_the_accessible_name_and_the_state_api_are_the_rings(self) -> None:
        ring = self.ring(state=orb.NEEDS)
        self.assertEqual(ring.accessibleName(), "Jarvis, needs you")
        self.assertEqual(ring.state_word(), "NEEDS YOU")


class MotionTests(BadgeCase):

    def lively(self) -> None:
        holo.set_motion_level(holo.MOTION_LIVELY)

    def turned(self, ring: orb.OrbWidget, seconds: float = 1.0) -> dict[str, float]:
        start = dict(ring.sample(10.0).spin)
        for i in range(1, 11):
            end = dict(ring.sample(10.0 + seconds * i / 10).spin)
        return {name: (end[name] - start[name] + 180.0) % 360.0 - 180.0 for name in start}

    def test_the_rings_turn_and_thinking_turns_them_fast_against_each_other(self) -> None:
        self.lively()
        idle = self.turned(self.ring(state=orb.IDLE))
        thinking = self.turned(self.ring(state=orb.THINKING), 0.5)
        self.assertGreater(idle["arc"], 0.5)
        self.assertGreater(abs(thinking["arc"]) / 0.5, 3 * abs(idle["arc"]))
        self.assertLess(thinking["ticks"], 0)                              # against the arc
        self.assertGreater(thinking["arc"], 0)

    def test_the_second_screen_and_rest_turn_slower(self) -> None:
        self.lively()
        hud_idle = self.turned(self.ring(hud_overlay.ORB_PX, orb.IDLE))
        full_idle = self.turned(self.ring(second_screen.RING, orb.IDLE))
        hud_speaking = self.turned(self.ring(hud_overlay.ORB_PX, orb.SPEAKING))
        self.assertLess(full_idle["arc"], hud_idle["arc"])
        self.assertGreater(hud_speaking["arc"], 2 * hud_idle["arc"])

    def test_calm_is_slower_and_motion_off_stands_every_ring_at_its_place(self) -> None:
        self.lively()
        lively = self.turned(self.ring(state=orb.SPEAKING))
        holo.set_motion_level(holo.MOTION_CALM)
        calm = self.turned(self.ring(state=orb.SPEAKING))
        self.assertAlmostEqual(calm["arc"] * orb.CALM_STRETCH, lively["arc"], delta=0.5)
        holo.set_motion_level(holo.MOTION_OFF)
        ring = self.ring(state=orb.THINKING)
        self.assertEqual(set(self.turned(ring).values()), {0.0})
        self.assertEqual(set(dict(ring.sample().spin).values()), {0.0})

    def test_a_still_keeps_the_rings_where_they_are(self) -> None:
        self.lively()
        ring = self.ring(state=orb.THINKING)
        self.turned(ring)
        held = ring.sample(11.0).spin
        ring.set_phase(11.0)
        self.assertEqual(ring.sample(11.0).spin, held)
        self.assertEqual(ring.sample(14.0).spin, held)

    def test_the_frame_clock_rules_hold_and_the_amber_arc_turns_a_little_smoother(self) -> None:
        self.lively()
        self.assertEqual(self.ring(state=orb.IDLE).frame_interval(), orb.FRAME_IDLE_MS)
        self.assertEqual(self.ring(state=orb.THINKING).frame_interval(), orb.FRAME_ACTIVE_MS)
        self.assertEqual(self.ring(state=orb.NEEDS).frame_interval(), orb.BADGE_NEEDS_MS)
        self.assertEqual(self.ring(state=orb.NEEDS, muted=True).frame_interval(), orb.FRAME_SLOW_MS)
        holo.set_canvas_look(False)
        self.assertEqual(self.ring(state=orb.NEEDS).frame_interval(), orb.FRAME_IDLE_MS)


class CostTests(BadgeCase):

    def test_the_still_parts_are_made_once(self) -> None:
        holo.set_motion_level(holo.MOTION_LIVELY)
        ring = self.ring(state=orb.SPEAKING)
        picture(ring, ring.sample(1.0))
        made = ring.badge_layer_builds()
        self.assertGreater(made, 0)
        for i in range(30):
            picture(ring, ring.sample(1.0 + i / 30))
        self.assertEqual(ring.badge_layer_builds(), made)

    def test_a_frame_costs_little_at_the_second_screens_size(self) -> None:
        holo.set_motion_level(holo.MOTION_LIVELY)
        ring = self.ring(second_screen.RING, orb.THINKING)
        image = QImage(ring.size(), QImage.Format.Format_ARGB32_Premultiplied)
        times = []
        for i in range(40):
            image.fill(0)
            painter = QPainter(image)
            started = time.perf_counter()
            ring.paint_into(painter, QPointF(0, 0), ring.sample(1.0 + i / 30))
            times.append((time.perf_counter() - started) * 1000.0)
            painter.end()
        self.assertLess(statistics.median(times[5:]), 3.0)                 # the ring's own budget (DESIGN-SPEC 4.4)


class SecondScreenTests(BadgeCase):

    def display(self, **state) -> second_screen.SecondScreen:
        display = second_screen.SecondScreen(native=ssf.FakeNative(), now=lambda: ssf.NOW)
        self.addCleanup(display.deleteLater)
        display.resize(1920, 1080)
        display.set_state(ssf.demo_state(**state))
        display.freeze(5.0)
        return display

    def shot(self, display: second_screen.SecondScreen) -> QImage:
        image = QImage(display.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        try:
            display.paint_display(painter, None, now=ssf.NOW, at=None)
        finally:
            painter.end()
        return image.copy(display.layout_now().ring.toAlignedRect())

    def test_oled_care_no_glow_and_nothing_over_oled_ink(self) -> None:
        image = self.shot(self.display(look=(orb.IDLE, False, False), needs=0, tone="holo"))
        cap = luma(QColor(holo.OLED_INK))
        self.assertLessEqual(max(luma(c) for _x, _y, c in pixels(image, 2)), cap + 1)
        lit = sum(1 for _x, _y, c in pixels(image, 2) if luma(c) > 6)
        ring = orb.OrbWidget(second_screen.RING)            # the same badge with its glows, for the share it lights
        self.addCleanup(ring.deleteLater)
        ring.set_badge_form(True)
        ring.settle()
        glowing = picture(ring)
        lit_glow = sum(1 for _x, _y, c in pixels(glowing, 2) if luma(c) > 6)
        self.assertLess(lit, 0.6 * lit_glow * image.width() * image.height() / (glowing.width() * glowing.height()))

    def test_the_amber_arc_shows_there_too(self) -> None:
        image = self.shot(self.display())                    # the demo: needs you, 1 waiting
        self.assertGreater(sum(1 for _x, _y, c in pixels(image) if amberish(c)), 40)

    def test_the_ring_repaints_inside_its_own_rect(self) -> None:
        display = self.display()
        layout = display.layout_now()
        ring = display.ring
        side = ring.width()
        reach = orb.BADGE_REACH[ring.badge_tier()] * ring.badge_scale()
        self.assertLessEqual(2 * reach, layout.ring.width() + 0.5)
        self.assertEqual(side, orb.widget_side(layout.ring_px))


class ToastTests(BadgeCase):

    def test_the_toast_gives_the_badge_room_and_keeps_its_line_and_action(self) -> None:
        toast = states_view.Toast()
        self.addCleanup(toast.deleteLater)
        toast.post("Your briefing is ready.", "Open", "briefing", look=(orb.NEEDS, False, False))
        self.addCleanup(toast.dismiss)
        self.assertEqual(toast.mini.size().toTuple(), (states_view.TOAST_BADGE, states_view.TOAST_BADGE))
        self.assertGreater(toast.text_rect().left(), toast.mini.geometry().right())
        self.assertEqual((toast.line(), toast.action_text(), toast.key()), ("Your briefing is ready.", "Open",
                                                                            "briefing"))
        image = mini_picture(toast.mini)
        self.assertTrue(any(amberish(c) for _x, _y, c in pixels(image)))
        centre = QPointF(toast.mini.width() / 2, toast.mini.height() / 2)
        self.assertGreater(max(luma(image.pixelColor(round(centre.x()) + dx, round(centre.y())))
                               for dx in range(-12, 13)), 200)               # the name

    def test_without_the_canvas_the_toast_is_as_it_was(self) -> None:
        holo.set_canvas_look(False)
        toast = states_view.Toast()
        self.addCleanup(toast.deleteLater)
        toast.post("Your briefing is ready.", "Open", "briefing")
        self.addCleanup(toast.dismiss)
        self.assertEqual(toast.mini.size().toTuple(), (orb.MINI, orb.MINI))
        self.assertFalse(toast.mini.badge_shown())

    def test_the_toasts_badge_is_still_at_rest_and_runs_while_active(self) -> None:
        mini = orb.MiniOrb()
        self.addCleanup(mini.deleteLater)
        mini.set_badge_form(True)
        self.assertIsNone(mini.frame_interval())
        mini.set_state(orb.THINKING)
        self.assertEqual(mini.frame_interval(), orb.FRAME_MINI_MS)


if __name__ == "__main__":
    unittest.main()
