"""WhatsApp integrations with draft-first safety."""

from __future__ import annotations

from typing import Any

import httpx

from asda.config import get_settings
from asda.models.lead import Lead
from asda.runtime import load_runtime


class WhatsAppCloudClient:
    """Build approved-template payloads; sending is intentionally disabled by default."""

    base_url = "https://graph.facebook.com/v22.0"

    def __init__(self) -> None:
        settings, runtime = get_settings(), load_runtime()
        self.access_token = runtime.whatsapp_access_token or settings.whatsapp_access_token
        self.phone_number_id = runtime.whatsapp_phone_number_id or settings.whatsapp_phone_number_id

    def configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id)

    def template_payload(self, lead: Lead, template_name: str, body_parameters: list[str]) -> dict[str, Any]:
        if not lead.phone:
            raise ValueError("Lead has no phone number")
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "".join(ch for ch in lead.phone if ch.isdigit()),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in body_parameters]}],
            },
        }

    def send_template(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("WhatsApp sending is disabled until explicit post-review approval.")

    def healthcheck(self) -> dict[str, str]:
        return {"source": "whatsapp_cloud", "status": "configured" if self.configured() else "needs_credentials", "detail": "Draft-only mode; no messages can be sent."}


def get_whatsapp() -> WhatsAppCloudClient:
    return WhatsAppCloudClient()


class WappflyClient:
    """Wappfly WhatsApp Web client. Sending remains deliberately disabled."""

    base_url = "https://wappfly.com/api"

    def __init__(self) -> None:
        settings, runtime = get_settings(), load_runtime()
        self.api_token = runtime.wappfly_api_token or settings.wappfly_api_token

    def configured(self) -> bool:
        return bool(self.api_token)

    def text_payload(self, lead: Lead, text: str) -> dict[str, str]:
        if not lead.phone:
            raise ValueError("Lead has no phone number")
        return {"to": f"{''.join(ch for ch in lead.phone if ch.isdigit())}@s.whatsapp.net", "text": text}

    def send_text(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("WhatsApp sending is disabled until explicit post-review approval.")

    def healthcheck(self) -> dict[str, str]:
        return {"source": "wappfly", "status": "configured" if self.configured() else "needs_credentials", "detail": "Draft-only mode; no messages can be sent."}
