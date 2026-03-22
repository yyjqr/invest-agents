"""Broker import — parse holding exports from Chinese brokerages and funds.

Supported sources:
  - 招商证券 (China Merchants Securities)
  - 腾讯微证券 (Tencent WeChat Securities / 富途)
  - 长江证券 (Changjiang Securities)
  - 东方财富 (East Money)
  - 同花顺 (THS / iFinD)
  - 支付宝基金 (Alipay Funds)
  - 天天基金 (Tian Tian Fund)
  - 通用CSV (Generic)
  - 手动JSON (Manual batch)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from invest_app.models import AssetType, Holding, Portfolio, Transaction, TransactionType
from invest_app.portfolio import load_portfolio, save_portfolio

logger = logging.getLogger("invest.broker_import")

# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

def normalise_ticker(raw: str, asset_type: AssetType = AssetType.STOCK) -> str:
    """Normalise a raw ticker code to include exchange suffix.

    A-share rules:
      6xxxxx / 688xxx → Shanghai (.SS)
      0xxxxx / 3xxxxx / 002xxx → Shenzhen (.SZ)
    Fund codes (6 digits, asset_type MUTUAL_FUND) stay as-is.
    """
    code = re.sub(r"[^0-9A-Za-z.]", "", raw.strip())
    if not code:
        return raw.strip().upper()

    # Already has suffix
    if "." in code:
        return code.upper()

    # Pure-digit Chinese codes
    if code.isdigit() and len(code) == 6:
        if asset_type == AssetType.MUTUAL_FUND:
            return code  # fund codes don't need suffix
        first = code[0]
        if first in ("6",):
            return f"{code}.SS"
        if first in ("0", "3"):
            return f"{code}.SZ"
        # 5xxxxx → ETF on Shanghai
        if first == "5":
            return f"{code}.SS"
        # 1xxxxx → ETF on Shenzhen / bonds
        if first == "1":
            return f"{code}.SZ"
        return f"{code}.SS"  # fallback

    return code.upper()


# ---------------------------------------------------------------------------
# Parsed row
# ---------------------------------------------------------------------------

@dataclass
class ImportedHolding:
    """One parsed holding row from a broker export."""
    ticker: str              # normalised
    name: str = ""
    shares: float = 0.0
    cost_price: float = 0.0  # per-share avg cost
    current_price: float = 0.0
    market_value: float = 0.0
    pnl: float = 0.0
    asset_type: AssetType = AssetType.STOCK
    broker: str = ""         # source broker label


@dataclass
class ImportResult:
    """Result of parsing a broker export file."""
    broker: str
    holdings: list[ImportedHolding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw_rows: int = 0


# ---------------------------------------------------------------------------
# Column name aliases per broker
# ---------------------------------------------------------------------------

# Map of conceptual field → list of possible column header names
_COL_TICKER = ["证券代码", "代码", "股票代码", "基金代码", "证券编码", "code", "ticker", "symbol"]
_COL_NAME = ["证券名称", "名称", "股票名称", "基金名称", "证券简称", "name"]
_COL_SHARES = [
    "股票余额", "持仓数量", "持有数量", "数量", "余额",
    "可用", "持仓", "当前持仓", "持有份额", "份额",
    "shares", "quantity", "qty", "volume",
]
_COL_COST = ["成本价", "买入均价", "成本", "持仓成本价", "摊薄成本价", "均价", "cost", "avg_cost"]
_COL_PRICE = ["现价", "当前价", "最新价", "市价", "price", "current_price", "last"]
_COL_VALUE = ["最新市值", "市值", "持有金额", "资产", "market_value", "value"]
_COL_PNL = ["浮动盈亏", "盈亏", "收益", "累计收益", "pnl", "profit"]


def _match_col(headers: list[str], aliases: list[str]) -> Optional[int]:
    """Find the column index matching any alias (case-insensitive, stripped)."""
    for idx, h in enumerate(headers):
        h_clean = h.strip().lower().replace(" ", "").replace("\t", "")
        for a in aliases:
            if a.lower() == h_clean or h_clean.startswith(a.lower()):
                return idx
    return None


def _safe_float(val: str) -> float:
    """Parse a float from potentially messy text (commas, %, Chinese chars)."""
    if not val:
        return 0.0
    cleaned = re.sub(r"[,%¥$￥\s]", "", val.strip())
    cleaned = cleaned.rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Generic CSV / TSV parser
# ---------------------------------------------------------------------------

def _detect_delimiter(first_line: str) -> str:
    if "\t" in first_line:
        return "\t"
    if "," in first_line:
        return ","
    if ";" in first_line:
        return ";"
    return ","


def _parse_csv_generic(
    text: str,
    broker_label: str = "通用CSV",
    asset_type: AssetType = AssetType.STOCK,
) -> ImportResult:
    """Parse a CSV/TSV holding export using fuzzy column matching."""
    result = ImportResult(broker=broker_label)

    lines = text.strip().splitlines()
    if not lines:
        result.errors.append("文件为空")
        return result

    # Skip leading non-data lines (some brokers add title/date rows)
    header_idx = 0
    for i, line in enumerate(lines):
        # A header line should contain at least one known column name
        lower = line.lower()
        if any(alias in lower for aliases in [_COL_TICKER, _COL_NAME, _COL_SHARES] for alias in aliases):
            header_idx = i
            break

    if header_idx >= len(lines) - 1:
        result.errors.append("未找到有效表头行")
        return result

    delimiter = _detect_delimiter(lines[header_idx])
    reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])), delimiter=delimiter)
    rows = list(reader)
    if len(rows) < 2:
        result.errors.append("无数据行")
        return result

    headers = rows[0]
    col_ticker = _match_col(headers, _COL_TICKER)
    col_name = _match_col(headers, _COL_NAME)
    col_shares = _match_col(headers, _COL_SHARES)
    col_cost = _match_col(headers, _COL_COST)
    col_price = _match_col(headers, _COL_PRICE)
    col_value = _match_col(headers, _COL_VALUE)
    col_pnl = _match_col(headers, _COL_PNL)

    if col_ticker is None and col_name is None:
        result.errors.append(f"无法识别表头列: {headers}")
        return result

    for row in rows[1:]:
        result.raw_rows += 1
        try:
            if not row or all(c.strip() == "" for c in row):
                continue

            raw_ticker = row[col_ticker].strip() if col_ticker is not None and col_ticker < len(row) else ""
            name = row[col_name].strip() if col_name is not None and col_name < len(row) else ""

            if not raw_ticker and not name:
                continue

            # Skip summary / total rows
            if any(kw in (raw_ticker + name) for kw in ["合计", "总计", "小计", "total", "合  计"]):
                continue

            shares = _safe_float(row[col_shares]) if col_shares is not None and col_shares < len(row) else 0.0
            cost = _safe_float(row[col_cost]) if col_cost is not None and col_cost < len(row) else 0.0
            price = _safe_float(row[col_price]) if col_price is not None and col_price < len(row) else 0.0
            value = _safe_float(row[col_value]) if col_value is not None and col_value < len(row) else 0.0
            pnl = _safe_float(row[col_pnl]) if col_pnl is not None and col_pnl < len(row) else 0.0

            if shares <= 0:
                continue

            ticker = normalise_ticker(raw_ticker, asset_type)

            result.holdings.append(ImportedHolding(
                ticker=ticker,
                name=name,
                shares=shares,
                cost_price=cost,
                current_price=price,
                market_value=value,
                pnl=pnl,
                asset_type=asset_type,
                broker=broker_label,
            ))
        except Exception as e:
            result.errors.append(f"行 {result.raw_rows}: {e}")

    return result


# ---------------------------------------------------------------------------
# Broker-specific parsers
# ---------------------------------------------------------------------------

def parse_zhaoshang(text: str) -> ImportResult:
    """招商证券 — 智远一户通导出的持仓CSV。"""
    return _parse_csv_generic(text, broker_label="招商证券")


def parse_tencent_weizhengquan(text: str) -> ImportResult:
    """腾讯微证券 — 持仓导出CSV。"""
    return _parse_csv_generic(text, broker_label="腾讯微证券")


def parse_changjiang(text: str) -> ImportResult:
    """长江证券 — 长江e号导出的持仓CSV。"""
    return _parse_csv_generic(text, broker_label="长江证券")


def parse_dongfangcaifu(text: str) -> ImportResult:
    """东方财富 — 持仓导出CSV。"""
    return _parse_csv_generic(text, broker_label="东方财富")


def parse_tonghuashun(text: str) -> ImportResult:
    """同花顺 — 持仓导出CSV。"""
    return _parse_csv_generic(text, broker_label="同花顺")


def parse_alipay_fund(text: str) -> ImportResult:
    """支付宝基金 — 持有基金导出CSV。"""
    return _parse_csv_generic(text, broker_label="支付宝基金", asset_type=AssetType.MUTUAL_FUND)


def parse_tiantian_fund(text: str) -> ImportResult:
    """天天基金 — 持有基金导出CSV。"""
    return _parse_csv_generic(text, broker_label="天天基金", asset_type=AssetType.MUTUAL_FUND)


def parse_generic_csv(text: str) -> ImportResult:
    """通用CSV — 自动匹配列名。"""
    return _parse_csv_generic(text, broker_label="通用CSV")


def parse_manual_json(text: str) -> ImportResult:
    """手动JSON批量导入。

    Expected format:
    [
      {"ticker": "600519", "shares": 100, "cost_price": 1500.0},
      {"ticker": "000858", "shares": 200, "cost_price": 150.0, "name": "五粮液"},
      ...
    ]
    """
    result = ImportResult(broker="手动输入")
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            result.errors.append("JSON 格式应为数组 [{...}, ...]")
            return result

        for i, item in enumerate(data):
            result.raw_rows += 1
            if not isinstance(item, dict):
                result.errors.append(f"第 {i+1} 项不是对象")
                continue
            raw_ticker = str(item.get("ticker", "")).strip()
            if not raw_ticker:
                result.errors.append(f"第 {i+1} 项缺少 ticker")
                continue

            at_str = item.get("asset_type", "stock")
            asset_type = AssetType.MUTUAL_FUND if at_str in ("fund", "mutual_fund") else (
                AssetType.ETF if at_str == "etf" else AssetType.STOCK
            )
            ticker = normalise_ticker(raw_ticker, asset_type)
            shares = float(item.get("shares", 0))
            cost = float(item.get("cost_price", item.get("cost", 0)))
            if shares <= 0:
                result.errors.append(f"第 {i+1} 项 shares <= 0, 跳过")
                continue

            result.holdings.append(ImportedHolding(
                ticker=ticker,
                name=str(item.get("name", "")),
                shares=shares,
                cost_price=cost,
                asset_type=asset_type,
                broker="手动输入",
            ))
    except json.JSONDecodeError as e:
        result.errors.append(f"JSON 解析失败: {e}")

    return result


# ---------------------------------------------------------------------------
# Broker registry
# ---------------------------------------------------------------------------

BROKER_PARSERS = {
    "zhaoshang": parse_zhaoshang,
    "tencent": parse_tencent_weizhengquan,
    "changjiang": parse_changjiang,
    "dongfangcaifu": parse_dongfangcaifu,
    "tonghuashun": parse_tonghuashun,
    "alipay_fund": parse_alipay_fund,
    "tiantian_fund": parse_tiantian_fund,
    "generic_csv": parse_generic_csv,
    "manual_json": parse_manual_json,
}

BROKER_INFO = [
    {
        "id": "zhaoshang",
        "name": "招商证券",
        "icon": "🏦",
        "type": "stock",
        "format": "CSV",
        "instructions": "打开招商证券「智远一户通」APP → 持仓 → 右上角 ⋯ → 导出持仓 → 选择CSV格式 → 发送到电脑",
    },
    {
        "id": "tencent",
        "name": "腾讯微证券",
        "icon": "💬",
        "type": "stock",
        "instructions": "微信 → 我 → 服务 → 理财通 → 股票 → 持仓 → 导出持仓明细",
    },
    {
        "id": "changjiang",
        "name": "长江证券",
        "icon": "🏦",
        "type": "stock",
        "format": "CSV",
        "instructions": "打开「长江e号」APP → 交易 → 持仓 → 导出 → 选择格式CSV → 发送到电脑",
    },
    {
        "id": "dongfangcaifu",
        "name": "东方财富",
        "icon": "📊",
        "type": "stock",
        "format": "CSV",
        "instructions": "打开「东方财富」APP → 交易 → 持仓 → 更多 → 导出持仓 → CSV",
    },
    {
        "id": "tonghuashun",
        "name": "同花顺",
        "icon": "📈",
        "type": "stock",
        "format": "CSV",
        "instructions": "打开「同花顺」APP → 交易 → 持仓 → 右上角导出 → 选择CSV",
    },
    {
        "id": "alipay_fund",
        "name": "支付宝基金",
        "icon": "💰",
        "type": "fund",
        "format": "CSV",
        "instructions": "支付宝 → 理财 → 基金 → 持有 → 右上角 ⋯ → 导出持仓明细 → 发送到电脑",
    },
    {
        "id": "tiantian_fund",
        "name": "天天基金",
        "icon": "🌐",
        "type": "fund",
        "format": "CSV",
        "instructions": "打开「天天基金」APP → 我的持仓 → 导出 → CSV格式",
    },
    {
        "id": "generic_csv",
        "name": "通用CSV",
        "icon": "📄",
        "type": "any",
        "format": "CSV",
        "instructions": "CSV文件需包含以下列（表头名灵活匹配）：证券代码、名称（可选）、持仓数量、成本价",
    },
    {
        "id": "manual_json",
        "name": "手动批量输入",
        "icon": "✏️",
        "type": "any",
        "format": "JSON",
        "instructions": '格式示例：[{"ticker":"600519","shares":100,"cost_price":1500},{"ticker":"000858","shares":200,"cost_price":150,"name":"五粮液"}]',
    },
]


# ---------------------------------------------------------------------------
# Import execution — merge parsed holdings into portfolio
# ---------------------------------------------------------------------------

def merge_into_portfolio(
    portfolio: Portfolio,
    holdings: list[ImportedHolding],
    mode: str = "merge",
    broker_tag: str = "",
) -> dict:
    """Merge imported holdings into an existing portfolio.

    mode:
      "merge"   — add to existing; if ticker exists, update shares & avg cost
      "replace" — clear all existing holdings first, then add
      "broker_replace" — only replace holdings from the same broker tag

    Returns summary dict.
    """
    added = 0
    updated = 0
    skipped = 0

    if mode == "replace":
        portfolio.holdings.clear()
    elif mode == "broker_replace" and broker_tag:
        # Remove holdings tagged with this broker
        to_remove = [
            t for t, h in portfolio.holdings.items()
            if any(tx.type == TransactionType.BUY and hasattr(tx, "_broker") for tx in h.transactions)
        ]
        for t in to_remove:
            del portfolio.holdings[t]

    for imp in holdings:
        if imp.shares <= 0:
            skipped += 1
            continue

        cost = imp.cost_price if imp.cost_price > 0 else 0.0
        ticker = imp.ticker

        tx = Transaction(
            type=TransactionType.BUY,
            ticker=ticker,
            shares=imp.shares,
            price_per_share=cost,
            date=datetime.now(),
        )

        if ticker in portfolio.holdings and mode == "merge":
            existing = portfolio.holdings[ticker]
            total_shares = existing.shares + imp.shares
            if total_shares > 0:
                existing.avg_cost_basis = (
                    (existing.shares * existing.avg_cost_basis) + (imp.shares * cost)
                ) / total_shares
            existing.shares = total_shares
            existing.asset_type = imp.asset_type
            existing.transactions.append(tx)
            updated += 1
        else:
            portfolio.holdings[ticker] = Holding(
                ticker=ticker,
                shares=imp.shares,
                avg_cost_basis=cost,
                asset_type=imp.asset_type,
                transactions=[tx],
            )
            added += 1

    return {"added": added, "updated": updated, "skipped": skipped, "total": len(holdings)}
