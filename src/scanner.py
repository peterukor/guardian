"""
Scan orchestrator for Guardian.

Wires together the four engines (Dependency Analyzer adapters, Git History
Miner, Risk Scorer, Evidence Store) to produce a full first scan of a
repository.  The scanner itself contains no parsing, git, math, or SQL logic
— it only calls the other modules and threads their outputs together.

Entry point
-----------
    from src.scanner import run_scan

    run_scan(repo_path, db_path)

What a full scan does (in order)
---------------------------------
1. Open the Evidence Store at db_path.
2. Call get_repo_state(repo_path) to learn the current HEAD commit and branch.
3. Use get_adapters(repo_path) to select which language adapters apply.
4. For each adapter: collect edges via adapter.analyze() and the full tracked
   file list via adapter.discovered_files().
5. Build the union of all tracked files and the union of all edges across
   every applicable adapter.
6. For every tracked file, fetch its Git-history signals with get_file_history().
7. Compute fan_in_count per file from the collected edge set (count incoming
   edges whose target_file matches the tracked file).
8. Pass the complete batch of per-file signals to score_files() in one call
   so percentile ranks span the full tracked set.
9. Persist all FileRecord rows and all EdgeRecord rows in the Evidence Store.
10. Write (or update) the scan_meta row with the current commit hash and branch.

If no adapters apply, steps 4–9 are skipped and the scan_meta row is still
written.  This is a valid, non-error outcome (e.g. a docs-only repository).

This module covers full scans only — incremental scanning, rename/delete
detection, CLI integration, Agent integration, and the Prediction Log are
not implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.dispatcher import get_adapters
from src.evidence_store import EdgeRecord, EvidenceStore, FileRecord
from src.git_history import get_file_history, get_repo_state
from src.risk_scorer import FileSignals, score_files


# ---------------------------------------------------------------------------
# Public result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """
    Summary of what a scan wrote to the Evidence Store.

    Returned by run_scan() so callers (tests, CLI, future Agent tools) can
    inspect outcomes without re-querying the database.

    files_scanned  — number of FileRecord rows upserted (0 for no-adapter repos)
    edges_stored   — number of EdgeRecord rows upserted
    commit_hash    — the HEAD SHA the scan was stamped with (None if no commits)
    branch         — the branch name (None if no commits)
    """
    files_scanned: int
    edges_stored: int
    commit_hash: str | None
    branch: str | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_scan(repo_path: str, db_path: str) -> ScanResult:
    """
    Run a full scan of repo_path and persist results to db_path.

    repo_path — path to the root of the git repository to scan
    db_path   — filesystem path for the SQLite Evidence Store; use ":memory:"
                for an ephemeral in-memory database (tests only)

    Returns a ScanResult summarising what was written.  Raises RuntimeError
    if git commands fail for any reason other than the no-commits-yet case
    (which is handled cleanly — scan_meta is still written).
    """
    store = EvidenceStore(db_path)
    try:
        return _run_scan(repo_path, store)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _run_scan(repo_path: str, store: EvidenceStore) -> ScanResult:
    """
    Core scan logic, separated from run_scan() so tests can inject an already-
    open EvidenceStore (e.g. an in-memory one) without going through the
    open/close lifecycle.
    """
    # ------------------------------------------------------------------
    # Step 1: repo state — commit hash and branch name
    # ------------------------------------------------------------------
    commit_hash, branch = get_repo_state(repo_path)

    # ------------------------------------------------------------------
    # Step 2: select adapters and collect files + edges
    # ------------------------------------------------------------------
    adapters = get_adapters(repo_path)

    all_files: set[str] = set()
    all_edge_tuples: list[tuple[str, str, str, float]] = []

    for adapter in adapters:
        for path in adapter.discovered_files(repo_path):
            all_files.add(path)
        for edge in adapter.analyze(repo_path):
            all_edge_tuples.append(edge)

    # Deduplicate edges — two adapters might (in theory) both resolve the
    # same import; keep unique (source, target) pairs, preferring the first
    # occurrence (highest confidence from the first adapter).
    seen_edges: set[tuple[str, str]] = set()
    deduped_edges: list[tuple[str, str, str, float]] = []
    for src, dst, rel, conf in all_edge_tuples:
        if (src, dst) not in seen_edges:
            seen_edges.add((src, dst))
            deduped_edges.append((src, dst, rel, conf))

    # ------------------------------------------------------------------
    # Step 3: fan_in_count — count incoming edges per target file
    # ------------------------------------------------------------------
    fan_in: dict[str, int] = {path: 0 for path in all_files}
    for _src, dst, _rel, _conf in deduped_edges:
        if dst in fan_in:
            fan_in[dst] += 1
        # Edges pointing to files outside the tracked set (e.g. a dependency
        # that exists but wasn't discovered by any adapter) are silently skipped
        # — we only track files that at least one adapter recognised.

    # ------------------------------------------------------------------
    # Step 4: Git-history signals for every tracked file
    # ------------------------------------------------------------------
    histories = {
        path: get_file_history(repo_path, path)
        for path in all_files
    }

    # ------------------------------------------------------------------
    # Step 5: risk scoring — one batch call for the full tracked set
    # ------------------------------------------------------------------
    signals = [
        FileSignals(
            path=path,
            fan_in=fan_in[path],
            bug_fix_count=histories[path].bug_fix_count,
            ownership_concentration=histories[path].top_author_pct,
        )
        for path in sorted(all_files)   # sorted for deterministic ordering
    ]

    risk_results = score_files(signals)
    risk_by_path = {r.path: r.risk_score for r in risk_results}

    # ------------------------------------------------------------------
    # Step 6: persist FileRecord rows
    # ------------------------------------------------------------------
    for path in all_files:
        hist = histories[path]
        store.upsert_file(FileRecord(
            path=path,
            last_touch_commit=hist.last_touch_commit,
            last_touch_date=hist.last_touch_date,
            fan_in_count=fan_in[path],
            bug_fix_count=hist.bug_fix_count,
            top_author_pct=hist.top_author_pct,
            risk_score=risk_by_path.get(path, 0.0),
        ))

    # ------------------------------------------------------------------
    # Step 7: persist EdgeRecord rows
    # ------------------------------------------------------------------
    edge_records = [
        EdgeRecord(
            source_file=src,
            target_file=dst,
            relationship_type=rel,
            confidence=conf,
        )
        for src, dst, rel, conf in deduped_edges
    ]
    if edge_records:
        store.upsert_edges_bulk(edge_records)

    # ------------------------------------------------------------------
    # Step 8: persist scan_meta — always, even when no adapters matched
    # ------------------------------------------------------------------
    store.set_scan_meta(
        commit_hash=commit_hash or "",
        branch=branch or "",
    )

    return ScanResult(
        files_scanned=len(all_files),
        edges_stored=len(edge_records),
        commit_hash=commit_hash,
        branch=branch,
    )
