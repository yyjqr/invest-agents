"""Market data retrieval using Yahoo Finance."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import quote_from_bytes
from urllib.request import Request, urlopen

import pandas as pd
import yfinance as yf

from invest_app.cache import read_history_cache, write_history_cache
from invest_app.config import get_alpha_vantage_key

# Max seconds to wait for a single yfinance network call.
_YF_TIMEOUT = 8


def _yf_info(ticker_str: str) -> dict:
    """Fetch yfinance .info with a hard thread-based timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: yf.Ticker(ticker_str).info)
        return future.result(timeout=_YF_TIMEOUT)


def _yf_history(ticker_str: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch yfinance .history with a hard thread-based timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            lambda: yf.Ticker(ticker_str).history(period=period, interval=interval)
        )
        return future.result(timeout=_YF_TIMEOUT)


@dataclass
class Quote:
    """A snapshot quote for a ticker."""

    ticker: str
    price: float
    change: float
    change_pct: float
    name: str
    market_cap: float | None = None       # legacy — prefer market_cap_total
    pe_ratio: float | None = None         # legacy — prefer pe_ttm
    turnover: float | None = None         # 成交额 (元)
    volume: float | None = None           # 成交量 (手)
    # ── Extended company info (Tencent API fields) ──────────────────
    pe_ttm: float | None = None           # PE (TTM)  [39]
    pb_ratio: float | None = None         # PB ratio  [43]
    market_cap_total: float | None = None # 总市值 (亿元)  [44]
    market_cap_float: float | None = None # 流通市值 (亿元) [45]
    turnover_rate: float | None = None    # 换手率 (%)  [38]


@dataclass
class SearchResult:
    """Search result for stock/fund instruments."""

    symbol: str
    code: str
    name: str
    exchange: str
    category: str


def _period_to_cn_days(period: str) -> int:
    mapping = {
        "5d": 5,
        "1mo": 22,
        "3mo": 66,
        "6mo": 132,
        "1y": 260,
        "2y": 520,
        "5y": 1300,
        "10y": 2500,
        "max": 4000,
    }
    return mapping.get(period, 260)


def _classify_cn_code(code: str, name: str = "") -> str:
    if "基金" in name or code.startswith(("15", "16", "18", "50", "51", "56", "58")):
        return "fund"
    if code.startswith("688"):
        return "star"
    return "stock"


def _cn_market_prefix(ticker: str) -> str | None:
    """Return Tencent market prefix for a China A-share code.

    - sh: Shanghai main board / STAR / B-share (6xx, 5xx, 9xx)
    - sz: Shenzhen board (0xx, 2xx, 3xx)
    """
    value = ticker.strip().upper()
    if "." in value:
        base, suffix = value.split(".", maxsplit=1)
        if len(base) == 6 and base.isdigit():
            if suffix == "SS":
                return "sh"
            if suffix == "SZ":
                return "sz"
        return None

    if len(value) == 6 and value.isdigit():
        if value.startswith(("5", "6", "9")):
            return "sh"
        return "sz"
    return None


def _try_cn_quote_tencent(ticker: str) -> Quote | None:
    """Try fetching China A-share quote from Tencent quote API.

    This endpoint is plain HTTP and avoids local OpenSSL/curl issues that can
    happen in some yfinance environments on Windows.
    """
    prefix = _cn_market_prefix(ticker)
    if prefix is None:
        return None

    raw = ticker.strip().upper()
    code = raw.split(".", maxsplit=1)[0]
    symbol = f"{prefix}{code}"
    url = f"http://qt.gtimg.cn/q={symbol}"

    try:
        with urlopen(url, timeout=8) as resp:  # nosec B310 - fixed public quote endpoint
            text = resp.read().decode("gbk", errors="ignore")
    except (OSError, URLError):
        return None

    # Example format: v_sh600519="1~贵州茅台~600519~1499.99~..."
    if "\"" not in text:
        return None
    payload = text.split("\"", maxsplit=2)[1]
    fields = payload.split("~")
    if len(fields) < 33:
        return None

    name = fields[1] or code
    last = fields[3]
    prev_close = fields[4]

    try:
        current = float(last)
        prev = float(prev_close)
    except ValueError:
        return None

    change = current - prev
    change_pct = (change / prev * 100) if prev else 0.0

    # fields[36]=成交量(手), [37]=成交额(万元), [38]=换手率(%), [39]=PE(TTM), [43]=PB, [44]=总市值(亿), [45]=流通市值(亿)
    def _f(idx: int) -> float | None:
        try:
            v = fields[idx] if len(fields) > idx else ""
            return float(v) if v else None
        except (ValueError, IndexError):
            return None

    raw_turnover = _f(37)
    turnover = raw_turnover * 10_000 if raw_turnover is not None else None  # 万元 → 元
    volume = _f(36)
    pe_ttm = _f(39)
    pb_ratio = _f(43)
    market_cap_total = _f(44)
    market_cap_float = _f(45)
    turnover_rate = _f(38)

    return Quote(
        ticker=normalize_ticker(code),
        price=current,
        change=change,
        change_pct=change_pct,
        name=name,
        turnover=turnover,
        volume=volume,
        pe_ttm=pe_ttm,
        pb_ratio=pb_ratio,
        market_cap_total=market_cap_total,
        market_cap_float=market_cap_float,
        turnover_rate=turnover_rate,
    )


