## Context

当前 AgentPool 有两个协议服务器需要消费 SessionPool 的 EventBus 事件：

- **OpenCode Server** (`session_pool_integration.py`): 已有完整的事件消费者实现，包括递归子代理订阅和 SpawnSessionStart 处理
- **ACP Server** (`handler.py`): 只有基础的事件消费者，缺少 SpawnSessionStart 检测、递归订阅、统一的错误处理

两边的核心逻辑高度重复：订阅 EventBus → 启动 async loop → 读取 queue → 处理事件 → 清理订阅。但各自内联实现，无法共享改进。

## Goals / Non-Goals

**Goals:**
- 提取事件消费者模式为共享 mixin，消除代码重复
- 让 ACP 补齐缺失的子代理事件自动订阅
- 统一错误处理和订阅清理逻辑
- 设计 mixin 接口兼容未来 OpenCode 采用

**Non-Goals:**
- 修改 OpenCode handler（当前 change 不重构 OpenCode，mixin 接口设计需兼容未来采用）
- 修改 EventBus 的实现
- 修改 SpawnSessionStart 事件结构
- 修改子代理的执行逻辑（run_stream / process_prompt）
- 简化业务层 Provider（BackgroundTaskProvider / DelegationProvider）
- 修改 OpenCode 特有的事件转换逻辑（`OpenCodeEventAdapter`）

## Decisions

### Decision 1: Mixin vs Service / Decorator

**选择**: Mixin (`ProtocolEventConsumerMixin`)

**理由**:
- Mixin 是 Python 中表示 "可共享行为" 的惯用模式
- 协议 handler 已经有自己的继承层次，mixin 是非侵入性的
- 每个 handler 保留自己的 `_handle_event()` 实现（OpenCode 用 `OpenCodeEventAdapter`，ACP 用 `ACPEventConverter`）
- 相比 Service，mixin 减少了实例生命周期管理的复杂度

**替代方案**: 独立的 `EventConsumerService`
- 拒绝原因: 需要额外管理 service 实例生命周期，handler 和 service 之间的状态同步更复杂

### Decision 2: ACP 先采用，OpenCode 后采用

**选择**: 本 change 只让 ACP 采用 mixin，OpenCode 保持原样

**理由**:
- OpenCode 当前实现 ~1143 LOC，重构风险高（子代理 UI 逻辑复杂，包括 ToolPart 生命周期、message 注册时机、child event 过滤）
- ACP 当前实现简单（~371 LOC），缺少的功能是"添加"而非"改变"
- ACP 采用 mixin 可以验证 mixin 接口的合理性，为后续 OpenCode 重构提供信心
- 避免在一个 change 中同时承担"添加 ACP 功能"和"重构 OpenCode"的双重风险

**替代方案**: 两边同时重构
- 拒绝原因: OpenCode 重构风险过高，一旦 regression 难以定位是 mixin 问题还是迁移问题

### Decision 3: 子消费者不由 mixin 自动创建

**选择**: Mixin 提供 `_on_spawn_session_start()` hook，默认 no-op。子类选择是否覆盖以创建子消费者。

**理由**:
- OpenCode 和 ACP 对子消费者的架构不同：
  - OpenCode：创建子消费者，parent 跳过 child events（`is_child_event` 过滤）
  - ACP：不创建子消费者，所有 descendant events 通过 parent converter 处理
- 自动创建子消费者会强加 OpenCode 的架构于 ACP，导致事件重复处理
- No-op 默认保持 ACP 现有行为不变

**替代方案**: Mixin 自动调用 `start_event_consumer(child_session_id)`
- 拒绝原因: 改变 ACP 事件处理架构，引入事件重复风险

### Decision 4: Scope 默认值

**选择**: mixin 默认 `scope="descendants"`

**理由**:
- OpenCode 当前使用 `descendants`（能收到子代理事件）
- ACP 当前也使用 `descendants`
- 保持默认向后兼容
- 子类可覆盖 `_get_subscription_scope()` 返回 `session` 或 `subtree`

### Decision 5: 错误处理边界

**选择**: Mixin 的 `_event_consumer_loop` 不自动 catch `_handle_event()` 的异常。子类在 `_handle_event()` 中自行处理异常。Mixin 仅 catch `ConsumerShutdown`（子类请求优雅关闭的信号）、`asyncio.CancelledError` 和未预料的异常用于清理。

