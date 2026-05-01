"""Web search layer for the Fin-Agent.

Responsibilities ("搜"):
  - Search news, announcements, industry events for a given ticker
  - Aggregate from multiple sources (Eastmoney, Sina, etc.)
  - Produce structured SearchResult dicts for the agent state
  - Web search via Tavily API (if key available)
    - Qveris.ai search enhancement (if key available)

Source priority for web-augmented search:
  1. Tavily Search API   — real-time global web, finance-focused
    2. Qveris.ai           — one-stop AI-native financial intelligence
  3. Eastmoney scraping  — always-available domestic A-share fallback

API keys are read from TAVILY_API_KEY / QVERIS_API_KEY env vars
or invest-app-data/api_keys.json.

This module is intentionally decoupled from LangGraph so it can be
tested independently and reused outside the graph.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any
from urllib.error import URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen

from invest_app.config import get_qveris_key, get_tavily_key
from invest_app.news import (
    NewsItem,
    _is_finance,
    _req,
    _ts_to_date,
    fetch_cn_news,
    fetch_research_by_ticker,
    fetch_research_reports,
)

# ---------------------------------------------------------------------------
# Ticker-specific news from Eastmoney
# ---------------------------------------------------------------------------

def _secid_from_ticker(ticker: str) -> str:
    """Convert plain 6-digit code to Eastmoney secid format."""
    code = ticker.strip().split(".")[0]
    if code.startswith("6"):
        return f"1.{code}"
    return f"0.{code}"


def search_ticker_news(ticker: str, limit: int = 15) -> list[dict[str, Any]]:
    """Fetch recent news/announcements for a specific stock from Eastmoney."""
    secid = _secid_from_ticker(ticker)
    code = ticker.strip().split(".")[0]
    results: list[dict[str, Any]] = []

    # Source 1: Eastmoney individual stock news
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        f"?cb=&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{code}%22"
        "%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D"
        "%2C%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22"
        "%2C%22clientVersion%22%3A%22curr%22"
        f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{limit}%2C%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )
    raw = _req(url)
    if raw:
        try:
            text = raw.decode("utf-8", errors="ignore").strip()
            # Strip JSONP wrapper
            if not text.startswith("{"):
                text = re.sub(r"^[^(]+\(", "", text).rstrip(");")
            data = json.loads(text)
            items = (
                data.get("result", {})
                .get("cmsArticleWebOld", {})
                .get("list", [])
            )
            for it in items[:limit]:
                title = (it.get("title") or "").strip()
                title = re.sub(r"<[^>]+>", "", title)  # strip HTML tags
                if not title:
                    continue
                results.append({
                    "title": title,
                    "url": it.get("url", ""),
                    "source": it.get("mediaName", "东方财富"),
                    "date": _ts_to_date(it.get("date", "")),
                    "tag": "个股",
                    "summary": (it.get("content") or "")[:200],
                })
        except Exception:
            pass

    # Source 2: Eastmoney stock announcement API
    ann_url = (
        f"https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?cb=&sr=-1&page_size={min(limit, 10)}&page_index=1"
        f"&ann_type=A&client_source=web&stock_list={code}"
    )
    raw2 = _req(ann_url)
    if raw2:
        try:
            text2 = raw2.decode("utf-8", errors="ignore").strip()
            if not text2.startswith("{"):
                text2 = re.sub(r"^[^(]+\(", "", text2).rstrip(");")
            data2 = json.loads(text2)
            for it in (data2.get("data", {}).get("list", []) or []):
                title = (it.get("title") or it.get("art_title") or "").strip()
                if not title:
                    continue
                results.append({
                    "title": title,
                    "url": it.get("url", ""),
                    "source": "东方财富公告",
                    "date": _ts_to_date(it.get("notice_date", "")),
                    "tag": "公告",
                    "summary": "",
                })
        except Exception:
            pass

    return results[:limit]


# ---------------------------------------------------------------------------
# Industry / sector event search
# ---------------------------------------------------------------------------

def search_sector_events(sector: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search for industry/sector news via Eastmoney sector keywords."""
    keyword = url_quote(sector.replace("/", " "))
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        f"?cb=&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{keyword}%22"
        "%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D"
        "%2C%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22"
        "%2C%22clientVersion%22%3A%22curr%22"
        f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{limit}%2C%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )
    raw = _req(url)
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text.startswith("{"):
            text = re.sub(r"^[^(]+\(", "", text).rstrip(");")
        data = json.loads(text)
        items = (
            data.get("result", {})
            .get("cmsArticleWebOld", {})
            .get("list", [])
        )
        results: list[dict[str, Any]] = []
        for it in items[:limit]:
            title = re.sub(r"<[^>]+>", "", (it.get("title") or "")).strip()
            if not title or not _is_finance(title):
                continue
            results.append({
                "title": title,
                "url": it.get("url", ""),
                "source": it.get("mediaName", "东方财富"),
                "date": _ts_to_date(it.get("date", "")),
                "tag": "行业",
                "summary": re.sub(r"<[^>]+>", "", (it.get("content") or ""))[:200],
            })
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Market-wide macro search
# ---------------------------------------------------------------------------

