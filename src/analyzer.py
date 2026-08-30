"""
Compatibility shim — src/analyzer.py.

The analyzer logic now lives in src/adapters/python_adapter.py.  This module
re-exports everything that was previously defined here so that any code that
still imports from src.analyzer continues to work without modification.

Nothing is reimplemented here.  If you are writing new code, import directly
from src.adapters.python_adapter instead.
"""

from src.adapters.python_adapter import (  # noqa: F401  (re-export)
    DependencyEdge,
    build_dependency_graph,
    get_edges,
    get_blast_radius,
    _extract_imports,
    _resolve_module,
    _resolve_relative_import,
    _iter_python_files,
    _rel,
    _module_to_candidates,
)
