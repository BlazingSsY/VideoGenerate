# 智能体与创意画布问题修复方案

> 状态：待实施
> 范围：智能体大模型接入、创意画布参考素材节点、尾帧输入、视频输出节点样式
> 不包含：转场、光效、音效输入及传统视频特效插件接入（仅在对话中保留设计思路）

---

## 1. 现状审计结论

### 1.1 视频预览性能方案的落地情况

参考 [video-preview-performance.md](./video-preview-performance.md)，当前代码已经完成部分 P0：

- [MessageList.tsx](../frontend/src/components/MessageList.tsx) 已改为主界面显示封面卡片；
- 主界面未打开预览时不再直接挂载成功视频的 `<video>`；
- 预览弹窗中只有一个活动播放器；
- 弹窗播放器使用 `preload="metadata"`；
- [backend/app/routers/media.py](../backend/app/routers/media.py) 已增加显式 Range 响应，包含 `206`、`Content-Range`、`Content-Length` 和 `Accept-Ranges`。

仍未完成的内容：

- 没有生成真实的视频封面文件，当前卡片主要是占位封面；
- 没有提取并持久化视频宽度、时长、大小、编码等元信息；
- 没有生成独立的 H.264/AAC 播放版本；
- 还没有实际运行环境中的 Range、首帧、`waiting`、`stalled` 指标基线；
- 画布节点中的视频素材和生成结果仍会直接渲染 `<video>`，画布场景尚未完全采用单一活动播放器策略。

结论：预览方案属于“P0 已部分落地，P1/P2 未完成”，不能认为整篇方案已经全部实现。

### 1.2 智能体目前没有真正调用 Agent 大模型

当前代码存在智能体 API 和工作台按钮：

- [backend/app/routers/agent.py](../backend/app/routers/agent.py) 提供 `/api/agent/plans` 和计划接受接口；
- [frontend/src/pages/Studio.tsx](../frontend/src/pages/Studio.tsx) 提供“获取创作建议”入口；
- [backend/app/agent_service.py](../backend/app/agent_service.py) 可以创建、估价和校验计划。

但 `agent_service.py` 当前通过关键词、素材数量和模型目录顺序选择能力与模型，属于确定性规划器。文件自身已经说明未来才会增加 LLM adapter。它没有调用 `AGENT_BASE_URL`、`AGENT_MODEL`、`AGENT_API_KEY`、`AGENT_TEMPERATURE` 或 `AGENT_MAX_TOKENS`。

当前上下文改写和 Skill 增强已经改为复用 Agent Provider 的默认模型，但仍使用独立的提示词任务；它们与 Agent 规划共享模型、URL、Key 和调用参数，不再存在第三套模型配置。

### 1.3 Agent 配置与上下文模型的统一

[docs/agent-design.md](./agent-design.md) 已规划以下配置：

```ini
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_MODEL=qwen-plus
AGENT_API_KEY=
AGENT_TEMPERATURE=0.3
AGENT_MAX_TOKENS=2048
AGENT_TIMEOUT_SECONDS=60
AGENT_DAILY_TOKEN_LIMIT=200000
AGENT_MAX_REPAIR_ATTEMPTS=2
AGENT_MAX_HISTORY_TURNS=8
```

当前 Agent 模型连接配置已经进入 `.env` 和 `backend/app/config.py`。上下文记忆不再维护 `CONTEXT_MODEL`、`CONTEXT_BASE_URL` 或 `CONTEXT_API_KEY`，而是统一调用 `AGENT_DEFAULT_MODEL` 对应的 Agent 模型。`CONTEXT_ENABLED` 和 `CONTEXT_MAX_TURNS` 仅控制是否启用以及读取多少轮历史。

### 1.4 画布参考素材数量固定

当前数量由 `catalog.py` 的 `MediaInput.max_count` 决定：

- Wan 参考模式：参考图 10、参考视频 5、参考音频 5；
- MiniMax 参考模式：参考图 9、参考视频 3、参考音频 3；
- HappyHorse 参考模式：参考图 9。

