import { expect, test, type Page, type Route } from '@playwright/test'

const market = {
  status: 'available',
  snapshot_id: 'snapshot-manual',
  provider: 'offline-fixture',
  session_date: '2026-08-28',
  available_at: '2026-08-28T16:00:00+08:00',
  close: '10.20',
  previous_close: '10.00',
  change_percent: '0.02',
  volume: '1000',
  amount: '10200',
  reason: null,
}

const position = {
  security: { symbol: '000001.SZ', name: '平安银行' },
  quantity: '100',
  average_cost: '10.00',
  invested_notional: '1000',
  last_fill_at: '2026-08-27T10:00:00+08:00',
}

const stockPerformance = {
  symbol: '000001.SZ',
  as_of: '2026-08-31T18:00:00+08:00',
  status: 'incomplete',
  start_session_date: '2026-08-27',
  end_session_date: '2026-08-28',
  latest_session_date: '2026-08-28',
  latest_daily_return: null,
  latest_cumulative_return: null,
  highest_return: '0.02',
  highest_return_date: '2026-08-27',
  maximum_loss_return: null,
  maximum_loss_date: null,
  reason: '2026-08-28 缺少仍持仓证券的 approved market_daily 行情：000001.SZ',
  points: [
    {
      session_date: '2026-08-27',
      valuation_at: '2026-08-27T16:00:00+08:00',
      status: 'available',
      market_snapshot_ids: ['snapshot-manual'],
      daily_return: '0.02',
      cumulative_return: '0.02',
      reason: null,
    },
    {
      session_date: '2026-08-28',
      valuation_at: '2026-08-28T16:00:00+08:00',
      status: 'incomplete',
      market_snapshot_ids: [],
      daily_return: null,
      cumulative_return: null,
      reason: '缺少仍持仓证券的 approved market_daily 行情：000001.SZ',
    },
  ],
}

const trackingCase = {
  tracking_case_id: 'manual-case-1',
  tracking_policy_id: 'manual-position-tracking-v1',
  security: { symbol: '000001.SZ', name: '平安银行' },
  tracking_started_at: '2026-08-31T10:00:00+08:00',
  created_at: '2026-08-31T10:00:00+08:00',
  origin: 'historical_enrollment',
  source_order_ids: ['legacy-buy'],
  source_fill_ids: ['legacy-fill'],
}

function manualDetail(enrolled: boolean) {
  return {
    symbol: '000001.SZ',
    name: '平安银行',
    as_of: '2026-08-31T18:00:00+08:00',
    source: 'manual_discovery',
    market,
    position,
    tracking_cases: enrolled ? [trackingCase] : [],
    signals: [],
    timeline: [
      {
        event_id: 'legacy-buy',
        event_type: 'order',
        occurred_at: '2026-08-27T09:30:00+08:00',
        source_id: 'legacy-buy',
        title: 'BUY 订单',
        detail: '用户记录的手工订单意图',
      },
      {
        event_id: 'legacy-fill',
        event_type: 'fill',
        occurred_at: '2026-08-27T10:00:00+08:00',
        source_id: 'legacy-fill',
        title: 'BUY 成交',
        detail: '数量 100 · 价格 10',
      },
    ],
  }
}

function watchlistSignal(candidateId: string, batchId: string, symbol: string, name: string, selectionDate: string, formulaName: string) {
  const [localCode, exchange] = symbol.split('.')
  return {
    batch: {
      batch_id: batchId,
      source: 'manual',
      formula_id: 'formula-watchlist',
      source_filename: null,
      selection_date: selectionDate,
      exported_at: `${selectionDate}T15:00:00+08:00`,
      imported_at: `${selectionDate}T15:05:00+08:00`,
      formula_name: formulaName,
      formula_version: 'v1',
      formula_sha256: null,
      source_file_sha256: `${batchId}-sha256`,
      candidate_count: 1,
    },
    candidate: {
      candidate_id: candidateId,
      batch_id: batchId,
      local_code: localCode,
      symbol,
      exchange,
      name,
      source_fields: {},
    },
    labels: [],
    annotations: [],
    latest_annotation: null,
    exclusions: [],
    latest_exclusion: null,
    review_state: 'watching',
    excluded: false,
    outcomes: [],
    performances: [],
    market,
    observation_performance: null,
    confirmed_buy: false,
    confirmed_buy_fill_ids: [],
  }
}

