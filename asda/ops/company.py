"""Company / offer config — the tenant the agent works for."""

from __future__ import annotations

from typing import Any

import yaml

from asda.config import ROOT, get_settings


def offer_path():
    return ROOT / "config" / "offer.yaml"


def load_offer() -> dict[str, Any]:
    path = offer_path()
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _lines(raw: str) -> list[str]:
    out = []
    for part in (raw or "").replace(",", "\n").splitlines():
        item = part.strip().strip("- ")
        if item:
            out.append(item)
    return out


def patch_offer(**fields: Any) -> dict[str, Any]:
    """Merge a few offer keys (used by talk-first onboarding)."""
    current = load_offer()
    for key, value in fields.items():
        if value is None or value == "":
            continue
        current[key] = value
    path = offer_path()
    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True))
    get_settings.cache_clear()
    return current


def save_offer(
    *,
    company_name: str,
    product_name: str,
    website: str,
    tagline: str,
    cbo_name: str,
    cbo_title: str,
    value_proposition: str,
    tone: str,
    call_to_action: str,
    icp_titles: str,
    icp_industries: str,
    icp_geos: str,
) -> dict[str, Any]:
    current = load_offer()
    current["company_name"] = company_name.strip()
    current["product_name"] = product_name.strip()
    current["website"] = website.strip()
    current["tagline"] = tagline.strip()
    current["cbo_name"] = cbo_name.strip()
    current["cbo_title"] = cbo_title.strip()
    current["value_proposition"] = value_proposition.strip()
    current["tone"] = tone.strip()
    current["call_to_action"] = call_to_action.strip()
    icp = dict(current.get("icp") or {})
    if icp_titles.strip():
        icp["titles"] = _lines(icp_titles)
    if icp_industries.strip():
        icp["industries"] = _lines(icp_industries)
    if icp_geos.strip():
        icp["geos"] = _lines(icp_geos)
    current["icp"] = icp
    offer_path().write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True))
    get_settings.cache_clear()
    return current
