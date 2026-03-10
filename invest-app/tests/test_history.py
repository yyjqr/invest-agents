"""Tests for search history persistence."""

import json
from pathlib import Path

import pytest

from invest_app.history import (
    HistoryRecord,
    SearchHistory,
    add_history_record,
    get_recent_tickers,
    load_history,
    save_history,
)


@pytest.fixture()
def tmp_history(tmp_path, monkeypatch):
    """Redirect HISTORY_FILE to a temp path."""
    import invest_app.history as hist_mod

    tmp_file = tmp_path / "search_history.json"
    monkeypatch.setattr(hist_mod, "HISTORY_FILE", tmp_file)
    # Also patch DATA_DIR so ensure_data_dir creates tmp_path
    monkeypatch.setattr(hist_mod, "DATA_DIR", tmp_path)
    # Stub ensure_data_dir so it doesn't try to create home dir
    monkeypatch.setattr(hist_mod, "ensure_data_dir", lambda: tmp_path)
    return tmp_file


def test_load_empty_history(tmp_history):
    h = load_history()
    assert h.records == []


def test_save_and_load_roundtrip(tmp_history):
    h = SearchHistory(records=[
        HistoryRecord(ticker="600519.SS", name="贵州茅台", action="持有观察",
                      sector="消费", price=1500.0, timestamp="2026-01-01T10:00:00"),
    ])
    save_history(h)
    loaded = load_history()
    assert len(loaded.records) == 1
    assert loaded.records[0].ticker == "600519.SS"
    assert loaded.records[0].price == 1500.0


def test_add_history_record_dedup(tmp_history):
    add_history_record("600519.SS", "贵州茅台", action="持有观察", sector="消费", price=1500.0)
    add_history_record("600519.SS", "贵州茅台", action="可分批买入", sector="消费", price=1450.0)
    records = get_recent_tickers()
    assert sum(1 for r in records if r.ticker == "600519.SS") == 1
    # Latest entry should win
    assert records[-1].action == "可分批买入"


def test_get_recent_tickers_order(tmp_history):
    for i in range(5):
        add_history_record(f"00000{i}.SZ", f"Stock{i}", price=float(i))
    recent = get_recent_tickers(limit=3)
    assert len(recent) == 3
    # Most recent first
    assert recent[0].ticker == "000004.SZ"


def test_history_file_is_valid_json(tmp_history):
    add_history_record("588000.SS", "科创50ETF", action="可分批买入", sector="科技", price=1.2)
    data = json.loads(tmp_history.read_text(encoding="utf-8"))
    assert "records" in data
    assert len(data["records"]) == 1
