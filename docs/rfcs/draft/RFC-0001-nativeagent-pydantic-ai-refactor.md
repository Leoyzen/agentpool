---
rfc_id: RFC-0001
status: DRAFT
author: Sisyphus (AI Agent)
created: 2026-06-01
last_updated: 2026-06-01
---

# RFC-0001: 将 NativeAgent 精简为 pydantic-ai 模式

## Overview

本 RFC 提出将 `agentpool` 中的 `NativeAgent` 从当前的 "wrapper over wrapper" 架构重构为直接基于 `pydantic-ai` 原生模式的轻量实现。当前 `NativeAgent` 在 `BaseAgent` 中自建了完整的 agent loop、injection manager、event system、tool framework，然后每次 run 时才临时构造 `pydantic_ai.Agent` 作为底层执行引擎。这种架构导致大量代码重复、维护成本高、且 pydantic-ai 的新特性无法及时暴露。

本 RFC 的目标是让 `NativeAgent` 成为 `pydantic-ai Agent` 的薄层适配器，将 turn 内部的 model loop、tool calling、output validation、streaming 等职责完全委托给 pydantic-ai，agentpool 仅保留 YAML 配置、session 管理、协议暴露、多 agent 编排等独特价值。

## Background & Context

### 当前架构

```
┌─────────────────────────────────────────────────────────────┐
│  Protocol Handlers (ACP / OpenCode / AG-UI / MCP / API)     │
├─────────────────────────────────────────────────────────────┤
│  SessionPool (TurnRunner + EventBus + SessionController)    │
├─────────────────────────────────────────────────────────────┤
│  AgentPool Registry (YAML Manifest + 异构 Agent 管理)        │
├─────────────────────────────────────────────────────────────┤
│  BaseAgent                                                  │
│  ├── run_stream() — while loop + injection_manager         │
│  ├── ToolManager — 自建 tool framework                     │
│  ├── MessageHistory — 自建 conversation management         │
│  ├── AgentRunContext — per-run state + cancellation        │
│  └── _stream_events() — abstract, subclass implements      │
├─────────────────────────────────────────────────────────────┤
│  NativeAgent                                                │
│  └── get_agentlet() — 每次 run new 一个 PydanticAgent      │
│      ├── 收集 tools → wrap → to_pydantic_ai()              │
│      ├── 收集 instructions from providers                  │
│      └── 构造 PydanticAgent(...)                           │
├─────────────────────────────────────────────────────────────┤
│  pydantic-ai Agent (临时实例，run 完即弃)                    │
│  └── Agent.iter() → AgentRun → Graph Loop                  │
│      (UserPromptNode → ModelRequestNode → CallToolsNode)   │
└─────────────────────────────────────────────────────────────┘
```

### 问题分析

1. **双重 loop**：BaseAgent 有 while loop 处理 queue/injection，PydanticAgent 内部也有 graph loop 处理 model→tool→model。两层嵌套导致复杂度高。

2. **临时实例**：每次 `run()` 都 `new PydanticAgent()`，tool wrapping、instruction 收集重复执行，无法复用。

3. **自建轮子**：
   - `Tool[T]` / `FunctionTool` / `ToolManager` → pydantic-ai 已有 `Tool` / `FunctionToolset` / `ToolManager`
   - `MessageHistory` + compaction → pydantic-ai 已有 `message_history: list[ModelMessage]` + `HistoryProcessor`
   - `AgentRunContext` → pydantic-ai 已有 `RunContext` + `GraphRunContext`
   - `RichAgentStreamEvent` → pydantic-ai 已有 `AgentStreamEvent` / `FunctionToolCallEvent` / `FunctionToolResultEvent`
   - `to_structured()` 运行时突变 → pydantic-ai 的 `output_type` + validators 更干净

4. **特性滞后**：pydantic-ai 持续演进（graph builder、capabilities、pending messages、durable execution），agentpool 无法自动继承。

## Problem Statement

### 具体问题

