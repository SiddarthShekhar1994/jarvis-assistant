"""Documents and datasheets, the model (part 1): ids, typed values and what a cell shows, columns, markdown sections and
the one splice that leaves every other byte as it was. No Qt, no files."""

from __future__ import annotations

import unittest

from briefing_reader.docs import model


def col(name: str, type_: str, choices: str = "") -> model.Column:
    column, reason = model.check_column(name, type_, choices)
    assert column is not None, reason
    return column


class IdTests(unittest.TestCase):
    def test_ids_and_kinds(self) -> None:
        doc, sheet = model.new_id(model.KIND_DOC), model.new_id(model.KIND_SHEET)
        self.assertRegex(doc, r"^d-[0-9a-f]{12}$")
        self.assertRegex(sheet, r"^s-[0-9a-f]{12}$")
        self.assertEqual((model.kind_of(doc), model.kind_of(sheet)), (model.KIND_DOC, model.KIND_SHEET))
        for bad in ("d-123", "x-0123456789ab", "../d-0123456789ab", "", None, 5):
            self.assertEqual(model.kind_of(bad), "")  # type: ignore[arg-type]
        self.assertTrue(model.valid_row_id(model.new_row_id()))
        self.assertTrue(model.valid_column_id(model.new_column_id()))
        self.assertFalse(model.valid_row_id("r-zz"))


class ValueTests(unittest.TestCase):
    def test_text(self) -> None:
        c = col("Note", model.TYPE_TEXT)
        self.assertEqual(model.parse_value(c, "  two\nlines\u200b "), ("two lines", ""))
        self.assertEqual(model.parse_value(c, ""), (None, ""))
        self.assertEqual(model.parse_value(c, "x" * 501), (None, model.REASON_TOO_LONG))
        self.assertEqual(model.parse_value(c, "x" * 500)[1], "")

    def test_number(self) -> None:
        c = col("Pay", model.TYPE_NUMBER)
        self.assertEqual(model.parse_value(c, "1,250"), (1250, ""))
        self.assertEqual(model.parse_value(c, "$12.50"), (12.5, ""))
        self.assertEqual(model.parse_value(c, 3), (3, ""))
        self.assertEqual(model.parse_value(c, 2.0), (2, ""))
        for bad in ("twelve", "1.2.3", "nan", "inf", True, float("nan"), "1e400"):
            self.assertEqual(model.parse_value(c, bad), (None, model.REASON_NOT_NUMBER), bad)
        self.assertEqual(model.show_value(c, 1250), "1,250")
        self.assertEqual(model.edit_value(c, 1250), "1250")
        self.assertEqual(model.csv_value(c, 12.5), "12.5")

    def test_date(self) -> None:
        c = col("Deadline", model.TYPE_DATE)
        self.assertEqual(model.parse_value(c, "2026-12-01"), ("2026-12-01", ""))
        self.assertEqual(model.parse_value(c, "12/1/2026"), ("2026-12-01", ""))
        self.assertEqual(model.parse_value(c, "2026-12-01T10:00"), ("2026-12-01", ""))
        for bad in ("2026-13-01", "Dec 1", "2026-12-01xyz", "2026-02-30"):
            self.assertEqual(model.parse_value(c, bad), (None, model.REASON_NOT_DATE), bad)

    def test_choice(self) -> None:
        c = col("Status", model.TYPE_CHOICE, "Open, Applied, Interview, open")
        self.assertEqual(c.choices, ("Open", "Applied", "Interview"))
        self.assertEqual(model.parse_value(c, "applied"), ("Applied", ""))
        self.assertEqual(model.parse_value(c, "Rejected"), (None, model.REASON_NOT_CHOICE))

    def test_checkbox(self) -> None:
        c = col("Done", model.TYPE_CHECKBOX)
        self.assertEqual(model.parse_value(c, True), (True, ""))
        self.assertEqual(model.parse_value(c, "yes"), (True, ""))
        self.assertEqual(model.parse_value(c, "no"), (False, ""))
        self.assertEqual(model.parse_value(c, "maybe"), (None, model.REASON_NOT_CHECKBOX))
        self.assertEqual(model.csv_value(c, True), "TRUE")
        self.assertEqual(model.csv_value(c, None), "FALSE")

    def test_link(self) -> None:
        c = col("Page", model.TYPE_LINK)
        self.assertEqual(model.parse_value(c, "https://example.com/jobs"), ("https://example.com/jobs", ""))
        for bad in ("javascript:alert(1)", "file:///C:/x", "example.com", "https://", "ftp://example.com"):
            self.assertEqual(model.parse_value(c, bad), (None, model.REASON_NOT_LINK), bad)

    def test_a_value_of_another_type_is_refused_not_turned(self) -> None:
        self.assertEqual(model.parse_value(col("Pay", model.TYPE_NUMBER), True)[1], model.REASON_NOT_NUMBER)
        self.assertEqual(model.parse_value(col("When", model.TYPE_DATE), False)[1], model.REASON_NOT_DATE)

    def test_stored_values_are_checked_again(self) -> None:
        c = col("Deadline", model.TYPE_DATE)
        self.assertIsNone(model.stored_value(c, "not a date"))
        self.assertEqual(model.stored_value(c, "2026-12-01"), "2026-12-01")


