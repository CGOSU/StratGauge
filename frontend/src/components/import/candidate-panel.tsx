import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { CandidateAnalysisPanel, CandidateModelScorePanel, RecommendationWorkbenchPanel } from '@/components/candidate-panels'
import { annotationDecisionLabels, annotationOutlookLabels, decisionLabel, outcomeLabel, reviewStateLabel } from '@/lib/labels'
import { formatManagementDate, formatPercent, formatWinRate, latestLabel, latestOutcome, latestPerformance } from '@/lib/format'
import type { ImportData } from '@/hooks/use-import-data'

export function CandidatePanel({ data }: { data: ImportData }) {
  const {
    selectedBatch,
    selectedBatchId,
    selectedStats,
    loadingBatch,
    recommendationWorkbench,
    recommendationLoading,
    loadRecommendationWorkbench,
    candidateAnalyses,
    analysisLoadingId,
    loadCandidateAnalysis,
    candidateModelScores,
    modelScoreLoadingId,
    loadCandidateModelScores,
  } = data
  return (
    <Card className="candidate-panel panel" aria-labelledby="candidate-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">03 · 候选</p>
            <h2 id="candidate-title">候选股票（只读）</h2>
          </div>
          <div className="section-heading-actions">
            {selectedBatch && (
              <span className="section-meta">
                <strong>{selectedBatch.batch.selection_date}</strong> · {selectedBatch.batch.formula_name}
              </span>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="secondary-button"
              disabled={!selectedBatchId || recommendationLoading}
              data-saving={recommendationLoading}
              aria-expanded={Boolean(recommendationWorkbench)}
              onClick={() => void loadRecommendationWorkbench()}
            >
              {recommendationLoading ? '加载中…' : '打开推荐工作台'}
            </Button>
          </div>
        </div>
        <p className="candidate-readonly-note">这里仅查看导入批次和历史证据。新的人工判断请进入“策略信号”，打开股票详情后使用“判断”Tab；旧标签仅保留为历史兼容数据，不再从此处新增或覆盖。</p>
        {recommendationWorkbench && <RecommendationWorkbenchPanel data={recommendationWorkbench} />}
        {selectedStats && !loadingBatch && (
          <div className="stats-strip" aria-label="批次统计">
            <div>
              <span>待成熟</span>
              <strong>{selectedStats.pending}</strong>
            </div>
            <div>
              <span>已完成</span>
              <strong>{selectedStats.summary.completed}</strong>
            </div>
            <div>
              <span>胜 / 负 / 平</span>
              <strong>
                {selectedStats.summary.wins} / {selectedStats.summary.losses} / {selectedStats.summary.flats}
              </strong>
            </div>
            <div>
              <span>完成样本胜率</span>
              <strong>{formatWinRate(selectedStats.summary)}</strong>
            </div>
          </div>
        )}
        {loadingBatch ? (
          <div className="candidate-skeleton" aria-label="候选加载中">
            <Skeleton />
            <Skeleton />
          </div>
        ) : !selectedBatch ? (
          <p className="empty-copy">选择一个批次后查看候选股票。</p>
        ) : (
          <div className="candidate-list">
            {selectedBatch.candidates.map((candidateView) => {
              const { candidate, labels, outcomes, performances } = candidateView
              const label = latestLabel(labels)
              const outcome = latestOutcome(outcomes)
              const performance = latestPerformance(performances)
              return (
                <article className="candidate-row" key={candidate.candidate_id}>
                  <div className="candidate-identity">
                    <strong>{candidate.symbol}</strong>
                    <span>{candidate.name ?? '未提供名称'}</span>
                  </div>
                  <div className="candidate-data">
                    {Object.entries(candidate.source_fields)
                      .filter(([key]) => !['代码', '名称', '市场'].includes(key))
                      .slice(0, 3)
                      .map(([key, value]) => (
                        <span key={key}>
                          {key} {value}
                        </span>
                      ))}
                  </div>
                  <div className="candidate-actions">
                    <div className="badge-row">
                      <Badge variant={candidateView.latest_annotation ? 'secondary' : 'outline'}>
                        {candidateView.latest_annotation
                          ? `${annotationDecisionLabels[candidateView.latest_annotation.decision]} · ${candidateView.latest_annotation.overall_score}分`
                          : label
                            ? `旧标签：${decisionLabel(label.decision)}`
                            : '未评审'}
                      </Badge>
                      {candidateView.latest_annotation && (
                        <Badge variant="outline">
                          {annotationOutlookLabels[candidateView.latest_annotation.outlook]}
                        </Badge>
                      )}
                      {candidateView.latest_annotation?.reusable_tag_snapshots.map((tag) => (
                        <Badge
                          key={tag.tag_id}
                          variant="outline"
                          className="reusable-tag-badge"
                          title={`历史快照：${tag.category}`}
                        >
                          {tag.label}
                        </Badge>
                      ))}
                      <Badge variant={candidateView.excluded ? 'outline' : 'secondary'}>
                        {candidateView.excluded ? '已排除' : reviewStateLabel(candidateView.review_state)}
                      </Badge>
                      <Badge variant={outcome ? 'secondary' : 'outline'} className={`outcome-badge outcome-badge--${outcome?.status ?? 'pending'}`}>
                        {outcome ? outcomeLabel(outcome.status) : '结果待跟踪'}
                      </Badge>
                    </div>
                    {performance && (
                      <div className="performance-strip" aria-label={`${candidate.symbol} 未来表现`}>
                        <span>未来表现</span>
                        {performance.points.map((point) => (
                          <span key={point.horizon_sessions}>
                            T+{point.horizon_sessions} {formatPercent(point.return_from_anchor)}
                            {point.target_achieved === true ? ' · 达标' : ''}
                          </span>
                        ))}
                      </div>
                    )}
                    {candidateView.annotations.some((annotation) => annotation.reusable_tag_snapshots.length > 0) && (
                      <div className="annotation-history" aria-label={`${candidate.symbol} 可复用标签历史`}>
                        <span className="annotation-history-title">标签历史</span>
                        {candidateView.annotations.map((annotation) => (
                          <div className="annotation-history-row" key={annotation.annotation_id}>
                            <span>{formatManagementDate(annotation.reviewed_at)}</span>
                            <div className="badge-row">
                              {annotation.reusable_tag_snapshots.map((tag) => (
                                <Badge
                                  key={tag.tag_id}
                                  variant="outline"
                                  className="reusable-tag-badge"
                                  title={`评审快照 · ${tag.category}`}
                                >
                                  {tag.label}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="candidate-analysis-actions">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="secondary-button"
                        disabled={analysisLoadingId !== null || modelScoreLoadingId !== null}
                        data-saving={analysisLoadingId === `${selectedBatchId}:${candidate.candidate_id}`}
                        aria-expanded={Boolean(candidateAnalyses[candidate.candidate_id])}
                        onClick={() => void loadCandidateAnalysis(candidate.candidate_id)}
                      >
                        {analysisLoadingId === `${selectedBatchId}:${candidate.candidate_id}` ? '分析中…' : '查看历史条件分析'}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="secondary-button"
                        disabled={analysisLoadingId !== null || modelScoreLoadingId !== null}
                        data-saving={modelScoreLoadingId === `${selectedBatchId}:${candidate.candidate_id}`}
                        aria-expanded={Boolean(candidateModelScores[candidate.candidate_id])}
                        onClick={() => void loadCandidateModelScores(candidate.candidate_id)}
                      >
                        {modelScoreLoadingId === `${selectedBatchId}:${candidate.candidate_id}` ? '加载中…' : '查看模型影子证据'}
                      </Button>
                      {candidateAnalyses[candidate.candidate_id] && (
                        <CandidateAnalysisPanel analysis={candidateAnalyses[candidate.candidate_id]} />
                      )}
                      {candidateModelScores[candidate.candidate_id] && (
                        <CandidateModelScorePanel data={candidateModelScores[candidate.candidate_id]} />
                      )}
                    </div>
                    <span className="candidate-readonly-hint">人工判断与交易记录请从策略信号股票详情进入。</span>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </Card>
  )
}
