import { memo, useEffect, useRef, useState } from 'react'
import { App as AntApp, Alert, Button, Card, Empty, Modal, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { DownloadOutlined, ExpandOutlined, FullscreenOutlined, PlayCircleFilled, RedoOutlined, VideoCameraOutlined } from '@ant-design/icons'
import { errorText } from '../api'
import type { Message, ModeId, VideoModel } from '../types'
import { MODE_COLORS } from '../types'
import { downloadMessageVideo } from '../utils/downloadVideo'

interface Props {
  messages: Message[]
  models: VideoModel[]
  onRetry: (assistant: Message) => void
}

const ratioLabel = (r?: string) => (r === 'adaptive' ? '自适应' : r)

function VideoPoster({ message, onPreview }: { message: Message; onPreview: () => void }) {
  const params = message.params || {}
  return (
    <Card className="video-preview-card" bodyStyle={{ padding: 0 }}>
      <button className="video-poster" type="button" onClick={onPreview} aria-label="打开视频预览">
        <span className="video-poster-icon"><PlayCircleFilled /></span>
        <span className="video-poster-meta">{params.resolution || '视频'} · {params.duration || '?'} 秒</span>
      </button>
      <div className="video-card-meta">
        <Typography.Text ellipsis>{message.model}</Typography.Text>
        <Tooltip title="打开视频预览"><Button type="text" icon={<ExpandOutlined />} onClick={onPreview} aria-label="打开视频预览" /></Tooltip>
      </div>
    </Card>
  )
}

function videoSrc(message: Message) {
  // 地址由后端签发，前端不再自己拼 /media/ 路径
  return message.video_src || message.video_url
}

function ParamTags({ message, models }: { message: Message; models: VideoModel[] }) {
  const model = models.find((m) => m.id === message.model)
  const params = message.params || {}
  const mode = params.capability as ModeId | undefined
  const capability = mode ? model?.capabilities.find((c) => c.id === mode) : undefined
  return (
    <Space size={[4, 4]} wrap style={{ marginTop: 8 }}>
      <Tag color="geekblue">{model?.label || message.model}</Tag>
      {capability && <Tag color={MODE_COLORS[capability.id]}>{capability.label}</Tag>}
      {params.resolution && <Tag>{params.resolution}</Tag>}
      {params.ratio && <Tag>{ratioLabel(params.ratio)}</Tag>}
      {params.duration && <Tag>{params.duration} 秒</Tag>}
      {params.watermark === true && <Tag>含水印</Tag>}
      {params.audio === false && <Tag>无音频</Tag>}
      {params.skill_name && <Tag color="magenta">Skill · {params.skill_name}</Tag>}
      {params.use_context === false && <Tag color="orange">不延续上下文</Tag>}
    </Space>
  )
}

const UserMessage = memo(function UserMessage({ message, models }: { message: Message; models: VideoModel[] }) {
  const media = message.reference_media?.length
    ? message.reference_media
    : (message.reference_images || []).map((url, index) => ({
        kind: 'image' as const,
        url,
        signed_url: message.reference_image_urls?.[index] || url,
        name: '参考图',
      }))
  return (
    <div className="msg-row user">
      <div className="msg-bubble">
        <div className="msg-prompt">{message.prompt}</div>
        {media.length > 0 && (
          <Space wrap style={{ marginTop: 10 }}>
            {media.map((item, index) => {
              const src = item.signed_url || item.url
              return item.kind === 'image' ? (
                <img key={item.url + index} className="ref-thumb" src={src} alt={item.name || '参考图'} />
              ) : item.kind === 'video' ? (
                <div key={item.url + index} className="ref-thumb ref-video ref-video-placeholder" title={item.name || '参考视频'}>
                  <VideoCameraOutlined />
                  <span>{item.name || '参考视频'}</span>
                </div>
              ) : (
                <audio
                  key={item.url + index}
                  className="ref-audio"
                  src={src}
                  controls
                  preload="metadata"
                />
              )
            })}
          </Space>
        )}
        <ParamTags message={message} models={models} />
      </div>
    </div>
  )
})

const AssistantMessage = memo(function AssistantMessage({
  message,
  models,
  onRetry,
  onPreview,
}: {
  message: Message
  models: VideoModel[]
  onRetry: (m: Message) => void
  onPreview: (m: Message) => void
}) {
  const { message: toast } = AntApp.useApp()
  const [downloading, setDownloading] = useState(false)

  const startDownload = async () => {
    setDownloading(true)
    try {
      await downloadMessageVideo(message, `${message.id.slice(0, 8)}.mp4`)
    } catch (error) {
      toast.error(errorText(error, '下载失败'))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="msg-row">
      <div className="msg-bubble" style={{ minWidth: 300 }}>
        {(message.status === 'pending' || message.status === 'running') && (
          <div className="status-pill">
            <Spin size="small" />
            <Typography.Text type="secondary">
              {message.status === 'pending'
                ? '正在整理提示词并提交任务…'
                : '视频生成中，通常需要 1-5 分钟，可离开页面稍后回来查看…'}
            </Typography.Text>
          </div>
        )}

        {message.status === 'failed' && (
          <>
            <Alert type="error" showIcon message="生成失败" description={message.error} />
            <Button
              size="small"
              icon={<RedoOutlined />}
              style={{ marginTop: 10 }}
              onClick={() => onRetry(message)}
            >
              用相同参数重试
            </Button>
          </>
        )}

        {message.status === 'succeeded' && message.video_expired && (
          <Alert
            type="info"
            showIcon
            message="视频已过期清理"
            description="超过保留期的视频会自动删除以释放磁盘空间。提示词和参数都还在，可以重新生成。"
          />
        )}

        {message.status === 'succeeded' && !message.video_expired && (
          <>
            <VideoPoster message={message} onPreview={() => onPreview(message)} />
            <div className="video-actions">
              <Button
                size="small"
                type="primary"
                className="btn-gradient"
                icon={<DownloadOutlined />}
                loading={downloading}
                onClick={startDownload}
              >
                下载到本地
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
})

function MessageList({ messages, models, onRetry }: Props) {
  const { message: toast } = AntApp.useApp()
  const [preview, setPreview] = useState<Message | null>(null)
  const [downloading, setDownloading] = useState(false)

  if (messages.length === 0) {
    return (
      <div style={{ paddingTop: 80 }}>
        <Empty
          description={
            <Typography.Text type="secondary">
              输入提示词开始生成第一个视频，之后可以在同一个对话里持续修改
            </Typography.Text>
          }
        />
      </div>
    )
  }

  return (
    <>
      {messages.map((message) =>
        message.role === 'user' ? (
          <UserMessage key={message.id} message={message} models={models} />
        ) : (
          <AssistantMessage
            key={message.id}
            message={message}
            models={models}
            onRetry={onRetry}
            onPreview={setPreview}
          />
        ),
      )}

      <Modal
        open={!!preview}
        onCancel={() => setPreview(null)}
        footer={null}
        width={880}
        centered
        destroyOnHidden
        title="视频预览"
      >
        {preview && (
          <VideoPreviewPlayer message={preview} downloading={downloading} setDownloading={setDownloading} toast={toast} />
        )}
      </Modal>
    </>
  )
}

function VideoPreviewPlayer({ message, downloading, setDownloading, toast }: { message: Message; downloading: boolean; setDownloading: (value: boolean) => void; toast: ReturnType<typeof AntApp.useApp>['message'] }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [waitingSince, setWaitingSince] = useState<number | null>(null)
  const [waitMs, setWaitMs] = useState(0)
  const [stallCount, setStallCount] = useState(0)

  useEffect(() => {
    setLoading(true); setFailed(false); setWaitingSince(null); setStallCount(0); setWaitMs(0)
  }, [message.id])

  const observe = (event: string) => {
    if (event === 'loadedmetadata' || event === 'canplay' || event === 'playing') setLoading(false)
    if (event === 'playing') {
      if (waitingSince !== null) setWaitMs((total) => total + performance.now() - waitingSince)
      setWaitingSince(null)
    }
    if (event === 'waiting') { setWaitingSince(performance.now()); setStallCount((count) => count + 1) }
    if (event === 'error' || event === 'stalled') setFailed(true)
    if (typeof window !== 'undefined' && window.localStorage.getItem('video-preview-debug') === '1') {
      console.debug(`[video-preview] ${event}`, { messageId: message.id })
    }
  }

  const enterFullscreen = () => videoRef.current?.requestFullscreen?.().catch(() => undefined)
  const startDownload = async () => {
    setDownloading(true)
    try { await downloadMessageVideo(message, `${message.id.slice(0, 8)}.mp4`) }
    catch (error) { toast.error(errorText(error, '下载失败')) }
    finally { setDownloading(false) }
  }

  return (
    <Card className="video-player-card" bodyStyle={{ padding: 0 }}>
      {loading && <div className="video-loading"><Spin tip="正在加载视频…" /></div>}
      {failed && <Alert type="error" showIcon message="视频加载失败" description="请关闭预览后重试；原始视频下载仍可用。" />}
      <video ref={videoRef} className="preview-video" src={videoSrc(message)} controls controlsList="nodownload" preload="metadata" playsInline
        onLoadedMetadata={() => observe('loadedmetadata')} onCanPlay={() => observe('canplay')} onPlaying={() => observe('playing')} onWaiting={() => observe('waiting')} onStalled={() => observe('stalled')} onError={() => observe('error')} />
      <div className="video-player-footer">
        <Typography.Text type="secondary">{message.params?.resolution} · {message.params?.duration} 秒{stallCount ? ` · ${stallCount} 次等待` : ''}{waitMs ? ` · 等待 ${(waitMs / 1000).toFixed(1)} 秒` : ''}</Typography.Text>
        <Space>
          <Tooltip title="全屏"><Button type="text" icon={<FullscreenOutlined />} onClick={enterFullscreen} aria-label="全屏" /></Tooltip>
          <Button type="primary" className="btn-gradient" icon={<DownloadOutlined />} loading={downloading} onClick={startDownload}>下载</Button>
        </Space>
      </div>
    </Card>
  )
}

export default memo(MessageList)
