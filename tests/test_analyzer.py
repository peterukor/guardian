"""
Unit tests for src/analyzer.py.

Tests are self-contained: each test builds a small fake repo tree inside a
temporary directory, writes Python source files into it, runs the analyzer,
and asserts the resulting graph.

No real repository is required. No database or CLI code is touched.
"""

import os
import textwrap
import tempfile
import unittest

from src.analyzer import (
    build_dependency_graph,
    get_edges,
    get_blast_radius,
    _extract_imports,
    _resolve_module,
    _resolve_relative_import,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_repo(files: dict[str, str]) -> str:
    """
    Create a temporary directory with the given files, then return its path.

    `files` maps relative paths (e.g. "pkg/utils.py") to source code strings.
    Directories are created automatically.
    """
    tmp = tempfile.mkdtemp()
    for rel, code in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(code))
    return tmp


def edges_as_pairs(graph) -> set[tuple[str, str]]:
    """Return the graph's edges as a set of (source, target) pairs."""
    return {(src, dst) for src, dst, _ in graph.edges(data=True)}


# ---------------------------------------------------------------------------
# _extract_imports — unit tests (pure, no filesystem)
# ---------------------------------------------------------------------------

class TestExtractImports(unittest.TestCase):

    def test_plain_import(self):
        """import os.path -> ('os.path', 0)"""
        result = _extract_imports("import os.path")
        self.assertIn(("os.path", 0), result)

    def test_from_import_absolute(self):
        """from src import analyzer records both the compound and parent names."""
        result = _extract_imports("from src import analyzer")
        self.assertIn(("src.analyzer", 0), result)
        self.assertIn(("src", 0), result)

    def test_from_import_relative_single_dot(self):
        """from .utils import helper -> ('utils', 1)"""
        result = _extract_imports("from .utils import helper")
        self.assertIn(("utils", 1), result)

    def test_from_import_relative_two_dots(self):
        """from ..base import core -> ('base', 2)"""
        result = _extract_imports("from ..base import core")
        self.assertIn(("base", 2), result)

    def test_syntax_error_returns_empty(self):
        """Unparseable source must return an empty list, not raise."""
        result = _extract_imports("def (broken syntax !!!")
        self.assertEqual(result, [])

    def test_bare_relative_dot_only(self):
        """
        `from . import something` -- ast gives module=None, level=1.
        The analyzer cannot statically determine whether `something` is a
        module or a symbol, so it records (None, 1) and skips resolution.
        """
        result = _extract_imports("from . import something")
        self.assertIn((None, 1), result)


# ---------------------------------------------------------------------------
# Absolute import resolution
# ---------------------------------------------------------------------------

class TestAbsoluteImportResolution(unittest.TestCase):

    def setUp(self):
        """
        Repo layout:
            pkg/__init__.py
            pkg/utils.py
            main.py          <- imports pkg.utils and pkg
        """
        self.root = make_repo({
            "pkg/__init__.py": "",
            "pkg/utils.py": "x = 1",
            "main.py": "from pkg import utils\nimport pkg.utils",
        })

    def test_edge_main_to_utils(self):
        """main.py -> pkg/utils.py edge must exist."""
        g = build_dependency_graph(self.root)
        pairs = edges_as_pairs(g)
        self.assertIn(("main.py", "pkg/utils.py"), pairs)

    def test_edge_direction(self):
        """The edge must go from importer to importee, not the other way."""
        g = build_dependency_graph(self.root)
        pairs = edges_as_pairs(g)
        self.assertNotIn(("pkg/utils.py", "main.py"), pairs)

    def test_edge_attributes(self):
        """Every resolved edge must carry relationship_type and confidence."""
        g = build_dependency_graph(self.root)
        data = g.get_edge_data("main.py", "pkg/utils.py")
        self.assertIsNotNone(data)
        self.assertEqual(data["relationship_type"], "imports")
        self.assertEqual(data["confidence"], 1.0)

    def test_external_import_not_in_graph(self):
        """
        Importing an external package (e.g. `import os`) must not create an
        edge -- os.py does not exist inside the repo.
        """
        root = make_repo({"main.py": "import os\nimport sys\n"})
        g = build_dependency_graph(root)
        # The only node is main.py; no edges.
        self.assertEqual(list(g.nodes()), ["main.py"])
        self.assertEqual(list(g.edges()), [])

    def test_resolve_module_finds_package_init(self):
        """A dotted name ending in a package should resolve to __init__.py."""
        root = make_repo({"mypkg/__init__.py": ""})
        result = _resolve_module("mypkg", root)
        self.assertEqual(result, "mypkg/__init__.py")

    def test_resolve_module_misses_external(self):
        """A module that doesn't exist in the repo must return None."""
        root = make_repo({"main.py": ""})
        result = _resolve_module("requests", root)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Relative import resolution
# ---------------------------------------------------------------------------

