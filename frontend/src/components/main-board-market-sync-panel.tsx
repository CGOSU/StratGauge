import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiClientError, requestJson } from '@/api/client'
import type {
  MarketSyncResponse,
  MarketSyncStageDiagnostic,
  MarketSyncStatus,
} from '@/api/contracts'
import { CalendarInput } from '@/components/calendar-input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'

type MarketSyncErrorView = {
  message: string
  reason: string | null
  suggestion: string | null
  code: string | null
}

type MarketSyncTaskStatus = 'queued' | 'running' | 'retrying' | 'completed' | 'partial' | 'failed' | 'cancelled'

type MarketSyncTask = {
  job_id: string
  kind: string
  status: MarketSyncTaskStatus
  request: {
    session_date: string
    minimum_coverage: string | number
    lookback_days: number
  }
  attempt_count: number
  max_retries: number
  timeout_seconds: number
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  result: MarketSyncResponse | null
  error: string | null
}

type MarketSyncTaskRunResponse = {
  jobs: MarketSyncTask[]
}

const marketSyncStatusLabels: Record<MarketSyncStatus, string> = {
  approved: '已批准',
  rejected: '未批准',
  pending: '待处理',
  superseded: '已被替代',
}

const marketSyncTaskStatusLabels: Record<MarketSyncTaskStatus, string> = {
  queued: '排队中',
  running: '执行中',
  retrying: '等待重试',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
}

const terminalTaskStatuses = new Set<MarketSyncTaskStatus>(['completed', 'partial', 'failed', 'cancelled'])

function formatMarketSyncCoverage(value: string | number): string {
  const coverage = Number(value)
  if (!Number.isFinite(coverage)) {
    return '—'
  }
  return `${(coverage * 100).toFixed(1).replace(/\.0$/, '')}%`
}

function marketSyncSessionIsReady(session: MarketSyncResponse['sessions'][number]): boolean {
  return session.market_status === 'approved' && session.adjustment_factor_status === 'approved'
}

function toMarketSyncError(reason: unknown, fallback: string): MarketSyncErrorView {
  if (reason instanceof ApiClientError) {
    const errorReason = typeof reason.details.reason === 'string' ? reason.details.reason : null
    const suggestion = typeof reason.details.suggestion === 'string' ? reason.details.suggestion : null
    return { message: reason.message, reason: errorReason, suggestion, code: reason.code ?? null }
  }
  return {
    message: reason instanceof Error ? reason.message : fallback,
    reason: null,
    suggestion: '检查行情服务和本地任务调度器后重试。',
    code: null,
  }
}

function taskStatusDescription(status: MarketSyncTaskStatus): string {
  switch (status) {
    case 'queued':
      return '任务已持久化，等待显式 runner 或操作系统调度执行。'
    case 'running':
      return 'runner 正在复用现有同步服务写入快照证据。'
    case 'retrying':
      return '上次遇到可重试的数据源或单写者边界问题，将在下一次 runner 执行时继续。'
    case 'completed':
      return '日线与复权因子均已通过质量门。'
    case 'partial':
      return '同步流程已结束，但部分阶段未通过质量门；未批准快照不会用于研究。'
    case 'failed':
      return '任务未完成，请查看错误摘要并修复前置条件后重新入队。'
    case 'cancelled':
      return '任务在排队阶段被取消，账本状态已持久化。'
  }
}

