# mcp-provider-lifecycle - Work Plan

## TL;DR (For humans)

**What you'll get:** MCP tools (filesystem, knowledge base, skill servers) will work reliably in subagents without race conditions or crash errors. Connections are shared efficiently at the pool level and isolated at the session level. Skills that declare their own MCP servers integrate seamlessly.

**Why this approach:** A three-tier architecture (immutable config snapshot → connection pools → lazy toolset materialization) eliminates the root cause: mutable provider lists and cached cross-task connections. The config snapshot is frozen at session creation and inherited by child sessions, so subagents get MCP tools immediately without waiting for session/load.

**What it will NOT do:** It will not change the YAML config schema, the ACP wire protocol, or the SkillMcpServerConfig model. It will not add config-declared statefulness or a single-gateway-tool pattern. It will not touch non-MCP resource provider paths.

**Effort:** Large
**Risk:** Medium — touches core session orchestration and agent lifecycle, but has comprehensive test coverage and fallback paths.
**Decisions to sanity-check:** Owner-task pattern for stdio (deer-flow reference), pool-level stateless assumption, session/load ignore-on-active semantics.

Your next move: approve, or run a high-accuracy review. Full execution detail follows below.

---

> TL;DR (machine): Large, Medium risk. 17 todos in 4 waves + final verification. Three-tier MCP connection scoping: config snapshot + global/session pools + lazy toolset materialization.

## Scope
### Must have
- `McpConfigEntry` + `McpConfigSnapshot` dataclasses (new file)
- `GlobalConnectionPool` with owner-task pattern (new file)
- `SessionConnectionPool` per-session isolation (new file)
- `MCPManager.as_capability()` rewrite to accept snapshot + use pools
- `get_agentlet()` MCP path bypass (read snapshot, not providers)
- `get_or_create_session_agent()` snapshot building + child inheritance
- `_build_pool_configs()` + `_build_agent_configs()` helper methods
- `Agent._mcp_snapshot` + `Agent._session_connection_pool` attributes
- `ACPSession.initialize_mcp_servers()` config entry building (no live providers)
- `resume_session()` session/load semantics (active=ignore, restore=use)
- `SkillMcpServerConfig.to_mcp_server_config()` bridge method
- `SkillMcpManager` delegates to `SessionConnectionPool` via snapshot
- `SkillCapability.get_toolset()` reads from session pool
- `streaming_adapter.py` CancelScope fix (move yield outside task group)
- Cleanup: remove `session_mcp_providers` list, debug logging, old `add_provider` calls for MCP
- Comprehensive test suite (unit + integration + manual QA)

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Config-declared statefulness (over-engineered for now)
- Single gateway tool pattern (oh-my-opencode pattern — too invasive)
- Changes to non-MCP ResourceProvider paths
- Changes to ACP protocol wire format
- Changes to YAML config schema
- Changes to SkillMcpServerConfig model itself (only add bridge method)
- Refactoring of existing test infrastructure
- `hasattr` or `getattr` usage (violates project rules)
- `as any`, `@ts-ignore` equivalent type suppressions
- TODOs or placeholders left in code

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (implementation first, then comprehensive test suite in Phase 4)
- Framework: pytest with asyncio_mode=auto
- Evidence: .omo/evidence/task-<N>-mcp-provider-lifecycle.<ext>

## Execution strategy
### Parallel execution waves

**Wave 1 (Phase 1: Config Snapshot + Inheritance)** — T1-T6
All tasks create new files or modify independent sections. T1 creates the dataclasses that T2-T6 depend on, so T1 is solo in Wave 1a, T2-T6 parallel in Wave 1b.

**Wave 2 (Phase 2: Connection Pools)** — T7-T10
T7 creates GlobalConnectionPool, T8 creates SessionConnectionPool (parallel). T9-T10 depend on T7+T8.

**Wave 3 (Phase 3: Skill MCP + Streaming Adapter)** — T11-T13
All independent: skill MCP integration, streaming adapter fix, cleanup.

