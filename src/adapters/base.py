"""
Base interface for Guardian language adapters.

Every language adapter must implement the `analyze` function with this exact
signature.  The shared edge format is documented here; all adapters must
produce edges in this form so the rest of Guardian (Evidence Store, Risk
Scorer, Agent) never needs to know which language adapter produced an edge.

Shared edge format (matches AGENTS.md Section 4):
    (source_file, target_file, relationship_type, confidence)

    source_file       — path of the file that contains the import, relative
                        to the repo root, forward-slash separated
    target_file       — path of the file being imported, same form
    relationship_type — string label, e.g. "imports"
    confidence        — 0.0–1.0; 1.0 for edges resolved to an actual file,
                        <1.0 for heuristic/inferred edges

An adapter is any module that exposes a callable with this signature.  There
is no abstract base class — Python's duck typing is sufficient for the small
set of adapters Guardian will ever have.  This module defines the type alias
and the expected contract in one place so it can be imported by both adapters
and the dispatcher without circular dependencies.
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
