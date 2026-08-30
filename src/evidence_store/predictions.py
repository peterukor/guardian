"""
predictions-table operations for EvidenceStore, as a mixin.

Assumes it is mixed into a class providing self._conn and self._tx (see
base.py). Contains every method that reads or writes the `predictions` table
-- the Prediction Log used by the future Feedback Loop.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.evidence_store.base import _EvidenceStoreBase
from src.evidence_store.schema import PredictionRecord


class _PredictionOpsMixin(_EvidenceStoreBase):
    """predictions-table CRUD. Combined into EvidenceStore -- see store.py.
    Inherits from _EvidenceStoreBase so self._conn/self._tx are real,
    known attributes here -- no repeated type declarations needed."""

    def insert_prediction(self, record: PredictionRecord) -> int:
        """
        Insert a new prediction row and return its generated id.

        agent_findings is serialized to JSON text for storage; None stays
        None (never an empty string or empty array substitute). record.id
        is ignored on insert -- SQLite assigns it.
        """
        findings_json = (
            json.dumps(record.agent_findings)
            if record.agent_findings is not None
            else None
        )
        with self._tx():
            cur = self._conn.execute(
                """
                INSERT INTO predictions
                    (invocation_id, repo_path, file_path, commit_hash, ref_range,
                     risk_score, risk_level, agent_findings, created_at,
                     outcome_type, outcome_description, outcome_recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.invocation_id,
                    record.repo_path,
                    record.file_path,
                    record.commit_hash,
                    record.ref_range,
                    record.risk_score,
                    record.risk_level,
                    findings_json,
                    record.created_at,
                    record.outcome_type,
                    record.outcome_description,
                    record.outcome_recorded_at,
                ),
            )
            # lastrowid is typed int | None by sqlite3's stubs (it's None for
            # cursors not tied to an INSERT) -- for a successful INSERT it is
            # always a real int. The assert both narrows the type for the
            # checker and serves as a genuine safety net.
            assert cur.lastrowid is not None, "INSERT into predictions produced no rowid"
            return cur.lastrowid

    def get_prediction(self, prediction_id: int) -> PredictionRecord | None:
        """Return the PredictionRecord for prediction_id, or None if it doesn't exist."""
        row = self._conn.execute(
            "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
        ).fetchone()
        return _row_to_prediction(row) if row else None

    def get_predictions_for_invocation(self, invocation_id: str) -> list[PredictionRecord]:
        """Return every prediction row sharing the given invocation_id, ordered by id."""
        rows = self._conn.execute(
            "SELECT * FROM predictions WHERE invocation_id = ? ORDER BY id",
            (invocation_id,),
        ).fetchall()
        return [_row_to_prediction(r) for r in rows]

    def update_outcome(
        self,
        prediction_id: int,
        outcome_type: str,
        outcome_description: str | None,
    ) -> None:
        """
        Manually record the eventual outcome for an existing prediction.
        This is persistence only -- not automatic detection or accuracy
        scoring, which are not implemented here.

        outcome_recorded_at is set to the current UTC time automatically.
        Raises ValueError if prediction_id has no matching row, consistent
        with how update_risk_scores/increment_fan_in handle a missing row
        elsewhere in this project.
        """
        recorded_at = datetime.now(timezone.utc).isoformat()
        with self._tx():
            cur = self._conn.execute(
                """
                UPDATE predictions
                SET outcome_type = ?, outcome_description = ?, outcome_recorded_at = ?
                WHERE id = ?
                """,
                (outcome_type, outcome_description, recorded_at, prediction_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"No prediction record for id: {prediction_id}")


def _row_to_prediction(row: sqlite3.Row) -> PredictionRecord:
    """
    Convert a sqlite3.Row from the predictions table into a PredictionRecord.

    agent_findings is deserialized from its stored JSON text back into a
    Python list[str]; a NULL column stays None, never an empty list.
    """
    findings = (
        json.loads(row["agent_findings"])
        if row["agent_findings"] is not None
        else None
    )
    return PredictionRecord(
        id=row["id"],
        invocation_id=row["invocation_id"],
        repo_path=row["repo_path"],
        file_path=row["file_path"],
        commit_hash=row["commit_hash"],
        ref_range=row["ref_range"],
        risk_score=row["risk_score"],
        risk_level=row["risk_level"],
        agent_findings=findings,
        created_at=row["created_at"],
        outcome_type=row["outcome_type"],
        outcome_description=row["outcome_description"],
        outcome_recorded_at=row["outcome_recorded_at"],
    )
