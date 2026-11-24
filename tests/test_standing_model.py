"""Tests for standing.model and the [standing] config table: definitions round-trip, every malformed part is
refused with a message that names the part but never the owner's words, a rule goes live only after its dry run,
titles are templates of the rule's own fields, tidy-ups name only a user label; [standing] keeps the owner's
numbers (30 runs a day, 6 per agent, 8 mails a run, 30-minute intervals at the shortest, a 5-minute mail check)."""

from __future__ import annotations

import copy
import unittest

from briefing_reader.config import STANDING_ENABLED_ENV, StandingConfig, _parse_standing
from briefing_reader.standing import model
from briefing_reader.standing.model import DefinitionError

SECRET_WORDS = "brief me on Quillfeather mail"

AGENT = {
    "id": "a-0123456789ab", "name": "Professors' mail", "words": SECRET_WORDS,
    "trigger": {"kind": "time", "times": ["08:00"], "weekdays": ["mon", "tue", "wed", "thu", "fri"]},
    "mail": [{"account": "work", "query": "from:prof@example.edu newer_than:1d"}],
    "calendar_days": 1, "notes": False, "proposes": ["todo", "reply"], "report": "when_needed",
    "runs_per_day": 2, "status": "live", "created": "2026-11-12T18:00:00-08:00",
    "bindings": [["work", "1234567890"]],
}

RULE = {
    "id": "r-0123456789ab", "name": "Invoices", "sentence": "when an invoice from Acme comes, make a to-do",
    "account": "personal", "query": "from:billing@acme.example subject:invoice",
    "question": "Does this mail ask the owner to pay an invoice?",
    "fields": [{"name": "amount", "type": "money", "hint": "the amount due"},
               {"name": "due", "type": "date", "hint": "the due date"},
               {"name": "vendor", "type": "text"}],
    "action": {"kind": "todo", "title": "Pay {vendor} {amount}", "due": "due"},
    "tidy": {"kind": "star"}, "checks_per_day": 20, "status": "live",
    "dry_run": {"at": "2026-11-12T18:05:00-08:00", "matched": 6, "window_days": 30, "judged": 6, "yes": 4},
}


def broken(base: dict, **changes) -> dict:
    data = copy.deepcopy(base)
    for key, value in changes.items():
        if value is KeyError:
            data.pop(key, None)
        else:
            data[key] = value
    return data


