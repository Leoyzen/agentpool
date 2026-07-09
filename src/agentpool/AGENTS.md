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
| EventBus, SessionController | `orchestrator/core.py` |
| RunHandle lifecycle (RunLoop) | `orchestrator/run.py` |
| Lifecycle dimensions (5 Protocols) | `lifecycle/protocols.py` |
| Lifecycle types (RunState, ToolExecutionRecord) | `lifecycle/types.py` |
| TriggerSource implementations | `lifecycle/triggers.py` |
| Journal implementations (Memory, Durable) | `lifecycle/journal.py` |
| SnapshotStore implementations (Memory, Durable) | `lifecycle/snapshot_store.py` |
| CommChannel implementations (Direct, Protocol) | `lifecycle/comm_channel.py` |
| EventTransport (InProcess) | `lifecycle/event_transport.py` |
| Dimension factory from config | `lifecycle/factory.py` |
| LifecycleConfig Pydantic model | `agentpool_config/lifecycle.py` |
| ResourceProvider base + all providers | `resource_providers/{base,mcp_provider,pool,local,...}.py` |
| Skill YAML frontmatter model | `skills/skill.py` |
| Skill as pydantic-ai capability (instructions, tools, MCP) | `skills/capability.py` (`SkillCapability`) |
| Skill MCP server connection lifecycle | `skills/skill_mcp_manager.py` (`SkillMcpManager`) |
| Skill Python tool import from config | `skills/skill_tool_manager.py` (`SkillToolManager`) |
| Skill auto-discovery from paths | `skills/registry.py` |
| Skill wrapped as slash commands | `skills/command.py`, `command_registry.py` |
| Skill URI resolver (`skill://`) | `skills/uri_resolver.py` |
| Skill config models (McpServerConfig, ToolConfig) | `agentpool_config/skills.py` |
| MCP client + tool bridge | `mcp_server/{client,manager,tool_bridge}.py` |
| Hook types (callable, command, prompt) | `hooks/{base,callable,command,prompt}.py` |
| AgentHooks container (deprecated) | `hooks/agent_hooks.py` |
| Repomap (tree-sitter code mapping) | `repomap/core.py` |
| Session store + models | `sessions/{store,models}.py` |

## Conventions

- **Every node extends MessageNode**: Agents, teams, and the pool itself. Always implement `_step` for pydantic-graph compatibility.
- **Two queue systems**: Native agents use PydanticAI's `PendingMessageDrainCapability`. ACP agents use manual queues (`_post_turn_injections`, `_post_turn_prompts`). M2 adds `CommChannel` feedback loop for `ProtocolChannel` sessions.
- **RunExecutor over bare iteration**: Always use `RunExecutor` to drive native agent runs. Bare `async for node in agent_run:` skips `after_node_run` hooks and breaks message draining.
- **ToolManager is dead**: New tools go through `ResourceProvider.as_capability()`. `ToolManager` emits deprecation warnings.
- **Deferred imports for circular safety**: `TYPE_CHECKING` blocks + `from __future__ import annotations`. For truly circular paths (`messagenode` ↔ `team`), defer imports inside function bodies.
- **Signals at step boundaries**: `SignalEmittingGraphRun` maps pydantic-graph transitions to `Talk` signals. Do not emit signals manually from inside steps.
- **RunLoop = RunHandle + dimension injection**: RunHandle is NOT a new class. Its `start()` async generator is the RunLoop. Six pluggable dimensions (TriggerSource, Journal, SnapshotStore, CommChannel, EventTransport) are injected via constructor fields with `__post_init__` defaults.
- **CommChannel owns the Journal**: Every `CommChannel` has a `_journal` reference. `publish()` journals (append/upsert) before delivery. `StateUpdate` events are journaled but NOT published to EventBus.
- **ProtocolChannel bridges protocol servers**: `SessionController` creates `ProtocolTrigger` and `ProtocolChannel` for protocol-handler sessions. Trigger delivers prompts via a queue; Channel publishes events to EventBus.
- **`lifecycle.EventEnvelope` != `orchestrator.event_bus.EventEnvelope`**: Different types with different responsibilities. Lifecycle envelope is for language-agnostic transport serialization.
- **Skills parse YAML frontmatter**: `Skill` model uses `extra="forbid"` to reject unknown keys. Instructions lazy-load from `SKILL.md`.
- **Skills are capabilities**: `SkillCapability` wraps each `Skill` as an `AbstractCapability` providing instructions (`get_instructions`), tools (`get_toolset`), and tool filtering (`get_wrapper_toolset`). Injected in `get_agentlet()` at position 5 (after MCP capabilities).
- **Skill tools come in two flavors**: Python tools declared via `tools` field (`SkillToolConfig` with `import_path` like `"os:getcwd"`) imported eagerly by `SkillToolManager`. MCP servers declared via `mcp_servers` field (`SkillMcpServerConfig` with `command+args` or `url`) connected lazily per-run by `SkillMcpManager`. Both are prefixed with `{skill_name}__tool__` and `{skill_name}__mcp__` respectively.
- **mcp.json companion file**: A `mcp.json` file in the skill directory (using Claude Desktop format `{"mcpServers": {...}}`) takes precedence over the frontmatter `mcp-servers` field. Environment variables (`${VAR}`) are expanded automatically.
- **allowed_tools enforced via FilteredToolset**: The `parsed_allowed_tools()` method parses the space/comma-separated `allowed-tools` frontmatter string. `SkillCapability.get_wrapper_toolset()` wraps the assembled toolset in a `FilteredToolset` that drops tools not in the allowed list.
- **SkillMcpManager has session-scoped lifecycle**: Connections are per `(session_id, server_name)` pair, lazily established on first tool access, with idle timeout (default 5 minutes) and exponential backoff retry (3 attempts). `on_run_ended()` triggers cleanup.
- **One MCP server per provider**: Each `MCPResourceProvider` wraps exactly one server. Use `AggregatingProvider` to combine them.

