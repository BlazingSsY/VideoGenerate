import { memo, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { type NodeProps, Handle, Position, useEdges, useNodes, useReactFlow } from '@xyflow/react'
import { Download, Layers, Film, Trash2, ArrowRight, Play, Loader2, ChevronDown, GripVertical } from 'lucide-react'
import * as RSelect from '@radix-ui/react-select'
import { cn } from '../../lib/utils'
import { CanvasNodeContext, useUpdateNodeData } from './context'
import NodeVideo from './NodeVideo'
import { orderedOutputItems } from '../../canvasOutput'
import type { CanvasFlowNode } from '../../canvasTypes'
import type { VideoModel } from '../../types'

/** 转场特效库 — 每个转场插入到两个片段之间 */
const TRANSITIONS = [
  { value: 'none', label: '直接切换' },
  { value: 'fade', label: '淡入淡出' },
  { value: 'slide', label: '横向滑动' },
  { value: 'zoom', label: '缩放撞击' },
]

type OutputItem = { nodeKey: string; label?: string; model?: string }
/** after = 转场所跟片段的 nodeKey —— 与顺序无关，重排时转场跟随所属片段 */
type TransitionItem = { after: string; type: string }

type OutputNodeData = {
  label?: string
  items?: OutputItem[]
  excluded?: string[]
  transitions?: TransitionItem[]
  outputVideoSrc?: string
}

/**
 * 最终输出节点：多个生成片段的拼接计划 — 顺序可拖拽调整、可移除、片段间插转场、
 * 可播放按当前顺序+转场合成的最终视频。
 */
export default memo(function OutputNode({ id, data }: NodeProps) {
  const d = data as OutputNodeData
  const ctx = useContext(CanvasNodeContext)
  const nodes = useNodes<CanvasFlowNode>()
  const edges = useEdges()
  const { setEdges } = useReactFlow()
  const setData = useUpdateNodeData(id, data)

  const models = (ctx?.models || []) as VideoModel[]
  const excluded = new Set(Array.isArray(d.excluded) ? d.excluded : [])
  const transitions: TransitionItem[] = Array.isArray(d.transitions) ? d.transitions : []

  /** 片段显示名：生成节点重命名名 > 模型名 > 兜底 —— 与生成节点卡片标题一致 */
  const labelOf = (nodeKey: string): OutputItem => {
    const node = nodes.find(n => n.id === nodeKey)
    const nd: any = node?.data || {}
    const customName = String(nd.name || '').trim()
    if (customName) return { nodeKey, label: customName, model: nd.model || '' }
    const modelId = String(nd.model || '')
    const mdl = models.find(m => m.id === modelId)
    return { nodeKey, label: mdl?.label || modelId || `片段 ${nodeKey.slice(0, 4)}`, model: modelId }
  }

  // Display and save use the same sequence, including newly connected clips.
  const ordered = orderedOutputItems(id, data, nodes, edges).map(item => labelOf(item.nodeKey))

  const persist = (nextItems: OutputItem[], nextExcluded: string[], nextTransitions: TransitionItem[]) => {
    setData({ items: nextItems, excluded: nextExcluded, transitions: nextTransitions })
  }

  const removeAt = (i: number) => {
    const victim = ordered[i]
    if (!victim) return
    // Delete only this output's incoming clip edge; reconnecting can add it again.
    setEdges(current => current.filter(edge => !(edge.source === victim.nodeKey && edge.target === id && edge.targetHandle === 'input')))
    const nextTransitions = transitions.filter(t => t.after !== victim.nodeKey)
    persist(ordered.filter((_, idx) => idx !== i), Array.from(excluded).filter(key => key !== victim.nodeKey), nextTransitions)
  }

  const transitionAfter = (nodeKey: string) => transitions.find(t => t.after === nodeKey)?.type || 'none'
  const setTransition = (afterKey: string, type: string) => {
    const next = transitions.filter(t => t.after !== afterKey)
    if (type !== 'none') next.push({ after: afterKey, type })
    persist(ordered, Array.from(excluded), next)
  }

  // ===== 播放最终视频 =====
  const [composing, setComposing] = useState(false)
  const runtimeStatus = ctx?.statusMap[id]
  const finalVideo = runtimeStatus ? runtimeStatus.video_src : d.outputVideoSrc
  const nodeBusy = ['queued', 'pending', 'running'].includes(runtimeStatus?.status || '')
  const [composeError, setComposeError] = useState('')
  const playFinal = async () => {
    if (!ctx?.composeOutput || composing) return
    setComposing(true); setComposeError('')
    try {
      await ctx.composeOutput(id)
    } catch (e: any) {
      setComposeError(String(e?.response?.data?.detail || e?.message || '合成失败'))
    } finally { setComposing(false) }
  }

  // Pointer capture works inside a zoomed ReactFlow canvas and in WebViews,
  // without relying on native HTML drag/drop competing with node dragging.
  const [dragKey, setDragKey] = useState<string | null>(null)
  const [overKey, setOverKey] = useState<string | null>(null)
  const dragging = useRef<string | null>(null)
  const dragOffset = useRef({ x: 0, y: 0 })
  const [dragPreview, setDragPreview] = useState<{ x: number; y: number; width: number; height: number; scale: number; label: string; index: number } | null>(null)
  const moveItem = (from: string, to: string) => {
    const fromIndex = ordered.findIndex(item => item.nodeKey === from)
    const toIndex = ordered.findIndex(item => item.nodeKey === to)
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return
    const next = [...ordered]
    const [it] = next.splice(fromIndex, 1)
    next.splice(toIndex, 0, it)
    persist(next, Array.from(excluded), transitions)
  }
  const rowAt = (event: React.PointerEvent) => document.elementsFromPoint(event.clientX, event.clientY)
    .map(element => element.closest<HTMLElement>('[data-output-sort-key]'))
    .find(element => element?.dataset.outputNode === id)?.dataset.outputSortKey
  const resetDrag = () => { dragging.current = null; setDragKey(null); setOverKey(null); setDragPreview(null) }
  useEffect(() => {
    if (!dragKey) return
    const cancel = (event: KeyboardEvent) => { if (event.key === 'Escape') resetDrag() }
    document.addEventListener('keydown', cancel)
    return () => document.removeEventListener('keydown', cancel)
  }, [dragKey])

  return (
    <div className="canvas-node-card relative rounded-xl bg-[var(--color-surface-1)] border border-[var(--color-border)] w-64">
      {dragPreview && createPortal(
        <div data-testid="output-drag-preview" aria-hidden="true" className="output-drag-preview"
          style={{ left: dragPreview.x, top: dragPreview.y, width: dragPreview.width, height: dragPreview.height }}>
          <div className="output-drag-card flex items-center gap-1 px-1.5 py-1.5 rounded-lg border"
            style={{ width: dragPreview.width / dragPreview.scale, height: dragPreview.height / dragPreview.scale, transform: `scale(${dragPreview.scale})`, transformOrigin: 'top left' }}>
            <GripVertical className="w-3 h-3 shrink-0 text-[var(--color-ink-tertiary)]" />
            <span className="w-3 text-center text-[11px] font-mono text-[var(--color-ink-tertiary)]">{dragPreview.index + 1}</span>
            <span className="flex-1 min-w-0 truncate text-[11px]">{dragPreview.label}</span>
            <Trash2 className="w-3 h-3 m-1 shrink-0 text-[var(--color-ink-secondary)]" />
          </div>
        </div>, document.body,
      )}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--color-border-soft)]"
           style={{ background: 'linear-gradient(90deg, var(--color-primary-light), transparent)' }}>
        <Download className="w-3.5 h-3.5 text-[var(--color-primary)]" />
        <span className="text-[12px] font-medium text-[var(--color-ink)]">最终输出</span>
        {ordered.length > 1 && <span className="ml-auto text-[11px] text-[var(--color-ink-tertiary)]">{ordered.length} 段</span>}
      </div>

      <div className="p-3 space-y-2">
        {ordered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-3">
            <div className="w-11 h-9 rounded-lg border border-dashed border-[var(--color-border)] flex items-center justify-center">
              <Film className="w-4 h-4 text-[var(--color-ink-tertiary)]" />
            </div>
            <p className="text-[11px] text-[var(--color-ink-tertiary)] text-center leading-tight">
              连接多个生成节点<br/>自定义拼接顺序与转场
            </p>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-1 text-[11px] text-[var(--color-ink-tertiary)] px-0.5">
              <Layers className="w-3 h-3" /> 拖拽调整顺序
            </div>
            {ordered.map((it, i) => (
              <div key={it.nodeKey}>
                <div data-testid={`output-segment-${i}`} data-seg-key={it.nodeKey}
                  data-output-sort-key={it.nodeKey} data-output-node={id}
                  onPointerDown={event => {
                    if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return
                    event.preventDefault(); event.stopPropagation()
                    dragging.current = it.nodeKey
                    setDragKey(it.nodeKey); setOverKey(it.nodeKey)
                    const rect = event.currentTarget.getBoundingClientRect()
                    dragOffset.current = { x: event.clientX - rect.left, y: event.clientY - rect.top }
                    setDragPreview({ x: rect.left, y: rect.top, width: rect.width, height: rect.height,
                      scale: rect.width / event.currentTarget.offsetWidth, label: it.label || '视频片段', index: i })
                    event.currentTarget.setPointerCapture(event.pointerId)
                  }}
                  onPointerMove={event => {
                    if (!dragging.current) return
                    setOverKey(rowAt(event) || null)
                    const x = event.clientX - dragOffset.current.x
                    const y = event.clientY - dragOffset.current.y
                    setDragPreview(preview => preview && { ...preview, x, y })
                  }}
                  onPointerUp={event => {
                    const target = rowAt(event)
                    if (dragging.current && target) moveItem(dragging.current, target)
                    resetDrag()
                    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
                  }}
                  onPointerCancel={resetDrag} onLostPointerCapture={resetDrag}
                  className={cn(
                    'nodrag nopan group flex items-center gap-1 px-1.5 py-1.5 rounded-lg border transition-colors cursor-grab touch-none select-none',
                    i === 0 ? 'bg-[var(--color-primary)]/8 border-[var(--color-primary)]/25' : 'bg-[var(--color-surface-3)] border-transparent',
                    dragKey === it.nodeKey && 'opacity-50 cursor-grabbing',
                    overKey === it.nodeKey && dragKey !== null && dragKey !== it.nodeKey && 'ring-2 ring-[var(--color-primary)]',
                  )}
                >
                  <GripVertical className="w-3 h-3 shrink-0 text-[var(--color-ink-tertiary)]" />
                  <span className="w-3 text-center text-[11px] font-mono text-[var(--color-ink-tertiary)]">{i + 1}</span>
                  <span className="flex-1 min-w-0 truncate text-[11px] text-[var(--color-ink)]" title={it.label}>{it.label}</span>
                  <button onClick={() => removeAt(i)}
                    className="opacity-0 group-hover:opacity-100 w-5 h-5 rounded flex items-center justify-center text-[var(--color-ink-secondary)] hover:text-[var(--color-danger)] transition-opacity shrink-0"
                    aria-label="移除片段">
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
                {i < ordered.length - 1 && (
                  <div className="flex items-center gap-1.5 pl-7 py-1" data-testid={`output-transition-${i}`}>
                    <ArrowRight className="w-3 h-3 text-[var(--color-ink-tertiary)]" />
                    <RSelect.Root value={transitionAfter(it.nodeKey)} onValueChange={v => setTransition(it.nodeKey, v)}>
                      <RSelect.Trigger
                        data-testid={`transition-select-${i}`}
                        className="flex flex-1 items-center justify-between gap-1 px-1.5 py-0.5 text-[11px] bg-[var(--color-surface-2)] text-[var(--color-ink-secondary)] rounded-lg outline-none border border-[var(--color-border-soft)] hover:border-[var(--color-border)] transition-colors"
                      >
                        <span className="truncate">{TRANSITIONS.find(t => t.value === transitionAfter(it.nodeKey))?.label}</span>
                        <ChevronDown className="w-2.5 h-2.5 text-[var(--color-ink-tertiary)] shrink-0" />
                      </RSelect.Trigger>
                      <RSelect.Portal>
                        <RSelect.Content
                          className="overflow-hidden bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl shadow-xl z-[100] animate-fade-in min-w-[96px]"
                          position="popper" sideOffset={4}
                        >
                          <RSelect.Viewport className="p-1">
                            {TRANSITIONS.map(t => (
                              <RSelect.Item key={t.value} value={t.value}
                                className="flex items-center px-2 py-1 text-[11px] text-[var(--color-ink-secondary)] rounded-lg outline-none cursor-pointer data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-ink)] data-[state=checked]:text-[var(--color-primary)] transition-colors"
                              >
                                <RSelect.ItemText>{t.label}</RSelect.ItemText>
                              </RSelect.Item>
                            ))}
                          </RSelect.Viewport>
                        </RSelect.Content>
                      </RSelect.Portal>
                    </RSelect.Root>
                  </div>
                )}
              </div>
            ))}

            {/* 合成 + 播放 */}
            <div className="pt-1 space-y-2">
              <button onClick={playFinal} disabled={composing || nodeBusy || ctx?.canvasRunning || ordered.length === 0}
                data-testid="compose-final-btn"
                className="flex items-center justify-center gap-1.5 w-full py-1.5 text-[11px] text-white bg-brand-gradient rounded-lg hover:shadow-md disabled:opacity-40 transition-all">
                {composing || nodeBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                {runtimeStatus?.status === 'queued' ? '等待片段完成…' : composing || nodeBusy ? '合成中…' : finalVideo ? '重新合成' : '合成最终视频'}
              </button>
              {(composeError || runtimeStatus?.error) && <p role="alert" className="text-[11px] text-[var(--color-danger)]">{composeError || runtimeStatus?.error}</p>}
              {finalVideo && (
                <NodeVideo src={finalVideo} title="最终输出视频" testId="final-video" />
              )}
            </div>
          </>
        )}
      </div>

      <Handle type="target" position={Position.Left} id="input" style={{ top: '26px', left: '-4px', '--handle-color': 'var(--color-port-video)' } as never} data-handle-color="1" />
    </div>
  )
})
