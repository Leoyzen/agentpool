---
rfc_id: RFC-0034
title: "ACP Session Config Options 统一化：在 IDE 中透出模型选择与 Agent Role 切换"
status: DRAFT
author: yuchen.liu
reviewers: []
created: 2026-05-26
last_updated: 2026-05-26
decision_date:
related_rfcs:
  - RFC-0027 (ACP Subagent Zed 兼容性)
  - RFC-0031 (ACP Per-Session Agent Isolation)
  - RFC-0032 (ACP Slash Commands Protocol Compliance)
---

# RFC-0034: ACP Session Config Options 统一化

## 概述

本 RFC 提出统一 AgentPool ACP Server 的 Session Config Options 透出逻辑，使 Zed、Cursor 等 ACP 兼容 IDE 能够在 UI 中：

1. **选择模型（Model）**：列出 manifest `model_variants` 和 tokonomics 发现的可用模型
2. **切换 Agent Role**：将 `agent_pool.all_agents` 中的多个 agent 作为可选 role 透出
3. **切换 Tool Permission**：already/never/per_tool 工具确认策略
4. **其他 config options**：thought_level、personality 等 agent 暴露的扩展 category

当前 AgentPool 仅通过 ACP 旧版 `SessionModeState`（`session/set_mode`）透出 tool permission，model 和 agent role 均未通过标准 ACP 机制透出。与此同时，`serve-opencode` 通过 REST API 自有机制透出了 model list，但两套透出逻辑相互独立、数据来源不同，存在显著差异。

本 RFC 的目标是对齐两套协议的数据来源，并让 ACP 通道能完整透出 model + agent role + 所有 mode categories，从而实现"在 IDE 中选择模型和 agent"的完整体验。

## 背景与上下文

### 当前系统状态

#### ACP 协议的 Mode/Config Options 层

ACP 协议提供两套机制透出可切换配置：

| 机制 | 协议方法 | 状态 | 描述 |
|------|----------|------|------|
| `SessionModeState` | `session/set_mode` | 正式规范 | 单一 mode 选择器，一次只能有一套 modes |
| `SessionConfigOption[]` | `session/set_config_option` | UNSTABLE | 多维度 config option，每个 category 独立选择器 |

AgentPool 当前实现：
- `NewSessionResponse.modes` → 透出 tool permission（`get_session_mode_state()`，只取 `id=="mode"` 的 category）
- `NewSessionResponse.config_options` → 透出所有 `agent.get_modes()` 返回的 categories（`get_session_config_options()`）
- `NewSessionResponse.models` → 透出 tokonomics 发现的模型 + manifest model_variants（`get_session_model_state()`）

#### agentpool 内部的 `get_modes()` 体系

各 agent 类型的 `get_modes()` 返回如下 categories：

| Agent 类型 | Categories | 说明 |
|------------|------------|------|
| `NativeAgent` | `mode`（tool permission）+ `model` | model 来自 tokonomics |
| `ClaudeCodeAgent` | `mode` + `model` + `thought_level` | Claude 的 extended thinking |
| `CodexAgent` | `mode`（approval policy）+ `model`（effort）+ `thought_level` | Codex 特有模式 |
| `ACPAgent`（嵌套） | passthrough from remote server | 透传远端 config_options |
| `AGUIAgent` | `[]`（无） | 不支持 mode 切换 |

#### serve-opencode 的 Model List 逻辑

`opencode_server/routes/config_routes.py` 的 `_build_providers_with_fallback()` 采用如下优先级：

1. **configured variants**（manifest `model_variants`）→ 解析 provider 信息，按 provider 分组
2. **tokonomics 全局发现**（`get_all_models()`，7 天缓存）→ 全量模型
3. **agent-specific modes**（仅 Codex/ClaudeCode 的 `thought_level`）→ fallback
4. 空列表 + warning

#### ACP 的 Model List 逻辑

`acp_server/acp_agent.py` 的 `get_session_model_state()` 采用如下优先级：

1. **tokonomics first**：`agent.get_available_models()` → 逐 agent 调用，每次请求都可能触发网络
2. **configured override**：manifest `model_variants` 的 key（仅取名称，不解析 provider）

#### Agent Role 的透出现状

- `acp_server/converters.py` 存在 `agent_to_mode()` 辅助函数，可将 agent 转换为 `SessionMode`，但**从未被调用**
- `get_session_mode_state()` 只取 `id=="mode"` 的 category（tool permission），不包含 agent role
- `agent_routes.py` 的 `GET /agent` 端点可列出所有 agents，供 opencode TUI 使用，但 ACP 协议无对应端点

### 问题陈述

#### GAP 1（P0）：Agent Role 完全未透出到 ACP

