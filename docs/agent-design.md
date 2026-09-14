# 创作智能体总体架构

> 状态：当前实现说明；按 2026-09-14 代码校准。旧版“待实现”方案已收敛为本文件描述的实现。

## 1. 定位

创作智能体是画布上的规划和编排入口，不是绕过业务规则直接调用模型的超级用户。它可以理解自然语言、查询受控信息、形成视频计划、修改画布并触发既有执行器，但所有写操作和付费动作必须经过服务端的权限、版本、结构、参数与成本校验。

当前前端只在“创意画布”中暴露 Agent 抽屉。后端数据结构仍保留 `surface`/`target_id`，用于把会话与目标表面绑定；画布会话必须绑定当前用户拥有的画布。

## 2. 核心原则

### 计划不是授权

LLM 返回的 JSON 只是一份候选计划。后端会重新校验：

- 用户是否可以使用计划中的模型；
- 模型是否支持所选 T2V/I2V/R2V 能力；
- 分辨率、比例、时长、水印、音频和媒体槽是否合法；
- 本地素材是否属于当前用户；
- 节点依赖是否完整且无环；
- 预估成本、自动节点数和用户每日预算是否允许执行。

Agent 永远不直接调用 DashScope。合法计划接受后才转换为 `AgentRun`/`AgentTask`，由现有任务执行器提交视频生成。

### 运行输入不可变

接受计划时保存计划指纹、画布修订和完整 `input_snapshot`。运行开始后的画布编辑不能改变已提交请求；旧结果也不能覆盖已经变化的新输入。

### 所有画布写入有版本

手工保存、计划导入和 Agent patch 共用 `canvas_service.py`：

- `revision` 保护普通并发编辑；
- `control_version` 表示 Agent 控制周期；
- `idempotency_key` 防止同一批 Agent 操作重复应用；
- 图校验在事务提交前完成；
- patch 保存前后快照，允许在没有后续冲突时撤销。

### 费用动作默认可见

界面提供两种模式：

| 模式 | 行为 |
| --- | --- |
| 手动确认（`ask`） | 生成并展示计划、预计金额和时间；用户决定绘制或执行 |
| 自动执行（`auto`） | 计划合法且在自动金额、节点数和每日预算内时执行；超限降级为手动确认 |

成本按 `AGENT_PRICE_TABLE` 或 `AGENT_PRICE_DEFAULT` 本地估算，只用于闸门和提示。供应商最终账单仍以供应商控制台为准。

## 3. 用户流程

```text
用户消息
   │
   ▼
创建 AgentTurn ── 绑定用户、会话和画布控制版本
   │
   ▼
意图分类 ── chat / generate / refine / asset / diagnose / unsupported
   │
   ├─ 非生成意图：查询受控上下文并流式回答
   │
   └─ 生成/修改意图
          │
          ├─ 加载模型目录、当前画布和按需工具结果
          ├─ 选择提示词技能
          ├─ 流式起草正文和结构化计划
          ├─ 后端校验；最多有限次数修复
          └─ 成本闸门
                 ├─ ask：等待用户
                 └─ auto：接受计划、落图并启动运行
```

手动确认时，用户可以：

- 继续对话，让 Agent 产出一份新的完整计划；
- 仅将计划绘制到当前画布；
- 接受并执行计划；
- 直接人工编辑画布。

## 4. 意图与降级

当前意图分类覆盖：

| 意图 | 典型请求 | 处理 |
| --- | --- | --- |
| `chat` | 能力咨询、创意讨论 | 流式文本回答，不创建运行 |
| `generate` | 创建视频或镜头 | 生成完整计划并校验 |
| `refine` | 修改已有计划或当前画布 | 读取既有上下文，生成新计划或调用画布工具 |
| `asset` | 查找素材 | 只检索当前用户素材库 |
| `diagnose` | 分析失败原因 | 查询当前用户失败记录并回答 |
| `unsupported` | 超出接入模型能力 | 解释限制并提供可行替代，不执行 |

Provider 未配置时：

