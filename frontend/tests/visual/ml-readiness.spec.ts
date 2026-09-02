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

function createMaturityPage(url: URL) {
  const statuses = ['pending', 'win', 'loss', 'flat', 'no_entry', 'unresolved', 'win', 'loss', 'flat']
  const allItems = statuses.map((status, index) => {
    const candidateId = `candidate-${index + 1}`
    const archived = index === 7
    const excluded = index === 7
    return {
      batch: {
        batch_id: `batch-${index + 1}`,
        source: 'tongdaxin',
        source_filename: `selection-${index + 1}.txt`,
        formula_id: 'formula-test',
        selection_date: `2026-01-${String(index + 5).padStart(2, '0')}`,
        exported_at: '2026-01-05T15:05:00+08:00',
        imported_at: '2026-01-05T15:10:00+08:00',
        formula_name: '趋势公式',
        formula_version: 'v1',
        formula_sha256: null,
        source_file_sha256: 'a'.repeat(64),
        candidate_count: 1,
      },
      candidate: {
        candidate_id: candidateId,
        batch_id: `batch-${index + 1}`,
        local_code: String(index + 1).padStart(6, '0'),
        symbol: `${String(index + 1).padStart(6, '0')}.SZ`,
        exchange: 'SZ',
        name: `测试候选 ${index + 1}`,
        source_fields: {},
      },
      status,
      outcome: status === 'pending'
        ? null
        : {
            status,
            evaluated_at: '2026-01-15T17:00:00+00:00',
            net_return: status === 'no_entry' || status === 'unresolved' ? null : index % 2 === 0 ? '0.09' : '-0.03',
            reason: status === 'no_entry' ? '固定窗口内没有满足入场条件' : status === 'unresolved' ? '未来行情证据不足' : 'fixed_horizon',
            tracking_policy_id: 'tracking-v1',
          },
      latest_exclusion: excluded
        ? {
            exclusion_id: 'exclusion-test',
            candidate_id: candidateId,
            excluded_at: '2026-01-06T09:00:00+08:00',
            recorded_at: '2026-01-06T09:01:00+08:00',
            reason: '测试排除',
          }
        : null,
      excluded,
      active: !archived,
    }
  })
  const statusCounts = {
    pending: 0,
    win: 0,
    loss: 0,
    flat: 0,
    no_entry: 0,
    unresolved: 0,
  }
  for (const item of allItems) {
    statusCounts[item.status as keyof typeof statusCounts] += 1
  }
  const status = url.searchParams.get('status')
  const filteredItems = status ? allItems.filter((item) => item.status === status) : allItems
  const page = Number(url.searchParams.get('page') ?? '1')
  const pageSize = Number(url.searchParams.get('page_size') ?? '8')
  const start = (page - 1) * pageSize
  return {
    items: filteredItems.slice(start, start + pageSize),
    page,
    page_size: pageSize,
    total: filteredItems.length,
    has_next: start + pageSize < filteredItems.length,
    status_counts: statusCounts,
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
    if (path === '/api/v1/selection-maturity/page' && method === 'GET') {
      await fulfillJson(route, createMaturityPage(new URL(request.url())))
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

  const desktopLayout = await page.locator('.research-layout').evaluate((element) => {
    const cards = Array.from(element.children).map((child) => child.getBoundingClientRect())
    return {
      columns: getComputedStyle(element).gridTemplateColumns.split(/\s+/).length,
      widthDelta: Math.abs(cards[0].width - cards[1].width),
      noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  expect(desktopLayout.columns).toBe(2)
  expect(desktopLayout.widthDelta).toBeLessThan(4)
  expect(desktopLayout.noHorizontalOverflow).toBe(true)

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

test('filters and paginates the read-only maturity status list', async ({ page }) => {
  const mocks = await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '导入数据' }).click()

  const panel = page.locator('.maturity-status-panel')
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: '读取成熟结果' }).click()
  await expect(panel).toContainText('成熟结果列表')
  await expect(panel).toContainText('待成熟')
  await expect(panel).toContainText('胜')
  await expect(panel).toContainText('已归档')
  await expect(panel).toContainText('已排除')
  await expect(panel.locator('.maturity-status-row')).toHaveCount(8)

  const filter = panel.getByRole('combobox', { name: '查看状态' })
  await filter.selectOption('pending')
  await expect(panel.locator('.maturity-status-row')).toHaveCount(1)
  await expect(panel).toContainText('尚未生成客观结果')
  await expect(panel.locator('.pagination-controls')).toHaveCount(0)

  await filter.selectOption('win')
  await expect(panel.locator('.maturity-status-row')).toHaveCount(2)
  await expect(panel).toContainText('跟踪策略 tracking-v1')

  await filter.selectOption('all')
  await expect(panel.getByRole('button', { name: '下一页' })).toBeEnabled()
  const pagination = panel.locator('.pagination-controls')
  await expect(pagination).toHaveCSS('row-gap', '8px')
  await expect(pagination).toHaveCSS('column-gap', '12px')
  await panel.getByRole('button', { name: '下一页' }).click()
  await expect(panel.locator('.maturity-status-row')).toHaveCount(1)
  await expect(panel).toContainText('000009.SZ')

  await filter.selectOption('pending')
  await expect(panel.locator('.maturity-status-row')).toHaveCount(1)
  await expect(panel).toContainText('尚未生成客观结果')
  expect(mocks.unexpectedRequests).toEqual([])
})

test('keeps dialog action buttons separated on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '导入数据' }).click()
  await page.getByRole('button', { name: '手动添加候选', exact: true }).click()

  const footer = page.locator('.dialog-footer')
  await expect(footer).toBeVisible()
  await expect(footer).toHaveCSS('row-gap', '8px')
  const footerLayout = await footer.evaluate((element) => ({
    noHorizontalOverflow: element.scrollWidth <= element.clientWidth,
    childCount: element.children.length,
  }))
  expect(footerLayout.childCount).toBe(2)
  expect(footerLayout.noHorizontalOverflow).toBe(true)
})

test('stacks the import workflow without horizontal overflow on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '导入数据' }).click()

  const narrowLayout = await page.locator('.research-layout').evaluate((element) => {
    const cards = Array.from(element.children).map((child) => child.getBoundingClientRect())
    return {
      columns: getComputedStyle(element).gridTemplateColumns.split(/\s+/).length,
      firstCardTop: cards[0].top,
      secondCardTop: cards[1].top,
      noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  expect(narrowLayout.columns).toBe(1)
  expect(narrowLayout.secondCardTop).toBeGreaterThan(narrowLayout.firstCardTop)
  expect(narrowLayout.noHorizontalOverflow).toBe(true)
  const maturityPanel = page.locator('.maturity-status-panel')
  await expect(maturityPanel).toBeVisible()
  await maturityPanel.getByRole('button', { name: '读取成熟结果' }).click()
  await expect(maturityPanel.locator('.maturity-status-row')).toHaveCount(8)
  const maturityLayout = await maturityPanel.locator('.maturity-status-row').first().evaluate((element) => ({
    columns: getComputedStyle(element).gridTemplateColumns.split(/\s+/).length,
    noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  expect(maturityLayout.columns).toBe(1)
  expect(maturityLayout.noHorizontalOverflow).toBe(true)
})
