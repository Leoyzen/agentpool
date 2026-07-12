## 1. P0: EventBus Stream API Fix (TC-1, ~58 failures)

- [ ] 1.1 Replace `receive()` with `get()` in `src/agentpool_server/opencode_server/routes/global_routes.py:270`
- [ ] 1.2 Replace `receive_nowait()` with `get_nowait()` in `src/agentpool/orchestrator/core.py:434`
- [ ] 1.3 Replace `send_nowait()` with `put_nowait()` in `src/agentpool/orchestrator/core.py:659`
- [ ] 1.4 Search for any remaining `receive()`, `receive_nowait()`, `send_nowait()` calls on EventBus streams across `src/` and replace with Queue API equivalents
- [ ] 1.5 Update exception handling: replace `anyio.WouldBlock` with `asyncio.QueueEmpty` where drain logic uses `get_nowait()`
- [ ] 1.6 Run `uv run pytest tests/servers/opencode_server/test_global_event.py --timeout=15 -q` and verify 0 failures
- [ ] 1.7 Run `uv run pytest tests/servers/opencode_server/ --timeout=15 -q --tb=no` and verify pass rate > 95%

## 2. P0: RunHandle Cleanup Callbacks Fix (TC-9, ~5 failures)

- [ ] 2.1 Restore cleanup callback invocation in `RunHandle.complete()` in `src/agentpool/orchestrator/run.py` — callbacks SHALL be invoked before `complete_event.set()`
- [ ] 2.2 Restore cleanup callback invocation in `RunHandle.fail()` in `src/agentpool/orchestrator/run.py` — callbacks SHALL be invoked before `complete_event.set()`, after `RunFailedEvent` is published
- [ ] 2.3 Run `uv run pytest tests/orchestrator/test_run_lifecycle.py --timeout=15 -q` and verify 0 failures
- [ ] 2.4 Run `uv run pytest tests/orchestrator/test_close_checkpoint.py --timeout=15 -q` and verify 0 failures

## 3. P1: RuntimeAgentRegistry for Pool-Less Agent Lookup (TC-5/TC-7, ~27 failures)

- [ ] 3.1 Create `RuntimeAgentRegistry` class in `src/agentpool/orchestrator/` — simple `dict[str, AgentConfig]` with `register(name, config)` and `lookup(name)` methods
- [ ] 3.2 Wire `RuntimeAgentRegistry` instance into `SessionController` (or `AgentPool`) as a property
- [ ] 3.3 Update `SessionController.get_or_create_session_agent()` in `src/agentpool/orchestrator/core.py` to check `_session_agents` cache → `RuntimeAgentRegistry` → `manifest.agents` in that order
- [ ] 3.4 Update subagent tool creation in `src/agentpool_toolsets/builtin/subagent_tools.py` to register target agent config in `RuntimeAgentRegistry` at tool creation time
- [ ] 3.5 Update worker tool creation in `src/agentpool_toolsets/builtin/workers.py` to register target agent config in `RuntimeAgentRegistry` at tool creation time
- [ ] 3.6 Run `uv run pytest tests/tools/test_workers.py --timeout=30 -q` and verify failure count < 5 (down from 20)
- [ ] 3.7 Run `uv run pytest tests/delegation/test_cross_provider_session_lifecycle.py --timeout=30 -q` and verify 0 failures

## 4. P1: BaseAgent Ephemeral Session for Pool-Less Operation (TC-2/TC-3, ~21 failures)

- [ ] 4.1 Update `BaseAgent.run()` standalone path in `src/agentpool/agents/base_agent.py` to generate ephemeral session ID via `uuid4()` when `agent_pool is None`
- [ ] 4.2 Update `BaseAgent.run_stream()` standalone path to use the same ephemeral session pattern
- [ ] 4.3 Update `get_active_run_context()` to return local `_run_context` when no pool session exists
- [ ] 4.4 Update `is_turn_active()` to check local `_run_context` when no pool session exists
- [ ] 4.5 Ensure ephemeral session is cleaned up after run completes (set `_run_context = None`)
- [ ] 4.6 Run `uv run pytest tests/agents/test_base_agent_api.py --timeout=15 -q` and verify 0 errors
- [ ] 4.7 Run `uv run pytest tests/agents/test_agent_basics.py --timeout=15 -q` and verify 0 failures

