"""Dash web application — interactive stock analysis & recommendation.

Features:
  - Single-stock analysis with dual chart (K-line at top + intraday)
  - Batch analysis for up to 10 tickers at once
  - Sector overview with top-50 stocks by turnover
  - Custom watchlist with live prices
  - Persistent search history (JSON-based)
"""

from __future__ import annotations

import traceback

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from plotly.subplots import make_subplots

from invest_app.history import add_history_record, get_recent_tickers
from invest_app.market_data import (
    Quote,
    fetch_sector_list,
    fetch_sector_stock_list,
    get_batch_quotes_tencent,
    get_fundamentals,
    get_history,
    get_intraday,
    get_quote,
    resolve_ticker,
    search_instruments,
)
from invest_app.recommendation import recommend_stock
from invest_app.news import fetch_cn_news, fetch_global_events, fetch_research_reports
from invest_app.watchlist import (
    add_to_watchlist,
    get_watchlist_items,
    remove_from_watchlist,
)

# ---------------------------------------------------------------------------
# App & layout
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    title="Invest App — 股票分析与建议",
    suppress_callback_exceptions=True,
)

_CARD = {
    "padding": "16px", "border": "1px solid #e2e8f0",
    "borderRadius": "8px", "marginBottom": "16px", "background": "#fff",
}
_BTN = {
    "padding": "8px 20px", "fontSize": "16px", "cursor": "pointer",
    "backgroundColor": "#2563eb", "color": "white", "border": "none",
    "borderRadius": "6px",
}

