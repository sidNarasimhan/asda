"""In-process event bus with optional Redis fan-out."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable

from asda.config import get_settings
from asda.models.events import Event, EventType

logger = logging.getLogger(__name__)

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[EventType | str, list[Handler]] = defaultdict(list)
        self._history: list[Event] = []
        self._redis = None
        url = get_settings().redis_url
        if url:
            try:
                import redis

                self._redis = redis.Redis.from_url(url)
            except Exception:
                logger.exception("Redis unavailable; using in-process bus only")

    def on(self, event_type: EventType | str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        self._history.append(event)
        if len(self._history) > 5000:
            self._history = self._history[-2000:]
        for key in (event.type, "*"):
            for handler in self._handlers.get(key, []):
                try:
                    handler(event)
                except Exception:
                    logger.exception("Event handler failed for %s", event.type)
        if self._redis is not None:
            try:
                self._redis.publish("asda.events", event.model_dump_json())
            except Exception:
                logger.exception("Redis publish failed")
        logger.info("event %s lead=%s", event.type.value, event.lead_id)
        self._persist(event)
        return event

    def _persist(self, event: Event) -> None:
        try:
            from asda.db.repository import Repository
            from asda.db.session import get_session

            session = get_session()
            try:
                Repository(session).save_event(event)
                session.commit()
            finally:
                session.close()
        except Exception:
            logger.exception("event persist failed")

    def emit_type(
        self,
        event_type: EventType,
        lead_id: str | None = None,
        actor: str = "system",
        **payload: object,
    ) -> Event:
        return self.emit(
            Event(type=event_type, lead_id=lead_id, actor=actor, payload=dict(payload))
        )

    def history(self, lead_id: str | None = None, limit: int = 100) -> list[Event]:
        items = self._history
        if lead_id:
            items = [e for e in items if e.lead_id == lead_id]
        return items[-limit:]


_BUS: EventBus | None = None


def get_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS
