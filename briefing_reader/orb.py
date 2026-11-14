"""The Jarvis ring, the Halo band (DESIGN-SPEC section 4): ``OrbWidget`` (Home's hero 200-320 px; 120 px on the
column head, Briefing and Plan; 92 px in the HUD's 104 px box) and ``MiniOrb`` (36 px: the Dock, the toast, the LIVE
pop-out). The API is the orb's (HOLOGRAM_SPEC section 12); only the drawing is new.

Geometry (a 360-unit box, its centre at 0, 0)
    A fixed, face-on **sight**: a ring at r156 (cyan 0.22; 0.5 while listening; dashed 10/9 on the offline voice),
    four major ticks r156-167 (r174 while listening), and at hero size 28 minor ticks r156-160 and faint rings at r172
    and r92. One **band** of light segments, r119-135, each 70 % of its step (``segments_for``: 64 at 200-320 px, 48
    at 120 px, 32 at 92 px; 16 at 36 px, where it runs r100-152), with tracks at r112 (0.32) and r141 (0.14; none at
    92 px). The band is a circle tipped by tau about the horizontal axis and then rolled by rho, orthographic
    (``project``): a band point at angle theta (clockwise from 3 o'clock) and radius r is (r cos theta, r sin theta);
    y' = y cos tau; X = x cos rho - y' sin rho, Y = x sin rho + y' cos rho. In QPainter that is ``translate;
    rotate(rho); scale(1, cos tau)`` over segment polygons built once per size. A segment is in the far half when
    sin theta sin tau < 0 (``is_far``): the far half is drawn before the core, and each segment takes the depth
    alpha 0.5 + 0.25 (sin theta + 1); below tau 3 deg there is no split (``SPLIT_TILT``). The **core** (a gradient,
    r16) and its glow (to r54); the lock-on **brackets**, corner Ls on a square of half-side 184 (open) to 164
    (locked), arms of 30 units at hero size, 40 at 120 px and 48 at 92 px, 1.5 px.

Looks (``ring_look(base, muted, offline, voice)``; ``POSES``: each look's tilt, roll and brackets)
    STANDBY (idle): 58 / -22 deg, the band at 0.66 breathing +-8 % over 4 s and swaying +-3 deg over 9 s (not at 92
    px), brackets open at 0.16. LISTENING by voice (the talk key or "Hey Jarvis": ``set_voice_listening(True)``):
    level and locked, notches at the four axes, a crosshair r104-30, a centre ring r16, a core pip r6, the brackets
    closed at 0.95; the level (``set_level``: the mic, 0..1) lights round(32 L) segments per side upward from 6
    o'clock at 0.95 (``lit_per_side``; smoothed with a 30 ms attack and a 180 ms release; with motion off three
    steps, 8 / 20 / 32), the rest at 0.20. LISTENING while typing (look ``TYPING``, the default): leans to 20 / -6
    deg, the bottom four segments lit, brackets half closed at 0.55. THINKING: 68 / -24 deg, a white head with a
    14-segment tail, a turn in 1.4 s. SPEAKING: 14 / -4 deg, each segment reaches out up to 22 units with the voice
    (today's 64 spikes reborn: ``spike_lengths``), an echo ring at r168 (hero size), core r19 and glow r70, brackets
    0.6 closed at 0.40. NEEDS YOU: 58 / -18 deg, amber segments from 14 to 66 deg and an amber arc (22-62 deg) with a
    dot on the sight. MUTED: steel, nearly edge-on (80 / -6 deg), no glow, struck through. OFFLINE VOICE: 58 / -18
    deg, 4 lit of every 6, a flat core; the offline modifier dashes the sight (and flattens the core) under any look.
    WAITING: 58 / -18 deg, the band off but for the scanner, a head and a 6-segment tail, a turn in 4 s; only the
    tracks show the orbit. The tails keep their share at every size (14 of 64 = 7 of 32 = 4 of 16; 6 = 3 = 2).
    Modifiers: ``set_recording`` (a coral ring at r176 with a dot at 12 o'clock, at every size) and ``set_dim``
    (quiet hours: the whole ring at 70 %).

Sizes (``ring_tier``)
    200-320 px everything; 120 px 48 segments, no minor ticks or faint rings; 92 px 32 segments, no outer track, no
    sway; 36 px (``MiniOrb``, ``paint_mini_orb``) 16 segments and the pose, no sight: listening is a centre ring with
    four ticks, the offline voice a dashed centre ring, waiting a faint orbit track with its 2-segment tick, needs you
    an amber badge dot with a keyline in the ground's colour.

Motion
    Facing (voice listening) takes 420 ms (ease-out cubic), a release back 700 ms (ease-in-out sine; any change that
    tips the band back does too, one that brings it more level takes 420 ms); the brackets close over 280 ms from
    140 ms; the crosshair, the notches and the sight's listening tone fade in over the last 120 ms. The band's
    pattern cross-fades (90 ms when facing, so the mic lights from the first sample, before the band is level;
    280 ms otherwise). Calm stretches every time by 1.6, the thinking chase takes 2.2 s and nothing sways. Motion off
    (``[ui] motion`` off or Windows' reduced motion): the still pose per look, at once, and the clock stops; the
    thinking tail and the scanner stand still, nothing reaches out, and the mic level steps.

OrbWidget(px=HERO)
    ``set_state(state)`` (``MUTED`` / ``OFFLINE`` mean idle with that modifier alone), ``set_muted(bool)``,
    ``set_offline(bool)``, ``set_look(base, muted=, offline=)``, ``set_voice_listening(bool)``, ``state()``,
    ``look()``, ``ring_look()``, ``voice_listening()``. ``set_level(value | None)``: the mic level while listening
    by voice, the voice envelope while speaking (about 30 Hz); without fresh speaking levels a synthetic curve runs.
    ``set_voice(level_fn | None)``: ``level_fn()`` returns the voice's level now (or None: the synthetic curve), read
    once a frame while speaking. ``set_orb_size(px)``; the widget is ``widget_side(px)`` square (the open brackets
    reach a little outside the ``px`` box: ``orb_rect()``). The band's reach stays inside the sight, so
    ``voice_side(px)`` is ``widget_side(px)``: ``set_voice_room(px)`` never grows the widget and ``spike_room()`` is
    always the full reach. ``set_ground(fn)`` paints the window ground under the ring (``fn(painter, widget)``) and
    makes the widget opaque. ``set_phase(seconds | None)`` freezes the motion (stills); ``settle()`` ends
    transitions. ``play_intro(calm=False, start=None)``: the start-up, in nine staggered stages (``INTRO_STAGGER_S``,
    ``INTRO_LAYER_S``: about 1.2 s; calm about 1.6 s, without the turn): the core, its glow, the band sweeping in
    clockwise from 12 o'clock as it rolls in from -80 deg, the tracks, the sight drawing round from 12 o'clock, then
    the brackets; never under reduced motion. ``set_glow_share(share)``, ``glow_share()`` (the new look, part 5c:
    ``LIT_GLOW`` while a frame on the board is lit - the core glow halves, eased over 200 ms; MiniOrb has it too, and
    the paint does no more work). ``set_dim(level)``, ``dim()``; ``set_recording(bool)``,
    ``is_recording()``. ``is_animating()``, ``frame_interval()``, ``detail()`` (``"full"`` / ``"reduced"`` /
    ``"compact"``), ``sample(now=None) -> OrbFrame`` (the computed values, for tests), ``paint_times()``,
    ``snapshot()`` (the ring now without its ground, in a transparent pixmap of the widget's size: the switch to the
    desktop HUD flies it; also while hidden). Accessible name "Jarvis, <state>" (e.g. "Jarvis, needs you").

Paint (DESIGN-SPEC 4.4: a median of at most 3 ms at 320 px, x2.25 at 1.5x)
    The ground slice and the fixed sight are one cached pixmap per tone (ink, dashed, listening; at most
    ``SIGHT_LAYERS``), rebuilt on a resize; tones cross-fade as two blits. The band's segment polygons are built once
    per size (and per reach step while speaking) and filled in a few alpha buckets under one transform; tracks, rings
    and brackets are cosmetic pens. If the hero's paints average over ``DEGRADE_MS`` (2.5 ms) over 60 frames, the
    minor ticks, the faint rings, the outer track and the glow drop (``detail()`` "reduced"; logged once).

MiniOrb()
    The 36 px ring for the Dock's Home slot, the LIVE pop-out header and the toast: the same state API
    (``set_state`` / ``set_look`` / ``set_voice_listening``), ``set_level``, ``set_recording``; still at rest, 15 fps
    (``FRAME_MINI_MS``) only while listening, thinking or speaking (it turns to face only then; at rest it takes its
    pose at once).

Frame clock
    One shared precise QTimer drives every ring at the fastest interval any running ring wants: 33 ms listening /
    thinking / speaking, 100 ms otherwise, 200 ms in deep idle (``DEEP_IDLE_S``, two quiet minutes: the breathing
    alone, at ``DEEP_PACE``); each repaints only its own rect at its own rate. A ring stops when it or its window is
    hidden, the window is minimized, the session is locked (``set_session_locked(bool)``, or ``set_lock_probe(fn)``
    polled every 2 s while a ring wants frames), motion is off (one repaint per change) or ``set_animated(False)``.
    ``watch_input()`` installs the input watcher behind deep idle; ``note_activity()`` counts as input.
"""

from __future__ import annotations

import logging
import math
import time
import weakref
from collections import OrderedDict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Sequence

import shiboken6
from PySide6.QtCore import QEvent, QLineF, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from . import holo

logger = logging.getLogger(__name__)

IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
NEEDS = "needs"
WAITING = "waiting"
MUTED = "muted"
OFFLINE = "offline"
TYPING = "typing"              # a look, not a state: LISTENING while the owner types (only the voice locks the ring)
BASE_STATES = (IDLE, LISTENING, THINKING, SPEAKING, NEEDS, WAITING)
STATES = BASE_STATES + (MUTED, OFFLINE)
ACTIVE_STATES = (LISTENING, THINKING, SPEAKING)
LOOKS = (IDLE, LISTENING, TYPING, THINKING, SPEAKING, NEEDS, MUTED, OFFLINE, WAITING)
READOUTS = {
    IDLE: "STANDBY", LISTENING: "LISTENING", THINKING: "THINKING", SPEAKING: "SPEAKING", NEEDS: "NEEDS YOU",
    WAITING: "WAITING FOR THE AM BRIEFING", MUTED: "MUTED", OFFLINE: "OFFLINE VOICE",
}
HERO = 320
COMPACT = 120
MINI = 36
COMPACT_BELOW = 200            # sizes under 200 px get the compact level of detail (48 or 32 segments)
HUD_BELOW = 106                # ... and under this the HUD's (32 segments, no outer track, no sway)
FRAME_ACTIVE_MS = 33           # 30 fps: listening, thinking, speaking
FRAME_IDLE_MS = 100            # 10 fps: idle, needs you, offline voice
FRAME_SLOW_MS = 100            # 10 fps: muted, waiting
FRAME_DEEP_MS = 200            # 5 fps: deep idle (the breathing alone)
FRAME_MINI_MS = 66             # 15 fps: the 36 px ring while listening, thinking or speaking
INTRO_LAYERS = 9               # the start-up's stages: core, glow, the band (four), tracks, sight, brackets
INTRO_STAGGER_S = 0.075        # ... each starts this much after the one before
INTRO_LAYER_S = 0.6            # ... and takes this long
INTRO_TURN = -80.0             # the band rolls in from this far (lively)
INTRO_CALM_STAGGER_S = 0.1     # calm: no turn, slower
INTRO_CALM_LAYER_S = 0.8
DEEP_IDLE_S = 120.0            # no look change and no input this long: deep idle
DEEP_PACE = 0.5                # deep idle runs the motion at half speed (the breathing alone: no sway) ...
DEEP_EASE_S = 1.5              # ... eased in over this long
WAKE_EASE_S = 0.4              # and back to full speed this fast
MAX_STEP_S = 0.25              # the longest step one frame advances (a 5 fps frame is 0.2 s)
DEGRADE_MS = 2.5               # paints averaging over this drop the minor ticks, outer track and glow
LEVEL_FRESH_S = 0.3
LOCK_POLL_MS = 2000
SIGHT_LAYERS = 6               # cached ground + sight pixmaps per ring (tones: ink, dashed, listening)

