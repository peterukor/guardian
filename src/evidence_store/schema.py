"""
Schema and data types for Guardian's Evidence Store.

Owns the table definitions and their corresponding dataclasses only -- no
store logic (connections, transactions, queries) lives here. See store.py
for the EvidenceStore class that operates on this schema.

Tables (AGENTS.md Section 4/5):

    files(path, last_touch_commit, last_touch_date,
          fan_in_count, bug_fix_count, top_author_pct, risk_score)

    edges(source_file, target_file, relationship_type, confidence)

    scan_meta(last_scan_commit_hash, branch)

    predictions(id, invocation_id, repo_path, file_path, commit_hash,
                ref_range, risk_score, risk_level, agent_findings,
                created_at, outcome_type, outcome_description,
                outcome_recorded_at)

Design rules enforced by this schema:
- last_touch_date is stored as an ISO-8601 date string (YYYY-MM-DD), never
  as a precomputed "days since" integer -- that would silently go stale even
  when nothing in the repo changed.
- edges holds only direct import relationships; transitive blast-radius
  closures are never stored -- they are recomputed from the edge set at
  query time via NetworkX.
- scan_meta always has at most one row (CHECK id = 1).
- predictions is append-only evidence for the future Feedback Loop; outcome
  fields are NULL until manually recorded, never empty-string placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path              TEXT PRIMARY KEY,
    last_touch_commit TEXT,
    last_touch_date   TEXT,          -- ISO-8601 date, e.g. "2024-03-15"
    fan_in_count      INTEGER NOT NULL DEFAULT 0,
    bug_fix_count     INTEGER NOT NULL DEFAULT 0,
    top_author_pct    REAL    NOT NULL DEFAULT 0.0,
    risk_score        REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS edges (
    source_file       TEXT NOT NULL,
    target_file       TEXT NOT NULL,
    relationship_type TEXT NOT NULL DEFAULT 'imports',
    confidence        REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (source_file, target_file)
);

CREATE TABLE IF NOT EXISTS scan_meta (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    last_scan_commit_hash TEXT,
    branch                TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id        TEXT NOT NULL,
    repo_path            TEXT NOT NULL,
    file_path            TEXT NOT NULL,
    commit_hash          TEXT,
    ref_range            TEXT,
    risk_score           REAL NOT NULL,
    risk_level           TEXT NOT NULL,
    agent_findings       TEXT,          -- JSON-encoded list[str]; NULL if none
    created_at           TEXT NOT NULL, -- ISO-8601 timestamp
    outcome_type         TEXT,          -- NULL until manually recorded
    outcome_description  TEXT,          -- NULL until manually recorded
    outcome_recorded_at  TEXT           -- ISO-8601 timestamp; NULL until recorded
);
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FileRecord:
    """One row from the files table. All fields map 1-to-1 to columns."""
    path: str
    last_touch_commit: str | None
    last_touch_date: str | None       # ISO-8601 date string
    fan_in_count: int
    bug_fix_count: int
    top_author_pct: float
    risk_score: float


@dataclass
class EdgeRecord:
    """One row from the edges table."""
    source_file: str
    target_file: str
    relationship_type: str
    confidence: float


@dataclass
class ScanMeta:
    """The single row in scan_meta. None means the store has never been scanned."""
    last_scan_commit_hash: str | None
    branch: str | None


@dataclass
class PredictionRecord:
    """
    One row from the predictions table -- a permanent, timestamped snapshot
    of a risk prediction made for one file during one `analyze` invocation.

    invocation_id groups every per-file prediction from the same `analyze`
    call (caller generates one UUID per call, reuses it per file).

    agent_findings is stored as JSON text in SQLite but exposed here as the
    deserialized Python value (list[str] or None) -- callers never see raw
    JSON strings.

    outcome_type/outcome_description/outcome_recorded_at are None until an
    outcome is manually recorded via update_outcome(); this is persistence
    only, not automatic detection or accuracy scoring.
    """
    id: int | None
    invocation_id: str
    repo_path: str
    file_path: str
    commit_hash: str | None
    ref_range: str | None
    risk_score: float
    risk_level: str
    agent_findings: list[str] | None
    created_at: str
    outcome_type: str | None
    outcome_description: str | None
    outcome_recorded_at: str | None
