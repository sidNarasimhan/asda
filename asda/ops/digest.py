"""Open-ended files from chat: CSV leads, PDFs, notes. All of it goes to memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from asda.config import get_settings
from asda.memory.store import remember

_TEXT = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".log"}
_PDF = {".pdf"}
_LEAD = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls"}


def _save(upload_name: str, data: bytes) -> Path:
    dest = get_settings().data_dir / "uploads" / (upload_name or "upload.bin")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:40]:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception:
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""


def digest_bytes(filename: str, data: bytes, note: str = "") -> dict[str, Any]:
    name = filename or "upload"
    suffix = Path(name).suffix.lower()
    path = _save(name, data)
    result: dict[str, Any] = {"file": name, "path": str(path), "leads": 0, "memory": 0, "kind": "note"}

    if suffix in _LEAD:
        from asda.ingestion.pipeline import ingest_path

        loaded = ingest_path(path)
        created = loaded.get("ingested") or 0
        total = loaded.get("total") or 0
        result.update({"kind": "leads", "leads": total, "created": created})
        remember(
            f"Ingested {total} leads from {name}" + (f". {note}" if note else ""),
            kind="episode",
            source="digest",
            importance=0.6,
            event=False,
        )
        return result

    text = ""
    if suffix in _PDF:
        text = _pdf_text(data)
        result["kind"] = "pdf"
    else:
        text = data.decode("utf-8", errors="ignore")
        result["kind"] = "note"

    blob = (note + "\n\n" if note else "") + text
    blob = blob.strip()
    if not blob:
        result["error"] = "could not read file"
        return result
    remember(
        f"From {name}: {blob[:3500]}",
        kind="fact",
        subject=Path(name).stem,
        source="digest",
        importance=0.7,
        tags=["upload", suffix.lstrip(".")],
    )
    result["memory"] = 1
    result["chars"] = len(blob)
    return result
