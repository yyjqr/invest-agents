"""FastAPI server — main entry point for the invest-app backend.

Exposes REST + WebSocket APIs consumed by the React/Tauri frontend.

Endpoints:
  POST  /api/auth/login          — username/password → JWT
  POST  /api/auth/register       — create new user
  GET   /api/auth/me             — current user info
  GET   /api/stocks/search       — search instruments
  GET   /api/stocks/quote/{t}    — single quote
  POST  /api/stocks/batch-quotes — batch quotes (up to 20)
  GET   /api/stocks/history/{t}  — OHLCV history
  GET   /api/stocks/intraday/{t} — intraday 1-min data
  GET   /api/strategy/{ticker}   — run all 3 strategies
  GET   /api/forecast/{ticker}   — short-term forecast
  GET   /api/watchlist           — user watchlist
  POST  /api/watchlist           — add item
  DELETE/api/watchlist/{ticker}  — remove item
  GET   /api/monitor             — user monitor list (≤20)
  PUT   /api/monitor             — update monitor list
  GET   /api/portfolio           — user portfolio
  GET   /api/settings            — user settings
  PUT   /api/settings            — update settings
  GET   /api/alerts              — price alert rules
  PUT   /api/alerts/{ticker}     — set / update one alert rule
  DELETE/api/alerts/{ticker}     — delete one alert rule
  GET   /api/smart-picks         — 智能精选（TTL 缓存）
  POST  /api/smart-picks/refresh — 强制刷新智能精选
  WS    /ws/prices               — realtime price stream (5s push)
"""

from __future__ import annotations

import asyncio
import json
import logging
import tomllib
import traceback
from datetime import datetime, time
from pathlib import Path


def _read_cargo_version(fallback: str = "0.1.1") -> str:
    """Read version from src-tauri/Cargo.toml (2 dirs up from invest_app/)."""
    try:
        cargo = Path(__file__).parents[2] / "src-tauri" / "Cargo.toml"
        with cargo.open("rb") as f:
            data = tomllib.load(f)
        return data["package"]["version"]
    except Exception:
        return fallback


_APP_VERSION = _read_cargo_version()
from contextlib import asynccontextmanager
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Body,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from invest_app.auth import (
    authenticate_user,
    create_access_token,
    create_user,
    decode_token,
    ensure_default_admin,
)
from invest_app.market_data import (
    get_batch_quotes_tencent,
    get_history,
    get_intraday,
    get_quote,
    get_fundamentals,
    get_full_stock_profile,
    resolve_ticker,
    search_instruments,
)
from invest_app.portfolio import load_portfolio, save_portfolio, add_holding, remove_holding
from invest_app.recommendation import recommend_stock
from invest_app.broker_import import (
    BROKER_INFO,
    BROKER_PARSERS,
    ImportedHolding,
    merge_into_portfolio,
)
from invest_app.forecast import build_short_term_forecast
from invest_app.news import (NewsItem, fetch_cn_news, fetch_research_by_ticker,
    fetch_research_reports, fetch_global_events, get_cached_news, _start_news_background)
from invest_app import research_store
from invest_app import user_data
from invest_app.config import save_api_keys
from invest_app.qveris import analyze_news_briefing
from invest_app.trading_gateway import router as trading_router

logger = logging.getLogger("invest.server")

CN_TZ = ZoneInfo("Asia/Shanghai")


def _is_cn_market_open(now: Optional[datetime] = None) -> bool:
    current = now.astimezone(CN_TZ) if now else datetime.now(CN_TZ)
    if current.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False

    current_time = current.time()
    morning_open = time(9, 30)
    morning_close = time(11, 30)
    afternoon_open = time(13, 0)
    afternoon_close = time(15, 0)

    return (
        (morning_open <= current_time < morning_close)
        or (afternoon_open <= current_time < afternoon_close)
    )


def _quote_from_last_close(ticker: str):
    """Build a Quote-like payload from the latest available daily close (e.g. Friday close on weekends)."""
    try:
        # Get the real Chinese name from Tencent API (works on weekends)
        try:
            quote = get_quote(ticker)
            name = quote.name or ticker
        except Exception:
            name = ticker

        history = get_history(ticker, period="5d", interval="1d")
        if history is None or history.empty or "Close" not in history.columns:
            return None

        closes = history["Close"].dropna()
        if closes.empty:
            return None

        current = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else current
        change = current - prev
        change_pct = (change / prev * 100) if prev else 0.0

        return {
            "ticker": ticker,
            "price": current,
            "change": change,
            "change_pct": change_pct,
            "name": name,
            "turnover": None,
            "volume": None,
        }
    except Exception:
        return None

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_default_admin()
    _start_news_background(interval_sec=120)
    logger.info("Invest-app FastAPI server started (default admin: admin/admin123)")
    yield


app = FastAPI(title="Invest App API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trading_router)

security = Depends(HTTPBearer(auto_error=False))


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_current_user(token: Optional[str]) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return payload


def _bearer(credentials: Optional[HTTPAuthorizationCredentials]) -> str | None:
    return credentials.credentials if credentials else None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user["username"], user["display_name"])
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.post("/api/auth/register", status_code=201)
async def register(req: RegisterRequest):
    ok = create_user(req.username, req.password, req.display_name)
    if not ok:
        raise HTTPException(status_code=409, detail="用户名已存在")
    token = create_access_token(req.username, req.display_name or req.username)
    return {"access_token": token, "token_type": "bearer",
            "user": {"username": req.username, "display_name": req.display_name or req.username}}


