## Why

当前 AgentPool 中所有子代理委派机制（BackgroundTask、Delegation 等）在切换到 SessionPool 路径时，事件路由出现问题。

当使用 **SessionPool 路径**（`session_pool.process_prompt()`）时，事件通过 **EventBus** 发布。但**没有人将 EventBus 事件转发给前端**，导致：

1. **前端看不到子代理的实时进度**：Agent Card 显示"运行中"，但点击进去看不到任何内容（没有文本增量、没有工具调用）
2. **任务状态不同步**：任务实际已完成，但前端卡片仍显示"运行中"，因为从未收到完成事件
3. **结果为空**：后台任务的结果文件未被写入，返回 "No result available"

而当使用 **Legacy 路径**（`node.run_stream()`）时，业务层手动将事件包装为 `SubAgentEvent` 发射给前端，**一切正常**。

问题的根因是：**SessionPool 路径下的事件订阅和转发逻辑缺失**。协议层应该透明地处理两种路径，让业务层无需关心事件如何到达前端。

## What Changes

- **在 ACP/OpenCode 协议层添加自动事件订阅机制**：当协议层收到 `SpawnSessionStart` 事件时，自动从 EventBus 订阅对应的子代理 session 事件
- **统一事件转发**：协议层将订阅到的事件通过 `SubAgentEvent` 包装后推送给前端，让前端能看到完整的子代理事件流
- **简化所有业务层委派代码**：移除 BackgroundTaskProvider、DelegationProvider 等的手动 EventBus 订阅和 SubAgentEvent 发射代码
- **透明处理两种路径**：无论使用 SessionPool 还是 Legacy 路径，前端都能收到一致的 SubAgentEvent 事件流

## Capabilities

### New Capabilities

- `auto-subscribe-subagent-events`: 协议层自动订阅和转发子代理事件。当收到 `SpawnSessionStart` 时，自动从 EventBus 订阅该子代理 session 的所有事件，并通过 SSE 推送给前端。

### Modified Capabilities

- `opencode-event-routing`: 修改事件路由逻辑，在 `SpawnSessionStart` 处理中添加自动 EventBus 订阅和转发逻辑。

## Impact

- **Affected code**:
  - `agentpool_server/opencode_server/routes/` — 消息路由处理，添加 SpawnSessionStart 检测和自动订阅
  - `xeno_agent/agentpool/resource_providers/background_task_provider.py` — 简化 SessionPool 路径的事件处理
  - `xeno_agent/agentpool/resource_providers/delegation_provider.py` — 简化事件发射逻辑
  - `agentpool/orchestrator/core.py` — SessionPool 事件订阅接口
- **APIs**: OpenCode SSE 事件流增加子代理事件自动推送
- **Dependencies**: 依赖 SessionPool 的 EventBus 和现有的 `SubAgentEvent` 事件类型
