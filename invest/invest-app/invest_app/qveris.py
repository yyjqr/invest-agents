"""Qveris.ai integration helpers for news and research analysis."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any
from urllib.request import Request, urlopen

from invest_app.config import get_qveris_key
from invest_app.news import NewsItem, fetch_stock_news
from invest_app.industry import (
    classify_sector,
    estimate_sector_maturity,
    get_sector_benchmarks,
    build_swot,
    FOCUS_SECTORS,
    _SECTOR_KEYWORDS,
    SECTOR_STOCKS,
)
from invest_app.market_data import (
    get_batch_quotes_tencent,
    resolve_ticker,
    get_company_profile,
    get_recent_earnings,
)

_POSITIVE_KEYWORDS = (
    "上调", "增持", "回购", "突破", "超预期", "修复", "改善", "提振", "增长", "景气",
    "新高", "落地", "催化", "受益", "扩产", "订单",
)
_NEGATIVE_KEYWORDS = (
    "下调", "减持", "制裁", "关税", "诉讼", "违约", "亏损", "退市", "承压", "波动",
    "下滑", "疲弱", "风险", "暴雷", "地缘", "冲突", "失速",
)
_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "政策监管": ("政策", "证监会", "央行", "财政", "监管", "降息", "加息", "国务院", "发改委"),
    "业绩估值": ("业绩", "财报", "年报", "季报", "净利", "营收", "利润", "估值", "PE", "PB"),
    "产业趋势": ("AI", "算力", "半导体", "新能源", "机器人", "医药", "消费", "军工", "有色", "煤炭"),
    "资金情绪": ("北向", "南向", "成交额", "主力资金", "融资", "融券", "增持", "减持", "回购"),
    "宏观扰动": ("美国", "非农", "通胀", "CPI", "PPI", "汇率", "贸易", "关税", "地缘", "选举"),
}
_CATEGORY_LABELS = {
    "cn": "财经快讯",
    "research": "券商研报",
    "global": "国际宏观",
    "all": "综合资讯",
}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
    if not url:
        return None
    try:
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _normalize_context_rows(data: dict[str, Any], limit: int) -> list[dict[str, str]]:
    rows = data.get("results") or data.get("items") or []
    context_rows: list[dict[str, str]] = []
    for item in rows[:limit]:
        title = (item.get("title") or item.get("headline") or "").strip()
        if not title:
            continue
        context_rows.append(
            {
                "title": title,
                "url": item.get("url", ""),
                "source": item.get("source") or "Qveris.ai",
                "summary": (item.get("summary") or item.get("snippet") or "").strip()[:220],
            }
        )
    return context_rows


def search_qveris_context(query: str, limit: int = 4) -> list[dict[str, str]]:
    """Fetch supplemental context from qveris.ai when a key is configured."""
    api_key = get_qveris_key()
    if not api_key:
        return []

    payload = {
        "query": query,
        "limit": limit,
        "topics": ["finance", "market", "research"],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    endpoints = [
        os.environ.get("QVERIS_SEARCH_URL", "").strip(),
        "https://api.qveris.ai/v1/search",
    ]
    for endpoint in endpoints:
        data = _post_json(endpoint, payload, headers)
        if not data:
            continue
        results = _normalize_context_rows(data, limit)
        if results:
            return results
    return []


def _flatten_texts(items: list[NewsItem], context_rows: list[dict[str, str]]) -> list[str]:
    texts: list[str] = []
    for item in items:
        text = " ".join(part for part in [item.title, item.summary, item.tag, item.source] if part).strip()
        if text:
            texts.append(text)
    for item in context_rows:
        text = " ".join(part for part in [item.get("title", ""), item.get("summary", ""), item.get("source", "")] if part).strip()
        if text:
            texts.append(text)
    return texts


def _count_keyword_hits(texts: list[str], keywords: tuple[str, ...]) -> int:
    return sum(1 for text in texts if any(keyword in text for keyword in keywords))


def _extract_themes(texts: list[str]) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for theme, keywords in _THEME_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                counter[theme] += 1
    return [theme for theme, _ in counter.most_common(3)]


def _build_query(items: list[NewsItem], category: str, focus: str, company: str = "", sector: str = "") -> str:
    category_label = _CATEGORY_LABELS.get(category, "市场资讯")
    headlines: list[str] = []
    for item in items[:3]:
        title = re.sub(r"\s+", " ", item.title).strip()
        if title:
            headlines.append(title[:24])
    focus_text = focus.strip()
    company_text = company.strip()
    sector_text = sector.strip()
    parts = [category_label]
    if company_text:
        parts.append(company_text)
    if sector_text:
        parts.append(sector_text)
    if focus_text:
        parts.append(focus_text)
    if headlines:
        parts.append(" | ".join(headlines))
    parts.append("市场影响 风险 催化")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _build_key_points(items: list[NewsItem], context_rows: list[dict[str, str]]) -> list[str]:
    points: list[str] = []
    for item in items[:3]:
        details = item.summary.strip()
        points.append(f"{item.title}；{details[:80]}" if details else item.title)
    for item in context_rows[:2]:
        details = item.get("summary", "")
        title = item.get("title", "")
        if title:
            points.append(f"Qveris 补充：{title}" + (f"；{details[:80]}" if details else ""))
    return points[:5]


def _build_opportunities(items: list[NewsItem], context_rows: list[dict[str, str]], themes: list[str]) -> list[str]:
    rows: list[str] = []
    for item in items:
        text = f"{item.title} {item.summary}"
        if any(keyword in text for keyword in _POSITIVE_KEYWORDS):
            rows.append(f"关注：{item.title}")
    for item in context_rows:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        if any(keyword in text for keyword in _POSITIVE_KEYWORDS):
            rows.append(f"Qveris 延伸：{item.get('title', '')}")
    if not rows and themes:
        rows.append(f"当前主线集中在{themes[0]}，适合继续跟踪政策、财报或资金验证。")
    if not rows:
        rows.append("当前没有特别集中的正向催化，适合继续观察。")
    return rows[:3]


def _build_risks(items: list[NewsItem], context_rows: list[dict[str, str]], category: str) -> list[str]:
    rows: list[str] = []
    for item in items:
        text = f"{item.title} {item.summary}"
        if any(keyword in text for keyword in _NEGATIVE_KEYWORDS):
            rows.append(f"警惕：{item.title}")
    for item in context_rows:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        if any(keyword in text for keyword in _NEGATIVE_KEYWORDS):
            rows.append(f"Qveris 风险提示：{item.get('title', '')}")
    if not rows:
        fallback = {
            "research": "研报结论需要回到财报、估值和成交验证。",
            "global": "国际宏观传导到 A 股通常存在时滞，避免线性外推。",
            "cn": "快讯噪声较高，需要结合公告与量能判断持续性。",
            "all": "综合资讯方向较散，需优先区分主线催化和一次性事件。",
        }
        rows.append(fallback.get(category, "需继续跟踪后续公告、成交量与资金面确认。"))
    return rows[:3]


# ---------------------------------------------------------------------------
# Company / sector analysis enrichment
# ---------------------------------------------------------------------------

def _map_to_internal_sector(sector_input: str) -> str:
    """Map a user-supplied sector string to the closest internal sector key."""
    if not sector_input:
        return ""
    for internal_sector, keywords in _SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in sector_input.lower():
                return internal_sector
    return sector_input


def _build_company_sector_analysis(
    company: str,
    sector: str,
    ticker: str = "",
) -> dict[str, Any]:
    """Fetch live fundamentals/history and generate SWOT + sector context.

    Two modes:
      1. Ticker provided → single-stock deep analysis (fundamentals + SWOT)
      2. Sector only → batch-fetch representative stocks, compute sector metrics,
         build sector-level SWOT with real data
    """
    company = company.strip()
    sector = sector.strip()
    ticker = ticker.strip()

    # Auto-detect sector if not provided
    detected_sector = _map_to_internal_sector(sector)
    if not detected_sector and ticker:
        detected_sector = classify_sector(ticker, company)
    if not detected_sector and company:
        detected_sector = classify_sector("", company)

    mapped = detected_sector or sector

    result: dict[str, Any] = {
        "sector": mapped,
        "maturity": "",
        "is_focus_sector": False,
        "benchmarks": [],
        "swot": None,
        "fundamentals": {},
        "sector_stocks": [],      # live performance of representative stocks
        "sector_summary": "",     # sector-level textual insight
    }

    if mapped:
        result["maturity"] = estimate_sector_maturity(mapped)
        result["is_focus_sector"] = mapped in FOCUS_SECTORS
        result["benchmarks"] = [
            {"ticker": t, "label": lbl}
            for t, lbl in get_sector_benchmarks(mapped)
        ]

    # ── Mode 1: Ticker provided → single-stock deep analysis ──────────
    if ticker:
        pe_ratio, profit_margin, current_price = None, None, None
        return_on_equity, debt_to_equity = None, None
        revenue_growth_yoy, free_cash_flow, dividend_yield = None, None, None
        ma20, ma60, high_6m, low_6m, volume_ratio = None, None, None, None, None

        try:
            import pandas as pd  # noqa: F401
            from invest_app.market_data import get_fundamentals, get_history

            fundamentals = get_fundamentals(ticker)
            pe_ratio = fundamentals.get("pe_ratio")
            profit_margin = fundamentals.get("profit_margin")
            return_on_equity = fundamentals.get("return_on_equity")
            debt_to_equity = fundamentals.get("debt_to_equity")
            revenue_growth_yoy = fundamentals.get("revenue_growth_yoy")
            free_cash_flow = fundamentals.get("free_cash_flow")
            dividend_yield = fundamentals.get("dividend_yield")

            history = get_history(ticker, period="6mo", interval="1d")
            if history is not None and not history.empty and "Close" in history.columns:
                closes = history["Close"].astype(float).dropna()
                if not closes.empty:
                    current_price = float(closes.iloc[-1])
                    if len(closes) >= 20:
                        ma20 = float(closes.tail(20).mean())
                    if len(closes) >= 60:
                        ma60 = float(closes.tail(60).mean())
                    high_6m = float(closes.max())
                    low_6m = float(closes.min())
                if "Volume" in history.columns:
                    vol = history["Volume"].astype(float).dropna()
                    avg_vol = float(vol.tail(20).mean()) if len(vol) >= 20 else None
                    if avg_vol and avg_vol > 0:
                        volume_ratio = float(vol.iloc[-1]) / avg_vol
        except Exception:
            pass

        result["fundamentals"] = {
            "pe_ratio": pe_ratio,
            "profit_margin": (
                round(profit_margin * 100, 1) if profit_margin is not None else None
            ),
            "return_on_equity": (
                round(return_on_equity * 100, 2) if return_on_equity is not None else None
            ),
            "debt_to_equity": (
                round(debt_to_equity * 100, 1) if debt_to_equity is not None else None
            ),
            "revenue_growth_yoy": (
                round(revenue_growth_yoy * 100, 1) if revenue_growth_yoy is not None else None
            ),
            "free_cash_flow": (
                round(free_cash_flow / 1e8, 2) if free_cash_flow is not None else None
            ),
            "dividend_yield": (
                round(dividend_yield * 100, 2) if dividend_yield is not None else None
            ),
        }

        try:
            swot = build_swot(
                current_price=current_price or 0.0,
                ma20=ma20, ma60=ma60,
                high_6m=high_6m, low_6m=low_6m,
                volume_ratio=volume_ratio,
                pe_ratio=pe_ratio, profit_margin=profit_margin,
                sector=mapped, is_fund=False,
            )
            result["swot"] = {
                "strengths": swot.strengths,
                "weaknesses": swot.weaknesses,
                "opportunities": swot.opportunities,
                "threats": swot.threats,
            }
        except Exception:
            pass

        # Also fetch sector representative stocks for context
        _enrich_sector_stocks(result, mapped)

        stock_name = company or ticker
        summary_parts = [f"「{stock_name}」"]
        if mapped:
            summary_parts.append(f"属于「{mapped}」板块")
        if pe_ratio is not None:
            val_tag = "低估值" if pe_ratio < 20 else ("中等估值" if pe_ratio < 40 else "高估值")
            summary_parts.append(f"PE {pe_ratio:.1f}（{val_tag}）")
        if profit_margin is not None:
            summary_parts.append(f"净利率 {profit_margin*100:.1f}%")
        if current_price and high_6m and low_6m:
            pos = (current_price - low_6m) / (high_6m - low_6m) * 100 if high_6m > low_6m else 50
            summary_parts.append(f"当前价 {current_price:.2f}，处于6个月区间 {pos:.0f}% 位置")
        result["sector_summary"] = "，".join(summary_parts) + "。"

        # ── Enrich: company profile (industry classification, region, listing info) ──
        try:
            profile = get_company_profile(ticker)
            result["profile"] = profile
            # Patch sector_summary with region if available
            if profile.get("region"):
                result["sector_summary"] += f"总部位于{profile['region']}。"
        except Exception:
            result["profile"] = {}

        # ── Enrich: recent quarterly / annual earnings ──
        try:
            result["recent_earnings"] = get_recent_earnings(ticker, n=4)
        except Exception:
            result["recent_earnings"] = []

        # ── Enrich: recent major news (利好/利空/公告) ──
        try:
            news_items = fetch_stock_news(ticker, n=5)
            result["recent_news"] = [
                {
                    "title": item.title,
                    "url": item.url,
                    "date": item.date,
                    "tag": item.tag,
                    "source": item.source,
                }
                for item in news_items
            ]
        except Exception:
            result["recent_news"] = []

        return result

    # ── Mode 2: Sector only → batch analysis of representative stocks ──
    _enrich_sector_stocks(result, mapped)

    sector_stocks_data = result.get("sector_stocks", [])
    valid = [s for s in sector_stocks_data if s.get("price") is not None]

    if valid:
        avg_pct = sum(s["change_pct"] for s in valid) / len(valid)
        best = max(valid, key=lambda s: s["change_pct"])
        worst = min(valid, key=lambda s: s["change_pct"])
        total_turnover = sum(s.get("turnover") or 0 for s in valid)

        # Sector-level SWOT using aggregate data
        strengths, weaknesses, opportunities, threats = [], [], [], []

        if avg_pct > 0.5:
            strengths.append(f"板块今日整体上涨（均涨 {avg_pct:+.2f}%），市场情绪偏暖")
        if best["change_pct"] > 2:
            strengths.append(f"龙头「{best['name']}」涨幅突出（{best['change_pct']:+.2f}%），带动板块热度")
        if total_turnover > 1e9:
            strengths.append(f"板块总成交额 {total_turnover/1e8:.1f}亿，资金参与度高")

        if avg_pct < -0.5:
            weaknesses.append(f"板块今日整体下跌（均跌 {avg_pct:+.2f}%），短期情绪偏弱")
        if worst["change_pct"] < -2:
            weaknesses.append(f"「{worst['name']}」跌幅较大（{worst['change_pct']:+.2f}%），注意个股风险")
        if total_turnover < 2e8:
            weaknesses.append("板块成交低迷，资金关注度不足")

        up_count = sum(1 for s in valid if s["change_pct"] > 0)
        if up_count > len(valid) * 0.7:
            opportunities.append(f"板块内 {up_count}/{len(valid)} 只股票上涨，普涨格局有延续可能")
        if mapped in FOCUS_SECTORS:
            opportunities.append(f"「{mapped}」属于长期重点赛道，政策与产业趋势支撑")

        down_count = sum(1 for s in valid if s["change_pct"] < 0)
        if down_count > len(valid) * 0.7:
            threats.append(f"板块内 {down_count}/{len(valid)} 只股票下跌，需警惕系统性回调")
        if avg_pct > 3:
            threats.append("板块短期涨幅过大，获利盘压力可能增加")

        if not strengths:
            strengths.append("板块波动平稳，无明显异动")
        if not weaknesses:
            weaknesses.append("暂无明显劣势信号")
        if not opportunities:
            opportunities.append("可关注板块内低位优质标的的补涨机会")
        if not threats:
            threats.append("需持续关注宏观政策与行业变化")

        result["swot"] = {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "opportunities": opportunities,
            "threats": threats,
        }

        # Build textual summary
        top3 = sorted(valid, key=lambda s: s["change_pct"], reverse=True)[:3]
        top3_str = "、".join(f"{s['name']}({s['change_pct']:+.2f}%)" for s in top3)
        turnover_str = f"{total_turnover/1e8:.1f}亿" if total_turnover >= 1e8 else f"{total_turnover/1e4:.0f}万"
        result["sector_summary"] = (
            f"「{mapped}」板块今日均涨幅 {avg_pct:+.2f}%，{up_count}涨{down_count}跌，"
            f"总成交额 {turnover_str}。涨幅前三：{top3_str}。"
        )
    else:
        result["sector_summary"] = f"「{mapped}」板块暂无实时行情数据，请稍后再试。"

    return result


def _enrich_sector_stocks(result: dict[str, Any], mapped: str) -> None:
    """Fetch live quotes for sector representative stocks and attach to result."""
    # Find matching sector in SECTOR_STOCKS (try multiple key formats)
    sector_stock_list = SECTOR_STOCKS.get(mapped, [])
    if not sector_stock_list:
        # Try partial match
        for key, stocks in SECTOR_STOCKS.items():
            if mapped in key or key in mapped:
                sector_stock_list = stocks
                break

    if not sector_stock_list:
        return

    raw_codes = [s.ticker.split(".")[0] for s in sector_stock_list]
    try:
        quotes = get_batch_quotes_tencent(raw_codes)
    except Exception:
        quotes = []

    quote_map = {}
    for q in quotes:
        raw = q.ticker.split(".")[0]
        quote_map[raw] = q

    sector_stocks_data = []
    for s in sector_stock_list:
        raw = s.ticker.split(".")[0]
        q = quote_map.get(raw)
        entry = {
            "ticker": s.ticker,
            "name": s.name,
            "desc": s.desc,
            "price": round(q.price, 2) if q else None,
            "change_pct": round(q.change_pct, 2) if q else None,
            "turnover": q.turnover if q else None,
        }
        sector_stocks_data.append(entry)
    result["sector_stocks"] = sector_stocks_data


def analyze_news_briefing(
    items: list[NewsItem],
    *,
    category: str,
    focus: str = "",
    company: str = "",
    sector: str = "",
    ticker: str = "",
) -> dict[str, Any]:
    """Summarize current news and enrich it with qveris.ai context when possible."""
    if not items:
        raise ValueError("没有可分析的资讯样本")

    # Auto-resolve: if user typed a stock name in focus but left company/sector/ticker empty,
    # try to resolve it to a ticker and classify the sector automatically.
    if focus.strip() and not company.strip() and not sector.strip() and not ticker.strip():
        try:
            resolved = resolve_ticker(focus.strip())
            # resolve_ticker returns the original string on failure; check if we got a code
            if resolved and resolved != focus.strip() and resolved[:1].isdigit():
                ticker = resolved
                company = focus.strip()
                detected = classify_sector(resolved, focus.strip())
                if detected and detected != "其他":
                    sector = detected
        except Exception:
            pass

    query = _build_query(items, category, focus, company=company, sector=sector)
    context_rows = search_qveris_context(query, limit=4)
    texts = _flatten_texts(items, context_rows)
    positive_hits = _count_keyword_hits(texts, _POSITIVE_KEYWORDS)
    negative_hits = _count_keyword_hits(texts, _NEGATIVE_KEYWORDS)
    delta = positive_hits - negative_hits
    if delta >= 2:
        sentiment = "偏多"
    elif delta <= -2:
        sentiment = "偏空"
    else:
        sentiment = "中性"

    themes = _extract_themes(texts)
    theme_text = "、".join(themes) if themes else "事件驱动"
    category_label = _CATEGORY_LABELS.get(category, "市场资讯")

    subject = (company.strip() or focus.strip() or sector.strip())
    focus_text = f"围绕\"{subject}\"" if subject else ""

    context_desc_parts = []
    if company.strip():
        context_desc_parts.append(f"公司：{company.strip()}")
    if sector.strip():
        context_desc_parts.append(f"行业：{sector.strip()}")
    context_desc = "（" + "，".join(context_desc_parts) + "）" if context_desc_parts else ""

    context_note = (
        "已叠加 Qveris.ai 外部检索上下文。"
        if context_rows else
        "当前未命中 Qveris.ai 外部上下文，结果以本地资讯归纳为主。"
    )
    summary = (
        f"本轮{category_label}{focus_text}{context_desc}的主线集中在{theme_text}。"
        f"整体情绪判断为{sentiment}，偏正向信号 {positive_hits} 条、偏负向信号 {negative_hits} 条。"
        f"{context_note}"
    )

    # Build company/sector analysis if company or sector was supplied
    company_analysis: dict[str, Any] | None = None
    if company.strip() or sector.strip() or ticker.strip():
        company_analysis = _build_company_sector_analysis(
            company=company,
            sector=sector,
            ticker=ticker,
        )
        # Append sector-specific summary from live data
        sector_summary = (company_analysis or {}).get("sector_summary", "")
        if sector_summary:
            summary += " " + sector_summary

    return {
        "provider": "Qveris.ai+local" if context_rows else "local",
        "summary": summary,
        "sentiment": sentiment,
        "score": max(0, min(100, 50 + delta * 12)),
        "themes": themes,
        "key_points": _build_key_points(items, context_rows),
        "opportunities": _build_opportunities(items, context_rows, themes),
        "risks": _build_risks(items, context_rows, category),
        "context_items": context_rows,
        "query": query,
        "key_configured": bool(get_qveris_key()),
        "company": company.strip(),
        "sector": sector.strip(),
        "company_analysis": company_analysis,
    }