[GenerateNode.tsx](../frontend/src/components/nodes/GenerateNode.tsx) 按 `max_count` 生成固定槽位，[canvas_graph.py](../backend/app/canvas_graph.py) 和 [canvas_executor.py](../backend/app/canvas_executor.py) 也按同一固定上限校验和收集输入。因此现在不能自由增加参考图或参考视频节点。

### 1.5 尾帧能力不存在

当前模型能力只有 `prompt`、参考图片、参考视频、参考音频等输入，没有 `end_frame` 或 `last_frame` 类型。前端 `GenerateNodeData`、后端 `GenerateRequest`、模型目录、画布图校验和供应商 payload 都没有尾帧字段。

因此“要求模型输出尾帧画面”目前有两个未解决问题：

1. 需要确认各模型官方接口是否支持尾帧图片输入，以及各模型的字段名和互斥规则；
2. 需要把尾帧约束同时落到画布连接、后端校验、生成请求和提示词中，不能只在前端显示一个文字开关。

### 1.6 视频输出节点白色背景块

当前 [OutputNode.tsx](../frontend/src/components/nodes/OutputNode.tsx) 使用通用 `NodeShell`。输出区域和空状态区域分别使用：

```css
.canvas-output-port { background: var(--surface-2); }
.canvas-output-empty { background: var(--surface-2); }
```

而 `.canvas-node`、`.react-flow` 和主题覆盖分别使用其它背景色。输出节点左半侧出现白块，最可能是输出内容区域的 `var(--surface-2)` 与节点/画布背景不一致；深色和浅色主题都复现，说明问题在结构化 CSS 变量和组件边界，不是单一主题颜色写错。

---

## 2. 总体目标

完成后应满足：

1. 管理员可以在 `.env` 中配置多个 Agent 大模型，用户只能选择管理员允许使用的模型名称；
2. 视频工作台和自由画布都提供 Agent 配置入口，并使用同一套 Agent Provider 和编排协议；
3. Agent 将长视频需求拆成多个不超过视频模型时长上限的子任务，生成可审阅、可编辑、可执行的任务计划；
4. Agent 只输出受 schema 约束的计划，不能直接执行供应商请求；
5. 参考图和参考视频通过可增删的独立素材节点自由组合；
6. 尾帧作为有明确语义的输入槽，只有支持该能力的模型才可连接；
7. 后端再次验证数量、类型、模型能力和尾帧约束；
8. 视频输出节点的所有区域继承节点主题，不出现与画布背景冲突的白色块；
9. 预览性能方案的剩余 P1 项能够继续实施和验收。

---

## 3. 智能体接入与任务编排方案

### 3.1 产品形态：配置选择 + 计划先行

参考 TapNow 的“自然语言输入、智能体规划、用户确认执行”交互，但结合本项目的视频生成约束，采用以下闭环：

```text
用户输入创作目标
        |
        v
选择 Agent 模型、工作模式和目标时长
        |
        v
Agent 分析目标并拆分镜头/子任务
        |
        v
生成任务计划：节点、依赖、素材、时长、模型和合成方式
        |
        v
用户查看、编辑、接受或拒绝
        |
        v
执行多个视频生成子任务
        |
        v
顺序拼接和最终输出
```

Agent 不是一个替代生成模型的聊天框，而是视频任务的编排器。它负责把“我要一段 45 秒的视频”转成多个可执行的短视频生成任务，并为任务之间的顺序、素材继承和最终合成建立明确关系。

Agent 模型和视频生成模型是两个独立选择：`agent_model_id` 决定由哪个大模型负责规划，计划中每个 `generate` 节点的 `model` 决定由哪个视频模型生成该镜头。前端可以选择 Agent 模型，但不能修改该模型的 URL、Key 或供应商配置。

### 3.2 管理员配置多个 Agent 模型

新增 `agent_provider.py` 或等价模块。管理员在 `.env` 中配置 Agent 模型目录，前端只接收脱敏后的可选模型信息，永远不接收 URL、Key 或完整连接配置。

推荐使用 JSON 配置多个模型，避免不断增加成组环境变量：

