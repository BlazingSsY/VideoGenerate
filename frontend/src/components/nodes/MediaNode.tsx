import { memo, useRef, useState } from 'react'
import { type NodeProps, Handle, Position } from '@xyflow/react'
import { Video, Music, Upload, Link2, Loader2 } from 'lucide-react'
import api, { errorText } from '../../api'
import NodeShell from './NodeShell'
import NodeVideo from './NodeVideo'
import { useUpdateNodeData } from './context'
import type { MediaNodeData } from '../../canvasTypes'

/**
 * 视频/音频素材节点 — 1 个 source Handle (video 或 audio)。
 * 视频/音频不能 Base64（media_resolver 仅 {image, end_frame}），必须公网可访问：
 * 上传文件的展示可以直接用 signed 路径，但喂给生成端点的仍是 /media/uploads 相对路径，
 * 后端提交生成时会校验 public_base_url，没有公网地址的视频/音频将被拒绝。
 */
const MediaNode = memo(function MediaNode({ id, data }: NodeProps) {
  const d = data as MediaNodeData
  const setData = useUpdateNodeData(id, data)
  const [uploading, setUploading] = useState(false)
  const [urlMode, setUrlMode] = useState(false)
  const [urlInput, setUrlInput] = useState('')
  const isAudio = d.kind === 'audio'
  const accept = isAudio ? '.mp3,.wav' : '.mp4,.mov'

  const upload = async (file: File) => {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await api.post('/api/uploads', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      const u = res.data as { url: string; preview_url: string; filename: string }
      setData({ url: u.url, name: file.name, signed_url: u.preview_url })
    } catch (err) {
      alert(errorText(err, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const applyUrl = () => {
    const u = urlInput.trim()
    if (!u) return
    setData({ url: u, name: d.name || (isAudio ? '外部音频' : '外部视频'), signed_url: u })
    setUrlMode(false)
    setUrlInput('')
  }

  return (
    <div className="relative">
      <NodeShell title={isAudio ? '音频素材' : '视频素材'} icon={isAudio ? <Music className="w-3.5 h-3.5" /> : <Video className="w-3.5 h-3.5" />} className="w-48">
        {d.signed_url && !isAudio ? (
          <NodeVideo src={d.signed_url} title={d.name || '视频素材'} testId={`media-video-${id}`} />
        ) : (
          <div className="w-full h-20 flex items-center justify-center bg-[var(--color-surface-3)] rounded-lg mb-2">
            {isAudio ? <Music className="w-5 h-5 text-[var(--color-ink-tertiary)]" /> : <Video className="w-5 h-5 text-[var(--color-ink-tertiary)]" />}
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
              placeholder={isAudio ? '粘贴公网音频 URL' : '需可公网访问的视频 URL（.mp4/.mov）'}
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
              <input type="file" hidden accept={accept}
                onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
            </label>
            <button onClick={() => setUrlMode(true)}
              className="flex items-center px-2 py-1 text-[12px] text-[var(--color-ink-secondary)] border border-[var(--color-border)] rounded-md hover:text-[var(--color-primary)]">
              <Link2 className="w-3 h-3" />
            </button>
          </div>
        )}
      </NodeShell>
      {/* 视频/音频不能 Base64；source handle 语义色与生成侧一致 */}
      <Handle type="source" position={Position.Right} id={d.kind} style={{ top: '50%', '--handle-color': d.kind === 'audio' ? 'var(--color-port-audio)' : 'var(--color-port-video)' } as never} data-handle-color="1" data-handle-label={d.kind === 'audio' ? '音频' : '视频'} />
    </div>
  )
})

export default MediaNode
