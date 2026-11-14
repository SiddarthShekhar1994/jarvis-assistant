"""The board's own address scheme, jarvis-board: (the new look, part 3; PLAN section 3, DESIGN-SPEC section 8).

    register_scheme()      the scheme, known to the web engine: only before the QApplication (__main__ calls it on every
                           start, whatever the settings), idempotent, never raises. False: this PySide6 has no
                           QtWebEngine 6.8+, or a QApplication exists already (the board then can't start)
    scheme_ready()         it was registered in this process (looks only: never imports the web engine)
    BOARD_URL              the one page there is: jarvis-board://board/board.html
    FILES                  every name served, with its content type; nothing else is ever served
    board_file(url)        the name in FILES a request's address asks for ("" = refused)
    body(name)             (content type, bytes) of a name in FILES, read now: board.html, board.css and board.js from
                           this package's assets folder, tokens.css written now from holo's tokens, qwebchannel.js from
                           Qt's own resource (:/qtwebchannel/qwebchannel.js, never a copy in the repo), the fonts from
                           the repo's fonts folder; None when it can't be read
    tokens_css()           tokens.css: holo's colours, radii, easings, type roles and glyphs, and hud's typefaces as
                           @font-face rules on this scheme. Written at each request, so it never drifts from holo
    handler(parent)        the scheme's QWebEngineUrlSchemeHandler for one profile (GUI thread, once
                           snapshot_engine.ensure_engine() said ""): serve(job) for each request

The scheme has the Host syntax with one host, "board": the page's origin is jarvis-board://board, so its scripts, styles
and fonts are its own (no CORS), and its flags are agent.browser's jarvis-task's: Secure | Local | LocalAccessAllowed (a
web page can't reach it). A request is served only when it is a GET of exactly jarvis-board://board/<a name in FILES>,
with no user, port, query or fragment; anything else fails with UrlNotFound. The page's content security policy is the
<meta> in board.html (measured: a policy sent as a header by this handler is not honoured on a custom scheme).

Logging: counts only ("served" / "refused"), never an address.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QFile, QIODevice, QObject, QUrl

logger = logging.getLogger(__name__)

SCHEME = "jarvis-board"
SCHEME_BYTES = SCHEME.encode("ascii")
HOST = "board"
PAGE = "board.html"
BOARD_URL = f"{SCHEME}://{HOST}/{PAGE}"
QWEBCHANNEL_RESOURCE = ":/qtwebchannel/qwebchannel.js"
ASSETS = Path(__file__).resolve().parent / "assets"
FONTS_FOLDER = Path(__file__).resolve().parents[2] / "fonts"

_HTML = "text/html;charset=utf-8"
_CSS = "text/css;charset=utf-8"
_JS = "text/javascript;charset=utf-8"
_FONT = "font/ttf"
# The page's names for hud.FONT_FILES: the three typefaces (Chakra Petch in its two weights, Sora, JetBrains Mono).
FONTS = {
    "fonts/chakra-petch-500.ttf": "ChakraPetch-Medium.ttf",
    "fonts/chakra-petch-600.ttf": "ChakraPetch-SemiBold.ttf",
    "fonts/sora.ttf": "Sora[wght].ttf",
    "fonts/jetbrains-mono.ttf": "JetBrainsMono[wght].ttf",
}
FILES: dict[str, str] = {
    PAGE: _HTML, "board.css": _CSS, "board.js": _JS, "tokens.css": _CSS, "qwebchannel.js": _JS,
    **{name: _FONT for name in FONTS},
}
_PAGE_ASSETS = frozenset({PAGE, "board.css", "board.js"})


# --------------------------------------------------------------------------
# The scheme
# --------------------------------------------------------------------------

def register_scheme() -> bool:
    """jarvis-board:, known to the web engine. Only before the QApplication exists; idempotent; never raises."""
    try:
        if scheme_ready():
            return True
        from .. import snapshot_engine

        if QCoreApplication.instance() is not None or not snapshot_engine.webengine_available():
            return False
        from PySide6.QtWebEngineCore import QWebEngineUrlScheme

        scheme = QWebEngineUrlScheme(SCHEME_BYTES)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        flag = QWebEngineUrlScheme.Flag
        # Local: a web page can't reach it; local access: its page may load its own files (the interceptor and the page's
        # policy refuse every other local address, file: too).
        scheme.setFlags(flag.SecureScheme | flag.LocalScheme | flag.LocalAccessAllowed)
        QWebEngineUrlScheme.registerScheme(scheme)
        return scheme_ready()
    except Exception as exc:  # noqa: BLE001 - Jarvis works without it: the board says it can't start
        logger.info("Board: the scheme was not registered (%s)", type(exc).__name__)
        return False


def scheme_ready() -> bool:
    """The scheme was registered in this process (never imports the web engine for it)."""
    core = sys.modules.get("PySide6.QtWebEngineCore")
    if core is None:
        return False
    try:
        return bytes(core.QWebEngineUrlScheme.schemeByName(SCHEME_BYTES).name().data()) == SCHEME_BYTES
    except Exception:  # noqa: BLE001 - unknown: not ready
        return False


def board_file(url: QUrl | str) -> str:
    """The name in FILES that ``url`` asks for, or "" (not exactly jarvis-board://board/<name>)."""
    try:
        address = url if isinstance(url, QUrl) else QUrl(str(url))
        if (not address.isValid() or address.scheme().casefold() != SCHEME or address.host() != HOST
                or address.port() != -1 or address.userInfo() or address.hasQuery() or address.hasFragment()):
            return ""
        path = address.path(QUrl.ComponentFormattingOption.FullyEncoded)
        name = path[1:] if path.startswith("/") else ""
        return name if name in FILES else ""
    except Exception:  # noqa: BLE001 - unsure: refused
        return ""


# --------------------------------------------------------------------------
# What is served
# --------------------------------------------------------------------------

def _qwebchannel() -> bytes | None:
    """Qt's own qwebchannel.js, from the resource QtWebChannel carries (imported here so it is loaded)."""
    import PySide6.QtWebChannel  # noqa: F401 - registers the resource

    resource = QFile(QWEBCHANNEL_RESOURCE)
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    try:
        return bytes(resource.readAll().data())
    finally:
        resource.close()


def body(name: str) -> tuple[str, bytes] | None:
    """(content type, bytes) of ``name`` (a key of FILES), read now; None for anything else or a file that can't be
    read. Never raises."""
    try:
        kind = FILES.get(name) if isinstance(name, str) else None
        if kind is None:
            return None
        if name in _PAGE_ASSETS:
            data = (ASSETS / name).read_bytes()
        elif name == "tokens.css":
            data = tokens_css().encode("utf-8")
        elif name == "qwebchannel.js":
            data = _qwebchannel()
        else:
            data = (FONTS_FOLDER / FONTS[name]).read_bytes()
        return (kind, data) if data else None
    except Exception as exc:  # noqa: BLE001 - not served
        logger.debug("Board: a file was not served (%s)", type(exc).__name__)
        return None


def _colour(value: Any) -> str:
    from PySide6.QtGui import QColor

    colour = QColor(value)
    if colour.alpha() == 255:
        return colour.name()
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {round(colour.alphaF(), 3)})"


