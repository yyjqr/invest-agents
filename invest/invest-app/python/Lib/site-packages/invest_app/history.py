"""Search history persistence — stores analysed tickers in a local JSON file.

Each record stores ticker, name, timestamp, and the action recommendation
so users can quickly re-analyse previously searched instruments.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from invest_app.config import DATA_DIR, ensure_data_dir

HISTORY_FILE = DATA_DIR / "search_history.json"
_MAX_RECORDS = 200  # keep at most this many records


@dataclass
class HistoryRecord:
    """One search/analysis record."""

    ticker: str
    name: str
    action: str = ""
    sector: str = ""
    price: float | None = None
    timestamp: str = ""  # ISO-8601


@dataclass
class SearchHistory:
    """Collection of history records."""

    records: list[HistoryRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def load_history() -> SearchHistory:
    """Load search history from JSON file."""
    ensure_data_dir()
    if not HISTORY_FILE.exists():
        return SearchHistory()

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        records = [HistoryRecord(**r) for r in data.get("records", [])]
        return SearchHistory(records=records)
    except Exception:
        return SearchHistory()


def save_history(history: SearchHistory) -> None:
    """Save search history to JSON file."""
    ensure_data_dir()
    # Trim to max
    history.records = history.records[-_MAX_RECORDS:]
    data = {"records": [asdict(r) for r in history.records]}
    HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_history_record(
    ticker: str,
    name: str,
    action: str = "",
    sector: str = "",
    price: float | None = None,
) -> None:
    """Append a record to search history (dedup by ticker, keep latest)."""
    history = load_history()
    ts = datetime.now().isoformat(timespec="seconds")

    # Remove old duplicate for the same ticker
    history.records = [r for r in history.records if r.ticker != ticker]

    history.records.append(
        HistoryRecord(
            ticker=ticker,
            name=name,
            action=action,
            sector=sector,
            price=price,
            timestamp=ts,
        )
    )
    save_history(history)


def get_recent_tickers(limit: int = 20) -> list[HistoryRecord]:
    """Return the most recent N history records (newest first)."""
    history = load_history()
    return list(reversed(history.records[-limit:]))
