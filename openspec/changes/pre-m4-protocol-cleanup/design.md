# Design: Pre-M4 Protocol Server Debt Cleanup

## Architecture Context

```
┌─────────────────────────────────────────────────────────────────┐
│                    Current State (post-M3)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ ACP      │    │ OpenCode │    │ AG-UI    │    │ OpenAI   │  │
│  │ Server   │    │ Server   │    │ Server   │    │ API      │  │
│  └────┬─────┘    └────┬─────┘    └──────────┘    └──────────┘  │
│       │               │                                          │
│       ▼               ▼                                          │
│  ┌─────────────────────────────┐                                │
│  │    ProtocolEventConsumer    │  ← adopted by all 4            │
│  │         Mixin               │                                │
│  └─────────────┬───────────────┘                                │
│                │                                                 │
│  ┌─────────────▼───────────────┐                                │
│  │       EventBus              │  ← clean                       │
│  └─────────────┬───────────────┘                                │
│                │                                                 │
│  ┌─────────────▼───────────────┐                                │
│  │    SessionController        │  ← dual paths, type ignores    │
│  └─────────────┬───────────────┘                                │
│                │                                                 │
│  ┌─────────────▼───────────────┐                                │
│  │      RunHandle.start()      │  ← 397 SLOC, type ignores      │
│  │   ┌─────────────────────┐   │                                │
│  │   │  _run_stream_once() │   │  ← ACP hook firing (legacy)    │
│  │   └─────────────────────┘   │                                │
│  │   ┌─────────────────────┐   │                                │
│  │   │  Turn.execute()     │   │  ← unified path (target)       │
│  │   │  via HookAwareTurn  │   │                                │
│  │   └─────────────────────┘   │                                │
│  └─────────────────────────────┘                                │
│                                                                  │
│  Problem: hooks_fired guard bridges the two paths               │
│  Problem: 12 # type: ignore in run.py                           │
│  Problem: OpenCode bypasses HostContext (68 sites)              │
│  Problem: Identity = agent.name + config_file_path              │
└─────────────────────────────────────────────────────────────────┘
```

## Phase 1: ACP Execution Path Unification

### Problem

ACP agents have two execution paths:

1. **Standalone** (`_run_stream_once`): `BaseAgent._stream_events()` → `_run_stream_once()` → inline hook firing for `AGENT_TYPE != "native"` → `hooks_fired.add("pre_turn")` / `hooks_fired.add("post_turn")`
2. **SessionPool** (`Turn.execute`): `RunHandle.start()` → `Turn.execute()` → `HookAwareTurn.fire_pre_turn_hooks()` → checks `hooks_fired` → skips if already fired

The `hooks_fired` set on `AgentRunContext` (21 refs across 4 files) is the double-fire guard. It works but adds complexity and makes hook ordering hard to reason about.

Additionally, `ACPAgentAPI` (the adapter that `ACPTurn` uses to communicate with the ACP client) is missing `stream_events()` and `get_messages()` methods. This means `ACPTurn.execute()` cannot actually run — it's a dead code path. ACP standalone falls back to the inline `_stream_events()` implementation (200 LOC in `acp_agent.py:412-611`).

### Design

```
Step 1: Build ACPAgentAPI adapter
  - Add stream_events() → wraps the internal state-polling loop as an async iterator.
    The inline path polls pop_update() on self._state (SessionState), woken by
    _update_event (TimeoutableEvent) via wait_with_timeout(0.05). The adapter MUST
    bridge this polling into an async iterator that ACPTurn.execute() can consume.
    It may NOT simply delegate to ACPClient.stream_events() if the transport uses polling.
  - Add get_messages() → wraps ACPClient.get_messages()
  - ACPAgentAPI now satisfies full ACPClientProtocol
  - Remove cast("ACPClientProtocol", self._api) at acp_agent.py:654

Step 2: Refactor _stream_events() to delegate to ACPTurn.execute()
  - ACPAgent._stream_events() creates ACPTurn and delegates
  - Inline 200-LOC implementation removed
  - ACPTurn.execute() becomes the single execution path

Step 3: Remove _run_stream_once() hook firing for ACP
  - Delete AGENT_TYPE != "native" branch in base_agent.py:1589-1613, 1649-1670
  - Hooks now fire ONLY through HookAwareTurn in Turn.execute()

Step 4: Remove hooks_fired guard
  - Delete hooks_fired set from AgentRunContext
  - Remove all 21 references in base_agent.py, turn.py, run.py, context.py
  - HookAwareTurn no longer checks guard before firing

Step 5: Remove deprecated queue_prompt/inject_prompt ACP branching
  - These methods have ACP-specific code paths that are no longer needed
  - Native agents already delegate to session_pool.followup()/steer()
  - ACP agents should follow the same pattern
```

