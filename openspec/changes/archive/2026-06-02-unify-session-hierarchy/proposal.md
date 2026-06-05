## Why

SessionPool 引入后，系统中存在三层并行的父子关系追踪：`SessionManager`（旧架构，负责数据库持久化）、`SessionController._children`（新架构，运行时生命周期）、`EventBus._session_tree`（新架构，事件路由）。`EventBus._session_tree` 从未被写入，导致 `scope="descendants"` 订阅永远收不到 subagent 事件，这是 ACP subagent event 和 inject prompt "失效" 的根因。需要统一 session 层级管理，消除重复状态。

## What Changes

- **消除 `SessionManager` 类**：将其持久化职责合并到 `SessionController`，`SessionPool` 成为 session 生命周期的唯一入口。
- **`SessionController` 直接持有 `SessionStore`**：`get_or_create_session` 时同时更新内存 `_children` 和持久化存储。
- **`EventBus` 不再维护独立的 `_session_tree`**：`_is_descendant()` 动态查询 `SessionController._children`，确保事件路由与生命周期管理看到同一棵树。
- **清理 `AgentContext.create_child_session()` 的双路径逻辑**：移除对旧 `SessionManager` 的 fallback 调用，只走 `SessionPool.create_session()`。
- **移除 `SessionPool.create_session()` 中对 `pool.sessions.create_child_session()` 的 legacy 调用**。
- **`SessionPool.create_session()` 继承 parent 的 `project_id` 和 `cwd`**：删除 `SessionManager` 后，workspace context 继承逻辑移到 `SessionPool`。
- **更新 `AgentPool` 初始化逻辑**：移除 `self.sessions = SessionManager(...)`，改由 `SessionPool` 接管全部 session 管理；保留 `AgentPool.sessions` 为 property alias 减少 breaking change。
- **迁移 OpenCode server 对 `pool.sessions.store` 的依赖**：`state.py`、`session_routes.py`、`server.py` 中的 `pool.sessions.store` 调用改为 `pool.session_pool.sessions.store`（保持 `SessionStore` 访问路径，不改为 `pool.storage`，因为 `StorageManager` 缺少 `list_sessions(parent_id=...)` API）。
- **BREAKING**: `AgentPool.sessions` 的类型从 `SessionManager` 变为 property alias，外部调用者需要逐步迁移到 `pool.session_pool`。

## Capabilities

### New Capabilities
- `unified-session-hierarchy`: 统一 session 父子关系管理，消除重复状态，确保 EventBus  descendants/subtree scope 正确路由事件。

### Modified Capabilities
- （无现有 spec 需要修改）

## Impact

- `src/agentpool/orchestrator/core.py`：重构 `EventBus` 和 `SessionController` 的关系。
- `src/agentpool/sessions/manager.py`：删除 `SessionManager` 类。
- `src/agentpool/agents/context.py`：简化 `create_child_session()` 逻辑。
- `src/agentpool/delegation/pool.py`：移除 `self.sessions = SessionManager(...)`，调整 `session_pool` 初始化，添加 `sessions` property alias。
- `src/agentpool_server/acp_server/session_manager.py`：移除对 `pool.sessions.create_child_session()` 的调用。
- `src/agentpool_server/opencode_server/state.py`、`session_routes.py`、`server.py`：将 `pool.sessions.store` 调用迁移到 `pool.session_pool.sessions.store`。
- `src/agentpool/delegation/team.py`、`teamrun.py`：适配新的 session 创建接口。
- `src/agentpool_toolsets/builtin/subagent_tools.py`、`workers.py`：适配新的 session 创建接口。
- 所有使用 `pool.sessions` 和 `SessionManager` 的测试用例需要更新（预计 78+ 个文件引用）。
