import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '@/store/useAuth'
import LoginPage from '@/pages/Login'
import MainLayout from '@/components/MainLayout'
import MonitorPage from '@/pages/Monitor'
import StockDetailPage from '@/pages/StockDetail'
import WatchlistPage from '@/pages/WatchlistPage'
import PortfolioPage from '@/pages/Portfolio'
import BrokerImportPage from '@/pages/BrokerImport'

function RequireAuth({ children }: { children: React.ReactNode }) {
    const user = useAuth((s) => s.user)
    if (!user) return <Navigate to="/login" replace />
    return <>{children}</>
}

export default function App() {
    const hydrate = useAuth((s) => s.hydrate)

    useEffect(() => {
        hydrate()
    }, [])

    return (
        <BrowserRouter>
            <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route
                    path="/"
                    element={
                        <RequireAuth>
                            <MainLayout />
                        </RequireAuth>
                    }
                >
                    <Route index element={<Navigate to="/monitor" replace />} />
                    <Route path="monitor" element={<MonitorPage />} />
                    <Route path="stock/:ticker" element={<StockDetailPage />} />
                    <Route path="watchlist" element={<WatchlistPage />} />
                    <Route path="portfolio" element={<PortfolioPage />} />
                    <Route path="portfolio/import" element={<BrokerImportPage />} />
                </Route>
            </Routes>
        </BrowserRouter>
    )
}