**Wave 4 (Phase 4: Testing)** — T14-T17
Comprehensive test suite. T14 unit tests, T15 integration tests (parallel). T16 full suite run, T17 manual QA.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | — | T2,T3,T4,T5,T6 | — |
| T2 | T1 | T9,T10 | T3,T4,T5,T6 |
| T3 | T1 | T9,T10 | T2,T4,T5,T6 |
| T4 | T1 | — | T2,T3,T5,T6 |
| T5 | T1 | T9 | T2,T3,T4,T6 |
| T6 | T1 | T9,T10 | T2,T3,T4,T5 |
| T7 | — | T9 | T8,T11,T12,T13 |
| T8 | — | T9,T10 | T7,T11,T12,T13 |
| T9 | T2,T3,T5,T6,T7,T8 | T14,T15 | T10,T11,T12,T13 |
| T10 | T2,T3,T6,T8 | T14,T15 | T9,T11,T12,T13 |
| T11 | T2 | T14,T15 | T9,T10,T12,T13 |
| T12 | — | T14,T15 | T7,T8,T9,T10,T11,T13 |
| T13 | T9,T10 | T16 | T11,T12 |
| T14 | T9,T10,T11,T12 | T16 | T15 |
| T15 | T9,T10,T11,T12 | T16 | T14 |
| T16 | T13,T14,T15 | F1-F4 | — |
| T17 | T16 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Create McpConfigEntry + McpConfigSnapshot dataclasses
  What to do / Must NOT do: Create new file `src/agentpool/mcp_server/config_snapshot.py` with `McpConfigEntry` (frozen dataclass: `server_config: BaseMCPServerConfig`, `source: Literal["pool","agent","session","skill"]`, `skill_name: str | None = None`) and `McpConfigSnapshot` (frozen dataclass: `pool_configs`, `agent_configs`, `session_configs`, `skill_configs` as `tuple[McpConfigEntry, ...]`). Properties: `all_configs`, `global_configs`, `session_scoped_configs`. Methods: `with_skill_configs(skills)`, `with_session_configs(sessions)` — both return new frozen instances. Must NOT add any runtime connection logic. Must NOT use `hasattr` or `getattr`.
  Parallelization: Wave 1a | Blocked by: — | Blocks: T2,T3,T4,T5,T6
  References (executor has NO interview context - be exhaustive): Design spec `docs/superpowers/specs/2026-07-01-mcp-provider-lifecycle-architecture-design.md` sections 1-2 (lines 101-190). `BaseMCPServerConfig` at `src/agentpool_config/mcp_server.py`. Project uses Python 3.13+, `from __future__ import annotations`, Google-style docstrings, mypy --strict.
  Acceptance criteria (agent-executable): `uv run ruff check src/agentpool/mcp_server/config_snapshot.py` passes. `uv run mypy src/agentpool/mcp_server/config_snapshot.py` passes. `python -c "from agentpool.mcp_server.config_snapshot import McpConfigSnapshot, McpConfigEntry; print('OK')"` succeeds.
  QA scenarios (name the exact tool + invocation): happy: import and instantiate snapshot, verify frozen=True via `dataclasses.is_dataclass` + `McpConfigSnapshot.__dataclass_fields__`. failure: attempt to mutate frozen field → `FrozenInstanceError`. Evidence .omo/evidence/task-1-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): add McpConfigEntry and McpConfigSnapshot dataclasses

- [x] 2. Add _build_pool_configs() + _build_agent_configs() + _mcp_snapshot to Agent
  What to do / Must NOT do: Add `_mcp_snapshot: McpConfigSnapshot | None = None` and `_session_connection_pool: SessionConnectionPool | None = None` attributes to `Agent` class (at `src/agentpool/agents/native_agent/agent.py`). Add `_build_pool_configs()` method returning `tuple[McpConfigEntry, ...]` from `self.mcp._servers` (the pool's MCPManager servers). Add `_build_agent_configs()` method returning `tuple[McpConfigEntry, ...]` from the agent's own `mcp_servers` config. Must NOT remove existing `self.mcp` attribute. Must NOT break standalone agent creation (snapshot is None when no pool).
  Parallelization: Wave 1b | Blocked by: T1 | Blocks: T9,T10 | Can parallelize with: T3,T4,T5,T6
  References: `src/agentpool/agents/native_agent/agent.py` — Agent class definition. `src/agentpool/messaging/messagenode.py:128-139` — `self.mcp = agent_pool.mcp` (pool-level MCP sharing). `src/agentpool_config/mcp_server.py` — `BaseMCPServerConfig.client_id` field. `src/agentpool/mcp_server/manager.py` — `MCPManager._servers` dict. Draft findings: `_build_pool_configs` and `_build_agent_configs` DO NOT EXIST (zero matches confirmed).
  Acceptance criteria: `uv run ruff check src/agentpool/agents/native_agent/agent.py` passes. `uv run mypy src/agentpool/agents/native_agent/agent.py` passes. `uv run pytest tests/agents/native_agent/ -q` passes (existing tests).
  QA scenarios: happy: create agent from config with mcp_servers, call `_build_agent_configs()`, verify entries have correct source="agent". failure: standalone agent (no pool) → `_mcp_snapshot` is None, `_build_pool_configs()` returns (). Evidence .omo/evidence/task-2-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): add _mcp_snapshot and config builder methods to Agent

