"""Value discovery engine based on Piotroski F-Score and factor scoring."""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timedelta
from hashlib import sha1
from statistics import pstdev
from typing import Any
from zoneinfo import ZoneInfo

from invest_app.config import DATA_DIR, ensure_data_dir
from invest_app.financial_data import FinancialStatements, get_financials, get_market_value
from invest_app.market_data import (
    Quote,
    fetch_sector_stock_list,
    get_batch_quotes_tencent,
    get_fundamentals,
    get_history,
)
from invest_app.value_factors import valuation_percentile_score, weighted_mean_available

CN_TZ = ZoneInfo("Asia/Shanghai")

_POOL_SIZE = 10
_MAX_REPLACE = 3
_MAX_REPLACE_LARGE = 5
_INCREMENTAL_CANDIDATES = 30
_MIN_F_SCORE = 5
_MAX_PER_SECTOR = 2  # Dalio diversification: no more than this many picks per industry
_TRADING_TTL = 3_600
_IDLE_TTL = 14_400
_F_SCORE_TTL = 24 * 3_600
_SECTOR_STATS_TTL = 4 * 3_600
_VALUATION_HISTORY_TTL = 24 * 3_600

_STATE_FILE = DATA_DIR / "cache" / "value_picks.json"
_F_SCORE_FILE = DATA_DIR / "cache" / "value_f_scores.json"
_SECTOR_STATS_FILE = DATA_DIR / "cache" / "value_sector_stats.json"
_VALUATION_CACHE_FILE = DATA_DIR / "cache" / "value_valuation_history.json"


@dataclass
class FScoreBreakdown:
    ticker: str
    name: str
    sector: str
    f_score: int
    label: str
    checks: dict[str, int]
    is_financial_sector: bool
    report_date: str = ""


@dataclass
class ValueFactorSnapshot:
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    dividend_yield: float | None = None
    fcf_to_ev: float | None = None
    revenue_cagr_3y: float | None = None
    net_profit_cagr_3y: float | None = None
    rd_intensity: float | None = None
    roe_3y_avg: float | None = None
    gross_margin_stability: float | None = None
    ocf_to_profit: float | None = None
    debt_ratio: float | None = None
    profit_stability: float | None = None   # 盈利稳定性 CV（越低越好）
    revenue_stability: float | None = None  # 营收稳定性 CV（越低越好）
    # ── BMP 新增 ──────────────────────────────────────────────────────────────
    moat_score: float | None = None          # 护城河评分：毛利率水平+ROE水平+毛利率稳定性 (0–100)
    owner_earnings_yield: float | None = None  # 股东盈利收益率% = OCF/市值 (Buffett proxy)


@dataclass
class ValuePickItem:
    ticker: str
    name: str
    sector: str
    f_score: int
    value_score: float
    growth_score: float
    quality_score: float
    total_score: float
    market_regime: str
    weight_profile: dict[str, float]
    factors: ValueFactorSnapshot
    picked_at: str
    change_pct: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    degraded: bool = False


@dataclass
class ValuePicksState:
    picks: list[ValuePickItem]
    updated_at: str
    ttl: int
    market_regime: str
    degraded: bool = False


@dataclass
class SectorStats:
    debt_ratios: list[float] = field(default_factory=list)


def _is_trading_hours() -> bool:
    now = datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 25) <= t <= dtime(15, 5)


def _ttl_seconds() -> int:
    return _TRADING_TTL if _is_trading_hours() else _IDLE_TTL


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _current_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=CN_TZ)
        return dt
    except Exception:
        return None


def _is_file_fresh(path, ttl: int) -> bool:
    if not path.exists():
        return False
    try:
        age = datetime.now(CN_TZ).timestamp() - path.stat().st_mtime
        return age < ttl
    except Exception:
        return False


def _value_pick_to_dict(item: ValuePickItem) -> dict[str, Any]:
    data = asdict(item)
    data["factors"] = asdict(item.factors)
    return data


def _value_pick_from_dict(data: dict[str, Any]) -> ValuePickItem:
    return ValuePickItem(
        ticker=data.get("ticker", ""),
        name=data.get("name", ""),
        sector=data.get("sector", "其他"),
        f_score=int(data.get("f_score", 0) or 0),
        value_score=float(data.get("value_score", 0.0) or 0.0),
        growth_score=float(data.get("growth_score", 0.0) or 0.0),
        quality_score=float(data.get("quality_score", 0.0) or 0.0),
        total_score=float(data.get("total_score", 0.0) or 0.0),
        market_regime=data.get("market_regime", "震荡"),
        weight_profile=dict(data.get("weight_profile", {})),
        factors=ValueFactorSnapshot(**dict(data.get("factors", {}))),
        picked_at=data.get("picked_at", ""),
        change_pct=data.get("change_pct"),
        turnover=data.get("turnover"),
        turnover_rate=data.get("turnover_rate"),
        degraded=bool(data.get("degraded", False)),
    )


