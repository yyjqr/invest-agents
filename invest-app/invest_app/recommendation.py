"""Single-stock/fund recommendation engine with differentiated strategies.

- Stocks: support/resistance zones from MA & extremes, sector context, SWOT
- Funds (ETF/LOF): percentile-based buy/sell bands suited to narrow ranges
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from invest_app.industry import (
    BenchmarkSnapshot,
    SWOTAnalysis,
    build_swot,
    classify_sector,
    estimate_sector_maturity,
    fetch_benchmark_snapshots,
    get_sector_benchmarks,
)
from invest_app.market_data import _classify_cn_code, get_history, get_quote
from invest_app.strategies import MultiStrategyReport, run_all_strategies


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StockRecommendation:
    ticker: str
    name: str
    current_price: float
    period: str
    instrument_type: str  # "stock" | "fund" | "star"
    sector: str
    sector_maturity: str
    ma20: float | None
    ma60: float | None
    high_3m: float | None
    low_3m: float | None
    high_6m: float | None
    low_6m: float | None
    volume_ratio: float | None
    pe_ratio: float | None
    profit_margin: float | None
    is_profitable: bool | None
    market_view: str
    valuation_view: str
    action: str
    buy_range: tuple[float, float] | None
    sell_range: tuple[float, float] | None
    stop_loss: float | None
    reasons: list[str]
    swot: SWOTAnalysis | None = None
    benchmarks: list[BenchmarkSnapshot] = field(default_factory=list)
    strategies: MultiStrategyReport | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_value(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    value = series.tail(window).mean()
    if pd.isna(value):
        return None
    return float(value)


def _window_extreme(series: pd.Series, window: int, kind: str) -> float | None:
    if len(series) < max(5, window // 4):
        return None
    data = series.tail(window)
    value = data.max() if kind == "max" else data.min()
    if pd.isna(value):
        return None
    return float(value)


def _market_view(
    volume_ratio: float | None,
    current: float,
    ma20: float | None,
    ma60: float | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if ma20 and ma60:
        if current > ma20 > ma60:
            reasons.append("价格位于20/60日均线上方，趋势偏强")
        elif current < ma20 < ma60:
            reasons.append("价格位于20/60日均线下方，趋势偏弱")
        else:
            reasons.append("均线结构中性，等待方向确认")

    if volume_ratio is not None:
        if volume_ratio >= 1.3:
            reasons.append("近期成交量放大，资金活跃度较高")
            return "偏强", reasons
        if volume_ratio <= 0.8:
            reasons.append("近期成交量偏低，资金活跃度一般")
            return "偏弱", reasons

    return "中性", reasons


def _valuation_view(
    pe_ratio: float | None, profit_margin: float | None
) -> tuple[str, bool | None, list[str]]:
    reasons: list[str] = []

    profitable: bool | None = None
    if profit_margin is not None:
        profitable = profit_margin > 0
        if profitable:
            reasons.append(f"利润率为正（{profit_margin * 100:.1f}%）")
        else:
            reasons.append(f"利润率为负（{profit_margin * 100:.1f}%）")

    if pe_ratio is None:
        reasons.append("市盈率数据不足")
        return "中性", profitable, reasons

    if pe_ratio <= 15:
        reasons.append(f"市盈率较低（PE={pe_ratio:.1f}）")
        return "偏低估", profitable, reasons
    if pe_ratio <= 30:
        reasons.append(f"市盈率中等（PE={pe_ratio:.1f}）")
        return "合理", profitable, reasons

    reasons.append(f"市盈率偏高（PE={pe_ratio:.1f}）")
    return "偏高估", profitable, reasons


# ---------------------------------------------------------------------------
# Fund-specific strategy (percentile bands)
# ---------------------------------------------------------------------------

def _fund_buy_sell_zones(
    close: pd.Series, current: float
) -> tuple[
    tuple[float, float] | None,
    tuple[float, float] | None,
    float | None,
    list[str],
]:
    """Compute buy/sell zones for funds using percentile bands.

    Funds like 588000 trade in a narrow range; classic support/resistance
    creates zones that are rarely reached. Instead we use:
      - Buy zone:  20th–35th percentile of recent closes
      - Sell zone: 75th–90th percentile
      - Stop loss: 10th percentile
    """
    reasons: list[str] = []

    if len(close) < 20:
        return None, None, None, ["数据不足，无法计算分位区间"]

    arr = close.values.astype(float)
    p10 = float(np.percentile(arr, 10))
    p20 = float(np.percentile(arr, 20))
    p35 = float(np.percentile(arr, 35))
    p50 = float(np.percentile(arr, 50))
    p75 = float(np.percentile(arr, 75))
    p90 = float(np.percentile(arr, 90))

    buy_range = (round(p20, 4), round(p35, 4))
    sell_range = (round(p75, 4), round(p90, 4))
    stop_loss = round(p10, 4)

    reasons.append(
        f"基金分位策略：中位数 ¥{p50:.4f}，"
        f"买入区 P20-P35，卖出区 P75-P90"
    )

    if current <= buy_range[1]:
        reasons.append("当前价格处于或低于买入分位区，性价比较高")
    elif current >= sell_range[0]:
        reasons.append("当前价格处于或高于卖出分位区，可考虑分批止盈")
    else:
        reasons.append("当前价格处于中间区域，可持有等待")

    return buy_range, sell_range, stop_loss, reasons


# ---------------------------------------------------------------------------
# Stock-specific strategy (support/resistance)
# ---------------------------------------------------------------------------

def _stock_buy_sell_zones(
    close: pd.Series,
    current: float,
    ma20: float | None,
    ma60: float | None,
) -> tuple[
    tuple[float, float] | None,
    tuple[float, float] | None,
    float | None,
    list[str],
]:
    """Compute buy/sell zones for stocks using MA + extremes."""
    reasons: list[str] = []

    high_3m = _window_extreme(close, 63, "max")
    low_3m = _window_extreme(close, 63, "min")
    high_6m = _window_extreme(close, 126, "max")
    low_6m = _window_extreme(close, 126, "min")

    support_candidates = [x for x in [ma20, ma60, low_3m, low_6m] if x is not None]
    resistance_candidates = [x for x in [high_3m, high_6m] if x is not None]

    support = min(support_candidates) if support_candidates else None
    resistance = max(resistance_candidates) if resistance_candidates else None

    buy_range = None
    sell_range = None
    stop_loss = None

    if support is not None:
        buy_range = (round(support * 0.98, 2), round(support * 1.02, 2))
        stop_loss = round(support * 0.94, 2)
        reasons.append(f"支撑位参考：¥{support:.2f}（取均线与区间低点）")

    if resistance is not None:
        sell_range = (round(resistance * 0.97, 2), round(resistance * 1.03, 2))
        reasons.append(f"压力位参考：¥{resistance:.2f}（取区间高点）")

    return buy_range, sell_range, stop_loss, reasons


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def recommend_stock(
    ticker: str,
    period: str = "6mo",
    pe_ratio: float | None = None,
    profit_margin: float | None = None,
) -> StockRecommendation:
    """Generate buy/sell guidance for one stock or fund.

    Automatically detects fund vs stock and applies the appropriate strategy.
    """
    quote = get_quote(ticker)
    history = get_history(ticker, period=period, interval="1d")
    if history.empty or "Close" not in history.columns:
        raise ValueError(f"No enough historical data for {ticker}")

    close = history["Close"].dropna()
    if close.empty:
        raise ValueError(f"No close price data for {ticker}")

    current = float(quote.price)

    # Detect instrument type
    raw_code = ticker.strip().upper().split(".", maxsplit=1)[0]
    instrument_type = _classify_cn_code(raw_code, quote.name)  # "fund" | "star" | "stock"
    is_fund = instrument_type == "fund"

    # Sector & maturity
    sector = classify_sector(raw_code, quote.name)
    sector_maturity = estimate_sector_maturity(sector)

    # Technical indicators
    ma20 = _last_value(close, 20)
    ma60 = _last_value(close, 60)
    high_3m = _window_extreme(close, 63, "max")
    low_3m = _window_extreme(close, 63, "min")
    high_6m = _window_extreme(close, 126, "max")
    low_6m = _window_extreme(close, 126, "min")

    volume_ratio: float | None = None
    if "Volume" in history.columns and not history["Volume"].dropna().empty:
        vol = history["Volume"].dropna()
        if len(vol) >= 20:
            avg20 = float(vol.tail(20).mean())
            last_vol = float(vol.iloc[-1])
            if avg20 > 0:
                volume_ratio = last_vol / avg20

    market_view, market_reasons = _market_view(volume_ratio, current, ma20, ma60)
    valuation_view, profitable, valuation_reasons = _valuation_view(pe_ratio, profit_margin)

    # Strategy-specific buy/sell zones
    if is_fund:
        buy_range, sell_range, stop_loss, zone_reasons = _fund_buy_sell_zones(close, current)
    else:
        buy_range, sell_range, stop_loss, zone_reasons = _stock_buy_sell_zones(
            close, current, ma20, ma60
        )

    # Action decision
    action = "观望"
    reasons: list[str] = []

    if buy_range and current <= buy_range[1] and market_view != "偏弱":
        action = "可分批买入"
        reasons.append("当前价格接近买入区")
    elif sell_range and current >= sell_range[0]:
        action = "可分批止盈"
        reasons.append("当前价格接近卖出区")
    elif market_view == "偏弱":
        action = "等待企稳"
        reasons.append("趋势与成交量偏弱")
    else:
        action = "持有观察"
        reasons.append("价格处于区间中部")

    if valuation_view == "偏高估":
        reasons.append("估值偏高，追高需谨慎")
    elif valuation_view == "偏低估":
        reasons.append("估值相对不高，可关注回调买点")

    if profitable is False:
        reasons.append("公司盈利能力偏弱，建议降低仓位")

    reasons.extend(zone_reasons)
    reasons.extend(market_reasons)
    reasons.extend(valuation_reasons)

    # Three-strategy analysis
    volume_series = (
        history["Volume"].dropna()
        if "Volume" in history.columns
        else None
    )
    strategy_report = run_all_strategies(
        close=close,
        volume=volume_series,
        current_price=current,
        ticker=quote.ticker,
        name=quote.name,
        sector=sector,
        pe_ratio=pe_ratio,
        profit_margin=profit_margin,
        is_fund=is_fund,
        sell_range=sell_range,
        buy_range=buy_range,
    )

    # Incorporate composite strategy signal into action & reasons
    combined = strategy_report.combined_score
    if combined >= 70:
        if action == "等待企稳" or action == "观望":
            action = strategy_report.composite_action
        reasons.insert(0, f"📊 策略综合评分 {combined:.0f}/100 → {strategy_report.composite_action}")
    elif combined >= 58:
        reasons.insert(0, f"📊 策略综合评分 {combined:.0f}/100 → 观望偏多，信号待确认")
    else:
        reasons.insert(0, f"📊 策略综合评分 {combined:.0f}/100")

    # Cross-market benchmarks (best-effort, non-blocking)
    sector_benchmarks = get_sector_benchmarks(sector)
    benchmarks: list[BenchmarkSnapshot] = []
    if sector_benchmarks:
        try:
            benchmarks = fetch_benchmark_snapshots(sector_benchmarks)
            for bm in benchmarks:
                if bm.price is not None:
                    reasons.append(
                        f"关联标的 {bm.label}({bm.ticker}): "
                        f"{bm.change_pct:+.2f}% → {bm.trend}"
                    )
        except Exception:
            pass  # non-critical

    # SWOT
    swot = build_swot(
        current_price=current,
        ma20=ma20, ma60=ma60,
        high_6m=high_6m, low_6m=low_6m,
        volume_ratio=volume_ratio,
        pe_ratio=pe_ratio,
        profit_margin=profit_margin,
        sector=sector,
        is_fund=is_fund,
        benchmarks=benchmarks,
    )

    # Sector maturity context
    reasons.append(f"行业「{sector}」当前处于「{sector_maturity}」阶段")

    return StockRecommendation(
        ticker=quote.ticker,
        name=quote.name,
        current_price=current,
        period=period,
        instrument_type=instrument_type,
        sector=sector,
        sector_maturity=sector_maturity,
        ma20=ma20,
        ma60=ma60,
        high_3m=high_3m,
        low_3m=low_3m,
        high_6m=high_6m,
        low_6m=low_6m,
        volume_ratio=volume_ratio,
        pe_ratio=pe_ratio,
        profit_margin=profit_margin,
        is_profitable=profitable,
        market_view=market_view,
        valuation_view=valuation_view,
        action=action,
        buy_range=buy_range,
        sell_range=sell_range,
        stop_loss=stop_loss,
        reasons=reasons,
        swot=swot,
        benchmarks=benchmarks,
        strategies=strategy_report,
    )
