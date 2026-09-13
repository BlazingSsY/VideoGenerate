import { useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Play, X } from 'lucide-react'

export default function NodeVideo({ src, title, testId }: { src: string; title: string; testId: string }) {
  const [open, setOpen] = useState(false)
  // Status polling refreshes URL signatures. Keep a still-valid URL for the
  // same local file so other running shots don't reset this player's position.
  const playingSource = useRef(src)
  if (playingSource.current !== src) {
    const previous = new URL(playingSource.current, window.location.origin)
    const incoming = new URL(src, window.location.origin)
    const sameFile = previous.pathname.startsWith('/media/') && previous.origin === incoming.origin && previous.pathname === incoming.pathname
    const expires = Number(previous.searchParams.get('exp') || 0)
    if (!sameFile || expires <= Date.now() / 1000 + 60) playingSource.current = src
  }
  const videoSrc = playingSource.current
  return (
    <div className="nodrag nopan nowheel space-y-1.5" onPointerDown={event => event.stopPropagation()}>
      <video data-testid={testId} src={videoSrc} aria-label={title} className="w-full rounded-lg bg-black" controls playsInline preload="metadata" />
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Trigger asChild>
          <button className="flex items-center justify-center gap-1.5 w-full py-1.5 text-[11px] rounded-lg bg-[var(--color-surface-3)] text-[var(--color-ink)]">
            <Play className="w-3 h-3" /> 观看视频
          </button>
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[110] bg-black/70" />
          <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[111] w-[min(90vw,1100px)] rounded-xl p-4 bg-[var(--color-surface-1)] shadow-2xl">
            <Dialog.Title className="text-sm text-[var(--color-ink)] mb-3 pr-8">{title}</Dialog.Title>
            <Dialog.Close aria-label="关闭视频" className="absolute right-3 top-3 text-[var(--color-ink-secondary)]"><X className="w-5 h-5" /></Dialog.Close>
            {open && <video src={videoSrc} className="w-full max-h-[75vh] bg-black" controls autoPlay playsInline />}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
