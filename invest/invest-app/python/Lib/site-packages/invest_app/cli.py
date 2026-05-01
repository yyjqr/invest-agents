"""Command-line interface for the investment app."""

import time
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

from invest_app.charts import show_kline_chart
from invest_app.advice import generate_advice
from invest_app.analysis import analyze_portfolio
from invest_app.forecast import forecast_batch
from invest_app.forecast_monitor import generate_analysis_report, run_loop, run_one_round
from invest_app.history import add_history_record, get_recent_tickers
from invest_app.market_data import get_fundamentals, get_history, get_quote, resolve_ticker, search_instruments
from invest_app.models import AssetType
from invest_app.portfolio import add_holding, load_portfolio, remove_holding, save_portfolio
from invest_app.recommendation import recommend_stock

console = Console(legacy_windows=False)


@click.group()
def main() -> None:
    """Invest App — track and analyze your stock & fund portfolio."""


@main.command()
@click.argument("ticker")
@click.option("--shares", required=True, type=float, help="Number of shares to buy.")
@click.option("--price", type=float, help="Price per share (optional; defaults to live quote).")
@click.option(
    "--type",
    "asset_type",
    type=click.Choice(["stock", "etf", "mutual_fund"], case_sensitive=False),
    default="stock",
    help="Asset type.",
)
def add(ticker: str, shares: float, price: float | None, asset_type: str) -> None:
    """Add shares of a TICKER to your portfolio."""
    if price is None:
        try:
            quote = get_quote(ticker)
            price = quote.price
            console.print(
                f"[dim]No --price provided, using live quote for {quote.ticker}: ${price:.2f}[/dim]"
            )
        except Exception as e:
            console.print(
                "[red]Error:[/red] Could not fetch live quote. "
                "Please provide --price manually."
            )
            console.print(f"[dim]Details: {e}[/dim]")
            return

    portfolio = load_portfolio()
    holding = add_holding(portfolio, ticker, shares, price, AssetType(asset_type))
    save_portfolio(portfolio)
    console.print(
        f"[green]OK[/green] Added {shares} shares of {holding.ticker} "
        f"@ ${price:.2f} (total: {holding.shares} shares)"
    )


@main.command()
@click.argument("ticker")
@click.option("--shares", required=True, type=float, help="Number of shares to sell.")
@click.option("--price", required=True, type=float, help="Sell price per share.")
def sell(ticker: str, shares: float, price: float) -> None:
    """Sell shares of a TICKER from your portfolio."""
    portfolio = load_portfolio()
    try:
        result = remove_holding(portfolio, ticker, shares, price)
        save_portfolio(portfolio)
        if result is None:
            console.print(f"[yellow]Sold all shares of {ticker.upper()}.[/yellow]")
        else:
            console.print(
                f"[green]OK[/green] Sold {shares} shares of {result.ticker} "
                f"@ ${price:.2f} (remaining: {result.shares} shares)"
            )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")


@main.command()
def portfolio() -> None:
    """Display your current portfolio."""
    p = load_portfolio()
    if not p.holdings:
        console.print("[dim]Portfolio is empty. Use 'invest add' to add holdings.[/dim]")
        return

    table = Table(title=f"📊 {p.name}")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Shares", justify="right")
    table.add_column("Avg Cost", justify="right", style="dim")
    table.add_column("Total Invested", justify="right")
    table.add_column("Type", style="dim")

    total = 0.0
    for ticker, h in sorted(p.holdings.items()):
        invested = h.shares * h.avg_cost_basis
        total += invested
        table.add_row(
            ticker,
            f"{h.shares:.2f}",
            f"${h.avg_cost_basis:.2f}",
            f"${invested:,.2f}",
            h.asset_type.value,
        )

    table.add_section()
    table.add_row("TOTAL", "", "", f"${total:,.2f}", "")
    console.print(table)


