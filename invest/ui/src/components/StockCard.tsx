/**
 * StockCard — compact real-time price card for the monitor grid.
 * Shows price, change%, key metrics and a mini spark indicator.
 */
import { ArrowUpRight, ArrowDownRight, BarChart2, Minus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { Quote } from '@/types'

interface Props {
  quote: Quote
  onRemove?: (ticker: string) => void
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || isNaN(n)) return '—'
  return n.toFixed(digits)
}

function fmtMoney(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10000) return `${(n / 10000).toFixed(1)}亿`
  if (n >= 1) return `${n.toFixed(1)}亿`
  return `${(n * 10000).toFixed(0)}万`
}

export default function StockCard({ quote, onRemove }: Props) {
  const navigate = useNavigate()
  const up = quote.change_pct > 0
  const dn = quote.change_pct < 0
  const colorClass = up ? 'text-bull' : dn ? 'text-bear' : 'text-slate-400'
  const bgClass = up ? 'bg-red-950/40 border-red-900/50'
                     : dn ? 'bg-green-950/40 border-green-900/50'
                          : 'bg-slate-800/60 border-slate-700'

  return (
    <div
      className={`rounded-xl border p-4 cursor-pointer hover:scale-[1.02] transition-transform
                  ${bgClass} relative group`}
      onClick={() => navigate(`/stock/${quote.ticker}`)}
    >
      {/* Remove button */}
      {onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(quote.ticker) }}
          className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity
                     w-5 h-5 rounded-full bg-slate-700 hover:bg-red-700 text-slate-300
                     hover:text-white text-xs flex items-center justify-center"
        >
          ×
        </button>
      )}

      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="font-semibold text-white text-sm leading-tight">{quote.name || quote.ticker}</p>
          <p className="text-xs text-slate-500 mt-0.5">{quote.ticker}</p>
        </div>
        <div className={`flex items-center gap-0.5 ${colorClass}`}>
          {up ? <ArrowUpRight className="w-4 h-4" />
               : dn ? <ArrowDownRight className="w-4 h-4" />
                    : <Minus className="w-4 h-4" />}
        </div>
      </div>

      {/* Price */}
      <div className="mb-3">
        <span className={`text-2xl font-bold font-mono ${colorClass}`}>
          {fmt(quote.price, 2)}
        </span>
        <span className={`ml-2 text-sm font-medium ${colorClass}`}>
          {up ? '+' : ''}{fmt(quote.change, 2)}
        </span>
        <span className={`ml-1 text-sm ${colorClass}`}>
          ({up ? '+' : ''}{fmt(quote.change_pct, 2)}%)
        </span>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <Metric label="市值" value={fmtMoney(quote.market_cap_total)} />
        <Metric label="换手率" value={quote.turnover_rate != null ? `${fmt(quote.turnover_rate, 2)}%` : '—'} />
        <Metric label="PE(TTM)" value={fmt(quote.pe_ttm, 1)} />
        <Metric label="PB" value={fmt(quote.pb_ratio, 2)} />
      </div>

      {/* Chart icon */}
      <div className="mt-3 pt-2 border-t border-white/5 flex items-center gap-1 text-[10px] text-slate-500">
        <BarChart2 className="w-3 h-3" />
        点击查看K线与策略分析
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-slate-500">{label}：</span>
      <span className="text-slate-300">{value}</span>
    </div>
  )
}
