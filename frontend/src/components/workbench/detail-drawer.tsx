import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { X } from 'lucide-react'
import { requestJson } from '@/api/client'
import { manualSellReasonLabels } from '@/api/contracts'
import type { ManualFill, ManualOrder, ManualSellReason } from '@/api/contracts'
import type { AnnotationOutlook, AnnotationDecision, BatchView, WorkbenchMarketEvidence, WorkbenchSignal, WorkbenchTimelineEvent, WorkbenchStockDetail, StockDrawerTab } from '@/types'
import { annotationOutlookLabels, annotationDecisionLabels, annotationTagGroups } from '@/lib/labels'
import { toShanghaiDateTime, formatManagementDate, formatPrice, formatPercent } from '@/lib/format'
import { workbenchReviewLabel } from '@/components/workbench/workspace'


export function workbenchEventLabel(eventType: WorkbenchTimelineEvent['event_type']): string {
  const labels: Record<WorkbenchTimelineEvent['event_type'], string> = {
    signal: '策略信号',
    annotation: '人工判断',
    exclusion: '候选排除',
    order: '订单',
    fill: '成交',
    review: '复盘',
    outcome: '策略结果',
    performance: '未来表现',
  }
  return labels[eventType]
}
export function WorkbenchMarketEvidenceView({ market }: { market: WorkbenchMarketEvidence }) {
  if (market.status !== 'available') {
    return (
      <div className="workbench-evidence-empty">
        <strong>行情证据{market.status === 'incomplete' ? '不完整' : '不可用'}</strong>
        <span>{market.reason ?? '没有已批准的市场快照，系统不填充价格。'}</span>
      </div>
    )
  }
  return (
    <div className="workbench-market-grid">
      <div><span>当前价</span><strong className="market-value">{formatPrice(market.close)}</strong></div>
      <div><span>涨跌幅</span><strong className={`market-value market-change ${Number(market.change_percent ?? 0) >= 0 ? 'market-profit' : 'market-loss'}`}>{formatPercent(market.change_percent)}</strong></div>
      <div><span>交易日</span><strong>{market.session_date ?? '—'}</strong></div>
      <div><span>快照</span><strong>{market.snapshot_id ?? '—'}</strong></div>
    </div>
  )
}
export function WorkbenchAnnotationPanel({
  apiBase,
  signal,
  onSaved,
}: {
  apiBase: string
  signal: WorkbenchSignal
  onSaved: () => void | Promise<void>
}) {
  const latest = signal.latest_annotation
  const [score, setScore] = useState(String(latest?.overall_score ?? 3))
  const [outlook, setOutlook] = useState<AnnotationOutlook>(latest?.outlook ?? 'neutral')
  const [decision, setDecision] = useState<AnnotationDecision>(latest?.decision ?? 'watch')
  const [tags, setTags] = useState<Record<'position_tags' | 'pattern_tags' | 'price_volume_tags' | 'sector_tags', string[]>>({
    position_tags: latest?.position_tags ?? [],
    pattern_tags: latest?.pattern_tags ?? [],
    price_volume_tags: latest?.price_volume_tags ?? [],
    sector_tags: latest?.sector_tags ?? [],
  })
  const [note, setNote] = useState(latest?.note ?? '')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    setScore(String(signal.latest_annotation?.overall_score ?? 3))
    setOutlook(signal.latest_annotation?.outlook ?? 'neutral')
    setDecision(signal.latest_annotation?.decision ?? 'watch')
    setTags({
      position_tags: signal.latest_annotation?.position_tags ?? [],
      pattern_tags: signal.latest_annotation?.pattern_tags ?? [],
      price_volume_tags: signal.latest_annotation?.price_volume_tags ?? [],
      sector_tags: signal.latest_annotation?.sector_tags ?? [],
    })
    setNote(signal.latest_annotation?.note ?? '')
    setErrorMessage(null)
  }, [signal.candidate.candidate_id, signal.latest_annotation?.annotation_id])

  function toggleTag(key: keyof typeof tags, value: string) {
    setTags((current) => ({
      ...current,
      [key]: current[key].includes(value) ? current[key].filter((item) => item !== value) : [...current[key], value],
    }))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setErrorMessage(null)
    try {
      await requestJson<BatchView>(
        `${apiBase}/v1/selection-batches/${encodeURIComponent(signal.batch.batch_id)}/annotations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            candidate_id: signal.candidate.candidate_id,
            reviewed_at: new Date().toISOString(),
            overall_score: Number(score),
            outlook,
            decision,
            position_tags: tags.position_tags,
            pattern_tags: tags.pattern_tags,
            price_volume_tags: tags.price_volume_tags,
            sector_tags: tags.sector_tags,
            reusable_tag_ids: [],
            note: note.trim() || null,
          }),
        },
      )
      await onSaved()
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '保存人工判断失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="drawer-judgment-panel">
      <div className="drawer-section-heading">
        <div>
          <span className="drawer-section-kicker">APPEND-ONLY ANNOTATION</span>
          <h3>快速判断</h3>
        </div>
        <span className="drawer-readonly-note">不覆盖历史</span>
      </div>
      {latest ? (
        <div className="drawer-latest-annotation">
          <span>上次判断 · {formatManagementDate(latest.reviewed_at)}</span>
          <strong>{'★'.repeat(latest.overall_score)}{'☆'.repeat(5 - latest.overall_score)} · {annotationOutlookLabels[latest.outlook]}</strong>
          <span>{annotationDecisionLabels[latest.decision]}{latest.note ? ` · ${latest.note}` : ''}</span>
        </div>
      ) : (
        <p className="drawer-muted-copy">尚未判断。未判断不等同于不看好。</p>
      )}
      <form className="drawer-annotation-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <legend>综合评分</legend>
          <div className="drawer-choice-row">
            {[1, 2, 3, 4, 5].map((value) => (
              <button type="button" className={`drawer-score-choice${Number(score) === value ? ' is-selected' : ''}`} key={value} onClick={() => setScore(String(value))}>
                {value}
              </button>
            ))}
          </div>
          <legend>判断</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            {(['bullish', 'neutral', 'bearish'] as AnnotationOutlook[]).map((value) => (
              <button type="button" className={`drawer-choice${outlook === value ? ' is-selected' : ''}`} key={value} onClick={() => setOutlook(value)}>
                {annotationOutlookLabels[value]}
              </button>
            ))}
          </div>
          <legend>操作意图</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            {(['discard', 'watch', 'prepare_buy'] as AnnotationDecision[]).map((value) => (
              <button type="button" className={`drawer-choice drawer-choice--${value}${decision === value ? ' is-selected' : ''}`} key={value} onClick={() => setDecision(value)}>
                {annotationDecisionLabels[value]}
              </button>
            ))}
          </div>
          <div className="drawer-tag-groups">
            {annotationTagGroups.map((group) => (
              <div className="drawer-tag-group" key={group.key}>
                <span>{group.label}</span>
                <div>
                  {group.options.map(([value, label]) => (
                    <button type="button" className={tags[group.key].includes(value) ? 'is-selected' : ''} key={value} onClick={() => toggleTag(group.key, value)}>{label}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <label className="drawer-field">
            <span>备注（可选）</span>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录这次判断的依据" rows={3} />
          </label>
        </fieldset>
        {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
        <button type="submit" className="drawer-primary-action" disabled={busy}>
          {busy ? '追加中…' : '追加本次判断'}
        </button>
      </form>
    </div>
  )
}
export function WorkbenchTradePanel({
  apiBase,
  signal,
  onSaved,
}: {
  apiBase: string
  signal: WorkbenchSignal
  onSaved: () => void | Promise<void>
}) {
  const [side, setSide] = useState<'buy' | 'sell'>(signal.confirmed_buy ? 'sell' : 'buy')
  const [orderId, setOrderId] = useState(() => crypto.randomUUID())
  const [fillId, setFillId] = useState(() => crypto.randomUUID())
  const [orderedAt, setOrderedAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [filledAt, setFilledAt] = useState(() => new Date().toISOString().slice(0, 16))
  const [quantity, setQuantity] = useState('100')
  const [price, setPrice] = useState(signal.market.close ?? '')
  const [sellReason, setSellReason] = useState<ManualSellReason>('target_reached')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    setSide(signal.confirmed_buy ? 'sell' : 'buy')
    setPrice(signal.market.close ?? '')
    setErrorMessage(null)
  }, [signal.candidate.candidate_id, signal.confirmed_buy, signal.market.close])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setErrorMessage(null)
    const commonBody = {
      order_id: orderId,
      fill_id: fillId,
      local_code: signal.candidate.local_code,
      exchange: signal.candidate.exchange,
      name: signal.candidate.name,
      ordered_at: toShanghaiDateTime(orderedAt),
      filled_at: toShanghaiDateTime(filledAt),
      quantity,
      price,
      selection_candidate_id: signal.candidate.candidate_id,
      note: note.trim() || null,
    }
    try {
      if (side === 'buy') {
        await requestJson<[ManualOrder, ManualFill]>(`${apiBase}/v1/selection-candidate-trades`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(commonBody),
        })
      } else {
        await requestJson<[ManualOrder, ManualFill]>(`${apiBase}/v1/manual-trades/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...commonBody, side, sell_reason: sellReason }),
        })
      }
      setOrderId(crypto.randomUUID())
      setFillId(crypto.randomUUID())
      setNote('')
      await onSaved()
    } catch (reason: unknown) {
      setErrorMessage(reason instanceof Error ? reason.message : '保存确认交易失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="drawer-trade-panel">
      <div className="drawer-section-heading">
        <div>
          <span className="drawer-section-kicker">MANUAL EXECUTION</span>
          <h3>确认交易</h3>
        </div>
        <span className="drawer-readonly-note">不自动下单</span>
      </div>
      <p className="drawer-muted-copy">这里只记录用户已经确认的真实事实，不生成推荐价格。成交价格必须由用户填写。</p>
      <form className="drawer-trade-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <legend>方向</legend>
          <div className="drawer-choice-row drawer-choice-row--wide">
            <button type="button" className={`drawer-choice drawer-choice--buy${side === 'buy' ? ' is-selected' : ''}`} disabled={signal.confirmed_buy} onClick={() => setSide('buy')}>确认买入</button>
            <button type="button" className={`drawer-choice drawer-choice--sell${side === 'sell' ? ' is-selected' : ''}`} onClick={() => setSide('sell')}>确认卖出</button>
          </div>
          {side === 'sell' && (
            <label className="drawer-field">
              <span>卖出原因（必填）</span>
              <select value={sellReason} onChange={(event) => setSellReason(event.target.value as ManualSellReason)}>
                {(Object.keys(manualSellReasonLabels) as ManualSellReason[]).map((value) => <option value={value} key={value}>{manualSellReasonLabels[value]}</option>)}
              </select>
            </label>
          )}
          <div className="drawer-trade-grid">
            <label className="drawer-field"><span>数量</span><input type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
            <label className="drawer-field"><span>成交价</span><input type="number" min="0.0001" step="0.0001" value={price} onChange={(event) => setPrice(event.target.value)} placeholder="必须人工填写" required /></label>
            <label className="drawer-field"><span>下单时间</span><input type="datetime-local" value={orderedAt} onChange={(event) => setOrderedAt(event.target.value)} required /></label>
            <label className="drawer-field"><span>成交时间</span><input type="datetime-local" value={filledAt} onChange={(event) => setFilledAt(event.target.value)} required /></label>
          </div>
          <label className="drawer-field"><span>交易备注（可选）</span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} placeholder="记录交易事实或复盘线索" /></label>
        </fieldset>
        {errorMessage && <p className="drawer-form-error" role="alert">{errorMessage}</p>}
        <button type="submit" className="drawer-primary-action drawer-primary-action--trade" disabled={busy || (side === 'buy' && signal.confirmed_buy)}>{busy ? '保存中…' : side === 'buy' ? '确认并记录买入' : '确认并记录卖出'}</button>
      </form>
    </div>
  )
}
export function StockDetailDrawer({
  apiBase,
  detail,
  onClose,
  onAnnotationSaved,
  onTradeSaved,
}: {
  apiBase: string
  detail: WorkbenchStockDetail
  onClose: () => void
  onAnnotationSaved: () => void | Promise<void>
  onTradeSaved: () => void | Promise<void>
}) {
  const [tab, setTab] = useState<StockDrawerTab>('market')
  const signal = detail.signals[0]

  useEffect(() => {
    setTab('market')
  }, [detail.symbol])

  return (
    <div className="stock-drawer-layer" role="presentation">
      <button type="button" className="stock-drawer-backdrop" aria-label="关闭股票详情" onClick={onClose} />
      <aside className="stock-detail-drawer" role="dialog" aria-modal="true" aria-label={`${detail.symbol} 股票详情`}>
        <header className="stock-drawer-header">
          <div>
            <span className="drawer-symbol">{detail.symbol}</span>
            <h2>{detail.name ?? '未提供名称'}</h2>
            <span className="drawer-subtitle">截至 {formatManagementDate(detail.as_of)}</span>
          </div>
          <button type="button" className="drawer-close-button" aria-label="关闭股票详情" onClick={onClose}><X aria-hidden="true" /></button>
        </header>
        <div className="stock-drawer-market-summary">
          <WorkbenchMarketEvidenceView market={detail.market} />
        </div>
        <nav className="stock-drawer-tabs" aria-label="股票详情标签">
          {([
            ['market', '行情'],
            ['judgment', '判断'],
            ['signal', '信号'],
            ['trade', '交易'],
            ['analysis', '分析'],
          ] as Array<[StockDrawerTab, string]>).map(([value, label]) => (
            <button type="button" className={tab === value ? 'is-active' : ''} key={value} onClick={() => setTab(value)}>{label}</button>
          ))}
        </nav>
        <div className="stock-drawer-body">
          {tab === 'market' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-market-title">
              <div className="drawer-section-heading"><h3 id="drawer-market-title">批准行情证据</h3><span>{detail.market.provider ?? '本地快照'}</span></div>
              <WorkbenchMarketEvidenceView market={detail.market} />
              <p className="drawer-muted-copy">行情仅读取已批准的 market_daily 快照；缺失时不伪造价格、K 线或指标。</p>
            </section>
          )}
          {tab === 'judgment' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-judgment-title">
              <WorkbenchAnnotationPanel apiBase={apiBase} signal={signal} onSaved={onAnnotationSaved} />
            </section>
          )}
          {tab === 'signal' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-signal-title">
              <div className="drawer-section-heading"><h3 id="drawer-signal-title">策略信号历史</h3><span>{detail.signals.length} 次命中</span></div>
              <div className="drawer-signal-list">
                {detail.signals.map((item) => (
                  <article className="drawer-signal-item" key={item.candidate.candidate_id}>
                    <div><strong>{item.batch.formula_name}{item.batch.formula_version ? ` · ${item.batch.formula_version}` : ''}</strong><span>{item.batch.selection_date} · {item.batch.source === 'manual' ? '手工' : '通达信'}</span></div>
                    <div className="drawer-signal-status"><span className={`workbench-status workbench-status--${item.review_state}`}>{workbenchReviewLabel(item.review_state)}</span>{item.confirmed_buy && <span className="workbench-status workbench-status--bought">已买入</span>}</div>
                  </article>
                ))}
              </div>
            </section>
          )}
          {tab === 'trade' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-trade-title">
              <div className="drawer-section-heading"><h3 id="drawer-trade-title">交易与生命周期</h3><span>{signal.confirmed_buy ? '存在买入证据' : '尚无关联买入'}</span></div>
              <WorkbenchTradePanel apiBase={apiBase} signal={signal} onSaved={onTradeSaved} />
              <div className="drawer-timeline">
                {detail.timeline.filter((event) => ['order', 'fill', 'review'].includes(event.event_type)).length === 0 ? <p className="drawer-muted-copy">暂无真实交易或复盘证据。</p> : detail.timeline.filter((event) => ['order', 'fill', 'review'].includes(event.event_type)).map((event) => (
                  <article className="drawer-timeline-item" key={event.event_id}><span className="drawer-timeline-marker" /><div><span>{formatManagementDate(event.occurred_at)} · {workbenchEventLabel(event.event_type)}</span><strong>{event.title}</strong><p>{event.detail}</p></div></article>
                ))}
              </div>
            </section>
          )}
          {tab === 'analysis' && (
            <section className="drawer-tab-panel" aria-labelledby="drawer-analysis-title">
              <div className="drawer-section-heading"><h3 id="drawer-analysis-title">客观结果</h3><span>只读证据</span></div>
              {detail.signals.map((item) => (
                <article className="drawer-analysis-block" key={item.candidate.candidate_id}>
                  <strong>{item.batch.selection_date} · {item.batch.formula_name}</strong>
                  {item.performances.length === 0 && item.outcomes.length === 0 ? <p className="drawer-muted-copy">尚无完成的未来表现或结果。</p> : (
                    <div className="drawer-analysis-metrics">
                      {item.performances.flatMap((performance) => performance.points).map((point) => <span key={`${item.candidate.candidate_id}:${point.horizon_sessions}`}>T+{point.horizon_sessions} {formatPercent(point.return_from_anchor)}</span>)}
                      {item.outcomes.map((outcome) => <span key={`${item.candidate.candidate_id}:${outcome.evaluated_at}`}>{outcome.status} {formatPercent(outcome.net_return)}</span>)}
                    </div>
                  )}
                </article>
              ))}
              <p className="drawer-muted-copy">模型概率、推荐和自动动作不会在此被推断；只有已保存的证据才会展示。</p>
            </section>
          )}
          <section className="drawer-timeline-section" aria-labelledby="drawer-timeline-title">
            <div className="drawer-section-heading"><h3 id="drawer-timeline-title">完整时间线</h3><span>{detail.timeline.length} 个事件</span></div>
            <div className="drawer-timeline">
              {detail.timeline.length === 0 ? <p className="drawer-muted-copy">暂无时间线事件。</p> : detail.timeline.map((event) => (
                <article className="drawer-timeline-item" key={event.event_id}><span className="drawer-timeline-marker" /><div><span>{formatManagementDate(event.occurred_at)} · {workbenchEventLabel(event.event_type)}</span><strong>{event.title}</strong><p>{event.detail}</p></div></article>
              ))}
            </div>
          </section>
        </div>
      </aside>
    </div>
  )
}
