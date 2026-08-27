"""Talk-first onboarding. The desk asks for keys in order; paste them in the chat."""

from __future__ import annotations

import re
from typing import Any

from asda.ops.company import patch_offer
from asda.runtime import effective, load_runtime, setup_status, update_runtime

OR_KEY = re.compile(r"sk-or-v1-[A-Za-z0-9._\-]{16,}")
LI_COOKIE = re.compile(r"AQE[A-Za-z0-9_\-]{20,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s]+")
APP_PW = re.compile(r"\b([a-z]{4}\s*){3}[a-z]{4}\b", re.I)
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-=.]{24,}")

STEPS: list[dict[str, Any]] = [
    {
        "id": "who",
        "label": "Who I work for",
        "ask": "What company am I selling for, and what should I call you?",
        "why": "Every mail and LinkedIn note is in this company's voice.",
        "how": [
            "Company name as customers know it.",
            "Your first name, so I can sign off like a person.",
        ],
        "kind": "who",
    },
    {
        "id": "llm",
        "field": "openrouter_api_key",
        "label": "Brain",
        "ask": "Paste an OpenRouter API key. That is how I research people and write.",
        "placeholder": "sk-or-v1-…",
        "why": "Without this I cannot think. One key, any model.",
        "href": "https://openrouter.ai/keys",
        "href_label": "Get a key at openrouter.ai/keys",
        "how": [
            "Open the link. Sign in. Click Create key.",
            "Copy the value. It starts with sk-or-v1-.",
            "Paste it below. I store it on this machine, not in git.",
        ],
        "kind": "secret",
    },
    {
        "id": "email",
        "label": "Mailbox",
        "ask": "Which inbox should I send from, and read replies in?",
        "why": "I send from this mailbox. If Microsoft will not let me read it, we catch replies in Gmail instead.",
        "kind": "email",
        "how": [
            "Pick Google, personal Outlook, or Microsoft 365 work. The steps below change with your pick.",
            "Microsoft 365: send can work with an app password. Reading that same inbox usually needs IT. Skip Graph. Add a Gmail reply inbox after this.",
        ],
    },
    {
        "id": "phantombuster",
        "field": "phantombuster_api_key",
        "label": "PhantomBuster",
        "ask": "Paste your PhantomBuster API key. I use it to send LinkedIn invites and messages.",
        "placeholder": "PhantomBuster API key",
        "why": "LinkedIn is run through PhantomBuster. One key, one LinkedIn login.",
        "href": "https://phantombuster.com",
        "href_label": "Open PhantomBuster",
        "how": [
            "Log into PhantomBuster.",
            "Open API in the account menu and copy the key.",
            "Paste it here.",
        ],
        "kind": "secret",
    },
    {
        "id": "linkedin_cookie",
        "field": "phantombuster_session_cookie",
        "label": "LinkedIn",
        "ask": "Paste your LinkedIn li_at cookie so PhantomBuster can act as you.",
        "placeholder": "li_at value (starts with AQE…)",
        "why": "That cookie is your session. One account. Weekdays 9 to 17 IST.",
        "kind": "cookie",
        "how": [
            "Open Chrome and go to linkedin.com while logged in.",
            "Right click, Inspect, then Application (Chrome) or Storage.",
            "Cookies → https://www.linkedin.com → click li_at.",
            "Copy the long value and paste it below.",
        ],
    },
    {
        "id": "cbo_email",
        "field": "cbo_email",
        "label": "Where I report",
        "ask": "Where should Monday reports and questions I cannot answer go?",
        "placeholder": "you@company.com",
        "why": "If a reply is unclear I email you instead of guessing.",
        "kind": "email_only",
        "how": [
            "Same as the send-from inbox is fine.",
            "Or a different address you actually read.",
        ],
    },
    {
        "id": "bolna",
        "field": "bolna_api_key",
        "label": "Phone (Bolna)",
        "ask": "Paste a Bolna API key if you want last-resort calls after email and LinkedIn go quiet.",
        "placeholder": "bn-…",
        "why": "Ananya, a female assistant, calls only when mail and LinkedIn both fail. Skip if you do not want calls yet.",
        "href": "https://platform.bolna.ai/developers",
        "href_label": "Bolna developers",
        "how": [
            "Optional. Calls never go out first.",
            "Key starts with bn-.",
            "You can skip this and add it later in Settings.",
        ],
        "kind": "secret",
    },
]

