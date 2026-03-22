/**
 * StockDetail page — full K-line chart + strategy panel + forecast.
 * Reachable by clicking any StockCard or entering /stock/:ticker directly.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, BookmarkPlus, RefreshCw, TrendingUp, AlertCircle } from 'lucide-react'
import KLineChart from '@/components/KLineChart'
import StrategyPanel from '@/components/StrategyPanel'
import { fetchHistory, fetchIntraday, fetchStrategy, fetchForecast, fetchQuote } from '@/api/client'
import { useWatchlist } from '@/store/useWatchlist'
import type { OHLCV, StrategyReport, Forecast, Quote } from '@/types'

const PERIODS = ['5d', '1mo', '3mo', '6mo', '1y', '2y'] as const
type Period = typeof PERIODS[number]

function fmt(n: number | null | undefined, d = 2) {
  return n == null ? '—' : n.toFixed(d)
}

export default function StockDetailPage() {
  const { ticker = '' } = useParams<{ ticker: string }>()
  const navigate = useNavigate()
  const { addItem, items } = useWatchlist()

  const [period, setPeriod] = useState<Period>('3mo')
  const [chartMode, setChartMode] = useState<'day' | 'intraday'>('day')
  const [ohlcv, setOhlcv] = useState<OHLCV[]>([])
  const [quote, setQuote] = useState<Quote | null>(null)
  const [report, setReport] = useState<StrategyReport | null>(null)
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [loadingChart, setLoadingChart] = useState(false)
  const [loadingStrategy, setLoadingStrategy] = useState(false)
  const [error, setError] = useState('')

  const inWatchlist = items.some((i) => i.ticker === ticker)

  // Load chart data
  const loadChart = async () => {
    setLoadingChart(true)
    setError('')
    try {
      let data: OHLCV[]
      if (chartMode === 'intraday') {
        data = await fetchIntraday(ticker)
      } else {
        data = await fetchHistory(ticker, period)
      }
      setOhlcv(data)
    } catch (e: any) {
      setError(e.message)
    }
    setLoadingChart(false)
  }

  // Load strategy & quote
  const loadStrategy = async () => {
    setLoadingStrategy(true)
    try {
      const [r, q, fc] = await Promise.allSettled([
        fetchStrategy(ticker),
        fetchQuote(ticker),
        fetchForecast(ticker, '1d'),
      ])
      if (r.status === 'fulfilled') setReport(r.value)
      if (q.status === 'fulfilled') setQuote(q.value)
      if (fc.status === 'fulfilled') setForecast(fc.value)
    } catch {}
    setLoadingStrategy(false)
  }

  useEffect(() => {
    if (!ticker) return
    loadChart()
    loadStrategy()
  }, [ticker])

  useEffect(() => { loadChart() }, [chartMode, period])

  const handleAddWatchlist = async () => {
    try {
      await addItem(ticker, quote?.name ?? '')
    } catch (e: any) {
      alert(e.message)
    }
  }

  const up = (quote?.change_pct ?? 0) > 0
  const dn = (quote?.change_pct ?? 0) < 0

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-4 px-6 py-4 border-b border-slate-800">
        <button
          onClick={() => navigate(-1)}
          className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>

        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-bold text-white">
              {quote?.name ?? ticker}
            </h1>
            <span className="text-sm text-slate-500">{ticker}</span>
            {quote && (
              <>
                <span className={`text-xl font-bold font-mono
                  ${up ? 'text-bull' : dn ? 'text-bear' : 'text-slate-300'}`}>
                  {fmt(quote.price)}
                </span>
                <span className={`text-sm ${up ? 'text-bull' : dn ? 'text-bear' : 'text-slate-400'}`}>
                  {up ? '+' : ''}{fmt(quote.change)} ({up ? '+' : ''}{fmt(quote.change_pct)}%)
                </span>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!inWatchlist && (
            <button
              onClick={handleAddWatchlist}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700
                         text-slate-300 hover:text-white text-sm rounded-lg transition-colors"
            >
              <BookmarkPlus className="w-4 h-4" />
              加自选
            </button>
          )}
          <button
            onClick={() => { loadChart(); loadStrategy() }}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white"
          >
            <RefreshCw className={`w-4 h-4 ${loadingChart || loadingStrategy ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-6 mt-3 px-4 py-2.5 rounded-lg bg-red-950/40 border border-red-900/40
                        flex items-center gap-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 flex gap-6">
        {/* Left col — chart */}
        <div className="flex-1 min-w-0 space-y-4">
          {/* Chart controls */}
          <div className="flex items-center justify-between">
            <div className="flex gap-1">
              {(['day', 'intraday'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setChartMode(m)}
                  className={`px-3 py-1.5 text-xs rounded-lg transition-colors
                    ${chartMode === m
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  {m === 'day' ? '日K线' : '分时'}
                </button>
              ))}
            </div>
            {chartMode === 'day' && (
              <div className="flex gap-1">
                {PERIODS.map((p) => (
                  <button
                    key={p}
                    onClick={() => setPeriod(p)}
                    className={`px-2.5 py-1 text-xs rounded-lg transition-colors
                      ${period === p
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* K-Line chart */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-2">
            {loadingChart ? (
              <div className="flex items-center justify-center h-[420px] text-slate-500 text-sm">
                <RefreshCw className="w-5 h-5 animate-spin mr-2" />
                加载中...
              </div>
            ) : ohlcv.length > 0 ? (
              <KLineChart data={ohlcv} height={420} />
            ) : (
              <div className="flex items-center justify-center h-[420px] text-slate-600 text-sm">
                暂无数据
              </div>
            )}
          </div>

          {/* Forecast card */}
          {forecast && (
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <div className="flex items-center gap-2 mb-3">
                <TrendingUp className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-semibold text-white">智能预测（明日）</h3>
                <span className={`ml-auto text-xs px-2 py-0.5 rounded-full
                  ${forecast.confidence >= 0.7 ? 'bg-green-950/50 text-green-400 border border-green-900/40'
                    : 'bg-yellow-950/50 text-yellow-400 border border-yellow-900/40'}`}>
                  置信度 {(forecast.confidence * 100).toFixed(0)}%
                </span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-center">
                <div>
                  <p className="text-xs text-slate-500 mb-1">预测价格</p>
                  <p className="text-lg font-bold font-mono text-white">{fmt(forecast.predicted_price)}</p>
                  <p className={`text-xs ${forecast.expected_return_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                    {forecast.expected_return_pct >= 0 ? '+' : ''}{fmt(forecast.expected_return_pct)}%
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">预测高点</p>
                  <p className="text-lg font-bold font-mono text-bull">{fmt(forecast.predicted_high)}</p>
                  <p className="text-xs text-slate-500">{forecast.high_time}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">预测低点</p>
                  <p className="text-lg font-bold font-mono text-bear">{fmt(forecast.predicted_low)}</p>
                  <p className="text-xs text-slate-500">{forecast.low_time}</p>
                </div>
              </div>
              <div className="mt-3 pt-3 border-t border-slate-800 text-xs text-slate-500">
                趋势：<span className="text-slate-300">{forecast.trend}</span>
                　波动区间：±{fmt(forecast.expected_range_pct)}%
              </div>
            </div>
          )}
        </div>

        {/* Right col — strategy */}
        <div className="w-80 flex-shrink-0">
          {loadingStrategy ? (
            <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
              <RefreshCw className="w-5 h-5 animate-spin mr-2" />
              分析中...
            </div>
          ) : report ? (
            <StrategyPanel report={report} />
          ) : (
            <div className="text-center text-slate-600 text-sm pt-12">暂无策略数据</div>
          )}
        </div>
      </div>
    </div>
  )
}
