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

async function installMocks(page: Page) {
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
        body: JSON.stringify([{
          position,
          as_of: '2026-08-31T18:00:00+08:00',
          source: 'manual_discovery',
          selection_candidate_ids: [],
          holding_days: 4,
          market,
          unrealized_profit: '20',
          unrealized_return: '0.02',
          unrealized_reason: null,
        }]),
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
