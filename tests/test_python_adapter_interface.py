"""
Unit tests for the adapter interface on src/adapters/python_adapter.py.

Tests cover:
  - discovered_files() returns every .py file in the repo.
  - discovered_files() includes isolated files (no imports, not imported).
  - discovered_files() and analyze() are consistent — every source file in
    an edge also appears in discovered_files().
  - analyze() is not affected by the addition of discovered_files().
"""

import os
import textwrap
import tempfile
import unittest

from src.adapters.python_adapter import analyze, discovered_files


def _make_repo(files: dict[str, str]) -> str:
    """Create a temp directory with the given filename -> content mapping."""
    tmp = tempfile.mkdtemp()
    for rel, content in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(content))
    return tmp


class TestDiscoveredFiles(unittest.TestCase):

    def test_returns_list_of_strings(self):
        """discovered_files must return a list of strings."""
        root = _make_repo({"app.py": "x = 1"})
        result = discovered_files(root)
        self.assertIsInstance(result, list)
        self.assertTrue(all(isinstance(p, str) for p in result))

    def test_single_file(self):
        """A repo with one .py file returns that file as the only entry."""
        root = _make_repo({"main.py": "pass"})
        result = discovered_files(root)
        self.assertEqual(result, ["main.py"])

    def test_multiple_files(self):
        """All .py files in a repo are returned."""
        root = _make_repo({
            "a.py": "pass",
            "b.py": "pass",
            "pkg/__init__.py": "",
            "pkg/utils.py": "pass",
        })
        result = discovered_files(root)
        self.assertCountEqual(result, ["a.py", "b.py", "pkg/__init__.py", "pkg/utils.py"])

    def test_non_python_files_excluded(self):
        """Non-.py files must not appear in discovered_files output."""
        root = _make_repo({
            "main.py": "pass",
            "README.md": "# docs",
            "config.yaml": "key: val",
        })
        result = discovered_files(root)
        self.assertEqual(result, ["main.py"])

    def test_empty_repo_returns_empty_list(self):
        """A directory with no .py files returns []."""
        root = tempfile.mkdtemp()
        self.assertEqual(discovered_files(root), [])

    def test_isolated_file_included(self):
        """
        A file that imports nothing AND is imported by nothing must still
        appear in discovered_files() — it has no edges in analyze(), but the
        scanner must still track it in the Evidence Store.
        """
        root = _make_repo({
            "isolated.py": "CONSTANT = 42\n",   # no imports, nothing imports it
            "importer.py": "import os\n",       # only imports external
        })
        files = discovered_files(root)
        self.assertIn("isolated.py", files)

        # Confirm analyze() produces zero edges for this repo (both files are
        # isolated from an in-repo dependency perspective).
        edges = analyze(root)
        edge_files = {src for src, *_ in edges} | {dst for _, dst, *_ in edges}
        self.assertNotIn("isolated.py", edge_files)
        # But discovered_files still returns it.
        self.assertIn("isolated.py", files)

    def test_paths_use_forward_slashes(self):
        """Returned paths must use forward slashes, not OS-specific separators."""
        root = _make_repo({"pkg/sub/mod.py": "pass"})
        result = discovered_files(root)
        self.assertTrue(all("/" in p or p.endswith(".py") for p in result))
        self.assertFalse(any("\\" in p for p in result))

    def test_discovered_files_consistent_with_analyze(self):
        """
        Every file that appears as a source or target in analyze() edges must
        also appear in discovered_files() — analyze() cannot produce an edge
        to a file that discovered_files() doesn't know about.
        """
        root = _make_repo({
            "pkg/__init__.py": "",
            "pkg/utils.py": "UTIL = 1",
            "main.py": "from pkg import utils",
        })
        files = set(discovered_files(root))
        edges = analyze(root)
        for src, dst, *_ in edges:
            self.assertIn(src, files, f"edge source {src!r} missing from discovered_files")
            self.assertIn(dst, files, f"edge target {dst!r} missing from discovered_files")

    def test_analyze_behavior_unchanged(self):
        """analyze() must still return correct edges — adding discovered_files
        must not change analyze()'s behavior."""
        root = _make_repo({
            "dep.py": "VALUE = 1",
            "main.py": "import dep",
        })
        edges = analyze(root)
        self.assertEqual(len(edges), 1)
        src, dst, rel, conf = edges[0]
        self.assertEqual(src, "main.py")
        self.assertEqual(dst, "dep.py")
        self.assertEqual(rel, "imports")
        self.assertAlmostEqual(conf, 1.0)


if __name__ == "__main__":
    unittest.main()