- [x] 3. Modify get_or_create_session_agent() to build snapshot at agent creation
  What to do / Must NOT do: In `src/agentpool/orchestrator/core.py` `get_or_create_session_agent()` (lines 930-1098), add snapshot building after `agent.__aenter__()` for ALL 3 paths (child native L976-1032, main native L1034-1069, non-native L1071-1098). For child sessions: `pool_configs` from parent snapshot, `agent_configs` from child's YAML config via `_build_agent_configs(cfg)`, `session_configs` INHERITED from parent's `session_configs`, `skill_configs=()`. For main sessions: `pool_configs` from `_build_pool_configs()`, `agent_configs` from `_build_agent_configs(cfg)`, `session_configs=()`, `skill_configs=()`. Set `agent._mcp_snapshot = snapshot` and `agent._session_connection_pool = SessionConnectionPool(session_id)`. Must NOT add MCP providers to `agent.tools.providers` — MCP bypasses providers. Must NOT remove non-MCP providers (skills instruction, skills tools).
  Parallelization: Wave 1b | Blocked by: T1 | Blocks: T9,T10 | Can parallelize with: T2,T4,T5,T6
  References: `src/agentpool/orchestrator/core.py:930-1098` — 3 paths. `src/agentpool/orchestrator/core.py:170-204` — `SessionState` dataclass. Design spec section 7 (lines 390-435). Draft findings: existing `agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())` at L1011 should be REMOVED (MCP no longer goes through providers).
  Acceptance criteria: `uv run ruff check src/agentpool/orchestrator/core.py` passes. `uv run mypy src/agentpool/orchestrator/core.py` passes. `uv run pytest tests/orchestrator/ -q` passes.
  QA scenarios: happy: create child session agent, verify `agent._mcp_snapshot.session_configs` matches parent's. failure: parent without snapshot → child gets empty session_configs. Evidence .omo/evidence/task-3-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): build McpConfigSnapshot at session agent creation with child inheritance

- [x] 4. Modify resume_session() session/load semantics
  What to do / Must NOT do: In `src/agentpool_server/acp_server/session_manager.py` `resume_session()` (lines 199-272), add active-session check: if `session_id` in `self._acp_sessions`, log info "Session already active, ignoring session/load mcpServers" and return existing session. For stored sessions being restored: pass `mcp_servers` to `ACPSession` constructor, call `session.initialize_mcp_servers()` to build snapshot from `mcpServers` param. Must NOT merge mcpServers into an already-active session's snapshot. Must NOT call `initialize_mcp_servers()` on an active session.
  Parallelization: Wave 1b | Blocked by: T1 | Blocks: — | Can parallelize with: T2,T3,T5,T6
  References: `src/agentpool_server/acp_server/session_manager.py:199-291` — `resume_session()`. Design spec section 9 (lines 498-523). Draft findings: previous attempted parent MCP server merging in resume_session() was reverted (wrong approach).
  Acceptance criteria: `uv run ruff check src/agentpool_server/acp_server/session_manager.py` passes. `uv run pytest tests/servers/acp_server/ -q` passes.
  QA scenarios: happy: session/load on stored session → snapshot built from mcpServers. failure: session/load on active session → existing snapshot unchanged. Evidence .omo/evidence/task-4-mcp-provider-lifecycle.md
  Commit: Y | fix(acp): session/load ignores mcpServers on active sessions, uses on restore

