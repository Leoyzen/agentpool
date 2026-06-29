## Context

AgentPool 当前实现了 ACP v1 协议（`src/acp/` 协议库 + `src/agentpool_server/acp_server/` 服务器集成）。ACP 官方发布了 v2 RFD 系列，包含多项破坏性变更：prompt 生命周期重构、消息整体 upsert、工具调用统一、能力字段清理等。

现有代码已预留 v2 扩展钩子（`event_converter.py` 中的 `# V2_EXTENSION:` 注释），并存在 `acp-notification-optimization` work-around 来缓解 v1 的 replay 性能问题。这些信号表明 v1 协议正在限制功能扩展。

约束：
- v1 客户端（Zed 等）必须继续工作，零影响
- 共享基础设施（SessionManager、MCP、ProviderRouter 等）不能因版本分叉而重复
- Python 3.13+，Pydantic v2，anyio，pydantic-ai 技术栈

## Goals / Non-Goals

**Goals:**
- v1 代码零修改（仅移动文件位置 + 更新 import 路径）
- v2 协议支持作为纯增量开发
- initialize 时版本协商，自动路由到 v1 或 v2 路径
- 共享层（SessionManager、InputProvider、MCPManager 等）两个版本复用
- v2 prompt 立即返回 + state_update 通知
- v2 事件转换器支持整体消息 upsert、统一 tool_call_update、plan_update

**Non-Goals:**
- 不实现 v1→v2 适配层（后续阶段）
- 不修改 `src/acp/` v1 协议库任何代码
- 不实现 v2 的 elicitation、telemetry 等 RFD 中尚未稳定的功能
- 不修改 AgentPool 核心（Agent、MessageNode、Team 等）
- 不做 v2 的 streamable-http/websocket 传输（复用 v1 传输层）

## Decisions

### D1: v2 schema 放独立顶层包 `src/acp_v2/`

**选择**: `src/acp_v2/` 独立包
**替代方案**: `src/acp/v2/` 子包
**理由**: v1 的 `src/acp/` 是独立发布的库（有 `py.typed`、entry points），v2 schema 的类型签名与 v1 完全不兼容（三态补丁字段、统一 capabilities），混在同一包内会导致 `__init__.py` 导出冲突和 import 混乱。独立包让 `from acp_v2.schema import ...` vs `from acp.schema import ...` 一目了然。

### D2: v1 代码移入 `acp_server/v1/` 子目录，逻辑不变

**选择**: `git mv` 三个文件到 `v1/` 子目录
**移动文件**: `acp_agent.py`、`event_converter.py`、`handler.py`
**理由**: 避免顶层 `acp_server/` 下同时存在 v1 和 v2 同名文件。v1 代码逻辑一行不改，仅 `server.py` 的 import 路径从 `from agentpool_server.acp_server.acp_agent import ...` 改为 `from agentpool_server.acp_server.v1.acp_agent import ...`。

### D3: 共享层不提取，原位复用

**选择**: `session_manager.py`、`session.py`、`input_provider.py`、`acp_mcp_manager.py`、`provider_router.py`、`converters.py`、`commands/` 保持原位
**理由**: 这些模块是版本无关的——会话管理、MCP 连接、权限输入等逻辑不依赖协议版本。提取到 `shared/` 子目录会增加不必要的 churn 和 import 路径变更。v1 和 v2 代码直接 import 同一路径即可。
**例外**: 新增 `shared/version_negotiator.py`，因为版本协商是新的横切关注点。

### D4: 版本协商在 `server.py` 入口完成

**选择**: `ACPServer` 在收到 `initialize` 请求时，根据 `protocolVersion` 字段选择创建 `AgentPoolACPAgent`（v1）或 `AgentPoolACPAgentV2`（v2）
**流程**:
```
client connect → wait for initialize → read protocolVersion
  → if 1: create v1 agent (现有代码)
  → if >= 2: create v2 agent
  → store negotiated version for session routing
```
**理由**: ACP 的版本协商就在 initialize 里做，这是最自然的路由点。不需要额外的 transport 层或连接层改动。

