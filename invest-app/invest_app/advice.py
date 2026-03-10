"""Rule-based investment advice built from portfolio analysis metrics."""

from dataclasses import dataclass

from invest_app.analysis import PortfolioSummary


@dataclass
class HoldingAdvice:
    """Advice for a single holding."""

    ticker: str
    score: int
    action: str
    reasons: list[str]


@dataclass
class AdviceReport:
    """Aggregated advice report for a full portfolio."""

    holdings: list[HoldingAdvice]
    portfolio_risks: list[str]


def _score_action(score: int) -> str:
    if score >= 70:
        return "可分批加仓"
    if score >= 55:
        return "继续持有"
    if score >= 40:
        return "持有观察"
    return "考虑减仓"


def generate_advice(summary: PortfolioSummary) -> AdviceReport:
    """Generate rule-based advice from portfolio analysis output."""
    holdings: list[HoldingAdvice] = []

    for h in summary.holdings:
        score = 50
        reasons: list[str] = []

        if h.gain_loss_pct >= 15:
            score += 18
            reasons.append("盈利趋势较强")
        elif h.gain_loss_pct >= 5:
            score += 8
            reasons.append("处于温和盈利")
        elif h.gain_loss_pct <= -15:
            score -= 20
            reasons.append("回撤较大")
        elif h.gain_loss_pct <= -5:
            score -= 10
            reasons.append("短期承压")

        if h.weight > 35:
            score -= 15
            reasons.append("单一持仓占比过高")
        elif h.weight > 25:
            score -= 8
            reasons.append("持仓集中度偏高")

        if h.market_value < 5000:
            reasons.append("仓位较小可灵活调整")

        score = max(0, min(100, score))
        holdings.append(
            HoldingAdvice(
                ticker=h.ticker,
                score=score,
                action=_score_action(score),
                reasons=reasons or ["数据中性，等待更多信号"],
            )
        )

    portfolio_risks: list[str] = []
    if summary.holdings:
        top_weight = max(h.weight for h in summary.holdings)
        if top_weight > 35:
            portfolio_risks.append("组合第一大持仓超过35%，建议分散风险")
        elif top_weight > 25:
            portfolio_risks.append("组合集中度偏高，可逐步均衡仓位")

    if summary.total_gain_loss_pct <= -12:
        portfolio_risks.append("组合整体回撤较大，建议降低仓位并控制节奏")
    elif summary.total_gain_loss_pct >= 20:
        portfolio_risks.append("组合累计收益较高，可考虑分批止盈")

    holdings.sort(key=lambda x: x.score, reverse=True)
    return AdviceReport(holdings=holdings, portfolio_risks=portfolio_risks)
