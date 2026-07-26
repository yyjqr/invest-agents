# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**投资大师 (InvestMaster)** — a desktop stock investment analysis application. It provides real-time A-share (China) and global market data, portfolio tracking, strategy recommendations, short-term price forecasting, and an AI agent pipeline with adversarial (bull/bear) analysis.

The app has three layers:
- **Python backend** (`invest-app/`) — CLI, FastAPI server, and Dash web dashboard
- **Rust/Tauri shell** (`src-tauri/`) — native Windows desktop wrapper
- **React/TypeScript frontend** (`ui/`) — Tauri-rendered UI (Vite + Tailwind + lightweight-charts)

## Development commands

### Environment setup (Windows, one-time)

```powershell
.\setup-env.ps1     # Creates Python .venv, installs Rust toolchain + Node.js dependencies
```

### Daily development

```powershell
.\dev-start.ps1     # Launches FastAPI backend (port 8765) + Vite dev server (5173) + Tauri dev window
```

The default admin login is `admin` / `admin123`.

### Manual backend start

```bash
# From invest-app/
source .venv/Scripts/activate
uvicorn invest_app.server:app --host 127.0.0.1 --port 8765 --reload
```

### CLI (from invest-app/ with venv active)

```bash
invest quote 600519          # Get a quote
invest portfolio             # View portfolio
invest recommend 600519      # Buy/sell zone recommendation
invest batch 600519 002600   # Batch-analyse up to 10 tickers
invest forecast 600519       # Short-term price forecast
invest web --port 8050       # Dash web dashboard
```

### Testing & linting

```bash
cd invest-app
pytest                                    # Run all tests
pytest tests/test_market_data.py -v       # Run a single test file
ruff check invest_app/                    # Lint (E, F, I, W, line-length 100, py310+)
```

### Production build

```powershell
.\build-prod.ps1            # Full Tauri MSI installer build
.\build-prod.ps1 -SkipBuild # Prepare Python embeddable env only (debug packaging)
```

`build-prod.ps1` downloads a self-contained Python embeddable distribution into `invest-app/python/`, installs only the server dependencies (`requirements-server.txt`), then runs `cargo tauri build`.

## Architecture

### Python package (`invest_app/`) — layer map

| Layer | Modules | Purpose |
|-------|---------|---------|
| Data | `market_data.py`, `news.py`, `industry.py`, `fundamentals` | External data: yfinance + Tencent API |
| Models | `models.py` | Pydantic models: `Portfolio`, `Holding`, `Transaction`, `AssetType` |
| Storage | `portfolio.py`, `config.py`, `cache.py`, `history.py`, `watchlist.py`, `user_data.py`, `order_store.py`, `research_store.py` | JSON-file persistence under `~/invest-app-data/` |
| Analysis | `recommendation.py`, `strategies.py`, `forecast.py`, `analysis.py`, `advice.py`, `forecast_monitor.py` | Technical analysis, trend/valuation signals, short-term forecasting |
| Agent | `agent_graph.py`, `agent_state.py`, `agent_tools.py`, `agent_search.py`, `agent_risk.py`, `agent_backtest.py` | LangGraph red-blue adversarial pipeline |
| Auth | `auth.py` | JWT auth with per-user data isolation |
| Transport | `server.py`, `cli.py`, `webapp.py`, `trading_gateway.py` | Three entry points: FastAPI REST+WS, Click CLI, Dash dashboard |
| Integration | `broker_import.py`, `qveris.py`, `industry_picks.py`, `smart_picks.py` | Broker CSV import, AI API, curated stock picks |

### Two Python dependency sets

- **`requirements.txt`** — full: CLI (`click`, `rich`), Dash dashboard (`dash`, `plotly`), charts (`matplotlib`), server (`fastapi`, `uvicorn`), agent (`langgraph`)
- **`requirements-server.txt`** — minimal: only `fastapi`, `uvicorn`, `yfinance`, `pandas`, `numpy`, `pydantic`, `langgraph` (and multipart). This is what gets bundled into the Tauri production installer via the embeddable Python — no CLI/dashboard/chart libraries.

When adding a dependency needed by the server at runtime, update **both** files.

### Market data sources

