"""
Tests for src/scanner.py -- orchestration of adapters, git history, risk
scoring, and Evidence Store persistence. Real temporary git repos, in-memory
Evidence Store injected directly via _run_scan.
"""

import os
import subprocess
import tempfile
import textwrap
import unittest

from src.evidence_store import EvidenceStore
from src.scanner import _run_scan


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)
def _write(repo: str, rel: str, content: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(textwrap.dedent(content))
def _repo(files: dict[str, str]) -> str:
    tmp = tempfile.mkdtemp()
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@guardian.test")
    _git(tmp, "config", "user.name", "Guardian Test")
    for rel, content in files.items():
        _write(tmp, rel, content)
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "initial commit")
    return tmp
def _require(x):
    """Narrow an Optional store-lookup result -- fail loudly, never chain off None."""
    assert x is not None
    return x


class TestScanMeta(unittest.TestCase):

    def test_written_for_python_repo_matching_head(self):
        repo = _repo({"app.py": "x = 1\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(_require(store.get_scan_meta()).last_scan_commit_hash, head)

    def test_written_even_for_no_adapter_and_no_commit_repos(self):
        """scan_meta must exist after any scan, including docs-only and empty repos."""
        docs_repo = _repo({"README.md": "# hi\n"})
        store = EvidenceStore(":memory:")
        _run_scan(docs_repo, store)
        self.assertIsNotNone(store.get_scan_meta())

        empty_repo = tempfile.mkdtemp()
        _git(empty_repo, "init")
        store2 = EvidenceStore(":memory:")
        _run_scan(empty_repo, store2)
        meta = _require(store2.get_scan_meta())
        # NULL, never an empty string -- would silently break incremental-scan logic.
        self.assertIsNone(meta.last_scan_commit_hash)
        self.assertIsNone(meta.branch)


class TestFileEdgeAndFanIn(unittest.TestCase):

    def test_isolated_and_connected_files_both_get_records(self):
        repo = _repo({"a.py": "import b\n", "isolated.py": "CONSTANT = 42\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        self.assertIsNotNone(store.get_file("a.py"))
        self.assertIsNotNone(store.get_file("isolated.py"))
        self.assertEqual(store.get_edges_from("isolated.py"), [])

    def test_file_record_fields_populated_from_history_and_scoring(self):
        repo = _repo({"calc.py": "def add(a, b): return a+b\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        rec = _require(store.get_file("calc.py"))
        self.assertIsNotNone(rec.last_touch_commit)
        self.assertRegex(_require(rec.last_touch_date), r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(0.0 <= rec.risk_score <= 10.0)

    def test_import_edges_stored_with_correct_direction(self):
        repo = _repo({"dep.py": "VALUE = 1\n", "main.py": "import dep\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        edges = store.get_edges_from("main.py")
        self.assertEqual([(e.source_file, e.target_file) for e in edges], [("main.py", "dep.py")])

    def test_no_adapter_repo_produces_zero_files_and_edges(self):
        repo = _repo({"README.md": "# hi\n", "notes.txt": "hi\n"})
        store = EvidenceStore(":memory:")
        result = _run_scan(repo, store)
        self.assertEqual((result.files_scanned, result.edges_stored), (0, 0))
        self.assertEqual(store.get_all_files(), [])

    def test_fan_in_counts_direct_importers_only_not_transitive(self):
        repo = _repo({"c.py": "C = 1\n", "b.py": "import c\n", "a.py": "import b\n",
                       "consumer_b2.py": "import b\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        # c.py is directly imported only by b.py -- not transitively by a.py.
        self.assertEqual(_require(store.get_file("c.py")).fan_in_count, 1)
        self.assertEqual(_require(store.get_file("b.py")).fan_in_count, 2)
        self.assertEqual(_require(store.get_file("a.py")).fan_in_count, 0)


class TestRiskScoring(unittest.TestCase):

    def test_batch_scoring_ranks_across_the_whole_tracked_set(self):
        """The file with more importers in this scan's batch must score higher --
        confirms percentile ranks were computed over the full set, not per-file."""
        repo = _repo({"popular.py": "VALUE = 1\n", "consumer_a.py": "import popular\n",
                       "consumer_b.py": "import popular\n", "loner.py": "LONER = 2\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        self.assertGreater(_require(store.get_file("popular.py")).risk_score,
                            _require(store.get_file("loner.py")).risk_score)

    def test_single_file_repo_score_reflects_ownership_not_zero(self):
        """Regression: n=1 means fan_in/bug_fix percentile ranks are 0.0, but
        ownership_concentration is used directly -- score must exceed zero."""
        repo = _repo({"solo.py": "pass\n"})
        store = EvidenceStore(":memory:")
        _run_scan(repo, store)
        self.assertGreater(_require(store.get_file("solo.py")).risk_score, 0.0)


class TestScanResult(unittest.TestCase):

    def test_result_counts_match_what_was_persisted(self):
        repo = _repo({"a.py": "import b\n", "b.py": "pass\n"})
        store = EvidenceStore(":memory:")
        result = _run_scan(repo, store)
        self.assertEqual(result.files_scanned, len(store.get_all_files()))
        self.assertEqual(result.edges_stored, len(store.get_all_edges()))
        self.assertRegex(_require(result.commit_hash), r"^[0-9a-f]{40}$")

    def test_result_is_none_for_repo_with_no_commits(self):
        tmp = tempfile.mkdtemp()
        _git(tmp, "init")
        result = _run_scan(tmp, EvidenceStore(":memory:"))
        self.assertEqual((result.commit_hash, result.branch), (None, None))


if __name__ == "__main__":
    unittest.main()
