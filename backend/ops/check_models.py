"""Dev self-check: load all ORM models and report schema coverage."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import metadata
from app.db.registry import ALL_MODELS

print("models:", len(ALL_MODELS))
missing = [t.__tablename__ for t in ALL_MODELS if not t.__table__.schema]
print("tables without schema:", missing)
print("by schema:", dict(Counter(t.__table__.schema for t in ALL_MODELS)))
print("metadata tables:", len(metadata.tables))
