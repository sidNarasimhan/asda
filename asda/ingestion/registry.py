from __future__ import annotations

from asda.ingestion.apollo import ApolloSource
from asda.ingestion.base import LeadSource
from asda.ingestion.csv_source import CSVSource
from asda.ingestion.generic_api import GenericAPISource
from asda.ingestion.sheets import GoogleSheetsSource
from asda.ingestion.signalhire import SignalHireSource
from asda.ingestion.webhook import WebhookSource


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, LeadSource] = {}

    def register(self, source: LeadSource) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> LeadSource:
        if name not in self._sources:
            raise KeyError(
                f"Unknown lead source '{name}'. Registered: {sorted(self._sources)}"
            )
        return self._sources[name]

    def names(self) -> list[str]:
        return sorted(self._sources)

    def all(self) -> list[LeadSource]:
        return list(self._sources.values())


def build_default_registry() -> SourceRegistry:
    registry = SourceRegistry()
    for source in (
        CSVSource(),
        ApolloSource(),
        WebhookSource(),
        GoogleSheetsSource(),
        GenericAPISource("zoominfo"),
        SignalHireSource(),
        GenericAPISource("clay"),
    ):
        registry.register(source)
    return registry


_REGISTRY: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY
