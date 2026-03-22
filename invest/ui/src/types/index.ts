/* Global types shared across the app */

export interface User {
  sub: string
  display_name: string
}

export interface Quote {
  ticker: string
  price: number
  change: number
  change_pct: number
  name: string
  pe_ttm?: number | null
  pb_ratio?: number | null
  market_cap_total?: number | null
  turnover?: number | null
  volume?: number | null
  turnover_rate?: number | null
}

export interface OHLCV {
  time: string   // "YYYY-MM-DD" or "YYYY-MM-DD HH:mm"
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface StrategySignal {
  name: string
  signal: '买入' | '卖出' | '观望' | '反弹机会'
  score: number
  details: string[]
  indicators: Record<string, number | string | null>
}

export interface StrategyReport {
  ticker: string
  name: string
  strategy1: StrategySignal
  strategy2: StrategySignal
  strategy3: StrategySignal
  combined_score: number
  composite_action: string
  hot_sectors: string[]
  in_hot_sector: boolean
  sector: string
  northbound_sentiment: string
  position_pct: number
  position_note: string
}

export interface Forecast {
  ticker: string
  name: string
  horizon: '1h' | '1d'
  current_price: number
  predicted_price: number
  predicted_high: number
  predicted_low: number
  trend: string
  confidence: number
  expected_return_pct: number
  expected_range_pct: number
  high_time: string
  low_time: string
}

export interface WatchlistItem {
  ticker: string
  name: string
  note: string
}

export interface SearchResult {
  symbol: string
  code: string
  name: string
  exchange: string
  category: string
}
