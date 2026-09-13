import { memo } from 'react'
import { type NodeProps, Handle, Position } from '@xyflow/react'
import { FileText } from 'lucide-react'
import NodeShell from './NodeShell'
import { useUpdateNodeData } from './context'
import type { PromptNodeData } from '../../canvasTypes'

/**
 * 提示词节点 — 只有 1 个 source Handle (prompt)，没有 target。
 * data patches go through setNodes, never in-place mutation.
 */
const PromptNode = memo(function PromptNode({ id, data }: NodeProps) {
  const d = data as PromptNodeData
  const setData = useUpdateNodeData(id, data)
  return (
    <>
      <NodeShell title="提示词" icon={<FileText className="w-3.5 h-3.5" />} className="w-64">
        <textarea
          value={d.text || ''}
          onChange={e => setData({ text: e.target.value })}
          placeholder="描述画面、镜头与风格……"
          rows={3}
          className="w-full bg-[var(--color-surface-3)] text-[12px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)] rounded-lg px-2.5 py-2 outline-none resize-none border border-transparent focus:border-[var(--color-primary)]/30"
        />
      </NodeShell>
      <Handle type="source" position={Position.Right} id="prompt" style={{ top: '50%', '--handle-color': 'var(--color-port-prompt)' } as never} data-handle-color="1" data-handle-label="提示词" />
    </>
  )
})

export default PromptNode
