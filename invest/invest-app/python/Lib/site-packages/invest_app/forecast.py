"""Short-term price forecasting for stocks and funds.

This module provides a lightweight ensemble model using:
  - recent trend / momentum
  - volatility / ATR-like range estimation
  - RSI and bias mean-reversion
  - volume depth: OBV divergence, volume-price divergence
  - capital flow estimation: MFI, intraday money flow proxy
  - MACD histogram direction & crossover
  - Bollinger Band squeeze / breakout
  - KDJ (Stochastic) momentum signal
  - enhanced pivot-based support / resistance
  - PE and profit margin adjustment when available
  - optional personal transaction bias from local portfolio records

The goal is not exact point forecasting, but a practical short-term research
estimate for the next 1 hour or 1 trading day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from invest_app.market_data import (
    _classify_cn_code,
    get_fundamentals,
    get_history,
    get_intraday,
    get_quote,
    resolve_ticker,
)
from invest_app.models import Holding, Portfolio, TransactionType
from invest_app.portfolio import load_portfolio
from invest_app.strategies import _calc_bias, _calc_rsi

ForecastHorizon = Literal["1h", "1d", "1w", "1m", "3m"]


@dataclass
class ForecastScenario:
    """One scenario in a probabilistic forecast."""
    label: str            # "乐观" | "基准" | "悲观"
    probability: float    # e.g. 0.25
    predicted_price: float
    expected_return_pct: float
    description: str      # human-readable short note


@dataclass
class ShortTermForecast:
    """Short-term price forecast for one instrument."""

    ticker: str
    name: str
    horizon: ForecastHorizon
    current_price: float
    predicted_price: float
    predicted_high: float
    predicted_low: float
    trend: str
    confidence: float
    expected_return_pct: float
    expected_range_pct: float
    high_time: str
    low_time: str
    history_period: str = "6mo"
    support_price: float | None = None
    resistance_price: float | None = None
    pe_ratio: float | None = None
    profit_margin: float | None = None
    factors: list[str] = field(default_factory=list)
    model_scores: dict[str, float] = field(default_factory=dict)
    scenarios: list[ForecastScenario] = field(default_factory=list)
    news_sentiment: str = ""          # "偏多" | "偏空" | "中性" | ""
    news_sentiment_score: float = 0.0  # 0-100 raw score from qveris analysis
    # Quantile price bands — derived from range_sigma (80% and 50% CI)
    quantile_10: float | None = None   # 10th pct price (80% CI lower)
    quantile_25: float | None = None   # 25th pct price (50% CI lower)
    quantile_75: float | None = None   # 75th pct price (50% CI upper)
    quantile_90: float | None = None   # 90th pct price (80% CI upper)


def _clip(value: float, lower: float, upper: float) -> float:
    return float(max(lower, min(upper, value)))


def _safe_mean(values: pd.Series, window: int) -> float | None:
    if len(values) < window:
        return None
    result = values.tail(window).mean()
    if pd.isna(result):
        return None
    return float(result)


def _calc_atr_like(history: pd.DataFrame, window: int = 14) -> float | None:
    """Estimate ATR from daily OHLC history."""
    required = {"High", "Low", "Close"}
    if history.empty or not required.issubset(history.columns):
        return None

    high = history["High"].astype(float)
    low = history["Low"].astype(float)
    close = history["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if tr.dropna().empty:
        return None
    atr = tr.tail(window).mean()
    if pd.isna(atr):
        return None
    return float(atr)


def _volume_ratio(history: pd.DataFrame, window: int = 20) -> float | None:
    if "Volume" not in history.columns:
        return None
    vol = history["Volume"].dropna().astype(float)
    if len(vol) < window:
        return None
    avg = float(vol.tail(window).mean())
    if avg <= 0:
        return None
    return float(vol.iloc[-1] / avg)


# ---------------------------------------------------------------------------
# New technical indicator helpers
# ---------------------------------------------------------------------------


def _calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulates volume based on price direction."""
    direction = close.diff().apply(
        lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)
    )
    return (volume * direction).cumsum()


def _obv_divergence_score(
    close: pd.Series, volume: pd.Series, window: int = 20,
) -> tuple[float, str | None]:
    """Detect OBV vs price divergence.

    Returns (score, description).
    Score > 0: OBV supporting price trend (or bullish divergence).
    Score < 0: OBV diverging from price (warning).
    """
    if len(close) < window or len(volume) < window:
        return 0.0, None

    cl = close.tail(window).reset_index(drop=True).astype(float)
    vl = volume.tail(window).reset_index(drop=True).astype(float)
    obv = _calc_obv(cl, vl)

    # Linear regression slope for price and OBV
    x = np.arange(len(cl))
    price_slope = float(np.polyfit(x, cl.values, 1)[0])
    obv_slope = float(np.polyfit(x, obv.values, 1)[0])

    # Normalize slopes
    price_mean = float(cl.mean()) or 1.0
    obv_mean = max(float(obv.abs().mean()), 1.0)
    p_norm = price_slope / price_mean
    o_norm = obv_slope / obv_mean

    # Same direction = confirmation; opposite = divergence
    if p_norm > 0 and o_norm > 0:
        score = _clip(min(p_norm, o_norm) / max(abs(p_norm), 0.001) * 0.6, 0.0, 0.8)
        detail = "OBV与价格同步上升，量能确认涨势"
    elif p_norm < 0 and o_norm < 0:
        score = _clip(max(p_norm, o_norm) / min(abs(p_norm), -0.001) * -0.6,
                       -0.8, 0.0)
        detail = "OBV与价格同步下降，量能确认跌势"
    elif p_norm > 0 and o_norm <= 0:
        score = _clip(-0.5 - abs(o_norm) / max(abs(p_norm), 0.001) * 0.3,
                       -1.0, -0.2)
        detail = "⚠ 价格上涨但OBV下降，量价背离（顶背离）"
    elif p_norm < 0 and o_norm >= 0:
        score = _clip(0.3 + abs(o_norm) / max(abs(p_norm), 0.001) * 0.3,
                       0.2, 1.0)
        detail = "✅ 价格下跌但OBV上升，量价背离（底背离，可能反转）"
    else:
        score = 0.0
        detail = None

    return round(score, 4), detail


