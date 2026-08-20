import { apiBase } from '@/lib/api'
import { Plus, Upload, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { CalendarInput } from '@/components/calendar-input'
import { ErrorAlert } from '@/components/error-alert'
import { PaginationControls } from '@/components/pagination-controls'
import { MainBoardMarketSyncPanel } from '@/components/main-board-market-sync-panel'
import { MlReadinessPanel } from '@/components/ml-readiness-panel'
import { MaturityStatusListPanel } from '@/components/import/maturity-status-list-panel'
import { sourceLabel } from '@/components/management/helpers'
import { formatFileSize } from '@/lib/format'
import type { HealthState } from '@/types'
import type { ImportData } from '@/hooks/use-import-data'
import { ManualCandidateDialog } from '@/components/import/manual-candidate-dialog'
import { CandidatePanel } from '@/components/import/candidate-panel'

export function ImportWorkspace({ data, healthState }: { data: ImportData; healthState: HealthState }) {
  const {
    error,
    setError,
    openManualDialog,
    manualBusy,
    file,
    setFile,
    busy,
    fileInputRef,
    clearSelectedFile,
    importSelection,
    selectionDate,
    setSelectionDate,
    exportedAt,
    setExportedAt,
    formulas,
    selectedFormulaId,
    setSelectedFormulaId,
    batches,
    batchPage,
    batchTotal,
    batchPageSize,
    loadingBatches,
    selectedBatchId,
    setSelectedBatchId,
    loadBatches,
  } = data
  return (
    <>
          <Card className="status-card" aria-labelledby="status-title">
        <div>
          <p className="eyebrow">半手动研究闭环</p>
          <h2 id="status-title">候选队列已就绪</h2>
          <p>导入通达信收盘后结果，人工标注后再由系统跟踪后续行情。</p>
        </div>
        <div className="status-actions">
          <Badge variant="secondary">仅手动交易</Badge>
          <Badge
            variant={healthState === 'offline' ? 'destructive' : 'secondary'}
            className={`health-status health-status--${healthState}`}
            role="status"
            aria-live="polite"
          >
            API：{healthState === 'checking' ? '检测中' : healthState === 'online' ? '在线' : '离线'}
          </Badge>
        </div>
      </Card>

      {error && <ErrorAlert message={error} onDismiss={() => setError(null)} />}

      <div className="import-page-content">
        <div className="import-main-flow">
          <section className="research-layout" aria-label="通达信选股研究工作区">
        <Card className="import-panel panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">01 · 导入</p>
              <h2>新建选股批次</h2>
            </div>
            <Button
              type="button"
              variant="outline"
              disabled={manualBusy}
              aria-haspopup="dialog"
              onClick={openManualDialog}
            >
              <Plus className="h-4 w-4" />
              手动添加候选
            </Button>
          </div>
          <ManualCandidateDialog data={data} />
          <form className="import-form" onSubmit={importSelection}>
            <div className="field">
              <Label htmlFor="selection-file">通达信导出文件</Label>
              <div className={`file-upload${file ? ' file-upload--selected' : ''}`}>
                <div className="file-upload-icon" aria-hidden="true">
                  <Upload className="h-5 w-5" />
                </div>
                <div className="file-upload-copy" aria-live="polite">
                  <strong>{file ? file.name : '选择通达信导出文件'}</strong>
                  <small>
                    {file ? `${formatFileSize(file.size)} · 已准备导入` : '支持 .EBK、CSV、TSV、TXT'}
                  </small>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant={file ? 'outline' : 'default'}
                  className="file-upload-action"
                  disabled={busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {file ? '更换文件' : '选择文件'}
                </Button>
                {file && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="file-upload-clear"
                    aria-label={`清除已选文件 ${file.name}`}
                    disabled={busy}
                    onClick={clearSelectedFile}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </Button>
                )}
                <Input
                  ref={fileInputRef}
                  id="selection-file"
                  type="file"
                  className="file-upload-input"
                  accept=".ebk,.EBK,.csv,.tsv,.txt,text/csv,text/plain"
                  aria-describedby="selection-file-help"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </div>
              <small id="selection-file-help">文件导入后会保留原始内容证据，建议使用收盘后导出的文件。</small>
            </div>
            <div className="field">
              <Label htmlFor="selection-date">选股日期</Label>
              <CalendarInput
                id="selection-date"
                value={selectionDate}
                onChange={setSelectionDate}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="exported-at">导出时间（北京时间）</Label>
              <CalendarInput
                id="exported-at"
                type="datetime-local"
                value={exportedAt}
                onChange={setExportedAt}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="formula-select">选股公式</Label>
              <div className="formula-picker">
                <Select value={selectedFormulaId} onValueChange={setSelectedFormulaId}>
                  <SelectTrigger id="formula-select">
                    <SelectValue placeholder={formulas.length ? '选择已创建公式' : '请到数据管理新建公式'} />
                  </SelectTrigger>
                  <SelectContent>
                    {formulas.map((formula) => (
                      <SelectItem key={formula.formula_id} value={formula.formula_id}>
                        {formula.name}{formula.version ? ` · ${formula.version}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {!formulas.length && <small>请先在“数据管理”中创建公式。</small>}
              {selectedFormulaId && (
                <small>
                  {formulas.find((formula) => formula.formula_id === selectedFormulaId)?.description ?? '已选择公式版本'}
                </small>
              )}
            </div>
            <Button type="submit" disabled={busy || !file || !selectedFormulaId}>
              {busy ? '导入中…' : '导入选股结果'}
            </Button>
          </form>
        </Card>

        <Card className="batch-panel panel" aria-labelledby="batch-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">02 · 批次</p>
              <h2 id="batch-title">选股批次</h2>
            </div>
            <Badge variant="outline">{batchTotal} 个批次</Badge>
          </div>
          {loadingBatches ? (
            <div className="skeleton-list" aria-label="批次加载中">
              <Skeleton />
              <Skeleton />
              <Skeleton />
            </div>
          ) : batches.length === 0 ? (
            <p className="empty-copy">还没有导入批次。先从左侧导入通达信结果。</p>
          ) : (
            <>
              <div className="batch-list" role="list">
              {batches.map((batch) => (
                <Button
                  variant="ghost"
                  className={`batch-item${batch.batch_id === selectedBatchId ? ' batch-item--selected' : ''}`}
                  type="button"
                  key={batch.batch_id}
                  onClick={() => setSelectedBatchId(batch.batch_id)}
                  aria-pressed={batch.batch_id === selectedBatchId}
                >
                  <span>
                    <strong className="batch-date">{batch.selection_date}</strong>
                    <small>
                      {batch.formula_name} · {sourceLabel(batch.source)} · {batch.source_filename ?? '未记录文件名'}
                    </small>
                  </span>
                  <span className="batch-count">{batch.candidate_count} 只</span>
                </Button>
              ))}
            </div>
              <PaginationControls
                page={batchPage}
                pageSize={batchPageSize}
                total={batchTotal}
                busy={loadingBatches}
                onPageChange={(page) => void loadBatches(undefined, page)}
              />
            </>
          )}
        </Card>
          </section>
          <CandidatePanel data={data} />
        </div>

        <section className="import-support-panels" aria-label="研究运行状态">
          <MlReadinessPanel apiBase={apiBase} />
          <MaturityStatusListPanel apiBase={apiBase} />
          <MainBoardMarketSyncPanel apiBase={apiBase} />
        </section>

        <section className="principles" aria-labelledby="principles-title">
          <h2 id="principles-title">当前边界</h2>
          <ul>
            <li>收盘后导入，最早下一交易日跟踪。</li>
            <li>人工标签与客观行情结果分开保存。</li>
            <li>系统不连接券商，也不会自动下单。</li>
          </ul>
        </section>
      </div>
    </>
  )
}
