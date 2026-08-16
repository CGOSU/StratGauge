import { useState } from 'react'
import type { FormEvent } from 'react'
import { Archive, Pencil, Plus, RotateCcw } from 'lucide-react'
import { requestJson } from '@/api/client'
import { formatManagementDate } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { Formula, ManagedFormula } from '@/types'

export function ManagementFormulaPanel({
  apiBase,
  formulas,
  busyKey,
  onChanged,
  onFormulaCreated,
  onChangeStatus,
  onError,
}: {
  apiBase: string
  formulas: ManagedFormula[]
  busyKey: string | null
  onChanged: () => Promise<void> | void
  onFormulaCreated: () => void
  onChangeStatus: (entity: 'formulas' | 'batches', entityId: string, active: boolean) => Promise<void>
  onError: (message: string) => void
}) {
  const [editingFormula, setEditingFormula] = useState<ManagedFormula | null>(null)
  const [formulaDialogOpen, setFormulaDialogOpen] = useState(false)
  const [formulaBusy, setFormulaBusy] = useState(false)
  const [newFormulaName, setNewFormulaName] = useState('')
  const [newFormulaVersion, setNewFormulaVersion] = useState('')
  const [newFormulaDescription, setNewFormulaDescription] = useState('')
  const [editFormulaName, setEditFormulaName] = useState('')
  const [editFormulaVersion, setEditFormulaVersion] = useState('')
  const [editFormulaDescription, setEditFormulaDescription] = useState('')
  const [editFormulaBusy, setEditFormulaBusy] = useState(false)

  function beginFormulaRevision(item: ManagedFormula) {
    setEditingFormula(item)
    setEditFormulaName(item.formula.name)
    setEditFormulaVersion(item.formula.version ?? '')
    setEditFormulaDescription(item.formula.description ?? '')
    onError('')
  }

  async function reviseFormula(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editingFormula || !editFormulaName.trim()) {
      return
    }
    const nextName = editFormulaName.trim()
    const nextVersion = editFormulaVersion.trim()
    const nextDescription = editFormulaDescription.trim() || null
    if (
      nextName === editingFormula.formula.name &&
      nextVersion === (editingFormula.formula.version ?? '')
    ) {
      onError('公式版本不可原地编辑，请修改名称或版本号后保存为新版本。')
      return
    }
    setEditFormulaBusy(true)
    try {
      await requestJson<{ formula: Formula }>(`${apiBase}/v1/formulas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: nextName,
          version: nextVersion || null,
          description: nextDescription,
        }),
      })
      await onChanged()
      setEditingFormula(null)
    } catch (reason: unknown) {
      onError(reason instanceof Error ? reason.message : '保存公式新版本失败。')
    } finally {
      setEditFormulaBusy(false)
    }
  }

  async function createManagedFormula(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!newFormulaName.trim()) {
      return
    }
    setFormulaBusy(true)
    try {
      await requestJson<{ formula: Formula }>(`${apiBase}/v1/formulas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newFormulaName.trim(),
          version: newFormulaVersion.trim() || null,
          description: newFormulaDescription.trim() || null,
        }),
      })
      await onChanged()
      onFormulaCreated()
      setNewFormulaName('')
      setNewFormulaVersion('')
      setNewFormulaDescription('')
      setFormulaDialogOpen(false)
    } catch (reason: unknown) {
      onError(reason instanceof Error ? reason.message : '新建选股公式失败。')
    } finally {
      setFormulaBusy(false)
    }
  }

  return (
    <>
      <Card className="management-section panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">公式目录</p>
            <h2>手动创建的公式</h2>
          </div>
          <div className="management-badge-group">
            <Badge variant="outline">{formulas.length} 项</Badge>
            <Dialog
              open={formulaDialogOpen}
              onOpenChange={(open) => {
                if (!open && formulaBusy) {
                  return
                }
                setFormulaDialogOpen(open)
              }}
            >
              <DialogTrigger asChild>
                <Button type="button" size="sm" variant="outline">
                  <Plus className="h-4 w-4" />
                  新建公式
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>新建选股公式</DialogTitle>
                  <DialogDescription>只保存公式名称和版本留痕，公式仍由通达信执行。</DialogDescription>
                </DialogHeader>
                <form className="dialog-form" onSubmit={createManagedFormula}>
                  <div className="field">
                    <Label htmlFor="management-new-formula-name">公式名称</Label>
                    <Input
                      id="management-new-formula-name"
                      value={newFormulaName}
                      onChange={(event) => setNewFormulaName(event.target.value)}
                      required
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="management-new-formula-version">公式版本（可选）</Label>
                    <Input
                      id="management-new-formula-version"
                      value={newFormulaVersion}
                      onChange={(event) => setNewFormulaVersion(event.target.value)}
                      placeholder="例如 v1"
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="management-new-formula-description">说明（可选）</Label>
                    <Input
                      id="management-new-formula-description"
                      value={newFormulaDescription}
                      onChange={(event) => setNewFormulaDescription(event.target.value)}
                    />
                  </div>
                  <DialogFooter>
                    <Button type="submit" disabled={formulaBusy || !newFormulaName.trim()}>
                      {formulaBusy ? '创建中…' : '创建公式'}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </div>
        </div>
        {formulas.length === 0 ? (
          <p className="empty-copy">当前筛选下没有公式。</p>
        ) : (
          <div className="management-record-list">
            {formulas.map((item) => {
              const action = item.active ? 'archive' : 'restore'
              const actionKey = `formulas:${item.formula.formula_id}:${action}`
              return (
                <article className="management-record" key={item.formula.formula_id}>
                  <div className="management-record-heading">
                    <div>
                      <strong>{item.formula.name}</strong>
                      <span>{item.formula.version ?? '未填写版本'}</span>
                    </div>
                    <Badge
                      variant={item.active ? 'secondary' : 'outline'}
                      className={`lifecycle-badge lifecycle-badge--${item.active ? 'active' : 'archived'}`}
                    >
                      {item.active ? '启用' : '已停用'}
                    </Badge>
                  </div>
                  <p>{item.formula.description ?? '未填写说明'}</p>
                  <div className="management-record-meta">
                    <span>创建于 {formatManagementDate(item.formula.created_at)}</span>
                    <span>生命周期事件 {item.lifecycle.length} 条</span>
                  </div>
                  <div className="management-record-actions">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={editFormulaBusy}
                      onClick={() => beginFormulaRevision(item)}
                    >
                      <Pencil className="h-4 w-4" />
                      编辑为新版本
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busyKey !== null}
                      onClick={() => void onChangeStatus('formulas', item.formula.formula_id, item.active)}
                    >
                      {item.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                      {busyKey === actionKey ? '处理中…' : item.active ? '停用公式' : '恢复公式'}
                    </Button>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </Card>

      <Dialog
        open={editingFormula !== null}
        onOpenChange={(open) => {
          if (!open && !editFormulaBusy) {
            setEditingFormula(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑为新版本</DialogTitle>
            <DialogDescription>
              公式目录采用不可变版本。修改名称或版本号后保存，历史批次仍指向原公式。
            </DialogDescription>
          </DialogHeader>
          <form className="dialog-form" onSubmit={reviseFormula}>
            <div className="field">
              <Label htmlFor="edit-formula-name">公式名称</Label>
              <Input
                id="edit-formula-name"
                value={editFormulaName}
                onChange={(event) => setEditFormulaName(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <Label htmlFor="edit-formula-version">公式版本</Label>
              <Input
                id="edit-formula-version"
                value={editFormulaVersion}
                onChange={(event) => setEditFormulaVersion(event.target.value)}
                placeholder="例如 v2"
              />
              <small>必须修改名称或版本号，不能覆盖原公式记录。</small>
            </div>
            <div className="field">
              <Label htmlFor="edit-formula-description">说明（可选）</Label>
              <Input
                id="edit-formula-description"
                value={editFormulaDescription}
                onChange={(event) => setEditFormulaDescription(event.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={editFormulaBusy || !editFormulaName.trim()}>
                {editFormulaBusy ? '保存中…' : '保存新版本'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
