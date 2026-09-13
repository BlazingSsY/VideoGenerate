import { memo } from 'react'
import { cn } from '../../lib/utils'

/**
 * Pure layout shell — no Handles.
 * Each node component renders only the Handles it actually needs.
 * 卡片宽度由各节点组件的 className 固定（w-48/w-64…），高度随内容自然增长。
 */
const NodeShell = memo(function NodeShell({
  title, icon, children, status, className, titleExtra, onTitleDoubleClick,
}: {
  title: string
  icon: React.ReactNode
  children?: React.ReactNode
  status?: string
  className?: string
  titleExtra?: React.ReactNode
  onTitleDoubleClick?: () => void
}) {
  const statusBorder = status === 'succeeded' ? 'border-[var(--color-success)]/40'
    : status === 'running' ? 'border-[var(--color-accent)]'
    : status === 'failed' ? 'border-[var(--color-danger)]/40'
    : 'border-[var(--color-border)]'

  return (
    <div data-status={status} className={cn('canvas-node-card rounded-xl bg-[var(--color-surface-1)] border overflow-hidden', statusBorder, className)}>
      <div
        className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--color-border-soft)]"
        onDoubleClick={onTitleDoubleClick ? (e => { e.stopPropagation(); onTitleDoubleClick() }) : undefined}
      >
        <span className="text-[var(--color-accent)]">{icon}</span>
        <span className="text-[12px] font-medium text-[var(--color-ink)] truncate max-w-28">{title}</span>
        {titleExtra}
        <span className="ml-auto flex items-center gap-1 shrink-0">
          {status === 'running' && <div className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-pulse" />}
          {status === 'succeeded' && <div className="w-2 h-2 rounded-full bg-[var(--color-success)]" />}
        </span>
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
})

export default NodeShell