# The 360-unit box (DESIGN-SPEC 4.1)
SIGHT_R = 156.0
MAJOR_TICK = 11.0              # r156 -> r167
MAJOR_TICK_LISTEN = 18.0       # r156 -> r174 while listening
MINOR_TICK = 4.0
MINOR_STEP = 11.25             # 32 positions, the four axes are the major ticks
FAINT_RINGS = ((172.0, 0.08), (92.0, 0.10))
BAND_IN, BAND_OUT = 119.0, 135.0
DOCK_IN, DOCK_OUT = 100.0, 152.0
GAP = 0.3                      # each segment is 70 % of its step
TRACK_IN, TRACK_OUT = 112.0, 141.0
DOCK_TRACK = 126.0
CROSS_OUT, CROSS_IN = 104.0, 30.0
CENTRE_RING = 16.0
BRACKET_OPEN, BRACKET_LOCKED = 184.0, 164.0
ECHO_R = 168.0
REC_R, REC_DOT = 176.0, 5.0
NEEDS_RUN = (14.0, 66.0)       # amber band segments (degrees in the band's plane, clockwise from 3 o'clock)
NEEDS_ARC = (22.0, 62.0)       # the amber arc on the sight
STRIKE = 118.0                 # muted: the strike from (-118, 118) to (118, -118)
SPLIT_TILT = 3.0               # below this tilt there is no far / near split
MINI_BOX = 372.0               # the 36 px ring shows the 360 box with a 6-unit margin (its brackets-free viewBox)

# Timing (DESIGN-SPEC 4.3 and 5)
FACE_S = 0.42                  # coming level: ease-out cubic
RELEASE_S = 0.70               # tipping back: ease-in-out sine
BRACKET_DELAY_S = 0.14
BRACKET_S = 0.28
SIGHT_S = 0.12                 # the crosshair and notches fade in over the last 120 ms of the turn
GLOW_SHARE_S = 0.20            # the core glow eases to its share (a lit frame halves it) with the lit brackets
LIT_GLOW = 0.5                 # the core glow's share while a frame on the board is lit (DESIGN-SPEC 1, 3.4)
BAND_FADE_S = 0.28
BAND_FADE_FACING_S = 0.09
CALM_STRETCH = 1.6
THINK_TURN_S = 1.4
THINK_TURN_CALM_S = 2.2
WAIT_TURN_S = 4.0
BREATH_S = 4.0
BREATH_DEPTH = 0.08
SWAY_S = 9.0
SWAY_DEG = 3.0
MIC_ATTACK_S = 0.03
MIC_RELEASE_S = 0.18
MIC_STEPS = (8, 20, 32)        # motion off: the mic lights this many per side (of 32)
THINK_HEAD = 28.0              # the chase's head at rest (motion off, stills)
THINK_TAIL = 14                # of 64 segments
WAIT_HEAD = 150.0
WAIT_TAIL = 6

# The speaking reach: "today's 64 spikes" became each segment reaching out with the voice
SPIKES = 64                    # the hero band's segments
SPIKE_MIN = 2.0                # the reach at rest (units) ...
SPIKE_MAX = 22.0               # ... and at the loudest (lively): r135 + 22 stays inside the sight (r156)
SPIKE_CALM_MAX = 11.0          # calm: gentler
SPIKE_STEP_S = 0.085           # the pattern changes this often (eased between steps; calm 0.14 s)
SPIKE_CALM_STEP_S = 0.14
SPIKE_FADE_S = 0.35
REACH_STEP = 0.5               # reach polygons are cached per half unit

TIER_HERO, TIER_MID, TIER_HUD, TIER_DOCK = "hero", "mid", "hud", "dock"
SEGMENTS = {TIER_HERO: 64, TIER_MID: 48, TIER_HUD: 32, TIER_DOCK: 16}
ARMS = {TIER_HERO: 30.0, TIER_MID: 40.0, TIER_HUD: 48.0}
_FILL_OPAQUE = Qt.GlobalColor.black  # under a ground: an opaque pixmap (no alpha) copies fastest

_STD = holo.bezier(holo.EASE_STD)
_EASE = holo.bezier(holo.CSS_EASE)
_IN_OUT = holo.bezier(holo.CSS_EASE_IN_OUT)
_OUT = holo.bezier(holo.EASE_OUT)


