"""Wipe operational data so onboarding starts empty. Does not touch .env."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from asda.config import get_settings
from asda.db.session import get_engine
from asda.runtime import RuntimeConfig, save_runtime


def reset_book(*, wipe_runtime: bool = True) -> dict[str, Any]:
    settings = get_settings()
    data = Path(settings.data_dir)
    removed: list[str] = []

    from asda.db.models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    removed.append("database tables")

    for name in ("playbook.json", "heartbeat.json"):
        path = data / name
        if path.exists():
            path.unlink()
            removed.append(name)
    if wipe_runtime:
        save_runtime(RuntimeConfig(worker_enabled=False, live_confirmed=False, dry_run=True))
        removed.append("runtime.json (keys/live cleared; .env still works if set)")

    for folder in ("pb_feeds", "uploads", "inbox"):
        d = data / folder
        if d.exists():
            for child in d.iterdir():
                if child.is_file():
                    child.unlink()
                    removed.append(str(child.name))
    pid = data / "worker.pid"
    if pid.exists():
        pid.unlink()
    return {"ok": True, "removed": removed, "n": len(removed)}
