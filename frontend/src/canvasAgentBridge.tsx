import { createContext, useContext } from 'react'

export interface CanvasAgentBinding {
  canvasId: string | null
  save: () => Promise<void>
  reload: () => Promise<void>
  refreshStatus: () => Promise<void>
  takeOver: () => Promise<void>
}

export interface CanvasAgentBridgeValue {
  binding: CanvasAgentBinding | null
  setBinding: (binding: CanvasAgentBinding | null) => void
}

export const CanvasAgentBridgeContext = createContext<CanvasAgentBridgeValue>({
  binding: null,
  setBinding: () => {},
})

export function useCanvasAgentBridge() {
  return useContext(CanvasAgentBridgeContext)
}
