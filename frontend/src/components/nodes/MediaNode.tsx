import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Alert, App as AntApp, Button, Input, Upload } from 'antd'
import { SoundOutlined, VideoCameraOutlined } from '@ant-design/icons'

import api, { errorText } from '../../api'
import type { CanvasFlowNode, MediaNodeData } from '../../canvasTypes'
import { useCanvasNodeContext } from './context'
import NodeShell from './NodeShell'

export default function MediaNode({ id, type, data, selected }: NodeProps<CanvasFlowNode>) {
  const { message } = AntApp.useApp()
  const { maxUploadMb, publicBaseUsable, updateNodeData } = useCanvasNodeContext()
  const media = data as MediaNodeData
  const kind = type === 'audio' ? 'audio' : 'video'
  const isVideo = kind === 'video'
  const label = isVideo ? '视频素材' : '音频素材'
  const accept = isVideo ? '.mp4,.mov' : '.mp3,.wav'
  const limitMb = isVideo ? maxUploadMb : Math.min(maxUploadMb, 15)
  const [uploading, setUploading] = useState(false)

  const upload = async (file: File) => {
    if (file.size > limitMb * 1024 * 1024) {
      message.error(`${label}不能超过 ${limitMb}MB`)
      return
    }
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await api.post('/api/uploads', form)
      updateNodeData(id, {
        kind,
        url: response.data.url,
        signed_url: response.data.preview_url,
        name: file.name,
      })
    } catch (error) {
      message.error(errorText(error, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const src = media.signed_url || media.url
  const icon = isVideo ? <VideoCameraOutlined /> : <SoundOutlined />

  return (
    <NodeShell eyebrow={isVideo ? 'VIDEO' : 'AUDIO'} title={media.name || label} selected={selected}>
      {src ? (
        isVideo ? (
          <video
            className="canvas-node-video nodrag nowheel"
            src={src}
            controls
            preload="metadata"
            playsInline
          />
        ) : (
          <audio
            className="canvas-node-audio nodrag nowheel"
            src={src}
            controls
            preload="metadata"
          />
        )
      ) : (
        <div className="canvas-image-empty">{icon} 等待{label}</div>
      )}
      {!publicBaseUsable && (
        <Alert
          className="canvas-media-warning"
          type="info"
          showIcon
          message="本机素材仅供预览"
          description="提交给模型时需配置 PUBLIC_BASE_URL，或直接粘贴公网外链。"
        />
      )}
      <Upload
        className="nodrag"
        showUploadList={false}
        accept={accept}
        beforeUpload={(file) => {
          upload(file as File)
          return false
        }}
      >
        <Button size="small" block loading={uploading} icon={icon}>
          上传{label}
        </Button>
      </Upload>
      <Input
        className="nodrag nowheel"
        size="small"
        value={media.url}
        placeholder={`或粘贴 http(s) ${label}地址`}
        onChange={(event) => updateNodeData(id, {
          kind,
          url: event.target.value,
          signed_url: undefined,
          name: `外链${label}`,
        })}
      />
      <Handle id="out" type="source" position={Position.Right} className={`canvas-handle ${kind}`} />
    </NodeShell>
  )
}
