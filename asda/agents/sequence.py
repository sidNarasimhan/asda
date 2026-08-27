"""Dual-channel sequence engine: email follow-ups + LinkedIn connect → 3 messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from asda.agents.base import audit
from asda.agents.email_outreach import EmailOutreachAgent
from asda.agents.linkedin_outreach import LinkedInOutreachAgent
from asda.agents.phone_outreach import PhoneOutreachAgent
from asda.agents.reply import ReplyAgent
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent
from asda.models.lead import Lead, LeadStatus
from asda.modules.phantombuster import PhantomBusterClient
from asda.runtime import effective


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due(when: datetime | None) -> bool:
    return when is None or when <= _now()


class SequenceEngine:
    def __init__(self) -> None:
        self.email = EmailOutreachAgent()
        self.linkedin = LinkedInOutreachAgent()
        self.phone = PhoneOutreachAgent()

    def start(self, lead: Lead, content: GeneratedContent) -> tuple[Lead, list[AuditEntry]]:
        """Kick off both channels (first email + connection request)."""
        logs: list[AuditEntry] = []
        st = lead.sequence_state
        st.sequence_id = st.sequence_id or lead.id[:8]
        st.last_touch_at = _now()
        if lead.email and st.email_step == 0:
            lead, elogs = self.email.send_next(lead, content)
            logs.extend(elogs)
        if lead.linkedin_url and st.linkedin_stage in {"", "idle"}:
            lead, llogs = self.linkedin.send_connect(lead, content)
            logs.extend(llogs)
        lead.status = LeadStatus.SEQUENCED
        self._refresh_next_action(lead)
        return lead, logs

    def tick(self, lead: Lead, content: GeneratedContent) -> tuple[Lead, list[AuditEntry]]:
        """Advance whatever is due. Called by the worker."""
        logs: list[AuditEntry] = []
        if lead.sequence_state.paused:
            return lead, [audit("sequence", "paused", lead.sequence_state.reason)]
        if lead.status in {LeadStatus.MEETING_BOOKED, LeadStatus.SUPPRESSED, LeadStatus.CLOSED}:
            return lead, logs

        st = lead.sequence_state
        if lead.email and not st.email_replied and not st.email_dropped and _due(st.next_email_at):
            lead, elogs = self.email.send_next(lead, content)
            logs.extend(elogs)

        if lead.linkedin_url and not st.linkedin_replied and not st.linkedin_dropped:
            if st.linkedin_stage == "delegated":
                pass
            elif st.linkedin_stage == "connect_sent":
                if self._check_accepted(lead):
                    st.linkedin_connected = True
                    st.linkedin_stage = "connected"
                    lead.status = LeadStatus.CONNECTED
                    st.next_linkedin_at = _now()
                    logs.append(audit("sequence", "linkedin_accepted", lead.linkedin_url))
            if (
                st.linkedin_stage in {"connected", "messaging"}
                and _due(st.next_linkedin_at)
                and st.linkedin_messages_sent < max(st.max_linkedin_messages, 1)
            ):
                # Playbook: one message after accept, then wait.
                if st.linkedin_messages_sent < 1:
                    lead, llogs = self.linkedin.send_followup(lead, content)
                    logs.extend(llogs)

        self._maybe_queue_call(lead)
        if st.phone_stage == "queued" and lead.phone and _due(st.next_call_at):
            lead, plogs = self.phone.place(lead)
            logs.extend(plogs)

        self._refresh_next_action(lead)
        if logs:
            from asda.models.events import EventType
            from asda.ops.activity import log

            log(
                EventType.SEQUENCE_STEP,
                lead=lead,
                summary=f"Advanced sequence for {lead.full_name}",
                actions=[a.action for a in logs],
            )
        return lead, logs

    def ingest_reply(
        self,
        lead: Lead,
        text: str,
        channel: str,
        reply_agent: ReplyAgent | None = None,
    ) -> tuple[Lead, Any, list[AuditEntry]]:
        st = lead.sequence_state
        st.last_inbound = text
        st.thread.append({"channel": channel, "role": "them", "text": text, "at": _now().isoformat()})
        if channel == "email":
            st.email_replied = True
            st.linkedin_dropped = True
            st.phone_stage = "skipped"
            st.reason = "conversation on email. LinkedIn and calls stopped."
        elif channel == "linkedin":
            st.linkedin_replied = True
            st.email_dropped = True
            st.phone_stage = "skipped"
            st.reason = "conversation on LinkedIn. Email and calls stopped."
        elif channel == "phone":
            st.phone_stage = "done"
            st.email_dropped = True
            st.linkedin_dropped = True
        agent = reply_agent or ReplyAgent()
        lead, decision, logs = agent.run(lead, _thread_text(st))
        if decision.draft and decision.should_auto_reply:
            st.thread.append(
                {
                    "channel": channel,
                    "role": "us",
                    "text": decision.draft,
                    "at": _now().isoformat(),
                }
            )
        if lead.status not in {
            LeadStatus.MEETING_BOOKED,
            LeadStatus.AWAITING_APPROVAL,
            LeadStatus.SUPPRESSED,
        }:
            lead.status = LeadStatus.REPLIED
        lead.add_outcome("reply_channel", channel)
        return lead, decision, logs

    def _check_accepted(self, lead: Lead) -> bool:
        cfg = effective()
        if cfg.dry_run:
            nxt = lead.sequence_state.next_linkedin_at
            return nxt is not None and _due(nxt)
        if not cfg.pb_connect_agent_id:
            return False
        try:
            rows = PhantomBusterClient().fetch_output_rows(cfg.pb_connect_agent_id)
        except Exception:
            return False
        target = (lead.linkedin_url or "").rstrip("/").lower()
        for row in rows:
            url = str(
                row.get("profileUrl") or row.get("linkedinUrl") or row.get("url") or ""
            ).rstrip("/").lower()
            if target and target not in url and url not in target:
                continue
            blob = " ".join(str(v).lower() for v in row.values())
            if any(w in blob for w in ("accepted", "connected", "1st", "first degree")):
                return True
        return False

    def _maybe_queue_call(self, lead: Lead) -> None:
        st = lead.sequence_state
        if st.email_replied or st.linkedin_replied:
            if st.phone_stage in {"", "idle", "queued"}:
                st.phone_stage = "skipped"
            return
        if not lead.phone:
            return
        if st.phone_stage not in {"", "idle"}:
            return
        if not self._both_channels_failed(lead):
            return
        st.phone_stage = "queued"
        st.next_call_at = _now() + timedelta(days=1)
        from asda.models.events import EventType
        from asda.ops.activity import log

        log(
            EventType.CALL_QUEUED,
            lead=lead,
            summary=f"Queued a call for {lead.full_name} after email and LinkedIn went quiet",
        )

    def _both_channels_failed(self, lead: Lead) -> bool:
        st = lead.sequence_state
        email_done = (not lead.email) or st.email_dropped or st.email_step >= 2
        if lead.email and st.email_step < 2 and not st.email_dropped:
            return False
        if not lead.linkedin_url or st.linkedin_dropped:
            return email_done
        if st.linkedin_stage in {"", "idle"}:
            return False
        if st.linkedin_stage in {"connect_sent", "delegated"}:
            return email_done and _due(st.next_linkedin_at)
        if st.linkedin_messages_sent >= 1:
            return email_done and _due(st.next_linkedin_at)
        return False

    def _refresh_next_action(self, lead: Lead) -> None:
        times = [
            t
            for t in (
                None if lead.sequence_state.email_dropped else lead.sequence_state.next_email_at,
                None if lead.sequence_state.linkedin_dropped else lead.sequence_state.next_linkedin_at,
                lead.sequence_state.next_call_at if lead.sequence_state.phone_stage == "queued" else None,
            )
            if t
        ]
        lead.sequence_state.next_action_at = min(times) if times else None


def _thread_text(st) -> str:
    lines = []
    for m in st.thread[-12:]:
        lines.append(f"{m.get('role','')}/{m.get('channel','')}: {m.get('text','')}")
    return "\n".join(lines) or st.last_inbound