- [x] 5. Modify initialize_mcp_servers() to build config entries instead of live providers
  What to do / Must NOT do: In `src/agentpool_server/acp_server/session.py` `initialize_mcp_servers()` (lines 446-571), replace live `MCPResourceProvider` creation with `McpConfigEntry` building. For each server: call `convert_acp_mcp_server_to_config(server)` to get config, create `McpConfigEntry(server_config=cfg, source="session")`, append to list. For `AcpMcpServer`: call `self.acp_agent.connect_acp_mcp_server(server)` to get connection, create `AcpMcpTransport(conn)`, store transport in `self.session_connection_pool`. After all servers processed: update `self.agent._mcp_snapshot` via `with_session_configs(merged)`. Remove provider registration on `agent.tools` (`agent.tools.add_provider(mcp_provider)` calls at lines 547-571). Must NOT create `MCPResourceProvider` instances. Must NOT register MCP providers on `agent.tools.providers`.
  Parallelization: Wave 1b | Blocked by: T1 | Blocks: T9 | Can parallelize with: T2,T3,T4,T6
  References: `src/agentpool_server/acp_server/session.py:446-571` — `initialize_mcp_servers()`. `src/agentpool_server/acp_server/session.py:181-184` — `mcp_servers` + `session_mcp_providers` fields. `src/acp/converters.py:61-111` — `convert_acp_mcp_server_to_config()`. Design spec section 8 (lines 437-496).
  Acceptance criteria: `uv run ruff check src/agentpool_server/acp_server/session.py` passes. `uv run pytest tests/servers/acp_server/ -q` passes.
  QA scenarios: happy: call `initialize_mcp_servers()` with AcpMcpServer, verify snapshot has session_configs entry. failure: server init times out → entry not added, other servers still work. Evidence .omo/evidence/task-5-mcp-provider-lifecycle.md
  Commit: Y | refactor(acp): initialize_mcp_servers builds config entries instead of live providers

- [x] 6. Modify get_agentlet() to read from snapshot (bypass providers)
  What to do / Must NOT do: In `src/agentpool/agents/native_agent/agent.py` `get_agentlet()` (around line 806-808), add new MCP path: read `self._mcp_snapshot`, call `self.mcp.as_capability(snapshot=self._mcp_snapshot, session_pool=self._session_connection_pool)`, extend `tool_capabilities`. If snapshot is None (standalone agent), fall back to legacy `await self.mcp.as_capability()`. Must NOT remove existing non-MCP capability building (tools, hooks, deferred bridge, approval bridge, skill capabilities). Must NOT break agents without pool (snapshot=None fallback).
  Parallelization: Wave 1b | Blocked by: T1 | Blocks: T9,T10 | Can parallelize with: T2,T3,T4,T5
  References: `src/agentpool/agents/native_agent/agent.py:806-808` — MCP capability building. `src/agentpool/agents/native_agent/agent.py:809-821` — Skill capability injection (position 5). Design spec section 6 (lines 364-388).
  Acceptance criteria: `uv run ruff check src/agentpool/agents/native_agent/agent.py` passes. `uv run mypy src/agentpool/agents/native_agent/agent.py` passes. `uv run pytest tests/agents/native_agent/ -q` passes.
  QA scenarios: happy: agent with snapshot → `as_capability(snapshot=...)` called. failure: agent without snapshot → legacy path used. Evidence .omo/evidence/task-6-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): get_agentlet reads from McpConfigSnapshot, bypasses providers list

- [x] 7. Implement GlobalConnectionPool with owner-task pattern
  What to do / Must NOT do: Create new file `src/agentpool/mcp_server/global_pool.py` with `GlobalConnectionPool` class. Implement owner-task pattern for stdio: dedicated `asyncio.Task` enters/exits transport context manager, callers signal via `asyncio.Event`. Use `threading.Lock` (NOT `asyncio.Lock`) for thread safety. For HTTP/SSE: create direct transport cached by `client_id`. Ref counting: N acquire → 1 connection, N release → shutdown. LRU eviction at MAX_SESSIONS=256. `asyncio.shield(ready_event.wait())` for init protection. Methods: `get_transport(config) -> ClientTransport`, `release(client_id)`, `shutdown_all(timeout=10.0)`. Must NOT pool ACP-transport servers (raise NotImplementedError). Must NOT use `asyncio.Lock` (use `threading.Lock` per deer-flow pattern).
  Parallelization: Wave 2a | Blocked by: — | Blocks: T9 | Can parallelize with: T8,T11,T12,T13
  References: Design spec section 3 (lines 202-280). deer-flow `MCPSessionPool` pattern: owner-task + `threading.Lock` + `asyncio.shield` + LRU. `src/agentpool_config/mcp_server.py` — `to_transport()` methods on `StdioMCPServerConfig`, `SSEMCPServerConfig`, `StreamableHTTPMCPServerConfig`. `BaseMCPServerConfig.client_id` field.
  Acceptance criteria: `uv run ruff check src/agentpool/mcp_server/global_pool.py` passes. `uv run mypy src/agentpool/mcp_server/global_pool.py` passes. Unit test: acquire/release lifecycle, ref counting, concurrent access.
  QA scenarios: happy: get_transport for stdio → owner task starts, transport returned. release → ref count decremented. failure: owner task crashes → timeout → get_transport raises. Evidence .omo/evidence/task-7-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): implement GlobalConnectionPool with owner-task pattern

