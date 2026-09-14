# Agent v3：对话、计划与事件协议

> 状态：已实现；按 2026-09-14 代码校准。

本文是 Agent v3 的开发接口说明，聚焦一轮对话如何产生计划、前端如何接收流式事件、计划如何进入持久运行。总体产品边界见 [agent-design.md](agent-design.md)，画布并发与人工接手见 [agent-canvas-takeover-plan.md](agent-canvas-takeover-plan.md)。

## 1. 四层对象

| 层 | 数据对象 | 生命周期 |
| --- | --- | --- |
| 会话 | `AgentSession` | 同一用户、surface 和 target 的连续对话 |
| 轮次 | `AgentTurn` | 每次用户输入都会创建，不要求一定产生计划 |
| 对话记录 | `AgentChatMessage`、`AgentStep` | 保存可见消息、计划快照和可回放处理步骤 |
| 执行 | `AgentRun`、`AgentTask`、`AgentRunEvent` | 只有计划被接受或自动放行后创建 |

把“轮次”和“计划”分开很重要：咨询、追问、素材检索和错误诊断都属于有效轮次，但它们不一定有可执行计划。

## 2. 创建轮次

```http
POST /api/agent/turns
Authorization: Bearer <token>
Content-Type: application/json
```

请求示例：

```json
{
  "surface": "canvas",
  "target_id": "canvas-id",
  "user_input": "做三个雨夜汽车广告镜头，总长 15 秒",
  "agent_model_id": "qwen-plus",
  "target_duration": 15,
  "autonomy": "ask",
  "reference_media": [],
  "session_id": null
}
```

字段约束：

- `surface` 支持 `studio` 和 `canvas`；当前 UI 使用 `canvas`。
- `canvas` surface 必须提供归当前用户所有的 `target_id`。
- `session_id` 只有在用户、surface 和 target 都匹配时才复用，否则创建新会话。
- `autonomy` 只能是 `ask` 或 `auto`。
- `user_input` 最长 4000 字符；`target_duration` 最大 600，但每个生成节点仍受模型和全局时长限制。
- 参考媒体在计划校验时再次检查格式、所属用户和模型槽位。

接口先持久化 turn，再用后台协程执行编排，立即返回：

```json
{
  "id": "turn-id",
  "session_id": "session-id",
  "status": "draft"
}
```

客户端随后订阅 turn 事件，而不是等待 POST 返回完整回答。

## 3. 编排流水线

`orchestrate_turn()` 的主流程：

```text
S1 classify     识别 chat/generate/refine/asset/diagnose/unsupported
S2 context      读取会话历史、旧计划和当前画布
S3 tools        按需查询模型、素材、历史、URL 或画布
S4 draft        流式生成正文；结构化计划在围栏内缓冲
S5 validate     用真实用户权限和目录规则验证计划并重新估价
S6 repair       校验失败时有限次请求模型修复
S7 gate         ask 等待确认；auto 检查成本和节点数
S8 execute      原子接受、导入画布、创建运行并启动执行器
```

非生成意图不会强行走计划流程。未配置 Provider 时，规则降级会跳过大模型步骤，生成基础计划或固定说明，但仍写入消息/步骤并发出结束事件。

## 4. 计划传输格式

Provider 使用 OpenAI Chat Completions 兼容协议。自然语言说明可以流式显示；计划 JSON 放在回复末尾：

````markdown
我会把 15 秒拆成三个独立镜头，最后统一拼接。

```plan
{"title":"雨夜汽车广告","nodes":[...]}
```
````

`FenceParser` 会把围栏外内容作为 `text.delta`，把计划块缓存到闭合后一次发出 `plan.draft`，避免 JSON 半截显示到聊天正文。

提取兜底顺序：

1. `plan` 围栏；
2. `json` 围栏；
3. 任意裸代码围栏中的计划对象；
4. 正文中的裸 JSON 对象。

没有提取到计划并不必然是错误：对话式回答或追问会把 turn 完成态设为 `answered`。

## 5. Turn SSE