## Anti-Patterns

- **`connect_to()` at runtime**: Deprecated. Define connections in YAML `graph:` or `connections:` sections.
- **Bare `async for` in agent loops**: Use `RunExecutor`. The bare pattern silently drops `after_node_run` capability hooks.
- **Mutable state on Agent objects**: `AgentRunContext` is per-execution isolation. Stashing mutable state on the `Agent` instance leaks between runs.
- **Config model imports from core**: Import config types from `agentpool_config.*`, not `agentpool.models`, to avoid circular deps.
- **Direct tool code in `tools/`**: Tool framework goes in `tools/`. Concrete implementations go in `tool_impls/`.
- **Accessing `agent_pool` read-only**: Use `host_context` (immutable `HostContext`) instead. The `agent_pool` property emits `DeprecationWarning` as of M2.
- **Mixing `lifecycle.EventEnvelope` with `orchestrator.event_bus.EventEnvelope`**: These are separate types with different roles. Import from `agentpool.lifecycle.types` for lifecycle transport envelopes; from `agentpool.orchestrator.event_bus` for internal EventBus envelopes.
- **Blocking calls in async paths**: MCP connections, tool execution, and hooks all expect async methods. Use `anyio` or `asyncio`.

## Notes

- **Talk signals backbone**: Every `Talk` instance carries `message_received`, `forwarded`, and `connection_processed` signals. `SignalEmittingGraphRun` bridges these to pydantic-graph.
- **Compaction is a pipeline**: `CompactionPipeline` runs strategies in sequence. Each strategy receives full history and returns a condensed version.
- **EventBus scoped subscriptions**: `"session"` (exact), `"descendants"` (children), `"subtree"` (full subtree), `"all"` (everything). Default is `"session"`.
- **RunHandle cleanup**: `complete_event` fires after all cleanup. `close_session()` awaits it with timeout, then falls back to `cancel_run()`.
- **Crash recovery via journal.resume()**: Detects in-flight Turns by comparing journal entries against snapshot store turn results. Strategy `"mark_interrupted"` skips re-execution; `"retry"` checks tool execution log for idempotency.
- **Tool execution logging in HookAwareTurn**: `_fire_post_tool_hooks()` calls `_log_tool_execution()` which stores a `ToolExecutionRecord` in the Journal. Independent of hooks config.
- **agent_pool deprecated for host_context**: `MessageNode.agent_pool` emits `DeprecationWarning` (M2), removal in M3. Use `host_context` (immutable `HostContext`).
- **Codemode is a metacall**: `CodeModeResourceProvider` wraps all tools into a single Python execution tool. One tool to rule them all.
- **Skill commands are protocol-agnostic**: `SkillCommand` wraps skills as slash commands working across ACP, AG-UI, and OpenCode without protocol-specific code.
- **SkillCapability injection order matters**: In `get_agentlet()`, skill capabilities are injected at position 5 (after MCP, deferred bridge, approval bridge, and hook capabilities). Each skill produces one `SkillCapability` instance with its own `SkillMcpManager` and `SkillToolManager` — there is one manager tree shared across all skills from the same agentlet creation call.
- **mcp.json format follows Claude Desktop**: The companion file uses `{"mcpServers": {"name": {"command": "...", "args": [...], ...}}}` JSON format. The `_load_mcp_json()` function handles env var expansion and converts entries to `SkillMcpServerConfig` objects. Only filesystem skills (UPath paths) can have companion files — virtual skills (PurePosixPath) cannot.
- **Tool prefixing prevents name collisions**: Python tools get the prefix `{skill_name}__tool__` and MCP tools get `{skill_name}__mcp__`. This ensures tool names from different skills never collide in the agent's tool namespace.
- **Config model lives in separate package**: `agentpool_config/` exists solely to prevent import cycles with protocol servers.
