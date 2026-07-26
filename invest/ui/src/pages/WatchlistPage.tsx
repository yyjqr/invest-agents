/**
 * WatchlistPage — manage watchlist with AI price forecast and price alerts.
 * New features:
 *   - 📈 Forecast: per-stock 1-day price prediction (inline expandable row)
 *   - 🔔 Alert: per-stock high/low threshold → native OS notification (Tauri)
 */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, Bell, ChevronDown, ChevronRight, Plus, RefreshCw, Search, Sparkles, Star, Trash2, TrendingUp, X } from 'lucide-react'
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { useWatchlist } from '@/store/useWatchlist'
import {
  deleteAlert,
  fetchAlerts,
  fetchBatchQuotes,
  fetchForecast,
       fetchSmartPicks,
  PriceWebSocket,
  refreshSmartPicks,
  searchStocks,
  setAlert,
} from '@/api/client'
import type { Forecast, PriceAlert, Quote, SearchResult, SmartPickItem } from '@/types'

// ── helpers ───────────────────────────────────────────────────────────────────
const fmt2 = (n: number) => n.toFixed(2)

function sendOsNotif(title: string, body: string) {
  sendNotification({ title, body })
}

const TREND_COLOR: Record<string, string> = {
  '看多': 'text-bull',
  '偏多': 'text-emerald-400',
  '中性': 'text-slate-300',
  '偏空': 'text-orange-400',
  '看空': 'text-bear',
}

