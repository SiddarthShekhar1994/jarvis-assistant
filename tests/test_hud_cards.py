"""Tests for the NEEDS YOUR OK card texts that come from the page.

Tooltips never read page text as HTML (Qt guesses the format of a tooltip, so
'<img src=...>' in a subject would be fetched on a hover): the cards, the speech
line and the agenda, deadline and section rows. Long email addresses wrap
inside the card, a long alias is cut with an ellipsis, a long draft's tooltip
is capped and the BLOCK ADDED link can be named. The RSVP / Move / Cancel card
states (countdown with Undo, sent, unknown with Retry, the reserved check line,
Edit / Sign in / Copy links) never move a card's buttons, and the Edit dialog
returns exactly what was typed and reads nothing as HTML. Qt runs offscreen in
this process with the bundled fonts and shows nothing; every text is invented.
"""

from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

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


class RowTooltipTests(unittest.TestCase):
    """Agenda, deadline and section rows show an event's or a page's title as plain text."""

    def assert_plain(self, widget, text: str) -> None:
        tip = widget.toolTip()
        self.assertNotIn("<img", tip)
        self.assertEqual(as_shown(tip), text)

    def test_an_agenda_row_tooltip_is_never_html(self) -> None:
        row = hud.AgendaRow(hud.AgendaRowInfo("9:00 AM", IMG + " Speaker series", "Room <b>204</b>"))
        self.assert_plain(row, IMG + " Speaker series\nRoom <b>204</b>")
        row.set_info(hud.AgendaRowInfo("ALL DAY", "<i>Holiday</i> & more"))
        self.assert_plain(row, "<i>Holiday</i> & more")
        row.close()

    def test_a_deadline_row_tooltip_is_never_html(self) -> None:
        row = hud.DeadlineRow(hud.DeadlineRowInfo(IMG + " Problem set 3", "Canvas <u>course</u>", "Tomorrow"))
        self.assert_plain(row, IMG + " Problem set 3\nCanvas <u>course</u>\nDue: Tomorrow")
        row.close()

    def test_a_section_row_tooltip_is_never_html(self) -> None:
        row = hud.SectionRow(hud.SectionRowInfo(IMG + " Still needs a reply", "3 items"), "01")
        self.assert_plain(row, IMG + " Still needs a reply")
        row.set_info(hud.SectionRowInfo("<b>Deadlines</b>"))
        self.assert_plain(row, "<b>Deadlines</b>")
        row.close()


