"""Three quantitative strategies for A-share market.

Strategy 1 — MA Bullish Alignment + Volume Surge (Trend Following)
  MA5 > MA10 > MA20, MA5 slope positive, volume_ratio > 1.5
  Buy signal on pullback to MA5 without breaking it.

Strategy 2 — RSI Oversold + Bias Mean-Reversion (Contrarian Rebound)
  RSI(14) < 25 AND Bias(20) < -10%  →  rebound entry
  Exit when RSI > 55 OR Bias > 0%

Strategy 3 — Macro + Industry Resonance (Multi-Factor Score)
  Factors:
    - Technical alignment score (MA + volume)
    - RSI / Bias sentiment score
    - Industry heat (sector momentum via hot-sector list from Eastmoney)
    - Northbound flow proxy (broad market sentiment via ETF change)
    - Valuation filter (PE < 50 or fund)
  Combined score 0-100; ≥ 60 → "综合买入信号"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.request import Request, urlopen
import json

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StrategySignal:
    """Result of a single strategy evaluation."""
    name: str
    signal: str          # "买入" | "卖出" | "观望" | "反弹机会"
    score: float         # 0-100
    details: list[str] = field(default_factory=list)
    indicators: dict[str, float | str | None] = field(default_factory=dict)


@dataclass
class MultiStrategyReport:
    """Aggregated report from all three strategies."""
    ticker: str
    name: str
    strategy1: StrategySignal
    strategy2: StrategySignal
    strategy3: StrategySignal
    combined_score: float        # weighted average 0-100
    composite_action: str        # final combined recommendation
    hot_sectors: list[str] = field(default_factory=list)
    in_hot_sector: bool = False
    sector: str = ""
    northbound_sentiment: str = "中性"  # 偏强 / 中性 / 偏弱
    position_pct: int = 0          # suggested position % of total capital
    position_note: str = ""        # human-readable position sizing advice


# ---------------------------------------------------------------------------
# RSI helper
# ---------------------------------------------------------------------------

def _calc_rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder RSI."""
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    last = rsi.iloc[-1]
    return None if pd.isna(last) else float(last)


def _calc_bias(close: pd.Series, period: int = 20) -> float | None:
    """Bias(20) = (close - MA20) / MA20 * 100%."""
    if len(close) < period:
        return None
    ma = close.tail(period).mean()
    if ma == 0:
        return None
    return float((close.iloc[-1] - ma) / ma * 100)


def _ma_slope(close: pd.Series, window: int = 5) -> float | None:
    """Slope of the last `window` closes (linear regression slope normalized by mean)."""
    if len(close) < window:
        return None
    y = close.tail(window).values.astype(float)
    x = np.arange(len(y))
    slope = float(np.polyfit(x, y, 1)[0])
    mean_price = float(y.mean())
    return (slope / mean_price * 100) if mean_price != 0 else 0.0


# ---------------------------------------------------------------------------
# Strategy 1: MA Bullish Alignment + Volume Surge
# ---------------------------------------------------------------------------

