from __future__ import annotations

from fastapi import APIRouter

from asda.agents.employee import talk
from asda.agents.report import build_brief, email_brief_to_cbo

router = APIRouter()


@router.post("/talk")
def talk_route(body: dict) -> dict:
    message = (body.get("message") or body.get("text") or "").strip()
    if not message:
        return {"reply": "Say what you need — pipeline, rules, pause, or a change to the offer."}
    return talk(message)


@router.get("/brief")
def brief() -> dict:
    return {"brief": build_brief()}


@router.post("/brief/send")
def send_brief() -> dict:
    return email_brief_to_cbo()