### D5: v2 prompt 生命周期用异步状态机

**选择**: v2 `session/prompt` 立即返回 `{}`，agent 执行逻辑在后台异步运行，通过 `state_update` 通知客户端状态
**v1 对比**: v1 的 `session/prompt` 挂起直到 turn 结束，返回 `{ stopReason: "end_turn" }`
**状态机**:
```
idle → (收到 prompt) → running → (turn 结束) → idle (带 stopReason)
                   → (需要用户输入) → requires_action → (用户响应) → running
```
**实现**: v2 handler 不阻塞等待 agent 完成，而是启动后台任务并立即返回。`ACPEventConverterV2` 在 agent 事件流结束时发送 `state_update: idle`。

### D6: 三态补丁字段用 sentinel value

**选择**: 用模块级 `_UNSET` sentinel 对象区分"省略"（`_UNSET`）、"清除"（`None`）和"替换"（具体值）
**替代方案**: Pydantic 的 `model_fields_set` 检查
**理由**: v2 的 patch 字段需要区分三种状态：省略=不变、null=清除、值=替换。Pydantic v2 的 `model_fields_set` 能知道字段是否被显式设置，但无法区分 `field=None` 和 `field` 未提供——两者都会出现在 `model_fields_set` 中（如果用户显式传了 `None`）。sentinel 方案更直观：`field: T | None | UnsetType = _UNSET`。

### D7: v2 事件转换器全新实现，不继承 v1

**选择**: `ACPEventConverterV2` 是独立类，不继承 `ACPEventConverter`
**理由**: v1 和 v2 的通知类型差异太大（v2 有 state_update/user_message/整体消息 upsert，v1 没有；v1 有 tool_call 创建通知，v2 没有）。继承会引入大量 override，不如干净的新类。两者共享的是输入（`RichAgentStreamEvent`），不是输出。

### D8: 适配层后续阶段实现

**选择**: 初始版本不实现 v1↔v2 通知转换
**理由**: 版本协商后客户端走对应路径，不需要跨版本转换。唯一需要适配的场景是 session/load 跨版本回放和多客户端混合版本——这些都是后续优化，不是 MVP。

## Risks / Trade-offs

- **[v1 import 路径变更可能遗漏]** → 全量测试验证，`grep` 确认无残留旧路径
- **[v2 prompt 异步化增加 SessionController 复杂度]** → v2 handler 独立实现，不修改 v1 的 SessionController
- **[三态字段在 Pydantic 序列化时可能丢失 null 语义]** → 自定义 `model_dump` 逻辑，确保 `None` 被序列化为 JSON `null` 而非省略
- **[双版本测试矩阵膨胀]** → v1 测试不动，v2 测试镜像 v1 结构，版本协商测试集中在独立文件
- **[v2 schema 与官方 RFD 不完全同步]** → 跟踪 RFD 修订日期，标注实现基于的 RFD 版本

## Migration Plan

1. **阶段 0（结构准备）**: 创建目录结构，移动 v1 文件，更新 import，跑全量测试确认 v1 不受影响
2. **阶段 1（v2 schema）**: 定义 v2 类型，不集成到服务器
3. **阶段 2（版本协商）**: server.py 集成版本路由，v2 路径暂返回 NotImplementedError
4. **阶段 3（v2 prompt 生命周期）**: 实现 v2 handler + event_converter 核心
5. **阶段 4（v2 通知格式）**: 补充 v2 event_converter 的所有通知类型
6. **阶段 5（v2 能力/初始化）**: v2 initialize 返回新格式
7. **阶段 6（适配层，可选）**: v1↔v2 转换

**回滚策略**: 每个阶段独立提交，可 `git revert` 单个阶段。阶段 0 如果 v1 测试失败，立即回滚移动操作。

## Open Questions

- v2 的 `requires_action` 状态是否需要携带具体等待的权限/elicitation 信息？RFD 中提到"we could explore adding which permission"，当前实现先不带。
- v2 的 `session/new` 是否需要在响应中提供 `available_commands`？RFD 提到"Response can provide available commands"，当前实现复用 v1 的异步通知机制。
