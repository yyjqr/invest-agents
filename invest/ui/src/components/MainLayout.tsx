import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '@/store/useAuth'
import {
  Activity, BarChart2, BookOpen, LogOut, TrendingUp, User, Wallet,
} from 'lucide-react'

const NAV_ITEMS = [
  { to: '/monitor',   icon: Activity,   label: '实时监控' },
  { to: '/watchlist', icon: BookOpen,   label: '自选股'   },
  { to: '/portfolio', icon: Wallet,     label: '我的持仓' },
]

export default function MainLayout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-slate-900 border-r border-slate-800
                        flex flex-col py-4">
        {/* Brand */}
        <div className="flex items-center gap-2.5 px-5 mb-6">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
            <TrendingUp className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-white text-sm">投资大师</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2 space-y-0.5">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all
                ${isActive
                  ? 'bg-blue-600/20 text-blue-400 border border-blue-600/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800'}`
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* User info */}
        <div className="px-3 pt-4 border-t border-slate-800 mt-auto">
          <div className="flex items-center gap-2.5 px-2 mb-3">
            <div className="w-7 h-7 rounded-full bg-blue-700 flex items-center justify-center">
              <User className="w-3.5 h-3.5 text-white" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-white truncate">
                {user?.display_name ?? user?.sub}
              </p>
              <p className="text-[10px] text-slate-500 truncate">{user?.sub}</p>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-xl text-sm
                       text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <LogOut className="w-4 h-4" />
            退出登录
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
