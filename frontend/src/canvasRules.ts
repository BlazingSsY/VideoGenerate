import type { CanvasFlowEdge, CanvasFlowNode } from './canvasTypes'
import type { MediaKind, VideoModel } from './types'

/**
 * Mirrors backend canvas_graph._input_type / _output_type (the single source of
 * truth that also validates every save). Frontend uses it for connect-time
 * rejection so users never draw an edge the backend would reject with 400.
 */

function specList(model: VideoModel | undefined, capabilityId: string) {
  return model?.capabilities.find(c => c.id === capabilityId)?.media_inputs || []
}

function sourceHandleType(node: CanvasFlowNode, handle: string): string | null {
  switch (node.type) {
    case 'prompt': return handle === 'prompt' ? 'text' : null
    // 图片素材单一 handle：`image` 源既能落到 image 槽也能落到 end_frame 槽（由目标槽颜色区分语义）
    case 'image': return handle === 'image' ? 'image-or-end-frame' : null
    case 'video': return handle === 'video' ? 'video' : null
    case 'audio': return handle === 'audio' ? 'audio' : null
    case 'generate': return handle === 'output' ? 'video' : null
    default: return null
  }
}

/**
 * Effective media slots for a generate node, mirroring backend media_handles():
 * declared media_slots win — 显式 `[]` 表示该 kind 零槽（尾帧图移除到底）；
 * 未声明（undefined）的 kind 默认一槽 `{kind}_0`。t2v yields no media slots.
 */
export function generateMediaSlots(
  model: VideoModel | undefined,
  capabilityId: string,
  declared: Partial<Record<MediaKind, string[]>> | undefined,
): Partial<Record<MediaKind, string[]>> {
  const specs = specList(model, capabilityId)
  const result: Partial<Record<MediaKind, string[]>> = {}
  for (const spec of specs) {
    const declaredList = declared?.[spec.kind]
    const list = Array.isArray(declaredList)
      ? declaredList
      : [`${spec.kind}_0`]
    result[spec.kind] = list.map((_, i) => `${spec.kind}_${i}`).slice(0, spec.max_count)
  }
  return result
}

/**
 * Target handle types a generate node accepts: `prompt` + every rendered
 * media slot. end_frame slots accept `end_frame` typed sources; other kinds
 * accept their own kind.
 */
export function generateTargetHandleTypes(
  model: VideoModel | undefined,
  capabilityId: string,
  declared: Partial<Record<MediaKind, string[]>> | undefined,
): Record<string, string> {
  const types: Record<string, string> = { prompt: 'text' }
  const slots = generateMediaSlots(model, capabilityId, declared)
  for (const [kind, handles] of Object.entries(slots)) {
    for (const handle of handles || []) {
      types[handle] = kind === 'end_frame' ? 'end_frame' : kind
    }
  }
  return types
}

/** slotKindOf 等价物，供路由/净化使用：`image_3` / `end_frame_0` → kind */
export function slotKindOfHandle(handle: string): string | null {
  if (handle.startsWith('end_frame_')) return 'end_frame'
  const m = /^([a-z]+)_\d+$/.exec(handle)
  if (m && ['image', 'video', 'audio', 'end_frame'].includes(m[1])) return m[1]
  return null
}

/**
 * Legacy canvases may carry edges aimed at bare handle names (`image`,
 * `video`, ...) or at slots the node no longer renders (capability 切换后
 * 悬空的 `image_2` 等 — 会令后端 validate_graph 对每次保存都 400).
 * Remap onto the kind's first surviving slot; drop the edge if the kind is
 * gone entirely.
 */
export function migrateLegacyTargetHandle(
  handle: string,
  targetTypes: Record<string, string>,
): string | null {
  if (targetTypes[handle] !== undefined) return handle
  const kind = /^(image|end_frame|video|audio)(_|$)/.exec(handle)?.[1]
  if (!kind) return handle // unknown handles stay as-is; connect rules will reject
  const prefix = `${kind}_`
  const survivor = Object.keys(targetTypes).find(h => h.startsWith(prefix))
  return survivor !== undefined ? survivor : null
}

export function isValidCanvasConnection(
  c: { source: string; target: string; sourceHandle: string | null; targetHandle: string | null },
  nodes: CanvasFlowNode[],
  models: VideoModel[],
): boolean {
  if (!c.source || !c.target || !c.sourceHandle || !c.targetHandle) return false
  const source = nodes.find(n => n.id === c.source)
  const target = nodes.find(n => n.id === c.target)
  if (!source || !target || source === target) return false
  const sourceType = sourceHandleType(source, c.sourceHandle)
  if (sourceType === null) return false
  if (target.type === 'output') return c.targetHandle === 'input' && sourceType === 'video'
  if (target.type !== 'generate') return false
  if (c.targetHandle === 'prompt') return sourceType === 'text'
  const d = target.data as GenerateNodeDataLike
  const model = models.find(m => m.id === d.model)
  const slots = generateMediaSlots(model, d.capability, d.media_slots)
  for (const [kind, handles] of Object.entries(slots)) {
    if (handles?.includes(c.targetHandle)) {
      const want = kind === 'end_frame' ? 'end_frame' : kind
      // image-or-end-frame：单一图片源 handle 允许落 image 槽或 end_frame 槽
      return sourceType === want || (sourceType === 'image-or-end-frame' && (want === 'image' || want === 'end_frame'))
    }
  }
  return false
}

