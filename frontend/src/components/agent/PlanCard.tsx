import { useState } from 'react'
import { Play, Settings2, Clock, DollarSign, Layers, Layout, Loader2, CheckCircle2 } from 'lucide-react'
import { cn, formatCost, formatDuration } from '../../lib/utils'

export default function PlanCard({
  plan, cost, seconds, validated, turnId, onExecute, onDraw, onRefine, executing, executed,
}: {
  plan: any
  cost: number
  seconds: number
  validated: boolean
  turnId?: string
  onExecute?: () => void
  onDraw?: () => Promise<void>
  onRefine?: () => void
  executing?: boolean
  executed?: boolean
}) {
  const [drawing, setDrawing] = useState(false)
  const [drawn, setDrawn] = useState(false)
  const [drawError, setDrawError] = useState('')

  const nodes = plan?.nodes || []
  const generateNodes = nodes.filter((n: any) => n.type === 'generate')
  const composeNodes = nodes.filter((n: any) => n.type === 'compose')

  const drawToCanvas = async () => {
    if (!turnId || drawing || !onDraw) return
    setDrawing(true)
    setDrawError('')
    try {
      await onDraw()
      setDrawn(true)
    } catch (error: any) {
      setDrawError(String(error?.response?.data?.detail || error?.message || '转换画布失败'))
    } finally { setDrawing(false) }
  }

  return (
    <div className="my-3 rounded-xl border border-[var(--color-border)] overflow-hidden bg-[var(--color-surface-1)]">
      <div className="flex items-center gap-2 px-3.5 py-2.5 bg-[var(--color-accent-light)]">
        <span className="text-sm font-semibold text-[var(--color-ink)]">{plan?.title || '创作计划'}</span>
        {validated && <span className="px-1.5 py-0.5 text-[10px] rounded bg-[var(--color-success)]/10 text-[var(--color-success)] font-medium">已校验</span>}
      </div>
      <div className="py-1">
        {generateNodes.map((node: any, i: number) => (
          <div key={i} className="flex items-center gap-2.5 px-3.5 py-1.5 border-b border-[var(--color-border-soft)] last:border-0">
            <span className="text-xs font-mono text-[var(--color-primary)] w-14 shrink-0">{node.id}</span>
            <span className="text-xs text-[var(--color-ink-secondary)] flex-1 truncate">
              {(node.prompt || '').slice(0, 50)}{(node.prompt || '').length > 50 ? '…' : ''}
            </span>
            <span className="text-[10px] text-[var(--color-ink-tertiary)] shrink-0 whitespace-nowrap">
              {(node.model || '').split('-')[0]} · {node.resolution} · {node.duration}s
            </span>
          </div>
        ))}
        {composeNodes.map((node: any, i: number) => (
          <div key={`c-${i}`} className="flex items-center gap-2.5 px-3.5 py-1.5 border-b border-[var(--color-border-soft)]">
            <span className="text-xs font-mono text-[var(--color-ink-tertiary)] w-14">{node.id}</span>
            <span className="text-xs text-[var(--color-ink-tertiary)]">合成 {node.inputs?.length || 0} 个片段</span>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-[var(--color-surface-3)]">
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1 text-[var(--color-warning)] font-medium">
            <DollarSign className="w-3 h-3" />{formatCost(cost)}
          </span>
          <span className="flex items-center gap-1 text-[var(--color-ink-tertiary)]">
            <Clock className="w-3 h-3" />{formatDuration(seconds)}
          </span>
          <span className="flex items-center gap-1 text-[var(--color-ink-tertiary)]">
            <Layers className="w-3 h-3" />{generateNodes.length} 节点
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={drawToCanvas}
            disabled={drawing || drawn || !turnId || !onDraw}
            className={cn(
              'flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border transition-colors',
              drawn
                ? 'border-[var(--color-success)]/30 text-[var(--color-success)] bg-[var(--color-success)]/5'
                : 'border-[var(--color-border)] text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-ink)]'
            )}
          >
            {drawing ? <Loader2 className="w-3 h-3 animate-spin" /> : drawn ? <CheckCircle2 className="w-3 h-3" /> : <Layout className="w-3 h-3" />}
            {drawing ? '绘制中…' : drawn ? '已画到画布' : '画到画布'}
          </button>
          {onRefine && (
            <button onClick={onRefine} className="flex items-center gap-1 px-2.5 py-1 text-xs text-[var(--color-ink-secondary)] hover:bg-[var(--color-surface-2)] rounded-md transition-colors">
              <Settings2 className="w-3 h-3" /> 改参数
            </button>
          )}
          {onExecute && !executed && (
            <button
              onClick={onExecute}
              disabled={executing}
              className="flex items-center gap-1 px-3 py-1 text-xs text-white bg-brand-gradient rounded-md hover:shadow-md disabled:opacity-50 transition-all"
            >
              {executing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
              {executing ? '执行中…' : '执行'}
            </button>
          )}
        </div>
      </div>
      {drawError && <p className="px-3.5 pb-2 text-[11px] text-[var(--color-danger)]">{drawError}</p>}
    </div>
  )
}
