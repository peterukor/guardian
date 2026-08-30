"""Tests for the predictions table in src/evidence_store.py."""

import unittest
from src.evidence_store import EvidenceStore, PredictionRecord


def _make_record(invocation_id="inv-1", file_path="a.py", **overrides) -> PredictionRecord:
    defaults = dict(
        id=None, invocation_id=invocation_id, repo_path="/repo", file_path=file_path,
        commit_hash="abc123", ref_range="HEAD~1..HEAD", risk_score=7.5, risk_level="HIGH",
        agent_findings=["high fan-in"], created_at="2026-01-01T00:00:00+00:00",
        outcome_type=None, outcome_description=None, outcome_recorded_at=None,
    )
    defaults.update(overrides)
    return PredictionRecord(**defaults)


class TestPredictionLog(unittest.TestCase):

    def setUp(self):
        self.store = EvidenceStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_insert_and_retrieve_preserves_all_fields(self):
        pid = self.store.insert_prediction(_make_record())
        rec = self.store.get_prediction(pid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.file_path, "a.py")
        self.assertEqual(rec.risk_score, 7.5)
        self.assertEqual(rec.risk_level, "HIGH")
        self.assertEqual(rec.agent_findings, ["high fan-in"])
        self.assertIsNone(rec.outcome_type)

    def test_multiple_predictions_for_same_invocation(self):
        self.store.insert_prediction(_make_record(file_path="a.py"))
        self.store.insert_prediction(_make_record(file_path="b.py"))
        self.store.insert_prediction(_make_record(invocation_id="inv-2", file_path="c.py"))

        results = self.store.get_predictions_for_invocation("inv-1")
        self.assertEqual(len(results), 2)
        self.assertEqual({r.file_path for r in results}, {"a.py", "b.py"})

    def test_outcome_null_then_updated(self):
        pid = self.store.insert_prediction(_make_record())
        rec_before = self.store.get_prediction(pid)
        self.assertIsNone(rec_before.outcome_type)
        self.assertIsNone(rec_before.outcome_recorded_at)

        self.store.update_outcome(pid, "false_positive", "no incident occurred")
        rec_after = self.store.get_prediction(pid)
        self.assertEqual(rec_after.outcome_type, "false_positive")
        self.assertEqual(rec_after.outcome_description, "no incident occurred")
        self.assertIsNotNone(rec_after.outcome_recorded_at)

    def test_update_outcome_missing_id_raises(self):
        with self.assertRaises(ValueError):
            self.store.update_outcome(9999, "false_positive", None)


if __name__ == "__main__":
    unittest.main()
