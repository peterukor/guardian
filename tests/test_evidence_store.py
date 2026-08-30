"""
Unit tests for src/evidence_store.py.

All tests use an in-memory SQLite database (:memory:) so they are fully
isolated, leave no files on disk, and run fast.

Coverage targets: schema creation, file record CRUD, edge record CRUD,
scan_meta upsert (both fields), fan_in increment/decrement, bulk operations,
and persistence across a close/reopen cycle (using a real temp file for that
one test to verify data survives between connections).
"""

import os
import tempfile
import unittest

from src.evidence_store import (
    EvidenceStore,
    EdgeRecord,
    FileRecord,
    ScanMeta,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_store() -> EvidenceStore:
    """Return a fresh in-memory EvidenceStore for each test."""
    return EvidenceStore(":memory:")


def sample_file(path: str = "src/payment.py", **overrides) -> FileRecord:
    """Return a FileRecord with sensible defaults, accepting field overrides."""
    defaults = dict(
        path=path,
        last_touch_commit="abc123",
        last_touch_date="2024-03-15",
        fan_in_count=5,
        bug_fix_count=3,
        top_author_pct=0.7,
        risk_score=6.5,
    )
    defaults.update(overrides)
    return FileRecord(**defaults)


def sample_edge(
    source: str = "src/main.py",
    target: str = "src/payment.py",
    rel: str = "imports",
    conf: float = 1.0,
) -> EdgeRecord:
    return EdgeRecord(source_file=source, target_file=target,
                      relationship_type=rel, confidence=conf)


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

class TestSchemaCreation(unittest.TestCase):

    def test_tables_exist_after_init(self):
        """All three Phase 1 tables must exist immediately after opening the store."""
        store = make_store()
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        self.assertIn("files", tables)
        self.assertIn("edges", tables)
        self.assertIn("scan_meta", tables)
        store.close()

    def test_files_columns(self):
        """The files table must have exactly the columns specified in AGENTS.md."""
        store = make_store()
        cursor = store._conn.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            "path", "last_touch_commit", "last_touch_date",
            "fan_in_count", "bug_fix_count", "top_author_pct", "risk_score",
        }
        self.assertEqual(cols, expected)
        store.close()

    def test_edges_columns(self):
        """The edges table must have exactly the columns specified in AGENTS.md."""
        store = make_store()
        cursor = store._conn.execute("PRAGMA table_info(edges)")
        cols = {row[1] for row in cursor.fetchall()}
        self.assertEqual(cols, {"source_file", "target_file", "relationship_type", "confidence"})
        store.close()

    def test_scan_meta_columns(self):
        """scan_meta must have last_scan_commit_hash, branch, and id columns."""
        store = make_store()
        cursor = store._conn.execute("PRAGMA table_info(scan_meta)")
        cols = {row[1] for row in cursor.fetchall()}
        self.assertIn("last_scan_commit_hash", cols)
        self.assertIn("branch", cols)
        store.close()

    def test_schema_init_is_idempotent(self):
        """Opening the store twice on the same file must not raise or duplicate tables."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            s1 = EvidenceStore(db_path)
            s1.close()
            s2 = EvidenceStore(db_path)  # second open — must not fail
            s2.close()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# File records
# ---------------------------------------------------------------------------

class TestFileRecords(unittest.TestCase):

    def setUp(self):
        self.store = make_store()

    def tearDown(self):
        self.store.close()

    def test_upsert_and_get_file(self):
        """A file upserted can be retrieved with all fields intact."""
        rec = sample_file()
        self.store.upsert_file(rec)
        got = self.store.get_file("src/payment.py")
        self.assertIsNotNone(got)
        self.assertEqual(got.path, "src/payment.py")
        self.assertEqual(got.last_touch_commit, "abc123")
        self.assertEqual(got.last_touch_date, "2024-03-15")
        self.assertEqual(got.fan_in_count, 5)
        self.assertEqual(got.bug_fix_count, 3)
        self.assertAlmostEqual(got.top_author_pct, 0.7)
        self.assertAlmostEqual(got.risk_score, 6.5)

    def test_get_missing_file_returns_none(self):
        """Querying a path that was never inserted must return None."""
        self.assertIsNone(self.store.get_file("nonexistent.py"))

    def test_upsert_replaces_existing(self):
        """A second upsert for the same path must overwrite the first row."""
        self.store.upsert_file(sample_file(risk_score=3.0))
        self.store.upsert_file(sample_file(risk_score=8.0))
        got = self.store.get_file("src/payment.py")
        self.assertAlmostEqual(got.risk_score, 8.0)

    def test_last_touch_date_stored_as_string_not_days(self):
        """last_touch_date must be stored as a date string, not a staleness integer."""
        self.store.upsert_file(sample_file(last_touch_date="2023-11-01"))
        got = self.store.get_file("src/payment.py")
        # Must be a string in ISO-8601 form, never an integer.
        self.assertIsInstance(got.last_touch_date, str)
        self.assertEqual(got.last_touch_date, "2023-11-01")

    def test_delete_file(self):
        """Deleting a file record must remove it from the store."""
        self.store.upsert_file(sample_file())
        self.store.delete_file("src/payment.py")
        self.assertIsNone(self.store.get_file("src/payment.py"))

    def test_get_all_files_returns_all(self):
        """get_all_files must return every row, ordered by path."""
        self.store.upsert_file(sample_file("z.py"))
        self.store.upsert_file(sample_file("a.py"))
        self.store.upsert_file(sample_file("m.py"))
        records = self.store.get_all_files()
        self.assertEqual([r.path for r in records], ["a.py", "m.py", "z.py"])

    def test_increment_fan_in_positive(self):
        """increment_fan_in(+1) must increase fan_in_count by 1."""
        self.store.upsert_file(sample_file(fan_in_count=3))
        self.store.increment_fan_in("src/payment.py", 1)
        got = self.store.get_file("src/payment.py")
        self.assertEqual(got.fan_in_count, 4)

    def test_increment_fan_in_negative(self):
        """increment_fan_in(-1) must decrease fan_in_count by 1."""
        self.store.upsert_file(sample_file(fan_in_count=3))
        self.store.increment_fan_in("src/payment.py", -1)
        got = self.store.get_file("src/payment.py")
        self.assertEqual(got.fan_in_count, 2)

    def test_update_risk_scores_bulk(self):
        """update_risk_scores must update risk_score for every path in the dict."""
        self.store.upsert_file(sample_file("a.py", risk_score=1.0))
        self.store.upsert_file(sample_file("b.py", risk_score=1.0))
        self.store.update_risk_scores({"a.py": 7.5, "b.py": 3.2})
        self.assertAlmostEqual(self.store.get_file("a.py").risk_score, 7.5)
        self.assertAlmostEqual(self.store.get_file("b.py").risk_score, 3.2)

    def test_increment_fan_in_raises_on_missing_path(self):
        """increment_fan_in must raise ValueError if the path has no row in files."""
        with self.assertRaises(ValueError):
            self.store.increment_fan_in("nonexistent.py", 1)

    def test_update_risk_scores_raises_on_missing_path(self):
        """update_risk_scores must raise ValueError if any path has no row in files."""
        self.store.upsert_file(sample_file("real.py", risk_score=1.0))
        with self.assertRaises(ValueError):
            self.store.update_risk_scores({"real.py": 5.0, "ghost.py": 8.0})


# ---------------------------------------------------------------------------
# Edge records
# ---------------------------------------------------------------------------

class TestEdgeRecords(unittest.TestCase):

    def setUp(self):
        self.store = make_store()

    def tearDown(self):
        self.store.close()

    def test_upsert_and_get_edge_from(self):
        """An upserted edge is retrievable via get_edges_from."""
        self.store.upsert_edge(sample_edge())
        edges = self.store.get_edges_from("src/main.py")
        self.assertEqual(len(edges), 1)
        e = edges[0]
        self.assertEqual(e.source_file, "src/main.py")
        self.assertEqual(e.target_file, "src/payment.py")
        self.assertEqual(e.relationship_type, "imports")
        self.assertAlmostEqual(e.confidence, 1.0)

    def test_get_edges_to(self):
        """get_edges_to returns edges where the given path is the target."""
        self.store.upsert_edge(sample_edge(source="a.py", target="c.py"))
        self.store.upsert_edge(sample_edge(source="b.py", target="c.py"))
        edges = self.store.get_edges_to("c.py")
        sources = {e.source_file for e in edges}
        self.assertEqual(sources, {"a.py", "b.py"})

    def test_only_direct_edges_stored(self):
        """Store only the two direct edges — no transitive closures."""
        # a -> b -> c: only store the two direct edges, not a->c
        self.store.upsert_edge(sample_edge("a.py", "b.py"))
        self.store.upsert_edge(sample_edge("b.py", "c.py"))
        all_edges = self.store.get_all_edges()
        pairs = {(e.source_file, e.target_file) for e in all_edges}
        self.assertEqual(pairs, {("a.py", "b.py"), ("b.py", "c.py")})
        self.assertNotIn(("a.py", "c.py"), pairs)

    def test_upsert_edges_bulk(self):
        """upsert_edges_bulk must insert all provided edges in one call."""
        records = [
            sample_edge("x.py", "y.py"),
            sample_edge("x.py", "z.py"),
            sample_edge("w.py", "y.py"),
        ]
        self.store.upsert_edges_bulk(records)
        self.assertEqual(len(self.store.get_all_edges()), 3)

    def test_upsert_edge_replaces_duplicate(self):
        """Upserting the same (source, target) pair must not create a duplicate row."""
        self.store.upsert_edge(sample_edge(conf=1.0))
        self.store.upsert_edge(sample_edge(conf=0.9))
        edges = self.store.get_edges_from("src/main.py")
        self.assertEqual(len(edges), 1)
        self.assertAlmostEqual(edges[0].confidence, 0.9)

    def test_delete_edges_from(self):
        """delete_edges_from must remove all edges where that file is the source."""
        self.store.upsert_edge(sample_edge("src/main.py", "a.py"))
        self.store.upsert_edge(sample_edge("src/main.py", "b.py"))
        self.store.upsert_edge(sample_edge("other.py", "a.py"))
        self.store.delete_edges_from("src/main.py")
        self.assertEqual(self.store.get_edges_from("src/main.py"), [])
        # Edge from other.py must be unaffected.
        self.assertEqual(len(self.store.get_edges_to("a.py")), 1)

    def test_delete_single_edge(self):
        """delete_edge removes only the specified (source, target) pair."""
        self.store.upsert_edge(sample_edge("a.py", "b.py"))
        self.store.upsert_edge(sample_edge("a.py", "c.py"))
        self.store.delete_edge("a.py", "b.py")
        remaining = self.store.get_edges_from("a.py")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].target_file, "c.py")

    def test_rename_file_in_edges(self):
        """rename_file_in_edges must update both source and target references."""
        self.store.upsert_edge(sample_edge("old.py", "dep.py"))
        self.store.upsert_edge(sample_edge("importer.py", "old.py"))
        self.store.rename_file_in_edges("old.py", "new.py")
        # old.py as source → now new.py
        from_new = self.store.get_edges_from("new.py")
        self.assertEqual(len(from_new), 1)
        self.assertEqual(from_new[0].target_file, "dep.py")
        # old.py as target → now new.py
        to_new = self.store.get_edges_to("new.py")
        self.assertEqual(len(to_new), 1)
        self.assertEqual(to_new[0].source_file, "importer.py")
        # Nothing should reference old.py anymore.
        self.assertEqual(self.store.get_edges_from("old.py"), [])
        self.assertEqual(self.store.get_edges_to("old.py"), [])

    def test_rename_file_in_edges_no_crash_on_collision(self):
        """
        rename_file_in_edges must not raise when the renamed edge would collide
        with one that already exists.  E.g. old.py→dep.py renamed to new.py
        when new.py→dep.py is already present: the old row should be dropped
        (the destination edge already correctly represents the dependency).
        """
        # Pre-existing edge: new.py already imports dep.py.
        self.store.upsert_edge(sample_edge("new.py", "dep.py"))
        # old.py also imports dep.py — renaming old→new would collide on (new.py, dep.py).
        self.store.upsert_edge(sample_edge("old.py", "dep.py"))
        # Give old.py a non-colliding edge too, to confirm it gets renamed normally.
        self.store.upsert_edge(sample_edge("old.py", "util.py"))

        # Must not raise.
        self.store.rename_file_in_edges("old.py", "new.py")

        # new.py→dep.py must still exist (exactly once).
        edges_to_dep = self.store.get_edges_to("dep.py")
        sources_to_dep = [e.source_file for e in edges_to_dep]
        self.assertEqual(sources_to_dep.count("new.py"), 1)

        # The non-colliding edge must have been renamed: new.py→util.py.
        edges_from_new = self.store.get_edges_from("new.py")
        targets = {e.target_file for e in edges_from_new}
        self.assertIn("util.py", targets)

        # old.py must no longer appear anywhere.
        self.assertEqual(self.store.get_edges_from("old.py"), [])
        self.assertEqual(self.store.get_edges_to("old.py"), [])


# ---------------------------------------------------------------------------
# scan_meta
# ---------------------------------------------------------------------------

class TestScanMeta(unittest.TestCase):

    def setUp(self):
        self.store = make_store()

    def tearDown(self):
        self.store.close()

    def test_get_scan_meta_returns_none_before_first_scan(self):
        """A fresh store has no scan_meta row — get_scan_meta must return None."""
        self.assertIsNone(self.store.get_scan_meta())

    def test_set_and_get_scan_meta(self):
        """After set_scan_meta, get_scan_meta returns both fields correctly."""
        self.store.set_scan_meta("deadbeef", "main")
        meta = self.store.get_scan_meta()
        self.assertIsNotNone(meta)
        self.assertEqual(meta.last_scan_commit_hash, "deadbeef")
        self.assertEqual(meta.branch, "main")

    def test_set_scan_meta_upserts(self):
        """Calling set_scan_meta twice must update, not insert a second row."""
        self.store.set_scan_meta("first", "main")
        self.store.set_scan_meta("second", "feature/x")
        meta = self.store.get_scan_meta()
        self.assertEqual(meta.last_scan_commit_hash, "second")
        self.assertEqual(meta.branch, "feature/x")
        # Confirm there is exactly one row.
        count = self.store._conn.execute("SELECT COUNT(*) FROM scan_meta").fetchone()[0]
        self.assertEqual(count, 1)

    def test_branch_field_stored_separately(self):
        """branch is its own column — must be stored and retrieved independently."""
        self.store.set_scan_meta("abc", "feat/risk-scorer")
        meta = self.store.get_scan_meta()
        self.assertEqual(meta.branch, "feat/risk-scorer")


# ---------------------------------------------------------------------------
# Persistence across close/reopen
# ---------------------------------------------------------------------------

class TestPersistence(unittest.TestCase):

    def test_data_survives_close_and_reopen(self):
        """
        Data written in one EvidenceStore session must still be readable after
        closing and reopening the same file. This confirms SQLite durability
        (not just in-memory correctness) for the three tables.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Session 1: write data.
            s1 = EvidenceStore(db_path)
            s1.upsert_file(sample_file("persist.py", risk_score=9.1))
            s1.upsert_edge(sample_edge("persist.py", "dep.py"))
            s1.set_scan_meta("commit999", "main")
            s1.close()

            # Session 2: read data back — must match what was written.
            s2 = EvidenceStore(db_path)
            f_rec = s2.get_file("persist.py")
            edges = s2.get_edges_from("persist.py")
            meta = s2.get_scan_meta()
            s2.close()

            self.assertIsNotNone(f_rec)
            self.assertAlmostEqual(f_rec.risk_score, 9.1)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_file, "dep.py")
            self.assertIsNotNone(meta)
            self.assertEqual(meta.last_scan_commit_hash, "commit999")
            self.assertEqual(meta.branch, "main")
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
