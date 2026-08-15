import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { requestJson } from '@/api/client'
import type { MlReadiness } from '@/api/contracts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

const stageLabels: Record<MlReadiness['stage'], string> = {
  collecting_labels: '收集成熟标签',
  awaiting_feature_evidence: '等待特征证据',
  ready_for_training: '可以开始训练',
  model_registered: '模型已注册，待启用 shadow',
  shadow_ready: 'shadow 已就绪',
}

const outcomeLabels: Record<string, string> = {
  win: '胜',
  loss: '负',
  flat: '平',
  no_entry: '未入场',
  unresolved: '未解决',
  pending: '待成熟',
}

export function MlReadinessPanel({ apiBase }: { apiBase: string }) {
  const [data, setData] = useState<MlReadiness | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true)
      setError(null)
      try {
        const response = await requestJson<MlReadiness>(`${apiBase}/v1/ml/readiness`, { signal })
        setData(response)
      } catch (reason: unknown) {
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          return
        }
        setError(reason instanceof Error ? reason.message : '读取机器学习就绪状态失败。')
      } finally {
        if (!signal?.aborted) {
          setLoading(false)
        }
      }
    },
    [apiBase],
  )

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  const accepted = data?.accepted_label_count ?? 0
  const minimum = data?.minimum_labeled_samples ?? 100
  const progress = Math.min(100, minimum > 0 ? (accepted / minimum) * 100 : 0)

  return (
    <Card className="ml-readiness-panel" aria-labelledby="ml-readiness-title">
      <div className="ml-readiness-heading">
        <div>
          <p className="eyebrow">机器学习前置检查</p>
          <h2 id="ml-readiness-title">什么时候可以运行机器学习？</h2>
        </div>
        <div className="ml-readiness-actions">
          {data && <Badge variant={data.training_ready ? 'secondary' : 'outline'}>{stageLabels[data.stage]}</Badge>}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="secondary-button"
            disabled={loading}
            data-saving={loading}
            aria-label="刷新机器学习就绪状态"
            onClick={() => void load()}
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            {loading ? '检查中…' : '刷新状态'}
          </Button>
        </div>
      </div>

      {error ? (
        <div className="ml-readiness-error" role="alert">
          <p>{error}</p>
          <Button type="button" size="sm" variant="outline" onClick={() => void load()}>
            重试
          </Button>
        </div>
      ) : data ? (
        <>
          <div className="ml-readiness-summary" aria-live="polite">
            <div>
              <span>已成熟训练标签</span>
              <strong>{accepted} / {minimum}</strong>
            </div>
            <div>
              <span>还需</span>
              <strong>{data.label_shortfall} 条</strong>
            </div>
            <div>
              <span>候选与批次</span>
              <strong>{data.source_candidate_count} / {data.source_batch_count}</strong>
            </div>
            <div>
              <span>已注册 shadow</span>
              <strong>{data.shadow_model_count}</strong>
            </div>
          </div>
          <div className="ml-readiness-progress" aria-label={`已成熟训练标签 ${accepted} / ${minimum}`}>
            <div
              className="ml-readiness-progress-bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={minimum}
              aria-valuenow={Math.min(accepted, minimum)}
              style={{ width: `${progress}%` }}
            />
          </div>

          <div className="ml-readiness-details">
            <div>
              <span className="ml-readiness-detail-title">成熟结果分布</span>
              <div className="ml-readiness-outcomes">
                {Object.entries(data.outcome_counts).map(([status, count]) => (
                  <span key={status}>{outcomeLabels[status] ?? status} {count}</span>
                ))}
              </div>
              <small>
                可训练标签：success {data.target_counts.success ?? 0} · not_success {data.target_counts.not_success ?? 0}；未入场、未解决和待成熟不会计入训练。
              </small>
            </div>
            <div>
              <span className="ml-readiness-detail-title">证据覆盖</span>
              <small>
                特征快照 {data.feature_snapshot_count} · 基准 {data.benchmark_snapshot_count} · 复权因子 {data.adjustment_factor_snapshot_count}
              </small>
            </div>
          </div>

          {!data.training_ready && (
            <div className="ml-readiness-blocked" role="status">
              <strong>当前尚不能运行完整训练流水线</strong>
              <ul>
                {data.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
              </ul>
            </div>
          )}
          {data.training_ready && data.registered_model_count === 0 && (
            <div className="ml-readiness-ready" role="status">
              <strong>训练前置已满足</strong>
              <p>现在可以人工运行显式训练流水线；系统不会在 Web 请求中自动训练。</p>
            </div>
          )}

          <div className="ml-readiness-next-steps">
            <span className="ml-readiness-detail-title">下一步</span>
            <ul>
              {data.next_steps.map((step) => <li key={step}>{step}</li>)}
            </ul>
          </div>
          <p className="ml-readiness-disclaimer">
            模型注册、shadow 启用和后续评分都需要固定证据与人工操作；shadow 分数始终不用于交易决策。
          </p>
        </>
      ) : (
        <p className="ml-readiness-loading" role="status">正在读取就绪证据…</p>
      )}
    </Card>
  )
}
