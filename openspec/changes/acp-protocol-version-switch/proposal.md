## Why

ACP v2 协议正在快速开发中（上游 `2.0.0-alpha.0`），agentpool 需要在保留 v1 稳定行为的同时支持 v2 协议的实验性测试。当前 `PROTOCOL_VERSION` 硬编码为 1（`AgentPoolACPAgent.PROTOCOL_VERSION: ClassVar = 1`），虽然有 `ACP_PROTOCOL_VERSION` 环境变量和 `BaseACPAgentConfig.protocol_version` 配置项，但这些只是占位符 — ClassVar 未连接到配置系统，没有实际的 v2 代码路径。需要一套完整的版本切换机制，让开发者通过环境变量或 CLI 参数在 v1/v2 之间切换，以便在 v2 规范稳定前进行实验性开发和测试。

## What Changes

- 新增 `--protocol-version` CLI 参数到 `agentpool serve-acp` 命令，接受 `1` 或 `2` 值
- 将 `AgentPoolACPAgent.PROTOCOL_VERSION` 从硬编码 ClassVar 改为从版本解析链获取的实例属性，连接到 `BaseACPAgentConfig.get_protocol_version()`
- 建立统一的版本解析优先级链：CLI 参数 > 环境变量 > 代理配置 > 默认值 (v1)
- 协商后的版本存储在**每连接级别**（`AgentSideConnection` 上），而非 `AgentPoolACPAgent` 实例上（后者可能被多连接共享）
- 在 `AgentPoolACPAgent.initialize()` 中根据协商版本返回不同的能力声明结构（v1 扁平化 vs v2 作用域化）
- 在 `ACPProtocolHandler.handle_prompt()` 中根据版本选择阻塞 (v1) 或立即返回 (v2) 的 prompt 响应路径，统一 v2 路径与现有 `turn_complete` 能力路径
- 在 `_agent_handler()` 中添加 v2 方法路由（`auth/login`, `auth/logout`）
- v2 模式下对已移除的 v1 方法（`session/set_mode`, `session/load`, `authenticate`）记录 deprecation 日志并重定向到 v2 等效方法
- **不重定向 `session/load` 到 `session/resume`** — 两者语义不同（load 回放历史，resume 不回放），v2 中 `session/load` 保留但标记为 deprecated
- 激活 `ACPEventConverter` 中已有的 `V2_EXTENSION` 钩子，v2 模式下发送 `state_update` 通知
- 在会话存储中添加 `protocol_version` 元数据，支持跨版本恢复时的版本检测和警告
- 添加 v2 alpha 版本兼容性常量 `ACP_V2_COMPAT_VERSION`，启动时记录

## Capabilities

### New Capabilities
- `protocol-version-switch`: 通过环境变量和 CLI 参数控制 ACP 协议版本（v1/v2）的切换机制，包括版本解析优先级链、每连接版本存储、initialize 能力协商分支、prompt 生命周期双路径（统一 turn_complete）、deprecation 重定向、会话存储版本元数据

### Modified Capabilities
（无现有 spec 需要修改 — 这是新能力）

## Impact

- **CLI 入口**: `src/agentpool_cli/serve_acp.py` — 新增 `--protocol-version` 参数
- **ACP Server**: `src/agentpool_server/acp_server/acp_agent.py` — `PROTOCOL_VERSION` 从 ClassVar 改为实例属性、连接 config、`initialize()` 版本分支
- **ACP Handler**: `src/agentpool_server/acp_server/handler.py` — `handle_prompt()` v1/v2 双路径，统一 `turn_complete` 逻辑
- **ACP Settings**: `src/acp/settings.py` — 版本解析逻辑完善，添加 `ACP_V2_COMPAT_VERSION` 常量
- **ACP Connection**: `src/acp/agent/connection.py` — `AgentSideConnection` 存储每连接协商版本、`_agent_handler()` 添加 v2 方法路由和 deprecation 日志
- **ACP Schema**: `src/acp/schema/__init__.py` — `PROTOCOL_VERSION` 保留为模块默认值
- **ACP Schema Messages**: `src/acp/schema/messages.py` — `AgentMethod` Literal 添加 `auth/login`, `auth/logout`, 补充缺失的 `session/set_config_option`
- **ACP Event Converter**: `src/agentpool_server/acp_server/event_converter.py` — 激活 V2_EXTENSION 钩子，明确 `state_update` 与 `TurnCompleteUpdate` 的顺序关系
- **会话存储**: `src/agentpool/sessions/models.py` 或 `session_store` — 添加 `protocol_version` 字段到会话元数据
- **测试**: 新增 v2 模式下的协议协商、prompt 生命周期、方法路由、并发 v1/v2 会话、跨版本恢复、deprecation 重定向测试
- **配置**: `BaseACPAgentConfig.protocol_version` 字段已有，需接入实际逻辑
- **关联 Issue**: Leoyzen/agentpool#109 (ACP v1→v2 迁移 tracking)
