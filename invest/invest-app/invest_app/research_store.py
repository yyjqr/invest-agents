"""LanceDB-backed research report store.

Caches A-share research reports locally for fast retrieval by the AI agent
and the frontend research panel.

Falls back to an in-memory list if lancedb is unavailable so the app still
runs in environments where the native lance binary is not installed.

Schema fields
─────────────
id            : unique identifier (infoCode from Eastmoney, or title hash)
title         : report title
org           : brokerage / institution short name
researcher    : analyst name(s)
stock_name    : stock name(s) mentioned
stock_code    : primary A-share code (no suffix, e.g. "600519")
sector        : 申万一级行业 name
rating        : 买入 / 增持 / 中性 / 减持 / 卖出 (empty if unavailable)
rating_change : 首次 / 上调 / 下调 / 维持 (empty if unavailable)
date          : publish date (YYYY-MM-DD)
url           : deep-link to report page
summary       : brief abstract (may be empty for Eastmoney public data)
ingested_at   : UTC ISO timestamp when the row was stored
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from invest_app.config import DATA_DIR

logger = logging.getLogger(__name__)

_TABLE_NAME = "research_reports"
_DB_DIR: Path = DATA_DIR / "research_db"
_MAX_AGE_DAYS = 30

# ---------------------------------------------------------------------------
# Optional LanceDB import
# ---------------------------------------------------------------------------

try:
    import lancedb
    import pyarrow as pa

    _LANCEDB_OK = True
except ImportError:
    _LANCEDB_OK = False
    logger.info("lancedb not installed — research_store uses in-memory fallback")

# In-memory fallback list (shared across calls in the same process)
_mem: list[dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_FIELDS = (
    "id", "title", "org", "researcher",
    "stock_name", "stock_code", "sector",
    "rating", "rating_change", "date", "url", "summary", "ingested_at",
)


def _make_schema() -> "pa.Schema":
    return pa.schema([pa.field(f, pa.string()) for f in _FIELDS])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_fields(row: dict[str, Any]) -> dict[str, str]:
    """Return a copy of *row* with all schema fields present (empty string as default)."""
    return {f: str(row.get(f) or "") for f in _FIELDS}


def _get_table():
    """Open (create if absent) the LanceDB research_reports table."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(_DB_DIR))
    if _TABLE_NAME not in db.table_names():
        schema = _make_schema()
        return db.create_table(_TABLE_NAME, schema=schema)
    return db.open_table(_TABLE_NAME)


def _title_hash(title: str) -> str:
    return hashlib.md5(title.encode("utf-8", errors="ignore")).hexdigest()[:16]  # nosec B324


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest(reports: list[dict[str, Any]]) -> None:
    """Add *reports* to the store, deduplicating by *id*."""
    if not reports:
        return

    now = datetime.utcnow().isoformat()
    rows: list[dict[str, str]] = []
    for r in reports:
        r = dict(r)
        r.setdefault("ingested_at", now)
        if not r.get("id"):
            r["id"] = _title_hash(r.get("title", "") + r.get("date", ""))
        rows.append(_ensure_fields(r))

    if _LANCEDB_OK:
        try:
            tbl = _get_table()
            # Deduplicate: keep only rows whose id is not already present
            try:
                existing_arrow = tbl.to_arrow()
                existing_ids: set[str] = set(existing_arrow.column("id").to_pylist())
            except Exception:
                existing_ids = set()
            new_rows = [r for r in rows if r["id"] not in existing_ids]
            if new_rows:
                tbl.add(new_rows)
            return
        except Exception as exc:
            logger.warning("LanceDB ingest error: %s — falling back to memory", exc)

    # ── memory fallback ──────────────────────────────────────────────────────
    global _mem
    existing_ids = {r.get("id") for r in _mem}
    _mem.extend(r for r in rows if r.get("id") not in existing_ids)
    _mem = sorted(_mem, key=lambda x: x.get("date", ""), reverse=True)[:300]


def search_by_ticker(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return reports whose *stock_code* matches *ticker* (suffix stripped)."""
    code = ticker.upper().split(".")[0]
    if _LANCEDB_OK:
        try:
            tbl = _get_table()
            rows = (
                tbl.search()
                .where(f"stock_code = '{code}'", prefilter=True)
                .limit(limit)
                .to_list()
            )
            return sorted(rows, key=lambda x: x.get("date", ""), reverse=True)
        except Exception as exc:
            logger.debug("LanceDB search_by_ticker error: %s", exc)

    return sorted(
        [r for r in _mem if r.get("stock_code") == code],
        key=lambda x: x.get("date", ""),
        reverse=True,
    )[:limit]


def search_by_sector(sector: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return reports whose *sector* contains *sector* as a substring."""
    if _LANCEDB_OK:
        try:
            tbl = _get_table()
            rows = (
                tbl.search()
                .where(f"sector LIKE '%{sector}%'", prefilter=True)
                .limit(limit)
                .to_list()
            )
            return sorted(rows, key=lambda x: x.get("date", ""), reverse=True)
        except Exception as exc:
            logger.debug("LanceDB search_by_sector error: %s", exc)

    return sorted(
        [r for r in _mem if sector in r.get("sector", "")],
        key=lambda x: x.get("date", ""),
        reverse=True,
    )[:limit]


def get_recent(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recently published reports."""
    if _LANCEDB_OK:
        try:
            tbl = _get_table()
            all_rows = tbl.to_arrow().to_pylist()
            return sorted(all_rows, key=lambda x: x.get("date", ""), reverse=True)[:limit]
        except Exception as exc:
            logger.debug("LanceDB get_recent error: %s", exc)

    return sorted(_mem, key=lambda x: x.get("date", ""), reverse=True)[:limit]


def search_text(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Keyword search across title, summary, stock_name, sector, org."""
    if not query.strip():
        return get_recent(limit)

    keywords = query.lower().split()

    candidates: list[dict[str, Any]] = []
    if _LANCEDB_OK:
        try:
            candidates = _get_table().to_arrow().to_pylist()
        except Exception:
            candidates = _mem
    else:
        candidates = _mem

    def _score(r: dict) -> int:
        haystack = " ".join([
            r.get("title", ""), r.get("summary", ""),
            r.get("stock_name", ""), r.get("sector", ""),
            r.get("org", ""),
        ]).lower()
        return sum(1 for kw in keywords if kw in haystack)

    scored = [(r, _score(r)) for r in candidates]
    scored.sort(key=lambda x: (-x[1], x[0].get("date", "") or ""), reverse=False)
    return [r for r, s in scored if s > 0][:limit]


def prune_old(days: int = _MAX_AGE_DAYS) -> int:
    """Delete reports older than *days* days.  Returns the count removed."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    if _LANCEDB_OK:
        try:
            tbl = _get_table()
            before = tbl.count_rows()
            tbl.delete(f"date < '{cutoff}'")
            return before - tbl.count_rows()
        except Exception as exc:
            logger.debug("LanceDB prune error: %s", exc)

    global _mem
    before = len(_mem)
    _mem = [r for r in _mem if (r.get("date") or "") >= cutoff]
    return before - len(_mem)
