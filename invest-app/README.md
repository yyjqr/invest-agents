# Invest App

A stock and fund investment tracking and analysis application built with Python.

## Features

- **Portfolio Management** — Track holdings across stocks and funds with buy/sell transactions
- **Market Data** — Fetch real-time and historical price data via Yahoo Finance
- **Analysis** — Calculate returns, allocation breakdowns, and performance metrics
- **CLI Interface** — Manage your portfolio from the command line
- **Web Dashboard** — Interactive Dash UI with K-line chart, live quote, and buy/sell zone visualization

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -e ".[dev]"
```

## Usage

```bash
# Add a holding
invest add AAPL --shares 10

# Add a holding with a manual price (optional)
invest add AAPL --shares 10 --price 150.00

# View portfolio
invest portfolio

# Get a stock quote
invest quote AAPL

# Get Shanghai / STAR Market quotes (A-share)
invest quote 600519
invest quote 688981

# Search by Chinese name/code (stock/fund)
invest search 茅台
invest search 科创50

# Watch quote updates every 5 seconds
invest watch 600519 --interval 5

# Show K-line (candlestick) chart
invest chart 600519 --period 6mo

# Single-stock buy/sell zone recommendation (3-6 months)
invest recommend 600519 --period 6mo

# Analyze portfolio performance
invest analyze

# Get rule-based portfolio advice
invest advise

# Launch interactive web dashboard (Dash)
invest web
invest web --port 8080 --debug
```

### China A-share quick workflow (Shanghai / STAR)

```bash
# 1) Query live-like quote (auto maps 600xxx/688xxx to .SS)
invest quote 600519
invest quote 688981

# 1.1) Search by Chinese name first if you don't remember the code
invest search 贵州茅台
invest search 科创50

# 2) Add holdings (price optional; app can fetch quote automatically)
invest add 600519 --shares 100
invest add 688981 --shares 50

# 2.1) Optionally watch intraday updates
invest watch 600519 --interval 5

# 2.2) Open K-line chart window
invest chart 600519 --period 6mo

# 2.3) Generate one-stock buy/sell zone suggestion
invest recommend 600519 --period 6mo

# 3) View portfolio and analysis
invest portfolio
invest analyze

# 4) Generate rule-based advice
invest advise

# 5) Or launch web dashboard for all-in-one interactive analysis
invest web
```

## Testing

```bash
pytest
```
