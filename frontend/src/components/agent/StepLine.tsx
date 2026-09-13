import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { cn } from '../../lib/utils'

export default function StepLine({ step }: {
  step: { seq: number; kind: string; title: string; status: 'running' | 'done' | 'failed'; summary?: string }
}) {
  const icon = step.status === 'done'
    ? <CheckCircle2 className="w-3.5 h-3.5 text-[var(--color-success)]" />
    : step.status === 'failed'
      ? <XCircle className="w-3.5 h-3.5 text-[var(--color-danger)]" />
      : <Loader2 className="w-3.5 h-3.5 text-[var(--color-accent)] animate-spin" />

  return (
    <div className="flex items-center gap-2 py-0.5">
      {icon}
      <span className={cn(
        'text-xs',
        step.status === 'done' ? 'text-[var(--color-ink-secondary)]' : 'text-[var(--color-ink)]'
      )}>
        {step.title}
      </span>
      {step.summary && (
        <span className="text-[10px] text-[var(--color-ink-tertiary)] truncate max-w-[160px]">
          {step.summary}
        </span>
      )}
    </div>
  )
}
