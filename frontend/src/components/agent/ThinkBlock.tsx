import { useState } from 'react'
import { ChevronRight, Brain } from 'lucide-react'
import { cn } from '../../lib/utils'

export default function ThinkBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(true)
  if (!text) return null

  return (
    <div className="my-2 rounded-xl border border-[var(--color-process-border)] overflow-hidden bg-[var(--color-process-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 w-full px-3 py-2.5 text-xs text-[var(--color-accent)] bg-[var(--color-process-header)] hover:bg-[var(--color-accent-light)] transition-colors"
      >
        <Brain className="w-3 h-3" />
        <span>思考中...</span>
        <ChevronRight className={cn('w-3 h-3 ml-auto transition-transform', expanded && 'rotate-90')} />
      </button>
      <div className={cn('px-3 py-2.5 text-xs text-[var(--color-ink-secondary)] whitespace-pre-wrap leading-relaxed max-h-[216px] overflow-y-auto overscroll-contain', !expanded && 'hidden')}>
        {text}
      </div>
    </div>
  )
}
