## Why

ACP v2 代码已实现（`acp-v2-dual-version` change），但 `server.py` 仍硬编码创建 v1 agent。客户端连接时无法根据 `initialize` 请求中的 `protocolVersion` 自动路由到 v1 或 v2 路径。需要一个分发层（DispatchAgent）在 `initialize` 时检查版本，委托给对应的 agent 实现，使同一 server 实例同时服务 v1 和 v2 客户端。

## What Changes

- **新增 `DispatchAgent` 类**：实现 v1 `Agent` 接口，在 `initialize()` 时根据 `protocolVersion` 创建并委托给 `AgentPoolACPAgent`（v1）或 `AgentPoolACPAgentV2`（v2）
- **修改 `server.py`**：用 `DispatchAgent` 替换直接创建 `AgentPoolACPAgent` 的 `functools.partial`
- **DispatchAgent 委托所有方法**：`new_session`、`prompt`、`cancel`、`close_session`、`load_session`、`list_sessions`、`fork_session`、`resume_session`、`authenticate`/`auth_login`、`logout`/`auth_logout`、`set_session_mode`、`set_session_model`、`set_session_config_option`、`ext_method`、`ext_notification`、`close`
- **版本协商降级**：v2 路径在 `SessionPool` 未启用时自动降级到 v1（v2 handler 依赖 SessionPool）
- **`server.py` 移除临时注释**：删除 "v2 路径暂返回 NotImplementedError" 等占位注释

## Capabilities

### New Capabilities

- `acp-dispatch-agent`: DispatchAgent 分发层——initialize 时版本协商、委托给 v1/v2 agent、SessionPool 未启用时降级

### Modified Capabilities

- `acp-version-negotiation`: 从独立工具类升级为 DispatchAgent 内部集成的运行时路由逻辑

## Impact

- **新增代码**：`src/agentpool_server/acp_server/shared/dispatch_agent.py`（约 200 行委托类）
- **修改代码**：`src/agentpool_server/acp_server/server.py`（替换 factory + 移除临时注释）
- **不影响**：v1 agent、v2 agent、event converter、handler、schema 等已实现代码
- **测试**：新增 `tests/servers/acp_server/test_dispatch_agent.py`（版本路由、降级、委托完整性）