**理由**:
- ACP 的 `_handle_event` 需要区分 `ConnectionResetError`（停止循环）和转换错误（记录日志继续）
- OpenCode 的 `_handle_event` 可能遇到 `anyio.ClosedResourceError`（停止循环）
- 统一 catch 会丢失协议特定的错误恢复逻辑
- 子类可通过抛出特定异常（如 `ConsumerShutdown`）来请求 mixin 停止循环

**替代方案**: Mixin 统一 try/except 包裹 `_handle_event()`
- 拒绝原因: ACP 的连接错误处理会被吞掉，导致循环无法优雅停止

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| Mixin 接口设计不当，未来 OpenCode 无法采用 | 设计时参考 OpenCode 的 5 个阶段需求（setup, spawn, child-filter, first-event, convert），预留 hook |
| ACP 新增递归订阅影响性能 | `scope="descendants"` 已在 OpenCode 验证无问题；ACP 不创建子消费者，性能影响最小 |
| Mixin 接口固化后难以扩展 | 使用 hook 模式（`_handle_event`, `_on_spawn_session_start`, `_before_consumer_loop`, `_after_consumer_loop`），新增 hook 不破坏现有子类 |
| OpenCode 未来重构引入回归 | 保留为后续 change，本 change 不触及 OpenCode |

## Migration Plan

1. **Phase 1**: 创建 `ProtocolEventConsumerMixin` + TDD 测试
2. **Phase 2**: 重构 ACP handler 使用 mixin，修复 SpawnSessionStart，添加 ACP 集成测试
3. **Phase 3**（后续 change）: 评估 mixin 接口是否适合 OpenCode，如适合则重构 OpenCode handler
4. **Phase 4**（后续 change）: 全量测试，验证 OpenCode (603 passed) 和 ACP (179 passed) 的现有测试
5. **Phase 5**（可选）: AG-UI / OpenAI API handler 采用同一 mixin

## Resolved Open Questions

- **Q: Mixin 是否应该提供 `_before_subscribe` / `_after_unsubscribe` hooks？**
  - A: 提供 `_before_consumer_loop(session_id)` 和 `_after_consumer_loop(session_id)` hooks，供协议 handler 做 per-loop 的上下文设置和清理。

- **Q: 是否需要把 `scope` 做成 per-session 可配置？**
  - A: 本 change 中 scope 为 handler 级别固定（通过 `_get_subscription_scope()`）。per-session 配置可在未来通过扩展 `_get_subscription_scope(session_id)` 实现，不破坏现有接口。

- **Q: `_on_spawn_session_start` 异常是否应该被 mixin catch？**
  - A: 不 catch。`ConsumerShutdown` 只在 `_handle_event()` 中被 catch。`_on_spawn_session_start` 中的异常应作为普通异常传播出去，触发 finally 块中的清理。这与 Decision 5 一致。

- **Q: 双 unsubscribe 是否安全？**
  - A: 安全。`stop_event_consumer()` 中调用 `unsubscribe`，`finally` 块中也调用 `unsubscribe`。EventBus.unsubscribe 是幂等的（多次 unsubscribe 同一 queue 无副作用）。

- **Q: ACP 的 `_should_use_session_pool` canary flag 如何处理？**
  - A: 保留 canary flag 逻辑。当 flag 为 False 时，不调用 mixin 方法，保持 ACP handler 的原有行为不变。

## Implementation Deviations

- `_consumer_lock_creation_lock` 最终命名为 `_consumer_lock_creation_lock`（较长但明确），在 `__init__` 中初始化为 `asyncio.Lock()`。
- `event_bus` 被提取为 abstract property，强制子类提供 EventBus 实例，而不是通过 `__init__` 参数传入。
- `ConsumerShutdown` 继承自 `Exception`（不是 `BaseException`），因此不会被裸 `except:` 捕获。

## Child Consumer Ownership

**明确归属**:
- **Mixin 负责**: 启动/停止消费者任务、订阅/取消订阅 EventBus、维护 `_consumer_tasks` 和 `_consumer_queues`
- **子类负责**: 
  - 决定是否创建子消费者（覆盖 `_on_spawn_session_start`）
  - 如果创建子消费者，子类负责跟踪和清理（OpenCode 使用 `child_tasks`，ACP 不创建子消费者）
  - 子类不应直接操作 `_consumer_tasks`，只使用 `start_event_consumer()` / `stop_event_consumer()` API
