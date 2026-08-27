"""Load a leads file of almost any shape: extra columns, Apollo/HubSpot headers, tabs, semicolons."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import is_valid_lead, normalize_row
from asda.ingestion.workbook import tables_from
from asda.models.lead import Lead, LeadQuery

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _delimiter(sample: str) -> str:
    header = sample.splitlines()[0] if sample else ""
    try:
        dialect = csv.Sniffer().sniff(sample[:8192], delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        pass
    counts = {",": header.count(","), ";": header.count(";"), "\t": header.count("\t"), "|": header.count("|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def iter_rows(path: Path) -> list[dict[str, str]]:
    text = _read_text(path)
    if not text.strip():
        return []
    delim = _delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim, restkey="_extra")
    rows: list[dict[str, str]] = []
    for raw in reader:
        row: dict[str, str] = {}
        extras: list[str] = []
        for key, value in raw.items():
            if key is None:
                continue
            name = str(key).strip()
            if not name:
                continue
            if name == "_extra":
                extras.extend(str(x) for x in (value or []) if x)
                continue
            # Duplicate headers: keep the first non-empty
            if name in row and row[name]:
                continue
            row[name] = "" if value is None else str(value).strip()
        if extras:
            row["_extra"] = " | ".join(extras)
        if any(v for v in row.values()):
            rows.append(row)
    return rows


class CSVSource(LeadSource):
    name = "csv"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None

    def fetch(self, query: LeadQuery) -> list[Lead]:
        path = Path(query.extra.get("path") or self.path or "")
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")

        leads: list[Lead] = []
        skipped = 0
        reasons: dict[str, int] = {}
        tables = tables_from(path)
        for _sheet, rows in tables:
            for row in rows:
                lead = normalize_row(row, source=self.name)
                ok, why = is_valid_lead(lead)
                if ok:
                    leads.append(lead)
                else:
                    skipped += 1
                    reasons[why] = reasons.get(why, 0) + 1
                if len(leads) >= query.limit:
                    break
            if len(leads) >= query.limit:
                break
        lead_blob = query.extra
        if isinstance(lead_blob, dict):
            lead_blob["skipped"] = skipped
            lead_blob["skip_reasons"] = reasons
            lead_blob["sheets"] = [{"name": n, "rows": len(r)} for n, r in tables]
        return leads
