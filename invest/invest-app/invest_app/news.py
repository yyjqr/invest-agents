"""News & research data fetchers.

Provides three data sources displayed on the homepage:
    - cn_news: Domestic A-share market fast news
        东方财富 + 凤凰财经 + 新浪财经 + 华尔街见闻 + 21世纪经济报道 + 雪球入口
    - research: A-share research reports (Eastmoney 研报 + 萝卜投研/慧博入口)
    - global_events: International macro / geopolitical news (Sina + Xinhua + 机构观点)

Official disclosure entries:
    - 巨潮资讯 CNINFO  — 证监会指定法定披露平台（上市公司公告/年报/季报）
    - 三大交易所       — SSE / SZSE / BSE 官方新闻频道

All functions return lists of NewsItem dataclasses.
They fail silently and return empty lists on network errors.
News items are filtered: entertainment / lifestyle content is discarded.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import quote


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
# Real-time institution research fetchers
# ---------------------------------------------------------------------------

def _fetch_gs_insights(n: int = 3) -> list[NewsItem]:
    """Try to fetch recent Goldman Sachs Insights articles via their RSS feed."""
    rss_urls = [
        "https://www.goldmansachs.com/insights/rss/insights.xml",
        "https://www.goldmansachs.com/intelligence/rss/insights.xml",
    ]
    for rss_url in rss_urls:
        raw = _req(rss_url, headers={"Referer": "https://www.goldmansachs.com/"})
        if not raw:
            continue
        try:
            text = raw.decode("utf-8", errors="ignore")
            items: list[NewsItem] = []
            # Minimal XML parsing without external libs: find <item> blocks
            item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for block in item_blocks[:n]:
                title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.DOTALL)
                link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
                date_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                link = (link_m.group(1).strip() if link_m else
                        "https://www.goldmansachs.com/intelligence/")
                pub = date_m.group(1).strip() if date_m else ""
                # Parse RFC2822 date to MM-DD HH:MM
                try:
                    from email.utils import parsedate
                    import time as _time
                    t = parsedate(pub)
                    if t:
                        pub = f"{t[1]:02d}-{t[2]:02d}"
                except Exception:
                    pub = pub[:10]
                items.append(NewsItem(
                    title=title,
                    url=link,
                    source="高盛 Goldman Sachs",
                    date=pub,
                    tag="机构观点",
                    summary="Goldman Sachs Insights",
                ))
            if items:
                return items
        except Exception:
            continue
    return []


def _fetch_imf_blog(n: int = 2) -> list[NewsItem]:
    """Fetch recent IMF Blog posts via their RSS feed."""
    rss_url = "https://www.imf.org/en/Blogs/rss"
    raw = _req(rss_url, headers={"Referer": "https://www.imf.org/"})
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore")
        items: list[NewsItem] = []
        item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        for block in item_blocks[:n]:
            title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.DOTALL)
            link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
            desc_m = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", block, re.DOTALL)
            if not title_m:
                continue
            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
            link = link_m.group(1).strip() if link_m else "https://www.imf.org/en/Blogs"
            pub = date_m.group(1).strip() if date_m else ""
            summary = re.sub(r"<[^>]+>", "", desc_m.group(1) if desc_m else "").strip()[:200]
            try:
                from email.utils import parsedate
                t = parsedate(pub)
                if t:
                    pub = f"{t[1]:02d}-{t[2]:02d}"
            except Exception:
                pub = pub[:10]
            items.append(NewsItem(
                title=title,
                url=link,
                source="IMF 国际货币基金",
                date=pub,
                tag="宏观",
                summary=summary,
            ))
        return items
    except Exception:
        return []


def _fetch_bridgewater_mentions(n: int = 2) -> list[NewsItem]:
    """Search for recent Bridgewater-related Chinese finance summaries on Eastmoney.

    Bridgewater doesn't have a public RSS. Their research is frequently
    summarised on Chinese financial sites, which is the practical source.
    """
    keyword = "%E6%A1%A5%E6%B0%B4"  # URL-encoded "桥水"
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        f"?cb=&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{keyword}%22"
        "%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D"
        "%2C%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22"
        "%2C%22clientVersion%22%3A%22curr%22"
        f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C%22sort%22%3A%22time%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{n}%2C%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )
    raw = _req(url)
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text.startswith("{"):
            text = re.sub(r"^[^(]+\(", "", text).rstrip(");")
        data = json.loads(text)
        rows = (
            data.get("result", {})
            .get("cmsArticleWebOld", {})
            .get("list", [])
        )
        results: list[NewsItem] = []
        for it in rows[:n]:
            title = re.sub(r"<[^>]+>", "", (it.get("title") or "")).strip()
            if not title:
                continue
            results.append(NewsItem(
                title=title,
                url=it.get("url", "https://www.bridgewater.com/research-and-insights"),
                source="桥水基金 Bridgewater",
                date=_ts_to_date(it.get("date", "")),
                tag="机构观点",
                summary=re.sub(r"<[^>]+>", "", (it.get("content") or ""))[:200],
            ))
        return results
    except Exception:
        return []


def _fetch_em_institution_mentions(
    keyword_cn: str,
    source_name: str,
    fallback_url: str,
    n: int = 2,
) -> list[NewsItem]:
    """Generic helper: search Eastmoney for Chinese finance articles mentioning *keyword_cn*.

    Used for institutions without public RSS feeds (Nomura, Blackstone, Berkshire, etc.).
    Falls back to an empty list on any error so callers can use their static link.
    """
    encoded = quote(keyword_cn)
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        f"?cb=&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{encoded}%22"
        "%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D"
        "%2C%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22"
        "%2C%22clientVersion%22%3A%22curr%22"
        f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C%22sort%22%3A%22time%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{n}%2C%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )
    raw = _req(url)
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text.startswith("{"):
            text = re.sub(r"^[^(]+\(", "", text).rstrip(");")
        data = json.loads(text)
        rows = (
            data.get("result", {})
            .get("cmsArticleWebOld", {})
            .get("list", [])
        )
        results: list[NewsItem] = []
        for it in rows[:n]:
            title = re.sub(r"<[^>]+>", "", (it.get("title") or "")).strip()
            if not title:
                continue
            results.append(NewsItem(
                title=title,
                url=it.get("url", fallback_url),
                source=source_name,
                date=_ts_to_date(it.get("date", "")),
                tag="机构观点",
                summary=re.sub(r"<[^>]+>", "", (it.get("content") or ""))[:200],
            ))
        return results
    except Exception:
        return []



def _build_institution_items() -> list[NewsItem]:
    """Return institution research items: live data where available, static links as fallback."""
    items: list[NewsItem] = []

    # 1. Goldman Sachs — try live RSS
    gs_live = _fetch_gs_insights(n=3)
    if gs_live:
        items.extend(gs_live)
    else:
        items.append(NewsItem(
            title="Goldman Sachs Global Macro Research（点击访问）",
            url="https://www.goldmansachs.com/intelligence/",
            source="高盛 Goldman Sachs",
            date="",
            tag="机构观点",
        ))

    # 2. Bridgewater — search Chinese financial sites
    bw_live = _fetch_bridgewater_mentions(n=2)
    if bw_live:
        items.extend(bw_live)
    else:
        items.append(NewsItem(
            title="桥水最新宏观研究（点击访问官网）",
            url="https://www.bridgewater.com/research-and-insights",
            source="桥水基金 Bridgewater",
            date="",
            tag="机构观点",
        ))

    # 3. IMF Blog — try live RSS
    imf_live = _fetch_imf_blog(n=2)
    if imf_live:
        items.extend(imf_live)
    else:
        items.append(NewsItem(
            title="IMF World Economic Outlook（点击访问）",
            url="https://www.imf.org/en/Publications/WEO",
            source="IMF 国际货币基金",
            date="",
            tag="宏观",
        ))

    # 4. Morgan Stanley — static link (no public RSS)
    items.append(NewsItem(
        title="Morgan Stanley Investment Outlook（点击访问）",
        url="https://www.morganstanley.com/ideas/",
        source="摩根斯坦利 Morgan Stanley",
        date="",
        tag="机构观点",
    ))

    # 5. Nomura Securities — live search then static
    nomura_live = _fetch_em_institution_mentions(
        "野村证券", "野村证券 Nomura",
        "https://so.eastmoney.com/news/s?keyword=%E9%87%8E%E6%9D%91%E8%AF%81%E5%88%B8", n=2,
    )
    if nomura_live:
        items.extend(nomura_live)
    else:
        items.append(NewsItem(
            title="野村证券 Nomura — 亚太及中国市场研究（点击访问）",
            url="https://so.eastmoney.com/news/s?keyword=%E9%87%8E%E6%9D%91%E8%AF%81%E5%88%B8",
            source="野村证券 Nomura",
            date="",
            tag="机构观点",
            summary="日本最大券商，亚太宏观策略与A股/港股深度研究",
        ))

    # 6. Blackstone — live search then static
    bst_live = _fetch_em_institution_mentions(
        "黑石集团", "黑石集团 Blackstone",
        "https://www.blackstone.com/insights/", n=2,
    )
    if bst_live:
        items.extend(bst_live)
    else:
        items.append(NewsItem(
            title="黑石集团 Blackstone — 另类资产投资洞察（点击访问）",
            url="https://www.blackstone.com/insights/",
            source="黑石集团 Blackstone",
            date="",
            tag="机构观点",
            summary="全球最大另类资产管理机构，私募股权、房地产与信贷市场观点",
        ))

    # 7. Berkshire Hathaway / Buffett — live search then static
    buf_live = _fetch_em_institution_mentions(
        "巴菲特", "巴菲特/伯克希尔哈撒韦",
        "https://www.berkshirehathaway.com/letters/letters.html", n=2,
    )
    if buf_live:
        items.extend(buf_live)
    else:
        items.append(NewsItem(
            title="巴菲特股东信 — 伯克希尔哈撒韦价值投资哲学（点击访问）",
            url="https://www.berkshirehathaway.com/letters/letters.html",
            source="巴菲特/伯克希尔哈撒韦",
            date="",
            tag="机构观点",
            summary="巴菲特年度股东信，长期价值投资理念与市场判断",
        ))

    # 8. Sequoia Capital — live search then static
    seq_live = _fetch_em_institution_mentions(
        "红杉资本", "红杉资本 Sequoia",
        "https://www.sequoiacap.com/article/", n=1,
    )
    if seq_live:
        items.extend(seq_live)
    else:
        items.append(NewsItem(
            title="红杉资本 Sequoia — 科技创投与市场趋势（点击访问）",
            url="https://www.sequoiacap.com/article/",
            source="红杉资本 Sequoia",
            date="",
            tag="机构观点",
            summary="全球顶级VC，科技行业前沿趋势与投资判断",
        ))

    return items



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
    # Research-report optional fields (empty string when not a 研报)
    rating: str = ""          # 买入 / 增持 / 中性 / 减持 / 卖出
    rating_change: str = ""   # 首次 / 上调 / 下调 / 维持
    stock_code: str = ""      # primary A-share code without suffix, e.g. "600519"
    sector: str = ""          # 申万一级行业, e.g. "食品饮料"
    info_code: str = ""       # Eastmoney infoCode for deep-link / dedup


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
# URL accessibility filter
# ---------------------------------------------------------------------------

# Article-URL path fragments that warrant reachability checks.
# Homepage / channel / livenews URLs are assumed always accessible.
_ARTICLE_PATTERNS = ("/articles/", "/a/", "/article/", "/content/", "/info/", "/detail/")


def _url_ok(url: str, timeout: int = 3) -> bool:
    """Return True if *url* responds with HTTP < 400 within *timeout* seconds.

    Uses HEAD first (fast); falls back to a GET if the server rejects HEAD.
    Returns True for non-HTTP URLs and empty strings so they pass through.
    """
    if not url or not url.startswith("http"):
        return True
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": _UA})  # nosec B310
        with urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.status < 400
    except Exception:
        try:
            req = Request(url, headers={"User-Agent": _UA})  # nosec B310
            with urlopen(req, timeout=timeout) as resp:  # nosec B310
                return resp.status < 400
        except Exception:
            return False


def _filter_accessible(items: list["NewsItem"], timeout: int = 3, max_workers: int = 10) -> list["NewsItem"]:
    """Remove items whose article URL is unreachable.

    Only URLs containing known article-path patterns are checked (e.g.
    ``/articles/``, ``/a/``).  Homepage and channel URLs are skipped to keep
    overhead low.  Checks run in parallel so latency equals the single slowest
    request, capped at *timeout* seconds.
    """
    if not items:
        return items

    to_check = [
        (i, item)
        for i, item in enumerate(items)
        if item.url and any(p in item.url for p in _ARTICLE_PATTERNS)
    ]
    if not to_check:
        return items

    bad: set[int] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_url_ok, item.url, timeout): idx for idx, item in to_check}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                if not fut.result():
                    bad.add(idx)
            except Exception:
                bad.add(idx)

    return [item for i, item in enumerate(items) if i not in bad]


# ---------------------------------------------------------------------------
# Source 1a: Eastmoney 财经要闻 (primary — category-filtered, finance only)
# ---------------------------------------------------------------------------

def _fetch_eastmoney_category(
    mtype: str,
    n: int,
    tag: str = "快讯",
    source_override: str = "",
) -> list[NewsItem]:
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
                source=source_override or it.get("mediaName", "东方财富"),
                date=_ts_to_date(it.get("showTime", it.get("publishDate", ""))),
                tag=tag,
            ))
        return results
    except Exception:
        return []


def _fetch_eastmoney_homepage(n: int, tag: str = "财经") -> list[NewsItem]:
    """Parse Eastmoney finance homepage headlines as a resilient fallback."""
    raw = _req("https://finance.eastmoney.com/")
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", errors="ignore")
        results: list[NewsItem] = []
        seen: set[str] = set()
        for match in re.finditer(
            r'href="(https://finance\.eastmoney\.com/a/[^"#]+\.html)"[^>]*>([^<]{2,120})<',
            text,
        ):
            title = unescape(re.sub(r"\s+", " ", match.group(2))).strip()
            key = re.sub(r"\s+", "", title)[:30]
            if not title or len(title) < 6 or key in seen or not _is_finance(title):
                continue
            seen.add(key)
            results.append(NewsItem(
                title=title,
                url=match.group(1),
                source="东方财富资讯",
                date="",
                tag=tag,
            ))
            if len(results) >= n:
                break
        return results
    except Exception:
        return []


def _fetch_eastmoney_news(n: int) -> list[NewsItem]:
    """Fetch Eastmoney finance headlines, falling back to homepage HTML when APIs are empty."""
    for mtype, tag in (("013001", "财经"), ("011001", "快讯")):
        items = _fetch_eastmoney_category(mtype, n + 5, tag, source_override="东方财富资讯")
        if items:
            return items[:n]
    return _fetch_eastmoney_homepage(n, tag="财经")


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

    # Endpoint 3: 凤凰财经首页 HTML fallback.
    raw3 = _req("https://finance.ifeng.com/", headers={"Referer": "https://finance.ifeng.com/"})
    if raw3:
        try:
            text3 = raw3.decode("utf-8", errors="ignore")
            seen: set[str] = set()
            for match in re.finditer(
                r'href="(https://finance\.ifeng\.com/c/[^"]+)"[^>]*>([^<]{8,120})<',
                text3,
            ):
                title = unescape(re.sub(r"\s+", " ", match.group(2))).strip()
                key = re.sub(r"\s+", "", title)[:30]
                if not title or key in seen or not _is_finance(title):
                    continue
                seen.add(key)
                results.append(NewsItem(
                    title=title,
                    url=match.group(1),
                    source="凤凰财经",
                    date="",
                    tag="快讯",
                ))
                if len(results) >= n:
                    break
            if results:
                return results[:n]
        except Exception:
            pass

    return results[:n]


def _fetch_xueqiu_market(n: int) -> list[NewsItem]:
    """Return stable entry points for Xueqiu market topics.

    Xueqiu's public feeds are heavily front-end rendered and frequently protected
    by WAF checks. Keep direct browser entry points in the news feed so users can
    still jump into 今日话题 / 7x24 from the app.
    """
    items = [
        NewsItem(
            title="雪球今日话题（点击在浏览器查看）",
            url="https://xueqiu.com/today",
            source="雪球",
            date="",
            tag="快讯",
            summary="直达雪球今日话题页面，查看热帖与市场讨论。",
        ),
        NewsItem(
            title="雪球 7x24 市场快讯（点击在浏览器查看）",
            url="https://xueqiu.com/today#/livenews",
            source="雪球",
            date="",
            tag="快讯",
            summary="直达雪球 7x24 页面，查看实时市场快讯。",
        ),
    ]
    return items[: max(0, n)]


# ---------------------------------------------------------------------------
# Source 1d: 华尔街见闻 (Wall Street CN) — articles (资讯) + live flash (快讯)
# ---------------------------------------------------------------------------

_WSCN_HEADERS = {
    "Referer": "https://wallstreetcn.com/",
    "Origin": "https://wallstreetcn.com",
}

_WSCN_ARTICLE_CHANNELS = ("a-stock-channel", "global-channel", "us-stock-channel")
_WSCN_LIVE_CHANNELS    = ("a-stock-channel", "global-channel", "us-stock-channel")
# Confirmed valid URLs (as of 2026-05):
#   资讯: https://wallstreetcn.com/articles/{id}
#   快讯: https://wallstreetcn.com/live          ← /livenews returns 404


def _wscn_fetch_articles(n: int) -> list[NewsItem]:
    """Try the /content/articles API — returns full-text articles with real /articles/{id} URLs."""
    results: list[NewsItem] = []
    seen_urls: set[str] = set()
    for channel in _WSCN_ARTICLE_CHANNELS:
        url = (
            f"https://api-one.wallstcn.com/apiv1/content/articles"
            f"?channel={channel}&cursor=&limit={n + 5}"
        )
        raw = _req(url, headers=_WSCN_HEADERS)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            items_raw = (
                data.get("data", {}).get("items", [])
                or data.get("data", {}).get("list", [])
                or []
            )
            for it in items_raw:
                title = (it.get("title") or "").strip()
                if not title or not _is_finance(title):
                    continue
                # Build article URL — uri field looks like "/articles/3773485"
                uri = it.get("uri") or ""
                if uri.startswith("/articles/"):
                    article_url = f"https://wallstreetcn.com{uri}"
                elif it.get("id"):
                    article_url = f"https://wallstreetcn.com/articles/{it['id']}"
                else:
                    continue
                if article_url in seen_urls:
                    continue
                seen_urls.add(article_url)
                pub = it.get("display_time") or it.get("created_at") or 0
                results.append(NewsItem(
                    title=title,
                    url=article_url,
                    source="华尔街见闻",
                    date=_ts_to_date(pub),
                    tag="资讯",
                ))
            if results:
                return results[:n]
        except Exception:
            continue
    return results


def _wscn_fetch_lives(n: int) -> list[NewsItem]:
    """Try the /content/lives API — short flash ticks, link to the live page."""
    results: list[NewsItem] = []
    for channel in _WSCN_LIVE_CHANNELS:
        url = (
            f"https://api-one.wallstcn.com/apiv1/content/lives"
            f"?channel={channel}&cursor=&limit={n + 3}"
        )
        raw = _req(url, headers=_WSCN_HEADERS)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            items_raw = (
                data.get("data", {}).get("items", [])
                or data.get("data", {}).get("list", [])
                or []
            )
            for it in items_raw:
                title = (it.get("content_text") or it.get("title") or "").strip()
                title = title.split("\n")[0].strip()
                if not title or not _is_finance(title):
                    continue
                pub = it.get("display_time") or it.get("created_at") or 0
                results.append(NewsItem(
                    title=title,
                    url="",   # live flash items have no individual article page
                    source="华尔街见闻",
                    date=_ts_to_date(pub),
                    tag="快讯",
                ))
            if results:
                return results[:n]
        except Exception:
            continue
    return results


def _fetch_wallstreetcn(n: int) -> list[NewsItem]:
    """Fetch news from 华尔街见闻.

    Two passes:
    • 资讯 — full editorial articles (wallstreetcn.com/articles/{id})
    • 快讯 — real-time flash ticks   (wallstreetcn.com/live)

    Results are merged and de-duplicated so the user gets both types and
    every link actually opens a valid page.
    """
    articles = _wscn_fetch_articles(max(n // 2 + 1, 3))
    lives    = _wscn_fetch_lives(max(n // 2 + 1, 3))

    # Merge, deduplicate by normalised title prefix
    seen: set[str] = set()
    merged: list[NewsItem] = []
    for item in articles + lives:
        key = re.sub(r"\s+", "", item.title)[:30]
        if key not in seen:
            seen.add(key)
            merged.append(item)
        if len(merged) >= n:
            break

    # Static fallback so the source always appears in the feed
    if not merged:
        merged.append(NewsItem(
            title="华尔街见闻 — 宏观政策与全球市场实时电报（点击访问）",
            url="https://wallstreetcn.com/live",
            source="华尔街见闻",
            date="",
            tag="快讯",
            summary="宏观政策解读、全球市场联动、实时电报推送",
        ))

    return merged[:n]


# ---------------------------------------------------------------------------
# Source 1e: 21世纪经济报道 — industry & corporate deep reports
# ---------------------------------------------------------------------------

def _fetch_21jingji(n: int) -> list[NewsItem]:
    """Fetch finance news from 21世纪经济报道 via RSS then HTML fallback."""
    results: list[NewsItem] = []

    # Try RSS first
    for rss_url in (
        "https://www.21jingji.com/rss.html",
        "https://m.21jingji.com/article/rss.html",
    ):
        raw = _req(rss_url, headers={"Referer": "https://www.21jingji.com/"})
        if not raw:
            continue
        try:
            text = raw.decode("utf-8", errors="ignore")
            item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for block in item_blocks[:n]:
                title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.DOTALL)
                link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
                date_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
                desc_m = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", block, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                if not title or not _is_finance(title):
                    continue
                link = (link_m.group(1).strip() if link_m else "https://www.21jingji.com/")
                pub = (date_m.group(1).strip() if date_m else "")
                summary = re.sub(r"<[^>]+>", "", desc_m.group(1) if desc_m else "").strip()[:200]
                try:
                    from email.utils import parsedate
                    t = parsedate(pub)
                    if t:
                        pub = f"{t[1]:02d}-{t[2]:02d}"
                except Exception:
                    pub = pub[:10]
                results.append(NewsItem(
                    title=title,
                    url=link,
                    source="21世纪经济报道",
                    date=pub,
                    tag="财经",
                    summary=summary,
                ))
            if results:
                return results[:n]
        except Exception:
            continue

    # HTML fallback — scrape article headlines from homepage
    raw_html = _req("https://www.21jingji.com/", headers={"Referer": "https://www.21jingji.com/"})
    if raw_html:
        try:
            text = raw_html.decode("utf-8", errors="ignore")
            seen: set[str] = set()
            for m in re.finditer(
                r'href="(https://www\.21jingji\.com/article/[^"]+)"[^>]*>([^<]{8,150})<',
                text,
            ):
                title = unescape(re.sub(r"\s+", " ", m.group(2))).strip()
                key = re.sub(r"\s+", "", title)[:30]
                if not title or key in seen or not _is_finance(title):
                    continue
                seen.add(key)
                results.append(NewsItem(
                    title=title,
                    url=m.group(1),
                    source="21世纪经济报道",
                    date="",
                    tag="财经",
                ))
                if len(results) >= n:
                    break
        except Exception:
            pass

    if not results:
        results.append(NewsItem(
            title="21世纪经济报道 — 产业链调研与公司经营深度（点击访问）",
            url="https://www.21jingji.com/",
            source="21世纪经济报道",
            date="",
            tag="财经",
            summary="产业链调研、公司经营内幕分析",
        ))

    return results[:n]


# ---------------------------------------------------------------------------
# Source 1f: 巨潮资讯 CNINFO — 证监会法定披露平台（公告/年报/季报）
# ---------------------------------------------------------------------------

def _fetch_cninfo_announcements(n: int) -> list[NewsItem]:
    """Fetch latest company announcements from 巨潮资讯 (CNINFO).

    CNINFO is the CSRC-designated mandatory disclosure platform.
    Their public query API requires a POST with JSON; falls back to static
    link entries for SSE/SZSE/BSE official news channels.
    """
    results: list[NewsItem] = []

    # Try CNINFO latest announcements public API
    api_url = "http://www.cninfo.com.cn/new/commonController/queryLatestBulletins"
    try:
        import urllib.parse
        body = urllib.parse.urlencode({
            "column": "szse",
            "tabName": "latest",
            "pageSize": str(n),
            "pageNum": "1",
            "seDate": "",
            "category": "",
            "searchKey": "",
            "market": "",
            "fulltext": "false",
        }).encode()
        req = Request(  # nosec B310
            api_url,
            data=body,
            headers={
                "User-Agent": _UA,
                "Referer": "http://www.cninfo.com.cn/new/disclosure/index",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(req, timeout=8) as resp:  # nosec B310
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        rows = (
            data.get("classifiedAnnouncements", [[]])[0]
            if data.get("classifiedAnnouncements")
            else data.get("announcements", [])
        )
        for it in rows[:n]:
            title = (it.get("announcementTitle") or "").strip()
            if not title:
                continue
            ann_id = it.get("announcementId", "")
            code = (it.get("secCode") or "").strip()
            link = (
                f"http://static.cninfo.com.cn/finalpage/{it.get('adjunctUrl', '')}"
                if it.get("adjunctUrl")
                else f"http://www.cninfo.com.cn/new/disclosure/detail?announcementId={ann_id}&orgId={it.get('orgId','')}"
            )
            pub = str(it.get("announcementTime", ""))
            if pub and pub.isdigit() and len(pub) >= 10:
                pub = _ts_to_date(int(pub) // 1000)
            else:
                pub = pub[:10]
            results.append(NewsItem(
                title=title,
                url=link,
                source="巨潮资讯 CNINFO",
                date=pub,
                tag="公告",
                stock_code=code,
                summary=f"股票代码: {code}" if code else "",
            ))
        if results:
            return results
    except Exception:
        pass

    # Static official disclosure entry fallbacks
    fallbacks = [
        NewsItem(
            title="巨潮资讯网 — 证监会指定法定信息披露平台（最新公告）",
            url="http://www.cninfo.com.cn/new/commonController/latestRelease",
            source="巨潮资讯 CNINFO",
            date="",
            tag="公告",
            summary="中国证监会指定的法定信息披露平台，上市公司公告/年报/季报最权威来源",
        ),
        NewsItem(
            title="上海证券交易所 — 最新市场公告与监管动态",
            url="http://www.sse.com.cn/disclosure/announcement/",
            source="上交所 SSE",
            date="",
            tag="公告",
            summary="上海证券交易所官方发布频道",
        ),
        NewsItem(
            title="深圳证券交易所 — 最新上市公司公告",
            url="https://www.szse.cn/disclosure/listed/notice/index.html",
            source="深交所 SZSE",
            date="",
            tag="公告",
            summary="深圳证券交易所官方发布频道",
        ),
        NewsItem(
            title="北京证券交易所 — 最新公告与市场资讯",
            url="https://www.bse.cn/disclosure/announcementlist.html",
            source="北交所 BSE",
            date="",
            tag="公告",
            summary="北京证券交易所官方发布频道",
        ),
    ]
    return fallbacks[:n]


def _merge_news_round_robin(source_lists: list[list[NewsItem]], n: int) -> list[NewsItem]:
    """Interleave source lists so secondary providers are not starved by primary ones."""
    indexes = [0 for _ in source_lists]
    seen: set[str] = set()
    results: list[NewsItem] = []

    while len(results) < n:
        progressed = False
        for idx, items in enumerate(source_lists):
            while indexes[idx] < len(items):
                item = items[indexes[idx]]
                indexes[idx] += 1
                progressed = True
                key = re.sub(r"\s+", "", item.title)[:30]
                if not item.title or key in seen:
                    continue
                seen.add(key)
                results.append(item)
                break
            if len(results) >= n:
                break
        if not progressed:
            break

    return results


# ---------------------------------------------------------------------------
# Source: 21世纪经济报道 — RSS feed
# ---------------------------------------------------------------------------

def _fetch_21jingji(n: int) -> list[NewsItem]:
    """Fetch 21世纪经济报道 via their RSS feed."""
    rss_urls = [
        "https://www.21jingji.com/rss/rss.xml",
        "https://rss.21jingji.com/",
    ]
    for rss_url in rss_urls:
        raw = _req(rss_url, headers={"Referer": "https://www.21jingji.com/"})
        if not raw:
            continue
        try:
            text = raw.decode("utf-8", errors="ignore")
            items: list[NewsItem] = []
            item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for block in item_blocks[:n]:
                title_m = re.search(
                    r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.DOTALL
                )
                link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
                date_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
                desc_m = re.search(
                    r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
                    block, re.DOTALL,
                )
                if not title_m:
                    continue
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                if not title or not _is_finance(title):
                    continue
                link = link_m.group(1).strip() if link_m else "https://www.21jingji.com/"
                pub = date_m.group(1).strip() if date_m else ""
                summary = re.sub(
                    r"<[^>]+>", "", desc_m.group(1) if desc_m else ""
                ).strip()[:200]
                try:
                    from email.utils import parsedate
                    t = parsedate(pub)
                    if t:
                        pub = f"{t[1]:02d}-{t[2]:02d}"
                except Exception:
                    pub = pub[:10]
                items.append(NewsItem(
                    title=title,
                    url=link,
                    source="21世纪经济报道",
                    date=pub,
                    tag="深度",
                    summary=summary,
                ))
            if items:
                return items
        except Exception:
            continue
    return []


# ---------------------------------------------------------------------------
# Source: CNINFO + 三大交易所 官方入口 (静态链接，法律效力最高)
# ---------------------------------------------------------------------------

_OFFICIAL_DISCLOSURE_LINKS: list[NewsItem] = [
    NewsItem(
        title="巨潮资讯 — 上市公司最新公告（点击在浏览器查看）",
        url="https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=information/bulletinSingle",
        source="巨潮资讯 CNINFO",
        date="",
        tag="公告",
        summary="证监会法定信息披露平台，上市公司年报/季报/公告权威来源。",
    ),
    NewsItem(
        title="上海证券交易所 — 最新公告与信息披露",
        url="https://www.sse.com.cn/disclosure/",
        source="上交所 SSE",
        date="",
        tag="公告",
        summary="上交所官方信息披露渠道，含主板/科创板最新公告。",
    ),
    NewsItem(
        title="深圳证券交易所 — 信息披露查询",
        url="https://www.szse.cn/disclosure/",
        source="深交所 SZSE",
        date="",
        tag="公告",
        summary="深交所官方信息披露，含主板/中小板/创业板公告。",
    ),
    NewsItem(
        title="北京证券交易所 — 信息披露中心",
        url="https://www.bse.cn/disclosure/",
        source="北交所 BSE",
        date="",
        tag="公告",
        summary="北交所官方披露渠道，专精特新中小企业上市信息。",
    ),
]


def _fetch_cninfo_announcements(n: int = 5) -> list[NewsItem]:
    """Try to fetch latest announcements from CNINFO fast-news API.

    Falls back to static entry points if the API is unreachable.
    """
    url = (
        "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
        "?url=information/bulletinSingle&pageNum=1&pageSize={n}"
    ).format(n=n)
    raw = _req(url, headers={"Referer": "https://www.cninfo.com.cn/"})
    items: list[NewsItem] = []
    if raw:
        try:
            text = raw.decode("utf-8", errors="ignore")
            data = json.loads(text)
            rows = (
                data.get("classifiedAnnouncements", [])
                or data.get("announcements", [])
                or data.get("data", {}).get("list", [])
                or []
            )
            for it in rows[:n]:
                title = (it.get("announcementTitle") or it.get("title") or "").strip()
                if not title:
                    continue
                items.append(NewsItem(
                    title=title,
                    url=f"https://www.cninfo.com.cn/new/announcement/{it.get('announcementId', '')}",
                    source="巨潮资讯 CNINFO",
                    date=_ts_to_date(it.get("announcementTime") or it.get("publishDate", "")),
                    tag="公告",
                    summary=it.get("announcementType", ""),
                ))
        except Exception:
            pass
    # Always include official entry-point links so users can navigate directly
    items.extend(_OFFICIAL_DISCLOSURE_LINKS)
    return items[:n + len(_OFFICIAL_DISCLOSURE_LINKS)]


# ---------------------------------------------------------------------------
# Source: 萝卜投研 / 慧博研报 入口 (专业研报聚合)
# ---------------------------------------------------------------------------

_RESEARCH_PLATFORM_LINKS: list[NewsItem] = [
    NewsItem(
        title="萝卜投研 — 机构研报与盈利预测（点击访问）",
        url="https://robo.datayes.com/",
        source="萝卜投研",
        date="",
        tag="研报",
        summary="专业机构研报聚合，含盈利预测、目标价、分析师评级汇总。",
    ),
    NewsItem(
        title="慧博研报 — A股研究报告中心（点击访问）",
        url="https://www.huibo.com/",
        source="慧博研报",
        date="",
        tag="研报",
        summary="研报聚合平台，适合抓取机构分析师盈利预测和行业深度报告。",
    ),
    NewsItem(
        title="财新网 — 深度调查与宏观分析（点击访问）",
        url="https://www.caixin.com/",
        source="财新网 Caixin",
        date="",
        tag="深度",
        summary="以深度调查闻名，对宏观经济和大型企业风险揭示具有极高权威性。",
    ),
]


# ---------------------------------------------------------------------------
# Source 1c: Sina finance rolling news — filtered
# ---------------------------------------------------------------------------

def _fetch_sina_finance(n: int) -> list[NewsItem]:
    """Sina Finance 财经滚动 news, filtered to financial content only."""
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

    Mixes multiple finance providers so one upstream source does not starve the
    rest of the feed. Results are deduped by title and capped at n items.

    Sources (in round-robin priority):
      东方财富 · 凤凰财经 · 华尔街见闻 · 21世纪经济报道 · 新浪财经 · 雪球 · 巨潮公告
    """
    if n <= 0:
        return []

    source_lists = [
        _fetch_eastmoney_news(n + 5),
        _fetch_wallstreetcn(max(3, n // 2 + 1)),
        _fetch_phoenix_finance(max(3, n // 2 + 1)),
        _fetch_21jingji(max(2, n // 3)),
        _fetch_sina_finance(max(4, n)),
        _fetch_xueqiu_market(2),
        _fetch_cninfo_announcements(max(2, n // 4)),
    ]
    merged = _merge_news_round_robin(source_lists, n + 10)
    # Remove items whose specific article URL is unreachable (e.g. deleted old articles).
    merged = _filter_accessible(merged)
    return merged[:n]


# ---------------------------------------------------------------------------
# Source 2: Eastmoney 研报 research reports
# ---------------------------------------------------------------------------

# Eastmoney ratingChange integer → Chinese label
_RATING_CHANGE_MAP: dict[str, str] = {
    "0": "首次", "1": "上调", "2": "下调", "3": "维持",
    "首次": "首次", "上调": "上调", "下调": "下调", "维持": "维持",
}

# Rating values we consider valid (reject garbage strings)
_VALID_RATINGS = {"买入", "增持", "中性", "持有", "减持", "卖出", "推荐", "强烈推荐",
                  "谨慎增持", "谨慎推荐", "中性评级", "看好", "看淡"}


def _parse_rating_change(raw: str | int | None) -> str:
    if raw is None:
        return ""
    return _RATING_CHANGE_MAP.get(str(raw).strip(), str(raw).strip()[:4])


def _parse_em_reports(rows: list[dict], limit: int) -> list[NewsItem]:
    """Convert raw Eastmoney report API rows into rich NewsItem objects."""
    from invest_app import research_store  # local import to avoid circular dep

    results: list[NewsItem] = []
    store_rows: list[dict] = []

    for it in rows[:limit]:
        title = (it.get("title") or "").strip()
        if not title:
            continue

        code = (it.get("stockCode") or "").strip()
        info_code = (it.get("infoCode") or "").strip()
        link = (
            f"https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info_code}"
            if info_code
            else (f"https://data.eastmoney.com/report/{code}.html" if code
                  else "https://data.eastmoney.com/report/")
        )

        raw_rating = (it.get("rating") or "").strip()
        rating = raw_rating if raw_rating in _VALID_RATINGS else ""
        rating_change = _parse_rating_change(it.get("ratingChange"))
        researcher = (it.get("researcher") or "").strip()
        stock_name = (it.get("stockName") or "").strip()
        org = (it.get("orgShotName") or it.get("orgName") or "券商").strip()
        sector = (it.get("indvInduName") or "").strip()
        pub_date = str(it.get("publishDate") or "")[:10]

        # Build a human-readable summary
        parts: list[str] = []
        if researcher:
            parts.append(f"分析师: {researcher}")
        if stock_name:
            parts.append(f"涉及: {stock_name}")
        if sector:
            parts.append(f"行业: {sector}")
        summary = " | ".join(parts)

        item = NewsItem(
            title=title,
            url=link,
            source=org,
            date=pub_date,
            tag="研报",
            summary=summary,
            rating=rating,
            rating_change=rating_change,
            stock_code=code,
            sector=sector,
            info_code=info_code,
        )
        results.append(item)

        store_rows.append({
            "id": info_code or (code + title[:20]),
            "title": title,
            "org": org,
            "researcher": researcher,
            "stock_name": stock_name,
            "stock_code": code,
            "sector": sector,
            "rating": rating,
            "rating_change": rating_change,
            "date": pub_date,
            "url": link,
            "summary": summary,
        })

    # Async-safe fire-and-forget ingest into LanceDB store
    try:
        research_store.ingest(store_rows)
    except Exception:
        pass

    return results


def fetch_research_reports(n: int = 8) -> list[NewsItem]:
    """Fetch latest A-share research reports from Eastmoney 研报 with full metadata."""
    url = (
        "http://reportapi.eastmoney.com/report/list"
        "?cb=&industryCode=*&pageSize={n}&industry=*&rating=*&ratingChange=*&pageIndex=1"
        "&fields=title,researcher,orgShotName,orgName,publishDate,stockName,stockCode"
        ",rating,ratingChange,infoCode,indvInduName,attachPages"
    ).format(n=n)
    raw = _req(url, headers={"Referer": "http://data.eastmoney.com/report/"})
    if not raw:
        return _build_research_platform_links()
    try:
        text = raw.decode("utf-8", errors="ignore").strip()
        if text.startswith("{"):
            data = json.loads(text)
        else:
            data = json.loads(text.split("(", 1)[1].rstrip(");"))
        rows = data.get("data", {}).get("list", []) or []
        items = _parse_em_reports(rows, n)
        # Append research platform entry links so they always appear
        items.extend(_build_research_platform_links())
        return items
    except Exception:
        return _build_research_platform_links()


def _build_research_platform_links() -> list[NewsItem]:
    """Return static research platform & institution entry links shown at bottom of 研报 tab."""
    return [
        NewsItem(
            title="萝卜投研 — 机构研报盈利预测聚合平台（点击访问）",
            url="https://robo.fund/",
            source="萝卜投研",
            date="",
            tag="研报",
            summary="专业研报聚合，机构分析师盈利预测与目标价汇总",
        ),
        NewsItem(
            title="慧博研报 — 专业研报数据库（点击访问）",
            url="https://www.hibor.com.cn/",
            source="慧博研报",
            date="",
            tag="研报",
            summary="券商研报、行业报告、宏观策略报告聚合检索",
        ),
        NewsItem(
            title="券商中国 — 证券时报旗下权威资讯（点击访问）",
            url="https://mp.weixin.qq.com/s?__biz=MzAxNjMwOTc0NA==",
            source="券商中国",
            date="",
            tag="研报",
            summary="证券时报旗下，一线券商政策解读与市场动态",
        ),
        # ── 国际机构资本观点 ──────────────────────────────────────────────
        NewsItem(
            title="野村证券 Nomura — 亚太及中国市场研究（点击访问）",
            url="https://so.eastmoney.com/news/s?keyword=%E9%87%8E%E6%9D%91%E8%AF%81%E5%88%B8",
            source="野村证券 Nomura",
            date="",
            tag="机构观点",
            summary="日本最大券商，亚太宏观策略与A股/港股深度研究",
        ),
        NewsItem(
            title="高盛 Goldman Sachs — 全球宏观与行业研究（点击访问）",
            url="https://www.goldmansachs.com/intelligence/",
            source="高盛 Goldman Sachs",
            date="",
            tag="机构观点",
            summary="全球顶级投行，宏观经济预测与行业深度报告",
        ),
        NewsItem(
            title="桥水基金 Bridgewater — 宏观经济与市场研究（点击访问）",
            url="https://www.bridgewater.com/research-and-insights",
            source="桥水基金 Bridgewater",
            date="",
            tag="机构观点",
            summary="全球最大对冲基金，全天候策略与宏观周期判断",
        ),
        NewsItem(
            title="黑石集团 Blackstone — 另类资产投资洞察（点击访问）",
            url="https://www.blackstone.com/insights/",
            source="黑石集团 Blackstone",
            date="",
            tag="机构观点",
            summary="全球最大另类资产管理机构，私募股权、房地产与信贷市场观点",
        ),
        NewsItem(
            title="红杉资本 Sequoia — 科技创投与市场趋势（点击访问）",
            url="https://www.sequoiacap.com/article/",
            source="红杉资本 Sequoia",
            date="",
            tag="机构观点",
            summary="全球顶级VC，科技行业前沿趋势与投资判断",
        ),
        NewsItem(
            title="巴菲特股东信 — 伯克希尔哈撒韦价值投资哲学（点击访问）",
            url="https://www.berkshirehathaway.com/letters/letters.html",
            source="巴菲特/伯克希尔哈撒韦",
            date="",
            tag="机构观点",
            summary="巴菲特年度股东信，长期价值投资理念与市场判断",
        ),
    ]


def fetch_research_by_ticker(ticker: str, n: int = 8) -> list[NewsItem]:
    """Fetch research reports targeting a specific A-share stock from Eastmoney.

    *ticker* may include a suffix (e.g. "600519.SS") or be a bare 6-digit code.
    """
    code = ticker.upper().split(".")[0]
    url = (
        "http://reportapi.eastmoney.com/report/list"
        f"?cb=&stockCode={code}&pageSize={n}&industry=*&rating=*&ratingChange=*&pageIndex=1"
        "&fields=title,researcher,orgShotName,orgName,publishDate,stockName,stockCode"
        ",rating,ratingChange,infoCode,indvInduName,attachPages"
    )
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
        return _parse_em_reports(rows, n)
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

    # Always append institution items (live data or static fallback)
    results.extend(_build_institution_items())

    # Append official Chinese disclosure platform entries
    results.extend([
        NewsItem(
            title="巨潮资讯网 — 最新上市公司公告（证监会法定披露平台）",
            url="http://www.cninfo.com.cn/new/commonController/latestRelease",
            source="巨潮资讯 CNINFO",
            date="",
            tag="公告",
            summary="中国证监会指定的法定信息披露平台，公告/年报/季报最权威来源",
        ),
        NewsItem(
            title="上交所 — 最新市场公告",
            url="http://www.sse.com.cn/disclosure/announcement/",
            source="上交所 SSE",
            date="",
            tag="公告",
        ),
        NewsItem(
            title="深交所 — 最新上市公司公告",
            url="https://www.szse.cn/disclosure/listed/notice/index.html",
            source="深交所 SZSE",
            date="",
            tag="公告",
        ),
    ])

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


# ---------------------------------------------------------------------------
# Stock-specific news — major headlines for a given ticker
# ---------------------------------------------------------------------------

def fetch_stock_news(ticker: str, n: int = 5) -> list[NewsItem]:
    """Fetch recent major news headlines for a specific A-share stock.

    Uses Eastmoney's stock-news API (最新公告 & 关联新闻).
    Returns up to n NewsItem objects.  Fails silently → empty list.
    """
    # Normalise code to 6-digit bare form
    code = ticker.strip().upper().split(".", maxsplit=1)[0]
    # Eastmoney stock news list
    url = (
        "https://np-listapi.eastmoney.com/comm/web/getListInfo"
        f"?cb=&client=web&type=1&mTypeAndCode=0{code},1{code}"
        f"&pageSize={n}&pageIndex=1"
        "&fields=title,showTime,mediaName,art_code,type"
    )
    results: list[NewsItem] = []
    raw = _req(url, headers={"Referer": "https://emweb.securities.eastmoney.com/"})
    if raw:
        try:
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text.startswith("{"):
                text = text.split("(", 1)[1].rstrip(");")
            data = json.loads(text)
            rows = (
                data.get("data", {}).get("list", [])
                or data.get("result", {}).get("data", [])
                or []
            )
            for it in rows[:n]:
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                ac = it.get("art_code", "")
                link = f"https://finance.eastmoney.com/a/{ac}.html" if ac else ""
                source = (it.get("mediaName") or "东方财富").strip()
                date_str = _ts_to_date(it.get("showTime", ""))
                # Determine sentiment tag by keyword scan
                neg_kw = ("下调", "减持", "亏损", "违规", "处罚", "立案", "退市", "质押", "暴跌", "诉讼")
                pos_kw = ("增持", "回购", "超预期", "高增长", "新高", "突破", "订单", "中标", "并购")
                if any(k in title for k in neg_kw):
                    tag = "利空"
                elif any(k in title for k in pos_kw):
                    tag = "利好"
                else:
                    tag = "公告"
                results.append(NewsItem(title=title, url=link, source=source, date=date_str, tag=tag))
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Background news cache (pre-heating)
# ---------------------------------------------------------------------------

_news_cache: dict[str, list[NewsItem]] = {}
_news_cache_lock = threading.Lock()


def get_cached_news(category: str, n: int) -> list[NewsItem] | None:
    """Return up to *n* cached items for *category*, or None if cache is cold."""
    with _news_cache_lock:
        items = _news_cache.get(category)
    if not items:
        return None
    return items[:n]


def _start_news_background(interval_sec: int = 120) -> None:
    """Start a daemon thread that pre-fetches all news categories every *interval_sec* seconds.

    First run happens immediately in the background so the cache is warm before
    the first user request.
    """
    _SOURCES: list[tuple[str, int]] = [
        ("cn", 25),
        ("global", 15),
        ("research", 15),
    ]

    def _loop() -> None:
        _fetch_map = {
            "cn": fetch_cn_news,
            "global": fetch_global_events,
            "research": fetch_research_reports,
        }
        while True:
            for cat, count in _SOURCES:
                try:
                    fresh = _fetch_map[cat](count)
                    with _news_cache_lock:
                        _news_cache[cat] = fresh
                except Exception:
                    pass
            time.sleep(interval_sec)

    t = threading.Thread(target=_loop, daemon=True, name="news-preheat")
    t.start()