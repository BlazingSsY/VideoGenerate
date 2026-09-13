import { CheckCircle2, Loader2, Circle, XCircle, RotateCcw, Square } from 'lucide-react'
import { cn } from '../../lib/utils'

export default function TaskList({
  tasks, runStatus, error, onCancel, onRetry,
}: {
  tasks: Array<{ nodeId: string; status: string; error?: string }>
  runStatus?: string
  error?: string
  onCancel?: () => void
  onRetry?: () => void
}) {
  return (
    <div className="my-2 space-y-0.5">
      {tasks.map((task, i) => {
        const icon = task.status === 'succeeded'
          ? <CheckCircle2 className="w-3 h-3 text-[var(--color-success)]" />
          : task.status === 'failed' || task.status === 'blocked' || task.status === 'canceled'
            ? <XCircle className="w-3 h-3 text-[var(--color-danger)]" />
            : task.status === 'running'
              ? <Loader2 className="w-3 h-3 text-[var(--color-accent)] animate-spin" />
              : <Circle className="w-3 h-3 text-[var(--color-ink-tertiary)]" />
        return (
          <div key={i} className="flex items-start gap-2 py-0.5">
            {icon}
            <div className="min-w-0">
              <span className={cn(
                'text-xs',
                task.status === 'succeeded' ? 'text-[var(--color-ink-tertiary)]' : 'text-[var(--color-ink-secondary)]'
              )}>
                {task.nodeId} · {task.status}
              </span>
              {task.error && <p className="text-[10px] text-[var(--color-danger)] break-words">{task.error}</p>}
            </div>
          </div>
        )
      })}
      {error && <p className="text-[11px] text-[var(--color-danger)] break-words">{error}</p>}
      <div className="flex gap-2 pt-1">
        {onCancel && ['queued', 'running', 'cancel_requested'].includes(runStatus || '') && (
          <button onClick={onCancel} className="flex items-center gap-1 px-2 py-1 text-[11px] rounded-md border border-[var(--color-border)] text-[var(--color-ink-secondary)] hover:text-[var(--color-danger)]">
            <Square className="w-3 h-3" /> 停止后续任务
          </button>
        )}
        {onRetry && ['failed', 'canceled'].includes(runStatus || '') && (
          <button onClick={onRetry} className="flex items-center gap-1 px-2 py-1 text-[11px] rounded-md border border-[var(--color-border)] text-[var(--color-ink-secondary)] hover:text-[var(--color-primary)]">
            <RotateCcw className="w-3 h-3" /> 重试失败分支
          </button>
        )}
      </div>
    </div>
  )
}
