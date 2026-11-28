"""Settings > Accounts' own part for the Outlook / Microsoft accounts (read only, first stage), under the Accounts rows
while config.toml names one (``[accounts.<alias>]`` with ``backend = "microsoft"``).

``OutlookAccountsPanel(aliases, parent=None)``: a line on what Outlook accounts do here, and per account its state and
one button - Connect (``connectClicked(alias)``) or Disconnect (``disconnectClicked(alias)``) - with a note line under
it (why the Connect can't start: no client id yet; what the last sign-in said). ``show_state(...)`` sets them.

``OutlookConnect(accounts, threaded=True, parent=None)``: ``attach_panel(panel)``, ``connect(alias)`` (the browser
sign-in on a thread of its own, only on the owner's click; ms_auth.MicrosoftAccount.sign_in), ``disconnect(alias)``
(deletes the saved sign-in), ``states()`` ({alias: signed in}, files only), ``busy(alias)``. ``finished(alias, outcome,
message)`` runs on the GUI thread when a sign-in or a disconnect ended (outcome CONNECTED / FAILED / DISCONNECTED); the
app then reads the Day view again. Without a client id, Connect starts nothing and the note says plainly what is
needed. The log says only the alias word ("work", "personal", else "other") and how it ended.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import holo, hud
from .google_auth import logged_alias
from .ms_auth import NO_CLIENT_ID_MESSAGE

logger = logging.getLogger(__name__)

CONNECTED, FAILED, DISCONNECTED = "connected", "failed", "disconnected"
INTRO_TEXT = ("Outlook accounts are read only for now: their calendar shows in the Day view and their mail's headers "
              "can be read. Jarvis never sends, answers or changes anything for them.")
CONNECT_TEXT = "Connect"
DISCONNECT_TEXT = "Disconnect"
STATE_WORDS = {True: "Outlook - connected (read only)", False: "Outlook - not connected"}
WAITING_TEXT = "Outlook - waiting for the sign-in in your browser..."
SIGNED_IN_NOTE = "Connected. Its calendar shows in the Day view from the next read."
DISCONNECTED_NOTE = "Disconnected: the saved sign-in was deleted from this PC."


class OutlookAccountsPanel(QWidget):
    """See the module docstring."""

    connectClicked = Signal(str)
    disconnectClicked = Signal(str)

    def __init__(self, aliases: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.intro = QLabel(INTRO_TEXT)
        self.intro.setWordWrap(True)
        self.intro.setFont(hud.body_font(13))
        hud.set_label_color(self.intro, hud.TEXT_SOFT)
        layout.addWidget(self.intro)
        self.states: dict[str, QLabel] = {}
        self.buttons: dict[str, holo.HoloButton] = {}
        self.notes: dict[str, QLabel] = {}
        self._connected: dict[str, bool] = {}
        for alias in aliases:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            name = QLabel(alias)
            name.setMinimumWidth(96)               # the state words line up under each other
            name.setFont(hud.body_font(13))
            hud.set_label_color(name, hud.TEXT_BODY)
            state = QLabel(STATE_WORDS[False])
            state.setFont(hud.body_font(13))
            hud.set_label_color(state, hud.TEXT_SOFT)
            button = holo.HoloButton(CONNECT_TEXT, holo.GHOST, size=holo.SIZE_SM)
            button.setAccessibleName(f"Connect the {alias} Outlook account")
            button.clicked.connect(lambda _checked=False, which=alias: self._clicked(which))
            row.addWidget(name)
            row.addWidget(state, 1)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
            layout.addLayout(row)
            note = QLabel("")
            note.setWordWrap(True)
            note.setFont(hud.body_font(12))
            hud.set_label_color(note, hud.TEXT_DIM)
            note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            note.hide()
            layout.addWidget(note)
            self.states[alias], self.buttons[alias], self.notes[alias] = state, button, note

    def _clicked(self, alias: str) -> None:
        (self.disconnectClicked if self._connected.get(alias) else self.connectClicked).emit(alias)

    def show_state(self, connected: Mapping[str, bool], busy: Sequence[str] = (), notes: Mapping[str, str] | None = None,
                   *, configured: bool = True) -> None:
        """Each account's state word, its button (Connect, or Disconnect once connected; off while its sign-in is
        open) and its note (no client id yet: NO_CLIENT_ID_MESSAGE)."""
        notes = notes or {}
        for alias, label in self.states.items():
            signed_in = bool(connected.get(alias))
            waiting = alias in busy
            self._connected[alias] = signed_in
            label.setText(WAITING_TEXT if waiting else STATE_WORDS[signed_in])
            button = self.buttons[alias]
            button.setText(DISCONNECT_TEXT if signed_in else CONNECT_TEXT)
            button.setAccessibleName(f"{DISCONNECT_TEXT if signed_in else CONNECT_TEXT} the {alias} Outlook account")
            button.setEnabled(not waiting)
            text = notes.get(alias, "") or ("" if configured or signed_in else NO_CLIENT_ID_MESSAGE)
            self.notes[alias].setText(text)
            self.notes[alias].setVisible(bool(text))


class OutlookConnect(QObject):
    """See the module docstring. ``threaded`` False (tests): the call runs in the caller."""

    finished = Signal(str, str, str)       # alias, CONNECTED / FAILED / DISCONNECTED, message
    _done = Signal(str, str, str)

    def __init__(self, accounts: Mapping[str, Any], *, threaded: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._accounts = dict(accounts)
        self._threaded = threaded
        self._busy: set[str] = set()
        self._notes: dict[str, str] = {}
        self._panel: OutlookAccountsPanel | None = None
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    @property
    def aliases(self) -> list[str]:
        return list(self._accounts)

    def attach_panel(self, panel: OutlookAccountsPanel) -> None:
        self._panel = panel
        panel.connectClicked.connect(self.connect)
        panel.disconnectClicked.connect(self.disconnect)
        self._show()

    def configured(self) -> bool:
        return all(self._safe(account, "is_configured") for account in self._accounts.values())

    def states(self) -> dict[str, bool]:
        """{alias: a usable saved sign-in} (files only)."""
        return {alias: self._safe(account, "is_signed_in") for alias, account in self._accounts.items()}

    def busy(self, alias: str | None = None) -> bool:
        return bool(self._busy) if alias is None else alias in self._busy

    def note(self, alias: str) -> str:
        return self._notes.get(alias, "")

    @staticmethod
    def _safe(account: Any, name: str) -> bool:
        try:
            return bool(getattr(account, name)())
        except Exception as exc:  # noqa: BLE001 - read as no
            logger.debug("Outlook: could not check an account (%s)", type(exc).__name__)
            return False

    def refresh(self) -> None:
        self._show()

    def _show(self) -> None:
        if self._panel is not None:
            self._panel.show_state(self.states(), sorted(self._busy), self._notes, configured=self.configured())

    def connect(self, alias: str) -> None:
        """The owner clicked Connect: the browser sign-in, unless one is open or there is no client id yet."""
        account = self._accounts.get(alias)
        if account is None or alias in self._busy:
            return
        if not self._safe(account, "is_configured"):
            self._notes[alias] = NO_CLIENT_ID_MESSAGE
            logger.info("Outlook (%s): Connect needs a client id first", logged_alias(alias))
            self._show()
            return
        self._busy.add(alias)
        self._notes.pop(alias, None)
        logger.info("Outlook (%s): the owner clicked Connect", logged_alias(alias))
        self._run(alias, account.sign_in, CONNECTED)

    def disconnect(self, alias: str) -> None:
        """The owner clicked Disconnect: the saved sign-in is deleted (files only)."""
        account = self._accounts.get(alias)
        if account is None or alias in self._busy:
            return
        self._busy.add(alias)
        logger.info("Outlook (%s): the owner clicked Disconnect", logged_alias(alias))
        self._run(alias, account.disconnect, DISCONNECTED)

    def _run(self, alias: str, call: Any, success: str) -> None:
        def work() -> None:
            outcome, message = success, ""
            try:
                call()
            except Exception as exc:  # noqa: BLE001 - every failure is a safe message (ms_auth's AccountErrors)
                outcome, message = FAILED, str(exc) or type(exc).__name__
            if self._threaded:
                self._done.emit(alias, outcome, message)
            else:
                self._on_done(alias, outcome, message)

        self._show()
        if self._threaded:
            threading.Thread(target=work, name="outlook-sign-in", daemon=True).start()
        else:
            work()

    def _on_done(self, alias: str, outcome: str, message: str) -> None:
        self._busy.discard(alias)
        self._notes[alias] = {CONNECTED: SIGNED_IN_NOTE, DISCONNECTED: DISCONNECTED_NOTE}.get(outcome, message)
        logger.info("Outlook (%s): %s", logged_alias(alias), outcome)
        self._show()
        self.finished.emit(alias, outcome, message)
