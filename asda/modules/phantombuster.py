"""PhantomBuster v2 — LinkedIn Outreach.js (current script; old Network Booster is gone)."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from asda.config import get_settings
from asda.models.content import GeneratedContent
from asda.models.lead import Lead
from asda.runtime import effective, update_runtime

logger = logging.getLogger(__name__)

BASE = "https://api.phantombuster.com/api/v2"

# Live-verified on this workspace: LinkedIn Network Booster.js returns 412.
OUTREACH_SCRIPT = "LinkedIn Outreach.js"
OUTREACH_NAME = "ASDA LinkedIn Outreach"

# LinkedIn Auto Connect (slave of Outreach) only fires on this window.
WEEKDAY_HOURS = {
    "day": list(range(1, 32)),
    "dow": ["mon", "tue", "wed", "thu", "fri"],
    "hour": list(range(9, 18)),
    "minute": [0],
    "month": ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
    "timezone": "Asia/Calcutta",
    "simplePreset": "Once per working hour, excluding weekends",
    "isSimplePresetEnabled": True,
}

# From PhantomBuster script 4545709793535249 argumentSchema
OUTREACH_KEYS = {
    "leadsSourceUrl",
    "crmProfileUrlColumnName",
    "columnName",
    "customizeInvite",
    "invitationMessage",
    "firstFollowUp",
    "firstFollowUpTime",
    "followUpMessage",
    "secondFollowUp",
    "secondFollowUpTime",
    "secondFollowUpMessage",
    "thirdFollowUp",
    "thirdFollowUpTime",
    "thirdFollowUpMessage",
    "retrieveLeadsEveryRun",
    "advancedSettings",
    "requestsTime",
    "maxNumberOfConnectionsPerDay",
    "launchTrigger",
    "masterAgentScriptSlug",
    "retrieveOutputFile",
    "useIntentData",
    "debugMessageSending",
    "followUpMessageAttachmentPath",
    "secondFollowUpMessageAttachmentPath",
    "thirdFollowUpMessageAttachmentPath",
    "identityId",
    "sessionCookie",
    "userAgent",
}
FOLLOWUP_TIMES = [
    "1 day",
    "2 days",
    "3 days",
    "4 days",
    "5 days",
    "6 days",
    "7 days",
    "10 days",
    "15 days",
]
FIRST_FOLLOWUP_TIMES = ["0 days", *FOLLOWUP_TIMES]
REQUESTS_TIMES = [
    "Weekdays during working hours",
    "Randomly throughout the day and week",
]
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PhantomBusterError(RuntimeError):
    pass


class PhantomBusterClient:
    def __init__(self, api_key: str | None = None) -> None:
        if api_key:
            self.api_key = api_key
        else:
            try:
                self.api_key = effective().phantombuster_api_key or get_settings().phantombuster_api_key
            except Exception:
                self.api_key = get_settings().phantombuster_api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Phantombuster-Key": self.api_key,
            "X-Phantombuster-Key-1": self.api_key,
            "Content-Type": "application/json",
        }

    def validate(self) -> dict[str, Any]:
        if not self.api_key:
            raise PhantomBusterError("PHANTOMBUSTER_API_KEY is not set")
        with httpx.Client(timeout=20) as client:
            resp = client.get(f"{BASE}/orgs/fetch", headers=self._headers)
            resp.raise_for_status()
            org = resp.json()
        agents = self.list_agents()
        return {
            "ok": True,
            "org": org.get("name"),
            "plan": (org.get("plan") or {}).get("name") or org.get("planSlug"),
            "agent_slots": (org.get("plan") or {}).get("agents"),
            "agent_count": len(agents),
            "agents": agents,
        }

    def list_agents(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=20) as client:
            resp = client.get(f"{BASE}/agents/fetch-all", headers=self._headers)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, list):
            return []
        return [
            {
                "id": str(a.get("id", "")),
                "name": a.get("name") or "",
                "script": a.get("script") or "",
            }
            for a in data
        ]

    def create_agent(self, script: str, name: str) -> dict[str, Any]:
        payload = {
            "org": "phantombuster",
            "script": script,
            "scriptOrgName": "phantombuster",
            "branch": "master",
            "name": name,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{BASE}/agents/save", headers=self._headers, json=payload)
            if resp.status_code >= 400:
                raise PhantomBusterError(
                    f"create {script} failed: {resp.status_code} {resp.text[:400]}"
                )
            return resp.json()

    def ensure_linkedin_phantoms(self) -> dict[str, Any]:
        """Use LinkedIn Outreach.js (connect + 3 follow-ups in one phantom)."""
        if not self.api_key:
            raise PhantomBusterError("PHANTOMBUSTER_API_KEY is not set")
        existing = self.list_agents()
        outreach = [
            a
            for a in existing
            if a["script"] == OUTREACH_SCRIPT or "outreach" in (a["name"] or "").lower()
        ]
        created: list[str] = []
        if outreach:
            agent_id = outreach[0]["id"]
            # Prefer one we named
            for a in outreach:
                if "ASDA" in a["name"]:
                    agent_id = a["id"]
                    break
        else:
            rec = self.create_agent(OUTREACH_SCRIPT, OUTREACH_NAME)
            agent_id = str(rec.get("id") or rec.get("agentId") or "")
            if not agent_id:
                raise PhantomBusterError(f"Phantom created but no id in response: {rec}")
            created.append(OUTREACH_NAME)

        if not agent_id:
            raise PhantomBusterError("Could not resolve LinkedIn Outreach phantom id")

        update_runtime(
            pb_connect_agent_id=agent_id,
            pb_message_agent_id=agent_id,
            pb_inbox_agent_id=agent_id,
        )
        # Never rewrite argument here. A cookie-only save is what PB calls
        # "invalid configuration" (required leadsSourceUrl).
        self._sync_cookie_if_safe(agent_id)
        return {
            "ok": True,
            "script": OUTREACH_SCRIPT,
            "agent_id": agent_id,
            "created": created,
            "existing": existing,
            "note": "One phantom handles invite + up to 3 LinkedIn follow-ups.",
        }

    def _parse_argument(self, raw: Any) -> dict[str, Any]:
        cur = raw
        for _ in range(4):
            if isinstance(cur, dict):
                return dict(cur)
            if isinstance(cur, str) and cur.strip():
                try:
                    cur = json.loads(cur)
                except json.JSONDecodeError:
                    return {}
                continue
            break
        return cur if isinstance(cur, dict) else {}

    def sanitize_outreach_argument(
        self,
        argument: dict[str, Any],
        *,
        cookie: str | None = None,
        leads_url: str | None = None,
    ) -> dict[str, Any]:
        """Keep only schema fields and satisfy Outreach.js required keys."""
        out: dict[str, Any] = {}
        for key, value in (argument or {}).items():
            if key in OUTREACH_KEYS and value not in (None, ""):
                out[key] = value
        if leads_url:
            out["leadsSourceUrl"] = leads_url
        if cookie:
            out["sessionCookie"] = cookie
        if not out.get("sessionCookie"):
            out["sessionCookie"] = effective().pb_cookie
        if not out.get("userAgent"):
            out["userAgent"] = DEFAULT_UA
        note = str(out.get("invitationMessage") or "")[:300]
        out["invitationMessage"] = note
        out["customizeInvite"] = bool(note)
        if not out.get("followUpMessage"):
            out["firstFollowUp"] = False
            out.pop("followUpMessage", None)
            out.pop("secondFollowUp", None)
            out.pop("secondFollowUpMessage", None)
            out.pop("thirdFollowUp", None)
            out.pop("thirdFollowUpMessage", None)
        else:
            out["firstFollowUp"] = True
            out["followUpMessage"] = str(out["followUpMessage"])[:8000]
        if not out.get("secondFollowUpMessage"):
            out["secondFollowUp"] = False
            out.pop("secondFollowUpMessage", None)
            out.pop("thirdFollowUp", None)
            out.pop("thirdFollowUpMessage", None)
        if not out.get("thirdFollowUpMessage"):
            out["thirdFollowUp"] = False
            out.pop("thirdFollowUpMessage", None)
        t1 = out.get("firstFollowUpTime") or "2 days"
        out["firstFollowUpTime"] = t1 if t1 in FIRST_FOLLOWUP_TIMES else "2 days"
        t2 = out.get("secondFollowUpTime") or "3 days"
        out["secondFollowUpTime"] = t2 if t2 in FOLLOWUP_TIMES else "3 days"
        t3 = out.get("thirdFollowUpTime") or "3 days"
        out["thirdFollowUpTime"] = t3 if t3 in FOLLOWUP_TIMES else "3 days"
        req = out.get("requestsTime") or REQUESTS_TIMES[0]
        out["requestsTime"] = req if req in REQUESTS_TIMES else REQUESTS_TIMES[0]
        try:
            cap = int(out.get("maxNumberOfConnectionsPerDay") or 8)
        except (TypeError, ValueError):
            cap = 8
        # Hard ceiling. LinkedIn flags accounts that spray invites.
        out["maxNumberOfConnectionsPerDay"] = max(1, min(8, cap))
        out["retrieveLeadsEveryRun"] = bool(out.get("retrieveLeadsEveryRun", True))
        if out.get("columnName") and str(out.get("leadsSourceUrl") or "").startswith("https://www.linkedin.com/in/"):
            # Single profile URL — column name is for spreadsheets only
            out.pop("columnName", None)
        if not out.get("leadsSourceUrl"):
            raise PhantomBusterError("LinkedIn Outreach requires leadsSourceUrl (profile or CSV URL)")
        if len(str(out.get("sessionCookie") or "")) < 15:
            raise PhantomBusterError("LinkedIn session cookie is missing")
        return out

    def _save_argument(self, agent_id: str, argument: dict[str, Any], *, info: dict | None = None) -> None:
        clean = self.sanitize_outreach_argument(argument)
        # PhantomBuster stores argument as a JSON string. Passing a dict sometimes
        # round-trips; passing dumps of dumps makes the UI show "invalid configuration".
        payload: dict[str, Any] = {
            "id": agent_id,
            "argument": json.dumps(clean),
        }
        if info:
            payload["name"] = info.get("name") or OUTREACH_NAME
            payload["script"] = info.get("script") or OUTREACH_SCRIPT
            payload["scriptOrgName"] = info.get("scriptOrgName") or "phantombuster"
            payload["branch"] = info.get("branch") or "master"
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{BASE}/agents/save", headers=self._headers, json=payload)
            if resp.status_code >= 400:
                raise PhantomBusterError(f"save argument failed: {resp.status_code} {resp.text[:400]}")

    def _sync_cookie_if_safe(self, agent_id: str) -> None:
        """Update li_at only when the saved config already has a lead source."""
        try:
            info = self.fetch_agent(agent_id)
        except Exception:
            logger.exception("fetch for cookie sync failed")
            return
        arg = self._parse_argument(info.get("argument"))
        cookie = effective().pb_cookie
        if not cookie or not arg.get("leadsSourceUrl"):
            return
        if arg.get("sessionCookie") == cookie and arg.get("userAgent"):
            return
        arg["sessionCookie"] = cookie
        try:
            self._save_argument(agent_id, arg, info=info)
        except PhantomBusterError:
            logger.exception("cookie sync skipped — would write invalid config")

    def repair_outreach_config(self) -> dict[str, Any]:
        """Rewrite ASDA Outreach with a schema-valid argument. Does not launch."""
        cfg = effective()
        if not cfg.pb_connect_agent_id:
            raise PhantomBusterError("no outreach agent id")
        info = self.fetch_agent(cfg.pb_connect_agent_id)
        arg = self._parse_argument(info.get("argument"))
        clean = self.sanitize_outreach_argument(arg, cookie=cfg.pb_cookie)
        self._save_argument(cfg.pb_connect_agent_id, clean, info=info)
        check = self._parse_argument(self.fetch_agent(cfg.pb_connect_agent_id).get("argument"))
        return {
            "ok": bool(check.get("leadsSourceUrl") and check.get("sessionCookie")),
            "has_leads": bool(check.get("leadsSourceUrl")),
            "has_cookie": bool(check.get("sessionCookie")),
            "has_ua": bool(check.get("userAgent")),
        }

    def launch(self, agent_id: str, argument: dict[str, Any]) -> dict[str, Any]:
        if not agent_id:
            raise PhantomBusterError("Phantom agent id is empty — LinkedIn bot is not connected")
        existing: dict[str, Any] = {}
        info: dict[str, Any] | None = None
        try:
            info = self.fetch_agent(agent_id)
            existing = self._parse_argument(info.get("argument"))
        except Exception:
            logger.exception("fetch before launch failed")
        merged = {**existing, **{k: v for k, v in (argument or {}).items() if v not in (None, "")}}
        clean = self.sanitize_outreach_argument(merged)
        self._save_argument(agent_id, clean, info=info)
        with httpx.Client(timeout=30) as client:
            payload = {"id": agent_id, "argument": clean, "manualLaunch": True}
            resp = client.post(f"{BASE}/agents/launch", headers=self._headers, json=payload)
            if resp.status_code >= 400:
                raise PhantomBusterError(f"launch failed: {resp.status_code} {resp.text[:400]}")
            return resp.json()

    def linkedin_status(self) -> dict[str, Any]:
        """Honest connection state for Settings — never echo the cookie."""
        cfg = effective()
        agents = []
        try:
            if cfg.pb_key_set:
                agents = self.list_agents()
        except Exception as exc:
            return {
                "connected": False,
                "cookie": bool(cfg.pb_cookie),
                "bot": bool(cfg.pb_connect_agent_id),
                "ok": False,
                "detail": f"PhantomBuster unreachable: {exc}",
                "schedule": "Weekdays 9:00–17:00 IST",
            }
        outreach = next(
            (a for a in agents if a.get("id") == cfg.pb_connect_agent_id),
            None,
        )
        if not outreach:
            outreach = next((a for a in agents if a.get("script") == OUTREACH_SCRIPT and "ASDA" in (a.get("name") or "")), None)
        auto = next((a for a in agents if a.get("script") == "LinkedIn Auto Connect.js"), None)
        last = ""
        last_ok = None
        if cfg.pb_connect_agent_id:
            try:
                info = self.fetch_agent(cfg.pb_connect_agent_id)
                arg = self._parse_argument(info.get("argument"))
                last = str(info.get("lastEndType") or "")
                last_ok = bool(arg.get("leadsSourceUrl") and arg.get("sessionCookie"))
                if last_ok:
                    last = "config valid" + (f" · last run {last}" if last else "")
                else:
                    last = "invalid configuration — missing lead source or cookie"
            except Exception:
                last = "could not read last run"
        connected = bool(cfg.pb_key_set and cfg.pb_cookie and (cfg.pb_connect_agent_id or outreach))
        return {
            "connected": connected,
            "cookie": bool(cfg.pb_cookie),
            "bot": bool(cfg.pb_connect_agent_id or outreach),
            "ok": connected and bool(outreach),
            "agent_name": (outreach or {}).get("name") or "ASDA Outreach",
            "script": (outreach or {}).get("script") or OUTREACH_SCRIPT,
            "auto_connect": bool(auto),
            "last": last,
            "last_ok": last_ok,
            "schedule": "Weekdays 9:00–17:00 IST",
            "detail": (
                "Invites go out weekdays 9:00–17:00 IST via LinkedIn Auto Connect. One LinkedIn session."
                if connected
                else "Not connected"
            ),
        }

    def fetch_agent(self, agent_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                f"{BASE}/agents/fetch",
                headers=self._headers,
                params={"id": agent_id},
            )
            resp.raise_for_status()
            return resp.json()

    def fetch_output_rows(self, agent_id: str) -> list[dict[str, Any]]:
        try:
            info = self.fetch_agent(agent_id)
        except Exception:
            logger.exception("fetch agent %s failed", agent_id)
            return []
        for key in ("resultObject", "output", "data"):
            blob = info.get(key)
            if isinstance(blob, str):
                try:
                    blob = json.loads(blob)
                except Exception:
                    continue
            if isinstance(blob, list):
                return [x for x in blob if isinstance(x, dict)]
            if isinstance(blob, dict) and isinstance(blob.get("rows"), list):
                return blob["rows"]
        return []


def write_lead_csv(lead: Lead) -> Path:
    settings = get_settings()
    folder = settings.data_dir / "pb_feeds"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lead.id}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["profileUrl", "firstName", "lastName", "company"])
        w.writeheader()
        w.writerow(
            {
                "profileUrl": lead.linkedin_url,
                "firstName": lead.first_name,
                "lastName": lead.last_name,
                "company": lead.company.name,
            }
        )
    return path


def outreach_argument(
    lead: Lead,
    content: GeneratedContent,
    cookie: str,
    leads_url: str,
) -> dict[str, Any]:
    msgs = [m for m in content.linkedin.messages if m.body]
    note = (content.linkedin.connection_note or "")[:300]
    return {
        "leadsSourceUrl": leads_url,
        "columnName": "profileUrl",
        "sessionCookie": cookie,
        "customizeInvite": bool(note),
        "invitationMessage": note,
        "firstFollowUp": len(msgs) >= 1,
        "firstFollowUpTime": "2 days",
        "followUpMessage": msgs[0].body if msgs else "",
        "secondFollowUp": len(msgs) >= 2,
        "secondFollowUpTime": "3 days",
        "secondFollowUpMessage": msgs[1].body if len(msgs) > 1 else "",
        "thirdFollowUp": len(msgs) >= 3,
        "thirdFollowUpTime": "3 days",
        "thirdFollowUpMessage": msgs[2].body if len(msgs) > 2 else "",
        "maxNumberOfConnectionsPerDay": 8,
        "requestsTime": "Weekdays during working hours",
        "retrieveLeadsEveryRun": True,
    }


# Back-compat names used by older launch helpers
def connect_argument(profile_url: str, note: str, cookie: str) -> dict[str, Any]:
    return {
        "sessionCookie": cookie,
        "leadsSourceUrl": profile_url,
        "columnName": "profileUrl",
        "customizeInvite": True,
        "invitationMessage": (note or "")[:300],
        "maxNumberOfConnectionsPerDay": 8,
    }


def message_argument(profile_url: str, body: str, cookie: str) -> dict[str, Any]:
    return connect_argument(profile_url, body, cookie)
