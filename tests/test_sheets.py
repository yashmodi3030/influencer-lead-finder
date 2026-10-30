from datetime import date

import pytest

from pipeline.config import DEFAULT_SETTINGS
from pipeline.models import Lead, Rejection
from pipeline.sheets import TAB_HEADERS, SheetStore, SheetsUnavailable, _open_spreadsheet

from .conftest import FakeSpreadsheet, FakeWorksheet


def test_bootstrap_creates_missing_tabs_and_seeds_settings():
    book = FakeSpreadsheet()
    store = SheetStore(book)
    store.bootstrap()
    assert set(book.added) == set(TAB_HEADERS)
    assert book.tabs["Leads"].rows[0] == TAB_HEADERS["Leads"]
    settings = store.read_settings()
    assert settings == {k: v for k, v in DEFAULT_SETTINGS.items()}


def test_bootstrap_never_overwrites_existing_data():
    leads = FakeWorksheet("Leads", [TAB_HEADERS["Leads"], ["UCriya"] + [""] * 22])
    settings = FakeWorksheet("Settings", [["key", "value"], ["niches", "yoga"], ["active", "FALSE"]])
    book = FakeSpreadsheet([leads, settings])
    store = SheetStore(book)
    store.bootstrap()
    assert leads.rows[1][0] == "UCriya"
    assert len(leads.rows) == 2
    values = store.read_settings()
    assert values["niches"] == "yoga" and values["active"] == "FALSE"
    assert values["min_subscribers"] == "50000"  # missing key appended
    assert "Leads" not in book.added and "Rejected" in book.added


def test_bootstrap_dry_run_writes_nothing():
    book = FakeSpreadsheet()
    SheetStore(book).bootstrap(dry_run=True)
    assert book.added == []


def test_append_leads_is_one_batched_call_in_header_order():
    # Owner re-ordered columns and capitalised a header: mapping follows the sheet.
    headers = ["Channel_ID", "email", "subscribers", "status", "extra_col"]
    leads_ws = FakeWorksheet("Leads", [headers])
    book = FakeSpreadsheet([leads_ws])
    store = SheetStore(book)
    store.bootstrap()
    leads = [Lead(channel_id=f"h{i}", handle="", channel_url="u", email=f"h{i}@x.in", subscribers=i * 1000)
             for i in range(5)]
    assert store.append_leads(leads) == 5
    assert len(leads_ws.append_calls) == 1
    rows, mode = leads_ws.append_calls[0]
    assert mode == "RAW"  # descriptions starting with "=" must not become formulas
    assert rows[0] == ["h0", "h0@x.in", 0, "new", ""]
    assert len(rows) == 5


def test_empty_append_makes_no_call():
    ws = FakeWorksheet("Rejected", [TAB_HEADERS["Rejected"]])
    store = SheetStore(FakeSpreadsheet([ws]))
    store.bootstrap()
    store.append_rejections([])
    assert ws.append_calls == []
    store.append_rejections([Rejection("a", "A", "r", "2026-10-30"), Rejection("b", "B", "r", "2026-10-30")])
    assert len(ws.append_calls) == 1


def test_reads_for_dedupe():
    status_col = TAB_HEADERS["Leads"].index("status")
    dnc = ["UCriya"] + [""] * (status_col - 1) + ["do_not_contact", ""]
    leads = FakeWorksheet("Leads", [TAB_HEADERS["Leads"], dnc, [" UCaman "] + [""] * 22])
    rejected = FakeWorksheet("Rejected", [TAB_HEADERS["Rejected"], ["UCjoe", "Joe", "x", "2026-10-01"]])
    qlog = FakeWorksheet("QueryLog", [TAB_HEADERS["QueryLog"], ["Q One", "tavily", "2026-10-01", "3"],
                                      ["q one", "serpapi", "2026-10-20", "1"]])
    store = SheetStore(FakeSpreadsheet([leads, rejected, qlog]))
    store.bootstrap()
    assert store.read_lead_statuses() == {"UCriya": "do_not_contact", "UCaman": "new"}
    assert store.read_rejected_ids() == {"UCjoe"}
    assert store.read_query_log() == {"q one": date(2026, 10, 20)}


def test_bad_service_account_json():
    with pytest.raises(SheetsUnavailable, match="not valid JSON"):
        _open_spreadsheet("{not json", "id")
    with pytest.raises(SheetsUnavailable, match="service-account key"):
        _open_spreadsheet('{"type": "x"}', "id")
