"""
Tests for src/scanner.py.

All tests use real temporary git repositories and an in-memory SQLite Evidence
Store (injected via _run_scan so we don't go through the open/close lifecycle).

Test structure
--------------
TestScanMeta           — scan_meta is always written, including no-adapter repos
TestFileRecords        — FileRecord rows, including isolated (edge-free) files
TestEdgeRecords        — EdgeRecord rows written correctly
TestFanIn              — fan_in_count computed correctly from edges
TestRiskScoring        — score_files() called as one batch, not per-file
TestNoAdapters         — docs-only repo (no .py) → scan_meta written, no files/edges
TestScanResult         — returned ScanResult fields match what was persisted
"""

import os
import subprocess
import tempfile
import textwrap
import unittest

from src.evidence_store import EvidenceStore
from src.scanner import ScanResult, _run_scan


# ---------------------------------------------------------------------------
# Helpers shared by all tests
# ---------------------------------------------------------------------------

def _git(repo: str, *args: str) -> None:
    """Run a git command in repo, raising on failure."""
    subprocess.run(
        ["git", "-C", repo, *args],
        check=True, capture_output=True, text=True,
    )


def _write(repo: str, rel_path: str, content: str) -> None:
    """Write content to rel_path inside repo, creating directories as needed."""
    full = os.path.join(repo, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(textwrap.dedent(content))


def _setup_repo(tmp: str) -> None:
    """Initialise a bare git repo with a test identity."""
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "test@guardian.test")
    _git(tmp, "config", "user.name", "Guardian Test")


def _make_committed_repo(files: dict[str, str]) -> str:
    """
    Create a temp git repo, write and commit all given files in one commit,
    and return the repo path.
    """
    tmp = tempfile.mkdtemp()
    _setup_repo(tmp)
    for rel, content in files.items():
        _write(tmp, rel, content)
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "initial commit")
    return tmp


def _make_store() -> EvidenceStore:
    """Return a fresh in-memory EvidenceStore."""
    return EvidenceStore(":memory:")


# ---------------------------------------------------------------------------
# TestScanMeta — scan_meta is always written
# ---------------------------------------------------------------------------

class TestScanMeta(unittest.TestCase):

    def test_scan_meta_written_for_python_repo(self):
        """After scanning a Python repo, scan_meta must be populated."""
        repo = _make_committed_repo({"app.py": "x = 1\n"})
        store = _make_store()
        _run_scan(repo, store)
        meta = store.get_scan_meta()
        self.assertIsNotNone(meta)

    def test_scan_meta_commit_hash_is_head(self):
        """scan_meta.last_scan_commit_hash must equal the repo's HEAD SHA."""
        import subprocess as sp
        repo = _make_committed_repo({"app.py": "x = 1\n"})
        store = _make_store()
        _run_scan(repo, store)
        meta = store.get_scan_meta()
        expected = sp.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(meta.last_scan_commit_hash, expected)

    def test_scan_meta_branch_stored(self):
        """scan_meta.branch must be a non-empty string after scanning."""
        repo = _make_committed_repo({"app.py": "x = 1\n"})
        store = _make_store()
        _run_scan(repo, store)
        meta = store.get_scan_meta()
        self.assertIsNotNone(meta.branch)
        self.assertGreater(len(meta.branch), 0)

    def test_scan_meta_written_for_no_adapter_repo(self):
        """scan_meta must be written even when no adapters match (docs-only repo)."""
        repo = _make_committed_repo({"README.md": "# hello\n"})
        store = _make_store()
        _run_scan(repo, store)
        meta = store.get_scan_meta()
        self.assertIsNotNone(meta)

    def test_scan_meta_written_when_no_commits_yet(self):
        """scan_meta must be written even for a repo with no commits."""
        tmp = tempfile.mkdtemp()
        _setup_repo(tmp)
        # no commits — get_repo_state returns (None, None)
        store = _make_store()
        _run_scan(tmp, store)
        meta = store.get_scan_meta()
        self.assertIsNotNone(meta)


