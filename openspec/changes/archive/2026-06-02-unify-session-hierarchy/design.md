## Context

AgentPool 目前存在三层并行的 session 父子关系追踪，这是 SessionPool 架构引入时的迁移残留：

1. **`SessionManager`**（旧架构，`src/agentpool/sessions/manager.py`）：负责数据库持久化（`SessionData` 模型），提供 `create_child_session()` 方法。
2. **`SessionController._children`**（新架构，`src/agentpool/orchestrator/core.py`）：运行时内存中的父子关系，用于级联关闭和过期清理。
3. **`EventBus._session_tree`**（新架构，`src/agentpool/orchestrator/core.py`）：用于事件路由（`descendants`/`subtree` scope），但**从未被写入**，永远是 `{}`。

这导致 `ACPProtocolHandler` 使用 `scope="descendants"` 订阅后，subagent 产生的事件无法被正确路由到父 session，表现为"ACP subagent event 失效"和"inject prompt 失效"。

此外，`AgentContext.create_child_session()` 存在双路径逻辑：优先走 `pool.session_pool.create_session()`（新路径），fallback 到 `pool.sessions.create_child_session()`（旧路径），增加了维护负担。

## Goals / Non-Goals

**Goals:**
- 统一 session 父子关系管理，消除重复状态
- 修复 `EventBus._session_tree` 未同步导致的 subagent 事件丢失
- 让 `SessionPool` 成为 session 生命周期的唯一入口
- 简化 `AgentContext.create_child_session()` 为单一路径

**Non-Goals:**
- 修改 ACP/OpenCode 协议层面的 event 格式或通信机制
- 修改 `BackgroundTaskProvider` 的业务逻辑（只修改它调用的接口）
- 修改 `SessionData` 数据库模型 schema
- 支持 subtree scope 的复杂路由（仅修复 descendants 以覆盖当前使用场景）

## Decisions

### Decision 1: 删除 `SessionManager`，将其持久化职责合并到 `SessionController`

**Rationale**: `SessionManager` 的唯一职责是调用 `SessionStore.save()` 来持久化 `SessionData`。`SessionController` 已经是运行时 session 的权威来源，让它同时负责持久化可以消除一层抽象。

**Alternatives considered**:
- 保留 `SessionManager` 作为 `SessionController` 的委托：增加复杂度，没有额外收益。

### Decision 2: `EventBus` 不再维护 `_session_tree`，而是动态查询 `SessionController`

**Rationale**: `_session_tree` 和 `_children` 分离是 bug 的根因。让 `EventBus` 在运行时查询 `SessionController` 的 `_children`，确保两者永远一致。

**Implementation**: `EventBus` 构造函数接收可选的 `session_controller` 引用。`_is_descendant()` 优先查询 `SessionController`，fallback 到内部 `_session_tree`（兼容测试场景）。

**Alternatives considered**:
- 在 `SessionController` 中同步更新 `EventBus._session_tree`：容易遗漏（如 `_close_session_unlocked` 中的删除操作），仍然有两份数据。

### Decision 3: `AgentPool.sessions` 从 `SessionManager` 改为 `SessionPool | None`

**Rationale**: 当 `session_pool` 启用时，`SessionPool` 是 session 管理的唯一权威。旧 `SessionManager` 不再存在，外部调用者应该直接使用 `session_pool`。

**Migration**: `AgentPool.__init__` 中移除 `self.sessions = SessionManager(...)`。需要 `pool.sessions` 的代码改为 `pool.session_pool`（如 `ACPSessionManager`）。

**Breaking change**: `AgentPool.sessions` 属性消失或类型改变，影响：
- `acp_agent.py` 中的 `ACPSessionManager`
- `BackgroundTaskProvider._on_task_completed`
- `team.py`, `teamrun.py`

### Decision 4: 保留 `SessionController._children` 作为运行时权威数据源

**Rationale**: `_children` 已经在 `get_or_create_session_locked` 和 `close_session` 中正确维护，级联关闭逻辑依赖它。不需要额外重构。

### Decision 5: `SessionPool.create_session()` 继承 parent 的 `project_id` 和 `cwd`

