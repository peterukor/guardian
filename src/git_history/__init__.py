"""
Git History Miner for Guardian, as a package.

This is a pure file-organization split of what used to be one large
git_history.py -- no behavior changed. The public import path is unchanged:
`from src.git_history import get_file_history, get_repo_state, ...` still
works exactly as before. No other file in the project needs to change.

Extracts per-file history signals needed by the Risk Scorer:
  - last_touch_commit, last_touch_date, bug_fix_count, top_author_pct

Design rules enforced across this package:
- Only fetch.py's fetch_file_commits() and diff.py's get_changed_files() and
  repo_state.py's get_repo_state() call subprocess/git -- every other
  function operates on already-parsed data, directly testable without a
  real repo.
- fetch_file_commits always uses `git log --follow` so history is tracked
  correctly across renames.
- Never calls an LLM; all logic here is deterministic.

File layout
-----------
- types.py:      CommitInfo, FileHistory, ChangedFile
- classifier.py: is_bug_fix_commit (pure)
- fetch.py:      fetch_file_commits + its parser (the one function doing
                 per-file git log I/O)
- aggregate.py:  count_bug_fix_commits, compute_top_author_pct, get_last_touch (pure)
- repo_state.py: get_repo_state (repo-level git I/O)
- diff.py:       get_changed_files + its parser (the one function doing
                 git diff I/O)
- history.py:    get_file_history -- ties fetch.py + aggregate.py together
"""

from src.git_history.aggregate import compute_top_author_pct, count_bug_fix_commits, get_last_touch
from src.git_history.classifier import is_bug_fix_commit
from src.git_history.diff import get_changed_files
from src.git_history.fetch import _parse_log_output, fetch_file_commits
from src.git_history.history import get_file_history
from src.git_history.repo_state import get_repo_state
from src.git_history.types import ChangedFile, CommitInfo, FileHistory

__all__ = [
    "CommitInfo",
    "FileHistory",
    "ChangedFile",
    "is_bug_fix_commit",
    "fetch_file_commits",
    "_parse_log_output",
    "count_bug_fix_commits",
    "compute_top_author_pct",
    "get_last_touch",
    "get_repo_state",
    "get_changed_files",
    "get_file_history",
]
