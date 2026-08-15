import { expect, test, type Page, type Route } from '@playwright/test'

type MockOrder = {
  order_id: string
  security: { symbol: string; name: string | null }
  side: 'buy' | 'sell'
  ordered_at: string
  quantity: string
  limit_price: string | null
  recommendation_id: string | null
  selection_candidate_id: string | null
  sell_reason: string | null
  note: string | null
}

type MockFill = {
  fill_id: string
  order_id: string
  filled_at: string
  quantity: string
  price: string
  commission: string
  stamp_duty: string
  note: string | null
}

type MockState = {
  orderCount: () => number
  fillCount: () => number
  unexpectedRequests: () => string[]
}

function fulfillJson(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(payload),
  })
}

function emptyPage() {
  return { items: [], page: 1, page_size: 50, total: 0, has_next: false }
}

async function installApiMocks(page: Page): Promise<MockState> {
  const orders: MockOrder[] = []
  const fills: MockFill[] = []
  let orderCount = 0
  let fillCount = 0
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
    if (path === '/api/v1/annotation-tags' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/formulas' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/selection-batches/page' && method === 'GET') {
      await fulfillJson(route, emptyPage())
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
    if (
      (path === '/api/v1/workbench/daily-signals/page' || path === '/api/v1/workbench/watchlist/page')
      && method === 'GET'
    ) {
      await fulfillJson(route, emptyPage())
      return
    }
    if (path === '/api/v1/workbench/positions' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/workbench/trades' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/workbench/strategies' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/workbench/analytics' && method === 'GET') {
      await fulfillJson(route, { as_of: '2026-08-14T16:00:00+08:00', cohorts: [] })
      return
    }
    if (path === '/api/v1/positions' && method === 'GET') {
      const quantity = fills.reduce((total, fill) => total + Number(fill.quantity), 0)
      await fulfillJson(route, quantity > 0 ? [{
        security: { symbol: '000001.SZ', name: '平安银行' },
        quantity: String(quantity),
        average_cost: '10.00',
        invested_notional: (quantity * 10).toFixed(2),
        last_fill_at: fills[fills.length - 1]?.filled_at ?? null,
      }] : [])
      return
    }
    if (path === '/api/v1/manual-orders' && method === 'GET') {
      await fulfillJson(route, orders)
      return
    }
    if (path === '/api/v1/manual-fills' && method === 'GET') {
      await fulfillJson(route, fills)
      return
    }
    if (path === '/api/v1/portfolio-reviews' && method === 'GET') {
      await fulfillJson(route, [])
      return
    }
    if (path === '/api/v1/manual-orders' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, string | null>
      if (body.local_code === '999999') {
        await fulfillJson(route, {
          error: {
            code: 'PORTFOLIO_ORDER_REJECTED',
            message: '模拟拒绝：证券代码不在主板股票池。',
            details: {},
          },
        }, 409)
        return
      }
      const order: MockOrder = {
        order_id: 'ui-order-1',
        security: { symbol: `${body.local_code}.${body.exchange}`, name: body.name },
        side: body.side as 'buy' | 'sell',
        ordered_at: String(body.ordered_at),
        quantity: String(body.quantity),
        limit_price: body.limit_price,
        recommendation_id: null,
        selection_candidate_id: null,
        sell_reason: body.sell_reason,
        note: body.note,
      }
      orders.push(order)
      orderCount += 1
      await fulfillJson(route, order, 201)
      return
    }
    if (path === '/api/v1/manual-fills' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, string | null>
      const fill: MockFill = {
        fill_id: 'ui-fill-1',
        order_id: String(body.order_id),
        filled_at: String(body.filled_at),
        quantity: String(body.quantity),
        price: String(body.price),
        commission: String(body.commission ?? '0'),
        stamp_duty: String(body.stamp_duty ?? '0'),
        note: body.note,
      }
      fills.push(fill)
      fillCount += 1
      await fulfillJson(route, fill, 201)
      return
    }
    if (path === '/api/v1/manual-trades/confirm' && method === 'POST') {
      await fulfillJson(route, [
        {
          order_id: 'confirmed-order',
          security: { symbol: '000001.SZ', name: '平安银行' },
          side: 'buy',
          ordered_at: '2026-08-14T09:00:00+08:00',
          quantity: '100',
          limit_price: null,
          recommendation_id: null,
          selection_candidate_id: null,
          sell_reason: null,
          note: null,
        },
        {
          fill_id: 'confirmed-fill',
          order_id: 'confirmed-order',
          filled_at: '2026-08-14T09:00:00+08:00',
          quantity: '100',
          price: '10',
          commission: '0',
          stamp_duty: '0',
          note: null,
        },
      ], 201)
      return
    }
    if (path === '/api/v1/portfolio-reviews' && method === 'POST') {
      await fulfillJson(route, {
        review_id: 'review-ui-1',
        symbol: '000001.SZ',
        reviewed_at: '2026-08-14T09:00:00+08:00',
        note: '测试复盘',
        recommendation_id: null,
      }, 201)
      return
    }
    if (path === '/api/v1/universe/master' && method === 'GET') {
      await fulfillJson(route, {
        query: '',
        effective_date: '2026-08-14',
        known_at: '2026-08-14T09:00:00+08:00',
        master_snapshot_id: 'master-ui',
        observed_at: '2026-08-14T09:00:00+08:00',
        results: [],
      })
      return
    }

    unexpectedRequests.push(`${method} ${path}`)
    await fulfillJson(route, {
      error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} },
    }, 404)
  })

  return {
    orderCount: () => orderCount,
    fillCount: () => fillCount,
    unexpectedRequests: () => unexpectedRequests,
  }
}

