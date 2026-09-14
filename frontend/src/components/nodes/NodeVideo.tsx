import { useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowUpRight, X } from 'lucide-react'
import VideoPlayer from '../VideoPlayer'

export default function NodeVideo({ src, title, testId }: { src: string; title: string; testId: string }) {
  const [open, setOpen] = useState(false)
  const inlineVideo = useRef<HTMLVideoElement>(null)
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
      <VideoPlayer testId={testId} src={videoSrc} title={title} videoRef={inlineVideo} />
      <Dialog.Root open={open} onOpenChange={value => { if (value) inlineVideo.current?.pause(); setOpen(value) }}>
        <Dialog.Trigger asChild>
          <button className="video-expand-button">
            观看视频 <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[110] bg-black/70" />
          <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[111] w-[min(90vw,1100px)] rounded-xl p-4 bg-[var(--color-surface-1)] shadow-2xl">
            <Dialog.Title className="text-sm text-[var(--color-ink)] mb-3 pr-8">{title}</Dialog.Title>
            <Dialog.Close aria-label="关闭视频" className="absolute right-3 top-3 text-[var(--color-ink-secondary)]"><X className="w-5 h-5" /></Dialog.Close>
            {open && <VideoPlayer src={videoSrc} title={title} testId={`${testId}-expanded`} autoPlay />}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
