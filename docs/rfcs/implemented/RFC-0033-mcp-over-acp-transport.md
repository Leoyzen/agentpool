---
rfc_id: RFC-0033
title: "MCP-over-ACP: Support MCP Servers via ACP Channel Transport"
status: DRAFT
author: yuchen.liu
reviewers:
  - name: TBD
    status: pending
created: 2026-05-26
last_updated: 2026-05-26
decision_date:
related_prds: []
related_rfcs:
  - RFC-0030-acp-streamable-http-websocket-transport.md
---

# RFC-0033: MCP-over-ACP: Support MCP Servers via ACP Channel Transport

## Overview

本 RFC 提议在 agentpool 中增加对 MCP-over-ACP 传输协议的支持，允许 ACP 客户端通过现有 ACP 连接注入 MCP 工具服务，而无需单独的 stdio 进程或 HTTP 端口。实现后，agentpool 可作为 ACP Agent 向客户端声明 `mcpCapabilities.acp: true`，并通过 `mcp/connect`、`mcp/message`、`mcp/disconnect` 三类消息完成完整的 MCP 工具调用生命周期。

该特性能够显著扩大 agentpool 的适用场景：客户端可注入项目感知工具（如代码感知的搜索工具）、沙箱环境（如 WASM 运行时）也能提供 MCP 工具，同时消除了通过"旁路"传输带来的沙箱逃逸风险。

## Table of Contents

