/**
 * API client — thin wrappers around the FastAPI backend at http://127.0.0.1:8765
 * All authenticated requests include the JWT Bearer token from localStorage.
 */

import type {
  AboutInfo,
  AppSettings,
  Forecast,
  ForecastRecord,
  IndustryPicksResponse,
  MarketIndex,
  NewsAnalysis,
  NewsItem,
  OHLCV,
  PriceAlert,
  Quote,
  SearchResult,
  SectorInfo,
  SmartPicksResponse,
  StockProfile,
  StrategyReport,
  WatchlistItem,
} from '@/types'

// In dev mode Vite proxies /api and /ws to the FastAPI backend (same-origin, no CORS).
// In production (packaged Tauri app) the webview origin is tauri://localhost so we
// need the full URL to reach the local Python server.
const isDev = import.meta.env.DEV
export const API_BASE = isDev ? '' : 'http://127.0.0.1:8765'
export const WS_BASE  = isDev
  ? `ws://${window.location.host}`
  : 'ws://127.0.0.1:8765'

const HEALTH_URL = `${API_BASE}/health`
const BACKEND_START_TIMEOUT_MS = 50000
const BACKEND_POLL_INTERVAL_MS = 300

let backendReadyPromise: Promise<void> | null = null

// ---------------------------------------------------------------------------
// Auth token helpers
// ---------------------------------------------------------------------------

export function getToken(): string | null {
  return localStorage.getItem('invest_token')
}

export function setToken(token: string) {
  localStorage.setItem('invest_token', token)
}

export function clearToken() {
  localStorage.removeItem('invest_token')
}

async function sleep(ms: number) {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

async function probeBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(HEALTH_URL, { method: 'GET' })
    if (!res.ok) return false
    // Must verify the build marker so we never talk to a stale orphan server
    // left behind from a previous version (which has /health but lacks newer
    // API routes, causing 404 on /api/sectors etc.).
    const data = await res.json()
    return data?.build === 1
  } catch {
    return false
  }
}

async function waitForBackendReady(force = false): Promise<void> {
  if (isDev) return

  if (!force && backendReadyPromise) {
    return backendReadyPromise
  }

  backendReadyPromise = (async () => {
    const startedAt = Date.now()

    while (Date.now() - startedAt < BACKEND_START_TIMEOUT_MS) {
      if (await probeBackendHealth()) {
        return
      }
      await sleep(BACKEND_POLL_INTERVAL_MS)
    }

    throw new Error('本地后端服务未就绪，请稍等几秒后重试。如果持续失败，请检查安装目录下的 python-server.log 文件以获取错误详情。')
  })()

  try {
    await backendReadyPromise
  } catch (error) {
    backendReadyPromise = null
    throw error
  }
}

// ---------------------------------------------------------------------------
// Fetch wrapper
// ---------------------------------------------------------------------------

async function _fetch<T>(
  path: string,
  options: RequestInit = {},
  auth = true,
): Promise<T> {
  await waitForBackendReady()

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (auth) {
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  } catch {
    await waitForBackendReady(true)
    try {
      res = await fetch(`${API_BASE}${path}`, { ...options, headers })
    } catch {
      throw new Error('无法连接本地服务，请确认 exe 已成功启动后端，或检查 127.0.0.1:8765 是否被安全软件拦截。')
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(
  username: string,
  password: string,
): Promise<{ access_token: string; user: { username: string; display_name: string } }> {
  return _fetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }, false)
}

export async function register(
  username: string,
  password: string,
  display_name: string,
): Promise<{ access_token: string; user: { username: string; display_name: string } }> {
  return _fetch('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password, display_name }),
  }, false)
}

export async function fetchMe() {
  return _fetch<{ sub: string; display_name: string }>('/api/auth/me')
}

// ---------------------------------------------------------------------------
// Stocks
// ---------------------------------------------------------------------------