### Risk

- `ACPTurn.execute()` is currently dead code — making it live may surface latent bugs
- Hook ordering may change (previously: `_run_stream_once` fired first; now: `Turn.execute` fires)
- Tests that mock `_run_stream_once` (3 test files) need updating

## Phase 2: Legacy Field & API Cleanup

### MCP Session State Consolidation

**Problem**: `NativeAgent` has `_mcp_snapshot: McpConfigSnapshot | None` and `_session_connection_pool: SessionConnectionPool | None` (agent.py:337-338). These are set internally during pool registration but also mutated externally by `ACPSession.initialize_mcp_servers()` (session.py:482-491). This is an encapsulation violation — server code reaches into agent internals to manage MCP state.

**Design**: Consolidate all MCP session state on `MCPManager._session_contexts`. The agent should not hold MCP state; it should query `MCPManager` via `host_context.mcp` when needed. `ACPSession.initialize_mcp_servers()` should call `MCPManager` methods, not mutate agent fields.

### CommChannel Protocol Typing

**Problem**: `deliver_feedback` is called via `try/except AttributeError` with `# type: ignore[attr-defined]` × 4 (run.py:822-830, 869-878). The method exists on `ProtocolChannel` but not on `DirectChannel`, so the caller duck-types it.

**Design**: Add `deliver_feedback(feedback: Feedback) -> None` to the `CommChannel` protocol. `DirectChannel` implements it as a no-op (or enqueues to the internal queue). Callers can then call `self._comm_channel.deliver_feedback(feedback)` without type ignores.

### RunStatus Enum Removal

**Problem**: `RunStatus` (pending, running, completed, failed, checkpointed, idle, done) coexists with `RunState` (IDLE, RUNNING, DONE) as a "legacy" enum. `RunHandle` has both `_run_state: RunState` and `RunStatus`-based legacy methods (`complete()`, `fail()`, `checkpoint()`).

**Design**: Remove `RunStatus` enum. Migrate `RunHandle.complete()` and `RunHandle.fail()` to use `RunState` transitions. Remove `RunStatus` from all type annotations. The legacy methods remain as backward-compatible wrappers but use `RunState` internally.

## Phase 3: OpenCode Server Hardening

> **MOVED TO M4** — This phase was merged into `m4-multi-config` task group 18 because it touches the same OpenCode route files that M4's RunScope routing modifies. See `openspec/changes/m4-multi-config/tasks.md` task group 18.

### Private Attribute Access

**Problem**: 6 route files access private attributes:

| File | Access | Fix |
|------|--------|-----|
| `agent_routes.py:108,479` | `agent._all_capabilities` | Add `agent.get_capabilities()` public method |
| `agent_routes.py:684,719` | `agent._get_all_tools()` | Rename to `agent.get_all_tools()` (make public) |
| `permission_routes.py:25,50` | `session_controller._sessions` | Add `session_controller.get_session(id)` |
| `question_routes.py:30,59,67,76` | `session_controller._sessions` | Same as above |
| `lsp_routes.py:38,200` | `lsp_manager._servers` | Add `lsp_manager.get_server(id)` or `lsp_manager.servers` property |
| `session_pool_integration.py:824` | `session_pool.sessions._runs` | Add `session_pool.get_runs(session_id)` |

**Design**: Add public API methods/properties on the owning classes. Routes call public API only.

### state.pool.* Migration

**Problem**: 68 direct `state.pool.*` accesses bypass `HostContext`.

