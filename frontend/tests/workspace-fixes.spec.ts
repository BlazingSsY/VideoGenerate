import { test, expect, type Page, type Route } from '@playwright/test'

async function workspace(page: Page, delayModels = false, images = false, video = false) {
  const state = { modelRoute: null as Route | null, turns: [] as any[], historyReads: 0 }
  const canvases = ['a', 'b'].map(id => ({
    id, title: `画布 ${id}`, revision: 1, updated_at: '2026-09-14T00:00:00',
    viewport: { x: 0, y: 0, zoom: 1 }, edges: [],
    nodes: video && id === 'a' ? [{ id: 'shot', type: 'generate', position: { x: 0, y: 0 }, data: { name: '预览视频', model: 'test', capability: 't2v', duration: 5, ratio: '16:9', resolution: '720P' } }] : images && id === 'a' ? [{ id: 'image', type: 'image', position: { x: 0, y: 0 }, data: { name: '大图', url: '/media/uploads/large.png', signed_url: '/media/uploads/large.png?exp=9999999999&sig=test' } }] : [],
  }))
  await page.addInitScript(() => localStorage.setItem('vg_token', 'test-token'))
  await page.route('**/api/**', async route => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/api/auth/me') return json({ id: 'u', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '工作台回归' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json(canvases)
    if (/\/api\/canvases\/[ab]$/.test(path)) return json(canvases.find(c => path.endsWith(c.id)))
    if (path.endsWith('/graph')) return json({ ...req.postDataJSON(), revision: 2 })
    if (path.endsWith('/status')) return json(video ? [{ node_id: 'shot', status: 'succeeded', video_src: '/tests/fixtures/player.mp4' }] : [])
    if (path.endsWith('/runs/latest')) return route.fulfill({ status: 404, json: {} })
    if (path === '/api/agent/models') {
      if (delayModels) { state.modelRoute = route; return }
      return json([{ id: 'model-1', name: '创作模型' }])
    }
    if (path === '/api/agent/turns') {
      state.turns.push(req.postDataJSON())
      return json({ id: 'turn-1', session_id: 'session-1', status: 'draft' })
    }
    if (path.endsWith('/turns/turn-1/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: text.delta\ndata: {"seq":1,"text":"这是第一轮回复"}\n\nevent: done\ndata: {"seq":2}\n\n' })
    if (path === '/api/agent/sessions') {
      state.historyReads++
      return json(state.turns.length ? [{ id: 'session-1', title: '第一轮创意', updated_at: '2026-09-14T00:00:00' }] : [])
    }
    if (path.endsWith('/sessions/session-1/messages')) return json([
      { id: 'm1', role: 'user', content: '第一轮创意', turn_id: 'turn-1' },
      { id: 'm2', role: 'assistant', content: '这是第一轮回复', turn_id: 'turn-1' },
    ])
    return json([])
  })
  await page.goto('/')
  await expect(page.getByText('画布 a', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  return state
}

test('模型初始化完成后首轮直接使用可用模型，选择器可以正常打开', async ({ page }) => {
  const state = await workspace(page, true)
  const input = page.getByRole('textbox', { name: '描述创意或需求' })
  await input.fill('第一轮创意')
  await input.press('Control+Enter')
  expect(state.turns).toHaveLength(0)
  await state.modelRoute!.fulfill({ json: [{ id: 'model-1', name: '创作模型' }] })
  await expect(page.getByRole('combobox', { name: '智能体模型' })).toContainText('创作模型')
  await input.press('Control+Enter')
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
  expect(state.turns[0].agent_model_id).toBe('model-1')
  await page.getByRole('combobox', { name: '智能体模型' }).click()
  await expect(page.getByRole('option', { name: '创作模型' })).toBeVisible()
})

test('新建对话后历史记录可恢复，关闭重开也保留消息', async ({ page }) => {
  await workspace(page)
  const input = page.getByRole('textbox', { name: '描述创意或需求' })
  await input.fill('第一轮创意')
  await input.press('Control+Enter')
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
  await page.getByTitle('新建对话', { exact: true }).click()
  await page.getByTitle('聊天记录', { exact: true }).click()
  await page.getByRole('button', { name: /第一轮创意/ }).click({ timeout: 2500 })
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
  await page.getByTitle('关闭', { exact: true }).click()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
})

test('侧栏可拖宽并自适应长消息，宽度在重开后保留', async ({ page }) => {
  await workspace(page)
  const drawer = page.getByRole('complementary', { name: '智能体对话' })
  const before = (await drawer.boundingBox())!
  const handle = page.getByRole('separator', { name: '调整智能体宽度' })
  const box = (await handle.boundingBox({ timeout: 2500 }))!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x - 200, box.y + box.height / 2, { steps: 10 })
  await page.mouse.up()
  expect((await drawer.boundingBox())!.width).toBeGreaterThan(before.width + 150)
  await page.getByRole('textbox', { name: '描述创意或需求' }).fill('https://example.com/' + 'long-unbroken-text'.repeat(50))
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.getByText('这是第一轮回复', { exact: true })).toBeVisible()
  await page.getByTitle('关闭', { exact: true }).click()
  await page.getByRole('button', { name: '智能体', exact: true }).click()
  expect((await drawer.boundingBox())!.width).toBeGreaterThan(before.width + 150)
  expect(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  await page.screenshot({ animations: 'disabled', path: '../artifacts/workspace-drawer-wide.png' })
  for (let i = 0; i < 15; i++) await handle.press('ArrowRight')
  expect(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
})

test('窄屏会话占满可用宽度，消息不横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await workspace(page)
  const drawer = page.getByRole('complementary', { name: '智能体对话' })
  expect((await drawer.boundingBox())!.width).toBe(375)
  await expect(page.getByRole('separator', { name: '调整智能体宽度' })).toBeHidden()
  expect(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
})

test('图片下载未结束仍能切换画布，旧图片节点立即卸载', async ({ page }) => {
  await page.route('**/media/**', () => {})
  await workspace(page, false, true)
  await expect(page.locator('.react-flow__node-image')).toBeVisible()
  await page.route('**/api/canvases/b', () => {})
  await page.getByText('画布 b', { exact: true }).click()
  await expect(page.locator('.react-flow__node-image')).toHaveCount(0, { timeout: 1000 })
  await page.getByText('画布 a', { exact: true }).click()
  await expect(page.locator('.react-flow__node-image')).toBeVisible({ timeout: 1000 })
})


test('自定义播放器支持播放、倍速、进度、静音、全屏和下载菜单', async ({ page }) => {
  await workspace(page, false, false, true)
  await page.getByTitle('关闭', { exact: true }).click()
  const node = page.locator('.react-flow__node-generate')
  const video = node.locator('video')
  await expect(video).not.toHaveAttribute('controls')
  await node.getByRole('button', { name: '播放', exact: true }).click()
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(0)
  await node.getByRole('button', { name: '暂停', exact: true }).click()
  await node.getByRole('button', { name: '视频选项' }).click()
  await page.getByRole('menuitem', { name: /播放速度/ }).hover()
  await page.getByRole('menuitemradio', { name: '1.5×', exact: true }).click()
  expect(await video.evaluate((el: HTMLVideoElement) => el.playbackRate)).toBe(1.5)
  await node.getByRole('slider', { name: '视频进度' }).fill('1.2')
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeCloseTo(1.2, 1)
  await node.getByRole('button', { name: '静音', exact: true }).click()
  expect(await video.evaluate((el: HTMLVideoElement) => el.muted)).toBe(true)
  await node.getByRole('button', { name: '全屏', exact: true }).click()
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(true)
  await page.getByRole('button', { name: '退出全屏', exact: true }).click()
  await node.getByRole('button', { name: '视频选项' }).click()
  await expect(page.getByRole('menuitem', { name: '下载视频' })).toHaveAttribute('href', /tests\/fixtures\/player\.mp4/)
  await expect(page.getByRole('menuitem', { name: '画中画' })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: '下载视频' }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe('预览视频.mp4')
  expect(await file.failure()).toBeNull()
  await page.keyboard.press('Escape')
  await node.getByRole('button', { name: '观看视频' }).click()
  const expanded = page.getByRole('dialog')
  await expect.poll(() => expanded.locator('video').evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(0)
  await expanded.getByRole('button', { name: '暂停', exact: true }).click()
  await expanded.getByRole('button', { name: '视频选项' }).click()
  await page.screenshot({ animations: 'disabled', path: '../artifacts/workspace-player-dark.png' })
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '关闭视频' }).click()
  await page.getByTestId('theme-toggle').click()
  await node.getByRole('button', { name: '观看视频' }).click()
  await expanded.getByRole('button', { name: '视频选项' }).click()
  await page.screenshot({ animations: 'disabled', path: '../artifacts/workspace-player-light.png' })
})