function MarketSyncStageDetail({ diagnostic }: { diagnostic: MarketSyncStageDiagnostic }) {
  const issueSeverityLabel = (severity: string) => {
    if (severity === 'error') {
      return '阻断'
    }
    if (severity === 'warning') {
      return '提示'
    }
    return '信息'
  }

  return (
    <section className={`market-sync-stage market-sync-stage--${diagnostic.status}`} aria-label={`${diagnostic.phase_label}诊断`}>
      <div className="market-sync-stage-heading">
        <div className="market-sync-stage-title">
          <strong>{diagnostic.phase_label}</strong>
          <span className={`market-sync-status market-sync-status--${diagnostic.status}`}>
            {marketSyncStatusLabels[diagnostic.status]}
          </span>
        </div>
        <span className="market-sync-stage-date">{diagnostic.session_date}</span>
      </div>
      <p className="market-sync-stage-summary">{diagnostic.summary}</p>
      <dl className="market-sync-stage-metrics">
        <div><dt>覆盖率</dt><dd>{formatMarketSyncCoverage(diagnostic.coverage)}</dd></div>
        <div><dt>实际 / 预期</dt><dd>{diagnostic.actual_securities} / {diagnostic.expected_securities} 只</dd></div>
        <div><dt>质量门</dt><dd>{formatMarketSyncCoverage(diagnostic.minimum_coverage)}</dd></div>
      </dl>
      {diagnostic.issues.length > 0 && (
        <div className="market-sync-issues">
          <strong>质量问题（{diagnostic.issues.length}）</strong>
          <ul>
            {diagnostic.issues.map((issue, index) => (
              <li key={`${issue.code}-${issue.symbol ?? ''}-${issue.session_date ?? ''}-${index}`}>
                <div className="market-sync-issue-heading">
                  <strong>{issue.label}</strong>
                  <span>{issueSeverityLabel(issue.severity)}</span>
                </div>
                <p>{issue.message}</p>
                <span>处理：{issue.action}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="market-sync-stage-action">
        <strong>下一步</strong>
        <span>{diagnostic.action}</span>
      </div>
      <div className="market-sync-evidence">
        <span>快照证据</span>
        <code title={diagnostic.snapshot_id}>{diagnostic.snapshot_id}</code>
        <span>质量报告</span>
        <code title={diagnostic.quality_run_id ?? '未生成质量报告'}>{diagnostic.quality_run_id ?? '未生成'}</code>
      </div>
    </section>
  )
}

function MarketSyncStageNotRun({ reason }: { reason: string }) {
  return (
    <section className="market-sync-stage market-sync-stage--not-run" aria-label="复权因子诊断">
      <div className="market-sync-stage-heading">
        <div className="market-sync-stage-title">
          <strong>复权因子</strong>
          <span className="market-sync-status market-sync-status--not-run">未执行</span>
        </div>
      </div>
      <p className="market-sync-stage-summary">{reason}</p>
    </section>
  )
}

export function MainBoardMarketSyncPanel({ apiBase }: { apiBase: string }) {
  const [sessionDate, setSessionDate] = useState(new Date().toISOString().slice(0, 10))
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [messageTone, setMessageTone] = useState<'success' | 'warning'>('success')
  const [syncError, setSyncError] = useState<MarketSyncErrorView | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<MarketSyncTask | null>(null)
  const [pollingEnabled, setPollingEnabled] = useState(true)
  const [polling, setPolling] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [runnerBusy, setRunnerBusy] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const pollControllerRef = useRef<AbortController | null>(null)
  const syncResult = task?.result ?? null

  useEffect(() => {
    if (!taskId || !pollingEnabled) {
      setPolling(false)
      return
    }
    const currentTaskId = taskId
    const controller = new AbortController()
    pollControllerRef.current = controller
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null

    async function pollTask() {
      if (!active) {
        return
      }
      setPolling(true)
      try {
        const next = await requestJson<MarketSyncTask>(
          `${apiBase}/v1/market-sync/tasks/${encodeURIComponent(currentTaskId)}`,
          { signal: controller.signal },
        )
        if (!active) {
          return
        }
        setTask(next)
        if (terminalTaskStatuses.has(next.status)) {
          setPolling(false)
          return
        }
        timer = setTimeout(() => void pollTask(), 2000)
      } catch (reason: unknown) {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        if (active) {
          setSyncError(toMarketSyncError(reason, '刷新行情同步任务失败。'))
          setPolling(false)
        }
      }
    }

    void pollTask()
    return () => {
      active = false
      controller.abort()
      if (timer) {
        clearTimeout(timer)
      }
      if (pollControllerRef.current === controller) {
        pollControllerRef.current = null
      }
      setPolling(false)
    }
  }, [apiBase, pollingEnabled, taskId, task?.status])

  async function syncMarket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setMessage(null)
    setSyncError(null)
    setPollingEnabled(true)
    setTask(null)
    setTaskId(null)
    try {
      const queued = await requestJson<MarketSyncTask>(`${apiBase}/v1/market-sync/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_date: sessionDate, minimum_coverage: '0.90' }),
      })
      setTask(queued)
      setTaskId(queued.job_id)
      setMessageTone('success')
      setMessage(`已加入主板行情同步队列：任务 ${queued.job_id}。提交不会等待完整行情下载。`)
    } catch (reason: unknown) {
      setSyncError(toMarketSyncError(reason, '加入主板行情同步队列失败。'))
    } finally {
      setBusy(false)
    }
  }

  async function refreshTask() {
    if (!taskId) {
      return
    }
    setRefreshing(true)
    setPollingEnabled(true)
    setSyncError(null)
    try {
      const next = await requestJson<MarketSyncTask>(
        `${apiBase}/v1/market-sync/tasks/${encodeURIComponent(taskId)}`,
      )
      setTask(next)
    } catch (reason: unknown) {
      setSyncError(toMarketSyncError(reason, '刷新行情同步任务失败。'))
    } finally {
      setRefreshing(false)
    }
  }

  async function runQueuedTasks() {
    if (!taskId) {
      return
    }
    setRunnerBusy(true)
    setSyncError(null)
    try {
      const result = await requestJson<MarketSyncTaskRunResponse>(`${apiBase}/v1/market-sync/tasks/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 1 }),
      })
      const current = result.jobs.find((job) => job.job_id === taskId)
      if (current) {
        setTask(current)
        setMessageTone(current.status === 'completed' ? 'success' : 'warning')
        setMessage(`已显式执行一次 runner：任务当前状态为${marketSyncTaskStatusLabels[current.status]}。`)
      } else {
        await refreshTask()
        setMessage('runner 已执行一次；当前任务未在本次返回结果中，请刷新查看。')
      }
    } catch (reason: unknown) {
      setSyncError(toMarketSyncError(reason, '显式执行行情同步 runner 失败。'))
    } finally {
      setRunnerBusy(false)
    }
  }

  async function cancelQueuedTask() {
    if (!taskId || task?.status !== 'queued') {
      return
    }
    setCancelBusy(true)
    setSyncError(null)
    try {
      const cancelled = await requestJson<MarketSyncTask>(
        `${apiBase}/v1/market-sync/tasks/${encodeURIComponent(taskId)}/cancel`,
        { method: 'POST' },
      )
      setTask(cancelled)
      setMessageTone('warning')
      setMessage('任务已取消；只有 queued 状态允许取消。')
    } catch (reason: unknown) {
      setSyncError(toMarketSyncError(reason, '取消排队中的行情同步任务失败。'))
    } finally {
      setCancelBusy(false)
    }
  }

  return (
    <Card className="market-sync-panel panel" aria-labelledby="market-sync-title">
      <div className="section-heading">
        <div><p className="eyebrow">行情 · 主板</p><h2 id="market-sync-title">同步收盘行情</h2></div>
        <Badge variant="outline">仅 SH / SZ 主板</Badge>
      </div>
      <p className="market-sync-copy">选择交易日后加入本地任务队列。任务由显式 runner 或操作系统调度执行，面板会轮询状态，不会在提交请求中等待完整行情下载；兼容同步入口仍保留为 <code>POST /v1/market-sync/main-board</code>。</p>
      <form className="market-sync-form" onSubmit={syncMarket}>
        <div className="field"><Label htmlFor="market-sync-date">交易日</Label><CalendarInput id="market-sync-date" value={sessionDate} onChange={setSessionDate} required /></div>
        <Button type="submit" disabled={busy || !sessionDate}>{busy ? '加入队列中…' : '加入主板行情队列'}</Button>
      </form>
      {message && <p className={`market-sync-success market-sync-success--${messageTone}`} role="status">{message}</p>}
      {syncError && (
        <div className="market-sync-error" role="alert">
          <strong>{syncError.message}</strong>
          {syncError.reason && <span>原因：{syncError.reason}</span>}
          {syncError.suggestion && <span>建议：{syncError.suggestion}</span>}
          {syncError.code && <code>错误代码：{syncError.code}</code>}
        </div>
      )}
      {task && (
        <section className="market-sync-task" aria-label="行情同步任务状态" aria-live="polite">
          <div className="market-sync-task-heading">
            <div>
              <strong>主板行情同步任务</strong>
              <span className={`market-sync-task-status market-sync-task-status--${task.status}`}>
                {marketSyncTaskStatusLabels[task.status]}
              </span>
            </div>
            <span className="market-sync-task-date">交易日 {task.request.session_date}</span>
          </div>
          <p className="market-sync-stage-summary">{taskStatusDescription(task.status)}</p>
          <dl className="market-sync-task-meta">
            <div><dt>任务 ID</dt><dd><code title={task.job_id}>{task.job_id}</code></dd></div>
            <div><dt>尝试次数</dt><dd>{task.attempt_count} / {task.max_retries + 1}</dd></div>
            <div><dt>更新时间</dt><dd>{new Date(task.updated_at).toLocaleString()}</dd></div>
          </dl>
          <div className="market-sync-task-actions">
            <Button type="button" variant="outline" size="sm" onClick={() => void refreshTask()} disabled={refreshing}>
              {refreshing ? '刷新中…' : '手动刷新'}
            </Button>
            {!terminalTaskStatuses.has(task.status) && (
              <Button type="button" variant="outline" size="sm" onClick={() => setPollingEnabled((value) => !value)}>
                {pollingEnabled ? '停止自动刷新' : '恢复自动刷新'}
              </Button>
            )}
            {(task.status === 'queued' || task.status === 'retrying') && (
              <Button type="button" variant="outline" size="sm" onClick={() => void runQueuedTasks()} disabled={runnerBusy}>
                {runnerBusy ? 'runner 执行中…' : '立即执行一次'}
              </Button>
            )}
            {task.status === 'queued' && (
              <Button type="button" variant="outline" size="sm" onClick={() => void cancelQueuedTask()} disabled={cancelBusy}>
                取消排队
              </Button>
            )}
            <span className="market-sync-task-polling">{polling ? '自动刷新中…' : '自动刷新已停止'}</span>
          </div>
          {task.error && (
            <div className="market-sync-error" role="alert">
              <strong>任务错误摘要</strong>
              <span>{task.error}</span>
            </div>
          )}
        </section>
      )}
      {syncResult && (
        <div className="market-sync-results" aria-label="行情同步明细">
          <div className={`market-sync-readiness market-sync-readiness--${syncResult.research_ready ? 'ready' : 'blocked'}`}>
            <strong>{syncResult.research_ready ? '研究数据已准备就绪' : '研究数据未准备就绪'}</strong>
            <span>{syncResult.research_ready ? '日线与复权因子均已通过质量门。' : '含未批准阶段的快照不会进入研究。请按下方诊断处理后重试。'}</span>
          </div>
          {syncResult.sessions.map((session) => (
            <div className="market-sync-session" key={session.session_date}>
              <div className="market-sync-result-row">
                <strong>{session.session_date}</strong>
                <span className={`market-sync-status market-sync-status--${session.market_status}`}>{marketSyncStatusLabels[session.market_status]}</span>
                <span>{session.skipped ? '已有批准快照，已跳过下载' : '本次请求已处理'}</span>
                <span className={marketSyncSessionIsReady(session) ? 'market-confirmed' : 'market-error'}>{marketSyncSessionIsReady(session) ? '可用于研究' : '未达到研究条件'}</span>
              </div>
              <div className="market-sync-stage-list">
                <MarketSyncStageDetail diagnostic={session.market_diagnostic} />
                {session.adjustment_factor_diagnostic ? (
                  <MarketSyncStageDetail diagnostic={session.adjustment_factor_diagnostic} />
                ) : (
                  <MarketSyncStageNotRun
                    reason={session.market_status === 'rejected'
                      ? '日线未通过质量门，复权因子未同步；未批准数据不会用于研究。'
                      : '未找到已批准的复权因子快照，当前交易日不具备完整研究证据。请重新同步。'}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
