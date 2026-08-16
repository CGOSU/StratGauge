import { Badge } from '@/components/ui/badge'
import type { CandidateAnalysis, CandidateModelScores, RecommendationWorkbench } from '@/types'
import { decisionLabel } from '@/lib/labels'
import { formatManagementDate, formatWinRate, formatPrice, formatModelScore } from '@/lib/format'


export function CandidateAnalysisPanel({ analysis }: { analysis: CandidateAnalysis }) {
  const stats = analysis.conditional_stats
  const prices = analysis.entry_price_stats
  return (
    <section className="candidate-analysis" aria-label={`${analysis.symbol} 历史条件分析`}>
      <div className="candidate-analysis-heading">
        <strong>历史条件分析</strong>
        <span>{analysis.tracking_policy_id ?? '尚无可用结果政策'}</span>
      </div>
      <div className="candidate-analysis-grid">
        <div>
          <span>同公式历史完成胜率</span>
          <strong>{formatWinRate(stats)}</strong>
        </div>
        <div>
          <span>完成样本</span>
          <strong>{stats.completed}</strong>
        </div>
        <div>
          <span>同公式历史入场价</span>
          <strong>
            {prices.sample_count ? `${formatPrice(prices.minimum)} – ${formatPrice(prices.maximum)}` : '—'}
          </strong>
        </div>
        <div>
          <span>入场价中位数</span>
          <strong>{formatPrice(prices.median)}</strong>
        </div>
      </div>
      <p>
        参考 {formatManagementDate(analysis.reference_at)} · {analysis.cohort_batch_count} 个历史批次、
        {analysis.cohort_candidate_count} 个历史候选；入场价仅为历史实际成交假设的描述统计，不是买入建议。
      </p>
    </section>
  )
}
export function CandidateModelScorePanel({ data }: { data: CandidateModelScores }) {
  return (
    <section className="candidate-model-scores" aria-label={`${data.symbol} 模型影子证据`}>
      <div className="candidate-analysis-heading">
        <strong>模型影子证据</strong>
        <span>只读 · 不用于决策</span>
      </div>
      {data.scores.length === 0 && data.failures.length === 0 && (
        <p className="empty-copy">暂无影子评分。</p>
      )}
      {data.scores.map((score) => (
        <div className="candidate-model-score" key={`${score.model_version_id}:${score.scored_at}`}>
          <div>
            <span>{score.model_version_id} · {score.model_type}</span>
            <strong>{score.calibrated ? '已校准分数' : '未校准分数'}：{formatModelScore(score.calibrated_model_score ?? score.uncalibrated_model_score)}</strong>
          </div>
          <div>
            <span>样本量</span>
            <strong>{score.sample_count ?? '—'}</strong>
          </div>
          <div>
            <span>适用日期</span>
            <strong>
              {score.applicable_date_start && score.applicable_date_end
                ? `${score.applicable_date_start} – ${score.applicable_date_end}`
                : '—'}
            </strong>
          </div>
          <p>政策 {score.tracking_policy_id} · 评分时间 {formatManagementDate(score.scored_at)} · authoritative=false</p>
        </div>
      ))}
      {data.failures.map((failure) => (
        <p className="candidate-model-score-failure" key={failure.model_version_id}>
          {failure.model_version_id}：{failure.reason}
        </p>
      ))}
    </section>
  )
}
export function RecommendationWorkbenchPanel({ data }: { data: RecommendationWorkbench }) {
  return (
    <section className="recommendation-workbench" aria-label="每日推荐工作台">
      <div className="section-heading">
        <div>
          <p className="eyebrow">推荐证据</p>
          <h3>每日推荐工作台</h3>
        </div>
        <Badge variant="outline">仅人工确认</Badge>
      </div>
      <p className="workbench-disclaimer">
        这是质量、历史统计和模型影子证据的只读汇总，不生成推荐价、订单或自动交易动作。
      </p>
      <div className="recommendation-list">
        {data.candidates.map((candidate) => (
          <article className="recommendation-item" key={candidate.candidate_id}>
            <div className="recommendation-item-heading">
              <strong>{candidate.symbol}</strong>
              <Badge variant={candidate.state === 'reviewable' ? 'secondary' : 'outline'}>
                {candidate.state === 'reviewable' ? '可人工复核' : '证据阻断'}
              </Badge>
            </div>
            <div className="recommendation-evidence-grid">
              <div>
                <span>质量状态</span>
                <strong>{candidate.quality_state}</strong>
              </div>
              <div>
                <span>历史完成样本</span>
                <strong>{candidate.historical_stats?.completed ?? '—'}</strong>
              </div>
              <div>
                <span>历史胜率</span>
                <strong>
                  {candidate.historical_stats ? formatWinRate(candidate.historical_stats) : '—'}
                </strong>
              </div>
              <div>
                <span>模型影子分数</span>
                <strong>
                  {candidate.model_score
                    ? formatModelScore(candidate.model_score.calibrated_model_score ?? candidate.model_score.uncalibrated_model_score)
                    : '—'}
                </strong>
              </div>
            </div>
            <p className="workbench-evidence-note">
              {candidate.model_score
                ? `${candidate.model_score.calibrated ? '已校准' : '未校准'} · ${candidate.model_score.model_version_id} · authoritative=false`
                : '暂无模型影子评分'}
            </p>
            {candidate.blocking_reasons.length > 0 && (
              <p className="workbench-blocking-reasons">
                阻断：{candidate.blocking_reasons.join('、')}
              </p>
            )}
            <p className="workbench-evidence-note">
              人工状态：{candidate.manual_decision ? decisionLabel(candidate.manual_decision) : '未确认'}
            </p>
          </article>
        ))}
      </div>
    </section>
  )
}
