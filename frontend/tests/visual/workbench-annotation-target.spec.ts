import { expect, test, type Page, type Route } from '@playwright/test'

const market = {
  status: 'available',
  snapshot_id: 'snapshot-test',
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
  annotation: Record<string, unknown> | null,
  reviewAnnotation: Record<string, unknown> | null = annotation,
) {
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
      local_code: '002673',
      symbol: '002673.SZ',
      exchange: 'SZ',
      name: '西部证券',
      source_fields: {},
    },
    labels: [],
    annotations: annotation ? [annotation] : [],
    review_annotations: reviewAnnotation ? [reviewAnnotation] : [],
    review_group_key: '2026-08-28:002673.SZ',
    review_source_candidate_id: reviewAnnotation ? reviewAnnotation.candidate_id : null,
    latest_annotation: annotation,
    latest_review_annotation: reviewAnnotation,
    exclusions: [],
    latest_exclusion: null,
    review_state: reviewAnnotation ? 'watching' : 'unreviewed',
    excluded: false,
    outcomes: [],
    performances: [],
    market,
    observation_performance: null,
    confirmed_buy: false,
    confirmed_buy_fill_ids: [],
  }
}

async function installMocks(page: Page) {
  const reviewedAnnotation = {
    annotation_id: 'annotation-reviewed',
    candidate_id: 'candidate-reviewed',
    reviewed_at: '2026-08-30T15:26:00+08:00',
    recorded_at: '2026-08-30T15:27:00+08:00',
    overall_score: 2,
    outlook: 'bearish',
    decision: 'watch',
    position_tags: [],
    pattern_tags: [],
    price_volume_tags: [],
    sector_tags: [],
    reusable_tag_snapshots: [],
    taxonomy_version: 'selection-annotation-taxonomy-v1',
    note: null,
  }
  const unreviewedSignal = createSignal('batch-unreviewed', 'candidate-unreviewed', '策略 A', null, reviewedAnnotation)
  const reviewedSignal = createSignal('batch-reviewed', 'candidate-reviewed', '策略 B', reviewedAnnotation)
  const detail = {
    symbol: '002673.SZ',
    name: '西部证券',
    as_of: '2026-08-31T00:00:00+08:00',
    source: 'signal',
    market,
    position: null,
    tracking_cases: [],
    // The API returns the reviewed signal first; the clicked candidate must still win.
    signals: [reviewedSignal, unreviewedSignal],
    timeline: [],
  }
  let annotationCandidateId: string | null = null

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
          as_of: detail.as_of,
          signal_count: 2,
          unreviewed_count: 0,
          watching_count: 2,
          preparing_count: 0,
          reviewed_count: 2,
          confirmed_buy_count: 0,
        }),
      })
      return
    }
    if (path === '/api/v1/workbench/daily-signals/page' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [unreviewedSignal, reviewedSignal], page: 1, page_size: 50, total: 2, has_next: false }),
      })
      return
    }
    if (path === '/api/v1/workbench/stocks/002673.SZ' && method === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) })
      return
    }
    if (path === '/api/v1/selection-batches/batch-unreviewed/annotations' && method === 'POST') {
      annotationCandidateId = (request.postDataJSON() as { candidate_id?: string }).candidate_id ?? null
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'TEST_UNEXPECTED_REQUEST', message: 'Unexpected test request', details: {} } }),
    })
  })

  return { annotationCandidateId: () => annotationCandidateId }
}

test('点击同一股票的重复策略信号时，显示共享评审并保存到被点击的候选', async ({ page }) => {
  const state = await installMocks(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '策略信号', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: '工作台', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('策略信号工作区').getByRole('heading', { name: '策略信号', exact: true })).toBeVisible()
  await expect(page.getByText('最新行情：2026-08-28 收盘')).toBeVisible()

  const rows = page.locator('.workbench-table-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toContainText('收盘日 2026-08-28')
  await expect(rows.first()).toContainText('策略 A')
  await rows.first().click()

  const drawer = page.getByRole('dialog', { name: '002673.SZ 股票详情' })
  await expect(drawer).toBeVisible()
  await drawer.getByRole('button', { name: '判断', exact: true }).click()
  await expect(drawer.getByText('上次判断', { exact: false })).toBeVisible()
  await drawer.getByRole('button', { name: '追加本次判断', exact: true }).click()

  await expect.poll(state.annotationCandidateId).toBe('candidate-unreviewed')
})
