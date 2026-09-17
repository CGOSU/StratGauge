import { expect, test, type Page, type Route } from '@playwright/test'

const market = {
  status: 'available',
  snapshot_id: 'snapshot-dismissal',
  provider: 'offline-fixture',
  session_date: '2026-08-28',
  available_at: '2026-08-28T16:00:00+08:00',
  close: '6.83',
  previous_close: '6.86',
  change_percent: '-0.0043',
  volume: '1000',
  amount: '6830',
  reason: null,
}

function createSignal(
  batchId: string,
  candidateId: string,
  formulaName: string,
  reviewState: 'unreviewed' | 'watching',
) {
  const annotation = reviewState === 'watching' ? {
    annotation_id: `${candidateId}-annotation`,
    candidate_id: candidateId,
    reviewed_at: '2026-08-30T15:26:00+08:00',
    recorded_at: '2026-08-30T15:27:00+08:00',
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
  } : null
  return {
    batch: {
      batch_id: batchId,
      source: 'tongdaxin',
      source_filename: `${batchId}.txt`,
      formula_id: batchId,
      selection_date: '2026-08-28',
      exported_at: '2026-08-28T18:19:00+08:00',
      imported_at: '2026-08-28T18:20:00+08:00',
      formula_name: formulaName,
      formula_version: null,
      formula_sha256: null,
      source_file_sha256: 'a'.repeat(64),
      candidate_count: 1,
    },
    candidate: {
      candidate_id: candidateId,
      batch_id: batchId,
      local_code: reviewState === 'unreviewed' ? '000001' : '000002',
      symbol: reviewState === 'unreviewed' ? '000001.SZ' : '000002.SZ',
      exchange: 'SZ',
      name: reviewState === 'unreviewed' ? '平安银行' : '万科 A',
      source_fields: {},
    },
    labels: [],
    annotations: annotation ? [annotation] : [],
    review_annotations: annotation ? [annotation] : [],
    review_group_key: `2026-08-28:${reviewState === 'unreviewed' ? '000001.SZ' : '000002.SZ'}`,
    review_source_candidate_id: annotation ? candidateId : null,
    latest_annotation: annotation,
    latest_review_annotation: annotation,
    exclusions: [],
    latest_exclusion: null,
    dismissals: [],
    latest_dismissal: null,
    review_state: reviewState,
    excluded: false,
    dismissed: false,
    outcomes: [],
    performances: [],
    market,
    observation_performance: null,
    confirmed_buy: false,
    confirmed_buy_fill_ids: [],
  }
}

async function installMocks(page: Page, { failDismissal = false } = {}) {
  const unreviewedSignal = createSignal('batch-unreviewed', 'candidate-unreviewed', '策略 A', 'unreviewed')
  const reviewedSignal = createSignal('batch-reviewed', 'candidate-reviewed', '策略 B', 'watching')
  let dismissed = false
  let dismissalCandidateId: string | null = null
  let dismissalRequests = 0

  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/healthz' && method === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
      return
    }
    if (path === '/api/v1/workbench/summary' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          as_of: '2026-08-31T00:00:00+08:00',
          signal_count: dismissed ? 1 : 2,
          excluded_count: 0,
          unreviewed_count: dismissed ? 0 : 1,
          watching_count: 1,
          preparing_count: 0,
          reviewed_count: 1,
          confirmed_buy_count: 0,
        }),
      })
      return
    }
    if (path === '/api/v1/workbench/daily-signals/page' && method === 'GET') {
      const items = dismissed ? [reviewedSignal] : [unreviewedSignal, reviewedSignal]
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items, page: 1, page_size: 50, total: items.length, has_next: false }),
      })
      return
    }
    if (path === '/api/v1/selection-batches/batch-unreviewed/dismissals' && method === 'POST') {
      dismissalRequests += 1
      dismissalCandidateId = (request.postDataJSON() as { candidate_id?: string }).candidate_id ?? null
      if (failDismissal) {
        await route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'SELECTION_CANDIDATE_ALREADY_REVIEWED', message: '同一选股日的该股票已有人工判断，不能删除策略信号。', details: {} } }),
        })
        return
      }
      dismissed = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ dismissal: { dismissal_id: 'dismissal-test', candidate_id: dismissalCandidateId, dismissed_at: '2026-08-31T00:00:00+08:00', recorded_at: '2026-08-31T00:00:00+08:00', reason: '用户从策略信号中移除' }, created: true }),
      })
      return
    }
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} } }),
    })
  })

  return {
    dismissalCandidateId: () => dismissalCandidateId,
    dismissalRequests: () => dismissalRequests,
  }
}

test('未判断策略信号可确认删除，已判断行没有删除入口且删除不打开详情', async ({ page }) => {
  const state = await installMocks(page)
  await page.goto('/')

  const rows = page.locator('.workbench-table-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.first().locator('button')).toHaveCount(2)
  await expect(rows.nth(1).locator('button')).toHaveCount(1)
  await expect(page.getByRole('button', { name: '删除 000001.SZ 未判断信号' })).toBeVisible()
  await expect(page.getByRole('button', { name: '删除 000002.SZ 未判断信号' })).toHaveCount(0)
  await expect(page.locator('.workbench-table-row button button')).toHaveCount(0)

  let dialogMessage = ''
  page.once('dialog', async (dialog) => {
    dialogMessage = dialog.message()
    await dialog.dismiss()
  })
  await page.getByRole('button', { name: '删除 000001.SZ 未判断信号' }).click()
  await expect.poll(state.dismissalRequests).toBe(0)
  await expect(rows).toHaveCount(2)
  expect(dialogMessage).toContain('原始策略信号及其历史证据仍会保留')

  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: '删除 000001.SZ 未判断信号' }).click()
  await expect.poll(state.dismissalRequests).toBe(1)
  await expect.poll(state.dismissalCandidateId).toBe('candidate-unreviewed')
  await expect(rows).toHaveCount(1)
  await expect(page.getByText('策略 B')).toBeVisible()
  await expect(page.getByText('共 1 条')).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('删除接口失败时保留行并显示真实错误', async ({ page }) => {
  await installMocks(page, { failDismissal: true })
  await page.goto('/')
  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: '删除 000001.SZ 未判断信号' }).click()
  await expect(page.getByRole('alert')).toContainText('同一选股日的该股票已有人工判断，不能删除策略信号。')
  await expect(page.locator('.workbench-table-row')).toHaveCount(2)
})
