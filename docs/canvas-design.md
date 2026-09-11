# 创意画布 · 技术方案

> 状态：设计稿，未实现
> 定位：与现有对话式工作台**并存**，顶栏新增「创意画布」入口，共用同一套模型目录、权限体系与生成执行链路。

---

## 1. 为什么是画布

对话式界面擅长**单条线索的反复打磨**（"把猫换成狗"→"节奏再快一点"），但表达不了分叉与汇聚：一个脚本拆成 5 个镜头、3 张参考图喂给同一个生成、两条视频拼成成片——这些在聊天流里只能靠人脑记账。

画布把这些关系显式化：**节点是想法的原子，连线是数据流，画布是无限的工作台。**

两个入口解决不同问题，都保留：

| | 对话式工作台 | 创意画布 |
| --- | --- | --- |
| 适合 | 快速试一条提示词、连续追问改 | 编排多镜头流程、复用素材 |
| 提示词 | 自动合并上下文（qwen 改写） | **不合并**，节点连什么就提交什么 |
| 产物 | 线性消息流 | DAG，可局部重跑 |

---

## 2. 复用现有代码（这是本方案成立的前提）

画布不需要重写后端，只是给现有能力换个编排方式：

| 现有代码 | 在画布里的角色 |
| --- | --- |
| `catalog.py` 的 `Capability(media_type, min_images, max_images)` | **直接就是节点的输入槽定义**：t2v 无图槽、i2v 一个首帧槽、r2v N 个参考槽 |
| `tasks.py` 的 `run_generation` | 节点执行器，签名不变 |
| `media_resolver.py` | 图片节点连入生成节点时自动选公网 URL 还是 Base64 |
| `messages` 表 + `/api/messages/{id}/download` | 节点产物的存储与下载，完全复用 |
| `prompt_context.py` 的 qwen 通道 | 仅 P2 的分镜节点会用到 |
| `security.py` 的 `can_use` | 节点执行前的模型权限校验 |

---

## 3. 概念模型

```
Canvas ──┬── Node[]   有类型、位置、data、状态、输入/输出槽
         └── Edge[]   source.handle ──▶ target.handle，带类型
```

连线类型只有三种：`text` / `image` / `video`。类型不匹配拒绝连接。

---

## 4. 节点规格

### 4.1 `prompt` 提示词节点

```jsonc
{ "type": "prompt", "data": { "text": "清晨的南方小镇老街……" } }
```

- 输入：无
- 输出：`out` (text)
- 一个提示词节点可以扇出到多个生成节点，这是"同一句话喂给不同模型对比"的用法

### 4.2 `image` 图片节点

```jsonc
{ "type": "image", "data": { "url": "/media/uploads/xxx.jpg", "name": "旗袍女性.jpg" } }
```

- 输入：无
- 输出：`out` (image)
- `url` 存**相对路径**（本机上传）或完整外链，与现有 `media_resolver` 的约定一致

### 4.3 `generate` 生成节点（核心）

```jsonc
{
  "type": "generate",
  "data": {
    "model": "happyhorse-1.1-r2v",
    "capability": "r2v",
    "resolution": "1080P",
    "ratio": "16:9",
    "duration": 5,
    "watermark": true,
    "audio": null,
    "inlinePrompt": ""      // 未连 prompt 边时用节点内联输入
  },
  "status": "succeeded",
  "message_id": "80fd5804…"
}
```

**输入槽由 capability 动态决定**，规则直接读 `catalog.py`：

| capability | 输入槽 |
| --- | --- |
| `t2v` | `prompt` (text) |
| `i2v` | `prompt` (text) + `image_0` (image，必填) |
| `r2v` | `prompt` (text) + `image_0 … image_{max-1}`，前 `min_images` 个必填 |

- 输出：`out` (video)
- 切换 capability 时，超出新槽位数量的入边**自动断开并提示**，不静默丢弃
- 节点自带视频播放器与「下载到本地」，复用现有下载接口

### 4.4 `video` 视频节点

素材节点，放已有视频（上传或外链），P0 可先不做，P2 配合拼接与 v2v 时补。

### 4.5 `storyboard` 分镜节点（P2）

- 输入：`in` (text)
- 输出：`shot_0 … shot_{n-1}` (text)，槽数由 `shotCount` 决定
- 执行：走 `prompt_context.py` 已有的 qwen 通道，把长脚本拆成 N 条独立镜头提示词

### 4.6 `concat` 拼接节点（P2）

- 输入：`in_0 … in_{n-1}` (video)
- 输出：`out` (video)
- 执行：ffmpeg concat demuxer；**需要给镜像装 ffmpeg，体积 +~100MB**

### 4.7 `group` / `note`（P1）

纯视觉，不参与执行。group 框住一组节点整体拖动，对应 TapNow 的"可复用章节"。

---

## 5. 连线协议与校验

```jsonc
{ "id": "e1", "source": "n1", "sourceHandle": "out", "target": "n3", "targetHandle": "image_0" }
```

