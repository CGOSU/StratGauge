import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import {
  Archive,
  CalendarDays,
  Database,
  Download,
  LayoutDashboard,
  Pencil,
  Plus,
  RotateCcw,
  Upload,
  WalletCards,
  X,
} from 'lucide-react'
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
type OutcomeStatus = 'win' | 'loss' | 'flat' | 'no_entry' | 'unresolved'
type Workspace = 'research' | 'management' | 'portfolio'
type LifecycleStatus = 'active' | 'archived'
type ManagementFilter = 'all' | LifecycleStatus
type BatchSource = 'tongdaxin' | 'manual'
type ManagementSourceFilter = 'all' | BatchSource
type Exchange = 'SH' | 'SZ' | 'BJ'
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

type SecurityMasterRecord = {
  symbol: string
  local_code: string
  exchange: Exchange
  name: string | null
  full_name: string | null
  area: string | null
  industry: string | null
  market: string
  list_status: string
  listed_at: string | null
  delisted_at: string | null
  actual_controller: string | null
}

type SecurityMasterResponse = {
  query: string
  effective_date: string
  known_at: string
  master_snapshot_id: string
  observed_at: string
  results: SecurityMasterRecord[]
}

type PortfolioSecurity = {
  symbol: string
  name: string | null
}

type PortfolioPosition = {
  security: PortfolioSecurity
  quantity: string
  average_cost: string
  invested_notional: string
  last_fill_at: string | null
}

type ManualOrder = {
  order_id: string
  security: PortfolioSecurity
  side: 'buy' | 'sell'
  ordered_at: string
  quantity: string
  limit_price: string | null
  recommendation_id: string | null
  selection_candidate_id: string | null
}

type ManualFill = {
  fill_id: string
  order_id: string
  filled_at: string
  quantity: string
  price: string
}

