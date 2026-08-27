"""Start / stop / inspect the background employee (due-steps + inbox + learn)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from asda.config import get_settings


def _pid_path() -> Path:
    p = get_settings().data_dir / "worker.pid"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def worker_status() -> dict:
    path = _pid_path()
    if not path.exists():
        return {"running": False, "pid": None}
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return {"running": False, "pid": None}
    if _alive(pid):
        from asda.ops.heartbeat import snapshot

        beat = snapshot({"running": True, "pid": pid})
        return {"running": True, "pid": pid, "now": beat, "stale": beat.get("stale")}
    return {"running": False, "pid": pid}


def ensure_worker() -> dict:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"running": False, "pid": None, "skipped": "pytest"}
    from asda.runtime import load_runtime

    if load_runtime().worker_enabled is False:
        st = worker_status()
        st["disabled"] = True
        return st
    st = worker_status()
    if st["running"]:
        return st
    log_path = get_settings().data_dir / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "asda.workers.runner"],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=fh,
        stderr=fh,
        start_new_session=True,
    )
    _pid_path().write_text(str(proc.pid))
    from asda.models.events import EventType
    from asda.ops.activity import log

    log(EventType.WORKER_STARTED, summary="Employee started", pid=proc.pid)
    return {"running": True, "pid": proc.pid, "started": True}


def start_worker() -> dict:
    from asda.runtime import update_runtime

    update_runtime(worker_enabled=True)
    return ensure_worker()


def stop_worker() -> dict:
    from asda.models.events import EventType
    from asda.ops.activity import log
    from asda.runtime import update_runtime

    st = worker_status()
    pid = st.get("pid")
    if st.get("running") and pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    update_runtime(worker_enabled=False)
    log(EventType.WORKER_STOPPED, summary="Employee stopped", pid=pid)
    return {"running": False, "pid": pid, "disabled": True}
