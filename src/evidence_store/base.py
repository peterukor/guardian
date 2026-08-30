"""
Base connection/lifecycle management for EvidenceStore.

Defines __init__, close(), schema application, and the shared _tx()
transaction context manager. Every table-specific mixin (files.py, edges.py,
scan_meta.py, predictions.py, graph.py) assumes it is mixed into a class
built on this base, using self._conn and self._tx.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from src.evidence_store.schema import SCHEMA


class _EvidenceStoreBase:
    """Connection lifecycle and transaction handling, shared by every mixin."""

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
        self._conn.executescript(SCHEMA)
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
