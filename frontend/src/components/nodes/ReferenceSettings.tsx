import { useEffect, useState } from 'react'
import { useEdges, useNodes } from '@xyflow/react'
import { ArrowUp, ArrowDown, ChevronDown, SlidersHorizontal } from 'lucide-react'
import type { CanvasFlowNode, GenerateNodeData } from '../../canvasTypes'
import { canvasReferences, referencePrompt } from '../../canvasReferences'
import { useUpdateNodeData } from './context'

export default function ReferenceSettings({ id, data }: { id: string; data: GenerateNodeData }) {
  const nodes = useNodes<CanvasFlowNode>()
  const edges = useEdges()
  const setData = useUpdateNodeData(id, data)
  const [open, setOpen] = useState(true)
  const { rows, bindings, enabled, prompt } = canvasReferences(id, data, nodes, edges)
  const signature = JSON.stringify(bindings)
  useEffect(() => {
    if (enabled && signature !== JSON.stringify(data.reference_bindings || [])) setData({ reference_bindings: bindings })
  }, [signature, enabled, data.reference_bindings, setData])
  const preview = referencePrompt(prompt, rows, enabled)
  const move = (edgeId: string, direction: number) => {
    const row = rows.find(item => item.edge_id === edgeId)!
    const group = rows.filter(item => item.kind === row.kind)
    const other = group[group.indexOf(row) + direction]
    if (!other) return
    const next = [...bindings]
    const from = next.findIndex(item => item.edge_id === edgeId)
    const to = next.findIndex(item => item.edge_id === other.edge_id)
    ;[next[from], next[to]] = [next[to], next[from]]
    setData({ reference_bindings: next })
  }
  return (
    <section data-testid="reference-settings" className="nodrag nopan nowheel mt-2 border border-[var(--color-border-soft)] rounded-lg overflow-hidden">
      <button onClick={() => setOpen(value => !value)} aria-expanded={open} className="flex items-center gap-1.5 w-full px-2 py-1.5 text-[11px] text-[var(--color-ink-secondary)] bg-[var(--color-surface-3)]">
        <SlidersHorizontal className="w-3 h-3" /> 素材引用 · {rows.length}<ChevronDown className={`ml-auto w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="p-2 space-y-2">
        <p className="text-[10px] leading-relaxed text-[var(--color-ink-tertiary)]">点击引用插入提示词；调整编号后，引用仍绑定原素材。用途说明会一同发送。</p>
        {rows.length === 0 && <p className="text-[11px] text-[var(--color-ink-tertiary)]">连接图片、视频或音频素材后设置引用。</p>}
        <div className="max-h-52 overflow-y-auto space-y-2">
          {rows.map(row => {
            const group = rows.filter(item => item.kind === row.kind)
            return <div key={row.edge_id} data-testid={`reference-${row.edge_id}`} className="rounded-md border border-[var(--color-border-soft)] p-1.5 space-y-1">
              <div className="flex items-center gap-1 text-[10px]">
                <span className="text-[var(--color-primary)] font-medium shrink-0">{row.tag}</span>
                <span title={row.name} className="flex-1 truncate text-[var(--color-ink-secondary)]">{row.name}</span>
                <button aria-label={`上移${row.alias}`} disabled={group[0] === row} onClick={() => move(row.edge_id, -1)} className="disabled:opacity-20"><ArrowUp className="w-3 h-3" /></button>
                <button aria-label={`下移${row.alias}`} disabled={group[group.length - 1] === row} onClick={() => move(row.edge_id, 1)} className="disabled:opacity-20"><ArrowDown className="w-3 h-3" /></button>
              </div>
              <button title="插入到附加提示词" className="text-[10px] font-mono text-[var(--color-primary)] hover:underline" onClick={() => setData({ reference_bindings: bindings, inlinePrompt: `${data.inlinePrompt || ''}{{${row.alias}}}` })}>{`{{${row.alias}}}`}</button>
              <input aria-label={`${row.alias}用途`} maxLength={200} placeholder="用途，例如：主角外观、镜头运动、配音"
                value={row.description} onChange={event => setData({ reference_bindings: bindings.map(item => item.edge_id === row.edge_id ? { ...item, description: event.target.value } : item) })}
                className="w-full min-w-0 px-1.5 py-1 text-[10px] rounded bg-[var(--color-surface-3)] outline-none text-[var(--color-ink)]" />
            </div>
          })}
        </div>
        {preview.missing.length > 0 && <p role="alert" className="text-[10px] text-[var(--color-danger)]">引用素材已断开或不存在：{preview.missing.join('、')}</p>}
        <details className="text-[10px] text-[var(--color-ink-secondary)]"><summary className="cursor-pointer">查看实际发送的提示词</summary><pre data-testid="reference-prompt-preview" className="whitespace-pre-wrap break-words mt-1 max-h-40 overflow-y-auto">{preview.prompt}</pre></details>
      </div>}
    </section>
  )
}
