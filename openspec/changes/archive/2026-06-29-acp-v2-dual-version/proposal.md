## Why

ACP v1 的 prompt 生命周期限制正在制约 AgentPool 的能力扩展：`session/prompt` 请求必须挂起直到 turn 结束才返回，导致排队消息、多客户端会话、后台子 agent 完成通知等场景无法优雅实现。代码库中已预留 `V2_EXTENSION` 钩子和 `acp-notification-optimization` work-around 作为信号。ACP 官方发布了 v2 RFD 系列解决这些问题，现在需要在**不影响 v1 客户端**的前提下引入 v2 协议支持。

## What Changes

- **新增 v2 协议库** (`src/acp_v2/`)：独立顶层包，定义 v2 schema（统一 `tool_call_update`、整体消息 upsert、`state_update`、`plan_update`、`auth/*` 方法分组、统一 `capabilities` 字段）
- **重构 ACP 服务器为分层结构**：将现有 v1 代码移入 `acp_server/v1/` 子目录（不修改逻辑），新建 `acp_server/v2/` 子目录实现 v2 专属逻辑
- **版本协商器** (`acp_server/shared/version_negotiator.py`)：在 `initialize` 时根据客户端请求的 `protocolVersion` 路由到 v1 或 v2 路径
- **v2 prompt 生命周期**：`session/prompt` 立即返回空响应，agent 异步发送 `user_message` 确认 + `state_update`（running/idle/requires_action）通知
- **v2 事件转换器**：将 `RichAgentStreamEvent` 转换为 v2 通知格式（整体消息 upsert、统一 tool_call_update、tool_call_content_chunk、plan_update 带标记）
- **共享层不动**：`session_manager`、`input_provider`、`acp_mcp_manager`、`provider_router`、`converters`、`commands/` 保持原位，v1 和 v2 共同复用
- **版本适配层** (`src/acp_v2/adapter/`)：v1↔v2 通知转换，用于 session/load 跨版本回放和混合版本多客户端场景（后续阶段）

## Capabilities

### New Capabilities

- `acp-v2-schema`: v2 协议类型定义——session_updates（user_message/agent_message/agent_thought/state_update/tool_call_content_chunk/plan_update）、capabilities（统一字段、对象标记）、client_requests（auth/login、auth/logout）、tool_call（三态补丁字段）
- `acp-v2-prompt-lifecycle`: v2 prompt 生命周期——prompt 立即返回、state_update 状态机（running/idle/requires_action）、user_message 确认通知、out-of-turn 更新
- `acp-v2-event-conversion`: v2 事件转换——RichAgentStreamEvent → v2 SessionUpdate 映射规则，包括整体消息 upsert、统一 tool_call_update、tool_call_content_chunk 流式追加
- `acp-version-negotiation`: 双版本协商——initialize 时版本路由、v1/v2 路径选择、共享层复用策略

### Modified Capabilities

（无——v1 行为完全不变，v2 是纯增量）

## Impact

- **新增代码**：`src/acp_v2/`（v2 协议库）、`src/agentpool_server/acp_server/v2/`（v2 服务器逻辑）、`src/agentpool_server/acp_server/shared/`（版本协商）
- **移动代码**：`acp_agent.py`、`event_converter.py`、`handler.py` 从 `acp_server/` 移入 `acp_server/v1/`（逻辑不变，仅改 import 路径）
- **修改代码**：`acp_server/server.py` 集成版本协商入口；`acp_server/__init__.py` 更新导出
- **不影响**：`session_manager.py`、`session.py`、`input_provider.py`、`acp_mcp_manager.py`、`provider_router.py`、`converters.py`、`commands/`、`src/acp/`（v1 协议库）
- **依赖**：无新外部依赖，复用现有 Pydantic、anyio、pydantic-ai 基础设施
- **测试**：新增 `tests/servers/acp_server/v2/` 目录，现有 v1 测试迁移到 `tests/servers/acp_server/v1/`
