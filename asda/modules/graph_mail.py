"""Microsoft Graph for inbox when IMAP basic auth is blocked.

Device-code login: you register a public-client app yourself (no IT ticket if
the tenant still allows user app registrations), then sign in as the mailbox.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from asda.runtime import load_runtime, update_runtime

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
# /.default asks for whatever delegated Graph perms are on the app registration.
# Bare User.Read makes Microsoft look up a fake resource https://graph.microsoft.com/User.Read (AADSTS500011).
SCOPES = "openid profile offline_access https://graph.microsoft.com/.default"


def _tenant(rt=None) -> str:
    """Single-tenant apps fail on /organizations. Use the mailbox domain."""
    rt = rt or load_runtime()
    explicit = (getattr(rt, "ms_tenant", "") or "").strip()
    if explicit:
        return explicit
    for raw in (rt.smtp_user, rt.smtp_from, rt.imap_user, rt.ms_user):
        if raw and "@" in raw:
            domain = raw.split("@", 1)[1].strip().lower()
            if domain:
                return domain
    return "organizations"


def _authority(rt=None) -> str:
    return f"https://login.microsoftonline.com/{_tenant(rt)}"


def _ms_error(text: str) -> str:
    raw = (text or "").strip()
    try:
        data = json.loads(raw)
        code = (data.get("error") or "").strip()
        desc = (data.get("error_description") or "").split("Trace ID:")[0].strip()
    except Exception:
        data, code, desc = {}, "", raw
    blob = f"{code} {desc}"
    if "AADSTS50059" in blob:
        return "Microsoft could not find your company from the app ID. ASDA now uses your mailbox domain. Try again."
    if "AADSTS700016" in blob:
        return "That Application (client) ID is not in this Microsoft tenant. Copy it from the ASDA app Overview page in Azure."
    if "unauthorized_client" in blob or "AADSTS7000218" in blob:
        return "In Azure, open the app → Authentication → Allow public client flows → Yes, then try again."
    if "AADSTS500011" in blob:
        return "Microsoft Graph is not on this app yet. In Azure: API permissions → Add a permission → Microsoft Graph → Delegated → User.Read, Mail.Read, Mail.Send."
    return (desc or code or raw or "Microsoft login failed")[:240]


def start_device_login(client_id: str) -> dict[str, Any]:
    cid = (client_id or "").strip()
    if len(cid) < 10:
        return {"ok": False, "error": "Paste the Application (client) ID from the app you registered."}
    tenant = _tenant()
    with httpx.Client(timeout=20) as client:
        r = client.post(
            f"{_authority()}/oauth2/v2.0/devicecode",
            data={"client_id": cid, "scope": SCOPES},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code >= 400:
        return {"ok": False, "error": _ms_error(r.text)}
    body = r.json()
    update_runtime(
        ms_client_id=cid,
        ms_tenant=tenant,
        ms_device_code=body.get("device_code") or "",
        ms_user_code=body.get("user_code") or "",
        ms_verify_url=body.get("verification_uri") or "https://microsoft.com/devicelogin",
        graph_verified=False,
    )
    return {
        "ok": True,
        "user_code": body.get("user_code"),
        "verify_url": body.get("verification_uri") or "https://microsoft.com/devicelogin",
        "message": body.get("message"),
        "interval": int(body.get("interval") or 5),
    }


def poll_device_login() -> dict[str, Any]:
    rt = load_runtime()
    if not rt.ms_client_id or not rt.ms_device_code:
        return {"ok": False, "status": "idle", "error": "Start Microsoft sign-in first."}
    with httpx.Client(timeout=20) as client:
        r = client.post(
            f"{_authority(rt)}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": rt.ms_client_id,
                "device_code": rt.ms_device_code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    data = r.json()
    if r.status_code >= 400:
        err = data.get("error") or ""
        if err in {"authorization_pending", "slow_down"}:
            return {"ok": True, "status": "pending", "user_code": rt.ms_user_code, "verify_url": rt.ms_verify_url}
        if err == "expired_token":
            update_runtime(ms_device_code="", ms_user_code="", ms_verify_url="")
            return {"ok": False, "status": "expired", "error": "Code expired. Start sign-in again."}
        return {"ok": False, "status": "error", "error": data.get("error_description") or err or r.text[:300]}
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in") or 3600))
    update_runtime(
        ms_access_token=data.get("access_token") or "",
        ms_refresh_token=data.get("refresh_token") or rt.ms_refresh_token,
        ms_token_expiry=expires.isoformat(),
        ms_device_code="",
        ms_user_code="",
        ms_verify_url="",
        graph_verified=True,
    )
    who = _whoami(data.get("access_token") or "")
    if who:
        update_runtime(ms_user=who)
    return {"ok": True, "status": "connected", "user": who}


def _whoami(token: str) -> str:
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 400:
            return ""
        body = r.json()
        return (body.get("mail") or body.get("userPrincipalName") or "").strip()
    except Exception:
        return ""


def _token() -> str:
    rt = load_runtime()
    if not rt.ms_refresh_token and not rt.ms_access_token:
        return ""
    exp = None
    if rt.ms_token_expiry:
        try:
            exp = datetime.fromisoformat(rt.ms_token_expiry.replace("Z", "+00:00"))
        except ValueError:
            exp = None
    now = datetime.now(timezone.utc)
    if rt.ms_access_token and exp and exp - now > timedelta(minutes=2):
        return rt.ms_access_token
    if not rt.ms_refresh_token:
        return rt.ms_access_token
    with httpx.Client(timeout=20) as client:
        r = client.post(
            f"{_authority(rt)}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": rt.ms_client_id,
                "refresh_token": rt.ms_refresh_token,
                "scope": SCOPES,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code >= 400:
        logger.warning("graph refresh failed: %s", r.text[:200])
        return ""
    data = r.json()
    expires = now + timedelta(seconds=int(data.get("expires_in") or 3600))
    update_runtime(
        ms_access_token=data.get("access_token") or "",
        ms_refresh_token=data.get("refresh_token") or rt.ms_refresh_token,
        ms_token_expiry=expires.isoformat(),
        graph_verified=True,
    )
    return data.get("access_token") or ""


def fetch_unseen(limit: int = 30) -> list[dict]:
    token = _token()
    if not token:
        return []
    params = {
        "$top": str(min(limit, 50)),
        "$orderby": "receivedDateTime desc",
        "$select": "from,subject,bodyPreview,isRead,receivedDateTime",
        "$filter": "isRead eq false",
    }
    with httpx.Client(timeout=25) as client:
        r = client.get(
            f"{GRAPH}/me/mailFolders/inbox/messages",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
    if r.status_code >= 400:
        logger.warning("graph inbox %s: %s", r.status_code, r.text[:200])
        return []
    out = []
    for item in r.json().get("value") or []:
        frm = ((item.get("from") or {}).get("emailAddress") or {}).get("address") or ""
        out.append(
            {
                "from": frm.strip().lower(),
                "subject": item.get("subject") or "",
                "body": (item.get("bodyPreview") or "")[:4000],
            }
        )
    return out