def strategy_ma_trend(
    close: pd.Series,
    volume: pd.Series | None,
    current_price: float,
) -> StrategySignal:
    """MA5 > MA10 > MA20 + volume ratio > 1.5 trend-following strategy."""
    details: list[str] = []
    indicators: dict = {}
    score = 50.0

    def _ma(n: int) -> float | None:
        if len(close) < n:
            return None
        v = float(close.tail(n).mean())
        return v if not pd.isna(v) else None

    ma5 = _ma(5)
    ma10 = _ma(10)
    ma20 = _ma(20)
    ma60 = _ma(60)
    slope5 = _ma_slope(close, 5)

    indicators.update({"MA5": ma5, "MA10": ma10, "MA20": ma20, "MA60": ma60, "MA5斜率%": slope5})

    # Volume ratio vs 20-day average
    vol_ratio: float | None = None
    if volume is not None and len(volume.dropna()) >= 20:
        v = volume.dropna()
        avg = float(v.tail(20).mean())
        last = float(v.iloc[-1])
        if avg > 0:
            vol_ratio = last / avg
    indicators["量比"] = vol_ratio

    # ── Scoring ──────────────────────────────────────────────────────
    bullish_align = (
        ma5 is not None and ma10 is not None and ma20 is not None
        and ma5 > ma10 > ma20
    )
    slope_pos = slope5 is not None and slope5 > 0
    volume_surge = vol_ratio is not None and vol_ratio >= 1.5

    if bullish_align:
        score += 20
        details.append(f"✅ 均线多头排列：MA5({ma5:.2f}) > MA10({ma10:.2f}) > MA20({ma20:.2f})")
    else:
        score -= 15
        if ma5 and ma10 and ma20:
            details.append(f"⚠️ 均线未多头排列：MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}")
        else:
            details.append("⚠️ 数据不足，无法判断均线排列")

    if slope_pos:
        score += 10
        details.append(f"✅ MA5 斜率为正（{slope5:.2f}%），短期上行动能存在")
    else:
        score -= 5
        if slope5 is not None:
            details.append(f"⚠️ MA5 斜率为负（{slope5:.2f}%），短期动能不足")

    if volume_surge:
        score += 20
        details.append(f"✅ 成交量放大（量比 {vol_ratio:.2f}x > 1.5），资金积极介入")
    elif vol_ratio is not None:
        if vol_ratio < 0.7:
            score -= 10
            details.append(f"⚠️ 成交低迷（量比 {vol_ratio:.2f}x），缺乏资金支撑")
        else:
            details.append(f"ℹ️ 量比 {vol_ratio:.2f}x，量能平稳")
    else:
        details.append("ℹ️ 成交量数据不足")

    # Pullback to MA5 opportunity
    if bullish_align and ma5 and abs(current_price - ma5) / ma5 < 0.02:
        score += 10
        details.append(f"✅ 当前价接近 MA5（±2%），回踩不破时为较好买点")

    # MA60 long-term alignment bonus
    if ma60 and ma20 and ma20 > ma60:
        score += 5
        details.append(f"✅ MA20({ma20:.2f}) > MA60({ma60:.2f})，中长期趋势向好")

    # Bias override: even in an uptrend, warn against chasing when price is too stretched
    bias_val = _calc_bias(close, 20)
    indicators["乖离率Bias(20)%"] = bias_val
    if bullish_align and bias_val is not None and bias_val > 15:
        score -= 15
        details.append(
            f"⚠️ 乖离率严重偏高（Bias={bias_val:.1f}% > 15%）：虽均线多头排列，"
            f"但价格已严重偏离均线，追高风险极大。"
            + (f"建议等待回踩 MA5（¥{ma5:.2f}）后再分批买入。" if ma5 else "建议等待回踩 MA5 后再买入。")
        )
    elif bullish_align and bias_val is not None and bias_val > 10:
        score -= 8
        details.append(
            f"⚠️ 乖离率偏高（Bias={bias_val:.1f}% > 10%）：趋势多头但价格位置偏高，"
            + (f"建议等待回踩 MA5（¥{ma5:.2f}）附近后分批介入，不宜追高。" if ma5 else "不宜追高。")
        )

    score = max(0.0, min(100.0, score))

    if score >= 70 and bullish_align and volume_surge:
        signal = "买入"
    elif score >= 55:
        signal = "观望偏多"
    elif score < 35:
        signal = "观望"
    else:
        signal = "观望"

    return StrategySignal(name="均线多头+量比策略", signal=signal, score=round(score, 1),
                          details=details, indicators=indicators)


# ---------------------------------------------------------------------------
# Strategy 2: RSI Oversold + Bias Mean-Reversion
# ---------------------------------------------------------------------------

