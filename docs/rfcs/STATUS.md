# RFC Status Index

This file is the single source of truth for RFC status. Last updated: 2026-07-21.

## Status Definitions

| Status | Meaning | Directory |
|---|---|---|
| **Draft** | Under discussion, not yet decided | `draft/` |
| **Accepted** | Decision made, implementation pending or in progress | `accepted/` |
| **Implemented** | Fully implemented in code | `implemented/` |
| **Rejected** | Decision made NOT to implement; preserved for history | `draft/` with "Status: Rejected" |

## Pipeline

```
RFC (decision phase) → accepted → OpenSpec change (execution phase) → archive → RFC marked "implemented"
```

- Major architectural decisions require an RFC first
- RFC accepted → open an OpenSpec change to implement
- Small changes (bug fixes, minor features) can go directly to OpenSpec

## All RFCs

### Implemented (14)

| RFC | Title | File |
|---|---|---|
| RFC-0001 | Workers and Teams Session Management Enhancement | [implemented/RFC-0001-workers-teams-session-management.md](implemented/RFC-0001-workers-teams-session-management.md) |
| RFC-0002 | Extended Tool Definition and Native PydanticAI Integration | [implemented/RFC-0002-extended-tool-definition.md](implemented/RFC-0002-extended-tool-definition.md) |
| RFC-0003 | PydanticAI History Processors Integration | [implemented/RFC-0003-pydantic-ai-history-processors-integration.md](implemented/RFC-0003-pydantic-ai-history-processors-integration.md) |
| RFC-0008 | Dynamic Skills Injection via ResourceProvider Instructions | [implemented/RFC-0008-dynamic-skills-injection.md](implemented/RFC-0008-dynamic-skills-injection.md) |
| RFC-0013 | Subagent Event Stream Unification for OpenCode Protocol | [implemented/RFC-0013-subagent-event-unification.md](implemented/RFC-0013-subagent-event-unification.md) |
| RFC-0014 | SpawnSessionStart Event for Explicit Subsession Creation | [implemented/RFC-0014-spawn-session-events.md](implemented/RFC-0014-spawn-session-events.md) |
| RFC-0020 | MCP Skills Resources Provider Protocol Support | [implemented/RFC-0020-mcp-skills-resources-provider.md](implemented/RFC-0020-mcp-skills-resources-provider.md) |
| RFC-0021 | Agent Concurrent Execution Safety | [implemented/RFC-0021-agent-concurrent-execution-safety.md](implemented/RFC-0021-agent-concurrent-execution-safety.md) |
| RFC-0021 | Pre-Flight Analysis (companion to RFC-0021) | [implemented/RFC-0021-PRE-FLIGHT-ANALYSIS.md](implemented/RFC-0021-PRE-FLIGHT-ANALYSIS.md) |
| RFC-0023 | Session-Scoped Interrupt Routing for Concurrent Agent Safety | [implemented/RFC-0023-session-scoped-interrupt-routing.md](implemented/RFC-0023-session-scoped-interrupt-routing.md) |
| RFC-0026 | Per-Session Agent Instances — Remove agent_lock | [implemented/RFC-0026-per-session-agent-isolation.md](implemented/RFC-0026-per-session-agent-isolation.md) |
| RFC-0031 | ACP Server Per-Session Agent Isolation | [implemented/RFC-0031-acp-per-session-agent-isolation.md](implemented/RFC-0031-acp-per-session-agent-isolation.md) |
| RFC-0033 | MCP-over-ACP: Support MCP Servers via ACP Channel Transport | [implemented/RFC-0033-mcp-over-acp-transport.md](implemented/RFC-0033-mcp-over-acp-transport.md) |
| RFC-0054 | V2 Message ID Infrastructure | [implemented/RFC-0054-v2-message-id-infrastructure.md](implemented/RFC-0054-v2-message-id-infrastructure.md) |

### Accepted (2)

| RFC | Title | File |
|---|---|---|
| RFC-0001 | Unified Run Tracking with RunHandle and PydanticAI Queue Adoption | [accepted/RFC-0001-unified-run-tracking.md](accepted/RFC-0001-unified-run-tracking.md) |
| RFC-0022 | OpenCode v1.4.4+ GlobalEvent Protocol Support | [accepted/RFC-0022-opencode-v144-global-event-protocol.md](accepted/RFC-0022-opencode-v144-global-event-protocol.md) |

### Draft (27)

