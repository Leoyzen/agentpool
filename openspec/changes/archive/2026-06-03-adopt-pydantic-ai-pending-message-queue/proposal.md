## Why

AgentPool's session orchestration currently tracks active agent runs through scattered, fragile mechanisms:
- `SessionState.active_run_ctx`: a manually-synchronized pointer to the ephemeral `AgentRunContext`
- `SessionState.turn_lock`: an `asyncio.Lock` used both for turn serialization and for graceful close-session waiting
- `TurnRunner._post_turn_injections` / `_post_turn_prompts`: manual queues for follow-up prompts after a turn ends
- `AgentRunContext.injection_manager`: a `PromptInjectionManager` with TWO distinct responsibilities:
  1. **`inject()`/`consume()` in `after_tool_execute`**: tools inject additional context into their own results (wrapped in `<injected-context>` tags)
  2. **`queue()`/`pop_queued()`**: queues follow-up prompts to be processed after the current run completes

There is **no unified, pool-level view** of active agent runs. To check if any runs are active, one must iterate all sessions and inspect `active_run_ctx`. To cancel a run, one must know its session ID. There is no `list_active_runs()`, `cancel_run_by_id()`, or `max_concurrent_runs` enforcement.

This is in contrast to xeno-agent's `BackgroundTaskManager`, which provides a unified registry for all background tasks: creation, tracking, cancellation, and enumeration from a single authority.