def search_macro_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch macro/policy news from existing news sources."""
    items = fetch_cn_news(n=limit * 2)
    macro_keywords = ("央行", "政策", "利率", "降息", "加息", "GDP", "CPI",
                      "货币", "财政", "监管", "证监会", "发改委", "国务院",
                      "关税", "贸易", "制裁", "地缘", "战争", "选举")
    results: list[dict[str, Any]] = []
    for it in items:
        if any(kw in it.title for kw in macro_keywords):
            results.append({
                "title": it.title,
                "url": it.url,
                "source": it.source,
                "date": it.date,
                "tag": "宏观",
                "summary": it.summary,
            })
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Research report search (LanceDB store + live Eastmoney fallback)
# ---------------------------------------------------------------------------

def search_research_reports(
    ticker: str = "",
    sector: str = "",
    query: str = "",
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return relevant research reports for the agent pipeline.

    Priority:
      1. Ticker-specific live fetch from Eastmoney (if ticker provided)
      2. Sector-filtered LanceDB query (if sector provided)
      3. Keyword search in LanceDB store
      4. Most-recent reports from LanceDB store

    Results are serialized to plain dicts compatible with the agent state.
    """
    from invest_app import research_store

    items: list[NewsItem] = []
    store_rows: list[dict[str, Any]] = []

    if ticker:
        # Live fetch brings fresh data AND auto-ingests into the store
        items = fetch_research_by_ticker(ticker, n=limit)

    if not items and sector:
        store_rows = research_store.search_by_sector(sector, limit=limit)

    if not items and not store_rows:
        if query:
            store_rows = research_store.search_text(query, limit=limit)
        else:
            store_rows = research_store.get_recent(limit=limit)

    def _item_to_dict(it: NewsItem) -> dict[str, Any]:
        return {
            "title": it.title,
            "url": it.url,
            "source": it.source,
            "date": it.date,
            "tag": it.tag,
            "summary": it.summary,
            "rating": it.rating,
            "rating_change": it.rating_change,
            "stock_code": it.stock_code,
            "sector": it.sector,
        }

    result = [_item_to_dict(it) for it in items] if items else list(store_rows[:limit])
    return result[:limit]


# ---------------------------------------------------------------------------
# Aggregated search entry point
# ---------------------------------------------------------------------------

