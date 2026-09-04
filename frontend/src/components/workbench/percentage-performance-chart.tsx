import { useId, useRef, useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { WorkbenchMarketEvidence } from '@/types'
import { formatPercent, formatPrice } from '@/lib/format'


type PercentagePerformancePoint = {
  session_date: string
  status: WorkbenchMarketEvidence['status']
  close: string | null
  daily_return: string | null
  cumulative_return: string | null
}

type ChartPoint = {
  index: number
  value: number
}

export type PercentagePerformanceChartProps = {
  symbol: string
  points: PercentagePerformancePoint[]
  title: string
  subtitle: string
  emptyMessage: string
  dailyLabel: string
  cumulativeLabel: string
  lineLabel: string
  pointUnit: string
  zeroLineLabel: string
}

type ChartSize = 'compact' | 'expanded'

function numericValue(value: string | null): number | null {
  if (value === null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function chartSegments(points: PercentagePerformancePoint[]): ChartPoint[][] {
  const segments: ChartPoint[][] = []
  let segment: ChartPoint[] = []
  points.forEach((point, index) => {
    const value = point.status === 'available' ? numericValue(point.cumulative_return) : null
    if (value === null) {
      if (segment.length > 0) segments.push(segment)
      segment = []
      return
    }
    segment.push({ index, value })
  })
  if (segment.length > 0) segments.push(segment)
  return segments
}

function evidenceValue(value: string | null, formatter: (value: string | null) => string): string {
  return value === null ? '不可用' : formatter(value)
}

export function PercentagePerformanceChart({
  symbol,
  points,
  title,
  subtitle,
  emptyMessage,
  dailyLabel,
  cumulativeLabel,
  lineLabel,
  pointUnit,
  zeroLineLabel,
  size = 'compact',
  onExpand,
}: PercentagePerformanceChartProps & {
  size?: ChartSize
  onExpand?: () => void
}) {
  const chartId = useId()
  const titleId = `${chartId}-title`
  const descriptionId = `${chartId}-description`
  const tooltipId = `${chartId}-tooltip`
  const [activePointIndex, setActivePointIndex] = useState<number | null>(null)
  const ignoreInitialExpandedMouseMove = useRef(size === 'expanded')
  const segments = chartSegments(points)
  const chartPoints = segments.flatMap((segment) => segment)
  const activeChartPoint = chartPoints.find((point) => point.index === activePointIndex) ?? null
  const activePoint = activeChartPoint ? points[activeChartPoint.index] : null
  const isExpanded = size === 'expanded'
  const chartWidth = isExpanded ? 1120 : 720
  const chartHeight = isExpanded ? 620 : 320
  const plotLeft = isExpanded ? 120 : 82
  const plotRight = isExpanded ? 52 : 34
  const plotTop = isExpanded ? 64 : 34
  const plotBottom = isExpanded ? 78 : 54
  const plotWidth = chartWidth - plotLeft - plotRight
  const plotHeight = chartHeight - plotTop - plotBottom
  const values = chartPoints.map((point) => point.value)

  if (values.length === 0) {
    return (
      <section className={`stock-performance-chart stock-performance-chart--${size}`} aria-labelledby={titleId}>
        <div className="stock-performance-chart-heading">
          <div>
            <h4 id={titleId}>{title}</h4>
            <span>{subtitle}</span>
          </div>
          {onExpand && <button className="stock-performance-expand-button" type="button" onClick={onExpand}>放大查看图表</button>}
        </div>
        <p className="stock-performance-chart-empty" role="status">
          {emptyMessage}
        </p>
      </section>
    )
  }

  const minimum = Math.min(0, ...values)
  const maximum = Math.max(0, ...values)
  const range = Math.max(maximum - minimum, 0.0001)
  const xForIndex = (index: number): number => (
    points.length <= 1 ? plotLeft + plotWidth / 2 : plotLeft + (plotWidth * index) / (points.length - 1)
  )
  const yForValue = (value: number): number => plotTop + ((maximum - value) / range) * plotHeight
  const zeroY = yForValue(0)
  const firstPoint = segments[0][0]
  const lastSegment = segments[segments.length - 1]
  const lastPoint = lastSegment[lastSegment.length - 1]
  const description = `${symbol} 的${title}，缺失行情${pointUnit}保持断开。`
  const tooltipWidth = isExpanded ? 330 : 248
  const tooltipHeight = isExpanded ? 128 : 112
  const tooltipX = activeChartPoint
    ? Math.min(Math.max(xForIndex(activeChartPoint.index) - tooltipWidth / 2, plotLeft), chartWidth - plotRight - tooltipWidth)
    : 0
  const tooltipY = activeChartPoint && yForValue(activeChartPoint.value) < plotTop + tooltipHeight + 18
    ? yForValue(activeChartPoint.value) + 18
    : activeChartPoint
      ? yForValue(activeChartPoint.value) - tooltipHeight - 18
      : 0
  const tooltipLines = activePoint ? [
    `交易日：${activePoint.session_date}`,
    `收盘价：${evidenceValue(activePoint.close, formatPrice)}`,
    `${dailyLabel}：${evidenceValue(activePoint.daily_return, formatPercent)}`,
    `${cumulativeLabel}：${evidenceValue(activePoint.cumulative_return, formatPercent)}`,
  ] : []

  return (
    <section className={`stock-performance-chart stock-performance-chart--${size}`} aria-labelledby={titleId}>
      <div className="stock-performance-chart-heading">
        <div>
          <h4 id={titleId}>{title}</h4>
          <span id={descriptionId}>{subtitle}</span>
        </div>
        <div className="stock-performance-chart-actions">
          <span>{points.length} 个{pointUnit}</span>
          {onExpand && <button className="stock-performance-expand-button" type="button" onClick={onExpand}>放大查看图表</button>}
        </div>
      </div>
      <svg
        className="stock-performance-chart-svg"
        viewBox={`0 0 ${chartWidth} ${chartHeight}`}
        role="img"
        aria-labelledby={`${titleId} ${descriptionId}`}
      >
        <title>{description}</title>
        <desc>{`${lineLabel}折线；虚线为${zeroLineLabel}基线，缺失${pointUnit}不连接。数据点可悬停或通过 Tab 聚焦以查看行情详情。`}</desc>
        <line
          className="stock-performance-zero-line"
          x1={plotLeft}
          x2={chartWidth - plotRight}
          y1={zeroY}
          y2={zeroY}
        />
        <text className="stock-performance-axis-label" x={plotLeft - 10} y={plotTop + 5} textAnchor="end">
          {formatPercent(maximum.toString())}
        </text>
        <text className="stock-performance-axis-label" x={plotLeft - 10} y={zeroY + 5} textAnchor="end">
          0.00%
        </text>
        <text className="stock-performance-axis-label" x={plotLeft - 10} y={plotTop + plotHeight + 5} textAnchor="end">
          {formatPercent(minimum.toString())}
        </text>
        {segments.map((segment) => (
          <path
            className="stock-performance-line"
            d={segment.map((point, pointIndex) => `${pointIndex === 0 ? 'M' : 'L'} ${xForIndex(point.index)} ${yForValue(point.value)}`).join(' ')}
            key={segment[0].index}
          />
        ))}
        {chartPoints.map((point) => {
          const chartPoint = points[point.index]
          const pointDescription = `${chartPoint.session_date}，收盘价 ${evidenceValue(chartPoint.close, formatPrice)}，${dailyLabel} ${evidenceValue(chartPoint.daily_return, formatPercent)}，${cumulativeLabel} ${evidenceValue(chartPoint.cumulative_return, formatPercent)}`
          return (
            <circle
              aria-describedby={activePointIndex === point.index ? tooltipId : undefined}
              aria-label={pointDescription}
              className="stock-performance-point"
              cx={xForIndex(point.index)}
              cy={yForValue(point.value)}
              key={point.index}
              onFocus={(event) => {
                if (event.currentTarget.matches(':focus-visible')) setActivePointIndex(point.index)
              }}
              onMouseMove={(event) => {
                if (ignoreInitialExpandedMouseMove.current) {
                  ignoreInitialExpandedMouseMove.current = false
                  return
                }
                if (event.movementX === 0 && event.movementY === 0) return
                setActivePointIndex(point.index)
              }}
              r={isExpanded ? 14 : 18}
              role="img"
              tabIndex={0}
            />
          )
        })}
        {activeChartPoint && activePoint && (
          <g className="stock-performance-tooltip" id={tooltipId} role="tooltip">
            <rect
              className="stock-performance-tooltip-background"
              height={tooltipHeight}
              rx="10"
              width={tooltipWidth}
              x={tooltipX}
              y={tooltipY}
            />
            {tooltipLines.map((line, index) => (
              <text
                className={index === 0 ? 'stock-performance-tooltip-title' : 'stock-performance-tooltip-text'}
                key={line}
                x={tooltipX + (isExpanded ? 18 : 14)}
                y={tooltipY + (isExpanded ? 30 : 25) + index * (isExpanded ? 25 : 22)}
              >
                {line}
              </text>
            ))}
          </g>
        )}
      </svg>
      <div className="stock-performance-chart-legend" aria-label="图表说明">
        <span>实线：{lineLabel}</span>
        <span>虚线：{zeroLineLabel}基线</span>
        <span>空档：行情证据不可用</span>
      </div>
      <div className="stock-performance-chart-footnote">
        <span>首个可用点：{points[firstPoint.index].session_date}</span>
        <span>末个可用点：{points[lastPoint.index].session_date}</span>
      </div>
    </section>
  )
}

export function PerformanceChartDialog({
  open,
  onOpenChange,
  ...chartProps
}: PercentagePerformanceChartProps & {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="performance-chart-dialog">
        <DialogHeader>
          <DialogTitle>{chartProps.title}</DialogTitle>
          <DialogDescription>放大查看本地 approved 行情证据；可悬停或通过 Tab 聚焦数据点查看详情。</DialogDescription>
        </DialogHeader>
        <PercentagePerformanceChart key={open ? 'open' : 'closed'} {...chartProps} size="expanded" />
      </DialogContent>
    </Dialog>
  )
}