def _out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _in_out_sine(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


@dataclass(frozen=True)
class Pose:
    """A look's still pose: the band's tilt and roll (degrees) and the brackets (close 0 open .. 1 locked; alpha)."""

    tilt: float
    roll: float
    close: float
    brackets: float


POSES = {
    IDLE: Pose(58.0, -22.0, 0.0, 0.16),
    LISTENING: Pose(0.0, 0.0, 1.0, 0.95),
    TYPING: Pose(20.0, -6.0, 0.5, 0.55),
    THINKING: Pose(68.0, -24.0, 0.0, 0.0),
    SPEAKING: Pose(14.0, -4.0, 0.6, 0.40),
    NEEDS: Pose(58.0, -18.0, 0.0, 0.16),
    MUTED: Pose(80.0, -6.0, 0.0, 0.0),
    OFFLINE: Pose(58.0, -18.0, 0.0, 0.10),
    WAITING: Pose(58.0, -18.0, 0.0, 0.0),
}
_CORE = {   # core r, glow r, glow alpha (units of 360; the 36 px ring: core x 1.8, glow x 1.2)
    IDLE: (16.0, 54.0, 0.36), LISTENING: (6.0, 30.0, 0.30), TYPING: (13.0, 46.0, 0.34),
    THINKING: (14.0, 46.0, 0.34), SPEAKING: (19.0, 70.0, 0.55), NEEDS: (16.0, 54.0, 0.36), MUTED: (14.0, 0.0, 0.0),
    OFFLINE: (15.0, 0.0, 0.0), WAITING: (12.0, 34.0, 0.20),
}
CORE_HOT, CORE_FLAT, CORE_STEEL = "hot", "flat", "steel"
GLOW_MAX = 70.0

# The band's colours (bucket keys hold the index)
C_HOLO, C_HI, C_LO, C_AMBER, C_STEEL, C_WHITE = range(6)
_INK_NAMES = (holo.HOLO, holo.HOLO_HI, holo.HOLO_LO, holo.AMBER, holo.STEEL, holo.INK_1)
_INKS = tuple(QColor(name) for name in _INK_NAMES)


def state_word(base: str, muted: bool = False, offline: bool = False) -> str:
    """The header readout for a look: MUTED wins, then an active base state, then OFFLINE VOICE."""
    if muted:
        return READOUTS[MUTED]
    if base in (LISTENING, THINKING, SPEAKING, NEEDS):
        return READOUTS[base]
    if offline:
        return READOUTS[OFFLINE]
    return READOUTS.get(base, READOUTS[IDLE])


def frame_interval_ms(base: str, muted: bool = False, deep: bool = False) -> int:
    """Frame interval of a look: 33 ms listening / thinking / speaking; 200 ms in deep idle; 100 ms
    otherwise (idle, needs you, offline voice, muted, waiting)."""
    if base in ACTIVE_STATES:
        return FRAME_ACTIVE_MS
    if deep:
        return FRAME_DEEP_MS
    if muted or base == WAITING:
        return FRAME_SLOW_MS
    return FRAME_IDLE_MS


def ring_look(base: str, muted: bool = False, offline: bool = False, voice: bool = False) -> str:
    """The look the ring shows (one of ``LOOKS``), as the state word reads: MUTED wins; LISTENING is the locked
    voice look with ``voice`` (the talk key or "Hey Jarvis"), else ``TYPING``; an active base or NEEDS; then the
    offline voice; else idle or waiting."""
    if muted:
        return MUTED
    if base == LISTENING:
        return LISTENING if voice else TYPING
    if base in (THINKING, SPEAKING, NEEDS):
        return base
    if offline:
        return OFFLINE
    return base if base in (IDLE, WAITING) else IDLE


def widget_side(px: int) -> int:
    """The square widget holding a ring of ``px``: room for the open brackets (half-side 184 of 180 units) and the
    recording dot, rounded up to a multiple of 4 px, so an opaque ring placed on a multiple of 4 covers whole device
    pixels at 125, 150 and 175 % scaling too (never a column of the window left unpainted)."""
    side = px + 2 * math.ceil(px * 0.045)
    return side + (-side) % 4


def voice_side(px: int) -> int:
    """The square widget a ring of ``px`` needs for its full voice reach: the band reaches out to at most r157,
    inside the sight, so it is ``widget_side(px)`` (the widget never grows while Jarvis speaks)."""
    return widget_side(px)


def ring_tier(px: int) -> str:
    """The level of detail for a ring of ``px``: hero (200-320), mid (120), hud (92)."""
    if px >= COMPACT_BELOW:
        return TIER_HERO
    return TIER_MID if px >= HUD_BELOW else TIER_HUD


def segments_for(px: int) -> int:
    """The band's segments for a ring of ``px`` (64 / 48 / 32; the 36 px ``MiniOrb`` has 16)."""
    if px <= MINI:
        return SEGMENTS[TIER_DOCK]
    return SEGMENTS[ring_tier(px)]


def project(r: float, theta: float, tilt: float, roll: float) -> tuple[float, float]:
    """Where the band point at ``theta`` degrees (clockwise from 3 o'clock) and radius ``r`` lands, relative to the
    centre (units), with the band tipped by ``tilt`` about the horizontal axis and rolled by ``roll`` (degrees)."""
    th, tau, rho = math.radians(theta), math.radians(tilt), math.radians(roll)
    x, y = r * math.cos(th), r * math.sin(th) * math.cos(tau)
    return x * math.cos(rho) - y * math.sin(rho), x * math.sin(rho) + y * math.cos(rho)


def band_transform(tilt: float, roll: float, scale: float = 1.0) -> QTransform:
    """The painter transform ``project`` describes (around the origin, ``scale`` px per unit): rotate, then squash."""
    transform = QTransform()
    transform.scale(scale, scale)
    transform.rotate(roll)
    transform.scale(1.0, max(0.02, math.cos(math.radians(tilt))))
    return transform


def is_far(theta: float, tilt: float) -> bool:
    """A band point at ``theta`` is in the far half (drawn before the core) - never below ``SPLIT_TILT``."""
    if tilt < SPLIT_TILT:
        return False
    return math.sin(math.radians(theta)) * math.sin(math.radians(tilt)) < -0.01


def depth_alpha(theta: float, tilt: float) -> float:
    """The depth shading of a segment at ``theta``: 0.5 + 0.25 (sin theta + 1); 1 below ``SPLIT_TILT``."""
    if tilt < SPLIT_TILT:
        return 1.0
    return 0.5 + 0.25 * (math.sin(math.radians(theta)) + 1.0)


def lit_per_side(level: float, segments: int = SPIKES, *, still: bool = False) -> int:
    """How many segments the mic level 0..1 lights on each side of 6 o'clock: round(32 L) of the hero's 32 per side
    (scaled to ``segments`` / 2); motion off (``still``) uses three steps, 8, 20 or 32 (0 while silent)."""
    level = max(0.0, min(1.0, float(level)))
    count = round(32 * level)
    if still and count:
        count = MIC_STEPS[0] if count <= 14 else MIC_STEPS[1] if count <= 26 else MIC_STEPS[2]
    return round(count * (segments // 2) / 32)


def tail_length(look: str, segments: int) -> int:
    """The chase's tail at ``segments``: thinking 14 of 64 (7 of 32, 4 of 16), waiting 6 of 64 (3 of 32, 2 of 16)."""
    share = THINK_TAIL if look == THINKING else WAIT_TAIL
    return max(1, math.ceil(share * segments / SPIKES - 1e-9))


def _spike_noise(i: int, step: int) -> float:
    value = math.sin(i * 12.9898 + step * 78.233) * 43758.5453
    return value - math.floor(value)


def spike_lengths(level: float, t: float, *, calm: bool = False, longest: float = SPIKE_MAX,
                  count: int = SPIKES) -> tuple[float, ...]:
    """How far each of ``count`` segments reaches out (units) for the voice's ``level`` 0..1 at time ``t`` (s): each
    a share of the level that changes every ``SPIKE_STEP_S`` (eased between steps), every eighth a little further;
    at least ``SPIKE_MIN``, at most ``longest`` (calm: at most ``SPIKE_CALM_MAX``)."""
    top = min(longest, SPIKE_CALM_MAX) if calm else longest
    span = max(0.0, top - SPIKE_MIN)
    level = max(0.0, min(1.0, level))
    period = SPIKE_CALM_STEP_S if calm else SPIKE_STEP_S
    position = t / period
    step = math.floor(position)
    x = position - step
    ease = x * x * (3.0 - 2.0 * x)
    out = []
    for i in range(count):
        a = _spike_noise(i, step)
        b = _spike_noise(i, step + 1)
        n = a + (b - a) * ease
        amp = level * (0.3 + 0.7 * n) + (0.2 * level if i % 8 == 0 else 0.0)
        out.append(SPIKE_MIN + span * min(1.0, amp))
    return tuple(out)

# --------------------------------------------------------------------------
# Curves
# --------------------------------------------------------------------------


def _frames(points: tuple[tuple[float, ...], ...], p: float, ease: Callable[[float], float]) -> tuple[float, ...]:
    """Keyframes ``(pos, v1, v2, ...)`` at progress ``p`` (0..1), ``ease`` applied per segment."""
    for (p0, *a), (p1, *b) in zip(points, points[1:]):
        if p <= p1:
            k = ease((p - p0) / (p1 - p0)) if p1 > p0 else 1.0
            return tuple(x + (y - x) * k for x, y in zip(a, b))
    return tuple(points[-1][1:])


def _linear(x: float) -> float:
    return x


def _cycle(t: float, period: float) -> float:
    return (t % period) / period


_LISTEN = ((0.0, 0.92, 1.0), (0.5, 1.02, 1.0), (1.0, 0.92, 1.0))
_SPEAK = ((0.0, 1.0), (0.11, 1.16), (0.22, 0.97), (0.35, 1.11), (0.46, 1.01), (0.58, 1.2), (0.70, 0.96),
          (0.84, 1.09), (1.0, 1.0))


def synthetic_level(t: float) -> float:
    """The speaking envelope without voice data: the ``hgSpeak`` curve mapped to 0..1."""
    scale = _frames(_SPEAK, _cycle(t, 1.05), _linear)[0]
    return max(0.0, min(1.0, (scale - 0.96) / 0.24))


def breath_at(t: float) -> float:
    """Idle's breathing: the band's strength 1 +- 8 % over 4 s."""
    return 1.0 + BREATH_DEPTH * math.sin(2.0 * math.pi * t / BREATH_S)


def sway_at(t: float) -> float:
    """Idle's sway: degrees added to the roll, +-3 over 9 s."""
    return SWAY_DEG * math.sin(2.0 * math.pi * t / SWAY_S)

# --------------------------------------------------------------------------
# The band's pattern per look
# --------------------------------------------------------------------------


def _ang_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def segment_angle(i: int, segments: int, phase: float = 0.0) -> float:
    """The centre of segment ``i`` (degrees clockwise from 3 o'clock; segment 0 starts at 12 o'clock); ``phase``
    (0..0.5 of a step) turns the band in its plane: at 0.5 a segment is centred on each axis (voice listening)."""
    return -90.0 + (i + 0.5 + phase) * 360.0 / segments


def band_pattern(look: str, segments: int, *, phase: float = 0.0, lit: int = 0, head: float | None = None,
                 reach: Sequence[float] | None = None, sight: float = 0.0) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Each segment's alpha (0: off; before the depth shading and idle's breathing) and colour (``C_*``) in
    ``look``: ``lit`` segments per side from 6 o'clock (listening), the chase's ``head`` in degrees (thinking,
    waiting), each segment's ``reach`` in units (speaking), ``sight`` 0..1 cutting the notches (listening)."""
    n = segments
    step = 360.0 / n
    alphas = [0.0] * n
    colors = [C_HOLO] * n
    if look in (THINKING, WAITING):
        h = (THINK_HEAD if look == THINKING else WAIT_HEAD) if head is None else head
        tail = tail_length(look, n)
    for i in range(n):
        th = -90.0 + (i + 0.5 + phase) * step
        a, c = 0.62, C_HOLO
        if look == IDLE:
            a = 0.66
        elif look == LISTENING:
            if lit >= 1 and _ang_dist(th, 90.0) <= (lit + 0.01) * step:
                a, c = 0.95, C_HI
            else:
                a = 0.20
            if sight > 0.0 and abs(((th + 45.0) % 90.0) - 45.0) < 0.75 * step:
                a *= 1.0 - sight                     # the notches at the four axes
        elif look == TYPING:
            if _ang_dist(th, 90.0) <= 2.2 * step:
                a, c = 0.95, C_HI
            else:
                a = 0.5
        elif look == THINKING or look == WAITING:
            k = ((h - th) % 360.0) / step
            if k < tail:
                if look == THINKING:
                    c, a = (C_WHITE if k < 1.5 else C_HI), 1.0 - k / (tail + 2)
                else:
                    c, a = (C_WHITE if k < 1.0 else C_HI), 1.0 - k / (tail + 1)
            else:
                a = 0.24 if look == THINKING else 0.0
        elif look == SPEAKING:
            if reach:
                amp = min(1.0, reach[i] / SPIKE_MAX)
                a, c = 0.55 + 0.45 * amp, (C_HI if amp > 0.6 else C_HOLO)
        elif look == NEEDS:
            if NEEDS_RUN[0] < th % 360.0 < NEEDS_RUN[1]:
                a, c = 1.0, C_AMBER
            else:
                a = 0.58
        elif look == MUTED:
            a, c = 0.55, C_STEEL
        elif look == OFFLINE:
            a, c = (0.0 if i % 6 >= 4 else 0.5), C_LO
        alphas[i] = a
        colors[i] = c
    return tuple(alphas), tuple(colors)

# --------------------------------------------------------------------------
# Geometry (built once per size)
# --------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _band_polygons(n: int, r_in: float, r_out: float) -> tuple[QPolygonF, ...]:
    """The segments at phase 0 in the band's plane: each a quad (outer edge, inner edge) of 70 % of its step."""
    step = 360.0 / n
    half = step * (1.0 - GAP) / 2.0
    out = []
    for i in range(n):
        c = -90.0 + (i + 0.5) * step
        a0, a1 = math.radians(c - half), math.radians(c + half)
        c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
        out.append(QPolygonF([QPointF(r_out * c0, r_out * s0), QPointF(r_out * c1, r_out * s1),
                              QPointF(r_in * c1, r_in * s1), QPointF(r_in * c0, r_in * s0)]))
    return tuple(out)


@lru_cache(maxsize=8192)
def _reach_polygon(n: int, r_in: float, r_out: float, i: int, q: int) -> QPolygonF:
    """Segment ``i`` reaching ``q`` half units further out (speaking)."""
    if q <= 0:
        return _band_polygons(n, r_in, r_out)[i]
    step = 360.0 / n
    half = step * (1.0 - GAP) / 2.0
    c = -90.0 + (i + 0.5) * step
    a0, a1 = math.radians(c - half), math.radians(c + half)
    r = r_out + q * REACH_STEP
    return QPolygonF([QPointF(r * math.cos(a0), r * math.sin(a0)), QPointF(r * math.cos(a1), r * math.sin(a1)),
                      QPointF(r_in * math.cos(a1), r_in * math.sin(a1)),
                      QPointF(r_in * math.cos(a0), r_in * math.sin(a0))])


@lru_cache(maxsize=64)
def _bracket_path(d: float, arm: float) -> QPainterPath:
    """The four corner Ls on a square of half-side ``d``."""
    path = QPainterPath()
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            path.moveTo(sx * d, sy * (d - arm))
            path.lineTo(sx * d, sy * d)
            path.lineTo(sx * (d - arm), sy * d)
    return path


@lru_cache(maxsize=4)
def _minor_ticks() -> tuple[QLineF, ...]:
    lines = []
    for j in range(32):
        a = j * MINOR_STEP
        if a % 90.0 == 0.0:
            continue
        r = math.radians(a)
        lines.append(QLineF(SIGHT_R * math.cos(r), SIGHT_R * math.sin(r), (SIGHT_R + MINOR_TICK) * math.cos(r),
                            (SIGHT_R + MINOR_TICK) * math.sin(r)))
    return tuple(lines)


def _axis_lines(r0: float, r1: float, draw: float = 1.0) -> list[QLineF]:
    """Lines from r0 to r1 on the four axes (12 o'clock first); ``draw`` < 1 keeps those the sight has reached."""
    lines = []
    for k, a in enumerate((-90.0, 0.0, 90.0, 180.0)):
        if draw < 1.0 and draw < k / 4.0 + 0.02:
            continue
        r = math.radians(a)
        lines.append(QLineF(r0 * math.cos(r), r0 * math.sin(r), r1 * math.cos(r), r1 * math.sin(r)))
    return lines


_COLORS: dict[tuple[int, int], QColor] = {}
_PENS: dict[tuple, QPen] = {}


def _rgba(ink: QColor, a: float) -> QColor:
    """``ink`` at alpha ``a``, cached by (rgb, alpha / 1024) so steady frames reuse the objects."""
    key = (ink.rgb(), max(0, min(1024, round(a * 1024))))
    color = _COLORS.get(key)
    if color is None:
        if len(_COLORS) > 4096:
            _COLORS.clear()
        color = QColor(ink)
        color.setAlphaF(key[1] / 1024)
        _COLORS[key] = color
    return color


def _pen(color: QColor, width: float = 1.0, *, cap: Qt.PenCapStyle = Qt.PenCapStyle.FlatCap,
         dash: tuple[float, ...] | None = None) -> QPen:
    """A cosmetic pen (``width`` px whatever the transform: the tracks stay 1 px on a tipped band)."""
    key = (color.rgba(), round(width, 3), cap, dash)
    pen = _PENS.get(key)
    if pen is None:
        if len(_PENS) > 2048:
            _PENS.clear()
        pen = QPen(color, width)
        pen.setCosmetic(True)
        pen.setCapStyle(cap)
        if dash:
            pen.setDashPattern([d / max(0.5, width) for d in dash])
        _PENS[key] = pen
    return pen


_CORE_BRUSH: QBrush | None = None
_GLOW_BRUSH: QBrush | None = None


def _core_brush() -> QBrush:
    """The core: white at its top-left, cyan-hi, cyan at the rim (the mock-up's gradient at .45 .42)."""
    global _CORE_BRUSH
    if _CORE_BRUSH is None:
        gradient = QRadialGradient(QPointF(0.45, 0.42), 0.5)
        gradient.setCoordinateMode(QGradient.CoordinateMode.ObjectMode)
        gradient.setColorAt(0.0, QColor("#FFFFFF"))
        gradient.setColorAt(0.45, QColor(holo.HOLO_HI))
        gradient.setColorAt(1.0, QColor(holo.HOLO))
        _CORE_BRUSH = QBrush(gradient)
    return _CORE_BRUSH


def _glow_brush() -> QBrush:
    """The glow at full strength (cyan to clear at its edge); drawn at the look's glow alpha."""
    global _GLOW_BRUSH
    if _GLOW_BRUSH is None:
        gradient = QRadialGradient(QPointF(0.5, 0.5), 0.5)
        gradient.setCoordinateMode(QGradient.CoordinateMode.ObjectMode)
        gradient.setColorAt(0.0, QColor(holo.HOLO))
        gradient.setColorAt(1.0, holo.alpha(holo.HOLO, 0.0))
        _GLOW_BRUSH = QBrush(gradient)
    return _GLOW_BRUSH


_GLOW_PIXMAPS: dict[tuple[int, float], QPixmap] = {}


def _glow_pixmap(px: int, dpr: float) -> QPixmap:
    """The glow at ``GLOW_MAX`` for a ring of ``px`` as a pixmap (a blit per frame, scaled to the look's radius)."""
    key = (px, round(dpr, 3))
    pixmap = _GLOW_PIXMAPS.get(key)
    if pixmap is not None:
        return pixmap
    side = max(2, math.ceil(2 * GLOW_MAX * px / 360.0 * dpr))
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_glow_brush())
        painter.drawEllipse(QRectF(0, 0, side, side))
    finally:
        painter.end()
    if len(_GLOW_PIXMAPS) > 16:
        _GLOW_PIXMAPS.clear()
    _GLOW_PIXMAPS[key] = pixmap
    return pixmap


def _paint_sight(painter: QPainter, ink: QColor, *, hero: bool, fine: bool, listen: bool, dashed: bool,
                 draw: float = 1.0) -> None:
    """The fixed sight around the origin (units): the ring, the major ticks, and at hero size (``fine``) the minor
    ticks and the faint rings; ``draw`` < 1 draws the ring round from 12 o'clock (the start-up)."""
    if draw <= 0.0:
        return
    ring = _rgba(ink, 0.5 if listen else 0.22)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(ring, 1.0, dash=(10.0, 9.0) if dashed else None))
    if draw >= 1.0:
        painter.drawEllipse(QPointF(0, 0), SIGHT_R, SIGHT_R)
    else:
        painter.drawArc(QRectF(-SIGHT_R, -SIGHT_R, 2 * SIGHT_R, 2 * SIGHT_R), 90 * 16, -round(360 * draw * 16))
    tick = MAJOR_TICK_LISTEN if listen else MAJOR_TICK
    painter.setPen(_pen(_rgba(ink, 0.95 if listen else 0.5), 1.5))
    painter.drawLines(_axis_lines(SIGHT_R, SIGHT_R + tick, draw))
    if hero and fine:
        painter.setPen(_pen(_rgba(ink, 0.3 * min(1.0, draw)), 1.0))
        painter.drawLines(_minor_ticks())
        for r, a in FAINT_RINGS:
            painter.setPen(_pen(_rgba(ink, a * min(1.0, draw)), 1.0))
            painter.drawEllipse(QPointF(0, 0), r, r)


