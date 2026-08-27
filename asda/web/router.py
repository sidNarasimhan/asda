from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from asda.agents.employee import talk
from asda.agents.learning import LearningAgent
from asda.agents.orchestrator import Orchestrator
from asda.agents.report import build_brief, email_brief_to_cbo
from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.ingestion.csv_source import CSVSource
from asda.models.lead import LeadQuery
from asda.modules.mail import PROVIDERS, check_imap, check_smtp, resolve_mail_spec
from asda.models.events import EventType
from asda.ops.activity import log
from asda.ops.analytics import capture_snapshot, dashboard_context
from asda.ops.company import save_offer
from asda.ops.hygiene import purge_fake_leads
from asda.ops.onboard import apply_step, ready_to_work, wizard as onboard_wizard
from asda.ops.onboard import prompt as onboard_prompt
from asda.ops.test_run import ingest_mock, run_smoke
from asda.ops.workboard import workboard
from asda.ops.worker import ensure_worker, start_worker, stop_worker, worker_status
from asda.runtime import effective, load_runtime, setup_status, update_runtime

WEB = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB / "templates"))
def _comma(v):
    try:
        n = float(v)
        return f"{int(n):,}" if n == int(n) else f"{n:,.1f}"
    except (TypeError, ValueError):
        return v


templates.env.filters["comma"] = _comma
router = APIRouter()


@router.get("/webhooks/whatsapp")
def whatsapp_webhook_verify(request: Request):
    runtime = load_runtime()
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and runtime.whatsapp_webhook_verify_token and hmac.compare_digest(token, runtime.whatsapp_webhook_verify_token):
        return PlainTextResponse(challenge)
    return PlainTextResponse("Forbidden", status_code=403)


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook_receive(request: Request):
    """Receive WhatsApp replies and pause every remaining follow-up for that lead."""
    payload = await request.json()
    phones: set[str] = set()
    for entry in payload.get("entry", []) if isinstance(payload, dict) else []:
        for change in entry.get("changes", []) if isinstance(entry, dict) else []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            for message in value.get("messages", []) if isinstance(value, dict) else []:
                if isinstance(message, dict) and message.get("from"):
                    phones.add("".join(ch for ch in str(message["from"]) if ch.isdigit()))
    if not phones:
        return JSONResponse({"ok": True, "paused": 0})
    session = get_session(); paused = 0
    try:
        repo = Repository(session)
        for lead in repo.list_leads(limit=20_000):
            lead_phone = "".join(ch for ch in lead.phone if ch.isdigit())
            if lead_phone and lead_phone in phones:
                lead.sequence_state.paused = True
                lead.sequence_state.reason = "WhatsApp reply received"
                repo.save_lead(lead)
                paused += 1
        session.commit()
    finally:
        session.close()
    return JSONResponse({"ok": True, "paused": paused})


@router.post("/webhooks/wappfly/{secret}")
async def wappfly_webhook_receive(secret: str, request: Request):
    """Accept Wappfly's inbound-message webhook and pause matching lead sequences."""
    runtime = load_runtime()
    configured_secret = runtime.wappfly_webhook_secret or get_settings().wappfly_webhook_secret
    if not configured_secret or not hmac.compare_digest(secret, configured_secret):
        return JSONResponse({"ok": False}, status_code=403)
    payload = await request.json()
    key = (((payload.get("data") or {}).get("messages") or {}).get("key") or {}) if isinstance(payload, dict) else {}
    if payload.get("event") != "messages.received" or key.get("fromMe"):
        return JSONResponse({"ok": True, "paused": 0})
    phone = "".join(ch for ch in str(key.get("cleanedSenderPn") or key.get("senderPn") or "") if ch.isdigit())
    if not phone:
        return JSONResponse({"ok": True, "paused": 0})
    session = get_session(); paused = 0
    try:
        repo = Repository(session)
        for lead in repo.list_leads(limit=20_000):
            lead_phone = "".join(ch for ch in lead.phone if ch.isdigit())
            if lead_phone and lead_phone == phone:
                lead.sequence_state.paused = True
                lead.sequence_state.reason = "WhatsApp reply received via Wappfly"
                repo.save_lead(lead)
                paused += 1
        session.commit()
    finally:
        session.close()
    return JSONResponse({"ok": True, "paused": paused})


