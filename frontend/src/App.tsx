import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import {
  Archive,
  BarChart3,
  Briefcase,
  ChartLine,
  Database,
  Eye,
  Download,
  LayoutDashboard,
  ListChecks,
  Pencil,
  Plus,
  Receipt,
  RotateCcw,
  Settings2,
  Upload,
  WalletCards,
  X,
} from 'lucide-react'
import { apiBaseUrl, healthUrl, requestJson } from '@/api/client'
import {
  manualSellReasonLabels,
  type Exchange,
  type ManualFill,
  type ManualOrder,
  type ManualSellReason,
  type PortfolioPosition,
  type PortfolioReview,
  type SecurityMasterRecord,
  type SecurityMasterResponse,
} from '@/api/contracts'
import { CalendarInput } from '@/components/calendar-input'
import { MainBoardMarketSyncPanel } from '@/components/main-board-market-sync-panel'
import { MlReadinessPanel } from '@/components/ml-readiness-panel'
import { PortfolioWorkspace } from '@/components/portfolio-workspace'
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
type AnnotationOutlook = 'bullish' | 'neutral' | 'bearish'
type AnnotationDecision = 'discard' | 'watch' | 'prepare_buy'
type ReviewState = 'unreviewed' | 'discarded' | 'watching' | 'preparing'
type AnnotationTag = string
type ReusableTagCategory = 'position' | 'pattern' | 'price_volume' | 'sector'
type ReusableTag = {
  tag_id: string
  category: ReusableTagCategory
  label: string
  created_at: string
  active: boolean
  events: Array<{
    event_id: string
    tag_id: string
    category: ReusableTagCategory
    event_type: 'rename' | 'archive' | 'restore'
    occurred_at: string
    new_label: string | null
    reason: string | null
  }>
}
type ReusableTagSnapshot = {
  tag_id: string
  category: ReusableTagCategory
  label: string
}
type OutcomeStatus = 'win' | 'loss' | 'flat' | 'no_entry' | 'unresolved'
type Workspace = 'research' | 'management' | 'portfolio'
type AppRoute = 'workbench' | 'signals' | 'watchlist' | 'positions' | 'trades' | 'strategies' | 'analytics' | 'import' | 'settings'
type LifecycleStatus = 'active' | 'archived'
type ManagementFilter = 'all' | LifecycleStatus
type BatchSource = 'tongdaxin' | 'manual'
type ManagementSourceFilter = 'all' | BatchSource
type Provider = 'tushare' | 'akshare' | 'baostock' | 'waizao'
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

type PerformancePoint = {
  horizon_sessions: number
  session_date: string | null
  close_price: string | null
  return_from_anchor: string | null
  maximum_rise: string | null
  maximum_drawdown: string | null
  target_achieved: boolean | null
}

type Performance = {
  status: 'resolved' | 'no_entry' | 'unresolved'
  evaluated_at: string
  performance_policy_id: string
  anchor_session: string | null
  anchor_price: string | null
  target_return: string | null
  points: PerformancePoint[]
  reason: string
}

type Label = {
  candidate_id: string
  decision: Decision
  labeled_at: string
  note: string | null
}

type Annotation = {
  annotation_id: string
  candidate_id: string
  reviewed_at: string
  recorded_at: string
  overall_score: number
  outlook: AnnotationOutlook
  decision: AnnotationDecision
  position_tags: AnnotationTag[]
  pattern_tags: AnnotationTag[]
  price_volume_tags: AnnotationTag[]
  sector_tags: AnnotationTag[]
  reusable_tag_snapshots: ReusableTagSnapshot[]
  taxonomy_version: string
  note: string | null
}

type Exclusion = {
  exclusion_id: string
  candidate_id: string
  excluded_at: string
  recorded_at: string
  reason: string
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
  annotations: Annotation[]
  exclusions: Exclusion[]
  latest_annotation: Annotation | null
  latest_exclusion: Exclusion | null
  review_state: ReviewState
  excluded: boolean
  outcomes: Outcome[]
  performances: Performance[]
}

type BatchView = {
  batch: Batch
  candidates: CandidateView[]
}

type BatchPage = {
  items: Batch[]
  page: number
  page_size: number
  total: number
  has_next: boolean
}

type StatsSummary = {
  total: number
  completed: number
  wins: number
  losses: number
  flats: number
  no_entry: number
  unresolved: number
  minimum_evidence_samples: number
  evidence_sufficient: boolean
  win_rate: string | null
  average_net_return: string | null
}

type EntryPriceStats = {
  sample_count: number
  minimum: string | null
  lower_quartile: string | null
  median: string | null
  upper_quartile: string | null
  maximum: string | null
  average: string | null
}

type CandidateAnalysis = {
  batch_id: string
  candidate_id: string
  symbol: string
  reference_at: string
  formula_id: string | null
  formula_name: string
  formula_version: string | null
  tracking_policy_id: string | null
  available_tracking_policy_ids: string[]
  cohort_batch_count: number
  cohort_candidate_count: number
  conditional_stats: StatsSummary
  entry_price_stats: EntryPriceStats
}

type ModelScore = {
  model_version_id: string
  model_type: string
  lifecycle: string
  tracking_policy_id: string
  scored_at: string
  uncalibrated_model_score: string
  calibrated_model_id: string | null
  calibrated_model_score: string | null
  calibrated: boolean
  sample_count: number | null
  calibration_sample_count: number | null
  applicable_date_start: string | null
  applicable_date_end: string | null
  authoritative: false
}

type ModelScoreFailure = {
  model_version_id: string
  reason: string
}

type CandidateModelScores = {
  batch_id: string
  candidate_id: string
  symbol: string
  scores: ModelScore[]
  failures: ModelScoreFailure[]
}

type WorkbenchModelScore = {
  score_id: string
  model_version_id: string
  model_lifecycle: 'shadow' | 'active'
  uncalibrated_model_score: string
  calibrated_model_score: string | null
  calibrated: boolean
  used_for_decision: false
}

type RecommendationCandidate = Candidate & {
  quality_state: string
  historical_stats: StatsSummary | null
  model_score: WorkbenchModelScore | null
  manual_decision: Decision | null
  state: 'reviewable' | 'blocked'
  blocking_reasons: string[]
  authoritative: false
}

