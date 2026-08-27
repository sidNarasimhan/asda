"""Bolna voice AI. Last-leg calls after email and LinkedIn go quiet."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from asda.config import get_settings
from asda.runtime import effective, update_runtime

logger = logging.getLogger(__name__)
BASE = "https://api.bolna.ai"
AGENT_NAME = "ASDA SDR assistant"


def e164(phone: str, default_cc: str = "+91") -> str:
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return default_cc + digits
    if digits.startswith("91") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 11:
        return default_cc + digits[1:]
    return "+" + digits if not digits.startswith("0") else default_cc + digits.lstrip("0")


class BolnaError(RuntimeError):
    pass


class BolnaClient:
    def __init__(self, api_key: str | None = None) -> None:
        cfg = effective()
        self.api_key = api_key or cfg.bolna_api_key or get_settings().bolna_api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "detail": "no Bolna key"}
        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(f"{BASE}/v2/agent/all", headers=self._headers)
            if r.status_code >= 400:
                return {"ok": False, "detail": r.text[:180]}
            agents = r.json() if isinstance(r.json(), list) else []
            return {"ok": True, "agents": len(agents), "detail": f"{len(agents)} agent(s)"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:180]}

    def list_agents(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=25) as client:
            r = client.get(f"{BASE}/v2/agent/all", headers=self._headers)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def ensure_agent(self) -> str:
        cfg = effective()
        if cfg.bolna_agent_id:
            return cfg.bolna_agent_id
        for agent in self.list_agents():
            name = (agent.get("agent_name") or "").lower()
            if "asda" in name:
                aid = agent.get("id") or ""
                if aid:
                    update_runtime(bolna_agent_id=aid)
                    return aid
        try:
            created = self.create_assistant()
            aid = created.get("agent_id") or created.get("id") or ""
            if aid:
                update_runtime(bolna_agent_id=aid)
                return aid
        except BolnaError:
            logger.exception("could not create ASDA Bolna agent, using an existing one")
        agents = self.list_agents()
        if agents:
            aid = agents[0].get("id") or ""
            if aid:
                update_runtime(bolna_agent_id=aid)
                return aid
        raise BolnaError("Bolna did not return an agent_id")

    def create_assistant(self) -> dict[str, Any]:
        from asda.config import get_settings

        offer = get_settings().offer or {}
        company = offer.get("company_name") or "our company"
        cbo = offer.get("cbo_name") or "the founder"
        product = offer.get("product_name") or company
        welcome = (
            f"Hi {{lead_name}}, this is Ananya, {{cbo_name}}'s assistant at {company}. "
            "Have you got thirty seconds?"
        )
        prompt = f"""You are Ananya, a female assistant to {cbo} at {company}.
You sound like a real person on a phone. Short sentences. No em dashes. No corporate filler.
You are calling {{lead_name}} at {{lead_company}}.

Goal:
1. Confirm this is a good time. If not, ask when to call back and hang up politely.
2. In one or two lines, say who {company} is: {offer.get('tagline') or offer.get('value_proposition') or product}.
3. Gauge the prospect's current cybersecurity priorities and whether a follow-up security conversation is useful.
4. If they are even mildly open, ask if they can talk to {cbo} for five minutes right now. If not now, offer to book.
5. If they say no, thank them and hang up. Never argue.

Do not mention you are an AI. Do not read a script word for word. Transfer intent means they said yes to {cbo}.
Keep the whole call under two minutes unless they want to keep talking.
"""
        body = {
            "agent_config": {
                "agent_name": AGENT_NAME,
                "agent_welcome_message": welcome,
                "agent_type": "other",
                "calling_guardrails": {"call_start_hour": 9, "call_end_hour": 18},
                "tasks": [
                    {
                        "task_type": "conversation",
                        "toolchain": {
                            "execution": "sequential",
                            "pipelines": [["transcriber", "llm", "synthesizer"]],
                        },
                        "tools_config": {
                            "llm_agent": {
                                "agent_type": "simple_llm_agent",
                                "agent_flow_type": "streaming",
                                "llm_config": {
                                    "provider": "openai",
                                    "model": "gpt-4o-mini",
                                    "max_tokens": 150,
                                    "temperature": 0.7,
                                },
                            },
                            "synthesizer": {
                                "provider": "elevenlabs",
                                "provider_config": {
                                    "voice": "Nila",
                                    "voice_id": "V9LCAAi4tTlqe9JadbCo",
                                    "model": "eleven_turbo_v2_5",
                                },
                                "stream": True,
                                "buffer_size": 250,
                                "audio_format": "wav",
                            },
                            "transcriber": {
                                "provider": "deepgram",
                                "model": "nova-3",
                                "language": "en",
                                "stream": True,
                                "encoding": "linear16",
                                "sampling_rate": 16000,
                                "endpointing": 250,
                            },
                            "input": {"provider": "plivo", "format": "wav"},
                            "output": {"provider": "plivo", "format": "wav"},
                        },
                        "task_config": {
                            "call_terminate": 120,
                            "hangup_after_silence": 8,
                            "check_if_user_online": True,
                        },
                    }
                ],
            },
            "agent_prompts": {"task_1": {"system_prompt": prompt}},
        }
        with httpx.Client(timeout=40) as client:
            r = client.post(f"{BASE}/v2/agent", headers=self._headers, json=body)
        if r.status_code >= 400:
            logger.warning("bolna create agent %s: %s", r.status_code, r.text[:300])
            raise BolnaError(r.text[:240])
        return r.json()

    def call(
        self,
        phone: str,
        *,
        user_data: dict[str, str] | None = None,
        from_number: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise BolnaError("BOLNA_API_KEY is not set")
        dest = e164(phone)
        if not dest:
            raise BolnaError("no phone number")
        cfg = effective()
        if cfg.dry_run:
            return {"status": "dry_run", "recipient": dest, "message": "would call"}
        agent_id = self.ensure_agent()
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "recipient_phone_number": dest,
            "user_data": user_data or {},
        }
        frm = from_number or cfg.bolna_from_number
        if frm:
            payload["from_phone_number"] = e164(frm)
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{BASE}/call", headers=self._headers, json=payload)
        if r.status_code >= 400:
            raise BolnaError(r.text[:240])
        return r.json()

    def execution(self, execution_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{BASE}/executions/{execution_id}", headers=self._headers)
        if r.status_code >= 400:
            r = httpx.get(
                f"{BASE}/v2/executions/{execution_id}",
                headers=self._headers,
                timeout=20,
            )
        r.raise_for_status()
        return r.json()
