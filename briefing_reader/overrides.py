"""Settings changed in the app: ``%LOCALAPPDATA%\\briefing-reader\\settings.json`` (no Qt; standard library only).

``{"version": 1, "values": {"voice.rate": "+10%", "display.clock": "24h"}}``: the values the owner changed in Settings
(Change on a row). config.load_config merges them into the parsed config.toml before the parsers run, so every value
goes through the same checks, and every process (the app, the hotkey agent, a standing run) sees the same values. The
order is config.toml < settings.json; ``.env``'s JARVIS_*_ENABLED stay as they are (no ``enabled`` is editable here).
Changes are used after a restart. config.toml itself is never written.

Public API
==========

``EDITABLE``
    ``"table.key" -> Editable(kind, ...)``: the comfort settings the app may change, with the parsers' own limits
    (config.py). Nothing that widens what leaves the PC, what is read, who receives, what runs on its own, or Windows'
    setup: never an ``enabled``, an account, a model, a key, a folder, a schedule or a network switch.
``coerce(key, value)``
    The value as config.toml would hold it after the parser's checks ("+10%", "24h", 15, True, "22:00"), or None when
    it is not usable for ``key`` (an unknown key, a wrong type, outside the limits).
``toml_value(key, doc)``
    What config.toml (the parsed ``doc``) gives ``key`` - its value as the parser would read it, else the code default.
``load(path)``, ``read(path, warn=)``, ``save(path, values)``
    The file's usable values (an unknown key or a bad value is left out with a warning naming the key only; a missing,
    corrupt, too big, newer or unreadable file counts as empty - whatever the error: load_config never raises over
    it); ``read`` also says whether the file was whole (False: it exists but could not be used as a whole); writing
    them atomically (temporary file + atomic.replace). False when the file could not be written (or, while the tests
    run, lies outside the temporary folder).
``apply(doc, values)``
    (merged doc, frozenset of the keys it changed, {key: config.toml's value}) for load_config.
``settings_file(data_dir)``, ``readable(path)``
    Where the file is; whether it may be read (while the tests run, only inside the temporary folder).
``SettingsStore(path)``
    The file for the Settings screen: ``values()``, ``update({key: value})``, ``reset(keys)``. The file is read afresh
    each time (a file deleted or edited while Jarvis runs is never written back from memory); a file that could not be
    used as a whole is kept as ``settings.json.bak`` before the first save replaces it (no save without that copy).

Logs hold the key names and counts only, never a value.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import atomic
from . import config as cfg
from .runstate import parse_time_of_day

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
BACKUP_FILE = "settings.json.bak"
VERSION = 1
MAX_BYTES = 64 * 1024                 # far more than every editable value; a bigger file is not this app's
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"    # desktop.backend.TEST_GUARD_ENV

KIND_BOOL, KIND_CHOICE, KIND_INT, KIND_PERCENT, KIND_TIME = "bool", "choice", "int", "percent", "time"
KINDS = (KIND_BOOL, KIND_CHOICE, KIND_INT, KIND_PERCENT, KIND_TIME)


@dataclass(frozen=True)
class Editable:
    """One editable setting: its kind and the parser's limits. ``choices`` for a choice; ``bools`` the choices that
    config.toml's true / false stand for (``[ui] motion = false`` is "off"); ``low`` .. ``high`` and ``step`` (the
    Settings stepper's) for a number or a percentage."""

    kind: str
    choices: tuple[str, ...] = ()
    bools: tuple[str, str] | None = None      # (for true, for false)
    low: int = 0
    high: int = 0
    step: int = 1


def _flag() -> Editable:
    return Editable(KIND_BOOL)


def _number(limits: tuple[int, int], step: int) -> Editable:
    return Editable(KIND_INT, low=limits[0], high=limits[1], step=step)


