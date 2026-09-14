# Agent 画布接管与一致性约束

> 状态：核心阶段已实现；按 2026-09-14 代码校准。文件名保留 `-plan` 以兼容已有链接，内容已从实施计划更新为当前行为和验收依据。

## 1. 已交付范围

最初改造的目标是让用户用自然语言搭建和修改画布，并且可以随时转回人工编辑。目前已经具备：

- 画布会话正确绑定目标画布，禁止跨用户和跨画布复用；
- LLM 和规则降级都能把有效计划写入 `AgentTurn.plan`；
- 计划完整、幂等地导入提示词、素材、生成、输出节点和连线；
- 接受计划时原子导入画布、冻结输入、创建运行和任务；
- 任务状态、输出和错误同步到计划、运行及对应画布节点；
- Agent 读取、检查和原子 patch 当前画布；
- Agent 操作可撤销，人工接手使旧控制周期失效；
- 持久运行事件、应用重启恢复、取消后续任务和失败分支重试；
- 单节点重新生成和全画布增量运行；
- 输入哈希阻止旧结果覆盖已经变化的画布。

尚未包含独立的视频内容质量审查或基于视觉模型的自动修正。现有“修复”指结构化计划校验修复和失败任务有限重试，不等同于对成片审美质量的自动验收。

## 2. 三个版本标识

### `Canvas.revision`

画布图的单调递增版本。所有节点/边变更都会推进 revision；并发写入只有一个能成功。

用途：

- 手动图保存的乐观锁；
- Agent patch 的基础版本；
- 运行输入对应的画布版本；
- 撤销前确认没有新的图修改。

### `Canvas.control_version`

Agent 控制周期（epoch）。用户点击“人工接手”时递增，但不等同于图内容版本。

每个 `AgentTurn` 在创建时冻结 `canvas_control_version`。后续计划导入、接受和 patch 必须仍匹配；不匹配表示该轮 Agent 已被用户撤销写权限。

### `plan_version`

对规范化计划计算的稳定指纹。用于：

- 计划导入幂等；
- 追踪运行对应的计划；
- 防止旧消息里的计划快照被当成当前计划；
- 接受前判断画布上的已导入输入是否仍与计划一致。

这三个值解决不同问题，不能互相替代：revision 管图的并发，control_version 管控制权，plan_version 管计划身份。

## 3. 所有写入走共享服务

`backend/app/canvas_service.py` 是画布写入的统一边界：

| 写入来源 | 入口 | 共同约束 |
| --- | --- | --- |
| 用户手工保存 | `persist_canvas_graph()` | 归属、revision、图校验、原子替换 |
| 计划导入 | `import_plan_to_canvas()` | control_version、plan_version、稳定 ID 映射、图校验 |
| Agent 操作 | `apply_agent_patch()` | revision、control_version、幂等键、操作 schema、图校验 |
| Agent 撤销 | `undo_agent_operation()` | 操作归属、未撤销、画布仍是该操作产生的 revision |

任何新写入路径都应复用这些服务，不能直接增删 `CanvasNode`/`CanvasEdge` 后提交，否则会绕过版本和输入失效逻辑。

## 4. 计划导入

### 完整转换

计划导入会把计划节点转换为实际画布节点：

- 为每个生成任务创建相应的提示词/素材/生成节点；
- 按模型能力创建首帧、尾帧或多模态参考连接；
- 把生成依赖转换为生成视频参考连线；
- 为 compose 任务建立输出节点并保存片段顺序/转场；
- 对计划节点 ID、实际节点 ID 和边 ID 建立稳定映射。

导入后使用真实画布校验器验证结果，不接受“计划 schema 合法但画布不可执行”的中间状态。

### 幂等性

`AgentCanvasImport` 以目标画布和计划版本记录导入结果。重复单击“绘制到画布”、网络重试或接受一个已经绘制的计划时，会复用已导入节点，不增加副本。

稳定 ID 还必须带画布和 turn 范围，避免不同画布导入相同计划节点名时发生主键冲突。

### 人工修改后的接受

“先绘制、后编辑、再执行”不能默认继续执行旧计划。接受前，`imported_plan_inputs_match()` 会逐个比较：

- 实际提示词；
- 模型与所有生成参数；
- 素材 URL、类型、顺序及首/尾帧角色；
- 生成依赖；
- 输出片段顺序和转场。

