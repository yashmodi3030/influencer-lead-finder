"""Shared fakes. No test touches the network."""

from __future__ import annotations

import json

import pytest
import requests


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None, url=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text else (json.dumps(json_data) if json_data is not None else "")
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.url = url
        self.encoding = "utf-8"

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def iter_content(self, chunk_size=65536):
        data = self.text.encode("utf-8")
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """Returns queued responses in order; records every call."""

    def __init__(self, responses=None, router=None):
        self.responses = list(responses or [])
        self.router = router
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.router:
            return self.router(method, url, kwargs)
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = [list(r) for r in (rows or [])]
        self.append_calls = []

    def row_values(self, n):
        return list(self.rows[n - 1]) if len(self.rows) >= n else []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update(self, values=None, range_name=None, **kwargs):
        """Supports header writes: 'A1' (whole row) or e.g. 'X1' (cells from column X)."""
        assert range_name.endswith("1") and range_name[:-1].isalpha()
        col = 0
        for ch in range_name[:-1]:
            col = col * 26 + (ord(ch.upper()) - 64)
        if not self.rows:
            self.rows.append([])
        row = self.rows[0]
        row.extend([""] * max(0, col - 1 - len(row)))
        self.rows[0] = row[:col - 1] + list(values[0])

    def batch_update(self, data, value_input_option=None, **kwargs):
        """Supports single-row ranges like 'A5:H5'."""
        self.batch_calls = getattr(self, "batch_calls", []) + [(data, value_input_option)]
        for item in data:
            row = int("".join(ch for ch in item["range"].split(":")[0] if ch.isdigit()))
            while len(self.rows) < row:
                self.rows.append([])
            self.rows[row - 1] = list(item["values"][0])

    def append_rows(self, rows, value_input_option=None, **kwargs):
        self.append_calls.append(([list(r) for r in rows], value_input_option))
        self.rows.extend(list(r) for r in rows)


class FakeSpreadsheet:
    def __init__(self, tabs=None):
        self.tabs = {t.title: t for t in (tabs or [])}
        self.added = []

    def worksheets(self):
        return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        self.tabs[title] = ws
        self.added.append(title)
        return ws

    def worksheet(self, title):
        return self.tabs[title]


@pytest.fixture
def no_sleep():
    return lambda seconds: None


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("real network access attempted in tests")

    monkeypatch.setattr(requests.Session, "request", guard)
