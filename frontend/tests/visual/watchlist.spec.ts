import { expect, test, type Page, type Route } from '@playwright/test'

function fulfillJson(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(payload),
  })
}

const market = {
  status: 'available',
  snapshot_id: 'snapshot-latest',
  provider: 'offline-fixture',
  session_date: '2026-08-14',
  available_at: '2026-08-14T16:00:00+08:00',
  close: '11.00',
  previous_close: '10.80',
  change_percent: '0.0185',
  volume: '1000',
  amount: '11000',
  reason: null,
}

function createSignal(
  localCode: string,
  symbol: string,
  name: string,
  changePercent: string,
) {
  const batchId = `batch-${localCode}`
  const candidateId = `candidate-${localCode}`
  const annotation = {
    annotation_id: `annotation-${localCode}`,
    candidate_id: candidateId,
    reviewed_at: '2026-08-10T10:00:00+08:00',
    recorded_at: '2026-08-10T10:01:00+08:00',
    overall_score: 4,
    outlook: 'bullish',
    decision: 'watch',
    position_tags: [],
    pattern_tags: [],
    price_volume_tags: [],
    sector_tags: [],
    reusable_tag_snapshots: [],
    taxonomy_version: 'selection-annotation-taxonomy-v1',
    note: null,
  }
  return {
    batch: {
      batch_id: batchId,
      source: 'manual',
      formula_id: 'formula-test',
      source_filename: null,
      selection_date: '2026-08-10',
      exported_at: '2026-08-10T09:00:00+08:00',
      imported_at: '2026-08-10T09:01:00+08:00',
      formula_name: '趋势公式',
      formula_version: 'v1',
      formula_sha256: null,
      source_file_sha256: 'a'.repeat(64),
      candidate_count: 1,
    },
    candidate: {
      candidate_id: candidateId,
      batch_id: batchId,
      local_code: localCode,
      symbol,
      exchange: symbol.slice(-2),
      name,
      source_fields: {},
    },
    labels: [],
    annotations: [annotation],
    latest_annotation: annotation,
    exclusions: [],
    latest_exclusion: null,
    review_state: 'watching',
    excluded: false,
    outcomes: [],
    performances: [],
    market,
    observation_performance: {
      status: 'available',
      started_at: '2026-08-10T10:00:00+08:00',
      start_snapshot_id: `snapshot-start-${localCode}`,
      start_session_date: '2026-08-07',
      start_available_at: '2026-08-07T16:00:00+08:00',
      start_close: '10.00',
      change_percent: changePercent,
      reason: null,
    },
    confirmed_buy: false,
    confirmed_buy_fill_ids: [],
  }
}

async function installApiMocks(page: Page) {
  const positiveSignal = createSignal('000788', '000788.SZ', '北大医药', '0.1')
  const negativeSignal = createSignal('600000', '600000.SH', '浦发银行', '-0.05')
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
    if (path === '/api/v1/workbench/summary' && method === 'GET') {
      await fulfillJson(route, {
        as_of: '2026-08-14T16:00:00+08:00',
        signal_count: 2,
        unreviewed_count: 0,
        watching_count: 2,
        preparing_count: 0,
        reviewed_count: 2,
        confirmed_buy_count: 0,
      })
      return
    }
    if (path === '/api/v1/workbench/daily-signals/page' && method === 'GET') {
      await fulfillJson(route, { items: [], page: 1, page_size: 50, total: 0, has_next: false })
      return
    }
    if (path === '/api/v1/workbench/watchlist/page' && method === 'GET') {
      await fulfillJson(route, {
        items: [positiveSignal, negativeSignal],
        page: 1,
        page_size: 50,
        total: 2,
        has_next: false,
      })
      return
    }
    if (path === '/api/v1/workbench/stocks/000788.SZ' && method === 'GET') {
      await fulfillJson(route, {
        symbol: positiveSignal.candidate.symbol,
        name: positiveSignal.candidate.name,
        as_of: '2026-08-14T16:00:00+08:00',
        source: 'signal',
        market,
        position: null,
        tracking_cases: [],
        signals: [positiveSignal],
        timeline: [
          {
            event_id: 'signal-000788',
            event_type: 'signal',
            occurred_at: '2026-08-10T09:00:00+08:00',
            source_id: 'batch-000788',
            title: '策略信号：趋势公式',
            detail: '选股日期 2026-08-10',
          },
          {
            event_id: 'annotation-000788',
            event_type: 'annotation',
            occurred_at: '2026-08-10T10:00:00+08:00',
            source_id: 'annotation-000788',
            title: '人工评审：4 分',
            detail: '观点 bullish · 决策 watch',
          },
        ],
      })
      return
    }
    unexpectedRequests.push(`${method} ${path}`)
    await fulfillJson(route, {
      error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} },
    }, 404)
  })

  return { unexpectedRequests: () => unexpectedRequests }
}

test('展示观察期涨跌并在详情中提供雪球日线 K 线外链', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const mocks = await installApiMocks(page)
  await page.goto('/')
  await page.getByRole('button', { name: '观察池', exact: true }).click()

  const table = page.locator('.workbench-table--watchlist')
  await expect(table).toBeVisible()
  await expect(table).toContainText('观察期涨跌')
  await expect(table).toContainText('10.00%')
  await expect(table).toContainText('-5.00%')
  await expect(table.locator('.workbench-observation-cell .market-profit')).toContainText('10.00%')
  await expect(table.locator('.workbench-observation-cell .market-loss')).toContainText('-5.00%')
  await expect(table).toContainText('起点价 10.00')
  await expect(table).toContainText('起点 2026年8月10日 10:00')

  const tableLayout = await table.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    overflowX: getComputedStyle(element).overflowX,
    pageScrollWidth: document.documentElement.scrollWidth,
  }))
  expect(tableLayout.scrollWidth).toBeGreaterThan(tableLayout.clientWidth)
  expect(tableLayout.overflowX).toBe('auto')
  expect(tableLayout.pageScrollWidth).toBeLessThanOrEqual(390)

  await table.locator('.workbench-table-row').first().click()
  const drawer = page.getByRole('dialog', { name: '000788.SZ 股票详情' })
  await expect(drawer).toBeVisible()
  const chartLink = drawer.getByRole('link', { name: '000788.SZ 在雪球查看日线 K 线' })
  await expect(chartLink).toHaveAttribute('href', 'https://xueqiu.com/S/SZ000788')
  await expect(chartLink).toHaveAttribute('target', '_blank')
  await expect(chartLink).toHaveAttribute('rel', 'noreferrer')
  await chartLink.focus()
  await expect(chartLink).toBeFocused()
  expect(mocks.unexpectedRequests()).toEqual([])
})