Zed 等 IDE 通过 `SessionModeState` 或 `config_options` 中的 `category="mode"` 来展示可选的 agent role（在 Zed 中称为 "profile" 或 "agent"）。AgentPool 的 `agent_pool.all_agents` 包含所有已配置的 agent，但 ACP Server 未将其透出。

**影响**：用户无法在 Zed 等 IDE 的 UI 中切换 agent role（如从 `diag-agent` 切换到 `plan-agent`）。

#### GAP 2（P1）：ACP 和 OpenCode 的 Model List 数据来源不一致

| 维度 | ACP | OpenCode |
|------|-----|---------|
| 数据优先级 | tokonomics first，configured override | configured first，tokonomics fallback |
| tokonomics 来源 | `agent.get_available_models()`（per-agent，每次调用） | `get_all_models()`（全局，7 天缓存） |
| model_variants | 仅取 key 名，无 provider 解析 | 解析 `AnyModelConfig`，提取 provider |
| 结构 | 扁平 `SessionModelState` | 按 provider 分组的 `Provider[]` |

当用户在两个协议下使用同一配置时，看到的模型列表不同。

#### GAP 3（P1）：`/mode` 路由硬编码，未动态透出 agent modes

`opencode_server/routes/config_routes.py` 的 `GET /mode` 返回硬编码的 `[Mode(name="default")]`，没有调用 `agent.get_modes()` 动态透出 `mode` category（如 permission modes）。

#### GAP 4（P2）：ACP `get_session_mode_state()` 过滤过严

当前 `get_session_mode_state()` 只提取 `id=="mode"` 的 category 填入旧版 `SessionModeState`。其余 categories（model、thought_level 等）通过 `config_options` 透出，但旧版 `modes` 字段只有 tool permission，导致仅支持旧版 `session/set_mode` 的客户端（如部分旧版 Zed）看不到 model 选择器。

### 相关工作

| RFC | 状态 | 关系 |
|-----|------|------|
| RFC-0027 | DRAFT | ACP Subagent Zed 兼容性，提出 `display_mode=zed` |
| RFC-0031 | IMPLEMENTED | ACP Per-Session Agent Isolation，每 session 独立 agent 实例 |
| RFC-0032 | DRAFT | ACP Slash Commands 合规性 |

### 术语表

| 术语 | 定义 |
|------|------|
| `SessionModeState` | ACP 旧版单一 mode 选择器，通过 `session/set_mode` 切换 |
| `SessionConfigOption` | ACP 新版多维度配置选项，通过 `session/set_config_option` 切换 |
| `SessionConfigOptionCategory` | Config option 的语义分类：`"mode"` / `"model"` / `"thought_level"` / `"other"` |
| `model_variants` | manifest YAML 中的模型变体配置，键为变体名，值为 `AnyModelConfig` |
| `get_modes()` | `BaseAgent` 抽象方法，返回所有可切换的 `ModeCategory` 列表 |
| agent role | 多 agent 配置中可切换的 agent 身份，如 `diag-agent`、`plan-agent` |
| `config_options` | `NewSessionResponse` / `LoadSessionResponse` 中的 UNSTABLE 扩展字段 |

## 目标与非目标

### 目标

| ID | 目标 | 优先级 |
|----|------|--------|
| G1 | ACP `config_options` 中透出 agent role（`agent_pool.all_agents`），支持 `session/set_config_option` 切换 | P0 |
| G2 | 统一 ACP 和 OpenCode 的 model list 数据来源（configured variants 优先，tokonomics 兜底） | P1 |
| G3 | ACP `config_options` 中的 model category 使用与 OpenCode 一致的优先级逻辑 | P1 |
| G4 | `serve-opencode` 的 `GET /mode` 路由动态透出 agent modes（调用 `agent.get_modes()`） | P1 |
| G5 | 新增 `agent_role` config option category，切换时实际替换 session 的 agent 实例 | P0 |
| G6 | 保持向后兼容：旧版 `SessionModeState.modes` 继续透出 tool permission | P0 |

### 非目标

| ID | 非目标 | 理由 |
|----|--------|------|
| NG1 | 修改 ACP 协议 schema 或 `SessionConfigOptionCategory` 枚举 | 使用现有 `"other"` category 即可承载 agent role |
| NG2 | 将 OpenCode 的 `/provider` REST API 替换为 ACP 协议 | 两套协议并行存在，各自服务不同客户端 |
| NG3 | 实现 agent role 的持久化（跨 session 记忆上次选择） | 不在本 RFC 范围 |
| NG4 | 支持动态新增/删除 agent（运行时 reload manifest） | 涉及 agent pool 热更新，属于单独 RFC |
| NG5 | 跨 session 同步 config option 变更 | 每个 session 独立配置，不需要全局同步 |

## 评估标准

