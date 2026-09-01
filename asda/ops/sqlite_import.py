"""One-time, authenticated import from ASDA's local SQLite database."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from asda.db.models import (
    ApprovalRow,
    Base,
    ContentRow,
    EventRow,
    InsightRow,
    LeadRow,
    MemoryRow,
    PatternRow,
    SafetyCounterRow,
    SnapshotRow,
)
from asda.db.session import get_engine

TABLES = (
    LeadRow,
    EventRow,
    ContentRow,
    ApprovalRow,
    PatternRow,
    SafetyCounterRow,
    SnapshotRow,
    InsightRow,
    MemoryRow,
)


def import_sqlite_database(source_path: Path) -> dict[str, int]:
    """Copy ASDA rows into the configured database without retaining the upload."""
    source = create_engine(f"sqlite:///{source_path}", future=True)
    target = get_engine()
    Base.metadata.create_all(target)
    copied: dict[str, int] = {}
    with Session(source) as src, Session(target) as dst:
        for model in TABLES:
            rows = list(src.scalars(select(model)))
            for row in rows:
                values = {column.key: getattr(row, column.key) for column in model.__table__.columns}
                dst.merge(model(**values))
            copied[model.__tablename__] = len(rows)
        dst.commit()
    source.dispose()
    return copied
