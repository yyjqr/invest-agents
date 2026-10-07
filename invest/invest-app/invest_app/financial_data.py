"""Financial data layer — single-stock financial statement fetch.

Uses ``akshare`` to pull annual financial abstracts, caches results by
calendar quarter (in-memory + disk), and exposes a ``FinancialStatements``
dataclass holding all F-Score and factor input fields.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from invest_app.config import DATA_DIR, ensure_data_dir


@contextlib.contextmanager
def _no_proxy():
    """Temporarily clear proxy env-vars so direct HTTPS connections can succeed.

    On some Windows setups a system proxy is configured but unreachable, causing
    every HTTPS request from both ``requests`` and ``akshare`` to fail with a
    ProxyError.  Clearing the variables for the duration of the call lets the
    library connect directly.
    """
    _keys = [
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY",
    ]
    saved = {k: os.environ.pop(k, None) for k in _keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CACHE_DIR = DATA_DIR / "cache" / "financials"

_now = date.today()
_CURRENT_QUARTER = f"{_now.year}-Q{(_now.month - 1) // 3 + 1}"

_cache: dict[str, "FinancialStatements"] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    """Safely divide two numbers, returning None on division by zero
    or missing values."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _strip_code(ticker: str) -> str:
    """Return a plain 6-digit A-share code from a ticker string."""
    return ticker.strip().upper().split(".", maxsplit=1)[0]


def _is_annual_col(col_name: str) -> bool:
    """Return True if *col_name* looks like an annual-report column."""
    s = str(col_name)
    return "-12-31" in s or (len(s) == 8 and s.endswith("1231"))


# ---------------------------------------------------------------------------
# FinancialStatements dataclass
# ---------------------------------------------------------------------------

@dataclass
class FinancialStatements:
    """Holds all F-Score and factor input fields for one company.

    Every value field is ``float | None`` with a default of ``None``.
    """

    ticker: str
    name: str
    sector: str = ""
    report_date: str = ""

    # -- Income statement ---------------------------------------------------
    revenue: float | None = None
    revenue_1y_ago: float | None = None
    revenue_2y_ago: float | None = None
    revenue_3y_ago: float | None = None
    net_profit: float | None = None
    net_profit_1y_ago: float | None = None
    net_profit_2y_ago: float | None = None
    net_profit_3y_ago: float | None = None
    gross_margin: float | None = None
    gross_margin_1y_ago: float | None = None
    gross_margin_2y_ago: float | None = None
    rd_expense: float | None = None
    dividend_per_share: float | None = None

    # -- Balance sheet ------------------------------------------------------
    total_assets: float | None = None
    total_assets_1y_ago: float | None = None
    total_liabilities: float | None = None
    total_liabilities_1y_ago: float | None = None
    current_assets: float | None = None
    current_assets_1y_ago: float | None = None
    current_liabilities: float | None = None
    current_liabilities_1y_ago: float | None = None
    share_count: float | None = None
    share_count_1y_ago: float | None = None

    # -- Cash flow ----------------------------------------------------------
    operating_cf: float | None = None
    operating_cf_1y_ago: float | None = None
    operating_cf_2y_ago: float | None = None

    # -- ROE ----------------------------------------------------------------
    roe: float | None = None
    roe_1y_ago: float | None = None
    roe_2y_ago: float | None = None

    # -- Asset turnover -----------------------------------------------------
    asset_turnover: float | None = None
    asset_turnover_1y_ago: float | None = None

    # -- Financial-sector specific ------------------------------------------
    npl_ratio: float | None = None
    npl_ratio_1y_ago: float | None = None
    nim: float | None = None
    nim_1y_ago: float | None = None
    car: float | None = None
    car_1y_ago: float | None = None

    # -- Internal -----------------------------------------------------------
    _quarter: str = ""

    # ------------------------------------------------------------------
    # Derived methods
    # ------------------------------------------------------------------

    def f_score_ready(self) -> bool:
        """Return True when all F-Score prerequisites are available."""
        return (
            self.net_profit is not None
            and self.total_assets is not None
            and self.operating_cf is not None
            and self.revenue is not None
            and self.roa() is not None
        )

    def roa(self) -> float | None:
        """Return on Assets: net_profit / total_assets."""
        return _safe_divide(self.net_profit, self.total_assets)

    def debt_ratio(self) -> float | None:
        """Return debt ratio: total_liabilities / total_assets."""
        return _safe_divide(self.total_liabilities, self.total_assets)

    def current_ratio(self) -> float | None:
        """Return current ratio: current_assets / current_liabilities."""
        return _safe_divide(self.current_assets, self.current_liabilities)

    def is_financial_sector(self) -> bool:
        """Return True when the company operates in the financial sector."""
        return self.sector in ("银行", "非银金融")


