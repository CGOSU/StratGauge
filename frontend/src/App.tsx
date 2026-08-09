import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

type HealthState = 'checking' | 'online' | 'offline'
type Decision = 'buy_candidate' | 'watch' | 'pass' | 'invalid'
type OutcomeStatus = 'win' | 'loss' | 'flat' | 'no_entry' | 'unresolved'

type Batch = {
  batch_id: string
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
  exchange: 'SH' | 'SZ' | 'BJ'
  name: string | null
  source_fields: Record<string, string>
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

type ApiErrorPayload = {
  error?: { message?: string }
}

const apiBase = import.meta.env.VITE_API_BASE_URL
  ? import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '')
  : '/api'
const healthUrl = `${apiBase}/healthz`

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
  const [healthState, setHealthState] = useState<HealthState>('checking')
  const [batches, setBatches] = useState<Batch[]>([])
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [selectedBatch, setSelectedBatch] = useState<BatchView | null>(null)
  const [selectedStats, setSelectedStats] = useState<BatchStats | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [selectionDate, setSelectionDate] = useState('')
  const [exportedAt, setExportedAt] = useState('')
  const [formulaName, setFormulaName] = useState('')
  const [formulaVersion, setFormulaVersion] = useState('')
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [loadingBatch, setLoadingBatch] = useState(false)
  const [labelingId, setLabelingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const batchesLoadStarted = useRef(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
    if (!selectedBatchId) {
      setSelectedBatch(null)
      setSelectedStats(null)
      return
    }
    const controller = new AbortController()
    setSelectedBatch(null)
    setSelectedStats(null)
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
        setError(reason instanceof Error ? reason.message : '加载批次失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingBatch(false)
        }
      })

    return () => controller.abort()
  }, [selectedBatchId])

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
    if (!file || !selectionDate || !exportedAt || !formulaName) {
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
          formula_name: formulaName,
          formula_version: formulaVersion || null,
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

  return (
    <main className="shell">
      <header className="hero">
        <p className="eyebrow">STRATGAUGE · 策衡</p>
        <h1>衡量优势，以证据交易。</h1>
        <p className="summary">通达信负责选股，系统负责留痕、标注和后续验证。</p>
      </header>

      <section className="status-card" aria-labelledby="status-title">
        <div>
          <p className="eyebrow">半手动研究闭环</p>
          <h2 id="status-title">候选队列已就绪</h2>
          <p>导入通达信收盘后结果，人工标注后再由系统跟踪后续行情。</p>
        </div>
        <div className="status-actions">
          <span className="status-badge">仅手动交易</span>
          <span
            className={`health-status health-status--${healthState}`}
            role="status"
            aria-live="polite"
          >
            API：{healthState === 'checking' ? '检测中' : healthState === 'online' ? '在线' : '离线'}
          </span>
        </div>
      </section>

      {error && (
        <div className="alert" role="alert">
          {error}
          <button type="button" className="text-button" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      <section className="research-layout" aria-label="通达信选股研究工作区">
        <aside className="import-panel panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">01 · 导入</p>
              <h2>新建选股批次</h2>
            </div>
          </div>
          <form className="import-form" onSubmit={importSelection}>
            <label className="field">
              <span>通达信导出文件</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.tsv,.txt,text/csv,text/plain"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <small>{file ? file.name : '支持 UTF-8 / GB18030 的 CSV、TSV、TXT'}</small>
            </label>
            <label className="field">
              <span>选股日期</span>
              <input
                type="date"
                value={selectionDate}
                onChange={(event) => setSelectionDate(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>导出时间（北京时间）</span>
              <input
                type="datetime-local"
                value={exportedAt}
                onChange={(event) => setExportedAt(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>公式名称</span>
              <input
                type="text"
                value={formulaName}
                onChange={(event) => setFormulaName(event.target.value)}
                placeholder="例如：趋势选股"
                required
              />
            </label>
            <label className="field">
              <span>公式版本（可选）</span>
              <input
                type="text"
                value={formulaVersion}
                onChange={(event) => setFormulaVersion(event.target.value)}
                placeholder="例如：v3"
              />
            </label>
            <button className="primary-button" type="submit" disabled={busy || !file}>
              {busy ? '导入中…' : '导入选股结果'}
            </button>
          </form>
        </aside>

        <section className="batch-panel panel" aria-labelledby="batch-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">02 · 批次</p>
              <h2 id="batch-title">选股批次</h2>
            </div>
            <span className="count-badge">{batches.length} 个批次</span>
          </div>
          {loadingBatches ? (
            <div className="skeleton-list" aria-label="批次加载中">
              <span />
              <span />
              <span />
            </div>
          ) : batches.length === 0 ? (
            <p className="empty-copy">还没有导入批次。先从左侧导入通达信结果。</p>
          ) : (
            <div className="batch-list" role="list">
              {batches.map((batch) => (
                <button
                  className={`batch-item${batch.batch_id === selectedBatchId ? ' batch-item--selected' : ''}`}
                  type="button"
                  key={batch.batch_id}
                  onClick={() => setSelectedBatchId(batch.batch_id)}
                  aria-pressed={batch.batch_id === selectedBatchId}
                >
                  <span>
                    <strong>{batch.formula_name}</strong>
                    <small>{batch.selection_date}</small>
                  </span>
                  <span className="batch-count">{batch.candidate_count} 只</span>
                </button>
              ))}
            </div>
          )}
        </section>
      </section>

      <section className="candidate-panel panel" aria-labelledby="candidate-title">
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
            <span />
            <span />
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
                      <span className={`decision-badge decision-badge--${label?.decision ?? 'unlabeled'}`}>
                        {label ? decisionLabel(label.decision) : '未标注'}
                      </span>
                      <span className={`outcome-badge outcome-badge--${outcome?.status ?? 'pending'}`}>
                        {outcome ? outcomeLabel(outcome.status) : '结果待跟踪'}
                      </span>
                    </div>
                    <input
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
                        <button
                          className="secondary-button"
                          type="button"
                          key={decision}
                          disabled={labelingId !== null}
                          data-saving={labelingId === `${candidate.candidate_id}:${decision}`}
                          onClick={() => void labelCandidate(candidate.candidate_id, decision)}
                        >
                          {decisionLabel(decision)}
                        </button>
                      ))}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </section>

      <section className="principles" aria-labelledby="principles-title">
        <h2 id="principles-title">当前边界</h2>
        <ul>
          <li>收盘后导入，最早下一交易日跟踪。</li>
          <li>人工标签与客观行情结果分开保存。</li>
          <li>系统不连接券商，也不会自动下单。</li>
        </ul>
      </section>
    </main>
  )
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
