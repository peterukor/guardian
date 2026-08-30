"""
Base interface for Guardian language adapters.

Every language adapter module must implement two functions with these exact
signatures.  The contracts are documented here; adapters must produce results
in the specified forms so the rest of Guardian (scanner, Evidence Store, Risk
Scorer, Agent) never needs to know which language adapter is in use.

Adapter contract (two required functions)
-----------------------------------------

1. analyze(repo_path: str) -> list[EdgeTuple]

   Walk repo_path, detect all in-repo dependencies, and return them as a list
   of EdgeTuples in the shared Guardian edge format (see below).

2. discovered_files(repo_path: str) -> list[str]

   Walk repo_path and return every file the adapter recognises as belonging to
   its language, as paths relative to repo_path with forward slashes.

   Crucially, this must include files with NO import edges — isolated files
   that import nothing and are imported by nothing still exist and must be
   recorded in the Evidence Store.  analyze() only produces edges; discovered_
   files() is the authoritative list of files the scanner should track.

Shared edge format (matches AGENTS.md Section 4):
    (source_file, target_file, relationship_type, confidence)

    source_file       — path of the file that contains the import, relative
                        to the repo root, forward-slash separated
    target_file       — path of the file being imported, same form
    relationship_type — string label, e.g. "imports"
    confidence        — 0.0–1.0; 1.0 for edges resolved to an actual file,
                        <1.0 for heuristic/inferred edges

An adapter is any module that exposes both callables above.  There is no
abstract base class — Python's duck typing is sufficient for the small set of
adapters Guardian will ever have.  This module defines the type aliases and
documented contracts in one place so it can be imported by both adapters and
the dispatcher without circular dependencies.
"""

from __future__ import annotations

# The canonical type for one edge returned by any adapter.
# A plain 4-tuple keeps the interface dependency-free (no dataclasses needed
# in callers) while still being explicit about what each position means.
EdgeTuple = tuple[str, str, str, float]
# (source_file, target_file, relationship_type, confidence)


def analyze(repo_path: str) -> list[EdgeTuple]:
    """
    Walk repo_path, detect all in-repo dependencies, and return them as a
    list of EdgeTuples.

    This function signature is the contract every adapter must satisfy.
    This module-level stub exists purely for documentation — import and call
    the concrete adapter (e.g. python_adapter.analyze) directly; do not call
    this stub.

    Raises NotImplementedError to make accidental calls to the stub obvious.
    """
    raise NotImplementedError(
        "base.analyze is a contract stub — import a concrete adapter instead"
    )


def discovered_files(repo_path: str) -> list[str]:
    """
    Walk repo_path and return every file the adapter recognises as belonging
    to its language, as relative forward-slash paths.

    Includes files that have no import edges (isolated files).  This is the
    authoritative file list the scanner uses to know which paths to track in
    the Evidence Store — analyze() only produces edges, not the full file set.

    This module-level stub exists purely for documentation — import and call
    the concrete adapter directly; do not call this stub.

    Raises NotImplementedError to make accidental calls to the stub obvious.
    """
    raise NotImplementedError(
        "base.discovered_files is a contract stub — import a concrete adapter instead"
    )