def _volume_price_divergence(
    close: pd.Series, volume: pd.Series, window: int = 10,
) -> tuple[float, str | None]:
    """Short-window volume-price trend divergence detector.

    Compares recent volume trend direction vs price trend direction.
    """
    if len(close) < window or len(volume) < window:
        return 0.0, None

    cl = close.tail(window).astype(float)
    vl = volume.tail(window).astype(float)

    price_change = (float(cl.iloc[-1]) - float(cl.iloc[0])) / max(
        float(cl.iloc[0]), 0.01
    )
    vol_change = (float(vl.iloc[-1]) - float(vl.iloc[0])) / max(
        float(vl.iloc[0]), 1.0
    )

    # Price up + volume up = strong, price up + volume down = weak
    if price_change > 0.01 and vol_change > 0.15:
        score = _clip(
            min(price_change, vol_change) * 3, 0.1, 0.8
        )
        detail = f"近期价涨量增（价+{price_change*100:.1f}%，量+{vol_change*100:.0f}%），走势健康"
    elif price_change > 0.01 and vol_change < -0.15:
        score = _clip(-0.3 - abs(vol_change) * 0.5, -0.8, -0.1)
        detail = f"⚠ 价涨量缩（价+{price_change*100:.1f}%，量{vol_change*100:.0f}%），上涨动能不足"
    elif price_change < -0.01 and vol_change > 0.15:
        score = _clip(-0.4 - abs(vol_change) * 0.3, -0.9, -0.2)
        detail = f"价跌量增，恐慌性抛售或主力出货信号"
    elif price_change < -0.01 and vol_change < -0.15:
        score = _clip(0.1 + abs(vol_change) * 0.3, 0.05, 0.5)
        detail = f"价跌量缩，下跌动能衰减，可能接近底部"
    else:
        score = 0.0
        detail = "近期量价变化不显著"

    return round(score, 4), detail


def _calc_macd(
    close: pd.Series,
    fast: int = 12, slow: int = 26, signal_period: int = 9,
) -> tuple[float | None, float | None, float | None]:
    """Compute MACD DIF, DEA, and histogram.

    Returns (dif, dea, histogram) or (None, None, None) if insufficient data.
    """
    if len(close) < slow + signal_period:
        return None, None, None

    cl = close.astype(float)
    ema_fast = cl.ewm(span=fast, adjust=False).mean()
    ema_slow = cl.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal_period, adjust=False).mean()
    histogram = (dif - dea) * 2  # standard MACD bar

    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(histogram.iloc[-1])


def _macd_score(close: pd.Series) -> tuple[float, str | None]:
    """Convert MACD into a normalized forecast factor score."""
    dif, dea, hist = _calc_macd(close)
    if dif is None:
        return 0.0, None

    # Previous histogram for cross detection
    prev_dif, prev_dea, prev_hist = None, None, None
    if len(close) >= 36:
        prev_dif, prev_dea, prev_hist = _calc_macd(close.iloc[:-1])

    score = 0.0
    detail_parts: list[str] = []

    # Histogram direction
    price_scale = float(close.iloc[-1]) or 1.0
    norm_hist = hist / price_scale * 100  # as percentage of price

    if hist > 0:
        score += _clip(norm_hist * 8, 0.05, 0.5)
        detail_parts.append(f"MACD柱状线为正({hist:.4f})")
    else:
        score += _clip(norm_hist * 8, -0.5, -0.05)
        detail_parts.append(f"MACD柱状线为负({hist:.4f})")

    # Golden / death cross detection
    if prev_hist is not None:
        if prev_hist <= 0 < hist:
            score += 0.35
            detail_parts.append("✅ MACD金叉（DIF上穿DEA），短线看多信号")
        elif prev_hist >= 0 > hist:
            score -= 0.35
            detail_parts.append("⚠ MACD死叉（DIF下穿DEA），短线看空信号")

    # DIF above/below zero line
    if dif > 0 and dea > 0:
        score += 0.10
        detail_parts.append("DIF/DEA均在零轴上方，中期偏多")
    elif dif < 0 and dea < 0:
        score -= 0.10
        detail_parts.append("DIF/DEA均在零轴下方，中期偏空")

    return _clip(score, -1.0, 1.0), "；".join(detail_parts) if detail_parts else None