- [Background & Context](#background--context)
- [Problem Statement](#problem-statement)
- [Goals & Non-Goals](#goals--non-goals)
- [Evaluation Criteria](#evaluation-criteria)
- [Options Analysis](#options-analysis)
- [Recommendation](#recommendation)
- [Technical Design](#technical-design)
- [Security Considerations](#security-considerations)
- [Implementation Plan](#implementation-plan)
- [Open Questions](#open-questions)
- [Decision Record](#decision-record)
- [References](#references)

---

## Background & Context

### Current State

agentpool 当前通过 ACP 协议对外提供 Agent 能力。在 `session/new` 和 `session/load` 请求中，客户端可传入 MCP 服务器配置（`mcp_servers`），agentpool 支持三种 MCP 传输类型：

| 类型 | 实现文件 | 状态 |
|------|----------|------|
| stdio | `StdioMcpServer` / `StdioMCPServerConfig` | ✅ 已支持 |
| SSE (HTTP) | `SseMcpServer` / `SSEMCPServerConfig` | ✅ 已支持 |
| Streamable HTTP | `HttpMcpServer` / `StreamableHTTPMCPServerConfig` | ✅ 已支持 |
| **ACP Channel** | — | ❌ 未支持 |

`McpCapabilities` 中只有 `http` 和 `sse` 两个字段，`InitializeResponse` 中也未声明 `acp` 能力。`AgentMethod`/`ClientMethod` 枚举中不包含 `mcp/connect`、`mcp/disconnect`、`mcp/message`。

### Historical Context

- [RFC-0030](../draft/RFC-0030-acp-streamable-http-websocket-transport.md) 扩展了 agentpool 对 Streamable HTTP / WebSocket 的 ACP 传输层支持，为本 RFC 奠定了传输基础设施基础。
- ACP 协议官方 RFD [mcp-over-acp](../../../../agent-client-protocol/docs/rfds/mcp-over-acp.mdx) 定义了完整规范，包括消息格式、连接复用和 Bridging 策略。

### Prerequisites

在实现本 RFC 之前，需先修复以下前置条件：

- **`StdioMcpServer.type` 字段缺失**：`acp/schema/mcp.py` 中 `StdioMcpServer` 的 `type: Literal["stdio"]` 被注释掉（第78-79行）。`McpServer` 作为 discriminated union 使用时，所有 variant 必须提供 `type` discriminator。需在引入 `AcpMcpServer` 之前先恢复该字段。

### Glossary

| Term | Definition |
|------|------------|
| ACP | Agent Client Protocol，agentpool 使用的 Agent-客户端双向通信协议 |
| MCP | Model Context Protocol，LLM 使用工具的标准协议 |
| MCP-over-ACP | 通过 ACP 连接通道传输 MCP 消息的扩展 |
| `acpId` | 客户端在 `session/new` 中为 ACP-transport MCP Server 生成的唯一标识 |
| `connectionId` | Agent 响应 `mcp/connect` 后返回的连接实例 ID，支持多路复用 |
| Bridging | 将 ACP-transport MCP Server 透明转换为 stdio/HTTP shim 的中间层，用于兼容不支持 ACP transport 的 Agent |

---

## Problem Statement

### The Problem

ACP 客户端（IDE、编辑器、代理中间层）希望向 Agent 注入与当前会话上下文紧密相关的 MCP 工具（如项目感知的代码搜索、本地文件访问、沙箱内工具），但目前只能通过"旁路"方式实现：

1. 在客户端侧启动一个独立 stdio 进程
2. 或开放一个本地 HTTP 端口

这两种方式都存在明显局限：

- **沙箱环境不可用**：WASM 运行时或容器内的客户端无法启动进程或绑定端口
- **架构不透明**：工具通信绕过了 ACP 会话层，无法通过标准 ACP 协议栈进行审计、代理或路由
- **运维复杂**：需要额外管理进程生命周期和端口分配

### Evidence

- ACP 官方 RFD [mcp-over-acp](../../../../agent-client-protocol/docs/rfds/mcp-over-acp.mdx) 明确指出该问题并提出了规范解决方案
- agentpool 的 `convert_acp_mcp_server_to_config()` 在遇到未知 MCP server 类型时直接 `assert_never`（即崩溃），无任何扩展入口
- `McpCapabilities` 中无 `acp` 字段，无法向客户端声明支持能力

### Impact of Inaction

- **功能缺失**：无法支持 ACP 客户端的 MCP 工具注入场景（如 Zed 插件、WASM 沙箱工具）
- **生态兼容性**：ACP 参考实现（Rust SDK sacp-conductor）已实现 Bridging，agentpool 无法与其互操作
- **扩展受阻**：未来的 proxy-chain 场景（RFC 参见 [proxy-chains RFD](../../../../agent-client-protocol/docs/rfds/)）依赖 MCP-over-ACP

---

## Goals & Non-Goals

### Goals (In Scope)

1. agentpool 作为 ACP Agent 在 `InitializeResponse` 中声明 `mcpCapabilities.acp: true`
2. 支持在 `session/new`、`session/load`、`session/fork`、`session/resume` 请求中接收 `type: "acp"` 的 MCP Server 声明
3. 实现 `mcp/connect` 消息处理：在 session 初始化阶段接收 `acpId`，返回唯一 `connectionId`
4. 实现双向 `mcp/message` 消息转发：将 Agent 发出的 MCP 工具调用路由至提供该 Server 的 ACP 客户端，并将结果原路返回
5. 实现 `mcp/disconnect` 消息处理：在 session 结束时清理连接状态
6. 支持同一 MCP Server 的多路连接复用（每次 `mcp/connect` 返回独立 `connectionId`）
7. **双向配置转换**：同步更新正向转换（`converters.py`）和反向转换（`acp_converters.py`），确保 agentpool 作为 ACP 客户端时代码不崩溃

### Non-Goals (Out of Scope)

1. **Bridging（可选，后续 RFC）**：将 ACP-transport MCP Server 透明转为 stdio/HTTP shim 不在本 RFC 范围内
2. **客户端侧实现**：本 RFC 仅覆盖 agentpool（Agent 侧）实现，不涉及客户端 SDK 变更
3. **MCP Server 能力缓存**：不在本 RFC 中实现跨连接的工具列表缓存
4. **认证与鉴权扩展**：MCP-over-ACP 沿用与 ACP 相同的信任模型，不新增额外认证机制

### Success Criteria

- [ ] `InitializeResponse` 包含 `mcpCapabilities.acp: true`
- [ ] 客户端传入 `type: "acp"` MCP Server 后，agentpool 成功建立 ACP MCP 连接（`mcp/connect` 返回 `connectionId`）
- [ ] Agent 可通过 `connectionId` 发起 `mcp/message` 工具调用并收到结果
- [ ] `mcp/disconnect` 正确清理连接
- [ ] 现有 stdio/SSE/HTTP MCP 功能无回归（通过已有测试集验证）
- [ ] 新增 `AcpMcpServer` / `AcpMCPServerConfig` 后，所有 `assert_never` 穷尽匹配通过（无运行时崩溃）

---

## Evaluation Criteria

| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| 协议合规性 | 高 | 与 ACP 官方 RFD mcp-over-acp 规范的符合程度 | 必须完全符合消息格式规范 |
| 向后兼容性 | 高 | 不破坏现有 stdio/SSE/HTTP MCP 功能 | 现有测试全部通过 |
| 实现复杂度 | 中 | 代码改动范围和引入的技术风险 | - |
| 可扩展性 | 中 | 是否为后续 Bridging 和 proxy-chain 留有扩展点 | - |
| 可测试性 | 中 | 是否可通过单元/集成测试覆盖核心路径 | 核心路径覆盖率 ≥ 80% |

---

## Options Analysis

### Option 1: 原生实现（Native ACP MCP Handler）

**Description**

在 agentpool 的 ACP server 层直接实现完整的 MCP-over-ACP 协议处理器：

- 扩展 `McpCapabilities` schema 添加 `acp` 字段
- 新增 `AcpMcpServer` schema 类型
- 在 `AgentMethod`/`ClientMethod` 枚举中注册三个新 method
- 实现 `AcpMcpConnectionManager`：维护 `acpId → connectionId → ACP client` 的映射
- 在 `acp_agent.py` 的 handler 分发中处理 `mcp/connect`、`mcp/disconnect`
- 通过 `client.send_request("mcp/message", ...)` 实现双向转发

**Advantages**

- 完全符合 ACP 官方规范，消息路径透明可审计
- 不需要额外进程或端口，适合沙箱/WASM 场景
- 复用现有 `Connection` JSON-RPC 引擎，架构一致
- 为后续 Bridging 和 proxy-chain 留下扩展点

**Disadvantages**

- 需要修改多个 schema 文件、`acp_agent.py`、`converters.py`、**`acp_converters.py`** 等，改动面较宽
- 需要实现并维护 `connectionId` 多路复用状态管理
- **fastmcp `ClientTransport` 实现复杂度高于预期**：需实现 async 流模拟、独立 JSON-RPC id 空间、超时传播等（详见技术设计）
- 需要新增集成测试覆盖 ACP↔MCP 双向消息流

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| 协议合规性 | ⭐⭐⭐⭐⭐ | 完全按照 RFD 规范实现 |
| 向后兼容性 | ⭐⭐⭐⭐⭐ | 仅添加新字段和新分支，不修改已有路径 |
| 实现复杂度 | ⭐⭐⭐ | 中等偏高，核心逻辑约 600-900 行（含 transport） |
| 可扩展性 | ⭐⭐⭐⭐⭐ | 结构清晰，Bridging 可作为独立模块追加 |
| 可测试性 | ⭐⭐⭐⭐ | 状态机可单独单测，集成测试需 mock ACP client |

**Effort Estimate**

- Complexity: Medium-High
- Resources: 1 人，预计 **1.5-2 周**（含 Transport Research spike + 测试）
- Dependencies: 无外部依赖，需先修复 `StdioMcpServer.type` 前置条件

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `connectionId` 状态泄漏（disconnect 未触发） | Medium | Medium | 绑定 ACP session 生命周期，session 关闭时清理所有连接 |
| `mcp/message` 双向路由逻辑错误 | Low | High | 充分的单元测试 + 集成测试，并参考 Rust SDK 参考实现 |
| 现有 MCP 路径回归 | Low | High | 在 CI 中运行完整 MCP 测试套件 |
| fastmcp `ClientTransport` 接口不兼容 | Medium | High | Pre-Phase 0 先 spike 验证 |
| `assert_never` 穷尽匹配遗漏 | Medium | High | 代码审查 checklist（见附录） |

---

### Option 2: Bridging-first（先实现 Stdio Shim 桥接）

**Description**

不修改 agentpool 核心，而是在 agentpool 外层增加一个 Conductor/Proxy 进程：对外声明支持 `mcpCapabilities.acp`，接收到 ACP-transport MCP Server 声明后，生成一个本地 stdio shim 进程，将 ACP channel 消息转换为 stdio MCP 消息，再传给 agentpool（以普通 stdio MCP Server 形式传入）。

**Advantages**

- agentpool 核心代码改动最小
- shim 进程可独立部署和测试
- 与 ACP RFD 中描述的 Bridging 模式对齐

**Disadvantages**

- 引入额外进程，恰好是本 RFC 要消除的架构问题
- 沙箱/WASM 场景下无法启动 shim 进程，适用范围受限
- 消息路径增加一跳，延迟增加
- 维护两个进程生命周期的复杂度不低于 Option 1

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| 协议合规性 | ⭐⭐⭐ | 从外部看符合规范，但内部绕过了核心目标 |
| 向后兼容性 | ⭐⭐⭐⭐⭐ | 对 agentpool 核心无侵入 |
| 实现复杂度 | ⭐⭐⭐ | shim 进程本身复杂度与 Option 1 相当 |
| 可扩展性 | ⭐⭐ | Bridging 作为唯一实现路径，无原生支持扩展点 |
| 可测试性 | ⭐⭐⭐ | 进程边界使集成测试更复杂 |

**Effort Estimate**

- Complexity: Medium-High（需要额外的进程管理基础设施）
- Resources: 1-2 人，预计 5-8 个工作日
- Dependencies: 需要设计 shim 协议和生命周期管理

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| shim 进程孤儿化（父进程崩溃后 shim 未退出） | Medium | Medium | 使用进程组或心跳机制 |
| 沙箱环境不支持 fork | High | High | 此方案在沙箱场景下根本无法使用 |

---

### Option 3: 延迟（不实现，等待上游）

**Description**

等待 ACP 官方 Rust SDK（sacp-conductor）稳定后，通过集成官方 bridge 实现支持，agentpool 自身不做任何改动。

**Advantages**

- 零开发成本

**Disadvantages**

- 上游稳定时间不可控
- 无法响应现有客户端需求（Zed 等工具的 MCP 注入场景）
- 错失生态先机

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| 协议合规性 | N/A | 不实现 |
| 向后兼容性 | ⭐⭐⭐⭐⭐ | 无变更 |
| 实现复杂度 | ⭐⭐⭐⭐⭐ | 零成本 |
| 可扩展性 | ⭐ | 无法提供扩展能力 |
| 可测试性 | N/A | 不实现 |

---

### Options Comparison Summary

| Criterion | Option 1: 原生实现 | Option 2: Bridging-first | Option 3: 延迟 |
|-----------|---------------------|--------------------------|----------------|
| 协议合规性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | N/A |
| 向后兼容性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 实现复杂度 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 可扩展性 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐ |
| 可测试性 | ⭐⭐⭐⭐ | ⭐⭐⭐ | N/A |
| **综合** | **⭐⭐⭐⭐** | **⭐⭐⭐** | **⭐** |

---

## Recommendation

### Recommended Option

**Option 1: 原生实现（Native ACP MCP Handler）**

### Justification

Option 1 是唯一能够完整实现 ACP RFD 目标的方案：

1. **直接解决根本问题**：无需额外进程，适配沙箱场景
2. **架构一致性**：复用已有 JSON-RPC `Connection` 基础设施，不引入新的架构层
3. **可扩展性最佳**：原生支持后，Bridging（Option 2 的思路）可作为可选的兼容层追加，两者不互斥
4. **改动可控**：核心改动集中在明确定义的接口层（schema、handler dispatch、connection manager），风险可测量

### Accepted Trade-offs

1. **改动文件数量较多**：涉及 `capabilities.py`、`mcp.py`、`messages.py`、`acp_agent.py`、`converters.py`、**`acp_converters.py`** 等，可通过 PR 分层拆解（schema → transport → handler → 集成）降低 review 难度
2. **需要新增状态管理**：`connectionId` 多路复用需要维护连接映射，通过绑定 ACP 连接生命周期保证清理
3. **fastmcp transport 实现复杂度**：`ClientTransport` 需要实现完整的 async 流协议，工作量高于最初预期

### Conditions

- **必须完成 Pre-Phase 0**：实现前需先 spike 验证 `fastmcp.ClientTransport` 的可行性（1-2 天）
- **必须先修复 `StdioMcpServer.type`**：恢复 discriminator 字段，否则 Pydantic union 验证会失败
- **需要补充集成测试**：覆盖 `mcp/connect → mcp/message → mcp/disconnect` 完整生命周期

---

## Technical Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  ACP Client (e.g. Zed, IDE plugin)                              │
│                                                                  │
│  Provides: AcpMcpServer { type: "acp", name: "tools", id: X }  │
│  Handles:  mcp/connect, mcp/message (server-originated)         │
└──────────────────────────┬──────────────────────────────────────┘
                           │ ACP Channel (JSON-RPC over stdio/WS)
┌──────────────────────────▼──────────────────────────────────────┐
│  agentpool ACP Server (AgentPoolACPAgent / acp_agent.py)        │
│                                                                  │
│  ┌─────────────────────────────────────┐                        │
│  │  AcpMcpConnectionManager            │                        │
│  │  (owned by AgentPoolACPAgent,       │                        │
│  │   per-ACP-connection lifecycle)     │                        │
│  │  acpId → { connectionIds[] }        │                        │
│  │  connectionId → AcpMcpConnection    │                        │
│  └──────────────────┬──────────────────┘                        │
│                     │                                            │
│  ┌──────────────────▼──────────────────┐                        │
│  │  AcpMcpTransport                    │                        │
│  │  (implements fastmcp.ClientTransport)│                       │
│  │  Routes MCP JSON-RPC over ACP       │                        │
│  │  mcp/message requests               │                        │
│  └──────────────────┬──────────────────┘                        │
└─────────────────────┼───────────────────────────────────────────┘
                      │ MCP Protocol (tool calls, list_tools, etc.)
┌─────────────────────▼───────────────────────────────────────────┐
│  fastmcp.Client → MCPClient → Agent (LLM)                       │
│  Uses MCP tools transparently                                    │
└─────────────────────────────────────────────────────────────────┘
```

### Message Flow

```
Client                    agentpool                   Agent (LLM)
  │                           │                           │
  │── session/new ───────────▶│                           │
  │   mcp_servers: [{          │                           │
  │     type: "acp",           │                           │
  │     name: "tools",         │                           │
  │     id: "uuid-xxx"         │                           │
  │   }]                       │                           │
  │◀─ session created ─────────│                           │
  │                            │                           │
  │── mcp/connect ────────────▶│  (session init phase)     │
  │   acpId: "uuid-xxx"        │                           │
  │◀─ connectionId: "conn-1" ──│                           │
  │                            │                           │
  │── prompt ─────────────────▶│──────────────────────────▶│
  │                            │                           │
  │◀── mcp/message ────────────│◀──────────────────────────│
  │    connectionId: "conn-1"  │   tools/call              │
  │    method: tools/call      │                           │
  │── result ─────────────────▶│──────────────────────────▶│
  │                            │                           │
  │  ... (multiple tool calls  │  over same connection)    │
  │                            │                           │
  │◀── mcp/disconnect ─────────│◀──────────────────────────│
  │    connectionId: "conn-1"  │   (session end)           │
  │── {} ─────────────────────▶│                          │
```

**关键设计决策**：`mcp/connect` 在 **session 初始化阶段**完成，connection 长期存活至 session 结束，而非 per-tool-call。MCP 协议需要 `initialize` 握手和 `tools/list` 缓存，per-tool-call 连接会带来灾难性性能开销。

### Key Components

#### 1. Schema 层扩展

**`acp/schema/capabilities.py`**

```python
class McpCapabilities(AnnotatedObject):
    http: bool | None = False
    sse: bool | None = False
    acp: bool | None = False  # 新增
    """Agent supports ACP-transport MCP servers."""
```

**`acp/schema/mcp.py`**

```python
class AcpMcpServer(BaseMcpServer):
    """ACP channel transport configuration."""
    type: Literal["acp"] = Field(default="acp", init=False)
    id: str
    """Component-generated unique identifier for routing."""

# 扩展 union — 注意：必须先恢复 StdioMcpServer.type
McpServer = HttpMcpServer | SseMcpServer | StdioMcpServer | AcpMcpServer
```

**`acp/schema/messages.py`**

```python
AgentMethod = Literal[
    ...,
    "mcp/connect",      # 新增
    "mcp/disconnect",   # 新增
]

ClientMethod = Literal[
    ...,
    "mcp/message",      # 新增（双向，以 Agent→Client 方向为主）
]
```

#### 2. AcpMcpConnectionManager

新增 `agentpool_server/acp_server/acp_mcp_manager.py`：

```python
from __future__ import annotations
from typing import TypedDict
from acp.client import Client

class McpJsonRpcRequest(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | int | None
    method: str
    params: dict[str, object] | None

class McpJsonRpcResponse(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | int | None
    result: object | None
    error: dict[str, object] | None

@dataclass
class AcpMcpConnection:
    acp_id: str
    connection_id: str
    # client 引用通过 manager 持有，不在 connection 中重复存储

class AcpMcpConnectionManager:
    """Manages MCP-over-ACP connection lifecycle.
    
    Owned by AgentPoolACPAgent (per-ACP-connection), NOT per-session.
    """

    def __init__(self, client: Client) -> None:
        self._client = client  # ACP client for forwarding mcp/message
        self._by_acp_id: dict[str, list[str]] = {}  # acpId -> connectionIds
        self._connections: dict[str, AcpMcpConnection] = {}  # connectionId -> connection

    def register_server(self, server: AcpMcpServer) -> None:
        """Register server config when session/new receives mcp_servers."""
        ...

    async def connect(self, acp_id: str) -> str:
        """Returns a new connectionId. Validates acpId is registered."""
        ...

    async def send_message(
        self, connection_id: str, request: McpJsonRpcRequest
    ) -> McpJsonRpcResponse:
        """Forward MCP JSON-RPC request to client via ACP mcp/message.
        
        Uses ACP JSON-RPC request/response with independent id space.
        """
        ...

    async def disconnect(self, connection_id: str) -> None:
        """Clean up single connection."""
        ...

    async def cleanup_all(self) -> None:
        """Async cleanup all connections when ACP transport disconnects."""
        ...
```

**设计决策说明**：

- **所有权**：Manager 由 `AgentPoolACPAgent` 持有（per-ACP-connection），因为 `acpId` 注册和 `connectionId` 映射必须存活于多个 session 生命周期
- **类型安全**：`McpJsonRpcRequest`/`Response` 用 `TypedDict` 替代 `Any`，符合代码库 "零 Any" 策略
- **异步清理**：`cleanup_all()` 为 async，因为可能需要发送 `mcp/disconnect` 消息

#### 3. acp_agent.py 集成

在 `initialize()` 中追加 `acp_mcp_servers=True`：

```python
return InitializeResponse.create(
    ...
    http_mcp_servers=True,
    sse_mcp_servers=True,
    acp_mcp_servers=True,  # 新增
    ...
)
```

**Handler 注册**：`AgentPoolACPAgent` 继承自 `acp` 库的 `ACPAgent`，dispatch 由库协议驱动。新 method 的 handler 需通过库支持的方式注册（如方法名约定或 `ext_method` 扩展点）。具体机制需在 Pre-Phase 0 中确认。

```python
# 预期的 handler 实现（具体 dispatch 机制依赖 acp 库协议）
async def _handle_mcp_connect(self, params: dict[str, object]) -> dict[str, object]:
    acp_id = params.get("acpId")
    if not isinstance(acp_id, str):
        raise AcpError(code=INVALID_PARAMS, message="acpId must be a string")
    connection_id = await self._mcp_manager.connect(acp_id)
    return {"connectionId": connection_id}

async def _handle_mcp_disconnect(self, params: dict[str, object]) -> dict[str, object]:
    connection_id = params.get("connectionId")
    if not isinstance(connection_id, str):
        raise AcpError(code=INVALID_PARAMS, message="connectionId must be a string")
    await self._mcp_manager.disconnect(connection_id)
    return {}
```

#### 4. fastmcp ClientTransport 实现

**新增 `agentpool_server/acp_server/acp_mcp_transport.py`**：

```python
from fastmcp.client.transports import ClientTransport

class AcpMcpTransport(ClientTransport):
    """fastmcp ClientTransport that routes MCP messages over ACP channel.
    
    Implements the async stream protocol by wrapping ACP mcp/message
    JSON-RPC requests. Maintains independent MCP JSON-RPC id space.
    """

    def __init__(self, connection_id: str, manager: AcpMcpConnectionManager) -> None:
        self._connection_id = connection_id
        self._manager = manager
        self._next_id = 0

    async def connect(self) -> tuple[MemoryObjectReceiveStream, MemoryObjectSendStream]:
        """Return async streams backed by ACP mcp/message."""
        ...

    async def send(self, message: JSONRPCMessage) -> None:
        """Serialize MCP message and send via ACP mcp/message."""
        ...

    async def receive(self) -> JSONRPCMessage:
        """Receive MCP message from ACP mcp/message response."""
        ...

    async def close(self) -> None:
        """Close transport and notify manager."""
        ...
```

**关键实现挑战**：

- MCP 的 JSON-RPC 消息需要被序列化后作为 ACP `mcp/message` 的 payload 发送
- ACP JSON-RPC 的 `id` 与 MCP JSON-RPC 的 `id` 属于两个独立命名空间
- Transport 需处理 `request → response` 的配对（通过 MCP id 匹配）
- 需支持 notification（无 response）和 bidirectional message（client→agent 的 notification）

#### 5. converters.py 扩展（正向）

```python
def convert_acp_mcp_server_to_config(acp_server: McpServer) -> MCPServerConfig:
    match acp_server:
        case StdioMcpServer() as server:
            return StdioMCPServerConfig(name=server.name, command=server.command)
        case SseMcpServer() as server:
            return SSEMCPServerConfig(name=server.name, url=server.url)
        case HttpMcpServer() as server:
            return StreamableHTTPMCPServerConfig(name=server.name, url=server.url)
        case AcpMcpServer() as server:   # 新增
            return AcpMCPServerConfig(name=server.name, acp_id=server.id)
        case _ as unreachable:
            assert_never(unreachable)
```

#### 6. acp_converters.py 扩展（反向）

```python
def mcp_config_to_acp(config: MCPServerConfig) -> McpServer:
    match config:
        case StdioMCPServerConfig() as cfg:
            return StdioMcpServer(name=cfg.name, command=cfg.command)
        case SSEMCPServerConfig() as cfg:
            return SseMcpServer(name=cfg.name, url=cfg.url)
        case StreamableHTTPMCPServerConfig() as cfg:
            return HttpMcpServer(name=cfg.name, url=cfg.url)
        case AcpMCPServerConfig() as cfg:   # 新增
            return AcpMcpServer(name=cfg.name, id=cfg.acp_id)
        case _ as unreachable:
            assert_never(unreachable)
```

#### 7. AcpMCPServerConfig（新增）

在 `agentpool_config/mcp_server.py` 中新增：

```python
class AcpMCPServerConfig(MCPServerConfig):
    """MCP server config for ACP-channel transport."""
    type: Literal["acp"] = "acp"
    acp_id: str
    timeout: float = 30.0  # mcp/message 超时（秒）
```

并更新 `MCPServerConfig` union：

```python
MCPServerConfig = Annotated[
    StdioMCPServerConfig | SSEMCPServerConfig | StreamableHTTPMCPServerConfig | AcpMCPServerConfig,
    Field(discriminator="type"),
]
```

#### 8. MCPResourceProvider 扩展

在 `agentpool/mcp_server/provider.py` 中更新 `transport_type`：

```python
@property
def transport_type(self) -> Literal["stdio", "sse", "http", "acp"]:
    match self.client.config:
        case StdioMCPServerConfig(): return "stdio"
        case StreamableHTTPMCPServerConfig(): return "http"
        case SSEMCPServerConfig(): return "sse"
        case AcpMCPServerConfig(): return "acp"  # 新增
        case _ as unreachable:
            assert_never(unreachable)
```

并更新 `MCPClient._get_client()` 以支持 `AcpMCPServerConfig`：

```python
match config:
    case StdioMCPServerConfig(...): ...
    case SSEMCPServerConfig(...): ...
    case StreamableHTTPMCPServerConfig(...): ...
    case AcpMCPServerConfig(acp_id=acp_id):   # 新增
        transport = AcpMcpTransport(acp_id, manager)
        return fastmcp.Client(transport)
    case _ as unreachable:
        assert_never(unreachable)
```

### Data Model

```
AgentPoolACPAgent (per-ACP-connection)
  └── client: acp.Client
  └── _mcp_manager: AcpMcpConnectionManager
        ├── _by_acp_id: dict[acpId, list[connectionId]]  # registered servers
        ├── _connections: dict[connectionId, AcpMcpConnection]  # active connections
        └── _client: acp.Client  # reference to parent ACP client

ACPSession (per-agent-session)
  └── mcp_servers: Sequence[McpServer]  # includes AcpMcpServer declarations
```

连接 ID 生成规则：使用 `uuid4().hex`（完整 32 字符），而非截断版本，避免高并发场景下的碰撞风险。

### Error Handling Specification

| 场景 | JSON-RPC Error Code | 消息 |
|------|---------------------|------|
| `mcp/connect` 缺少 `acpId` | `-32602` (Invalid Params) | `"Missing required parameter: acpId"` |
| `mcp/connect` 未知 `acpId` | `-32602` (Invalid Params) | `"Unknown acpId: {acpId}"` |
| `mcp/message` 缺少 `connectionId` | `-32602` (Invalid Params) | `"Missing required parameter: connectionId"` |
| `mcp/message` 未知 `connectionId` | `-32602` (Invalid Params) | `"Unknown connectionId: {connectionId}"` |
| `mcp/message` 客户端超时 | `-32000` (Server Error) | `"MCP message timeout after {timeout}s"` |
| `mcp/message` 客户端返回 MCP 错误 | 透传 | 透传客户端返回的 MCP error 对象 |

### Timeout Semantics

- `mcp/connect`：5 秒超时
- `mcp/message`：可配置，默认 30 秒（`AcpMCPServerConfig.timeout`）
- `mcp/disconnect`：5 秒超时，超时后强制清理本地状态

---

## Security Considerations

### Threat Analysis

| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| 客户端注入恶意 MCP 工具 | High | Medium | 与现有 MCP 信任模型一致：agentpool 不对 MCP 工具内容进行额外验证，工具调用权限由用户在会话级别授权 |
| `acpId` 碰撞/伪造 | Medium | Low | `acpId` 由客户端生成（UUID），agentpool 仅路由不验证所有权；**关键防护**：`acpId` 绑定到声明它的 session，跨 session 不可复用 |
| `mcp/message` 参数注入 | Low | Low | 透明转发，agentpool 不解析工具参数内容；安全责任由 MCP Server（客户端侧）承担 |
| 连接未清理导致内存泄漏 | Medium | Low | 绑定 ACP 连接生命周期，transport 断开时强制 `cleanup_all()` |
| **消息洪泛攻击** | High | Medium | **新增**：对 `mcp/message` 实施 rate limiting（max 100 req/min per connectionId） |
| **超大 payload 攻击** | High | Medium | **新增**：`mcp/message` 请求/响应 body 限制 10MB，超限返回 `-32000` error |
| **connectionId 枚举** | Medium | Low | 使用完整 `uuid4().hex`（128-bit 熵），不可预测 |

### Security Measures

- [ ] `AcpMcpConnectionManager` 仅存储当前 ACP connection 内注册的 servers，不跨 connection 共享
- [ ] `mcp/connect` 时验证 `acpId` 是否在当前 connection 的注册列表中，未注册则返回 `-32602` 错误
- [ ] `mcp/disconnect` 后立即从活跃连接表中移除，防止重放
- [ ] `mcp/message` 请求体大小限制 10MB，响应体大小限制 10MB
- [ ] Rate limiting：每个 `connectionId` 每分钟最多 100 条 `mcp/message`
- [ ] ACP transport 断开时，自动调用 `cleanup_all()` 清理所有活跃连接

### Compliance

MCP-over-ACP 遵循与现有 MCP 传输相同的信任模型：工具调用由 Agent（LLM）发起，用户通过 `session/request_permission` 授权，无需新增合规机制。

---

## Implementation Plan

### Pre-Phase 0: Transport Research Spike（2 天，必须完成）

- **Scope**：验证 `fastmcp.ClientTransport` 接口的可行性，确认 pinned fastmcp 版本支持自定义 transport
- **Deliverables**：
  - 可运行的 `AcpMcpTransport` 原型（最小实现，仅支持 `tools/call`）
  - 确认 `fastmcp.Client` 与自定义 transport 的集成路径
  - 确认 `acp` 库的 handler 注册机制（method dispatch 如何实现）
- **Go/No-Go 决策**：如果 Pre-Phase 0 发现 fastmcp 接口不兼容，需重新评估方案或升级依赖

### Phase 1: Schema 层扩展 + 前置条件修复（2-3 天）

- **Scope**：
  - 修复 `StdioMcpServer.type`（恢复 discriminator 字段）
  - 扩展 `McpCapabilities`（`acp` 字段）
  - 新增 `AcpMcpServer` schema
  - 扩展 `AgentMethod`/`ClientMethod`
  - 更新 `AgentCapabilities.create()`
  - 更新 `MCPServerConfig` union（`agentpool_config/mcp_server.py`）
  - 更新 `MCPResourceProvider.transport_type` 返回类型
- **Deliverables**：Schema 变更 PR，附单元测试
- **Dependencies**：Pre-Phase 0 通过

### Phase 2: AcpMcpConnectionManager + Transport 实现（4-5 天）

- **Scope**：
  - 新增 `acp_mcp_manager.py`：实现 register/connect/disconnect/send_message/cleanup
  - 新增 `acp_mcp_transport.py`：实现 `fastmcp.ClientTransport`
  - 处理 MCP JSON-RPC id 空间与 ACP JSON-RPC id 空间的隔离
  - 实现 timeout、rate limiting、payload 大小检查
- **Deliverables**：Manager + Transport 模块 + 单元测试（mock ACP client）
- **Dependencies**：Phase 1

### Phase 3: acp_agent.py 集成（3-4 天）

- **Scope**：
  - 在 `AgentPoolACPAgent` 中实例化 `AcpMcpConnectionManager`
  - 注册 `mcp/connect`、`mcp/disconnect` handler（通过 acp 库协议）
  - 在 `initialize()` 中声明 `acp` 能力
  - session 初始化时注册 ACP MCP servers 到 manager
  - ACP transport 断开时调用 `cleanup_all()`
- **Deliverables**：集成代码 + 集成测试（完整消息流）
- **Dependencies**：Phase 1 & 2

### Phase 4: converters.py 双向扩展 + 端到端验证（2-3 天）

- **Scope**：
  - 扩展 `convert_acp_mcp_server_to_config()`（正向）
  - 扩展 `mcp_config_to_acp()`（反向，`acp_converters.py`）
  - 扩展 `MCPClient._get_client()` 支持 `AcpMCPServerConfig`
  - 端到端测试：LLM 通过 ACP channel 调用 MCP 工具
- **Deliverables**：端到端可用 + 完整测试覆盖
- **Dependencies**：Phase 1-3

### Milestones

| Milestone | Description | Target | Status |
|-----------|-------------|--------|--------|
| Transport Spike 完成 | Pre-Phase 0 通过，确认 fastmcp 接口可行 | TBD | Not Started |
| Schema PR merged | Phase 1 完成，schema 可用 | TBD | Not Started |
| Manager + Transport 单测通过 | Phase 2 完成，核心逻辑可测 | TBD | Not Started |
| 集成测试通过 | Phase 3 完成，完整流程可验证 | TBD | Not Started |
| 端到端验证 | Phase 4 完成，LLM 实测工具调用 | TBD | Not Started |

### Rollback Strategy

- **Feature Flag**：新增 `acp` 能力声明默认关闭，通过配置项开启，便于线上紧急回滚
- **Schema 兼容性**：各 Phase 均为增量添加（新字段、新模块、新 case 分支），不修改已有路径。如需回滚，删除对应新增文件和字段即可
- **Session 持久化兼容性**：如果 sessions 被持久化且包含 `AcpMcpServer`，回滚前需迁移或清除相关 session 数据

---

## Open Questions

### 已解决

1. **`AgentCapabilities.create()` 参数命名**
   - **Decision**：命名为 `acp_mcp_servers`，与 `http_mcp_servers`、`sse_mcp_servers` 保持一致
   - **Rationale**：命名一致性优先，虽略显冗长但明确无歧义

2. **pydantic-ai MCP transport 接入点**
   - **Decision**：不直接对接 pydantic-ai，而是实现 `fastmcp.ClientTransport`，通过 `fastmcp.Client` 接入 agentpool 现有的 `MCPClient`
   - **Rationale**：agentpool 实际使用 fastmcp（而非 pydantic-ai 的 MCP 抽象）管理 MCP 连接。`ClientTransport` 是正确的扩展点

3. **`mcp/message` 的 method 归属**
   - **Decision**：在 `AgentMethod` 和 `ClientMethod` 中均注册 `"mcp/message"`
   - **Rationale**：RFD 明确其为双向 method。agent→client 方向用于工具调用，client→agent 方向用于 notification（如 `tools/list_changed`）

4. **是否同步实现 Bridging**
   - **Decision**：**否**，Bridging defer 至后续 RFC
   - **Rationale**：本 RFC 聚焦原生支持，Bridging 作为兼容层可独立演进。过早引入 Bridging 会增加本 RFC 的复杂度和风险

### 仍待确认

1. **`acp` 库 handler 注册机制**
   - Context：`AgentPoolACPAgent` 继承自外部 `acp` 库的 `ACPAgent`，新 method 的 dispatch 机制需在 Pre-Phase 0 中确认
   - Owner: 本 RFC 实现者
   - Status: **Blocker for Phase 3**

2. **fastmcp pinned 版本是否支持自定义 ClientTransport**
   - Context：需确认 agentpool 当前锁定的 fastmcp 版本是否暴露 `ClientTransport` 接口
   - Owner: 本 RFC 实现者
   - Status: **Blocker for Pre-Phase 0**

---

## Decision Record

> 待 RFC review 结束后填写

### Decision

**Status**: DRAFT

**Date**: —

**Approvers**: —

### Decision Summary

—

### Key Discussion Points

—

### Conditions of Approval

—

### Dissenting Opinions

—

---

## References

### Related Documents

- [ACP RFD: mcp-over-acp](../../../../agent-client-protocol/docs/rfds/mcp-over-acp.mdx)
- [RFC-0030: ACP Streamable HTTP/WebSocket Transport](../draft/RFC-0030-acp-streamable-http-websocket-transport.md)
- [ACP RFD: proxy-chains](../../../../agent-client-protocol/docs/rfds/)

### External Resources

- [ACP Protocol Documentation](https://agentclientprotocol.com/protocol/initialization)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [ACP Rust SDK sacp-conductor (reference bridging implementation)](https://github.com/anthropics/rust-sdk)

### Appendix

#### A. 当前 Schema 缺失一览

| 缺失项 | 文件 | 类型 |
|--------|------|------|
| `McpCapabilities.acp` 字段 | `acp/schema/capabilities.py` | Schema 字段 |
| `AcpMcpServer` 类型 | `acp/schema/mcp.py` | Schema 类 |
| `mcp/connect` method | `acp/schema/messages.py` | AgentMethod |
| `mcp/disconnect` method | `acp/schema/messages.py` | AgentMethod |
| `mcp/message` method | `acp/schema/messages.py` | ClientMethod（双向） |
| `AcpMcpServer` handler | `agentpool_server/acp_server/converters.py` | 转换逻辑（正向） |
| `AcpMcpServer` reverse handler | `agentpool_server/acp_server/acp_converters.py` | 转换逻辑（反向） |
| `AcpMCPServerConfig` | `agentpool_config/mcp_server.py` | Config 类 |
| `AcpMcpConnectionManager` | `agentpool_server/acp_server/acp_mcp_manager.py` | 核心逻辑 |
| `AcpMcpTransport` | `agentpool_server/acp_server/acp_mcp_transport.py` | fastmcp Transport |

#### B. `assert_never` 穷尽匹配更新清单

新增 `AcpMcpServer` / `AcpMCPServerConfig` 后，以下匹配块**必须**同步更新，否则运行时崩溃：

| 文件 | 函数/属性 | 操作 |
|------|----------|------|
| `agentpool_server/acp_server/converters.py` | `convert_acp_mcp_server_to_config()` | 新增 `case AcpMcpServer()` |
| `agentpool_server/acp_server/acp_converters.py` | `mcp_config_to_acp()` | 新增 `case AcpMCPServerConfig()` |
| `agentpool/mcp_server/provider.py` | `transport_type` property | 新增 `"acp"` 到 return `Literal` |
| `agentpool/mcp_server/client.py` | `MCPClient._get_client()` | 新增 `case AcpMCPServerConfig()` |
| `agentpool_config/mcp_server.py` | `MCPServerConfig` union | 新增 `AcpMCPServerConfig` |
| `agentpool_config/mcp_server.py` | `parse_mcp_servers_json()` | 新增 `"acp"` transport 分支 |

#### C. 前置条件修复清单

| 修复项 | 文件 | 说明 |
|--------|------|------|
| 恢复 `StdioMcpServer.type` | `acp/schema/mcp.py:78-79` | 取消注释 `typ: Literal["stdio"] = Field(...)`，使 `McpServer` union 成为合法的 discriminated union |

#### D. 测试策略补充

| 测试类型 | 覆盖场景 |
|----------|----------|
| 单元测试 | `AcpMcpConnectionManager` 状态机（connect/disconnect/send_message/cleanup） |
| 单元测试 | `AcpMcpTransport` 流模拟（request/response 配对、id 空间隔离、timeout） |
| 集成测试 | 完整生命周期：`session/new` → `mcp/connect` → `mcp/message` → `mcp/disconnect` |
| 集成测试 | 并发场景：多个 `connectionId` 同时发送 `mcp/message` |
| 集成测试 | 错误场景：未知 `acpId`、未知 `connectionId`、客户端超时、payload 超限 |
| 集成测试 | 反向转换：`AcpMCPServerConfig` → `AcpMcpServer`（`acp_converters.py`） |
| 回归测试 | 现有 stdio/SSE/HTTP MCP 功能无回归 |
| 端到端测试 | LLM 通过 ACP channel 调用真实 MCP 工具（需 mock ACP client） |