- `AGENT_FALLBACK_RULES=true`：规则规划器可以创建基础合法计划，并支持一部分明确的画布参数修改。
- `AGENT_FALLBACK_RULES=false`：创建轮次返回 503。

规则降级不是完整自然语言智能体，不应被描述为与 LLM 模式等价。

## 5. 工具边界

当前工具注册表：

| 工具 | 权限与约束 |
| --- | --- |
| `catalog.describe` | 读取服务端模型能力，不返回 API Key |
| `library.search` | 只查询当前用户的素材记录 |
| `history.search` | 只查询当前用户的历史成功生成 |
| `url.fetch` | 只允许用户本轮明确给出的 http(s) URL；拒绝私网、回环和非白名单地址，并进行 DNS 固定 |
| `canvas.read` | 只读取当前绑定且归用户所有的画布 |
| `canvas.inspect_node` | 读取当前画布指定节点及相关连线 |
| `canvas.apply_patch` | 使用 revision、control_version 和幂等键原子修改当前画布 |

`canvas.apply_patch` 支持 `add_node`、`update_node`、`delete_node`、`move_node`、`connect` 和 `disconnect`。工具调用有 `AGENT_MAX_TOOL_CALLS` 上限，Provider 边界会把带点的公开名称转换为兼容的 function 名称。

## 6. 计划结构

计划至少包含标题和节点列表。可执行节点类型为：

- `generate`：一个付费视频生成镜头；保存模型、能力、提示词、参数、参考媒体及依赖。
- `compose`：最终拼接任务；引用一个或多个生成节点并保存顺序/转场。

LLM 在普通回复末尾用 `plan` 围栏返回 JSON。解析器依次兼容 `plan` 围栏、`json` 围栏、裸代码围栏和正文中的裸 JSON。提取不到计划对普通追问是正常情况，不会自动当作系统错误。

后端不信任模型生成的显示文案、成本或 ID：计划会重新计算和规范化；导入画布时使用稳定映射生成实际节点/边 ID，重复导入同一计划不会产生副本。

## 7. 状态模型

### 规划层

`AgentSession` 表示同一用户在同一目标画布上的连续对话；`AgentTurn` 表示一轮用户输入。

常见 `AgentTurn.status`：

| 状态 | 含义 |
| --- | --- |
| `draft` | 本轮有待确认的有效计划 |
| `answered` | 本轮已回答但没有待执行计划 |
| `accepted` | 计划已原子接受并创建运行 |
| `executed` | 对应运行成功完成 |
| 过期（校验结果） | `expires_at` 已过时，接受接口返回 410；当前实现不依赖单独的过期状态迁移 |

`AgentChatMessage` 保存用户/助手可见消息和当时的计划快照；`AgentStep` 保存分类、工具、起草、校验和修复等步骤，供刷新与断线后回放。

### 执行层

`AgentRun` 是一次不可变执行，`AgentTask` 是计划中的生成或合成节点。

```text
AgentSession
  └─ AgentTurn
       ├─ AgentChatMessage
       ├─ AgentStep
       └─ AgentRun (每个 turn 最多一个)
            ├─ AgentTask
            └─ AgentRunEvent
```

运行状态主要为 `queued`、`running`、`succeeded`、`failed`、`canceled`；任务还可能为 `blocked`。同一 turn 的运行有数据库唯一约束，重复接受返回冲突而不会二次提交。

## 8. 画布接管与人工接手

### 计划落图

接受画布计划时，服务端在同一事务内：

1. 重新校验计划、期限、权限和预算；
2. 校验 turn 记录的 `canvas_control_version`；
3. 幂等导入提示词、素材、生成、输出节点及连线；
4. 验证已导入节点没有被人工改成与计划不一致；
5. 创建运行、任务和初始持久事件；
6. 提交后才启动执行器。

“绘制到画布”只执行计划导入，不产生视频费用。

### 人工接手

用户点击“人工接手”后，画布 `control_version` 递增。旧 turn 携带的控制版本随即失效：

- 旧 Agent patch 不能提交；
- 旧计划不能再导入或接受；
- 用户后续手工编辑成为当前事实；
- 已经启动的视频任务仍保留并继续显示进度，可通过运行取消接口阻止后续任务。