# holo's tokens as the page's custom properties (--tile-amber-lit, --line-faint, ...).
_COLOURS = ("VOID", "VOID_EDGE", "DOCK", "TILE", "TILE_FOCUS", "TILE_AMBER", "TILE_AMBER_LIT", "TILE_OK", "TILE_ALERT",
            "RAISED", "FIELD", "PICTURE", "FOCUS", "HOLO", "HOLO_HI", "HOLO_LO", "HOLO_INK", "INK_1", "INK_2", "INK_3",
            "INK_4", "INK_KICKER", "AMBER", "AMBER_HI", "AMBER_LO", "AMBER_INK", "AMBER_TX", "AMBER_TX2", "OK", "OK_INK",
            "ALERT", "ALERT_HI", "ALERT_LO", "ALERT_TX", "ALERT_TX2", "ALERT_INK", "STEEL", "GRID", "SCAN", "LINE_FAINT",
            "LINE", "LINE_STRONG", "LINE_FOCUS", "LINE_INPUT", "AMBER_LINE", "FRAME_EDGE", "CUE", "CUE_AMBER")
_RADII = (("radius-frame", "RADIUS_FRAME"), ("radius-amber", "RADIUS_TILE_AMBER"), ("radius-badge", "RADIUS_BADGE"),
          ("radius-button-sm", "RADIUS_BUTTON_SM"), ("bracket", "BRACKET_WIDTH"))
_EASINGS = (("ease-std", "EASE_STD"), ("ease-out", "EASE_OUT"), ("ease-in", "EASE_IN"), ("ease-back", "EASE_BACK"),
            ("ease-css", "CSS_EASE"), ("ease-in-out", "CSS_EASE_IN_OUT"))
# The faces: (page name, family attribute of hud, weight range).
_FACES = (("fonts/chakra-petch-500.ttf", "DISPLAY_FAMILY", "400 500"),
          ("fonts/chakra-petch-600.ttf", "DISPLAY_FAMILY", "600 700"),
          ("fonts/sora.ttf", "BODY_FAMILY", "100 800"),
          ("fonts/jetbrains-mono.ttf", "MONO_FAMILY", "100 800"))


