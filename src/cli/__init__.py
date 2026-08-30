"""
CLI entry point for Guardian, as a package.

This is a pure file-organization split of what used to be one large
cli.py -- no behavior changed. `python -m src.cli` still works exactly as
before, via __main__.py.

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

File layout
-----------
- passport.py:  FilePassport/ChangePassport dataclasses, default_db()
- preflight.py: path/repo/commit validation checks
- builder.py:   reads Evidence Store, builds passports, records predictions
- render.py:    text and JSON rendering, both from the same passport object
- commands.py:  cmd_scan/cmd_analyze -- orchestration only
- parser.py:    argument parsing and main()
"""

from src.cli.parser import build_parser, main

__all__ = ["build_parser", "main"]
