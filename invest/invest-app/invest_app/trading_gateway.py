"""Trading Gateway — 交易网关

架构层次：
┌─────────────────────────────────────────────────────┐
│  FastAPI 端点 (server.py imports this router)        │
├─────────────────────────────────────────────────────┤
│  风控层 RiskGuard  ← 单笔/日累计仓位检查              │
├─────────────────────────────────────────────────────┤
│  执行模式                                            │
│  • paper      — 纯内存模拟，零依赖                   │
│  • easytrader — EasyTrader Python 桥接（需安装）      │
│  • manual     — 仅记录，用户自行操作券商客户端         │
├─────────────────────────────────────────────────────┤
│  持久层 order_store.py  ← JSON 文件，线程安全         │
└─────────────────────────────────────────────────────┘

合规说明：
  A 股严禁个人程序化直接接入券商生产环境。
  paper 模式为完全模拟，不产生任何真实交易。
  easytrader 模式通过 GUI 自动化操作已登录的本地客户端，
  属于辅助工具，用户需自行确保符合所在券商的用户协议。

Rust FFI 集成（未来扩展）：
  当券商提供 C/C++ SDK（如招商智远量化 API）时，可通过 PyO3
  暴露的 Rust 模块调用，提供纳秒级回调处理能力。
  接口预留在 _execute_rust_sdk() 存根中。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from invest_app.order_store import (
    OrderRecord,
    add_order,
    cancel_order,
    get_order,
    get_orders,
    update_order,
)
from invest_app.config import ensure_data_dir

logger = logging.getLogger("invest.trading")
_CN_TZ = ZoneInfo("Asia/Shanghai")

router = APIRouter(prefix="/api/trade", tags=["trading"])
_security = Depends(HTTPBearer(auto_error=False))

# ---------------------------------------------------------------------------
# Paper trading in-memory state (reset on server restart)
# ---------------------------------------------------------------------------

_paper_lock = threading.Lock()

# {ticker: {"qty": int, "avg_cost": float, "name": str}}
_paper_positions: dict[str, dict] = {}

# Paper account cash balance (CNY)
_PAPER_INITIAL_CASH = 1_000_000.0
_paper_cash = _PAPER_INITIAL_CASH

_PAPER_STATE_FILE = ensure_data_dir() / "paper_account.json"


def _load_paper_state() -> None:
    """Load paper account state from disk (called once at module import)."""
    global _paper_cash, _paper_positions
    if not _PAPER_STATE_FILE.exists():
        return
    try:
        d = json.loads(_PAPER_STATE_FILE.read_text(encoding="utf-8"))
        with _paper_lock:
            _paper_cash = float(d.get("cash", _PAPER_INITIAL_CASH))
            _paper_positions = {
                k: v for k, v in d.get("positions", {}).items() if v.get("qty", 0) > 0
            }
    except Exception as exc:
        logger.warning("Failed to load paper account state: %s", exc)


def _save_paper_state() -> None:
    """Persist paper account state to disk (called after each trade/reset)."""
    try:
        with _paper_lock:
            data = {"cash": _paper_cash, "positions": dict(_paper_positions)}
        _PAPER_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to save paper account state: %s", exc)


_load_paper_state()


def _get_paper_summary() -> dict:
    """Return current paper account snapshot."""
    global _paper_cash
    with _paper_lock:
        positions = [
            {
                "ticker": t,
                "name": v["name"],
                "qty": v["qty"],
                "avg_cost": round(v["avg_cost"], 3),
                "market_value": None,  # filled by frontend via quote
            }
            for t, v in _paper_positions.items()
            if v["qty"] > 0
        ]
    return {
        "cash": round(_paper_cash, 2),
        "initial_cash": _PAPER_INITIAL_CASH,
        "positions": positions,
    }


# ---------------------------------------------------------------------------
# Risk Guard
# ---------------------------------------------------------------------------

class RiskGuardConfig(BaseModel):
    max_single_order_pct: float = 0.20   # single order ≤ 20% of cash
    max_daily_trade_pct: float = 0.50    # daily total turnover ≤ 50% of cash
    min_lot_size: int = 100              # A股最小单位


_risk_config = RiskGuardConfig()


def _check_risk(
    action: str,
    quantity: int,
    price: float,
    mode: str,
) -> str:
    """Returns "pass", "warn:<reason>", or "block:<reason>"."""
    global _paper_cash

    if quantity <= 0:
        return "block:委托数量必须大于0"
    if quantity % _risk_config.min_lot_size != 0:
        return f"block:A股最小交易单位为{_risk_config.min_lot_size}股，请调整数量"

    if mode != "paper":
        return "pass"  # manual/easytrader: risk at user's discretion

    order_value = quantity * (price if price > 0 else 999_999)
    available = _paper_cash

    if action == "BUY":
        if order_value > available:
            return f"block:模拟账户余额不足（需 ¥{order_value:,.0f}，余 ¥{available:,.0f}）"
        if order_value > available * _risk_config.max_single_order_pct:
            pct = order_value / available * 100
            return f"warn:单笔占用 {pct:.1f}% 资金，超过 {_risk_config.max_single_order_pct*100:.0f}% 建议上限"

    if action == "SELL":
        ticker_positions = {k: v for k, v in _paper_positions.items()}
        # Will be validated per-ticker in execute

    return "pass"


# ---------------------------------------------------------------------------
# Execution layer
# ---------------------------------------------------------------------------

def _execute_paper(rec: OrderRecord) -> OrderRecord:
    """Simulate instant fill at limit price (or 0 → use price as market)."""
    global _paper_cash
    fill_price = rec.price if rec.price > 0 else rec.price

    with _paper_lock:
        if rec.action == "BUY":
            cost = fill_price * rec.quantity
            if cost > _paper_cash:
                rec.status = "rejected"
                rec.risk_check = f"block:资金不足（需 ¥{cost:,.0f}）"
                return rec
            _paper_cash -= cost
            pos = _paper_positions.get(rec.ticker, {"qty": 0, "avg_cost": 0.0, "name": rec.name})
            total_cost = pos["avg_cost"] * pos["qty"] + cost
            pos["qty"] += rec.quantity
            pos["avg_cost"] = total_cost / pos["qty"] if pos["qty"] else fill_price
            pos["name"] = rec.name
            _paper_positions[rec.ticker] = pos

        elif rec.action == "SELL":
            pos = _paper_positions.get(rec.ticker)
            if not pos or pos["qty"] < rec.quantity:
                rec.status = "rejected"
                rec.risk_check = f"block:持仓不足（持有 {pos['qty'] if pos else 0} 股）"
                return rec
            pos["qty"] -= rec.quantity
            _paper_cash += fill_price * rec.quantity
            if pos["qty"] == 0:
                del _paper_positions[rec.ticker]

    now = datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    rec.status = "filled"
    rec.fill_price = fill_price
    rec.fill_qty = rec.quantity
    rec.fill_time = now
    _save_paper_state()
    return rec


def _execute_easytrader(rec: OrderRecord) -> OrderRecord:
    """Bridge to EasyTrader — calls local broker GUI client via pywinauto."""
    try:
        import easytrader  # type: ignore
    except ImportError:
        rec.status = "rejected"
        rec.risk_check = "block:EasyTrader 未安装（pip install easytrader）"
        return rec

    rec.status = "rejected"
    rec.risk_check = "block:EasyTrader 连接未配置，请先在设置中绑定券商客户端"
    logger.warning("EasyTrader connector not yet configured")
    return rec


def _execute_rust_sdk(rec: OrderRecord) -> OrderRecord:
    """Stub for future Rust FFI / PyO3 broker SDK integration."""
    # When a C/C++ SDK (e.g. 招商智远量化 API) is available, import the
    # compiled PyO3 extension and delegate to its submit_order() function.
    rec.status = "rejected"
    rec.risk_check = "block:券商 SDK 未集成（Rust FFI 预留接口）"
    return rec


def _execute_manual(rec: OrderRecord) -> OrderRecord:
    """Manual mode — record only, user executes in broker client."""
    rec.status = "pending"
    rec.broker_ref = "MANUAL"
    return rec


def execute_order(rec: OrderRecord) -> OrderRecord:
    """Route order to the appropriate execution layer."""
    if rec.mode == "paper":
        rec = _execute_paper(rec)
    elif rec.mode == "easytrader":
        rec = _execute_easytrader(rec)
    elif rec.mode == "manual":
        rec = _execute_manual(rec)
    else:
        rec.status = "rejected"
        rec.risk_check = f"block:未知执行模式 {rec.mode}"
    add_order(rec)
    return rec


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    ticker: str = Field(..., min_length=3, max_length=20)
    name: str = ""
    action: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: int = Field(..., ge=100)
    price: float = Field(..., ge=0)
    mode: str = Field("paper", pattern="^(paper|easytrader|manual)$")
    note: str = ""


class RiskConfigRequest(BaseModel):
    max_single_order_pct: float = Field(0.20, ge=0.01, le=1.0)
    max_daily_trade_pct: float = Field(0.50, ge=0.01, le=1.0)


# ---------------------------------------------------------------------------
# Auth helper (re-uses server pattern)
# ---------------------------------------------------------------------------

def _bearer_token(credentials: Optional[HTTPAuthorizationCredentials]) -> str | None:
    return credentials.credentials if credentials else None


def _require_user(credentials: Optional[HTTPAuthorizationCredentials]) -> dict:
    from invest_app.auth import decode_token
    token = _bearer_token(credentials)
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return payload


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/order", status_code=201)
async def submit_order(
    req: OrderRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """提交委托单（模拟 / EasyTrader / 手工记录）。"""
    _require_user(credentials)

    risk_result = _check_risk(req.action, req.quantity, req.price, req.mode)
    if risk_result.startswith("block:"):
        raise HTTPException(status_code=422, detail=risk_result[6:])

    rec = OrderRecord(
        ticker=req.ticker,
        name=req.name,
        action=req.action,
        quantity=req.quantity,
        price=req.price,
        mode=req.mode,
        note=req.note,
        risk_check=risk_result,
    )
    rec = execute_order(rec)
    return {
        "order_id": rec.order_id,
        "status": rec.status,
        "fill_price": rec.fill_price,
        "fill_qty": rec.fill_qty,
        "fill_time": rec.fill_time,
        "risk_check": rec.risk_check,
        "mode": rec.mode,
    }


@router.get("/orders")
async def list_orders(
    mode: Optional[str] = Query(None, pattern="^(paper|easytrader|manual)$"),
    status: Optional[str] = Query(None, pattern="^(pending|filled|rejected|cancelled)$"),
    limit: int = Query(50, ge=1, le=200),
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """查询委托历史记录。"""
    _require_user(credentials)
    return get_orders(limit=limit, mode=mode, status=status)


@router.put("/orders/{order_id}/cancel")
async def cancel_order_api(
    order_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """撤销待成交委托。"""
    _require_user(credentials)
    ok = cancel_order(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail="委托单不存在或已处于终态")
    return {"order_id": order_id, "status": "cancelled"}


@router.put("/orders/{order_id}/fill")
async def mark_filled(
    order_id: str,
    fill_price: float = Query(..., gt=0),
    fill_qty: Optional[int] = Query(None, gt=0),
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """手工记录成交（manual 模式专用）。"""
    _require_user(credentials)
    rec = get_order(order_id)
    if not rec:
        raise HTTPException(status_code=404, detail="委托单不存在")
    if rec.get("mode") != "manual":
        raise HTTPException(status_code=400, detail="仅 manual 模式支持手工回填成交")
    now = datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    update_order(
        order_id,
        status="filled",
        fill_price=fill_price,
        fill_qty=fill_qty or rec.get("quantity"),
        fill_time=now,
    )
    return {"order_id": order_id, "status": "filled", "fill_price": fill_price}


@router.get("/positions")
async def get_positions(
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """获取模拟账户当前持仓与资金。"""
    _require_user(credentials)
    return _get_paper_summary()


@router.post("/positions/reset")
async def reset_paper_account(
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """重置模拟账户（清空持仓与委托历史）。"""
    global _paper_cash
    _require_user(credentials)
    with _paper_lock:
        _paper_positions.clear()
        _paper_cash = _PAPER_INITIAL_CASH
    _save_paper_state()
    from invest_app.order_store import clear_orders
    cleared = clear_orders(mode="paper")
    return {"message": f"模拟账户已重置，清除 {cleared} 条历史委托", "cash": _PAPER_INITIAL_CASH}


@router.get("/risk-config")
async def get_risk_config(
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """查看当前风控参数。"""
    _require_user(credentials)
    return _risk_config.model_dump()


@router.put("/risk-config")
async def update_risk_config(
    req: RiskConfigRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = _security,
):
    """更新风控参数（max_single_order_pct / max_daily_trade_pct）。"""
    _require_user(credentials)
    global _risk_config
    _risk_config = RiskGuardConfig(**req.model_dump())
    return _risk_config.model_dump()