- [x] 8. Implement SessionConnectionPool per-session isolation
  What to do / Must NOT do: Create new file `src/agentpool/mcp_server/session_pool.py` with `SessionConnectionPool` class. Per-session, isolated connections. Key: `(config.client_id, skill_name)`. Owner-task pattern for stdio (same as GlobalConnectionPool). HTTP/SSE per-session (not shared but lightweight). Methods: `get_transport(config, skill_name=None) -> ClientTransport`, `add_transport(client_id, transport, skill_name=None)` (for pre-created transports like ACP), `cleanup(timeout=5.0)`. Must NOT share connections across sessions. Must NOT use `asyncio.Lock`.
  Parallelization: Wave 2a | Blocked by: — | Blocks: T9,T10 | Can parallelize with: T7,T11,T12,T13
  References: Design spec section 4 (lines 281-321). Same owner-task pattern as T7. `src/agentpool_server/acp_server/session.py` — `ACPSession.session_connection_pool` field (to be added).
  Acceptance criteria: `uv run ruff check src/agentpool/mcp_server/session_pool.py` passes. `uv run mypy src/agentpool/mcp_server/session_pool.py` passes. Unit test: two sessions → two connections, skill_name key isolation.
  QA scenarios: happy: get_transport for session → isolated connection created. failure: cleanup with timeout → force-cancel remaining. Evidence .omo/evidence/task-8-mcp-provider-lifecycle.md
  Commit: Y | feat(mcp): implement SessionConnectionPool for per-session isolation

- [x] 9. Rewrite MCPManager.as_capability() to use pools
  What to do / Must NOT do: In `src/agentpool/mcp_server/manager.py`, rewrite `as_capability()` to accept `snapshot: McpConfigSnapshot | None = None` and `session_pool: SessionConnectionPool | None = None`. New path (snapshot provided): for `global_configs` → borrow transport from `self._global_pool.get_transport(entry.server_config)`, create fresh `MCPToolset(client=transport, ...)`. For `session_scoped_configs` → borrow from `session_pool.get_transport(entry.server_config, entry.skill_name)`. For ACP entries in session_scoped → use pre-stored transport via `session_pool.add_transport()`. Legacy path (snapshot=None): keep existing behavior (pool servers only). Remove `_toolset_cache` (already removed, verify). Remove `get_aggregating_provider()` (no longer needed — MCP bypasses providers). Must NOT cache MCPToolset instances. Must NOT create MCPResourceProvider instances.
  Parallelization: Wave 2b | Blocked by: T2,T3,T5,T6,T7,T8 | Blocks: T14,T15 | Can parallelize with: T10,T11,T12,T13
  References: `src/agentpool/mcp_server/manager.py:287-353` — current `as_capability()`. Design spec section 5 (lines 323-362). `src/agentpool/mcp_server/manager.py:273-285` — `get_aggregating_provider()` (to be removed). `src/agentpool/agents/native_agent/agent.py:806-808` — caller.
  Acceptance criteria: `uv run ruff check src/agentpool/mcp_server/manager.py` passes. `uv run mypy src/agentpool/mcp_server/manager.py` passes. `uv run pytest tests/mcp_server/ -q` passes.
  QA scenarios: happy: as_capability with snapshot → MCPToolset created from correct pool. failure: pool server down → per-server catch, remaining servers still work. Evidence .omo/evidence/task-9-mcp-provider-lifecycle.md
  Commit: Y | refactor(mcp): rewrite as_capability to use GlobalConnectionPool and SessionConnectionPool

- [x] 10. Remove MCP providers from agent.tools.providers path
  What to do / Must NOT do: Remove all `agent.tools.add_provider(mcp_provider)` calls for MCP: (1) `src/agentpool/orchestrator/core.py:1011-1013` — remove `agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())` from child session path. (2) `src/agentpool_server/acp_server/session.py:547-571` — remove provider registration in `initialize_mcp_servers()` (already done in T5, verify). (3) `src/agentpool_server/acp_server/session.py:719-725` — remove provider registration in send_prompt path. (4) `src/agentpool_server/acp_server/handler.py:421-445` — remove provider registration in session/prompt handler. (5) `src/agentpool_server/acp_server/acp_agent.py:646-649` — remove provider registration. Must NOT remove non-MCP provider registrations (skills instruction, skills tools, pool resource provider). Must NOT break existing test mocks that expect `add_provider` calls for non-MCP providers.
  Parallelization: Wave 2b | Blocked by: T2,T3,T6,T8 | Blocks: T14,T15 | Can parallelize with: T9,T11,T12,T13
  References: Draft findings: 4 re-registration sites. `src/agentpool/orchestrator/core.py:1011-1013`, `src/agentpool_server/acp_server/session.py:547-571`, `src/agentpool_server/acp_server/session.py:719-725`, `src/agentpool_server/acp_server/handler.py:421-445`, `src/agentpool_server/acp_server/acp_agent.py:646-649`.
  Acceptance criteria: `grep -r "add_provider.*mcp" src/agentpool_server/ src/agentpool/orchestrator/` returns zero matches (excluding non-MCP providers). `uv run pytest tests/orchestrator/ tests/servers/acp_server/ -q` passes.
  QA scenarios: happy: agent.tools.providers does NOT contain MCP providers. failure: grep finds MCP provider registration → test fails. Evidence .omo/evidence/task-10-mcp-provider-lifecycle.md
  Commit: Y | refactor(mcp): remove MCP provider registration from agent.tools.providers path

