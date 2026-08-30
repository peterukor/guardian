"""
Repo-level state (current commit hash and branch) for Guardian's Git
History module.
"""

from __future__ import annotations

import subprocess


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
