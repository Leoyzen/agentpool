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
| Capability base + all capabilities | `capabilities/{function_toolset,filtered_toolset,combined_toolset,...}.py` |
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
- **ToolManager is deprecated**: New tools go through native `AbstractCapability` instances. `ToolManager` emits deprecation warnings.
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
- **One MCP server per capability**: Each `MCPCapability` wraps exactly one server. Use `CombinedToolsetCapability` to combine them.
- **Span instrumentation is mandatory**: All critical-path methods (RunLoop, Turn, delegation, capabilities, lifecycle) MUST use `@logfire.instrument` or `with logfire.span(...)`. Never `asyncio.create_task()` without an active span — it produces orphan traces. See root AGENTS.md "Telemetry & Span Instrumentation" for rules and naming conventions.

## Anti-Patterns

- **`connect_to()` at runtime**: Deprecated. Define connections in YAML `graph:` or `connections:` sections.
- **Bare `async for` in agent loops**: Use `RunExecutor`. The bare pattern silently drops `after_node_run` capability hooks.
- **Mutable state on Agent objects**: `AgentRunContext` is per-execution isolation. Stashing mutable state on the `Agent` instance leaks between runs.
- **Config model imports from core**: Import config types from `agentpool_config.*`, not `agentpool.models`, to avoid circular deps.
- **Direct tool code in `tools/`**: Tool framework goes in `tools/`. Concrete implementations go in `tool_impls/`.
- **Accessing `agent_pool` read-only**: Use `host_context` (immutable `HostContext`) instead. The `agent_pool` property emits `DeprecationWarning` as of M2.
- **Mixing `lifecycle.EventEnvelope` with `orchestrator.event_bus.EventEnvelope`**: These are separate types with different roles. Import from `agentpool.lifecycle.types` for lifecycle transport envelopes; from `agentpool.orchestrator.event_bus` for internal EventBus envelopes.
- **Blocking calls in async paths**: MCP connections, tool execution, and hooks all expect async methods. Use `anyio` or `asyncio`.
- **Bare `asyncio.create_task()` without span**: Produces orphan traces. Always ensure a `logfire.span` is active at the call site.

## Notes

- **Talk signals backbone**: Every `Talk` instance carries `message_received`, `forwarded`, and `connection_processed` signals. `SignalEmittingGraphRun` bridges these to pydantic-graph.
- **Compaction is a pipeline**: `CompactionPipeline` runs strategies in sequence. Each strategy receives full history and returns a condensed version.
- **EventBus scoped subscriptions**: `"session"` (exact), `"descendants"` (children), `"subtree"` (full subtree), `"all"` (everything). Default is `"session"`.
- **RunHandle cleanup**: `complete_event` fires after all cleanup. `close_session()` awaits it with timeout, then falls back to `cancel_run()`.
- **Crash recovery via journal.resume()**: Detects in-flight Turns by comparing journal entries against snapshot store turn results. Strategy `"mark_interrupted"` skips re-execution; `"retry"` checks tool execution log for idempotency.
- **Tool execution logging in HookAwareTurn**: `_fire_post_tool_hooks()` calls `_log_tool_execution()` which stores a `ToolExecutionRecord` in the Journal. Independent of hooks config.
- **agent_pool deprecated for host_context**: `MessageNode.agent_pool` emits `DeprecationWarning` (M2). Most call sites migrated in M3 (~60), but 18 references remain (primarily ACP server code). Full removal tracked as follow-up before M4. Use `host_context` (immutable `HostContext`).
- **Codemode is a metacall**: `CodeModeCapability` wraps all tools into a single Python execution tool. One tool to rule them all.
- **Skill commands are protocol-agnostic**: `SkillCommand` wraps skills as slash commands working across ACP, AG-UI, and OpenCode without protocol-specific code.
- **SkillCapability injection order matters**: In `get_agentlet()`, skill capabilities are injected at position 5 (after MCP, deferred bridge, approval bridge, and hook capabilities). Each skill produces one `SkillCapability` instance with its own `SkillMcpManager` and `SkillToolManager` — there is one manager tree shared across all skills from the same agentlet creation call.
- **mcp.json format follows Claude Desktop**: The companion file uses `{"mcpServers": {"name": {"command": "...", "args": [...], ...}}}` JSON format. The `_load_mcp_json()` function handles env var expansion and converts entries to `SkillMcpServerConfig` objects. Only filesystem skills (UPath paths) can have companion files — virtual skills (PurePosixPath) cannot.
- **Tool prefixing prevents name collisions**: Python tools get the prefix `{skill_name}__tool__` and MCP tools get `{skill_name}__mcp__`. This ensures tool names from different skills never collide in the agent's tool namespace.
- **Config model lives in separate package**: `agentpool_config/` exists solely to prevent import cycles with protocol servers.

## Event Types

The `RichAgentStreamEvent` union (`agents/events/events.py`) covers all events that flow through the EventBus and `run_stream()`. The taxonomy:

