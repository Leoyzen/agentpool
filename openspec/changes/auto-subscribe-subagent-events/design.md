## Context

当前 AgentPool 中所有子代理委派机制在切换到 SessionPool 路径时，事件路由存在问题：

- **SessionPool 路径**（`session_pool.process_prompt()`）：事件通过 EventBus 发布，但**没有人将 EventBus 事件转发给前端**
- **Legacy 路径**（`node.run_stream()`）：业务层手动将事件包装为 `SubAgentEvent` 发射给前端，**正常工作**

受影响的委派机制包括：
- `BackgroundTaskProvider` — 后台任务（`_task_async` 使用 SessionPool 路径）
- `DelegationProvider` — 同步委派（当前使用 Legacy 路径，但计划切换到 SessionPool）
- 任何使用 SessionPool 运行子代理的工具或流程

这个设计的核心问题是：**SessionPool 路径下的事件订阅和转发逻辑缺失，事件路由逻辑散落在业务层，而不是由协议层统一处理**。

## Goals / Non-Goals

**Goals:**
- 协议层（ACP/OpenCode）自动处理子代理事件的订阅和转发
- 前端能实时看到子代理的文本增量、工具调用、完成状态
- 简化所有业务层委派代码（BackgroundTaskProvider、DelegationProvider 等）
- 透明处理两种路径：无论使用 SessionPool 还是 Legacy 路径，前端都能收到一致的 SubAgentEvent 事件流

**Non-Goals:**
- 修改 EventBus 的实现
- 修改 SpawnSessionStart 事件结构
- 修改子代理的执行逻辑（run_stream / process_prompt）
- 支持非 SessionPool 场景下的自动订阅（Legacy 路径保持原样）

## Decisions

### Decision 1: 协议层自动订阅 vs 业务层手动处理

**选择**：协议层自动订阅

**理由**：
- 协议层是事件的"最后一公里"，最了解如何向前端推送 SSE
- 业务层不应该关心事件如何到达前端，只应关注业务逻辑（启动任务、处理结果）
- 集中处理避免了多个业务模块重复实现事件转发逻辑

**替代方案**：让各个 Provider 继续手动 emit SubAgentEvent
- 拒绝原因：代码冗余，容易遗漏事件类型，维护成本高，每个 Provider 都要重复实现

### Decision 2: 订阅时机 — SpawnSessionStart vs 显式注册

**选择**：收到 `SpawnSessionStart` 时自动订阅

**理由**：
- `SpawnSessionStart` 是子代理生命周期的起点，天然适合作为订阅触发点
- 不需要修改 BackgroundTaskProvider 的 API（无需额外的注册调用）
- 与现有的事件流集成，无侵入性

**替代方案**：BackgroundTaskProvider 显式调用 `register_subagent_subscription`
- 拒绝原因：增加 API 复杂度，容易遗漏调用

### Decision 3: 订阅范围 — session-scoped vs global

**选择**：session-scoped 订阅（`scope="session"`）

**理由**：
- 只订阅特定子代理 session 的事件，避免性能问题
- SessionPool 的 EventBus 已支持 scoped 订阅
- 子代理 session 结束时自动清理订阅

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 内存泄漏：忘记取消 EventBus 订阅 | 在 `StreamCompleteEvent` 或子代理 session 结束时自动取消订阅 |
| 事件重复：协议层和业务层同时 emit 事件 | 明确区分：SessionPool 路径由协议层处理，Legacy 路径由业务层处理 |
| 性能：大量子代理同时运行时 EventBus 压力 | session-scoped 订阅限制了范围；必要时可添加背压机制 |
| 向前兼容：现有 client 可能不期望新的事件类型 | 新事件通过现有 `SubAgentEvent` 包装，client 无需修改 |

## Migration Plan

1. **Phase 1**：在协议层实现自动订阅和转发（本 change）
2. **Phase 2**：验证前端能正常显示子代理事件（BackgroundTaskProvider + DelegationProvider）
3. **Phase 3**：简化所有业务层 Provider（BackgroundTaskProvider、DelegationProvider 等），移除手动事件处理代码
4. **Phase 4**：全量切换到 SessionPool 路径，废弃 Legacy 路径

## Open Questions

- 是否需要支持子代理嵌套（子代理再创建子代理）的自动订阅？
- 如果子代理异常退出（没有 StreamCompleteEvent），订阅如何清理？
