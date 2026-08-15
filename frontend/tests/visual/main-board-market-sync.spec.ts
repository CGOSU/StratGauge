import { expect, test, type Page, type Route } from '@playwright/test'

type TaskStatus = 'queued' | 'completed' | 'partial' | 'cancelled'
type StageStatus = 'approved' | 'rejected'

type MockTask = {
  job_id: string
  kind: string
  status: TaskStatus
  request: {
    session_date: string
    minimum_coverage: string
    lookback_days: number
  }
  attempt_count: number
  max_retries: number
  timeout_seconds: number
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  result: MockMarketSyncResult | null
  error: string | null
}

type MockMarketSyncResult = {
  requested_through: string
  universe_snapshot_id: string
  security_count: number
  research_ready: boolean
  sessions: Array<{
    session_date: string
    market_snapshot_id: string
    market_status: StageStatus
    adjustment_factor_snapshot_id: string | null
    adjustment_factor_status: StageStatus | null
    skipped: boolean
    market_diagnostic: MockStageDiagnostic
    adjustment_factor_diagnostic: MockStageDiagnostic | null
  }>
}

type MockStageDiagnostic = {
  phase: 'market_daily' | 'adjustment_factor'
  phase_label: string
  session_date: string
  snapshot_id: string
  status: StageStatus
  quality_run_id: string | null
  expected_securities: number
  actual_securities: number
  coverage: string
  minimum_coverage: string
  issues: Array<{
    severity: string
    code: string
    label: string
    message: string
    symbol: string | null
    session_date: string | null
    details: Record<string, string>
    action: string
  }>
  summary: string
  action: string
}

type MockScenario = {
  terminalStatus?: Exclude<TaskStatus, 'queued' | 'cancelled'>
}

type MockState = {
  taskGetCount: () => number
  runCount: () => number
  cancelCount: () => number
  unexpectedRequests: () => string[]
}

const sessionDate = '2026-08-14'
const taskId = 'job-market-sync-20260814'
const fixedCreatedAt = '2026-08-14T08:00:00+08:00'
const fixedUpdatedAt = '2026-08-14T16:00:00+08:00'
const taskPath = `/api/v1/market-sync/tasks/${taskId}`

function fulfillJson(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(payload),
  })
}

function createDiagnostic(
  phase: MockStageDiagnostic['phase'],
  status: StageStatus,
): MockStageDiagnostic {
  const isMarketDaily = phase === 'market_daily'
  const phaseLabel = isMarketDaily ? '日线行情' : '复权因子'
  const isApproved = status === 'approved'
  return {
    phase,
    phase_label: phaseLabel,
    session_date: sessionDate,
    snapshot_id: isApproved
      ? `snapshot-${isMarketDaily ? 'daily' : 'factor'}-20260814`
      : `snapshot-${isMarketDaily ? 'daily' : 'factor'}-rejected`,
    status,
    quality_run_id: isApproved ? `quality-${isMarketDaily ? 'daily' : 'factor'}-20260814` : `quality-${phase}-rejected`,
    expected_securities: 3193,
    actual_securities: isApproved ? 3193 : 2860,
    coverage: isApproved ? '1.0000' : '0.8954',
    minimum_coverage: '0.9000',
    issues: isApproved
      ? []
      : [{
          severity: 'error',
          code: 'COVERAGE_BELOW_THRESHOLD',
          label: '覆盖率不足',
          message: '复权因子未覆盖全部主板证券。',
          symbol: null,
          session_date: sessionDate,
          details: { actual: '2860', expected: '3193' },
          action: '补齐缺失证券后重新执行同步。',
        }],
    summary: isApproved ? `${phaseLabel}已通过质量门。` : `${phaseLabel}未通过质量门。`,
    action: isApproved ? '可继续使用本阶段证据。' : '修复覆盖率后重新同步。',
  }
}

function createResult(researchReady: boolean): MockMarketSyncResult {
  const factorStatus: StageStatus = researchReady ? 'approved' : 'rejected'
  const marketDiagnostic = createDiagnostic('market_daily', 'approved')
  const factorDiagnostic = createDiagnostic('adjustment_factor', factorStatus)
  return {
    requested_through: sessionDate,
    universe_snapshot_id: 'universe-main-board-20260814',
    security_count: 3193,
    research_ready: researchReady,
    sessions: [{
      session_date: sessionDate,
      market_snapshot_id: marketDiagnostic.snapshot_id,
      market_status: 'approved',
      adjustment_factor_snapshot_id: factorDiagnostic.snapshot_id,
      adjustment_factor_status: factorStatus,
      skipped: false,
      market_diagnostic: marketDiagnostic,
      adjustment_factor_diagnostic: factorDiagnostic,
    }],
  }
}

