"""Jarvis HUD widget kit: theme tokens, fonts, painting helpers and widgets.

The look follows the user's "Jarvis HUD - sci-fi" artboard: a near-black
ground with a faint cyan grid, chamfered (cut-corner) panels, cyan accents,
amber for anything that needs the user's OK, and three typefaces: Chakra Petch
(wordmark), Sora (prose) and JetBrains Mono (labels, meta, numbers). Every
widget paints itself with QPainter; nothing here knows about briefings,
Notion or Google, so ``ui.py`` composes the views from these parts. The one
import from the package is the Qt-free clock formatter
(``text_prep.clock_parts`` / ``format_time``), so every screen writes times
the same way.

Public API
==========

Tokens and fonts
    Colour constants (hex strings): ``GROUND``, ``TEXT_BRIGHT``, ``TEXT_BODY``,
    ``TEXT_SOFT``, ``TEXT_MUTED``, ``TEXT_DIM``, ``TEXT_SUB``, ``TEXT_TIME``,
    ``ACCENT`` (+ ``_HOVER``/``_PRESSED``/``_INK``), ``AMBER`` (+ ``_HOVER``/
    ``_PRESSED``/``_INK``/``_TEXT``/``_SUB``/``_META``), ``GREEN``, ``RED``,
    ``RED_LINE``, ``PINK``, ``SLATE``, ``OFF``; QColors ``PANEL_GROUND`` and
    ``AMBER_GROUND``; ``rgba(color, alpha) -> QColor``.
    ``load_fonts(fonts_dir) -> dict[str, str]`` registers the bundled TTFs
    (call once after QApplication exists; missing files are logged and the
    font helpers fall back to Bahnschrift / Segoe UI / Cascadia Mono, Consolas).
    ``display_font(px, weight, spacing)``, ``body_font(...)``, ``mono_font(...)``
    return QFonts; ``spacing`` is CSS letter-spacing in em.
    ``apply_theme(app)``: Fusion, HUD palette, Sora 13 px and a stylesheet for
    tooltips, scroll bars, menus and transparent text browsers/scroll areas.
    ``make_label(text, font, color, wrap=, name=)`` plain-text QLabel;
    ``set_label_color(label, color)``; ``SpeechLabel`` (QLabel with a real
    line height and optional ``max_lines`` elision; ``text()`` returns the
    plain text that was set, ``shown_text()`` what is displayed).

Painting helpers
    Corner flags ``CUT_TL``, ``CUT_TR``, ``CUT_BR``, ``CUT_BL``, ``CUT_DIAGONAL``
    (top-left + bottom-right, the default), ``CUT_TOP``.
    ``chamfer_path(rect, cut, corners) -> QPainterPath``.
    ``paint_ground(painter, rect, glow_center=None, glow_radii=None)``: ground
    colour, 48 px grid and the soft radial cyan glow.
    ``glow_pixmap(path, color, blur, dpr=1.0, key=None) -> (QPixmap, QPointF)``:
    a cached, blurred copy of ``path`` (CSS box-shadow ``0 0 <blur>px``).
    ``paint_row_highlight(painter, rect, color=ACCENT, strength=1.0)``: the
    agenda-row look (3 px glowing bar on the left + left-to-right gradient),
    shared by ``SectionList`` and the transcript highlight.
    ``paint_dash_bar(painter, rect, fraction, color)``: segmented telemetry bar.

Widgets
    ``HudWindowFrame(QWidget)``: frameless, translucent, always-on-top window
        with chamfered outer corners, painted ground (``set_glow_anchor``), a
        ``header`` (HeaderBar; its close button calls ``close()``), ``body`` /
        ``body_layout`` for the content, optional edge resizing
        (``set_resizable``).
    ``HeaderBar``: logo, JARVIS wordmark, subtitle, ServiceChips
        (``set_service(name, status, tooltip)``), date + clock updated each
        minute (``set_clock(callable)``, ``clock_texts()``; "1:52 PM" with a
        small "PM", or "13:52" after ``set_hour24(True)``), drag-to-move,
        ``close_button`` (accessible name "Close") -> ``closeRequested``.
        Narrow bars drop the subtitle, then the date, then tighten the chips
        and the room around them (``compact_level()``), so the 560 px prompt
        still fits.
    ``ServiceChip``: statuses ``STATUS_OK`` / ``STATUS_WARN`` / ``STATUS_ERROR``
        / ``STATUS_OFF``.
    ``ChamferPanel(title, meta, variant=PANEL_CYAN|PANEL_AMBER, cut, corners)``:
        ``body_layout`` (QVBoxLayout below the title row), ``set_title``,
        ``set_meta``.
    ``MainPanel``: the transcript panel (top corners cut 10 px) with a header
        strip holding ``steps`` (StepStrip) and ``live`` (LiveMarker)
        (``strip_layout`` takes more widgets), and ``body_layout`` for the
        text browser.
    ``HudButton(text, variant, compact=False)``: QPushButton painted per
        variant ``PRIMARY``, ``SECONDARY``, ``DENY``, ``APPROVE``, ``LINK``;
        never default/autoDefault, Tab focus only, ring on keyboard focus,
        ``set_selected`` (secondary), ``set_variant``, ``set_link_color``.
    ``Orb(size=168, state=ORB_STANDBY)``: ``set_state``, ``state()``,
        ``set_phase(seconds or None)`` (freeze for screenshots),
        ``set_animated(bool)``, ``is_animating()``. ~30 fps only while visible
        and not minimized. States in ``ORB_STATES`` (colour, label).
    ``StateLabel``: blinking square + state text (``set_state(state, text=None)``).
    ``TelemetryBar(label, value, fraction, color)``: ``set_value`` (with an
        optional ``short`` form shown when the value does not fit), ``shown_value()``.
    ``SectionList`` (scroll area of ``SectionRow`` buttons): ``set_rows`` with
        ``SectionRowInfo``, ``set_row_state``, ``row(i)``, ``ensure_visible``,
        ``content_height()``, ``rowClicked(int)``. Row states ``ROW_PLAYED``,
        ``ROW_CURRENT``, ``ROW_UPCOMING``, ``ROW_IGNORED``.
    ``AgendaPanel(title)``: the TODAY / TOMORROW agenda (``AgendaRow`` from
        ``AgendaRowInfo``: a glowing 3 px bar, mono time ("12:30 PM" drawn as
        "12:30" over a small "PM"), title, meta; states
        ``AGENDA_UPCOMING`` cyan, ``AGENDA_NOW`` green, ``AGENDA_PAST`` dim) or
        one message line with an optional link (``set_message``,
        ``linkClicked``), then DEADLINES (``DeadlineRow`` from
        ``DeadlineRowInfo``; the due label is red / amber / grey for
        ``URGENCY_HIGH`` / ``URGENCY_MEDIUM`` / ``URGENCY_LOW``). It scrolls
        inside the panel.
    ``ChipButton(text)``: a small checkable chamfered toggle with a count and
        a caret (``set_meta``), e.g. SECTIONS in the transcript strip.
    ``Popover(title, meta)``: a frameless popup around ``panel`` (a
        ChamferPanel); ``open_below(anchor, width, height)``, ``closed``.
    ``StepStrip``: ``set_steps([(label, STEP_DONE|STEP_ACTIVE|STEP_TODO|STEP_DENIED)])``.
    ``LiveMarker``: blinking red dot + LIVE; ``set_live(bool)`` (keeps its space).
    ``ActionCard(action_id, kind, title, detail, actionable=True, *,
        approve_text="Approve", body="", title_lines=0, open_text="",
        copy_text="")``:
        ``set_status(CARD_*, message="", link="", link_text="")`` (``CARD_DONE``
        reads DONE in green; ``link_text`` names the result's link, "Open" by
        default), ``set_note(text)``, ``note()``, ``result_text()``, ``title()``,
        ``body()``, ``show_copied()``, signals ``approveClicked(str)``,
        ``denyClicked(str)``, ``openClicked(str)`` (the link),
        ``sourceClicked(str)`` / ``copyClicked(str)`` (the action id, from the
        tools row's Open / Copy links). Deny / Approve
        and the result (WORKING..., ADDED + Open, DENIED, ...) share one
        fixed-height slot, so a status change never changes the card's height
        and the cards below never move; the FAILED reason and the note sit
        under the buttons and may only make a card taller. The body preview
        (three lines) and the tools row sit above the slot, are built once and
        never change height. ``set_locked(bool,
        tooltip)`` dims Deny / Approve (a click on them emits
        ``lockedClicked(str)``); the tools row is never locked. ``COPIED_TEXT``
        / ``COPIED_MS`` are the Copy feedback; ``plain_tooltip(text)`` makes a
        tooltip that keeps line breaks and is never read as HTML (the card
        and speech-line tooltips use it). ``ActionList``:
        scrollable cards ``ActionList.CARD_GAP`` px apart with an empty-state
        line (``add_card``, ``card(id)``, ``cards()``, ``clear()``,
        ``set_empty_text``).
    ``ActivityLog``: ``add(tag, message, sub="", when=None)`` newest first,
        at most ``ActivityLog.MAX_ENTRIES``; tags ``TAG_RUN``, ``TAG_DONE``,
        ``TAG_WAIT``, ``TAG_STOP``; ``entries()``; ``set_hour24(bool)``.
    ``HudChip(text, color)``: small chamfered tag (e.g. "2 NEED YOUR OK").
    ``FlowLayout(h_spacing, v_spacing)``: wrapping row for buttons, so a
        narrow column gets two rows instead of a wider window.

Notes for integration:
* Every clickable thing is a real QAbstractButton with Tab focus (HudButton,
  SectionRow, the close button), so a Space shortcut filter that checks
  ``isinstance(watched, QAbstractButton)`` covers them all. Focus rings show
  for keyboard focus only (Tab, Backtab, shortcuts), like CSS :focus-visible.
* PRIMARY / APPROVE glows are QGraphicsDropShadowEffects: a child cannot paint
  outside its parent, so leave ~8 px between such a button and the edge of
  the widget that holds it, or that side of the glow is cut off.
* Timers (orb animation, blinking, clock) only run while their widget is shown.
* Widths: the regular reading controls (Pause, Skip section, Read everything,
  Open in Notion, Done) need ~570 px in one row (~505 compact); put them in a
  FlowLayout or give the center column that much room.
"""

from __future__ import annotations

import html
import logging
import math
import re
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QLineF,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontDatabase,
    QFontInfo,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QTextLayout,
    QTextOption,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsDropShadowEffect,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from .text_prep import clock_parts, format_time

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

GROUND = "#020509"
TEXT_BRIGHT = "#eaffff"
TEXT_BODY = "#d9f6ff"
TEXT_SOFT = "#a9d8e6"
TEXT_MUTED = "#7fb3c4"
TEXT_DIM = "#5f8a99"
TEXT_SUB = "#6f9aab"          # activity sub-lines
TEXT_TIME = "#7a8692"         # activity timestamps (artboard #6b7682, lifted for contrast)
BUTTON_TEXT = "#e6edf3"

ACCENT = "#22d3ee"
ACCENT_HOVER = "#67e8f9"
ACCENT_PRESSED = "#06b6d4"
ACCENT_INK = "#021016"        # text on accent fills

AMBER = "#fbbf24"
AMBER_HOVER = "#fcd34d"
AMBER_PRESSED = "#f59e0b"
AMBER_INK = "#1a1203"
AMBER_TEXT = "#fff7e6"
AMBER_SUB = "#b8a67a"
AMBER_META = "#a38a52"

GREEN = "#86efac"
RED = "#fca5a5"
RED_LINE = "#f87171"
PINK = "#f5d0fe"
SLATE = "#64748b"
OFF = "#475569"
STEP_DIM = "#3a4450"

GRID_STEP = 48
MIDDLE_DOT = chr(0xB7)


def rgba(color: str | QColor, alpha: float) -> QColor:
    """``color`` with alpha ``alpha`` (0..1)."""
    result = QColor(color)
    result.setAlphaF(max(0.0, min(1.0, alpha)))
    return result


PANEL_GROUND = rgba("#030a10", 0.94)
AMBER_GROUND = rgba("#0c0903", 0.94)

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

DISPLAY_FAMILY = "Chakra Petch"
BODY_FAMILY = "Sora"
MONO_FAMILY = "JetBrains Mono"
FONT_FILES = (
    "ChakraPetch-Medium.ttf",
    "ChakraPetch-SemiBold.ttf",
    "Sora[wght].ttf",
    "JetBrainsMono[wght].ttf",
)
_FALLBACKS: dict[str, tuple[str, ...]] = {
    DISPLAY_FAMILY: ("Bahnschrift", "Segoe UI"),
    BODY_FAMILY: ("Segoe UI",),
    MONO_FAMILY: ("Cascadia Mono", "Consolas"),
}
_loaded_font_dirs: set[str] = set()


def load_fonts(fonts_dir: Path) -> dict[str, str]:
    """Register the bundled TTFs; returns {wanted family: family that will be used}.

    Never raises: a missing or broken file is logged and the font helpers fall
    back to the next family in their list. Loading the same folder twice is a no-op.
    """
    folder = Path(fonts_dir)
    key = str(folder.resolve())
    if key not in _loaded_font_dirs:
        _loaded_font_dirs.add(key)
        for name in FONT_FILES:
            _add_font_file(folder / name)
    available = set(QFontDatabase.families())
    used = {}
    for family, fallbacks in _FALLBACKS.items():
        choice = next((name for name in (family, *fallbacks) if name in available), "")
        if choice != family:
            logger.warning("Font %s is not available; using %s", family, choice or "the default font")
        used[family] = choice or family
    return used


def _add_font_file(path: Path) -> None:
    if not path.is_file():
        logger.warning("Font file missing: %s", path)
        return
    try:
        font_id = QFontDatabase.addApplicationFont(str(path))
    except Exception as exc:  # noqa: BLE001 - a font must never stop the app
        logger.warning("Could not load font %s: %s", path.name, exc)
        return
    if font_id < 0:
        logger.warning("Could not load font %s", path.name)
    else:
        logger.debug("Loaded font %s: %s", path.name, QFontDatabase.applicationFontFamilies(font_id))


def _font(family: str, px: int, weight: int, spacing: float) -> QFont:
    font = QFont()
    font.setFamilies([family, *_FALLBACKS.get(family, ())])
    font.setPixelSize(px)
    font.setWeight(QFont.Weight(weight))
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px * spacing)
    return font


def display_font(px: int = 20, weight: int = 600, spacing: float = 0.0) -> QFont:
    """Chakra Petch (wordmark, display headings); ``spacing`` in em."""
    return _font(DISPLAY_FAMILY, px, weight, spacing)


def body_font(px: int = 14, weight: int = 400, spacing: float = 0.0) -> QFont:
    """Sora (speech line, body text, titles)."""
    return _font(BODY_FAMILY, px, weight, spacing)


def mono_font(px: int = 11, weight: int = 400, spacing: float = 0.0) -> QFont:
    """JetBrains Mono (labels, meta, numbers, activity)."""
    return _font(MONO_FAMILY, px, weight, spacing)


def _qss_color(color: str | QColor) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


_STYLESHEET = """
QToolTip {
    color: @TEXT_BODY; background-color: #06121a; border: 1px solid @TIP_BORDER;
    padding: 4px 6px; font-family: "JetBrains Mono", "Cascadia Mono", Consolas; font-size: 11px;
}
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 1px 2px 0px; }
QScrollBar::handle:vertical { background: @HANDLE; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: @HANDLE_HOVER; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0px 2px 1px 2px; }
QScrollBar::handle:horizontal { background: @HANDLE; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: @HANDLE_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu { color: @TEXT_BODY; background-color: #050d14; border: 1px solid @TIP_BORDER; padding: 4px; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background-color: @MENU_SELECTED; color: @TEXT_BRIGHT; }
QMenu::separator { height: 1px; background: @TIP_BORDER; margin: 4px 6px; }
QTextBrowser {
    background: transparent; border: none; color: @TEXT_BODY;
    selection-background-color: @SELECTION; selection-color: @TEXT_BRIGHT;
}
QScrollArea#hudScroll { background: transparent; border: none; }
"""


def stylesheet() -> str:
    """The application stylesheet (tooltips, scroll bars, menus, text browsers)."""
    tokens = {
        "TEXT_BODY": TEXT_BODY, "TEXT_BRIGHT": TEXT_BRIGHT,
        "TIP_BORDER": _qss_color(rgba(ACCENT, 0.35)), "HANDLE": _qss_color(rgba(ACCENT, 0.22)),
        "HANDLE_HOVER": _qss_color(rgba(ACCENT, 0.45)), "MENU_SELECTED": _qss_color(rgba(ACCENT, 0.16)),
        "SELECTION": _qss_color(rgba(ACCENT, 0.32)),
    }
    text = _STYLESHEET
    for name in sorted(tokens, key=len, reverse=True):
        text = text.replace("@" + name, tokens[name])
    return text


def hud_palette() -> QPalette:
    role = QPalette.ColorRole
    colors = {
        role.Window: GROUND, role.WindowText: TEXT_BODY, role.Base: "#030a10", role.AlternateBase: GROUND,
        role.ToolTipBase: "#06121a", role.ToolTipText: TEXT_BODY, role.PlaceholderText: TEXT_DIM,
        role.Text: TEXT_BODY, role.Button: "#06121a", role.ButtonText: TEXT_BODY, role.BrightText: TEXT_BRIGHT,
        role.Highlight: "#0f4b59", role.HighlightedText: TEXT_BRIGHT, role.Link: ACCENT,
        role.LinkVisited: ACCENT, role.Light: "#12303a", role.Midlight: "#0c222a", role.Mid: "#0a1a21",
        role.Dark: "#01030a", role.Shadow: "#000000",
    }
    palette = QPalette()
    for color_role, color in colors.items():
        palette.setColor(color_role, QColor(color))
    for color_role in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, color_role, QColor(TEXT_DIM))
    return palette


