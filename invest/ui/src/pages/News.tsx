/**
 * News page — tabbed market information feed.
 * Three categories: 财经快讯 / 研报动态 / 国际宏观
 * Auto-refreshes every 5 minutes.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bookmark, BookmarkCheck, BrainCircuit, ChevronDown, Copy, ExternalLink, KeyRound, RefreshCw, Rss, Save, Trash2 } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'
import { open as shellOpen } from '@tauri-apps/plugin-shell'
import {
    fetchNews,
    fetchNewsAnalysis,
    fetchSettings,
    fetchSavedArticles,
    saveArticle,
    deleteSavedArticle,
    clearSavedArticles,
    updateSettings,
    type NewsCategory,
} from '@/api/client'
import type { CompanyAnalysis, NewsAnalysis, NewsItem, CompanyAnalysisSectorStock, SavedArticle } from '@/types'

/** Open a URL in the system default browser via Tauri shell plugin.
 * Falls back to clipboard copy + window.open when shell open is unavailable.
 * onCopied is called when clipboard fallback is used.
 */
async function _openUrl(url: string, onCopied?: () => void): Promise<void> {
    if (!url) return
    // 1. Rust command — bypasses plugin-shell permission checks, works on all builds
    try {
        await invoke('open_url', { url })
        return
    } catch { /* fall through */ }
    // 2. Tauri shell plugin (may fail if old binary, keep as second chance)
    try {
        await shellOpen(url)
        return
    } catch { /* fall through */ }
    // 3. Clipboard fallback — user can paste in browser manually
    try {
        await navigator.clipboard.writeText(url)
        onCopied?.()
    } catch {
        window.open(url, '_blank', 'noopener,noreferrer')
    }
}

const TABS: { key: NewsCategory | 'saved'; label: string; color: string }[] = [
    { key: 'cn', label: '财经快讯', color: 'text-blue-400 border-blue-500' },
    { key: 'research', label: '研报动态', color: 'text-violet-400 border-violet-500' },
    { key: 'global', label: '国际宏观', color: 'text-amber-400 border-amber-500' },
    { key: 'saved', label: '📌 收藏夹', color: 'text-emerald-400 border-emerald-500' },
]

const TAG_COLOR: Record<string, string> = {
    '快讯': 'bg-blue-500/15 text-blue-300',
    '财经': 'bg-sky-500/15 text-sky-300',
    '研报': 'bg-violet-500/15 text-violet-300',
    '宏观': 'bg-amber-500/15 text-amber-300',
    '地缘': 'bg-red-500/15 text-red-300',
    '机构观点': 'bg-emerald-500/15 text-emerald-300',
}

// Rating badge — Chinese market convention: 买入/增持 = bullish (red), 减持/卖出 = bearish (green)
const RATING_COLOR: Record<string, string> = {
    '买入':     'bg-red-500/20 text-red-300 border border-red-700/40',
    '强烈推荐': 'bg-red-500/20 text-red-300 border border-red-700/40',
    '推荐':     'bg-red-500/20 text-red-300 border border-red-700/40',
    '看好':     'bg-red-500/20 text-red-300 border border-red-700/40',
    '增持':     'bg-orange-500/20 text-orange-300 border border-orange-700/40',
    '谨慎推荐': 'bg-orange-500/20 text-orange-300 border border-orange-700/40',
    '谨慎增持': 'bg-orange-500/20 text-orange-300 border border-orange-700/40',
    '中性':     'bg-slate-600/30 text-slate-300 border border-slate-600/40',
    '持有':     'bg-slate-600/30 text-slate-300 border border-slate-600/40',
    '中性评级': 'bg-slate-600/30 text-slate-300 border border-slate-600/40',
    '减持':     'bg-green-700/20 text-green-300 border border-green-700/40',
    '看淡':     'bg-green-700/20 text-green-300 border border-green-700/40',
    '卖出':     'bg-emerald-700/20 text-emerald-300 border border-emerald-700/40',
}

const RATING_CHANGE_COLOR: Record<string, string> = {
    '首次': 'bg-violet-500/15 text-violet-300',
    '上调': 'bg-red-500/10 text-red-400',
    '下调': 'bg-green-500/10 text-green-400',
    '维持': 'bg-slate-700/30 text-slate-400',
}

const AUTO_REFRESH_MS = 5 * 60 * 1000

// ---------------------------------------------------------------------------
// Company / sector analysis sub-component
// ---------------------------------------------------------------------------

