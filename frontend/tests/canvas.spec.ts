import { test, expect, type Page } from '@playwright/test'

/**
 * 画布交互 e2e（替代旧 responsive.spec.ts 的 AntD 选择器）。
 * 覆盖本次修复：
 * 1. Provider 真值注入 —— 生成节点的模型下拉有内容、可运行
 * 2. 模式切换即时重渲染（原卡顿 bug）
 * 3. i2v 尾帧槽（wan3 模型）与 HappyHorse 无尾帧
 * 4. r2v 槽位自由数量 + 到达上限禁用
 * 5. 素材节点连线到动态槽位 handle
 *
 * 节点内下拉是 Radix Select：trigger 是 button[role=combobox]，
 * 空值时 trigger 无文本（getByText 不可用）；options 渲染在 body 下的 portal。
 */

async function login(page: Page) {
  await page.goto('/')
  const user = page.locator('input[type="text"]')
  if (await user.isVisible()) {
    await user.fill('admin')
    await page.locator('input[type="password"]').fill('admin123')
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page.locator('input[type="password"]')).toBeHidden({ timeout: 10000 })
  }
}

async function openCanvas(page: Page) {
  await login(page)
  await page.locator('[data-testid="new-canvas-btn"]').click()
  await page.waitForTimeout(800)
}

/** 添加节点：工具栏加号 → 节点菜单 */
async function addNodes(page: Page, types: string[]) {
  for (const type of types) {
    await page.locator('[data-testid="add-node-btn"]').click()
    await page.locator(`[data-testid="node-tool-${type}"]`).click()
    await page.waitForTimeout(400)
  }
}

/** 生成节点的第 n 个下拉（0=模型 1=模式 2=分辨率 3=比例 4=时长），选中 label 匹配项 */
async function pickNodeSelect(page: Page, index: number, label: string | RegExp) {
  const trigger = page.locator('.react-flow__node-generate button[role="combobox"]').nth(index)
  await trigger.click()
  const option = page.locator('[role="option"]', { hasText: label }).last()
  await expect(option).toBeVisible({ timeout: 5000 })
  await option.click()
  await page.waitForTimeout(300)
}

type Rect = { x: number; y: number; width: number; height: number }

/** 画布 pane 自身 rect：所有拖拽落点/抓取点由它推导。此前用固定视口坐标 (150,700)/(100,350)，
 *  在各桌面视口下落在侧栏区域（pane 起点 x≈165），把节点甩到 pane 左侧外、handle 悬进侧栏，
 *  mouse.up 永远按不到 handle → 0 边（:187 回归根源）。 */
async function paneBox(page: Page) {
  const b = await page.locator('.react-flow').boundingBox()
  expect(b).toBeTruthy()
  return b as Rect
}

/** a 框内找一个未被任一 blocker 覆盖的点（6×6 网格）；全部被盖住返回 null。
 *  两卡随机落位常重叠（后加的卡在 DOM 上层），盲抓"卡片顶部"会抓错卡。 */
function safePoint(a: Rect, blockers: (Rect | null)[]): { x: number; y: number } | null {
  for (let i = 0; i <= 5; i++) for (let j = 0; j <= 5; j++) {
    const x = a.x + (a.width * i) / 5
    const y = a.y + (a.height * j) / 5
    const covered = (b: Rect | null) => !!b && x >= b.x && x <= b.x + b.width && y >= b.y && y <= b.y + b.height
    if (blockers.every(b => !covered(b))) return { x, y }
  }
  return null
}

async function dragNode(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(to.x, to.y, { steps: 10 })
  await page.mouse.up()
  await page.waitForTimeout(400)
}

/** 依次把若干节点拖到 pane 内的相对位置（px/py ∈ (0,1)），相互错开并避开 pane 边缘
 *  （靠近边缘会触发 auto-pan 平移 viewport，节点又被带回一起）。
 *  每次拖拽前重新取全部节点盒：safePoint 保证抓取点落在目标卡自身、未被其他卡盖住
 *  （卡片随机落位常重叠且后加的在 DOM 上层，盲抓会拖错卡）。 */