function createTask(status: TaskStatus, result: MockMarketSyncResult | null = null): MockTask {
  const isTerminal = status !== 'queued'
  return {
    job_id: taskId,
    kind: 'market_sync.main_board',
    status,
    request: {
      session_date: sessionDate,
      minimum_coverage: '0.90',
      lookback_days: 10,
    },
    attempt_count: isTerminal ? 1 : 0,
    max_retries: 2,
    timeout_seconds: 900,
    created_at: fixedCreatedAt,
    updated_at: fixedUpdatedAt,
    started_at: isTerminal ? fixedUpdatedAt : null,
    finished_at: isTerminal ? fixedUpdatedAt : null,
    result,
    error: null,
  }
}

function createWorkbenchSummary() {
  return {
    as_of: '2026-08-14T16:00:00+08:00',
    signal_count: 0,
    unreviewed_count: 0,
    watching_count: 0,
    preparing_count: 0,
    reviewed_count: 0,
    confirmed_buy_count: 0,
  }
}

function createEmptyPage() {
  return { items: [], page: 1, page_size: 50, total: 0, has_next: false }
}

function createMlReadiness() {
  return {
    report_version: 'selection-ml-readiness-v1',
    purpose: 'candidate_success',
    stage: 'collecting_labels',
    minimum_labeled_samples: 100,
    source_batch_count: 2,
    source_candidate_count: 4,
    accepted_label_count: 4,
    label_shortfall: 96,
    target_counts: { success: 2, not_success: 2 },
    outcome_counts: { flat: 0, loss: 2, no_entry: 0, pending: 0, unresolved: 0, win: 2 },
    tracking_policy_ids: ['tracking-v1'],
    outcome_policy_ids: ['outcome-v1'],
    feature_snapshot_count: 0,
    invalid_feature_snapshot_count: 0,
    benchmark_snapshot_count: 0,
    adjustment_factor_snapshot_count: 1,
    invalid_market_manifest_count: 0,
    registered_model_count: 0,
    calibrated_model_count: 0,
    shadow_model_count: 0,
    invalid_model_evidence_count: 0,
    label_ready: false,
    evidence_ready: false,
    training_ready: false,
    blockers: ['已成熟候选结果不足：当前 4 条，还需要 96 条。', '缺少已批准的 000300.SH 独立 index_daily 基准证据。'],
    next_steps: ['继续同步并运行成熟任务，直到 win/loss/flat 的已成熟结果达到 100 条。'],
  }
}

async function installApiMocks(page: Page, scenario: MockScenario): Promise<MockState> {
  let currentTask: MockTask | null = null
  let taskGetCount = 0
  let runCount = 0
  let cancelCount = 0
  const unexpectedRequests: string[] = []
  const terminalTask = scenario.terminalStatus
    ? createTask(scenario.terminalStatus, createResult(scenario.terminalStatus === 'completed'))
    : null

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname

    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/healthz' && method === 'GET') {
      await fulfillJson(route, { status: 'ok', service: 'stratgauge', version: 'test' })
      return
    }
    if (path === '/api/v1/annotation-tags' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/formulas' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/selection-batches/page' && method === 'GET') {
      await fulfillJson(route, createEmptyPage())
      return
    }
    if (path === '/api/v1/ml/readiness' && method === 'GET') {
      await fulfillJson(route, createMlReadiness())
      return
    }
    if (path === '/api/v1/workbench/summary' && method === 'GET') {
      await fulfillJson(route, createWorkbenchSummary())
      return
    }
    if (
      (path === '/api/v1/workbench/daily-signals/page' || path === '/api/v1/workbench/watchlist/page')
      && method === 'GET'
    ) {
      await fulfillJson(route, createEmptyPage())
      return
    }
    if (path === '/api/v1/market-sync/tasks' && method === 'POST') {
      currentTask = createTask('queued')
      await fulfillJson(route, currentTask)
      if (terminalTask) {
        currentTask = terminalTask
      }
      return
    }
    if (path === taskPath && method === 'GET') {
      taskGetCount += 1
      await fulfillJson(route, currentTask ?? createTask('queued'))
      return
    }
    if (path === `${taskPath}/cancel` && method === 'POST') {
      cancelCount += 1
      currentTask = createTask('cancelled')
      await fulfillJson(route, currentTask)
      return
    }
    if (path === '/api/v1/market-sync/tasks/run' && method === 'POST') {
      runCount += 1
      await fulfillJson(route, { jobs: currentTask ? [currentTask] : [] })
      return
    }

    unexpectedRequests.push(`${method} ${path}`)
    await fulfillJson(route, {
      error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} },
    }, 404)
  })

  return {
    taskGetCount: () => taskGetCount,
    runCount: () => runCount,
    cancelCount: () => cancelCount,
    unexpectedRequests: () => unexpectedRequests,
  }
}