| 标准 | 权重 | 描述 | 最低阈值 |
|------|------|------|----------|
| IDE 兼容性 | 关键 | Zed 等 IDE 能显示 agent role 和模型选择器 | config_options 能被客户端正确解析 |
| 数据一致性 | 高 | ACP 和 OpenCode 透出的模型列表相同 | 相同配置下两者列表集合一致 |
| 向后兼容性 | 关键 | 旧版 SessionModeState.modes 不变 | 现有 tool permission 切换功能正常 |
| 代码复用 | 高 | ACP 和 OpenCode 复用共享的 model 构建逻辑 | 提取到 `shared/model_utils.py` |
| 实施复杂度 | 中 | 修改范围可控 | 不超过 5 个核心文件 |
| 可测试性 | 中 | 可单元测试的 config option 构建逻辑 | 覆盖率 ≥ 80% |

## 方案分析

### 选项 1：最小修复 — 仅补充 Agent Role 到 config_options

**描述**：仅在 `get_session_config_options()` 的输出中追加一个 `agent_role` config option（枚举 `agent_pool.all_agents`），不修改 model list 逻辑，不统一数据来源。

**实施范围**：
- 在 `acp_server/acp_agent.py` 中新增 `get_agent_role_config_option()` 函数
- 修改 `get_session_config_options()` 追加 agent role option
- 修改 `AgentPoolACPAgent.set_session_config_option()` 处理 `agent_role` category，切换 session agent

**优势**：
- 修改范围最小，仅涉及 `acp_server/acp_agent.py` 一个文件
- 快速解决 G1（P0 问题）
- 不影响 model list 现有逻辑，风险低

**劣势**：
- 未解决 G2/G3（ACP 与 OpenCode model list 不一致）
- 未解决 G4（OpenCode `/mode` 路由硬编码）
- 技术债务继续积累，后续需二次重构

**评估**：

| 标准 | 评分 | 说明 |
|------|------|------|
| IDE 兼容性 | 3/5 | Agent role 透出，但 model list 仍与 OpenCode 不一致 |
| 数据一致性 | 1/5 | 未解决 ACP/OpenCode model list 差异 |
| 向后兼容性 | 5/5 | 不影响现有 modes 逻辑 |
| 代码复用 | 2/5 | 未提取共享逻辑 |
| 实施复杂度 | 5/5 | 改动最小 |
| 可测试性 | 4/5 | 逻辑简单，易于测试 |

**工作量**：低（~80 行）

---

### 选项 2：统一数据来源 + 完整 Config Options 对齐

**描述**：统一 ACP 和 OpenCode 的 model list 构建逻辑（提取到共享模块），同时补充 agent role config option，修复 OpenCode `/mode` 路由。

**实施范围**：

**Phase 1：共享 Model List 逻辑**
- 在 `shared/model_utils.py` 中提取统一的 `build_model_config_options()` 函数
  - configured variants 优先（解析 provider，与 OpenCode 对齐）
  - tokonomics 兜底（使用 `agent.get_available_models()` 而非全局 `get_all_models()`，保持 per-agent 语义）
- 修改 `acp_server/acp_agent.py` 的 `get_session_model_state()` 使用共享逻辑
- 保持 ACP 的扁平 `SessionModelState` 结构（不改为 provider 分组，协议格式不变）

**Phase 2：Agent Role Config Option**
- 新增 `get_agent_role_config_option()` 函数，枚举 `agent_pool.all_agents`
- 将 agent role 作为 `SessionConfigOption`（`id="agent_role"`，`category="other"`）
- 修改 `set_session_config_option()` 处理 `agent_role`：调用 `AgentPoolACPAgent._swap_session_agent()` 切换 session agent 实例

**Phase 3：OpenCode /mode 路由修复**
- 修改 `config_routes.py` 的 `GET /mode`，调用 `agent.get_modes()` 动态透出 `mode` category 的 available modes
- 将 `ModeCategory.available_modes` 转换为 `list[Mode]` 返回

**优势**：
- 彻底解决 ACP 与 OpenCode 的 model list 不一致问题
- Agent role 透出完整，切换逻辑闭环
- 代码复用性高，未来新增协议支持时可复用共享逻辑
- `/mode` 路由不再硬编码，动态反映 agent 配置

**劣势**：
- 修改范围较大（5 个文件），需要更充分的测试
- Phase 1 中统一 model list 来源可能带来细微行为差异（tokonomics per-agent vs 全局）
- OpenCode `/mode` 路由修复是纯增量，不涉及破坏性变更，但需验证所有 agent 类型

**评估**：

| 标准 | 评分 | 说明 |
|------|------|------|
| IDE 兼容性 | 5/5 | 完整透出 model + agent role，两协议一致 |
| 数据一致性 | 5/5 | 共享 model 构建逻辑，来源统一 |
| 向后兼容性 | 5/5 | 旧版 modes 不变，config_options 为增量 |
| 代码复用 | 5/5 | shared/model_utils.py 统一模型构建 |
| 实施复杂度 | 3/5 | 三阶段，每阶段独立可交付 |
| 可测试性 | 4/5 | 共享逻辑独立可测试 |

