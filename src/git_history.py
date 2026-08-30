"""
Git History Miner for Guardian.

Extracts per-file history signals needed by the Risk Scorer:
  - last_touch_commit: the most recent commit hash touching this file
  - last_touch_date:   ISO-8601 date string (YYYY-MM-DD) of that commit
  - bug_fix_count:     number of commits whose messages look like bug fixes
  - top_author_pct:    fraction of commits authored by the single most active
                       author (0.0 if the file has no history)

Design rules enforced here:
- Only one function (fetch_file_commits) calls subprocess/git — every other
  computation operates on the already-parsed list it returns. This keeps git
  I/O isolated and every pure function directly testable without a real repo.
- Always uses `git log --follow` so history is tracked correctly across renames.
  Without --follow, a renamed file would appear to have a fresh, empty history.
- Handles files with no history cleanly (brand-new or uncommitted file).
- Never calls an LLM; all logic is deterministic.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CommitInfo:
    """
    Parsed data for one commit from git log.

    hash   — full 40-char (or abbreviated) commit SHA
    author — author name as stored in git (Name <email> if using --format=%an)
    date   — ISO-8601 date string, e.g. "2024-03-15"
    message — the first line of the commit message (subject line only)
    """
    hash: str
    author: str
    date: str
    message: str


@dataclass
class FileHistory:
    """
    Aggregated history signals for one file. All fields map directly to
    columns in the Evidence Store's `files` table.
    """
    path: str
    last_touch_commit: str | None   # None if no history (new/uncommitted file)
    last_touch_date: str | None     # ISO-8601 date string, or None
    bug_fix_count: int
    top_author_pct: float           # 0.0–1.0 fraction; 0.0 if no history


# ---------------------------------------------------------------------------
# Bug-fix classifier (pure function — no git I/O)
# ---------------------------------------------------------------------------

# Whole-word patterns that indicate a bug-fix commit.  Using \b word boundaries
# prevents false positives from partial matches (e.g. "prefix" contains "fix"
# but should not count).
_BUG_FIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfix(es|ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bbug\b",             re.IGNORECASE),
    re.compile(r"\bhotfix\b",          re.IGNORECASE),
    re.compile(r"\brevert\b",          re.IGNORECASE),
    # Issue tracker references: bare #123, or an explicit known-prefix (GH-, JIRA-).
    # The previous catch-all [A-Z]+-\d+ was too broad — it also matched things like
    # "README-2024" or "CHAPTER-5", which are not issue references.
    re.compile(r"(#|GH-|JIRA-)\d+", re.IGNORECASE),
]


def is_bug_fix_commit(message: str) -> bool:
    """
    Return True if the commit message looks like a bug-fix commit.

    Checks for whole-word occurrences of common bug-fix keywords (fix, bug,
    hotfix, revert) and issue-tracker references (#123, GH-42, JIRA-99).
    Word boundaries prevent partial matches: "prefix" won't match "fix",
    and "debug" won't match "bug".

    This is a pure function with no I/O — it can be tested directly with
    arbitrary message strings without needing a git repository.
    """
    return any(pattern.search(message) for pattern in _BUG_FIX_PATTERNS)


# ---------------------------------------------------------------------------
# Git I/O — exactly one function
# ---------------------------------------------------------------------------

# Record separator that won't appear in any real commit field.
_RECORD_SEP = "\x1e"
# Field separator within one record.
_FIELD_SEP = "\x1f"

# git log format: hash<FS>author<FS>date<FS>subject<RS>
_LOG_FORMAT = f"--format={_FIELD_SEP}%H{_FIELD_SEP}%an{_FIELD_SEP}%ad{_FIELD_SEP}%s{_RECORD_SEP}"


def fetch_file_commits(repo_root: str, file_path: str) -> list[CommitInfo]:
    """
    Return a list of CommitInfo records for every commit that touched file_path,
    ordered newest-first (git's default).

    Uses `git log --follow` so renames are tracked and the file's full history
    is returned even if it was renamed at some point.

    repo_root — absolute path to the root of the git repository (where .git lives)
    file_path — path to the file, relative to repo_root

    Returns an empty list if the file has no history (brand-new or untracked).
    Raises RuntimeError if git exits with a non-zero status for any reason other
    than the file simply having no commits (which git handles silently).
    """
    cmd = [
        "git", "-C", repo_root,
        "log", "--follow", "--date=short",
        _LOG_FORMAT,
        "--", file_path,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A repo with no commits yet produces a non-zero exit and this message.
        # That is a legitimate "no history" state — not a failure worth raising.
        if "does not have any commits yet" in result.stderr:
            return []
        raise RuntimeError(
            f"git log failed for '{file_path}' in '{repo_root}': {result.stderr.strip()}"
        )

    return _parse_log_output(result.stdout)


def _parse_log_output(raw: str) -> list[CommitInfo]:
    """
    Parse the raw stdout from `git log` using our custom record/field separators.

    Each record is introduced by a leading _FIELD_SEP (so the first split token
    is always an empty string before the first real record) and terminated by
    _RECORD_SEP. Fields within each record are separated by _FIELD_SEP.
    """
    commits: list[CommitInfo] = []
    # Split on the record separator; each non-empty chunk is one commit.
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Strip the leading field-separator introduced by the format string.
        if chunk.startswith(_FIELD_SEP):
            chunk = chunk[len(_FIELD_SEP):]
        parts = chunk.split(_FIELD_SEP)
        if len(parts) < 4:
            continue  # malformed record — skip rather than crash
        hash_, author, date, *message_parts = parts
        message = _FIELD_SEP.join(message_parts)  # re-join if subject had FS chars
        if hash_ and date:  # both required; skip empty/header lines
            commits.append(CommitInfo(
                hash=hash_.strip(),
                author=author.strip(),
                date=date.strip(),
                message=message.strip(),
            ))
    return commits


# ---------------------------------------------------------------------------
# Pure aggregation functions (no git I/O)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public high-level API
# ---------------------------------------------------------------------------

def get_repo_state(repo_root: str) -> tuple[str | None, str | None]:
    """
    Return (current_commit_hash, current_branch_name) for the repo at repo_root.

    Uses two git commands:
      - `git rev-parse HEAD`              -> the full commit SHA of HEAD
      - `git rev-parse --abbrev-ref HEAD` -> the branch name (e.g. "main")

    Returns (None, None) if the repo has no commits yet.  A repo without any
    commits has no HEAD to parse, so both values are meaningless — returning
    None, None signals to the scanner that a full scan is required and that
    scan_meta cannot be seeded yet.

    Raises RuntimeError for any other git failure (e.g. repo_root is not a
    git repository) so the caller sees a clear error rather than garbage.
    """
    def _run(args: list[str]) -> str | None:
        """Run a git command; return stdout stripped, or None on expected failure."""
        result = subprocess.run(
            ["git", "-C", repo_root] + args,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if "does not have any commits yet" in result.stderr or \
               "unknown revision" in result.stderr:
                return None
            raise RuntimeError(
                f"git command {args} failed in '{repo_root}': {result.stderr.strip()}"
            )
        return result.stdout.strip()

    commit_hash = _run(["rev-parse", "HEAD"])
    if commit_hash is None:
        return None, None
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    return commit_hash, branch


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
