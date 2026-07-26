"""智能精选 — 基于板块轮动的多因子动态股票筛选。

独立于手动自选 (watchlist)，结果存储在 DATA_DIR/cache/smart_picks.json。

池规则：
  - 池容量：最多 10 只
  - 每次刷新最多替换 3 只（若当前池内"有问题"股票 > 3 只，则允许替换 5 只）
  - "有问题"定义：当日跌幅 < -1.5%，或成交额 < 5000 万（流动性枯竭），
    或无法获取行情（可能停牌/退市）
  - TTL：交易时段（09:25–15:05）1 小时；非交易时段 4 小时
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from invest_app.config import DATA_DIR, ensure_data_dir

CN_TZ = ZoneInfo("Asia/Shanghai")

_CACHE_FILE = DATA_DIR / "cache" / "smart_picks.json"

_POOL_SIZE = 10          # 池容量
_TOP_SECTORS = 5         # 每次选取最热 N 个板块
_CANDIDATES_PER_SECTOR = 30  # 每个板块取候选股数量
_TTL_TRADING = 3_600     # 盘中 TTL（秒）
_TTL_IDLE = 14_400       # 盘外 TTL（秒）


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SmartPickItem:
    ticker: str
    name: str
    sector: str           # 所属板块名称
    sector_code: str      # 所属板块代码
    change_pct: float     # 当日涨跌幅 (%)
    turnover: float       # 成交额 (元)
    turnover_rate: float | None  # 换手率 (%)
    score: float          # 综合评分 (0–100)
    picked_at: str        # 首次入池的 ISO 时间戳


@dataclass
class SmartPicksState:
    picks: list[SmartPickItem]
    updated_at: str   # 最近一次刷新的 ISO 时间戳
    ttl: int          # 本次缓存有效期（秒）


# ---------------------------------------------------------------------------
# TTL helpers
# ---------------------------------------------------------------------------

def _is_trading_hours() -> bool:
    now = datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 25) <= t <= dtime(15, 5)


def _ttl_seconds() -> int:
    return _TTL_TRADING if _is_trading_hours() else _TTL_IDLE


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_state() -> SmartPicksState | None:
    ensure_data_dir()
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        picks = [SmartPickItem(**p) for p in data.get("picks", [])]
        return SmartPicksState(
            picks=picks,
            updated_at=data.get("updated_at", ""),
            ttl=data.get("ttl", _TTL_TRADING),
        )
    except Exception:
        return None


def _save_state(state: SmartPicksState) -> None:
    ensure_data_dir()
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "picks": [asdict(p) for p in state.picks],
        "updated_at": state.updated_at,
        "ttl": state.ttl,
    }
    _CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_fresh(state: SmartPicksState) -> bool:
    if not state.updated_at:
        return False
    try:
        updated = datetime.fromisoformat(state.updated_at)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=CN_TZ)
        age = (datetime.now(CN_TZ) - updated).total_seconds()
        return age < _ttl_seconds()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Scoring & filtering
# ---------------------------------------------------------------------------

def _score_quote(
    change_pct: float,
    turnover: float,
    turnover_rate: float | None,
    sector_rank: int,
    total_sectors: int,
) -> float:
    """多因子综合评分 (0–100):
      换手率    40% — turnover_rate 归一化至 5%满分
      涨幅贡献  30% — change_pct 正向 clamp 至 [0, 8]
      板块热度  20% — 板块排名 (rank 0 = 最热)
      成交额    10% — log 归一化
    """
    tr = turnover_rate or 0.0
    tr_score = min(tr / 5.0, 1.0) * 40

    cp_score = (max(0.0, min(change_pct, 8.0)) / 8.0) * 30

    sector_score = ((total_sectors - sector_rank) / max(total_sectors, 1)) * 20

    to_score = 0.0
    if turnover > 0:
        # log10(5000万)≈7.7 → 0, log10(100亿)≈10 → 1.0
        to_score = max(0.0, min((math.log10(turnover) - 7.7) / 2.3, 1.0)) * 10

    return round(tr_score + cp_score + sector_score + to_score, 2)


def _is_valid_quote(name: str, price: float, change_pct: float, turnover: float) -> bool:
    """过滤不符合条件的股票：ST/退市/停牌/涨跌停/流动性过差。"""
    name_up = name.upper()
    if "ST" in name_up or "退" in name_up:
        return False
    if price < 2.0:
        return False
    if turnover < 5e7:          # 成交额 < 5000 万视为流动性不足
        return False
    if abs(change_pct) > 9.5:   # 涨跌停不追
        return False
    return True


# ---------------------------------------------------------------------------
# Underperforming check
# ---------------------------------------------------------------------------

def _is_underperforming(
    pick: SmartPickItem,
    current_quotes: "dict[str, Any]",  # type: ignore[type-arg]
) -> bool:
    """判断一只精选股是否当前"有问题"（候选替换）。"""
    from invest_app.market_data import Quote
    q: Quote | None = current_quotes.get(pick.ticker) or current_quotes.get(
        pick.ticker.split(".")[0]
    )
    if q is None:
        return True  # 拉不到行情 → 停牌/退市风险
    if q.change_pct < -1.5:
        return True  # 跌幅超 1.5%
    if (q.turnover or 0) < 5e7:
        return True  # 流动性变差
    return False


# ---------------------------------------------------------------------------
# Core refresh logic
# ---------------------------------------------------------------------------

def _refresh_picks(current_picks: list[SmartPickItem]) -> list[SmartPickItem]:
    """执行完整选股流程，返回更新后的精选池。"""
    from invest_app.market_data import (
        fetch_hot_sectors,
        fetch_sector_stock_list,
        get_batch_quotes_tencent,
    )

    # ── 1. 获取热门板块 ────────────────────────────────────────────────────
    hot_sectors = fetch_hot_sectors(limit=_TOP_SECTORS)
    if not hot_sectors:
        return current_picks  # 网络失败，保留现有

    # ── 2. 并发拉取各板块候选股 ────────────────────────────────────────────
    # (quote, sector_name, sector_code, sector_rank)
    all_candidates: list[tuple] = []

    def _fetch_one(idx_sector: tuple) -> tuple:
        idx, sector = idx_sector
        quotes = fetch_sector_stock_list(sector["code"], limit=_CANDIDATES_PER_SECTOR)
        return idx, sector, quotes

    with ThreadPoolExecutor(max_workers=min(len(hot_sectors), 5)) as pool:
        futures = [pool.submit(_fetch_one, (i, s)) for i, s in enumerate(hot_sectors)]
        for fut in as_completed(futures):
            try:
                idx, sector, quotes = fut.result(timeout=15)
                for q in quotes:
                    if _is_valid_quote(q.name, q.price, q.change_pct, q.turnover or 0):
                        all_candidates.append((q, sector["name"], sector["code"], idx))
            except Exception:
                continue

    if not all_candidates:
        return current_picks  # 全部板块拉取失败，保留现有

    # ── 3. 打分 + 去重排序 ─────────────────────────────────────────────────
    scored: list[tuple[float, object, str, str]] = []
    n_sectors = len(hot_sectors)
    for q, s_name, s_code, s_rank in all_candidates:
        sc = _score_quote(
            q.change_pct, q.turnover or 0.0, q.turnover_rate, s_rank, n_sectors
        )
        scored.append((sc, q, s_name, s_code))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 同一只股票可能出现在多个板块，保留得分最高的那次
    seen: set[str] = set()
    deduped: list[tuple[float, object, str, str]] = []
    for entry in scored:
        t = entry[1].ticker  # type: ignore[attr-defined]
        if t not in seen:
            seen.add(t)
            deduped.append(entry)

    # ── 4. 实时评估当前池内股票健康状况 ────────────────────────────────────
    current_quotes: dict = {}
    if current_picks:
        try:
            codes = [p.ticker.split(".")[0] for p in current_picks]
            q_list = get_batch_quotes_tencent(codes)
            for q in q_list:
                current_quotes[q.ticker] = q
                current_quotes[q.ticker.split(".")[0]] = q
        except Exception:
            pass

    underperforming = [p for p in current_picks if _is_underperforming(p, current_quotes)]
    # 超过 3 只有问题时，允许替换 5 只；否则最多 3 只
    max_replace = 5 if len(underperforming) > 3 else 3

    # ── 5. 构建新池 ────────────────────────────────────────────────────────
    now_iso = datetime.now(CN_TZ).isoformat()

    # 保留健康的股票（上限：POOL_SIZE - max_replace，给新候选留空位）
    keep_limit = _POOL_SIZE - max_replace
    keep = [p for p in current_picks if p not in underperforming][:keep_limit]
    keep_tickers = {p.ticker for p in keep}

    new_items: list[SmartPickItem] = []
    for sc, q, s_name, s_code in deduped:  # type: ignore[misc]
        if len(new_items) >= _POOL_SIZE - len(keep):
            break
        if q.ticker in keep_tickers:  # type: ignore[union-attr]
            continue
        new_items.append(
            SmartPickItem(
                ticker=q.ticker,  # type: ignore[union-attr]
                name=q.name,  # type: ignore[union-attr]
                sector=s_name,
                sector_code=s_code,
                change_pct=q.change_pct,  # type: ignore[union-attr]
                turnover=q.turnover or 0.0,  # type: ignore[union-attr]
                turnover_rate=q.turnover_rate,  # type: ignore[union-attr]
                score=sc,
                picked_at=now_iso,
            )
        )

    return keep + new_items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_smart_picks(force: bool = False) -> SmartPicksState:
    """返回智能精选状态。

    - 缓存新鲜时直接返回缓存（0 网络调用）
    - force=True 或缓存过期时执行完整选股并更新缓存
    """
    state = _load_state()
    if not force and state is not None and _is_fresh(state):
        return state

    current_picks = state.picks if state else []
    try:
        new_picks = _refresh_picks(current_picks)
    except Exception:
        # 任何异常都保留旧结果，不崩溃服务
        new_picks = current_picks

    new_state = SmartPicksState(
        picks=new_picks,
        updated_at=datetime.now(CN_TZ).isoformat(),
        ttl=_ttl_seconds(),
    )
    _save_state(new_state)
    return new_state
