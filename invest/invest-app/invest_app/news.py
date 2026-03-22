"""News & research data fetchers.

Provides three data sources displayed on the homepage:
  - cn_news: Domestic A-share market fast news (Eastmoney 财经快讯 + 凤凰财经)
  - research: A-share research reports (Eastmoney 研报)
  - global_events: International macro / geopolitical news (Sina + Xinhua)

All functions return lists of NewsItem dataclasses.
They fail silently and return empty lists on network errors.
News items are filtered: entertainment / lifestyle content is discarded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_INSTITUTION_LINKS: list[dict[str, str]] = [
    {
        "source": "桥水基金 Bridgewater",
        "title": "桥水最新宏观研究（点击访问官网）",
        "url": "https://www.bridgewater.com/research-and-insights",
        "date": "",
        "tag": "机构观点",
    },
    {
        "source": "高盛 Goldman Sachs",
        "title": "Goldman Sachs Global Macro Research（点击访问）",
        "url": "https://www.goldmansachs.com/intelligence/",
        "date": "",
        "tag": "机构观点",
    },
    {
        "source": "摩根斯坦利 Morgan Stanley",
        "title": "Morgan Stanley Investment Outlook（点击访问）",
        "url": "https://www.morganstanley.com/ideas/",
        "date": "",
        "tag": "机构观点",
    },
    {
        "source": "IMF 国际货币基金",
        "title": "IMF World Economic Outlook（点击访问）",
        "url": "https://www.imf.org/en/Publications/WEO",
        "date": "",
        "tag": "宏观",
    },
]

# ---------------------------------------------------------------------------
# Finance keyword filters
# ---------------------------------------------------------------------------

# If ANY of these appear in the title → definitely keep (positive signal)
_FINANCE_WHITELIST: tuple[str, ...] = (
    "股", "市场", "A股", "港股", "美股", "基金", "债券", "利率", "央行", "货币",
    "经济", "GDP", "CPI", "PPI", "通胀", "通缩", "财政", "税", "汇率", "外汇",
    "银行", "证券", "期货", "期权", "指数", "沪指", "深成", "创业板", "科创板",
    "板块", "行业", "营收", "净利", "利润", "营业", "业绩", "财报", "年报",
    "上市", "IPO", "退市", "增发", "回购", "分红", "股息", "派息", "融资",
    "并购", "重组", "减持", "增持", "举牌", "产业链", "供应链", "出口", "进口",
    "贸易", "关税", "制造业", "消费", "地产", "楼市", "房价", "降息", "加息",
    "美联储", "欧央行", "国债", "信用债", "可转债", "大宗商品", "石油", "黄金",
    "半导体", "芯片", "新能源", "光伏", "锂电", "医药", "医疗", "科技",
    "涨", "跌", "估值", "市盈率", "市净率", "市值", "换手", "成交量", "资金",
    "主力", "外资", "北向", "南向", "QFII", "沪深", "资产", "投资", "理财",
    "国企", "央企", "民企", "产业政策", "监管", "证监会", "发改委",
)

# If ANY of these appear → discard (entertainment / lifestyle junk)
_NOISE_BLOCKLIST: tuple[str, ...] = (
    "电影", "电视剧", "综艺", "明星", "演员", "歌手", "网红", "直播", "娱乐",
    "颜值", "恋爱", "婚姻", "离婚", "男友", "女友", "爱情", "美食", "旅游",
    "攻略", "游戏", "手游", "动漫", "小说", "漫画", "排行榜热", "八卦",
    "体育赛", "世界杯", "奥运", "NBA", "足球", "篮球", "羽毛球", "乒乓",
    "减肥", "健身", "医美", "整容", "护肤", "彩妆", "时尚", "穿搭",
    "宠物", "萌", "搞笑", "段子", "网络热词", "表情包",
)


def _is_finance(title: str) -> bool:
    """Return True if a news title is financial/economic content."""
    if not title:
        return False
    for kw in _NOISE_BLOCKLIST:
        if kw in title:
            return False
    for kw in _FINANCE_WHITELIST:
        if kw in title:
            return True
    # Ambiguous: keep if title is short or contains numbers (likely market data)
    return bool(re.search(r"\d", title)) or len(title) < 30


@dataclass
class NewsItem:
    """One news / research item."""

    title: str
    url: str = ""
    source: str = ""
    date: str = ""          # display string, e.g. "03-04 14:32"
    tag: str = ""           # 快讯 / 研报 / 宏观 / 机构观点 / 地缘
    summary: str = ""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _req(url: str, headers: dict | None = None) -> bytes | None:
    """Fetch URL bytes with timeout; return None on any error."""
    h = {"User-Agent": _UA, "Referer": "https://finance.eastmoney.com/"}
    if headers:
        h.update(headers)
    try:
        with urlopen(Request(url, headers=h), timeout=8) as resp:  # nosec B310
            return resp.read()
    except (OSError, URLError):
        return None


def _ts_to_date(ts: int | str | None) -> str:
    """Convert unix timestamp (seconds) or 'YYYY-MM-DD HH:MM:SS' string to 'MM-DD HH:MM'."""
    if not ts:
        return ""
    try:
        ts_int = int(ts)
        return datetime.fromtimestamp(ts_int).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        pass
    try:
        s = str(ts)
        # "20260304161415" → "2026-03-04 16:14:15"
        if len(s) == 14 and s.isdigit():
            return f"{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
        # "2026-03-04 16:14:15"
        dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(ts)[:10]


# ---------------------------------------------------------------------------
# Source 1a: Eastmoney 财经要闻 (primary — category-filtered, finance only)
# ---------------------------------------------------------------------------

def _fetch_eastmoney_category(mtype: str, n: int, tag: str = "快讯") -> list[NewsItem]:
    """Fetch Eastmoney news list by mTypeAndCode category."""
    url = (
        "https://np-listapi.eastmoney.com/comm/web/getListInfo"
        f"?cb=&client=web&type=1&mTypeAndCode={mtype}"
        f"&pageSize={n}&pageIndex=1"
        "&fields=title,showTime,mediaName,newsId,art_code"
    )
    raw = _req(url)
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            data = json.loads(text.split("(", 1)[1].rstrip(");"))
        items_raw = (
            data.get("data", {}).get("list", [])
            or data.get("result", {}).get("data", [])
            or []
        )
        results: list[NewsItem] = []
        for it in items_raw:
            title = it.get("title", "").strip()
            if not title or not _is_finance(title):
                continue
            art_code = it.get("art_code") or it.get("newsId", "")
            link = (
                f"https://finance.eastmoney.com/a/{art_code}.html"
                if art_code
                else "https://finance.eastmoney.com/"
            )
            results.append(NewsItem(
                title=title,
                url=link,
                source=it.get("mediaName", "东方财富"),
                date=_ts_to_date(it.get("showTime", it.get("publishDate", ""))),
                tag=tag,
            ))
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Source 1b: 凤凰财经 Phoenix Finance news
# ---------------------------------------------------------------------------

def _fetch_phoenix_finance(n: int) -> list[NewsItem]:
    """Fetch finance news from 凤凰财经 ifeng.com.

    Tries two known endpoints; fails silently.
    """
    results: list[NewsItem] = []

    # Endpoint 1: ifeng 滚动新闻 (财经频道 cid=551 or 207)
    for cid in ("551", "207"):
        url = (
            f"https://api.ifeng.com/a1/pv/article/3/?cid={cid}"
            f"&callback=&num={n}&from=0&page=1&type=0"
        )
        raw = _req(url, headers={"Referer": "https://finance.ifeng.com/"})
        if raw:
            try:
                text = raw.decode("utf-8", errors="ignore").strip()
                if not text.startswith("{"):
                    text = re.sub(r"^[^(]+\(", "", text).rstrip(");")
                data = json.loads(text)
                rows = (
                    data.get("newslist", [])
                    or data.get("data", {}).get("list", [])
                    or data.get("result", [])
                    or []
                )
                for it in rows[:n]:
                    title = (it.get("title") or it.get("name") or "").strip()
                    if not title or not _is_finance(title):
                        continue
                    pub = it.get("update_time") or it.get("publishTime", "")
                    results.append(NewsItem(
                        title=title,
                        url=it.get("url") or it.get("link", ""),
                        source="凤凰财经",
                        date=_ts_to_date(pub),
                        tag="快讯",
                    ))
                if results:
                    return results
            except Exception:
                continue

    # Endpoint 2: ifeng sub-channel RSS-like
    url2 = "https://finance.ifeng.com/a/listByChannel?channelId=524&from=0&size=10"
    raw2 = _req(url2, headers={"Referer": "https://finance.ifeng.com/"})
    if raw2:
        try:
            data2 = json.loads(raw2.decode("utf-8", errors="ignore"))
            rows2 = data2.get("data", {}).get("list", []) or data2.get("list", []) or []
            for it in rows2[:n]:
                title = (it.get("title") or "").strip()
                if not title or not _is_finance(title):
                    continue
                results.append(NewsItem(
                    title=title,
                    url=it.get("url", ""),
                    source="凤凰财经",
                    date=_ts_to_date(it.get("update_time") or it.get("publishTime", "")),
                    tag="快讯",
                ))
            if results:
                return results
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Source 1c: Sina finance rolling news — filtered
# ---------------------------------------------------------------------------

def _fetch_sina_finance(n: int) -> list[NewsItem]:
    """Sina Finance 财经滚动 news, filtered to financial content only."""
    # lid=2516 → 国内财经; lid=2513 → 财经频道 (mixed); lid=2514 → 财经要闻
    results: list[NewsItem] = []
    for lid in ("2516", "2514", "2513"):
        url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={n * 2}&page=1"
        raw = _req(url)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            rows = data.get("result", {}).get("data", [])
            for it in rows:
                title = it.get("title", "").strip()
                if not title or not _is_finance(title):
                    continue
                results.append(NewsItem(
                    title=title,
                    url=it.get("url", ""),
                    source="新浪财经",
                    date=_ts_to_date(it.get("ctime")),
                    tag="快讯",
                ))
            if len(results) >= n:
                return results[:n]
        except Exception:
            continue
    return results[:n]


# ---------------------------------------------------------------------------
# Public API: fetch_cn_news (merged, deduplicated, finance-filtered)
# ---------------------------------------------------------------------------

def fetch_cn_news(n: int = 10) -> list[NewsItem]:
    """Fetch latest CN market financial news.

    Priority:
      1. Eastmoney 财经要闻 (category 013001)
      2. Eastmoney A股快讯 (category 011001)
      3. 凤凰财经 Phoenix Finance
      4. Sina 财经要闻 (filtered)
    Results are deduped by title and capped at n items.
    """
    seen: set[str] = set()
    results: list[NewsItem] = []

    def _add(items: list[NewsItem]) -> None:
        for it in items:
            key = it.title[:20]
            if key not in seen and it.title:
                seen.add(key)
                results.append(it)

    # Level 1: Eastmoney 财经要闻
    _add(_fetch_eastmoney_category("013001", n + 5, "财经"))
    if len(results) < n:
        # Level 2: Eastmoney A股快讯
        _add(_fetch_eastmoney_category("011001", n + 5, "快讯"))
    if len(results) < n:
        # Level 3: 凤凰财经
        _add(_fetch_phoenix_finance(n))
    if len(results) < n:
        # Level 4: Sina (filtered)
        _add(_fetch_sina_finance(n * 2))

    return results[:n]


# ---------------------------------------------------------------------------
# Source 2: Eastmoney 研报 research reports
# ---------------------------------------------------------------------------

def fetch_research_reports(n: int = 8) -> list[NewsItem]:
    """Fetch latest A-share research reports from Eastmoney 研报."""
    url = (
        "http://reportapi.eastmoney.com/report/list"
        "?cb=&industryCode=*&pageSize={n}&industry=*"
        "&rating=*&ratingChange=*&pageIndex=1"
        "&fields=title,researcher,orgShotName,publishDate,stockName,attachPages"
    ).format(n=n)
    raw = _req(url, headers={"Referer": "http://data.eastmoney.com/report/"})
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            data = json.loads(text.split("(", 1)[1].rstrip(");"))

        rows = data.get("data", {}).get("list", []) or []
        results: list[NewsItem] = []
        for it in rows[:n]:
            code = it.get("stockCode", "")
            link = (
                f"https://data.eastmoney.com/report/{code}.html" if code
                else "https://data.eastmoney.com/report/"
            )
            results.append(NewsItem(
                title=it.get("title", "").strip(),
                url=link,
                source=it.get("orgShotName", "券商"),
                date=str(it.get("publishDate", ""))[:10],
                tag="研报",
                summary=f"研究员: {it.get('researcher','')} | 涉及: {it.get('stockName','')}",
            ))
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Source 3: International macro / geopolitical events
# ---------------------------------------------------------------------------

def fetch_global_events(n: int = 8) -> list[NewsItem]:
    """Fetch major global macro/geopolitical news.

    Tries Sina international finance, falls back to Xinhua world news.
    Appends curated institution links (Bridgewater, Goldman, etc.).
    """
    results: list[NewsItem] = []

    # Try Sina international finance (lid=2516)
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num={n}&page=1"
    raw = _req(url)
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            rows = data.get("result", {}).get("data", [])
            for it in rows[: n - 2]:
                title = it.get("title", "").strip()
                if not title:
                    continue
                results.append(NewsItem(
                    title=title,
                    url=it.get("url", ""),
                    source="新浪国际",
                    date=_ts_to_date(it.get("ctime")),
                    tag=_geo_tag(title),
                ))
        except Exception:
            pass

    # Try Eastmoney 国际要闻
    if len(results) < 4:
        url2 = (
            "https://np-listapi.eastmoney.com/comm/web/getListInfo"
            "?cb=&client=web&type=1&mTypeAndCode=011003"
            f"&pageSize={n}&pageIndex=1"
            "&fields=title,showTime,mediaName,art_code"
        )
        raw2 = _req(url2)
        if raw2:
            try:
                text = raw2.decode("utf-8", errors="ignore").strip()
                if not text.startswith("{"):
                    text = text.split("(", 1)[1].rstrip(");")
                data2 = json.loads(text)
                rows2 = (
                    data2.get("data", {}).get("list", [])
                    or data2.get("result", {}).get("data", [])
                    or []
                )
                for it in rows2[: n - len(results)]:
                    title = it.get("title", "").strip()
                    if not title:
                        continue
                    ac = it.get("art_code", "")
                    link = f"https://finance.eastmoney.com/a/{ac}.html" if ac else ""
                    results.append(NewsItem(
                        title=title,
                        url=link,
                        source=it.get("mediaName", "东方财富"),
                        date=_ts_to_date(it.get("showTime", "")),
                        tag=_geo_tag(title),
                    ))
            except Exception:
                pass

    # Always append institution links
    for link_info in _INSTITUTION_LINKS:
        results.append(NewsItem(
            title=link_info["title"],
            url=link_info["url"],
            source=link_info["source"],
            date=link_info["date"],
            tag=link_info["tag"],
        ))

    return results


def _geo_tag(title: str) -> str:
    """Assign a tag based on news title keywords."""
    kw_map = [
        (["战争", "冲突", "军事", "导弹", "制裁", "俄", "乌克兰", "加沙", "台海"], "地缘"),
        (["美联储", "利率", "CPI", "GDP", "通胀", "加息", "降息", "经济"], "宏观"),
        (["特朗普", "拜登", "习近平", "领导人", "峰会", "G7", "G20"], "政治"),
        (["石油", "原油", "黄金", "大宗商品", "OPEC"], "大宗"),
    ]
    for keywords, tag in kw_map:
        if any(k in title for k in keywords):
            return tag
    return "国际"
