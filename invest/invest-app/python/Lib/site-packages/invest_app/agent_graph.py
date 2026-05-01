"""LangGraph-based Fin-Agent with red-blue adversarial architecture.

Graph topology:
  START
    → load_market_data      # 获取行情数据
    → load_portfolio         # 加载持仓上下文
    → run_compute            # 运行 forecast + recommendation + strategy
    → run_search             # Perplexica-style 搜新闻/公告/事件
    → [bull_case ∥ bear_case]  # 红蓝对抗: 并行生成多空论点
    → judge                  # 裁决节点: 综合多空, 生成最终判决
    → risk_budget            # 风险预算 + 仓位决策
    → validate               # 多轮验证 → 通过则 END, 否则 retry
    → (retry → run_compute → ... 或 END)

Three layers:
  搜 (Search):  run_search node   → agent_search module
  控 (Control): this graph         → LangGraph orchestration
  算 (Compute): run_compute node  → forecast/strategy/recommendation
"""

from __future__ import annotations

import time
from typing import Any

from invest_app.agent_state import (
    FinAgentState,
    ValidationResult,
    create_initial_state,
)
from invest_app.agent_tools import (
    bear_case_tool,
    bull_case_tool,
    forecast_tool,
    judge_tool,
    market_data_tool,
    recommendation_tool,
    search_tool,
    strategy_tool,
)


# ───────────────────────────────────────────────────────────────────────────
# Node functions
# Each returns a partial state dict that LangGraph merges into the graph state
# ───────────────────────────────────────────────────────────────────────────

def load_market_data_node(state: FinAgentState) -> dict:
    """Node 1: Load all market data for the ticker."""
    try:
        market_data = market_data_tool(
            state["ticker"],
            period=state.get("period", "6mo"),
            history_interval=state.get("history_interval", "1d"),
            include_intraday=state.get("include_intraday", True),
        )
        return {"market_data": market_data}
    except Exception as exc:
        return {"errors": [f"load_market_data: {exc}"]}


def load_portfolio_node(state: FinAgentState) -> dict:
    """Node 2: Load portfolio context for position-aware decisions."""
    try:
        from invest_app.agent_risk import build_portfolio_context
        market_data = state.get("market_data", {})
        ctx = build_portfolio_context(
            ticker=state["ticker"],
            current_price=market_data.get("current_price", 0),
            sector=market_data.get("sector", ""),
        )
        return {"portfolio_context": ctx}
    except Exception as exc:
        return {
            "errors": [f"load_portfolio: {exc}"],
            "portfolio_context": {
                "has_position": False,
                "current_shares": 0,
                "avg_cost_basis": 0,
                "unrealized_pnl": 0,
                "unrealized_pnl_pct": 0,
                "portfolio_weight_pct": 0,
                "total_portfolio_value": 0,
                "total_positions": 0,
                "sector_exposure_pct": 0,
            },
        }


def run_compute_node(state: FinAgentState) -> dict:
    """Node 3: Run all deterministic compute engines (算 layer).

    Runs recommendation → forecast → strategy sequentially since
    strategy depends on recommendation output.
    """
    result: dict[str, Any] = {}
    market_data = state.get("market_data")
    period = state.get("period", "6mo")
    errors: list[str] = []

    # Recommendation first (strategy needs buy/sell ranges)
    try:
        rec = recommendation_tool(
            state["ticker"], period=period, market_data=market_data,
        )
        result["recommendation"] = rec
    except Exception as exc:
        errors.append(f"recommendation: {exc}")
        rec = None

    # Forecast
    try:
        fc = forecast_tool(
            state["ticker"],
            horizon=state.get("horizon", "1d"),
            period=period,
            market_data=market_data,
        )
        result["forecast"] = fc
    except Exception as exc:
        errors.append(f"forecast: {exc}")

    # Strategy
    try:
        strat = strategy_tool(
            state["ticker"],
            period=period,
            market_data=market_data,
            recommendation=rec,
        )
        result["strategies"] = strat
    except Exception as exc:
        errors.append(f"strategy: {exc}")

    if errors:
        result["errors"] = errors
    return result


