import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { requestJson } from '@/api/client'
import type {
  Exchange,
  ManualFill,
  ManualOrder,
  ManualSellReason,
  PortfolioPosition,
  PortfolioReview,
  SecurityMasterRecord,
  SecurityMasterResponse,
} from '@/api/contracts'
import { manualSellReasonLabels } from '@/api/contracts'
import { CalendarInput } from '@/components/calendar-input'
import { ManualFillEntry } from '@/components/manual-fill-entry'
import { ManualOrderEntry } from '@/components/manual-order-entry'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'

export type PortfolioWorkspaceProps = {
  apiBase: string
  formatDate: (value: string) => string
  formatPrice: (value: string | null) => string
}

function toShanghaiDateTime(value: string): string {
  return `${value}:00+08:00`
}

function numericQuantity(value: string): number {
  const quantity = Number(value)
  return Number.isFinite(quantity) ? quantity : 0
}

function formatQuantity(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(8)))
}

function filledQuantity(orderId: string, fills: ManualFill[]): number {
  return fills
    .filter((fill) => fill.order_id === orderId)
    .reduce((total, fill) => total + numericQuantity(fill.quantity), 0)
}

function orderProgress(order: ManualOrder, fills: ManualFill[]): string {
  const filled = filledQuantity(order.order_id, fills)
  const ordered = numericQuantity(order.quantity)
  if (filled <= 0) {
    return '未成交'
  }
  if (filled >= ordered) {
    return '已全部成交'
  }
  return `部分成交，剩余 ${formatQuantity(Math.max(0, ordered - filled))} 股`
}

function PortfolioErrorAlert({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <Alert variant="destructive" className="sticky-alert">
      <AlertDescription>{message}</AlertDescription>
      <Button type="button" variant="link" size="sm" onClick={onDismiss}>
        关闭
      </Button>
    </Alert>
  )
}

