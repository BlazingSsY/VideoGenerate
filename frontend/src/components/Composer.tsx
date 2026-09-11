import { useState } from 'react'
import {
  App as AntApp,
  Button,
  Input,
  Segmented,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import {
  DeleteOutlined,
  HighlightOutlined,
  HistoryOutlined,
  ExperimentOutlined,
  LinkOutlined,
  PictureOutlined,
  SendOutlined,
  SoundOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'

import api, { errorText } from '../api'
import { defaultModelParams, nextModelParams, useModelParams } from '../hooks/useModelParams'
import {
  capabilityMediaInputs,
  filterMediaForCapability,
  mediaCount,
  mediaKindLabel,
  minimumMedia,
} from '../mediaCapabilities'
import type { ModelParamState } from '../hooks/useModelParams'
import type {
  MediaInputSpec,
  MediaKind,
  ModeId,
  PromptSkill,
  ReferenceMedia,
  VideoModel,
} from '../types'
import { MODE_COLORS } from '../types'

export type RefMedia = ReferenceMedia

export interface ComposerState extends ModelParamState {
  useContext: boolean
  skillId: string
  prompt: string
  refMedia: RefMedia[]
}

interface Props {
  models: VideoModel[]
  skills: PromptSkill[]
  state: ComposerState
  onChange: (patch: Partial<ComposerState>) => void
  onSubmit: () => void
  sending: boolean
  maxDuration: number
  maxUploadMb: number
  publicBaseUsable: boolean
}

export function stateForModel(model: VideoModel, mode?: ModeId): ComposerState {
  return {
    ...defaultModelParams(model, mode),
    useContext: true,
    skillId: '',
    prompt: '',
    refMedia: [],
  }
}

const ratioLabel = (ratio: string) => (ratio === 'adaptive' ? '自适应' : ratio)

export default function Composer({
  models,
  skills,
  state,
  onChange,
  onSubmit,
  sending,
  maxDuration,
  maxUploadMb,
  publicBaseUsable,
}: Props) {
  const { message } = AntApp.useApp()
  const [uploadingKind, setUploadingKind] = useState<MediaKind | null>(null)
  const [linkInput, setLinkInput] = useState('')
  const [linkKind, setLinkKind] = useState<MediaKind>('image')
  const { model, capability, ratioOptions } = useModelParams(models, state, onChange)

  if (!model || !capability) return null

  const mediaSpecs = capabilityMediaInputs(capability)
  const needsMedia = mediaSpecs.length > 0
  const activeLinkKind = mediaSpecs.some((item) => item.kind === linkKind)
    ? linkKind
    : mediaSpecs[0]?.kind
  const missingSpec = mediaSpecs.find(
    (item) => mediaCount(state.refMedia, item.kind) < item.min_count,
  )
  const missingTotal = Math.max(0, minimumMedia(capability) - state.refMedia.length)
  const missingMedia = Boolean(missingSpec || missingTotal)
  const missingMediaText = missingSpec
    ? `还需添加 ${missingSpec.min_count - mediaCount(state.refMedia, missingSpec.kind)} 个${missingSpec.label}`
    : missingTotal
      ? `还需添加 ${missingTotal} 个参考素材`
      : ''
  const canSend = !!state.prompt.trim() && !missingMedia && !sending

  const applyModel = (nextModel: VideoModel, mode?: ModeId) => {
    const next = nextModelParams(nextModel, state, mode)
    const nextCapability =
      nextModel.capabilities.find((item) => item.id === next.capability) || nextModel.capabilities[0]
    onChange({
      ...next,
      skillId: nextCapability.id === 't2v' ? state.skillId : '',
      refMedia: filterMediaForCapability(state.refMedia, nextCapability),
    })
  }

  const addMedia = (item: RefMedia, spec: MediaInputSpec) => {
    if (mediaCount(state.refMedia, spec.kind) >= spec.max_count) {
      message.warning(`${spec.label}最多 ${spec.max_count} 个`)
      return
    }
    onChange({ refMedia: [...state.refMedia, item] })
  }

  const upload = async (file: File, spec: MediaInputSpec) => {
    const limitMb = Math.min(maxUploadMb, spec.max_mb)
    if (file.size > limitMb * 1024 * 1024) {
      message.error(`${spec.label}不能超过 ${limitMb}MB`)
      return
    }
    setUploadingKind(spec.kind)
    try {
      const data = new FormData()
      data.append('file', file)
      const response = await api.post('/api/uploads', data)
      addMedia({
        kind: spec.kind,
        url: response.data.url,
        signed_url: response.data.preview_url,
        name: file.name,
      }, spec)
    } catch (error) {
      message.error(errorText(error, '上传失败'))
    } finally {
      setUploadingKind(null)
    }
  }

  const addLink = () => {
    const value = linkInput.trim()
    if (!value || !activeLinkKind) return
    if (!/^https?:\/\//i.test(value)) {
      message.error('请填写以 http(s):// 开头的素材地址')
      return
    }
    const spec = mediaSpecs.find((item) => item.kind === activeLinkKind)
    if (!spec) return
    addMedia({
      kind: activeLinkKind,
      url: value,
      name: `外链${mediaKindLabel(activeLinkKind)}`,
    }, spec)
    setLinkInput('')
  }

  const placeholder =
    capability.id === 'r2v'
      ? '描述画面，并用“图 1”“视频 1”“音频 1”指名引用参考素材……'
      : capability.id === 'i2v'
        ? '描述这张图要怎么动起来：镜头如何运动、主体做什么动作……'
        : '描述你想要的画面、镜头与风格；后续可继续说“把猫换成狗”“节奏再快一点”……'

  return (
    <div className="composer">
      <div className="composer-panel">
        <div className="composer-param-bar">
          <div className="composer-control composer-control-model">
            <Select
              aria-label="模型"
              variant="borderless"
              value={state.model}
              onChange={(id) => {
                const next = models.find((item) => item.id === id)
                if (next) applyModel(next)
              }}
              options={models.map((item) => ({ value: item.id, label: item.label }))}
              optionRender={(option) => {
                const item = models.find((candidate) => candidate.id === option.value)
                if (!item) return option.label
                return (
                  <div className="composer-model-option">
                    <div>
                      <span>{item.label}</span>
                      <Space size={4} style={{ marginLeft: 7 }}>
                        {item.capabilities.map((entry) => (
                          <Tag key={entry.id} color={MODE_COLORS[entry.id]}>{entry.label}</Tag>
                        ))}
                      </Space>
                    </div>
                    <Typography.Text type="secondary">{item.description}</Typography.Text>
                  </div>
                )
              }}
            />
          </div>

          <div className="composer-control composer-control-mode">
            {model.capabilities.length > 1 ? (
              <Segmented
                aria-label="生成方式"
                value={capability.id}
                onChange={(value) => applyModel(model, value as ModeId)}
                options={model.capabilities.map((item) => ({
                  value: item.id,
                  label: item.label.replace('视频', ''),
                }))}
              />
            ) : (
              <span className={`composer-mode-badge ${capability.id}`}>{capability.label}</span>
            )}
          </div>

          <Tooltip title={`${model.resolution_note || '输出清晰度'}。可选：${model.resolutions.join(' / ')}`}>
            <div className="composer-control composer-control-small">
              <Select
                aria-label="清晰度"
                variant="borderless"
                value={state.resolution}
                onChange={(resolution) => onChange({ resolution })}
                options={model.resolutions.map((item) => ({ value: item, label: item }))}
              />
            </div>
          </Tooltip>

          <div className="composer-control composer-control-small">
            {ratioOptions.length ? (
              <Select
                aria-label="画面比例"
                variant="borderless"
                value={state.ratio}
                onChange={(ratio) => onChange({ ratio })}
                options={ratioOptions.map((item) => ({ value: item, label: ratioLabel(item) }))}
              />
            ) : (
              <span className="composer-control-value">跟随原图</span>
            )}
          </div>

          <Tooltip title={`官方支持 ${model.duration_min}-${model.duration_max} 秒，本系统最长 ${maxDuration} 秒`}>
            <div className="composer-control composer-control-small">
              <Select
                aria-label="视频时长"
                variant="borderless"
                value={state.duration}
                onChange={(duration) => onChange({ duration })}
                options={model.durations.map((item) => ({ value: item, label: `${item} 秒` }))}
              />
            </div>
          </Tooltip>
        </div>

        {needsMedia && (
          <div className="composer-media-section">
            <div className="composer-media-list">
              {state.refMedia.map((item, index) => {
                const itemNumber = state.refMedia
                  .slice(0, index + 1)
                  .filter((candidate) => candidate.kind === item.kind).length
                const token = `${mediaKindLabel(item.kind)} ${itemNumber}`
                const preview = item.signed_url || item.url
                return (
                  <div className={`composer-media-card ${item.kind}`} key={`${item.url}-${index}`}>
                    {item.kind === 'image' ? (
                      <img src={preview} alt={token} />
                    ) : item.kind === 'video' ? (
                      <video src={preview} muted preload="metadata" playsInline />
                    ) : (
                      <div className="composer-media-placeholder">
                        <SoundOutlined />
                        <span>{item.name || token}</span>
                      </div>
                    )}
                    <button
                      type="button"
                      className="composer-media-remove"
                      aria-label={`删除${token}`}
                      onClick={() =>
                        onChange({
                          refMedia: state.refMedia.filter((_, itemIndex) => itemIndex !== index),
                        })
                      }
                    >
                      <DeleteOutlined />
                    </button>
                    {capability.id === 'r2v' ? (
                      <button
                        type="button"
                        className="composer-media-token"
                        onClick={() => onChange({ prompt: `${state.prompt}${token}` })}
                      >
                        {token}
                      </button>
                    ) : (
                      <span className="composer-media-token">首帧</span>
                    )}
                  </div>
                )
              })}

              {mediaSpecs
                .filter((spec) => mediaCount(state.refMedia, spec.kind) < spec.max_count)
                .map((spec) => (
                <Upload
                  key={spec.kind}
                  className="composer-upload-wrap"
                  showUploadList={false}
                  accept={spec.accept}
                  beforeUpload={(file) => {
                    upload(file as File, spec)
                    return false
                  }}
                >
                  <button
                    type="button"
                    className={`composer-upload-card ${spec.kind}`}
                    disabled={uploadingKind !== null}
                  >
                    <span className="composer-upload-icon">
                      {spec.kind === 'image'
                        ? <PictureOutlined />
                        : spec.kind === 'video'
                          ? <VideoCameraOutlined />
                          : <SoundOutlined />}
                    </span>
                    <span className="composer-upload-copy">
                      <strong>
                        {uploadingKind === spec.kind ? '正在上传…' : `添加${spec.label}`}
                      </strong>
                      <small>{spec.note || spec.accept.replace(/\./g, '').toUpperCase()}</small>
                    </span>
                    <span className="composer-upload-count">
                      {mediaCount(state.refMedia, spec.kind)}/{spec.max_count}
                    </span>
                  </button>
                </Upload>
              ))}
            </div>

            {mediaSpecs.some((spec) => mediaCount(state.refMedia, spec.kind) < spec.max_count) && (
              <div className="composer-link-input">
                <LinkOutlined />
                {mediaSpecs.length > 1 && activeLinkKind && (
                  <Select
                    variant="borderless"
                    value={activeLinkKind}
                    onChange={setLinkKind}
                    options={mediaSpecs.map((spec) => ({
                      value: spec.kind,
                      label: spec.label,
                    }))}
                  />
                )}
                <Input
                  variant="borderless"
                  value={linkInput}
                  placeholder={`也可以粘贴${activeLinkKind ? mediaKindLabel(activeLinkKind) : '素材'}外链`}
                  onChange={(event) => setLinkInput(event.target.value)}
                  onPressEnter={addLink}
                />
                <Button type="text" disabled={!linkInput.trim()} onClick={addLink}>添加</Button>
              </div>
            )}

            <div className="composer-media-note">
              {capability.id === 'i2v'
                ? '首帧图会严格成为生成视频的第一帧。'
                : '参考素材只用于引导人物、物体、动作、风格或声音，不会被强制作为首帧。'}
              {!publicBaseUsable && mediaSpecs.some((spec) => spec.kind !== 'image') &&
                ' 本机音视频需要配置 PUBLIC_BASE_URL；未配置时请粘贴公网外链。'}
            </div>
          </div>
        )}

        <div className="composer-input-card">
          <Input.TextArea
            variant="borderless"
            value={state.prompt}
            onChange={(event) => onChange({ prompt: event.target.value })}
            placeholder={placeholder}
            autoSize={{ minRows: 3, maxRows: 8 }}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && canSend) onSubmit()
            }}
          />
          <div className="composer-submit-row">
            <span
              className={'composer-summary' + (missingMedia ? ' warning' : '')}
              title={capability.description}
            >
              {missingMedia
                ? missingMediaText
                : (
                    <>
                      <i className={`mode-dot ${capability.id}`} />
                      {state.resolution}{state.ratio ? ` · ${ratioLabel(state.ratio)}` : ''} · {state.duration} 秒
                    </>
                  )}
            </span>
            <div className="composer-toggles">
              {capability.id === 't2v' && (
                <Tooltip title="用已安装的 Skill 自动增强本次提示词">
                  <div className="composer-skill-picker">
                    <ExperimentOutlined />
                    <Select
                      variant="borderless"
                      value={state.skillId || undefined}
                      placeholder={skills.length ? '选择提示词 Skill' : '暂无可用 Skill'}
                      allowClear
                      disabled={skills.length === 0}
                      onChange={(skillId) => onChange({ skillId: skillId || '' })}
                      options={skills.map((skill) => ({
                        value: skill.id,
                        label: skill.name,
                        title: skill.description,
                      }))}
                    />
                  </div>
                </Tooltip>
              )}
              {model.supports_watermark && (
                <button
                  type="button"
                  className={'composer-toggle' + (state.watermark ? ' active' : '')}
                  aria-pressed={state.watermark}
                  onClick={() => onChange({ watermark: !state.watermark })}
                >
                  <HighlightOutlined /> 水印
                </button>
              )}
              {model.supports_audio && (
                <Tooltip title="生成对白、BGM 与音效">
                  <button
                    type="button"
                    className={'composer-toggle' + (state.audio ? ' active' : '')}
                    aria-pressed={state.audio}
                    onClick={() => onChange({ audio: !state.audio })}
                  >
                    <SoundOutlined /> 音频
                  </button>
                </Tooltip>
              )}
              <Tooltip title="结合本对话的历史提示词理解本次修改要求">
                <button
                  type="button"
                  className={'composer-toggle' + (state.useContext ? ' active' : '')}
                  aria-pressed={state.useContext}
                  onClick={() => onChange({ useContext: !state.useContext })}
                >
                  <HistoryOutlined /> 延续上下文
                </button>
              </Tooltip>
            </div>
            <span className="composer-shortcut">⌘/Ctrl + Enter</span>
            <Button
              type="primary"
              className="composer-submit"
              loading={sending}
              disabled={!canSend}
              onClick={onSubmit}
            >
              <span>生成视频</span>
              {!sending && <SendOutlined />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
