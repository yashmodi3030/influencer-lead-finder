"""All Google Sheets reads/writes, plus first-run tab bootstrap.

Existing data is never overwritten: missing tabs are created, empty header rows are
filled, and only missing Settings keys are appended.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Iterable

from . import config
from .models import Lead, QueryLogEntry, Rejection, RunStats, SearchRequest

log = logging.getLogger(__name__)

TAB_HEADERS: dict[str, list[str]] = {
    "Leads": [
        "channel_id", "handle", "channel_url", "name", "subscribers", "total_views",
        "video_count", "median_views", "avg_views", "engagement_rate", "last_upload",
        "country", "niche", "description", "website", "instagram", "instagram_url",
        "instagram_followers", "email", "email_status", "phone", "contact_source", "source_url",
        "search_query", "date_found", "status", "notes",
    ],
    # Type a query in column A (status blank or "pending"); the next run searches it.
    "Searches": [
        "query", "niche", "status", "requested_at", "processed_at", "channels_found",
        "leads_added", "note",
    ],
    "Settings": ["key", "value"],
    "Rejected": ["channel_id", "name", "reason", "date"],
    "QueryLog": ["query", "api", "date", "results_count"],
    "RunLog": [
        "run_id", "started_at", "finished_at", "youtube_units_used", "web_searches_used", "found",
        "checked", "accepted", "rejected", "errors", "warnings",
    ],
}


class SheetsUnavailable(Exception):
    """The spreadsheet cannot be opened or written (credentials, sharing, network)."""


def _open_spreadsheet(service_account_json: str, sheet_id: str):
    import gspread

    try:
        info = json.loads(service_account_json)
    except ValueError as exc:
        raise SheetsUnavailable(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key file contents."
        ) from exc
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        raise SheetsUnavailable(
            "GOOGLE_SERVICE_ACCOUNT_JSON does not look like a service-account key (missing client_email/private_key)."
        )
    try:
        client = gspread.service_account_from_dict(info)
    except ValueError as exc:
        raise SheetsUnavailable(
            "The private_key inside GOOGLE_SERVICE_ACCOUNT_JSON could not be read. Re-download the "
            "JSON key and paste the whole file unchanged."
        ) from exc
    try:
        return client.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetsUnavailable(
            f"Spreadsheet {sheet_id!r} not found, or not shared with {info.get('client_email')} as Editor."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetsUnavailable(f"Google Sheets API error: {exc}") from exc
    except Exception as exc:  # auth / network errors from google-auth or requests
        raise SheetsUnavailable(f"Could not open the spreadsheet: {type(exc).__name__}: {exc}") from exc


def _column_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


class SheetStore:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self._worksheets: dict = {}
        self._headers: dict[str, list[str]] = {}

    @classmethod
    def connect(cls, service_account_json: str, sheet_id: str) -> "SheetStore":
        return cls(_open_spreadsheet(service_account_json, sheet_id))

    # -- bootstrap ---------------------------------------------------------

    def bootstrap(self, dry_run: bool = False) -> list[str]:
        """Create missing tabs / headers / Settings keys. Returns a list of actions taken."""
        actions: list[str] = []
        try:
            existing = {ws.title: ws for ws in self.spreadsheet.worksheets()}
        except Exception as exc:
            raise SheetsUnavailable(f"Could not list worksheets: {exc}") from exc

        for title, headers in TAB_HEADERS.items():
            ws = existing.get(title)
            if ws is None:
                actions.append(f"create tab {title}")
                if dry_run:
                    self._headers[title] = list(headers)
                    continue
                ws = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=max(len(headers), 2))
                ws.update(values=[headers], range_name="A1")
                self._headers[title] = list(headers)
            else:
                current = [h.strip() for h in ws.row_values(1)]
                if not any(current):
                    actions.append(f"write headers for {title}")
                    if not dry_run:
                        ws.update(values=[headers], range_name="A1")
                    current = list(headers)
                else:
                    # Columns added in newer versions go after the owner's existing columns.
                    have = {h.lower() for h in current}
                    missing = [h for h in headers if h.lower() not in have]
                    if missing:
                        actions.append(f"add columns {', '.join(missing)} to {title}")
                        if not dry_run:
                            needed = len(current) + len(missing)
                            col_count = getattr(ws, "col_count", needed)
                            if col_count < needed:
                                ws.add_cols(needed - col_count)
                            ws.update(values=[missing], range_name=f"{_column_letter(len(current) + 1)}1")
                        current = current + missing
                self._headers[title] = current
            self._worksheets[title] = ws

        actions.extend(self._seed_settings(dry_run))
        return actions

    def _seed_settings(self, dry_run: bool) -> list[str]:
        ws = self._worksheets.get("Settings")
        existing_keys: set[str] = set()
        if ws is not None:
            existing_keys = {row[0].strip().lower() for row in ws.get_all_values()[1:] if row and row[0].strip()}
        missing = [[k, v] for k, v in config.DEFAULT_SETTINGS.items() if k not in existing_keys]
        if not missing:
            return []
        if not dry_run and ws is not None:
            ws.append_rows(missing, value_input_option="RAW")
        return [f"seed Settings key {k}" for k, _ in missing]

    def attach(self) -> None:
        """Map existing tabs without creating or changing anything (read-only use)."""
        try:
            self._worksheets = {ws.title: ws for ws in self.spreadsheet.worksheets()}
        except Exception as exc:
            raise SheetsUnavailable(f"Could not list worksheets: {exc}") from exc

    def read_table(self, tab: str) -> list[dict[str, str]]:
        """All rows of a tab as dicts keyed by lower-cased header."""
        return self._records(tab)

    # -- helpers -----------------------------------------------------------

    def headers(self, tab: str) -> list[str]:
        return self._headers.get(tab) or list(TAB_HEADERS[tab])

    def _records(self, tab: str) -> list[dict[str, str]]:
        ws = self._worksheets.get(tab)
        if ws is None:
            return []
        rows = ws.get_all_values()
        if not rows:
            return []
        headers = [h.strip().lower() for h in rows[0]]
        return [
            {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers)) if headers[i]}
            for row in rows[1:]
        ]

    # -- reads -------------------------------------------------------------

    def read_settings(self) -> dict[str, str]:
        settings = {}
        for record in self._records("Settings"):
            key = (record.get("key") or "").strip().lower()
            if key:
                settings[key] = (record.get("value") or "").strip()
        return settings

    def read_lead_statuses(self) -> dict[str, str]:
        """channel_id -> status, for every existing lead (IDs are case-sensitive)."""
        statuses = {}
        for record in self._records("Leads"):
            channel_id = (record.get("channel_id") or "").strip()
            if channel_id:
                statuses[channel_id] = (record.get("status") or "new").strip().lower()
        return statuses

    def read_rejected_ids(self) -> set[str]:
        return {
            (r.get("channel_id") or "").strip()
            for r in self._records("Rejected")
            if (r.get("channel_id") or "").strip()
        }

    def read_pending_searches(self) -> list[SearchRequest]:
        """Rows of the Searches tab whose status is blank or "pending"."""
        pending = []
        for i, record in enumerate(self._records("Searches"), start=2):  # row 1 = headers
            query = (record.get("query") or "").strip()
            status = (record.get("status") or "").strip().lower()
            if query and status in ("", "pending"):
                pending.append(SearchRequest(query=query, niche=(record.get("niche") or "").strip(), row=i,
                                             requested_at=(record.get("requested_at") or "").strip()))
        return pending

    def read_query_log(self) -> dict[str, date]:
        """Lower-cased query -> most recent date it ran."""
        last: dict[str, date] = {}
        for record in self._records("QueryLog"):
            query = (record.get("query") or "").strip().lower()
            day = _parse_date(record.get("date", ""))
            if query and day and (query not in last or day > last[query]):
                last[query] = day
        return last

    # -- writes (each is a single batched append) ----------------------------

    def _append(self, tab: str, rows: list[list]) -> None:
        if not rows:
            return
        ws = self._worksheets.get(tab)
        if ws is None:
            raise SheetsUnavailable(f"Tab {tab} is not available")
        ws.append_rows(rows, value_input_option="RAW")

    def append_leads(self, leads: Iterable[Lead]) -> int:
        headers = self.headers("Leads")
        rows = [lead.to_row(headers) for lead in leads]
        self._append("Leads", rows)
        return len(rows)

    def append_rejections(self, rejections: Iterable[Rejection]) -> int:
        headers = self.headers("Rejected")
        rows = [r.to_row(headers) for r in rejections]
        self._append("Rejected", rows)
        return len(rows)

    def append_query_log(self, entries: Iterable[QueryLogEntry]) -> int:
        headers = self.headers("QueryLog")
        rows = [e.to_row(headers) for e in entries]
        self._append("QueryLog", rows)
        return len(rows)

    def save_searches(self, searches: Iterable[SearchRequest]) -> None:
        """Update processed rows in place (one batched call) and append new ones (row == 0)."""
        searches = list(searches)
        if not searches:
            return
        ws = self._worksheets.get("Searches")
        if ws is None:
            raise SheetsUnavailable("Tab Searches is not available")
        headers = self.headers("Searches")
        last_col = _column_letter(len(headers))
        updates = [{"range": f"A{s.row}:{last_col}{s.row}", "values": [s.to_row(headers)]}
                   for s in searches if s.row]
        if updates:
            ws.batch_update(updates, value_input_option="RAW")
        new_rows = [s.to_row(headers) for s in searches if not s.row]
        self._append("Searches", new_rows)

    def append_run_log(self, stats: RunStats) -> None:
        self._append("RunLog", [stats.to_row(self.headers("RunLog"))])
