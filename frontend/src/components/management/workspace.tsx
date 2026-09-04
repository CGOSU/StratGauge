import { useEffect, useRef, useState } from 'react'
import { requestJson } from '@/api/client'
import { ErrorAlert } from '@/components/error-alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  managementFilterLabel,
  matchesManagementFilter,
  matchesSourceFilter,
  sourceFilterLabel,
} from '@/components/management/helpers'
import { ManagementStatsPanel } from '@/components/management/stats-panel'
import { ManagementTagsPanel } from '@/components/management/tags-panel'
import { ManagementFormulaPanel } from '@/components/management/formula-panel'
import { ManagementBatchesPanel } from '@/components/management/batches-panel'
import { ManagementBatchDetailPanel } from '@/components/management/batch-detail-panel'
import type {
  ManagementFilter,
  ManagementSourceFilter,
  ManagedBatch,
  ManagedBatchDetail,
  ManagedBatchPage,
  ManagedFormula,
  ManagedReusableTag,
} from '@/types'

export function DataManagementWorkspace({
  apiBase,
  onFormulaCreated,
}: {
  apiBase: string
  onFormulaCreated: () => void
}) {
  const [formulas, setFormulas] = useState<ManagedFormula[]>([])
  const [batches, setBatches] = useState<ManagedBatch[]>([])
  const [batchPage, setBatchPage] = useState(1)
  const [batchTotal, setBatchTotal] = useState(0)
  const batchPageSize = 50
  const [filter, setFilter] = useState<ManagementFilter>('all')
  const [sourceFilter, setSourceFilter] = useState<ManagementSourceFilter>('all')
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [selectedBatch, setSelectedBatch] = useState<ManagedBatchDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [hasLoadedManagement, setHasLoadedManagement] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [managedTags, setManagedTags] = useState<ManagedReusableTag[]>([])
  const [error, setError] = useState<string | null>(null)
  const managementController = useRef<AbortController | null>(null)

  async function loadManagement(requestedPage = 1) {
    managementController.current?.abort()
    const controller = new AbortController()
    managementController.current = controller
    setLoading(true)
    try {
      const batchParams = new URLSearchParams({ page: String(requestedPage), page_size: String(batchPageSize) })
      const signal = { signal: controller.signal }
      const [formulaResult, batchResult, tagResult] = await Promise.all([
        requestJson<ManagedFormula[]>(`${apiBase}/v1/data-management/formulas`, signal),
        requestJson<ManagedBatchPage>(`${apiBase}/v1/data-management/batches/page?${batchParams.toString()}`, signal),
        requestJson<ManagedReusableTag[]>(`${apiBase}/v1/data-management/annotation-tags`, signal),
      ])
      if (controller.signal.aborted) {
        return
      }
      setFormulas(formulaResult)
      setBatches(batchResult.items)
      setBatchPage(batchResult.page)
      setBatchTotal(batchResult.total)
      setManagedTags(tagResult)
      setHasLoadedManagement(true)
      setSelectedBatchId((current) => {
        if (current && batchResult.items.some((item) => item.batch.batch_id === current)) {
          return current
        }
        return null
      })
    } catch (reason: unknown) {
      if (reason instanceof DOMException && reason.name === 'AbortError') {
        return
      }
      setError(reason instanceof Error ? reason.message : '加载数据管理失败。')
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    void loadManagement()
    return () => managementController.current?.abort()
  }, [])

  useEffect(() => {
    if (!selectedBatchId) {
      setSelectedBatch(null)
      return
    }
    const controller = new AbortController()
    setDetailLoading(true)
    void requestJson<ManagedBatchDetail>(
      `${apiBase}/v1/data-management/batches/${encodeURIComponent(selectedBatchId)}`,
      { signal: controller.signal },
    )
      .then(setSelectedBatch)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载批次详情失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDetailLoading(false)
        }
      })
    return () => controller.abort()
  }, [apiBase, selectedBatchId])

  useEffect(() => {
    if (!selectedBatchId) {
      return
    }
    const selectedItem = batches.find((item) => item.batch.batch_id === selectedBatchId)
    if (
      selectedItem &&
      matchesManagementFilter(selectedItem.active, filter) &&
      matchesSourceFilter(selectedItem.batch.source, sourceFilter)
    ) {
      return
    }
    const nextItem = batches.find(
      (item) =>
        matchesManagementFilter(item.active, filter) &&
        matchesSourceFilter(item.batch.source, sourceFilter),
    )
    setSelectedBatchId(nextItem?.batch.batch_id ?? null)
  }, [batches, filter, selectedBatchId, sourceFilter])

  async function changeStatus(
    entity: 'formulas' | 'batches',
    entityId: string,
    active: boolean,
  ) {
    const action = active ? 'archive' : 'restore'
    const key = `${entity}:${entityId}:${action}`
    setBusyKey(key)
    setError(null)
    try {
      await requestJson(
        `${apiBase}/v1/data-management/${entity}/${encodeURIComponent(entityId)}/${action}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: active ? 'Web 数据管理操作' : 'Web 数据管理恢复' }),
        },
      )
      await loadManagement()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '更新数据状态失败。')
    } finally {
      setBusyKey(null)
    }
  }

  function selectManagedBatch(batchId: string) {
    setSelectedBatchId(batchId)
    requestAnimationFrame(() => {
      document.getElementById('management-detail-panel')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    })
  }

  const formulaVersions = Array.from(
    new Set(batches.map((item) => item.batch.formula_version).filter((value): value is string => Boolean(value))),
  ).sort()
  const visibleFormulas = formulas.filter((item) => matchesManagementFilter(item.active, filter))
  const visibleBatches = batches.filter(
    (item) =>
      matchesManagementFilter(item.active, filter) &&
      matchesSourceFilter(item.batch.source, sourceFilter),
  )

  return (
    <section className="management-workspace" aria-label="数据管理工作区">
      <div className="management-intro">
        <div>
          <p className="eyebrow">数据管理</p>
          <h2>管理公式、批次和原始证据</h2>
          <p>停用只改变研究页面的可见性，不删除原始导出、标签历史或客观结果。</p>
        </div>
        <div className="management-filters">
          <div className="management-filter" role="group" aria-label="数据状态筛选">
            {(['all', 'active', 'archived'] as ManagementFilter[]).map((value) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={filter === value ? 'default' : 'outline'}
                onClick={() => setFilter(value)}
              >
                {managementFilterLabel(value)}
              </Button>
            ))}
          </div>
          <div className="management-filter" role="group" aria-label="批次来源筛选">
            {(['all', 'tongdaxin', 'manual'] as ManagementSourceFilter[]).map((value) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={sourceFilter === value ? 'default' : 'outline'}
                onClick={() => setSourceFilter(value)}
              >
                {sourceFilterLabel(value)}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {error && <ErrorAlert message={error} onDismiss={() => setError(null)} />}

      <ManagementStatsPanel
        apiBase={apiBase}
        filter={filter}
        sourceFilter={sourceFilter}
        formulaVersions={formulaVersions}
        onError={setError}
      />

      {loading && !hasLoadedManagement ? (
        <div className="management-loading">
          <Skeleton />
          <Skeleton />
          <Skeleton />
        </div>
      ) : (
        <>
          <div className="management-grid">
            <ManagementTagsPanel
              apiBase={apiBase}
              tags={managedTags}
              onChanged={loadManagement}
              onError={setError}
            />

            <ManagementFormulaPanel
              apiBase={apiBase}
              formulas={visibleFormulas}
              busyKey={busyKey}
              onChanged={loadManagement}
              onFormulaCreated={onFormulaCreated}
              onChangeStatus={changeStatus}
              onError={setError}
            />

            <ManagementBatchesPanel
              apiBase={apiBase}
              batches={visibleBatches}
              batchPage={batchPage}
              batchPageSize={batchPageSize}
              batchTotal={batchTotal}
              loading={loading}
              busyKey={busyKey}
              onSelectBatch={selectManagedBatch}
              onChangeStatus={changeStatus}
              onPageChange={(page) => loadManagement(page)}
            />
          </div>

          <ManagementBatchDetailPanel
            apiBase={apiBase}
            selectedBatch={selectedBatch}
            detailLoading={detailLoading}
            busyKey={busyKey}
            onChangeStatus={changeStatus}
          />
        </>
      )}
    </section>
  )
}
