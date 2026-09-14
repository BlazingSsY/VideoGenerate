import { useEffect, useRef, useState } from 'react'
import { ImageOff, Loader2 } from 'lucide-react'

// Leave connections available for navigation, chat streams, and saves.
let active = 0
const waiting: (() => void)[] = []
function acquire(signal: AbortSignal): Promise<() => void> {
  return new Promise((resolve, reject) => {
    const cancel = () => {
      const index = waiting.indexOf(start)
      if (index >= 0) waiting.splice(index, 1)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const start = () => {
      signal.removeEventListener('abort', cancel)
      if (signal.aborted) { cancel(); return }
      active++
      resolve(() => { active--; waiting.shift()?.() })
    }
    signal.addEventListener('abort', cancel, { once: true })
    if (signal.aborted) cancel()
    else if (active < 3) start()
    else waiting.push(start)
  })
}

export default function PreviewImage({ src, alt, className = '' }: { src: string; alt?: string; className?: string }) {
  const container = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [loaded, setLoaded] = useState<{ source: string; url: string } | null>(null)
  const [failed, setFailed] = useState(false)
  let local: URL | null = null
  try { local = new URL(src, window.location.origin) } catch { /* The image error state handles incomplete pasted URLs. */ }
  const isUpload = local?.origin === window.location.origin && local.pathname.startsWith('/media/uploads/')

  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '150px' })
    if (container.current) observer.observe(container.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    setFailed(false)
    if (!visible || !isUpload) return
    const controller = new AbortController()
    let objectUrl = ''
    const url = new URL(src, window.location.origin)
    url.searchParams.set('preview', '1')
    void (async () => {
      const release = await acquire(controller.signal)
      try {
        const response = await fetch(url, { signal: controller.signal, priority: 'low' } as RequestInit)
        if (!response.ok) throw new Error('Preview unavailable')
        const blob = await response.blob()
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setLoaded({ source: src, url: objectUrl })
      } finally { release() }
    })().catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [src, visible, isUpload])

  const imageUrl = visible ? (isUpload ? (loaded?.source === src ? loaded.url : '') : src) : ''
  return (
    <div ref={container} className={`relative overflow-hidden bg-[var(--color-surface-3)] ${className}`}>
      {failed ? <span className="absolute inset-0 grid place-items-center text-[var(--color-ink-tertiary)]" title="图片预览加载失败"><ImageOff className="w-4 h-4" /></span>
      : imageUrl ? <img src={imageUrl} alt={alt || '图片素材'} decoding="async" loading="lazy" onError={() => setFailed(true)} className="block w-full h-full object-contain" />
      : <span className="absolute inset-0 grid place-items-center text-[var(--color-ink-tertiary)]"><Loader2 className="w-4 h-4 animate-spin" /></span>}
    </div>
  )
}