```ini
AGENT_ENABLED=true
AGENT_DEFAULT_MODEL=qwen-plus
AGENT_MODELS_JSON=[
  {"id":"qwen-plus","name":"通义千问 Plus","provider":"openai","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","api_key_env":"AGENT_KEY_QWEN_PLUS","supports_json":true},
  {"id":"deepseek-chat","name":"DeepSeek Chat","provider":"openai","base_url":"https://api.deepseek.com/v1","api_key_env":"AGENT_KEY_DEEPSEEK","supports_json":true}
]
AGENT_KEY_QWEN_PLUS=
AGENT_KEY_DEEPSEEK=
```

如果 `.env` 中直接写 JSON 在部署工具里不便维护，也可以使用编号配置，但必须经过同一个配置解析器：

```ini
AGENT_MODEL_IDS=qwen-plus,deepseek-chat
AGENT_MODEL_QWEN_PLUS_NAME=通义千问 Plus
AGENT_MODEL_QWEN_PLUS_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_MODEL_QWEN_PLUS_API_KEY=...
AGENT_MODEL_DEEPSEEK_CHAT_NAME=DeepSeek Chat
AGENT_MODEL_DEEPSEEK_CHAT_BASE_URL=https://api.deepseek.com/v1
AGENT_MODEL_DEEPSEEK_CHAT_API_KEY=...
```

推荐 JSON 目录 + `api_key_env` 方式，因为 Key 可以独立注入，不需要把密钥直接放到模型目录 JSON 中。配置解析后，后端只向前端返回：

```json
[
  {"id":"qwen-plus","name":"通义千问 Plus","supports_json":true},
  {"id":"deepseek-chat","name":"DeepSeek Chat","supports_json":true}
]
```

新增 `GET /api/agent/models`，只允许登录用户读取当前角色可用的 Agent 模型列表。管理员通过 `.env` 控制目录，普通用户不能提交任意 URL、任意模型名或任意 Key。

Provider 层职责包括：

- 读取 `AGENT_PROVIDER`、`AGENT_BASE_URL`、`AGENT_MODEL`、`AGENT_API_KEY`；
- 根据用户选择的模型 ID 解析对应 URL、Key 和模型名；
- 调用 OpenAI Chat Completions 兼容接口；
- 请求中使用结构化 JSON schema 或明确的 JSON 输出模式；
- 设置 temperature、最大 token 和超时；
- 隐藏 API Key，禁止在日志和计划中回显；
- 将网络、HTTP、解析错误转换为可识别的 Agent 异常。

单模型配置变量保留作为兼容 fallback，但新功能优先使用 `AGENT_MODELS_JSON`：

```ini
AGENT_ENABLED=true
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_MODEL=qwen-plus
AGENT_API_KEY=
AGENT_TEMPERATURE=0.3
AGENT_MAX_TOKENS=2048
AGENT_TIMEOUT_SECONDS=60
AGENT_MAX_REPAIR_ATTEMPTS=2
AGENT_MAX_HISTORY_TURNS=8
AGENT_DAILY_TOKEN_LIMIT=200000
```

`AGENT_API_KEY` 留空时可以按设计复用 `DASHSCOPE_API_KEY_WAN`，但应在配置层明确记录实际策略。Agent 的调用地址和视频生成地址是两条概念不同的链路，不能复用 HappyHorse 的视频 Token Plan URL。

每次规划请求应显式提交：

```json
{
  "agent_model_id": "qwen-plus",
  "surface": "studio",
  "user_input": "制作一段 45 秒的产品宣传视频",
  "target_duration": 45,
  "reference_media": []
}
```

后端根据 `agent_model_id` 从配置目录解析真实连接信息，禁止信任请求中的 `base_url`、`api_key`、`provider` 或模型原始名称。

### 3.3 工作台 Agent 配置

在视频工作台底部的 Composer 区增加 Agent 配置条，位于提示词输入框附近但与视频模型参数区分开：

```text
[Agent 开关] [Agent 模型 ▼] [模式：建议 / 确认执行 / 自动] [目标时长：自动 / 15 / 30 / 60 秒]
```

交互规则：