```http
GET /api/agent/turns/{turn_id}/events?from_seq=0
Accept: text/event-stream
```

主要事件：

| 事件 | 用途 |
| --- | --- |
| `step.start` / `step.end` | 分类、检索、起草、校验和修复步骤 |
| `think.delta` | Provider 提供的思考增量（由配置控制） |
| `text.delta` | 用户可见回复增量 |
| `tool.call` / `tool.result` | 工具调用和安全摘要 |
| `skill.selected` | 选中的提示词技能 |
| `plan.draft` | 完整候选计划及校验/估算信息 |
| `validate.error` | 计划校验失败详情 |
| `gate` | 手动确认、自动放行或自动降级信息 |
| `canvas.changed` | Agent 已修改当前画布，前端应刷新 |
| `run.started` | Auto 模式已创建运行，包含 `run_id` |
| `run.task` | 兼容事件：执行器向 turn 流推送任务变化 |
| `done` | 本轮编排正常结束 |
| `error` | 本轮编排失败 |
| `ping` | 空闲保活 |

事件中的 `seq` 单调递增。重连时传 `from_seq=N`：服务先把数据库中 `seq > N` 的 `AgentStep` 转为 `step.replay`，再接入当前内存队列，并丢弃不大于 `N` 的实时事件。

注意：turn 的处理步骤被持久化，但所有文本 token 的逐块事件不是完整的永久事件日志。刷新后应以会话消息和步骤快照恢复 UI，而不是假设每个 `text.delta` 都能逐字重放。

## 6. 会话恢复

```http
GET /api/agent/sessions/{session_id}/messages
```

返回用户/助手消息、当时的计划快照、对应 turn 状态、估算、警告和 token 计数。`plan_validated=true` 仅表示该消息中的计划仍等于 turn 当前计划，不表示它仍未过期或仍匹配已被人工修改的画布；接受时会再次校验。

```http
GET /api/agent/turns/{turn_id}
```

返回完整 turn 和已保存步骤，适合刷新后恢复单轮处理状态。

## 7. 接受与绘制计划

### 接受并执行

```http
POST /api/agent/turns/{turn_id}/plan/accept
```

服务端在事务中：

1. 校验 turn 归属、状态和到期时间；
2. 重新验证计划并估价；
3. 检查每日预算和画布控制版本；
4. 导入或复用该计划对应的画布节点；
5. 检查已导入输入是否被人工改变；
6. 条件更新 turn 为 `accepted`；
7. 创建唯一 `AgentRun`、任务、输入快照和初始事件；
8. 提交事务后启动运行。

成功响应包含 `run_id`。同一 turn 再次接受返回 409，不会创建第二次运行。

### 只绘制到画布

```http
POST /api/agent/turns/{turn_id}/plan/to-canvas
```

该接口执行同样的归属、版本、计划有效性和幂等检查，但不创建运行，不调用视频供应商。返回计划节点到实际画布节点的映射以及当前画布版本。

## 8. Run API 与持久事件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/agent/runs/{run_id}` | 读取运行及所有任务快照 |
| `GET` | `/api/agent/sessions/{session_id}/runs` | 恢复该会话的运行列表 |
| `GET` | `/api/agent/runs/{run_id}/events?from_seq=N` | 订阅持久运行事件 |
| `POST` | `/api/agent/runs/{run_id}/cancel` | 请求停止后续任务 |
| `POST` | `/api/agent/runs/{run_id}/retry` | 重试失败/取消/阻断分支 |

Run 事件与 turn 事件不同：`AgentRunEvent` 会持久化，支持每个订阅者独立从 `from_seq` 重放。

| kind | 载荷重点 |
| --- | --- |
| `run.status` | `queued/running/succeeded/failed/canceled`、错误、输出文件 |
| `task.status` | task/node/canvas_node、状态、尝试次数、输出与错误 |
| `run.cancel_requested` | 已记录取消请求 |

运行 SSE 也发送 `ping` 保活。前端以 run 流作为执行阶段的权威来源，`run.task` 只是保留给仍跟随 turn 流的兼容客户端。

## 9. 执行语义