async function openPortfolioPage(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '持仓', exact: true }).click()
  await page.getByText('打开现有手工台账录入', { exact: true }).click()
  const workspace = page.locator('.portfolio-workspace')
  await expect(workspace.getByRole('heading', { name: '持仓与复盘' })).toBeVisible()
  await expect(workspace.getByRole('heading', { name: '记录未成交订单' })).toBeVisible()
  return workspace
}

test.describe('人工订单与成交入口', () => {
  test('拒绝异常订单、记录未成交订单并追加部分成交', async ({ page }) => {
    const mocks = await installApiMocks(page)
    const workspace = await openPortfolioPage(page)
    const orderForm = workspace.locator('form.portfolio-order-entry')

    await orderForm.locator('#manual-order-code').fill('999999')
    await orderForm.getByRole('button', { name: '记录未成交订单' }).click()
    await expect(orderForm.getByRole('alert')).toContainText('模拟拒绝：证券代码不在主板股票池。')
    expect(mocks.orderCount()).toBe(0)

    await orderForm.locator('#manual-order-code').fill('000001')
    await orderForm.locator('#manual-order-name').fill('平安银行')
    await orderForm.locator('#manual-order-quantity').fill('100')
    await orderForm.getByRole('button', { name: '记录未成交订单' }).click()
    await expect(orderForm.getByRole('status')).toContainText('已记录未成交订单：000001.SZ，100 股。')
    await expect(workspace.getByText('000001.SZ · 买入 · 未成交')).toBeVisible()
    expect(mocks.orderCount()).toBe(1)

    const fillForm = workspace.locator('form.portfolio-fill-entry')
    await expect(fillForm.getByText('当前剩余未成交：')).toContainText('100 股')
    await fillForm.locator('#manual-fill-quantity').fill('40')
    await fillForm.locator('#manual-fill-price').fill('10')
    await fillForm.getByRole('button', { name: '追加成交事实' }).click()
    await expect(fillForm.getByRole('status')).toContainText('已追加成交：40 股；该订单剩余 60 股。')
    await expect(workspace.getByText('000001.SZ · 买入 · 部分成交，剩余 60 股')).toBeVisible()
    const positionCard = workspace.locator('.portfolio-position-card')
    await expect(positionCard.getByText('000001.SZ', { exact: true })).toBeVisible()
    await expect(positionCard.getByText('平安银行', { exact: true })).toBeVisible()
    expect(mocks.fillCount()).toBe(1)

    const fillsBeforeOverfill = mocks.fillCount()
    await fillForm.locator('#manual-fill-quantity').fill('61')
    await fillForm.getByRole('button', { name: '追加成交事实' }).click()
    await expect.poll(mocks.fillCount).toBe(fillsBeforeOverfill)
    await expect(fillForm.locator('#manual-fill-quantity')).toHaveAttribute('max', '60')

    expect(mocks.unexpectedRequests()).toEqual([])
  })

  test('保留旧确认成交入口并在卖出时显示结构化卖出原因', async ({ page }) => {
    await installApiMocks(page)
    const workspace = await openPortfolioPage(page)
    const confirmedForm = workspace.locator('form').filter({ has: page.getByRole('heading', { name: '录入订单与成交' }) })
    await expect(confirmedForm).toBeVisible()
    await confirmedForm.locator('#trade-side').selectOption('sell')
    await expect(confirmedForm.locator('#trade-sell-reason')).toBeVisible()
    await expect(confirmedForm.locator('#trade-sell-reason')).toHaveValue('target_reached')
  })
})
