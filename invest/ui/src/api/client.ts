/**
 * API client — thin wrappers around the FastAPI backend at http://127.0.0.1:8765
 * All authenticated requests include the JWT Bearer token from localStorage.
 */

import type {
  Forecast,
  OHLCV,
  Quote,
  SearchResult,
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

// ---------------------------------------------------------------------------
// Fetch wrapper
// ---------------------------------------------------------------------------

async function _fetch<T>(
  path: string,
  options: RequestInit = {},
  auth = true,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (auth) {
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
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

// ---------------------------------------------------------------------------
// Strategy & Forecast
// ---------------------------------------------------------------------------

export async function fetchStrategy(ticker: string): Promise<StrategyReport> {
  return _fetch(`/api/strategy/${ticker}`)
}

export async function fetchForecast(ticker: string, horizon: '1h' | '1d' = '1d'): Promise<Forecast> {
  return _fetch(`/api/forecast/${ticker}?horizon=${horizon}`)
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
  return _fetch<{ theme: string; refresh_interval: number; price_alert: boolean }>('/api/settings')
}

export async function updateSettings(settings: Partial<{ theme: string; refresh_interval: number; price_alert: boolean }>) {
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