# ---------------------------------------------------------------------------
# JSON serialisation (disk cache)
# ---------------------------------------------------------------------------

def _to_dict(fs: FinancialStatements) -> dict:
    """Convert a FinancialStatements to a JSON-serialisable dict."""
    return {
        "ticker": fs.ticker,
        "name": fs.name,
        "sector": fs.sector,
        "report_date": fs.report_date,
        "revenue": fs.revenue,
        "revenue_1y_ago": fs.revenue_1y_ago,
        "revenue_2y_ago": fs.revenue_2y_ago,
        "revenue_3y_ago": fs.revenue_3y_ago,
        "net_profit": fs.net_profit,
        "net_profit_1y_ago": fs.net_profit_1y_ago,
        "net_profit_2y_ago": fs.net_profit_2y_ago,
        "net_profit_3y_ago": fs.net_profit_3y_ago,
        "gross_margin": fs.gross_margin,
        "gross_margin_1y_ago": fs.gross_margin_1y_ago,
        "gross_margin_2y_ago": fs.gross_margin_2y_ago,
        "rd_expense": fs.rd_expense,
        "dividend_per_share": fs.dividend_per_share,
        "total_assets": fs.total_assets,
        "total_assets_1y_ago": fs.total_assets_1y_ago,
        "total_liabilities": fs.total_liabilities,
        "total_liabilities_1y_ago": fs.total_liabilities_1y_ago,
        "current_assets": fs.current_assets,
        "current_assets_1y_ago": fs.current_assets_1y_ago,
        "current_liabilities": fs.current_liabilities,
        "current_liabilities_1y_ago": fs.current_liabilities_1y_ago,
        "share_count": fs.share_count,
        "share_count_1y_ago": fs.share_count_1y_ago,
        "operating_cf": fs.operating_cf,
        "operating_cf_1y_ago": fs.operating_cf_1y_ago,
        "operating_cf_2y_ago": fs.operating_cf_2y_ago,
        "roe": fs.roe,
        "roe_1y_ago": fs.roe_1y_ago,
        "roe_2y_ago": fs.roe_2y_ago,
        "asset_turnover": fs.asset_turnover,
        "asset_turnover_1y_ago": fs.asset_turnover_1y_ago,
        "npl_ratio": fs.npl_ratio,
        "npl_ratio_1y_ago": fs.npl_ratio_1y_ago,
        "nim": fs.nim,
        "nim_1y_ago": fs.nim_1y_ago,
        "car": fs.car,
        "car_1y_ago": fs.car_1y_ago,
        "_quarter": fs._quarter,
    }


