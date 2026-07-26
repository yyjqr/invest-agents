"""Dynamic industry picks based on live Eastmoney sector plates.

This module is intentionally separate from the fixed representative-stock
sector overview. It classifies live Eastmoney plates into an L1/L2/L3
taxonomy, then selects 3-5 liquid candidates dynamically for each industry.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime

from concurrent.futures import ThreadPoolExecutor, as_completed

from invest_app.market_data import Quote, fetch_sector_list, fetch_sector_stock_list

# Module-level cache — survives across requests within the same server process.
_LAST_SUCCESSFUL_RESPONSE: dict | None = None


@dataclass(frozen=True)
class IndustryRule:
    slug: str
    l1: str
    l2: str
    l3: str
    keywords: tuple[str, ...]
    note: str = ""


TAXONOMY_RULES: tuple[IndustryRule, ...] = (
    IndustryRule("semiconductor-materials", "电子", "半导体", "电子材料", ("电子化学品", "半导体", "芯片"), "半导体材料与晶圆制造上游。"),
    IndustryRule("consumer-electronics", "电子", "光学光电子", "消费电子组件", ("消费电子", "彩电", "元件", "清洁小家电"), "终端创新与品牌链条映射。"),
    IndustryRule("telecom-operators", "通信", "通信服务", "运营商服务", ("电信运营商", "通信"), "运营商资本开支与算网基础设施。"),
    IndustryRule("digital-media", "传媒", "数字媒体", "数字广告/内容分发", ("其他数字媒体", "广告营销", "营销代理", "影视院线"), "流量广告与数字内容平台。"),
    IndustryRule("software-services", "计算机", "软件开发", "数字化软件服务", ("互联网服务", "软件开发", "综合电商"), "企业数字化与平台软件方向。"),
    IndustryRule("computer-equipment", "计算机", "计算机设备", "服务器/算力设备", ("计算机设备",), "算力硬件与设备承载。"),
    IndustryRule("battery", "电力设备", "电池", "动力/储能电池", ("蓄电池及其他电池", "电池"), "动力电池与储能电池映射。"),
    IndustryRule("solar-power", "电力设备", "光伏设备", "光伏发电/逆变器", ("光伏发电", "逆变器"), "光伏发电与逆变设备。"),
    IndustryRule("wind-power", "电力设备", "风电设备", "风电运营/设备", ("风力发电",), "风电景气与风机配套。"),
    IndustryRule("power-utilities", "公用事业", "电力", "火电/水电/核电运营", ("火力发电", "水力发电", "核力发电", "电力", "公用事业", "电能综合服务"), "公用事业高股息与电改方向。"),
    IndustryRule("new-energy-vehicle", "汽车", "汽车整车", "新能源乘用车", ("电动乘用车", "商用载货车", "商用载客车", "商用车"), "整车销量与电动化趋势。"),
    IndustryRule("transport-equipment", "机械设备", "高端装备", "轨交/航海装备", ("轨交设备", "航海装备"), "轨交和船舶装备制造。"),
    IndustryRule("banking", "银行", "银行", "城商行/股份行/大行", ("银行", "股份制银行", "城商行", "农商行", "国有大型银行"), "利率与资产质量周期。"),
    IndustryRule("non-bank-finance", "非银金融", "证券保险", "券商/保险", ("金融控股", "保险", "多元金融", "证券"), "资本市场风险偏好代理。"),
    IndustryRule("baijiu", "食品饮料", "白酒", "高端白酒", ("白酒", "非白酒", "其他酒类"), "白酒与品牌消费主线。"),
    IndustryRule("food-processing", "食品饮料", "食品加工", "乳品/零食/调味", ("食品饮料", "食品加工", "乳品", "零食", "调味发酵品", "啤酒", "餐饮"), "大众消费与渠道修复。"),
    IndustryRule("medical-commerce", "医药生物", "医药商业", "流通/零售药店", ("医药商业", "医药流通", "线下药店"), "药品流通与连锁零售。"),
    IndustryRule("medical-beauty", "医药生物", "医疗美容", "医美耗材/服务", ("医美耗材", "医疗美容"), "医美消费与上游耗材。"),
    IndustryRule("chemicals", "基础化工", "化学制品", "化学原料/制品", ("化学制品", "其他化学原料", "纺织化学制品", "煤化工", "氮肥"), "化工景气与周期品弹性。"),
    IndustryRule("oil-gas", "石油石化", "油气开采", "油服/炼化工程", ("油气及炼化工程", "油服工程", "油田服务", "油气开采", "炼化及贸易", "石油石化", "其他石化"), "油气资本开支与炼化链条。"),
    IndustryRule("coal", "煤炭", "煤炭开采", "焦煤/动力煤", ("煤炭开采", "煤炭", "动力煤", "焦煤", "焦炭"), "煤价与供需收缩逻辑。"),
    IndustryRule("metals", "有色金属", "工业金属", "铜铝/金属新材料", ("金属包装", "工业金属", "有色金属"), "工业金属与材料加工。"),
    IndustryRule("logistics", "交通运输", "物流", "快递/仓储/港口", ("快递", "仓储物流", "跨境物流", "港口", "高速公路"), "物流链效率与跨境贸易。"),
    IndustryRule("real-estate", "房地产", "房地产开发", "产业地产/商业运营", ("产业地产", "商业物业经营", "房屋建设", "基础建设"), "地产开发与商业运营。"),
    IndustryRule("home-appliances", "家用电器", "家电", "白电/小家电", ("家用电器", "空调", "小家电", "厨房小家电", "黑色家电", "卫浴电器", "卫浴制品"), "家电更新与消费复苏。"),
    IndustryRule("agriculture", "农林牧渔", "种植/养殖", "粮食/渔业/林业", ("种植业", "粮食种植", "渔业", "林业", "食用菌"), "农产品与种植链条。"),
)


def _normalize_plate_name(name: str) -> str:
    text = re.sub(r"\s+", "", name or "")
    text = re.sub(r"[ⅠⅡⅢIV]+$", "", text)
    return text


def _fmt_turnover(turnover: float | None) -> str:
    if turnover is None:
        return "暂无成交额"
    if turnover >= 1e8:
        return f"{turnover / 1e8:.1f}亿"
    if turnover >= 1e4:
        return f"{turnover / 1e4:.0f}万"
    return f"{turnover:.0f}"


def _classify_plate(plate_name: str) -> IndustryRule | None:
    name = _normalize_plate_name(plate_name)
    best_rule: IndustryRule | None = None
    best_score = -1
    for rule in TAXONOMY_RULES:
        for idx, keyword in enumerate(rule.keywords):
            token = _normalize_plate_name(keyword)
            if not token:
                continue
            if token == name:
                score = 1000 - idx
            elif token in name or name in token:
                score = len(token) * 10 - idx
            else:
                continue
            if score > best_score:
                best_rule = rule
                best_score = score
    return best_rule


def _candidate_score(quote: Quote, rank: int) -> float:
    turnover = quote.turnover or 0.0
    change_pct = quote.change_pct or 0.0
    turnover_score = min(42.0, math.log10(turnover + 1.0) * 4.6) if turnover > 0 else 0.0
    momentum_score = max(-8.0, min(18.0, change_pct * 2.1))
    rank_score = max(0.0, 16.0 - rank * 1.5)
    resilience_bonus = 5.0 if change_pct >= -1.0 else 0.0
    return round(turnover_score + momentum_score + rank_score + resilience_bonus, 2)


def _candidate_reason(quote: Quote, rank: int) -> str:
    parts: list[str] = []
    if rank <= 3:
        parts.append("板块成交额前列")
    if quote.turnover:
        parts.append(f"成交额 {_fmt_turnover(quote.turnover)}")
    if quote.change_pct is not None:
        if quote.change_pct >= 3:
            parts.append(f"当日强势 {quote.change_pct:+.2f}%")
        elif quote.change_pct >= 0:
            parts.append(f"保持强于板块 {quote.change_pct:+.2f}%")
        else:
            parts.append(f"回撤可控 {quote.change_pct:+.2f}%")
    return " · ".join(parts[:3]) or "流动性与热度综合得分靠前"


def _build_industry_entry(
    rule: IndustryRule,
    plate: dict[str, str],
    picks: list[Quote],
    *,
    picks_per_industry: int,
) -> dict:
    scored: list[dict] = []
    for rank, quote in enumerate(picks, start=1):
        scored.append({
            "ticker": quote.ticker,
            "name": quote.name,
            "price": round(quote.price, 2) if quote.price is not None else None,
            "change_pct": round(quote.change_pct, 2) if quote.change_pct is not None else None,
            "turnover": quote.turnover,
            "score": _candidate_score(quote, rank),
            "reason": _candidate_reason(quote, rank),
        })

    selected = sorted(scored, key=lambda item: item["score"], reverse=True)[:picks_per_industry]
    summary = (
        f"按东财板块「{plate.get('name', '')}」实时成交额、涨跌幅与流动性综合筛选，"
        f"当前输出 {len(selected)} 只候选股。"
    )
    return {
        "slug": rule.slug,
        "l1": rule.l1,
        "l2": rule.l2,
        "l3": rule.l3,
        "available": True,
        "status": "ok",
        "plate_code": plate.get("code", ""),
        "plate_name": plate.get("name", ""),
        "note": rule.note,
        "summary": summary,
        "picks": selected,
    }


def _build_unavailable_entry(rule: IndustryRule, status: str) -> dict:
    return {
        "slug": rule.slug,
        "l1": rule.l1,
        "l2": rule.l2,
        "l3": rule.l3,
        "available": False,
        "status": status,
        "plate_code": "",
        "plate_name": "",
        "note": rule.note,
        "summary": "当前未命中对应实时板块，等待下一轮市场数据刷新。",
        "picks": [],
    }


def get_industry_picks(limit: int = 20, picks_per_industry: int = 5) -> dict:
    """Return dynamic industry picks with an L1/L2/L3 taxonomy view."""
    global _LAST_SUCCESSFUL_RESPONSE

    industry_limit = max(1, min(limit, len(TAXONOMY_RULES)))
    per_industry = max(3, min(picks_per_industry, 10))
    plates = fetch_sector_list(limit=220)

    # ── Phase 1: match plates to taxonomy rules (CPU-only, fast) ──────────
    matched: list[tuple[IndustryRule, dict]] = []
    used_rules: set[str] = set()
    for plate in plates:
        rule = _classify_plate(plate.get("name", ""))
        if not rule or rule.slug in used_rules:
            continue
        matched.append((rule, plate))
        used_rules.add(rule.slug)
        if len(matched) >= industry_limit:
            break

    # ── Phase 2: fetch stock lists concurrently (parallel HTTP) ───────────
    # Running all N requests in parallel cuts worst-case time from
    # N × 10 s (sequential) to ≈ 10 s (parallel), keeping the response
    # well within browser connection-timeout limits (~60 s).
    def _fetch_entry(args: tuple[IndustryRule, dict]) -> dict | None:
        rule, plate = args
        try:
            candidates = fetch_sector_stock_list(
                sector_code=plate.get("code", ""), limit=max(20, per_industry * 2 + 5)
            )
        except Exception:
            return None
        if not candidates:
            return None
        return _build_industry_entry(
            rule, plate, candidates, picks_per_industry=per_industry
        )

    items: list[dict] = []
    if matched:
        # Use up to 8 workers; never more than the number of plates to fetch.
        workers = min(8, len(matched))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_entry, args): args for args in matched}
            # Preserve original ordering by rule-slug key
            results: dict[str, dict] = {}
            for future in as_completed(futures, timeout=20):
                entry = future.result()
                if entry:
                    results[entry["slug"]] = entry
        # Re-sort into the original matched order
        for rule, _ in matched:
            if rule.slug in results:
                items.append(results[rule.slug])

    if len(items) < industry_limit:
        items_slugs = {it["slug"] for it in items}
        for rule in TAXONOMY_RULES:
            if rule.slug in items_slugs:
                continue
            status = "fetch_failed" if rule.slug in used_rules else "plate_not_found"
            items.append(_build_unavailable_entry(rule, status))
            if len(items) >= industry_limit:
                break

    matched_count = sum(1 for item in items if item.get("available"))

    if matched_count == 0 and _LAST_SUCCESSFUL_RESPONSE is not None:
        cached = copy.deepcopy(_LAST_SUCCESSFUL_RESPONSE)
        cached["cache_hit"] = True
        return cached

    response = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "matched_count": matched_count,
        "cache_hit": False,
        "taxonomy": {
            "l1": "申万一级行业（宏观配置）",
            "l2": "二级行业（中观景气）",
            "l3": "三级行业（微观选股）",
        },
        "items": items,
    }
    if matched_count > 0:
        _LAST_SUCCESSFUL_RESPONSE = copy.deepcopy(response)
    return response