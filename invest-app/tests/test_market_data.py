"""Tests for market data helpers and China-market fallbacks."""

from invest_app.market_data import (
    _classify_cn_code,
    _cn_market_prefix,
    _period_to_cn_days,
    normalize_ticker,
    search_instruments,
)


def test_cn_market_prefix_and_normalization():
    assert _cn_market_prefix("600519") == "sh"
    assert _cn_market_prefix("688981") == "sh"
    assert _cn_market_prefix("000001") == "sz"
    assert normalize_ticker("600519") == "600519.SS"
    assert normalize_ticker("000001") == "000001.SZ"


def test_period_mapping_defaults():
    assert _period_to_cn_days("1mo") == 22
    assert _period_to_cn_days("6mo") == 132
    assert _period_to_cn_days("unknown") == 260


def test_classify_cn_code_for_star_and_fund():
    assert _classify_cn_code("688981", "中芯国际") == "star"
    assert _classify_cn_code("588000", "科创50ETF") == "fund"
    assert _classify_cn_code("600519", "贵州茅台") == "stock"


def test_search_instruments_parses_sina_response(monkeypatch):
    payload = (
        'var suggestvalue="11,11,sh600519,600519,贵州茅台,0,0;'
        '11,11,sh588000,588000,科创50ETF,0,0";'
    )

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return payload.encode("gbk", errors="ignore")

    monkeypatch.setattr(
        "invest_app.market_data.urlopen",
        lambda *_args, **_kwargs: DummyResponse(),
    )

    results = search_instruments("茅台", limit=5)
    assert len(results) == 2
    assert results[0].code == "600519"
    assert results[0].exchange == "SSE"
    assert results[1].category == "fund"
