"""Setup page — must never crash the rest of the app."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import yaml

from asda.config import get_settings
from asda.modules.mail import PROVIDERS, check_imap, check_smtp
from asda.modules.phantombuster import PhantomBusterClient, PhantomBusterError
from asda.runtime import load_runtime, setup_status, update_runtime

ROOT = Path(__file__).resolve().parents[2]

GOOGLE_STEPS = [
    "Open the Google account you send sales mail from.",
    "Turn on 2-Step Verification: https://myaccount.google.com/security",
    "Create a one-time code (not your normal password): https://myaccount.google.com/apppasswords — name it ASDA, click Create.",
    "Google shows 16 characters like xxxx xxxx xxxx xxxx. Copy that. Paste it below.",
]
MICROSOFT_STEPS = [
    "Open the Outlook / Microsoft 365 inbox you send sales mail from.",
    "Try your normal email + password first.",
    "If it fails (Authenticator): https://account.microsoft.com/security → App passwords → create ASDA. Personal Hotmail: https://account.live.com/proofs/AppPassword",
]


def _pill(ok: bool, label: str) -> str:
    return f"{'✅' if ok else '○'} {label}"


def render_setup() -> None:
    try:
        _render()
    except Exception as exc:
        st.error("Setup hit an error. Your other pages still work.")
        st.exception(exc)


def _render() -> None:
    settings = get_settings()
    rt = load_runtime()
    steps = setup_status()["steps"]
    offer = settings.offer or {}

    st.title("Connect")
    st.write("Google **or** Outlook for mail. LinkedIn cookie for outreach. Instantly is not used.")

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_pill(steps.get("email"), "Email"))
    c2.markdown(_pill(steps.get("linkedin_phantoms"), "LinkedIn bot"))
    c3.markdown(_pill(steps.get("linkedin_cookie"), "LinkedIn login"))
    c4.markdown(_pill(steps.get("live"), "Live sending"))

    st.divider()
    with st.expander("Company profile", expanded=False):
        company = st.text_input("Company", offer.get("company_name") or "", key="setup_co")
        cbo = st.text_input("Your name", offer.get("cbo_name") or "", key="setup_cbo")
        if st.button("Save company", key="setup_save_co"):
            path = ROOT / "config" / "offer.yaml"
            data = yaml.safe_load(path.read_text()) or {}
            data.update({"company_name": company, "cbo_name": cbo})
            path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
            st.success("Saved.")

    st.subheader("Email")
    st.write("Which inbox sends your sales email? Pick **one**.")
    mail_keys = list(PROVIDERS.keys())
    default_ix = mail_keys.index(rt.mail_provider) if rt.mail_provider in PROVIDERS else 0
    provider = st.radio(
        "Inbox",
        options=mail_keys,
        format_func=lambda k: PROVIDERS[k]["label"],
        index=default_ix,
        horizontal=True,
        key="setup_provider",
    )
    spec = PROVIDERS.get(provider) or PROVIDERS["gmail"]
    how = spec.get("how") or (GOOGLE_STEPS if provider == "gmail" else MICROSOFT_STEPS)
    for i, line in enumerate(how, 1):
        st.markdown(f"{i}. {line}")

    smtp_user = st.text_input("Email address", rt.smtp_user, key="setup_smtp_user")
    smtp_password = st.text_input(
        spec.get("password_label") or "Password / 16-character code",
        value="",
        type="password",
        key="setup_smtp_pass",
        help="Gmail: the 16-character code, not your normal password.",
    )
    if st.button("Connect email", type="primary", key="setup_connect_mail"):
        pwd = smtp_password or rt.smtp_password
        smtp_ok, smtp_msg = check_smtp(spec["smtp_host"], spec["smtp_port"], smtp_user, pwd)
        imap_ok, imap_msg = check_imap(spec["imap_host"], spec["imap_port"], smtp_user, pwd)
        if smtp_ok:
            update_runtime(
                mail_provider=provider,
                smtp_host=spec["smtp_host"],
                smtp_port=spec["smtp_port"],
                smtp_user=smtp_user.strip(),
                smtp_password=pwd.replace(" ", ""),
                smtp_from=smtp_user.strip(),
                imap_host=spec["imap_host"],
                imap_port=spec["imap_port"],
                imap_user=smtp_user.strip(),
                imap_password=pwd.replace(" ", ""),
                smtp_verified=True,
                imap_verified=imap_ok,
            )
            st.success("Sending works. " + smtp_msg)
            if imap_ok:
                st.success("Reading replies works. " + imap_msg)
            else:
                st.warning("Sending works, reading replies does not. " + imap_msg)
        else:
            update_runtime(smtp_verified=False)
            st.error(smtp_msg)

    if load_runtime().smtp_verified:
        st.success(f"Email connected as {load_runtime().smtp_user}")

    st.divider()
    st.subheader("LinkedIn")
    st.markdown(
        "The bot is already in PhantomBuster. We only need the login cookie from **your** Chrome session.\n\n"
        "1. Open [linkedin.com](https://www.linkedin.com) and log in.\n"
        "2. Press **F12** (Mac: **Cmd + Option + I**).\n"
        "3. Click **Application** (or **>>** then Application).\n"
        "4. Left: **Cookies** → `https://www.linkedin.com`.\n"
        "5. Find **`li_at`**. Copy the long **Value**.\n"
        "6. Paste it in the box. Click Save."
    )
    if rt.pb_connect_agent_id:
        st.success("LinkedIn bot is ready.")
    else:
        if st.button("Create LinkedIn bot", key="setup_pb_create"):
            try:
                result = PhantomBusterClient().ensure_linkedin_phantoms()
                st.success(f"Bot ready ({result.get('agent_id')})")
            except PhantomBusterError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(str(exc))

    if rt.phantombuster_session_cookie:
        st.success("LinkedIn login is saved.")
    cookie = st.text_area(
        "Paste li_at here",
        value="",
        height=80,
        key="setup_li_cookie",
        placeholder="AQED… (long string)",
    )
    if st.button("Save LinkedIn", key="setup_li_save"):
        token = (cookie or "").strip()
        if len(token) < 20:
            st.error("Paste the long Value from li_at, not the word li_at.")
        else:
            update_runtime(phantombuster_session_cookie=token)
            st.success("LinkedIn login saved.")
            st.rerun()

    st.divider()
    st.subheader("Go live")
    live = st.checkbox(
        "Send real email and LinkedIn to real people",
        value=bool(rt.live_confirmed),
        key="setup_live",
    )
    if st.button("Save live setting", key="setup_live_save"):
        update_runtime(live_confirmed=live, dry_run=not live)
        st.success("Live — sending on." if live else "Practice mode — nothing is sent.")
