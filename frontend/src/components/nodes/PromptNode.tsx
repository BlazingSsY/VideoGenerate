import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Input } from 'antd'

import type { CanvasFlowNode, PromptNodeData } from '../../canvasTypes'
import { useCanvasNodeContext } from './context'
import NodeShell from './NodeShell'

export default function PromptNode({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { updateNodeData } = useCanvasNodeContext()
  const prompt = data as PromptNodeData
  return (
    <NodeShell eyebrow="TEXT" title="提示词" selected={selected}>
      <Input.TextArea
        className="nodrag nowheel"
        value={prompt.text}
        placeholder="描述画面、镜头与风格……"
        autoSize={{ minRows: 4, maxRows: 10 }}
        onChange={(event) => updateNodeData(id, { text: event.target.value })}
      />
      <Handle id="out" type="source" position={Position.Right} className="canvas-handle text" />
    </NodeShell>
  )
}
