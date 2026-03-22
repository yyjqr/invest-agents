"""Watchlist persistence — stores user's custom stock watchlist in a local JSON file.

Users can add/remove stocks to their personal watchlist. The data is stored
alongside search history in the invest-app-data directory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from invest_app.config import DATA_DIR, ensure_data_dir

WATCHLIST_FILE = DATA_DIR / "watchlist.json"
_MAX_ITEMS = 200  # keep at most this many items


@dataclass
class WatchlistItem:
    """One watchlist entry."""

    ticker: str          # 6-digit code like '600519'
    name: str            # e.g. '贵州茅台'
    note: str = ""       # user note (optional)


@dataclass
class Watchlist:
    """Collection of watchlist items."""

    items: list[WatchlistItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def load_watchlist() -> Watchlist:
    """Load watchlist from JSON file."""
    ensure_data_dir()
    if not WATCHLIST_FILE.exists():
        return Watchlist()

    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        items = [WatchlistItem(**r) for r in data.get("items", [])]
        return Watchlist(items=items)
    except Exception:
        return Watchlist()


def save_watchlist(wl: Watchlist) -> None:
    """Save watchlist to JSON file."""
    ensure_data_dir()
    wl.items = wl.items[-_MAX_ITEMS:]
    data = {"items": [asdict(r) for r in wl.items]}
    WATCHLIST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_to_watchlist(ticker: str, name: str, note: str = "") -> None:
    """Add a stock to the watchlist (dedup by ticker)."""
    wl = load_watchlist()
    wl.items = [item for item in wl.items if item.ticker != ticker]
    wl.items.append(WatchlistItem(ticker=ticker, name=name, note=note))
    save_watchlist(wl)


def remove_from_watchlist(ticker: str) -> None:
    """Remove a stock from the watchlist."""
    wl = load_watchlist()
    wl.items = [item for item in wl.items if item.ticker != ticker]
    save_watchlist(wl)


def get_watchlist_items() -> list[WatchlistItem]:
    """Return all watchlist items."""
    return load_watchlist().items


def is_in_watchlist(ticker: str) -> bool:
    """Check if a ticker is already in the watchlist."""
    return any(item.ticker == ticker for item in load_watchlist().items)
