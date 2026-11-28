"""The window re-fits the screen after a screen change (the walkthrough of 2026-11-28, fix 1).

After a Remote Desktop reconnect at another size the window kept its old layout (the top bar cut off, the Ask bar and
the orb below the screen) until it was restored and maximised again. ``BriefingWindow.refit`` does that itself once a
screen change settles. Offscreen only: Windows' own messages are not sent here, Qt's screen signals and the method are.
"""

from __future__ import annotations

import time
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from briefing_reader import config, hud, ui

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_window_refit", "-platform", "offscreen"])
    hud.load_fonts(config.PROJECT_ROOT / "fonts")


def settle(rounds: int = 6) -> None:
    for _ in range(rounds):
        QApplication.processEvents()


def wait(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


class RefitTests(unittest.TestCase):
    def window(self) -> ui.BriefingWindow:
        window = ui.BriefingWindow(10, 30)
        self.addCleanup(window.deleteLater)
        window.show_reading_view()
        window.resize(ui.READING_MIN_SIZE)
        window.show()
        settle()
        return window

    def test_maximised_window_comes_back_maximised(self) -> None:
        window = self.window()
        window.showMaximized()
        settle()
        self.assertTrue(window.refit())
        settle()
        self.assertTrue(window.windowState() & Qt.WindowState.WindowMaximized)
        self.assertEqual(window.refits, 1)

    def test_full_screen_window_stays_full_screen(self) -> None:
        window = self.window()
        window.showFullScreen()
        settle()
        self.assertTrue(window.refit())
        settle()
        self.assertTrue(window.windowState() & Qt.WindowState.WindowFullScreen)

    def test_own_size_window_moves_back_onto_the_screen(self) -> None:
        window = self.window()
        area = window.screen().availableGeometry()
        window.move(area.right() + 200, area.bottom() + 200)
        settle()
        self.assertTrue(window.refit())
        settle()
        frame = window.frameGeometry()
        self.assertTrue(area.contains(frame.topLeft()), (area, frame))
        self.assertLessEqual(frame.right(), area.right())
        self.assertLessEqual(frame.bottom(), area.bottom())

    def test_own_size_window_that_fits_is_left_alone(self) -> None:
        window = self.window()
        area = window.screen().availableGeometry()
        if window.minimumWidth() > area.width() - 20 or window.minimumHeight() > area.height() - 20:
            window.setMinimumSize(200, 200)
            window.resize(300, 300)
        window.move(area.left() + 10, area.top() + 10)
        settle()
        before = window.frameGeometry()
        if not area.contains(before):
            self.skipTest("the offscreen screen is smaller than the window's least size")
        self.assertTrue(window.refit())
        self.assertEqual(window.frameGeometry(), before)

    def test_hidden_or_minimised_window_is_not_touched(self) -> None:
        window = self.window()
        window.hide()
        settle()
        self.assertFalse(window.refit())
        window.screen_changed()
        self.assertFalse(window._refit_timer.isActive())
        window.show()
        window.showMinimized()
        settle()
        self.assertFalse(window.refit())
        self.assertEqual(window.refits, 0)

    def test_a_screen_change_refits_once_it_settles(self) -> None:
        window = self.window()
        window.showMaximized()
        settle()
        self.assertFalse(window._refit_timer.isActive())     # showing it changed nothing yet
        for _ in range(3):                                    # several messages come together
            window.screen_changed()
        self.assertTrue(window._refit_timer.isActive())
        wait(ui.REFIT_DELAY_MS + 300)
        self.assertEqual(window.refits, 1)
        self.assertTrue(window.windowState() & Qt.WindowState.WindowMaximized)

    def test_screen_signals_are_watched(self) -> None:
        window = self.window()
        screen = window.screen()
        self.assertTrue(any(seen is screen for seen in window._watched_screens))
        screen.logicalDotsPerInchChanged.emit(120.0)
        self.assertTrue(window._refit_timer.isActive())

    def test_the_log_line_holds_no_geometry(self) -> None:
        window = self.window()
        window.showMaximized()
        settle()
        with self.assertLogs("briefing_reader.ui", level="INFO") as logs:
            window.refit()
        line = " ".join(logs.output)
        self.assertIn("re-fitted", line)
        self.assertNotRegex(line, r"\d{3,}")

    def test_contents_are_inside_the_window_after_a_refit(self) -> None:
        window = self.window()
        window.showMaximized()
        settle()
        window.refit()
        settle()
        header = window.reading.header
        corner = header.mapTo(window, QPoint(header.width(), header.height()))
        self.assertLessEqual(corner.x(), window.width())


if __name__ == "__main__":
    unittest.main()
