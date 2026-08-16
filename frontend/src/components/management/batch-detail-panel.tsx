import { Archive, Download, RotateCcw } from 'lucide-react'
import { sourceLabel } from '@/components/management/helpers'
import { decisionLabel, outcomeLabel } from '@/lib/labels'
import { formatManagementDate, latestLabel, latestOutcome } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import type { ManagedBatchDetail } from '@/types'

export function ManagementBatchDetailPanel({
  apiBase,
  selectedBatch,
  detailLoading,
  busyKey,
  onChangeStatus,
}: {
  apiBase: string
  selectedBatch: ManagedBatchDetail | null
  detailLoading: boolean
  busyKey: string | null
  onChangeStatus: (entity: 'formulas' | 'batches', entityId: string, active: boolean) => Promise<void>
}) {
  return (
    <Card
      id="management-detail-panel"
      className="management-detail panel"
      aria-labelledby="management-detail-title"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">证据详情</p>
          <h2 id="management-detail-title">批次内容与生命周期</h2>
        </div>
        {selectedBatch && (
          <div className="management-badge-group">
            <Badge
              variant="outline"
              className={`source-badge source-badge--${selectedBatch.view.batch.source}`}
            >
              {sourceLabel(selectedBatch.view.batch.source)}
            </Badge>
            <Badge variant={selectedBatch.active ? 'secondary' : 'outline'}>
              {selectedBatch.active ? '启用' : '已归档'}
            </Badge>
          </div>
        )}
      </div>
      {detailLoading ? (
        <div className="management-loading">
          <Skeleton />
          <Skeleton />
        </div>
      ) : !selectedBatch ? (
        <p className="empty-copy">选择一个批次查看候选、标签、结果和生命周期事件。</p>
      ) : (
        <>
          <div className="management-detail-summary">
            <div>
              <strong>
                {sourceLabel(selectedBatch.view.batch.source)} ·{' '}
                {selectedBatch.view.batch.source_filename ?? '未记录原始文件名'}
              </strong>
              <span>
                <strong>{selectedBatch.view.batch.selection_date}</strong> · {selectedBatch.view.batch.formula_name}
              </span>
            </div>
            <div className="management-record-actions">
              <Button asChild type="button" size="sm" variant="outline">
                <a
                  href={`${apiBase}/v1/data-management/batches/${encodeURIComponent(selectedBatch.view.batch.batch_id)}/raw`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download className="h-4 w-4" />
                  下载原始文件
                </a>
              </Button>
              <Button asChild type="button" size="sm" variant="outline">
                <a
                  href={`${apiBase}/v1/data-management/batches/${encodeURIComponent(selectedBatch.view.batch.batch_id)}/dataset`}
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
                onClick={() =>
                  void onChangeStatus('batches', selectedBatch.view.batch.batch_id, selectedBatch.active)
                }
              >
                {selectedBatch.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                {selectedBatch.active ? '归档批次' : '恢复批次'}
              </Button>
            </div>
          </div>
          <div className="management-candidate-list">
            {selectedBatch.view.candidates.map(({ candidate, labels, outcomes }) => {
              const label = latestLabel(labels)
              const outcome = latestOutcome(outcomes)
              return (
                <div className="management-candidate" key={candidate.candidate_id}>
                  <div>
                    <strong>{candidate.symbol}</strong>
                    <span>{candidate.name ?? '未提供名称'}</span>
                  </div>
                  <div className="badge-row">
                    <Badge variant={label ? 'secondary' : 'outline'} className={`decision-badge decision-badge--${label?.decision ?? 'unlabeled'}`}>
                      {label ? decisionLabel(label.decision) : '未标注'}
                    </Badge>
                    <Badge variant={outcome ? 'secondary' : 'outline'} className={`outcome-badge outcome-badge--${outcome?.status ?? 'pending'}`}>
                      {outcome ? outcomeLabel(outcome.status) : '结果待跟踪'}
                    </Badge>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="lifecycle-list">
            <strong>生命周期历史</strong>
            {selectedBatch.lifecycle.length === 0 ? (
              <span>尚未发生停用或恢复操作。</span>
            ) : (
              selectedBatch.lifecycle.map((event) => (
                <span key={event.event_id}>
                  {event.status === 'active' ? '恢复启用' : '归档停用'} · {formatManagementDate(event.changed_at)}
                  {event.reason ? ` · ${event.reason}` : ''}
                </span>
              ))
            )}
          </div>
        </>
      )}
    </Card>
  )
}
