"""Tests for portfolio management."""

import json
from pathlib import Path

import pytest

from invest_app.models import AssetType, Portfolio
from invest_app.portfolio import add_holding, load_portfolio, remove_holding, save_portfolio


def test_add_new_holding():
    portfolio = Portfolio()
    holding = add_holding(portfolio, "AAPL", 10, 150.0)
    assert holding.ticker == "AAPL"
    assert holding.shares == 10
    assert holding.avg_cost_basis == 150.0
    assert "AAPL" in portfolio.holdings


def test_add_to_existing_holding():
    portfolio = Portfolio()
    add_holding(portfolio, "AAPL", 10, 100.0)
    holding = add_holding(portfolio, "AAPL", 10, 200.0)
    assert holding.shares == 20
    assert holding.avg_cost_basis == 150.0  # weighted average


def test_add_holding_with_asset_type():
    portfolio = Portfolio()
    holding = add_holding(portfolio, "SPY", 5, 450.0, AssetType.ETF)
    assert holding.asset_type == AssetType.ETF


def test_remove_partial_holding():
    portfolio = Portfolio()
    add_holding(portfolio, "AAPL", 10, 150.0)
    result = remove_holding(portfolio, "AAPL", 5, 200.0)
    assert result is not None
    assert result.shares == 5
    assert "AAPL" in portfolio.holdings


def test_remove_all_shares():
    portfolio = Portfolio()
    add_holding(portfolio, "AAPL", 10, 150.0)
    result = remove_holding(portfolio, "AAPL", 10, 200.0)
    assert result is None
    assert "AAPL" not in portfolio.holdings


def test_remove_too_many_shares():
    portfolio = Portfolio()
    add_holding(portfolio, "AAPL", 10, 150.0)
    with pytest.raises(ValueError, match="Cannot sell"):
        remove_holding(portfolio, "AAPL", 20, 200.0)


def test_remove_nonexistent_ticker():
    portfolio = Portfolio()
    with pytest.raises(ValueError, match="not found"):
        remove_holding(portfolio, "MSFT", 5, 100.0)


def test_ticker_uppercased():
    portfolio = Portfolio()
    holding = add_holding(portfolio, "aapl", 10, 150.0)
    assert holding.ticker == "AAPL"
    assert "AAPL" in portfolio.holdings


def test_save_and_load(tmp_path: Path):
    path = tmp_path / "portfolio.json"
    portfolio = Portfolio(name="Test")
    add_holding(portfolio, "AAPL", 10, 150.0)
    save_portfolio(portfolio, path)

    loaded = load_portfolio(path)
    assert loaded.name == "Test"
    assert "AAPL" in loaded.holdings
    assert loaded.holdings["AAPL"].shares == 10


def test_load_missing_file(tmp_path: Path):
    path = tmp_path / "nonexistent.json"
    portfolio = load_portfolio(path)
    assert portfolio.holdings == {}
