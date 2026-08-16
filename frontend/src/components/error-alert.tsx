import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'


export function ErrorAlert({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <Alert variant="destructive" className="sticky-alert">
      <AlertDescription>{message}</AlertDescription>
      <Button type="button" variant="link" size="sm" onClick={onDismiss}>
        关闭
      </Button>
    </Alert>
  )
}
