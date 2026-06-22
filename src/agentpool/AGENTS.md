# agentpool (Core Framework)

## Overview

229 files implementing agent runtimes, message routing, tool management, skill discovery, session orchestration, MCP integration, and codebase mapping. All processing units share the `MessageNode` abstraction.

## Where to Look

| Task | File |
|---|---|
| Agent lifecycle (setup, run, cleanup) | `agents/base_agent.py` |
| Native PydanticAI agent internals | `agents/native_agent/` |
| ACP agent (subprocess JSON-RPC) | `agents/acp_agent/` |
| Per-run state container | `agents/context.py` (`AgentRunContext`) |
| Tool result injection into conversation | `agents/prompt_injection.py` |
| Stream event types (20+ variants) | `agents/events/events.py` |
| Stream event emitter + processors | `agents/events/event_emitter.py`, `processors.py` |
| Core MessageNode abstraction | `messaging/messagenode.py` |
| pydantic-graph step wrapper | `messaging/graph_adapter.py` |
| Signal bridge (anyenv to pydantic-graph) | `messaging/signal_adapter.py` |
| Chat message compaction pipeline | `messaging/compaction.py` |
| AgentPool registry | `delegation/pool.py` |
| Parallel team orchestration | `delegation/team.py` |
| Sequential team orchestration | `delegation/teamrun.py` |
| Talk connections between nodes | `talk/talk.py` |
| Connection registry + graph edges | `talk/registry.py`, `talk/graph_edges.py` |
| Tool base classes + signature parsing | `tools/base.py` |
| Concrete tool implementations | `tool_impls/{bash,read,grep,...}/` |
| RunExecutor (native agent loop) | `orchestrator/run_executor.py` |
| EventBus, SessionController, TurnRunner | `orchestrator/core.py` |
| RunHandle lifecycle | `orchestrator/run.py` |
| ResourceProvider base + all providers | `resource_providers/{base,mcp_provider,pool,local,...}.py` |
| Skill YAML frontmatter model | `skills/skill.py` |
| Skill auto-discovery from paths | `skills/registry.py` |
| Skill wrapped as slash commands | `skills/command.py`, `command_registry.py` |
| Skill URI resolver (`skill://`) | `skills/uri_resolver.py` |
| MCP client + tool bridge | `mcp_server/{client,manager,tool_bridge}.py` |
| Hook types (callable, command, prompt) | `hooks/{base,callable,command,prompt}.py` |
| AgentHooks container (deprecated) | `hooks/agent_hooks.py` |
| Repomap (tree-sitter code mapping) | `repomap/core.py` |
| Session store + models | `sessions/{store,models}.py` |

## Conventions

- **Every node extends MessageNode**: Agents, teams, and the pool itself. Always implement `_step` for pydantic-graph compatibility.
- **Two queue systems**: Native agents use PydanticAI's `PendingMessageDrainCapability`. ACP agents use `TurnRunner` manual queues (`_post_turn_injections`, `_post_turn_prompts`).
- **RunExecutor over bare iteration**: Always use `RunExecutor` to drive native agent runs. Bare `async for node in agent_run:` skips `after_node_run` hooks and breaks message draining.
- **ToolManager is dead**: New tools go through `ResourceProvider.as_capability()`. `ToolManager` emits deprecation warnings.
- **Deferred imports for circular safety**: `TYPE_CHECKING` blocks + `from __future__ import annotations`. For truly circular paths (`messagenode` ↔ `team`), defer imports inside function bodies.
- **Signals at step boundaries**: `SignalEmittingGraphRun` maps pydantic-graph transitions to `Talk` signals. Do not emit signals manually from inside steps.
- **Skills parse YAML frontmatter**: `Skill` model uses `extra="forbid"` to reject unknown keys. Instructions lazy-load from `SKILL.md`.
- **One MCP server per provider**: Each `MCPResourceProvider` wraps exactly one server. Use `AggregatingProvider` to combine them.

## Anti-Patterns

- **`connect_to()` at runtime**: Deprecated. Define connections in YAML `graph:` or `connections:` sections.
- **Bare `async for` in agent loops**: Use `RunExecutor`. The bare pattern silently drops `after_node_run` capability hooks.
- **Mutable state on Agent objects**: `AgentRunContext` is per-execution isolation. Stashing mutable state on the `Agent` instance leaks between runs.
- **Config model imports from core**: Import config types from `agentpool_config.*`, not `agentpool.models`, to avoid circular deps.
- **Direct tool code in `tools/`**: Tool framework goes in `tools/`. Concrete implementations go in `tool_impls/`.
- **Blocking calls in async paths**: MCP connections, tool execution, and hooks all expect async methods. Use `anyio` or `asyncio`.

## Notes

- **Talk signals backbone**: Every `Talk` instance carries `message_received`, `forwarded`, and `connection_processed` signals. `SignalEmittingGraphRun` bridges these to pydantic-graph.
- **Compaction is a pipeline**: `CompactionPipeline` runs strategies in sequence. Each strategy receives full history and returns a condensed version.
- **EventBus scoped subscriptions**: `"session"` (exact), `"descendants"` (children), `"subtree"` (full subtree), `"all"` (everything). Default is `"session"`.
- **RunHandle cleanup**: `complete_event` fires after all cleanup. `close_session()` awaits it with timeout, then falls back to `cancel_run()`.
- **Codemode is a metacall**: `CodeModeResourceProvider` wraps all tools into a single Python execution tool. One tool to rule them all.
- **Skill commands are protocol-agnostic**: `SkillCommand` wraps skills as slash commands working across ACP, AG-UI, and OpenCode without protocol-specific code.
- **Config model lives in separate package**: `agentpool_config/` exists solely to prevent import cycles with protocol servers.
