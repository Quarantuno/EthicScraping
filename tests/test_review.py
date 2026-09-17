"""Test della logica di ReviewSession (nessun input interattivo richiesto:
la CLI in main.py e' un layer sottile sopra questa classe)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.review import ReviewSession


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestReviewSession(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dataset_path = os.path.join(self.tmpdir, "output.jsonl")
        self.records = [
            {"id": "a", "url": "https://esempio.it/a", "text": "Testo A"},
            {"id": "b", "url": "https://esempio.it/b", "text": "Testo B"},
            {"id": "c", "url": "https://esempio.it/c", "text": "Testo C"},
        ]
        _write_jsonl(self.dataset_path, self.records)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_raises_if_dataset_missing(self):
        with self.assertRaises(FileNotFoundError):
            ReviewSession(os.path.join(self.tmpdir, "non-esiste.jsonl"))

    def test_all_records_pending_initially(self):
        session = ReviewSession(self.dataset_path)
        self.assertEqual(len(session.pending()), 3)
        self.assertEqual(session.stats(), {"total": 3, "approved": 0, "rejected": 0, "pending": 3})

    def test_decide_approve_and_reject_updates_state_and_files(self):
        session = ReviewSession(self.dataset_path)
        session.decide(self.records[0], "approved")
        session.decide(self.records[1], "rejected")

        self.assertEqual(session.stats(),
                          {"total": 3, "approved": 1, "rejected": 1, "pending": 1})
        self.assertEqual([r["id"] for r in session.pending()], ["c"])

        with open(session.approved_path, encoding="utf-8") as f:
            approved = [json.loads(line) for line in f]
        with open(session.rejected_path, encoding="utf-8") as f:
            rejected = [json.loads(line) for line in f]

        self.assertEqual([r["id"] for r in approved], ["a"])
        self.assertEqual([r["id"] for r in rejected], ["b"])

    def test_rejects_invalid_decision(self):
        session = ReviewSession(self.dataset_path)
        with self.assertRaises(ValueError):
            session.decide(self.records[0], "maybe")

    def test_state_persists_across_sessions(self):
        session1 = ReviewSession(self.dataset_path)
        session1.decide(self.records[0], "approved")

        session2 = ReviewSession(self.dataset_path)
        self.assertEqual(len(session2.pending()), 2)
        self.assertEqual(session2.stats()["approved"], 1)

    def test_redeciding_same_record_does_not_duplicate_file_entries(self):
        session = ReviewSession(self.dataset_path)
        session.decide(self.records[0], "approved")
        session.decide(self.records[0], "approved")  # stessa decisione ripetuta

        with open(session.approved_path, encoding="utf-8") as f:
            approved = [json.loads(line) for line in f]
        self.assertEqual(len(approved), 1)


if __name__ == "__main__":
    unittest.main()