class AgentTests(unittest.TestCase):

    def test_an_agent_round_trips(self) -> None:
        agent = model.agent_from(AGENT)
        self.assertEqual(model.agent_from(model.to_data(agent)), agent)
        self.assertEqual(agent.trigger.weekdays, (0, 1, 2, 3, 4))
        self.assertEqual(agent.accounts, ("work",))
        self.assertNotIn("Quillfeather", repr(agent))

    def test_malformed_parts_are_refused_without_their_words(self) -> None:
        cases = [
            broken(AGENT, id="a-XYZ"),
            broken(AGENT, name=""),
            broken(AGENT, words="x" * 601),
            broken(AGENT, words="brief me\x07 now"),
            broken(AGENT, words="brief​ me"),
            broken(AGENT, trigger={"kind": "time", "times": []}),
            broken(AGENT, trigger={"kind": "time", "times": ["24:00"]}),
            broken(AGENT, trigger={"kind": "every", "every_minutes": 15}),
            broken(AGENT, trigger={"kind": "mail"}, mail=[]),
            broken(AGENT, mail=[], calendar_days=0, notes=False),
            broken(AGENT, proposes=["email"]),
            broken(AGENT, status="tried"),
            broken(AGENT, runs_per_day=0),
            broken(AGENT, mail=[{"account": "Work Account", "query": "from:a@b.c"}]),
            broken(AGENT, bindings=[["work", "not a sub!"]]),
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(DefinitionError) as caught:
                model.agent_from(data)
            self.assertNotIn("Quillfeather", str(caught.exception))

    def test_seven_weekdays_mean_every_day_and_names_read_as_days(self) -> None:
        agent = model.agent_from(broken(AGENT, trigger={"kind": "time", "times": ["09:30", "08:00", "08:00"],
                                                        "weekdays": [0, 1, 2, 3, 4, 5, "sunday"]}))
        self.assertEqual(agent.trigger.weekdays, ())
        self.assertEqual(agent.trigger.times, ("08:00", "09:30"))

    def test_an_interval_respects_the_configured_floor(self) -> None:
        data = broken(AGENT, trigger={"kind": "every", "every_minutes": 45})
        self.assertEqual(model.agent_from(data).trigger.every_minutes, 45)
        with self.assertRaises(DefinitionError):
            model.agent_from(data, min_every=60)


class RuleTests(unittest.TestCase):

    def test_a_rule_round_trips(self) -> None:
        rule = model.rule_from(RULE)
        self.assertEqual(model.rule_from(model.to_data(rule)), rule)
        self.assertEqual(rule.trigger.kind, model.TRIGGER_MAIL)
        self.assertEqual(rule.mail, (model.MailSource("personal", RULE["query"]),))
        self.assertAlmostEqual(rule.dry_run.yes_rate, 4 / 6)
        self.assertNotIn("Acme", repr(rule))

    def test_a_rule_goes_live_only_after_its_dry_run(self) -> None:
        with self.assertRaises(DefinitionError):
            model.rule_from(broken(RULE, dry_run=None))
        draft = model.rule_from(broken(RULE, dry_run=None, status="draft"))
        with self.assertRaises(DefinitionError):
            model.with_status(draft, model.STATUS_LIVE)
        self.assertEqual(model.with_status(model.rule_from(RULE), model.STATUS_PAUSED).status, "paused")

    def test_malformed_rule_parts_are_refused(self) -> None:
        action = RULE["action"]
        cases = [
            broken(RULE, id="a-0123456789ab"),
            broken(RULE, fields=[{"name": "Amount", "type": "money"}]),
            broken(RULE, fields=[{"name": "amount", "type": "currency"}]),
            broken(RULE, fields=[{"name": "a", "type": "text"}, {"name": "a", "type": "date"}]),
            broken(RULE, fields=[{"name": f"f{n}", "type": "text"} for n in range(6)]),
            broken(RULE, action={**action, "title": "Pay {payee}"}),
            broken(RULE, action={**action, "title": "Pay {vendor"}),
            broken(RULE, action={**action, "title": "Pay {{vendor}}"}),
            broken(RULE, action={**action, "due": "vendor"}),
            broken(RULE, action={"kind": "event", "title": "Call", "start": "due"}),
            broken(RULE, action={"kind": "todo", "title": ""}),
            broken(RULE, action={"kind": "todo", "title": "Pay", "reply_hint": "say thanks"}),
            broken(RULE, action={"kind": "event", "title": "Call", "start": "due", "minutes": 5}),
            broken(RULE, tidy={"kind": "label", "label_id": "INBOX"}),
            broken(RULE, tidy={"kind": "label", "label_id": ""}),
            broken(RULE, tidy={"kind": "star", "label_id": "Label_12"}),
            broken(RULE, tidy={"kind": "trash"}),
            broken(RULE, query=""),
            broken(RULE, question="x" * 201),
            broken(RULE, dry_run={"at": "2026-11-12T18:05:00", "judged": 2, "yes": 3}),
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(DefinitionError) as caught:
                model.rule_from(data)
            self.assertNotIn("Acme", str(caught.exception))

    def test_an_event_and_a_label_tidy_up(self) -> None:
        rule = model.rule_from(broken(RULE, fields=[{"name": "when", "type": "datetime"}],
                                      action={"kind": "event", "title": "Interview", "start": "when", "minutes": 45},
                                      tidy={"kind": "label", "label_id": "Label_42", "label_name": "Interviews"}))
        self.assertEqual((rule.action.start, rule.action.minutes), ("when", 45))
        self.assertEqual(rule.tidy.label_id, "Label_42")

    def test_a_tell_me_rule_needs_no_fields(self) -> None:
        rule = model.rule_from(broken(RULE, fields=[], action={"kind": "tell"}, tidy=None))
        self.assertEqual((rule.action.kind, rule.tidy.kind), ("tell", ""))

    def test_render_joins_the_checked_values(self) -> None:
        self.assertEqual(model.render("Pay {vendor} {amount}", {"vendor": "Acme", "amount": "$120.00"}),
                         "Pay Acme $120.00")
        self.assertEqual(model.render("Pay {vendor} {amount}", {"amount": "$5"}), "Pay $5")

    def test_new_ids(self) -> None:
        self.assertRegex(model.new_id("agent"), r"a-[0-9a-f]{12}")
        self.assertRegex(model.new_id("rule"), r"r-[0-9a-f]{12}")
        with self.assertRaises(ValueError):
            model.new_id("task")


class StandingConfigTests(unittest.TestCase):

    def test_the_defaults_are_the_owner_s_answers(self) -> None:
        d = StandingConfig()
        self.assertEqual((d.enabled, d.model, d.rule_model), (False, "sonnet", "haiku"))
        self.assertEqual((d.max_runs_per_day, d.agent_runs_per_day, d.rule_batch), (30, 6, 8))
        self.assertEqual((d.poll_minutes, d.min_every_minutes, d.keep_days), (5, 30, 7))

    def test_env_turns_it_on_and_ranges_hold(self) -> None:
        doc = {"standing": {"min_every_minutes": 15, "poll_minutes": 2, "rule_batch": 20, "quiet_from": "23:30"}}
        with self.assertLogs("briefing_reader.config", "WARNING"):
            parsed = _parse_standing(doc, {STANDING_ENABLED_ENV: "true"})
        self.assertTrue(parsed.enabled)
        self.assertEqual((parsed.min_every_minutes, parsed.poll_minutes, parsed.rule_batch), (30, 5, 8))
        self.assertEqual(parsed.quiet_from, "23:30")
        self.assertFalse(_parse_standing({"standing": {"enabled": True}}, {STANDING_ENABLED_ENV: "no"}).enabled)

    def test_an_unknown_key_is_reported(self) -> None:
        with self.assertLogs("briefing_reader.config", "WARNING") as logs:
            _parse_standing({"standing": {"web": True}})
        self.assertTrue(any("standing.web" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
