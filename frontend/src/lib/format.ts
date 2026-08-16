import type { Outcome, Performance, SelectionLabel, StatsSummary } from '@/types'


export function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string' || !result.includes(',')) {
        reject(new Error('无法读取导出文件。'))
        return
      }
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => reject(new Error('读取导出文件失败。'))
    reader.readAsDataURL(file)
  })
}
export function toShanghaiDateTime(value: string): string {
  return `${value}:00+08:00`
}
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const units = ['KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = -1
  do {
    size /= 1024
    unitIndex += 1
  } while (size >= 1024 && unitIndex < units.length - 1)
  return `${size.toFixed(unitIndex === 0 || size >= 10 ? 0 : 1)} ${units[unitIndex]}`
}
export function latestLabel(labels: SelectionLabel[]): SelectionLabel | null {
  return labels.length === 0 ? null : labels[labels.length - 1]
}

export function latestOutcome(outcomes: Outcome[]): Outcome | null {
  return outcomes.length === 0 ? null : outcomes[outcomes.length - 1]
}

export function latestPerformance(performances: Performance[]): Performance | null {
  return performances.length === 0 ? null : performances[performances.length - 1]
}
export function formatManagementDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}
export function formatWinRate(summary: StatsSummary): string {
  if (!summary.evidence_sufficient) {
    return `证据不足（${summary.completed}/${summary.minimum_evidence_samples}）`
  }
  return formatPercent(summary.win_rate)
}
export function formatPrice(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(2) : value
}
export function formatModelScore(value: string): string {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(4) : '—'
}
export function formatPercent(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const percentage = Number(value) * 100
  return Number.isFinite(percentage) ? `${percentage.toFixed(2)}%` : '—'
}
