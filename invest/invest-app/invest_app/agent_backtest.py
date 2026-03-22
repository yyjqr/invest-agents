"""Prediction tracking and accuracy monitoring for the Fin-Agent.

Records every agent verdict, then later compares against actual price moves.
This enables:
  - Historical accuracy rate (win/loss/draw)
  - Calibration analysis (predicted vs actual returns)
  - Confidence reliability (high-confidence predictions should be more accurate)
  - Strategy-level attribution
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from invest_app.config import DATA_DIR

PREDICTIONS_FILE = DATA_DIR / "agent_predictions.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PredictionRecord:
    """One agent prediction to be tracked for accuracy."""
    id: str                         # "{ticker}_{timestamp}"
    ticker: str
    name: str
    timestamp: float                # unix epoch when prediction was made
    horizon: str                    # "1h" | "1d"
    verdict: str                    # "strong_buy" .. "strong_sell"
    confidence: str                 # "high" | "medium" | "low"
    score: float                    # 0-100
    entry_price: float              # price at prediction time
    target_price: float | None = None
    stop_loss: float | None = None
    predicted_return_pct: float = 0.0

    # Filled in later by check_prediction()
    actual_price: float | None = None
    actual_return_pct: float | None = None
    outcome: str = "pending"        # "win" | "loss" | "draw" | "pending"
    checked_at: float | None = None

    # Additional context
    bull_score: float = 0.0
    bear_score: float = 0.0
    risk_reward_ratio: float = 0.0
    factors: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_records() -> list[dict[str, Any]]:
    """Load prediction records from disk."""
    if not PREDICTIONS_FILE.exists():
        return []
    try:
        return json.loads(PREDICTIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_records(records: list[dict[str, Any]]) -> None:
    """Save prediction records to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PREDICTIONS_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Record a new prediction
# ---------------------------------------------------------------------------

def record_prediction(
    *,
    ticker: str,
    name: str,
    horizon: str,
    verdict: str,
    confidence: str,
    score: float,
    entry_price: float,
    target_price: float | None = None,
    stop_loss: float | None = None,
    predicted_return_pct: float = 0.0,
    bull_score: float = 0.0,
    bear_score: float = 0.0,
    risk_reward_ratio: float = 0.0,
    factors: dict[str, Any] | None = None,
) -> PredictionRecord:
    """Create and persist a new prediction record."""
    ts = time.time()
    rec = PredictionRecord(
        id=f"{ticker}_{int(ts)}",
        ticker=ticker,
        name=name,
        timestamp=ts,
        horizon=horizon,
        verdict=verdict,
        confidence=confidence,
        score=score,
        entry_price=entry_price,
        target_price=target_price,
        stop_loss=stop_loss,
        predicted_return_pct=predicted_return_pct,
        bull_score=bull_score,
        bear_score=bear_score,
        risk_reward_ratio=risk_reward_ratio,
        factors=factors or {},
    )

    records = _load_records()
    records.append(asdict(rec))
    _save_records(records)
    return rec


# ---------------------------------------------------------------------------
# Check predictions against actual outcomes
# ---------------------------------------------------------------------------

def check_predictions(
    get_price_fn: Any = None,
) -> list[dict[str, Any]]:
    """Check all pending predictions against current prices.

    Args:
        get_price_fn: Optional callable(ticker) -> float. If not provided,
                      uses invest_app.market_data.get_quote.
    """
    if get_price_fn is None:
        from invest_app.market_data import get_quote
        def get_price_fn(ticker: str) -> float:
            return float(get_quote(ticker).price)

    records = _load_records()
    updated = False
    now = time.time()

    for rec in records:
        if rec.get("outcome") != "pending":
            continue

        # Check if enough time has passed
        elapsed_hours = (now - rec["timestamp"]) / 3600
        horizon = rec.get("horizon", "1d")
        min_hours = 1.5 if horizon == "1h" else 24.0
        if elapsed_hours < min_hours:
            continue

        try:
            actual_price = get_price_fn(rec["ticker"])
        except Exception:
            continue

        entry = rec["entry_price"]
        if entry <= 0:
            continue

        actual_return = (actual_price - entry) / entry * 100
        rec["actual_price"] = actual_price
        rec["actual_return_pct"] = round(actual_return, 4)
        rec["checked_at"] = now

        # Determine outcome
        verdict = rec.get("verdict", "hold")
        if verdict in ("strong_buy", "buy"):
            # Bull prediction: win if price went up
            if actual_return > 0.5:
                rec["outcome"] = "win"
            elif actual_return < -0.5:
                rec["outcome"] = "loss"
            else:
                rec["outcome"] = "draw"
        elif verdict in ("strong_sell", "sell"):
            # Bear prediction: win if price went down
            if actual_return < -0.5:
                rec["outcome"] = "win"
            elif actual_return > 0.5:
                rec["outcome"] = "loss"
            else:
                rec["outcome"] = "draw"
        else:
            # Hold: win if price stayed within ±1%
            if abs(actual_return) < 1.0:
                rec["outcome"] = "win"
            else:
                rec["outcome"] = "draw"

        updated = True

    if updated:
        _save_records(records)

    return [r for r in records if r.get("checked_at")]