def _greeting(name: str) -> str:
    hour = datetime.now().hour
    if hour < 12:
        hi = "Good morning"
    elif hour < 17:
        hi = "Good afternoon"
    else:
        hi = "Good evening"
    return f"{hi}, {name}."


def _base(request: Request, nav: str, **extra):
    steps = setup_status()["steps"]
    checks = [
        {
            "ok": bool(steps.get("llm")),
            "label": "Brain (OpenRouter)",
            "hint": "Paste a key in the chat — that's how I research and write",
            "href": "/#ask",
            "required": True,
        },
        {
            "ok": steps["email"],
            "label": "Email",
            "hint": "Work mailbox to send. Gmail if Microsoft will not let us read replies",
            "href": "/settings#email",
            "required": True,
        },
        {
            "ok": steps["linkedin_phantoms"] and steps["linkedin_cookie"],
            "label": "LinkedIn",
            "hint": "ASDA Outreach · weekdays 9–17 IST" if (steps["linkedin_phantoms"] and steps["linkedin_cookie"]) else "One LinkedIn session via PhantomBuster",
            "href": "/settings#linkedin",
            "required": True,
        },
        {
            "ok": steps["live"],
            "label": "Live sending",
            "hint": "Off = practice. On = real people.",
            "href": "/settings#live",
            "required": True,
        },
        {
            "ok": bool(steps.get("apollo")),
            "label": "Apollo search",
            "hint": "Optional. Don't buy a plan unless you want people search — CSV works.",
            "href": "/settings#apollo",
            "required": False,
        },
    ]
    offer = get_settings().offer or {}
    cbo = offer.get("cbo_name") or "there"
    company = offer.get("company_name") or "Altisec"
    return {
        "request": request,
        "nav": nav,
        "title": extra.pop("title", None) or "Home",
        "live": steps["live"],
        "worker": worker_status() | {"enabled": load_runtime().worker_enabled is not False},
        "checks": checks,
        "open_checks": sum(1 for c in checks if c["required"] and not c["ok"]),
        "greeting": _greeting(cbo),
        "cbo": cbo,
        "company": company,
        "flash": request.query_params.get("ok"),
        "error": request.query_params.get("err"),
        **extra,
    }


def render(request: Request, name: str, nav: str, **extra):
    return templates.TemplateResponse(request, name, _base(request, nav, **extra))


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": request.query_params.get("err"), "next": next or "/"},
    )