def _cn_eastmoney_secid(ticker: str) -> str | None:
    """Return Eastmoney `secid` for a China A-share code."""
    prefix = _cn_market_prefix(ticker)
    if prefix is None:
        return None

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    market = "1" if prefix == "sh" else "0"
    return f"{market}.{code}"


def _try_cn_quote_eastmoney(ticker: str) -> Quote | None:
    """Best-effort fallback quote for China A-shares via Eastmoney."""
    secid = _cn_eastmoney_secid(ticker)
    if secid is None:
        return None

    url = (
        "http://push2.eastmoney.com/api/qt/stock/get?"
        "ut=fa5fd1943c7b386f172d6893dbfba10b&invt=2&fltt=2&fields="
        "f57,f58,f43,f47,f48,f60,f116,f117,f162,f167,f168,f169,f170&"
        f"secid={secid}"
    )

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:  # nosec B310 - fixed public quote endpoint
            payload = resp.read().decode("utf-8", errors="ignore")
    except (OSError, URLError):
        return None

    try:
        node = json.loads(payload).get("data") or {}
    except (ValueError, TypeError, AttributeError):
        return None

    if not node:
        return None

    def _num(field: str, scale: float = 1.0) -> float | None:
        value = node.get(field)
        if value in (None, "", "-"):
            return None
        try:
            return float(value) / scale
        except (TypeError, ValueError):
            return None

    code = str(node.get("f57", "") or ticker).split(".", maxsplit=1)[0]
    name = str(node.get("f58", "") or code)
    current = _num("f43", 100)
    if current is None or current <= 0:
        return None

    prev = _num("f60", 100)
    change = _num("f169", 100)
    change_pct = _num("f170", 100)

    if change is None and prev is not None:
        change = current - prev
    if change_pct is None and prev not in (None, 0):
        change_pct = (current - prev) / prev * 100

    return Quote(
        ticker=normalize_ticker(code),
        price=current,
        change=change or 0.0,
        change_pct=change_pct or 0.0,
        name=name,
        turnover=_num("f48"),
        volume=_num("f47"),
        pe_ttm=_num("f162", 100),
        pb_ratio=_num("f167", 100),
        market_cap_total=_num("f116"),
        market_cap_float=_num("f117"),
        turnover_rate=_num("f168", 100),
    )


