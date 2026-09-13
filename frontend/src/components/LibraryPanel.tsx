import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Upload, Link2, Loader2, Search, Image as ImageIcon, Video, Music } from 'lucide-react'
import api, { errorText } from '../api'
import { cn } from '../lib/utils'

export interface LibraryAsset {
  id: string; name: string
  kind: 'image' | 'video' | 'audio'
  category: string; description: string
  file_url: string; preview_url: string; source_url: string
}

/**
 * 素材库面板 — 列表 + kind 筛选 + 上传 + 外链补录。
 * 每个条目可拖入画布（onDrop 由 Canvas 处理，dataTransfer 携带 asset JSON）。
 */
export default function LibraryPanel({ onClose, onAddToCanvas }: {
  onClose: () => void
  onAddToCanvas: (asset: LibraryAsset) => void
}) {
  const [assets, setAssets] = useState<LibraryAsset[]>([])
  const [kind, setKind] = useState<'image' | 'video' | 'audio' | ''>('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [linkMode, setLinkMode] = useState(false)
  const [linkForm, setLinkForm] = useState({ name: '', kind: 'image' as LibraryAsset['kind'], url: '' })
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (kind) params.kind = kind
      if (query.trim()) params.q = query.trim()
      const res = await api.get('/api/assets', { params })
      setAssets(res.data)
      setError('')
    } catch (e) {
      setError(errorText(e, '加载素材失败'))
    } finally {
      setLoading(false)
    }
  }, [kind, query])

  useEffect(() => { load() }, [load])

  const upload = async (file: File) => {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      await api.post('/api/uploads', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      await load()
    } catch (e) {
      setError(errorText(e, '上传失败'))
    } finally {
      setUploading(false)
    }
  }

  const registerLink = async () => {
    const { name, kind: k, url } = linkForm
    if (!name.trim() || !url.trim()) { setError('名称与地址不能为空'); return }
    try {
      await api.post('/api/assets', {
        name: name.trim(), kind: k, source_url: url.trim(),
        description: '',
      })
      setLinkMode(false)
      setLinkForm({ name: '', kind: 'image', url: '' })
      setError('')
      await load()
    } catch (e) {
      setError(errorText(e, '登记失败'))
    }
  }

  const remove = async (id: string) => {
    try { await api.delete(`/api/assets/${id}`); await load() }
    catch (e) { setError(errorText(e, '删除失败')) }
  }

  const Icon = (k: string) => k === 'image' ? ImageIcon : k === 'video' ? Video : Music

  return (
    <div data-testid="library-panel" className="absolute left-14 top-4 bottom-4 z-40 w-80 flex flex-col bg-[var(--color-popover)] border border-[var(--color-border)] rounded-2xl shadow-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
        <h2 className="text-xs font-semibold text-[var(--color-ink)]">素材库</h2>
        <div className="flex items-center gap-1">
          {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--color-primary)]" /> : (
            <button onClick={() => fileRef.current?.click()} title="上传"
              className="p-1.5 rounded-md hover:bg-[var(--color-surface-3)] text-[var(--color-ink-secondary)] hover:text-[var(--color-primary)]"
              data-testid="lib-upload">
              <Upload className="w-3.5 h-3.5" />
            </button>
          )}
          <button onClick={() => setLinkMode(!linkMode)} title="登记外链"
            className={cn('p-1.5 rounded-md text-[var(--color-ink-secondary)] hover:text-[var(--color-primary)]', linkMode && 'bg-[var(--color-surface-3)] text-[var(--color-primary)]')}>
            <Link2 className="w-3.5 h-3.5" />
          </button>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      <input ref={fileRef} type="file" hidden accept=".jpg,.jpeg,.png,.webp,.bmp,.mp4,.mov,.mp3,.wav"
        onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />

      <div className="pb-2 border-b border-[var(--color-border-soft)]">
        <div className="flex items-center gap-2 px-3 pt-2">
          <div className="flex-1 flex items-center gap-1.5 px-2 py-1 bg-[var(--color-surface-3)] rounded-md">
            <Search className="w-3 h-3 text-[var(--color-ink-tertiary)]" />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索素材…"
              className="flex-1 bg-transparent outline-none text-[11px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
          </div>
        </div>
        <div className="flex items-center gap-1.5 px-3 pt-2">
          {([['', '全部'], ['image', '图片'], ['video', '视频'], ['audio', '音频']] as const).map(([k, label]) => (
            <button key={k || 'all'} onClick={() => setKind(k as any)} data-testid={`lib-filter-${k || 'all'}`}
              className={cn('px-2 py-0.5 text-[10px] rounded-full border transition-colors',
                kind === k
                  ? 'bg-brand-gradient text-white border-transparent'
                  : 'text-[var(--color-ink-secondary)] border-[var(--color-border)] hover:border-[var(--color-primary)]')}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {linkMode && (
        <div className="mx-3 mt-2 p-2.5 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl space-y-2 animate-fade-in">
          <div className="flex gap-1.5">
            {(['image', 'video', 'audio'] as const).map(k => (
              <button key={k} data-testid={`lib-link-kind-${k}`} onClick={() => setLinkForm(f => ({ ...f, kind: k }))}
                className={cn('px-2 py-0.5 text-[10px] rounded-full border transition-colors',
                  linkForm.kind === k ? 'bg-brand-gradient text-white border-transparent'
                    : 'text-[var(--color-ink-secondary)] border-[var(--color-border)]')}>
                {k === 'image' ? '图片' : k === 'video' ? '视频' : '音频'}
              </button>
            ))}
          </div>
          <input value={linkForm.name} onChange={e => setLinkForm(f => ({ ...f, name: e.target.value }))} placeholder="名称"
            className="w-full px-2 py-1 text-[11px] bg-[var(--color-surface-3)] rounded-md outline-none text-[var(--color-ink)] border border-[var(--color-border)] focus:border-[var(--color-primary)]" />
          <input value={linkForm.url} onChange={e => setLinkForm(f => ({ ...f, url: e.target.value }))}
            placeholder="https://…（需可公网访问）"
            className="w-full px-2 py-1 text-[11px] bg-[var(--color-surface-3)] rounded-md outline-none text-[var(--color-ink)] border border-[var(--color-border)] focus:border-[var(--color-primary)]" />
          <div className="flex justify-end gap-1.5">
            <button onClick={() => setLinkMode(false)} className="px-2 py-1 text-[10px] text-[var(--color-ink-tertiary)] hover:text-[var(--color-ink)]">取消</button>
            <button onClick={registerLink} className="px-3 py-1 text-[10px] text-white bg-brand-gradient rounded-md">登记</button>
          </div>
        </div>
      )}

      {error && <p className="mx-3 mt-2 text-[10px] text-[var(--color-danger)]">{error}</p>}

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {loading && <p className="text-[11px] text-[var(--color-ink-tertiary)] text-center py-6">加载中…</p>}
        {!loading && assets.length === 0 && (
          <div className="text-center py-8">
            <p className="text-[11px] text-[var(--color-ink-tertiary)]">还没有素材</p>
            <p className="text-[10px] text-[var(--color-ink-tertiary)] mt-1">上传图片 / 视频 / 音频，或登记公网外链</p>
          </div>
        )}
        {!loading && assets.map(a => {
          const KIcon = Icon(a.kind)
          return (
            <div key={a.id} data-testid="lib-item" draggable
              onDragStart={e => {
                e.dataTransfer.setData('application/x-vg-asset', JSON.stringify(a))
                e.dataTransfer.effectAllowed = 'copy'
              }}
              onDoubleClick={() => onAddToCanvas(a)}
              className="group flex items-start gap-2.5 p-2 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-lg cursor-grab active:cursor-grabbing hover:border-[var(--color-primary)]/60 transition-colors">
              {a.kind === 'image' && a.preview_url ? (
                <img src={a.preview_url} alt={a.name} className="w-10 h-10 rounded-md object-cover shrink-0 bg-[var(--color-surface-3)]" />
              ) : (
                <div className="w-10 h-10 rounded-md bg-[var(--color-surface-3)] flex items-center justify-center shrink-0">
                  <KIcon className="w-4 h-4 text-[var(--color-ink-tertiary)]" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-medium text-[var(--color-ink)] truncate">{a.name}</p>
                <p className="text-[10px] text-[var(--color-ink-tertiary)] truncate">
                  {a.kind === 'image' ? '图片' : a.kind === 'video' ? '视频' : '音频'}
                  {a.source_url && ' · 外链'}{a.category && ` · ${a.category}`}
                </p>
              </div>
              <div className="flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={() => onAddToCanvas(a)} title="加入画布"
                  className="p-1 rounded text-[10px] text-[var(--color-primary)] hover:bg-[var(--color-surface-3)]">＋</button>
                <button onClick={() => remove(a.id)} title="删除"
                  className="p-1 rounded text-[10px] text-[var(--color-ink-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-surface-3)]">✕</button>
              </div>
            </div>
          )
        })}
      </div>
      <p className="px-3 py-2 text-[10px] text-[var(--color-ink-tertiary)] border-t border-[var(--color-border-soft)]">
        拖拽条目到画布即可创建素材节点
      </p>
    </div>
  )
}
