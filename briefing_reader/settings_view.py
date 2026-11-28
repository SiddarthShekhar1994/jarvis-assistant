"""The Settings screen in the hologram look (HOLOGRAM_SPEC 2-7, 15, 16.8; round 2, part 2b; Smaller items, parts 4-5).

Public API
==========

``SettingsStage``
    Left: the SETTINGS head and the group list (``GROUPS``: Voice, Briefing times, Ask, Accounts, Quiet
    hours, LIVE and privacy; a click, or Up / Down on the list, opens a group: ``select_group(key)``,
    ``current_group()``). Right, the focal panel: the open group's name, its settings as they are now in
    plain words (``set_config(config)``: the config Jarvis loaded, never a file read here; ``rows()``), a
    line on what it covers, how to change them (``text_label``: Change for the rows that have it, else the
    config.toml tables; either way a restart), ``config_path`` with Open config.toml (``config_button``,
    ``openConfig``) and ``data_path`` with Open the data folder (``data_button``, ``openDataFolder``;
    ``set_paths(config_path, data_dir)``). Accounts show only their names and whether they are connected
    (``set_connected({alias: True | False})``, from the app's own sign-in check; "Not checked yet" before it); an
    Outlook account's row says "Outlook - ..." and its Connect / Disconnect is outlook_ui's part under the rows.
    Ask also says whether the simpler model on this PC answers when Claude can't, and whether it is installed and
    its size (local_llm.settings_rows: file reads only; it is put in by hand, so there is no Download).
    LIVE and privacy also says whether other AI apps may read the board (``MCP_LABEL``: [mcp] enabled, set in
    config.toml only - it widens who reads what Jarvis keeps, so it never has a Change), and has "Sites Jarvis may use
    signed in" as its own part under the rows (site_signins_ui.SitesPanel: kept in site_signins.json, never in
    settings.json, whose overrides never widen what is read or reached).

``SettingsStage.set_store(store)``, ``EDIT_ROWS``, ``edit_keys(config, group, label)``
    Changing a comfort setting here (Smaller items, part 4): with the app's ``overrides.SettingsStore`` (settings.json
    in the data folder), a row of ``EDIT_ROWS`` (or of a feature group's ``edits``) has Change (``change_buttons``):
    it opens ``editor`` (settings_edit.RowEditor) under the row - Save writes the value to settings.json, Cancel
    writes nothing, and the keyboard goes back to the row's Change. A row changed here says so under its value
    (``row_note(label)``: "Changed here", or "Changed here to <value> - used after a restart") and has Back to
    config.toml (``back_buttons``), which takes the value out of settings.json. The row's value is the one in use now;
    a change is used after a restart (no Restart button). config.toml is never written. Without a store every row is
    read only.

``SettingsStage.add_group(key, title, note=, how=, rows=, extra=, edits=)``
    A group a feature adds while it is on (Meetings, with [meetings] on): its rows come from ``rows()``, its
    how-to line is ``how`` and ``extra`` (a widget of the feature's own, e.g. the device choice and Download)
    shows under its rows; ``edits`` ({row label: keys}) gives rows a Change (``INSTANT_EDITS``, ``TALK_EDITS``).

``SettingsStage.attach_extra(key, widget)``
    A built-in group's own part (Voice: the local voice's Download and add-ons,
    local_voice_ui.VoiceSettingsPanel), shown only while that group is open, under its rows. The rows and these parts
    scroll inside the panel (``scroll``) on a short window; the how-to lines, Open config.toml and Open the data folder
    sit at the panel's foot, outside the scrolling, so they stay in the window on every group (900 x 600 too).

``settings_rows(config, group, connected)``
    The (label, value) pairs a group shows (used by the panel and the tests).

``desktop_rows(config, turned_on=False, current=True)``, ``DESKTOP_GROUP`` / ``DESKTOP_TITLE`` / ``DESKTOP_NOTE`` /
``DESKTOP_HOW``
    Settings > Desktop, added (``add_group``) only while [desktop] is on: its values and whether the owner turned it
    on on its first-run card ("Your OK": "Turned on", or "Not turned on yet"); its own part under the rows
    (desktop_view.DesktopSettingsPanel) has Turn off, which takes the OK back - the only thing it changes.

``effective_hud_key(config)``
    The HUD key that can work: [desktop] hud_key, or "" while the keyboard shortcut ([hotkey] enabled) is off (no
    key can be registered then). The Dock's "<key> switches" line and Settings > Desktop show only this one.

Nothing here writes config.toml or .env (only settings.json, through the store, for ``overrides.EDITABLE``'s keys),
and no secret, token, client id, page id, file path of a sign-in or e-mail address is ever shown (Accounts: the alias
and connected yes / no only).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import shiboken6
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import holo, hud, overrides, settings_edit
from .memory_view import COLUMN_MAX, PANEL_PAD, PanelFrame, Rule, SectionHead, body_label

logger = logging.getLogger(__name__)

SETTINGS_TITLE = "SETTINGS"
GROUPS: tuple[tuple[str, str], ...] = (("voice", "Voice"), ("times", "Briefing times"), ("ask", "Ask"),
                                       ("accounts", "Accounts"), ("quiet", "Quiet hours"),
                                       ("live", "LIVE and privacy"))
GROUP_TABLES: dict[str, tuple[str, ...]] = {
    "voice": ("voice", "assistant"), "times": ("schedule", "polling", "display", "ui"),
    "ask": ("ask", "research", "local_model", "hotkey", "actions"), "accounts": ("accounts",), "quiet": ("productivity",),
    "live": ("live", "productivity", "mcp"),
}
# Other AI apps reading the board (mcp_server; [mcp] enabled in config.toml only: it widens who reads what Jarvis keeps,
# so it is never a Change here).
MCP_LABEL = "Other AI apps (MCP)"
MCP_ON = ("On - an AI app you set up can read your waiting cards, to-dos, captures, documents, agents' reports and "
          "the last day's conversation; it can't send, approve or change anything")
MCP_OFF = "Off - no other app can read the board"
GROUP_NOTES: dict[str, str] = {
    "voice": "How Jarvis sounds and when he speaks.",
    "times": "The scheduled tasks keep their own times; these are used when no task gives one.",
    "ask": "Ask Jarvis uses your own signed-in Claude Code.",
    "accounts": "Each account's sign-in stays on this PC.",
    "quiet": "Quiet hours keep offers silent.",
    "live": "What LIVE shows stays in memory and is never saved.",
}
HOW_TEXT = "To change these, edit {tables} in config.toml, then restart Jarvis."
HOW_EDIT_TEXT = ("Change sets a row on this PC (settings.json in the data folder, which wins over config.toml); it is "
                 "used after you restart Jarvis. For the rest, edit {tables} in config.toml, then restart Jarvis.")
CHANGE_TEXT = "Change"
BACK_TEXT = "Back to config.toml"
CHANGED_HERE = "Changed here"
CHANGED_RESTART = "Changed here to {words} - used after a restart"
BACK_RESTART = "Back to config.toml ({words}) after a restart"
SAVE_FAILED = "That could not be saved on this PC; nothing changed."
# The rows Change can set (Decision 6's comfort settings, overrides.EDITABLE): {group: {row label: keys}}.
EDIT_ROWS: dict[str, dict[str, tuple[str, ...]]] = {
    "voice": {"Speed": ("voice.rate",), "Volume": ("voice.volume",), "Speaks on his own": ("assistant.speak",),
              "Speaks his answers": ("assistant.speak_replies",),
              "Says what happened after an approval": ("assistant.speak_results",),
              "Greets you when opened": ("assistant.greet", "assistant.greeting_context"),
              "Voice kept loaded": ("voice.keep_warm_minutes",),
              "Quiet during calls": ("voice.quiet_in_calls",)},
    "times": {"When a briefing arrives": ("assistant.scheduled_prompt",), "Clock on screen": ("display.clock",),
              "Screen motion": ("ui.motion",)},
    "quiet": {"Quiet hours": ("productivity.quiet_from", "productivity.quiet_until"),
              "Brief before a meeting": ("productivity.prep_lead_minutes",)},
    "live": {"Opens": ("live.auto_open",)},
}
INSTANT_EDITS: dict[str, tuple[str, ...]] = {"Temperature": ("instant.temperature",)}
TALK_EDITS: dict[str, tuple[str, ...]] = {"Quiet that ends a talk": ("talk.quiet_ms",),
                                          "Model kept loaded": ("talk.keep_warm_minutes",),
                                          "Live captions": ("talk.live_captions",)}
DATA_TEXT = "What Jarvis keeps on this PC (memory, decisions, voice cache, logs) is in the data folder."
OPEN_CONFIG_TEXT = "Open config.toml"
OPEN_DATA_TEXT = "Open the data folder"
NOT_CHECKED = "Not checked yet"
LIST_WIDTH = 220
REVEAL_HOLD_MS = 1000            # reveal(): its row is kept in view while the group settles
_LIVE_OPENS = {"tab": "On the Live screen", "window": "In its own window", "off": "Only when you open it"}
_MOTION_WORDS = {holo.MOTION_LIVELY: "Lively - screens sweep and assemble, the orb assembles at start-up",
                 holo.MOTION_CALM: "Calm - slower, gentle fades only",
                 holo.MOTION_OFF: "Off - no entrance or transition effects"}
MOTION_WINDOWS_OFF = "Off - Windows' animations are turned off"
DESKTOP_GROUP = "desktop"
DESKTOP_TITLE = "Desktop"
DESKTOP_NOTE = "Jarvis works on your desktop only when you ask, in the folders and apps you allow."
DESKTOP_HOW = ("Jarvis on your desktop is switched on or off with [desktop] in config.toml (or JARVIS_DESKTOP_ENABLED "
               "in .env), then a restart. The one thing you can change here is your OK: Turn off takes it back.")
NOT_TURNED_ON = "Not turned on yet"
TURNED_ON = "Turned on"
TURNED_ON_PHASE_1 = "Turned on for opening things - the updated card waits for your OK"   # p2r-6: Phase 1's card
TURNED_ON_NO_TASKS = "Turned on - tasks wait for your OK on the updated card"              # the agent's row
NO_HUD_KEY_SHORTCUT_OFF = "No key while the keyboard shortcut is off - the Dock's Desktop switches"
_DESKTOP_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
                    "music": "Music", "videos": "Videos"}


def motion_words(config: Any) -> str:
    """``[ui] motion`` in words; "off" whenever Windows' "Show animations in Windows" is off."""
    level = str(getattr(getattr(config, "ui", None), "motion", holo.MOTION_LIVELY) or holo.MOTION_LIVELY)
    if holo.reduced_motion() and level != holo.MOTION_OFF:
        return MOTION_WINDOWS_OFF
    return _MOTION_WORDS.get(level, _MOTION_WORDS[holo.MOTION_LIVELY])