- Agent 关闭时，沿用当前单次视频生成流程；
- Agent 开启时，用户输入的提示词先交给 Agent 规划；
- Agent 模型下拉只显示 `/api/agent/models` 返回的模型名称；
- URL、Key、provider 和模型内部参数不在浏览器中出现；
- 默认模式为“确认执行”，防止生成多个视频时产生不可预期费用；
- 用户点击“让 Agent 规划”后显示计划，不立即提交视频生成；
- 用户可以修改子任务提示词、模型、时长、顺序和合成方式，再点击“接受并执行”；
- 计划执行过程中显示每个子任务状态、预计成本、失败原因和重试入口。

工作台的计划展示建议采用可折叠时间线，而不是把多个子任务重新塞成一条聊天消息：

```text
45 秒产品宣传片 · 4 个镜头 · 预计 4 个视频任务
  1. 开场产品特写       8 秒   已完成
  2. 使用场景展示       10 秒  生成中
  3. 核心卖点动画       10 秒  等待上游
  4. 品牌收束与尾帧     8 秒   等待上游
  合成：顺序拼接
```

### 3.4 画布 Agent 面板与编排

**产品决策：自由画布第一阶段采用 Agent 面板，不增加 `agent` 节点。**

Agent 面板与画布节点解耦，负责生成和审阅画布草案；生成节点、合成节点和输出节点仍然是实际可执行的画布对象。这样可以让 Agent 负责规划，让用户继续掌握画布的编辑权，也避免在第一阶段为 Agent 节点设计额外的输入输出句柄、生命周期和持久化语义。

#### 面板位置与入口

在自由画布右侧增加可折叠的 Agent 面板，桌面端默认停靠在画布右侧，窄屏端使用 Drawer 打开。画布顶部工具栏保留一个 Agent 图标按钮，用于打开或关闭面板；不把 Agent 配置混入普通节点工具栏。

面板顶部提供：

```text
Agent 模型 [下拉]
运行模式 [建议 / 确认执行 / 自动]
目标总时长 [自动 / 15 / 30 / 45 / 60 秒]
```

面板主体提供一个多行提示词输入框，并允许选择当前画布中的素材作为规划上下文。当前选中的图片、视频、音频节点可以作为默认参考素材，也可以在面板中取消引用。

#### 面板交互流程

```text
打开 Agent 面板
        |
        v
选择 Agent 模型、模式和目标总时长
        |
        v
输入创作目标，选择画布素材上下文
        |
        v
点击“规划画布”
        |
        v
显示计划预览，不修改当前画布
        |
        v
用户编辑、接受或放弃计划
        |
        v
接受后批量写入节点和连线
        |
        v
用户继续手动调整，点击画布运行入口执行
```

#### 计划预览

Agent 返回计划后，面板先显示 draft 预览，当前画布保持不变。预览至少包括：

- 计划名称和目标总时长；
- 预计生成节点数量和合成节点数量；
- 每个镜头的提示词、视频模型、能力、分辨率和时长；
- 每个节点的输入素材；
- 节点之间的依赖关系；
- 是否需要尾帧；
- 预计成本和预计等待时间；
- 不满足模型能力、时长或素材约束的警告。

计划预览中的每个 `generate` 节点允许修改提示词、视频模型、时长和素材引用。编辑计划不会直接改变原始 Agent 输出，保存为新的计划版本。

#### 接受计划后的画布写入

用户点击“应用到画布”后，后端或前端将计划转换为普通画布节点：

```text
Agent 计划
  -> prompt 节点
  -> image/video/audio 素材节点
  -> 多个 generate 节点
  -> compose 节点
  -> output 节点
```

写入规则：

- 默认创建一组新的节点，不覆盖用户已有节点；
- 新节点按“素材区 -> 生成区 -> 合成区 -> 输出区”自动布局；
- 计划中的 `depends_on` 转换为对应的画布边；
- 计划中的参考图、参考视频和音频引用转换为素材节点或连接到已有素材节点；
- 应用前再次执行画布图校验，失败时整批回滚，不能只写入半张画布；
- 应用成功后保留 `agent_turn_id` 和计划版本，方便追踪来源和再次规划；
- 应用后的节点完全由用户编辑，Agent 不会在后台静默覆盖节点。

#### 再次规划与版本管理