class CountdownCardTests(unittest.TestCase):
    """The RSVP / Move / Cancel card: nothing above the buttons moves when its state changes."""

    def make(self, **options):
        defaults = {"approve_text": "Accept", "open_text": "Open event", "edit_text": "Edit",
                    "sign_in_text": "Sign in", "check_line": True}
        defaults.update(options)
        card = hud.ActionCard("c1", "rsvp \u00b7 work", "Speaker series", "Answer: yes \u00b7 Tue Oct 6",
                              **defaults)
        shown(card)
        return card

    def test_states_keep_the_buttons_in_place(self) -> None:
        card = self.make()
        deny_x = card.deny_button.mapTo(card, card.deny_button.rect().topLeft()).x()
        height = card.height()
        card.set_check_line("Google: " + IMG + " Speaker series \u00b7 Tue Oct 6 \u00b7 5:00-6:00 PM \u00b7 "
                            "organized by someone else \u00b7 you have not answered")
        card.set_sign_in_visible(True)
        card.set_approve_text("Retry")
        QApplication.processEvents()
        self.assertEqual(card.check_line()[:8], "Google: ")
        self.assertNotIn("<img", card.check_label.toolTip())
        self.assertEqual(card.height(), height)   # the check line was reserved from the start
        self.assertEqual(card.deny_button.mapTo(card, card.deny_button.rect().topLeft()).x(), deny_x)
        self.assertEqual(card.approve_text(), "Retry")
        card.set_status(hud.CARD_COUNTDOWN, "Sending in 9 s")
        QApplication.processEvents()
        self.assertTrue(card.undo_button.isVisible())
        self.assertFalse(card.deny_button.isVisible())
        self.assertEqual(card.result_text(), "SENDING IN 9 S")
        self.assertEqual(card.result_label.full_text(), "SENDING IN 9\u00a0S")   # "S" never wraps alone
        self.assertEqual(card.height(), height)
        card.set_status(hud.CARD_UNKNOWN, "check the calendar before retrying")
        QApplication.processEvents()
        self.assertFalse(card.undo_button.isVisible())
        self.assertTrue(card.deny_button.isVisible() and card.approve_button.isVisible())
        self.assertEqual(card.failure_label.full_text(), "UNKNOWN: CHECK THE CALENDAR BEFORE RETRYING")
        card.set_status(hud.CARD_SENT, "Accepted", "https://www.google.com/calendar/event?eid=x", "Open event")
        QApplication.processEvents()
        self.assertEqual((card.result_text(), card.open_button.text()), ("ACCEPTED", "Open event"))
        self.assertTrue(card.open_button.isVisible())
        card.close()

    def test_undo_edit_and_sign_in_signals_carry_the_id(self) -> None:
        card = self.make()
        got: list[tuple[str, str]] = []
        card.undoClicked.connect(lambda action_id: got.append(("undo", action_id)))
        card.editClicked.connect(lambda action_id: got.append(("edit", action_id)))
        card.signInClicked.connect(lambda action_id: got.append(("sign in", action_id)))
        card.edit_button.click()
        card.set_sign_in_visible(True)
        card.sign_in_button.click()
        card.set_status(hud.CARD_COUNTDOWN, "Sending in 3 s")
        card.undo_button.click()
        self.assertEqual(got, [("edit", "c1"), ("sign in", "c1"), ("undo", "c1")])
        card.set_edit_enabled(False)
        self.assertFalse(card.edit_button.isEnabled())
        card.close()

    def test_a_long_button_fits_a_narrow_card(self) -> None:
        card = self.make(approve_text="Cancel event")
        wide = card.approve_button.width()
        card.resize(212, 400)
        QApplication.processEvents()
        button = card.approve_button
        right = button.mapTo(card, button.rect().topRight()).x()
        self.assertLessEqual(right, card.width() - 1)   # inside the card, the text whole
        self.assertGreaterEqual(button.width(), button.tight_width("Cancel event"))
        self.assertLess(button.width(), wide)
        deny_x = card.deny_button.mapTo(card, card.deny_button.rect().topLeft()).x()
        card.set_approve_text("Done")
        QApplication.processEvents()
        self.assertEqual(card.deny_button.mapTo(card, card.deny_button.rect().topLeft()).x(), deny_x)
        card.close()

    def test_the_tools_slot_switches_without_moving_anything(self) -> None:
        card = self.make(open_text="", copy_text="Copy note", event_text="Open event")
        card.resize(212, 400)   # the narrowest card (760 px window)
        QApplication.processEvents()
        self.assertEqual(card.tool_slot(), hud.TOOL_EDIT)
        self.assertTrue(card.edit_button.isVisible() and card.sign_in_button.isHidden()
                        and card.event_button.isHidden())
        place, height = card.edit_button.geometry(), card.height()
        deny = card.deny_button.mapTo(card, card.deny_button.rect().topLeft())
        got: list[tuple[str, str]] = []
        card.sourceClicked.connect(lambda action_id: got.append(("open", action_id)))
        card.signInClicked.connect(lambda action_id: got.append(("sign in", action_id)))
        for mode, button in ((hud.TOOL_SIGN_IN, card.sign_in_button), (hud.TOOL_OPEN, card.event_button),
                             (hud.TOOL_EDIT, card.edit_button)):
            with self.subTest(mode=mode):
                card.set_tool_slot(mode)
                QApplication.processEvents()
                self.assertEqual(card.tool_slot(), mode)
                self.assertTrue(button.isVisible())
                self.assertEqual(sum(b.isVisible() for b in (card.edit_button, card.sign_in_button,
                                                             card.event_button)), 1)
                self.assertEqual(button.geometry(), place)   # the same place, the same width
                self.assertEqual(card.height(), height)
                self.assertEqual(card.deny_button.mapTo(card, card.deny_button.rect().topLeft()), deny)
                if button is not card.edit_button:
                    button.click()
        self.assertEqual(got, [("sign in", "c1"), ("open", "c1")])
        card.set_edit_enabled(False)   # another card's countdown: no dialog, no sign-in beside it
        self.assertFalse(card.edit_button.isEnabled() or card.sign_in_button.isEnabled())
        self.assertTrue(card.event_button.isEnabled())
        plain = self.make()            # built without "Open event": that mode shows Edit
        plain.set_tool_slot(hud.TOOL_OPEN)
        self.assertEqual(plain.tool_slot(), hud.TOOL_EDIT)
        card.close()
        plain.close()

    def test_skip_names_the_left_hand_button(self) -> None:
        card = self.make(deny_text="Skip", approve_text="Decline")
        self.assertEqual((card.deny_button.text(), card.approve_text()), ("Skip", "Decline"))
        card.set_status(hud.CARD_DENIED, "Skipped")
        self.assertEqual(card.result_text(), "SKIPPED")
        card.close()

    def test_three_check_lines_show_googles_title_and_time_at_760(self) -> None:
        card = self.make(approve_text="Cancel event")
        card.resize(212, 400)
        QApplication.processEvents()
        text = ("Google: Quarterly planning review with the\u2026 \u00b7 Thu Oct 8 \u00b7 12:00-1:00 PM \u00b7 "
                "organized by Ana Example \u00b7 you haven't answered \u00b7 as you@example.edu")
        card.set_check_line(text)
        QApplication.processEvents()
        self.assertIn("12:00-1:00 PM", card.check_label.text())   # title and time are never cut
        self.assertEqual(card.check_label.full_text(), text)

    def test_an_edited_kind_label_wraps_instead_of_being_cut(self) -> None:
        card = hud.ActionCard("c2", "cancel \u00b7 personal", "Study group", "Fri Oct 9", approve_text="Cancel event",
                              edit_text="Edit", check_line=True)
        shown(card, 212)
        height = card.height()
        card.set_texts("cancel \u00b7 personal \u00b7 edited", "Study group", "Fri Oct 9 \u00b7 guests notified")
        QApplication.processEvents()
        self.assertNotIn("\u2026", card.kind_label.text())
        self.assertEqual(card.kind_label.text().replace("\n", " "), "CANCEL \u00b7 PERSONAL \u00b7 EDITED")
        card.set_texts("cancel \u00b7 personal", "Study group", "Fri Oct 9")
        QApplication.processEvents()
        self.assertGreaterEqual(card.height(), height)   # it never gets shorter again
        card.close()

    def test_undo_keeps_the_keyboard_focus_on_its_card(self) -> None:
        host = QWidget()
        first = hud.ActionCard("u1", "rsvp \u00b7 work", "Speaker series", "Answer: yes", approve_text="Accept",
                               edit_text="Edit", check_line=True, parent=host)
        second = hud.ActionCard("u2", "move \u00b7 work", "Project sync", "New time", approve_text="Move",
                                open_text="Open event", edit_text="Edit", check_line=True, parent=host)
        second.move(0, 400)
        shown(host, 300)
        host.activateWindow()
        first.set_status(hud.CARD_COUNTDOWN, "Sending in 9 s")
        QApplication.processEvents()
        first.undo_button.setFocus(Qt.FocusReason.TabFocusReason)
        QApplication.processEvents()
        if QApplication.focusWidget() is first.undo_button:   # offscreen focus may be unavailable
            first.set_status(hud.CARD_PENDING)   # Undo pressed: the buttons are back
            QApplication.processEvents()
            focus = QApplication.focusWidget()
            self.assertFalse(focus is not None and second.isAncestorOf(focus), focus)
        host.close()

    def test_copy_can_be_added_after_an_edit(self) -> None:
        card = self.make(approve_text="Move")
        self.assertFalse(card.copy_button.isVisible())
        edit_x = card.edit_button.x()
        card.set_copy_text("Copy note")
        card.set_body("Moving to 2 PM so everyone can join.")
        QApplication.processEvents()
        self.assertTrue(card.copy_button.isVisible())
        self.assertEqual(card.copy_text(), "Copy note")
        card.show_copied()
        self.assertEqual(card.copy_button.text(), "Copied")
        self.assertGreaterEqual(card.edit_button.x(), edit_x)
        card.set_copy_text("")
        self.assertFalse(card.copy_button.isVisible())
        card.close()


