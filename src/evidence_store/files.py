"""
files-table operations for EvidenceStore, as a mixin.

Assumes it is mixed into a class providing self._conn and self._tx (see
base.py). Contains every method that reads or writes the `files` table.
"""

from __future__ import annotations

import sqlite3

from src.evidence_store.base import _EvidenceStoreBase
from src.evidence_store.schema import FileRecord


class _FileOpsMixin(_EvidenceStoreBase):
    """files-table CRUD. Combined into EvidenceStore -- see store.py.
    Inherits from _EvidenceStoreBase so self._conn/self._tx are real,
    known attributes here -- no repeated type declarations needed."""

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
