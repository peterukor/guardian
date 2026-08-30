"""
The primary high-level entry point for Guardian's Git History module --
ties fetch_file_commits() and the aggregation functions together.
"""

from __future__ import annotations

from src.git_history.aggregate import compute_top_author_pct, count_bug_fix_commits, get_last_touch
from src.git_history.fetch import fetch_file_commits
from src.git_history.types import FileHistory


def get_file_history(repo_root: str, file_path: str) -> FileHistory:
    """
    Fetch and aggregate all history signals for file_path in repo_root.

    This is the primary entry point for the scanner.  It calls fetch_file_commits
    once to get the raw commit list, then delegates all aggregation to the pure
    functions above — keeping git I/O in exactly one place.

    Returns a FileHistory with all None/zero values for files with no history
    (e.g. newly added but not yet committed files).
    """
    commits = fetch_file_commits(repo_root, file_path)
    last_commit, last_date = get_last_touch(commits)
    return FileHistory(
        path=file_path,
        last_touch_commit=last_commit,
        last_touch_date=last_date,
        bug_fix_count=count_bug_fix_commits(commits),
        top_author_pct=compute_top_author_pct(commits),
    )
