import type { Capability, MediaInputSpec, MediaKind, ReferenceMedia } from './types'

export function capabilityMediaInputs(capability: Capability): MediaInputSpec[] {
  if (capability.media_inputs?.length) return capability.media_inputs
  if (!capability.max_images) return []
  return [{
    kind: 'image',
    media_type: capability.media_type,
    label: capability.id === 'i2v' ? '首帧图' : '参考图',
    min_count: capability.min_images,
    max_count: capability.max_images,
    accept: '.jpg,.jpeg,.png,.webp,.bmp',
    max_mb: 20,
    note: '',
  }]
}

export function minimumMedia(capability: Capability) {
  return Math.max(
    capability.min_media ?? 0,
    capabilityMediaInputs(capability).reduce((total, item) => total + item.min_count, 0),
  )
}

export function mediaCount(items: ReferenceMedia[], kind: MediaKind) {
  return items.filter((item) => item.kind === kind).length
}

export function filterMediaForCapability(items: ReferenceMedia[], capability: Capability) {
  const limits = new Map(
    capabilityMediaInputs(capability).map((item) => [item.kind, item.max_count]),
  )
  const counts = new Map<MediaKind, number>()
  return items.filter((item) => {
    const limit = limits.get(item.kind) ?? 0
    const count = counts.get(item.kind) ?? 0
    if (count >= limit) return false
    counts.set(item.kind, count + 1)
    return true
  })
}

export function mediaKindLabel(kind: MediaKind) {
  return { image: '图片', video: '视频', audio: '音频' }[kind]
}