- **维护负担高**：BaseAgent + NativeAgent 约 2000+ 行代码，大量逻辑与 pydantic-ai 重复
- **类型不安全**：自建 event system 缺乏 pydantic-ai 的严格类型保证
- **测试覆盖难**：自建 loop 的 edge cases（cancellation、injection timing、tool retry）需要大量测试
- **功能受限**：无法使用 pydantic-ai 的 `Agent.iter()` 粒度控制、`GraphBuilder` 工作流、`capabilities` 插件系统

### 不解决的代价

- 每次 pydantic-ai 升级都需要在 agentpool 中做适配映射
- 新开发者需要同时理解两套 loop 语义
- AgentPool 的"重"使其难以嵌入其他项目

## Goals & Non-Goals

### Goals

1. **NativeAgent 成为 pydantic-ai Agent 的持久持有者**，而非临时构造器
2. **BaseAgent 剥离 turn 内部 loop**，仅保留跨 turn 的 session/queuing 逻辑
3. **Tool 体系复用 pydantic-ai 原生实现**，保留 agentpool 特有的 MCP bridge 和 ToolResult enrichment
4. **Event 体系映射到 pydantic-ai 原生事件**，在 SessionPool/Protocol 层做 enrichment
5. **MessageHistory 复用 pydantic-ai 的 `ModelMessage`**，保留 persistence/compaction 层

### Non-Goals

1. **不动 ACPAgent / ClaudeCodeAgent / CodexAgent / AGUIAgent**：这些 agent 没有 LLM loop，不涉及 pydantic-ai 迁移
2. **不动 SessionPool / Protocol Handlers**：这些层不直接调用 LLM
3. **不动 YAML Config / AgentPool Registry**：配置层是 agentpool 的核心价值
4. **不引入 pydantic-graph 重写 Team/TeamRun**：本 RFC 仅聚焦 NativeAgent
5. **不追求 100% API 兼容**：允许 Breaking Changes，但需有迁移指南

## Evaluation Criteria

| Criterion | Weight | Description |
|---|---|---|
| **Code Reduction** | High | NativeAgent + BaseAgent 代码行数减少目标 ≥ 50% |
| **Maintainability** | High | 移除自建 loop 后的测试复杂度、debug 难度 |
| **Feature Parity** | High | 现有功能（streaming、injection、queuing、tools、history、hooks）不丢失 |
| **Pydantic-AI Alignment** | High | 能自动继承 pydantic-ai 新特性（capabilities、graph、evals） |
| **Breaking Impact** | Medium | 对外部用户 API 的影响范围、迁移成本 |
| **Implementation Risk** | Medium | 重构期间的功能回归风险、测试覆盖要求 |

## Options Analysis

### Option 1: 保守适配 — 保持 BaseAgent，NativeAgent 持有 PydanticAgent 实例

**Description**：
- `BaseAgent` 保持不变，仍提供 `run_stream()` while loop + injection manager
- `NativeAgent.__init__()` 中直接构造 `PydanticAgent` 并持久持有
- `NativeAgent._stream_events()` 调用 `self._pydantic_agent.iter()`，但仍在 background task 中运行，通过 queue 中转事件
- 事件转换：pydantic-ai `AgentStreamEvent` → agentpool `RichAgentStreamEvent`

**Advantages**：
- 改动范围最小，主要集中在 `native_agent/agent.py`
- BaseAgent 的 queuing/injection 语义保持不变，上层（SessionPool/Protocol）无感知
- 风险低，可渐进式验证

**Disadvantages**：
- 仍存在双重 loop（BaseAgent while + pydantic-ai graph），复杂度未根本降低
- BaseAgent 的 1000+ 行代码无法删除
- 无法使用 pydantic-ai 的 `RunContext.enqueue()` 替代 injection manager

**Evaluation**：
| Criterion | Score | Notes |
|---|---|---|
| Code Reduction | 2/5 | 仅 NativeAgent 减少，BaseAgent 不变 |
| Maintainability | 2/5 | 双重 loop 仍在 |
| Feature Parity | 5/5 | 无功能损失 |
| Pydantic-AI Alignment | 2/5 | 仍隔离在 wrapper 层 |
| Breaking Impact | 5/5 | 无 Breaking Change |
| Implementation Risk | 4/5 | 低风险 |

