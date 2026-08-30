"""
Tests for EvidenceStore.build_graph() in src/evidence_store.py.

Coverage:
  - Empty store → empty graph (no nodes, no edges)
  - Stored files become graph nodes, including isolated files with no edges
  - Stored edges become directed graph edges with correct attributes
  - Edge direction is preserved (source → target)
  - Blast-radius results from a rebuilt graph match those from a graph built
    directly from a small equivalent repository via build_dependency_graph()
"""

import os
import tempfile
import textwrap
import unittest

from src.evidence_store import EdgeRecord, EvidenceStore, FileRecord
from src.adapters.python_adapter import build_dependency_graph, get_blast_radius


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> EvidenceStore:
    return EvidenceStore(":memory:")


def _file(path: str, fan_in: int = 0) -> FileRecord:
    return FileRecord(
        path=path,
        last_touch_commit="abc",
        last_touch_date="2024-01-01",
        fan_in_count=fan_in,
        bug_fix_count=0,
        top_author_pct=1.0,
        risk_score=0.0,
    )


def _edge(src: str, dst: str, rel: str = "imports", conf: float = 1.0) -> EdgeRecord:
    return EdgeRecord(
        source_file=src,
        target_file=dst,
        relationship_type=rel,
        confidence=conf,
    )


# ---------------------------------------------------------------------------
# Basic graph structure
# ---------------------------------------------------------------------------

class TestBuildGraphStructure(unittest.TestCase):

    def test_empty_store_returns_empty_graph(self):
        """A store with no files and no edges must produce an empty graph."""
        store = _make_store()
        g = store.build_graph()
        self.assertEqual(len(g.nodes), 0)
        self.assertEqual(len(g.edges), 0)

    def test_files_become_nodes(self):
        """Every stored file must appear as a node in the graph."""
        store = _make_store()
        store.upsert_file(_file("a.py"))
        store.upsert_file(_file("b.py"))
        g = store.build_graph()
        self.assertIn("a.py", g.nodes)
        self.assertIn("b.py", g.nodes)

    def test_isolated_file_is_node_with_no_edges(self):
        """A file with no stored edges must appear as a node with degree 0."""
        store = _make_store()
        store.upsert_file(_file("isolated.py"))
        g = store.build_graph()
        self.assertIn("isolated.py", g.nodes)
        self.assertEqual(g.degree("isolated.py"), 0)

    def test_edges_are_added_to_graph(self):
        """Stored edges must appear in the reconstructed graph."""
        store = _make_store()
        store.upsert_file(_file("src.py"))
        store.upsert_file(_file("dep.py"))
        store.upsert_edge(_edge("src.py", "dep.py"))
        g = store.build_graph()
        self.assertIn(("src.py", "dep.py"), g.edges)

    def test_edge_direction_preserved(self):
        """Source → target direction must be preserved exactly."""
        store = _make_store()
        store.upsert_file(_file("importer.py"))
        store.upsert_file(_file("lib.py"))
        store.upsert_edge(_edge("importer.py", "lib.py"))
        g = store.build_graph()
        self.assertIn(("importer.py", "lib.py"), g.edges)
        self.assertNotIn(("lib.py", "importer.py"), g.edges)

    def test_edge_attributes_preserved(self):
        """relationship_type and confidence must be present on graph edges."""
        store = _make_store()
        store.upsert_file(_file("a.py"))
        store.upsert_file(_file("b.py"))
        store.upsert_edge(_edge("a.py", "b.py", rel="imports", conf=0.9))
        g = store.build_graph()
        data = g.get_edge_data("a.py", "b.py")
        self.assertIsNotNone(data)
        self.assertEqual(data["relationship_type"], "imports")
        self.assertAlmostEqual(data["confidence"], 0.9)

    def test_node_count_equals_stored_file_count(self):
        """The number of graph nodes must equal the number of FileRecord rows."""
        store = _make_store()
        for i in range(5):
            store.upsert_file(_file(f"file{i}.py"))
        g = store.build_graph()
        self.assertEqual(len(g.nodes), 5)

    def test_edge_count_equals_stored_edge_count(self):
        """The number of graph edges must equal the number of EdgeRecord rows."""
        store = _make_store()
        store.upsert_file(_file("a.py"))
        store.upsert_file(_file("b.py"))
        store.upsert_file(_file("c.py"))
        store.upsert_edge(_edge("a.py", "b.py"))
        store.upsert_edge(_edge("b.py", "c.py"))
        g = store.build_graph()
        self.assertEqual(len(g.edges), 2)


