"""
Command handlers for Guardian's CLI -- orchestration only. All real
computation is delegated to scanner, builder, render, and Agent integration.
"""

from __future__ import annotations

import argparse
import os
import sys

from src.cli.builder import build_passport, record_predictions
from src.cli.passport import default_db
from src.cli.preflight import die, preflight
from src.cli.render import render_file_names, render_json, render_text
from src.git_history import get_changed_files
from src.scanner import run_scan


def cmd_scan(args: argparse.Namespace) -> None:
    """Handle 'guardian scan': run a full scan and print a concise summary."""
    repo_path = os.path.abspath(args.repo_path)
    preflight(repo_path)

    db_path = os.path.abspath(args.db) if args.db else default_db(repo_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    result = run_scan(repo_path, db_path)

    branch = result.branch or "(detached HEAD)"
    commit = result.commit_hash[:12] if result.commit_hash else "(none)"
    print(f"Scan complete")
    print(f"  Repo:    {repo_path}")
    print(f"  Branch:  {branch}  ({commit})")
    print(f"  Files:   {result.files_scanned}")
    print(f"  Edges:   {result.edges_stored}")


def cmd_analyze(args: argparse.Namespace) -> None:
    """
    Handle 'guardian analyze': build and render a Change Passport.

    Reads existing Evidence Store data only — never rescans the repository.
    Supports --diff <ref1>..<ref2> and --files <path ...> modes.
    """
    repo_path = os.path.abspath(getattr(args, "path", None) or ".")
    preflight(repo_path)

    db_path = os.path.abspath(args.db) if args.db else default_db(repo_path)
    if not os.path.exists(db_path):
        die(
            f"Error: no Evidence Store found at '{db_path}'. "
            "Run 'guardian scan' first."
        )

    if args.diff:
        # Parse "ref1..ref2" into two separate refs.
        if ".." not in args.diff:
            die(
                f"Error: --diff value must be in 'ref1..ref2' format "
                f"(e.g. HEAD~1..HEAD), got: '{args.diff}'"
            )
        ref1, ref2 = args.diff.split("..", 1)
        try:
            changed_files = get_changed_files(repo_path, ref1, ref2)
            changed = [(cf.path, cf.status) for cf in changed_files]
        except RuntimeError as exc:
            die(f"Error: {exc}")
            return  # unreachable; satisfies type checker that changed is bound
        ref_range = args.diff
    else:
        # --files mode: check each file exists first (pre-flight §6).
        changed = []
        for p in args.files:
            abs_p = os.path.abspath(p)
            if not os.path.exists(abs_p):
                die(f"Error: '{p}' does not exist.")
            # Use path relative to repo for evidence lookup.
            rel = os.path.relpath(abs_p, repo_path)
            changed.append((rel, "M"))
        ref_range = None

    if not changed:
        print("No changed files found.")
        return

    passport = build_passport(repo_path, changed, ref_range, db_path)

    if args.file_name:
        # Risk scores are deterministic -- no AI needed here at all.
        print(render_file_names(passport))
        return

    # Agent runs once for the whole batch of changed files -- never per file,
    # and never blocks the deterministic passport above from being shown.
    # Skip it entirely when nothing has evidence to investigate -- no point
    # spending an API call on a batch with nothing real to reason about.
    if not any(fp.evidence_available for fp in passport.files):
        passport.agent_available = False
        passport.agent_error = "No evidence available for any changed file — nothing to investigate."
    else:
        from src.agent.integration import generate_agent_findings
        agent_result = generate_agent_findings(repo_path, db_path, changed)
        passport.agent_available = agent_result.available
        passport.agent_findings = agent_result.findings
        passport.agent_checks = agent_result.checks
        passport.agent_error = agent_result.error

    prediction_count, invocation_id = record_predictions(passport, db_path)

    if args.json:
        print(render_json(passport))
    else:
        print(render_text(passport, top_n=args.top))

    # Printed to stderr, not stdout -- stdout must stay pure JSON when
    # --json is used, so a script piping/parsing the output isn't broken
    # by this trailing status line.
    print(
        f"Recorded {prediction_count} prediction(s) — invocation {invocation_id}",
        file=sys.stderr,
    )
