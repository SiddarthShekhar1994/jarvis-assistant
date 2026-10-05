"""Static checks of install-schedule.ps1 and uninstall-schedule.ps1.

The scripts are never run here (running them would register tasks or read
.env); these checks read their text. They guard what a -DryRun cannot see
without Task Scheduler: every planned task carries an action built with
New-ScheduledTaskAction, Register-Plan registers that action, and -DryRun
builds each complete task definition (New-ScheduledTask) without registering.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL = PROJECT_ROOT / "install-schedule.ps1"
UNINSTALL = PROJECT_ROOT / "uninstall-schedule.ps1"
TASK_NAMES = ("Briefing AM", "Briefing PM", "Briefing catch-up", "Briefing hotkey")


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
        self.assertEqual(len(plans), 3, "AM/PM (one loop), catch-up and hotkey plans")
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


class UninstallScriptTests(unittest.TestCase):
    def test_removes_every_task(self) -> None:
        text = UNINSTALL.read_text(encoding="ascii")
        listed = re.search(r"\$TaskNames = @\((.*?)\)", text)
        self.assertIsNotNone(listed)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', listed.group(1))), TASK_NAMES)
        self.assertIn("Stop-ScheduledTask", text)
        self.assertIn("SupportsShouldProcess = $true", text)


if __name__ == "__main__":
    unittest.main()