def _load_state() -> ValuePicksState | None:
    ensure_data_dir()
    if not _STATE_FILE.exists():
        return None
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return ValuePicksState(
            picks=[_value_pick_from_dict(item) for item in data.get("picks", [])],
            updated_at=data.get("updated_at", ""),
            ttl=int(data.get("ttl", _TRADING_TTL) or _TRADING_TTL),
            market_regime=data.get("market_regime", "震荡"),
            degraded=bool(data.get("degraded", False)),
        )
    except Exception:
        return None


def _save_state(state: ValuePicksState) -> None:
    ensure_data_dir()
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "picks": [_value_pick_to_dict(item) for item in state.picks],
        "updated_at": state.updated_at,
        "ttl": state.ttl,
        "market_regime": state.market_regime,
        "degraded": state.degraded,
    }
    _STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_state_fresh(state: ValuePicksState) -> bool:
    updated = _parse_dt(state.updated_at)
    if updated is None:
        return False
    return (datetime.now(CN_TZ) - updated).total_seconds() < _ttl_seconds()


def _load_json_cache(path, ttl: int) -> dict[str, Any] | None:
    if not _is_file_fresh(path, ttl):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json_cache(path, payload: dict[str, Any]) -> None:
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _f_score_label(score: int) -> str:
    if score >= 7:
        return "优质"
    if score >= _MIN_F_SCORE:
        return "合格"
    return "淘汰"


def _previous_roa(fs: FinancialStatements) -> float | None:
    return _safe_divide(fs.net_profit_1y_ago, fs.total_assets_1y_ago)


def _previous_debt_ratio(fs: FinancialStatements) -> float | None:
    return _safe_divide(fs.total_liabilities_1y_ago, fs.total_assets_1y_ago)


def _previous_current_ratio(fs: FinancialStatements) -> float | None:
    return _safe_divide(fs.current_assets_1y_ago, fs.current_liabilities_1y_ago)


def _passes_positive(value: float | None) -> int:
    return 1 if value is not None and value > 0 else 0


def _passes_increase(current: float | None, previous: float | None) -> int:
    return 1 if current is not None and previous is not None and current > previous else 0


def _passes_decrease(current: float | None, previous: float | None) -> int:
    return 1 if current is not None and previous is not None and current < previous else 0


def _passes_greater_than(left: float | None, right: float | None) -> int:
    return 1 if left is not None and right is not None and left > right else 0


def calculate_f_score(financials: FinancialStatements) -> FScoreBreakdown | None:
    """Calculate Piotroski F-Score with financial-sector substitutions.

    The score remains a 9-point binary checklist. For financial companies we keep
    the same score slots, but replace leverage/liquidity/turnover checks with
    indicators that are more meaningful for banks and brokers.
    """
    if not financials.f_score_ready():
        return None

    checks: dict[str, int] = {}
    roa_now = financials.roa()
    roa_prev = _previous_roa(financials)
    debt_now = financials.debt_ratio()
    debt_prev = _previous_debt_ratio(financials)
    current_now = financials.current_ratio()
    current_prev = _previous_current_ratio(financials)

    # Profitability: positive earnings, positive cash flow, improving ROA,
    # and cash profits stronger than accounting profits.
    checks["F1"] = _passes_positive(roa_now)
    checks["F2"] = _passes_positive(financials.operating_cf)
    checks["F3"] = _passes_increase(roa_now, roa_prev)
    checks["F4"] = _passes_greater_than(financials.operating_cf, financials.net_profit)

    if financials.is_financial_sector():
        # Financials use sector-specific balance-sheet quality proxies instead
        # of generic leverage / current ratio / asset-turnover checks.
        checks["F5"] = _passes_decrease(financials.npl_ratio, financials.npl_ratio_1y_ago)
        checks["F6"] = _passes_increase(financials.nim, financials.nim_1y_ago)
        checks["F7"] = _passes_increase(financials.car, financials.car_1y_ago)
        checks["F9"] = 0
    else:
        # Non-financials follow the standard Piotroski capital-structure tests.
        checks["F5"] = _passes_decrease(debt_now, debt_prev)
        checks["F6"] = _passes_increase(current_now, current_prev)
        if (
            financials.share_count is not None
            and financials.share_count_1y_ago not in (None, 0)
        ):
            checks["F7"] = 1 if financials.share_count <= financials.share_count_1y_ago else 0
        else:
            checks["F7"] = 0
        checks["F9"] = _passes_increase(
            financials.asset_turnover,
            financials.asset_turnover_1y_ago,
        )

    # Operating efficiency: expanding margin is good; asset turnover is only
    # meaningful for non-financials, so F9 stays zero for banks/brokers.
    checks["F8"] = 1 if (
        financials.gross_margin is not None
        and financials.gross_margin_1y_ago is not None
        and financials.gross_margin > financials.gross_margin_1y_ago
    ) else 0

    score = sum(checks.values())
    return FScoreBreakdown(
        ticker=financials.ticker,
        name=financials.name,
        sector=financials.sector or "其他",
        f_score=score,
        label=_f_score_label(score),
        checks=checks,
        is_financial_sector=financials.is_financial_sector(),
        report_date=financials.report_date,
    )


