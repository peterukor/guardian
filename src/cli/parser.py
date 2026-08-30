"""
Argument parser and entry point for Guardian's CLI.
"""

from __future__ import annotations

import argparse

from src.cli.commands import cmd_analyze, cmd_scan


def build_parser() -> argparse.ArgumentParser:
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
    analyze_p.add_argument(
        "--file-name",
        action="store_true",
        help="Print only changed file names and risk scores. Skips the "
             "Agent entirely -- nothing is sent to the AI.",
    )

    return parser


def main() -> None:
    """Parse arguments and dispatch to the appropriate command handler."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "analyze":
        cmd_analyze(args)