def _from_dict(data: dict) -> FinancialStatements:
    """Reconstruct a FinancialStatements from a plain dict."""
    return FinancialStatements(
        ticker=data.get("ticker", ""),
        name=data.get("name", ""),
        sector=data.get("sector", ""),
        report_date=data.get("report_date", ""),
        revenue=data.get("revenue"),
        revenue_1y_ago=data.get("revenue_1y_ago"),
        revenue_2y_ago=data.get("revenue_2y_ago"),
        revenue_3y_ago=data.get("revenue_3y_ago"),
        net_profit=data.get("net_profit"),
        net_profit_1y_ago=data.get("net_profit_1y_ago"),
        net_profit_2y_ago=data.get("net_profit_2y_ago"),
        net_profit_3y_ago=data.get("net_profit_3y_ago"),
        gross_margin=data.get("gross_margin"),
        gross_margin_1y_ago=data.get("gross_margin_1y_ago"),
        gross_margin_2y_ago=data.get("gross_margin_2y_ago"),
        rd_expense=data.get("rd_expense"),
        dividend_per_share=data.get("dividend_per_share"),
        total_assets=data.get("total_assets"),
        total_assets_1y_ago=data.get("total_assets_1y_ago"),
        total_liabilities=data.get("total_liabilities"),
        total_liabilities_1y_ago=data.get("total_liabilities_1y_ago"),
        current_assets=data.get("current_assets"),
        current_assets_1y_ago=data.get("current_assets_1y_ago"),
        current_liabilities=data.get("current_liabilities"),
        current_liabilities_1y_ago=data.get("current_liabilities_1y_ago"),
        share_count=data.get("share_count"),
        share_count_1y_ago=data.get("share_count_1y_ago"),
        operating_cf=data.get("operating_cf"),
        operating_cf_1y_ago=data.get("operating_cf_1y_ago"),
        operating_cf_2y_ago=data.get("operating_cf_2y_ago"),
        roe=data.get("roe"),
        roe_1y_ago=data.get("roe_1y_ago"),
        roe_2y_ago=data.get("roe_2y_ago"),
        asset_turnover=data.get("asset_turnover"),
        asset_turnover_1y_ago=data.get("asset_turnover_1y_ago"),
        npl_ratio=data.get("npl_ratio"),
        npl_ratio_1y_ago=data.get("npl_ratio_1y_ago"),
        nim=data.get("nim"),
        nim_1y_ago=data.get("nim_1y_ago"),
        car=data.get("car"),
        car_1y_ago=data.get("car_1y_ago"),
        _quarter=data.get("_quarter", ""),
    )


# ---------------------------------------------------------------------------
# akshare DataFrame parsing
# ---------------------------------------------------------------------------

# Indicator name mappings:  canonical_key → [akshare indicator names]
_INDICATOR_MAP: dict[str, list[str]] = {
    "revenue":        ["营业总收入", "营业总收入(元)"],
    "cost":           ["营业成本", "营业成本(元)"],
    "net_profit":     ["净利润", "净利润(元)", "归属于母公司所有者的净利润",
                       "归属母公司股东的净利润", "归属净利润"],
    "total_assets":   ["资产总计", "资产总计(元)"],
    "total_liabilities": ["负债合计", "负债合计(元)", "负债总计", "负债总计(元)"],
    "current_assets": ["流动资产合计", "流动资产合计(元)"],
    "current_liabilities": ["流动负债合计", "流动负债合计(元)"],
    "operating_cf":   ["经营现金流量净额",
                       "经营活动产生的现金流量净额",
                       "经营活动产生的现金流量净额(元)",
                       "经营活动现金流量净额"],
    "rd_expense":     ["研发费用", "研发费用(元)"],
    "equity":         ["股东权益合计(净资产)", "归属于母公司股东权益合计",
                       "归属于母公司股东权益合计(元)",
                       "归属母公司股东的权益合计",
                       "股东权益合计", "股东权益合计(元)"],
    "roe_direct":     ["净资产收益率(ROE)", "净资产收益率",
                       "加权平均净资产收益率",
                       "全面摊薄净资产收益率", "净资产收益率(%)",
                       "净资产收益率-加权"],
    "asset_turnover_direct": ["总资产周转率", "总资产周转率(次)"],
    "gross_margin_direct":   ["毛利率", "营业毛利率", "营业毛利率(%)",
                              "销售毛利率", "销售毛利率(%)"],
    "share_count":    ["总股本", "总股本(股)"],
    "dividend_per_share": ["每股股利", "每股股利(元)", "每股派息",
                           "每股派息(元)", "每股股息"],
    "npl_ratio":      ["不良贷款率", "不良贷款率(%)"],
    "nim":            ["净息差", "净息差(%)", "净利差"],
    "car":            ["资本充足率", "资本充足率(%)"],
}

# ---------------------------------------------------------------------------
# Balance-sheet supplement (Eastmoney) — fills fields missing from abstract
# ---------------------------------------------------------------------------

