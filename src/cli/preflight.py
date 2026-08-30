"""
Pre-flight validation checks for Guardian's CLI, run before any command's
real logic -- in this order: path exists, is a git repo, has commits.
"""

from __future__ import annotations

import os
import subprocess
import sys


def check_path_exists(path: str) -> None:
    """Abort with a clear message if path does not exist on disk."""
    if not os.path.exists(path):
        die(f"Error: '{path}' does not exist.")


def check_is_git_repo(path: str) -> None:
    """Abort with a clear message if path is not inside a git repository."""
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(
            f"Error: '{path}' is not a git repository. "
            "Guardian needs git history to compute risk scores."
        )


def check_has_commits(path: str) -> None:
    """Abort with a clear message if the repository has no commits yet."""
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die("No commit history found — risk scores require at least one commit.")


def preflight(path: str) -> None:
    """Run all three pre-flight checks in the required order."""
    check_path_exists(path)
    check_is_git_repo(path)
    check_has_commits(path)


def die(message: str) -> None:
    """Print message to stderr and exit with code 1."""
    print(message, file=sys.stderr)
    sys.exit(1)
