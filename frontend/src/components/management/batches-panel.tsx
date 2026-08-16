import { Archive, Download, RotateCcw } from 'lucide-react'
import { sourceLabel } from '@/components/management/helpers'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { PaginationControls } from '@/components/pagination-controls'
import type { ManagedBatch } from '@/types'

export function ManagementBatchesPanel({
  apiBase,
  batches,
  batchPage,
  batchPageSize,
  batchTotal,
  loading,
  busyKey,
  onSelectBatch,
  onChangeStatus,
  onPageChange,
}: {
  apiBase: string
  batches: ManagedBatch[]
  batchPage: number
  batchPageSize: number
  batchTotal: number
  loading: boolean
  busyKey: string | null
  onSelectBatch: (batchId: string) => void
  onChangeStatus: (entity: 'formulas' | 'batches', entityId: string, active: boolean) => Promise<void>
  onPageChange: (page: number) => Promise<void> | void
}) {
  return (
    <Card className="management-section panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">导入批次</p>
          <h2>选股数据</h2>
        </div>
        <Badge variant="outline">{batchTotal} 项</Badge>
      </div>
      {batches.length === 0 ? (
        <p className="empty-copy">当前筛选下没有批次。</p>
      ) : (
        <div className="management-record-list">
          {batches.map((item) => {
            const action = item.active ? 'archive' : 'restore'
            const actionKey = `batches:${item.batch.batch_id}:${action}`
            return (
              <article className="management-record" key={item.batch.batch_id}>
                <div className="management-record-heading">
                  <div>
                    <strong>{item.batch.selection_date}</strong>
                    <span>{item.batch.formula_name}</span>
                  </div>
                  <div className="management-badge-group">
                    <Badge
                      variant="outline"
                      className={`source-badge source-badge--${item.batch.source}`}
                    >
                      {sourceLabel(item.batch.source)}
                    </Badge>
                    <Badge
                      variant={item.active ? 'secondary' : 'outline'}
                      className={`lifecycle-badge lifecycle-badge--${item.active ? 'active' : 'archived'}`}
                    >
                      {item.active ? '启用' : '已归档'}
                    </Badge>
                  </div>
                </div>
                <p>{item.batch.source_filename ?? '未记录原始文件名'}</p>
                <div className="management-record-meta">
                  <span>{item.batch.candidate_count} 只候选</span>
                  <span>{item.label_count} 条标注 · {item.outcome_count} 条结果</span>
                </div>
                <div className="management-record-actions">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-controls="management-detail-panel"
                    onClick={() => onSelectBatch(item.batch.batch_id)}
                  >
                    查看详情
                  </Button>
                  <Button asChild type="button" size="sm" variant="ghost">
                    <a
                      href={`${apiBase}/v1/data-management/batches/${encodeURIComponent(item.batch.batch_id)}/raw`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Download className="h-4 w-4" />
                      原始文件
                    </a>
                  </Button>
                  <Button asChild type="button" size="sm" variant="ghost">
                    <a
                      href={`${apiBase}/v1/data-management/batches/${encodeURIComponent(item.batch.batch_id)}/dataset`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Download className="h-4 w-4" />
                      导出 JSONL
                    </a>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busyKey !== null}
                    onClick={() => void onChangeStatus('batches', item.batch.batch_id, item.active)}
                  >
                    {item.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                    {busyKey === actionKey ? '处理中…' : item.active ? '归档' : '恢复'}
                  </Button>
                </div>
              </article>
            )
          })}
        </div>
      )}
      <PaginationControls
        page={batchPage}
        pageSize={batchPageSize}
        total={batchTotal}
        busy={loading}
        onPageChange={onPageChange}
      />
    </Card>
  )
}
