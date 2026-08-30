"""
Unit tests for src/git_history.py.

Test structure:
  TestIsBugFixCommit         — pure classifier, no git needed; cover many
                               message strings including tricky partial-match
                               cases.
  TestCountBugFixCommits     — pure aggregation over a list of CommitInfo.
  TestComputeTopAuthorPct    — pure ownership calculation.
  TestGetLastTouch           — newest-first ordering and empty-list handling.
  TestParseLogOutput         — internal parser tested with canned git output.
  TestGitIntegration         — real temporary git repos to confirm fetch,
                               rename-follow, and empty-history handling.
"""

import os
import subprocess
import tempfile
import unittest

from src.git_history import (
    CommitInfo,
    FileHistory,
    _parse_log_output,
    compute_top_author_pct,
    count_bug_fix_commits,
    fetch_file_commits,
    get_file_history,
    get_last_touch,
    get_repo_state,
    is_bug_fix_commit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_commit(hash_: str = "abc123", author: str = "Alice",
                date: str = "2024-01-01", message: str = "add feature") -> CommitInfo:
    """Build a CommitInfo with sensible defaults for test reuse."""
    return CommitInfo(hash=hash_, author=author, date=date, message=message)


# ---------------------------------------------------------------------------
# Bug-fix classifier (pure function)
# ---------------------------------------------------------------------------

class TestIsBugFixCommit(unittest.TestCase):
    """
    The classifier must match bug-fix keywords at whole-word boundaries and
    must not produce false positives from partial matches.
    """

    # --- expected True --------------------------------------------------

    def test_plain_fix(self):
        self.assertTrue(is_bug_fix_commit("fix null pointer exception"))

    def test_fix_uppercase(self):
        self.assertTrue(is_bug_fix_commit("Fix the broken login flow"))

    def test_fixes(self):
        self.assertTrue(is_bug_fix_commit("fixes #42"))

    def test_fixed(self):
        self.assertTrue(is_bug_fix_commit("fixed crash on startup"))

    def test_fixing(self):
        self.assertTrue(is_bug_fix_commit("fixing edge case in parser"))

    def test_bug_keyword(self):
        self.assertTrue(is_bug_fix_commit("bug: incorrect rounding"))

    def test_bug_uppercase(self):
        self.assertTrue(is_bug_fix_commit("BUG: missing null check"))

    def test_hotfix(self):
        self.assertTrue(is_bug_fix_commit("hotfix: revert payment change"))

    def test_hotfix_uppercase(self):
        self.assertTrue(is_bug_fix_commit("HOTFIX production outage"))

    def test_revert(self):
        self.assertTrue(is_bug_fix_commit('Revert "add experimental feature"'))

    def test_revert_lowercase(self):
        self.assertTrue(is_bug_fix_commit("revert bad merge"))

    def test_github_issue_ref(self):
        self.assertTrue(is_bug_fix_commit("close #99"))

    def test_jira_issue_ref(self):
        self.assertTrue(is_bug_fix_commit("JIRA-123 crash in payment flow"))

    def test_issue_ref_in_middle(self):
        self.assertTrue(is_bug_fix_commit("resolves GH-7 by adding validation"))

    def test_jira_issue_ref_closes(self):
        self.assertTrue(is_bug_fix_commit("closes JIRA-99"))

    def test_false_positive_readme_dash_year(self):
        """'README-2024' must NOT match — not an issue tracker reference."""
        self.assertFalse(is_bug_fix_commit("Update README-2024 with new install steps"))

    def test_false_positive_chapter_dash_number(self):
        """'CHAPTER-5' must NOT match — not an issue tracker reference."""
        self.assertFalse(is_bug_fix_commit("add CHAPTER-5 to the docs"))

    # --- expected False (false-positive guard) ---------------------------

    def test_no_match_normal_feature(self):
        self.assertFalse(is_bug_fix_commit("add user authentication"))

    def test_no_match_refactor(self):
        self.assertFalse(is_bug_fix_commit("refactor payment module"))

    def test_partial_fix_in_prefix_word(self):
        """'prefix' contains 'fix' but must NOT match."""
        self.assertFalse(is_bug_fix_commit("add prefix to all log messages"))

    def test_partial_fix_in_suffix_word(self):
        """'suffix' contains 'fix' but must NOT match."""
        self.assertFalse(is_bug_fix_commit("append suffix to filename"))

    def test_partial_bug_in_debug(self):
        """'debug' contains 'bug' but must NOT match."""
        self.assertFalse(is_bug_fix_commit("add debug logging"))

    def test_partial_bug_in_buggy(self):
        """'buggy' is not a keyword match (only 'bug' as whole word)."""
        # 'buggy' does contain 'bug' at a word boundary start — this is
        # intentionally ambiguous; our regex uses \b which WILL match 'bug'
        # in 'buggy'.  This test documents the known behavior rather than
        # asserting a strict false negative.
        result = is_bug_fix_commit("remove buggy code path")
        # 'buggy' starts with 'bug' followed by 'gy' — \bbug\b won't match
        # because 'g' after 'bug' is still a word character.
        self.assertFalse(result)

    def test_no_match_empty_string(self):
        self.assertFalse(is_bug_fix_commit(""))

    def test_no_match_chore(self):
        self.assertFalse(is_bug_fix_commit("chore: update dependencies"))

    def test_no_match_docs(self):
        self.assertFalse(is_bug_fix_commit("docs: improve README"))

    def test_no_match_feat(self):
        self.assertFalse(is_bug_fix_commit("feat: add dark mode"))


# ---------------------------------------------------------------------------
# count_bug_fix_commits (pure)
# ---------------------------------------------------------------------------

class TestCountBugFixCommits(unittest.TestCase):

    def test_empty_list(self):
        self.assertEqual(count_bug_fix_commits([]), 0)

    def test_no_bug_fixes(self):
        commits = [
            make_commit(message="feat: add user auth"),
            make_commit(message="refactor: clean up parser"),
        ]
        self.assertEqual(count_bug_fix_commits(commits), 0)

    def test_all_bug_fixes(self):
        commits = [
            make_commit(message="fix null dereference"),
            make_commit(message="hotfix: payment crash"),
            make_commit(message="revert bad migration"),
        ]
        self.assertEqual(count_bug_fix_commits(commits), 3)

    def test_mixed_commits(self):
        commits = [
            make_commit(message="feat: add API endpoint"),
            make_commit(message="fix login redirect"),
            make_commit(message="docs: update README"),
            make_commit(message="bug: incorrect total calculation"),
        ]
        self.assertEqual(count_bug_fix_commits(commits), 2)

    def test_single_bug_fix(self):
        commits = [make_commit(message="fix: crash on empty input")]
        self.assertEqual(count_bug_fix_commits(commits), 1)


# ---------------------------------------------------------------------------
# compute_top_author_pct (pure)
# ---------------------------------------------------------------------------

class TestComputeTopAuthorPct(unittest.TestCase):

    def test_empty_list_returns_zero(self):
        self.assertAlmostEqual(compute_top_author_pct([]), 0.0)

    def test_single_author_all_commits(self):
        commits = [
            make_commit(author="Alice"),
            make_commit(author="Alice"),
            make_commit(author="Alice"),
        ]
        self.assertAlmostEqual(compute_top_author_pct(commits), 1.0)

    def test_equal_split_two_authors(self):
        commits = [
            make_commit(author="Alice"),
            make_commit(author="Bob"),
        ]
        self.assertAlmostEqual(compute_top_author_pct(commits), 0.5)

    def test_dominant_author(self):
        """One author with 3 out of 4 commits → 75%."""
        commits = [
            make_commit(author="Alice"),
            make_commit(author="Alice"),
            make_commit(author="Alice"),
            make_commit(author="Bob"),
        ]
        self.assertAlmostEqual(compute_top_author_pct(commits), 0.75)

    def test_three_authors_unequal(self):
        """Alice:3, Bob:2, Carol:1 → top = Alice = 3/6 = 0.5."""
        commits = (
            [make_commit(author="Alice")] * 3
            + [make_commit(author="Bob")] * 2
            + [make_commit(author="Carol")] * 1
        )
        self.assertAlmostEqual(compute_top_author_pct(commits), 0.5)

    def test_single_commit_single_author(self):
        commits = [make_commit(author="Solo")]
        self.assertAlmostEqual(compute_top_author_pct(commits), 1.0)


# ---------------------------------------------------------------------------
# get_last_touch (pure)
# ---------------------------------------------------------------------------

class TestGetLastTouch(unittest.TestCase):

    def test_empty_list_returns_none(self):
        commit, date = get_last_touch([])
        self.assertIsNone(commit)
        self.assertIsNone(date)

    def test_single_commit(self):
        commits = [make_commit(hash_="abc123", date="2024-06-01")]
        commit, date = get_last_touch(commits)
        self.assertEqual(commit, "abc123")
        self.assertEqual(date, "2024-06-01")

    def test_first_element_is_newest(self):
        """git log is newest-first; get_last_touch must return commits[0]."""
        commits = [
            make_commit(hash_="newest", date="2024-12-01"),
            make_commit(hash_="older",  date="2024-01-01"),
        ]
        commit, date = get_last_touch(commits)
        self.assertEqual(commit, "newest")
        self.assertEqual(date, "2024-12-01")

    def test_date_is_iso8601_string(self):
        """The returned date must be a string in ISO-8601 format."""
        commits = [make_commit(date="2023-07-15")]
        _, date = get_last_touch(commits)
        self.assertIsInstance(date, str)
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# _parse_log_output (internal parser, tested with canned output)
# ---------------------------------------------------------------------------

class TestParseLogOutput(unittest.TestCase):
    """
    Test the log parser directly with hand-crafted strings that mimic exactly
    what `git log --format=...` would produce — avoids needing a real repo for
    parser correctness.
    """

    # The record separator (RS) and field separator (FS) used in the format.
    RS = "\x1e"
    FS = "\x1f"

    def _make_raw(self, commits: list[tuple[str, str, str, str]]) -> str:
        """Build a fake git log stdout from a list of (hash, author, date, msg) tuples."""
        parts = []
        for hash_, author, date, msg in commits:
            parts.append(f"{self.FS}{hash_}{self.FS}{author}{self.FS}{date}{self.FS}{msg}{self.RS}")
        return "".join(parts)

    def test_single_commit(self):
        raw = self._make_raw([("abc123", "Alice", "2024-03-15", "fix: crash")])
        result = _parse_log_output(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].hash, "abc123")
        self.assertEqual(result[0].author, "Alice")
        self.assertEqual(result[0].date, "2024-03-15")
        self.assertEqual(result[0].message, "fix: crash")

    def test_multiple_commits(self):
        raw = self._make_raw([
            ("aaa", "Alice", "2024-06-01", "feat: add login"),
            ("bbb", "Bob",   "2024-05-10", "fix: null pointer"),
            ("ccc", "Carol", "2024-04-20", "docs: update readme"),
        ])
        result = _parse_log_output(raw)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].hash, "aaa")
        self.assertEqual(result[1].author, "Bob")
        self.assertEqual(result[2].date, "2024-04-20")

    def test_empty_output(self):
        """An empty string (no commits) must return an empty list."""
        self.assertEqual(_parse_log_output(""), [])

    def test_whitespace_only_output(self):
        self.assertEqual(_parse_log_output("   \n  "), [])

    def test_preserves_order(self):
        """Parser must preserve the git-log order (newest first)."""
        raw = self._make_raw([
            ("first",  "A", "2024-12-01", "newest commit"),
            ("second", "B", "2024-01-01", "oldest commit"),
        ])
        result = _parse_log_output(raw)
        self.assertEqual(result[0].hash, "first")
        self.assertEqual(result[1].hash, "second")


