"""
Structured passport data types for Guardian's CLI, plus the default
Evidence Store path helper.

FilePassport/ChangePassport are rendered to human-readable text or JSON from
the same object (see render.py), so both output formats always agree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------

_DB_FILENAME = os.path.join(".guardian", "guardian.db")


def default_db(repo_path: str) -> str:
    """Return the default Evidence Store path for repo_path."""
    return os.path.join(repo_path, _DB_FILENAME)


# ---------------------------------------------------------------------------
# Structured passport
# ---------------------------------------------------------------------------

@dataclass
class FilePassport:
    """
    Evidence summary for one changed file.

    All values come directly from the Evidence Store or from deterministic
    engine calls — never invented.  Fields are None / empty when evidence
    is genuinely unavailable (deleted file, not yet scanned, etc.) so the
    renderer can show an explicit 'unavailable' message rather than zeros.
    """
    path: str
    status: str                        # A / M / D / R
    risk_score: float | None
    risk_level: str | None
    fan_in: int | None
    bug_fix_count: int | None
    top_author_pct: float | None
    last_touch_date: str | None
    blast_radius_direct: int | None
    blast_radius_indirect: int | None
    blast_radius_total: int | None
    evidence_available: bool


@dataclass
class ChangePassport:
    """
    Top-level structured passport for a set of changed files.

    Rendered to human-readable text or JSON from this single object so both
    output formats share exactly the same data.
    """
    repo_path: str
    ref_range: str | None              # e.g. "HEAD~1..HEAD"; None for --files
    files: list[FilePassport] = field(default_factory=list)
    # Agent runs once per whole call, not per file -- these live at batch
    # level, not on FilePassport. agent_available=False means the section
    # must render as unavailable, never fabricated.
    agent_available: bool = False
    agent_findings: list[str] = field(default_factory=list)
    agent_checks: list[str] = field(default_factory=list)
    agent_error: str | None = None