type RecommendationWorkbench = {
  batch: Batch
  generated_at: string
  candidates: RecommendationCandidate[]
  automatic_action: false
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

type ManagedReusableTag = ReusableTag

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

type ManagedBatchPage = {
  items: ManagedBatch[]
  page: number
  page_size: number
  total: number
  has_next: boolean
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

type WorkbenchMarketEvidence = {
  status: 'available' | 'unavailable' | 'incomplete'
  snapshot_id: string | null
  provider: string | null
  session_date: string | null
  available_at: string | null
  close: string | null
  previous_close: string | null
  change_percent: string | null
  volume: string | null
  amount: string | null
  reason: string | null
}

type WorkbenchSignalPage = {
  items: WorkbenchSignal[]
  page: number
  page_size: number
  total: number
  has_next: boolean
}

type WorkbenchSignal = {
  batch: Batch
  candidate: Candidate
  labels: Label[]
  annotations: Annotation[]
  latest_annotation: Annotation | null
  exclusions: Exclusion[]
  latest_exclusion: Exclusion | null
  review_state: ReviewState
  excluded: boolean
  outcomes: Outcome[]
  performances: Performance[]
  market: WorkbenchMarketEvidence
  confirmed_buy: boolean
  confirmed_buy_fill_ids: string[]
}

type WorkbenchSummary = {
  as_of: string
  signal_count: number
  excluded_count: number
  unreviewed_count: number
  reviewed_count: number
  watching_count: number
  preparing_count: number
  confirmed_buy_count: number
}

type WorkbenchTimelineEvent = {
  event_id: string
  event_type: 'signal' | 'annotation' | 'exclusion' | 'order' | 'fill' | 'review' | 'outcome' | 'performance'
  occurred_at: string
  source_id: string
  title: string
  detail: string
}

type WorkbenchStockDetail = {
  symbol: string
  name: string | null
  as_of: string
  market: WorkbenchMarketEvidence
  signals: WorkbenchSignal[]
  timeline: WorkbenchTimelineEvent[]
}

type WorkbenchPositionView = {
  position: PortfolioPosition
  as_of: string
  source: 'signal' | 'manual_discovery' | 'mixed'
  selection_candidate_ids: string[]
  holding_days: number
  market: WorkbenchMarketEvidence
  unrealized_profit: string | null
  unrealized_return: string | null
  unrealized_reason: string | null
}

type WorkbenchTradeView = {
  order: ManualOrder
  fills: ManualFill[]
  source: 'signal' | 'manual_discovery' | 'mixed'
}

type WorkbenchHorizonMetric = {
  horizon_sessions: number
  sample_count: number
  average_return: string | null
  average_maximum_rise: string | null
  average_maximum_drawdown: string | null
  target_3_percent_rate: string | null
  target_5_percent_rate: string | null
}

type WorkbenchCohortMetrics = {
  cohort: 'all_signals' | 'human_bullish' | 'confirmed_trades'
  sample_count: number
  resolved_count: number
  excluded_count: number
  horizons: WorkbenchHorizonMetric[]
}

type WorkbenchStrategyView = {
  formula_id: string | null
  formula_name: string
  formula_version: string | null
  signal_count: number
  cohorts: WorkbenchCohortMetrics[]
}

type WorkbenchAnalyticsView = {
  as_of: string
  cohorts: WorkbenchCohortMetrics[]
}

type WorkbenchRoute = Extract<AppRoute, 'workbench' | 'signals' | 'watchlist' | 'positions' | 'trades' | 'strategies' | 'analytics'>
type WorkbenchReviewFilter = 'all' | 'unreviewed' | 'bullish' | 'watching' | 'preparing' | 'discarded' | 'bought'
type StockDrawerTab = 'market' | 'judgment' | 'signal' | 'trade' | 'analysis'

const apiBase = apiBaseUrl

const providerLabels: Record<Provider, string> = {
  tushare: 'Tushare Pro（主源）',
  akshare: 'AKShare（兼容）',
  baostock: 'BaoStock（备用）',
  waizao: '歪枣网（临时恢复源）',
}

const exchangeLabels: Record<Exchange, string> = {
  SH: '上海 SH',
  SZ: '深圳 SZ',
  BJ: '北京 BJ',
}

const annotationOutlookLabels: Record<AnnotationOutlook, string> = {
  bullish: '看好',
  neutral: '一般',
  bearish: '不看好',
}

const annotationDecisionLabels: Record<AnnotationDecision, string> = {
  discard: '放弃',
  watch: '继续观察',
  prepare_buy: '准备买入',
}

const reusableTagCategoryLabels: Record<ReusableTagCategory, string> = {
  position: '位置',
  pattern: '形态',
  price_volume: '量价',
  sector: '板块环境',
}

const annotationTagGroups: Array<{
  key: 'position_tags' | 'pattern_tags' | 'price_volume_tags' | 'sector_tags'
  reusableCategory: ReusableTagCategory
  label: string
  options: Array<[string, string]>
}> = [
  { key: 'position_tags', reusableCategory: 'position', label: '位置', options: [['low', '低位'], ['mid', '中位'], ['high', '高位'], ['support', '支撑附近'], ['resistance', '压力附近'], ['new_high', '阶段新高']] },
  { key: 'pattern_tags', reusableCategory: 'pattern', label: '形态', options: [['trend_continuation', '趋势延续'], ['breakout', '突破'], ['pullback', '回踩'], ['consolidation', '横盘整理'], ['reversal', '反转'], ['pattern_break', '形态破坏']] },
  { key: 'price_volume_tags', reusableCategory: 'price_volume', label: '量价', options: [['price_up_volume_up', '价涨量增'], ['price_up_volume_down', '价涨量缩'], ['price_down_volume_up', '价跌量增'], ['price_down_volume_down', '价跌量缩'], ['volume_breakout', '放量突破'], ['volume_pullback', '缩量回踩']] },
  { key: 'sector_tags', reusableCategory: 'sector', label: '板块环境', options: [['sector_strong', '板块强势'], ['sector_rotation', '板块轮动'], ['sector_leader', '板块龙头'], ['sector_following', '板块跟涨'], ['sector_weak', '板块弱势'], ['stock_independent', '个股独立']] },
]

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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const units = ['KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = -1
  do {
    size /= 1024
    unitIndex += 1
  } while (size >= 1024 && unitIndex < units.length - 1)
  return `${size.toFixed(unitIndex === 0 || size >= 10 ? 0 : 1)} ${units[unitIndex]}`
}

function latestLabel(labels: Label[]): Label | null {
  return labels.length === 0 ? null : labels[labels.length - 1]
}

function latestOutcome(outcomes: Outcome[]): Outcome | null {
  return outcomes.length === 0 ? null : outcomes[outcomes.length - 1]
}

function latestPerformance(performances: Performance[]): Performance | null {
  return performances.length === 0 ? null : performances[performances.length - 1]
}

function ErrorAlert({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <Alert variant="destructive" className="sticky-alert">
      <AlertDescription>{message}</AlertDescription>
      <Button type="button" variant="link" size="sm" onClick={onDismiss}>
        关闭
      </Button>
    </Alert>
  )
}

function PaginationControls({
  page,
  pageSize,
  total,
  busy,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  busy: boolean
  onPageChange: (page: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  if (pageCount <= 1) {
    return null
  }
  return (
    <div className="pagination-controls" aria-label="分页导航">
      <Button type="button" size="sm" variant="outline" disabled={busy || page <= 1} onClick={() => onPageChange(page - 1)}>
        上一页
      </Button>
      <span aria-live="polite">第 {page} / {pageCount} 页 · 共 {total} 条</span>
      <Button type="button" size="sm" variant="outline" disabled={busy || page >= pageCount} onClick={() => onPageChange(page + 1)}>
        下一页
      </Button>
    </div>
  )
}

function CandidateAnalysisPanel({ analysis }: { analysis: CandidateAnalysis }) {
  const stats = analysis.conditional_stats
  const prices = analysis.entry_price_stats
  return (
    <section className="candidate-analysis" aria-label={`${analysis.symbol} 历史条件分析`}>
      <div className="candidate-analysis-heading">
        <strong>历史条件分析</strong>
        <span>{analysis.tracking_policy_id ?? '尚无可用结果政策'}</span>
      </div>
      <div className="candidate-analysis-grid">
        <div>
          <span>同公式历史完成胜率</span>
          <strong>{formatWinRate(stats)}</strong>
        </div>
        <div>
          <span>完成样本</span>
          <strong>{stats.completed}</strong>
        </div>
        <div>
          <span>同公式历史入场价</span>
          <strong>
            {prices.sample_count ? `${formatPrice(prices.minimum)} – ${formatPrice(prices.maximum)}` : '—'}
          </strong>
        </div>
        <div>
          <span>入场价中位数</span>
          <strong>{formatPrice(prices.median)}</strong>
        </div>
      </div>
      <p>
        参考 {formatManagementDate(analysis.reference_at)} · {analysis.cohort_batch_count} 个历史批次、
        {analysis.cohort_candidate_count} 个历史候选；入场价仅为历史实际成交假设的描述统计，不是买入建议。
      </p>
    </section>
  )
}

function CandidateModelScorePanel({ data }: { data: CandidateModelScores }) {
  return (
    <section className="candidate-model-scores" aria-label={`${data.symbol} 模型影子证据`}>
      <div className="candidate-analysis-heading">
        <strong>模型影子证据</strong>
        <span>只读 · 不用于决策</span>
      </div>
      {data.scores.length === 0 && data.failures.length === 0 && (
        <p className="empty-copy">暂无影子评分。</p>
      )}
      {data.scores.map((score) => (
        <div className="candidate-model-score" key={`${score.model_version_id}:${score.scored_at}`}>
          <div>
            <span>{score.model_version_id} · {score.model_type}</span>
            <strong>{score.calibrated ? '已校准分数' : '未校准分数'}：{formatModelScore(score.calibrated_model_score ?? score.uncalibrated_model_score)}</strong>
          </div>
          <div>
            <span>样本量</span>
            <strong>{score.sample_count ?? '—'}</strong>
          </div>
          <div>
            <span>适用日期</span>
            <strong>
              {score.applicable_date_start && score.applicable_date_end
                ? `${score.applicable_date_start} – ${score.applicable_date_end}`
                : '—'}
            </strong>
          </div>
          <p>政策 {score.tracking_policy_id} · 评分时间 {formatManagementDate(score.scored_at)} · authoritative=false</p>
        </div>
      ))}
      {data.failures.map((failure) => (
        <p className="candidate-model-score-failure" key={failure.model_version_id}>
          {failure.model_version_id}：{failure.reason}
        </p>
      ))}
    </section>
  )
}

function RecommendationWorkbenchPanel({ data }: { data: RecommendationWorkbench }) {
  return (
    <section className="recommendation-workbench" aria-label="每日推荐工作台">
      <div className="section-heading">
        <div>
          <p className="eyebrow">推荐证据</p>
          <h3>每日推荐工作台</h3>
        </div>
        <Badge variant="outline">仅人工确认</Badge>
      </div>
      <p className="workbench-disclaimer">
        这是质量、历史统计和模型影子证据的只读汇总，不生成推荐价、订单或自动交易动作。
      </p>
      <div className="recommendation-list">
        {data.candidates.map((candidate) => (
          <article className="recommendation-item" key={candidate.candidate_id}>
            <div className="recommendation-item-heading">
              <strong>{candidate.symbol}</strong>
              <Badge variant={candidate.state === 'reviewable' ? 'secondary' : 'outline'}>
                {candidate.state === 'reviewable' ? '可人工复核' : '证据阻断'}
              </Badge>
            </div>
            <div className="recommendation-evidence-grid">
              <div>
                <span>质量状态</span>
                <strong>{candidate.quality_state}</strong>
              </div>
              <div>
                <span>历史完成样本</span>
                <strong>{candidate.historical_stats?.completed ?? '—'}</strong>
              </div>
              <div>
                <span>历史胜率</span>
                <strong>
                  {candidate.historical_stats ? formatWinRate(candidate.historical_stats) : '—'}
                </strong>
              </div>
              <div>
                <span>模型影子分数</span>
                <strong>
                  {candidate.model_score
                    ? formatModelScore(candidate.model_score.calibrated_model_score ?? candidate.model_score.uncalibrated_model_score)
                    : '—'}
                </strong>
              </div>
            </div>
            <p className="workbench-evidence-note">
              {candidate.model_score
                ? `${candidate.model_score.calibrated ? '已校准' : '未校准'} · ${candidate.model_score.model_version_id} · authoritative=false`
                : '暂无模型影子评分'}
            </p>
            {candidate.blocking_reasons.length > 0 && (
              <p className="workbench-blocking-reasons">
                阻断：{candidate.blocking_reasons.join('、')}
              </p>
            )}
            <p className="workbench-evidence-note">
              人工状态：{candidate.manual_decision ? decisionLabel(candidate.manual_decision) : '未确认'}
            </p>
          </article>
        ))}
      </div>
    </section>
  )
}

const appRouteLabels: Record<AppRoute, string> = {
  workbench: '工作台',
  signals: '今日信号',
  watchlist: '观察池',
  positions: '持仓',
  trades: '交易记录',
  strategies: '策略',
  analytics: '分析',
  import: '导入数据',
  settings: '设置',
}

const appRouteIcons: Record<AppRoute, typeof LayoutDashboard> = {
  workbench: LayoutDashboard,
  signals: ListChecks,
  watchlist: Eye,
  positions: Briefcase,
  trades: Receipt,
  strategies: BarChart3,
  analytics: ChartLine,
  import: Upload,
  settings: Settings2,
}

const primaryAppRoutes: AppRoute[] = [
  'workbench',
  'signals',
  'watchlist',
  'positions',
  'trades',
  'strategies',
  'analytics',
]

function AppSidebar({
  activeRoute,
  onNavigate,
}: {
  activeRoute: AppRoute
  onNavigate: (route: AppRoute) => void
}) {
  return (
    <aside className="app-sidebar" aria-label="主导航">
      <div className="app-brand">
        <div className="app-brand-mark" aria-hidden="true">SG</div>
        <div>
          <strong>StratGauge</strong>
          <span>策衡研究台</span>
        </div>
      </div>
      <div className="app-sidebar-section-label">研究工作区</div>
      <nav className="app-sidebar-nav">
        {primaryAppRoutes.map((route) => {
          const Icon = appRouteIcons[route]
          return (
            <button
              type="button"
              className={`app-nav-item${activeRoute === route ? ' app-nav-item--active' : ''}`}
              aria-label={appRouteLabels[route]}
              aria-current={activeRoute === route ? 'page' : undefined}
              key={route}
              title={appRouteLabels[route]}
              onClick={() => onNavigate(route)}
            >
              <Icon className="app-nav-icon" aria-hidden="true" />
              <span>{appRouteLabels[route]}</span>
            </button>
          )
        })}
      </nav>
      <div className="app-sidebar-spacer" />
      <div className="app-sidebar-section-label">低频管理</div>
      <button
        type="button"
        className={`app-nav-item${activeRoute === 'import' ? ' app-nav-item--active' : ''}`}
        aria-label={appRouteLabels.import}
        aria-current={activeRoute === 'import' ? 'page' : undefined}
        onClick={() => onNavigate('import')}
        title={appRouteLabels.import}
      >
        <Upload className="app-nav-icon" aria-hidden="true" />
        <span>导入数据</span>
      </button>
      <button
        type="button"
        className={`app-nav-item${activeRoute === 'settings' ? ' app-nav-item--active' : ''}`}
        aria-label={appRouteLabels.settings}
        aria-current={activeRoute === 'settings' ? 'page' : undefined}
        onClick={() => onNavigate('settings')}
        title={appRouteLabels.settings}
      >
        <Settings2 className="app-nav-icon" aria-hidden="true" />
        <span>设置</span>
      </button>
      <div className="app-sidebar-footer">
        <span className="app-sidebar-status-dot" aria-hidden="true" />
        <span>人工确认模式</span>
      </div>
    </aside>
  )
}

function OperationalStatusBar({
  healthState,
  activeRoute,
}: {
  healthState: HealthState
  activeRoute: AppRoute
}) {
  return (
    <header className="operational-bar">
      <div className="operational-context">
        <span className="operational-kicker">研究工作台</span>
        <strong>{appRouteLabels[activeRoute]}</strong>
      </div>
      <div className="operational-statuses">
        <span className="operational-status operational-status--evidence">
          <span className="operational-status-dot" aria-hidden="true" />
          本地证据优先
        </span>
        <span className={`operational-status operational-status--${healthState}`} role="status" aria-live="polite">
          <span className="operational-status-dot" aria-hidden="true" />
          API {healthState === 'checking' ? '检测中' : healthState === 'online' ? '在线' : '离线'}
        </span>
        <span className="operational-status operational-status--manual">不自动下单</span>
      </div>
    </header>
  )
}

function workbenchReviewLabel(state: ReviewState): string {
  const labels: Record<ReviewState, string> = {
    unreviewed: '未判断',
    discarded: '不看好',
    watching: '观察中',
    preparing: '准备买入',
  }
  return labels[state]
}

function workbenchCohortLabel(cohort: WorkbenchCohortMetrics['cohort']): string {
  const labels: Record<WorkbenchCohortMetrics['cohort'], string> = {
    all_signals: '全部信号',
    human_bullish: '人工看好',
    confirmed_trades: '实际买入',
  }
  return labels[cohort]
}

function workbenchEventLabel(eventType: WorkbenchTimelineEvent['event_type']): string {
  const labels: Record<WorkbenchTimelineEvent['event_type'], string> = {
    signal: '策略信号',
    annotation: '人工判断',
    exclusion: '候选排除',
    order: '订单',
    fill: '成交',
    review: '复盘',
    outcome: '策略结果',
    performance: '未来表现',
  }
  return labels[eventType]
}

function matchesWorkbenchFilter(signal: WorkbenchSignal, filter: WorkbenchReviewFilter): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'unreviewed':
      return signal.review_state === 'unreviewed'
    case 'bullish':
      return signal.latest_annotation?.outlook === 'bullish'
    case 'watching':
      return signal.review_state === 'watching'
    case 'preparing':
      return signal.review_state === 'preparing'
    case 'discarded':
      return signal.review_state === 'discarded'
    case 'bought':
      return signal.confirmed_buy
  }
}

function WorkbenchMarketEvidenceView({ market }: { market: WorkbenchMarketEvidence }) {
  if (market.status !== 'available') {
    return (
      <div className="workbench-evidence-empty">
        <strong>行情证据{market.status === 'incomplete' ? '不完整' : '不可用'}</strong>
        <span>{market.reason ?? '没有已批准的市场快照，系统不填充价格。'}</span>
      </div>
    )
  }
  return (
    <div className="workbench-market-grid">
      <div><span>当前价</span><strong className="market-value">{formatPrice(market.close)}</strong></div>
      <div><span>涨跌幅</span><strong className={`market-value market-change ${Number(market.change_percent ?? 0) >= 0 ? 'market-profit' : 'market-loss'}`}>{formatPercent(market.change_percent)}</strong></div>
      <div><span>交易日</span><strong>{market.session_date ?? '—'}</strong></div>
      <div><span>快照</span><strong>{market.snapshot_id ?? '—'}</strong></div>
    </div>
  )
}

function WorkbenchAnnotationPanel({
  apiBase,
  signal,
  onSaved,
}: {
  apiBase: string
  signal: WorkbenchSignal
  onSaved: () => void | Promise<void>
}) {
  const latest = signal.latest_annotation
  const [score, setScore] = useState(String(latest?.overall_score ?? 3))
  const [outlook, setOutlook] = useState<AnnotationOutlook>(latest?.outlook ?? 'neutral')
  const [decision, setDecision] = useState<AnnotationDecision>(latest?.decision ?? 'watch')
  const [tags, setTags] = useState<Record<'position_tags' | 'pattern_tags' | 'price_volume_tags' | 'sector_tags', string[]>>({
    position_tags: latest?.position_tags ?? [],
    pattern_tags: latest?.pattern_tags ?? [],
    price_volume_tags: latest?.price_volume_tags ?? [],
    sector_tags: latest?.sector_tags ?? [],
  })
  const [note, setNote] = useState(latest?.note ?? '')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    setScore(String(signal.latest_annotation?.overall_score ?? 3))
    setOutlook(signal.latest_annotation?.outlook ?? 'neutral')
    setDecision(signal.latest_annotation?.decision ?? 'watch')
    setTags({
      position_tags: signal.latest_annotation?.position_tags ?? [],
      pattern_tags: signal.latest_annotation?.pattern_tags ?? [],
      price_volume_tags: signal.latest_annotation?.price_volume_tags ?? [],
      sector_tags: signal.latest_annotation?.sector_tags ?? [],
    })
    setNote(signal.latest_annotation?.note ?? '')
    setErrorMessage(null)
  }, [signal.candidate.candidate_id, signal.latest_annotation?.annotation_id])

  function toggleTag(key: keyof typeof tags, value: string) {
    setTags((current) => ({
      ...current,
      [key]: current[key].includes(value) ? current[key].filter((item) => item !== value) : [...current[key], value],
    }))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setErrorMessage(null)
    try {
      await requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(signal.batch.batch_id)}/annotations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            candidate_id: signal.candidate.candidate_id,
            reviewed_at: new Date().toISOString(),
            overall_score: Number(score),
            outlook,
            decision,
            position_tags: tags.position_tags,
            pattern_tags: tags.pattern_tags,
            price_volume_tags: tags.price_volume_tags,
            sector_tags: tags.sector_tags,
            reusable_tag_ids: [],
            note: note.trim() || null,
          }),
        },
      )
      await onSaved()
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '保存人工判断失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="drawer-judgment-panel">
      <div className="drawer-section-heading">
        <div>
          <span className="drawer-section-kicker">APPEND-ONLY ANNOTATION</span>
          <h3>快速判断</h3>
        </div>
        <span className="drawer-readonly-note">不覆盖历史</span>
      </div>
      {latest ? (
        <div className="drawer-latest-annotation">
          <span>上次判断 · {formatManagementDate(latest.reviewed_at)}</span>
          <strong>{'★'.repeat(latest.overall_score)}{'☆'.repeat(5 - latest.overall_score)} · {annotationOutlookLabels[latest.outlook]}</strong>
          <span>{annotationDecisionLabels[latest.decision]}{latest.note ? ` · ${latest.note}` : ''}</span>
        </div>
      ) : (
        <p className="drawer-muted-copy">尚未判断。未判断不等同于不看好。</p>
      )}
      <form className="drawer-annotation-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <legend>综合评分</legend>
          <div className="drawer-choice-row">
            {[1, 2, 3, 4, 5].map((value) => (
              <button type="button" className={`drawer-score-choice${Number(score) === value ? ' is-selected' : ''}`} key={value} onClick={() => setScore(String(value))}>
                {value}
              </button>
            ))}
          </div>
          <legend>判断</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            {(['bullish', 'neutral', 'bearish'] as AnnotationOutlook[]).map((value) => (
              <button type="button" className={`drawer-choice${outlook === value ? ' is-selected' : ''}`} key={value} onClick={() => setOutlook(value)}>
                {annotationOutlookLabels[value]}
              </button>
            ))}
          </div>
          <legend>操作意图</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            {(['discard', 'watch', 'prepare_buy'] as AnnotationDecision[]).map((value) => (
              <button type="button" className={`drawer-choice drawer-choice--${value}${decision === value ? ' is-selected' : ''}`} key={value} onClick={() => setDecision(value)}>
                {annotationDecisionLabels[value]}
              </button>
            ))}
          </div>
          <div className="drawer-tag-groups">
            {annotationTagGroups.map((group) => (
              <div className="drawer-tag-group" key={group.key}>
                <span>{group.label}</span>
                <div>
                  {group.options.map(([value, label]) => (
                    <button type="button" className={tags[group.key].includes(value) ? 'is-selected' : ''} key={value} onClick={() => toggleTag(group.key, value)}>{label}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <label className="drawer-field">
            <span>备注（可选）</span>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录这次判断的依据" rows={3} />
          </label>
        </fieldset>
        {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
        <button type="submit" className="drawer-primary-action" disabled={busy}>
          {busy ? '追加中…' : '追加本次判断'}
        </button>
      </form>
    </div>
  )
}

function WorkbenchTradePanel({
  apiBase,
  signal,
  onSaved,
}: {
  apiBase: string
  signal: WorkbenchSignal
  onSaved: () => void | Promise<void>
}) {
  const [side, setSide] = useState<'buy' | 'sell'>(signal.confirmed_buy ? 'sell' : 'buy')
  const [orderId, setOrderId] = useState(() => crypto.randomUUID())
  const [fillId, setFillId] = useState(() => crypto.randomUUID())
  const [orderedAt, setOrderedAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [filledAt, setFilledAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [quantity, setQuantity] = useState('100')
  const [price, setPrice] = useState(signal.market.close ?? '')
  const [sellReason, setSellReason] = useState<ManualSellReason>('target_reached')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    setSide(signal.confirmed_buy ? 'sell' : 'buy')
    setPrice(signal.market.close ?? '')
    setErrorMessage(null)
  }, [signal.candidate.candidate_id, signal.confirmed_buy, signal.market.close])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setErrorMessage(null)
    const commonBody = {
      order_id: orderId,
      fill_id: fillId,
      local_code: signal.candidate.local_code,
      exchange: signal.candidate.exchange,
      name: signal.candidate.name,
      ordered_at: toShanghaiDateTime(orderedAt),
      filled_at: toShanghaiDateTime(filledAt),
      quantity,
      price,
      selection_candidate_id: signal.candidate.candidate_id,
      note: note.trim() || null,
    }
    try {
      if (side === 'buy') {
        await requestJson<[ManualOrder, ManualFill]>(`${apiBase}/v1/selection-candidate-trades`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(commonBody),
        })
      } else {
        await requestJson<[ManualOrder, ManualFill]>(`${apiBase}/v1/manual-trades/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...commonBody, side, sell_reason: sellReason }),
        })
      }
      setOrderId(crypto.randomUUID())
      setFillId(crypto.randomUUID())
      setNote('')
      await onSaved()
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '保存确认交易失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="drawer-trade-panel">
      <div className="drawer-section-heading">
        <div>
          <span className="drawer-section-kicker">MANUAL EXECUTION</span>
          <h3>确认交易</h3>
        </div>
        <span className="drawer-readonly-note">不自动下单</span>
      </div>
      <p className="drawer-muted-copy">这里只记录用户已经确认的真实事实，不生成推荐价格。成交价格必须由用户填写。</p>
      <form className="drawer-trade-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <legend>方向</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            <button type="button" className={`drawer-choice drawer-choice--buy${side === 'buy' ? ' is-selected' : ''}`} disabled={signal.confirmed_buy} onClick={() => setSide('buy')}>确认买入</button>
            <button type="button" className={`drawer-choice drawer-choice--sell${side === 'sell' ? ' is-selected' : ''}`} onClick={() => setSide('sell')}>确认卖出</button>
          </div>
          {side === 'sell' && (
            <label className="drawer-field">
              <span>卖出原因（必填）</span>
              <select value={sellReason} onChange={(event) => setSellReason(event.target.value as ManualSellReason)}>
                {(Object.keys(manualSellReasonLabels) as ManualSellReason[]).map((value) => <option value={value} key={value}>{manualSellReasonLabels[value]}</option>)}
              </select>
            </label>
          )}
          <div className="drawer-trade-grid">
            <label className="drawer-field"><span>数量</span><input type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
            <label className="drawer-field"><span>成交价</span><input type="number" min="0.0001" step="0.0001" value={price} onChange={(event) => setPrice(event.target.value)} placeholder="必须人工填写" required /></label>
            <label className="drawer-field"><span>下单时间</span><input type="datetime-local" value={orderedAt} onChange={(event) => setOrderedAt(event.target.value)} required /></label>
            <label className="drawer-field"><span>成交时间</span><input type="datetime-local" value={filledAt} onChange={(event) => setFilledAt(event.target.value)} required /></label>
          </div>
          <label className="drawer-field"><span>交易备注（可选）</span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} placeholder="记录交易事实或复盘线索" /></label>
        </fieldset>
        {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
        <button type="submit" className="drawer-primary-action drawer-primary-action--trade" disabled={busy || (side === 'buy' && signal.confirmed_buy)}>{busy ? '保存中…' : side === 'buy' ? '确认并记录买入' : '确认并记录卖出'}</button>
      </form>
    </div>
  )
}

function StockDetailDrawer({
  apiBase,
  detail,
  onClose,
  onAnnotationSaved,
  onTradeSaved,
}: {
  apiBase: string
  detail: WorkbenchStockDetail
  onClose: () => void
  onAnnotationSaved: () => void | Promise<void>
  onTradeSaved: () => void | Promise<void>
}) {
  const [tab, setTab] = useState<StockDrawerTab>('market')
  const signal = detail.signals[0]

  useEffect(() => {
    setTab('market')
  }, [detail.symbol])

  return (
    <div className="stock-drawer-layer" role="presentation">
      <button type="button" className="stock-drawer-backdrop" aria-label="关闭股票详情" onClick={onClose} />
      <aside className="stock-detail-drawer" role="dialog" aria-modal="true" aria-label={`${detail.symbol} 股票详情`}>
        <header className="stock-drawer-header">
          <div>
            <span className="drawer-symbol">{detail.symbol}</span>
            <h2>{detail.name ?? '未提供名称'}</h2>
            <span className="drawer-subtitle">截至 {formatManagementDate(detail.as_of)}</span>
          </div>
          <button type="button" className="drawer-close-button" aria-label="关闭股票详情" onClick={onClose}><X aria-hidden="true" /></button>
        </header>
        <div className="stock-drawer-market-summary">
          <WorkbenchMarketEvidenceView market={detail.market} />
        </div>
        <nav className="stock-drawer-tabs" aria-label="股票详情标签">
          {([
            ['market', '行情'],
            ['judgment', '判断'],
            ['signal', '信号'],
            ['trade', '交易'],
            ['analysis', '分析'],
          ] as Array<[StockDrawerTab, string]>).map(([value, label]) => (
            <button type="button" className={tab === value ? 'is-active' : ''} key={value} onClick={() => setTab(value)}>{label}</button>
          ))}
        </nav>
        <div className="stock-drawer-body">
          {tab === 'market' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-market-title">
              <div className="drawer-section-heading"><h3 id="drawer-market-title">批准行情证据</h3><span>{detail.market.provider ?? '本地快照'}</span></div>
              <WorkbenchMarketEvidenceView market={detail.market} />
              <p className="drawer-muted-copy">行情仅读取已批准的 market_daily 快照；缺失时不伪造价格、K 线或指标。</p>
            </section>
          )}
          {tab === 'judgment' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-judgment-title">
              <WorkbenchAnnotationPanel apiBase={apiBase} signal={signal} onSaved={onAnnotationSaved} />
            </section>
          )}
          {tab === 'signal' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-signal-title">
              <div className="drawer-section-heading"><h3 id="drawer-signal-title">策略信号历史</h3><span>{detail.signals.length} 次命中</span></div>
              <div className="drawer-signal-list">
                {detail.signals.map((item) => (
                  <article className="drawer-signal-item" key={item.candidate.candidate_id}>
                    <div><strong>{item.batch.formula_name}{item.batch.formula_version ? ` · ${item.batch.formula_version}` : ''}</strong><span>{item.batch.selection_date} · {item.batch.source === 'manual' ? '手工' : '通达信'}</span></div>
                    <div className="drawer-signal-status"><span className={`workbench-status workbench-status--${item.review_state}`}>{workbenchReviewLabel(item.review_state)}</span>{item.confirmed_buy && <span className="workbench-status workbench-status--bought">已买入</span>}</div>
                  </article>
                ))}
              </div>
            </section>
          )}
          {tab === 'trade' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-trade-title">
              <div className="drawer-section-heading"><h3 id="drawer-trade-title">交易与生命周期</h3><span>{signal.confirmed_buy ? '存在买入证据' : '尚无关联买入'}</span></div>
              <WorkbenchTradePanel apiBase={apiBase} signal={signal} onSaved={onTradeSaved} />
              <div className="drawer-timeline">
                {detail.timeline.filter((event) => ['order', 'fill', 'review'].includes(event.event_type)).length === 0 ? <p className="drawer-muted-copy">暂无真实交易或复盘证据。</p> : detail.timeline.filter((event) => ['order', 'fill', 'review'].includes(event.event_type)).map((event) => (
                  <article className="drawer-timeline-item" key={event.event_id}><span className="drawer-timeline-marker" /><div><span>{formatManagementDate(event.occurred_at)} · {workbenchEventLabel(event.event_type)}</span><strong>{event.title}</strong><p>{event.detail}</p></div></article>
                ))}
              </div>
            </section>
          )}
          {tab === 'analysis' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-analysis-title">
              <div className="drawer-section-heading"><h3 id="drawer-analysis-title">客观结果</h3><span>只读证据</span></div>
              {detail.signals.map((item) => (
                <article className="drawer-analysis-block" key={item.candidate.candidate_id}>
                  <strong>{item.batch.selection_date} · {item.batch.formula_name}</strong>
                  {item.performances.length === 0 && item.outcomes.length === 0 ? <p className="drawer-muted-copy">尚无完成的未来表现或结果。</p> : (
                    <div className="drawer-analysis-metrics">
                      {item.performances.flatMap((performance) => performance.points).map((point) => <span key={`${item.candidate.candidate_id}:${point.horizon_sessions}`}>T+{point.horizon_sessions} {formatPercent(point.return_from_anchor)}</span>)}
                      {item.outcomes.map((outcome) => <span key={`${item.candidate.candidate_id}:${outcome.evaluated_at}`}>{outcome.status} {formatPercent(outcome.net_return)}</span>)}
                    </div>
                  )}
                </article>
              ))}
              <p className="drawer-muted-copy">模型概率、推荐和自动动作不会在此被推断；只有已保存的证据才会展示。</p>
            </section>
          )}
          <section className="drawer-timeline-section" aria-labelledby="drawer-timeline-title">
            <div className="drawer-section-heading"><h3 id="drawer-timeline-title">完整时间线</h3><span>{detail.timeline.length} 个事件</span></div>
            <div className="drawer-timeline">
              {detail.timeline.length === 0 ? <p className="drawer-muted-copy">暂无时间线事件。</p> : detail.timeline.map((event) => (
                <article className="drawer-timeline-item" key={event.event_id}><span className="drawer-timeline-marker" /><div><span>{formatManagementDate(event.occurred_at)} · {workbenchEventLabel(event.event_type)}</span><strong>{event.title}</strong><p>{event.detail}</p></div></article>
              ))}
            </div>
          </section>
        </div>
      </aside>
    </div>
  )
}

function workbenchActionRank(signal: WorkbenchSignal): number {
  if (!signal.latest_annotation) return 0
  if (signal.review_state === 'watching' || signal.review_state === 'preparing') return 1
  if (signal.confirmed_buy) return 3
  return 2
}

function workbenchSignalSort(signals: WorkbenchSignal[]): WorkbenchSignal[] {
  return [...signals].sort((left, right) => {
    const rankDifference = workbenchActionRank(left) - workbenchActionRank(right)
    if (rankDifference !== 0) return rankDifference
    const leftAt = left.latest_annotation?.reviewed_at ?? left.batch.selection_date
    const rightAt = right.latest_annotation?.reviewed_at ?? right.batch.selection_date
    return rightAt.localeCompare(leftAt)
  })
}

function WorkbenchChangeSummary({ signals }: { signals: WorkbenchSignal[] }) {
  const recheckSignals = signals.filter((signal) => signal.annotations.length > 1)
  const changedSignals = recheckSignals.filter((signal) => {
    const latest = signal.annotations[signal.annotations.length - 1]
    const previous = signal.annotations[signal.annotations.length - 2]
    return latest.overall_score !== previous.overall_score || latest.outlook !== previous.outlook || latest.decision !== previous.decision
  })
  return (
    <section className="workbench-change-summary" aria-label="观察池变化摘要">
      <div className="workbench-section-heading workbench-section-heading--compact">
        <div><span className="eyebrow">WATCHLIST · SINCE LAST JUDGMENT</span><h3>自上次判断以来发生了什么</h3></div>
        <span className="workbench-as-of">仅基于追加记录</span>
      </div>
      <div className="workbench-change-cards">
        <div><strong>{recheckSignals.length}</strong><span>已有复评记录</span></div>
        <div><strong>{changedSignals.length}</strong><span>判断发生变化</span></div>
        <div><strong>{signals.filter((signal) => signal.latest_annotation?.decision === 'prepare_buy').length}</strong><span>当前准备买入</span></div>
      </div>
      {changedSignals.length > 0 ? <div className="workbench-change-list">{changedSignals.slice(0, 8).map((signal) => {
        const latest = signal.annotations[signal.annotations.length - 1]
        const previous = signal.annotations[signal.annotations.length - 2]
        return <div className="workbench-change-row" key={signal.candidate.candidate_id}><strong>{signal.candidate.symbol}</strong><span>{annotationDecisionLabels[previous.decision]} → {annotationDecisionLabels[latest.decision]}</span><span>{previous.overall_score} 分 → {latest.overall_score} 分</span><small>{formatManagementDate(latest.reviewed_at)}</small></div>
      })}</div> : <p className="workbench-empty workbench-empty--compact">当前没有可比较的复评变化；未判断不等于不看好。</p>}
    </section>
  )
}

function WorkbenchSignalTable({
  signals,
  onOpen,
  emptyMessage,
}: {
  signals: WorkbenchSignal[]
  onOpen: (symbol: string) => void
  emptyMessage: string
}) {
  return signals.length === 0 ? <p className="workbench-empty">{emptyMessage}</p> : (
    <div className="workbench-table" role="table" aria-label="策略信号列表">
      <div className="workbench-table-header" role="row"><span>股票</span><span>策略</span><span>信号时间</span><span>行情</span><span>人工判断</span><span>状态</span></div>
      {signals.map((signal) => (
        <button type="button" className="workbench-table-row" role="row" key={signal.candidate.candidate_id} onClick={() => onOpen(signal.candidate.symbol)}>
          <span className="workbench-stock-cell"><strong>{signal.candidate.symbol}</strong><small>{signal.candidate.name ?? '未提供名称'}</small></span>
          <span className="workbench-table-cell"><strong>{signal.batch.formula_name}</strong><small>{signal.batch.formula_version ?? '未版本化'}</small></span>
          <span className="workbench-table-cell"><strong>{signal.batch.selection_date}</strong><small>{formatManagementDate(signal.batch.exported_at)}</small></span>
          <span className="workbench-table-cell"><strong className="market-value">{formatPrice(signal.market.close)}</strong><small className={`market-change ${Number(signal.market.change_percent ?? 0) >= 0 ? 'market-profit' : 'market-loss'}`}>{formatPercent(signal.market.change_percent)}</small></span>
          <span className="workbench-table-cell"><strong>{signal.latest_annotation ? `${'★'.repeat(signal.latest_annotation.overall_score)}${'☆'.repeat(5 - signal.latest_annotation.overall_score)}` : '—'}</strong><small>{signal.latest_annotation ? annotationOutlookLabels[signal.latest_annotation.outlook] : '未判断'}</small></span>
          <span className="workbench-table-status"><span className={`workbench-status workbench-status--${signal.review_state}`}>{workbenchReviewLabel(signal.review_state)}</span>{signal.confirmed_buy && <span className="workbench-status workbench-status--bought">已买入</span>}</span>
        </button>
      ))}
    </div>
  )
}

function WorkbenchFilterBar({
  query,
  onQueryChange,
  filter,
  onFilterChange,
  onRefresh,
  busy,
}: {
  query: string
  onQueryChange: (value: string) => void
  filter: WorkbenchReviewFilter
  onFilterChange: (value: WorkbenchReviewFilter) => void
  onRefresh: () => void
  busy: boolean
}) {
  const filters: Array<[WorkbenchReviewFilter, string]> = [
    ['all', '全部'],
    ['unreviewed', '未判断'],
    ['bullish', '看好'],
    ['watching', '观察'],
    ['preparing', '准备买入'],
    ['discarded', '不看好'],
    ['bought', '已买入'],
  ]
  return (
    <div className="workbench-filter-bar">
      <label className="workbench-search"><span className="sr-only">搜索股票或策略</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索代码、名称或策略" /></label>
      <div className="workbench-filter-buttons" role="group" aria-label="信号筛选">
        {filters.map(([value, label]) => <button type="button" className={filter === value ? 'is-active' : ''} key={value} onClick={() => onFilterChange(value)}>{label}</button>)}
      </div>
      <button type="button" className="workbench-refresh" onClick={onRefresh} disabled={busy}><RotateCcw className={busy ? 'is-spinning' : ''} aria-hidden="true" />刷新</button>
    </div>
  )
}

function workbenchServerReviewState(filter: WorkbenchReviewFilter): ReviewState | undefined {
  return filter === 'unreviewed' || filter === 'watching' || filter === 'preparing' || filter === 'discarded'
    ? filter
    : undefined
}

function WorkbenchWorkspace({
  apiBase,
  route,
}: {
  apiBase: string
  route: WorkbenchRoute
}) {
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null)
  const [signals, setSignals] = useState<WorkbenchSignal[]>([])
  const [positions, setPositions] = useState<WorkbenchPositionView[]>([])
  const [trades, setTrades] = useState<WorkbenchTradeView[]>([])
  const [strategies, setStrategies] = useState<WorkbenchStrategyView[]>([])
  const [analytics, setAnalytics] = useState<WorkbenchAnalyticsView | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<WorkbenchReviewFilter>('all')
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [signalPage, setSignalPage] = useState(1)
  const [signalTotal, setSignalTotal] = useState(0)
  const signalPageSize = 50
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [stockDetail, setStockDetail] = useState<WorkbenchStockDetail | null>(null)
  const [drawerLoading, setDrawerLoading] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setPageError(null)
    const signal = { signal: controller.signal }
    const load = async () => {
      try {
        const pageParams = new URLSearchParams({
          page: String(signalPage),
          page_size: String(signalPageSize),
        })
        if (query.trim()) {
          pageParams.set('query', query.trim())
        }
        const serverReviewState = workbenchServerReviewState(filter)
        if (serverReviewState) {
          pageParams.set('review_state', serverReviewState)
        }
        if (route === 'workbench' || route === 'signals') {
          const [nextSummary, nextSignals] = await Promise.all([
            requestJson<WorkbenchSummary>(`${apiBase}/v1/workbench/summary`, signal),
            requestJson<WorkbenchSignalPage>(`${apiBase}/v1/workbench/daily-signals/page?${pageParams.toString()}`, signal),
          ])
          setSummary(nextSummary)
          setSignals(nextSignals.items)
          setSignalTotal(nextSignals.total)
        } else if (route === 'watchlist') {
          const nextSignals = await requestJson<WorkbenchSignalPage>(`${apiBase}/v1/workbench/watchlist/page?${pageParams.toString()}`, signal)
          setSignals(nextSignals.items)
          setSignalTotal(nextSignals.total)
          setSummary(null)
        } else if (route === 'positions') {
          setPositions(await requestJson<WorkbenchPositionView[]>(`${apiBase}/v1/workbench/positions`, signal))
        } else if (route === 'trades') {
          setTrades(await requestJson<WorkbenchTradeView[]>(`${apiBase}/v1/workbench/trades`, signal))
        } else if (route === 'strategies') {
          setStrategies(await requestJson<WorkbenchStrategyView[]>(`${apiBase}/v1/workbench/strategies`, signal))
        } else {
          setAnalytics(await requestJson<WorkbenchAnalyticsView>(`${apiBase}/v1/workbench/analytics`, signal))
        }
      } catch (reason: unknown) {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setPageError(reason instanceof Error ? reason.message : '加载工作台数据失败。')
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    }
    void load()
    return () => controller.abort()
  }, [apiBase, filter, query, route, reloadToken, signalPage])

  useEffect(() => {
    setSignalPage(1)
  }, [filter, query, route])

  useEffect(() => {
    setSelectedSymbol(null)
    setStockDetail(null)
    setDrawerError(null)
  }, [route])

  async function openStock(symbol: string) {
    setSelectedSymbol(symbol)
    setStockDetail(null)
    setDrawerError(null)
    setDrawerLoading(true)
    try {
      setStockDetail(await requestJson<WorkbenchStockDetail>(`${apiBase}/v1/workbench/stocks/${encodeURIComponent(symbol)}`))
    } catch (reason: unknown) {
      setDrawerError(reason instanceof Error ? reason.message : '加载股票详情失败。')
    } finally {
      setDrawerLoading(false)
    }
  }

  async function refreshStock() {
    if (!selectedSymbol) {
      return
    }
    await openStock(selectedSymbol)
  }

  const visibleSignals = workbenchSignalSort(signals.filter((signal) => {
    const normalizedQuery = query.trim().toLowerCase()
    const matchesQuery = !normalizedQuery || [signal.candidate.symbol, signal.candidate.local_code, signal.candidate.name ?? '', signal.batch.formula_name].some((value) => value.toLowerCase().includes(normalizedQuery))
    return matchesQuery && matchesWorkbenchFilter(signal, filter)
  }))
  const nextActionableSignal = visibleSignals.find((signal) => workbenchActionRank(signal) < 2)
  const openNextActionable = () => {
    if (nextActionableSignal) void openStock(nextActionableSignal.candidate.symbol)
  }
  const summaryCards = summary ? [
    ['今日信号', summary.signal_count, 'neutral'],
    ['待判断', summary.unreviewed_count, 'pending'],
    ['观察中', summary.watching_count, 'review'],
    ['准备买入', summary.preparing_count, 'profit'],
    ['已判断', summary.reviewed_count, 'neutral'],
    ['当前已买入', summary.confirmed_buy_count, 'confirmed'],
  ] as Array<[string, number, string]> : []

  return (
    <section className="workbench-page" aria-label={`${appRouteLabels[route]}工作区`}>
      {pageError && <div className="workbench-page-error" role="alert">{pageError}</div>}
      {(route === 'workbench' || route === 'signals') && summary && (
        <div className="workbench-kpi-grid">
          {summaryCards.map(([label, value, tone]) => <article className={`workbench-kpi workbench-kpi--${tone}`} key={label}><span>{label}</span><strong className="workbench-kpi-value">{value}</strong></article>)}
        </div>
      )}
      {route === 'workbench' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">TODAY · ACTION QUEUE</span><h2>今天需要处理什么</h2><p>按“未判断 → 观察复评 → 已确认事实”排序；点击股票，在右侧详情中追加判断。</p></div><div className="workbench-heading-actions"><span className="workbench-as-of">证据截至 {summary ? formatManagementDate(summary.as_of) : '—'}</span><button type="button" className="workbench-primary-action" onClick={openNextActionable} disabled={!nextActionableSignal || loading}>连续处理待办</button></div></div>
          <WorkbenchFilterBar query={query} onQueryChange={setQuery} filter={filter} onFilterChange={setFilter} onRefresh={() => setReloadToken((value) => value + 1)} busy={loading} />
          {loading ? <div className="workbench-loading">正在读取本地研究证据…</div> : <WorkbenchSignalTable signals={visibleSignals.slice(0, 12)} onOpen={(symbol) => void openStock(symbol)} emptyMessage="当前没有待处理信号。" />}
          {!loading && visibleSignals.length > 12 && <p className="workbench-queue-note">当前页显示前 12 条；点击“连续处理待办”可从优先级最高的未判断信号开始。</p>}
          <PaginationControls page={signalPage} pageSize={signalPageSize} total={signalTotal} busy={loading} onPageChange={setSignalPage} />
        </>
      )}
      {route === 'signals' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">SIGNALS · IMMUTABLE FACTS</span><h2>今日信号</h2><p>展示所有策略命中的股票；人工判断不会改变原始信号。</p></div><span className="workbench-as-of">共 {signalTotal} 条</span></div>
          <WorkbenchFilterBar query={query} onQueryChange={setQuery} filter={filter} onFilterChange={setFilter} onRefresh={() => setReloadToken((value) => value + 1)} busy={loading} />
          {loading ? <div className="workbench-loading">正在读取信号…</div> : <WorkbenchSignalTable signals={visibleSignals} onOpen={(symbol) => void openStock(symbol)} emptyMessage="没有匹配的策略信号。" />}
          <PaginationControls page={signalPage} pageSize={signalPageSize} total={signalTotal} busy={loading} onPageChange={setSignalPage} />
        </>
      )}
      {route === 'watchlist' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">WATCHLIST · RE-EVALUATION</span><h2>观察池</h2><p>只显示已进入观察或准备买入、且尚未确认买入的信号。</p></div><span className="workbench-as-of">共 {signalTotal} 条</span></div>
          <WorkbenchFilterBar query={query} onQueryChange={setQuery} filter={filter} onFilterChange={setFilter} onRefresh={() => setReloadToken((value) => value + 1)} busy={loading} />
          {loading ? <div className="workbench-loading">正在读取观察池…</div> : <><WorkbenchChangeSummary signals={signals} /><WorkbenchSignalTable signals={visibleSignals} onOpen={(symbol) => void openStock(symbol)} emptyMessage="观察池为空。" /><PaginationControls page={signalPage} pageSize={signalPageSize} total={signalTotal} busy={loading} onPageChange={setSignalPage} /></>}
        </>
      )}
      {route === 'positions' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">POSITIONS · CONFIRMED FACTS</span><h2>持仓</h2><p>持仓由已确认成交推导，不把策略信号或推荐当作交易。</p></div><span className="workbench-as-of">共 {positions.length} 个</span></div>
          {loading ? <div className="workbench-loading">正在读取持仓…</div> : positions.length === 0 ? <p className="workbench-empty">暂无持仓。</p> : <div className="workbench-position-list">{positions.map((item) => <button type="button" className="workbench-position-row" key={item.position.security.symbol} onClick={() => void openStock(item.position.security.symbol)}><span className="workbench-stock-cell"><strong>{item.position.security.symbol}</strong><small>{item.position.security.name ?? '未提供名称'} · {item.source === 'signal' ? '策略来源' : item.source === 'mixed' ? '混合来源' : '手工发现'}</small></span><span><small>数量</small><strong>{item.position.quantity}</strong></span><span><small>成本</small><strong>{formatPrice(item.position.average_cost)}</strong></span><span><small>现价</small><strong>{formatPrice(item.market.close)}</strong></span><span><small>浮动收益</small><strong className={Number(item.unrealized_return ?? 0) >= 0 ? 'market-profit' : 'market-loss'}>{formatPercent(item.unrealized_return)}</strong></span><span><small>持有天数</small><strong>{item.holding_days}</strong></span></button>)}</div>}
        </>
      )}
      {route === 'trades' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">TRADES · MANUAL EXECUTIONS</span><h2>交易记录</h2><p>仅展示用户显式确认的订单与成交。</p></div><span className="workbench-as-of">共 {trades.length} 笔</span></div>
          {loading ? <div className="workbench-loading">正在读取交易…</div> : trades.length === 0 ? <p className="workbench-empty">暂无已确认成交。</p> : <div className="workbench-trade-list">{trades.map((trade) => <button type="button" className="workbench-trade-row" key={trade.order.order_id} onClick={() => void openStock(trade.order.security.symbol)}><span className="workbench-stock-cell"><strong>{trade.order.security.symbol}</strong><small>{trade.order.security.name ?? '未提供名称'} · {trade.source === 'signal' ? '策略来源' : trade.source === 'mixed' ? '混合来源' : '手工发现'}</small></span><span className={`trade-side trade-side--${trade.order.side}`}>{trade.order.side === 'buy' ? '买入' : '卖出'}</span><span><small>数量</small><strong>{trade.order.quantity}</strong></span><span><small>成交</small><strong>{trade.fills.length} 笔</strong></span><span><small>最近成交</small><strong>{trade.fills.length ? formatManagementDate(trade.fills[trade.fills.length - 1].filled_at) : '—'}</strong></span></button>)}</div>}
        </>
      )}
      {route === 'strategies' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">STRATEGIES · VERSIONED EVIDENCE</span><h2>策略</h2><p>按策略版本比较全部信号、人工看好和实际买入的客观结果。</p></div><span className="workbench-as-of">共 {strategies.length} 个版本</span></div>
          {loading ? <div className="workbench-loading">正在读取策略证据…</div> : strategies.length === 0 ? <p className="workbench-empty">暂无策略信号。</p> : <div className="workbench-strategy-grid">{strategies.map((strategy) => <article className="workbench-strategy-card" key={`${strategy.formula_id ?? 'none'}:${strategy.formula_version ?? 'none'}`}><div className="workbench-card-heading"><div><strong>{strategy.formula_name}</strong><span>{strategy.formula_version ?? '未版本化'}</span></div><b>{strategy.signal_count}<small>信号</small></b></div><div className="workbench-cohort-grid">{strategy.cohorts.map((cohort) => <div className="workbench-cohort" key={cohort.cohort}><span>{workbenchCohortLabel(cohort.cohort)}</span><strong>{cohort.sample_count}</strong><small>已解决 {cohort.resolved_count} · 排除 {cohort.excluded_count}</small>{cohort.horizons[0] && <small>T+{cohort.horizons[0].horizon_sessions} 平均 {formatPercent(cohort.horizons[0].average_return)}</small>}</div>)}</div></article>)}</div>}
        </>
      )}
      {route === 'analytics' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">ANALYTICS · HUMAN VS EVIDENCE</span><h2>分析</h2><p>指标只来自已解决的客观表现；缺失样本保持为空。</p></div><span className="workbench-as-of">截至 {analytics ? formatManagementDate(analytics.as_of) : '—'}</span></div>
          {loading ? <div className="workbench-loading">正在读取分析…</div> : !analytics || analytics.cohorts.length === 0 ? <p className="workbench-empty">暂无可比较的策略证据。</p> : <div className="workbench-analytics-grid">{analytics.cohorts.map((cohort) => <article className="workbench-analytics-card" key={cohort.cohort}><div className="workbench-card-heading"><div><strong>{workbenchCohortLabel(cohort.cohort)}</strong><span>样本 {cohort.sample_count} · 已解决 {cohort.resolved_count}</span></div><span className="workbench-status workbench-status--neutral">只读</span></div><div className="workbench-analytics-horizons">{cohort.horizons.map((horizon) => <div key={horizon.horizon_sessions}><span>T+{horizon.horizon_sessions}</span><strong>{formatPercent(horizon.average_return)}</strong><small>{horizon.sample_count} 个表现样本</small></div>)}</div></article>)}</div>}
        </>
      )}
      {(route === 'positions' || route === 'trades') && (
        <details className="workbench-legacy-tools">
          <summary>打开现有手工台账录入</summary>
          <p>交易写入仍保留在独立的手工台账流程中；这里不会把策略信号自动转换为订单。</p>
          <PortfolioWorkspace
            apiBase={apiBase}
            formatDate={formatManagementDate}
            formatPrice={formatPrice}
          />
        </details>
      )}
      {selectedSymbol && drawerLoading && <div className="stock-drawer-loading" role="status">正在读取 {selectedSymbol} 的证据…</div>}
      {selectedSymbol && drawerError && <div className="stock-drawer-loading stock-drawer-loading--error" role="alert">{drawerError}<button type="button" onClick={() => setSelectedSymbol(null)}>关闭</button></div>}
      {stockDetail && <StockDetailDrawer apiBase={apiBase} detail={stockDetail} onClose={() => { setSelectedSymbol(null); setStockDetail(null) }} onAnnotationSaved={async () => { setReloadToken((value) => value + 1); await refreshStock() }} onTradeSaved={async () => { setReloadToken((value) => value + 1); await refreshStock() }} />}
    </section>
  )
}

function App() {
  const [workspace, setWorkspace] = useState<Workspace>('research')
  const [activeRoute, setActiveRoute] = useState<AppRoute>('workbench')
  const [healthState, setHealthState] = useState<HealthState>('checking')
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
  const [provider, setProvider] = useState<Provider>('tushare')
  const [evaluatedAt, setEvaluatedAt] = useState('')
  const [horizonSessions, setHorizonSessions] = useState('5')
  const [positionNotional, setPositionNotional] = useState('10000')
  const [targetReturn, setTargetReturn] = useState('')
  const [maxRetries, setMaxRetries] = useState('2')
  const [timeoutSeconds, setTimeoutSeconds] = useState('900')
  const [maturityTasks, setMaturityTasks] = useState<MaturityTask[]>([])
  const [maturityTaskAction, setMaturityTaskAction] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [batchReloadToken, setBatchReloadToken] = useState(0)
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [loadingBatch, setLoadingBatch] = useState(false)
  const [loadingMaturityTasks, setLoadingMaturityTasks] = useState(false)
  const [annotationCandidateId, setAnnotationCandidateId] = useState<string | null>(null)
  const [annotationReviewedAt, setAnnotationReviewedAt] = useState('')
  const [annotationScore, setAnnotationScore] = useState('3')
  const [annotationOutlook, setAnnotationOutlook] = useState<AnnotationOutlook>('neutral')
  const [annotationDecision, setAnnotationDecision] = useState<AnnotationDecision>('watch')
  const [annotationTags, setAnnotationTags] = useState<Record<string, string[]>>({})
  const [annotationReusableTagIds, setAnnotationReusableTagIds] = useState<string[]>([])
  const [annotationNewTagLabels, setAnnotationNewTagLabels] = useState<Record<ReusableTagCategory, string>>({
    position: '',
    pattern: '',
    price_volume: '',
    sector: '',
  })
  const [annotationTagBusy, setAnnotationTagBusy] = useState<ReusableTagCategory | null>(null)
  const [reusableTags, setReusableTags] = useState<ReusableTag[]>([])
  const [annotationNote, setAnnotationNote] = useState('')
  const [annotationBusy, setAnnotationBusy] = useState(false)
  const [tradeCandidateId, setTradeCandidateId] = useState<string | null>(null)
  const [tradeOrderId, setTradeOrderId] = useState('')
  const [tradeFillId, setTradeFillId] = useState('')
  const [tradeOrderedAt, setTradeOrderedAt] = useState('')
  const [tradeFilledAt, setTradeFilledAt] = useState('')
  const [tradeQuantity, setTradeQuantity] = useState('100')
  const [tradePrice, setTradePrice] = useState('')
  const [tradeNote, setTradeNote] = useState('')
  const [tradeBusy, setTradeBusy] = useState(false)
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
    void loadReusableTags()
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
      void loadReusableTags()
    }
  }, [workspace])

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

  async function createAndRunMaturityTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedBatchId || !evaluatedAt || !horizonSessions || !positionNotional) {
      return
    }

    const horizon = Number(horizonSessions)
    const retries = Number(maxRetries)
    const timeout = Number(timeoutSeconds)
    const notional = Number(positionNotional)
    const target = targetReturn === '' ? null : Number(targetReturn)
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
    if (target !== null && (!Number.isFinite(target) || target < 0)) {
      setError('目标收益必须是非负数，例如 0.10 表示 10%。')
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
            target_return: targetReturn === '' ? null : targetReturn,
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

  function openAnnotation(candidateView: CandidateView) {
    setAnnotationCandidateId(candidateView.candidate.candidate_id)
    setAnnotationReviewedAt(new Date().toISOString().slice(0, 16))
    setAnnotationScore('3')
    setAnnotationOutlook('neutral')
    setAnnotationDecision('watch')
    setAnnotationTags({})
    setAnnotationReusableTagIds([])
    setAnnotationNewTagLabels({ position: '', pattern: '', price_volume: '', sector: '' })
    setAnnotationNote('')
  }

  function closeAnnotation() {
    if (!annotationBusy) {
      setAnnotationCandidateId(null)
    }
  }

  function toggleAnnotationTag(group: string, tag: string) {
    setAnnotationTags((current) => {
      const values = current[group] ?? []
      return {
        ...current,
        [group]: values.includes(tag) ? values.filter((item) => item !== tag) : [...values, tag],
      }
    })
  }

  function toggleReusableTag(tagId: string) {
    setAnnotationReusableTagIds((current) =>
      current.includes(tagId) ? current.filter((item) => item !== tagId) : [...current, tagId],
    )
  }

  async function loadReusableTags() {
    try {
      const tags = await requestJson<ReusableTag[]>(`${apiBase}/v1/annotation-tags`)
      setReusableTags(tags)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '加载评审标签失败。')
    }
  }

  async function createAndSelectReusableTag(category: ReusableTagCategory) {
    const label = annotationNewTagLabels[category].trim()
    if (!label || annotationTagBusy !== null) {
      return
    }
    setAnnotationTagBusy(category)
    setError(null)
    try {
      const tag = await requestJson<ReusableTag>(`${apiBase}/v1/annotation-tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, label }),
      })
      setReusableTags((current) => [tag, ...current.filter((item) => item.tag_id !== tag.tag_id)])
      setAnnotationReusableTagIds((current) => (
        current.includes(tag.tag_id) ? current : [...current, tag.tag_id]
      ))
      setAnnotationNewTagLabels((current) => ({ ...current, [category]: '' }))
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '创建评审标签失败。')
    } finally {
      setAnnotationTagBusy(null)
    }
  }

  function openCandidateTrade(candidateView: CandidateView) {
    const now = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 16)
    setTradeCandidateId(candidateView.candidate.candidate_id)
    setTradeOrderId(`order-${Date.now()}`)
    setTradeFillId(`fill-${Date.now()}`)
    setTradeOrderedAt(now)
    setTradeFilledAt(now)
    setTradeQuantity('100')
    setTradePrice(candidateView.candidate.source_fields['现价'] ?? '')
    setTradeNote('')
  }

  function closeCandidateTrade() {
    if (!tradeBusy) {
      setTradeCandidateId(null)
    }
  }

  async function submitCandidateTrade(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!tradeCandidateId) {
      return
    }
    const candidateView = selectedBatch?.candidates.find(
      (item) => item.candidate.candidate_id === tradeCandidateId,
    )
    if (!candidateView) {
      return
    }
    setTradeBusy(true)
    setError(null)
    try {
      await requestJson<[ManualOrder, { order_id: string }]>(`${apiBase}/v1/selection-candidate-trades`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_id: tradeOrderId.trim(),
          fill_id: tradeFillId.trim(),
          selection_candidate_id: tradeCandidateId,
          local_code: candidateView.candidate.local_code,
          exchange: candidateView.candidate.exchange,
          name: candidateView.candidate.name,
          ordered_at: toShanghaiDateTime(tradeOrderedAt),
          filled_at: toShanghaiDateTime(tradeFilledAt),
          quantity: tradeQuantity,
          price: tradePrice,
          note: tradeNote.trim() || null,
        }),
      })
      setTradeCandidateId(null)
      setActiveRoute('positions')
      setWorkspace('portfolio')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存实际买入失败。')
    } finally {
      setTradeBusy(false)
    }
  }

  async function submitAnnotation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedBatchId || !annotationCandidateId) {
      return
    }
    setAnnotationBusy(true)
    setError(null)
    try {
      const result = await requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(selectedBatchId)}/annotations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            candidate_id: annotationCandidateId,
            reviewed_at: toShanghaiDateTime(annotationReviewedAt),
            overall_score: Number(annotationScore),
            outlook: annotationOutlook,
            decision: annotationDecision,
            position_tags: annotationTags.position_tags ?? [],
            pattern_tags: annotationTags.pattern_tags ?? [],
            price_volume_tags: annotationTags.price_volume_tags ?? [],
            sector_tags: annotationTags.sector_tags ?? [],
            reusable_tag_ids: annotationReusableTagIds,
            note: annotationNote.trim() || null,
          }),
        },
      )
      setSelectedBatch(result)
      setAnnotationCandidateId(null)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存人工评审失败。')
    } finally {
      setAnnotationBusy(false)
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

  function navigateTo(route: AppRoute) {
    setActiveRoute(route)
    if (route === 'settings') {
      setWorkspace('management')
    } else if (route === 'positions' || route === 'trades') {
      setWorkspace('portfolio')
    } else {
      setWorkspace('research')
    }
  }

  return (
    <main className="app-shell">
      <AppSidebar activeRoute={activeRoute} onNavigate={navigateTo} />
      <div className="app-main-column">
        <OperationalStatusBar healthState={healthState} activeRoute={activeRoute} />
        <section className="app-content-shell">
          <header className="content-page-heading">
            <div>
              <p className="eyebrow">STRATGAUGE · 策衡</p>
              <h1>{appRouteLabels[activeRoute]}</h1>
              <p className="content-page-summary">
                {activeRoute === 'settings'
                  ? '公式、标签和批次等低频管理。'
                  : activeRoute === 'import'
                    ? '导入通达信收盘后结果，保留原始文件证据并创建选股批次。'
                    : '从策略信号出发，保留人工判断、客观行情与真实交易的证据边界。'}
              </p>
            </div>
            <div className="content-page-meta">
              <span>证据驱动</span>
              <span>不自动交易</span>
            </div>
          </header>

      {activeRoute !== 'settings' && activeRoute !== 'import' ? (
        <WorkbenchWorkspace apiBase={apiBase} route={activeRoute as WorkbenchRoute} />
      ) : workspace === 'management' ? (
        <DataManagementWorkspace
          apiBase={apiBase}
          onFormulaCreated={() => void loadFormulas()}
        />
      ) : workspace === 'portfolio' ? (
        <PortfolioWorkspace
          apiBase={apiBase}
          formatDate={formatManagementDate}
          formatPrice={formatPrice}
        />
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

      {error && <ErrorAlert message={error} onDismiss={() => setError(null)} />}

      <MlReadinessPanel apiBase={apiBase} />

      <MainBoardMarketSyncPanel apiBase={apiBase} />

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
                    <CalendarInput
                      id="manual-selection-date"
                      value={manualSelectionDate}
                      onChange={setManualSelectionDate}
                      required
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="manual-recorded-at">记录时间（北京时间）</Label>
                    <CalendarInput
                      id="manual-recorded-at"
                      type="datetime-local"
                      value={manualRecordedAt}
                      onChange={setManualRecordedAt}
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

      <Card className="candidate-panel panel" aria-labelledby="candidate-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">03 · 候选</p>
            <h2 id="candidate-title">候选股票（只读）</h2>
          </div>
          <div className="section-heading-actions">
            {selectedBatch && (
              <span className="section-meta">
                <strong>{selectedBatch.batch.selection_date}</strong> · {selectedBatch.batch.formula_name}
              </span>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="secondary-button"
              disabled={!selectedBatchId || recommendationLoading}
              data-saving={recommendationLoading}
              aria-expanded={Boolean(recommendationWorkbench)}
              onClick={() => void loadRecommendationWorkbench()}
            >
              {recommendationLoading ? '加载中…' : '打开推荐工作台'}
            </Button>
          </div>
        </div>
        <p className="candidate-readonly-note">这里仅查看导入批次和历史证据。新的人工判断请进入“工作台 / 今日信号”，打开股票详情后使用“判断”Tab；旧标签仅保留为历史兼容数据，不再从此处新增或覆盖。</p>
        {recommendationWorkbench && <RecommendationWorkbenchPanel data={recommendationWorkbench} />}
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
              <strong>{formatWinRate(selectedStats.summary)}</strong>
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
            {selectedBatch.candidates.map((candidateView) => {
              const { candidate, labels, outcomes, performances } = candidateView
              const label = latestLabel(labels)
              const outcome = latestOutcome(outcomes)
              const performance = latestPerformance(performances)
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
                      <Badge variant={candidateView.latest_annotation ? 'secondary' : 'outline'}>
                        {candidateView.latest_annotation
                          ? `${annotationDecisionLabels[candidateView.latest_annotation.decision]} · ${candidateView.latest_annotation.overall_score}分`
                          : label
                            ? `旧标签：${decisionLabel(label.decision)}`
                            : '未评审'}
                      </Badge>
                      {candidateView.latest_annotation && (
                        <Badge variant="outline">
                          {annotationOutlookLabels[candidateView.latest_annotation.outlook]}
                        </Badge>
                      )}
                      {candidateView.latest_annotation?.reusable_tag_snapshots.map((tag) => (
                        <Badge
                          key={tag.tag_id}
                          variant="outline"
                          className="reusable-tag-badge"
                          title={`历史快照：${tag.category}`}
                        >
                          {tag.label}
                        </Badge>
                      ))}
                      <Badge variant={candidateView.excluded ? 'outline' : 'secondary'}>
                        {candidateView.excluded ? '已排除' : reviewStateLabel(candidateView.review_state)}
                      </Badge>
                      <Badge variant={outcome ? 'secondary' : 'outline'} className={`outcome-badge outcome-badge--${outcome?.status ?? 'pending'}`}>
                        {outcome ? outcomeLabel(outcome.status) : '结果待跟踪'}
                      </Badge>
                    </div>
                    {performance && (
                      <div className="performance-strip" aria-label={`${candidate.symbol} 未来表现`}>
                        <span>未来表现</span>
                        {performance.points.map((point) => (
                          <span key={point.horizon_sessions}>
                            T+{point.horizon_sessions} {formatPercent(point.return_from_anchor)}
                            {point.target_achieved === true ? ' · 达标' : ''}
                          </span>
                        ))}
                      </div>
                    )}
                    {candidateView.annotations.some((annotation) => annotation.reusable_tag_snapshots.length > 0) && (
                      <div className="annotation-history" aria-label={`${candidate.symbol} 可复用标签历史`}>
                        <span className="annotation-history-title">标签历史</span>
                        {candidateView.annotations.map((annotation) => (
                          <div className="annotation-history-row" key={annotation.annotation_id}>
                            <span>{formatManagementDate(annotation.reviewed_at)}</span>
                            <div className="badge-row">
                              {annotation.reusable_tag_snapshots.map((tag) => (
                                <Badge
                                  key={tag.tag_id}
                                  variant="outline"
                                  className="reusable-tag-badge"
                                  title={`评审快照 · ${tag.category}`}
                                >
                                  {tag.label}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="candidate-analysis-actions">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="secondary-button"
                        disabled={analysisLoadingId !== null || modelScoreLoadingId !== null}
                        data-saving={analysisLoadingId === `${selectedBatchId}:${candidate.candidate_id}`}
                        aria-expanded={Boolean(candidateAnalyses[candidate.candidate_id])}
                        onClick={() => void loadCandidateAnalysis(candidate.candidate_id)}
                      >
                        {analysisLoadingId === `${selectedBatchId}:${candidate.candidate_id}` ? '分析中…' : '查看历史条件分析'}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="secondary-button"
                        disabled={analysisLoadingId !== null || modelScoreLoadingId !== null}
                        data-saving={modelScoreLoadingId === `${selectedBatchId}:${candidate.candidate_id}`}
                        aria-expanded={Boolean(candidateModelScores[candidate.candidate_id])}
                        onClick={() => void loadCandidateModelScores(candidate.candidate_id)}
                      >
                        {modelScoreLoadingId === `${selectedBatchId}:${candidate.candidate_id}` ? '加载中…' : '查看模型影子证据'}
                      </Button>
                      {candidateAnalyses[candidate.candidate_id] && (
                        <CandidateAnalysisPanel analysis={candidateAnalyses[candidate.candidate_id]} />
                      )}
                      {candidateModelScores[candidate.candidate_id] && (
                        <CandidateModelScorePanel data={candidateModelScores[candidate.candidate_id]} />
                      )}
                    </div>
                    <span className="candidate-readonly-hint">人工判断与交易记录请从工作台股票详情进入。</span>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </Card>

      <Dialog open={annotationCandidateId !== null} onOpenChange={(open) => !open && closeAnnotation()}>
        <DialogContent className="annotation-dialog">
          <DialogHeader>
            <DialogTitle>新增人工评审</DialogTitle>
            <DialogDescription>
              {selectedBatch?.candidates.find((item) => item.candidate.candidate_id === annotationCandidateId)?.candidate.symbol ?? '候选股票'}
              。每次提交都会追加历史，不会覆盖之前的评审。
            </DialogDescription>
          </DialogHeader>
          <form className="annotation-form" onSubmit={submitAnnotation}>
            <div className="annotation-form-grid">
              <div className="field">
                <Label htmlFor="annotation-reviewed-at">评审时间（北京时间）</Label>
                <CalendarInput
                  id="annotation-reviewed-at"
                  type="datetime-local"
                  value={annotationReviewedAt}
                  onChange={setAnnotationReviewedAt}
                  required
                />
              </div>
              <div className="field">
                <Label htmlFor="annotation-score">综合评分（1～5）</Label>
                <select id="annotation-score" className="native-select" value={annotationScore} onChange={(event) => setAnnotationScore(event.target.value)}>
                  {[1, 2, 3, 4, 5].map((score) => <option key={score} value={score}>{score} 分</option>)}
                </select>
              </div>
              <div className="field">
                <Label htmlFor="annotation-outlook">观点</Label>
                <select id="annotation-outlook" className="native-select" value={annotationOutlook} onChange={(event) => setAnnotationOutlook(event.target.value as AnnotationOutlook)}>
                  {Object.entries(annotationOutlookLabels).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                </select>
              </div>
              <div className="field">
                <Label htmlFor="annotation-decision">当前决策</Label>
                <select id="annotation-decision" className="native-select" value={annotationDecision} onChange={(event) => setAnnotationDecision(event.target.value as AnnotationDecision)}>
                  {Object.entries(annotationDecisionLabels).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                </select>
              </div>
            </div>
            <div className="annotation-tag-groups">
              {annotationTagGroups.map((group) => {
                const categoryTags = reusableTags.filter((tag) => tag.category === group.reusableCategory && tag.active)
                return (
                  <fieldset className="annotation-tag-group" key={group.key}>
                    <legend>{group.label}</legend>
                    <div className="annotation-tag-options">
                      {group.options.map(([value, text]) => {
                        const checked = annotationTags[group.key]?.includes(value) ?? false
                        return (
                          <label className="annotation-tag-option" key={value}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleAnnotationTag(group.key, value)}
                            />
                            <span>{text}</span>
                          </label>
                        )
                      })}
                      {categoryTags.map((tag) => (
                        <label className="annotation-tag-option annotation-tag-option--reusable" key={tag.tag_id}>
                          <input
                            type="checkbox"
                            checked={annotationReusableTagIds.includes(tag.tag_id)}
                            onChange={() => toggleReusableTag(tag.tag_id)}
                          />
                          <span>{tag.label}</span>
                        </label>
                      ))}
                    </div>
                    <div className="annotation-tag-create">
                      <Input
                        value={annotationNewTagLabels[group.reusableCategory]}
                        onChange={(event) => setAnnotationNewTagLabels((current) => ({
                          ...current,
                          [group.reusableCategory]: event.target.value,
                        }))}
                        placeholder={`新建${group.label}标签`}
                        aria-label={`新建${group.label}标签`}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            void createAndSelectReusableTag(group.reusableCategory)
                          }
                        }}
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={annotationBusy || annotationTagBusy !== null || !annotationNewTagLabels[group.reusableCategory].trim()}
                        data-saving={annotationTagBusy === group.reusableCategory}
                        onClick={() => void createAndSelectReusableTag(group.reusableCategory)}
                      >
                        {annotationTagBusy === group.reusableCategory ? '创建中…' : '新建并选中'}
                      </Button>
                    </div>
                  </fieldset>
                )
              })}
            </div>
            <div className="field">
              <Label htmlFor="annotation-note">自由备注（可选）</Label>
              <textarea
                id="annotation-note"
                className="annotation-note"
                value={annotationNote}
                onChange={(event) => setAnnotationNote(event.target.value)}
                placeholder="记录当时为什么这样判断"
                rows={3}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={annotationBusy} onClick={closeAnnotation}>取消</Button>
              <Button type="submit" disabled={annotationBusy}>{annotationBusy ? '保存中…' : '保存评审'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={tradeCandidateId !== null} onOpenChange={(open) => !open && closeCandidateTrade()}>
        <DialogContent className="annotation-dialog">
          <DialogHeader>
            <DialogTitle>记录实际买入</DialogTitle>
            <DialogDescription>
              这会同时写入关联候选的真实订单和成交，不会修改人工评审历史。
            </DialogDescription>
          </DialogHeader>
          <form className="annotation-form" onSubmit={submitCandidateTrade}>
            <div className="annotation-form-grid">
              <div className="field">
                <Label htmlFor="trade-order-id">订单 ID</Label>
                <Input id="trade-order-id" value={tradeOrderId} onChange={(event) => setTradeOrderId(event.target.value)} required />
              </div>
              <div className="field">
                <Label htmlFor="trade-fill-id">成交 ID</Label>
                <Input id="trade-fill-id" value={tradeFillId} onChange={(event) => setTradeFillId(event.target.value)} required />
              </div>
              <div className="field">
                <Label htmlFor="trade-ordered-at">下单时间（北京时间）</Label>
                <CalendarInput id="trade-ordered-at" type="datetime-local" value={tradeOrderedAt} onChange={setTradeOrderedAt} required />
              </div>
              <div className="field">
                <Label htmlFor="trade-filled-at">成交时间（北京时间）</Label>
                <CalendarInput id="trade-filled-at" type="datetime-local" value={tradeFilledAt} onChange={setTradeFilledAt} required />
              </div>
              <div className="field">
                <Label htmlFor="trade-quantity">成交数量</Label>
                <Input id="trade-quantity" type="number" min="1" value={tradeQuantity} onChange={(event) => setTradeQuantity(event.target.value)} required />
              </div>
              <div className="field">
                <Label htmlFor="trade-price">成交价</Label>
                <Input id="trade-price" type="number" min="0.0001" step="0.0001" value={tradePrice} onChange={(event) => setTradePrice(event.target.value)} required />
              </div>
            </div>
            <div className="field">
              <Label htmlFor="trade-note">备注（可选）</Label>
              <textarea id="trade-note" className="annotation-note" value={tradeNote} onChange={(event) => setTradeNote(event.target.value)} rows={3} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={tradeBusy} onClick={closeCandidateTrade}>取消</Button>
              <Button type="submit" disabled={tradeBusy}>{tradeBusy ? '保存中…' : '保存订单与成交'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {false && <Card className="mature-panel panel" aria-labelledby="mature-title">
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
            <CalendarInput
              id="evaluated-at"
              type="datetime-local"
              value={evaluatedAt}
              onChange={setEvaluatedAt}
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
            <Label htmlFor="target-return">目标收益（可选）</Label>
            <Input
              id="target-return"
              type="number"
              min="0"
              step="0.01"
              value={targetReturn}
              onChange={(event) => setTargetReturn(event.target.value)}
              disabled={maturityTaskAction !== null}
              placeholder="例如 0.10"
            />
            <small>留空不判断达标；0.10 表示 10%。</small>
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
      </Card>}

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
        </section>
      </div>
    </main>
  )
}

function DataManagementWorkspace({
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
  const [formulaDialogOpen, setFormulaDialogOpen] = useState(false)
  const [formulaBusy, setFormulaBusy] = useState(false)
  const [newFormulaName, setNewFormulaName] = useState('')
  const [newFormulaVersion, setNewFormulaVersion] = useState('')
  const [newFormulaDescription, setNewFormulaDescription] = useState('')
  const [editFormulaName, setEditFormulaName] = useState('')
  const [editFormulaVersion, setEditFormulaVersion] = useState('')
  const [editFormulaDescription, setEditFormulaDescription] = useState('')
  const [editFormulaBusy, setEditFormulaBusy] = useState(false)
  const [managedTags, setManagedTags] = useState<ManagedReusableTag[]>([])
  const [tagEditId, setTagEditId] = useState<string | null>(null)
  const [tagEditLabel, setTagEditLabel] = useState('')
  const [tagBusyKey, setTagBusyKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function loadManagement(requestedPage = 1) {
    setLoading(true)
    try {
      const batchParams = new URLSearchParams({ page: String(requestedPage), page_size: String(batchPageSize) })
      const [formulaResult, batchResult, tagResult] = await Promise.all([
        requestJson<ManagedFormula[]>(`${apiBase}/v1/data-management/formulas`),
        requestJson<ManagedBatchPage>(`${apiBase}/v1/data-management/batches/page?${batchParams.toString()}`),
        requestJson<ManagedReusableTag[]>(`${apiBase}/v1/data-management/annotation-tags`),
      ])
      setFormulas(formulaResult)
      setBatches(batchResult.items)
      setBatchPage(batchResult.page)
      setBatchTotal(batchResult.total)
      setManagedTags(tagResult)
      setSelectedBatchId((current) => {
        if (current && batchResult.items.some((item) => item.batch.batch_id === current)) {
          return current
        }
        return batchResult.items[0]?.batch.batch_id ?? null
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

  async function createManagedFormula(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!newFormulaName.trim()) {
      return
    }
    setFormulaBusy(true)
    setError(null)
    try {
      await requestJson<{ formula: Formula }>(`${apiBase}/v1/formulas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newFormulaName.trim(),
          version: newFormulaVersion.trim() || null,
          description: newFormulaDescription.trim() || null,
        }),
      })
      await loadManagement()
      onFormulaCreated()
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

  function beginTagRename(tag: ManagedReusableTag) {
    setTagEditId(tag.tag_id)
    setTagEditLabel(tag.label)
    setError(null)
  }

  async function renameManagedTag(tag: ManagedReusableTag) {
    const label = tagEditLabel.trim()
    if (!label || tagBusyKey !== null) {
      return
    }
    const key = `rename:${tag.tag_id}`
    setTagBusyKey(key)
    setError(null)
    try {
      await requestJson<ManagedReusableTag>(
        `${apiBase}/v1/data-management/annotation-tags/${encodeURIComponent(tag.tag_id)}/rename`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label }),
        },
      )
      await loadManagement()
      setTagEditId(null)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存评审标签改名失败。')
    } finally {
      setTagBusyKey(null)
    }
  }

  async function changeManagedTagStatus(tag: ManagedReusableTag) {
    const action = tag.active ? 'archive' : 'restore'
    const key = `tags:${tag.tag_id}:${action}`
    setTagBusyKey(key)
    setError(null)
    try {
      await requestJson<ManagedReusableTag>(
        `${apiBase}/v1/data-management/annotation-tags/${encodeURIComponent(tag.tag_id)}/${action}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: tag.active ? 'Web 评审标签管理操作' : 'Web 评审标签恢复' }),
        },
      )
      await loadManagement()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '更新评审标签状态失败。')
    } finally {
      setTagBusyKey(null)
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

      {error && <ErrorAlert message={error} onDismiss={() => setError(null)} />}

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

      {loading ? (
        <div className="management-loading">
          <Skeleton />
          <Skeleton />
          <Skeleton />
        </div>
      ) : (
        <>
          <div className="management-grid">
            <Card className="management-section panel management-tags-panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">评审标签目录</p>
                  <h2>可复用标签</h2>
                </div>
                <Badge variant="outline">{managedTags.length} 项</Badge>
              </div>
              <p className="form-hint">改名和归档只追加生命周期事件；历史评审保留当时的标签快照。可复用标签不会自动进入机器学习数据。</p>
              {managedTags.length === 0 ? (
                <p className="empty-copy">还没有可复用标签。可以在评审表单中直接新建。</p>
              ) : (
                <div className="management-record-list">
                  {managedTags.map((tag) => {
                    const action = tag.active ? 'archive' : 'restore'
                    const actionKey = `tags:${tag.tag_id}:${action}`
                    const editing = tagEditId === tag.tag_id
                    return (
                      <article className="management-record" key={tag.tag_id}>
                        <div className="management-record-heading">
                          <div>
                            <strong>{tag.label}</strong>
                            <span>{reusableTagCategoryLabels[tag.category]} · {tag.tag_id}</span>
                          </div>
                          <Badge variant={tag.active ? 'secondary' : 'outline'} className={`lifecycle-badge lifecycle-badge--${tag.active ? 'active' : 'archived'}`}>
                            {tag.active ? '启用' : '已归档'}
                          </Badge>
                        </div>
                        {editing ? (
                          <div className="management-tag-edit">
                            <Input
                              value={tagEditLabel}
                              onChange={(event) => setTagEditLabel(event.target.value)}
                              aria-label={`修改标签 ${tag.label}`}
                              autoFocus
                            />
                            <Button
                              type="button"
                              size="sm"
                              disabled={tagBusyKey !== null || !tagEditLabel.trim()}
                              data-saving={tagBusyKey === `rename:${tag.tag_id}`}
                              onClick={() => void renameManagedTag(tag)}
                            >
                              {tagBusyKey === `rename:${tag.tag_id}` ? '保存中…' : '保存改名'}
                            </Button>
                            <Button type="button" size="sm" variant="ghost" disabled={tagBusyKey !== null} onClick={() => setTagEditId(null)}>取消</Button>
                          </div>
                        ) : (
                          <div className="management-record-actions">
                            <Button type="button" size="sm" variant="outline" onClick={() => beginTagRename(tag)}>改名</Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={tagBusyKey !== null}
                              data-saving={tagBusyKey === actionKey}
                              onClick={() => void changeManagedTagStatus(tag)}
                            >
                              {tag.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                              {tagBusyKey === actionKey ? '处理中…' : tag.active ? '归档' : '恢复'}
                            </Button>
                          </div>
                        )}
                      </article>
                    )
                  })}
                </div>
              )}
            </Card>

            <Card className="management-section panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">公式目录</p>
                  <h2>手动创建的公式</h2>
                </div>
                <div className="management-badge-group">
                  <Badge variant="outline">{visibleFormulas.length} 项</Badge>
                  <Dialog
                    open={formulaDialogOpen}
                    onOpenChange={(open) => {
                      if (!open && formulaBusy) {
                        return
                      }
                      setFormulaDialogOpen(open)
                    }}
                  >
                    <DialogTrigger asChild>
                      <Button type="button" size="sm" variant="outline">
                        <Plus className="h-4 w-4" />
                        新建公式
                      </Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>新建选股公式</DialogTitle>
                        <DialogDescription>只保存公式名称和版本留痕，公式仍由通达信执行。</DialogDescription>
                      </DialogHeader>
                      <form className="dialog-form" onSubmit={createManagedFormula}>
                        <div className="field">
                          <Label htmlFor="management-new-formula-name">公式名称</Label>
                          <Input
                            id="management-new-formula-name"
                            value={newFormulaName}
                            onChange={(event) => setNewFormulaName(event.target.value)}
                            required
                          />
                        </div>
                        <div className="field">
                          <Label htmlFor="management-new-formula-version">公式版本（可选）</Label>
                          <Input
                            id="management-new-formula-version"
                            value={newFormulaVersion}
                            onChange={(event) => setNewFormulaVersion(event.target.value)}
                            placeholder="例如 v1"
                          />
                        </div>
                        <div className="field">
                          <Label htmlFor="management-new-formula-description">说明（可选）</Label>
                          <Input
                            id="management-new-formula-description"
                            value={newFormulaDescription}
                            onChange={(event) => setNewFormulaDescription(event.target.value)}
                          />
                        </div>
                        <DialogFooter>
                          <Button type="submit" disabled={formulaBusy || !newFormulaName.trim()}>
                            {formulaBusy ? '创建中…' : '创建公式'}
                          </Button>
                        </DialogFooter>
                      </form>
                    </DialogContent>
                  </Dialog>
                </div>
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
                <Badge variant="outline">{batchTotal} 项</Badge>
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
                            onClick={() => selectManagedBatch(item.batch.batch_id)}
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
              <PaginationControls
                page={batchPage}
                pageSize={batchPageSize}
                total={batchTotal}
                busy={loading}
                onPageChange={(page) => void loadManagement(page)}
              />
            </Card>
          </div>

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

function formatWinRate(summary: StatsSummary): string {
  if (!summary.evidence_sufficient) {
    return `证据不足（${summary.completed}/${summary.minimum_evidence_samples}）`
  }
  return formatPercent(summary.win_rate)
}

function formatPrice(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(2) : value
}

function formatModelScore(value: string): string {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(4) : '—'
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

function reviewStateLabel(state: ReviewState): string {
  const labels: Record<ReviewState, string> = {
    unreviewed: '未评审',
    discarded: '已放弃',
    watching: '观察中',
    preparing: '准备买入',
  }
  return labels[state]
}

export default App