def _band_buckets(bands: Sequence[tuple[float, Sequence[float], Sequence[int], Sequence[float] | None]], n: int,
                  r_in: float, r_out: float, *, phase: float, tilt: float, sweep: Sequence[float] | None = None,
                  boost: float = 1.0) -> tuple[list[tuple[QColor, QPainterPath]], list[tuple[QColor, QPainterPath]]]:
    """The band as (far, near) lists of (colour, path): each segment's look(s) (``bands``: weight, alphas, colours,
    reach) with its depth shading (and ``boost``: headroom the painter's opacity takes back), grouped in alpha
    buckets of 1/20 per colour so a frame fills a few paths."""
    split = tilt >= SPLIT_TILT
    sin_tilt = math.sin(math.radians(tilt))
    step = 360.0 / n
    buckets: dict[tuple[bool, int, int], QPainterPath] = {}
    single = len(bands) == 1
    for i in range(n):
        th = math.radians(-90.0 + (i + 0.5 + phase) * step)
        s = math.sin(th)
        depth = 0.5 + 0.25 * (s + 1.0) if split else 1.0
        far = split and s * sin_tilt < -0.01
        gain = depth * boost * (sweep[i] if sweep is not None else 1.0)
        if single:
            weight, alphas, colors, reach = bands[0]
            entries: Sequence[tuple[int, float, float]] = ((colors[i], alphas[i] * weight, reach[i] if reach else 0.0),)
        else:
            (w0, a0, c0, r0), (w1, a1, c1, r1) = bands[0], bands[-1]
            x0, x1 = (r0[i] if r0 else 0.0), (r1[i] if r1 else 0.0)
            if c0[i] == c1[i] and abs(x0 - x1) < REACH_STEP:
                entries = ((c1[i], a0[i] * w0 + a1[i] * w1, x0 * w0 + x1 * w1),)
            else:
                entries = ((c0[i], a0[i] * w0, x0), (c1[i], a1[i] * w1, x1))
        for color, alpha, reach_units in entries:
            q = round(alpha * gain * 20.0)
            if q <= 0:
                continue
            key = (far, color, min(20, q))
            path = buckets.get(key)
            if path is None:
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.WindingFill)
                buckets[key] = path
            path.addPolygon(_reach_polygon(n, r_in, r_out, i, round(reach_units / REACH_STEP)))
    far_list, near_list = [], []
    for (far, color, q), path in buckets.items():
        (far_list if far else near_list).append((_rgba(_INKS[color], q / 20.0), path))
    return far_list, near_list


def _fill(painter: QPainter, buckets: list[tuple[QColor, QPainterPath]]) -> None:
    for color, path in buckets:
        painter.fillPath(path, color)

# --------------------------------------------------------------------------
# Tweens
# --------------------------------------------------------------------------


class _Tween:
    """A scalar easing from its value at the last retarget to the target over ``dur`` seconds (after ``delay``)."""

    __slots__ = ("a", "b", "t0", "dur", "ease")

    def __init__(self, value: float) -> None:
        self.a = self.b = float(value)
        self.t0 = 0.0
        self.dur = 0.0
        self.ease: Callable[[float], float] = _STD

    def to(self, value: float, now: float, dur: float, ease: Callable[[float], float] = _STD,
           delay: float = 0.0) -> None:
        value = float(value)
        if value == self.b:
            return
        self.a, self.b, self.t0, self.dur, self.ease = self.value(now), value, now + max(0.0, delay), dur, ease

    def value(self, now: float) -> float:
        if self.dur <= 0.0 or now >= self.t0 + self.dur:
            return self.b
        return self.a + (self.b - self.a) * self.ease(max(0.0, (now - self.t0) / self.dur))

    def done(self, now: float) -> bool:
        return self.dur <= 0.0 or now >= self.t0 + self.dur

    def settle(self) -> None:
        self.a, self.dur = self.b, 0.0


class _Fade:
    """A cross-fade between a previous and a current discrete value (a look, a tone, a core style)."""

    __slots__ = ("prev", "cur", "t0", "dur")

    def __init__(self, value: Any) -> None:
        self.prev = self.cur = value
        self.t0 = 0.0
        self.dur = 0.0

    def to(self, value: Any, now: float, dur: float, delay: float = 0.0) -> None:
        if value == self.cur:
            return
        self.prev = self.cur if self.mix(now) >= 0.5 else self.prev
        self.cur, self.t0, self.dur = value, now + max(0.0, delay), dur

    def mix(self, now: float) -> float:
        if self.dur <= 0.0 or now >= self.t0 + self.dur:
            return 1.0
        return _EASE(max(0.0, (now - self.t0) / self.dur))

    def done(self, now: float) -> bool:
        return self.mix(now) >= 1.0

    def settle(self) -> None:
        self.prev, self.dur = self.cur, 0.0

    def weights(self, now: float) -> tuple[tuple[Any, float], ...]:
        m = self.mix(now)
        return ((self.cur, 1.0),) if m >= 1.0 or self.prev == self.cur else ((self.prev, 1.0 - m), (self.cur, m))

# --------------------------------------------------------------------------
# Frame clock
# --------------------------------------------------------------------------


class _FrameClock(QObject):
    """One precise timer for every orb, at the fastest interval a running orb wants."""

    def __init__(self) -> None:
        super().__init__()
        self._wanted: weakref.WeakKeyDictionary[QWidget, int] = weakref.WeakKeyDictionary()
        self._timer = QTimer(self)
        # Windows rounds a coarse timer up to its 15.6 ms tick; a precise one keeps the rate.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(LOCK_POLL_MS)
        self._lock_timer.timeout.connect(self._poll_lock)
        self._locked = False
        self._probe: Callable[[], bool] | None = None
        self._orbs: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._activity_at = time.monotonic()
        self._deep = False

    def register(self, orb: QWidget) -> None:
        """Every orb hears deep idle start and end (also while it is still)."""
        self._orbs.add(orb)

    def want(self, orb: QWidget, interval_ms: int | None) -> None:
        if interval_ms is None:
            self._wanted.pop(orb, None)
        else:
            self._wanted[orb] = interval_ms
        self._retime()

    def wants(self, orb: QWidget) -> bool:
        return orb in self._wanted

    def running(self) -> bool:
        try:
            return self._timer.isActive()
        except RuntimeError:          # Qt is shutting down
            return False

    def interval(self) -> int:
        return self._timer.interval()

    def locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        if locked != self._locked:
            self._locked = locked
            self._retime()
            if not locked:
                for orb in list(self._wanted.keys()):
                    if shiboken6.isValid(orb):
                        orb.update()

    def set_probe(self, probe: Callable[[], bool] | None) -> None:
        self._probe = probe
        self._retime()

    # ---- deep idle ------------------------------------------------------------------------

    def deep(self) -> bool:
        return self._deep

    def idle_seconds(self) -> float:
        return time.monotonic() - self._activity_at

    def note_activity(self) -> None:
        """A look change, input or a window activation: deep idle ends at once (and its clock restarts)."""
        self._activity_at = time.monotonic()
        if self._deep:
            self.set_deep(False)

    def set_deep(self, deep: bool) -> None:
        """Enter or leave deep idle now (the clock does this itself after DEEP_IDLE_S; tests and harnesses)."""
        deep = bool(deep)
        if deep == self._deep:
            return
        self._deep = deep
        if not deep:
            self._activity_at = time.monotonic()
        for orb in list(self._orbs):
            if shiboken6.isValid(orb):
                orb._deep_changed(deep)
        self._retime()

    def _retime(self) -> None:
        try:
            live = [ms for orb, ms in list(self._wanted.items()) if shiboken6.isValid(orb)]
            if live and not self._locked:
                interval = min(live)
                if self._timer.interval() != interval:
                    self._timer.setInterval(interval)
                if not self._timer.isActive():
                    self._timer.start()
            elif self._timer.isActive():
                self._timer.stop()
            if live and self._probe is not None:
                if not self._lock_timer.isActive():
                    self._lock_timer.start()
            elif self._lock_timer.isActive():
                self._lock_timer.stop()
        except RuntimeError:          # timers already deleted while Qt shuts down
            pass

    def _poll_lock(self) -> None:
        if self._probe is None:
            return
        try:
            locked = bool(self._probe())
        except Exception as exc:  # noqa: BLE001 - an unknown answer keeps the orb running
            logger.debug("Lock probe failed: %s", exc)
            locked = False
        self.set_locked(locked)

    def _tick(self) -> None:
        now = time.monotonic()
        if not self._deep and now - self._activity_at >= DEEP_IDLE_S:
            self.set_deep(True)
        for orb, ms in list(self._wanted.items()):
            if not shiboken6.isValid(orb):
                self._wanted.pop(orb, None)
                continue
            if now - getattr(orb, "_last_frame", 0.0) >= (ms - 6) / 1000.0:
                orb._request_frame()


_clock: _FrameClock | None = None


def frame_clock() -> _FrameClock:
    global _clock
    if _clock is None:
        _clock = _FrameClock()
    return _clock


def set_session_locked(locked: bool) -> None:
    """Stop (True) or resume (False) every orb because the Windows session is locked."""
    frame_clock().set_locked(locked)


def set_lock_probe(probe: Callable[[], bool] | None) -> None:
    """A callable answering "is the session locked?", polled every 2 s while an orb wants frames."""
    frame_clock().set_probe(probe)


def note_activity() -> None:
    """Input or a state change outside the orbs: deep idle ends and its 2 minutes start again."""
    frame_clock().note_activity()


class _InputWatch(QObject):
    """The application-wide input watcher behind deep idle: only the event's type is looked at."""

    _TYPES = frozenset({QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                        QEvent.Type.MouseMove, QEvent.Type.WindowActivate, QEvent.Type.ApplicationActivate})

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() in self._TYPES:
            frame_clock().note_activity()
        return False


_input_watch: _InputWatch | None = None
_watched_targets: weakref.WeakSet[QObject] = weakref.WeakSet()


def watch_input(target: QObject | None = None) -> None:
    """Install the input watcher on ``target`` (once per target): a window's QWindow (the app passes its
    main window's handle, which sees every key, click, wheel and mouse move of that window and nothing
    else, so the watcher costs next to nothing), or, with None, the whole application."""
    global _input_watch
    from PySide6.QtWidgets import QApplication

    target = target or QApplication.instance()
    if target is None or target in _watched_targets:
        return
    if _input_watch is None or not shiboken6.isValid(_input_watch):
        _input_watch = _InputWatch()
    target.installEventFilter(_input_watch)
    _watched_targets.add(target)


def watching(target: QObject) -> bool:
    return target in _watched_targets


def _still() -> bool:
    """Motion is off: ``[ui] motion`` off, or Windows' reduced motion (holo.motion_level() says off for both)."""
    return holo.motion_level() == holo.MOTION_OFF


def _stretch() -> float:
    """Calm stretches every ring timing by 1.6."""
    return CALM_STRETCH if holo.motion_level() == holo.MOTION_CALM else 1.0

# --------------------------------------------------------------------------
# Shared widget behaviour
# --------------------------------------------------------------------------