OPTIONAL = [
    {
        "id": "apollo",
        "field": "apollo_api_key",
        "label": "Apollo key",
        "ask": "Optional: paste an Apollo key if you want people search. CSV upload works without it. Don't buy a paid plan unless you want me to pull people from Apollo instead of a spreadsheet.",
        "placeholder": "Apollo API key",
        "why": "Free keys authenticate but People Search is locked. Org search and CSV still work.",
    }
]


def _steps_ok() -> dict[str, bool]:
    st = setup_status()["steps"]
    e = effective()
    return {
        "who": bool(load_runtime().who_confirmed),
        "llm": bool(e.openrouter_api_key or e.xai_api_key),
        "email": bool(st.get("email")),
        "phantombuster": bool(e.pb_key_set),
        "linkedin_cookie": bool(st.get("linkedin_cookie")),
        "linkedin_phantoms": bool(st.get("linkedin_phantoms")),
        "cbo_email": bool(e.cbo_email or e.smtp_user),
        "bolna": bool(e.bolna_key_set or load_runtime().bolna_skipped),
        "live": bool(st.get("live")),
        "apollo": bool(e.apollo_key_set),
        "company": bool((e.__dict__ if False else True)),
    }


def ready_to_work() -> bool:
    s = _steps_ok()
    return bool(s["llm"] and s["email"] and s["linkedin_cookie"])


def next_step() -> dict[str, str] | None:
    ok = _steps_ok()
    for step in STEPS:
        if not ok.get(step["id"]):
            return step
    return None


def missing() -> list[dict[str, str]]:
    ok = _steps_ok()
    return [s for s in STEPS if not ok.get(s["id"])]


def _save_mail(user: str, password: str, provider: str = "") -> str | None:
    from asda.modules.mail import check_imap, check_smtp, resolve_mail_spec

    spec = resolve_mail_spec(provider, user)
    smtp_ok, smtp_msg = check_smtp(spec["smtp_host"], spec["smtp_port"], user, password)
    if not smtp_ok:
        return str(smtp_msg)
    imap_ok, imap_msg = check_imap(spec["imap_host"], spec["imap_port"], user, password)
    work_outlook = spec["id"] in {"outlook_work", "outlook"}
    if not imap_ok and not work_outlook:
        return (
            "Sending login worked, but I could not read the inbox. "
            "Without IMAP I cannot see replies. "
            + str(imap_msg)
        )
    rt = load_runtime()
    fields: dict[str, Any] = {
        "mail_provider": spec["id"],
        "smtp_host": spec["smtp_host"],
        "smtp_port": spec["smtp_port"],
        "smtp_user": user.strip(),
        "smtp_password": password.replace(" ", ""),
        "smtp_from": user.strip(),
        "smtp_verified": True,
        "cbo_email": user.strip() if not rt.cbo_email else rt.cbo_email,
    }
    if imap_ok:
        fields.update(
            imap_host=spec["imap_host"],
            imap_user=user.strip(),
            imap_password=password.replace(" ", ""),
            imap_verified=True,
        )
    else:
        fields["graph_skipped"] = True
    update_runtime(**fields)
    return None


