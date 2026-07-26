/**
 * Portfolio page — shows user's current holdings with P&L.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Wallet, TrendingUp, TrendingDown, Upload, Pencil, X, Trash2, BarChart2, Download } from 'lucide-react'
import { fetchBatchQuotes, fetchPortfolio, addPortfolioHolding, deletePortfolioHolding, sellPortfolioHolding, editPortfolioHolding, PriceWebSocket, downloadPortfolioCsv } from '@/api/client'
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
  const wsRef = useRef<PriceWebSocket | null>(null)

  // Edit modal state
  const [editH, setEditH] = useState<Holding | null>(null)
  const [editTab, setEditTab] = useState<'buy' | 'sell' | 'adjust'>('buy')
  const [fShares, setFShares] = useState('')
  const [fPrice, setFPrice] = useState('')
  const [fAdjShares, setFAdjShares] = useState('')
  const [fAdjCost, setFAdjCost] = useState('')
  const [editBusy, setEditBusy] = useState(false)
  const [editErr, setEditErr] = useState('')
  const [delConfirm, setDelConfirm] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchPortfolio()
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

  // WebSocket for continuous price updates
  useEffect(() => {
    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])
  useEffect(() => {
    const tickers = [...Object.keys(portfolio?.holdings ?? {}), '000300.SS']
    if (tickers.length <= 1) return  // only 000300.SS means no holdings yet
    if (wsRef.current) {
      wsRef.current.updateTickers(tickers)
    } else {
      wsRef.current = new PriceWebSocket(tickers, (newQuotes) => {
        setQuotes((prev) => {
          const map = { ...prev }
          newQuotes.forEach((q) => (map[q.ticker] = q))
          return map
        })
      })
    }
  }, [portfolio])

  const openEdit = (h: Holding, e: React.MouseEvent) => {
    e.stopPropagation()
    setEditH(h)
    setEditTab('buy')
    setFShares('')
    setFPrice('')
    setFAdjShares(String(h.shares))
    setFAdjCost(h.avg_cost_basis.toFixed(3))
    setEditErr('')
    setDelConfirm(false)
  }

  const closeEdit = () => { setEditH(null); setEditErr(''); setDelConfirm(false) }

  const handleEditSubmit = async () => {
    if (!editH || editBusy) return
    setEditBusy(true)
    setEditErr('')
    try {
      if (editTab === 'buy') {
        const s = parseFloat(fShares)
        const p = parseFloat(fPrice)
        if (!(s > 0)) throw new Error('请输入有效买入数量')
        if (!(p > 0)) throw new Error('请输入有效成交价')
        await addPortfolioHolding(editH.ticker, s, p, editH.asset_type)
      } else if (editTab === 'sell') {
        const s = parseFloat(fShares)
        const p = parseFloat(fPrice)
        if (!(s > 0)) throw new Error('请输入有效卖出数量')
        if (s > editH.shares) throw new Error(`超过持仓数量 ${editH.shares} 股`)
        if (!(p > 0)) throw new Error('请输入有效成交价')
        await sellPortfolioHolding(editH.ticker, s, p)
      } else {
        const s = parseFloat(fAdjShares)
        const c = parseFloat(fAdjCost)
        if (!(s > 0)) throw new Error('持仓数量必须大于 0')
        if (!(c > 0)) throw new Error('请输入有效成本价')
        await editPortfolioHolding(editH.ticker, s, c)
      }
      closeEdit()
      await load()
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : '操作失败，请重试')
    } finally {
      setEditBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!editH || editBusy) return
    if (!delConfirm) { setDelConfirm(true); return }
    setEditBusy(true)
    try {
      await deletePortfolioHolding(editH.ticker)
      closeEdit()
      await load()
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : '删除失败')
    } finally {
      setEditBusy(false)
    }
  }

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

  // Weighted portfolio daily change % = Σ(shares × price × change_pct) / totalValue
  let portfolioDailyPct = 0
  if (totalValue > 0) {
    holdings.forEach((h) => {
      const q = quotes[h.ticker]
      if (q) portfolioDailyPct += (h.shares * q.price * q.change_pct) / totalValue
    })
  }
  const benchmarkQ = quotes['000300.SS']
  const benchmarkPct: number | null = benchmarkQ ? benchmarkQ.change_pct : null
  const alpha: number | null = benchmarkPct !== null ? portfolioDailyPct - benchmarkPct : null

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
            onClick={() => downloadPortfolioCsv().catch(console.error)}
            title="导出 CSV"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                       bg-slate-700/40 border border-slate-700 text-slate-300 text-xs font-medium
                       hover:bg-slate-700/70 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            导出 CSV
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

        {/* Benchmark comparison — today's portfolio vs CSI 300 */}
        {holdings.length > 0 && (
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 mb-6">
            <div className="flex items-center gap-2 mb-3">
              <BarChart2 className="w-4 h-4 text-blue-400" />
              <p className="text-xs font-semibold text-slate-400">今日表现 vs 沪深300 基准</p>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-[11px] text-slate-500 mb-0.5">组合今日涨跌</p>
                <p className={`text-xl font-bold font-mono ${portfolioDailyPct >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {portfolioDailyPct >= 0 ? '+' : ''}{portfolioDailyPct.toFixed(2)}%
                </p>
              </div>
              <div>
                <p className="text-[11px] text-slate-500 mb-0.5">沪深300</p>
                <p className={`text-xl font-bold font-mono ${benchmarkPct == null ? 'text-slate-500' : benchmarkPct >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {benchmarkPct != null ? `${benchmarkPct >= 0 ? '+' : ''}${benchmarkPct.toFixed(2)}%` : '—'}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-slate-500 mb-0.5">超额收益 α</p>
                <p className={`text-xl font-bold font-mono ${alpha == null ? 'text-slate-500' : alpha >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {alpha != null ? `${alpha >= 0 ? '+' : ''}${alpha.toFixed(2)}%` : '—'}
                </p>
                {alpha != null && (
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    {alpha >= 0 ? '跑赢指数' : '跑输指数'}
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

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
                  <th className="px-3 py-3 font-medium text-slate-500">操作</th>
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
                      <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={(e) => openEdit(h, e)}
                          className="p-1.5 rounded-lg hover:bg-slate-700 text-slate-600 hover:text-slate-200 transition-colors"
                          title="编辑持仓"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Edit modal ── */}
      {editH && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={closeEdit}>
          <div className="absolute inset-0 bg-black/60" />
          <div
            className="relative w-full max-w-sm mx-4 bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl p-5"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-sm font-semibold text-white">
                  {quotes[editH.ticker]?.name ?? editH.ticker}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {editH.ticker} · 持仓 {editH.shares.toLocaleString()} 股 · 成本 {editH.avg_cost_basis.toFixed(3)}
                </p>
              </div>
              <button onClick={closeEdit} className="p-1 rounded-lg hover:bg-slate-800 text-slate-500 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Tabs */}
            <div className="flex gap-0 mb-4 border-b border-slate-800">
              {(['buy', 'sell', 'adjust'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => { setEditTab(t); setEditErr('') }}
                  className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                    editTab === t
                      ? t === 'buy'    ? 'text-blue-400 border-blue-500'
                        : t === 'sell' ? 'text-amber-400 border-amber-500'
                        : 'text-slate-200 border-slate-400'
                      : 'text-slate-500 border-transparent hover:text-slate-300'
                  }`}
                >
                  {t === 'buy' ? '买入' : t === 'sell' ? '卖出' : '修改持仓'}
                </button>
              ))}
            </div>

            {/* Form */}
            {editTab !== 'adjust' ? (
              <div className="space-y-3">
                <div>
                  <label className="text-xs text-slate-500 mb-1 block">数量（股）</label>
                  <input
                    type="number" min="0" step="100"
                    value={fShares} onChange={(e) => setFShares(e.target.value)}
                    placeholder={editTab === 'sell' ? `最多 ${editH.shares} 股` : ''}
                    className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-500 mb-1 block">成交价</label>
                  <input
                    type="number" min="0" step="0.01"
                    value={fPrice} onChange={(e) => setFPrice(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-blue-500"
                  />
                </div>
                {fShares && fPrice && !isNaN(+fShares) && !isNaN(+fPrice) && (
                  <p className="text-xs text-slate-500">成交金额：{(+fShares * +fPrice).toFixed(2)}</p>
                )}
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="text-xs text-slate-500 mb-1 block">持仓数量（股）</label>
                  <input
                    type="number" min="0"
                    value={fAdjShares} onChange={(e) => setFAdjShares(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-500 mb-1 block">成本均价</label>
                  <input
                    type="number" min="0" step="0.001"
                    value={fAdjCost} onChange={(e) => setFAdjCost(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
                <p className="text-xs text-slate-600">直接修改持仓数量与成本价，不记录交易流水。</p>
              </div>
            )}

            {editErr && <p className="text-xs text-rose-400 mt-2">{editErr}</p>}

            {/* Actions */}
            <div className="flex items-center gap-2 mt-4">
              <button
                onClick={handleEditSubmit}
                disabled={editBusy}
                className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
                  editTab === 'buy'  ? 'bg-blue-600 hover:bg-blue-500 text-white' :
                  editTab === 'sell' ? 'bg-amber-600 hover:bg-amber-500 text-white' :
                                      'bg-slate-700 hover:bg-slate-600 text-white'
                }`}
              >
                {editBusy ? '处理中…' : editTab === 'buy' ? '确认买入' : editTab === 'sell' ? '确认卖出' : '保存修改'}
              </button>
              {!delConfirm ? (
                <button
                  onClick={() => setDelConfirm(true)}
                  className="p-2 rounded-lg text-slate-600 hover:text-rose-400 hover:bg-slate-800 transition-colors"
                  title="删除持仓"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              ) : (
                <button
                  onClick={handleDelete}
                  disabled={editBusy}
                  className="px-3 py-2 rounded-lg text-xs bg-rose-600/20 text-rose-400 border border-rose-700/40 hover:bg-rose-600/30 transition-colors disabled:opacity-50"
                >
                  确认删除
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