# ---------------------------------------------------------------------------
# Blast-radius compatibility
# ---------------------------------------------------------------------------

class TestBuildGraphBlastRadius(unittest.TestCase):
    """
    Verify that blast-radius results from a graph rebuilt from the Evidence
    Store match those from a graph built directly from an equivalent small
    repository.  This confirms build_graph() produces a graph that is
    structurally equivalent to what build_dependency_graph() produces.
    """

    def _make_repo(self, files: dict[str, str]) -> str:
        tmp = tempfile.mkdtemp()
        for rel, content in files.items():
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(textwrap.dedent(content))
        return tmp

    def test_blast_radius_matches_direct_graph(self):
        """
        Blast radius of 'c.py' in a chain a→b→c must be identical whether
        the graph comes from build_dependency_graph() or from build_graph()
        reconstructed via the Evidence Store.
        """
        repo = self._make_repo({
            "c.py": "C = 1\n",
            "b.py": "from c import C\n",
            "a.py": "from b import C\n",
        })
        # Build graph directly from the repo.
        direct_graph = build_dependency_graph(repo)
        direct_br = get_blast_radius(direct_graph, "c.py")

        # Reconstruct the same graph from the Evidence Store.
        store = _make_store()
        for path in ["a.py", "b.py", "c.py"]:
            store.upsert_file(_file(path))
        store.upsert_edge(_edge("b.py", "c.py"))
        store.upsert_edge(_edge("a.py", "b.py"))
        rebuilt_graph = store.build_graph()
        rebuilt_br = get_blast_radius(rebuilt_graph, "c.py")

        self.assertEqual(direct_br["direct_dependents"],  rebuilt_br["direct_dependents"])
        self.assertEqual(direct_br["indirect_dependents"], rebuilt_br["indirect_dependents"])
        self.assertEqual(direct_br["total"],               rebuilt_br["total"])

    def test_isolated_file_blast_radius_is_zero(self):
        """
        An isolated file stored in the Evidence Store must have blast radius 0
        from the rebuilt graph — it is a node but has no dependents.
        """
        store = _make_store()
        store.upsert_file(_file("alone.py"))
        g = store.build_graph()
        br = get_blast_radius(g, "alone.py")
        self.assertEqual(br["total"], 0)
        self.assertEqual(br["direct_dependents"], [])
        self.assertEqual(br["indirect_dependents"], [])

    def test_unknown_file_blast_radius_is_zero(self):
        """
        Querying blast radius for a file not in the graph must return zeroes,
        matching the behaviour of a graph built from a real repository.
        """
        store = _make_store()
        store.upsert_file(_file("a.py"))
        g = store.build_graph()
        br = get_blast_radius(g, "does_not_exist.py")
        self.assertEqual(br["total"], 0)

    def test_diamond_dependency_blast_radius(self):
        """
        Diamond: a→b, a→c, b→d, c→d.
        Blast radius of d must include b, c, a (total=3, direct=[b,c], indirect=[a]).
        """
        store = _make_store()
        for p in ["a.py", "b.py", "c.py", "d.py"]:
            store.upsert_file(_file(p))
        store.upsert_edge(_edge("a.py", "b.py"))
        store.upsert_edge(_edge("a.py", "c.py"))
        store.upsert_edge(_edge("b.py", "d.py"))
        store.upsert_edge(_edge("c.py", "d.py"))
        g = store.build_graph()
        br = get_blast_radius(g, "d.py")
        self.assertEqual(br["total"], 3)
        self.assertCountEqual(br["direct_dependents"], ["b.py", "c.py"])
        self.assertCountEqual(br["indirect_dependents"], ["a.py"])


if __name__ == "__main__":
    unittest.main()