**Effort Estimate**：1-2 周，1 人

---

### Option 2: 激进重构 — BaseAgent 拆分为 BaseNode + TurnController，NativeAgent 完全 delegate 给 pydantic-ai

**Description**：
- 将 `BaseAgent` 拆分为两个角色：
  - **`BaseNode`**：保留 MessageNode 接口（`process()`、`connections`、`signals`），但移除 `run_stream()` while loop
  - **`TurnController`**（或并入 `SessionPool.TurnRunner`）：接管 queuing/injection/auto-resume
- `NativeAgent` 直接继承 `BaseNode`，内部持有 `PydanticAgent`
- `NativeAgent.run()` = 调用 `self._pydantic_agent.run_sync()` 或 `run()`
- `NativeAgent.run_stream()` = 调用 `self._pydantic_agent.iter()`，直接 yield pydantic-ai events（或做 thin mapping）
- **Injection/Queuing**：从 NativeAgent 层移除，完全由 SessionPool/TurnRunner 处理。Tool 中的 `ctx.agent.inject_prompt()` 改为 `ctx.run_context.enqueue()`（pydantic-ai native）或由 SessionPool 调度

**Advantages**：
- 根本消除双重 loop，架构清晰
- BaseAgent 代码可减少 60%+
- 完全对齐 pydantic-ai 语义，自动继承新特性
- 可使用 pydantic-ai `Agent.iter()` 的细粒度控制（逐 node 观察）

**Disadvantages**：
- **Breaking Change**：`BaseAgent.run_stream()` 的 while loop 被移除，影响所有子类（ACPAgent 等需要适配）
- **Injection 语义变化**：`agent.inject_prompt()` 不再可用（或需要 SessionPool 层模拟），影响现有 tool 实现
- **Event 体系变化**：`RichAgentStreamEvent` 需要重新设计为 pydantic-ai events 的 enrichment
- 测试重写工作量大

**Evaluation**：
| Criterion | Score | Notes |
|---|---|---|
| Code Reduction | 5/5 | BaseAgent + NativeAgent 大幅精简 |
| Maintainability | 5/5 | 单一层级 loop |
| Feature Parity | 3/5 | injection/queuing 语义需重新设计 |
| Pydantic-AI Alignment | 5/5 | 完全对齐 |
| Breaking Impact | 2/5 | 影响所有 Agent 子类和 tool 实现 |
| Implementation Risk | 2/5 | 高风险，需充分测试 |

**Effort Estimate**：4-6 周，1-2 人

---

### Option 3: 混合方案 — BaseAgent 保留接口但内部 delegate loop 到 pydantic-ai

**Description**：
- `BaseAgent` 保留 `run_stream()` 接口签名，但内部不再自建 while loop
- `BaseAgent.run_stream()` 改为调用 `_run_single_turn()`（由子类实现）
- `NativeAgent._run_single_turn()` = 调用 `self._pydantic_agent.iter()`
- `BaseAgent` 的 queuing/injection 保留，但实现改为：
  - `queue_prompt()` → 将 prompt 加入 SessionPool 队列（或 pydantic-ai `RunContext.enqueue()`）
  - `inject_prompt()` → 同上，或标记为 deprecated，推荐 SessionPool 调度
- 非 NativeAgent 子类（ACPAgent）继续用自己的 `_run_single_turn()` 实现

**Advantages**：
- 保留 `BaseAgent.run_stream()` 接口，子类无需立即适配
- NativeAgent 获得 pydantic-ai 完整 loop，其他 agent 不受影响
- 可以渐进式移除 BaseAgent 的 loop 逻辑

**Disadvantages**：
- BaseAgent 仍需维护（虽然 loop 逻辑可简化）
- `run_stream()` 的 while loop 语义与 pydantic-ai `iter()` 的语义不完全一致，存在认知负担
- 不是根本解决，是过渡方案

