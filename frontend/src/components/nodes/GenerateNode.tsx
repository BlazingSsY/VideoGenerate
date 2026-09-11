import { useEffect, useState } from 'react'
import { Handle, Position, type NodeProps, useUpdateNodeInternals } from '@xyflow/react'
import { Alert, App as AntApp, Button, Input, Select, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import { DeleteOutlined, DownloadOutlined, PlusOutlined, PlayCircleOutlined } from '@ant-design/icons'

import type { CanvasFlowNode, GenerateNodeData } from '../../canvasTypes'
import { useModelParams } from '../../hooks/useModelParams'
import type { ModelParamState } from '../../hooks/useModelParams'
import { capabilityMediaInputs, minimumMedia } from '../../mediaCapabilities'
import type { MediaKind } from '../../types'
import { MODE_COLORS } from '../../types'
import { downloadMessageVideo } from '../../utils/downloadVideo'
import { useCanvasNodeContext } from './context'
import NodeShell from './NodeShell'

const statusLabel: Record<string, string> = {
  idle: '待运行',
  pending: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败',
}

/** 一行输入槽：连接点绝对定位在这一行的左边缘，行数再多也不会错位 */
function Port({
  id,
  kind,
  label,
  hint,
  connected,
  required,
}: {
  id: string
  kind: 'text' | MediaKind
  label: string
  hint?: string
  connected: boolean
  required?: boolean
}) {
  return (
    <div className={'canvas-port' + (connected ? ' connected' : '')}>
      <Handle
        id={id}
        type="target"
        position={Position.Left}
        className={`canvas-handle ${kind === 'end_frame' ? 'image' : kind}`}
      />
      <span className="canvas-port-label">
        {label}
        {required && <em className="canvas-port-required">*</em>}
      </span>
      <span className="canvas-port-hint">{connected ? '已连接' : hint || '未连接'}</span>
    </div>
  )
}

function MediaPortGroup({
  kind,
  label,
  note,
  minCount,
  maxCount,
  wired,
  handles,
  onAdd,
  onRemove,
}: {
  kind: MediaKind
  label: string
  note?: string
  minCount: number
  maxCount: number
  wired: Set<string>
  handles: string[]
  onAdd: () => void
  onRemove: (handle: string) => void
}) {
  const connectedCount = handles.filter((handle) => wired.has(handle)).length

  return (
    <div className={`canvas-media-port-group ${kind}`}>
      <div className="canvas-media-port-head">
        <span>{label}</span>
        <span>{connectedCount}/{maxCount} {handles.length < maxCount && <Button type="text" size="small" icon={<PlusOutlined />} onClick={onAdd} aria-label={`添加${label}`} />}</span>
      </div>
      <div className="canvas-media-slots">
        {handles.map((handle, index) => {
          const isConnected = wired.has(handle)
          const required = index < minCount
          return (
            <div
              key={handle}
              className={`canvas-media-slot${isConnected ? ' connected' : ''}`}
              title={`${label} ${index + 1}${required ? '（必填）' : ''}${note ? `：${note}` : ''}`}
            >
              <Handle
                id={handle}
                type="target"
                position={Position.Left}
                className={`canvas-handle ${kind === 'end_frame' ? 'image' : kind}`}
              />
              <span>{index + 1}</span>
              {required && <em>*</em>}
              {!required && <Button type="text" size="small" icon={<DeleteOutlined />} onClick={() => onRemove(handle)} aria-label={`删除${label}${index + 1}`} />}
            </div>
          )
        })}
      </div>
      {note && <div className="canvas-media-port-note">{note}</div>}
    </div>
  )
}

export default function GenerateNode({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { message } = AntApp.useApp()
  const { models, runtime, connected, updateNodeData, removeEdgesForHandle, runNode } = useCanvasNodeContext()
  const value = data as GenerateNodeData
  const state: ModelParamState = {
    model: value.model,
    capability: value.capability,
    resolution: value.resolution,
    ratio: value.ratio,
    duration: value.duration,
    watermark: value.watermark,
    audio: value.audio,
  }
  const { model, capability, ratioOptions, applyModel, unavailable } = useModelParams(
    models,
    state,
    (patch) => updateNodeData(id, patch),
  )
  const [downloading, setDownloading] = useState(false)
  const nodeRuntime = runtime[id] || { status: 'idle', message_id: null }
  const wired = connected[id] ?? new Set<string>()
  const updateNodeInternals = useUpdateNodeInternals()
  const mediaSpecs = capability ? capabilityMediaInputs(capability) : []
  const declaredSlots = value.media_slots || {}
  const mediaHandleSignature = mediaSpecs
    .map((spec) => `${spec.kind}:${(declaredSlots[spec.kind] || []).join(',')}`)
    .join('|')

  useEffect(() => {
    updateNodeInternals(id)
  }, [id, mediaHandleSignature, updateNodeInternals])

  // 模型下线或账号被降级时，明确说明并允许改选，而不是渲染成一个看不见的空节点
  if (unavailable || !model || !capability) {
    return (
      <NodeShell eyebrow="GENERATE" title="视频生成" selected={selected}>
        <Alert
          type="warning"
          showIcon
          message="模型不可用"
          description={
            <span style={{ fontSize: 12 }}>
              <code>{value.model || '未设置'}</code> 当前不可用，可能已下线或你的账号无权使用。
              换一个模型即可继续。
            </span>
          }
        />
        <div className="canvas-node-form nodrag nowheel" style={{ marginTop: 10 }}>
          <Select
            placeholder="改选一个可用模型"
            value={undefined}
            onChange={(modelId) => {
              const next = models.find((item) => item.id === modelId)
              if (next) applyModel(next)
            }}
            options={models.map((item) => ({ value: item.id, label: item.label }))}
          />
        </div>
      </NodeShell>
    )
  }

  const videoSrc = nodeRuntime.video_src
  const busy = nodeRuntime.status === 'pending' || nodeRuntime.status === 'running'
  const missingRequiredMedia = mediaSpecs.flatMap((spec) =>
    Array.from({ length: spec.min_count }, (_, index) => `${spec.kind}_${index}`)
      .filter((handle) => !wired.has(handle)),
  )
  const connectedMediaCount = mediaSpecs.reduce<number>(
    (total, spec) => total + Array.from(
      { length: spec.max_count },
      (_, index) => wired.has(`${spec.kind}_${index}`),
    ).filter(Boolean).length,
    0,
  )
  const missingMediaTotal = Math.max(0, minimumMedia(capability) - connectedMediaCount)
  const promptReady = wired.has('prompt') || Boolean(value.inlinePrompt?.trim())
  const runnable = promptReady && missingRequiredMedia.length === 0 && missingMediaTotal === 0

  return (
    <NodeShell eyebrow="GENERATE" title="视频生成" selected={selected}>
      <div className="canvas-ports">
        <Port
          id="prompt"
          kind="text"
          label="提示词"
          hint={value.inlinePrompt?.trim() ? '用内联' : '未连接'}
          connected={wired.has('prompt')}
        />
        {mediaSpecs.map((spec) => (
          (() => {
            const legacyHandles = Array.from({ length: spec.min_count }, (_, index) => `${spec.kind}_${index}`)
            const connectedHandles = Array.from(wired).filter((handle) => handle.startsWith(`${spec.kind}_`))
            const handles = Array.from(new Set([...(declaredSlots[spec.kind] || []), ...legacyHandles, ...connectedHandles])).slice(0, spec.max_count)
            return (
          <MediaPortGroup
            key={spec.kind}
            kind={spec.kind}
            label={spec.label}
            note={spec.note}
            minCount={spec.min_count}
            maxCount={spec.max_count}
            wired={wired}
            handles={handles}
            onAdd={() => updateNodeData(id, { media_slots: { ...declaredSlots, [spec.kind]: [...handles, `${spec.kind}_${handles.length}`] } })}
            onRemove={(handle) => { removeEdgesForHandle(id, handle); updateNodeData(id, { media_slots: { ...declaredSlots, [spec.kind]: handles.filter((item) => item !== handle) } }) }}
          />
            )
          })()
        ))}
      </div>

      <div className="canvas-node-form nodrag nowheel">
        <Select
          value={value.model}
          onChange={(modelId) => {
            const next = models.find((item) => item.id === modelId)
            if (next) applyModel(next)
          }}
          options={models.map((item) => ({ value: item.id, label: item.label }))}
        />
        {model.capabilities.length > 1 && (
          <Select
            value={capability.id}
            onChange={(mode) => applyModel(model, mode)}
            options={model.capabilities.map((item) => ({ value: item.id, label: item.label }))}
          />
        )}
        <Space.Compact block>
          <Tooltip title={model.resolution_note || model.resolutions.join(' / ')}>
            <Select
              value={value.resolution}
              onChange={(resolution) => updateNodeData(id, { resolution })}
              options={model.resolutions.map((item) => ({ value: item, label: item }))}
            />
          </Tooltip>
          {ratioOptions.length > 0 ? (
            <Select
              value={value.ratio}
              onChange={(ratio) => updateNodeData(id, { ratio })}
              options={ratioOptions.map((item) => ({
                value: item,
                label: item === 'adaptive' ? '自适应' : item,
              }))}
            />
          ) : (
            <Tooltip title="该生成方式的画面比例跟随原图">
              <Tag className="canvas-ratio-fixed">跟随原图</Tag>
            </Tooltip>
          )}
          <Select
            value={value.duration}
            onChange={(duration) => updateNodeData(id, { duration })}
            options={model.durations.map((item) => ({ value: item, label: `${item} 秒` }))}
          />
        </Space.Compact>
        <Input.TextArea
          value={value.inlinePrompt}
          placeholder="未连接提示词节点时使用这里的提示词"
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={wired.has('prompt')}
          onChange={(event) => updateNodeData(id, { inlinePrompt: event.target.value })}
        />
        <Space size={12} wrap>
          <Tag color={MODE_COLORS[capability.id]}>{capability.label}</Tag>
          {model.supports_watermark && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              <Switch
                size="small"
                checked={value.watermark}
                onChange={(watermark) => updateNodeData(id, { watermark })}
              />{' '}
              水印
            </Typography.Text>
          )}
          {model.supports_audio && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              <Switch
                size="small"
                checked={value.audio}
                onChange={(audio) => updateNodeData(id, { audio })}
              />{' '}
              生成音轨
            </Typography.Text>
          )}
        </Space>
      </div>

      <div className={`canvas-node-status ${nodeRuntime.status || 'idle'}`}>
        {statusLabel[nodeRuntime.status || 'idle'] || nodeRuntime.status}
      </div>
      {nodeRuntime.error && <div className="canvas-node-error">{nodeRuntime.error}</div>}
      {nodeRuntime.video_expired && (
        <div className="canvas-node-note">视频已过期清理，可重新生成</div>
      )}
      {videoSrc && (
        <video
          className="canvas-node-video nodrag nowheel"
          src={videoSrc}
          controls
          controlsList="nodownload"
          preload="metadata"
          playsInline
        />
      )}

      <Space.Compact block className="nodrag">
        <Tooltip
          title={
            runnable
              ? ''
              : !promptReady
                ? '请连接提示词节点，或在上方填写内联提示词'
                : missingRequiredMedia.length
                  ? `还缺 ${missingRequiredMedia.length} 个必填素材`
                  : `还需连接 ${missingMediaTotal} 个参考素材`
          }
        >
          <Button
            type="primary"
            className="btn-gradient"
            block
            icon={<PlayCircleOutlined />}
            loading={busy}
            disabled={!runnable && !busy}
            onClick={() => runNode(id)}
          >
            {nodeRuntime.status === 'succeeded' ? '重新生成' : '运行节点'}
          </Button>
        </Tooltip>
        {nodeRuntime.status === 'succeeded' && nodeRuntime.message_id && !nodeRuntime.video_expired && (
          <Button
            icon={<DownloadOutlined />}
            loading={downloading}
            onClick={async () => {
              setDownloading(true)
              try {
                await downloadMessageVideo(
                  { id: nodeRuntime.message_id!, video_src: videoSrc },
                  `canvas-${id}.mp4`,
                )
              } catch (error) {
                message.error(error instanceof Error ? error.message : '下载失败')
              } finally {
                setDownloading(false)
              }
            }}
          />
        )}
      </Space.Compact>
      <div className="canvas-output-port">
        <span>视频输出</span>
        <Handle id="out" type="source" position={Position.Right} className="canvas-handle video" />
      </div>
    </NodeShell>
  )
}
