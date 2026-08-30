"""
Changed-file diff detection for Guardian's Git History module.

This is the only function that runs `git diff --name-status` -- the CLI
and scanner must call get_changed_files() rather than spawning their own
subprocess, keeping this specific git operation in one place.
"""

from __future__ import annotations

import subprocess

from src.git_history.types import ChangedFile


def get_changed_files(repo_root: str, ref1: str, ref2: str) -> list[ChangedFile]:
    """
    Return the files changed between ref1 and ref2 in the repository.

    Uses `git diff --name-status -M ref1 ref2` so that renames are detected
    and reported rather than appearing as an unrelated delete + add.

    This is the only function that runs this git diff operation — the CLI and
    scanner must call this function rather than spawning their own subprocess.

    Status codes produced by git and their mapping:
        A        → ChangedFile(path, "A")
        M        → ChangedFile(path, "M")
        D        → ChangedFile(path, "D")
        R100, R75, … → ChangedFile(new_path, "R", old_path=old_path)
          Any Rxx rename percentage is normalized to the single letter "R".

    Raises RuntimeError if git exits non-zero (bad refs, not a repo, etc.).
    """
    result = subprocess.run(
        ["git", "-C", repo_root, "diff", "--name-status", "-M", ref1, ref2],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff failed for '{ref1}..{ref2}' in '{repo_root}': "
            f"{result.stderr.strip()}"
        )
    return _parse_diff_name_status(result.stdout)


def _parse_diff_name_status(raw: str) -> list[ChangedFile]:
    """
    Parse the output of `git diff --name-status -M`.

    Each line is one of:
        A   path
        M   path
        D   path
        Rxx old_path   new_path   (xx is a similarity percentage, e.g. R100)

    Returns a list of ChangedFile objects in the order git emitted them.
    """
    changed: list[ChangedFile] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        status_raw = parts[0]
        if status_raw.startswith("R"):
            # Rename: parts[1] = old path, parts[2] = new path
            if len(parts) < 3:
                continue  # malformed line — skip
            changed.append(ChangedFile(
                path=parts[2],
                status="R",
                old_path=parts[1],
            ))
        elif len(parts) >= 2:
            # A, M, or D: parts[1] = path
            status = status_raw[0]  # take only the first character
            changed.append(ChangedFile(path=parts[1], status=status))
    return changed