**工作量**：中（~280 行）

---

### 选项 3：全面重构 — 引入统一 AgentCapabilities 注册机制

**描述**：在选项 2 基础上，引入统一的 `AgentCapabilities` 注册中心，ACP/OpenCode/AGUI 三套协议均从同一来源构建 model list、mode list 和 agent role list。

**实施范围**：
- 新增 `AgentCapabilitiesProvider` 抽象层，统一各协议的 capabilities 查询
- 三套协议的 model/mode/role 查询均委托给 `AgentCapabilitiesProvider`
- 引入能力缓存层，避免重复调用 tokonomics

**优势**：
- 单一真相来源，三套协议数据完全一致
- 可扩展性强，未来新协议直接复用
- 缓存层解决 tokonomics 性能问题

**劣势**：
- 工程量大，抽象层设计复杂
- 现有三套协议各有历史包袱，完全统一风险高
- 本 RFC 范围之外，需独立 RFC 立项

**评估**：

| 标准 | 评分 | 说明 |
|------|------|------|
| IDE 兼容性 | 5/5 | 完整且一致 |
| 数据一致性 | 5/5 | 单一真相来源 |
| 向后兼容性 | 3/5 | 重构风险较高 |
| 代码复用 | 5/5 | 终极复用 |
| 实施复杂度 | 1/5 | 工程量极大 |
| 可测试性 | 4/5 | 抽象层可独立测试 |

**工作量**：高（~800 行以上）

---

## 推荐

**推荐选项 2：统一数据来源 + 完整 Config Options 对齐**，分三阶段实施。

推荐理由：
1. 选项 1 仅解决 P0 问题，遗留 model list 不一致，用户在 Zed 和 OpenCode TUI 看到不同模型列表，体验差
2. 选项 2 通过提取共享逻辑，以较低成本同时解决 G1-G4，技术债务清零
3. 选项 3 范围过大，引入不必要的架构复杂度，超出本 RFC 目标
4. 三阶段设计使每阶段独立可交付，降低集成风险

**接受的权衡**：
- ACP 侧 tokonomics 来源保持 per-agent（`agent.get_available_models()`），与 OpenCode 的全局 `get_all_models()` 不完全相同。这是有意为之：ACP 是 per-session 协议，per-agent 语义更准确；OpenCode 是全局 REST API，全局缓存更合理。
- model list 的 **结构**（ACP：扁平 `SessionModelState`；OpenCode：provider 分组）保持不变，统一的是**数据来源优先级**，而非数据结构。

## 技术设计

### 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                    当前状态（存在差异）                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ACP Server                      OpenCode Server                │
│  ──────────────────               ────────────────────────────  │
│  get_session_model_state()        _build_providers_with_fallback │
│    → tokonomics first             → configured first            │
│    → manifest.model_variants      → tokonomics fallback         │
│      （仅取 key 名）              → agent-specific modes         │
│                                                                  │
│  get_session_config_options()     GET /mode                     │
│    → get_modes() (完整)           → 硬编码 [default] ❌          │
│                                                                  │
│  get_session_mode_state()         GET /agent                    │
│    → tool permission only         → all_agents (正确)           │
│                                                                  │
│  Agent Role: 未透出 ❌                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    目标状态（对齐后）                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  shared/model_utils.py                                           │
│    build_model_config_option_for_acp()                          │
│      → configured variants first（解析 provider → 仅取名）       │
│      → tokonomics fallback（agent.get_available_models()）       │
│                                                                  │
│  ACP Server                      OpenCode Server                │
│  ──────────────────               ────────────────────────────  │
│  get_session_model_state()        _build_providers_with_fallback │
│    ↑ 使用共享逻辑                  ↑ 保持不变（provider 分组）     │
│    → configured first ✅          → configured first ✅          │
│                                                                  │
│  get_session_config_options()     GET /mode                     │
│    → get_modes() + agent_role     → agent.get_modes() 动态 ✅   │
│                                                                  │
│  Agent Role: config_options ✅                                   │
│    id="agent_role", category="other"                             │
│    options=[all_agents as ModeInfo]                              │
│    切换时: _swap_session_agent()                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Phase 1：共享 Model List 逻辑

#### `shared/model_utils.py` 新增函数