## 5. P1: Executor/Running Module Fix (TC-6, ~7 failures)

- [ ] 5.1 Inspect `src/agentpool/running/` executor and delegation modules for pool-level agent dependencies
- [ ] 5.2 Update executor to work with `RuntimeAgentRegistry` or direct agent references instead of pool-level lookup
- [ ] 5.3 Run `uv run pytest tests/running/ --timeout=15 -q` and verify 0 failures

## 6. P1: Messaging/Signal System Fix (TC-8, ~7 failures)

- [ ] 6.1 Inspect `tests/messaging/test_agent_signals.py::test_message_chain_through_routing` failure — check if signal emission depends on pool-level agent registration
- [ ] 6.2 Fix signal forwarding in `src/agentpool/messaging/messagenode.py` — ensure `message_sent` signal fires without pool-level agent
- [ ] 6.3 Fix `test_agent_piping.py::test_agent_piping_background_error` — check if piping depends on pool
- [ ] 6.4 Fix `test_signal_forwarding.py::test_invalid_forward_target` — check if forward target validation depends on pool
- [ ] 6.5 Fix `test_talks.py::test_token_tracking` — check if token tracking depends on pool-level agent
- [ ] 6.6 Run `uv run pytest tests/messaging/ --timeout=15 -q` and verify 0 failures

## 7. P1: ACP Server MagicMock Fix (TC-13, ~2+ failures)

- [ ] 7.1 Fix `session_store.load()` mock in `tests/servers/acp_server/` — replace MagicMock with AsyncMock for async methods
- [ ] 7.2 Run `uv run pytest tests/servers/acp_server/ --timeout=15 -q --tb=no` and verify 0 failures

## 8. P2: Stale API Reference Cleanup (TC-14)

- [ ] 8.1 Migrate `tests/agents/native_agent/test_inject_prompt_cross_task.py` from `session_pool.turns` API to new `SessionController` API (4 skipped tests)
- [ ] 8.2 Update `tests/agents/test_deprecation_warnings.py` to use new API or remove if testing removed API
- [ ] 8.3 Migrate `tests/agents/test_contextvar_concurrency.py` from `_run_stream_once` to `create_turn().execute()`
- [ ] 8.4 Register `pytest.mark.security` in `pyproject.toml` to clear `PytestUnknownMarkWarning` (19 warnings)
- [ ] 8.5 Run `uv run pytest tests/agents/ --timeout=15 -q --tb=no` and verify no new failures

## 9. P2: Flaky/Slow Test Marking (TC-10)

- [ ] 9.1 Add `@pytest.mark.flaky(reruns=3)` to `tests/orchestrator/test_performance.py` benchmark tests (3 failures)
- [ ] 9.2 Add `@pytest.mark.flaky(reruns=3)` to `tests/performance/test_skill_performance.py::test_opencode_bridge_conversion`
- [ ] 9.3 Add `@pytest.mark.slow` to `tests/orchestrator/test_sessionpool_e2e_integration.py::test_e2e_pre_existing_session_consumer_started`
- [ ] 9.4 Run `uv run pytest tests/orchestrator/test_performance.py tests/performance/ --timeout=30 -q` and verify flaky tests pass with reruns

## 10. P2: Static Analysis Cleanup

- [ ] 10.1 Run `uv run ruff check --fix src/` to auto-fix 21 safe ruff issues
- [ ] 10.2 Run `uv run ruff format src/` to format 64 unformatted files
- [ ] 10.3 Remove 51 redundant `# type: ignore` comments identified by mypy `unused-ignore` — verify each removal does not introduce new mypy errors
- [ ] 10.4 Run `uv run ruff check src/` and verify error count < 250 (down from 267)
- [ ] 10.5 Run `uv run --no-group docs mypy src/` and verify error count < 290 (down from 337)

## 11. Final Verification

- [ ] 11.1 Run full test suite per-directory with `--timeout=15 -p no:cacheprovider` and collect pass/fail counts
- [ ] 11.2 Verify total failure count < 20 (down from ~120+)
- [ ] 11.3 Verify no new regressions introduced by the fixes
- [ ] 11.4 Run `uv run ruff check src/` and `uv run --no-group docs mypy src/` — record final counts
- [ ] 11.5 Update `.omo/reports/regression-analysis-eliminate-pool-level-agents.md` with fix results
