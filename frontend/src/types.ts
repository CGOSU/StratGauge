import type {
  Exchange,
  ManualFill,
  ManualOrder,
  MaturityStatus,
  MaturityStatusCounts,
  PortfolioPosition,
} from '@/api/contracts'

export type { MaturityStatus } from '@/api/contracts'


export type HealthState = 'checking' | 'online' | 'offline'
export type MaturityStatusFilter = 'all' | MaturityStatus
export type Decision = 'buy_candidate' | 'watch' | 'pass' | 'invalid'
export type AnnotationOutlook = 'bullish' | 'neutral' | 'bearish'
export type AnnotationDecision = 'discard' | 'watch' | 'prepare_buy'
export type ReviewState = 'unreviewed' | 'discarded' | 'watching' | 'preparing'
export type AnnotationTag = string
export type ReusableTagCategory = 'position' | 'pattern' | 'price_volume' | 'sector'
export type ReusableTag = {
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
export type ReusableTagSnapshot = {
  tag_id: string
  category: ReusableTagCategory
  label: string
}
export type OutcomeStatus = Exclude<MaturityStatus, 'pending'>
export type Workspace = 'research' | 'management' | 'portfolio'
export type AppRoute = 'workbench' | 'signals' | 'watchlist' | 'positions' | 'trades' | 'strategies' | 'analytics' | 'import' | 'settings'
export type LifecycleStatus = 'active' | 'archived'
export type ManagementFilter = 'all' | LifecycleStatus
export type BatchSource = 'tongdaxin' | 'manual'
export type ManagementSourceFilter = 'all' | BatchSource

export type Formula = {
  formula_id: string
  name: string
  version: string | null
  description: string | null
  created_at: string
}

export type Batch = {
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

export type Outcome = {
  status: OutcomeStatus
  evaluated_at: string
  net_return: string | null
  reason: string
  tracking_policy_id: string
}

export type PerformancePoint = {
  horizon_sessions: number
  session_date: string | null
  close_price: string | null
  return_from_anchor: string | null
  maximum_rise: string | null
  maximum_drawdown: string | null
  target_achieved: boolean | null
}

export type Performance = {
  status: 'resolved' | 'no_entry' | 'unresolved'
  evaluated_at: string
  performance_policy_id: string
  anchor_session: string | null
  anchor_price: string | null
  target_return: string | null
  points: PerformancePoint[]
  reason: string
}

export type SelectionLabel = {
  candidate_id: string
  decision: Decision
  labeled_at: string
  note: string | null
}

export type Annotation = {
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

export type Exclusion = {
  exclusion_id: string
  candidate_id: string
  excluded_at: string
  recorded_at: string
  reason: string
}

export type Candidate = {
  candidate_id: string
  batch_id: string
  local_code: string
  symbol: string
  exchange: Exchange
  name: string | null
  source_fields: Record<string, string>
}

export type ManualCandidateDraft = {
  id: number
  local_code: string
  exchange: Exchange
  name: string
  note: string
}

export type CandidateView = {
  candidate: Candidate
  labels: SelectionLabel[]
  annotations: Annotation[]
  exclusions: Exclusion[]
  latest_annotation: Annotation | null
  latest_exclusion: Exclusion | null
  review_state: ReviewState
  excluded: boolean
  outcomes: Outcome[]
  performances: Performance[]
}

export type BatchView = {
  batch: Batch
  candidates: CandidateView[]
}

export type BatchPage = {
  items: Batch[]
  page: number
  page_size: number
  total: number
  has_next: boolean
}

export type MaturityStatusItem = {
  batch: Batch
  candidate: Candidate
  status: MaturityStatus
  outcome: Outcome | null
  latest_exclusion: Exclusion | null
  excluded: boolean
  active: boolean
}

export type MaturityStatusPage = {
  items: MaturityStatusItem[]
  page: number
  page_size: number
  total: number
  has_next: boolean
  status_counts: MaturityStatusCounts
}

export type StatsSummary = {
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

export type EntryPriceStats = {
  sample_count: number
  minimum: string | null
  lower_quartile: string | null
  median: string | null
  upper_quartile: string | null
  maximum: string | null
  average: string | null
}

export type CandidateAnalysis = {
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

export type ModelScore = {
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

export type ModelScoreFailure = {
  model_version_id: string
  reason: string
}

export type CandidateModelScores = {
  batch_id: string
  candidate_id: string
  symbol: string
  scores: ModelScore[]
  failures: ModelScoreFailure[]
}

export type WorkbenchModelScore = {
  score_id: string
  model_version_id: string
  model_lifecycle: 'shadow' | 'active'
  uncalibrated_model_score: string
  calibrated_model_score: string | null
  calibrated: boolean
  used_for_decision: false
}

export type RecommendationCandidate = Candidate & {
  quality_state: string
  historical_stats: StatsSummary | null
  model_score: WorkbenchModelScore | null
  manual_decision: Decision | null
  state: 'reviewable' | 'blocked'
  blocking_reasons: string[]
  authoritative: false
}

export type RecommendationWorkbench = {
  batch: Batch
  generated_at: string
  candidates: RecommendationCandidate[]
  automatic_action: false
}

export type BatchStats = {
  batch_id: string
  candidate_count: number
  pending: number
  latest_outcome_count: number
  summary: StatsSummary
}

export type LifecycleEvent = {
  event_id: string
  entity_type: 'formula' | 'batch'
  entity_id: string
  status: LifecycleStatus
  changed_at: string
  reason: string | null
}

export type ManagedFormula = {
  formula: Formula
  active: boolean
  lifecycle: LifecycleEvent[]
}

export type ManagedReusableTag = ReusableTag

export type ManagedBatch = {
  batch: Batch
  active: boolean
  label_count: number
  outcome_count: number
  lifecycle: LifecycleEvent[]
}

export type ManagedBatchDetail = {
  view: BatchView
  active: boolean
  lifecycle: LifecycleEvent[]
}

export type ManagedBatchPage = {
  items: ManagedBatch[]
  page: number
  page_size: number
  total: number
  has_next: boolean
}

export type ManagementStats = {
  batch_count: number
  candidate_count: number
  pending: number
  sample_count: number
  tracking_policy_ids: string[]
  summary: StatsSummary
}

export type WorkbenchMarketEvidence = {
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

export type WorkbenchObservationPerformance = {
  status: 'available' | 'unavailable' | 'incomplete'
  started_at: string | null
  start_snapshot_id: string | null
  start_session_date: string | null
  start_available_at: string | null
  start_close: string | null
  change_percent: string | null
  reason: string | null
}

export type WorkbenchSignalPage = {
  items: WorkbenchSignal[]
  page: number
  page_size: number
  total: number
  has_next: boolean
}

export type WorkbenchSignal = {
  batch: Batch
  candidate: Candidate
  labels: SelectionLabel[]
  annotations: Annotation[]
  latest_annotation: Annotation | null
  exclusions: Exclusion[]
  latest_exclusion: Exclusion | null
  review_state: ReviewState
  excluded: boolean
  outcomes: Outcome[]
  performances: Performance[]
  market: WorkbenchMarketEvidence
  observation_performance: WorkbenchObservationPerformance | null
  confirmed_buy: boolean
  confirmed_buy_fill_ids: string[]
}

export type WorkbenchSummary = {
  as_of: string
  signal_count: number
  excluded_count: number
  unreviewed_count: number
  reviewed_count: number
  watching_count: number
  preparing_count: number
  confirmed_buy_count: number
}

export type WorkbenchTimelineEvent = {
  event_id: string
  event_type: 'signal' | 'annotation' | 'exclusion' | 'order' | 'fill' | 'review' | 'outcome' | 'performance'
  occurred_at: string
  source_id: string
  title: string
  detail: string
}

export type WorkbenchStockDetail = {
  symbol: string
  name: string | null
  as_of: string
  market: WorkbenchMarketEvidence
  signals: WorkbenchSignal[]
  timeline: WorkbenchTimelineEvent[]
}

export type WorkbenchPositionView = {
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

export type WorkbenchTradeView = {
  order: ManualOrder
  fills: ManualFill[]
  source: 'signal' | 'manual_discovery' | 'mixed'
}

export type WorkbenchHorizonMetric = {
  horizon_sessions: number
  sample_count: number
  average_return: string | null
  average_maximum_rise: string | null
  average_maximum_drawdown: string | null
  target_3_percent_rate: string | null
  target_5_percent_rate: string | null
}

export type WorkbenchCohortMetrics = {
  cohort: 'all_signals' | 'human_bullish' | 'confirmed_trades'
  sample_count: number
  resolved_count: number
  excluded_count: number
  horizons: WorkbenchHorizonMetric[]
}

export type WorkbenchStrategyView = {
  formula_id: string | null
  formula_name: string
  formula_version: string | null
  signal_count: number
  cohorts: WorkbenchCohortMetrics[]
}

export type WorkbenchAnalyticsView = {
  as_of: string
  cohorts: WorkbenchCohortMetrics[]
}

export type WorkbenchRoute = Extract<AppRoute, 'workbench' | 'signals' | 'watchlist' | 'positions' | 'trades' | 'strategies' | 'analytics'>
export type WorkbenchReviewFilter = 'all' | 'unreviewed' | 'bullish' | 'watching' | 'preparing' | 'discarded' | 'bought'
export type StockDrawerTab = 'market' | 'judgment' | 'signal' | 'trade' | 'analysis'
