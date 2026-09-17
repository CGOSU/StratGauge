import { useEffect, useRef, useState } from 'react'
import { RotateCcw, Trash2 } from 'lucide-react'
import { requestJson } from '@/api/client'
import { PortfolioWorkspace } from '@/components/portfolio-workspace'
import type { Annotation, ReviewState, WorkbenchDismissalResponse, WorkbenchSignalPage, WorkbenchSignal, WorkbenchSummary, WorkbenchStockDetail, WorkbenchPositionView, WorkbenchTradeView, WorkbenchCohortMetrics, WorkbenchStrategyView, WorkbenchAnalyticsView, WorkbenchRoute, WorkbenchReviewFilter } from '@/types'
import { annotationOutlookLabels, annotationDecisionLabels } from '@/lib/labels'
import { formatManagementDate, formatPrice, formatPercent } from '@/lib/format'
import { PaginationControls } from '@/components/pagination-controls'
import { appRouteLabels } from '@/components/app-shell'
import { StockDetailDrawer } from '@/components/workbench/detail-drawer'


const WORKBENCH_QUERY_DEBOUNCE_MS = 250
const DUPLICATE_ROW_MARKER_CLASSES = [
  'workbench-table-row--duplicate-1',
  'workbench-table-row--duplicate-2',
  'workbench-table-row--duplicate-3',
  'workbench-table-row--duplicate-4',
] as const


export function workbenchReviewLabel(state: ReviewState): string {
  const labels: Record<ReviewState, string> = {
    unreviewed: '未判断',
    discarded: '不看好',
    watching: '观察中',
    preparing: '准备买入',
  }
  return labels[state]
}

export function workbenchCohortLabel(cohort: WorkbenchCohortMetrics['cohort']): string {
  const labels: Record<WorkbenchCohortMetrics['cohort'], string> = {
    all_signals: '全部信号',
    human_bullish: '人工看好',
    confirmed_trades: '实际买入',
  }
  return labels[cohort]
}
export function workbenchLatestAnnotation(signal: WorkbenchSignal): Annotation | null {
  return signal.latest_review_annotation ?? signal.latest_annotation
}
function workbenchReviewAnnotations(signal: WorkbenchSignal): Annotation[] {
  return signal.review_annotations ?? signal.annotations
}
type WorkbenchReviewGroup = {
  key: string
  symbol: string
  annotations: Annotation[]
}
function workbenchReviewGroups(signals: WorkbenchSignal[]): WorkbenchReviewGroup[] {
  const groups = new Map<string, WorkbenchReviewGroup>()
  for (const signal of signals) {
    const key = signal.review_group_key ?? `${signal.batch.selection_date}:${signal.candidate.symbol}`
    const group = groups.get(key) ?? { key, symbol: signal.candidate.symbol, annotations: [] }
    const annotationIds = new Set(group.annotations.map((annotation) => annotation.annotation_id))
    for (const annotation of workbenchReviewAnnotations(signal)) {
      if (!annotationIds.has(annotation.annotation_id)) {
        group.annotations.push(annotation)
        annotationIds.add(annotation.annotation_id)
      }
    }
    group.annotations.sort((left, right) => left.recorded_at.localeCompare(right.recorded_at) || left.annotation_id.localeCompare(right.annotation_id))
    groups.set(key, group)
  }
  return [...groups.values()]
}
export function matchesWorkbenchFilter(signal: WorkbenchSignal, filter: WorkbenchReviewFilter): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'unreviewed':
      return signal.review_state === 'unreviewed'
    case 'bullish':
      return workbenchLatestAnnotation(signal)?.outlook === 'bullish'
    case 'watching':
      return signal.review_state === 'watching'
    case 'preparing':
      return signal.review_state === 'preparing'
    case 'discarded':
      return signal.review_state === 'discarded'
    case 'bought':
      return signal.confirmed_buy
  }
}
export function workbenchActionRank(signal: WorkbenchSignal): number {
  if (!workbenchLatestAnnotation(signal)) return 0
  if (signal.review_state === 'watching' || signal.review_state === 'preparing') return 1
  if (signal.confirmed_buy) return 3
  return 2
}