def run_search_node(state: FinAgentState) -> dict:
    """Node 4: Perplexica-style search for news, announcements, events (搜 layer)."""
    if not state.get("include_news", True):
        return {"notes": ["search skipped (include_news=False)"]}

    try:
        sector = (state.get("market_data") or {}).get("sector", "")
        results = search_tool(state["ticker"], sector)
        return {
            "search_news": results.get("news", []) + results.get("macro_events", []),
            "search_announcements": results.get("announcements", []),
            "search_events": results.get("sector_events", []),
        }
    except Exception as exc:
        return {"errors": [f"search: {exc}"]}


def bull_case_node(state: FinAgentState) -> dict:
    """Node 5a: Red team — build the bullish argument."""
    try:
        all_news = (state.get("search_news") or []) + (state.get("search_events") or [])
        case = bull_case_tool(
            market_data=state.get("market_data", {}),
            forecast=state.get("forecast", {}),
            strategies=state.get("strategies", {}),
            recommendation=state.get("recommendation", {}),
            news=all_news,
        )
        return {"bull_case": case}
    except Exception as exc:
        return {"errors": [f"bull_case: {exc}"]}


def bear_case_node(state: FinAgentState) -> dict:
    """Node 5b: Blue team — build the bearish argument."""
    try:
        all_news = (state.get("search_news") or []) + (state.get("search_events") or [])
        case = bear_case_tool(
            market_data=state.get("market_data", {}),
            forecast=state.get("forecast", {}),
            strategies=state.get("strategies", {}),
            recommendation=state.get("recommendation", {}),
            news=all_news,
        )
        return {"bear_case": case}
    except Exception as exc:
        return {"errors": [f"bear_case: {exc}"]}


def judge_node(state: FinAgentState) -> dict:
    """Node 6: Reconcile bull and bear cases into final verdict."""
    try:
        verdict = judge_tool(
            bull_case=state.get("bull_case", {}),
            bear_case=state.get("bear_case", {}),
            market_data=state.get("market_data", {}),
            forecast=state.get("forecast", {}),
        )
        return {"judge_verdict": verdict}
    except Exception as exc:
        return {"errors": [f"judge: {exc}"]}


def risk_budget_node(state: FinAgentState) -> dict:
    """Node 7: Compute risk budget and position sizing."""
    try:
        from invest_app.agent_risk import compute_risk_budget

        verdict_data = state.get("judge_verdict", {})
        market_data = state.get("market_data", {})
        history = market_data.get("history")

        if history is None:
            return {"errors": ["risk_budget: no history data available"]}

        budget = compute_risk_budget(
            verdict=verdict_data.get("final_verdict", "hold"),
            confidence=verdict_data.get("confidence", "medium"),
            score=verdict_data.get("score", 50),
            current_price=market_data.get("current_price", 0),
            target_price=verdict_data.get("target_price"),
            stop_loss=verdict_data.get("stop_loss"),
            history=history,
            portfolio_ctx=state.get("portfolio_context", {}),
        )
        return {"risk_budget": budget}
    except Exception as exc:
        return {"errors": [f"risk_budget: {exc}"]}


