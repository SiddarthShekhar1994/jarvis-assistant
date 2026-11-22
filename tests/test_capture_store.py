"""captures.json (part A): atomic, versioned, read-only when damaged or newer, bounded, pruned; cards rebuilt."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from briefing_reader import capture_store
from briefing_reader.capture import MAX_KEPT_CARDS, CapturedItem, Series, item_to_data, to_card
from briefing_reader.capture_store import CaptureStore, card_from, new_capture_id

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=timezone(timedelta(hours=-8)))
TODAY = NOW.date()


def item(n: int = 0, **extra) -> CapturedItem:
    return CapturedItem("event", f"Lecture {n}", start=datetime(2026, 12, 1, 9, 0) + timedelta(days=n),
                        end=datetime(2026, 12, 1, 10, 0) + timedelta(days=n), **extra)


def pairs(count: int, label: str = "term.ics"):
    return [(it, to_card(it, label, today=TODAY)) for it in (item(n) for n in range(count))]


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.clock = [NOW]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def store(self) -> CaptureStore:
        return CaptureStore(self.dir, clock=lambda: self.clock[0])

    def test_add_and_read_back(self) -> None:
        store = self.store()
        cid = new_capture_id()
        series_item = item(9, series=Series("WEEKLY", until=date(2027, 3, 1)), uid="u9")
        cards = pairs(2) + [(series_item, to_card(series_item, "term.ics", today=TODAY))]
        self.assertTrue(store.add(cid, label="term.ics", kind="ics", cards=cards, counts={"found": 3}, ext=".ics"))
        raw = json.loads((self.dir / "captures.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        again = self.store().cards(TODAY)
        self.assertEqual([card.id for _, card in again], [card.id for _, card in cards])
        self.assertEqual(again[2][1].uid, "u9")
        self.assertEqual({cid}, {capture_id for capture_id, _ in again})
        self.assertEqual(self.store().capture(cid)["counts"], {"found": 3})

    def test_changed_record_is_dropped(self) -> None:
        store = self.store()
        cid = new_capture_id()
        store.add(cid, label="term.ics", kind="ics", cards=pairs(2))
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        payload["captures"][cid]["cards"][0]["item"]["title"] = "Something else"
        payload["captures"][cid]["cards"][1]["item"] = {"kind": "nope"}
        store.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self.store().cards(TODAY), [])

    def test_at_most_fifty_per_capture_and_a_hundred_kept(self) -> None:
        store = self.store()
        ids = []
        for n in range(3):
            cid = new_capture_id()
            ids.append(cid)
            self.clock[0] = NOW + timedelta(minutes=n)
            store.add(cid, label=f"f{n}.ics", kind="ics", cards=pairs(60, f"f{n}.ics"))
        kept = store.captures()
        self.assertEqual([cid for cid, _ in kept], ids[1:])            # the oldest went
        self.assertTrue(all(len(entry["cards"]) == 50 for _, entry in kept))
        self.assertLessEqual(sum(len(entry["cards"]) for _, entry in kept), MAX_KEPT_CARDS)

    def test_damaged_and_newer_files_are_read_only(self) -> None:
        for content, problem in (("{not json", "damaged"), (json.dumps({"version": 99, "captures": {}}), "newer"),
                                 (json.dumps([1]), "damaged")):
            with self.subTest(problem=problem):
                (self.dir / "captures.json").write_text(content, encoding="utf-8")
                with self.assertLogs("briefing_reader.capture_store", "WARNING"):
                    store = self.store()
                self.assertTrue(store.read_only)
                self.assertEqual(store.problem, problem)
                self.assertFalse(store.add(new_capture_id(), label="x", kind="ics", cards=pairs(1)))
                self.assertEqual((self.dir / "captures.json").read_text(encoding="utf-8"), content)

    def test_remove_deletes_the_kept_copy(self) -> None:
        store = self.store()
        cid = new_capture_id()
        store.add(cid, label="board.png", kind="picture", cards=pairs(1), ext=".png")
        path = store.keep_source(cid, b"\x89PNG data", ".png")
        self.assertEqual(path, self.dir / "captures" / cid / "source.png")
        self.assertEqual(store.source_path(cid), path)
        self.assertTrue(store.remove(cid))
        self.assertFalse((self.dir / "captures" / cid).exists())
        self.assertIsNone(store.source_path(cid))
        with self.assertRaises(ValueError):
            store.keep_source(cid, b"x", ".exe")
        with self.assertRaises(ValueError):
            store.folder("..\\..\\x")

    def test_prune(self) -> None:
        store = self.store()
        old, decided, open_ = new_capture_id(), new_capture_id(), new_capture_id()
        self.clock[0] = NOW - timedelta(days=31)
        store.add(old, label="a", kind="text", cards=pairs(1))
        self.clock[0] = NOW - timedelta(days=10)
        decided_pairs = pairs(2)
        store.add(decided, label="b", kind="text", cards=decided_pairs)
        open_items = [item(n + 20) for n in range(2)]
        open_pairs = [(it, to_card(it, "c", today=TODAY)) for it in open_items]
        store.add(open_, label="c", kind="text", cards=open_pairs)
        self.clock[0] = NOW
        times = {card.id: NOW - timedelta(days=8) for _, card in decided_pairs}
        times[open_pairs[0][1].id] = NOW - timedelta(days=8)      # one of two: still open
        self.assertEqual(sorted(store.prune(NOW, times.get)), sorted([old, decided]))
        self.assertEqual([cid for cid, _ in self.store().captures()], [open_])
        times[open_pairs[1][1].id] = NOW - timedelta(days=2)      # all decided, but the last only 2 days ago
        self.assertEqual(store.prune(NOW, times.get), [])

    def test_card_from_refuses_bad_records(self) -> None:
        card = to_card(item(1), "x", today=TODAY)
        good = {"id": card.id, "item": item_to_data(item(1))}
        self.assertEqual(card_from(good, "x", TODAY).id, card.id)
        for bad in (None, {"id": "zz", "item": good["item"]}, {"id": card.id}, {"id": "0" * 16, "item": good["item"]}):
            self.assertIsNone(card_from(bad, "x", TODAY))

    def test_log_holds_no_title_or_label(self) -> None:
        store = self.store()
        cid = new_capture_id()
        store.add(cid, label="secret-plans.pdf", kind="pdf", cards=pairs(1, "secret-plans.pdf"))
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        payload["captures"][cid]["cards"][0]["item"]["title"] = "Changed"
        store.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertLogs(capture_store.logger, "INFO") as logs:
            self.store().cards(TODAY)
        text = "\n".join(logs.output)
        self.assertNotIn("secret", text)
        self.assertNotIn("Lecture", text)
        self.assertNotIn("Changed", text)



class ReviewStoreTests(unittest.TestCase):
    def test_no_kept_copy_for_a_read_only_file_or_an_unknown_capture(self) -> None:
        """Review item 4: a copy is kept only for a capture captures.json holds."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "captures.json").write_text("{damaged", encoding="utf-8")
            store = CaptureStore(folder)
            cid = new_capture_id()
            self.assertFalse(store.add(cid, label="x", kind="text", cards=pairs(1)))
            self.assertIsNone(store.keep_source(cid, b"x", ".txt"))
            self.assertFalse((folder / "captures").exists())
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(CaptureStore(Path(tmp)).keep_source(new_capture_id(), b"x", ".txt"))


if __name__ == "__main__":
    unittest.main()
