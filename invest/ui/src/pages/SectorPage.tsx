/**
 * SectorPage — 板块分析页面
 *
 * 展示 15 个 A 股核心板块，每个板块包括：
 *  - 实时行情表格（价格、涨跌幅、成交额）
 *  - 三维度排行榜：涨幅领先、资金活跃、成交量排行
 * 点击股票跳转至 StockDetail 详情页。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowDownRight, ArrowUpRight, ChevronDown, ChevronRight,
  Flame, Layers, RefreshCw, TrendingUp,
} from 'lucide-react'
import { fetchIndices, fetchSectors } from '@/api/client'
import type { MarketIndex, SectorInfo, SectorStock } from '@/types'

// ── helpers ──────────────────────────────────────────────────────────────────
const SECTOR_COLOR: Record<string, string> = {
  '电子':     'from-indigo-900/60 to-indigo-800/40 border-indigo-700/50',
  '计算机':   'from-violet-900/60 to-violet-800/40 border-violet-700/50',
  '通信':     'from-cyan-900/60 to-cyan-800/40 border-cyan-700/50',
  '传媒':     'from-pink-900/60 to-pink-800/40 border-pink-700/50',
  '电力设备': 'from-emerald-900/60 to-emerald-800/40 border-emerald-700/50',
  '公用事业': 'from-lime-900/60 to-lime-800/40 border-lime-700/50',
  '汽车':     'from-blue-900/60 to-blue-800/40 border-blue-700/50',
  '机械设备': 'from-sky-900/60 to-sky-800/40 border-sky-700/50',
  '国防军工': 'from-slate-700/60 to-slate-600/40 border-slate-500/50',
  '医药生物': 'from-rose-900/60 to-rose-800/40 border-rose-700/50',
  '食品饮料': 'from-amber-900/60 to-amber-800/40 border-amber-700/50',
  '家用电器': 'from-green-900/60 to-green-800/40 border-green-700/50',
  '纺织服饰': 'from-fuchsia-900/60 to-fuchsia-800/40 border-fuchsia-700/50',
  '轻工制造': 'from-teal-900/60 to-teal-800/40 border-teal-700/50',
  '商贸零售': 'from-orange-900/60 to-orange-800/40 border-orange-700/50',
  '社会服务': 'from-purple-900/60 to-purple-800/40 border-purple-700/50',
  '银行':     'from-yellow-900/60 to-yellow-800/40 border-yellow-700/50',
  '非银金融': 'from-orange-900/60 to-orange-800/40 border-orange-700/50',
  '有色金属': 'from-zinc-700/60 to-zinc-600/40 border-zinc-500/50',
  '基础化工': 'from-fuchsia-900/60 to-fuchsia-800/40 border-fuchsia-700/50',
  '钢铁':     'from-slate-800/60 to-slate-700/40 border-slate-600/50',
  '煤炭':     'from-stone-800/60 to-stone-700/40 border-stone-600/50',
  '石油石化': 'from-red-900/60 to-red-800/40 border-red-700/50',
  '房地产':   'from-stone-700/60 to-stone-600/40 border-stone-500/50',
  '建筑材料': 'from-amber-900/60 to-amber-800/40 border-amber-700/50',
  '建筑装饰': 'from-yellow-900/60 to-yellow-800/40 border-yellow-700/50',
  '交通运输': 'from-teal-900/60 to-teal-800/40 border-teal-700/50',
  '农林牧渔': 'from-lime-900/60 to-lime-800/40 border-lime-700/50',
  '环保':     'from-green-900/60 to-green-800/40 border-green-700/50',
  '美容护理': 'from-pink-900/60 to-pink-800/40 border-pink-700/50',
  '综合':     'from-slate-700/60 to-slate-600/40 border-slate-500/50',
}
const SECTOR_TAG_COLOR: Record<string, string> = {
  '电子':     'bg-indigo-800/50 text-indigo-300 border border-indigo-700/50',
  '计算机':   'bg-violet-800/50 text-violet-300 border border-violet-700/50',
  '通信':     'bg-cyan-800/50 text-cyan-300 border border-cyan-700/50',
  '传媒':     'bg-pink-800/50 text-pink-300 border border-pink-700/50',
  '电力设备': 'bg-emerald-800/50 text-emerald-300 border border-emerald-700/50',
  '公用事业': 'bg-lime-800/50 text-lime-300 border border-lime-700/50',
  '汽车':     'bg-blue-800/50 text-blue-300 border border-blue-700/50',
  '机械设备': 'bg-sky-800/50 text-sky-300 border border-sky-700/50',
  '国防军工': 'bg-slate-600/50 text-slate-200 border border-slate-500/50',
  '医药生物': 'bg-rose-800/50 text-rose-300 border border-rose-700/50',
  '食品饮料': 'bg-amber-800/50 text-amber-300 border border-amber-700/50',
  '家用电器': 'bg-green-800/50 text-green-300 border border-green-700/50',
  '纺织服饰': 'bg-fuchsia-800/50 text-fuchsia-300 border border-fuchsia-700/50',
  '轻工制造': 'bg-teal-800/50 text-teal-300 border border-teal-700/50',
  '商贸零售': 'bg-orange-800/50 text-orange-300 border border-orange-700/50',
  '社会服务': 'bg-purple-800/50 text-purple-300 border border-purple-700/50',
  '银行':     'bg-yellow-800/50 text-yellow-300 border border-yellow-700/50',
  '非银金融': 'bg-orange-800/50 text-orange-200 border border-orange-700/50',
  '有色金属': 'bg-zinc-600/50 text-zinc-200 border border-zinc-500/50',
  '基础化工': 'bg-fuchsia-800/50 text-fuchsia-300 border border-fuchsia-700/50',
  '钢铁':     'bg-slate-700/50 text-slate-200 border border-slate-600/50',
  '煤炭':     'bg-stone-700/50 text-stone-200 border border-stone-600/50',
  '石油石化': 'bg-red-900/50 text-red-300 border border-red-700/50',
  '房地产':   'bg-stone-600/50 text-stone-200 border border-stone-500/50',
  '建筑材料': 'bg-amber-800/50 text-amber-200 border border-amber-700/50',
  '建筑装饰': 'bg-yellow-800/50 text-yellow-200 border border-yellow-700/50',
  '交通运输': 'bg-teal-800/50 text-teal-300 border border-teal-700/50',
  '农林牧渔': 'bg-lime-800/50 text-lime-300 border border-lime-700/50',
  '环保':     'bg-green-800/50 text-green-200 border border-green-700/50',
  '美容护理': 'bg-pink-800/50 text-pink-300 border border-pink-700/50',
  '综合':     'bg-slate-600/50 text-slate-200 border border-slate-500/50',
}

function bgColor(s: string) { return SECTOR_COLOR[s] ?? 'from-slate-800/60 to-slate-700/40 border-slate-600/50' }
function tColor(s: string) { return SECTOR_TAG_COLOR[s] ?? 'bg-slate-700/50 text-slate-200 border border-slate-600/50' }

function fmtPct(v?: number | null) {
  if (v == null) return '--'
  const s = v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
  return s
}
function fmtTurnover(v?: number | null) {
  if (v == null) return '--'
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return v.toFixed(0)
}
function fmtVol(v?: number | null) {
  if (v == null) return '--'
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万手`
  return `${v.toFixed(0)}手`
}
function pctClass(v?: number | null) {
  if (v == null) return 'text-slate-400'
  return v > 0 ? 'text-red-400' : v < 0 ? 'text-green-400' : 'text-slate-400'
}

// ── sector heatmap ───────────────────────────────────────────────────────────
function heatColor(avg: number | null): string {
  if (avg == null) return 'bg-slate-700/50 text-slate-400'
  if (avg >= 3)    return 'bg-red-600 text-white'
  if (avg >= 1.5)  return 'bg-red-700/80 text-red-100'
  if (avg >= 0.5)  return 'bg-red-900/60 text-red-200'
  if (avg > 0.05)  return 'bg-red-950/50 text-red-300'
  if (avg <= -3)   return 'bg-green-600 text-white'
  if (avg <= -1.5) return 'bg-green-700/80 text-green-100'
  if (avg <= -0.5) return 'bg-green-900/60 text-green-200'
  if (avg < -0.05) return 'bg-green-950/50 text-green-300'
  return 'bg-slate-700/50 text-slate-300'
}

function SectorHeatmap({
  sectors,
  focusSector,
  onSelect,
}: {
  sectors: SectorInfo[]
  focusSector: string | null
  onSelect: (name: string) => void
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <p className="text-[10px] font-semibold tracking-widest text-slate-500 uppercase mb-2.5">板块热力图 · 点击定位</p>
      <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-1.5">
        {sectors.map((s) => {
          const valid = s.stocks.filter((x) => x.change_pct != null)
          const avg = valid.length
            ? valid.reduce((sum, x) => sum + (x.change_pct ?? 0), 0) / valid.length
            : null
          const isFocus = focusSector === s.name
          return (
            <button
              key={s.name}
              onClick={() => onSelect(s.name)}
              className={`rounded-lg px-1.5 py-2.5 text-center transition-all duration-150 ${heatColor(avg)} ${isFocus ? 'ring-2 ring-white/60 scale-105 shadow-lg z-10' : 'hover:scale-[1.04]'}`}
            >
              <p className="text-[11px] font-semibold leading-tight truncate">{s.name}</p>
              <p className="text-[11px] font-mono mt-0.5 tabular-nums">{fmtPct(avg)}</p>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── ranking mini-table ──────────────────────────────────────────────────────
function RankingTable({
  title,
  icon,
  iconColor,
  stocks,
  metric,
  formatFn,
  navigate,
}: {
  title: string
  icon: React.ReactNode
  iconColor: string
  stocks: SectorStock[]
  metric: keyof SectorStock
  formatFn: (v: unknown) => string
  navigate: (ticker: string) => void
}) {
  if (!stocks || stocks.length === 0) return null
  return (
    <div className="flex-1 min-w-0">
      <div className={`flex items-center gap-1 mb-1.5 ${iconColor}`}>
        {icon}
        <span className="text-[10px] font-semibold tracking-wide uppercase">{title}</span>
      </div>
      <div className="space-y-0.5">
        {stocks.map((s, i) => (
          <button
            key={s.ticker}
            onClick={() => navigate(s.ticker)}
            className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded
                       hover:bg-white/5 transition-colors text-left"
          >
            <span className="text-[10px] font-bold text-slate-500 w-3 text-right">{i + 1}</span>
            <span className="text-xs text-slate-200 truncate flex-1">{s.name}</span>
            <span className={`text-xs font-mono tabular-nums ${
              metric === 'change_pct' ? pctClass(s.change_pct as number) : 'text-blue-300'
            }`}>
              {formatFn(s[metric])}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── stock table row ─────────────────────────────────────────────────────────
function StockRow({ stock, onClick }: { stock: SectorStock; onClick: () => void }) {
  const cp = stock.change_pct
  return (
    <button
      onClick={onClick}
      className="w-full grid grid-cols-[1fr_64px_72px_72px] gap-1 items-center
                 px-2 py-1.5 hover:bg-white/5 rounded transition-colors text-left"
    >
      <div className="min-w-0">
        <div className="text-xs font-semibold text-slate-200 truncate">{stock.name}</div>
        <div className="text-[10px] text-slate-500 font-mono">{stock.ticker.replace(/\.(SS|SZ)$/, '')}</div>
      </div>
      <span className="text-xs font-mono tabular-nums text-slate-300 text-right">
        {stock.price?.toFixed(2) ?? '--'}
      </span>
      <span className={`text-xs font-mono tabular-nums text-right ${pctClass(cp)}`}>
        {cp != null && cp > 0 && <ArrowUpRight className="inline w-3 h-3 -mt-0.5" />}
        {cp != null && cp < 0 && <ArrowDownRight className="inline w-3 h-3 -mt-0.5" />}
        {fmtPct(cp)}
      </span>
      <span className="text-[10px] font-mono tabular-nums text-slate-400 text-right">
        {fmtTurnover(stock.turnover)}
      </span>
    </button>
  )
}

// ── sector card ─────────────────────────────────────────────────────────────
function SectorCard({ sector, expanded, onToggle, isFocused, id }: {
  sector: SectorInfo; expanded: boolean; onToggle: () => void; isFocused?: boolean; id?: string
}) {
  const navigate = useNavigate()
  const go = (t: string) => navigate(`/stock/${encodeURIComponent(t)}`)

  // Compute sector-level stats
  const valid = sector.stocks.filter(s => s.price != null)
  const avgPct = valid.length
    ? valid.reduce((sum, s) => sum + (s.change_pct ?? 0), 0) / valid.length
    : null
  const totalTurnover = valid.reduce((sum, s) => sum + (s.turnover ?? 0), 0)

  return (
    <div id={id} className={`rounded-xl border bg-gradient-to-br ${bgColor(sector.name)} transition-all duration-200 ${isFocused ? 'ring-2 ring-blue-400/70 shadow-lg shadow-blue-900/20' : ''}`}>
      {/* Header */}
      <button onClick={onToggle} className="w-full flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${tColor(sector.name)}`}>
            {sector.name}
          </span>
          {avgPct != null && (
            <span className={`text-xs font-mono tabular-nums ${pctClass(avgPct)}`}>
              均涨 {fmtPct(avgPct)}
            </span>
          )}
          {totalTurnover > 0 && (
            <span className="text-[10px] text-slate-500">
              总额 {fmtTurnover(totalTurnover)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-slate-500">{sector.stocks.length}只</span>
          {expanded
            ? <ChevronDown className="w-4 h-4 text-slate-400" />
            : <ChevronRight className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="px-3 pb-3 space-y-3">
          {/* Rankings row */}
          <div className="flex gap-3">
            <RankingTable
              title="涨幅领先"
              icon={<TrendingUp className="w-3 h-3" />}
              iconColor="text-red-400"
              stocks={sector.top_gainers ?? []}
              metric="change_pct"
              formatFn={(v) => fmtPct(v as number)}
              navigate={go}
            />
            <RankingTable
              title="资金活跃"
              icon={<Flame className="w-3 h-3" />}
              iconColor="text-amber-400"
              stocks={sector.top_turnover ?? []}
              metric="turnover"
              formatFn={(v) => fmtTurnover(v as number)}
              navigate={go}
            />
            <RankingTable
              title="成交量排行"
              icon={<Layers className="w-3 h-3" />}
              iconColor="text-blue-400"
              stocks={sector.top_volume ?? []}
              metric="volume"
              formatFn={(v) => fmtVol(v as number)}
              navigate={go}
            />
          </div>

          {/* Full stock table */}
          <div>
            <div className="grid grid-cols-[1fr_64px_72px_72px] gap-1 px-2 pb-1 text-[10px] text-slate-500 font-semibold">
              <span>股票</span>
              <span className="text-right">现价</span>
              <span className="text-right">涨跌幅</span>
              <span className="text-right">成交额</span>
            </div>
            <div className="border-t border-white/5">
              {sector.stocks.map(s => (
                <StockRow key={s.ticker} stock={s} onClick={() => go(s.ticker)} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── main page ────────────────────────────────────────────────────────────────
export default function SectorPage() {
  const [sectors, setSectors] = useState<SectorInfo[]>([])
  const [indices, setIndices] = useState<MarketIndex[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [focusSector, setFocusSector] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, idx] = await Promise.all([
        fetchSectors(),
        fetchIndices().catch(() => [] as MarketIndex[]),
      ])
      setSectors(data)
      setIndices(idx)
      if (Object.keys(expanded).length === 0) {
        const init: Record<string, boolean> = {}
        data.forEach((s) => { init[s.name] = true })
        setExpanded(init)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const toggleAll = () => {
    const anyOpen = Object.values(expanded).some(Boolean)
    const next: Record<string, boolean> = {}
    sectors.forEach((s) => { next[s.name] = !anyOpen })
    setExpanded(next)
  }

  const onSelectSector = (name: string) => {
    setFocusSector((prev) => (prev === name ? null : name))
    setExpanded((prev) => ({ ...prev, [name]: true }))
    setTimeout(() => {
      document.getElementById(`sector-card-${name}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, 60)
  }

  return (
    <div className="p-4 space-y-4">
      {/* Title bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="w-5 h-5 text-blue-400" />
          <h1 className="text-lg font-semibold text-white">板块分析</h1>
          <span className="text-xs text-slate-400">— 实时行情 · 多维度排行</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={toggleAll}
            className="text-xs text-slate-400 hover:text-slate-200 transition-colors px-2 py-1
                       rounded border border-slate-700 hover:border-slate-500"
          >
            {Object.values(expanded).some(Boolean) ? '全部收起' : '全部展开'}
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded border border-slate-700 hover:border-slate-500
                       text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-red-900/30 border border-red-700/50 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Sector heatmap overview */}
      {sectors.length > 0 && (
        <SectorHeatmap sectors={sectors} focusSector={focusSector} onSelect={onSelectSector} />
      )}

      {/* Major indices bar */}
      {indices.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {indices.map((idx) => (
            <div
              key={idx.code}
              className="rounded-lg border border-slate-700/60 bg-slate-900/80 px-4 py-2.5 flex items-center justify-between"
            >
              <div>
                <p className="text-xs font-semibold text-slate-300">{idx.name}</p>
                <p className="text-sm font-bold font-mono text-white">
                  {idx.price != null ? idx.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--'}
                </p>
              </div>
              <div className="text-right">
                <p className={`text-xs font-mono tabular-nums ${
                  idx.change_pct != null && idx.change_pct > 0 ? 'text-red-400'
                    : idx.change_pct != null && idx.change_pct < 0 ? 'text-green-400'
                    : 'text-slate-400'
                }`}>
                  {idx.change != null ? (idx.change >= 0 ? '+' : '') + idx.change.toFixed(2) : '--'}
                </p>
                <p className={`text-sm font-bold font-mono tabular-nums ${
                  idx.change_pct != null && idx.change_pct > 0 ? 'text-red-400'
                    : idx.change_pct != null && idx.change_pct < 0 ? 'text-green-400'
                    : 'text-slate-400'
                }`}>
                  {idx.change_pct != null ? (idx.change_pct >= 0 ? '+' : '') + idx.change_pct.toFixed(2) + '%' : '--'}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {loading && sectors.length === 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-40 rounded-xl bg-slate-800/50 animate-pulse" />
          ))}
        </div>
      )}

      {sectors.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {sectors.map((s) => (
            <SectorCard
              key={s.name}
              id={`sector-card-${s.name}`}
              sector={s}
              expanded={!!expanded[s.name]}
              isFocused={focusSector === s.name}
              onToggle={() => setExpanded((prev) => ({ ...prev, [s.name]: !prev[s.name] }))}
            />
          ))}
        </div>
      )}
    </div>
  )
}