```python
async def build_model_state_for_acp(
    agent: BaseAgent,
    manifest: AgentsManifest | None = None,
) -> SessionModelState | None:
    """构建 ACP SessionModelState，与 OpenCode 对齐优先级逻辑。

    优先级：
    1. manifest.model_variants（configured variants，优先）
    2. agent.get_available_models()（tokonomics，兜底）

    Args:
        agent: 当前 session 的 agent 实例
        manifest: 可选的 AgentsManifest 配置

    Returns:
        SessionModelState，若无可用模型则返回 None
    """
    from acp.schema import ModelInfo as ACPModelInfo, SessionModelState

    # Step 1: configured variants（与 OpenCode 对齐：configured first）
    configured: dict[str, ACPModelInfo] = {}
    if manifest and manifest.model_variants:
        for name in manifest.model_variants:
            configured[name] = ACPModelInfo(model_id=name, name=name)

    # Step 2: tokonomics fallback（仅当 configured 为空时调用）
    toko_models: dict[str, ACPModelInfo] = {}
    if not configured:
        try:
            raw = await agent.get_available_models()
            for m in raw or []:
                mid = m.id_override or m.id
                toko_models[mid] = ACPModelInfo(
                    model_id=mid,
                    name=m.name,
                    description=m.description or "",
                )
        except Exception:
            logger.exception("Failed to get available models from agent")

    all_models = configured if configured else toko_models  # strict fallback: configured 存在时只用 configured

    if not all_models:
        return None

    acp_list = list(all_models.values())
    all_ids = [m.model_id for m in acp_list]
    current = agent.model_name
    if current and current not in all_ids:
        acp_list.insert(0, ACPModelInfo(
            model_id=current, name=current, description="Currently configured model"
        ))
        current_id = current
    else:
        current_id = current if current in all_ids else all_ids[0]

    return SessionModelState(available_models=acp_list, current_model_id=current_id)
```

#### 修改 `acp_server/acp_agent.py`

将 `get_session_model_state()` 中的逻辑替换为调用 `build_model_state_for_acp()`：

```python
# 修改前（acp_agent.py:70-142）：tokonomics first，configured override
async def get_session_model_state(agent: BaseAgent) -> SessionModelState | None:
    toko_models = await agent.get_available_models()          # tokonomics first
    configured = [ACPModelInfo(model_id=name, ...) for name in manifest.model_variants]
    ...

# 修改后：委托给共享逻辑
async def get_session_model_state(agent: BaseAgent) -> SessionModelState | None:
    from agentpool_server.shared.model_utils import build_model_state_for_acp
    manifest = getattr(getattr(agent, "agent_pool", None), "manifest", None)
    return await build_model_state_for_acp(agent, manifest)
```

### Phase 2：Agent Role Config Option

#### 数据模型

Agent role 作为一个 `SessionConfigOption` 透出：

```python
# acp_server/acp_agent.py 中新增
async def get_agent_role_config_option(
    agent: BaseAgent,
) -> SessionConfigOption | None:
    """构建 agent role config option。

    将 agent_pool.all_agents 作为可选 role 枚举。
    若 pool 只有一个 agent，或 pool 不可访问，返回 None。

    Returns:
        SessionConfigOption（id="agent_role"，category="other"），
        若 agent 数量 <= 1 则返回 None（无需透出单一 agent）。
    """
    from acp.schema import SessionConfigOption, SessionConfigSelectOption

    pool = getattr(agent, "agent_pool", None)
    if pool is None:
        return None

    all_agents = getattr(pool, "all_agents", {})
    if len(all_agents) <= 1:
        return None  # 单一 agent 无需切换

    options = [
        SessionConfigSelectOption(
            value=name,
            name=ag.display_name or name,
            description=ag.description or f"Switch to {name} agent",
        )
        for name, ag in all_agents.items()
    ]
    current = agent.name

    return SessionConfigOption(
        id="agent_role",
        name="Agent",
        description="Select the active agent for this session",
        category="other",
        current_value=current,
        options=options,
    )
```

#### 修改 `get_session_config_options()`

```python
async def get_session_config_options(agent: BaseAgent) -> list[SessionConfigOption]:
    """Get all SessionConfigOptions from agent's modes + agent role."""
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        mode_categories = []

    options = [to_session_config_option(category) for category in mode_categories]

    # 追加 agent role option（若有多个 agents）
    if role_opt := await get_agent_role_config_option(agent):
        options.append(role_opt)

    return options
```

#### 修改 `set_session_config_option()` — 处理 `agent_role`

```python
async def set_session_config_option(
    self, params: SetSessionConfigOptionRequest
) -> SetSessionConfigOptionResponse | None:
    ...
    if params.config_id == "agent_role":
        # 切换 session 的 agent 实例
        await self._swap_session_agent(params.session_id, params.value)
    else:
        # 原有逻辑：转发到 agent.set_mode()
        await session.agent.set_mode(params.value, category_id=params.config_id)

    config_options = await get_session_config_options(session.agent)
    return SetSessionConfigOptionResponse(config_options=config_options)
```