def strategy_rsi_bias(
    close: pd.Series,
    current_price: float,
) -> StrategySignal:
    """RSI < 25 AND Bias(20) < -10% → contrarian rebound entry."""
    details: list[str] = []
    indicators: dict = {}
    score = 50.0

    rsi = _calc_rsi(close, 14)
    bias = _calc_bias(close, 20)
    rsi_exit = _calc_rsi(close, 14)  # same value; named for clarity in context

    indicators.update({"RSI(14)": rsi, "乖离率Bias(20)%": bias})

    if rsi is None:
        details.append("⚠️ RSI 数据不足（需至少 15 个交易日）")
    elif rsi < 20:
        score += 30
        details.append(f"✅ RSI 极度超卖（{rsi:.1f} < 20），情绪达极端低点，反弹概率大")
    elif rsi < 25:
        score += 20
        details.append(f"✅ RSI 超卖（{rsi:.1f} < 25），短期可能触底")
    elif rsi < 35:
        score += 10
        details.append(f"ℹ️ RSI 偏低（{rsi:.1f}），有一定超卖迹象")
    elif rsi > 70:
        score -= 20
        details.append(f"⚠️ RSI 超买（{rsi:.1f} > 70），追高风险大")
    elif rsi > 55:
        score -= 10
        details.append(f"ℹ️ RSI 中性偏高（{rsi:.1f}），非抄底时机")
    else:
        details.append(f"ℹ️ RSI 中性（{rsi:.1f}）")

    if bias is None:
        details.append("⚠️ 乖离率数据不足")
    elif bias < -15:
        score += 30
        details.append(f"✅ 乖离率严重偏离（Bias={bias:.1f}%，< -15%），均值回归动能强")
    elif bias < -10:
        score += 20
        details.append(f"✅ 乖离率偏离（Bias={bias:.1f}%，< -10%），向上修正概率大")
    elif bias < -5:
        score += 10
        details.append(f"ℹ️ 乖离率偏低（Bias={bias:.1f}%），有一定回归空间")
    elif bias > 10:
        score -= 15
        details.append(f"⚠️ 乖离率过高（Bias={bias:.1f}%），存在均值回调压力")
    else:
        details.append(f"ℹ️ 乖离率正常（Bias={bias:.1f}%）")

    # Combined RSI + Bias double-confirmation bonus
    if rsi is not None and bias is not None and rsi < 25 and bias < -10:
        score += 10
        details.append("✅ RSI 超卖 + 乖离率双重确认，A股情绪博弈反弹信号较强")

    # Exit hints
    if rsi is not None and rsi > 55:
        details.append("  → 出场参考：RSI 突破 55 或乖离率回正时可分批减仓")

    score = max(0.0, min(100.0, score))

    if score >= 72:
        signal = "反弹机会"
    elif score >= 58:
        signal = "观望偏多"
    elif score < 35:
        signal = "观望"
    else:
        signal = "观望"

    return StrategySignal(name="RSI超卖+乖离率策略", signal=signal, score=round(score, 1),
                          details=details, indicators=indicators)


# ---------------------------------------------------------------------------
# Hot sector fetcher (Eastmoney sector ranking — best-effort)
# ---------------------------------------------------------------------------

# Curated list of focus sectors (updated 2026 Q1 judgment)
_FOCUS_SECTORS_CN = {
    "半导体", "芯片", "AI", "人工智能", "算力", "新能源", "光伏", "储能",
    "军工", "国防", "创新药", "医疗器械", "机器人", "自动驾驶", "卫星",
    "消费电子", "云计算", "大模型", "信创", "北斗",
}

# Map strategy sector tags to focus list keywords
_SECTOR_TAG_TO_FOCUS: dict[str, list[str]] = {
    "芯片/半导体": ["半导体", "芯片"],
    "AI/人工智能": ["AI", "人工智能", "算力", "大模型"],
    "电力/能源": ["新能源", "光伏", "储能"],
    "科技": ["信创", "云计算", "消费电子"],
}


