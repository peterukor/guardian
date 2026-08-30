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
        description=(
            "Guardian tells you how risky a code change is, using real git "
            "history and dependency data -- not guesses."
        ),
        epilog=(
            "Quick start:\n"
            "  guardian scan .                          # scan the repo first (do this once)\n"
            "  guardian analyze . --diff HEAD~1..HEAD   # then analyze recent changes\n\n"
            "Run 'guardian <command> --help' for full details and examples."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # -- scan ----------------------------------------------------------------
    scan_p = sub.add_parser(
        "scan",
        help="Scan a repository and save its risk data (run this first).",
        description=(
            "Scans a git repository and computes risk data for every file -- "
            "dependencies, git history, ownership -- saving it to a local "
            "database. You must run this before 'guardian analyze' will work."
        ),
        epilog=(
            "Examples:\n"
            "  guardian scan .\n"
            "  guardian scan /path/to/repo --db custom.db"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_p.add_argument("repo_path", help="Path to the git repository to scan.")
    scan_p.add_argument(
        "--db", metavar="PATH",
        help="Where to save the scan data (default: <repo>/.guardian/guardian.db). "
             "You normally don't need to set this.",
    )

    # -- analyze -------------------------------------------------------------
    analyze_p = sub.add_parser(
        "analyze",
        help="Show a risk report for changed files (run 'guardian scan' first).",
        description=(
            "Shows a risk report for a set of changed files: how risky each "
            "change is, what it might break, and AI-generated findings and "
            "recommended checks. Requires the repo to already be scanned -- "
            "run 'guardian scan' first if you haven't."
        ),
        epilog=(
            "Examples:\n"
            "  guardian analyze . --diff HEAD~1..HEAD\n"
            "  guardian analyze . --files src/app.py src/utils.py\n"
            "  guardian analyze . --diff HEAD~5..HEAD --file-name   # quick list, no AI\n"
            "  guardian analyze . --diff HEAD~5..HEAD -n 10         # show 10 files in full\n"
            "  guardian analyze . --diff HEAD~1..HEAD --json        # machine-readable output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_p.add_argument("path", nargs="?", default=".", help="Path to the repository (default: current directory).")
    mode = analyze_p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--diff", metavar="REF1..REF2",
        help="Compare two git commits/branches, e.g. HEAD~1..HEAD for the last "
             "commit, or main..my-branch to compare branches.",
    )
    mode.add_argument(
        "--files", nargs="+", metavar="FILE",
        help="Analyze specific files directly, instead of a git diff.",
    )
    analyze_p.add_argument(
        "--db", metavar="PATH",
        help="Location of the scan data to read (default: <repo>/.guardian/guardian.db). "
             "Must match what you used with 'guardian scan'.",
    )
    analyze_p.add_argument(
        "--json", action="store_true",
        help="Print the full report as JSON instead of readable text (for scripts/tooling).",
    )
    analyze_p.add_argument(
        "--file-name",
        action="store_true",
        help="Quick mode: just list changed files and their risk scores. "
             "Skips the AI entirely, so it's instant.",
    )
    analyze_p.add_argument(
        "-n", "--top",
        type=int,
        default=3,
        metavar="N",
        help="Show full detail for only the N riskiest files (default: 3). "
             "The rest are shown as a short one-line summary. Example: -n 6",
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