EDITABLE: dict[str, Editable] = {
    "voice.rate": Editable(KIND_PERCENT, low=cfg.RATE_RANGE[0], high=cfg.RATE_RANGE[1], step=5),
    "voice.volume": Editable(KIND_PERCENT, low=cfg.VOLUME_RANGE[0], high=cfg.VOLUME_RANGE[1], step=5),
    "voice.keep_warm_minutes": _number(cfg.KEEP_WARM_RANGE, 15),
    "assistant.speak": _flag(),
    "assistant.greet": _flag(),
    "assistant.greeting_context": _flag(),
    "assistant.speak_replies": _flag(),
    "assistant.speak_results": _flag(),
    "assistant.scheduled_prompt": _flag(),
    "display.clock": Editable(KIND_CHOICE, choices=(cfg.CLOCK_12H, cfg.CLOCK_24H)),
    "ui.motion": Editable(KIND_CHOICE, choices=cfg.MOTION_CHOICES, bools=(cfg.MOTION_LIVELY, cfg.MOTION_OFF)),
    "live.auto_open": Editable(KIND_CHOICE, choices=cfg.LIVE_OPEN_CHOICES,
                               bools=(cfg.LIVE_OPEN_TAB, cfg.LIVE_OPEN_OFF)),
    "productivity.quiet_from": Editable(KIND_TIME),
    "productivity.quiet_until": Editable(KIND_TIME),
    "productivity.prep_lead_minutes": _number(cfg.PRODUCTIVITY_RANGES["prep_lead_minutes"], 5),
    "instant.temperature": Editable(KIND_CHOICE, choices=cfg.INSTANT_TEMPERATURES),
    "talk.quiet_ms": _number(cfg.TALK_RANGES["quiet_ms"], 100),
    "talk.keep_warm_minutes": _number(cfg.TALK_RANGES["keep_warm_minutes"], 5),
}

# The tables' own settings, for a key's code default (what holds when config.toml does not set it).
_DEFAULTS: dict[str, type] = {"voice": cfg.VoiceConfig, "assistant": cfg.AssistantConfig, "display": cfg.DisplayConfig,
                              "ui": cfg.UiConfig, "live": cfg.LiveConfig, "productivity": cfg.ProductivityConfig,
                              "instant": cfg.InstantConfig, "talk": cfg.TalkConfig}
_KEY_SHAPE = re.compile(r"[a-z_]{1,24}\.[a-z_]{1,40}")
_PERCENT_RE = re.compile(r"([+-]?)(\d{1,4})%")


def split(key: str) -> tuple[str, str]:
    table, _dot, name = key.partition(".")
    return table, name


def default(key: str) -> Any:
    """The code default of an editable key (config.toml silent)."""
    table, name = split(key)
    return getattr(_DEFAULTS[table](), name)


def current(config: Any, key: str) -> Any:
    """The value the loaded ``config`` uses for ``key`` (None when it has no such setting)."""
    table, name = split(key)
    return getattr(getattr(config, table, None), name, None)


def shown_key(key: object) -> str:
    """A key as a log may name it: an editable one, else "an unknown entry" (a hand-edited file may hold anything)."""
    return key if isinstance(key, str) and (key in EDITABLE or _KEY_SHAPE.fullmatch(key)) else "an unknown entry"