const duplicateWatchlistSignals = [
  watchlistSignal('watch-a', 'batch-a', '000001.SZ', '平安银行', '2026-08-28', '趋势一'),
  watchlistSignal('watch-b', 'batch-b', '600000.SH', '浦发银行', '2026-08-27', '趋势二'),
  watchlistSignal('watch-c', 'batch-c', '000001.SZ', '平安银行', '2026-08-26', '趋势三'),
  watchlistSignal('watch-d', 'batch-d', '000002.SZ', '万科A', '2026-08-25', '趋势四'),
  watchlistSignal('watch-e', 'batch-e', '600000.SH', '浦发银行', '2026-08-24', '趋势五'),
]

async function installMocks(page: Page, { withPosition = true, withDuplicateWatchlist = false }: { withPosition?: boolean; withDuplicateWatchlist?: boolean } = {}) {
  let enrolled = false
  let tradeBody: Record<string, unknown> | null = null
  let reviewBody: Record<string, unknown> | null = null

  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/healthz' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok' }),
      })
      return
    }
    if (path === '/api/v1/workbench/positions' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(withPosition ? [{
          position,
          as_of: '2026-08-31T18:00:00+08:00',
          source: 'manual_discovery',
          selection_candidate_ids: [],
          holding_days: 4,
          market,
          unrealized_profit: '20',
          unrealized_return: '0.02',
          unrealized_reason: null,
        }] : []),
      })
      return
    }
    if (path === '/api/v1/workbench/stocks/000001.SZ/performance' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(stockPerformance),
      })
      return
    }
    if (path === '/api/v1/workbench/watchlist/page' && method === 'GET' && withDuplicateWatchlist) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: duplicateWatchlistSignals, page: 1, page_size: 50, total: duplicateWatchlistSignals.length, has_next: false }),
      })
      return
    }
    if (path === '/api/v1/workbench/stocks/000001.SZ' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(manualDetail(enrolled)),
      })
      return
    }
    if (path === '/api/v1/manual-tracking-cases/enroll' && method === 'POST') {
      enrolled = true
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(trackingCase),
      })
      return
    }
    if (path === '/api/v1/portfolio-reviews' && method === 'POST') {
      reviewBody = request.postDataJSON() as Record<string, unknown>
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ review_id: 'review-1', symbol: '000001.SZ', reviewed_at: '2026-08-31T18:10:00+08:00', note: reviewBody.note }),
      })
      return
    }
    if (path === '/api/v1/manual-trades/confirm' && method === 'POST') {
      tradeBody = request.postDataJSON() as Record<string, unknown>
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify([
          { order_id: 'manual-sell', tracking_case_id: 'manual-case-1' },
          { fill_id: 'manual-sell-fill', tracking_case_id: 'manual-case-1' },
        ]),
      })
      return
    }
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'TEST_UNEXPECTED_REQUEST',
          message: 'Unexpected test request',
          details: {},
        },
      }),
    })
  })

  return {
    reviewBody: () => reviewBody,
    tradeBody: () => tradeBody,
  }
}

test('无当前持仓时不提供单证券表现入口', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installMocks(page, { withPosition: false })
  await page.goto('/')
  await page.getByRole('button', { name: '持仓', exact: true }).click()

  const workspace = page.getByLabel('持仓工作区')
  await expect(workspace.getByRole('heading', { name: '持仓', exact: true })).toBeVisible()
  await expect(workspace.getByText('暂无持仓。', { exact: true })).toBeVisible()
  await expect(workspace.getByRole('button', { name: '表现', exact: true })).toHaveCount(0)
  await expect(page.getByText('组合历史净盈亏', { exact: true })).toHaveCount(0)

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth)
})

test('当前持仓详情展示单证券净收益率和缺失行情证据', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '持仓', exact: true }).click()
  await page.locator('.workbench-position-row').click()

  const drawer = page.getByRole('dialog', { name: '000001.SZ 股票详情' })
  await expect(drawer.getByRole('button', { name: '表现', exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: '表现', exact: true }).click()
  await expect(drawer.getByRole('heading', { name: '单证券历史净收益率', exact: true })).toBeVisible()
  await expect(drawer.getByRole('img', { name: /累计净收益率走势/ })).toBeVisible()
  await expect(drawer.getByRole('table', { name: '证券净收益率估值明细' })).toBeVisible()
  await expect(drawer.getByText('证据不完整', { exact: true }).first()).toBeVisible()
  await expect(drawer.locator('.stock-performance-summary').getByText('2.00%', { exact: true }).first()).toBeVisible()
  await expect(drawer.getByText(/缺少仍持仓证券的 approved market_daily 行情：000001\.SZ/).first()).toBeVisible()
  await expect(page.getByText('组合历史净盈亏', { exact: true })).toHaveCount(0)

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth)
})