export function PortfolioWorkspace({ apiBase, formatDate, formatPrice }: PortfolioWorkspaceProps) {
  const [positions, setPositions] = useState<PortfolioPosition[]>([])
  const [orders, setOrders] = useState<ManualOrder[]>([])
  const [fills, setFills] = useState<ManualFill[]>([])
  const [reviews, setReviews] = useState<PortfolioReview[]>([])
  const [masterQuery, setMasterQuery] = useState('')
  const [masterEffectiveDate, setMasterEffectiveDate] = useState(new Date().toISOString().slice(0, 10))
  const [masterResults, setMasterResults] = useState<SecurityMasterRecord[]>([])
  const [masterBusy, setMasterBusy] = useState(false)
  const [tradeCode, setTradeCode] = useState('')
  const [tradeName, setTradeName] = useState('')
  const [tradeExchange, setTradeExchange] = useState<Exchange>('SZ')
  const [tradeSide, setTradeSide] = useState<'buy' | 'sell'>('buy')
  const [tradeQuantity, setTradeQuantity] = useState('100')
  const [tradePrice, setTradePrice] = useState('')
  const [tradeSellReason, setTradeSellReason] = useState<ManualSellReason>('target_reached')
  const [reviewSymbol, setReviewSymbol] = useState('')
  const [reviewNote, setReviewNote] = useState('')
  const [ledgerBusy, setLedgerBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      requestJson<PortfolioPosition[]>(`${apiBase}/v1/positions`, { signal: controller.signal }),
      requestJson<ManualOrder[]>(`${apiBase}/v1/manual-orders`, { signal: controller.signal }),
      requestJson<ManualFill[]>(`${apiBase}/v1/manual-fills`, { signal: controller.signal }),
      requestJson<PortfolioReview[]>(`${apiBase}/v1/portfolio-reviews`, { signal: controller.signal }),
    ])
      .then(([nextPositions, nextOrders, nextFills, nextReviews]) => {
        setPositions(nextPositions)
        setOrders(nextOrders)
        setFills(nextFills)
        setReviews(nextReviews)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载持仓台账失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [apiBase])

  async function reloadPositions() {
    const nextPositions = await requestJson<PortfolioPosition[]>(`${apiBase}/v1/positions`)
    setPositions(nextPositions)
  }

  async function searchMaster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMasterBusy(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        effective_date: masterEffectiveDate,
        known_at: new Date().toISOString(),
        query: masterQuery,
        limit: '20',
      })
      const response = await requestJson<SecurityMasterResponse>(`${apiBase}/v1/universe/master?${params.toString()}`)
      setMasterResults(response.results)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '搜索证券主数据失败。')
    } finally {
      setMasterBusy(false)
    }
  }

  async function createTrade(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLedgerBusy(true)
    setError(null)
    const timestamp = new Date().toISOString()
    try {
      const [order, fill] = await requestJson<[ManualOrder, ManualFill]>(`${apiBase}/v1/manual-trades/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_id: crypto.randomUUID(),
          fill_id: crypto.randomUUID(),
          local_code: tradeCode,
          name: tradeName.trim() || null,
          exchange: tradeExchange,
          side: tradeSide,
          ordered_at: timestamp,
          filled_at: timestamp,
          quantity: tradeQuantity,
          price: tradePrice,
          sell_reason: tradeSide === 'sell' ? tradeSellReason : null,
        }),
      })
      setOrders((current) => [...current, order])
      setFills((current) => [...current, fill])
      await reloadPositions()
      setTradeCode('')
      setTradeName('')
      setTradePrice('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存订单与成交失败。')
    } finally {
      setLedgerBusy(false)
    }
  }

  async function createReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLedgerBusy(true)
    setError(null)
    try {
      const review = await requestJson<PortfolioReview>(`${apiBase}/v1/portfolio-reviews`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          review_id: crypto.randomUUID(),
          symbol: reviewSymbol,
          reviewed_at: new Date().toISOString(),
          note: reviewNote,
        }),
      })
      setReviews((current) => [...current, review])
      setReviewSymbol('')
      setReviewNote('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '保存复盘失败。')
    } finally {
      setLedgerBusy(false)
    }
  }

  function appendOrder(order: ManualOrder) {
    setOrders((current) => [...current, order])
  }

  async function appendFill(fill: ManualFill) {
    setFills((current) => [...current, fill])
    await reloadPositions()
  }

  return (
    <section className="portfolio-workspace" aria-label="手工持仓台账工作区">
      <div className="workspace-intro">
        <div>
          <p className="eyebrow">手工台账 · 不连接券商</p>
          <h2>持仓与复盘</h2>
          <p>持仓由成交记录推导；订单、成交和复盘记录与模型证据分离。</p>
        </div>
        <Badge variant="secondary">只读汇总</Badge>
      </div>
      {error && <PortfolioErrorAlert message={error} onDismiss={() => setError(null)} />}
      <div className="portfolio-entry-grid">
        <form className="portfolio-entry-form" onSubmit={createTrade} aria-busy={ledgerBusy}>
          <h3>录入订单与成交</h3>
          <p className="form-hint">一次保存已确认的订单和成交，订单 ID 与成交 ID 由系统自动生成。</p>
          <div className="field">
            <Label htmlFor="trade-code">证券代码</Label>
            <Input id="trade-code" value={tradeCode} onChange={(event) => setTradeCode(event.target.value)} placeholder="六位代码" required />
          </div>
          <div className="field">
            <Label htmlFor="trade-name">证券名称（可选）</Label>
            <Input id="trade-name" value={tradeName} onChange={(event) => setTradeName(event.target.value)} placeholder="例如 平安银行" />
          </div>
          <div className="field">
            <Label htmlFor="trade-exchange">交易所</Label>
            <select id="trade-exchange" className="native-select" value={tradeExchange} onChange={(event) => setTradeExchange(event.target.value as Exchange)}>
              <option value="SZ">SZ</option><option value="SH">SH</option><option value="BJ">BJ</option>
            </select>
          </div>
          <div className="field">
            <Label htmlFor="trade-side">交易方向</Label>
            <select id="trade-side" className="native-select" value={tradeSide} onChange={(event) => setTradeSide(event.target.value as 'buy' | 'sell')}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
          </div>
          {tradeSide === 'sell' && (
            <div className="field">
              <Label htmlFor="trade-sell-reason">卖出原因</Label>
              <select id="trade-sell-reason" className="native-select" value={tradeSellReason} onChange={(event) => setTradeSellReason(event.target.value as ManualSellReason)}>
                {(Object.keys(manualSellReasonLabels) as ManualSellReason[]).map((value) => <option value={value} key={value}>{manualSellReasonLabels[value]}</option>)}
              </select>
            </div>
          )}
          <div className="field">
            <Label htmlFor="trade-quantity">成交数量</Label>
            <Input id="trade-quantity" type="number" min="1" step="1" value={tradeQuantity} onChange={(event) => setTradeQuantity(event.target.value)} required />
          </div>
          <div className="field">
            <Label htmlFor="trade-price">成交价</Label>
            <Input id="trade-price" type="number" min="0.0001" step="0.0001" value={tradePrice} onChange={(event) => setTradePrice(event.target.value)} required />
          </div>
          <Button type="submit" size="sm" disabled={ledgerBusy}>{ledgerBusy ? '保存中…' : '保存订单与成交'}</Button>
        </form>
        <ManualOrderEntry apiBase={apiBase} onSaved={appendOrder} />
        <ManualFillEntry apiBase={apiBase} orders={orders} fills={fills} onSaved={appendFill} />
        <form className="portfolio-entry-form" onSubmit={createReview} aria-busy={ledgerBusy}>
          <h3>添加复盘</h3>
          <div className="field">
            <Label htmlFor="review-symbol">证券标识</Label>
            <Input id="review-symbol" value={reviewSymbol} onChange={(event) => setReviewSymbol(event.target.value)} placeholder="代码.交易所" required />
          </div>
          <div className="field">
            <Label htmlFor="review-note">复盘内容</Label>
            <Input id="review-note" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} required />
          </div>
          <Button type="submit" size="sm" disabled={ledgerBusy}>{ledgerBusy ? '保存中…' : '保存复盘'}</Button>
        </form>
      </div>
      <Card className="panel portfolio-master-search">
        <div className="section-heading"><h3>证券主数据搜索</h3><Badge variant="outline">时间点查询</Badge></div>
        <form className="portfolio-search-form" onSubmit={searchMaster}>
          <Input value={masterQuery} onChange={(event) => setMasterQuery(event.target.value)} placeholder="代码、名称、行业或控制人" />
          <CalendarInput
            id="master-effective-date"
            value={masterEffectiveDate}
            onChange={setMasterEffectiveDate}
            required
          />
          <Button type="submit" size="sm" disabled={masterBusy}>{masterBusy ? '搜索中…' : '搜索'}</Button>
        </form>
        {masterResults.length > 0 && (
          <div className="portfolio-master-results">
            {masterResults.map((record) => (
              <div className="portfolio-ledger-row" key={record.symbol}>
                <strong>{record.symbol} · {record.name ?? '未提供名称'}</strong>
                <span>{record.industry ?? '行业未知'} · {record.area ?? '地区未知'} · {record.full_name ?? '全称未知'}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      {loading ? <Skeleton /> : (
        <>
          <div className="portfolio-position-grid">
            {positions.length === 0 ? <p className="empty-copy">暂无持仓。</p> : positions.map((position) => (
              <Card className="portfolio-position-card" key={position.security.symbol}>
                <strong>{position.security.symbol}</strong>
                <span>{position.security.name ?? '未提供名称'}</span>
                <div><span>数量</span><strong>{position.quantity}</strong></div>
                <div><span>平均成本</span><strong>{formatPrice(position.average_cost)}</strong></div>
                <div><span>投入金额</span><strong>{formatPrice(position.invested_notional)}</strong></div>
              </Card>
            ))}
          </div>
          <div className="portfolio-ledger-grid">
            <Card className="panel">
              <div className="section-heading"><h3>订单</h3><Badge variant="outline">{orders.length}</Badge></div>
              {orders.length === 0 ? <p className="empty-copy">暂无订单。</p> : orders.map((order) => (
                <div className="portfolio-ledger-row" key={order.order_id}>
                  <strong>{order.security.symbol} · {order.side === 'buy' ? '买入' : '卖出'} · {orderProgress(order, fills)}</strong>
                  <span>{order.quantity} 股 · 已成交 {formatQuantity(filledQuantity(order.order_id, fills))} 股 · {formatDate(order.ordered_at)}</span>
                </div>
              ))}
            </Card>
            <Card className="panel">
              <div className="section-heading"><h3>成交</h3><Badge variant="outline">{fills.length}</Badge></div>
              {fills.length === 0 ? <p className="empty-copy">暂无成交。</p> : fills.map((fill) => (
                <div className="portfolio-ledger-row" key={fill.fill_id}>
                  <strong>{fill.fill_id}</strong>
                  <span>{fill.quantity} 股 · {formatPrice(fill.price)} · {formatDate(fill.filled_at)}</span>
                </div>
              ))}
            </Card>
          </div>
          <Card className="panel">
            <div className="section-heading"><h3>复盘记录</h3><Badge variant="outline">{reviews.length}</Badge></div>
            {reviews.length === 0 ? <p className="empty-copy">暂无复盘记录。</p> : reviews.map((review) => (
              <div className="portfolio-ledger-row" key={review.review_id}>
                <strong>{review.symbol}</strong>
                <span>{review.note} · {formatDate(review.reviewed_at)}</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </section>
  )
}
