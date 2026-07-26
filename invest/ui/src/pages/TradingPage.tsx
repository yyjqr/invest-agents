import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BadgeDollarSign,
  CheckCircle2,
  Copy,
  RefreshCw,
  ShieldCheck,
  Trash2,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import {
  cancelOrder,
  fetchOrders,
  fetchPaperPositions,
  markOrderFilled,
  resetPaperAccount,
  submitOrder,
} from '@/api/client'
import type { OrderRecord, PaperAccountSummary } from '@/types'

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtMoney(v: number | null, unit = '¥') {
  if (v == null) return '--'
  return `${unit}${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtPct(v: number | null) {
  if (v == null) return '--'
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    filled:    'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30',
    pending:   'bg-amber-500/15 text-amber-300 border border-amber-500/30',
    cancelled: 'bg-slate-600/30 text-slate-400 border border-slate-600/30',
    rejected:  'bg-red-500/15 text-red-400 border border-red-500/30',
  }
  const label: Record<string, string> = {
    filled: '已成交', pending: '待成交', cancelled: '已撤单', rejected: '已拒绝',
  }
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${map[status] ?? 'text-slate-400'}`}>
      {label[status] ?? status}
    </span>
  )
}

// ─── Order submission form ────────────────────────────────────────────────

type FormMode = 'paper' | 'easytrader' | 'manual'

interface OrderForm {
  ticker: string
  name: string
  action: 'BUY' | 'SELL'
  quantity: string
  price: string
  mode: FormMode
  note: string
}

const DEFAULT_FORM: OrderForm = {
  ticker: '', name: '', action: 'BUY',
  quantity: '100', price: '', mode: 'paper', note: '',
}