@main.command()
@click.argument("ticker")
def quote(ticker: str) -> None:
    """Get a live quote for a TICKER."""
    try:
        q = get_quote(ticker)
        color = "green" if q.change >= 0 else "red"
        console.print(f"[bold]{q.name}[/bold] ({q.ticker})")
        console.print(f"  Price:  ${q.price:.2f}")
        console.print(f"  Change: [{color}]{q.change:+.2f} ({q.change_pct:+.2f}%)[/{color}]")
        if q.market_cap:
            console.print(f"  Mkt Cap: ${q.market_cap:,.0f}")
        if q.pe_ratio:
            console.print(f"  P/E:    {q.pe_ratio:.2f}")
    except Exception as e:
        console.print(f"[red]Error fetching quote for {ticker}:[/red] {e}")


@main.command()
@click.argument("query")
@click.option("--limit", default=10, show_default=True, type=int, help="Max results to show.")
def search(query: str, limit: int) -> None:
    """Search Chinese stocks/funds by code or Chinese name."""
    results = search_instruments(query, limit=max(1, limit))
    if not results:
        console.print("[yellow]No instruments found.[/yellow]")
        return

    table = Table(title=f"🔎 Search Results: {query}")
    table.add_column("Name", style="bold")
    table.add_column("Code", style="cyan")
    table.add_column("Exchange", style="dim")
    table.add_column("Category", style="dim")
    table.add_column("Use", style="green")

    for item in results:
        table.add_row(
            item.name,
            item.code,
            item.exchange,
            item.category,
            f"invest quote {item.code}",
        )

    console.print(table)


