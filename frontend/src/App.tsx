import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { Archive, Database, Download, LayoutDashboard, Pencil, Plus, RotateCcw } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'

type HealthState = 'checking' | 'online' | 'offline'
type Decision = 'buy_candidate' | 'watch' | 'pass' | 'invalid'
type OutcomeStatus = 'win' | 'loss' | 'flat' | 'no_entry' | 'unresolved'
type Workspace = 'research' | 'management'
type LifecycleStatus = 'active' | 'archived'
type ManagementFilter = 'all' | LifecycleStatus
type BatchSource = 'tongdaxin' | 'manual'
type ManagementSourceFilter = 'all' | BatchSource
type Exchange = 'SH' | 'SZ' | 'BJ'
type Provider = 'akshare' | 'baostock' | 'waizao'
type MaturityTaskStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed'

type Formula = {
  formula_id: string
  name: string
  version: string | null
  description: string | null
  created_at: string
}

type Batch = {
  batch_id: string
  source: BatchSource
  formula_id: string | null
  source_filename: string | null
  selection_date: string
  exported_at: string
  imported_at: string
  formula_name: string
  formula_version: string | null
  formula_sha256: string | null
  source_file_sha256: string
  candidate_count: number
}

type Outcome = {
  status: OutcomeStatus
  evaluated_at: string
  net_return: string | null
  reason: string
}

type Label = {
  candidate_id: string
  decision: Decision
  labeled_at: string
  note: string | null
}

type Candidate = {
  candidate_id: string
  batch_id: string
  local_code: string
  symbol: string
  exchange: Exchange
  name: string | null
  source_fields: Record<string, string>
}

type ManualCandidateDraft = {
  id: number
  local_code: string
  exchange: Exchange
  name: string
  note: string
}

type CandidateView = {
  candidate: Candidate
  labels: Label[]
  outcomes: Outcome[]
}

type BatchView = {
  batch: Batch
  candidates: CandidateView[]
}

type StatsSummary = {
  total: number
  completed: number
  wins: number
  losses: number
  flats: number
  no_entry: number
  unresolved: number
  win_rate: string | null
  average_net_return: string | null
}

type BatchStats = {
  batch_id: string
  candidate_count: number
  pending: number
  latest_outcome_count: number
  summary: StatsSummary
}

type LifecycleEvent = {
  event_id: string
  entity_type: 'formula' | 'batch'
  entity_id: string
  status: LifecycleStatus
  changed_at: string
  reason: string | null
}

type ManagedFormula = {
  formula: Formula
  active: boolean
  lifecycle: LifecycleEvent[]
}

type ManagedBatch = {
  batch: Batch
  active: boolean
  label_count: number
  outcome_count: number
  lifecycle: LifecycleEvent[]
}

type ManagedBatchDetail = {
  view: BatchView
  active: boolean
  lifecycle: LifecycleEvent[]
}

type ManagementStats = {
  batch_count: number
  candidate_count: number
  pending: number
  sample_count: number
  tracking_policy_ids: string[]
  summary: StatsSummary
}

type MaturityTaskEvent = {
  event_id?: string
  event_type?: string
  status?: MaturityTaskStatus
  occurred_at?: string
  created_at?: string
  message?: string | null
  reason?: string | null
  failure_reason?: string | null
  retryable?: boolean
}

type MaturityTask = {
  task_id: string
  batch_id: string
  provider: Provider
  evaluated_at: string
  target_evaluation_date: string
  tracking_policy_id: string
  horizon_sessions: number
  position_notional: string | number
  max_retries: number
  timeout_seconds: number
  status: MaturityTaskStatus
  attempt_count: number
  outcomes_written: number
  rejected_candidates: Array<[string, string]>
  market_snapshot_ids: string[]
  failure_reason: string | null
  retryable: boolean
  created_at: string
  updated_at: string
  events: MaturityTaskEvent[]
}

type ApiErrorPayload = {
  error?: { message?: string }
}

const apiBase = import.meta.env.VITE_API_BASE_URL
  ? import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '')
  : '/api'
const healthUrl = `${apiBase}/healthz`

const providerLabels: Record<Provider, string> = {
  akshare: 'AKShare（主源）',
  baostock: 'BaoStock（备用）',
  waizao: '歪枣网（临时恢复源）',
}

const exchangeLabels: Record<Exchange, string> = {
  SH: '上海 SH',
  SZ: '深圳 SZ',
  BJ: '北京 BJ',
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const payload = (await response.json()) as T | ApiErrorPayload
  if (!response.ok) {
    const error = payload as ApiErrorPayload
    throw new Error(error.error?.message ?? `请求失败：${response.status}`)
  }
  return payload as T
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string' || !result.includes(',')) {
        reject(new Error('无法读取导出文件。'))
        return
      }
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => reject(new Error('读取导出文件失败。'))
    reader.readAsDataURL(file)
  })
}

function toShanghaiDateTime(value: string): string {
  return `${value}:00+08:00`
}

function latestLabel(labels: Label[]): Label | null {
  return labels.length === 0 ? null : labels[labels.length - 1]
}

function latestOutcome(outcomes: Outcome[]): Outcome | null {
  return outcomes.length === 0 ? null : outcomes[outcomes.length - 1]
}

