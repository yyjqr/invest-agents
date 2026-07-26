import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart2,
  GitBranch,
  RefreshCw,
  Sparkles,
} from 'lucide-react'
import { fetchIndustryPicks } from '@/api/client'
import type { IndustryPickInfo, IndustryPicksResponse, IndustryPickStock } from '@/types'

const L1_COLOR: Record<string, string> = {
  '电子': 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30',
  '通信': 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30',
  '计算机': 'bg-violet-500/15 text-violet-300 border border-violet-500/30',
  '电力设备': 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30',
  '公用事业': 'bg-lime-500/15 text-lime-300 border border-lime-500/30',
  '汽车': 'bg-blue-500/15 text-blue-300 border border-blue-500/30',
  '医药生物': 'bg-rose-500/15 text-rose-300 border border-rose-500/30',
  '食品饮料': 'bg-amber-500/15 text-amber-300 border border-amber-500/30',
  '非银金融': 'bg-orange-500/15 text-orange-300 border border-orange-500/30',
  '银行': 'bg-yellow-500/15 text-yellow-300 border border-yellow-500/30',
  '石油石化': 'bg-red-500/15 text-red-300 border border-red-500/30',
  '基础化工': 'bg-fuchsia-500/15 text-fuchsia-300 border border-fuchsia-500/30',
  '机械设备': 'bg-sky-500/15 text-sky-300 border border-sky-500/30',
  '传媒': 'bg-pink-500/15 text-pink-300 border border-pink-500/30',
  '交通运输': 'bg-teal-500/15 text-teal-300 border border-teal-500/30',
  '房地产': 'bg-stone-500/15 text-stone-300 border border-stone-500/30',
  '有色金属': 'bg-zinc-500/15 text-zinc-300 border border-zinc-500/30',
  '煤炭': 'bg-slate-500/15 text-slate-300 border border-slate-500/30',
  '家用电器': 'bg-green-500/15 text-green-300 border border-green-500/30',
  '农林牧渔': 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30',
}

function fmtPct(v: number | null) {
  if (v == null) return '--'
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}

function pctClass(v: number | null) {
  if (v == null) return 'text-slate-400'
  return v > 0 ? 'text-red-400' : v < 0 ? 'text-green-400' : 'text-slate-400'
}

function fmtTurnover(v: number | null) {
  if (v == null) return '--'
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return v.toFixed(0)
}

function scoreClass(score: number) {
  if (score >= 70) return 'text-emerald-300'
  if (score >= 60) return 'text-blue-300'
  if (score >= 50) return 'text-amber-300'
  return 'text-slate-400'
}

function PickRow({ stock, onOpen }: { stock: IndustryPickStock; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="w-full rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-left hover:border-slate-700 hover:bg-slate-900 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white">{stock.name}</span>
            <span className="text-[11px] font-mono text-slate-500">{stock.ticker.replace(/\.(SS|SZ)$/, '')}</span>
          </div>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">{stock.reason}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className={`text-xs font-semibold ${scoreClass(stock.score)}`}>评分 {stock.score.toFixed(1)}</p>
          <p className="text-[11px] text-slate-500 mt-1">成交额 {fmtTurnover(stock.turnover)}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs">
        <span className="text-slate-400">现价 <span className="font-mono text-slate-200">{stock.price?.toFixed(2) ?? '--'}</span></span>
        <span className={`font-mono ${pctClass(stock.change_pct)}`}>
          {stock.change_pct != null && stock.change_pct > 0 && <ArrowUpRight className="inline w-3 h-3 -mt-0.5" />}
          {stock.change_pct != null && stock.change_pct < 0 && <ArrowDownRight className="inline w-3 h-3 -mt-0.5" />}
          {fmtPct(stock.change_pct)}
        </span>
      </div>
    </button>
  )
}

