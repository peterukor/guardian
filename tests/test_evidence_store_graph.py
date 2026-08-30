"""
Unit tests for EvidenceStore.build_graph() (graph reconstruction from stored
files/edges) and the predictions table (the Prediction Log for the future
Feedback Loop). In-memory SQLite (:memory:).
"""

import unittest

from src.adapters.python_adapter import get_blast_radius
from src.evidence_store import EdgeRecord, EvidenceStore, FileRecord, PredictionRecord


def _store() -> EvidenceStore:
    return EvidenceStore(":memory:")


def _require(x):
    """Narrow an Optional store-lookup result -- fail loudly, never chain off None."""
    assert x is not None
    return x


def _file(path="a.py", *, last_touch_commit="abc", last_touch_date="2024-03-15",
          fan_in_count=0, bug_fix_count=0, top_author_pct=1.0, risk_score=0.0) -> FileRecord:
    return FileRecord(path=path, last_touch_commit=last_touch_commit, last_touch_date=last_touch_date,
                       fan_in_count=fan_in_count, bug_fix_count=bug_fix_count,
                       top_author_pct=top_author_pct, risk_score=risk_score)


def _edge(src="a.py", dst="b.py", conf=1.0) -> EdgeRecord:
    return EdgeRecord(source_file=src, target_file=dst, relationship_type="imports", confidence=conf)


def _prediction(
    *, invocation_id="inv-1", repo_path="/repo", file_path="a.py", commit_hash="abc",
    ref_range="HEAD~1..HEAD", risk_score=7.5, risk_level="HIGH",
    agent_findings: list[str] | None = ["high fan-in"], created_at="2026-01-01T00:00:00+00:00",
) -> PredictionRecord:
    return PredictionRecord(
        id=None, invocation_id=invocation_id, repo_path=repo_path, file_path=file_path,
        commit_hash=commit_hash, ref_range=ref_range, risk_score=risk_score, risk_level=risk_level,
        agent_findings=agent_findings, created_at=created_at,
        outcome_type=None, outcome_description=None, outcome_recorded_at=None,
    )


class TestBuildGraph(unittest.TestCase):

    def test_reconstructs_directed_graph_and_blast_radius_matches_diamond_shape(self):
        store = _store()
        for p in ("a.py", "b.py", "c.py", "d.py", "isolated.py"):
            store.upsert_file(_file(p))
        store.upsert_edges_bulk([_edge("a.py", "b.py", conf=0.9), _edge("a.py", "c.py"),
                                  _edge("b.py", "d.py"), _edge("c.py", "d.py")])
        g = store.build_graph()
        self.assertEqual(g.degree("isolated.py"), 0)
        self.assertAlmostEqual(g.get_edge_data("a.py", "b.py")["confidence"], 0.9)

        br = get_blast_radius(g, "d.py")
        self.assertEqual(br["total"], 3)
        self.assertCountEqual(br["direct_dependents"], ["b.py", "c.py"])


class TestPredictionLog(unittest.TestCase):

    def setUp(self):
        self.store = _store()

    def test_insert_and_retrieve_preserves_all_fields(self):
        pid = self.store.insert_prediction(_prediction())
        rec = _require(self.store.get_prediction(pid))
        self.assertEqual((rec.file_path, rec.risk_score, rec.agent_findings),
                          ("a.py", 7.5, ["high fan-in"]))
        self.assertIsNone(rec.outcome_type)

    def test_predictions_grouped_by_invocation_id(self):
        self.store.insert_prediction(_prediction(file_path="a.py"))
        self.store.insert_prediction(_prediction(file_path="b.py"))
        self.store.insert_prediction(_prediction(invocation_id="inv-2", file_path="c.py"))
        results = self.store.get_predictions_for_invocation("inv-1")
        self.assertEqual({r.file_path for r in results}, {"a.py", "b.py"})

    def test_update_outcome_sets_fields_and_raises_on_missing_id(self):
        pid = self.store.insert_prediction(_prediction())
        self.store.update_outcome(pid, "false_positive", "no incident occurred")
        rec = _require(self.store.get_prediction(pid))
        self.assertEqual(rec.outcome_type, "false_positive")
        self.assertIsNotNone(rec.outcome_recorded_at)
        with self.assertRaises(ValueError):
            self.store.update_outcome(9999, "false_positive", None)


if __name__ == "__main__":
    unittest.main()
