"""Chart utilities for displaying candlestick (K-line) views."""

from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle


def show_kline_chart(df: pd.DataFrame, ticker: str, title: str | None = None) -> None:
    """Display a simple candlestick chart using OHLCV DataFrame."""
    if df.empty:
        raise ValueError("No historical data available for chart")

    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(df.columns)):
        raise ValueError("Historical data must include Open/High/Low/Close columns")

    plot_df = df.copy()
    if not isinstance(plot_df.index, pd.DatetimeIndex):
        plot_df.index = pd.to_datetime(plot_df.index)

    fig, ax = plt.subplots(figsize=(12, 6))
    x_values = mdates.date2num(plot_df.index.to_pydatetime())
    candle_width = 0.6

    for x, row in zip(x_values, plot_df.itertuples()):
        color = "#16a34a" if row.Close >= row.Open else "#dc2626"
        ax.vlines(x, row.Low, row.High, color=color, linewidth=1.0)

        body_low = min(row.Open, row.Close)
        body_height = abs(row.Close - row.Open)
        if body_height == 0:
            body_height = 0.001

        rect = Rectangle(
            (x - candle_width / 2, body_low),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            alpha=0.85,
        )
        ax.add_patch(rect)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=30, ha="right")
    ax.set_ylabel("Price")
    ax.set_title(title or f"{ticker} K-Line")
    ax.grid(alpha=0.2)
    plt.tight_layout()
    plt.show()
