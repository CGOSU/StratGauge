import { useEffect, useId, useState } from 'react'
import { requestJson } from '@/api/client'
import { PercentagePerformanceChart } from '@/components/workbench/percentage-performance-chart'
import type { WorkbenchObservationPerformanceHistory, WorkbenchObservationPerformancePoint } from '@/types'
import { formatManagementDate, formatPercent, formatPrice } from '@/lib/format'


function numericValue(value: string | null): number | null {
  if (value === null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function returnClass(value: string | null): string {
  const numeric = numericValue(value)
  if (numeric === null) return ''
  return numeric >= 0 ? 'market-profit' : 'market-loss'
}

function statusLabel(status: WorkbenchObservationPerformanceHistory['status']): string {
  const labels: Record<WorkbenchObservationPerformanceHistory['status'], string> = {
    available: '证据完整',
    incomplete: '证据不完整',
    unavailable: '表现不可用',
  }
  return labels[status]
}

function pointEvidence(point: WorkbenchObservationPerformancePoint): string {
  if (point.market_snapshot_id) return `行情快照：${point.market_snapshot_id}`
  return point.reason ?? '该交易日行情证据不可用；百分比保持为空。'
}

function observationReturnExtremes(points: WorkbenchObservationPerformancePoint[]) {
  let highest: WorkbenchObservationPerformancePoint | null = null
  let lowest: WorkbenchObservationPerformancePoint | null = null
  for (const point of points) {
    const value = point.status === 'available' ? numericValue(point.cumulative_return) : null
    if (value === null) continue
    const highestValue = highest ? numericValue(highest.cumulative_return) : null
    const lowestValue = lowest ? numericValue(lowest.cumulative_return) : null
    if (highestValue === null || value > highestValue) highest = point
    if (lowestValue === null || value < lowestValue) lowest = point
  }
  return { highest, lowest }
}

export function ObservationPerformancePanel({
  apiBase,
  candidateId,
  asOf,
}: {
  apiBase: string
  candidateId: string
  asOf: string
}) {
  const panelId = useId()
  const tableId = `${panelId}-table`
  const [performance, setPerformance] = useState<WorkbenchObservationPerformanceHistory | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ as_of: asOf })
    void requestJson<WorkbenchObservationPerformanceHistory>(
      `${apiBase}/v1/workbench/candidates/${encodeURIComponent(candidateId)}/observation-performance?${query}`,
      { signal: controller.signal },
    ).then((nextPerformance) => {
      if (!controller.signal.aborted) setPerformance(nextPerformance)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setErrorMessage(reason instanceof Error ? reason.message : '加载观察期每日涨跌幅失败。')
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [apiBase, asOf, candidateId])

  if (performance === null) {
    return (
      <section className="stock-performance-panel" aria-labelledby={panelId} aria-busy={loading}>
        <div className="stock-performance-heading">
          <div className="stock-performance-heading-copy">
            <span className="drawer-section-kicker">WATCHLIST · PERCENTAGE CHANGE</span>
            <h3 id={panelId}>观察期每日涨跌幅</h3>
            <p>只读取本地 approved 日线，所有表现均为百分比。</p>
          </div>
          <span className="stock-performance-status stock-performance-status--unavailable">{loading ? '读取中' : '表现不可用'}</span>
        </div>
        <p className="stock-performance-loading" role={loading ? 'status' : 'alert'}>
          {loading ? '正在读取观察期每日涨跌幅…' : `观察期表现暂时无法读取：${errorMessage ?? '未知错误。'}`}
        </p>
      </section>
    )
  }

  const { highest, lowest } = observationReturnExtremes(performance.points)

  return (
    <section className="stock-performance-panel" aria-labelledby={panelId} aria-busy={loading}>
      <div className="stock-performance-heading">
        <div className="stock-performance-heading-copy">
          <span className="drawer-section-kicker">WATCHLIST · PERCENTAGE CHANGE</span>
          <h3 id={panelId}>观察期每日涨跌幅</h3>
          <p>日涨跌幅按相邻有效收盘价计算，观察期涨跌幅以观察起点收盘价为基准。</p>
        </div>
        <div className="stock-performance-heading-meta">
          <span className={`stock-performance-status stock-performance-status--${performance.status}`}>{statusLabel(performance.status)}</span>
          <small>截至 {formatManagementDate(performance.as_of)}</small>
        </div>
      </div>
      <dl className="stock-performance-summary" aria-label="观察期百分比表现摘要">
        <div className="stock-performance-stat stock-performance-stat--neutral">
          <dt>最新日涨跌幅</dt>
          <dd className={returnClass(performance.latest_daily_return)}>{formatPercent(performance.latest_daily_return)}</dd>
          <small>{performance.latest_session_date ?? '暂无可用交易日'}</small>
        </div>
        <div className="stock-performance-stat stock-performance-stat--neutral">
          <dt>观察期累计涨跌幅</dt>
          <dd className={returnClass(performance.latest_cumulative_return)}>{formatPercent(performance.latest_cumulative_return)}</dd>
          <small>{performance.anchor_session_date ? `起点 ${performance.anchor_session_date}` : '观察起点不可用'}</small>
        </div>
        <div className="stock-performance-stat stock-performance-stat--profit">
          <dt>观察期最高涨幅</dt>
          <dd className={returnClass(highest?.cumulative_return ?? null)}>{formatPercent(highest?.cumulative_return ?? null)}</dd>
          <small>{highest?.session_date ?? '暂无可用交易日'}</small>
        </div>
        <div className="stock-performance-stat stock-performance-stat--loss">
          <dt>观察期最大跌幅</dt>
          <dd className={returnClass(lowest?.cumulative_return ?? null)}>{formatPercent(lowest?.cumulative_return ?? null)}</dd>
          <small>{lowest?.session_date ?? '暂无可用交易日'}</small>
        </div>
      </dl>
      {performance.reason && (
        <p className={`stock-performance-note stock-performance-note--${performance.status}`}>
          <strong>行情证据说明：</strong>{performance.reason}
        </p>
      )}
      <div className="stock-performance-content">
        <PercentagePerformanceChart
          cumulativeLabel="观察期累计涨跌幅"
          dailyLabel="日涨跌幅"
          emptyMessage="当前没有可用的观察期累计涨跌幅点；缺失行情不会用旧价、零值或猜测值补齐。"
          lineLabel="观察期累计涨跌幅"
          points={performance.points}
          pointUnit="交易日"
          subtitle="按 approved 日线收盘价计算"
          symbol={performance.symbol}
          title="观察期累计涨跌幅走势"
          zeroLineLabel="零涨跌幅"
        />
        <section className="stock-performance-table-section" aria-labelledby={tableId}>
          <div className="stock-performance-table-heading">
            <div>
              <h4 id={tableId}>逐交易日百分比明细</h4>
              <span>缺失证据不会填为 0% 或沿用前值。</span>
            </div>
            <span>{performance.points.length} 个交易日</span>
          </div>
          {performance.points.length === 0 ? (
            <p className="stock-performance-table-empty">暂无可展示的观察期交易日明细。</p>
          ) : (
            <div className="stock-performance-table-wrap">
              <table className="stock-performance-table">
                <caption className="sr-only">观察期逐交易日百分比明细</caption>
                <thead>
                  <tr>
                    <th scope="col">交易日</th>
                    <th scope="col">状态</th>
                    <th scope="col">收盘价证据</th>
                    <th scope="col">日涨跌幅</th>
                    <th scope="col">观察期累计涨跌幅</th>
                    <th scope="col">行情证据</th>
                  </tr>
                </thead>
                <tbody>
                  {performance.points.map((point) => (
                    <tr className={`stock-performance-row--${point.status}`} key={point.session_date}>
                      <th scope="row">
                        <strong>{point.session_date}</strong>
                        <small>{point.available_at ? formatManagementDate(point.available_at) : '可用时间不可用'}</small>
                      </th>
                      <td><span className={`stock-performance-status stock-performance-status--${point.status}`}>{statusLabel(point.status)}</span></td>
                      <td className="stock-performance-number">{formatPrice(point.close)}</td>
                      <td className={`stock-performance-number ${returnClass(point.daily_return)}`}>{formatPercent(point.daily_return)}</td>
                      <td className={`stock-performance-number ${returnClass(point.cumulative_return)}`}>{formatPercent(point.cumulative_return)}</td>
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
