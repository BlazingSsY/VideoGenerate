import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

let clip: Buffer
test.beforeAll(() => {
  const directory = mkdtempSync(join(tmpdir(), 'vg-player-test-'))
  try {
    const filename = join(directory, 'clip.mp4')
    execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=25',
      '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', filename])
    clip = readFileSync(filename)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

async function setup(page: Page, withOutput = true) {
  const state = { run: null as any, complete: false, readyShots: [] as string[], statusReads: 0, failSaves: false, saves: 0, starts: 0, nodeStarts: [] as string[], compositions: 0,
    saved: {
      id: 'run-canvas', title: '完整画布运行', revision: 1, control_version: 0,
      updated_at: '2026-09-13T00:00:00', viewport: { x: 0, y: 0, zoom: 0.7 },
      nodes: [
        ...[1, 2].map(i => ({ id: `shot-${i}`, type: 'generate', position: { x: 100, y: (i - 1) * 370 },
          data: { name: `镜头${i}`, model: 'wan3.0-video-prime', capability: 't2v', resolution: '720P', ratio: '16:9', duration: 5, inlinePrompt: `旧提示词${i}` },
          status: '', message_id: null })),
        ...(withOutput ? [{ id: 'out', type: 'output', position: { x: 600, y: 80 },
          data: { label: '最终输出', items: [{ nodeKey: 'shot-2' }, { nodeKey: 'shot-1' }], transitions: [{ after: 'shot-2', type: 'fade' }] },
          status: '', message_id: null }] : []),
      ] as any[],
      edges: withOutput ? [1, 2].map(i => ({ id: `edge-${i}`, source: `shot-${i}`, source_handle: 'output', target: 'out', target_handle: 'input' })) : [] as any[],
    },
  }
  await page.addInitScript(() => localStorage.setItem('vg_token', 'test-token'))
  await page.route('**/media/videos/*.mp4*', route => route.fulfill({ contentType: 'video/mp4', body: clip }))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/api/auth/me') return json({ id: 'user', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '完整画布运行' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json([state.saved])
    if (path === '/api/canvases/run-canvas') return json(state.saved)
    if (path.endsWith('/graph')) {
      state.saves++
      if (state.failSaves) return route.fulfill({ status: 409, json: { detail: '保存冲突' } })
      state.saved = { ...state.saved, ...route.request().postDataJSON(), revision: state.saved.revision + 1 }
      return json(state.saved)
    }
    if (path === '/api/canvases/run-canvas/run') {
      state.starts++
      expect(route.request().postDataJSON()).toEqual({ revision: state.saved.revision })
      state.run = { id: 'run-1', canvas_id: 'run-canvas', status: 'running', tasks: state.saved.nodes.map(node => ({
        id: node.id, node_id: node.id, canvas_node_id: node.id, task_type: node.type === 'output' ? 'compose' : 'generate', status: 'queued',
      })) }
      return json(state.run)
    }
    if (path.endsWith('/runs/latest')) {
      if (state.run && state.complete) state.run = { ...state.run, status: 'succeeded', tasks: state.run.tasks.map((task: any) => ({ ...task, status: 'succeeded' })) }
      return json(state.run)
    }
    if (path.endsWith('/status')) {
      state.statusReads++
      return json(state.saved.nodes.map(node => ({
      node_id: node.id, message_id: null,
      status: state.complete || state.readyShots.includes(node.id) ? 'succeeded' : state.run ? 'queued' : '',
      video_src: state.complete ? `/media/videos/${node.id}.mp4` : state.readyShots.includes(node.id)
        ? `/media/videos/${node.id}.mp4?exp=${Math.floor(Date.now() / 1000) + 3600}&sig=${state.statusReads}` : '',
    }))) }
    if (path.includes('/nodes/') && path.endsWith('/run')) {
      state.nodeStarts.push(path)
      return json({ status: 'pending' })
    }
    if (path.endsWith('/compose')) { state.compositions++; return json({ video_src: '/media/videos/out.mp4' }) }
    return json([])
  })
  await page.goto('/')
  await expect(page.locator('.react-flow__node-generate')).toHaveCount(2)
  return state
}

