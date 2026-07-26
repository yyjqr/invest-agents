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

export interface ForecastScenario {
  label: '乐观' | '基准' | '悲观'
  probability: number
  predicted_price: number
  expected_return_pct: number
  description: string
}

export interface Forecast {
  ticker: string
  name: string
  horizon: '1h' | '1d' | '1w' | '1m' | '3m'
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
  factors?: string[]
  support_price?: number | null
  resistance_price?: number | null
  news_sentiment?: string
  news_sentiment_score?: number
  scenarios?: ForecastScenario[]
  // Quantile price bands (80% CI = q10/q90, 50% CI = q25/q75)
  quantile_10?: number | null
  quantile_25?: number | null
  quantile_75?: number | null
  quantile_90?: number | null
}

export interface EarningRecord {
  period: string          // e.g. "2024-09-30"
  report_type: string     // "年报" | "三季报" | "中报" | "一季报"
  revenue: number | null  // 亿元
  net_profit: number | null  // 亿元
  eps: number | null      // 元/股
  revenue_yoy: number | null  // 营收同比 (%)
  profit_yoy: number | null   // 净利同比 (%)
}

export interface StockProfile {
  ticker: string
  name: string
  main_business: string
  industry: string
  region: string
  listing_date: string
  employees: number | null
  pe_ttm: number | null
  pb_ratio: number | null
  market_cap_total: number | null  // 亿元
  profit_margin: number | null     // 净利率 (0-1)
  revenue_growth_yoy: number | null  // 营收同比 (0-1)
  return_on_equity: number | null    // ROE (0-1)
  dividend_yield: number | null      // 股息率 (0-1)
  recent_earnings: EarningRecord[]
}

export interface WatchlistItem {
  ticker: string
  name: string
  note: string
}

export interface NewsItem {
  title: string
  url: string
  source: string
  date: string
  tag: string
  summary: string
  // Research-report optional fields (empty string when not a 研报)
  rating?: string          // 买入 / 增持 / 中性 / 减持 / 卖出
  rating_change?: string   // 首次 / 上调 / 下调 / 维持
  stock_code?: string      // 6-digit A-share code without suffix
  sector?: string          // 申万一级行业
}

export interface NewsAnalysisContextItem {
  title: string
  url: string
  source: string
  summary: string
}

export interface CompanyAnalysisSWOT {
  strengths: string[]
  weaknesses: string[]
  opportunities: string[]
  threats: string[]
}

export interface CompanyAnalysisBenchmark {
  ticker: string
  label: string
}

export interface CompanyAnalysisSectorStock {
  ticker: string
  name: string
  desc: string
  price: number | null
  change_pct: number | null
  turnover: number | null
}

export interface CompanyAnalysis {
  sector: string
  maturity: string
  is_focus_sector: boolean
  benchmarks: CompanyAnalysisBenchmark[]
  swot: CompanyAnalysisSWOT | null
  fundamentals: {
    pe_ratio: number | null
    profit_margin: number | null  // percentage, e.g. 12.5 means 12.5%
    return_on_equity?: number | null  // percentage
    debt_to_equity?: number | null    // percentage
    revenue_growth_yoy?: number | null // percentage
    free_cash_flow?: number | null     // 亿元
    dividend_yield?: number | null     // percentage
  }
  sector_stocks: CompanyAnalysisSectorStock[]
  sector_summary: string
  // enriched company info (available when ticker is provided)
  profile?: {
    industry: string
    region: string
    listing_date: string
    employees: number | null
    company_type: string
  } | null
  recent_earnings?: Array<{
    period: string
    report_type: string
    revenue: number | null    // 亿元
    net_profit: number | null // 亿元
    eps: number | null        // 元
    revenue_yoy: number | null  // %
    profit_yoy: number | null   // %
  }>
  recent_news?: Array<{
    title: string
    url: string
    date: string
    tag: string   // 利好 | 利空 | 公告
    source: string
  }>
}