用户可以基于当前画布再次打开 Agent 面板并规划。再次规划默认读取：

- 当前画布标题；
- 当前节点摘要；
- 当前已连接的素材；
- 用户在面板中输入的新目标。

每次规划都创建新的 draft 版本。已接受或正在执行的版本保持不变；用户可以比较新旧计划后再应用，避免重新规划导致正在运行的任务丢失。

#### 执行边界

Agent 面板只负责规划和批量生成画布草案，不直接执行视频生成任务。接受计划后，用户仍需通过画布的运行入口执行生成节点；任务调度器负责按依赖运行生成和合成节点。

后续如果确实需要在画布内部表达动态规划，再评估增加 `agent` 节点。但这不属于当前第一阶段实现范围。

### 3.5 结构化计划 schema

大模型只负责生成计划，不负责提交供应商任务。为了支持多段视频和画布编排，计划不再只是 `generations[]`，而是包含任务节点和依赖关系：

```json
{
  "title": "45 秒产品宣传片",
  "target_duration": 45,
  "nodes": [
    {
      "id": "shot-1",
      "type": "generate",
      "prompt": "...",
      "model": "happyhorse-1.1-t2v",
      "capability": "t2v",
      "duration": 8,
      "reference_media": [],
      "end_frame": null,
      "depends_on": [],
      "reason": "开场建立产品和环境"
    },
    {
      "id": "shot-2",
      "type": "generate",
      "prompt": "...",
      "model": "happyhorse-1.1-t2v",
      "capability": "t2v",
      "duration": 10,
      "reference_media": [],
      "end_frame": null,
      "depends_on": ["shot-1"],
      "reason": "承接上一镜头展示使用场景"
    },
    {
      "id": "compose-1",
      "type": "compose",
      "operation": "concat",
      "inputs": ["shot-1", "shot-2"],
      "depends_on": ["shot-1", "shot-2"]
    }
  ],
  "output_node": "compose-1",
  "reason": "将长目标拆成两个可生成镜头并顺序合成"
}
```

计划节点类型至少包括：

- `generate`：调用现有视频模型生成一个不超过供应商上限的短片；
- `compose`：拼接多个已完成视频；第一阶段只支持顺序拼接，转场作为后续能力；
- `input`：引用用户上传的图片、视频或音频素材；
- `output`：连接最终视频结果。

Agent 必须遵守以下硬约束：

- 单个 `generate` 节点时长不能超过所选视频模型的最大时长和系统 `MAX_VIDEO_DURATION`；
- 总时长通过多个 `generate` 节点和 `compose` 节点实现；
- 任务 DAG 不能有环；
- `depends_on` 只能引用同一计划中存在的节点；
- 每个子任务必须有明确 prompt、模型、能力、时长和素材引用；
- 目标总时长与各子任务时长允许存在片头片尾误差，但必须在计划中显示；
- 子任务数量、并发数量、总成本和每日成本必须受后端限制。

生成后必须经过：

```text
大模型输出
  -> JSON 解析
  -> DAG 校验
  -> 单节点时长校验
  -> 子任务数量和成本校验
  -> 模型权限校验
  -> 参数和素材校验
  -> 不合法时有限次数修复
  -> 保存 draft 计划
  -> 用户确认
  -> 复用现有生成接口执行
```

不能让 Agent 直接调用 DashScope，也不能让它绕过现有的 `can_use()`、`_validate()`、成本上限和每日额度限制。

### 3.6 长视频拆分与执行

15 秒限制不能通过 Agent 绕过，它只能通过多个短片的编排来间接实现更长的最终视频：

```text
45 秒目标
  -> Agent 拆成 5 个 7-10 秒镜头
  -> 并行或按依赖生成短片
  -> 检查每段视频和音频
  -> 顺序拼接/后期合成
  -> 输出最终视频
```

执行器应增加独立的 `AgentRun` / `AgentTask` 概念，不能把所有子任务伪装成一条 Message：

```text
AgentTurn       一次用户规划请求和计划版本
AgentRun        用户接受后的实际执行批次
AgentTask       一个 generate / compose / output 子任务
Message         兼容现有单视频生成记录的产物记录
```

