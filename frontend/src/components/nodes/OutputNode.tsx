import { memo, useContext, useEffect, useState } from 'react'
import { type NodeProps, Handle, Position, useReactFlow } from '@xyflow/react'
import { Download, Layers, Film, Trash2, ArrowRight, Play, Loader2, ChevronDown } from 'lucide-react'
import * as RSelect from '@radix-ui/react-select'
import { cn } from '../../lib/utils'
import { CanvasNodeContext, useUpdateNodeData } from './context'
import api from '../../api'
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
  const { getEdges, getNodes } = useReactFlow()
  const setData = useUpdateNodeData(id, data)

  const models = (ctx?.models || []) as VideoModel[]
  const canvasId = ctx?.activeCanvasId
  const saved: OutputItem[] = Array.isArray(d.items) ? d.items : []
  const excluded = new Set(Array.isArray(d.excluded) ? d.excluded : [])
  const transitions: TransitionItem[] = Array.isArray(d.transitions) ? d.transitions : []

  // 连线事实：当前连入的 generate 源（连线增删立即反映）
  const connected = getEdges()
    .filter(e => e.target === id && e.targetHandle === 'input' && getNodes().some(n => n.id === e.source && n.type === 'generate'))
    .map(e => e.source)

  /** 片段显示名：生成节点重命名名 > 模型名 > 兜底 —— 与生成节点卡片标题一致 */
  const labelOf = (nodeKey: string): OutputItem => {
    const node = getNodes().find(n => n.id === nodeKey)
    const nd: any = node?.data || {}
    const customName = String(nd.name || '').trim()
    if (customName) return { nodeKey, label: customName, model: nd.model || '' }
    const modelId = String(nd.model || '')
    const mdl = models.find(m => m.id === modelId)
    return { nodeKey, label: mdl?.label || modelId || `片段 ${nodeKey.slice(0, 4)}`, model: modelId }
  }

  // 保存顺序优先；已断连的剔除；新连入且未被移除的追加到末尾。
  // 注意：name 改动也要走这里刷新 items 里的 label —— ordered 传入 persist 即落库。
  // 不 useMemo：name/label 源自 getNodes()，是命令式快照，依赖没法靠引用追踪（每次都新数组），
  // 片段数秒级量级，直接重算最可靠。
  const ordered: OutputItem[] = [
    ...saved.filter(it => connected.includes(it.nodeKey)).map(it => ({ ...it, ...labelOf(it.nodeKey) })),
    ...connected.filter(k => !saved.some(it => it.nodeKey === k) && !excluded.has(k)).map(labelOf),
  ]

  const persist = (nextItems: OutputItem[], nextExcluded: string[], nextTransitions: TransitionItem[]) => {
    setData({ items: nextItems, excluded: nextExcluded, transitions: nextTransitions })
  }

  const removeAt = (i: number) => {
    const victim = ordered[i]
    if (!victim) return
    // 该片段的出转场一并移除；仍连着线 —— 记入 excluded，防止重渲染时又被当作新连入追加
    const nextTransitions = transitions.filter(t => t.after !== victim.nodeKey)
    persist(ordered.filter((_, idx) => idx !== i), [...Array.from(excluded), victim.nodeKey], nextTransitions)
  }

  const transitionAfter = (nodeKey: string) => transitions.find(t => t.after === nodeKey)?.type || 'none'
  const setTransition = (afterKey: string, type: string) => {
    const next = transitions.filter(t => t.after !== afterKey)
    if (type !== 'none') next.push({ after: afterKey, type })
    persist(ordered, Array.from(excluded), next)
  }

  // ===== 播放最终视频 =====
  const [composing, setComposing] = useState(false)
  const [finalVideo, setFinalVideo] = useState<string | null>(d.outputVideoSrc || null)
  const runtimeStatus = ctx?.statusMap[id]
  useEffect(() => {
    setFinalVideo(runtimeStatus ? runtimeStatus.video_src || null : d.outputVideoSrc || null)
  }, [runtimeStatus?.video_src, runtimeStatus?.status, d.outputVideoSrc])
  const [composeError, setComposeError] = useState('')
  const playFinal = async () => {
    if (!canvasId || composing) return
    setComposing(true); setComposeError(''); setFinalVideo(null)
    try {
      const res = await api.post(`/api/canvases/${canvasId}/output/${id}/compose`)
      setFinalVideo((res.data as { video_src: string }).video_src)
    } catch (e: any) {
      setComposeError(String(e?.response?.data?.detail || e?.message || '合成失败'))
    } finally { setComposing(false) }
  }

  // ===== 拖拽排序（HTML5 dnd；行内 dragstart 起点在节点内部，ReactFlow 只监听节点级拖拽，不冲突）=====
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [overIdx, setOverIdx] = useState<number | null>(null)
  const onRowDragStart = (i: number) => (e: React.DragEvent) => {
    setDragIdx(i)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(i))
  }
  const onRowDragEnter = (i: number) => () => {
    if (dragIdx === null || dragIdx === i) return
    setOverIdx(i)
  }
  const onRowDrop = (i: number) => (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation()
    if (dragIdx === null || dragIdx === i) { resetDrag(); return }
    const next = [...ordered]
    const [it] = next.splice(dragIdx, 1)
    next.splice(i, 0, it)
    persist(next, Array.from(excluded), transitions)
    resetDrag()
  }
  const resetDrag = () => { setDragIdx(null); setOverIdx(null) }
  const onRowDragEnd = () => resetDrag()

  return (
    <div className="canvas-node-card relative rounded-xl bg-[var(--color-surface-1)] border border-[var(--color-border)] w-64">
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
                  draggable
                  onDragStart={onRowDragStart(i)}
                  onDragEnter={onRowDragEnter(i)}
                  onDragOver={e => e.preventDefault()}
                  onDrop={onRowDrop(i)}
                  onDragEnd={onRowDragEnd}
                  className={cn(
                    'group flex items-center gap-1.5 px-2 py-1.5 rounded-lg border transition-all',
                    i === 0 ? 'bg-[var(--color-primary)]/8 border-[var(--color-primary)]/25' : 'bg-[var(--color-surface-3)] border-transparent',
                    dragIdx === i && 'opacity-40',
                    overIdx === i && dragIdx !== null && dragIdx !== i && 'border-t-2 border-t-[var(--color-primary)]',
                  )}
                >
                  <span className="w-4 text-center text-[11px] font-mono text-[var(--color-ink-tertiary)]">{i + 1}</span>
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
              <button onClick={playFinal} disabled={composing || ordered.length === 0}
                data-testid="compose-final-btn"
                className="flex items-center justify-center gap-1.5 w-full py-1.5 text-[11px] text-white bg-brand-gradient rounded-lg hover:shadow-md disabled:opacity-40 transition-all">
                {composing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                {composing ? '合成中…' : finalVideo ? '重新合成并播放' : '播放最终视频'}
              </button>
              {composeError && <p className="text-[11px] text-[var(--color-danger)]">{composeError}</p>}
              {finalVideo && (
                <video data-testid="final-video" src={finalVideo} className="w-full rounded-lg" controls autoPlay />
              )}
            </div>
          </>
        )}
      </div>

      <Handle type="target" position={Position.Left} id="input" style={{ top: '26px', left: '-4px', '--handle-color': 'var(--color-port-video)' } as never} data-handle-color="1" />
    </div>
  )
})
