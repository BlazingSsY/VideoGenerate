export interface User {
  id: string
  username: string
  role: 'admin' | 'user'
  display_name: string
  is_active: boolean
  created_at: string
}

export interface PromptSkill {
  id: string
  name: string
  description: string
  instructions: string
  enabled: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

export type ModeId = 't2v' | 'i2v' | 'r2v'
export type MediaKind = 'image' | 'video' | 'audio'

export interface MediaInputSpec {
  kind: MediaKind
  media_type: string
  label: string
  min_count: number
  max_count: number
  accept: string
  max_mb: number
  note: string
}

export interface ReferenceMedia {
  kind: MediaKind
  url: string
  name: string
  signed_url?: string
}

export interface Capability {
  id: ModeId
  label: string
  description: string
  media_type: string
  min_images: number
  max_images: number
  ratios: string[] | null
  default_ratio: string | null
  supports_ratio: boolean
  media_inputs?: MediaInputSpec[]
  min_media?: number
}

export interface RatioOption {
  options: string[]
  default: string
}

export interface VideoModel {
  id: string
  label: string
  description: string
  capabilities: Capability[]
  resolutions: string[]
  default_resolution: string
  resolution_note: string
  supports_base64_media: boolean
  ratios: string[]
  default_ratio: string
  ratio_options: Record<string, RatioOption>
  duration_min: number
  duration_max: number
  durations: number[]
  default_duration: number
  supports_watermark: boolean
  watermark_default: boolean
  supports_audio: boolean
  audio_default: boolean
  doc_url: string
  notes: string[]
}

export interface MatrixRow {
  id: string
  label: string
  modes: ModeId[]
  doc_url: string
  allowed: boolean
}

export interface Conversation {
  id: string
  title: string
  last_model: string
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  prompt: string
  resolved_prompt: string
  model: string
  params: Record<string, any>
  reference_images: string[]
  reference_media: ReferenceMedia[]
  status: '' | 'pending' | 'running' | 'succeeded' | 'failed'
  task_id: string
  video_url: string
  local_video: string
  /** 视频文件已超过保留期被清理，记录仍在但播不了 */
  video_expired: boolean
  /** 后端签发的可直接播放的地址（带签名与有效期），优先用它 */
  video_src: string
  /** 参考图的签名地址，和 reference_images 一一对应 */
  reference_image_urls: string[]
  error: string
  elapsed_seconds: number
  created_at: string
}

export interface AppConfig {
  app_name: string
  max_duration: number
  max_upload_mb: number
  context_enabled: boolean
  public_base_url_configured: boolean
  public_base_url_usable: boolean
}

export interface AgentPlan {
  id: string
  surface: 'studio' | 'canvas'
  target_id: string
  status: string
  skill_id: string
  plan: { generations: Array<{ prompt: string; model: string; capability: ModeId; resolution: string; ratio: string; duration: number; reference_media: ReferenceMedia[]; reason: string }>; reason: string }
  est_cost: number
  est_seconds: number
}

export const MODE_COLORS: Record<ModeId, string> = {
  t2v: 'blue',
  i2v: 'geekblue',
  r2v: 'purple',
}
