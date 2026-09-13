import axios from 'axios'

export const TOKEN_KEY = 'vg_token'

const api = axios.create({ baseURL: '/' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export function errorText(error: any, fallback = '操作失败'): string {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  return error?.message || fallback
}

export default api

// ── v3: Agent 对话式智能体 API ─────────────────────────────────

export async function createAgentTurn(payload: {
  surface: 'studio' | 'canvas'
  target_id?: string
  user_input: string
  agent_model_id?: string | null
  target_duration?: number | null
  autonomy?: 'ask' | 'auto'
  reference_media?: any[]
  session_id?: string | null
}) {
  const res = await api.post('/api/agent/turns', payload)
  return res.data as { id: string; session_id: string; status: string }
}

// Fetch available agent models from /api/agent/models (v1.5 compat endpoint)
export async function getAgentModels() {
  const res = await api.get('/api/agent/models')
  return res.data as Array<{ id: string; name: string; supports_json: boolean }>
}

export async function acceptTurnPlan(turnId: string) {
  const res = await api.post(`/api/agent/turns/${turnId}/plan/accept`)
  return res.data
}

export async function planToCanvas(turnId: string) {
  const res = await api.post(`/api/agent/turns/${turnId}/plan/to-canvas`)
  return res.data as { canvas_id: string; added: boolean; revision: number; node_map: Record<string, any>; canvas: any }
}

export interface AgentRunTask {
  id: string
  node_id: string
  canvas_node_id: string
  task_type: string
  status: string
  depends_on: string[]
  attempt_count: number
  message_id?: string | null
  output_file?: string
  video_src?: string
  error?: string
}

export interface AgentRunSnapshot {
  id: string
  turn_id: string
  canvas_id?: string | null
  canvas_revision: number
  plan_version: string
  status: string
  cancel_requested: boolean
  output_file?: string
  video_src?: string
  error?: string
  tasks: AgentRunTask[]
}

export async function getAgentRun(runId: string) {
  const res = await api.get(`/api/agent/runs/${runId}`)
  return res.data as AgentRunSnapshot
}

export async function getSessionRuns(sessionId: string) {
  const res = await api.get(`/api/agent/sessions/${sessionId}/runs`)
  return res.data as AgentRunSnapshot[]
}

export async function getAgentSessionMessages(sessionId: string) {
  const res = await api.get(`/api/agent/sessions/${sessionId}/messages`)
  return res.data as Array<{
    id: string
    session_id: string
    turn_id?: string | null
    role: 'user' | 'assistant'
    content: string
    plan?: any | null
    plan_validated: boolean
    turn_status: string
    est_cost: number
    est_seconds: number
    warning: string
    created_at: string
  }>
}

export async function cancelAgentRun(runId: string) {
  const res = await api.post(`/api/agent/runs/${runId}/cancel`)
  return res.data as AgentRunSnapshot
}

export async function retryAgentRun(runId: string) {
  const res = await api.post(`/api/agent/runs/${runId}/retry`)
  return res.data as AgentRunSnapshot
}

export function subscribeAgentEvents(
  turnId: string,
  fromSeq: number,
  onEvent: (eventType: string, data: any) => void,
  onError?: (err: any) => void,
): () => void {
  const token = localStorage.getItem(TOKEN_KEY)
  const controller = new AbortController()
  let currentEvent = 'message'
  let completed = false

  fetch(`/api/agent/turns/${turnId}/events?from_seq=${fromSeq}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const reader = response.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      // currentEvent 必须跨 chunk 存活：event: 行可能在上一 chunk，data: 行在下一 chunk
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            onEvent(currentEvent, data)
            if (currentEvent === 'done' || currentEvent === 'error') completed = true
          } catch { /* skip */ }
        }
      }
    }
    if (!completed && !controller.signal.aborted && onError) {
      onError(new Error('规划事件流意外中断'))
    }
  }).catch((err) => {
    if (err.name !== 'AbortError' && onError) onError(err)
  })

  return () => controller.abort()
}

export function subscribeAgentRunEvents(
  runId: string,
  fromSeq: number,
  onEvent: (eventType: string, data: any) => void,
  onError?: (err: any) => void,
): () => void {
  const token = localStorage.getItem(TOKEN_KEY)
  const controller = new AbortController()
  let currentEvent = 'message'
  let completed = false

  fetch(`/api/agent/runs/${runId}/events?from_seq=${fromSeq}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const reader = response.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('event: ')) currentEvent = line.slice(7).trim()
        else if (line.startsWith('data: ')) {
          try {
            onEvent(currentEvent, JSON.parse(line.slice(6)))
            if (currentEvent === 'done') completed = true
          } catch { /* ignore malformed event */ }
        }
      }
    }
    if (!completed && !controller.signal.aborted && onError) {
      onError(new Error('运行事件流意外中断'))
    }
  }).catch((err) => {
    if (err.name !== 'AbortError' && onError) onError(err)
  })
  return () => controller.abort()
}