# Column name candidates used by stock_balance_sheet_by_report_em
_BS_COL_MAP: dict[str, list[str]] = {
    "total_assets":        ["TOTAL_ASSETS", "资产总计"],
    "total_liabilities":   ["TOTAL_LIABILITIES", "负债合计"],
    "current_assets":      ["TOTAL_CURRENT_ASSETS", "流动资产合计"],
    "current_liabilities": ["TOTAL_CURRENT_LIAB", "TOTAL_CURRENT_LIABILITIES", "流动负债合计"],
    "share_count":         ["SHARE_CAPITAL", "TOTAL_SHARE_CAPITAL", "总股本", "股本"],
}
_BS_DATE_COLS = ["REPORT_DATE", "报告期", "NOTICE_DATE"]

# Eastmoney F10 API rejects requests without a browser User-Agent (returns a
# block page with no ``hidctype`` element). akshare's balance-sheet helper sends
# no UA, so we query the endpoints directly instead.
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _em_symbol(code: str) -> str:
    """Map a bare 6-digit A-share code to the Eastmoney ``SH/SZ/BJ`` prefix form."""
    if code[0] == "6":
        return f"SH{code}"
    if code[0] in ("0", "3"):
        return f"SZ{code}"
    return f"BJ{code}"


def _fetch_balance_sheet_em(code: str) -> dict[str, list[float | None]]:
    """Fetch annual balance-sheet absolutes from Eastmoney.

    Returns ``{field: [newest_annual, prev_annual]}`` (up to 2 year-end values).
    Used to supplement balance-sheet fields that ``stock_financial_abstract``
    does not provide (total_assets, total_liabilities, current_assets,
    current_liabilities, share_count).

    Implemented against the Eastmoney F10 endpoints directly rather than
    ``akshare.stock_balance_sheet_by_report_em``, which Eastmoney currently
    blocks because it sends no ``User-Agent`` header.
    """
    try:
        import requests  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        return {}

    symbol = _em_symbol(code)

    # Use a proxy-free session so misconfigured system proxies don't block HTTPS.
    session = requests.Session()
    session.trust_env = False

    # 1. Company type — distinguishes general industrials from banks/insurers.
    try:
        resp = session.get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index",
            params={"type": "web", "code": symbol.lower()},
            headers=_EM_HEADERS,
            timeout=10,
        )
        company_type = BeautifulSoup(resp.text, "lxml").find(
            attrs={"id": "hidctype"}
        )["value"]
    except Exception:
        return {}

    # 2. Report dates available for this company.
    try:
        resp = session.get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbDateAjaxNew",
            params={"companyType": company_type, "reportDateType": "0", "code": symbol},
            headers=_EM_HEADERS,
            timeout=10,
        )
        dates = [str(item.get("REPORT_DATE", "")) for item in resp.json().get("data", [])]
        dates = [d for d in dates if d]
    except Exception:
        return {}

    if not dates:
        return {}

    # 3. Balance-sheet rows, in batches of 5 dates per request.
    frames: list = []
    for i in range(0, len(dates), 5):
        try:
            resp = session.get(
                "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
                params={
                    "companyType": company_type,
                    "reportDateType": "0",
                    "reportType": "1",
                    "dates": ",".join(dates[i : i + 5]),
                    "code": symbol,
                },
                headers=_EM_HEADERS,
                timeout=10,
            )
            data = resp.json().get("data")
            if not data:
                break
            frames.append(pd.DataFrame(data))
        except Exception:
            break

    if not frames:
        return {}

    df = pd.concat(frames, ignore_index=True)
    date_col = next((c for c in _BS_DATE_COLS if c in df.columns), None)
    if date_col is None:
        return {}

    try:
        df = df.copy()
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
        # Keep year-end (annual) rows only
        annual = (
            df[df["_date"].dt.month == 12]
            .sort_values("_date", ascending=False)
            .head(2)
        )
    except Exception:
        return {}

    if annual.empty:
        return {}

    result: dict[str, list[float | None]] = {}
    for field, candidates in _BS_COL_MAP.items():
        col = next((c for c in candidates if c in annual.columns), None)
        values: list[float | None] = []
        for _, row in annual.iterrows():
            if col is None:
                values.append(None)
                continue
            try:
                v = row[col]
                values.append(None if pd.isna(v) else float(v))
            except (ValueError, TypeError):
                values.append(None)
        while len(values) < 2:
            values.append(None)
        result[field] = values

    return result


