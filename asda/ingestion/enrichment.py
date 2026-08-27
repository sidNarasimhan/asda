"""Apply contact-provider data only when identity has been verified."""

from __future__ import annotations

from asda.ingestion.pipeline import _fold_person
from asda.ingestion.signalhire import _linkedin_handle


def apply_verified_enrichment(original, enriched) -> None:
    """Merge contacts, then replace employment fields from an exact LinkedIn match."""
    _fold_person(original, enriched)
    if enriched.linkedin_url and _linkedin_handle(enriched.linkedin_url) == _linkedin_handle(original.linkedin_url):
        if enriched.company.name:
            original.company = enriched.company
        if enriched.title:
            original.title = enriched.title
    original.raw_data["signalhire_enriched"] = True
