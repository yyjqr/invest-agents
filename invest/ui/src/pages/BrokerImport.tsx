/**
 * BrokerImport — import portfolio holdings from various Chinese brokerages.
 *
 * Supports: 招商证券, 腾讯微证券, 长江证券, 东方财富, 同花顺,
 *           支付宝基金, 天天基金, 通用CSV, 快速粘贴, 手动JSON/CSV
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    ArrowLeft,
    CheckCircle2,
    ChevronRight,
    FileUp,
    Info,
    Loader2,
    Plus,
    Upload,
    XCircle,
} from 'lucide-react'
import {
    addPortfolioHolding,
    fetchBrokers,
    importExecute,
    importPreview,
    type BrokerInfo,
    type ImportedHolding,
    type ImportPreviewResult,
} from '@/api/client'

type Step = 'select' | 'upload' | 'preview' | 'done' | 'manual_add'

export default function BrokerImportPage() {
    const navigate = useNavigate()
    const fileRef = useRef<HTMLInputElement>(null)

    const [brokers, setBrokers] = useState<BrokerInfo[]>([])
    const [step, setStep] = useState<Step>('select')
    const [selectedBroker, setSelectedBroker] = useState<BrokerInfo | null>(null)
    const [fileContent, setFileContent] = useState('')
    const [preview, setPreview] = useState<ImportPreviewResult | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge')
    const [result, setResult] = useState<{ added: number; updated: number; skipped: number } | null>(null)

    // Manual add form
    const [manualTicker, setManualTicker] = useState('')
    const [manualName, setManualName] = useState('')
    const [manualShares, setManualShares] = useState('')
    const [manualCost, setManualCost] = useState('')
    const [manualType, setManualType] = useState('stock')
    const [manualBatchMode, setManualBatchMode] = useState(false)
    const [manualBatchText, setManualBatchText] = useState('')

    useEffect(() => {
        fetchBrokers().then(setBrokers).catch(() => { })
    }, [])

    // -- Step 1: select broker --
    const selectBroker = (b: BrokerInfo) => {
        setSelectedBroker(b)
        setError('')
        setPreview(null)
        setFileContent('')
        if (b.id === 'manual_json') {
            setStep('upload') // show textarea
        } else {
            setStep('upload')
        }
    }

    // -- Step 2: upload / paste --
    const handleFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (!file) return
        const text = await file.text()
        setFileContent(text)
    }, [])

    const doPreview = async () => {
        if (!selectedBroker || !fileContent.trim()) {
            setError('请先上传文件或粘贴内容')
            return
        }
        setLoading(true)
        setError('')
        try {
            const res = await importPreview(selectedBroker.id, fileContent)
            setPreview(res)
            if (res.holdings.length > 0) {
                setStep('preview')
            } else {
                setError('未能解析到任何持仓数据，请检查文件格式')
            }
        } catch (err: any) {
            setError(err.message || '解析失败')
        }
        setLoading(false)
    }

    // -- Step 3: confirm import --
    const doImport = async () => {
        if (!selectedBroker || !fileContent) return
        setLoading(true)
        setError('')
        try {
            const res = await importExecute(selectedBroker.id, fileContent, importMode)
            setResult(res.summary)
            setStep('done')
        } catch (err: any) {
            setError(err.message || '导入失败')
        }
        setLoading(false)
    }

    // -- Manual add single --
    const doManualAdd = async () => {
        if (!manualTicker.trim() || !manualShares || !manualCost) {
            setError('请填写完整信息')
            return
        }
        setLoading(true)
        setError('')
        try {
            await addPortfolioHolding(manualTicker.trim(), Number(manualShares), Number(manualCost), manualType)
            setResult({ added: 1, updated: 0, skipped: 0 })
            setStep('done')
        } catch (err: any) {
            setError(err.message || '添加失败')
        }
        setLoading(false)
    }

    // -- Manual batch paste (quick_paste broker) --
    const doManualBatchPreview = async () => {
        if (!manualBatchText.trim()) {
            setError('请先输入持仓内容')
            return
        }
        const quickBroker = brokers.find((b) => b.id === 'quick_paste')
        if (!quickBroker) {
            setError('快速粘贴功能暂不可用')
            return
        }
        setLoading(true)
        setError('')
        try {
            const res = await importPreview('quick_paste', manualBatchText)
            setPreview(res)
            setSelectedBroker(quickBroker)
            setFileContent(manualBatchText)
            if (res.holdings.length > 0) {
                setStep('preview')
            } else {
                setError('未能解析到任何持仓数据，请检查格式：代码,名称,数量,成本价')
            }
        } catch (err: any) {
            setError(err.message || '解析失败')
        }
        setLoading(false)
    }

    const reset = () => {
        setStep('select')
        setSelectedBroker(null)
        setFileContent('')
        setPreview(null)
        setError('')
        setResult(null)
        setManualTicker('')
        setManualName('')
        setManualShares('')
        setManualCost('')
        setManualBatchMode(false)
        setManualBatchText('')
    }

    // Group brokers
    const stockBrokers = brokers.filter((b) => b.type === 'stock')
    const fundBrokers = brokers.filter((b) => b.type === 'fund')
    const otherBrokers = brokers.filter((b) => b.type === 'any')

    return (
        <div className="h-full flex flex-col">
            {/* Header */}
            <div className="flex items-center gap-3 px-6 py-4 border-b border-slate-800">
                <button
                    onClick={() => (step === 'select' ? navigate('/portfolio') : reset())}
                    className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white"
                >
                    <ArrowLeft className="w-4 h-4" />
                </button>
                <div>
                    <h1 className="text-lg font-bold text-white">导入持仓</h1>
                    <p className="text-xs text-slate-500 mt-0.5">
                        从券商 / 基金平台导入持仓数据
                    </p>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
                {/* ========= STEP: Select Broker ========= */}
                {step === 'select' && (
                    <div className="max-w-2xl mx-auto space-y-6">
                        {/* Manual entry shortcut */}
                        <button
                            onClick={() => { setStep('manual_add'); setError('') }}
                            className="w-full flex items-center gap-4 p-4 bg-blue-600/10 border border-blue-600/30
                         rounded-xl hover:bg-blue-600/20 transition-colors text-left"
                        >
                            <div className="w-10 h-10 rounded-lg bg-blue-600/20 flex items-center justify-center text-blue-400">
                                <Plus className="w-5 h-5" />
                            </div>
                            <div className="flex-1">
                                <p className="text-sm font-medium text-blue-400">手动添加持仓</p>
                                <p className="text-xs text-slate-500">直接输入股票代码、数量和成本价</p>
                            </div>
                            <ChevronRight className="w-4 h-4 text-slate-600" />
                        </button>

                        {/* Stock brokers */}
                        <div>
                            <p className="text-xs font-medium text-slate-500 mb-3 px-1">证券账户</p>
                            <div className="grid grid-cols-2 gap-3">
                                {stockBrokers.map((b) => (
                                    <BrokerCard key={b.id} broker={b} onClick={() => selectBroker(b)} />
                                ))}
                            </div>
                        </div>

                        {/* Fund platforms */}
                        <div>
                            <p className="text-xs font-medium text-slate-500 mb-3 px-1">基金平台</p>
                            <div className="grid grid-cols-2 gap-3">
                                {fundBrokers.map((b) => (
                                    <BrokerCard key={b.id} broker={b} onClick={() => selectBroker(b)} />
                                ))}
                            </div>
                        </div>

                        {/* Generic */}
                        <div>
                            <p className="text-xs font-medium text-slate-500 mb-3 px-1">其它方式</p>
                            <div className="grid grid-cols-2 gap-3">
                                {otherBrokers.map((b) => (
                                    <BrokerCard key={b.id} broker={b} onClick={() => selectBroker(b)} />
                                ))}
                            </div>
                        </div>

                        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
                            <div className="flex gap-2 mb-2">
                                <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
                                <p className="text-xs text-slate-400 leading-relaxed">
                                    <strong className="text-slate-300">关于账户关联：</strong>
                                    由于国内券商（招商、长江等）及支付宝不对个人投资者开放 API 接口，
                                    暂无法直接绑定账户。推荐方式：从 APP 导出持仓明细（CSV 文件），
                                    然后在此导入。导入只读取持仓数据，<strong className="text-green-400">不涉及任何账户密码</strong>。
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* ========= STEP: Upload ========= */}
                {step === 'upload' && selectedBroker && (
                    <div className="max-w-2xl mx-auto space-y-5">
                        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
                            <p className="text-sm font-medium text-white mb-1">
                                {selectedBroker.icon} {selectedBroker.name}
                            </p>
                            <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-line">
                                {selectedBroker.instructions}
                            </p>
                        </div>

                        {/* File upload */}
                        <div
                            onClick={() => fileRef.current?.click()}
                            className="flex flex-col items-center justify-center h-40 border-2 border-dashed border-slate-700
                         rounded-xl hover:border-blue-600/50 cursor-pointer transition-colors"
                        >
                            <Upload className="w-8 h-8 text-slate-600 mb-2" />
                            <p className="text-sm text-slate-400">
                                {fileContent ? '✓ 文件已加载，可重新选择' : '点击选择 CSV / TXT 文件'}
                            </p>
                            <p className="text-xs text-slate-600 mt-1">
                                支持 .csv .txt .tsv 格式
                            </p>
                            <input
                                ref={fileRef}
                                type="file"
                                accept=".csv,.txt,.tsv,.xls,.xlsx"
                                onChange={handleFile}
                                className="hidden"
                            />
                        </div>

                        {/* Or paste */}
                        <div>
                            <p className="text-xs text-slate-500 mb-2">或直接粘贴内容：</p>
                            <textarea
                                value={fileContent}
                                onChange={(e) => setFileContent(e.target.value)}
                                rows={8}
                                placeholder={
                                    selectedBroker.id === 'manual_json'
                                        ? '[{"ticker":"600519","shares":100,"cost_price":1500}]\n\n// 也支持无表头 CSV：\n000630,铜陵有色,1300,6.795'
                                        : selectedBroker.id === 'quick_paste'
                                        ? '000630,铜陵有色,1300,6.795\n600519,贵州茅台,100,1800\n159915,创业板ETF,5000,1.23'
                                        : '证券代码,证券名称,持仓数量,成本价\n600519,贵州茅台,100,1455.02'
                                }
                                className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-3
                           text-sm text-slate-300 font-mono resize-none
                           focus:outline-none focus:border-blue-600/50"
                            />
                        </div>

                        {error && (
                            <p className="text-sm text-red-400 flex items-center gap-1.5">
                                <XCircle className="w-4 h-4" /> {error}
                            </p>
                        )}

                        <button
                            onClick={doPreview}
                            disabled={loading || !fileContent.trim()}
                            className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium
                         hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
                         flex items-center justify-center gap-2"
                        >
                            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />}
                            解析预览
                        </button>
                    </div>
                )}

                {/* ========= STEP: Preview ========= */}
                {step === 'preview' && preview && (
                    <div className="max-w-3xl mx-auto space-y-5">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-white font-medium">
                                    解析到 {preview.holdings.length} 条持仓记录
                                </p>
                                <p className="text-xs text-slate-500">
                                    来源: {preview.broker} · 原始行数: {preview.raw_rows}
                                </p>
                            </div>
                            <div className="flex items-center gap-2">
                                <label className="text-xs text-slate-400">导入模式:</label>
                                <select
                                    value={importMode}
                                    onChange={(e) => setImportMode(e.target.value as 'merge' | 'replace')}
                                    className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1
                             text-xs text-slate-300 focus:outline-none"
                                >
                                    <option value="merge">合并 (追加到现有持仓)</option>
                                    <option value="replace">替换 (清空后导入)</option>
                                </select>
                            </div>
                        </div>

                        {preview.errors.length > 0 && (
                            <div className="bg-yellow-900/20 border border-yellow-800/30 rounded-xl p-3">
                                <p className="text-xs text-yellow-400 font-medium mb-1">解析警告:</p>
                                {preview.errors.map((e, i) => (
                                    <p key={i} className="text-xs text-yellow-500/70">{e}</p>
                                ))}
                            </div>
                        )}

                        <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
                            <table className="w-full">
                                <thead>
                                    <tr className="text-xs text-slate-500 border-b border-slate-800">
                                        <th className="text-left px-4 py-2.5 font-medium">代码</th>
                                        <th className="text-left px-4 py-2.5 font-medium">名称</th>
                                        <th className="text-right px-4 py-2.5 font-medium">数量</th>
                                        <th className="text-right px-4 py-2.5 font-medium">成本价</th>
                                        <th className="text-right px-4 py-2.5 font-medium">市值</th>
                                        <th className="text-right px-4 py-2.5 font-medium">类型</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {preview.holdings.map((h, i) => (
                                        <PreviewRow key={i} holding={h} />
                                    ))}
                                </tbody>
                            </table>
                        </div>

                        {error && (
                            <p className="text-sm text-red-400 flex items-center gap-1.5">
                                <XCircle className="w-4 h-4" /> {error}
                            </p>
                        )}

                        <div className="flex gap-3">
                            <button
                                onClick={() => setStep('upload')}
                                className="flex-1 py-2.5 rounded-xl border border-slate-700 text-slate-400 text-sm
                           hover:bg-slate-800"
                            >
                                返回修改
                            </button>
                            <button
                                onClick={doImport}
                                disabled={loading}
                                className="flex-1 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium
                           hover:bg-blue-500 disabled:opacity-50 flex items-center justify-center gap-2"
                            >
                                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                                确认导入 ({preview.holdings.length} 条)
                            </button>
                        </div>
                    </div>
                )}

                {/* ========= STEP: Manual Add ========= */}
                {step === 'manual_add' && (
                    <div className="max-w-md mx-auto space-y-5">
                        {/* Mode toggle */}
                        <div className="flex bg-slate-900 border border-slate-800 rounded-xl p-1">
                            <button
                                onClick={() => { setManualBatchMode(false); setError('') }}
                                className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                                    !manualBatchMode
                                        ? 'bg-blue-600 text-white'
                                        : 'text-slate-400 hover:text-white'
                                }`}
                            >
                                单条添加
                            </button>
                            <button
                                onClick={() => { setManualBatchMode(true); setError('') }}
                                className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                                    manualBatchMode
                                        ? 'bg-blue-600 text-white'
                                        : 'text-slate-400 hover:text-white'
                                }`}
                            >
                                批量粘贴
                            </button>
                        </div>

                        {!manualBatchMode ? (
                            <>
                                <div className="space-y-3">
                                    <div className="grid grid-cols-2 gap-3">
                                        <div>
                                            <label className="text-xs text-slate-500 mb-1 block">股票/基金代码</label>
                                            <input
                                                value={manualTicker}
                                                onChange={(e) => setManualTicker(e.target.value)}
                                                placeholder="如 600519"
                                                className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5
                                     text-sm text-white focus:outline-none focus:border-blue-600/50"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-slate-500 mb-1 block">名称（可选）</label>
                                            <input
                                                value={manualName}
                                                onChange={(e) => setManualName(e.target.value)}
                                                placeholder="如 贵州茅台"
                                                className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5
                                     text-sm text-white focus:outline-none focus:border-blue-600/50"
                                            />
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 gap-3">
                                        <div>
                                            <label className="text-xs text-slate-500 mb-1 block">持仓数量</label>
                                            <input
                                                type="number"
                                                value={manualShares}
                                                onChange={(e) => setManualShares(e.target.value)}
                                                placeholder="100"
                                                className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5
                                       text-sm text-white focus:outline-none focus:border-blue-600/50"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-slate-500 mb-1 block">成本价</label>
                                            <input
                                                type="number"
                                                step="0.01"
                                                value={manualCost}
                                                onChange={(e) => setManualCost(e.target.value)}
                                                placeholder="1500.00"
                                                className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5
                                       text-sm text-white focus:outline-none focus:border-blue-600/50"
                                            />
                                        </div>
                                    </div>
                                    <div>
                                        <label className="text-xs text-slate-500 mb-1 block">资产类型</label>
                                        <select
                                            value={manualType}
                                            onChange={(e) => setManualType(e.target.value)}
                                            className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5
                                     text-sm text-white focus:outline-none"
                                        >
                                            <option value="stock">股票</option>
                                            <option value="etf">ETF</option>
                                            <option value="mutual_fund">基金</option>
                                        </select>
                                    </div>
                                </div>

                                {error && (
                                    <p className="text-sm text-red-400 flex items-center gap-1.5">
                                        <XCircle className="w-4 h-4" /> {error}
                                    </p>
                                )}

                                <button
                                    onClick={doManualAdd}
                                    disabled={loading}
                                    className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium
                             hover:bg-blue-500 disabled:opacity-50 flex items-center justify-center gap-2"
                                >
                                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                                    添加到持仓
                                </button>
                            </>
                        ) : (
                            <>
                                <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                                    <p className="text-xs text-slate-400 leading-relaxed">
                                        每行一条，格式：<span className="font-mono text-slate-300">代码,名称,数量,成本价</span>
                                        <br />
                                        名称可省略：<span className="font-mono text-slate-300">代码,数量,成本价</span>
                                    </p>
                                </div>
                                <textarea
                                    value={manualBatchText}
                                    onChange={(e) => setManualBatchText(e.target.value)}
                                    rows={10}
                                    placeholder={`000630,铜陵有色,1300,6.795\n600519,贵州茅台,100,1800\n159915,创业板ETF,5000,1.23`}
                                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-3
                             text-sm text-slate-300 font-mono resize-none
                             focus:outline-none focus:border-blue-600/50"
                                />

                                {error && (
                                    <p className="text-sm text-red-400 flex items-center gap-1.5">
                                        <XCircle className="w-4 h-4" /> {error}
                                    </p>
                                )}

                                <button
                                    onClick={doManualBatchPreview}
                                    disabled={loading || !manualBatchText.trim()}
                                    className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium
                             hover:bg-blue-500 disabled:opacity-50 flex items-center justify-center gap-2"
                                >
                                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileUp className="w-4 h-4" />}
                                    解析预览
                                </button>
                            </>
                        )}
                    </div>
                )}

                {/* ========= STEP: Done ========= */}
                {step === 'done' && result && (
                    <div className="max-w-md mx-auto flex flex-col items-center py-12">
                        <CheckCircle2 className="w-14 h-14 text-green-400 mb-4" />
                        <p className="text-lg font-bold text-white mb-2">导入完成</p>
                        <div className="text-sm text-slate-400 space-y-1 text-center">
                            {result.added > 0 && <p>新增 {result.added} 条持仓</p>}
                            {result.updated > 0 && <p>更新 {result.updated} 条已有持仓</p>}
                            {result.skipped > 0 && <p>跳过 {result.skipped} 条无效记录</p>}
                        </div>
                        <div className="flex gap-3 mt-6">
                            <button
                                onClick={reset}
                                className="px-5 py-2 rounded-xl border border-slate-700 text-slate-400 text-sm
                           hover:bg-slate-800"
                            >
                                继续导入
                            </button>
                            <button
                                onClick={() => navigate('/portfolio')}
                                className="px-5 py-2 rounded-xl bg-blue-600 text-white text-sm font-medium
                           hover:bg-blue-500"
                            >
                                查看持仓
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function BrokerCard({ broker, onClick }: { broker: BrokerInfo; onClick: () => void }) {
    return (
        <button
            onClick={onClick}
            className="flex items-center gap-3 p-3.5 bg-slate-900 border border-slate-800 rounded-xl
                 hover:border-blue-600/40 hover:bg-slate-800/50 transition-colors text-left"
        >
            <span className="text-xl">{broker.icon}</span>
            <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{broker.name}</p>
                {broker.format && (
                    <p className="text-xs text-slate-500">{broker.format} 导入</p>
                )}
            </div>
            <ChevronRight className="w-3.5 h-3.5 text-slate-600 flex-shrink-0" />
        </button>
    )
}

function PreviewRow({ holding }: { holding: ImportedHolding }) {
    const typeMap: Record<string, string> = {
        stock: '股票',
        etf: 'ETF',
        mutual_fund: '基金',
    }
    return (
        <tr className="border-b border-slate-800/50 last:border-0">
            <td className="px-4 py-2.5 text-sm font-mono text-blue-400">{holding.ticker}</td>
            <td className="px-4 py-2.5 text-sm text-white">{holding.name || '-'}</td>
            <td className="text-right px-4 py-2.5 text-sm font-mono text-slate-300">
                {holding.shares.toLocaleString()}
            </td>
            <td className="text-right px-4 py-2.5 text-sm font-mono text-slate-400">
                {holding.cost_price > 0 ? holding.cost_price.toFixed(3) : '-'}
            </td>
            <td className="text-right px-4 py-2.5 text-sm font-mono text-slate-400">
                {holding.market_value > 0 ? holding.market_value.toFixed(2) : '-'}
            </td>
            <td className="text-right px-4 py-2.5 text-xs text-slate-500">
                {typeMap[holding.asset_type] ?? holding.asset_type}
            </td>
        </tr>
    )
}
