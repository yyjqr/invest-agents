"""BMP 增补因子：Graham 过滤、Sloan 应计、估值分位修复与股息率利差、横截面标准化与因子快照。

设计原则
- 纯函数、无网络、无全局状态，便于单测；只有 ``fetch_*`` 会延迟导入 akshare。
- 缺失数据一律返回 ``None``，由调用方用 :func:`weighted_mean_available` 重新归一权重，
  不再用 ``x or 50.0`` 这种写法（0.0 会被误判为缺失）。
- 单位沿用现有代码：金额为元，股本为股，股息率/国债收益率为百分数（3.2 表示 3.2%），
  ROE/负债率等比例为小数。
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # 仅类型检查时导入，避免模块加载时依赖行情/财报模块
    from invest_app.financial_data import FinancialStatements

# 债券属性强、估值应结合利率看的行业（与 financial_data 的行业命名保持一致，按需增删）
BOND_LIKE_SECTORS = {"公用事业", "电力", "水电", "高速公路", "银行"}
FINANCIAL_SECTORS = {"银行", "非银金融"}
UTILITY_SECTORS = {"公用事业", "电力", "水电"}


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _num(value: Any) -> float | None:
    """转成有限 float，None/NaN/inf 一律返回 None。"""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def percentile_rank(
    value: float | None,
    sample: Iterable[float | None],
    *,
    min_n: int = 30,
) -> float | None:
    """中位秩分位（0–100）。value 缺失或样本不足 min_n 时返回 None（不是 0，也不是 50）。"""
    x = _num(value)
    clean = sorted(v for v in (_num(s) for s in sample) if v is not None)
    if x is None or len(clean) < min_n:
        return None
    below = bisect_left(clean, x)
    equal = bisect_right(clean, x) - below
    return round((below + 0.5 * equal) / len(clean) * 100, 2)


def weighted_mean_available(
    pairs: Sequence[tuple[float | None, float]],
) -> tuple[float | None, float]:
    """对 (分数, 权重) 列表求加权均值，缺失项剔除并重新归一。

    返回 (均值或 None, 覆盖率)。覆盖率 = 有数据的权重 / 总权重，可用来给低覆盖打折或标红。
    """
    total_w = sum(w for _, w in pairs)
    got = [(s, w) for s, w in pairs if s is not None and w > 0]
    used_w = sum(w for _, w in got)
    if total_w <= 0 or used_w <= 0:
        return None, 0.0
    return round(sum(s * w for s, w in got) / used_w, 2), round(used_w / total_w, 4)


def scale_linear(value: float | None, low: float, high: float, *, inverse: bool = False) -> float | None:
    """线性映射到 0–100，缺失返回 None（原实现缺失返回 0，会被当成“很差”）。"""
    x = _num(value)
    if x is None or high <= low:
        return None
    score = _clamp((x - low) / (high - low) * 100)
    return round(100 - score if inverse else score, 2)


# ---------------------------------------------------------------------------
# 1. Graham 防御型过滤 + 格雷厄姆数
# ---------------------------------------------------------------------------

@dataclass
class GrahamResult:
    checks: dict[str, bool | None]
    graham_number: float | None = None
    margin_of_safety: float | None = None
    bvps_note: str = "BVPS 为 (总资产-总负债)/总股本，含少数股东权益，是近似值"

    @property
    def evaluated(self) -> int:
        return sum(1 for v in self.checks.values() if v is not None)

    @property
    def passed(self) -> int:
        return sum(1 for v in self.checks.values() if v is True)

    @property
    def pass_ratio(self) -> float | None:
        return None if self.evaluated == 0 else round(self.passed / self.evaluated, 4)

    def passes_gate(self, min_ratio: float = 0.8, min_evaluated: int = 4) -> bool:
        """硬过滤：可评估项不少于 min_evaluated 且通过率不低于 min_ratio。"""
        ratio = self.pass_ratio
        return ratio is not None and self.evaluated >= min_evaluated and ratio >= min_ratio


def graham_screen(
    fs: "FinancialStatements",
    *,
    price: float | None,
    pe: float | None,
    pb: float | None,
    market_cap_yi: float | None = None,
    min_market_cap_yi: float = 50.0,
    min_current_ratio: float = 1.5,
    max_debt_ratio: float = 0.70,
    max_pe_x_pb: float = 22.5,
) -> GrahamResult:
    """A 股适配版 Graham 防御型七项检查。

    G1 近 4 年净利润均为正；G2 有派息；G3 三年净利润增长为正；
    G4 流动比率 ≥ 1.5（金融、公用事业不适用）；G5 资产负债率 ≤ 70%（金融不适用）；
    G6 PE×PB ≤ 22.5（亏损或缺数据视为不通过）；G7 市值不小于 min_market_cap_yi 亿。
    Graham 原文要求 10 年盈利/20 年派息，财报接口只有约 4 年，这里是缩短版，请勿等同原版。
    """
    sector = fs.sector or ""
    is_fin = sector in FINANCIAL_SECTORS
    is_util = sector in UTILITY_SECTORS
    checks: dict[str, bool | None] = {}

    profits = [_num(v) for v in (fs.net_profit, fs.net_profit_1y_ago, fs.net_profit_2y_ago, fs.net_profit_3y_ago)]
    checks["G1_profit_4y"] = None if any(p is None for p in profits) else all(p > 0 for p in profits)

    dps = _num(fs.dividend_per_share)
    checks["G2_dividend"] = None if dps is None else dps > 0

    now, old = _num(fs.net_profit), _num(fs.net_profit_3y_ago)
    checks["G3_growth"] = None if now is None or old is None else (now > old and old > 0)

    cr = _num(fs.current_ratio())
    checks["G4_current_ratio"] = None if (is_fin or is_util or cr is None) else cr >= min_current_ratio

    dr = _num(fs.debt_ratio())
    checks["G5_debt_ratio"] = None if (is_fin or dr is None) else dr <= max_debt_ratio

    pe_v, pb_v = _num(pe), _num(pb)
    if pe_v is None or pb_v is None:
        checks["G6_pe_x_pb"] = None
    else:
        checks["G6_pe_x_pb"] = pe_v > 0 and pb_v > 0 and pe_v * pb_v <= max_pe_x_pb

    mc = _num(market_cap_yi)
    checks["G7_size"] = None if mc is None else mc >= min_market_cap_yi

    shares = _num(fs.share_count)
    eps = (now / shares) if (now is not None and shares) else None
    ta, tl = _num(fs.total_assets), _num(fs.total_liabilities)
    bvps = ((ta - tl) / shares) if (ta is not None and tl is not None and shares) else None
    gn = mos = None
    if eps is not None and bvps is not None and eps > 0 and bvps > 0:
        gn = round(math.sqrt(22.5 * eps * bvps), 4)
        p = _num(price)
        if p and p > 0:
            mos = round(gn / p - 1, 4)
    return GrahamResult(checks=checks, graham_number=gn, margin_of_safety=mos)


# ---------------------------------------------------------------------------
# 2. Sloan 应计比率
# ---------------------------------------------------------------------------

def sloan_accrual_ratio(fs: "FinancialStatements") -> float | None:
    """(净利润 - 经营现金流) / 平均总资产。越低越好；金融行业不适用，返回 None。"""
    if (fs.sector or "") in FINANCIAL_SECTORS:
        return None
    ni, cfo, ta = _num(fs.net_profit), _num(fs.operating_cf), _num(fs.total_assets)
    if ni is None or cfo is None or ta is None:
        return None
    ta0 = _num(fs.total_assets_1y_ago)
    avg_assets = (ta + ta0) / 2 if ta0 is not None else ta
    return None if avg_assets <= 0 else round((ni - cfo) / avg_assets, 4)


def sloan_accrual_ratio_3y(fs: "FinancialStatements") -> float | None:
    """三年累计应计 / 平均总资产，平滑单年波动（如一次性收款）。"""
    nis = [_num(v) for v in (fs.net_profit, fs.net_profit_1y_ago, fs.net_profit_2y_ago)]
    cfs = [_num(v) for v in (fs.operating_cf, fs.operating_cf_1y_ago, fs.operating_cf_2y_ago)]
    ta, ta0 = _num(fs.total_assets), _num(fs.total_assets_1y_ago)
    if (fs.sector or "") in FINANCIAL_SECTORS or any(v is None for v in nis + cfs) or ta is None:
        return None
    avg_assets = (ta + ta0) / 2 if ta0 is not None else ta
    return None if avg_assets <= 0 else round((sum(nis) - sum(cfs)) / (3 * avg_assets), 4)


def accrual_score(
    fs: "FinancialStatements",
    *,
    good: float = -0.05,
    bad: float = 0.10,
) -> tuple[float | None, dict[str, float | None]]:
    """应计质量分（0–100，越高越好）。好/坏阈值先用经验值，建议后续改成行业内分位。"""
    r1, r3 = sloan_accrual_ratio(fs), sloan_accrual_ratio_3y(fs)
    s1 = scale_linear(r1, good, bad, inverse=True)
    s3 = scale_linear(r3, good, bad, inverse=True)
    score, _ = weighted_mean_available([(s1, 0.4), (s3, 0.6)])
    return score, {"sloan_1y": r1, "sloan_3y": r3}


# ---------------------------------------------------------------------------
# 3. 估值分位修复 + 股息率利差
# ---------------------------------------------------------------------------

def valuation_percentile_score(
    current: float | None,
    history: Iterable[float | None],
    *,
    min_n: int = 250,
) -> tuple[float | None, float | None]:
    """返回 (历史分位, 便宜度得分)。亏损（≤0）或历史不足返回 (None, None)。

    便宜度 = 100 - 分位。注意：分位恰好为 0（比全部历史都低）时得分是 100，不是中性 50。
    """
    x = _num(current)
    if x is None or x <= 0:
        return None, None
    pct = percentile_rank(
        x,
        [h for h in (_num(v) for v in history) if h is not None and h > 0],
        min_n=min_n,
    )
    return pct, (None if pct is None else round(100 - pct, 2))


def align_forward_fill(
    primary: Mapping[str, float],
    secondary: Mapping[str, float],
) -> list[tuple[str, float, float]]:
    """按日期（ISO 字符串）对齐：对 primary 每个日期取 secondary 中不晚于它的最近值。"""
    sec_dates = sorted(secondary)
    out: list[tuple[str, float, float]] = []
    for d in sorted(primary):
        i = bisect_right(sec_dates, d) - 1
        a, b = _num(primary[d]), (_num(secondary[sec_dates[i]]) if i >= 0 else None)
        if a is not None and b is not None:
            out.append((d, a, b))
    return out


def dividend_yield_history_from_prices(
    dps: float | None,
    price_history: Mapping[str, float],
) -> dict[str, float]:
    """用“最新每股股利 / 历史价格”近似历史股息率（%）。

    只反映价格变化、不含历史分红变化，是接口拿不到历史股息率时的退而求其次；
    若数据源直接提供 TTM 股息率序列（如 akshare 估值指标里的 dv_ttm，需自行核对），优先用它。
    """
    d = _num(dps)
    if d is None or d <= 0:
        return {}
    return {k: round(d / p * 100, 4) for k, p in price_history.items() if (_num(p) or 0) > 0}


def yield_spread_stats(
    div_yield_now: float | None,
    bond_yield_now: float | None,
    div_yield_hist: Mapping[str, float],
    bond_yield_hist: Mapping[str, float],
    *,
    min_n: int = 250,
) -> dict[str, float | None]:
    """股息率 - 10 年期国债收益率（百分点）及其历史分位。分位越高，相对债券越便宜。"""
    dy, by = _num(div_yield_now), _num(bond_yield_now)
    spread = None if dy is None or by is None else round(dy - by, 4)
    hist = [a - b for _, a, b in align_forward_fill(div_yield_hist, bond_yield_hist)]
    pct = percentile_rank(spread, hist, min_n=min_n)
    return {"spread": spread, "spread_pct": pct, "spread_score": pct}


def rate_beta(stock_returns: Sequence[float], yield_changes: Sequence[float], *, min_n: int = 60) -> float | None:
    """个股日收益对国债收益率日变动（同单位）的 OLS 斜率。显著为负 = 对利率上行敏感（债性强）。"""
    n = min(len(stock_returns), len(yield_changes))
    if n < min_n:
        return None
    ys, xs = list(stock_returns)[-n:], list(yield_changes)[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var, 4)


def fetch_cn10y_history() -> dict[str, float]:
    """抓取中国 10 年期国债收益率日序列 {YYYY-MM-DD: 百分数}，失败返回 {}。

    未联网验证：用 akshare.bond_zh_us_rate，并按列名里同时含“中国”和“10年”（且不含“-”）找列。
    接口名/列名随 akshare 版本变动，请在本机确认后再用；调用方应缓存结果并在为空时降级。
    """
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.bond_zh_us_rate(start_date="20150101")
        col = next((c for c in df.columns if "中国" in str(c) and "10年" in str(c) and "-" not in str(c)), None)
        date_col = next((c for c in df.columns if "日期" in str(c)), None)
        if df is None or df.empty or col is None or date_col is None:
            return {}
        out: dict[str, float] = {}
        for d, v in zip(df[date_col].tolist(), df[col].tolist()):
            fv = _num(v)
            if fv is not None:
                out[str(d)[:10]] = fv
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 4. 横截面标准化（行业内分位，样本不足回退全市场）+ 因子快照
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactorSpec:
    name: str
    weight: float
    higher_is_better: bool = True


@dataclass
class CrossSectionScorer:
    """把原始因子值转成 0–100 的行业内分位分。

    - fit() 用“参考全样本”建分布（建议每天收盘后用全 A 股拟合并落盘）；
    - score_row() 对单只股票打分，所以盘中只刷新少数候选股时分布依然稳定；
    - 行业样本数 < min_group 时回退全市场分布，避免 3 只股票的行业里排名失真。
    """

    specs: list[FactorSpec]
    min_group: int = 8
    market: dict[str, list[float]] = field(default_factory=dict)
    by_sector: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def fit(self, rows: Mapping[str, Mapping[str, float | None]], sectors: Mapping[str, str]) -> "CrossSectionScorer":
        self.market, self.by_sector = {}, {}
        for ticker, row in rows.items():
            sector = sectors.get(ticker, "其他")
            for spec in self.specs:
                v = _num(row.get(spec.name))
                if v is None:
                    continue
                self.market.setdefault(spec.name, []).append(v)
                self.by_sector.setdefault(sector, {}).setdefault(spec.name, []).append(v)
        return self

    def _reference(self, factor: str, sector: str) -> list[float]:
        group = self.by_sector.get(sector, {}).get(factor, [])
        return group if len(group) >= self.min_group else self.market.get(factor, [])

    def score_factor(self, factor: str, value: float | None, sector: str) -> float | None:
        spec = next((s for s in self.specs if s.name == factor), None)
        pct = percentile_rank(value, self._reference(factor, sector), min_n=self.min_group)
        if spec is None or pct is None:
            return None
        return pct if spec.higher_is_better else round(100 - pct, 2)

    def score_row(
        self,
        row: Mapping[str, float | None],
        sector: str,
    ) -> tuple[float | None, dict[str, float | None], float]:
        per = {s.name: self.score_factor(s.name, row.get(s.name), sector) for s in self.specs}
        total, coverage = weighted_mean_available([(per[s.name], s.weight) for s in self.specs])
        return total, per, coverage

    def dump(self) -> dict[str, Any]:
        return {
            "min_group": self.min_group,
            "market": self.market,
            "by_sector": self.by_sector,
            "specs": [vars(s) for s in self.specs],
        }

    @classmethod
    def load(cls, payload: Mapping[str, Any]) -> "CrossSectionScorer":
        return cls(
            specs=[FactorSpec(**s) for s in payload["specs"]],
            min_group=int(payload.get("min_group", 8)),
            market={k: list(v) for k, v in payload.get("market", {}).items()},
            by_sector={
                sector: {k: list(v) for k, v in data.items()}
                for sector, data in payload.get("by_sector", {}).items()
            },
        )


def append_factor_snapshots(path: Path, rows: Iterable[Mapping[str, Any]], asof: str) -> int:
    """追加写入因子快照（JSONL），同一 (asof, ticker) 只保留第一条，返回新增条数。

    这是“采集时点”快照，不是严格的 point-in-time：接口给的财报可能含重述，且没有公告日。
    建议每行带上 report_date，后续用它近似“当时已披露”的口径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("asof") == asof:
                    seen.add(str(rec.get("ticker")))
    added = 0
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            ticker = str(row.get("ticker"))
            if ticker in seen:
                continue
            f.write(json.dumps({"asof": asof, **row}, ensure_ascii=False) + "\n")
            seen.add(ticker)
            added += 1
    return added


def load_factor_snapshots(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(vx * vy), 4)


def rank_ic(
    scores: Mapping[str, float],
    forward_returns: Mapping[str, float],
    *,
    min_n: int = 20,
) -> float | None:
    """某一日因子得分与未来 N 日收益的秩相关（IC）。样本不足返回 None。"""
    common = [
        ticker
        for ticker in scores
        if ticker in forward_returns and _num(scores[ticker]) is not None and _num(forward_returns[ticker]) is not None
    ]
    if len(common) < min_n:
        return None
    return spearman([scores[t] for t in common], [forward_returns[t] for t in common])