import { Button } from '@/components/ui/button'


export function PaginationControls({
  page,
  pageSize,
  total,
  busy,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  busy: boolean
  onPageChange: (page: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  if (pageCount <= 1) {
    return null
  }
  return (
    <div className="pagination-controls" aria-label="分页导航">
      <Button type="button" size="sm" variant="outline" disabled={busy || page <= 1} onClick={() => onPageChange(page - 1)}>
        上一页
      </Button>
      <span aria-live="polite">第 {page} / {pageCount} 页 · 共 {total} 条</span>
      <Button type="button" size="sm" variant="outline" disabled={busy || page >= pageCount} onClick={() => onPageChange(page + 1)}>
        下一页
      </Button>
    </div>
  )
}
