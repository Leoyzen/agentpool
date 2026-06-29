## 1. 目录结构准备（v1 零风险）

- [x] 1.1 创建 `src/agentpool_server/acp_server/v1/__init__.py`
- [x] 1.2 `git mv` `acp_agent.py` → `v1/acp_agent.py`
- [x] 1.3 `git mv` `event_converter.py` → `v1/event_converter.py`
- [x] 1.4 `git mv` `handler.py` → `v1/handler.py`
- [x] 1.5 更新 `server.py` 的 import 路径：`from agentpool_server.acp_server.v1.acp_agent import AgentPoolACPAgent`
- [x] 1.6 更新 `acp_server/__init__.py` 的导出路径
- [x] 1.7 `grep` 确认全代码库无残留旧路径 `from agentpool_server.acp_server.acp_agent import`
- [x] 1.8 跑全量 `uv run pytest -m unit` 确认 v1 测试全部通过
- [x] 1.9 跑 `uv run ruff check src/agentpool_server/acp_server/` 确认无 lint 错误

## 2. v2 协议库骨架 (`src/acp_v2/`)

- [x] 2.1 创建 `src/acp_v2/__init__.py` 包骨架
- [x] 2.2 创建 `src/acp_v2/schema/__init__.py`
- [x] 2.3 定义三态补丁字段 sentinel：`src/acp_v2/schema/_unset.py`（`UnsetType` + `_UNSET`）
- [x] 2.4 在 `pyproject.toml` 中注册 `acp_v2` 包

## 3. v2 Schema — SessionUpdate 类型

- [x] 3.1 `src/acp_v2/schema/session_updates.py`：定义 `UserMessage`、`AgentMessage`、`AgentThought`（整体消息 upsert，required `messageId`，optional `content`/`_meta`）
- [x] 3.2 定义 `UserMessageChunk`、`AgentMessageChunk`、`AgentThoughtChunk`（required `messageId`，单个 `content` 项）
- [x] 3.3 定义 `ToolCallUpdate`（统一 upsert，三态补丁字段，keyed by `toolCallId`）
- [x] 3.4 定义 `ToolCallContentChunk`（required `toolCallId` + 单个 `content` 项）
- [x] 3.5 定义 `StateUpdate`（`state`: running/idle/requires_action，optional `stopReason`）
- [x] 3.6 定义 `PlanUpdate`（`plan={type, id, entries}`，稳定 type="items"）
- [x] 3.7 定义 `UsageUpdate`、`SessionInfoUpdate`、`ConfigOptionUpdate`、`AvailableCommandsUpdate`（复用 v1 结构，适配 v2 命名）
- [x] 3.8 定义 `SessionUpdate` 联合类型（`Annotated[..., Field(discriminator="session_update")]`）
- [x] 3.9 编写 `tests/acp_v2/test_session_updates.py` 验证序列化/反序列化

## 4. v2 Schema — 初始化与能力

- [x] 4.1 `src/acp_v2/schema/capabilities.py`：统一 `Capabilities` 类型（对象标记，session 分组）
- [x] 4.2 `src/acp_v2/schema/client_requests.py`：`InitializeRequest`（`capabilities` + `info`，无 `clientCapabilities`/`clientInfo`）
- [x] 4.3 `src/acp_v2/schema/client_responses.py`：`InitializeResponse`（`capabilities` + `info`，无 `agentCapabilities`/`agentInfo`）
- [x] 4.4 定义 `auth/login` 和 `auth/logout` 的 request/response 类型（`LoginAuthRequest`/`LoginAuthResponse`/`LogoutAuthRequest`/`LogoutAuthResponse`）
- [x] 4.5 定义 v2 `SessionNotification` 和 `CancelNotification`
- [x] 4.6 定义 v2 `messages.py` 方法枚举（`auth/login`、`auth/logout`、`session/prompt` 等）
- [x] 4.7 编写 `tests/acp_v2/test_capabilities.py` 验证对象标记序列化

## 5. v2 Schema — 移除项

- [x] 5.1 v2 schema 中不定义 `session/set_mode` 方法、`SessionMode`/`SessionModeState` 类型、`current_mode_update` 通知
- [x] 5.2 v2 schema 中不定义 `fs/*` 方法、`terminal/*` 方法、terminal ToolCallContent
- [x] 5.3 v2 schema 中不定义 v1 `tool_call`（创建）通知和 v1 `plan` 通知
- [x] 5.4 v2 MCP schema 中移除 SSE 传输，要求 `type` 判别器

## 6. 版本协商器

- [x] 6.1 创建 `src/agentpool_server/acp_server/shared/__init__.py`
- [x] 6.2 实现 `shared/version_negotiator.py`：`VersionNegotiator.negotiate(requested: int) -> Literal[1, 2]`
- [x] 6.3 编写 `tests/servers/acp_server/test_version_negotiation.py`（v1→1, v2→2, v0→error）
- [x] 6.4 修改 `server.py`：在 initialize 时调用 `VersionNegotiator`，路由到 v1 或 v2 agent
- [x] 6.5 v2 路径暂返回 `NotImplementedError`（占位，后续阶段填充）
- [x] 6.6 跑全量测试确认 v1 路径不受影响

