"""
Unit tests for src/adapters/python_adapter.py and src/adapters/dispatcher.py.
Focus: import-resolution edge cases that are easy to get subtly wrong
(compound names, relative imports, duplicate-edge bugs), blast-radius
correctness, and adapter dispatch. Trivial glue is not covered here.
"""

import os
import tempfile
import textwrap
import unittest

from src.adapters import python_adapter
from src.adapters.dispatcher import get_adapters
from src.adapters.python_adapter import (
    _extract_imports,
    _resolve_relative_import,
    analyze,
    build_dependency_graph,
    discovered_files,
    get_blast_radius,
)


def make_repo(files: dict[str, str]) -> str:
    tmp = tempfile.mkdtemp()
    for rel, code in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(code))
    return tmp
def edges_as_pairs(graph) -> set[tuple[str, str]]:
    return {(src, dst) for src, dst, _ in graph.edges(data=True)}

class TestExtractImports(unittest.TestCase):
    """ast-level parsing quirks that are easy to get wrong."""
    def test_absolute_and_relative_imports_record_expected_name_and_level(self):
        result = _extract_imports("from src import analyzer")
        self.assertIn(("src.analyzer", 0), result)
        self.assertNotIn(("src", 0), result)  # compound name only, not the parent
        self.assertIn(("utils", 1), _extract_imports("from .utils import helper"))
        self.assertIn(("base", 2), _extract_imports("from ..base import core"))

    def test_bare_relative_dot_and_syntax_error(self):
        result = _extract_imports("from . import something")
        self.assertIn(("something", 1), result)
        self.assertNotIn((None, 1), result)
        self.assertEqual(_extract_imports("def (broken !!!"), [])

class TestImportResolution(unittest.TestCase):
    """build_dependency_graph()'s end-to-end resolution, blast radius, and
    the discovered_files()/analyze() interface contract."""
    def test_absolute_import_edge_direction_and_attributes(self):
        root = make_repo({"pkg/__init__.py": "", "pkg/utils.py": "x = 1",
                           "main.py": "from pkg import utils"})
        g = build_dependency_graph(root)
        pairs = edges_as_pairs(g)
        self.assertIn(("main.py", "pkg/utils.py"), pairs)
        self.assertNotIn(("pkg/utils.py", "main.py"), pairs)
        data = g.get_edge_data("main.py", "pkg/utils.py")
        self.assertEqual(data["relationship_type"], "imports")
        self.assertEqual(data["confidence"], 1.0)

    def test_external_import_produces_no_edge(self):
        root = make_repo({"main.py": "import os\nimport sys\n"})
        self.assertEqual(list(build_dependency_graph(root).edges()), [])

    def test_from_pkg_import_mod_produces_single_edge_not_duplicate(self):
        """Regression: pkg/mod.py + pkg/__init__.py both existing must not
        double up -- only the more specific pkg/mod.py gets an edge."""
        root = make_repo({"pkg/__init__.py": "# package", "pkg/mod.py": "VALUE = 1",
                           "main.py": "from pkg import mod"})
        pairs = edges_as_pairs(build_dependency_graph(root))
        self.assertIn(("main.py", "pkg/mod.py"), pairs)
        self.assertNotIn(("main.py", "pkg/__init__.py"), pairs)

    def test_from_pkg_import_symbol_falls_back_to_init(self):
        """`from pkg import SomeClass` where only __init__.py defines it."""
        root = make_repo({"pkg/__init__.py": "class SomeClass: pass",
                           "main.py": "from pkg import SomeClass"})
        pairs = edges_as_pairs(build_dependency_graph(root))
        self.assertIn(("main.py", "pkg/__init__.py"), pairs)

    def test_bare_relative_import_resolves_each_sibling_name(self):
        """`from . import a, b` (module=None) resolves every name; a
        nonexistent sibling must not produce a phantom edge."""
        root = make_repo({"pkg/__init__.py": "", "pkg/utils.py": "", "pkg/helpers.py": "",
                           "pkg/core.py": "from . import utils, helpers, nonexistent"})
        pairs = edges_as_pairs(build_dependency_graph(root))
        self.assertIn(("pkg/core.py", "pkg/utils.py"), pairs)
        self.assertIn(("pkg/core.py", "pkg/helpers.py"), pairs)
        self.assertNotIn(("pkg/core.py", "pkg/nonexistent.py"), pairs)

    def test_relative_import_levels_resolve_correct_ancestor(self):
        root = make_repo({
            "pkg/__init__.py": "", "pkg/core.py": "CORE = 1",
            "pkg/utils.py": "from .core import CORE",
            "pkg/sub/__init__.py": "", "pkg/sub/thing.py": "from ..utils import x",
        })
        self.assertEqual(_resolve_relative_import("core", 1, "pkg/utils.py", root), "pkg/core.py")
        self.assertEqual(_resolve_relative_import("utils", 2, "pkg/sub/thing.py", root), "pkg/utils.py")
        self.assertIsNone(_resolve_relative_import("nonexistent", 1, "pkg/utils.py", root))

    def test_isolated_file_is_node_with_no_self_loop(self):
        root = make_repo({"standalone.py": "X = 42\n",
                           "pkg/__init__.py": "", "pkg/mod.py": "from pkg import mod"})
        g = build_dependency_graph(root)
        self.assertIn("standalone.py", g.nodes())
        self.assertFalse(any(src == dst for src, dst in g.edges()))

    def test_blast_radius_counts_and_zero_cases(self):
        # a.py -> b.py -> c.py, d.py -> b.py
        root = make_repo({"c.py": "C = 1", "b.py": "from c import C",
                           "a.py": "from b import C", "d.py": "from b import C"})
        graph = build_dependency_graph(root)
        br = get_blast_radius(graph, "c.py")
        self.assertEqual(br["direct_dependents"], ["b.py"])
        self.assertCountEqual(br["indirect_dependents"], ["a.py", "d.py"])
        self.assertEqual(br["total"], 3)
        self.assertEqual(get_blast_radius(graph, "a.py")["total"], 0)  # leaf
        self.assertEqual(get_blast_radius(graph, "ghost.py")["total"], 0)  # unknown
    def test_discovered_files_includes_isolated_and_covers_every_analyze_edge(self):
        """discovered_files() is the authoritative file list; analyze() is edges only."""
        root = make_repo({"isolated.py": "CONSTANT = 42\n", "pkg/__init__.py": "",
                           "pkg/utils.py": "UTIL = 1", "main.py": "from pkg import utils"})
        files = set(discovered_files(root))
        self.assertIn("isolated.py", files)  # no edges, but still tracked
        for src, dst, *_ in analyze(root):
            self.assertIn(src, files)
            self.assertIn(dst, files)


class TestDispatcher(unittest.TestCase):
    def test_python_files_select_python_adapter(self):
        root = make_repo({"main.py": "x = 1", "pkg/utils.py": "UTIL = 1"})
        self.assertEqual(get_adapters(root), [python_adapter])

    def test_no_known_extensions_selects_no_adapters(self):
        root = make_repo({"README.md": "# hi", "config.yaml": "key: val"})
        self.assertEqual(get_adapters(root), [])

    def test_adapter_module_exposes_analyze_and_discovered_files(self):
        adapter = get_adapters(make_repo({"app.py": "pass"}))[0]
        self.assertTrue(callable(adapter.analyze))
        self.assertTrue(callable(adapter.discovered_files))


if __name__ == "__main__":
    unittest.main()