**Rationale**: `SessionManager.create_child_session()` 会加载 parent 的 `SessionData` 并将 `project_id` 和 `cwd` 复制给 child，这是 OpenCode TUI workspace filtering 的关键逻辑。删除 `SessionManager` 后，此逻辑必须移到 `SessionPool.create_session()` 或 `SessionController._get_or_create_session_locked()`。

**Implementation**: 在 `SessionPool.create_session()` 中，当 `parent_session_id` 不为空时，先调用 `pool.session_pool.sessions.store.load(parent_session_id)` 获取 parent 的 `SessionData`，将其 `project_id` 和 `cwd` 传入 `SessionController.get_or_create_session()` 的 `metadata` 参数。

### Decision 6: `SessionController` 接收 `SessionStore` 引用，但不由它管理生命周期

**Rationale**: `SessionStore` 目前在 `AgentPool.__init__` 中创建（通过 `self.manifest.storage.get_session_store()`），在 `AgentPool.__aenter__` 中通过 `exit_stack.enter_async_context(self.sessions)` 进入 async context。`SessionManager` 只是代理了 store 的 `__aenter__`/`__aexit__`。

如果让 `SessionController` 管理 store 生命周期，则 `SessionController` 需要在 `AgentPool.__init__` 中创建（因为 store 在 __init__ 中创建），但 `SessionController` 目前只在 `SessionPool.__init__` 中创建（在 `AgentPool.__aenter__` 中）。

更简单的方案：**保持 `SessionStore` 的创建和生命周期管理在 `AgentPool` 中不变，将 store 引用传给 `SessionController`**。`SessionController` 通过 `store` 参数接收引用，在 `get_or_create_session` 和 `close_session` 中读写 store。`AgentPool.__aenter__` 继续通过 `exit_stack` 管理 store 的 async context。

**Implementation**: 
- `AgentPool.__init__` 继续创建 `session_store = self.manifest.storage.get_session_store()`
- `AgentPool.__init__` 不再创建 `SessionManager`，而是将 `session_store` 保存为 `self._session_store`
- `SessionPool.__init__` 接收 `store` 参数并传给 `SessionController`
- `SessionController.__init__` 接收 `store: SessionStore | None` 参数
- `AgentPool.__aenter__` 中 `exit_stack.enter_async_context(self._session_store)` 管理 store 生命周期
- OpenCode server 通过 `pool.session_pool.sessions.store` 访问 store

### Decision 7: `AgentPool.sessions` 保留为返回 `SessionPool` 的 property alias + `SessionPool` 添加兼容 shim

**Rationale**: 32 个源文件引用 `pool.sessions`。立即删除会造成大量 breaking changes。但 `SessionPool` 没有 `create_child_session()` 方法和 `store` 属性，直接 alias 会导致 `AttributeError`。

**Implementation**: 
1. `AgentPool.sessions` 作为 `@property` 返回 `self.session_pool`
2. `SessionPool` 添加临时兼容方法：
   - `create_child_session()` —— 委托给 `create_session()`
   - `store` property —— 委托给 `self.sessions.store`
3. 所有 caller 迁移完成后（Phase 5-6），在 Phase 7 删除这些 shim

这给出平滑的迁移路径：旧代码继续工作，新代码使用新 API，最后统一清理。

## Risks / Trade-offs

