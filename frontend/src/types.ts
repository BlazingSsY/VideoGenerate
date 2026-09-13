export interface User {
  id: string
  username: string
  role: 'admin' | 'user'
  display_name: string
  is_active: boolean
  created_at: string
}

export type ModeId = 't2v' | 'i2v' | 'r2v'
export type MediaKind = 'image' | 'end_frame' | 'video' | 'audio'

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

export interface AppConfig {
  app_name: string
  max_duration: number
  max_upload_mb: number
  context_enabled: boolean
  public_base_url_configured: boolean
  public_base_url_usable: boolean
}
