"""Industry & cross-market context for stock/fund recommendations.

Provides sector classification, related US/global benchmarks,
and SWOT-style analytical framework output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from invest_app.market_data import _classify_cn_code, get_history, get_quote, normalize_ticker

# ---------------------------------------------------------------------------
# Sector / industry mapping
# ---------------------------------------------------------------------------

# Map A-share codes or name keywords → sector tag
_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "科技": ["科技", "信息", "软件", "互联", "通信", "电子", "计算机", "数字", "云"],
    "芯片/半导体": ["芯片", "半导体", "集成电路", "IC", "晶圆", "封测", "光刻"],
    "AI/人工智能": ["AI", "人工智能", "机器学习", "大模型", "智能", "算力", "GPU"],
    "电力/能源": ["电力", "能源", "电网", "核电", "光伏", "风电", "水电", "储能", "新能源"],
    "有色金属": ["有色", "铜", "铝", "锂", "钴", "镍", "稀土", "黄金", "白银", "矿"],
    "消费": ["消费", "白酒", "食品", "饮料", "零售", "家电", "医药", "茅台"],
    "金融": ["银行", "保险", "券商", "证券", "金融"],
    "地产": ["地产", "房产", "物业"],
}

# Related US/global benchmark tickers per sector
_SECTOR_BENCHMARKS: dict[str, list[tuple[str, str]]] = {
    "科技": [("QQQ", "纳斯达克100 ETF"), ("XLK", "美国科技板块")],
    "芯片/半导体": [("SOXX", "美半导体ETF"), ("TSM", "台积电"), ("NVDA", "英伟达")],
    "AI/人工智能": [("NVDA", "英伟达"), ("MSFT", "微软"), ("GOOGL", "谷歌")],
    "电力/能源": [("XLE", "美能源ETF"), ("ICLN", "全球清洁能源ETF")],
    "有色金属": [("GLD", "黄金ETF"), ("COPX", "铜矿ETF"), ("LIT", "锂电池ETF")],
    "消费": [("XLP", "美必需消费ETF"), ("XLY", "美可选消费ETF")],
    "金融": [("XLF", "美金融ETF")],
    "地产": [("XLRE", "美房地产ETF")],
}

# Focus sectors the user cares about most
FOCUS_SECTORS = {"科技", "芯片/半导体", "AI/人工智能", "电力/能源", "有色金属"}


def classify_sector(ticker: str, name: str = "") -> str:
    """Classify a ticker into a sector based on name keywords."""
    combined = f"{name} {ticker}".upper()
    for sector, keywords in _SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw.upper() in combined:
                return sector
    return "其他"


def get_sector_benchmarks(sector: str) -> list[tuple[str, str]]:
    """Return list of (ticker, label) for related US/global benchmarks."""
    return _SECTOR_BENCHMARKS.get(sector, [])


# ---------------------------------------------------------------------------
# Cross-market snapshot
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSnapshot:
    ticker: str
    label: str
    price: float | None = None
    change_pct: float | None = None
    trend: str = "N/A"  # 偏强 / 中性 / 偏弱


def fetch_benchmark_snapshots(benchmarks: list[tuple[str, str]]) -> list[BenchmarkSnapshot]:
    """Best-effort fetch of US/global benchmark quotes for context.

    Each ticker is individually wrapped so one failure never blocks the rest.
    Total wall-clock time is capped to ~20 s (each get_quote already has an 8 s
    hard timeout, so at most 2-3 tickers will be tried before giving up).
    """
    results: list[BenchmarkSnapshot] = []
    for ticker, label in benchmarks[:3]:  # cap to 3 to limit total wait
        try:
            q = get_quote(ticker)
            trend = "偏强" if q.change_pct > 0.3 else ("偏弱" if q.change_pct < -0.3 else "中性")
            results.append(BenchmarkSnapshot(
                ticker=ticker, label=label,
                price=q.price, change_pct=q.change_pct, trend=trend,
            ))
        except Exception:
            # Silently skip – benchmark data is supplementary
            results.append(BenchmarkSnapshot(ticker=ticker, label=label))
    return results


# ---------------------------------------------------------------------------
# SWOT-style analytical framework
# ---------------------------------------------------------------------------

@dataclass
class SWOTAnalysis:
    """Structured SWOT analysis for one instrument."""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)


def build_swot(
    *,
    current_price: float,
    ma20: float | None,
    ma60: float | None,
    high_6m: float | None,
    low_6m: float | None,
    volume_ratio: float | None,
    pe_ratio: float | None,
    profit_margin: float | None,
    sector: str,
    is_fund: bool,
    benchmarks: list[BenchmarkSnapshot] | None = None,
) -> SWOTAnalysis:
    """Generate SWOT analysis from quantitative signals."""
    s: list[str] = []
    w: list[str] = []
    o: list[str] = []
    t: list[str] = []

    # --- Strengths ---
    if ma20 and ma60 and current_price > ma20 > ma60:
        s.append("价格位于20/60日均线上方，短中期趋势健康")
    if volume_ratio and volume_ratio >= 1.2:
        s.append(f"成交量放大（量比{volume_ratio:.1f}x），资金关注度高")
    if profit_margin and profit_margin > 0.15:
        s.append(f"利润率较高（{profit_margin*100:.1f}%），盈利能力强")
    if pe_ratio and pe_ratio < 20:
        s.append(f"估值偏低（PE {pe_ratio:.1f}），安全边际充足")
    if sector in FOCUS_SECTORS:
        s.append(f"属于重点关注行业「{sector}」，政策与产业趋势支撑")
    if is_fund:
        s.append("指数/基金产品分散了个股风险")

    # --- Weaknesses ---
    if ma20 and ma60 and current_price < ma20 < ma60:
        w.append("均线空头排列，短期走势偏弱")
    if volume_ratio and volume_ratio < 0.7:
        w.append("成交低迷，流动性风险偏高")
    if profit_margin is not None and profit_margin < 0:
        w.append(f"公司亏损（利润率{profit_margin*100:.1f}%）")
    if pe_ratio and pe_ratio > 60:
        w.append(f"估值偏高（PE {pe_ratio:.1f}），回调风险大")
    if is_fund and low_6m and high_6m:
        rng = (high_6m - low_6m) / low_6m * 100
        if rng < 8:
            w.append(f"近6个月波动仅{rng:.1f}%，趋势性机会有限")

    # --- Opportunities ---
    if low_6m and current_price < low_6m * 1.05:
        o.append("当前价格接近6个月低点，有望获得低位筹码")
    if benchmarks:
        strong = [b for b in benchmarks if b.trend == "偏强"]
        if strong:
            o.append(f"海外关联标的偏强（{', '.join(b.label for b in strong[:2])}），外围情绪正面")
    if sector in FOCUS_SECTORS:
        o.append(f"「{sector}」行业处于长期成长赛道，可关注波段机会")

    # --- Threats ---
    if high_6m and current_price > high_6m * 0.95:
        t.append("接近6个月高点，短期获利盘压力大")
    if benchmarks:
        weak = [b for b in benchmarks if b.trend == "偏弱"]
        if weak:
            t.append(f"海外关联标的走弱（{', '.join(b.label for b in weak[:2])}），注意联动风险")
    if pe_ratio and pe_ratio > 40:
        t.append("高估值环境下，业绩不及预期可能引发估值杀")

    # Ensure at least one item per category
    if not s:
        s.append("暂无明显优势信号")
    if not w:
        w.append("暂无明显劣势信号")
    if not o:
        o.append("暂无明显机会信号")
    if not t:
        t.append("暂无明显威胁信号")

    return SWOTAnalysis(strengths=s, weaknesses=w, opportunities=o, threats=t)


# ---------------------------------------------------------------------------
# Gartner Hype-Cycle-inspired maturity assessment (simplified)
# ---------------------------------------------------------------------------

_MATURITY_STAGES = [
    "技术萌芽期",
    "期望膨胀期",
    "泡沫破裂期",
    "稳步爬升期",
    "成熟应用期",
]


def estimate_sector_maturity(sector: str) -> str:
    """Rough heuristic for where the sector is on the hype/maturity cycle.

    In a real system this would be data-driven; here we use reasonable
    defaults for the focus sectors as of 2025-2026.
    """
    mapping: dict[str, str] = {
        "AI/人工智能": "期望膨胀期",
        "芯片/半导体": "稳步爬升期",
        "科技": "稳步爬升期",
        "电力/能源": "稳步爬升期",
        "有色金属": "成熟应用期",
        "消费": "成熟应用期",
        "金融": "成熟应用期",
        "地产": "泡沫破裂期",
    }
    return mapping.get(sector, "成熟应用期")
