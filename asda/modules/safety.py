"""Throttles, daily caps, bounce/complaint auto-pause.

LinkedIn connection requests are capped hard. LinkedIn flags accounts that
spray invites. We would rather sit idle than burn the profile.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from asda.config import get_settings
from asda.db.models import EventRow, SafetyCounterRow
from asda.db.session import get_session
from asda.models.lead import Lead


class SafetyError(RuntimeError):
    pass


class SafetyGate:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _key(self, channel: str, day: str | None = None) -> tuple[str, str]:
        day = day or date.today().isoformat()
        return channel, day

    def _row(self, channel: str) -> SafetyCounterRow:
        channel, day = self._key(channel)
        session = get_session()
        try:
            pk = f"{channel}:{day}"
            row = session.get(SafetyCounterRow, pk)
            if row is None:
                row = SafetyCounterRow(id=pk, channel=channel, day=day, sent=0)
                session.add(row)
                session.commit()
                session.refresh(row)
            return row
        finally:
            session.close()

    def remaining(self, channel: str) -> int:
        caps = {
            "email": self.settings.email_daily_cap,
            "linkedin": self.settings.linkedin_daily_cap,
            "linkedin_connect": self.settings.linkedin_connect_daily_cap,
            "whatsapp": self.settings.whatsapp_daily_cap,
        }
        cap = caps.get(channel, 50)
        row = self._row(channel)
        if row.paused:
            return 0
        return max(0, cap - row.sent)

    def remaining_week(self, channel: str) -> int:
        if channel not in {"linkedin_connect", "whatsapp"}:
            return 999
        cap = int(
            self.settings.linkedin_connect_weekly_cap
            if channel == "linkedin_connect"
            else self.settings.whatsapp_weekly_cap
        )
        used = 0
        session = get_session()
        try:
            for i in range(7):
                day = (date.today() - timedelta(days=i)).isoformat()
                row = session.get(SafetyCounterRow, f"{channel}:{day}")
                if row:
                    used += int(row.sent or 0)
        finally:
            session.close()
        return max(0, cap - used)

    def _linkedin_window(self, now: datetime | None = None) -> tuple[bool, str]:
        spec = self.settings.safety.get("linkedin") or {}
        tz_name = str(spec.get("timezone") or "Asia/Kolkata")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")
        local = (now or datetime.now(timezone.utc)).astimezone(tz)
        if spec.get("weekdays_only", True) and local.weekday() >= 5:
            return False, "weekend"
        start = int(spec.get("hour_start") or 9)
        end = int(spec.get("hour_end") or 18)
        if local.hour < start or local.hour >= end:
            return False, "outside_hours"
        return True, "ok"

    def _whatsapp_window(self, now: datetime | None = None) -> tuple[bool, str]:
        spec = self.settings.safety.get("whatsapp") or {}
        try:
            tz = ZoneInfo(str(spec.get("timezone") or "Asia/Kolkata"))
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")
        local = (now or datetime.now(timezone.utc)).astimezone(tz)
        if spec.get("weekdays_only", True) and local.weekday() >= 5:
            return False, "weekend"
        if local.hour < int(spec.get("hour_start") or 10) or local.hour >= int(spec.get("hour_end") or 17):
            return False, "outside_hours"
        return True, "ok"

    def _min_gap_ok(self, channel: str) -> tuple[bool, str]:
        if channel.startswith("linkedin"):
            spec = self.settings.safety.get("linkedin") or {}
            gap = int(spec.get("min_seconds_between_actions") or (900 if channel == "linkedin_connect" else 90))
        else:
            spec = self.settings.safety.get(channel) or {}
            gap = int(spec.get("min_seconds_between_sends") or 45)
        session = get_session()
        try:
            last = session.scalar(
                select(EventRow)
                .where(EventRow.type == f"{channel}.sent")
                .order_by(EventRow.ts.desc())
                .limit(1)
            )
        finally:
            session.close()
        if not last or not last.ts:
            return True, "ok"
        ts = last.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        wait = gap - (datetime.now(timezone.utc) - ts).total_seconds()
        if wait > 0:
            return False, "too_soon"
        return True, "ok"

    def allow(self, channel: str, lead: Lead | None = None) -> tuple[bool, str]:
        from asda.runtime import effective

        if effective().dry_run or self.settings.dry_run:
            return True, "dry_run"
        if lead and self._role_suppressed(lead):
            return False, "role_account_suppressed"
        row = self._row(channel)
        if row.paused:
            return False, "channel_paused"
        if self.remaining(channel) <= 0:
            return False, "daily_cap_reached"
        if channel.startswith("linkedin"):
            ok, why = self._linkedin_window()
            if not ok:
                return False, why
            ok, why = self._min_gap_ok(channel)
            if not ok:
                return False, why
            if channel == "linkedin_connect" and self.remaining_week(channel) <= 0:
                return False, "weekly_cap_reached"
        if channel == "whatsapp":
            ok, why = self._whatsapp_window()
            if not ok:
                return False, why
            ok, why = self._min_gap_ok(channel)
            if not ok:
                return False, why
            if self.remaining_week(channel) <= 0:
                return False, "weekly_cap_reached"
        safety = self.settings.safety.get(channel, {})
        sent = max(row.sent, 1)
        if row.bounces / sent >= float(safety.get("pause_on_bounce_rate", 0.04)):
            self.pause(channel, "bounce_rate")
            return False, "bounce_rate_pause"
        if row.complaints / sent >= float(safety.get("pause_on_complaint_rate", 0.001)):
            self.pause(channel, "complaint_rate")
            return False, "complaint_rate_pause"
        return True, "ok"

    def record_send(self, channel: str) -> None:
        self._bump(channel, "sent")

    def record_bounce(self, channel: str = "email") -> None:
        self._bump(channel, "bounces")

    def record_complaint(self, channel: str = "email") -> None:
        self._bump(channel, "complaints")

    def pause(self, channel: str, reason: str) -> None:
        session = get_session()
        try:
            row = session.get(SafetyCounterRow, f"{channel}:{date.today().isoformat()}")
            if row:
                row.paused = 1
                session.commit()
        finally:
            session.close()

    def snapshot(self) -> dict:
        session = get_session()
        try:
            today = date.today().isoformat()
            rows = list(
                session.scalars(select(SafetyCounterRow).where(SafetyCounterRow.day == today))
            )
            return {
                r.channel: {
                    "sent": r.sent,
                    "bounces": r.bounces,
                    "complaints": r.complaints,
                    "paused": bool(r.paused),
                    "remaining": self.remaining(r.channel),
                }
                for r in rows
            }
        finally:
            session.close()

    def _bump(self, channel: str, field: str) -> None:
        session = get_session()
        try:
            pk = f"{channel}:{date.today().isoformat()}"
            row = session.get(SafetyCounterRow, pk)
            if row is None:
                row = SafetyCounterRow(id=pk, channel=channel, day=date.today().isoformat())
                session.add(row)
            setattr(row, field, int(getattr(row, field) or 0) + 1)
            session.commit()
        finally:
            session.close()

    def _role_suppressed(self, lead: Lead) -> bool:
        if not lead.email or "@" not in lead.email:
            return False
        local = lead.email.split("@", 1)[0]
        roles = (
            self.settings.safety.get("email", {}).get("suppress_roles")
            or ["noreply", "no-reply", "postmaster"]
        )
        return any(local.startswith(r) for r in roles)


def next_slot(channel: str, last_at: datetime | None = None) -> datetime:
    settings = get_settings()
    gap = int(settings.safety.get(channel, {}).get("min_seconds_between_sends", 45))
    now = datetime.now(timezone.utc)
    if last_at and (now - last_at).total_seconds() < gap:
        from datetime import timedelta

        return last_at + timedelta(seconds=gap)
    return now