def _load_f_score_cache() -> dict[str, FScoreBreakdown]:
    data = _load_json_cache(_F_SCORE_FILE, _F_SCORE_TTL)
    if not data:
        return {}
    items = data.get("items", {})
    result: dict[str, FScoreBreakdown] = {}
    for ticker, item in items.items():
        try:
            result[ticker] = FScoreBreakdown(**item)
        except Exception:
            continue
    return result


def _save_f_score_cache(entries: dict[str, FScoreBreakdown]) -> None:
    payload = {
        "updated_at": _current_iso(),
        "items": {ticker: asdict(item) for ticker, item in entries.items()},
    }
    _save_json_cache(_F_SCORE_FILE, payload)


def _scan_one_f_score(ticker: str) -> tuple[str, FScoreBreakdown | None]:
    fs = get_financials(ticker)
    if fs is None:
        return ticker, None
    return ticker, calculate_f_score(fs)


def _load_universe() -> list[str]:
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty and "代码" in df.columns:
            codes = [str(code).strip() for code in df["代码"].tolist()]
            return [code for code in codes if len(code) == 6 and code.isdigit()]
    except Exception:
        pass

    fallback = fetch_sector_stock_list(limit=5000)
    return [item.ticker.split(".")[0] for item in fallback]


def scan_f_scores(
    tickers: list[str] | None = None,
    *,
    force_refresh: bool = False,
    max_workers: int = 16,
) -> tuple[dict[str, FScoreBreakdown], bool]:
    universe = [ticker.split(".")[0] for ticker in (tickers or _load_universe())]
    cached = {} if force_refresh else _load_f_score_cache()

    results: dict[str, FScoreBreakdown] = dict(cached)
    missing = [ticker for ticker in universe if ticker not in cached]
    degraded = False

    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_scan_one_f_score, ticker): ticker for ticker in missing}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    _, breakdown = future.result()
                    if breakdown is not None:
                        results[ticker] = breakdown
                except Exception:
                    degraded = True

    scoped = {ticker: results[ticker] for ticker in universe if ticker in results}
    if scoped:
        merged = dict(cached)
        merged.update(scoped)
        _save_f_score_cache(merged)
    return scoped, degraded


def _quote_map_for_codes(codes: list[str]) -> dict[str, Quote]:
    result: dict[str, Quote] = {}
    normalized_codes = [code.split(".")[0] for code in codes if code]
    if not normalized_codes:
        return result
    try:
        for quote in get_batch_quotes_tencent(normalized_codes):
            key = quote.ticker.split(".")[0]
            result[key] = quote
    except Exception:
        return result
    return result


def _sector_signature(items: list[tuple[FScoreBreakdown, FinancialStatements, Quote | None]]) -> str:
    raw = "|".join(sorted(item[0].ticker for item in items))
    return sha1(raw.encode("utf-8")).hexdigest()


def _build_sector_stats(
    items: list[tuple[FScoreBreakdown, FinancialStatements, Quote | None]],
) -> dict[str, SectorStats]:
    signature = _sector_signature(items)
    cached = _load_json_cache(_SECTOR_STATS_FILE, _SECTOR_STATS_TTL)
    if cached and cached.get("signature") == signature:
        try:
            return {
                sector: SectorStats(**payload)
                for sector, payload in cached.get("stats", {}).items()
            }
        except Exception:
            pass

    stats: dict[str, SectorStats] = {}
    for _, financials, quote in items:
        sector = financials.sector or "其他"
        bucket = stats.setdefault(sector, SectorStats())
        debt_ratio = financials.debt_ratio()
        if debt_ratio is not None and debt_ratio >= 0:
            bucket.debt_ratios.append(float(debt_ratio))

    payload = {
        "updated_at": _current_iso(),
        "signature": signature,
        "stats": {sector: asdict(bucket) for sector, bucket in stats.items()},
    }
    _save_json_cache(_SECTOR_STATS_FILE, payload)
    return stats


