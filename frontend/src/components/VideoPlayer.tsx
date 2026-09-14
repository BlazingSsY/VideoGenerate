import { useEffect, useRef, useState, type RefObject } from 'react'
import * as Menu from '@radix-ui/react-dropdown-menu'
import { Check, ChevronRight, Download, Gauge, Loader2, Maximize, Minimize, MoreHorizontal, Pause, PictureInPicture2, Play, Volume2, VolumeX } from 'lucide-react'

const timeLabel = (time: number) => Number.isFinite(time) ? `${Math.floor(time / 60)}:${Math.floor(time % 60).toString().padStart(2, '0')}` : '0:00'

export default function VideoPlayer({ src, title, testId, autoPlay = false, videoRef: externalRef }: {
  src: string; title: string; testId?: string; autoPlay?: boolean; videoRef?: RefObject<HTMLVideoElement>
}) {
  const internalRef = useRef<HTMLVideoElement>(null)
  const videoRef = externalRef || internalRef
  const root = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [duration, setDuration] = useState(0)
  const [time, setTime] = useState(0)
  const [rate, setRate] = useState('1')
  const [waiting, setWaiting] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [error, setError] = useState('')
  const [mediaFailed, setMediaFailed] = useState(false)

  useEffect(() => {
    setTime(0); setDuration(0); setPlaying(false); setError(''); setMediaFailed(false); setWaiting(false)
  }, [src])
  useEffect(() => {
    const change = () => setFullscreen(document.fullscreenElement === root.current)
    document.addEventListener('fullscreenchange', change)
    return () => document.removeEventListener('fullscreenchange', change)
  }, [])

  const togglePlay = async () => {
    const video = videoRef.current
    if (!video) return
    setError('')
    if (mediaFailed) { setMediaFailed(false); video.load() }
    if (video.paused) {
      try { await video.play() } catch { setWaiting(false); setError('暂时无法播放，请重试') }
    } else video.pause()
  }
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === root.current) await document.exitFullscreen()
      else if (root.current?.requestFullscreen) await root.current.requestFullscreen()
      else setError('当前浏览器不支持全屏')
    } catch { setError('暂时无法进入全屏') }
  }
  const pictureInPicture = async () => {
    try {
      if (document.pictureInPictureElement === videoRef.current) await document.exitPictureInPicture()
      else await videoRef.current?.requestPictureInPicture()
    } catch { setError('暂时无法打开画中画，请先播放视频') }
  }
  let downloadUrl: URL | null = null
  try { downloadUrl = new URL(src, window.location.origin) } catch { /* Keep a malformed media URL from crashing the canvas. */ }
  if (downloadUrl?.origin === window.location.origin && downloadUrl.pathname.startsWith('/media/')) downloadUrl.searchParams.set('download', '1')
  const pipAvailable = Boolean(document.pictureInPictureEnabled)

  return (
    <div ref={root} className="video-player nodrag nopan nowheel" role="group" aria-label={`${title}播放器`} onPointerDown={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
      <div className="video-stage">
        <video ref={videoRef} data-testid={testId} src={src} aria-label={title} playsInline preload="none" autoPlay={autoPlay}
          onClick={() => { void togglePlay() }}
          onLoadedMetadata={event => { setDuration(event.currentTarget.duration); event.currentTarget.playbackRate = Number(rate) }}
          onDurationChange={event => setDuration(event.currentTarget.duration)}
          onTimeUpdate={event => setTime(event.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onPlaying={() => setWaiting(false)} onWaiting={() => setWaiting(true)}
          onCanPlay={() => setWaiting(false)} onEnded={() => { setPlaying(false); setWaiting(false) }}
          onVolumeChange={event => setMuted(event.currentTarget.muted || event.currentTarget.volume === 0)}
          onRateChange={event => setRate(String(event.currentTarget.playbackRate))}
          onError={() => { setWaiting(false); setPlaying(false); setMediaFailed(true); setError('视频加载失败，点击播放重试') }}
        />
        {!playing && !waiting && <button type="button" aria-label="播放视频" className="video-play-hero" onClick={() => { void togglePlay() }}><Play className="w-5 h-5 fill-current ml-0.5" /></button>}
        {waiting && <span className="video-loading" role="status" aria-label="视频缓冲中"><Loader2 className="w-6 h-6 animate-spin" /></span>}
      </div>
      <div className="video-control-panel">
        <input type="range" aria-label="视频进度" className="video-progress" min={0} max={Number.isFinite(duration) ? duration : 0} step={0.1} value={time} disabled={!duration}
          style={{ '--video-progress': `${duration ? time / duration * 100 : 0}%` } as React.CSSProperties}
          onChange={event => { const next = Number(event.target.value); if (videoRef.current) videoRef.current.currentTime = next; setTime(next) }} />
        <div className="video-controls">
          <button type="button" aria-label={playing ? '暂停' : '播放'} title={playing ? '暂停' : '播放'} className="video-control video-control-primary" onClick={() => { void togglePlay() }}>{playing ? <Pause /> : <Play />}</button>
          <span className="video-time">{timeLabel(time)}<span> / {timeLabel(duration)}</span></span>
          <div className="flex-1" />
          <button type="button" aria-label={muted ? '取消静音' : '静音'} title={muted ? '取消静音' : '静音'} className="video-control" onClick={() => { if (videoRef.current) videoRef.current.muted = !videoRef.current.muted }}>{muted ? <VolumeX /> : <Volume2 />}</button>
          <Menu.Root modal={false}>
            <Menu.Trigger asChild><button type="button" aria-label="视频选项" title="视频选项" className="video-control"><MoreHorizontal /></button></Menu.Trigger>
            <Menu.Portal container={fullscreen ? root.current : undefined}>
              <Menu.Content className="video-menu nodrag nopan nowheel" align="end" side="top" sideOffset={8} onPointerDown={event => event.stopPropagation()}>
                <Menu.Item asChild><a className="video-menu-item" href={downloadUrl?.href} download={`${title.replace(/[\\/:*?"<>|]/g, '-')}.mp4`}><Download />下载视频</a></Menu.Item>
                <Menu.Sub>
                  <Menu.SubTrigger className="video-menu-item"><Gauge />播放速度<span className="ml-auto text-[10px] opacity-60">{rate}×</span><ChevronRight /></Menu.SubTrigger>
                  <Menu.Portal container={fullscreen ? root.current : undefined}>
                    <Menu.SubContent className="video-menu" sideOffset={6}>
                      <Menu.RadioGroup value={rate} onValueChange={value => { if (videoRef.current) videoRef.current.playbackRate = Number(value); setRate(value) }}>
                        {['0.5', '0.75', '1', '1.25', '1.5', '2'].map(value => <Menu.RadioItem key={value} value={value} className="video-menu-item"><span className="w-4">{rate === value && <Check />}</span>{value === '1' ? '正常 · 1×' : `${value}×`}</Menu.RadioItem>)}
                      </Menu.RadioGroup>
                    </Menu.SubContent>
                  </Menu.Portal>
                </Menu.Sub>
                <Menu.Separator className="h-px my-1 bg-[var(--color-border-soft)]" />
                <Menu.Item className="video-menu-item" disabled={!pipAvailable || !duration} onSelect={() => { void pictureInPicture() }}><PictureInPicture2 />画中画</Menu.Item>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
          <button type="button" aria-label={fullscreen ? '退出全屏' : '全屏'} title={fullscreen ? '退出全屏' : '全屏'} className="video-control" onClick={() => { void toggleFullscreen() }}>{fullscreen ? <Minimize /> : <Maximize />}</button>
        </div>
        {error && <p role="alert" className="px-1 pb-1 text-[10px] text-[var(--color-danger)]">{error}</p>}
      </div>
    </div>
  )
}
