import { useState } from 'react'
import type { FormEvent } from 'react'
import { requestJson } from '@/api/client'
import {
  manualSellReasonLabels,
  type Exchange,
  type ManualOrder,
  type ManualSellReason,
} from '@/api/contracts'
import { CalendarInput } from '@/components/calendar-input'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type ManualOrderEntryProps = {
  apiBase: string
  onSaved: (order: ManualOrder) => void | Promise<void>
}

function toShanghaiDateTime(value: string): string {
  return `${value}:00+08:00`
}

export function ManualOrderEntry({ apiBase, onSaved }: ManualOrderEntryProps) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [exchange, setExchange] = useState<Exchange>('SZ')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [orderedAt, setOrderedAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [quantity, setQuantity] = useState('100')
  const [limitPrice, setLimitPrice] = useState('')
  const [sellReason, setSellReason] = useState<ManualSellReason>('target_reached')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setErrorMessage(null)
    setSuccessMessage(null)
    try {
      const order = await requestJson<ManualOrder>(`${apiBase}/v1/manual-orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_id: crypto.randomUUID(),
          local_code: code,
          name: name.trim() || null,
          exchange,
          side,
          ordered_at: toShanghaiDateTime(orderedAt),
          quantity,
          limit_price: limitPrice || null,
          sell_reason: side === 'sell' ? sellReason : null,
          note: note.trim() || null,
        }),
      })
      await onSaved(order)
      setSuccessMessage(`已记录未成交订单：${order.security.symbol}，${order.quantity} 股。`)
      setCode('')
      setName('')
      setLimitPrice('')
      setNote('')
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '保存未成交订单失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="portfolio-entry-form portfolio-order-entry" onSubmit={submit} aria-busy={busy}>
      <div>
        <h3>记录未成交订单</h3>
        <p className="form-hint">只记录已提交但尚未确认成交的订单，不会自动生成成交。</p>
      </div>
      <div className="field">
        <Label htmlFor="manual-order-code">证券代码</Label>
        <Input id="manual-order-code" value={code} onChange={(event) => setCode(event.target.value)} placeholder="六位代码" required />
      </div>
      <div className="field">
        <Label htmlFor="manual-order-name">证券名称（可选）</Label>
        <Input id="manual-order-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 平安银行" />
      </div>
      <div className="field">
        <Label htmlFor="manual-order-exchange">交易所</Label>
        <select id="manual-order-exchange" className="native-select" value={exchange} onChange={(event) => setExchange(event.target.value as Exchange)}>
          <option value="SZ">SZ</option>
          <option value="SH">SH</option>
          <option value="BJ">BJ</option>
        </select>
      </div>
      <div className="field">
        <Label htmlFor="manual-order-side">交易方向</Label>
        <select id="manual-order-side" className="native-select" value={side} onChange={(event) => setSide(event.target.value as 'buy' | 'sell')}>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
      </div>
      {side === 'sell' && (
        <div className="field">
          <Label htmlFor="manual-order-sell-reason">卖出原因（必填）</Label>
          <select id="manual-order-sell-reason" className="native-select" value={sellReason} onChange={(event) => setSellReason(event.target.value as ManualSellReason)}>
            {(Object.keys(manualSellReasonLabels) as ManualSellReason[]).map((value) => (
              <option value={value} key={value}>{manualSellReasonLabels[value]}</option>
            ))}
          </select>
        </div>
      )}
      <div className="field">
        <Label htmlFor="manual-order-ordered-at">下单时间</Label>
        <CalendarInput id="manual-order-ordered-at" type="datetime-local" value={orderedAt} onChange={setOrderedAt} required />
      </div>
      <div className="field">
        <Label htmlFor="manual-order-quantity">委托数量</Label>
        <Input id="manual-order-quantity" type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} required />
      </div>
      <div className="field">
        <Label htmlFor="manual-order-limit-price">限价（可选）</Label>
        <Input id="manual-order-limit-price" type="number" min="0.0001" step="0.0001" value={limitPrice} onChange={(event) => setLimitPrice(event.target.value)} placeholder="市价或未填写" />
      </div>
      <div className="field">
        <Label htmlFor="manual-order-note">订单备注（可选）</Label>
        <Input id="manual-order-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录委托背景" />
      </div>
      {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
      {successMessage && <p className="portfolio-form-success" role="status">{successMessage}</p>}
      <Button type="submit" size="sm" disabled={busy}>{busy ? '保存中…' : '记录未成交订单'}</Button>
    </form>
  )
}