@router.post("/login")
def login_submit(
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    from asda.runtime import load_runtime

    pwd = os.environ.get("ASDA_SHARE_PASSWORD") or load_runtime().share_password or ""
    if pwd and hmac.compare_digest(username.strip(), "asda") and hmac.compare_digest(password, pwd):
        token = hmac.new(b"asda-share", pwd.encode(), "sha256").hexdigest()[:32]
        dest = next if isinstance(next, str) and next.startswith("/") else "/"
        resp = RedirectResponse(dest, status_code=303)
        resp.set_cookie("asda_share", token, httponly=True, samesite="lax", max_age=14 * 24 * 3600)
        return resp
    return RedirectResponse("/login?err=" + quote("Wrong password."), status_code=303)


def _desk(request: Request, **extra):
    ctx = dashboard_context()
    wst = worker_status()
    board = workboard(wst)
    return render(
        request,
        "home.html",
        "home",
        title="Home",
        metrics=ctx["metrics"],
        events=ctx["events"],
        patterns=ctx["patterns"],
        insight=ctx["insight"],
        playbook=ctx["playbook"],
        conversions=ctx["conversions"],
        wow=ctx["wow"],
        scoreboard=ctx["scoreboard"],
        memories=ctx.get("memories") or {},
        board=board,
        now=board["now"],
        onboard=onboard_prompt(),
        **extra,
    )


@router.get("/")
def home(request: Request):
    if not ready_to_work():
        return RedirectResponse("/onboard", status_code=303)
    if load_runtime().worker_enabled:
        ensure_worker()
    return _desk(request, reply=request.query_params.get("reply"), applied=[])


@router.get("/setup")
def setup_redirect():
    return RedirectResponse("/onboard", status_code=303)


@router.get("/onboard")
def onboard_page(request: Request):
    wiz = onboard_wizard()
    if wiz["ready"] and request.query_params.get("stay") != "1":
        return RedirectResponse("/?ok=You+are+set", status_code=303)
    return templates.TemplateResponse(
        request,
        "onboard.html",
        {
            "request": request,
            "wizard": wiz,
            "flash": request.query_params.get("ok"),
            "error": request.query_params.get("err"),
            "title": "Hire ASDA",
            "mail_guides": PROVIDERS,
        },
    )


@router.post("/onboard")
def onboard_submit(
    step: str = Form(""),
    value: str = Form(""),
    company_name: str = Form(""),
    cbo_name: str = Form(""),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    provider: str = Form("gmail"),
    cookie: str = Form(""),
    cbo_email: str = Form(""),
    openrouter_api_key: str = Form(""),
    phantombuster_api_key: str = Form(""),
    skip: str = Form(""),
):
    result = apply_step(
        step,
        {
            "value": value,
            "skip": skip,
            "company_name": company_name,
            "cbo_name": cbo_name,
            "smtp_user": smtp_user,
            "smtp_password": smtp_password,
            "provider": provider,
            "cookie": cookie,
            "cbo_email": cbo_email,
            "openrouter_api_key": openrouter_api_key,
            "phantombuster_api_key": phantombuster_api_key,
            "bolna_api_key": value,
        },
    )
    if not result["ok"]:
        return RedirectResponse("/onboard?err=" + quote(result["error"] or "Could not save"), status_code=303)
    if result["ready"]:
        return RedirectResponse("/?ok=You+are+set.+Practice+mode+until+you+go+live.", status_code=303)
    return RedirectResponse("/onboard?ok=Saved", status_code=303)


@router.get("/pipeline")
def pipeline(request: Request):
    ctx = dashboard_context()
    wst = worker_status()
    channels = workboard(wst)
    return render(
        request,
        "pipeline.html",
        "pipeline",
        title="Pipeline",
        board=ctx["board"],
        channels=channels,
        now=channels["now"],
    )


@router.get("/leads")
def leads(request: Request):
    from asda.ops.tracker import summary_line, tracker

    q = request.query_params.get("q") or ""
    status = request.query_params.get("status") or ""
    company = request.query_params.get("company") or ""
    group = request.query_params.get("group") or ""
    ch = request.query_params.get("ch") or ""
    sort = request.query_params.get("sort") or "relevance"
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except ValueError:
        page = 1
    per = 50
    init_db()
    session = get_session()
    try:
        repo = Repository(session)
        all_leads = repo.list_leads(
            status=status or None,
            q=q or None,
            company=company or None,
            limit=5000,
        )
        if ch == "email":
            all_leads = [l for l in all_leads if l.email or l.emails]
        elif ch == "linkedin":
            all_leads = [l for l in all_leads if l.linkedin_url]
        elif ch == "phone":
            all_leads = [l for l in all_leads if l.phone]
        elif ch == "dnr":
            all_leads = [l for l in all_leads if "dnr" in (l.tags or [])]
        if sort == "recent":
            # Repository returns most recently updated first.
            pass
        elif sort == "company":
            all_leads.sort(key=lambda l: ((l.company.name or "zzz").lower(), -l.score, l.full_name.lower()))
        else:
            # Altisec default: security decision-makers at substantial companies first.
            all_leads.sort(key=lambda l: (-l.score, l.full_name.lower(), (l.company.name or "").lower()))
        addr = set()
        for lead in all_leads:
            if lead.email:
                addr.add(lead.email.lower())
            for e in lead.emails or []:
                if e:
                    addr.add(e.lower())
        stats = {
            "people": len(all_leads),
            "email": sum(1 for l in all_leads if l.email or l.emails),
            "email_addrs": len(addr),
            "linkedin": sum(1 for l in all_leads if l.linkedin_url),
            "phone": sum(1 for l in all_leads if l.phone),
            "dnr": sum(1 for l in all_leads if "dnr" in (l.tags or [])),
        }
        total = len(all_leads)
        slice_ = all_leads[(page - 1) * per : page * per]
        rows = []
        companies: dict[str, list] = {}
        for lead in all_leads if group == "company" else slice_:
            tr = tracker(lead)
            rows.append({"lead": lead, "tr": tr, "next": summary_line(tr)})
            key = lead.company.name or lead.company.domain or "Unknown"
            companies.setdefault(key, []).append(lead)
        pages = max(1, (total + per - 1) // per)
    finally:
        session.close()
    from asda.ingestion.census import load_report

    census = load_report()
    return render(
        request,
        "leads.html",
        "leads",
        title="Leads",
        rows=rows if group != "company" else [],
        leads=slice_,
        total=total,
        page=page,
        pages=pages,
        q=q,
        status_f=status,
        company_f=company,
        group=group,
        ch=ch,
        sort=sort,
        stats=stats,
        census=census,
        companies=companies if group == "company" else {},
        per=per,
    )


@router.get("/leads/{lead_id}")
def lead_detail(request: Request, lead_id: str):
    init_db()
    session = get_session()
    try:
        repo = Repository(session)
        lead = repo.get_lead(lead_id)
        content = repo.get_content(lead_id) if lead else None
        events = repo.events_for(lead_id) if lead else []
    finally:
        session.close()
    memories = []
    tr = None
    if lead:
        try:
            from asda.memory.store import for_lead
            from asda.ops.tracker import tracker

            memories = for_lead(lead.id, limit=12)
            tr = tracker(lead, content)
        except Exception:
            memories = []
    if not lead:
        return RedirectResponse("/leads?err=not+found", status_code=303)
    return render(
        request,
        "lead.html",
        "leads",
        title=lead.full_name,
        lead=lead,
        content=content,
        events=events,
        memories=memories,
        tracker=tr,
    )


@router.post("/leads/upload")
async def upload_csv(
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
):
    uploads = [f for f in (files or []) if f is not None and (f.filename or "").strip()]
    if file is not None and (file.filename or "").strip():
        uploads.append(file)
    if not uploads:
        return RedirectResponse("/leads?err=" + quote("Drop a CSV or Excel file."), status_code=303)
    from asda.ingestion.pipeline import ingest_path

    settings = get_settings()
    created = deduped = dnr = total = 0
    names: list[str] = []
    dest_dir = settings.data_dir / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for item in uploads:
        raw_name = Path(item.filename or "upload.csv").name
        if Path(raw_name).suffix.lower() not in {".csv", ".tsv", ".xlsx", ".xls"}:
            failed.append(f"{raw_name}: not a CSV or Excel file")
            continue
        dest = dest_dir / f"{uuid4().hex[:8]}-{raw_name}"
        dest.write_bytes(await item.read())
        try:
            result = ingest_path(dest)
        except Exception as exc:
            failed.append(f"{raw_name}: {exc}")
            continue
        created += int(result.get("ingested") or 0)
        deduped += int(result.get("deduped") or 0)
        dnr += int(result.get("dnr") or 0)
        total += int(result.get("total") or 0)
        names.append(raw_name)
    if not names and failed:
        return RedirectResponse("/leads?err=" + quote("Could not read: " + "; ".join(failed[:3])), status_code=303)
    log(
        EventType.LEAD_INGESTED,
        summary=f"Uploaded {total} cleaned leads from {', '.join(names[:6])}",
        count=total,
        created=created,
        dnr=dnr,
    )
    msg = f"Loaded {created} new people ({deduped} already in the book)."
    if dnr:
        msg += f" {dnr} marked DNR, will not be mailed."
    return RedirectResponse("/leads?ok=" + quote(msg), status_code=303)


@router.post("/leads/sample")
def load_sample():
    result = ingest_mock()
    return RedirectResponse(
        f"/leads?ok=Loaded+{result['ingested']}+India+D2C+leads.+No+sends.",
        status_code=303,
    )


@router.post("/leads/purge")
def purge_leads():
    result = purge_fake_leads()
    return RedirectResponse(
        f"/leads?ok=" + quote(f"Removed {result['count']} sample leads. Kept {len(result['kept'])}."),
        status_code=303,
    )


@router.post("/leads/{lead_id}/run")
def run_lead(lead_id: str, mode: str = Form("research")):
    session = get_session()
    try:
        lead = Repository(session).get_lead(lead_id)
    finally:
        session.close()
    if not lead:
        return RedirectResponse("/leads?err=not+found", status_code=303)
    Orchestrator().run(lead, skip_outreach=(mode != "sequence"))
    return RedirectResponse(f"/leads/{lead_id}?ok=Ran", status_code=303)


@router.get("/reports")
def reports(request: Request):
    capture_snapshot()
    ctx = dashboard_context()
    return render(
        request,
        "reports.html",
        "reports",
        title="Reports",
        metrics=ctx["metrics"],
        outcomes=ctx["outcomes"],
        snapshots=ctx["snapshots"],
        funnel=ctx["funnel"],
        conversions=ctx["conversions"],
        wow=ctx["wow"],
        patterns=ctx["patterns"],
        insight=ctx["insight"],
        playbook=ctx["playbook"],
        brief=build_brief(),
        scoreboard=ctx["scoreboard"],
    )


@router.post("/reports/send")
def send_report():
    result = email_brief_to_cbo()
    msg = "Brief emailed" if result.get("status") == "sent" else result.get("reason") or "skipped"
    return RedirectResponse(f"/reports?ok={quote(str(msg))}", status_code=303)


@router.get("/settings")
def settings_page(request: Request):
    rt = load_runtime()
    offer = get_settings().offer or {}
    li = {
        "connected": False,
        "ok": False,
        "cookie": bool(rt.phantombuster_session_cookie),
        "bot": bool(rt.pb_connect_agent_id),
        "agent_name": "",
        "auto_connect": False,
        "last": "",
        "schedule": "Weekdays 9:00–17:00 IST",
        "detail": "Not connected",
    }
    if not os.environ.get("PYTEST_CURRENT_TEST") and (
        rt.pb_connect_agent_id or get_settings().phantombuster_api_key
    ):
        try:
            from asda.modules.phantombuster import PhantomBusterClient

            client = PhantomBusterClient()
            # Do not rewrite PhantomBuster config on every page load.
            if not rt.pb_connect_agent_id and get_settings().phantombuster_api_key:
                client.ensure_linkedin_phantoms()
            li = client.linkedin_status()
        except Exception as exc:
            li["detail"] = str(exc)[:200]
            li["connected"] = bool(rt.phantombuster_session_cookie and rt.pb_connect_agent_id)
    wappfly_secret = rt.wappfly_webhook_secret or get_settings().wappfly_webhook_secret
    wappfly_base_url = rt.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return render(
        request,
        "settings.html",
        "settings",
        title="Settings",
        mail=rt.smtp_user if rt.smtp_verified else "",
        mail_provider=rt.mail_provider or "gmail",
        mail_guides=PROVIDERS,
        mail_imap=rt.imap_verified,
        reply_inbox=rt.imap_user if rt.imap_verified else "",
        reply_to=rt.smtp_reply_to,
        graph=rt.graph_verified,
        graph_user=rt.ms_user,
        ms_client_id=rt.ms_client_id,
        ms_user_code=rt.ms_user_code,
        ms_verify_url=rt.ms_verify_url,
        phantom=rt.pb_connect_agent_id,
        cookie=bool(rt.phantombuster_session_cookie),
        li=li,
        cbo_email=rt.cbo_email or rt.smtp_user or "",
        whatsapp={"configured": bool(rt.wappfly_api_token or get_settings().wappfly_api_token), "callback_url": f"{wappfly_base_url}/webhooks/wappfly/{wappfly_secret}" if wappfly_secret else ""},
        apollo=bool(effective().apollo_key_set),
        keys={
            "openrouter": bool(effective().openrouter_api_key),
            "apollo": bool(effective().apollo_key_set),
            "phantombuster": bool(effective().pb_key_set),
        },
        apollo_info=_apollo_info(),
        targets={
            "outreach": rt.target_outreach,
            "replies": rt.target_replies,
            "meetings": rt.target_meetings,
        },
        offer=offer,
        min_score=rt.min_score if rt.min_score is not None else get_settings().min_score,
        mcp_cmd="asda mcp",
    )


@router.post("/settings/mail")
def settings_mail(provider: str = Form("gmail"), smtp_user: str = Form(""), smtp_password: str = Form("")):
    spec = resolve_mail_spec(provider, smtp_user)
    smtp_ok, smtp_msg = check_smtp(spec["smtp_host"], spec["smtp_port"], smtp_user, smtp_password)
    if not smtp_ok:
        return RedirectResponse(f"/settings?err={quote(str(smtp_msg))}", status_code=303)
    imap_ok, imap_msg = check_imap(spec["imap_host"], spec["imap_port"], smtp_user, smtp_password)
    if not imap_ok and spec["id"] not in {"outlook_work", "outlook"}:
        return RedirectResponse(
            "/settings?err=" + quote("Sending worked but inbox read failed. " + str(imap_msg)),
            status_code=303,
        )
    fields = {
        "mail_provider": spec["id"],
        "smtp_host": spec["smtp_host"],
        "smtp_port": spec["smtp_port"],
        "smtp_user": smtp_user.strip(),
        "smtp_password": smtp_password.replace(" ", ""),
        "smtp_from": smtp_user.strip(),
        "smtp_verified": True,
        "cbo_email": smtp_user.strip() or load_runtime().cbo_email,
    }
    if imap_ok:
        fields.update(
            imap_host=spec["imap_host"],
            imap_user=smtp_user.strip(),
            imap_password=smtp_password.replace(" ", ""),
            imap_verified=True,
        )
    else:
        fields["graph_skipped"] = True
    update_runtime(**fields)
    if not imap_ok:
        return RedirectResponse(
            "/settings?ok="
            + quote(
                "Sending works from this mailbox. Microsoft will not let us read it without an admin. Connect a Gmail reply inbox below."
            )
            + "#replies",
            status_code=303,
        )
    return RedirectResponse("/?ok=Email+connected.+Sending+and+replies+verified.", status_code=303)


@router.post("/settings/reply-inbox")
def settings_reply_inbox(smtp_user: str = Form(""), smtp_password: str = Form("")):
    from asda.modules.mail import connect_reply_inbox

    ok, msg = connect_reply_inbox(smtp_user, smtp_password)
    if not ok:
        return RedirectResponse("/settings?err=" + quote(str(msg)[:240]) + "#replies", status_code=303)
    return RedirectResponse("/settings?ok=" + quote(str(msg)[:240]) + "#replies", status_code=303)


@router.post("/settings/microsoft")
def settings_microsoft_start(client_id: str = Form("")):
    from asda.modules.graph_mail import start_device_login

    result = start_device_login(client_id)
    if not result.get("ok"):
        return RedirectResponse(
            "/settings?err=" + quote((result.get("error") or "Microsoft login failed")[:240]) + "#graph",
            status_code=303,
        )
    return RedirectResponse("/settings#graph", status_code=303)


@router.get("/settings/microsoft/poll")
def settings_microsoft_poll():
    from asda.modules.graph_mail import poll_device_login

    return poll_device_login()


@router.post("/settings/linkedin")
def settings_li(cookie: str = Form("")):
    token = cookie.strip()
    if len(token) < 20:
        return RedirectResponse("/settings?err=Cookie+too+short", status_code=303)
    update_runtime(phantombuster_session_cookie=token)
    return RedirectResponse("/?ok=LinkedIn+saved", status_code=303)


@router.post("/settings/brief")
def settings_brief(cbo_email: str = Form("")):
    update_runtime(cbo_email=cbo_email.strip())
    return RedirectResponse("/?ok=Saved", status_code=303)


@router.post("/settings/whatsapp")
def settings_whatsapp(wappfly_api_token: str = Form("")):
    current = load_runtime()
    update_runtime(
        wappfly_api_token=wappfly_api_token.strip() or current.wappfly_api_token,
        wappfly_webhook_secret=current.wappfly_webhook_secret or get_settings().wappfly_webhook_secret or secrets.token_urlsafe(32),
    )
    return RedirectResponse("/settings?ok=Wappfly+saved+in+draft-only+mode", status_code=303)


@router.post("/settings/live")
def settings_live(live: str | None = Form(None)):
    on = live == "1"
    update_runtime(live_confirmed=on, dry_run=not on)
    return RedirectResponse("/?ok=" + ("Live+on" if on else "Practice+mode"), status_code=303)


@router.post("/ask")
async def ask(
    request: Request,
    message: str = Form(""),
    file: UploadFile | None = File(None),
):
    if load_runtime().worker_enabled:
        ensure_worker()
    applied: list[str] = []
    extra_reply = ""
    if file and file.filename:
        from asda.ops.digest import digest_bytes

        data = await file.read()
        dig = digest_bytes(file.filename, data, note=message)
        if dig.get("kind") == "leads":
            extra_reply = (
                f"Loaded {dig.get('leads', 0)} people from {file.filename}. "
                f"{dig.get('created', 0)} new. Same email, LinkedIn, or phone is one person."
            )
            applied.append(f"ingested {dig.get('leads', 0)} leads")
        elif dig.get("memory"):
            extra_reply = f"Read {file.filename} into memory ({dig.get('chars', 0)} chars)."
            applied.append(f"remembered {file.filename}")
        elif dig.get("error"):
            extra_reply = f"Could not read {file.filename}: {dig['error']}"
        if not message.strip():
            message = f"I uploaded {file.filename}"
    try:
        result = talk(message) if message.strip() else {"reply": extra_reply, "applied": []}
        reply = ((extra_reply + " " if extra_reply else "") + (result.get("reply") or "")).strip()
        applied = applied + (result.get("applied") or [])
        err = None
    except Exception as exc:
        reply = extra_reply
        err = f"I couldn't think just then: {exc}"
    return _desk(request, reply=reply, applied=applied, error=err, asked=message)


@router.post("/test-run")
def test_run():
    result = run_smoke()
    if result.get("ok"):
        email = result.get("email_send") or {}
        li = result.get("linkedin_send") or {}
        msg = (
            f"Tested {result.get('lead')}. "
            f"Email reached={email.get('reached')}. "
            f"LinkedIn reached={li.get('reached')}."
        )
        return RedirectResponse("/?ok=" + quote(msg), status_code=303)
    return RedirectResponse("/?err=" + quote(str(result.get("error") or "test failed")), status_code=303)


def _as_int(raw: str, default: int) -> int:
    try:
        cleaned = str(raw).lower().replace(",", "").replace("k", "000").strip()
        return max(0, int(cleaned or default))
    except ValueError:
        return default


@router.post("/targets")
def save_targets(
    outreach: str = Form("10000"),
    replies: str = Form("400"),
    meetings: str = Form("80"),
):
    update_runtime(
        target_outreach=_as_int(outreach, 10000),
        target_replies=_as_int(replies, 400),
        target_meetings=_as_int(meetings, 80),
    )
    log(
        EventType.CONFIG_UPDATED,
        summary="Updated monthly targets",
        outreach=_as_int(outreach, 10000),
        replies=_as_int(replies, 400),
        meetings=_as_int(meetings, 80),
    )
    return RedirectResponse("/?ok=Targets+saved", status_code=303)


@router.get("/activity")
def activity_page(request: Request):
    ctx = dashboard_context()
    return render(
        request,
        "activity.html",
        "activity",
        title="Activity",
        events=ctx["events"],
    )


@router.post("/worker/start")
def worker_start():
    start_worker()
    return RedirectResponse("/?ok=Employee+started", status_code=303)


@router.post("/worker/stop")
def worker_stop():
    stop_worker()
    return RedirectResponse("/?ok=Employee+stopped", status_code=303)


@router.post("/settings/company")
def settings_company(
    company_name: str = Form(""),
    product_name: str = Form(""),
    website: str = Form(""),
    tagline: str = Form(""),
    cbo_name: str = Form(""),
    cbo_title: str = Form(""),
    value_proposition: str = Form(""),
    tone: str = Form(""),
    call_to_action: str = Form(""),
    icp_titles: str = Form(""),
    icp_industries: str = Form(""),
    icp_geos: str = Form(""),
    min_score: str = Form("55"),
):
    save_offer(
        company_name=company_name,
        product_name=product_name,
        website=website,
        tagline=tagline,
        cbo_name=cbo_name,
        cbo_title=cbo_title,
        value_proposition=value_proposition,
        tone=tone,
        call_to_action=call_to_action,
        icp_titles=icp_titles,
        icp_industries=icp_industries,
        icp_geos=icp_geos,
    )
    update_runtime(min_score=_as_int(min_score, 55))
    log(EventType.CONFIG_UPDATED, summary=f"Updated company profile for {company_name.strip() or 'the book'}")
    return RedirectResponse("/settings?ok=Company+saved", status_code=303)


@router.post("/reset")
def reset_all():
    from asda.ops.reset import reset_book
    from asda.ops.worker import stop_worker

    try:
        stop_worker()
    except Exception:
        pass
    result = reset_book(wipe_runtime=True)
    log(EventType.CONFIG_UPDATED, summary="Reset book. Onboarding starts empty.")
    return RedirectResponse("/?ok=" + quote(f"Cleared {result['n']} things. Talk to me to set up."), status_code=303)


@router.post("/learn")
def learn():
    insight = LearningAgent().run()
    return RedirectResponse("/?ok=" + quote((insight.summary or "Learned")[:180]), status_code=303)


@router.post("/settings/keys")
def settings_keys(
    openrouter_api_key: str = Form(""),
    phantombuster_api_key: str = Form(""),
    apollo_api_key: str = Form(""),
):
    fields: dict = {}
    if openrouter_api_key.strip():
        fields["openrouter_api_key"] = openrouter_api_key.strip()
    if phantombuster_api_key.strip():
        fields["phantombuster_api_key"] = phantombuster_api_key.strip()
    if apollo_api_key.strip():
        fields["apollo_api_key"] = apollo_api_key.strip()
    if fields:
        update_runtime(**fields)
        from asda.llm.client import reset_llm

        reset_llm()
        log(EventType.CONFIG_UPDATED, summary="Saved API keys from Settings")
    return RedirectResponse("/?ok=Keys+saved.+Talk+to+me+if+anything+is+still+missing.", status_code=303)


def _apollo_info() -> dict:
    e = effective()
    if not e.apollo_key_set:
        return {
            "detail": "No key. Optional — upload a CSV instead. Do not buy a plan unless you want People Search.",
            "buy": False,
            "search": False,
        }
    try:
        from asda.ingestion.apollo import ApolloSource

        return ApolloSource().probe()
    except Exception as exc:
        return {"detail": str(exc)[:200], "buy": False, "search": False}
