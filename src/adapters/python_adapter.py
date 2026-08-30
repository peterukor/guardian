"""
Python language adapter for Guardian.

Implements the adapter interface defined in src/adapters/base.py for Python
repositories.  Walks the repo, parses every .py file with the built-in `ast`
module, extracts import statements, and resolves those imports to actual files
inside the repository.

Adapter interface
-----------------
The public entry point is `analyze(repo_path)`, which returns a list of
EdgeTuples in the shared Guardian format:
    (source_file, target_file, relationship_type, confidence)

Existing public API
-------------------
The three functions used by the rest of Guardian before the adapter layer was
introduced are still exported here unchanged:
    build_dependency_graph(repo_root) -> nx.DiGraph
    get_edges(graph)                  -> list[DependencyEdge]
    get_blast_radius(graph, file)     -> dict

These are kept because the Evidence Store, CLI, and tests rely on them.  They
are not removed — only the physical location has changed (from analyzer.py to
this file).  analyzer.py now re-exports them as a thin compatibility shim.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from collections.abc import Generator

import networkx as nx

from src.adapters.base import EdgeTuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DependencyEdge:
    """
    One directed dependency between two files in the repository.

    source_file and target_file are paths relative to the repository root,
    using forward slashes, e.g. "guardian/analyzer.py".
    relationship_type is always "imports" for this adapter.
    confidence is 1.0 for resolved edges (the file actually exists) and would
    be <1.0 for heuristic edges in future language adapters.
    """
    source_file: str
    target_file: str
    relationship_type: str
    confidence: float


# ---------------------------------------------------------------------------
# Internal helpers  (identical to original analyzer.py — zero logic change)
# ---------------------------------------------------------------------------

def _rel(path: str, root: str) -> str:
    """
    Return `path` as a forward-slash path relative to `root`.
    All internal references use this form for consistency.
    """
    return os.path.relpath(path, root).replace(os.sep, "/")


def _module_to_candidates(module_name: str) -> list[str]:
    """
    Convert a dotted module name to the two file-path forms it could take.

    "guardian.analyzer"  ->  ["guardian/analyzer.py",
                               "guardian/analyzer/__init__.py"]

    The adapter checks both candidates against the real filesystem.
    """
    parts = module_name.split(".")
    base = os.path.join(*parts)
    return [base + ".py", os.path.join(base, "__init__.py")]


def _resolve_module(module_name: str, root: str) -> str | None:
    """
    Try to find a real file inside `root` that corresponds to `module_name`.

    Returns the relative path (e.g. "guardian/analyzer.py") if found,
    or None if the module is external / unresolvable.
    """
    for candidate in _module_to_candidates(module_name):
        full = os.path.join(root, candidate)
        if os.path.isfile(full):
            return candidate.replace(os.sep, "/")
    return None


def _resolve_relative_import(
    module: str | None,
    level: int,
    source_file: str,
    root: str,
) -> str | None:
    """
    Resolve a relative import (e.g. `from .utils import X`) to a real file.

    `level` is the number of leading dots: `from .utils` -> level=1,
    `from ..utils` -> level=2. `module` is the dotted suffix after the dots,
    or None for bare `from . import X`.

    Resolution works by:
    1. Walking up `level - 1` package directories from the source file's
       directory (one dot means "same package", two dots means "parent").
    2. Appending the module's path parts to get the candidate path.
    3. Checking whether that file actually exists in the repo.
    """
    # Start from the directory containing the source file.
    source_dir = os.path.dirname(os.path.join(root, source_file))

    # Each additional dot means go one package level up.
    for _ in range(level - 1):
        source_dir = os.path.dirname(source_dir)

    # Build the absolute module path relative to this anchor directory.
    if module:
        parts = module.split(".")
        anchor = os.path.join(source_dir, *parts)
    else:
        # `from . import X` -- the anchor *is* the package dir; the symbol X
        # is resolved at runtime, not statically. Nothing to record.
        return None

    # Check both file forms.
    for candidate in [anchor + ".py", os.path.join(anchor, "__init__.py")]:
        if os.path.isfile(candidate):
            return _rel(candidate, root)
    return None


def _iter_python_files(root: str) -> Generator[str, None, None]:
    """
    Yield every .py file under `root`, skipping hidden directories (e.g. .git)
    and common non-source directories (e.g. __pycache__, .venv, venv).
    Yields relative paths from `root`, e.g. "guardian/analyzer.py".
    """
    SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".tox", "node_modules"}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune directories in-place so os.walk won't descend into them.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                full = os.path.join(dirpath, filename)
                yield _rel(full, root)


def _extract_imports(source_code: str) -> list[tuple[str | None, int]]:
    """
    Parse `source_code` with ast and return a flat list of import targets.

    Each item is (module_name, level):
    - `import guardian.analyzer`       -> ("guardian.analyzer", 0)
    - `from guardian import analyzer`  -> ("guardian.analyzer", 0)
                                          also records ("guardian", 0)
    - `from .utils import X`           -> ("utils", 1)
    - `from .. import base`            -> ("base", 2)
    - `from . import something`        -> ("something", 1)

    Returns an empty list if the file cannot be parsed (syntax errors, etc.).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import a.b.c` -- each alias is a separate top-level import.
            for alias in node.names:
                results.append((alias.name, 0))

        elif isinstance(node, ast.ImportFrom):
            level = node.level  # number of leading dots
            if level == 0:
                # Absolute `from pkg import X` — record only the compound name
                # "pkg.X". If X is a submodule, "pkg/X.py" resolves directly.
                # If X is a symbol (class/function), "pkg/X.py" won't exist and
                # the resolution step falls back to "pkg/__init__.py" at that
                # point — we don't emit the fallback here to avoid duplicate edges.
                if node.module:
                    for alias in node.names:
                        results.append((f"{node.module}.{alias.name}", 0))
            else:
                # Relative import. When node.module is not None, e.g.
                # `from .utils import X`, we record ("utils", level) and
                # _resolve_relative_import handles the path lookup.
                # When node.module IS None, e.g. `from . import utils`,
                # each name in node.names is itself a module — record each one.
                if node.module is not None:
                    results.append((node.module, level))
                else:
                    for alias in node.names:
                        results.append((alias.name, level))

    return results


# ---------------------------------------------------------------------------
# Public API — existing functions (unchanged behavior)
# ---------------------------------------------------------------------------

def build_dependency_graph(repo_root: str) -> nx.DiGraph:
    """
    Walk `repo_root`, parse every Python file, and return a directed
    dependency graph.

    Nodes are relative file paths (e.g. "guardian/analyzer.py").
    Edges go from importing file -> imported file with attributes:
        relationship_type = "imports"
        confidence        = 1.0

    Only in-repo imports appear as edges. External packages are ignored.
    """
    root = os.path.abspath(repo_root)
    graph = nx.DiGraph()

    for rel_path in _iter_python_files(root):
        # Ensure every discovered file appears as a node, even if it imports
        # nothing and is imported by nobody.
        graph.add_node(rel_path)

        full_path = os.path.join(root, rel_path)
        try:
            source = open(full_path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        imports = _extract_imports(source)

        for module_name, level in imports:
            if level == 0:
                # module_name is None only for bare relative imports (level > 0),
                # so this branch is always a non-None string -- but we guard
                # explicitly to satisfy the type checker.
                if module_name is None:
                    continue
                target = _resolve_module(module_name, root)
                # If "pkg.X" didn't resolve to a file, X is likely a symbol
                # (class, function, constant) defined in the package's __init__.
                # Fall back to the parent package so we still record the real
                # file dependency rather than dropping the edge entirely.
                if target is None and "." in module_name:
                    parent = module_name.rsplit(".", 1)[0]
                    target = _resolve_module(parent, root)
            else:
                target = _resolve_relative_import(module_name, level, rel_path, root)

            if target and target != rel_path:
                # Add the edge. If it already exists (two imports that resolve
                # to the same file) nx.DiGraph simply keeps one edge.
                graph.add_edge(
                    rel_path,
                    target,
                    relationship_type="imports",
                    confidence=1.0,
                )

    return graph


def get_edges(graph: nx.DiGraph) -> list[DependencyEdge]:
    """
    Return all edges in `graph` as a list of DependencyEdge named tuples.
    Useful for inspection, testing, and writing to the Evidence Store later.
    """
    return [
        DependencyEdge(
            source_file=src,
            target_file=dst,
            relationship_type=data.get("relationship_type", "imports"),
            confidence=data.get("confidence", 1.0),
        )
        for src, dst, data in graph.edges(data=True)
    ]


def get_blast_radius(graph: nx.DiGraph, file_path: str) -> dict:
    """
    Return the blast radius of `file_path`: all files that (transitively)
    import it and would therefore be affected by a change.

    Returns a dict:
        {
            "file": file_path,
            "direct_dependents": [list of files one hop away],
            "indirect_dependents": [list of files two or more hops away],
            "total": int,
        }

    Uses the reversed graph so we can walk from `file_path` outward to
    everything that depends on it (ancestors in the dependency direction =
    descendants in the reversed graph).
    """
    reversed_graph = graph.reverse(copy=False)

    if file_path not in reversed_graph:
        return {
            "file": file_path,
            "direct_dependents": [],
            "indirect_dependents": [],
            "total": 0,
        }

    # Direct dependents: nodes exactly one hop away in the reversed graph.
    direct = set(reversed_graph.successors(file_path))

    # All transitive dependents (includes direct).
    all_dependents = nx.descendants(reversed_graph, file_path)

    indirect = all_dependents - direct

    return {
        "file": file_path,
        "direct_dependents": sorted(direct),
        "indirect_dependents": sorted(indirect),
        "total": len(all_dependents),
    }


# ---------------------------------------------------------------------------
# Adapter interface implementation
# ---------------------------------------------------------------------------

def analyze(repo_path: str) -> list[EdgeTuple]:
    """
    Adapter entry point: walk repo_path, parse all Python files, and return
    every resolved in-repo import edge as a list of EdgeTuples.

    This is the function the dispatcher calls.  It delegates entirely to
    build_dependency_graph and get_edges — no logic is duplicated.
    """
    graph = build_dependency_graph(repo_path)
    return [
        (e.source_file, e.target_file, e.relationship_type, e.confidence)
        for e in get_edges(graph)
    ]