def apply_theme(app: QApplication) -> None:
    """Fusion style, the HUD palette, Sora 13 px and the HUD stylesheet."""
    app.setStyle("Fusion")
    app.setPalette(hud_palette())
    app.setFont(body_font(13))
    app.setStyleSheet(stylesheet())


def make_label(text: str = "", font: QFont | None = None, color: str | QColor = TEXT_BODY, *,
               wrap: bool = False, name: str = "") -> QLabel:
    """A plain-text label (page text is never interpreted as HTML)."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    if font is not None:
        label.setFont(font)
    set_label_color(label, color)
    return label


def set_label_color(label: QWidget, color: str | QColor) -> None:
    """Text colour through a widget stylesheet (wins over any app stylesheet rule)."""
    label.setStyleSheet(f"color: {_qss_color(color)}; background: transparent;")


def plain_tooltip(text: str) -> str:
    """``text`` as a tooltip that keeps its line breaks and is never read as HTML.

    QToolTip guesses the format (Qt.AutoText), so page text with "<img src=...>" given to
    setToolTip as it is would be rendered, and the image file fetched, on a mere hover.
    """
    return f'<p style="white-space:pre-wrap">{html.escape(text)}</p>' if text else ""


class SpeechLabel(QLabel):
    """Word-wrapped prose with a CSS-like line height (Sora Light 20 px by default).

    The text is escaped and shown as rich text only to get the line height;
    ``text()`` returns exactly what was passed to ``setText``. With
    ``max_lines`` the text is cut to that many lines at the current width,
    ending in an ellipsis (the full text becomes the tooltip).
    """

    def __init__(self, text: str = "", *, font: QFont | None = None, color: str | QColor = TEXT_BRIGHT,
                 line_height: float = 1.4, max_lines: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plain = ""
        self._line_height = line_height
        self._max_lines = max_lines
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setFont(font or body_font(20, 300))
        set_label_color(self, color)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - mirrors QLabel
        self._plain = text or ""
        self.setAccessibleName(self._plain)
        self._render()

    def text(self) -> str:  # noqa: D102 - mirrors QLabel
        return self._plain

    def shown_text(self) -> str:
        """The text as displayed (elided when it needs more than ``max_lines``)."""
        return self._elided(self._plain) if self._max_lines else self._plain

    def set_max_lines(self, max_lines: int) -> None:
        self._max_lines = max_lines
        self._render()

    def _line_percent(self) -> int:
        # CSS line-height is relative to the font size; Qt's percentage is
        # relative to the font's own line height (ascent + descent).
        natural = QFontMetricsF(self.font()).height()
        pixels = QFontInfo(self.font()).pixelSize()
        return round(100 * self._line_height * pixels / natural) if natural > 0 else 100

    def _render(self) -> None:
        shown = self.shown_text()
        self.setToolTip(plain_tooltip(self._plain) if shown != self._plain else "")
        body = html.escape(shown).replace("\n", "<br>")
        super().setText(f'<div style="line-height:{self._line_percent()}%;">{body}</div>')

    def _elided(self, text: str) -> str:
        # A little slack against rounding in QLabel's own wrap.
        return _elide_to_lines(text, self.font(), self.contentsRect().width() - 2, self._max_lines)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._max_lines and event.oldSize().width() != event.size().width():
            self._render()


def _elide_to_lines(text: str, font: QFont, width: int, max_lines: int) -> str:
    """``text`` cut to ``max_lines`` word-wrapped lines of ``width`` px, ending in an ellipsis."""
    if width <= 0 or not text or max_lines <= 0:
        return text
    flat = text.replace("\n", " ")
    layout = QTextLayout(flat, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WordWrap)
    layout.setTextOption(option)
    starts: list[int] = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        starts.append(line.textStart())
    layout.endLayout()
    if len(starts) <= max_lines:
        return text
    start = starts[max_lines - 1]
    tail = QFontMetricsF(font).elidedText(flat[start:], Qt.TextElideMode.ElideRight, width)
    return flat[:start] + tail


# --------------------------------------------------------------------------
# Painting helpers
# --------------------------------------------------------------------------

CUT_TL = 1
CUT_TR = 2
CUT_BR = 4
CUT_BL = 8
CUT_DIAGONAL = CUT_TL | CUT_BR
CUT_TOP = CUT_TL | CUT_TR


def chamfer_path(rect: QRectF, cut: float, corners: int = CUT_DIAGONAL) -> QPainterPath:
    """``rect`` with the chosen corners cut at 45 degrees by ``cut`` px (like the CSS clip-path)."""
    c = max(0.0, min(float(cut), rect.width() / 2, rect.height() / 2))
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    corner_points = (
        (CUT_TL, (left, top), ((left, top + c), (left + c, top))),
        (CUT_TR, (right, top), ((right - c, top), (right, top + c))),
        (CUT_BR, (right, bottom), ((right, bottom - c), (right - c, bottom))),
        (CUT_BL, (left, bottom), ((left + c, bottom), (left, bottom - c))),
    )
    points: list[QPointF] = []
    for flag, square, cut_points in corner_points:
        for x, y in (cut_points if corners & flag and c > 0 else (square,)):
            points.append(QPointF(x, y))
    path = QPainterPath()
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


def paint_ground(painter: QPainter, rect: QRectF, glow_center: QPointF | None = None,
                 glow_radii: tuple[float, float] | None = None) -> None:
    """The HUD ground: colour, a 48 px grid of faint lines and a soft radial cyan glow.

    Grid lines sit at multiples of 48 px from ``rect``'s top-left. Without
    ``glow_center`` the glow sits at 50 % / 42 % like the artboard; the default
    radii are 53 % of the width and height (760 x 480 on 1440 x 900).
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.fillRect(rect, QColor(GROUND))
    line = rgba(ACCENT, 0.03)
    x = rect.left()
    while x < rect.right():
        painter.fillRect(QRectF(x, rect.top(), 1, rect.height()), line)
        x += GRID_STEP
    y = rect.top()
    while y < rect.bottom():
        painter.fillRect(QRectF(rect.left(), y, rect.width(), 1), line)
        y += GRID_STEP
    center = glow_center or QPointF(rect.left() + rect.width() * 0.5, rect.top() + rect.height() * 0.42)
    rx, ry = glow_radii or (rect.width() * 0.528, rect.height() * 0.533)
    if rx > 0 and ry > 0:
        gradient = QRadialGradient(center, rx)
        gradient.setColorAt(0.0, rgba(ACCENT, 0.10))
        gradient.setColorAt(0.7, rgba(ACCENT, 0.0))
        gradient.setColorAt(1.0, rgba(ACCENT, 0.0))
        squash = QTransform()
        squash.translate(center.x(), center.y())
        squash.scale(1.0, ry / rx)
        squash.translate(-center.x(), -center.y())
        brush = QBrush(gradient)
        brush.setTransform(squash)
        painter.fillRect(rect, brush)
    painter.restore()


_GLOW_CACHE: OrderedDict[tuple, tuple[QPixmap, QPointF]] = OrderedDict()
_GLOW_CACHE_MAX = 128


def _path_key(path: QPainterPath) -> tuple:
    elements = (path.elementAt(i) for i in range(path.elementCount()))
    return tuple((round(e.x, 2), round(e.y, 2), int(e.type.value)) for e in elements)


def _blur(image: QImage, radius: float) -> QImage:
    """Gaussian-like blur through QGraphicsBlurEffect (only used for cached glows)."""
    # Everything is owned by the scene (the effect via its QObject parent), so
    # deleting the scene frees it all once, whatever order Python drops the names in.
    scene = QGraphicsScene()
    effect = QGraphicsBlurEffect(scene)
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item = scene.addPixmap(QPixmap.fromImage(image))
    item.setGraphicsEffect(effect)
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        scene.render(painter, QRectF(out.rect()), QRectF(0, 0, image.width(), image.height()))
    finally:
        painter.end()
    return out


