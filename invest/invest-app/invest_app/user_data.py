"""Per-user data management — each user has their own watchlist and portfolio stored
in DATA_DIR/users/{username}/ to ensure full data isolation between accounts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from invest_app.config import DATA_DIR


def _user_dir(username: str) -> Path:
    d = DATA_DIR / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_file(username: str, filename: str) -> Path:
    return _user_dir(username) / filename


# ---------------------------------------------------------------------------
# Watchlist (per-user)
# ---------------------------------------------------------------------------

def load_watchlist(username: str) -> list[dict]:
    f = _user_file(username, "watchlist.json")
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_watchlist(username: str, items: list[dict]) -> None:
    f = _user_file(username, "watchlist.json")
    f.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add_watchlist_item(username: str, ticker: str, name: str = "", note: str = "") -> list[dict]:
    items = load_watchlist(username)
    if not any(i["ticker"] == ticker for i in items):
        items.append({"ticker": ticker, "name": name, "note": note})
        save_watchlist(username, items)
    return items


def remove_watchlist_item(username: str, ticker: str) -> list[dict]:
    items = [i for i in load_watchlist(username) if i["ticker"] != ticker]
    save_watchlist(username, items)
    return items


def update_watchlist_item(username: str, ticker: str, **kwargs: Any) -> list[dict]:
    items = load_watchlist(username)
    for item in items:
        if item["ticker"] == ticker:
            item.update(kwargs)
    save_watchlist(username, items)
    return items


# ---------------------------------------------------------------------------
# Monitor list — a subset of watchlist pinned to realtime monitor (max 10)
# ---------------------------------------------------------------------------

def load_monitor_list(username: str) -> list[str]:
    f = _user_file(username, "monitor.json")
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data[:10] if isinstance(data, list) else []
    except Exception:
        return []


def save_monitor_list(username: str, tickers: list[str]) -> None:
    f = _user_file(username, "monitor.json")
    f.write_text(json.dumps(tickers[:10], ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Portfolio (per-user, thin wrapper around Portfolio model)
# ---------------------------------------------------------------------------

def get_portfolio_path(username: str) -> Path:
    return _user_file(username, "portfolio.json")


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

def load_settings(username: str) -> dict:
    f = _user_file(username, "settings.json")
    if not f.exists():
        return {"theme": "light", "refresh_interval": 5, "price_alert": True}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(username: str, settings: dict) -> None:
    current = load_settings(username)
    current.update(settings)
    f = _user_file(username, "settings.json")
    f.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