export interface NewsAnalysis {
  provider: string
  summary: string
  sentiment: string
  score: number
  themes: string[]
  key_points: string[]
  opportunities: string[]
  risks: string[]
  context_items: NewsAnalysisContextItem[]
  query: string
  key_configured: boolean
  company?: string
  sector?: string
  company_analysis?: CompanyAnalysis | null
}

export interface AppSettings {
  theme?: string
  refresh_interval?: number
  price_alert?: boolean
  has_alpha_vantage_key?: boolean
  has_tavily_key?: boolean
  has_qveris_key?: boolean
}

export interface PriceAlert {
  alert_high: number | null
  alert_low: number | null
}

export interface SearchResult {
  symbol: string
  code: string
  name: string
  exchange: string
  category: string
}

export interface SectorStock {
  ticker: string
  name: string
  desc: string
  price?: number | null
  change?: number | null
  change_pct?: number | null
  turnover?: number | null
  volume?: number | null
}

export interface SectorInfo {
  name: string
  stocks: SectorStock[]
  top_gainers?: SectorStock[]
  top_turnover?: SectorStock[]
  top_volume?: SectorStock[]
}

export interface MarketIndex {
  code: string
  name: string
  price: number | null
  change: number | null
  change_pct: number | null
}

export interface IndustryPickStock {
  ticker: string
  name: string
  price: number | null
  change_pct: number | null
  turnover: number | null
  score: number
  reason: string
}

export interface IndustryPickInfo {
  slug: string
  l1: string
  l2: string
  l3: string
  available: boolean
  status: string
  plate_code: string
  plate_name: string
  note: string
  summary: string
  picks: IndustryPickStock[]
}

export interface IndustryPicksResponse {
  updated_at: string
  matched_count: number
  taxonomy: {
    l1: string
    l2: string
    l3: string
  }
  items: IndustryPickInfo[]
}

export interface SmartPickItem {
  ticker: string
  name: string
  sector: string           // 所属板块名称
  sector_code: string      // 所属板块代码
  change_pct: number       // 当日涨跌幅 (%)
  turnover: number         // 成交额 (元)
  turnover_rate: number | null  // 换手率 (%)
  score: number            // 综合评分 (0–100)
  picked_at: string        // 首次入池时间 ISO
}

export interface SmartPicksResponse {
  picks: SmartPickItem[]
  updated_at: string       // 最近刷新时间 ISO
  ttl: number              // 缓存有效期 (秒)
}

export interface ForecastRecord {
  date: string
  ticker: string
  name: string
  horizon: string
  current_price: number
  predicted_price: number
  predicted_high: number
  predicted_low: number
  trend: string
  confidence: number
  expected_return_pct: number
  scenarios?: ForecastScenario[]
}

export interface AboutInfo {
  version: string
  name: string
  description: string
  disclaimer: string
  copyright: string
}

export interface SavedArticle {
  title: string
  url: string
  source: string
  date: string
  tag: string
  summary: string
  category: string
  saved_at: string
}

// ---------------------------------------------------------------------------
// Trading / Paper trading
// ---------------------------------------------------------------------------

export type OrderAction = 'BUY' | 'SELL'
export type OrderMode = 'paper' | 'easytrader' | 'manual'
export type OrderStatus = 'pending' | 'filled' | 'rejected' | 'cancelled'

export interface OrderRecord {
  order_id: string
  ticker: string
  name: string
  action: OrderAction
  quantity: number
  price: number
  mode: OrderMode
  status: OrderStatus
  fill_price: number | null
  fill_qty: number | null
  fill_time: string | null
  broker_ref: string | null
  risk_check: string
  note: string
  created_at: string
}

export interface PaperPosition {
  ticker: string
  name: string
  qty: number
  avg_cost: number
  market_value: number | null
}

export interface PaperAccountSummary {
  cash: number
  initial_cash: number
  positions: PaperPosition[]
}

export interface RiskConfig {
  max_single_order_pct: number
  max_daily_trade_pct: number
}

export interface OrderSubmitResult {
  order_id: string
  status: OrderStatus
  fill_price: number | null
  fill_qty: number | null
  fill_time: string | null
  risk_check: string
  mode: OrderMode
}