## 7. v2 Agent 协议接口

- [x] 7.1 `src/acp_v2/agent/protocol.py`：定义 v2 `Agent` typing.Protocol（方法签名用 v2 类型）
- [x] 7.2 `src/acp_v2/agent/connection.py`：v2 `AgentSideConnection`（JSON-RPC 分发用 v2 方法名）
- [x] 7.3 `src/acp_v2/client/protocol.py`：定义 v2 `Client` typing.Protocol
- [x] 7.4 `src/acp_v2/client/connection.py`：v2 `ClientSideConnection`
- [x] 7.5 复用 `src/acp/transports.py` 传输层（v2 不需要新传输）

## 8. v2 Prompt 生命周期

- [ ] 8.1 创建 `src/agentpool_server/acp_server/v2/__init__.py`
- [ ] 8.2 实现 `v2/prompt_lifecycle.py`：`PromptLifecycleManager` 状态机（idle→running→idle/requires_action）
- [ ] 8.3 实现 `v2/handler.py`：`ACPProtocolHandlerV2` — `session/prompt` 立即返回 `{}`
- [ ] 8.4 handler 在 prompt 接受后启动后台 agent 执行任务（不阻塞请求）
- [ ] 8.5 handler 在 agent 完成后发送 `state_update: idle` + `stopReason`
- [ ] 8.6 handler 发送 `user_message` 通知（带 agent 分配的 `messageId`）
- [ ] 8.7 编写 `tests/servers/acp_server/v2/test_prompt_lifecycle.py`

## 9. v2 事件转换器

- [ ] 9.1 实现 `v2/event_converter.py`：`ACPEventConverterV2` 独立类（不继承 v1）
- [ ] 9.2 转换 `PartDeltaEvent` → `agent_message_chunk`（required `messageId`）
- [ ] 9.3 转换 `ToolCallStartEvent` → `tool_call_update`（统一 upsert）
- [ ] 9.4 转换 `ToolCallProgressEvent` → `tool_call_update`（patch fields only）
- [ ] 9.5 转换 `ToolCallCompleteEvent` → `tool_call_update`（with results）
- [ ] 9.6 转换 `PlanUpdateEvent` → `plan_update`（`plan={type:"items", id:"main", entries}`）
- [ ] 9.7 转换 `StreamCompleteEvent` → `state_update: idle` + `stopReason`
- [ ] 9.8 转换 `RunStartedEvent` / 首个 `PartStartEvent` → `state_update: running`
- [ ] 9.9 支持 `SpawnSessionStart` → out-of-turn `tool_call_update`
- [ ] 9.10 实现 `tool_call_content_chunk` 流式追加支持
- [ ] 9.11 编写 `tests/servers/acp_server/v2/test_event_converter.py`

## 10. v2 ACP Agent 实现

- [ ] 10.1 实现 `v2/acp_agent.py`：`AgentPoolACPAgentV2`（实现 v2 `Agent` protocol）
- [ ] 10.2 `PROTOCOL_VERSION = 2` 类变量
- [ ] 10.3 `initialize()` 返回 v2 格式（统一 `capabilities` + `info`，无 `agentCapabilities`/`agentInfo`）
- [ ] 10.4 `new_session()` 返回 v2 格式（无 `modes` 字段）
- [ ] 10.5 `prompt()` 委托给 `ACPProtocolHandlerV2`（立即返回）
- [ ] 10.6 不实现 `set_session_mode`（v2 移除）
- [ ] 10.7 不实现 `set_session_model`（用 config options 替代）
- [ ] 10.8 实现 `auth_login()` 和 `auth_logout()` 方法
- [ ] 10.9 复用共享 `ACPSessionManager`、`ACPInputProvider`、`AcpMcpConnectionManager`
- [ ] 10.10 编写 `tests/servers/acp_server/v2/test_acp_agent.py`

## 11. v2 集成测试

- [ ] 11.1 创建 `tests/servers/acp_server/v2/__init__.py`
- [ ] 11.2 端到端测试：v2 客户端 initialize → session/new → session/prompt → state_update
- [ ] 11.3 测试 v2 prompt 立即返回 + 异步 state_update
- [ ] 11.4 测试 v2 tool_call_update 统一 upsert 行为
- [ ] 11.5 测试 v2 整体消息 upsert 替换 + chunk 追加交互
- [ ] 11.6 测试 v1+v2 双版本共存（同一 server 实例）
- [ ] 11.7 跑 `uv run pytest tests/servers/acp_server/ -v` 确认双版本测试通过

## 12. Lint 与类型检查

- [ ] 12.1 `uv run ruff check src/acp_v2/ src/agentpool_server/acp_server/v2/ src/agentpool_server/acp_server/shared/`
- [ ] 12.2 `uv run ruff format --check src/acp_v2/ src/agentpool_server/acp_server/v2/`
- [ ] 12.3 `uv run --no-group docs mypy src/acp_v2/ src/agentpool_server/acp_server/v2/`
- [ ] 12.4 修复所有 lint 和类型错误
