"""Runtime configuration. Env vars win; YAML supplies offer + safety defaults."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Bare xAI model names → OpenRouter slugs (used when provider=openrouter)
_XAI_TO_OPENROUTER = {
    "grok-4.6": "x-ai/grok-4",
    "grok-4.5": "x-ai/grok-4",
    "grok-4.3": "x-ai/grok-3-mini",
    "grok-4": "x-ai/grok-4",
    "grok-3": "x-ai/grok-3",
    "grok-3-mini": "x-ai/grok-3-mini",
    "grok-2": "x-ai/grok-2-1212",
}


def _config_dirs() -> list[Path]:
    env_dir = Path(__import__("os").environ.get("ASDA_CONFIG_DIR", ""))
    candidates = [
        env_dir,
        Path.cwd() / "config",
        ROOT / "config",
        Path(__file__).resolve().parent / "config",
    ]
    return [p for p in candidates if p and p.exists()]


def _load_yaml(name: str) -> dict[str, Any]:
    for directory in _config_dirs():
        path = directory / name
        if path.exists():
            with path.open() as fh:
                return yaml.safe_load(fh) or {}
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="dev", alias="ASDA_ENV")
    log_level: str = Field(default="INFO", alias="ASDA_LOG_LEVEL")
    data_dir: Path = Field(default=Path("./data"), alias="ASDA_DATA_DIR")
    min_score: int = Field(default=55, alias="ASDA_MIN_SCORE")
    dry_run: bool = Field(default=True, alias="ASDA_DRY_RUN")
    hitl_stages: str = Field(default="reply,outreach", alias="ASDA_HITL_STAGES")

    database_url: str = Field(default="sqlite:///./data/asda.db", alias="DATABASE_URL")
    redis_url: str = Field(default="", alias="REDIS_URL")

    llm_provider: str = Field(default="auto", alias="ASDA_LLM_PROVIDER")
    # auto → OPENROUTER_API_KEY wins, else XAI_API_KEY, else FakeLLM

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_site_url: str = Field(default="https://localhost", alias="OPENROUTER_SITE_URL")
    openrouter_app_name: str = Field(default="ASDA", alias="OPENROUTER_APP_NAME")

    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")
    model_frontier: str = Field(default="grok-4.6", alias="ASDA_MODEL_FRONTIER")
    model_fast: str = Field(default="grok-4.3", alias="ASDA_MODEL_FAST")

    # Used when provider=openrouter and the xAI default slugs were left in place
    openrouter_model_frontier: str = Field(
        default="anthropic/claude-sonnet-4", alias="ASDA_OPENROUTER_MODEL_FRONTIER"
    )
    openrouter_model_fast: str = Field(
        default="openai/gpt-4o-mini", alias="ASDA_OPENROUTER_MODEL_FAST"
    )

    apollo_api_key: str = Field(default="", alias="APOLLO_API_KEY")
    signalhire_api_key: str = Field(default="", alias="SIGNALHIRE_API_KEY")
    google_sheets_credentials_json: str = Field(default="", alias="GOOGLE_SHEETS_CREDENTIALS_JSON")

    instantly_api_key: str = Field(default="", alias="INSTANTLY_API_KEY")
    instantly_campaign_id: str = Field(default="", alias="INSTANTLY_CAMPAIGN_ID")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    smtp_reply_to: str = Field(default="", alias="SMTP_REPLY_TO")
    smtp_verified: bool = Field(default=False, alias="ASDA_SMTP_VERIFIED")

    phantombuster_api_key: str = Field(default="", alias="PHANTOMBUSTER_API_KEY")
    phantombuster_session_cookie: str = Field(default="", alias="PHANTOMBUSTER_SESSION_COOKIE")
    pb_connect_agent_id: str = Field(default="", alias="ASDA_PB_CONNECT_AGENT_ID")
    pb_message_agent_id: str = Field(default="", alias="ASDA_PB_MESSAGE_AGENT_ID")
    pb_inbox_agent_id: str = Field(default="", alias="ASDA_PB_INBOX_AGENT_ID")
    pb_search_agent_id: str = Field(default="", alias="ASDA_PB_SEARCH_AGENT_ID")
    bolna_api_key: str = Field(default="", alias="BOLNA_API_KEY")
    bolna_agent_id: str = Field(default="", alias="BOLNA_AGENT_ID")
    bolna_from_number: str = Field(default="", alias="BOLNA_FROM_NUMBER")
    whatsapp_access_token: str = Field(default="", alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str = Field(default="", alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_business_account_id: str = Field(default="", alias="WHATSAPP_BUSINESS_ACCOUNT_ID")
    whatsapp_webhook_verify_token: str = Field(default="", alias="WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    wappfly_api_token: str = Field(default="", alias="WAPPFLY_API_TOKEN")
    wappfly_webhook_secret: str = Field(default="", alias="WAPPFLY_WEBHOOK_SECRET")
    public_base_url: str = Field(default="", alias="ASDA_PUBLIC_BASE_URL")

    hubspot_access_token: str = Field(default="", alias="HUBSPOT_ACCESS_TOKEN")
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")

    email_daily_cap: int = Field(default=80, alias="ASDA_EMAIL_DAILY_CAP")
    linkedin_daily_cap: int = Field(default=15, alias="ASDA_LINKEDIN_DAILY_CAP")
    linkedin_connect_daily_cap: int = Field(default=8, alias="ASDA_LINKEDIN_CONNECT_DAILY_CAP")
    linkedin_connect_weekly_cap: int = Field(default=40, alias="ASDA_LINKEDIN_CONNECT_WEEKLY_CAP")
    # Wappfly uses a WhatsApp Web session, so treat it more conservatively than LinkedIn.
    whatsapp_daily_cap: int = Field(default=5, alias="ASDA_WHATSAPP_DAILY_CAP")
    whatsapp_weekly_cap: int = Field(default=20, alias="ASDA_WHATSAPP_WEEKLY_CAP")

    @property
    def hitl(self) -> set[str]:
        return {s.strip().lower() for s in self.hitl_stages.split(",") if s.strip()}

    @property
    def provider(self) -> str:
        """Resolved LLM backend: openrouter | xai | none."""
        explicit = (self.llm_provider or "auto").strip().lower()
        if explicit in {"openrouter", "or"}:
            return "openrouter"
        if explicit in {"xai", "spacexai", "grok"}:
            return "xai"
        if self.openrouter_api_key:
            return "openrouter"
        if self.xai_api_key:
            return "xai"
        return "none"

    def remap_model(self, name: str, *, which: str = "frontier") -> str:
        """Map a model slug onto the active provider. Bare xAI names become OpenRouter ids."""
        raw = (name or "").strip()
        if self.provider != "openrouter":
            return raw or (self.model_frontier if which == "frontier" else self.model_fast)
        if "/" in raw:
            return raw
        if raw in _XAI_TO_OPENROUTER:
            return _XAI_TO_OPENROUTER[raw]
        fallback = (
            self.openrouter_model_frontier if which == "frontier" else self.openrouter_model_fast
        )
        return fallback

    def resolve_model(self, which: str = "frontier") -> str:
        raw = self.model_frontier if which == "frontier" else self.model_fast
        return self.remap_model(raw, which=which)

    @property
    def offer(self) -> dict[str, Any]:
        return _load_yaml("offer.yaml")

    @property
    def safety(self) -> dict[str, Any]:
        return _load_yaml("safety.yaml")

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        (self.data_dir / "inbox").mkdir(exist_ok=True)
        return self.data_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dir()
    return settings