详细一致性规则见 [agent-canvas-takeover-plan.md](agent-canvas-takeover-plan.md)。

## 9. 运行可靠性

- 接受计划时一次性持久化运行、任务、输入快照和初始事件。
- `AgentRunEvent` 使用单调递增 `seq`，每个订阅者可以从指定序号重放。
- 应用启动会恢复 `queued`/`running` 的运行。
- 调度器只运行依赖已成功的节点，失败会阻断相应下游而不是无条件停止所有独立分支。
- 重试只重置失败分支和其下游，成功的独立分支继续复用。
- 取消先持久化 `cancel_requested`，并立即取消尚未开始的任务。

当前没有跨进程任务队列。默认设计针对单 Uvicorn 进程；多副本部署必须补充共享调度、事件广播和媒体存储。

## 10. 安全与限额

| 风险 | 当前约束 |
| --- | --- |
| 重复接受导致重复扣费 | turn/run 唯一约束 + 条件状态更新 |
| 越权画布或素材 | 每个 API 和工具按 `user_id` 校验 |
| 计划过期后执行旧参数 | `AGENT_PLAN_TTL_MINUTES` |
| Auto 失控 | 金额、节点数、每日金额、每日 token 限制 |
| 无限工具循环 | `AGENT_MAX_TOOL_CALLS` |
| 无限修复/重试 | `AGENT_MAX_REPAIR_ATTEMPTS`、`AGENT_TASK_MAX_ATTEMPTS` |
| URL SSRF | 用户 URL 白名单、协议/端口/私网检查、DNS 固定、响应限制 |
| 并发覆盖画布 | revision + control_version + 原子事务 |
| 迟到结果污染新输入 | 输入哈希和冻结快照 |

## 11. 配置入口

配置以 `.env.example` 为准，主要分组包括：

- Provider：`AGENT_MODELS_JSON` 或 `AGENT_BASE_URL`/`AGENT_MODEL`/`AGENT_API_KEY`；
- 对话：`AGENT_MAX_HISTORY_TURNS`、`AGENT_CHAT_MAX_TOKENS`、`AGENT_ENABLE_THINKING`；
- 闸门：`AGENT_MAX_AUTO_COST`、`AGENT_MAX_AUTO_NODES`、`AGENT_DAILY_COST_LIMIT`；
- 限流：`AGENT_RATE_PER_MINUTE`、`AGENT_DAILY_TOKEN_LIMIT`、`AGENT_MAX_TOOL_CALLS`；
- 运行：`AGENT_RUN_CONCURRENCY`、`AGENT_TASK_MAX_ATTEMPTS`；
- 降级：`AGENT_FALLBACK_RULES`。

`AGENT_AUTONOMY` 是配置层保留字段；当前前端每次创建 turn 时明确发送 `ask` 或 `auto`，因此用户选择是实际生效值。

## 12. 当前明确不做的事

- Agent 不直接绕过画布/计划校验调用视频供应商。
- URL 工具不浏览任意链接，只读取用户明确提供且安全校验通过的地址。
- 取消不承诺撤销已经被供应商接受的异步生成任务。
- 自动修复有次数上限，不会无限生成直到“满意”。
- 当前只验证技术执行成功，不包含独立的视觉内容质量审核模型。
- 单机事件和任务协调不等于多实例分布式调度。

## 13. 关键实现与测试

- `backend/app/agent_chat.py`：意图、流式对话、工具循环、计划提取与修复
- `backend/app/agent_tools.py`：工具注册表和安全执行
- `backend/app/agent_service.py`：计划生成、校验和费用估算
- `backend/app/agent_run_service.py`：计划接受、持久运行、取消和重试
- `backend/app/agent_executor.py`：依赖调度和执行恢复
- `backend/app/canvas_service.py`：计划导入、patch、undo 和版本控制
- `frontend/src/components/agent/`：抽屉、对话记录、步骤和计划卡片
- `backend/tests/test_agent_v3.py`
- `backend/tests/test_agent_security.py`
- `backend/tests/test_agent_canvas_takeover.py`