class TestRelativeImportResolution(unittest.TestCase):

    def setUp(self):
        """
        Repo layout:
            pkg/__init__.py
            pkg/core.py
            pkg/utils.py     <- `from .core import X` (single dot)
            pkg/sub/__init__.py
            pkg/sub/thing.py <- `from ..utils import Y` (two dots)
        """
        self.root = make_repo({
            "pkg/__init__.py": "",
            "pkg/core.py": "CORE = 1",
            "pkg/utils.py": "from .core import CORE",
            "pkg/sub/__init__.py": "",
            "pkg/sub/thing.py": "from ..utils import something",
        })

    def test_single_dot_relative(self):
        """pkg/utils.py imports .core -> edge to pkg/core.py."""
        g = build_dependency_graph(self.root)
        pairs = edges_as_pairs(g)
        self.assertIn(("pkg/utils.py", "pkg/core.py"), pairs)

    def test_two_dot_relative(self):
        """pkg/sub/thing.py imports ..utils -> edge to pkg/utils.py."""
        g = build_dependency_graph(self.root)
        pairs = edges_as_pairs(g)
        self.assertIn(("pkg/sub/thing.py", "pkg/utils.py"), pairs)

    def test_relative_edge_direction(self):
        """Relative import edges also go from importer to importee."""
        g = build_dependency_graph(self.root)
        pairs = edges_as_pairs(g)
        self.assertNotIn(("pkg/core.py", "pkg/utils.py"), pairs)

    def test_resolve_relative_single_dot(self):
        """_resolve_relative_import with level=1 should find sibling module."""
        result = _resolve_relative_import("core", 1, "pkg/utils.py", self.root)
        self.assertEqual(result, "pkg/core.py")

    def test_resolve_relative_two_dots(self):
        """_resolve_relative_import with level=2 should reach parent package."""
        result = _resolve_relative_import("utils", 2, "pkg/sub/thing.py", self.root)
        self.assertEqual(result, "pkg/utils.py")

    def test_resolve_relative_none_module(self):
        """A bare `from . import X` (module=None) returns None gracefully."""
        result = _resolve_relative_import(None, 1, "pkg/utils.py", self.root)
        self.assertIsNone(result)

    def test_unresolvable_relative_returns_none(self):
        """A relative import pointing to a non-existent file returns None."""
        result = _resolve_relative_import("nonexistent", 1, "pkg/utils.py", self.root)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------

class TestBlastRadius(unittest.TestCase):

    def setUp(self):
        """
        Dependency chain: a.py -> b.py -> c.py
        And also:         d.py -> b.py  (d also imports b)

        blast radius of c.py should be:
            direct:   [b.py]
            indirect: [a.py, d.py]
            total:    3
        """
        self.root = make_repo({
            "c.py": "C = 1",
            "b.py": "from c import C",
            "a.py": "from b import C",
            "d.py": "from b import C",
        })
        self.graph = build_dependency_graph(self.root)

    def test_direct_dependents(self):
        """Files one import hop away from c.py."""
        br = get_blast_radius(self.graph, "c.py")
        self.assertEqual(br["direct_dependents"], ["b.py"])

    def test_indirect_dependents(self):
        """Files two or more hops away from c.py."""
        br = get_blast_radius(self.graph, "c.py")
        self.assertCountEqual(br["indirect_dependents"], ["a.py", "d.py"])

    def test_total_count(self):
        """Total must be direct + indirect."""
        br = get_blast_radius(self.graph, "c.py")
        self.assertEqual(br["total"], 3)

    def test_leaf_has_no_dependents(self):
        """a.py and d.py are importers, not imported -- their blast radius is 0."""
        br_a = get_blast_radius(self.graph, "a.py")
        self.assertEqual(br_a["total"], 0)
        self.assertEqual(br_a["direct_dependents"], [])

    def test_unknown_file_returns_zero(self):
        """Querying a file not in the graph returns a zeroed result."""
        br = get_blast_radius(self.graph, "doesnotexist.py")
        self.assertEqual(br["total"], 0)


# ---------------------------------------------------------------------------
# Graph completeness
# ---------------------------------------------------------------------------

class TestGraphCompleteness(unittest.TestCase):

    def test_isolated_file_is_still_a_node(self):
        """
        A file with no imports and nothing importing it must still appear
        as a node in the graph so we know the file exists.
        """
        root = make_repo({"standalone.py": "X = 42\n"})
        g = build_dependency_graph(root)
        self.assertIn("standalone.py", g.nodes())
        self.assertEqual(len(list(g.edges())), 0)

    def test_get_edges_returns_dataclass_list(self):
        """get_edges must return a list of DependencyEdge objects."""
        root = make_repo({
            "a.py": "import b",
            "b.py": "",
        })
        g = build_dependency_graph(root)
        edges = get_edges(g)
        self.assertEqual(len(edges), 1)
        e = edges[0]
        self.assertEqual(e.source_file, "a.py")
        self.assertEqual(e.target_file, "b.py")
        self.assertEqual(e.relationship_type, "imports")
        self.assertEqual(e.confidence, 1.0)

    def test_no_self_loops(self):
        """A file must never have an edge pointing to itself."""
        root = make_repo({"pkg/__init__.py": "", "pkg/mod.py": "from pkg import mod"})
        g = build_dependency_graph(root)
        self.assertFalse(any(src == dst for src, dst in g.edges()))


if __name__ == "__main__":
    unittest.main()