# ---------------------------------------------------------------------------
# TestFileRecords — files table population
# ---------------------------------------------------------------------------

class TestFileRecords(unittest.TestCase):

    def test_all_tracked_files_have_a_record(self):
        """Every .py file in the repo must have a FileRecord after scanning."""
        repo = _make_committed_repo({
            "a.py": "import b\n",
            "b.py": "x = 1\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        self.assertIsNotNone(store.get_file("a.py"))
        self.assertIsNotNone(store.get_file("b.py"))

    def test_isolated_file_gets_a_record(self):
        """
        A file that imports nothing and is imported by nothing must still
        produce a FileRecord — discovered_files() guarantees it is tracked
        even though analyze() produces no edges for it.
        """
        repo = _make_committed_repo({
            "isolated.py": "CONSTANT = 42\n",
            "also_isolated.py": "import os\n",   # only imports external
        })
        store = _make_store()
        _run_scan(repo, store)
        self.assertIsNotNone(store.get_file("isolated.py"))
        self.assertIsNotNone(store.get_file("also_isolated.py"))

    def test_file_record_fields_populated(self):
        """FileRecord fields must be set from git history and risk scoring."""
        repo = _make_committed_repo({"calc.py": "def add(a, b): return a+b\n"})
        store = _make_store()
        _run_scan(repo, store)
        rec = store.get_file("calc.py")
        self.assertIsNotNone(rec)
        # last_touch fields come from git history.
        self.assertIsNotNone(rec.last_touch_commit)
        self.assertIsNotNone(rec.last_touch_date)
        # bug_fix_count must be an int (0 for a clean "initial commit").
        self.assertIsInstance(rec.bug_fix_count, int)
        # top_author_pct must be a float in [0, 1].
        self.assertGreaterEqual(rec.top_author_pct, 0.0)
        self.assertLessEqual(rec.top_author_pct, 1.0)
        # risk_score must be in [0, 10].
        self.assertGreaterEqual(rec.risk_score, 0.0)
        self.assertLessEqual(rec.risk_score, 10.0)

    def test_file_record_date_is_iso8601(self):
        """last_touch_date must be an ISO-8601 date string (YYYY-MM-DD)."""
        import re
        repo = _make_committed_repo({"mod.py": "pass\n"})
        store = _make_store()
        _run_scan(repo, store)
        rec = store.get_file("mod.py")
        self.assertRegex(rec.last_touch_date, r"^\d{4}-\d{2}-\d{2}$")

    def test_no_file_records_for_no_adapter_repo(self):
        """A docs-only repo must produce zero FileRecord rows."""
        repo = _make_committed_repo({"README.md": "# hi\n"})
        store = _make_store()
        _run_scan(repo, store)
        self.assertEqual(store.get_all_files(), [])


# ---------------------------------------------------------------------------
# TestEdgeRecords — edges table population
# ---------------------------------------------------------------------------

class TestEdgeRecords(unittest.TestCase):

    def test_import_edge_is_stored(self):
        """An import relationship must produce an EdgeRecord."""
        repo = _make_committed_repo({
            "dep.py": "VALUE = 1\n",
            "main.py": "import dep\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        edges = store.get_edges_from("main.py")
        self.assertEqual(len(edges), 1)
        e = edges[0]
        self.assertEqual(e.source_file, "main.py")
        self.assertEqual(e.target_file, "dep.py")
        self.assertEqual(e.relationship_type, "imports")
        self.assertAlmostEqual(e.confidence, 1.0)

    def test_multiple_edges_stored(self):
        """Multiple import edges in a repo must all be stored."""
        repo = _make_committed_repo({
            "pkg/__init__.py": "",
            "pkg/utils.py": "UTIL = 1\n",
            "pkg/core.py": "CORE = 2\n",
            "main.py": "from pkg import utils, core\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        edges = store.get_edges_from("main.py")
        targets = {e.target_file for e in edges}
        self.assertIn("pkg/utils.py", targets)
        self.assertIn("pkg/core.py", targets)

    def test_no_edges_for_no_adapter_repo(self):
        """A docs-only repo must produce zero EdgeRecord rows."""
        repo = _make_committed_repo({"README.md": "# hi\n"})
        store = _make_store()
        _run_scan(repo, store)
        self.assertEqual(store.get_all_edges(), [])

    def test_isolated_file_produces_no_edges(self):
        """A file with no in-repo imports must appear in files but not in edges."""
        repo = _make_committed_repo({"standalone.py": "import os\n"})
        store = _make_store()
        _run_scan(repo, store)
        # FileRecord must exist.
        self.assertIsNotNone(store.get_file("standalone.py"))
        # But no edges.
        self.assertEqual(store.get_edges_from("standalone.py"), [])
        self.assertEqual(store.get_edges_to("standalone.py"), [])


# ---------------------------------------------------------------------------
# TestFanIn — fan_in_count computed from edges
# ---------------------------------------------------------------------------

class TestFanIn(unittest.TestCase):

    def test_fan_in_zero_for_isolated_file(self):
        """A file nobody imports must have fan_in_count = 0."""
        repo = _make_committed_repo({"alone.py": "X = 1\n"})
        store = _make_store()
        _run_scan(repo, store)
        rec = store.get_file("alone.py")
        self.assertEqual(rec.fan_in_count, 0)

    def test_fan_in_counts_direct_importers(self):
        """fan_in_count must equal the number of files that directly import it."""
        repo = _make_committed_repo({
            "shared.py": "VALUE = 1\n",
            "consumer_a.py": "import shared\n",
            "consumer_b.py": "import shared\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        rec = store.get_file("shared.py")
        self.assertEqual(rec.fan_in_count, 2)

    def test_fan_in_not_transitive(self):
        """fan_in_count is direct importers only — not transitive."""
        repo = _make_committed_repo({
            "c.py": "C = 1\n",
            "b.py": "import c\n",
            "a.py": "import b\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        # c.py is directly imported only by b.py — not by a.py.
        rec_c = store.get_file("c.py")
        self.assertEqual(rec_c.fan_in_count, 1)
        rec_b = store.get_file("b.py")
        self.assertEqual(rec_b.fan_in_count, 1)
        rec_a = store.get_file("a.py")
        self.assertEqual(rec_a.fan_in_count, 0)


# ---------------------------------------------------------------------------
# TestRiskScoring — score_files called as one full batch
# ---------------------------------------------------------------------------

class TestRiskScoring(unittest.TestCase):

    def test_risk_scores_stored_for_all_files(self):
        """Every FileRecord must have a risk_score after scanning."""
        repo = _make_committed_repo({
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "C = 1\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        for path in ("a.py", "b.py", "c.py"):
            rec = store.get_file(path)
            self.assertIsNotNone(rec.risk_score)
            self.assertGreaterEqual(rec.risk_score, 0.0)
            self.assertLessEqual(rec.risk_score, 10.0)

    def test_highest_fan_in_gets_highest_risk_score(self):
        """
        The file with the highest fan_in in the batch must receive a higher
        risk score than a file with zero fan_in — confirming that percentile
        ranks were computed across the full batch, not just for one file.
        """
        repo = _make_committed_repo({
            "popular.py": "VALUE = 1\n",
            "consumer_a.py": "import popular\n",
            "consumer_b.py": "import popular\n",
            "consumer_c.py": "import popular\n",
            "loner.py": "LONER = 2\n",
        })
        store = _make_store()
        _run_scan(repo, store)
        popular = store.get_file("popular.py")
        loner = store.get_file("loner.py")
        self.assertGreater(popular.risk_score, loner.risk_score)

    def test_single_file_repo_score_reflects_ownership(self):
        """
        With only one file, all percentile-ranked signals (fan_in, bug_fix_count)
        are 0.0 (no distribution to compare against), but ownership_concentration
        is used directly — not percentile-ranked — so a single author (pct=1.0)
        still produces a non-zero score.  The score must be in [0, 10] and must
        be higher than zero (ownership drives it up even without fan-in or bugs).
        """
        repo = _make_committed_repo({"solo.py": "pass\n"})
        store = _make_store()
        _run_scan(repo, store)
        rec = store.get_file("solo.py")
        # Score must be in valid range.
        self.assertGreaterEqual(rec.risk_score, 0.0)
        self.assertLessEqual(rec.risk_score, 10.0)
        # Single-file repos have top_author_pct=1.0, which contributes directly
        # to the score via the ownership_concentration weight — never truly zero.
        self.assertGreater(rec.risk_score, 0.0)


# ---------------------------------------------------------------------------
# TestNoAdapters — docs-only repo
# ---------------------------------------------------------------------------

class TestNoAdapters(unittest.TestCase):

    def test_no_file_records(self):
        """No-adapter repo must produce zero FileRecord rows."""
        repo = _make_committed_repo({"README.md": "# docs\n", "notes.txt": "hi\n"})
        store = _make_store()
        _run_scan(repo, store)
        self.assertEqual(store.get_all_files(), [])

    def test_no_edge_records(self):
        """No-adapter repo must produce zero EdgeRecord rows."""
        repo = _make_committed_repo({"README.md": "# docs\n"})
        store = _make_store()
        _run_scan(repo, store)
        self.assertEqual(store.get_all_edges(), [])

    def test_scan_meta_still_written(self):
        """scan_meta must be written even when no adapters matched."""
        repo = _make_committed_repo({"README.md": "# docs\n"})
        store = _make_store()
        _run_scan(repo, store)
        meta = store.get_scan_meta()
        self.assertIsNotNone(meta)


# ---------------------------------------------------------------------------
# TestScanResult — returned ScanResult struct
# ---------------------------------------------------------------------------

class TestScanResult(unittest.TestCase):

    def test_result_files_scanned_matches_store(self):
        """ScanResult.files_scanned must equal the number of FileRecord rows."""
        repo = _make_committed_repo({
            "x.py": "pass\n",
            "y.py": "pass\n",
            "z.py": "pass\n",
        })
        store = _make_store()
        result = _run_scan(repo, store)
        self.assertEqual(result.files_scanned, len(store.get_all_files()))

    def test_result_edges_stored_matches_store(self):
        """ScanResult.edges_stored must equal the number of EdgeRecord rows."""
        repo = _make_committed_repo({
            "a.py": "import b\n",
            "b.py": "pass\n",
        })
        store = _make_store()
        result = _run_scan(repo, store)
        self.assertEqual(result.edges_stored, len(store.get_all_edges()))

    def test_result_commit_hash_populated(self):
        """ScanResult.commit_hash must be a non-None SHA string."""
        repo = _make_committed_repo({"f.py": "pass\n"})
        store = _make_store()
        result = _run_scan(repo, store)
        self.assertIsNotNone(result.commit_hash)
        self.assertRegex(result.commit_hash, r"^[0-9a-f]{40}$")

    def test_result_branch_populated(self):
        """ScanResult.branch must be a non-empty string."""
        repo = _make_committed_repo({"f.py": "pass\n"})
        store = _make_store()
        result = _run_scan(repo, store)
        self.assertIsNotNone(result.branch)
        self.assertGreater(len(result.branch), 0)

    def test_result_zero_for_no_adapter_repo(self):
        """ScanResult for a no-adapter repo must report 0 files and 0 edges."""
        repo = _make_committed_repo({"README.md": "# hi\n"})
        store = _make_store()
        result = _run_scan(repo, store)
        self.assertEqual(result.files_scanned, 0)
        self.assertEqual(result.edges_stored, 0)

    def test_result_none_for_no_commits(self):
        """ScanResult.commit_hash and .branch must be None for an empty repo."""
        tmp = tempfile.mkdtemp()
        _setup_repo(tmp)
        store = _make_store()
        result = _run_scan(tmp, store)
        self.assertIsNone(result.commit_hash)
        self.assertIsNone(result.branch)


if __name__ == "__main__":
    unittest.main()
