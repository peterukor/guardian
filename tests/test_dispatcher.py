"""
Unit tests for src/adapters/dispatcher.py.

Tests confirm:
  - A repo containing .py files returns the Python adapter module.
  - A repo with no recognized files returns an empty list.
  - The returned value is the adapter module itself (not a bare callable),
    exposing both analyze() and discovered_files().
  - Adding a second extension type alongside .py doesn't suppress the Python
    adapter (intersection-based matching).
"""

import os
import tempfile
import unittest

from src.adapters import python_adapter
from src.adapters.dispatcher import get_adapters


def _make_repo(files: dict[str, str]) -> str:
    """Create a temp directory with the given filename -> content mapping."""
    tmp = tempfile.mkdtemp()
    for rel, content in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    return tmp


class TestGetAdapters(unittest.TestCase):

    def test_python_repo_returns_python_adapter(self):
        """A repo with .py files must return exactly the python_adapter module."""
        root = _make_repo({"main.py": "x = 1"})
        adapters = get_adapters(root)
        self.assertEqual(len(adapters), 1)
        self.assertIs(adapters[0], python_adapter)

    def test_no_known_files_returns_empty_list(self):
        """A repo with only unknown extensions (e.g. .md) returns []."""
        root = _make_repo({"README.md": "# hi", "config.yaml": "key: val"})
        adapters = get_adapters(root)
        self.assertEqual(adapters, [])

    def test_empty_directory_returns_empty_list(self):
        """An empty repo directory returns no adapters."""
        root = tempfile.mkdtemp()
        self.assertEqual(get_adapters(root), [])

    def test_returned_value_is_module_not_callable(self):
        """Each entry in the returned list must be an adapter module, not a
        bare function — so callers can call both analyze() and discovered_files()."""
        import types
        root = _make_repo({"app.py": "pass"})
        adapters = get_adapters(root)
        self.assertEqual(len(adapters), 1)
        self.assertIsInstance(adapters[0], types.ModuleType)

    def test_returned_module_exposes_analyze(self):
        """The adapter module must have a callable analyze attribute."""
        root = _make_repo({"app.py": "pass"})
        adapter = get_adapters(root)[0]
        self.assertTrue(callable(getattr(adapter, "analyze", None)))

    def test_returned_module_exposes_discovered_files(self):
        """The adapter module must have a callable discovered_files attribute."""
        root = _make_repo({"app.py": "pass"})
        adapter = get_adapters(root)[0]
        self.assertTrue(callable(getattr(adapter, "discovered_files", None)))

    def test_mixed_extensions_still_returns_python_adapter(self):
        """A repo with .py files alongside other extensions still matches Python."""
        root = _make_repo({
            "main.py": "pass",
            "README.md": "docs",
            "config.json": "{}",
        })
        adapters = get_adapters(root)
        self.assertIn(python_adapter, adapters)

    def test_py_in_subdirectory_triggers_adapter(self):
        """Detection must recurse into subdirectories."""
        root = _make_repo({"pkg/utils.py": "UTIL = 1"})
        adapters = get_adapters(root)
        self.assertIn(python_adapter, adapters)


if __name__ == "__main__":
    unittest.main()