function pairTypeCompatible(sourceType: string, targetType: string): boolean {
  if (sourceType === targetType) return true
  return sourceType === 'image-or-end-frame' && (targetType === 'image' || targetType === 'end_frame')
}

/**
 * 该 handle 是否是聚合媒体点（r2v 参考图片/视频/音频 — max_count > 1 的 kind）。
 * 聚合点允许多条边共连一个视觉点；单槽类（首帧图/尾帧图）仍一槽一边。
 * handle 通常是 declared 首槽 `{kind}_0`（GenerateNode 每类只渲染一个点）。
 */
export function isAggregateTargetHandle(
  handle: string,
  target: { data?: unknown },
  models: VideoModel[],
): boolean {
  const d = target.data as GenerateNodeDataLike | undefined
  if (!d) return false
  const model = models.find(m => m.id === d.model)
  const kind = slotKindOfHandle(handle)
  const spec = kind
    ? model?.capabilities.find(c => c.id === d.capability)?.media_inputs?.find(s => s.kind === kind)
    : undefined
  return Boolean(spec && spec.max_count > 1)
}
/**
 * 加载期净化器：把历史各代代码写入库里的脏边在展示前修正/清理，
 * 保证随后任何一次保存都能通过后端 validate_graph——
 * 否则一条悬空边会让「保存」永远 400，且错误被静默吞掉（用户看到的就是
 * “点了保存没反应、刷新回到初始布局”）。
 * 修正规则（对目标为 generate 的边）：
 *   - 裸名（image/video/...）或悬空 `kind_N` → 重挂该 kind 的第一个（聚合）槽；
 *   - kind 已不被该节点渲染 → 丢弃；
 *   - 类型不兼容（如视频源连图片槽）→ 丢弃；
 *   - 聚合槽（max_count > 1）允许多条边；单槽类重复 → 保留第一条。
 * 其他节点（output 等）仅做类型校验。
 */
export function sanitizeLoadedEdges(
  flowEdges: CanvasFlowEdge[],
  flowNodes: CanvasFlowNode[],
  models: VideoModel[],
): CanvasFlowEdge[] {
  const nodesById = new Map(flowNodes.map(n => [n.id, n]))
  const out: CanvasFlowEdge[] = []
  const seenTargets = new Set<string>()
  for (let e of flowEdges) {
    const source = nodesById.get(e.source)
    const target = nodesById.get(e.target)
    if (!source || !target) continue // 引用不存在的节点
    // 源侧迁移：双 handle 时代 image 节点曾带 end_frame 源，统一改挂 image
    if (e.sourceHandle === 'end_frame' && source.type === 'image') {
      e = { ...e, sourceHandle: 'image' }
    }
    const sourceType = sourceHandleType(source, e.sourceHandle || '')
    if (sourceType === null) continue
    if (target.type === 'output') {
      if (e.targetHandle !== 'input' || sourceType !== 'video') continue
      out.push(e)
      continue
    }
    if (target.type !== 'generate') continue
    if (e.targetHandle === 'prompt') {
      if (sourceType !== 'text') continue
      out.push(e)
      continue
    }
    if (!e.targetHandle) continue
    const d = target.data as GenerateNodeDataLike
    const model = models.find(m => m.id === d.model)
    const targetTypes = generateTargetHandleTypes(model, d.capability, d.media_slots)
    let handle: string | null = e.targetHandle
    if (targetTypes[handle] === undefined) {
      handle = migrateLegacyTargetHandle(handle, targetTypes)
      if (handle === null || targetTypes[handle] === undefined) continue
    }
    const targetType = targetTypes[handle] as string
    if (!pairTypeCompatible(sourceType, targetType)) continue
    // 聚合槽（r2v）多边合法；单槽类（首帧/尾帧/prompt）各保留第一条
    const aggregate = isAggregateTargetHandle(handle, target, models)
    const key = `${e.target}|${handle}`
    if (!aggregate && seenTargets.has(key)) continue
    seenTargets.add(key)
    out.push(handle === e.targetHandle ? e : { ...e, targetHandle: handle })
  }
  return out
}

interface GenerateNodeDataLike {
  model: string
  capability: string
  media_slots?: Partial<Record<MediaKind, string[]>>
}
