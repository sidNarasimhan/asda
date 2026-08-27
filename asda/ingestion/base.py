"""LeadSource interface — implement this to add any new provider."""

from __future__ import annotations

from abc import ABC, abstractmethod

from asda.models.lead import Lead, LeadQuery


class LeadSource(ABC):
    """Drop-in connector. Adding Apollo vs Clay vs a CSV is just a new class."""

    name: str = "base"

    @abstractmethod
    def fetch(self, query: LeadQuery) -> list[Lead]:
        """Return normalized Leads. Never raise on empty results."""

    def validate_config(self) -> None:
        """Raise ValueError if required credentials/config are missing."""

    def healthcheck(self) -> dict[str, str]:
        try:
            self.validate_config()
            return {"source": self.name, "status": "ok"}
        except Exception as exc:  # noqa: BLE001 — health endpoint must not crash
            return {"source": self.name, "status": "misconfigured", "detail": str(exc)}
