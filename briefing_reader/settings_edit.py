"""Settings' inline editors: changing a comfort setting on this PC (Smaller items, part 4; overrides.py keeps it).

Public API
==========

``RowEditor(keys, values, hour24)`` (``draft()``, ``focus_path()``, ``focus_target(path)``: kept across a refill)
    The editor a row's Change opens under it: one control per key (``overrides.EDITABLE``'s kind) - On / Off or a
    choice as chips (the chosen one lit), a number or a percentage as a stepper (Less / More within the parser's
    limits, Up / Down on its value too), a time as an HH:MM field - then Save and Cancel. ``saveRequested(dict)`` with
    {key: value} (a time that is not HH:MM shows a line and saves nothing), ``cancelRequested()``; Esc is Cancel,
    Enter in a time field is Save. ``first_control()`` is where the keyboard goes when it opens. ``set_problem(text)``
    shows a line under the controls (a save that failed).

``value_words(key, value, hour24)``, ``row_words(keys, values, hour24)``
    A value in the words its row uses ("10% faster", "24-hour (13:05)", "10:00 PM to 8:00 AM").

``RowLink(text, accessible)``
    A row's Change and Back to config.toml: cyan words on the row's line height (no taller than the row).

Never shows a secret, an address or a path: only the values of ``overrides.EDITABLE``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import holo, overrides
from .memory_view import body_label

SAVE_TEXT = "Save"
CANCEL_TEXT = "Cancel"
LESS_TEXT = "Less"
MORE_TEXT = "More"
BAD_TIME_TEXT = "Type a 24-hour time like 22:00."
# A control's own words on a row that sets more than one key.
CONTROL_LABELS = {"productivity.quiet_from": "From", "productivity.quiet_until": "Until",
                  "assistant.greet": "Greets you", "assistant.greeting_context": "With one fact"}
GREET_KEYS = ("assistant.greet", "assistant.greeting_context")
QUIET_KEYS = ("productivity.quiet_from", "productivity.quiet_until")
GREET_WITH_FACT = "Yes, with one fact (counts and times only)"

_CLOCK_WORDS = {"12h": "12-hour (1:05 PM)", "24h": "24-hour (13:05)"}
_MOTION_WORDS = {"lively": "Lively", "calm": "Calm", "off": "Off"}
_LIVE_WORDS = {"tab": "On the Live screen", "window": "In its own window", "off": "Only when you open it"}
_TEMPERATURE_WORDS = {"auto": "Auto - the Windows region", "celsius": "Celsius", "fahrenheit": "Fahrenheit"}
_CHOICE_WORDS: dict[str, Mapping[str, str]] = {"display.clock": _CLOCK_WORDS, "ui.motion": _MOTION_WORDS,
                                               "live.auto_open": _LIVE_WORDS, "instant.temperature": _TEMPERATURE_WORDS}
_BOOL_WORDS: dict[str, tuple[str, str]] = {          # (true, false)
    "assistant.speak": ("Yes", "No - text only"),
    "assistant.scheduled_prompt": ('Asks "Hear it now?" first', "Says it is ready"),
    "voice.quiet_in_calls": ("Yes - captions only while another app uses the microphone", "No - speaks as usual"),
}
_PERCENT_WORDS = {"voice.rate": ("faster", "slower"), "voice.volume": ("louder", "quieter")}


def _clock(value: Any, hour24: bool) -> str:
    text = str(value or "")
    hours, _colon, minutes = text.partition(":")
    if not (hours.isdigit() and minutes.isdigit()):
        return text
    hour, minute = int(hours), int(minutes)
    if hour24:
        return f"{hour:02d}:{minute:02d}"
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _percent_number(value: Any) -> int:
    try:
        return int(str(value or "+0%").strip().rstrip("%"))
    except ValueError:
        return 0


def value_words(key: str, value: Any, hour24: bool = False) -> str:
    """``value`` of ``key`` in its row's words."""
    spec = overrides.EDITABLE.get(key)
    if spec is None:
        return str(value)
    if spec.kind == overrides.KIND_BOOL:
        yes, no = _BOOL_WORDS.get(key, ("Yes", "No"))
        return yes if value else no
    if spec.kind == overrides.KIND_CHOICE:
        return _CHOICE_WORDS.get(key, {}).get(str(value), str(value))
    if spec.kind == overrides.KIND_PERCENT:
        number = _percent_number(value)
        more, less = _PERCENT_WORDS.get(key, ("more", "less"))
        return "Normal" if number == 0 else f"{abs(number)}% {more if number > 0 else less}"
    if spec.kind == overrides.KIND_TIME:
        return _clock(value, hour24)
    number = int(value)
    if key == "voice.keep_warm_minutes":
        return "While Jarvis runs" if number == 0 else f"{number} min after he last spoke"
    if key == "talk.keep_warm_minutes":
        return f"{number} min after a talk"
    if key == "talk.quiet_ms":
        return f"{number} ms"
    if key == "productivity.prep_lead_minutes":
        return f"{number} minutes before it starts"
    return str(number)


