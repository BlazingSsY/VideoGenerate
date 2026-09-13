import { test, expect, type Page } from '@playwright/test'

async function setup(page: Page, legacy = false) {
  const specs = ['image', 'video', 'audio'].map(kind => ({ kind, media_type: `reference_${kind}`, label: kind, min_count: 0, max_count: 5 }))
  const models = [
    { id: 'wan3.0-video-prime', label: 'Wan 3.0', capabilities: [{ id: 'r2v', label: '参考生视频', media_inputs: specs }] },
    { id: 'happyhorse-1.1-r2v', label: 'HappyHorse', capabilities: [{ id: 'r2v', label: '参考生视频', media_inputs: specs.slice(0, 1) }] },
  ].map(model => ({ ...model, resolutions: ['720P'], default_resolution: '720P', durations: [5], default_duration: 5, ratio_options: {} }))
  let graph: any = {
    id: 'interactions', title: '素材引用与画布外观', revision: 1, control_version: 0,
    updated_at: '2026-09-13T00:00:00', viewport: { x: 0, y: 0, zoom: 0.7 },
    nodes: [
      { id: 'image-a', type: 'image', position: { x: 0, y: 0 }, data: { name: '主角.png', url: 'https://files.test/actor.png' } },
      { id: 'image-b', type: 'image', position: { x: 0, y: 310 }, data: { name: '街景.png', url: 'https://files.test/street.png' } },
      { id: 'video-a', type: 'video', position: { x: 330, y: 0 }, data: { name: '运镜.mp4', kind: 'video', url: 'https://files.test/camera.mp4' } },
      { id: 'audio-a', type: 'audio', position: { x: 330, y: 310 }, data: { name: '配音.wav', kind: 'audio', url: 'https://files.test/voice.wav' } },
      { id: 'gen', type: 'generate', position: { x: 680, y: 0 }, data: {
        name: '参考生成', model: 'wan3.0-video-prime', capability: 'r2v', resolution: '720P', ratio: '16:9', duration: 5,
        inlinePrompt: '{{图片1}} 在 {{图片2}} 中行走，动作参考 {{视频1}}，声音参考 {{音频1}}。',
        reference_bindings: [
          { edge_id: 'image-a', alias: '图片1', description: '主角外观' },
          { edge_id: 'image-b', alias: '图片2', description: '街道背景' },
          { edge_id: 'video-a', alias: '视频1', description: '镜头运动' },
          { edge_id: 'audio-a', alias: '音频1', description: '对白声音' },
        ],
      } },
    ].map(node => ({ ...node, status: '', message_id: null })),
    edges: ['image-a', 'image-b', 'video-a', 'audio-a'].map(id => ({
      id, source: id, source_handle: id.split('-')[0], target: 'gen', target_handle: `${id.split('-')[0]}_0`,
    })),
  }
  if (legacy) {
    const data = graph.nodes.find((node: any) => node.id === 'gen').data
    delete data.reference_bindings
    data.inlinePrompt = '旧画布的内联提示词'
    graph.nodes.push({ id: 'prompt', type: 'prompt', position: { x: 0, y: -180 }, data: { text: '已连接的旧提示词' }, status: '', message_id: null })
    graph.edges.push({ id: 'prompt', source: 'prompt', source_handle: 'prompt', target: 'gen', target_handle: 'prompt' })
  }
  await page.addInitScript(() => localStorage.setItem('vg_token', 'mock-token'))
  await page.route('https://files.test/**', route => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAADElEQVR4nGNgGAUgAAAABQABhAsAhwAAAABJRU5ErkJggg==', 'base64') }))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/api/auth/me') return json({ id: 'user', username: 'admin', display_name: '管理员', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '视频生成工作台' })
    if (path === '/api/models') return json({ models })
    if (path === '/api/canvases') return json([graph])
    if (path === '/api/canvases/interactions') return json(graph)
    if (path.endsWith('/graph')) { graph = { ...graph, ...route.request().postDataJSON(), revision: graph.revision + 1 }; return json(graph) }
    return json([])
  })
  await page.goto('/')
  await expect(page.getByTestId('reference-settings')).toBeVisible()
  return () => graph
}

