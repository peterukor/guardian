"""
EvidenceStore -- the final class, assembled from focused per-table mixins.

Each mixin owns one table's operations (see base.py, files.py, edges.py,
scan_meta.py, predictions.py, graph.py). This file only combines them -- it
contains no query logic of its own. Splitting by table this way keeps each
file focused on one concern, the same pattern used throughout this project
(one file per adapter, one module per engine).

Open with EvidenceStore(path) where path is a filesystem path to the .db
file. Pass ":memory:" for an in-memory database (tests only). Schema is
created automatically on first open; subsequent opens are idempotent.
"""

from __future__ import annotations

from src.evidence_store.base import _EvidenceStoreBase
from src.evidence_store.edges import _EdgeOpsMixin
from src.evidence_store.files import _FileOpsMixin
from src.evidence_store.graph import _GraphOpsMixin
from src.evidence_store.predictions import _PredictionOpsMixin
from src.evidence_store.scan_meta import _ScanMetaOpsMixin


class EvidenceStore(
    _GraphOpsMixin,
    _FileOpsMixin,
    _EdgeOpsMixin,
    _ScanMetaOpsMixin,
    _PredictionOpsMixin,
    _EvidenceStoreBase,
):
    """
    Thin wrapper around the SQLite database file. See module docstring for
    usage. No method names overlap between the mixins above, so combining
    them here carries no MRO ambiguity -- each table's operations are fully
    independent of the others.
    """
    pass
