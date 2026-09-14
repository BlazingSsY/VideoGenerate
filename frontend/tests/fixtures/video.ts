import { readFileSync } from 'node:fs'
import type { Route } from '@playwright/test'

// Synthetic three-second clip, served with the byte ranges browsers need to seek.
const clip = readFileSync(new URL('./player.mp4', import.meta.url))
export function serveVideo(route: Route) {
  const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/)
  const start = range ? Number(range[1]) : 0
  const end = range?.[2] ? Math.min(Number(range[2]), clip.length - 1) : clip.length - 1
  return route.fulfill({
    status: range ? 206 : 200,
    contentType: 'video/mp4',
    headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${clip.length}` } : {}) },
    body: clip.subarray(start, end + 1),
  })
}
