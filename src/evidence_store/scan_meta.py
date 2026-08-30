"""
scan_meta-table operations for EvidenceStore, as a mixin.

Assumes it is mixed into a class providing self._conn and self._tx (see
base.py). Contains every method that reads or writes the single-row
`scan_meta` table.
"""

from __future__ import annotations

import sqlite3

from src.evidence_store.base import _EvidenceStoreBase
from src.evidence_store.schema import ScanMeta


class _ScanMetaOpsMixin(_EvidenceStoreBase):
    """scan_meta single-row read/upsert. Combined into EvidenceStore -- see store.py.
    Inherits from _EvidenceStoreBase so self._conn/self._tx are real,
    known attributes here -- no repeated type declarations needed."""

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

    def set_scan_meta(self, commit_hash: str | None, branch: str | None) -> None:
        """
        Upsert the single scan_meta row. Always keyed to id=1 so there can
        only ever be one row — the CHECK constraint on the table enforces this.

        commit_hash and branch may be None (e.g. a repo with no commits yet).
        None is passed through to SQLite as NULL so that a subsequent call to
        get_scan_meta() can distinguish "scanned but empty repo" from a real
        commit hash — empty strings must never be used as a sentinel here.
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
