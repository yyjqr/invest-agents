"""Tests for data models."""

from invest_app.models import (
    AssetType,
    Holding,
    Portfolio,
    Transaction,
    TransactionType,
)


def test_transaction_total_cost():
    tx = Transaction(
        type=TransactionType.BUY,
        ticker="AAPL",
        shares=10,
        price_per_share=150.0,
    )
    assert tx.total_cost == 1500.0


def test_holding_total_invested():
    holding = Holding(ticker="AAPL", shares=10, avg_cost_basis=150.0)
    assert holding.total_invested == 1500.0


def test_holding_default_asset_type():
    holding = Holding(ticker="AAPL", shares=5, avg_cost_basis=100.0)
    assert holding.asset_type == AssetType.STOCK


def test_portfolio_defaults():
    portfolio = Portfolio()
    assert portfolio.name == "My Portfolio"
    assert portfolio.holdings == {}
