## Context

`acp-v2-dual-version` change 完成了 v2 协议库（`src/acp_v2/`）和 v2 服务器层（`acp_server/v2/`），但 `server.py` 仍直接创建 v1 agent。ACP 的 `serve()` 函数接受一个 factory，factory 在连接建立时被调用一次，返回的 Agent 实例处理所有后续请求。

版本协商需要看 `initialize` 请求中的 `protocolVersion` 字段——这在 factory 被调用时已知（factory 收到 `AgentSideConnection`，连接建立后才收到 initialize）。因此 DispatchAgent 需要在 `initialize()` 方法内部做版本路由。

约束：
- v1 `Agent` 接口（`acp.agent.protocol.Agent`）是 ACP 库定义的 typing.Protocol
- v2 `Agent` 接口（`acp_v2.agent.protocol.Agent`）方法签名不同（`auth_login` 而非 `authenticate`，无 `set_session_mode`）
- ACP 库的 `_agent_handler` 通过 `hasattr` 检查 agent 上是否存在方法来分发 JSON-RPC 请求
- v2 handler 依赖 `SessionPool`，未启用时 v2 路径不可用

## Goals / Non-Goals

**Goals:**
- 同一 server 实例同时服务 v1 和 v2 客户端
- v1 客户端行为零变化（DispatchAgent 对 v1 完全透明）
- v2 客户端自动走 v2 协议路径
- SessionPool 未启用时 v2 请求降级到 v1
- DispatchAgent 实现简单委托，不引入新逻辑

**Non-Goals:**
- 不实现 v1↔v2 通知转换（适配层是后续 change）
- 不修改 v1 或 v2 agent 的任何实现代码
- 不修改 ACP 库的 `serve()` 或 `AgentSideConnection`
- 不处理多客户端连同一 session 的混合版本场景（需要适配层）

## Decisions

### D1: DispatchAgent 实现 v1 Agent 接口作为外壳

**选择**: DispatchAgent 实现 v1 `Agent` 接口（`acp.Agent`），同时通过 `hasattr` 暴露 v2 方法名（`auth_login`、`auth_logout`）
**理由**: ACP 库的 `_agent_handler` 用 `hasattr(agent, method_name)` 检查方法是否存在。v1 客户端调用 `authenticate`，v2 客户端调用 `auth/login`。DispatchAgent 需要同时响应两套方法名。
**实现**: DispatchAgent 同时定义 `authenticate`（委托给 v1）和 `auth_login`（委托给 v2），`set_session_mode`（v1 委托）等。在 `initialize` 确定版本后，非委托版本的方法返回错误或 no-op。

### D2: 延迟委托——initialize 时创建真正的 agent

**选择**: DispatchAgent 在 `__init__` 时不创建 v1/v2 agent，在 `initialize()` 被调用时根据 `protocolVersion` 创建对应的 agent 并委托
**流程**:
```
factory(connection) → DispatchAgent(connection, default_agent, ...)
  → 等待 initialize 请求
  → initialize(params) 被调用
  → 读取 params.protocol_version
  → VersionNegotiator.negotiate()
  → 创建 AgentPoolACPAgent 或 AgentPoolACPAgentV2
  → 调用 delegate.initialize(params)
  → 返回 delegate 的响应
```
**理由**: factory 被调用时连接刚建立，还没有 initialize 请求。在 initialize 时创建 agent 是最自然的时机，此时已知版本。

### D3: SessionPool 未启用时 v2 降级到 v1

**选择**: 当 `protocolVersion >= 2` 但 `pool.manifest.acp.use_session_pool` 为 False 时，创建 v1 agent 但返回 `protocolVersion=2` 的响应
**理由**: v2 handler 的 `prompt()` 立即返回逻辑依赖 SessionPool 的异步执行能力。没有 SessionPool，v2 的 prompt 生命周期无法工作。降级到 v1 行为但协商为 v2 版本，让客户端知道我们"说 v2 但行为是 v1 兼容的"。
**替代方案**: 直接拒绝 v2 请求。但这对客户端不友好——客户端可能没有 v1 回退能力。

### D4: DispatchAgent 放在 shared/ 目录

**选择**: `src/agentpool_server/acp_server/shared/dispatch_agent.py`
**理由**: DispatchAgent 是版本无关的分发层，不属于 v1 或 v2。与 `version_negotiator.py` 同目录。

## Risks / Trade-offs

- **[委托层增加方法调用开销]** → 每个请求多一层 `getattr(self._delegate, method)` 调用，但 ACP 请求频率低（每秒 < 10 次），开销可忽略
- **[v2 降级时客户端可能困惑]** → v2 客户端收到 `protocolVersion=2` 但实际走 v1 行为（prompt 阻塞返回）。在响应的 `_meta` 中标注 `fallback: true` 让客户端知晓
- **[DispatchAgent 需要覆盖所有方法]** → 遗漏方法会导致 ACP 库的 `_agent_handler` 找不到方法。用 `__getattr__` 动态委托作为兜底
