"""ASDA as an MCP server — other agents call these tools over stdio JSON-RPC.

Claude Desktop / Cursor / any MCP host:

  {
    "mcpServers": {
      "asda": { "command": "asda", "args": ["mcp"] }
    }
  }

HTTP agents can POST /api/agent/{tool} with a JSON body instead.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from asda import __version__

ToolFn = Callable[[dict[str, Any]], Any]


def _tools() -> dict[str, dict[str, Any]]:
    return {
        "asda.status": {
            "description": "Employee status: live/practice, worker, connections, this-month scoreboard.",
            "schema": {"type": "object", "properties": {}},
            "fn": _status,
        },
        "asda.talk": {
            "description": "Talk to ASDA in plain language (pause, targeting, how the week went).",
            "schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            "fn": _talk,
        },
        "asda.metrics": {
            "description": "Pipeline metrics and monthly target vs actual.",
            "schema": {"type": "object", "properties": {}},
            "fn": _metrics,
        },
        "asda.activity": {
            "description": "Real activity log — only actions ASDA actually took.",
            "schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 25}},
            },
            "fn": _activity,
        },
        "asda.leads.list": {
            "description": "List leads in the book.",
            "schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            "fn": _leads_list,
        },
        "asda.leads.run": {
            "description": "Research a lead (and optionally start outreach).",
            "schema": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "outreach": {"type": "boolean", "default": False},
                },
                "required": ["lead_id"],
            },
            "fn": _leads_run,
        },
        "asda.pause": {
            "description": "Pause sending (practice mode).",
            "schema": {"type": "object", "properties": {}},
            "fn": lambda _: _talk({"message": "pause sending"}),
        },
        "asda.resume": {
            "description": "Resume sending if live is confirmed.",
            "schema": {"type": "object", "properties": {}},
            "fn": lambda _: _talk({"message": "resume sending"}),
        },
        "asda.learn": {
            "description": "Run the learning loop and update the playbook.",
            "schema": {"type": "object", "properties": {}},
            "fn": _learn,
        },
        "asda.targets.set": {
            "description": "Set this month's outreach / replies / meetings targets.",
            "schema": {
                "type": "object",
                "properties": {
                    "outreach": {"type": "integer"},
                    "replies": {"type": "integer"},
                    "meetings": {"type": "integer"},
                },
            },
            "fn": _set_targets,
        },
        "asda.worker.start": {
            "description": "Start the employee worker (due steps, inbox, Sunday learn).",
            "schema": {"type": "object", "properties": {}},
            "fn": _worker_start,
        },
        "asda.worker.stop": {
            "description": "Stop the employee worker.",
            "schema": {"type": "object", "properties": {}},
            "fn": _worker_stop,
        },
        "asda.company.get": {
            "description": "Read the company / offer the agent works for.",
            "schema": {"type": "object", "properties": {}},
            "fn": _company_get,
        },
        "asda.workboard": {
            "description": "Live channel pipelines: mail queued/sent/conversations, LinkedIn invite/accepted/message, and what the employee is doing now.",
            "schema": {"type": "object", "properties": {}},
            "fn": _workboard,
        },
        "asda.validate": {
            "description": "Self-check: LLM, mail, LinkedIn, worker, Apollo plan, MCP. Does not send.",
            "schema": {"type": "object", "properties": {}},
            "fn": _validate,
        },
        "asda.leads.purge_fakes": {
            "description": "Delete sample/demo leads. Keeps known live people (Sanath, Kushal / BoxUp).",
            "schema": {"type": "object", "properties": {}},
            "fn": lambda _: _purge(),
        },
        "asda.onboard.status": {
            "description": "Which API keys / connections are still missing.",
            "schema": {"type": "object", "properties": {}},
            "fn": lambda _: _onboard(),
        },
        "asda.memory.recall": {
            "description": "Search evolving memory (facts, people, preferences, playbook, episodes).",
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "lead_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
            "fn": _memory_recall,
        },
        "asda.memory.remember": {
            "description": "Write a durable memory. Near-duplicates are strengthened, not copied.",
            "schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "description": "episode|fact|person|preference|playbook|goal|mistake"},
                    "lead_id": {"type": "string"},
                    "importance": {"type": "number"},
                },
                "required": ["text"],
            },
            "fn": _memory_remember,
        },
        "asda.tick": {
            "description": "Run one agent tick: recall, harvest events, reflect, write memory.",
            "schema": {"type": "object", "properties": {}},
            "fn": lambda _: _tick(),
        },
    }


def dispatch(name: str, arguments: dict[str, Any] | None = None) -> Any:
    spec = _tools().get(name)
    if not spec:
        raise KeyError(f"unknown tool: {name}")
    return spec["fn"](arguments or {})


def tool_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["schema"],
        }
        for name, spec in _tools().items()
    ]


def _status(_: dict) -> dict:
    from asda.ops.analytics import scoreboard
    from asda.ops.worker import worker_status
    from asda.runtime import setup_status

    return {
        "setup": setup_status(),
        "worker": worker_status(),
        "scoreboard": scoreboard(),
    }


def _talk(args: dict) -> dict:
    from asda.agents.employee import talk

    return talk(str(args.get("message") or ""))


def _metrics(_: dict) -> dict:
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.ops.analytics import scoreboard

    session = get_session()
    try:
        metrics = Repository(session).metrics()
    finally:
        session.close()
    return {"metrics": metrics, "scoreboard": scoreboard()}


def _activity(args: dict) -> list:
    from asda.ops.analytics import dashboard_context

    limit = int(args.get("limit") or 25)
    return dashboard_context()["events"][:limit]


def _leads_list(args: dict) -> list:
    from asda.db.repository import Repository
    from asda.db.session import get_session

    session = get_session()
    try:
        leads = Repository(session).list_leads(
            status=args.get("status"),
            limit=int(args.get("limit") or 20),
        )
        return [
            {
                "id": l.id,
                "name": l.full_name,
                "company": l.company.name,
                "title": l.title,
                "score": l.score,
                "status": l.status.value,
            }
            for l in leads
        ]
    finally:
        session.close()


def _leads_run(args: dict) -> dict:
    from asda.agents.orchestrator import Orchestrator
    from asda.db.repository import Repository
    from asda.db.session import get_session

    session = get_session()
    try:
        lead = Repository(session).get_lead(str(args.get("lead_id") or ""))
    finally:
        session.close()
    if not lead:
        return {"ok": False, "error": "lead not found"}
    result = Orchestrator().run(lead, skip_outreach=not bool(args.get("outreach")))
    return {
        "ok": True,
        "decision": result["decision"],
        "score": result["lead"].score,
        "error": result.get("error"),
    }


def _learn(_: dict) -> dict:
    from asda.agents.learning import LearningAgent

    insight = LearningAgent().run()
    return insight.model_dump(mode="json")


def _set_targets(args: dict) -> dict:
    from asda.models.events import EventType
    from asda.ops.activity import log
    from asda.runtime import load_runtime, update_runtime

    rt = load_runtime()
    fields = {}
    if args.get("outreach") is not None:
        fields["target_outreach"] = int(args["outreach"])
    if args.get("replies") is not None:
        fields["target_replies"] = int(args["replies"])
    if args.get("meetings") is not None:
        fields["target_meetings"] = int(args["meetings"])
    if fields:
        rt = update_runtime(**fields)
        log(EventType.CONFIG_UPDATED, summary="Updated monthly targets", **fields)
    return {
        "outreach": rt.target_outreach,
        "replies": rt.target_replies,
        "meetings": rt.target_meetings,
    }


def _worker_start(_: dict) -> dict:
    from asda.ops.worker import start_worker

    return start_worker()


def _worker_stop(_: dict) -> dict:
    from asda.ops.worker import stop_worker

    return stop_worker()


def _company_get(_: dict) -> dict:
    from asda.ops.company import load_offer

    return load_offer()


def _workboard(_: dict) -> dict:
    from asda.ops.workboard import workboard

    board = workboard()
    # compact for other agents
    def _names(cols):
        return {c["key"]: [l["name"] for l in c["leads"]] for c in cols}

    return {
        "now": board["now"],
        "email": _names(board["email"]),
        "linkedin": _names(board["linkedin"]),
        "counts": board["counts"],
    }


def _validate(_: dict) -> dict:
    from asda.ops.validate import self_check

    return self_check()


def _purge() -> dict:
    from asda.ops.hygiene import purge_fake_leads

    return purge_fake_leads()


def _memory_recall(args: dict) -> list:
    from asda.memory.store import search

    return search(
        str(args.get("query") or ""),
        lead_id=args.get("lead_id") or None,
        limit=int(args.get("limit") or 10),
        mark_used=True,
    )


def _memory_remember(args: dict) -> dict:
    from asda.memory.store import remember

    return remember(
        str(args.get("text") or ""),
        kind=str(args.get("kind") or "fact"),
        lead_id=args.get("lead_id") or None,
        importance=float(args.get("importance") or 0.7),
        source="mcp",
    )


def _tick() -> dict:
    from asda.agents.brain import tick

    return tick()


def _onboard() -> dict:
    from asda.ops.onboard import prompt

    data = prompt()
    # never return secrets
    return {
        "ready": data["ready"],
        "ask": data["ask"],
        "missing": [s["id"] for s in data["missing"]],
        "steps": data["steps"],
    }


def _rpc(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "asda", "version": __version__},
            },
        }
    if method == "notifications/initialized" or method is None:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tool_manifest()}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = dispatch(name, arguments)
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": str(exc)},
            }
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"unknown method {method}"},
    }


def serve_stdio() -> None:
    """MCP stdio loop. One JSON-RPC object per line."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        resp = _rpc(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
