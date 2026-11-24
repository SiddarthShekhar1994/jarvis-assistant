"""Radar (build plan item 6): four ready-made standing agents (internships, hackathons, deadlines, subscriptions).
Part 1: the presets are fixed data whose words, filters, definitions and sheets pass the same checks a setup's answer
does; Add makes a draft (never live) without any run, usage or network; a sheet found by name is reused read only, a
new one is planned with its key columns; Documents off refuses. Fakes only; temporary folders; every name is
invented."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from briefing_reader import standing_setup
from briefing_reader.config import StandingConfig
from briefing_reader.docs import model as docs_model
from briefing_reader.docs import store as docs_store
from briefing_reader.standing import model, radar, setup
from briefing_reader.standing.filters import check_filter
from briefing_reader.standing.store import StandingFiles
from tests.standing_fakes import agent_data

NOW_P1 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone(timedelta(hours=-8)))


def snapshot(folder: Path) -> dict:
    return {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in folder.rglob("*") if path.is_file()}


class Account:
    def __init__(self, signed: bool) -> None:
        self.signed = signed

    def is_signed_in(self, feature: str | None = None) -> bool:
        return self.signed


def config_of(data_dir: Path, features: dict[str, tuple[str, ...]], *, docs: bool = True) -> SimpleNamespace:
    return SimpleNamespace(accounts={alias: SimpleNamespace(features=can) for alias, can in features.items()},
                           docs=SimpleNamespace(enabled=docs), data_dir=data_dir, standing=StandingConfig())


READ = ("gmail_read",)
READ_CAL = ("gmail_read", "calendar")


class PresetTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)

    def make(self, preset, aliases=("work",), calendar_ok=False):  # type: ignore[no-untyped-def]
        return radar.preset_agent(preset, aliases, calendar_ok=calendar_ok, docs_dir=self.data, now=NOW_P1,
                                  runs_per_day=5)

    def test_the_four_presets_pass_setups_own_checks(self) -> None:
        self.assertEqual([p.key for p in radar.PRESETS], ["internships", "hackathons", "deadlines", "subscriptions"])
        for preset in radar.PRESETS:
            with self.subTest(preset=preset.key):
                self.assertEqual(setup.check_words(preset.words), preset.words)
                for term in preset.terms:
                    self.assertEqual(check_filter(term, preset.words), term)
                self.assertLessEqual(len(preset.name), model.MAX_NAME)
                taken: list[str] = []
                for name, type_, choices in preset.columns:
                    column, reason = docs_model.check_column(name, type_, choices, taken=tuple(taken))
                    self.assertIsNotNone(column, reason)
                    taken.append(name)
                agent = self.make(preset)
                self.assertEqual(agent.status, model.STATUS_DRAFT)
                self.assertEqual(model.live([agent]), [])
                self.assertEqual(agent.report, model.REPORT_NEEDED)
                self.assertEqual(agent.runs_per_day, 5)
                self.assertEqual(agent.words, preset.words)
                self.assertNotIn(model.PROPOSE_REPLY, agent.proposes)
                self.assertEqual(model.agent_from(model.to_data(agent)), agent)
                self.assertEqual(agent.lands, ("sheet", ""))
                self.assertEqual(agent.lands_name, preset.sheet)
                self.assertEqual(agent.lands_keys, preset.keys)

    def test_triggers_and_proposals(self) -> None:
        made = {preset.key: self.make(preset) for preset in radar.PRESETS}
        for key in ("internships", "hackathons", "subscriptions"):
            self.assertEqual(made[key].trigger.kind, model.TRIGGER_MAIL)
        self.assertEqual((made["deadlines"].trigger.kind, made["deadlines"].trigger.times,
                          made["deadlines"].trigger.weekdays), (model.TRIGGER_TIME, ("08:00",), ()))
        for key in ("internships", "hackathons", "deadlines"):
            self.assertEqual(made[key].proposes, (model.PROPOSE_TODO,))
        self.assertEqual(made["subscriptions"].proposes, (model.PROPOSE_CALENDAR,))
        self.assertEqual([(s.account, s.query) for s in made["deadlines"].mail],
                         [("work", "deadline"), ("work", '"due date"')])

    def test_deadlines_reads_the_calendar_only_when_some_account_can(self) -> None:
        deadlines = radar.preset_of("deadlines")
        self.assertEqual(self.make(deadlines, calendar_ok=True).calendar_days, 14)
        self.assertEqual(self.make(deadlines, calendar_ok=False).calendar_days, 0)
        for preset in radar.PRESETS:
            if preset.key != "deadlines":
                self.assertEqual(self.make(preset, calendar_ok=True).calendar_days, 0)

    def test_at_most_two_accounts_four_sources(self) -> None:
        agent = self.make(radar.preset_of("internships"), aliases=("a", "b", "c"))
        self.assertEqual(agent.accounts, ("a", "b"))
        self.assertEqual(len(agent.mail), model.MAX_MAIL_SOURCES)

    def test_an_existing_sheet_is_reused_read_only(self) -> None:
        store = docs_store.DocsStore(self.data)
        column, _ = docs_model.check_column("Company", "text")
        sheet_id, _ = store.create_sheet("Internships", [column])
        before = snapshot(self.data / "docs")
        agent = self.make(radar.preset_of("internships"))
        self.assertEqual((agent.lands, agent.lands_columns, agent.lands_keys), (("sheet", sheet_id), (), 1))
        self.assertEqual(snapshot(self.data / "docs"), before)

    def test_a_new_sheet_is_planned_and_nothing_written(self) -> None:
        self.make(radar.preset_of("subscriptions"))
        self.assertFalse((self.data / "docs").exists() and any((self.data / "docs").rglob("*")))

    def test_missing(self) -> None:
        self.assertEqual(radar.missing([]), radar.PRESETS)
        agent = self.make(radar.preset_of("hackathons"))
        self.assertEqual([p.key for p in radar.missing([agent])], ["internships", "deadlines", "subscriptions"])
        other = model.agent_from(agent_data(name="hackathons RADAR"))
        self.assertNotIn(radar.preset_of("hackathons"), radar.missing([other]))


class KeysTests(unittest.TestCase):
    COLUMNS = [{"name": "Company", "type": "text", "choices": []}, {"name": "Role", "type": "text", "choices": []},
               {"name": "Deadline", "type": "date", "choices": []}]

    def lands(self, **extra):  # type: ignore[no-untyped-def]
        return agent_data(lands={"kind": "sheet", "id": "", "name": "Internships", "columns": self.COLUMNS, **extra})

    def test_keys_round_trip_and_older_files(self) -> None:
        agent = model.agent_from(self.lands(keys=2))
        self.assertEqual(agent.lands_keys, 2)
        self.assertEqual(model.to_data(agent)["lands"]["keys"], 2)
        self.assertEqual(model.agent_from(model.to_data(agent)), agent)
        old = model.agent_from(self.lands())
        self.assertEqual(old.lands_keys, 1)
        self.assertNotIn("keys", model.to_data(old)["lands"])        # an older file is written as it was

    def test_bad_keys_are_refused(self) -> None:
        for bad in (0, 4, True, "2", 2.0):
            with self.subTest(bad=bad), self.assertRaises(model.DefinitionError):
                model.agent_from(self.lands(keys=bad))
        with self.assertRaises(model.DefinitionError):
            model.agent_from(agent_data(lands={"kind": "sheet", "id": "", "name": "x", "columns": self.COLUMNS[:1],
                                               "keys": 2}))
        with self.assertRaises(model.DefinitionError):
            model.agent_from(agent_data(lands={"kind": "doc", "id": "", "name": "x", "keys": 2}))

    def test_make_for_agent_uses_the_first_keys(self) -> None:
        from briefing_reader.docs_ui import DocsController

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        host = SimpleNamespace(store=docs_store.DocsStore(Path(tmp.name)), refresh_list=lambda: None)
        columns = [(c["name"], c["type"], ()) for c in self.COLUMNS]
        for keys, want in ((2, 2), (1, 1), (9, 3)):
            item_id = DocsController.make_for_agent(host, "sheet", f"Sheet {keys}", columns, keys=keys)
            sheet = host.store.read_sheet(item_id)
            self.assertEqual(sheet.keys, [c.id for c in sheet.columns[:want]])
        item_id = DocsController.make_for_agent(host, "sheet", "Old", columns)
        self.assertEqual(len(host.store.read_sheet(item_id).keys), 1)


class AddPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)

    def add(self, key="internships", features=None, accounts=None, docs=True):  # type: ignore[no-untyped-def]
        config = config_of(self.data, features if features is not None else {"personal": READ, "work": READ},
                           docs=docs)
        return standing_setup.add_preset(key, config, accounts, now=NOW_P1)

    def test_signed_in_accounts_first_two_sorted(self) -> None:
        features = {"c": READ, "b": READ, "a": READ, "z": ()}
        outcome = self.add(features=features, accounts={"a": Account(False), "b": Account(True), "c": Account(True)})
        self.assertEqual(outcome.job.accounts, ("b", "c"))
        outcome = self.add(features=features, accounts={"c": Account(True)})
        self.assertEqual(outcome.job.accounts, ("c",))

    def test_none_signed_in_keeps_the_readable_ones(self) -> None:
        features = {"c": READ, "b": READ, "a": READ, "z": ()}
        outcome = self.add(features=features, accounts={"a": Account(False)})
        self.assertEqual(outcome.job.accounts, ("a", "b"))
        self.assertEqual(standing_setup.signed_out(outcome.job, {}), ("a", "b"))
        self.assertEqual(self.add(features=features, accounts=None).job.accounts, ("a", "b"))

    def test_no_account_reads_mail(self) -> None:
        outcome = self.add(features={"work": ("calendar",)})
        self.assertIsNone(outcome.job)
        self.assertEqual(outcome.refusal, 'The work account can\'t read mail yet (config.toml features "gmail_read")')
        self.assertIn("gmail_read", self.add(features={}).refusal)

    def test_deadlines_calendar_by_feature(self) -> None:
        self.assertEqual(self.add("deadlines", features={"work": READ_CAL}).job.calendar_days, 14)
        self.assertEqual(self.add("deadlines", features={"work": READ, "home": ("calendar",)}).job.calendar_days, 14)
        self.assertEqual(self.add("deadlines", features={"work": READ}).job.calendar_days, 0)

    def test_docs_off_refused_and_nothing_written(self) -> None:
        outcome = self.add(docs=False)
        self.assertEqual((outcome.job, outcome.refusal), (None, standing_setup.DOCS_OFF_TEXT))
        self.assertEqual(list(self.data.rglob("*")), [])

    def test_unknown_key(self) -> None:
        self.assertIsNone(self.add("radio").job)

    def test_no_run_no_usage_draft_only(self) -> None:
        # add_preset takes no engine (the app's fake engine records no run: the app tests); the usage file stays absent
        with self.assertLogs("briefing_reader.standing_setup", level="INFO") as logs:
            outcome = self.add("subscriptions")
        self.assertFalse(StandingFiles(self.data).usage.exists())
        self.assertEqual(list(self.data.rglob("*")), [])
        self.assertEqual(outcome.job.status, model.STATUS_DRAFT)
        self.assertEqual(outcome.job.runs_per_day, StandingConfig().agent_runs_per_day)
        self.assertEqual(logs.output, [f"INFO:briefing_reader.standing_setup:Standing: ready-made subscriptions "
                                       f"added as draft {outcome.job.id}"])

    def test_does_line_first(self) -> None:
        job = self.add("hackathons").job
        lines = standing_setup.job_lines(job, SimpleNamespace(), does=job.words)
        self.assertEqual(lines[0], f"Does: {job.words}")
        self.assertNotIn(f"Does: {job.words}", standing_setup.job_lines(job, SimpleNamespace()))


# --------------------------------------------------------------------------------------------------------------------
# Part 2: the Radar card on the Agents screen (Qt offscreen; the app's engine a fake that records any run)
# --------------------------------------------------------------------------------------------------------------------

from PySide6.QtWidgets import QApplication  # noqa: E402

from briefing_reader import standing_ui  # noqa: E402
from briefing_reader.config import AskConfig  # noqa: E402
from briefing_reader.standing import runner  # noqa: E402
from briefing_reader.standing.store import AppState, RunRequests  # noqa: E402
from tests.standing_fakes import STANDING_ON, files_of  # noqa: E402
from tests.ui_fakes import AppHarness, fonts  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_radar", "-platform", "offscreen"])
    fonts()


class SignedIn:
    def __init__(self, sub: str) -> None:
        self.sub = sub

    def binding(self):  # type: ignore[no-untyped-def]
        return type("Binding", (), {"sub": self.sub, "email": "me@example.edu"})()

    def is_signed_in(self, feature: str | None = None) -> bool:
        return True


class RecordingEngine:
    def __init__(self) -> None:
        self.runs: list = []

    def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.runs.append(kwargs.get("kind"))
        raise AssertionError("no run may start")


class AppTests(unittest.TestCase):
    extra = ""

    def setUp(self) -> None:
        self.app = AppHarness(self, config_extra=STANDING_ON + self.extra)
        self.c = self.app.c
        self.s = self.c.standing
        self.c._locked = lambda: False
        self.s.busy_probe = lambda: False
        self.s.thread_runner = lambda work: work()
        self.s.start_runner = lambda: True
        self.engine = RecordingEngine()
        self.s.engine_factory = lambda: self.engine
        self.c._google_accounts = {"work": SignedIn("sub-work-1")}        # personal is not signed in
        self.app.reading(autoplay=False)
        self.s.reload()
        self.docs_dir = self.app.config.data_dir / "docs"

    def radar(self):  # type: ignore[no-untyped-def]
        return next((card for card in self.s.agents_info().cards if card.card_id == standing_ui.RADAR_CARD), None)

    def offered(self) -> list[str]:
        card = self.radar()
        return [button.action for button in card.buttons] if card is not None else []

    def docs_files(self) -> dict:
        return snapshot(self.docs_dir) if self.docs_dir.exists() else {}

    def setup_card(self):  # type: ignore[no-untyped-def]
        return next(card for card in self.s.agents_info().cards if card.card_id == standing_ui.SETUP_CARD)

    def add(self, key: str) -> None:
        tile = self.c.window.reading.agents_stage.notice_card(standing_ui.RADAR_CARD)
        tile.button(key).click()

    def test_the_card_add_consent_turn_on(self) -> None:
        self.assertEqual(self.offered(), ["internships", "hackathons", "deadlines", "subscriptions", "hide"])
        card = self.radar()
        self.assertEqual(card.kicker, "Radar · ready-made agents")
        self.assertEqual([b.text for b in card.buttons],
                         ["Internships", "Hackathons", "Deadlines", "Subscriptions", "Hide"])
        self.s._show()
        before = self.docs_files()
        with self.assertLogs("briefing_reader", level="INFO") as logs:
            self.add("internships")
        (agent,) = self.s.agents
        self.assertEqual((agent.name, agent.status, agent.accounts), ("Internships radar", model.STATUS_DRAFT,
                                                                       ("work",)))
        self.assertEqual(self.engine.runs, [])                                       # no Claude run
        self.assertFalse(files_of(self.app.config).usage.exists())                   # no usage counted
        self.assertEqual(self.docs_files(), before)                                  # no sheet until Turn on
        lines = self.setup_card().lines
        self.assertEqual(lines[0], f"Does: {radar.preset_of('internships').words}")
        self.assertIn("Reads: work mail matching internship; work mail matching internships", lines)
        self.assertIn("Writes to: Internships (a new sheet: Company · Role · Deadline · Link · "
                      "Status)", lines)
        self.assertEqual(lines[-1], standing_setup.PRIVACY_LINE)
        ids = [card.card_id for card in self.s.agents_info().cards]
        self.assertIn(standing_ui.CONSENT_CARD, ids)
        said = [record.getMessage() for record in logs.records if "ready-made" in record.getMessage()]
        self.assertEqual(said, [f"Standing: ready-made internships added as draft {agent.id}"])
        self.assertNotIn("Hackathons radar", " ".join(record.getMessage() for record in logs.records))
        self.assertNotIn("internships", self.offered())

        self.s.turn_on(agent.id)                                                     # before the consent: refused
        self.assertEqual(self.s.agents[0].status, model.STATUS_DRAFT)
        self.assertEqual(self.setup_card().note, standing_setup.turn_on_problem(agent, self.app.config, {}, False))
        self.s._on_card(standing_ui.CONSENT_CARD, "yes")
        self.s._on_card(standing_ui.SETUP_CARD, "on")
        on = self.s.agents[0]
        self.assertEqual(on.status, model.STATUS_LIVE)
        kind, sheet_id = on.lands
        sheet = self.c.docs.store.read_sheet(sheet_id)
        self.assertEqual([c.name for c in sheet.columns], ["Company", "Role", "Deadline", "Link", "Status"])
        self.assertEqual(sheet.keys, [sheet.columns[0].id, sheet.columns[1].id])
        self.assertEqual(sheet.rows, [])
        self.assertEqual(self.offered(), ["hackathons", "deadlines", "subscriptions", "hide"])
        self.assertEqual(self.engine.runs, [])

        self.s.toggle_pause_all()                                                    # Pause all stops it
        RunRequests(files_of(self.app.config)).ask(on.id, NOW_P1)
        calls: list = []

        class Engine:
            def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(kwargs.get("kind"))

        config = SimpleNamespace(data_dir=self.app.config.data_dir, standing=StandingConfig(enabled=True),
                                 ask=AskConfig(enabled=True), accounts={"work": None})
        deps = runner.Deps(readers={}, engine=Engine(), clock=lambda: NOW_P1, app_running=lambda: False,
                           tell_app=lambda runs: True, start_app=lambda: True, hold_extra_usage=lambda until: None,
                           own=lambda: [], notes=lambda: [])
        self.assertEqual(runner.run_check(config, deps, origin=runner.ORIGIN_TASK).status, "paused")
        self.assertEqual(calls, [])

    def test_discard_offers_it_again_and_edit_puts_the_words_in_the_bar(self) -> None:
        self.add("hackathons")
        self.assertNotIn("hackathons", self.offered())
        self.s._on_card(standing_ui.SETUP_CARD, "discard")
        self.assertEqual(list(self.s.agents), [])
        self.assertIn("hackathons", self.offered())
        self.s._show()
        self.add("deadlines")
        self.assertEqual(self.s.agents[0].calendar_days, 14)                  # the harness's accounts have calendar
        self.s._on_card(standing_ui.SETUP_CARD, "edit")
        self.assertEqual(self.c.window.reading.command_bar.input.text(),
                         f"agent: {radar.preset_of('deadlines').words}")
        self.assertEqual(list(self.s.agents), [])

    def test_hide_persists_and_the_link_brings_it_back(self) -> None:
        self.s._on_card(standing_ui.RADAR_CARD, "hide")
        self.assertIsNone(self.radar())
        self.assertTrue(AppState(files_of(self.app.config)).radar_hidden)
        notes = self.s.agents_info().notes
        self.assertIn((standing_ui.RADAR_HIDDEN_TEXT, standing_ui.RADAR_SHOW_TEXT, standing_ui.RADAR_LINK), notes)
        self.s.app_state.reload()
        self.assertIsNone(self.radar())                                       # read again: still hidden
        self.s._on_link(standing_ui.RADAR_LINK)
        self.assertIsNotNone(self.radar())
        self.assertFalse(AppState(files_of(self.app.config)).radar_hidden)
        self.assertNotIn(standing_ui.RADAR_LINK, [link for _t, _l, link in self.s.agents_info().notes])

    def test_hidden_while_a_setup_runs(self) -> None:
        self.s._busy = "setup"
        self.assertIsNone(self.radar())
        self.s.add_preset_card("internships")
        self.assertEqual(list(self.s.agents), [])
        self.assertIn(standing_ui.SETUP_BUSY_TEXT, [text for text, _l, _id in self.s.agents_info().notes])

    def test_hidden_while_a_setup_card_shows(self) -> None:
        self.add("hackathons")
        self.assertIsNone(self.radar())                                       # the draft's card is up
        self.assertIsNone(self.c.window.reading.agents_stage.notice_card(standing_ui.RADAR_CARD))
        self.s._on_card(standing_ui.SETUP_CARD, "discard")
        self.assertIsNotNone(self.radar())
        self.s._setup = standing_setup.SetupDraft("some words", note="Couldn't")   # a failed setup's card
        self.assertIsNone(self.radar())

    def test_turn_on_uses_a_sheet_of_that_name_made_since_add(self) -> None:
        self.add("internships")
        column, _ = docs_model.check_column("Company", "text")
        sheet_id, _ = self.c.docs.store.create_sheet("internships", [column])
        before = self.docs_files()
        self.s._on_card(standing_ui.CONSENT_CARD, "yes")
        self.s._on_card(standing_ui.SETUP_CARD, "on")
        on = self.s.agents[0]
        self.assertEqual((on.status, on.lands, on.lands_columns, on.lands_keys),
                         (model.STATUS_LIVE, ("sheet", sheet_id), (), 1))
        self.assertEqual(self.docs_files(), before)                           # no second sheet, nothing written
        sheet = self.c.docs.store.read_sheet(sheet_id)
        self.assertEqual(([c.name for c in sheet.columns], sheet.rows), (["Company"], []))

    def test_all_four_added_no_card(self) -> None:
        for key in ("internships", "hackathons", "deadlines", "subscriptions"):
            self.s.add_preset_card(key)
        self.assertEqual(len(self.s.agents), 4)
        self.assertIsNone(self.radar())
        self.assertTrue(all(agent.status == model.STATUS_DRAFT for agent in self.s.agents))
        self.assertEqual(model.live(self.s.agents), [])


class DocsOffTests(AppTests):
    extra = "\n[docs]\nenabled = false\n"

    test_the_card_add_consent_turn_on = None  # type: ignore[assignment]
    test_discard_offers_it_again_and_edit_puts_the_words_in_the_bar = None  # type: ignore[assignment]
    test_hide_persists_and_the_link_brings_it_back = None  # type: ignore[assignment]
    test_hidden_while_a_setup_runs = None  # type: ignore[assignment]
    test_all_four_added_no_card = None  # type: ignore[assignment]
    test_hidden_while_a_setup_card_shows = None  # type: ignore[assignment]
    test_turn_on_uses_a_sheet_of_that_name_made_since_add = None  # type: ignore[assignment]

    def test_docs_off_no_card_and_add_refused(self) -> None:
        self.assertIsNone(self.radar())
        self.s.add_preset_card("internships")
        self.assertEqual(list(self.s.agents), [])
        self.assertIn(standing_ui.DOCS_OFF_TEXT, [text for text, _l, _id in self.s.agents_info().notes])
        self.assertEqual(self.docs_files(), {})


if __name__ == "__main__":
    unittest.main()
