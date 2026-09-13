import type { CanvasFlowEdge, CanvasFlowNode } from './canvasTypes'
import { canvasReferences } from './canvasReferences'

export type OutputItem = { nodeKey: string; label?: string; model?: string }

export function orderedOutputItems(id: string, data: Record<string, unknown>, nodes: CanvasFlowNode[], edges: CanvasFlowEdge[]): OutputItem[] {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const connected = [...new Set(edges.filter(edge => edge.target === id && edge.targetHandle === 'input'
    && byId.get(edge.source)?.type === 'generate').map(edge => edge.source))]
  const saved: OutputItem[] = Array.isArray(data.items) ? data.items : []
  const excluded = new Set(Array.isArray(data.excluded) ? data.excluded : [])
  const keys = [...new Set(saved.map(item => item.nodeKey).filter(key => connected.includes(key) && !excluded.has(key)))]
  keys.push(...connected.filter(key => !keys.includes(key) && !excluded.has(key)))
  return keys.map(nodeKey => ({ ...saved.find(item => item.nodeKey === nodeKey), nodeKey }))
}

export function serializeCanvasNodes(nodes: CanvasFlowNode[], edges: CanvasFlowEdge[]) {
  return nodes.map(node => {
    const references = node.type === 'generate' && node.data.capability === 'r2v'
      ? canvasReferences(node.id, node.data, nodes, edges) : undefined
    return {
      id: node.id, type: node.type!, position: node.position,
      data: node.type === 'output' ? { ...node.data, items: orderedOutputItems(node.id, node.data, nodes, edges) }
        : references?.enabled ? { ...node.data, reference_bindings: references.bindings } : node.data,
      status: '', message_id: null,
    }
  })
}
