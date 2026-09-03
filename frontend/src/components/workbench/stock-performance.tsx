import { useEffect, useId, useState } from 'react'
import { requestJson } from '@/api/client'
import type { WorkbenchStockPerformance, WorkbenchStockPerformancePoint } from '@/types'
import { formatManagementDate, formatPercent } from '@/lib/format'


type ChartPoint = {
  index: number
  value: number
}

function numericValue(value: string | null): number | null {
  if (value === null) {
    return null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function statusLabel(status: WorkbenchStockPerformance['status']): string {
  const labels: Record<WorkbenchStockPerformance['status'], string> = {
    available: '证据完整',
    incomplete: '证据不完整',
    unavailable: '表现不可用',
  }
  return labels[status]
}

function statusClass(status: WorkbenchStockPerformance['status']): string {
  return `stock-performance-status stock-performance-status--${status}`
}

function returnClass(value: string | null): string {
  const numeric = numericValue(value)
  if (numeric === null) {
    return ''
  }
  return numeric >= 0 ? 'market-profit' : 'market-loss'
}

function pointEvidence(point: WorkbenchStockPerformancePoint): string {
  if (point.status === 'available') {
    return point.market_snapshot_ids.length > 0
      ? `行情快照：${point.market_snapshot_ids.join('、')}`
      : '行情证据完整'
  }
  return point.reason ?? '该估值日行情证据不可用；收益率保持为空。'
}

function chartSegments(points: WorkbenchStockPerformancePoint[]): ChartPoint[][] {
  const segments: ChartPoint[][] = []
  let segment: ChartPoint[] = []
  points.forEach((point, index) => {
    const value = point.status === 'available' ? numericValue(point.cumulative_return) : null
    if (value === null) {
      if (segment.length > 0) {
        segments.push(segment)
      }
      segment = []
      return
    }
    segment.push({ index, value })
  })
  if (segment.length > 0) {
    segments.push(segment)
  }
  return segments
}

function formatReturn(value: string | null): string {
  return formatPercent(value)
}

function StockPerformanceChart({
  symbol,
  points,
}: {
  symbol: string
  points: WorkbenchStockPerformancePoint[]
}) {
  const chartId = useId()
  const titleId = `${chartId}-title`
  const descriptionId = `${chartId}-description`
  const segments = chartSegments(points)
  const chartWidth = 720
  const chartHeight = 270
  const plotLeft = 64
  const plotRight = 18
  const plotTop = 20
  const plotBottom = 36
  const plotWidth = chartWidth - plotLeft - plotRight
  const plotHeight = chartHeight - plotTop - plotBottom
  const values = segments.flatMap((segment) => segment.map((point) => point.value))

  if (values.length === 0) {
    return (
      <section className="stock-performance-chart" aria-labelledby={titleId}>
        <div className="stock-performance-chart-heading">
          <h4 id={titleId}>累计净收益率走势</h4>
          <span>按 approved 日线收盘估值</span>
        </div>
        <p className="stock-performance-chart-empty" role="status">
          当前没有可用的累计净收益率估值点；缺失行情不会用旧价、零值或猜测值补齐。
        </p>
      </section>
    )
  }

  const minimum = Math.min(0, ...values)
  const maximum = Math.max(0, ...values)
  const range = Math.max(maximum - minimum, 0.0001)
  const xForIndex = (index: number): number => {
    if (points.length <= 1) {
      return plotLeft + plotWidth / 2
    }
    return plotLeft + (plotWidth * index) / (points.length - 1)
  }
  const yForValue = (value: number): number => (
    plotTop + ((maximum - value) / range) * plotHeight
  )
  const zeroY = yForValue(0)
  const firstPoint = segments[0][0]
  const lastSegment = segments[segments.length - 1]
  const lastPoint = lastSegment[lastSegment.length - 1]
  const description = `${symbol} 的累计净收益率走势，缺失行情估值日保持断开。`

  return (
    <section className="stock-performance-chart" aria-labelledby={titleId}>
      <div className="stock-performance-chart-heading">
        <div>
          <h4 id={titleId}>累计净收益率走势</h4>
          <span id={descriptionId}>百分比按净收益率比例展示，缺口表示估值证据不可用。</span>
        </div>
        <span>{points.length} 个估值日</span>
      </div>
      <svg
        className="stock-performance-chart-svg"
        viewBox={`0 0 ${chartWidth} ${chartHeight}`}
        role="img"
        aria-labelledby={`${titleId} ${descriptionId}`}
      >
        <title>{description}</title>
        <desc>累计净收益率折线；虚线为零收益率基线，缺失估值日不连接。</desc>
        <line
          className="stock-performance-zero-line"
          x1={plotLeft}
          x2={chartWidth - plotRight}
          y1={zeroY}
          y2={zeroY}
        />
        <text className="stock-performance-axis-label" x={plotLeft - 8} y={plotTop + 4} textAnchor="end">
          {formatReturn(maximum.toString())}
        </text>
        <text className="stock-performance-axis-label" x={plotLeft - 8} y={zeroY + 4} textAnchor="end">
          0.00%
        </text>
        <text className="stock-performance-axis-label" x={plotLeft - 8} y={plotTop + plotHeight + 4} textAnchor="end">
          {formatReturn(minimum.toString())}
        </text>
        {segments.map((segment) => (
          <path
            className="stock-performance-line"
            d={segment.map((point, pointIndex) => `${pointIndex === 0 ? 'M' : 'L'} ${xForIndex(point.index)} ${yForValue(point.value)}`).join(' ')}
            key={segment[0].index}
          />
        ))}
        {segments.flatMap((segment) => segment).map((point) => (
          <circle
            className="stock-performance-point"
            cx={xForIndex(point.index)}
            cy={yForValue(point.value)}
            key={point.index}
            r="4"
          />
        ))}
      </svg>
      <div className="stock-performance-chart-legend" aria-label="图表说明">
        <span>实线：累计净收益率</span>
        <span>虚线：零收益率基线</span>
        <span>空档：估值证据不可用</span>
      </div>
      <div className="stock-performance-chart-footnote">
        <span>首个可用点：{points[firstPoint.index].session_date}</span>
        <span>末个可用点：{points[lastPoint.index].session_date}</span>
      </div>
    </section>
  )
}

function PerformanceStat({
  label,
  value,
  date,
  tone,
}: {
  label: string
  value: string | null
  date: string | null
  tone: 'profit' | 'loss' | 'neutral'
}) {
  return (
    <div className={`stock-performance-stat stock-performance-stat--${tone}`}>
      <dt>{label}</dt>
      <dd className={returnClass(value)}>{formatReturn(value)}</dd>
      <small>{date ? `${date} 估值` : '暂无可用估值日'}</small>
    </div>
  )
}

export function StockPerformancePanel({
  apiBase,
  symbol,
}: {
  apiBase: string
  symbol: string
}) {
  const panelId = useId()
  const tableId = `${panelId}-table`
  const [performance, setPerformance] = useState<WorkbenchStockPerformance | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    void requestJson<WorkbenchStockPerformance>(
      `${apiBase}/v1/workbench/stocks/${encodeURIComponent(symbol)}/performance`,
      { signal: controller.signal },
    ).then((nextPerformance) => {
      if (!controller.signal.aborted) {
        setPerformance(nextPerformance)
      }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setErrorMessage(reason instanceof Error ? reason.message : '加载证券历史表现失败。')
      }
    }).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    })
    return () => controller.abort()
  }, [apiBase, symbol])

  if (performance === null) {
    return (
      <section className="stock-performance-panel" aria-labelledby={panelId} aria-busy={loading}>
        <div className="stock-performance-heading">
          <div className="stock-performance-heading-copy">
            <span className="drawer-section-kicker">STOCK · NET RETURN</span>
            <h3 id={panelId}>单证券历史净收益率</h3>
            <p>按该证券已确认成交和本地 approved 行情快照只读重放。</p>
          </div>
          <span className={statusClass('unavailable')}>{loading ? '读取中' : '表现不可用'}</span>
        </div>
        <p className="stock-performance-loading" role={loading ? 'status' : 'alert'}>
          {loading ? '正在读取该证券历史净收益率…' : `单证券历史表现暂时无法读取：${errorMessage ?? '未知错误。'}`}
        </p>
      </section>
    )
  }

  return (
    <section className="stock-performance-panel" aria-labelledby={panelId} aria-busy={loading}>
      <div className="stock-performance-heading">
        <div className="stock-performance-heading-copy">
          <span className="drawer-section-kicker">STOCK · NET RETURN</span>
          <h3 id={panelId}>单证券历史净收益率</h3>
          <p>收益率以百分比展示；已计入成交佣金和印花税，不混合其他证券。</p>
        </div>
        <div className="stock-performance-heading-meta">
          <span className={statusClass(performance.status)}>{statusLabel(performance.status)}</span>
          <small>截至 {formatManagementDate(performance.as_of)}</small>
        </div>
      </div>
      <dl className="stock-performance-summary" aria-label="单证券净收益率摘要">
        <PerformanceStat
          date={performance.highest_return_date}
          label="历史最高净收益率"
          tone="profit"
          value={performance.highest_return}
        />
        <PerformanceStat
          date={performance.maximum_loss_date}
          label="最大亏损净收益率"
          tone="loss"
          value={performance.maximum_loss_return}
        />
        <PerformanceStat
          date={performance.latest_session_date}
          label="最新累计净收益率"
          tone="neutral"
          value={performance.latest_cumulative_return}
        />
        <PerformanceStat
          date={performance.latest_session_date}
          label="最新日净收益率"
          tone="neutral"
          value={performance.latest_daily_return}
        />
      </dl>
      {performance.reason && (
        <p className={`stock-performance-note stock-performance-note--${performance.status}`}>
          <strong>行情证据说明：</strong>{performance.reason}
        </p>
      )}
      <div className="stock-performance-content">
        <StockPerformanceChart symbol={performance.symbol} points={performance.points} />
        <section className="stock-performance-table-section" aria-labelledby={tableId}>
          <div className="stock-performance-table-heading">
            <div>
              <h4 id={tableId}>估值日明细</h4>
              <span>日净收益率与累计净收益率</span>
            </div>
            <span>{performance.points.length} 个估值点</span>
          </div>
          {performance.points.length === 0 ? (
            <p className="stock-performance-table-empty">暂无可展示的收益率估值日明细。</p>
          ) : (
            <div className="stock-performance-table-wrap">
              <table className="stock-performance-table">
                <caption className="sr-only">证券净收益率估值明细</caption>
                <thead>
                  <tr>
                    <th scope="col">估值日</th>
                    <th scope="col">状态</th>
                    <th scope="col">日净收益率</th>
                    <th scope="col">累计净收益率</th>
                    <th scope="col">行情证据</th>
                  </tr>
                </thead>
                <tbody>
                  {performance.points.map((point) => (
                    <tr className={`stock-performance-row--${point.status}`} key={point.session_date}>
                      <th scope="row">
                        <strong>{point.session_date}</strong>
                        <small>{formatManagementDate(point.valuation_at)}</small>
                      </th>
                      <td><span className={statusClass(point.status)}>{statusLabel(point.status)}</span></td>
                      <td className={`stock-performance-number ${returnClass(point.daily_return)}`}>{formatReturn(point.daily_return)}</td>
                      <td className={`stock-performance-number ${returnClass(point.cumulative_return)}`}>{formatReturn(point.cumulative_return)}</td>
                      <td>{pointEvidence(point)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </section>
  )
}