def validate_node(state: FinAgentState) -> dict:
    """Node 8: Multi-round validation with retry logic.

    Checks:
      1. Judge confidence ≥ medium
      2. Bull/bear spread not extreme (>60 points)
      3. Risk-reward ratio ≥ 1.0 (for buy/sell verdicts)
      4. No critical errors in pipeline
      5. Forecast and strategy data present
    """
    round_num = state.get("validation_round", 0) + 1
    max_rounds = state.get("max_validation_rounds", 3)

    verdict = state.get("judge_verdict", {})
    bull = state.get("bull_case", {})
    bear = state.get("bear_case", {})
    risk = state.get("risk_budget", {})
    errors = state.get("errors", [])

    checks_passed: list[str] = []
    checks_failed: list[str] = []

    # Check 1: Confidence
    conf = verdict.get("confidence", "low")
    if conf in ("high", "medium"):
        checks_passed.append(f"置信度={conf}")
    else:
        checks_failed.append(f"置信度过低({conf})，决策可靠性不足")

    # Check 2: Bull/bear spread
    spread = abs((bull.get("score", 50)) - (bear.get("score", 50)))
    if spread <= 60:
        checks_passed.append(f"多空分歧={spread:.0f}，在合理范围")
    else:
        checks_failed.append(f"多空分歧过大({spread:.0f})，需要更多信息")

    # Check 3: Risk-reward for non-hold verdicts
    final_v = verdict.get("final_verdict", "hold")
    if final_v in ("strong_buy", "buy", "strong_sell", "sell"):
        rr = risk.get("risk_reward_ratio", 0)
        if rr >= 1.0:
            checks_passed.append(f"风险收益比={rr:.2f}")
        else:
            checks_failed.append(f"风险收益比不佳({rr:.2f}<1.0)")

    # Check 4: Pipeline integrity
    critical = [e for e in errors if any(k in e for k in ("market_data", "forecast", "strategy"))]
    if not critical:
        checks_passed.append("核心数据管道完整")
    else:
        checks_failed.append(f"核心管道异常: {critical[0]}")

    # Check 5: Data completeness
    if state.get("forecast") and state.get("strategies"):
        checks_passed.append("预测和策略数据完整")
    else:
        checks_failed.append("缺少预测或策略数据")

    passed = len(checks_failed) == 0
    is_final = passed or round_num >= max_rounds

    retry_reason = None
    if not passed and not is_final:
        retry_reason = checks_failed[0] if checks_failed else "验证未通过"

    validation: ValidationResult = {
        "passed": passed,
        "round_number": round_num,
        "max_rounds": max_rounds,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "retry_reason": retry_reason,
        "final": is_final,
    }

    return {
        "validation": validation,
        "validation_round": round_num,
    }


def record_prediction_node(state: FinAgentState) -> dict:
    """Terminal node: Record the prediction for accuracy tracking."""
    try:
        from invest_app.agent_backtest import record_prediction

        verdict = state.get("judge_verdict", {})
        market_data = state.get("market_data", {})
        forecast = state.get("forecast", {})
        bull = state.get("bull_case", {})
        bear = state.get("bear_case", {})
        risk = state.get("risk_budget", {})

        record_prediction(
            ticker=state["ticker"],
            name=market_data.get("name", ""),
            horizon=state.get("horizon", "1d"),
            verdict=verdict.get("final_verdict", "hold"),
            confidence=verdict.get("confidence", "low"),
            score=verdict.get("score", 50),
            entry_price=market_data.get("current_price", 0),
            target_price=verdict.get("target_price"),
            stop_loss=verdict.get("stop_loss"),
            predicted_return_pct=forecast.get("expected_return_pct", 0),
            bull_score=bull.get("score", 0),
            bear_score=bear.get("score", 0),
            risk_reward_ratio=risk.get("risk_reward_ratio", 0),
            factors=forecast.get("model_scores", {}),
        )
        return {"notes": ["prediction recorded for accuracy tracking"]}
    except Exception as exc:
        return {"errors": [f"record_prediction: {exc}"]}


# ───────────────────────────────────────────────────────────────────────────
# Conditional edges
# ───────────────────────────────────────────────────────────────────────────

def should_retry(state: FinAgentState) -> str:
    """Decide whether to retry or finalize after validation."""
    validation = state.get("validation", {})
    if validation.get("final", True):
        return "finalize"
    if not validation.get("passed", False):
        return "retry"
    return "finalize"


# ───────────────────────────────────────────────────────────────────────────
# Sequential fallback runner (no LangGraph dependency)
# ───────────────────────────────────────────────────────────────────────────

