"""
Pure aggregation functions for Guardian's Git History module.

No git I/O -- these operate on an already-parsed list[CommitInfo], so they
are directly testable with canned data.
"""

from __future__ import annotations

from src.git_history.classifier import is_bug_fix_commit
from src.git_history.types import CommitInfo


def count_bug_fix_commits(commits: list[CommitInfo]) -> int:
    """
    Count how many commits in the list are classified as bug fixes.
    Pure function — operates on already-parsed CommitInfo objects.
    """
    return sum(1 for c in commits if is_bug_fix_commit(c.message))


def compute_top_author_pct(commits: list[CommitInfo]) -> float:
    """
    Return the fraction of commits (0.0–1.0) authored by the single most
    active author.  Returns 0.0 for an empty commit list.

    This is the ownership_concentration signal fed into the Risk Scorer.
    A value close to 1.0 means almost all commits come from one person —
    high bus-factor risk.
    """
    if not commits:
        return 0.0
    counts: dict[str, int] = {}
    for c in commits:
        counts[c.author] = counts.get(c.author, 0) + 1
    top_count = max(counts.values())
    return top_count / len(commits)


def get_last_touch(commits: list[CommitInfo]) -> tuple[str | None, str | None]:
    """
    Return (commit_hash, date) of the most recent commit, or (None, None) if
    the list is empty. git log returns commits newest-first, so the first
    element is always the most recent.
    """
    if not commits:
        return None, None
    latest = commits[0]
    return latest.hash, latest.date
