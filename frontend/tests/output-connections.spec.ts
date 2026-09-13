import { test, expect, type Page } from '@playwright/test'
import type { CanvasDetail } from '../src/canvasTypes'

async function openOutputCanvas(page: Page, otherOutput = false) {
  let saved: CanvasDetail = {
    id: 'output-connections', title: '多片段输出', revision: 1, control_version: 0,
    created_at: '2026-09-13T00:00:00', updated_at: '2026-09-13T00:00:00',
    viewport: { x: 0, y: 0, zoom: 0.7 },
    nodes: [
      ...['一', '二'].map((label, i) => ({
        id: `gen-${i + 1}`, type: 'generate' as const, position: { x: 100, y: i * 360 },
        data: { name: `片段${label}`, model: 'wan3.0-video-prime', capability: 't2v' as const,
          resolution: '720P', ratio: '16:9', duration: 5, watermark: false, audio: false, inlinePrompt: label },
        status: '' as const, message_id: null,
      })),
      ...[1, 2].map(i => ({
        id: `prompt-${i}`, type: 'prompt' as const, position: { x: -280, y: (i - 1) * 260 },
        data: { text: `提示词 ${i}` }, status: '' as const, message_id: null,
      })),
      { id: 'out', type: 'output', position: { x: 650, y: 140 },
        data: { label: '最终输出' }, status: '', message_id: null },
    ],
    edges: [],
  }
  if (otherOutput) saved.nodes.push({ id: 'out-other', type: 'output', position: { x: 1000, y: 500 }, data: { label: '另一个输出' }, status: '', message_id: null })
  await page.addInitScript(() => localStorage.setItem('vg_token', 'mock-token'))
  // Keep this regression independent of local accounts, databases and generation APIs.
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/api/auth/me') return json({ id: 'tester', username: 'tester', role: 'admin' })
    if (path === '/api/config') return json({ app_name: '多片段输出回归' })
    if (path === '/api/models') return json({ models: [] })
    if (path === '/api/canvases') return json([saved])
    if (path === `/api/canvases/${saved.id}`) return json(saved)
    if (path === `/api/canvases/${saved.id}/graph`) {
      saved = { ...saved, ...route.request().postDataJSON(), revision: saved.revision + 1 }
      return json(saved)
    }
    return json([])
  })
  await page.goto('/')
  await expect(page.locator('.react-flow__node')).toHaveCount(otherOutput ? 6 : 5)
  return () => saved
}

async function connect(page: Page, source: string, sourceHandle: string, target: string, targetHandle: string) {
  const handle = (node: string, port: string, kind: string) => page.locator(
    `.react-flow__node[data-id="${node}"] .react-flow__handle.${kind}[data-handleid="${port}"]`,
  )
  const from = await handle(source, sourceHandle, 'source').boundingBox()
  const to = await handle(target, targetHandle, 'target').boundingBox()
  expect(from).toBeTruthy()
  expect(to).toBeTruthy()
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2)
  await page.mouse.down()
  await page.mouse.move(to!.x + to!.width / 2, to!.y + to!.height / 2, { steps: 12 })
  await page.mouse.up()
}