| RFC | Title | File |
|---|---|---|
| RFC-0015 | Multi-Question Elicitation Support for OpenCode Server | [draft/RFC-0015-multiple-questions-elicitation.md](draft/RFC-0015-multiple-questions-elicitation.md) |
| RFC-0016 | Unified Skill-to-Slash Command Architecture | [draft/RFC-0016-skill-slash-commands.md](draft/RFC-0016-skill-slash-commands.md) |
| RFC-0017 | OpenCode Command Endpoint Skill Support | [draft/RFC-0017-opencode-command-skill-support.md](draft/RFC-0017-opencode-command-skill-support.md) |
| RFC-0018 | Agent Simulation Framework | [draft/RFC-0018-simulation-framework.md](draft/RFC-0018-simulation-framework.md) |
| RFC-0019 | MCP Server Display Name Separation from Client ID | [draft/RFC-0019-mcp-server-display-name-separation.md](draft/RFC-0019-mcp-server-display-name-separation.md) |
| RFC-0024 | Agent Stateless Refactor | [draft/RFC-0024-agent-stateless-refactor.md](draft/RFC-0024-agent-stateless-refactor.md) |
| RFC-0025 | Shared Agent Architecture | [draft/RFC-0025-shared-agent-architecture.md](draft/RFC-0025-shared-agent-architecture.md) |
| RFC-0027 | ACP Subagent Zed Compatibility | [draft/RFC-0027-acp-subagent-zed-compatibility.md](draft/RFC-0027-acp-subagent-zed-compatibility.md) |
| RFC-0028 | Delegation Provider Session Adaptation | [draft/RFC-0028-delegation-provider-session-adaptation.md](draft/RFC-0028-delegation-provider-session-adaptation.md) |
| RFC-0029 | Agent Reactivation via Pending Prompt Queue | [draft/RFC-0029-agent-reactivation-pending-prompt-queue.md](draft/RFC-0029-agent-reactivation-pending-prompt-queue.md) |
| RFC-0030 | ACP Streamable HTTP WebSocket Transport | [draft/RFC-0030-acp-streamable-http-websocket-transport.md](draft/RFC-0030-acp-streamable-http-websocket-transport.md) |
| RFC-0032 | ACP Slash Commands Protocol Compliance | [draft/RFC-0032-acp-slash-commands-session-update.md](draft/RFC-0032-acp-slash-commands-session-update.md) |
| RFC-0034 | ACP Session Config Options Unified | [draft/RFC-0034-acp-session-config-options-unified.md](draft/RFC-0034-acp-session-config-options-unified.md) |
| RFC-0034 | Per-Session TodoTracker Isolation | [draft/RFC-0034-per-session-todo-tracker-isolation.md](draft/RFC-0034-per-session-todo-tracker-isolation.md) |
| RFC-0034 | BackgroundTask Architecture Redesign | [draft/RFC-0034-background-task-redesign.md](draft/RFC-0034-background-task-redesign.md) |
| RFC-0035 | MCP-over-ACP: Complete Connection Chain | [draft/RFC-0035-mcp-over-acp-complete-connection-chain.md](draft/RFC-0035-mcp-over-acp-complete-connection-chain.md) |
| RFC-0036 | MCP-over-ACP: Comprehensive Test Coverage | [draft/RFC-0036-mcp-over-acp-test-coverage.md](draft/RFC-0036-mcp-over-acp-test-coverage.md) |
| RFC-0037 | Unify Steer and Followup Message Injection | [draft/RFC-0037-unify-steer-followup.md](draft/RFC-0037-unify-steer-followup.md) |
| RFC-0038 | Eliminate Pool-Level Agent Instances | [draft/RFC-0038-eliminate-pool-level-agents.md](draft/RFC-0038-eliminate-pool-level-agents.md) |
| RFC-0039 | ACP Subagent Zed Protocol Upgrade | [draft/RFC-0039-acp-subagent-zed-protocol-upgrade.md](draft/RFC-0039-acp-subagent-zed-protocol-upgrade.md) |
| RFC-0040 | Subagent Display Compatibility | [draft/RFC-0040-subagent-display-compatibility.md](draft/RFC-0040-subagent-display-compatibility.md) |
| RFC-0041 | Run vs Turn: Separating Session-Level Persistence from Reactive Execution | [draft/RFC-0041-loop-run-separation.md](draft/RFC-0041-loop-run-separation.md) |
| RFC-0042 | Unified Lifecycle Architecture | [draft/RFC-0042-unified-lifecycle-architecture.md](draft/RFC-0042-unified-lifecycle-architecture.md) |
| RFC-0050 | AgentWolf v1.0 Foundation Architecture | [draft/RFC-0050-agentwolf-v1-foundation-architecture.md](draft/RFC-0050-agentwolf-v1-foundation-architecture.md) |
| RFC-0051 | Extension Source Architecture | [draft/RFC-0051-extension-source-architecture.md](draft/RFC-0051-extension-source-architecture.md) |
| RFC-0052 | Restore Skill System Capabilities After M3 Refactor | [draft/RFC-0052-restore-skill-capabilities.md](draft/RFC-0052-restore-skill-capabilities.md) |
| RFC-0053 | Pre-M4 Protocol Server Debt Cleanup | [draft/RFC-0053-pre-m4-protocol-debt-cleanup.md](draft/RFC-0053-pre-m4-protocol-debt-cleanup.md) |

## Notes

- RFC-0020 had a stale pointer in `accepted/` (just a redirect to `implemented/`). Removed; STATUS.md now tracks this.
- RFC-0001 has two separate RFCs with the same number: one for "Workers and Teams" (implemented) and one for "Unified Run Tracking" (accepted). Both are legitimate — they cover different topics.
- RFC-0034 has three separate RFCs with the same number, covering different topics. All are in draft.
- RFC-0021-PRE-FLIGHT-ANALYSIS is a companion document to RFC-0021, not a separate RFC. Kept in `implemented/` alongside the main RFC.
- Some draft RFCs may be stale (features already implemented without the RFC being updated). A thorough review should reconcile these in a future pass.