**Evaluation**：
| Criterion | Score | Notes |
|---|---|---|
| Code Reduction | 3/5 | BaseAgent 简化但保留 |
| Maintainability | 3/5 | 仍有接口层差异 |
| Feature Parity | 4/5 | 大部分保留，injection 需调整 |
| Pydantic-AI Alignment | 4/5 | NativeAgent 完全对齐 |
| Breaking Impact | 3/5 | 接口保留，内部实现变 |
| Implementation Risk | 3/5 | 中等风险 |

**Effort Estimate**：2-3 周，1 人

## Recommendation

**推荐 Option 2（激进重构）**，但采用分阶段实施以降低风险。

### 推荐理由

1. **根本解决问题**：Option 1 和 Option 3 都是"在现有架构上打补丁"，无法消除双重 loop 的根本矛盾。agentpool 的核心价值在 SessionPool/Protocol/Registry 层，不在 agent loop 层。

2. **长期维护成本**：pydantic-ai 是活跃维护的框架（Pydantic 团队），其 agent loop 的可靠性、测试覆盖、新特性演进远超 agentpool 自建实现。delegate 后维护成本显著降低。

3. **与 SessionPool 架构一致**：`ARCHITECTURE-ORCHESTRATOR.md` 设计文档已经明确"Turn = 单次 LLM 调用"，SessionPool 负责 turn 编排。将 turn 内部 loop 下沉到 pydantic-ai 与该设计完全一致。

4. **接受的风险可控**：Breaking Changes 主要影响内部 tool 实现和子类适配，外部用户（YAML 配置 + Protocol）感知有限。

### 接受的风险

- **Injection 语义变化**：现有 tool 中 `ctx.agent.inject_prompt()` 需要改为 `ctx.run_context.enqueue()` 或 SessionPool API。需编写迁移指南。
- **Event 适配成本**：`RichAgentStreamEvent` 体系需要重写为 pydantic-ai events 的 enrichment 层。
- **测试重写**：NativeAgent 的测试需要大量重写，但可复用 pydantic-ai 的 `TestModel`。

