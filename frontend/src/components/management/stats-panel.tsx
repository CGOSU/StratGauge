import { useEffect, useState } from 'react'
import type { Decision } from '@/types'
import { decisionLabel } from '@/lib/labels'
import { formatWinRate, formatPercent } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { CalendarInput } from '@/components/calendar-input'
import type { ManagementFilter, ManagementSourceFilter, ManagementStats } from '@/types'
import { requestJson } from '@/api/client'

export function ManagementStatsPanel({
  apiBase,
  filter,
  sourceFilter,
  formulaVersions,
  onError,
}: {
  apiBase: string
  filter: ManagementFilter
  sourceFilter: ManagementSourceFilter
  formulaVersions: string[]
  onError: (message: string) => void
}) {
  const [statsFormulaVersion, setStatsFormulaVersion] = useState('all')
  const [statsDateFrom, setStatsDateFrom] = useState('')
  const [statsDateTo, setStatsDateTo] = useState('')
  const [statsDecision, setStatsDecision] = useState<'all' | Decision>('all')
  const [statsPolicy, setStatsPolicy] = useState('all')
  const [stats, setStats] = useState<ManagementStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

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
        onError(reason instanceof Error ? reason.message : '加载管理统计失败。')
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

  function resetStatsFilters() {
    setStatsFormulaVersion('all')
    setStatsDateFrom('')
    setStatsDateTo('')
    setStatsDecision('all')
    setStatsPolicy('all')
  }

  return (
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
          <CalendarInput
            id="stats-date-from"
            value={statsDateFrom}
            onChange={setStatsDateFrom}
            max={statsDateTo || undefined}
            aria-describedby="stats-date-help"
          />
        </div>
        <div className="field">
          <Label htmlFor="stats-date-to">选股日期止</Label>
          <CalendarInput
            id="stats-date-to"
            value={statsDateTo}
            onChange={setStatsDateTo}
            min={statsDateFrom || undefined}
            aria-describedby="stats-date-help"
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
      <p id="stats-date-help" className="form-hint">
        日期可留空；选择起止日期后，结束日期不能早于开始日期。
      </p>
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
            <strong>{formatWinRate(stats.summary)}</strong>
          </div>
          <div>
            <span>平均净收益</span>
            <strong>{formatPercent(stats.summary.average_net_return)}</strong>
          </div>
        </div>
      ) : null}
    </Card>
  )
}