class _OrbBase(QWidget):
    """Look, run conditions (hidden / minimized / locked / motion off) and the frame clock hook."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._base = IDLE
        self._muted = False
        self._offline = False
        self._voice = False               # LISTENING by voice (the talk key, "Hey Jarvis"); else it is typing
        self._animated = True
        self._frozen: float | None = None
        self._t_origin = time.monotonic()
        self._last_frame = 0.0
        self._watched: weakref.ref[QWidget] | None = None
        holo.motion_settings().changed.connect(self._on_motion_changed)
        frame_clock().register(self)
        self._update_name()

    # ---- look -------------------------------------------------------------

    def state(self) -> str:
        return self._base

    def is_muted(self) -> bool:
        return self._muted

    def is_offline(self) -> bool:
        return self._offline

    def look(self) -> tuple[str, bool, bool]:
        return self._base, self._muted, self._offline

    def ring_look(self) -> str:
        """The look painted now (``LOOKS``): typing and voice listening are both the state LISTENING."""
        return ring_look(self._base, self._muted, self._offline, self._voice)

    def voice_listening(self) -> bool:
        return self._voice

    def state_word(self) -> str:
        return state_word(self._base, self._muted, self._offline)

    def set_state(self, state: str) -> None:
        """A base state; ``MUTED`` / ``OFFLINE`` mean idle with only that modifier (the style guide's tiles)."""
        if state == MUTED:
            self.set_look(IDLE, muted=True, offline=False)
        elif state == OFFLINE:
            self.set_look(IDLE, muted=False, offline=True)
        else:
            self.set_look(state if state in BASE_STATES else IDLE, muted=self._muted, offline=self._offline)

    def set_muted(self, muted: bool) -> None:
        self.set_look(self._base, muted=muted, offline=self._offline)

    def set_offline(self, offline: bool) -> None:
        self.set_look(self._base, muted=self._muted, offline=offline)

    def set_look(self, base: str, *, muted: bool = False, offline: bool = False) -> None:
        base = base if base in BASE_STATES else IDLE
        if (base, bool(muted), bool(offline)) == (self._base, self._muted, self._offline):
            return
        self._base, self._muted, self._offline = base, bool(muted), bool(offline)
        frame_clock().note_activity()      # a state change ends deep idle
        self._look_changed(self._now())
        self._update_name()
        self._sync()
        self.update()

    def set_voice_listening(self, voice: bool) -> None:
        """LISTENING is the owner's voice (True: the talk key or "Hey Jarvis" - the ring comes level and locks) or
        typing (False, the default: it only leans). Set it before the look; it shows only while LISTENING."""
        voice = bool(voice)
        if voice == self._voice:
            return
        self._voice = voice
        if self._base == LISTENING:
            frame_clock().note_activity()
            self._look_changed(self._now())
            self._sync()
            self.update()

    def _look_changed(self, now: float) -> None:
        """Retarget the transitions (subclasses)."""

    def _deep_changed(self, _deep: bool) -> None:
        """Deep idle started or ended (the frame clock tells every orb)."""
        self._sync()

    def set_glow_share(self, share: float) -> None:
        """The core glow at ``share`` of its strength (the new look, part 5c: ``LIT_GLOW`` - it halves - while a frame
        on the board is lit, so one thing is bright at a time; 1.0 as it is). Nothing else of the ring changes and the
        paint does no more work: the glow's own alpha is scaled."""
        share = max(0.0, min(1.0, float(share)))
        if share == getattr(self, "_glow_share", 1.0):
            return
        self._glow_share = share
        self._glow_changed(self._now())
        self._sync()
        self.update()

    def glow_share(self) -> float:
        return getattr(self, "_glow_share", 1.0)

    def _glow_changed(self, now: float) -> None:
        """The glow's share changed (subclasses ease to it)."""

    def _update_name(self) -> None:
        self.setAccessibleName(f"Jarvis, {self.state_word().lower()}")
        self.setAccessibleDescription(f"Jarvis is {self._describe()}")

    def _describe(self) -> str:
        words = {IDLE: "standing by", LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking",
                 NEEDS: "waiting for your decision", WAITING: "waiting for the AM briefing"}
        text = words.get(self._base, "standing by")
        if self._muted:
            text += ", muted"
        elif self._offline:
            text += ", on the offline voice"
        return text

    # ---- time ---------------------------------------------------------------

    def _now(self) -> float:
        return self._frozen if self._frozen is not None else time.monotonic() - self._t_origin

    def set_phase(self, seconds: float | None) -> None:
        """Freeze the motion at ``seconds`` (deterministic screenshots); None resumes."""
        self._frozen = seconds
        if seconds is not None:
            self.settle()
        self._sync()
        self.update()

    def settle(self) -> None:
        """End every running transition at its target."""

    def set_animated(self, animated: bool) -> None:
        self._animated = animated
        self._sync()

    # ---- run conditions ------------------------------------------------------

    def frame_interval(self) -> int | None:
        """The interval this orb wants now (ms), or None when it should be still."""
        return frame_interval_ms(self._base, self._muted, frame_clock().deep())

    def _should_run(self) -> bool:
        window = self.window()
        return (self._animated and self._frozen is None and not _still() and self.isVisible()
                and not (window is not None and window.isMinimized()) and self.frame_interval() is not None)

    def _sync(self) -> None:
        clock = frame_clock()
        clock.want(self, self.frame_interval() if self._should_run() else None)

    def is_animating(self) -> bool:
        clock = frame_clock()
        return clock.wants(self) and clock.running() and not clock.locked()

    def _request_frame(self) -> None:
        self._last_frame = time.monotonic()
        self.update()

    def _on_motion_changed(self, _reduced: bool) -> None:
        if shiboken6.isValid(self):
            self.settle()
            self._sync()
            self.update()

    def _watch_window(self) -> None:
        # Minimizing does not hide child widgets: listen to the window's state changes.
        window = self.window()
        if window is not self and (self._watched is None or self._watched() is not window):
            window.installEventFilter(self)
            self._watched = weakref.ref(window)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.WindowStateChange and watched is self.window():
            QTimer.singleShot(0, self._sync_if_valid)
        return False

    def _sync_if_valid(self) -> None:
        if shiboken6.isValid(self):
            self._sync()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._watch_window()
        if not event.spontaneous():
            frame_clock().note_activity()    # an orb coming into view (a screen opened) is not idle
        self._sync()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._sync()

    def changeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        frame_clock().want(self, None)
        super().closeEvent(event)

# --------------------------------------------------------------------------
# The ring
# --------------------------------------------------------------------------


@dataclass
class OrbFrame:
    """Everything one frame paints (computed by ``OrbWidget.sample``)."""

    t: float
    look: str                                  # the look now (``LOOKS``)
    tilt: float                                # degrees: the band tipped about the horizontal axis (0 face-on)
    roll: float                                # degrees: then rolled (idle's sway included)
    phase: float                               # 0..0.5 of a step: voice listening centres segments on the axes
    segments: int
    bands: tuple[tuple[str, float, tuple[float, ...], tuple[int, ...]], ...]   # (look, weight, alphas, colours)
    band_gain: float                           # idle's breathing: the band's strength (1 +- 8 %)
    sway: float                                # degrees of roll from idle's sway now
    lit: int                                   # mic-lit segments per side (voice listening)
    level: float                               # the smoothed level: the mic while listening, the voice speaking
    voice: float                               # how much of the real voice drives the speaking reach
    head: float                                # the chase's head (thinking, waiting): degrees in the band's plane
    sight: float                               # the listening sight: crosshair, centre ring, notches (0..1)
    close: float                               # the brackets: 0 open (184) .. 1 locked (164)
    brackets: float                            # their alpha
    ink: QColor                                # the tracks' colour (steel while muted)
    tones: tuple[tuple[tuple[str, bool, bool], float], ...]   # the sight's cached tones (ink, dashed, listening)
    tracks: float                              # the tracks' strength (the start-up)
    core_r: float
    glow_r: float
    glow: float                                # the glow's alpha
    core_op: float
    core_scale: float
    core_looks: tuple[tuple[str, float], ...]  # (CORE_HOT / CORE_FLAT / CORE_STEEL, weight)
    alert: float                               # needs you: the amber arc and dot on the sight
    arc: float                                 # ... drawn in so far
    slash: float                               # muted: the strike
    echo: float                                # speaking: the echo ring (hero size)
    dashed: bool                               # the offline voice: the sight dashed
    compact: bool
    fine: bool                                 # the minor ticks, faint rings, outer track and glow (hero, not degraded)
    spikes: float = 0.0                        # the speaking reach's strength (it fades in and out)
    spike_lens: tuple[float, ...] = ()         # each segment's reach (units) at full strength
    sight_draw: float = 1.0                    # the start-up: the sight drawn round so far
    band_in: float = 1.0                       # the start-up: how much of the band has swept in
    sweep: tuple[float, ...] | None = None     # the start-up: each segment's share

    @property
    def alphas(self) -> tuple[float, ...]:
        """The current look's segment alphas (the band's pattern at rest)."""
        return self.bands[-1][2]

    @property
    def colors(self) -> tuple[int, ...]:
        return self.bands[-1][3]

    @property
    def reach(self) -> tuple[float, ...]:
        """Each segment's reach now (units): the spike lengths at the reach's strength."""
        return tuple(length * self.spikes for length in self.spike_lens)


class OrbWidget(_OrbBase):
    """The ring at 200-320 / 120 / 92 px; see the module docstring."""

    auto_degrade = True

    def __init__(self, px: int = HERO, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._px = px
        self._ground: Callable[[QPainter, QWidget], None] | None = None
        self._degraded = False
        self._paint_ms: deque[float] = deque(maxlen=60)
        self._backdrops: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._built = False
        self._layer_builds = 0
        self._level_target: float | None = None
        self._level_at = -1.0
        self._env = 0.0
        self._last_advance: float | None = None
        self._lag = 0.0                     # seconds the motion fell behind the clock in deep idle
        self._think = THINK_HEAD            # the chase heads (degrees in the band's plane)
        self._scan = WAIT_HEAD
        self._alert_since = -10.0
        self._still_lit = 0                 # motion off: the mic's step shown
        self._look = self.ring_look()
        pose = POSES[self._look]
        core_r, glow_r, glow = _CORE[self._look]
        self._tw: dict[str, _Tween] = {
            "tilt": _Tween(pose.tilt), "roll": _Tween(pose.roll), "phase": _Tween(0.0),
            "close": _Tween(pose.close), "brackets": _Tween(pose.brackets), "sight": _Tween(0.0),
            "core_r": _Tween(core_r), "glow_r": _Tween(glow_r), "glow": _Tween(glow), "core_op": _Tween(1.0),
            "life": _Tween(1.0), "alert": _Tween(0.0), "slash": _Tween(0.0), "echo": _Tween(0.0),
            "spikes": _Tween(0.0), "voice": _Tween(0.0), "pace": _Tween(DEEP_PACE if frame_clock().deep() else 1.0),
            "glow_share": _Tween(1.0),
        }
        self._band = _Fade(self._look)
        self._ink = _Fade(holo.HOLO)
        self._tone = _Fade((holo.HOLO, False))
        self._core_look = _Fade(CORE_HOT)
        self._voice_fn: Callable[[], float | None] | None = None
        self._room = 0                      # the square the stage may give the widget (the reach never needs it)
        self._dim = 1.0                     # quiet hours: the ring at 70 % (set_dim)
        self._intro: tuple[float, bool] | None = None   # the start-up: (orb time it began, calm)
        self._recording = False             # a meeting records: the coral ring (meetings_ui)
        self.set_orb_size(px)

    # ---- API --------------------------------------------------------------------

    def set_recording(self, recording: bool) -> None:
        """The recording look while a meeting records: a coral ring at r176 with a coral dot at 12 o'clock (still:
        it never blinks), over any look, and "recording" in the ring's accessible description."""
        recording = bool(recording)
        if recording != self._recording:
            self._recording = recording
            self._update_name()
            self.update()

    def is_recording(self) -> bool:
        return self._recording

    def _describe(self) -> str:
        text = super()._describe()
        return f"{text}, recording a meeting" if getattr(self, "_recording", False) else text

    def _paint_recording(self, painter: QPainter) -> None:
        center = self.orb_rect().center()
        k = self._px / 360.0
        painter.resetTransform()
        painter.setOpacity(1.0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(center)
        painter.scale(k, k)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(_rgba(QColor(holo.ALERT), 0.9), 1.5))
        painter.drawEllipse(QPointF(0, 0), REC_R, REC_R)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(holo.ALERT_HI))
        dot = max(REC_DOT, 2.2 / k)                  # at least a 2.2 px dot on the small rings
        painter.drawEllipse(QPointF(0, -REC_R), dot, dot)
        painter.resetTransform()

    def orb_size(self) -> int:
        return self._px

    def set_orb_size(self, px: int) -> None:
        self._px = int(px)
        side = self._side()
        self.setFixedSize(side, side)
        self._backdrops.clear()
        self._paint_ms.clear()
        self._degraded = False
        self.update()

    def orb_rect(self) -> QRectF:
        """The ring's own box (``px`` square) inside the widget."""
        side = self.width()
        off = (side - self._px) / 2
        return QRectF(off, off, self._px, self._px)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        side = self._side()
        return QSize(side, side)

    def _side(self) -> int:
        return widget_side(self._px)

    def set_voice_room(self, px: int) -> None:
        """The stage may give the widget up to ``px`` square for the voice's reach; the band reaches out inside its
        sight, so the widget keeps ``widget_side`` whatever it is given."""
        self._room = max(0, int(px))

    def spike_room(self) -> float:
        """The longest reach (units) that fits: the full ``SPIKE_MAX`` at every size (it ends inside the sight)."""
        return SPIKE_MAX

    def set_voice(self, level_fn: Callable[[], float | None] | None) -> None:
        """The voice's level now, read once a frame while speaking (None: the synthetic curve)."""
        self._voice_fn = level_fn
        if level_fn is None:
            self.set_level(None)

    def detail(self) -> str:
        """``"full"`` (hero: every layer), ``"reduced"`` (hero without the minor ticks, faint rings, outer track and
        glow, after slow paints) or ``"compact"`` (under 200 px)."""
        if self._compact():
            return "compact"
        return "reduced" if self._degraded else "full"

    def tier(self) -> str:
        return ring_tier(self._px)

    def segment_count(self) -> int:
        return SEGMENTS[ring_tier(self._px)]

    def is_degraded(self) -> bool:
        return self._degraded

    def paint_times(self) -> list[float]:
        return list(self._paint_ms)

    def ring_layer_builds(self) -> int:
        """How often a cached ground + sight layer was built (tests and measurements)."""
        return self._layer_builds

    def backdrop_count(self) -> int:
        """Cached ground + sight layers (at most ``SIGHT_LAYERS``)."""
        return len(self._backdrops)

    def set_ground(self, painter_fn: Callable[[QPainter, QWidget], None] | None) -> None:
        """``painter_fn(painter, widget)`` paints the ground under the ring; the ring then paints opaquely."""
        self._ground = painter_fn
        self._backdrops.clear()
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, painter_fn is not None)
        self.update()

    def snapshot(self) -> QPixmap:
        """The ring as it is now - its recording ring and quiet-hours dimming too - without its ground, in a
        transparent pixmap of the widget's size at its device pixel ratio: the switch flies it over the desktop
        (alive.WindowFlight), also while the widget is hidden. The widget's own ground, cached layers and paint
        times stay as they were (its motion moves on to now, as a frame would)."""
        dpr = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        ground, backdrops, builds = self._ground, self._backdrops, self._layer_builds
        self._ground, self._backdrops = None, OrderedDict()      # layers of its own: the widget's stay untouched
        try:
            frame = self.sample()
            painter = QPainter(pixmap)
            try:
                self._paint(painter, frame, dpr)
                if self._recording:
                    self._paint_recording(painter)
                if self._dim < 1.0:                     # its strength over nothing: the alpha, scaled
                    painter.resetTransform()
                    painter.setOpacity(1.0)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    painter.fillRect(self.rect(), QColor(0, 0, 0, round(255 * self._dim)))
            finally:
                painter.end()
        finally:
            self._ground, self._backdrops, self._layer_builds = ground, backdrops, builds
        return pixmap

    def set_dim(self, level: float) -> None:
        """Draw the whole ring at ``level`` (0..1) of its strength over the ground: quiet hours dim it to 0.7
        (HOLOGRAM_SPEC 16.7); 1 is the ring as it is."""
        level = max(0.0, min(1.0, float(level)))
        if level != self._dim:
            self._dim = level
            self.update()

    def dim(self) -> float:
        return self._dim

    def set_level(self, level: float | None) -> None:
        """The level 0..1 now (about 30 Hz): the mic's while listening by voice (it lights the band from 6
        o'clock), the voice's envelope while speaking; None: no level (speaking returns to the synthetic curve)."""
        if level is None:
            self._level_target = None
            self._level_at = -1.0
        else:
            self._level_target = max(0.0, min(1.0, float(level)))
            self._level_at = time.monotonic()
        if self._look == LISTENING and _still():             # motion off: repaint when the step changes
            lit = lit_per_side(self._level_target or 0.0, self.segment_count(), still=True)
            if lit != self._still_lit:
                self._still_lit = lit
                self.update()

    def level(self) -> float:
        """The smoothed level the band uses (synthetic while speaking without fresh voice data)."""
        return self._env

    def settle(self) -> None:
        self._intro = None
        for tween in self._tw.values():
            tween.settle()
        for fade in (self._band, self._ink, self._tone, self._core_look):
            fade.settle()
        self._alert_since = -10.0
        self._last_advance = None

    # ---- transitions -----------------------------------------------------------------

    def _compact(self) -> bool:
        return self._px < COMPACT_BELOW

    def _look_changed(self, now: float) -> None:
        old, new = self._look, self.ring_look()
        self._look = new
        tw = self._tw
        pose = POSES[new]
        m = _stretch()
        facing = new == LISTENING and old != LISTENING
        if facing or pose.tilt < tw["tilt"].value(now) - 0.01:
            dur, ease = FACE_S * m, _out_cubic          # coming more level: 420 ms
        else:
            dur, ease = RELEASE_S * m, _in_out_sine     # tipping back: 700 ms
        tw["tilt"].to(pose.tilt, now, dur, ease)
        tw["roll"].to(pose.roll, now, dur, ease)
        tw["phase"].to(0.5 if new == LISTENING else 0.0, now, dur, ease)
        delay = BRACKET_DELAY_S * m if facing else 0.0
        tw["close"].to(pose.close, now, BRACKET_S * m, _STD, delay)
        tw["brackets"].to(pose.brackets, now, BRACKET_S * m, _STD, delay)
        if facing:
            tw["sight"].to(1.0, now, SIGHT_S * m, _EASE, (FACE_S - SIGHT_S) * m)
        else:
            tw["sight"].to(1.0 if new == LISTENING else 0.0, now, SIGHT_S * m, _EASE)
        self._band.to(new, now, (BAND_FADE_FACING_S if facing else BAND_FADE_S) * m)
        core_r, glow_r, glow = _CORE[new]
        if self._offline:
            glow = 0.0                                  # the offline voice: a flat core, no glow
        tw["core_r"].to(core_r, now, 0.4 * m, _STD)
        tw["glow_r"].to(glow_r, now, 0.4 * m, _STD)
        tw["glow"].to(glow, now, 0.4 * m, _EASE)
        tw["core_op"].to(0.6 if new == WAITING else 1.0, now, 0.4 * m, _EASE)
        tw["life"].to(1.0 if new == IDLE else 0.0, now, dur, ease)     # breathing and sway go with the turn
        tw["slash"].to(1.0 if new == MUTED else 0.0, now, 0.6 * m, _EASE)
        tw["echo"].to(1.0 if new == SPEAKING else 0.0, now, 0.5 * m, _EASE)
        tw["spikes"].to(1.0 if new == SPEAKING and not _still() else 0.0, now, SPIKE_FADE_S, _EASE)
        if new == NEEDS and old != NEEDS:
            self._alert_since = now
            tw["alert"].to(1.0, now, 0.0)
            tw["alert"].settle()
        elif new != NEEDS and old == NEEDS:
            tw["alert"].to(0.0, now, 0.4, _EASE)
        ink = holo.STEEL if new == MUTED else holo.HOLO
        self._ink.to(ink, now, 0.6 * m)
        self._tone.to((ink, self._offline), now, 0.3 * m)
        self._core_look.to(CORE_STEEL if new == MUTED else CORE_FLAT if self._offline else CORE_HOT, now, 0.4 * m)
        if _still() or self._frozen is not None:
            self.settle()

    def _glow_changed(self, now: float) -> None:
        """The glow eases to its share with the lit frame's brackets (200 ms, x1.6 calm; at once with motion off)."""
        tween = self._tw["glow_share"]
        tween.to(self.glow_share(), now, GLOW_SHARE_S * _stretch(), _STD)
        if _still() or self._frozen is not None:
            tween.settle()

    def _deep_changed(self, deep: bool) -> None:
        now = self._now()
        pace = self._tw["pace"]
        pace.to(DEEP_PACE if deep else 1.0, now, DEEP_EASE_S if deep else WAKE_EASE_S, _STD)
        if _still() or self._frozen is not None:
            pace.settle()
        self._sync()
        self.update()

    def pace(self) -> float:
        """The motion's speed now: 1.0, or DEEP_PACE in deep idle (eased between them)."""
        return self._tw["pace"].value(self._now())

    def frame_interval(self) -> int | None:
        ms = frame_interval_ms(self._base, self._muted, frame_clock().deep())
        now = self._now()
        if (any(not tween.done(now) for tween in self._tw.values())
                or not all(fade.done(now) for fade in (self._band, self._ink, self._tone, self._core_look))
                or self.intro_running(now)):
            ms = min(ms, FRAME_ACTIVE_MS)      # transitions run smoothly, then the rate drops
        return ms

    # ---- the start-up ------------------------------------------------------------------

    @staticmethod
    def _intro_timing(calm: bool) -> tuple[float, float, float]:
        """(stagger, stage length, turn) of the start-up."""
        if calm:
            return INTRO_CALM_STAGGER_S, INTRO_CALM_LAYER_S, 0.0
        return INTRO_STAGGER_S, INTRO_LAYER_S, INTRO_TURN

    @classmethod
    def intro_seconds(cls, calm: bool = False) -> float:
        stagger, layer, _turn = cls._intro_timing(calm)
        return stagger * (INTRO_LAYERS - 1) + layer

    def play_intro(self, *, calm: bool = False, start: float | None = None) -> None:
        """The ring assembles (see the module docstring); nothing under reduced motion."""
        if holo.reduced_motion():
            return
        self._intro = (self._now() if start is None else float(start), bool(calm))
        self._sync()
        self.update()

    def intro_running(self, now: float | None = None) -> bool:
        if self._intro is None:
            return False
        now = self._now() if now is None else now
        start, calm = self._intro
        return now - start < self.intro_seconds(calm)

    def _apply_intro(self, f: OrbFrame, now: float) -> None:
        """Stage by stage: the core (0), its glow (1), the band sweeping in clockwise from 12 o'clock (2-5) as it
        rolls in, the tracks (6), the sight drawing round (7), the brackets (8)."""
        if self._intro is None:
            return
        start, calm = self._intro
        elapsed = now - start
        if holo.reduced_motion() or elapsed >= self.intro_seconds(calm):
            if holo.reduced_motion() or self._frozen is None:
                self._intro = None              # (a still keeps it: an earlier moment may still be asked for)
            return
        stagger, length, turn = self._intro_timing(calm)

        def progress(stage: float) -> float:
            return _OUT(max(0.0, min(1.0, (elapsed - stage * stagger) / length)))

        core = progress(0)
        f.core_op *= core
        f.core_scale *= 0.55 + 0.45 * core
        f.glow *= progress(1)
        n = f.segments
        f.sweep = tuple(progress(2.0 + 3.0 * i / n) for i in range(n))
        f.band_in = sum(f.sweep) / n
        f.roll += turn * (1.0 - progress(3.5))
        f.tracks *= progress(6)
        f.sight_draw = progress(7)
        f.brackets *= progress(8)
        f.alert *= progress(7)

    # ---- frame values ----------------------------------------------------------------------

    def _advance(self, now: float) -> None:
        last = self._last_advance
        self._last_advance = now
        dt = 0.0 if last is None else max(0.0, min(MAX_STEP_S, now - last))
        still = _still()
        calm = holo.motion_level() == holo.MOTION_CALM
        think = 360.0 / (THINK_TURN_CALM_S if calm else THINK_TURN_S)
        scan = 360.0 / (WAIT_TURN_S * (CALM_STRETCH if calm else 1.0))
        if still:
            self._think, self._scan = THINK_HEAD, WAIT_HEAD
        elif self._frozen is not None:              # a still: where a constant look's heads are at t
            self._think = (THINK_HEAD + think * now) % 360.0
            self._scan = (WAIT_HEAD + scan * now) % 360.0
        else:
            # Deep idle slows the motion (pace < 1): the heads turn slower and the breathing runs on a clock that
            # falls behind by ``_lag``, so nothing jumps when the pace changes.
            pace = self._tw["pace"].value(now)
            self._lag += (1.0 - pace) * dt
            self._think = (self._think + think * pace * dt) % 360.0
            self._scan = (self._scan + scan * pace * dt) % 360.0
        look = self._look
        if self._voice_fn is not None and self._base == SPEAKING:
            try:
                heard = self._voice_fn()
            except Exception:  # noqa: BLE001 - the voice only animates: the synthetic curve instead
                heard = None
            if heard is not None:
                self._level_target = max(0.0, min(1.0, float(heard)))
                self._level_at = time.monotonic()
        known = self._level_target
        fresh = known is not None and time.monotonic() - self._level_at <= LEVEL_FRESH_S
        voice = self._tw["voice"]
        voice.to(1.0 if fresh and look == SPEAKING else 0.0, now, 0.25, _EASE)
        target, attack, release = 0.0, 0.04, 0.12
        if look == SPEAKING:
            target = known if fresh and known is not None else synthetic_level(now)
        elif look == LISTENING:
            attack, release = MIC_ATTACK_S, MIC_RELEASE_S
            if known is not None and (fresh or still):   # motion off: the last level holds (no frames age it)
                target = known
        if self._frozen is not None or still:
            self._env = target
        elif dt > 0.0:
            tau = attack if target > self._env else release
            self._env += (target - self._env) * (1.0 - math.exp(-dt / tau))

    def sample(self, now: float | None = None) -> OrbFrame:
        """Advance to ``now`` (default: the ring's clock) and return the frame's values."""
        now = self._now() if now is None else now
        self._advance(now)
        tw = self._tw
        still = _still()
        t = 0.0 if still else (now if self._frozen is not None else now - self._lag)
        look = self._look
        tier = ring_tier(self._px)
        n = SEGMENTS[tier]
        tilt, roll, phase = tw["tilt"].value(now), tw["roll"].value(now), tw["phase"].value(now)
        life = tw["life"].value(now)
        pace = tw["pace"].value(now)
        gain = 1.0 + (breath_at(t) - 1.0) * life if not still else 1.0
        sway = 0.0
        if not still and tier != TIER_HUD and holo.motion_level() == holo.MOTION_LIVELY and life > 0.0:
            awake = max(0.0, min(1.0, (pace - DEEP_PACE) / (1.0 - DEEP_PACE)))   # deep idle: the breathing alone
            sway = sway_at(t) * life * awake
        level = self._env
        lit = lit_per_side(level, n, still=still) if look == LISTENING else 0
        spikes = tw["spikes"].value(now)
        lens: tuple[float, ...] = ()
        if spikes > 0.003 and not still:
            lens = spike_lengths(level, t, calm=holo.motion_level() == holo.MOTION_CALM, longest=SPIKE_MAX, count=n)
        else:
            spikes = 0.0
        reach = tuple(length * spikes for length in lens) if lens else None
        sight = tw["sight"].value(now)
        bands = []
        for band_look, weight in self._band.weights(now):
            head = self._think if band_look == THINKING else self._scan
            alphas, colors = band_pattern(band_look, n, phase=phase, lit=lit, head=head,
                                          reach=reach if band_look == SPEAKING else None, sight=sight)
            bands.append((band_look, weight, alphas, colors))
        ink = holo.mix(self._ink.prev, self._ink.cur, self._ink.mix(now))
        tones = []
        for (tone_ink, dashed), weight in self._tone.weights(now):
            if sight < 1.0:
                tones.append(((tone_ink, dashed, False), weight * (1.0 - sight)))
            if sight > 0.0:
                tones.append(((tone_ink, dashed, True), weight * sight))
        voice = tw["voice"].value(now)
        core_scale = 1.0 + 0.18 * level * voice if look == SPEAKING and not still else 1.0
        alert = tw["alert"].value(now)
        arc = 1.0 if still else _STD(max(0.0, min(1.0, now - self._alert_since)))
        frame = OrbFrame(
            t=now, look=look, tilt=tilt, roll=roll + sway, phase=phase, segments=n, bands=tuple(bands),
            band_gain=gain, sway=sway, lit=lit, level=level, voice=voice,
            head=self._think if look == THINKING else self._scan, sight=sight, close=tw["close"].value(now),
            brackets=tw["brackets"].value(now), ink=ink, tones=tuple(tones), tracks=1.0, core_r=tw["core_r"].value(now),
            glow_r=tw["glow_r"].value(now), glow=tw["glow"].value(now) * tw["glow_share"].value(now),
            core_op=tw["core_op"].value(now),
            core_scale=core_scale, core_looks=self._core_look.weights(now), alert=alert, arc=arc,
            slash=tw["slash"].value(now), echo=tw["echo"].value(now), dashed=self._offline, compact=self._compact(),
            fine=not (self._compact() or self._degraded), spikes=spikes, spike_lens=lens,
        )
        self._apply_intro(frame, now)
        return frame

    # ---- painting ------------------------------------------------------------------------------

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        started = time.perf_counter()
        self._built = False
        frame = self.sample()
        painter = QPainter(self)
        try:
            self._paint(painter, frame, self.devicePixelRatioF() or 1.0)
            if self._dim < 1.0:
                self._paint_dim(painter)
            if self._recording:
                self._paint_recording(painter)
        finally:
            painter.end()
        if self.is_animating():
            if not self._built:
                self._paint_ms.append((time.perf_counter() - started) * 1000.0)
            self._maybe_degrade()
            self._sync()          # the rate follows the look (transitions run at 30 fps, then drop)

    def _paint_dim(self, painter: QPainter) -> None:
        """The ring at ``dim`` of its strength: the ground (or the void) laid over it at the rest, so where no
        ring is drawn nothing changes."""
        painter.resetTransform()
        painter.setOpacity(1.0 - self._dim)
        if self._ground is not None:
            self._ground(painter, self)
        else:
            painter.fillRect(self.rect(), QColor(holo.VOID))
        painter.setOpacity(1.0)

    def _backdrop_key(self) -> tuple:
        if self._ground is None:
            return (None,)
        window = self.window()
        return (id(self._ground), id(window), window.width(), window.height(),
                self.mapTo(window, QPoint(0, 0)).toTuple())

    def _backdrop(self, tone: tuple[str, bool, bool] | None, dpr: float, fine: bool) -> QPixmap:
        """The ground slice and the fixed sight in ``tone`` (ink, dashed, listening; None: the ground alone)."""
        key = (self._backdrop_key(), tone, self._px, round(dpr, 3), fine, self.width())
        pixmap = self._backdrops.get(key)
        if pixmap is not None:
            self._backdrops.move_to_end(key)
            return pixmap
        self._built = True
        self._layer_builds += 1
        side = self.width()
        pixmap = QPixmap(round(side * dpr), round(side * dpr))
        pixmap.setDevicePixelRatio(dpr)
        # Over a ground the copy is opaque (the ground covers it): a plain copy to the screen.
        pixmap.fill(_FILL_OPAQUE if self._ground is not None else Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if self._ground is not None:
                self._ground(painter, self)
            if tone is not None:
                ink, dashed, listen = tone
                painter.translate(self.orb_rect().center())
                k = self._px / 360.0
                painter.scale(k, k)
                _paint_sight(painter, QColor(ink), hero=not self._compact(), fine=fine, listen=listen, dashed=dashed)
        finally:
            painter.end()
        self._backdrops[key] = pixmap
        while len(self._backdrops) > SIGHT_LAYERS:
            self._backdrops.popitem(last=False)
        return pixmap

    def _paint_backdrop(self, painter: QPainter, f: OrbFrame, dpr: float) -> None:
        """The cached ground + sight; tones cross-fade (an exact blend over the ground: each later layer at its share
        of the weight so far). While the start-up draws the sight, the ground alone (the sight is drawn after)."""
        layers = [(None, 1.0)] if f.sight_draw < 1.0 else [tone for tone in f.tones if tone[1] > 0.003]
        opaque = self._ground is not None
        total = 0.0
        for index, (tone, weight) in enumerate(layers):
            pixmap = self._backdrop(tone, dpr, f.fine)
            total += weight
            if opaque:
                painter.setOpacity(1.0 if index == 0 else min(1.0, weight / max(1e-6, total)))
            else:
                painter.setOpacity(min(1.0, weight))
            painter.drawPixmap(QPoint(0, 0), pixmap)
        if not layers and opaque:
            painter.drawPixmap(QPoint(0, 0), self._backdrop(None, dpr, f.fine))
        painter.setOpacity(1.0)

    def _maybe_degrade(self) -> None:
        if (self.auto_degrade and not self._degraded and self._px >= COMPACT_BELOW
                and len(self._paint_ms) == self._paint_ms.maxlen):
            average = sum(self._paint_ms) / len(self._paint_ms)
            if average > DEGRADE_MS:
                self._degraded = True
                self._backdrops.clear()
                logger.info("Orb: compact layers (paint %.1f ms)", average)

    def _paint(self, painter: QPainter, f: OrbFrame, dpr: float = 1.0) -> None:
        tier = ring_tier(self._px)
        n = f.segments
        center = self.orb_rect().center()
        k = self._px / 360.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._paint_backdrop(painter, f, dpr)
        painter.translate(center)
        painter.scale(k, k)
        face = painter.transform()
        if f.sight_draw < 1.0:
            tone_ink = f.tones[-1][0][0] if f.tones else holo.HOLO
            _paint_sight(painter, QColor(tone_ink), hero=tier == TIER_HERO, fine=f.fine, listen=f.sight >= 0.5,
                         dashed=f.dashed, draw=f.sight_draw)
        painter.rotate(f.roll)
        painter.scale(1.0, max(0.02, math.cos(math.radians(f.tilt))))
        flat = painter.transform()
        painter.rotate(f.phase * 360.0 / n)
        band = painter.transform()
        reach = f.reach if f.spike_lens else None
        bands = [(weight, alphas, colors, reach if look == SPEAKING else None) for look, weight, alphas, colors
                 in f.bands if weight > 0.003]
        # idle's breathing goes into the painter's opacity (smooth, never a step of the buckets): the buckets carry
        # the headroom it takes back
        boost = 1.0 + BREATH_DEPTH if f.band_gain != 1.0 else 1.0
        far, near = _band_buckets(bands, n, BAND_IN, BAND_OUT, phase=f.phase, tilt=f.tilt, sweep=f.sweep, boost=boost)
        opacity = max(0.0, min(1.0, f.band_gain / boost))
        painter.setPen(Qt.PenStyle.NoPen)
        if far:
            painter.setOpacity(opacity)
            _fill(painter, far)
            painter.setOpacity(1.0)
        # the tracks (the orbit's edges; dashed inside on the offline voice); the outer one at 200-320 and 120 px
        painter.setTransform(flat)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if f.tracks > 0.003:
            painter.setPen(_pen(_rgba(f.ink, 0.32 * f.tracks), 1.0, dash=(3.0, 5.0) if f.dashed else None))
            painter.drawEllipse(QPointF(0, 0), TRACK_IN, TRACK_IN)
            if tier == TIER_MID or (tier == TIER_HERO and not self._degraded):
                painter.setPen(_pen(_rgba(f.ink, 0.14 * f.tracks), 1.0))
                painter.drawEllipse(QPointF(0, 0), TRACK_OUT, TRACK_OUT)
        # the glow and the core
        painter.setTransform(face)
        self._paint_core(painter, f, dpr, glow=tier != TIER_HERO or not self._degraded)
        # the listening sight: crosshair and centre ring
        if f.sight > 0.003:
            hi = QColor(holo.HOLO_HI)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(_rgba(hi, 0.6 * f.sight), 1.0))
            painter.drawLines(_axis_lines(CROSS_OUT, CROSS_IN))
            painter.setPen(_pen(_rgba(hi, 0.9 * f.sight), 1.2))
            painter.drawEllipse(QPointF(0, 0), CENTRE_RING, CENTRE_RING)
        # the near half (all of the band below the split)
        painter.setTransform(band)
        painter.setPen(Qt.PenStyle.NoPen)
        if near:
            painter.setOpacity(opacity)
            _fill(painter, near)
            painter.setOpacity(1.0)
        if f.echo > 0.003 and tier == TIER_HERO:
            painter.setTransform(flat)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(_rgba(QColor(holo.HOLO), 0.16 * f.echo), 1.0))
            painter.drawEllipse(QPointF(0, 0), ECHO_R, ECHO_R)
        painter.setTransform(face)
        self._paint_marks(painter, f, tier)
        painter.resetTransform()
        painter.setOpacity(1.0)

    def _paint_core(self, painter: QPainter, f: OrbFrame, dpr: float, *, glow: bool) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        if glow and f.glow > 0.003 and f.glow_r > 0.5 and f.core_op > 0.003:
            pixmap = _glow_pixmap(self._px, dpr)
            r = f.glow_r * f.core_scale
            painter.setOpacity(min(1.0, f.glow * f.core_op))
            painter.drawPixmap(QRectF(-r, -r, 2 * r, 2 * r), pixmap, QRectF(pixmap.rect()))
        r = f.core_r * f.core_scale
        for style, weight in f.core_looks:
            opacity = f.core_op * weight
            if opacity <= 0.003 or r <= 0.0:
                continue
            painter.setOpacity(min(1.0, opacity))
            painter.setPen(Qt.PenStyle.NoPen)
            if style == CORE_STEEL:
                painter.setBrush(_rgba(QColor(holo.STEEL), 0.55))
                painter.drawEllipse(QPointF(0, 0), r, r)
            elif style == CORE_FLAT:
                lo = QColor(holo.HOLO_LO)
                painter.setBrush(_rgba(lo, 0.85))
                painter.drawEllipse(QPointF(0, 0), r, r)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(_pen(_rgba(lo, 0.5), 1.0))
                painter.drawEllipse(QPointF(0, 0), r + 8.0, r + 8.0)
            else:
                painter.setBrush(_core_brush())
                painter.drawEllipse(QPointF(0, 0), r, r)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setOpacity(1.0)

    def _paint_marks(self, painter: QPainter, f: OrbFrame, tier: str) -> None:
        """Face-on marks over the band: the needs arc and dot, the mute strike, the brackets."""
        if f.alert > 0.003:
            span = (NEEDS_ARC[1] - NEEDS_ARC[0]) * f.arc
            amber = QColor(holo.AMBER)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(_rgba(amber, f.alert), 2.5))
            if span > 0.2:
                rect = QRectF(-SIGHT_R, -SIGHT_R, 2 * SIGHT_R, 2 * SIGHT_R)
                painter.drawArc(rect, round(-NEEDS_ARC[0] * 16), round(-span * 16))
            end = math.radians(NEEDS_ARC[0] + span)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_rgba(QColor(holo.AMBER_HI), f.alert))
            dot = 5.0 if tier == TIER_HERO else 9.0
            painter.drawEllipse(QPointF(SIGHT_R * math.cos(end), SIGHT_R * math.sin(end)), dot, dot)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if f.slash > 0.003:
            a, b = QPointF(-STRIKE, STRIKE), QPointF(STRIKE, -STRIKE)
            painter.setPen(_pen(_rgba(QColor(holo.VOID), f.slash), 7.0))
            painter.drawLine(a, b)
            painter.setPen(_pen(_rgba(QColor(holo.STEEL), f.slash), 2.5))
            painter.drawLine(a, b)
        if f.brackets > 0.003:
            d = BRACKET_OPEN - (BRACKET_OPEN - BRACKET_LOCKED) * max(0.0, min(1.0, f.close))
            color = holo.mix(holo.HOLO, holo.HOLO_HI, max(0.0, min(1.0, (f.close - 0.9) / 0.1)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(_rgba(color, f.brackets), 1.2 if tier == TIER_HUD else 1.5,
                                cap=Qt.PenCapStyle.SquareCap))
            painter.drawPath(_bracket_path(round(d, 2), ARMS[tier]))

# --------------------------------------------------------------------------
# The 36 px ring
# --------------------------------------------------------------------------


def paint_mini_orb(painter: QPainter, center: QPointF, base: str, muted: bool = False, offline: bool = False, *,
                   t: float = 0.0, ring_angle: float = 0.0, ring_color: str | QColor = holo.DOCK,
                   scale: float = 1.0, voice: bool = False, pose: tuple[float, float, float] | None = None,
                   level: float = 0.0, recording: bool = False, glow_share: float = 1.0) -> None:
    """The 36 px ring (DESIGN-SPEC 4.4): 16 segments (r100-152) in the look's pose (``pose``: tilt, roll, phase
    during a turn), the core (x 1.8) and its glow (x 1.2), no sight; listening by voice is a centre ring with four
    ticks, the offline voice a dashed centre ring, waiting a faint orbit track with its 2-segment tick, needs you an
    amber badge dot with a keyline in ``ring_color`` (the ground's), muted a strike. ``ring_angle`` turns the
    thinking head from its rest; ``t`` pulses the core while listening or speaking; ``level`` lights the band from 6
    o'clock while listening; ``recording`` adds the coral ring; ``glow_share`` scales the glow (a lit frame halves
    it)."""
    look = ring_look(base, muted, offline, voice)
    rest = POSES[look]
    tilt, roll, phase = pose if pose is not None else (rest.tilt, rest.roll, 0.5 if look == LISTENING else 0.0)
    n = SEGMENTS[TIER_DOCK]
    lit = lit_per_side(level, n) if look == LISTENING else 0
    head = (THINK_HEAD + ring_angle) % 360.0 if look == THINKING else None
    alphas, colors = band_pattern(look, n, phase=phase, lit=lit, head=head, sight=1.0 if look == LISTENING else 0.0)
    far, near = _band_buckets(((1.0, alphas, colors, None),), n, DOCK_IN, DOCK_OUT, phase=phase, tilt=tilt)
    if look == SPEAKING:
        pulse = _frames(_SPEAK, _cycle(t, 1.05), _linear)[0]
    elif look == LISTENING:
        pulse = _frames(_LISTEN, _cycle(t, 1.1), _IN_OUT)[0]
    else:
        pulse = 1.0
    core_r, glow_r, glow = _CORE[look]
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(center)
        k = MINI / MINI_BOX * scale
        painter.scale(k, k)
        face = painter.transform()
        painter.rotate(roll)
        painter.scale(1.0, max(0.02, math.cos(math.radians(tilt))))
        flat = painter.transform()
        painter.rotate(phase * 360.0 / n)
        band = painter.transform()
        painter.setPen(Qt.PenStyle.NoPen)
        _fill(painter, far)
        if look == WAITING:                      # the orbit the scanner runs round
            painter.setTransform(flat)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(_rgba(QColor(holo.HOLO), 0.3), 1.0))
            painter.drawEllipse(QPointF(0, 0), DOCK_TRACK, DOCK_TRACK)
        painter.setTransform(face)
        painter.setPen(Qt.PenStyle.NoPen)
        glow *= max(0.0, min(1.0, float(glow_share)))
        if glow > 0.0 and not offline:
            r = glow_r * 1.2
            painter.setOpacity(glow)
            painter.setBrush(_glow_brush())
            painter.drawEllipse(QPointF(0, 0), r, r)
            painter.setOpacity(1.0)
        r = core_r * 1.8 * pulse
        if look == MUTED:
            painter.setBrush(_rgba(QColor(holo.STEEL), 0.55))
        elif offline:
            painter.setBrush(_rgba(QColor(holo.HOLO_LO), 0.85))
        else:
            painter.setBrush(_core_brush())
            painter.setOpacity(0.6 if look == WAITING else 1.0)
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.setOpacity(1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if look == LISTENING:                    # no sight at 36 px: a centre ring and four ticks
            hi = QColor(holo.HOLO_HI)
            painter.setPen(_pen(hi, 2.0))
            painter.drawLines(_axis_lines(SIGHT_R, BRACKET_OPEN))
            painter.setPen(_pen(hi, 1.6))
            painter.drawEllipse(QPointF(0, 0), 34.0, 34.0)
        painter.setTransform(band)
        painter.setPen(Qt.PenStyle.NoPen)
        _fill(painter, near)
        painter.setTransform(face)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if offline and look != MUTED:            # the offline voice: a dashed centre ring
            painter.setPen(_pen(_rgba(QColor(holo.HOLO_HI), 0.9), 1.4, dash=(2.6, 2.2)))
            painter.drawEllipse(QPointF(0, 0), 70.0, 70.0)
        if look == NEEDS:                        # an amber badge dot, keyed out of the ground
            painter.setPen(QPen(QColor(ring_color), 16.0))
            painter.setBrush(QColor(holo.AMBER))
            painter.drawEllipse(QPointF(126.0, 126.0), 46.0, 46.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if look == MUTED:
            a, b = QPointF(-STRIKE, STRIKE), QPointF(STRIKE, -STRIKE)
            painter.setPen(_pen(QColor(holo.VOID), 7.0))
            painter.drawLine(a, b)
            painter.setPen(_pen(QColor(holo.STEEL), 2.0))
            painter.drawLine(a, b)
        if recording:
            painter.setPen(_pen(_rgba(QColor(holo.ALERT), 0.9), 2.5))
            painter.drawEllipse(QPointF(0, 0), 172.0, 172.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(holo.ALERT_HI))
            painter.drawEllipse(QPointF(0, -172.0), 16.0, 16.0)
    finally:
        painter.restore()


class MiniOrb(_OrbBase):
    """The 36 px ring; still at rest, 15 fps only while listening, thinking or speaking."""

    def __init__(self, parent: QWidget | None = None, *, ring_color: str | QColor = holo.DOCK) -> None:
        super().__init__(parent)
        self._head = 0.0
        self._last_advance: float | None = None
        self._ring_color = ring_color
        self._level = 0.0
        self._recording = False
        self._look = self.ring_look()
        pose = POSES[self._look]
        self._pose = {"tilt": _Tween(pose.tilt), "roll": _Tween(pose.roll), "phase": _Tween(0.0)}
        self.setFixedSize(MINI, MINI)

    def frame_interval(self) -> int | None:
        return FRAME_MINI_MS if self._base in ACTIVE_STATES else None

    def settle(self) -> None:
        self._last_advance = None
        for tween in self._pose.values():
            tween.settle()

    def set_level(self, level: float | None) -> None:
        """The mic level 0..1 (lights the band from 6 o'clock while listening by voice); None: none."""
        level = 0.0 if level is None else max(0.0, min(1.0, float(level)))
        if level != self._level:
            self._level = level
            self.update()

    def set_recording(self, recording: bool) -> None:
        """The coral recording ring (every size has it)."""
        if bool(recording) != self._recording:
            self._recording = bool(recording)
            self.update()

    def is_recording(self) -> bool:
        return self._recording

    def _look_changed(self, now: float) -> None:
        """It turns to face only while it runs (an active look); at rest it takes its pose at once."""
        old, new = self._look, self.ring_look()
        self._look = new
        pose = POSES[new]
        facing = new == LISTENING and old != LISTENING
        m = _stretch()
        if facing or pose.tilt < self._pose["tilt"].value(now) - 0.01:
            dur, ease = FACE_S * m, _out_cubic
        else:
            dur, ease = RELEASE_S * m, _in_out_sine
        turning = self._base in ACTIVE_STATES and not _still() and self._frozen is None
        for name, value in (("tilt", pose.tilt), ("roll", pose.roll), ("phase", 0.5 if new == LISTENING else 0.0)):
            tween = self._pose[name]
            tween.to(value, now, dur, ease)
            if not turning:
                tween.settle()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        now = self._now()
        still = _still()
        speed = 360.0 / (THINK_TURN_CALM_S if holo.motion_level() == holo.MOTION_CALM else THINK_TURN_S)
        if self._base == THINKING and not still:
            if self._frozen is not None:
                self._head = (now * speed) % 360.0
            elif self._last_advance is not None:
                self._head = (self._head + speed * max(0.0, min(0.2, now - self._last_advance))) % 360.0
        elif still:
            self._head = 0.0
        self._last_advance = now
        pose = (self._pose["tilt"].value(now), self._pose["roll"].value(now), self._pose["phase"].value(now))
        painter = QPainter(self)
        try:
            paint_mini_orb(painter, QPointF(self.width() / 2, self.height() / 2), self._base, self._muted,
                           self._offline, t=0.25 if still else now, ring_angle=self._head,
                           ring_color=self._ring_color, voice=self._voice, pose=pose, level=self._level,
                           recording=self._recording, glow_share=self.glow_share())
        finally:
            painter.end()


def ground_painter(cache: holo.GroundCache) -> Callable[[QPainter, QWidget], None]:
    """A ``set_ground`` function painting the widget's slice of its window's cached ground."""

    def paint(painter: QPainter, widget: QWidget) -> None:
        window = widget.window()
        origin = widget.mapTo(window, widget.rect().topLeft())
        source = QRect(origin, widget.size())
        cache.paint_slice(painter, widget.rect(), source, window.size(), widget.devicePixelRatioF())

    return paint
