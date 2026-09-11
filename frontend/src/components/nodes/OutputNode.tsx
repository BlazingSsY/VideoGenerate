import { useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Alert, App as AntApp, Button, Input } from 'antd'
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons'

import type { CanvasFlowNode, CanvasRuntime, OutputNodeData } from '../../canvasTypes'
import { downloadMessageVideo } from '../../utils/downloadVideo'
import { useCanvasNodeContext } from './context'
import NodeShell from './NodeShell'

const statusLabel: Record<string, string> = {
  idle: '等待上游运行',
  pending: '上游排队中',
  running: '上游生成中',
  succeeded: '输出已就绪',
  failed: '上游生成失败',
}

export default function OutputNode({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { message } = AntApp.useApp()
  const { incoming, runtime, updateNodeData } = useCanvasNodeContext()
  const output = data as OutputNodeData
  const sourceId = incoming[id]?.in
  const sourceRuntime: CanvasRuntime = sourceId
    ? runtime[sourceId] || { status: 'idle', message_id: null }
    : { status: 'idle', message_id: null }
  const [downloading, setDownloading] = useState(false)

  return (
    <NodeShell eyebrow="OUTPUT" title={output.label || '视频输出'} selected={selected}>
      <div className={'canvas-port' + (sourceId ? ' connected' : '')}>
        <Handle id="in" type="target" position={Position.Left} className="canvas-handle video" />
        <span className="canvas-port-label">生成结果</span>
        <span className="canvas-port-hint">{sourceId ? '已连接' : '从生成节点连接'}</span>
      </div>
      <Input
        className="nodrag nowheel"
        size="small"
        value={output.label}
        placeholder="输出名称"
        onChange={(event) => updateNodeData(id, { label: event.target.value })}
      />
      {!sourceId && (
        <div className="canvas-output-empty">
          <InboxOutlined />
          <span>把生成节点右侧的“视频输出”连接到这里</span>
        </div>
      )}
      {sourceRuntime.video_src && (
        <video
          className="canvas-node-video nodrag nowheel"
          src={sourceRuntime.video_src}
          controls
          controlsList="nodownload"
          preload="metadata"
          playsInline
        />
      )}
      {sourceRuntime.error && (
        <Alert type="error" showIcon message="输出失败" description={sourceRuntime.error} />
      )}
      {sourceRuntime.video_expired && (
        <Alert type="info" showIcon message="视频已过期清理" />
      )}
      {sourceId && (
        <div className={`canvas-node-status ${sourceRuntime.status || 'idle'}`}>
          {statusLabel[sourceRuntime.status || 'idle'] || sourceRuntime.status}
        </div>
      )}
      {sourceRuntime.status === 'succeeded' && sourceRuntime.message_id && !sourceRuntime.video_expired && (
        <Button
          className="nodrag"
          block
          icon={<DownloadOutlined />}
          loading={downloading}
          onClick={async () => {
            setDownloading(true)
            try {
              await downloadMessageVideo(
                { id: sourceRuntime.message_id!, video_src: sourceRuntime.video_src },
                `${output.label || 'canvas-output'}.mp4`,
              )
            } catch (error) {
              message.error(error instanceof Error ? error.message : '下载失败')
            } finally {
              setDownloading(false)
            }
          }}
        >
          下载输出视频
        </Button>
      )}
    </NodeShell>
  )
}
