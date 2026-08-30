"""
Passport-building logic for Guardian's CLI.

Reads Evidence Store data to build FilePassport/ChangePassport objects, and
records permanent PredictionRecord rows once a passport is built. No
business logic beyond assembly -- all computation is delegated to
evidence_store, python_adapter, and risk_scorer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.adapters.python_adapter import get_blast_radius
from src.cli.passport import ChangePassport, FilePassport
from src.evidence_store import EvidenceStore, PredictionRecord
from src.risk_scorer import classify_risk_level


def build_file_passport(path: str, status: str, store: EvidenceStore) -> FilePassport:
    """
    Build a FilePassport for one changed file using stored evidence.

    Status is checked first, before querying the store.  Deleted ("D") and
    renamed ("R") files must report evidence_available=False even when a
    stale record still exists in the store from a prior scan — showing old
    evidence as if it were current would be a data-honesty violation.

    For all other statuses, evidence_available=False is also returned when
    the store has no record (file not yet scanned).
    """
    unavailable = FilePassport(
        path=path,
        status=status,
        risk_score=None,
        risk_level=None,
        fan_in=None,
        bug_fix_count=None,
        top_author_pct=None,
        last_touch_date=None,
        blast_radius_direct=None,
        blast_radius_indirect=None,
        blast_radius_total=None,
        evidence_available=False,
    )

    # Deleted and renamed files must never show stale store data as current.
    if status in ("D", "R"):
        return unavailable

    rec = store.get_file(path)
    if rec is None:
        return unavailable

    graph = store.build_graph()
    br = get_blast_radius(graph, path)

    return FilePassport(
        path=path,
        status=status,
        risk_score=rec.risk_score,
        risk_level=classify_risk_level(rec.risk_score),
        fan_in=rec.fan_in_count,
        bug_fix_count=rec.bug_fix_count,
        top_author_pct=rec.top_author_pct,
        last_touch_date=rec.last_touch_date,
        blast_radius_direct=len(br["direct_dependents"]),
        blast_radius_indirect=len(br["indirect_dependents"]),
        blast_radius_total=br["total"],
        evidence_available=True,
    )


def build_passport(
    repo_path: str,
    changed: list[tuple[str, str]],   # list of (path, status)
    ref_range: str | None,
    db_path: str,
) -> ChangePassport:
    """
    Build a full ChangePassport by reading Evidence Store data for each file.

    Opens and closes the store within this function so the caller never
    needs to manage the connection lifecycle.
    """
    passport = ChangePassport(repo_path=repo_path, ref_range=ref_range)
    store = EvidenceStore(db_path)
    try:
        for path, status in changed:
            passport.files.append(build_file_passport(path, status, store))
    finally:
        store.close()
    return passport


def record_predictions(passport: ChangePassport, db_path: str) -> tuple[int, str]:
    """
    Persist one PredictionRecord per file in the passport that has real
    evidence. Skips files with evidence_available=False -- there is no real
    risk_score/risk_level to record for a deleted/unscanned file, and
    recording one anyway would be fabricating data.

    One invocation_id and one created_at timestamp are generated here, once,
    and shared across every file's row from this call -- not regenerated
    per file. Predictions are recorded whether or not the Agent was
    available; agent_findings is None (never an empty list) when it wasn't.

    Returns (count_recorded, invocation_id) for the caller to report.
    """
    invocation_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    store = EvidenceStore(db_path)
    try:
        commit_hash = None
        meta = store.get_scan_meta()
        if meta is not None:
            commit_hash = meta.last_scan_commit_hash

        agent_findings = passport.agent_findings if passport.agent_available else None

        count = 0
        for fp in passport.files:
            if not fp.evidence_available:
                continue
            # evidence_available=True guarantees these are real values, not
            # None -- asserted here to narrow the type and as a safety net
            # if that invariant is ever broken by a future edit elsewhere.
            assert fp.risk_score is not None
            assert fp.risk_level is not None
            store.insert_prediction(PredictionRecord(
                id=None,
                invocation_id=invocation_id,
                repo_path=passport.repo_path,
                file_path=fp.path,
                commit_hash=commit_hash,
                ref_range=passport.ref_range,
                risk_score=fp.risk_score,
                risk_level=fp.risk_level,
                agent_findings=agent_findings,
                created_at=created_at,
                outcome_type=None,
                outcome_description=None,
                outcome_recorded_at=None,
            ))
            count += 1
    finally:
        store.close()

    return count, invocation_id