test('添加节点始终悬停展开，移除设置开关，工具图标即时高亮', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('vg-add-node-hover', 'false'))
  await setup(page)
  const button = page.getByTestId('add-node-btn')
  await button.hover()
  await expect(page.getByTestId('add-node-menu')).toBeVisible()
  await page.getByTestId('node-tool-prompt').hover()
  await page.getByTestId('node-tool-prompt').click()
  await expect(page.locator('.react-flow__node-prompt')).toHaveCount(1)
  await button.hover()
  await expect(page.getByRole('checkbox', { name: '悬停自动展开' })).toHaveCount(0)
  await page.locator('header').click({ position: { x: 10, y: 10 } })
  await button.hover()
  await expect(page.getByTestId('add-node-menu')).toBeVisible()
  await page.reload()
  await button.hover()
  await expect(page.getByTestId('add-node-menu')).toBeVisible()
  const library = page.getByTestId('tool-library')
  await library.hover()
  await expect(library).toHaveCSS('transition-duration', '0s')
  await expect(library).toHaveCSS('color', 'rgb(153, 198, 237)')
})

test('右上角日月开关位于管理员左侧，浅色连线使用协调配色的三层流光，刷新保留主题', async ({ page }, testInfo) => {
  await setup(page)
  const toggle = page.getByRole('switch', { name: '浅色主题' })
  const account = page.getByTestId('account-menu-btn')
  expect((await toggle.boundingBox())!.x).toBeLessThan((await account.boundingBox())!.x)
  await expect(page.locator('header').getByTestId('theme-toggle')).toHaveCount(1)
  await expect(toggle).not.toBeChecked()
  const darkEdge = page.locator('.react-flow__edge').first()
  await expect(darkEdge.locator('path').nth(1)).toHaveCSS('filter', 'blur(6px)')
  await expect(darkEdge.locator('path').nth(2)).toHaveCSS('filter', 'blur(3px)')
  await expect(darkEdge.locator('animate').first()).toHaveAttribute('dur', '3.5s')
  await expect(page.locator('.canvas-edge-shimmer')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('canvas-dark.png'), animations: 'disabled' })
  await toggle.click()
  await expect(toggle).toBeChecked()
  await expect(toggle.locator('.theme-switch-thumb')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 30, 0)')
  await expect(page.locator('html')).toHaveClass(/theme-light/)
  const halo = page.locator('.canvas-edge-halo').first()
  await expect(halo).toHaveCSS('filter', 'blur(6px)')
  await expect(halo).toHaveCSS('opacity', '0.16')
  await expect(page.locator('.canvas-edge-glow').first()).toHaveCSS('filter', 'blur(3px)')
  await expect(page.locator('.canvas-edge-line').first()).toHaveCSS('stroke-width', '2px')
  await expect(page.locator('.canvas-edge-line').first()).toHaveCSS('stroke', /url\(.*#edge-gradient-/)
  await expect(page.locator('.canvas-edge').first().locator('animateTransform')).toHaveAttribute('dur', '3.5s')
  await expect(page.locator('.canvas-edge').first().locator('stop').first()).toHaveAttribute('stop-color', '#9380bd')
  await page.screenshot({ path: testInfo.outputPath('canvas-light.png'), animations: 'disabled' })
  await page.reload()
  await expect(toggle).toBeChecked()
  await toggle.press('Space')
  await expect(toggle).not.toBeChecked()
  await expect(darkEdge.locator('animate').first()).toHaveAttribute('dur', '3.5s')
})

test('打开旧参考画布或移动节点不会自动启用引用设置，首次插入引用才保存绑定', async ({ page }) => {
  const saved = await setup(page, true)
  await page.getByText('查看实际发送的提示词', { exact: true }).click()
  await expect(page.getByTestId('reference-prompt-preview')).toHaveText('已连接的旧提示词')
  const card = page.locator('.react-flow__node-generate')
  const box = (await card.boundingBox())!
  await page.mouse.move(box.x + 40, box.y + 10)
  await page.mouse.down()
  await page.mouse.move(box.x + 90, box.y + 35, { steps: 8 })
  await page.mouse.up()
  await expect.poll(() => saved().nodes.find((node: any) => node.id === 'gen').position.x).not.toBe(680)
  expect(saved().nodes.find((node: any) => node.id === 'gen').data.reference_bindings).toBeUndefined()
  await page.getByTestId('reference-image-a').getByRole('button', { name: '{{图片1}}', exact: true }).click()
  await expect.poll(() => saved().nodes.find((node: any) => node.id === 'gen').data.reference_bindings?.length).toBe(4)
  await expect(page.getByTestId('reference-prompt-preview')).toContainText('旧画布的内联提示词图 1')
})

test('多素材设置显示分别编号，调整发送顺序后固定引用仍指向原素材，刷新保留设置', async ({ page }) => {
  const saved = await setup(page)
  await expect(page.getByTestId('reference-image-a')).toContainText('图 1')
  await expect(page.getByTestId('reference-video-a')).toContainText('视频 1')
  await expect(page.getByTestId('reference-audio-a')).toContainText('音频 1')
  await page.getByRole('button', { name: '下移图片1', exact: true }).click()
  await expect(page.getByTestId('reference-image-a')).toContainText('图 2')
  await expect(page.getByTestId('reference-image-a')).toContainText('主角.png')
  await page.getByRole('textbox', { name: '图片1用途', exact: true }).fill('只参考人物服装')
  await page.getByText('查看实际发送的提示词', { exact: true }).click()
  const preview = page.getByTestId('reference-prompt-preview')
  await expect(preview).toContainText('图 2 在 图 1 中行走，动作参考 视频 1，声音参考 音频 1。')
  await expect(preview).toContainText('图 2：只参考人物服装')
  await expect.poll(() => saved().nodes.find((node: any) => node.id === 'gen').data.reference_bindings[0].edge_id).toBe('image-b')
  await expect.poll(() => saved().nodes.find((node: any) => node.id === 'gen').data.reference_bindings.find((item: any) => item.alias === '图片1').description).toBe('只参考人物服装')
  await page.reload()
  await expect(page.getByTestId('reference-image-a')).toContainText('图 2')
  await page.getByTestId('reference-image-a').getByRole('button', { name: '{{图片1}}', exact: true }).click()
  await expect(page.locator('.react-flow__node-generate textarea')).toHaveValue(/。\{\{图片1\}\}$/)
})

test('切换模型调整引用格式，删除素材后固定引用不会误指向其他素材', async ({ page }) => {
  await setup(page)
  const prompt = page.locator('.react-flow__node-generate textarea')
  await prompt.fill('{{图片1}} 向前走')
  await page.locator('.react-flow__node-generate button[role="combobox"]').first().click()
  await page.getByRole('option', { name: 'HappyHorse', exact: true }).click()
  await expect(page.getByTestId('reference-image-a')).toContainText('[Image 1]')
  await page.getByText('查看实际发送的提示词', { exact: true }).click()
  await expect(page.getByTestId('reference-prompt-preview')).toContainText('[Image 1] 向前走')
  await page.locator('.react-flow__node-image[data-id="image-a"]').click({ position: { x: 30, y: 15 } })
  await page.keyboard.press('Backspace')
  await expect(page.getByTestId('reference-image-a')).toHaveCount(0)
  await expect(page.getByTestId('reference-settings').getByRole('alert')).toContainText('图片1')
  await expect(page.getByTestId('reference-image-b')).toContainText('{{图片2}}')
})