type PortfolioReview = {
  review_id: string
  symbol: string
  reviewed_at: string
  note: string
  recommendation_id: string | null
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

const annotationTagGroups: Array<{
  key: 'position_tags' | 'pattern_tags' | 'price_volume_tags' | 'sector_tags'
  label: string
  options: Array<[string, string]>
}> = [
  { key: 'position_tags', label: '位置', options: [['low', '低位'], ['mid', '中位'], ['high', '高位'], ['support', '支撑附近'], ['resistance', '压力附近'], ['new_high', '阶段新高']] },
  { key: 'pattern_tags', label: '形态', options: [['trend_continuation', '趋势延续'], ['breakout', '突破'], ['pullback', '回踩'], ['consolidation', '横盘整理'], ['reversal', '反转'], ['pattern_break', '形态破坏']] },
  { key: 'price_volume_tags', label: '量价', options: [['price_up_volume_up', '价涨量增'], ['price_up_volume_down', '价涨量缩'], ['price_down_volume_up', '价跌量增'], ['price_down_volume_down', '价跌量缩'], ['volume_breakout', '放量突破'], ['volume_pullback', '缩量回踩']] },
  { key: 'sector_tags', label: '板块环境', options: [['sector_strong', '板块强势'], ['sector_rotation', '板块轮动'], ['sector_leader', '板块龙头'], ['sector_following', '板块跟涨'], ['sector_weak', '板块弱势'], ['stock_independent', '个股独立']] },
]

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

type CalendarInputProps = {
  id: string
  type?: 'date' | 'datetime-local'
  value: string
  onChange: (value: string) => void
  required?: boolean
  disabled?: boolean
  min?: string
  max?: string
  'aria-describedby'?: string
}

function CalendarInput({
  id,
  type = 'date',
  value,
  onChange,
  required = false,
  disabled = false,
  min,
  max,
  'aria-describedby': ariaDescribedBy,
}: CalendarInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const isDateTime = type === 'datetime-local'
  const pickerLabel = isDateTime ? '打开日期和时间选择器' : '打开日期选择器'

  function openPicker() {
    const input = inputRef.current
    if (!input) {
      return
    }
    input.focus()
    try {
      input.showPicker?.()
    } catch {
      return
    }
  }

  function clearValue() {
    onChange('')
    inputRef.current?.focus()
  }

  return (
    <div className="calendar-input">
      <Input
        ref={inputRef}
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        disabled={disabled}
        min={min}
        max={max}
        aria-describedby={ariaDescribedBy}
      />
      {!required && value && (
        <button
          type="button"
          className="calendar-input-clear"
          aria-label={isDateTime ? '清空日期和时间' : '清空日期'}
          disabled={disabled}
          onClick={clearValue}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      )}
      <button
        type="button"
        className="calendar-input-trigger"
        aria-label={pickerLabel}
        disabled={disabled}
        onClick={openPicker}
      >
        <CalendarDays className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  )
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

function PortfolioWorkspace({ apiBase }: { apiBase: string }) {
  const [positions, setPositions] = useState<PortfolioPosition[]>([])
  const [orders, setOrders] = useState<ManualOrder[]>([])
  const [fills, setFills] = useState<ManualFill[]>([])
  const [reviews, setReviews] = useState<PortfolioReview[]>([])
  const [masterQuery, setMasterQuery] = useState('')
  const [masterEffectiveDate, setMasterEffectiveDate] = useState(new Date().toISOString().slice(0, 10))
  const [masterResults, setMasterResults] = useState<SecurityMasterRecord[]>([])
  const [masterBusy, setMasterBusy] = useState(false)
  const [orderCode, setOrderCode] = useState('')
  const [orderExchange, setOrderExchange] = useState<Exchange>('SZ')
  const [orderSide, setOrderSide] = useState<'buy' | 'sell'>('buy')
  const [orderQuantity, setOrderQuantity] = useState('100')
  const [fillOrderId, setFillOrderId] = useState('')
  const [fillQuantity, setFillQuantity] = useState('100')
  const [fillPrice, setFillPrice] = useState('')
  const [reviewSymbol, setReviewSymbol] = useState('')
  const [reviewNote, setReviewNote] = useState('')
  const [ledgerBusy, setLedgerBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      requestJson<PortfolioPosition[]>(`${apiBase}/v1/positions`, { signal: controller.signal }),
      requestJson<ManualOrder[]>(`${apiBase}/v1/manual-orders`, { signal: controller.signal }),
      requestJson<ManualFill[]>(`${apiBase}/v1/manual-fills`, { signal: controller.signal }),
      requestJson<PortfolioReview[]>(`${apiBase}/v1/portfolio-reviews`, { signal: controller.signal }),
    ])
      .then(([nextPositions, nextOrders, nextFills, nextReviews]) => {
        setPositions(nextPositions)
        setOrders(nextOrders)
        setFills(nextFills)
        setReviews(nextReviews)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载持仓台账失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [apiBase])

  async function searchMaster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMasterBusy(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        effective_date: masterEffectiveDate,
        known_at: new Date().toISOString(),
        query: masterQuery,
        limit: '20',
      })
      const response = await requestJson<SecurityMasterResponse>(`${apiBase}/v1/universe/master?${params.toString()}`)
      setMasterResults(response.results)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '搜索证券主数据失败。')
    } finally {
      setMasterBusy(false)
    }
  }

  async function createOrder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLedgerBusy(true)
    setError(null)
    try {
      const order = await requestJson<ManualOrder>(`${apiBase}/v1/manual-orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_id: crypto.randomUUID(),
          local_code: orderCode,
          exchange: orderExchange,
          side: orderSide,
          ordered_at: new Date().toISOString(),
          quantity: orderQuantity,
        }),
      })
      setOrders((current) => [...current, order])
      setOrderCode('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '录入订单失败。')
    } finally {
      setLedgerBusy(false)
    }
  }

  async function createFill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLedgerBusy(true)
    setError(null)
    try {
      const fill = await requestJson<ManualFill>(`${apiBase}/v1/manual-fills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fill_id: crypto.randomUUID(),
          order_id: fillOrderId,
          filled_at: new Date().toISOString(),
          quantity: fillQuantity,
          price: fillPrice,
        }),
      })
      setFills((current) => [...current, fill])
      const nextPositions = await requestJson<PortfolioPosition[]>(`${apiBase}/v1/positions`)
      setPositions(nextPositions)
      setFillOrderId('')
      setFillPrice('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '录入成交失败。')
    } finally {
      setLedgerBusy(false)
    }
  }

  async function createReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLedgerBusy(true)
    setError(null)
    try {
      const review = await requestJson<PortfolioReview>(`${apiBase}/v1/portfolio-reviews`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          review_id: crypto.randomUUID(),
          symbol: reviewSymbol,
          reviewed_at: new Date().toISOString(),
          note: reviewNote,
        }),
      })
      setReviews((current) => [...current, review])
      setReviewSymbol('')
      setReviewNote('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存复盘失败。')
    } finally {
      setLedgerBusy(false)
    }
  }

  return (
    <section className="portfolio-workspace" aria-label="手工持仓台账工作区">
      <div className="workspace-intro">
        <div>
          <p className="eyebrow">手工台账 · 不连接券商</p>
          <h2>持仓与复盘</h2>
          <p>持仓由成交记录推导；订单、成交和复盘记录与模型证据分离。</p>
        </div>
        <Badge variant="secondary">只读汇总</Badge>
      </div>
      {error && <ErrorAlert message={error} onDismiss={() => setError(null)} />}
      <div className="portfolio-entry-grid">
        <form className="portfolio-entry-form" onSubmit={createOrder} aria-busy={ledgerBusy}>
          <h3>录入订单</h3>
          <div className="field">
            <Label htmlFor="order-code">证券代码</Label>
            <Input id="order-code" value={orderCode} onChange={(event) => setOrderCode(event.target.value)} placeholder="六位代码" required />
          </div>
          <div className="field">
            <Label htmlFor="order-exchange">交易所</Label>
            <select id="order-exchange" className="native-select" value={orderExchange} onChange={(event) => setOrderExchange(event.target.value as Exchange)}>
              <option value="SZ">SZ</option><option value="SH">SH</option><option value="BJ">BJ</option>
            </select>
          </div>
          <div className="field">
            <Label htmlFor="order-side">订单方向</Label>
            <select id="order-side" className="native-select" value={orderSide} onChange={(event) => setOrderSide(event.target.value as 'buy' | 'sell')}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
          </div>
          <div className="field">
            <Label htmlFor="order-quantity">数量</Label>
            <Input id="order-quantity" type="number" min="1" value={orderQuantity} onChange={(event) => setOrderQuantity(event.target.value)} required />
          </div>
          <Button type="submit" size="sm" disabled={ledgerBusy}>{ledgerBusy ? '保存中…' : '保存订单'}</Button>
        </form>
        <form className="portfolio-entry-form" onSubmit={createFill} aria-busy={ledgerBusy}>
          <h3>录入成交</h3>
          <div className="field">
            <Label htmlFor="fill-order-id">订单 ID</Label>
            <Input id="fill-order-id" value={fillOrderId} onChange={(event) => setFillOrderId(event.target.value)} required />
          </div>
          <div className="field">
            <Label htmlFor="fill-quantity">成交数量</Label>
            <Input id="fill-quantity" type="number" min="1" value={fillQuantity} onChange={(event) => setFillQuantity(event.target.value)} required />
          </div>
          <div className="field">
            <Label htmlFor="fill-price">成交价</Label>
            <Input id="fill-price" type="number" min="0.0001" step="0.0001" value={fillPrice} onChange={(event) => setFillPrice(event.target.value)} required />
          </div>
          <Button type="submit" size="sm" disabled={ledgerBusy}>{ledgerBusy ? '保存中…' : '保存成交'}</Button>
        </form>
        <form className="portfolio-entry-form" onSubmit={createReview} aria-busy={ledgerBusy}>
          <h3>添加复盘</h3>
          <div className="field">
            <Label htmlFor="review-symbol">证券标识</Label>
            <Input id="review-symbol" value={reviewSymbol} onChange={(event) => setReviewSymbol(event.target.value)} placeholder="代码.交易所" required />
          </div>
          <div className="field">
            <Label htmlFor="review-note">复盘内容</Label>
            <Input id="review-note" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} required />
          </div>
          <Button type="submit" size="sm" disabled={ledgerBusy}>{ledgerBusy ? '保存中…' : '保存复盘'}</Button>
        </form>
      </div>
      <Card className="panel portfolio-master-search">
        <div className="section-heading"><h3>证券主数据搜索</h3><Badge variant="outline">时间点查询</Badge></div>
        <form className="portfolio-search-form" onSubmit={searchMaster}>
          <Input value={masterQuery} onChange={(event) => setMasterQuery(event.target.value)} placeholder="代码、名称、行业或控制人" />
          <CalendarInput
            id="master-effective-date"
            value={masterEffectiveDate}
            onChange={setMasterEffectiveDate}
            required
          />
          <Button type="submit" size="sm" disabled={masterBusy}>{masterBusy ? '搜索中…' : '搜索'}</Button>
        </form>
        {masterResults.length > 0 && (
          <div className="portfolio-master-results">
            {masterResults.map((record) => (
              <div className="portfolio-ledger-row" key={record.symbol}>
                <strong>{record.symbol} · {record.name ?? '未提供名称'}</strong>
                <span>{record.industry ?? '行业未知'} · {record.area ?? '地区未知'} · {record.full_name ?? '全称未知'}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      {loading ? <Skeleton /> : (
        <>
          <div className="portfolio-position-grid">
            {positions.length === 0 ? <p className="empty-copy">暂无持仓。</p> : positions.map((position) => (
              <Card className="portfolio-position-card" key={position.security.symbol}>
                <strong>{position.security.symbol}</strong>
                <span>{position.security.name ?? '未提供名称'}</span>
                <div><span>数量</span><strong>{position.quantity}</strong></div>
                <div><span>平均成本</span><strong>{formatPrice(position.average_cost)}</strong></div>
                <div><span>投入金额</span><strong>{formatPrice(position.invested_notional)}</strong></div>
              </Card>
            ))}
          </div>
          <div className="portfolio-ledger-grid">
            <Card className="panel">
              <div className="section-heading"><h3>订单</h3><Badge variant="outline">{orders.length}</Badge></div>
              {orders.length === 0 ? <p className="empty-copy">暂无订单。</p> : orders.map((order) => (
                <div className="portfolio-ledger-row" key={order.order_id}>
                  <strong>{order.security.symbol} · {order.side === 'buy' ? '买入' : '卖出'}</strong>
                  <span>{order.quantity} 股 · {formatManagementDate(order.ordered_at)}</span>
                </div>
              ))}
            </Card>
            <Card className="panel">
              <div className="section-heading"><h3>成交</h3><Badge variant="outline">{fills.length}</Badge></div>
              {fills.length === 0 ? <p className="empty-copy">暂无成交。</p> : fills.map((fill) => (
                <div className="portfolio-ledger-row" key={fill.fill_id}>
                  <strong>{fill.fill_id}</strong>
                  <span>{fill.quantity} 股 · {formatPrice(fill.price)} · {formatManagementDate(fill.filled_at)}</span>
                </div>
              ))}
            </Card>
          </div>
          <Card className="panel">
            <div className="section-heading"><h3>复盘记录</h3><Badge variant="outline">{reviews.length}</Badge></div>
            {reviews.length === 0 ? <p className="empty-copy">暂无复盘记录。</p> : reviews.map((review) => (
              <div className="portfolio-ledger-row" key={review.review_id}>
                <strong>{review.symbol}</strong>
                <span>{review.note} · {formatManagementDate(review.reviewed_at)}</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </section>
  )
}

function App() {
  const [workspace, setWorkspace] = useState<Workspace>('research')
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
  const [notes, setNotes] = useState<Record<string, string>>({})
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
      setCandidateAnalyses({})
      setAnalysisLoadingId(null)
      setCandidateModelScores({})
      setModelScoreLoadingId(null)
      setRecommendationWorkbench(null)
      setRecommendationLoading(false)
      setMaturityTasks([])
      setLoadingMaturityTasks(false)
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
          <Button
            type="button"
            variant={workspace === 'portfolio' ? 'default' : 'outline'}
            onClick={() => setWorkspace('portfolio')}
          >
            <WalletCards className="h-4 w-4" />
            持仓台账
          </Button>
        </nav>
      </header>

      {workspace === 'management' ? (
        <DataManagementWorkspace
          apiBase={apiBase}
          onFormulaCreated={() => void loadFormulas()}
        />
      ) : workspace === 'portfolio' ? (
        <PortfolioWorkspace apiBase={apiBase} />
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
                    <strong className="batch-date">{batch.selection_date}</strong>
                    <small>
                      {batch.formula_name} · {sourceLabel(batch.source)} · {batch.source_filename ?? '未记录文件名'}
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
        <aside className="decision-guide" aria-labelledby="decision-guide-title">
          <div className="decision-guide-heading">
            <div>
              <p className="eyebrow">先看这里</p>
              <h3 id="decision-guide-title">标记代表什么？</h3>
            </div>
            <span>当天标记，下一交易日前完成；实际成交另记。</span>
          </div>
          <div className="decision-guide-grid ">
            <div>
              <Badge className="decision-badge--buy_candidate">候选</Badge>
              <p>明天优先复核</p>
            </div>
            <div>
              <Badge className="decision-badge--watch">观察</Badge>
              <p>暂时不买，继续跟踪</p>
            </div>
            <div>
              <Badge className="decision-badge--pass">放弃</Badge>
              <p>本次不考虑</p>
            </div>
            <div>
              <Badge className="decision-badge--invalid">无效</Badge>
              <p>代码或数据无效</p>
            </div>
          </div>
          <p className="decision-guide-callout">
            <strong>明天不买、后天才买：</strong>
            今天标“观察”；实际成交时到持仓台账记录时间和价格。
          </p>
        </aside>
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
                    <div className="decision-buttons" aria-label={`${candidate.symbol} 人工评审操作`}>
                      <Button
                        variant="default"
                        size="sm"
                        type="button"
                        disabled={annotationBusy || tradeBusy || candidateView.excluded}
                        onClick={() => openAnnotation(candidateView)}
                      >
                        {candidateView.latest_annotation ? '新增评审' : '开始评审'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        type="button"
                        disabled={annotationBusy || tradeBusy || candidateView.excluded}
                        onClick={() => openCandidateTrade(candidateView)}
                      >
                        记录实际买入
                      </Button>
                      {candidateView.annotations.length > 0 && (
                        <span className="form-hint">已评审 {candidateView.annotations.length} 次</span>
                      )}
                    </div>
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
              {annotationTagGroups.map((group) => (
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
                  </div>
                </fieldset>
              ))}
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

function DataManagementWorkspace({
  apiBase,
  onFormulaCreated,
}: {
  apiBase: string
  onFormulaCreated: () => void
}) {
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
  const [formulaDialogOpen, setFormulaDialogOpen] = useState(false)
  const [formulaBusy, setFormulaBusy] = useState(false)
  const [newFormulaName, setNewFormulaName] = useState('')
  const [newFormulaVersion, setNewFormulaVersion] = useState('')
  const [newFormulaDescription, setNewFormulaDescription] = useState('')
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

function decisionDescription(decision: Decision): string {
  const descriptions: Record<Decision, string> = {
    buy_candidate: '列入下一交易日优先复核，不代表已经买入。',
    watch: '暂时不买，后续实际成交请记录到持仓台账。',
    pass: '本次不考虑买入。',
    invalid: '数据或代码无效，不作为有效研究样本。',
  }
  return descriptions[decision]
}

export default App