@app.get("/api/auth/me")
async def me(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user


# ---------------------------------------------------------------------------
# Stock data endpoints
# ---------------------------------------------------------------------------

@app.get("/api/stocks/search")
async def search_stocks(q: str = Query(..., min_length=1)):
    try:
        results = search_instruments(q)
        return [{"symbol": r.symbol, "code": r.code, "name": r.name,
                 "exchange": r.exchange, "category": r.category} for r in results]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


@app.get("/api/stocks/quote/{ticker}")
async def get_stock_quote(ticker: str,
                          credentials: Optional[HTTPAuthorizationCredentials] = security):
    _get_current_user(_bearer(credentials))
    try:
        q = get_quote(ticker)
        if q is None:
            raise HTTPException(status_code=404, detail=f"未找到股票 {ticker}")
        return {
            "ticker": q.ticker, "price": q.price, "change": q.change,
            "change_pct": q.change_pct, "name": q.name,
            "pe_ttm": q.pe_ttm, "pb_ratio": q.pb_ratio,
            "market_cap_total": q.market_cap_total, "market_cap_float": q.market_cap_float,
            "turnover": q.turnover, "volume": q.volume, "turnover_rate": q.turnover_rate,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Quote error {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BatchQuoteRequest(BaseModel):
    tickers: list[str]


@app.post("/api/stocks/batch-quotes")
async def batch_quotes(req: BatchQuoteRequest,
                       credentials: Optional[HTTPAuthorizationCredentials] = security):
    _get_current_user(_bearer(credentials))
    tickers = req.tickers[:20]
    quotes = get_batch_quotes_tencent(tickers)
    return [
        {
            "ticker": q.ticker, "price": q.price, "change": q.change,
            "change_pct": q.change_pct, "name": q.name,
            "pe_ttm": q.pe_ttm, "pb_ratio": q.pb_ratio,
            "market_cap_total": q.market_cap_total,
            "turnover": q.turnover, "volume": q.volume, "turnover_rate": q.turnover_rate,
        }
        for q in quotes
    ]


@app.get("/api/stocks/history/{ticker}")
async def get_stock_history(
    ticker: str,
    period: str = Query("3mo", pattern="^(5d|1mo|3mo|6mo|1y|2y|5y)$"),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        df = get_history(ticker, period=period, interval="1d")
        if df.empty:
            raise HTTPException(status_code=404, detail=f"无历史数据 {ticker}")
        df = df.reset_index()
        # Normalize column names
        col_map = {c.lower(): c for c in df.columns}
        records = []
        for _, row in df.iterrows():
            t = row.get("Date") or row.get("Datetime") or row.get("date")
            ts = str(t)[:10] if t is not None else ""
            records.append({
                "time": ts,
                "open": round(float(row.get("Open", 0) or 0), 3),
                "high": round(float(row.get("High", 0) or 0), 3),
                "low": round(float(row.get("Low", 0) or 0), 3),
                "close": round(float(row.get("Close", 0) or 0), 3),
                "volume": int(row.get("Volume", 0) or 0),
            })
        return records
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"History error {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stocks/intraday/{ticker}")
async def get_stock_intraday(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        df = get_intraday(ticker)
        if df is None or df.empty:
            return []
        df = df.reset_index()
        records = []
        for _, row in df.iterrows():
            t = row.get("Datetime") or row.get("Date") or row.get("datetime")
            ts = str(t)[:16] if t is not None else ""
            records.append({
                "time": ts,
                "open": round(float(row.get("Open", 0) or 0), 3),
                "high": round(float(row.get("High", 0) or 0), 3),
                "low": round(float(row.get("Low", 0) or 0), 3),
                "close": round(float(row.get("Close", 0) or 0), 3),
                "volume": int(row.get("Volume", 0) or 0),
            })
        return records
    except Exception as e:
        logger.error(f"Intraday error {ticker}: {e}")
        return []


# ---------------------------------------------------------------------------
# Strategy & forecast
# ---------------------------------------------------------------------------

@app.get("/api/strategy/{ticker}")
async def get_strategy(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        # recommend_stock fetches history/quote and calls run_all_strategies internally
        rec = await asyncio.to_thread(recommend_stock, ticker)
        if rec.strategies is None:
            raise HTTPException(status_code=500, detail="策略计算失败")
        return _serialize_report(rec.strategies)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Strategy error {ticker}: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


def _serialize_signal(s) -> dict:
    return {
        "name": s.name,
        "signal": s.signal,
        "score": s.score,
        "details": s.details,
        "indicators": s.indicators,
    }


def _serialize_report(report) -> dict:
    return {
        "ticker": report.ticker,
        "name": report.name,
        "strategy1": _serialize_signal(report.strategy1),
        "strategy2": _serialize_signal(report.strategy2),
        "strategy3": _serialize_signal(report.strategy3),
        "combined_score": report.combined_score,
        "composite_action": report.composite_action,
        "hot_sectors": report.hot_sectors,
        "in_hot_sector": report.in_hot_sector,
        "sector": report.sector,
        "northbound_sentiment": report.northbound_sentiment,
        "position_pct": report.position_pct,
        "position_note": report.position_note,
    }


@app.get("/api/forecast/{ticker}")
async def get_forecast(
    ticker: str,
    horizon: str = Query("1d", pattern="^(1h|1d|1w|1m|3m)$"),
    news_sentiment: str = Query("", pattern="^(偏多|偏空|中性|)$"),
    news_score: float = Query(0.0, ge=0, le=100),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        resolved = await asyncio.to_thread(resolve_ticker, ticker)
        quote = await asyncio.to_thread(get_quote, resolved)
        history_period = {"1h": "6mo", "1d": "6mo", "1w": "1y", "1m": "2y", "3m": "2y"}.get(horizon, "6mo")
        history = await asyncio.to_thread(
            get_history, resolved, history_period, "1d"
        )
        intraday = await asyncio.to_thread(get_intraday, resolved)
        fundamentals = await asyncio.to_thread(get_fundamentals, resolved)

        fc = build_short_term_forecast(
            ticker=resolved,
            name=quote.name,
            history=history,
            current_price=quote.price,
            horizon=horizon,
            history_period=history_period,
            intraday=intraday if not intraday.empty else None,
            pe_ratio=fundamentals.get("pe_ratio"),
            profit_margin=fundamentals.get("profit_margin"),
            news_sentiment=news_sentiment,
            news_sentiment_score=news_score,
        )
        return {
            "ticker": fc.ticker,
            "name": fc.name,
            "horizon": fc.horizon,
            "current_price": fc.current_price,
            "predicted_price": fc.predicted_price,
            "predicted_high": fc.predicted_high,
            "predicted_low": fc.predicted_low,
            "trend": fc.trend,
            "confidence": fc.confidence,
            "expected_return_pct": fc.expected_return_pct,
            "expected_range_pct": fc.expected_range_pct,
            "high_time": fc.high_time,
            "low_time": fc.low_time,
            "factors": fc.factors,
            "support_price": fc.support_price,
            "resistance_price": fc.resistance_price,
            "news_sentiment": fc.news_sentiment,
            "news_sentiment_score": fc.news_sentiment_score,
            "quantile_10": fc.quantile_10,
            "quantile_25": fc.quantile_25,
            "quantile_75": fc.quantile_75,
            "quantile_90": fc.quantile_90,
            "scenarios": [
                {
                    "label": s.label,
                    "probability": s.probability,
                    "predicted_price": s.predicted_price,
                    "expected_return_pct": s.expected_return_pct,
                    "description": s.description,
                }
                for s in fc.scenarios
            ],
        }
    except Exception as e:
        logger.error(f"Forecast error {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Company profile — industry, earnings, fundamentals for StockDetail panel
# ---------------------------------------------------------------------------

@app.get("/api/stocks/profile/{ticker}")
async def get_stock_profile(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        profile = await asyncio.to_thread(get_full_stock_profile, ticker)
        return profile
    except Exception as e:
        logger.error(f"Profile error {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Forecast history — save/load/delete prediction records
# ---------------------------------------------------------------------------

@app.get("/api/forecast-history")
async def get_forecast_history(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user_data.load_forecast_history(user["sub"])


@app.post("/api/forecast-history", status_code=201)
async def save_forecast_to_history(
    req: dict,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    return user_data.save_forecast_record(user["sub"], req)


@app.delete("/api/forecast-history/{index}")
async def delete_forecast_from_history(
    index: int,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    return user_data.delete_forecast_record(user["sub"], index)


@app.delete("/api/forecast-history")
async def clear_all_forecast_history(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user_data.clear_forecast_history(user["sub"])


# ---------------------------------------------------------------------------
# App version info
# ---------------------------------------------------------------------------

@app.get("/api/about")
async def get_about():
    return {
        "version": _APP_VERSION,
        "name": "投资大师 (InvestMaster)",
        "description": "基于经验 + AI 开发的智能投资分析工具。联系或建议，请发送邮件到yyjqr789@sina.com",
        "disclaimer": "本软件仅供学习与研究参考，不构成任何投资建议。投资有风险，理财需谨慎。",
        "copyright": "©EANGEL 2024-2026 InvestMaster. All rights reserved.",
    }


# ---------------------------------------------------------------------------
# News / market information endpoints
# ---------------------------------------------------------------------------

def _serialize_news(items) -> list[dict]:
    return [
        {
            "title": it.title,
            "url": it.url,
            "source": it.source,
            "date": it.date,
            "tag": it.tag,
            "summary": it.summary,
            "rating": getattr(it, "rating", ""),
            "rating_change": getattr(it, "rating_change", ""),
            "stock_code": getattr(it, "stock_code", ""),
            "sector": getattr(it, "sector", ""),
        }
        for it in items
        if it.title
    ]


class NewsAnalyzeItem(BaseModel):
    title: str
    url: str = ""
    source: str = ""
    date: str = ""
    tag: str = ""
    summary: str = ""


class NewsAnalyzeRequest(BaseModel):
    category: str = Field("cn", pattern="^(cn|research|global|all)$")
    focus: str = ""
    company: str = ""
    sector: str = ""
    ticker: str = ""
    items: list[NewsAnalyzeItem]


@app.get("/api/news")
async def get_news(
    category: str = Query("cn", pattern="^(cn|research|global|all)$"),
    n: int = Query(15, ge=1, le=30),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Fetch market news / research reports.

    category: cn (A股快讯) | research (研报) | global (国际宏观) | all (合并)
    """
    _get_current_user(_bearer(credentials))
    loop = asyncio.get_event_loop()
    try:
        if category == "all":
            cn = get_cached_news("cn", 10) or await loop.run_in_executor(None, lambda: fetch_cn_news(10))
            res = get_cached_news("research", 5) or await loop.run_in_executor(None, lambda: fetch_research_reports(5))
            gl = get_cached_news("global", 5) or await loop.run_in_executor(None, lambda: fetch_global_events(5))
            items = cn + res + gl
        else:
            items = get_cached_news(category, n)
            if items is None:
                if category == "cn":
                    items = await loop.run_in_executor(None, lambda: fetch_cn_news(n))
                elif category == "research":
                    items = await loop.run_in_executor(None, lambda: fetch_research_reports(n))
                else:  # global
                    items = await loop.run_in_executor(None, lambda: fetch_global_events(n))
        return _serialize_news(items)
    except Exception as e:
        logger.error(f"News fetch error: {e}")
        return []


@app.get("/api/research-reports")
async def get_research_reports(
    ticker: str = Query(""),
    sector: str = Query(""),
    query: str = Query(""),
    limit: int = Query(10, ge=1, le=30),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Research report retrieval with LanceDB + live Eastmoney fallback.

    Precedence: ticker-specific live fetch > sector filter > keyword/text search
    > most recent in store.
    """
    _get_current_user(_bearer(credentials))
    loop = asyncio.get_event_loop()

    async def _live_ticker():
        if not ticker:
            return []
        items = await loop.run_in_executor(
            None, lambda: fetch_research_by_ticker(ticker, n=limit)
        )
        return _serialize_news(items)

    async def _store_query():
        def _query():
            if sector:
                rows = research_store.search_by_sector(sector, limit=limit)
            elif query:
                rows = research_store.search_text(query, limit=limit)
            else:
                rows = research_store.get_recent(limit=limit)
            return rows
        return await loop.run_in_executor(None, _query)

    try:
        if ticker:
            results = await _live_ticker()
            # If live fetch returned nothing, fall back to store
            if not results:
                rows = await _store_query()
                results = [dict(r) for r in rows]
        else:
            rows = await _store_query()
            results = [dict(r) for r in rows]

            # Seed the store on first use if empty
            if not results:
                items = await loop.run_in_executor(None, lambda: fetch_research_reports(limit))
                results = _serialize_news(items)

        return results
    except Exception as e:
        logger.error(f"Research reports error: {e}")
        return []


@app.post("/api/news/analyze")
async def analyze_news(
    req: NewsAnalyzeRequest = Body(...),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    items = [
        item for item in req.items
        if item.title.strip()
    ][:20]
    if not items:
        raise HTTPException(status_code=400, detail="没有可分析的资讯内容")

    try:
        news_items = [
            NewsItem(
                title=item.title.strip(),
                url=item.url,
                source=item.source,
                date=item.date,
                tag=item.tag,
                summary=item.summary,
            )
            for item in items
        ]
        result = analyze_news_briefing(
            news_items,
            category=req.category,
            focus=req.focus,
            company=req.company,
            sector=req.sector,
            ticker=req.ticker,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"News analysis error: {e}")
        raise HTTPException(status_code=500, detail="资讯分析失败，请稍后重试")


# ---------------------------------------------------------------------------
# Saved articles (bookmarks)
# ---------------------------------------------------------------------------

class SaveArticleRequest(BaseModel):
    title: str
    url: str = ""
    source: str = ""
    date: str = ""
    tag: str = ""
    summary: str = ""
    category: str = ""  # cn | research | global


@app.get("/api/saved-articles")
async def get_saved_articles(credentials: Optional[HTTPAuthorizationCredentials] = security):
    """Return all saved/bookmarked articles for the current user."""
    user = _get_current_user(_bearer(credentials))
    return user_data.load_saved_articles(user["sub"])


@app.post("/api/saved-articles", status_code=201)
async def add_saved_article(
    req: SaveArticleRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Bookmark a news/research article."""
    user = _get_current_user(_bearer(credentials))
    return user_data.save_article(user["sub"], req.model_dump())


@app.delete("/api/saved-articles/{index}")
async def delete_saved_article(
    index: int,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Delete a saved article by its list index."""
    user = _get_current_user(_bearer(credentials))
    return user_data.delete_saved_article(user["sub"], index)


@app.delete("/api/saved-articles")
async def clear_saved_articles(credentials: Optional[HTTPAuthorizationCredentials] = security):
    """Clear all saved articles."""
    user = _get_current_user(_bearer(credentials))
    return user_data.clear_saved_articles(user["sub"])


# ---------------------------------------------------------------------------
# Watchlist endpoints
# ---------------------------------------------------------------------------

class WatchlistAddRequest(BaseModel):
    ticker: str
    name: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------------------

@app.get("/api/sectors")
async def get_sectors(credentials: Optional[HTTPAuthorizationCredentials] = security):
    _get_current_user(_bearer(credentials))
    loop = asyncio.get_event_loop()
    from invest_app.industry import get_sectors_with_market_data
    return await loop.run_in_executor(None, get_sectors_with_market_data)


@app.get("/api/industry-picks")
async def get_industry_picks_api(
    picks: int = Query(5, ge=3, le=10),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    from invest_app.industry_picks import get_industry_picks
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(get_industry_picks, picks_per_industry=picks),
            timeout=28.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="行业数据加载超时，东财接口响应缓慢，请稍后重试")


@app.get("/api/indices")
async def get_indices(credentials: Optional[HTTPAuthorizationCredentials] = security):
    _get_current_user(_bearer(credentials))
    from invest_app.industry import get_major_indices
    return await asyncio.to_thread(get_major_indices)


# ---------------------------------------------------------------------------
# Smart Picks endpoints
# ---------------------------------------------------------------------------

@app.get("/api/smart-picks")
async def get_smart_picks_api(
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    from invest_app.smart_picks import get_smart_picks
    try:
        state = await asyncio.wait_for(
            asyncio.to_thread(get_smart_picks, False),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="智能精选超时，请稍候重试")
    from dataclasses import asdict
    return asdict(state)


@app.post("/api/smart-picks/refresh")
async def refresh_smart_picks_api(
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    from invest_app.smart_picks import get_smart_picks
    try:
        state = await asyncio.wait_for(
            asyncio.to_thread(get_smart_picks, True),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="强制刷新超时，请稍候重试")
    from dataclasses import asdict
    return asdict(state)


@app.get("/api/watchlist")
async def get_watchlist(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user_data.load_watchlist(user["sub"])


@app.post("/api/watchlist", status_code=201)
async def add_to_watchlist(
    req: WatchlistAddRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    # Try to resolve name if not provided
    name = req.name
    if not name:
        try:
            q = get_quote(req.ticker)
            name = q.name if q else req.ticker
        except Exception:
            name = req.ticker
    items = user_data.add_watchlist_item(user["sub"], req.ticker, name, req.note)
    return items


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    items = user_data.remove_watchlist_item(user["sub"], ticker)
    return items


# ---------------------------------------------------------------------------
# Monitor list endpoints
# ---------------------------------------------------------------------------

class MonitorUpdateRequest(BaseModel):
    tickers: list[str]


@app.get("/api/monitor")
async def get_monitor(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user_data.load_monitor_list(user["sub"])


@app.put("/api/monitor")
async def update_monitor(
    req: MonitorUpdateRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    user_data.save_monitor_list(user["sub"], req.tickers)
    return req.tickers[:10]


# ---------------------------------------------------------------------------
# Price alert endpoints
# ---------------------------------------------------------------------------

class AlertSetRequest(BaseModel):
    alert_high: Optional[float] = None
    alert_low: Optional[float] = None


@app.get("/api/alerts")
async def get_alerts(credentials: Optional[HTTPAuthorizationCredentials] = security):
    """Return all price alert rules for the current user."""
    user = _get_current_user(_bearer(credentials))
    return user_data.load_alerts(user["sub"])


@app.put("/api/alerts/{ticker}")
async def set_price_alert(
    ticker: str,
    req: AlertSetRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Create or update a price alert rule for one ticker."""
    user = _get_current_user(_bearer(credentials))
    return user_data.set_alert(user["sub"], ticker, req.alert_high, req.alert_low)


@app.delete("/api/alerts/{ticker}")
async def delete_price_alert(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Delete the price alert rule for one ticker."""
    user = _get_current_user(_bearer(credentials))
    return user_data.delete_alert(user["sub"], ticker)


# ---------------------------------------------------------------------------
# Portfolio endpoints
# ---------------------------------------------------------------------------

@app.get("/api/portfolio")
async def get_portfolio(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)
    return json.loads(portfolio.model_dump_json())


# -- Portfolio mutations ----------------------------------------------------

class AddHoldingRequest(BaseModel):
    ticker: str
    shares: float
    cost_price: float
    asset_type: str = "stock"


@app.post("/api/portfolio/add")
async def api_add_holding(
    req: AddHoldingRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Add a single holding to the portfolio."""
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)

    from invest_app.models import AssetType as AT
    at = AT.MUTUAL_FUND if req.asset_type == "mutual_fund" else (
        AT.ETF if req.asset_type == "etf" else AT.STOCK
    )
    from invest_app.broker_import import normalise_ticker
    ticker = normalise_ticker(req.ticker, at)
    add_holding(portfolio, ticker, req.shares, req.cost_price, at)
    save_portfolio(portfolio, path)
    return {"ok": True, "ticker": ticker}


@app.delete("/api/portfolio/{ticker:path}")
async def api_delete_holding(
    ticker: str,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Remove a holding entirely from the portfolio."""
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)
    ticker_upper = ticker.upper()
    if ticker_upper not in portfolio.holdings:
        raise HTTPException(404, f"持仓中未找到 {ticker_upper}")
    del portfolio.holdings[ticker_upper]
    save_portfolio(portfolio, path)
    return {"ok": True}


class SellHoldingRequest(BaseModel):
    shares: float
    price: float


@app.post("/api/portfolio/{ticker}/sell")
async def api_sell_holding(
    ticker: str,
    req: SellHoldingRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Sell (reduce) shares of a holding and record the transaction."""
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)
    ticker_upper = ticker.upper()
    if ticker_upper not in portfolio.holdings:
        raise HTTPException(404, f"持仓中未找到 {ticker_upper}")
    try:
        result = remove_holding(portfolio, ticker_upper, req.shares, req.price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    save_portfolio(portfolio, path)
    return {"ok": True, "remaining_shares": result.shares if result else 0}


class EditHoldingRequest(BaseModel):
    shares: float
    avg_cost_basis: float


@app.patch("/api/portfolio/{ticker}/edit")
async def api_edit_holding(
    ticker: str,
    req: EditHoldingRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Directly set shares and avg_cost_basis (manual correction, no transaction recorded)."""
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)
    ticker_upper = ticker.upper()
    if ticker_upper not in portfolio.holdings:
        raise HTTPException(404, f"持仓中未找到 {ticker_upper}")
    if req.shares <= 0:
        raise HTTPException(400, "持仓数量必须大于 0，清空请使用删除功能")
    holding = portfolio.holdings[ticker_upper]
    holding.shares = req.shares
    holding.avg_cost_basis = req.avg_cost_basis
    save_portfolio(portfolio, path)
    return {"ok": True}


# -- Broker import ----------------------------------------------------------

@app.get("/api/portfolio/export/csv")
async def export_portfolio_csv(
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Export portfolio holdings as CSV with live quotes for current P&L."""
    from fastapi.responses import Response
    from invest_app.market_data import get_quote

    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])
    portfolio = load_portfolio(path)

    rows = ["股票代码,股票名称,持股数量,成本均价,当前价,市值,盈亏金额,盈亏比例%,资产类型"]
    for ticker, h in portfolio.holdings.items():
        try:
            q = get_quote(ticker)
            cur_price = q.price
            name = q.name or ticker
        except Exception:
            cur_price = h.avg_cost_basis
            name = ticker

        market_value = round(h.shares * cur_price, 2)
        cost_value   = round(h.shares * h.avg_cost_basis, 2)
        pnl_amt      = round(market_value - cost_value, 2)
        pnl_pct      = round((cur_price - h.avg_cost_basis) / h.avg_cost_basis * 100, 2) if h.avg_cost_basis else 0.0

        rows.append(
            f"{ticker},{name},{h.shares},{h.avg_cost_basis},"
            f"{cur_price},{market_value},{pnl_amt},{pnl_pct},{h.asset_type.value}"
        )

    csv_body = "\n".join(rows)
    return Response(
        content=csv_body.encode("utf-8-sig"),   # utf-8-sig = Excel BOM
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="portfolio.csv"',
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/portfolio/brokers")
async def list_brokers():
    """Return list of supported brokers with import instructions."""
    return BROKER_INFO


class ImportPreviewRequest(BaseModel):
    broker: str
    content: str  # file content as text


@app.post("/api/portfolio/import/preview")
async def import_preview(
    req: ImportPreviewRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Parse an uploaded file and return a preview of holdings to import."""
    _get_current_user(_bearer(credentials))  # auth check

    parser = BROKER_PARSERS.get(req.broker)
    if not parser:
        raise HTTPException(400, f"不支持的券商: {req.broker}")

    result = parser(req.content)
    return {
        "broker": result.broker,
        "raw_rows": result.raw_rows,
        "holdings": [
            {
                "ticker": h.ticker,
                "name": h.name,
                "shares": h.shares,
                "cost_price": h.cost_price,
                "current_price": h.current_price,
                "market_value": h.market_value,
                "pnl": h.pnl,
                "asset_type": h.asset_type.value,
            }
            for h in result.holdings
        ],
        "errors": result.errors,
    }


class ImportExecuteRequest(BaseModel):
    broker: str
    content: str
    mode: str = "merge"  # merge | replace


@app.post("/api/portfolio/import")
async def import_execute(
    req: ImportExecuteRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Parse and merge imported holdings into the user portfolio."""
    user = _get_current_user(_bearer(credentials))
    path = user_data.get_portfolio_path(user["sub"])

    parser = BROKER_PARSERS.get(req.broker)
    if not parser:
        raise HTTPException(400, f"不支持的券商: {req.broker}")

    result = parser(req.content)
    if not result.holdings:
        raise HTTPException(400, "未解析到有效持仓数据")

    portfolio = load_portfolio(path)
    summary = merge_into_portfolio(portfolio, result.holdings, mode=req.mode, broker_tag=result.broker)
    save_portfolio(portfolio, path)

    return {
        "ok": True,
        "summary": summary,
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsUpdateRequest(BaseModel):
    theme: Optional[str] = None
    refresh_interval: Optional[int] = None
    price_alert: Optional[bool] = None
    # API keys — stored globally in api_keys.json (not per-user), masked on read
    alpha_vantage_key: Optional[str] = None
    tavily_key: Optional[str] = None
    qveris_key: Optional[str] = None


@app.get("/api/settings")
async def get_settings(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    s = user_data.load_settings(user["sub"])
    # Mask stored API keys for security: return only presence indicator
    from invest_app.config import get_alpha_vantage_key, get_tavily_key, get_qveris_key
    s["has_alpha_vantage_key"] = bool(get_alpha_vantage_key())
    s["has_tavily_key"] = bool(get_tavily_key())
    s["has_qveris_key"] = bool(get_qveris_key())
    return s


@app.put("/api/settings")
async def update_settings(
    req: SettingsUpdateRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    updates = {k: v for k, v in req.model_dump().items()
               if v is not None and not k.endswith("_key")}
    user_data.save_settings(user["sub"], updates)

    # Persist API keys globally (empty string = clear the key)
    key_map = {
        "alpha_vantage_key": "alpha_vantage",
        "tavily_key": "tavily",
        "qveris_key": "qveris",
    }
    api_updates = {}
    for field, json_field in key_map.items():
        val = getattr(req, field)
        if val is not None:
            api_updates[json_field] = val
    if api_updates:
        save_api_keys(**api_updates)

    return await get_settings(credentials)


# ---------------------------------------------------------------------------
# WebSocket — realtime price stream
# ---------------------------------------------------------------------------

class _PriceStreamManager:
    """Manages WebSocket connections and broadcasts price updates."""

    def __init__(self):
        # conn_id → (websocket, tickers, stale_cache)
        # stale_cache: bare_code → last known quote dict; serves data when batch fetch misses
        self._connections: dict[str, tuple[WebSocket, list[str], dict[str, dict]]] = {}

    async def connect(self, ws: WebSocket, conn_id: str, tickers: list[str]):
        await ws.accept()
        self._connections[conn_id] = (ws, tickers, {})
        logger.info(f"WS connected: {conn_id}, tickers={tickers}")

    def disconnect(self, conn_id: str):
        self._connections.pop(conn_id, None)
        logger.info(f"WS disconnected: {conn_id}")

    def update_tickers(self, conn_id: str, tickers: list[str]):
        if conn_id in self._connections:
            ws, _, stale_cache = self._connections[conn_id]
            self._connections[conn_id] = (ws, tickers, stale_cache)

    async def push_prices(self, conn_id: str):
        if conn_id not in self._connections:
            return
        ws, tickers, stale_cache = self._connections[conn_id]
        if not tickers:
            return
        try:
            # Always use batch quotes — Tencent returns last-close prices even during
            # non-trading hours (weekends, evenings), so there is no need to skip this
            # call and fall back to slow per-ticker get_history() calls.
            try:
                realtime_quotes = await asyncio.wait_for(
                    asyncio.to_thread(get_batch_quotes_tencent, tickers),
                    timeout=8.0,
                )
            except (asyncio.TimeoutError, Exception):
                realtime_quotes = []

            realtime_map = {
                q.ticker.split(".", maxsplit=1)[0]: q for q in realtime_quotes
            }

            data: list[dict] = []
            missing_tickers: list[str] = []

            for ticker in tickers:
                key = ticker.split(".", maxsplit=1)[0]
                q = realtime_map.get(key)
                if q is not None:
                    entry = {
                        "ticker": q.ticker,
                        "price": q.price,
                        "change": q.change,
                        "change_pct": q.change_pct,
                        "name": q.name,
                        "turnover": q.turnover,
                        "volume": q.volume,
                    }
                    stale_cache[key] = entry  # keep stale cache fresh
                    data.append(entry)
                elif key in stale_cache:
                    # Serve last-known data with stale indicator rather than blocking
                    stale_entry = dict(stale_cache[key])
                    stale_entry["stale"] = True
                    data.append(stale_entry)
                else:
                    missing_tickers.append(ticker)

            # For tickers with no cached data at all, fetch individually in parallel
            if missing_tickers:
                async def _fetch_one(t: str) -> dict | None:
                    try:
                        q = await asyncio.wait_for(
                            asyncio.to_thread(get_quote, t),
                            timeout=5.0,
                        )
                        return {
                            "ticker": q.ticker,
                            "price": q.price,
                            "change": q.change,
                            "change_pct": q.change_pct,
                            "name": q.name,
                            "turnover": q.turnover,
                            "volume": q.volume,
                        }
                    except Exception:
                        return None

                individual = await asyncio.gather(*[_fetch_one(t) for t in missing_tickers])
                for t, entry in zip(missing_tickers, individual):
                    if entry is not None:
                        key = t.split(".", maxsplit=1)[0]
                        stale_cache[key] = entry
                        data.append(entry)

            if not data:
                return

            await ws.send_json({"type": "prices", "data": data})
        except Exception as e:
            logger.warning(f"Push error {conn_id}: {e}")


_manager = _PriceStreamManager()


@app.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    import uuid
    conn_id = str(uuid.uuid4())

    async def _safe_close(code: int = 1000) -> None:
        """Close the WebSocket, silently ignoring already-closed state."""
        try:
            await websocket.close(code=code)
        except Exception:
            pass

    # ── Handshake: accept + authenticate ────────────────────────────────────
    await websocket.accept()
    try:
        init_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        init = json.loads(init_text)
        token = init.get("token", "")
        payload = decode_token(token)
        if not payload:
            await websocket.send_json({"type": "error", "message": "认证失败"})
            await _safe_close(code=1008)
            return
        tickers: list[str] = init.get("tickers", [])[:20]
    except WebSocketDisconnect:
        # Client disconnected before auth completed — nothing to close
        return
    except Exception:
        await _safe_close()
        return

    _manager._connections[conn_id] = (websocket, tickers, {})
    logger.info(f"WS auth ok: {payload.get('sub')}, tickers={tickers}")

    try:
        # Start push loop
        async def _push_loop():
            while conn_id in _manager._connections:
                await _manager.push_prices(conn_id)
                await asyncio.sleep(5)

        push_task = asyncio.create_task(_push_loop())

        # Listen for ticker updates from client
        while True:
            try:
                msg_text = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                msg = json.loads(msg_text)
                if msg.get("type") == "update_tickers":
                    new_tickers = msg.get("tickers", [])[:20]
                    _manager.update_tickers(conn_id, new_tickers)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            except Exception:
                break

        push_task.cancel()
    finally:
        _manager.disconnect(conn_id)


# ---------------------------------------------------------------------------
# Agent endpoints (Fin-Agent with red-blue adversarial)
# ---------------------------------------------------------------------------

@app.get("/api/agent/analyze/{ticker}")
async def agent_analyze(
    ticker: str,
    horizon: str = "1d",
    period: str = "6mo",
    include_news: bool = True,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Run the full Fin-Agent pipeline for a ticker.

    Returns the complete analysis including:
      - Bull/bear cases (red-blue adversarial)
      - Judge verdict (reconciled decision)
      - Risk budget & position sizing
      - Validation status
      - Prediction tracking
    """
    _get_current_user(_bearer(credentials))

    from invest_app.agent_graph import run_agent_pipeline

    state = await asyncio.to_thread(
        run_agent_pipeline,
        ticker,
        horizon=horizon,
        period=period,
        include_news=include_news,
    )

    # Strip non-serializable fields (pd.DataFrame)
    md = dict(state.get("market_data", {}))
    md.pop("history", None)
    md.pop("intraday", None)

    return {
        "ticker": state.get("ticker"),
        "horizon": state.get("horizon"),
        "market_data": md,
        "forecast": state.get("forecast"),
        "strategies": state.get("strategies"),
        "recommendation": state.get("recommendation"),
        "search_news": state.get("search_news", []),
        "search_announcements": state.get("search_announcements", []),
        "search_events": state.get("search_events", []),
        "bull_case": state.get("bull_case"),
        "bear_case": state.get("bear_case"),
        "judge_verdict": state.get("judge_verdict"),
        "portfolio_context": state.get("portfolio_context"),
        "risk_budget": state.get("risk_budget"),
        "validation": state.get("validation"),
        "notes": state.get("notes", []),
        "errors": state.get("errors", []),
        "meta": state.get("meta", {}),
    }


@app.get("/api/agent/verdict/{ticker}")
async def agent_verdict(
    ticker: str,
    horizon: str = "1d",
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Quick verdict: just the judge's decision + risk budget (lighter than full analyze)."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_graph import run_agent_pipeline

    state = await asyncio.to_thread(
        run_agent_pipeline,
        ticker,
        horizon=horizon,
        include_news=False,  # faster without news search
    )

    return {
        "ticker": state.get("ticker"),
        "judge_verdict": state.get("judge_verdict"),
        "risk_budget": state.get("risk_budget"),
        "bull_score": (state.get("bull_case") or {}).get("score"),
        "bear_score": (state.get("bear_case") or {}).get("score"),
        "validation": state.get("validation"),
        "errors": state.get("errors", []),
    }


@app.get("/api/agent/accuracy")
async def agent_accuracy(
    ticker: str | None = None,
    last_n: int = 100,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Get prediction accuracy statistics."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_backtest import get_accuracy_stats
    return await asyncio.to_thread(get_accuracy_stats, ticker, last_n)


@app.get("/api/agent/predictions")
async def agent_predictions(
    ticker: str | None = None,
    limit: int = 20,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Get recent prediction records."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_backtest import get_recent_predictions
    return await asyncio.to_thread(get_recent_predictions, ticker, limit)


@app.post("/api/agent/check-predictions")
async def agent_check_predictions(
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Check all pending predictions against current prices."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_backtest import check_predictions
    results = await asyncio.to_thread(check_predictions)
    return {"checked": len(results), "results": results}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    # build=1 是版本标记，Tauri 启动时探活时会检查此字段。
    # 如果运行中的服务器没有此标记（旧版孤儿进程），Tauri 会将其杀掉并重启。
    return {"status": "ok", "build": 1}


@app.get("/api/debug/paths")
async def debug_paths():
    """返回运行时路径信息，用于诊断打包版数据目录问题。"""
    import os
    from pathlib import Path
    from invest_app.config import DATA_DIR
    home = Path.home()
    return {
        "home": str(home),
        "data_dir": str(DATA_DIR),
        "data_dir_exists": DATA_DIR.exists(),
        "userprofile_env": os.environ.get("USERPROFILE", ""),
        "homepath_env": os.environ.get("HOMEPATH", ""),
        "homedrive_env": os.environ.get("HOMEDRIVE", ""),
        "users_dir_exists": (DATA_DIR / "users").exists(),
        "users_subdirs": [str(p.name) for p in (DATA_DIR / "users").iterdir()] if (DATA_DIR / "users").exists() else [],
    }


# ---------------------------------------------------------------------------
# Agent endpoints — Fin-Agent red-blue adversarial pipeline
# ---------------------------------------------------------------------------

@app.get("/api/agent/analyze/{ticker}")
async def agent_analyze(
    ticker: str,
    horizon: str = "1d",
    period: str = "6mo",
    include_news: bool = True,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Run the full Fin-Agent pipeline with red-blue adversarial analysis."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_graph import run_agent_pipeline

    state = await asyncio.to_thread(
        run_agent_pipeline,
        ticker,
        horizon=horizon,
        period=period,
        include_news=include_news,
    )

    # Strip non-serializable data (pd.DataFrame)
    md = dict(state.get("market_data", {}))
    md.pop("history", None)
    md.pop("intraday", None)

    return {
        "ticker": state.get("ticker"),
        "market_data": md,
        "forecast": state.get("forecast"),
        "strategies": state.get("strategies"),
        "recommendation": state.get("recommendation"),
        "bull_case": state.get("bull_case"),
        "bear_case": state.get("bear_case"),
        "judge_verdict": state.get("judge_verdict"),
        "portfolio_context": state.get("portfolio_context"),
        "risk_budget": state.get("risk_budget"),
        "validation": state.get("validation"),
        "search_news": state.get("search_news", []),
        "search_announcements": state.get("search_announcements", []),
        "search_events": state.get("search_events", []),
        "errors": state.get("errors", []),
        "notes": state.get("notes", []),
        "meta": state.get("meta", {}),
    }


@app.get("/api/agent/predictions")
async def agent_predictions(
    ticker: str = "",
    limit: int = 20,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Get recent agent predictions with accuracy tracking."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_backtest import get_recent_predictions, get_accuracy_stats

    predictions = get_recent_predictions(ticker=ticker or None, limit=limit)
    stats = get_accuracy_stats(ticker=ticker or None)
    return {"predictions": predictions, "stats": stats}


@app.post("/api/agent/check-predictions")
async def agent_check_predictions(
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    """Check pending predictions against current prices."""
    _get_current_user(_bearer(credentials))

    from invest_app.agent_backtest import check_predictions

    checked = await asyncio.to_thread(check_predictions)
    return {"checked": len(checked), "results": checked[-10:]}


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # 检测 httptools 原生扩展是否完整（打包时若 .pyd 被删但 stub 残留，
    # uvicorn 会选 httptools_impl 并在运行时抛 AttributeError）。
    _http_impl: str
    try:
        import httptools as _ht
        _ht.HttpRequestParser  # noqa: B018 – 触发 AttributeError 若 pyd 缺失
        _http_impl = "httptools"
    except (ImportError, AttributeError):
        _http_impl = "h11"
    uvicorn.run("invest_app.server:app", host="127.0.0.1", port=8765, reload=False, http=_http_impl)