def _try_cn_history_tencent(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame | None:
    """Try fetching China A-share daily kline history from Tencent API."""
    if interval != "1d":
        return None

    prefix = _cn_market_prefix(ticker)
    if prefix is None:
        return None

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    symbol = f"{prefix}{code}"
    days = _period_to_cn_days(period)
    url = (
        "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={symbol},day,,,{days},qfq"
    )

    try:
        with urlopen(url, timeout=8) as resp:  # nosec B310 - fixed public quote endpoint
            payload = resp.read().decode("utf-8", errors="ignore")
    except (OSError, URLError):
        return None

    try:
        data = json.loads(payload)
        node = data.get("data", {}).get(symbol, {})
        rows = node.get("qfqday") or node.get("day") or []
    except (ValueError, TypeError, AttributeError):
        return None

    if not rows:
        return None

    records: list[dict[str, float | int | str]] = []
    for row in rows:
        # date, open, close, high, low, volume
        if len(row) < 6:
            continue
        try:
            records.append(
                {
                    "Date": row[0],
                    "Open": float(row[1]),
                    "Close": float(row[2]),
                    "High": float(row[3]),
                    "Low": float(row[4]),
                    "Volume": float(row[5]),
                }
            )
        except (TypeError, ValueError):
            continue

    if not records:
        return None

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"]) 
    df = df.set_index("Date").sort_index()
    return df


def get_intraday(ticker: str) -> pd.DataFrame:
    """Fetch today's intraday (minute-level) data for a China A-share ticker.

    Returns DataFrame with columns: Time, Price, Volume, AvgPrice.
    Falls back to an empty DataFrame for non-CN tickers.
    """
    prefix = _cn_market_prefix(ticker)
    if prefix is None:
        return pd.DataFrame()

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    symbol = f"{prefix}{code}"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"

    try:
        with urlopen(url, timeout=8) as resp:
            payload = resp.read().decode("utf-8", errors="ignore")
    except (OSError, URLError):
        return pd.DataFrame()

    try:
        data = json.loads(payload)
        node = data.get("data", {}).get(symbol, {})
        date_str = node.get("date", "")
        rows = node.get("data", {}).get("data", [])
        if not rows:
            # Alternative structure
            rows = node.get("data", [])
    except (ValueError, TypeError, AttributeError):
        return pd.DataFrame()

    if not rows or not date_str:
        return pd.DataFrame()

    records: list[dict] = []
    for row in rows:
        # Each row is "HHMM price volume" e.g. "0930 12.50 12345"
        if isinstance(row, str):
            parts = row.split()
            if len(parts) < 3:
                continue
            try:
                time_str = parts[0]
                price = float(parts[1])
                vol = float(parts[2])
                records.append({
                    "Time": f"{date_str} {time_str[:2]}:{time_str[2:]}",
                    "Price": price,
                    "Volume": vol,
                })
            except (ValueError, IndexError):
                continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["Time"] = pd.to_datetime(df["Time"], errors="coerce")
    df = df.dropna(subset=["Time"]).set_index("Time").sort_index()
    return df


def normalize_ticker(ticker: str) -> str:
    """Normalize ticker symbols for data providers.

    Examples:
    - 600519 -> 600519.SS
    - 688001 -> 688001.SS
    - 000001 -> 000001.SZ
    """
    value = ticker.strip().upper()
    if "." in value:
        return value
    if len(value) == 6 and value.isdigit():
        if value.startswith(("5", "6", "9")):
            return f"{value}.SS"
        return f"{value}.SZ"
    return value


def _is_cn_ticker(ticker: str) -> bool:
    """Return True if ticker looks like a China A-share code."""
    return _cn_market_prefix(ticker) is not None


def resolve_ticker(query: str) -> str:
    """Resolve a user query to a canonical ticker code.

    - 6-digit numeric codes (e.g. '600519') or codes with exchange suffix
      (e.g. '600519.SS') are returned unchanged.
    - Chinese names, abbreviations, or partial codes (e.g. '茅台', 'TCL',
      '长江电力') are looked up via Tencent smartbox and the best-matching
      6-digit code is returned.
    - If search returns nothing, the original query is returned so that
      downstream code can produce a meaningful error.
    """
    q = query.strip()
    if not q:
        return q

    # Already a valid code — no search needed
    upper = q.upper()
    if (len(upper) == 6 and upper.isdigit()) or (
        "." in upper and upper.split(".")[0].isdigit()
    ):
        return q

    # Try Tencent smartbox search
    results = search_instruments(q, limit=1)
    if results:
        return results[0].code  # 6-digit plain code like '600519'

    # Fallback: return original (will fail with a clear error)
    return q


def _cn_ticker_to_av_symbol(ticker: str) -> str | None:
    """Convert a CN A-share ticker to Alpha Vantage symbol format.

    Shanghai (sh) → CODE.SHG, Shenzhen (sz) → CODE.SHZ.
    Returns None for non-CN tickers.
    """
    prefix = _cn_market_prefix(ticker)
    if prefix is None:
        return None
    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    return f"{code}.SHG" if prefix == "sh" else f"{code}.SHZ"


def _try_alpha_vantage_quote(ticker: str) -> Quote | None:
    """Tertiary fallback quote via Alpha Vantage GLOBAL_QUOTE endpoint.

    Requires ALPHA_VANTAGE_KEY env variable or api_keys.json entry.
    Free tier: 25 req/day (sufficient for personal use).
    Premium plans unlock higher throughput if needed.
    """
    api_key = get_alpha_vantage_key()
    if not api_key:
        return None

    # Determine symbol: CN A-share → CODE.SHG / CODE.SHZ, otherwise direct
    av_symbol = _cn_ticker_to_av_symbol(ticker)
    if av_symbol is None:
        av_symbol = normalize_ticker(ticker)  # e.g. AAPL, 9988.HK

    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={av_symbol}&apikey={api_key}"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None

    gq = data.get("Global Quote") or {}
    price_str = gq.get("05. price", "").strip()
    if not price_str:
        return None  # quota exceeded or unknown ticker

    try:
        price = float(price_str)
        change = float(gq.get("09. change", "0") or "0")
        pct_raw = (gq.get("10. change percent") or "0%").strip("%")
        change_pct = float(pct_raw) if pct_raw else 0.0
    except ValueError:
        return None

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    return Quote(
        ticker=normalize_ticker(code if _cn_market_prefix(ticker) else ticker),
        price=price,
        change=change,
        change_pct=change_pct,
        name=gq.get("01. symbol", code),
    )


def get_quote(ticker: str) -> Quote:
    """Fetch the latest quote for a single ticker.

    Accepts 6-digit codes, exchange-suffixed codes, or Chinese names/
    abbreviations — the latter are auto-resolved via Tencent search.

    Priority for A-shares:
      1. Tencent qt.gtimg.cn  (free, real-time, most fields)
      2. Eastmoney push2      (free, real-time, fallback)
      3. Alpha Vantage        (API key required, cross-border reliable)
    """
    ticker = resolve_ticker(ticker)  # resolve names like '长江电力' → '600900'
    cn_quote = _try_cn_quote_tencent(ticker)
    if cn_quote is not None:
        return cn_quote

    cn_quote = _try_cn_quote_eastmoney(ticker)
    if cn_quote is not None:
        return cn_quote

    # Third-party API fallback (works when domestic endpoints are unreachable)
    if _is_cn_ticker(ticker):
        av_quote = _try_alpha_vantage_quote(ticker)
        if av_quote is not None:
            return av_quote
        raise ValueError(
            f"无法从腾讯/东财行情获取 {ticker} 的数据，请检查代码是否正确或稍后重试"
        )

    normalized_ticker = normalize_ticker(ticker)
    try:
        info = _yf_info(normalized_ticker)
    except FuturesTimeout:
        raise ValueError(
            f"Yahoo Finance 请求超时（{_YF_TIMEOUT}s），请检查网络或稍后重试"
        )
    except Exception as exc:
        raise ValueError(f"Yahoo Finance 请求失败: {exc}") from exc

    prev_close = info.get("previousClose", 0)
    current = info.get("currentPrice") or info.get("regularMarketPrice") or prev_close
    change = current - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    return Quote(
        ticker=normalized_ticker,
        price=current,
        change=change,
        change_pct=change_pct,
        name=info.get("shortName", normalized_ticker),
        market_cap=info.get("marketCap"),
        pe_ratio=info.get("trailingPE"),
    )


def get_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch historical price data for a ticker.

    Args:
        ticker: Stock/fund ticker symbol. Chinese names are auto-resolved.
        period: Data period (e.g. "1mo", "6mo", "1y", "5y", "max").
        interval: Data interval (e.g. "1d", "1wk", "1mo").

    Returns:
        DataFrame with Date, Open, High, Low, Close, Volume columns.
    """
    ticker = resolve_ticker(ticker)  # resolve names like '长江电力' → '600900'

    # Serve from Parquet cache when available (daily bars only)
    if interval == "1d":
        cached = read_history_cache(ticker, period, interval)
        if cached is not None:
            return cached

    cn_df = _try_cn_history_tencent(ticker, period=period, interval=interval)
    if cn_df is not None and not cn_df.empty:
        if interval == "1d":
            write_history_cache(ticker, period, cn_df, interval)
        return cn_df

    if _is_cn_ticker(ticker):
        return pd.DataFrame()  # no yfinance fallback for A-shares

    try:
        df = _yf_history(normalize_ticker(ticker), period=period, interval=interval)
    except Exception:
        return pd.DataFrame()
    if not df.empty and interval == "1d":
        write_history_cache(ticker, period, df, interval)
    return df


def search_instruments(query: str, limit: int = 10) -> list[SearchResult]:
    """Search Chinese stocks/funds by code or name (supports Chinese keywords)."""
    keyword = query.strip()
    if not keyword:
        return []

    tencent_results = _search_instruments_tencent(keyword, limit)
    if tencent_results:
        return tencent_results

    return _search_instruments_sina(keyword, limit)


def _search_instruments_tencent(query: str, limit: int) -> list[SearchResult]:
    encoded = quote_from_bytes(query.encode("utf-8", errors="ignore"), safe="")
    url = f"http://smartbox.gtimg.cn/s3/?q={encoded}&t=all"

    try:
        with urlopen(url, timeout=8) as resp:  # nosec B310 - fixed public search endpoint
            text = resp.read().decode("gbk", errors="ignore")
    except (OSError, URLError):
        return []

    if '"' not in text:
        return []

    payload = text.split('"', maxsplit=2)[1].strip()
    if not payload:
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()

    for raw_item in payload.split("^"):
        parts = [part.strip() for part in raw_item.split("~")]
        if len(parts) < 4:
            continue

        market = parts[0].lower()
        code = parts[1]
        if len(code) != 6 or not code.isdigit():
            continue

        raw_name = parts[2]
        try:
            name = raw_name.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            name = raw_name

        symbol = f"{market}{code}"
        if symbol in seen:
            continue

        if market == "sh":
            exchange = "SSE"
        elif market == "sz":
            exchange = "SZSE"
        else:
            exchange = "OTHER"

        results.append(
            SearchResult(
                symbol=symbol,
                code=code,
                name=name,
                exchange=exchange,
                category=_classify_cn_code(code, name),
            )
        )
        seen.add(symbol)

        if len(results) >= limit:
            break

    return results


def _search_instruments_sina(query: str, limit: int) -> list[SearchResult]:
    encoded = quote_from_bytes(query.encode("gbk", errors="ignore"), safe="")
    url = f"http://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key={encoded}"

    try:
        with urlopen(url, timeout=8) as resp:  # nosec B310 - fixed public search endpoint
            text = resp.read().decode("gbk", errors="ignore")
    except (OSError, URLError):
        return []

    if '"' not in text:
        return []

    payload = text.split('"', maxsplit=2)[1]
    if not payload:
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()

    for raw_item in payload.split(";"):
        parts = [part.strip() for part in raw_item.split(",")]
        if len(parts) < 5:
            continue

        symbol = parts[2].lower()
        code = parts[3]
        name = parts[4]

        if len(code) != 6 or not code.isdigit() or symbol in seen:
            continue

        if symbol.startswith("sh"):
            exchange = "SSE"
        elif symbol.startswith("sz"):
            exchange = "SZSE"
        else:
            exchange = "OTHER"

        results.append(
            SearchResult(
                symbol=symbol,
                code=code,
                name=name,
                exchange=exchange,
                category=_classify_cn_code(code, name),
            )
        )
        seen.add(symbol)

        if len(results) >= limit:
            break

    return results


def get_bulk_quotes(tickers: list[str]) -> dict[str, Quote]:
    """Fetch quotes for multiple tickers."""
    return {t: get_quote(t) for t in tickers}


def get_batch_quotes_tencent(codes: list[str]) -> list[Quote]:
    """Batch-fetch quotes for multiple A-share codes via a single Tencent API call.

    Accepts 6-digit codes like ['600519', '000001']. Returns list of Quote.
    Much faster than calling get_quote() in a loop because Tencent supports
    comma-separated multi-code requests.

    If the batch endpoint is unreachable or only returns a partial result,
    missing codes fall back to the normal single-quote path so callers do not
    see an all-or-nothing empty response.
    """
    if not codes:
        return []

    # Build comma-separated symbols and preserve the original code for fallback.
    requests: list[tuple[str, str]] = []
    for c in codes:
        prefix = _cn_market_prefix(c)
        if prefix:
            raw = c.strip().upper().split(".", maxsplit=1)[0]
            requests.append((raw, f"{prefix}{raw}"))

    if not requests:
        return []

    # Tencent supports up to ~80 codes per request
    results: list[Quote] = []
    result_codes: set[str] = set()
    batch_size = 60
    for i in range(0, len(requests), batch_size):
        chunk = requests[i:i + batch_size]
        chunk_codes = [code for code, _ in chunk]
        chunk_symbols = [symbol for _, symbol in chunk]
        url = f"http://qt.gtimg.cn/q={','.join(chunk_symbols)}"
        try:
            with urlopen(url, timeout=10) as resp:  # nosec B310
                text = resp.read().decode("gbk", errors="ignore")
        except Exception:  # noqa: BLE001 — catch IncompleteRead, URLError, OSError, etc.
            text = ""

        batch_ok = bool(text)  # False = 整批请求失败（超时/被拦截）
        if text:
            for line in text.strip().split(";"):
                line = line.strip()
                if '"' not in line:
                    continue
                payload = line.split('"', maxsplit=2)[1]
                fields = payload.split("~")
                if len(fields) < 33:
                    continue

                name = fields[1]
                code = fields[2]
                last = fields[3]
                prev_close = fields[4]

                try:
                    current = float(last)
                    prev = float(prev_close)
                except ValueError:
                    continue

                change = current - prev
                change_pct = (change / prev * 100) if prev else 0.0

                turnover: float | None = None
                volume: float | None = None
                try:
                    if len(fields) > 37 and fields[37]:
                        turnover = float(fields[37]) * 10_000
                except (ValueError, IndexError):
                    pass
                try:
                    if len(fields) > 36 and fields[36]:
                        volume = float(fields[36])
                except (ValueError, IndexError):
                    pass

                normalized = normalize_ticker(code)
                results.append(Quote(
                    ticker=normalized,
                    price=current,
                    change=change,
                    change_pct=change_pct,
                    name=name,
                    turnover=turnover,
                    volume=volume,
                ))
                result_codes.add(normalized.split(".", maxsplit=1)[0])

        missing_codes = [code for code in chunk_codes if code not in result_codes]
        for code in missing_codes:
            quote: Quote | None = None
            if not batch_ok:
                # 批量腾讯端点已确认失败，直接走东财，避免每支再等 8s Tencent 超时
                quote = _try_cn_quote_eastmoney(code)
            if quote is None:
                try:
                    quote = get_quote(code)
                except Exception:
                    continue
            results.append(quote)
            result_codes.add(code)

    return results


def fetch_sector_stock_list(sector_code: str = "", limit: int = 50) -> list[Quote]:
    """Fetch a list of stocks ranked by turnover from Eastmoney.

    If sector_code is provided, fetches stocks within that sector.
    Otherwise, fetches from the full A-share market.
    Returns up to `limit` Quote objects with turnover data.
    """
    # Eastmoney list API — fs parameter controls market/sector filtering
    if sector_code:
        fs = f"b:{sector_code}"  # BK plate filter; +f:!50 causes empty results for industry plates
    else:
        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # All A-shares

    url = (
        "http://push2.eastmoney.com/api/qt/clist/get?"
        f"pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2&"
        f"fid=f6&fs={fs}&"
        "fields=f2,f3,f4,f6,f8,f12,f14"
    )
    # f2=最新价, f3=涨跌幅%, f4=涨跌额, f6=成交额, f8=换手率%, f12=代码, f14=名称

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except (OSError, URLError, ValueError):
        return []

    diff = data.get("data", {})
    if not diff:
        return []
    items = diff.get("diff", [])
    if not items:
        return []

    results: list[Quote] = []
    for item in items:
        try:
            code = str(item.get("f12", ""))
            name = str(item.get("f14", ""))
            price = item.get("f2")
            change_pct = item.get("f3")
            change = item.get("f4")
            turnover = item.get("f6")

            if price == "-" or price is None:
                continue

            turnover_rate = item.get("f8")
            results.append(Quote(
                ticker=normalize_ticker(code),
                price=float(price),
                change=float(change) if change and change != "-" else 0.0,
                change_pct=float(change_pct) if change_pct and change_pct != "-" else 0.0,
                name=name,
                turnover=float(turnover) if turnover and turnover != "-" else None,
                turnover_rate=float(turnover_rate) if turnover_rate and turnover_rate != "-" else None,
            ))
        except (TypeError, ValueError):
            continue

    return results


def fetch_sector_list(limit: int = 100) -> list[dict[str, str]]:
    """Fetch list of A-share sector/industry plates from Eastmoney.

    Returns list of dicts with 'code' and 'name' keys.
    """
    target = max(1, min(limit, 500))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    page = 1

    while len(results) < target:
        page_size = min(100, target - len(results))
        url = (
            "http://push2.eastmoney.com/api/qt/clist/get?"
            f"pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2&"
            "fid=f3&fs=m:90+t:2&"
            "fields=f12,f14"
        )
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except (OSError, URLError, ValueError):
            break

        diff = data.get("data", {})
        items = diff.get("diff", []) if diff else []
        if not items:
            break

        added = 0
        for item in items:
            code = str(item.get("f12", "")).strip()
            name = str(item.get("f14", "")).strip()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            results.append({"code": code, "name": name})
            added += 1
            if len(results) >= target:
                break

        if added == 0:
            break
        page += 1

    return results


def fetch_hot_sectors(limit: int = 5) -> list[dict]:
    """Fetch top-N hot sector plates ranked by composite heat score.

    Returns list of dicts: {code, name, change_pct, turnover, rise_count, fall_count}
    sorted by composite heat score (涨幅 × 0.5 + 成交额归一 × 0.5) descending.
    """
    fetch_count = max(limit * 4, 40)
    url = (
        "http://push2.eastmoney.com/api/qt/clist/get?"
        f"pn=1&pz={fetch_count}&po=1&np=1&fltt=2&invt=2&"
        "fid=f3&fs=m:90+t:2&"
        "fields=f12,f14,f3,f6,f104,f105"
    )
    # f3=涨跌幅%, f6=成交额, f104=上涨家数, f105=下跌家数
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except (OSError, URLError, ValueError):
        return []

    diff = data.get("data") or {}
    items = diff.get("diff") or []

    sectors: list[dict] = []
    for item in items:
        try:
            code = str(item.get("f12", "")).strip()
            name = str(item.get("f14", "")).strip()
            if not code or not name:
                continue
            cp = item.get("f3")
            to = item.get("f6")
            rise = item.get("f104", 0)
            fall = item.get("f105", 0)
            sectors.append({
                "code": code,
                "name": name,
                "change_pct": float(cp) if cp and cp != "-" else 0.0,
                "turnover": float(to) if to and to != "-" else 0.0,
                "rise_count": int(rise) if rise else 0,
                "fall_count": int(fall) if fall else 0,
            })
        except (TypeError, ValueError):
            continue

    if not sectors:
        return []

    max_to = max(s["turnover"] for s in sectors) or 1.0
    for s in sectors:
        s["_heat"] = s["change_pct"] * 0.5 + (s["turnover"] / max_to) * 100 * 0.5
    sectors.sort(key=lambda x: x["_heat"], reverse=True)

    return [
        {k: v for k, v in s.items() if k != "_heat"}
        for s in sectors[:limit]
    ]


def _fetch_cn_fundamentals(ticker: str) -> dict[str, float | None]:
    """Fetch fundamental indicators for A-shares from Eastmoney.

    Returns: pe_ratio, profit_margin, return_on_equity, debt_to_equity,
             revenue_growth_yoy, free_cash_flow, dividend_yield.
    """
    _empty: dict[str, float | None] = {
        "pe_ratio": None, "profit_margin": None, "return_on_equity": None,
        "debt_to_equity": None, "revenue_growth_yoy": None,
        "free_cash_flow": None, "dividend_yield": None,
    }
    secid = _cn_eastmoney_secid(ticker)
    if secid is None:
        return _empty

    code = ticker.strip().upper().split(".", maxsplit=1)[0]

    # Step 1: Get PE, PB, dividend yield from push2 quote API (already reliable)
    result = dict(_empty)
    try:
        url_q = (
            "http://push2.eastmoney.com/api/qt/stock/get?"
            "ut=fa5fd1943c7b386f172d6893dbfba10b&invt=2&fltt=2&fields="
            "f162,f167,f173,f183,f186,f187,f188&secid=" + secid
        )
        req = Request(url_q, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        d = data.get("data", {})
        if d:
            # f162=PE(TTM), f167=PE(static), f173=ROE(%), f183=profit margin(%),
            # f186=debt/equity ratio(%), f187=revenue growth YoY(%), f188=dividend yield(%)
            pe = d.get("f162")
            if isinstance(pe, (int, float)) and pe != 0 and pe != "-":
                result["pe_ratio"] = float(pe)
            roe = d.get("f173")
            if isinstance(roe, (int, float)) and roe != "-":
                roe_f = float(roe)
                # Sanity: ROE outside [-500%, +500%] is a data-feed error
                if abs(roe_f) <= 500:
                    result["return_on_equity"] = roe_f / 100.0
            pm = d.get("f183")
            if isinstance(pm, (int, float)) and pm != "-":
                pm_f = float(pm)
                # Sanity: profit margin outside [-500%, +500%] is a data-feed error
                if abs(pm_f) <= 500:
                    result["profit_margin"] = pm_f / 100.0
            de = d.get("f186")
            if isinstance(de, (int, float)) and de != "-":
                de_f = float(de)
                if abs(de_f) <= 100_000:
                    result["debt_to_equity"] = de_f / 100.0
            rg = d.get("f187")
            if isinstance(rg, (int, float)) and rg != "-":
                rg_f = float(rg)
                # Sanity: revenue growth outside [-100%, +5000%] is a data-feed error
                if -100 <= rg_f <= 5000:
                    result["revenue_growth_yoy"] = rg_f / 100.0
            dy = d.get("f188")
            if isinstance(dy, (int, float)) and dy != "-":
                dy_f = float(dy)
                if 0 <= dy_f <= 100:
                    result["dividend_yield"] = dy_f / 100.0
    except (OSError, URLError, ValueError, KeyError):
        pass

    # Step 2: Try to get free cash flow from financial report summary
    try:
        market = "SH" if secid.startswith("1.") else "SZ"
        url_fcf = (
            f"https://datacenter.eastmoney.com/securities/api/data/get?"
            f"type=RPT_F10_FINANCE_MAINFINADATA&sty=FREE_CASH_FLOW&"
            f"filter=(SECUCODE=\"{code}.{market}\")&"
            f"p=1&ps=1&sr=-1&st=REPORT_DATE"
        )
        req = Request(url_fcf, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        items = (data.get("result") or {}).get("data") or []
        if items and items[0].get("FREE_CASH_FLOW") is not None:
            result["free_cash_flow"] = float(items[0]["FREE_CASH_FLOW"])
    except (OSError, URLError, ValueError, KeyError):
        pass

    return result


def get_fundamentals(ticker: str) -> dict[str, float | None]:
    """Fetch basic fundamentals with best-effort fallback.

    Returns keys: pe_ratio, profit_margin, return_on_equity,
    and for A-shares also: debt_to_equity, revenue_growth_yoy,
    free_cash_flow, dividend_yield.

    Missing values are returned as None.
    """
    _empty: dict[str, float | None] = {
        "pe_ratio": None, "profit_margin": None, "return_on_equity": None,
        "debt_to_equity": None, "revenue_growth_yoy": None,
        "free_cash_flow": None, "dividend_yield": None,
    }

    if _is_cn_ticker(ticker):
        return _fetch_cn_fundamentals(ticker)

    normalized = normalize_ticker(ticker)
    try:
        info = _yf_info(normalized)
    except Exception:
        return _empty

    pe_ratio = info.get("trailingPE") or info.get("forwardPE")
    profit_margin = info.get("profitMargins")
    return_on_equity = info.get("returnOnEquity")

    return {
        "pe_ratio": float(pe_ratio) if isinstance(pe_ratio, (int, float)) else None,
        "profit_margin": (
            float(profit_margin) if isinstance(profit_margin, (int, float)) else None
        ),
        "return_on_equity": (
            float(return_on_equity) if isinstance(return_on_equity, (int, float)) else None
        ),
        "debt_to_equity": None,
        "revenue_growth_yoy": None,
        "free_cash_flow": None,
        "dividend_yield": None,
    }


# ---------------------------------------------------------------------------
# Company profile — industry classification, region, listing info
# ---------------------------------------------------------------------------

def get_company_profile(ticker: str) -> dict[str, str | int | None]:
    """Fetch company basic profile for an A-share ticker from Eastmoney.

    Returns keys: industry, region, listing_date, employees, company_type.
    Falls back to empty strings on failure.
    """
    result: dict[str, str | int | None] = {
        "industry": "",
        "region": "",
        "listing_date": "",
        "employees": None,
        "company_type": "",
    }
    if not _is_cn_ticker(ticker):
        return result

    secid = _cn_eastmoney_secid(ticker)
    if secid is None:
        return result

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    market_suffix = "SH" if secid.startswith("1.") else "SZ"
    secucode = f"{code}.{market_suffix}"

    # Eastmoney F10 company profile API
    url = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
        "reportName=RPT_F10_INFO_ORGPROFILE&"
        f"columns=SECURITY_CODE,ORG_NAME,BELONG_INDUSTRY,EM2016,REG_PROVINCE,"
        f"LISTING_DATE,EMP_NUM,ORG_TYPE&"
        f"filter=(SECUCODE%3D%22{secucode}%22)"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"})
        with urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        rows = (data.get("result") or {}).get("data") or []
        if rows:
            row = rows[0]
            result["industry"] = (row.get("EM2016") or row.get("BELONG_INDUSTRY") or "").strip()
            result["region"] = (row.get("REG_PROVINCE") or "").strip()
            listing = (row.get("LISTING_DATE") or "").strip()
            result["listing_date"] = listing[:10] if listing else ""
            emp = row.get("EMP_NUM")
            result["employees"] = int(emp) if emp is not None else None
            result["company_type"] = (row.get("ORG_TYPE") or "").strip()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Recent earnings — last N quarterly / annual reports
# ---------------------------------------------------------------------------

def get_recent_earnings(ticker: str, n: int = 4) -> list[dict[str, str | float | None]]:
    """Fetch the most recent N earnings reports for an A-share ticker.

    Returns a list of dicts with keys:
      period (str), revenue (亿元), net_profit (亿元), eps (元),
      revenue_yoy (%), profit_yoy (%), report_type (str).
    """
    empty: list[dict[str, str | float | None]] = []
    if not _is_cn_ticker(ticker):
        return empty

    secid = _cn_eastmoney_secid(ticker)
    if secid is None:
        return empty

    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    market_suffix = "SH" if secid.startswith("1.") else "SZ"
    secucode = f"{code}.{market_suffix}"

    url = (
        "https://datacenter.eastmoney.com/securities/api/data/get?"
        "type=RPT_F10_FINANCE_MAINFINADATA&"
        "sty=REPORT_DATE,TOTAL_OPERATE_INCOME,NET_PROFIT_MOTHER,"
        "BASIC_EPS,YSTZ,SJLTZ,NOTICE_DATE&"
        f"filter=(SECUCODE%3D%22{secucode}%22)&"
        f"p=1&ps={n}&sr=-1&st=REPORT_DATE"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        rows = (data.get("result") or {}).get("data") or []
    except Exception:
        return empty

    records: list[dict[str, str | float | None]] = []
    for row in rows[:n]:
        report_date = (row.get("REPORT_DATE") or "")[:10]
        # Determine report type from date
        if report_date.endswith("-12-31"):
            rtype = "年报"
        elif report_date.endswith("-09-30"):
            rtype = "三季报"
        elif report_date.endswith("-06-30"):
            rtype = "中报"
        elif report_date.endswith("-03-31"):
            rtype = "一季报"
        else:
            rtype = "报告期"

        def _f(v: object) -> float | None:
            try:
                return float(v) if v is not None and v != "-" else None  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        revenue_raw = _f(row.get("TOTAL_OPERATE_INCOME"))
        profit_raw = _f(row.get("NET_PROFIT_MOTHER"))
        records.append({
            "period": report_date,
            "report_type": rtype,
            "revenue": round(revenue_raw / 1e8, 2) if revenue_raw is not None else None,
            "net_profit": round(profit_raw / 1e8, 2) if profit_raw is not None else None,
            "eps": _f(row.get("BASIC_EPS")),
            "revenue_yoy": _f(row.get("YSTZ")),   # 营收同比增长(%)
            "profit_yoy": _f(row.get("SJLTZ")),    # 净利同比增长(%)
        })

    return records


# ---------------------------------------------------------------------------
# Full stock profile — combined data for the StockDetail company info panel
# ---------------------------------------------------------------------------

def get_full_stock_profile(ticker: str) -> dict:
    """Return a combined company profile dict for the StockDetail UI panel.

    Merges: quote (name, pe_ttm, pb_ratio, market_cap), company profile
    (industry, region, listing_date, employees), fundamentals (profit_margin,
    roe, revenue_growth), and recent earnings (last 4 quarters).
    """
    ticker = resolve_ticker(ticker)

    result: dict = {
        "ticker": ticker,
        "name": ticker,
        "main_business": "",
        "industry": "",
        "region": "",
        "listing_date": "",
        "employees": None,
        "pe_ttm": None,
        "pb_ratio": None,
        "market_cap_total": None,
        "profit_margin": None,
        "revenue_growth_yoy": None,
        "return_on_equity": None,
        "dividend_yield": None,
        "recent_earnings": [],
    }

    # 1. Quote — name, pe_ttm, pb_ratio, market_cap_total
    try:
        q = get_quote(ticker)
        result["name"] = q.name or ticker
        result["pe_ttm"] = q.pe_ttm
        result["pb_ratio"] = q.pb_ratio
        result["market_cap_total"] = q.market_cap_total
    except Exception:
        pass

    # 2. Company profile — industry, region, listing_date, employees
    try:
        prof = get_company_profile(ticker)
        result["industry"] = prof.get("industry") or ""
        result["region"] = prof.get("region") or ""
        result["listing_date"] = prof.get("listing_date") or ""
        result["employees"] = prof.get("employees")
    except Exception:
        pass

    # 3. Main business description — Eastmoney F10 ORG basic info
    try:
        secid = _cn_eastmoney_secid(ticker)
        if secid:
            code = ticker.strip().upper().split(".", maxsplit=1)[0]
            market_suffix = "SH" if secid.startswith("1.") else "SZ"
            secucode = f"{code}.{market_suffix}"
            url_biz = (
                "https://datacenter.eastmoney.com/securities/api/data/get?"
                "type=RPT_F10_ORG_ORGPROFILE&"
                "sty=MAIN_BUSINESS&"
                f'filter=(SECUCODE="{secucode}")&p=1&ps=1'
            )
            req = Request(url_biz, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=8) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            rows = (data.get("result") or {}).get("data") or []
            if rows and rows[0].get("MAIN_BUSINESS"):
                mb = str(rows[0]["MAIN_BUSINESS"]).strip()
                result["main_business"] = mb[:300] if mb else ""
    except Exception:
        pass

    # 4. Fundamentals — profit_margin, roe, revenue_growth, dividend_yield
    try:
        fundas = get_fundamentals(ticker)
        result["profit_margin"] = fundas.get("profit_margin")
        result["revenue_growth_yoy"] = fundas.get("revenue_growth_yoy")
        result["return_on_equity"] = fundas.get("return_on_equity")
        result["dividend_yield"] = fundas.get("dividend_yield")
    except Exception:
        pass

    # 5. Recent earnings (last 4 quarters / annual reports)
    try:
        result["recent_earnings"] = get_recent_earnings(ticker, n=4)
    except Exception:
        pass

    return result

