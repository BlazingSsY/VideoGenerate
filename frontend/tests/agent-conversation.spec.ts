import { test, expect, type Page } from '@playwright/test'

const canvases = [
  { id: 'canvas-a', title: '画布 A' },
  { id: 'canvas-b', title: '画布 B' },
]

function graph(canvas: (typeof canvases)[number]) {
  return {
    ...canvas,
    revision: 1,
    control_version: 0,
    updated_at: '2026-09-14T00:00:00',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [],
    edges: [],
  }
}

async function mockConversationApp(page: Page, assistantText = '收到，开始规划') {
  await page.addInitScript(() => localStorage.setItem('vg_token', 'mock-token'))
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })

    if (path === '/api/auth/me') return json({ id: 'user-1', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '智能体对话回归' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json(canvases.map(graph))
    if (path === '/api/canvases/canvas-a') return json(graph(canvases[0]))
    if (path === '/api/canvases/canvas-b') return json(graph(canvases[1]))
    if (path.endsWith('/status')) return json([])
    if (path.endsWith('/runs/latest')) return route.fulfill({ status: 404, json: { detail: '没有运行记录' } })
    if (path === '/api/agent/models') return json([])
    if (path === '/api/agent/turns' && request.method() === 'POST') {
      return json({ id: 'turn-a', session_id: 'session-a', status: 'draft' })
    }
    if (path === '/api/agent/turns/turn-a/events') {
      return route.fulfill({
        contentType: 'text/event-stream',
        body: [
          'event: text.delta',
          `data: ${JSON.stringify({ seq: 1, text: assistantText })}`,
          '',
          'event: done',
          'data: {"seq":2}',
          '',
          '',
        ].join('\n'),
      })
    }
    return json([])
  })

  await page.goto('/')
  await expect(page.getByText('画布 A', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
}

async function mockInterruptedTurnApp(page: Page) {
  const state = { turnStreamRequests: 0, completed: false }
  await page.addInitScript(() => localStorage.setItem('vg_token', 'mock-token'))
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })

    if (path === '/api/auth/me') return json({ id: 'user-1', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '智能体切换回归' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json(canvases.map(graph))
    if (path === '/api/canvases/canvas-a') return json(graph(canvases[0]))
    if (path === '/api/canvases/canvas-b') return json(graph(canvases[1]))
    if (path.endsWith('/status')) return json([])
    if (path.endsWith('/runs/latest')) return route.fulfill({ status: 404, json: { detail: '没有运行记录' } })
    if (path === '/api/agent/models') return json([])
    if (path.endsWith('/graph')) return json(graph(path.includes('canvas-b') ? canvases[1] : canvases[0]))
    if (path === '/api/agent/turns' && request.method() === 'POST') {
      return json({ id: 'turn-a', session_id: 'session-a', status: 'draft' })
    }
    if (path === '/api/agent/turns/turn-a' && request.method() === 'GET') {
      return json({
        id: 'turn-a', session_id: 'session-a', user_input: '规划一段雪山航拍',
        status: state.completed ? 'ready' : 'draft', steps: [],
      })
    }
    if (path === '/api/agent/turns/turn-a/events') {
      state.turnStreamRequests += 1
      if (state.turnStreamRequests === 1) return
      state.completed = true
      return route.fulfill({
        contentType: 'text/event-stream',
        body: [
          'event: text.delta',
          'data: {"seq":1,"text":"规划已恢复"}',
          '',
          'event: done',
          'data: {"seq":2}',
          '',
          '',
        ].join('\n'),
      })
    }
    if (path === '/api/agent/sessions/session-a/messages') {
      return json(state.completed ? [
        { id: 'message-user', role: 'user', content: '规划一段雪山航拍', turn_id: 'turn-a' },
        { id: 'message-assistant', role: 'assistant', content: '规划已恢复', turn_id: 'turn-a' },
      ] : [])
    }
    if (path === '/api/agent/sessions/session-a/runs') return json([])
    return json([])
  })

  await page.goto('/')
  await expect(page.getByText('画布 A', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  return state
}

test('新会话首轮发送后同时显示用户消息和智能体回复', async ({ page }) => {
  await mockConversationApp(page)

  const composer = page.getByRole('textbox', { name: '描述创意或需求' })
  await composer.fill('生成一段城市夜景')
  await composer.press('Control+Enter')

  const drawer = page.getByRole('complementary', { name: '智能体对话' })
  await expect(drawer.getByText('生成一段城市夜景', { exact: true })).toBeVisible()
  await expect(drawer.getByText('收到，开始规划', { exact: true })).toBeVisible()
})

test('智能体回复将 Markdown 渲染为对话框排版', async ({ page }) => {
  await mockConversationApp(page, [
    '## 任务方案',
    '',
    '1. 分析 **素材**',
    '2. 执行 `render`',
    '',
    '```text',
    'ready',
    '```',
  ].join('\n'))

  const composer = page.getByRole('textbox', { name: '描述创意或需求' })
  await composer.fill('请给出方案')
  await composer.press('Control+Enter')

  const drawer = page.getByRole('complementary', { name: '智能体对话' })
  await expect(drawer.getByRole('heading', { name: '任务方案', level: 2 })).toBeVisible()
  await expect(drawer.locator('strong')).toHaveText('素材')
  await expect(drawer.locator('ol > li')).toHaveCount(2)
  await expect(drawer.locator('code').filter({ hasText: 'render' })).toBeVisible()
  await expect(drawer.locator('pre code')).toHaveText('ready')
})

test('规划中切换画布后可快速切回并恢复进行中的任务', async ({ page }) => {
  const state = await mockInterruptedTurnApp(page)
  const composer = page.getByRole('textbox', { name: '描述创意或需求' })
  await composer.fill('规划一段雪山航拍')
  await composer.press('Control+Enter')
  await expect.poll(() => state.turnStreamRequests).toBe(1)

  const canvasA = page.getByText('画布 A', { exact: true }).locator('..')
  const canvasB = page.getByText('画布 B', { exact: true }).locator('..')
  await canvasB.click()
  await expect(canvasB).toHaveClass(/sidebar-accent/, { timeout: 1000 })
  await canvasA.click()
  await expect(canvasA).toHaveClass(/sidebar-accent/, { timeout: 1000 })

  await expect.poll(() => state.turnStreamRequests).toBe(2)
  const drawer = page.getByRole('complementary', { name: '智能体对话' })
  await expect(drawer.getByText('规划一段雪山航拍', { exact: true })).toBeVisible()
  await expect(drawer.getByText('规划已恢复', { exact: true })).toBeVisible()
})