def coerce(key: str, value: Any, *, clamp: bool = False) -> Any:
    """``value`` as the parser would leave it for ``key``, or None when it is not usable. ``clamp``: a number or a
    percentage outside the limits is brought inside them, as config.toml's parser does (settings.json's own values
    must be inside: the app writes nothing else)."""
    spec = EDITABLE.get(key)
    if spec is None:
        return None
    if spec.kind == KIND_BOOL:
        return value if isinstance(value, bool) else None
    if spec.kind == KIND_CHOICE:
        if isinstance(value, bool):
            return None if spec.bools is None else spec.bools[0 if value else 1]
        if not isinstance(value, str):
            return None
        text = value.strip().casefold()
        return next((option for option in spec.choices if option.casefold() == text), None)
    if spec.kind == KIND_INT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if isinstance(value, float) and (not clamp or not math.isfinite(value)):
            return None
        number = round(value)
        if number < 0:
            return None
        if clamp:
            return min(max(number, spec.low), spec.high)
        return number if spec.low <= number <= spec.high else None
    if spec.kind == KIND_PERCENT:
        match = _PERCENT_RE.fullmatch(value.strip()) if isinstance(value, str) else None
        if match is None:
            return None
        number = int(match.group(2)) * (-1 if match.group(1) == "-" else 1)
        if clamp:
            number = min(max(number, spec.low), spec.high)
        elif not spec.low <= number <= spec.high:
            return None
        return f"{number:+d}%"
    if spec.kind == KIND_TIME:
        try:
            return parse_time_of_day(value).strftime("%H:%M")
        except ValueError:
            return None
    return None


def toml_value(key: str, doc: Mapping[str, Any]) -> Any:
    """What config.toml (the parsed ``doc``, before any merge) gives ``key``: its value as the parser reads it, else
    the code default (missing or not usable)."""
    table, name = split(key)
    section = doc.get(table)
    raw = section.get(name) if isinstance(section, Mapping) else None
    value = coerce(key, raw, clamp=True) if raw is not None else None
    return default(key) if value is None else value


def settings_file(data_dir: Path | str) -> Path:
    return Path(data_dir) / SETTINGS_FILE


def readable(path: Path | str) -> bool:
    """While the tests run (the test guard on), only a file inside the temporary folder is read: never the owner's
    data folder, whatever a test's environment says."""
    if os.environ.get(TEST_GUARD_ENV, "") != "1":
        return True
    try:
        temp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
        where = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([temp, where]) == temp
    except (OSError, ValueError):
        return False


def load(path: Path | str) -> dict[str, Any]:
    """The usable values in ``path`` ({} when it is missing, unreadable, corrupt or of another version)."""
    return read(path)[0]


def read(path: Path | str, *, warn: bool = True) -> tuple[dict[str, Any], bool]:
    """(the usable values in ``path``, whether the file was whole): ({}, True) when it is missing; ({}, False) when it
    exists but could not be used as a whole (unreadable, too big, not JSON - whatever the error -, another shape or
    version). ``warn`` False: no warnings (the Settings screen reads it often; the start-up load warned)."""
    def problem(text: str, *args: Any) -> tuple[dict[str, Any], bool]:
        if warn:
            logger.warning("Settings: settings.json " + text + "; config.toml as it is", *args)
        return {}, False

    path = Path(path)
    if not readable(path):
        return {}, True
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_BYTES + 1)            # never more than this: a bigger file is not this app's
    except FileNotFoundError:
        return {}, True
    except Exception as exc:  # noqa: BLE001 - nothing about the file may stop a start
        return problem("could not be read (%s)", type(exc).__name__)
    if len(data) > MAX_BYTES:
        return problem("is too big to be this app's")
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001 - also RecursionError (deep nesting) and MemoryError
        return problem("is not readable JSON")
    if not isinstance(doc, dict) or not isinstance(doc.get("values"), dict):
        return problem("is not in the expected shape")
    if doc.get("version") != VERSION:
        return problem("is of another version")
    values: dict[str, Any] = {}
    for key, raw in doc["values"].items():
        if key not in EDITABLE:
            if warn:
                logger.warning("Settings: settings.json: %s is not a setting the app changes; ignored",
                               shown_key(key))
            continue
        try:
            value = coerce(key, raw)
        except Exception:  # noqa: BLE001 - an odd value is not usable, never an error
            value = None
        if value is None:
            if warn:
                logger.warning("Settings: settings.json value for %s is not usable; config.toml's value is used",
                               key)
            continue
        values[key] = value
    return values, True