任务状态建议为：

```text
draft -> accepted -> queued -> running -> succeeded
                                  \-> failed -> retrying
```

执行策略：

- 没有依赖的生成节点可以有限并发；
- 有 `depends_on` 的节点必须等待上游成功；
- `compose` 节点只有所有输入视频成功后才能执行；
- 单个生成节点失败时支持重新生成该节点，不重复执行已成功的节点；
- 合成失败时只重试合成，不重新生成源视频；
- 用户可以取消尚未开始的任务；
- 自动模式也必须受最大节点数、并发数、每日成本和总成本限制；
- 最终输出应保存为独立产物，并可在视频输出节点和工作台结果卡片中预览和下载。

第一阶段的 `compose` 可以使用 FFmpeg concat demuxer 或统一转码后拼接。只有在需要跨编码、跨比例或后续加入转场时，才使用 `filter_complex`。不能在浏览器端把多个完整视频下载后再拼接。

Agent 的系统提示词需要明确要求：

- 先判断用户目标总时长，再决定镜头数量；
- 每个镜头时长必须在所选视频模型支持范围内，且不超过系统上限；
- 每个镜头都要有独立且可执行的画面提示词；
- 相邻镜头需要通过主体、场景、时间或参考素材保持连续性；
- 需要长视频时输出多个 `generate` 节点和至少一个 `compose` 节点；
- 不得声称单个视频模型可以生成超过其上限的片段；
- 不确定的模型能力、尾帧能力或供应商参数留给后端校验，不得自行伪造。

### 3.7 接入工作台和画布

- 工作台底部 Agent 配置调用 Agent Provider，输出可编辑的多节点计划；
- 画布 Agent 面板输出节点、边和素材语义，用户确认后写入画布草案；
- `AgentTurn` 保存原始输入、所选 Agent 模型 ID、模型输出、计划版本、修复次数、token 用量和成本；
- `AgentRun` 和 `AgentTask` 保存用户接受后的执行状态、依赖和产物；
- 用户接受后，单段任务复用现有 `/generate`，合成任务进入新的 compose executor；
- `suggest`、`confirm`、`auto` 三种自主性保持现有成本闸门；
- Agent 失败时给出可读错误，不能静默退化成规则结果，除非显式配置了 fallback。

### 3.8 `.env`、权限与安全边界

将完整 Agent 配置加入 `.env.example`，真实 `.env` 由部署者填写。上下文记忆只保留 `CONTEXT_ENABLED` 和 `CONTEXT_MAX_TURNS`，统一使用 `AGENT_DEFAULT_MODEL`。README 和 `docs/agent-design.md` 中的变量名、默认值和 fallback 行为必须保持一致。

必须增加以下安全边界：

- 浏览器只能提交 `agent_model_id`，不能提交 `base_url` 或 `api_key`；
- 后端通过配置目录重新解析模型和 Key，拒绝未知模型 ID；
- 日志只记录模型 ID、请求耗时、token 数量和错误类型，不记录 Key、完整 prompt 或供应商响应中的敏感字段；
- 不同用户角色可以配置不同的 Agent 模型白名单；
- Agent 生成的模型必须再次经过视频模型 `can_use()` 校验；
- Agent 子任务总数、单次总时长、并发数、估算成本和每日成本都必须后端限制；
- Agent 开启但模型、地址或 Key 无法解析时，启动日志明确提示，并让 API 返回可操作的配置错误。

---

## 4. 画布自由参考素材方案

### 4.1 数据模型

模型能力目录继续描述供应商硬上限，但不再把它直接渲染成固定槽位。建议拆成：

```text
MediaInputSpec
  - kind: image | video | audio
  - media_type: 供应商字段
  - min_count: 最少数量
  - provider_max_count: 供应商硬上限
  - dynamic: 是否允许前端动态添加
```

生成节点 data 保存实际连接槽位：

```json
{
  "media_slots": {
    "image": ["image_0", "image_1"],
    "video": ["video_0"],
    "audio": []
  }
}
```

`media_slots` 是用户当前添加的槽位数量；`provider_max_count` 是后端最终校验上限。这样可以做到界面按需增加，而不是一开始渲染 10 个或 5 个空槽。

