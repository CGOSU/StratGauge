import type { ManagementFilter, BatchSource, ManagementSourceFilter } from '@/types'


export function matchesManagementFilter(active: boolean, filter: ManagementFilter): boolean {
  return filter === 'all' || (filter === 'active' ? active : !active)
}

export function managementFilterLabel(filter: ManagementFilter): string {
  return filter === 'all' ? '全部' : filter === 'active' ? '启用中' : '已停用'
}

export function matchesSourceFilter(source: BatchSource, filter: ManagementSourceFilter): boolean {
  return filter === 'all' || source === filter
}

export function sourceFilterLabel(filter: ManagementSourceFilter): string {
  return filter === 'all' ? '全部' : sourceLabel(filter)
}

export function sourceLabel(source: BatchSource): string {
  return source === 'manual' ? '手工' : '通达信'
}
