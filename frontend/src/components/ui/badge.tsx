import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'status-badge inline-flex items-center px-2.5 py-0.5 text-xs',
  {
    variants: {
      variant: {
        default: 'status-badge--default',
        secondary: 'status-badge--secondary',
        destructive: 'status-badge--destructive',
        outline: 'status-badge--outline',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
