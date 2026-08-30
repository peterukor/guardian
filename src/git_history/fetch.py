"""
Per-file commit history fetching for Guardian's Git History module.

Owns the one function that calls git for per-file history. Uses `git log
--follow` so renames are tracked correctly -- without it, a renamed file
would appear to have a fresh, empty history under its new name.
"""

from __future__ import annotations

import subprocess

from src.git_history.types import CommitInfo

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
