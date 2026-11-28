"""Settings' inline editors (Smaller items parts 4-5): Change on a comfort row writes settings.json, never config.toml.

Qt offscreen. Every file is in a temporary folder."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from briefing_reader import config, overrides, settings_edit, settings_view, ui
from briefing_reader.config import load_config
from tests.ui_fakes import AppHarness, fonts, settle

_app: QApplication | None = None
_REPO_BYTES = (config.PROJECT_ROOT / config.CONFIG_FILE_NAME).read_bytes()
PROD_ON = "[ask]\nenabled = true\n[productivity]\nenabled = true\nprep_offers = true\n"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_settings_edit", "-platform", "offscreen"])
    fonts()


def tearDownModule() -> None:
    if (config.PROJECT_ROOT / config.CONFIG_FILE_NAME).read_bytes() != _REPO_BYTES:
        raise AssertionError("the repo's config.toml changed during test_settings_edit")


class StageCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.environ = {"NOTION_TOKEN": "", "LOCALAPPDATA": str(self.root / "local")}
        self.file = overrides.settings_file(self.root / "local" / "briefing-reader")

    def stage(self, toml: str = "", store: bool = True) -> settings_view.SettingsStage:
        (self.root / "config.toml").write_text(toml, encoding="utf-8")
        self.config = load_config(self.root, environ=self.environ)
        stage = settings_view.SettingsStage()
        self.addCleanup(stage.deleteLater)
        stage.resize(1100, 1400)
        stage.set_config(self.config)
        if store:
            self.store = overrides.SettingsStore(self.file)
            stage.set_store(self.store)
        stage.show()
        settle()
        return stage

    def saved(self) -> dict:
        if not self.file.exists():
            return {}
        return json.loads(self.file.read_text(encoding="utf-8"))["values"]


class EditorKindTests(StageCase):
    def test_on_off_chips_save_and_mark_the_row(self) -> None:
        stage = self.stage()
        stage.select_group("times")
        stage.change_buttons["When a briefing arrives"].click()
        editor = stage.editor
        chips = editor.controls["assistant.scheduled_prompt"]
        self.assertEqual([chip.text() for _value, chip in chips.chips],
                         ['Asks "Hear it now?" first', "Says it is ready"])
        self.assertEqual(chips.value(), False)
        self.assertEqual(chips.chips[1][1].variant(), "go")                     # the chosen one is lit
        chips.chips[0][1].click()
        self.assertEqual((chips.chips[0][1].variant(), chips.chips[1][1].variant()), ("go", "ghost"))
        with self.assertLogs("briefing_reader.overrides", level="INFO") as logs:
            editor.save_button.click()
        self.assertEqual(self.saved(), {"assistant.scheduled_prompt": True})
        self.assertIn("Settings: assistant.scheduled_prompt changed here", "\n".join(logs.output))
        self.assertIsNone(stage.editor)
        self.assertEqual(dict(stage.rows())["When a briefing arrives"], "Says it is ready")     # in use until restart
        self.assertEqual(stage.row_note("When a briefing arrives"),
                         'Changed here to Asks "Hear it now?" first - used after a restart')
        self.assertIn("When a briefing arrives", stage.back_buttons)

    def test_a_choice_and_a_percentage(self) -> None:
        stage = self.stage()
        stage.select_group("times")
        stage.change_buttons["Clock on screen"].click()
        chips = stage.editor.controls["display.clock"]
        self.assertEqual([chip.text() for _v, chip in chips.chips], ["12-hour (1:05 PM)", "24-hour (13:05)"])
        chips.chips[1][1].click()
        stage.editor.save_button.click()
        stage.select_group("voice")
        stage.change_buttons["Speed"].click()
        stepper = stage.editor.controls["voice.rate"]
        self.assertEqual(stepper.shown.text(), "Normal")
        for _ in range(3):
            stepper.more.click()
        self.assertEqual(stepper.shown.text(), "15% faster")
        QTest.keyClick(stepper.more, Qt.Key.Key_Down)                          # Up / Down step too
        self.assertEqual(stepper.value(), "+10%")
        stage.editor.save_button.click()
        self.assertEqual(self.saved(), {"display.clock": "24h", "voice.rate": "+10%"})
        self.assertEqual(stage.row_note("Speed"), "Changed here to 10% faster - used after a restart")

    def test_a_stepper_keeps_the_parsers_limits(self) -> None:
        stage = self.stage('[voice]\nvolume = "+95%"\n')
        stage.change_buttons["Volume"].click()
        stepper = stage.editor.controls["voice.volume"]
        stepper.more.click()
        self.assertEqual(stepper.value(), "+100%")
        self.assertFalse(stepper.more.isEnabled())
        stepper.more.click()
        self.assertEqual(stepper.value(), "+100%")
        stage.close_editor()
        stage.change_buttons["Voice kept loaded"].click()
        minutes = stage.editor.controls["voice.keep_warm_minutes"]
        self.assertEqual((minutes.shown.text(), minutes.less.isEnabled()), ("While Jarvis runs", False))
        minutes.more.click()
        self.assertEqual(minutes.shown.text(), "15 min after he last spoke")

    def test_quiet_hours_take_two_times(self) -> None:
        stage = self.stage(PROD_ON)
        stage.select_group("quiet")
        stage.change_buttons["Quiet hours"].click()
        editor = stage.editor
        start, until = editor.controls["productivity.quiet_from"], editor.controls["productivity.quiet_until"]
        self.assertEqual((start.text(), until.text()), ("22:00", "08:00"))
        start.setText("23:5")
        editor.save_button.click()
        self.assertEqual(editor.problem.text(), settings_edit.BAD_TIME_TEXT)   # not HH:MM: nothing saved
        self.assertEqual(self.saved(), {})
        start.setText("21:30")
        until.setText("6:45")
        QTest.keyClick(until, Qt.Key.Key_Return)                               # Enter in a time saves
        self.assertEqual(self.saved(), {"productivity.quiet_from": "21:30", "productivity.quiet_until": "06:45"})
        self.assertEqual(stage.row_note("Quiet hours"), "Changed here to 9:30 PM to 6:45 AM - used after a restart")
        stage.change_buttons["Brief before a meeting"].click()
        lead = stage.editor.controls["productivity.prep_lead_minutes"]
        lead.more.click()
        stage.editor.save_button.click()
        self.assertEqual(self.saved()["productivity.prep_lead_minutes"], 20)

    def test_the_greeting_row_sets_both_its_keys(self) -> None:
        stage = self.stage()
        self.assertEqual(dict(stage.rows())["Greets you when opened"], settings_edit.GREET_WITH_FACT)
        stage.change_buttons["Greets you when opened"].click()
        fact = stage.editor.controls["assistant.greeting_context"]
        fact.chips[1][1].click()
        stage.editor.save_button.click()
        self.assertEqual(self.saved(), {"assistant.greeting_context": False})     # only what changed
        self.assertEqual(stage.row_note("Greets you when opened"), "Changed here to Yes - used after a restart")


class EditorFlowTests(StageCase):
    def test_cancel_and_esc_save_nothing_and_give_the_keyboard_back(self) -> None:
        stage = self.stage()
        stage.select_group("times")
        stage.window().activateWindow()
        stage.change_buttons["Screen motion"].click()
        settle()
        chips = stage.editor.controls["ui.motion"]
        if QApplication.focusWidget() is not None:                         # offscreen focus may be unavailable
            self.assertIs(QApplication.focusWidget(), chips.first())
        chips.chips[2][1].click()
        stage.editor.cancel_button.click()
        settle()
        self.assertIsNone(stage.editor)
        self.assertFalse(self.file.exists())
        if QApplication.focusWidget() is not None:
            self.assertIs(QApplication.focusWidget(), stage.change_buttons["Screen motion"])
        stage.change_buttons["Screen motion"].click()
        QTest.keyClick(stage.editor.controls["ui.motion"].chips[0][1], Qt.Key.Key_Escape)
        self.assertIsNone(stage.editor)
        self.assertFalse(self.file.exists())
        self.assertEqual(stage.row_note("Screen motion"), "")

    def test_saving_the_value_in_use_writes_nothing(self) -> None:
        stage = self.stage()
        stage.select_group("times")
        stage.change_buttons["Clock on screen"].click()
        stage.editor.save_button.click()
        self.assertFalse(self.file.exists())
        self.assertIsNone(stage.editor)

    def test_back_to_config_toml_takes_the_key_out(self) -> None:
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"version": 1, "values": {"voice.rate": "+20%"}}), encoding="utf-8")
        stage = self.stage('[voice]\nrate = "-10%"\n')
        self.assertEqual(dict(stage.rows())["Speed"], "20% faster")               # settings.json won at load
        self.assertEqual(stage.row_note("Speed"), settings_view.CHANGED_HERE)
        with self.assertLogs("briefing_reader.overrides", level="INFO") as logs:
            stage.back_buttons["Speed"].click()
        self.assertIn("Settings: voice.rate back to config.toml", "\n".join(logs.output))
        self.assertEqual(self.saved(), {})
        self.assertEqual(stage.row_note("Speed"), "Back to config.toml (10% slower) after a restart")
        self.assertNotIn("Speed", stage.back_buttons)
        self.assertEqual(load_config(self.root, environ=self.environ).voice.rate, "-10%")

    def test_a_failed_save_says_so_and_keeps_the_editor(self) -> None:
        stage = self.stage()
        stage.select_group("times")
        stage.change_buttons["Clock on screen"].click()
        stage.editor.controls["display.clock"].chips[1][1].click()
        with mock.patch.object(overrides.atomic, "replace", side_effect=PermissionError(13, "denied")), \
                self.assertLogs("briefing_reader.overrides", level="WARNING"):
            stage.editor.save_button.click()
        self.assertIsNotNone(stage.editor)
        self.assertEqual(stage.editor.problem.text(), settings_view.SAVE_FAILED)
        self.assertEqual(stage.row_note("Clock on screen"), "")

    def test_read_only_rows_have_no_change(self) -> None:
        stage = self.stage()
        for group, editable in (("voice", {"Speed", "Volume", "Speaks on his own", "Speaks his answers",
                                           "Says what happened after an approval", "Greets you when opened",
                                           "Voice kept loaded", "Quiet during calls"}),
                                ("times", {"When a briefing arrives", "Clock on screen", "Screen motion"}),
                                ("ask", set()), ("accounts", set()), ("quiet", set()), ("live", {"Opens"})):
            stage.select_group(group)
            with self.subTest(group=group):
                self.assertEqual(set(stage.change_buttons), editable)
                for button in stage.change_buttons.values():
                    self.assertFalse(button.isWindow())                    # never shown as a window of its own
                for label, _value in stage.rows():
                    self.assertEqual(bool(stage.row_keys(label)), label in editable)
        stage.select_group("ask")
        self.assertIn("config.toml, then restart Jarvis", stage.text_label.text())
        self.assertNotIn("Change sets", stage.text_label.text())
        stage.select_group("voice")
        self.assertIn("Change sets a row on this PC", stage.text_label.text())
        bare = self.stage(store=False)                                          # no store: read only, as before
        for group, _title in settings_view.GROUPS:
            bare.select_group(group)
            self.assertEqual(bare.change_buttons, {})

    def test_productivity_rows_are_editable_only_while_it_is_on(self) -> None:
        stage = self.stage()
        stage.select_group("quiet")
        self.assertEqual(stage.rows(), [("Quiet hours", "Not used - offers are off on this PC")])
        self.assertEqual(stage.change_buttons, {})
        on = self.stage("[ask]\nenabled = true\n[productivity]\nenabled = true\nprep_offers = false\n")
        on.select_group("quiet")
        self.assertEqual(set(on.change_buttons), {"Quiet hours"})            # no meeting brief offered: no row
        no_ask = self.stage("[productivity]\nenabled = true\n")               # the bundle needs Ask: read only
        no_ask.select_group("quiet")
        self.assertEqual(no_ask.change_buttons, {})

    def test_feature_groups_take_their_edits(self) -> None:
        stage = self.stage()
        rows = [("Talk key", "Ctrl+Alt+Space"), ("Quiet that ends a talk", "500 ms"),
                ("Model kept loaded", "10 min after a talk")]
        stage.add_group("talk", "Talk", note="n", how="h", rows=lambda: rows, edits=settings_view.TALK_EDITS)
        stage.select_group("talk")
        self.assertEqual(set(stage.change_buttons), {"Quiet that ends a talk", "Model kept loaded"})
        stage.change_buttons["Quiet that ends a talk"].click()
        quiet = stage.editor.controls["talk.quiet_ms"]
        quiet.more.click()
        self.assertEqual(quiet.shown.text(), "600 ms")
        stage.editor.save_button.click()
        self.assertEqual(self.saved(), {"talk.quiet_ms": 600})
        self.assertEqual(stage.text_label.text(), "h")

    def test_nothing_secret_is_shown_and_the_file_holds_only_editable_keys(self) -> None:
        stage = self.stage(PROD_ON)
        for group, _title in settings_view.GROUPS:
            stage.select_group(group)
            for label in list(stage.change_buttons):
                stage.open_editor(label)
                for text in stage.shown_texts():
                    with self.subTest(group=group, label=label):
                        self.assertNotIn("@", text)
                        self.assertNotIn(str(self.root), text)
                        self.assertNotIn("token", text.lower())
                stage.editor.save_button.click()
                stage.close_editor()
        for group, _title in settings_view.GROUPS:
            stage.select_group(group)
            for label in list(stage.change_buttons):
                stage.open_editor(label)
                for control in stage.editor.controls.values():
                    if hasattr(control, "chips"):
                        control.chips[-1][1].click()
                    elif hasattr(control, "more"):
                        control.less.click()
                    else:
                        control.setText("12:00")
                stage.editor.save_button.click()
        self.assertLessEqual(set(self.saved()), set(overrides.EDITABLE))
        self.assertTrue(self.saved())


class FixRoundTests(StageCase):
    """The review's findings on parts 4-5 (each fails without its fix)."""

    def focused(self, stage: settings_view.SettingsStage, widget) -> None:
        stage.activateWindow()
        QApplication.setActiveWindow(stage)
        widget.setFocus(Qt.FocusReason.TabFocusReason)
        settle()
        if not widget.hasFocus():
            self.skipTest("no keyboard focus offscreen here")

    def test_a_step_at_its_limit_hands_the_keyboard_to_the_other_step(self) -> None:
        stage = self.stage('[voice]\nvolume = "+95%"\n')
        stage.change_buttons["Volume"].click()
        stepper = stage.editor.controls["voice.volume"]
        self.focused(stage, stepper.more)
        QTest.keyClick(stepper.more, Qt.Key.Key_Space)
        self.assertEqual(stepper.value(), "+100%")
        self.assertFalse(stepper.more.isEnabled())
        self.assertTrue(stepper.less.hasFocus())
        self.assertFalse(stage.editor.save_button.hasFocus())
        QTest.keyClick(stepper.less, Qt.Key.Key_Return)                       # Enter does not save
        self.assertFalse(self.file.exists())

    def test_a_refill_keeps_the_open_editor_and_its_focus(self) -> None:
        stage = self.stage(PROD_ON)
        stage.change_buttons["Speed"].click()
        stepper = stage.editor.controls["voice.rate"]
        stepper.more.click()
        stepper.more.click()
        self.focused(stage, stepper.more)
        stage.refresh()                                                       # e.g. a "Hey Jarvis" state change
        settle()
        again = stage.editor.controls["voice.rate"]
        self.assertIsNot(again, stepper)
        self.assertEqual(again.value(), "+10%")
        self.assertTrue(again.more.hasFocus())
        stage.select_group("quiet")
        stage.change_buttons["Quiet hours"].click()
        field = stage.editor.controls["productivity.quiet_from"]
        field.setText("21:")
        stage.editor.save_button.click()                                      # half a time: a line, nothing saved
        stage.set_config(self.config)                                         # e.g. the voice was picked
        self.assertEqual(stage.editor.controls["productivity.quiet_from"].text(), "21:")
        self.assertEqual(stage.editor.problem.text(), settings_edit.BAD_TIME_TEXT)
        self.assertFalse(self.file.exists())

    def test_rows_show_settings_json_as_it_is_now(self) -> None:
        stage = self.stage()
        stage.change_buttons["Speed"].click()
        stage.editor.controls["voice.rate"].more.click()
        stage.editor.save_button.click()
        self.assertIn("Speed", stage.back_buttons)
        self.file.unlink()                                                    # deleted while Jarvis runs
        stage.refresh()
        self.assertEqual(stage.row_note("Speed"), "")
        self.assertNotIn("Speed", stage.back_buttons)


