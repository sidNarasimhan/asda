from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from asda.config import get_settings

_ENGINE: Engine | None = None
_SESSION: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _ENGINE, _SESSION
    if _ENGINE is None:
        settings = get_settings()
        url = settings.database_url
        if url.startswith("sqlite"):
            settings.ensure_data_dir()
            # Ensure parent of sqlite file exists when a custom path is used
            if ":///" in url:
                db_path = url.split(":///", 1)[1]
                if db_path and db_path != ":memory:":
                    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        kwargs: dict = {"future": True, "connect_args": connect_args}
        # :memory: is per-connection unless we share a single pool
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        _ENGINE = create_engine(url, **kwargs)
        _SESSION = sessionmaker(_ENGINE, expire_on_commit=False, future=True)
    return _ENGINE


def get_session() -> Session:
    get_engine()
    assert _SESSION is not None
    return _SESSION()


def init_db() -> None:
    from asda.db.models import Base

    Base.metadata.create_all(get_engine())


def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