- `generate` 任务创建隐藏对话中的 `Message`，复用既有 DashScope 提交/轮询/下载逻辑。
- `compose` 等待其依赖的生成任务成功，再用 FFmpeg 生成最终文件。
- 独立任务并发数由 `AGENT_RUN_CONCURRENCY` 限制。
- 失败节点的下游标记为 `blocked`，无关分支继续运行。
- 重试只重置失败集合和依赖它们的下游；达到 `AGENT_TASK_MAX_ATTEMPTS` 后拒绝继续重试。
- 应用启动时 `resume_runs()` 恢复 queued/running 运行；已成功 message 会复用，不重新提交。
- 取消请求会阻止后续 queued 任务；对已经提交到供应商的异步任务不作远程撤销保证。

## 10. 工具循环

Provider 可以请求以下公开工具：

- `catalog.describe`
- `library.search`
- `history.search`
- `url.fetch`
- `canvas.read`
- `canvas.inspect_node`
- `canvas.apply_patch`

带点名称在发送给兼容 Provider 时转换为双下划线名称，收到调用后再映射回公开名称。每次执行结果作为 tool message 送回模型；用户界面只展示摘要。达到 `AGENT_MAX_TOOL_CALLS` 后停止继续调用并要求模型基于已有信息完成回答。

`url.fetch` 只允许本轮用户文本中出现的 URL，并拒绝非 HTTP(S)、带认证信息、私网/回环解析、危险端口、重定向越界和过大响应。解析后使用固定地址建立连接，降低 DNS 重绑定风险。

## 11. 限流与计量

| 配置 | 作用 |
| --- | --- |
| `AGENT_RATE_PER_MINUTE` | 当前用户 Agent 请求速率 |
| `AGENT_DAILY_TOKEN_LIMIT` | 创建新 turn 前检查用户当日 token 总量 |
| `AGENT_MAX_TOKENS` / `AGENT_CHAT_MAX_TOKENS` | Provider 输出限制 |
| `AGENT_MAX_REPAIR_ATTEMPTS` | 计划修复次数 |
| `AGENT_PLAN_TTL_MINUTES` | 待接受计划期限 |
| `AGENT_MAX_AUTO_COST` | Auto 单计划金额上限 |
| `AGENT_MAX_AUTO_NODES` | Auto 计划节点上限 |
| `AGENT_DAILY_COST_LIMIT` | 用户每日执行计划金额上限 |

token 计数记录在 turn、step 和 chat message。供应商不返回 usage 时，计数可能不完整，不能把它作为财务审计数据。

## 12. 前端组件职责

```text
AgentDrawer
├─ AgentWelcome          空状态示例与能力提示
├─ AgentTranscript       消息、步骤和运行事件的归并展示
│  ├─ ChatBubble
│  ├─ ThinkBlock
│  ├─ StepLine
│  ├─ PlanCard
│  └─ TaskList
└─ 输入区               模型、ask/auto、参考素材、发送/停止
```

`AgentDrawer` 按画布保存 session id，切换画布不会复用另一画布的会话。发送可能写画布的请求前调用画布保存桥接；收到 `canvas.changed` 或运行状态变化后通知画布重新加载。

## 13. 兼容性说明

- 旧 `/api/agent/plans` 接口已经移除，不应继续出现在客户端或文档中。
- `/api/agent/models` 作为模型列表端点保留。
- turn SSE 中的 `run.task` 是过渡兼容通道；新执行状态应订阅 run SSE。
- `surface="studio"` 仍被 schema 接受，但当前产品导航只暴露画布形态。

## 14. 测试覆盖

主要回归位于：

- `backend/tests/test_agent_v3.py`：计划围栏、SSE、工具循环、对话恢复、限额和素材库；
- `backend/tests/test_agent_security.py`：素材归属、重复接受、计划结构和公开模型配置；
- `backend/tests/test_agent_canvas_takeover.py`：计划落图、运行事件、取消/重试、画布版本和人工接手；
- `backend/tests/test_agent_migrations.py`：已有 SQLite 的兼容迁移和唯一约束。
