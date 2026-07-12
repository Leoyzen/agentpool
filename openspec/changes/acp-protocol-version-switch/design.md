## Context

agentpool 的 ACP 实现是纯手写 Python（~60 个 Pydantic 模型，无外部 Rust crate 依赖）。当前 `PROTOCOL_VERSION` 硬编码为 1（`AgentPoolACPAgent.PROTOCOL_VERSION: ClassVar = 1`），未连接到已有的 `BaseACPAgentConfig.get_protocol_version()` 配置方法。已有部分 v2 基础设施但均为占位符：

- `acp/settings.py` 中有 `ProtocolVersion` 枚举 (V1=1, V2=2) 和 `ACP_PROTOCOL_VERSION` 环境变量（进程级全局单例）
- `BaseACPAgentConfig.protocol_version: Literal[1, 2] | None` 每代理可覆盖
- `TurnCompleteUpdate` 类型已定义并在 `client_supports_turn_complete=True` 时使用（`handler.py:494` 已有非阻塞路径）
- `ACPEventConverter` 有 `V2_EXTENSION` 空操作钩子 (`_on_state_change`, `_on_out_of_turn_update`)
- `session/set_config_option` 已实现（v2 中替代 `session/set_mode`）但**不在 `AgentMethod` Literal 类型中**

关键发现（来自 Oracle 审查）：
- `AgentPoolACPAgent` 是单实例被多连接共享的，不能在其上存储每连接的协商版本
- `session/load` 和 `session/resume` 语义不同（load 回放历史，resume 不回放），不能简单重定向
- 会话存储中没有 `protocol_version` 字段，跨版本恢复时无法检测版本不匹配

上游 ACP v2 处于 `2.0.0-alpha.0`，规范仍在变化。本设计聚焦于**版本切换基础设施**，而非完整的 v2 协议实现。

## Goals / Non-Goals

**Goals:**
- 建立统一的协议版本解析链：CLI 参数 > 环境变量 > 代理配置 > 默认值 (v1)
- 将 `AgentPoolACPAgent.PROTOCOL_VERSION` ClassVar 连接到 `BaseACPAgentConfig.get_protocol_version()`
- 协商版本存储在每连接级别（`AgentSideConnection`），支持同一服务器实例同时服务 v1 和 v2 客户端
- `agentpool serve-acp --protocol-version 2` 能启动 v2 模式的 ACP Server
- v2 模式下 `handle_prompt()` 立即返回，通过 `state_update` 通知完成
- 统一 v2 非阻塞路径与现有 `turn_complete` 能力路径，避免重复代码
- v2 模式下已移除的 v1 方法记录 deprecation 日志并重定向到 v2 等效方法
- 会话存储中添加 `protocol_version` 元数据，跨版本恢复时发出警告
- v1 模式行为完全不变（零回归）

**Non-Goals:**
- 完整的 v1↔v2 类型转换层（Rust 版 10,688 行 — 不在本变更范围内）
- v2 新增特性实现（整消息 Upsert、三态 Patch、流式工具调用内容等 — 后续变更）
- ACP Agent Client 侧的 v2 支持（`agentpool/agents/acp_agent/` — 后续变更）
- v2 客户端 fs/terminal 移除的替代方案（需单独评估）
- v2 正式发布（v2 仍是实验性 alpha）
- 重定向 `session/load` 到 `session/resume`（两者语义不同，不重定向）

## Decisions

### D1: 版本解析优先级链

**决策**: CLI 参数 > 环境变量 > 代理配置 > 默认值 (v1)

**理由**: 遵循 12-factor app 原则。CLI 参数用于开发时快速切换，环境变量用于部署配置，代理配置用于多代理场景下的细粒度控制。

### D2: 协商版本存储在每连接级别

**决策**: 将 `_negotiated_version` 存储在 `AgentSideConnection` 上，而非 `AgentPoolACPAgent` 实例上。

**理由**: `AgentPoolACPAgent` 是单实例被多连接共享的（在 `ACPServer` 中创建一次）。如果在其上存储版本，并发连接的不同版本会互相覆盖。`AgentSideConnection` 是每连接独立的，是正确的存储位置。

**替代方案**: 在 `AgentPoolACPAgent` 上存储 — 被否决，因为会导致并发连接版本覆盖。

### D3: 统一 v2 非阻塞路径与现有 turn_complete 路径