#### `_swap_session_agent()` 实现

```python
async def _swap_session_agent(
    self,
    session_id: str,
    agent_name: str,
) -> None:
    """将指定 session 切换到不同的 agent 实例。

    利用 RFC-0031 的 per-session agent 机制，委托给 ACPSession.switch_active_agent()。
    整个 swap 过程在 session 级锁保护下执行，防止并发竞态。

    Args:
        session_id: 目标 session ID
        agent_name: 目标 agent 名称（须在 agent_pool.all_agents 中存在）

    Raises:
        ValueError: agent_name 不在 pool 中
        RuntimeError: session 不存在，或 swap 期间存在未完成的 prompt task
    """
    session = self.session_manager.get_session(session_id)
    if not session:
        raise RuntimeError(f"Session not found: {session_id}")

    # 获取或创建 session 级锁
    if session_id not in self._session_agent_locks:
        self._session_agent_locks[session_id] = asyncio.Lock()

    async with self._session_agent_locks[session_id]:
        # 协调 session._task_lock：若当前有 active prompt，禁止 swap
        if hasattr(session, "_task_lock") and session._task_lock.locked():
            raise RuntimeError(
                f"Cannot swap agent while a prompt is in progress (session={session_id})"
            )

        pool = self.agent_pool
        if pool is None or pool.manifest is None or agent_name not in pool.manifest.agents:
            raise ValueError(f"Agent not found: {agent_name}")

        # 委托给 ACPSession.switch_active_agent() 处理完整的 swap 流程：
        # 1. 断开旧 agent 的 state_updated 信号
        # 2. 调用 remove_session_agent() 关闭旧 per-session agent（跳过 default_agent）
        # 3. 创建新 per-session agent（get_or_create_session_agent）
        # 4. 重新应用 session mutation（env, input_provider, sys_prompts, cwd context）
        # 5. 重新连接 state_updated 信号
        # 6. 持久化 agent 切换（session_manager.update_session_agent）
        # 7. 发送 available commands update
        await session.switch_active_agent(agent_name)

        # 同步更新 ACP agent 层的 session_agents 注册表
        self._session_agents[session_id] = session.agent
        logger.info("Session agent swapped", session_id=session_id, new_agent=agent_name)
```

### Phase 3：OpenCode `/mode` 路由修复

#### 修改 `config_routes.py` 的 `GET /mode`

```python
@router.get("/mode")
async def list_modes(state: StateDep) -> list[Mode]:
    """List available modes from agent's mode category."""
    if state.agent is None:
        return [Mode(name="default", tools={})]

    try:
        mode_categories = await state.agent.get_modes()
        for category in mode_categories:
            if category.id == "mode":
                return [
                    Mode(
                        name=mode.id,
                        tools={},
                        # prompt 可选：若 ModeInfo 有 description 则填入
                    )
                    for mode in category.available_modes
                ]
    except Exception:  # noqa: BLE001
        logger.warning("Failed to get modes from agent for /mode route")

    # Fallback：返回默认值（保持向后兼容）
    return [Mode(name="default", tools={})]
```

### API 变更汇总

#### 新增函数

| 函数 | 文件 | 描述 |
|------|------|------|
| `build_model_state_for_acp()` | `shared/model_utils.py` | ACP model state 构建，configured first |
| `get_agent_role_config_option()` | `acp_server/acp_agent.py` | 构建 agent role config option |
| `_swap_session_agent()` | `acp_server/acp_agent.py` | 切换 session 的 agent 实例（委托 `session.switch_active_agent()` + 锁保护） |

#### 修改函数

| 函数 | 文件 | 变更描述 |
|------|------|----------|
| `get_session_model_state()` | `acp_server/acp_agent.py` | 委托给 `build_model_state_for_acp()` |
| `get_session_config_options()` | `acp_server/acp_agent.py` | 追加 agent role option |
| `set_session_config_option()` | `acp_server/acp_agent.py` | 处理 `agent_role` category |
| `list_modes()` | `opencode_server/routes/config_routes.py` | 动态调用 `agent.get_modes()` |

### Agent Role 切换状态机

```
session 初始化
    │
    ├─ NewSessionResponse.config_options 包含 agent_role option
    │    current_value = session.agent.name  # 当前 session 实际运行的 agent
    │    options = [all pool agents]
    │
    └─ 用户在 IDE 中选择 agent
         │
         ▼
    session/set_config_option
    { config_id: "agent_role", value: "plan-agent" }
         │
         ▼
    _swap_session_agent(session_id, "plan-agent")
         │
         ├─ 关闭旧 agent（__aexit__）
         └─ 创建新 agent（get_or_create_session_agent）
              │
              ▼
         SetSessionConfigOptionResponse
         { config_options: [...updated with current_value="plan-agent"] }
```