def run_agent_pipeline(
    ticker: str,
    *,
    horizon: str = "1d",
    period: str = "6mo",
    history_interval: str = "1d",
    include_intraday: bool = True,
    include_news: bool = True,
    max_validation_rounds: int = 3,
) -> FinAgentState:
    """Run the full agent pipeline sequentially without LangGraph.

    Implements the same topology as the LangGraph version:
      market_data → portfolio → compute → search → bull ∥ bear → judge → risk → validate [→ retry]
    """
    state = create_initial_state(
        ticker,
        horizon=horizon,
        period=period,
        history_interval=history_interval,
        include_intraday=include_intraday,
        include_news=include_news,
        max_validation_rounds=max_validation_rounds,
    )

    def _merge(partial: dict) -> None:
        for key, value in partial.items():
            if key in ("notes", "errors"):
                existing = state.get(key, [])
                if isinstance(existing, list) and isinstance(value, list):
                    state[key] = existing + value
                else:
                    state[key] = value
            elif key == "meta" and isinstance(value, dict):
                existing = state.get("meta", {})
                existing.update(value)
                state["meta"] = existing
            else:
                state[key] = value

    # Phase 1: Data gathering
    _merge(load_market_data_node(state))
    _merge(load_portfolio_node(state))

    # Phase 2: Compute (算)
    _merge(run_compute_node(state))

    # Phase 3: Search (搜)
    _merge(run_search_node(state))

    # Phase 4: Red-blue adversarial (控)
    _merge(bull_case_node(state))
    _merge(bear_case_node(state))
    _merge(judge_node(state))

    # Phase 5: Risk budget
    _merge(risk_budget_node(state))

    # Phase 6: Validation with retry loop
    for _round in range(max_validation_rounds):
        _merge(validate_node(state))
        validation = state.get("validation", {})

        if validation.get("passed") or validation.get("final"):
            break

        # Retry: re-run compute with potentially updated params
        state["notes"] = state.get("notes", []) + [
            f"retry round {state.get('validation_round', 0)}: {validation.get('retry_reason', '')}"
        ]
        _merge(run_compute_node(state))
        _merge(bull_case_node(state))
        _merge(bear_case_node(state))
        _merge(judge_node(state))
        _merge(risk_budget_node(state))

    # Phase 7: Record prediction
    _merge(record_prediction_node(state))

    state["meta"]["completed_at"] = time.time()
    elapsed = state["meta"]["completed_at"] - state["meta"].get("started_at", 0)
    state["meta"]["elapsed_seconds"] = round(elapsed, 2)

    return state


# ───────────────────────────────────────────────────────────────────────────
# LangGraph compiled graph builder
# ───────────────────────────────────────────────────────────────────────────

def build_agent_graph() -> Any:
    """Build and compile the LangGraph workflow.

    Graph:
      START → market_data → portfolio → compute → search
            → bull_case ─┐
            → bear_case ─┤
                         └→ judge → risk_budget → validate
                                                     ↓
                              finalize ←── should_retry ──→ retry(compute)
                                ↓
                           record_prediction → END
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is not installed. Use run_agent_pipeline() as fallback, "
            "or install it: pip install langgraph"
        ) from exc

    graph = StateGraph(FinAgentState)

    # Register nodes
    graph.add_node("market_data", load_market_data_node)
    graph.add_node("portfolio", load_portfolio_node)
    graph.add_node("compute", run_compute_node)
    graph.add_node("search", run_search_node)
    graph.add_node("bull_case", bull_case_node)
    graph.add_node("bear_case", bear_case_node)
    graph.add_node("judge", judge_node)
    graph.add_node("risk_budget", risk_budget_node)
    graph.add_node("validate", validate_node)
    graph.add_node("record_prediction", record_prediction_node)

    # Sequential edges: data gathering → compute → search
    graph.add_edge(START, "market_data")
    graph.add_edge("market_data", "portfolio")
    graph.add_edge("portfolio", "compute")
    graph.add_edge("compute", "search")

    # Fan-out: search → bull_case AND bear_case in parallel
    graph.add_edge("search", "bull_case")
    graph.add_edge("search", "bear_case")

    # Fan-in: both cases → judge
    graph.add_edge("bull_case", "judge")
    graph.add_edge("bear_case", "judge")

    # Judge → risk → validate
    graph.add_edge("judge", "risk_budget")
    graph.add_edge("risk_budget", "validate")

    # Conditional: validate → retry or finalize
    graph.add_conditional_edges(
        "validate",
        should_retry,
        {
            "retry": "compute",      # Loop back to recompute
            "finalize": "record_prediction",
        },
    )

    # Terminal
    graph.add_edge("record_prediction", END)

    return graph.compile()