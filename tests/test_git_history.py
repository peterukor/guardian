"""
Unit tests for src/git_history/ -- bug-fix classification, aggregation,
log parsing, and real-repo integration. Git I/O uses real temp repos.
"""

import os
import subprocess
import tempfile
import unittest

from src.git_history import (
    ChangedFile,
    CommitInfo,
    FileHistory,
    _parse_log_output,
    compute_top_author_pct,
    count_bug_fix_commits,
    fetch_file_commits,
    get_changed_files,
    get_file_history,
    get_last_touch,
    get_repo_state,
    is_bug_fix_commit,
)

def make_commit(hash_="abc", author="Alice", date="2024-01-01", message="add feature") -> CommitInfo:
    return CommitInfo(hash=hash_, author=author, date=date, message=message)
def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)
def _write(repo: str, rel: str, content: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
def _init_repo() -> str:
    tmp = tempfile.mkdtemp()
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@guardian.test")
    _git(tmp, "config", "user.name", "Guardian Test")
    return tmp
def _commit(repo: str, rel: str, content: str, message: str) -> str:
    _write(repo, rel, content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
def _require(x):
    """Narrow an Optional git-lookup result -- fail loudly, never chain off None."""
    assert x is not None
    return x

class TestPureFunctions(unittest.TestCase):
    """Classifier, aggregation, log-parsing -- no git I/O, no repo needed."""
    def test_bug_fix_classifier_matches_keywords_not_partial_words(self):
        for msg in ("fix null pointer", "Fixed crash", "fixing edge case", "bug: bad total",
                    "HOTFIX outage", 'Revert "bad merge"', "close #99", "JIRA-123 crash"):
            self.assertTrue(is_bug_fix_commit(msg), msg)
        for msg in ("add prefix to logs", "add debug logging", "Update README-2024",
                    "add CHAPTER-5 to docs", "feat: add dark mode"):
            self.assertFalse(is_bug_fix_commit(msg), msg)

    def test_aggregation_over_commit_lists(self):
        commits = [make_commit(message="feat: add API"), make_commit(message="fix login"),
                   make_commit(message="docs: update"), make_commit(message="bug: bad total")]
        self.assertEqual(count_bug_fix_commits(commits), 2)
        weighted = [make_commit(author="Alice")] * 3 + [make_commit(author="Bob")]
        self.assertAlmostEqual(compute_top_author_pct(weighted), 0.75)
        self.assertAlmostEqual(compute_top_author_pct([]), 0.0)
        ordered = [make_commit(hash_="newest", date="2024-12-01"),
                   make_commit(hash_="older", date="2024-01-01")]
        self.assertEqual(get_last_touch(ordered), ("newest", "2024-12-01"))
        self.assertEqual(get_last_touch([]), (None, None))

    def test_parse_log_output_order_and_empty_input(self):
        rs, fs = "\x1e", "\x1f"
        raw = "".join(f"{fs}{h}{fs}{a}{fs}{d}{fs}{m}{rs}" for h, a, d, m in
                       [("aaa", "Alice", "2024-06-01", "feat: add login"),
                        ("bbb", "Bob", "2024-05-10", "fix: null pointer")])
        result = _parse_log_output(raw)
        self.assertEqual([c.hash for c in result], ["aaa", "bbb"])
        self.assertEqual(result[1].author, "Bob")
        self.assertEqual(_parse_log_output(""), [])
        self.assertEqual(_parse_log_output("   \n "), [])


class TestGitIntegration(unittest.TestCase):
    """Real temporary repos -- confirms actual git behavior, not mocked calls."""
    def setUp(self):
        self.repo = _init_repo()
    def _head(self) -> str:
        return subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()

    def test_fetch_follows_renames_and_ignores_untracked_files(self):
        _commit(self.repo, "old_name.py", "v1", "initial: add old_name.py")
        os.rename(os.path.join(self.repo, "old_name.py"), os.path.join(self.repo, "new_name.py"))
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "rename old_name.py to new_name.py")
        _commit(self.repo, "new_name.py", "v2", "update new_name.py")
        commits = fetch_file_commits(self.repo, "new_name.py")
        self.assertEqual(len(commits), 3)
        self.assertEqual(commits[0].message, "update new_name.py")  # newest first
        _write(self.repo, "untouched.py", "content")  # never added/committed
        self.assertEqual(fetch_file_commits(self.repo, "untouched.py"), [])

    def test_get_file_history_aggregates_or_zeroes_out_cleanly(self):
        _commit(self.repo, "service.py", "v1", "feat: add service")
        _commit(self.repo, "service.py", "v2", "fix: handle edge case")
        history = get_file_history(self.repo, "service.py")
        self.assertIsInstance(history, FileHistory)
        self.assertEqual(history.bug_fix_count, 1)
        self.assertAlmostEqual(history.top_author_pct, 1.0)
        self.assertRegex(_require(history.last_touch_date), r"^\d{4}-\d{2}-\d{2}$")
        _write(self.repo, "untracked.py", "x = 1")
        untracked = get_file_history(self.repo, "untracked.py")
        self.assertIsNone(untracked.last_touch_commit)
        self.assertEqual(untracked.bug_fix_count, 0)

    def test_changed_files_status_codes_rename_and_error_handling(self):
        base = _commit(self.repo, "base.py", "BASE = 1\n", "base commit")
        _write(self.repo, "new.py", "NEW = 1\n")
        _write(self.repo, "base.py", "BASE = 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "add + modify")
        changed = {c.path: c for c in get_changed_files(self.repo, base, self._head())}
        self.assertEqual(changed["new.py"].status, "A")
        self.assertEqual(changed["base.py"].status, "M")
        self.assertEqual(get_changed_files(self.repo, base, base), [])
        with self.assertRaises(RuntimeError):
            get_changed_files(self.repo, "nonexistent-ref", "HEAD")
        # Any Rxx similarity score must normalize to plain "R", old_path set.
        base2 = self._head()
        os.rename(os.path.join(self.repo, "base.py"), os.path.join(self.repo, "renamed.py"))
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "rename")
        renames = [c for c in get_changed_files(self.repo, base2, self._head()) if c.status == "R"]
        self.assertEqual(renames, [ChangedFile(path="renamed.py", status="R", old_path="base.py")])

    def test_repo_state_tracks_head_across_commits_and_handles_no_commits(self):
        self.assertEqual(get_repo_state(self.repo), (None, None))
        _commit(self.repo, "f.py", "v1", "init")
        hash1, branch = get_repo_state(self.repo)
        self.assertRegex(_require(hash1), r"^[0-9a-f]{40}$")
        self.assertIn(branch, ("master", "main"))
        _commit(self.repo, "f.py", "v2", "second")
        hash2, _ = get_repo_state(self.repo)
        self.assertNotEqual(hash1, hash2)

if __name__ == "__main__":
    unittest.main()
