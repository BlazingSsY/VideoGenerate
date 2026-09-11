import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { App as AntApp, Button, Input, Upload } from 'antd'
import { PictureOutlined } from '@ant-design/icons'

import api, { errorText } from '../../api'
import type { CanvasFlowNode, ImageNodeData } from '../../canvasTypes'
import { useCanvasNodeContext } from './context'
import NodeShell from './NodeShell'

export default function ImageNode({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { message } = AntApp.useApp()
  const { maxUploadMb, updateNodeData } = useCanvasNodeContext()
  const image = data as ImageNodeData
  const [uploading, setUploading] = useState(false)

  const upload = async (file: File) => {
    if (file.size > maxUploadMb * 1024 * 1024) {
      message.error(`图片不能超过 ${maxUploadMb}MB`)
      return
    }
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await api.post('/api/uploads', form)
      updateNodeData(id, {
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

  return (
    <NodeShell eyebrow="IMAGE" title={image.name || '图片素材'} selected={selected}>
      {image.url ? (
        <img
          className="canvas-image-preview"
          src={image.signed_url || image.url}
          alt={image.name || '图片素材'}
        />
      ) : (
        <div className="canvas-image-empty"><PictureOutlined /> 等待图片</div>
      )}
      <Upload
        className="nodrag"
        showUploadList={false}
        accept="image/*"
        beforeUpload={(file) => {
          upload(file as File)
          return false
        }}
      >
        <Button size="small" block loading={uploading} icon={<PictureOutlined />}>
          上传图片
        </Button>
      </Upload>
      <Input
        className="nodrag nowheel"
        size="small"
        value={image.url}
        placeholder="或粘贴 http(s) 图片地址"
        onChange={(event) => updateNodeData(id, { url: event.target.value, name: '外链图片' })}
      />
      <Handle id="out" type="source" position={Position.Right} className="canvas-handle image" />
    </NodeShell>
  )
}
