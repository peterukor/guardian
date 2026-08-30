"""
Guardian's SQLite Evidence Store, as a package.

This is a pure file-organization split of what used to be one large
evidence_store.py -- no behavior changed, and the public import path is
unchanged: `from src.evidence_store import EvidenceStore, FileRecord, ...`
still works exactly as before. No other file in the project needs to change.

- schema.py: table DDL and dataclasses (FileRecord, EdgeRecord, ScanMeta,
  PredictionRecord)
- store.py:  the EvidenceStore class and its row-to-dataclass helpers
"""

from src.evidence_store.schema import (
    EdgeRecord,
    FileRecord,
    PredictionRecord,
    ScanMeta,
)
from src.evidence_store.store import EvidenceStore

__all__ = [
    "EvidenceStore",
    "FileRecord",
    "EdgeRecord",
    "ScanMeta",
    "PredictionRecord",
]