def detect_market_regime() -> tuple[str, dict[str, float]]:
    history = get_history("000300", period="6mo", interval="1d")
    if history.empty or "Close" not in history.columns or len(history) < 60:
        return "震荡", {"value": 0.30, "growth": 0.30, "quality": 0.40}

    ma60 = history["Close"].rolling(60).mean().dropna()
    if ma60.empty:
        return "震荡", {"value": 0.30, "growth": 0.30, "quality": 0.40}

    latest_price = float(history["Close"].iloc[-1])
    latest_ma = float(ma60.iloc[-1])
    slope_window = ma60.tail(min(20, len(ma60))).tolist()
    slope = 0.0
    if len(slope_window) >= 2:
        slope = (slope_window[-1] - slope_window[0]) / (len(slope_window) - 1)

    if latest_price > latest_ma and slope > 0:
        return "牛市", {"value": 0.25, "growth": 0.40, "quality": 0.35}
    if latest_price < latest_ma and slope < 0:
        return "熊市", {"value": 0.40, "growth": 0.25, "quality": 0.35}
    return "震荡", {"value": 0.30, "growth": 0.30, "quality": 0.40}


def _percentile_rank(value: float | None, values: list[float]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if value is None or not clean:
        return None
    below = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    rank = (below + 0.5 * equal) / len(clean)
    return round(rank * 100, 2)


def _scale_linear(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.0
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low) * 100)


def _scale_inverse(value: float | None, low: float, high: float) -> float:
    return 100.0 - _scale_linear(value, low, high)


def _coalesce_score(value: float | None, default: float) -> float:
    return default if value is None else value


def _load_valuation_cache() -> dict[str, dict[str, Any]]:
    data = _load_json_cache(_VALUATION_CACHE_FILE, _VALUATION_HISTORY_TTL)
    if not data:
        return {}
    cache = data.get("items", {})
    return cache if isinstance(cache, dict) else {}


def _save_valuation_cache(cache: dict[str, dict[str, Any]]) -> None:
    payload = {
        "updated_at": _current_iso(),
        "items": cache,
    }
    _save_json_cache(_VALUATION_CACHE_FILE, payload)


def _fetch_valuation_history(ticker: str) -> dict[str, list[float]]:
    code = ticker.split(".")[0]
    pe_values: list[float] = []
    pb_values: list[float] = []
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_a_lg_indicator(symbol=code)
        if df is not None and not df.empty:
            if "pe" in df.columns:
                pe_values = [float(v) for v in df["pe"].tolist() if v not in (None, 0) and float(v) > 0]
            if "pb" in df.columns:
                pb_values = [float(v) for v in df["pb"].tolist() if v not in (None, 0) and float(v) > 0]
    except Exception:
        pass

    if not pe_values and not pb_values:
        return {"pe": [], "pb": []}

    recent_pe = pe_values[-1250:] if len(pe_values) > 1250 else pe_values
    recent_pb = pb_values[-1250:] if len(pb_values) > 1250 else pb_values
    return {"pe": recent_pe, "pb": recent_pb}