function App() {
  const [workspace, setWorkspace] = useState<Workspace>('research')
  const [healthState, setHealthState] = useState<HealthState>('checking')
  const [formulas, setFormulas] = useState<Formula[]>([])
  const [selectedFormulaId, setSelectedFormulaId] = useState('')
  const [newFormulaName, setNewFormulaName] = useState('')
  const [newFormulaVersion, setNewFormulaVersion] = useState('')
  const [newFormulaDescription, setNewFormulaDescription] = useState('')
  const [formulaDialogOpen, setFormulaDialogOpen] = useState(false)
  const [formulaBusy, setFormulaBusy] = useState(false)
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
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [selectedBatch, setSelectedBatch] = useState<BatchView | null>(null)
  const [selectedStats, setSelectedStats] = useState<BatchStats | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [selectionDate, setSelectionDate] = useState('')
  const [exportedAt, setExportedAt] = useState('')
  const [provider, setProvider] = useState<Provider>('baostock')
  const [evaluatedAt, setEvaluatedAt] = useState('')
  const [horizonSessions, setHorizonSessions] = useState('5')
  const [positionNotional, setPositionNotional] = useState('10000')
  const [maxRetries, setMaxRetries] = useState('2')
  const [timeoutSeconds, setTimeoutSeconds] = useState('900')
  const [maturityTasks, setMaturityTasks] = useState<MaturityTask[]>([])
  const [maturityTaskAction, setMaturityTaskAction] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [batchReloadToken, setBatchReloadToken] = useState(0)
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [loadingBatch, setLoadingBatch] = useState(false)
  const [loadingMaturityTasks, setLoadingMaturityTasks] = useState(false)
  const [labelingId, setLabelingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const batchesLoadStarted = useRef(false)
  const formulasLoadStarted = useRef(false)
  const workspaceInitialized = useRef(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const nextManualCandidateId = useRef(0)

  useEffect(() => {
    const controller = new AbortController()

    void fetch(healthUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Health check failed: ${response.status}`)
        }
        setHealthState('online')
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setHealthState('offline')
      })

    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (batchesLoadStarted.current) {
      return
    }
    batchesLoadStarted.current = true
    void loadBatches()
  }, [])

  useEffect(() => {
    if (formulasLoadStarted.current) {
      return
    }
    formulasLoadStarted.current = true
    void loadFormulas()
  }, [])

  useEffect(() => {
    if (!workspaceInitialized.current) {
      workspaceInitialized.current = true
      return
    }
    if (workspace === 'research') {
      void loadBatches()
      void loadFormulas()
    }
  }, [workspace])

  useEffect(() => {
    if (!selectedBatchId) {
      setSelectedBatch(null)
      setSelectedStats(null)
      setMaturityTasks([])
      setLoadingMaturityTasks(false)
      return
    }
    const controller = new AbortController()
    setSelectedBatch(null)
    setSelectedStats(null)
    setMaturityTasks([])
    setLoadingBatch(true)
    setLoadingMaturityTasks(true)
    void Promise.all([
      requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}`,
        { signal: controller.signal },
      ),
      requestJson<BatchStats>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/stats`,
        { signal: controller.signal },
      ),
      requestJson<MaturityTask[]>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/maturity-tasks`,
        { signal: controller.signal },
      ),
    ])
      .then(([batch, stats, tasks]) => {
        setSelectedBatch(batch)
        setSelectedStats(stats)
        setMaturityTasks(tasks)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载批次和成熟任务失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingBatch(false)
          setLoadingMaturityTasks(false)
        }
      })

    return () => controller.abort()
  }, [selectedBatchId, batchReloadToken])

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

  async function createFormula(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!newFormulaName.trim()) {
      return
    }
    setFormulaBusy(true)
    setError(null)
    try {
      const result = await requestJson<{ formula: Formula }>(`${apiBase}/v1/formulas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newFormulaName.trim(),
          version: newFormulaVersion.trim() || null,
          description: newFormulaDescription.trim() || null,
        }),
      })
      await loadFormulas(result.formula.formula_id)
      setNewFormulaName('')
      setNewFormulaVersion('')
      setNewFormulaDescription('')
      setFormulaDialogOpen(false)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '新建选股公式失败。')
    } finally {
      setFormulaBusy(false)
    }
  }

  async function loadBatches(preferredBatchId?: string) {
    setLoadingBatches(true)
    try {
      const result = await requestJson<Batch[]>(`${apiBase}/v1/selection-batches`)
      setBatches(result)
      const nextBatchId = preferredBatchId ?? selectedBatchId
      if (nextBatchId && result.some((batch) => batch.batch_id === nextBatchId)) {
        setSelectedBatchId(nextBatchId)
      } else {
        setSelectedBatchId(result[0]?.batch_id ?? null)
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载选股批次失败。')
    } finally {
      setLoadingBatches(false)
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
      setFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '导入失败。')
    } finally {
      setBusy(false)
    }
  }

  async function createAndRunMaturityTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedBatchId || !evaluatedAt || !horizonSessions || !positionNotional) {
      return
    }

    const horizon = Number(horizonSessions)
    const retries = Number(maxRetries)
    const timeout = Number(timeoutSeconds)
    const notional = Number(positionNotional)
    if (!Number.isInteger(horizon) || horizon < 1) {
      setError('持有交易日必须是正整数。')
      return
    }
    if (!Number.isInteger(retries) || retries < 0 || retries > 5) {
      setError('最大重试必须是 0 到 5 之间的整数。')
      return
    }
    if (!Number.isInteger(timeout) || timeout < 1 || timeout > 86400) {
      setError('超时秒数必须是 1 到 86400 之间的整数。')
      return
    }
    if (!Number.isFinite(notional) || notional <= 0) {
      setError('单笔名义金额必须是正数。')
      return
    }

    const batchId = selectedBatchId
    setMaturityTaskAction('create-run')
    setError(null)
    try {
      const createdTask = await requestJson<MaturityTask>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(batchId)}/maturity-tasks`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider,
            evaluated_at: toShanghaiDateTime(evaluatedAt),
            horizon_sessions: horizon,
            position_notional: positionNotional,
            max_retries: retries,
            timeout_seconds: timeout,
          }),
        },
      )
      setMaturityTasks((current) => upsertMaturityTask(current, createdTask))

      const executedTask = await requestJson<MaturityTask>(
        `${apiBase}/v1/maturity-tasks/${encodeURIComponent(createdTask.task_id)}/run`,
        { method: 'POST' },
      )
      setMaturityTasks((current) => upsertMaturityTask(current, executedTask))
      setBatchReloadToken((current) => current + 1)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '创建或执行成熟任务失败。')
    } finally {
      setMaturityTaskAction(null)
    }
  }

  async function refreshMaturityTasks() {
    if (!selectedBatchId || maturityTaskAction !== null) {
      return
    }
    const batchId = selectedBatchId
    setMaturityTaskAction('refresh')
    setError(null)
    try {
      const tasks = await requestJson<MaturityTask[]>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(batchId)}/maturity-tasks`,
      )
      if (selectedBatchId === batchId) {
        setMaturityTasks(tasks)
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '刷新成熟任务失败。')
    } finally {
      setMaturityTaskAction(null)
    }
  }

  async function runMaturityTask(taskId: string) {
    if (!selectedBatchId || maturityTaskAction !== null) {
      return
    }
    const task = maturityTasks.find((item) => item.task_id === taskId)
    if (!task || task.status !== 'queued') {
      return
    }
    setMaturityTaskAction(`run:${taskId}`)
    setError(null)
    try {
      const executedTask = await requestJson<MaturityTask>(
        `${apiBase}/v1/maturity-tasks/${encodeURIComponent(taskId)}/run`,
        { method: 'POST' },
      )
      setMaturityTasks((current) => upsertMaturityTask(current, executedTask))
      setBatchReloadToken((current) => current + 1)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '执行成熟任务失败。')
    } finally {
      setMaturityTaskAction(null)
    }
  }

  async function labelCandidate(candidateId: string, decision: Decision) {
    if (!selectedBatchId) {
      return
    }
    setLabelingId(`${candidateId}:${decision}`)
    setError(null)
    try {
      const result = await requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/labels`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            candidate_id: candidateId,
            decision,
            labeled_at: new Date().toISOString(),
            note: notes[candidateId]?.trim() || null,
          }),
        },
      )
      setSelectedBatch(result)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存标注失败。')
    } finally {
      setLabelingId(null)
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

  return (
    <main className="shell">
      <header className="hero">
        <p className="eyebrow">STRATGAUGE · 策衡</p>
        <h1>衡量优势，以证据交易。</h1>
        <p className="summary">通达信负责选股，系统负责留痕、标注和后续验证。</p>
        <nav className="workspace-nav" aria-label="工作区导航">
          <Button
            type="button"
            variant={workspace === 'research' ? 'default' : 'outline'}
            onClick={() => setWorkspace('research')}
          >
            <LayoutDashboard className="h-4 w-4" />
            研究工作台
          </Button>
          <Button
            type="button"
            variant={workspace === 'management' ? 'default' : 'outline'}
            onClick={() => setWorkspace('management')}
          >
            <Database className="h-4 w-4" />
            数据管理
          </Button>
        </nav>
      </header>

      {workspace === 'management' ? (
        <DataManagementWorkspace apiBase={apiBase} />
      ) : (
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

      {error && (
        <Alert variant="destructive" className="alert">
          <AlertDescription>{error}</AlertDescription>
          <Button type="button" variant="link" size="sm" onClick={() => setError(null)}>
            关闭
          </Button>
        </Alert>
      )}

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
          <Dialog
            open={manualDialogOpen}
            onOpenChange={(open) => {
              if (!open && manualBusy) {
                return
              }
              if (!open) {
                resetManualForm()
              }
              setManualDialogOpen(open)
              if (open) {
                setManualError(null)
              }
            }}
          >
            <DialogContent className="manual-dialog-content">
              <DialogHeader>
                <DialogTitle>手动添加候选</DialogTitle>
                <DialogDescription>
                  明确记录选股批次和交易所，不根据代码前缀猜测市场。提交后会保存为手工来源批次。
                </DialogDescription>
              </DialogHeader>
              <form className="dialog-form manual-dialog-form" onSubmit={createManualBatch}>
                <div className="manual-metadata-fields">
                  <div className="field">
                    <Label htmlFor="manual-formula-select">启用公式</Label>
                    <Select value={manualFormulaId} onValueChange={setManualFormulaId}>
                      <SelectTrigger id="manual-formula-select" aria-describedby="manual-formula-help">
                        <SelectValue placeholder={formulas.length ? '选择启用公式' : '暂无启用公式'} />
                      </SelectTrigger>
                      <SelectContent>
                        {formulas.map((formula) => (
                          <SelectItem key={formula.formula_id} value={formula.formula_id}>
                            {formula.name}{formula.version ? ` · ${formula.version}` : ''}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <small id="manual-formula-help">
                      {formulas.length ? '仅显示当前启用公式。' : '请先新建一个公式。'}
                    </small>
                  </div>
                  <div className="field">
                    <Label htmlFor="manual-selection-date">选股日期</Label>
                    <Input
                      id="manual-selection-date"
                      type="date"
                      value={manualSelectionDate}
                      onChange={(event) => setManualSelectionDate(event.target.value)}
                      required
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="manual-recorded-at">记录时间（北京时间）</Label>
                    <Input
                      id="manual-recorded-at"
                      type="datetime-local"
                      value={manualRecordedAt}
                      onChange={(event) => setManualRecordedAt(event.target.value)}
                      required
                    />
                    <small>提交时会带上 +08:00 时区。</small>
                  </div>
                </div>

                <div className="manual-candidate-entry">
                  <div>
                    <h3>添加一条候选</h3>
                    <p>代码必须为六位数字，交易所必须显式选择。</p>
                  </div>
                  <div className="manual-entry-fields">
                    <div className="field">
                      <Label htmlFor="manual-candidate-code">六位代码</Label>
                      <Input
                        id="manual-candidate-code"
                        type="text"
                        inputMode="numeric"
                        maxLength={6}
                        pattern="[0-9]{6}"
                        value={manualCandidateCode}
                        onChange={(event) => setManualCandidateCode(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="例如 600000"
                        aria-describedby="manual-candidate-help"
                      />
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-exchange">交易所</Label>
                      <select
                        id="manual-candidate-exchange"
                        className="native-select"
                        value={manualCandidateExchange}
                        onChange={(event) => setManualCandidateExchange(event.target.value as Exchange | '')}
                        onKeyDown={handleManualCandidateKeyDown}
                      >
                        <option value="">选择交易所</option>
                        {Object.entries(exchangeLabels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-name">名称（可选）</Label>
                      <Input
                        id="manual-candidate-name"
                        type="text"
                        value={manualCandidateName}
                        onChange={(event) => setManualCandidateName(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="例如 平安银行"
                      />
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-note">备注（可选）</Label>
                      <Input
                        id="manual-candidate-note"
                        type="text"
                        value={manualCandidateNote}
                        onChange={(event) => setManualCandidateNote(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="记录当时的判断"
                      />
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={manualBusy}
                      onClick={addManualCandidate}
                    >
                      加入预览
                    </Button>
                  </div>
                  <small id="manual-candidate-help">按 Enter 也可以加入预览。</small>
                </div>

                {manualError && (
                  <p className="manual-form-error" role="alert">
                    {manualError}
                  </p>
                )}

                <div className="manual-preview" aria-labelledby="manual-preview-title">
                  <div className="manual-preview-heading">
                    <h3 id="manual-preview-title">候选预览</h3>
                    <span>{manualCandidates.length} 条</span>
                  </div>
                  {manualCandidates.length === 0 ? (
                    <p className="empty-copy">尚未加入候选，至少添加一条后才能提交。</p>
                  ) : (
                    <div className="manual-preview-list">
                      {manualCandidates.map((candidate) => (
                        <article className="manual-preview-row" key={candidate.id}>
                          <div className="manual-preview-identity">
                            <strong>{candidate.local_code}.{candidate.exchange}</strong>
                            <span>{candidate.name || '未提供名称'}</span>
                          </div>
                          <span className="manual-preview-note">{candidate.note || '未填写备注'}</span>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={manualBusy}
                            aria-label={`移除 ${candidate.local_code}.${candidate.exchange}`}
                            onClick={() => removeManualCandidate(candidate.id)}
                          >
                            移除
                          </Button>
                        </article>
                      ))}
                    </div>
                  )}
                </div>

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={manualBusy}
                    onClick={closeManualDialog}
                  >
                    取消
                  </Button>
                  <Button
                    type="submit"
                    disabled={
                      manualBusy ||
                      !manualFormulaId ||
                      !manualSelectionDate ||
                      !manualRecordedAt ||
                      manualCandidates.length === 0
                    }
                  >
                    {manualBusy ? '提交中…' : '提交手工批次'}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          <form className="import-form" onSubmit={importSelection}>
            <div className="field">
              <Label htmlFor="selection-file">通达信导出文件</Label>
              <Input
                ref={fileInputRef}
                id="selection-file"
                type="file"
                accept=".ebk,.EBK,.csv,.tsv,.txt,text/csv,text/plain"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <small>{file ? file.name : '支持 .EBK 或 UTF-8 / GB18030 的 CSV、TSV、TXT'}</small>
            </div>
            <div className="field">
              <Label htmlFor="selection-date">选股日期</Label>
              <Input
                id="selection-date"
                type="date"
                value={selectionDate}
                onChange={(event) => setSelectionDate(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="exported-at">导出时间（北京时间）</Label>
              <Input
                id="exported-at"
                type="datetime-local"
                value={exportedAt}
                onChange={(event) => setExportedAt(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="formula-select">选股公式</Label>
              <div className="formula-picker">
                <Select value={selectedFormulaId} onValueChange={setSelectedFormulaId}>
                  <SelectTrigger id="formula-select">
                    <SelectValue placeholder={formulas.length ? '选择已创建公式' : '请先新建公式'} />
                  </SelectTrigger>
                  <SelectContent>
                    {formulas.map((formula) => (
                      <SelectItem key={formula.formula_id} value={formula.formula_id}>
                        {formula.name}{formula.version ? ` · ${formula.version}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Dialog open={formulaDialogOpen} onOpenChange={setFormulaDialogOpen}>
                  <DialogTrigger asChild>
                    <Button type="button" variant="outline" size="icon" aria-label="新建选股公式">
                      <Plus className="h-4 w-4" />
                    </Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>新建选股公式</DialogTitle>
                      <DialogDescription>只保存公式名称和版本留痕，公式仍由通达信执行。</DialogDescription>
                    </DialogHeader>
                    <form className="dialog-form" onSubmit={createFormula}>
                      <div className="field">
                        <Label htmlFor="new-formula-name">公式名称</Label>
                        <Input
                          id="new-formula-name"
                          value={newFormulaName}
                          onChange={(event) => setNewFormulaName(event.target.value)}
                          placeholder="输入公式名称"
                          required
                        />
                      </div>
                      <div className="field">
                        <Label htmlFor="new-formula-version">公式版本（可选）</Label>
                        <Input
                          id="new-formula-version"
                          value={newFormulaVersion}
                          onChange={(event) => setNewFormulaVersion(event.target.value)}
                          placeholder="输入版本（可选）"
                        />
                      </div>
                      <div className="field">
                        <Label htmlFor="new-formula-description">说明（可选）</Label>
                        <Input
                          id="new-formula-description"
                          value={newFormulaDescription}
                          onChange={(event) => setNewFormulaDescription(event.target.value)}
                          placeholder="输入公式说明（可选）"
                        />
                      </div>
                      <DialogFooter>
                        <Button type="submit" disabled={formulaBusy}>
                          {formulaBusy ? '创建中…' : '创建并选择'}
                        </Button>
                      </DialogFooter>
                    </form>
                  </DialogContent>
                </Dialog>
              </div>
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
            <Badge variant="outline">{batches.length} 个批次</Badge>
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
                    <strong>{batch.formula_name}</strong>
                    <small>
                      {batch.selection_date} · {sourceLabel(batch.source)} · {batch.source_filename ?? '未记录文件名'}
                    </small>
                  </span>
                  <span className="batch-count">{batch.candidate_count} 只</span>
                </Button>
              ))}
            </div>
          )}
        </Card>
      </section>

      <Card className="candidate-panel panel" aria-labelledby="candidate-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">03 · 标注</p>
            <h2 id="candidate-title">候选股票</h2>
          </div>
          {selectedBatch && (
            <span className="section-meta">
              {selectedBatch.batch.formula_name} · {selectedBatch.batch.selection_date}
            </span>
          )}
        </div>
        {selectedStats && !loadingBatch && (
          <div className="stats-strip" aria-label="批次统计">
            <div>
              <span>待成熟</span>
              <strong>{selectedStats.pending}</strong>
            </div>
            <div>
              <span>已完成</span>
              <strong>{selectedStats.summary.completed}</strong>
            </div>
            <div>
              <span>胜 / 负 / 平</span>
              <strong>
                {selectedStats.summary.wins} / {selectedStats.summary.losses} / {selectedStats.summary.flats}
              </strong>
            </div>
            <div>
              <span>完成样本胜率</span>
              <strong>{formatPercent(selectedStats.summary.win_rate)}</strong>
            </div>
          </div>
        )}
        {loadingBatch ? (
          <div className="candidate-skeleton" aria-label="候选加载中">
            <Skeleton />
            <Skeleton />
          </div>
        ) : !selectedBatch ? (
          <p className="empty-copy">选择一个批次后查看候选股票。</p>
        ) : (
          <div className="candidate-list">
            {selectedBatch.candidates.map(({ candidate, labels, outcomes }) => {
              const label = latestLabel(labels)
              const outcome = latestOutcome(outcomes)
              return (
                <article className="candidate-row" key={candidate.candidate_id}>
                  <div className="candidate-identity">
                    <strong>{candidate.symbol}</strong>
                    <span>{candidate.name ?? '未提供名称'}</span>
                  </div>
                  <div className="candidate-data">
                    {Object.entries(candidate.source_fields)
                      .filter(([key]) => !['代码', '名称', '市场'].includes(key))
                      .slice(0, 3)
                      .map(([key, value]) => (
                        <span key={key}>
                          {key} {value}
                        </span>
                      ))}
                  </div>
                  <div className="candidate-actions">
                    <div className="badge-row">
                      <Badge variant={label ? 'secondary' : 'outline'} className={`decision-badge decision-badge--${label?.decision ?? 'unlabeled'}`}>
                        {label ? decisionLabel(label.decision) : '未标注'}
                      </Badge>
                      <Badge variant={outcome ? 'secondary' : 'outline'} className={`outcome-badge outcome-badge--${outcome?.status ?? 'pending'}`}>
                        {outcome ? outcomeLabel(outcome.status) : '结果待跟踪'}
                      </Badge>
                    </div>
                    <Input
                      className="note-input"
                      type="text"
                      value={notes[candidate.candidate_id] ?? ''}
                      onChange={(event) =>
                        setNotes((current) => ({
                          ...current,
                          [candidate.candidate_id]: event.target.value,
                        }))
                      }
                      placeholder="备注（可选）"
                      aria-label={`${candidate.symbol} 的标注备注`}
                    />
                    <div className="decision-buttons" aria-label={`${candidate.symbol} 标注操作`}>
                      {(['buy_candidate', 'watch', 'pass', 'invalid'] as Decision[]).map((decision) => (
                        <Button
                          variant="outline"
                          size="sm"
                          className="secondary-button"
                          type="button"
                          key={decision}
                          disabled={labelingId !== null}
                          data-saving={labelingId === `${candidate.candidate_id}:${decision}`}
                          onClick={() => void labelCandidate(candidate.candidate_id, decision)}
                        >
                          {decisionLabel(decision)}
                        </Button>
                      ))}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </Card>

      <Card className="mature-panel panel" aria-labelledby="mature-title">
        <div className="section-heading maturity-section-heading">
          <div>
            <p className="eyebrow">04 · 成熟</p>
            <h2 id="mature-title">跟踪客观结果</h2>
          </div>
          <div className="maturity-task-heading-actions">
            <Badge variant="outline">{maturityTasks.length} 个任务</Badge>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="maturity-action-button"
              disabled={!selectedBatchId || maturityTaskAction !== null || loadingMaturityTasks}
              data-saving={maturityTaskAction === 'refresh'}
              onClick={() => void refreshMaturityTasks()}
            >
              <RotateCcw className="h-4 w-4" />
              {maturityTaskAction === 'refresh' ? '刷新中…' : '刷新任务'}
            </Button>
          </div>
        </div>
        <form className="mature-form" onSubmit={createAndRunMaturityTask}>
          <div className="field">
            <Label htmlFor="provider-select">行情源</Label>
            <Select
              value={provider}
              onValueChange={(value) => setProvider(value as Provider)}
              disabled={maturityTaskAction !== null}
            >
              <SelectTrigger id="provider-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(providerLabels).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="field">
            <Label htmlFor="evaluated-at">评估时间（北京时间）</Label>
            <Input
              id="evaluated-at"
              type="datetime-local"
              value={evaluatedAt}
              onChange={(event) => setEvaluatedAt(event.target.value)}
              disabled={maturityTaskAction !== null}
              required
            />
          </div>
          <div className="field">
            <Label htmlFor="horizon-sessions">持有交易日</Label>
            <Input
              id="horizon-sessions"
              type="number"
              min="1"
              value={horizonSessions}
              onChange={(event) => setHorizonSessions(event.target.value)}
              disabled={maturityTaskAction !== null}
              required
            />
          </div>
          <div className="field">
            <Label htmlFor="position-notional">单笔名义金额</Label>
            <Input
              id="position-notional"
              type="number"
              min="0.01"
              step="0.01"
              value={positionNotional}
              onChange={(event) => setPositionNotional(event.target.value)}
              disabled={maturityTaskAction !== null}
              required
            />
          </div>
          <div className="field">
            <Label htmlFor="max-retries">最大重试次数</Label>
            <Input
              id="max-retries"
              type="number"
              min="0"
              max="5"
              step="1"
              value={maxRetries}
              onChange={(event) => setMaxRetries(event.target.value)}
              disabled={maturityTaskAction !== null}
              required
            />
            <small>默认 2 次，允许 0—5 次。</small>
          </div>
          <div className="field">
            <Label htmlFor="timeout-seconds">超时秒数</Label>
            <Input
              id="timeout-seconds"
              type="number"
              min="1"
              max="86400"
              step="1"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              disabled={maturityTaskAction !== null}
              required
            />
            <small>默认 900 秒，允许 1—86400 秒。</small>
          </div>
          <Button
            type="submit"
            disabled={maturityTaskAction !== null || !selectedBatchId}
            data-saving={maturityTaskAction === 'create-run'}
          >
            {maturityTaskAction === 'create-run'
              ? '创建并执行中…'
              : selectedBatchId
                ? '创建并执行一次'
                : '先选择批次'}
          </Button>
        </form>
        <p className="form-hint">先创建幂等成熟任务，再同步执行一次；不会轮询、连接券商或自动下单。</p>

        {loadingMaturityTasks ? (
          <div className="maturity-task-loading" aria-label="成熟任务加载中">
            <Skeleton />
            <Skeleton />
          </div>
        ) : !selectedBatchId ? (
          <p className="empty-copy">选择一个批次后查看成熟任务。</p>
        ) : maturityTasks.length === 0 ? (
          <p className="empty-copy">当前批次还没有成熟任务。提交上方表单后会立即创建并执行。</p>
        ) : (
          <div className="maturity-task-list" aria-label="成熟任务列表">
            {maturityTasks.map((task) => {
              const failureReason = maturityTaskFailureReason(task)
              const showRetryHint =
                Boolean(failureReason) ||
                task.retryable ||
                task.status === 'failed' ||
                task.status === 'partial'
              const runAction = `run:${task.task_id}`
              return (
                <article className="maturity-task-card" key={task.task_id}>
                  <div className="maturity-task-card-heading">
                    <div className="maturity-task-identity">
                      <strong>{taskProviderLabel(task.provider)}</strong>
                      <span>任务 {task.task_id}</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={`maturity-status maturity-status--${task.status}`}
                      role="status"
                    >
                      {maturityTaskStatusLabel(task.status)}
                    </Badge>
                  </div>
                  <div className="maturity-task-meta">
                    <div>
                      <span>评估时间</span>
                      <strong>{formatManagementDate(task.evaluated_at)}</strong>
                    </div>
                    <div>
                      <span>目标评估日</span>
                      <strong>{task.target_evaluation_date}</strong>
                    </div>
                    <div>
                      <span>持有交易日</span>
                      <strong>{task.horizon_sessions}</strong>
                    </div>
                    <div>
                      <span>单笔名义金额</span>
                      <strong>{formatNotional(task.position_notional)}</strong>
                    </div>
                    <div>
                      <span>尝试次数</span>
                      <strong>{task.attempt_count}</strong>
                    </div>
                    <div>
                      <span>写入 / 拒绝</span>
                      <strong>
                        {task.outcomes_written} / {task.rejected_candidates.length}
                      </strong>
                    </div>
                  </div>
                  <div className="maturity-task-secondary-meta">
                    <span>策略 {task.tracking_policy_id}</span>
                    <span>最大重试 {task.max_retries} 次</span>
                    <span>超时 {task.timeout_seconds} 秒</span>
                    <span>事件 {task.events.length} 条</span>
                    <span>更新于 {formatManagementDate(task.updated_at)}</span>
                  </div>
                  {showRetryHint && (
                    <p className="maturity-task-feedback" role="status">
                      {failureReason && <span>最近错误：{failureReason}</span>}
                      <strong className={task.retryable ? 'retryable' : 'not-retryable'}>
                        {task.retryable ? '可重试' : '不可重试'}
                      </strong>
                    </p>
                  )}
                  {task.status === 'queued' && (
                    <div className="maturity-task-actions">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="maturity-action-button"
                        disabled={maturityTaskAction !== null}
                        data-saving={maturityTaskAction === runAction}
                        onClick={() => void runMaturityTask(task.task_id)}
                      >
                        {maturityTaskAction === runAction ? '执行中…' : '执行一次'}
                      </Button>
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </Card>

      <section className="principles" aria-labelledby="principles-title">
        <h2 id="principles-title">当前边界</h2>
        <ul>
          <li>收盘后导入，最早下一交易日跟踪。</li>
          <li>人工标签与客观行情结果分开保存。</li>
          <li>系统不连接券商，也不会自动下单。</li>
        </ul>
      </section>
        </>
      )}
    </main>
  )
}

function DataManagementWorkspace({ apiBase }: { apiBase: string }) {
  const [formulas, setFormulas] = useState<ManagedFormula[]>([])
  const [batches, setBatches] = useState<ManagedBatch[]>([])
  const [filter, setFilter] = useState<ManagementFilter>('all')
  const [sourceFilter, setSourceFilter] = useState<ManagementSourceFilter>('all')
  const [statsFormulaVersion, setStatsFormulaVersion] = useState('all')
  const [statsDateFrom, setStatsDateFrom] = useState('')
  const [statsDateTo, setStatsDateTo] = useState('')
  const [statsDecision, setStatsDecision] = useState<'all' | Decision>('all')
  const [statsPolicy, setStatsPolicy] = useState('all')
  const [stats, setStats] = useState<ManagementStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [selectedBatch, setSelectedBatch] = useState<ManagedBatchDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [editingFormula, setEditingFormula] = useState<ManagedFormula | null>(null)
  const [editFormulaName, setEditFormulaName] = useState('')
  const [editFormulaVersion, setEditFormulaVersion] = useState('')
  const [editFormulaDescription, setEditFormulaDescription] = useState('')
  const [editFormulaBusy, setEditFormulaBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function loadManagement() {
    setLoading(true)
    try {
      const [formulaResult, batchResult] = await Promise.all([
        requestJson<ManagedFormula[]>(`${apiBase}/v1/data-management/formulas`),
        requestJson<ManagedBatch[]>(`${apiBase}/v1/data-management/batches`),
      ])
      setFormulas(formulaResult)
      setBatches(batchResult)
      setSelectedBatchId((current) => {
        if (current && batchResult.some((item) => item.batch.batch_id === current)) {
          return current
        }
        return batchResult[0]?.batch.batch_id ?? null
      })
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载数据管理失败。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadManagement()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams()
    if (filter !== 'all') {
      params.set('lifecycle', filter)
    }
    if (sourceFilter !== 'all') {
      params.set('source', sourceFilter)
    }
    if (statsFormulaVersion !== 'all') {
      params.set('formula_version', statsFormulaVersion)
    }
    if (statsDateFrom) {
      params.set('selection_date_from', statsDateFrom)
    }
    if (statsDateTo) {
      params.set('selection_date_to', statsDateTo)
    }
    if (statsDecision !== 'all') {
      params.set('manual_decision', statsDecision)
    }
    if (statsPolicy !== 'all') {
      params.set('tracking_policy_id', statsPolicy)
    }
    setStatsLoading(true)
    void requestJson<ManagementStats>(
      `${apiBase}/v1/data-management/stats?${params.toString()}`,
      { signal: controller.signal },
    )
      .then(setStats)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setStats(null)
        setError(reason instanceof Error ? reason.message : '加载管理统计失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setStatsLoading(false)
        }
      })
    return () => controller.abort()
  }, [
    apiBase,
    filter,
    sourceFilter,
    statsFormulaVersion,
    statsDateFrom,
    statsDateTo,
    statsDecision,
    statsPolicy,
  ])

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

  function beginFormulaRevision(item: ManagedFormula) {
    setEditingFormula(item)
    setEditFormulaName(item.formula.name)
    setEditFormulaVersion(item.formula.version ?? '')
    setEditFormulaDescription(item.formula.description ?? '')
    setError(null)
  }

  async function reviseFormula(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editingFormula || !editFormulaName.trim()) {
      return
    }
    const nextName = editFormulaName.trim()
    const nextVersion = editFormulaVersion.trim()
    const nextDescription = editFormulaDescription.trim() || null
    if (
      nextName === editingFormula.formula.name &&
      nextVersion === (editingFormula.formula.version ?? '')
    ) {
      setError('公式版本不可原地编辑，请修改名称或版本号后保存为新版本。')
      return
    }
    setEditFormulaBusy(true)
    setError(null)
    try {
      await requestJson<{ formula: Formula }>(`${apiBase}/v1/formulas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: nextName,
          version: nextVersion || null,
          description: nextDescription,
        }),
      })
      await loadManagement()
      setEditingFormula(null)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存公式新版本失败。')
    } finally {
      setEditFormulaBusy(false)
    }
  }

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

  function resetStatsFilters() {
    setStatsFormulaVersion('all')
    setStatsDateFrom('')
    setStatsDateTo('')
    setStatsDecision('all')
    setStatsPolicy('all')
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
          <p>停用只改变工作台可见性，不删除原始导出、标签历史或客观结果。</p>
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

      {error && (
        <Alert variant="destructive" className="alert">
          <AlertDescription>{error}</AlertDescription>
          <Button type="button" variant="link" size="sm" onClick={() => setError(null)}>
            关闭
          </Button>
        </Alert>
      )}

      <Card className="management-stats-panel panel" aria-labelledby="management-stats-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">统计筛选</p>
            <h2 id="management-stats-title">研究样本概览</h2>
          </div>
          <Button type="button" size="sm" variant="outline" onClick={resetStatsFilters}>
            清空筛选
          </Button>
        </div>
        <div className="management-stats-filters">
          <div className="field">
            <Label htmlFor="stats-formula-version">公式版本</Label>
            <select
              id="stats-formula-version"
              className="native-select"
              value={statsFormulaVersion}
              onChange={(event) => setStatsFormulaVersion(event.target.value)}
            >
              <option value="all">全部版本</option>
              {formulaVersions.map((version) => (
                <option key={version} value={version}>
                  {version}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <Label htmlFor="stats-date-from">选股日期起</Label>
            <Input
              id="stats-date-from"
              type="date"
              value={statsDateFrom}
              onChange={(event) => setStatsDateFrom(event.target.value)}
            />
          </div>
          <div className="field">
            <Label htmlFor="stats-date-to">选股日期止</Label>
            <Input
              id="stats-date-to"
              type="date"
              value={statsDateTo}
              onChange={(event) => setStatsDateTo(event.target.value)}
            />
          </div>
          <div className="field">
            <Label htmlFor="stats-decision">人工决策</Label>
            <select
              id="stats-decision"
              className="native-select"
              value={statsDecision}
              onChange={(event) => setStatsDecision(event.target.value as 'all' | Decision)}
            >
              <option value="all">全部决策</option>
              {(['buy_candidate', 'watch', 'pass', 'invalid'] as Decision[]).map((decision) => (
                <option key={decision} value={decision}>
                  {decisionLabel(decision)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <Label htmlFor="stats-policy">结果政策</Label>
            <select
              id="stats-policy"
              className="native-select"
              value={statsPolicy}
              onChange={(event) => setStatsPolicy(event.target.value)}
            >
              <option value="all">全部政策</option>
              {(stats?.tracking_policy_ids ?? []).map((policyId) => (
                <option key={policyId} value={policyId}>
                  {policyId}
                </option>
              ))}
            </select>
          </div>
        </div>
        {statsLoading && !stats ? (
          <div className="management-loading" aria-label="统计加载中">
            <Skeleton />
          </div>
        ) : stats ? (
          <div className="management-stats-summary" aria-live="polite">
            <div>
              <span>匹配批次</span>
              <strong>{stats.batch_count}</strong>
            </div>
            <div>
              <span>候选样本</span>
              <strong>{stats.candidate_count}</strong>
            </div>
            <div>
              <span>待定数</span>
              <strong>{stats.pending}</strong>
            </div>
            <div>
              <span>结果样本</span>
              <strong>{stats.sample_count}</strong>
            </div>
            <div>
              <span>胜率</span>
              <strong>{formatPercent(stats.summary.win_rate)}</strong>
            </div>
            <div>
              <span>平均净收益</span>
              <strong>{formatPercent(stats.summary.average_net_return)}</strong>
            </div>
          </div>
        ) : null}
      </Card>

      {loading ? (
        <div className="management-loading">
          <Skeleton />
          <Skeleton />
          <Skeleton />
        </div>
      ) : (
        <>
          <div className="management-grid">
            <Card className="management-section panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">公式目录</p>
                  <h2>手动创建的公式</h2>
                </div>
                <Badge variant="outline">{visibleFormulas.length} 项</Badge>
              </div>
              {visibleFormulas.length === 0 ? (
                <p className="empty-copy">当前筛选下没有公式。</p>
              ) : (
                <div className="management-record-list">
                  {visibleFormulas.map((item) => {
                    const action = item.active ? 'archive' : 'restore'
                    const actionKey = `formulas:${item.formula.formula_id}:${action}`
                    return (
                      <article className="management-record" key={item.formula.formula_id}>
                        <div className="management-record-heading">
                          <div>
                            <strong>{item.formula.name}</strong>
                            <span>{item.formula.version ?? '未填写版本'}</span>
                          </div>
                          <Badge
                            variant={item.active ? 'secondary' : 'outline'}
                            className={`lifecycle-badge lifecycle-badge--${item.active ? 'active' : 'archived'}`}
                          >
                            {item.active ? '启用' : '已停用'}
                          </Badge>
                        </div>
                        <p>{item.formula.description ?? '未填写说明'}</p>
                        <div className="management-record-meta">
                          <span>创建于 {formatManagementDate(item.formula.created_at)}</span>
                          <span>生命周期事件 {item.lifecycle.length} 条</span>
                        </div>
                        <div className="management-record-actions">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={editFormulaBusy}
                            onClick={() => beginFormulaRevision(item)}
                          >
                            <Pencil className="h-4 w-4" />
                            编辑为新版本
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busyKey !== null}
                            onClick={() => void changeStatus('formulas', item.formula.formula_id, item.active)}
                          >
                            {item.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                            {busyKey === actionKey ? '处理中…' : item.active ? '停用公式' : '恢复公式'}
                          </Button>
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </Card>

            <Card className="management-section panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">导入批次</p>
                  <h2>选股数据</h2>
                </div>
                <Badge variant="outline">{visibleBatches.length} 项</Badge>
              </div>
              {visibleBatches.length === 0 ? (
                <p className="empty-copy">当前筛选下没有批次。</p>
              ) : (
                <div className="management-record-list">
                  {visibleBatches.map((item) => {
                    const action = item.active ? 'archive' : 'restore'
                    const actionKey = `batches:${item.batch.batch_id}:${action}`
                    return (
                      <article className="management-record" key={item.batch.batch_id}>
                        <div className="management-record-heading">
                          <div>
                            <strong>{item.batch.formula_name}</strong>
                            <span>{item.batch.selection_date}</span>
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
                            onClick={() => setSelectedBatchId(item.batch.batch_id)}
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
                            onClick={() => void changeStatus('batches', item.batch.batch_id, item.active)}
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
            </Card>
          </div>

          <Card className="management-detail panel" aria-labelledby="management-detail-title">
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
                    {selectedBatch.active ? '启用' : '已���档'}
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
                    <span>{selectedBatch.view.batch.formula_name} · {selectedBatch.view.batch.selection_date}</span>
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
                        void changeStatus('batches', selectedBatch.view.batch.batch_id, selectedBatch.active)
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
        </>
      )}

      <Dialog
        open={editingFormula !== null}
        onOpenChange={(open) => {
          if (!open && !editFormulaBusy) {
            setEditingFormula(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑为新版本</DialogTitle>
            <DialogDescription>
              公式目录采用不可变版本。修改名称或版本号后保存，历史批次仍指向原公式。
            </DialogDescription>
          </DialogHeader>
          <form className="dialog-form" onSubmit={reviseFormula}>
            <div className="field">
              <Label htmlFor="edit-formula-name">公式名称</Label>
              <Input
                id="edit-formula-name"
                value={editFormulaName}
                onChange={(event) => setEditFormulaName(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="edit-formula-version">公式版本</Label>
              <Input
                id="edit-formula-version"
                value={editFormulaVersion}
                onChange={(event) => setEditFormulaVersion(event.target.value)}
                placeholder="例如 v2"
              />
              <small>必须修改名称或版本号，不能覆盖原公式记录。</small>
            </div>
            <div className="field">
              <Label htmlFor="edit-formula-description">说明（可选）</Label>
              <Input
                id="edit-formula-description"
                value={editFormulaDescription}
                onChange={(event) => setEditFormulaDescription(event.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={editFormulaBusy || !editFormulaName.trim()}>
                {editFormulaBusy ? '保存中…' : '保存新版本'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function upsertMaturityTask(tasks: MaturityTask[], task: MaturityTask): MaturityTask[] {
  const existingIndex = tasks.findIndex((item) => item.task_id === task.task_id)
  if (existingIndex === -1) {
    return [task, ...tasks]
  }
  return tasks.map((item) => (item.task_id === task.task_id ? task : item))
}

function taskProviderLabel(provider: Provider): string {
  return providerLabels[provider]
}

function maturityTaskStatusLabel(status: MaturityTaskStatus): string {
  const labels: Record<MaturityTaskStatus, string> = {
    queued: '排队中',
    running: '执行中',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
  }
  return labels[status]
}

function maturityTaskFailureReason(task: MaturityTask): string | null {
  const directReason = task.failure_reason?.trim()
  if (directReason) {
    return directReason
  }
  for (const event of [...task.events].reverse()) {
    const eventReason = event.failure_reason?.trim()
    if (eventReason) {
      return eventReason
    }
  }
  return null
}

function formatNotional(value: string | number): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return String(value)
  }
  return numeric.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function matchesManagementFilter(active: boolean, filter: ManagementFilter): boolean {
  return filter === 'all' || (filter === 'active' ? active : !active)
}

function managementFilterLabel(filter: ManagementFilter): string {
  return filter === 'all' ? '全部' : filter === 'active' ? '启用中' : '已停用'
}

function matchesSourceFilter(source: BatchSource, filter: ManagementSourceFilter): boolean {
  return filter === 'all' || source === filter
}

function sourceFilterLabel(filter: ManagementSourceFilter): string {
  return filter === 'all' ? '全部' : sourceLabel(filter)
}

function sourceLabel(source: BatchSource): string {
  return source === 'manual' ? '手工' : '通达信'
}

function formatManagementDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function formatPercent(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const percentage = Number(value) * 100
  return Number.isFinite(percentage) ? `${percentage.toFixed(2)}%` : '—'
}

function outcomeLabel(status: OutcomeStatus): string {
  const labels: Record<OutcomeStatus, string> = {
    win: '胜',
    loss: '负',
    flat: '平',
    no_entry: '未入场',
    unresolved: '未解决',
  }
  return labels[status]
}

function decisionLabel(decision: Decision): string {
  const labels: Record<Decision, string> = {
    buy_candidate: '候选',
    watch: '观察',
    pass: '放弃',
    invalid: '无效',
  }
  return labels[decision]
}

export default App