def _fetch_company_name(code: str) -> str:
    """Return the company's short name, or the bare code on failure.

    Scrapes the Eastmoney F10 page title (``贵州茅台(600519.SH)…``), which sits
    on the same host as the balance-sheet supplement and accepts the same
    ``User-Agent``. Falls back to ``akshare.stock_individual_info_em`` (whose
    ``push2.eastmoney.com`` endpoint is sometimes proxy-blocked) and finally the
    bare code.
    """
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415

        symbol = _em_symbol(code)
        _sess = requests.Session()
        _sess.trust_env = False
        resp = _sess.get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index",
            params={"type": "web", "code": symbol.lower()},
            headers=_EM_HEADERS,
            timeout=10,
        )
        title = BeautifulSoup(resp.text, "lxml").find("title")
        text = title.get_text(strip=True) if title is not None else ""
        name = text.split("(", 1)[0].strip()
        if name:
            return name
    except Exception:
        pass

    try:
        import akshare as ak  # noqa: PLC0415

        with _no_proxy():
            info_df = ak.stock_individual_info_em(symbol=code)
        if info_df is not None and not info_df.empty:
            for _, row in info_df.iterrows():
                if str(row.iloc[0]).strip() == "股票简称":
                    return str(row.iloc[1]).strip()
    except Exception:
        pass

    return code


def _col_value(row, col_name: str) -> float | None:
    """Extract a float value from a DataFrame row for the given column."""
    try:
        import pandas as pd
        v = row.get(col_name)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def _extract_indicator(
    indicators: dict[str, list[float | None]],
    keys: list[str],
    index: int,
) -> float | None:
    """Pull the *index*-th value for the first matching indicator key."""
    for key in keys:
        vals = indicators.get(key)
        if vals is not None and len(vals) > index:
            v = vals[index]
            if v is not None:
                return v
    return None


def _parse_financial_df(
    df,
) -> tuple[dict[str, list[float | None]], list[str]]:
    """Parse an akshare *stock_financial_abstract* DataFrame.

    Returns ``(indicators, years)`` where *indicators* maps each indicator
    name to a list of up to 4 annual values (newest first) and *years* is
    the corresponding list of year strings (e.g. ``["2024-12-31", …]``).
    """
    import pandas as pd

    # 1. Find annual-report date columns -----------------------------------
    date_cols: list[str] = []
    for col in df.columns:
        if _is_annual_col(str(col)):
            date_cols.append(str(col))
    date_cols.sort(reverse=True)
    date_cols = date_cols[:4]
    if not date_cols:
        return {}, []

    # 2. Find the indicator-name column ------------------------------------
    indicator_col: str | None = None
    for col in df.columns:
        if str(col).lower() in ("指标", "item", "indicator", "name"):
            indicator_col = str(col)
            break

    # 3. Build indicators dict ---------------------------------------------
    indicators: dict[str, list[float | None]] = {}

    if indicator_col is not None:
        for _, row in df.iterrows():
            name = str(row[indicator_col]).strip()
            if not name:
                continue
            vals: list[float | None] = []
            for dc in date_cols:
                vals.append(_col_value(row, dc))
            if name in indicators:
                # If duplicate, prefer the row with more non-None values
                existing = indicators[name]
                if sum(1 for v in vals if v is not None) > sum(
                    1 for v in existing if v is not None
                ):
                    indicators[name] = vals
            else:
                indicators[name] = vals
    else:
        # Index is the indicator name
        for idx_val in df.index:
            name = str(idx_val).strip()
            if not name:
                continue
            vals: list[float | None] = []
            for dc in date_cols:
                try:
                    v = df.loc[idx_val, dc]
                    vals.append(_col_value({"v": v}, "v"))  # re-use _col_value logic
                except (KeyError, ValueError, TypeError):
                    vals.append(None)
            if name in indicators:
                existing = indicators[name]
                if sum(1 for v in vals if v is not None) > sum(
                    1 for v in existing if v is not None
                ):
                    indicators[name] = vals
            else:
                indicators[name] = vals

    return indicators, date_cols


