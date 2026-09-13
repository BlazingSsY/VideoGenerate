import type { Edge, Node, Viewport } from '@xyflow/react'

import type { MediaKind, ModeId } from './types'

export type CanvasNodeType = 'prompt' | 'image' | 'video' | 'audio' | 'generate' | 'output'
export type CanvasNodeStatus = '' | 'idle' | 'pending' | 'running' | 'succeeded' | 'failed'

export interface PromptNodeData extends Record<string, unknown> {
  text: string
}

export interface ImageNodeData extends Record<string, unknown> {
  url: string           // 规范相对路径或外链，存库用
  name: string
  signed_url?: string   // 后端签发的显示地址，仅用于 <img src>
}

export interface MediaNodeData extends Record<string, unknown> {
  kind: Exclude<MediaKind, 'image'>
  url: string
  name: string
  signed_url?: string
}

export interface OutputNodeData extends Record<string, unknown> {
  label: string
  outputFile?: string
  outputVideoSrc?: string
}

export interface GenerateNodeData extends Record<string, unknown> {
  model: string
  capability: ModeId
  resolution: string
  ratio: string
  duration: number
  watermark: boolean
  audio: boolean
  inlinePrompt: string
  media_slots?: Partial<Record<MediaKind, string[]>>
}

export type CanvasNodeData =
  | PromptNodeData
  | ImageNodeData
  | MediaNodeData
  | GenerateNodeData
  | OutputNodeData
export type CanvasFlowNode = Node<CanvasNodeData, CanvasNodeType>
export type CanvasFlowEdge = Edge

export interface CanvasRuntime {
  status: CanvasNodeStatus
  message_id: string | null
  video_src?: string
  video_expired?: boolean
  error?: string
}

export interface CanvasSummary {
  id: string
  title: string
  viewport: Viewport
  created_at: string
  updated_at: string
  revision: number
  control_version: number
}

export interface CanvasNodeRecord {
  id: string
  type: CanvasNodeType
  position: { x: number; y: number }
  size?: { width: number; height: number } | null
  data: CanvasNodeData
  status: CanvasNodeStatus
  message_id: string | null
}

export interface CanvasEdgeRecord {
  id: string
  source: string
  source_handle: string
  target: string
  target_handle: string
}

export interface CanvasDetail extends CanvasSummary {
  nodes: CanvasNodeRecord[]
  edges: CanvasEdgeRecord[]
}
