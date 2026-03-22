/**
 * WatchlistPage — manage the user's personal watchlist.
 * Each item can be pinned to the realtime monitor (max 10).
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Star, Trash2, Activity, Search, X } from 'lucide-react'
import { useWatchlist } from '@/store/useWatchlist'
import { searchStocks, fetchBatchQuotes } from '@/api/client'
import type { Quote, SearchResult } from '@/types'

export default function WatchlistPage() {
  const { items, monitorTickers, loadWatchlist, loadMonitor, addItem, removeItem, toggleMonitor } =
    useWatchlist()
  const navigate = useNavigate()
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searchTimer, setSearchTimer] = useState<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    loadWatchlist()
    loadMonitor()
  }, [])

  // Fetch quotes for watchlist
  useEffect(() => {
    if (!items.length) return
    fetchBatchQuotes(items.map((i) => i.ticker))
      .then((qs) => {
        const map: Record<string, Quote> = {}
        qs.forEach((q) => (map[q.ticker] = q))
        setQuotes(map)
      })
      .catch(() => {})
  }, [items])

  // Search debounce
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

  const handleAdd = async (r: SearchResult) => {
    await addItem(r.code || r.symbol, r.name)
    setSearchQ('')
    setSearchResults([])
  }

  const up = (pct: number) => pct > 0
  const dn = (pct: number) => pct < 0

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-800">
        <h1 className="text-lg font-bold text-white">自选股</h1>
        <p className="text-xs text-slate-500 mt-0.5">{items.length} 只股票 · 点击 ★ 加入实时监控</p>
      </div>

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
            <button onClick={() => { setSearchQ(''); setSearchResults([]) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white">
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
                <span className="text-xs text-slate-400">{r.code} <Plus className="w-3 h-3 inline ml-1" /></span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* List */}
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
                <th className="text-center px-3 py-3 font-medium w-24">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const q = quotes[item.ticker]
                const inMonitor = monitorTickers.includes(item.ticker)
                const changePct = q?.change_pct ?? 0

                return (
                  <tr
                    key={item.ticker}
                    className="border-b border-slate-800 hover:bg-slate-800/40
                               cursor-pointer transition-colors group"
                    onClick={() => navigate(`/stock/${item.ticker}`)}
                  >
                    <td className="px-6 py-3">
                      <p className="text-sm font-medium text-white">{item.name || q?.name || item.ticker}</p>
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
                      <div className="flex items-center justify-center gap-1.5">
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
                        <button
                          title="删除自选"
                          onClick={() => removeItem(item.ticker)}
                          className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-950/30 transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
