import { createContext, useCallback, useContext, type MutableRefObject } from 'react'
import { useReactFlow } from '@xyflow/react'
import type { VideoModel } from '../../types'

export interface CanvasNodeContextValue {
  models: VideoModel[]
  activeCanvasId: string | null
  activeCanvasIdRef?: MutableRefObject<string | null>
  statusMap: Record<string, { status: string; message_id: string | null; video_src?: string; error?: string }>
  refresh?: () => void
  canvasRunning?: boolean
  runNode?: (id: string) => Promise<void>
  composeOutput?: (id: string) => Promise<void>
}

export const CanvasNodeContext = createContext<CanvasNodeContextValue | null>(null)
export const CanvasNodeProvider = CanvasNodeContext.Provider

/**
 * Node components receive `data` from ReactFlow, but must never mutate it in
 * place — ReactFlow compares node.data by reference to decide re-renders.
 * This helper is the single sanctioned way for nodes to patch their own data.
 */
export function useUpdateNodeData(id: string, data: Record<string, unknown>) {
  const { setNodes } = useReactFlow()
  const context = useContext(CanvasNodeContext)
  const canvasId = context?.activeCanvasId
  const activeCanvasIdRef = context?.activeCanvasIdRef
  return useCallback((patch: Record<string, unknown>) => {
    // Offscreen nodes can finish uploading; nodes on a different canvas cannot
    // patch a reused ID in the newly selected graph.
    if (activeCanvasIdRef && activeCanvasIdRef.current !== canvasId) return
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n))
  }, [id, setNodes, canvasId, activeCanvasIdRef])
}