**决策**: v2 的非阻塞 prompt 路径与现有 `client_supports_turn_complete=True` 路径统一。`_negotiated_version == 2` 隐含 `turn_complete=True` 行为，不创建独立代码分支。

**理由**: 现有 `handler.py:494-497` 已有非阻塞路径：当 `client_capabilities.turn_complete` 为 True 时跳过 `_turn_complete_event.wait()`。v2 的非阻塞路径本质上是同一逻辑。创建独立分支会导致重复代码和潜在的重复通知。

**实现**: 在 `handle_prompt()` 中，将版本检查整合到现有条件中：
```python
non_blocking = (
    self._negotiated_version == 2
    or (self.client_capabilities is not None and self.client_capabilities.turn_complete)
)
if run_handle is not None and not non_blocking:
    await run_handle._turn_complete_event.wait()
```

### D4: 已移除方法的 deprecation 重定向策略

**决策**: v2 模式下对已移除的 v1 方法采用统一策略：**记录 deprecation 日志 + 重定向到 v2 等效方法**，而非返回 `method_not_found` 错误。

具体重定向映射：
| v1 方法 | v2 等效 | 处理 |
|---------|---------|------|
| `session/set_mode` | `session/set_config_option` | 将 `mode_id` 映射到 `category_id="mode"` 配置项 |
| `session/load` | 保留原逻辑（不重定向到 `session/resume`） | 记录 deprecation 日志，正常执行 load 逻辑（含历史回放） |
| `authenticate` | `auth/login` | 记录 deprecation 日志，调用现有认证逻辑 |

**理由**: Zed 等客户端仍会发送这些方法。返回错误会导致客户端崩溃。重定向比拒绝更友好，且符合向后兼容原则。`session/load` 不重定向到 `session/resume` 是因为两者语义不同（load 回放历史，resume 不回放），重定向会破坏客户端行为。

**替代方案**: 返回 `method_not_found` — 被否决，因为会破坏过渡期客户端。

### D5: v2 能力声明结构

**决策**: 在 `initialize()` 中根据协商版本返回不同的 `InitializeResponse`：
- v1: 现有扁平化结构（`load_session`, `prompt_capabilities`, `mcp_capabilities` 等顶级字段）
- v2: 作用域化结构（`session.load`, `session.mcp`, `session.list` 等嵌套字段），`info` 替代 `agent_info`

### D6: PROTOCOL_VERSION 连接到配置系统

**决策**: 将 `AgentPoolACPAgent.PROTOCOL_VERSION` 从 `ClassVar = 1` 改为实例属性。由于 `AgentPoolACPAgent` 是 `@dataclass`，使用 `field(init=False, default=1)` 声明，在 `__post_init__` 中调用 `BaseACPAgentConfig.get_protocol_version()` 获取实际值。`acp/schema/__init__.py` 的 `PROTOCOL_VERSION = 1` 保留作为模块级默认值。

### D7: 会话存储添加 protocol_version 元数据

**决策**: 在 `SessionData` 模型中添加 `protocol_version: int | None` 字段。`load_session()` 和 `resume_session()` 在恢复时检查存储版本与当前协商版本是否一致，不一致时记录 warning 日志。

**注意**: `SessionData` 已有 `version: str = "1"` 字段，这是会话 schema 版本（用于 Pydantic 模型向后兼容），与 ACP 协议版本无关。新增的 `protocol_version: int | None` 是独立的 ACP 协议版本字段，两者语义不同，不应合并。

**理由**: 服务器从 v1 升级到 v2 后，已有的 v1 会话 checkpoint 可能在 v2 模式下恢复。需要检测这种情况并警告，避免静默使用不兼容的行为。

### D8: v2 alpha 版本兼容性标记

**决策**: 添加 `ACP_V2_COMPAT_VERSION = "2.0.0-alpha.0"` 常量到 `acp/settings.py`。v2 模式启动时记录此版本。当上游 alpha 版本变化时，更新此常量并运行 snapshot 测试检测 breakage。

**理由**: 上游 v2 规范仍在变化。显式标记兼容的 alpha 版本，让开发者知道当前实现的兼容性范围。

### D9: _negotiated_version 作为参数传递给共享 ACPProtocolHandler