function IndustryCard({ item, onOpenStock }: { item: IndustryPickInfo; onOpenStock: (ticker: string) => void }) {
  const l1Tone = L1_COLOR[item.l1] ?? 'bg-slate-700/40 text-slate-200 border border-slate-600/50'

  return (
    <div className={`rounded-2xl border p-4 ${item.available ? 'border-slate-800 bg-slate-900/70' : 'border-slate-800/70 bg-slate-950/70 opacity-80'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap mb-2">
            <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${l1Tone}`}>{item.l1}</span>
            <span className="rounded-full border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{item.l2}</span>
            <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-400">{item.l3}</span>
          </div>
          <h2 className="text-base font-semibold text-white">{item.plate_name || `${item.l2} · 待匹配`}</h2>
          <p className="mt-1 text-xs leading-5 text-slate-400">{item.summary}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <div className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-1 text-[11px] text-blue-300">
            <Sparkles className="w-3 h-3" />
            {item.available ? `${item.picks.length} 只候选` : '等待数据'}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 flex-wrap text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1 rounded-full bg-slate-800 px-2 py-1">
          <GitBranch className="w-3 h-3" />
          L1/L2/L3 金字塔映射
        </span>
        {item.note && <span>{item.note}</span>}
      </div>

      {item.available ? (
        <div className="mt-4 grid grid-cols-1 xl:grid-cols-2 gap-3">
          {item.picks.map((stock) => (
            <PickRow key={stock.ticker} stock={stock} onOpen={() => onOpenStock(stock.ticker)} />
          ))}
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-dashed border-slate-800 px-4 py-6 text-sm text-slate-500">
          当前未命中可用板块，保留 taxonomy 位置，等待下一轮市场数据刷新。
        </div>
      )}
    </div>
  )
}

export default function IndustryPicksPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<IndustryPicksResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [perIndustry, setPerIndustry] = useState(5)

  const load = async (n = perIndustry) => {
    setLoading(true)
    setError(null)
    try {
      const next = await fetchIndustryPicks(n)
      setData(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const items = data?.items ?? []

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <BarChart2 className="w-5 h-5 text-blue-400" />
            <h1 className="text-lg font-semibold text-white">行业精选</h1>
            <span className="text-xs text-slate-400">— L1/L2/L3 金字塔 · 动态筛股</span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            独立于固定代表股板块分析，按实时行业板块热度、流动性和强弱筛出每个行业候选股。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-900 px-3 py-2">
            <span className="text-xs text-slate-400">每板块</span>
            <select
              value={perIndustry}
              onChange={e => {
                const n = Number(e.target.value)
                setPerIndustry(n)
                load(n)
              }}
              disabled={loading}
              className="bg-transparent text-sm text-white outline-none cursor-pointer"
            >
              {[3,4,5,6,7,8,9,10].map(n => (
                <option key={n} value={n} className="bg-slate-900">{n} 只</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500 hover:text-white transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <p className="text-[11px] font-semibold tracking-wide text-slate-400">L1 宏观配置</p>
          <p className="mt-2 text-sm text-slate-200">{data?.taxonomy.l1 ?? '申万一级行业（宏观配置）'}</p>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <p className="text-[11px] font-semibold tracking-wide text-slate-400">L2 中观景气</p>
          <p className="mt-2 text-sm text-slate-200">{data?.taxonomy.l2 ?? '二级行业（中观景气）'}</p>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
          <p className="text-[11px] font-semibold tracking-wide text-slate-400">L3 微观选股</p>
          <p className="mt-2 text-sm text-slate-200">{data?.taxonomy.l3 ?? '三级行业（微观选股）'}</p>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap text-xs text-slate-500">
        <span>更新时间 {data?.updated_at ?? '--'}</span>
        <span>已匹配行业 {data?.matched_count ?? 0}/{items.length || 20}</span>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-700/40 bg-rose-900/20 p-4 text-sm text-rose-300">
          {error}
        </div>
      )}

      {loading && items.length === 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {Array.from({ length: 6 }).map((_, idx) => (
            <div key={idx} className="h-56 rounded-2xl bg-slate-800/50 animate-pulse" />
          ))}
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {items.map((item) => (
            <IndustryCard
              key={item.slug}
              item={item}
              onOpenStock={(ticker) => navigate(`/stock/${encodeURIComponent(ticker)}`)}
            />
          ))}
        </div>
      )}
    </div>
  )
}