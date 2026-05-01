"""Parquet-based disk cache for historical OHLCV price data.

Caches daily and intraday bar data under <DATA_DIR>/cache/history/.

TTL rules:
  - interval='1d' / '1wk' / '1mo'  →  4 hours (refreshed once per session)
  - intraday / unknown              →  15 minutes (stays fresh during live trading)

Requires: pyarrow (installed alongside the package for Parquet support).
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from invest_app.config import DATA_DIR

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HISTORY_CACHE_DIR = DATA_DIR / "cache" / "history"

_TTL_SECONDS: dict[str, int] = {
    "1d":  4 * 3_600,
    "1wk": 4 * 3_600,
    "1mo": 24 * 3_600,
}
_DEFAULT_TTL = 15 * 60  # 15 minutes for intraday


def _safe_name(ticker: str) -> str:
    """Convert ticker to a safe filename component."""
    return (
        ticker
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace(".", "_")
    )


def _cache_path(ticker: str, period: str, interval: str) -> Path:
    return _HISTORY_CACHE_DIR / f"{_safe_name(ticker)}_{period}_{interval}.parquet"


def _ttl_seconds(interval: str) -> int:
    return _TTL_SECONDS.get(interval, _DEFAULT_TTL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_history_cache(ticker: str, period: str, interval: str = "1d") -> pd.DataFrame | None:
    """Return cached DataFrame if present and fresh; otherwise None."""
    path = _cache_path(ticker, period, interval)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _ttl_seconds(interval):
        return None
    try:
        df = pd.read_parquet(path)
        return df if not df.empty else None
    except Exception:
        return None


def write_history_cache(ticker: str, period: str, df: pd.DataFrame, interval: str = "1d") -> None:
    """Persist DataFrame to Parquet. Silently ignores any write errors."""
    if df.empty:
        return
    try:
        _HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_cache_path(ticker, period, interval), index=True)
    except Exception:
        pass
