## Why

AgentPool's session and turn management is currently duplicated across ACP and OpenCode protocol handlers, each independently implementing per-session agent registries (`_session_agents`), agent lifecycle, and turn loops. This leads to code duplication, inconsistent concurrency safety, and makes it impossible to reliably handle post-turn work (Issue #39: BackgroundTaskProvider async mode fails to resume lead agents after subagent completion).

We need a unified runtime session management layer that decouples protocol handlers from agent lifecycle management, enforces turn serialization per session, and provides reliable cross-turn event routing.

## What Changes

- **New `orchestrator/` package**: Introduces `SessionPool`, `SessionController`, `TurnRunner`, and `EventBus` — a unified runtime layer for session and turn management
- **Feature flag integration**: `AgentPool` optionally composes `SessionPool`; per-protocol toggles (`acp.use_session_pool`, `opencode.use_session_pool`) enable gradual rollout
- **ACP handler migration**: New `ACPProtocolHandler` replaces duplicated session/agent management in `AgentPoolACPAgent`; old code preserved behind feature flag
- **OpenCode handler migration**: New `OpenCodeProtocolHandler` replaces `ServerState._session_agents`; old code preserved behind feature flag
- **BaseAgent API extension**: Adds `get_active_run_context()` public API to eliminate getattr chains and support external turn orchestration
- **Backward compatibility**: All changes are opt-in via feature flags; existing code paths remain unchanged when disabled

## Capabilities

### New Capabilities
- `session-pool-core`: Core session pool infrastructure including `EventBus` (bounded queues with dropping), `SessionController` (per-session agent lifecycle), `TurnRunner` (turn loop + auto-resume), and `SessionPool` (high-level facade)
- `agent-pool-integration`: AgentPool optional composition with SessionPool, YAML configuration schema for session pool settings, and per-protocol feature flags
- `acp-session-pool-handler`: ACP protocol handler using SessionPool with persistent cross-turn event consumer
- `opencode-session-pool-handler`: OpenCode protocol handler using SessionPool with persistent SSE event consumer

### Modified Capabilities
- *(none — this change introduces new infrastructure without altering existing capability requirements)*

## Impact

- **New modules**: `src/agentpool/orchestrator/` (SessionPool core)
- **Modified modules**:
  - `src/agentpool/delegation/pool.py` — optional SessionPool composition
  - `src/agentpool/agents/base_agent.py` — `get_active_run_context()` public API
  - `src/agentpool_server/acp_server/` — new handler + feature flag branch
  - `src/agentpool_server/opencode_server/` — new handler + feature flag branch
- **Configuration**: New `session_pool` section in YAML config
- **Risk**: Low — feature flags ensure complete backward compatibility; canary deployment supported
