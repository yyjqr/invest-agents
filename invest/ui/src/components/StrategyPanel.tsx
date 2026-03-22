/**
 * StrategyPanel — displays the three-strategy analysis report.
 */
import { AlertTriangle, CheckCircle, TrendingUp, Target, Activity } from 'lucide-react'
import type { StrategyReport, StrategySignal } from '@/types'

interface Props {
  report: StrategyReport
}

const SIGNAL_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  '买入':   { color: 'text-bull',      bg: 'bg-red-950/40',   border: 'border-red-900/50' },
  '卖出':   { color: 'text-bear',      bg: 'bg-green-950/30', border: 'border-green-900/50' },
  '观望':   { color: 'text-yellow-400', bg: 'bg-yellow-950/30', border: 'border-yellow-900/40' },
  '反弹机会': { color: 'text-orange-400', bg: 'bg-orange-950/30', border: 'border-orange-900/40' },
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score))
  const color = pct >= 65 ? '#ef4444' : pct >= 45 ? '#eab308' : '#22c55e'
  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1.5 rounded-full bg-slate-700">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-mono text-slate-400 w-8 text-right">{pct.toFixed(0)}</span>
    </div>
  )
}

function SignalCard({ signal }: { signal: StrategySignal }) {
  const style = SIGNAL_STYLE[signal.signal] ?? { color: 'text-slate-300', bg: 'bg-slate-800', border: 'border-slate-700' }
  return (
    <div className={`rounded-xl border p-4 ${style.bg} ${style.border}`}>
      <div className="flex items-start justify-between mb-2">
        <p className="text-xs text-slate-400 font-medium">{signal.name}</p>
        <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${style.color} ${style.border} bg-black/20`}>
          {signal.signal}
        </span>
      </div>
      <ScoreBar score={signal.score} />
      <div className="mt-3 space-y-1 max-h-32 overflow-y-auto">
        {signal.details.map((d, i) => (
          <p key={i} className="text-[11px] text-slate-300 leading-relaxed">{d}</p>
        ))}
      </div>
    </div>
  )
}

export default function StrategyPanel({ report }: Props) {
  const compositeStyle = SIGNAL_STYLE[report.composite_action]
    ?? { color: 'text-slate-300', bg: 'bg-slate-800', border: 'border-slate-700' }

  return (
    <div className="space-y-4">
      {/* Combined score header */}
      <div className={`rounded-xl border p-5 ${compositeStyle.bg} ${compositeStyle.border}`}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="font-bold text-white text-lg">{report.name}</h3>
            <p className="text-xs text-slate-400">{report.ticker}
              {report.sector && <span className="ml-2 text-slate-500">· {report.sector}</span>}
            </p>
          </div>
          <div className="text-right">
            <div className={`text-2xl font-bold ${compositeStyle.color}`}>
              {report.composite_action}
            </div>
            <div className="text-xs text-slate-400">仓位建议 {report.position_pct}%</div>
          </div>
        </div>

        {/* Combined score bar */}
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400 w-16">综合评分</span>
          <div className="flex-1">
            <ScoreBar score={report.combined_score} />
          </div>
        </div>

        {/* Tags */}
        <div className="flex flex-wrap gap-2 mt-3">
          {report.in_hot_sector && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-orange-900/40 border border-orange-800/40 text-orange-300">
              🔥 热门板块
            </span>
          )}
          <span className={`text-[11px] px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700
            ${report.northbound_sentiment === '偏强' ? 'text-red-400'
              : report.northbound_sentiment === '偏弱' ? 'text-green-400' : 'text-slate-400'}`}>
            北向 {report.northbound_sentiment}
          </span>
        </div>

        {/* Position note */}
        {report.position_note && (
          <p className="mt-3 text-xs text-slate-400 border-t border-white/5 pt-3">{report.position_note}</p>
        )}
      </div>

      {/* Three strategy cards */}
      <div className="grid grid-cols-1 gap-3">
        <SignalCard signal={report.strategy1} />
        <SignalCard signal={report.strategy2} />
        <SignalCard signal={report.strategy3} />
      </div>
    </div>
  )
}
