"""State schema for the Fin-Agent workflow.

Designed for LangGraph with red-blue adversarial architecture:
  搜 (Search)  — Perplexica-style news/announcement/event retrieval
  控 (Control) — LangGraph graph orchestration, red-blue adversarial, retry
  算 (Compute) — Deterministic forecast/strategy/recommendation engines

State flows through:
  market_data → search → [bull_case ∥ bear_case] → judge → risk_budget → validate
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal, TypedDict

# ---------------------------------------------------------------------------
# LangGraph reducer helpers (state merge semantics)
# ---------------------------------------------------------------------------

def _list_append(left: list, right: list) -> list:
    """LangGraph reducer: append new items to existing list."""
    return (left or []) + (right or [])


def _dict_merge(left: dict, right: dict) -> dict:
    """LangGraph reducer: shallow merge dicts (right wins on conflict)."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

AgentHorizon = Literal["1h", "1d"]
Verdict = Literal["strong_buy", "buy", "hold", "sell", "strong_sell"]
Confidence = Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------
# Sub-state schemas
# ---------------------------------------------------------------------------

class MarketDataState(TypedDict, total=False):
    ticker: str
    resolved_ticker: str
    name: str
    current_price: float
    quote: dict[str, Any]
    history_period: str
    history_interval: str
    history: Any                    # pd.DataFrame (not serialized)
    history_rows: list[dict[str, Any]]
    intraday: Any
    intraday_rows: list[dict[str, Any]]
    fundamentals: dict[str, Any]
    sector: str
    instrument_type: str
    is_fund: bool


class SearchResult(TypedDict, total=False):
    """Result from Perplexica-style search."""
    query: str
    items: list[dict[str, Any]]     # NewsItem dicts
    sources: list[str]              # URL references
    summary: str                    # LLM-generated digest (if available)


class CaseArgument(TypedDict, total=False):
    """One side of the red-blue adversarial debate."""
    side: Literal["bull", "bear"]
    verdict: Verdict
    confidence: Confidence
    score: float                    # 0-100
    target_price: float | None
    stop_loss: float | None
    key_arguments: list[str]        # top-5 reasons
    factor_weights: dict[str, float]
    risk_flags: list[str]
    catalysts: list[str]            # upcoming events that may move price
    evidence: dict[str, Any]        # raw data references


class JudgeVerdict(TypedDict, total=False):
    """Reconciled output from the judge node."""
    final_verdict: Verdict
    confidence: Confidence
    score: float                    # 0-100 consensus
    bull_weight: float              # 0-1, how much judge agrees with bull
    bear_weight: float              # 0-1, how much judge agrees with bear
    target_price: float | None
    stop_loss: float | None
    reasoning: list[str]            # judge's rationale
    dissent: list[str]              # points of disagreement
    risk_warnings: list[str]


class PortfolioContext(TypedDict, total=False):
    """Snapshot of user's current holdings relevant to this ticker."""
    has_position: bool
    current_shares: float
    avg_cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    portfolio_weight_pct: float     # this position / total portfolio value
    total_portfolio_value: float
    total_positions: int
    sector_exposure_pct: float      # same-sector weight


class RiskBudget(TypedDict, total=False):
    """Position sizing and risk management output."""
    max_position_pct: float         # max % of portfolio for this position
    suggested_shares: int
    suggested_amount: float
    kelly_fraction: float           # Kelly criterion
    position_action: Literal["open", "add", "hold", "reduce", "close"]
    position_pct: int               # 0-100
    position_note: str
    risk_reward_ratio: float
    max_drawdown_pct: float         # historical max drawdown
    volatility_20d: float           # 20-day annualized vol
    var_95: float                   # 95% Value-at-Risk (daily)


class ValidationResult(TypedDict, total=False):
    """Multi-round validation output."""
    passed: bool
    round_number: int
    max_rounds: int
    checks_passed: list[str]
    checks_failed: list[str]
    retry_reason: str | None
    final: bool                     # True if this is the terminal validation


# ---------------------------------------------------------------------------
# Top-level agent state
# ---------------------------------------------------------------------------

class FinAgentState(TypedDict, total=False):
    # ── Input ──
    ticker: str
    horizon: AgentHorizon
    period: str
    history_interval: str
    include_intraday: bool
    include_news: bool

    # ── 算: Compute layer ──
    market_data: MarketDataState
    forecast: dict[str, Any]
    strategies: dict[str, Any]
    recommendation: dict[str, Any]

    # ── 搜: Search layer ──
    search_news: Annotated[list[dict[str, Any]], _list_append]
    search_announcements: Annotated[list[dict[str, Any]], _list_append]
    search_events: Annotated[list[dict[str, Any]], _list_append]

    # ── 控: Red-blue adversarial ──
    bull_case: CaseArgument
    bear_case: CaseArgument
    judge_verdict: JudgeVerdict

    # ── Portfolio & Risk ──
    portfolio_context: PortfolioContext
    risk_budget: RiskBudget

    # ── Validation & Retry ──
    validation: ValidationResult
    validation_round: int           # current round (starts at 0)
    max_validation_rounds: int      # default 3

    # ── Tracing ──
    notes: Annotated[list[str], _list_append]
    errors: Annotated[list[str], _list_append]
    meta: Annotated[dict[str, Any], _dict_merge]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_initial_state(
    ticker: str,
    *,
    horizon: AgentHorizon = "1d",
    period: str = "6mo",
    history_interval: str = "1d",
    include_intraday: bool = True,
    include_news: bool = True,
    max_validation_rounds: int = 3,
) -> FinAgentState:
    """Build the initial state for the agent pipeline."""
    return {
        "ticker": ticker,
        "horizon": horizon,
        "period": period,
        "history_interval": history_interval,
        "include_intraday": include_intraday,
        "include_news": include_news,
        "search_news": [],
        "search_announcements": [],
        "search_events": [],
        "validation_round": 0,
        "max_validation_rounds": max_validation_rounds,
        "notes": [],
        "errors": [],
        "meta": {"started_at": time.time()},
    }