def _yes(value: Any) -> str:
    return "Yes" if value else "No"


def _on(value: Any) -> str:
    return "On" if value else "Off"


def _percent(value: Any, more: str, less: str) -> str:
    """"+0%" -> "Normal", "+10%" -> "10% faster", "-5%" -> "5% slower"."""
    text = str(value or "").strip()
    try:
        number = int(text.rstrip("%"))
    except ValueError:
        return text or "Normal"
    if number == 0:
        return "Normal"
    return f"{abs(number)}% {more if number > 0 else less}"


def _clock(value: Any, hour24: bool) -> str:
    """"22:00" -> "10:00 PM" (or "22:00" with the 24-hour clock); anything else as it is."""
    text = str(value or "").strip()
    hours, _colon, minutes = text.partition(":")
    if not (hours.isdigit() and minutes.isdigit()):
        return text
    hour, minute = int(hours), int(minutes)
    if hour24:
        return f"{hour:02d}:{minute:02d}"
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _combo(value: Any) -> str:
    """"ctrl+alt+j" -> "Ctrl+Alt+J"."""
    return "+".join(part[:1].upper() + part[1:] for part in str(value or "").split("+") if part)


def _every(seconds: Any) -> str:
    try:
        count = int(seconds)
    except (TypeError, ValueError):
        return "now and then"
    if count % 60 == 0:
        minutes = count // 60
        return "every minute" if minutes == 1 else f"every {minutes} minutes"
    return f"every {count} seconds"


