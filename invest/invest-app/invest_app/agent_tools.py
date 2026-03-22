"""Tool wrappers for the Fin-Agent workflow.

Three layers:
  搜 (Search): agent_search module           → news, announcements, events
  控 (Control): agent_graph module            → LangGraph orchestration
  算 (Compute): this module wraps existing    → forecast, strategy, recommendation

These helpers normalize the inputs required by the existing deterministic
modules so that the graph layer can operate on a consistent state contract.

Red-blue adversarial tools:
  bull_case_tool  — builds the bullish argument
  bear_case_tool  — builds the bearish argument
  judge_tool      — reconciles both sides
"""

from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any

import pandas as pd

from invest_app.agent_state import (
    AgentHorizon,
    CaseArgument,
    Confidence,
    JudgeVerdict,
    MarketDataState,
    Verdict,
)
from invest_app.forecast import build_short_term_forecast
from invest_app.industry import classify_sector
from invest_app.market_data import (
    _classify_cn_code,
    get_fundamentals,
    get_history,
    get_intraday,
    get_quote,
    resolve_ticker,
)
from invest_app.recommendation import recommend_stock
from invest_app.strategies import run_all_strategies


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_dataclass(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    raise TypeError(f"Unsupported value for serialization: {type(value)!r}")


def _serialize_frame(frame: pd.DataFrame, limit: int = 120) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    subset = frame.tail(limit).copy()
    subset = subset.reset_index()
    rows: list[dict[str, Any]] = []
    for row in subset.to_dict(orient="records"):
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()
            elif pd.isna(value):
                normalized[key] = None
            else:
                normalized[key] = value
        rows.append(normalized)
    return rows


# ---------------------------------------------------------------------------
# 算 Layer: Market data
# ---------------------------------------------------------------------------

def market_data_tool(
    ticker: str,
    *,
    period: str = "6mo",
    history_interval: str = "1d",
    include_intraday: bool = True,
) -> MarketDataState:
    """Load the market context needed by downstream agent nodes."""
    resolved_ticker = resolve_ticker(ticker)
    quote = get_quote(resolved_ticker)
    history = get_history(resolved_ticker, period=period, interval=history_interval)
    intraday = get_intraday(resolved_ticker) if include_intraday else pd.DataFrame()
    fundamentals = get_fundamentals(resolved_ticker)

    raw_code = resolved_ticker.strip().upper().split(".", maxsplit=1)[0]
    instrument_type = _classify_cn_code(raw_code, quote.name)
    sector = classify_sector(raw_code, quote.name)

    return {
        "ticker": ticker,
        "resolved_ticker": resolved_ticker,
        "name": quote.name,
        "current_price": float(quote.price),
        "quote": _serialize_dataclass(quote),
        "history_period": period,
        "history_interval": history_interval,
        "history": history,
        "history_rows": _serialize_frame(history),
        "intraday": intraday,
        "intraday_rows": _serialize_frame(intraday),
        "fundamentals": fundamentals,
        "sector": sector,
        "instrument_type": instrument_type,
        "is_fund": instrument_type == "fund",
    }


# ---------------------------------------------------------------------------
# 算 Layer: Forecast
# ---------------------------------------------------------------------------

def forecast_tool(
    ticker: str,
    *,
    horizon: AgentHorizon = "1d",
    period: str = "6mo",
    market_data: MarketDataState | None = None,
) -> dict[str, Any]:
    """Wrap the short-term forecast engine with a graph-friendly contract."""
    snapshot = market_data or market_data_tool(ticker, period=period)
    forecast = build_short_term_forecast(
        ticker=snapshot["resolved_ticker"],
        name=snapshot["name"],
        history=snapshot["history"],
        current_price=snapshot["current_price"],
        horizon=horizon,
        history_period=snapshot.get("history_period", period),
        intraday=snapshot.get("intraday"),
        pe_ratio=snapshot.get("fundamentals", {}).get("pe_ratio"),
        profit_margin=snapshot.get("fundamentals", {}).get("profit_margin"),
        is_fund=bool(snapshot.get("is_fund")),
    )
    return _serialize_dataclass(forecast)


# ---------------------------------------------------------------------------
# 算 Layer: Recommendation
# ---------------------------------------------------------------------------

def recommendation_tool(
    ticker: str,
    *,
    period: str = "6mo",
    market_data: MarketDataState | None = None,
) -> dict[str, Any]:
    """Wrap the recommendation engine for agent consumption."""
    snapshot = market_data or market_data_tool(ticker, period=period)
    recommendation = recommend_stock(
        snapshot["resolved_ticker"],
        period=period,
        pe_ratio=snapshot.get("fundamentals", {}).get("pe_ratio"),
        profit_margin=snapshot.get("fundamentals", {}).get("profit_margin"),
    )
    return _serialize_dataclass(recommendation)


# ---------------------------------------------------------------------------
# 算 Layer: Multi-strategy
# ---------------------------------------------------------------------------

def strategy_tool(
    ticker: str,
    *,
    period: str = "6mo",
    market_data: MarketDataState | None = None,
    recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap the multi-strategy engine with consistent input preparation."""
    snapshot = market_data or market_data_tool(ticker, period=period)
    recommendation_data = recommendation or recommendation_tool(
        ticker, period=period, market_data=snapshot,
    )

    history = snapshot["history"]
    close = history["Close"].dropna().astype(float)
    volume = history["Volume"].dropna().astype(float) if "Volume" in history.columns else None

    report = run_all_strategies(
        close=close,
        volume=volume,
        current_price=snapshot["current_price"],
        ticker=snapshot["resolved_ticker"],
        name=snapshot["name"],
        sector=snapshot["sector"],
        pe_ratio=snapshot.get("fundamentals", {}).get("pe_ratio"),
        profit_margin=snapshot.get("fundamentals", {}).get("profit_margin"),
        is_fund=bool(snapshot.get("is_fund")),
        sell_range=recommendation_data.get("sell_range"),
        buy_range=recommendation_data.get("buy_range"),
    )
    return _serialize_dataclass(report)


# ---------------------------------------------------------------------------
# 控 Layer: Red-Blue Adversarial Tools
# ---------------------------------------------------------------------------

def _score_to_verdict(score: float) -> Verdict:
    """Map a 0-100 score to a Verdict enum."""
    if score >= 80:
        return "strong_buy"
    elif score >= 60:
        return "buy"
    elif score >= 40:
        return "hold"
    elif score >= 20:
        return "sell"
    return "strong_sell"


def _score_to_confidence(score: float, spread: float) -> Confidence:
    """Derive confidence from score extremity and bull/bear spread."""
    extremity = abs(score - 50)
    if extremity > 25 and spread < 30:
        return "high"
    elif extremity > 15:
        return "medium"
    return "low"


def bull_case_tool(
    *,
    market_data: MarketDataState,
    forecast: dict[str, Any],
    strategies: dict[str, Any],
    recommendation: dict[str, Any],
    news: list[dict[str, Any]],
) -> CaseArgument:
    """Build the BULLISH case by emphasizing positive factors.

    This is the "red team" (看多方) in the adversarial framework.
    """
    arguments: list[str] = []
    catalysts: list[str] = []
    risk_flags: list[str] = []
    score_components: list[float] = []
    factor_weights: dict[str, float] = {}

    price = market_data.get("current_price", 0)
    fundamentals = market_data.get("fundamentals", {})

    # ── Forecast signals (weight: 30%) ──
    fc_score = forecast.get("confidence", 50)
    trend = forecast.get("trend", "")
    predicted_return = forecast.get("expected_return_pct", 0)

    if predicted_return > 0:
        bull_fc = min(100, 50 + predicted_return * 10)
        arguments.append(f"预测上涨{predicted_return:.2f}%，趋势:{trend}")
    else:
        bull_fc = max(20, 50 + predicted_return * 5)  # Less penalty for bull side
        risk_flags.append(f"模型预测下行{predicted_return:.2f}%")

    score_components.append(bull_fc * 0.30)
    factor_weights["forecast"] = round(bull_fc, 1)

    # ── Strategy signals (weight: 30%) ──
    combined = strategies.get("combined_score", 50)
    s1 = strategies.get("strategy1", {}).get("score", 50)
    s2 = strategies.get("strategy2", {}).get("score", 50)
    s3 = strategies.get("strategy3", {}).get("score", 50)

    # Bull emphasis: weight the highest-scoring strategy more
    best_strategy_score = max(s1, s2, s3)
    bull_strat = combined * 0.6 + best_strategy_score * 0.4

    if combined > 60:
        arguments.append(f"多策略综合评分{combined:.0f}，偏看多")
    if strategies.get("in_hot_sector"):
        catalysts.append(f"当前处于热门板块「{strategies.get('sector', '')}」")
        bull_strat = min(100, bull_strat + 5)
    if strategies.get("northbound_sentiment") == "偏强":
        catalysts.append("北向资金偏强，外资看好")
        bull_strat = min(100, bull_strat + 5)

    score_components.append(bull_strat * 0.30)
    factor_weights["strategy"] = round(bull_strat, 1)

    # ── Recommendation signals (weight: 20%) ──
    action = recommendation.get("action", "")
    buy_range = recommendation.get("buy_range")
    sell_range = recommendation.get("sell_range")

    if "买入" in action:
        bull_rec = 80
        arguments.append(f"推荐动作: {action}")
    elif "持有" in action:
        bull_rec = 60
    else:
        bull_rec = 40
        risk_flags.append(f"推荐系统建议: {action}")

    # Near buy zone is bullish
    if buy_range and price:
        low, high = buy_range
        if low <= price <= high:
            arguments.append(f"当前价格在买入区间 {low:.2f}-{high:.2f}")
            bull_rec = min(100, bull_rec + 10)
        elif price < low:
            arguments.append(f"低于买入区间下沿 {low:.2f}，可能超跌")
            bull_rec = min(100, bull_rec + 5)

    score_components.append(bull_rec * 0.20)
    factor_weights["recommendation"] = round(bull_rec, 1)

    # ── Fundamental signals (weight: 10%) ──
    pe = fundamentals.get("pe_ratio")
    pm = fundamentals.get("profit_margin")
    bull_fund = 50

    if pe and 0 < pe < 25:
        arguments.append(f"PE={pe:.1f}，估值合理偏低")
        bull_fund = 70
    elif pe and pe < 0:
        risk_flags.append("公司净利润为负")
        bull_fund = 30

    if pm and pm > 0.1:
        arguments.append(f"利润率{pm:.1%}，盈利能力较强")
        bull_fund = min(100, bull_fund + 10)

    score_components.append(bull_fund * 0.10)
    factor_weights["fundamentals"] = round(bull_fund, 1)

    # ── News sentiment (weight: 10%) ──
    bull_news = 50
    positive_words = ("上涨", "利好", "增长", "突破", "新高", "加仓", "增持", "超预期")
    for item in (news or [])[:10]:
        title = item.get("title", "")
        if any(w in title for w in positive_words):
            catalysts.append(title[:60])
            bull_news = min(100, bull_news + 5)

    score_components.append(bull_news * 0.10)
    factor_weights["news_sentiment"] = round(bull_news, 1)

    # ── Aggregate ──
    total_score = sum(score_components)
    total_score = max(0, min(100, total_score))

    # Target price: use forecast target or sell range upper
    target = forecast.get("predicted_high") or (sell_range[1] if sell_range else None)
    stop = forecast.get("support_price") or (buy_range[0] if buy_range else None)

    return {
        "side": "bull",
        "verdict": _score_to_verdict(total_score),
        "confidence": _score_to_confidence(total_score, 0),
        "score": round(total_score, 1),
        "target_price": round(target, 2) if target else None,
        "stop_loss": round(stop, 2) if stop else None,
        "key_arguments": arguments[:5],
        "factor_weights": factor_weights,
        "risk_flags": risk_flags[:5],
        "catalysts": catalysts[:5],
        "evidence": {
            "forecast_trend": trend,
            "combined_score": combined,
            "action": action,
        },
    }


def bear_case_tool(
    *,
    market_data: MarketDataState,
    forecast: dict[str, Any],
    strategies: dict[str, Any],
    recommendation: dict[str, Any],
    news: list[dict[str, Any]],
) -> CaseArgument:
    """Build the BEARISH case by emphasizing risk factors.

    This is the "blue team" (看空方) in the adversarial framework.
    """
    arguments: list[str] = []
    catalysts: list[str] = []
    risk_flags: list[str] = []
    score_components: list[float] = []
    factor_weights: dict[str, float] = {}

    price = market_data.get("current_price", 0)
    fundamentals = market_data.get("fundamentals", {})
    history = market_data.get("history")

    # ── Forecast risk (weight: 30%) ──
    predicted_return = forecast.get("expected_return_pct", 0)
    confidence = forecast.get("confidence", 50)

    if predicted_return < 0:
        bear_fc = min(100, 50 + abs(predicted_return) * 10)
        arguments.append(f"模型预测下行{predicted_return:.2f}%")
    else:
        bear_fc = max(20, 50 - predicted_return * 5)
        risk_flags.append(f"模型偏乐观(+{predicted_return:.2f}%)，需警惕反转")

    # Low confidence = bear signal
    if confidence < 50:
        bear_fc = min(100, bear_fc + 10)
        arguments.append(f"模型置信度仅{confidence:.0f}%，不确定性高")

    score_components.append(bear_fc * 0.30)
    factor_weights["forecast_risk"] = round(bear_fc, 1)

    # ── Strategy weakness (weight: 25%) ──
    combined = strategies.get("combined_score", 50)
    s1 = strategies.get("strategy1", {}).get("score", 50)
    s2 = strategies.get("strategy2", {}).get("score", 50)
    s3 = strategies.get("strategy3", {}).get("score", 50)

    # Bear emphasis: weight the weakest strategy
    worst_score = min(s1, s2, s3)
    bear_strat = (100 - combined) * 0.6 + (100 - worst_score) * 0.4

    if combined < 40:
        arguments.append(f"多策略综合仅{combined:.0f}分，技术面偏弱")
    if strategies.get("northbound_sentiment") == "偏弱":
        arguments.append("北向资金偏弱，外资在撤离")
        bear_strat = min(100, bear_strat + 5)

    score_components.append(bear_strat * 0.25)
    factor_weights["strategy_weakness"] = round(bear_strat, 1)

    # ── Price structure risk (weight: 20%) ──
    bear_structure = 50
    sell_range = recommendation.get("sell_range")
    ma20 = recommendation.get("ma20")

    if sell_range and price:
        low, high = sell_range
        if price >= low:
            arguments.append(f"当前价格已达压力区 {low:.2f}-{high:.2f}")
            bear_structure = 75

    if ma20 and price and price < ma20:
        arguments.append(f"股价低于20日均线({ma20:.2f})，中期趋势走弱")
        bear_structure = min(100, bear_structure + 10)

    # Recent drawdown
    if history is not None and not (isinstance(history, pd.DataFrame) and history.empty):
        try:
            close = history["Close"].dropna().astype(float)
            if len(close) >= 20:
                recent_high = float(close.tail(20).max())
                current = float(close.iloc[-1])
                drawdown_pct = (current - recent_high) / recent_high * 100
                if drawdown_pct < -10:
                    arguments.append(f"近20日从高点回撤{drawdown_pct:.1f}%")
                    bear_structure = min(100, bear_structure + 10)
        except Exception:
            pass

    score_components.append(bear_structure * 0.20)
    factor_weights["price_structure"] = round(bear_structure, 1)

    # ── Valuation risk (weight: 15%) ──
    pe = fundamentals.get("pe_ratio")
    pm = fundamentals.get("profit_margin")
    bear_val = 50

    if pe and pe > 50:
        arguments.append(f"PE={pe:.1f}，估值偏高")
        bear_val = 80
    elif pe and pe > 30:
        risk_flags.append(f"PE={pe:.1f}，估值不便宜")
        bear_val = 60

    if pm and pm < 0:
        arguments.append("公司处于亏损状态")
        bear_val = min(100, bear_val + 15)
    elif pm and pm < 0.05:
        risk_flags.append(f"利润率仅{pm:.1%}，盈利能力较弱")
        bear_val = min(100, bear_val + 5)

    score_components.append(bear_val * 0.15)
    factor_weights["valuation_risk"] = round(bear_val, 1)

    # ── Negative news (weight: 10%) ──
    bear_news = 50
    negative_words = ("下跌", "利空", "下滑", "减持", "亏损", "处罚", "违规",
                      "暴雷", "退市", "风险", "做空", "预警", "下调")
    for item in (news or [])[:10]:
        title = item.get("title", "")
        if any(w in title for w in negative_words):
            risk_flags.append(title[:60])
            bear_news = min(100, bear_news + 8)

    score_components.append(bear_news * 0.10)
    factor_weights["negative_news"] = round(bear_news, 1)

    # ── Aggregate ──
    total_score = sum(score_components)
    total_score = max(0, min(100, total_score))

    # Bear target: support level or forecast low
    target = forecast.get("predicted_low") or (recommendation.get("buy_range") or [None])[0]
    # Bear stop: resistance (if you're short, stop above resistance)
    stop = forecast.get("resistance_price") or (sell_range[1] if sell_range else None)

    return {
        "side": "bear",
        "verdict": _score_to_verdict(100 - total_score),  # Invert: high bear score → sell
        "confidence": _score_to_confidence(total_score, 0),
        "score": round(total_score, 1),
        "target_price": round(target, 2) if target else None,
        "stop_loss": round(stop, 2) if stop else None,
        "key_arguments": arguments[:5],
        "factor_weights": factor_weights,
        "risk_flags": risk_flags[:5],
        "catalysts": catalysts[:5],
        "evidence": {
            "forecast_return": forecast.get("expected_return_pct"),
            "combined_score": combined,
            "pe_ratio": pe,
        },
    }


def judge_tool(
    *,
    bull_case: CaseArgument,
    bear_case: CaseArgument,
    market_data: MarketDataState,
    forecast: dict[str, Any],
) -> JudgeVerdict:
    """Reconcile bull and bear cases into a final verdict.

    The judge weighs both sides, identifies consensus and dissent,
    and produces the final actionable decision.
    """
    bull_score = bull_case.get("score", 50)
    bear_score = bear_case.get("score", 50)
    bull_conf = bull_case.get("confidence", "medium")
    bear_conf = bear_case.get("confidence", "medium")

    # Confidence weights
    conf_map = {"high": 1.2, "medium": 1.0, "low": 0.8}
    bull_w = conf_map.get(bull_conf, 1.0)
    bear_w = conf_map.get(bear_conf, 1.0)

    # Historical accuracy bias (will be populated from backtest data)
    try:
        from invest_app.agent_backtest import get_accuracy_stats
        stats = get_accuracy_stats(last_n=50)
        win_rate = stats.get("win_rate", 0.5)
        # If historical accuracy is high, trust the model more
        accuracy_bonus = (win_rate - 0.5) * 10  # ±5 points
    except Exception:
        accuracy_bonus = 0

    # Weighted consensus score (0-100, higher = more bullish)
    total_w = bull_w + bear_w
    consensus = (bull_score * bull_w - bear_score * bear_w + 100 * bear_w) / (total_w + bear_w)
    consensus = consensus + accuracy_bonus
    consensus = max(0, min(100, consensus))

    # Spread analysis
    spread = abs(bull_score - bear_score)
    if spread > 40:
        # Large disagreement → lower confidence
        final_conf: Confidence = "low"
    elif spread > 20:
        final_conf = "medium"
    else:
        # Strong agreement
        final_conf = "high"

    verdict = _score_to_verdict(consensus)

    # Bull/bear agreement weights (how much the judge sides with each)
    bull_weight = bull_score / (bull_score + bear_score) if (bull_score + bear_score) > 0 else 0.5
    bear_weight = 1 - bull_weight

    # Reasoning
    reasoning: list[str] = []
    if bull_score > bear_score + 15:
        reasoning.append(f"多方论据更强(多方{bull_score:.0f} vs 空方{bear_score:.0f})")
    elif bear_score > bull_score + 15:
        reasoning.append(f"空方论据更强(空方{bear_score:.0f} vs 多方{bull_score:.0f})")
    else:
        reasoning.append(f"多空分歧不大(多方{bull_score:.0f} vs 空方{bear_score:.0f})")

    # Forward arguments from winning side
    winner = bull_case if bull_score >= bear_score else bear_case
    loser = bear_case if bull_score >= bear_score else bull_case
    for arg in (winner.get("key_arguments") or [])[:3]:
        reasoning.append(f"✓ {arg}")

    # Dissent: key points from the losing side
    dissent: list[str] = []
    for arg in (loser.get("key_arguments") or [])[:3]:
        dissent.append(arg)

    # Risk warnings from both sides
    risk_warnings = list(bull_case.get("risk_flags") or [])
    risk_warnings.extend(bear_case.get("risk_flags") or [])
    risk_warnings = list(dict.fromkeys(risk_warnings))[:5]  # dedupe

    # Target / stop from winning side
    target = winner.get("target_price") or forecast.get("predicted_price")
    stop = winner.get("stop_loss") or forecast.get("support_price")

    return {
        "final_verdict": verdict,
        "confidence": final_conf,
        "score": round(consensus, 1),
        "bull_weight": round(bull_weight, 3),
        "bear_weight": round(bear_weight, 3),
        "target_price": round(target, 2) if target else None,
        "stop_loss": round(stop, 2) if stop else None,
        "reasoning": reasoning[:5],
        "dissent": dissent[:3],
        "risk_warnings": risk_warnings[:5],
    }


# ---------------------------------------------------------------------------
# 搜 Layer: Search (delegates to agent_search)
# ---------------------------------------------------------------------------

def search_tool(ticker: str, sector: str = "") -> dict[str, list[dict[str, Any]]]:
    """Unified search entry point for the agent graph."""
    from invest_app.agent_search import agent_search
    return agent_search(ticker, sector)