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
# Monitor list — a subset of watchlist pinned to realtime monitor (max 20)
# ---------------------------------------------------------------------------

def load_monitor_list(username: str) -> list[str]:
    f = _user_file(username, "monitor.json")
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data[:20] if isinstance(data, list) else []
    except Exception:
        return []


def save_monitor_list(username: str, tickers: list[str]) -> None:
    f = _user_file(username, "monitor.json")
    f.write_text(json.dumps(tickers[:20], ensure_ascii=False), encoding="utf-8")


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
    defaults = {
        "theme": "light",
        "refresh_interval": 5,
        "price_alert": True,
    }
    if not f.exists():
        return defaults
    try:
        stored = json.loads(f.read_text(encoding="utf-8"))
        defaults.update(stored)
        return defaults
    except Exception:
        return defaults


def save_settings(username: str, settings: dict) -> None:
    current = load_settings(username)
    current.update(settings)
    f = _user_file(username, "settings.json")
    f.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Price alerts (per-user)
# ---------------------------------------------------------------------------

def load_alerts(username: str) -> dict:
    """Return {ticker: {alert_high: float|None, alert_low: float|None}}."""
    f = _user_file(username, "alerts.json")
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_alerts(username: str, alerts: dict) -> None:
    f = _user_file(username, "alerts.json")
    f.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")


def set_alert(username: str, ticker: str, alert_high: float | None, alert_low: float | None) -> dict:
    alerts = load_alerts(username)
    alerts[ticker] = {"alert_high": alert_high, "alert_low": alert_low}
    save_alerts(username, alerts)
    return alerts


def delete_alert(username: str, ticker: str) -> dict:
    alerts = load_alerts(username)
    alerts.pop(ticker, None)
    save_alerts(username, alerts)
    return alerts


# ---------------------------------------------------------------------------
# Forecast history (per-user) — save predictions for later comparison
# ---------------------------------------------------------------------------

def _forecast_file(username: str) -> Path:
    return _user_file(username, "forecast_history.json")


def load_forecast_history(username: str) -> list[dict]:
    f = _forecast_file(username)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_forecast_record(username: str, record: dict) -> list[dict]:
    """Append one forecast record to the history file.

    Each record should contain: date, ticker, name, horizon,
    current_price, predicted_price, predicted_high, predicted_low,
    trend, confidence, expected_return_pct, scenarios, etc.
    Keeps up to 500 records (FIFO).
    """
    history = load_forecast_history(username)
    history.append(record)
    # Cap at 500 to keep file size manageable
    if len(history) > 500:
        history = history[-500:]
    f = _forecast_file(username)
    f.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def delete_forecast_record(username: str, index: int) -> list[dict]:
    """Delete a forecast record by index."""
    history = load_forecast_history(username)
    if 0 <= index < len(history):
        history.pop(index)
    f = _forecast_file(username)
    f.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def clear_forecast_history(username: str) -> list[dict]:
    """Clear all forecast history records."""
    f = _forecast_file(username)
    f.write_text("[]", encoding="utf-8")
    return []


# ---------------------------------------------------------------------------
# Saved articles (per-user) — bookmark news / research reports
# ---------------------------------------------------------------------------

def _saved_articles_file(username: str) -> Path:
    return _user_file(username, "saved_articles.json")


def load_saved_articles(username: str) -> list[dict]:
    """Return list of saved articles, newest first."""
    f = _saved_articles_file(username)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_article(username: str, article: dict) -> list[dict]:
    """Bookmark one article.

    article fields: title, url, source, date, tag, summary, category
    Prevents duplicates by URL. Keeps up to 500 records (FIFO).
    """
    items = load_saved_articles(username)
    url = article.get("url", "")
    # Deduplicate by URL if URL is non-empty
    if url and any(i.get("url") == url for i in items):
        return items
    from datetime import datetime
    article.setdefault("saved_at", datetime.now().isoformat(timespec="seconds"))
    items.insert(0, article)  # newest first
    if len(items) > 500:
        items = items[:500]
    f = _saved_articles_file(username)
    f.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def delete_saved_article(username: str, index: int) -> list[dict]:
    """Delete a saved article by index."""
    items = load_saved_articles(username)
    if 0 <= index < len(items):
        items.pop(index)
    f = _saved_articles_file(username)
    f.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def clear_saved_articles(username: str) -> list[dict]:
    """Clear all saved articles."""
    f = _saved_articles_file(username)
    f.write_text("[]", encoding="utf-8")
    return []