def _historical_valuation_percentiles(
    ticker: str,
    current_pe: float | None,
    current_pb: float | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    cache = _load_valuation_cache()
    code = ticker.split(".")[0]
    item = cache.get(code)
    if item is None:
        item = _fetch_valuation_history(code)
        cache[code] = item
        _save_valuation_cache(cache)
    pe_pct, pe_score = valuation_percentile_score(current_pe, item.get("pe", []))
    pb_pct, pb_score = valuation_percentile_score(current_pb, item.get("pb", []))
    return pe_pct, pb_pct, pe_score, pb_score


def _calculate_cagr(current: float | None, old: float | None, years: int = 3) -> float | None:
    if current is None or old is None or current <= 0 or old <= 0 or years <= 0:
        return None
    try:
        return (current / old) ** (1 / years) - 1
    except Exception:
        return None


def _gross_margin_stability_score(values: list[float | None]) -> tuple[float, float | None]:
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return 0.0, None
    mean = sum(clean) / len(clean)
    if mean == 0:
        return 0.0, None
    ratio = pstdev(clean) / abs(mean)
    return round(_clamp(100 - ratio * 100, 0, 100), 2), round(ratio, 4)


def _roe_consistency_score(financials: FinancialStatements) -> tuple[float, float | None]:
    values = [financials.roe, financials.roe_1y_ago, financials.roe_2y_ago]
    clean = [value for value in values if value is not None]
    if not clean:
        return 0.0, None
    avg_roe = sum(clean) / len(clean)
    passed = sum(1 for value in clean if value > 0.12)
    score = (passed / 3) * 100
    return round(score, 2), round(avg_roe * 100, 2)


def _earnings_stability_score(values: list[float | None]) -> tuple[float, float | None]:
    """Return a stability score [0–100] based on CV, with two safeguards.

    * Fewer than 2 data points → 0.0 (insufficient history).
    * Any non-positive value in *values* → 0.0 (loss-making periods disqualify
      stability; CV becomes meaningless when the mean is near zero or negative).
    * Otherwise mirrors ``_gross_margin_stability_score``.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return 0.0, None
    if any(v <= 0 for v in clean):
        return 0.0, None
    mean = sum(clean) / len(clean)
    if mean == 0:
        return 0.0, None
    ratio = pstdev(clean) / mean  # mean > 0 guaranteed above
    return round(_clamp(100 - ratio * 100, 0, 100), 2), round(ratio, 4)


def _moat_score(financials: FinancialStatements) -> tuple[float, dict]:
    """Quantify economic moat — Buffett & Munger's primary quality criterion.

    Three signals (all measured on a 0–100 scale):
    1. **Gross margin level** — High absolute margin = pricing power.
       Buffett avoids commodity businesses (<20% GM); exceptional moats > 60%.
    2. **Sustained ROE level** — Durable high returns on equity = competitive
       advantage that repels rivals. < 8% = weak; > 25% = exceptional.
    3. **Gross margin stability** — Consistent margins mean the moat holds up
       under competition and economic cycles (CV-based, already in use).

    Weights: GM level 40% + ROE level 40% + GM stability 20%
    """
    # 1. Gross margin level: 15% → 0, 65% → 100
    gm = financials.gross_margin
    gm_level = _scale_linear(gm, 0.15, 0.65) if gm is not None else 35.0

    # 2. Average ROE level across up to 3 years: 8% → 0, 25% → 100
    roe_values = [v for v in [financials.roe, financials.roe_1y_ago, financials.roe_2y_ago] if v is not None]
    avg_roe = sum(roe_values) / len(roe_values) if roe_values else None
    roe_level = _scale_linear(avg_roe, 0.08, 0.25) if avg_roe is not None else 25.0

    # 3. Gross margin stability (CV-based)
    margin_stab, _ = _gross_margin_stability_score([
        financials.gross_margin,
        financials.gross_margin_1y_ago,
        financials.gross_margin_2y_ago,
    ])

    score = round(
        gm_level * 0.40
        + roe_level * 0.40
        + margin_stab * 0.20,
        2,
    )
    return score, {
        "gm_level": round(gm_level, 2),
        "roe_level": round(roe_level, 2),
        "margin_stab": round(margin_stab, 2),
        "avg_roe_pct": round(avg_roe * 100, 2) if avg_roe is not None else None,
    }


def _owner_earnings_yield(
    financials: FinancialStatements,
    quote: Quote | None,
) -> float | None:
    """Buffett's 'owner earnings' yield — operating cash flow relative to market cap.

    owner_earnings ≈ operating_cf  (simplified; maintenance capex not in current model)
    yield (%) = operating_cf / market_cap × 100

    A yield of 8–12% suggests the market is pricing a quality business fairly;
    >12% is potentially undervalued from Buffett's perspective.

    Returns None when OCF ≤ 0 or market cap is unavailable.
    """
    if financials.operating_cf is None or financials.operating_cf <= 0:
        return None

    # Prefer real-time market cap from Tencent batch quote (亿元 → 元)
    market_cap: float | None = None
    if quote is not None and quote.market_cap_total not in (None, 0):
        market_cap = quote.market_cap_total * 1e8
    if market_cap in (None, 0):
        mv = get_market_value(financials.ticker)
        mkt_cap_yi = mv.get("market_cap")
        if mkt_cap_yi:
            market_cap = float(mkt_cap_yi) * 1e8
    if market_cap in (None, 0):
        return None
    return round(financials.operating_cf / market_cap * 100, 3)


def _three_year_sum(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if len(clean) < 3:
        return None
    return float(sum(clean))


def _ocf_quality_score(financials: FinancialStatements) -> tuple[float, float | None]:
    ocf_sum = _three_year_sum([
        financials.operating_cf,
        financials.operating_cf_1y_ago,
        financials.operating_cf_2y_ago,
    ])
    profit_sum = _three_year_sum([
        financials.net_profit,
        financials.net_profit_1y_ago,
        financials.net_profit_2y_ago,
    ])
    if profit_sum in (None, 0) or ocf_sum is None:
        return 0.0, None
    ratio = ocf_sum / profit_sum
    return round(_clamp(ratio * 100, 0, 100), 2), round(ratio, 4)


def _rd_intensity_score(financials: FinancialStatements) -> tuple[float, float | None]:
    if financials.rd_expense is None or financials.revenue in (None, 0):
        return 0.0, None
    ratio = financials.rd_expense / financials.revenue * 100
    multiplier = 1.3 if financials.sector in {"电子", "计算机", "通信"} else 1.0
    return round(_clamp(ratio * 3 * multiplier, 0, 100), 2), round(ratio, 2)


def _fcf_to_ev(financials: FinancialStatements, quote: Quote | None) -> float | None:
    if quote is None:
        return None
    fundamentals = get_fundamentals(financials.ticker)
    free_cash_flow = fundamentals.get("free_cash_flow")
    if free_cash_flow is None:
        return None
    market_cap = quote.market_cap_total
    if market_cap in (None, 0):
        if quote.price in (None, 0) or financials.share_count in (None, 0):
            return None
        market_cap = quote.price * financials.share_count / 100_000_000
    if market_cap in (None, 0):
        return None
    enterprise_value = market_cap * 100_000_000 + (financials.total_liabilities or 0)
    if enterprise_value <= 0:
        return None
    return free_cash_flow / enterprise_value * 100


def score_value_stock(
    breakdown: FScoreBreakdown,
    financials: FinancialStatements,
    quote: Quote | None,
    sector_stats: dict[str, SectorStats],
    market_regime: str,
    weights: dict[str, float],
    *,
    picked_at: str,
) -> ValuePickItem:
    sector = financials.sector or "其他"
    bucket = sector_stats.get(sector, SectorStats())
    market_values = get_market_value(financials.ticker)

    pe = quote.pe_ttm if quote is not None else market_values.get("pe")
    pb = quote.pb_ratio if quote is not None else market_values.get("pb")
    dividend_yield = market_values.get("dividend_yield")
    debt_ratio = financials.debt_ratio()
    fcf_to_ev = _fcf_to_ev(financials, quote)
    oey = _owner_earnings_yield(financials, quote)

    pe_pct, pb_pct, pe_score, pb_score = _historical_valuation_percentiles(financials.ticker, pe, pb)
    debt_pct = _percentile_rank(debt_ratio, bucket.debt_ratios)

    # ── Value score ────────────────────────────────────────────────────────
    # Buffett: "Price is what you pay, value is what you get."
    # Earnings power = FCF/EV when available (more precise), otherwise owner
    # earnings yield (OCF/market_cap) as reliable fallback.
    # Weight shift: earnings power raised to 30% because both Buffett and
    # Munger emphasise that cash generation is the only real value metric.
    earnings_power = fcf_to_ev if fcf_to_ev is not None else oey
    dividend_score = None if dividend_yield is None else _scale_linear(dividend_yield, 0, 8)
    ep_score = None if earnings_power is None else _scale_linear(earnings_power, 0, 12)
    value_score_raw, _ = weighted_mean_available(
        [
            (pe_score, 0.25),
            (pb_score, 0.25),
            (dividend_score, 0.20),
            (ep_score, 0.30),
        ]
    )
    value_score = round(value_score_raw or 0.0, 2)

    revenue_cagr = _calculate_cagr(financials.revenue, financials.revenue_3y_ago)
    profit_cagr = _calculate_cagr(financials.net_profit, financials.net_profit_3y_ago)
    rd_score, rd_ratio = _rd_intensity_score(financials)
    growth_score = round(
        (
            _scale_linear(None if revenue_cagr is None else revenue_cagr * 100, -10, 30) * 0.35
            + _scale_linear(None if profit_cagr is None else profit_cagr * 100, -10, 35) * 0.35
            + rd_score * 0.30
        ),
        2,
    )

    # ── Quality score ──────────────────────────────────────────────────────
    # BMP 护城河 (M) + Buffett 盈利稳定性 + Munger 现金流 + Dalio 去杠杆
    #
    # 权重设计依据：
    #   moat_score    35% — 护城河核心：毛利率水平(40%)+ROE水平(40%)+毛利率稳定(20%)
    #                        取代原来分散的 ROE连续性(25%) 和 毛利率稳定(20%)，
    #                        但新增了"绝对水平"维度，高ROE/高毛利公司得到更高奖励。
    #   profit_stab   25% — 盈利CV稳定性（Buffett：无法预测的公司不值得持有）
    #   ocf_score     25% — OCF/净利润 3年均值（Munger：只有现金才是真实利润）
    #   leverage      15% — 负债率相对行业（Dalio：债务是文明最大风险）
    moat_val, _ = _moat_score(financials)
    roe_score, roe_avg = _roe_consistency_score(financials)  # kept for factors display
    margin_score, margin_ratio = _gross_margin_stability_score(
        [
            financials.gross_margin,
            financials.gross_margin_1y_ago,
            financials.gross_margin_2y_ago,
        ]
    )
    ocf_score, ocf_ratio = _ocf_quality_score(financials)
    leverage_score = None if debt_pct is None else 100 - debt_pct
    profit_stab_score, profit_stab_ratio = _earnings_stability_score(
        [financials.net_profit, financials.net_profit_1y_ago, financials.net_profit_2y_ago]
    )
    revenue_stab_score, revenue_stab_ratio = _earnings_stability_score(
        [financials.revenue, financials.revenue_1y_ago, financials.revenue_2y_ago]
    )
    quality_score_raw, _ = weighted_mean_available(
        [
            (moat_val, 0.35),
            (profit_stab_score, 0.25),
            (ocf_score, 0.25),
            (leverage_score, 0.15),
        ]
    )
    quality_score = round(quality_score_raw or 0.0, 2)

    total_score = round(
        value_score * weights["value"]
        + growth_score * weights["growth"]
        + quality_score * weights["quality"],
        2,
    )

    return ValuePickItem(
        ticker=breakdown.ticker,
        name=breakdown.name,
        sector=breakdown.sector,
        f_score=breakdown.f_score,
        value_score=value_score,
        growth_score=growth_score,
        quality_score=quality_score,
        total_score=total_score,
        market_regime=market_regime,
        weight_profile=weights,
        factors=ValueFactorSnapshot(
            pe_percentile=pe_pct,
            pb_percentile=pb_pct,
            dividend_yield=round(dividend_yield, 2) if dividend_yield is not None else None,
            fcf_to_ev=round(fcf_to_ev, 2) if fcf_to_ev is not None else None,
            revenue_cagr_3y=round(revenue_cagr * 100, 2) if revenue_cagr is not None else None,
            net_profit_cagr_3y=round(profit_cagr * 100, 2) if profit_cagr is not None else None,
            rd_intensity=rd_ratio,
            roe_3y_avg=roe_avg,
            gross_margin_stability=margin_ratio,
            ocf_to_profit=ocf_ratio,
            debt_ratio=round(debt_ratio * 100, 2) if debt_ratio is not None else None,
            profit_stability=profit_stab_ratio,
            revenue_stability=revenue_stab_ratio,
            moat_score=round(moat_val, 2),
            owner_earnings_yield=oey,
        ),
        picked_at=picked_at,
        change_pct=quote.change_pct if quote is not None else None,
        turnover=quote.turnover if quote is not None else None,
        turnover_rate=quote.turnover_rate if quote is not None else None,
    )


def _trading_days_since(iso_value: str) -> int:
    start = _parse_dt(iso_value)
    if start is None:
        return 0
    current = datetime.now(CN_TZ).date()
    day = start.date()
    count = 0
    while day < current:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def _candidate_half_cutoff(candidates: list[ValuePickItem]) -> float:
    if not candidates:
        return 0.0
    scores = sorted((item.total_score for item in candidates), reverse=True)
    return scores[min(len(scores) // 2, len(scores) - 1)]


def _is_underperforming(
    item: ValuePickItem,
    latest: ValuePickItem | None,
    cutoff_score: float,
) -> bool:
    if latest is None:
        return True
    if latest.change_pct is None or latest.turnover is None:
        return True
    if latest.f_score < _MIN_F_SCORE:
        return True

    # Buffett: short-term price drops in high-moat companies are opportunities,
    # not reasons to sell.  Allow a wider daily decline tolerance when the moat
    # score (护城河) is strong (> 65).
    moat = latest.factors.moat_score
    if moat is not None and moat > 65:
        # High moat: tolerate up to -3% daily, require higher liquidity proof
        if latest.change_pct < -3.0:
            return True
        if latest.turnover < 2e7:
            return True
    else:
        # Standard threshold
        if latest.change_pct < -1.5:
            return True
        if latest.turnover < 5e7:
            return True

    if _trading_days_since(item.picked_at) > 10 and latest.total_score < cutoff_score:
        return True
    return False


def _apply_sector_diversification(
    keep: list[ValuePickItem],
    ranked: list[ValuePickItem],
    *,
    pool_size: int = _POOL_SIZE,
    max_per_sector: int = _MAX_PER_SECTOR,
) -> list[ValuePickItem]:
    """Fill a pool of up to ``pool_size`` picks under a per-industry cap.

    ``keep`` (healthy existing picks) is retained verbatim to avoid churn; the
    remaining slots are taken from ``ranked`` in score order, skipping any item
    whose sector already holds ``max_per_sector`` entries.

    Fallback relaxation: when the candidate universe is too concentrated to fill
    the pool under ``max_per_sector``, the cap is raised one step at a time until
    the pool is full or candidates are exhausted. This guarantees a full pool
    rather than silently under-filling on a lopsided market.
    """
    result = list(keep)
    seen_tickers = {item.ticker for item in result}

    cap = max_per_sector
    while len(result) < pool_size:
        sector_counts: dict[str, int] = {}
        for item in result:
            sector_counts[item.sector] = sector_counts.get(item.sector, 0) + 1

        added = 0
        for item in ranked:
            if len(result) >= pool_size:
                break
            if item.ticker in seen_tickers:
                continue
            if sector_counts.get(item.sector, 0) >= cap:
                continue
            result.append(item)
            seen_tickers.add(item.ticker)
            sector_counts[item.sector] = sector_counts.get(item.sector, 0) + 1
            added += 1

        if added:
            continue
        # Nothing could enter at the current cap. If candidates remain they all
        # sit in saturated sectors, so relax the cap; otherwise we are done.
        if not any(item.ticker not in seen_tickers for item in ranked):
            break
        cap += 1

    return result


def _merge_pool(
    current: list[ValuePickItem],
    ranked: list[ValuePickItem],
) -> list[ValuePickItem]:
    if not current:
        return _apply_sector_diversification([], ranked)

    by_ticker = {item.ticker.split(".")[0]: item for item in ranked}
    cutoff = _candidate_half_cutoff(ranked)

    underperforming: list[ValuePickItem] = []
    healthy: list[ValuePickItem] = []
    for existing in current:
        latest = by_ticker.get(existing.ticker.split(".")[0])
        if _is_underperforming(existing, latest, cutoff):
            underperforming.append(existing)
        elif latest is not None:
            latest.picked_at = existing.picked_at or latest.picked_at
            healthy.append(latest)

    max_replace = _MAX_REPLACE_LARGE if len(underperforming) > 3 else _MAX_REPLACE
    keep_limit = _POOL_SIZE - max_replace
    keep = healthy[:keep_limit]
    merged = _apply_sector_diversification(keep, ranked)
    merged.sort(key=lambda item: (item.total_score, item.f_score), reverse=True)
    return merged


def _load_incremental_candidates(current: list[ValuePickItem]) -> list[str]:
    tickers = {item.ticker.split(".")[0] for item in current}
    try:
        for quote in fetch_sector_stock_list(limit=_INCREMENTAL_CANDIDATES):
            if quote.turnover is None or quote.turnover < 5e7:
                continue
            tickers.add(quote.ticker.split(".")[0])
    except Exception:
        pass
    return list(tickers)


def _prepare_scoring_set(
    tickers: list[str],
    *,
    force_refresh: bool,
) -> tuple[list[tuple[FScoreBreakdown, FinancialStatements, Quote | None]], bool]:
    f_scores, degraded = scan_f_scores(tickers, force_refresh=force_refresh)
    passed = [item for item in f_scores.values() if item.f_score >= _MIN_F_SCORE]
    codes = [item.ticker for item in passed]
    quotes = _quote_map_for_codes(codes)

    scored_inputs: list[tuple[FScoreBreakdown, FinancialStatements, Quote | None]] = []
    for breakdown in passed:
        financials = get_financials(breakdown.ticker)
        if financials is None:
            degraded = True
            continue
        quote = quotes.get(breakdown.ticker.split(".")[0])
        scored_inputs.append((breakdown, financials, quote))
    return scored_inputs, degraded


def _refresh_value_picks(current: list[ValuePickItem]) -> ValuePicksState:
    trading = _is_trading_hours()
    tickers = _load_incremental_candidates(current) if trading else _load_universe()
    scored_inputs, degraded = _prepare_scoring_set(tickers, force_refresh=not trading)

    market_regime, weights = detect_market_regime()
    if not scored_inputs:
        return ValuePicksState(
            picks=current,
            updated_at=_current_iso(),
            ttl=_ttl_seconds(),
            market_regime=market_regime,
            degraded=True,
        )

    sector_stats = _build_sector_stats(scored_inputs)
    picked_lookup = {
        item.ticker.split(".")[0]: item.picked_at for item in current if item.picked_at
    }
    ranked: list[ValuePickItem] = []
    for breakdown, financials, quote in scored_inputs:
        item = score_value_stock(
            breakdown,
            financials,
            quote,
            sector_stats,
            market_regime,
            weights,
            picked_at=picked_lookup.get(breakdown.ticker.split(".")[0], _current_iso()),
        )
        ranked.append(item)

    ranked.sort(key=lambda item: (item.total_score, item.f_score), reverse=True)
    picks = _merge_pool(current, ranked)
    return ValuePicksState(
        picks=picks,
        updated_at=_current_iso(),
        ttl=_ttl_seconds(),
        market_regime=market_regime,
        degraded=degraded,
    )


def get_value_picks(force: bool = False) -> ValuePicksState:
    state = _load_state()
    if not force and state is not None and _is_state_fresh(state):
        return state

    current = state.picks if state is not None else []
    try:
        new_state = _refresh_value_picks(current)
    except Exception:
        new_state = ValuePicksState(
            picks=current,
            updated_at=_current_iso(),
            ttl=_ttl_seconds(),
            market_regime=state.market_regime if state else "震荡",
            degraded=True,
        )

    _save_state(new_state)
    return new_state


def refresh_value_picks() -> ValuePicksState:
    return get_value_picks(force=True)


def get_value_picks_brief(force: bool = False) -> dict[str, Any]:
    state = get_value_picks(force=force)
    return {
        "picks": [
            {
                "ticker": item.ticker,
                "name": item.name,
                "total_score": item.total_score,
                "f_score": item.f_score,
            }
            for item in state.picks
        ],
        "updated_at": state.updated_at,
        "market_regime": state.market_regime,
        "ttl": state.ttl,
        "degraded": state.degraded,
    }