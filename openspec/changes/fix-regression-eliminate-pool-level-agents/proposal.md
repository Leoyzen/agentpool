## Why

The `eliminate-pool-level-agents` branch introduced ~120+ test failures, 14 errors, and significant static analysis regressions (267 ruff errors, 337 mypy errors, 181 ty diagnostics). The most critical issue is a regression of the EventBus `receive()` → `get()` fix (RC-2 from `feat/run-turn-separation`), which alone causes 58 opencode_server test failures. Additionally, the pool-level agent removal broke worker/subagent tools (20 failures), base agent API (14 errors), executor (7 failures), cross-provider session lifecycle (7 failures), and RunHandle lifecycle (5 failures). Without fixing these regressions, the branch cannot be merged.

## What Changes

- **Fix EventBus stream API regression**: Replace `receive()` with `get()`, `receive_nowait()` with `get_nowait()`, and `send_nowait()` with `put_nowait()` in `global_routes.py` and `core.py` (TC-1, ~58 failures)
- **Fix RunHandle lifecycle callbacks**: Restore cleanup callback invocation in `RunHandle.complete()` and `RunHandle.fail()` (TC-9, ~5 failures)
- **Fix base agent API for pool-less operation**: Update `BaseAgent` run context and turn-active APIs to work without pool-level agent registration (TC-2/TC-3, ~14 errors + 7 failures)
- **Fix worker/subagent tool session creation**: Update subagent tools to create sessions without pool-level agent registry (TC-5, ~20 failures)
- **Fix executor/running module**: Update `agentpool.running` executor to work with new pool architecture (TC-6, ~7 failures)
- **Fix cross-provider session lifecycle**: Restore child session creation, parent ID propagation, and depth tracking without pool-level agents (TC-7, ~7 failures)
- **Fix messaging/signal system**: Update signal forwarding and message piping for pool-less architecture (TC-8, ~7 failures)
- **Fix ACP server MagicMock issues**: Correct mock setups for `session_store.load()` async compatibility (TC-13, ~2+ failures)
- **Clean up stale test API references**: Migrate `session_pool.turns`, `_run_stream_once` references to new APIs (TC-14)
- **Mark flaky/slow tests**: Add `@pytest.mark.flaky` to performance tests, `@pytest.mark.slow` to e2e tests (TC-10)
- **Run ruff auto-fix and format**: Apply `ruff check --fix` and `ruff format` to clear 21 auto-fixable issues + 64 format issues
- **Clean mypy unused-ignore**: Remove 51 redundant `# type: ignore` comments

## Capabilities

### New Capabilities

_(None — this change fixes regressions in existing capabilities, not introducing new ones.)_

### Modified Capabilities

- `session-orchestration`: RunHandle lifecycle callbacks must be invoked in `complete()` and `fail()`; EventBus stream API must use `asyncio.Queue` methods (`get`/`get_nowait`/`put`/`put_nowait`) consistently
- `sessionpool-only-execution`: Worker/subagent tools must create child sessions without pool-level agent registry; executor must work with pool-less agent architecture
- `unified-session-lifecycle`: Cross-provider session lifecycle (child session creation, parent ID propagation, depth tracking) must work without pool-level agent registration
- `eventbus-single-subscriber-per-session`: EventBus stream consumer must use `get()` not `receive()` on `asyncio.Queue` streams

## Impact

- **Source files**: `src/agentpool_server/opencode_server/routes/global_routes.py`, `src/agentpool/orchestrator/core.py`, `src/agentpool/orchestrator/run.py`, `src/agentpool/agents/base_agent.py`, `src/agentpool_toolsets/builtin/subagent_tools.py`, `src/agentpool_toolsets/builtin/workers.py`, `src/agentpool/agents/acp_agent/acp_agent.py`, `src/agentpool_server/acp_server/handler.py`
- **Test files**: `tests/agents/test_base_agent_api.py`, `tests/tools/test_workers.py`, `tests/tools/test_pick.py`, `tests/tools/test_runcontext.py`, `tests/running/test_executor.py`, `tests/running/test_delegation.py`, `tests/delegation/test_cross_provider_session_lifecycle.py`, `tests/messaging/test_agent_signals.py`, `tests/messaging/test_signal_forwarding.py`, `tests/messaging/test_agent_piping.py`, `tests/orchestrator/test_run_lifecycle.py`, `tests/servers/opencode_server/test_global_event.py`, `tests/servers/acp_server/`
- **Statics**: ~267 ruff errors (21 auto-fixable), ~337 mypy errors (51 unused-ignore), ~181 ty diagnostics
- **Dependencies**: No new dependencies; fixes are internal API alignment
- **Risk**: Medium — changes touch core orchestration (`core.py`), agent base (`base_agent.py`), and server routes, but all changes restore previously-working behavior rather than introducing new patterns
