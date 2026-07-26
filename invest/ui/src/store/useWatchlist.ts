import { create } from 'zustand'
import type { WatchlistItem } from '@/types'
import {
  addToWatchlist,
  fetchMonitor,
  fetchWatchlist,
  removeFromWatchlist,
  updateMonitor,
} from '@/api/client'

interface WatchlistState {
  items: WatchlistItem[]
  monitorTickers: string[]     // max 20 — pinned to realtime monitor
  loading: boolean
  loadWatchlist: () => Promise<void>
  loadMonitor: () => Promise<void>
  addItem: (ticker: string, name?: string, note?: string) => Promise<void>
  removeItem: (ticker: string) => Promise<void>
  setMonitor: (tickers: string[]) => Promise<void>
  toggleMonitor: (ticker: string) => Promise<void>
}

export const useWatchlist = create<WatchlistState>((set, get) => ({
  items: [],
  monitorTickers: [],
  loading: false,

  loadWatchlist: async () => {
    set({ loading: true })
    try {
      const items = await fetchWatchlist()
      set({ items, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  loadMonitor: async () => {
    const tickers = await fetchMonitor().catch(() => [] as string[])
    set({ monitorTickers: tickers })
  },

  addItem: async (ticker, name = '', note = '') => {
    const items = await addToWatchlist(ticker, name, note)
    set({ items })
  },

  removeItem: async (ticker) => {
    const items = await removeFromWatchlist(ticker)
    // Also remove from monitor if present
    const { monitorTickers } = get()
    const newMonitor = monitorTickers.filter((t) => t !== ticker)
    if (newMonitor.length !== monitorTickers.length) {
      await updateMonitor(newMonitor)
      set({ monitorTickers: newMonitor })
    }
    set({ items })
  },

  setMonitor: async (tickers) => {
    const saved = await updateMonitor(tickers.slice(0, 20))
    set({ monitorTickers: saved })
  },

  toggleMonitor: async (ticker) => {
    const { monitorTickers } = get()
    let next: string[]
    if (monitorTickers.includes(ticker)) {
      next = monitorTickers.filter((t) => t !== ticker)
    } else {
      if (monitorTickers.length >= 20) {
        throw new Error('监控列表最多20只股票')
      }
      next = [...monitorTickers, ticker]
    }
    await updateMonitor(next)
    set({ monitorTickers: next })
  },
}))
