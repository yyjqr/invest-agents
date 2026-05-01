"""Risk budgeting and position sizing for the Fin-Agent.

Implements Kelly criterion, volatility-scaled sizing, max-drawdown guard,
and portfolio-aware position limits. Designed to be called as a graph node
after the judge has reached a verdict.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from invest_app.agent_state import (
    Confidence,
    PortfolioContext,
    RiskBudget,
    Verdict,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum single-position weight by verdict
_MAX_WEIGHT: dict[Verdict, float] = {
    "strong_buy": 0.25,
    "buy": 0.15,
    "hold": 0.10,
    "sell": 0.05,
    "strong_sell": 0.0,
}

# Kelly scaling factor (fractional Kelly for conservatism)
_KELLY_FRACTION = 0.25  # use quarter-Kelly


# ---------------------------------------------------------------------------
# Volatility & Risk metrics
# ---------------------------------------------------------------------------

def calc_volatility_20d(history: pd.DataFrame) -> float:
    """Annualized 20-day realized volatility."""
    close = history["Close"].dropna().astype(float)
    if len(close) < 21:
        return 0.0
    returns = close.pct_change().dropna().tail(20)
    return float(returns.std() * math.sqrt(252))


def calc_max_drawdown(history: pd.DataFrame) -> float:
    """Maximum drawdown (%) over the full history period."""
    close = history["Close"].dropna().astype(float)
    if len(close) < 2:
        return 0.0
    cummax = close.cummax()
    drawdown = (close - cummax) / cummax
    return float(drawdown.min() * 100)  # negative %


def calc_var_95(history: pd.DataFrame) -> float:
    """Daily 95% Value-at-Risk (as negative %)."""
    close = history["Close"].dropna().astype(float)
    if len(close) < 30:
        return 0.0
    returns = close.pct_change().dropna()
    return float(np.percentile(returns, 5) * 100)


# ---------------------------------------------------------------------------
# Kelly criterion
# ---------------------------------------------------------------------------

def kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """Compute Kelly fraction: f* = (p*b - q) / b where b = avg_win/avg_loss.

    Returns a value in [0, 1], already scaled by _KELLY_FRACTION for safety.
    """
    if avg_loss == 0 or avg_win == 0:
        return 0.0
    b = abs(avg_win / avg_loss)
    q = 1 - win_rate
    raw = (win_rate * b - q) / b
    return max(0.0, min(1.0, raw * _KELLY_FRACTION))


def _estimate_win_rate_from_score(score: float) -> float:
    """Map a 0-100 consensus score to an estimated win rate."""
    # Sigmoid-like mapping: 50 → 0.50, 80 → 0.72, 30 → 0.35
    return 1 / (1 + math.exp(-0.05 * (score - 50)))


# ---------------------------------------------------------------------------
# Portfolio context builder
# ---------------------------------------------------------------------------

def build_portfolio_context(
    ticker: str,
    current_price: float,
    sector: str = "",
) -> PortfolioContext:
    """Load user's portfolio and compute context for this ticker."""
    try:
        from invest_app.portfolio import load_portfolio
        portfolio = load_portfolio()
    except Exception:
        return {
            "has_position": False,
            "current_shares": 0.0,
            "avg_cost_basis": 0.0,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "portfolio_weight_pct": 0.0,
            "total_portfolio_value": 0.0,
            "total_positions": 0,
            "sector_exposure_pct": 0.0,
        }

    resolved = ticker.strip().split(".")[0].upper()
    holdings = portfolio.holdings or {}
    holding = holdings.get(resolved) or holdings.get(ticker)

    # Calculate total portfolio value (rough: shares * current_price for each)
    total_value = 0.0
    sector_value = 0.0
    for _t, h in holdings.items():
        est_price = h.avg_cost_basis if h.avg_cost_basis > 0 else 1.0
        pos_value = h.shares * est_price
        total_value += pos_value

    has_position = holding is not None and holding.shares > 0
    current_shares = holding.shares if has_position else 0.0
    avg_cost = holding.avg_cost_basis if has_position else 0.0
    pos_value = current_shares * current_price
    unrealized = pos_value - (current_shares * avg_cost) if has_position else 0.0
    unrealized_pct = (unrealized / (current_shares * avg_cost) * 100) if (has_position and avg_cost > 0) else 0.0
    weight_pct = (pos_value / total_value * 100) if total_value > 0 else 0.0

    return {
        "has_position": has_position,
        "current_shares": current_shares,
        "avg_cost_basis": avg_cost,
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_pnl_pct": round(unrealized_pct, 2),
        "portfolio_weight_pct": round(weight_pct, 2),
        "total_portfolio_value": round(total_value, 2),
        "total_positions": len(holdings),
        "sector_exposure_pct": 0.0,  # TODO: needs sector mapping for all holdings
    }


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def compute_risk_budget(
    *,
    verdict: Verdict,
    confidence: Confidence,
    score: float,
    current_price: float,
    target_price: float | None,
    stop_loss: float | None,
    history: pd.DataFrame,
    portfolio_ctx: PortfolioContext,
) -> RiskBudget:
    """Compute full risk budget and position recommendation."""

    vol_20d = calc_volatility_20d(history)
    max_dd = calc_max_drawdown(history)
    var_95 = calc_var_95(history)

    # ---------- Risk/reward ratio ----------
    if target_price and stop_loss and current_price > 0:
        if verdict in ("strong_sell", "sell"):
            # For sell: reward = drop to target, risk = rise to stop
            upside = abs(current_price - target_price) / current_price
            downside = abs(stop_loss - current_price) / current_price if stop_loss > current_price else 0.01
        else:
            upside = (target_price - current_price) / current_price
            downside = abs(current_price - stop_loss) / current_price
        upside = max(upside, 0.001)
        downside = max(downside, 0.001)
        rr_ratio = upside / downside
    else:
        upside = abs(score - 50) / 100 * 0.1  # rough proxy
        downside = vol_20d / math.sqrt(252) if vol_20d > 0 else 0.05
        upside = max(upside, 0.001)
        downside = max(downside, 0.001)
        rr_ratio = upside / downside

    # ---------- Kelly position sizing ----------
    win_rate = _estimate_win_rate_from_score(score)
    avg_win = upside if upside > 0 else 0.05
    avg_loss = downside if downside > 0 else 0.05
    kf = kelly_fraction(win_rate, avg_win, avg_loss)

    # ---------- Verdict cap ----------
    max_weight = _MAX_WEIGHT.get(verdict, 0.10)

    # Confidence scaling
    conf_scale = {"high": 1.0, "medium": 0.7, "low": 0.4}.get(confidence, 0.5)

    # Volatility penalty: high-vol stocks get smaller positions
    vol_penalty = 1.0
    if vol_20d > 0.6:
        vol_penalty = 0.5
    elif vol_20d > 0.4:
        vol_penalty = 0.7

    # Portfolio concentration guard
    existing_weight = portfolio_ctx.get("portfolio_weight_pct", 0) / 100
    remaining_budget = max(0, max_weight - existing_weight)

    # Final position %
    raw_pct = min(kf, max_weight) * conf_scale * vol_penalty
    final_pct = min(raw_pct, remaining_budget)
    position_pct = int(round(final_pct * 100))

    # Dollar amount & shares
    total_value = portfolio_ctx.get("total_portfolio_value", 0)
    suggested_amount = total_value * final_pct if total_value > 0 else 0.0
    suggested_shares = int(suggested_amount / current_price) if current_price > 0 else 0
    # Round to lots of 100 for A-share
    suggested_shares = (suggested_shares // 100) * 100

    # ---------- Action ----------
    has_pos = portfolio_ctx.get("has_position", False)
    if verdict in ("strong_sell", "sell"):
        action = "close" if has_pos else "hold"
    elif verdict in ("strong_buy", "buy"):
        action = "add" if has_pos else "open"
    else:
        action = "hold"

    # ---------- Position note ----------
    notes = []
    if vol_20d > 0.5:
        notes.append(f"高波动(年化{vol_20d:.0%})，建议控制仓位")
    if existing_weight > 0.15:
        notes.append(f"当前持仓占比{existing_weight:.1%}，注意集中度风险")
    if max_dd < -30:
        notes.append(f"6月最大回撤{max_dd:.1f}%，注意下行风险")
    if rr_ratio < 1.0:
        notes.append("风险收益比<1，谨慎操作")
    if rr_ratio > 3.0:
        notes.append(f"风险收益比{rr_ratio:.1f}，有较好的风险回报")

    note = "；".join(notes) if notes else f"建议仓位{position_pct}%，{action}"

    return {
        "max_position_pct": round(max_weight * 100, 1),
        "suggested_shares": suggested_shares,
        "suggested_amount": round(suggested_amount, 2),
        "kelly_fraction": round(kf, 4),
        "position_action": action,
        "position_pct": position_pct,
        "position_note": note,
        "risk_reward_ratio": round(rr_ratio, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "volatility_20d": round(vol_20d, 4),
        "var_95": round(var_95, 4),
    }