# ---------------------------------------------------------------------------
# akshare fetch
# ---------------------------------------------------------------------------

def _fetch_from_akshare(code: str) -> FinancialStatements | None:
    """Fetch financial data for *code* from akshare.

    All network calls are wrapped in try/except and return ``None`` on
    failure.  ``akshare`` is lazily imported inside this function.
    """
    try:
        import akshare as ak  # noqa: PLC0415 — lazy import
    except ImportError:
        return None

    # 1. Fetch financial abstract DataFrame --------------------------------
    try:
        with _no_proxy():
            df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return None

    if df is None:
        return None
    try:
        empty = df.empty
    except Exception:
        empty = True
    if empty:
        return None

    # 2. Parse into {indicator: [y0, y1, y2, y3]} ---------------------------
    indicators, years = _parse_financial_df(df)
    if not years or not indicators:
        return None

    # 3. Company name -------------------------------------------------------
    name = _fetch_company_name(code)

    # 4. Sector classification ----------------------------------------------
    sector = ""
    try:
        from invest_app.industry import classify_sector  # noqa: PLC0415
        sector = classify_sector(code, name)
    except Exception:
        pass

    # 5. Extract raw indicator values (per year) ---------------------------
    def _v(keys: list[str], idx: int) -> float | None:
        return _extract_indicator(indicators, keys, idx)

    rev0  = _v(_INDICATOR_MAP["revenue"], 0)
    rev1  = _v(_INDICATOR_MAP["revenue"], 1)
    rev2  = _v(_INDICATOR_MAP["revenue"], 2)
    rev3  = _v(_INDICATOR_MAP["revenue"], 3)
    cost0 = _v(_INDICATOR_MAP["cost"], 0)
    cost1 = _v(_INDICATOR_MAP["cost"], 1)
    cost2 = _v(_INDICATOR_MAP["cost"], 2)
    cost3 = _v(_INDICATOR_MAP["cost"], 3)
    np0   = _v(_INDICATOR_MAP["net_profit"], 0)
    np1   = _v(_INDICATOR_MAP["net_profit"], 1)
    np2   = _v(_INDICATOR_MAP["net_profit"], 2)
    np3   = _v(_INDICATOR_MAP["net_profit"], 3)
    ta0   = _v(_INDICATOR_MAP["total_assets"], 0)
    ta1   = _v(_INDICATOR_MAP["total_assets"], 1)
    tl0   = _v(_INDICATOR_MAP["total_liabilities"], 0)
    tl1   = _v(_INDICATOR_MAP["total_liabilities"], 1)
    ca0   = _v(_INDICATOR_MAP["current_assets"], 0)
    ca1   = _v(_INDICATOR_MAP["current_assets"], 1)
    cl0   = _v(_INDICATOR_MAP["current_liabilities"], 0)
    cl1   = _v(_INDICATOR_MAP["current_liabilities"], 1)
    ocf0  = _v(_INDICATOR_MAP["operating_cf"], 0)
    ocf1  = _v(_INDICATOR_MAP["operating_cf"], 1)
    ocf2  = _v(_INDICATOR_MAP["operating_cf"], 2)
    ocf3  = _v(_INDICATOR_MAP["operating_cf"], 3)
    rd0   = _v(_INDICATOR_MAP["rd_expense"], 0)
    eq0   = _v(_INDICATOR_MAP["equity"], 0)
    eq1   = _v(_INDICATOR_MAP["equity"], 1)
    eq2   = _v(_INDICATOR_MAP["equity"], 2)
    eq3   = _v(_INDICATOR_MAP["equity"], 3)
    sc0   = _v(_INDICATOR_MAP["share_count"], 0)
    sc1   = _v(_INDICATOR_MAP["share_count"], 1)
    dpr0  = _v(_INDICATOR_MAP["dividend_per_share"], 0)

    # 5b. Supplement balance-sheet absolutes from Eastmoney if abstract is missing them
    if ta0 is None or tl0 is None or ca0 is None or cl0 is None:
        _bs = _fetch_balance_sheet_em(code)
        def _bs_val(field: str, idx: int) -> float | None:
            return (_bs.get(field) or [None, None])[idx] if idx < len(_bs.get(field) or []) else None
        if ta0 is None:
            ta0 = _bs_val("total_assets", 0)
        if ta1 is None:
            ta1 = _bs_val("total_assets", 1)
        if tl0 is None:
            tl0 = _bs_val("total_liabilities", 0)
        if tl1 is None:
            tl1 = _bs_val("total_liabilities", 1)
        if ca0 is None:
            ca0 = _bs_val("current_assets", 0)
        if ca1 is None:
            ca1 = _bs_val("current_assets", 1)
        if cl0 is None:
            cl0 = _bs_val("current_liabilities", 0)
        if cl1 is None:
            cl1 = _bs_val("current_liabilities", 1)
        if sc0 is None:
            sc0 = _bs_val("share_count", 0)
        if sc1 is None:
            sc1 = _bs_val("share_count", 1)

    # 6. Compute derived fields ---------------------------------------------
    # Gross margin: from direct indicator, or compute from (revenue - cost)/revenue
    gm0 = _v(_INDICATOR_MAP["gross_margin_direct"], 0)
    if gm0 is not None and abs(gm0) < 1:
        # Already a ratio like 0.92 — check magnitude
        pass
    elif gm0 is not None:
        # Might be a percentage like 92.0
        gm0 = gm0 / 100.0
    if gm0 is None:
        gm0 = _safe_divide((rev0 or 0) - (cost0 or 0), rev0)

    gm1 = _v(_INDICATOR_MAP["gross_margin_direct"], 1)
    if gm1 is not None and gm1 > 1:
        gm1 = gm1 / 100.0
    if gm1 is None:
        gm1 = _safe_divide((rev1 or 0) - (cost1 or 0), rev1)

    gm2 = _v(_INDICATOR_MAP["gross_margin_direct"], 2)
    if gm2 is not None and gm2 > 1:
        gm2 = gm2 / 100.0
    if gm2 is None:
        gm2 = _safe_divide((rev2 or 0) - (cost2 or 0), rev2)

    gm3 = _v(_INDICATOR_MAP["gross_margin_direct"], 3)
    if gm3 is not None and gm3 > 1:
        gm3 = gm3 / 100.0
    if gm3 is None:
        gm3 = _safe_divide((rev3 or 0) - (cost3 or 0), rev3)

    # ROE: from direct indicator, or compute from net_profit / equity
    roe0 = _v(_INDICATOR_MAP["roe_direct"], 0)
    if roe0 is not None and abs(roe0) <= 1:
        pass  # already a ratio
    elif roe0 is not None:
        roe0 = roe0 / 100.0
    if roe0 is None:
        roe0 = _safe_divide(np0, eq0)

    roe1 = _v(_INDICATOR_MAP["roe_direct"], 1)
    if roe1 is not None and roe1 > 1:
        roe1 = roe1 / 100.0
    if roe1 is None:
        roe1 = _safe_divide(np1, eq1)

    roe2 = _v(_INDICATOR_MAP["roe_direct"], 2)
    if roe2 is not None and roe2 > 1:
        roe2 = roe2 / 100.0
    if roe2 is None:
        roe2 = _safe_divide(np2, eq2)

    roe3 = _v(_INDICATOR_MAP["roe_direct"], 3)
    if roe3 is not None and roe3 > 1:
        roe3 = roe3 / 100.0
    if roe3 is None:
        roe3 = _safe_divide(np3, eq3)

    # Asset turnover: from direct indicator, or compute from revenue / total_assets
    at0 = _v(_INDICATOR_MAP["asset_turnover_direct"], 0)
    if at0 is None:
        at0 = _safe_divide(rev0, ta0)

    at1 = _v(_INDICATOR_MAP["asset_turnover_direct"], 1)
    if at1 is None:
        at1 = _safe_divide(rev1, ta1)

    # Financial-sector specific
    npl0 = _v(_INDICATOR_MAP["npl_ratio"], 0)
    npl1 = _v(_INDICATOR_MAP["npl_ratio"], 1)
    nim0 = _v(_INDICATOR_MAP["nim"], 0)
    nim1 = _v(_INDICATOR_MAP["nim"], 1)
    car0 = _v(_INDICATOR_MAP["car"], 0)
    car1 = _v(_INDICATOR_MAP["car"], 1)

    # 7. Build and return --------------------------------------------------
    return FinancialStatements(
        ticker=code,
        name=name,
        sector=sector,
        report_date=years[0] if years else "",
        revenue=rev0,
        revenue_1y_ago=rev1,
        revenue_2y_ago=rev2,
        revenue_3y_ago=rev3,
        net_profit=np0,
        net_profit_1y_ago=np1,
        net_profit_2y_ago=np2,
        net_profit_3y_ago=np3,
        gross_margin=gm0,
        gross_margin_1y_ago=gm1,
        gross_margin_2y_ago=gm2,
        rd_expense=rd0,
        dividend_per_share=dpr0,
        total_assets=ta0,
        total_assets_1y_ago=ta1,
        total_liabilities=tl0,
        total_liabilities_1y_ago=tl1,
        current_assets=ca0,
        current_assets_1y_ago=ca1,
        current_liabilities=cl0,
        current_liabilities_1y_ago=cl1,
        share_count=sc0,
        share_count_1y_ago=sc1,
        operating_cf=ocf0,
        operating_cf_1y_ago=ocf1,
        operating_cf_2y_ago=ocf2,
        roe=roe0,
        roe_1y_ago=roe1,
        roe_2y_ago=roe2,
        asset_turnover=at0,
        asset_turnover_1y_ago=at1,
        npl_ratio=npl0,
        npl_ratio_1y_ago=npl1,
        nim=nim0,
        nim_1y_ago=nim1,
        car=car0,
        car_1y_ago=car1,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_financials(ticker: str) -> FinancialStatements | None:
    """Fetch financial statements for *ticker*.

    Caching (in-memory + disk) is keyed by calendar quarter.  A cache hit
    is served when the stored ``_quarter`` matches the current quarter.

    Returns ``None`` when the network call fails and no cache is available.
    """
    code = _strip_code(ticker)

    # 1. In-memory cache ---------------------------------------------------
    if code in _cache:
        cached = _cache[code]
        if cached._quarter == _CURRENT_QUARTER:
            return cached

    # 2. Disk cache --------------------------------------------------------
    cache_file = _CACHE_DIR / f"{code}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("_quarter") == _CURRENT_QUARTER:
                fs = _from_dict(data)
                _cache[code] = fs
                return fs
        except Exception:
            pass

    # 3. Fetch from akshare ------------------------------------------------
    fs = _fetch_from_akshare(code)
    if fs is None:
        return None

    fs._quarter = _CURRENT_QUARTER
    _cache[code] = fs

    # 4. Persist to disk cache ---------------------------------------------
    try:
        ensure_data_dir()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(_to_dict(fs), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass

    return fs


def get_market_value(ticker: str) -> dict:
    """Return PE, PB, dividend yield, and price for *ticker*.

    Uses ``invest_app.market_data.get_quote`` for price / PE / PB and
    ``get_financials`` for the dividend-per-share needed to compute
    dividend yield.

    Returns ``{"pe": …, "pb": …, "dividend_yield": …, "price": …}``.
    """
    result: dict = {"pe": None, "pb": None, "dividend_yield": None, "price": None}

    try:
        from invest_app.market_data import get_quote  # noqa: PLC0415
        q = get_quote(ticker)
        result["price"] = q.price
        result["pe"] = q.pe_ttm
        result["pb"] = q.pb_ratio
    except Exception:
        pass

    # Dividend yield = dividend_per_share / price * 100
    try:
        fs = get_financials(ticker)
        if fs is not None and fs.dividend_per_share is not None and result["price"]:
            result["dividend_yield"] = fs.dividend_per_share / result["price"] * 100
    except Exception:
        pass

    return result