The code uses two data providers:
1. **Yahoo Finance** (`yfinance`) — global stocks, historical OHLCV, fundamentals. Each call is wrapped in a `ThreadPoolExecutor` with an 8-second timeout.
2. **Tencent API** (`qt.gtimg.cn`) — Chinese A-shares with richer intraday data (PE TTM, PB, turnover rate, market cap). Used by `get_quote()`, `get_batch_quotes_tencent()`, and `search_instruments()`.

Ticker resolution (`resolve_ticker()`) auto-maps bare 6-digit codes to the correct exchange suffix (`.SS` for Shanghai, `.SZ` for Shenzhen, etc.).

### FastAPI server (`server.py`)

The server is the backend for the Tauri/React desktop frontend. Key design decisions:
- **Auth**: JWT via `python-multipart` + custom `auth.py`. `ensure_default_admin()` creates an `admin/admin123` user on first launch. All non-auth endpoints require a Bearer token.
- **WebSocket** (`/ws/prices`): pushes live prices every 5 seconds via Tencent batch quotes. Client first sends a JSON auth message `{"token": "...", "tickers": [...]}`, then optionally `{"type": "update_tickers", "tickers": [...]}` to change the watch list.
- **Health check** (`/health`): returns `{"status": "ok", "build": 1}`. Tauri's Rust process polls this at startup; if it fails, Tauri kills the old Python process and restarts it.
- **CORS**: wide open (`allow_origins=["*"]`) because this is a local desktop app.

### LangGraph agent pipeline

In `agent_graph.py`, the Fin-Agent follows a fixed DAG:
1. `load_market_data` — fetch quotes, history, intraday, fundamentals
2. `load_portfolio` — load user holdings for context
3. `run_compute` — run forecast + recommendation + all 3 strategies
4. `run_search` — search news, announcements, events (Perplexica-style)
5. `bull_case ∥ bear_case` — two parallel adversarial nodes generate opposing arguments
6. `judge` — reconciles bull/bear into a final verdict with confidence
7. `risk_budget` — position sizing and risk allocation
8. `validate` — multi-round validation; passes through or retries from `run_compute`

Agent tools are defined in `agent_tools.py`, state schema in `agent_state.py`, and backtest accuracy tracking in `agent_backtest.py`.

### Frontend (`ui/`)

- **Framework**: React 18 + TypeScript + Vite
- **Styling**: Tailwind CSS 3
- **Charts**: lightweight-charts (TradingView library) for K-line/candlestick rendering
- **State management**: Zustand
- **Routing**: React Router v6
- **Desktop**: Tauri v2 with plugins for notifications, shell, and persistent store

### Tauri/Rust layer (`src-tauri/`)

- `lib.rs` — app setup, sidecar management, Python server lifecycle
- `main.rs` — entry point
- The Tauri app spawns the Python FastAPI server as a background process on launch and kills it on exit
- Production builds bundle an embeddable Python distribution (`invest-app/python/`) so users don't need Python installed

## Data storage

All persistent data lives under `~/invest-app-data/` (`DATA_DIR` in `config.py`):
- `portfolio.json` — default portfolio (shared/legacy)
- `api_keys.json` — Alpha Vantage / Tavily / Qveris API keys
- `users/<username>/` — per-user: `portfolio.json`, `settings.json`, `watchlist.json`, `monitor.json`, `alerts.json`, `forecast_history.json`, `saved_articles.json`

## China A-share ticker conventions

- Shanghai: `600519.SS` (bare `600519` auto-resolved)
- Shenzhen: `000001.SZ`
- STAR Market (科创板): `688981.SS`
- Beijing: `833819.BJ`
- Funds/ETFs are resolved by `search_instruments()` first, falling back to Tencent's fund API (`sqt.gtimg.cn`)

## Key notes

- The project targets **Windows desktop** (NSIS installer). The Tauri config only defines Windows bundle targets.
- The version is read at server startup from `src-tauri/Cargo.toml` — it is not synced with `invest_app/__init__.py` or `ui/package.json`.
- `run.sh` is a convenience for quickly launching the Dash web dashboard, not part of the main dev workflow.
- China market trading hours check (`_is_cn_market_open`) is used for context only — data endpoints always attempt to return data regardless.
