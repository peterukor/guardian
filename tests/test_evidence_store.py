"""
Unit tests for src/evidence_store/ -- schema and per-table CRUD (files,
edges, scan_meta). In-memory SQLite (:memory:) except the schema/persistence
tests, which need a real file on disk. See test_evidence_store_graph.py for
build_graph() and the prediction log.
"""

import os
import tempfile
import unittest

from src.evidence_store import EdgeRecord, EvidenceStore, FileRecord


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


class TestSchemaAndPersistence(unittest.TestCase):

    def test_reopen_is_idempotent_and_creates_all_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EvidenceStore(db_path).close()
            s = EvidenceStore(db_path)  # second open on same file must not raise
            tables = {r[0] for r in s._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            s.close()
        finally:
            os.unlink(db_path)
        self.assertEqual({"files", "edges", "scan_meta", "predictions"} - tables, set())

    def test_data_survives_close_and_reopen(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            s1 = EvidenceStore(db_path)
            s1.upsert_file(_file("persist.py", risk_score=9.1))
            s1.set_scan_meta("commit999", "main")
            s1.close()
            s2 = EvidenceStore(db_path)
            rec = _require(s2.get_file("persist.py"))
            meta = _require(s2.get_scan_meta())
            s2.close()
        finally:
            os.unlink(db_path)
        self.assertAlmostEqual(rec.risk_score, 9.1)
        self.assertEqual(meta.last_scan_commit_hash, "commit999")


class TestFileRecords(unittest.TestCase):

    def setUp(self):
        self.store = _store()

    def test_upsert_get_replace_and_delete_round_trip(self):
        self.store.upsert_file(_file(risk_score=3.0))
        self.store.upsert_file(_file(risk_score=8.0))  # replaces, not duplicates
        got = _require(self.store.get_file("a.py"))
        self.assertAlmostEqual(got.risk_score, 8.0)
        self.assertIsInstance(got.last_touch_date, str)  # never a precomputed day-count
        self.store.delete_file("a.py")
        self.assertIsNone(self.store.get_file("a.py"))

    def test_get_all_files_ordered_by_path(self):
        for p in ("z.py", "a.py", "m.py"):
            self.store.upsert_file(_file(p))
        self.assertEqual([r.path for r in self.store.get_all_files()], ["a.py", "m.py", "z.py"])

    def test_increment_fan_in_and_update_risk_scores_bulk(self):
        self.store.upsert_file(_file(fan_in_count=3))
        self.store.increment_fan_in("a.py", -1)
        self.store.upsert_file(_file("b.py"))
        self.store.update_risk_scores({"a.py": 7.5, "b.py": 3.2})
        self.assertEqual(_require(self.store.get_file("a.py")).fan_in_count, 2)
        self.assertAlmostEqual(_require(self.store.get_file("b.py")).risk_score, 3.2)

    def test_increment_and_update_raise_on_missing_path(self):
        """Never silently no-op on a missing row -- that would hide a caller bug."""
        with self.assertRaises(ValueError):
            self.store.increment_fan_in("ghost.py", 1)
        with self.assertRaises(ValueError):
            self.store.update_risk_scores({"ghost.py": 1.0})


class TestEdgeRecords(unittest.TestCase):

    def setUp(self):
        self.store = _store()

    def test_upsert_replaces_duplicate_source_target_pair(self):
        self.store.upsert_edge(_edge())
        self.store.upsert_edge(_edge(conf=0.9))
        edges = self.store.get_edges_from("a.py")
        self.assertEqual(len(edges), 1)
        self.assertAlmostEqual(edges[0].confidence, 0.9)

    def test_only_direct_edges_stored_no_transitive_closure(self):
        self.store.upsert_edges_bulk([_edge("a.py", "b.py"), _edge("b.py", "c.py")])
        pairs = {(e.source_file, e.target_file) for e in self.store.get_all_edges()}
        self.assertEqual(pairs, {("a.py", "b.py"), ("b.py", "c.py")})

    def test_delete_edges_from_only_affects_that_source(self):
        self.store.upsert_edges_bulk([_edge("a.py", "x.py"), _edge("other.py", "x.py")])
        self.store.delete_edges_from("a.py")
        self.assertEqual(self.store.get_edges_from("a.py"), [])
        self.assertEqual(len(self.store.get_edges_to("x.py")), 1)

    def test_rename_updates_references_and_drops_collisions_without_raising(self):
        self.store.upsert_edges_bulk([_edge("old.py", "dep.py"), _edge("importer.py", "old.py")])
        self.store.rename_file_in_edges("old.py", "new.py")
        self.assertEqual(self.store.get_edges_from("new.py")[0].target_file, "dep.py")
        self.assertEqual(self.store.get_edges_to("new.py")[0].source_file, "importer.py")
        self.assertEqual(self.store.get_edges_from("old.py") + self.store.get_edges_to("old.py"), [])

        # Collision: new.py already imports dep.py -- renaming old2.py (which
        # also imports dep.py) onto it must drop the stale row, not raise.
        self.store.upsert_edges_bulk([_edge("new.py", "dep.py"), _edge("old2.py", "dep.py")])
        self.store.rename_file_in_edges("old2.py", "new.py")
        self.assertEqual([e.source_file for e in self.store.get_edges_to("dep.py")], ["new.py"])


class TestScanMeta(unittest.TestCase):

    def test_upserts_single_row_and_none_means_never_scanned(self):
        store = _store()
        self.assertIsNone(store.get_scan_meta())
        store.set_scan_meta(None, None)  # e.g. repo with no commits yet
        self.assertIsNone(_require(store.get_scan_meta()).last_scan_commit_hash)  # NULL, never ""
        store.set_scan_meta("second", "feature/x")
        meta = _require(store.get_scan_meta())
        self.assertEqual((meta.last_scan_commit_hash, meta.branch), ("second", "feature/x"))
        count = store._conn.execute("SELECT COUNT(*) FROM scan_meta").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
