import { memo, useRef, useState } from 'react'
import { type NodeProps, Handle, Position } from '@xyflow/react'
import { Image as ImageIcon, Upload, Link2, Loader2 } from 'lucide-react'
import api, { errorText } from '../../api'
import NodeShell from './NodeShell'
import PreviewImage from '../PreviewImage'
import { useUpdateNodeData } from './context'
import type { ImageNodeData } from '../../canvasTypes'

/**
 * 图片素材节点 — 2 个 source Handle: image (首帧/参考图) + end_frame (尾帧)。
 * 本地上传走 POST /api/uploads；远端图片直接粘贴 URL。
 * All data patches go through useUpdateNodeData (setNodes), never mutation.
 */
const ImageNode = memo(function ImageNode({ id, data }: NodeProps) {
  const d = data as ImageNodeData
  const setData = useUpdateNodeData(id, data)
  const [uploading, setUploading] = useState(false)
  const [urlMode, setUrlMode] = useState(false)
  const [urlInput, setUrlInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = async (file: File) => {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await api.post('/api/uploads', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      const u = res.data as { url: string; preview_url: string; filename: string }
      setData({
        url: u.url,               // 存库相对路径，生成时由 media_resolver 决定公网 URL / Base64
        name: file.name,
        signed_url: u.preview_url,
      })
    } catch (err) {
      alert(errorText(err, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const applyUrl = () => {
    const u = urlInput.trim()
    if (!u) return
    setData({ url: u, name: d.name || '外部图片', signed_url: u })
    setUrlMode(false)
    setUrlInput('')
  }

  return (
    <div className="relative">
      <NodeShell title="图片素材" icon={<ImageIcon className="w-3.5 h-3.5" />} className="w-48">
        {d.signed_url ? (
          <PreviewImage src={d.signed_url} alt={d.name} className="w-full h-28 rounded-lg mb-2" />
        ) : (
          <div className="w-full h-24 flex items-center justify-center bg-[var(--color-surface-3)] rounded-lg mb-2">
            <ImageIcon className="w-6 h-6 text-[var(--color-ink-tertiary)]" />
          </div>
        )}
        {uploading ? (
          <div className="w-full h-8 flex items-center justify-center gap-1.5 text-[12px] text-[var(--color-ink-secondary)] bg-[var(--color-surface-3)] rounded-md">
            <Loader2 className="w-3 h-3 animate-spin" /> 上传中…
          </div>
        ) : urlMode ? (
          <div className="space-y-1.5">
            <input
              autoFocus
              value={urlInput}
              onChange={e => setUrlInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') applyUrl(); if (e.key === 'Escape') { setUrlMode(false); setUrlInput('') } }}
              placeholder="粘贴公网图片 URL"
              className="w-full bg-[var(--color-surface-3)] text-[12px] text-[var(--color-ink)] rounded-md px-2 py-1.5 outline-none border border-[var(--color-primary)]/40"
            />
            <div className="flex gap-1.5">
              <button onClick={applyUrl} disabled={!urlInput.trim()}
                className="flex-1 py-1 text-[12px] text-white bg-brand-gradient rounded-md disabled:opacity-40">使用</button>
              <button onClick={() => { setUrlMode(false); setUrlInput('') }}
                className="px-2 py-1 text-[12px] text-[var(--color-ink-tertiary)] border border-[var(--color-border)] rounded-md">取消</button>
            </div>
          </div>
        ) : (
          <div className="flex gap-1.5">
            <label className="flex-1 flex items-center justify-center gap-1 py-1 text-[12px] text-[var(--color-ink-secondary)] bg-[var(--color-surface-3)] rounded-md cursor-pointer hover:text-[var(--color-primary)] transition-colors">
              <Upload className="w-3 h-3" /> 上传
              <input ref={inputRef} type="file" hidden accept="image/*"
                onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
            </label>
            <button onClick={() => setUrlMode(true)}
              className="flex items-center gap-1 px-2 py-1 text-[12px] text-[var(--color-ink-secondary)] border border-[var(--color-border)] rounded-md hover:text-[var(--color-primary)]">
              <Link2 className="w-3 h-3" />
            </button>
          </div>
        )}
      </NodeShell>
      {/* source: 单一 image handle（绿色）；连到生成节点的不同语义槽（首帧/尾帧/参考图）由槽位颜色区分 */}
      <Handle type="source" position={Position.Right} id="image" style={{ top: '50%', '--handle-color': 'var(--color-port-image)' } as never} data-handle-color="1" data-handle-label="图片" />
    </div>
  )
})

export default ImageNode
