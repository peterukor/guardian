"""
CLI entry point for Guardian.

Handles argument parsing, pre-flight validation, orchestration of existing
engines, and rendering of results.  No business logic lives here — every
computation is delegated to scanner, evidence_store, git_history, risk_scorer,
or python_adapter.

Commands
--------
    guardian scan   <repo_path> [--db <path>]
    guardian analyze [path] --diff <ref1>..<ref2> [--db <path>] [--json]
    guardian analyze [path] --files <file ...>   [--db <path>] [--json]

Default database path: <repo>/.guardian/guardian.db
This keeps evidence co-located with the repository (like .git/) and avoids
collisions when multiple repositories are used on the same machine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field

from src.adapters.python_adapter import get_blast_radius
from src.evidence_store import EvidenceStore
from src.git_history import get_changed_files
from src.risk_scorer import classify_risk_level
from src.scanner import run_scan


# ---------------------------------------------------------------------------
# Default DB path
# ---------------------------------------------------------------------------

_DB_FILENAME = os.path.join(".guardian", "guardian.db")


def _default_db(repo_path: str) -> str:
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


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def _check_path_exists(path: str) -> None:
    """Abort with a clear message if path does not exist on disk."""
    if not os.path.exists(path):
        _die(f"Error: '{path}' does not exist.")


def _check_is_git_repo(path: str) -> None:
    """Abort with a clear message if path is not inside a git repository."""
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _die(
            f"Error: '{path}' is not a git repository. "
            "Guardian needs git history to compute risk scores."
        )


def _check_has_commits(path: str) -> None:
    """Abort with a clear message if the repository has no commits yet."""
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _die("No commit history found — risk scores require at least one commit.")


def _preflight(path: str) -> None:
    """Run all three pre-flight checks in the required order."""
    _check_path_exists(path)
    _check_is_git_repo(path)
    _check_has_commits(path)


def _die(message: str) -> None:
    """Print message to stderr and exit with code 1."""
    print(message, file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Passport builder
# ---------------------------------------------------------------------------

def _build_file_passport(path: str, status: str, store: EvidenceStore) -> FilePassport:
    """
    Build a FilePassport for one changed file using stored evidence.

    Status is checked first, before querying the store.  Deleted ("D") and
    renamed ("R") files must report evidence_available=False even when a
    stale record still exists in the store from a prior scan — showing old
    evidence as if it were current would be a data-honesty violation.

    For all other statuses, evidence_available=False is also returned when
    the store has no record (file not yet scanned).
    """
    _unavailable = FilePassport(
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
        return _unavailable

    rec = store.get_file(path)
    if rec is None:
        return _unavailable

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


def _build_passport(
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
            passport.files.append(_build_file_passport(path, status, store))
    finally:
        store.close()
    return passport


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_text(passport: ChangePassport) -> str:
    """
    Render a ChangePassport as the human-readable format specified in AGENTS.md.

    One block per changed file.  Files with no evidence get an explicit
    'Evidence unavailable' notice rather than zeros.  Agent findings/checks
    are rendered once, after all file blocks, since the Agent runs once per
    call over the whole changed-file set -- not once per file.
    """
    lines: list[str] = []
    for fp in passport.files:
        lines.append("\nGUARDIAN CHANGE PASSPORT")
        lines.append("─" * 24)

        if not fp.evidence_available:
            lines.append(f"File: {fp.path}  [{fp.status}]")
            lines.append("Evidence unavailable — file not in Evidence Store.")
            lines.append("Run 'guardian scan' first, or this file may have been deleted.")
            continue

        lines.append(f"Risk: {fp.risk_level} — {fp.risk_score:.1f}/10")
        lines.append("")
        lines.append(f"Changed files: {fp.path}  [{fp.status}]")
        lines.append(
            f"Blast radius: {fp.blast_radius_total} dependent files "
            f"({fp.blast_radius_direct} direct, {fp.blast_radius_indirect} indirect)"
        )
        lines.append("")
        lines.append("Risk factors:")
        lines.append(f"  Fan-in: {fp.fan_in}")
        lines.append(f"  Bug-fix commits: {fp.bug_fix_count}")
        top_pct = fp.top_author_pct or 0.0
        lines.append(f"  Top author concentration: {top_pct * 100:.0f}%")
        last = fp.last_touch_date if fp.last_touch_date else "unknown"
        lines.append(f"  Last touch: {last}")

    lines.append("")
    lines.append("Important findings:")
    if passport.agent_available:
        for finding in passport.agent_findings:
            lines.append(f"  - {finding}")
    else:
        lines.append(f"  [Agent unavailable — {passport.agent_error or 'unknown reason'}]")

    lines.append("")
    lines.append("Recommended checks:")
    if passport.agent_available:
        for check in passport.agent_checks:
            lines.append(f"  - {check}")
    else:
        lines.append(f"  [Agent unavailable — {passport.agent_error or 'unknown reason'}]")

    return "\n".join(lines)


def _render_json(passport: ChangePassport) -> str:
    """Serialize the ChangePassport to JSON. Uses dataclass-to-dict conversion."""
    return json.dumps(asdict(passport), indent=2)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_scan(args: argparse.Namespace) -> None:
    """Handle 'guardian scan': run a full scan and print a concise summary."""
    repo_path = os.path.abspath(args.repo_path)
    _preflight(repo_path)

    db_path = os.path.abspath(args.db) if args.db else _default_db(repo_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    result = run_scan(repo_path, db_path)

    branch = result.branch or "(detached HEAD)"
    commit = result.commit_hash[:12] if result.commit_hash else "(none)"
    print(f"Scan complete")
    print(f"  Repo:    {repo_path}")
    print(f"  Branch:  {branch}  ({commit})")
    print(f"  Files:   {result.files_scanned}")
    print(f"  Edges:   {result.edges_stored}")
    print(f"  DB:      {db_path}")


def _cmd_analyze(args: argparse.Namespace) -> None:
    """
    Handle 'guardian analyze': build and render a Change Passport.

    Reads existing Evidence Store data only — never rescans the repository.
    Supports --diff <ref1>..<ref2> and --files <path ...> modes.
    """
    repo_path = os.path.abspath(getattr(args, "path", None) or ".")
    _preflight(repo_path)

    db_path = os.path.abspath(args.db) if args.db else _default_db(repo_path)
    if not os.path.exists(db_path):
        _die(
            f"Error: no Evidence Store found at '{db_path}'. "
            "Run 'guardian scan' first."
        )

    if args.diff:
        # Parse "ref1..ref2" into two separate refs.
        if ".." not in args.diff:
            _die(
                f"Error: --diff value must be in 'ref1..ref2' format "
                f"(e.g. HEAD~1..HEAD), got: '{args.diff}'"
            )
        ref1, ref2 = args.diff.split("..", 1)
        try:
            changed_files = get_changed_files(repo_path, ref1, ref2)
            changed = [(cf.path, cf.status) for cf in changed_files]
        except RuntimeError as exc:
            _die(f"Error: {exc}")
            return  # unreachable; satisfies type checker that changed is bound
        ref_range = args.diff
    else:
        # --files mode: check each file exists first (pre-flight §6).
        changed = []
        for p in args.files:
            abs_p = os.path.abspath(p)
            if not os.path.exists(abs_p):
                _die(f"Error: '{p}' does not exist.")
            # Use path relative to repo for evidence lookup.
            rel = os.path.relpath(abs_p, repo_path)
            changed.append((rel, "M"))
        ref_range = None

    if not changed:
        print("No changed files found.")
        return

    passport = _build_passport(repo_path, changed, ref_range, db_path)

    # Agent runs once for the whole batch of changed files -- never per file,
    # and never blocks the deterministic passport above from being shown.
    from src.agent.integration import generate_agent_findings
    agent_result = generate_agent_findings(repo_path, db_path, changed)
    passport.agent_available = agent_result.available
    passport.agent_findings = agent_result.findings
    passport.agent_checks = agent_result.checks
    passport.agent_error = agent_result.error

    if args.json:
        print(_render_json(passport))
    else:
        print(_render_text(passport))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser with sub-commands."""
    parser = argparse.ArgumentParser(
        prog="guardian",
        description="Evidence-based pre-merge intelligence for code changes.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # -- scan ----------------------------------------------------------------
    scan_p = sub.add_parser("scan", help="Scan a repository and build the Evidence Store.")
    scan_p.add_argument("repo_path", help="Path to the git repository root.")
    scan_p.add_argument("--db", metavar="PATH", help="Evidence Store path (default: <repo>/.guardian/guardian.db).")

    # -- analyze -------------------------------------------------------------
    analyze_p = sub.add_parser("analyze", help="Analyze changed files and produce a Change Passport.")
    analyze_p.add_argument("path", nargs="?", default=".", help="Repository path (default: current directory).")
    mode = analyze_p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diff", metavar="REF1..REF2", help="Git diff range, e.g. HEAD~1..HEAD.")
    mode.add_argument("--files", nargs="+", metavar="FILE", help="Explicit file list.")
    analyze_p.add_argument("--db", metavar="PATH", help="Evidence Store path (default: <repo>/.guardian/guardian.db).")
    analyze_p.add_argument("--json", action="store_true", help="Output as JSON.")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and dispatch to the appropriate command handler."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        _cmd_scan(args)
    elif args.command == "analyze":
        _cmd_analyze(args)


if __name__ == "__main__":
    main()
