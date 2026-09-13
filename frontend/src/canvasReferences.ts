import type { CanvasFlowEdge, CanvasFlowNode } from './canvasTypes'
import { slotKindOfHandle } from './canvasRules'

export type ReferenceKind = 'image' | 'video' | 'audio'
export type ReferenceBinding = { edge_id: string; alias: string; description: string }
export type ReferenceRow = ReferenceBinding & { kind: ReferenceKind; name: string; tag: string; source: string }
const names = { image: '图片', video: '视频', audio: '音频' }

export function referenceTag(model: string, kind: ReferenceKind, number: number) {
  if (model.startsWith('happyhorse')) return `[${{ image: 'Image', video: 'Video', audio: 'Audio' }[kind]} ${number}]`
  if (model.startsWith('wan')) return `${{ image: '图', video: '视频', audio: '音频' }[kind]} ${number}`
  return `第 ${number} ${kind === 'image' ? '张参考图片' : kind === 'video' ? '段参考视频' : '段参考音频'}`
}

export function canvasReferences(id: string, data: Record<string, unknown>, nodes: CanvasFlowNode[], edges: CanvasFlowEdge[]) {
  const saved: ReferenceBinding[] = Array.isArray(data.reference_bindings) ? data.reference_bindings : []
  const byId = new Map(nodes.map(node => [node.id, node]))
  const connected = edges.filter(edge => edge.target === id && ['image', 'video', 'audio'].includes(slotKindOfHandle(edge.targetHandle || '') || '') && byId.has(edge.source))
  const rank = new Map(saved.map((item, index) => [item.edge_id, index]))
  const used = new Set(saved.map(item => item.alias))
  const rows: ReferenceRow[] = []
  for (const kind of ['image', 'video', 'audio'] as const) {
    const group = connected.filter(edge => slotKindOfHandle(edge.targetHandle || '') === kind)
      .sort((a, b) => (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity))
    for (const [index, edge] of group.entries()) {
      const existing = saved.find(item => item.edge_id === edge.id)
      let next = 1
      while (used.has(`${names[kind]}${next}`)) next++
      const alias = existing?.alias || `${names[kind]}${next}`
      used.add(alias)
      const source = byId.get(edge.source)!
      rows.push({ edge_id: edge.id, alias, description: existing?.description || '', kind,
        name: String(source.data.name || source.data.label || `${names[kind]}素材`), source: source.id,
        tag: referenceTag(String(data.model || ''), kind, index + 1) })
    }
  }
  // Keep retired aliases reserved, so reconnecting a different asset cannot
  // silently reuse a reference that remains in the user's prompt.
  const bindings = [...rows.map(({ edge_id, alias, description }) => ({ edge_id, alias, description })),
    ...saved.filter(item => !rows.some(row => row.edge_id === item.edge_id))]
  const promptEdge = edges.find(edge => edge.target === id && edge.targetHandle === 'prompt')
  const connectedPrompt = String(byId.get(promptEdge?.source || '')?.data.text || '').trim()
  const inlinePrompt = String(data.inlinePrompt || '').trim()
  // Merely opening or moving a legacy node must not change its frozen inputs.
  const enabled = saved.length > 0 || /\{\{[^{}]+\}\}/.test(connectedPrompt + inlinePrompt)
  const prompt = [connectedPrompt, !promptEdge || enabled ? inlinePrompt : ''].filter(Boolean).join('\n\n')
  return { rows, bindings, enabled, prompt }
}

export function referencePrompt(prompt: string, rows: ReferenceRow[], enabled = true) {
  const missing = new Set<string>()
  const resolved = prompt.replace(/\{\{([^{}]+)\}\}/g, (token, alias: string) => {
    const row = rows.find(item => item.alias === alias.trim())
    if (!row) { missing.add(alias.trim()); return token }
    return row.tag
  })
  const legend = rows.map(row => `${row.tag}：${row.description.trim() || row.name}`)
  return { prompt: resolved + (enabled && legend.length ? `\n\n参考素材对应关系（各类型分别按发送顺序编号）：\n${legend.join('\n')}` : ''), missing: [...missing] }
}