def row_words(keys: Sequence[str], values: Mapping[str, Any], hour24: bool = False) -> str:
    """A row's value in words: one key's words, a span ("10:00 PM to 8:00 AM") for quiet hours, the greeting and its
    fact in one."""
    if tuple(keys) == GREET_KEYS:
        if not values.get(keys[0]):
            return "No"
        return GREET_WITH_FACT if values.get(keys[1]) else "Yes"
    if tuple(keys) == QUIET_KEYS:
        return f"{_clock(values.get(keys[0]), hour24)} to {_clock(values.get(keys[1]), hour24)}"
    return ", ".join(value_words(key, values.get(key), hour24) for key in keys)


def _field_style(edit: QWidget) -> None:
    """A text field in the screen's look (square in the canvas look), as the documents' fields."""
    radius = holo.CANVAS_RADIUS if holo.canvas_look() else 6
    ground = "#03090C" if holo.canvas_look() else holo.FIELD
    edge = "rgba(143, 235, 255, 153)" if holo.canvas_look() else "rgba(60, 211, 238, 110)"
    focus = "#FFFFFF" if holo.canvas_look() else holo.HOLO
    name = type(edit).__name__
    edit.setStyleSheet(f"{name} {{ background: {ground}; border: 1px solid {edge}; border-radius: {radius:g}px;"
                       f" padding: 4px 8px; color: {holo.INK_1}; selection-background-color: rgba(53, 211, 240, 82);"
                       f" selection-color: #FFFFFF; }} {name}:focus {{ border: 1px solid {focus}; }}")