def agent_search(
    ticker: str,
    sector: str = "",
    *,
    news_limit: int = 10,
    announcement_limit: int = 5,
    sector_limit: int = 5,
    macro_limit: int = 5,
    research_limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Run all search queries for the agent pipeline.

    Returns a dict with keys: news, announcements, sector_events, macro_events,
    research_reports.  Each value is a list of item dicts.  Failures return empty lists.
    """
    ticker_items = search_ticker_news(ticker, limit=news_limit + announcement_limit)

    news = [it for it in ticker_items if it.get("tag") != "公告"][:news_limit]
    announcements = [it for it in ticker_items if it.get("tag") == "公告"][:announcement_limit]

    # Add general market news to fill gaps
    if len(news) < news_limit:
        general = fetch_cn_news(n=news_limit - len(news))
        for it in general:
            news.append({
                "title": it.title,
                "url": it.url,
                "source": it.source,
                "date": it.date,
                "tag": it.tag or "快讯",
                "summary": it.summary,
            })

    sector_events = search_sector_events(sector, limit=sector_limit) if sector else []
    macro_events = search_macro_events(limit=macro_limit)

    # Augment with web search if an API key is configured
    web_news = search_web_news(f"{ticker} 股票 新闻", limit=min(news_limit, 5))
    if web_news:
        existing_titles = {it.get("title", "") for it in news}
        for item in web_news:
            if item["title"] not in existing_titles and len(news) < news_limit:
                news.append(item)
                existing_titles.add(item["title"])

    # Research reports via LanceDB store + live Eastmoney
    research_reports = search_research_reports(
        ticker=ticker, sector=sector, limit=research_limit
    )

    return {
        "news": news[:news_limit],
        "announcements": announcements[:announcement_limit],
        "sector_events": sector_events[:sector_limit],
        "macro_events": macro_events[:macro_limit],
        "research_reports": research_reports,
    }


# ---------------------------------------------------------------------------
# Web search backends (Tavily → Qveris.ai MCP → no-op)
# ---------------------------------------------------------------------------

def _search_via_tavily(query: str, limit: int) -> list[dict[str, Any]]:
    """Call Tavily Search API for real-time web results.

    Tavily is fully compatible with LangGraph/LangChain agents and provides
    finance-quality snippets from the open web.
    API key: TAVILY_API_KEY env var or api_keys.json {"tavily": "tvly-..."}

    Docs: https://docs.tavily.com/docs/rest-api/api-reference
    """
    api_key = get_tavily_key()
    if not api_key:
        return []

    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "topic": "finance",
        "include_answer": False,
    }).encode("utf-8")
    try:
        req = Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for item in data.get("results", [])[:limit]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "url": item.get("url", ""),
            "source": "Tavily Web",
            "date": "",
            "tag": "搜索",
            "summary": (item.get("content") or "")[:300],
        })
    return results


def _search_via_qveris(query: str, limit: int) -> list[dict[str, Any]]:
    """Call Qveris.ai REST/MCP endpoint for financial intelligence.

    Qveris.ai provides a one-stop financial data & AI search platform with
    native MCP (Model Context Protocol) support, making it straightforward
    to wire into LangGraph as a tool.

    When the user has a Qveris API key this function is the preferred global
    web source; for purely domestic A-share queries Eastmoney scraping is
    faster and free.

    Integration options:
      A. REST  — POST /v1/search with JSON body (used here as a plain fallback)
      B. MCP   — expose Qveris as a langchain-mcp-adapters tool in agent_graph.py
                 so LangGraph can call it autonomously during the "搜" node.

    API key: QVERIS_API_KEY env var or api_keys.json {"qveris": "qv-..."}
    Docs: https://docs.qveris.ai  (replace with actual endpoint when available)
    """
    api_key = get_qveris_key()
    if not api_key:
        return []

    payload = json.dumps({
        "query": query,
        "limit": limit,
        "topics": ["finance", "market"],
    }).encode("utf-8")
    try:
        req = Request(
            "https://api.qveris.ai/v1/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(req, timeout=15) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for item in (data.get("results") or data.get("items") or [])[:limit]:
        title = (item.get("title") or item.get("headline") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "url": item.get("url", ""),
            "source": "Qveris.ai",
            "date": item.get("published_at", ""),
            "tag": "搜索",
            "summary": (item.get("summary") or item.get("snippet") or "")[:300],
        })
    return results


def search_web_news(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Unified web search entry point for the agent pipeline.

    Tries Tavily first, then Qveris.ai, silently returns empty list if
    neither key is configured (domestic Eastmoney paths cover the gap).
    """
    results = _search_via_tavily(query, limit)
    if results:
        return results
    return _search_via_qveris(query, limit)
