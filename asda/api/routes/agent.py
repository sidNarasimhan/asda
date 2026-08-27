"""HTTP face of the same MCP tools — any agent that speaks JSON can call ASDA."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from asda.mcp_server import dispatch, tool_manifest

router = APIRouter()


@router.get("/tools")
def list_tools() -> dict:
    return {"tools": tool_manifest()}


@router.post("/{name}")
def call_tool(name: str, body: dict | None = None) -> dict:
    try:
        result = dispatch(name if name.startswith("asda.") else f"asda.{name}", body or {})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "result": result}