async function openImportPage(page: Page): Promise<ReturnType<Page['locator']>> {
  await page.goto('/')
  await page.getByRole('button', { name: '导入数据' }).click()
  const panel = page.locator('.market-sync-panel')
  await expect(panel).toBeVisible()
  return panel
}

async function submitSyncTask(panel: ReturnType<Page['locator']>) {
  await panel.locator('#market-sync-date').fill(sessionDate)
  await panel.getByRole('button', { name: '加入主板行情队列' }).click()
  await expect(panel.getByRole('region', { name: '行情同步任务状态' })).toBeVisible()
}

test.describe('主板行情同步面板', () => {
  test('固定排队状态展示并保留 queued-only 操作', async ({ page }) => {
    const mocks = await installApiMocks(page, {})
    const panel = await openImportPage(page)
    await submitSyncTask(panel)

    await expect(panel.getByText('排队中', { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: '取消排队' })).toBeVisible()
    await expect(panel.getByRole('button', { name: '立即执行一次' })).toBeVisible()
    await expect(panel.getByText('POST /v1/market-sync/main-board')).toBeVisible()
    await expect(panel).toHaveScreenshot('main-board-market-sync-queued-desktop.png')

    await panel.getByRole('button', { name: '立即执行一次' }).click()
    await expect(panel.getByText('已显式执行一次 runner：任务当前状态为排队中。')).toBeVisible()
    expect(mocks.runCount()).toBe(1)
    expect(mocks.unexpectedRequests()).toEqual([])
  })

  test('停止和恢复自动刷新、手动刷新以及取消均使用持久任务 API', async ({ page }) => {
    const mocks = await installApiMocks(page, {})
    const panel = await openImportPage(page)
    await submitSyncTask(panel)
    await expect(panel.getByText('排队中', { exact: true })).toBeVisible()
    await expect.poll(mocks.taskGetCount).toBeGreaterThan(0)

    await panel.getByRole('button', { name: '停止自动刷新' }).click()
    await expect(panel.getByRole('button', { name: '恢复自动刷新' })).toBeVisible()
    await expect(panel.getByText('自动刷新已停止', { exact: true })).toBeVisible()

    const requestsBeforeManualRefresh = mocks.taskGetCount()
    await panel.getByRole('button', { name: '手动刷新' }).click()
    await expect.poll(mocks.taskGetCount).toBeGreaterThan(requestsBeforeManualRefresh)

    await panel.getByRole('button', { name: '停止自动刷新' }).click()
    await panel.getByRole('button', { name: '取消排队' }).click()
    await expect(panel.getByText('任务已取消；只有 queued 状态允许取消。')).toBeVisible()
    await expect(panel.getByRole('button', { name: '取消排队' })).not.toBeVisible()
    expect(mocks.cancelCount()).toBe(1)
    expect(mocks.unexpectedRequests()).toEqual([])
  })

  test('完成状态展示研究就绪证据且不提供 queued-only 取消', async ({ page }) => {
    const mocks = await installApiMocks(page, { terminalStatus: 'completed' })
    const panel = await openImportPage(page)
    await submitSyncTask(panel)

    await expect(panel.getByText('研究数据已准备就绪', { exact: true })).toBeVisible()
    await expect(panel.getByText('已完成', { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: '取消排队' })).not.toBeVisible()
    await expect(panel.locator('.market-sync-results')).toHaveScreenshot('main-board-market-sync-completed-results-desktop.png')

    expect(mocks.unexpectedRequests()).toEqual([])
  })

  test('窄屏部分完成状态明确阻断研究使用', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const mocks = await installApiMocks(page, { terminalStatus: 'partial' })
    const panel = await openImportPage(page)
    await submitSyncTask(panel)

    await expect(panel.getByText('研究数据未准备就绪', { exact: true })).toBeVisible()
    await expect(panel.getByText('复权因子未通过质量门。', { exact: true })).toBeVisible()
    await expect(panel.getByText('未达到研究条件', { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: '取消排队' })).not.toBeVisible()
    await expect(panel.locator('.market-sync-results')).toHaveScreenshot('main-board-market-sync-partial-results-mobile.png')

    expect(mocks.unexpectedRequests()).toEqual([])
  })
})