function CompanyAnalysisBlock({ ca, onOpenUrl }: { ca: CompanyAnalysis; onOpenUrl: (url: string) => void }) {
    const hasFundamentals = ca.fundamentals?.pe_ratio != null || ca.fundamentals?.profit_margin != null
        || ca.fundamentals?.return_on_equity != null || ca.fundamentals?.debt_to_equity != null
        || ca.fundamentals?.revenue_growth_yoy != null || ca.fundamentals?.dividend_yield != null
    const hasSectorStocks = ca.sector_stocks && ca.sector_stocks.length > 0
    const hasProfile = ca.profile && (ca.profile.industry || ca.profile.region || ca.profile.listing_date)
    const hasEarnings = ca.recent_earnings && ca.recent_earnings.length > 0
    const hasRecentNews = ca.recent_news && ca.recent_news.length > 0

    const fmtPct = (v: number | null) => {
        if (v == null) return '--'
        return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
    }
    const pctColor = (v: number | null) => {
        if (v == null) return 'text-slate-400'
        return v > 0 ? 'text-red-400' : v < 0 ? 'text-green-400' : 'text-slate-400'
    }
    const fmtTurnover = (v: number | null) => {
        if (v == null) return '--'
        if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
        if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
        return v.toFixed(0)
    }

    return (
        <div className="border-t border-slate-800 pt-4">
            <div className="flex items-center gap-2 mb-3 flex-wrap">
                <h3 className="text-xs font-semibold tracking-wide text-teal-400">公司 / 行业分析</h3>
                {ca.sector && (
                    <span className="px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-300 text-[11px]">
                        {ca.sector}
                    </span>
                )}
                {ca.maturity && (
                    <span className="px-2 py-0.5 rounded-full bg-slate-700 text-slate-300 text-[11px]">
                        {ca.maturity}
                    </span>
                )}
                {ca.is_focus_sector && (
                    <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 text-[11px]">
                        重点赛道
                    </span>
                )}
            </div>

            {/* Sector summary */}
            {ca.sector_summary && (
                <p className="text-xs leading-5 text-slate-200 mb-3 bg-slate-800/50 rounded-lg px-3 py-2">
                    {ca.sector_summary}
                </p>
            )}

            {/* Company profile — industry / region / listing info */}
            {hasProfile && (
                <div className="flex flex-wrap gap-x-5 gap-y-1.5 mb-3 px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/50">
                    {ca.profile!.industry && (
                        <div className="text-xs flex items-center gap-1.5">
                            <span className="text-slate-500">行业</span>
                            <span className="text-teal-300 font-medium">{ca.profile!.industry}</span>
                        </div>
                    )}
                    {ca.profile!.region && (
                        <div className="text-xs flex items-center gap-1.5">
                            <span className="text-slate-500">地区</span>
                            <span className="text-slate-200">{ca.profile!.region}</span>
                        </div>
                    )}
                    {ca.profile!.listing_date && (
                        <div className="text-xs flex items-center gap-1.5">
                            <span className="text-slate-500">上市日期</span>
                            <span className="text-slate-300">{ca.profile!.listing_date}</span>
                        </div>
                    )}
                    {ca.profile!.employees != null && (
                        <div className="text-xs flex items-center gap-1.5">
                            <span className="text-slate-500">员工数</span>
                            <span className="text-slate-300">{ca.profile!.employees.toLocaleString()}</span>
                        </div>
                    )}
                </div>
            )}

            {/* Fundamentals */}
            {hasFundamentals && (
                <div className="flex flex-wrap gap-x-4 gap-y-1.5 mb-3">
                    {ca.fundamentals.pe_ratio != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">PE  </span>
                            <span className="text-slate-200 font-mono">{ca.fundamentals.pe_ratio.toFixed(1)}</span>
                        </div>
                    )}
                    {ca.fundamentals.profit_margin != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">净利率  </span>
                            <span className="text-slate-200 font-mono">{ca.fundamentals.profit_margin}%</span>
                        </div>
                    )}
                    {ca.fundamentals.return_on_equity != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">ROE  </span>
                            <span className="text-slate-200 font-mono">{ca.fundamentals.return_on_equity}%</span>
                        </div>
                    )}
                    {ca.fundamentals.debt_to_equity != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">负债率  </span>
                            <span className="text-slate-200 font-mono">{ca.fundamentals.debt_to_equity}%</span>
                        </div>
                    )}
                    {ca.fundamentals.revenue_growth_yoy != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">营收增长  </span>
                            <span className={`font-mono ${ca.fundamentals.revenue_growth_yoy >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                                {ca.fundamentals.revenue_growth_yoy >= 0 ? '+' : ''}{ca.fundamentals.revenue_growth_yoy}%
                            </span>
                        </div>
                    )}
                    {ca.fundamentals.free_cash_flow != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">自由现金流  </span>
                            <span className={`font-mono ${ca.fundamentals.free_cash_flow >= 0 ? 'text-slate-200' : 'text-red-400'}`}>
                                {ca.fundamentals.free_cash_flow}亿
                            </span>
                        </div>
                    )}
                    {ca.fundamentals.dividend_yield != null && (
                        <div className="text-xs">
                            <span className="text-slate-500">股息率  </span>
                            <span className="text-slate-200 font-mono">{ca.fundamentals.dividend_yield}%</span>
                        </div>
                    )}
                </div>
            )}

            {/* Sector representative stocks performance */}
            {hasSectorStocks && (
                <div className="mb-3">
                    <p className="text-[11px] text-slate-500 mb-1.5">板块代表股实时行情</p>
                    <div className="rounded-lg border border-slate-800 overflow-hidden">
                        <div className="grid grid-cols-[1fr_60px_72px_68px] gap-1 px-2.5 py-1.5 text-[10px] text-slate-500 font-semibold bg-slate-900/50">
                            <span>股票</span>
                            <span className="text-right">现价</span>
                            <span className="text-right">涨跌幅</span>
                            <span className="text-right">成交额</span>
                        </div>
                        {ca.sector_stocks.map((s: CompanyAnalysisSectorStock) => (
                            <div key={s.ticker}
                                className="grid grid-cols-[1fr_60px_72px_68px] gap-1 px-2.5 py-1.5 border-t border-slate-800/50 hover:bg-slate-800/30">
                                <div className="min-w-0">
                                    <span className="text-xs text-slate-200 font-medium">{s.name}</span>
                                    <span className="text-[10px] text-slate-500 font-mono ml-1.5">
                                        {s.ticker.replace(/\.(SS|SZ)$/, '')}
                                    </span>
                                </div>
                                <span className="text-xs font-mono tabular-nums text-slate-300 text-right">
                                    {s.price?.toFixed(2) ?? '--'}
                                </span>
                                <span className={`text-xs font-mono tabular-nums text-right ${pctColor(s.change_pct)}`}>
                                    {fmtPct(s.change_pct)}
                                </span>
                                <span className="text-[10px] font-mono tabular-nums text-slate-400 text-right">
                                    {fmtTurnover(s.turnover)}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Benchmarks */}
            {ca.benchmarks.length > 0 && (
                <div className="mb-3">
                    <p className="text-[11px] text-slate-500 mb-1.5">关联全球基准</p>
                    <div className="flex flex-wrap gap-2">
                        {ca.benchmarks.map((b) => (
                            <span key={b.ticker} className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono">
                                {b.ticker} <span className="text-slate-500 font-sans">{b.label}</span>
                            </span>
                        ))}
                    </div>
                </div>
            )}

            {/* SWOT */}
            {ca.swot && (
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <p className="text-[11px] font-medium text-emerald-400 mb-1">优势</p>
                        {ca.swot.strengths.map((s, i) => (
                            <p key={i} className="text-[11px] text-slate-300 leading-5">· {s}</p>
                        ))}
                    </div>
                    <div>
                        <p className="text-[11px] font-medium text-rose-400 mb-1">劣势</p>
                        {ca.swot.weaknesses.map((s, i) => (
                            <p key={i} className="text-[11px] text-slate-300 leading-5">· {s}</p>
                        ))}
                    </div>
                    <div>
                        <p className="text-[11px] font-medium text-blue-400 mb-1">机会</p>
                        {ca.swot.opportunities.map((s, i) => (
                            <p key={i} className="text-[11px] text-slate-300 leading-5">· {s}</p>
                        ))}
                    </div>
                    <div>
                        <p className="text-[11px] font-medium text-amber-400 mb-1">威胁</p>
                        {ca.swot.threats.map((s, i) => (
                            <p key={i} className="text-[11px] text-slate-300 leading-5">· {s}</p>
                        ))}
                    </div>
                </div>
            )}

            {/* Recent earnings table */}
            {hasEarnings && (
                <div className="mb-3 mt-3">
                    <p className="text-[11px] text-slate-500 mb-1.5">近期业绩</p>
                    <div className="rounded-lg border border-slate-800 overflow-hidden text-[11px]">
                        <div className="grid grid-cols-[90px_1fr_1fr_50px_70px_70px] gap-1 px-2 py-1.5 text-[10px] text-slate-500 font-semibold bg-slate-900/50">
                            <span>报告期</span>
                            <span className="text-right">营收(亿)</span>
                            <span className="text-right">净利(亿)</span>
                            <span className="text-right">EPS</span>
                            <span className="text-right">营收增速</span>
                            <span className="text-right">净利增速</span>
                        </div>
                        {ca.recent_earnings!.map((e) => {
                            const posColor = (v: number | null) => v == null ? 'text-slate-400' : v >= 0 ? 'text-red-400' : 'text-green-400'
                            const fmtPctVal = (v: number | null) => v == null ? '--' : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
                            const fmtNum = (v: number | null) => v == null ? '--' : v.toFixed(2)
                            return (
                                <div key={e.period}
                                    className="grid grid-cols-[90px_1fr_1fr_50px_70px_70px] gap-1 px-2 py-1.5 border-t border-slate-800/50 tabular-nums font-mono">
                                    <div>
                                        <span className="text-slate-300">{e.period?.slice(0, 7)}</span>
                                        <span className="text-[10px] text-slate-500 ml-1">{e.report_type}</span>
                                    </div>
                                    <span className="text-right text-slate-200">{fmtNum(e.revenue)}</span>
                                    <span className="text-right text-slate-200">{fmtNum(e.net_profit)}</span>
                                    <span className="text-right text-slate-300">{fmtNum(e.eps)}</span>
                                    <span className={`text-right ${posColor(e.revenue_yoy)}`}>{fmtPctVal(e.revenue_yoy)}</span>
                                    <span className={`text-right ${posColor(e.profit_yoy)}`}>{fmtPctVal(e.profit_yoy)}</span>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

            {/* Recent major news (利好/利空/公告) */}
            {hasRecentNews && (
                <div className="mb-1 mt-3">
                    <p className="text-[11px] text-slate-500 mb-1.5">最新公告&新闻</p>
                    <div className="space-y-1">
                        {ca.recent_news!.map((n, i) => {
                            const tagColor = n.tag === '利好' ? 'bg-red-900/60 text-red-300'
                                : n.tag === '利空' ? 'bg-emerald-900/60 text-emerald-300'
                                : 'bg-slate-800/60 text-slate-400'
                            return (
                                <div key={i}
                                    className="flex items-start gap-2 px-2 py-1.5 rounded-lg bg-slate-800/30 hover:bg-slate-800/60 cursor-pointer"
                                    onClick={() => n.url && onOpenUrl(n.url)}>
                                    <span className={`shrink-0 mt-0.5 text-[10px] px-1 rounded ${tagColor}`}>{n.tag}</span>
                                    <span className="text-[11px] text-slate-200 leading-5 flex-1 line-clamp-2">{n.title}</span>
                                    <span className="shrink-0 text-[10px] text-slate-500 whitespace-nowrap">{n.date?.slice(5)}</span>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}
        </div>
    )
}

export default function NewsPage() {
    const [tab, setTab] = useState<NewsCategory | 'saved'>('cn')
    const [items, setItems] = useState<NewsItem[]>([])
    const [loading, setLoading] = useState(false)
    const [analyzing, setAnalyzing] = useState(false)
    const [analysis, setAnalysis] = useState<NewsAnalysis | null>(null)
    const [analysisError, setAnalysisError] = useState('')
    const [focus, setFocus] = useState('')
    const [company, setCompany] = useState('')
    const [sector, setSector] = useState('')
    const [ticker, setTicker] = useState('')
    const [qverisKey, setQverisKey] = useState('')
    const [hasQverisKey, setHasQverisKey] = useState(false)
    const [savingKey, setSavingKey] = useState(false)
    const [keyMessage, setKeyMessage] = useState('')
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
    const [savedArticles, setSavedArticles] = useState<SavedArticle[]>([])
    const [savingId, setSavingId] = useState<number | null>(null)
    const [toast, setToast] = useState('')
    const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
    const [expandedIdx, setExpandedIdx] = useState<Set<number>>(new Set())

    const showToast = useCallback((msg: string) => {
        if (toastTimer.current) clearTimeout(toastTimer.current)
        setToast(msg)
        toastTimer.current = setTimeout(() => setToast(''), 4000)
    }, [])

    const openUrl = useCallback((url: string) => {
        _openUrl(url, () => showToast('链接已复制到剪贴板，请在浏览器中打开'))
    }, [showToast])

    const copyUrl = useCallback((url: string) => {
        navigator.clipboard.writeText(url)
            .then(() => showToast('链接已复制到剪贴板'))
            .catch(() => showToast('复制失败：' + url))
    }, [showToast])
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

    const loadSettings = useCallback(async () => {
        try {
            const settings = await fetchSettings()
            setHasQverisKey(Boolean(settings.has_qveris_key))
        } catch {
            // ignore settings failures on the news page
        }
    }, [])

    const loadSaved = useCallback(async () => {
        try {
            const data = await fetchSavedArticles()
            setSavedArticles(data)
        } catch {
            // silently ignore
        }
    }, [])

    const handleSaveArticle = useCallback(async (item: NewsItem, idx: number) => {
        setSavingId(idx)
        try {
            const updated = await saveArticle({
                title: item.title,
                url: item.url ?? '',
                source: item.source ?? '',
                date: item.date ?? '',
                tag: item.tag ?? '',
                summary: item.summary ?? '',
                category: tab as string,
            })
            setSavedArticles(updated)
        } catch {
            // ignore
        } finally {
            setSavingId(null)
        }
    }, [tab])

    const handleDeleteSaved = useCallback(async (index: number) => {
        try {
            const updated = await deleteSavedArticle(index)
            setSavedArticles(updated)
        } catch {
            // ignore
        }
    }, [])

    const handleClearSaved = useCallback(async () => {
        if (!confirm('确定清空所有收藏？')) return
        try {
            const updated = await clearSavedArticles()
            setSavedArticles(updated)
        } catch {
            // ignore
        }
    }, [])

    const load = useCallback(async (cat: NewsCategory) => {
        setLoading(true)
        try {
            const data = await fetchNews(cat, 20)
            setItems(data)
            setAnalysis(null)
            setAnalysisError('')
            setLastUpdated(new Date())
        } catch {
            // silently ignore — network may be down
        } finally {
            setLoading(false)
        }
    }, [])

    const runAnalysis = useCallback(async () => {
        if (items.length === 0) return
        setAnalyzing(true)
        setAnalysisError('')
        try {
            const result = await fetchNewsAnalysis({
                category: tab === 'saved' ? 'cn' : tab,
                focus: focus.trim(),
                company: company.trim(),
                sector: sector.trim(),
                ticker: ticker.trim(),
                items,
            })
            setAnalysis(result)
            setHasQverisKey(result.key_configured)
        } catch (error) {
            const message = error instanceof Error ? error.message : '资讯分析失败，请稍后重试'
            setAnalysisError(message)
            setAnalysis(null)
        } finally {
            setAnalyzing(false)
        }
    }, [focus, company, sector, ticker, items, tab])

    const saveQverisKey = useCallback(async () => {
        setSavingKey(true)
        setKeyMessage('')
        try {
            await updateSettings({ qveris_key: qverisKey.trim() })
            setQverisKey('')
            setHasQverisKey(Boolean(qverisKey.trim()))
            setKeyMessage(qverisKey.trim() ? 'Qveris.ai Key 已保存' : 'Qveris.ai Key 已清空')
            if (analysis) {
                setAnalysis(null)
            }
        } catch (error) {
            setKeyMessage(error instanceof Error ? error.message : '保存失败')
        } finally {
            setSavingKey(false)
        }
    }, [analysis, qverisKey])

    // Reload when tab changes + auto-refresh
    useEffect(() => {
        if (tab === 'saved') {
            loadSaved()
            return
        }
        load(tab)
        if (timerRef.current) clearInterval(timerRef.current)
        timerRef.current = setInterval(() => load(tab as NewsCategory), AUTO_REFRESH_MS)
        return () => { if (timerRef.current) clearInterval(timerRef.current) }
    }, [tab, load, loadSaved])

    useEffect(() => {
        loadSettings()
        loadSaved()
    }, [loadSettings, loadSaved])


    return (
        <div className="p-5 h-full flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
                <div>
                    <div className="flex items-center gap-2">
                        <Rss className="w-5 h-5 text-blue-400" />
                        <h1 className="text-lg font-semibold text-white">市场资讯</h1>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                        点击资讯会在默认浏览器打开原文，app 内暂不内嵌正文。
                    </p>
                </div>
                <div className="flex items-center gap-3 flex-wrap justify-end">
                    {lastUpdated && (
                        <span className="text-xs text-slate-500">
                            更新于 {lastUpdated.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    )}
                    <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-slate-800 bg-slate-900">
                        <KeyRound className="w-3.5 h-3.5 text-slate-400" />
                        <input
                            type="password"
                            value={qverisKey}
                            onChange={(e) => setQverisKey(e.target.value)}
                            placeholder={hasQverisKey ? '已配置 Qveris.ai Key，可重新覆盖' : '输入 Qveris.ai Key'}
                            className="w-52 bg-transparent text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none"
                        />
                        <button
                            onClick={saveQverisKey}
                            disabled={savingKey}
                            className="flex items-center gap-1 text-xs text-slate-300 hover:text-white disabled:opacity-50"
                        >
                            <Save className="w-3.5 h-3.5" />
                            保存
                        </button>
                    </div>
                    <input
                        value={focus}
                        onChange={(e) => setFocus(e.target.value)}
                        placeholder="关注主题/股票（可选）"
                        className="w-40 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
                    />
                    <input
                        value={company}
                        onChange={(e) => setCompany(e.target.value)}
                        placeholder="所选公司（可选）"
                        className="w-32 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-violet-500"
                    />
                    <input
                        value={sector}
                        onChange={(e) => setSector(e.target.value)}
                        placeholder="所在行业（可选）"
                        className="w-32 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-amber-500"
                    />
                    <input
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value)}
                        placeholder="股票代码（可选）"
                        className="w-32 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-emerald-500"
                    />
                    <button
                        onClick={runAnalysis}
                        disabled={analyzing || items.length === 0}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600/15 border border-blue-500/30 text-blue-300 text-xs hover:bg-blue-600/20 transition-colors disabled:opacity-50"
                    >
                        <BrainCircuit className={`w-3.5 h-3.5 ${analyzing ? 'animate-pulse' : ''}`} />
                        AI 研判
                    </button>
                    <button
                        onClick={() => tab === 'saved' ? loadSaved() : load(tab as NewsCategory)}
                        disabled={loading}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800
                       text-slate-300 text-xs hover:bg-slate-700 transition-colors disabled:opacity-50"
                    >
                        <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                        刷新
                    </button>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 mb-4 border-b border-slate-800">
                {TABS.map(({ key, label, color }) => (
                    <button
                        key={key}
                        onClick={() => setTab(key)}
                        className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors
              ${tab === key
                                ? `${color}`
                                : 'text-slate-500 border-transparent hover:text-slate-300'
                            }`}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {tab !== 'saved' && (keyMessage || hasQverisKey) && (
                <div className="mb-3 text-xs text-slate-500">
                    {keyMessage || '已检测到 Qveris.ai Key，分析时会自动叠加外部上下文。'}
                </div>
            )}

            {tab !== 'saved' && (analysis || analysisError) && (
                <div className="mb-4 rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
                    <div className="flex items-center justify-between gap-3 mb-3">
                        <div className="flex items-center gap-2">
                            <BrainCircuit className="w-4 h-4 text-blue-300" />
                            <h2 className="text-sm font-semibold text-white">Qveris.ai 资讯研判</h2>
                        </div>
                        {analysis && (
                            <div className="flex items-center gap-2 text-[11px] text-slate-400">
                                <span className="px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                                    {analysis.provider}
                                </span>
                                <span className="px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                                    情绪 {analysis.sentiment} · {analysis.score}
                                </span>
                                {analysis.company && (
                                    <span className="px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-300">
                                        {analysis.company}
                                    </span>
                                )}
                                {analysis.sector && (
                                    <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300">
                                        {analysis.sector}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>

                    {analysisError && (
                        <p className="text-sm text-rose-300">{analysisError}</p>
                    )}

                    {analysis && (
                        <div className="space-y-4">
                            <p className="text-sm leading-6 text-slate-200">{analysis.summary}</p>

                            {analysis.themes.length > 0 && (
                                <div className="flex flex-wrap gap-2">
                                    {analysis.themes.map((theme) => (
                                        <span
                                            key={theme}
                                            className="px-2 py-1 rounded-full bg-blue-500/10 text-blue-300 text-[11px]"
                                        >
                                            {theme}
                                        </span>
                                    ))}
                                </div>
                            )}

                            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                                <div>
                                    <h3 className="text-xs font-semibold tracking-wide text-slate-400 mb-2">关键信号</h3>
                                    <div className="space-y-2">
                                        {analysis.key_points.map((point, idx) => (
                                            <p key={idx} className="text-xs leading-5 text-slate-300">{point}</p>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <h3 className="text-xs font-semibold tracking-wide text-emerald-400 mb-2">机会点</h3>
                                    <div className="space-y-2">
                                        {analysis.opportunities.map((point, idx) => (
                                            <p key={idx} className="text-xs leading-5 text-slate-300">{point}</p>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <h3 className="text-xs font-semibold tracking-wide text-amber-400 mb-2">风险点</h3>
                                    <div className="space-y-2">
                                        {analysis.risks.map((point, idx) => (
                                            <p key={idx} className="text-xs leading-5 text-slate-300">{point}</p>
                                        ))}
                                    </div>
                                </div>
                            </div>

                            {analysis.context_items.length > 0 && (
                                <div>
                                    <h3 className="text-xs font-semibold tracking-wide text-slate-400 mb-2">Qveris 外部上下文</h3>
                                    <div className="space-y-2">
                                        {analysis.context_items.map((item, idx) => (
                                            <button
                                                key={`${item.title}-${idx}`}
                                                onClick={() => openUrl(item.url)}
                                                className="w-full text-left p-2 rounded-lg bg-slate-950 border border-slate-800 hover:border-slate-700 transition-colors"
                                            >
                                                <p className="text-xs text-slate-200">{item.title}</p>
                                                <p className="text-[11px] text-slate-500 mt-1">
                                                    {item.source}{item.summary ? ` · ${item.summary}` : ''}
                                                </p>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Company / sector analysis */}
                            {analysis.company_analysis && (
                                <CompanyAnalysisBlock ca={analysis.company_analysis} onOpenUrl={openUrl} />
                            )}

                            {!analysis.key_configured && (
                                <p className="text-[11px] text-slate-500">
                                    当前未检测到 QVERIS_API_KEY，结果以本地资讯归纳为主；保存 Key 后会自动叠加 Qveris.ai 外部检索上下文。
                                </p>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* News list — hidden when on saved tab */}
            {tab !== 'saved' && <div className="flex-1 overflow-y-auto space-y-2">
                {loading && items.length === 0 && (
                    <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
                        加载中...
                    </div>
                )}
                {!loading && items.length === 0 && (
                    <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
                        暂无数据，请稍后刷新
                    </div>
                )}
                {items.map((item, idx) => (
                    // Flex row: content button (flex-1) + icon column (fixed) — no overlap
                    <div key={idx} className="flex items-stretch rounded-xl overflow-hidden
                        border border-slate-800 hover:border-slate-600 transition-all bg-slate-900 group/item">
                        {/* Clickable content area */}
                        <button
                            onClick={() => item.url ? openUrl(item.url) : setExpandedIdx(prev => {
                                const next = new Set(prev); next.has(idx) ? next.delete(idx) : next.add(idx); return next
                            })}
                            className="flex-1 text-left p-3 hover:bg-slate-800/60 transition-colors min-w-0"
                        >
                            <div className="min-w-0">
                                <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                                    {item.tag && (
                                        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded
                      ${TAG_COLOR[item.tag] ?? 'bg-slate-700 text-slate-300'}`}>
                                            {item.tag}
                                        </span>
                                    )}
                                    {item.rating && (
                                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded
                                            ${RATING_COLOR[item.rating] ?? 'bg-slate-700/50 text-slate-300 border border-slate-600/40'}`}>
                                            {item.rating}
                                        </span>
                                    )}
                                    {item.rating_change && (
                                        <span className={`text-[10px] px-1.5 py-0.5 rounded
                                            ${RATING_CHANGE_COLOR[item.rating_change] ?? 'bg-slate-700/30 text-slate-400'}`}>
                                            {item.rating_change}
                                        </span>
                                    )}
                                    {item.source && (
                                        <span className="text-[10px] text-slate-500">{item.source}</span>
                                    )}
                                    {item.sector && (
                                        <span className="text-[10px] text-slate-600 bg-slate-800/60 px-1 rounded">
                                            {item.sector}
                                        </span>
                                    )}
                                    {item.date && (
                                        <span className="text-[10px] text-slate-600">{item.date}</span>
                                    )}
                                </div>
                                <p className="text-sm text-slate-200 leading-relaxed group-hover/item:text-white transition-colors">
                                    {item.title}
                                </p>
                                {item.summary && (
                                    <p className={`text-xs text-slate-400 mt-1.5 leading-5 ${expandedIdx.has(idx) ? '' : 'line-clamp-2'}`}>
                                        {item.summary}
                                    </p>
                                )}
                            </div>
                        </button>
                        {/* Icon column — separate from button, no overlap */}
                        <div className="flex flex-col items-center justify-start gap-0.5 py-2 px-1.5
                            border-l border-slate-800/60 flex-shrink-0">
                            {item.url && (
                                <button
                                    onClick={() => openUrl(item.url ?? '')}
                                    title="在浏览器中打开"
                                    className="p-1 text-slate-600 hover:text-blue-400 transition-colors"
                                >
                                    <ExternalLink className="w-3.5 h-3.5" />
                                </button>
                            )}
                            {item.url && (
                                <button
                                    onClick={() => copyUrl(item.url ?? '')}
                                    title="复制链接"
                                    className="p-1 text-slate-600 hover:text-slate-300 transition-colors"
                                >
                                    <Copy className="w-3.5 h-3.5" />
                                </button>
                            )}
                            {item.summary && item.summary.length > 80 && (
                                <button
                                    onClick={() => setExpandedIdx(prev => {
                                        const next = new Set(prev); next.has(idx) ? next.delete(idx) : next.add(idx); return next
                                    })}
                                    title={expandedIdx.has(idx) ? '收起' : '展开摘要'}
                                    className="p-1 text-slate-600 hover:text-slate-400 transition-colors"
                                >
                                    <ChevronDown className={`w-3.5 h-3.5 transition-transform ${expandedIdx.has(idx) ? 'rotate-180' : ''}`} />
                                </button>
                            )}
                            <button
                                onClick={() => handleSaveArticle(item, idx)}
                                disabled={savingId === idx}
                                title="收藏"
                                className="p-1 text-slate-600 hover:text-emerald-400 transition-colors"
                            >
                                {savedArticles.some(a => a.url && a.url === item.url)
                                    ? <BookmarkCheck className="w-3.5 h-3.5 text-emerald-400" />
                                    : <Bookmark className="w-3.5 h-3.5" />}
                            </button>
                        </div>
                    </div>
                ))}
            </div>}

            {/* Saved articles tab */}
            {tab === 'saved' && (
                <div className="flex-1 overflow-y-auto">
                    <div className="flex items-center justify-between mb-3">
                        <p className="text-xs text-slate-500">共 {savedArticles.length} 篇已收藏</p>
                        {savedArticles.length > 0 && (
                            <button
                                onClick={handleClearSaved}
                                className="flex items-center gap-1 text-xs text-rose-400 hover:text-rose-300"
                            >
                                <Trash2 className="w-3 h-3" />
                                清空全部
                            </button>
                        )}
                    </div>
                    {savedArticles.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-sm gap-2">
                            <Bookmark className="w-8 h-8 opacity-30" />
                            <span>暂无收藏，在资讯列表中点击书签图标即可收藏</span>
                        </div>
                    )}
                    <div className="space-y-2">
                        {savedArticles.map((article, idx) => (
                            <div key={idx} className="flex items-start gap-2 p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition-all group">
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 mb-1">
                                        {article.tag && (
                                            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded
                                                ${TAG_COLOR[article.tag] ?? 'bg-slate-700 text-slate-300'}`}>
                                                {article.tag}
                                            </span>
                                        )}
                                        {article.source && <span className="text-[10px] text-slate-500">{article.source}</span>}
                                        {article.date && <span className="text-[10px] text-slate-600">{article.date}</span>}
                                        <span className="text-[10px] text-slate-700 ml-auto">{article.saved_at?.slice(0, 10)}</span>
                                    </div>
                                    <p className="text-sm text-slate-200 leading-relaxed">{article.title}</p>
                                                    {article.summary && <p className="text-xs text-slate-500 mt-1 line-clamp-2">{article.summary}</p>}
                                </div>
                                <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
                                    {article.url && (
                                        <button onClick={() => openUrl(article.url)} title="在浏览器中打开"
                                            className="p-1 text-slate-600 hover:text-blue-400 transition-colors">
                                            <ExternalLink className="w-3.5 h-3.5" />
                                        </button>
                                    )}
                                    {article.url && (
                                        <button onClick={() => copyUrl(article.url)} title="复制链接"
                                            className="p-1 text-slate-600 hover:text-slate-300 transition-colors">
                                            <Copy className="w-3.5 h-3.5" />
                                        </button>
                                    )}
                                    <button onClick={() => handleDeleteSaved(idx)} title="删除"
                                        className="p-1 text-slate-700 hover:text-rose-400 transition-colors">
                                        <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Toast notification — clipboard fallback feedback */}
            {toast && (
                <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50
                    bg-slate-800 border border-slate-600 rounded-xl px-4 py-2.5
                    text-sm text-slate-200 shadow-2xl flex items-center gap-2 whitespace-nowrap">
                    <Copy className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    {toast}
                </div>
            )}
        </div>
    )
}
