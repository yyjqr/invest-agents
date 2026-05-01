"""Hourly forecast-vs-actual monitoring script.

Runs every hour during A-share trading hours (09:30–15:00), makes 1h forecasts
for a configured list of 5–10 tickers, then compares the prediction against the
actual price observed one hour later.  All results are persisted to CSV and JSON
for subsequent analysis and strategy tuning.

Usage (standalone):
    python -m invest_app.forecast_monitor --tickers 600519 000001 512400 --once
    python -m invest_app.forecast_monitor --tickers 600519 000001 --loop

Usage (via CLI):
    invest monitor 600519 000001 512400 --once
    invest monitor 600519 000001 --loop
"""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

import pandas as pd

from invest_app.config import DATA_DIR, ensure_data_dir
from invest_app.forecast import (
    ShortTermForecast,
    build_short_term_forecast,
)
from invest_app.market_data import (
    _classify_cn_code,
    get_history,
    get_intraday,
    get_quote,
    resolve_ticker,
)
from invest_app.portfolio import load_portfolio

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MONITOR_DIR = DATA_DIR / "forecast_monitor"
CSV_FILE = MONITOR_DIR / "hourly_results.csv"
JSON_FILE = MONITOR_DIR / "hourly_results.json"
SUMMARY_FILE = MONITOR_DIR / "summary_report.txt"