def settings_rows(config: Any, group: str, connected: Mapping[str, bool] | None = None) -> list[tuple[str, str]]:
    """What ``group`` shows: (label, value) pairs from the loaded ``config``, in plain words. Never a secret,
    a token, a client id, a page id, a sign-in file or an e-mail address."""
    if config is None:
        return []
    hour24 = bool(getattr(getattr(config, "display", None), "hour24", False))
    assistant = getattr(config, "assistant", None)
    prod = getattr(config, "productivity", None)
    prod_on = bool(getattr(prod, "enabled", False))
    if group == "voice":
        voice = getattr(config, "voice", None)
        offline = str(getattr(voice, "offline_voice", "") or "")
        address = str(getattr(assistant, "address", "") or "")
        local = getattr(voice, "engine", "") == "local"
        windows = f"Windows voice: {offline}" if offline else "Windows' default voice"
        if local:   # what that means for private lines is said under the rows (local_voice_ui.VoiceSettingsPanel)
            from .local_voice import voice_label

            named = f"{voice_label(str(getattr(voice, 'local_voice', '') or 'bm_george'))}, made on this PC"
        else:
            named = f"{str(getattr(voice, 'voice', '') or 'Default')}, online (Microsoft)"
        rows = [("Voice", named),
                ("Speed", _percent(getattr(voice, "rate", ""), "faster", "slower")),
                ("Volume", _percent(getattr(voice, "volume", ""), "louder", "quieter")),
                ("When that voice can't run" if local else "When that voice is out of reach", windows),
                ("Speaks on his own", "Yes" if getattr(assistant, "speak", True) else "No - text only"),
                ("Speaks his answers", _yes(getattr(assistant, "speak_replies", True))),
                ("Says what happened after an approval", _yes(getattr(assistant, "speak_results", True))),
                ("Greets you when opened", settings_edit.row_words(settings_edit.GREET_KEYS, {
                    "assistant.greet": getattr(assistant, "greet", True),
                    "assistant.greeting_context": getattr(assistant, "greeting_context", True)}))]
        if local:
            minutes = int(getattr(voice, "keep_warm_minutes", 0) or 0)
            rows.append(("Voice kept loaded", settings_edit.value_words("voice.keep_warm_minutes", minutes)))
        rows.append(("Quiet during calls", settings_edit.value_words("voice.quiet_in_calls",
                                                                     bool(getattr(voice, "quiet_in_calls", True)))))
        rows.append(("Calls you", f'"{address}"' if address else "Nothing"))
        return rows
    if group == "times":
        schedule = getattr(config, "schedule", None)
        polling = getattr(config, "polling", None)
        return [("Morning briefing", _clock(getattr(schedule, "am", ""), hour24)),
                ("Evening briefing", _clock(getattr(schedule, "pm", ""), hour24)),
                ("Waits for a late briefing", f"Up to {getattr(polling, 'timeout_minutes', 15)} minutes, "
                 f"checking {_every(getattr(polling, 'interval_seconds', 60))}"),
                ("When a briefing arrives", 'Asks "Hear it now?" first' if getattr(assistant, "scheduled_prompt",
                                                                                 False) else "Says it is ready"),
                ("Clock on screen", "24-hour (13:05)" if hour24 else "12-hour (1:05 PM)"),
                ("Screen motion", motion_words(config))]
    if group == "ask":
        ask = getattr(config, "ask", None)
        research = getattr(config, "research", None)
        hotkey = getattr(config, "hotkey", None)
        actions = getattr(config, "actions", None)
        enabled = bool(getattr(ask, "enabled", False))
        research_on = enabled and bool(getattr(research, "enabled", False))
        research_words = ("On" if getattr(research, "planner_may_ask", True) else "Only when you ask for it") \
            if research_on else "Off"
        from . import local_llm

        rows = [("Ask Jarvis", _on(enabled)),
                ("Model", str(getattr(ask, "model", "") or "")),
                ("Reads the mail a request is about", _yes(getattr(ask, "read_mail", True))),
                ("Web research", research_words),
                ("Requests", f"Up to {getattr(ask, 'max_per_hour', 20)} an hour and "
                 f"{getattr(ask, 'max_per_day', 60)} a day"),
                ("Keyboard shortcut", _combo(getattr(hotkey, "combo", "")) if getattr(hotkey, "enabled", False)
                 else "Off"),
                ("Undo after Send or Cancel event", f"{getattr(actions, 'undo_seconds', 5)} seconds"),
                ("Undo after a calendar change", "10 minutes")]
        # Answers on this PC when Claude can't (local_llm): whether the model is there and its size (file reads only).
        return rows + local_llm.settings_rows(config)
    if group == "accounts":
        connected = connected or {}
        found = getattr(config, "accounts", {}) or {}
        accounts = dict(found) if isinstance(found, Mapping) else {alias: None for alias in found}
        words = {True: "Connected", False: "Not connected"}
        # An Outlook account (backend "microsoft") says so (its Connect is in the part under the rows).
        outlook = {True: "Outlook - connected", False: "Outlook - not connected"}
        return [(str(alias), (outlook if getattr(account, "backend", "") == "microsoft" else words).get(
            connected.get(alias), NOT_CHECKED)) for alias, account in accounts.items()]
    if group == "quiet":
        if not prod_on:
            return [("Quiet hours", "Not used - offers are off on this PC")]
        rows = [("Quiet hours", f"{_clock(getattr(prod, 'quiet_from', ''), hour24)} to "
                                f"{_clock(getattr(prod, 'quiet_until', ''), hour24)}"),
                ("Then", "Offers show as text only; nothing is said out loud")]
        if getattr(prod, "prep_offers", False):
            rows.append(("Brief before a meeting", "Offered " + settings_edit.value_words(
                "productivity.prep_lead_minutes", int(getattr(prod, "prep_lead_minutes", 15) or 15))))
        return rows
    if group == "live":
        live = getattr(config, "live", None)
        live_on = bool(getattr(live, "enabled", True))
        rows = [("LIVE", "On - every step on screen, never saved" if live_on else "Off")]
        if live_on:
            rows += [("Opens", _LIVE_OPENS.get(str(getattr(live, "auto_open", "tab")), "On the Live screen")),
                     ("Exact texts", "Shown on screen" if getattr(live, "text", True) else "Their sizes only"),
                     ("Pictures", "Drawn on this PC" if getattr(live, "pictures", True) else "Off"),
                     ("Pictures of web pages", "On - the only part that uses the network"
                      if getattr(live, "page_snapshots", True) else "Off")]
        memory_on = prod_on and bool(getattr(prod, "memory", False))
        rows.append(("Memory", "On - kept only on this PC, never given to web research" if memory_on else "Off"))
        if memory_on:
            rows.append(("Memory suggestions", "On - nothing is kept until you press Keep"
                         if getattr(prod, "memory_suggestions", False) else "Off"))
        rows.append(("Follow-ups", "On - message headers only" if prod_on and getattr(prod, "followups", False)
                     else "Off"))
        rows.append((MCP_LABEL, MCP_ON if getattr(getattr(config, "mcp", None), "enabled", False) is True
                     else MCP_OFF))
        return rows
    return []


