import { useMemo } from 'react'

import type { ModeId, VideoModel } from '../types'

export interface ModelParamState {
  model: string
  capability: ModeId
  resolution: string
  ratio: string
  duration: number
  watermark: boolean
  audio: boolean
}

export function defaultModelParams(model: VideoModel, mode?: ModeId): ModelParamState {
  const capability = (mode && model.capabilities.find((item) => item.id === mode)) || model.capabilities[0]
  return {
    model: model.id,
    capability: capability.id,
    resolution: model.default_resolution,
    ratio: model.ratio_options[capability.id]?.default ?? '',
    duration: model.default_duration,
    watermark: model.watermark_default,
    audio: model.audio_default,
  }
}

export function nextModelParams(
  model: VideoModel,
  current: ModelParamState,
  mode?: ModeId,
): ModelParamState {
  const capability = (mode && model.capabilities.find((item) => item.id === mode)) || model.capabilities[0]
  const ratio = model.ratio_options[capability.id]
  return {
    model: model.id,
    capability: capability.id,
    resolution: model.resolutions.includes(current.resolution)
      ? current.resolution
      : model.default_resolution,
    ratio: ratio?.options.includes(current.ratio) ? current.ratio : (ratio?.default ?? ''),
    duration: model.durations.includes(current.duration) ? current.duration : model.default_duration,
    watermark: model.watermark_default,
    audio: model.audio_default,
  }
}

export function useModelParams(
  models: VideoModel[],
  state: ModelParamState,
  onChange: (patch: Partial<ModelParamState>) => void,
) {
  // 存的模型可能已下线，或用户被降级后无权使用——这种情况要能被调用方识别，
  // 而不是悄悄回退到列表第一个模型（那样界面显示的和实际提交的会对不上）
  const found = useMemo(
    () => models.find((item) => item.id === state.model),
    [models, state.model],
  )
  const model = found ?? (state.model ? undefined : models[0])
  const unavailable = Boolean(state.model) && !found
  const capability = useMemo(
    () => model?.capabilities.find((item) => item.id === state.capability) || model?.capabilities[0],
    [model, state.capability],
  )
  const ratioOptions = capability ? (model?.ratio_options[capability.id]?.options ?? []) : []

  const applyModel = (nextModel: VideoModel, mode?: ModeId) => {
    onChange(nextModelParams(nextModel, state, mode))
  }

  return { model, capability, ratioOptions, applyModel, unavailable }
}
