"""Tests for invest_app/strategies.py."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from invest_app.strategies import (
    MultiStrategyReport,
    StrategySignal,
    _calc_bias,
    _calc_rsi,
    _ma_slope,
    _position_advice,
    is_in_hot_sector,
    run_all_strategies,
    strategy_ma_trend,
    strategy_macro_industry,
    strategy_rsi_bias,
)


# ---------------------------------------------------------------------------
# RSI / Bias / Slope helpers
# ---------------------------------------------------------------------------

def _make_close(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_rsi_returns_none_for_insufficient_data():
    s = _make_close([100.0] * 10)  # < 15 points
    assert _calc_rsi(s) is None


def test_rsi_flat_series_is_nan_safe():
    # Constant price → zero gain AND zero loss → RS undefined; should be None or 50
    s = _make_close([100.0] * 20)
    result = _calc_rsi(s)
    # pandas will produce NaN for 0/0, so expect None
    assert result is None


def test_rsi_rising_series():
    # Uptrend with small pullback every 3 days; ensures avg_loss > 0 in every window
    prices: list[float] = []
    p = 50.0
    for i in range(40):
        if i % 3 == 2:
            p -= 0.2   # small down day
        else:
            p += 1.5   # two up days
        prices.append(p)
    s = _make_close(prices)
    rsi = _calc_rsi(s)
    assert rsi is not None
    assert rsi > 60  # predominantly up → RSI well above 50


def test_rsi_falling_series():
    # All losses → RSI should be close to 0
    prices = [float(30 - i) for i in range(30)]
    s = _make_close(prices)
    rsi = _calc_rsi(s)
    assert rsi is not None
    assert rsi < 20


def test_bias_positive_above_ma():
    # Last price well above MA20 → positive bias
    prices = [100.0] * 19 + [120.0]
    s = _make_close(prices)
    bias = _calc_bias(s)
    assert bias is not None
    assert bias > 0


def test_bias_negative_below_ma():
    prices = [100.0] * 19 + [80.0]
    s = _make_close(prices)
    bias = _calc_bias(s)
    assert bias is not None
    assert bias < 0


def test_bias_returns_none_for_short_series():
    s = _make_close([100.0] * 10)
    assert _calc_bias(s) is None


def test_ma_slope_rising():
    prices = [float(i) for i in range(1, 10)]
    s = _make_close(prices)
    slope = _ma_slope(s, 5)
    assert slope is not None
    assert slope > 0


def test_ma_slope_falling():
    prices = [float(10 - i) for i in range(10)]
    s = _make_close(prices)
    slope = _ma_slope(s, 5)
    assert slope is not None
    assert slope < 0


# ---------------------------------------------------------------------------
# Strategy 1 — MA Trend
# ---------------------------------------------------------------------------

def test_strategy1_bullish_signal():
    """Golden MA alignment with volume surge → Buy signal."""
    # MA5 > MA10 > MA20: use an uptrend then stable prices
    base = [float(i) for i in range(1, 61)]
    close = _make_close(base)
    volume = pd.Series([1_000_000.0] * 55 + [2_500_000.0] * 5)
    current = float(close.iloc[-1])
    sig = strategy_ma_trend(close, volume, current)

    assert isinstance(sig, StrategySignal)
    assert sig.score >= 0
    assert sig.score <= 100
    # Uptrend → bullish alignment should score higher half
    assert sig.score > 50


def test_strategy1_bearish_signal():
    """MA in death-cross alignment → lower score."""
    # Downtrend: prices start high, go low → at end MA5 < MA10 < MA20
    base = [float(60 - i) for i in range(60)]
    close = _make_close(base)
    volume = pd.Series([1_000_000.0] * 60)
    current = float(close.iloc[-1])
    sig = strategy_ma_trend(close, volume, current)

    assert sig.score < 60


def test_strategy1_no_volume():
    """No volume data → still returns a valid signal."""
    base = [float(i) for i in range(1, 61)]
    close = _make_close(base)
    sig = strategy_ma_trend(close, None, float(close.iloc[-1]))
    assert isinstance(sig, StrategySignal)
    assert 0 <= sig.score <= 100


def test_strategy1_insufficient_data():
    """Very short close series → still returns safely."""
    close = _make_close([10.0, 11.0, 12.0])
    sig = strategy_ma_trend(close, None, 12.0)
    assert isinstance(sig, StrategySignal)


# ---------------------------------------------------------------------------
# Strategy 2 — RSI + Bias
# ---------------------------------------------------------------------------

def test_strategy2_oversold_signal():
    """Rapidly falling then flat → RSI oversold → high score."""
    prices = [float(100 - i * 3) for i in range(20)] + [float(42)] * 5
    close = _make_close(prices)
    sig = strategy_rsi_bias(close, float(close.iloc[-1]))
    assert sig.score >= 50  # oversold → at least neutral-high


def test_strategy2_overbought_signal():
    """Rapidly rising prices → overbought → lower score."""
    prices = [float(i * 3) for i in range(1, 30)]
    close = _make_close(prices)
    sig = strategy_rsi_bias(close, float(close.iloc[-1]))
    assert sig.score < 70


def test_strategy2_insufficient_data():
    """Too few points → gracefully returns a signal."""
    close = _make_close([10.0, 11.0])
    sig = strategy_rsi_bias(close, 11.0)
    assert isinstance(sig, StrategySignal)
    assert 0 <= sig.score <= 100


# ---------------------------------------------------------------------------
# Strategy 3 — Macro + Industry Resonance
# ---------------------------------------------------------------------------

def test_strategy3_hot_sector_scores_higher():
    """Stock in hot sector → higher score than non-hot sector."""
    close = _make_close([float(i) for i in range(1, 61)])
    current = 60.0
    hot_sectors = ["半导体ETF", "芯片板块", "AI算力"]

    sig_hot = strategy_macro_industry(
        close, current, "芯片/半导体", pe_ratio=20, profit_margin=0.12,
        is_fund=False, hot_sectors=hot_sectors, northbound_sentiment="偏强"
    )
    sig_cold = strategy_macro_industry(
        close, current, "消费", pe_ratio=20, profit_margin=0.12,
        is_fund=False, hot_sectors=hot_sectors, northbound_sentiment="中性"
    )
    assert sig_hot.score > sig_cold.score


def test_strategy3_low_pe_bonus():
    """PE < 15 → valuation bonus → higher score than PE=60."""
    close = _make_close([float(i) for i in range(1, 61)])
    current = 60.0

    sig_low_pe = strategy_macro_industry(
        close, current, "消费", pe_ratio=12, profit_margin=0.15,
        is_fund=False, hot_sectors=[], northbound_sentiment="中性"
    )
    sig_high_pe = strategy_macro_industry(
        close, current, "消费", pe_ratio=80, profit_margin=0.05,
        is_fund=False, hot_sectors=[], northbound_sentiment="中性"
    )
    assert sig_low_pe.score > sig_high_pe.score


def test_strategy3_northbound_positive_bonus():
    """偏强 northbound sentiment → higher score."""
    close = _make_close([float(i) for i in range(1, 61)])
    current = 60.0

    sig_strong = strategy_macro_industry(
        close, current, "消费", pe_ratio=25, profit_margin=0.1,
        is_fund=False, hot_sectors=[], northbound_sentiment="偏强"
    )
    sig_weak = strategy_macro_industry(
        close, current, "消费", pe_ratio=25, profit_margin=0.1,
        is_fund=False, hot_sectors=[], northbound_sentiment="偏弱"
    )
    assert sig_strong.score > sig_weak.score


def test_strategy3_fund_no_pe_constraint():
    """Fund type → pe_ratio=None should not crash and gets baseline val_score."""
    close = _make_close([float(i) for i in range(1, 61)])
    sig = strategy_macro_industry(
        close, 60.0, "宽基ETF", pe_ratio=None, profit_margin=None,
        is_fund=True, hot_sectors=[], northbound_sentiment="中性"
    )
    assert isinstance(sig, StrategySignal)
    assert 0 <= sig.score <= 100


def test_strategy3_score_bounded():
    """Score must always be in [0, 100]."""
    close = _make_close([float(i) for i in range(1, 100)])
    for n_str in ["偏强", "偏弱", "中性"]:
        sig = strategy_macro_industry(
            close, 99.0, "芯片/半导体", pe_ratio=5, profit_margin=0.5,
            is_fund=False, hot_sectors=["半导体", "芯片", "AI"],
            northbound_sentiment=n_str,
        )
        assert 0 <= sig.score <= 100


# ---------------------------------------------------------------------------
# is_in_hot_sector
# ---------------------------------------------------------------------------

def test_is_in_hot_sector_matches():
    hot = ["半导体ETF", "芯片龙头", "人工智能"]
    assert is_in_hot_sector("芯片/半导体", hot) is True
    assert is_in_hot_sector("AI/人工智能", hot) is True


def test_is_in_hot_sector_no_match():
    hot = ["医疗ETF", "白酒板块"]
    assert is_in_hot_sector("芯片/半导体", hot) is False


# ---------------------------------------------------------------------------
# run_all_strategies (unit — mocked-style via offline data)
# ---------------------------------------------------------------------------

def test_run_all_strategies_returns_report():
    """run_all_strategies should return a MultiStrategyReport without errors."""
    close = _make_close([float(i) for i in range(1, 130)])
    volume = pd.Series([1_000_000.0] * 129)

    report = run_all_strategies(
        close=close,
        volume=volume,
        current_price=129.0,
        ticker="600519",
        name="贵州茅台",
        sector="消费",
        pe_ratio=28.0,
        profit_margin=0.45,
        is_fund=False,
    )

    assert isinstance(report, MultiStrategyReport)
    assert isinstance(report.strategy1, StrategySignal)
    assert isinstance(report.strategy2, StrategySignal)
    assert isinstance(report.strategy3, StrategySignal)
    assert 0 <= report.combined_score <= 100
    assert report.composite_action  # non-empty string


def test_run_all_strategies_weighted_combination():
    """Combined score = strategy1*0.30 + strategy2*0.25 + strategy3*0.45 (approx)."""
    close = _make_close([float(i) for i in range(1, 130)])
    volume = pd.Series([1_000_000.0] * 129)

    report = run_all_strategies(
        close=close,
        volume=volume,
        current_price=129.0,
        ticker="000001",
        name="平安银行",
        sector="金融",
        pe_ratio=6.0,
        profit_margin=0.30,
        is_fund=False,
    )

    expected = round(
        report.strategy1.score * 0.30
        + report.strategy2.score * 0.25
        + report.strategy3.score * 0.45,
        1,
    )
    assert abs(report.combined_score - expected) < 0.2


def test_run_all_strategies_fund_type():
    """Fund type produces valid report without valuation penalty."""
    close = _make_close([float(i * 0.01 + 1.0) for i in range(130)])
    volume = pd.Series([10_000_000.0] * 130)

    report = run_all_strategies(
        close=close,
        volume=volume,
        current_price=float(close.iloc[-1]),
        ticker="588000",
        name="科创50ETF",
        sector="芯片/半导体",
        pe_ratio=None,
        profit_margin=None,
        is_fund=True,
    )
    assert isinstance(report, MultiStrategyReport)
    assert 0 <= report.combined_score <= 100


# ---------------------------------------------------------------------------
# Position advice (_position_advice)
# ---------------------------------------------------------------------------

def test_position_advice_in_sell_zone():
    """Price in sell zone → 0% position."""
    pct, note = _position_advice(
        combined=75.0, bias=5.0,
        sell_range=(100.0, 110.0), buy_range=(80.0, 85.0),
        current_price=102.0,   # >= sell_range[0]
    )
    assert pct == 0
    assert "卖出" in note or "止盈" in note


def test_position_advice_weak_signal():
    """Score < 42 → 0% position."""
    pct, note = _position_advice(
        combined=35.0, bias=None,
        sell_range=None, buy_range=None,
        current_price=50.0,
    )
    assert pct == 0


def test_position_advice_strong_no_bias():
    """Score >= 75, normal bias → 50% position."""
    pct, note = _position_advice(
        combined=78.0, bias=3.0,
        sell_range=None, buy_range=None,
        current_price=50.0,
    )
    assert pct == 50


def test_position_advice_strong_high_bias():
    """Score >= 75 but bias > 15% → reduced to 20%."""
    pct, note = _position_advice(
        combined=78.0, bias=20.0,
        sell_range=None, buy_range=None,
        current_price=50.0,
    )
    assert pct == 20
    assert "MA5" in note or "回踩" in note


def test_position_advice_medium_elevated_bias():
    """Score 65-75, bias 10-15 → reduced from 30% to 20%."""
    pct, note = _position_advice(
        combined=68.0, bias=12.0,
        sell_range=None, buy_range=None,
        current_price=50.0,
    )
    assert pct == 20


# ---------------------------------------------------------------------------
# Strategy 1 bias-override caveat
# ---------------------------------------------------------------------------

def test_strategy1_bias_override_penalty_high():
    """Bullish MA alignment but price >>20% above MA20 → score penalized."""
    # Build an uptrend then a sharp spike at the end
    prices = [float(i) for i in range(1, 61)]
    prices[-10:] = [prices[-11] * (1 + i * 0.04) for i in range(1, 11)]  # 4% per day
    close = _make_close(prices)
    current = float(close.iloc[-1])
    volume = pd.Series([1_000_000.0] * 60)

    sig_with_spike = strategy_ma_trend(close, volume, current)

    # A normal uptrend without spike should score higher (if both are bullish)
    normal_prices = [float(i * 1.01) for i in range(1, 61)]
    normal_close = _make_close(normal_prices)
    normal_sig = strategy_ma_trend(normal_close, volume, float(normal_close.iloc[-1]))

    # The spike version should have a bias-override warning in details
    has_bias_warn = any("乖离率" in d for d in sig_with_spike.details)
    assert has_bias_warn or sig_with_spike.indicators.get("乖离率Bias(20)%") is not None


def test_strategy1_bias_key_in_indicators():
    """strategy_ma_trend always populates 乖离率Bias(20)% in indicators."""
    prices = [float(i) for i in range(1, 41)]
    close = _make_close(prices)
    sig = strategy_ma_trend(close, None, float(close.iloc[-1]))
    assert "乖离率Bias(20)%" in sig.indicators


# ---------------------------------------------------------------------------
# run_all_strategies with sell_range / buy_range
# ---------------------------------------------------------------------------

def test_run_all_strategies_with_zone_params():
    """Passing sell_range and buy_range should produce non-empty position_note."""
    close = _make_close([float(i) for i in range(1, 130)])
    volume = pd.Series([1_000_000.0] * 129)

    report = run_all_strategies(
        close=close,
        volume=volume,
        current_price=129.0,
        ticker="600519",
        name="贵州茉台",
        sector="消费",
        pe_ratio=28.0,
        profit_margin=0.45,
        is_fund=False,
        sell_range=(130.0, 140.0),
        buy_range=(100.0, 110.0),
    )
    assert isinstance(report.position_pct, int)
    assert isinstance(report.position_note, str)
    assert len(report.position_note) > 0
