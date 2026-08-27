from __future__ import annotations

from asda.config import get_settings
from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery


class GoogleSheetsSource(LeadSource):
    """Optional Google Sheets connector. Extra: spreadsheet_id, worksheet."""

    name = "sheets"

    def validate_config(self) -> None:
        if not get_settings().google_sheets_credentials_json:
            raise ValueError("GOOGLE_SHEETS_CREDENTIALS_JSON is not set")

    def fetch(self, query: LeadQuery) -> list[Lead]:
        self.validate_config()
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise RuntimeError("Install extras: pip install 'asda[sheets]'") from exc

        settings = get_settings()
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(
            settings.google_sheets_credentials_json, scopes=scopes
        )
        gc = gspread.authorize(creds)
        sheet_id = query.extra.get("spreadsheet_id")
        if not sheet_id:
            raise ValueError("query.extra.spreadsheet_id is required")
        ws_name = query.extra.get("worksheet", 0)
        sh = gc.open_by_key(sheet_id)
        ws = sh.worksheet(ws_name) if isinstance(ws_name, str) else sh.get_worksheet(ws_name)
        rows = ws.get_all_records()
        leads: list[Lead] = []
        for row in rows:
            lead = normalize_row(row, source=self.name)
            ok, _ = is_valid_lead(lead)
            if ok:
                leads.append(lead)
            if len(leads) >= query.limit:
                break
        return leads
