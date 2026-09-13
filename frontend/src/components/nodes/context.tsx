import { createContext, useCallback, useContext } from 'react'
import { useReactFlow } from '@xyflow/react'
import type { VideoModel } from '../../types'

export interface CanvasNodeContextValue {
  models: VideoModel[]
  activeCanvasId: string | null
  statusMap: Record<string, { status: string; message_id: string | null; video_src?: string; error?: string }>
  refresh?: () => void
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
  return useCallback((patch: Record<string, unknown>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...data, ...patch } } : n))
  }, [id, data, setNodes])
}
