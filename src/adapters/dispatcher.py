"""
Adapter dispatcher for Guardian.

Given a repository path, detects which file extensions are present and
returns the list of adapter modules that apply to it.

Design: the dispatch table maps a frozenset of file extensions to an adapter
module.  Detection walks the repo once (cheaply, no parsing) and collects all
extensions found.  Any adapter whose trigger extensions overlap with the found
set is included in the result.

Adding a new language later means adding one entry to _DISPATCH_TABLE — the
detection loop and return logic do not change.

Only .py files trigger the Python adapter in Phase 1.  More adapters will be
added in Phase 2 as new language support is introduced.

Return shape
------------
get_adapters(repo_path) returns a list of adapter modules (not bare callables),
so that callers can invoke either adapter function:
    adapter.analyze(repo_path)          -> list[EdgeTuple]
    adapter.discovered_files(repo_path) -> list[str]
"""

from __future__ import annotations

import os
from types import ModuleType

from src.adapters import python_adapter

# ---------------------------------------------------------------------------
# Dispatch table — one entry per supported language.
# Each key is a frozenset of file extensions (with leading dot) that trigger
# this adapter.  The value is the adapter module itself.
# To add a new language: add one row here, no other changes needed.
# ---------------------------------------------------------------------------
_DISPATCH_TABLE: list[tuple[frozenset[str], ModuleType]] = [
    (frozenset({".py"}), python_adapter),
]

# Directories to skip when scanning for extensions — same list used by the
# Python adapter's file walker, kept in sync here so detection is consistent.
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".tox", "node_modules"}


def _collect_extensions(repo_path: str) -> frozenset[str]:
    """
    Walk repo_path and return the set of all file extensions present.

    Only looks at filenames; does not read file contents.  Hidden directories
    and common non-source directories are skipped so extension detection isn't
    polluted by cache or vendor files.
    """
    extensions: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for filename in filenames:
            _, ext = os.path.splitext(filename)
            if ext:
                extensions.add(ext.lower())
    return frozenset(extensions)


def get_adapters(repo_path: str) -> list[ModuleType]:
    """
    Return the list of adapter modules applicable to repo_path.

    Detection is based on file extensions found under repo_path.  Each entry
    in the dispatch table whose trigger set overlaps with the found extensions
    is included — order follows the dispatch table definition order.

    Returns an empty list if no known language files are detected (e.g. a
    repo containing only config files).

    Each returned module exposes:
        module.analyze(repo_path)          -> list[EdgeTuple]
        module.discovered_files(repo_path) -> list[str]
    """
    found_extensions = _collect_extensions(repo_path)
    return [
        adapter_module
        for trigger_exts, adapter_module in _DISPATCH_TABLE
        if trigger_exts & found_extensions  # non-empty intersection → match
    ]
