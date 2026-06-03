## Context

AgentPool currently has session management logic duplicated across ACP and OpenCode protocol handlers:

- **ACP**: `AgentPoolACPAgent._session_agents` with double-checked locking, per-session agent creation from `NativeAgentConfig`, `ACPSessionManager` for persistence
- **OpenCode**: `ServerState._session_agents` with nearly identical logic, `ensure_session()` for store-first resolution

Both implement:
- Per-session agent registries
- Agent lifecycle (create, cache, cleanup)
- Event streaming via `async for event in agent.run_stream()` (tight coupling)

**Problems**:
1. **Code duplication**: Same `_session_agents` / `get_or_create_session_agent()` pattern in two places
2. **No turn serialization**: Concurrent prompts to the same session can corrupt agent state
3. **Lost events**: Background task events between turns are lost because event consumer is tied to the `run_stream()` iterator
4. **Issue #39**: Post-turn injections (from `BackgroundTaskProvider` async mode) fail when no active turn exists

**Existing infrastructure**:
- `sessions/manager.py` — `SessionManager` for persistence (RFC-0028)
- `sessions/models.py` — `SessionData` / `ProjectData` schemas
- `sessions/store.py` — `SessionStore` protocol
- `BaseAgent._run_stream_once()` — single-turn implementation (RFC-0021)
- `BaseAgent._active_run_ctx` — cross-task run context access

**Constraints**:
- Python 3.13+, strict typing, no `getattr`/`hasattr`
- Must maintain backward compatibility via feature flags
- Must support canary deployment per protocol

## Goals / Non-Goals

**Goals:**
1. Extract duplicated session/agent management into a unified `SessionPool` layer
2. Enforce "1 turn per session" serialization via `SessionState.turn_lock`
3. Decouple event production from consumption via `EventBus` (persistent subscribers)
4. Support post-turn auto-resume to fix Issue #39
5. Enable gradual rollout via feature flags (per-protocol)
6. Provide observability (metrics, queue depth, turn latency)

**Non-Goals:**
- Replacing `sessions/` data persistence layer (coexists with `orchestrator/` runtime layer)
- Modifying `SessionManager` or `SessionData` schemas
- Changing AG-UI or OpenAI API servers (stateless, no session management needed)
- Agent-level stateless refactor (RFC-0024, deferred)
- Session persistence across process restarts (future enhancement)

## Decisions

### Decision 1: New `orchestrator/` package instead of extending `sessions/`

**Rationale**: `sessions/` is the data persistence layer (RFC-0028). `orchestrator/` is the runtime layer. They serve different purposes and can coexist. Mixing them would create confusion.

**Alternatives considered**:
- Extend `sessions/manager.py`: Rejected — would conflate data and runtime concerns
- Create `runtime/` or `session_pool/`: Rejected — `orchestrator/` is already used in the architecture doc and clearly indicates orchestration responsibility

### Decision 2: Feature flags with per-protocol granularity

**Rationale**: Enables independent canary deployment for ACP and OpenCode. A bug in one handler doesn't affect the other.

**Design**:
```yaml
session_pool:
  enabled: false          # Master switch
  auto_resume: true
  event_bus: true
  max_auto_resume: 10
  max_queue_size: 1000
  session_ttl_seconds: 3600

acp:
  use_session_pool: false

opencode:
  use_session_pool: false
```

### Decision 3: EventBus with bounded queues and dropping strategy

**Rationale**: Prevents OOM under load. Slow consumers shouldn't block the entire system.

**Design**:
- Default max queue size: 1000
- Drop oldest event when queue full
- Sentinel (`None`) for graceful shutdown
- Shallow copy events per subscriber to prevent mutation side effects

### Decision 4: Turn serialization at session level (not agent level)

**Rationale**: The constraint is "1 turn per session", not "1 turn per agent". Multiple sessions can use the same shared agent concurrently (though per-session agents are preferred).

**Design**:
- `SessionState.turn_lock: asyncio.Lock` — each session has its own lock
- `TurnRunner.run_loop()` acquires `turn_lock` before running turns
- `TurnRunner.run_turn()` acquires `turn_lock` for single turns

### Decision 5: Auto-resume as explicit loop in TurnRunner (not in BaseAgent)

**Rationale**: Moving the loop out of `BaseAgent` makes it observable, controllable, and testable. It also enables the EventBus decoupling.

**Design**:
- `TurnRunner.run_loop()` runs initial turn + auto-resume iterations
- `_process_queued_work()` drains post-turn injections/prompts and runs additional turns
- Configurable `max_auto_resume` (default 10) prevents infinite loops

### Decision 6: Session TTL cleanup for injection lock accumulation (P1.2)

**Rationale**: Per-session injection locks can accumulate if sessions are not properly closed. TTL cleanup prevents memory leaks.

**Design**:
- Background task scans for expired sessions every `session_ttl_seconds / 2`
- Expired sessions are closed (releases locks, cleans up queues)

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| EventBus queue overflow drops events | High | Bounded queues with monitoring; alert on queue depth; consumers should keep up |
| Auto-resume infinite loop | Medium | `max_auto_resume` hard limit; logging at warning level |
| ACP handler complexity (~400 lines) | Medium | Incremental implementation; MVP first; thorough testing |
| OpenCode `state.py` coupling | Medium | Discovery phase before migration; retain `state.py` functions that don't overlap |
| Feature flag misconfiguration | Low | Validation at startup; clear documentation |
| Performance regression | Medium | Phase 1 benchmarks; p99 EventBus latency < 10ms target |
| Concurrent session limit (MCP processes) | Medium | `mcp_max_processes` hard limit; fallback to shared agent |

## Migration Plan

### Phase 1: Infrastructure (5-6 weeks)
1. Implement `orchestrator/core.py` (SessionPool, SessionController, TurnRunner, EventBus)
2. Implement `orchestrator/metrics.py`
3. Add `AgentPool` integration with feature flags
4. Add YAML config schema
5. Stress tests (100 concurrent sessions)
6. Performance benchmarks

### Phase 2: ACP Migration (3-4 weeks)
1. Create `ACPProtocolHandler`
2. Add `acp.use_session_pool` feature flag
3. Canary deployment: 1% → 10% → 50% → 100%
4. Remove old code after validation

### Phase 3: OpenCode Migration (3-4 weeks)
1. Analyze `state.py` coupling
2. Create `OpenCodeProtocolHandler`
3. Add `opencode.use_session_pool` feature flag
4. Canary deployment
5. Remove old code after validation

### Phase 4: Validation (2-3 weeks)
1. End-to-end Issue #39 verification
2. Performance regression testing
3. Memory leak detection
4. Monitoring and alerting setup
5. Operational runbook

### Rollback
- Set `session_pool.enabled: false` or per-protocol `use_session_pool: false`
- Old code paths remain in place until explicitly removed

## Open Questions

1. **AG-UI adaptation**: AG-UI is stateless per-request. Confirmed: no migration needed.
2. **OpenAI API adaptation**: Also stateless per-request. Confirmed: no migration needed.
3. **Cross-protocol session isolation**: Session IDs prefixed by protocol handler (e.g., `acp:session_123`). Confirmed: SessionPool doesn't manage prefixes.
4. **Event mutability**: Events are mutable dataclasses. Mitigation: shallow copy in EventBus.publish(). Long-term: frozen dataclass (Phase 5).
5. **Team/Subagent session propagation**: RFC-0028 handles child session creation. Confirmed: `orchestrator/` operates at a different layer and doesn't conflict.
