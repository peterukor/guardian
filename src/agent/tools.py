"""
Deterministic tool layer for the Guardian Agent.

Each function here is one "tool" the Agent loop may call.  All data returned
comes from the Evidence Store, Git history, or dependency-graph engines —
never from an LLM.  The Agent is only allowed to interpret this evidence, not
invent or recalculate it.

Tool outputs are plain dicts so they are JSON-serializable and can be passed
directly through watsonx (or any other provider's) tool-result API.

Tool registry
-------------
TOOLS is a list of watsonx-compatible tool definition dicts (following the
IBM function-calling schema).  The Agent loop passes this list to the model
so it knows what it can call.

TOOL_FUNCTIONS maps each tool name to its Python implementation so the loop
can dispatch by name without a large if/elif chain.
"""

from __future__ import annotations

from typing import Callable

from src.adapters.python_adapter import get_blast_radius
from src.evidence_store import EvidenceStore
from src.git_history import get_changed_files
from src.risk_scorer import classify_risk_level


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def get_file_evidence(db_path: str, file_path: str) -> dict:
    """
    Return all stored evidence for file_path from the Evidence Store.

    Covers every signal the Risk Scorer used: risk_score, risk_level,
    fan_in, bug_fix_count, top_author_pct, last_touch_date.  There is no
    separate "get risk score" tool because risk score lives in the same
    Evidence Store row as every other signal — splitting it into two round
    trips would mean the Agent calling two tools for information that comes
    from a single row.

    Returns a dict with an "error" key instead of raising when evidence is
    absent so the Agent loop can pass the error back to the model as a
    tool result rather than crashing.
    """
    store = EvidenceStore(db_path)
    try:
        rec = store.get_file(file_path)
    finally:
        store.close()

    if rec is None:
        return {
            "error": f"No evidence found for '{file_path}'. "
                     "Run 'guardian scan' first.",
            "file_path": file_path,
        }

    return {
        "file_path": rec.path,
        "risk_score": rec.risk_score,
        "risk_level": classify_risk_level(rec.risk_score),
        "fan_in": rec.fan_in_count,
        "bug_fix_count": rec.bug_fix_count,
        "top_author_pct": rec.top_author_pct,
        "last_touch_date": rec.last_touch_date,
    }


def get_file_blast_radius(db_path: str, file_path: str) -> dict:
    """
    Return the blast radius for file_path using the stored dependency graph.

    Checks that file_path has an actual evidence record before computing
    blast radius — a file never scanned would otherwise silently produce
    the same {"total": 0, ...} shape as a file genuinely confirmed to have
    zero dependents, which is exactly the kind of stale/absent-evidence
    ambiguity Guardian must never present as a real result.

    Loads the full graph from the Evidence Store via build_graph() (no
    re-parsing of source files) and delegates to the existing get_blast_radius
    implementation so the calculation is identical to what the CLI shows.

    Returns a dict with an "error" key if the file has no evidence, or the
    normal blast-radius dict (counts and sorted file lists) otherwise.
    """
    store = EvidenceStore(db_path)
    try:
        rec = store.get_file(file_path)
        if rec is None:
            return {
                "error": f"No evidence found for '{file_path}'. "
                         "Run 'guardian scan' first.",
                "file_path": file_path,
            }
        graph = store.build_graph()
    finally:
        store.close()

    return get_blast_radius(graph, file_path)


def get_diff_files(repo_path: str, ref1: str, ref2: str) -> dict:
    """
    Return the list of files changed between ref1 and ref2 in repo_path.

    Delegates to get_changed_files() from git_history so the diff logic
    lives in exactly one place.  Each entry includes the file path, change
    status (A/M/D/R), and old_path for renames.

    Returns a dict with an "error" key on git failure instead of raising,
    so the Agent loop can surface the error as a tool result.
    """
    try:
        changed = get_changed_files(repo_path, ref1, ref2)
    except RuntimeError as exc:
        return {"error": str(exc), "repo_path": repo_path, "ref1": ref1, "ref2": ref2}

    return {
        "repo_path": repo_path,
        "ref1": ref1,
        "ref2": ref2,
        "files": [
            {"path": cf.path, "status": cf.status, "old_path": cf.old_path}
            for cf in changed
        ],
    }


# ---------------------------------------------------------------------------
# Tool registry — watsonx-compatible function-calling schema
# ---------------------------------------------------------------------------
# These definitions follow the IBM watsonx.ai / OpenAI function-calling JSON
# schema.  The Agent loop passes TOOLS to the model so it knows what it can
# call; TOOL_FUNCTIONS maps name → callable for dispatch.

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_file_evidence",
            "description": (
                "Retrieve all stored evidence for a file from the Guardian "
                "Evidence Store: risk score, risk level, fan-in count, "
                "bug-fix commit count, top-author ownership percentage, and "
                "last-touch date. Call this before interpreting a file's risk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "db_path": {
                        "type": "string",
                        "description": "Filesystem path to the Guardian SQLite Evidence Store.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository root.",
                    },
                },
                "required": ["db_path", "file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_blast_radius",
            "description": (
                "Return the blast radius for a file: which other files "
                "directly or transitively depend on it and would be affected "
                "by a change. Uses the stored dependency graph — no re-parsing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "db_path": {
                        "type": "string",
                        "description": "Filesystem path to the Guardian SQLite Evidence Store.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository root.",
                    },
                },
                "required": ["db_path", "file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diff_files",
            "description": (
                "Return the list of files changed between two Git refs. "
                "Each entry includes the file path, change status "
                "(A=added, M=modified, D=deleted, R=renamed), and old path "
                "for renames."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the git repository root.",
                    },
                    "ref1": {
                        "type": "string",
                        "description": "Start git ref, e.g. 'HEAD~1' or a commit SHA.",
                    },
                    "ref2": {
                        "type": "string",
                        "description": "End git ref, e.g. 'HEAD'.",
                    },
                },
                "required": ["repo_path", "ref1", "ref2"],
            },
        },
    },
]

# TODO (post-PoC, not now): db_path/repo_path should not be tool arguments
# the model has to supply. The runtime always knows which repo/Evidence Store
# it's operating on — asking the model to produce a correct filesystem path
# as a string is unnecessary risk (hallucinated/stale/malformed paths fail as
# opaque tool errors, indistinguishable from the model being wrong). Fix:
# bind db_path/repo_path via functools.partial (or a closure) when building
# this dict for a given request, so the exposed tool schema only takes
# file_path/ref1/ref2 — no infra params at all. Not worth doing yet — this
# changes the tool schema itself, and right now we only want to prove the
# tool-calling loop works at all, one variable at a time.
#
# Maps tool name → callable.  The loop calls TOOL_FUNCTIONS[name](**args).
TOOL_FUNCTIONS: dict[str, Callable] = {
    "get_file_evidence": get_file_evidence,
    "get_file_blast_radius": get_file_blast_radius,
    "get_diff_files": get_diff_files,
}