## Technical Design

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│  Protocol Handlers (ACP / OpenCode / AG-UI / MCP / API)     │
│  → 通过 EventBus 消费事件，不受 NativeAgent 内部变化影响      │
├─────────────────────────────────────────────────────────────┤
│  SessionPool (TurnRunner + EventBus + SessionController)    │
│  → TurnRunner 直接调用 agent.run() / agent.iter()           │
│  → 不再依赖 agent._run_stream_once()                        │
├─────────────────────────────────────────────────────────────┤
│  AgentPool Registry (YAML Manifest + 异构 Agent 管理)        │
├─────────────────────────────────────────────────────────────┤
│  BaseNode (原 BaseAgent 精简)                                │
│  ├── process() / run() / run_stream() — 纯接口               │
│  ├── connections / signals — 保留                            │
│  └── conversation — 可简化为 thin wrapper                    │
├─────────────────────────────────────────────────────────────┤
│  NativeAgent[TDeps, OutputDataT]                            │
│  ├── _pydantic_agent: PydanticAgent[TDeps, OutputDataT]     │
│  │   (init 时构造，持久持有)                                  │
│  ├── run() → self._pydantic_agent.run_sync()                │
│  ├── run_stream() → self._pydantic_agent.iter() → map events│
│  └── tools → 注册到 PydanticAgent（支持 prepare/override）  │
├─────────────────────────────────────────────────────────────┤
│  pydantic-ai Agent                                          │
│  └── Agent.iter() → AgentRun → Graph Loop                   │
│      (UserPromptNode → ModelRequestNode → CallToolsNode)    │
│  └── ToolManager / FunctionToolset / output validators      │
│  └── RunContext / GraphRunContext / message_history         │
└─────────────────────────────────────────────────────────────┘
```

### NativeAgent 新设计

```python
class NativeAgent[TDeps = None, TResult = str](BaseNode[TDeps, TResult]):
    """AgentPool 原生 agent，基于 pydantic-ai Agent 构建。

    职责：
    1. 将 YAML 配置转换为 PydanticAgent 配置
    2. 管理 tools（含 MCP bridge、agent-as-tool）
    3. 将 pydantic-ai events 映射为 agentpool events
    4. 管理 conversation persistence（在 pydantic-ai message_history 之上）
    """

    def __init__(
        self,
        *,
        name: str,
        model: str | Model,
        system_prompt: str | Sequence[str] = (),
        instructions: Sequence[str | Callable] = (),
        tools: Sequence[Tool | Callable] = (),
        toolsets: Sequence[AbstractToolset] = (),
        output_type: type[TResult] = str,
        deps_type: type[TDeps] = type(None),
        model_settings: ModelSettings | None = None,
        retries: int | AgentRetries | None = None,
        end_strategy: EndStrategy = "early",
        capabilities: Sequence[AgentCapability] = (),
        # agentpool 特有
        agent_pool: AgentPool | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] = (),
        hooks: AgentHooks | None = None,
        storage: StorageManager | None = None,
    ) -> None:
        # 构建 pydantic-ai Agent（持久持有，非临时）
        self._pydantic_agent = PydanticAgent(
            model=model,
            name=name,
            system_prompt=system_prompt,
            instructions=instructions,
            tools=tools,
            toolsets=toolsets,
            output_type=output_type,
            deps_type=deps_type,
            model_settings=model_settings,
            retries=retries,
            end_strategy=end_strategy,
            capabilities=capabilities,
        )
        # agentpool 特有层
        self.agent_pool = agent_pool
        self.hooks = hooks
        self._storage = storage
        # conversation persistence（在 pydantic-ai history 之上）
        self._conversation_persistence = ConversationPersistence(storage)

    async def run(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_history: Sequence[ModelMessage] | None = None,
        deps: TDeps | None = None,
        **kwargs: Any,
    ) -> ChatMessage[TResult]:
        """Run agent and return final message."""
        # 从持久化加载历史
        history = message_history or await self._load_persisted_history()
        # 直接委托给 pydantic-ai
        result = await self._pydantic_agent.run(
            prompts,
            message_history=history,
            deps=self._build_run_context(deps),
        )
        # 持久化新消息
        if store_history:
            await self._persist_messages(result.all_messages())
        return self._to_chat_message(result)

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_history: Sequence[ModelMessage] | None = None,
        deps: TDeps | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Run agent with streaming events."""
        history = message_history or await self._load_persisted_history()
        async with self._pydantic_agent.iter(
            prompts,
            message_history=history,
            deps=self._build_run_context(deps),
        ) as agent_run:
            async for event in self._enrich_events(agent_run, session_id=self.session_id):
                yield event
        if store_history:
            await self._persist_messages(agent_run.result.all_messages())

    def _enrich_events(
        self,
        agent_run: AgentRun,
        session_id: str | None = None,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """将 pydantic-ai events 映射为 agentpool events，添加 session/agent 上下文。"""
        # pydantic-ai AgentStreamEvent → agentpool RichAgentStreamEvent
        # 添加：session_id、agent_name、cost_info、tool_call_id mapping
        ...

    async def _build_run_context(self, user_deps: TDeps | None) -> RunContext[TDeps]:
        """构建 pydantic-ai RunContext，注入 agentpool 特有依赖。"""
        # 包含：AgentPool、InputProvider、internal_fs、hooks
        ...
```

### Event 映射层

| pydantic-ai Event | agentpool RichAgentStreamEvent | Enrichment |
|---|---|---|
| `PartStartEvent` | `PartStartEvent` | + agent_name, session_id |
| `PartDeltaEvent` | `PartDeltaEvent` | + agent_name, session_id |
| `PartEndEvent` | `PartEndEvent` | + agent_name, session_id |
| `FunctionToolCallEvent` | `ToolCallStartEvent` | + agent_name, session_id, tool_call_id |
| `FunctionToolResultEvent` | `ToolCallCompleteEvent` | + agent_name, session_id, metadata |
| `FinalResultEvent` | `StreamCompleteEvent` | + agent_name, session_id, cost_info, ChatMessage |
| `ModelResponse` (node) | `ModelResponseEvent` | + model_name, provider_name, usage |
| `RunUsage` | — | 累积到 ChatMessage.cost_info |

### Tool 适配

```python
# agentpool Tool → pydantic-ai Tool
class AgentpoolToolAdapter:
    """将 agentpool 的 Tool/FunctionTool 适配为 pydantic-ai Tool。

    保留 agentpool 特有功能：
    - schema_override（schemez）
    - requires_confirmation（通过 pydantic-ai ApprovalRequiredToolset）
    - ToolResult enrichment（content + structured_content + metadata）
    - MCP bridge（通过 pydantic-ai MCPServerTool / ExternalToolset）
    """

    @staticmethod
    def to_pydantic_ai(tool: Tool, context: AgentContext) -> PydanticTool:
        # 1. 提取 callable
        fn = tool.get_callable()
        # 2. 应用 schema_override（schemez）
        schema = tool.schema_override or infer_schema(fn)
        # 3. 包装为 pydantic-ai Tool
        pydantic_tool = PydanticTool(
            fn,
            name=tool.name,
            description=tool.description,
            # prepare 函数处理 schema_override
            prepare=tool._get_effective_prepare(),
        )
        # 4. confirmation 通过 ApprovalRequiredToolset 处理
        if tool.requires_confirmation:
            pydantic_tool = ApprovalRequiredToolset([pydantic_tool])
        return pydantic_tool
```

### MessageHistory 适配

```python
class ConversationPersistence:
    """在 pydantic-ai message_history 之上提供持久化和 compaction。

    pydantic-ai 负责：
    - 运行时 message_history: list[ModelMessage]
    - HistoryProcessor（pre-run 处理）

    agentpool 负责：
    - 加载/保存到 StorageManager（SQL）
    - Compaction/summarization（跨 session）
    - format_history()（用于非 LLM 消费）
    """

    async def load(self, session_id: str) -> list[ModelMessage]:
        # 从 SQL 加载 ChatMessage，转换为 ModelMessage
        ...

    async def save(self, session_id: str, messages: list[ModelMessage]) -> None:
        # 将 ModelMessage 转换为 ChatMessage，保存到 SQL
        ...

    async def compact(self, session_id: str, max_tokens: int) -> list[ModelMessage]:
        # 调用 compaction 策略，返回压缩后的 history
        ...
```

## Implementation Plan

### Phase 1: 基础设施准备（1 周）

1. **Event 映射层**
   - 实现 `_enrich_events()`：pydantic-ai `AgentStreamEvent` → `RichAgentStreamEvent`
   - 确保所有现有 event types 都有对应映射
   - 编写 event 映射测试

2. **Tool 适配层**
   - 实现 `AgentpoolToolAdapter.to_pydantic_ai()`
   - 验证 schema_override、prepare、confirmation 功能
   - MCP bridge 验证

3. **MessageHistory 适配层**
   - 实现 `ConversationPersistence`
   - `ChatMessage` ↔ `ModelMessage` 双向转换
   - Compaction 逻辑迁移

### Phase 2: NativeAgent 重构（1-2 周）

1. **新 NativeAgent 实现**
   - 基于 `BaseNode`（精简后的基类）
   - 持久持有 `PydanticAgent`
   - 实现 `run()` / `run_stream()` / `run_iter()`

2. **BaseAgent 精简**
   - 移除 while loop（移至 SessionPool/TurnRunner）
   - 移除 injection manager（使用 pydantic-ai `RunContext.enqueue()` 或 SessionPool 队列）
   - 保留：signals、connections、hooks interface

3. **测试覆盖**
   - 使用 pydantic-ai `TestModel` 编写单元测试
   - 集成测试：streaming、tool calling、output validation、history
   - 回归测试：现有 YAML config 加载运行

### Phase 3: 子类适配（1 周）

1. **ACPAgent 适配**
   - ACPAgent 不依赖 pydantic-ai，但需要适配精简后的 BaseNode 接口
   - 验证 `_stream_events()` 接口变化

2. **其他子类**
   - ClaudeCodeAgent、CodexAgent、AGUIAgent 验证

### Phase 4: SessionPool 集成（1 周）

1. **TurnRunner 适配**
   - 调用 `agent.run()` / `agent.iter()` 替代 `_run_stream_once()`
   - auto-resume 逻辑验证

2. **EventBus 适配**
   - 消费 pydantic-ai 原生 events（经 enrichment）

### Phase 5: 文档与迁移（1 周）

1. **API 文档更新**
2. **迁移指南**
   - `agent.inject_prompt()` → `RunContext.enqueue()`
   - `agent.queue_prompt()` → SessionPool API
   - Event handler 类型变化
3. **内部工具迁移审查**

### Rollback Strategy

- 每个 Phase 都有独立分支
- Phase 2-3 期间保留旧 NativeAgent 为 `NativeAgentLegacy`，通过 feature flag 切换
- 全部验证通过后删除 Legacy

## Open Questions

1. **pydantic-ai 版本**：当前 agentpool 依赖 PyPI 版 `pydantic-ai-slim>=1.0.0`，是否改为 editable 依赖本地 `packages/pydantic-ai/`？
2. **`AgentContext` 如何处理**：pydantic-ai 的 `RunContext` 类型参数是 `deps_type`，agentpool 的 `AgentContext` 包含 pool、input_provider、fs 等。如何在不破坏类型安全的前提下注入？
3. **Hooks 体系**：`AgentHooks` 的 pre_run/post_run hooks 如何在 pydantic-ai 的 capability/hook 体系中实现？是否需要实现 custom capability？
4. **Session ID / Conversation ID**：pydantic-ai 的 `conversation_id` 是 `uuid7`，agentpool 使用字符串 session_id。映射策略？
5. **`to_structured()` 运行时突变**：现有代码允许运行时改变 output_type。 pydantic-ai 是否支持？如果不支持，替代方案？

## Decision Record

| Field | Value |
|---|---|
| **Decision** | 采用 Option 2（激进重构），分 5 个 Phase 实施 |
| **Date** | 2026-06-01 |
| **Approver** | 待确定 |
| **Key Discussion Points** | 1. Breaking Change 的接受程度；2. Injection 语义迁移成本；3. 与 pydantic-ai 版本绑定策略 |
| **Conditions** | 1. 本地 `packages/pydantic-ai/` 保持活跃同步；2. 每个 Phase 有独立 rollback 能力；3. 现有 YAML config 100% 兼容 |

---

## Appendix A: 代码行数估算

| 模块 | 当前行数 | 目标行数 | 减少比例 |
|---|---|---|---|
| `agents/base_agent.py` | ~1224 | ~400 | 67% |
| `agents/native_agent/agent.py` | ~1341 | ~500 | 63% |
| `tools/base.py` | ~787 | ~300 | 62% |
| `messaging/message_history.py` | ~347 | ~150 | 57% |
| **合计** | **~3700** | **~1350** | **63%** |

## Appendix B: 相关文件清单

### 需要重构的文件
- `src/agentpool/agents/base_agent.py` — 精简 while loop、injection manager
- `src/agentpool/agents/native_agent/agent.py` — 改为持有 PydanticAgent
- `src/agentpool/agents/native_agent/tool_wrapping.py` — 适配 pydantic-ai Tool
- `src/agentpool/tools/base.py` — 简化，delegate schema 到 pydantic-ai
- `src/agentpool/messaging/message_history.py` — 改为 persistence 层
- `src/agentpool/agents/events/` — 添加 event mapping 层

### 需要适配的文件
- `src/agentpool/orchestrator/core.py` — TurnRunner 调用新接口
- `src/agentpool/agents/acp_agent/acp_agent.py` — 适配精简后的基类
- `src/agentpool/agents/claude_code_agent/` — 验证兼容性
- `src/agentpool/agents/codex_agent/` — 验证兼容性

### 不需要改动的文件
- `src/agentpool/delegation/pool.py` — Registry 层不变
- `src/agentpool_server/` — Protocol 层不变
- `src/agentpool/models/manifest.py` — YAML schema 不变
- `src/agentpool/sessions/` — Session  persistence 不变