**决策**: `_negotiated_version` 存储在 `AgentSideConnection`（每连接），但 `ACPProtocolHandler` 是 `AgentPoolACPAgent` 上的共享单例。版本信息通过**方法参数**传递给 `ACPProtocolHandler.handle_prompt()` 等版本感知方法，**不**存储在 handler 实例上。

**理由**: `ACPProtocolHandler` 在 `AgentPoolACPAgent.__post_init__()` 中创建为单例，被所有连接共享。如果在 handler 实例上存储版本，并发连接会互相覆盖——与 D2 试图解决的并发问题相同。通过参数传递，每次调用都是无状态的。

**实现**:
```python
# AgentPoolACPAgent.prompt() 中
async def prompt(self, params: PromptRequest) -> PromptResponse:
    if self._protocol_handler is not None:
        return await self._protocol_handler.handle_prompt(
            params.session_id, params.prompt,
            negotiated_version=self._connection._negotiated_version,  # 从连接获取
        )
```

`ACPEventConverter` 在 `_before_consumer_loop()` 中创建时，也通过参数接收版本（而非从共享 handler 状态读取）。

### D10: v2 模式下 state_update 替代 TurnCompleteUpdate

**决策**: 在 v2 模式下，`state_update(idle)` **替代** `TurnCompleteUpdate`。`ACPEventConverter` 在 `_negotiated_version == 2` 时跳过 `TurnCompleteUpdate` 的发送，改为发送 `state_update(idle + stop_reason)`。v1 模式下保持现有 `TurnCompleteUpdate` 行为不变。

**理由**: 两者都标记轮次结束。同时发送会造成客户端收到两个结束通知，产生歧义。v2 的 `state_update` 是规范定义的完成信号，应替代 v1 的 `TurnCompleteUpdate`。

### D11: 双 handler 架构澄清

**决策**: 版本切换涉及两个不同的代码路径：
1. **`_agent_handler()` (connection.py)** — 负责方法路由和 deprecation 重定向（D4）。所有版本检查在此处的 match 语句中完成。
2. **`ACPProtocolHandler` (handler.py)** — 负责 prompt 生命周期（D3）。仅在 `use_session_pool=True` 时启用。版本信息通过参数传递（D9）。

当 `use_session_pool=False` 时，v2 prompt 生命周期不在本变更范围内（legacy `ACPSession.process_prompt()` 路径已废弃，不投入 v2 改造）。

## Risks / Trade-offs

- **[v2 规范仍在变化]** → 用 `ACP_V2_COMPAT_VERSION` 常量标记兼容版本，v2 代码通过版本条件门控
- **[Prompt 异步化竞态条件]** → 统一 v2 路径与现有 `turn_complete` 路径，避免重复通知。添加 `state_update` 顺序测试
- **[v1 回归风险]** → v1 路径不修改任何现有逻辑，所有 v2 分支通过 `_negotiated_version == 2` 条件门控
- **[并发 v1/v2 会话]** → 版本存储在每连接级别（`AgentSideConnection`），通过参数传递给共享 handler（D9），不影响全局状态
- **[client_capabilities 共享问题（已知）]** → `AgentPoolACPAgent.client_capabilities` 和 `ACPProtocolHandler.client_capabilities` 存在与 `_negotiated_version` 相同的共享实例并发问题。这是**预存问题**，不在本变更范围内修复。后续变更应将 `client_capabilities` 也移至每连接存储。当前实现中，D3 的 `non_blocking` 检查同时依赖 `_negotiated_version` 和 `client_capabilities`，在并发场景下 `client_capabilities` 可能被覆盖——但 v2 模式下 `_negotiated_version == 2` 已足以触发非阻塞路径，不依赖 `client_capabilities`
- **[跨版本会话恢复]** → 会话存储添加 `protocol_version` 元数据，恢复时检测并警告
- **[上游 alpha 版本不稳定]** → 版本切换基础设施是向前兼容的，v2 具体实现可随上游变化调整
- **[deprecation 重定向的客户端兼容性]** → Zed/JetBrains 等客户端仍发送 v1 方法，重定向确保平滑过渡
- **[SQL 存储迁移]** → 添加 `protocol_version` 字段到 `SessionData` 模型。SQL 存储使用 nullable column（`ALTER TABLE ADD COLUMN ... NULL`），向后兼容。旧记录为 NULL，恢复时触发版本不匹配 warning。如使用 Alembic，需添加迁移脚本