| Name | Kind | Purpose |
|---|---|---|
| `AgentStreamEvent` | Stream | PydanticAI-native streaming delta |
| `StreamCompleteEvent` | Lifecycle | Run finished with final `ChatMessage` |
| `RunStartedEvent` | Lifecycle | Run began |
| `RunErrorEvent` | Lifecycle | Run errored (recoverable) |
| `RunFailedEvent` | Lifecycle | Run failed (unrecoverable) |
| `ToolCallStartEvent` | Tool | Tool invocation started |
| `ToolCallProgressEvent` | Tool | Tool invocation progress |
| `ToolCallCompleteEvent` | Tool | Tool invocation finished |
| `ToolCallDeferredEvent` | Tool | Tool invocation deferred (elicitation/approval) |
| `ElicitationDeferredEvent` | Tool | Elicitation request deferred |
| `SessionResumeEvent` | Lifecycle | Session resumed from checkpoint |
| `PlanUpdateEvent` | Plan | Todo/plan entries updated |
| `CompactionEvent` | Lifecycle | Context window compacted |
| `SubAgentEvent` | Subagent | Wraps events from a child agent |
| `SpawnSessionStart` | Subagent | Child session spawned |
| `ToolResultMetadataEvent` | Tool | Tool result metadata attached |
| `CustomEvent` | Custom | User-defined event |
| `StateUpdate` | Lifecycle | Internal loop state transition (journaled, NOT published to EventBus) |
| `ToolCallUpdateEvent` | Tool | Tool call updated (incremental) |
| `MessageReplacementEvent` | Message | Replace a prior message (upsert semantics) |
| `SystemNotificationEvent` | System | System-level notification visible in the TUI (background task completion, lifecycle events, steer/followup injection, team activity) |

### SystemNotificationEvent

`SystemNotificationEvent` (`agents/events/events.py`) carries a system-generated message that should be displayed inline in the conversation transcript without an agent turn. It is distinct from `ToastInfo` (chrome-level OS toast/sound notifications).

**Fields:**

| Field | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `""` | Session this notification belongs to |
| `level` | `Literal["info","warning","error","success"]` | `"info"` | Severity level |
| `source` | `Literal[...]` | `"system"` | What produced the notification (see below) |
| `title` | `str` | `""` | Short human-readable title (optional) |
| `text` | `str` | (required) | Notification body text |
| `ref_session_id` | `str \| None` | `None` | Optional reference to a related session (e.g. child session of a background task) |
| `ref_label` | `str \| None` | `None` | Optional human-readable label for `ref_session_id` (e.g. `"subagent: researcher"`) |
| `timestamp` | `float` | `time.time()` | Epoch seconds when the notification was emitted |

**`source` enum values:**

| Value | Produced by |
|---|---|
| `background_task` | `AgentRunContext.complete_background_task()` — a background subagent finished |
| `system` | Generic system source (default) |
| `lifecycle` | Lifecycle-event fallback mapping in `EventProcessor` (compaction, plan update, session resume) |
| `steer` | `RunHandle.steer(emit_notification=True)` — mid-turn message injection |
| `followup` | `RunHandle.followup(emit_notification=True)` — queued prompt for next turn |
| `team` | Team mode `send_message` — one member notified another |
| `custom` | User code calling `emit_system_notification(source="custom", ...)` |

**`level` enum values:** `info` (default), `warning`, `error`, `success`.

**`ref_session_id` / `ref_label`:** When set, the OpenCode `EventProcessor` appends `(ref_label)` — or `(session: ref_session_id)` if only `ref_session_id` is set — to the rendered output so the TUI can link the notification to a related session.

## System Notifications

### AgentRunContext.emit_system_notification()

`AgentRunContext.emit_system_notification()` (`agents/context.py`) is the public API for capabilities and tools to emit custom system notifications. It publishes a `SystemNotificationEvent` directly to `self.event_bus` (the same pattern as `report_progress()` and `StreamEventEmitter._emit()`). It does NOT go through `CommChannel` — notifications are point-in-time and never journaled.

Best-effort semantics: empty `text` logs a warning and returns without emitting; `event_bus` failures are logged but never raised. Decorated with `@logfire.instrument("agent.emit_system_notification {session_id}")`.

```python
# From a tool or capability with access to RunContext.deps
await ctx.deps.run_ctx.emit_system_notification(
    level="info",
    source="custom",
    text="Analysis complete",
    ref_session_id="child-123",
    ref_label="subagent: researcher",
)
```

`AgentRunContext.complete_background_task()` calls this internally with `source="background_task"` and `ref_session_id=child_session_id` after the `steer_callback` try/except block, so the TUI surfaces background task completion even if the model doesn't echo the result.

### RunHandle.steer() / followup() Notification Parameters

`RunHandle.steer()` and `RunHandle.followup()` (`orchestrator/run.py`) accept an `emit_notification: bool` parameter that controls whether a `SystemNotificationEvent` is scheduled for the injection.

| Method | Default | `source` | Rationale |
|---|---|---|---|
| `steer(emit_notification=True)` | `True` | `"steer"` | Mid-turn injections are noteworthy — the user should see that a steer message arrived |
| `followup(emit_notification=False)` | `False` | `"followup"` | Queued messages are less urgent and will be visible when the next turn processes them |

**Fire-and-forget mechanism:** When `emit_notification` is `True`, the notification is scheduled via `asyncio.get_running_loop().create_task(self._emit_steer_notification(message))` (or `_emit_followup_notification`). This is viable because `steer()` and `followup()` are always called from an async context (protocol handlers, `SessionController`). The `create_task` call is wrapped in `try/except RuntimeError` — if there is no running event loop, the notification is silently skipped and `steer()` / `followup()` still return normally. The private `_emit_*_notification` coroutines truncate the message to 80 chars (with `"..."` suffix) and wrap the `emit_system_notification` call in `try/except` so failures never propagate.

**Team mode suppression:** Team mode `send_message` passes `emit_notification=False` to `steer()` to suppress the generic `"System injected: ..."` notification, then emits its own `source="team"` notification with a `ref_session_id` / `ref_label` pointing at the recipient member. This avoids duplicate notifications and gives the TUI more specific context.
