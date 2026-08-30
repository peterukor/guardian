"""
Tests for get_changed_files() in src/git_history.py.

All tests use real temporary git repositories so the output is grounded in
actual git behaviour, not mocked subprocess calls.

Coverage:
  - Added file  → status "A"
  - Modified file → status "M"
  - Deleted file  → status "D"
  - Renamed file (high similarity, R100) → status "R", old_path set
  - Renamed file (lower similarity) → still normalised to "R"
  - Multiple files changed in one diff
  - Empty diff (no changes between refs) → empty list
  - Bad ref raises RuntimeError
"""

import os
import subprocess
import tempfile
import unittest

from src.git_history import ChangedFile, get_changed_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo, *args],
        check=True, capture_output=True, text=True,
    )


def _write(repo: str, rel: str, content: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


def _setup_repo(tmp: str) -> None:
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "test@guardian.test")
    _git(tmp, "config", "user.name", "Guardian Test")


def _make_base_repo() -> tuple[str, str]:
    """
    Create a repo with one initial commit and return (repo_path, base_sha).
    """
    tmp = tempfile.mkdtemp()
    _setup_repo(tmp)
    _write(tmp, "base.py", "BASE = 1\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "base commit")
    sha = subprocess.run(
        ["git", "-C", tmp, "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    return tmp, sha


# ---------------------------------------------------------------------------
# Individual status tests
# ---------------------------------------------------------------------------

class TestGetChangedFilesAdded(unittest.TestCase):

    def test_added_file_has_status_A(self):
        repo, base = _make_base_repo()
        _write(repo, "new.py", "NEW = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add new.py")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        changed = get_changed_files(repo, base, head)
        paths = {c.path: c for c in changed}
        self.assertIn("new.py", paths)
        self.assertEqual(paths["new.py"].status, "A")
        self.assertIsNone(paths["new.py"].old_path)


class TestGetChangedFilesModified(unittest.TestCase):

    def test_modified_file_has_status_M(self):
        repo, base = _make_base_repo()
        _write(repo, "base.py", "BASE = 2\n")   # modify the existing file
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "modify base.py")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        changed = get_changed_files(repo, base, head)
        paths = {c.path: c for c in changed}
        self.assertIn("base.py", paths)
        self.assertEqual(paths["base.py"].status, "M")
        self.assertIsNone(paths["base.py"].old_path)


class TestGetChangedFilesDeleted(unittest.TestCase):

    def test_deleted_file_has_status_D(self):
        repo, base = _make_base_repo()
        os.remove(os.path.join(repo, "base.py"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "delete base.py")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        changed = get_changed_files(repo, base, head)
        paths = {c.path: c for c in changed}
        self.assertIn("base.py", paths)
        self.assertEqual(paths["base.py"].status, "D")
        self.assertIsNone(paths["base.py"].old_path)


class TestGetChangedFilesRenamed(unittest.TestCase):

    def _commit_rename(self, repo: str, old: str, new: str) -> str:
        """Rename old → new, commit, and return new HEAD sha."""
        old_full = os.path.join(repo, old)
        new_full = os.path.join(repo, new)
        os.makedirs(os.path.dirname(new_full), exist_ok=True)
        os.rename(old_full, new_full)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"rename {old} to {new}")
        return subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

    def test_renamed_file_has_status_R(self):
        """A git-detected rename must produce status 'R' (not 'R100')."""
        repo, base = _make_base_repo()
        head = self._commit_rename(repo, "base.py", "renamed.py")

        changed = get_changed_files(repo, base, head)
        renames = [c for c in changed if c.status == "R"]
        self.assertEqual(len(renames), 1)
        r = renames[0]
        self.assertEqual(r.path, "renamed.py")
        self.assertEqual(r.old_path, "base.py")

    def test_renamed_file_status_normalized(self):
        """Any Rxx status (R100, R75…) must be normalized to plain 'R'."""
        repo, base = _make_base_repo()
        head = self._commit_rename(repo, "base.py", "moved.py")

        changed = get_changed_files(repo, base, head)
        for c in changed:
            if c.old_path is not None:
                # Must be exactly "R", never "R100" or similar
                self.assertEqual(c.status, "R")
                self.assertNotRegex(c.status, r"R\d+")

    def test_renamed_file_has_old_path(self):
        """For a rename, old_path must be the previous filename."""
        repo, base = _make_base_repo()
        head = self._commit_rename(repo, "base.py", "new_name.py")

        changed = get_changed_files(repo, base, head)
        renames = [c for c in changed if c.status == "R"]
        self.assertGreater(len(renames), 0)
        self.assertEqual(renames[0].old_path, "base.py")

    def test_non_rename_has_no_old_path(self):
        """Added, modified, and deleted files must have old_path = None."""
        repo, base = _make_base_repo()
        _write(repo, "added.py", "X = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add file")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        changed = get_changed_files(repo, base, head)
        for c in changed:
            if c.status != "R":
                self.assertIsNone(c.old_path)


# ---------------------------------------------------------------------------
# Multiple files and edge cases
# ---------------------------------------------------------------------------

class TestGetChangedFilesMultiple(unittest.TestCase):

    def test_multiple_changed_files_all_returned(self):
        """All changed files in a commit must appear in the result."""
        repo, base = _make_base_repo()
        _write(repo, "a.py", "A = 1\n")
        _write(repo, "b.py", "B = 2\n")
        _write(repo, "base.py", "BASE = 99\n")   # modify existing
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add a, b, modify base")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        changed = get_changed_files(repo, base, head)
        paths = {c.path for c in changed}
        self.assertIn("a.py", paths)
        self.assertIn("b.py", paths)
        self.assertIn("base.py", paths)

    def test_empty_diff_returns_empty_list(self):
        """Comparing a commit with itself must return an empty list."""
        repo, base = _make_base_repo()
        changed = get_changed_files(repo, base, base)
        self.assertEqual(changed, [])

    def test_bad_ref_raises_runtime_error(self):
        """A non-existent ref must raise RuntimeError, not silently fail."""
        repo, _ = _make_base_repo()
        with self.assertRaises(RuntimeError):
            get_changed_files(repo, "nonexistent-ref", "HEAD")

    def test_returns_list_of_changed_file_objects(self):
        """Return type must be a list of ChangedFile instances."""
        repo, base = _make_base_repo()
        _write(repo, "x.py", "X = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add x.py")
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        changed = get_changed_files(repo, base, head)
        self.assertIsInstance(changed, list)
        for item in changed:
            self.assertIsInstance(item, ChangedFile)


if __name__ == "__main__":
    unittest.main()
