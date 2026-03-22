/**
 * Monitor page — real-time price monitoring for up to 10 stocks.
 * Prices are pushed via WebSocket every 5 seconds.
 * Users can add/remove stocks from their personal monitor list.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Plus, RefreshCw, Wifi, WifiOff, Search, X } from 'lucide-react'
import StockCard from '@/components/StockCard'
import { PriceWebSocket, searchStocks } from '@/api/client'
import { useWatchlist } from '@/store/useWatchlist'
import type { Quote, SearchResult } from '@/types'

export default function MonitorPage() {
  const { monitorTickers, loadMonitor, toggleMonitor } = useWatchlist()
  const wsRef = useRef<PriceWebSocket | null>(null)
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [connected, setConnected] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load monitor list on mount
  useEffect(() => {
    loadMonitor()
  }, [])

  // Bootstrap/update WebSocket when ticker list changes
  useEffect(() => {
    if (monitorTickers.length === 0) {
      wsRef.current?.close()
      wsRef.current = null
      setConnected(false)
      return
    }

    if (wsRef.current) {
      wsRef.current.updateTickers(monitorTickers)
    } else {
      const ws = new PriceWebSocket(monitorTickers, (newQuotes) => {
        setConnected(true)
        setLastUpdated(new Date())
        setQuotes((prev) => {
          const updated = { ...prev }
          newQuotes.forEach((q) => { updated[q.ticker] = q })
          return updated
        })
      })
      wsRef.current = ws
    }

    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [monitorTickers])

  // Search debounced
  useEffect(() => {
    if (!searchQ.trim()) { setSearchResults([]); return }
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(async () => {
      setSearching(true)
      try {
        const res = await searchStocks(searchQ)
        setSearchResults(res.slice(0, 8))
      } catch {}
      setSearching(false)
    }, 400)
  }, [searchQ])

  const handleAdd = useCallback(async (result: SearchResult) => {
    try {
      await toggleMonitor(result.code || result.symbol)
      setShowSearch(false)
      setSearchQ('')
      setSearchResults([])
    } catch (e: any) {
      alert(e.message)
    }
  }, [toggleMonitor])

  const handleRemove = useCallback(async (ticker: string) => {
    await toggleMonitor(ticker)
  }, [toggleMonitor])

  const orderedQuotes = monitorTickers.map((t) => quotes[t]).filter(Boolean)

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
        <div>
          <h1 className="text-lg font-bold text-white">实时监控</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {monitorTickers.length > 0
              ? `${monitorTickers.length}/10 只股票 · 每5秒自动刷新`
              : '添加股票开始监控'}
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Connection status */}
          <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full
            ${connected
              ? 'bg-green-950/50 border border-green-900/50 text-green-400'
              : 'bg-slate-800 border border-slate-700 text-slate-500'}`}>
            {connected
              ? <><Wifi className="w-3 h-3" /> 实时连接</>
              : <><WifiOff className="w-3 h-3" /> 等待连接</>}
          </div>

          {lastUpdated && (
            <div className="flex items-center gap-1 text-xs text-slate-500">
              <RefreshCw className="w-3 h-3" />
              {lastUpdated.toLocaleTimeString('zh-CN')}
            </div>
          )}

          {/* Add button */}
          {monitorTickers.length < 10 && (
            <button
              onClick={() => setShowSearch(!showSearch)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700
                         text-white text-sm rounded-lg transition-colors"
            >
              <Plus className="w-4 h-4" />
              添加股票
            </button>
          )}
        </div>
      </div>

      {/* Search panel */}
      {showSearch && (
        <div className="px-6 py-3 bg-slate-900/60 border-b border-slate-800">
          <div className="max-w-md relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              autoFocus
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="搜索股票代码或名称（如：600519、贵州茅台）"
              className="w-full pl-9 pr-8 py-2 bg-slate-800 border border-slate-700 rounded-xl
                         text-white placeholder-slate-500 text-sm outline-none focus:border-blue-500"
            />
            {searchQ && (
              <button onClick={() => { setSearchQ(''); setSearchResults([]) }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          {(searching || searchResults.length > 0) && (
            <div className="max-w-md mt-2 bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
              {searching && (
                <p className="px-3 py-2 text-xs text-slate-400">搜索中...</p>
              )}
              {searchResults.map((r) => {
                const already = monitorTickers.includes(r.code || r.symbol)
                return (
                  <button
                    key={r.symbol}
                    disabled={already}
                    onClick={() => handleAdd(r)}
                    className="w-full flex items-center justify-between px-3 py-2.5 text-sm
                               hover:bg-slate-700 transition-colors border-b border-slate-700/50
                               last:border-0 disabled:opacity-40"
                  >
                    <div className="text-left">
                      <span className="text-white font-medium">{r.name}</span>
                      <span className="ml-2 text-xs text-slate-400">{r.code}</span>
                    </div>
                    <span className="text-xs text-slate-500">
                      {already ? '已添加' : `+ 添加`}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Stock grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {monitorTickers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-slate-500">
            <div className="w-16 h-16 rounded-2xl bg-slate-800 flex items-center justify-center mb-4">
              <Plus className="w-8 h-8" />
            </div>
            <p className="text-lg font-medium text-slate-400">还没有监控股票</p>
            <p className="text-sm mt-1">点击右上角"添加股票"开始实时监控</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {monitorTickers.map((ticker) => {
              const q = quotes[ticker]
              if (!q) {
                return (
                  <div key={ticker}
                    className="rounded-xl border border-slate-700 bg-slate-800/40 p-4 animate-pulse">
                    <p className="text-sm font-medium text-slate-400 mb-2">{ticker}</p>
                    <div className="h-7 bg-slate-700 rounded mb-3 w-32" />
                    <div className="h-3 bg-slate-700 rounded w-20" />
                  </div>
                )
              }
              return <StockCard key={ticker} quote={q} onRemove={handleRemove} />
            })}
          </div>
        )}
      </div>
    </div>
  )
}
