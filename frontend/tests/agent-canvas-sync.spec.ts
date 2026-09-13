import { test, expect, type Page, type Route } from '@playwright/test'

const canvas = {
  id: 'canvas-1', title: '同步回归', revision: 1, control_version: 0,
  updated_at: '2026-09-13T00:00:00', viewport: { x: 0, y: 0, zoom: 1 },
  nodes: [{ id: 'prompt-1', type: 'prompt', position: { x: 120, y: 120 }, data: { text: '已保存内容' }, status: '', message_id: null }],
  edges: [],
}

async function mockApp(page: Page, restoreRun: boolean) {
  const state = { graphReads: 0, statusReads: 0, runStream: null as Route | null, reload: null as Route | null, holdReload: false, rejectSaves: false }
  let savedCanvas = structuredClone(canvas)
  await page.addInitScript(({ restoreRun }) => {
    localStorage.setItem('vg_token', 'mock-token')
    if (restoreRun) localStorage.setItem('vg-agent-session-canvas-1', 'session-1')
  }, { restoreRun })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/api/auth/me') return json({ id: 'user-1', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '同步回归' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json([canvas])
    if (path === '/api/canvases/canvas-1') {
      state.graphReads++
      if (state.holdReload) { state.reload = route; return }
      return json(savedCanvas)
    }
    if (path === '/api/canvases/canvas-1/status') {
      state.statusReads++
      return json([{ node_id: 'prompt-1', status: 'succeeded', message_id: null }])
    }
    // Keep edits unsaved while events arrive; never contact a real backend.
    if (path.endsWith('/graph')) {
      if (state.rejectSaves) return route.fulfill({ status: 409, json: { detail: '模拟保存冲突' } })
      savedCanvas = { ...savedCanvas, ...route.request().postDataJSON(), revision: savedCanvas.revision + 1 }
      return json(savedCanvas)
    }
    if (path === '/api/agent/sessions/session-1/runs') return json([{ id: 'run-1', turn_id: 'turn-1', status: 'running', tasks: [] }])
    if (path === '/api/agent/runs/run-1/events') { state.runStream = route; return }
    if (path === '/api/agent/turns') return json({ id: 'turn-1', session_id: 'session-1', status: 'draft' })
    if (path === '/api/agent/turns/turn-1/events') return route.fulfill({
      contentType: 'text/event-stream', body: 'event: canvas.changed\ndata: {"seq":1,"canvas_id":"canvas-1"}\n\nevent: done\ndata: {"seq":2}\n\n',
    })
    return json([])
  })
  await page.goto('/')
  await expect(page.locator('.react-flow__node-prompt textarea')).toHaveValue('已保存内容')
  return state
}

test('运行 SSE 更新状态时保留未保存提示词，不重载画布', async ({ page }) => {
  const state = await mockApp(page, true)
  await expect.poll(() => Boolean(state.runStream)).toBe(true)
  const prompt = page.locator('.react-flow__node-prompt textarea')
  state.rejectSaves = true
  await prompt.fill('尚未保存的新提示词')
  const previousReads = state.graphReads
  const previousStatuses = state.statusReads
  await state.runStream!.fulfill({
    contentType: 'text/event-stream', body: 'event: task.status\ndata: {"seq":1,"node_id":"shot-1","status":"succeeded"}\n\nevent: done\ndata: {"seq":1}\n\n',
  })
  await expect.poll(() => state.statusReads).toBeGreaterThan(previousStatuses)
  await expect(prompt).toHaveValue('尚未保存的新提示词')
  expect(state.graphReads).toBe(previousReads)
})

test('图更新请求在途时的新编辑不会被迟到的响应覆盖', async ({ page }) => {
  const state = await mockApp(page, false)
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  state.holdReload = true
  const chat = page.locator('textarea').last()
  await chat.fill('调整当前画布')
  await chat.press('Control+Enter')
  await expect.poll(() => Boolean(state.reload)).toBe(true)
  state.rejectSaves = true
  const prompt = page.locator('.react-flow__node-prompt textarea')
  await prompt.fill('请求发出后继续编辑')
  await state.reload!.fulfill({ json: { ...canvas, revision: 2 } })
  await expect(prompt).toHaveValue('请求发出后继续编辑')
  await expect(page.getByText('智能体已更新画布。本地编辑仍保留，请保存或处理版本冲突后再刷新。')).toBeVisible()
})