def edit_keys(config: Any, group: str, label: str) -> tuple[str, ...]:
    """The settings Change sets on ``group``'s row ``label`` (``EDIT_ROWS``), or () - read only. Quiet hours and the
    meeting brief only while the productivity bundle runs - it is on and so is Ask (else they do nothing)."""
    keys = EDIT_ROWS.get(group, {}).get(label, ())
    if group == "quiet" and not (getattr(getattr(config, "productivity", None), "enabled", False)
                                 and getattr(getattr(config, "ask", None), "enabled", False)):
        return ()
    return tuple(key for key in keys if key in overrides.EDITABLE)


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def effective_hud_key(config: Any) -> str:
    """The HUD key that can work ("ctrl+alt+h"; "" for none): [desktop] hud_key, but none while the keyboard
    shortcut ([hotkey] enabled) is off - the hotkey agent registers both keys and does not run then (SPEC 9, D1).
    What the Dock's "<key> switches" line and Settings > Desktop show."""
    if not getattr(getattr(config, "hotkey", None), "enabled", False):
        return ""
    return str(getattr(getattr(config, "desktop", None), "hud_key", "") or "")


def desktop_rows(config: Any, turned_on: bool = False, current: bool = True,
                 tasks: bool = True) -> list[tuple[str, str]]:
    """Settings > Desktop: [desktop] as loaded, in plain words, and whether the owner turned it on (its first-run
    card; ``current``: today's card - not yet, the OK of Phase 1's covers opening things only; ``tasks`` False: tasks
    are on, and that card did not list them). Read only, like every group."""
    desktop = getattr(config, "desktop", None)
    if desktop is None:
        return []
    folders = [_DESKTOP_FOLDERS.get(str(name), str(name)) for name in getattr(desktop, "folders", ())]
    extra = len(getattr(desktop, "extra_folders", ()) or ())
    parts = [_and(folders)] if folders else []
    if extra:
        parts.append(f"{extra} {'folder' if extra == 1 else 'folders'} of your own")
    where = ", plus ".join(parts) or "None"
    hud_key = effective_hud_key(config)
    if hud_key:
        key_words = f"{_combo(hud_key)} - switches between the window and the HUD"
    elif getattr(desktop, "hud_key", ""):
        key_words = NO_HUD_KEY_SHORTCUT_OFF
    else:
        key_words = "None - the Dock's Desktop switches"
    days = int(getattr(desktop, "undo_days", 7) or 7)
    shots = str(getattr(desktop, "screenshots_dir", "") or "")
    ok = NOT_TURNED_ON
    if turned_on:
        ok = (TURNED_ON if tasks else TURNED_ON_NO_TASKS) if current else TURNED_ON_PHASE_1
    return [("Your OK", ok),
            ("Folders Jarvis may look in", where),
            ("HUD key", key_words),
            ("Left out of screenshots and screen sharing", _yes(getattr(desktop, "hide_from_capture", True))),
            ("Screenshots go to", shots or "Jarvis\\Screenshots in your user folder"),
            ("Undo", f"Offered for {days} {'day' if days == 1 else 'days'}"),
            ("Work inside apps", "On - only in the apps you allow" if getattr(desktop, "apps", False) else "Off")]