三条规则，**前后端都要校验**（前端为了体验，后端为了正确性）：

1. `sourceHandle` 的输出类型必须等于 `targetHandle` 的输入类型
2. 一个 `targetHandle` 只能接一条边（多输入靠多个 handle，不靠多重边）
3. 加边时做环检测，不允许成环

---

## 6. 数据模型

新增 3 张表，现有表只加一个可空字段。

```sql
-- 画布
canvases
  id           TEXT PRIMARY KEY
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
  title        TEXT NOT NULL DEFAULT '未命名画布'
  viewport     JSON            -- {x, y, zoom}，下次打开恢复视野
  created_at   DATETIME
  updated_at   DATETIME

-- 节点
canvas_nodes
  id           TEXT PRIMARY KEY      -- 前端生成的 nanoid
  canvas_id    TEXT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE
  type         TEXT NOT NULL         -- prompt|image|generate|video|storyboard|concat|group|note
  position     JSON NOT NULL         -- {x, y}
  size         JSON                  -- {width, height}，可空
  data         JSON NOT NULL         -- 见第 4 节各节点规格
  status       TEXT DEFAULT ''       -- 仅执行类节点：idle|queued|running|succeeded|failed|blocked
  message_id   TEXT REFERENCES messages(id) ON DELETE SET NULL
  input_hash   TEXT DEFAULT ''       -- 增量执行用
  created_at   DATETIME
  updated_at   DATETIME

-- 连线
canvas_edges
  id            TEXT PRIMARY KEY
  canvas_id     TEXT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE
  source        TEXT NOT NULL
  source_handle TEXT NOT NULL
  target        TEXT NOT NULL
  target_handle TEXT NOT NULL
  UNIQUE(canvas_id, target, target_handle)   -- 规则 2 落到数据库约束
```

**节点 id 由前端生成**：React Flow 需要在连线前就有稳定 id，用 nanoid 避免一次往返。

### 6.1 如何复用 `messages` 表

`messages.conversation_id` 是非空外键。为了让画布的产物完全复用现有的视频落盘、下载、重试链路，采用**影子会话**：

- 每个 canvas 创建时同步建一条 `conversations` 记录
- `conversations` 加字段 `kind TEXT DEFAULT 'chat'`，画布的记为 `'canvas'`
- 对话列表接口过滤 `kind = 'chat'`，画布的影子会话不出现在左侧列表
- 于是 `/api/messages/{id}/download`、视频落盘、任务恢复**一行都不用改**

这是本方案里性价比最高的一个决定：用一个字段换掉一整套重复实现。

---

## 7. API

```
GET    /api/canvases                         列表（自己的）
POST   /api/canvases                         新建（同时建影子会话）
GET    /api/canvases/{id}                    取完整图：viewport + nodes + edges
PATCH  /api/canvases/{id}                    改标题 / 视口
DELETE /api/canvases/{id}                    删除（级联节点、边、影子会话）

PUT    /api/canvases/{id}/graph              整图保存（前端防抖 800ms）
GET    /api/canvases/{id}/status             轻量轮询：只回 [{node_id, status, message_id}]

POST   /api/canvases/{id}/nodes/{nid}/run    运行单节点
POST   /api/canvases/{id}/run                运行整图              (P1)
POST   /api/canvases/{id}/cancel             取消未开始的排队节点   (P1)
```

**整图保存而非增量**：画布通常几十个节点，序列化后几十 KB，整图 PUT 简单可靠，不必为省几 KB 引入复杂的 patch 协议。节点数上限 **200**，超过拒绝保存。

**并发覆盖**：`PUT /graph` 带 `updated_at` 做乐观锁，两个标签页同时开同一画布时后提交的收到 409，提示刷新。

---

## 8. 执行器

### 8.1 单节点运行（P0）

```
前端 POST /nodes/{nid}/run
  │
  ├─1  取该节点的所有入边，解析上游值
  │      prompt 边 → 上游 prompt 节点的 data.text
  │      image  边 → 上游 image  节点的 data.url
  │      未连 prompt 边时回退到 data.inlinePrompt
  │
  ├─2  组装成等价于现有 GenerateRequest 的结构
  │
  ├─3  校验：can_use(role, model) + 复用 _validate 的参数校验
  │      必填图槽为空 → 400「image_0 未连接」
  │
  ├─4  在影子会话下建 Message(role=assistant, status=pending)
  │      节点 message_id 指向它
  │
  ├─5  spawn(run_generation)   ← 现有函数，零改动
  │
  └─6  立即返回，前端轮询 /status
```

### 8.2 提示词不做上下文合并

对话式界面会调 qwen 把历史提示词和本轮修改要求合并；**画布模式关闭这个行为**。

理由：画布里提示词是显式的，用户连了哪个 prompt 节点就该提交哪句话。隐式改写会让"为什么出来的和我写的不一样"变得无法排查，也破坏了 `input_hash` 的确定性（同样的输入必须得到同样的请求）。

需要"在上一版基础上改"时，用户复制一个 prompt 节点改几个字即可——这在画布里本来就是一次拖拽。

