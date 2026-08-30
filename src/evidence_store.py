"""
SQLite Evidence Store for Guardian.

Owns the three Phase 1 tables defined in AGENTS.md Section 4:

    files(path, last_touch_commit, last_touch_date,
          fan_in_count, bug_fix_count, top_author_pct, risk_score)

    edges(source_file, target_file, relationship_type, confidence)

    scan_meta(last_scan_commit_hash, branch)

Every claim shown to a user must trace back to this store or another named
source — the store is never populated by the Agent, only read by it.

Design rules enforced here:
- last_touch_date is stored as an ISO-8601 date string (YYYY-MM-DD), never
  as a precomputed "days since" integer — that would silently go stale even
  when nothing in the repo changed.
- edges holds only direct import relationships; transitive blast-radius
  closures are never stored — they are recomputed from the edge set at
  query time via NetworkX.
- scan_meta always has at most one row (UPSERT keyed on rowid=1).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
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


# ---------------------------------------------------------------------------
# EvidenceStore
# ---------------------------------------------------------------------------

class EvidenceStore:
    """
    Thin wrapper around the SQLite database file.

    Open with EvidenceStore(path) where path is a filesystem path to the
    .db file. Pass ":memory:" for an in-memory database (tests only).
    Schema is created automatically on first open; subsequent opens are
    idempotent (CREATE TABLE IF NOT EXISTS).
    """

    def __init__(self, db_path: str) -> None:
        """Open (or create) the SQLite database and apply the schema."""
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # Enforce foreign-key constraints and WAL mode for safer concurrent reads.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._apply_schema()

    def _apply_schema(self) -> None:
        """Create all tables if they don't already exist. Safe to call repeatedly."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection. Always call this when done."""
        self._conn.close()

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that commits on success and rolls back on exception."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # files table
    # ------------------------------------------------------------------

    def upsert_file(self, record: FileRecord) -> None:
        """
        Insert or replace a file record. Uses INSERT OR REPLACE so callers
        don't need to know whether a row already exists for this path.
        """
        with self._tx():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO files
                    (path, last_touch_commit, last_touch_date,
                     fan_in_count, bug_fix_count, top_author_pct, risk_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.path,
                    record.last_touch_commit,
                    record.last_touch_date,
                    record.fan_in_count,
                    record.bug_fix_count,
                    record.top_author_pct,
                    record.risk_score,
                ),
            )

    def get_file(self, path: str) -> FileRecord | None:
        """Return the FileRecord for path, or None if it doesn't exist."""
        row = self._conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        return _row_to_file(row) if row else None

    def get_all_files(self) -> list[FileRecord]:
        """Return every row in the files table, ordered by path."""
        rows = self._conn.execute(
            "SELECT * FROM files ORDER BY path"
        ).fetchall()
        return [_row_to_file(r) for r in rows]

    def delete_file(self, path: str) -> None:
        """Remove a file row. Used when a file is deleted from the repo."""
        with self._tx():
            self._conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def increment_fan_in(self, path: str, delta: int) -> None:
        """
        Adjust fan_in_count for path by delta (positive or negative).
        Used during incremental scan when a file gains or loses importers
        without needing a full re-parse.
        Raises ValueError if path has no row in files — silently no-oping on a
        missing record would violate the rule to never swallow failures silently.
        """
        with self._tx():
            cur = self._conn.execute(
                "UPDATE files SET fan_in_count = fan_in_count + ? WHERE path = ?",
                (delta, path),
            )
            if cur.rowcount == 0:
                raise ValueError(f"No file record for path: {path}")

    def update_risk_scores(self, scores: dict[str, float]) -> None:
        """
        Bulk-update risk_score for every path in the dict. Called after the
        Risk Scorer recomputes percentile ranks across the whole files table.
        Runs in a single transaction for performance.
        Raises ValueError for the first path that has no matching row in files —
        silently no-oping on a missing record would hide a caller bug.
        """
        with self._tx():
            for path, score in scores.items():
                cur = self._conn.execute(
                    "UPDATE files SET risk_score = ? WHERE path = ?",
                    (score, path),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"No file record for path: {path}")

    # ------------------------------------------------------------------
    # edges table
    # ------------------------------------------------------------------

    def upsert_edge(self, record: EdgeRecord) -> None:
        """Insert or replace a direct dependency edge."""
        with self._tx():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO edges
                    (source_file, target_file, relationship_type, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.source_file,
                    record.target_file,
                    record.relationship_type,
                    record.confidence,
                ),
            )

    def upsert_edges_bulk(self, records: list[EdgeRecord]) -> None:
        """Insert or replace a batch of edges in one transaction."""
        with self._tx():
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO edges
                    (source_file, target_file, relationship_type, confidence)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (r.source_file, r.target_file, r.relationship_type, r.confidence)
                    for r in records
                ],
            )

    def get_edges_from(self, source_file: str) -> list[EdgeRecord]:
        """Return all edges where source_file is the importer."""
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE source_file = ?", (source_file,)
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def get_edges_to(self, target_file: str) -> list[EdgeRecord]:
        """Return all edges where target_file is the importee (its direct importers)."""
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE target_file = ?", (target_file,)
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def get_all_edges(self) -> list[EdgeRecord]:
        """Return every edge in the store. Used to rebuild the NetworkX graph."""
        rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [_row_to_edge(r) for r in rows]

    def delete_edges_from(self, source_file: str) -> None:
        """Remove all edges originating from source_file (e.g. when it's deleted)."""
        with self._tx():
            self._conn.execute(
                "DELETE FROM edges WHERE source_file = ?", (source_file,)
            )

    def delete_edge(self, source_file: str, target_file: str) -> None:
        """Remove a single specific edge (e.g. when an import is dropped)."""
        with self._tx():
            self._conn.execute(
                "DELETE FROM edges WHERE source_file = ? AND target_file = ?",
                (source_file, target_file),
            )

    def rename_file_in_edges(self, old_path: str, new_path: str) -> None:
        """
        Update all edges that reference old_path (as source or target) to
        use new_path instead. Called when a high-confidence rename is detected.

        Collision handling: if renaming a row would produce a (source, target)
        pair that already exists (e.g. old.py→dep.py when new.py→dep.py is
        already present), the old row is deleted instead of updated — the
        existing row already correctly represents the dependency, so nothing
        is lost and no UNIQUE constraint error is raised.
        """
        with self._tx():
            # Rename edges where old_path is the source.
            # Find targets that would collide with an existing (new_path, target) row.
            colliding_targets = {
                row[0]
                for row in self._conn.execute(
                    """
                    SELECT e_old.target_file
                    FROM edges e_old
                    JOIN edges e_new
                      ON e_new.source_file = ? AND e_new.target_file = e_old.target_file
                    WHERE e_old.source_file = ?
                    """,
                    (new_path, old_path),
                ).fetchall()
            }
            if colliding_targets:
                self._conn.executemany(
                    "DELETE FROM edges WHERE source_file = ? AND target_file = ?",
                    [(old_path, t) for t in colliding_targets],
                )
            self._conn.execute(
                "UPDATE edges SET source_file = ? WHERE source_file = ?",
                (new_path, old_path),
            )

            # Rename edges where old_path is the target.
            # Find sources that would collide with an existing (source, new_path) row.
            colliding_sources = {
                row[0]
                for row in self._conn.execute(
                    """
                    SELECT e_old.source_file
                    FROM edges e_old
                    JOIN edges e_new
                      ON e_new.target_file = ? AND e_new.source_file = e_old.source_file
                    WHERE e_old.target_file = ?
                    """,
                    (new_path, old_path),
                ).fetchall()
            }
            if colliding_sources:
                self._conn.executemany(
                    "DELETE FROM edges WHERE source_file = ? AND target_file = ?",
                    [(s, old_path) for s in colliding_sources],
                )
            self._conn.execute(
                "UPDATE edges SET target_file = ? WHERE target_file = ?",
                (new_path, old_path),
            )

    # ------------------------------------------------------------------
    # scan_meta table
    # ------------------------------------------------------------------

    def get_scan_meta(self) -> ScanMeta | None:
        """
        Return the scan_meta row, or None if the store has never been scanned.
        None signals that a full scan is required rather than an incremental one.
        """
        row = self._conn.execute("SELECT * FROM scan_meta WHERE id = 1").fetchone()
        if row is None:
            return None
        return ScanMeta(
            last_scan_commit_hash=row["last_scan_commit_hash"],
            branch=row["branch"],
        )

    def set_scan_meta(self, commit_hash: str, branch: str) -> None:
        """
        Upsert the single scan_meta row. Always keyed to id=1 so there can
        only ever be one row — the CHECK constraint on the table enforces this.
        """
        with self._tx():
            self._conn.execute(
                """
                INSERT INTO scan_meta (id, last_scan_commit_hash, branch)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_scan_commit_hash = excluded.last_scan_commit_hash,
                    branch                = excluded.branch
                """,
                (commit_hash, branch),
            )


# ---------------------------------------------------------------------------
# Row-to-dataclass helpers (keep the rest of the code free of dict indexing)
# ---------------------------------------------------------------------------

def _row_to_file(row: sqlite3.Row) -> FileRecord:
    """Convert a sqlite3.Row from the files table into a FileRecord."""
    return FileRecord(
        path=row["path"],
        last_touch_commit=row["last_touch_commit"],
        last_touch_date=row["last_touch_date"],
        fan_in_count=row["fan_in_count"],
        bug_fix_count=row["bug_fix_count"],
        top_author_pct=row["top_author_pct"],
        risk_score=row["risk_score"],
    )


def _row_to_edge(row: sqlite3.Row) -> EdgeRecord:
    """Convert a sqlite3.Row from the edges table into an EdgeRecord."""
    return EdgeRecord(
        source_file=row["source_file"],
        target_file=row["target_file"],
        relationship_type=row["relationship_type"],
        confidence=row["confidence"],
    )
