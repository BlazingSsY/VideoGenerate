import { memo, useContext, useState } from 'react'
import { type NodeProps, Handle, Position, useReactFlow, useStoreApi } from '@xyflow/react'
import { Zap, Play, Loader2, Check, X, PenLine } from 'lucide-react'
import { CanvasNodeContext, useUpdateNodeData } from './context'
import NodeSelect from './NodeSelect'
import NodeShell from './NodeShell'
import api from '../../api'
import type { GenerateNodeData } from '../../canvasTypes'
import { generateMediaSlots } from '../../canvasRules'
import type { MediaKind, VideoModel } from '../../types'

/** 连接点语义色，内联注入各 Handle 的 --handle-color；styles.css 的 [data-handle-color] 规则消费。 */
const HANDLE_COLORS: Record<string, string> = {
  prompt: 'var(--color-port-prompt)',
  image: 'var(--color-port-image)',
  end_frame: 'var(--color-port-end-frame)',
  video: 'var(--color-port-video)',
  audio: 'var(--color-port-audio)',
}

/** kind → hover 名称（无数字：i2v 是“首帧图/尾帧图”，r2v 聚合点是“图片/视频/音频”）。 */
const KIND_LABELS: Record<string, string> = {
  image: '图片',
  end_frame: '尾帧图',
  video: '视频',
  audio: '音频',
}

/**
 * Generation node（v4 — 每 kind 单点）：
 * - 每个 media kind 只渲染一个连接点，多条素材线共连这一点（聚合）；
 * - i2v：image 点 = 首帧图、end_frame 点 = 尾帧图，都不带数字；
 * - 槽位容量由模型 spec 决定（连接时校验），卡片上不再有 ± 按钮和数字。
 * Data patches go through setNodes via useUpdateNodeData; mutating
 * node.data in place would freeze the UI (ReactFlow compares data refs).
 */
