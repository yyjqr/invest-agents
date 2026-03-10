"""Portfolio analysis — returns, allocation, and performance metrics."""

from dataclasses import dataclass

from invest_app.market_data import get_bulk_quotes
from invest_app.models import Portfolio


@dataclass
class HoldingAnalysis:
    """Analysis of a single holding."""

    ticker: str
    shares: float
    avg_cost: float
    current_price: float
    market_value: float
    total_cost: float
    gain_loss: float
    gain_loss_pct: float
    weight: float  # percentage of total portfolio


@dataclass
class PortfolioSummary:
    """Overall portfolio summary."""

    total_value: float
    total_cost: float
    total_gain_loss: float
    total_gain_loss_pct: float
    holdings: list[HoldingAnalysis]


def analyze_portfolio(portfolio: Portfolio) -> PortfolioSummary:
    """Analyze the full portfolio with live market data."""
    if not portfolio.holdings:
        return PortfolioSummary(
            total_value=0,
            total_cost=0,
            total_gain_loss=0,
            total_gain_loss_pct=0,
            holdings=[],
        )

    tickers = list(portfolio.holdings.keys())
    quotes = get_bulk_quotes(tickers)

    holding_analyses: list[HoldingAnalysis] = []
    total_value = 0.0
    total_cost = 0.0

    for ticker, holding in portfolio.holdings.items():
        quote = quotes[ticker]
        market_value = holding.shares * quote.price
        cost = holding.shares * holding.avg_cost_basis
        gain_loss = market_value - cost
        gain_loss_pct = (gain_loss / cost * 100) if cost else 0.0

        total_value += market_value
        total_cost += cost

        holding_analyses.append(
            HoldingAnalysis(
                ticker=ticker,
                shares=holding.shares,
                avg_cost=holding.avg_cost_basis,
                current_price=quote.price,
                market_value=market_value,
                total_cost=cost,
                gain_loss=gain_loss,
                gain_loss_pct=gain_loss_pct,
                weight=0,  # calculated below
            )
        )

    # Calculate weights
    for h in holding_analyses:
        h.weight = (h.market_value / total_value * 100) if total_value else 0.0

    total_gain_loss = total_value - total_cost
    total_gain_loss_pct = (total_gain_loss / total_cost * 100) if total_cost else 0.0

    return PortfolioSummary(
        total_value=total_value,
        total_cost=total_cost,
        total_gain_loss=total_gain_loss,
        total_gain_loss_pct=total_gain_loss_pct,
        holdings=sorted(holding_analyses, key=lambda h: h.market_value, reverse=True),
    )
