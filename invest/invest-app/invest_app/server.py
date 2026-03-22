"""FastAPI server — main entry point for the invest-app backend.

Exposes REST + WebSocket APIs consumed by the React/Tauri frontend.

Endpoints:
  POST  /api/auth/login          — username/password → JWT
  POST  /api/auth/register       — create new user
  GET   /api/auth/me             — current user info
  GET   /api/stocks/search       — search instruments
  GET   /api/stocks/quote/{t}    — single quote
  POST  /api/stocks/batch-quotes — batch quotes (up to 10)
  GET   /api/stocks/history/{t}  — OHLCV history
  GET   /api/stocks/intraday/{t} — intraday 1-min data
  GET   /api/strategy/{ticker}   — run all 3 strategies
  GET   /api/forecast/{ticker}   — short-term forecast
  GET   /api/watchlist           — user watchlist
  POST  /api/watchlist           — add item
  DELETE/api/watchlist/{ticker}  — remove item
  GET   /api/monitor             — user monitor list (≤10)
  PUT   /api/monitor             — update monitor list
  GET   /api/portfolio           — user portfolio
  GET   /api/settings            — user settings
  PUT   /api/settings            — update settings
  WS    /ws/prices               — realtime price stream (5s push)
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, time
from contextlib import asynccontextmanager
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

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
from invest_app import user_data

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
    tickers = req.tickers[:10]
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


@app.get("/api/forecast/{ticker}")
async def get_forecast(
    ticker: str,
    horizon: str = Query("1d", pattern="^(1h|1d)$"),
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    _get_current_user(_bearer(credentials))
    try:
        fc = build_short_term_forecast(ticker, horizon=horizon)  # type: ignore
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
        }
    except Exception as e:
        logger.error(f"Forecast error {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _serialize_report(report) -> dict:
    def _sig(s) -> dict:
        return {
            "name": s.name, "signal": s.signal, "score": s.score,
            "details": s.details, "indicators": {
                k: (float(v) if isinstance(v, float) else v)
                for k, v in s.indicators.items()
            },
        }
    return {
        "ticker": report.ticker,
        "name": report.name,
        "strategy1": _sig(report.strategy1),
        "strategy2": _sig(report.strategy2),
        "strategy3": _sig(report.strategy3),
        "combined_score": report.combined_score,
        "composite_action": report.composite_action,
        "hot_sectors": report.hot_sectors,
        "in_hot_sector": report.in_hot_sector,
        "sector": report.sector,
        "northbound_sentiment": report.northbound_sentiment,
        "position_pct": report.position_pct,
        "position_note": report.position_note,
    }


# ---------------------------------------------------------------------------
# Watchlist endpoints
# ---------------------------------------------------------------------------

class WatchlistAddRequest(BaseModel):
    ticker: str
    name: str = ""
    note: str = ""


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


# -- Broker import ----------------------------------------------------------

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


@app.get("/api/settings")
async def get_settings(credentials: Optional[HTTPAuthorizationCredentials] = security):
    user = _get_current_user(_bearer(credentials))
    return user_data.load_settings(user["sub"])


@app.put("/api/settings")
async def update_settings(
    req: SettingsUpdateRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = security,
):
    user = _get_current_user(_bearer(credentials))
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    user_data.save_settings(user["sub"], updates)
    return user_data.load_settings(user["sub"])


# ---------------------------------------------------------------------------
# WebSocket — realtime price stream
# ---------------------------------------------------------------------------

class _PriceStreamManager:
    """Manages WebSocket connections and broadcasts price updates."""

    def __init__(self):
        self._connections: dict[str, tuple[WebSocket, list[str]]] = {}

    async def connect(self, ws: WebSocket, conn_id: str, tickers: list[str]):
        await ws.accept()
        self._connections[conn_id] = (ws, tickers)
        logger.info(f"WS connected: {conn_id}, tickers={tickers}")

    def disconnect(self, conn_id: str):
        self._connections.pop(conn_id, None)
        logger.info(f"WS disconnected: {conn_id}")

    def update_tickers(self, conn_id: str, tickers: list[str]):
        if conn_id in self._connections:
            ws = self._connections[conn_id][0]
            self._connections[conn_id] = (ws, tickers)

    async def push_prices(self, conn_id: str):
        if conn_id not in self._connections:
            return
        ws, tickers = self._connections[conn_id]
        if not tickers:
            return
        try:
            use_close_fallback_only = not _is_cn_market_open()

            realtime_quotes = []
            if not use_close_fallback_only:
                realtime_quotes = await asyncio.to_thread(get_batch_quotes_tencent, tickers)

            realtime_map = {
                q.ticker.split(".", maxsplit=1)[0]: q for q in realtime_quotes
            }

            data = []
            for ticker in tickers:
                key = ticker.split(".", maxsplit=1)[0]
                q = realtime_map.get(key)
                if q is not None:
                    data.append(
                        {
                            "ticker": q.ticker,
                            "price": q.price,
                            "change": q.change,
                            "change_pct": q.change_pct,
                            "name": q.name,
                            "turnover": q.turnover,
                            "volume": q.volume,
                        }
                    )
                    continue

                close_quote = await asyncio.to_thread(_quote_from_last_close, ticker)
                if close_quote is not None:
                    data.append(close_quote)

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
    tickers: list[str] = []

    # Expect first message: {"token": "...", "tickers": [...]}
    await websocket.accept()
    try:
        init_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        init = json.loads(init_text)
        token = init.get("token", "")
        payload = decode_token(token)
        if not payload:
            await websocket.send_json({"type": "error", "message": "认证失败"})
            await websocket.close()
            return
        tickers = init.get("tickers", [])[:10]
    except Exception:
        await websocket.close()
        return

    _manager._connections[conn_id] = (websocket, tickers)
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
                    new_tickers = msg.get("tickers", [])[:10]
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
    return {"status": "ok"}


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
    uvicorn.run("invest_app.server:app", host="127.0.0.1", port=8765, reload=False)
