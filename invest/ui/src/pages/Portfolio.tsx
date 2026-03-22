/**
 * Portfolio page — shows user's current holdings with P&L.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Wallet, TrendingUp, TrendingDown, Upload } from 'lucide-react'
import { fetchBatchQuotes, deletePortfolioHolding } from '@/api/client'
import { API_BASE } from '@/api/client'
import type { Quote } from '@/types'

interface Holding {
  ticker: string
  shares: number
  avg_cost_basis: number
  asset_type: string
}

interface Portfolio {
  holdings: Record<string, Holding>
  cash: number
}

export default function PortfolioPage() {
  const navigate = useNavigate()
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const token = localStorage.getItem('invest_token')
      const res = await fetch(`${API_BASE}/api/portfolio`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const data: Portfolio = await res.json()
      setPortfolio(data)
      const tickers = Object.keys(data.holdings)
      if (tickers.length) {
        const qs = await fetchBatchQuotes(tickers)
        const map: Record<string, Quote> = {}
        qs.forEach((q) => (map[q.ticker] = q))
        setQuotes(map)
      }
    } catch { }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const holdings = Object.values(portfolio?.holdings ?? {})
  const cash = portfolio?.cash ?? 0

  let totalCost = 0
  let totalValue = 0
  holdings.forEach((h) => {
    const q = quotes[h.ticker]
    const price = q?.price ?? h.avg_cost_basis
    totalCost += h.shares * h.avg_cost_basis
    totalValue += h.shares * price
  })
  const totalPnl = totalValue - totalCost
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
        <div>
          <h1 className="text-lg font-bold text-white">我的持仓</h1>
          <p className="text-xs text-slate-500 mt-0.5">{holdings.length} 只股票</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate('/portfolio/import')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                       bg-blue-600/20 border border-blue-600/30 text-blue-400 text-xs font-medium
                       hover:bg-blue-600/30 transition-colors"
          >
            <Upload className="w-3.5 h-3.5" />
            导入持仓
          </button>
          <button
            onClick={load}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {/* Summary cards */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
            <p className="text-xs text-slate-500 mb-1">持仓市值</p>
            <p className="text-xl font-bold font-mono text-white">{totalValue.toFixed(2)}</p>
          </div>
          <div className={`bg-slate-900 rounded-xl border p-4
            ${totalPnl >= 0 ? 'border-red-900/40' : 'border-green-900/40'}`}>
            <p className="text-xs text-slate-500 mb-1">总盈亏</p>
            <p className={`text-xl font-bold font-mono ${totalPnl >= 0 ? 'text-bull' : 'text-bear'}`}>
              {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
            </p>
            <p className={`text-xs ${totalPnl >= 0 ? 'text-bull' : 'text-bear'}`}>
              {totalPnlPct >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%
            </p>
          </div>
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
            <p className="text-xs text-slate-500 mb-1 flex items-center gap-1">
              <Wallet className="w-3 h-3" /> 现金余额
            </p>
            <p className="text-xl font-bold font-mono text-slate-300">{cash.toFixed(2)}</p>
          </div>
        </div>

        {/* Holdings table */}
        {holdings.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-slate-600">
            <Wallet className="w-10 h-10 mb-3 text-slate-700" />
            <p className="text-slate-500 mb-3">暂无持仓记录</p>
            <button
              onClick={() => navigate('/portfolio/import')}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl
                         bg-blue-600 text-white text-sm font-medium hover:bg-blue-500"
            >
              <Upload className="w-4 h-4" />
              导入持仓
            </button>
          </div>
        ) : (
          <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-800">
                  <th className="text-left px-5 py-3 font-medium">股票</th>
                  <th className="text-right px-4 py-3 font-medium">持仓</th>
                  <th className="text-right px-4 py-3 font-medium">成本价</th>
                  <th className="text-right px-4 py-3 font-medium">现价</th>
                  <th className="text-right px-4 py-3 font-medium">市值</th>
                  <th className="text-right px-4 py-3 font-medium">盈亏</th>
                  <th className="text-right px-4 py-3 font-medium">盈亏%</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => {
                  const q = quotes[h.ticker]
                  const price = q?.price ?? h.avg_cost_basis
                  const value = h.shares * price
                  const cost = h.shares * h.avg_cost_basis
                  const pnl = value - cost
                  const pnlPct = cost > 0 ? (pnl / cost) * 100 : 0
                  const win = pnl >= 0

                  return (
                    <tr
                      key={h.ticker}
                      onClick={() => navigate(`/stock/${h.ticker}`)}
                      className="border-b border-slate-800 hover:bg-slate-800/40 cursor-pointer transition-colors"
                    >
                      <td className="px-5 py-3">
                        <p className="text-sm font-medium text-white">
                          {q?.name ?? h.ticker}
                        </p>
                        <p className="text-xs text-slate-500">{h.ticker}</p>
                      </td>
                      <td className="text-right px-4 py-3 text-sm font-mono text-slate-300">
                        {h.shares.toLocaleString()}
                      </td>
                      <td className="text-right px-4 py-3 text-sm font-mono text-slate-400">
                        {h.avg_cost_basis.toFixed(3)}
                      </td>
                      <td className={`text-right px-4 py-3 text-sm font-mono
                        ${(q?.change_pct ?? 0) > 0 ? 'text-bull' : (q?.change_pct ?? 0) < 0 ? 'text-bear' : 'text-slate-300'}`}>
                        {price.toFixed(3)}
                      </td>
                      <td className="text-right px-4 py-3 text-sm font-mono text-slate-300">
                        {value.toFixed(2)}
                      </td>
                      <td className={`text-right px-4 py-3 text-sm font-mono ${win ? 'text-bull' : 'text-bear'}`}>
                        {win ? '+' : ''}{pnl.toFixed(2)}
                      </td>
                      <td className={`text-right px-4 py-3 text-sm font-medium ${win ? 'text-bull' : 'text-bear'}`}>
                        {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
