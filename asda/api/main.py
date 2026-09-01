from __future__ import annotations

import base64
import hmac
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from asda import __version__
from fastapi.responses import FileResponse

from fastapi.staticfiles import StaticFiles

from asda.api.routes import agent, approvals, employee, ingest, leads, metrics, pipeline, setup
from asda.config import get_settings
from asda.db.session import init_db
from asda.ops.worker import ensure_worker
from asda.web.router import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    try:
        from asda.memory.store import seed_if_empty

        seed_if_empty()
    except Exception:
        logging.getLogger(__name__).exception("memory seed failed")
    ensure_worker()  # no-ops if the CBO has stopped the employee
    yield


app = FastAPI(
    title="ASDA — Autonomous Sales Development Agent",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _share_password() -> str:
    pwd = os.environ.get("ASDA_SHARE_PASSWORD") or ""
    if pwd:
        return pwd
    try:
        from asda.runtime import load_runtime

        return load_runtime().share_password or ""
    except Exception:
        return ""


def _share_ok(request: Request, pwd: str) -> bool:
    token = hmac.new(b"asda-share", pwd.encode(), "sha256").hexdigest()[:32]
    if hmac.compare_digest(request.cookies.get("asda_share") or "", token):
        return True
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("basic "):
        try:
            user, given = base64.b64decode(header.split(" ", 1)[1]).decode().split(":", 1)
        except Exception:
            return False
        return hmac.compare_digest(user, "asda") and hmac.compare_digest(given, pwd)
    return False


@app.middleware("http")
async def share_password_gate(request: Request, call_next):
    """Public share link needs a password. Login page, not a browser popup."""
    path = request.url.path
    if path.startswith("/static") or path in {"/login", "/favicon.ico", "/api/ingest/signalhire/callback", "/webhooks/whatsapp", "/internal/bootstrap/sqlite"} or path.startswith("/webhooks/wappfly/"):
        return await call_next(request)
    pwd = _share_password()
    if not pwd or _share_ok(request, pwd):
        response = await call_next(request)
        response.headers["Email-Obfuscation"] = "off"
        return response
    if request.method in {"GET", "HEAD"}:
        nxt = path if path.startswith("/") else "/"
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/login?next=" + nxt, status_code=303)
    return Response("Sign in at /login", status_code=401)

app.include_router(leads.router, prefix="/api/leads", tags=["leads"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
app.include_router(setup.router, prefix="/api/setup", tags=["setup"])
app.include_router(employee.router, prefix="/api/employee", tags=["employee"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(web_router)

from pathlib import Path

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "web" / "static")), name="static")


@app.post("/internal/bootstrap/sqlite")
async def bootstrap_sqlite(request: Request, database: UploadFile):
    """One-time migration endpoint, protected by an unguessable deployment secret."""
    token = os.environ.get("ASDA_BOOTSTRAP_TOKEN", "")
    supplied = request.headers.get("x-asda-bootstrap-token", "")
    if not token or not hmac.compare_digest(supplied, token):
        raise HTTPException(403, "Invalid bootstrap token")
    if not (database.filename or "").endswith(".db"):
        raise HTTPException(400, "Expected an ASDA SQLite .db file")
    from asda.db.models import LeadRow
    from asda.db.session import get_session
    from asda.ops.sqlite_import import import_sqlite_database

    session = get_session()
    try:
        if session.query(LeadRow).count() > 0:
            raise HTTPException(409, "Database already contains leads")
    finally:
        session.close()
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        written = 0
        while chunk := await database.read(1024 * 1024):
            written += len(chunk)
            if written > 80 * 1024 * 1024:
                raise HTTPException(413, "Database upload exceeds 80 MB")
            tmp.write(chunk)
        tmp.flush()
        return {"ok": True, "copied": import_sqlite_database(Path(tmp.name))}


@app.get("/feeds/{lead_id}.csv")
def lead_feed(lead_id: str):
    """Public CSV PhantomBuster fetches for LinkedIn Outreach."""
    path = get_settings().data_dir / "pb_feeds" / f"{lead_id}.csv"
    if not path.exists():
        from fastapi import HTTPException

        raise HTTPException(404, "feed not found")
    return FileResponse(path, media_type="text/csv")


@app.post("/mcp")
async def mcp_http(request: Request):
    """JSON-RPC MCP over HTTP. Same tools as `asda mcp` stdio."""
    body = await request.json()
    from asda.mcp_server import _rpc

    resp = _rpc(body)
    if resp is None:
        return JSONResponse({"jsonrpc": "2.0", "result": None})
    return JSONResponse(resp)


@app.get("/health")
def health() -> dict:
    from asda.config import get_settings
    from asda.ops.heartbeat import snapshot
    from asda.ops.worker import worker_status
    from asda.runtime import effective, setup_status

    settings = get_settings()
    e = effective()
    return {
        "ok": True,
        "version": __version__,
        "llm": {
            "provider": "openrouter" if e.openrouter_api_key else ("xai" if e.xai_api_key else settings.provider),
            "frontier": settings.resolve_model("frontier"),
            "fast": settings.resolve_model("fast"),
            "key": bool(e.llm_key_set),
        },
        "setup": setup_status()["steps"],
        "worker": worker_status(),
        "now": snapshot(worker_status()),
    }