def save(path: Path | str, values: Mapping[str, Any]) -> bool:
    """Write the usable ``values`` (editable keys only) atomically; False when the file could not be written (or, while
    the tests run, lies outside the temporary folder: the owner's data folder is never written by a test)."""
    path = Path(path)
    if not readable(path):
        logger.warning("Settings: settings.json is not written while the tests run outside the temporary folder")
        return False
    kept = {key: coerce(key, values[key]) for key in sorted(values) if key in EDITABLE}
    kept = {key: value for key, value in kept.items() if value is not None}
    text = json.dumps({"version": VERSION, "values": kept}, indent=2, sort_keys=True) + "\n"
    part: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, part = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        atomic.replace(part, path)
        part = None
        return True
    except OSError as exc:
        logger.warning("Settings: settings.json could not be saved (%s)", type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass


def apply(doc: Mapping[str, Any], values: Mapping[str, Any]) -> tuple[dict[str, Any], frozenset[str], dict[str, Any]]:
    """config.toml's parsed ``doc`` with ``values`` merged in (a copy: tables copied before a change), the keys merged
    and config.toml's own value of each. A table config.toml holds as something else than a table keeps its warning:
    the value is not merged then."""
    merged: dict[str, Any] = dict(doc)
    keys: list[str] = []
    base: dict[str, Any] = {}
    for key, value in values.items():
        if key not in EDITABLE or coerce(key, value) is None:
            continue
        table, name = split(key)
        section = merged.get(table, {})
        if not isinstance(section, dict):
            logger.warning("Settings: %s is not used: config.toml's [%s] is not a table", key, table)
            continue
        base[key] = toml_value(key, doc)
        merged[table] = {**section, name: coerce(key, value)}
        keys.append(key)
    return merged, frozenset(keys), base


def _backup(path: Path) -> bool:
    """Keep a file that could not be used as a whole as settings.json.bak (replacing an older copy); False when that
    failed (then nothing may replace the file)."""
    target = path.with_name(BACKUP_FILE)
    part: str | None = None
    try:
        data = path.read_bytes()
        fd, part = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        atomic.replace(part, target)
        part = None
    except FileNotFoundError:
        return True                                   # gone meanwhile: nothing to keep
    except OSError as exc:
        logger.warning("Settings: settings.json could not be kept as settings.json.bak (%s); not saved",
                       type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass
    logger.warning("Settings: settings.json could not be used, so it was kept as settings.json.bak")
    return True


class SettingsStore:
    """settings.json for the Settings screen (read afresh each time; a failed write changes nothing)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _current(self) -> dict[str, Any] | None:
        """The file's values now, or None when it could not be used as a whole and could not be kept aside."""
        values, whole = read(self.path, warn=False)
        if whole:
            return values
        return {} if _backup(self.path) else None

    def values(self) -> dict[str, Any]:
        with self._lock:
            return read(self.path, warn=False)[0]

    def update(self, changes: Mapping[str, Any]) -> bool:
        """Set every key of ``changes`` (each must be usable: ValueError otherwise, nothing written)."""
        cleaned: dict[str, Any] = {}
        for key, value in changes.items():
            usable = coerce(key, value)
            if usable is None:
                raise ValueError(f"{shown_key(key)} cannot take that value")
            cleaned[key] = usable
        if not cleaned:
            return True
        with self._lock:
            current = self._current()
            if current is None or not save(self.path, {**current, **cleaned}):
                return False
        for key in sorted(cleaned):
            logger.info("Settings: %s changed here", key)
        return True

    def reset(self, keys: Iterable[str]) -> bool:
        """Take ``keys`` out of the file: config.toml's values hold again after a restart."""
        with self._lock:
            values, whole = read(self.path, warn=False)
            gone = sorted(key for key in set(keys) if key in values)
            if not gone:
                return True                           # (a file not whole holds none of them: left as it is)
            left = {key: value for key, value in values.items() if key not in gone}
            if not save(self.path, left):
                return False
        for key in gone:
            logger.info("Settings: %s back to config.toml", key)
        return True