test('一键运行先保存最新输入与输出顺序，刷新恢复进度，各节点直接播放视频', async ({ page }) => {
  const state = await setup(page)
  const toolbar = page.getByTestId('canvas-action-toolbar')
  await expect(toolbar.getByTestId('run-canvas-btn')).toBeVisible()
  const undo = (await toolbar.getByTestId('undo-canvas-btn').boundingBox())!
  const run = (await toolbar.getByTestId('run-canvas-btn').boundingBox())!
  expect(run.x).toBeGreaterThan(undo.x)
  expect(Math.abs(run.y - undo.y)).toBeLessThan(2)
  await page.locator('.react-flow__node[data-id="shot-1"] textarea').fill('点击运行前的新提示词')
  await page.getByTestId('run-canvas-btn').click()
  await expect.poll(() => state.starts).toBe(1)
  expect(state.saved.nodes.find(node => node.id === 'shot-1').data.inlinePrompt).toBe('点击运行前的新提示词')
  expect(state.saved.nodes.find(node => node.id === 'out').data.items.map((item: any) => item.nodeKey)).toEqual(['shot-2', 'shot-1'])
  await expect(page.getByTestId('run-canvas-btn')).toBeDisabled()
  await expect(page.getByTestId('compose-final-btn')).toHaveText('等待片段完成…')
  await page.reload()
  await expect(page.getByTestId('run-canvas-btn')).toHaveText('运行中 0/3')
  state.complete = true
  await expect(page.getByText('全部完成，可在节点中观看视频')).toBeVisible({ timeout: 8000 })
  await expect(page.getByTestId('generated-video-shot-1')).toHaveAttribute('controls', '')
  await expect(page.getByTestId('generated-video-shot-2')).toHaveAttribute('src', '/media/videos/shot-2.mp4')
  await expect(page.getByTestId('final-video')).toHaveAttribute('src', '/media/videos/out.mp4')
  for (const id of ['shot-1', 'shot-2', 'out']) {
    await page.locator(`.react-flow__node[data-id="${id}"]`).getByRole('button', { name: '观看视频' }).click()
    const video = page.getByRole('dialog').locator('video')
    await expect(video).toBeVisible()
    await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(0)
    await page.getByRole('button', { name: '关闭视频', exact: true }).click()
  }
  expect(state.starts).toBe(1)
  expect(state.nodeStarts).toEqual([])
  expect(state.compositions).toBe(0)
})

test('没有输出节点时自动创建并连入全部镜头', async ({ page }) => {
  const state = await setup(page, false)
  await page.getByTestId('run-canvas-btn').click()
  await expect.poll(() => state.starts).toBe(1)
  await expect(page.locator('.react-flow__node-output')).toHaveCount(1)
  const output = state.saved.nodes.find(node => node.type === 'output')
  expect(output.data.items.map((item: any) => item.nodeKey)).toEqual(['shot-1', 'shot-2'])
  expect(state.saved.edges.map(edge => [edge.source, edge.target])).toEqual([['shot-1', output.id], ['shot-2', output.id]])
})

test('单节点运行只调用该节点接口，并先保存编辑', async ({ page }) => {
  const state = await setup(page)
  const node = page.locator('.react-flow__node[data-id="shot-2"]')
  await node.locator('textarea').fill('单独修改第二镜头')
  await node.getByRole('button', { name: '运行', exact: true }).click()
  await expect.poll(() => state.nodeStarts).toEqual(['/api/canvases/run-canvas/nodes/shot-2/run'])
  expect(state.saved.nodes.find(node => node.id === 'shot-2').data.inlinePrompt).toBe('单独修改第二镜头')
  expect(state.starts).toBe(0)
})

test('保存冲突时不提交画布运行', async ({ page }) => {
  const state = await setup(page)
  state.failSaves = true
  await page.locator('.react-flow__node[data-id="shot-1"] textarea').fill('尚未保存')
  await page.getByTestId('run-canvas-btn').click()
  await expect(page.getByRole('alert')).toContainText('保存冲突')
  expect(state.starts).toBe(0)
  await expect(page.locator('.react-flow__node[data-id="shot-1"] textarea')).toHaveValue('尚未保存')
})

test('其他镜头仍在生成时，轮询更新签名不会打断已完成镜头的播放', async ({ page }) => {
  const state = await setup(page)
  await page.getByTestId('run-canvas-btn').click()
  await expect.poll(() => state.starts).toBe(1)
  state.readyShots = ['shot-1']
  const node = page.locator('.react-flow__node[data-id="shot-1"]')
  await node.getByRole('button', { name: '观看视频' }).click()
  const previousReads = state.statusReads
  const video = page.getByRole('dialog').locator('video')
  const src = await video.getAttribute('src')
  await expect.poll(() => state.statusReads).toBeGreaterThan(previousReads)
  await expect(video).toHaveAttribute('src', src!)
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(2.2)
})
