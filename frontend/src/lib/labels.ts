import type { Exchange } from '@/api/contracts'
import type { Decision, AnnotationOutlook, AnnotationDecision, ReviewState, ReusableTagCategory, OutcomeStatus } from '@/types'


export const exchangeLabels: Record<Exchange, string> = {
  SH: '上海 SH',
  SZ: '深圳 SZ',
  BJ: '北京 BJ',
}

export const annotationOutlookLabels: Record<AnnotationOutlook, string> = {
  bullish: '看好',
  neutral: '一般',
  bearish: '不看好',
}

export const annotationDecisionLabels: Record<AnnotationDecision, string> = {
  discard: '放弃',
  watch: '继续观察',
  prepare_buy: '准备买入',
}

export const reusableTagCategoryLabels: Record<ReusableTagCategory, string> = {
  position: '位置',
  pattern: '形态',
  price_volume: '量价',
  sector: '板块环境',
}

export const annotationTagGroups: Array<{
  key: 'position_tags' | 'pattern_tags' | 'price_volume_tags' | 'sector_tags'
  reusableCategory: ReusableTagCategory
  label: string
  options: Array<[string, string]>
}> = [
  { key: 'position_tags', reusableCategory: 'position', label: '位置', options: [['low', '低位'], ['mid', '中位'], ['high', '高位'], ['support', '支撑附近'], ['resistance', '压力附近'], ['new_high', '阶段新高']] },
  { key: 'pattern_tags', reusableCategory: 'pattern', label: '形态', options: [['trend_continuation', '趋势延续'], ['breakout', '突破'], ['pullback', '回踩'], ['consolidation', '横盘整理'], ['reversal', '反转'], ['pattern_break', '形态破坏']] },
  { key: 'price_volume_tags', reusableCategory: 'price_volume', label: '量价', options: [['price_up_volume_up', '价涨量增'], ['price_up_volume_down', '价涨量缩'], ['price_down_volume_up', '价跌量增'], ['price_down_volume_down', '价跌量缩'], ['volume_breakout', '放量突破'], ['volume_pullback', '缩量回踩']] },
  { key: 'sector_tags', reusableCategory: 'sector', label: '板块环境', options: [['sector_strong', '板块强势'], ['sector_rotation', '板块轮动'], ['sector_leader', '板块龙头'], ['sector_following', '板块跟涨'], ['sector_weak', '板块弱势'], ['stock_independent', '个股独立']] },
]
export function outcomeLabel(status: OutcomeStatus): string {
  const labels: Record<OutcomeStatus, string> = {
    win: '胜',
    loss: '负',
    flat: '平',
    no_entry: '未入场',
    unresolved: '未解决',
  }
  return labels[status]
}
export function decisionLabel(decision: Decision): string {
  const labels: Record<Decision, string> = {
    buy_candidate: '候选',
    watch: '观察',
    pass: '放弃',
    invalid: '无效',
  }
  return labels[decision]
}
export function reviewStateLabel(state: ReviewState): string {
  const labels: Record<ReviewState, string> = {
    unreviewed: '未评审',
    discarded: '已放弃',
    watching: '观察中',
    preparing: '准备买入',
  }
  return labels[state]
}