### 4.2 前端交互

在 `GenerateNode` 的每个素材类型组中增加：

- 已添加槽位列表；
- “添加参考图”或“添加参考视频”图标按钮；
- 每个槽位的删除按钮；
- 必填数量提示；
- 达到供应商硬上限后禁用添加按钮并用 Tooltip 解释原因。

连接校验根据当前 `media_slots` 判断；新增槽位后调用 `useUpdateNodeInternals` 更新 React Flow Handle；删除槽位时同步删除对应边，并重新编号或保留稳定 id。推荐保留稳定 id，避免删除中间槽位导致已有边错连。

### 4.3 后端校验

`canvas_graph.py` 的输入槽校验需要接受节点 data 中声明的动态槽位，同时校验：

- 槽位类型必须属于当前模型能力；
- 槽位索引不能超过供应商硬上限；
- 每个输入槽最多一条边；
- 必填素材满足 `min_count`；
- 参考图、参考视频和参考音频的总量符合供应商规则；
- 保存图和执行节点使用同一套校验函数。

`canvas_executor.py` 只收集实际连接的动态槽位，并将其转换成现有 `reference_media`。前端动态添加不是安全边界，后端必须再次验证。

### 4.4 兼容旧画布

读取旧数据时，如果没有 `media_slots`，根据现有边和旧的固定槽位推导实际槽位；保存时写入新格式。不要强制迁移并删除旧边，避免历史画布打开后素材连接丢失。

---

## 5. 尾帧功能方案

### 5.1 能力抽象

新增独立能力类型，例如：

```text
end_frame / last_frame
```

它应被建模为图片输入，而不是普通参考图。模型目录需要声明：

```python
MediaInput(
    kind="image",
    media_type="last_frame",
    label="尾帧图",
    min_count=0,
    max_count=1,
    dynamic=False,
)
```

最终字段名不能直接假设为 `last_frame`，必须按每个供应商官方接口适配。对于不支持尾帧的模型，前端不显示该槽，后端拒绝伪造字段。

### 5.2 强制约束

“强制要求大模型输出尾帧画面”应拆成两个明确状态：

1. 用户连接了尾帧图：生成请求必须把尾帧素材传给供应商，提示词必须明确“以该素材作为最后画面”；
2. 用户开启“必须有尾帧”开关但未连接尾帧图：节点不可运行，给出明确错误。

生成节点需要增加：

```text
尾帧图输入槽（最多 1 个）
必须使用尾帧开关
```

后端 `GenerateRequest`、画布执行器、普通工作台校验和 Agent 计划 schema 都要表达该约束。Agent 只能规划支持尾帧的模型，不能自行选择不支持的模型来满足该需求。

### 5.3 与首帧、参考素材的互斥关系

在接入前先按官方文档建立能力矩阵，至少确认：

- 首帧和尾帧能否同时使用；
- 尾帧能否与参考图/参考视频同时使用；
- 尾帧是否只支持图片；
- 尾帧图片格式、尺寸、比例和大小限制；
- Wan、MiniMax H3、HappyHorse 三组模型的具体支持情况。

未确认前，后端应按“能力未声明即不支持”处理，避免把普通参考图误当成尾帧。

---

## 6. 视频输出节点样式修复

### 6.1 推荐修复

把输出节点的区域背景从独立的 `var(--surface-2)` 改为节点上下文色，或直接使用透明背景：

```css
.canvas-output-port,
.canvas-output-empty {
  background: transparent;
}
```

边框继续使用主题变量：

```css
.canvas-output-port,
.canvas-output-empty {
  border-color: var(--border-soft);
}
```

如果需要弱化区分，使用与 `.canvas-node` 同一色系的半透明背景，而不是固定浅色块：

```css
.canvas-output-port,
.canvas-output-empty {
  background: color-mix(in srgb, var(--surface) 70%, transparent);
}
```

考虑浏览器兼容性时，可以使用主题变量 `--canvas-node-inner` 替代 `color-mix`，在浅色和深色主题分别定义。

### 6.2 结构检查

修复时检查：

