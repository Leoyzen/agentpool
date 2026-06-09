## Why

当前 AgentPool 中子代理事件订阅逻辑分散在两个协议服务器中（OpenCode 和 ACP），各自独立实现，代码重复且 ACP 侧功能不完整。

OpenCode 侧（`session_pool_integration.py`）已有基于 `descendants` scope 的递归事件消费者，能正确处理 `SpawnSessionStart` 和嵌套子代理。但实现是内联的，没有共享抽象。

ACP 侧（`handler.py`）只有基础的事件消费者循环，缺少：
1. `SpawnSessionStart` 检测和处理（`event_converter.py` 中是 `...` 占位符）
2. 递归子代理订阅（child session events 不自动转发）
3. 统一的错误处理和订阅清理

问题的根因是：**两边各自实现同样的事件消费者模式，没有共享抽象**。当需要修改事件消费行为（如添加 scope 配置、错误恢复）时，需要在两边重复修改。

## What Changes

### 方案 B：提取 ProtocolEventConsumerMixin + ACP 采用（Phase 1）

本 change 聚焦两个目标：

1. **提取共享 mixin**：创建 `ProtocolEventConsumerMixin`，作为协议服务器事件消费者的基础抽象。包含：
   - 生命周期管理（`start_event_consumer`, `stop_event_consumer`）
   - 事件消费者循环（`_event_consumer_loop`）
   - 可覆盖的 hook：`SpawnSessionStart` 通知（`_on_spawn_session_start`）、循环前后（`_before_consumer_loop`, `_after_consumer_loop`）
   - 订阅清理保证（try/finally）
   - 可配置的订阅 scope（默认 `descendants`，可覆盖为 `session` / `subtree`）

2. **重构 ACP 侧**：
   - `ACPProtocolHandler` 继承 `ProtocolEventConsumerMixin`
   - 实现 `_handle_event()` hook，将事件转换为 ACP `session/update` 通知
   - 实现 `_on_spawn_session_start()` hook，创建 per-child converter
   - 修复 `event_converter.py` 中 `SpawnSessionStart` 的占位符实现
   - ACP 获得递归子代理事件订阅能力

### Phase 2（后续 Change，不在本范围内）

3. **可选：重构 OpenCode 侧**：
   - `OpenCodeSessionPoolIntegration` 继承 `ProtocolEventConsumerMixin`
   - 将现有的 `_event_consumer_loop` 逻辑迁移到 hook
   - 保留 OpenCode 特有的事件转换（`OpenCodeEventAdapter`）和 ToolPart 管理
   - **注意**：OpenCode 当前实现 ~1143 LOC，重构风险高。本 change 不触及 OpenCode，但 mixin 接口设计需兼容未来 OpenCode 采用。

## Implementation Status

**Phase 1 已完成**:
- `ProtocolEventConsumerMixin` 已创建并通过 TDD 测试（12 个 mixin 单元测试通过）
- `ACPProtocolHandler` 已重构为使用 mixin，获得递归子代理事件订阅能力
- `ACPEventConverter` 的 `SpawnSessionStart` 占位符已修复，新增 `_child_sessions` 字段用于子会话跟踪
- ACP 集成测试通过（8 个 subagent 事件集成测试通过）
- 所有现有 ACP 测试保持通过（179+）
- `ruff` 和 `mypy` 检查通过

## Future Work

- AG-UI handler 和 OpenAI API handler 也可采用同一 mixin
- BackgroundTaskProvider / DelegationProvider 简化（依赖 parent repo）

## Capabilities

### New Capabilities

- `auto-subscribe-subagent-events`: 协议层自动订阅和转发子代理事件。提取为共享 `ProtocolEventConsumerMixin`，ACP handler 首先采用。

### Modified Capabilities

- `acp-event-routing`: ACP handler 采用 `ProtocolEventConsumerMixin`，补齐递归子代理订阅和 SpawnSessionStart 处理。

## Impact

- **Affected code**:
  - `src/agentpool_server/mixins.py` — 新增 `ProtocolEventConsumerMixin`
  - `src/agentpool_server/acp_server/handler.py` — 重构为使用 mixin
  - `src/agentpool_server/acp_server/event_converter.py` — 修复 `SpawnSessionStart` 处理
- **未触及代码**:
  - `src/agentpool_server/opencode_server/session_pool_integration.py` — 保持原样（未来可选重构）
- **APIs**: ACP `session/update` 增加子代理事件自动推送
- **Dependencies**: 依赖 SessionPool 的 EventBus 和现有的 `SpawnSessionStart` 事件类型
- **Breaking**: 无（ACP 添加新功能，协议层行为增强而非改变）
