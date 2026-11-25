"""The canvas's status line over the ask line: an answer gets two balanced lines (one line cut it mid-word, "...free
before n..."), its box only as tall as the lines it shows; off the canvas it is the hologram pill's one line again.
Qt runs offscreen; nothing else starts."""

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import hud
from tests.ui_fakes import fonts, settle

_app: QApplication | None = None

LONG = "Yes, sir: the Lake Loop trail reopened on the 14th, and parking is free before nine."
SHORT = "Done, sir."
TOO_LONG = " ".join(["The trail reopened after the bridge repair and parking is free before nine."] * 4)


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas_status_line", "-platform", "offscreen"])
    fonts()


class StatusLineTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bar = hud.CommandBar()
        self.addCleanup(self.bar.deleteLater)
        self.bar.set_holo(True)
        self.bar.set_canvas(True)
        self.bar.resize(524, 120)
        self.bar.show()
        settle()
        self.line = self.bar._line

    def say(self, text: str) -> hud._ClampedLabel:
        self.bar.set_status(text, hud.TONE_DONE)
        settle()
        settle()
        return self.bar.status_label

    def test_a_long_answer_shows_whole_on_two_lines(self) -> None:
        label = self.say(LONG)
        self.assertTrue(label.isVisible())
        self.assertFalse(label.is_cut())
        self.assertEqual(label.text(), LONG)
        self.assertEqual(label.height(), self.line * 2 + 2)

    def test_the_two_lines_are_balanced_never_one_stray_word(self) -> None:
        label = self.say(LONG)
        width = label.contentsRect().width() - 2
        self.assertLess(width, label.width() - 40)                     # narrowed by even side margins
        self.assertEqual(label.contentsMargins().left(), label.contentsMargins().right())
        self.assertEqual(hud._line_count(LONG, label.font(), width), 2)  # still two lines at that width

    def test_a_short_answer_keeps_one_line_and_no_margins(self) -> None:
        label = self.say(LONG)
        label = self.say(SHORT)
        self.assertEqual(label.height(), self.line + 2)
        self.assertEqual(label.contentsMargins().left(), 0)
        self.assertEqual(label.text(), SHORT)

    def test_an_answer_longer_than_two_lines_is_cut_with_its_whole_text_as_tooltip(self) -> None:
        label = self.say(TOO_LONG)
        self.assertTrue(label.is_cut())
        self.assertTrue(label.text().endswith("…"))
        self.assertEqual(label.height(), self.line * 2 + 2)
        self.assertIn("bridge repair", label.toolTip())

    def test_off_the_canvas_the_pill_has_one_line_again(self) -> None:
        self.say(LONG)
        self.bar.set_canvas(False)
        settle()
        label = self.bar.status_label
        self.assertEqual(label._max_lines, 1)
        self.assertEqual(label.height(), self.line + 2)
        self.assertEqual(label.contentsMargins().left(), 0)

    def test_line_count(self) -> None:
        font = self.bar.status_label.font()
        self.assertEqual(hud._line_count("", font, 100), 1)
        self.assertEqual(hud._line_count(LONG, font, 0), 1)
        self.assertEqual(hud._line_count(SHORT, font, 400), 1)
        self.assertGreaterEqual(hud._line_count(LONG, font, 200), 3)


if __name__ == "__main__":
    unittest.main()