test('最终输出接入多个生成节点，保留片段顺序并在刷新后恢复全部连线', async ({ page }) => {
  const saved = await openOutputCanvas(page)
  for (let i = 1; i <= 2; i++) {
    await connect(page, `gen-${i}`, 'output', 'out', 'input')
    await expect(page.locator('.react-flow__edge')).toHaveCount(i)
    await expect(page.getByTestId(`output-segment-${i - 1}`)).toHaveAttribute('data-seg-key', `gen-${i}`)
  }
  await expect(page.locator('.react-flow__node-output')).toContainText('2 段')

  // Persist an explicit sequence by adding a transition between the two clips.
  await page.getByTestId('transition-select-0').click()
  await page.getByRole('option', { name: '淡入淡出', exact: true }).click()
  await expect.poll(() => saved().edges.length).toBe(2)
  await expect.poll(() => saved().nodes.find(n => n.id === 'out')?.data.items)
    .toMatchObject([{ nodeKey: 'gen-1' }, { nodeKey: 'gen-2' }])
  await expect.poll(() => saved().nodes.find(n => n.id === 'out')?.data.transitions)
    .toEqual([{ after: 'gen-1', type: 'fade' }])

  await page.reload()
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await expect(page.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-1')
  await expect(page.getByTestId('output-segment-1')).toHaveAttribute('data-seg-key', 'gen-2')
  await expect(page.getByTestId('transition-select-0')).toContainText('淡入淡出')
})

test('输出多连接仍拒绝重复及非视频连线，提示词输入仍只接一条线', async ({ page }) => {
  const saved = await openOutputCanvas(page)
  await connect(page, 'gen-1', 'output', 'out', 'input')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await connect(page, 'gen-1', 'output', 'out', 'input')
  await connect(page, 'prompt-1', 'prompt', 'out', 'input')
  await connect(page, 'prompt-1', 'prompt', 'gen-1', 'prompt')
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await connect(page, 'prompt-2', 'prompt', 'gen-1', 'prompt')
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await expect.poll(() => saved().edges.length).toBe(2)
  expect(saved().edges.map(e => [e.source, e.target])).toEqual([
    ['gen-1', 'out'], ['prompt-1', 'gen-1'],
  ])
})

test('删除输出片段同时移除对应连线，保留其他连接，刷新与重新连接均正常', async ({ page }) => {
  const saved = await openOutputCanvas(page, true)
  await connect(page, 'gen-1', 'output', 'out', 'input')
  await connect(page, 'gen-2', 'output', 'out', 'input')
  await connect(page, 'gen-1', 'output', 'out-other', 'input')
  await connect(page, 'prompt-1', 'prompt', 'gen-1', 'prompt')
  await expect(page.locator('.react-flow__edge')).toHaveCount(4)
  const output = page.locator('.react-flow__node[data-id="out"]')
  await output.getByTestId('output-segment-0').getByRole('button', { name: '移除片段' }).click()
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)
  await expect(output.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-2')
  await expect(page.locator('.react-flow__node[data-id="out-other"]').getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-1')
  await expect(page.locator('.react-flow__node-generate')).toHaveCount(2)
  await expect.poll(() => saved().edges.map(edge => [edge.source, edge.target])).toEqual([
    ['gen-2', 'out'], ['gen-1', 'out-other'], ['prompt-1', 'gen-1'],
  ])
  await page.reload()
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)
  await expect(output.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-2')
  await connect(page, 'gen-1', 'output', 'out', 'input')
  await expect(page.locator('.react-flow__edge')).toHaveCount(4)
  await expect(output.getByTestId('output-segment-1')).toHaveAttribute('data-seg-key', 'gen-1')
  await expect.poll(() => saved().nodes.find(node => node.id === 'out')?.data.items).toMatchObject([{ nodeKey: 'gen-2' }, { nodeKey: 'gen-1' }])
})

test('缩放画布内用鼠标拖动片段可排序，节点位置不变，刷新后顺序与转场保留', async ({ page }, testInfo) => {
  const saved = await openOutputCanvas(page)
  for (let i = 1; i <= 2; i++) {
    await connect(page, `gen-${i}`, 'output', 'out', 'input')
    await expect(page.locator('.react-flow__edge')).toHaveCount(i)
  }
  await page.getByTestId('transition-select-0').click()
  await page.getByRole('option', { name: '淡入淡出', exact: true }).click()
  const position = saved().nodes.find(node => node.id === 'out')!.position
  const from = (await page.getByTestId('output-segment-1').boundingBox())!
  const to = (await page.getByTestId('output-segment-0').boundingBox())!
  await page.mouse.move(from.x + from.width * 0.4, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(to.x + to.width * 0.4, to.y + to.height / 2, { steps: 16 })
  const preview = page.getByTestId('output-drag-preview')
  await expect(preview).toBeVisible()
  await expect(preview).toHaveCSS('opacity', '0.72')
  await expect(preview).toHaveCSS('pointer-events', 'none')
  const previewBox = (await preview.boundingBox())!
  expect(previewBox.y).toBeLessThan(from.y)
  expect(previewBox.width).toBeCloseTo(from.width, 1)
  expect(previewBox.height).toBeCloseTo(from.height, 1)
  await expect(page.getByRole('button', { name: /[上下]移片段/ })).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('output-drag-preview.png') })
  await page.mouse.up()
  await expect(preview).toHaveCount(0)
  await expect(page.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-2')
  await expect.poll(() => saved().nodes.find(node => node.id === 'out')!.data.items)
    .toMatchObject([{ nodeKey: 'gen-2' }, { nodeKey: 'gen-1' }])
  expect(saved().nodes.find(node => node.id === 'out')!.position).toEqual(position)
  expect(saved().nodes.find(node => node.id === 'out')!.data.transitions).toEqual([{ after: 'gen-1', type: 'fade' }])
  await page.reload()
  await expect(page.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-2')
  // Check the same preview size and sorting after changing the canvas zoom.
  await page.getByRole('button', { name: 'zoom in' }).click()
  const second = (await page.getByTestId('output-segment-1').boundingBox())!
  const first = (await page.getByTestId('output-segment-0').boundingBox())!
  await page.mouse.move(second.x + second.width * 0.4, second.y + second.height / 2)
  await page.mouse.down()
  await page.mouse.move(first.x + first.width * 0.4, first.y + first.height / 2, { steps: 10 })
  const resizedPreview = (await preview.boundingBox())!
  expect(resizedPreview.width).toBeCloseTo(second.width, 1)
  expect(resizedPreview.height).toBeCloseTo(second.height, 1)
  await page.mouse.up()
  await expect(page.getByTestId('output-segment-0')).toHaveAttribute('data-seg-key', 'gen-1')
  await expect(page.getByTestId('transition-select-0')).toHaveText('淡入淡出')
})