# ---------------------------------------------------------------------------
# Git integration tests (real temporary repos)
# ---------------------------------------------------------------------------

def _git(repo_dir: str, *args: str) -> None:
    """Run a git command in repo_dir, raising on failure."""
    subprocess.run(
        ["git", "-C", repo_dir, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo_dir: str, filename: str, content: str) -> None:
    """Write content to filename inside repo_dir."""
    path = os.path.join(repo_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _setup_repo(tmp_dir: str) -> None:
    """Initialise a minimal git repo with identity config so commits work."""
    _git(tmp_dir, "init")
    _git(tmp_dir, "config", "user.email", "test@guardian.test")
    _git(tmp_dir, "config", "user.name", "Guardian Test")


class TestGitIntegration(unittest.TestCase):
    """
    Tests that require a real (temporary) git repository.
    Each test creates its own isolated repo in a temp directory and cleans
    up afterward, so tests are fully independent.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- fetch_file_commits ---

    def test_fetch_returns_commits_newest_first(self):
        """fetch_file_commits must return commits in newest-first order."""
        _write(self.tmp, "app.py", "v1")
        _git(self.tmp, "add", "app.py")
        _git(self.tmp, "commit", "-m", "initial add")

        _write(self.tmp, "app.py", "v2")
        _git(self.tmp, "add", "app.py")
        _git(self.tmp, "commit", "-m", "update app")

        commits = fetch_file_commits(self.tmp, "app.py")
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0].message, "update app")
        self.assertEqual(commits[1].message, "initial add")

    def test_fetch_empty_history_for_untracked_file(self):
        """A file that exists on disk but was never committed has no history."""
        _write(self.tmp, "new.py", "content")
        # Do NOT add or commit — untracked file.
        commits = fetch_file_commits(self.tmp, "new.py")
        self.assertEqual(commits, [])

    def test_fetch_commit_fields(self):
        """All four CommitInfo fields must be populated correctly."""
        _write(self.tmp, "calc.py", "def add(a, b): return a+b")
        _git(self.tmp, "add", "calc.py")
        _git(self.tmp, "commit", "-m", "feat: add calculator")

        commits = fetch_file_commits(self.tmp, "calc.py")
        self.assertEqual(len(commits), 1)
        c = commits[0]
        self.assertTrue(len(c.hash) >= 7)
        self.assertEqual(c.author, "Guardian Test")
        # Date must be ISO-8601 YYYY-MM-DD format.
        self.assertRegex(c.date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(c.message, "feat: add calculator")

    def test_fetch_only_sees_commits_touching_target_file(self):
        """Commits to other files must not appear in the target file's history."""
        _write(self.tmp, "a.py", "a")
        _git(self.tmp, "add", "a.py")
        _git(self.tmp, "commit", "-m", "add a")

        _write(self.tmp, "b.py", "b")
        _git(self.tmp, "add", "b.py")
        _git(self.tmp, "commit", "-m", "add b")

        commits_a = fetch_file_commits(self.tmp, "a.py")
        commits_b = fetch_file_commits(self.tmp, "b.py")
        self.assertEqual(len(commits_a), 1)
        self.assertEqual(commits_a[0].message, "add a")
        self.assertEqual(len(commits_b), 1)
        self.assertEqual(commits_b[0].message, "add b")

    # --- rename-follow ---

    def test_history_followed_across_rename(self):
        """
        fetch_file_commits must return the full history of a file even after
        it has been renamed.  Without --follow, the new name would appear to
        have only the post-rename commits.
        """
        # Commit the file under its original name.
        _write(self.tmp, "old_name.py", "original content")
        _git(self.tmp, "add", "old_name.py")
        _git(self.tmp, "commit", "-m", "initial: add old_name.py")

        # Rename the file (git mv so git tracks the rename).
        old_path = os.path.join(self.tmp, "old_name.py")
        new_path = os.path.join(self.tmp, "new_name.py")
        os.rename(old_path, new_path)
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-m", "rename old_name.py to new_name.py")

        # Add one more commit under the new name.
        _write(self.tmp, "new_name.py", "updated content")
        _git(self.tmp, "add", "new_name.py")
        _git(self.tmp, "commit", "-m", "update new_name.py")

        commits = fetch_file_commits(self.tmp, "new_name.py")
        # Must see all 3 commits (including the one made under the old name).
        self.assertEqual(len(commits), 3)
        messages = [c.message for c in commits]
        self.assertIn("initial: add old_name.py", messages)
        self.assertIn("rename old_name.py to new_name.py", messages)
        self.assertIn("update new_name.py", messages)

    # --- bug-fix counting via real commits ---

    def test_bug_fix_count_from_real_commits(self):
        """bug_fix_count must reflect how many commit messages look like fixes."""
        _write(self.tmp, "mod.py", "v1")
        _git(self.tmp, "add", "mod.py")
        _git(self.tmp, "commit", "-m", "feat: initial implementation")

        _write(self.tmp, "mod.py", "v2")
        _git(self.tmp, "add", "mod.py")
        _git(self.tmp, "commit", "-m", "fix: null pointer in validate()")

        _write(self.tmp, "mod.py", "v3")
        _git(self.tmp, "add", "mod.py")
        _git(self.tmp, "commit", "-m", "hotfix: production crash")

        history = get_file_history(self.tmp, "mod.py")
        self.assertEqual(history.bug_fix_count, 2)

    # --- get_file_history (high-level) ---

    def test_get_file_history_no_history(self):
        """get_file_history for an untracked file must return safe zero-values."""
        _write(self.tmp, "untracked.py", "x = 1")
        history = get_file_history(self.tmp, "untracked.py")
        self.assertIsInstance(history, FileHistory)
        self.assertIsNone(history.last_touch_commit)
        self.assertIsNone(history.last_touch_date)
        self.assertEqual(history.bug_fix_count, 0)
        self.assertAlmostEqual(history.top_author_pct, 0.0)

    def test_get_file_history_fields(self):
        """get_file_history must populate all fields correctly from real commits."""
        _write(self.tmp, "service.py", "v1")
        _git(self.tmp, "add", "service.py")
        _git(self.tmp, "commit", "-m", "feat: add service")

        _write(self.tmp, "service.py", "v2")
        _git(self.tmp, "add", "service.py")
        _git(self.tmp, "commit", "-m", "fix: handle edge case")

        history = get_file_history(self.tmp, "service.py")
        self.assertEqual(history.path, "service.py")
        self.assertIsNotNone(history.last_touch_commit)
        self.assertRegex(history.last_touch_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(history.bug_fix_count, 1)
        # Single author → 100% ownership.
        self.assertAlmostEqual(history.top_author_pct, 1.0)

    def test_get_file_history_last_touch_is_most_recent(self):
        """last_touch_commit/date must reflect the newest commit, not the first."""
        _write(self.tmp, "item.py", "v1")
        _git(self.tmp, "add", "item.py")
        _git(self.tmp, "commit", "-m", "first commit")

        _write(self.tmp, "item.py", "v2")
        _git(self.tmp, "add", "item.py")
        _git(self.tmp, "commit", "-m", "second commit")

        commits = fetch_file_commits(self.tmp, "item.py")
        history = get_file_history(self.tmp, "item.py")

        # The most recent commit is commits[0] (newest-first).
        self.assertEqual(history.last_touch_commit, commits[0].hash)
        self.assertEqual(history.last_touch_date, commits[0].date)


# ---------------------------------------------------------------------------
# get_repo_state integration tests (real temporary repos)
# ---------------------------------------------------------------------------

class TestGetRepoState(unittest.TestCase):
    """
    Tests for get_repo_state(repo_root) using real temporary git repos.
    Each test creates and cleans up its own isolated repo.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_tuple_of_two(self):
        """get_repo_state must return a 2-tuple."""
        _write(self.tmp, "f.py", "x=1")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "init")
        result = get_repo_state(self.tmp)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_commit_hash_is_full_sha(self):
        """The first element must be a 40-character hexadecimal SHA string."""
        _write(self.tmp, "f.py", "x=1")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "init")
        commit_hash, _ = get_repo_state(self.tmp)
        self.assertIsNotNone(commit_hash)
        self.assertRegex(commit_hash, r"^[0-9a-f]{40}$")

    def test_branch_name_matches_current_branch(self):
        """The second element must be the currently checked-out branch name."""
        _write(self.tmp, "f.py", "x=1")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "init")
        _, branch = get_repo_state(self.tmp)
        self.assertIsNotNone(branch)
        # git init creates 'master' or 'main' depending on git config; accept either.
        self.assertIn(branch, ("master", "main"))

    def test_hash_matches_git_rev_parse(self):
        """The returned hash must equal what `git rev-parse HEAD` produces."""
        import subprocess as sp
        _write(self.tmp, "f.py", "x=1")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "init")
        commit_hash, _ = get_repo_state(self.tmp)
        expected = sp.run(
            ["git", "-C", self.tmp, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(commit_hash, expected)

    def test_hash_changes_after_new_commit(self):
        """The returned hash must reflect HEAD at call time, not a stale cache."""
        _write(self.tmp, "f.py", "v1")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "first")
        hash1, _ = get_repo_state(self.tmp)

        _write(self.tmp, "f.py", "v2")
        _git(self.tmp, "add", "f.py")
        _git(self.tmp, "commit", "-m", "second")
        hash2, _ = get_repo_state(self.tmp)

        self.assertNotEqual(hash1, hash2)

    def test_no_commits_returns_none_none(self):
        """A repo with no commits yet must return (None, None), not raise."""
        # Fresh repo, nothing committed.
        commit_hash, branch = get_repo_state(self.tmp)
        self.assertIsNone(commit_hash)
        self.assertIsNone(branch)


if __name__ == "__main__":
    unittest.main()