- [x] 11. SkillMcpManager integration with snapshot
  What to do / Must NOT do: (A) Add `to_mcp_server_config()` bridge method to `SkillMcpServerConfig` in `src/agentpool/skills/` — converts to `StdioMCPServerConfig` or `StreamableHTTPMCPServerConfig` based on transport type. (B) Modify `SkillMcpManager` to register configs in snapshot via `agent._mcp_snapshot.with_skill_configs()`. (C) Modify `SkillCapability.get_toolset()` in `src/agentpool/skills/capability.py:98` to read from `SessionConnectionPool` via snapshot. (D) Modify `Pool._on_skills_changed` to broadcast to active session snapshots. Must NOT modify `SkillMcpServerConfig` model itself (only add method). Must NOT break existing skill loading.
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: T14,T15 | Can parallelize with: T9,T10,T12,T13
  References: `src/agentpool/skills/skill_mcp_manager.py:308` — existing `_create_and_connect` bridge. `src/agentpool/skills/capability.py:98` — `SkillCapability.get_toolset()`. `src/agentpool/delegation/pool.py:601-641` — `_rebuild_skill_capabilities`. `src/agentpool/agents/native_agent/agent.py:809-821` — skill capability injection. Draft findings: SkillMcpServerConfig lacks `to_transport()`, `client_id` — bridge conversion needed.
  Acceptance criteria: `uv run ruff check src/agentpool/skills/` passes. `uv run pytest tests/skills/ -q` passes (existing tests).
  QA scenarios: happy: load skill with MCP server → `skill_configs` in snapshot, tools available in get_agentlet(). failure: skill MCP server fails to connect → skill instructions still injected, tools fail at call time. Evidence .omo/evidence/task-11-mcp-provider-lifecycle.md
  Commit: Y | feat(skills): integrate SkillMcpManager with McpConfigSnapshot

- [x] 12. Fix streaming_adapter.py CancelScope error
  What to do / Must NOT do: In `src/agentpool/messaging/streaming_adapter.py` around lines 267-274, fix the `yield inside create_task_group()` bug. Move the consumer loop (yield) outside the task group, keep the producer inside. Follow the same pattern already applied in `base_agent.py:1102-1186` (asyncio.ensure_future producer + consumer outside) and `acp_agent.py:404-609` (asyncio.create_task forwarders + consumer outside). The archived change `fix-cancel-scope-lifecycle` fixed base_agent + acp_agent but MISSED streaming_adapter. Must NOT change the streaming adapter's public API. Must NOT introduce new CancelScope issues.
  Parallelization: Wave 3 | Blocked by: — | Blocks: T14,T15 | Can parallelize with: T7,T8,T9,T10,T11,T13
  References: `src/agentpool/messaging/streaming_adapter.py:267-274` — bug location. `src/agentpool/agents/base_agent.py:1102-1186` — fixed pattern (reference). `src/agentpool_server/acp_server/acp_agent.py:404-609` — fixed pattern (reference). Archived change `fix-cancel-scope-lifecycle`.
  Acceptance criteria: `uv run ruff check src/agentpool/messaging/streaming_adapter.py` passes. `uv run pytest tests/messaging/ -q` passes. New test `test_streaming_adapter_no_cancel_scope` passes.
  QA scenarios: happy: streaming adapter shutdown does not trigger CancelScope RuntimeError. failure: yield inside task group → CancelScope error (before fix). Evidence .omo/evidence/task-12-mcp-provider-lifecycle.md
  Commit: Y | fix(streaming): move yield outside task group to prevent CancelScope error

