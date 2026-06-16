## Why

AgentPool currently has two parallel execution paths for native agent streaming: `_run_agentlet_core()` (used by `_stream_events()` → `TurnRunner`) and `RunExecutor.execute()` (dead code, never instantiated in production). This bifurcation causes real bugs: `_run_agentlet_core()` never sets `RunHandle.active_agent_run`, so `TurnRunner.steer()`/`followup()` always falls through to the idle path, leaving steer/followup messages undelivered for native agents. Separately, the standalone `agent.run_stream()` path still uses `merge_queue_into_iterator` for event merging, while the SessionPool path uses EventBus — a dual-path architecture that the `sessionpool-only-architecture` proposal intended to eliminate but never completed.

## What Changes

- **BREAKING**: `_run_agentlet_core()` is removed. `_stream_events()` and `_execute_node()` both use `RunExecutor.execute()` as the sole native agent execution engine.
- **BREAKING**: `merge_queue_into_iterator` is removed from the native agent path. All event routing goes through EventBus.
- `RunExecutor` gains the missing features from `_run_agentlet_core()`: `emitted_tool_starts` dedup (both `FunctionToolCallEvent` and `PartStartEvent(BaseToolCallPart)`), cancelled-response fallback, concurrent-run warning.
- `RunExecutor` correctly manages `RunHandle.active_agent_run` (already implemented), fixing the steer/followup delivery bug.
- `_stream_events()` is refactored to use `RunExecutor.execute()` instead of wrapping `_run_agentlet_core()` in a background task.
- `BaseAgent.run_stream()` already delegates to `SessionPool.run_stream()` when pooled (lines 901-939). The `_execute_direct()` fallback path is removed; unpooled `run_stream()` raises `RuntimeError`.
- Migratable standalone callers (Vercel serve, OpenAI API server, ACP server session) are migrated to `SessionPool.run_stream()`.
- `_consume_event_queue` background task in TurnRunner is gated behind `agent_type != "native"` (becomes a no-op for native agents).

## Capabilities

### New Capabilities

- `unified-native-execution`: All native agent streaming goes through `RunExecutor.execute()`. There is exactly one pydantic-ai iteration loop implementation.
- `sessionpool-standalone-migration`: Direct `agent.run_stream()` callers that can migrate use `SessionPool.run_stream()`. The `_execute_direct()` fallback is removed.

### Modified Capabilities

- `sessionpool-only-execution`: Extends the existing spec — `merge_queue_into_iterator` usage in native agents is removed. `_execute_direct()` fallback removed.
- `unified-event-routing`: EventBus becomes the exclusive event routing mechanism for native agents. `merge_queue_into_iterator` is removed from native agents. `_consume_event_queue` is gated for native agents.

## Impact

- `agentpool/agents/native_agent/agent.py`: Remove `_run_agentlet_core()`. Refactor `_stream_events()` and `_execute_node()` to use `RunExecutor.execute()`. Remove `merge_queue_into_iterator` import and usage.
- `agentpool/orchestrator/run_executor.py`: Add `emitted_tool_starts` dedup (both `FunctionToolCallEvent` and `PartStartEvent`), cancelled-response fallback, concurrent-run warning. Wire `RunStartedEvent` with `session_id` and `parent_session_id`. Add `parent_id` parameter.
- `agentpool/agents/base_agent.py`: Remove `_execute_direct()` fallback from `run_stream()`. Remove manual follow-up loop for native agents.
- `agentpool/orchestrator/core.py`: Gate `_consume_event_queue` behind `agent_type != "native"` in TurnRunner.
- `agentpool_cli/serve_vercel.py`: Migrate `agent.run_stream()` → `session_pool.run_stream()`.
- `agentpool_server/openai_api_server/completions/helpers.py`: Same migration.
- `agentpool_server/acp_server/session.py`: Formalize existing SessionPool routing (already routes through SessionPool via `BaseAgent.run_stream()` delegation).
- `agentpool/utils/streams.py`: `merge_queue_into_iterator` stays (ACP agent at `acp_agent.py:490` still uses it). Only native agent import removed.
- Tests: All test callers of `_run_agentlet_core()` migrate to `RunExecutor.execute()`. Tests using `agent.run_stream()` directly migrate to `session_pool.run_stream()`.

## Non-Goals (Explicitly Excluded)

- **AG-UI adapter** (`agui_server/base_agent_adapter.py:126`): Already routes through SessionPool via `BaseAgent.run_stream()` delegation. No migration needed.
- **Streaming tools** (`streaming_tools.py:119,164`): Uses `message_history=fork_history` and runs inside turn context. `SessionPool.run_stream()` doesn't support forked history. Not migrated.
- **FSSpec toolset** (`fsspec_toolset/toolset.py:1310,1438,1524`): Same fork-history + turn-context pattern. Not migrated.
- Removing `merge_queue_into_iterator` from ACP agent or Claude Code agent (separate concern).

## Known Regressions

After `_execute_direct()` removal (task 6.1), callers that currently rely on the fallback path will break:

- **`streaming_tools.py`**: Uses `message_history=fork_history` via `agent.run_stream()`. Without `_execute_direct()`, this will raise `RuntimeError` if SessionPool is unavailable, or fail with incorrect history if SessionPool is available (doesn't support `fork_history`). **Fix**: Follow-up change to add `message_history` support to `SessionPool.run_stream()`.
- **`fsspec_toolset/toolset.py`**: Same fork-history pattern. Same follow-up needed.

These regressions are accepted as a trade-off to eliminate the dual-path architecture. The follow-up to add `message_history` support to SessionPool is tracked separately.
