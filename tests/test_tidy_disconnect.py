"""An account's tidy-up sign-in goes with its main sign-in ("No, use another account"): the action worker deletes
google_token_<alias>.tidy.json once the main sign-in is gone, and the Agents screen's controller forgets the one it
keeps in memory. Only files in a temporary folder; nothing reaches Google."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from briefing_reader import standing_ui, ui
from briefing_reader.executor import ExecError
from briefing_reader.google_auth import PROBLEM_SIGNED_OUT, tidy_token_path


class FakeExecutor:
    def __init__(self, fails: bool = False) -> None:
        self.fails = fails
        self.disconnected: list[str] = []

    def disconnect(self, alias: str) -> None:
        self.disconnected.append(alias)
        if self.fails:
            raise ExecError("Could not delete the work account's saved sign-in")


def worker(executor: FakeExecutor, data_dir: Path) -> ui._ActionWorker:
    """The worker's disconnect alone (no thread started)."""
    made = ui._ActionWorker.__new__(ui._ActionWorker)
    made._executor = executor
    made._tidy_token = lambda alias: tidy_token_path(data_dir, alias)
    return made


class WorkerTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="tidy-disconnect-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.tidy = tidy_token_path(self.dir, "work")
        self.other = tidy_token_path(self.dir, "personal")
        for path in (self.tidy, self.other):
            path.write_text("{}", encoding="utf-8")       # a stand-in: never a real token

    def test_the_tidy_up_sign_in_goes_with_the_main_one(self) -> None:
        executor = FakeExecutor()
        self.assertEqual(worker(executor, self.dir)._disconnect("work"), "")
        self.assertEqual(executor.disconnected, ["work"])
        self.assertFalse(self.tidy.exists())
        self.assertTrue(self.other.exists())                # another account's stays

    def test_a_failed_disconnect_keeps_the_tidy_up_sign_in(self) -> None:
        self.assertNotEqual(worker(FakeExecutor(fails=True), self.dir)._disconnect("work"), "")
        self.assertTrue(self.tidy.exists())

    def test_no_tidy_up_sign_in_or_one_that_cant_be_deleted_still_lets_the_sign_in_follow(self) -> None:
        self.tidy.unlink()
        self.assertEqual(worker(FakeExecutor(), self.dir)._disconnect("work"), "")
        self.tidy.write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("in use")), \
                self.assertLogs("briefing_reader.ui", "WARNING") as logs:
            self.assertEqual(worker(FakeExecutor(), self.dir)._disconnect("work"), "")
        self.assertIn("tidy-up sign-in (PermissionError)", "\n".join(logs.output))

    def test_a_worker_without_the_path_changes_nothing(self) -> None:
        made = worker(FakeExecutor(), self.dir)
        made._tidy_token = None
        self.assertEqual(made._disconnect("work"), "")
        self.assertTrue(self.tidy.exists())


class ControllerTests(unittest.TestCase):

    def fake(self, built: dict | None) -> SimpleNamespace:
        ran: list = []
        return SimpleNamespace(_tidy_built=built, thread_runner=lambda work: ran.append(work) or work(), ran=ran)

    def test_the_tidy_up_sign_in_kept_in_memory_is_forgotten_on_a_worker(self) -> None:
        account = mock.Mock()
        other = mock.Mock()
        host = self.fake({"work": account, "personal": other})
        standing_ui.StandingController.forget_tidy(host, "work")
        self.assertEqual(len(host.ran), 1)
        account.forget.assert_called_once_with("disconnected", problem=PROBLEM_SIGNED_OUT)
        other.forget.assert_not_called()

    def test_nothing_loaded_nothing_to_forget(self) -> None:
        for built in (None, {}, {"personal": mock.Mock()}):
            host = self.fake(built)
            standing_ui.StandingController.forget_tidy(host, "work")
            self.assertEqual(host.ran, [])

    def test_a_forget_that_fails_is_logged(self) -> None:
        account = mock.Mock()
        account.forget.side_effect = OSError("in use")
        with self.assertLogs("briefing_reader.standing_ui", "WARNING"):
            standing_ui.StandingController.forget_tidy(self.fake({"work": account}), "work")

    def test_the_wrong_account_answer_forgets_the_tidy_up_sign_in_before_the_new_sign_in(self) -> None:
        source = Path(ui.__file__).read_text(encoding="utf-8")
        at = source.index("self.standing.forget_tidy(alias)")
        self.assertLess(at, source.index("self._start_sign_in(alias, card_id=card_id, action=mail, disconnect=True)"))


if __name__ == "__main__":
    unittest.main()