- [x] 13. Cleanup: remove session_mcp_providers, debug logging, old code
  What to do / Must NOT do: (A) Remove `session_mcp_providers` list from `ACPSession` dataclass (`src/agentpool_server/acp_server/session.py:181-184`). (B) Remove debug logging added during debugging in `src/agentpool/mcp_server/manager.py` (info logging in `get_tools()`, etc.). (C) Remove debug logging in `src/agentpool/resource_providers/base.py` and `src/agentpool/resource_providers/mcp_provider.py`. (D) Remove `get_aggregating_provider()` from `MCPManager` if no longer used (verify with grep first). (E) Remove any temporary fix code from prior patches (e.g., core.py:1011 re-added provider, session.py:532+ provider registration). Must NOT remove production logging. Must NOT remove functionality that is still used.
  Parallelization: Wave 3 | Blocked by: T9,T10 | Blocks: T16 | Can parallelize with: T11,T12
  References: `src/agentpool_server/acp_server/session.py:181-184` — `session_mcp_providers` field. `src/agentpool/mcp_server/manager.py` — debug logging. `src/agentpool/resource_providers/base.py` — debug logging. `src/agentpool/resource_providers/mcp_provider.py` — debug logging.
  Acceptance criteria: `grep -r "session_mcp_providers" src/` returns zero matches. `grep -r "debug" src/agentpool/mcp_server/manager.py` returns zero matches (or only intentional debug-level logging). `uv run ruff check src/` passes. `uv run mypy src/` passes.
  QA scenarios: happy: all tests pass after cleanup. failure: removing still-used code → test failure. Evidence .omo/evidence/task-13-mcp-provider-lifecycle.md
  Commit: Y | chore(mcp): remove session_mcp_providers, debug logging, and dead code

- [x] 14. Write unit tests for new components
  What to do / Must NOT do: Create `tests/mcp_server/test_config_snapshot.py` — test McpConfigEntry (frozen, source validation) and McpConfigSnapshot (immutability, with_skill_configs returns new instance, global_configs/session_scoped_configs partition, dedup by client_id). Create `tests/mcp_server/test_global_pool.py` — test GlobalConnectionPool (owner-task lifecycle, ref counting, concurrent access, LRU eviction, stdio vs HTTP/SSE path selection). Create `tests/mcp_server/test_session_pool.py` — test SessionConnectionPool (per-session isolation, skill_name key isolation, lazy creation, cleanup with timeout, cleanup force-cancel). Use in-process FastMCP servers as fixtures (same pattern as existing `test_mcp_integration.py`). Must NOT create trivial tests (expect true). Must NOT skip failure scenarios.
  Parallelization: Wave 4 | Blocked by: T9,T10,T11,T12 | Blocks: T16 | Can parallelize with: T15
  References: `tests/mcp_server/test_mcp_integration.py` — existing integration test pattern with FastMCP. `tests/mcp_server/test_manager_capability.py` — existing unit test pattern. Design spec testing strategy (lines 650-678).
  Acceptance criteria: `uv run pytest tests/mcp_server/test_config_snapshot.py tests/mcp_server/test_global_pool.py tests/mcp_server/test_session_pool.py -q` all pass. `uv run ruff check tests/mcp_server/test_config_snapshot.py tests/mcp_server/test_global_pool.py tests/mcp_server/test_session_pool.py` passes.
  QA scenarios: happy: all unit tests pass. failure: frozen dataclass mutation → FrozenInstanceError. Evidence .omo/evidence/task-14-mcp-provider-lifecycle.md
  Commit: Y | test(mcp): add unit tests for McpConfigSnapshot, GlobalConnectionPool, SessionConnectionPool

- [x] 15. Write integration tests for MCP provider lifecycle
  What to do / Must NOT do: Create `tests/mcp_server/test_provider_lifecycle.py` with integration tests: `test_child_session_inherits_parent_session_mcp`, `test_child_session_has_own_agent_configs`, `test_child_session_has_pool_configs`, `test_session_load_on_active_ignored`, `test_session_load_on_restore_uses_mcpServers`, `test_get_agentlet_has_all_mcp_tools`, `test_cross_task_no_cancel_scope_error`, `test_stateful_mcp_isolation`, `test_skill_mcp_in_snapshot`, `test_mcp_bypasses_providers_list`, `test_global_pool_ref_counting`, `test_session_pool_cleanup`, `test_global_pool_shutdown`, `test_owner_task_same_task_enter_exit`, `test_streaming_adapter_no_cancel_scope`. Use in-process FastMCP servers + mock ACP sessions. Must NOT create tests that always pass regardless of implementation. Must NOT skip integration scenarios.
  Parallelization: Wave 4 | Blocked by: T9,T10,T11,T12 | Blocks: T16 | Can parallelize with: T14
  References: Design spec testing strategy (lines 660-678). `tests/mcp_server/test_mcp_integration.py` — FastMCP server fixture pattern. `tests/orchestrator/test_sessionpool_subagent_mcp_inheritance.py` — existing subagent MCP tests. `tests/servers/acp_server/test_acp_session_mcp_registration.py` — existing ACP session tests.
  Acceptance criteria: `uv run pytest tests/mcp_server/test_provider_lifecycle.py -q` all pass. `uv run ruff check tests/mcp_server/test_provider_lifecycle.py` passes.
  QA scenarios: happy: all integration tests pass. failure: child session without inherited session_configs → test fails. Evidence .omo/evidence/task-15-mcp-provider-lifecycle.md
  Commit: Y | test(mcp): add integration tests for MCP provider lifecycle and subagent inheritance