def glow_pixmap(path: QPainterPath, color: str | QColor, blur: float, *, dpr: float = 1.0,
                key: Any = None) -> tuple[QPixmap, QPointF]:
    """A blurred, filled copy of ``path`` and the top-left point to draw it at.

    Mimics CSS ``box-shadow: 0 0 <blur>px <color>`` / ``text-shadow``. Results
    are cached (LRU) by ``key`` (default: the path's geometry).
    """
    color = QColor(color)
    cache_key = (key if key is not None else _path_key(path), color.rgba(), round(blur, 2), round(dpr, 3))
    cached = _GLOW_CACHE.get(cache_key)
    if cached is not None:
        _GLOW_CACHE.move_to_end(cache_key)
        return cached
    pad = math.ceil(blur * 1.6) + 2
    bounds = path.boundingRect()
    origin = QPointF(math.floor(bounds.left()) - pad, math.floor(bounds.top()) - pad)
    width = math.ceil(bounds.right()) + pad - origin.x()
    height = math.ceil(bounds.bottom()) + pad - origin.y()
    image = QImage(max(1, round(width * dpr)), max(1, round(height * dpr)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(dpr, dpr)
        painter.translate(-origin)
        painter.fillPath(path, color)
    finally:
        painter.end()
    pixmap = QPixmap.fromImage(_blur(image, blur * dpr) if blur > 0 else image)
    pixmap.setDevicePixelRatio(dpr)
    _GLOW_CACHE[cache_key] = (pixmap, origin)
    while len(_GLOW_CACHE) > _GLOW_CACHE_MAX:
        _GLOW_CACHE.popitem(last=False)
    return pixmap, origin


def _draw_glow(painter: QPainter, path: QPainterPath, color: str | QColor, blur: float, *,
               dpr: float, key: Any = None) -> None:
    pixmap, origin = glow_pixmap(path, color, blur, dpr=dpr, key=key)
    painter.drawPixmap(origin, pixmap)


def paint_row_highlight(painter: QPainter, rect: QRectF, color: str | QColor = ACCENT, *,
                        strength: float = 1.0, bar_width: float = 3.0, glow: bool = True,
                        dpr: float = 1.0) -> None:
    """The artboard's agenda row: a glowing ``bar_width`` px bar on the left and a gradient.

    ``strength`` scales the gradient (1.0 = the current item, ~0.4 = a quiet row).
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gradient = QLinearGradient(rect.topLeft(), rect.topRight())
    gradient.setColorAt(0.0, rgba(color, 0.12 * strength))
    gradient.setColorAt(1.0, rgba(color, 0.0))
    painter.fillRect(rect, gradient)
    bar = QRectF(rect.left(), rect.top(), bar_width, rect.height())
    if glow:
        path = QPainterPath()
        path.addRect(QRectF(0, 0, bar.width(), bar.height()))
        painter.translate(bar.topLeft())
        _draw_glow(painter, path, rgba(color, 0.85), 10, dpr=dpr,
                   key=("bar", round(bar.width(), 1), round(bar.height(), 1)))
        painter.translate(-bar.topLeft())
    painter.fillRect(bar, QColor(color))
    painter.restore()


def paint_dash_bar(painter: QPainter, rect: QRectF, fraction: float, color: str | QColor) -> None:
    """Segmented bar: 6 px dashes, 2 px gaps; a faint track and ``fraction`` filled in ``color``."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    track = rgba(ACCENT, 0.12)
    fill_until = rect.left() + rect.width() * max(0.0, min(1.0, fraction))
    fill = QColor(color)
    x = rect.left()
    while x < rect.right():
        dash_end = min(x + 6, rect.right())
        painter.fillRect(QRectF(x, rect.top(), dash_end - x, rect.height()), track)
        if x < fill_until:
            painter.fillRect(QRectF(x, rect.top(), min(dash_end, fill_until) - x, rect.height()), fill)
        x += 8
    painter.restore()


def _text_advance(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


# Times with the widest text on each clock: two-digit hours, both halves of the day.
_WIDEST_TIMES = (datetime(2000, 1, 1, 0, 0), datetime(2000, 1, 1, 12, 59), datetime(2000, 1, 1, 23, 59))


def _clock_width(font: QFont, hour24: bool, part: int | None = None) -> float:
    """The advance of the widest time on that clock: the whole text ("12:59 PM" / "23:59"),
    or one part of it (0: the digits, 1: "AM" / "PM", "" on the 24-hour clock)."""
    texts = (format_time(moment, hour24=hour24) if part is None else clock_parts(moment, hour24=hour24)[part]
             for moment in _WIDEST_TIMES)
    return max(_text_advance(font, text) for text in texts)


def _line_height(font: QFont) -> float:
    return QFontMetricsF(font).height()


_KEYBOARD_FOCUS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)


def _focus_ring_after(reason: Qt.FocusReason, previous: bool) -> bool:
    """CSS :focus-visible: a ring for keyboard focus, none for mouse or programmatic focus.

    Re-activating the window keeps whatever the focus looked like before.
    """
    if reason in _KEYBOARD_FOCUS:
        return True
    if reason == Qt.FocusReason.ActiveWindowFocusReason:
        return previous
    return False


# --------------------------------------------------------------------------
# Shared blink clock (state squares, LIVE dot)
# --------------------------------------------------------------------------

class _BlinkClock(QObject):
    """One 250 ms timer for every blinking widget, running only while one is shown.

    The phase comes from the monotonic clock, so all blinkers stay in step.
    """

    def __init__(self) -> None:
        super().__init__()
        # Weak: a widget deleted while shown (no hide event) must not be kept alive here.
        self._widgets: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._last_on: bool | None = None

    @staticmethod
    def is_on() -> bool:
        return (time.monotonic() % 1.0) < 0.5

    def register(self, widget: QWidget) -> None:
        self._widgets.add(widget)
        try:
            if not self._timer.isActive():
                self._timer.start()
        except RuntimeError:   # the timer is already gone while Qt shuts down
            pass

    def unregister(self, widget: QWidget) -> None:
        self._widgets.discard(widget)
        try:
            if not self._widgets:
                self._timer.stop()
        except RuntimeError:   # widgets are hidden during shutdown, after the timer died
            pass

    def active(self) -> bool:
        try:
            return self._timer.isActive()
        except RuntimeError:
            return False

    def _tick(self) -> None:
        on = self.is_on()
        if on == self._last_on:
            return
        self._last_on = on
        for widget in list(self._widgets):
            if shiboken6.isValid(widget):
                widget.update()
            else:
                self.unregister(widget)


_blink_clock_instance: _BlinkClock | None = None


def _blink_clock() -> _BlinkClock:
    global _blink_clock_instance
    if _blink_clock_instance is None:
        _blink_clock_instance = _BlinkClock()
    return _blink_clock_instance


class _Blinking(QWidget):
    """Base for widgets with a 1 s on/off blink (CSS ``steps(1)``: 100 % / 15 % opacity)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._blinking = True
        self._frozen_on: bool | None = None

    def set_blinking(self, blinking: bool) -> None:
        self._blinking = blinking
        self._sync_blink()
        self.update()

    def freeze_blink(self, on: bool | None) -> None:
        """Pin the blink phase (screenshots); None resumes."""
        self._frozen_on = on
        self._sync_blink()
        self.update()

    def blink_opacity(self) -> float:
        if not self._blinking:
            return 1.0
        on = self._frozen_on if self._frozen_on is not None else _BlinkClock.is_on()
        return 1.0 if on else 0.15

    def _sync_blink(self) -> None:
        clock = _blink_clock()
        if self._blinking and self._frozen_on is None and self.isVisible():
            clock.register(self)
        else:
            clock.unregister(self)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._sync_blink()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        _blink_clock().unregister(self)


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------

PRIMARY = "primary"
SECONDARY = "secondary"
DENY = "deny"
APPROVE = "approve"
LINK = "link"
BUTTON_VARIANTS = (PRIMARY, SECONDARY, DENY, APPROVE, LINK)


@dataclass(frozen=True)
class _ButtonSpec:
    font: Callable[[], QFont]
    height: int
    padding: int
    cut: int


_BUTTON_SPECS: dict[tuple[str, bool], _ButtonSpec] = {
    (PRIMARY, False): _ButtonSpec(lambda: mono_font(12, 700, 0.14), 40, 18, 8),
    (PRIMARY, True): _ButtonSpec(lambda: mono_font(11, 700, 0.14), 36, 14, 6),
    (SECONDARY, False): _ButtonSpec(lambda: body_font(13, 400, 0.02), 40, 16, 8),
    (SECONDARY, True): _ButtonSpec(lambda: body_font(12, 400, 0.02), 36, 12, 6),
    (DENY, False): _ButtonSpec(lambda: mono_font(12, 400, 0.12), 40, 16, 8),
    (DENY, True): _ButtonSpec(lambda: mono_font(11, 400, 0.12), 36, 12, 6),
    (APPROVE, False): _ButtonSpec(lambda: mono_font(12, 700, 0.14), 40, 18, 8),
    (APPROVE, True): _ButtonSpec(lambda: mono_font(11, 700, 0.14), 36, 14, 6),
    (LINK, False): _ButtonSpec(lambda: mono_font(11, 400, 0.12), 24, 2, 0),
    (LINK, True): _ButtonSpec(lambda: mono_font(10, 400, 0.12), 22, 2, 0),
}
_GLOWS = {PRIMARY: (ACCENT, 0.55, 22.0), APPROVE: (AMBER, 0.5, 16.0)}


class HudButton(QPushButton):
    """A chamfered HUD button; looks are chosen by ``variant`` (see BUTTON_VARIANTS).

    Never a default button, focus only via Tab, pointing-hand cursor. The glow
    of PRIMARY/APPROVE is a drop-shadow effect, so it may extend outside the
    button; the keyboard focus ring is drawn inside the button's rect. The HUD
    font is kept in ``self._font`` and used for painting and sizing, so an app
    stylesheet cannot change it.
    """

    def __init__(self, text: str = "", variant: str = SECONDARY, *, compact: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._variant = variant
        self._compact = compact
        self._selected = False
        self._focus_visible = False
        self._link_color = QColor(TEXT_MUTED)
        self._spec = _BUTTON_SPECS[(variant, compact)]
        self._font = self._spec.font()
        self._apply_variant()

    # ---- configuration --------------------------------------------------------

    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        if variant != self._variant:
            self._variant = variant
            self._apply_variant()

    def set_selected(self, selected: bool) -> None:
        """Secondary buttons only: accent border and brighter text."""
        if selected != self._selected:
            self._selected = selected
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def set_link_color(self, color: str | QColor) -> None:
        self._link_color = QColor(color)
        self.update()

    def _apply_variant(self) -> None:
        self._spec = _BUTTON_SPECS[(self._variant, self._compact)]
        self._font = self._spec.font()
        self.setFont(self._font)
        glow = _GLOWS.get(self._variant)
        if glow is None:
            self.setGraphicsEffect(None)
        else:
            color, alpha, blur = glow
            effect = QGraphicsDropShadowEffect(self)
            effect.setOffset(0, 0)
            effect.setBlurRadius(blur)
            effect.setColor(rgba(color, alpha))
            effect.setEnabled(self.isEnabled())
            self.setGraphicsEffect(effect)
        self.updateGeometry()
        self.update()

    # ---- size -----------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        text = self.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        width = math.ceil(_text_advance(self._font, text)) + 2 * self._spec.padding
        return QSize(max(width, self._spec.height), self._spec.height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    # ---- state tracking -----------------------------------------------------------

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._focus_visible = _focus_ring_after(event.reason(), self._focus_visible)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            effect = self.graphicsEffect()
            if effect is not None:
                effect.setEnabled(self.isEnabled())

    # ---- painting ---------------------------------------------------------------

    def _colors(self) -> tuple[QColor | None, QColor | None, QColor]:
        """(fill, border, text) for the current variant and state."""
        enabled, down = self.isEnabled(), self.isDown()
        hover = enabled and self.underMouse() and not down
        variant = self._variant
        if variant == PRIMARY:
            if not enabled:
                return rgba(ACCENT, 0.16), None, rgba(TEXT_BRIGHT, 0.38)
            fill = ACCENT_PRESSED if down else ACCENT_HOVER if hover else ACCENT
            return QColor(fill), None, QColor(ACCENT_INK)
        if variant == APPROVE:
            if not enabled:
                return rgba(AMBER, 0.16), None, rgba(AMBER_TEXT, 0.38)
            fill = AMBER_PRESSED if down else AMBER_HOVER if hover else AMBER
            return QColor(fill), None, QColor(AMBER_INK)
        if variant == DENY:
            if not enabled:
                return None, rgba(RED_LINE, 0.22), rgba(RED, 0.4)
            fill = rgba(RED_LINE, 0.18 if down else 0.10) if (down or hover) else None
            return fill, rgba(RED_LINE, 0.8 if hover or down else 0.55), QColor(RED)
        if variant == LINK:
            if not enabled:
                return None, None, QColor(TEXT_DIM)
            return None, None, QColor(TEXT_BRIGHT) if hover or down else QColor(self._link_color)
        if not enabled:   # SECONDARY
            return None, rgba("#ffffff", 0.06), QColor(TEXT_DIM)
        fill = rgba("#ffffff", 0.10 if down else 0.06) if (down or hover or self._selected) else None
        if self._selected:
            border = QColor(ACCENT)
        else:
            border = rgba(ACCENT, 0.5) if hover or down else rgba("#ffffff", 0.12)
        text = BUTTON_TEXT if hover or down or self._selected else TEXT_SOFT
        return fill, border, QColor(text)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            fill, border, text_color = self._colors()
            cut = self._spec.cut
            if fill is not None:
                painter.fillPath(chamfer_path(rect, cut), fill)
            if border is not None:
                painter.setPen(QPen(border, 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(chamfer_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), cut))
            font = QFont(self._font)
            focused = self.hasFocus() and self._focus_visible
            if self._variant == LINK and ((self.isEnabled() and self.underMouse()) or focused):
                font.setUnderline(True)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextShowMnemonic, self.text())
            if focused:
                self._paint_focus(painter, rect)
        finally:
            painter.end()

    def _paint_focus(self, painter: QPainter, rect: QRectF) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._variant in (PRIMARY, APPROVE) and self.isEnabled():
            ink = ACCENT_INK if self._variant == PRIMARY else AMBER_INK
            painter.setPen(QPen(QColor(ink), 2))
            painter.drawPath(chamfer_path(rect.adjusted(3, 3, -3, -3), max(0, self._spec.cut - 2)))
        elif self._variant != LINK:
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawPath(chamfer_path(rect.adjusted(1, 1, -1, -1), self._spec.cut))


class _CloseButton(QPushButton):
    """The header's close button: an X drawn as two strokes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAccessibleName("Close")
        self.setToolTip("Close")
        self.setFixedSize(30, 30)
        self._focus_visible = False

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._focus_visible = _focus_ring_after(event.reason(), self._focus_visible)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            active = self.underMouse() or self.isDown()
            if active:
                painter.fillPath(chamfer_path(rect, 6), rgba(RED_LINE, 0.18 if self.isDown() else 0.12))
            pen = QPen(QColor(RED if active else TEXT_MUTED), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            c, r = rect.center(), 5.0
            painter.drawLine(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
            painter.drawLine(QPointF(c.x() - r, c.y() + r), QPointF(c.x() + r, c.y() - r))
            if self.hasFocus() and self._focus_visible:
                painter.setPen(QPen(QColor(ACCENT), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(chamfer_path(rect.adjusted(1, 1, -1, -1), 6))
        finally:
            painter.end()


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto further rows when narrow (Qt's flow layout).

    Meant for button rows that must never force their window wider; it reports
    heightForWidth, so the rows below move down when it wraps.
    """

    def __init__(self, parent: QWidget | None = None, h_spacing: int = 6, v_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _visible(self) -> list[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        items = self._visible()
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in items) + self._h_spacing * max(0, len(items) - 1)
        height = max((item.sizeHint().height() for item in items), default=0)
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.x() + area.width():
                x, y, row_height = area.x(), y + row_height + self._v_spacing, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h_spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


# --------------------------------------------------------------------------
# Small painted pieces
# --------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_OFF = "off"
STATUS_COLORS = {STATUS_OK: GREEN, STATUS_WARN: AMBER, STATUS_ERROR: RED, STATUS_OFF: OFF}
_STATUS_WORDS = {STATUS_OK: "ok", STATUS_WARN: "attention", STATUS_ERROR: "error", STATUS_OFF: "off"}


class ServiceChip(QWidget):
    """Header chip: 2 px left border and dot in the status colour, then the service name."""

    def __init__(self, name: str, status: str = STATUS_OFF, tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._status = status
        self._tight = False
        self._font = mono_font(11, 400, 0.04)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_status(status, tooltip)

    def set_tight(self, tight: bool) -> None:
        """Narrower padding (used by a crowded header)."""
        if tight != self._tight:
            self._tight = tight
            self.updateGeometry()
            self.update()

    def _padding(self, tight: bool) -> tuple[int, int]:
        return (7, 5) if tight else (10, 6)   # (outer padding, dot-to-text gap)

    def width_for(self, tight: bool) -> int:
        pad, gap = self._padding(tight)
        return math.ceil(2 + pad + 6 + gap + _text_advance(self._font, self._name) + pad)

    def name(self) -> str:
        return self._name

    def status(self) -> str:
        return self._status

    def set_status(self, status: str, tooltip: str = "") -> None:
        self._status = status if status in STATUS_COLORS else STATUS_OFF
        self.setToolTip(tooltip)
        self.setAccessibleName(f"{self._name}: {_STATUS_WORDS[self._status]}")
        self.setAccessibleDescription(tooltip)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(self._tight), math.ceil(_line_height(self._font)) + 10)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(True), self.sizeHint().height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        pad, gap = self._padding(self._tight)
        painter = QPainter(self)
        try:
            rect = QRectF(self.rect())
            color = QColor(STATUS_COLORS[self._status])
            painter.fillRect(rect, rgba(ACCENT, 0.05))
            painter.fillRect(QRectF(0, 0, 2, rect.height()), color)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(2 + pad + 3, rect.height() / 2), 3, 3)
            painter.setFont(self._font)
            painter.setPen(QColor(TEXT_SOFT))
            painter.drawText(rect.adjusted(2 + pad + 6 + gap, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._name)
        finally:
            painter.end()


class HudChip(QWidget):
    """A small chamfered tag in mono caps, e.g. "2 NEED YOUR OK" (amber by default)."""

    def __init__(self, text: str = "", color: str | QColor = AMBER, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._color = QColor(color)
        self._font = mono_font(10, 500, 0.14)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def set_color(self, color: str | QColor) -> None:
        self._color = QColor(color)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = _text_advance(self._font, self._text.upper()) + 20
        return QSize(math.ceil(width), math.ceil(_line_height(self._font)) + 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            painter.fillPath(chamfer_path(rect, 5), rgba(self._color, 0.10))
            painter.setPen(QPen(rgba(self._color, 0.5), 1))
            painter.drawPath(chamfer_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5))
            painter.setFont(self._font)
            painter.setPen(self._color)
            painter.drawText(rect.adjusted(1, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, self._text.upper())
        finally:
            painter.end()


class LiveMarker(_Blinking):
    """Red bordered "LIVE" tag with a blinking dot; hidden (space kept) when not live."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = mono_font(11, 400, 0.2)
        policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.setAccessibleName("Live")

    def set_live(self, live: bool) -> None:
        self.setVisible(live)

    def is_live(self) -> bool:
        return not self.isHidden()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = 10 + 7 + 8 + _text_advance(self._font, "LIVE") + 10
        return QSize(math.ceil(width), math.ceil(_line_height(self._font)) + 8 + 2)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            painter.setPen(QPen(rgba(RED_LINE, 0.6), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rgba(RED, self.blink_opacity()))
            painter.drawEllipse(QPointF(10 + 3.5, rect.height() / 2), 3.5, 3.5)
            painter.setFont(self._font)
            painter.setPen(QColor(RED))
            painter.drawText(rect.adjusted(10 + 7 + 8, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "LIVE")
        finally:
            painter.end()


# --------------------------------------------------------------------------
# Orb and state label
# --------------------------------------------------------------------------

ORB_WORKING = "working"
ORB_WAITING = "waiting"
ORB_STANDBY = "standby"
ORB_SPEAKING = "speaking"
ORB_LISTENING = "listening"
ORB_STATES: dict[str, tuple[str, str]] = {
    ORB_WORKING: (ACCENT, "WORKING"),
    ORB_WAITING: (AMBER, "WAITING ON YOU"),
    ORB_STANDBY: (SLATE, "STANDBY"),
    ORB_SPEAKING: (PINK, "SPEAKING"),
    ORB_LISTENING: (GREEN, "LISTENING"),
}
_ORB_BASE = 168.0
_TICKS = tuple(
    QLineF(77 * math.sin(math.radians(a)), -77 * math.cos(math.radians(a)),
           84 * math.sin(math.radians(a)), -84 * math.cos(math.radians(a)))
    for a in (k * 10 + 0.3 for k in range(36))
)
_ORIGIN = QPointF(0, 0)
_ARC_RECT = QRectF(-72, -72, 144, 144)
_CROSSHAIR = (QLineF(-62, 0, 62, 0), QLineF(0, -62, 0, 62))


_SWEEP_ALPHA = 0x55 / 255      # the artboard's stateGlow = state colour + "55"


def _ease_in_out(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def orb_color(state: str) -> str:
    return ORB_STATES.get(state, ORB_STATES[ORB_STANDBY])[0]


@dataclass(frozen=True)
class _OrbStyle:
    """Pens, brushes and colours for one orb state, built once (the orb paints ~30 times a second)."""

    color: QColor
    tick_pen: QPen
    arc_pen: QPen
    hairline: QPen
    sweep: QBrush
    glow: tuple[QColor, QColor, QColor, QColor]
    core_white: QColor
    core_dark: QColor


_ORB_STYLES: dict[str, _OrbStyle] = {}


def _orb_style(state: str) -> _OrbStyle:
    style = _ORB_STYLES.get(state)
    if style is not None:
        return style
    color = QColor(orb_color(state))
    tick_pen = QPen(rgba(ACCENT, 0.42), 1.0)
    tick_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    arc_pen = QPen(rgba(color, 0.7), 2.0)
    arc_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    hairline = QPen(rgba(ACCENT, 0.25), 1.0)
    hairline.setCosmetic(True)
    # CSS conic-gradient(from 0deg, glow 0deg, transparent 70deg): bright at the
    # top, fading clockwise. Qt's conical gradient runs counter-clockwise from
    # 3 o'clock, so start at 90 degrees and put the colour at the end. The
    # leading edge gets a ~4 degree ramp; a hard edge renders as steps.
    sweep = QConicalGradient(QPointF(0, 0), 90)
    sweep.setColorAt(0.0, rgba(color, _SWEEP_ALPHA * 0.5))
    sweep.setColorAt(0.006, rgba(color, 0.0))
    sweep.setColorAt(1 - 70 / 360, rgba(color, 0.0))
    sweep.setColorAt(0.994, rgba(color, _SWEEP_ALPHA))
    sweep.setColorAt(1.0, rgba(color, _SWEEP_ALPHA * 0.5))
    glow = (rgba(color, _SWEEP_ALPHA), rgba(color, _SWEEP_ALPHA * 0.6), rgba(color, _SWEEP_ALPHA * 0.18),
            rgba(color, 0.0))
    style = _OrbStyle(color, tick_pen, arc_pen, hairline, QBrush(sweep), glow,
                      QColor("#ffffff"), QColor(2, 6, 12, 230))
    _ORB_STYLES[state] = style
    return style


class Orb(QWidget):
    """The animated status orb (tick ring, counter arc, radar sweep, pulsing core).

    One ~30 fps timer drives it, and only while the orb is visible and its
    window is not minimized. ``set_phase(t)`` freezes the animation at ``t``
    seconds (deterministic screenshots).
    """

    FRAME_MS = 33

    def __init__(self, size: int = 168, state: str = ORB_STANDBY, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._state = state if state in ORB_STATES else ORB_STANDBY
        self._t0 = time.monotonic()
        self._frozen: float | None = None
        self._animated = True
        self._timer = QTimer(self)
        # Windows rounds a coarse 33 ms timer up to its 15.6 ms tick (about 21 fps);
        # a precise one keeps ~30 fps at a similar cost (no multimedia timer above 20 ms).
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self.update)
        self.setAccessibleName("Status")
        self.setAccessibleDescription(ORB_STATES[self._state][1])

    # ---- API ------------------------------------------------------------------

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        state = state if state in ORB_STATES else ORB_STANDBY
        if state != self._state:
            self._state = state
            self.setAccessibleDescription(ORB_STATES[state][1])
            self.update()

    def set_phase(self, seconds: float | None) -> None:
        self._frozen = seconds
        self._sync_timer()
        self.update()

    def set_animated(self, animated: bool) -> None:
        self._animated = animated
        self._sync_timer()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    # ---- timer control ------------------------------------------------------

    def _should_run(self) -> bool:
        window = self.window()
        return (self._animated and self._frozen is None and self.isVisible()
                and not (window is not None and window.isMinimized()))

    def _sync_timer(self) -> None:
        if self._should_run():
            if not self._timer.isActive():
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()

    def _watch_window(self) -> None:
        # Minimizing does not hide child widgets, so listen to the window's state
        # changes. No reference to the window is kept (Qt drops the filter when
        # either object dies, and installing it twice keeps one).
        window = self.window()
        if window is not self:
            window.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.WindowStateChange and watched is self.window():
            QTimer.singleShot(0, self._sync_timer)
        return False

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._watch_window()
        self._sync_timer()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._sync_timer()

    # ---- painting ---------------------------------------------------------------

    def _elapsed(self) -> float:
        return self._frozen if self._frozen is not None else time.monotonic() - self._t0

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        t = self._elapsed()
        style = _orb_style(self._state)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(self.width() / 2, self.height() / 2)
            scale = self.width() / _ORB_BASE
            painter.scale(scale, scale)
            self._paint_rings(painter, t, style)
            self._paint_core(painter, t, style)
        finally:
            painter.end()

    @staticmethod
    def _paint_rings(painter: QPainter, t: float, style: _OrbStyle) -> None:
        # DOM order of the artboard: ticks, arc, inner circle, sweep, crosshair.
        base = painter.transform()
        painter.rotate((t / 24.0 * 360.0) % 360.0)
        painter.setPen(style.tick_pen)
        painter.drawLines(_TICKS)
        painter.setTransform(base)
        painter.rotate(-(t / 16.0 * 360.0) % 360.0)
        painter.setPen(style.arc_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(_ARC_RECT, 45 * 16, 90 * 16)
        painter.setTransform(base)
        painter.setPen(style.hairline)
        painter.drawEllipse(_ORIGIN, 61.5, 61.5)
        painter.rotate((t / 3.2 * 360.0) % 360.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(style.sweep)
        painter.drawEllipse(_ORIGIN, 62, 62)
        painter.setTransform(base)
        painter.setPen(style.hairline)
        painter.drawLines(_CROSSHAIR)

    @staticmethod
    def _paint_core(painter: QPainter, t: float, style: _OrbStyle) -> None:
        u = (t % 1.8) / 1.8
        k = _ease_in_out(u * 2) if u < 0.5 else 1.0 - _ease_in_out((u - 0.5) * 2)
        grow = 1.0 + 0.1 * k
        painter.setOpacity(0.7 + 0.3 * k)
        radius = 23.0 * grow
        glow_radius = radius + 24.0
        edge = radius / glow_radius
        full, edge_color, tail, clear = style.glow
        glow = QRadialGradient(_ORIGIN, glow_radius)
        glow.setColorAt(0.0, full)
        glow.setColorAt(edge * 0.8, full)
        glow.setColorAt(edge, edge_color)
        glow.setColorAt(edge + (1 - edge) * 0.45, tail)
        glow.setColorAt(1.0, clear)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(_ORIGIN, glow_radius, glow_radius)
        # radial-gradient(circle at 45% 38%, #fff 0%, state 32%, rgba(2,6,12,.9) 80%), farthest corner
        core = QRadialGradient(QPointF(-2.3 * grow, -5.52 * grow), 38.1 * grow)
        core.setColorAt(0.0, style.core_white)
        core.setColorAt(0.32, style.color)
        core.setColorAt(0.8, style.core_dark)
        painter.setBrush(core)
        painter.drawEllipse(_ORIGIN, radius, radius)
        painter.setOpacity(1.0)


class StateLabel(_Blinking):
    """Blinking square + state text in mono caps, coloured like the orb state."""

    def __init__(self, state: str = ORB_STANDBY, text: str | None = None, *,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = mono_font(12, 400, 0.2)
        self._state = ORB_STANDBY
        self._text = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.set_state(state, text)

    def state(self) -> str:
        return self._state

    def text(self) -> str:
        return self._text

    def set_state(self, state: str, text: str | None = None) -> None:
        self._state = state if state in ORB_STATES else ORB_STANDBY
        self._text = text if text is not None else ORB_STATES[self._state][1]
        self.setAccessibleName(self._text)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = 7 + 10 + _text_advance(self._font, self._text)
        return QSize(math.ceil(width), math.ceil(_line_height(self._font)) + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(40, self.sizeHint().height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            color = QColor(orb_color(self._state))
            rect = QRectF(self.rect())
            square = QRectF(0, round(rect.height() / 2 - 3.5), 7, 7)
            painter.fillRect(square, rgba(color, self.blink_opacity()))
            painter.setFont(self._font)
            painter.setPen(color)
            text_rect = rect.adjusted(7 + 10, 0, 0, 0)
            text = QFontMetricsF(self._font).elidedText(self._text, Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        finally:
            painter.end()


# --------------------------------------------------------------------------
# Header bar
# --------------------------------------------------------------------------

_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_HEADER_HEIGHT = 46
_HEADER_PAD = 14


def header_date(now: datetime) -> str:
    """"SUN 04 OCT" (English, independent of the locale)."""
    return f"{_DAYS[now.weekday()]} {now.day:02d} {_MONTHS[now.month - 1]}"


class _Brand(QWidget):
    """Rotated-square logo, JARVIS wordmark (with glow) and the mono subtitle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._word_font = display_font(20, 600, 0.34)
        self._sub_font = mono_font(11, 400, 0.1)
        self._subtitle = f"briefing {MIDDLE_DOT} desktop"
        self._show_subtitle = True
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_subtitle(self, text: str) -> None:
        self._subtitle = text
        self.updateGeometry()
        self.update()

    def subtitle(self) -> str:
        return self._subtitle

    def set_subtitle_visible(self, visible: bool) -> None:
        if visible != self._show_subtitle:
            self._show_subtitle = visible
            self.updateGeometry()
            self.update()

    def subtitle_visible(self) -> bool:
        return self._show_subtitle

    def _word_x(self) -> float:
        return _HEADER_PAD + 14 + 16

    def _sub_x(self) -> float:
        return self._word_x() + _text_advance(self._word_font, "JARVIS") + 16

    def width_for(self, subtitle: bool) -> int:
        if subtitle and self._subtitle:
            return math.ceil(self._sub_x() + _text_advance(self._sub_font, self._subtitle) + 4)
        return math.ceil(self._sub_x() - 12)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(self._show_subtitle), _HEADER_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(False), _HEADER_HEIGHT)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            dpr = self.devicePixelRatioF()
            mid = self.height() / 2
            self._paint_logo(painter, QPointF(_HEADER_PAD + 7, mid), dpr)
            metrics = QFontMetricsF(self._word_font)
            baseline = round(mid + (metrics.ascent() - metrics.descent()) / 2)
            origin = QPointF(self._word_x(), baseline)
            word = QPainterPath()
            word.addText(origin, self._word_font, "JARVIS")
            _draw_glow(painter, word, rgba(ACCENT, 0.45), 10, dpr=dpr,
                       key=("wordmark", origin.x(), origin.y()))
            painter.setFont(self._word_font)
            painter.setPen(QColor(TEXT_BRIGHT))
            painter.drawText(origin, "JARVIS")
            if self._show_subtitle:
                sub_metrics = QFontMetricsF(self._sub_font)
                sub_baseline = round(mid + (sub_metrics.ascent() - sub_metrics.descent()) / 2)
                painter.setFont(self._sub_font)
                painter.setPen(QColor(TEXT_DIM))
                painter.drawText(QPointF(self._sub_x(), sub_baseline), self._subtitle)
        finally:
            painter.end()

    @staticmethod
    def _paint_logo(painter: QPainter, center: QPointF, dpr: float) -> None:
        painter.save()
        painter.translate(center)
        painter.rotate(45)
        square = QPainterPath()
        square.addRect(QRectF(-7, -7, 14, 14))
        outside = QPainterPath()
        outside.addRect(QRectF(-30, -30, 60, 60))
        painter.save()
        painter.setClipPath(outside.subtracted(square))
        _draw_glow(painter, square, rgba(ACCENT, 0.9), 12, dpr=dpr, key="logo")
        painter.restore()
        painter.setPen(QPen(QColor(ACCENT), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(-6, -6, 12, 12))
        painter.restore()


class _Clock(QWidget):
    """Date (mono 11 dim) and time (mono 24 bright with glow), baseline aligned.

    The 12-hour clock ends in a small "AM" / "PM" on the same baseline. The
    widget is as wide as the widest time, so the header does not move when the
    hour gains a digit; the time keeps to the right edge and the date to it.
    """

    _MERIDIEM_GAP = 4
    _END_ROOM = 6             # after the digits: room for their glow
    _MERIDIEM_END_ROOM = 2    # after "AM" / "PM", which has no glow

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._date_font = mono_font(11, 400, 0.12)
        self._time_font = mono_font(24, 400)
        self._meridiem_font = mono_font(11, 500, 0.06)
        self._hour24 = False
        self._date = ""
        self._time = ""            # as read: "1:05 PM" (or "13:05")
        self._digits = ""          # as drawn: "1:05" big, then "PM" small
        self._meridiem = ""
        self._show_date = True
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_hour24(self, hour24: bool) -> None:
        """Takes effect at the next set_now."""
        if hour24 != self._hour24:
            self._hour24 = hour24
            self.updateGeometry()

    def hour24(self) -> bool:
        return self._hour24

    def set_date_visible(self, visible: bool) -> None:
        if visible != self._show_date:
            self._show_date = visible
            self.updateGeometry()
            self.update()

    def date_visible(self) -> bool:
        return self._show_date

    def set_now(self, now: datetime) -> None:
        date, clock = header_date(now), format_time(now, hour24=self._hour24)
        if (date, clock) != (self._date, self._time):
            self._date, self._time = date, clock
            self._digits, self._meridiem = clock_parts(now, hour24=self._hour24)
            self.setAccessibleName(f"{date} {clock}")
            self.updateGeometry()
            self.update()

    def texts(self) -> tuple[str, str]:
        return self._date, self._time

    def _date_width(self) -> float:
        return _text_advance(self._date_font, self._date or "WED 00 SEP")

    def _time_width(self) -> float:
        """The widest digits and, on the 12-hour clock, the gap and the wider of "AM" / "PM",
        with the room after them."""
        digits = _clock_width(self._time_font, self._hour24, 0)
        meridiem = _clock_width(self._meridiem_font, self._hour24, 1)
        if not meridiem:
            return digits + self._END_ROOM
        return digits + self._MERIDIEM_GAP + meridiem + self._MERIDIEM_END_ROOM

    def width_for(self, date: bool) -> int:
        return math.ceil((self._date_width() + 12 if date else 0) + self._time_width())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(self._show_date), _HEADER_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(False), _HEADER_HEIGHT)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            dpr = self.devicePixelRatioF()
            metrics = QFontMetricsF(self._time_font)
            baseline = round(self.height() / 2 + (metrics.ascent() - metrics.descent()) / 2)
            right = self.width() - (self._MERIDIEM_END_ROOM if self._meridiem else self._END_ROOM)
            meridiem_x = right - _text_advance(self._meridiem_font, self._meridiem)
            time_x = (meridiem_x - (self._MERIDIEM_GAP if self._meridiem else 0)
                      - _text_advance(self._time_font, self._digits))
            if self._show_date:
                painter.setFont(self._date_font)
                painter.setPen(QColor(TEXT_DIM))
                painter.drawText(QPointF(time_x - 12 - self._date_width(), baseline), self._date)
            origin = QPointF(time_x, baseline)
            path = QPainterPath()
            path.addText(origin, self._time_font, self._digits)
            _draw_glow(painter, path, rgba(ACCENT, 0.6), 14, dpr=dpr,
                       key=("clock", self._digits, round(origin.x(), 1), origin.y()))
            painter.setFont(self._time_font)
            painter.setPen(QColor(TEXT_BRIGHT))
            painter.drawText(origin, self._digits)
            if self._meridiem:
                painter.setFont(self._meridiem_font)
                painter.setPen(QColor(TEXT_SOFT))
                painter.drawText(QPointF(meridiem_x, baseline), self._meridiem)
        finally:
            painter.end()


class HeaderBar(QWidget):
    """The 46 px title bar of a frameless HUD window.

    Dragging anywhere but the close button moves the window (double-click does
    nothing). The clock updates on each minute boundary while the bar is shown.
    When the bar is narrow it drops detail in steps (``compact_level()``):
    1 hides the subtitle, 2 also the date, 3 also tightens the chips. A
    12-hour clock is wider, so it has a level 4 that also moves the chips up to
    the wordmark and the close button closer to the clock (the 560 px prompt),
    and it keeps at least 12 px from the chips, so its digits and their glow
    never crowd the last one.
    """

    closeRequested = Signal()

    _MAX_LEVEL = 4           # 3 on the 24-hour clock
    _RIGHT_MARGIN = 8
    _CHIP_MARGINS = ((16, 16), (16, 16), (16, 16), (8, 8), (0, 8))   # (left, right) of the chips, per level
    _CLOSE_GAPS = (10, 10, 10, 10, 6)                                  # between the clock and the X, per level
    _MERIDIEM_ROOM = 12      # at least this much between the chips and a 12-hour clock

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HEADER_HEIGHT)
        self._brand = _Brand()
        self._clock = _Clock()
        self._now: Callable[[], datetime] = datetime.now
        self._chips: dict[str, ServiceChip] = {}
        self._level = 0
        self._chips_box = QWidget()
        self._chip_row = QHBoxLayout(self._chips_box)
        self._chip_row.setSpacing(4)
        self._chip_row.setContentsMargins(16, 0, 16, 0)
        self.close_button = _CloseButton()
        self.close_button.clicked.connect(self._on_close_clicked)
        self._drag_offset: QPoint | None = None
        self._clock_timer = QTimer(self)
        self._clock_timer.setSingleShot(True)
        self._clock_timer.timeout.connect(self._tick)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, self._RIGHT_MARGIN, 1)
        layout.setSpacing(0)
        layout.addWidget(self._brand)
        layout.addStretch(1)
        layout.addWidget(self._chips_box)
        layout.addStretch(1)
        layout.addWidget(self._clock)
        self._close_gap = QSpacerItem(self._CLOSE_GAPS[0], 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        layout.addSpacerItem(self._close_gap)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._clock.set_now(self._now())

    def _on_close_clicked(self) -> None:
        self.closeRequested.emit()

    # ---- content ----------------------------------------------------------------

    def set_subtitle(self, text: str) -> None:
        self._brand.set_subtitle(text)
        self._fit()

    def subtitle(self) -> str:
        return self._brand.subtitle()

    def set_service(self, name: str, status: str, tooltip: str = "") -> ServiceChip:
        """Create or update the chip called ``name`` (chips keep their creation order)."""
        chip = self._chips.get(name)
        if chip is None:
            chip = ServiceChip(name, status, tooltip)
            chip.set_tight(self._level >= 3)
            self._chips[name] = chip
            self._chip_row.addWidget(chip)
            self.updateGeometry()
            self._fit()
        else:
            chip.set_status(status, tooltip)
        return chip

    # ---- narrow widths ---------------------------------------------------------------

    def compact_level(self) -> int:
        return self._level

    def _max_level(self) -> int:
        return self._MAX_LEVEL - 1 if self._clock.hour24() else self._MAX_LEVEL

    def _chip_margins(self, level: int) -> tuple[int, int]:
        left, right = self._CHIP_MARGINS[level]
        return left, right if self._clock.hour24() else max(right, self._MERIDIEM_ROOM)

    def _required_width(self, level: int) -> int:
        tight = level >= 3
        chips = [chip.width_for(tight) for chip in self._chips.values()]
        chips_width = sum(chips) + 4 * max(0, len(chips) - 1) + (sum(self._chip_margins(level)) if chips else 0)
        return (self._brand.width_for(level == 0) + chips_width + self._clock.width_for(level <= 1)
                + self._CLOSE_GAPS[level] + self.close_button.width() + self._RIGHT_MARGIN)

    def _fit(self, force: bool = False) -> None:
        """Pick the least compact level that fits (``force``: re-apply it after the clock changed)."""
        width = self.width()
        level = next((lvl for lvl in range(self._max_level() + 1) if self._required_width(lvl) <= width),
                     self._max_level())
        if level == self._level and not force:
            return
        self._level = level
        self._brand.set_subtitle_visible(level == 0)
        self._clock.set_date_visible(level <= 1)
        for chip in self._chips.values():
            chip.set_tight(level >= 3)
        left, right = self._chip_margins(level)
        self._chip_row.setContentsMargins(left, 0, right, 0)
        if self._close_gap.sizeHint().width() != self._CLOSE_GAPS[level]:
            self._close_gap.changeSize(self._CLOSE_GAPS[level], 0, QSizePolicy.Policy.Fixed,
                                       QSizePolicy.Policy.Minimum)
            self.layout().invalidate()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._required_width(self._max_level()), _HEADER_HEIGHT)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()

    def service_chip(self, name: str) -> ServiceChip | None:
        return self._chips.get(name)

    def set_clock(self, now: Callable[[], datetime]) -> None:
        """Replace the time source (tests, screenshots) and refresh."""
        self._now = now
        self._tick()

    def set_hour24(self, hour24: bool) -> None:
        """Show the time as "13:52" (True) or "1:52 PM" (False, the default)."""
        self._clock.set_hour24(hour24)
        self._tick()
        self.updateGeometry()
        self._fit(force=True)

    def clock_texts(self) -> tuple[str, str]:
        """("SUN 04 OCT", "1:52 PM") as read (the "PM" is drawn small); "13:52" on the 24-hour clock."""
        return self._clock.texts()

    def clock_running(self) -> bool:
        return self._clock_timer.isActive()

    def _tick(self) -> None:
        now = self._now()
        self._clock.set_now(now)
        if self.isVisible():
            ms_left = (60 - now.second) * 1000 - now.microsecond // 1000
            self._clock_timer.start(max(250, ms_left + 50))

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._tick()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._clock_timer.stop()

    # ---- painting and dragging --------------------------------------------------------

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), rgba(ACCENT, 0.14))
        finally:
            painter.end()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        event.accept()
        handle = self.window().windowHandle()
        if handle is not None and handle.startSystemMove():
            return
        self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        event.accept()   # no maximize / restore on double-click


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

PANEL_CYAN = "cyan"
PANEL_AMBER = "amber"


class ChamferPanel(QWidget):
    """A panel with a 1 px border, cut corners, a mono title row and a body layout."""

    def __init__(self, title: str = "", meta: str = "", *, variant: str = PANEL_CYAN, cut: int = 8,
                 corners: int = CUT_DIAGONAL, border_alpha: float | None = None,
                 padding: tuple[int, int, int, int] = (16, 14, 16, 14), spacing: int = 10,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cut = cut
        self._corners = corners
        amber = variant == PANEL_AMBER
        alpha = border_alpha if border_alpha is not None else (0.35 if amber else 0.22)
        self._border = rgba(AMBER if amber else ACCENT, alpha)
        self._ground = AMBER_GROUND if amber else PANEL_GROUND
        self.title_label = make_label(title.upper(), mono_font(11, 400, 0.16), AMBER if amber else ACCENT)
        self.meta_label = make_label(meta, mono_font(11, 400, 0.16), AMBER_META if amber else TEXT_DIM)
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._header = QWidget()
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.meta_label)
        self._header.setVisible(bool(title or meta))
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(*padding)
        self.body_layout.setSpacing(spacing)
        self.body_layout.addWidget(self._header)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text.upper())
        self._header.setVisible(bool(text or self.meta_label.text()))

    def set_meta(self, text: str, color: str | QColor | None = None) -> None:
        """Right-hand meta text, shown as given (the artboard mixes "1 PENDING" and "live")."""
        self.meta_label.setText(text)
        if color is not None:
            set_label_color(self.meta_label, color)
        self._header.setVisible(bool(text or self.title_label.text()))

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            painter.fillPath(chamfer_path(rect, self._cut, self._corners), self._border)
            painter.fillPath(chamfer_path(rect.adjusted(1, 1, -1, -1), self._cut, self._corners), self._ground)
        finally:
            painter.end()


STEP_DONE = "done"
STEP_ACTIVE = "active"
STEP_TODO = "todo"
STEP_DENIED = "denied"
_STEP_STYLE: dict[str, tuple[str, QColor, QColor, str]] = {
    # state: (dot, bottom border, background, text)
    STEP_DONE: (GREEN, rgba(GREEN, 0.3), rgba(GREEN, 0.06), "#c3ccd6"),
    STEP_ACTIVE: (ACCENT, QColor(ACCENT), rgba(ACCENT, 0x22 / 255), BUTTON_TEXT),
    STEP_TODO: (STEP_DIM, rgba("#ffffff", 0.08), rgba("#ffffff", 0.0), "#8592a0"),
    STEP_DENIED: (RED, rgba("#ffffff", 0.08), rgba("#ffffff", 0.0), RED),
}


class StepStrip(QWidget):
    """A row of step chips (dot + label, 2 px bottom border), e.g. neighbouring sections.

    Chips that do not fit are left out; when not even the first one fits, it
    is drawn narrower with its label elided.
    """

    MAX_LABEL_PX = 150
    _MIN_CUT_CHIP = 36 + 28    # chip padding and dot + room for a few letters

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = mono_font(11, 400, 0.04)
        self._steps: list[tuple[str, str]] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_steps(self, steps: Sequence[tuple[str, str]]) -> None:
        self._steps = [(label, state if state in _STEP_STYLE else STEP_TODO) for label, state in steps]
        self.setAccessibleName(", ".join(f"{label} ({state})" for label, state in self._steps))
        self.updateGeometry()
        self.update()

    def steps(self) -> list[tuple[str, str]]:
        return list(self._steps)

    def _chip_widths(self) -> list[float]:
        metrics = QFontMetricsF(self._font)
        return [12 + 6 + 6 + min(metrics.horizontalAdvance(label), self.MAX_LABEL_PX) + 12
                for label, _ in self._steps]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(math.ceil(sum(self._chip_widths())), math.ceil(_line_height(self._font)) + 12 + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, self.sizeHint().height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(self._font)
            metrics = QFontMetricsF(self._font)
            height = float(self.height())
            x = 0.0
            for index, ((label, state), width) in enumerate(zip(self._steps, self._chip_widths())):
                room = self.width() - x
                if width > room + 0.5:
                    # Never a half chip; only a lone first chip is cut down (its label elided).
                    if index > 0 or room < self._MIN_CUT_CHIP:
                        break
                    width = room
                dot, border, ground, text = _STEP_STYLE[state]
                painter.fillRect(QRectF(x, 0, width, height), ground)
                painter.fillRect(QRectF(x, height - 2, width, 2), border)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(dot))
                painter.drawEllipse(QPointF(x + 12 + 3, (height - 2) / 2), 3, 3)
                painter.setPen(QColor(text))
                shown = metrics.elidedText(label, Qt.TextElideMode.ElideRight, min(self.MAX_LABEL_PX, width - 36))
                painter.drawText(QRectF(x + 12 + 12, 0, width - 24, height - 2),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)
                x += width
        finally:
            painter.end()


class MainPanel(QWidget):
    """The transcript panel.

    Top corners cut 10 px, gradient ground with a soft inner glow, a header
    strip (``steps`` on the left, ``live`` on the right; ``strip_layout``
    takes more widgets at its right end) and ``body_layout``.
    """

    def __init__(self, cut: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cut = cut
        self._cache: QPixmap | None = None
        self._cache_key: tuple = ()
        self.steps = StepStrip()
        self.live = LiveMarker()
        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(20, 12, 20, 12)
        self.strip_layout.setSpacing(10)
        self.strip_layout.addWidget(self.steps, 1, Qt.AlignmentFlag.AlignVCenter)
        self.strip_layout.addWidget(self.live, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(8, 4, 4, 8)
        self.body_layout.setSpacing(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.strip)
        layout.addLayout(self.body_layout, 1)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._cache = None

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self.strip.geometry().getRect())
        if self._cache is None or key != self._cache_key:
            self._cache = self._render_background(dpr)
            self._cache_key = key
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, self._cache)
        finally:
            painter.end()

    def _render_background(self, dpr: float) -> QPixmap:
        size = self.size()
        image = QImage(max(1, round(size.width() * dpr)), max(1, round(size.height() * dpr)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(0, 0, size.width(), size.height())
            painter.fillPath(chamfer_path(rect, self._cut, CUT_TOP), rgba(ACCENT, 0.30))
            inner_rect = rect.adjusted(1, 1, -1, -1)
            inner = chamfer_path(inner_rect, self._cut, CUT_TOP)
            ground = QLinearGradient(inner_rect.topLeft(), inner_rect.bottomLeft())
            ground.setColorAt(0.0, QColor(4, 14, 22, 247))
            ground.setColorAt(1.0, QColor(2, 6, 11, 247))
            painter.fillPath(inner, ground)
            painter.setClipPath(inner)
            self._paint_inner_glow(painter, inner_rect)
            strip = QRectF(self.strip.geometry())
            painter.fillRect(strip, rgba(ACCENT, 0.04))
            painter.fillRect(QRectF(strip.left(), strip.bottom() - 1, strip.width(), 1), rgba(ACCENT, 0.16))
        finally:
            painter.end()
        return QPixmap.fromImage(image)

    @staticmethod
    def _paint_inner_glow(painter: QPainter, rect: QRectF) -> None:
        depth = 56.0
        edges = (
            (rect.topLeft(), QPointF(rect.left(), rect.top() + depth),
             QRectF(rect.left(), rect.top(), rect.width(), depth)),
            (rect.bottomLeft(), QPointF(rect.left(), rect.bottom() - depth),
             QRectF(rect.left(), rect.bottom() - depth, rect.width(), depth)),
            (rect.topLeft(), QPointF(rect.left() + depth, rect.top()),
             QRectF(rect.left(), rect.top(), depth, rect.height())),
            (rect.topRight(), QPointF(rect.right() - depth, rect.top()),
             QRectF(rect.right() - depth, rect.top(), depth, rect.height())),
        )
        for start, end, area in edges:
            gradient = QLinearGradient(start, end)
            gradient.setColorAt(0.0, rgba(ACCENT, 0.07))
            gradient.setColorAt(1.0, rgba(ACCENT, 0.0))
            painter.fillRect(area, gradient)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------

class TelemetryBar(QWidget):
    """Label left, value right (mono 11) and a 4 px segmented bar under them.

    A value given with a ``short`` form ("Mon 11:31 PM" for "yesterday
    11:31 PM") shows that form while the full value does not fit beside the
    label with ``_MIN_GAP`` to spare, so a narrow column never crowds them.
    """

    _MIN_GAP = 8     # between the label and a value that has a short form

    def __init__(self, label: str, value: str = "", fraction: float = 0.0, color: str | QColor = ACCENT,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = mono_font(11)
        self._label = label
        self._value = value
        self._short = ""
        self._fraction = max(0.0, min(1.0, fraction))
        self._color = QColor(color)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._update_accessible()

    def set_label(self, label: str) -> None:
        self._label = label
        self._update_accessible()
        self.update()

    def set_value(self, value: str, fraction: float | None = None, color: str | QColor | None = None,
                  short: str = "") -> None:
        self._value = value
        self._short = short
        if fraction is not None:
            self._fraction = max(0.0, min(1.0, fraction))
        if color is not None:
            self._color = QColor(color)
        self._update_accessible()
        self.update()

    def label(self) -> str:
        return self._label

    def value(self) -> str:
        return self._value

    def shown_value(self) -> str:
        """The value as painted at the bar's current width: ``value()`` or its short form."""
        room = self.width() - _text_advance(self._font, self._label) - self._MIN_GAP
        if self._short and _text_advance(self._font, self._value) > room:
            return self._short
        return self._value

    def fraction(self) -> float:
        return self._fraction

    def _update_accessible(self) -> None:
        self.setAccessibleName(f"{self._label}: {self._value}")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(200, math.ceil(_line_height(self._font)) + 4 + 4)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(80, self.sizeHint().height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            text_rect = QRectF(0, 0, self.width(), _line_height(self._font))
            painter.setFont(self._font)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)
            painter.setPen(QColor(TEXT_BRIGHT))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             self.shown_value())
            paint_dash_bar(painter, QRectF(0, self.height() - 4, self.width(), 4), self._fraction, self._color)
        finally:
            painter.end()


# --------------------------------------------------------------------------
# Section list
# --------------------------------------------------------------------------

ROW_PLAYED = "played"
ROW_CURRENT = "current"
ROW_UPCOMING = "upcoming"
ROW_IGNORED = "ignored"
_ROW_STYLE: dict[str, tuple[str, str, str, float, bool]] = {
    # state: (bar, title colour, meta colour, gradient strength, bar glow)
    ROW_PLAYED: (GREEN, TEXT_SOFT, TEXT_MUTED, 0.35, True),
    ROW_CURRENT: (ACCENT, TEXT_BRIGHT, TEXT_SOFT, 1.0, True),
    ROW_UPCOMING: ("#3f5a66", TEXT_BODY, TEXT_MUTED, 0.35, False),
    ROW_IGNORED: (SLATE, TEXT_DIM, TEXT_DIM, 0.0, False),
}


@dataclass(frozen=True)
class SectionRowInfo:
    """One row of a SectionList. ``number`` defaults to the 1-based position ("01").

    ``flag`` (e.g. "now") follows the meta only when the whole line fits, so a
    narrow row never cuts the meta short for it.
    """

    title: str
    meta: str = ""
    state: str = ROW_UPCOMING
    clickable: bool = True
    number: str = ""
    flag: str = ""


class SectionRow(QAbstractButton):
    """A clickable agenda-style row: glowing state bar, number, title and meta."""

    HEIGHT = 46
    _TEXT_LEFT = 21 + 26 + 4    # bar, number column, gap
    _TEXT_RIGHT = 8

    def __init__(self, info: SectionRowInfo, number: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._title_font = body_font(14)
        self._meta_font = mono_font(11)
        self._number_font = mono_font(12)
        self._number = number
        self._info = info
        self._focus_visible = False
        self.set_info(info)

    def info(self) -> SectionRowInfo:
        return self._info

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._focus_visible = _focus_ring_after(event.reason(), self._focus_visible)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def set_info(self, info: SectionRowInfo) -> None:
        self._info = info
        self.setEnabled(info.clickable)
        self.setCursor(Qt.CursorShape.PointingHandCursor if info.clickable else Qt.CursorShape.ArrowCursor)
        self.setText(info.title)
        self.setAccessibleName(f"{info.title}, {info.state}" + (f", {info.meta}" if info.meta else ""))
        self.setToolTip(info.title)
        self.update()

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(220, self.HEIGHT)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        bar, title_color, meta_color, strength, glow = _ROW_STYLE.get(self._info.state, _ROW_STYLE[ROW_UPCOMING])
        hover = self.isEnabled() and self.underMouse()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0.0, rgba(ACCENT, 0.07 * strength + (0.05 if hover else 0.0)))
            gradient.setColorAt(1.0, rgba(ACCENT, 0.0))
            painter.fillRect(rect, gradient)
            bar_rect = QRectF(8, round((rect.height() - 30) / 2), 3, 30)
            if glow:
                path = QPainterPath()
                path.addRect(bar_rect)
                _draw_glow(painter, path, QColor(bar), 10, dpr=self.devicePixelRatioF(),
                           key=("rowbar", bar, bar_rect.top()))
            painter.fillRect(bar_rect, QColor(bar))
            painter.setFont(self._number_font)
            painter.setPen(QColor(TEXT_BRIGHT if self._info.state == ROW_CURRENT else TEXT_MUTED))
            painter.drawText(QRectF(21, 0, 26, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self._info.number or self._number)
            self._paint_texts(painter, rect, title_color, meta_color)
            if self.hasFocus() and self._focus_visible:
                painter.setPen(QPen(QColor(ACCENT), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        finally:
            painter.end()

    def _paint_texts(self, painter: QPainter, rect: QRectF, title_color: str, meta_color: str) -> None:
        left = self._TEXT_LEFT
        width = rect.width() - left - self._TEXT_RIGHT
        title_metrics = QFontMetricsF(self._title_font)
        meta_metrics = QFontMetricsF(self._meta_font)
        has_meta = bool(self._info.meta or self._info.flag)
        block = title_metrics.height() + (meta_metrics.height() if has_meta else 0)
        top = round((rect.height() - block) / 2)
        painter.setFont(self._title_font)
        painter.setPen(QColor(title_color))
        title = title_metrics.elidedText(self._info.title, Qt.TextElideMode.ElideRight, width)
        painter.drawText(QPointF(left, top + title_metrics.ascent()), title)
        if has_meta:
            painter.setFont(self._meta_font)
            painter.setPen(QColor(meta_color))
            meta = self._meta_text(meta_metrics, width)
            painter.drawText(QPointF(left, top + title_metrics.height() + meta_metrics.ascent()), meta)

    def meta_text(self) -> str:
        """The meta line as painted at the row's current width."""
        return self._meta_text(QFontMetricsF(self._meta_font), self.width() - self._TEXT_LEFT - self._TEXT_RIGHT)

    def _meta_text(self, metrics: QFontMetricsF, width: float) -> str:
        meta, flag = self._info.meta, self._info.flag
        if flag:
            joined = f"{meta} {MIDDLE_DOT} {flag}" if meta else flag
            if metrics.horizontalAdvance(joined) <= width:
                return joined
        return metrics.elidedText(meta, Qt.TextElideMode.ElideRight, width)


class _ScrollContent(QWidget):
    """Scroll-area content whose height follows heightForWidth only.

    QScrollArea also enforces the content's minimumSizeHint, which for
    word-wrapped labels is measured at their narrowest width and so is too tall.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, 0)


def _transparent_scroll(area: QScrollArea, content: QWidget | None = None) -> QWidget:
    """Make ``area`` frameless and see-through; returns its (transparent) content widget.

    The default content is a _ScrollContent (height from heightForWidth only);
    rows of fixed height need a plain QWidget, whose layout minimum makes the
    area scroll instead of squeezing the rows together.
    """
    area.setObjectName("hudScroll")
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    content = content if content is not None else _ScrollContent()
    content.setObjectName("hudScrollContent")
    content.setAutoFillBackground(False)
    content.setStyleSheet("#hudScrollContent { background: transparent; }")
    area.setWidget(content)
    return content


class SectionList(QScrollArea):
    """Scrollable list of SectionRows; ``rowClicked(index)`` when a clickable row is clicked."""

    rowClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        content = _transparent_scroll(self, QWidget())
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._rows: list[SectionRow] = []

    def set_rows(self, rows: Sequence[SectionRowInfo]) -> None:
        """Show ``rows``; existing row widgets are reused, so focus and scroll survive updates."""
        while len(self._rows) > len(rows):
            row = self._rows.pop()
            row.hide()
            row.deleteLater()
        for index, info in enumerate(rows):
            if index < len(self._rows):
                self._rows[index].set_info(info)
                continue
            row = SectionRow(info, f"{index + 1:02d}")
            row.clicked.connect(self._on_row_clicked)
            self._layout.insertWidget(index, row)
            self._rows.append(row)

    def _on_row_clicked(self) -> None:
        row = self.sender()
        if row in self._rows:
            self.rowClicked.emit(self._rows.index(row))

    def set_row_state(self, index: int, state: str, meta: str | None = None) -> None:
        if 0 <= index < len(self._rows):
            info = self._rows[index].info()
            self._rows[index].set_info(SectionRowInfo(info.title, info.meta if meta is None else meta,
                                                      state, info.clickable, info.number, info.flag))

    def row(self, index: int) -> SectionRow:
        return self._rows[index]

    def row_count(self) -> int:
        return len(self._rows)

    def content_height(self) -> int:
        """Height of all rows with their gaps (what the list needs to show everything)."""
        count = len(self._rows)
        return count * SectionRow.HEIGHT + max(0, count - 1) * self._layout.spacing()

    def ensure_visible(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self.ensureWidgetVisible(self._rows[index], 0, 12)


# --------------------------------------------------------------------------
# Agenda and deadlines
# --------------------------------------------------------------------------

AGENDA_PAST = "past"
AGENDA_NOW = "now"
AGENDA_UPCOMING = "upcoming"
URGENCY_HIGH = "high"
URGENCY_MEDIUM = "medium"
URGENCY_LOW = "low"
DEADLINE_LATER = "#c3ccd6"    # the artboard's colour for a deadline that is not close yet
_AGENDA_STYLE: dict[str, tuple[str, float, bool, str, str, str]] = {
    # state: (bar, gradient strength, bar glow, time colour, title colour, meta colour)
    AGENDA_UPCOMING: (ACCENT, 1.0, True, TEXT_BRIGHT, TEXT_BODY, TEXT_MUTED),
    AGENDA_NOW: (GREEN, 1.4, True, TEXT_BRIGHT, TEXT_BRIGHT, GREEN),
    AGENDA_PAST: ("#3f5a66", 0.35, False, TEXT_DIM, TEXT_MUTED, TEXT_DIM),
}
URGENCY_COLORS = {URGENCY_HIGH: RED, URGENCY_MEDIUM: AMBER, URGENCY_LOW: DEADLINE_LATER}


@dataclass(frozen=True)
class AgendaRowInfo:
    """One agenda row: the time ("9:00 AM", "09:00", "ALL DAY"), title, meta and an AGENDA_* state."""

    time: str
    title: str
    meta: str = ""
    state: str = AGENDA_UPCOMING


@dataclass(frozen=True)
class DeadlineRowInfo:
    """One deadline row: title, source (drawn in capitals), due text and an URGENCY_* urgency."""

    title: str
    source: str = ""
    due: str = ""
    urgency: str = URGENCY_LOW


def _elide(metrics: QFontMetricsF, text: str, width: float) -> str:
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0.0, width))


def _split_meridiem(text: str) -> tuple[str, str]:
    """("12:30", "PM") for "12:30 PM"; (text, "") for anything else ("09:00", "ALL DAY")."""
    digits, _, meridiem = text.rpartition(" ")
    return (digits, meridiem) if digits and meridiem in ("AM", "PM") else (text, "")


class AgendaRow(QWidget):
    """The artboard's agenda row: a glowing 3 px bar, the time in mono, the title and a meta line.

    Upcoming rows have a cyan bar, the current one a green bar, past ones a
    dim bar without glow. Long titles and metas are cut with an ellipsis
    (the tooltip has them in full). A 12-hour time ("12:30 PM") is drawn as
    "12:30" on the title's line with a small "PM" under it on the meta line,
    so it fits the same column as "09:00". Such a row keeps its title on that
    line even without a meta, and so does every row of a list with 12-hour
    times (``set_two_line_times``, "ALL DAY" included), so the column keeps
    one rhythm.
    """

    HEIGHT = 44
    TIME_WIDTH = 44
    _PAD = 8
    _GAP = 10

    def __init__(self, info: AgendaRowInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._time_font = mono_font(12)
        self._time_small = mono_font(10)
        self._meridiem_font = mono_font(10, 400, 0.06)
        self._title_font = body_font(14)
        self._meta_font = mono_font(11)
        self._info = info
        self._two_line_times = False
        self.set_info(info)

    def info(self) -> AgendaRowInfo:
        return self._info

    def set_two_line_times(self, two_line: bool) -> None:
        """True while the list shows 12-hour times: keep the time and title on the first line, as those rows do."""
        if two_line != self._two_line_times:
            self._two_line_times = two_line
            self.update()

    def _two_line(self) -> bool:
        return self._two_line_times or bool(_split_meridiem(self._info.time)[1])

    def set_info(self, info: AgendaRowInfo) -> None:
        self._info = info
        text = f"{info.time} {info.title}" + (f", {info.meta}" if info.meta else "")
        self.setAccessibleName(f"{text}, {info.state}")
        self.setToolTip(info.title + (f"\n{info.meta}" if info.meta else ""))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(220, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(120, self.HEIGHT)

    def text_left(self) -> int:
        return self._PAD + 3 + self._GAP + self.TIME_WIDTH + self._GAP

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        state = self._info.state if self._info.state in _AGENDA_STYLE else AGENDA_UPCOMING
        bar, strength, glow, time_color, title_color, meta_color = _AGENDA_STYLE[state]
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            tint = GREEN if state == AGENDA_NOW else ACCENT
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0.0, rgba(tint, 0.07 * strength))
            gradient.setColorAt(1.0, rgba(tint, 0.0))
            painter.fillRect(rect, gradient)
            bar_rect = QRectF(self._PAD, round((rect.height() - 30) / 2), 3, 30)
            if glow:
                path = QPainterPath()
                path.addRect(bar_rect)
                _draw_glow(painter, path, QColor(bar), 10, dpr=self.devicePixelRatioF(),
                           key=("agendabar", bar, bar_rect.top()))
            painter.fillRect(bar_rect, QColor(bar))
            self._paint_time(painter, rect, time_color, meta_color)
            self._paint_texts(painter, rect, title_color, meta_color)
        finally:
            painter.end()

    def _text_top(self, rect: QRectF, with_meta: bool) -> int:
        """Top of the title line (and the meta line under it), centred in the row."""
        block = _line_height(self._title_font) + (_line_height(self._meta_font) if with_meta else 0)
        return round((rect.height() - block) / 2)

    def _paint_time(self, painter: QPainter, rect: QRectF, color: str, meridiem_color: str) -> None:
        text, meridiem = _split_meridiem(self._info.time)
        font = self._time_font if _text_advance(self._time_font, text) <= self.TIME_WIDTH else self._time_small
        painter.setFont(font)
        painter.setPen(QColor(color))
        left = self._PAD + 3 + self._GAP
        shown = _elide(QFontMetricsF(font), text, self.TIME_WIDTH + self._GAP - 2)
        if not self._two_line():
            painter.drawText(QRectF(left, 0, self.TIME_WIDTH + self._GAP, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)
            return
        # On the lines of a row with a meta, in every row, so the times line up down the column.
        top = self._text_top(rect, True)
        title_metrics = QFontMetricsF(self._title_font)
        painter.drawText(QPointF(left, top + title_metrics.ascent()), shown)
        if not meridiem:
            return
        painter.setFont(self._meridiem_font)
        painter.setPen(QColor(meridiem_color))
        meta_ascent = QFontMetricsF(self._meta_font).ascent()
        painter.drawText(QPointF(left, top + title_metrics.height() + meta_ascent), meridiem)

    def _paint_texts(self, painter: QPainter, rect: QRectF, title_color: str, meta_color: str) -> None:
        left = self.text_left()
        width = rect.width() - left - 6
        title_metrics = QFontMetricsF(self._title_font)
        meta_metrics = QFontMetricsF(self._meta_font)
        top = self._text_top(rect, bool(self._info.meta) or self._two_line())
        painter.setFont(self._title_font)
        painter.setPen(QColor(title_color))
        painter.drawText(QPointF(left, top + title_metrics.ascent()),
                         _elide(title_metrics, self._info.title, width))
        if self._info.meta:
            painter.setFont(self._meta_font)
            painter.setPen(QColor(meta_color))
            painter.drawText(QPointF(left, top + title_metrics.height() + meta_metrics.ascent()),
                             _elide(meta_metrics, self._info.meta, width))


class DeadlineRow(QWidget):
    """A deadline: title over its source (mono capitals), the due label on the right, a dashed rule.

    The due label is red, amber or soft grey by urgency. A label too wide
    for the row ("TONIGHT 11:59 PM" in a narrow column) is drawn smaller,
    on two lines when it has a space.
    """

    HEIGHT = 44
    _GAP = 10
    _DUE_SHARE = 0.42      # the due label may take this much of the row's width

    def __init__(self, info: DeadlineRowInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._title_font = body_font(13)
        self._source_font = mono_font(10, 400, 0.06)
        self._due_font = mono_font(15, 500)
        self._due_small = mono_font(11, 500, 0.04)
        self._info = info
        self.set_info(info)

    def info(self) -> DeadlineRowInfo:
        return self._info

    def set_info(self, info: DeadlineRowInfo) -> None:
        self._info = info
        parts = [info.title, info.source, info.due]
        self.setAccessibleName(", ".join(part for part in parts if part))
        self.setToolTip(info.title + (f"\n{info.source}" if info.source else "")
                        + (f"\nDue: {info.due}" if info.due else ""))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(220, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(120, self.HEIGHT)

    def due_lines(self, width: float | None = None) -> tuple[list[str], QFont]:
        """The due label as drawn at ``width`` (default: the row's): its lines and font."""
        width = float(self.width() if width is None else width)
        due = self._info.due
        if _text_advance(self._due_font, due) <= width * self._DUE_SHARE:
            return [due], self._due_font
        lines = due.split(" ", 1) if " " in due else [due]
        return lines, self._due_small

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(0, 0, self.width(), self.height() - 1)
            lines, font = self.due_lines()
            metrics = QFontMetricsF(font)
            due_width = min(max((metrics.horizontalAdvance(line) for line in lines), default=0.0),
                            rect.width() * 0.6)
            self._paint_due(painter, rect, lines, font, due_width)
            self._paint_texts(painter, rect, rect.width() - due_width - (self._GAP if due_width else 0))
            pen = QPen(rgba(ACCENT, 0.16), 1)
            pen.setDashPattern([4.0, 3.0])
            painter.setPen(pen)
            painter.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))
        finally:
            painter.end()

    def _paint_due(self, painter: QPainter, rect: QRectF, lines: list[str], font: QFont, width: float) -> None:
        if not lines or not lines[0]:
            return
        metrics = QFontMetricsF(font)
        painter.setFont(font)
        painter.setPen(QColor(URGENCY_COLORS.get(self._info.urgency, DEADLINE_LATER)))
        top = (rect.height() - metrics.height() * len(lines)) / 2
        for number, line in enumerate(lines):
            baseline = top + metrics.height() * number + metrics.ascent()
            shown = _elide(metrics, line, width)
            painter.drawText(QPointF(rect.right() - metrics.horizontalAdvance(shown), baseline), shown)

    def _paint_texts(self, painter: QPainter, rect: QRectF, width: float) -> None:
        title_metrics = QFontMetricsF(self._title_font)
        source_metrics = QFontMetricsF(self._source_font)
        source = self._info.source.upper()
        block = title_metrics.height() + (source_metrics.height() if source else 0)
        top = round((rect.height() - block) / 2)
        painter.setFont(self._title_font)
        painter.setPen(QColor(TEXT_BODY))
        painter.drawText(QPointF(0, top + title_metrics.ascent()), _elide(title_metrics, self._info.title, width))
        if source:
            painter.setFont(self._source_font)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(QPointF(0, top + title_metrics.height() + source_metrics.ascent()),
                             _elide(source_metrics, source, width))


class AgendaPanel(ChamferPanel):
    """TODAY / TOMORROW agenda rows followed by the DEADLINES list, scrolling inside the panel.

    ``set_day(title, meta)`` names the day; ``set_events(rows)`` shows
    AgendaRows, or ``set_message(text, color, link, tooltip)`` shows one line
    instead, with an optional link button (``link_button``, ``linkClicked``).
    ``set_deadlines(rows, empty_text)`` and ``set_deadlines_meta(text)`` fill
    the DEADLINES part. Row widgets are reused, so a refresh does not flicker.
    """

    linkClicked = Signal()
    EVENT_GAP = 4

    def __init__(self, title: str = "Today", parent: QWidget | None = None) -> None:
        super().__init__(title, "", border_alpha=0.18, parent=parent)
        caps = QFont(self.meta_label.font())
        caps.setCapitalization(QFont.Capitalization.AllUppercase)
        self.meta_label.setFont(caps)
        self._scroll = QScrollArea()
        content = _transparent_scroll(self._scroll, QWidget())
        self._event_rows: list[AgendaRow] = []
        self._deadline_rows: list[DeadlineRow] = []
        self._events_layout = QVBoxLayout()
        self._events_layout.setContentsMargins(0, 0, 0, 0)
        self._events_layout.setSpacing(self.EVENT_GAP)
        self._message_box = self._make_message_box()
        self._deadline_layout = QVBoxLayout()
        self._deadline_layout.setContentsMargins(0, 0, 0, 0)
        self._deadline_layout.setSpacing(0)
        self.deadlines_empty = make_label("", mono_font(11, 400, 0.02), TEXT_DIM, wrap=True)
        self.deadlines_empty.hide()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)   # keeps the rows off the scroll bar
        layout.setSpacing(0)
        layout.addLayout(self._events_layout)
        layout.addWidget(self._message_box)
        layout.addSpacing(12)
        layout.addWidget(self._make_deadlines_header())
        layout.addSpacing(2)
        layout.addLayout(self._deadline_layout)
        layout.addWidget(self.deadlines_empty)
        layout.addStretch(1)
        self.body_layout.addWidget(self._scroll, 1)

    def _make_message_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)
        self.message_label = make_label("", mono_font(11, 400, 0.02), TEXT_SOFT, wrap=True)
        self.link_button = HudButton("Connect", LINK)
        self.link_button.set_link_color(ACCENT)
        self.link_button.clicked.connect(self._on_link)
        self.link_button.hide()
        layout.addWidget(self.message_label)
        layout.addWidget(self.link_button, 0, Qt.AlignmentFlag.AlignLeft)
        box.hide()
        return box

    def _make_deadlines_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        self.deadlines_title = make_label("DEADLINES", mono_font(11, 400, 0.16), ACCENT)
        caps = mono_font(11, 400, 0.16)
        caps.setCapitalization(QFont.Capitalization.AllUppercase)
        self.deadlines_meta = make_label("", caps, TEXT_DIM)
        self.deadlines_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.deadlines_title)
        row.addStretch(1)
        row.addWidget(self.deadlines_meta)
        return header

    def _on_link(self) -> None:
        self.linkClicked.emit()

    # ---- narrow columns -------------------------------------------------------------

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_metas()

    def _fit_metas(self) -> None:
        """Hide a meta that does not fit beside its title, so it never widens the panel
        (a wider scroll content would cut the wrapped lines off at the right)."""
        margins = self.body_layout.contentsMargins()
        width = self.width() - margins.left() - margins.right()
        pairs = ((self.title_label, self.meta_label, width),
                 (self.deadlines_title, self.deadlines_meta, width - 6 - 10))   # content margin, scroll bar
        for title, meta, room in pairs:
            fits = title.sizeHint().width() + 8 + meta.sizeHint().width() <= room
            meta.setVisible(bool(meta.text()) and fits)

    # ---- content ------------------------------------------------------------------

    def set_meta(self, text: str, color: str | QColor | None = None) -> None:
        super().set_meta(text, color)
        self._fit_metas()

    def set_day(self, title: str, meta: str = "") -> None:
        self.set_title(title)
        self.set_meta(meta)

    def set_events(self, rows: Sequence[AgendaRowInfo]) -> None:
        """Show these agenda rows (and no message line)."""
        _reuse_rows(self._event_rows, self._events_layout, rows, AgendaRow)
        two_line = any(_split_meridiem(row.time)[1] for row in rows)
        for row in self._event_rows:
            row.set_two_line_times(two_line)
        self._message_box.hide()

    def set_message(self, text: str, color: str | QColor = TEXT_SOFT, *, link: str = "",
                    tooltip: str = "") -> None:
        """One line instead of the agenda rows; ``link`` adds a link button under it."""
        _reuse_rows(self._event_rows, self._events_layout, (), AgendaRow)
        self.message_label.setText(text)
        self.message_label.setToolTip(tooltip)
        set_label_color(self.message_label, color)
        self.link_button.setText(link or "Connect")
        self.link_button.setVisible(bool(link))
        self.link_button.updateGeometry()
        self._message_box.setVisible(bool(text or link))

    def set_deadlines(self, rows: Sequence[DeadlineRowInfo], empty_text: str = "") -> None:
        _reuse_rows(self._deadline_rows, self._deadline_layout, rows, DeadlineRow)
        self.deadlines_empty.setText(empty_text)
        self.deadlines_empty.setVisible(not rows and bool(empty_text))

    def set_deadlines_meta(self, text: str) -> None:
        self.deadlines_meta.setText(text)
        self._fit_metas()

    # ---- for tests ---------------------------------------------------------------

    def events(self) -> list[AgendaRowInfo]:
        return [row.info() for row in self._event_rows]

    def event_rows(self) -> list[AgendaRow]:
        return list(self._event_rows)

    def deadlines(self) -> list[DeadlineRowInfo]:
        return [row.info() for row in self._deadline_rows]

    def deadline_rows(self) -> list[DeadlineRow]:
        return list(self._deadline_rows)

    def message(self) -> str:
        """The message line ("" while agenda rows or nothing are shown)."""
        return self.message_label.text() if not self._message_box.isHidden() else ""

    def scroll_area(self) -> QScrollArea:
        return self._scroll


def _reuse_rows(rows: list, layout: QVBoxLayout, infos: Sequence[Any],
                factory: Callable[[Any], QWidget]) -> None:
    """Make ``rows`` show ``infos``: widgets are updated in place, added or removed as needed."""
    while len(rows) > len(infos):
        row = rows.pop()
        layout.removeWidget(row)
        row.hide()
        row.deleteLater()
    for index, info in enumerate(infos):
        if index < len(rows):
            if rows[index].info() != info:
                rows[index].set_info(info)
            continue
        row = factory(info)
        layout.addWidget(row)
        rows.append(row)


# --------------------------------------------------------------------------
# Chip button and popover
# --------------------------------------------------------------------------

class ChipButton(QAbstractButton):
    """A small chamfered toggle in mono capitals with an optional count and a caret ("SECTIONS 8").

    Checkable: it shows "open" (bright border, caret up) while checked, so it
    can stand for a popover. Tab focus only, with a ring for keyboard focus.
    """

    HEIGHT = 26
    _PAD = 10
    _CARET = 8

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._font = mono_font(11, 400, 0.14)
        self._meta_font = mono_font(11)
        self._meta = ""
        self._focus_visible = False
        self.setAccessibleName(text)

    def set_meta(self, text: str) -> None:
        if text != self._meta:
            self._meta = text
            self.setAccessibleName(f"{self.text()} {text}".strip())
            self.updateGeometry()
            self.update()

    def meta(self) -> str:
        return self._meta

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = self._PAD + _text_advance(self._font, self.text().upper())
        if self._meta:
            width += 8 + _text_advance(self._meta_font, self._meta)
        width += 8 + self._CARET + self._PAD
        return QSize(math.ceil(width), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._focus_visible = _focus_ring_after(event.reason(), self._focus_visible)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        opened, hover = self.isChecked() or self.isDown(), self.underMouse() and self.isEnabled()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect())
            painter.fillPath(chamfer_path(rect, 5), rgba(ACCENT, 0.14 if opened else 0.08 if hover else 0.04))
            border = rgba(ACCENT, 0.9 if opened else 0.6 if hover else 0.3)
            painter.setPen(QPen(border, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(chamfer_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5))
            text_color = QColor(TEXT_BRIGHT if opened or hover else TEXT_SOFT)
            if not self.isEnabled():
                text_color = QColor(TEXT_DIM)
            x = float(self._PAD)
            painter.setFont(self._font)
            painter.setPen(text_color)
            label = self.text().upper()
            painter.drawText(QRectF(x, 0, rect.width(), rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            x += _text_advance(self._font, label)
            if self._meta:
                x += 8
                painter.setFont(self._meta_font)
                painter.setPen(QColor(ACCENT if opened else TEXT_DIM))
                painter.drawText(QRectF(x, 0, rect.width(), rect.height()),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._meta)
            self._paint_caret(painter, rect.width() - self._PAD - self._CARET / 2, rect.height() / 2,
                              opened, text_color)
            if self.hasFocus() and self._focus_visible:
                painter.setPen(QPen(QColor(ACCENT), 2))
                painter.drawPath(chamfer_path(rect.adjusted(1, 1, -1, -1), 5))
        finally:
            painter.end()

    def _paint_caret(self, painter: QPainter, cx: float, cy: float, up: bool, color: QColor) -> None:
        half, rise = self._CARET / 2, 2.0 if up else -2.0
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(cx - half, cy + rise), QPointF(cx, cy - rise),
                                        QPointF(cx + half, cy + rise)]))


class Popover(QWidget):
    """A frameless popup holding ``panel`` (a ChamferPanel), opened under an anchor widget.

    It closes on a click outside it, on Escape and on ``close()``; ``closed``
    is emitted whenever it hides. A click on the anchor itself only closes
    it (that press is not replayed to the anchor), so a toggle button that
    opened it does not open it again at once. Create it together with its
    window, before that is shown: a popup created later was seen to close
    again right after its first show (offscreen platform).
    """

    closed = Signal()
    MARGIN = 10      # room around the panel for its shadow
    _GAP = 6         # between the anchor and the panel

    def __init__(self, title: str = "", meta: str = "", parent: QWidget | None = None) -> None:
        flags = (Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._anchor: weakref.ref[QWidget] | None = None   # weak: the anchor is a sibling widget
        self.panel = ChamferPanel(title, meta, border_alpha=0.4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        layout.setSpacing(0)
        layout.addWidget(self.panel)

    def anchor(self) -> QWidget | None:
        anchor = self._anchor() if self._anchor is not None else None
        return anchor if anchor is not None and shiboken6.isValid(anchor) else None

    def open_below(self, anchor: QWidget, width: int, height: int) -> None:
        """Show the panel ``width`` x ``height`` under ``anchor``, right edges aligned, on screen."""
        self._anchor = weakref.ref(anchor)
        margin = self.MARGIN
        size = QSize(width + 2 * margin, height + 2 * margin)
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
        top = anchor.mapToGlobal(QPoint(0, 0)).y()
        screen = anchor.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = corner.x() - width - margin
        y = corner.y() + self._GAP - margin
        if y + size.height() > area.bottom() + 1 and top - self._GAP - height - margin >= area.top():
            y = top - self._GAP - height - margin   # no room below: open above the anchor
        x = max(area.left(), min(x, area.right() + 1 - size.width()))
        y = max(area.top(), min(y, area.bottom() + 1 - size.height()))
        self.setGeometry(QRect(QPoint(x, y), size))
        self.show()   # a Qt popup takes the keyboard by itself (activating it can close it again)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        anchor = self.anchor()
        point = event.globalPosition().toPoint()
        on_anchor = (anchor is not None and anchor.isVisible()
                     and QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size()).contains(point))
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, on_anchor)
        super().mousePressEvent(event)

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self.closed.emit()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            geometry = self.panel.geometry()
            outline = chamfer_path(QRectF(geometry), 8)
            _draw_glow(painter, outline, QColor(0, 0, 0, 200), 9, dpr=self.devicePixelRatioF(),
                       key=("popover", geometry.getRect()))
            painter.fillPath(outline, QColor(GROUND))   # the panel's own ground is not quite opaque
        finally:
            painter.end()


# --------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------

CARD_PENDING = "pending"
CARD_WORKING = "working"
CARD_SIGNIN = "signin"
CARD_ADDED = "added"
CARD_EXISTS = "exists"
CARD_DENIED = "denied"
CARD_FAILED = "failed"
CARD_DONE = "done"
CARD_STATUSES = (CARD_PENDING, CARD_WORKING, CARD_SIGNIN, CARD_ADDED, CARD_EXISTS, CARD_DENIED, CARD_FAILED,
                 CARD_DONE)
_CARD_RESULTS: dict[str, tuple[str, str]] = {
    CARD_WORKING: ("WORKING...", ACCENT),
    CARD_SIGNIN: ("WAITING FOR GOOGLE SIGN-IN", ACCENT),
    CARD_ADDED: ("ADDED", GREEN),
    CARD_EXISTS: ("ALREADY ON CALENDAR", GREEN),
    CARD_DENIED: ("DENIED", RED),
    CARD_FAILED: ("FAILED", RED),
    CARD_DONE: ("DONE", GREEN),
}
COPIED_TEXT = "Copied"
COPIED_MS = 1500


_TIP_CHARS = 1200   # a longer tooltip (a drafted reply can have 5000) would be taller than the screen
_TIP_LINES = 24


def _capped_tip_text(text: str, more: str = "") -> str:
    """``text`` cut to about 24 lines and 1200 characters for a tooltip, ending "..." and ``more``."""
    cut = "\n".join(text.split("\n")[:_TIP_LINES])
    if len(cut) > _TIP_CHARS:
        cut = cut[:_TIP_CHARS]
        space = cut.rfind(" ", _TIP_CHARS * 3 // 4)
        cut = cut[:space] if space > 0 else cut
    if cut == text:
        return text
    return cut.rstrip() + "\u2026" + (f"\n({more})" if more else "")


class _ClampedLabel(QLabel):
    """Word-wrapped plain text cut to ``max_lines`` lines at its width (ellipsis, full text as tooltip).

    ``full_text()`` is what was set, ``text()`` what is shown. A ``tooltip``
    given with the text is shown whether or not the text had to be cut.
    """

    def __init__(self, font: QFont, color: str | QColor, max_lines: int = 2) -> None:
        super().__init__()
        self._full = ""
        self._tip: str | None = None
        self._max_lines = max_lines
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setFont(font)
        set_label_color(self, color)

    def set_full_text(self, text: str, tooltip: str | None = None) -> None:
        self._full = text
        self._tip = tooltip
        self._render()

    def full_text(self) -> str:
        return self._full

    def _render(self) -> None:
        shown = _elide_to_lines(self._full, self.font(), self.contentsRect().width() - 2, self._max_lines)
        cut = plain_tooltip(self._full) if shown != self._full else ""
        self.setToolTip(self._tip if self._tip is not None else cut)
        super().setText(shown)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._render()


_SOFT_BREAK = "\u200b"      # zero-width space: a line may break here, nothing is drawn
_LONGEST_PIECE = 24         # a card's detail holds about 25 mono characters per line at the narrowest
_LONG_WORD = _LONGEST_PIECE  # a word that fits on a line even then ("jordan.smith@example.edu") stays whole


def _with_soft_breaks(text: str) -> str:
    """``text`` with zero-width break chances inside long words, so an email address wraps.

    QLabel wraps only at spaces and hyphens, which cuts "firstname.lastname@cs.example.edu" at
    the card edge on a narrow window. In a word longer than _LONG_WORD characters, break chances
    go after "@" and "/" and before "."; a piece still longer than _LONGEST_PIECE characters
    (hyphens count as breaks) gets one every _LONGEST_PIECE characters.
    """
    words = re.split(r"(\s+)", text)
    for index, word in enumerate(words):
        if len(word.rstrip(",;")) <= _LONG_WORD or word.isspace():   # "ana@example.edu," in a list
            continue
        out: list[str] = []
        run = 0
        for char in word:
            if char == "." and out and run:
                out.append(_SOFT_BREAK)
                run = 0
            if run >= _LONGEST_PIECE:
                out.append(_SOFT_BREAK)
                run = 0
            out.append(char)
            run += 1
            if char in "@/":
                out.append(_SOFT_BREAK)
                run = 0
            elif char == "-":   # QLabel may already break after a hyphen
                run = 0
        words[index] = "".join(out)
    return "".join(words)


class _BreakableLabel(QLabel):
    """A word-wrapped plain-text label that can also break inside long words (see _with_soft_breaks).

    ``text()`` and the accessible name are the text as set, without the break chances.
    """

    def __init__(self, text: str, font: QFont, color: str | QColor) -> None:
        super().__init__()
        self._plain = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setFont(font)
        set_label_color(self, color)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - mirrors QLabel
        self._plain = text or ""
        self.setAccessibleName(self._plain)
        super().setText(_with_soft_breaks(self._plain))

    def text(self) -> str:  # noqa: D102 - mirrors QLabel
        return self._plain


class _GrowOnlyLabel(QLabel):
    """A word-wrapped plain-text line that never gets shorter once it has been shown.

    Hidden until it first gets text. From then on it stays in the layout (blank
    when cleared) and keeps the height of the tallest text it has shown, at the
    current width, so the card holding it can grow downward but never shrinks
    under the mouse. With ``max_lines`` the text is cut to that many lines at
    the current width (ellipsis; the full text is the tooltip), which bounds
    how far it can ever push the cards below. ``full_text()`` is what was set,
    ``text()`` what is shown.
    """

    _REMEMBER = 4
    _CACHE_LIMIT = 256

    def __init__(self, font: QFont, color: str | QColor, alignment: Qt.AlignmentFlag, *,
                 max_lines: int = 0) -> None:
        super().__init__()
        self._meter = QLabel(self)   # measures the earlier texts; never shown
        self._meter.hide()
        for label in (self, self._meter):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setFont(font)
            label.setAlignment(alignment | Qt.AlignmentFlag.AlignTop)
            set_label_color(label, color)
        self._max_lines = max_lines
        self._full = ""
        self._shown: list[str] = []
        self._heights: dict[tuple[str, int], int] = {}
        self.hide()

    def used(self) -> bool:
        return bool(self._shown)

    def full_text(self) -> str:
        return self._full

    def set_line(self, text: str) -> None:
        self._full = text
        self._render()
        if text and text not in self._shown:
            self._shown.append(text)
            self._shown.sort(key=len, reverse=True)
            del self._shown[self._REMEMBER:]
        self.setVisible(self.used())
        self.updateGeometry()

    def _fit(self, text: str, width: int) -> str:
        """``text`` as shown at ``width`` (cut to ``max_lines``, with a little slack like _ClampedLabel)."""
        return _elide_to_lines(text, self.font(), width - 2, self._max_lines) if self._max_lines else text

    def _render(self) -> None:
        shown = self._fit(self._full, self.contentsRect().width())
        self.setToolTip(plain_tooltip(self._full) if shown != self._full else "")
        self.setText(shown)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        # A cut label is measured only through the texts it has shown (each cut
        # at ``width``), so a long text never counts for more than max_lines.
        height = 0 if self._max_lines and self._shown else super().heightForWidth(width)
        if len(self._heights) > self._CACHE_LIMIT:
            self._heights.clear()
        for text in self._shown:
            key = (text, width)
            if key not in self._heights:
                self._meter.setText(self._fit(text, width))
                self._heights[key] = self._meter.heightForWidth(width)
            height = max(height, self._heights[key])
        return height

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._max_lines and event.oldSize().width() != event.size().width():
            self._render()


class _ToolLink(HudButton):
    """A card's tools-row link: the LINK look, but its text starts flush with the card's texts.

    No side padding, and the text is drawn from the left edge, so "Copied" in
    a button sized for "Copy reply" stays where the words began.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text, LINK)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().sizeHint()
        return QSize(max(1, hint.width() - 2 * self._spec.padding), hint.height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            _fill, _border, text_color = self._colors()
            font = QFont(self._font)
            if (self.isEnabled() and self.underMouse()) or (self.hasFocus() and self._focus_visible):
                font.setUnderline(True)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                             | Qt.TextFlag.TextShowMnemonic, self.text())
        finally:
            painter.end()


class _DecisionButton(HudButton):
    """Deny / Approve: also reports a click made while it is disabled (``disabledClicked``).

    A disabled QAbstractButton takes the mouse events itself (so they never
    reach a widget below), which is the only place such a click can be seen.
    """

    disabledClicked = Signal()

    def event(self, event: QEvent) -> bool:
        if (event.type() == QEvent.Type.MouseButtonRelease and not self.isEnabled()
                and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.disabledClicked.emit()
        return super().event(event)


class ActionCard(QWidget):
    """An approval card: kind, title, detail, then one fixed-height slot with Deny / Approve or the result.

    The slot swaps its content (the buttons, or a result such as ADDED with an
    "Open" link) but never its height, so a click never moves this card or the
    cards below it, and a second click at the same spot cannot land on another
    card's button. FAILED keeps the buttons (to retry) and shows the reason
    under them, cut to two lines (the whole reason is the tooltip); that line
    and the amber note (``set_note``) let the card grow downward by a bounded
    amount, but a card never gets shorter again. ``actionable=False``
    makes an informational card (no buttons, dim kind). ADDED / EXISTS show
    the "Open" link when a link is known (``openClicked(link)``).

    ``approve_text`` names the right-hand button ("Approve", "Add block",
    "Done"); ``title_lines`` cuts the title to that many lines (0 = never);
    ``body`` adds a three-line preview of a drafted text (hover for all of
    it). ``open_text`` / ``copy_text`` add a tools row of links above the
    slot ("Open thread" -> ``sourceClicked(action_id)``, "Copy reply" ->
    ``copyClicked(action_id)``; ``show_copied()`` reads "Copied" for 1.5 s).
    The tools row is there from the start, is never locked or hidden, and
    its buttons keep their width, so nothing in it moves.

    ``set_locked(True, tooltip)`` disables (dims) Deny / Approve without any
    change in size, for example while another card's approval runs; a click
    on a locked button emits ``lockedClicked(action_id)`` instead of
    ``approveClicked`` / ``denyClicked``, so the caller can log it.
    """

    approveClicked = Signal(str)
    denyClicked = Signal(str)
    openClicked = Signal(str)
    lockedClicked = Signal(str)
    sourceClicked = Signal(str)
    copyClicked = Signal(str)

    _PAD_LEFT = 14
    _PAD_TOP = 10
    _PAD_RIGHT = 12
    _PAD_BOTTOM = 10
    _SLOT_GAP = 8   # between the texts and the buttons / result
    _FAILURE_LINES = 2
    _BODY_LINES = 3
    _BODY_GAP = 4    # above the body preview (on top of the texts' 2 px spacing)
    _TOOLS_GAP = 4   # above the tools row
    _TOOLS_SPACING = 18

    def __init__(self, action_id: str, kind: str, title: str, detail: str = "", *,
                 actionable: bool = True, approve_text: str = "Approve", body: str = "",
                 title_lines: int = 0, open_text: str = "", copy_text: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action_id = action_id
        self._actionable = actionable
        self._status = CARD_PENDING
        self._link = ""
        self._result_text = ""
        self._locked = False
        self._lock_tip = ""
        self._title = title
        self._copy_text = copy_text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        # One line, cut with an ellipsis when an account alias makes it wider than the card.
        self.kind_label = _ClampedLabel(mono_font(10, 400, 0.16), AMBER if actionable else TEXT_DIM, max_lines=1)
        self.kind_label.set_full_text(kind.upper())
        if title_lines > 0:
            self.title_label: QLabel = _ClampedLabel(body_font(14, 600), AMBER_TEXT, max_lines=title_lines)
            self.title_label.set_full_text(title)
        else:
            self.title_label = make_label(title, body_font(14, 600), AMBER_TEXT, wrap=True)
        # Wraps inside long email addresses too (QLabel alone would cut them at the card edge).
        self.detail_label: QLabel = _BreakableLabel(detail, mono_font(11), AMBER_SUB)
        self.detail_label.setVisible(bool(detail))
        self.body_label = _ClampedLabel(body_font(12), TEXT_SOFT, max_lines=self._BODY_LINES)
        self.body_label.setContentsMargins(0, self._BODY_GAP, 0, 0)
        more = f"{copy_text} for the whole text" if copy_text else ""
        self.body_label.set_full_text(" ".join(body.split()), tooltip=plain_tooltip(_capped_tip_text(body, more)))
        self.body_label.setVisible(bool(body.strip()))
        self.source_button: HudButton = _ToolLink(open_text or "Open")
        self.copy_button: HudButton = _ToolLink(copy_text or "Copy")
        self._make_tools(kind, title, open_text, copy_text)
        self.deny_button = _DecisionButton("Deny", DENY, compact=True)
        self.approve_button = _DecisionButton(approve_text or "Approve", APPROVE, compact=True)
        self.result_label = _ClampedLabel(mono_font(11, 400, 0.12), GREEN)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.open_button = HudButton("Open", LINK)
        self.open_button.set_link_color(ACCENT)
        # Two lines at most (hover for the whole reason): a 200-character Google
        # error would otherwise push the cards below down by about a card's
        # height and slide another card's Approve under the mouse.
        self.failure_label = _GrowOnlyLabel(mono_font(11, 400, 0.12), RED, Qt.AlignmentFlag.AlignRight,
                                            max_lines=self._FAILURE_LINES)
        self.note_label = _GrowOnlyLabel(mono_font(11), AMBER, Qt.AlignmentFlag.AlignLeft)
        self.deny_button.clicked.connect(self._on_deny)
        self.approve_button.clicked.connect(self._on_approve)
        self.deny_button.disabledClicked.connect(self._on_locked_click)
        self.approve_button.disabledClicked.connect(self._on_locked_click)
        self.open_button.clicked.connect(self._on_open)
        self.source_button.clicked.connect(self._on_source)
        self.copy_button.clicked.connect(self._on_copy)
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(COPIED_MS)
        self._copied_timer.timeout.connect(self._reset_copy)
        self._build_layout()
        self.set_status(CARD_PENDING)

    def _make_tools(self, kind: str, title: str, open_text: str, copy_text: str) -> None:
        """The tools row: Open / Copy links, each as wide as its widest text from the start."""
        self._tools = QWidget()
        tools = FlowLayout(self._tools, h_spacing=self._TOOLS_SPACING, v_spacing=2)
        tools.setContentsMargins(0, self._TOOLS_GAP, 0, 0)
        for button, text, texts in ((self.source_button, open_text, (open_text,)),
                                    (self.copy_button, copy_text, (copy_text, COPIED_TEXT))):
            button.set_link_color(ACCENT)
            fallback = button.text()
            width = 0
            for shown in texts:
                button.setText(shown)
                width = max(width, button.sizeHint().width())
            button.setText(text or fallback)
            button.setMinimumWidth(width)
            button.setAccessibleName(f"{text} ({kind.upper()}: {title})")
            button.setVisible(bool(text))
            tools.addWidget(button)
        self._tools.setVisible(bool(open_text or copy_text))

    # Bound methods, not lambdas: a lambda capturing self keeps the card alive.
    def _on_deny(self) -> None:
        self.denyClicked.emit(self.action_id)

    def _on_approve(self) -> None:
        self.approveClicked.emit(self.action_id)

    def _on_open(self) -> None:
        self.openClicked.emit(self._link)

    def _on_source(self) -> None:
        self.sourceClicked.emit(self.action_id)

    def _on_copy(self) -> None:
        self.copyClicked.emit(self.action_id)

    def show_copied(self) -> None:
        """The Copy button reads "Copied" for 1.5 s (its width never changes)."""
        if not self._copy_text:
            return
        self.copy_button.setText(COPIED_TEXT)
        self._copied_timer.start()

    def _reset_copy(self) -> None:
        self.copy_button.setText(self._copy_text)

    def _build_layout(self) -> None:
        self._texts = QVBoxLayout()
        self._texts.setSpacing(2)
        for label in (self.kind_label, self.title_label, self.detail_label, self.body_label):
            self._texts.addWidget(label)
        self._texts.addWidget(self._tools)
        # Both slot pages reach the card's right and bottom edges (their margins
        # are the card padding) so the Approve glow is not clipped.
        self._buttons = QWidget()
        buttons = QHBoxLayout(self._buttons)
        buttons.setContentsMargins(0, self._SLOT_GAP, self._PAD_RIGHT, self._PAD_BOTTOM)
        buttons.setSpacing(6)
        buttons.addStretch(1)
        buttons.addWidget(self.deny_button)
        buttons.addWidget(self.approve_button)
        self._result = QWidget()
        result = QHBoxLayout(self._result)
        result.setContentsMargins(0, self._SLOT_GAP, self._PAD_RIGHT, self._PAD_BOTTOM)
        result.setSpacing(10)
        result.addWidget(self.result_label, 1)
        result.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # One slot, one height: the result takes the buttons' place (up to two lines).
        self._slot = QWidget()
        self._stack = QStackedLayout(self._slot)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._buttons)
        self._stack.addWidget(self._result)
        two_lines = math.ceil(2 * QFontMetricsF(self.result_label.font()).lineSpacing())
        self._slot.setFixedHeight(max(self._buttons.sizeHint().height(),
                                      self._SLOT_GAP + two_lines + self._PAD_BOTTOM))
        # Under the slot: the FAILED reason and the note (they only ever add height).
        self._extra = QWidget()
        extra = QVBoxLayout(self._extra)
        extra.setContentsMargins(0, 0, self._PAD_RIGHT, self._PAD_BOTTOM)
        extra.setSpacing(4)
        extra.addWidget(self.failure_label)
        extra.addWidget(self.note_label)
        self._extra.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._PAD_LEFT, self._PAD_TOP, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._texts)
        layout.addWidget(self._slot)
        layout.addWidget(self._extra)

    # ---- state -----------------------------------------------------------------

    @property
    def actionable(self) -> bool:
        return self._actionable

    def status(self) -> str:
        return self._status

    def link(self) -> str:
        return self._link

    def result_text(self) -> str:
        """The result as set (e.g. "ADDED", "FAILED: <REASON>"), never elided; "" while pending."""
        return self._result_text

    def title(self) -> str:
        """The whole title (the label may show it cut to ``title_lines``)."""
        return self._title

    def body(self) -> str:
        """The body preview as shown before any cut (line breaks folded into spaces)."""
        return self.body_label.full_text()

    def set_texts(self, kind: str, title: str, detail: str) -> None:
        self._title = title
        self.kind_label.set_full_text(kind.upper())
        if isinstance(self.title_label, _ClampedLabel):
            self.title_label.set_full_text(title)
        else:
            self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))

    def set_status(self, status: str, message: str = "", link: str = "", link_text: str = "") -> None:
        """Show ``status`` (one of CARD_STATUSES); ``message`` is the FAILED reason or a custom result.

        ``link_text`` names the result's link ("Open event"; "Open" by default).
        Only the content of the button slot changes, never the card's height,
        except that a FAILED reason (under the buttons) may add a line or two
        once; the card keeps that room afterwards.
        """
        self._status = status if status in CARD_STATUSES else CARD_PENDING
        self._link = link
        self.open_button.setText(link_text or "Open")
        text, color = _CARD_RESULTS.get(self._status, ("", TEXT_DIM))
        if self._status == CARD_FAILED and message:
            text = f"FAILED: {message}"
        elif message and self._status != CARD_PENDING:
            text = message
        text = text.upper()
        self._result_text = text
        failed = self._status == CARD_FAILED
        retry = self._status in (CARD_PENDING, CARD_FAILED)
        show_buttons = self._actionable and retry
        self.result_label.set_full_text("" if failed else text)
        set_label_color(self.result_label, color)
        self.open_button.setVisible(self._status in (CARD_ADDED, CARD_EXISTS) and bool(link))
        self.failure_label.set_line(text if failed else "")
        # A Tab-focused Deny / Approve that goes away would hand the keyboard
        # focus to the next card's Deny (and scroll the list to it), so a second
        # Space would decide that card: keep the focus on this card instead.
        focus = QApplication.focusWidget()
        if not show_buttons and focus is not None and self._buttons.isAncestorOf(focus):
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._stack.setCurrentWidget(self._buttons if show_buttons else self._result)
        self._sync_buttons()
        slot = self._actionable or bool(text)
        self._slot.setVisible(slot)
        self._texts.setContentsMargins(0, 0, self._PAD_RIGHT, 0 if slot else self._PAD_BOTTOM)
        if not retry:
            self.set_note("")
        self._sync_extra()
        name = f"{self.kind_label.full_text()}: {self._title}"
        self.setAccessibleName(f"{name}, {text}" if text else name)

    def set_note(self, text: str) -> None:
        """An amber note under the buttons (e.g. "Google Calendar is not set up yet...")."""
        self.note_label.set_line(text)
        self._sync_extra()

    # ---- lock -------------------------------------------------------------------

    def set_locked(self, locked: bool, tooltip: str = "") -> None:
        """Dim and disable Deny / Approve (``tooltip`` explains why); nothing moves or resizes."""
        locked = bool(locked)
        if locked == self._locked and (tooltip if locked else "") == self._lock_tip:
            return
        self._locked = locked
        self._lock_tip = tooltip if locked else ""
        self._sync_buttons()

    def is_locked(self) -> bool:
        return self._locked

    def _buttons_shown(self) -> bool:
        return self._actionable and self._status in (CARD_PENDING, CARD_FAILED)

    def _sync_buttons(self) -> None:
        enabled = self._buttons_shown() and not self._locked
        focus = QApplication.focusWidget()
        if not enabled and focus is not None and self._buttons.isAncestorOf(focus):
            # Qt would hand the focus to the next widget in the chain (another card's
            # button); keep it on this card instead, so a second Space decides nothing.
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        for button in (self.deny_button, self.approve_button):
            button.setEnabled(enabled)
            button.setToolTip(self._lock_tip)
            button.setAccessibleDescription(self._lock_tip)

    def _on_locked_click(self) -> None:
        if self._locked and self._buttons_shown():
            self.lockedClicked.emit(self.action_id)

    def note(self) -> str:
        return self.note_label.text()

    def _sync_extra(self) -> None:
        self._extra.setVisible(self.failure_label.used() or self.note_label.used())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            rect = QRectF(self.rect())
            strength = 1.0 if self._actionable else 0.5
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0.0, rgba(AMBER, 0.08 * strength))
            gradient.setColorAt(1.0, rgba(AMBER, 0.025 * strength))
            painter.fillRect(rect, gradient)
            # A hairline round the whole card, button row included, so the
            # buttons read as this card's and not the next one's.
            painter.setPen(QPen(rgba(AMBER, 0.22 * strength), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            painter.fillRect(QRectF(0, 0, 2, rect.height()), rgba(AMBER, 1.0 if self._actionable else 0.35))
        finally:
            painter.end()


class ActionList(QScrollArea):
    """Scrollable column of ActionCards with an empty-state line.

    Its size hint follows the cards' height up to ``max_height``, so a panel
    holding it shrinks to its content and scrolls beyond that. ``CARD_GAP``
    px of empty space separate the cards, so a card's buttons never sit
    right on top of the next card's title.
    """

    CARD_GAP = 14

    def __init__(self, empty_text: str = "Nothing needs your OK", *, max_height: int = 460,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        content = _transparent_scroll(self)
        self._max_height = max_height
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 0, 4, 0)   # keeps the cards' outline off the scroll bar
        self._layout.setSpacing(self.CARD_GAP)
        self.empty_label = make_label(empty_text, mono_font(11, 400, 0.08), TEXT_DIM)
        self._layout.addWidget(self.empty_label)
        self._layout.addStretch(1)
        self._cards: list[ActionCard] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_empty_text(self, text: str) -> None:
        self.empty_label.setText(text)

    def add_card(self, card: ActionCard) -> ActionCard:
        self._layout.insertWidget(len(self._cards), card)
        self._cards.append(card)
        self.empty_label.setVisible(False)
        self.updateGeometry()
        return card

    def cards(self) -> list[ActionCard]:
        return list(self._cards)

    def card(self, action_id: str) -> ActionCard | None:
        return next((card for card in self._cards if card.action_id == action_id), None)

    def clear(self) -> None:
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards = []
        self.empty_label.setVisible(True)
        self.updateGeometry()

    def _content_height(self, width: int) -> int:
        layout = self.widget().layout()
        if layout.hasHeightForWidth():
            return layout.totalHeightForWidth(width)
        return self.widget().sizeHint().height()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # The width without a scroll bar: if everything fits there, none is needed.
        width = max(self.width() - 2 * self.frameWidth(), 200)
        return QSize(width, min(self._max_height, self._content_height(width)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(120, min(self.sizeHint().height(), 120))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        old_width = event.oldSize().width()
        super().resizeEvent(event)
        if old_width != event.size().width():
            self.updateGeometry()


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------

TAG_RUN = "run"
TAG_DONE = "done"
TAG_WAIT = "wait"
TAG_STOP = "stop"
TAG_COLORS = {TAG_RUN: ACCENT, TAG_DONE: GREEN, TAG_WAIT: AMBER, TAG_STOP: RED}


class _ActivityRow(QWidget):
    """One log row. On the 24-hour clock the time and tag columns are 38 and 44 px wide.

    The 12-hour clock sizes them from the font instead: the time column fits
    the widest time ("12:59 PM", right-aligned so the colons and AM / PM line
    up) and the tag column the widest tag, which gives the message back most
    of the width the longer times take.
    """

    _TIME_WIDTH_24H = 38
    _TAG_WIDTH_24H = 44
    _PAD = 3          # after the widest time and the widest tag on the 12-hour clock

    def __init__(self, when: str, tag: str, message: str, sub: str, hour24: bool = False) -> None:
        super().__init__()
        font = mono_font(11)
        self.entry = (when, tag, message, sub)
        time_label = make_label(when, font, TEXT_TIME)
        tag_label = make_label(tag, font, TAG_COLORS.get(tag, TEXT_MUTED))
        if hour24:
            time_label.setFixedWidth(self._TIME_WIDTH_24H)
            tag_label.setFixedWidth(self._TAG_WIDTH_24H)
        else:
            time_label.setFixedWidth(math.ceil(_clock_width(font, False)) + self._PAD)
            time_label.setContentsMargins(0, 0, self._PAD, 0)
            time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            widest_tag = max(_text_advance(font, text) for text in (*TAG_COLORS, tag))
            tag_label.setFixedWidth(math.ceil(widest_tag) + self._PAD)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addWidget(make_label(message, font, TEXT_BODY, wrap=True))
        if sub:
            texts.addWidget(make_label(sub, font, TEXT_SUB, wrap=True))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(time_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(tag_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(texts, 1)
        self.setAccessibleName(f"{when} {tag} {message}" + (f", {sub}" if sub else ""))


class ActivityLog(QScrollArea):
    """Newest-first log rows "time  tag  message / sub"; keeps the last MAX_ENTRIES.

    Times read "1:04 PM", or "13:04" after ``set_hour24(True)``; the 12-hour
    time column is as wide as its widest time.
    """

    MAX_ENTRIES = 50

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        content = _transparent_scroll(self)
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._rows: list[_ActivityRow] = []
        self._hour24 = False

    def set_hour24(self, hour24: bool) -> None:
        """The clock of the entries added from now on (call it before the first one)."""
        self._hour24 = hour24

    def add(self, tag: str, message: str, sub: str = "", when: datetime | str | None = None) -> None:
        stamp = when if isinstance(when, str) else format_time(when or datetime.now(), hour24=self._hour24)
        row = _ActivityRow(stamp, tag, message, sub, self._hour24)
        self._layout.insertWidget(0, row)
        self._rows.insert(0, row)
        while len(self._rows) > self.MAX_ENTRIES:
            old = self._rows.pop()
            old.hide()
            old.deleteLater()

    def entries(self) -> list[tuple[str, str, str, str]]:
        """(time, tag, message, sub) newest first."""
        return [row.entry for row in self._rows]

    def clear(self) -> None:
        for row in self._rows:
            row.hide()
            row.deleteLater()
        self._rows = []


# --------------------------------------------------------------------------
# Window frame
# --------------------------------------------------------------------------

_RESIZE_ZONE = 6


class HudWindowFrame(QWidget):
    """Base for frameless HUD windows: chamfered translucent outline, painted ground, header.

    ``header`` is a HeaderBar whose close button calls ``close()`` (so a
    subclass's closeEvent keeps deciding what closing means). Put the content
    in ``body_layout``. ``set_glow_anchor(widget)`` centres the ground glow on
    that widget (e.g. the orb). ``set_resizable(True)`` lets the edges resize
    the window (the frame's margins are the grab zone).
    """

    def __init__(self, parent: QWidget | None = None, *, cut: int = 14, corners: int = CUT_DIAGONAL,
                 margins: tuple[int, int, int, int] = (10, 4, 10, 10), always_on_top: bool = True,
                 with_header: bool = True) -> None:
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._cut = cut
        self._corners = corners
        self._glow_anchor: weakref.ref[QWidget] | None = None   # weak: the anchor is our own child
        self._resizable = False
        self._resize_edges = Qt.Edge(0)
        self._resize_start: tuple[QPoint, QRect] | None = None
        self._cache: QPixmap | None = None
        self._cache_key: tuple = ()
        self.header: HeaderBar | None = HeaderBar() if with_header else None
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*margins)
        layout.setSpacing(0)
        if self.header is not None:
            layout.addWidget(self.header)
            self.header.closeRequested.connect(self.close)
        layout.addWidget(self.body, 1)

    # ---- configuration ------------------------------------------------------------

    def set_glow_anchor(self, widget: QWidget | None) -> None:
        self._glow_anchor = weakref.ref(widget) if widget is not None else None
        self.update()

    def set_resizable(self, resizable: bool) -> None:
        self._resizable = resizable
        if not resizable:
            self.unsetCursor()

    def outline_path(self) -> QPainterPath:
        """The window's chamfered outline in widget coordinates."""
        return chamfer_path(QRectF(self.rect()), self._cut, self._corners)

    # ---- painting -------------------------------------------------------------------

    def _glow_center(self) -> QPointF | None:
        anchor = self._glow_anchor() if self._glow_anchor is not None else None
        if anchor is None or not shiboken6.isValid(anchor) or not anchor.isVisible() or anchor.window() is not self:
            return None
        return QPointF(anchor.mapTo(self, anchor.rect().center()))

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        dpr = self.devicePixelRatioF()
        center = self._glow_center()
        anchor_key = None if center is None else (round(center.x()), round(center.y()))
        key = (self.width(), self.height(), dpr, anchor_key)
        if self._cache is None or key != self._cache_key:
            self._cache = self._render_ground(dpr, center)
            self._cache_key = key
        # Copy only the exposed part: the orb repaints its own small rect ~30 times a second.
        exposed = QRectF(event.rect())
        source = QRectF(exposed.x() * dpr, exposed.y() * dpr, exposed.width() * dpr, exposed.height() * dpr)
        painter = QPainter(self)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawPixmap(exposed, self._cache, source)
        finally:
            painter.end()

    def _render_ground(self, dpr: float, center: QPointF | None) -> QPixmap:
        size = self.size()
        image = QImage(max(1, round(size.width() * dpr)), max(1, round(size.height() * dpr)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        rect = QRectF(0, 0, size.width(), size.height())
        radii = (rect.width() * 0.528, rect.height() * 0.533) if center is not None else None
        painter = QPainter(image)
        try:
            paint_ground(painter, rect, center, radii)
            # Clear the cut-off corners (antialiased edges). Masking with DestinationIn would
            # not work: a composition mode only acts on the pixels the fill covers.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            outside = QPainterPath()
            outside.addRect(rect)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillPath(outside.subtracted(chamfer_path(rect, self._cut, self._corners)), QColor(0, 0, 0, 255))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(rgba(ACCENT, 0.28), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(chamfer_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._cut, self._corners))
        finally:
            painter.end()
        return QPixmap.fromImage(image)

    # ---- edge resizing ----------------------------------------------------------------

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if not self._resizable:
            return edges
        if pos.x() <= _RESIZE_ZONE:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - _RESIZE_ZONE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_ZONE:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - _RESIZE_ZONE:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edge) -> Qt.CursorShape | None:
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            falling = edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
            return Qt.CursorShape.SizeFDiagCursor if falling else Qt.CursorShape.SizeBDiagCursor
        if horizontal:
            return Qt.CursorShape.SizeHorCursor
        if vertical:
            return Qt.CursorShape.SizeVerCursor
        return None

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self._resize_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._manual_resize(event.globalPosition().toPoint())
            return
        cursor = self._cursor_for(self._edges_at(event.position().toPoint()))
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges:
            event.accept()
            handle = self.windowHandle()
            if handle is not None and handle.startSystemResize(edges):
                return
            self._resize_edges = edges
            self._resize_start = (event.globalPosition().toPoint(), self.geometry())
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._resize_start = None
        super().mouseReleaseEvent(event)

    def _manual_resize(self, global_pos: QPoint) -> None:
        start, geometry = self._resize_start
        delta = global_pos - start
        rect = QRect(geometry)
        minimum = self.minimumSize()
        if self._resize_edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - minimum.width()))
        if self._resize_edges & Qt.Edge.RightEdge:
            rect.setRight(max(rect.right() + delta.x(), rect.left() + minimum.width()))
        if self._resize_edges & Qt.Edge.TopEdge:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - minimum.height()))
        if self._resize_edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + minimum.height()))
        self.setGeometry(rect)
