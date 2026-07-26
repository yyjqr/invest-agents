/**
 * BacktestPage — Agent prediction accuracy dashboard.
 *
 * Visualises historical accuracy of the AI agent's buy/sell/hold verdicts:
 *   • Win / Loss / Draw breakdown (donut)
 *   • Win rate by confidence tier (bar)
 *   • Win rate by verdict type (bar)
 *   • Avg predicted vs actual return calibration
 *   • Scrollable prediction history table
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  BarChart2,
  CheckCircle2,
  ChevronDown,
  RefreshCw,
  Target,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import {
  fetchAccuracyStats,
  fetchPredictions,
  triggerCheckPredictions,
  type AccuracyStats,
  type PredictionRecord,
} from '@/api/client'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(v: number) {
  return `${(v * 100).toFixed(1)}%`
}

function fmtReturn(v: number | null) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function fmtDate(ts: number | null) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const VERDICT_LABEL: Record<string, string> = {
  strong_buy:  '强买',
  buy:         '买入',
  hold:        '持有',
  sell:        '卖出',
  strong_sell: '强卖',
}

const VERDICT_COLOR: Record<string, string> = {
  strong_buy:  'text-red-400',
  buy:         'text-orange-400',
  hold:        'text-slate-400',
  sell:        'text-teal-400',
  strong_sell: 'text-green-400',
}

const OUTCOME_STYLE: Record<string, { label: string; cls: string }> = {
  win:     { label: '✓ 胜',  cls: 'bg-red-500/15 text-red-300' },
  loss:    { label: '✗ 败',  cls: 'bg-green-500/15 text-green-300' },
  draw:    { label: '= 平',  cls: 'bg-slate-600/40 text-slate-400' },
  pending: { label: '⏳ 待', cls: 'bg-amber-500/10 text-amber-400' },
}

const CONF_LABEL: Record<string, string> = { high: '高置信', medium: '中置信', low: '低置信' }

// ---------------------------------------------------------------------------
// Donut component (pure SVG, no dep)
// ---------------------------------------------------------------------------

interface DonutProps {
  wins: number
  losses: number
  draws: number
  pending: number
}

function WinLossDonut({ wins, losses, draws, pending }: DonutProps) {
  const total = wins + losses + draws + pending
  if (total === 0) return (
    <div className="flex items-center justify-center w-36 h-36 rounded-full border-4 border-slate-700 text-slate-500 text-xs">暂无数据</div>
  )

  const r = 52
  const cx = 68
  const cy = 68
  const circumference = 2 * Math.PI * r

  const segments = [
    { value: wins,    color: '#f87171', label: '胜' },   // red-400
    { value: losses,  color: '#4ade80', label: '败' },   // green-400
    { value: draws,   color: '#64748b', label: '平' },   // slate-500
    { value: pending, color: '#fbbf24', label: '待' },   // amber-400
  ]

  let offset = 0
  const arcs = segments.map((s) => {
    const frac = s.value / total
    const dash = frac * circumference
    const gap  = circumference - dash
    const el = (
      <circle
        key={s.label}
        cx={cx} cy={cy} r={r}
        fill="none"
        stroke={s.color}
        strokeWidth={14}
        strokeDasharray={`${dash} ${gap}`}
        strokeDashoffset={-offset}
        style={{ transform: 'rotate(-90deg)', transformOrigin: `${cx}px ${cy}px` }}
      />
    )
    offset += dash
    return el
  })

  const resolved = wins + losses + draws
  const winRate = resolved > 0 ? ((wins / resolved) * 100).toFixed(1) : '—'

  return (
    <div className="relative">
      <svg width={136} height={136}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1e293b" strokeWidth={14} />
        {arcs}
        <text x={cx} y={cy - 6}  textAnchor="middle" fill="#f1f5f9" fontSize="18" fontWeight="bold">{winRate}%</text>
        <text x={cx} y={cy + 14} textAnchor="middle" fill="#94a3b8" fontSize="10">胜率</text>
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Bar chart row
// ---------------------------------------------------------------------------

function BarRow({ label, wins, total, color = 'bg-red-500' }: {
  label: string; wins: number; total: number; color?: string
}) {
  const rate = total > 0 ? wins / total : 0
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 text-slate-400 text-right shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${rate * 100}%` }} />
      </div>
      <span className="w-10 text-slate-300 font-mono shrink-0 text-right">{pct(rate)}</span>
      <span className="w-12 text-slate-600 shrink-0 text-right">{wins}/{total}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function BacktestPage() {
  const [stats, setStats]       = useState<AccuracyStats | null>(null)
  const [preds, setPreds]       = useState<PredictionRecord[]>([])
  const [loading, setLoading]   = useState(true)
  const [checking, setChecking] = useState(false)
  const [showAll, setShowAll]   = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const [s, p] = await Promise.allSettled([
      fetchAccuracyStats(undefined, 200),
      fetchPredictions(undefined, 100),
    ])
    if (s.status === 'fulfilled') setStats(s.value)
    if (p.status === 'fulfilled') setPreds(p.value.slice().reverse())  // newest first
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const handleCheck = async () => {
    setChecking(true)
    try { await triggerCheckPredictions() } catch { /* ignore */ }
    await load()
    setChecking(false)
  }

  const displayPreds = showAll ? preds : preds.slice(0, 20)

  return (
    <div className="p-4 space-y-5 max-w-5xl mx-auto">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-400" />
          <h1 className="text-lg font-bold text-slate-100">AI 预测回测</h1>
          {stats && (
            <span className="text-xs text-slate-500 ml-1">
              共 {stats.total} 条 · 已结算 {stats.total - stats.pending} 条
            </span>
          )}
        </div>
        <button
          onClick={handleCheck}
          disabled={checking}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600/20 text-blue-300
            border border-blue-600/30 rounded-lg hover:bg-blue-600/30 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${checking ? 'animate-spin' : ''}`} />
          {checking ? '检查中…' : '更新结算'}
        </button>
      </div>

      {loading && (
        <div className="text-center py-16 text-slate-500 text-sm">加载中…</div>
      )}

      {!loading && stats && (
        <>
          {/* Top stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: '总胜率',  value: pct(stats.win_rate),                    icon: <Target className="w-4 h-4" />,        color: 'text-red-400' },
              { label: '平均预测收益', value: fmtReturn(stats.avg_predicted_return * 100), icon: <TrendingUp className="w-4 h-4" />,  color: 'text-orange-400' },
              { label: '平均实际收益', value: fmtReturn(stats.avg_actual_return * 100),    icon: <BarChart2 className="w-4 h-4" />,   color: stats.avg_actual_return >= 0 ? 'text-red-400' : 'text-green-400' },
              { label: '校准误差',  value: `±${(stats.calibration_error).toFixed(2)}%`,   icon: <Activity className="w-4 h-4" />,   color: 'text-slate-400' },
            ].map((c) => (
              <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-xl p-3">
                <div className="flex items-center gap-1.5 text-slate-500 mb-1.5">
                  {c.icon}
                  <span className="text-[11px]">{c.label}</span>
                </div>
                <p className={`text-xl font-bold font-mono ${c.color}`}>{c.value}</p>
              </div>
            ))}
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">

            {/* Donut */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-400 mb-3">胜负分布</p>
              <div className="flex items-center gap-4">
                <WinLossDonut
                  wins={stats.wins}
                  losses={stats.losses}
                  draws={stats.draws}
                  pending={stats.pending}
                />
                <div className="space-y-1.5 text-xs">
                  {[
                    { label: '胜',  v: stats.wins,    color: 'bg-red-400' },
                    { label: '败',  v: stats.losses,  color: 'bg-green-400' },
                    { label: '平',  v: stats.draws,   color: 'bg-slate-500' },
                    { label: '待',  v: stats.pending, color: 'bg-amber-400' },
                  ].map((it) => (
                    <div key={it.label} className="flex items-center gap-1.5">
                      <span className={`w-2.5 h-2.5 rounded-sm ${it.color}`} />
                      <span className="text-slate-400">{it.label}</span>
                      <span className="text-slate-200 font-mono ml-1">{it.v}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* By confidence */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-400 mb-3">置信度分层胜率</p>
              <div className="space-y-3 mt-2">
                {(['high', 'medium', 'low'] as const).map((c, i) => {
                  const d = stats.by_confidence[c]
                  const colors = ['bg-red-500', 'bg-orange-500', 'bg-slate-500']
                  return (
                    <BarRow
                      key={c}
                      label={CONF_LABEL[c]}
                      wins={d?.wins ?? 0}
                      total={d?.total ?? 0}
                      color={colors[i]}
                    />
                  )
                })}
              </div>
            </div>

            {/* By verdict */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-400 mb-3">信号类型胜率</p>
              <div className="space-y-3 mt-2">
                {(['strong_buy', 'buy', 'hold', 'sell', 'strong_sell'] as const).map((v, i) => {
                  const d = stats.by_verdict[v]
                  const colors = ['bg-red-600', 'bg-red-400', 'bg-slate-500', 'bg-teal-500', 'bg-green-500']
                  return (
                    <BarRow
                      key={v}
                      label={VERDICT_LABEL[v]}
                      wins={d?.wins ?? 0}
                      total={d?.total ?? 0}
                      color={colors[i]}
                    />
                  )
                })}
              </div>
            </div>
          </div>

          {/* Calibration card */}
          {stats.avg_predicted_return !== 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-400 mb-3">预测校准</p>
              <div className="flex items-end gap-6">
                {[
                  { label: '平均预测收益', v: stats.avg_predicted_return * 100, icon: <TrendingUp className="w-4 h-4" /> },
                  { label: '平均实际收益', v: stats.avg_actual_return * 100,    icon: <BarChart2  className="w-4 h-4" /> },
                ].map((it) => (
                  <div key={it.label} className="flex items-center gap-2">
                    <span className={`${it.v >= 0 ? 'text-red-400' : 'text-green-400'}`}>{it.icon}</span>
                    <div>
                      <p className="text-[11px] text-slate-500">{it.label}</p>
                      <p className={`text-lg font-bold font-mono ${it.v >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                        {fmtReturn(it.v)}
                      </p>
                    </div>
                  </div>
                ))}
                <div className="ml-auto text-right">
                  <p className="text-[11px] text-slate-500">校准误差 (MAE)</p>
                  <p className="text-lg font-bold font-mono text-slate-300">
                    ±{stats.calibration_error.toFixed(2)}%
                  </p>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Prediction history table */}
      {!loading && preds.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
            <p className="text-xs font-semibold text-slate-400">预测记录（最近 {preds.length} 条）</p>
            <button
              onClick={handleCheck}
              disabled={checking}
              className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1"
            >
              <RefreshCw className={`w-3 h-3 ${checking ? 'animate-spin' : ''}`} />刷新
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500">
                  <th className="text-left px-3 py-2 font-medium">股票</th>
                  <th className="text-left px-3 py-2 font-medium">信号</th>
                  <th className="text-right px-3 py-2 font-medium">置信</th>
                  <th className="text-right px-3 py-2 font-medium">预测涨跌</th>
                  <th className="text-right px-3 py-2 font-medium">实际涨跌</th>
                  <th className="text-center px-3 py-2 font-medium">结果</th>
                  <th className="text-right px-3 py-2 font-medium">时间</th>
                </tr>
              </thead>
              <tbody>
                {displayPreds.map((p) => {
                  const os = OUTCOME_STYLE[p.outcome] ?? OUTCOME_STYLE.pending
                  return (
                    <tr key={p.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="px-3 py-2">
                        <span className="text-slate-200 font-medium">{p.name}</span>
                        <span className="text-slate-600 ml-1 font-mono">{p.ticker.split('.')[0]}</span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`font-semibold ${VERDICT_COLOR[p.verdict] ?? 'text-slate-400'}`}>
                          {VERDICT_LABEL[p.verdict] ?? p.verdict}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-400">{p.confidence}</td>
                      <td className={`px-3 py-2 text-right font-mono ${p.predicted_return_pct >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                        {fmtReturn(p.predicted_return_pct)}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono ${
                        p.actual_return_pct == null ? 'text-slate-600'
                        : p.actual_return_pct >= 0 ? 'text-red-400' : 'text-green-400'
                      }`}>
                        {fmtReturn(p.actual_return_pct)}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${os.cls}`}>
                          {os.label}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-500">{fmtDate(p.timestamp)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {preds.length > 20 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="w-full py-2.5 text-xs text-slate-500 hover:text-slate-300 flex items-center justify-center gap-1 border-t border-slate-800"
            >
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAll ? 'rotate-180' : ''}`} />
              {showAll ? '收起' : `展开全部 ${preds.length} 条`}
            </button>
          )}
        </div>
      )}

      {!loading && preds.length === 0 && (
        <div className="text-center py-20 text-slate-500 text-sm">
          <Activity className="w-10 h-10 mx-auto mb-3 text-slate-700" />
          <p>暂无预测记录</p>
          <p className="text-xs mt-1 text-slate-600">在股票详情页进行 AI 分析后，预测记录将自动出现在这里</p>
        </div>
      )}

    </div>
  )
}
