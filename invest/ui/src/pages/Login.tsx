import { useState, useRef, useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/store/useAuth'
import { TrendingUp, Lock, User as UserIcon, Eye, EyeOff, ChevronDown, X } from 'lucide-react'

const STORAGE_KEY = 'invest_saved_usernames'

function loadSavedUsernames(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as string[]) : ['admin']
  } catch {
    return ['admin']
  }
}

function saveSavedUsernames(names: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(names))
  } catch {}
}

export default function LoginPage() {
    const { login, register, user, isLoading } = useAuth()
    const [mode, setMode] = useState<'login' | 'register'>('login')
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [displayName, setDisplayName] = useState('')
    const [showPwd, setShowPwd] = useState(false)
    const [error, setError] = useState('')
    const [savedUsernames, setSavedUsernames] = useState<string[]>(loadSavedUsernames)
    const [showDropdown, setShowDropdown] = useState(false)
    const usernameRef = useRef<HTMLInputElement>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)

    // 点击外部关闭下拉
    useEffect(() => {
      const handler = (e: MouseEvent) => {
        if (
          dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
          usernameRef.current && !usernameRef.current.contains(e.target as Node)
        ) {
          setShowDropdown(false)
        }
      }
      document.addEventListener('mousedown', handler)
      return () => document.removeEventListener('mousedown', handler)
    }, [])

    if (user) return <Navigate to="/monitor" replace />

    // 登录成功后保存用户名
    const persistUsername = (name: string) => {
      if (!name.trim()) return
      const updated = [name, ...savedUsernames.filter((u) => u !== name)].slice(0, 8)
      setSavedUsernames(updated)
      saveSavedUsernames(updated)
    }

    // 移除某个保存的用户名
    const removeUsername = (name: string, e: React.MouseEvent) => {
      e.stopPropagation()
      const updated = savedUsernames.filter((u) => u !== name)
      setSavedUsernames(updated)
      saveSavedUsernames(updated)
    }

    // 从下拉选择用户名
    const selectUsername = (name: string) => {
      setUsername(name)
      setShowDropdown(false)
      // 自动聚焦密码框
      setTimeout(() => {
        const pwd = document.querySelector<HTMLInputElement>('input[type="password"], input[name="password"]')
        pwd?.focus()
      }, 50)
    }

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        setError('')
        try {
            if (mode === 'login') {
                await login(username, password)
                persistUsername(username)
            } else {
                if (!displayName.trim()) { setError('请填写昵称'); return }
                await register(username, password, displayName)
                persistUsername(username)
            }
        } catch (err: any) {
            setError(err.message ?? '操作失败，请重试')
        }
    }

    // 下拉候选：与当前输入匹配的已保存用户名
    const dropdownItems = savedUsernames.filter((u) =>
      !username || u.toLowerCase().includes(username.toLowerCase())
    )

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900
                    flex items-center justify-center p-4">
            <div className="w-full max-w-md">
                {/* Logo */}
                <div className="text-center mb-8">
                    <div className="inline-flex items-center justify-center w-16 h-16
                          rounded-2xl bg-blue-600 mb-4 shadow-lg shadow-blue-600/30">
                        <TrendingUp className="w-8 h-8 text-white" />
                    </div>
                    <h1 className="text-3xl font-bold text-white">投资大师</h1>
                    <p className="text-slate-400 mt-1 text-sm">实时股票分析 · 量化策略 · 智能预测</p>
                </div>

                {/* Card */}
                <div className="bg-slate-800/70 backdrop-blur border border-slate-700 rounded-2xl p-8 shadow-xl">
                    {/* Tab switcher */}
                    <div className="flex rounded-xl bg-slate-900/60 p-1 mb-6">
                        {(['login', 'register'] as const).map((m) => (
                            <button
                                key={m}
                                onClick={() => { setMode(m); setError('') }}
                                className={`flex-1 py-2 text-sm font-medium rounded-lg transition-all
                  ${mode === m
                                        ? 'bg-blue-600 text-white shadow'
                                        : 'text-slate-400 hover:text-white'}`}
                            >
                                {m === 'login' ? '登录' : '注册新账号'}
                            </button>
                        ))}
                    </div>

                    <form onSubmit={handleSubmit} className="space-y-4">
                        {/* Display name (register only) */}
                        {mode === 'register' && (
                            <div>
                                <label className="text-sm text-slate-300 mb-1.5 block">昵称</label>
                                <div className="relative">
                                    <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                    <input
                                        value={displayName}
                                        onChange={(e) => setDisplayName(e.target.value)}
                                        placeholder="你的显示名称"
                                        className="w-full pl-10 pr-4 py-2.5 bg-slate-900/60 border border-slate-600
                               rounded-xl text-white placeholder-slate-500 focus:border-blue-500
                               focus:ring-1 focus:ring-blue-500 outline-none text-sm"
                                    />
                                </div>
                            </div>
                        )}

                        {/* Username — 带历史下拉 */}
                        <div>
                            <label className="text-sm text-slate-300 mb-1.5 block">用户名</label>
                            <div className="relative">
                                <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 z-10" />
                                <input
                                    ref={usernameRef}
                                    value={username}
                                    onChange={(e) => { setUsername(e.target.value); setShowDropdown(true) }}
                                    onFocus={() => setShowDropdown(dropdownItems.length > 0)}
                                    placeholder="输入或选择用户名"
                                    autoComplete="off"
                                    required
                                    className="w-full pl-10 pr-8 py-2.5 bg-slate-900/60 border border-slate-600
                             rounded-xl text-white placeholder-slate-500 focus:border-blue-500
                             focus:ring-1 focus:ring-blue-500 outline-none text-sm"
                                />
                                {/* 展开/收起按钮 */}
                                {savedUsernames.length > 0 && (
                                  <button
                                    type="button"
                                    onClick={() => setShowDropdown((v) => !v)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                                  >
                                    <ChevronDown className={`w-4 h-4 transition-transform ${showDropdown ? 'rotate-180' : ''}`} />
                                  </button>
                                )}

                                {/* 下拉列表 */}
                                {showDropdown && dropdownItems.length > 0 && (
                                  <div
                                    ref={dropdownRef}
                                    className="absolute top-full left-0 right-0 mt-1 bg-slate-800 border border-slate-700
                                               rounded-xl shadow-xl z-50 overflow-hidden"
                                  >
                                    <p className="text-[10px] text-slate-500 px-3 pt-2 pb-1">历史登录账号</p>
                                    {dropdownItems.map((name) => (
                                      <div
                                        key={name}
                                        onClick={() => selectUsername(name)}
                                        className="flex items-center gap-2 px-3 py-2 hover:bg-slate-700/60
                                                   cursor-pointer transition-colors group"
                                      >
                                        <UserIcon className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                                        <span className="flex-1 text-sm text-slate-200">{name}</span>
                                        <button
                                          type="button"
                                          onClick={(e) => removeUsername(name, e)}
                                          className="opacity-0 group-hover:opacity-100 text-slate-500
                                                     hover:text-red-400 transition-all"
                                          title="移除记录"
                                        >
                                          <X className="w-3.5 h-3.5" />
                                        </button>
                                      </div>
                                    ))}
                                  </div>
                                )}
                            </div>
                        </div>

                        {/* Password */}
                        <div>
                            <label className="text-sm text-slate-300 mb-1.5 block">密码</label>
                            <div className="relative">
                                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                <input
                                    type={showPwd ? 'text' : 'password'}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="输入密码"
                                    autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                                    required
                                    className="w-full pl-10 pr-10 py-2.5 bg-slate-900/60 border border-slate-600
                             rounded-xl text-white placeholder-slate-500 focus:border-blue-500
                             focus:ring-1 focus:ring-blue-500 outline-none text-sm"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowPwd(!showPwd)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                                >
                                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                </button>
                            </div>
                        </div>

                        {/* Error */}
                        {error && (
                            <div className="text-red-400 text-sm bg-red-950/40 border border-red-800 rounded-lg px-3 py-2">
                                {error}
                            </div>
                        )}

                        {/* Submit */}
                        <button
                            type="submit"
                            disabled={isLoading}
                            className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-60
                         text-white font-semibold rounded-xl transition-colors shadow-lg
                         shadow-blue-600/20 mt-2"
                        >
                            {isLoading ? '处理中...' : mode === 'login' ? '登录' : '注册并登录'}
                        </button>
                    </form>

                    {/* Hint */}
                    <p className="text-xs text-slate-500 text-center mt-4">
                        默认测试账号：<code className="text-slate-400">admin / admin123</code>
                    </p>
                </div>
            </div>
        </div>
    )
}
