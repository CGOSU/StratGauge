import { useRef } from 'react'
import { CalendarDays, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export type CalendarInputProps = {
  id: string
  type?: 'date' | 'datetime-local'
  value: string
  onChange: (value: string) => void
  required?: boolean
  disabled?: boolean
  min?: string
  max?: string
  className?: string
  'aria-describedby'?: string
}

export function CalendarInput({
  id,
  type = 'date',
  value,
  onChange,
  required = false,
  disabled = false,
  min,
  max,
  className,
  'aria-describedby': ariaDescribedBy,
}: CalendarInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const isDateTime = type === 'datetime-local'
  const pickerLabel = isDateTime ? '打开日期和时间选择器' : '打开日期选择器'

  function openPicker() {
    const input = inputRef.current
    if (!input) {
      return
    }
    input.focus()
    try {
      input.showPicker?.()
    } catch {
      return
    }
  }

  function clearValue() {
    onChange('')
    inputRef.current?.focus()
  }

  return (
    <div className={cn('calendar-input', className)}>
      <Input
        ref={inputRef}
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        disabled={disabled}
        min={min}
        max={max}
        aria-describedby={ariaDescribedBy}
      />
      {!required && value && (
        <button
          type="button"
          className="calendar-input-clear"
          aria-label={isDateTime ? '清空日期和时间' : '清空日期'}
          disabled={disabled}
          onClick={clearValue}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      )}
      <button
        type="button"
        className="calendar-input-trigger"
        aria-label={pickerLabel}
        disabled={disabled}
        onClick={openPicker}
      >
        <CalendarDays className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  )
}