**Why now**: PydanticAI v1.101.0+ (already at 1.102.0 in this project) introduced a native **pending message queue** via `AgentRun.enqueue(*content, priority='asap' | 'when_idle')` and `RunContext.enqueue()`. This replaces only the **follow-up prompt queuing** part of `PromptInjectionManager` (responsibility #2 above). The **tool result augmentation** part (responsibility #1) is NOT replaced by PydanticAI's queue and must be preserved.

This gives us an opportunity to:
1. Add pool-level run tracking via `SessionPool._runs` (all agent types)
2. Eliminate the fragile `active_run_ctx` pointer
3. Replace native agents' manual **follow-up prompt queue** with PydanticAI's native queue
4. Keep non-native agents' manual queue system via a compatibility layer
5. Preserve `PromptInjectionManager.inject()`/`consume()` for tool result augmentation across all agents

**Critical scoping note**: PydanticAI's pending message queue is **only available to native agents**. Non-native agents (ACP, ClaudeCode, AGUI) continue using the existing manual queue system. The unified run tracking, however, covers **all agent types**.

## What Changes

### Phase 1: Unified Run Tracking (All Agent Types) — Foundation
- **NEW**: `SessionPool._runs: dict[str, RunHandle]` — pool-level run tracking
- **NEW**: `RunHandle` as a first-class ephemeral object: `run_id`, `status`, `run_ctx`, `agent_type`, `session_id`, `created_at`, `completed_at`, `complete_event`
- **NEW**: `AgentPool` exposes `list_active_runs()`, `cancel_run(run_id)`, `get_run(run_id)` via `SessionPool`
- **NEW**: `SessionPool.max_concurrent_runs` — optional pool-level concurrency limit
- **BREAKING**: Remove `SessionState.active_run_ctx`; replace with `SessionState.current_run_id: str | None`
- **BREAKING**: `close_session()` awaits `RunHandle.complete_event` with timeout instead of acquiring `turn_lock`
- **BREAKING**: `MetricsCollector` uses `SessionPool.active_runs` instead of `turn_lock.locked()`
- **KEEP**: `SessionState.turn_lock` is retained for **non-native agents** (native agents no longer use it)
- **KEEP**: `AgentRunContext.injection_manager` is retained for **tool result augmentation** (`inject()`/`consume()`); made optional (`PromptInjectionManager | None`) for native agents that don't use it for follow-up queuing

### Phase 2: Native Agent Queue Simplification (Native Agents Only) — Build on Phase 1
- **BREAKING (Native only)**: Replace `BaseAgent._run_stream_once()` with PydanticAI's `agent.iter()` + `agent_run.next()` loop
- **BREAKING (Native only)**: Remove `TurnRunner._post_turn_prompts`, `_injection_locks`, `queue_prompt()`, `_process_queued_work()`, `_trigger_auto_resume()` for native agents
- **BREAKING (Native only)**: Remove `AgentRunContext.injection_manager.queue()`/`pop_queued()` for native agents (PydanticAI's `enqueue(priority='when_idle')` replaces follow-up prompt queuing)
- Refactor native-agent execution: `TurnRunner` → `RunExecutor` (drives `agent.iter()` loop, maps PydanticAI events to EventBus)

### 3. Non-Native Agent Compatibility (Non-Native Agents Only)
- Extract current `TurnRunner` queue logic into `LegacyTurnRunner` for non-native agents
- Non-native agents continue using `LegacyTurnRunner.inject_prompt()` / `queue_prompt()`
- Both native and non-native runs produce `RunHandle` objects tracked in `SessionPool._runs`
- `LegacyTurnRunner` continues using `SessionState.turn_lock` for turn serialization

### 4. Unified Request Router (All Agent Types)
- Refactor `SessionController` to become the single request entry point:
  - `receive_request(session_id, content, priority)` → fire-and-forget
  - Checks agent type, routes to native (`RunExecutor` + PydanticAI `enqueue()`) or non-native (`LegacyTurnRunner`)
  - Creates `RunHandle` and adds to `SessionPool._runs` for both paths
- Protocol handlers (`acp_server`, `opencode_server`, `mcp_server`) call `SessionController.receive_request()` for native agents; non-native handlers unchanged

## Capabilities

### New Capabilities
- `unified-run-tracking`: Pool-level run tracking with creation, cancellation, and enumeration for all agent types via `SessionPool._runs`
- `pending-message-queue`: PydanticAI native `enqueue()` for native agents' follow-up prompts; non-native agents continue with manual queue

### Modified Capabilities
- `sessionpool-only-execution`: `SessionController` becomes unified request router with agent-type-aware dispatch. `TurnRunner` replaced by `RunExecutor` (native) and `LegacyTurnRunner` (non-native)
- `runctx-session-binding`: `AgentRunContext` loses `injection_manager` follow-up queuing for native agents; `SessionState` loses `active_run_ctx`. Interrupt/cancel logic uses `RunHandle` via `SessionPool`

## Impact
- `src/agentpool/orchestrator/core.py`: Major refactor — `SessionController`, `SessionState`, `TurnRunner` extraction
- `src/agentpool/orchestrator/run.py`: New file for `RunHandle`
- `src/agentpool/orchestrator/run_executor.py`: New file for native-agent `RunExecutor`
- `src/agentpool/orchestrator/legacy_runner.py`: New file for non-native `LegacyTurnRunner`
- `src/agentpool/agents/base_agent.py`: `inject_prompt()` / `queue_prompt()` delegate to `SessionController` for native agents
- `src/agentpool/agents/context.py`: Make `injection_manager` optional (`PromptInjectionManager | None`)
- `src/agentpool/metrics.py`: Update active-turn counting to use `SessionPool.active_runs`
- `pyproject.toml` / `uv.lock`: Already at `pydantic-ai==1.102.0` (no change needed)
- Protocol handlers: Native-agent paths use `SessionController.receive_request()`; non-native paths unchanged
- Tests: Add `RunHandle` + `SessionPool._runs` tests; replace native-agent auto-resume tests with PydanticAI equivalents; add non-native compatibility tests

## Breaking Changes
**For all agents:**
1. `SessionState.active_run_ctx` removed; `SessionState.current_run_id` replaces it
2. `close_session()` behavior changes: awaits `RunHandle.complete_event` instead of `turn_lock`
3. `MetricsCollector` metric source changes
4. `SessionPool.process_prompt()` becomes fire-and-forget; runtime exceptions published as `RunFailedEvent` on EventBus instead of propagating synchronously

**For native agents only:**
5. `BaseAgent.inject_prompt()` / `queue_prompt()` implementation changes: delegate to `SessionController` → PydanticAI `enqueue()` for follow-up prompts
6. `AgentRunContext.injection_manager` loses `queue()`/`pop_queued()` for follow-up prompts (tool result augmentation via `inject()`/`consume()` preserved)
7. `_run_stream_once()` internal while loop removed
8. Event stream behavior changes: PydanticAI handles follow-up prompt queuing internally

**For non-native agents:** None. All non-native paths are transparent. `turn_lock` is retained.

## Acceptance Criteria
1. `SessionPool.active_runs` returns all active runs across all sessions and agent types
2. `SessionPool.cancel_run(run_id)` cancels any active run (native or non-native)
3. `AgentPool.list_active_runs()` delegates to `SessionPool`; handles `session_pool is None` gracefully
4. Native agent `inject_prompt()` triggers within same run iteration (no nested turn)
5. Multiple concurrent `inject_prompt()` calls for native agents don't race or deadlock
6. `SessionState.current_run_id` correctly tracks run lifecycle for all agent types
7. `RunHandle` is first-class: has lifecycle events, tracks state, can be awaited/cancelled; cleanup callback (`_cleanup_run()`) sets `complete_event` after all cleanup
8. Per-session lock prevents concurrent `run()` calls on same session
9. `close_session()` gracefully waits for run completion (with timeout) for all agent types
10. `close_session()` rejects new `receive_request()` calls after `closing=True` is set
11. `max_concurrent_runs` enforces pool-level run limit when configured
12. Non-native agents continue working unchanged with `turn_lock` still present
13. Tool result augmentation via `PromptInjectionManager.inject()`/`consume()` still works for native agents
14. `BaseAgent._get_session_run_ctx()` finds `RunHandle` via `SessionPool._runs` instead of `session.active_run_ctx`
15. Tests pass: all existing + new `RunHandle`, `enqueue`, event mapping, non-native compatibility tests

## Risks
| Risk | Impact | Mitigation |
|------|--------|-----------|
| `SessionPool._runs` dict operations under high concurrency | Low | Dict ops are atomic in CPython; fine-grained locking per run_id if needed |
| PydanticAI `enqueue()` behavior differs from assumptions | High | Prototype before full implementation (task 1.3); add extensive tests |
| Non-native agents accidentally affected | Medium | Explicit agent-type checks; `turn_lock` retained; dedicated compatibility tests |
| `close_session()` hang if run never completes | Medium | Configurable timeout; force close after timeout via `cancel_run()` |
| Memory leak: `SessionPool._runs` retains completed runs | Medium | Cleanup callback removes from dict; add max_history limit |
| Event mapping loses information | Medium | Prototype event mapping (task 1.3) before implementing `RunExecutor` |
| Two queue systems (PydanticAI + manual) creates confusion | Low | Clear documentation; agent-type-aware routing is explicit in code |

## Implementation Strategy
**Phase 1** (lower risk): Add `RunHandle` + `SessionPool._runs`, keep existing manual queues for ALL agents. Validate pool-level tracking, cancellation, and enumeration work across all agent types.
**Phase 2** (higher risk): Migrate native agents to PydanticAI `enqueue()` for follow-up prompts, extract `LegacyTurnRunner` for non-native agents. Requires successful Phase 1 + event mapping prototype.