class AppSettingsTests(unittest.TestCase):
    def assert_config_button_in_window(self, size: tuple[int, int], config_extra: str = "") -> None:
        """Open config.toml lies wholly inside the window on every group, at ``size`` (the screen harness's check)."""
        app = AppHarness(self, productivity=True, config_extra=config_extra)
        app.reading(autoplay=False)
        window = app.c.window
        window.resize(*size)
        reading = window.reading
        reading.set_tab(ui.SCREEN_SETTINGS)
        settle()
        stage = reading.settings_page
        for key in list(stage.group_buttons):
            stage.group_buttons[key].click()
            settle()
            button = stage.config_button
            origin = button.mapTo(window, QPoint(0, 0))
            with self.subTest(size=size, group=key):
                self.assertTrue(button.isVisible())
                self.assertGreaterEqual(min(origin.x(), origin.y()), 0)
                self.assertLess(origin.x() + button.width(), window.width() + 1)
                self.assertLess(origin.y() + button.height(), window.height() + 1)
                self.assertGreaterEqual(stage.scroll.viewport().height(), settings_view._FitScroll.MIN_ROWS_HEIGHT - 2)

    def test_open_config_toml_stays_in_the_window_at_900_by_600(self) -> None:
        self.assert_config_button_in_window((900, 600))

    def test_open_config_toml_stays_in_the_window_at_1120_by_700(self) -> None:
        self.assert_config_button_in_window((1120, 700))

    def test_open_config_toml_stays_in_view_with_settings_talk_at_900_by_600(self) -> None:
        # Smaller items part 9: Settings > Talk gains "Live captions"; the button still sits in the window.
        self.assert_config_button_in_window((900, 600), config_extra="\n[talk]\nenabled = true\n")

    def test_the_live_captions_row_and_its_longest_value_are_whole_at_900_by_600(self) -> None:
        app = AppHarness(self, config_extra="\n[talk]\nenabled = true\n")
        app.reading(autoplay=False)
        window = app.c.window
        window.resize(900, 600)
        reading = window.reading
        stage = reading.settings_page
        reading.set_tab(ui.SCREEN_SETTINGS)
        settle()
        stage.select_group("talk")
        settle()
        viewport = stage.scroll.viewport()
        right = viewport.mapTo(window, QPoint(viewport.width(), 0)).x()

        def whole(widget) -> None:
            origin = widget.mapTo(window, QPoint(0, 0))
            self.assertTrue(widget.isVisible())
            self.assertGreaterEqual(origin.x(), 0)
            self.assertLessEqual(origin.x() + widget.width(), right + 1)
            if isinstance(widget, QLabel) and widget.wordWrap():
                self.assertGreaterEqual(widget.height(), widget.heightForWidth(widget.width()))
            else:
                self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())

        longest = settings_edit.value_words("talk.live_captions", "auto")
        stage.scroll.ensureWidgetVisible(stage.change_buttons["Live captions"])
        settle()
        value = next(label for label in stage._value_labels if label.accessibleName().startswith("Live captions:"))
        self.assertEqual(value.text(), longest)
        whole(value)
        name = next(label for label in stage._grid_box.findChildren(QLabel) if label.text() == "Live captions")
        whole(name)
        whole(stage.change_buttons["Live captions"])
        stage.change_buttons["Live captions"].click()
        settle()
        for _value, chip in stage.editor.controls["talk.live_captions"].chips:
            with self.subTest(chip=chip.text()):
                stage.scroll.ensureWidgetVisible(chip)
                settle()
                whole(chip)

    def test_live_captions_are_changed_in_settings_talk(self) -> None:
        app = AppHarness(self, config_extra="\n[talk]\nenabled = true\n")
        app.reading(autoplay=False)
        reading = app.c.window.reading
        stage = reading.settings_page
        reading.set_tab(ui.SCREEN_SETTINGS)
        settle()
        stage.select_group("talk")
        settle()
        self.assertEqual(dict(stage.rows())["Live captions"], "Auto - when the graphics card runs it")
        self.assertIn("Live captions", stage.change_buttons)
        stage.change_buttons["Live captions"].click()
        chips = stage.editor.controls["talk.live_captions"]
        self.assertEqual([chip.text() for _value, chip in chips.chips],
                         ["Auto - when the graphics card runs it", "On", "Off"])
        chips.chips[2][1].click()
        stage.editor.save_button.click()
        self.assertEqual(json.loads(overrides.settings_file(app.config.data_dir).read_text(encoding="utf-8")),
                         {"version": 1, "values": {"talk.live_captions": "off"}})
        self.assertEqual(stage.row_note("Live captions"), settings_view.CHANGED_RESTART.format(words="Off"))
        loaded = load_config(Path(app.root), environ={"LOCALAPPDATA": str(Path(app.root) / "local")})
        self.assertEqual(loaded.talk.live_captions, "off")                     # used after a restart

    def test_the_app_wires_the_store_and_config_toml_stays_as_it_is(self) -> None:
        app = AppHarness(self, config_extra="\n[productivity]\nenabled = true\n")
        app.reading(autoplay=False)
        reading = app.c.window.reading
        stage = reading.settings_page
        config_path = Path(app.root) / "config.toml"
        before = config_path.read_bytes()
        reading.set_tab(ui.SCREEN_SETTINGS)
        settle()
        stage.select_group("voice")
        settle()
        stage.change_buttons["Speed"].click()
        stage.editor.controls["voice.rate"].more.click()
        stage.editor.save_button.click()
        self.assertEqual(json.loads(overrides.settings_file(app.config.data_dir).read_text(encoding="utf-8")),
                         {"version": 1, "values": {"voice.rate": "+5%"}})
        stage.select_group("instant")
        self.assertEqual(set(stage.change_buttons), {"Temperature"})
        self.assertEqual(config_path.read_bytes(), before)
        sites = app.c.site_signins.panel.field                       # LIVE and privacy's "Sites ... signed in"
        self.assertEqual([edit for edit in stage.findChildren(QLineEdit) if edit is not sites], [])   # no time field


if __name__ == "__main__":
    unittest.main()