const GenerateNode = memo(function GenerateNode({ id, data }: NodeProps) {
  const ctx = useContext(CanvasNodeContext)
  const d = data as GenerateNodeData
  const { setEdges } = useReactFlow()
  const storeApi = useStoreApi()
  const setData = useUpdateNodeData(id, data)
  const [running, setRunning] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')

  // 重命名：名称随 node.data 持久化；输出节点读取同名片段显示
  const nodeName = String(d.name || '')
  const startRename = () => { setNameDraft(nodeName); setRenaming(true) }
  const commitRename = () => {
    const v = nameDraft.trim().slice(0, 40)
    if (v !== nodeName) setData({ name: v })
    setRenaming(false)
  }

  const models = ctx?.models || []
  const model = models.find(m => m.id === d.model) as VideoModel | undefined
  const capabilities = model?.capabilities || []
  const capSpecs = (ofModel: VideoModel | undefined, capabilityId: string) =>
    (ofModel?.capabilities.find(c => c.id === capabilityId)?.media_inputs) || []

  const specs = capSpecs(model, d.capability)
  const slots = generateMediaSlots(model, d.capability, d.media_slots)

  // 每 kind 一个展示行；handle id 取 declared 首槽（无 declared 时 {kind}_0）
  interface KindRow { kind: MediaKind; label: string; handle: string; note?: string }
  const rows: KindRow[] = []
  for (const spec of specs) {
    const handles = slots[spec.kind] || []
    if (handles.length === 0) continue
    const isI2vFirstFrame = d.capability === 'i2v' && spec.kind === 'image'
    rows.push({
      kind: spec.kind,
      label: isI2vFirstFrame ? '首帧图' : KIND_LABELS[spec.kind],
      handle: handles[0],
      note: spec.note,
    })
  }

  // 删除挂在本节点已失效 handle 上的边（模型/模式切换后）
  const pruneEdges = (validHandles: string[]) => {
    const valid = new Set(['prompt', ...validHandles])
    setEdges(eds => eds.filter(e =>
      e.target === id ? valid.has(e.targetHandle!) : true
    ))
  }

  /** 按目标（新）模型的 spec 重建 media_slots：单槽类一槽、聚合类一槽占位。 */
  const slotsFor = (ofModel: VideoModel | undefined, capabilityId: string) => {
    const next: Partial<Record<MediaKind, string[]>> = {}
    for (const spec of capSpecs(ofModel, capabilityId)) {
      next[spec.kind] = [`${spec.kind}_0`]
    }
    return next
  }

  /**
   * 模式/模型切换会增删 target Handle（如 t2v→i2v 出现 end_frame_0）。
   * ReactFlow 仅在节点尺寸变化时经 ResizeObserver 重测 handleBounds——若切换前后
   * 卡片高度不变（i2v 删掉文字行后恰为零变化），新 Handle 永不注册，引用它的边
   * 在 getEdgePosition 拿 null 后被静默丢弃（store 有边 / DOM 无边的根因）。
   * 对策：切换后调用库内部 ResizeObserver 同款 updateNodeInternals 强制重测。
   */
  const remeasureHandles = () => {
    const el = document.querySelector<HTMLDivElement>(`.react-flow__node[data-id="${id}"]`)
    if (!el) return
    const { updateNodeInternals } = storeApi.getState()
    if (updateNodeInternals) {
      updateNodeInternals(new Map([[id, { id, nodeElement: el, force: true }]]))
    }
  }

  const changeCapability = (v: string) => {
    if (v === d.capability) return
    const nextSlots = slotsFor(model, v)
    pruneEdges(Object.values(nextSlots).flat())
    setData({ capability: v, media_slots: nextSlots })
    // handle 集随模式切换增删（end_frame_0 等），需强制重测注册
    requestAnimationFrame(() => remeasureHandles())
  }

  const changeModel = (v: string) => {
    if (v === d.model) return
    // 槽位/清边必须按新模型的 spec 计算（旧闭包曾用旧模型 → 悬空边 → 保存 400）
    const nextModel = models.find(m => m.id === v) as VideoModel | undefined
    const caps = nextModel?.capabilities.map(c => c.id) || []
    const nextCap = caps.includes(d.capability as never) ? d.capability : (caps[0] as never || 't2v')
    const nextSlots = slotsFor(nextModel, nextCap)
    const validHandles = Object.values(nextSlots).flat()
    pruneEdges(validHandles)
    const resolution = nextModel?.resolutions.includes(d.resolution) ? d.resolution : (nextModel?.default_resolution || '1080P')
    const duration = (nextModel?.durations || []).includes(d.duration) ? d.duration : (nextModel?.default_duration || 5)
    setData({
      model: v,
      capability: nextCap,
      media_slots: nextSlots,
      resolution, duration,
    })
    requestAnimationFrame(() => remeasureHandles())
  }

  const run = async () => {
    if (!ctx?.activeCanvasId) return
    setRunning(true)
    try {
      await api.post(`/api/canvases/${ctx.activeCanvasId}/run`, { node_id: id })
      ctx?.refresh?.()
    } catch {} finally { setRunning(false) }
  }

  const status = ctx?.statusMap?.[id]
  const ratioOpts = model?.ratio_options?.[d.capability as 't2v' | 'i2v' | 'r2v']?.options
  const ratioList = ratioOpts && ratioOpts.length > 0 ? ratioOpts : ['16:9', '9:16', '1:1']
  // 模式下拉始终显示：未选模型时用通用能力兜底，选好后切到该模型真实能力
  const capOptions = capabilities.length > 0
    ? capabilities.map(c => ({ value: c.id as string, label: c.label }))
    : ([{ value: 't2v', label: '文生视频' }, { value: 'i2v', label: '图生视频' }, { value: 'r2v', label: '参考生视频' }] as { value: string; label: string }[])

  return (
    <div className="relative">
      <NodeShell
        title={nodeName || '生成'}
        icon={<Zap className="w-3.5 h-3.5" />}
        status={status?.status}
        className="w-72"
        onTitleDoubleClick={startRename}
        titleExtra={renaming ? (
          <span className="flex items-center gap-1 ml-2" onClick={e => e.stopPropagation()}>
            <input
              autoFocus data-testid="gen-rename-input"
              value={nameDraft}
              onChange={e => setNameDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenaming(false) }}
              placeholder="节点名称（显示于输出卡）"
              className="w-28 bg-[var(--color-surface-3)] text-[12px] text-[var(--color-ink)] rounded-md px-1.5 py-0.5 outline-none border border-[var(--color-primary)]/40"
            />
            <button onClick={commitRename} aria-label="确认名称" className="text-[var(--color-primary)] hover:text-[var(--color-primary)] shrink-0"><Check className="w-3 h-3" /></button>
            <button onClick={() => setRenaming(false)} aria-label="取消" className="text-[var(--color-ink-tertiary)] hover:text-[var(--color-danger)] shrink-0"><X className="w-3 h-3" /></button>
          </span>
        ) : (
          <button
            onClick={e => { e.stopPropagation(); startRename() }}
            title="重命名（双击标题也可）"
            aria-label="重命名生成节点"
            className="ml-1 p-0.5 rounded text-[var(--color-ink-tertiary)] hover:text-[var(--color-primary)] hover:bg-[var(--color-surface-3)] transition-colors shrink-0"
          >
            <PenLine className="w-2.5 h-2.5" />
          </button>
        )}
      >
        <div className="grid grid-cols-2 gap-2">
          <NodeSelect value={d.model} onChange={changeModel} placeholder="选择模型"
            options={[{ value: '', label: '选择模型' }, ...models.map(m => ({ value: m.id, label: m.label }))]}
          />
          <NodeSelect value={d.capability} onChange={changeCapability} options={capOptions} />
        </div>

        {/* 参数下拉行在前（需求：分辨率/比例/时长置于槽位文字标签上方）；kind 说明行在后。
            kind 标签（r2v 显示 图片/视频/音频，i2v 不渲染）跟在参数行下方 */}
        <div className="grid grid-cols-3 gap-2 mt-2">
          <NodeSelect value={d.resolution} onChange={v => setData({ resolution: v })}
            options={(model?.resolutions || ['480P', '720P', '1080P']).map(r => ({ value: r, label: r }))}
          />
          <NodeSelect value={d.ratio} onChange={v => setData({ ratio: v })}
            options={ratioList.map(r => ({ value: r, label: r }))}
          />
          <NodeSelect value={String(d.duration)} onChange={v => setData({ duration: parseInt(v) })}
            options={(model?.durations || [3, 5, 8, 10, 15]).map(s => ({ value: String(s), label: `${s}s` }))}
          />
        </div>
        {rows.length > 0 && (
          <div className="flex items-center justify-around gap-1 mt-2">
            {rows.map(r => (
              <div key={r.kind} className="flex items-center gap-1 text-[11px] text-[var(--color-ink-tertiary)]">
                <div className="w-1.5 h-1.5 rounded-full" style={{ background: HANDLE_COLORS[r.kind] }} />
                {r.label}
              </div>
            ))}
          </div>
        )}
        <textarea value={d.inlinePrompt || ''} onChange={e => setData({ inlinePrompt: e.target.value })} placeholder="附加提示词（可选），未连提示词节点时必填" rows={2} className="w-full bg-[var(--color-surface-3)] text-[12px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)] rounded-lg px-2 py-1.5 outline-none resize-none mt-2 border border-transparent focus:border-[var(--color-primary)]/30" />
        {status?.error && <p className="text-[11px] text-[var(--color-danger)] mt-1">{status.error}</p>}
        {status?.video_src && <video src={status.video_src} className="w-full rounded-lg mt-2" controls />}
        <button onClick={run} disabled={running || !d.model} className="flex items-center justify-center gap-1.5 w-full py-1.5 mt-2 text-[12px] text-white bg-brand-gradient rounded-lg hover:shadow-md disabled:opacity-40 transition-all">
          {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
          {running ? '生成中…' : '运行'}
        </button>
      </NodeShell>

      {/* Target handles: prompt 固定近顶，其后每 kind 一个点。语义色与素材 source 侧同色；
          hover 放大时弹名称标签（无数字）。 */}
      <Handle type="target" position={Position.Left} id="prompt"
        style={{ top: '12%', '--handle-color': HANDLE_COLORS.prompt } as never}
        data-handle-color="1" data-handle-label="提示词" />
      {rows.map((r, i) => (
        <Handle
          key={r.kind}
          type="target"
          position={Position.Left}
          id={r.handle}
          style={{ top: `${18 + (i + 1) * (72 / (rows.length + 1))}%`, '--handle-color': HANDLE_COLORS[r.kind] } as never}
          data-handle-color="1"
          data-handle-label={r.label}
        />
      ))}
      <Handle type="source" position={Position.Right} id="output"
        style={{ top: '50%', '--handle-color': HANDLE_COLORS.video } as never}
        data-handle-color="1" data-handle-label="视频输出" />
    </div>
  )
})

export default GenerateNode