- `.canvas-node`、`.canvas-node-body`、`.canvas-output-port` 是否存在叠加背景；
- 输出视频是否有默认白色背景或白色容器；
- `Alert`、`Input` 和 Ant Design 默认 token 是否在输出节点左侧形成白色区域；
- 深色和浅色主题是否都覆盖了输出节点；
- React Flow 节点尺寸测量是否因背景块边界造成布局变化。

验收以黑色画布和浅色画布各检查一次，确保输出节点只显示节点自身主题，不出现左半侧独立白块。

---

## 7. 实施顺序与测试

### P0：先恢复正确能力边界

1. 增加 `AGENT_MODELS_JSON`、`AGENT_DEFAULT_MODEL` 和单模型 fallback 配置解析；
2. 增加 `GET /api/agent/models`，在工作台底部和画布面板显示可选 Agent 模型；
3. 实现 Agent Provider、结构化 schema、DAG 校验和验证链；
4. 工作台先支持“确认执行”模式，显示多子任务计划但不自动提交；
5. 为 Agent Provider、模型选择、JSON 解析、非法计划和配置缺失增加测试；
6. 修复输出节点背景，并进行浅色/深色截图验收；
7. 抽取画布输入校验公共函数。

### P0.5：长视频编排闭环

当前进度：执行层已补齐第一版，仍需部署环境联调与画布 compose 节点完整执行联调。

1. [已实现] 增加 `AgentRun` / `AgentTask` 状态模型；
2. [已实现] 支持多个短视频生成任务和依赖关系；
3. [已实现] 增加顺序拼接 compose executor，并在 Docker 镜像安装 FFmpeg；
4. [已实现] 工作台显示任务状态、失败原因和单任务重试入口；
5. [部分实现] 画布可写入 Agent 生成节点，compose 节点还需完成画布侧执行联调；
6. [已实现] 拆分时同时满足视频模型最小时长和最大时长，避免出现 `15+1` 这类非法片段；
7. [待联调] 使用真实 Agent Provider 和真实视频文件验证 30、45、60 秒成片。

### P1：动态参考素材

1. 加入 `media_slots` 数据结构；
2. 实现素材槽位添加、删除和连线同步；
3. 后端接受动态槽位并保留供应商硬上限；
4. 添加旧画布兼容；
5. 测试 0、1、多个参考图和参考视频，以及删除中间槽位后的连线稳定性。

### P2：尾帧

1. 先完成各模型官方能力矩阵；
2. [已实现] Wan 3 与 MiniMax H3 的图生视频增加尾帧图片输入；HappyHorse 仅保留首帧输入；
3. [已实现] 增加按模型能力显示和校验尾帧输入；
4. [已实现] 将尾帧映射为供应商 `last_frame` 字段；
5. [待完善] 增加“必须使用尾帧”开关，以及测试首尾帧冲突和尾帧与参考素材共存场景。

### 回归测试清单

- Agent 使用 `.env` 中配置的地址、模型和 Key，日志不泄露 Key；
- 工作台和画布只能选择后端返回的 Agent 模型 ID；
- Agent URL 和 Key 不会出现在浏览器响应、计划 JSON 或日志中；
- Agent 可以将 30 秒以上目标拆分为多个合法短任务；
- 每个生成子任务均不超过模型和系统时长上限；
- 子任务依赖、失败重试和合成状态可恢复；
- Agent 生成的计划不能绕过模型权限、参数校验和成本闸门；
- Agent 失败不会伪装成成功计划；
- 旧画布可以正常读取和执行；
- 动态参考图/视频槽位能自由添加、删除和保存；
- 供应商硬上限仍由后端强制执行；
- 尾帧只在模型声明支持时出现；
- 必须尾帧但未连接时无法运行；
- 输出节点在深色和浅色主题下均无白色背景块；
- 视频预览 P0 既有行为不回归。

---

## 8. 暂不纳入本方案

转场特效、光效、音效输入、传统视频编辑插件和 FFmpeg 特效流水线暂不写入本修复方案。相关可行性和设计思路单独保留在本次对话中，待模型输入能力和画布素材节点稳定后再评估是否立项。
