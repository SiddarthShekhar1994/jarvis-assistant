"""Static checks of install-schedule.ps1 and uninstall-schedule.ps1.

The scripts are never run here (running them would register tasks or read
.env); these checks read their text. They guard what a -DryRun cannot see
without Task Scheduler: every planned task carries an action built with
New-ScheduledTaskAction, Register-Plan registers that action, and -DryRun
builds each complete task definition (New-ScheduledTask) without registering.
For "Briefing agents" they also check the plan that -DryRun prints and builds:
its name, logon and 5-minute triggers, settings and arguments.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL = PROJECT_ROOT / "install-schedule.ps1"
UNINSTALL = PROJECT_ROOT / "uninstall-schedule.ps1"
TASK_NAMES = ("Briefing AM", "Briefing PM", "Briefing catch-up", "Briefing hotkey", "Briefing agents")


def _function_body(text: str, name: str) -> str:
    """The text of ``function <name>`` up to the next top-level function or section rule."""
    match = re.search(rf"^function {re.escape(name)}\b.*?(?=^function |^# -{{10}})", text, re.M | re.S)
    return match.group(0) if match else ""


class InstallScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = INSTALL.read_text(encoding="ascii")

    def test_every_plan_has_an_action(self) -> None:
        plans = re.findall(r"\$plans \+= @\{(.*?)\n\s*\}", self.text, re.S)
        self.assertEqual(len(plans), 4, "AM/PM (one loop), catch-up, hotkey and agents plans")
        for plan in plans:
            with self.subTest(plan=plan.strip()[:40]):
                self.assertRegex(plan, r"Action = \(New-PlanAction \$\w+ \$\w+\)")

    def test_actions_are_built_with_new_scheduled_task_action(self) -> None:
        body = _function_body(self.text, "New-PlanAction")
        self.assertIn("New-ScheduledTaskAction -Execute (Format-Program $Program)", body)
        self.assertIn("-WorkingDirectory $ProjectRoot", body)

    def test_register_and_dry_run_use_the_same_definition(self) -> None:
        register = _function_body(self.text, "Register-Plan")
        dry_run = _function_body(self.text, "Test-Plan")
        for body in (register, dry_run):
            self.assertIn("-Action $Plan.Action", body)
            self.assertIn("-Trigger $Plan.Triggers", body)
            self.assertIn("-Settings $Plan.Settings", body)
            self.assertIn("-Principal $principal", body)
        self.assertIn("New-ScheduledTask ", dry_run)
        self.assertNotIn("Register-ScheduledTask", dry_run)
        self.assertRegex(self.text, r"if \(\$DryRun\) \{\s*Write-Host \"\"\s*foreach \(\$plan in \$plans\) \{\s*"
                                    r"try \{\s*Test-Plan \$plan")

    def test_task_arguments(self) -> None:
        self.assertIn('"${argPrefix}-m briefing_reader --run $($daily.Run) $slotsArgument"', self.text)
        self.assertIn('"${argPrefix}-m briefing_reader --catch-up $slotsArgument"', self.text)
        self.assertIn('"${argPrefix}-m briefing_reader --hotkey-agent $slotsArgument"', self.text)
        self.assertIn('$CatchUpDelay = "PT20S"', self.text)
        self.assertIn("$TaskSessionUnlock = 8", self.text)

    def test_ascii_only(self) -> None:
        for path in (INSTALL, UNINSTALL):
            with self.subTest(path=path.name):
                self.assertTrue(path.read_bytes().isascii())

    def _agents_plan(self) -> str:
        plans = [plan for plan in re.findall(r"\$plans \+= @\{(.*?)\n\s*\}", self.text, re.S)
                 if "Name = $AgentsTaskName;" in plan]
        self.assertEqual(len(plans), 1)
        return plans[0]

    def test_agents_task(self) -> None:
        self.assertIn('$AgentsTaskName = "Briefing agents"', self.text)
        plan = self._agents_plan()
        # The hotkey task's windowless Python (also with -Console), started in the project folder (New-PlanAction).
        self.assertIn("Program = $agentProgram;", plan)
        self.assertIn("Action = (New-PlanAction $agentProgram $agentsArguments)", plan)
        self.assertIn('$agentsArguments = "${argPrefix}-m briefing_reader --standing-run --origin task"', self.text)
        # Always installed (the app checks its own settings): the plan is not inside an "if".
        self.assertRegex(self.text, r"(?m)^\$plans \+= @\{\n    Name = \$AgentsTaskName;")

    def test_agents_triggers(self) -> None:
        plan = self._agents_plan()
        self.assertIn('Triggers = @((New-LogonTrigger $userId ""), (New-RepeatTrigger $AgentsInterval))', plan)
        self.assertIn("$AgentsInterval = New-TimeSpan -Minutes 5", self.text)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn -User $UserId", _function_body(self.text, "New-LogonTrigger"))
        repeat = _function_body(self.text, "New-RepeatTrigger")
        self.assertIn("New-ScheduledTaskTrigger -Once -At ([datetime]::Today) -RepetitionInterval $Interval", repeat)
        # No end ("Indefinitely"): no -RepetitionDuration in the code, and nothing is stopped at an end.
        code = "\n".join(line for line in repeat.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("-RepetitionDuration", code)
        self.assertIn("$trigger.Repetition.StopAtDurationEnd = $false", repeat)

    def test_agents_settings(self) -> None:
        match = re.search(r"^\$agentsSettings = ((?:[^\n]*`\n)*[^\n]*)", self.text, re.M)
        self.assertIsNotNone(match)
        settings = match.group(1)
        self.assertTrue(settings.startswith("New-ScheduledTaskSettingsSet "))
        for switch in ("-StartWhenAvailable", "-MultipleInstances IgnoreNew", "-AllowStartIfOnBatteries",
                       "-DontStopIfGoingOnBatteries", "-ExecutionTimeLimit (New-TimeSpan -Minutes 30)"):
            with self.subTest(switch=switch):
                self.assertIn(switch, settings)
        self.assertNotIn("-WakeToRun", settings)
        self.assertIn("Settings = $agentsSettings", self._agents_plan())

    def test_agents_dry_run_lines(self) -> None:
        # -DryRun prints each plan's name and When, its program and arguments, then builds it (Test-Plan).
        self.assertIn('Write-Host ("  {0}: {1}" -f $plan.Name, $plan.When)', self.text)
        self.assertIn('Write-Host ("    Args:     {0}" -f $plan.Arguments)', self.text)
        when = re.search(r"When = (.*?)\n\s*Description = ", self._agents_plan(), re.S)
        self.assertIsNotNone(when)
        for words in ("at logon of $userId and every 5 minutes", "a missed start runs as soon as possible",
                      "no wake", "one at a time", "at most 30 min"):
            with self.subTest(words=words):
                self.assertIn(words, when.group(1))

    def test_help_names_every_task(self) -> None:
        help_text = self.text.split("#>", 1)[0]
        synopsis = help_text.split(".DESCRIPTION", 1)[0]
        for name in TASK_NAMES:
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', synopsis)
        self.assertIn("... -m briefing_reader --standing-run --origin task", help_text)
        self.assertIn("The agents task is a check every 5 minutes for standing agents and rules", help_text)


class UninstallScriptTests(unittest.TestCase):
    def test_removes_every_task(self) -> None:
        text = UNINSTALL.read_text(encoding="ascii")
        listed = re.search(r"\$TaskNames = @\((.*?)\)", text)
        self.assertIsNotNone(listed)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', listed.group(1))), TASK_NAMES)
        self.assertIn("Stop-ScheduledTask", text)
        self.assertIn("SupportsShouldProcess = $true", text)

    def test_removes_the_agents_task(self) -> None:
        text = UNINSTALL.read_text(encoding="ascii")
        listed = re.search(r"\$TaskNames = @\((.*?)\)", text)
        self.assertIsNotNone(listed)
        self.assertIn('"Briefing agents"', listed.group(1))
        synopsis = text.split(".DESCRIPTION", 1)[0]
        for name in TASK_NAMES:
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', synopsis)


if __name__ == "__main__":
    unittest.main()
