"""Tests for single-stock recommendation logic."""

import pandas as pd

from invest_app.recommendation import recommend_stock
from invest_app.industry import BenchmarkSnapshot, SWOTAnalysis


def _monkeypatch_common(monkeypatch, history, quote_cls):
    """Shared monkeypatching for recommendation tests."""
    monkeypatch.setattr("invest_app.recommendation.get_history", lambda *_a, **_k: history)
    monkeypatch.setattr("invest_app.recommendation.get_quote", lambda *_a, **_k: quote_cls())
    # Stub industry helpers so tests don't make network calls
    monkeypatch.setattr("invest_app.recommendation.classify_sector", lambda _n, _t: "科技")
    monkeypatch.setattr("invest_app.recommendation.estimate_sector_maturity", lambda _s: "成长期")
    monkeypatch.setattr("invest_app.recommendation.get_sector_benchmarks", lambda _s: [])
    monkeypatch.setattr("invest_app.recommendation.fetch_benchmark_snapshots", lambda _b: [])
    monkeypatch.setattr(
        "invest_app.recommendation.build_swot",
        lambda **_kw: SWOTAnalysis(
            strengths=["stub"], weaknesses=["stub"], opportunities=["stub"], threats=["stub"],
        ),
    )


def _make_history(n: int = 130, base: float = 10.0, step: float = 0.1):
    return pd.DataFrame(
        {
            "Open": [base + i * step for i in range(n)],
            "High": [base + 0.2 + i * step for i in range(n)],
            "Low": [base - 0.2 + i * step for i in range(n)],
            "Close": [base + i * step for i in range(n)],
            "Volume": [1000] * (n - 10) + [2000] * 10,
        },
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )


# ─── Stock tests ──────────────────────────────────────────────────────────


def test_recommend_stock_generates_ranges(monkeypatch):
    history = _make_history()

    class DummyQuote:
        ticker = "600519.SS"
        name = "贵州茅台"
        price = 22.5

    _monkeypatch_common(monkeypatch, history, DummyQuote)

    report = recommend_stock("600519", period="6mo", pe_ratio=25.0, profit_margin=0.2)

    assert report.ticker == "600519.SS"
    assert report.instrument_type == "stock"
    assert report.sector == "科技"
    assert report.sector_maturity == "成长期"
    assert report.buy_range is not None
    assert report.sell_range is not None
    assert report.stop_loss is not None
    assert report.market_view in {"偏强", "中性", "偏弱"}
    assert report.valuation_view == "合理"
    assert report.swot is not None
    assert len(report.swot.strengths) > 0


def test_recommend_stock_raises_on_empty_history(monkeypatch):
    class DummyQuote:
        ticker = "600519.SS"
        name = "贵州茅台"
        price = 100.0

    _monkeypatch_common(monkeypatch, pd.DataFrame(), DummyQuote)

    try:
        recommend_stock("600519")
        assert False, "should raise ValueError"
    except ValueError as exc:
        assert "No enough historical data" in str(exc)


# ─── Fund tests ───────────────────────────────────────────────────────────


def test_recommend_fund_uses_percentile_strategy(monkeypatch):
    """Fund (588000) should use percentile-based buy/sell zones."""
    history = _make_history(n=130, base=1.0, step=0.005)

    class DummyQuote:
        ticker = "588000.SS"
        name = "科创50ETF"
        price = 1.35

    _monkeypatch_common(monkeypatch, history, DummyQuote)

    report = recommend_stock("588000", period="6mo")

    assert report.instrument_type == "fund"
    assert report.buy_range is not None
    assert report.sell_range is not None
    # Fund buy zone should be within the historic range (percentile-based)
    assert report.buy_range[0] >= history["Close"].min() * 0.95
    assert report.sell_range[1] <= history["Close"].max() * 1.05