### 向后兼容保证

| 机制 | 影响 | 兼容性 |
|------|------|--------|
| `NewSessionResponse.modes`（旧版） | tool permission，不变 | ✅ 完全兼容 |
| `NewSessionResponse.models`（旧版） | model list，数据来源统一为 configured first | ✅ 格式兼容，数据来源优先级与 OpenCode 一致 |
| `NewSessionResponse.config_options`（新） | 追加 `agent_role`，现有 config_options 不变 | ✅ 新增字段，客户端忽略未知 config_id |
| `session/set_mode` | tool permission 切换，不变 | ✅ 完全兼容 |
| `session/set_config_option` | 新增 `agent_role` 处理，现有 config_id 不变 | ✅ 扩展，不破坏现有 |

## 实施计划

### Phase 1：共享 Model List 逻辑（优先级 P1）

**目标**：G2、G3

**范围**：
- [ ] 在 `shared/model_utils.py` 中新增 `build_model_state_for_acp()` 函数（configured first）
- [ ] 修改 `acp_server/acp_agent.py` 的 `get_session_model_state()` 委托给共享函数
- [ ] 编写单元测试：验证 configured variants 存在时 tokonomics 不被调用
- [ ] 编写单元测试：验证 configured variants 为空时 tokonomics 作为 fallback
- [ ] 编写单元测试：验证 current model 不在列表时被插入到列表头

**预估代码量**：~80 行新增，~30 行修改
**依赖**：无

---

### Phase 2：Agent Role Config Option（优先级 P0）

**目标**：G1、G5

**范围**：
- [ ] 在 `acp_server/acp_agent.py` 中新增 `get_agent_role_config_option()` 函数（`current_value=agent.name`）
- [ ] 修改 `get_session_config_options()` 追加 agent role option
- [ ] 在 `AgentPoolACPAgent` 中新增 `_swap_session_agent()` 方法
  - 获取 `_session_agent_locks[session_id]` 防止并发竞态
  - 检查 `session._task_lock` 拒绝 active prompt 期间的 swap
  - 验证 `pool.manifest` 非空且 `agent_name` 存在
  - 委托 `session.switch_active_agent()` 处理完整 swap 流程（信号、env、input_provider、sys_prompts 重连）
  - 同步更新 `_session_agents` 注册表
- [ ] 修改 `set_session_config_option()` 处理 `agent_role` category
- [ ] 预验证：确认 Zed 能正确渲染 `category="other"` 的 config options（若不能，需回退到旧版 `SessionModeState.modes` 方案）
- [ ] 编写单元测试：单一 agent 时 `get_agent_role_config_option()` 返回 None
- [ ] 编写单元测试：多 agent 时正确枚举所有 agent，且 `current_value` 为当前 session agent
- [ ] 编写并发测试：两个同时的 `set_config_option("agent_role", ...)` 请求被序列化，不 corrupt registry
- [ ] 编写集成测试：`session/set_config_option` 切换 agent 后，后续 `session/prompt` 使用新 agent 的 system prompt 和工具
- [ ] 添加 ACP 快照测试：验证 `NewSessionResponse.config_options` 包含 `agent_role`

**预估代码量**：~100 行新增
**依赖**：无（可与 Phase 1 并行）
**前置验证**：Zed 对 `category="other"` 的渲染行为（go/no-go 决策点）

---

### Phase 3：OpenCode /mode 路由修复（优先级 P1）

**目标**：G4

**范围**：
- [ ] 修改 `config_routes.py` 的 `GET /mode` 动态调用 `agent.get_modes()`
- [ ] 将 `mode` category 的 `available_modes` 转换为 `list[Mode]` 返回
- [ ] 保留 fallback：`get_modes()` 抛出异常时返回 `[Mode(name="default")]`
- [ ] 编写测试：`NativeAgent` 返回 `[always, never, per_tool]` 三个 Mode
- [ ] 编写测试：`agent.get_modes()` 异常时返回 `[default]`

**预估代码量**：~30 行修改
**依赖**：无（最简单，可独立交付）

---

### 里程碑总览

| Phase | 目标 | 工作量 |
|-------|------|--------|
| Phase 1 | 统一 model list 数据来源 | ~110 行 |
| Phase 2 | Agent role config option | ~100 行 |
| Phase 3 | OpenCode /mode 路由修复 | ~30 行 |
| **合计** | | **~240 行** |

Phase 1 和 Phase 2 可并行。Phase 3 独立，可任意时序交付。

## 开放问题

1. **tokonomics per-agent vs 全局的细微差异**：`build_model_state_for_acp()` 中 tokonomics 来源为 `agent.get_available_models()`（per-agent），而 OpenCode 使用 `get_all_models()`（全局 7 天缓存）。两者实际返回的模型列表是否完全一致？如果某个 agent 配置了特定的 `providers` 参数，per-agent 的返回集合可能是全局的子集。

