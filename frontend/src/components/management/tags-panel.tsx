import { useState } from 'react'
import { Archive, RotateCcw } from 'lucide-react'
import { requestJson } from '@/api/client'
import { reusableTagCategoryLabels } from '@/lib/labels'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import type { ManagedReusableTag } from '@/types'

export function ManagementTagsPanel({
  apiBase,
  tags,
  onChanged,
  onError,
}: {
  apiBase: string
  tags: ManagedReusableTag[]
  onChanged: () => Promise<void> | void
  onError: (message: string) => void
}) {
  const [tagEditId, setTagEditId] = useState<string | null>(null)
  const [tagEditLabel, setTagEditLabel] = useState('')
  const [tagBusyKey, setTagBusyKey] = useState<string | null>(null)

  function beginTagRename(tag: ManagedReusableTag) {
    setTagEditId(tag.tag_id)
    setTagEditLabel(tag.label)
    onError('')
  }

  async function renameManagedTag(tag: ManagedReusableTag) {
    const label = tagEditLabel.trim()
    if (!label || tagBusyKey !== null) {
      return
    }
    const key = `rename:${tag.tag_id}`
    setTagBusyKey(key)
    try {
      await requestJson<ManagedReusableTag>(
        `${apiBase}/v1/data-management/annotation-tags/${encodeURIComponent(tag.tag_id)}/rename`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label }),
        },
      )
      await onChanged()
      setTagEditId(null)
    } catch (reason: unknown) {
      onError(reason instanceof Error ? reason.message : '保存评审标签改名失败。')
    } finally {
      setTagBusyKey(null)
    }
  }

  async function changeManagedTagStatus(tag: ManagedReusableTag) {
    const action = tag.active ? 'archive' : 'restore'
    const key = `tags:${tag.tag_id}:${action}`
    setTagBusyKey(key)
    try {
      await requestJson<ManagedReusableTag>(
        `${apiBase}/v1/data-management/annotation-tags/${encodeURIComponent(tag.tag_id)}/${action}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: tag.active ? 'Web 评审标签管理操作' : 'Web 评审标签恢复' }),
        },
      )
      await onChanged()
    } catch (reason: unknown) {
      onError(reason instanceof Error ? reason.message : '更新评审标签状态失败。')
    } finally {
      setTagBusyKey(null)
    }
  }

  return (
    <Card className="management-section panel management-tags-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">评审标签目录</p>
          <h2>可复用标签</h2>
        </div>
        <Badge variant="outline">{tags.length} 项</Badge>
      </div>
      <p className="form-hint">改名和归档只追加生命周期事件；历史评审保留当时的标签快照。可复用标签不会自动进入机器学习数据。</p>
      {tags.length === 0 ? (
        <p className="empty-copy">还没有可复用标签。可以在评审表单中直接新建。</p>
      ) : (
        <div className="management-record-list">
          {tags.map((tag) => {
            const action = tag.active ? 'archive' : 'restore'
            const actionKey = `tags:${tag.tag_id}:${action}`
            const editing = tagEditId === tag.tag_id
            return (
              <article className="management-record" key={tag.tag_id}>
                <div className="management-record-heading">
                  <div>
                    <strong>{tag.label}</strong>
                    <span>{reusableTagCategoryLabels[tag.category]} · {tag.tag_id}</span>
                  </div>
                  <Badge variant={tag.active ? 'secondary' : 'outline'} className={`lifecycle-badge lifecycle-badge--${tag.active ? 'active' : 'archived'}`}>
                    {tag.active ? '启用' : '已归档'}
                  </Badge>
                </div>
                {editing ? (
                  <div className="management-tag-edit">
                    <Input
                      value={tagEditLabel}
                      onChange={(event) => setTagEditLabel(event.target.value)}
                      aria-label={`修改标签 ${tag.label}`}
                      autoFocus
                    />
                    <Button
                      type="button"
                      size="sm"
                      disabled={tagBusyKey !== null || !tagEditLabel.trim()}
                      data-saving={tagBusyKey === `rename:${tag.tag_id}`}
                      onClick={() => void renameManagedTag(tag)}
                    >
                      {tagBusyKey === `rename:${tag.tag_id}` ? '保存中…' : '保存改名'}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" disabled={tagBusyKey !== null} onClick={() => setTagEditId(null)}>取消</Button>
                  </div>
                ) : (
                  <div className="management-record-actions">
                    <Button type="button" size="sm" variant="outline" onClick={() => beginTagRename(tag)}>改名</Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={tagBusyKey !== null}
                      data-saving={tagBusyKey === actionKey}
                      onClick={() => void changeManagedTagStatus(tag)}
                    >
                      {tag.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                      {tagBusyKey === actionKey ? '处理中…' : tag.active ? '归档' : '恢复'}
                    </Button>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </Card>
  )
}