# ---------------------------------------------------------------------------
# Accuracy statistics
# ---------------------------------------------------------------------------

def get_accuracy_stats(
    ticker: str | None = None,
    last_n: int | None = None,
) -> dict[str, Any]:
    """Compute accuracy statistics, optionally filtered by ticker.

    Returns:
        {
            "total": int,
            "wins": int,
            "losses": int,
            "draws": int,
            "pending": int,
            "win_rate": float (0-1),
            "avg_predicted_return": float,
            "avg_actual_return": float,
            "by_confidence": {"high": {...}, "medium": {...}, "low": {...}},
            "by_verdict": {"strong_buy": {...}, ...},
            "calibration_error": float,  # |predicted - actual| mean
        }
    """
    records = _load_records()

    if ticker:
        code = ticker.strip().split(".")[0]
        records = [r for r in records if r["ticker"].split(".")[0] == code]

    if last_n and len(records) > last_n:
        records = records[-last_n:]

    total = len(records)
    resolved = [r for r in records if r.get("outcome") != "pending"]
    pending = total - len(resolved)
    wins = sum(1 for r in resolved if r["outcome"] == "win")
    losses = sum(1 for r in resolved if r["outcome"] == "loss")
    draws = sum(1 for r in resolved if r["outcome"] == "draw")

    win_rate = wins / len(resolved) if resolved else 0.0

    predicted_returns = [r.get("predicted_return_pct", 0) for r in resolved if r.get("predicted_return_pct") is not None]
    actual_returns = [r.get("actual_return_pct", 0) for r in resolved if r.get("actual_return_pct") is not None]

    avg_predicted = sum(predicted_returns) / len(predicted_returns) if predicted_returns else 0.0
    avg_actual = sum(actual_returns) / len(actual_returns) if actual_returns else 0.0

    # Calibration error
    cal_errors = []
    for r in resolved:
        pred = r.get("predicted_return_pct", 0) or 0
        act = r.get("actual_return_pct", 0) or 0
        cal_errors.append(abs(pred - act))
    calibration_error = sum(cal_errors) / len(cal_errors) if cal_errors else 0.0

    # By confidence
    by_confidence: dict[str, dict[str, Any]] = {}
    for conf in ("high", "medium", "low"):
        subset = [r for r in resolved if r.get("confidence") == conf]
        w = sum(1 for r in subset if r["outcome"] == "win")
        by_confidence[conf] = {
            "total": len(subset),
            "wins": w,
            "win_rate": w / len(subset) if subset else 0.0,
        }

    # By verdict
    by_verdict: dict[str, dict[str, Any]] = {}
    for v in ("strong_buy", "buy", "hold", "sell", "strong_sell"):
        subset = [r for r in resolved if r.get("verdict") == v]
        w = sum(1 for r in subset if r["outcome"] == "win")
        by_verdict[v] = {
            "total": len(subset),
            "wins": w,
            "win_rate": w / len(subset) if subset else 0.0,
        }

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "pending": pending,
        "win_rate": round(win_rate, 4),
        "avg_predicted_return": round(avg_predicted, 4),
        "avg_actual_return": round(avg_actual, 4),
        "calibration_error": round(calibration_error, 4),
        "by_confidence": by_confidence,
        "by_verdict": by_verdict,
    }


# ---------------------------------------------------------------------------
# Recent predictions list
# ---------------------------------------------------------------------------

def get_recent_predictions(
    ticker: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the most recent prediction records."""
    records = _load_records()
    if ticker:
        code = ticker.strip().split(".")[0]
        records = [r for r in records if r["ticker"].split(".")[0] == code]
    return records[-limit:]