2. **Agent 切换时的对话历史**：切换 agent role 时，新 agent **不继承**旧 agent 的对话历史。原因如下：
   - 不同 agent 通常具有不同的 system prompt 和工具集，继承历史消息可能导致新 agent 对上下文的理解出现偏差
   - ACP 协议层面没有定义 conversation 迁移的标准方式
   - 若用户需要保持上下文，可通过显式的 session/prompt 将历史摘要传递给新 agent
   **决策**：agent swap 后新 agent 从空对话开始；如需历史继承，将在后续 RFC 中专门设计 `conversation` 迁移协议。

3. **Agent Role 的 `current_value` 初始值**（✅ 已解决）：修正为 `agent.name`（当前 session 实际运行的 agent 实例），而非 `pool.main_agent.name`。这样确保 per-session agent 被切换后，IDE 中显示的是正确的当前 agent。

4. **OpenCode `/mode` 与 config_options 的关系**：`GET /mode` 修复后返回 `NativeAgent` 的 tool permission modes（always/never/per_tool），但 OpenCode TUI 的 mode 切换 UI 与 ACP 的 `config_options` 是独立的。两者是否需要在语义上对齐（即 `PATCH /config` 中修改 `mode` 是否等价于 ACP 的 `session/set_config_option` for `"mode"`）？

5. **agent_role config option 的 display 策略**：若 pool 只有 2 个 agent，是否应显示 agent role 选择器？`len(all_agents) <= 1` 时返回 `None` 的逻辑是否合理？

## 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-05-26 | 推荐选项 2（三阶段统一） | 技术债务清零，覆盖所有 P0/P1 问题，工作量可接受 |
| 2026-05-26 | ACP 侧保持 `SessionModelState` 扁平结构 | 不改变 ACP 协议格式，只统一数据来源优先级 |
| 2026-05-26 | agent_role 使用 `category="other"` | 不修改 ACP schema，`"other"` category 可承载非标准配置项 |
| 2026-05-26 | tokonomics 来源保持 per-agent 语义 | ACP per-session 协议与 per-agent model 语义一致，不强制对齐全局缓存 |
| 2026-05-26 | 单一 agent 时不透出 agent_role | 单 agent 配置无需切换，减少无意义 UI 噪声 |
| 2026-05-26 | `_swap_session_agent()` 委托 `session.switch_active_agent()` | 复用已有的完整 session mutation 逻辑（信号、env、input_provider、sys_prompts），避免重复实现和遗漏 |
| 2026-05-26 | agent swap 在 `_session_agent_locks` 保护下执行 | 防止并发 `set_config_option` 请求导致竞态条件和 session corruption |
| 2026-05-26 | agent swap 拒绝在 active prompt 期间执行 | 通过检查 `session._task_lock.locked()` 避免 mid-stream agent 切换导致 crash |
| 2026-05-26 | agent swap 后新 agent 不继承对话历史 | 不同 agent 的 system prompt 和工具集可能不兼容；历史继承需单独 RFC 设计 |

## 参考

### AgentPool 源码

- `packages/agentpool/src/agentpool_server/acp_server/acp_agent.py` — ACP server 核心，`get_session_model_state`、`get_session_config_options`
- `packages/agentpool/src/agentpool_server/acp_server/converters.py` — `to_session_config_option()`、`agent_to_mode()`（已有但未使用）
- `packages/agentpool/src/agentpool_server/opencode_server/routes/config_routes.py` — OpenCode model list 逻辑、`/mode` 路由
- `packages/agentpool/src/agentpool_server/shared/model_utils.py` — 现有共享 model 工具函数
- `packages/agentpool/src/agentpool/agents/native_agent/helpers.py` — `get_permission_category()`、`get_model_category()`
- `packages/agentpool/src/agentpool/agents/modes.py` — `ModeCategory`、`ModeInfo`、`ModeCategoryId`

### ACP 协议

- `packages/agentpool/src/acp/schema/session_state.py` — `SessionConfigOption`、`SessionConfigOptionCategory`、`SessionModeState`
- `packages/agentpool/src/acp/schema/agent_responses.py` — `NewSessionResponse`（`models`、`modes`、`config_options` 字段）
- [ACP 协议官网](https://agentclientprotocol.com/protocol/session-modes)

### 相关 RFC

- [RFC-0027: ACP Subagent Zed 兼容性](RFC-0027-acp-subagent-zed-compatibility.md)
- [RFC-0031: ACP Per-Session Agent Isolation](../implemented/RFC-0031-acp-per-session-agent-isolation.md)
- [RFC-0032: ACP Slash Commands Protocol Compliance](RFC-0032-acp-slash-commands-session-update.md)