class EditDialogTests(unittest.TestCase):

    def test_values_are_what_was_typed(self) -> None:
        dialog = hud.EditDialog("m1", hud.EDIT_MOVE, "move \u00b7 work", IMG + " Project sync",
                                notify="all", date_text="2026-10-08", start_text="14:00", end_text="15:00",
                                note="Moving to 2 PM.", check_text="Google: <b>Project sync</b>")
        self.assertEqual(dialog.title_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(dialog.check_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(dialog.check_label.text(), "Google: <b>Project sync</b>")
        dialog.end_edit.setText(" 15:30 ")
        dialog.notify_box.setChecked(False)
        got: list = []
        dialog.saved.connect(lambda action_id, values: got.append((action_id, values)))
        dialog.save_button.click()
        self.assertEqual(got, [("m1", {"answer": "yes", "notify": "none", "date": "2026-10-08", "start": "14:00",
                                       "end": "15:30", "note": "Moving to 2 PM."})])
        dialog.show_error("the new time must end 5 minutes to 12 hours after it starts")
        self.assertTrue(dialog.error().startswith("the new time"))
        dialog.close()

    def test_enter_in_a_field_saves_and_times_follow_the_clock(self) -> None:
        dialog = hud.EditDialog("m2", hud.EDIT_MOVE, "move \u00b7 work", "Project sync", date_text="2026-10-08",
                                start_text="2:00 PM", end_text="3:00 PM")
        got: list = []
        dialog.saved.connect(lambda action_id, values: got.append(values))
        self.assertTrue(dialog.save_button.isDefault())
        self.assertEqual(dialog.start_edit.placeholderText(), "2:00 PM")
        dialog.show()
        QApplication.processEvents()
        dialog.end_edit.setFocus()
        QTest.keyClick(dialog.end_edit, Qt.Key.Key_Return)
        self.assertEqual([(v["start"], v["end"]) for v in got], [("2:00 PM", "3:00 PM")])
        dialog.close()
        hour24 = hud.EditDialog("m3", hud.EDIT_MOVE, "move", "Project sync", hour24=True)
        self.assertEqual(hour24.start_edit.placeholderText(), "14:00")
        hour24.close()

    def test_rsvp_notify_says_who_is_emailed(self) -> None:
        dialog = hud.EditDialog("r2", hud.EDIT_RSVP, "rsvp \u00b7 work", "Speaker series", notify="all")
        self.assertEqual(dialog.notify_box.text(), "Email the organizer about my answer")
        outside = hud.EditDialog("r3", hud.EDIT_RSVP, "rsvp \u00b7 work", "Speaker series", notify="external")
        self.assertEqual(outside.notify_box.text(), "Email the organizer only if outside your organization")
        dialog.close()
        outside.close()

    def test_an_rsvp_answer_is_picked_with_one_button(self) -> None:
        dialog = hud.EditDialog("r1", hud.EDIT_RSVP, "rsvp \u00b7 work", "Speaker series", answer="yes",
                                notify="external")
        dialog.answer_buttons["maybe"].click()
        values = dialog.values()
        self.assertEqual((values["answer"], values["notify"]), ("maybe", "external"))
        self.assertTrue(dialog.answer_buttons["maybe"].isChecked())
        self.assertFalse(dialog.answer_buttons["yes"].isChecked())
        self.assertTrue(dialog.date_edit.isHidden())
        with self.assertRaises(ValueError):
            hud.EditDialog("x", "reply", "reply", "Re: Notes")
        dialog.close()


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