_CSV_HEADERS = [
    "date",
    "time_start",
    "time_end",
    "ticker",
    "name",
    "price_at_forecast",
    "predicted_price",
    "predicted_high",
    "predicted_low",
    "predicted_trend",
    "confidence",
    "expected_return_pct",
    "expected_range_pct",
    "actual_price_1h",
    "actual_high_1h",
    "actual_low_1h",
    "actual_return_pct",
    "price_error_pct",
    "high_error_pct",
    "low_error_pct",
    "direction_correct",
    "within_range",
    "trend_signal",
    "macd_score",
    "bollinger_score",
    "kdj_score",
    "obv_divergence",
    "mfi_score",
    "vol_price_div",
    "volume_ratio",
    "mom_short",
    "rsi_score",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HourlyRecord:
    """One forecast-vs-actual record for a single ticker at one time point."""

    date: str
    time_start: str
    time_end: str
    ticker: str
    name: str
    # Forecast
    price_at_forecast: float
    predicted_price: float
    predicted_high: float
    predicted_low: float
    predicted_trend: str
    confidence: float
    expected_return_pct: float
    expected_range_pct: float
    # Actual (filled after 1h)
    actual_price_1h: float | None = None
    actual_high_1h: float | None = None
    actual_low_1h: float | None = None
    # Metrics (computed after actual is available)
    actual_return_pct: float | None = None
    price_error_pct: float | None = None
    high_error_pct: float | None = None
    low_error_pct: float | None = None
    direction_correct: bool | None = None
    within_range: bool | None = None
    # Key factor scores for analysis
    trend_signal: float = 0.0
    macd_score: float = 0.0
    bollinger_score: float = 0.0
    kdj_score: float = 0.0
    obv_divergence: float = 0.0
    mfi_score: float = 0.0
    vol_price_div: float = 0.0
    volume_ratio: float = 0.0
    mom_short: float = 0.0
    rsi_score: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_monitor_dir() -> Path:
    ensure_data_dir()
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    return MONITOR_DIR


def _now() -> datetime:
    return datetime.now()


def _is_trading_time(dt: datetime | None = None) -> bool:
    """Return True if *dt* falls within A-share trading hours (weekday, 09:25–15:05)."""
    dt = dt or _now()
    if dt.weekday() >= 5:  # Saturday / Sunday
        return False
    t = dt.time()
    from datetime import time as T
    return T(9, 25) <= t <= T(15, 5)


def _next_hour_slot(dt: datetime | None = None) -> datetime:
    """Return the start of the next exact hour slot on or after *dt*."""
    dt = dt or _now()
    # Round up to next hour
    if dt.minute == 0 and dt.second == 0:
        return dt
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _trading_hour_slots() -> list[str]:
    """Return the list of hour-start times used for monitoring."""
    return ["09:30", "10:30", "11:00", "13:00", "14:00"]


# ---------------------------------------------------------------------------
# Core: make forecast for one ticker
# ---------------------------------------------------------------------------


def _make_one_forecast(ticker: str) -> tuple[ShortTermForecast | None, str]:
    """Fetch data and produce an 1h forecast. Returns (forecast, error_msg)."""
    try:
        resolved = resolve_ticker(ticker)
        quote = get_quote(resolved)
        history = get_history(resolved, period="6mo", interval="1d")
        intraday = get_intraday(resolved)

        instrument_type = _classify_cn_code(
            resolved.split(".", maxsplit=1)[0], quote.name,
        )
        portfolio = load_portfolio()

        # Find holding
        from invest_app.forecast import _find_holding
        holding = _find_holding(portfolio, resolved)

        fc = build_short_term_forecast(
            ticker=quote.ticker,
            name=quote.name,
            history=history,
            current_price=float(quote.price),
            horizon="1h",
            history_period="6mo",
            intraday=intraday,
            pe_ratio=None,
            profit_margin=None,
            holding=holding,
            is_fund=instrument_type == "fund",
        )
        return fc, ""
    except Exception as exc:
        return None, str(exc)


def _fetch_actual_price(ticker: str) -> tuple[float | None, float | None, float | None]:
    """Fetch current price as the 'actual' 1h-later snapshot.

    Returns (current_price, high_est, low_est).
    High/low are estimated from intraday data if available.
    """
    try:
        quote = get_quote(resolve_ticker(ticker))
        current = float(quote.price)

        # Try to get recent intraday high/low from last ~60 minutes
        intraday = get_intraday(resolve_ticker(ticker))
        high_est = current
        low_est = current
        if not intraday.empty and "Price" in intraday.columns:
            recent = intraday.tail(60)  # last ~60 minutes
            prices = recent["Price"].astype(float)
            if not prices.empty:
                high_est = float(prices.max())
                low_est = float(prices.min())
        return current, high_est, low_est
    except Exception:
        return None, None, None


def _compute_metrics(record: HourlyRecord) -> None:
    """Fill in comparison metrics after actual data is available."""
    if record.actual_price_1h is None:
        return

    base = record.price_at_forecast
    if base <= 0:
        return

    actual_ret = (record.actual_price_1h - base) / base * 100
    record.actual_return_pct = round(actual_ret, 4)

    # Price error: predicted vs actual end price
    pred_ret = record.expected_return_pct
    record.price_error_pct = round(
        (record.predicted_price - record.actual_price_1h) / base * 100, 4,
    )

    # High/low error
    if record.actual_high_1h is not None:
        record.high_error_pct = round(
            (record.predicted_high - record.actual_high_1h) / base * 100, 4,
        )
    if record.actual_low_1h is not None:
        record.low_error_pct = round(
            (record.predicted_low - record.actual_low_1h) / base * 100, 4,
        )

    # Direction correct?
    predicted_dir = 1 if pred_ret >= 0 else -1
    actual_dir = 1 if actual_ret >= 0 else -1
    record.direction_correct = predicted_dir == actual_dir

    # Was actual price within predicted high/low range?
    record.within_range = (
        record.predicted_low <= record.actual_price_1h <= record.predicted_high
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _append_csv(records: list[HourlyRecord]) -> None:
    """Append records to CSV file."""
    _ensure_monitor_dir()
    file_exists = CSV_FILE.exists() and CSV_FILE.stat().st_size > 0

    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        for rec in records:
            row = asdict(rec)
            writer.writerow(row)


def _append_json(records: list[HourlyRecord]) -> None:
    """Append records to JSON-lines file."""
    _ensure_monitor_dir()
    with open(JSON_FILE, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def load_all_records() -> list[dict]:
    """Load all historical records from JSON-lines file."""
    if not JSON_FILE.exists():
        return []
    records = []
    for line in JSON_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ---------------------------------------------------------------------------
# One-round runner
# ---------------------------------------------------------------------------


def run_one_round(
    tickers: Sequence[str],
    *,
    wait_for_actual: bool = True,
    wait_minutes: int = 60,
    log_fn=None,
) -> list[HourlyRecord]:
    """Execute one monitoring round:

    1. Make 1h forecasts for all tickers.
    2. (Optionally) Wait ~60 minutes.
    3. Fetch actual prices and compute comparison metrics.
    4. Persist to CSV + JSON.
    5. Print summary.

    Returns the completed list of HourlyRecord.
    """
    if log_fn is None:
        log_fn = print

    now = _now()
    date_str = now.strftime("%Y-%m-%d")
    time_start = now.strftime("%H:%M")
    time_end_dt = now + timedelta(minutes=wait_minutes)
    time_end = time_end_dt.strftime("%H:%M")

    log_fn(f"\n{'='*70}")
    log_fn(f"[{date_str} {time_start}] 开始第一轮预测（共 {len(tickers)} 只）")
    log_fn(f"{'='*70}")

    # Step 1: Forecast
    records: list[HourlyRecord] = []
    for tk in tickers:
        fc, err = _make_one_forecast(tk)
        if fc is None:
            log_fn(f"  ⚠ {tk}: 预测失败 — {err}")
            continue

        scores = fc.model_scores
        rec = HourlyRecord(
            date=date_str,
            time_start=time_start,
            time_end=time_end,
            ticker=fc.ticker,
            name=fc.name,
            price_at_forecast=fc.current_price,
            predicted_price=fc.predicted_price,
            predicted_high=fc.predicted_high,
            predicted_low=fc.predicted_low,
            predicted_trend=fc.trend,
            confidence=fc.confidence,
            expected_return_pct=fc.expected_return_pct,
            expected_range_pct=fc.expected_range_pct,
            trend_signal=scores.get("trend_signal", 0.0),
            macd_score=scores.get("macd_score", 0.0),
            bollinger_score=scores.get("bollinger_score", 0.0),
            kdj_score=scores.get("kdj_score", 0.0),
            obv_divergence=scores.get("obv_divergence", 0.0),
            mfi_score=scores.get("mfi_score", 0.0),
            vol_price_div=scores.get("vol_price_div", 0.0),
            volume_ratio=scores.get("volume_ratio", 0.0),
            mom_short=scores.get("mom_short", 0.0),
            rsi_score=scores.get("rsi_score", 0.0),
        )
        records.append(rec)
        log_fn(
            f"  ✓ {fc.name}({fc.ticker}): "
            f"现价 ¥{fc.current_price:.4f} → "
            f"预测 ¥{fc.predicted_price:.4f} "
            f"[{fc.trend}] 置信度 {fc.confidence:.0f}%"
        )

    if not records:
        log_fn("  没有成功预测的标的，本轮跳过。")
        return records

    # Step 2: Wait
    if wait_for_actual:
        log_fn(f"\n  ⏳ 等待 {wait_minutes} 分钟后获取实际价格...")
        _wait_with_progress(wait_minutes, log_fn)

        # Step 3: Fetch actual prices
        log_fn(f"\n[{_now().strftime('%H:%M')}] 获取实际价格并计算偏差")
        log_fn("-" * 70)
        for rec in records:
            actual, high_est, low_est = _fetch_actual_price(rec.ticker)
            rec.actual_price_1h = actual
            rec.actual_high_1h = high_est
            rec.actual_low_1h = low_est
            _compute_metrics(rec)

    # Step 4: Persist
    _append_csv(records)
    _append_json(records)

    # Step 5: Print summary
    _print_round_summary(records, log_fn)

    return records


def _wait_with_progress(minutes: int, log_fn) -> None:
    """Wait with periodic progress dots."""
    total_seconds = minutes * 60
    interval = 300  # print dot every 5 min
    elapsed = 0
    while elapsed < total_seconds:
        sleep_time = min(interval, total_seconds - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time
        remaining = (total_seconds - elapsed) // 60
        if remaining > 0:
            log_fn(f"    ... 还剩约 {remaining} 分钟")


def _print_round_summary(records: list[HourlyRecord], log_fn) -> None:
    """Print a single-round comparison summary."""
    log_fn(f"\n{'─'*70}")
    log_fn("预测 vs 实际 对比")
    log_fn(f"{'─'*70}")

    header = (
        f"{'代码':<10} {'名称':<10} {'预测价':>10} {'实际价':>10} "
        f"{'偏差%':>8} {'方向':>6} {'区间':>6} {'趋势':<10}"
    )
    log_fn(header)
    log_fn("-" * len(header))

    dir_correct_count = 0
    range_count = 0
    total_with_actual = 0
    abs_errors: list[float] = []

    for rec in records:
        if rec.actual_price_1h is None:
            log_fn(
                f"{rec.ticker:<10} {rec.name:<10} "
                f"¥{rec.predicted_price:>9.4f} {'N/A':>10} "
                f"{'—':>8} {'—':>6} {'—':>6} {rec.predicted_trend}"
            )
            continue

        total_with_actual += 1
        err = rec.price_error_pct or 0.0
        abs_errors.append(abs(err))
        dir_ok = "✓" if rec.direction_correct else "✗"
        rng_ok = "✓" if rec.within_range else "✗"
        if rec.direction_correct:
            dir_correct_count += 1
        if rec.within_range:
            range_count += 1

        log_fn(
            f"{rec.ticker:<10} {rec.name:<10} "
            f"¥{rec.predicted_price:>9.4f} ¥{rec.actual_price_1h:>9.4f} "
            f"{err:>+7.3f}% {dir_ok:>6} {rng_ok:>6} {rec.predicted_trend}"
        )

    if total_with_actual > 0:
        dir_acc = dir_correct_count / total_with_actual * 100
        rng_acc = range_count / total_with_actual * 100
        mae = sum(abs_errors) / len(abs_errors) if abs_errors else 0.0
        log_fn(f"\n📊 本轮统计 ({total_with_actual} 只)：")
        log_fn(f"   方向准确率：{dir_acc:.1f}% ({dir_correct_count}/{total_with_actual})")
        log_fn(f"   区间覆盖率：{rng_acc:.1f}% ({range_count}/{total_with_actual})")
        log_fn(f"   平均价格偏差 MAE：{mae:.4f}%")


# ---------------------------------------------------------------------------
# Multi-round loop: run all day
# ---------------------------------------------------------------------------


def run_loop(
    tickers: Sequence[str],
    *,
    log_fn=None,
) -> None:
    """Run forecast monitoring for the entire trading day.

    Auto-schedules at each trading-hour slot (09:30, 10:30, 11:00, 13:00, 14:00).
    Prints and persists results after each round.
    At end-of-day (after 15:00), generates a daily summary.
    """
    if log_fn is None:
        log_fn = print

    log_fn("=" * 70)
    log_fn("🔬 预测监控系统启动")
    log_fn(f"   监控标的：{', '.join(tickers)}")
    log_fn(f"   数据目录：{MONITOR_DIR}")
    log_fn("=" * 70)

    slots = _trading_hour_slots()  # ["09:30", "10:30", "11:00", "13:00", "14:00"]
    completed_slots: set[str] = set()
    day_records: list[HourlyRecord] = []

    while True:
        now = _now()

        # End of day check
        if now.hour >= 15 and now.minute >= 5:
            log_fn(f"\n[{now.strftime('%H:%M')}] 交易日结束，生成每日汇总。")
            _daily_summary(day_records, log_fn)
            break

        # Weekend check
        if now.weekday() >= 5:
            log_fn("今日非交易日，退出。")
            break

        # Find current eligible slot
        current_slot = None
        for slot in slots:
            if slot in completed_slots:
                continue
            sh, sm = map(int, slot.split(":"))
            slot_time = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            # Allow starting within 5 minutes of slot time
            if abs((now - slot_time).total_seconds()) < 300:
                current_slot = slot
                break
            # Already past this slot (by more than 5 min) — skip
            if now > slot_time + timedelta(minutes=5):
                completed_slots.add(slot)

        if current_slot:
            log_fn(f"\n🕐 时间段 {current_slot} — 开始监控轮次")
            completed_slots.add(current_slot)

            # Wait minutes depends on time until next boundary
            # 09:30 → 10:30 = 60 min, 10:30 → 11:30 (but market closes 11:30 → 13:00)
            wait_mins = 60
            sh, sm = map(int, current_slot.split(":"))
            if sh == 11 and sm == 0:
                wait_mins = 30  # 11:00 → 11:30 only half hour left in morning
            elif sh == 14 and sm == 0:
                wait_mins = 60  # 14:00 → 15:00

            records = run_one_round(
                tickers, wait_for_actual=True,
                wait_minutes=wait_mins, log_fn=log_fn,
            )
            day_records.extend(records)
        else:
            # Sleep until next check
            time.sleep(30)

    log_fn("\n🔬 监控系统已停止。")


def _daily_summary(records: list[HourlyRecord], log_fn) -> None:
    """Generate end-of-day summary with aggregate metrics."""
    if not records:
        log_fn("  今日无监控数据。")
        return

    log_fn(f"\n{'='*70}")
    log_fn("📋 每日汇总报告")
    log_fn(f"{'='*70}")

    with_actual = [r for r in records if r.actual_price_1h is not None]
    if not with_actual:
        log_fn("  今日所有轮次均未获取到实际价格。")
        return

    # Per-ticker aggregation
    ticker_stats: dict[str, dict] = {}
    for rec in with_actual:
        key = rec.ticker
        if key not in ticker_stats:
            ticker_stats[key] = {
                "name": rec.name,
                "rounds": 0,
                "dir_correct": 0,
                "in_range": 0,
                "abs_errors": [],
                "factor_scores": {},
            }
        stats = ticker_stats[key]
        stats["rounds"] += 1
        if rec.direction_correct:
            stats["dir_correct"] += 1
        if rec.within_range:
            stats["in_range"] += 1
        stats["abs_errors"].append(abs(rec.price_error_pct or 0.0))

        # Accumulate factor scores for correlation analysis
        for factor in [
            "macd_score", "bollinger_score", "kdj_score",
            "obv_divergence", "mfi_score", "vol_price_div",
            "volume_ratio", "mom_short", "rsi_score",
        ]:
            val = getattr(rec, factor, 0.0)
            stats["factor_scores"].setdefault(factor, []).append(val)

    log_fn(f"\n{'代码':<10} {'名称':<10} {'轮次':>4} {'方向准确率':>10} "
           f"{'区间覆盖率':>10} {'MAE%':>8}")
    log_fn("-" * 62)

    total_rounds = 0
    total_dir = 0
    total_range = 0
    all_errors: list[float] = []

    for tk, stats in ticker_stats.items():
        n = stats["rounds"]
        dir_pct = stats["dir_correct"] / n * 100
        rng_pct = stats["in_range"] / n * 100
        mae = sum(stats["abs_errors"]) / len(stats["abs_errors"])
        log_fn(
            f"{tk:<10} {stats['name']:<10} {n:>4} "
            f"{dir_pct:>9.1f}% {rng_pct:>9.1f}% {mae:>7.4f}%"
        )
        total_rounds += n
        total_dir += stats["dir_correct"]
        total_range += stats["in_range"]
        all_errors.extend(stats["abs_errors"])

    log_fn("-" * 62)
    if total_rounds > 0:
        log_fn(
            f"{'合计':<10} {'':10} {total_rounds:>4} "
            f"{total_dir/total_rounds*100:>9.1f}% "
            f"{total_range/total_rounds*100:>9.1f}% "
            f"{sum(all_errors)/len(all_errors):>7.4f}%"
        )

    # Factor effectiveness hint
    log_fn(f"\n📈 因子贡献分析参考：")
    log_fn("  （正值=看涨因子评分，与实际涨幅方向是否一致可评估因子有效性）")

    # Compute per-factor direction agreement rate
    for factor in [
        "macd_score", "bollinger_score", "kdj_score",
        "obv_divergence", "mfi_score", "vol_price_div",
        "volume_ratio", "mom_short", "rsi_score",
    ]:
        agree = 0
        total = 0
        for rec in with_actual:
            fval = getattr(rec, factor, 0.0)
            if abs(fval) < 0.01:
                continue  # skip near-zero (neutral)
            actual_ret = rec.actual_return_pct or 0.0
            if (fval > 0 and actual_ret > 0) or (fval < 0 and actual_ret < 0):
                agree += 1
            total += 1

        if total >= 2:
            pct = agree / total * 100
            label = {
                "macd_score": "MACD",
                "bollinger_score": "布林带",
                "kdj_score": "KDJ",
                "obv_divergence": "OBV量价",
                "mfi_score": "资金流MFI",
                "vol_price_div": "量价背离",
                "volume_ratio": "量比",
                "mom_short": "短期动量",
                "rsi_score": "RSI情绪",
            }.get(factor, factor)
            emoji = "🟢" if pct >= 60 else ("🟡" if pct >= 45 else "🔴")
            log_fn(f"  {emoji} {label:<12}: 方向一致率 {pct:.0f}% ({agree}/{total})")

    # Save summary to file
    _save_daily_summary(records, ticker_stats, total_rounds, total_dir, total_range, all_errors)


def _save_daily_summary(
    records, ticker_stats, total_rounds, total_dir, total_range, all_errors,
) -> None:
    """Append daily summary to a persistent text file."""
    _ensure_monitor_dir()
    date_str = records[0].date if records else _now().strftime("%Y-%m-%d")
    lines = [
        f"\n===== {date_str} 每日汇总 =====",
        f"总轮次: {total_rounds}",
        f"方向准确率: {total_dir/max(total_rounds,1)*100:.1f}%",
        f"区间覆盖率: {total_range/max(total_rounds,1)*100:.1f}%",
        f"MAE: {sum(all_errors)/max(len(all_errors),1):.4f}%",
        "",
    ]
    for tk, stats in ticker_stats.items():
        n = stats["rounds"]
        lines.append(
            f"  {tk} {stats['name']}: "
            f"方向{stats['dir_correct']}/{n} "
            f"区间{stats['in_range']}/{n} "
            f"MAE={sum(stats['abs_errors'])/max(len(stats['abs_errors']),1):.4f}%"
        )
    lines.append("")

    with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Historical analysis helpers
# ---------------------------------------------------------------------------


def generate_analysis_report(log_fn=None) -> str:
    """Analyze all historical monitoring data and produce a tuning report.

    Returns the report text.
    """
    if log_fn is None:
        log_fn = print

    records = load_all_records()
    if not records:
        msg = "没有历史监控数据，请先运行 invest monitor 收集数据。"
        log_fn(msg)
        return msg

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("📊 预测模型历史表现分析报告")
    lines.append(f"   数据量：{len(records)} 条记录")
    lines.append("=" * 70)

    with_actual = [r for r in records if r.get("actual_price_1h") is not None]
    if not with_actual:
        msg = "所有记录均无实际价格数据。"
        lines.append(msg)
        report = "\n".join(lines)
        log_fn(report)
        return report

    # Overall metrics
    dir_ok = sum(1 for r in with_actual if r.get("direction_correct"))
    rng_ok = sum(1 for r in with_actual if r.get("within_range"))
    errors = [abs(r.get("price_error_pct", 0.0)) for r in with_actual]
    n = len(with_actual)

    lines.append(f"\n整体表现 ({n} 条有效记录)：")
    lines.append(f"  方向准确率: {dir_ok/n*100:.1f}%")
    lines.append(f"  区间覆盖率: {rng_ok/n*100:.1f}%")
    lines.append(f"  平均绝对偏差 MAE: {sum(errors)/len(errors):.4f}%")
    lines.append(f"  最大偏差: {max(errors):.4f}%")
    lines.append(f"  中位数偏差: {sorted(errors)[len(errors)//2]:.4f}%")

    # Per-factor effectiveness
    factor_names = [
        "macd_score", "bollinger_score", "kdj_score",
        "obv_divergence", "mfi_score", "vol_price_div",
        "volume_ratio", "mom_short", "rsi_score",
    ]
    label_map = {
        "macd_score": "MACD",
        "bollinger_score": "布林带",
        "kdj_score": "KDJ",
        "obv_divergence": "OBV量价",
        "mfi_score": "资金流MFI",
        "vol_price_div": "量价背离",
        "volume_ratio": "量比",
        "mom_short": "短期动量",
        "rsi_score": "RSI情绪",
    }

    lines.append(f"\n各因子方向一致率：")
    lines.append(f"  {'因子':<14} {'一致率':>8} {'有效次数':>8} {'建议'}")
    lines.append("  " + "-" * 50)

    for factor in factor_names:
        agree = 0
        total = 0
        for r in with_actual:
            fval = r.get(factor, 0.0)
            if abs(fval) < 0.01:
                continue
            actual_ret = r.get("actual_return_pct", 0.0) or 0.0
            if (fval > 0 and actual_ret > 0) or (fval < 0 and actual_ret < 0):
                agree += 1
            total += 1

        label = label_map.get(factor, factor)
        if total >= 3:
            pct = agree / total * 100
            if pct >= 65:
                advice = "✅ 有效，可适当增加权重"
            elif pct >= 50:
                advice = "⚠ 中等，维持当前权重"
            elif pct >= 35:
                advice = "🔻 偏弱，考虑降低权重"
            else:
                advice = "❌ 无效/反向，建议重新审视"
            lines.append(f"  {label:<14} {pct:>7.1f}% {total:>8} {advice}")
        else:
            lines.append(f"  {label:<14} {'数据不足':>8} {total:>8} —")

    # Per-ticker breakdown
    ticker_stats: dict[str, dict] = {}
    for r in with_actual:
        tk = r.get("ticker", "?")
        if tk not in ticker_stats:
            ticker_stats[tk] = {
                "name": r.get("name", ""),
                "n": 0, "dir_ok": 0, "rng_ok": 0, "errors": [],
            }
        s = ticker_stats[tk]
        s["n"] += 1
        if r.get("direction_correct"):
            s["dir_ok"] += 1
        if r.get("within_range"):
            s["rng_ok"] += 1
        s["errors"].append(abs(r.get("price_error_pct", 0.0)))

    lines.append(f"\n各标的表现：")
    lines.append(
        f"  {'代码':<10} {'名称':<10} {'次数':>4} "
        f"{'方向%':>6} {'区间%':>6} {'MAE%':>7}"
    )
    lines.append("  " + "-" * 50)
    for tk, s in sorted(ticker_stats.items()):
        nn = s["n"]
        lines.append(
            f"  {tk:<10} {s['name']:<10} {nn:>4} "
            f"{s['dir_ok']/nn*100:>5.1f}% "
            f"{s['rng_ok']/nn*100:>5.1f}% "
            f"{sum(s['errors'])/len(s['errors']):>6.4f}%"
        )

    lines.append(f"\n{'='*70}")
    lines.append("调参建议：")
    lines.append("  1. 方向一致率 > 65% 的因子可增加权重 5-10%")
    lines.append("  2. 方向一致率 < 35% 的因子建议检查信号逻辑或大幅降权")
    lines.append("  3. 区间覆盖率 < 70% 时，考虑增大 expected_range_pct 乘数")
    lines.append("  4. MAE > 1% 时，考虑收紧 signal 到 return 的映射系数")
    lines.append(f"{'='*70}")

    report = "\n".join(lines)
    log_fn(report)

    # Also save to file
    _ensure_monitor_dir()
    report_file = MONITOR_DIR / "analysis_report.txt"
    report_file.write_text(report, encoding="utf-8")
    log_fn(f"\n报告已保存到: {report_file}")

    return report


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="预测监控脚本")
    parser.add_argument(
        "--tickers", nargs="+", required=True,
        help="要监控的股票代码列表（5-10只）",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="只运行一轮（不等待实际价格）",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="循环监控整个交易日",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="生成历史分析报告",
    )
    parser.add_argument(
        "--wait", type=int, default=60,
        help="等待获取实际价格的分钟数（默认60）",
    )

    args = parser.parse_args()

    if args.report:
        generate_analysis_report()
        sys.exit(0)

    tickers = args.tickers[:10]

    if args.once:
        run_one_round(tickers, wait_for_actual=True, wait_minutes=args.wait)
    elif args.loop:
        run_loop(tickers)
    else:
        # Default: one round without waiting
        run_one_round(tickers, wait_for_actual=False)