async function separateNodes(
  page: Page,
  plan: { node: ReturnType<Page['locator']>; to: { px: number; py: number } }[],
) {
  const pane = await paneBox(page)
  const all = plan.map(p => p.node)
  for (const item of plan) {
    const boxes = await Promise.all(all.map(n => n.boundingBox()))
    const me = await item.node.boundingBox()
    if (!me) continue
    const blockers = boxes.filter(b => b && b !== me) as Rect[]
    const sp = safePoint(me, blockers)
    if (sp) {
      await dragNode(page, sp, {
        x: pane.x + pane.width * item.to.px,
        y: pane.y + pane.height * item.to.py,
      })
    }
  }
}

// 生成节点的编辑交互（下拉/± 槽位/连线）目标场景是桌面画布；
// ≤375px 时悬浮工具栏会遮挡节点，无有效交互可测，跳过移动 project。
test.describe('生成节点与动态槽位', () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name.startsWith('mobile-'),
      '移动视口下悬浮工具栏遮挡节点，生成节点交互为桌面场景')
  })
  test('Provider 注入真值后模型下拉非空且模式切换即时生效', async ({ page }) => {
    await openCanvas(page)
    await addNodes(page, ['generate'])
    await expect(page.locator('.react-flow__node-generate')).toHaveCount(1, { timeout: 5000 })
    await page.locator('.react-flow__node-generate button[role="combobox"]').first().click()
    const options = page.locator('[role="option"]')
    await expect(options.first()).toBeVisible({ timeout: 5000 })
    expect(await options.count()).toBeGreaterThan(1)
    await page.getByRole('option', { name: /Wan 3.0/ }).click()
    await page.waitForTimeout(400)
    // 默认 t2v → 无媒体槽行
    await expect(page.locator('.react-flow__node-generate').getByText(/参考图|首帧/)).toHaveCount(0)
    // 切 r2v 应即时重渲染（原卡顿 bug：切换后无重渲染）；行标签现为 图片（不带数字）
    await pickNodeSelect(page, 1, '参考生视频')
    await expect(page.locator('.react-flow__node-generate').getByText('图片', { exact: true })).toBeVisible({ timeout: 5000 })
    // 切 i2v：文字行不渲染（只留边框连接点），handle 语义经 data-handle-label 表达
    await pickNodeSelect(page, 1, '图生视频')
    await expect(page.locator('.react-flow__node-generate').getByText('首帧图')).toHaveCount(0)
    await expect(
      page.locator('.react-flow__node-generate .react-flow__handle-left[data-handleid="image_0"]')
    ).toHaveAttribute('data-handle-label', '首帧图')
  })

  test('i2v 模式显示尾帧槽，HappyHorse 无尾帧', async ({ page }) => {
    await openCanvas(page)
    await addNodes(page, ['generate'])
    await pickNodeSelect(page, 0, /Wan 3.0/)
    await pickNodeSelect(page, 1, '图生视频')
    // wan3 i2v: 首帧 + 尾帧（i2v 无文字行，槽位以 handle 表达）
    const genNode = page.locator('.react-flow__node-generate')
    await expect(genNode.locator('.react-flow__handle-left[data-handleid="end_frame_0"]')).toHaveCount(1)
    // 换 HappyHorse I2V（该模型 i2v 无尾帧 spec）
    await pickNodeSelect(page, 0, /HappyHorse 1.1 I2V/)
    // 保持 i2v 模式；HappyHorse i2v 无尾帧 → 无「尾帧图」行
    await expect(genNode.locator('.react-flow__handle-left[data-handleid="end_frame_0"]')).toHaveCount(0)
  })

  test('r2v 每类素材只渲染一个连接点，标签无数字无 ± 按钮', async ({ page }) => {
    await openCanvas(page)
    await addNodes(page, ['generate'])
    await pickNodeSelect(page, 0, /Wan 3.0/)
    await pickNodeSelect(page, 1, '参考生视频')
    const genNode = page.locator('.react-flow__node-generate')
    // 三类各一个点（image_0/video_0/audio_0），无加号按钮
    await expect(genNode.locator('.react-flow__handle-left[data-handleid="image_0"]')).toHaveCount(1)
    await expect(genNode.locator('.react-flow__handle-left[data-handleid="video_0"]')).toHaveCount(1)
    await expect(genNode.locator('.react-flow__handle-left[data-handleid="audio_0"]')).toHaveCount(1)
    expect(await genNode.locator('button[data-slot-add]').count()).toBe(0)
    // 卡片行显示 图片/视频/音频（不再是 参考图 ×1）
    await expect(genNode.getByText('图片', { exact: true })).toBeVisible()
    await expect(genNode.getByText('视频', { exact: true })).toBeVisible()
    await expect(genNode.getByText('音频', { exact: true })).toBeVisible()
  })

  test('i2v 卡片无首帧/尾帧文字行，语义只在 handle 弹出标签', async ({ page }) => {
    await openCanvas(page)
    await addNodes(page, ['generate'])
    await pickNodeSelect(page, 0, /Wan 3.0/)
    await pickNodeSelect(page, 1, '图生视频')
    const genNode = page.locator('.react-flow__node-generate')
    // 两个连接点：image_0 = 首帧图、end_frame_0 = 尾帧图；handle 的 data-handle-label 无数字
    const firstFrame = genNode.locator('.react-flow__handle-left[data-handleid="image_0"]')
    const endFrame = genNode.locator('.react-flow__handle-left[data-handleid="end_frame_0"]')
    await expect(firstFrame).toHaveCount(1)
    await expect(endFrame).toHaveCount(1)
    await expect(firstFrame).toHaveAttribute('data-handle-label', '首帧图')
    await expect(endFrame).toHaveAttribute('data-handle-label', '尾帧图')
    // 卡片内不渲染文字行，只留边框连接点
    await expect(genNode.getByText('首帧图', { exact: true })).toHaveCount(0)
    await expect(genNode.getByText('尾帧图', { exact: true })).toHaveCount(0)
  })

  test('r2v 聚合点可多条线共连（两个图片节点連同一 image_0）', async ({ page }, testInfo) => {
    // 窄视口(768)下卡片互相重叠、拖拽落点被遮挡，与 mobile 跳过同理：连线属桌面场景
    test.skip(testInfo.project.name === 'tablet-768', '窄视口节点卡片重叠，连线拖拽仅桌面视口可靠')
    await openCanvas(page)
    await addNodes(page, ['image', 'image', 'generate'])
    await expect(page.locator('.react-flow__node')).toHaveCount(3, { timeout: 5000 })
    await pickNodeSelect(page, 0, /Wan 3.0/)
    await pickNodeSelect(page, 1, '参考生视频')
    const genNode = page.locator('.react-flow__node-generate')
    const imgs = page.locator('.react-flow__node-image')
    // 两个 img 节点拖开避免重叠，再各自连到同一 image_0 聚合点。
    // pane 相对落点+避边缘（旧绝对坐标落在侧栏且会触发 auto-pan）。
    await separateNodes(page, [
      { node: genNode, to: { px: 0.65, py: 0.25 } },
      { node: imgs.nth(0), to: { px: 0.15, py: 0.45 } },
      { node: imgs.nth(1), to: { px: 0.15, py: 0.8 } },
    ])
    const tgt = genNode.locator('.react-flow__handle-left[data-handleid="image_0"]')
    for (let i = 0; i < 2; i++) {
      await expect(async () => {
        const edges = await page.locator('.react-flow__edge').count()
        if (edges < i + 1) {
          const src = imgs.nth(i).locator('.react-flow__handle-right[data-handleid="image"]')
          const s = await src.boundingBox()
          const t = await tgt.boundingBox()
          expect(s && t).toBeTruthy()
          if (s && t) {
            await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2)
            await page.mouse.down()
            await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 12 })
            await page.mouse.up()
            await page.waitForTimeout(500)
          }
        }
        await expect(page.locator('.react-flow__edge')).toHaveCount(i + 1, { timeout: 800 })
      }).toPass({ timeout: 15000 })
    }
    // 保存后刷新不回退（保存 bug 回归：聚合多边可保存、不 400）
    await page.locator('[data-testid="save-canvas-btn"]').click()
    await page.waitForTimeout(800)
    await expect(page.locator('[data-testid="save-error"]')).toHaveCount(0)
    await page.reload()
    await login(page)
    await expect(page.locator('.react-flow__edge')).toHaveCount(2, { timeout: 8000 })
  })

  test('连线：图片素材的 end_frame handle 可连到 i2v 尾帧槽', async ({ page }, testInfo) => {
    // 与 :187 r2v 聚合同理：窄视口(768)卡片重叠、拖拽落点被遮挡，连线仅桌面视口可靠
    test.skip(testInfo.project.name === 'tablet-768', '窄视口节点卡片重叠，连线拖拽仅桌面视口可靠')
    await openCanvas(page)
    await addNodes(page, ['image', 'generate'])
    await expect(page.locator('.react-flow__node')).toHaveCount(2, { timeout: 5000 })
    await pickNodeSelect(page, 0, /Wan 3.0/)
    await pickNodeSelect(page, 1, '图生视频')
    // 新节点随机落在画布左上且常互相重叠（后加的卡在 DOM 上层，盲抓会抓错卡）。
    // 分离策略：先把 generate 拖到 pane 右上内侧，再把 image 拖到 pane 左下内侧。
    // 两点都必须留在 pane 内缩 15% 的安全区 —— 拖拽目标若靠近 pane 边缘，会触发
    // ReactFlow auto-pan 平移整个 viewport，两卡很快又叠回一起（此前 0 边回归的根因）。
    const imgNode = page.locator('.react-flow__node-image')
    await separateNodes(page,
      [{ node: page.locator('.react-flow__node-generate'), to: { px: 0.65, py: 0.25 } },
       { node: imgNode, to: { px: 0.15, py: 0.75 } }])
    // 需求⑧：图片素材单一 image handle；连到 i2v 尾帧槽即语义 = 尾帧图（颜色区分）
    const src = page.locator('.react-flow__node-image .react-flow__handle-right[data-handleid="image"]')
    const tgt = page.locator('.react-flow__node-generate .react-flow__handle-left[data-handleid="end_frame_0"]')
    await expect(src).toHaveCount(1)
    await expect(tgt).toHaveCount(1)
    // 连线包进 toPass：若拖拽恰好落在拖动后 Handle 重挂载的瞬间，点击会静默不建边；
    // 每次重试前重读两 handle 的最新几何再连一次。
    await expect(async () => {
      if ((await page.locator('.react-flow__edge').count()) === 0) {
        const s = await src.boundingBox()
        const t = await tgt.boundingBox()
        expect(s && t).toBeTruthy()
        if (s && t) {
          await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2)
          await page.mouse.down()
          await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 12 })
          await page.mouse.up()
          await page.waitForTimeout(500)
        }
      }
      await expect(page.locator('.react-flow__edge')).toHaveCount(1, { timeout: 800 })
    }).toPass({ timeout: 15000 })
  })

  test('生成节点默认选 wan3.0，运行按钮可用', async ({ page }) => {
    await openCanvas(page)
    await addNodes(page, ['generate'])
    await expect(page.locator('.react-flow__node-generate')).toHaveCount(1, { timeout: 5000 })
    // 需求④：新建生成节点默认模型 wan3.0-video-prime，运行按钮不再因空模型禁用
    await expect(page.locator('.react-flow__node-generate')).toContainText('Wan 3.0')
    const runBtn = page.locator('.react-flow__node-generate').getByRole('button', { name: /运行/ })
    await expect(runBtn).toBeEnabled()
  })
})