如果输入已改变，旧计划被拒绝，用户应从当前画布直接运行或让 Agent 基于新状态重新规划。纯布局变化不影响接受。

## 5. Agent patch

请求结构：

```json
{
  "base_revision": 8,
  "control_version": 3,
  "idempotency_key": "turn-xxx-operation-1",
  "operations": [
    {"op": "update_node", "node_id": "shot-2", "data": {"duration": 6}},
    {"op": "move_node", "node_id": "shot-2", "position": {"x": 420, "y": 180}}
  ]
}
```

允许的操作：

- `add_node`
- `update_node`
- `delete_node`
- `move_node`
- `connect`
- `disconnect`

执行顺序：

1. 检查画布和用户归属；
2. 用幂等键查找已成功的相同操作；
3. 同时比较 `base_revision` 与 `control_version`；
4. 在内存快照上应用整批操作；
5. 验证完整结果图；
6. 一次事务提交节点、边、新 revision 和 `CanvasOperation` 前后快照；
7. 返回新 revision 与变更摘要。

批次内任何操作失败都会回滚整批，不允许只应用一半。

## 6. 撤销

Agent patch 保存 `before_graph` 和 `after_graph`。撤销默认选择最近一条未撤销操作，也可以指定 operation id。

只有画布当前 revision 仍等于该操作的 `result_revision` 时才允许撤销；如果用户或另一个 Agent 已经继续编辑，撤销会返回冲突，避免把后续工作一起覆盖。

撤销本身也会产生新的 revision，而不是把版本号倒退。

## 7. 人工接手

```http
POST /api/canvases/{canvas_id}/takeover
```

接手操作会：

1. 锁定当前画布；
2. 增加 `control_version`；
3. 返回新的 revision/control_version；
4. 使旧 turn 的后续 Agent 写入、计划导入和计划接受失败。

边界：

- 接手不删除 Agent 已经完成的修改；用户可以保留或手动调整。
- 接手不自动回滚最近 patch；需要撤销时应在图未继续变化前调用 undo。
- 接手不取消已经创建的 `AgentRun`。界面仍跟踪运行，用户可以单独请求 cancel。
- 已经提交到供应商的异步生成可能继续完成；迟到结果只有在输入仍匹配时才回填画布。

## 8. 接受计划的原子边界

`accept_plan()` 将以下操作放在一个数据库事务中：

- 重新校验计划和金额；
- 检查计划 TTL、每日预算和 Auto 上限；
- 条件更新 turn 状态，防止重复接受；
- 校验画布控制版本并导入/复用计划图；
- 冻结资产 URL 和完整执行计划；
- 创建唯一 `AgentRun`；
- 创建每个 `AgentTask` 及依赖关系；
- 写入初始 `run.status` 和 `task.status` 事件。

只有事务提交后才 `spawn_run()`。因此进程在提交前崩溃不会留下半个运行；提交后崩溃则由启动恢复逻辑接管。

同一 turn 通过唯一约束只允许一个 run。重复点击、两个标签页并发或网络重试都不能创建第二份执行。

## 9. 状态同步

执行器更新任务时，同时维护：

- `AgentTask.status/error/output_file/message_id`；
- 对应 `CanvasNode.status/message_id/data`（仍安全匹配时）；
- `AgentRunEvent` 持久事件；
- turn SSE 的兼容 `run.task` 事件。

前端执行阶段订阅 run 事件，并在 `task.status` 或 `run.status` 变化后刷新画布节点状态。刷新页面时先查询会话运行列表和 run 快照，再从最后一个 `seq` 继续订阅。

运行状态不得依赖规划协程的内存队列：规划完成或 SSE 断开不会停止后台生成。

## 10. 运行取消

```http
POST /api/agent/runs/{run_id}/cancel
```

取消请求会持久化 `cancel_requested=true`，立即把 queued 任务设为 canceled，并通知调度器停止启动后续任务。

取消语义是“停止本系统继续提交”，不是“供应商撤单”。已经运行/提交的任务可能仍产生费用和结果，文档及 UI 不应承诺全部远程取消。

## 11. 失败重试

```http
POST /api/agent/runs/{run_id}/retry
```

只有 `failed` 或 `canceled` 的运行可以重试。服务计算失败集合，并递归包含依赖这些节点的下游：

