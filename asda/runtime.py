"""UI-editable runtime config (data/runtime.json). Overlays .env without committing secrets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from asda.config import get_settings


class RuntimeConfig(BaseModel):
    dry_run: bool | None = None
    hitl_stages: str | None = None
    min_score: int | None = None

    phantombuster_session_cookie: str = ""
    pb_connect_agent_id: str = ""
    pb_message_agent_id: str = ""
    pb_inbox_agent_id: str = ""
    pb_search_agent_id: str = ""

    smtp_host: str = ""
    smtp_port: int | None = None
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_reply_to: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""

    slack_webhook_url: str = ""
    crm_webhook_url: str = ""
    hubspot_access_token: str = ""
    instantly_api_key: str = ""
    instantly_campaign_id: str = ""
    cbo_email: str = ""
    public_base_url: str = ""
    linkedin_sheet_url: str = ""
    smtp_verified: bool = False
    imap_verified: bool = False
    mail_provider: str = "gmail"

    live_confirmed: bool = False

    # Secrets the UI / talk-onboarding can store without a .env file
    openrouter_api_key: str = ""
    apollo_api_key: str = ""
    phantombuster_api_key: str = ""
    xai_api_key: str = ""
    bolna_api_key: str = ""
    bolna_agent_id: str = ""
    bolna_from_number: str = ""
    bolna_skipped: bool = False
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_webhook_verify_token: str = ""
    wappfly_api_token: str = ""
    wappfly_webhook_secret: str = ""
    campaign_schedule: str = ""

    # Monthly quotas shown on Home (target vs actual)
    target_outreach: int = 10000
    target_replies: int = 400
    target_meetings: int = 80

    worker_enabled: bool = True
    who_confirmed: bool = False
    share_password: str = ""

    # Microsoft Graph (read replies when IMAP basic auth is off)
    ms_client_id: str = ""
    ms_tenant: str = ""
    ms_refresh_token: str = ""
    ms_access_token: str = ""
    ms_token_expiry: str = ""
    ms_user: str = ""
    graph_verified: bool = False
    graph_skipped: bool = False
    ms_device_code: str = ""
    ms_user_code: str = ""
    ms_verify_url: str = ""


def _path() -> Path:
    p = get_settings().data_dir / "runtime.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_runtime() -> RuntimeConfig:
    path = _path()
    if not path.exists():
        return RuntimeConfig()
    try:
        return RuntimeConfig.model_validate_json(path.read_text())
    except Exception:
        return RuntimeConfig()


def save_runtime(cfg: RuntimeConfig) -> RuntimeConfig:
    _path().write_text(cfg.model_dump_json(indent=2))
    return cfg


def update_runtime(**fields: Any) -> RuntimeConfig:
    current = load_runtime().model_dump()
    current.update({k: v for k, v in fields.items() if v is not None})
    return save_runtime(RuntimeConfig.model_validate(current))


class Effective(BaseModel):
    dry_run: bool
    hitl: set[str] = Field(default_factory=set)
    min_score: int
    pb_cookie: str = ""
    pb_connect_agent_id: str = ""
    pb_message_agent_id: str = ""
    pb_inbox_agent_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_reply_to: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    crm_webhook_url: str = ""
    hubspot_access_token: str = ""
    slack_webhook_url: str = ""
    instantly_api_key: str = ""
    instantly_campaign_id: str = ""
    cbo_email: str = ""
    live_confirmed: bool = False
    apollo_key_set: bool = False
    pb_key_set: bool = False
    instantly_key_set: bool = False
    public_base_url: str = ""
    linkedin_sheet_url: str = ""
    smtp_verified: bool = False
    imap_verified: bool = False
    mail_provider: str = "gmail"
    openrouter_api_key: str = ""
    apollo_api_key: str = ""
    phantombuster_api_key: str = ""
    xai_api_key: str = ""
    llm_key_set: bool = False
    bolna_api_key: str = ""
    bolna_agent_id: str = ""
    bolna_from_number: str = ""
    bolna_key_set: bool = False
    whatsapp_configured: bool = False
    wappfly_configured: bool = False
    graph_verified: bool = False
    ms_user: str = ""


def effective() -> Effective:
    s = get_settings()
    r = load_runtime()
    if r.hitl_stages is not None:
        hitl_raw = r.hitl_stages
    elif r.live_confirmed:
        # 24/7 employee: do not wait for approval on every send.
        hitl_raw = ""
    else:
        hitl_raw = s.hitl_stages
    dry = s.dry_run if r.dry_run is None else r.dry_run
    # Never go live until the wizard confirms, even if env says otherwise
    if not r.live_confirmed:
        dry = True
    openrouter = r.openrouter_api_key or s.openrouter_api_key
    apollo = r.apollo_api_key or s.apollo_api_key
    pb_key = r.phantombuster_api_key or s.phantombuster_api_key
    xai = r.xai_api_key or s.xai_api_key
    bolna = r.bolna_api_key or s.bolna_api_key
    whatsapp_token = r.whatsapp_access_token or s.whatsapp_access_token
    whatsapp_phone_id = r.whatsapp_phone_number_id or s.whatsapp_phone_number_id
    wappfly_token = r.wappfly_api_token or s.wappfly_api_token
    return Effective(
        dry_run=dry,
        hitl={x.strip().lower() for x in (hitl_raw or "").split(",") if x.strip()},
        min_score=r.min_score if r.min_score is not None else s.min_score,
        pb_cookie=r.phantombuster_session_cookie or s.phantombuster_session_cookie,
        pb_connect_agent_id=r.pb_connect_agent_id or s.pb_connect_agent_id,
        pb_message_agent_id=r.pb_message_agent_id or s.pb_message_agent_id,
        pb_inbox_agent_id=r.pb_inbox_agent_id or s.pb_inbox_agent_id,
        smtp_host=r.smtp_host or s.smtp_host,
        smtp_port=r.smtp_port or s.smtp_port,
        smtp_user=r.smtp_user or s.smtp_user,
        smtp_password=r.smtp_password or s.smtp_password,
        smtp_from=r.smtp_from or s.smtp_from,
        smtp_reply_to=r.smtp_reply_to or s.smtp_reply_to,
        imap_host=r.imap_host if r.imap_verified else "",
        imap_port=r.imap_port,
        imap_user=r.imap_user if r.imap_verified else "",
        imap_password=r.imap_password if r.imap_verified else "",
        crm_webhook_url=r.crm_webhook_url,
        hubspot_access_token=r.hubspot_access_token or s.hubspot_access_token,
        slack_webhook_url=r.slack_webhook_url or s.slack_webhook_url,
        instantly_api_key=r.instantly_api_key or s.instantly_api_key,
        instantly_campaign_id=r.instantly_campaign_id or s.instantly_campaign_id,
        cbo_email=r.cbo_email,
        live_confirmed=r.live_confirmed,
        apollo_key_set=bool(apollo),
        pb_key_set=bool(pb_key),
        openrouter_api_key=openrouter,
        apollo_api_key=apollo,
        phantombuster_api_key=pb_key,
        xai_api_key=xai,
        llm_key_set=bool(openrouter or xai),
        bolna_api_key=bolna,
        bolna_agent_id=r.bolna_agent_id or s.bolna_agent_id,
        bolna_from_number=r.bolna_from_number or s.bolna_from_number,
        bolna_key_set=bool(bolna),
        whatsapp_configured=bool(whatsapp_token and whatsapp_phone_id),
        wappfly_configured=bool(wappfly_token),
        instantly_key_set=bool(r.instantly_api_key or s.instantly_api_key),
        public_base_url=r.public_base_url or s.public_base_url,
        linkedin_sheet_url=r.linkedin_sheet_url,
        smtp_verified=bool(r.smtp_verified or s.smtp_verified),
        imap_verified=r.imap_verified,
        mail_provider=r.mail_provider or "gmail",
        graph_verified=bool(r.graph_verified and r.ms_refresh_token),
        ms_user=r.ms_user,
    )


def setup_status() -> dict[str, Any]:
    e = effective()
    r = load_runtime()
    s = get_settings()
    steps = {
        "llm": e.llm_key_set,
        "apollo": e.apollo_key_set,
        "phantombuster": e.pb_key_set,
        "linkedin_cookie": bool(e.pb_cookie),
        "linkedin_phantoms": bool(e.pb_connect_agent_id and e.pb_message_agent_id),
        "email": bool(e.smtp_verified),
        "crm": bool(e.crm_webhook_url or e.hubspot_access_token),
        "live": bool(r.live_confirmed) and not e.dry_run,
        "bolna": e.bolna_key_set,
    }
    return {
        "steps": steps,
        "ready_to_send": steps["phantombuster"] and steps["linkedin_cookie"] and steps["linkedin_phantoms"],
        "dry_run": e.dry_run,
        "live_confirmed": r.live_confirmed,
        "provider_llm": s.provider,
    }