test.describe('素材库完整版', () => {
  // 注意：测试库跨并行 worker 共享（5 个 project 同时跑同一 DB），
  // 名称必须带随机后缀使 hasText 过滤只命中本次运行的行，before+1 计数才不被邻 worker 干扰。
  test('上传素材 → 自动入库 → 列表显示 → 拖拽条目（+ 按钮）落到画布', async ({ page }) => {
    await openCanvas(page)

    // 打开素材库面板
    await page.locator('[data-testid="tool-library"]').click()
    await expect(page.locator('[data-testid="library-panel"]')).toBeVisible()
    // 列表初次加载完成后再计数（加载中时数为 0，会把 before 测成 0）
    await expect(page.locator('[data-testid="library-panel"]')).not.toContainText('加载中')

    const waveName = `海浪${Math.random().toString(36).slice(2, 7)}`
    const waveItems = page.locator('[data-testid="lib-item"]', { hasText: waveName })
    const before = await waveItems.count()

    // 上传 PNG（DataInjection: set_input_files）—— 注意 setInputFiles 用固定文件名，
    // 上传后列表里按名称匹配的是 Asset.name（= 上传文件名 stem）
    await page.locator('[data-testid="library-panel"] input[type="file"]')
      .setInputFiles({ name: `${waveName}.png`, mimeType: 'image/png', buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAADElEQVR4nGNgGAUgAAAABQABhAsAhwAAAABJRU5ErkJggg==', 'base64',
      ) })
    await expect(waveItems).toHaveCount(before + 1, { timeout: 8000 })

    // 点 ＋ 按钮把素材加进画布（created_at desc，first 即本次上传的）
    await waveItems.first().locator('button[title="加入画布"]').click()
    await expect(page.locator('.react-flow__node-image')).toHaveCount(1)
    // 节点应显示素材预览图
    await expect(page.locator('.react-flow__node-image img').first()).toBeVisible({ timeout: 5000 })

    // 面板在加节点后自动关闭
    await expect(page.locator('[data-testid="library-panel"]')).toBeHidden({ timeout: 3000 })
  })

  test('外链登记 → kind 筛选 → 删除', async ({ page }) => {
    await openCanvas(page)
    await page.locator('[data-testid="tool-library"]').click()
    await expect(page.locator('[data-testid="library-panel"]')).toBeVisible()
    await expect(page.locator('[data-testid="library-panel"]')).not.toContainText('加载中')

    const surfName = `冲浪视频${Math.random().toString(36).slice(2, 7)}`
    const surfItems = page.locator('[data-testid="lib-item"]', { hasText: surfName })
    const before = await surfItems.count()

    // 打开外链表单
    await page.locator('[data-testid="library-panel"] button[title="登记外链"]').click()
    await page.locator('[data-testid="library-panel"] input[placeholder="名称"]').fill(surfName)
    await page.locator('[data-testid="lib-link-kind-video"]').click()
    await page.locator('[data-testid="library-panel"] input[placeholder^="https"]').fill('https://cdn.example.com/wave.mp4')
    await page.locator('[data-testid="library-panel"] button', { hasText: '登记' }).click()
    await expect(surfItems).toHaveCount(before + 1, { timeout: 5000 })
    await expect(surfItems.first()).toContainText('外链')

    // image 筛选下不应出现视频条目
    await page.locator('[data-testid="lib-filter-image"]').click()
    await expect(surfItems).toHaveCount(0, { timeout: 5000 })

    // 回全部 → 删除（最新一条即本次登记的）
    await page.locator('[data-testid="lib-filter-all"]').click()
    await expect(surfItems.first().locator('button[title="删除"]')).toBeVisible({ timeout: 5000 })
    await surfItems.first().locator('button[title="删除"]').click()
    await expect(surfItems).toHaveCount(before, { timeout: 5000 })
  })
})
