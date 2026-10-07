import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { History, RefreshCw, Trash2 } from 'lucide-react'
import { clearForecastHistory, deleteForecastRecord, fetchForecastHistory } from '@/api/client'
import type { ForecastRecord } from '@/types'

function fmt(n: number | null | undefined, d = 2) {
  return n == null ? '—' : n.toFixed(d)
}

type ForecastRow = ForecastRecord & { originalIndex: number }

export default function ForecastHistoryPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<ForecastRow[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const rows = await fetchForecastHistory()
      const enriched = rows
        .map((item, index) => ({ ...item, originalIndex: index }))
        .reverse()
      setItems(enriched)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载预测记录失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleDelete = async (index: number) => {
    setBusy(true)
    setError('')
    try {
      const rows = await deleteForecastRecord(index)
      setItems(rows.map((item, i) => ({ ...item, originalIndex: i })).reverse())
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  const handleClear = async () => {
    if (!window.confirm('确认清空全部预测记录？')) return
    setBusy(true)
    setError('')
    try {
      await clearForecastHistory()
      setItems([])
    } catch (e) {
      setError(e instanceof Error ? e.message : '清空失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <History className="w-5 h-5 text-blue-400" />
            <h1 className="text-lg font-bold text-white">预测记录</h1>
          </div>
          <p className="text-xs text-slate-500 mt-1">查看已保存的智能预测记录，数据来自每个账户自己的 forecast_history.json。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading || busy}
            className="px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 hover:text-white hover:bg-slate-700 text-sm transition-colors disabled:opacity-50"
          >
            <span className="inline-flex items-center gap-1.5">
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </span>
          </button>
          <button
            onClick={handleClear}
            disabled={items.length === 0 || loading || busy}
            className="px-3 py-1.5 rounded-lg bg-red-950/40 text-red-300 hover:text-white border border-red-900/40 text-sm transition-colors disabled:opacity-50"
          >
            清空记录
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 rounded-lg border border-red-900/40 bg-red-950/40 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
        {loading ? (
          <div className="h-48 flex items-center justify-center text-slate-500 text-sm">
            <RefreshCw className="w-5 h-5 animate-spin mr-2" />
            加载中...
          </div>
        ) : items.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-slate-600 text-sm">
            暂无预测记录
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-500 border-b border-slate-800">
                  <th className="text-left px-4 py-3 font-medium">时间</th>
                  <th className="text-left px-4 py-3 font-medium">标的</th>
                  <th className="text-left px-4 py-3 font-medium">周期</th>
                  <th className="text-right px-4 py-3 font-medium">当前价</th>
                  <th className="text-right px-4 py-3 font-medium">预测价</th>
                  <th className="text-right px-4 py-3 font-medium">预期收益</th>
                  <th className="text-left px-4 py-3 font-medium">趋势</th>
                  <th className="text-right px-4 py-3 font-medium">置信度</th>
                  <th className="text-right px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {items.map((item) => (
                  <tr key={`${item.date}-${item.ticker}-${item.originalIndex}`} className="hover:bg-slate-800/40 transition-colors">
                    <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{item.date.replace('T', ' ')}</td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => navigate(`/stock/${encodeURIComponent(item.ticker)}`)}
                        className="text-left hover:text-white transition-colors"
                      >
                        <div className="font-medium text-blue-400">{item.ticker}</div>
                        <div className="text-xs text-slate-500">{item.name || '—'}</div>
                      </button>
                    </td>
                    <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{item.horizon}</td>
                    <td className="px-4 py-3 text-right font-mono text-slate-300">{fmt(item.current_price)}</td>
                    <td className="px-4 py-3 text-right font-mono text-white">{fmt(item.predicted_price)}</td>
                    <td className={`px-4 py-3 text-right font-mono ${item.expected_return_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {item.expected_return_pct >= 0 ? '+' : ''}{fmt(item.expected_return_pct)}%
                    </td>
                    <td className="px-4 py-3 text-slate-300 whitespace-nowrap">{item.trend}</td>
                    <td className="px-4 py-3 text-right text-slate-400">{(item.confidence * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleDelete(item.originalIndex)}
                        disabled={busy}
                        className="inline-flex items-center justify-center p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-slate-800 transition-colors disabled:opacity-50"
                        title="删除该记录"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}