- 失败/取消/阻断节点重置为 queued；
- 成功且不依赖失败分支的任务保持成功；
- 新的视频生成尝试创建新的 message，不覆盖旧供应商尝试；
- 达到 `AGENT_TASK_MAX_ATTEMPTS` 的节点拒绝继续重试；
- run 重新进入 queued 并写入带 `retry=true` 的持久事件。

这保证局部失败不导致所有已成功镜头重新付费。

## 12. 输入失效与迟到结果

`graph_input_hash()` 只覆盖影响节点结果的输入，并刻意排除纯布局数据。

典型规则：

| 变化 | 旧生成结果 | 旧合成结果 |
| --- | --- | --- |
| 移动节点、缩放画布 | 保留 | 保留 |
| 修改提示词/模型/参数 | 失效 | 依赖该片段的输出失效 |
| 改变素材连接或顺序 | 失效 | 依赖该片段的输出失效 |
| 上游生成视频变化 | 下游参考生成失效 | 相应输出失效 |
| 只调整输出顺序/转场 | 镜头保留 | 输出失效，需重新合成 |
| 从输出移除片段 | 镜头保留 | 当前输出失效 |

异步结果回写前再次比较当前输入。布局变化允许回填；影响输入的变化会拒绝迟到结果，防止用户看到与当前配置不一致的视频。

## 13. 恢复模型

应用启动执行两类恢复：

- `tasks.resume_unfinished()`：继续跟踪未完成的供应商视频任务；
- `agent_executor.resume_runs()`：重新调度状态为 queued/running 的 Agent 运行。

执行器会识别已经成功的 message/文件并复用，不应在恢复时盲目重复提交。供应商已接受请求但本地尚未保存 task id 的极短崩溃窗口无法凭当前接口完全证明是否扣费，出现这类异常时应明确报告，而不是声称绝不重复。

## 14. API 索引

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/canvases/{id}/agent/context` | Agent 读取简化画布上下文 |
| `POST` | `/api/canvases/{id}/agent/patch` | 版本化批量修改 |
| `POST` | `/api/canvases/{id}/agent/undo` | 撤销最近/指定 Agent 操作 |
| `POST` | `/api/canvases/{id}/takeover` | 人工接手并推进控制版本 |
| `POST` | `/api/agent/turns/{id}/plan/to-canvas` | 幂等导入计划但不执行 |
| `POST` | `/api/agent/turns/{id}/plan/accept` | 原子接受并创建运行 |
| `GET` | `/api/agent/runs/{id}/events` | 重放/订阅持久运行事件 |
| `POST` | `/api/agent/runs/{id}/cancel` | 停止后续任务 |
| `POST` | `/api/agent/runs/{id}/retry` | 重试失败分支 |

## 15. 回归验收

以下行为由 `backend/tests/test_agent_canvas_takeover.py` 等测试覆盖：

- [x] 人工接手后，在途旧 patch 不能提交。
- [x] 缓存的旧画布不能覆盖并发保存。
- [x] 提示词重连、素材增加/重排会拒绝旧计划。
- [x] 纯布局保存保留结果，输入修改使结果失效。
- [x] 计划完整且幂等地导入画布。
- [x] 规则 Auto 能落图并创建运行。
- [x] 接受计划原子导入并绑定任务到实际画布节点。
- [x] 执行状态和合成结果回填画布。
- [x] patch、undo 和 takeover 都受版本保护。
- [x] 本地资产在执行前解析并冻结，包含尾帧素材。
- [x] 持久运行事件可以被多个订阅者独立重放。
- [x] 取消可在供应商提交前停止所有排队任务。
- [x] 重试只重置失败分支及其下游。
- [x] 不同画布不会复用同一 Agent session。

## 16. 后续扩展约束

若继续增加视觉质量检查或自动修正，应遵循现有边界：

1. 检查结果存为结构化数据，不由检查器直接提交付费生成。
2. 修正必须产生可验证的新计划或受版本保护的 patch。
3. 只重新执行输入受影响的最小分支。
4. 额外生成仍受金额、尝试次数和总时限限制。
5. 用户人工接手始终能使旧自动写操作失效。

若改为多实例部署，还必须引入共享数据库锁语义、分布式任务队列、跨进程事件广播和共享媒体存储；当前内存订阅队列只适用于单应用进程。
