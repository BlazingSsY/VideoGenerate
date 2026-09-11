import api from '../api'
import type { Message } from '../types'

type DownloadableMessage = Pick<Message, 'id'> & Partial<Pick<Message, 'video_src' | 'video_url'>>

function clickDownload(url: string, filename: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export async function downloadMessageVideo(message: DownloadableMessage, filename = 'video.mp4') {
  const source = message.video_src || message.video_url
  if (source) {
    const directUrl = new URL(source, window.location.origin)
    if (directUrl.origin === window.location.origin && directUrl.pathname.startsWith('/media/videos/')) {
      // 复用已有签名链接，让浏览器直接流式保存；不再先把整段视频读进 JS 内存。
      directUrl.searchParams.set('download', '1')
      clickDownload(directUrl.toString(), filename)
      return
    }
  }

  // 未落盘的历史任务保留兼容路径；新任务默认都会走上面的直接下载。
  try {
    const response = await api.get(`/api/messages/${message.id}/download`, { responseType: 'blob' })
    const objectUrl = URL.createObjectURL(response.data)
    clickDownload(objectUrl, filename)
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  } catch (error: any) {
    if (error?.response?.data instanceof Blob) {
      try {
        const detail = JSON.parse(await error.response.data.text()).detail
        if (typeof detail === 'string') throw new Error(detail)
      } catch (parsedError) {
        if (parsedError instanceof Error && !(parsedError instanceof SyntaxError)) {
          throw parsedError
        }
      }
    }
    throw error
  }
}
