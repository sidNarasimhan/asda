"""asda — command line for ingest, run, serve, learn."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from asda.db.session import init_db

app = typer.Typer(help="ASDA — Autonomous Sales Development Agent", no_args_is_help=True)
console = Console()


@app.callback()
def _init() -> None:
    init_db()


@app.command()
def ingest(
    source: str = typer.Argument(..., help="csv | apollo | webhook | sheets | clay | ..."),
    path: Optional[str] = typer.Option(None, help="CSV path when source=csv"),
    limit: int = 50,
) -> None:
    from asda.api.routes.ingest import _persist
    from asda.ingestion.registry import get_registry
    from asda.models.lead import LeadQuery

    extra = {"path": path} if path else {}
    leads = get_registry().get(source).fetch(LeadQuery(limit=limit, extra=extra))
    result = _persist(leads)
    console.print(result)


@app.command("run")
def run_cmd(
    lead_id: Optional[str] = None,
    limit: int = 5,
    skip_outreach: bool = True,
) -> None:
    from asda.agents.orchestrator import Orchestrator
    from asda.db.repository import Repository
    from asda.db.session import get_session

    session = get_session()
    try:
        repo = Repository(session)
        leads = [repo.get_lead(lead_id)] if lead_id else repo.list_leads(status="new", limit=limit)
        leads = [l for l in leads if l]
    finally:
        session.close()

    orch = Orchestrator()
    table = Table(title="Pipeline")
    table.add_column("Lead")
    table.add_column("Score")
    table.add_column("Decision")
    table.add_column("Status")
    for lead in leads:
        result = orch.run(lead, skip_outreach=skip_outreach)
        table.add_row(
            result["lead"].full_name,
            str(result["lead"].score),
            result["decision"],
            result["lead"].status.value,
        )
    console.print(table)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn

    uvicorn.run("asda.api.main:app", host=host, port=port, reload=True)


@app.command()
def ui(port: int = 8501) -> None:
    import uvicorn

    from asda.ops.worker import ensure_worker

    ensure_worker()
    console.print(f"Dashboard → http://localhost:{port}")
    uvicorn.run("asda.api.main:app", host="0.0.0.0", port=port, reload=False)


@app.command()
def mcp() -> None:
    """Run ASDA as an MCP server on stdio so other agents can connect."""
    from asda.mcp_server import serve_stdio

    serve_stdio()


@app.command()
def learn() -> None:
    from asda.agents.learning import LearningAgent

    insight = LearningAgent().run()
    console.print_json(insight.model_dump_json())


@app.command()
def sources() -> None:
    from asda.config import get_settings
    from asda.ingestion.registry import get_registry

    settings = get_settings()
    console.print(
        {
            "llm": settings.provider,
            "frontier": settings.resolve_model("frontier"),
            "fast": settings.resolve_model("fast"),
        }
    )
    for s in get_registry().all():
        console.print(s.healthcheck())


@app.command()
def export_lead(lead_id: str) -> None:
    from asda.db.repository import Repository
    from asda.db.session import get_session

    session = get_session()
    try:
        lead = Repository(session).get_lead(lead_id)
        if not lead:
            raise typer.BadParameter("not found")
        console.print(json.dumps(lead.model_dump(mode="json"), indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    app()
