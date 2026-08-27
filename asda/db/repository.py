from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from asda.db.models import (
    ApprovalRow,
    ContentRow,
    EventRow,
    InsightRow,
    LeadRow,
    PatternRow,
    SnapshotRow,
)
from asda.models.content import GeneratedContent
from asda.models.events import Event
from asda.models.lead import Lead, LeadStatus
from asda.models.outcomes import Pattern


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _match_identity(self, lead: Lead) -> LeadRow | None:
        """Same person across email, LinkedIn handle, and phone+name."""
        from asda.ingestion.cleanup import extract_linkedin

        emails = {e.lower() for e in [lead.email, *(getattr(lead, "emails", None) or [])] if e}
        if emails:
            row = self.session.scalar(select(LeadRow).where(LeadRow.email.in_(list(emails)), LeadRow.email != ""))
            if row:
                return row
        handle = extract_linkedin(lead.linkedin_url)
        if handle:
            row = self.session.scalar(
                select(LeadRow).where(LeadRow.linkedin_url == handle, LeadRow.linkedin_url != "")
            )
            if row:
                return row
            for row in self.session.scalars(select(LeadRow).where(LeadRow.linkedin_url != "")):
                if extract_linkedin(row.linkedin_url) == handle:
                    return row
        phone = "".join(ch for ch in (lead.phone or "") if ch.isdigit())
        incoming_tokens = set((lead.full_name or "").lower().split())
        if phone and len(phone) >= 8:
            for row in self.session.scalars(select(LeadRow).where(LeadRow.phone != "")):
                digits = "".join(ch for ch in (row.phone or "") if ch.isdigit())
                if not digits or not (digits[-10:] == phone[-10:] or digits == phone):
                    continue
                existing = Lead.model_validate(row.payload)
                have = set((existing.full_name or "").lower().split())
                if not incoming_tokens or not have or incoming_tokens & have:
                    return row
        return self.session.scalar(select(LeadRow).where(LeadRow.fingerprint == lead.fingerprint))

    def upsert_lead(self, lead: Lead) -> tuple[Lead, bool]:
        """Insert or merge. Same email, LinkedIn, or phone is one person."""
        existing = self._match_identity(lead)
        now = datetime.now(timezone.utc)
        lead.updated_at = now
        if existing:
            merged = Lead.model_validate(existing.payload)
            merged.raw_data = {**merged.raw_data, **lead.raw_data}
            incoming_emails = [e for e in [lead.email, *(getattr(lead, "emails", None) or [])] if e]
            have_emails = [e for e in [merged.email, *(getattr(merged, "emails", None) or [])] if e]
            merged.emails = list(dict.fromkeys([*have_emails, *incoming_emails]))
            if lead.email and not merged.email:
                merged.email = lead.email
            if merged.emails and not merged.email:
                merged.email = merged.emails[0]
            if lead.linkedin_url and not merged.linkedin_url:
                merged.linkedin_url = lead.linkedin_url
            from asda.ingestion.cleanup import extract_linkedin as _canon_li

            merged.linkedin_url = _canon_li(merged.linkedin_url) or merged.linkedin_url
            if lead.phone and not merged.phone:
                merged.phone = lead.phone
            if lead.title and not merged.title:
                merged.title = lead.title
            if lead.first_name and not merged.first_name:
                merged.first_name = lead.first_name
            if lead.last_name and not merged.last_name:
                merged.last_name = lead.last_name
            from asda.ingestion.cleanup import clean_company, looks_like_address

            incoming_co = clean_company(lead.company.name)
            have_co = (merged.company.name or "").strip()
            if incoming_co and (not have_co or looks_like_address(have_co)):
                merged.company.name = incoming_co
                if lead.company.domain:
                    merged.company.domain = lead.company.domain
            if "dnr" in lead.tags and "dnr" not in merged.tags:
                merged.tags = list(merged.tags) + ["dnr"]
                if merged.status == LeadStatus.NEW:
                    merged.status = LeadStatus.SUPPRESSED
            merged.updated_at = now
            existing.payload = merged.model_dump(mode="json")
            existing.email = merged.email
            existing.phone = merged.phone
            existing.linkedin_url = merged.linkedin_url
            existing.company_name = merged.company.name
            existing.status = merged.status.value
            existing.score = merged.score
            existing.updated_at = now
            return merged, False

        row = LeadRow(
            id=lead.id,
            fingerprint=lead.fingerprint,
            source=lead.source,
            email=lead.email,
            phone=lead.phone,
            linkedin_url=lead.linkedin_url,
            company_name=lead.company.name,
            status=lead.status.value,
            score=lead.score,
            payload=lead.model_dump(mode="json"),
            created_at=lead.created_at,
            updated_at=now,
        )
        self.session.add(row)
        return lead, True

    def save_lead(self, lead: Lead) -> Lead:
        lead.touch()
        row = self.session.get(LeadRow, lead.id)
        if row is None:
            self.upsert_lead(lead)
            return lead
        row.payload = lead.model_dump(mode="json")
        row.status = lead.status.value
        row.score = lead.score
        row.email = lead.email
        row.phone = lead.phone
        row.linkedin_url = lead.linkedin_url
        row.company_name = lead.company.name
        row.updated_at = lead.updated_at
        return lead

    def delete_lead(self, lead_id: str) -> bool:
        row = self.session.get(LeadRow, lead_id)
        if not row:
            return False
        self.session.execute(sql_delete(ContentRow).where(ContentRow.lead_id == lead_id))
        self.session.delete(row)
        return True

    def get_lead(self, lead_id: str) -> Lead | None:
        row = self.session.get(LeadRow, lead_id)
        if not row:
            return None
        return Lead.model_validate(row.payload)

    def list_leads(
        self,
        status: LeadStatus | str | None = None,
        limit: int = 100,
        min_score: int | None = None,
        offset: int = 0,
        q: str | None = None,
        company: str | None = None,
    ) -> list[Lead]:
        # The default desk view is a campaign worklist: show the strongest ICP
        # prospects first, with recently corrected records breaking ties.
        stmt = select(LeadRow).order_by(LeadRow.score.desc(), LeadRow.updated_at.desc())
        if status:
            value = status.value if isinstance(status, LeadStatus) else status
            stmt = stmt.where(LeadRow.status == value)
        if min_score is not None:
            stmt = stmt.where(LeadRow.score >= min_score)
        rows = list(self.session.scalars(stmt.limit(5000)))
        leads = [Lead.model_validate(r.payload) for r in rows]
        if q:
            needle = q.lower().strip()
            leads = [
                l
                for l in leads
                if needle in f"{l.full_name} {l.email} {l.company.name} {l.title} {l.phone} {l.linkedin_url}".lower()
            ]
        if company:
            key = company.lower().strip()
            leads = [
                l
                for l in leads
                if key in (l.company.domain or "").lower() or key in (l.company.name or "").lower()
            ]
        return leads[offset : offset + limit]

    def count_leads(self, status: LeadStatus | str | None = None) -> int:
        stmt = select(LeadRow)
        if status:
            value = status.value if isinstance(status, LeadStatus) else status
            stmt = stmt.where(LeadRow.status == value)
        return len(list(self.session.scalars(stmt)))

    def save_event(self, event: Event) -> None:
        self.session.add(
            EventRow(
                id=event.id,
                type=event.type.value,
                lead_id=event.lead_id,
                actor=event.actor,
                payload=event.payload,
                ts=event.ts,
            )
        )

    def events_for(self, lead_id: str, limit: int = 100) -> list[dict]:
        stmt = (
            select(EventRow)
            .where(EventRow.lead_id == lead_id)
            .order_by(EventRow.ts.desc())
            .limit(limit)
        )
        return [
            {
                "id": r.id,
                "type": r.type,
                "lead_id": r.lead_id,
                "actor": r.actor,
                "payload": r.payload,
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in self.session.scalars(stmt)
        ]

    def save_content(self, lead_id: str, content: GeneratedContent) -> str:
        cid = str(uuid4())
        self.session.add(
            ContentRow(id=cid, lead_id=lead_id, payload=content.model_dump(mode="json"))
        )
        return cid

    def get_content(self, lead_id: str) -> GeneratedContent | None:
        stmt = (
            select(ContentRow)
            .where(ContentRow.lead_id == lead_id)
            .order_by(ContentRow.created_at.desc())
            .limit(1)
        )
        row = self.session.scalars(stmt).first()
        return GeneratedContent.model_validate(row.payload) if row else None

    def request_approval(self, lead_id: str, stage: str, payload: dict) -> str:
        aid = str(uuid4())
        self.session.add(
            ApprovalRow(id=aid, lead_id=lead_id, stage=stage, payload=payload, status="pending")
        )
        return aid

    def pending_approvals(self) -> list[ApprovalRow]:
        stmt = select(ApprovalRow).where(ApprovalRow.status == "pending")
        return list(self.session.scalars(stmt))

    def decide_approval(self, approval_id: str, status: str, decided_by: str) -> ApprovalRow | None:
        row = self.session.get(ApprovalRow, approval_id)
        if not row:
            return None
        row.status = status
        row.decided_by = decided_by
        return row

    def save_patterns(self, patterns: list[Pattern]) -> None:
        for p in patterns:
            self.session.add(
                PatternRow(
                    id=str(uuid4()),
                    kind=p.kind,
                    text=p.text,
                    lift=p.lift,
                    sample_size=p.sample_size,
                    notes=p.notes,
                )
            )

    def winning_patterns(self, limit: int = 20) -> list[Pattern]:
        stmt = select(PatternRow).order_by(PatternRow.lift.desc()).limit(limit)
        return [
            Pattern(
                kind=r.kind,
                text=r.text,
                lift=r.lift,
                sample_size=r.sample_size,
                notes=r.notes,
            )
            for r in self.session.scalars(stmt)
        ]

    def metrics(self) -> dict:
        rows = list(self.session.scalars(select(LeadRow)))
        total = len(rows)
        by_status: dict[str, int] = {}
        meetings = 0
        replies = 0
        scored = 0
        score_sum = 0
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            if r.status == LeadStatus.MEETING_BOOKED.value:
                meetings += 1
            if r.status == LeadStatus.REPLIED.value:
                replies += 1
            if r.score:
                scored += 1
                score_sum += r.score
        return {
            "total_leads": total,
            "by_status": by_status,
            "meetings": meetings,
            "replies": replies,
            "avg_score": round(score_sum / scored, 1) if scored else 0,
            "meetings_per_100": round(meetings / total * 100, 2) if total else 0,
        }

    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for lead in self.list_leads(limit=2000):
            for o in lead.outcomes:
                counts[o.kind] = counts.get(o.kind, 0) + 1
        return counts

    def month_actuals(self, start: datetime, end: datetime) -> dict:
        """This-month activity. Unique people for outreach / replies / meetings."""

        def _aware(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        def _in(dt: datetime | None) -> bool:
            when = _aware(dt)
            if when is None:
                return False
            return start <= when < end

        reached: set[str] = set()
        replied: set[str] = set()
        booked: set[str] = set()
        ev_emails = 0
        ev_li = 0
        oc_emails = 0
        oc_li = 0

        stmt = select(EventRow).where(EventRow.ts >= start, EventRow.ts < end)
        for row in self.session.scalars(stmt):
            lid = row.lead_id or ""
            if row.type in {"email.sent", "linkedin.sent", "linkedin.queued", "email.queued"}:
                if lid:
                    reached.add(lid)
            if row.type == "email.sent":
                ev_emails += 1
            if row.type == "linkedin.sent":
                ev_li += 1
            if row.type in {"reply.received", "reply.classified"} and lid:
                replied.add(lid)
            if row.type == "meeting.booked" and lid:
                booked.add(lid)

        for lead in self.list_leads(limit=5000):
            for o in lead.outcomes:
                if not _in(o.at):
                    continue
                if o.kind in {"email_sent", "email_enqueued"}:
                    oc_emails += 1
                    reached.add(lead.id)
                elif o.kind in {"linkedin_connect", "linkedin_message"}:
                    oc_li += 1
                    reached.add(lead.id)
                elif o.kind in {"reply", "reply_channel"}:
                    replied.add(lead.id)
                elif o.kind in {"meeting_link", "meeting_booked"}:
                    booked.add(lead.id)
            if lead.status is LeadStatus.MEETING_BOOKED and _in(lead.updated_at):
                booked.add(lead.id)
            if lead.status is LeadStatus.REPLIED and _in(lead.updated_at):
                replied.add(lead.id)

        return {
            "outreach": len(reached),
            "emails": max(oc_emails, ev_emails),
            "linkedin": max(oc_li, ev_li),
            "replies": len(replied),
            "meetings": len(booked),
        }

    def recent_events(self, limit: int = 25) -> list[dict]:
        stmt = select(EventRow).order_by(EventRow.ts.desc()).limit(limit)
        rows = list(self.session.scalars(stmt))
        names: dict[str, str] = {}
        ids = {r.lead_id for r in rows if r.lead_id}
        if ids:
            for lead in self.session.scalars(select(LeadRow).where(LeadRow.id.in_(ids))):
                p = lead.payload or {}
                names[lead.id] = (
                    f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                    or lead.company_name
                    or lead.id[:8]
                )
        out = []
        for r in rows:
            payload = r.payload or {}
            who = payload.get("lead_name") or names.get(r.lead_id or "")
            if who:
                payload = {**payload, "lead_name": who}
            out.append(
                {
                    "id": r.id,
                    "type": r.type,
                    "lead_id": r.lead_id,
                    "actor": r.actor,
                    "payload": payload,
                    "ts": r.ts.isoformat() if r.ts else None,
                }
            )
        return out

    def save_snapshot(self, day: str, payload: dict) -> None:
        existing = self.session.scalar(select(SnapshotRow).where(SnapshotRow.day == day))
        if existing:
            existing.payload = payload
            return
        self.session.add(SnapshotRow(id=str(uuid4()), day=day, payload=payload))

    def snapshots(self, limit: int = 14) -> list[dict]:
        stmt = select(SnapshotRow).order_by(SnapshotRow.day.desc()).limit(limit)
        return [{"day": r.day, **(r.payload or {})} for r in self.session.scalars(stmt)]

    def save_insight(self, period: str, payload: dict) -> None:
        self.session.add(InsightRow(id=str(uuid4()), period=period, payload=payload))

    def latest_insight(self) -> dict | None:
        stmt = select(InsightRow).order_by(InsightRow.created_at.desc()).limit(1)
        row = self.session.scalars(stmt).first()
        if not row:
            return None
        return {"period": row.period, **(row.payload or {})}