test('观察池同页重复股票连续分组并保持相同的左侧颜色标记', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installMocks(page, { withDuplicateWatchlist: true })
  await page.goto('/')
  await page.getByRole('button', { name: '观察池', exact: true }).click()

  const workspace = page.getByLabel('观察池工作区')
  const rows = workspace.locator('.workbench-table--watchlist .workbench-table-row')
  await expect(rows).toHaveCount(duplicateWatchlistSignals.length)
  await expect(rows.locator('.workbench-stock-cell strong')).toHaveText([
    '000001.SZ',
    '000001.SZ',
    '600000.SH',
    '600000.SH',
    '000002.SZ',
  ])
  await expect(rows.nth(0).locator('.workbench-table-cell strong').first()).toHaveText('趋势一')
  await expect(rows.nth(1).locator('.workbench-table-cell strong').first()).toHaveText('趋势三')
  await expect(rows.nth(2).locator('.workbench-table-cell strong').first()).toHaveText('趋势二')
  await expect(rows.nth(3).locator('.workbench-table-cell strong').first()).toHaveText('趋势五')
  const markers = await rows.evaluateAll((elements) => elements.map((element) => {
    const style = window.getComputedStyle(element)
    return {
      color: style.borderLeftColor,
      style: style.borderLeftStyle,
      width: style.borderLeftWidth,
    }
  }))

  expect(markers[0]).toMatchObject({ style: 'solid', width: '4px' })
  expect(markers[1]).toMatchObject({ style: 'solid', width: '4px' })
  expect(markers[2]).toMatchObject({ style: 'solid', width: '4px' })
  expect(markers[3]).toMatchObject({ style: 'solid', width: '4px' })
  expect(markers[4]).toMatchObject({ style: 'none', width: '0px' })
  expect(markers[0].color).toBe(markers[1].color)
  expect(markers[2].color).toBe(markers[3].color)
  expect(markers[0].color).not.toBe(markers[2].color)
})

test('手工持仓详情可纳入跟踪并按案例提交卖出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await installMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '持仓', exact: true }).click()
  await expect(page.getByLabel('持仓工作区').getByRole('heading', { name: '持仓', exact: true })).toBeVisible()
  await page.locator('.workbench-position-row').click()

  const drawer = page.getByRole('dialog', { name: '000001.SZ 股票详情' })
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('手工持仓跟踪来源', { exact: true })).toBeVisible()
  await expect(drawer.getByRole('button', { name: '判断', exact: true })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: '信号', exact: true })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: '分析', exact: true })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: '复盘', exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: '跟踪', exact: true }).click()
  await drawer.getByRole('button', { name: '纳入当前持仓跟踪', exact: true }).press('Enter')
  await expect(drawer.getByRole('button', { name: '纳入当前持仓跟踪', exact: true })).toHaveCount(0)
  await drawer.getByRole('button', { name: '跟踪', exact: true }).click()
  await expect(drawer.getByText('已纳入', { exact: true })).toBeVisible()
  await expect(drawer.getByText('manual-case-1', { exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: '复盘', exact: true }).click()
  await drawer.getByLabel('复盘内容').fill('继续观察执行和风险变化。')
  await drawer.getByRole('button', { name: '追加本次复盘', exact: true }).click()
  await expect.poll(state.reviewBody).toMatchObject({ symbol: '000001.SZ', note: '继续观察执行和风险变化。' })
  await expect(drawer).toBeVisible()
  await drawer.getByRole('button', { name: '交易', exact: true }).click()
  await drawer.getByLabel(/跟踪案例/).selectOption('manual-case-1')
  await drawer.locator('input[type="number"]').nth(0).fill('40')
  await drawer.locator('input[type="number"]').nth(1).fill('10.50')
  await drawer.getByRole('button', { name: '确认并记录卖出', exact: true }).click()

  await expect.poll(state.tradeBody).toMatchObject({
    side: 'sell',
    tracking_case_id: 'manual-case-1',
    sell_reason: 'target_reached',
  })
  expect(state.tradeBody()).not.toHaveProperty('selection_candidate_id')
  await expect.poll(async () => {
    const currentBox = await page.getByRole('dialog', { name: '000001.SZ 股票详情' }).boundingBox()
    return currentBox?.width ?? 0
  }).toBeGreaterThan(0)
  const box = await page.getByRole('dialog', { name: '000001.SZ 股票详情' }).boundingBox()
  expect(box).not.toBeNull()
  expect(box?.width).toBeLessThanOrEqual(390)
})