app.layout = html.Div(
    style={"maxWidth": "1060px", "margin": "0 auto", "padding": "24px",
           "fontFamily": "sans-serif", "background": "#f1f5f9"},
    children=[
        html.H2("📈 Invest App — 个股 / 批量分析与买卖建议", style={"textAlign": "center"}),

        # ── News / Research panel ──────────────────────────────────────
        dcc.Interval(id="news-interval", interval=300_000, n_intervals=0),
        html.Div(id="news-panel", style={"marginBottom": "20px"}),

        dcc.Tabs(id="main-tabs", value="single", children=[
            # ── Tab 1: single stock ──────────────────────────────────
            dcc.Tab(label="📌 个股分析", value="single", children=[
                html.Div(style={"marginTop": "16px"}, children=[
                    # ── Hot stocks quick-pick panel ───────────────
                    dcc.Loading(type="circle", children=[
                        html.Div(id="hot-stocks-panel"),
                    ]),

                    # search bar
                    html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "12px"}, children=[
                        dcc.Input(
                            id="ticker-input", type="text",
                            placeholder="输入代码或中文名（如 600519、茅台）",
                            debounce=True,
                            style={"flex": "1", "padding": "8px", "fontSize": "16px"},
                        ),
                        dcc.Dropdown(
                            id="period-dropdown",
                            options=[
                                {"label": "3 个月", "value": "3mo"},
                                {"label": "6 个月", "value": "6mo"},
                                {"label": "1 年", "value": "1y"},
                            ],
                            value="6mo", clearable=False,
                            style={"width": "140px"},
                        ),
                        html.Button("分析", id="analyze-btn", n_clicks=0, style=_BTN),
                    ]),
                    html.Div(id="search-results", style={"marginBottom": "12px"}),

                    # loading area
                    dcc.Loading(type="circle", children=[
                        html.Div(id="quote-summary"),
                        dcc.Graph(id="kline-chart", style={"display": "none"}),
                        html.Div(id="intraday-chart-area"),
                        html.Div(id="recommendation-output"),
                    ]),
                ]),
            ]),

            # ── Tab 2: batch analysis ────────────────────────────────
            dcc.Tab(label="📊 批量分析", value="batch", children=[
                html.Div(style={"marginTop": "16px"}, children=[
                    html.P("输入最多 10 个代码，用逗号或空格分隔：",
                           style={"marginBottom": "8px"}),
                    dcc.Textarea(
                        id="batch-input",
                        placeholder="例如: 600519, 002600, 588000, 512400",
                        style={"width": "100%", "height": "60px", "fontSize": "15px",
                               "padding": "8px"},
                    ),
                    html.Div(style={"display": "flex", "gap": "12px", "marginTop": "8px"}, children=[
                        dcc.Dropdown(
                            id="batch-period",
                            options=[
                                {"label": "3 个月", "value": "3mo"},
                                {"label": "6 个月", "value": "6mo"},
                            ],
                            value="6mo", clearable=False,
                            style={"width": "140px"},
                        ),
                        html.Button("批量分析", id="batch-btn", n_clicks=0, style=_BTN),
                    ]),
                    dcc.Loading(type="circle", children=[
                        html.Div(id="batch-output", style={"marginTop": "16px"}),
                    ]),
                ]),
            ]),

            # ── Tab 3: history ───────────────────────────────────────
            dcc.Tab(label="🕘 历史记录", value="history", children=[
                html.Div(style={"marginTop": "16px"}, children=[
                    html.Button("刷新历史", id="refresh-history-btn", n_clicks=0,
                                style={**_BTN, "marginBottom": "12px"}),
                    html.Div(id="history-output"),
                ]),
            ]),

            # ── Tab 4: sector overview + watchlist ───────────────────
            dcc.Tab(label="📋 板块行情 / 自选股", value="sector", children=[
                html.Div(style={"marginTop": "16px"}, children=[
                    # ─ Watchlist section ─
                    html.H4("⭐ 我的自选股", style={"marginBottom": "8px"}),
                    html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "12px"}, children=[
                        dcc.Input(
                            id="watchlist-add-input", type="text",
                            placeholder="输入代码或名称添加自选（如 600519、茅台）",
                            debounce=True,
                            style={"flex": "1", "padding": "8px", "fontSize": "14px"},
                        ),
                        html.Button("➕ 加入自选", id="watchlist-add-btn", n_clicks=0,
                                    style={**_BTN, "fontSize": "14px", "padding": "6px 16px"}),
                    ]),
                    dcc.Loading(type="circle", children=[
                        html.Div(id="watchlist-output"),
                    ]),

                    html.Hr(style={"margin": "20px 0"}),

                    # ─ Sector overview section ─
                    html.H4("📊 板块行情 — 成交额 TOP50", style={"marginBottom": "8px"}),
                    html.Div(id="sector-add-status", style={"marginBottom": "8px"}),
                    html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "12px",
                                    "alignItems": "center"}, children=[
                        dcc.Dropdown(
                            id="sector-dropdown",
                            options=[{"label": "全部 A 股", "value": ""}],
                            value="",
                            placeholder="选择板块…",
                            clearable=True,
                            style={"width": "220px"},
                        ),
                        html.Button("🔄 刷新行情", id="sector-refresh-btn", n_clicks=0,
                                    style={**_BTN, "fontSize": "14px", "padding": "6px 16px"}),
                    ]),
                    dcc.Loading(type="circle", children=[
                        html.Div(id="sector-output"),
                    ]),
                ]),
            ]),
        ]),

        html.Hr(),
        html.P(
            "仅供研究参考，不构成投资建议。请结合基本面与风险承受能力独立决策。",
            style={"color": "#888", "fontSize": "13px", "textAlign": "center"},
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Hot stocks quick-pick panel (loads when Tab 1 is active)
# ---------------------------------------------------------------------------


@callback(
    Output("hot-stocks-panel", "children"),
    Input("main-tabs", "value"),
    prevent_initial_call=False,
)
def load_hot_stocks(tab_value):
    """Show top 80 + sector picks for quick selection in single-stock analysis."""
    if tab_value != "single":
        return ""

    try:
        top_stocks = fetch_sector_stock_list(sector_code="", limit=80)
    except Exception:
        return ""

    if not top_stocks:
        return ""

    def _make_stock_btn(s: Quote) -> html.Button:
        raw_code = s.ticker.split(".")[0] if "." in s.ticker else s.ticker
        chg_color = "#16a34a" if s.change_pct >= 0 else "#dc2626"
        if s.turnover and s.turnover >= 1e8:
            tv = f"{s.turnover / 1e8:.1f}亿"
        else:
            tv = ""

        return html.Button(
            children=[
                html.Span(f"{s.name} ", style={"fontWeight": "bold", "fontSize": "12px"}),
                html.Span(f"{s.change_pct:+.1f}%",
                          style={"color": chg_color, "fontSize": "11px", "fontWeight": "bold"}),
                html.Span(f" {tv}" if tv else "",
                          style={"color": "#888", "fontSize": "10px"}),
            ],
            id={"type": "hot-pick", "index": raw_code},
            n_clicks=0,
            style={
                "padding": "4px 8px", "margin": "2px 4px 2px 0",
                "border": "1px solid #e2e8f0", "borderRadius": "6px",
                "cursor": "pointer", "background": "#fff",
                "display": "inline-flex", "alignItems": "center", "gap": "4px",
                "fontSize": "12px", "lineHeight": "1.4",
            },
        )

    top_items = [_make_stock_btn(s) for s in top_stocks]

    # 行业精选（每个行业取5只，参考MSCI常见行业维度）
    sector_sections: list = []
    sector_keywords = {
        "科技": ["科技", "电子"],
        "芯片": ["半导体", "芯片"],
        "制造": ["高端制造", "机械", "制造"],
        "软件": ["软件", "信创", "计算机"],
        "医药": ["医药", "医疗", "生物"],
    }

    try:
        sectors = fetch_sector_list()
    except Exception:
        sectors = []

    for label, keywords in sector_keywords.items():
        sector_code = ""
        for sec in sectors:
            sec_name = sec.get("name", "")
            if any(k in sec_name for k in keywords):
                sector_code = sec.get("code", "")
                break

        picks: list[Quote] = []
        if sector_code:
            try:
                picks = fetch_sector_stock_list(sector_code=sector_code, limit=5)
            except Exception:
                picks = []

        if picks:
            sector_sections.append(
                html.Div(style={"marginTop": "8px"}, children=[
                    html.Span(f"{label}精选5只：", style={"fontWeight": "bold", "fontSize": "12px", "color": "#334155"}),
                    html.Div(style={"display": "flex", "flexWrap": "wrap", "marginTop": "4px"},
                             children=[_make_stock_btn(s) for s in picks]),
                ])
            )

    return html.Div(style={
        "padding": "10px 12px", "background": "#fff",
        "borderRadius": "8px", "marginBottom": "12px",
        "boxShadow": "0 1px 4px rgba(0,0,0,.06)",
    }, children=[
        html.Div(style={"display": "flex", "alignItems": "center", "marginBottom": "6px"}, children=[
            html.Span("🔥 今日热门（成交额 TOP80）",
                       style={"fontWeight": "bold", "fontSize": "13px", "color": "#333"}),
            html.Span("  点击代码可直接分析 ↓",
                       style={"fontSize": "11px", "color": "#999"}),
        ]),
        html.Div(style={"display": "flex", "flexWrap": "wrap"}, children=top_items),
        html.Div(style={"marginTop": "8px", "fontSize": "11px", "color": "#64748b"},
                 children="附：行业精选参考MSCI常见行业维度（科技/芯片/制造/软件/医药）。"),
        *sector_sections,
    ])


@callback(
    Output("ticker-input", "value", allow_duplicate=True),
    Input({"type": "hot-pick", "index": dash.ALL}, "n_clicks"),
    State({"type": "hot-pick", "index": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def pick_hot_stock(n_clicks_list, id_list):
    """Fill ticker input when user clicks a hot stock button."""
    if not n_clicks_list or not any(n and n > 0 for n in n_clicks_list):
        return dash.no_update
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    click_val = ctx.triggered[0].get("value", 0)
    if not click_val or click_val <= 0:
        return dash.no_update
    prop_id = ctx.triggered[0]["prop_id"]
    try:
        import json as _json
        key = _json.loads(prop_id.rsplit(".", maxsplit=1)[0])
        return key["index"]
    except Exception:
        return dash.no_update


@callback(
    Output("search-results", "children"),
    Input("ticker-input", "value"),
    prevent_initial_call=True,
)
def live_search(query: str | None):
    """Show search hints as the user types Chinese keywords."""
    if not query or len(query.strip()) < 2:
        return ""

    # Only search when query looks like Chinese text (not pure digits)
    stripped = query.strip()
    if stripped.replace(".", "").isdigit():
        return ""

    results = search_instruments(stripped, limit=6)
    if not results:
        return html.Span("未找到匹配标的", style={"color": "#999"})

    buttons = []
    for r in results:
        buttons.append(
            html.Button(
                f"{r.name} ({r.code})",
                id={"type": "search-pick", "index": r.code},
                n_clicks=0,
                style={
                    "margin": "2px 6px 2px 0",
                    "padding": "4px 10px",
                    "border": "1px solid #ccc",
                    "borderRadius": "4px",
                    "cursor": "pointer",
                    "background": "#f5f5f5",
                },
            )
        )
    return html.Div(buttons)


@callback(
    Output("ticker-input", "value"),
    Input({"type": "search-pick", "index": dash.ALL}, "n_clicks"),
    State({"type": "search-pick", "index": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def pick_search_result(n_clicks_list, id_list):
    """Fill the input box when user clicks a search result."""
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    prop_id = ctx.triggered[0]["prop_id"]
    # prop_id looks like '{"index":"600519","type":"search-pick"}.n_clicks'
    import json

    try:
        key = json.loads(prop_id.rsplit(".", maxsplit=1)[0])
        return key["index"]
    except Exception:
        return dash.no_update


@callback(
    Output("quote-summary", "children"),
    Output("kline-chart", "figure"),
    Output("kline-chart", "style"),
    Output("intraday-chart-area", "children"),
    Output("recommendation-output", "children"),
    Input("analyze-btn", "n_clicks"),
    State("ticker-input", "value"),
    State("period-dropdown", "value"),
    prevent_initial_call=True,
)
def run_analysis(n_clicks: int, ticker: str | None, period: str):
    """Main analysis pipeline: quote, K-line (top), intraday chart, recommendation."""
    _empty = (
        html.Span("请输入股票代码或名称", style={"color": "orange"}),
        go.Figure(), {"display": "none"},
        "",
        "",
    )
    if not ticker or not ticker.strip():
        return _empty

    ticker = ticker.strip()

    def _err(msg: str):
        return (
            html.Span(f"分析失败：{msg}", style={"color": "red", "whiteSpace": "pre-wrap"}),
            go.Figure(), {"display": "none"},
            "",
            "",
        )

    try:
        # ── 1. Live quote ─────────────────────────────────────────────
        q = get_quote(ticker)
        chg_color = "#16a34a" if q.change >= 0 else "#dc2626"
        from datetime import datetime as _dt

        # Format turnover for display
        turnover_text = ""
        if q.turnover is not None:
            if q.turnover >= 1e8:
                turnover_text = f"成交额: {q.turnover / 1e8:.2f}亿"
            elif q.turnover >= 1e4:
                turnover_text = f"成交额: {q.turnover / 1e4:.2f}万"
            else:
                turnover_text = f"成交额: {q.turnover:.0f}元"

        quote_children = [
                html.Span(q.name, style={"fontSize": "20px", "fontWeight": "bold"}),
                html.Span(f"({q.ticker})", style={"color": "#888", "fontSize": "14px"}),
                html.Span(f"¥{q.price:.4f}",
                          style={"fontSize": "26px", "fontWeight": "bold", "color": chg_color}),
                html.Span(f"{q.change:+.4f}  {q.change_pct:+.2f}%",
                          style={"color": chg_color, "fontWeight": "bold"}),
        ]
        if turnover_text:
            quote_children.append(
                html.Span(turnover_text,
                          style={"color": "#2563eb", "fontWeight": "bold", "fontSize": "14px"}),
            )
        quote_children.append(
                html.Span(f"更新时间: {_dt.now().strftime('%H:%M:%S')}",
                          style={"color": "#999", "fontSize": "12px", "marginLeft": "auto"}),
        )

        quote_div = html.Div(
            style={
                "display": "flex", "gap": "20px", "alignItems": "baseline",
                "padding": "12px 16px", "background": "#fff",
                "borderRadius": "8px", "marginBottom": "4px",
                "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
                "flexWrap": "wrap",
            },
            children=quote_children,
        )

        # ── Company fundamentals info card ───────────────────────────
        company_items: list = []
        if q.market_cap_total is not None:
            company_items.append(
                html.Span(f"总市值: {q.market_cap_total:.0f}亿",
                          style={"fontSize": "13px", "color": "#374151", "padding": "3px 8px",
                                 "background": "#f1f5f9", "borderRadius": "4px"}))
        if q.market_cap_float is not None:
            company_items.append(
                html.Span(f"流通市值: {q.market_cap_float:.0f}亿",
                          style={"fontSize": "13px", "color": "#374151", "padding": "3px 8px",
                                 "background": "#f1f5f9", "borderRadius": "4px"}))
        if q.pe_ttm is not None:
            _pe_color = "#16a34a" if q.pe_ttm > 0 else "#dc2626"
            _pe_label = f"PE(TTM): {q.pe_ttm:.2f}" + ("" if q.pe_ttm > 0 else " 📉亏损")
            company_items.append(
                html.Span(_pe_label,
                          style={"fontSize": "13px", "color": _pe_color, "fontWeight": "bold",
                                 "padding": "3px 8px", "background": "#f1f5f9",
                                 "borderRadius": "4px"}))
        if q.pb_ratio is not None:
            company_items.append(
                html.Span(f"PB: {q.pb_ratio:.2f}",
                          style={"fontSize": "13px", "color": "#374151", "padding": "3px 8px",
                                 "background": "#f1f5f9", "borderRadius": "4px"}))
        if q.turnover_rate is not None:
            company_items.append(
                html.Span(f"换手率: {q.turnover_rate:.2f}%",
                          style={"fontSize": "13px", "color": "#2563eb", "padding": "3px 8px",
                                 "background": "#eff6ff", "borderRadius": "4px"}))
        company_div = html.Div(
            company_items,
            style={
                "display": "flex", "gap": "8px", "flexWrap": "wrap",
                "padding": "6px 16px", "background": "#fff",
                "borderRadius": "8px", "marginBottom": "8px",
                "boxShadow": "0 1px 4px rgba(0,0,0,.06)",
            },
        ) if company_items else html.Span()
        quote_summary_content = html.Div([quote_div, company_div])

        # ── 2. Realtime K-line (5min) below long-term K-line ─────────
        idf = get_intraday(ticker)
        if not idf.empty and "Price" in idf.columns:
            mdf = idf[["Price"]].copy()
            # 1分钟价格重采样为5分钟OHLC，形成“实时K线”
            ohlc = mdf["Price"].resample("5min").ohlc().dropna()
            if not ohlc.empty:
                rfig = go.Figure(go.Candlestick(
                    x=ohlc.index,
                    open=ohlc["open"],
                    high=ohlc["high"],
                    low=ohlc["low"],
                    close=ohlc["close"],
                    increasing_line_color="#16a34a",
                    decreasing_line_color="#dc2626",
                    name="实时K线(5m)",
                ))
                rfig.add_hline(
                    y=q.price, line_dash="dot", line_color=chg_color,
                    annotation_text=f"现价 ¥{q.price:.4f}",
                    annotation_position="bottom right",
                )
                rfig.update_layout(
                    title=f"{q.name} — 今日实时K线（5分钟）",
                    yaxis_title="价格（¥）",
                    xaxis_title="",
                    height=300,
                    margin={"t": 40, "b": 40, "l": 60, "r": 20},
                    template="plotly_white",
                    showlegend=False,
                    xaxis_rangeslider_visible=False,
                )
                intraday_div = dcc.Graph(figure=rfig)
            else:
                intraday_div = html.Div(
                    "⏸ 当前分钟数据不足，暂无法生成实时K线",
                    style={"color": "#999", "fontSize": "13px", "padding": "12px",
                           "background": "#f8fafc", "borderRadius": "6px",
                           "textAlign": "center", "marginTop": "8px"},
                )
        else:
            # Fallback: show last 10 trading-day daily K-line
            try:
                _fb_df = get_history(ticker, period="1mo", interval="1d")
            except Exception:
                _fb_df = None
            if _fb_df is not None and not _fb_df.empty and len(_fb_df) >= 3:
                _fb = _fb_df.tail(10)
                rfig_fb = go.Figure(go.Candlestick(
                    x=_fb.index,
                    open=_fb["Open"], high=_fb["High"],
                    low=_fb["Low"], close=_fb["Close"],
                    increasing_line_color="#16a34a",
                    decreasing_line_color="#dc2626",
                    name="近期日K",
                ))
                rfig_fb.update_layout(
                    title=f"{q.name} — 近10日K线（非交易时段）",
                    height=280,
                    margin={"t": 40, "b": 30, "l": 55, "r": 20},
                    template="plotly_white",
                    showlegend=False,
                    xaxis_rangeslider_visible=False,
                )
                intraday_div = dcc.Graph(figure=rfig_fb)
            else:
                intraday_div = html.Div(
                    "⏸ 当前非交易时段，暂无今日实时K线数据",
                    style={"color": "#999", "fontSize": "13px", "padding": "12px",
                           "background": "#f8fafc", "borderRadius": "6px",
                           "textAlign": "center", "marginTop": "8px"},
                )

        # ── 3. Historical K-line ──────────────────────────────────────
        df = get_history(ticker, period=period, interval="1d")
        if df.empty:
            return (
                quote_div,
                go.Figure(), {"display": "none"},
                intraday_div,
                html.Span("历史数据不足，无法生成 K 线图", style={"color": "orange"}),
            )

        # ── 4. Fundamentals + Recommendation ─────────────────────────
        fundamentals = get_fundamentals(ticker)
        pe = fundamentals.get("pe_ratio")
        pm = fundamentals.get("profit_margin")

        report = recommend_stock(ticker=ticker, period=period, pe_ratio=pe, profit_margin=pm)

        # ── 5. K-line figure ──────────────────────────────────────────
        kfig = go.Figure(go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
            name="K线",
        ))
        if report.buy_range:
            kfig.add_hrect(y0=report.buy_range[0], y1=report.buy_range[1],
                           fillcolor="rgba(22,163,74,0.12)", line_width=0,
                           annotation_text="买入区", annotation_position="top left")
        if report.sell_range:
            kfig.add_hrect(y0=report.sell_range[0], y1=report.sell_range[1],
                           fillcolor="rgba(220,38,38,0.12)", line_width=0,
                           annotation_text="卖出区", annotation_position="top left")
        if report.stop_loss:
            kfig.add_hline(y=report.stop_loss, line_dash="dash", line_color="red",
                           annotation_text=f"止损 ¥{report.stop_loss:.4f}")
        if report.ma20:
            kfig.add_hline(y=report.ma20, line_dash="dot", line_color="#f59e0b",
                           annotation_text=f"MA20 ¥{report.ma20:.4f}",
                           annotation_position="bottom right")
        if report.ma60:
            kfig.add_hline(y=report.ma60, line_dash="dot", line_color="#3b82f6",
                           annotation_text=f"MA60 ¥{report.ma60:.4f}",
                           annotation_position="bottom left")
        kfig.update_layout(
            title=f"{report.name} ({report.ticker}) — {period} K 线走势",
            yaxis_title="价格（¥）",
            xaxis_rangeslider_visible=False,
            height=420, template="plotly_white",
        )

        # ── 6. Save to history ────────────────────────────────────────
        try:
            add_history_record(
                ticker=report.ticker, name=report.name,
                action=report.action, sector=report.sector,
                price=report.current_price,
            )
        except Exception:
            pass

        # ── 7. Recommendation card ────────────────────────────────────
        action_color = {
            "可分批买入": "#16a34a", "可分批止盈": "#dc2626",
            "持有观察": "#ca8a04", "等待企稳": "#6b7280",
        }
        kind_label = {"fund": "基金", "star": "科创板", "stock": "股票"}.get(
            report.instrument_type, "股票"
        )

        def _row(label: str, value: str, bold: bool = False) -> html.Tr:
            return html.Tr([
                html.Td(label, style={"padding": "5px 12px", "fontWeight": "bold", "color": "#555",
                                      "whiteSpace": "nowrap"}),
                html.Td(value, style={"padding": "5px 12px",
                                      "fontWeight": "bold" if bold else "normal"}),
            ])

        rows = [
            _row("品种", f"{kind_label} · {report.sector}"),
            _row("行业成熟度", report.sector_maturity),
            _row("操作建议", report.action, bold=True),
            _row("市场趋势", report.market_view),
            _row("估值评价", report.valuation_view),
        ]
        for label, val in [
            ("买入区间", f"¥{report.buy_range[0]:.4f} ~ ¥{report.buy_range[1]:.4f}" if report.buy_range else None),
            ("卖出区间", f"¥{report.sell_range[0]:.4f} ~ ¥{report.sell_range[1]:.4f}" if report.sell_range else None),
            ("建议止损", f"¥{report.stop_loss:.4f}" if report.stop_loss else None),
            ("MA20", f"¥{report.ma20:.4f}" if report.ma20 else None),
            ("MA60", f"¥{report.ma60:.4f}" if report.ma60 else None),
            ("量比", f"{report.volume_ratio:.2f}x" if report.volume_ratio is not None else None),
            ("今日成交额", turnover_text if turnover_text else None),
            ("市盈率 P/E", f"{pe:.2f}" if pe is not None else None),
            ("利润率", f"{pm*100:.2f}%" if pm is not None else None),
            ("3月区间", f"¥{report.low_3m:.4f} ~ ¥{report.high_3m:.4f}" if report.low_3m and report.high_3m else None),
            ("6月区间", f"¥{report.low_6m:.4f} ~ ¥{report.high_6m:.4f}" if report.low_6m and report.high_6m else None),
        ]:
            if val:
                rows.append(_row(label, val))

        # benchmarks
        bm_children: list = []

        # ── Turnover advice ──────────────────────────────────────────
        turnover_children: list = []
        if q.turnover is not None and q.turnover > 0:
            # Compute 5-day average turnover from history
            avg_turnover = None
            if not df.empty and "Volume" in df.columns and "Close" in df.columns and len(df) >= 5:
                recent5 = df.tail(5)
                daily_turnovers = recent5["Close"] * recent5["Volume"] * 100  # 手→股
                avg_turnover = float(daily_turnovers.mean())

            turnover_detail: list[str] = []
            if q.turnover >= 1e8:
                turnover_detail.append(f"今日成交额 ¥{q.turnover/1e8:.2f}亿")
            else:
                turnover_detail.append(f"今日成交额 ¥{q.turnover/1e4:.2f}万")

            if avg_turnover and avg_turnover > 0:
                ratio = q.turnover / avg_turnover
                if avg_turnover >= 1e8:
                    turnover_detail.append(f"近5日均额 ¥{avg_turnover/1e8:.2f}亿")
                else:
                    turnover_detail.append(f"近5日均额 ¥{avg_turnover/1e4:.2f}万")
                turnover_detail.append(f"成交额比 {ratio:.2f}x")

                if ratio >= 2.0:
                    advice = "⚡ 资金显著放量，关注度大幅提升，注意跟踪主力动向"
                    adv_color = "#dc2626"
                elif ratio >= 1.5:
                    advice = "📈 资金较活跃，成交放量，有增量资金入场迹象"
                    adv_color = "#16a34a"
                elif ratio >= 0.8:
                    advice = "📊 成交正常，资金面稳定"
                    adv_color = "#6b7280"
                else:
                    advice = "📉 成交缩量，资金关注度下降，流动性偏低"
                    adv_color = "#ca8a04"

                turnover_children = [
                    html.Div(style={
                        "marginTop": "12px", "padding": "10px 14px",
                        "background": "#f8fafc", "borderRadius": "6px",
                        "border": f"1px solid {adv_color}40",
                    }, children=[
                        html.Span("💰 成交额分析：", style={"fontWeight": "bold", "fontSize": "13px"}),
                        html.Span(advice, style={"color": adv_color, "fontWeight": "bold",
                                                 "fontSize": "13px", "marginLeft": "8px"}),
                        html.Div(" | ".join(turnover_detail),
                                 style={"fontSize": "12px", "color": "#666", "marginTop": "4px"}),
                    ]),
                ]

        if report.benchmarks:
            bm_rows = [html.Tr([
                html.Th(h, style={"padding": "4px 8px", "textAlign": "left", "background": "#f8fafc"})
                for h in ["标的", "代码", "涨跌%", "趋势"]
            ])]
            for bm in report.benchmarks:
                chg = f"{bm.change_pct:+.2f}%" if bm.change_pct is not None else "N/A"
                bm_rows.append(html.Tr([
                    html.Td(bm.label, style={"padding": "4px 8px"}),
                    html.Td(bm.ticker, style={"padding": "4px 8px", "color": "#2563eb"}),
                    html.Td(chg, style={"padding": "4px 8px", "textAlign": "right"}),
                    html.Td(bm.trend, style={"padding": "4px 8px"}),
                ]))
            bm_children = [
                html.H4("🌐 关联海外标的", style={"marginTop": "16px"}),
                html.Table(bm_rows, style={"width": "100%", "borderCollapse": "collapse"}),
            ]

        # SWOT
        swot_children: list = []
        if report.swot:
            def _swot_box(title: str, items: list, color: str) -> html.Div:
                return html.Div(style={
                    "flex": "1", "minWidth": "180px", "padding": "8px",
                    "border": f"1px solid {color}", "borderRadius": "6px", "margin": "4px",
                }, children=[
                    html.Strong(title, style={"color": color}),
                    html.Ul([html.Li(i, style={"fontSize": "12px"}) for i in items],
                            style={"margin": "4px 0", "paddingLeft": "16px"}),
                ])
            swot_children = [
                html.H4("📊 SWOT 分析", style={"marginTop": "16px"}),
                html.Div(style={"display": "flex", "flexWrap": "wrap"}, children=[
                    _swot_box("Strengths 优势", report.swot.strengths, "#16a34a"),
                    _swot_box("Weaknesses 劣势", report.swot.weaknesses, "#dc2626"),
                    _swot_box("Opportunities 机会", report.swot.opportunities, "#2563eb"),
                    _swot_box("Threats 威胁", report.swot.threats, "#ca8a04"),
                ]),
            ]

        # ── Strategy signals section ──────────────────────────────────
        strategy_children: list = []
        if report.strategies:
            sr = report.strategies

            def _score_badge(label: str, signal: str, score: float) -> html.Div:
                sig_color = {
                    "买入": "#16a34a", "反弹机会": "#2563eb",
                    "综合买入信号": "#16a34a", "可分批建仓": "#16a34a",
                    "观望偏多": "#ca8a04", "持有观察": "#6b7280",
                    "观望": "#94a3b8", "卖出": "#dc2626",
                }.get(signal, "#6b7280")
                bar_w = f"{score:.0f}%"
                return html.Div(style={
                    "flex": "1", "minWidth": "200px", "padding": "10px",
                    "border": f"1px solid {sig_color}", "borderRadius": "8px",
                    "margin": "4px", "background": "#fafafa",
                }, children=[
                    html.Div(style={"display": "flex", "justifyContent": "space-between"}, children=[
                        html.Strong(label, style={"fontSize": "12px", "color": "#333"}),
                        html.Span(signal, style={"color": sig_color, "fontWeight": "bold",
                                                 "fontSize": "12px"}),
                    ]),
                    # Score bar
                    html.Div(style={"background": "#e2e8f0", "borderRadius": "4px",
                                    "height": "6px", "margin": "6px 0"}, children=[
                        html.Div(style={
                            "width": bar_w, "height": "100%",
                            "background": sig_color, "borderRadius": "4px",
                        }),
                    ]),
                    html.Span(f"评分 {score:.0f}/100",
                              style={"fontSize": "11px", "color": "#888"}),
                ])

            composite_color = "#16a34a" if sr.combined_score >= 65 else (
                "#ca8a04" if sr.combined_score >= 50 else "#94a3b8"
            )

            strategy_children = [
                html.H4("🎯 策略信号", style={"marginTop": "16px", "marginBottom": "8px"}),
                html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "4px"}, children=[
                    _score_badge("① 均线多头+量比", sr.strategy1.signal, sr.strategy1.score),
                    _score_badge("② RSI超卖+乖离率", sr.strategy2.signal, sr.strategy2.score),
                    _score_badge("③ 宏观+行业共振", sr.strategy3.signal, sr.strategy3.score),
                ]),
                html.Div(style={
                    "display": "flex", "alignItems": "center", "gap": "12px",
                    "marginTop": "8px", "padding": "8px 12px",
                    "background": "#f1f5f9", "borderRadius": "6px",
                }, children=[
                    html.Strong("综合评分：", style={"fontSize": "13px"}),
                    html.Div(style={"flex": "1", "background": "#e2e8f0",
                                    "borderRadius": "6px", "height": "10px"}, children=[
                        html.Div(style={
                            "width": f"{sr.combined_score:.0f}%",
                            "height": "100%", "background": composite_color,
                            "borderRadius": "6px",
                        }),
                    ]),
                    html.Span(f"{sr.combined_score:.0f}/100",
                              style={"color": composite_color, "fontWeight": "bold", "fontSize": "14px"}),
                    html.Span(f"→ {sr.composite_action}",
                              style={"color": composite_color, "fontWeight": "bold", "fontSize": "13px"}),
                ]),
                # Position sizing advice
                html.Div(style={
                    "marginTop": "8px", "padding": "8px 12px",
                    "background": "#f0fdf4" if sr.position_pct >= 30 else (
                        "#fefce8" if sr.position_pct >= 15 else "#f8fafc"
                    ),
                    "borderRadius": "6px",
                    "border": "1px solid " + (
                        "#bbf7d0" if sr.position_pct >= 30 else
                        "#fde68a" if sr.position_pct >= 15 else "#e2e8f0"
                    ),
                }, children=[
                    html.Span("💼 仓位建议：",
                              style={"fontWeight": "bold", "fontSize": "13px"}),
                    html.Span(
                        f"{sr.position_pct}% 仓位" if sr.position_pct > 0 else "暂不建仓",
                        style={
                            "fontWeight": "bold", "fontSize": "14px",
                            "color": "#16a34a" if sr.position_pct >= 30 else
                                     "#ca8a04" if sr.position_pct >= 15 else "#6b7280",
                            "marginRight": "8px",
                        },
                    ),
                    html.Span(sr.position_note,
                              style={"fontSize": "12px", "color": "#555"}),
                ]),
                # Sector heat & northbound
                html.Div(style={"marginTop": "6px", "fontSize": "12px", "color": "#555"}, children=[
                    html.Span(f"板块热度：{'命中热门板块 ✅' if sr.in_hot_sector else '未命中热门板块'}  "),
                    html.Span(f"北向情绪代理：{sr.northbound_sentiment}  "),
                    html.Span(
                        f"今日热门板块前5：{', '.join(sr.hot_sectors[:5])}" if sr.hot_sectors
                        else "热门板块数据暂不可用",
                        style={"color": "#888"},
                    ),
                ]),
                # Strategy 3 detail reasoning
                html.Details(style={"marginTop": "8px"}, children=[
                    html.Summary("查看宏观共振分析详情",
                                 style={"cursor": "pointer", "color": "#2563eb", "fontSize": "12px"}),
                    html.Ul([
                        html.Li(d, style={"fontSize": "12px", "lineHeight": "1.8"})
                        for d in sr.strategy3.details
                    ], style={"marginTop": "4px", "paddingLeft": "20px"}),
                ]),
            ]

        rec_div = html.Div(
            style={**_CARD, "borderLeft": f"4px solid {action_color.get(report.action, '#2563eb')}"},
            children=[
                html.H3(f"📌 {kind_label}综合建议", style={"marginTop": 0}),
                html.Table(rows, style={"width": "100%", "borderCollapse": "collapse"}),
                *turnover_children,
                *strategy_children,
                *bm_children,
                *swot_children,
                html.H4("分析理由", style={"marginTop": "16px"}),
                html.Ul([html.Li(r) for r in report.reasons[:10]]),
                html.P("基金采用分位区间策略（P20-P35买/P75-P90卖），股票采用支撑/压力位策略。",
                       style={"color": "#999", "fontSize": "12px", "marginTop": "8px"}),
            ],
        )

        return quote_summary_content, kfig, {"display": "block"}, intraday_div, rec_div

    except Exception:
        return _err(traceback.format_exc())


# ---------------------------------------------------------------------------
# Batch callback
# ---------------------------------------------------------------------------

@callback(
    Output("batch-output", "children"),
    Input("batch-btn", "n_clicks"),
    State("batch-input", "value"),
    State("batch-period", "value"),
    prevent_initial_call=True,
)
def run_batch(n_clicks: int, raw_input: str | None, period: str):
    """Analyse up to 10 tickers and show a summary table + mini cards."""
    if not raw_input or not raw_input.strip():
        return html.Span("请输入股票代码", style={"color": "orange"})

    import re
    tickers = re.split(r"[\s,，;；]+", raw_input.strip())
    tickers = [t.strip() for t in tickers if t.strip()][:10]

    if not tickers:
        return html.Span("未识别到代码", style={"color": "orange"})

    action_color = {
        "可分批买入": "#16a34a", "可分批止盈": "#dc2626",
        "持有观察": "#ca8a04", "等待企稳": "#6b7280",
    }

    # ── Summary table ─────────────────────────────────────────────────
    thead = html.Tr([
        html.Th(h, style={"padding": "6px 10px", "background": "#f1f5f9",
                           "fontWeight": "bold", "whiteSpace": "nowrap"})
        for h in ["代码", "名称", "现价", "涨跌%", "操作建议", "策略评分", "行业", "买入区间", "卖出区间", "止损"]
    ])

    tbody_rows = []
    cards = []
    for tk in tickers:
        # Resolve names/abbreviations to 6-digit codes first
        resolved = resolve_ticker(tk)
        try:
            q = get_quote(resolved)
            fundamentals = get_fundamentals(resolved)
            report = recommend_stock(
                ticker=resolved, period=period,
                pe_ratio=fundamentals.get("pe_ratio"),
                profit_margin=fundamentals.get("profit_margin"),
            )
            # save to history
            try:
                add_history_record(
                    ticker=report.ticker, name=report.name,
                    action=report.action, sector=report.sector,
                    price=report.current_price,
                )
            except Exception:
                pass

            chg_color = "#16a34a" if q.change_pct >= 0 else "#dc2626"
            ac = action_color.get(report.action, "#2563eb")
            buy_str = (f"¥{report.buy_range[0]:.2f}~{report.buy_range[1]:.2f}"
                       if report.buy_range else "—")
            sell_str = (f"¥{report.sell_range[0]:.2f}~{report.sell_range[1]:.2f}"
                        if report.sell_range else "—")
            stop_str = f"¥{report.stop_loss:.2f}" if report.stop_loss else "—"
            strat_score = (
                f"{report.strategies.combined_score:.0f}/100 {report.strategies.composite_action}"
                if report.strategies else "—"
            )
            strat_color = (
                "#16a34a" if report.strategies and report.strategies.combined_score >= 65
                else "#ca8a04" if report.strategies and report.strategies.combined_score >= 50
                else "#94a3b8"
            ) if report.strategies else "#94a3b8"

            tbody_rows.append(html.Tr([
                html.Td(report.ticker, style={"padding": "5px 10px", "color": "#2563eb", "fontWeight": "bold"}),
                html.Td(report.name, style={"padding": "5px 10px"}),
                html.Td(f"¥{report.current_price:.4f}", style={"padding": "5px 10px"}),
                html.Td(f"{q.change_pct:+.2f}%", style={"padding": "5px 10px", "color": chg_color, "fontWeight": "bold"}),
                html.Td(report.action, style={"padding": "5px 10px", "color": ac, "fontWeight": "bold"}),
                html.Td(strat_score, style={"padding": "5px 10px", "color": strat_color, "fontSize": "12px"}),
                html.Td(report.sector, style={"padding": "5px 10px", "fontSize": "12px"}),
                html.Td(buy_str, style={"padding": "5px 10px", "color": "#16a34a", "fontSize": "12px"}),
                html.Td(sell_str, style={"padding": "5px 10px", "color": "#dc2626", "fontSize": "12px"}),
                html.Td(stop_str, style={"padding": "5px 10px", "fontSize": "12px"}),
            ]))

            # mini SWOT card
            swot_text = ""
            if report.swot:
                swot_text = " | ".join(report.swot.strengths[:1] + report.swot.opportunities[:1])

            cards.append(html.Div(style={
                **_CARD,
                "borderLeft": f"4px solid {ac}",
                "marginBottom": "8px",
            }, children=[
                html.Div(style={"display": "flex", "justifyContent": "space-between",
                                "alignItems": "center"}, children=[
                    html.Span(f"{report.name} ({report.ticker})",
                              style={"fontWeight": "bold", "fontSize": "15px"}),
                    html.Span(report.action, style={"color": ac, "fontWeight": "bold"}),
                    html.Span(f"¥{report.current_price:.4f}  {q.change_pct:+.2f}%",
                              style={"color": chg_color}),
                ]),
                html.Div(f"行业：{report.sector}（{report.sector_maturity}） | "
                         f"买入：{buy_str}  卖出：{sell_str}  止损：{stop_str}",
                         style={"fontSize": "12px", "color": "#666", "marginTop": "4px"}),
                html.Div(swot_text, style={"fontSize": "12px", "color": "#888", "marginTop": "2px"}),
            ]))

        except Exception as exc:
            display_code = resolved if resolved != tk else tk
            tbody_rows.append(html.Tr([
                html.Td(display_code, style={"padding": "5px 10px", "color": "#2563eb"}),
                html.Td(f"({tk})" if resolved != tk else "", style={"padding": "5px 10px", "color": "#888", "fontSize": "11px"}),
                *[html.Td("—", style={"padding": "5px 10px"}) for _ in range(7)],
                html.Td(str(exc)[:50], style={"padding": "5px 10px", "color": "red", "fontSize": "11px"}),
            ]))

    summary_table = html.Div(style={"overflowX": "auto", "marginBottom": "16px"}, children=[
        html.Table(
            [thead] + tbody_rows,
            style={"width": "100%", "borderCollapse": "collapse",
                   "border": "1px solid #e2e8f0"},
        )
    ])

    return html.Div([
        html.H4(f"批量分析结果（{len(tickers)} 支）"),
        summary_table,
        html.H4("详细卡片"),
        *cards,
    ])


# ---------------------------------------------------------------------------
# History callback
# ---------------------------------------------------------------------------

@callback(
    Output("history-output", "children"),
    Input("refresh-history-btn", "n_clicks"),
    Input("main-tabs", "value"),
    prevent_initial_call=False,
)
def show_history(n_clicks, tab_value):
    """Render the search history table."""
    records = get_recent_tickers(limit=50)
    if not records:
        return html.Span("暂无历史记录。分析股票后将自动记录。",
                         style={"color": "#999"})

    action_color = {
        "可分批买入": "#16a34a", "可分批止盈": "#dc2626",
        "持有观察": "#ca8a04", "等待企稳": "#6b7280",
    }

    thead = html.Tr([
        html.Th(h, style={"padding": "6px 12px", "background": "#f1f5f9",
                           "fontWeight": "bold", "textAlign": "left"})
        for h in ["时间", "代码", "名称", "现价(记录时)", "操作建议", "行业", "重新分析"]
    ])

    tbody_rows = []
    for rec in records:
        ac = action_color.get(rec.action, "#666")
        price_str = f"¥{rec.price:.4f}" if rec.price is not None else "—"
        tbody_rows.append(html.Tr([
            html.Td(rec.timestamp[:16], style={"padding": "5px 12px", "color": "#888",
                                               "fontSize": "12px"}),
            html.Td(rec.ticker, style={"padding": "5px 12px", "color": "#2563eb",
                                       "fontWeight": "bold"}),
            html.Td(rec.name, style={"padding": "5px 12px"}),
            html.Td(price_str, style={"padding": "5px 12px"}),
            html.Td(rec.action, style={"padding": "5px 12px", "color": ac, "fontWeight": "bold"}),
            html.Td(rec.sector, style={"padding": "5px 12px", "fontSize": "12px"}),
            html.Td(
                html.Span(
                    rec.ticker,
                    title="复制代码到个股分析樿就可重新分析",
                    style={
                        "color": "#2563eb", "fontSize": "12px",
                        "cursor": "copy", "fontFamily": "monospace",
                        "background": "#eff6ff", "padding": "2px 6px",
                        "borderRadius": "4px", "border": "1px solid #bfdbfe",
                    },
                ),
                style={"padding": "5px 12px"},
            ),
        ]))

    return html.Div([
        html.P(f"共 {len(records)} 条记录（最多显示50条，最新在前）",
               style={"color": "#888", "fontSize": "12px", "marginBottom": "8px"}),
        html.Div(style={"overflowX": "auto"}, children=[
            html.Table(
                [thead] + tbody_rows,
                style={"width": "100%", "borderCollapse": "collapse",
                       "border": "1px solid #e2e8f0"},
            )
        ]),
    ])


# ---------------------------------------------------------------------------
# Sector overview callback
# ---------------------------------------------------------------------------


@callback(
    Output("sector-dropdown", "options"),
    Input("main-tabs", "value"),
    prevent_initial_call=False,
)
def load_sector_options(tab_value):
    """Populate sector dropdown when tab is opened."""
    base_options = [{"label": "全部 A 股（成交额排名）", "value": ""}]
    if tab_value != "sector":
        return base_options

    try:
        sectors = fetch_sector_list()
        for s in sectors:
            base_options.append({"label": s["name"], "value": s["code"]})
    except Exception:
        pass
    return base_options


@callback(
    Output("sector-output", "children"),
    Output("sector-add-status", "children"),
    Input("sector-refresh-btn", "n_clicks"),
    Input("sector-dropdown", "value"),
    Input("main-tabs", "value"),
    prevent_initial_call=False,
)
def refresh_sector(n_clicks, sector_code, tab_value):
    """Show top 50 stocks by turnover for the selected sector/market."""
    if tab_value != "sector":
        return "", ""

    try:
        stocks = fetch_sector_stock_list(sector_code=sector_code or "", limit=50)
    except Exception:
        return html.Span("加载板块行情失败，请稍后重试", style={"color": "red"}), ""

    if not stocks:
        return html.Span("暂无数据", style={"color": "#999"}), ""

    thead = html.Tr([
        html.Th(h, style={"padding": "6px 10px", "background": "#f1f5f9",
                           "fontWeight": "bold", "whiteSpace": "nowrap", "textAlign": "left"})
        for h in ["序号", "代码", "名称", "最新价", "涨跌幅", "成交额", "加入自选"]
    ])

    tbody_rows = []
    for idx, s in enumerate(stocks, 1):
        chg_color = "#16a34a" if s.change_pct >= 0 else "#dc2626"
        if s.turnover and s.turnover >= 1e8:
            tv_text = f"{s.turnover / 1e8:.2f}亿"
        elif s.turnover and s.turnover >= 1e4:
            tv_text = f"{s.turnover / 1e4:.2f}万"
        elif s.turnover:
            tv_text = f"{s.turnover:.0f}"
        else:
            tv_text = "—"

        # Extract 6-digit code from normalized ticker
        raw_code = s.ticker.split(".")[0] if "." in s.ticker else s.ticker

        tbody_rows.append(html.Tr([
            html.Td(str(idx), style={"padding": "5px 10px", "color": "#888", "fontSize": "12px"}),
            html.Td(raw_code, style={"padding": "5px 10px", "color": "#2563eb",
                                     "fontWeight": "bold", "fontFamily": "monospace"}),
            html.Td(s.name, style={"padding": "5px 10px"}),
            html.Td(f"¥{s.price:.2f}", style={"padding": "5px 10px", "fontWeight": "bold"}),
            html.Td(f"{s.change_pct:+.2f}%", style={"padding": "5px 10px", "color": chg_color,
                                                      "fontWeight": "bold"}),
            html.Td(tv_text, style={"padding": "5px 10px", "color": "#2563eb", "fontSize": "13px"}),
            html.Td(
                html.Button("⭐加自选", id={"type": "sector-add-wl", "index": f"{raw_code}|{s.name}"},
                            n_clicks=0,
                            style={"padding": "2px 8px", "fontSize": "12px", "cursor": "pointer",
                                   "border": "1px solid #cbd5e1", "borderRadius": "4px",
                                   "background": "#fefce8"}),
                style={"padding": "5px 10px"},
            ),
        ]))

    return html.Div([
        html.P(f"共 {len(stocks)} 只股票（按成交额排序）",
               style={"color": "#888", "fontSize": "12px", "marginBottom": "6px"}),
        html.Div(style={"overflowX": "auto"}, children=[
            html.Table(
                [thead] + tbody_rows,
                style={"width": "100%", "borderCollapse": "collapse",
                       "border": "1px solid #e2e8f0"},
            )
        ]),
    ]), ""


@callback(
    Output("sector-add-status", "children", allow_duplicate=True),
    Input({"type": "sector-add-wl", "index": dash.ALL}, "n_clicks"),
    State({"type": "sector-add-wl", "index": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def add_from_sector(n_clicks_list, id_list):
    """Handle add-to-watchlist button clicks from sector table."""
    if not n_clicks_list or not any(n and n > 0 for n in n_clicks_list):
        return dash.no_update

    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update

    # Parse the triggered button's index directly from prop_id
    prop_id = ctx.triggered[0]["prop_id"]
    try:
        import json as _json
        raw_id = prop_id.rsplit(".", maxsplit=1)[0]
        key = _json.loads(raw_id)
        if key.get("type") != "sector-add-wl":
            return dash.no_update
        parts = key["index"].split("|", maxsplit=1)
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else code
        add_to_watchlist(code, name)
        return html.Span(
            f"[+] 已将 {name}({code}) 加入自选，可继续添加其它股票",
            style={"color": "#16a34a", "fontWeight": "bold", "fontSize": "13px"},
        )
    except Exception:
        return html.Span("添加失败，请重试", style={"color": "#dc2626", "fontSize": "13px"})


# ---------------------------------------------------------------------------
# Watchlist callbacks
# ---------------------------------------------------------------------------


@callback(
    Output("watchlist-output", "children"),
    Input("watchlist-add-btn", "n_clicks"),
    Input("main-tabs", "value"),
    State("watchlist-add-input", "value"),
    prevent_initial_call=False,
)
def refresh_watchlist(n_clicks, tab_value, add_input):
    """Display watchlist with live prices; handle adding new stocks."""
    ctx = dash.callback_context
    triggered = ctx.triggered[0]["prop_id"] if ctx.triggered else ""

    # Handle add
    if "watchlist-add-btn" in triggered and add_input and add_input.strip():
        try:
            code = resolve_ticker(add_input.strip())
            q = get_quote(code)
            add_to_watchlist(q.ticker, q.name)
        except Exception:
            pass

    # Always render current watchlist
    items = get_watchlist_items()
    if not items:
        return html.Span("自选股为空。使用上方输入框添加，或从板块行情点击 ⭐ 加入。",
                         style={"color": "#999", "fontSize": "13px"})

    # Fetch live quotes for all watchlist items
    codes = [item.ticker.split(".")[0] if "." in item.ticker else item.ticker for item in items]
    try:
        live_quotes = get_batch_quotes_tencent(codes)
    except Exception:
        live_quotes = []

    # Build a lookup by 6-digit code
    live_map: dict[str, "Quote"] = {}
    for lq in live_quotes:
        raw = lq.ticker.split(".")[0] if "." in lq.ticker else lq.ticker
        live_map[raw] = lq

    thead = html.Tr([
        html.Th(h, style={"padding": "6px 10px", "background": "#f1f5f9",
                           "fontWeight": "bold", "whiteSpace": "nowrap", "textAlign": "left"})
        for h in ["代码", "名称", "最新价", "涨跌幅", "成交额", "操作"]
    ])

    tbody_rows = []
    for item in reversed(items):  # newest first
        raw_code = item.ticker.split(".")[0] if "." in item.ticker else item.ticker
        lq = live_map.get(raw_code)

        if lq:
            chg_color = "#16a34a" if lq.change_pct >= 0 else "#dc2626"
            price_str = f"¥{lq.price:.2f}"
            chg_str = f"{lq.change_pct:+.2f}%"
            if lq.turnover and lq.turnover >= 1e8:
                tv_str = f"{lq.turnover / 1e8:.2f}亿"
            elif lq.turnover and lq.turnover >= 1e4:
                tv_str = f"{lq.turnover / 1e4:.2f}万"
            else:
                tv_str = "—"
        else:
            chg_color = "#888"
            price_str = "—"
            chg_str = "—"
            tv_str = "—"

        tbody_rows.append(html.Tr([
            html.Td(raw_code, style={"padding": "5px 10px", "color": "#2563eb",
                                     "fontWeight": "bold", "fontFamily": "monospace"}),
            html.Td(lq.name if lq else item.name,
                    style={"padding": "5px 10px"}),
            html.Td(price_str, style={"padding": "5px 10px", "fontWeight": "bold"}),
            html.Td(chg_str, style={"padding": "5px 10px", "color": chg_color, "fontWeight": "bold"}),
            html.Td(tv_str, style={"padding": "5px 10px", "color": "#2563eb", "fontSize": "13px"}),
            html.Td(
                html.Button("❌ 移除", id={"type": "wl-remove", "index": raw_code},
                            n_clicks=0,
                            style={"padding": "2px 8px", "fontSize": "12px", "cursor": "pointer",
                                   "border": "1px solid #fca5a5", "borderRadius": "4px",
                                   "background": "#fef2f2", "color": "#dc2626"}),
                style={"padding": "5px 10px"},
            ),
        ]))

    return html.Div([
        html.P(f"共 {len(items)} 只自选股",
               style={"color": "#888", "fontSize": "12px", "marginBottom": "6px"}),
        html.Div(style={"overflowX": "auto"}, children=[
            html.Table(
                [thead] + tbody_rows,
                style={"width": "100%", "borderCollapse": "collapse",
                       "border": "1px solid #e2e8f0"},
            )
        ]),
    ])


@callback(
    Output("watchlist-output", "children", allow_duplicate=True),
    Input({"type": "wl-remove", "index": dash.ALL}, "n_clicks"),
    State({"type": "wl-remove", "index": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def remove_from_watchlist_cb(n_clicks_list, id_list):
    """Handle remove from watchlist."""
    # Guard: only act if a button was actually clicked (not just re-rendered)
    if not n_clicks_list or not any(n and n > 0 for n in n_clicks_list):
        return dash.no_update

    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update

    triggered_id = getattr(ctx, "triggered_id", None)
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "wl-remove":
        return dash.no_update

    # Find which button was clicked (the one with n_clicks > 0 in triggered)
    prop_id = ctx.triggered[0]["prop_id"]
    click_val = ctx.triggered[0].get("value", 0)
    if not click_val or click_val <= 0:
        return dash.no_update

    try:
        import json as _json
        key = _json.loads(prop_id.rsplit(".", maxsplit=1)[0])
        code = key["index"]
        remove_from_watchlist(code)
        # Re-render the full watchlist
        items = get_watchlist_items()
        if not items:
            return html.Span("自选股已清空", style={"color": "#999"})

        # Fetch live quotes
        codes = [it.ticker.split(".")[0] if "." in it.ticker else it.ticker for it in items]
        try:
            live_quotes = get_batch_quotes_tencent(codes)
        except Exception:
            live_quotes = []
        live_map: dict[str, Quote] = {}
        for lq in live_quotes:
            raw = lq.ticker.split(".")[0] if "." in lq.ticker else lq.ticker
            live_map[raw] = lq

        thead = html.Tr([
            html.Th(h, style={"padding": "6px 10px", "background": "#f1f5f9",
                               "fontWeight": "bold", "whiteSpace": "nowrap", "textAlign": "left"})
            for h in ["代码", "名称", "最新价", "涨跌幅", "成交额", "操作"]
        ])
        tbody_rows = []
        for item in reversed(items):
            raw_code = item.ticker.split(".")[0] if "." in item.ticker else item.ticker
            lq = live_map.get(raw_code)
            if lq:
                chg_c = "#16a34a" if lq.change_pct >= 0 else "#dc2626"
                price_s = f"¥{lq.price:.2f}"
                chg_s = f"{lq.change_pct:+.2f}%"
                tv_s = (f"{lq.turnover/1e8:.2f}亿" if lq.turnover and lq.turnover >= 1e8
                        else f"{lq.turnover/1e4:.2f}万" if lq.turnover and lq.turnover >= 1e4
                        else "—")
            else:
                chg_c, price_s, chg_s, tv_s = "#888", "—", "—", "—"
            tbody_rows.append(html.Tr([
                html.Td(raw_code, style={"padding": "5px 10px", "color": "#2563eb",
                                         "fontWeight": "bold", "fontFamily": "monospace"}),
                html.Td(lq.name if lq else item.name, style={"padding": "5px 10px"}),
                html.Td(price_s, style={"padding": "5px 10px", "fontWeight": "bold"}),
                html.Td(chg_s, style={"padding": "5px 10px", "color": chg_c, "fontWeight": "bold"}),
                html.Td(tv_s, style={"padding": "5px 10px", "color": "#2563eb", "fontSize": "13px"}),
                html.Td(
                    html.Button("❌ 移除", id={"type": "wl-remove", "index": raw_code},
                                n_clicks=0,
                                style={"padding": "2px 8px", "fontSize": "12px", "cursor": "pointer",
                                       "border": "1px solid #fca5a5", "borderRadius": "4px",
                                       "background": "#fef2f2", "color": "#dc2626"}),
                    style={"padding": "5px 10px"},
                ),
            ]))
        return html.Div([
            html.P(f"✅ 已移除 {code} — 共 {len(items)} 只自选股",
                   style={"color": "#16a34a", "fontSize": "12px", "marginBottom": "6px"}),
            html.Div(style={"overflowX": "auto"}, children=[
                html.Table([thead] + tbody_rows,
                           style={"width": "100%", "borderCollapse": "collapse",
                                  "border": "1px solid #e2e8f0"})
            ]),
        ])
    except Exception:
        return dash.no_update


# ---------------------------------------------------------------------------
# News panel callback
# ---------------------------------------------------------------------------

def _tag_badge(tag: str) -> html.Span:
    """Colored badge for a news tag."""
    _colors = {
        "快讯": "#2563eb", "研报": "#7c3aed", "宏观": "#ca8a04",
        "地缘": "#dc2626", "政治": "#b45309", "大宗": "#0891b2",
        "国际": "#374151", "机构观点": "#16a34a",
    }
    c = _colors.get(tag, "#6b7280")
    return html.Span(tag, style={
        "fontSize": "11px", "color": "#fff", "background": c,
        "padding": "1px 6px", "borderRadius": "3px", "marginRight": "4px",
        "whiteSpace": "nowrap",
    })


def _news_item_row(item) -> html.Div:
    return html.Div(style={
        "padding": "7px 0", "borderBottom": "1px solid #f1f5f9",
        "display": "flex", "alignItems": "flex-start", "gap": "6px",
    }, children=[
        _tag_badge(item.tag),
        html.Div(style={"flex": "1"}, children=[
            html.A(item.title or "（无标题）", href=item.url, target="_blank",
                   style={"color": "#1e3a5f", "fontSize": "13px",
                          "textDecoration": "none", "lineHeight": "1.5"}),
            html.Span(
                f" {item.source}  {item.date}",
                style={"color": "#aaa", "fontSize": "11px", "marginLeft": "6px"},
            ),
        ]),
    ])


@callback(
    Output("news-panel", "children"),
    Input("news-interval", "n_intervals"),
)
def refresh_news(n_intervals: int):
    """Load CN market news + research reports + global macro/institution news."""
    cn_items = fetch_cn_news(10)
    research_items = fetch_research_reports(6)
    global_items = fetch_global_events(8)

    _panel_card = {
        "flex": "1", "minWidth": "280px",
        "padding": "14px 16px", "background": "#fff",
        "borderRadius": "8px", "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
    }

    cn_col = html.Div(style=_panel_card, children=[
        html.H4("📰 国内市场动态",
                style={"marginTop": 0, "marginBottom": "8px",
                       "borderBottom": "2px solid #2563eb", "paddingBottom": "6px"}),
        *([_news_item_row(it) for it in cn_items] if cn_items else [
            html.P("暂无数据", style={"color": "#aaa", "fontSize": "12px"})
        ]),
    ])

    report_rows = [_news_item_row(it) for it in research_items]
    global_rows = [_news_item_row(it) for it in global_items]

    global_col = html.Div(style=_panel_card, children=[
        html.H4("🌐 研报 & 国际机构观点",
                style={"marginTop": 0, "marginBottom": "8px",
                       "borderBottom": "2px solid #7c3aed", "paddingBottom": "6px"}),
        html.P("📄 最新研报", style={"fontWeight": "bold", "fontSize": "12px",
                                    "color": "#555", "margin": "4px 0"}),
        *(report_rows if report_rows else [html.P("暂无研报",
                                                  style={"color": "#aaa", "fontSize": "12px"})]),
        html.Hr(style={"margin": "8px 0", "borderColor": "#f1f5f9"}),
        html.P("🌍 宏观与地缘动态", style={"fontWeight": "bold", "fontSize": "12px",
                                           "color": "#555", "margin": "4px 0"}),
        *(global_rows if global_rows else [html.P("暂无数据",
                                                  style={"color": "#aaa", "fontSize": "12px"})]),
    ])

    return html.Div(style={
        "display": "flex", "gap": "16px", "flexWrap": "wrap",
    }, children=[cn_col, global_col])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_server(host: str = "0.0.0.0", port: int = 8050, debug: bool = False) -> None:
    """Start the Dash development server.

    Binds to 0.0.0.0 by default so the dashboard is reachable from any device
    on the local network (LAN/Wi-Fi).  Pass host='127.0.0.1' to restrict to
    localhost only.
    """
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server(debug=True)