class ColumnTests(unittest.TestCase):
    def test_check_column(self) -> None:
        self.assertEqual(model.check_column("", "text")[1], model.REASON_NO_NAME)
        self.assertEqual(model.check_column("A", "formula")[1], model.REASON_BAD_TYPE)
        self.assertEqual(model.check_column("Status", "choice", "")[1], model.REASON_NO_CHOICES)
        self.assertEqual(model.check_column("status", "text", taken=("Status",))[1], model.REASON_SAME_NAME)
        many = ",".join(f"c{i}" for i in range(21))
        self.assertEqual(model.check_column("S", "choice", many)[1], model.REASON_TOO_MANY)
        column, _ = model.check_column("x" * 60, "text")
        assert column is not None
        self.assertEqual(len(column.name), model.MAX_COLUMN_NAME)

    def test_round_trip(self) -> None:
        c = col("Status", model.TYPE_CHOICE, "Open, Applied")
        self.assertEqual(model.column_from(model.column_data(c)), c)
        self.assertIsNone(model.column_from({"id": "c-12", "name": "x", "type": "text"}))
        self.assertIsNone(model.column_from({"id": c.id, "name": "x", "type": "choice", "choices": []}))


TEXT = """Intro line.

# Plan

Step one.

```
# not a heading inside code
```

## Next week

- call Ana

# Notes

Last.
"""


class SectionTests(unittest.TestCase):
    def test_headings_outside_code_with_section_ends(self) -> None:
        found = model.headings(TEXT)
        self.assertEqual([(h.level, h.title) for h in found], [(1, "Plan"), (2, "Next week"), (1, "Notes")])
        plan = found[0]
        self.assertEqual(TEXT[plan.start:plan.end].split("\n")[0], "# Plan")
        self.assertIn("## Next week", TEXT[plan.start:plan.end])        # a sub-heading stays in its section
        self.assertTrue(TEXT[plan.end:].startswith("# Notes"))

    def test_find_by_name_folds(self) -> None:
        self.assertEqual(len(model.find_headings(TEXT, "  next WEEK: ")), 1)
        self.assertEqual(model.find_headings(TEXT, "nope"), [])
        self.assertEqual(model.find_headings(TEXT, ""), [])

    def test_replace_keeps_every_other_byte(self) -> None:
        heading = model.find_headings(TEXT, "Next week")[0]
        out = model.replace_section(TEXT, heading, "## Next week\n\n- call Ana\n- book the room")
        self.assertTrue(out.startswith(TEXT[:heading.start]))
        self.assertTrue(out.endswith(TEXT[heading.end:]))
        self.assertIn("- book the room", out)

    def test_replace_with_nothing_removes(self) -> None:
        heading = model.find_headings(TEXT, "Notes")[0]
        out = model.replace_section(TEXT, heading, "")
        self.assertEqual(out, TEXT[:heading.start])

    def test_add_after_a_heading_and_at_the_end(self) -> None:
        plan = model.find_headings(TEXT, "Plan")[0]
        out = model.add_section(TEXT, "## Budget\n\n$200", plan)
        self.assertTrue(out.startswith(TEXT[:plan.end]))
        self.assertTrue(out.endswith(TEXT[plan.end:]))
        self.assertLess(out.index("## Budget"), out.index("# Notes"))
        end = model.add_section(TEXT, "# Later\nmore")
        self.assertTrue(end.startswith(TEXT))
        self.assertTrue(end.endswith("# Later\nmore\n"))
        self.assertEqual(model.add_section("", "# First"), "# First\n")

    def test_plain_text_and_words(self) -> None:
        plain = model.plain_text("# Title\n\nSome **bold** and [a link](https://x.example) ![pic](p.png)\n- item")
        self.assertEqual(plain, "Title\n\nSome bold and a link \n\u2022 item")
        self.assertEqual(model.word_count("# Two words"), 2)

    def test_clean_doc_text(self) -> None:
        self.assertEqual(model.clean_doc_text("a\r\nb\rc\x00d\te"), "a\nb\ncd\te")


if __name__ == "__main__":
    unittest.main()
