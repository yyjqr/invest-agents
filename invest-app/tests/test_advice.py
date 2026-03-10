"""Tests for rule-based investment advice."""

from invest_app.advice import generate_advice
from invest_app.analysis import HoldingAnalysis, PortfolioSummary
from invest_app.market_data import normalize_ticker


def test_normalize_ticker_for_shanghai_and_star_market():
    assert normalize_ticker("600519") == "600519.SS"
    assert normalize_ticker("688001") == "688001.SS"
    assert normalize_ticker("000001") == "000001.SZ"
    assert normalize_ticker("600519.SS") == "600519.SS"


def test_generate_advice_penalizes_overweight_and_drawdown():
    summary = PortfolioSummary(
        total_value=100000,
        total_cost=120000,
        total_gain_loss=-20000,
        total_gain_loss_pct=-16.7,
        holdings=[
            HoldingAnalysis(
                ticker="688001.SS",
                shares=100,
                avg_cost=100,
                current_price=70,
                market_value=7000,
                total_cost=10000,
                gain_loss=-3000,
                gain_loss_pct=-30,
                weight=40,
            ),
            HoldingAnalysis(
                ticker="600519.SS",
                shares=10,
                avg_cost=1200,
                current_price=1500,
                market_value=15000,
                total_cost=12000,
                gain_loss=3000,
                gain_loss_pct=25,
                weight=15,
            ),
        ],
    )

    report = generate_advice(summary)
    result = {item.ticker: item for item in report.holdings}

    assert result["688001.SS"].score < result["600519.SS"].score
    assert result["688001.SS"].action in {"考虑减仓", "持有观察"}
    assert any("回撤" in risk for risk in report.portfolio_risks)
