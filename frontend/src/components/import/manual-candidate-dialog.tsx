import { exchangeLabels } from '@/lib/labels'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CalendarInput } from '@/components/calendar-input'
import type { ImportData } from '@/hooks/use-import-data'
import type { Exchange } from '@/api/contracts'

export function ManualCandidateDialog({ data }: { data: ImportData }) {
  const {
    manualDialogOpen,
    setManualDialogOpen,
    manualFormulaId,
    setManualFormulaId,
    manualSelectionDate,
    setManualSelectionDate,
    manualRecordedAt,
    setManualRecordedAt,
    manualCandidateCode,
    setManualCandidateCode,
    manualCandidateExchange,
    setManualCandidateExchange,
    manualCandidateName,
    setManualCandidateName,
    manualCandidateNote,
    setManualCandidateNote,
    manualCandidates,
    manualBusy,
    manualError,
    setManualError,
    formulas,
    resetManualForm,
    closeManualDialog,
    addManualCandidate,
    handleManualCandidateKeyDown,
    removeManualCandidate,
    createManualBatch,
  } = data
  return (
          <Dialog
            open={manualDialogOpen}
            onOpenChange={(open) => {
              if (!open && manualBusy) {
                return
              }
              if (!open) {
                resetManualForm()
              }
              setManualDialogOpen(open)
              if (open) {
                setManualError(null)
              }
            }}
          >
            <DialogContent className="manual-dialog-content">
              <DialogHeader>
                <DialogTitle>手动添加候选</DialogTitle>
                <DialogDescription>
                  明确记录选股批次和交易所，不根据代码前缀猜测市场。提交后会保存为手工来源批次。
                </DialogDescription>
              </DialogHeader>
              <form className="dialog-form manual-dialog-form" onSubmit={createManualBatch}>
                <div className="manual-metadata-fields">
                  <div className="field">
                    <Label htmlFor="manual-formula-select">启用公式</Label>
                    <Select value={manualFormulaId} onValueChange={setManualFormulaId}>
                      <SelectTrigger id="manual-formula-select" aria-describedby="manual-formula-help">
                        <SelectValue placeholder={formulas.length ? '选择启用公式' : '暂无启用公式'} />
                      </SelectTrigger>
                      <SelectContent>
                        {formulas.map((formula) => (
                          <SelectItem key={formula.formula_id} value={formula.formula_id}>
                            {formula.name}{formula.version ? ` · ${formula.version}` : ''}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <small id="manual-formula-help">
                      {formulas.length ? '仅显示当前启用公式。' : '请先新建一个公式。'}
                    </small>
                  </div>
                  <div className="field">
                    <Label htmlFor="manual-selection-date">选股日期</Label>
                    <CalendarInput
                      id="manual-selection-date"
                      value={manualSelectionDate}
                      onChange={setManualSelectionDate}
                      required
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="manual-recorded-at">记录时间（北京时间）</Label>
                    <CalendarInput
                      id="manual-recorded-at"
                      type="datetime-local"
                      value={manualRecordedAt}
                      onChange={setManualRecordedAt}
                      required
                    />
                    <small>提交时会带上 +08:00 时区。</small>
                  </div>
                </div>

                <div className="manual-candidate-entry">
                  <div>
                    <h3>添加一条候选</h3>
                    <p>代码必须为六位数字，交易所必须显式选择。</p>
                  </div>
                  <div className="manual-entry-fields">
                    <div className="field">
                      <Label htmlFor="manual-candidate-code">六位代码</Label>
                      <Input
                        id="manual-candidate-code"
                        type="text"
                        inputMode="numeric"
                        maxLength={6}
                        pattern="[0-9]{6}"
                        value={manualCandidateCode}
                        onChange={(event) => setManualCandidateCode(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="例如 600000"
                        aria-describedby="manual-candidate-help"
                      />
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-exchange">交易所</Label>
                      <select
                        id="manual-candidate-exchange"
                        className="native-select"
                        value={manualCandidateExchange}
                        onChange={(event) => setManualCandidateExchange(event.target.value as Exchange | '')}
                        onKeyDown={handleManualCandidateKeyDown}
                      >
                        <option value="">选择交易所</option>
                        {Object.entries(exchangeLabels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-name">名称（可选）</Label>
                      <Input
                        id="manual-candidate-name"
                        type="text"
                        value={manualCandidateName}
                        onChange={(event) => setManualCandidateName(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="例如 平安银行"
                      />
                    </div>
                    <div className="field">
                      <Label htmlFor="manual-candidate-note">备注（可选）</Label>
                      <Input
                        id="manual-candidate-note"
                        type="text"
                        value={manualCandidateNote}
                        onChange={(event) => setManualCandidateNote(event.target.value)}
                        onKeyDown={handleManualCandidateKeyDown}
                        placeholder="记录当时的判断"
                      />
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={manualBusy}
                      onClick={addManualCandidate}
                    >
                      加入预览
                    </Button>
                  </div>
                  <small id="manual-candidate-help">按 Enter 也可以加入预览。</small>
                </div>

                {manualError && (
                  <p className="manual-form-error" role="alert">
                    {manualError}
                  </p>
                )}

                <div className="manual-preview" aria-labelledby="manual-preview-title">
                  <div className="manual-preview-heading">
                    <h3 id="manual-preview-title">候选预览</h3>
                    <span>{manualCandidates.length} 条</span>
                  </div>
                  {manualCandidates.length === 0 ? (
                    <p className="empty-copy">尚未加入候选，至少添加一条后才能提交。</p>
                  ) : (
                    <div className="manual-preview-list">
                      {manualCandidates.map((candidate) => (
                        <article className="manual-preview-row" key={candidate.id}>
                          <div className="manual-preview-identity">
                            <strong>{candidate.local_code}.{candidate.exchange}</strong>
                            <span>{candidate.name || '未提供名称'}</span>
                          </div>
                          <span className="manual-preview-note">{candidate.note || '未填写备注'}</span>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={manualBusy}
                            aria-label={`移除 ${candidate.local_code}.${candidate.exchange}`}
                            onClick={() => removeManualCandidate(candidate.id)}
                          >
                            移除
                          </Button>
                        </article>
                      ))}
                    </div>
                  )}
                </div>

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={manualBusy}
                    onClick={closeManualDialog}
                  >
                    取消
                  </Button>
                  <Button
                    type="submit"
                    disabled={
                      manualBusy ||
                      !manualFormulaId ||
                      !manualSelectionDate ||
                      !manualRecordedAt ||
                      manualCandidates.length === 0
                    }
                  >
                    {manualBusy ? '提交中…' : '提交手工批次'}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
  )
}