export async function searchStocks(q: string): Promise<SearchResult[]> {
  return _fetch(`/api/stocks/search?q=${encodeURIComponent(q)}`)
}

export async function fetchQuote(ticker: string): Promise<Quote> {
  return _fetch(`/api/stocks/quote/${ticker}`)
}

export async function fetchBatchQuotes(tickers: string[]): Promise<Quote[]> {
  return _fetch('/api/stocks/batch-quotes', {
    method: 'POST',
    body: JSON.stringify({ tickers }),
  })
}

export async function fetchHistory(ticker: string, period = '3mo'): Promise<OHLCV[]> {
  return _fetch(`/api/stocks/history/${ticker}?period=${period}`)
}

export async function fetchIntraday(ticker: string): Promise<OHLCV[]> {
  return _fetch(`/api/stocks/intraday/${ticker}`)
}

export async function fetchStockProfile(ticker: string): Promise<StockProfile> {
  return _fetch(`/api/stocks/profile/${encodeURIComponent(ticker)}`)
}

export async function fetchValueAnalysis(ticker: string): Promise<import('../types').ValueAnalysis> {
  return _fetch(`/api/stocks/value-analysis/${encodeURIComponent(ticker)}`)
}

export async function fetchWatchlistRanked(): Promise<import('../types').WatchlistRanked> {
  return _fetch('/api/watchlist/ranked')
}

// ---------------------------------------------------------------------------
// Strategy & Forecast
// ---------------------------------------------------------------------------

export async function fetchStrategy(ticker: string): Promise<StrategyReport> {
  return _fetch(`/api/strategy/${ticker}`)
}

export async function fetchForecast(
  ticker: string,
  horizon: '1h' | '1d' | '1w' | '1m' | '3m' = '1d',
  newsSentiment?: string,
  newsScore?: number,
): Promise<Forecast> {
  const params = new URLSearchParams({ horizon })
  if (newsSentiment) params.set('news_sentiment', newsSentiment)
  if (newsScore != null && newsScore > 0) params.set('news_score', String(newsScore))
  return _fetch(`/api/forecast/${encodeURIComponent(ticker)}?${params}`)
}

// ---------------------------------------------------------------------------
// Forecast history
// ---------------------------------------------------------------------------

export async function fetchForecastHistory(): Promise<ForecastRecord[]> {
  return _fetch('/api/forecast-history')
}

export async function saveForecastRecord(record: ForecastRecord): Promise<ForecastRecord[]> {
  return _fetch('/api/forecast-history', {
    method: 'POST',
    body: JSON.stringify(record),
  })
}

export async function deleteForecastRecord(index: number): Promise<ForecastRecord[]> {
  return _fetch(`/api/forecast-history/${index}`, { method: 'DELETE' })
}