- [x] 16. Run full test suite + lint + type checking
  What to do / Must NOT do: Run `uv run ruff check src/` — zero errors. Run `uv run ruff format --check src/` — zero changes needed. Run `uv run --no-group docs mypy src/` — zero issues. Run `uv run pytest -q` — all tests pass (note pre-existing failure `test_inject_prompt_triggers_continuation` is unrelated). Run `uv run pytest -m unit -q` — all unit tests pass. Run grep checks: zero `from pydantic_ai.mcp import MCPServer*` in src/, zero `to_pydantic_ai` in src/, zero `session_mcp_providers` in src/, zero `add_provider.*mcp` in src/ (excluding non-MCP). Must NOT suppress type errors. Must NOT delete failing tests to pass.
  Parallelization: Wave 4 | Blocked by: T13,T14,T15 | Blocks: T17 | Can parallelize with: —
  References: AGENTS.md testing commands.
  Acceptance criteria: All commands exit 0. Grep checks return zero matches.
  QA scenarios: happy: full suite green. failure: any check fails → fix and re-run. Evidence .omo/evidence/task-16-mcp-provider-lifecycle.md
  Commit: N | (verification only, no commit)

- [x] 17. Manual QA with diag-agent-ng.yaml config
  What to do / Must NOT do: Run `agentpool serve-acp xeno-agent/config/diag-agent-ng.yaml` and verify: (1) Parent agent (engineer) has pool + agent + session MCP tools. (2) Spawn subagent (librarian) — subagent has pool + inherited session MCP tools (workspace-fs). (3) Multiple subagents in parallel — each has isolated SessionConnectionPool, no CancelScope errors. (4) Session restore via session/load — restored session has MCP tools from mcpServers param. (5) Skill with MCP server — skill MCP tools available after skill load. Check logs for: zero `RuntimeError: Attempted to exit cancel scope`, zero `GET stream disconnected` loops, all MCP tool calls succeed. Must NOT skip any of the 5 scenarios. Must NOT declare success without running the actual server.
  Parallelization: Wave 4 | Blocked by: T16 | Blocks: F1-F4 | Can parallelize with: —
  References: `xeno-agent/config/diag-agent-ng.yaml` — test config. Pool-level MCP: `knowledge_base` (streamable-http). Agent-level MCP (engineer): `expert-anno` (streamable-http). Session-level MCP (from Seed client): `workspace-fs`, `agentic-alg-scratchpad-local` (ACP transport).
  Acceptance criteria: All 5 manual QA scenarios pass. Zero CancelScope errors in logs. Zero MCP tool call failures.
  QA scenarios: happy: all scenarios pass. failure: any scenario fails → document and fix. Evidence .omo/evidence/task-17-mcp-provider-lifecycle.md
  Commit: N | (verification only, no commit)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity

## Commit strategy
- One commit per todo (except T16, T17 which are verification-only)
- Commit messages follow conventional commits: `feat(mcp):`, `fix(acp):`, `refactor(mcp):`, `test(mcp):`, `chore(mcp):`, `fix(streaming):`, `feat(skills):`
- Each commit should be atomic and independently verifiable

## Success criteria
1. Subagents inherit parent's session-level MCP tools (workspace-fs) — no race condition
2. No cross-task CancelScope errors — owner-task pattern for stdio, no caching for HTTP/SSE
3. Pool-level MCP servers shared efficiently via GlobalConnectionPool
4. Session-level MCP servers isolated via SessionConnectionPool
5. Skill MCP servers integrated into snapshot
6. streaming_adapter.py CancelScope bug fixed
7. All existing tests pass + new comprehensive test suite
8. Manual QA with diag-agent-ng.yaml passes all 5 scenarios