class RowLink(QAbstractButton):
    """A row's Change (and Back to config.toml): cyan words on the row's own line height, underlined on hover and
    keyboard focus (with the focus ring then); Tab reaches it, Space or Enter presses it."""

    PAD = 4

    def __init__(self, text: str, accessible: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(accessible or text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._font = holo.font("body_sm", 600)
        self._ring = False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        metrics = QFontMetricsF(self._font)
        return QSize(int(metrics.horizontalAdvance(self.text())) + 2 * self.PAD + 2, int(metrics.height()) + 6)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def focusInEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self._ring = holo.keyboard_focus(event.reason(), self._ring)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.reason() != Qt.FocusReason.ActiveWindowFocusReason:
            self._ring = False
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            lit = self.underMouse() or (self.hasFocus() and self._ring)
            font = QFont(self._font)
            font.setUnderline(lit)
            painter.setFont(font)
            painter.setPen(QColor("#9BEBFA" if lit else holo.HOLO))
            rect = QRectF(self.rect()).adjusted(self.PAD, 0, -self.PAD, 0)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
            if self.hasFocus() and self._ring:
                holo.paint_focus_ring(painter, QRectF(self.rect()).adjusted(2, 2, -2, -2), 1.0, offset=0.0)
        finally:
            painter.end()


class ChoiceChips(QWidget):
    """On / Off, or a setting's choices: one small button each; the chosen one lit (GO), the others ghost."""

    def __init__(self, key: str, value: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        spec = overrides.EDITABLE[key]
        options: list[tuple[Any, str]]
        if spec.kind == overrides.KIND_BOOL:
            options = [(True, value_words(key, True)), (False, value_words(key, False))]
        else:
            options = [(choice, value_words(key, choice)) for choice in spec.choices]
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.chips: list[tuple[Any, holo.HoloButton]] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for option, words in options:
            chip = holo.HoloButton(words, holo.GHOST, size=holo.SIZE_SM)
            chip.setCheckable(True)
            chip.toggled.connect(lambda _on=False, button=chip: self._lit(button))
            self._group.addButton(chip)
            self.chips.append((option, chip))
            row.addWidget(chip)
        row.addStretch(1)
        self.set_value(value)

    def _lit(self, chip: holo.HoloButton) -> None:
        chip.set_variant(holo.GO if chip.isChecked() else holo.GHOST)
        chip.setAccessibleDescription("Chosen" if chip.isChecked() else "")

    def set_value(self, value: Any) -> None:
        for option, chip in self.chips:
            chip.setChecked(option == value)
            self._lit(chip)

    def value(self) -> Any:
        return next((option for option, chip in self.chips if chip.isChecked()), None)

    def first(self) -> QWidget:
        return next((chip for _option, chip in self.chips if chip.isChecked()), self.chips[0][1])

    def focusables(self) -> list[QWidget]:
        return [chip for _option, chip in self.chips]

    def raw(self) -> Any:
        return self.value()


class Stepper(QWidget):
    """A number or a percentage: Less, the value in words, More; each step ``Editable.step``, within its limits."""

    def __init__(self, key: str, value: Any, hour24: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._spec = overrides.EDITABLE[key]
        self._percent = self._spec.kind == overrides.KIND_PERCENT
        self._number = _percent_number(value) if self._percent else int(value)
        self.less = holo.HoloButton(LESS_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.more = holo.HoloButton(MORE_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.shown: QLabel = body_label("", "body", holo.INK_1, wrap=False)
        self.shown.setMinimumWidth(150)
        self.shown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.less.clicked.connect(lambda: self.step(-1))
        self.more.clicked.connect(lambda: self.step(1))
        for button in (self.less, self.more):
            button.installEventFilter(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.less)
        row.addWidget(self.shown)
        row.addWidget(self.more)
        row.addStretch(1)
        self._show()

    def step(self, direction: int) -> None:
        spec = self._spec
        number = self._number + direction * spec.step
        if direction > 0 and self._number % spec.step:            # off the grid (config.toml's 7): to the next mark
            number = self._number + (spec.step - self._number % spec.step)
        elif direction < 0 and self._number % spec.step:
            number = self._number - self._number % spec.step
        self._number = min(max(number, spec.low), spec.high)
        self._show()

    def _show(self) -> None:
        self.shown.setText(value_words(self.key, self.value()))
        self.shown.setAccessibleName(self.shown.text())
        for button, other, on in ((self.less, self.more, self._number > self._spec.low),
                                  (self.more, self.less, self._number < self._spec.high)):
            if not on and button.hasFocus() and other.isEnabled():
                other.setFocus(Qt.FocusReason.TabFocusReason)   # at the limit: the other step, never on to Save
            button.setEnabled(on)
        self.less.setAccessibleName(f"{LESS_TEXT}: {self.shown.text()}")
        self.more.setAccessibleName(f"{MORE_TEXT}: {self.shown.text()}")

    def value(self) -> Any:
        return f"{self._number:+d}%" if self._percent else self._number

    def first(self) -> QWidget:
        return self.more if self.more.isEnabled() else self.less

    def focusables(self) -> list[QWidget]:
        return [self.less, self.more]

    def raw(self) -> Any:
        return self.value()

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override
        if event.type() == event.Type.KeyPress and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.step(1 if event.key() == Qt.Key.Key_Up else -1)
            return True
        return super().eventFilter(watched, event)


class TimeField(QLineEdit):
    """A 24-hour time, HH:MM (checked on Save with the parser's own rule)."""

    def __init__(self, key: str, value: Any, parent: QWidget | None = None) -> None:
        super().__init__(str(value or ""), parent)
        self.key = key
        self.setMaxLength(5)
        self.setPlaceholderText("HH:MM")
        self.setFixedWidth(88)
        self.setFont(holo.font("body"))
        self.setAccessibleName(CONTROL_LABELS.get(key, "Time"))
        _field_style(self)

    def value(self) -> Any:
        return overrides.coerce(self.key, self.text())

    def first(self) -> QWidget:
        return self

    def focusables(self) -> list[QWidget]:
        return [self]

    def raw(self) -> Any:
        return self.text()                       # as typed, even half a time (kept across a refill)


class RowEditor(QWidget):
    """See the module docstring."""

    saveRequested = Signal(dict)
    cancelRequested = Signal()

    def __init__(self, keys: Sequence[str], values: Mapping[str, Any], hour24: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsEditor")
        self.keys = tuple(keys)
        self.controls: dict[str, Any] = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 6)
        column.setSpacing(8)
        times = QHBoxLayout()
        times.setContentsMargins(0, 0, 0, 0)
        times.setSpacing(10)
        several = len(self.keys) > 1
        for key in self.keys:
            kind = overrides.EDITABLE[key].kind
            words = CONTROL_LABELS.get(key, "") if several else ""
            if kind == overrides.KIND_TIME:
                control: Any = TimeField(key, values.get(key))
                control.returnPressed.connect(self._save)
                if words:
                    times.addWidget(body_label(words, "body_sm", holo.INK_3, wrap=False))
                times.addWidget(control)
                self.controls[key] = control
                continue
            if kind in (overrides.KIND_BOOL, overrides.KIND_CHOICE):
                control = ChoiceChips(key, values.get(key))
            else:
                control = Stepper(key, values.get(key), hour24)
            if words:
                line = QHBoxLayout()
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(10)
                named = body_label(words, "body_sm", holo.INK_3, wrap=False)
                named.setMinimumWidth(96)
                line.addWidget(named)
                line.addWidget(control, 1)
                column.addLayout(line)
            else:
                column.addWidget(control)
            self.controls[key] = control
        if times.count():
            times.addStretch(1)
            column.addLayout(times)
        self.problem: QLabel = body_label("", "body_sm", holo.ALERT_TX, name="settingsEditProblem")
        self.problem.hide()
        column.addWidget(self.problem)
        self.save_button = holo.HoloButton(SAVE_TEXT, holo.GO, size=holo.SIZE_SM)
        self.cancel_button = holo.HoloButton(CANCEL_TEXT, holo.GHOST, size=holo.SIZE_SM)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.cancelRequested)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        column.addLayout(buttons)

    def first_control(self) -> QWidget:
        return self.controls[self.keys[0]].first()

    def draft(self) -> dict[str, Any]:
        """What each control holds now, unsaved (a time as typed): a refilled screen builds the editor from it."""
        return {key: control.raw() for key, control in self.controls.items()}

    def focus_path(self) -> tuple[str, int] | None:
        """Where the keyboard is inside the editor: (key or "save" / "cancel", which of its buttons), else None."""
        for key, control in self.controls.items():
            for index, widget in enumerate(control.focusables()):
                if widget.hasFocus():
                    return key, index
        for name, button in (("save", self.save_button), ("cancel", self.cancel_button)):
            if button.hasFocus():
                return name, 0
        return None

    def focus_target(self, path: tuple[str, int] | None) -> QWidget:
        """The widget at ``path`` (focus_path) in this editor; the first control when there is none."""
        if path is not None:
            name, index = path
            if name == "save":
                return self.save_button
            if name == "cancel":
                return self.cancel_button
            control = self.controls.get(name)
            if control is not None:
                widgets = control.focusables()
                if 0 <= index < len(widgets) and widgets[index].isEnabled():
                    return widgets[index]
                return control.first()
        return self.first_control()

    def set_problem(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

    def values(self) -> dict[str, Any] | None:
        """{key: value}, or None (and the line shown) while a time is not HH:MM."""
        found: dict[str, Any] = {}
        for key, control in self.controls.items():
            value = control.value()
            if value is None:
                self.set_problem(BAD_TIME_TEXT)
                control.first().setFocus(Qt.FocusReason.TabFocusReason)
                return None
            found[key] = value
        return found

    def _save(self) -> None:
        found = self.values()
        if found is not None:
            self.saveRequested.emit(found)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
