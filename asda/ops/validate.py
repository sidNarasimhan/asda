"""End-to-end self-check. Does not send mail or launch LinkedIn."""

from __future__ import annotations

from typing import Any


def self_check() -> dict[str, Any]:
    from asda.config import get_settings
    from asda.mcp_server import _rpc, tool_manifest
    from asda.ops.heartbeat import JOBS, snapshot
    from asda.ops.onboard import prompt
    from asda.ops.worker import worker_status
    from asda.runtime import effective, setup_status

    s = get_settings()
    e = effective()
    steps = setup_status()["steps"]
    worker = worker_status()
    onboard = prompt()

    mcp_init = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    mcp_ok = bool(mcp_init and mcp_init.get("result", {}).get("serverInfo", {}).get("name") == "asda")

    apollo: dict[str, Any] = {"key": bool(e.apollo_key_set), "buy": False, "detail": "No key"}
    if e.apollo_key_set:
        try:
            from asda.ingestion.apollo import ApolloSource

            apollo = ApolloSource().probe()
        except Exception as exc:
            apollo = {"key": True, "ok": False, "detail": str(exc)[:200], "buy": False}

    return {
        "agent": {
            "kind": "hybrid",
            "brain": "perceive → recall memory → plan → act → remember/reflect",
            "hands": "sequence engine + SMTP/IMAP + PhantomBuster + APScheduler",
            "memory": "evolving facts/people/preferences/playbook (strengthen on reuse)",
            "loop": [j["id"] for j in JOBS],
            "autonomous_when_live": e.live_confirmed and not e.hitl,
        },
        "llm": {
            "ready": bool(e.llm_key_set),
            "provider": "openrouter" if e.openrouter_api_key else ("xai" if e.xai_api_key else s.provider),
        },
        "email": {"connected": bool(steps.get("email")), "user": e.smtp_user},
        "linkedin": {
            "cookie": bool(steps.get("linkedin_cookie")),
            "phantoms": bool(steps.get("linkedin_phantoms")),
            "pb_key": bool(e.pb_key_set),
        },
        "worker": worker,
        "now": snapshot(worker),
        "mcp": {"ok": mcp_ok, "tools": len(tool_manifest()), "http": "POST /mcp", "stdio": "asda mcp"},
        "apollo": apollo,
        "onboard": {"ready": onboard["ready"], "missing": [x["id"] for x in onboard["missing"]]},
        "live": e.live_confirmed and not e.dry_run,
        "jobs": JOBS,
        "memory": _memory_health(),
    }


def _memory_health() -> dict:
    try:
        from asda.memory.store import count, recent

        return {"entries": count(), "recent": [m["text"][:80] for m in recent(5)]}
    except Exception as exc:
        return {"entries": 0, "error": str(exc)[:160]}
