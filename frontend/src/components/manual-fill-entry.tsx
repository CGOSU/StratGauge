import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { requestJson } from '@/api/client'
import type { ManualFill, ManualOrder } from '@/api/contracts'
import { CalendarInput } from '@/components/calendar-input'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type ManualFillEntryProps = {
  apiBase: string
  orders: ManualOrder[]
  fills: ManualFill[]
  onSaved: (fill: ManualFill) => void | Promise<void>
}

type RemainingOrder = {
  order: ManualOrder
  remaining: number
}

function toShanghaiDateTime(value: string): string {
  return `${value}:00+08:00`
}

function numericQuantity(value: string): number {
  const quantity = Number(value)
  return Number.isFinite(quantity) ? quantity : 0
}

function formatQuantity(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0'
  }
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(8)))
}

function remainingQuantity(order: ManualOrder, fills: ManualFill[]): number {
  const filled = fills
    .filter((fill) => fill.order_id === order.order_id)
    .reduce((total, fill) => total + numericQuantity(fill.quantity), 0)
  return Math.max(0, numericQuantity(order.quantity) - filled)
}

export function ManualFillEntry({ apiBase, orders, fills, onSaved }: ManualFillEntryProps) {
  const [selectedOrderId, setSelectedOrderId] = useState('')
  const [filledAt, setFilledAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [commission, setCommission] = useState('0')
  const [stampDuty, setStampDuty] = useState('0')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const availableOrders = useMemo<RemainingOrder[]>(
    () => orders
      .map((order) => ({ order, remaining: remainingQuantity(order, fills) }))
      .filter((item) => item.remaining > 0),
    [fills, orders],
  )
  const selectedOrder = availableOrders.find((item) => item.order.order_id === selectedOrderId) ?? null

  useEffect(() => {
    if (!selectedOrder || !availableOrders.some((item) => item.order.order_id === selectedOrderId)) {
      setSelectedOrderId(availableOrders[0]?.order.order_id ?? '')
    }
  }, [availableOrders, selectedOrder, selectedOrderId])

  useEffect(() => {
    setQuantity(selectedOrder ? formatQuantity(selectedOrder.remaining) : '')
  }, [selectedOrder?.order.order_id, selectedOrder?.remaining])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !selectedOrder) {
      return
    }
    const requestedQuantity = numericQuantity(quantity)
    if (requestedQuantity <= 0 || requestedQuantity > selectedOrder.remaining) {
      setErrorMessage(`成交数量必须大于 0 且不超过剩余 ${formatQuantity(selectedOrder.remaining)} 股。`)
      return
    }
    setBusy(true)
    setErrorMessage(null)
    setSuccessMessage(null)
    try {
      const fill = await requestJson<ManualFill>(`${apiBase}/v1/manual-fills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fill_id: crypto.randomUUID(),
          order_id: selectedOrder.order.order_id,
          filled_at: toShanghaiDateTime(filledAt),
          quantity,
          price,
          commission,
          stamp_duty: stampDuty,
          note: note.trim() || null,
        }),
      })
      await onSaved(fill)
      setSuccessMessage(`已追加成交：${fill.quantity} 股；该订单剩余 ${formatQuantity(selectedOrder.remaining - requestedQuantity)} 股。`)
      setPrice('')
      setCommission('0')
      setStampDuty('0')
      setNote('')
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '追加成交失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="portfolio-entry-form portfolio-fill-entry" onSubmit={submit} aria-busy={busy}>
      <div>
        <h3>追加部分成交</h3>
        <p className="form-hint">从已有订单追加独立成交事实；订单不会因创建而自动变成成交。</p>
      </div>
      {availableOrders.length === 0 ? (
        <p className="empty-copy">暂无可追加成交的未完成订单。</p>
      ) : (
        <>
          <div className="field">
            <Label htmlFor="manual-fill-order">选择订单</Label>
            <select id="manual-fill-order" className="native-select" value={selectedOrderId} onChange={(event) => setSelectedOrderId(event.target.value)} required>
              {availableOrders.map(({ order, remaining }) => (
                <option value={order.order_id} key={order.order_id}>
                  {order.security.symbol} · {order.side === 'buy' ? '买入' : '卖出'} · 剩余 {formatQuantity(remaining)} 股
                </option>
              ))}
            </select>
          </div>
          <p className="portfolio-fill-remaining">
            当前剩余未成交：<strong>{selectedOrder ? formatQuantity(selectedOrder.remaining) : '—'} 股</strong>；后端仍会校验累计数量和卖出持仓。
          </p>
          <div className="field">
            <Label htmlFor="manual-fill-filled-at">成交时间</Label>
            <CalendarInput id="manual-fill-filled-at" type="datetime-local" value={filledAt} onChange={setFilledAt} required />
          </div>
          <div className="field">
            <Label htmlFor="manual-fill-quantity">本次成交数量</Label>
            <Input id="manual-fill-quantity" type="number" min="1" step="1" max={selectedOrder ? formatQuantity(selectedOrder.remaining) : undefined} value={quantity} onChange={(event) => setQuantity(event.target.value)} required />
          </div>
          <div className="field">
            <Label htmlFor="manual-fill-price">成交价</Label>
            <Input id="manual-fill-price" type="number" min="0.0001" step="0.0001" value={price} onChange={(event) => setPrice(event.target.value)} required />
          </div>
          <div className="portfolio-fill-cost-grid">
            <div className="field">
              <Label htmlFor="manual-fill-commission">佣金</Label>
              <Input id="manual-fill-commission" type="number" min="0" step="0.01" value={commission} onChange={(event) => setCommission(event.target.value)} />
            </div>
            <div className="field">
              <Label htmlFor="manual-fill-stamp-duty">印花税</Label>
              <Input id="manual-fill-stamp-duty" type="number" min="0" step="0.01" value={stampDuty} onChange={(event) => setStampDuty(event.target.value)} />
            </div>
          </div>
          <div className="field">
            <Label htmlFor="manual-fill-note">成交备注（可选）</Label>
            <Input id="manual-fill-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录分批成交情况" />
          </div>
        </>
      )}
      {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
      {successMessage && <p className="portfolio-form-success" role="status">{successMessage}</p>}
      <Button type="submit" size="sm" disabled={busy || !selectedOrder}>{busy ? '保存中…' : '追加成交事实'}</Button>
    </form>
  )
}