// ── component ─────────────────────────────────────────────────────────────────
export default function WatchlistPage() {
  const { items, monitorTickers, loadWatchlist, loadMonitor, addItem, removeItem, toggleMonitor } =
    useWatchlist()
  const navigate = useNavigate()

  // quotes
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  // search
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searchTimer, setSearchTimer] = useState<ReturnType<typeof setTimeout> | null>(null)
  // forecast
  const [forecasts, setForecasts] = useState<Record<string, Forecast>>({})
  const [loadingFc, setLoadingFc] = useState<Record<string, boolean>>({})
  const [expandedFc, setExpandedFc] = useState<string | null>(null)
  // alerts
  const [alerts, setAlerts] = useState<Record<string, PriceAlert>>({})
  const [alertEditor, setAlertEditor] = useState<string | null>(null)
  const [alertHigh, setAlertHigh] = useState('')
  const [alertLow, setAlertLow] = useState('')
  const [savingAlert, setSavingAlert] = useState(false)
  const [alertSaveError, setAlertSaveError] = useState<string | null>(null)
  // in-app notification bar
  const [notifs, setNotifs] = useState<Array<{ id: string; msg: string; dir: 'high' | 'low' }>>([])
  const firedRef = useRef<Set<string>>(new Set())
  const wsRef = useRef<PriceWebSocket | null>(null)
  // smart picks
  const [smartPicks, setSmartPicks] = useState<SmartPickItem[]>([])
  const [smartUpdatedAt, setSmartUpdatedAt] = useState('')
  const [smartLoading, setSmartLoading] = useState(false)
  const [smartCollapsed, setSmartCollapsed] = useState(false)

  // ── data loading ─────────────────────────────────────────────────────────────
  useEffect(() => {
    loadWatchlist()
    loadMonitor()
    fetchAlerts().then(setAlerts).catch(() => {})
    // Request Tauri notification permission on first load
    isPermissionGranted().then((granted) => {
      if (!granted) requestPermission().catch(() => {})
    }).catch(() => {})
    // Load smart picks (cached, non-blocking)
    fetchSmartPicks()
      .then((r) => { setSmartPicks(r.picks); setSmartUpdatedAt(r.updated_at) })
      .catch(() => {})
  }, [])

  const refreshQuotes = useCallback(async () => {
    if (!items.length) return
    const qs = await fetchBatchQuotes(items.map((i) => i.ticker)).catch(() => [] as Quote[])
    const map: Record<string, Quote> = {}
    qs.forEach((q) => {
      map[q.ticker] = q
      // Also index by 6-digit base code (e.g. "600036" for "600036.SS")
      // so watchlist items stored without suffix are found correctly
      const base = q.ticker.split('.')[0]
      if (base !== q.ticker) map[base] = q
    })
    setQuotes(map)
  }, [items])

  useEffect(() => { refreshQuotes() }, [items, refreshQuotes])

  // WebSocket price stream (5 s push from server)
  // Mount: close WS on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])
  // Update subscription whenever watchlist changes
  useEffect(() => {
    const tickers = items.map((i) => i.ticker)
    if (wsRef.current) {
      wsRef.current.updateTickers(tickers)
    } else if (tickers.length > 0) {
      wsRef.current = new PriceWebSocket(tickers, (newQuotes) => {
        setQuotes((prev) => {
          const map = { ...prev }
          newQuotes.forEach((q) => {
            map[q.ticker] = q
            const base = q.ticker.split('.')[0]
            if (base !== q.ticker) map[base] = q
          })
          return map
        })
      })
    }
  }, [items])

  // check alerts whenever quotes update
  useEffect(() => {
    if (!Object.keys(quotes).length || !Object.keys(alerts).length) return
    const newNotifs: Array<{ id: string; msg: string; dir: 'high' | 'low' }> = []
    for (const [ticker, rule] of Object.entries(alerts)) {
      const q = quotes[ticker]
      if (!q) continue
      const idHigh = `${ticker}_h`
      const idLow  = `${ticker}_l`
      if (rule.alert_high != null && q.price >= rule.alert_high) {
        if (!firedRef.current.has(idHigh)) {
          firedRef.current.add(idHigh)
          const msg = `${q.name}(${ticker}) 现价 ${fmt2(q.price)} ≥ 上限 ${fmt2(rule.alert_high)}`
          sendOsNotif('📈 价格预警·触及上限', msg)
          newNotifs.push({ id: idHigh, msg, dir: 'high' })
        }
      } else {
        firedRef.current.delete(idHigh)
      }
      if (rule.alert_low != null && q.price <= rule.alert_low) {
        if (!firedRef.current.has(idLow)) {
          firedRef.current.add(idLow)
          const msg = `${q.name}(${ticker}) 现价 ${fmt2(q.price)} ≤ 下限 ${fmt2(rule.alert_low)}`
          sendOsNotif('📉 价格预警·触及下限', msg)
          newNotifs.push({ id: idLow, msg, dir: 'low' })
        }
      } else {
        firedRef.current.delete(idLow)
      }
    }
    if (newNotifs.length) setNotifs((p) => [...p, ...newNotifs].slice(-6))
  }, [quotes, alerts])

  // search debounce
  useEffect(() => {
    if (!searchQ.trim()) { setSearchResults([]); return }
    if (searchTimer) clearTimeout(searchTimer)
    const t = setTimeout(async () => {
      setSearching(true)
      const res = await searchStocks(searchQ).catch(() => [] as SearchResult[])
      setSearchResults(res.slice(0, 8))
      setSearching(false)
    }, 400)
    setSearchTimer(t)
  }, [searchQ])

  // ── actions ───────────────────────────────────────────────────────────────────
  const handleAdd = async (r: SearchResult) => {
    await addItem(r.code || r.symbol, r.name)
    setSearchQ(''); setSearchResults([])
  }

  const handleAddSmartPick = async (pick: SmartPickItem) => {
    await addItem(pick.ticker.split('.')[0], pick.name)
  }

  const handleRefreshSmartPicks = async () => {
    setSmartLoading(true)
    try {
      const r = await refreshSmartPicks()
      setSmartPicks(r.picks)
      setSmartUpdatedAt(r.updated_at)
    } catch { /* ignore */ }
    finally { setSmartLoading(false) }
  }

  const toggleForecast = async (ticker: string) => {
    if (expandedFc === ticker) { setExpandedFc(null); return }
    setAlertEditor(null)
    setLoadingFc((p) => ({ ...p, [ticker]: true }))
    try {
      const fc = await fetchForecast(ticker)
      setForecasts((p) => ({ ...p, [ticker]: fc }))
      setExpandedFc(ticker)
    } catch { /* silently ignore */ }
    finally { setLoadingFc((p) => ({ ...p, [ticker]: false })) }
  }

  const openAlertEditor = (ticker: string) => {
    if (alertEditor === ticker) { setAlertEditor(null); return }
    setExpandedFc(null)
    const a = alerts[ticker]
    setAlertHigh(a?.alert_high?.toString() ?? '')
    setAlertLow(a?.alert_low?.toString() ?? '')
    setAlertSaveError(null)
    setAlertEditor(ticker)
  }

  const parseAlertVal = (s: string): number | null => {
    const v = parseFloat(s)
    return isNaN(v) ? null : v
  }

  const saveAlert = async (ticker: string, currentPrice?: number) => {
    setAlertSaveError(null)
    const high = parseAlertVal(alertHigh)
    const low  = parseAlertVal(alertLow)

    // ── 前端校验 ────────────────────────────────────────────────────────────
    if (high !== null && low !== null && high <= low) {
      setAlertSaveError('上限价必须大于下限价')
      return
    }
    if (currentPrice != null && currentPrice > 0) {
      const maxHigh = currentPrice * 3
      const minLow  = Math.max(1, currentPrice / 3)
      if (high !== null && high > maxHigh) {
        setAlertSaveError(`上限价不能超过现价 3 倍（≤ ${fmt2(maxHigh)}）`)
        return
      }
      if (low !== null && low < minLow) {
        setAlertSaveError(`下限价不能低于现价 1/3 或 ¥1（≥ ${fmt2(minLow)}）`)
        return
      }
    }

    setSavingAlert(true)
    try {
      const updated = await setAlert(ticker, { alert_high: high, alert_low: low })
      setAlerts(updated)
      setAlertEditor(null)
    } catch (e: unknown) {
      setAlertSaveError(e instanceof Error ? e.message : '保存失败，请重试')
    } finally {
      setSavingAlert(false)
    }
  }

  const clearAlert = async (ticker: string) => {
    const updated = await deleteAlert(ticker).catch(() => alerts)
    setAlerts(updated)
    setAlertEditor(null)
  }

  // ── render ────────────────────────────────────────────────────────────────────
  const up = (v: number) => v > 0
  const dn = (v: number) => v < 0

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-800">
        <h1 className="text-lg font-bold text-white">自选股</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          {items.length} 只股票 · 🔔 价格预警 &amp; 📈 AI 预测
        </p>
      </div>

      {/* Smart Picks panel */}
      <div className="border-b border-slate-800">
        {/* Smart picks header row */}
        <div
          className="flex items-center justify-between px-6 py-2.5 cursor-pointer
                     hover:bg-slate-800/30 select-none"
          onClick={() => setSmartCollapsed((v) => !v)}
        >
          <div className="flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-yellow-400" />
            <span className="text-xs font-semibold text-yellow-300">智能精选</span>
            {smartPicks.length > 0 && (
              <span className="text-[10px] text-slate-500">
                {smartPicks.length} 只 ·&nbsp;
                {smartUpdatedAt
                  ? `更新于 ${new Date(smartUpdatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
                  : '未更新'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              title="手动刷新智能精选"
              onClick={(e) => { e.stopPropagation(); handleRefreshSmartPicks() }}
              disabled={smartLoading}
              className="p-1 rounded-lg text-slate-500 hover:text-yellow-400
                         hover:bg-slate-700 transition-colors disabled:opacity-40"
            >
              <RefreshCw className={`w-3 h-3 ${smartLoading ? 'animate-spin' : ''}`} />
            </button>
            {smartCollapsed
              ? <ChevronRight className="w-3.5 h-3.5 text-slate-500" />
              : <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
            }
          </div>
        </div>

        {/* Smart picks list */}
        {!smartCollapsed && (
          <div className="px-6 pb-3">
            {smartLoading && smartPicks.length === 0 ? (
              <p className="text-xs text-slate-500 py-2">正在拉取行情数据...</p>
            ) : smartPicks.length === 0 ? (
              <p className="text-xs text-slate-500 py-2">暂无精选，点击刷新按钮获取</p>
            ) : (
              <div className="grid grid-cols-1 gap-1">
                {smartPicks.map((pick) => {
                  const alreadyIn = items.some(
                    (i) => i.ticker === pick.ticker || i.ticker.split('.')[0] === pick.ticker.split('.')[0]
                  )
                  const up = pick.change_pct > 0
                  const dn = pick.change_pct < 0
                  return (
                    <div
                      key={pick.ticker}
                      className="flex items-center justify-between py-1.5 px-2 rounded-lg
                                 hover:bg-slate-800/50 group transition-colors"
                    >
                      <div
                        className="flex items-center gap-3 flex-1 cursor-pointer min-w-0"
                        onClick={() => navigate(`/stock/${pick.ticker}`)}
                      >
                        <div className="min-w-0">
                          <p className="text-xs font-medium text-white truncate">{pick.name}</p>
                          <p className="text-[10px] text-slate-500">
                            {pick.ticker.split('.')[0]} · {pick.sector}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 flex-shrink-0">
                        <span className={`text-xs font-mono font-medium
                          ${up ? 'text-bull' : dn ? 'text-bear' : 'text-slate-400'}`}>
                          {pick.change_pct >= 0 ? '+' : ''}{pick.change_pct.toFixed(2)}%
                        </span>
                        {pick.turnover_rate != null && (
                          <span className="text-[10px] text-slate-500 hidden sm:inline">
                            换手 {pick.turnover_rate.toFixed(1)}%
                          </span>
                        )}
                        <span className="text-[10px] text-slate-600 hidden md:inline">
                          评分 {pick.score.toFixed(0)}
                        </span>
                        <button
                          title={alreadyIn ? '已在自选' : '加入自选'}
                          onClick={() => !alreadyIn && handleAddSmartPick(pick)}
                          disabled={alreadyIn}
                          className={`p-1 rounded-lg transition-colors
                            ${alreadyIn
                              ? 'text-yellow-500 cursor-default'
                              : 'text-slate-500 hover:text-yellow-400 hover:bg-slate-700'}`}
                        >
                          <Plus className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* In-app alert notifications */}
      {notifs.length > 0 && (
        <div className="px-6 py-2 space-y-1.5 bg-slate-950/70 border-b border-slate-800">
          {notifs.map((n) => (
            <div
              key={n.id}
              className={`flex items-center justify-between text-xs px-3 py-1.5 rounded-lg
                ${n.dir === 'high'
                  ? 'bg-bull/10 text-bull border border-bull/20'
                  : 'bg-bear/10 text-bear border border-bear/20'}`}
            >
              <span>{n.msg}</span>
              <button onClick={() => setNotifs((p) => p.filter((x) => x.id !== n.id))}>
                <X className="w-3 h-3 ml-2 flex-shrink-0" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Search bar */}
      <div className="px-6 py-3 border-b border-slate-800">
        <div className="max-w-sm relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="搜索添加股票..."
            className="w-full pl-9 pr-8 py-2 bg-slate-800 border border-slate-700 rounded-xl
                       text-white text-sm placeholder-slate-500 outline-none focus:border-blue-500"
          />
          {searchQ && (
            <button
              onClick={() => { setSearchQ(''); setSearchResults([]) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        {(searching || searchResults.length > 0) && (
          <div className="max-w-sm mt-2 bg-slate-800 border border-slate-700 rounded-xl overflow-hidden z-10 relative">
            {searching && <p className="px-3 py-2 text-xs text-slate-400">搜索中...</p>}
            {searchResults.map((r) => (
              <button
                key={r.symbol}
                onClick={() => handleAdd(r)}
                className="w-full flex items-center justify-between px-3 py-2.5 text-sm
                           hover:bg-slate-700 border-b border-slate-700/50 last:border-0"
              >
                <span className="text-white">{r.name}</span>
                <span className="text-xs text-slate-400">
                  {r.code} <Plus className="w-3 h-3 inline ml-1" />
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-slate-500">
            <Star className="w-10 h-10 mb-3 text-slate-700" />
            <p className="font-medium text-slate-400">自选股为空</p>
            <p className="text-sm mt-1">搜索添加你关注的股票</p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="sticky top-0 bg-slate-900/90 backdrop-blur">
              <tr className="text-xs text-slate-500 border-b border-slate-800">
                <th className="text-left px-6 py-3 font-medium">股票</th>
                <th className="text-right px-4 py-3 font-medium">最新价</th>
                <th className="text-right px-4 py-3 font-medium">涨跌幅</th>
                <th className="text-right px-4 py-3 font-medium">市值</th>
                <th className="text-right px-4 py-3 font-medium">PE(TTM)</th>
                <th className="text-center px-3 py-3 font-medium w-36">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const q          = quotes[item.ticker]
                const inMonitor  = monitorTickers.includes(item.ticker)
                const changePct  = q?.change_pct ?? 0
                const fc         = forecasts[item.ticker]
                const isFcOpen   = expandedFc === item.ticker
                const isFcLoading= loadingFc[item.ticker] ?? false
                const hasAlert   = !!(alerts[item.ticker]?.alert_high != null || alerts[item.ticker]?.alert_low != null)
                const isAlertOpen= alertEditor === item.ticker

                return (
                  <Fragment key={item.ticker}>
                    {/* ── main row ─────────────────────────────────────── */}
                    <tr
                      className="border-b border-slate-800 hover:bg-slate-800/40
                                 cursor-pointer transition-colors group"
                      onClick={() => navigate(`/stock/${item.ticker}`)}
                    >
                      <td className="px-6 py-3">
                        <p className="text-sm font-medium text-white">
                          {item.name || q?.name || item.ticker}
                        </p>
                        <p className="text-xs text-slate-500">{item.ticker}</p>
                      </td>
                      <td className="text-right px-4 py-3">
                        <span className={`font-mono text-sm
                          ${up(changePct) ? 'text-bull' : dn(changePct) ? 'text-bear' : 'text-slate-300'}`}>
                          {q ? q.price.toFixed(2) : '—'}
                        </span>
                      </td>
                      <td className="text-right px-4 py-3">
                        <span className={`text-sm font-medium
                          ${up(changePct) ? 'text-bull' : dn(changePct) ? 'text-bear' : 'text-slate-400'}`}>
                          {q ? `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%` : '—'}
                        </span>
                      </td>
                      <td className="text-right px-4 py-3 text-sm text-slate-400">
                        {q?.market_cap_total ? `${q.market_cap_total.toFixed(0)}亿` : '—'}
                      </td>
                      <td className="text-right px-4 py-3 text-sm text-slate-400">
                        {q?.pe_ttm?.toFixed(1) ?? '—'}
                      </td>
                      <td className="text-center px-3 py-3" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-center gap-1">
                          {/* monitor */}
                          <button
                            title={inMonitor ? '从监控移除' : '加入监控'}
                            onClick={() => toggleMonitor(item.ticker).catch((e) => alert(e.message))}
                            className={`p-1.5 rounded-lg transition-colors
                              ${inMonitor
                                ? 'text-yellow-400 bg-yellow-950/40 hover:bg-yellow-950/60'
                                : 'text-slate-500 hover:text-yellow-400 hover:bg-slate-700'}`}
                          >
                            <Activity className="w-3.5 h-3.5" />
                          </button>
                          {/* alert */}
                          <button
                            title={hasAlert ? '修改价格预警' : '设置价格预警'}
                            onClick={() => openAlertEditor(item.ticker)}
                            className={`relative p-1.5 rounded-lg transition-colors
                              ${isAlertOpen || hasAlert
                                ? 'text-amber-400 bg-amber-950/40 hover:bg-amber-950/60'
                                : 'text-slate-500 hover:text-amber-400 hover:bg-slate-700'}`}
                          >
                            <Bell className="w-3.5 h-3.5" />
                            {hasAlert && (
                              <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-amber-400" />
                            )}
                          </button>
                          {/* forecast */}
                          <button
                            title="AI 价格预测（1日）"
                            onClick={() => toggleForecast(item.ticker)}
                            className={`p-1.5 rounded-lg transition-colors
                              ${isFcOpen
                                ? 'text-blue-400 bg-blue-950/40 hover:bg-blue-950/60'
                                : 'text-slate-500 hover:text-blue-400 hover:bg-slate-700'}`}
                          >
                            <TrendingUp className={`w-3.5 h-3.5 ${isFcLoading ? 'animate-pulse' : ''}`} />
                          </button>
                          {/* delete */}
                          <button
                            title="删除自选"
                            onClick={() => removeItem(item.ticker)}
                            className="p-1.5 rounded-lg text-slate-500 hover:text-red-400
                                       hover:bg-red-950/30 transition-colors"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>

                    {/* ── alert editor row ─────────────────────────────── */}
                    {isAlertOpen && (
                      <tr className="border-b border-amber-500/20 bg-amber-950/5">
                        <td colSpan={6} className="px-6 py-3">
                          <div className="flex items-center gap-4 flex-wrap">
                            <span className="text-xs font-medium text-amber-300 w-14 flex-shrink-0">
                              价格预警
                            </span>
                            <label className="flex items-center gap-2 text-xs text-slate-400">
                              上限价
                              <div className="flex flex-col gap-0.5">
                                <input
                                  type="number"
                                  value={alertHigh}
                                  onChange={(e) => setAlertHigh(e.target.value)}
                                  placeholder={q ? `≤ ${fmt2(q.price * 3)}` : '—'}
                                  min={q ? q.price : undefined}
                                  max={q ? q.price * 3 : undefined}
                                  step="0.01"
                                  className="w-28 px-2 py-1 bg-slate-800 border border-slate-700 rounded-lg
                                             text-white text-xs focus:outline-none focus:border-amber-400"
                                />
                                {q && <span className="text-[10px] text-slate-600">上限 ≤ {fmt2(q.price * 3)}</span>}
                              </div>
                            </label>
                            <label className="flex items-center gap-2 text-xs text-slate-400">
                              下限价
                              <div className="flex flex-col gap-0.5">
                                <input
                                  type="number"
                                  value={alertLow}
                                  onChange={(e) => setAlertLow(e.target.value)}
                                  placeholder={q ? `≥ ${fmt2(Math.max(1, q.price / 3))}` : '—'}
                                  min={q ? Math.max(1, q.price / 3) : 1}
                                  max={q ? q.price : undefined}
                                  step="0.01"
                                  className="w-28 px-2 py-1 bg-slate-800 border border-slate-700 rounded-lg
                                             text-white text-xs focus:outline-none focus:border-amber-400"
                                />
                                {q && <span className="text-[10px] text-slate-600">下限 ≥ {fmt2(Math.max(1, q.price / 3))}</span>}
                              </div>
                            </label>
                            <button
                              onClick={() => saveAlert(item.ticker, q?.price)}
                              disabled={savingAlert}
                              className="px-3 py-1 rounded-lg bg-amber-600/30 border border-amber-500/40
                                         text-amber-300 text-xs hover:bg-amber-600/40 disabled:opacity-50"
                            >
                              {savingAlert ? '保存中...' : '保存'}
                            </button>
                            {alertSaveError && alertEditor === item.ticker && (
                              <span className="text-xs text-red-400">{alertSaveError}</span>
                            )}
                            {hasAlert && (
                              <>
                                <button
                                  onClick={() => clearAlert(item.ticker)}
                                  className="px-3 py-1 rounded-lg bg-slate-800 text-slate-400
                                             text-xs hover:text-red-400"
                                >
                                  清除
                                </button>
                                <span className="text-xs text-amber-400/60">
                                  当前：
                                  {alerts[item.ticker]?.alert_high != null
                                    ? `上限 ${fmt2(alerts[item.ticker].alert_high!)}`
                                    : ''}
                                  {alerts[item.ticker]?.alert_high != null &&
                                   alerts[item.ticker]?.alert_low != null
                                    ? ' / '
                                    : ''}
                                  {alerts[item.ticker]?.alert_low != null
                                    ? `下限 ${fmt2(alerts[item.ticker].alert_low!)}`
                                    : ''}
                                </span>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}

                    {/* ── forecast expanded row ────────────────────────── */}
                    {isFcOpen && fc && (
                      <tr className="border-b border-blue-500/20 bg-blue-950/5">
                        <td colSpan={6} className="px-6 py-3">
                          <div className="flex items-center gap-6 flex-wrap">
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">明日预测价</p>
                              <p className={`text-base font-mono font-semibold
                                ${fc.expected_return_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                                {fmt2(fc.predicted_price)}
                                <span className="text-xs ml-1 opacity-70">
                                  {fc.expected_return_pct >= 0 ? '+' : ''}
                                  {fc.expected_return_pct.toFixed(2)}%
                                </span>
                              </p>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">预测区间</p>
                              <p className="text-sm font-mono text-slate-300">
                                <span className="text-bear">{fmt2(fc.predicted_low)}</span>
                                <span className="text-slate-500 mx-1">~</span>
                                <span className="text-bull">{fmt2(fc.predicted_high)}</span>
                              </p>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">方向</p>
                              <p className={`text-sm font-medium ${TREND_COLOR[fc.trend] ?? 'text-slate-300'}`}>
                                {fc.trend}
                              </p>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">置信度</p>
                              <div className="flex items-center gap-1.5">
                                <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                  <div
                                    className="h-full bg-blue-400 rounded-full"
                                    style={{ width: `${(fc.confidence * 100).toFixed(0)}%` }}
                                  />
                                </div>
                                <span className="text-xs text-slate-400">
                                  {(fc.confidence * 100).toFixed(0)}%
                                </span>
                              </div>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">预计高点</p>
                              <p className="text-xs text-slate-300">{fc.high_time || '—'}</p>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 mb-0.5">预计低点</p>
                              <p className="text-xs text-slate-300">{fc.low_time || '—'}</p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

