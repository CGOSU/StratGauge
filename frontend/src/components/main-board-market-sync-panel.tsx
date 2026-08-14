import { useState } from 'react'
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

const marketSyncStatusLabels: Record<MarketSyncStatus, string> = {
  approved: '已批准',
  rejected: '未批准',
  pending: '待处理',
  superseded: '已被替代',
}

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
  const [syncResult, setSyncResult] = useState<MarketSyncResponse | null>(null)

  async function syncMarket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setMessage(null)
    setSyncError(null)
    setSyncResult(null)
    try {
      const result = await requestJson<MarketSyncResponse>(`${apiBase}/v1/market-sync/main-board`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_date: sessionDate, minimum_coverage: '0.90' }),
      })
      setSyncResult(result)
      const approvedMarketCount = result.sessions.filter((session) => session.market_status === 'approved').length
      const approvedFactorCount = result.sessions.filter((session) => session.adjustment_factor_status === 'approved').length
      const skippedCount = result.sessions.filter((session) => session.skipped).length
      const blockedCount = result.sessions.filter((session) => !marketSyncSessionIsReady(session)).length
      const rejectedCount = result.sessions.filter((session) => session.market_status === 'rejected' || session.adjustment_factor_status === 'rejected').length
      setMessageTone(result.research_ready ? 'success' : 'warning')
      setMessage(
        result.research_ready
          ? `同步完成至 ${result.requested_through}：检查 ${result.sessions.length} 个工作日，日线 ${approvedMarketCount} 个、复权因子 ${approvedFactorCount} 个均已批准，${skippedCount} 个已使用本地快照；股票池 ${result.security_count} 只。`
          : `同步已完成，但 ${blockedCount} 个工作日未达到研究条件，其中 ${rejectedCount} 个阶段被质量门拒绝；未批准快照不会用于研究。已检查 ${result.sessions.length} 个工作日，股票池 ${result.security_count} 只。`,
      )
    } catch (reason: unknown) {
      if (reason instanceof ApiClientError) {
        const errorReason = typeof reason.details.reason === 'string' ? reason.details.reason : null
        const suggestion = typeof reason.details.suggestion === 'string' ? reason.details.suggestion : null
        setSyncError({ message: reason.message, reason: errorReason, suggestion, code: reason.code ?? null })
      } else {
        setSyncError({
          message: reason instanceof Error ? reason.message : '同步主板收盘行情失败。',
          reason: null,
          suggestion: '检查行情服务和网络连接后重试。',
          code: null,
        })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="market-sync-panel panel" aria-labelledby="market-sync-title">
      <div className="section-heading">
        <div><p className="eyebrow">行情 · 主板</p><h2 id="market-sync-title">同步收盘行情</h2></div>
        <Badge variant="outline">仅 SH / SZ 主板</Badge>
      </div>
      <p className="market-sync-copy">选择交易日后，从 Tushare 同步当前主板股票池的日线收盘数据，并写入批准快照。不会同步创业板、科创板、北交所，也不会触发交易。</p>
      <form className="market-sync-form" onSubmit={syncMarket}>
        <div className="field"><Label htmlFor="market-sync-date">交易日</Label><CalendarInput id="market-sync-date" value={sessionDate} onChange={setSessionDate} required /></div>
        <Button type="submit" disabled={busy || !sessionDate}>{busy ? '同步中…' : '同步今日主板行情'}</Button>
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
