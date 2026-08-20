export type HealthResponse = {
  status: 'ok'
  service: string
  version: string
}

export type MaturityStatus =
  | 'pending'
  | 'win'
  | 'loss'
  | 'flat'
  | 'no_entry'
  | 'unresolved'

export type MaturityStatusCounts = Record<MaturityStatus, number>

export type MlReadinessStage =
  | 'collecting_labels'
  | 'awaiting_feature_evidence'
  | 'ready_for_training'
  | 'model_registered'
  | 'shadow_ready'

export type MlReadiness = {
  report_version: 'selection-ml-readiness-v1'
  purpose: 'candidate_success'
  stage: MlReadinessStage
  minimum_labeled_samples: number
  source_batch_count: number
  source_candidate_count: number
  accepted_label_count: number
  label_shortfall: number
  target_counts: Record<string, number>
  outcome_counts: Record<string, number>
  tracking_policy_ids: string[]
  outcome_policy_ids: string[]
  feature_snapshot_count: number
  invalid_feature_snapshot_count: number
  benchmark_snapshot_count: number
  adjustment_factor_snapshot_count: number
  invalid_market_manifest_count: number
  registered_model_count: number
  calibrated_model_count: number
  shadow_model_count: number
  invalid_model_evidence_count: number
  label_ready: boolean
  evidence_ready: boolean
  training_ready: boolean
  blockers: string[]
  next_steps: string[]
}

export type ErrorResponse = {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
  }
}

export type MarketSyncStatus = 'approved' | 'rejected' | 'pending' | 'superseded'

export type MarketSyncQualityIssue = {
  severity: string
  code: string
  label: string
  message: string
  symbol: string | null
  session_date: string | null
  details: Record<string, string>
  action: string
}

export type MarketSyncStageDiagnostic = {
  phase: 'market_daily' | 'adjustment_factor'
  phase_label: string
  session_date: string
  snapshot_id: string
  status: MarketSyncStatus
  quality_run_id: string | null
  expected_securities: number
  actual_securities: number
  coverage: string | number
  minimum_coverage: string | number
  issues: MarketSyncQualityIssue[]
  summary: string
  action: string
}

export type MarketSyncSession = {
  session_date: string
  market_snapshot_id: string
  market_status: MarketSyncStatus
  adjustment_factor_snapshot_id: string | null
  adjustment_factor_status: MarketSyncStatus | null
  skipped: boolean
  market_diagnostic: MarketSyncStageDiagnostic
  adjustment_factor_diagnostic: MarketSyncStageDiagnostic | null
}

export type MarketSyncResponse = {
  requested_through: string
  universe_snapshot_id: string
  security_count: number
  research_ready: boolean
  sessions: MarketSyncSession[]
}

export type Exchange = 'SH' | 'SZ' | 'BJ'

export type SecurityMasterRecord = {
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

export type SecurityMasterResponse = {
  query: string
  effective_date: string
  known_at: string
  master_snapshot_id: string
  observed_at: string
  results: SecurityMasterRecord[]
}

export type PortfolioSecurity = {
  symbol: string
  name: string | null
}

export type PortfolioPosition = {
  security: PortfolioSecurity
  quantity: string
  average_cost: string
  invested_notional: string
  last_fill_at: string | null
}

export type ManualSellReason =
  | 'target_reached'
  | 'stop_loss_broken'
  | 'trend_weakened'
  | 'sector_weakened'
  | 'market_risk'
  | 'strategy_invalidated'
  | 'other'

export const manualSellReasonLabels: Record<ManualSellReason, string> = {
  target_reached: '达到目标',
  stop_loss_broken: '跌破止损',
  trend_weakened: '走势转弱',
  sector_weakened: '板块转弱',
  market_risk: '大盘风险',
  strategy_invalidated: '策略失效',
  other: '其他',
}

export type ManualOrder = {
  order_id: string
  security: PortfolioSecurity
  side: 'buy' | 'sell'
  ordered_at: string
  quantity: string
  limit_price: string | null
  strategy_version_id?: string | null
  recommendation_id: string | null
  selection_candidate_id: string | null
  sell_reason?: ManualSellReason | null
  note?: string | null
}

export type ManualFill = {
  fill_id: string
  order_id: string
  filled_at: string
  quantity: string
  price: string
  commission?: string
  stamp_duty?: string
  note?: string | null
}

export type PortfolioReview = {
  review_id: string
  symbol: string
  reviewed_at: string
  note: string
  recommendation_id: string | null
}
