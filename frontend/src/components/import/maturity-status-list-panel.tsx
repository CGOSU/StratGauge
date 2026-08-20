import { useCallback, useEffect, useState } from 'react'
import { requestJson } from '@/api/client'
import { PaginationControls } from '@/components/pagination-controls'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { outcomeLabel } from '@/lib/labels'
import { formatManagementDate, formatPercent } from '@/lib/format'
import type { MaturityStatus, MaturityStatusFilter, MaturityStatusItem, MaturityStatusPage } from '@/types'

const PAGE_SIZE = 8

const outcomeStatusFilters: Array<{ value: MaturityStatus; label: string }> = [
  { value: 'pending', label: '待成熟' },
  { value: 'win', label: '胜' },
  { value: 'loss', label: '负' },
  { value: 'flat', label: '平' },
  { value: 'no_entry', label: '未入场' },
  { value: 'unresolved', label: '未解决' },
]

const statusFilters: Array<{ value: MaturityStatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  ...outcomeStatusFilters,
]

const statusLabels: Record<MaturityStatus, string> = {
  pending: '待成熟',
  win: '胜',
  loss: '负',
  flat: '平',
  no_entry: '未入场',
  unresolved: '未解决',
}

function getStatusLabel(status: MaturityStatus): string {
  return status === 'pending' ? statusLabels.pending : outcomeLabel(status)
}

function getResultDescription(item: MaturityStatusItem): string {
  if (!item.outcome) {
    return '等待固定持有期行情与成熟任务。'
  }
  if (item.status === 'no_entry' || item.status === 'unresolved') {
    return `原因：${item.outcome.reason}`
  }
  if (item.outcome.net_return === null) {
    return `原因：${item.outcome.reason}`
  }
  return `净收益 ${formatPercent(item.outcome.net_return)}`
}

function MaturityStatusRow({ item }: { item: MaturityStatusItem }) {
  return (
    <article className="maturity-status-row" role="listitem">
      <div className="maturity-status-identity">
        <strong>{item.candidate.symbol}</strong>
        <span>{item.candidate.name ?? '未提供名称'}</span>
        <small>
          {item.batch.selection_date} · {item.batch.formula_name}
        </small>
      </div>
      <div className="maturity-status-result">
        <div className="badge-row">
          <Badge
            variant="secondary"
            className={`outcome-badge outcome-badge--${item.status}`}
          >
            {getStatusLabel(item.status)}
          </Badge>
          {!item.active && <Badge className="maturity-origin-badge maturity-origin-badge--archived">已归档</Badge>}
          {item.excluded && <Badge className="maturity-origin-badge maturity-origin-badge--excluded">已排除</Badge>}
        </div>
        <div className="maturity-status-evidence">
          {item.outcome ? (
            <>
              <span>评估于 {formatManagementDate(item.outcome.evaluated_at)}</span>
              <span>跟踪策略 {item.outcome.tracking_policy_id}</span>
            </>
          ) : (
            <span>尚未生成客观结果</span>
          )}
          <strong>{getResultDescription(item)}</strong>
        </div>
      </div>
    </article>
  )
}

export function MaturityStatusListPanel({ apiBase }: { apiBase: string }) {
  const [status, setStatus] = useState<MaturityStatusFilter>('all')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<MaturityStatusPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true)
      setError(null)
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
      if (status !== 'all') {
        params.set('status', status)
      }
      try {
        const response = await requestJson<MaturityStatusPage>(
          `${apiBase}/v1/selection-maturity/page?${params.toString()}`,
          { signal },
        )
        setData(response)
      } catch (reason: unknown) {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '读取成熟结果列表失败。')
      } finally {
        if (!signal?.aborted) {
          setLoading(false)
        }
      }
    },
    [apiBase, page, status],
  )

  useEffect(() => {
    const controller = new AbortController()
    void Promise.resolve().then(() => {
      if (!controller.signal.aborted) {
        return load(controller.signal)
      }
      return undefined
    })
    return () => controller.abort()
  }, [load])

  function handleStatusChange(nextStatus: MaturityStatusFilter) {
    setStatus(nextStatus)
    setPage(1)
  }

  return (
    <Card className="maturity-status-panel" aria-labelledby="maturity-status-title">
      <div className="section-heading maturity-status-heading">
        <div>
          <p className="eyebrow">客观结果索引</p>
          <h2 id="maturity-status-title">成熟结果列表</h2>
          <p className="maturity-status-summary">
            只读展示所有历史候选；归档批次和已排除候选仍保留为证据。
          </p>
        </div>
        <Badge variant="outline">
          {data ? `${data.total} 条当前记录` : '读取中'}
        </Badge>
      </div>

      <div className="maturity-status-toolbar">
        <label className="maturity-status-filter" htmlFor="maturity-status-filter">
          <span>查看状态</span>
          <select
            id="maturity-status-filter"
            className="native-select"
            value={status}
            onChange={(event) => handleStatusChange(event.target.value as MaturityStatusFilter)}
          >
            {statusFilters.map((filter) => (
              <option key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
        </label>
        {data && (
          <div className="maturity-status-counts" aria-label="成熟状态汇总">
            {outcomeStatusFilters.map((filter) => (
              <span key={filter.value}>
                <strong>{data.status_counts[filter.value]}</strong> {filter.label}
              </span>
            ))}
          </div>
        )}
      </div>

      {error ? (
        <div className="maturity-status-error" role="alert">
          <p>{error}</p>
          <Button type="button" size="sm" variant="outline" onClick={() => void load()}>
            重试
          </Button>
        </div>
      ) : loading && !data ? (
        <div className="maturity-status-loading" role="status" aria-label="成熟结果加载中">
          <Skeleton />
          <Skeleton />
          <Skeleton />
        </div>
      ) : data && data.items.length > 0 ? (
        <>
          <div className="maturity-status-list" role="list" aria-live="polite">
            {data.items.map((item) => (
              <MaturityStatusRow
                key={`${item.candidate.candidate_id}:${item.outcome?.tracking_policy_id ?? 'pending'}`}
                item={item}
              />
            ))}
          </div>
          <PaginationControls
            page={data.page}
            pageSize={data.page_size}
            total={data.total}
            busy={loading}
            onPageChange={setPage}
          />
        </>
      ) : (
        <p className="empty-copy">当前状态没有成熟结果记录。</p>
      )}
    </Card>
  )
}
