import { expect, test, type Page, type Route } from '@playwright/test'

function fulfillJson(route: Route, payload: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(payload),
  })
}

function createReadiness() {
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

async function installMocks(page: Page) {
  let readinessRequestCount = 0
  const unexpectedRequests: string[] = []
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
    if (path === '/api/v1/ml/readiness' && method === 'GET') {
      readinessRequestCount += 1
      await fulfillJson(route, createReadiness())
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
      await fulfillJson(route, { items: [], page: 1, page_size: 50, total: 0, has_next: false })
      return
    }
    if (path === '/api/v1/workbench/summary' && method === 'GET') {
      await fulfillJson(route, {
        as_of: '2026-08-14T16:00:00+08:00',
        signal_count: 0,
        unreviewed_count: 0,
        watching_count: 0,
        preparing_count: 0,
        reviewed_count: 0,
        confirmed_buy_count: 0,
      })
      return
    }
    if (path === '/api/v1/workbench/daily-signals/page' || path === '/api/v1/workbench/watchlist/page') {
      await fulfillJson(route, { items: [], page: 1, page_size: 50, total: 0, has_next: false })
      return
    }
    unexpectedRequests.push(`${method} ${path}`)
    await fulfillJson(route, { error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} } })
  })
  return { getReadinessRequestCount: () => readinessRequestCount, unexpectedRequests }
}

test('shows blocked ML readiness and supports deterministic refresh', async ({ page }) => {
  const mocks = await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '导入数据' }).click()

  const panel = page.locator('.ml-readiness-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('什么时候可以运行机器学习？')
  await expect(panel).toContainText('4 / 100')
  await expect(panel).toContainText('96 条')
  await expect(panel).toContainText('000300.SH')
  await expect(panel).toContainText('当前尚不能运行完整训练流水线')
  const initialRequestCount = mocks.getReadinessRequestCount()

  await panel.getByRole('button', { name: '刷新机器学习就绪状态' }).click()
  await expect.poll(mocks.getReadinessRequestCount).toBe(initialRequestCount + 1)
  expect(mocks.unexpectedRequests).toEqual([])
  await expect(panel).toHaveScreenshot('ml-readiness-blocked.png')
})
