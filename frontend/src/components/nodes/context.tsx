import { createContext, useContext } from 'react'

import type { CanvasNodeData, CanvasRuntime } from '../../canvasTypes'
import type { VideoModel } from '../../types'

interface CanvasNodeContextValue {
  models: VideoModel[]
  maxUploadMb: number
  publicBaseUsable: boolean
  runtime: Record<string, CanvasRuntime>
  connected: Record<string, Set<string>>   // nodeId -> 已连线的输入槽
  incoming: Record<string, Record<string, string>> // nodeId -> handle -> source node id
  updateNodeData: (nodeId: string, patch: Partial<CanvasNodeData>) => void
  removeEdgesForHandle: (nodeId: string, handle: string) => void
  runNode: (nodeId: string) => Promise<void>
}

const CanvasNodeContext = createContext<CanvasNodeContextValue | null>(null)

export const CanvasNodeProvider = CanvasNodeContext.Provider

export function useCanvasNodeContext() {
  const value = useContext(CanvasNodeContext)
  if (!value) throw new Error('CanvasNodeProvider is missing')
  return value
}