| Cluster | Count | Migration Target |
|---------|-------|------------------|
| `state.pool.session_pool` | ~40 | `state.host_context.session_pool` |
| `state.pool.manifest` | 8 | `state.host_context.manifest` |
| `state.pool.todos` | 5 | `state.host_context.todos` |
| `state.pool.skill_resolver/.skill_provider/.skills` | 10 | `state.host_context.skills` (add accessor) |
| `state.pool.storage` | 3 | `state.host_context.storage` |
| `state.pool.file_ops` | 6 | `state.host_context.file_ops` |
| `state.pool.extension_registry` | 2 | `state.host_context.extension_registry` |

**Design**: `state.host_context` already exists and returns `HostContext`. The migration is mechanical: replace `state.pool.X` with `state.host_context.X`. Some fields (like `skill_resolver`, `skill_provider`) may need new accessors on `HostContext`.

### Dual Abort Paths

**Problem**: `session_routes.py:914-934` uses modern `SessionController.abort()`, then `936-947` falls back to `state.agent.interrupt()`. Also `state.agent.run()` at line 1954 bypasses `SessionPool` entirely.

**Design**: Remove the legacy fallback. All abort/run operations go through `SessionController`/`SessionPool`.

## Phase 4: Type Safety

### CommChannel Type Ignore Cluster

**Problem**: 6 `# type: ignore[attr-defined]` in `run.py` (lines 266, 270, 825, 830, 872, 877) access `_journal`, `_trigger_source`, `_snapshot_store` as private members of `CommChannel`. (2 additional `type: ignore[arg-type]` at lines 388, 563 are pre-existing and not in scope.)

**Design**: Either (a) add these as read-only properties on the `CommChannel` protocol, or (b) have `RunHandle` hold direct references to the dimensions (it already does via `_journal`, `_trigger_source` etc. — the issue is that `CommChannel` owns the journal). Option (b) is cleaner: `RunHandle` holds `_journal` directly, and `CommChannel` receives it in the constructor. `RunHandle` accesses `self._journal`, not `self._comm_channel._journal`.

### _channel_publishes_to_event_bus

**Problem**: `isinstance(self._comm_channel, ProtocolChannel)` check in `run.py:286` to avoid double-publishing. Fragile and untypeable.

**Design**: Add `publishes_to_event_bus: bool` property to `CommChannel` protocol. `ProtocolChannel` returns `True`, `DirectChannel` returns `False`. `RunHandle` checks the property instead of isinstance.

## Phase 5: M4 Identity Preparation

> **MOVED TO M4** — This phase was merged into `m4-multi-config` task groups 7-8 (RunScope) and 18 (OpenCode identity). See `openspec/changes/m4-multi-config/tasks.md`.

### Problem

OpenCode server uses `state.agent.name` as session identity (5 files) and `config_file_path` as pool identity (3 files). `session_controller` hardcodes `self.pool.manifest.agents` (4 sites). Under M4, identity comes from `RunScope` (config_id, tenant_id, session_id), not from agent name or file path.

### Design

```
Current:                           Target (M4-ready):
─────────────────────────          ─────────────────────────
session_id = agent.name     →     session_id = run_scope.session_id
pool_id = config_file_path  →     pool_id = run_scope.config_id
agents = pool.manifest.agents →   agents = host_registry.get_agents(config_id)
```

For now (pre-M4), we introduce a `RunScope` dataclass with default values (`config_id="default"`, `session_id=agent.name`). This doesn't change behavior but makes the identity abstraction explicit. When M4 arrives, `RunScope` is populated from protocol headers instead of defaults.

## Phase 6: Event System Gaps

### RunStartedEvent

**Problem**: `EventProcessor` (910 LOC) doesn't handle `RunStartedEvent`. Docs say it should map to `SessionStatusEvent(busy)`. Currently only handled in `session_pool_integration.py:1132`.

**Design**: Add `RunStartedEvent` handler in `EventProcessor._handle_event()` that emits `SessionStatusEvent(status="busy")`.

### McpToolsChangedEvent

**Problem**: Defined in `models/events.py:850-862` with a TODO comment but never wired.

**Design**: Wire it in `MCPCapability.on_change()` stream. `EventProcessor` handles it by triggering a tool list refresh.

### StreamCompleteEvent(cancelled=True)

**Problem**: `EventProcessor` at line 189 doesn't distinguish cancelled from completed.

**Design**: Check `event.cancelled` flag and emit appropriate `SessionStatusEvent` (`"cancelled"` vs `"idle"`).
