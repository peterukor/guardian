"""
Graph reconstruction for EvidenceStore, as a mixin.

Cross-cutting: combines data from both files and edges tables, so it doesn't
belong to either individual mixin. Assumes get_all_files() and
get_all_edges() are available on the combined class (see store.py).
"""

from __future__ import annotations

import networkx as nx

from src.evidence_store.edges import _EdgeOpsMixin
from src.evidence_store.files import _FileOpsMixin


class _GraphOpsMixin(_FileOpsMixin, _EdgeOpsMixin):
    """Graph reconstruction. Combined into EvidenceStore -- see store.py.
    Inherits from _FileOpsMixin/_EdgeOpsMixin so get_all_files()/
    get_all_edges() are real, known methods here -- no repeated type
    declarations needed."""

    def build_graph(self) -> nx.DiGraph:
        """
        Reconstruct a NetworkX directed graph entirely from the stored evidence.

        Every file in the files table is added as a node, including isolated
        files that have no edges (so blast-radius lookups don't silently miss
        them).  Every row in the edges table is added as a directed edge with
        relationship_type and confidence preserved as edge attributes.

        The resulting graph is compatible with get_blast_radius() from
        python_adapter — the node and edge attribute format is identical to
        what build_dependency_graph() produces.  No repository parsing or
        adapter calls are made.
        """
        graph = nx.DiGraph()
        for rec in self.get_all_files():
            graph.add_node(rec.path)
        for edge in self.get_all_edges():
            graph.add_edge(
                edge.source_file,
                edge.target_file,
                relationship_type=edge.relationship_type,
                confidence=edge.confidence,
            )
        return graph