export async function clearForecastHistory(): Promise<ForecastRecord[]> {
  return _fetch('/api/forecast-history', { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Saved articles (bookmarks)
// ---------------------------------------------------------------------------

export async function fetchSavedArticles() {
  return _fetch<import('../types').SavedArticle[]>('/api/saved-articles')
}

export async function saveArticle(article: Omit<import('../types').SavedArticle, 'saved_at'>) {
  return _fetch<import('../types').SavedArticle[]>('/api/saved-articles', {
    method: 'POST',
    body: JSON.stringify(article),
  })
}

export async function deleteSavedArticle(index: number) {
  return _fetch<import('../types').SavedArticle[]>(`/api/saved-articles/${index}`, { method: 'DELETE' })
}

export async function clearSavedArticles() {
  return _fetch<import('../types').SavedArticle[]>('/api/saved-articles', { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// About
// ---------------------------------------------------------------------------

export async function fetchAbout(): Promise<AboutInfo> {
  return _fetch('/api/about')
}

// ---------------------------------------------------------------------------
// Smart Picks
// ---------------------------------------------------------------------------

export async function fetchSmartPicks(): Promise<SmartPicksResponse> {
  return _fetch('/api/smart-picks')
}

export async function refreshSmartPicks(): Promise<SmartPicksResponse> {
  return _fetch('/api/smart-picks/refresh', { method: 'POST' })
}

// ---------------------------------------------------------------------------
// Watchlist
// ---------------------------------------------------------------------------

export async function fetchWatchlist(): Promise<WatchlistItem[]> {
  return _fetch('/api/watchlist')
}

export async function addToWatchlist(ticker: string, name = '', note = '') {
  return _fetch<WatchlistItem[]>('/api/watchlist', {
    method: 'POST',
    body: JSON.stringify({ ticker, name, note }),
  })
}

export async function removeFromWatchlist(ticker: string) {
  return _fetch<WatchlistItem[]>(`/api/watchlist/${ticker}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Monitor list
// ---------------------------------------------------------------------------

export async function fetchMonitor(): Promise<string[]> {
  return _fetch('/api/monitor')
}

export async function updateMonitor(tickers: string[]) {
  return _fetch<string[]>('/api/monitor', {
    method: 'PUT',
    body: JSON.stringify({ tickers }),
  })
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export async function fetchSettings() {
  return _fetch<AppSettings>('/api/settings')
}

export async function updateSettings(settings: Partial<AppSettings & { qveris_key: string }>) {
  return _fetch('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  })
}

// ---------------------------------------------------------------------------
// Portfolio — import & management
// ---------------------------------------------------------------------------

export interface BrokerInfo {
  id: string
  name: string
  icon: string
  type: string
  format?: string
  instructions: string
}

export interface ImportedHolding {
  ticker: string
  name: string
  shares: number
  cost_price: number
  current_price: number
  market_value: number
  pnl: number
  asset_type: string
}

export interface ImportPreviewResult {
  broker: string
  raw_rows: number
  holdings: ImportedHolding[]
  errors: string[]
}

export interface ImportExecuteResult {
  ok: boolean
  summary: { added: number; updated: number; skipped: number; total: number }
  errors: string[]
}

export async function fetchBrokers(): Promise<BrokerInfo[]> {
  return _fetch('/api/portfolio/brokers')
}

export async function fetchPortfolio() {
  return _fetch<{ holdings: Record<string, { ticker: string; shares: number; avg_cost_basis: number; asset_type: string }>; cash: number }>('/api/portfolio')
}

export async function importPreview(broker: string, content: string): Promise<ImportPreviewResult> {
  return _fetch('/api/portfolio/import/preview', {
    method: 'POST',
    body: JSON.stringify({ broker, content }),
  })
}

export async function importExecute(broker: string, content: string, mode = 'merge'): Promise<ImportExecuteResult> {
  return _fetch('/api/portfolio/import', {
    method: 'POST',
    body: JSON.stringify({ broker, content, mode }),
  })
}

export async function addPortfolioHolding(ticker: string, shares: number, cost_price: number, asset_type = 'stock') {
  return _fetch<{ ok: boolean; ticker: string }>('/api/portfolio/add', {
    method: 'POST',
    body: JSON.stringify({ ticker, shares, cost_price, asset_type }),
  })
}

export async function deletePortfolioHolding(ticker: string) {
  return _fetch<{ ok: boolean }>(`/api/portfolio/${encodeURIComponent(ticker)}`, {
    method: 'DELETE',
  })
}

export async function sellPortfolioHolding(ticker: string, shares: number, price: number) {
  return _fetch<{ ok: boolean; remaining_shares: number }>(
    `/api/portfolio/${encodeURIComponent(ticker)}/sell`,
    { method: 'POST', body: JSON.stringify({ shares, price }) },
  )
}

export async function editPortfolioHolding(ticker: string, shares: number, avg_cost_basis: number) {
  return _fetch<{ ok: boolean }>(
    `/api/portfolio/${encodeURIComponent(ticker)}/edit`,
    { method: 'PATCH', body: JSON.stringify({ shares, avg_cost_basis }) },
  )
}

// ---------------------------------------------------------------------------
// News
// ---------------------------------------------------------------------------

export type NewsCategory = 'cn' | 'research' | 'global' | 'all'

export async function fetchNews(category: NewsCategory = 'cn', n = 15): Promise<NewsItem[]> {
  return _fetch(`/api/news?category=${category}&n=${n}`)
}

export async function fetchResearchReports(params: {
  ticker?: string
  sector?: string
  query?: string
  limit?: number
} = {}): Promise<NewsItem[]> {
  const { ticker = '', sector = '', query = '', limit = 10 } = params
  const qs = new URLSearchParams()
  if (ticker) qs.set('ticker', ticker)
  if (sector) qs.set('sector', sector)
  if (query) qs.set('query', query)
  qs.set('limit', String(limit))
  return _fetch(`/api/research-reports?${qs.toString()}`)
}

// ---------------------------------------------------------------------------
// Sectors
// ---------------------------------------------------------------------------

export async function fetchSectors(): Promise<SectorInfo[]> {
  return _fetch('/api/sectors')
}

export async function fetchIndices(): Promise<MarketIndex[]> {
  return _fetch('/api/indices')
}

export async function fetchIndustryPicks(perIndustry = 5): Promise<IndustryPicksResponse> {
  return _fetch(`/api/industry-picks?picks=${perIndustry}`)
}

// ---------------------------------------------------------------------------
// Price alerts
// ---------------------------------------------------------------------------

export async function fetchAlerts(): Promise<Record<string, PriceAlert>> {
  return _fetch('/api/alerts')
}

export async function setAlert(
  ticker: string,
  rule: { alert_high: number | null; alert_low: number | null },
): Promise<Record<string, PriceAlert>> {
  return _fetch(`/api/alerts/${encodeURIComponent(ticker)}`, {
    method: 'PUT',
    body: JSON.stringify(rule),
  })
}

export async function deleteAlert(ticker: string): Promise<Record<string, PriceAlert>> {
  return _fetch(`/api/alerts/${encodeURIComponent(ticker)}`, { method: 'DELETE' })
}

export async function fetchNewsAnalysis(payload: {
  category: NewsCategory
  focus?: string
  company?: string
  sector?: string
  ticker?: string
  items: NewsItem[]
}): Promise<NewsAnalysis> {
  return _fetch('/api/news/analyze', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ---------------------------------------------------------------------------
// WebSocket — realtime price stream
// ---------------------------------------------------------------------------

export type PriceUpdateHandler = (quotes: Quote[]) => void

export class PriceWebSocket {
  private ws: WebSocket | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private tickers: string[] = []
  private onPrices: PriceUpdateHandler
  private closed = false

  constructor(tickers: string[], onPrices: PriceUpdateHandler) {
    this.tickers = tickers
    this.onPrices = onPrices
    this._connect()
  }

  private _connect() {
    if (this.closed) return
    this.ws = new WebSocket(`${WS_BASE}/ws/prices`)

    this.ws.onopen = () => {
      const token = getToken() ?? ''
      this.ws!.send(JSON.stringify({ token, tickers: this.tickers }))
    }

    this.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'prices') {
          this.onPrices(msg.data as Quote[])
        }
      } catch {}
    }

    this.ws.onclose = () => {
      if (!this.closed) {
        this.reconnectTimer = setTimeout(() => this._connect(), 5000)
      }
    }
  }

  updateTickers(tickers: string[]) {
    this.tickers = tickers
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'update_tickers', tickers }))
    }
  }

  close() {
    this.closed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
  }
}

// ---------------------------------------------------------------------------
// Trading / Paper trading API
// ---------------------------------------------------------------------------

import type {
  OrderRecord,
  OrderSubmitResult,
  PaperAccountSummary,
  RiskConfig,
} from '@/types'

export async function submitOrder(payload: {
  ticker: string
  name?: string
  action: 'BUY' | 'SELL'
  quantity: number
  price: number
  mode?: 'paper' | 'easytrader' | 'manual'
  note?: string
}): Promise<OrderSubmitResult> {
  return _fetch('/api/trade/order', {
    method: 'POST',
    body: JSON.stringify({ mode: 'paper', name: '', note: '', ...payload }),
  })
}

export async function fetchOrders(params?: {
  mode?: string
  status?: string
  limit?: number
}): Promise<OrderRecord[]> {
  const q = new URLSearchParams()
  if (params?.mode) q.set('mode', params.mode)
  if (params?.status) q.set('status', params.status)
  if (params?.limit) q.set('limit', String(params.limit))
  const qs = q.toString()
  return _fetch(`/api/trade/orders${qs ? '?' + qs : ''}`)
}

export async function cancelOrder(orderId: string): Promise<{ order_id: string; status: string }> {
  return _fetch(`/api/trade/orders/${encodeURIComponent(orderId)}/cancel`, { method: 'PUT' })
}

export async function markOrderFilled(
  orderId: string,
  fillPrice: number,
  fillQty?: number,
): Promise<{ order_id: string; status: string; fill_price: number }> {
  const q = new URLSearchParams({ fill_price: String(fillPrice) })
  if (fillQty) q.set('fill_qty', String(fillQty))
  return _fetch(`/api/trade/orders/${encodeURIComponent(orderId)}/fill?${q}`, { method: 'PUT' })
}

export async function fetchPaperPositions(): Promise<PaperAccountSummary> {
  return _fetch('/api/trade/positions')
}

export async function resetPaperAccount(): Promise<{ message: string; cash: number }> {
  return _fetch('/api/trade/positions/reset', { method: 'POST' })
}

export async function fetchRiskConfig(): Promise<RiskConfig> {
  return _fetch('/api/trade/risk-config')
}

export async function updateRiskConfig(cfg: RiskConfig): Promise<RiskConfig> {
  return _fetch('/api/trade/risk-config', {
    method: 'PUT',
    body: JSON.stringify(cfg),
  })
}

// ---------------------------------------------------------------------------
// Backtest (agent prediction accuracy)
// ---------------------------------------------------------------------------

export interface AccuracyStats {
  total: number
  wins: number
  losses: number
  draws: number
  pending: number
  win_rate: number
  avg_predicted_return: number
  avg_actual_return: number
  calibration_error: number
  by_confidence: Record<string, { total: number; wins: number; win_rate: number }>
  by_verdict: Record<string, { total: number; wins: number; win_rate: number }>
}

export interface PredictionRecord {
  id: string
  ticker: string
  name: string
  timestamp: number
  horizon: string
  verdict: string
  confidence: string
  score: number
  entry_price: number
  predicted_return_pct: number
  actual_price: number | null
  actual_return_pct: number | null
  outcome: string
  checked_at: number | null
}

export async function fetchAccuracyStats(ticker?: string, lastN = 100): Promise<AccuracyStats> {
  const p = new URLSearchParams()
  if (ticker) p.set('ticker', ticker)
  p.set('last_n', String(lastN))
  return _fetch(`/api/agent/accuracy?${p}`)
}

export async function fetchPredictions(ticker?: string, limit = 50): Promise<PredictionRecord[]> {
  const p = new URLSearchParams()
  if (ticker) p.set('ticker', ticker)
  p.set('limit', String(limit))
  return _fetch(`/api/agent/predictions?${p}`)
}

export async function triggerCheckPredictions(): Promise<{ checked: number }> {
  return _fetch('/api/agent/check-predictions', { method: 'POST' })
}

// ---------------------------------------------------------------------------
// Portfolio CSV export (returns a Blob for download)
// ---------------------------------------------------------------------------

export async function downloadPortfolioCsv(): Promise<void> {
  await waitForBackendReady()
  const token = getToken()
  const resp = await fetch(`${API_BASE}/api/portfolio/export/csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new Error(`CSV export failed: ${resp.status}`)
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `portfolio_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
