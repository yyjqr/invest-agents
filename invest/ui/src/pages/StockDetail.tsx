/**
 * StockDetail page — full K-line chart + strategy panel + forecast.
 * Reachable by clicking any StockCard or entering /stock/:ticker directly.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, BookmarkPlus, RefreshCw, Save, TrendingUp, AlertCircle, Building2 } from 'lucide-react'
import KLineChart from '@/components/KLineChart'
import StrategyPanel from '@/components/StrategyPanel'
import { fetchHistory, fetchIntraday, fetchStrategy, fetchForecast, fetchQuote, fetchStockProfile, saveForecastRecord, fetchResearchReports } from '@/api/client'
import { useWatchlist } from '@/store/useWatchlist'
import type { OHLCV, StrategyReport, Forecast, Quote, StockProfile, NewsItem } from '@/types'

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
  const [forecastHorizon, setForecastHorizon] = useState<'1h' | '1d' | '1w' | '1m' | '3m'>('1d')
  const [profile, setProfile] = useState<StockProfile | null>(null)
  const [stockNews, setStockNews] = useState<NewsItem[]>([])
  const [loadingChart, setLoadingChart] = useState(false)
  const [loadingStrategy, setLoadingStrategy] = useState(false)
  const [loadingForecast, setLoadingForecast] = useState(false)
  const [forecastSaved, setForecastSaved] = useState(false)
  const [forecastSaveError, setForecastSaveError] = useState<string | null>(null)
  const [error, setError] = useState('')

  const inWatchlist = items.some((i) => i.ticker === ticker)

  // Save forecast to history
  const handleSaveForecast = async () => {
    if (!forecast) return
    setForecastSaveError(null)
    try {
      await saveForecastRecord({
        date: new Date().toISOString().slice(0, 19).replace('T', ' '),
        ticker: forecast.ticker,
        name: forecast.name,
        horizon: forecast.horizon,
        current_price: forecast.current_price,
        predicted_price: forecast.predicted_price,
        predicted_high: forecast.predicted_high,
        predicted_low: forecast.predicted_low,
        trend: forecast.trend,
        confidence: forecast.confidence,
        expected_return_pct: forecast.expected_return_pct,
        scenarios: forecast.scenarios,
      })
      setForecastSaved(true)
      setTimeout(() => setForecastSaved(false), 2000)
    } catch (e: unknown) {
      setForecastSaveError(e instanceof Error ? e.message : '保存失败')
      setTimeout(() => setForecastSaveError(null), 4000)
    }
  }

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
        fetchForecast(ticker, forecastHorizon),
      ])
      if (r.status === 'fulfilled') setReport(r.value)
      if (q.status === 'fulfilled') setQuote(q.value)
      if (fc.status === 'fulfilled') setForecast(fc.value)
    } catch {}
    // Load profile in background (non-critical)
    fetchStockProfile(ticker).then(setProfile).catch(() => {})
    fetchResearchReports({ ticker, limit: 6 }).then(setStockNews).catch(() => {})
    setLoadingStrategy(false)
  }

  // Reload forecast when horizon changes
  const loadForecast = async (h: '1h' | '1d' | '1w' | '1m' | '3m') => {
    setForecastHorizon(h)
    setLoadingForecast(true)
    try {
      const fc = await fetchForecast(ticker, h)
      setForecast(fc)
    } catch {}
    setLoadingForecast(false)
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
            <div className={`bg-slate-900 rounded-xl border border-slate-800 p-4 ${loadingForecast ? 'opacity-60' : ''}`}>
              <div className="flex items-center gap-2 mb-3">
                <TrendingUp className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-semibold text-white">智能预测</h3>
                <div className="flex items-center gap-1 ml-2">
                  {(['1h', '1d', '1w', '1m', '3m'] as const).map((h) => (
                    <button
                      key={h}
                      onClick={() => loadForecast(h)}
                      disabled={loadingForecast}
                      className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                        forecastHorizon === h
                          ? 'bg-blue-600 text-white'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                      }`}
                    >
                      {{ '1h': '1小时', '1d': '明日', '1w': '一周', '1m': '一月', '3m': '三月' }[h]}
                    </button>
                  ))}
                </div>
                <span className={`ml-auto text-xs px-2 py-0.5 rounded-full
                  ${forecast.confidence >= 0.7 ? 'bg-green-950/50 text-green-400 border border-green-900/40'
                    : 'bg-yellow-950/50 text-yellow-400 border border-yellow-900/40'}`}>
                  置信度 {(forecast.confidence * 100).toFixed(0)}%
                </span>
                <button
                  onClick={handleSaveForecast}
                  title="保存预测记录"
                  className={`ml-1 p-1 rounded transition-colors ${
                    forecastSaved
                      ? 'text-green-400'
                      : forecastSaveError
                      ? 'text-red-400'
                      : 'text-slate-500 hover:text-blue-400 hover:bg-slate-800'
                  }`}
                >
                  <Save className="w-3.5 h-3.5" />
                </button>
                {forecastSaveError && (
                  <span className="text-xs text-red-400 ml-1">{forecastSaveError}</span>
                )}
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
                {forecast.support_price != null && (
                  <>　支撑：<span className="text-slate-300">¥{fmt(forecast.support_price)}</span></>
                )}
                {forecast.resistance_price != null && (
                  <>　压力：<span className="text-slate-300">¥{fmt(forecast.resistance_price)}</span></>
                )}
              </div>

              {/* Three-scenario probability forecast */}
              {forecast.scenarios && forecast.scenarios.length > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-800">
                  <p className="text-xs text-slate-500 mb-2">情景概率预测</p>
                  <div className="grid grid-cols-3 gap-2">
                    {forecast.scenarios.map((s) => {
                      const isOpt = s.label === '乐观'
                      const isPes = s.label === '悲观'
                      const borderColor = isOpt
                        ? 'border-green-800/50'
                        : isPes
                          ? 'border-red-800/50'
                          : 'border-blue-800/50'
                      const bgColor = isOpt
                        ? 'bg-green-950/30'
                        : isPes
                          ? 'bg-red-950/30'
                          : 'bg-blue-950/30'
                      const labelColor = isOpt
                        ? 'text-green-400'
                        : isPes
                          ? 'text-red-400'
                          : 'text-blue-400'
                      return (
                        <div
                          key={s.label}
                          className={`rounded-lg border ${borderColor} ${bgColor} p-2 text-center`}
                        >
                          <p className={`text-xs font-semibold ${labelColor} mb-1`}>
                            {s.label}
                            <span className="ml-1 text-slate-500 font-normal">
                              {(s.probability * 100).toFixed(0)}%
                            </span>
                          </p>
                          <p className="text-sm font-bold font-mono text-white">
                            ¥{fmt(s.predicted_price)}
                          </p>
                          <p className={`text-xs ${s.expected_return_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                            {s.expected_return_pct >= 0 ? '+' : ''}{fmt(s.expected_return_pct)}%
                          </p>
                          <p className="text-[10px] text-slate-500 mt-1 leading-tight">
                            {s.description}
                          </p>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Quantile price distribution band (80% and 50% CI) */}
              {forecast.quantile_10 != null && forecast.quantile_90 != null && (() => {
                const q10 = forecast.quantile_10!
                const q25 = forecast.quantile_25 ?? (q10 + (forecast.quantile_90! - q10) * 0.25)
                const q75 = forecast.quantile_75 ?? (q10 + (forecast.quantile_90! - q10) * 0.75)
                const q90 = forecast.quantile_90!
                const span = q90 - q10
                if (span <= 0) return null
                const pct = (v: number) => Math.max(0, Math.min(100, ((v - q10) / span) * 100))
                const pp = pct(forecast.predicted_price)
                const pc = pct(forecast.current_price)
                const p25 = pct(q25)
                const p75 = pct(q75)
                return (
                  <div className="mt-3 pt-3 border-t border-slate-800">
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-xs text-slate-500">价格分布区间</p>
                      <p className="text-[10px] text-slate-600">
                        P10 ¥{q10.toFixed(2)} · P90 ¥{q90.toFixed(2)}
                      </p>
                    </div>
                    <div className="relative h-5">
                      {/* 80% CI background */}
                      <div className="absolute top-1.5 bottom-1.5 left-0 right-0 bg-slate-800 rounded-full" />
                      {/* 50% CI band */}
                      <div
                        className="absolute top-0.5 bottom-0.5 bg-blue-900/70 rounded-full"
                        style={{ left: `${p25}%`, width: `${p75 - p25}%` }}
                      />
                      {/* Current price tick */}
                      <div
                        className="absolute top-0 bottom-0 w-0.5 bg-slate-400 rounded-full"
                        style={{ left: `${pc}%` }}
                        title={`当前 ¥${forecast.current_price.toFixed(2)}`}
                      />
                      {/* Predicted price dot */}
                      <div
                        className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-blue-400 border-2 border-slate-900"
                        style={{ left: `calc(${pp}% - 6px)` }}
                        title={`预测 ¥${forecast.predicted_price.toFixed(2)}`}
                      />
                    </div>
                    <div className="flex justify-between mt-0.5 text-[9px] text-slate-600">
                      <span>¥{q10.toFixed(2)}</span>
                      <span className="text-slate-500">预测 ¥{forecast.predicted_price.toFixed(2)}</span>
                      <span>¥{q90.toFixed(2)}</span>
                    </div>
                  </div>
                )
              })()}

              {/* News sentiment indicator */}
              {forecast.news_sentiment && (                <div className="mt-2 pt-2 border-t border-slate-800 flex items-center gap-2 text-xs">
                  <span className="text-slate-500">资讯情绪：</span>
                  <span className={
                    forecast.news_sentiment === '偏多'
                      ? 'text-green-400'
                      : forecast.news_sentiment === '偏空'
                        ? 'text-red-400'
                        : 'text-slate-400'
                  }>
                    {forecast.news_sentiment}
                  </span>
                  {forecast.news_sentiment_score != null && forecast.news_sentiment_score > 0 && (
                    <span className="text-slate-600">
                      ({forecast.news_sentiment_score.toFixed(0)}/100)
                    </span>
                  )}
                </div>
              )}

              {/* Factor details */}
              {forecast.factors && forecast.factors.length > 0 && (
                <div className="mt-2 pt-2 border-t border-slate-800">
                  <p className="text-xs text-slate-500 mb-1.5">关键因子</p>
                  <div className="space-y-1">
                    {forecast.factors.slice(0, 6).map((f, i) => (
                      <p key={i} className="text-[11px] text-slate-400 leading-snug">
                        {f}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Company info panel */}
          {profile && (
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <div className="flex items-center gap-2 mb-3">
                <Building2 className="w-4 h-4 text-slate-400" />
                <h3 className="text-sm font-semibold text-white">公司概要</h3>
                {profile.industry && (
                  <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
                    {profile.industry}
                  </span>
                )}
              </div>

              {/* Key metrics row */}
              <div className="grid grid-cols-4 gap-2 text-center mb-3">
                {profile.pe_ttm != null && (
                  <div>
                    <p className="text-[10px] text-slate-500">PE(TTM)</p>
                    <p className="text-sm font-mono text-white">{profile.pe_ttm.toFixed(1)}</p>
                  </div>
                )}
                {profile.pb_ratio != null && (
                  <div>
                    <p className="text-[10px] text-slate-500">PB</p>
                    <p className="text-sm font-mono text-white">{profile.pb_ratio.toFixed(2)}</p>
                  </div>
                )}
                {profile.profit_margin != null && Math.abs(profile.profit_margin) <= 5 && (
                  <div>
                    <p className="text-[10px] text-slate-500">净利率</p>
                    <p className={`text-sm font-mono ${profile.profit_margin >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {(profile.profit_margin * 100).toFixed(1)}%
                    </p>
                  </div>
                )}
                {profile.return_on_equity != null && Math.abs(profile.return_on_equity) <= 5 && (
                  <div>
                    <p className="text-[10px] text-slate-500">ROE</p>
                    <p className={`text-sm font-mono ${profile.return_on_equity >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {(profile.return_on_equity * 100).toFixed(1)}%
                    </p>
                  </div>
                )}
              </div>

              {/* Meta info */}
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500 mb-3">
                {profile.region && <span>📍 {profile.region}</span>}
                {profile.listing_date && <span>上市：{profile.listing_date}</span>}
                {profile.employees != null && <span>员工：{profile.employees.toLocaleString()}</span>}
                {profile.market_cap_total != null && (
                  <span>市值：{profile.market_cap_total >= 1000
                    ? `${(profile.market_cap_total / 1000).toFixed(1)}千亿`
                    : `${profile.market_cap_total.toFixed(0)}亿`}</span>
                )}
                {profile.revenue_growth_yoy != null && Math.abs(profile.revenue_growth_yoy) <= 50 && (
                  <span className={profile.revenue_growth_yoy >= 0 ? 'text-bull' : 'text-bear'}>
                    营收增速：{profile.revenue_growth_yoy >= 0 ? '+' : ''}{(profile.revenue_growth_yoy * 100).toFixed(1)}%
                  </span>
                )}
              </div>

              {/* Main business */}
              {profile.main_business && (
                <p className="text-[11px] text-slate-400 leading-relaxed mb-3 line-clamp-3">
                  {profile.main_business}
                </p>
              )}

              {/* Recent earnings table */}
              {profile.recent_earnings && profile.recent_earnings.length > 0 && (
                <div className="mt-1">
                  <p className="text-xs text-slate-500 mb-1.5">近期业绩</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[10px]">
                      <thead>
                        <tr className="text-slate-600">
                          <th className="text-left pb-1 font-normal">报告期</th>
                          <th className="text-right pb-1 font-normal">营收(亿)</th>
                          <th className="text-right pb-1 font-normal">净利(亿)</th>
                          <th className="text-right pb-1 font-normal">EPS</th>
                          <th className="text-right pb-1 font-normal">净利同比</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/50">
                        {profile.recent_earnings.map((e, i) => (
                          <tr key={i}>
                            <td className="py-1 text-slate-400">
                              {e.period.slice(0, 7)}
                              <span className="ml-1 text-slate-600">{e.report_type}</span>
                            </td>
                            <td className="py-1 text-right font-mono text-slate-300">
                              {e.revenue != null ? e.revenue.toFixed(1) : '—'}
                            </td>
                            <td className={`py-1 text-right font-mono ${
                              e.net_profit == null ? 'text-slate-500'
                              : e.net_profit >= 0 ? 'text-bull' : 'text-bear'
                            }`}>
                              {e.net_profit != null ? e.net_profit.toFixed(1) : '—'}
                            </td>
                            <td className="py-1 text-right font-mono text-slate-400">
                              {e.eps != null ? e.eps.toFixed(2) : '—'}
                            </td>
                            <td className={`py-1 text-right font-mono ${
                              e.profit_yoy == null ? 'text-slate-500'
                              : e.profit_yoy >= 0 ? 'text-bull' : 'text-bear'
                            }`}>
                              {e.profit_yoy != null
                                ? `${e.profit_yoy >= 0 ? '+' : ''}${e.profit_yoy.toFixed(1)}%`
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Research reports (positive / negative sentiment) */}
              {stockNews.filter(n => n.rating || n.rating_change).length > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-800">
                  <p className="text-xs text-slate-500 mb-2">最新研报</p>
                  <div className="space-y-2">
                    {stockNews
                      .filter(n => n.rating || n.rating_change)
                      .slice(0, 5)
                      .map((n, i) => {
                        const isPositive = ['买入', '增持', '推荐', '强烈推荐'].includes(n.rating ?? '')
                          || ['首次', '上调'].includes(n.rating_change ?? '')
                        const isNegative = ['减持', '卖出'].includes(n.rating ?? '')
                          || n.rating_change === '下调'
                        return (
                          <a
                            key={i}
                            href={n.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-start gap-2 group"
                          >
                            {n.rating && (
                              <span className={`flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded font-medium mt-0.5 ${
                                isPositive
                                  ? 'bg-green-950 text-green-400 border border-green-900'
                                  : isNegative
                                  ? 'bg-red-950 text-red-400 border border-red-900'
                                  : 'bg-slate-800 text-slate-400 border border-slate-700'
                              }`}>
                                {n.rating}
                              </span>
                            )}
                            <div className="flex-1 min-w-0">
                              <p className="text-[11px] text-slate-400 group-hover:text-slate-200 leading-relaxed line-clamp-2 transition-colors">
                                {n.title}
                              </p>
                              <p className="text-[10px] text-slate-600 mt-0.5">
                                {n.source}{n.date ? ` · ${n.date}` : ''}
                              </p>
                            </div>
                          </a>
                        )
                      })}
                  </div>
                </div>
              )}
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
