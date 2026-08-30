"""
Unit tests for src/adapters/dispatcher.py.

Each test builds a small temporary directory tree and checks which
adapter(s) get_adapters() selects for it. No real git repo is needed --
the dispatcher only looks at file extensions, never file contents or git
history.

Coverage targets: correct adapter selection for a recognized language,
no adapters for an unrecognized repo, skip-dirs being respected during
extension detection, and case-insensitive extension matching.
"""

import os
import tempfile
import unittest

from src.adapters.dispatcher import get_adapters
from src.adapters import python_adapter


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_tree(files: dict[str, str]) -> str:
    """
    Create a temporary directory containing the given files and return its
    path. `files` maps relative paths to file contents (contents are never
    read by the dispatcher, but real files are still needed on disk).
    """
    tmp = tempfile.mkdtemp()
    for rel_path, content in files.items():
        full = os.path.join(tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    return tmp


# ---------------------------------------------------------------------------
# Adapter selection
# ---------------------------------------------------------------------------

class TestAdapterSelection(unittest.TestCase):

    def test_python_repo_selects_python_adapter(self):
        """A repo containing .py files must select python_adapter.analyze."""
        root = make_tree({"main.py": "import os"})
        adapters = get_adapters(root)
        self.assertEqual(adapters, [python_adapter.analyze])

    def test_docs_only_repo_selects_no_adapter(self):
        """A repo with only non-code files must return an empty adapter list."""
        root = make_tree({
            "README.md": "# hello",
            "config.json": "{}",
        })
        self.assertEqual(get_adapters(root), [])

    def test_empty_repo_selects_no_adapter(self):
        """A completely empty directory must return an empty adapter list,
        not raise an error."""
        root = tempfile.mkdtemp()
        self.assertEqual(get_adapters(root), [])

    def test_mixed_extensions_still_selects_python_once(self):
        """A repo with .py alongside unrelated extensions must select
        python_adapter exactly once, not duplicated."""
        root = make_tree({
            "main.py": "import os",
            "utils.py": "X = 1",
            "README.md": "# hello",
            "data.json": "{}",
        })
        adapters = get_adapters(root)
        self.assertEqual(adapters, [python_adapter.analyze])

    def test_uppercase_extension_still_detected(self):
        """Extension matching must be case-insensitive: .PY must trigger
        python_adapter just like .py does."""
        root = make_tree({"MAIN.PY": "import os"})
        self.assertEqual(get_adapters(root), [python_adapter.analyze])


# ---------------------------------------------------------------------------
# Skip-dirs and hidden directories
# ---------------------------------------------------------------------------

class TestSkipDirsRespected(unittest.TestCase):

    def test_py_file_inside_node_modules_not_detected(self):
        """A .py file sitting only inside node_modules must not trigger
        adapter selection -- vendor/dependency directories are excluded
        from extension detection entirely."""
        root = make_tree({"node_modules/somepkg/script.py": "x = 1"})
        self.assertEqual(get_adapters(root), [])

    def test_py_file_inside_git_dir_not_detected(self):
        """A .py file sitting inside .git must not trigger adapter selection."""
        root = make_tree({".git/hooks/pre-commit.py": "x = 1"})
        self.assertEqual(get_adapters(root), [])

    def test_py_file_inside_hidden_dir_not_detected(self):
        """A .py file inside any hidden directory (leading dot) must not
        trigger adapter selection, matching the same rule the Python
        adapter itself uses when walking the repo."""
        root = make_tree({".cache/tmp/leftover.py": "x = 1"})
        self.assertEqual(get_adapters(root), [])

    def test_real_python_file_alongside_skipped_dirs_still_detected(self):
        """Skip-dirs must only exclude files inside them -- a real,
        top-level .py file in the same repo must still be detected."""
        root = make_tree({
            "node_modules/somepkg/script.py": "x = 1",
            "main.py": "import os",
        })
        self.assertEqual(get_adapters(root), [python_adapter.analyze])


if __name__ == "__main__":
    unittest.main()
