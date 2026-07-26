"""Persistent order log — JSON file backed, thread-safe append-first design.

Mirrors the research_store.py pattern but uses plain JSON for simplicity
(orders are small in count; no vector search needed).

Schema (OrderRecord):
  order_id   : str  — UUID4
  ticker     : str  — e.g. "600519.SS"
  name       : str  — display name e.g. "贵州茅台"
  action     : str  — "BUY" | "SELL"
  quantity   : int  — number of shares (A股 minimum 100)
  price      : float — limit price; 0 = market order
  mode       : str  — "paper" | "easytrader" | "manual"
  status     : str  — "pending" | "filled" | "rejected" | "cancelled"
  fill_price : float | None — actual execution price
  fill_qty   : int  | None — actual filled quantity
  fill_time  : str  | None — ISO timestamp of fill
  broker_ref : str  | None — broker order ID (EasyTrader / manual)
  risk_check : str  — "pass" | "warn:<reason>" | "block:<reason>"
  note       : str  — free-form user note
  created_at : str  — ISO timestamp (Asia/Shanghai)
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from invest_app.config import DATA_DIR, ensure_data_dir

_CN_TZ = ZoneInfo("Asia/Shanghai")
_ORDER_FILE = DATA_DIR / "orders.json"
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OrderRecord:
    ticker: str
    name: str
    action: str            # "BUY" | "SELL"
    quantity: int
    price: float           # limit price; 0 = market
    mode: str              # "paper" | "easytrader" | "manual"
    status: str = "pending"
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fill_price: Optional[float] = None
    fill_qty: Optional[int] = None
    fill_time: Optional[str] = None
    broker_ref: Optional[str] = None
    risk_check: str = "pass"
    note: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    )


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    try:
        return json.loads(_ORDER_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _save(records: list[dict]) -> None:
    ensure_data_dir()
    _ORDER_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_order(rec: OrderRecord) -> OrderRecord:
    """Persist a new order record (prepend so newest is first)."""
    with _lock:
        records = _load()
        records.insert(0, asdict(rec))
        _save(records)
    return rec


def update_order(order_id: str, **kwargs) -> bool:
    """Patch fields on an existing order by ID. Returns False if not found."""
    with _lock:
        records = _load()
        for r in records:
            if r.get("order_id") == order_id:
                r.update(kwargs)
                _save(records)
                return True
    return False


def cancel_order(order_id: str) -> bool:
    """Mark order as cancelled. Returns False if not found or already terminal."""
    with _lock:
        records = _load()
        for r in records:
            if r.get("order_id") == order_id:
                if r.get("status") in ("filled", "cancelled", "rejected"):
                    return False
                r["status"] = "cancelled"
                _save(records)
                return True
    return False


def get_orders(
    limit: int = 100,
    mode: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Return orders, optionally filtered by mode/status, newest first."""
    records = _load()
    if mode:
        records = [r for r in records if r.get("mode") == mode]
    if status:
        records = [r for r in records if r.get("status") == status]
    return records[:limit]


def get_order(order_id: str) -> Optional[dict]:
    """Return a single order by ID, or None."""
    return next((r for r in _load() if r.get("order_id") == order_id), None)


def clear_orders(mode: Optional[str] = None) -> int:
    """Delete orders (optionally only for a given mode). Returns deleted count."""
    with _lock:
        records = _load()
        if mode:
            keep = [r for r in records if r.get("mode") != mode]
        else:
            keep = []
        deleted = len(records) - len(keep)
        _save(keep)
    return deleted
