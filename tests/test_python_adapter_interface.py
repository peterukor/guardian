"""
Unit tests for the adapter-interface entry point: python_adapter.analyze().

test_analyzer.py already thoroughly covers build_dependency_graph, get_edges,
and get_blast_radius directly. These tests only check the thin analyze()
wrapper itself: does it return the correct shape, and does it produce the
same result as calling the underlying functions directly.
"""

import os
import tempfile
import unittest

from src.adapters.base import EdgeTuple
from src.adapters.python_adapter import (
    analyze,
    build_dependency_graph,
    get_edges,
)


def make_repo(files: dict[str, str]) -> str:
    """Create a temporary directory with the given files and return its path."""
    tmp = tempfile.mkdtemp()
    for rel, code in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(code)
    return tmp


class TestAnalyzeEntryPoint(unittest.TestCase):

    def test_analyze_returns_list_of_plain_tuples(self):
        """analyze() must return plain 4-tuples matching EdgeTuple's shape,
        not DependencyEdge dataclass instances -- this is the contract the
        dispatcher and any future adapter must satisfy identically."""
        root = make_repo({
            "pkg/__init__.py": "",
            "pkg/utils.py": "X = 1",
            "main.py": "from pkg import utils",
        })
        result = analyze(root)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        edge = result[0]
        self.assertIsInstance(edge, tuple)
        self.assertEqual(len(edge), 4)

    def test_analyze_edge_values_correct(self):
        """Each tuple's fields must be (source_file, target_file,
        relationship_type, confidence) in that order, with correct values."""
        root = make_repo({
            "pkg/__init__.py": "",
            "pkg/utils.py": "X = 1",
            "main.py": "from pkg import utils",
        })
        source, target, rel_type, confidence = analyze(root)[0]
        self.assertEqual(source, "main.py")
        self.assertEqual(target, "pkg/utils.py")
        self.assertEqual(rel_type, "imports")
        self.assertAlmostEqual(confidence, 1.0)

    def test_analyze_matches_manual_graph_and_get_edges(self):
        """analyze(repo) must produce exactly the same edges as manually
        calling build_dependency_graph + get_edges -- analyze() must not
        duplicate or diverge from the underlying logic in any way."""
        root = make_repo({
            "a.py": "import b",
            "b.py": "import c",
            "c.py": "X = 1",
        })
        via_analyze = set(analyze(root))

        graph = build_dependency_graph(root)
        via_manual = {
            (e.source_file, e.target_file, e.relationship_type, e.confidence)
            for e in get_edges(graph)
        }
        self.assertEqual(via_analyze, via_manual)

    def test_analyze_empty_repo_returns_empty_list(self):
        """A repo with no Python files must return an empty list, not None
        or an error."""
        root = tempfile.mkdtemp()
        self.assertEqual(analyze(root), [])

    def test_analyze_return_type_matches_edgetuple_alias(self):
        """Sanity check that EdgeTuple's declared shape (4-tuple of
        str, str, str, float) matches what analyze() actually returns."""
        root = make_repo({"a.py": "import b", "b.py": "X = 1"})
        result: list[EdgeTuple] = analyze(root)
        source, target, rel_type, confidence = result[0]
        self.assertIsInstance(source, str)
        self.assertIsInstance(target, str)
        self.assertIsInstance(rel_type, str)
        self.assertIsInstance(confidence, float)


if __name__ == "__main__":
    unittest.main()