@main.command()
@click.argument("ticker")
@click.option("--interval", default=5.0, show_default=True, type=float, help="Refresh interval in seconds.")
def watch(ticker: str, interval: float) -> None:
    """Continuously watch real-time-like quote updates. Press Ctrl+C to stop."""
    interval = max(1.0, interval)
    console.print(f"[dim]Watching {ticker} every {interval:.0f}s. Press Ctrl+C to stop.[/dim]")
    try:
        while True:
            q = get_quote(ticker)
            color = "green" if q.change >= 0 else "red"
            ts = datetime.now().strftime("%H:%M:%S")
            console.print(
                f"[{ts}] [bold]{q.name}[/bold] ({q.ticker}) "
                f"${q.price:.2f} [{color}]{q.change:+.2f} ({q.change_pct:+.2f}%)[/{color}]"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
    except Exception as e:
        console.print(f"[red]Error while watching {ticker}:[/red] {e}")


@main.command()
@click.argument("ticker")
@click.option(
    "--period",
    default="6mo",
    show_default=True,
    type=click.Choice(["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"], case_sensitive=False),
    help="K-line history period.",
)
def chart(ticker: str, period: str) -> None:
    """Show a K-line (candlestick) chart in a window."""
    try:
        df = get_history(ticker, period=period, interval="1d")
        if df.empty:
            console.print(f"[yellow]No historical data for {ticker}.[/yellow]")
            return
        show_kline_chart(df, ticker=ticker, title=f"{ticker} K-Line ({period})")
    except Exception as e:
        console.print(f"[red]Error drawing chart for {ticker}:[/red] {e}")


@main.command()
@click.argument("ticker")
@click.option(
    "--period",
    default="6mo",
    show_default=True,
    type=click.Choice(["3mo", "6mo"], case_sensitive=False),
    help="Analysis window for buy/sell guidance.",
)
def recommend(ticker: str, period: str) -> None:
    """Recommend buy/sell zones for one stock from trend + valuation signals."""
    try:
        fundamentals = get_fundamentals(ticker)
        report = recommend_stock(
            ticker=ticker,
            period=period,
            pe_ratio=fundamentals.get("pe_ratio"),
            profit_margin=fundamentals.get("profit_margin"),
        )
    except Exception as e:
        console.print(f"[red]Error generating recommendation for {ticker}:[/red] {e}")
        return

    # ── main metrics table ────────────────────────────────────────────
    kind_label = {"fund": "基金", "star": "科创板", "stock": "股票"}.get(
        report.instrument_type, "股票"
    )
    table = Table(
        title=f"📌 {report.name} ({report.ticker}) — {kind_label} · {report.sector}"
    )
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value")

    table.add_row("当前价格", f"¥{report.current_price:.4f}")
    table.add_row("操作建议", report.action)
    table.add_row("市场趋势", report.market_view)
    table.add_row("估值评价", report.valuation_view)
    table.add_row("行业", f"{report.sector}（{report.sector_maturity}）")
    if report.ma20 is not None:
        table.add_row("MA20", f"¥{report.ma20:.4f}")
    if report.ma60 is not None:
        table.add_row("MA60", f"¥{report.ma60:.4f}")
    if report.high_3m is not None and report.low_3m is not None:
        table.add_row("3M Range", f"¥{report.low_3m:.4f} ~ ¥{report.high_3m:.4f}")
    if report.high_6m is not None and report.low_6m is not None:
        table.add_row("6M Range", f"¥{report.low_6m:.4f} ~ ¥{report.high_6m:.4f}")
    if report.volume_ratio is not None:
        table.add_row("量比", f"{report.volume_ratio:.2f}x (vs 20日均量)")
    if report.pe_ratio is not None:
        table.add_row("市盈率", f"{report.pe_ratio:.2f}")
    if report.profit_margin is not None:
        table.add_row("利润率", f"{report.profit_margin * 100:.2f}%")
    if report.buy_range is not None:
        table.add_row("买入区间", f"¥{report.buy_range[0]:.4f} ~ ¥{report.buy_range[1]:.4f}")
    if report.sell_range is not None:
        table.add_row("卖出区间", f"¥{report.sell_range[0]:.4f} ~ ¥{report.sell_range[1]:.4f}")
    if report.stop_loss is not None:
        table.add_row("建议止损", f"¥{report.stop_loss:.4f}")

    console.print(table)

    # ── benchmarks ────────────────────────────────────────────────────
    if report.benchmarks:
        bm_table = Table(title="🌐 关联海外标的")
        bm_table.add_column("标的", style="bold")
        bm_table.add_column("代码", style="cyan")
        bm_table.add_column("涨跌%", justify="right")
        bm_table.add_column("趋势")
        for bm in report.benchmarks:
            chg = f"{bm.change_pct:+.2f}%" if bm.change_pct is not None else "N/A"
            bm_table.add_row(bm.label, bm.ticker, chg, bm.trend)
        console.print(bm_table)

    # ── SWOT ──────────────────────────────────────────────────────────
    if report.swot:
        console.print("\n[bold]📊 SWOT 分析[/bold]")
        console.print("[green]Strengths（优势）[/green]")
        for item in report.swot.strengths:
            console.print(f"  + {item}")
        console.print("[red]Weaknesses（劣势）[/red]")
        for item in report.swot.weaknesses:
            console.print(f"  - {item}")
        console.print("[blue]Opportunities（机会）[/blue]")
        for item in report.swot.opportunities:
            console.print(f"  ▸ {item}")
        console.print("[yellow]Threats（威胁）[/yellow]")
        for item in report.swot.threats:
            console.print(f"  ▾ {item}")

    # ── Strategy signals ──────────────────────────────────────────────
    if report.strategies:
        sr = report.strategies
        strat_table = Table(title="🎯 策略信号一览")
        strat_table.add_column("策略", style="bold cyan", min_width=22)
        strat_table.add_column("信号", min_width=14)
        strat_table.add_column("评分", justify="right", min_width=10)
        sig_colors = {
            "买入": "green", "反弹机会": "blue", "综合买入信号": "green",
            "可分批建仓": "green", "观望偏多": "yellow", "持有观察": "dim",
            "观望": "white", "卖出": "red",
        }
        for s in [sr.strategy1, sr.strategy2, sr.strategy3]:
            clr = sig_colors.get(s.signal, "white")
            score_bar = "█" * int(s.score / 10) + "░" * (10 - int(s.score / 10))
            strat_table.add_row(
                s.name,
                f"[{clr}]{s.signal}[/{clr}]",
                f"{score_bar} {s.score:.0f}/100",
            )
        cclr = "green" if sr.combined_score >= 65 else "yellow" if sr.combined_score >= 50 else "white"
        strat_table.add_row(
            "[bold]综合评分[/bold]",
            f"[{cclr}]{sr.composite_action}[/{cclr}]",
            f"[bold {cclr}]{sr.combined_score:.0f}/100[/bold {cclr}]",
        )
        console.print(strat_table)

        # Position advice
        pos_color = "green" if sr.position_pct >= 30 else "yellow" if sr.position_pct >= 15 else "dim"
        pos_label = f"{sr.position_pct}% 仓位" if sr.position_pct > 0 else "暂不建仓"
        console.print(
            f"  💼 仓位建议: [{pos_color}]{pos_label}[/{pos_color}]  —  {sr.position_note}"
        )

        # Hot sector & northbound
        hot_str = (
            f"今日热门板块：{', '.join(sr.hot_sectors[:5])}" if sr.hot_sectors
            else "热门板块数据暂不可用"
        )
        hit_str = "[green]HIT[/green]" if sr.in_hot_sector else "miss"
        console.print(
            f"[dim]行业热度：{hit_str}  |  北向情绪代理：{sr.northbound_sentiment}  |  {hot_str}[/dim]"
        )

        # Strategy 3 detail
        if sr.strategy3.details:
            console.print("\n[bold]宏观共振分析详情[/bold]")
            for d in sr.strategy3.details:
                console.print(f"  {d}")

    # ── reasoning ─────────────────────────────────────────────────────
    console.print("\n[bold]分析理由[/bold]")
    for reason in report.reasons[:10]:
        console.print(f"- {reason}")
    console.print(
        "\n[dim]说明：资金买卖方向使用成交量变化作近似，不等同逐笔主力资金。"
        "基金采用分位区间策略（P20-P35买/P75-P90卖），股票采用支撑/压力位策略。"
        "最近基本面依赖公开数据源，可能存在延迟。仅供研究参考。[/dim]"
    )


@main.command()
def analyze() -> None:
    """Analyze your portfolio performance with live prices."""
    p = load_portfolio()
    if not p.holdings:
        console.print("[dim]Portfolio is empty.[/dim]")
        return

    try:
        summary = analyze_portfolio(p)
    except Exception as e:
        console.print(f"[red]Error during analysis:[/red] {e}")
        return

    table = Table(title="Portfolio Analysis")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Shares", justify="right")
    table.add_column("Avg Cost", justify="right", style="dim")
    table.add_column("Price", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Gain/Loss", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Weight", justify="right", style="dim")

    for h in summary.holdings:
        gl_color = "green" if h.gain_loss >= 0 else "red"
        table.add_row(
            h.ticker,
            f"{h.shares:.2f}",
            f"${h.avg_cost:.2f}",
            f"${h.current_price:.2f}",
            f"${h.market_value:,.2f}",
            f"[{gl_color}]${h.gain_loss:+,.2f}[/{gl_color}]",
            f"[{gl_color}]{h.gain_loss_pct:+.1f}%[/{gl_color}]",
            f"{h.weight:.1f}%",
        )

    table.add_section()
    total_color = "green" if summary.total_gain_loss >= 0 else "red"
    table.add_row(
        "TOTAL",
        "",
        f"${summary.total_cost:,.2f}",
        "",
        f"${summary.total_value:,.2f}",
        f"[{total_color}]${summary.total_gain_loss:+,.2f}[/{total_color}]",
        f"[{total_color}]{summary.total_gain_loss_pct:+.1f}%[/{total_color}]",
        "",
    )
    console.print(table)


@main.command()
def advise() -> None:
    """Generate rule-based investment advice from live portfolio data."""
    p = load_portfolio()
    if not p.holdings:
        console.print("[dim]Portfolio is empty.[/dim]")
        return

    try:
        summary = analyze_portfolio(p)
        report = generate_advice(summary)
    except Exception as e:
        console.print(f"[red]Error during advice generation:[/red] {e}")
        return

    table = Table(title="🧭 Portfolio Advice")
    table.add_column("Ticker", style="bold cyan")
    table.add_column("Score", justify="right")
    table.add_column("Action", style="bold")
    table.add_column("Return %", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Reason", style="dim")

    analysis_map = {h.ticker: h for h in summary.holdings}
    for item in report.holdings:
        h = analysis_map[item.ticker]
        score_color = "green" if item.score >= 55 else "yellow" if item.score >= 40 else "red"
        table.add_row(
            item.ticker,
            f"[{score_color}]{item.score}[/{score_color}]",
            item.action,
            f"{h.gain_loss_pct:+.1f}%",
            f"{h.weight:.1f}%",
            "；".join(item.reasons[:2]),
        )

    console.print(table)
    if report.portfolio_risks:
        console.print("\n[bold yellow]组合风险提示[/bold yellow]")
        for risk in report.portfolio_risks:
            console.print(f"- {risk}")

    console.print(
        "\n[dim]仅供研究参考，不构成投资建议。请结合基本面与风险承受能力独立决策。[/dim]"
    )


@main.command()
@click.argument("tickers", nargs=-1, required=True)
@click.option(
    "--period",
    default="6mo",
    show_default=True,
    type=click.Choice(["3mo", "6mo"], case_sensitive=False),
    help="Analysis window.",
)
def batch(tickers: tuple[str, ...], period: str) -> None:
    """Batch-analyse up to 10 tickers and show a summary table.

    Example: invest batch 600519 002600 588000 512400
    """
    stocks = list(tickers)[:10]
    action_color = {
        "可分批买入": "green", "可分批止盈": "red",
        "持有观察": "yellow", "等待企稳": "dim",
    }

    table = Table(title=f"📊 批量分析（{period}，共 {len(stocks)} 支）")
    table.add_column("代码", style="bold cyan")
    table.add_column("名称")
    table.add_column("现价", justify="right")
    table.add_column("涨跌%", justify="right")
    table.add_column("操作建议", justify="center")
    table.add_column("策略评分", justify="right")
    table.add_column("行业", style="dim")
    table.add_column("买入区间")
    table.add_column("止损")

    for tk in stocks:
        resolved = resolve_ticker(tk)  # handle Chinese names / abbreviations
        try:
            q = get_quote(resolved)
            fundamentals = get_fundamentals(resolved)
            report = recommend_stock(
                ticker=resolved, period=period,
                pe_ratio=fundamentals.get("pe_ratio"),
                profit_margin=fundamentals.get("profit_margin"),
            )
            # Save to history
            try:
                add_history_record(
                    ticker=report.ticker, name=report.name,
                    action=report.action, sector=report.sector,
                    price=report.current_price,
                )
            except Exception:
                pass

            chg_color = "green" if q.change_pct >= 0 else "red"
            ac = action_color.get(report.action, "white")
            buy_str = (f"¥{report.buy_range[0]:.2f}~{report.buy_range[1]:.2f}"
                       if report.buy_range else "—")
            stop_str = f"¥{report.stop_loss:.2f}" if report.stop_loss else "—"
            if report.strategies:
                ssc = report.strategies.combined_score
                s_color = "green" if ssc >= 65 else "yellow" if ssc >= 50 else "white"
                strat_str = f"[{s_color}]{ssc:.0f}/100[/{s_color}]"
            else:
                strat_str = "—"

            table.add_row(
                report.ticker,
                report.name[:10],
                f"¥{report.current_price:.4f}",
                f"[{chg_color}]{q.change_pct:+.2f}%[/{chg_color}]",
                f"[{ac}]{report.action}[/{ac}]",
                strat_str,
                report.sector,
                buy_str,
                stop_str,
            )
        except Exception as exc:
            display = resolved if resolved != tk else tk
            table.add_row(display, f"({tk})" if resolved != tk else "—",
                          "—", "—", "[red]错误[/red]", "—", str(exc)[:24], "—", "—")

    console.print(table)
    console.print("[dim]已将分析记录保存至历史。用 invest history 查看。[/dim]")


@main.command()
@click.argument("tickers", nargs=-1, required=True)
@click.option(
    "--horizon",
    default="1d",
    show_default=True,
    type=click.Choice(["1h", "1d"], case_sensitive=False),
    help="Forecast horizon.",
)
@click.option(
    "--history-period",
    default="6mo",
    show_default=True,
    type=click.Choice(["3mo", "6mo"], case_sensitive=False),
    help="Historical lookback window used by the forecast model.",
)
def forecast(tickers: tuple[str, ...], horizon: str, history_period: str) -> None:
    """Forecast the next 1 hour or 1 trading day for up to 10 stocks/funds.

    Example:
      invest forecast 600519 588000 512400 --horizon 1d --history-period 6mo
    """
    targets = [item for item in tickers if item.strip()][:10]
    if not targets:
        console.print("[yellow]请至少输入 1 个代码或名称。[/yellow]")
        return

    try:
        portfolio = load_portfolio()
        results = forecast_batch(
            targets,
            horizon=horizon,
            history_period=history_period,
            portfolio=portfolio,
        )
    except Exception as e:
        console.print(f"[red]Error generating forecasts:[/red] {e}")
        return

    table = Table(
        title=(
            f"🔮 短期预测（未来 {horizon}，历史窗口 {history_period}，"
            f"共 {len(results)} 支）"
        )
    )
    table.add_column("代码", style="bold cyan")
    table.add_column("名称")
    table.add_column("现价", justify="right")
    table.add_column("趋势", justify="center")
    table.add_column("预测价", justify="right")
    table.add_column("高点", justify="right")
    table.add_column("低点", justify="right")
    table.add_column("置信度", justify="right")

    trend_color = {
        "上涨偏多": "green",
        "震荡偏强": "yellow",
        "震荡整理": "white",
        "震荡偏弱": "yellow",
        "下跌偏空": "red",
    }

    for item in results:
        color = trend_color.get(item.trend, "white")
        table.add_row(
            item.ticker,
            item.name[:12],
            f"¥{item.current_price:.4f}",
            f"[{color}]{item.trend}[/{color}]",
            f"¥{item.predicted_price:.4f}",
            f"¥{item.predicted_high:.4f} @ {item.high_time}",
            f"¥{item.predicted_low:.4f} @ {item.low_time}",
            f"{item.confidence:.0f}%",
        )

    console.print(table)

    console.print("\n[bold]主要依据[/bold]")
    for item in results:
        console.print(
            f"• [bold]{item.name}[/bold] ({item.ticker})："
            f"末端预测 {item.predicted_price:.4f}，"
            f"预期波动 {item.expected_return_pct:+.2f}% / 区间 {item.expected_range_pct:.2f}%"
        )
        for reason in item.factors[:4]:
            console.print(f"  - {reason}")
        if item.support_price is not None or item.resistance_price is not None:
            support_text = (
                f"¥{item.support_price:.4f}" if item.support_price is not None else "—"
            )
            resistance_text = (
                f"¥{item.resistance_price:.4f}"
                if item.resistance_price is not None
                else "—"
            )
            console.print(
                f"  - 参考支撑/压力：{support_text} / {resistance_text}"
            )

    console.print(
        "\n[dim]说明：该功能基于近 3-6 个月历史行情、RSI/Bias、波动率、量能、"
        "PE/利润率及本地交易记录做短线研究性预测，不保证未来实际价格路径。[/dim]"
    )


@main.command()
@click.option("--limit", default=20, show_default=True, type=int, help="Max records to show.")
def history(limit: int) -> None:
    """Show the most recently analysed tickers from search history."""
    records = get_recent_tickers(limit=limit)
    if not records:
        console.print("[dim]暂无历史记录。运行 invest recommend / invest batch 后将自动记录。[/dim]")
        return

    action_color = {
        "可分批买入": "green", "可分批止盈": "red",
        "持有观察": "yellow", "等待企稳": "dim",
    }

    table = Table(title=f"🕘 最近分析历史（最新 {len(records)} 条）")
    table.add_column("时间", style="dim")
    table.add_column("代码", style="bold cyan")
    table.add_column("名称")
    table.add_column("记录价格", justify="right")
    table.add_column("操作建议")
    table.add_column("行业", style="dim")

    for rec in records:
        ac = action_color.get(rec.action, "white")
        price_str = f"¥{rec.price:.4f}" if rec.price is not None else "—"
        table.add_row(
            rec.timestamp[:16],
            rec.ticker,
            rec.name[:12],
            price_str,
            f"[{ac}]{rec.action}[/{ac}]",
            rec.sector,
        )

    console.print(table)


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True,
              help="Bind host. Use 0.0.0.0 (default) for LAN access, 127.0.0.1 for localhost only.")
@click.option("--port", default=8050, show_default=True, type=int, help="Server port.")
@click.option("--debug", is_flag=True, default=False, help="Enable Dash debug mode.")
def web(host: str, port: int, debug: bool) -> None:
    """Launch the interactive web dashboard (Dash)."""
    import socket
    from invest_app.webapp import run_server  # lazy import to keep CLI fast

    # Resolve a friendly local IP for display
    _display_host = host
    if host in ("0.0.0.0", ""):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            _display_host = s.getsockname()[0]
            s.close()
        except OSError:
            _display_host = "<local-ip>"

    console.print(
        f"[green]>>[/green] Starting web dashboard\n"
        f"  Local:   [bold]http://127.0.0.1:{port}[/bold]\n"
        f"  Network: [bold]http://{_display_host}:{port}[/bold]\n"
        "  Press Ctrl+C to stop."
    )
    run_server(host=host, port=port, debug=debug)


# ------------------------------------------------------------------
# monitor – hourly forecast-vs-actual tracking
# ------------------------------------------------------------------


@main.command("monitor")
@click.argument("tickers", nargs=-1, required=True)
@click.option(
    "--loop", "loop_mode", is_flag=True,
    help="循环监控整个交易日（自动在每小时交易时段运行）。",
)
@click.option(
    "--once", "once_mode", is_flag=True,
    help="只运行一轮（预测 → 等待 → 对比）。",
)
@click.option(
    "--no-wait", "no_wait", is_flag=True,
    help="只做预测，不等待获取实际价格。",
)
@click.option(
    "--wait", "wait_minutes", type=int, default=60,
    help="等待获取实际价格的分钟数（默认 60）。",
)
def monitor_cmd(tickers, loop_mode, once_mode, no_wait, wait_minutes):
    """每小时预测 5-10 只股票并与实际价格对比。

    \b
    TICKERS：要监控的股票/基金代码，建议 5-10 只。
    示例：invest monitor 600519 000001 512400 --loop
    """
    tickers = list(tickers[:10])
    log = console.print

    if loop_mode:
        run_loop(tickers, log_fn=log)
    elif once_mode or no_wait:
        run_one_round(
            tickers,
            wait_for_actual=not no_wait,
            wait_minutes=wait_minutes,
            log_fn=log,
        )
    else:
        run_one_round(
            tickers,
            wait_for_actual=True,
            wait_minutes=wait_minutes,
            log_fn=log,
        )


@main.command("monitor-report")
def monitor_report_cmd():
    """根据历史监控数据生成预测准确度分析报告。"""
    generate_analysis_report(log_fn=console.print)


if __name__ == "__main__":
    main()
