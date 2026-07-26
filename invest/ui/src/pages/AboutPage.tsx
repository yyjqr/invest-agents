import { useEffect, useState } from 'react'
import { fetchAbout } from '@/api/client'
import type { AboutInfo } from '@/types'
import { TrendingUp } from 'lucide-react'

export default function AboutPage() {
  const [info, setInfo] = useState<AboutInfo | null>(null)

  useEffect(() => {
    fetchAbout().then(setInfo).catch(() => {})
  }, [])

  return (
    <div className="min-h-full flex items-center justify-center p-8">
      <div className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl p-8 space-y-6">
        {/* Logo + Name */}
        <div className="flex flex-col items-center gap-3">
          <div className="w-14 h-14 rounded-2xl bg-blue-600 flex items-center justify-center">
            <TrendingUp className="w-7 h-7 text-white" />
          </div>
          <h1 className="text-xl font-bold text-white">
            {info?.name ?? '投资大师 (InvestMaster)'}
          </h1>
          <span className="text-xs text-slate-500 bg-slate-800 px-3 py-1 rounded-full">
            版本 {info?.version ?? '—'}
          </span>
        </div>

        {/* Description */}
        <p className="text-sm text-slate-400 text-center leading-relaxed">
          {info?.description ?? '基于经验 + AI 开发的智能投资分析工具'}
        </p>

        {/* Disclaimer */}
        <div className="bg-yellow-950/30 border border-yellow-900/40 rounded-xl p-4">
          <h2 className="text-xs font-semibold text-yellow-400 mb-2">免责声明</h2>
          <p className="text-xs text-yellow-200/70 leading-relaxed">
            {info?.disclaimer ??
              '本软件仅供学习与研究参考，不构成任何投资建议。投资有风险，理财需谨慎。'}
          </p>
        </div>

        {/* Dev info */}
        <div className="text-center space-y-1">
          <p className="text-[11px] text-slate-500">基于经验 + AI 协作开发</p>
          <p className="text-[11px] text-slate-600">
            {info?.copyright ?? '© 2024-2026 InvestMaster. All rights reserved.'}
          </p>
        </div>
      </div>
    </div>
  )
}
