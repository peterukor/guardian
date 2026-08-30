"""
Data types for Guardian's Git History module.
"""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class ChangedFile:
    """
    One file entry returned by get_changed_files().

    path     — new (or current) path of the file, relative to repo root,
               forward-slash separated.
    status   — single-letter change type: "A" (added), "M" (modified),
               "D" (deleted), "R" (renamed — any similarity percentage).
               Rename statuses like "R100" or "R75" are normalized to "R".
    old_path — previous path before a rename; None for non-rename statuses.
    """
    path: str
    status: str
    old_path: str | None = None