def _number(value: float) -> str:
    return f"{value:g}"


def tokens_css() -> str:
    """tokens.css (see the module doc): every value comes from holo / hud as they are now."""
    from .. import holo, hud

    generic = {hud.DISPLAY_FAMILY: "sans-serif", hud.BODY_FAMILY: "sans-serif", hud.MONO_FAMILY: "monospace"}
    lines = ["/* Written by briefing_reader.board.scheme from holo's tokens at each request: do not edit. */"]
    for name, family_attr, weights in _FACES:
        lines.append(f'@font-face{{font-family:"{getattr(hud, family_attr)}";src:url("{SCHEME}://{HOST}/{name}") '
                     f'format("truetype");font-weight:{weights};font-style:normal;font-display:block}}')
    props = [f"--{name.lower().replace('_', '-')}:{_colour(getattr(holo, name))}" for name in _COLOURS]
    props.append(f"--alert-line:{_colour(holo.TILE_STYLES[holo.TILE_ERROR].border)}")
    props += [f"--{prop}:{_number(getattr(holo, attr))}px" for prop, attr in _RADII]
    props += [f"--{prop}:cubic-bezier({', '.join(_number(v) for v in getattr(holo, attr))})" for prop, attr in _EASINGS]
    for family, css in generic.items():
        key = {hud.DISPLAY_FAMILY: "display", hud.BODY_FAMILY: "body", hud.MONO_FAMILY: "mono"}[family]
        props.append(f'--font-{key}:"{family}",{css}')
    lines.append(":root{" + ";".join(props) + "}")
    for role_name, role in holo.ROLES.items():
        rule = (f"font:{role.weight} {role.px}px/{role.line}px \"{role.family}\",{generic.get(role.family, 'sans-serif')};"
                f"letter-spacing:{_number(role.tracking)}em")
        if role.caps:
            rule += ";text-transform:uppercase"
        if role.tabular:
            rule += ";font-variant-numeric:tabular-nums"
        lines.append(f".t-{role_name.replace('_', '-')}{{{rule}}}")
    for glyph, parts in holo.ICONS.items():
        lines.append(f'.g-{glyph.replace("_", "-")}{{d:path("{" ".join(parts)}")}}')
    for glyph, part in holo.FILLED_ICONS.items():
        lines.append(f'.g-{glyph.replace("_", "-")}{{d:path("{part}")}}')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The handler
# --------------------------------------------------------------------------

def serve(job: Any, counts: Counter[str] | None = None) -> None:
    """One request of the page: the file it names when it is a GET of a name in FILES, read now; else UrlNotFound.
    Never raises (a request that went wrong is refused)."""
    tally = counts if counts is not None else Counter()
    try:
        method = bytes(job.requestMethod().data()).upper()
        name = board_file(job.requestUrl()) if method == b"GET" else ""
        item = body(name) if name else None
        if item is None:
            tally["refused"] += 1
            job.fail(type(job).Error.UrlNotFound)
            return
        kind, data = item
        buffer = QBuffer(job)          # deleted with the request
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(kind.encode("ascii"), buffer)
        tally["served"] += 1
    except Exception as exc:  # noqa: BLE001 - never raise into Chromium: refused
        tally["refused"] += 1
        logger.debug("Board: a request was not served (%s)", type(exc).__name__)
        try:
            job.fail(type(job).Error.UrlNotFound)
        except Exception:  # noqa: BLE001 - the request is gone already
            pass


_HANDLER: list[Any] = []


def handler(parent: QObject) -> Any:
    """The scheme's handler for one profile (made on first use: nothing here imports QtWebEngine at import). Its
    ``counts`` say how many requests were served and refused."""
    if not _HANDLER:
        from PySide6.QtWebEngineCore import QWebEngineUrlSchemeHandler

        class Handler(QWebEngineUrlSchemeHandler):
            """Serves the board's FILES and nothing else (serve)."""

            def __init__(self, owner: QObject) -> None:
                super().__init__(owner)
                self.counts: Counter[str] = Counter()

            def requestStarted(self, job: Any) -> None:  # noqa: N802 - Qt's name
                serve(job, self.counts)

        _HANDLER.append(Handler)
    return _HANDLER[0](parent)


__all__ = ["BOARD_URL", "FILES", "FONTS", "HOST", "QWEBCHANNEL_RESOURCE", "SCHEME", "SCHEME_BYTES", "board_file", "body",
           "handler", "register_scheme", "scheme_ready", "serve", "tokens_css"]