export function workbenchSignalSort(signals: WorkbenchSignal[]): WorkbenchSignal[] {
  return [...signals].sort((left, right) => {
    const rankDifference = workbenchActionRank(left) - workbenchActionRank(right)
    if (rankDifference !== 0) return rankDifference
    const leftAt = workbenchLatestAnnotation(left)?.reviewed_at ?? left.batch.selection_date
    const rightAt = workbenchLatestAnnotation(right)?.reviewed_at ?? right.batch.selection_date
    return rightAt.localeCompare(leftAt)
  })
}

function latestMarketSessionDate(signals: WorkbenchSignal[]): string | null {
  return signals.reduce<string | null>((latest, signal) => {
    const sessionDate = signal.market.session_date
    return sessionDate && (!latest || sessionDate > latest) ? sessionDate : latest
  }, null)
}
export function WorkbenchChangeSummary({ signals }: { signals: WorkbenchSignal[] }) {
  const reviewGroups = workbenchReviewGroups(signals)
  const recheckGroups = reviewGroups.filter((group) => group.annotations.length > 1)
  const changedGroups = recheckGroups.filter((group) => {
    const latest = group.annotations[group.annotations.length - 1]
    const previous = group.annotations[group.annotations.length - 2]
    return latest.overall_score !== previous.overall_score || latest.outlook !== previous.outlook || latest.decision !== previous.decision
  })
  const preparingGroupCount = reviewGroups.filter((group) => group.annotations[group.annotations.length - 1]?.decision === 'prepare_buy').length
  return (
    <section className="workbench-change-summary" aria-label="观察池变化摘要">
      <div className="workbench-section-heading workbench-section-heading--compact">
        <div><span className="eyebrow">WATCHLIST · SINCE LAST JUDGMENT</span><h3>自上次判断以来发生了什么</h3></div>
        <span className="workbench-as-of">仅基于追加记录</span>
      </div>
      <div className="workbench-change-cards">
        <div><strong>{recheckGroups.length}</strong><span>已有复评记录</span></div>
        <div><strong>{changedGroups.length}</strong><span>判断发生变化</span></div>
        <div><strong>{preparingGroupCount}</strong><span>当前准备买入</span></div>
      </div>
      {changedGroups.length > 0 ? <div className="workbench-change-list">{changedGroups.slice(0, 8).map((group) => {
        const latest = group.annotations[group.annotations.length - 1]
        const previous = group.annotations[group.annotations.length - 2]
        return <div className="workbench-change-row" key={group.key}><strong>{group.symbol}</strong><span>{annotationDecisionLabels[previous.decision]} → {annotationDecisionLabels[latest.decision]}</span><span>{previous.overall_score} 分 → {latest.overall_score} 分</span><small>{formatManagementDate(latest.reviewed_at)}</small></div>
      })}</div> : <p className="workbench-empty workbench-empty--compact">当前没有可比较的复评变化；未判断不等于不看好。</p>}
    </section>
  )
}
function observationChangeClass(
  observation: WorkbenchSignal['observation_performance'],
): string {
  if (!observation || observation.status !== 'available' || observation.change_percent === null) {
    return 'market-pending'
  }
  const value = Number(observation.change_percent)
  return Number.isFinite(value) && value >= 0 ? 'market-profit' : 'market-loss'
}

function stableGroupSignals(signals: WorkbenchSignal[]): WorkbenchSignal[] {
  const groups = new Map<string, WorkbenchSignal[]>()
  for (const signal of signals) {
    const symbol = signal.candidate.symbol
    const group = groups.get(symbol)
    if (group) {
      group.push(signal)
    } else {
      groups.set(symbol, [signal])
    }
  }
  return [...groups.values()].flat()
}