function OrderFormPanel({ onSubmitted, prefill }: {
  onSubmitted: () => void
  prefill?: Partial<OrderForm>
}) {
  const [form, setForm] = useState<OrderForm>(DEFAULT_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [isError, setIsError] = useState(false)

  // Apply prefill whenever a copy is requested from the order history
  useEffect(() => {
    if (prefill) setForm({ ...DEFAULT_FORM, ...prefill })
  }, [prefill])

  const set = (k: keyof OrderForm, v: string) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const qty = parseInt(form.quantity)
    const price = parseFloat(form.price)
    if (!form.ticker.trim() || isNaN(qty) || isNaN(price)) {
      setResult('请填写完整的股票代码、数量和价格')
      setIsError(true)
      return
    }
    setSubmitting(true)
    setResult(null)
    try {
      const res = await submitOrder({
        ticker: form.ticker.trim().toUpperCase(),
        name: form.name,
        action: form.action,
        quantity: qty,
        price,
        mode: form.mode,
        note: form.note,
      })
      if (res.status === 'filled') {
        setResult(`✓ 委托成交 — ${form.action === 'BUY' ? '买入' : '卖出'} ${qty} 股，成交价 ¥${res.fill_price}`)
        setIsError(false)
        setForm(DEFAULT_FORM)
      } else if (res.status === 'pending') {
        setResult(`⟳ 委托已提交（${form.mode === 'manual' ? '请在券商客户端操作' : 'EasyTrader 处理中'}）`)
        setIsError(false)
        setForm(DEFAULT_FORM)
      } else {
        setResult(`委托被拒绝：${res.risk_check.replace(/^block:/, '')}`)
        setIsError(true)
      }
      onSubmitted()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setResult(msg)
      setIsError(true)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 space-y-4">
      <div className="flex items-center gap-2">
        <BadgeDollarSign className="w-4 h-4 text-blue-400" />
        <h2 className="text-sm font-semibold text-white">提交委托</h2>
        <span className="rounded-full bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 text-[11px] text-amber-300">
          仅供研究参考 · 不构成投资建议
        </span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        {/* Ticker + Name */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">股票代码</label>
            <input
              value={form.ticker} onChange={e => set('ticker', e.target.value)}
              placeholder="600519.SS"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">股票名称（可选）</label>
            <input
              value={form.name} onChange={e => set('name', e.target.value)}
              placeholder="贵州茅台"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* Action + Mode */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">买卖方向</label>
            <div className="flex rounded-lg overflow-hidden border border-slate-700">
              {(['BUY', 'SELL'] as const).map(a => (
                <button
                  key={a} type="button"
                  onClick={() => set('action', a)}
                  className={`flex-1 py-2 text-sm font-semibold transition-colors ${
                    form.action === a
                      ? a === 'BUY'
                        ? 'bg-red-500/20 text-red-300'
                        : 'bg-emerald-500/20 text-emerald-300'
                      : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  {a === 'BUY' ? '买入' : '卖出'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">执行模式</label>
            <select
              value={form.mode} onChange={e => set('mode', e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            >
              <option value="paper">模拟交易（纸上交易）</option>
              <option value="manual">手工记录（券商自行操作）</option>
              <option value="easytrader">EasyTrader 桥接</option>
            </select>
          </div>
        </div>

        {/* Quantity + Price */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">数量（股，最小100）</label>
            <input
              type="number" min={100} step={100}
              value={form.quantity} onChange={e => set('quantity', e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="text-[11px] text-slate-400 mb-1 block">价格（元）</label>
            <input
              type="number" min={0} step={0.01}
              value={form.price} onChange={e => set('price', e.target.value)}
              placeholder="0 = 市价"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* Note */}
        <div>
          <label className="text-[11px] text-slate-400 mb-1 block">备注（可选）</label>
          <input
            value={form.note} onChange={e => set('note', e.target.value)}
            placeholder="理由、策略标签等"
            className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
          />
        </div>

        <button
          type="submit" disabled={submitting}
          className={`w-full rounded-xl py-2.5 text-sm font-semibold transition-colors disabled:opacity-50 ${
            form.action === 'BUY'
              ? 'bg-red-500/20 hover:bg-red-500/30 text-red-300 border border-red-500/30'
              : 'bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30'
          }`}
        >
          {submitting ? '提交中...' : form.action === 'BUY' ? '买入委托' : '卖出委托'}
        </button>
      </form>

      {result && (
        <div className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
          isError
            ? 'bg-red-900/20 border-red-700/40 text-red-300'
            : 'bg-emerald-900/20 border-emerald-700/40 text-emerald-300'
        }`}>
          {isError ? <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />}
          {result}
        </div>
      )}
    </div>
  )
}

// ─── Paper account panel ────────────────────────────────────────────────────

function PaperAccountPanel({ summary, onReset }: {
  summary: PaperAccountSummary | null
  onReset: () => void
}) {
  const [resetting, setResetting] = useState(false)

  const handleReset = async () => {
    if (!window.confirm('确认重置模拟账户？所有持仓和委托历史将清空。')) return
    setResetting(true)
    await resetPaperAccount().catch(() => null)
    onReset()
    setResetting(false)
  }

  if (!summary) return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 animate-pulse h-36" />
  )

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <h2 className="text-sm font-semibold text-white">模拟账户</h2>
        </div>
        <button
          onClick={handleReset} disabled={resetting}
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-red-400 transition-colors"
        >
          <Trash2 className="w-3 h-3" />
          重置
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-slate-800/60 p-3">
          <p className="text-[11px] text-slate-400">可用资金</p>
          <p className="text-lg font-mono font-semibold text-white mt-0.5">{fmtMoney(summary.cash)}</p>
          <p className="text-[11px] text-slate-500 mt-0.5">
            初始 {fmtMoney(summary.initial_cash)} ·
            {summary.cash < summary.initial_cash
              ? ` 亏损 ${fmtMoney(summary.initial_cash - summary.cash)}`
              : ` 盈余 ${fmtMoney(summary.cash - summary.initial_cash)}`}
          </p>
        </div>
        <div className="rounded-xl bg-slate-800/60 p-3">
          <p className="text-[11px] text-slate-400">持仓股数</p>
          <p className="text-lg font-semibold text-white mt-0.5">{summary.positions.length} 只</p>
          <p className="text-[11px] text-slate-500 mt-0.5">
            {summary.positions.map(p => p.name || p.ticker).slice(0, 3).join('、')}
            {summary.positions.length > 3 ? ' 等' : ''}
          </p>
        </div>
      </div>

      {summary.positions.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-semibold text-slate-400">当前持仓</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-800">
                  <th className="text-left py-1.5 pr-4">股票</th>
                  <th className="text-right py-1.5 pr-4">数量</th>
                  <th className="text-right py-1.5">均成本</th>
                </tr>
              </thead>
              <tbody>
                {summary.positions.map(p => (
                  <tr key={p.ticker} className="border-b border-slate-800/50">
                    <td className="py-1.5 pr-4">
                      <span className="text-white">{p.name || p.ticker}</span>
                      <span className="ml-1.5 font-mono text-slate-500">{p.ticker.replace(/\.(SS|SZ)$/, '')}</span>
                    </td>
                    <td className="text-right py-1.5 pr-4 font-mono text-slate-200">{p.qty.toLocaleString()}</td>
                    <td className="text-right py-1.5 font-mono text-slate-200">¥{p.avg_cost.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Order history ────────────────────────────────────────────────────────

function OrderHistory({ orders, onRefresh, onCopy }: {
  orders: OrderRecord[]
  onRefresh: () => void
  onCopy: (o: OrderRecord) => void
}) {
  const [cancelling, setCancelling] = useState<string | null>(null)
  const [manualFill, setManualFill] = useState<{ id: string; price: string } | null>(null)

  const handleCancel = async (orderId: string) => {
    setCancelling(orderId)
    await cancelOrder(orderId).catch(() => null)
    onRefresh()
    setCancelling(null)
  }

  const handleFill = async (orderId: string, priceStr: string) => {
    const p = parseFloat(priceStr)
    if (isNaN(p) || p <= 0) return
    await markOrderFilled(orderId, p).catch(() => null)
    setManualFill(null)
    onRefresh()
  }

  if (orders.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-sm text-slate-500 text-center">
        暂无委托记录
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <p className="text-sm font-semibold text-white">委托历史</p>
        <button onClick={onRefresh} className="text-xs text-slate-500 hover:text-white transition-colors flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> 刷新
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 border-b border-slate-800">
              <th className="text-left px-4 py-2">时间</th>
              <th className="text-left px-4 py-2">股票</th>
              <th className="text-center px-4 py-2">方向</th>
              <th className="text-right px-4 py-2">数量</th>
              <th className="text-right px-4 py-2">委托价</th>
              <th className="text-right px-4 py-2">成交价</th>
              <th className="text-center px-4 py-2">模式</th>
              <th className="text-center px-4 py-2">状态</th>
              <th className="text-center px-4 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {orders.map(o => (
              <tr key={o.order_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="px-4 py-2 text-slate-400 whitespace-nowrap">{o.created_at.slice(5, 16)}</td>
                <td className="px-4 py-2">
                  <span className="text-white">{o.name || o.ticker}</span>
                  <span className="ml-1 font-mono text-slate-500">{o.ticker.replace(/\.(SS|SZ)$/, '')}</span>
                </td>
                <td className="px-4 py-2 text-center">
                  <span className={`font-semibold ${o.action === 'BUY' ? 'text-red-400' : 'text-emerald-400'}`}>
                    {o.action === 'BUY' ? (
                      <span className="flex items-center justify-center gap-0.5"><ArrowUpRight className="w-3 h-3" />买入</span>
                    ) : (
                      <span className="flex items-center justify-center gap-0.5"><ArrowDownRight className="w-3 h-3" />卖出</span>
                    )}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono text-slate-200">{o.quantity.toLocaleString()}</td>
                <td className="px-4 py-2 text-right font-mono text-slate-200">
                  {o.price > 0 ? `¥${o.price.toFixed(2)}` : '市价'}
                </td>
                <td className="px-4 py-2 text-right font-mono text-emerald-300">
                  {o.fill_price != null ? `¥${o.fill_price.toFixed(2)}` : '--'}
                </td>
                <td className="px-4 py-2 text-center">
                  <span className="rounded-full bg-slate-800 border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                    {o.mode === 'paper' ? '模拟' : o.mode === 'manual' ? '手工' : 'Easy'}
                  </span>
                </td>
                <td className="px-4 py-2 text-center">{statusBadge(o.status)}</td>
                <td className="px-4 py-2 text-center">
                  {o.status === 'pending' && o.mode === 'manual' && (
                    manualFill?.id === o.order_id ? (
                      <span className="flex items-center gap-1">
                        <input
                          type="number" step={0.01} min={0}
                          value={manualFill.price}
                          onChange={e => setManualFill({ id: o.order_id, price: e.target.value })}
                          className="w-20 rounded border border-slate-600 bg-slate-800 px-1.5 py-0.5 text-xs text-white outline-none"
                        />
                        <button onClick={() => handleFill(o.order_id, manualFill.price)}
                          className="text-emerald-400 hover:text-emerald-300">✓</button>
                        <button onClick={() => setManualFill(null)}
                          className="text-slate-500 hover:text-white">✕</button>
                      </span>
                    ) : (
                      <button onClick={() => setManualFill({ id: o.order_id, price: '' })}
                        className="text-xs text-blue-400 hover:text-blue-300">
                        回填成交
                      </button>
                    )
                  )}
                  {o.status === 'pending' && o.mode !== 'manual' && (
                    <button
                      onClick={() => handleCancel(o.order_id)}
                      disabled={cancelling === o.order_id}
                      className="text-xs text-slate-500 hover:text-red-400 transition-colors"
                    >
                      撤单
                    </button>
                  )}
                  {o.risk_check.startsWith('warn:') && (
                    <span title={o.risk_check.slice(5)}>
                      <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                    </span>
                  )}
                  {/* Copy to form — available for any order */}
                  <button
                    onClick={() => onCopy(o)}
                    className="ml-1 p-0.5 rounded text-slate-600 hover:text-blue-400 transition-colors"
                    title="复制委托到表单"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function TradingPage() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<PaperAccountSummary | null>(null)
  const [orders, setOrders] = useState<OrderRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [prefill, setPrefill] = useState<Partial<OrderForm> | undefined>()
  const formRef = useRef<HTMLDivElement>(null)

  const loadAll = async () => {
    setLoading(true)
    const [sum, ords] = await Promise.allSettled([
      fetchPaperPositions(),
      fetchOrders({ limit: 50 }),
    ])
    if (sum.status === 'fulfilled') setSummary(sum.value)
    if (ords.status === 'fulfilled') setOrders(ords.value)
    setLoading(false)
  }

  const handleCopyOrder = (o: OrderRecord) => {
    setPrefill({
      ticker: o.ticker,
      name: o.name || '',
      action: o.action as 'BUY' | 'SELL',
      quantity: String(o.quantity),
      price: o.price > 0 ? String(o.price) : '',
      mode: o.mode as FormMode,
      note: o.note || '',
    })
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  useEffect(() => { loadAll() }, [])

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-blue-400" />
            <h1 className="text-lg font-semibold text-white">交易中心</h1>
            <span className="rounded-full bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 text-[11px] text-amber-300">
              模拟 / 辅助记录
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            模拟交易（纸上交易）· 手工记录 · EasyTrader 桥接 — 风控层实时检查仓位限额
          </p>
        </div>
        <button
          onClick={loadAll} disabled={loading}
          className="flex items-center gap-1.5 rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500 hover:text-white transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {/* Risk disclaimer */}
      <div className="flex items-start gap-2 rounded-xl border border-amber-700/30 bg-amber-900/10 p-3 text-xs text-amber-300/80">
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
        <span>
          本功能仅供学习研究与交易复盘记录，不构成投资建议。A 股法规严禁个人程序化直接接入券商生产环境。
          <b>模拟交易</b>不产生任何真实资金变动；<b>EasyTrader</b> 模式需遵守所在券商用户协议。
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Left: form + positions */}
        <div className="space-y-4">
          <div ref={formRef}>
            <OrderFormPanel onSubmitted={loadAll} prefill={prefill} />
          </div>
          <PaperAccountPanel summary={summary} onReset={loadAll} />
        </div>

        {/* Right: architecture note */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 space-y-3 text-xs text-slate-400">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-blue-400" />
            <p className="text-sm font-semibold text-white">交易网关架构说明</p>
          </div>
          <div className="space-y-2 leading-relaxed">
            <p className="font-semibold text-slate-300">三种执行模式</p>
            <div className="space-y-1.5 pl-2 border-l border-slate-700">
              <p><span className="text-emerald-300">paper（模拟）</span> — 纯内存撮合，余额/持仓实时更新，零依赖，重启清零。</p>
              <p><span className="text-blue-300">manual（手工记录）</span> — 仅写入日志，用户在券商客户端自行操作后回填成交价。</p>
              <p><span className="text-amber-300">EasyTrader（桥接）</span> — 通过 GUI 自动化操作已登录的招商/华泰等客户端，需 pip install easytrader。</p>
            </div>
            <p className="font-semibold text-slate-300 mt-3">风控层（RiskGuard）</p>
            <div className="space-y-1 pl-2 border-l border-slate-700">
              <p>单笔委托金额 ≤ 账户现金 × 20%（可调）</p>
              <p>A 股最小交易单位 100 股强制校验</p>
              <p>卖出检查持仓是否充足</p>
            </div>
            <p className="font-semibold text-slate-300 mt-3">路径 B：券商量化 API（Rust FFI）</p>
            <div className="space-y-1 pl-2 border-l border-slate-700">
              <p>招商智远等量化终端提供本地 DLL 接口</p>
              <p>可通过 Rust bindgen 封装 C/C++ SDK，经 PyO3 暴露给 Python</p>
              <p>接口预留在 <span className="font-mono text-slate-300">trading_gateway._execute_rust_sdk()</span></p>
            </div>
          </div>
          <p
            className="mt-3 cursor-pointer text-blue-400 hover:text-blue-300 transition-colors"
            onClick={() => navigate('/portfolio/import')}
          >
            → 导入券商持仓对账单（路径 C）
          </p>
        </div>
      </div>

      {/* Order history */}
      <OrderHistory orders={orders} onRefresh={loadAll} onCopy={handleCopyOrder} />
    </div>
  )
}