### 8.3 整图运行（P1）

```
1. 取图 → 建 DAG → 环检测（有环直接 400）
2. 拓扑排序分层
3. 逐节点算 input_hash = sha256(model|capability|params|上游解析后的值)
4. input_hash 未变且 status=succeeded → 跳过（增量执行）
5. 按层执行，同层并发 ≤ CANVAS_MAX_CONCURRENCY（默认 2）
6. 某节点失败 → 其所有下游标记 blocked，其他分支继续跑
7. 运行前先返回预估：将执行 N 个节点、合计 M 秒视频，让用户确认
```

第 3、4 步是**省钱的关键**：改一句提示词只重跑受影响的分支，不是整图重来。

---

## 9. 权限

- 画布归属 `user_id`，**只有本人可见**，管理员也看不到别人的画布（与对话一致）
- 节点执行前再次 `can_use(role, model)`：前端过滤只是体验，后端校验才是边界
- 普通用户的模型下拉只列 HappyHorse 系列
- 画布里若存有该用户无权模型的节点（例如被降级），运行时报 403，但画布本身仍可打开编辑

---

## 10. 前端

- **`@xyflow/react` v12**：无限画布、自定义节点、连线校验（`isValidConnection`）、框选、小地图全部现成
- 自定义节点组件：`PromptNode` / `ImageNode` / `GenerateNode` / `VideoNode`
- `GenerateNode` 内嵌参数表单——把现有 `Composer.tsx` 里的模型/能力/分辨率/比例/时长联动逻辑抽成 `useModelParams` hook，两个入口共用，避免两套规则漂移
- 保存：`onNodesChange` / `onEdgesChange` 触发防抖 800ms 的 `PUT /graph`
- 轮询：存在 `running` 节点时 3s 一次 `GET /status`
- 视觉沿用现有深色 + 蓝紫渐变；画布背景用 dot grid，选中节点用渐变描边

---

## 11. 需要改动的现有文件

| 文件 | 改动 |
| --- | --- |
| `backend/app/models.py` | 新增 3 张表；`Conversation` 加 `kind` 字段 |
| `backend/app/routers/conversations.py` | 列表接口过滤 `kind='chat'` |
| `backend/app/routers/canvas.py` | **新增**，第 7 节全部接口 |
| `backend/app/canvas_executor.py` | **新增**，第 8 节执行器 |
| `backend/app/tasks.py` | 不改 |
| `backend/app/catalog.py` | 不改 |
| `backend/app/media_resolver.py` | 不改 |
| `frontend/src/pages/Canvas.tsx` | **新增** |
| `frontend/src/components/nodes/*` | **新增** |
| `frontend/src/components/Composer.tsx` | 抽出 `useModelParams` hook |
| `frontend/src/App.tsx` | 顶栏加「创意画布」入口 |

---

## 12. 风险与边界

| 风险 | 应对 |
| --- | --- |
| **一键运行 = 一键烧钱** | 运行前显示预估节点数与总秒数并二次确认；`CANVAS_MAX_CONCURRENCY` 默认 2；单次运行节点数上限 |
| 大画布性能 | 节点上限 200；React Flow 开启 `onlyRenderVisibleElements` |
| 多标签页覆盖 | `PUT /graph` 乐观锁，409 提示刷新 |
| 拼接需要 ffmpeg | 推到 P2，届时评估镜像体积 |
| 影子会话泄漏到对话列表 | `kind` 字段过滤，写测试覆盖 |

---

## 13. 里程碑

| 阶段 | 内容 | 交付标准 |
| --- | --- | --- |
| **P0** | 画布 CRUD、prompt/image/generate 三类节点、连线类型校验、单节点运行、整图保存 | 能拖能连能出片，一条 r2v 链路跑通 |
| **P1** | DAG 整图运行、增量重跑、并发控制、group/note、运行前预估 | 一键跑完多镜头流程 |
| **P2** | 分镜节点、video 素材节点、concat 拼接导出、模板库 | 长片工作流闭环 |
| **P3** | 参考视频节点（v2v）、协作 | — |

---

## 附：一条典型链路

```
┌─ prompt ────────────┐
│ 红色旗袍女性，中景  │──── text ──┐
│ 切低角度仰拍…       │            │
└─────────────────────┘            ▼
┌─ image ──┐                ┌─ generate (r2v) ──────┐      ┌─ 视频输出 ─┐
│ 旗袍.jpg │──── image ────▶│ happyhorse-1.1-r2v    │─────▶│  播放器   │
└──────────┘   image_0     │ 1080P · 16:9 · 5s     │      │  下载     │
┌─ image ──┐               │ [Image 1] [Image 2]   │      └───────────┘
│ 折扇.jpg │──── image ────▶│                       │
└──────────┘   image_1     └───────────────────────┘
```

改提示词 → 只有这个 generate 节点变脏 → 重跑一个节点。
换一个模型对比 → 复制 generate 节点，改 model，两条边一拖。