- **[Risk] OpenCode server regression** → `state.py` 和 `session_routes.py` 有 15+ 处直接读写 `pool.sessions.store`。迁移后改为 `pool.session_pool.sessions.store`。**Mitigation**: 在任务列表中明确列出所有 OpenCode server 的修改点，并作为 Phase 1 优先完成。
- **[Risk] `pool.sessions.store` 链式访问变长** → 从 `pool.sessions.store` 变为 `pool.session_pool.sessions.store`。代码稍冗长，但语义清晰。**Mitigation**: 这是临时状态，后续 cleanup PR 可引入 shortcut property。
- **[Risk] 测试中使用 `MagicMock` 模拟 `pool.sessions`** → **Mitigation**: 更新测试 mock，模拟 `pool.session_pool` 或直接模拟 `SessionPool`。
- **[Risk] EventBus / SessionController coupling** → `EventBus` 将持有 `SessionController` 引用。如果 `SessionController` 先于 `EventBus` 被销毁，引用会 dangling。**Mitigation**: 确保 `TurnRunner`（拥有两者）按正确顺序 teardown。`SessionPool.shutdown()` 先关 EventBus 再关 sessions。
- **[Risk] `SessionStore` 和 `StorageManager` 双重持久化** → `SessionController` 通过 `SessionStore` 持久化 session metadata，`StorageManager` 也保存 `SessionData`。OpenCode server 直接读写 `SessionStore`（通过 `pool.session_pool.sessions.store`），同时 `SessionController` 在 session 创建/关闭时也读写同一 store。这意味着同一 record 可能被写两次。**Mitigation**: 两者写的是同一 `SessionData` schema，且 `SessionController` 仅在 session 创建/关闭时写入（lifecycle events），OpenCode server 在 session 更新时写入（metadata changes）。最终数据是一致的。后续如需优化，可在 `SessionController` 中加入 "只写 if not exists" 逻辑。
- **[Trade-off] 移除了无 SessionPool 时的 session 持久化 fallback** → 当 `session_pool` 未启用时，`AgentPool.sessions` 返回 `None`，旧代码通过 `pool.sessions.store` 的访问会失败。这是可接受的，因为当前生产配置都启用了 `session_pool`。对于未启用的情况，代码应检查 `pool.session_pool is not None`。

## Migration Plan

**Phase 1: 核心修复（EventBus + SessionController 连线）**
1. 修改 `EventBus` 查询 `SessionController`
2. 修改 `TurnRunner` 将 `SessionController` 传给 `EventBus`
3. 运行 red flag 测试验证修复（重写断言 bug 行为的测试）

**Phase 2: SessionStore 迁移到 SessionController + 添加 Property Alias**
4. 修改 `AgentPool.__init__`：保留 `session_store` 创建，删除 `SessionManager` 实例化，**同时添加 `sessions` property alias**
5. 修改 `SessionPool.__init__`：接收 `store` 参数并传给 `SessionController`
6. 修改 `SessionController.__init__`：接收 `store` 参数
7. 在 `SessionController._get_or_create_session_locked()` 中持久化 `SessionData`
8. 在 `SessionController._close_session_unlocked()` 中更新/删除 store 记录
9. 在 `SessionPool.create_session()` 中继承 parent 的 `project_id` 和 `cwd`（从 SessionStore 加载 parent）

**Phase 3: 更新 OpenCode Server**
10. 更新 OpenCode server (`state.py`, `session_routes.py`, `server.py`)：将 `pool.sessions.store` 改为 `pool.session_pool.sessions.store`

**Phase 4: 更新 ACP Server 和 Delegation 层**
11. 更新 `ACPSessionManager`：适配新的 store 访问路径，**重写 `create_session()` child path**
12. 更新 delegation 层 (`team.py`, `teamrun.py`)
13. 更新 toolsets (`subagent_tools.py`, `workers.py`)

**Phase 5: 清理 AgentContext 和 SessionPool**
14. 修改 `AgentContext.create_child_session()`：移除 fallback
15. 修改 `SessionPool.create_session()`：移除 legacy 调用

**Phase 6: 删除 SessionManager**
16. 删除 `SessionManager` 类和相关文件

**Phase 7: 审计**
17. 全局搜索确保无遗漏

**Phase 8: 测试与验证**
18. 更新所有测试
19. 运行全量回归测试

## Open Questions

- `StorageManager`（`pool.storage`）和 `SessionStore` 是两个不同的持久化后端。未来是否应该统一？
  - 当前方案：`SessionController` 继续用 `SessionStore` 持久化 session metadata，`StorageManager` 负责 interaction history。不在本 PR 范围内合并。
- `AgentPool.sessions` property alias 应该在什么时候彻底删除？
  - 建议：在本次 PR 中保留并标记 deprecated，在后续 cleanup PR 中删除。