export function WorkbenchSignalTable({
  signals,
  onOpen,
  onDismiss,
  dismissingCandidateId = null,
  emptyMessage,
  showObservationPerformance = false,
}: {
  signals: WorkbenchSignal[]
  onOpen: (symbol: string, candidateId: string) => void
  onDismiss?: (signal: WorkbenchSignal) => void | Promise<void>
  dismissingCandidateId?: string | null
  emptyMessage: string
  showObservationPerformance?: boolean
}) {
  const duplicateSymbolCounts = signals.reduce((counts, signal) => {
    const symbol = signal.candidate.symbol
    counts.set(symbol, (counts.get(symbol) ?? 0) + 1)
    return counts
  }, new Map<string, number>())
  const duplicateSymbolMarkers = new Map<string, (typeof DUPLICATE_ROW_MARKER_CLASSES)[number]>()
  if (showObservationPerformance) {
    for (const signal of signals) {
      const symbol = signal.candidate.symbol
      if ((duplicateSymbolCounts.get(symbol) ?? 0) < 2 || duplicateSymbolMarkers.has(symbol)) continue
      duplicateSymbolMarkers.set(symbol, DUPLICATE_ROW_MARKER_CLASSES[duplicateSymbolMarkers.size % DUPLICATE_ROW_MARKER_CLASSES.length])
    }
  }
  const displaySignals = showObservationPerformance ? stableGroupSignals(signals) : signals
  return displaySignals.length === 0 ? <p className="workbench-empty">{emptyMessage}</p> : (
    <div className={`workbench-table${showObservationPerformance ? ' workbench-table--watchlist' : ' workbench-table--signals'}`} role="table" aria-label={showObservationPerformance ? '观察池列表' : '策略信号列表'}>
      <div className="workbench-table-header" role="row"><span>股票</span><span>策略</span><span>信号时间</span><span>行情</span>{showObservationPerformance && <span>观察期涨跌</span>}<span>人工判断</span><span>状态</span>{!showObservationPerformance && <span>操作</span>}</div>
      {displaySignals.map((signal) => {
        const observation = signal.observation_performance
        const latestAnnotation = workbenchLatestAnnotation(signal)
        const duplicateMarker = duplicateSymbolMarkers.get(signal.candidate.symbol)
        return (
          <div
            className={`workbench-table-row${duplicateMarker ? ` workbench-table-row--duplicate ${duplicateMarker}` : ''}`}
            role="row"
            key={signal.candidate.candidate_id}
            onClick={(event) => {
              if (event.target instanceof Element && event.target.closest('.workbench-table-row-dismiss')) {
                return
              }
              onOpen(signal.candidate.symbol, signal.candidate.candidate_id)
            }}
          >
            <button
              type="button"
              className="workbench-table-row-main"
              aria-label={`打开 ${signal.candidate.symbol} 股票详情`}
            />
            <div className="workbench-table-row-content" aria-hidden="true">
              <span className="workbench-stock-cell"><strong>{signal.candidate.symbol}</strong><small>{signal.candidate.name ?? '未提供名称'}</small></span>
              <span className="workbench-table-cell"><strong>{signal.batch.formula_name}</strong><small>{signal.batch.formula_version ?? '未版本化'}</small></span>
              <span className="workbench-table-cell"><strong>{signal.batch.selection_date}</strong><small>{formatManagementDate(signal.batch.exported_at)}</small></span>
              <span className="workbench-table-cell"><strong className="market-value">{formatPrice(signal.market.close)}</strong><small className={`market-change ${Number(signal.market.change_percent ?? 0) >= 0 ? 'market-profit' : 'market-loss'}`}>{formatPercent(signal.market.change_percent)}</small><small>{signal.market.session_date ? `收盘日 ${signal.market.session_date}` : '收盘日期不可用'}</small></span>
              {showObservationPerformance && <span className="workbench-table-cell workbench-observation-cell"><strong className={`market-value ${observationChangeClass(observation)}`}>{observation?.status === 'available' ? formatPercent(observation.change_percent) : '—'}</strong><small>{observation?.started_at ? `起点 ${formatManagementDate(observation.started_at)}` : '观察起点不可用'}</small><small>{observation?.start_close !== null && observation?.start_close !== undefined ? `起点价 ${formatPrice(observation.start_close)}` : observation?.reason ?? '起点价不可用'}</small></span>}
              <span className="workbench-table-cell"><strong>{latestAnnotation ? `${'★'.repeat(latestAnnotation.overall_score)}${'☆'.repeat(5 - latestAnnotation.overall_score)}` : '—'}</strong><small>{latestAnnotation ? annotationOutlookLabels[latestAnnotation.outlook] : '未判断'}</small></span>
              <span className="workbench-table-status"><span className={`workbench-status workbench-status--${signal.review_state}`}>{workbenchReviewLabel(signal.review_state)}</span>{signal.confirmed_buy && <span className="workbench-status workbench-status--bought">已买入</span>}</span>
            </div>
            {onDismiss && !showObservationPerformance && signal.review_state === 'unreviewed' && !signal.dismissed && (
              <button
                type="button"
                className="workbench-table-row-dismiss"
                aria-label={`删除 ${signal.candidate.symbol} 未判断信号`}
                title="删除未判断信号"
                disabled={dismissingCandidateId !== null}
                onClick={() => void onDismiss(signal)}
              >
                <Trash2 aria-hidden="true" />
                <span>{dismissingCandidateId === signal.candidate.candidate_id ? '删除中…' : '删除'}</span>
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
export function WorkbenchFilterBar({
  query,
  onQueryChange,
  filter,
  onFilterChange,
  onRefresh,
  busy,
}: {
  query: string
  onQueryChange: (value: string) => void
  filter: WorkbenchReviewFilter
  onFilterChange: (value: WorkbenchReviewFilter) => void
  onRefresh: () => void
  busy: boolean
}) {
  const filters: Array<[WorkbenchReviewFilter, string]> = [
    ['all', '全部'],
    ['unreviewed', '未判断'],
    ['bullish', '看好'],
    ['watching', '观察'],
    ['preparing', '准备买入'],
    ['discarded', '不看好'],
    ['bought', '已买入'],
  ]
  return (
    <div className="workbench-filter-bar">
      <label className="workbench-search"><span className="sr-only">搜索股票或策略</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索代码、名称或策略" /></label>
      <div className="workbench-filter-buttons" role="group" aria-label="信号筛选">
        {filters.map(([value, label]) => <button type="button" className={filter === value ? 'is-active' : ''} key={value} onClick={() => onFilterChange(value)}>{label}</button>)}
      </div>
      <button type="button" className="workbench-refresh" onClick={onRefresh} disabled={busy}><RotateCcw className={busy ? 'is-spinning' : ''} aria-hidden="true" />刷新</button>
    </div>
  )
}
export function workbenchServerReviewState(filter: WorkbenchReviewFilter): ReviewState | undefined {
  return filter === 'unreviewed' || filter === 'watching' || filter === 'preparing' || filter === 'discarded'
    ? filter
    : undefined
}
export function WorkbenchWorkspace({
  apiBase,
  route,
}: {
  apiBase: string
  route: WorkbenchRoute
}) {
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null)
  const [signals, setSignals] = useState<WorkbenchSignal[]>([])
  const [positions, setPositions] = useState<WorkbenchPositionView[]>([])
  const [trades, setTrades] = useState<WorkbenchTradeView[]>([])
  const [strategies, setStrategies] = useState<WorkbenchStrategyView[]>([])
  const [analytics, setAnalytics] = useState<WorkbenchAnalyticsView | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<WorkbenchReviewFilter>('all')
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [dismissingCandidateId, setDismissingCandidateId] = useState<string | null>(null)
  const [signalPage, setSignalPage] = useState(1)
  const [signalTotal, setSignalTotal] = useState(0)
  const signalPageSize = 50
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null)
  const [stockDetail, setStockDetail] = useState<WorkbenchStockDetail | null>(null)
  const [drawerLoading, setDrawerLoading] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [loadedRoute, setLoadedRoute] = useState<WorkbenchRoute | null>(null)
  const [legacyToolsOpen, setLegacyToolsOpen] = useState(false)
  const requestScope = useRef({ route, filter, query })
  const stockController = useRef<AbortController | null>(null)
  const stockRequestId = useRef(0)

  useEffect(() => {
    const scopeChanged = requestScope.current.route !== route
      || requestScope.current.filter !== filter
      || requestScope.current.query !== query
    if (scopeChanged) {
      requestScope.current = { route, filter, query }
      if (signalPage !== 1) {
        setSignalPage(1)
        return
      }
    }

    const controller = new AbortController()
    setLoading(true)
    setPageError(null)
    const debounceTimer = window.setTimeout(() => {
      const signal = { signal: controller.signal }
      const load = async () => {
        try {
          const pageParams = new URLSearchParams({
            page: String(signalPage),
            page_size: String(signalPageSize),
          })
          if (query.trim()) {
            pageParams.set('query', query.trim())
          }
          const serverReviewState = workbenchServerReviewState(filter)
          if (serverReviewState) {
            pageParams.set('review_state', serverReviewState)
          }
          if (route === 'signals') {
            const [nextSummary, nextSignals] = await Promise.all([
              requestJson<WorkbenchSummary>(`${apiBase}/v1/workbench/summary`, signal),
              requestJson<WorkbenchSignalPage>(`${apiBase}/v1/workbench/daily-signals/page?${pageParams.toString()}`, signal),
            ])
            setSummary(nextSummary)
            setSignals(nextSignals.items)
            setSignalTotal(nextSignals.total)
          } else if (route === 'watchlist') {
            const nextSignals = await requestJson<WorkbenchSignalPage>(`${apiBase}/v1/workbench/watchlist/page?${pageParams.toString()}`, signal)
            setSignals(nextSignals.items)
            setSignalTotal(nextSignals.total)
            setSummary(null)
          } else if (route === 'positions') {
            const nextPositions = await requestJson<WorkbenchPositionView[]>(
              `${apiBase}/v1/workbench/positions`,
              signal,
            )
            setPositions(nextPositions)
          } else if (route === 'trades') {
            setTrades(await requestJson<WorkbenchTradeView[]>(`${apiBase}/v1/workbench/trades`, signal))
          } else if (route === 'strategies') {
            setStrategies(await requestJson<WorkbenchStrategyView[]>(`${apiBase}/v1/workbench/strategies`, signal))
          } else {
            setAnalytics(await requestJson<WorkbenchAnalyticsView>(`${apiBase}/v1/workbench/analytics`, signal))
          }
          setLoadedRoute(route)
        } catch (reason: unknown) {
          if (reason instanceof DOMException && reason.name === 'AbortError') {
            return
          }
          setPageError(reason instanceof Error ? reason.message : '加载研究数据失败。')
        } finally {
          if (!controller.signal.aborted) {
            setLoading(false)
          }
        }
      }
      void load()
    }, WORKBENCH_QUERY_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(debounceTimer)
      controller.abort()
    }
  }, [apiBase, filter, query, route, reloadToken, signalPage])

  useEffect(() => {
    stockController.current?.abort()
    stockController.current = null
    stockRequestId.current += 1
    setSelectedSymbol(null)
    setSelectedCandidateId(null)
    setStockDetail(null)
    setDrawerError(null)
    setLegacyToolsOpen(false)
  }, [route])

  function handleQueryChange(value: string) {
    setQuery(value)
    setSignalPage(1)
  }

  function handleFilterChange(value: WorkbenchReviewFilter) {
    setFilter(value)
    setSignalPage(1)
  }

  function closeStock() {
    stockController.current?.abort()
    stockController.current = null
    stockRequestId.current += 1
    setSelectedSymbol(null)
    setSelectedCandidateId(null)
    setStockDetail(null)
    setDrawerLoading(false)
    setDrawerError(null)
  }

  async function openStock(symbol: string, candidateId?: string) {
    stockController.current?.abort()
    const controller = new AbortController()
    stockController.current = controller
    const requestId = stockRequestId.current + 1
    stockRequestId.current = requestId
    setSelectedSymbol(symbol)
    setSelectedCandidateId(candidateId ?? null)
    setStockDetail(null)
    setDrawerError(null)
    setDrawerLoading(true)
    try {
      const detail = await requestJson<WorkbenchStockDetail>(
        `${apiBase}/v1/workbench/stocks/${encodeURIComponent(symbol)}`,
        { signal: controller.signal },
      )
      if (requestId === stockRequestId.current && !controller.signal.aborted) {
        setStockDetail(detail)
      }
    } catch (reason: unknown) {
      if (reason instanceof DOMException && reason.name === 'AbortError') {
        return
      }
      if (requestId === stockRequestId.current) {
        setDrawerError(reason instanceof Error ? reason.message : '加载股票详情失败。')
      }
    } finally {
      if (requestId === stockRequestId.current && !controller.signal.aborted) {
        setDrawerLoading(false)
      }
    }
  }

  async function refreshStock() {
    if (!selectedSymbol) {
      return
    }
    await openStock(selectedSymbol, selectedCandidateId ?? undefined)
  }

  async function dismissSignal(signal: WorkbenchSignal) {
    if (route !== 'signals' || signal.review_state !== 'unreviewed' || dismissingCandidateId !== null) {
      return
    }
    const confirmed = window.confirm(
      `删除 ${signal.candidate.symbol} 的未判断信号？\n\n这只会将它从策略信号列表移除，原始策略信号及其历史证据仍会保留。`,
    )
    if (!confirmed) {
      return
    }
    const candidateId = signal.candidate.candidate_id
    setDismissingCandidateId(candidateId)
    setPageError(null)
    try {
      await requestJson<WorkbenchDismissalResponse>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(signal.batch.batch_id)}/dismissals`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ candidate_id: candidateId }),
        },
      )
      if (selectedCandidateId === candidateId) {
        closeStock()
      }
      setSignalPage((current) => current > 1 && signals.length === 1 ? current - 1 : current)
      setReloadToken((value) => value + 1)
    } catch (reason: unknown) {
      setPageError(reason instanceof Error ? reason.message : '删除策略信号失败。')
    } finally {
      setDismissingCandidateId(null)
    }
  }

  const visibleSignals = workbenchSignalSort(signals.filter((signal) => {
    const normalizedQuery = query.trim().toLowerCase()
    const matchesQuery = !normalizedQuery || [signal.candidate.symbol, signal.candidate.local_code, signal.candidate.name ?? '', signal.batch.formula_name].some((value) => value.toLowerCase().includes(normalizedQuery))
    return matchesQuery && matchesWorkbenchFilter(signal, filter)
  }))
  const latestSignalMarketDate = latestMarketSessionDate(signals)
  const summaryCards = summary ? [
    ['策略信号', summary.signal_count, 'neutral'],
    ['待判断', summary.unreviewed_count, 'pending'],
    ['观察中', summary.watching_count, 'review'],
    ['准备买入', summary.preparing_count, 'profit'],
    ['已判断', summary.reviewed_count, 'neutral'],
    ['当前已买入', summary.confirmed_buy_count, 'confirmed'],
  ] as Array<[string, number, string]> : []

  return (
    <section className="workbench-page" aria-label={`${appRouteLabels[route]}工作区`}>
      {pageError && <div className="workbench-page-error" role="alert">{pageError}</div>}
      {route === 'signals' && summary && (
        <div className="workbench-kpi-grid">
          {summaryCards.map(([label, value, tone]) => <article className={`workbench-kpi workbench-kpi--${tone}`} key={label}><span>{label}</span><strong className="workbench-kpi-value">{value}</strong></article>)}
        </div>
      )}
      {route === 'signals' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">SIGNALS · IMMUTABLE FACTS</span><h2>策略信号</h2><p>展示当前证据时间内已导入批次的所有策略命中；人工判断不会改变原始信号。</p></div><span className="workbench-as-of">共 {signalTotal} 条 · 最新行情：{latestSignalMarketDate ? `${latestSignalMarketDate} 收盘` : '不可用'}</span></div>
          <WorkbenchFilterBar query={query} onQueryChange={handleQueryChange} filter={filter} onFilterChange={handleFilterChange} onRefresh={() => setReloadToken((value) => value + 1)} busy={loading} />
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取信号…</div> : <WorkbenchSignalTable signals={visibleSignals} onOpen={(symbol, candidateId) => void openStock(symbol, candidateId)} onDismiss={dismissSignal} dismissingCandidateId={dismissingCandidateId} emptyMessage="没有匹配的策略信号。" />}
          <PaginationControls page={signalPage} pageSize={signalPageSize} total={signalTotal} busy={loading} onPageChange={setSignalPage} />
        </>
      )}
      {route === 'watchlist' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">WATCHLIST · RE-EVALUATION</span><h2>观察池</h2><p>只显示已进入观察或准备买入、且尚未确认买入的信号。</p></div><span className="workbench-as-of">共 {signalTotal} 条</span></div>
          <WorkbenchFilterBar query={query} onQueryChange={handleQueryChange} filter={filter} onFilterChange={handleFilterChange} onRefresh={() => setReloadToken((value) => value + 1)} busy={loading} />
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取观察池…</div> : <><WorkbenchChangeSummary signals={signals} /><WorkbenchSignalTable signals={visibleSignals} onOpen={(symbol, candidateId) => void openStock(symbol, candidateId)} emptyMessage="观察池为空。" showObservationPerformance /><PaginationControls page={signalPage} pageSize={signalPageSize} total={signalTotal} busy={loading} onPageChange={setSignalPage} /></>}
        </>
      )}
      {route === 'positions' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">POSITIONS · CONFIRMED FACTS</span><h2>持仓</h2><p>持仓由已确认成交推导，不把策略信号或推荐当作交易。</p></div><span className="workbench-as-of">共 {positions.length} 个</span></div>
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取持仓…</div> : positions.length === 0 ? <p className="workbench-empty">暂无持仓。</p> : <div className="workbench-position-list">{positions.map((item) => <button type="button" className="workbench-position-row" key={item.position.security.symbol} onClick={() => void openStock(item.position.security.symbol)}><span className="workbench-stock-cell"><strong>{item.position.security.symbol}</strong><small>{item.position.security.name ?? '未提供名称'} · {item.source === 'signal' ? '策略来源' : item.source === 'mixed' ? '混合来源' : '手工发现'}</small></span><span><small>数量</small><strong>{item.position.quantity}</strong></span><span><small>成本</small><strong>{formatPrice(item.position.average_cost)}</strong></span><span><small>现价</small><strong>{formatPrice(item.market.close)}</strong></span><span><small>浮动收益</small><strong className={Number(item.unrealized_return ?? 0) >= 0 ? 'market-profit' : 'market-loss'}>{formatPercent(item.unrealized_return)}</strong></span><span><small>持有天数</small><strong>{item.holding_days}</strong></span></button>)}</div>}
        </>
      )}
      {route === 'trades' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">TRADES · MANUAL EXECUTIONS</span><h2>交易记录</h2><p>仅展示用户显式确认的订单与成交。</p></div><span className="workbench-as-of">共 {trades.length} 笔</span></div>
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取交易…</div> : trades.length === 0 ? <p className="workbench-empty">暂无已确认成交。</p> : <div className="workbench-trade-list">{trades.map((trade) => <button type="button" className="workbench-trade-row" key={trade.order.order_id} onClick={() => void openStock(trade.order.security.symbol)}><span className="workbench-stock-cell"><strong>{trade.order.security.symbol}</strong><small>{trade.order.security.name ?? '未提供名称'} · {trade.source === 'signal' ? '策略来源' : trade.source === 'mixed' ? '混合来源' : '手工发现'}</small></span><span className={`trade-side trade-side--${trade.order.side}`}>{trade.order.side === 'buy' ? '买入' : '卖出'}</span><span><small>数量</small><strong>{trade.order.quantity}</strong></span><span><small>成交</small><strong>{trade.fills.length} 笔</strong></span><span><small>最近成交</small><strong>{trade.fills.length ? formatManagementDate(trade.fills[trade.fills.length - 1].filled_at) : '—'}</strong></span></button>)}</div>}
        </>
      )}
      {route === 'strategies' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">STRATEGIES · VERSIONED EVIDENCE</span><h2>策略</h2><p>按策略版本比较全部信号、人工看好和实际买入的客观结果。</p></div><span className="workbench-as-of">共 {strategies.length} 个版本</span></div>
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取策略证据…</div> : strategies.length === 0 ? <p className="workbench-empty">暂无策略信号。</p> : <div className="workbench-strategy-grid">{strategies.map((strategy) => <article className="workbench-strategy-card" key={`${strategy.formula_id ?? 'none'}:${strategy.formula_version ?? 'none'}`}><div className="workbench-card-heading"><div><strong>{strategy.formula_name}</strong><span>{strategy.formula_version ?? '未版本化'}</span></div><b>{strategy.signal_count}<small>信号</small></b></div><div className="workbench-cohort-grid">{strategy.cohorts.map((cohort) => <div className="workbench-cohort" key={cohort.cohort}><span>{workbenchCohortLabel(cohort.cohort)}</span><strong>{cohort.sample_count}</strong><small>已解决 {cohort.resolved_count} · 排除 {cohort.excluded_count}</small>{cohort.horizons[0] && <small>T+{cohort.horizons[0].horizon_sessions} 平均 {formatPercent(cohort.horizons[0].average_return)}</small>}</div>)}</div></article>)}</div>}
        </>
      )}
      {route === 'analytics' && (
        <>
          <div className="workbench-section-heading"><div><span className="eyebrow">ANALYTICS · HUMAN VS EVIDENCE</span><h2>分析</h2><p>指标只来自已解决的客观表现；缺失样本保持为空。</p></div><span className="workbench-as-of">截至 {analytics ? formatManagementDate(analytics.as_of) : '—'}</span></div>
          {loading && loadedRoute !== route ? <div className="workbench-loading">正在读取分析…</div> : !analytics || analytics.cohorts.length === 0 ? <p className="workbench-empty">暂无可比较的策略证据。</p> : <div className="workbench-analytics-grid">{analytics.cohorts.map((cohort) => <article className="workbench-analytics-card" key={cohort.cohort}><div className="workbench-card-heading"><div><strong>{workbenchCohortLabel(cohort.cohort)}</strong><span>样本 {cohort.sample_count} · 已解决 {cohort.resolved_count}</span></div><span className="workbench-status workbench-status--neutral">只读</span></div><div className="workbench-analytics-horizons">{cohort.horizons.map((horizon) => <div key={horizon.horizon_sessions}><span>T+{horizon.horizon_sessions}</span><strong>{formatPercent(horizon.average_return)}</strong><small>{horizon.sample_count} 个表现样本</small></div>)}</div></article>)}</div>}
        </>
      )}
      {(route === 'positions' || route === 'trades') && (
        <details
          className="workbench-legacy-tools"
          onToggle={(event) => setLegacyToolsOpen(event.currentTarget.open)}
        >
          <summary>打开现有手工台账录入</summary>
          <p>交易写入仍保留在独立的手工台账流程中；这里不会把策略信号自动转换为订单。</p>
          {legacyToolsOpen && (
            <PortfolioWorkspace
              apiBase={apiBase}
              formatDate={formatManagementDate}
              formatPrice={formatPrice}
            />
          )}
        </details>
      )}
      {selectedSymbol && drawerLoading && <div className="stock-drawer-loading" role="status">正在读取 {selectedSymbol} 的证据…</div>}
      {selectedSymbol && drawerError && <div className="stock-drawer-loading stock-drawer-loading--error" role="alert">{drawerError}<button type="button" onClick={() => setSelectedSymbol(null)}>关闭</button></div>}
      {stockDetail && <StockDetailDrawer apiBase={apiBase} detail={stockDetail} selectedCandidateId={selectedCandidateId} onClose={closeStock} onAnnotationSaved={async () => { setReloadToken((value) => value + 1); await refreshStock() }} onTradeSaved={async () => { setReloadToken((value) => value + 1); await refreshStock() }} />}
    </section>
  )
}