def fetch_hot_sectors(limit: int = 15) -> list[str]:
    """Best-effort fetch of today's top-performing A-share sectors from Eastmoney.

    Returns a list of sector names (strings). Falls back to curated list on failure.
    """
    url = (
        "http://push2.eastmoney.com/api/qt/clist/get"
        "?pn=1&pz=50&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        "&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
        "&fields=f2,f3,f4,f12,f14"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        items = data.get("data", {}).get("diff", []) or []
        # f14 = sector name, f3 = change_pct
        sectors = [
            item.get("f14", "") for item in items
            if isinstance(item.get("f3"), (int, float)) and item.get("f3", 0) > 0
        ]
        return [s for s in sectors if s][:limit]
    except Exception:
        return []


def is_in_hot_sector(sector_tag: str, hot_sectors: list[str]) -> bool:
    """Check if a sector tag matches any hot sector."""
    keywords = _SECTOR_TAG_TO_FOCUS.get(sector_tag, [sector_tag])
    hot_lower = " ".join(hot_sectors).lower()
    return any(k.lower() in hot_lower for k in keywords)


# ---------------------------------------------------------------------------
# Northbound proxy (use CSI300 ETF 510300 change as broad proxy)
# ---------------------------------------------------------------------------

def fetch_northbound_sentiment() -> str:
    """Proxy northbound fund sentiment via CSI300 ETF (510300) intraday change.

    Returns: "偏强" | "中性" | "偏弱"
    """
    try:
        from invest_app.market_data import _try_cn_quote_tencent
        q = _try_cn_quote_tencent("510300")
        if q is None:
            return "中性"
        if q.change_pct > 0.5:
            return "偏强"
        if q.change_pct < -0.5:
            return "偏弱"
        return "中性"
    except Exception:
        return "中性"


# ---------------------------------------------------------------------------
# Strategy 3: Macro + Industry Resonance Multi-Factor
# ---------------------------------------------------------------------------

def strategy_macro_industry(
    close: pd.Series,
    current_price: float,
    sector: str,
    pe_ratio: float | None,
    profit_margin: float | None,
    is_fund: bool,
    hot_sectors: list[str],
    northbound_sentiment: str = "中性",
) -> StrategySignal:
    """Multi-factor macro + industry resonance score (0-100).

    Factors (with weights):
      1. Technical alignment (MA + momentum)    — 25 pts
      2. RSI / Bias sentiment                   — 15 pts
      3. Industry heat (hot-sector match)       — 25 pts
      4. Northbound sentiment proxy             — 15 pts
      5. Valuation filter (PE, profit margin)   — 20 pts
    """
    details: list[str] = []
    indicators: dict = {}
    score = 0.0

    # ── Factor 1: Technical alignment (max 25) ────────────────────────
    def _ma_val(n: int) -> float | None:
        if len(close) < n:
            return None
        v = float(close.tail(n).mean())
        return None if pd.isna(v) else v

    ma5, ma10, ma20 = _ma_val(5), _ma_val(10), _ma_val(20)
    slope5 = _ma_slope(close, 5)
    tech_score = 0.0

    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            tech_score += 15
            details.append("✅ [技术] 均线多头排列，趋势向上")
        elif ma5 < ma10 < ma20:
            tech_score -= 5
            details.append("⚠️ [技术] 均线空头排列，趋势偏弱")
        else:
            tech_score += 5
            details.append("ℹ️ [技术] 均线结构中性")
    if slope5 is not None and slope5 > 0.05:
        tech_score += 10
        details.append(f"✅ [技术] MA5 斜率正向（{slope5:.2f}%），短期动能充足")
    score += min(25.0, max(0.0, tech_score))
    indicators.update({"MA5": ma5, "MA10": ma10, "MA20": ma20})

    # ── Factor 2: RSI + Bias sentiment (max 15) ───────────────────────
    rsi = _calc_rsi(close, 14)
    bias = _calc_bias(close, 20)
    sent_score = 8.0  # neutral baseline
    if rsi is not None:
        if rsi < 25:
            sent_score += 7
            details.append(f"✅ [情绪] RSI 超卖（{rsi:.1f}），情绪见底信号")
        elif rsi > 70:
            sent_score -= 6
            details.append(f"⚠️ [情绪] RSI 超买（{rsi:.1f}），回调风险高")
        indicators["RSI(14)"] = rsi
    if bias is not None:
        if bias < -10:
            sent_score += 4
            details.append(f"✅ [情绪] 乖离率低位（Bias={bias:.1f}%），均值回归空间大")
        elif bias > 10:
            sent_score -= 3
            details.append(f"⚠️ [情绪] 乖离率偏高（Bias={bias:.1f}%），注意回调")
        indicators["Bias(20)%"] = bias
    score += min(15.0, max(0.0, sent_score))

    # ── Factor 3: Industry heat (max 25) ─────────────────────────────
    in_hot = is_in_hot_sector(sector, hot_sectors)
    indicators["热门板块匹配"] = "是" if in_hot else "否"
    if in_hot:
        score += 25
        matched = [h for h in hot_sectors if any(
            k.lower() in h.lower()
            for k in _SECTOR_TAG_TO_FOCUS.get(sector, [sector])
        )]
        details.append(
            f"✅ [行业] 所属行业「{sector}」命中热门板块"
            + (f"：{', '.join(matched[:3])}" if matched else "")
        )
    elif sector in {"芯片/半导体", "AI/人工智能", "电力/能源", "科技"}:
        score += 12
        details.append(f"ℹ️ [行业] 「{sector}」属于中长期重点赛道，今日未列热门但具战略价值")
    else:
        score += 5
        details.append(f"ℹ️ [行业] 「{sector}」今日非热门板块，景气度一般")
    indicators["今日热门板块数"] = len(hot_sectors)

    # ── Factor 4: Northbound sentiment (max 15) ───────────────────────
    nb_map = {"偏强": 15.0, "中性": 8.0, "偏弱": 2.0}
    nb_score = nb_map.get(northbound_sentiment, 8.0)
    score += nb_score
    indicators["北向资金情绪代理"] = northbound_sentiment
    if northbound_sentiment == "偏强":
        details.append("✅ [宏观] 北向资金情绪代理偏强（沪深300 ETF 涨幅 > 0.5%），外资风险偏好提升")
    elif northbound_sentiment == "偏弱":
        details.append("⚠️ [宏观] 北向资金情绪代理偏弱（沪深300 ETF 跌幅 > 0.5%），外资避险情绪上升")
    else:
        details.append("ℹ️ [宏观] 北向资金情绪中性，市场无明显方向性驱动")

    # ── Factor 5: Valuation (max 20) ─────────────────────────────────
    val_score = 10.0  # neutral baseline
    if is_fund:
        val_score = 14.0
        details.append("✅ [估值] 指数/基金产品，无 PE 约束，分散了个股估值风险")
    else:
        if pe_ratio is not None:
            indicators["PE"] = pe_ratio
            if pe_ratio < 15:
                val_score += 10
                details.append(f"✅ [估值] PE 低估（{pe_ratio:.1f} < 15），安全边际充足")
            elif pe_ratio < 30:
                val_score += 5
                details.append(f"ℹ️ [估值] PE 合理（{pe_ratio:.1f}）")
            elif pe_ratio < 50:
                val_score -= 3
                details.append(f"⚠️ [估值] PE 偏高（{pe_ratio:.1f}），需业绩支撑")
            else:
                val_score -= 8
                details.append(f"⚠️ [估值] PE 过高（{pe_ratio:.1f} > 50），估值泡沫风险")
        if profit_margin is not None:
            indicators["利润率%"] = profit_margin * 100
            if profit_margin > 0.15:
                val_score += 5
                details.append(f"✅ [基本面] 利润率良好（{profit_margin*100:.1f}%），盈利能力强")
            elif profit_margin < 0:
                val_score -= 5
                details.append(f"⚠️ [基本面] 公司亏损，业绩风险较大")
    score += min(20.0, max(0.0, val_score))

    score = max(0.0, min(100.0, score))

    if score >= 72:
        signal = "综合买入信号"
    elif score >= 58:
        signal = "观望偏多"
    elif score >= 45:
        signal = "持有观察"
    else:
        signal = "观望"

    return StrategySignal(
        name="宏观+行业共振策略",
        signal=signal,
        score=round(score, 1),
        details=details,
        indicators=indicators,
    )


# ---------------------------------------------------------------------------
# Position sizing advice
# ---------------------------------------------------------------------------

def _position_advice(
    combined: float,
    bias: float | None,
    sell_range: tuple[float, float] | None,
    buy_range: tuple[float, float] | None,
    current_price: float,
) -> tuple[int, str]:
    """Return (position_pct, advice_text) based on composite signal strength.

    position_pct: suggested commitment as % of total investable capital.
    Logic:
      - In sell zone                      → 0%  (不建仓/应减仓)
      - Score < 42                        → 0%  (暂不介入)
      - Score 42-55, no buy zone hit      → 10% (轻仓观察)
      - Score 55-65                       → 20% (小仓试探)
      - Score 65-75, bias OK              → 30% (适度建仓)
      - Score 65-75, bias high (>10%)     → 15% (减半，等回踩)
      - Score ≥ 75, bias OK              → 50% (信号强，可重仓)
      - Score ≥ 75, bias high (>15%)     → 20% (强势但严重偏离，等回踩)
    """
    in_sell_zone = sell_range is not None and current_price >= sell_range[0]
    if in_sell_zone:
        return 0, "价格已进入卖出区间，不建议新建仓位；现有持仓可分批止盈"

    if combined < 42:
        return 0, "综合信号偏弱，暂不介入，等待信号改善"

    high_bias = bias is not None and bias > 15
    elevated_bias = bias is not None and 10 < bias <= 15

    if combined >= 75:
        if high_bias:
            return 20, (
                f"综合信号强（{combined:.0f}分）但乖离率过高（Bias={bias:.1f}%），"
                "建议仓位≤20%，等待回踩 MA5 后再加仓至 50%"
            )
        return 50, f"综合信号强（{combined:.0f}分），技术+基本面+宏观共振，可建立 50% 仓位"

    if combined >= 65:
        if high_bias:
            return 15, (
                f"信号较强（{combined:.0f}分）但乖离率偏高（Bias={bias:.1f}%），"
                "建议仓位≤15%，回踩均线后可加仓至 30%"
            )
        if elevated_bias:
            return 20, (
                f"信号较强（{combined:.0f}分），乖离率略高（Bias={bias:.1f}%），"
                "建议仓位 20%，不建议追高，等待 MA5 回踩"
            )
        return 30, f"信号较强（{combined:.0f}分），建议仓位 30%，可在买入区间附近分批介入"

    if combined >= 55:
        if high_bias or elevated_bias:
            return 10, (
                f"信号中性偏强（{combined:.0f}分）但乖离率偏高（Bias={bias:.1f}%），"
                "建议仓位≤10%，高位追入风险大"
            )
        return 20, f"信号中性偏强（{combined:.0f}分），建议轻仓 20% 试探，设好止损"

    # 42-55
    return 10, f"信号中性（{combined:.0f}分），建议持仓观望或 10% 小仓，不宜加仓"


# ---------------------------------------------------------------------------
# Unified multi-strategy runner
# ---------------------------------------------------------------------------

def run_all_strategies(
    close: pd.Series,
    volume: pd.Series | None,
    current_price: float,
    ticker: str,
    name: str,
    sector: str,
    pe_ratio: float | None,
    profit_margin: float | None,
    is_fund: bool,
    sell_range: tuple[float, float] | None = None,
    buy_range: tuple[float, float] | None = None,
) -> MultiStrategyReport:
    """Run all three strategies and return a combined report.

    Weights: Strategy1=30%, Strategy2=25%, Strategy3=45%
    Optional sell_range / buy_range feed the position-sizing advice.
    """
    # Best-effort fetch hot sectors & northbound proxy (non-blocking)
    hot_sectors: list[str] = []
    northbound_sentiment = "中性"
    try:
        hot_sectors = fetch_hot_sectors(limit=15)
    except Exception:
        pass
    try:
        northbound_sentiment = fetch_northbound_sentiment()
    except Exception:
        pass

    s1 = strategy_ma_trend(close, volume, current_price)
    s2 = strategy_rsi_bias(close, current_price)
    s3 = strategy_macro_industry(
        close=close,
        current_price=current_price,
        sector=sector,
        pe_ratio=pe_ratio,
        profit_margin=profit_margin,
        is_fund=is_fund,
        hot_sectors=hot_sectors,
        northbound_sentiment=northbound_sentiment,
    )

    combined = round(s1.score * 0.30 + s2.score * 0.25 + s3.score * 0.45, 1)
    in_hot = is_in_hot_sector(sector, hot_sectors)

    if combined >= 70:
        action = "综合买入信号" if in_hot else "可分批建仓"
    elif combined >= 58:
        action = "观望偏多，等待确认"
    elif combined >= 42:
        action = "持有观察"
    else:
        action = "暂不介入"

    # Position sizing
    bias = s2.indicators.get("乖离率Bias(20)%")
    pos_pct, pos_note = _position_advice(
        combined=combined,
        bias=float(bias) if bias is not None else None,
        sell_range=sell_range,
        buy_range=buy_range,
        current_price=current_price,
    )

    return MultiStrategyReport(
        ticker=ticker,
        name=name,
        strategy1=s1,
        strategy2=s2,
        strategy3=s3,
        combined_score=combined,
        composite_action=action,
        hot_sectors=hot_sectors,
        in_hot_sector=in_hot,
        sector=sector,
        northbound_sentiment=northbound_sentiment,
        position_pct=pos_pct,
        position_note=pos_note,
    )
