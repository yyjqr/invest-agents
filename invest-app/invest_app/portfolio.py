"""Portfolio management — loading, saving, and modifying holdings."""

import json
from datetime import datetime
from pathlib import Path

from invest_app.config import PORTFOLIO_FILE, ensure_data_dir
from invest_app.models import (
    AssetType,
    Holding,
    Portfolio,
    Transaction,
    TransactionType,
)


def load_portfolio(path: Path | None = None) -> Portfolio:
    """Load portfolio from disk, or create a new one if none exists."""
    path = path or PORTFOLIO_FILE
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return Portfolio.model_validate(data)
    return Portfolio()


def save_portfolio(portfolio: Portfolio, path: Path | None = None) -> None:
    """Persist the portfolio to disk as JSON."""
    path = path or PORTFOLIO_FILE
    ensure_data_dir()
    portfolio.updated_at = datetime.now()
    path.write_text(portfolio.model_dump_json(indent=2), encoding="utf-8")


def add_holding(
    portfolio: Portfolio,
    ticker: str,
    shares: float,
    price: float,
    asset_type: AssetType = AssetType.STOCK,
) -> Holding:
    """Add shares of a ticker to the portfolio (creates or updates the holding)."""
    ticker = ticker.upper()
    tx = Transaction(
        type=TransactionType.BUY,
        ticker=ticker,
        shares=shares,
        price_per_share=price,
    )

    if ticker in portfolio.holdings:
        holding = portfolio.holdings[ticker]
        total_shares = holding.shares + shares
        holding.avg_cost_basis = (
            (holding.shares * holding.avg_cost_basis) + (shares * price)
        ) / total_shares
        holding.shares = total_shares
        holding.transactions.append(tx)
    else:
        holding = Holding(
            ticker=ticker,
            shares=shares,
            avg_cost_basis=price,
            asset_type=asset_type,
            transactions=[tx],
        )
        portfolio.holdings[ticker] = holding

    return holding


def remove_holding(
    portfolio: Portfolio,
    ticker: str,
    shares: float,
    price: float,
) -> Holding | None:
    """Sell shares from a holding. Returns the updated holding, or None if fully sold."""
    ticker = ticker.upper()
    if ticker not in portfolio.holdings:
        raise ValueError(f"Ticker {ticker} not found in portfolio")

    holding = portfolio.holdings[ticker]
    if shares > holding.shares:
        raise ValueError(
            f"Cannot sell {shares} shares of {ticker} — only {holding.shares} held"
        )

    tx = Transaction(
        type=TransactionType.SELL,
        ticker=ticker,
        shares=shares,
        price_per_share=price,
    )
    holding.transactions.append(tx)
    holding.shares -= shares

    if holding.shares == 0:
        del portfolio.holdings[ticker]
        return None

    return holding