def consume(message: str) -> dict[str, Any] | None:
    """Pull keys and mailbox details out of a chat message. None if nothing landed."""
    text = (message or "").strip()
    if not text:
        return None
    applied: list[str] = []
    errors: list[str] = []

    or_key = OR_KEY.search(text)
    if or_key:
        update_runtime(openrouter_api_key=or_key.group(0))
        from asda.llm.client import reset_llm

        reset_llm()
        applied.append("saved OpenRouter key")

    li = LI_COOKIE.search(text.replace(" ", ""))
    if not li:
        li = LI_COOKIE.search(text)
    if li:
        update_runtime(phantombuster_session_cookie=li.group(0))
        applied.append("saved LinkedIn cookie")

    addr = EMAIL.search(text)
    pw_match = APP_PW.search(text)
    if addr and pw_match:
        err = _save_mail(addr.group(0), pw_match.group(0))
        if err:
            errors.append(f"mailbox: {err}")
        else:
            applied.append("connected email")
            if not load_runtime().cbo_email:
                update_runtime(cbo_email=addr.group(0))
                applied.append("reports will go to that inbox (say another address if you want)")

    step = next_step()
    tokens = LONG_TOKEN.findall(text.replace(" ", "")) or LONG_TOKEN.findall(text)
    token = tokens[-1] if tokens else ""

    # Current-step fallback: the whole paste is the secret
    if step and token and step["id"] != "email":
        if step["id"] == "llm" and not or_key and len(token) >= 20:
            update_runtime(openrouter_api_key=token)
            from asda.llm.client import reset_llm

            reset_llm()
            applied.append("saved OpenRouter key")
        elif step["id"] == "phantombuster" and "saved PhantomBuster" not in " ".join(applied):
            if not or_key or token != (or_key.group(0) if or_key else ""):
                update_runtime(phantombuster_api_key=token)
                applied.append("saved PhantomBuster key")
        elif step["id"] == "linkedin_cookie" and not li and len(token) >= 20:
            update_runtime(phantombuster_session_cookie=token)
            applied.append("saved LinkedIn cookie")

    low = text.lower()
    if "apollo" in low and token and not or_key:
        update_runtime(apollo_api_key=token)
        applied.append("saved Apollo key")

    if any(k in low for k in ("go live", "send for real", "live on", "start sending for real")):
        rt = load_runtime()
        if rt.smtp_verified:
            update_runtime(live_confirmed=True, dry_run=False, hitl_stages="")
            applied.append("live sending on")
        else:
            errors.append("connect email before going live")

    if addr and not pw_match:
        step_now = next_step()
        if (step_now and step_now["id"] == "cbo_email") or "report" in low or "brief" in low:
            update_runtime(cbo_email=addr.group(0))
            applied.append("report inbox saved")

    company_m = re.search(r"(?:i work (?:at|for)|company is|we are)\s+([A-Za-z0-9 .&'-]{2,40})", text, re.I)
    if company_m:
        name = company_m.group(1).strip(" .")
        if name:
            patch_offer(company_name=name)
            applied.append(f"company → {name}")

    if not applied and not errors:
        return None

    nxt = next_step()
    if errors and not applied:
        return {
            "reply": " ".join(errors) + (" " + (nxt["ask"] if nxt else "")),
            "applied": [],
            "notes": "onboard",
        }
    if nxt:
        reply = ("Got it. " if applied else "") + nxt["ask"]
    else:
        reply = (
            "You're wired. I can research, write, send mail, and run LinkedIn. "
            "Say “go live” when you want real sends, or start the employee to keep me running 24/7."
        )
    if errors:
        reply = " ".join(errors) + " " + reply
    return {"reply": reply, "applied": applied, "notes": "onboard"}


