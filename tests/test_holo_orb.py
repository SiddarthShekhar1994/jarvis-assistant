"""Tests for the ring, the Halo band (briefing_reader.orb; DESIGN-SPEC section 4): states, poses and transitions,
the projection, segment counts, the far / near split, every look's distinct form at every size, the mic level, typing
against voice listening, frame rates, when it stops, motion off, the paint budget, the recording ring, the dim, the
36 px ring and the snapshot the switch to the desktop flies.

Qt runs offscreen in this process and shows nothing.
"""

from __future__ import annotations

import math
import time
import unittest

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import config, holo, hud, orb

_app: QApplication | None = None
_GROUND = holo.GroundCache()


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_holo_orb", "-platform", "offscreen"])
    hud.load_fonts(config.PROJECT_ROOT / "fonts")


def settle(rounds: int = 4) -> None:
    for _ in range(rounds):
        QApplication.processEvents()


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class Stage(QWidget):
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        _GROUND.paint_slice(painter, self.rect(), self.rect(), self.size(), self.devicePixelRatioF())
        painter.end()


class PaintCounter(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


# (look, base, muted, offline, voice): the nine looks as the app sets them
LOOK_CASES = (
    (orb.IDLE, orb.IDLE, False, False, False),
    (orb.LISTENING, orb.LISTENING, False, False, True),
    (orb.TYPING, orb.LISTENING, False, False, False),
    (orb.THINKING, orb.THINKING, False, False, False),
    (orb.SPEAKING, orb.SPEAKING, False, False, False),
    (orb.NEEDS, orb.NEEDS, False, False, False),
    (orb.MUTED, orb.IDLE, True, False, False),
    (orb.OFFLINE, orb.IDLE, False, True, False),
    (orb.WAITING, orb.WAITING, False, False, False),
)


def set_case(widget: orb._OrbBase, base: str, muted: bool, offline: bool, voice: bool) -> None:
    widget.set_voice_listening(voice)
    widget.set_look(base, muted=muted, offline=offline)


def difference(a: QImage, b: QImage) -> float:
    """The mean absolute channel difference of two same-size images (0..255), on a 3 px lattice."""
    total, count = 0, 0
    for y in range(0, a.height(), 3):
        for x in range(0, a.width(), 3):
            p, q = a.pixelColor(x, y), b.pixelColor(x, y)
            total += abs(p.red() - q.red()) + abs(p.green() - q.green()) + abs(p.blue() - q.blue())
            count += 3
    return total / max(1, count)


class _OrbCase(unittest.TestCase):
    def setUp(self) -> None:
        holo.set_reduced_motion(False)
        holo.set_motion_level(holo.MOTION_LIVELY)
        orb.set_session_locked(False)
        orb.frame_clock().set_deep(False)       # the clock is shared: no test starts in deep idle
        orb.note_activity()
        self.addCleanup(holo.set_reduced_motion, None)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.addCleanup(orb.set_session_locked, False)
        self.addCleanup(orb.frame_clock().set_deep, False)

    def stage(self, px: int = orb.HERO) -> tuple[Stage, orb.OrbWidget]:
        stage = Stage()
        stage.resize(600, 420)
        widget = orb.OrbWidget(px, stage)
        widget.set_ground(orb.ground_painter(_GROUND))
        widget.move(40, 20)
        self.addCleanup(stage.deleteLater)
        self.addCleanup(stage.hide)
        stage.show()
        settle()
        return stage, widget

    def ring(self, px: int = orb.HERO) -> orb.OrbWidget:
        widget = orb.OrbWidget(px)
        self.addCleanup(widget.deleteLater)
        return widget

    def clocked(self, px: int = orb.HERO) -> tuple[orb.OrbWidget, list[float]]:
        widget = self.ring(px)
        clock = [20.0]
        widget._now = lambda: clock[0]
        return widget, clock


class StateTests(_OrbCase):
    def test_state_words(self) -> None:
        self.assertEqual(orb.state_word(orb.IDLE), "STANDBY")
        self.assertEqual(orb.state_word(orb.NEEDS), "NEEDS YOU")
        self.assertEqual(orb.state_word(orb.WAITING), "WAITING FOR THE AM BRIEFING")
        self.assertEqual(orb.state_word(orb.SPEAKING, muted=True), "MUTED")
        self.assertEqual(orb.state_word(orb.IDLE, offline=True), "OFFLINE VOICE")
        self.assertEqual(orb.state_word(orb.SPEAKING, offline=True), "SPEAKING")
        self.assertEqual(orb.state_word(orb.LISTENING), "LISTENING")       # typing and the voice say the same

    def test_set_state_and_accessible_name(self) -> None:
        widget = self.ring()
        self.assertEqual(widget.accessibleName(), "Jarvis, standby")
        widget.set_state(orb.SPEAKING)
        self.assertEqual(widget.look(), (orb.SPEAKING, False, False))
        self.assertEqual(widget.accessibleName(), "Jarvis, speaking")
        widget.set_state(orb.MUTED)
        self.assertEqual(widget.look(), (orb.IDLE, True, False))
        self.assertEqual(widget.accessibleName(), "Jarvis, muted")
        widget.set_state(orb.NEEDS)
        self.assertEqual(widget.look(), (orb.NEEDS, True, False))          # the modifier stays
        widget.set_look(orb.NEEDS)
        self.assertEqual(widget.accessibleName(), "Jarvis, needs you")
        self.assertIn("waiting for your decision", widget.accessibleDescription())
        widget.set_voice_listening(True)
        widget.set_state(orb.LISTENING)
        self.assertEqual(widget.accessibleName(), "Jarvis, listening")
        widget.set_state("nonsense")
        self.assertEqual(widget.state(), orb.IDLE)

    def test_the_looks_follow_the_state_word(self) -> None:
        self.assertEqual(orb.ring_look(orb.SPEAKING, muted=True), orb.MUTED)
        self.assertEqual(orb.ring_look(orb.LISTENING), orb.TYPING)
        self.assertEqual(orb.ring_look(orb.LISTENING, voice=True), orb.LISTENING)
        self.assertEqual(orb.ring_look(orb.NEEDS, offline=True), orb.NEEDS)
        self.assertEqual(orb.ring_look(orb.WAITING, offline=True), orb.OFFLINE)
        self.assertEqual(orb.ring_look(orb.IDLE, voice=True), orb.IDLE)     # the voice flag only shapes LISTENING
        self.assertEqual(set(orb.POSES), set(orb.LOOKS))

    def test_frame_interval_per_state(self) -> None:
        widget = self.ring()
        # Round 1 decision: 10 fps at rest, 30 fps listening / thinking / speaking.
        for state, ms in ((orb.IDLE, 100), (orb.LISTENING, 33), (orb.THINKING, 33), (orb.SPEAKING, 33),
                          (orb.NEEDS, 100), (orb.WAITING, 100), (orb.MUTED, 100), (orb.OFFLINE, 100)):
            widget.set_state(state)
            widget.settle()
            self.assertEqual(widget.frame_interval(), ms, state)
        widget.set_look(orb.SPEAKING, muted=True)
        widget.settle()
        self.assertEqual(widget.frame_interval(), 33)        # muted, but the briefing plays

    def test_transitions_run_at_30_fps_then_drop(self) -> None:
        widget = self.ring()
        widget.set_state(orb.LISTENING)
        self.assertEqual(widget.frame_interval(), 33)
        widget.set_state(orb.IDLE)
        self.assertEqual(widget.frame_interval(), 33)        # the 700 ms release back to standby
        widget.settle()
        self.assertEqual(widget.frame_interval(), 100)

    def test_the_turn_to_face_takes_420_ms_and_the_release_700(self) -> None:
        widget, clock = self.clocked()
        widget.settle()
        t0 = clock[0]
        widget.set_voice_listening(True)
        widget.set_look(orb.LISTENING)
        early = widget.sample(t0 + 0.1)
        self.assertTrue(0.0 < early.tilt < 58.0 and early.tilt < 30.0, early.tilt)  # ease-out: most of it early
        self.assertEqual(early.close, 0.0)                                          # the brackets wait 140 ms
        self.assertEqual(early.sight, 0.0)
        mid = widget.sample(t0 + 0.3)
        self.assertTrue(0.0 < mid.close < 1.0, mid.close)                           # closing (140 to 420 ms)
        self.assertEqual(mid.sight, 0.0)                                            # the sight fades in last
        self.assertTrue(0.0 < widget.sample(t0 + 0.36).sight < 1.0)
        locked = widget.sample(t0 + 0.421)
        self.assertEqual((locked.tilt, locked.roll, locked.close, locked.sight), (0.0, 0.0, 1.0, 1.0))
        self.assertAlmostEqual(locked.brackets, 0.95)
        self.assertAlmostEqual(locked.phase, 0.5)                                   # segments centred on the axes
        t1 = clock[0] = t0 + 1.0
        widget.set_voice_listening(False)
        widget.set_look(orb.IDLE)
        self.assertLess(widget.sample(t1 + 0.42).tilt, 58.0)                        # the release takes 700 ms
        rest = widget.sample(t1 + 0.701)
        self.assertEqual((rest.tilt, rest.roll - rest.sway, rest.close, rest.sight), (58.0, -22.0, 0.0, 0.0))

    def test_calm_turns_slower_and_motion_off_takes_the_pose_at_once(self) -> None:
        holo.set_motion_level(holo.MOTION_CALM)
        widget, clock = self.clocked()
        widget.set_voice_listening(True)
        widget.set_look(orb.LISTENING)
        self.assertGreater(widget.sample(clock[0] + 0.5).tilt, 0.0)                 # 420 ms x 1.6
        self.assertEqual(widget.sample(clock[0] + 0.68).tilt, 0.0)
        holo.set_motion_level(holo.MOTION_OFF)
        still = self.ring()
        set_case(still, orb.LISTENING, False, False, True)
        frame = still.sample()
        self.assertEqual((frame.tilt, frame.roll, frame.close, frame.sight), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(still.frame_interval(), orb.FRAME_ACTIVE_MS)               # the rate it would run at ...
        self.assertFalse(still._should_run())                                       # ... but motion off is still

    def test_every_look_takes_its_pose(self) -> None:
        widget = self.ring()
        holo.set_motion_level(holo.MOTION_CALM)                                     # no sway in the numbers
        for look, base, muted, offline, voice in LOOK_CASES:
            with self.subTest(look=look):
                set_case(widget, base, muted, offline, voice)
                widget.settle()
                frame = widget.sample()
                pose = orb.POSES[look]
                self.assertEqual(widget.ring_look(), look)
                self.assertEqual(frame.look, look)
                self.assertEqual((frame.tilt, frame.roll), (pose.tilt, pose.roll))
                self.assertEqual((frame.close, frame.brackets), (pose.close, pose.brackets))
        expected = {orb.IDLE: (58, -22), orb.LISTENING: (0, 0), orb.TYPING: (20, -6), orb.THINKING: (68, -24),
                    orb.SPEAKING: (14, -4), orb.NEEDS: (58, -18), orb.MUTED: (80, -6), orb.OFFLINE: (58, -18),
                    orb.WAITING: (58, -18)}
        self.assertEqual({look: (pose.tilt, pose.roll) for look, pose in orb.POSES.items()}, expected)
        none = [look for look, pose in orb.POSES.items() if pose.brackets == 0.0]
        self.assertEqual(sorted(none), sorted([orb.THINKING, orb.MUTED, orb.WAITING]))   # no brackets

    def test_the_bands_pattern_per_look(self) -> None:
        n = 64

        def frame_of(base: str, muted: bool = False, offline: bool = False, voice: bool = False) -> orb.OrbFrame:
            widget = self.ring()
            set_case(widget, base, muted, offline, voice)
            widget.settle()
            return widget.sample()

        idle = frame_of(orb.IDLE)
        self.assertEqual(set(idle.alphas), {0.66})                                  # a full band, breathing
        waiting = frame_of(orb.WAITING)
        lit = [a for a in waiting.alphas if a > 0.0]
        self.assertEqual(len(lit), orb.tail_length(orb.WAITING, n))                 # the scanner and its tail only
        self.assertEqual(len(lit), 6)
        self.assertEqual(waiting.brackets, 0.0)                                     # and no brackets
        thinking = frame_of(orb.THINKING)
        self.assertEqual(sum(1 for c in thinking.colors if c in (orb.C_WHITE, orb.C_HI)), 14)
        self.assertIn(orb.C_WHITE, thinking.colors)                                 # a white head
        needs = frame_of(orb.NEEDS)
        amber = [i for i, c in enumerate(needs.colors) if c == orb.C_AMBER]
        self.assertTrue(amber)
        for i in amber:
            self.assertTrue(14.0 < orb.segment_angle(i, n) % 360.0 < 66.0)
        offline = frame_of(orb.IDLE, offline=True)
        self.assertEqual(sum(1 for a in offline.alphas if a > 0.0), 44)             # 4 lit of every 6 (64 = 10 x 6 + 4)
        self.assertTrue(offline.dashed)
        muted = frame_of(orb.IDLE, muted=True)
        self.assertEqual(set(muted.colors), {orb.C_STEEL})
        self.assertEqual(muted.glow, 0.0)
        typing = frame_of(orb.LISTENING)
        self.assertEqual(sum(1 for c in typing.colors if c == orb.C_HI), 4)         # the bottom four
        bottom = [orb.segment_angle(i, n) for i, c in enumerate(typing.colors) if c == orb.C_HI]
        self.assertTrue(all(abs(a - 90.0) < 3 * 360.0 / n for a in bottom))
        self.assertEqual(typing.sight, 0.0)                                         # no crosshair
        voice = frame_of(orb.LISTENING, voice=True)
        notches = [i for i, a in enumerate(voice.alphas) if a == 0.0]
        self.assertEqual([orb.segment_angle(i, n, 0.5) % 360.0 for i in notches], [0.0, 90.0, 180.0, 270.0])
        self.assertEqual(frame_of(orb.SPEAKING).echo, 1.0)

    def test_the_tails_keep_their_share_at_every_size(self) -> None:
        self.assertEqual([orb.tail_length(orb.THINKING, n) for n in (64, 32, 16)], [14, 7, 4])
        self.assertEqual([orb.tail_length(orb.WAITING, n) for n in (64, 32, 16)], [6, 3, 2])
        for n in (64, 48, 32, 16):
            alphas, _colors = orb.band_pattern(orb.WAITING, n)
            self.assertEqual(sum(1 for a in alphas if a > 0.0), orb.tail_length(orb.WAITING, n), n)

    def test_needs_arc_draws_in_and_fades_out(self) -> None:
        widget = self.ring()
        t0 = widget._now()
        widget.set_state(orb.NEEDS)
        self.assertLess(widget.sample(t0 + 0.1).arc, 1.0)
        full = widget.sample(t0 + 1.05)
        self.assertEqual((full.arc, full.alert), (1.0, 1.0))
        self.assertIn(orb.C_AMBER, full.colors)                                     # an amber run on the band
        t1 = widget._now()
        widget.set_state(orb.IDLE)
        self.assertTrue(0.0 < widget.sample(t1 + 0.2).alert < 1.0)
        self.assertEqual(widget.sample(t1 + 0.45).alert, 0.0)

    def test_the_thinking_head_turns_once_in_1_4_s_without_jumps(self) -> None:
        widget, clock = self.clocked()
        widget.set_state(orb.THINKING)
        widget.settle()
        heads = []
        for i in range(43):                                                         # 1.4 s at 30 fps
            clock[0] += 1.4 / 42
            heads.append(widget.sample().head)
        steps = [(b - a) % 360.0 for a, b in zip(heads, heads[1:])]
        self.assertAlmostEqual(max(steps), 360.0 / 42, delta=0.01)                  # one frame of speed, never more
        self.assertAlmostEqual(sum(steps), 360.0, delta=0.5)
        holo.set_motion_level(holo.MOTION_CALM)
        widget.sample()
        clock[0] += 0.1
        self.assertAlmostEqual(widget.sample().head - heads[-1], 360.0 / 2.2 * 0.1, delta=0.6)   # calm: 2.2 s

    def test_the_level_drives_the_speaking_reach_and_the_core_with_a_synthetic_fallback(self) -> None:
        widget, clock = self.clocked()
        widget.set_state(orb.SPEAKING)
        widget.settle()
        frame = None
        for _ in range(20):
            widget.set_level(1.0)
            clock[0] += 0.033
            frame = widget.sample()
        self.assertGreater(widget.level(), 0.95)
        self.assertGreater(frame.voice, 0.9)
        self.assertGreater(max(frame.reach), 18.0)                                  # the segments reach out
        self.assertLessEqual(max(frame.reach), orb.SPIKE_MAX + 1e-6)                # ... never past the sight
        self.assertLess(orb.BAND_OUT + orb.SPIKE_MAX, orb.SIGHT_R + 2.0)
        self.assertAlmostEqual(frame.core_scale, 1.0 + 0.18 * widget.level(), delta=0.03)
        for _ in range(20):
            widget.set_level(0.0)
            clock[0] += 0.033
            frame = widget.sample()
        self.assertLess(widget.level(), 0.05)
        self.assertLess(max(frame.reach), orb.SPIKE_MIN + 0.5)
        widget.set_level(None)
        clock[0] += 2.0
        widget.sample()
        clock[0] += 0.4
        frame = widget.sample()
        self.assertLess(frame.voice, 0.05)                                          # the synthetic curve took over
        self.assertTrue(0.0 <= frame.level <= 1.0)
        widget.set_level(7.0)
        self.assertEqual(widget._level_target, 1.0)

    def test_motion_off_paints_a_still_frame(self) -> None:
        for reduce in (True, False):
            with self.subTest(reduced_motion=reduce):
                holo.set_reduced_motion(reduce)
                holo.set_motion_level(holo.MOTION_LIVELY if reduce else holo.MOTION_OFF)
                for look, base, muted, offline, voice in LOOK_CASES:
                    widget = self.ring()
                    set_case(widget, base, muted, offline, voice)
                    first, second = widget.sample(10.0), widget.sample(11.3)
                    self.assertEqual((first.tilt, first.roll, first.head, first.band_gain),
                                     (second.tilt, second.roll, second.head, second.band_gain), look)
                    self.assertEqual((first.tilt, first.roll), (orb.POSES[look].tilt, orb.POSES[look].roll))
                    self.assertEqual((first.sway, first.band_gain, first.core_scale), (0.0, 1.0, 1.0))
                    self.assertEqual((first.spikes, first.spike_lens), (0.0, ()))     # nothing reaches out
                    self.assertEqual(first.alphas, second.alphas)
                thinking = self.ring()
                thinking.set_state(orb.THINKING)
                self.assertEqual(thinking.sample(5.0).head, orb.THINK_HEAD)          # 14 fixed segments

    def test_sizes_level_of_detail_and_segments(self) -> None:
        hero, compact, hud_ring = self.ring(orb.HERO), self.ring(orb.COMPACT), self.ring(92)
        self.assertEqual((hero.detail(), compact.detail(), hud_ring.detail()), ("full", "compact", "compact"))
        self.assertEqual((hero.tier(), compact.tier(), hud_ring.tier()), (orb.TIER_HERO, orb.TIER_MID, orb.TIER_HUD))
        self.assertEqual(hero.width(), orb.widget_side(orb.HERO))
        self.assertEqual(hero.orb_rect().width(), orb.HERO)
        half = orb.BRACKET_OPEN * orb.HERO / 360.0                                  # the open brackets fit
        self.assertGreater(hero.width() / 2, half + 1.5)
        self.assertEqual(hud_ring.width(), 104)                                     # the HUD's box
        self.assertEqual([orb.segments_for(px) for px in (320, 200, 199, 120, 106, 92, 36)],
                         [64, 64, 48, 48, 48, 32, 16])
        for widget, n in ((hero, 64), (compact, 48), (hud_ring, 32)):
            frame = widget.sample()
            self.assertEqual((frame.segments, len(frame.alphas), widget.segment_count()), (n, n, n))
        hero.set_orb_size(orb.COMPACT)
        self.assertEqual((hero.detail(), hero.segment_count()), ("compact", 48))
        self.assertEqual(orb.voice_side(orb.HERO), orb.widget_side(orb.HERO))       # the reach needs no room

    def test_slow_paints_drop_the_minor_ticks_outer_track_and_glow(self) -> None:
        widget = self.ring()
        widget._paint_ms.extend([2.0] * 60)
        widget._maybe_degrade()
        self.assertEqual(widget.detail(), "full")
        with self.assertLogs("briefing_reader.orb", level="INFO") as logs:
            widget._paint_ms.extend([3.0] * 60)                                     # over 2.5 ms on average
            widget._maybe_degrade()
        self.assertEqual(widget.detail(), "reduced")
        self.assertIn("Orb: compact layers (paint 3.0 ms)", logs.output[0])
        frame = widget.sample()
        self.assertEqual((frame.compact, frame.fine, frame.segments), (False, False, 64))   # hero band, no fine layers
        self.assertFalse(widget.grab().isNull())

    def test_the_sight_is_one_cached_layer_per_tone(self) -> None:
        _stage, widget = self.stage()
        clock = [50.0]
        widget._now = lambda: clock[0]
        widget.grab()
        builds = widget.ring_layer_builds()
        self.assertGreaterEqual(builds, 1)
        for _ in range(5):                                         # steady frames blit it: nothing is built
            clock[0] += 0.1
            widget.grab()
        self.assertEqual(widget.ring_layer_builds(), builds)
        widget.set_voice_listening(True)                           # the listening tone: one more layer
        widget.set_look(orb.LISTENING)
        clock[0] += 1.0
        widget.grab()
        self.assertEqual(widget.ring_layer_builds(), builds + 1)
        for look, base, muted, offline, voice in LOOK_CASES:
            set_case(widget, base, muted, offline, voice)
            widget.settle()
            widget.grab()
        self.assertLessEqual(widget.backdrop_count(), orb.SIGHT_LAYERS)

    def test_every_look_paints_its_core(self) -> None:
        _stage, widget = self.stage()
        for px in (orb.HERO, orb.COMPACT, 92):
            widget.set_orb_size(px)
            for look, base, muted, offline, voice in LOOK_CASES:
                set_case(widget, base, muted, offline, voice)
                widget.set_phase(1.7)
                image = widget.grab().toImage()
                center = image.pixelColor(image.width() // 2, image.height() // 2)
                if look == orb.MUTED:
                    self.assertLess(center.hslSaturation(), 100, (px, look))       # steel and its strike, no cyan
                else:
                    self.assertGreater(center.lightness(), 70, (px, look))
                widget.set_phase(None)

    def test_every_look_is_distinct_at_every_size(self) -> None:
        stage = Stage()
        stage.resize(600, 420)
        self.addCleanup(stage.deleteLater)
        stage.show()
        for px in (orb.HERO, orb.COMPACT, 92, orb.MINI):
            images = {}
            for look, base, muted, offline, voice in LOOK_CASES:
                widget = orb.MiniOrb(stage, ring_color=holo.VOID) if px == orb.MINI else orb.OrbWidget(px, stage)
                if px != orb.MINI:
                    widget.set_ground(orb.ground_painter(_GROUND))
                set_case(widget, base, muted, offline, voice)
                widget.set_phase(2.35)
                images[look] = widget.grab().toImage()
                widget.deleteLater()
            looks = list(images)
            for i, a in enumerate(looks):
                for b in looks[i + 1:]:                 # (the closest pairs measure 1.7 to 2.3 on this lattice)
                    self.assertGreater(difference(images[a], images[b]), 1.0, (px, a, b))
        settle()

    def test_offline_voice_is_dashed_and_waiting_is_a_lone_scanner_at_every_size(self) -> None:
        for px in (orb.HERO, orb.COMPACT, 92):
            offline, waiting = self.ring(px), self.ring(px)
            offline.set_state(orb.OFFLINE)
            waiting.set_state(orb.WAITING)
            offline.settle()
            waiting.settle()
            self.assertTrue(offline.sample().dashed, px)
            self.assertIn((holo.HOLO, True, False), [tone for tone, _w in offline.sample().tones])
            frame = waiting.sample()
            n = orb.segments_for(px)
            self.assertEqual(sum(1 for a in frame.alphas if a > 0.0), orb.tail_length(orb.WAITING, n), px)
            self.assertEqual(frame.brackets, 0.0)
        # 36 px: a dashed centre ring (offline) and the faint orbit with its 2-segment tick (waiting)
        plain = QImage(36, 36, QImage.Format.Format_ARGB32_Premultiplied)
        dashed = QImage(36, 36, QImage.Format.Format_ARGB32_Premultiplied)
        for image, offline_on in ((plain, False), (dashed, True)):
            image.fill(QColor(holo.VOID))
            painter = QPainter(image)
            orb.paint_mini_orb(painter, QPointF(18, 18), orb.IDLE, offline=offline_on, ring_color=holo.VOID)
            painter.end()
        ring_r = 70.0 * orb.MINI / orb.MINI_BOX                                      # the dashed centre ring
        lit = [dashed.pixelColor(round(18 + ring_r * math.cos(a)), round(18 + ring_r * math.sin(a))).lightness()
               - plain.pixelColor(round(18 + ring_r * math.cos(a)), round(18 + ring_r * math.sin(a))).lightness()
               for a in (i * math.pi / 8 for i in range(16))]
        self.assertGreater(max(lit), 40)                                             # its dashes show
        self.assertLessEqual(min(lit), 20)                                           # ... with gaps

    def test_a_snapshot_is_the_ring_without_its_ground_and_leaves_it_as_it_was(self) -> None:
        stage, widget = self.stage()
        clock = [50.0]
        widget._now = lambda: clock[0]
        widget.set_look(orb.SPEAKING)
        clock[0] += 2.0
        widget.grab()                                 # steady: its sight layer is built
        ground, backdrops, builds = widget._ground, dict(widget._backdrops), widget.ring_layer_builds()
        times = widget.paint_times()
        picture = widget.snapshot().toImage()
        grabbed = widget.grab().toImage()
        self.assertEqual(picture.size(), grabbed.size())
        self.assertEqual(picture.width(), picture.height())                          # square, the ring centred
        self.assertEqual(picture.pixelColor(1, 1).alpha(), 0)            # no ground: the corners stay clear
        self.assertEqual(grabbed.pixelColor(1, 1).alpha(), 255)          # (the widget paints its ground there)
        center = picture.pixelColor(picture.width() // 2, picture.height() // 2)
        self.assertGreater(center.lightness(), 70)
        self.assertIs(widget._ground, ground)
        self.assertEqual(dict(widget._backdrops), backdrops)            # its own layers were never painted over
        self.assertEqual(widget.ring_layer_builds(), builds)
        self.assertEqual(widget.paint_times()[:len(times)], times)
        widget.set_dim(0.5)                                             # quiet hours: half its strength, over nothing
        dimmed = widget.snapshot().toImage().pixelColor(picture.width() // 2, picture.height() // 2)
        self.assertAlmostEqual(dimmed.alpha() / max(1, center.alpha()), 0.5, delta=0.05)
        widget.set_dim(1.0)
        stage.hide()                                                    # hidden: it still takes one
        settle()
        hidden = widget.snapshot().toImage()
        self.assertGreater(hidden.pixelColor(hidden.width() // 2, hidden.height() // 2).lightness(), 70)

    def test_quiet_hours_paint_the_ring_at_70_percent(self) -> None:
        _stage, widget = self.stage()
        widget.set_phase(2.35)
        full = widget.grab().toImage()
        widget.set_dim(0.7)
        self.assertEqual(widget.dim(), 0.7)
        dimmed = widget.grab().toImage()
        x, y = full.width() // 2, full.height() // 2
        ground = full.pixelColor(2, 2)
        for name in ("red", "green", "blue"):
            a, b, g = (getattr(c, name)() for c in (full.pixelColor(x, y), dimmed.pixelColor(x, y), ground))
            self.assertAlmostEqual(b, g + (a - g) * 0.7, delta=4, msg=name)          # the ground laid over at 30 %
        self.assertEqual(dimmed.pixelColor(2, 2), ground)                            # nothing changes off the ring

    def test_the_recording_ring_is_coral_with_a_dot_at_12(self) -> None:
        _stage, widget = self.stage()
        widget.set_phase(2.35)
        rect = widget.orb_rect()
        k = orb.HERO / 360.0

        def coral_at(image: QImage, dx: float, dy: float) -> bool:
            dpr = image.devicePixelRatio()
            color = image.pixelColor(round((rect.center().x() + dx) * dpr), round((rect.center().y() + dy) * dpr))
            return color.red() > color.blue() + 60 and color.red() > 120

        before = widget.grab().toImage()
        self.assertFalse(coral_at(before, 0, -orb.REC_R * k))
        widget.set_recording(True)
        self.assertIn("recording a meeting", widget.accessibleDescription())
        image = widget.grab().toImage()
        self.assertTrue(coral_at(image, 0, -orb.REC_R * k))                          # the dot at 12 o'clock
        self.assertTrue(coral_at(image, orb.REC_R * k, 0) or coral_at(image, orb.REC_R * k - 0.5, 0))   # the ring
        widget.set_recording(False)
        self.assertFalse(coral_at(widget.grab().toImage(), 0, -orb.REC_R * k))


class RingMathsTests(unittest.TestCase):
    """The projection (DESIGN-SPEC 4.3): a band point at theta and r, tipped by tau, rolled by rho."""

    def test_a_band_point_lands_where_the_spec_says(self) -> None:
        self.assertEqual(orb.project(135, 0, 0, 0), (135.0, 0.0))
        x, y = orb.project(135, 90, 0, 0)                          # 6 o'clock, face-on
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 135.0, places=6)
        x, y = orb.project(135, 90, 60, 0)                         # tipped 60 deg: y' = y cos tau
        self.assertAlmostEqual(y, 67.5, places=6)
        for r, theta, tilt, roll in ((135, 90, 60, 90), (119, 33, 58, -22), (135, -90, 68, -24), (141, 200, 14, -4)):
            th, tau, rho = (math.radians(v) for v in (theta, tilt, roll))
            px, py = r * math.cos(th), r * math.sin(th) * math.cos(tau)
            ex, ey = px * math.cos(rho) - py * math.sin(rho), px * math.sin(rho) + py * math.cos(rho)
            x, y = orb.project(r, theta, tilt, roll)
            self.assertAlmostEqual(x, ex, places=6)
            self.assertAlmostEqual(y, ey, places=6)
            mapped = orb.band_transform(tilt, roll).map(QPointF(r * math.cos(th), r * math.sin(th)))
            self.assertAlmostEqual(mapped.x(), ex, places=4)       # the painter's transform is the same projection
            self.assertAlmostEqual(mapped.y(), ey, places=4)
        x, y = orb.project(135, 90, 60, 90)                        # rolled a quarter turn: 6 o'clock goes to 9
        self.assertAlmostEqual(x, -67.5, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_the_far_half_and_its_depth(self) -> None:
        self.assertTrue(orb.is_far(-90, 58))                       # 12 o'clock tips away
        self.assertFalse(orb.is_far(90, 58))                       # 6 o'clock comes near
        self.assertFalse(orb.is_far(-90, 2.9))                     # below 3 deg: no split
        self.assertAlmostEqual(orb.depth_alpha(-90, 58), 0.5)
        self.assertAlmostEqual(orb.depth_alpha(90, 58), 1.0)
        self.assertAlmostEqual(orb.depth_alpha(0, 58), 0.75)
        self.assertEqual(orb.depth_alpha(-90, 2.0), 1.0)
        n = 64
        far = [i for i in range(n) if orb.is_far(orb.segment_angle(i, n), 58)]
        self.assertEqual(len(far), 32)                             # the upper half of the band's plane
        alphas, colors = orb.band_pattern(orb.IDLE, n)
        bands = ((1.0, alphas, colors, None),)
        back, front = orb._band_buckets(bands, n, orb.BAND_IN, orb.BAND_OUT, phase=0.0, tilt=58.0)
        count = lambda buckets: sum(path.elementCount() for _c, path in buckets) // 4   # noqa: E731
        self.assertEqual((count(back), count(front)), (32, 32))
        self.assertLess(max(c.alphaF() for c, _p in back), min(c.alphaF() for c, _p in front) + 0.2)
        back, front = orb._band_buckets(bands, n, orb.BAND_IN, orb.BAND_OUT, phase=0.0, tilt=2.0)
        self.assertEqual((count(back), count(front)), (0, 64))     # level: one pass, no depth

    def test_the_mic_lights_round_32_l_per_side_and_three_steps_with_motion_off(self) -> None:
        self.assertEqual([orb.lit_per_side(level) for level in (0.0, 0.15, 0.5, 0.9, 1.0)], [0, 5, 16, 29, 32])
        self.assertEqual([orb.lit_per_side(level, still=True) for level in (0.0, 0.15, 0.5, 0.9)], [0, 8, 20, 32])
        self.assertEqual([orb.lit_per_side(0.5, n) for n in (64, 48, 32, 16)], [16, 12, 8, 4])
        alphas, colors = orb.band_pattern(orb.LISTENING, 64, phase=0.5, lit=5, sight=1.0)
        lit = [orb.segment_angle(i, 64, 0.5) for i, c in enumerate(colors) if c == orb.C_HI and alphas[i] > 0]
        self.assertEqual(len(lit), 10)                             # 5 per side; the one at 6 o'clock is a notch
        self.assertTrue(all(abs(a - 90.0) <= 5 * 5.625 + 1e-6 for a in lit))


class LevelTests(_OrbCase):
    def test_the_mic_lights_the_band_from_the_first_sample(self) -> None:
        widget, clock = self.clocked()
        widget.set_voice_listening(True)
        widget.set_look(orb.LISTENING)
        widget.sample()
        widget.set_level(0.5)
        clock[0] += 0.033
        first = widget.sample()                                     # one frame: before the band is level
        self.assertGreater(first.tilt, 10.0)
        self.assertGreater(first.lit, 0)                            # ... it already lights
        self.assertGreater(widget.level(), 0.3)                     # a 30 ms attack
        for _ in range(10):
            widget.set_level(0.5)
            clock[0] += 0.033
            frame = widget.sample()
        self.assertEqual(frame.lit, 16)
        widget.set_level(0.0)
        clock[0] += 0.18
        widget.sample()
        self.assertAlmostEqual(widget.level(), 0.5 * math.exp(-1.0), delta=0.03)   # a 180 ms release
        for _ in range(30):
            widget.set_level(0.0)
            clock[0] += 0.033
            frame = widget.sample()
        self.assertEqual(frame.lit, 0)

    def test_motion_off_steps_the_mic_and_repaints_only_on_a_new_step(self) -> None:
        holo.set_motion_level(holo.MOTION_OFF)
        _stage, widget = self.stage()
        set_case(widget, orb.LISTENING, False, False, True)
        settle()
        self.assertFalse(widget.is_animating())                     # still: no frame clock
        counter = PaintCounter()
        widget.installEventFilter(counter)
        for level, step in ((0.15, 8), (0.5, 20), (0.9, 32)):
            widget.set_level(level)
            settle()
            self.assertEqual(widget.sample().lit, step)
        painted = counter.count
        self.assertGreaterEqual(painted, 3)
        widget.set_level(0.92)                                      # the same step: no repaint
        settle()
        self.assertEqual(counter.count, painted)

    def test_typing_leans_and_only_the_voice_locks(self) -> None:
        widget, clock = self.clocked()
        widget.set_state(orb.LISTENING)
        widget.settle()
        typing = widget.sample()
        self.assertEqual((widget.ring_look(), widget.voice_listening()), (orb.TYPING, False))
        self.assertEqual((typing.tilt, typing.roll, typing.close, typing.brackets), (20.0, -6.0, 0.5, 0.55))
        self.assertEqual((typing.sight, typing.lit), (0.0, 0))      # no crosshair, the mic never lights it
        widget.set_level(1.0)
        clock[0] += 0.1
        self.assertEqual(widget.sample().lit, 0)
        widget.set_voice_listening(True)                            # the talk key: it comes level and locks
        self.assertEqual(widget.ring_look(), orb.LISTENING)
        clock[0] += 0.43
        locked = widget.sample()
        self.assertEqual((locked.tilt, locked.close, locked.sight), (0.0, 1.0, 1.0))
        widget.set_voice_listening(False)                           # typing again: it leans back out
        clock[0] += 0.8
        self.assertEqual(widget.sample().tilt, 20.0)
        widget.set_state(orb.IDLE)
        widget.set_voice_listening(True)                            # the flag alone changes nothing at rest
        self.assertEqual(widget.ring_look(), orb.IDLE)


class RunTests(_OrbCase):
    def test_runs_only_while_visible(self) -> None:
        stage, widget = self.stage()
        self.assertTrue(widget.is_animating())
        stage.hide()
        settle()
        self.assertFalse(widget.is_animating())
        self.assertFalse(orb.frame_clock().running())
        stage.show()
        settle()
        self.assertTrue(widget.is_animating())
        widget.hide()
        self.assertFalse(widget.is_animating())

    def test_stops_when_minimized(self) -> None:
        stage, widget = self.stage()
        stage.showMinimized()
        wait(50)
        self.assertFalse(widget.is_animating())
        stage.showNormal()
        wait(50)
        self.assertTrue(widget.is_animating())

    def test_stops_while_the_session_is_locked(self) -> None:
        _stage, widget = self.stage()
        orb.set_session_locked(True)
        self.assertFalse(widget.is_animating())
        orb.set_session_locked(False)
        self.assertTrue(widget.is_animating())
        locked = [True]
        orb.set_lock_probe(lambda: locked[0])
        self.addCleanup(orb.set_lock_probe, None)
        orb.frame_clock()._poll_lock()
        self.assertFalse(widget.is_animating())
        locked[0] = False
        orb.frame_clock()._poll_lock()
        self.assertTrue(widget.is_animating())

    def test_reduced_motion_and_motion_off_stop_the_clock(self) -> None:
        _stage, widget = self.stage()
        holo.set_reduced_motion(True)
        self.assertFalse(widget.is_animating())
        holo.set_reduced_motion(False)
        self.assertTrue(widget.is_animating())
        holo.set_motion_level(holo.MOTION_OFF)                      # [ui] motion = "off": the still pose too
        widget.set_state(orb.THINKING)
        self.assertFalse(widget.is_animating())
        holo.set_motion_level(holo.MOTION_LIVELY)
        widget.set_state(orb.IDLE)
        self.assertTrue(widget.is_animating())

    def test_clock_rate_follows_the_state(self) -> None:
        _stage, widget = self.stage()
        for state, ms in ((orb.SPEAKING, 33), (orb.IDLE, 100), (orb.MUTED, 100)):
            widget.set_state(state)
            widget.settle()
            widget.update()
            wait(120)
            self.assertEqual(orb.frame_clock().interval(), ms, state)

    def test_speaking_paints_about_30_frames_a_second(self) -> None:
        _stage, widget = self.stage()
        widget.set_state(orb.SPEAKING)
        widget.settle()
        counter = PaintCounter()
        widget.installEventFilter(counter)
        wait(150)
        counter.count = 0
        wait(600)
        self.assertTrue(8 <= counter.count <= 22, counter.count)

    def test_hero_paint_is_under_budget(self) -> None:
        _stage, widget = self.stage()
        for look, base, muted, offline, voice in LOOK_CASES:
            if look not in (orb.SPEAKING, orb.LISTENING, orb.THINKING, orb.IDLE):
                continue
            set_case(widget, base, muted, offline, voice)
            widget.settle()
            widget.set_animated(False)
            if look == orb.SPEAKING:
                widget.set_voice(lambda: 0.8)
            elif look == orb.LISTENING:
                widget.set_level(0.6)
            best = None
            for _attempt in range(3):          # the best of three medians: a busy machine must not fail it
                times = []
                for _ in range(41):
                    t0 = time.perf_counter()
                    widget.repaint()
                    times.append((time.perf_counter() - t0) * 1000)
                times.sort()
                best = times[20] if best is None else min(best, times[20])
                if best <= 3.0:
                    break
            self.assertLessEqual(best, 3.0, look)
            widget.set_voice(None)
        self.assertLessEqual(widget.backdrop_count(), orb.SIGHT_LAYERS)

    def test_mini_orb_moves_only_while_active(self) -> None:
        stage = Stage()
        self.addCleanup(stage.deleteLater)
        self.addCleanup(stage.hide)
        mini = orb.MiniOrb(stage)
        stage.show()
        settle()
        self.assertFalse(mini.is_animating())
        self.assertIsNone(mini.frame_interval())
        mini.set_state(orb.THINKING)
        self.assertTrue(mini.is_animating())
        self.assertEqual(mini.frame_interval(), orb.FRAME_MINI_MS)     # 15 fps, as the spec says
        mini.set_look(orb.NEEDS, muted=True)
        self.assertFalse(mini.is_animating())
        self.assertEqual(mini.accessibleName(), "Jarvis, muted")
        self.assertFalse(mini.grab().isNull())

    def test_the_mini_ring_turns_to_face_only_while_it_runs(self) -> None:
        mini = orb.MiniOrb()
        self.addCleanup(mini.deleteLater)
        clock = [5.0]
        mini._now = lambda: clock[0]
        mini.set_state(orb.NEEDS)                                      # at rest: its pose at once
        self.assertEqual(mini._pose["tilt"].value(clock[0]), orb.POSES[orb.NEEDS].tilt)
        mini.set_voice_listening(True)
        mini.set_state(orb.LISTENING)                                  # it runs: it turns in 420 ms
        self.assertEqual(mini.ring_look(), orb.LISTENING)
        self.assertTrue(0.0 < mini._pose["tilt"].value(clock[0] + 0.1) < 58.0)
        self.assertEqual(mini._pose["tilt"].value(clock[0] + 0.43), 0.0)
        mini.set_recording(True)
        self.assertTrue(mini.is_recording())
        self.assertFalse(mini.grab().isNull())


class DeepIdleTests(_OrbCase):
    """After 2 minutes with no look change and no input, a resting ring eases to half speed at 5 fps."""

    def test_two_quiet_minutes_ease_into_deep_idle(self) -> None:
        _stage, widget = self.stage()
        widget.settle()
        self.assertEqual(widget.frame_interval(), orb.FRAME_IDLE_MS)
        clock = orb.frame_clock()
        clock._activity_at = time.monotonic() - orb.DEEP_IDLE_S - 1     # two quiet minutes have passed
        wait(250)                                                      # the next frame notices
        self.assertTrue(clock.deep())
        self.assertEqual(widget.frame_interval(), orb.FRAME_ACTIVE_MS)  # the 1.5 s ease runs smoothly
        widget.settle()
        self.assertEqual(widget.frame_interval(), orb.FRAME_DEEP_MS)    # then 5 fps
        self.assertAlmostEqual(widget.pace(), orb.DEEP_PACE)
        self.assertEqual(widget.sample().sway, 0.0)                     # the breathing alone
        self.assertTrue(widget.is_animating())
        wait(250)
        self.assertEqual(clock.interval(), orb.FRAME_DEEP_MS)

    def test_a_look_change_input_or_activation_ends_it_at_once(self) -> None:
        stage, widget = self.stage()
        clock = orb.frame_clock()
        orb.watch_input()
        for wake in ("look", "key", "click", "activation", "note"):
            with self.subTest(wake=wake):
                clock.set_deep(True)
                widget.settle()
                self.assertEqual(widget.frame_interval(), orb.FRAME_DEEP_MS)
                if wake == "look":
                    widget.set_state(orb.NEEDS)
                elif wake == "key":
                    QApplication.sendEvent(stage, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                                                            Qt.KeyboardModifier.NoModifier, "a"))
                elif wake == "click":
                    QTest.mouseClick(stage, Qt.MouseButton.LeftButton)
                elif wake == "activation":
                    QApplication.sendEvent(stage, QEvent(QEvent.Type.WindowActivate))
                else:
                    orb.note_activity()
                self.assertFalse(clock.deep(), wake)
                widget.settle()
                self.assertEqual(widget.frame_interval(), orb.FRAME_IDLE_MS)
                self.assertEqual(widget.pace(), 1.0)
                widget.set_state(orb.IDLE)

    def test_active_states_keep_30_fps_and_hidden_stays_still(self) -> None:
        stage, widget = self.stage()
        orb.frame_clock().set_deep(True)
        for state in (orb.LISTENING, orb.THINKING, orb.SPEAKING):
            widget.set_state(state)
            orb.frame_clock().set_deep(True)            # the change woke it; deep idle again
            widget.settle()
            self.assertEqual(widget.frame_interval(), orb.FRAME_ACTIVE_MS, state)
        widget.set_state(orb.MUTED)
        orb.frame_clock().set_deep(True)
        widget.settle()
        self.assertEqual(widget.frame_interval(), orb.FRAME_DEEP_MS)
        stage.hide()
        settle()
        self.assertFalse(widget.is_animating())
        self.assertFalse(orb.frame_clock().running())

    def test_the_slower_pace_never_makes_the_scanner_jump(self) -> None:
        widget = self.ring()
        widget.set_state(orb.WAITING)
        widget.settle()

        def step(a: float, b: float) -> float:
            return abs(((b - a + 180.0) % 360.0) - 180.0)

        widget.sample(10.0)
        before = widget.sample(10.1).head
        step_full = step(before, widget.sample(10.2).head)               # 0.1 s at full speed
        orb.frame_clock().set_deep(True)
        widget._tw["pace"].settle()
        widget.sample(10.4)                       # one 0.2 s step (a 5 fps frame) at half speed
        step_deep = step(widget.sample(10.6).head, widget.sample(10.8).head)
        self.assertGreater(step_full, 0.0)
        self.assertAlmostEqual(step_deep, step_full, delta=0.05 * step_full)   # 2x the time at half speed
        self.assertGreater(widget._lag, 0.0)       # the breathing clock fell behind, smoothly


if __name__ == "__main__":
    unittest.main()
