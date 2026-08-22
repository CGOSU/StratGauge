import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { requestJson } from '@/api/client'
import { readFileAsBase64, toShanghaiDateTime } from '@/lib/format'
import type { Exchange } from '@/api/contracts'
import type {
  Batch,
  BatchPage,
  BatchStats,
  BatchView,
  CandidateAnalysis,
  CandidateModelScores,
  Formula,
  ManualCandidateDraft,
  RecommendationWorkbench,
} from '@/types'


export function useImportData(apiBase: string, enabled: boolean) {
  const [formulas, setFormulas] = useState<Formula[]>([])
  const [selectedFormulaId, setSelectedFormulaId] = useState('')
  const [manualDialogOpen, setManualDialogOpen] = useState(false)
  const [manualFormulaId, setManualFormulaId] = useState('')
  const [manualSelectionDate, setManualSelectionDate] = useState('')
  const [manualRecordedAt, setManualRecordedAt] = useState('')
  const [manualCandidateCode, setManualCandidateCode] = useState('')
  const [manualCandidateExchange, setManualCandidateExchange] = useState<Exchange | ''>('')
  const [manualCandidateName, setManualCandidateName] = useState('')
  const [manualCandidateNote, setManualCandidateNote] = useState('')
  const [manualCandidates, setManualCandidates] = useState<ManualCandidateDraft[]>([])
  const [manualBusy, setManualBusy] = useState(false)
  const [manualError, setManualError] = useState<string | null>(null)
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchPage, setBatchPage] = useState(1)
  const [batchTotal, setBatchTotal] = useState(0)
  const batchPageSize = 50
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [selectedBatch, setSelectedBatch] = useState<BatchView | null>(null)
  const [selectedStats, setSelectedStats] = useState<BatchStats | null>(null)
  const [candidateAnalyses, setCandidateAnalyses] = useState<Record<string, CandidateAnalysis>>({})
  const [analysisLoadingId, setAnalysisLoadingId] = useState<string | null>(null)
  const [candidateModelScores, setCandidateModelScores] = useState<Record<string, CandidateModelScores>>({})
  const [modelScoreLoadingId, setModelScoreLoadingId] = useState<string | null>(null)
  const [recommendationWorkbench, setRecommendationWorkbench] = useState<RecommendationWorkbench | null>(null)
  const [recommendationLoading, setRecommendationLoading] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [selectionDate, setSelectionDate] = useState('')
  const [exportedAt, setExportedAt] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [loadingBatch, setLoadingBatch] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const batchesLoadStarted = useRef(false)
  const formulasLoadStarted = useRef(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const nextManualCandidateId = useRef(0)

  useEffect(() => {
    if (!enabled) {
      return
    }
    if (!batchesLoadStarted.current) {
      batchesLoadStarted.current = true
      void loadBatches()
    }
    if (!formulasLoadStarted.current) {
      formulasLoadStarted.current = true
      void loadFormulas()
    }
  }, [enabled])

  useEffect(() => {
    if (!selectedBatchId) {
      setSelectedBatch(null)
      setSelectedStats(null)
      setCandidateAnalyses({})
      setAnalysisLoadingId(null)
      setCandidateModelScores({})
      setModelScoreLoadingId(null)
      setRecommendationWorkbench(null)
      setRecommendationLoading(false)
      return
    }
    const controller = new AbortController()
    setSelectedBatch(null)
    setSelectedStats(null)
    setCandidateAnalyses({})
    setAnalysisLoadingId(null)
    setCandidateModelScores({})
    setModelScoreLoadingId(null)
    setRecommendationWorkbench(null)
    setRecommendationLoading(false)
    setLoadingBatch(true)
    void Promise.all([
      requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}`,
        { signal: controller.signal },
      ),
      requestJson<BatchStats>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/stats`,
        { signal: controller.signal },
      ),
    ])
      .then(([batch, stats]) => {
        setSelectedBatch(batch)
        setSelectedStats(stats)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载批次详情失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingBatch(false)
        }
      })

    return () => controller.abort()
  }, [selectedBatchId])

  async function loadFormulas(preferredFormulaId?: string) {
    try {
      const result = await requestJson<Formula[]>(`${apiBase}/v1/formulas`)
      setFormulas(result)
      const nextFormulaId = preferredFormulaId ?? selectedFormulaId
      setSelectedFormulaId(
        nextFormulaId && result.some((formula) => formula.formula_id === nextFormulaId)
          ? nextFormulaId
          : result[0]?.formula_id ?? '',
      )
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载选股公式失败。')
    }
  }

  async function loadBatches(preferredBatchId?: string, requestedPage = 1) {
    setLoadingBatches(true)
    const page = preferredBatchId ? 1 : requestedPage
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(batchPageSize) })
      const result = await requestJson<BatchPage>(`${apiBase}/v1/selection-batches/page?${params.toString()}`)
      setBatches(result.items)
      setBatchPage(result.page)
      setBatchTotal(result.total)
      const nextBatchId = preferredBatchId ?? selectedBatchId
      if (nextBatchId && result.items.some((batch) => batch.batch_id === nextBatchId)) {
        setSelectedBatchId(nextBatchId)
      } else if (!selectedBatchId || preferredBatchId || page === 1) {
        setSelectedBatchId(result.items[0]?.batch_id ?? null)
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载选股批次失败。')
    } finally {
      setLoadingBatches(false)
    }
  }

  function clearSelectedFile() {
    setFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  async function importSelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || !selectionDate || !exportedAt || !selectedFormulaId) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      const contentBase64 = await readFileAsBase64(file)
      const result = await requestJson<BatchView>(`${apiBase}/v1/selection-batches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content_base64: contentBase64,
          selection_date: selectionDate,
          exported_at: toShanghaiDateTime(exportedAt),
          source_filename: file.name,
          formula_id: selectedFormulaId,
        }),
      })
      await loadBatches(result.batch.batch_id)
      clearSelectedFile()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '导入失败。')
    } finally {
      setBusy(false)
    }
  }

  async function loadCandidateAnalysis(candidateId: string) {
    if (!selectedBatchId || analysisLoadingId !== null) {
      return
    }
    const analysisKey = `${selectedBatchId}:${candidateId}`
    setAnalysisLoadingId(analysisKey)
    setError(null)
    try {
      const analysis = await requestJson<CandidateAnalysis>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/candidates/${encodeURIComponent(candidateId)}/analysis`,
      )
      setCandidateAnalyses((current) => ({ ...current, [candidateId]: analysis }))
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载历史条件分析失败。')
    } finally {
      setAnalysisLoadingId(null)
    }
  }

  async function loadCandidateModelScores(candidateId: string) {
    if (!selectedBatchId || modelScoreLoadingId !== null) {
      return
    }
    const scoreKey = `${selectedBatchId}:${candidateId}`
    setModelScoreLoadingId(scoreKey)
    setError(null)
    try {
      const scores = await requestJson<CandidateModelScores>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/candidates/${encodeURIComponent(candidateId)}/model-scores`,
      )
      setCandidateModelScores((current) => ({ ...current, [candidateId]: scores }))
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载模型影子证据失败。')
    } finally {
      setModelScoreLoadingId(null)
    }
  }

  async function loadRecommendationWorkbench() {
    if (!selectedBatchId || recommendationLoading) {
      return
    }
    setRecommendationLoading(true)
    setError(null)
    try {
      const workbench = await requestJson<RecommendationWorkbench>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/recommendation-workbench`,
      )
      setRecommendationWorkbench(workbench)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载推荐工作台失败。')
    } finally {
      setRecommendationLoading(false)
    }
  }

  function openManualDialog() {
    const availableFormulaId = formulas.some((formula) => formula.formula_id === selectedFormulaId)
      ? selectedFormulaId
      : formulas[0]?.formula_id ?? ''
    setManualFormulaId(availableFormulaId)
    setManualSelectionDate(selectionDate)
    setManualRecordedAt(exportedAt)
    setManualError(null)
    setManualDialogOpen(true)
  }

  function resetManualForm() {
    setManualFormulaId('')
    setManualSelectionDate('')
    setManualRecordedAt('')
    setManualCandidateCode('')
    setManualCandidateExchange('')
    setManualCandidateName('')
    setManualCandidateNote('')
    setManualCandidates([])
    setManualError(null)
    nextManualCandidateId.current = 0
  }

  function closeManualDialog() {
    if (manualBusy) {
      return
    }
    resetManualForm()
    setManualDialogOpen(false)
  }

  function addManualCandidate() {
    const localCode = manualCandidateCode.trim()
    if (!/^\d{6}$/.test(localCode)) {
      setManualError('股票代码必须是六位数字。')
      return
    }
    if (!manualCandidateExchange) {
      setManualError('请选择交易所，系统不会根据代码前缀猜测交易所。')
      return
    }
    if (
      manualCandidates.some(
        (candidate) =>
          candidate.local_code === localCode && candidate.exchange === manualCandidateExchange,
      )
    ) {
      setManualError(`${localCode}.${manualCandidateExchange} 已在预览中。`)
      return
    }
    nextManualCandidateId.current += 1
    setManualCandidates((current) => [
      ...current,
      {
        id: nextManualCandidateId.current,
        local_code: localCode,
        exchange: manualCandidateExchange,
        name: manualCandidateName.trim(),
        note: manualCandidateNote.trim(),
      },
    ])
    setManualCandidateCode('')
    setManualCandidateExchange('')
    setManualCandidateName('')
    setManualCandidateNote('')
    setManualError(null)
  }

  function handleManualCandidateKeyDown(
    event: KeyboardEvent<HTMLInputElement> | KeyboardEvent<HTMLSelectElement>,
  ) {
    if (event.key === 'Enter') {
      event.preventDefault()
      addManualCandidate()
    }
  }

  function removeManualCandidate(candidateId: number) {
    setManualCandidates((current) => current.filter((candidate) => candidate.id !== candidateId))
  }

  async function createManualBatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!manualFormulaId || !manualSelectionDate || !manualRecordedAt || manualCandidates.length === 0) {
      setManualError('请选择公式、日期和记录时间，并至少加入一条候选。')
      return
    }
    setManualBusy(true)
    setManualError(null)
    try {
      const result = await requestJson<BatchView>(`${apiBase}/v1/selection-batches/manual`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selection_date: manualSelectionDate,
          recorded_at: toShanghaiDateTime(manualRecordedAt),
          formula_id: manualFormulaId,
          candidates: manualCandidates.map((candidate) => ({
            local_code: candidate.local_code,
            exchange: candidate.exchange,
            name: candidate.name || null,
            note: candidate.note || null,
          })),
        }),
      })
      await loadBatches(result.batch.batch_id)
      resetManualForm()
      setManualDialogOpen(false)
    } catch (reason: unknown) {
      setManualError(reason instanceof Error ? reason.message : '创建手工批次失败。')
    } finally {
      setManualBusy(false)
    }
  }

  return {
    formulas,
    selectedFormulaId,
    setSelectedFormulaId,
    loadFormulas,
    manualDialogOpen,
    setManualDialogOpen,
    manualFormulaId,
    setManualFormulaId,
    manualSelectionDate,
    setManualSelectionDate,
    manualRecordedAt,
    setManualRecordedAt,
    manualCandidateCode,
    setManualCandidateCode,
    manualCandidateExchange,
    setManualCandidateExchange,
    manualCandidateName,
    setManualCandidateName,
    manualCandidateNote,
    setManualCandidateNote,
    manualCandidates,
    manualBusy,
    manualError,
    setManualError,
    batches,
    batchPage,
    batchTotal,
    batchPageSize,
    selectedBatchId,
    setSelectedBatchId,
    selectedBatch,
    selectedStats,
    candidateAnalyses,
    analysisLoadingId,
    candidateModelScores,
    modelScoreLoadingId,
    recommendationWorkbench,
    recommendationLoading,
    file,
    setFile,
    selectionDate,
    setSelectionDate,
    exportedAt,
    setExportedAt,
    busy,
    loadingBatches,
    loadingBatch,
    error,
    setError,
    fileInputRef,
    loadBatches,
    clearSelectedFile,
    importSelection,
    loadCandidateAnalysis,
    loadCandidateModelScores,
    loadRecommendationWorkbench,
    openManualDialog,
    resetManualForm,
    closeManualDialog,
    addManualCandidate,
    handleManualCandidateKeyDown,
    removeManualCandidate,
    createManualBatch,
  }
}

export type ImportData = ReturnType<typeof useImportData>