def _calc_bollinger(
    close: pd.Series, window: int = 20, num_std: float = 2.0,
) -> tuple[float | None, float | None, float | None]:
    """Compute Bollinger Bands: (upper, middle, lower)."""
    if len(close) < window:
        return None, None, None

    cl = close.astype(float)
    middle = float(cl.tail(window).mean())
    std = float(cl.tail(window).std(ddof=0))
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def _bollinger_score(close: pd.Series, current_price: float) -> tuple[float, str | None]:
    """Score based on Bollinger Band position and bandwidth."""
    upper, middle, lower = _calc_bollinger(close)
    if upper is None or middle is None or lower is None:
        return 0.0, None

    bandwidth = (upper - lower) / middle if middle else 0.0
    position = (current_price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    score = 0.0
    detail_parts: list[str] = []

    # Position scoring
    if position >= 1.0:
        # Above upper band — overbought
        excess = position - 1.0
        score = _clip(-0.3 - excess * 0.5, -0.8, -0.2)
        detail_parts.append(
            f"价格突破布林上轨(¥{upper:.2f})，短线超买"
        )
    elif position <= 0.0:
        # Below lower band — oversold
        excess = abs(position)
        score = _clip(0.3 + excess * 0.5, 0.2, 0.8)
        detail_parts.append(
            f"价格跌破布林下轨(¥{lower:.2f})，短线超卖，反弹概率较大"
        )
    elif position > 0.8:
        score = _clip(-(position - 0.5) * 0.6, -0.4, -0.05)
        detail_parts.append(f"价格接近布林上轨，偏强但需防回落")
    elif position < 0.2:
        score = _clip((0.5 - position) * 0.6, 0.05, 0.4)
        detail_parts.append(f"价格接近布林下轨，偏弱但有支撑")
    else:
        detail_parts.append(f"价格处于布林带中间区域")

    # Bandwidth squeeze detection (low volatility → breakout coming)
    if bandwidth < 0.04:
        detail_parts.append("⚡ 布林带极度收窄，变盘信号，注意方向突破")
    elif bandwidth < 0.08:
        detail_parts.append("布林带收窄，波动率下降，可能酝酿突破")

    detail = f"布林位置{position*100:.0f}%，带宽{bandwidth*100:.1f}%；" + "；".join(
        detail_parts
    )
    return round(score, 4), detail


def _calc_kdj(
    high: pd.Series, low: pd.Series, close: pd.Series,
    n: int = 9, m1: int = 3, m2: int = 3,
) -> tuple[float | None, float | None, float | None]:
    """Compute KDJ indicator: (K, D, J)."""
    if len(close) < n:
        return None, None, None

    h = high.astype(float)
    lo = low.astype(float)
    cl = close.astype(float)

    lowest = lo.rolling(n, min_periods=n).min()
    highest = h.rolling(n, min_periods=n).max()
    rsv = ((cl - lowest) / (highest - lowest).replace(0, np.nan)) * 100

    k_values = rsv.ewm(com=(m1 - 1), adjust=False).mean()
    d_values = k_values.ewm(com=(m2 - 1), adjust=False).mean()
    j_values = 3 * k_values - 2 * d_values

    k_last = k_values.iloc[-1]
    d_last = d_values.iloc[-1]
    j_last = j_values.iloc[-1]

    if any(pd.isna(v) for v in [k_last, d_last, j_last]):
        return None, None, None

    return float(k_last), float(d_last), float(j_last)


def _kdj_score(
    high: pd.Series, low: pd.Series, close: pd.Series,
) -> tuple[float, str | None]:
    """KDJ momentum factor score."""
    k, d, j = _calc_kdj(high, low, close)
    if k is None:
        return 0.0, None

    score = 0.0
    detail_parts: list[str] = []

    # Oversold / overbought
    if j < 0:
        score += _clip((20 - j) / 40, 0.2, 0.8)
        detail_parts.append(f"KDJ: J值={j:.1f}极度超卖，反弹动能强")
    elif j < 20:
        score += _clip((25 - j) / 50, 0.1, 0.5)
        detail_parts.append(f"KDJ: J值={j:.1f}超卖区域")
    elif j > 100:
        score += _clip((80 - j) / 40, -0.8, -0.2)
        detail_parts.append(f"KDJ: J值={j:.1f}极度超买，回调风险大")
    elif j > 80:
        score += _clip((75 - j) / 50, -0.5, -0.1)
        detail_parts.append(f"KDJ: J值={j:.1f}超买区域")
    else:
        detail_parts.append(f"KDJ: K={k:.1f} D={d:.1f} J={j:.1f}")

    # K/D cross detection
    prev_k, prev_d, _ = _calc_kdj(
        high.iloc[:-1], low.iloc[:-1], close.iloc[:-1],
    ) if len(close) > 10 else (None, None, None)

    if prev_k is not None and prev_d is not None:
        if prev_k <= prev_d and k > d:
            score += 0.25
            detail_parts.append("✅ KDJ金叉（K上穿D），短线动量转强")
        elif prev_k >= prev_d and k < d:
            score -= 0.25
            detail_parts.append("⚠ KDJ死叉（K下穿D），短线动量转弱")

    return _clip(score, -1.0, 1.0), "；".join(detail_parts) if detail_parts else None


def _calc_mfi(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: pd.Series, period: int = 14,
) -> float | None:
    """Money Flow Index (MFI) — volume-weighted RSI.

    MFI combines price and volume for a more reliable overbought/oversold signal.
    Range: 0-100.
    """
    if len(close) < period + 1:
        return None

    typical = (high.astype(float) + low.astype(float) + close.astype(float)) / 3
    money_flow = typical * volume.astype(float)
    delta = typical.diff()

    pos_flow = money_flow.where(delta > 0, 0.0).rolling(period).sum()
    neg_flow = money_flow.where(delta < 0, 0.0).rolling(period).sum()

    ratio = pos_flow / neg_flow.replace(0, np.nan)
    mfi = 100 - (100 / (1 + ratio))

    last = mfi.iloc[-1]
    return float(last) if not pd.isna(last) else None


def _mfi_score(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: pd.Series,
) -> tuple[float, str | None]:
    """Capital flow factor using MFI (Money Flow Index)."""
    mfi = _calc_mfi(high, low, close, volume)
    if mfi is None:
        return 0.0, None

    if mfi < 20:
        score = _clip((25 - mfi) / 20, 0.3, 0.9)
        detail = f"MFI={mfi:.1f}，资金严重流出后超卖，反弹资金回流概率大"
    elif mfi < 30:
        score = _clip((35 - mfi) / 25, 0.1, 0.5)
        detail = f"MFI={mfi:.1f}，资金流出偏多，处于超卖区域"
    elif mfi > 80:
        score = _clip((75 - mfi) / 20, -0.9, -0.3)
        detail = f"MFI={mfi:.1f}，资金大量流入后超买，需防获利了结"
    elif mfi > 70:
        score = _clip((65 - mfi) / 25, -0.5, -0.1)
        detail = f"MFI={mfi:.1f}，资金流入偏多，接近超买"
    else:
        score = 0.0
        detail = f"MFI={mfi:.1f}，资金流向中性"

    return round(score, 4), detail


def _intraday_capital_flow(
    intraday: pd.DataFrame,
) -> tuple[float, str | None]:
    """Estimate intraday capital flow direction from minute-level data.

    Heuristic: when price rises on a bar, that volume is treated as 'buy';
    when price falls, that volume is 'sell'. The net ratio estimates
    capital direction.
    """
    if intraday.empty or "Price" not in intraday.columns or "Volume" not in intraday.columns:
        return 0.0, None

    price = intraday["Price"].astype(float)
    vol = intraday["Volume"].astype(float)

    if len(price) < 10:
        return 0.0, None

    delta = price.diff()
    buy_vol = vol.where(delta > 0, 0.0).sum()
    sell_vol = vol.where(delta < 0, 0.0).sum()
    total = buy_vol + sell_vol

    if total <= 0:
        return 0.0, None

    net_ratio = (buy_vol - sell_vol) / total

    score = _clip(net_ratio * 1.5, -1.0, 1.0)

    if net_ratio > 0.15:
        detail = f"分时资金净流入偏多（买/卖比={buy_vol/max(sell_vol,1):.2f}），主动买盘占优"
    elif net_ratio < -0.15:
        detail = f"分时资金净流出偏多（卖/买比={sell_vol/max(buy_vol,1):.2f}），主动卖盘占优"
    else:
        detail = "分时资金流入流出基本平衡"

    return round(score, 4), detail


def _estimate_support_resistance_pivot(
    high: pd.Series, low: pd.Series, close: pd.Series,
    window: int = 20,
) -> tuple[float | None, float | None]:
    """Enhanced support/resistance using classic pivot point method.

    Uses recent N-day high/low/close to compute pivot-based levels.
    """
    if len(close) < min(window, 5):
        return None, None

    recent_high = high.tail(window).astype(float)
    recent_low = low.tail(window).astype(float)
    recent_close = close.tail(window).astype(float)

    # Classic pivot
    h = float(recent_high.max())
    lo = float(recent_low.min())
    c = float(recent_close.iloc[-1])
    pivot = (h + lo + c) / 3.0

    # S1 = 2*Pivot - High,  R1 = 2*Pivot - Low
    support_1 = 2 * pivot - h
    resist_1 = 2 * pivot - lo

    # Also consider actual recent lows/highs as stronger levels
    # Use the higher of S1 and recent 5-day low as practical support
    recent_5_low = float(low.tail(5).astype(float).min())
    recent_5_high = float(high.tail(5).astype(float).max())

    support = min(support_1, recent_5_low)
    resistance = max(resist_1, recent_5_high)

    return round(support, 4), round(resistance, 4)


def _find_holding(portfolio: Portfolio | None, ticker: str) -> Holding | None:
    if portfolio is None:
        return None

    candidates = {
        ticker.upper(),
        ticker.upper().split(".", maxsplit=1)[0],
    }
    for key, holding in portfolio.holdings.items():
        if key.upper() in candidates or key.upper().split(".", maxsplit=1)[0] in candidates:
            return holding
    return None


def _transaction_signal(holding: Holding | None, current_price: float) -> tuple[float, str | None]:
    """Convert local personal trade records into a mild sentiment adjustment."""
    if holding is None or not holding.transactions:
        return 0.0, "无本地交易记录，交易行为因子置中"

    txs = sorted(holding.transactions, key=lambda item: item.date)[-12:]
    gross_shares = sum(float(tx.shares) for tx in txs)
    if gross_shares <= 0:
        return 0.0, None

    net_shares = sum(
        float(tx.shares) if tx.type == TransactionType.BUY else -float(tx.shares)
        for tx in txs
    )
    net_bias = net_shares / gross_shares

    score = _clip(net_bias * 0.35, -0.25, 0.25)
    if holding.avg_cost_basis > 0:
        cost_gap = (current_price - holding.avg_cost_basis) / holding.avg_cost_basis
        score = _clip(score + _clip(cost_gap / 6, -0.10, 0.10), -0.30, 0.30)

    if score >= 0.08:
        return score, "本地交易记录偏向净买入，持仓行为对短线情绪略偏正面"
    if score <= -0.08:
        return score, "本地交易记录偏向净卖出，持仓行为对短线情绪略偏谨慎"
    return score, "本地交易记录对短线方向影响有限"


def _intraday_signal(
    intraday: pd.DataFrame,
) -> tuple[float, float | None, pd.Timestamp | None, str | None]:
    """Return short intraday momentum score and realized hourly sigma."""
    if intraday.empty or "Price" not in intraday.columns:
        return 0.0, None, None, "无有效分时数据，1小时预测退回日线模型"

    price = intraday["Price"].dropna().astype(float)
    if len(price) < 8:
        last_ts = intraday.index[-1] if len(intraday.index) else None
        return 0.0, None, last_ts, "分时样本偏少，小时级信号可信度一般"

    short_avg = float(price.tail(5).mean())
    base_avg = float(price.tail(min(20, len(price))).mean())
    drift = (short_avg - base_avg) / base_avg if base_avg else 0.0

    ret = price.pct_change().dropna()
    sigma = None
    if not ret.empty:
        sigma = float(ret.tail(min(30, len(ret))).std(ddof=0) * np.sqrt(12))
    last_ts = price.index[-1] if len(price.index) else None

    detail = None
    if drift > 0.002:
        detail = "分时价格重心上移，小时级动量偏强"
    elif drift < -0.002:
        detail = "分时价格重心下移，小时级动量偏弱"
    else:
        detail = "分时价格围绕均价震荡，小时级方向仍待确认"

    return _clip(drift / 0.012, -1.0, 1.0), sigma, last_ts, detail


def _label_trend(signal: float) -> str:
    if signal >= 0.35:
        return "上涨偏多"
    if signal >= 0.10:
        return "震荡偏强"
    if signal > -0.10:
        return "震荡整理"
    if signal > -0.35:
        return "震荡偏弱"
    return "下跌偏空"


def _estimate_support_resistance(close: pd.Series) -> tuple[float | None, float | None]:
    if close.empty:
        return None, None
    support = float(close.tail(min(20, len(close))).min()) if len(close) >= 5 else None
    resistance = float(close.tail(min(20, len(close))).max()) if len(close) >= 5 else None
    return support, resistance


def _time_windows(
    horizon: ForecastHorizon,
    trend: str,
    anchor_time: pd.Timestamp | None,
) -> tuple[str, str]:
    if horizon in ("1w", "1m", "3m"):
        # For weekly/monthly horizons, return approximate window descriptions
        if trend in {"上涨偏多", "震荡偏强"}:
            return "周期末段", "周期前段"
        if trend in {"下跌偏空", "震荡偏弱"}:
            return "周期前段", "周期末段"
        return "周期中段", "周期中段"

    if horizon == "1d":
        if trend in {"上涨偏多", "震荡偏强"}:
            return "14:10-15:00", "10:00-10:40"
        if trend in {"下跌偏空", "震荡偏弱"}:
            return "09:40-10:20", "14:00-15:00"
        return "13:30-14:30", "10:30-11:00"

    base = anchor_time.to_pydatetime() if anchor_time is not None else datetime.now()
    if trend in {"上涨偏多", "震荡偏强"}:
        high_dt = base + timedelta(minutes=45)
        low_dt = base + timedelta(minutes=15)
    elif trend in {"下跌偏空", "震荡偏弱"}:
        high_dt = base + timedelta(minutes=15)
        low_dt = base + timedelta(minutes=45)
    else:
        high_dt = base + timedelta(minutes=35)
        low_dt = base + timedelta(minutes=20)
    return high_dt.strftime("%H:%M"), low_dt.strftime("%H:%M")


def _news_sentiment_score(
    sentiment: str, score: float,
) -> tuple[float, str | None]:
    """Convert news sentiment + score into a forecast factor.

    Bloomberg-style: news sentiment acts as a momentum amplifier/dampener.
    sentiment: "偏多" | "偏空" | "中性"
    score: 0-100 (50 = neutral)
    Returns (factor_score in [-1, 1], detail_text).
    """
    if not sentiment:
        return 0.0, None

    # Normalize score: 50 → 0, 100 → +1, 0 → -1
    normalized = _clip((score - 50) / 50, -1.0, 1.0)

    if sentiment == "偏多":
        factor = _clip(0.15 + normalized * 0.45, 0.0, 0.7)
        detail = f"资讯情绪偏多（得分 {score:.0f}/100），正面信号较强"
    elif sentiment == "偏空":
        factor = _clip(-0.15 + normalized * 0.45, -0.7, 0.0)
        detail = f"资讯情绪偏空（得分 {score:.0f}/100），负面信号需警惕"
    else:
        factor = _clip(normalized * 0.2, -0.3, 0.3)
        detail = f"资讯情绪中性（得分 {score:.0f}/100）"

    return round(factor, 4), detail


def _build_scenarios(
    current: float,
    signal: float,
    range_sigma: float,
    confidence: float,
    trend: str,
    support: float | None,
    resistance: float | None,
) -> list[ForecastScenario]:
    """Build 3-scenario probabilistic forecast (optimistic / base / pessimistic).

    Inspired by BlackRock Aladdin's scenario analysis:
    - Optimistic (25%): signal + 1 sigma upside
    - Base (50%): signal-based prediction
    - Pessimistic (25%): signal - 1 sigma downside
    """
    base_return = _clip(signal * range_sigma * 1.25, -0.08, 0.08)
    base_price = current * (1 + base_return)

    # Optimistic: stronger move in signal direction, capped by resistance
    opt_return = base_return + range_sigma * 0.85
    opt_price = current * (1 + opt_return)
    if resistance and opt_price > resistance * 1.02:
        opt_price = resistance * 1.01
        opt_return = (opt_price / current - 1) if current > 0 else opt_return

    # Pessimistic: adverse move, floored by support
    pes_return = base_return - range_sigma * 0.85
    pes_price = current * (1 + pes_return)
    if support and pes_price < support * 0.98:
        pes_price = support * 0.99
        pes_return = (pes_price / current - 1) if current > 0 else pes_return

    # Probability adjustment: when signal is strong, shift probability
    if abs(signal) > 0.3:
        if signal > 0:
            opt_prob, base_prob, pes_prob = 0.35, 0.45, 0.20
        else:
            opt_prob, base_prob, pes_prob = 0.20, 0.45, 0.35
    else:
        opt_prob, base_prob, pes_prob = 0.25, 0.50, 0.25

    # Build descriptions
    if trend in ("上涨偏多", "震荡偏强"):
        opt_desc = "多因子共振向上，突破关键阻力位可能性较大"
        base_desc = "延续当前偏强趋势，波动区间内运行"
        pes_desc = "上升动能衰减或外部冲击导致短期调整"
    elif trend in ("下跌偏空", "震荡偏弱"):
        opt_desc = "超跌反弹或利好催化带动技术修复"
        base_desc = "延续当前偏弱走势，寻底过程"
        pes_desc = "负面因素叠加，加速下跌风险"
    else:
        opt_desc = "突破盘整区间上沿，资金推动上行"
        base_desc = "维持窄幅震荡，等待方向选择"
        pes_desc = "跌破盘整下沿，进入调整通道"

    return [
        ForecastScenario(
            label="乐观",
            probability=round(opt_prob, 2),
            predicted_price=round(opt_price, 4),
            expected_return_pct=round(opt_return * 100, 2),
            description=opt_desc,
        ),
        ForecastScenario(
            label="基准",
            probability=round(base_prob, 2),
            predicted_price=round(base_price, 4),
            expected_return_pct=round(base_return * 100, 2),
            description=base_desc,
        ),
        ForecastScenario(
            label="悲观",
            probability=round(pes_prob, 2),
            predicted_price=round(pes_price, 4),
            expected_return_pct=round(pes_return * 100, 2),
            description=pes_desc,
        ),
    ]


def build_short_term_forecast(
    *,
    ticker: str,
    name: str,
    history: pd.DataFrame,
    current_price: float,
    horizon: ForecastHorizon = "1d",
    history_period: str = "6mo",
    intraday: pd.DataFrame | None = None,
    pe_ratio: float | None = None,
    profit_margin: float | None = None,
    holding: Holding | None = None,
    is_fund: bool = False,
    news_sentiment: str = "",
    news_sentiment_score: float = 0.0,
) -> ShortTermForecast:
    """Build a short-term forecast from already loaded market data.

    Enhanced ensemble using 14+ factor scores covering:
      - Momentum (short-term & mid-term)
      - Mean-reversion (bias)
      - RSI sentiment
      - Volume depth: simple ratio, OBV divergence, volume-price divergence
      - Capital flow: MFI (Money Flow Index), intraday capital flow
      - MACD: histogram direction & golden/death cross
      - Bollinger Bands: position & squeeze
      - KDJ: stochastic momentum
      - PE / profit margin valuation
      - Personal transaction bias
      - Intraday micro-trend (1h only)
    """
    if history.empty or "Close" not in history.columns:
        raise ValueError(f"{ticker} 缺少历史收盘数据，无法预测")

    close = history["Close"].dropna().astype(float)
    if len(close) < 20:
        raise ValueError(f"{ticker} 历史数据不足（至少需要 20 个交易日）")

    current = float(current_price or close.iloc[-1])
    daily_ret = close.pct_change().dropna()
    sigma_daily = (
        float(daily_ret.tail(min(20, len(daily_ret))).std(ddof=0))
        if not daily_ret.empty
        else 0.0
    )
    sigma_daily = max(sigma_daily, 0.003)

    # --- Existing base factors ---
    rsi = _calc_rsi(close, 14)
    bias = _calc_bias(close, 20)
    vol_ratio = _volume_ratio(history)
    atr = _calc_atr_like(history, 14)

    # --- Enhanced support/resistance (pivot-based) ---
    has_ohlc = {"High", "Low", "Close"}.issubset(history.columns)
    if has_ohlc:
        high_s = history["High"].dropna().astype(float)
        low_s = history["Low"].dropna().astype(float)
        support, resistance = _estimate_support_resistance_pivot(
            high_s, low_s, close,
        )
    else:
        support, resistance = _estimate_support_resistance(close)

    ret_5 = (current / float(close.iloc[-6]) - 1) if len(close) >= 6 else 0.0
    ret_20 = (
        (current / float(close.iloc[-21]) - 1) if len(close) >= 21 else ret_5
    )
    mom_short = _clip(
        ret_5 / max(sigma_daily * np.sqrt(5), 0.01), -1.0, 1.0,
    )
    mom_mid = _clip(
        ret_20 / max(sigma_daily * np.sqrt(20), 0.015), -1.0, 1.0,
    )
    mean_rev = _clip((-(bias or 0.0)) / 12, -1.0, 1.0)

    rsi_score = 0.0
    if rsi is not None:
        if rsi < 30:
            rsi_score = _clip((35 - rsi) / 15, 0.0, 1.0)
        elif rsi > 70:
            rsi_score = _clip(-(rsi - 65) / 15, -1.0, 0.0)
        elif rsi > 58:
            rsi_score = _clip(-(rsi - 58) / 20, -0.6, 0.0)

    # --- Simple volume ratio score (kept but reduced weight) ---
    volume_score = 0.0
    if vol_ratio is not None:
        volume_strength = _clip((vol_ratio - 1.0) / 1.2, -0.5, 1.0)
        direction_hint = 1.0 if mom_short >= 0 else -1.0
        volume_score = volume_strength * direction_hint

    # --- NEW: OBV divergence score ---
    obv_score = 0.0
    obv_detail = None
    if "Volume" in history.columns and len(close) >= 20:
        vol_s = history["Volume"].dropna().astype(float)
        if len(vol_s) >= 20:
            obv_score, obv_detail = _obv_divergence_score(close, vol_s)

    # --- NEW: Volume-price divergence score ---
    vpd_score = 0.0
    vpd_detail = None
    if "Volume" in history.columns and len(close) >= 10:
        vol_s = history["Volume"].dropna().astype(float)
        if len(vol_s) >= 10:
            vpd_score, vpd_detail = _volume_price_divergence(close, vol_s)

    # --- NEW: MACD score ---
    macd_s, macd_detail = _macd_score(close)

    # --- NEW: Bollinger score ---
    boll_s, boll_detail = _bollinger_score(close, current)

    # --- NEW: KDJ score ---
    kdj_s = 0.0
    kdj_detail = None
    if has_ohlc:
        kdj_s, kdj_detail = _kdj_score(high_s, low_s, close)

    # --- NEW: MFI (capital flow) score ---
    mfi_s = 0.0
    mfi_detail = None
    if has_ohlc and "Volume" in history.columns:
        vol_s = history["Volume"].dropna().astype(float)
        if len(vol_s) >= 15 and len(high_s) >= 15:
            mfi_s, mfi_detail = _mfi_score(high_s, low_s, close, vol_s)

    # --- NEW: Intraday capital flow (for 1h) ---
    intra_flow_score = 0.0
    intra_flow_detail = None
    if horizon == "1h" and intraday is not None and not intraday.empty:
        intra_flow_score, intra_flow_detail = _intraday_capital_flow(intraday)

    # --- PE / profit valuation ---
    pe_score = 0.0
    if is_fund:
        pe_score = 0.05
    elif pe_ratio is not None:
        if pe_ratio <= 15:
            pe_score = 0.22
        elif pe_ratio <= 30:
            pe_score = 0.08
        elif pe_ratio >= 60:
            pe_score = -0.22
        elif pe_ratio >= 40:
            pe_score = -0.10

    profit_score = 0.0
    if profit_margin is not None:
        if profit_margin > 0.15:
            profit_score = 0.10
        elif profit_margin < 0:
            profit_score = -0.12

    tx_score, tx_detail = _transaction_signal(holding, current)

    # --- NEW: News sentiment factor ---
    news_s, news_detail = _news_sentiment_score(news_sentiment, news_sentiment_score)

    # --- Intraday micro-trend (1h only) ---
    intra_score = 0.0
    sigma_hour = None
    last_intraday_ts = None
    intraday_detail = None
    if horizon == "1h":
        intra_score, sigma_hour, last_intraday_ts, intraday_detail = (
            _intraday_signal(intraday if intraday is not None else pd.DataFrame())
        )

    # ===================================================================
    # Weighted ensemble — rebalanced for 15 factors
    # ===================================================================
    if horizon == "1h":
        signal = (
            intra_score * 0.20
            + intra_flow_score * 0.09
            + mom_short * 0.09
            + mean_rev * 0.07
            + macd_s * 0.09
            + boll_s * 0.07
            + kdj_s * 0.06
            + rsi_score * 0.06
            + news_s * 0.06
            + obv_score * 0.05
            + vpd_score * 0.04
            + mfi_s * 0.04
            + volume_score * 0.03
            + pe_score * 0.02
            + profit_score * 0.02
            + tx_score * 0.01
        )
        range_sigma = max(
            sigma_hour or 0.0,
            sigma_daily / np.sqrt(4),
            ((atr / current) if atr is not None and current > 0 else 0.0) * 0.30,
            0.0025,
        )
        expected_return_pct = _clip(signal * range_sigma * 1.45, -0.030, 0.030)
        expected_range_pct = _clip(range_sigma * 1.60, 0.004, 0.050)
    elif horizon == "1d":
        signal = (
            mom_short * 0.12
            + mom_mid * 0.09
            + macd_s * 0.12
            + mean_rev * 0.09
            + news_s * 0.08
            + obv_score * 0.07
            + boll_s * 0.07
            + mfi_s * 0.07
            + kdj_s * 0.06
            + vpd_score * 0.05
            + rsi_score * 0.05
            + volume_score * 0.04
            + pe_score * 0.03
            + profit_score * 0.02
            + tx_score * 0.02
        )
        range_sigma = max(
            sigma_daily,
            ((atr / current) if atr is not None and current > 0 else 0.0) * 0.55,
            0.004,
        )
        expected_return_pct = _clip(
            signal * range_sigma * 1.25, -0.080, 0.080,
        )
        expected_range_pct = _clip(range_sigma * 2.10, 0.008, 0.120)
    elif horizon == "1w":
        # 1w: shift toward mid-term momentum + fundamental factors
        signal = (
            mom_mid * 0.14
            + mom_short * 0.08
            + macd_s * 0.12
            + mean_rev * 0.10
            + news_s * 0.09
            + obv_score * 0.08
            + boll_s * 0.06
            + mfi_s * 0.07
            + kdj_s * 0.05
            + vpd_score * 0.04
            + rsi_score * 0.04
            + volume_score * 0.03
            + pe_score * 0.05
            + profit_score * 0.03
            + tx_score * 0.02
        )
        range_sigma = max(
            sigma_daily * np.sqrt(5),
            ((atr / current) if atr is not None and current > 0 else 0.0) * 1.2,
            0.012,
        )
        expected_return_pct = _clip(
            signal * range_sigma * 0.90, -0.15, 0.15,
        )
        expected_range_pct = _clip(range_sigma * 1.80, 0.02, 0.25)
    elif horizon == "1m":
        # 1m: fundamental + macro weight increases
        signal = (
            mom_mid * 0.12
            + macd_s * 0.10
            + mean_rev * 0.12
            + news_s * 0.10
            + obv_score * 0.08
            + boll_s * 0.06
            + mfi_s * 0.06
            + pe_score * 0.10
            + profit_score * 0.06
            + mom_short * 0.05
            + kdj_s * 0.04
            + rsi_score * 0.04
            + vpd_score * 0.03
            + volume_score * 0.02
            + tx_score * 0.02
        )
        range_sigma = max(
            sigma_daily * np.sqrt(22),
            ((atr / current) if atr is not None and current > 0 else 0.0) * 2.5,
            0.03,
        )
        expected_return_pct = _clip(
            signal * range_sigma * 0.65, -0.25, 0.25,
        )
        expected_range_pct = _clip(range_sigma * 1.60, 0.05, 0.40)
    else:
        # 3m: long-term fundamental-dominant
        signal = (
            pe_score * 0.15
            + profit_score * 0.10
            + mean_rev * 0.13
            + mom_mid * 0.10
            + news_s * 0.10
            + macd_s * 0.08
            + obv_score * 0.07
            + boll_s * 0.05
            + mfi_s * 0.05
            + rsi_score * 0.04
            + mom_short * 0.03
            + kdj_s * 0.03
            + vpd_score * 0.03
            + volume_score * 0.02
            + tx_score * 0.02
        )
        range_sigma = max(
            sigma_daily * np.sqrt(66),
            ((atr / current) if atr is not None and current > 0 else 0.0) * 4.5,
            0.06,
        )
        expected_return_pct = _clip(
            signal * range_sigma * 0.45, -0.40, 0.40,
        )
        expected_range_pct = _clip(range_sigma * 1.40, 0.08, 0.60)

    predicted_price = current * (1 + expected_return_pct)

    if signal >= 0.10:
        high_pad = expected_range_pct * 0.62
        low_pad = expected_range_pct * 0.38
    elif signal <= -0.10:
        high_pad = expected_range_pct * 0.38
        low_pad = expected_range_pct * 0.62
    else:
        high_pad = low_pad = expected_range_pct * 0.50

    predicted_high = max(current, predicted_price) * (1 + high_pad)
    predicted_low = min(current, predicted_price) * (1 - low_pad)
    predicted_low = min(predicted_low, current, predicted_price)
    predicted_high = max(predicted_high, current, predicted_price)

    trend = _label_trend(signal)
    high_time, low_time = _time_windows(horizon, trend, last_intraday_ts)

    # Confidence: boost when more factors agree
    factor_agreement = sum(
        1 for s in [
            mom_short, macd_s, boll_s, kdj_s, obv_score, mfi_s, rsi_score, news_s,
        ] if (s > 0.1) == (signal > 0)
    )
    confidence = _clip(
        44
        + abs(signal) * 26
        + min(len(close), 126) / 126 * 8
        + factor_agreement * 2.5
        + (5 if horizon == "1h" and sigma_hour is not None else 0)
        + (3 if news_sentiment else 0),  # bonus for having news context
        38,
        92,
    )

    # ===================================================================
    # Build 3-scenario probabilistic forecast
    # ===================================================================
    scenarios = _build_scenarios(
        current=current,
        signal=signal,
        range_sigma=range_sigma,
        confidence=confidence,
        trend=_label_trend(signal),
        support=support,
        resistance=resistance,
    )

    # ===================================================================
    # Human-readable factor descriptions (up to 10)
    # ===================================================================
    factors: list[str] = []
    if mom_short >= 0.15:
        factors.append(f"近5日动量偏强（{ret_5 * 100:+.2f}%）")
    elif mom_short <= -0.15:
        factors.append(f"近5日动量偏弱（{ret_5 * 100:+.2f}%）")

    if macd_detail:
        factors.append(macd_detail)
    if boll_detail:
        factors.append(boll_detail)
    if kdj_detail:
        factors.append(kdj_detail)

    if obv_detail:
        factors.append(obv_detail)
    if vpd_detail:
        factors.append(vpd_detail)
    if mfi_detail:
        factors.append(mfi_detail)

    if bias is not None:
        if bias <= -6:
            factors.append(
                f"价格低于20日均值较多（Bias={bias:.1f}%），存在均值回归支撑",
            )
        elif bias >= 6:
            factors.append(
                f"价格偏离20日均值偏高（Bias={bias:.1f}%），短线有回吐压力",
            )

    if rsi is not None:
        if rsi < 35:
            factors.append(f"RSI={rsi:.1f}，情绪偏冷，反弹概率有所提升")
        elif rsi > 65:
            factors.append(f"RSI={rsi:.1f}，情绪偏热，需防短线回调")

    if vol_ratio is not None:
        if vol_ratio >= 1.3:
            factors.append(f"量比约 {vol_ratio:.2f}x，成交活跃度提升")
        elif vol_ratio <= 0.8:
            factors.append(f"量比约 {vol_ratio:.2f}x，量能一般")

    if news_detail:
        factors.append(news_detail)
    if intraday_detail and horizon == "1h":
        factors.append(intraday_detail)
    if intra_flow_detail and horizon == "1h":
        factors.append(intra_flow_detail)
    if tx_detail:
        factors.append(tx_detail)
    if pe_ratio is not None:
        factors.append(f"市盈率参考：PE={pe_ratio:.2f}")

    # ===================================================================
    # Model scores dictionary
    # ===================================================================
    model_scores = {
        "trend_signal": round(signal, 4),
        "mom_short": round(mom_short, 4),
        "mom_mid": round(mom_mid, 4),
        "mean_reversion": round(mean_rev, 4),
        "rsi_score": round(rsi_score, 4),
        "volume_ratio": round(volume_score, 4),
        "obv_divergence": round(obv_score, 4),
        "vol_price_div": round(vpd_score, 4),
        "macd_score": round(macd_s, 4),
        "bollinger_score": round(boll_s, 4),
        "kdj_score": round(kdj_s, 4),
        "mfi_score": round(mfi_s, 4),
        "pe_score": round(pe_score, 4),
        "profit_score": round(profit_score, 4),
        "transaction_score": round(tx_score, 4),
        "news_sentiment": round(news_s, 4),
    }
    if horizon == "1h":
        model_scores["intraday_score"] = round(intra_score, 4)
        model_scores["intraday_flow"] = round(intra_flow_score, 4)

    # ===================================================================
    # Quantile price bands (80% and 50% confidence intervals)
    # z10 = norm.ppf(0.90) ≈ 1.2816,  z25 = norm.ppf(0.75) ≈ 0.6745
    # ===================================================================
    _Z90 = 1.2816
    _Z75 = 0.6745
    _q_scale = range_sigma * 0.7  # empirical scaling to match scenario spreads
    q_10 = max(predicted_price * (1 - _Z90 * _q_scale), current * 0.4)
    q_25 = predicted_price * (1 - _Z75 * _q_scale)
    q_75 = predicted_price * (1 + _Z75 * _q_scale)
    q_90 = min(predicted_price * (1 + _Z90 * _q_scale), current * 2.5)

    return ShortTermForecast(
        ticker=ticker,
        name=name,
        horizon=horizon,
        history_period=history_period,
        current_price=round(current, 4),
        predicted_price=round(predicted_price, 4),
        predicted_high=round(predicted_high, 4),
        predicted_low=round(predicted_low, 4),
        trend=trend,
        confidence=round(confidence / 100, 3),
        expected_return_pct=round(expected_return_pct * 100, 2),
        expected_range_pct=round(expected_range_pct * 100, 2),
        high_time=high_time,
        low_time=low_time,
        support_price=round(support, 4) if support is not None else None,
        resistance_price=round(resistance, 4) if resistance is not None else None,
        pe_ratio=pe_ratio,
        profit_margin=profit_margin,
        factors=factors[:10],
        model_scores=model_scores,
        scenarios=scenarios,
        news_sentiment=news_sentiment,
        news_sentiment_score=news_sentiment_score,
        quantile_10=round(q_10, 4),
        quantile_25=round(q_25, 4),
        quantile_75=round(q_75, 4),
        quantile_90=round(q_90, 4),
    )


def forecast_ticker(
    ticker: str,
    horizon: ForecastHorizon = "1d",
    history_period: str = "",
    portfolio: Portfolio | None = None,
) -> ShortTermForecast:
    """Fetch market data and produce one short-term forecast."""
    # Auto-select history period based on horizon if not explicitly set
    if not history_period:
        history_period = {"1h": "6mo", "1d": "6mo", "1w": "1y", "1m": "2y", "3m": "2y"}.get(horizon, "6mo")

    resolved = resolve_ticker(ticker)
    quote = get_quote(resolved)
    history = get_history(resolved, period=history_period, interval="1d")
    intraday = get_intraday(resolved) if horizon == "1h" else pd.DataFrame()

    try:
        fundamentals = get_fundamentals(resolved)
    except Exception:
        fundamentals = {"pe_ratio": None, "profit_margin": None}

    instrument_type = _classify_cn_code(resolved.split(".", maxsplit=1)[0], quote.name)
    holding = _find_holding(portfolio, resolved)

    return build_short_term_forecast(
        ticker=quote.ticker,
        name=quote.name,
        history=history,
        current_price=float(quote.price),
        horizon=horizon,
        history_period=history_period,
        intraday=intraday,
        pe_ratio=fundamentals.get("pe_ratio"),
        profit_margin=fundamentals.get("profit_margin"),
        holding=holding,
        is_fund=instrument_type == "fund",
    )


def forecast_batch(
    tickers: list[str],
    horizon: ForecastHorizon = "1d",
    history_period: str = "6mo",
    portfolio: Portfolio | None = None,
) -> list[ShortTermForecast]:
    """Forecast up to 10 instruments in batch."""
    batch = [t.strip() for t in tickers if t and t.strip()][:10]
    if not batch:
        return []

    pf = portfolio if portfolio is not None else load_portfolio()
    return [
        forecast_ticker(ticker=item, horizon=horizon, history_period=history_period, portfolio=pf)
        for item in batch
    ]
