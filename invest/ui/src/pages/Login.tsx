import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/store/useAuth'
import { TrendingUp, Lock, User as UserIcon, Eye, EyeOff } from 'lucide-react'

export default function LoginPage() {
    const { login, register, user, isLoading } = useAuth()
    const [mode, setMode] = useState<'login' | 'register'>('login')
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [displayName, setDisplayName] = useState('')
    const [showPwd, setShowPwd] = useState(false)
    const [error, setError] = useState('')

    if (user) return <Navigate to="/monitor" replace />

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        setError('')
        try {
            if (mode === 'login') {
                await login(username, password)
            } else {
                if (!displayName.trim()) { setError('请填写昵称'); return }
                await register(username, password, displayName)
            }
        } catch (err: any) {
            setError(err.message ?? '操作失败，请重试')
        }
    }

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

                        {/* Username */}
                        <div>
                            <label className="text-sm text-slate-300 mb-1.5 block">用户名</label>
                            <div className="relative">
                                <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                <input
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder="输入用户名"
                                    autoComplete="username"
                                    required
                                    className="w-full pl-10 pr-4 py-2.5 bg-slate-900/60 border border-slate-600
                             rounded-xl text-white placeholder-slate-500 focus:border-blue-500
                             focus:ring-1 focus:ring-blue-500 outline-none text-sm"
                                />
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
