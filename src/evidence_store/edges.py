from __future__ import annotations

import sqlite3

from src.evidence_store.base import _EvidenceStoreBase
from src.evidence_store.schema import EdgeRecord


class _EdgeOpsMixin(_EvidenceStoreBase):
    """edges-table CRUD. Combined into EvidenceStore -- see store.py.
    Inherits from _EvidenceStoreBase so self._conn/self._tx are real,
    known attributes here -- no repeated type declarations needed."""

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


def _row_to_edge(row: sqlite3.Row) -> EdgeRecord:
    """Convert a sqlite3.Row from the edges table into an EdgeRecord."""
    return EdgeRecord(
        source_file=row["source_file"],
        target_file=row["target_file"],
        relationship_type=row["relationship_type"],
        confidence=row["confidence"],
    )
