/**
 * KLineChart — wraps TradingView lightweight-charts library.
 * Renders candlestick + volume histogram for an array of OHLCV data.
 */
import { useEffect, useRef } from 'react'
import {
  createChart,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type Time,
} from 'lightweight-charts'
import type { OHLCV } from '@/types'

interface Props {
  data: OHLCV[]
  height?: number
  /** Show volume histogram below candles */
  showVolume?: boolean
}

export default function KLineChart({ data, height = 420, showVolume = true }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height,
    })

    // Candle series (top 75% if volume visible)
    const candle = chart.addCandlestickSeries({
      upColor: '#ef4444',
      downColor: '#22c55e',
      borderUpColor: '#ef4444',
      borderDownColor: '#22c55e',
      wickUpColor: '#ef4444',
      wickDownColor: '#22c55e',
      priceScaleId: 'right',
    })
    chartRef.current = chart
    candleRef.current = candle

    // Volume histogram
    if (showVolume) {
      const vol = chart.addHistogramSeries({
        color: '#3b82f680',
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
      })
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })
      chart.priceScale('right').applyOptions({
        scaleMargins: { top: 0.04, bottom: 0.2 },
      })
      volRef.current = vol
    }

    // Responsive resize
    const observer = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volRef.current = null
    }
  }, [height, showVolume])

  // Update data when prop changes
  useEffect(() => {
    if (!candleRef.current || !data.length) return

    const candleData: CandlestickData[] = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))
    candleRef.current.setData(candleData)

    if (volRef.current) {
      const volData: HistogramData[] = data.map((d, i) => ({
        time: d.time as Time,
        value: d.volume,
        color: i > 0 && d.close >= data[i - 1].close ? '#ef444460' : '#22c55e60',
      }))
      volRef.current.setData(volData)
    }

    chartRef.current?.timeScale().fitContent()
  }, [data])

  return (
    <div
      ref={containerRef}
      style={{ height, width: '100%' }}
      className="rounded-xl overflow-hidden"
    />
  )
}
