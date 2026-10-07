"""Tests for the NEEDS YOUR OK card texts that come from the page.

Tooltips never read page text as HTML (Qt guesses the format of a tooltip, so
'<img src=...>' in a subject would be fetched on a hover), long email addresses
wrap inside the card, a long alias is cut with an ellipsis, a long draft's
tooltip is capped and the BLOCK ADDED link can be named. Qt runs offscreen in
this process with the bundled fonts and shows nothing; every text is invented.
"""

from __future__ import annotations

import unittest

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication

from briefing_reader import config, hud

_app: QApplication | None = None
IMG = '<img src="file://203.0.113.9/share/x.png">'
LONG_TITLE = (IMG + " Work on problem set three, chapter four exercises, and the lab write-up for the "
              "chemistry course before the review session")


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_hud_cards", "-platform", "offscreen"])
    hud.load_fonts(config.PROJECT_ROOT / "fonts")


def as_shown(tooltip: str) -> str:
    """What a tooltip shows: Qt reads it as HTML when it looks like HTML."""
    doc = QTextDocument()
    doc.setHtml(tooltip)
    return doc.toPlainText()


def shown(widget, width: int = 300) -> None:
    widget.resize(width, 400)
    widget.show()
    QApplication.processEvents()


class TooltipTests(unittest.TestCase):

    def test_plain_tooltip_shows_markup_as_text(self) -> None:
        tip = hud.plain_tooltip(IMG + " a & b\nnext line")
        self.assertNotIn("<img", tip)
        self.assertEqual(as_shown(tip), IMG + " a & b\nnext line")
        self.assertEqual(hud.plain_tooltip(""), "")

    def test_a_cut_card_title_has_an_escaped_tooltip(self) -> None:
        card = hud.ActionCard("t1", "todo", LONG_TITLE, "Due Wed Oct 7", approve_text="Done", title_lines=2)
        shown(card)
        self.assertNotEqual(card.title_label.text(), LONG_TITLE)   # cut to two lines
        self.assertEqual(as_shown(card.title_label.toolTip()), LONG_TITLE)
        self.assertNotIn("<img", card.title_label.toolTip())
        card.set_status(hud.CARD_FAILED, LONG_TITLE * 2)
        QApplication.processEvents()
        self.assertNotIn("<IMG", card.failure_label.toolTip())
        self.assertEqual(as_shown(card.failure_label.toolTip()), ("FAILED: " + LONG_TITLE * 2).upper())
        card.close()

    def test_a_cut_speech_line_has_an_escaped_tooltip(self) -> None:
        speech = hud.SpeechLabel(max_lines=2)
        shown(speech)
        speech.setText(LONG_TITLE + " " + LONG_TITLE)
        QApplication.processEvents()
        self.assertNotEqual(speech.shown_text(), speech.text())
        self.assertNotIn("<img", speech.toolTip())
        self.assertEqual(as_shown(speech.toolTip()), LONG_TITLE + " " + LONG_TITLE)
        speech.close()

    def test_a_long_draft_tooltip_is_capped(self) -> None:
        body = "\n".join(["This is a fairly long paragraph of drafted reply text that goes on and on."] * 60)
        card = hud.ActionCard("t2", "reply", "Re: Notes", "To: ana@example.edu", body=body,
                              copy_text="Copy reply", approve_text="Done")
        tip = as_shown(card.body_label.toolTip())
        self.assertLess(len(tip), 1300)
        self.assertTrue(tip.endswith("\u2026\n(Copy reply for the whole text)"), tip[-60:])
        self.assertEqual(card.body(), " ".join(body.split()))   # the preview and Copy keep everything
        short = hud.ActionCard("t3", "reply", "Re: Notes", "", body="Hi,\nsee you then.", copy_text="Copy reply")
        self.assertEqual(as_shown(short.body_label.toolTip()), "Hi,\nsee you then.")
        card.close()
        short.close()


class CardTextTests(unittest.TestCase):

    def test_soft_breaks_only_inside_long_words(self) -> None:
        breaks = hud._with_soft_breaks
        self.assertEqual(breaks("To: jordan.smith@example.edu, ana@example.edu"),
                         "To: jordan.smith@example.edu, ana@example.edu")
        self.assertEqual(breaks("alexander.thompson@students.department.example.edu").split("\u200b"),
                         ["alexander", ".thompson@", "students", ".department", ".example", ".edu"])
        self.assertTrue(all(len(piece) <= 24 for piece in breaks("x" * 60).split("\u200b")))
        calendar_detail = "Fri Oct 9 \u00b7 3:00-4:00 PM \u00b7 Room 204, Engineering building"
        self.assertEqual(breaks(calendar_detail), calendar_detail)

    def test_a_long_address_wraps_inside_the_card(self) -> None:
        detail = ("To: alexander.thompson@students.department.example.edu \u00b7 "
                  "Cc: someone.with.a.really.long.address@subdomain.department.example.edu")
        card = hud.ActionCard("t4", "reply \u00b7 work", "Re: Group project", detail, approve_text="Done")
        shown(card, 210)
        label = card.detail_label
        self.assertEqual(label.text(), detail)                 # break chances are display only
        self.assertEqual(label.accessibleName(), detail)
        self.assertLessEqual(label.minimumSizeHint().width(), label.width() + 1)
        self.assertLessEqual(label.heightForWidth(label.width()), label.height() + 1)
        card.close()

    def test_a_long_alias_is_cut_with_an_ellipsis(self) -> None:
        card = hud.ActionCard("t5", "share \u00b7 a-very-long-alias-name1", "Trip budget", "sam@example.com",
                              approve_text="Done")
        shown(card, 210)
        full = "SHARE \u00b7 A-VERY-LONG-ALIAS-NAME1"
        self.assertEqual(card.kind_label.full_text(), full)
        self.assertTrue(card.kind_label.text().endswith("\u2026"), card.kind_label.text())
        self.assertTrue(card.accessibleName().startswith(f"{full}: Trip budget"))
        card.close()

    def test_the_result_link_can_be_named(self) -> None:
        card = hud.ActionCard("t6", "todo", "Read chapter 4", "Due Tue Oct 6", approve_text="Add block")
        card.set_status(hud.CARD_ADDED, "Block added", "https://www.google.com/calendar/event?eid=x", "Open event")
        self.assertEqual((card.open_button.text(), card.result_text()), ("Open event", "BLOCK ADDED"))
        card.set_status(hud.CARD_EXISTS, "", "https://www.google.com/calendar/event?eid=x")
        self.assertEqual(card.open_button.text(), "Open")
        card.close()


if __name__ == "__main__":
    unittest.main()