def _tables(group: str) -> str:
    names = [f"[{name}]" for name in GROUP_TABLES.get(group, ())]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class _FitScroll(QScrollArea):
    """The open group's rows: as tall as they are (the panel then fits its group), shrinking to scroll when the
    window is short, so the panel's foot (Open config.toml) stays in view."""

    MIN_ROWS_HEIGHT = 96

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width = -1

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        base = super().sizeHint()
        inner = self.widget()
        if inner is None:
            return base
        width = max(self.viewport().width(), 240)
        height = inner.heightForWidth(width) if inner.hasHeightForWidth() else -1
        if height < 0:
            height = inner.sizeHint().height()
        return QSize(base.width(), height + 2 * self.frameWidth())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().minimumSizeHint().width(), self.MIN_ROWS_HEIGHT)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self.viewport().width() != self._width:       # wrapped rows change height with the width
            self._width = self.viewport().width()
            self.updateGeometry()


class GroupButton(QAbstractButton):
    """One group in the list: 40 px, radius 8; the open one on ``tile-focus`` with a 3 px ``holo`` bar."""

    HEIGHT = 40

    def __init__(self, key: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAccessibleName(f"{text} settings")
        self._focus_ring = False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = QFontMetricsF(holo.font("button", 500)).horizontalAdvance(self.text())
        return QSize(int(width) + 40, self.HEIGHT)

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._focus_ring = holo.keyboard_focus(event.reason(), self._focus_ring)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.reason() != Qt.FocusReason.ActiveWindowFocusReason:
            self._focus_ring = False
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
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(3.5, 2.5, -3.5, -2.5)
            on = self.isChecked()
            if holo.canvas_look():                # part 4: a lit square row, the canvas frame's edge
                if on:
                    holo.paint_canvas_frame(painter, rect, holo.CANVAS_SELECTED)
                elif self.underMouse():
                    painter.fillRect(rect, holo.alpha(holo.HOLO, 0.06))
            elif on:
                painter.setPen(QPen(holo.LINE, 1.0))
                painter.setBrush(QColor(holo.TILE_FOCUS))
                painter.drawRoundedRect(rect, holo.RADIUS_BUTTON, holo.RADIUS_BUTTON)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(holo.HOLO))
                painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 9, 3, rect.height() - 18), 1.5, 1.5)
            elif self.underMouse():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(holo.alpha(holo.INK_1, 0.05))
                painter.drawRoundedRect(rect, holo.RADIUS_BUTTON, holo.RADIUS_BUTTON)
            painter.setFont(holo.font("button", 500))
            painter.setPen(QColor(holo.INK_1 if on else holo.INK_2))
            painter.drawText(rect.adjusted(14, 0, -8, 0),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
            if self.hasFocus() and self._focus_ring:
                holo.paint_focus_ring(painter, rect, holo.RADIUS_BUTTON)
        finally:
            painter.end()


class SettingsStage(QWidget):
    """The Settings screen; see the module docstring."""

    openConfig = Signal()
    openDataFolder = Signal()
    groupChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config: Any = None
        self._connected: dict[str, bool] = {}
        self._group = GROUPS[0][0]
        self._groups: list[tuple[str, str]] = list(GROUPS)
        self._added: dict[str, tuple[str, str, Callable[[], list[tuple[str, str]]], QWidget | None]] = {}
        self._added_edits: dict[str, dict[str, tuple[str, ...]]] = {}
        self._store: Any = None                       # overrides.SettingsStore (set_store); None: read only
        self._editing = ""                            # the row whose editor is open
        self.editor: settings_edit.RowEditor | None = None
        self.change_buttons: dict[str, settings_edit.RowLink] = {}
        self.back_buttons: dict[str, settings_edit.RowLink] = {}
        self._notes: dict[str, str] = {}
        self._snapshot: dict[str, Any] | None = None      # settings.json as read for the fill under way
        self._revealing: tuple[QWidget, QWidget | None] | None = None   # reveal()'s row, kept in view a moment
        self._reveal_seq = 0
        self._extras: dict[str, QWidget] = {}         # a built-in group's own part (attach_extra)
        self.head = SectionHead("settings", SETTINGS_TITLE)
        self.group_buttons: dict[str, GroupButton] = {}
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(2)
        side.addWidget(self.head)
        side.addSpacing(12)
        for key, text in GROUPS:
            button = GroupButton(key, text)
            button.clicked.connect(lambda _checked=False, which=key: self.select_group(which))
            button.installEventFilter(self)
            self.group_buttons[key] = button
            side.addWidget(button)
        self._side_layout = side
        side.addStretch(1)
        self._side = QWidget()
        self._side.setLayout(side)
        self._side.setFixedWidth(LIST_WIDTH)
        # The open group (the focal panel): its rows (and a feature's own part) scroll in the panel on a short window;
        # the how-to lines with Open config.toml and Open the data folder stay at its foot, always in view (900 x 600).
        self.scroll = _FitScroll()
        content = hud._transparent_scroll(self.scroll)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range)
        self.panel = PanelFrame()
        self.panel.setMaximumWidth(COLUMN_MAX)
        rows_column = QVBoxLayout(content)
        rows_column.setContentsMargins(0, 0, 0, 0)
        rows_column.setSpacing(0)
        self.title_label = body_label("", "screen_title", holo.INK_1, wrap=False)
        self.note_label = body_label("", "body_sm", holo.INK_3, name="settingsNote")
        self._grid_box = QWidget()
        self._grid = QGridLayout(self._grid_box)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(8)
        self._grid.setColumnStretch(0, 2)
        self._grid.setColumnStretch(1, 3)
        self._grid.setColumnStretch(2, 0)
        self.text_label = body_label("", "body_sm", holo.INK_2, selectable=True, name="settingsHow")
        self.config_path = body_label("", "body_sm", holo.INK_4, selectable=True, name="settingsConfigPath")
        self.config_button = holo.HoloButton(OPEN_CONFIG_TEXT, holo.GHOST)
        self.config_button.clicked.connect(self.openConfig)
        self.data_label = body_label(DATA_TEXT, "body_sm", holo.INK_2, selectable=True, name="settingsData")
        self.data_path = body_label("", "body_sm", holo.INK_4, selectable=True, name="settingsDataPath")
        self.data_button = holo.HoloButton(OPEN_DATA_TEXT, holo.GHOST)
        self.data_button.clicked.connect(self.openDataFolder)
        column = QVBoxLayout(self.panel)
        column.setContentsMargins(PANEL_PAD[0] + 4, PANEL_PAD[1] + 4, PANEL_PAD[2] + 4, PANEL_PAD[3] + 4)
        column.setSpacing(0)
        column.addWidget(self.title_label)
        column.addSpacing(6)
        column.addWidget(self.note_label)
        column.addSpacing(14)
        rows_column.addWidget(self._grid_box)
        self._extra_box = QVBoxLayout()
        self._extra_box.setContentsMargins(0, 12, 0, 0)
        rows_column.addLayout(self._extra_box)
        self._tail_box = QVBoxLayout()            # a built-in group's own part (attach_extra): under its rows
        self._tail_box.setContentsMargins(0, 18, 0, 0)
        rows_column.addLayout(self._tail_box)
        rows_column.addStretch(1)
        column.addWidget(self.scroll, 1)
        column.addSpacing(16)
        column.addWidget(Rule())
        column.addSpacing(12)
        column.addWidget(self.text_label)
        column.addSpacing(4)
        column.addWidget(self.config_path)
        column.addSpacing(10)
        column.addWidget(self.data_label)
        column.addSpacing(4)
        column.addWidget(self.data_path)
        column.addSpacing(8)
        buttons = QHBoxLayout()                   # side by side: the foot stays short on a small window
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self.config_button)
        buttons.addWidget(self.data_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        holder = QHBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.addWidget(self.panel, 1000)
        holder.addStretch(1)
        right.addLayout(holder)                   # as tall as the group needs, no taller than the window
        right.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(holo.GUTTER_SIDE + 8, holo.GUTTER_TOP, holo.GUTTER_SIDE, 16)
        layout.setSpacing(24)
        layout.addWidget(self._side)
        layout.addLayout(right, 1)
        self._value_labels: list[QLabel] = []
        self.select_group(self._group)

    # ---- API --------------------------------------------------------------------------------

    def add_group(self, key: str, title: str, *, note: str, how: str, rows: Callable[[], list[tuple[str, str]]],
                  extra: QWidget | None = None, edits: Mapping[str, Sequence[str]] | None = None) -> None:
        """A feature's own group (see the module docstring), listed after the built-in ones."""
        if key in self.group_buttons:
            return
        self._added_edits[key] = {label: tuple(name for name in keys if name in overrides.EDITABLE)
                                  for label, keys in dict(edits or {}).items()}
        button = GroupButton(key, title)
        button.clicked.connect(lambda _checked=False, which=key: self.select_group(which))
        button.installEventFilter(self)
        self.group_buttons[key] = button
        self._side_layout.insertWidget(self._side_layout.count() - 1, button)
        self._groups.append((key, title))
        self._added[key] = (note, how, rows, extra)
        if extra is not None:
            self._extra_box.addWidget(extra)
            extra.hide()
        if self._group == key:
            self._fill()

    def attach_extra(self, key: str, widget: QWidget) -> None:
        """``widget`` under the rows of the built-in group ``key`` (see the module docstring)."""
        if key in self._extras or key not in dict(GROUPS):
            return
        self._extras[key] = widget
        self._tail_box.addWidget(widget)
        widget.setVisible(self._group == key)

    def refresh(self) -> None:
        """Show the open group again (a feature's rows changed)."""
        self._fill()

    def set_store(self, store: Any) -> None:
        """The app's settings.json (overrides.SettingsStore): rows of EDIT_ROWS get Change. None: read only."""
        self._store = store
        self._editing = ""
        self._fill()

    def row_keys(self, label: str) -> tuple[str, ...]:
        """The settings Change sets on the open group's row ``label`` (() without a store, or for a read-only row)."""
        if self._store is None:
            return ()
        if self._group in self._added:
            return self._added_edits.get(self._group, {}).get(label, ())
        return edit_keys(self._config, self._group, label)

    def row_note(self, label: str) -> str:
        """What the open group's row ``label`` says under its value ("" for none)."""
        return self._notes.get(label, "")

    def open_editor(self, label: str) -> None:
        """Change on the row ``label``: its editor under it, the keyboard on its first control."""
        if not self.row_keys(label):
            return
        self._editing = label
        self._fill()
        editor = self.editor
        if editor is not None:
            self.reveal(editor, editor.first_control())

    def close_editor(self) -> None:
        """Cancel: nothing is written; the keyboard goes back to the row's Change."""
        label, self._editing = self._editing, ""
        self._fill()
        self._focus_change(label)

    def set_paths(self, config_path: str, data_dir: str) -> None:
        self.config_path.setText(config_path)
        self.data_path.setText(data_dir)

    def set_config(self, config: Any) -> None:
        """The loaded config (read only; never written back)."""
        self._config = config
        self._fill()

    def set_connected(self, connected: Mapping[str, bool]) -> None:
        """Which accounts are signed in (the app's own check); only yes / no is shown."""
        self._connected = {str(alias): bool(value) for alias, value in dict(connected or {}).items()}
        if self._group == "accounts":
            self._fill()

    def select_group(self, key: str) -> None:
        if key not in self.group_buttons:
            return
        changed = key != self._group
        if changed:
            self._editing = ""
            self._revealing = None
        self._group = key
        self.group_buttons[key].setChecked(True)
        self._fill()
        self.scroll.verticalScrollBar().setValue(0)
        if changed:
            self.groupChanged.emit(key)

    def reveal(self, widget: QWidget, focus: QWidget | None = None) -> None:
        """Canvas part 5B: ``widget`` (a row of the open group) scrolled into view, and the keyboard to ``focus`` (else
        to it) - once the group is laid out, so it holds on a short window too (1120 x 700)."""
        def show() -> None:
            if not shiboken6.isValid(widget) or not widget.isVisibleTo(self):
                return
            self.scroll.widget().layout().activate()
            self._scroll_to(widget, focus)
            target = focus if focus is not None and shiboken6.isValid(focus) else widget
            target.setFocus(Qt.FocusReason.TabFocusReason)    # (its focus ring shows: the keyboard is there)

        # The panel's height follows its rows, so the scroll range may come after this: scrolled again whenever it
        # changes during the next moment (REVEAL_HOLD_MS), then left to the owner.
        self._revealing = (widget, focus)
        self._reveal_seq += 1
        seq = self._reveal_seq
        QTimer.singleShot(REVEAL_HOLD_MS, self, lambda: self._end_reveal(seq))
        show()
        QTimer.singleShot(0, self, show)          # (and again after the layout the group's fill asked for)

    def _scroll_to(self, widget: QWidget, focus: QWidget | None) -> None:
        self.scroll.ensureWidgetVisible(widget, 0, 48)
        if focus is not None and focus is not widget and shiboken6.isValid(focus) and focus.isVisibleTo(self):
            self.scroll.ensureWidgetVisible(focus, 0, 16)    # a tall row on a short window: its button too

    def _on_scroll_range(self, _low: int, _high: int) -> None:
        pending = self._revealing
        if pending is not None and all(item is None or shiboken6.isValid(item) for item in pending):
            if pending[0].isVisibleTo(self):
                self._scroll_to(*pending)

    def _end_reveal(self, seq: int) -> None:
        if seq == self._reveal_seq:
            self._revealing = None

    def current_group(self) -> str:
        return self._group

    def rows(self) -> list[tuple[str, str]]:
        """What the open group shows now."""
        if self._group in self._added:
            return list(self._added[self._group][2]())
        return settings_rows(self._config, self._group, self._connected)

    def shown_texts(self) -> list[str]:
        """Every text the panel shows (tests: nothing secret)."""
        labels = [self.title_label, self.note_label, self.text_label, self.config_path, self.data_label,
                  self.data_path, *self._value_labels]
        labels += [widget for widget in self._grid_box.findChildren(QLabel)]
        texts = [label.text() for label in labels]
        return texts + [button.text() for button in self._grid_box.findChildren(QAbstractButton)]

    # ---- inside -------------------------------------------------------------------------------

    def _fill(self) -> None:
        key = self._group
        added = self._added.get(key)
        self.title_label.setText(dict(self._groups)[key])
        self.note_label.setText(added[0] if added else GROUP_NOTES.get(key, ""))
        rows = self.rows()
        editable = any(self.row_keys(label) for label, _value in rows)
        how = HOW_EDIT_TEXT if editable else HOW_TEXT
        self.text_label.setText(added[1] if added else how.format(tables=_tables(key)))
        for other, (_note, _how, _rows, extra) in self._added.items():
            if extra is not None:
                extra.setVisible(other == key)
        for other, extra in self._extras.items():
            extra.setVisible(other == key)
        # An open editor outlives a refill (a feature's rows changed, the voice was picked): what the owner chose so
        # far and where the keyboard was come back in the new one.
        draft: dict[str, Any] = {}
        focus_path: tuple[str, int] | None = None
        old_editor = self.editor
        if old_editor is not None and shiboken6.isValid(old_editor) and self._editing:
            draft, focus_path = old_editor.draft(), old_editor.focus_path()
            problem = old_editor.problem.text() if old_editor.problem.isVisibleTo(old_editor) else ""
        else:
            problem = ""
        self._snapshot = self._stored()           # settings.json read once for this fill (afresh: LOW 3)
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._value_labels = []
        self.editor = None
        self.change_buttons, self.back_buttons, self._notes = {}, {}, {}
        line = 0
        for label, value in rows:
            name = body_label(label, "body_sm", holo.INK_3)
            name.setContentsMargins(0, 2, 0, 0)                  # on the value's first line
            shown = body_label(value, "body", holo.INK_1, selectable=True)
            shown.setAccessibleName(f"{label}: {value}")
            self._grid.addWidget(name, line, 0, Qt.AlignmentFlag.AlignTop)
            self._value_labels.append(shown)
            keys = self.row_keys(label)
            cell: QWidget = shown
            if keys:
                cell = self._edit_cell(label, keys, shown)
                change = settings_edit.RowLink(CHANGE_TEXT, f"{CHANGE_TEXT} {label}")
                change.clicked.connect(lambda _checked=False, which=label: self.open_editor(which))
                self.change_buttons[label] = change
                self._grid.addWidget(change, line, 2, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
                if self._editing == label:
                    change.hide()                   # (only once in the grid: never a window of its own)
            self._grid.addWidget(cell, line, 1, Qt.AlignmentFlag.AlignTop)
            line += 1
            if keys and self._editing == label:
                start = {**self._after_restart(keys), **{k: v for k, v in draft.items() if k in keys}}
                editor = settings_edit.RowEditor(keys, start, self._hour24())
                editor.saveRequested.connect(lambda values, which=label: self._save(which, values))
                editor.cancelRequested.connect(self.close_editor)
                if problem:
                    editor.set_problem(problem)
                self.editor = editor
                self._grid.addWidget(editor, line, 0, 1, 3)
                line += 1
        self._snapshot = None
        if self._editing and self.editor is None:
            self._editing = ""                       # its row went (the group's rows changed)
        self._grid_box.setVisible(bool(rows))
        self.scroll.widget().updateGeometry()
        self.scroll.updateGeometry()
        if self.editor is not None and focus_path is not None:
            self.editor.focus_target(focus_path).setFocus(Qt.FocusReason.TabFocusReason)

    def _hour24(self) -> bool:
        return bool(getattr(getattr(self._config, "display", None), "hour24", False))

    def _stored(self) -> dict[str, Any]:
        """settings.json's values: read afresh (a file deleted or edited meanwhile shows as it is), once per fill."""
        if self._snapshot is not None:
            return dict(self._snapshot)
        try:
            return dict(self._store.values()) if self._store is not None else {}
        except Exception as exc:  # noqa: BLE001 - a store that fails shows nothing changed
            logger.warning("Settings: settings.json could not be read (%s)", type(exc).__name__)
            return {}

    def _after_restart(self, keys: Sequence[str]) -> dict[str, Any]:
        """Each key's value from the next start: settings.json's, else config.toml's (for a key taken back), else the
        one in use now."""
        stored = self._stored()
        overridden = getattr(self._config, "overridden", frozenset()) or frozenset()
        toml = getattr(self._config, "toml_values", {}) or {}
        values: dict[str, Any] = {}
        for key in keys:
            if key in stored:
                values[key] = stored[key]
            elif key in overridden and key in toml:
                values[key] = toml[key]
            else:
                values[key] = overrides.current(self._config, key)
        return values

    def _edit_cell(self, label: str, keys: tuple[str, ...], shown: QLabel) -> QWidget:
        """The value, and under it what a change here means (and Back to config.toml) when there is one."""
        stored = self._stored()
        here = [key for key in keys if key in stored]
        after = self._after_restart(keys)
        pending = any(after[key] != overrides.current(self._config, key) for key in keys)
        words = settings_edit.row_words(keys, after, self._hour24())
        if here:
            note = CHANGED_RESTART.format(words=words) if pending else CHANGED_HERE
        elif pending:
            note = BACK_RESTART.format(words=words)
        else:
            return shown
        self._notes[label] = note
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(shown)
        noted = body_label(note, "body_sm", holo.INK_3, name="settingsRowNote")
        noted.setAccessibleName(f"{label}: {note}")
        column.addWidget(noted)
        if here:
            back = settings_edit.RowLink(BACK_TEXT, f"{BACK_TEXT}: {label}")
            back.clicked.connect(lambda _checked=False, which=label, names=keys: self._back(which, names))
            self.back_buttons[label] = back
            column.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def _save(self, label: str, values: Mapping[str, Any]) -> None:
        """Save: what differs from the value of the next start goes to settings.json; the row says so."""
        keys = self.row_keys(label)
        after = self._after_restart(keys)
        changes = {key: value for key, value in values.items() if key in keys and value != after.get(key)}
        try:
            saved = self._store.update(changes) if changes else True
        except ValueError:
            saved = False
        if not saved:
            if self.editor is not None:
                self.editor.set_problem(SAVE_FAILED)
            return
        self._editing = ""
        self._fill()
        self._focus_change(label)

    def _back(self, label: str, keys: Sequence[str]) -> None:
        """Back to config.toml: the row's keys leave settings.json (config.toml's values from the next start)."""
        if self._store is None or not self._store.reset(keys):
            logger.warning("Settings: a row could not be taken back to config.toml")
            return
        self._editing = ""
        self._fill()
        self._focus_change(label)

    def _focus_change(self, label: str) -> None:
        button = self.change_buttons.get(label)
        if button is not None:
            self.reveal(button)

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override
        if isinstance(watched, GroupButton) and event.type() == event.Type.KeyPress:
            keys = [key for key, _text in self._groups]
            step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(event.key(), 0)
            if step:
                index = (keys.index(watched.key) + step) % len(keys)
                self.select_group(keys[index])
                self.group_buttons[keys[index]].setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(watched, event)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(LIST_WIDTH + 320, 300)
