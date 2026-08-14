export type HealthResponse = {
  status: 'ok'
  service: string
  version: string
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