def apply_step(step_id: str, data: dict[str, str]) -> dict[str, Any]:
    """Save one wizard step. Returns {ok, error, next}."""
    sid = (step_id or "").strip()
    value = (data.get("value") or data.get("message") or "").strip()
    error = ""
    if sid == "who":
        company = (data.get("company_name") or "").strip()
        cbo = (data.get("cbo_name") or "").strip()
        if not company:
            error = "Company name is required."
        else:
            patch_offer(company_name=company, **({"cbo_name": cbo} if cbo else {}))
            update_runtime(who_confirmed=True)
    elif sid == "llm":
        key = value or (data.get("openrouter_api_key") or "").strip()
        if len(key) < 12:
            error = "That does not look like an OpenRouter key."
        else:
            update_runtime(openrouter_api_key=key)
            from asda.llm.client import reset_llm

            reset_llm()
    elif sid == "email":
        user = (data.get("smtp_user") or "").strip()
        password = (data.get("smtp_password") or "").strip()
        if not user or not password:
            error = "I need both the address and the app password."
        else:
            error = _save_mail(user, password, provider=data.get("provider") or "") or ""
    elif sid == "phantombuster":
        key = value or (data.get("phantombuster_api_key") or "").strip()
        if len(key) < 8:
            error = "Paste the PhantomBuster API key."
        else:
            update_runtime(phantombuster_api_key=key)
    elif sid == "linkedin_cookie":
        token = value or (data.get("cookie") or "").strip()
        if len(token) < 20:
            error = "Cookie looks too short. Copy the full li_at value."
        else:
            update_runtime(phantombuster_session_cookie=token)
            try:
                from asda.modules.phantombuster import PhantomBusterClient

                PhantomBusterClient().ensure_linkedin_phantoms()
            except Exception:
                pass
    elif sid == "bolna":
        if (data.get("skip") or "").strip():
            update_runtime(bolna_skipped=True)
            return {"ok": True, "error": "", "next": next_step(), "ready": ready_to_work()}
        key = value or (data.get("bolna_api_key") or "").strip()
        if len(key) < 8:
            error = "Paste the Bolna API key (starts with bn-)."
        else:
            update_runtime(bolna_api_key=key)
            try:
                from asda.modules.bolna import BolnaClient

                health = BolnaClient(key).health()
                if not health.get("ok"):
                    error = health.get("detail") or "Bolna did not accept the key."
            except Exception as exc:
                error = str(exc)[:180]
    elif sid == "cbo_email":
        addr = value or (data.get("cbo_email") or "").strip()
        if not EMAIL.search(addr):
            error = "That does not look like an email address."
        else:
            update_runtime(cbo_email=addr)
    else:
        error = "Unknown step."
    nxt = next_step()
    return {"ok": not error, "error": error, "next": nxt, "ready": ready_to_work()}


def wizard() -> dict[str, Any]:
    ok = _steps_ok()
    nxt = next_step()
    trail = []
    for i, step in enumerate(STEPS, 1):
        trail.append(
            {
                **step,
                "n": i,
                "done": bool(ok.get(step["id"])),
                "current": bool(nxt and nxt["id"] == step["id"]),
            }
        )
    done = sum(1 for t in trail if t["done"])
    if nxt:
        for t in trail:
            if t["id"] == nxt["id"]:
                nxt = {**nxt, "n": t["n"], "kind": t.get("kind"), "how": t.get("how"), "href": t.get("href"), "href_label": t.get("href_label"), "placeholder": t.get("placeholder")}
                break
    return {
        "ready": ready_to_work(),
        "next": nxt,
        "trail": trail,
        "done": done,
        "total": len(STEPS),
        "missing": missing(),
        "steps": ok,
    }


def prompt() -> dict[str, Any]:
    nxt = next_step()
    ok = _steps_ok()
    return {
        "ready": ready_to_work(),
        "next": nxt,
        "missing": missing(),
        "steps": ok,
        "ask": (nxt or {}).get("ask")
        or "You're set. Talk to me like an SDR — pause, change ICP, ask how the week is going.",
        "placeholder": (nxt or {}).get("placeholder") or "Focus Mumbai gifting · pause sending · how’s the week?",
        "intro": (
            "I'm ASDA, your hired SDR. Talk to me. I'll ask for the keys I need, then I run 24/7: "
            "research each person, write unique mail, send, read replies, book meetings, and learn."
            if not ready_to_work()
            else ""
        ),
    }
