# Tasks: Pre-M4 Protocol Server Debt Cleanup

## Phase 1: ACP Execution Path Unification

- [ ] 1.1 Build `ACPAgentAPI` adapter: add `stream_events()` and `get_messages()` methods to satisfy full `ACPClientProtocol`. The inline `_stream_events()` uses state polling — `pop_update()` on `self._state` (an `ACPSessionState` object at `session_state.py:76`), woken by `_update_event` (a `TimeoutableEvent` at `client_handler.py:93`) via `wait_with_timeout(0.05)`. The adapter MUST bridge this polling loop into an async iterator that `ACPTurn.execute()` can consume — it may NOT simply delegate to `ACPClient.stream_events()` if the underlying transport uses polling. Remove the `cast("ACPClientProtocol", self._api)` at `acp_agent.py:654` (search: `grep -n 'cast.*ACPClientProtocol' src/agentpool/agents/acp_agent/acp_agent.py`). See TODO at `acp_agent.py:648-652`. Verify: `isinstance(acp_agent_api, ACPClientProtocol)` passes without cast.
- [ ] 1.2 Refactor `ACPAgent._stream_events()` (`acp_agent.py:412-611`, search: `grep -n '_stream_events' src/agentpool/agents/acp_agent/acp_agent.py`) to delegate to `ACPTurn.execute()` instead of inline 200-LOC implementation. Verify: ACP standalone streaming produces same events as before (snapshot test V10). Note: `ACPTurn.execute()` uses `stream_events(response)` (async iterator) while inline path polls `pop_update()` — event ordering must be equivalent. Quick sanity check after refactoring: `uv run pytest tests/agents/test_acp_agent.py -k stream -x`.
- [ ] 1.3 Remove `_run_stream_once()` hook firing for ACP agents: delete `AGENT_TYPE != "native"` branches (search: `grep -n 'AGENT_TYPE.*native' src/agentpool/agents/base_agent.py`). Hooks now fire only through `HookAwareTurn` in `Turn.execute()`.
- [ ] 1.4 Remove `hooks_fired` double-fire guard: delete `hooks_fired` field from `AgentRunContext` (search: `grep -rn 'hooks_fired' src/`), remove all 21 references across `base_agent.py` (6), `turn.py` (12), `run.py` (2), `context.py` (1). NOTE: `_log_tool_execution` at `turn.py:213-265` uses `hooks_fired` for tool-log idempotency (`tool_log:{tool_call_id}` key at line 242, or `tool_log:{tool_name}` at line 244 when `tool_call_id` is None) — replace with `self._logged_tools: set[str] = set()` initialized as an instance attribute on `HookAwareTurn` (add `__init__` to the mixin or initialize lazily via `if not hasattr(self, '_logged_tools'): self._logged_tools = set()`). The set is per-Turn-instance; a new Turn is created per turn so no cross-turn reset is needed. Verify: `grep -rn 'hooks_fired' src/` returns 0.
- [ ] 1.5 Remove deprecated `queue_prompt`/`inject_prompt` ACP branching (search: `grep -n 'queue_prompt\|inject_prompt' src/agentpool/agents/base_agent.py`). ACP agents should use same `session_pool.followup()`/`steer()` path as native agents — see `native_agent/agent.py` for the target pattern.
- [ ] 1.7 Update tests that mock `_run_stream_once` (3 test files, search: `grep -rn '_run_stream_once' tests/`). These tests WILL break after tasks 1.2-1.3. This is a BLOCKING dependency, not optional.

## Phase 2: Legacy Field & API Cleanup

- [ ] 2.1a Verify existing MCPManager API: `get_session_context()` already exists at `src/agentpool/mcp_server/manager.py:212` (returns `_SessionContext | None`). `update_session_snapshot()` already exists at `manager.py:219`. `add_transport()` exists on `SessionConnectionPool` at `session_pool.py:225` (NOT on MCPManager). Actions: (1) Make `_SessionContext` public (rename to `McpSessionContext`) or add a type alias. (2) Add `MCPManager.add_transport(session_id, transport)` that delegates to the internal `SessionConnectionPool`. (3) Use existing `update_session_snapshot()` (NOT `update_snapshot`).
- [ ] 2.1c Update `ACPSession.initialize_mcp_servers()` at `src/agentpool_server/acp_server/session.py:482-491` (search: `grep -n '_mcp_snapshot\|_session_connection_pool' src/agentpool_server/acp_server/session.py`) to call `MCPManager` methods instead of mutating agent fields. The two access sites are: line 482 (`existing = self.agent._mcp_snapshot` — read) and line 491 (`self.agent._mcp_snapshot = new_snapshot` — write), plus line 438-439 (`self.agent._session_connection_pool.add_transport(...)` — method call). Replace with `MCPManager.update_session_snapshot()` and `MCPManager.add_transport()`.
- [ ] 2.1d Remove `_mcp_snapshot` and `_session_connection_pool` fields from `NativeAgent` at `src/agentpool/agents/native_agent/agent.py:337-338` (search: `grep -rn '_mcp_snapshot\|_session_connection_pool' src/`). NOTE: These fields are ONLY accessed in `session.py` (not in `get_agentlet()` or any other agent method). Verify: `grep -rn '_mcp_snapshot\|_session_connection_pool' src/` returns 0.
- [ ] 2.2 Add `deliver_feedback(feedback: Feedback) -> None` to `CommChannel` protocol (`lifecycle/protocols.py`). `DirectChannel` implements as no-op. `ProtocolChannel` already has `deliver_feedback()` at `comm_channel.py:263`. Remove the `try/except AttributeError` blocks in `run.py:822-830, 869-878` (search: `grep -n 'deliver_feedback\|except AttributeError' src/agentpool/orchestrator/run.py`) — these become dead code once `DirectChannel` implements the method. Also remove the 4 `# type: ignore[attr-defined]` at lines 825, 830, 872, 877.
- [ ] 2.3 Remove `HostContext.pool` escape hatch. Migrate 1 remaining site at `base_team.py:411` (search: `grep -rn 'host_context.pool' src/`). Verify: `grep -rn 'host_context.pool' src/` returns 0. NOTE: AGENTS.md mentions "~6 skill-related accesses" — verify these are resolved before removing `pool`.
- [ ] 2.4 Remove legacy `RunStatus` enum from `src/agentpool/orchestrator/run.py:130` (NOT `lifecycle/types.py` — `RunStatus` is defined in `run.py`, while `RunState` is in `lifecycle/types.py:15`). Migration mapping: `RunStatus.idle → RunState.IDLE`, `RunStatus.running → RunState.RUNNING`, all terminal states (`completed`, `failed`, `done`, `checkpointed`) → `RunState.DONE`. Add `RunOutcome` enum (`COMPLETED`, `FAILED`, `CHECKPOINTED`) to `lifecycle/types.py` (next to `RunState`). Add `outcome: RunOutcome | None = None` field to `RunHandle` to preserve terminal state distinction (`done` maps to `outcome=None`). Migrate `steer()` and `followup()` which check `_status == RunStatus.idle/running` at `run.py:841,846` (search: `grep -n 'RunStatus\|_status' src/agentpool/orchestrator/run.py`). Remove both `_status: RunStatus` field (line 202) and `status: RunStatus` field (line 193). Update ALL external consumers (search: `grep -rn 'RunStatus' src/` — covers `session_pool.py`, `orchestrator/__init__.py`, `orchestrator/core.py`, `session_controller.py`, `src/agentpool_server/opencode_server/routes/message_routes.py`, `src/agentpool_server/opencode_server/session_pool_integration.py`). Verify: `grep -rn 'RunStatus' src/` returns 0.
- [ ] 2.5 Remove dead code in `session_controller.py:484-491` (unreachable after `return` statement). NOTE: `session_controller.py` is also modified by M4 task 18.9 — line numbers will drift if M4 changes land first.

## Phase 4: Type Safety & Code Quality

- [ ] 4.1 Remove 6 `# type: ignore[attr-defined]` in `run.py` (search: `grep -n "type: ignore\[attr-defined\]" src/agentpool/orchestrator/run.py`). `RunHandle` should hold direct references to `_journal`, `_trigger_source`, `_snapshot_store` instead of accessing them via `self._comm_channel._journal`. Also remove the `__post_init__` journal injection pattern at `run.py:265-270` — `CommChannel` should receive the journal via constructor, not post-hoc field mutation.
- [ ] 4.1b Add `set_replaying(flag: bool) -> None` method to `CommChannel` protocol. Replace direct `self._comm_channel._replaying = True/False` access at `run.py:444,449` (search: `grep -n '_replaying' src/agentpool/orchestrator/run.py`). Also remove the `_replaying: bool` protocol attribute from `protocols.py:213` (if present) so callers must use `set_replaying()`.
- [ ] 4.2 Replace `_channel_publishes_to_event_bus` isinstance check (search: `grep -n '_channel_publishes_to_event_bus\|isinstance.*ProtocolChannel' src/agentpool/orchestrator/run.py`) with `publishes_to_event_bus: bool` property on `CommChannel` protocol. `ProtocolChannel` returns `True`, `DirectChannel` returns `False`.
- [ ] 4.3 Replace `hasattr` usage in ACP code: `src/agentpool_server/acp_server/acp_agent.py:984` (full path to disambiguate from core `agents/acp_agent/acp_agent.py` which is only 874 lines), `src/agentpool_server/acp_server/session.py:306`. Use typed interface checks or protocol methods instead.
- [ ] 4.4 Fix `ACPTurn` generic `except Exception` clauses (search: `grep -n 'except Exception' src/agentpool/agents/acp_agent/turn.py`). Catch specific exception types.
- [ ] 4.5 Refactor `RunHandle.start()` (~373 SLOC, search: `grep -n 'def start' src/agentpool/orchestrator/run.py`) into composable sub-methods. Proposed split (5 methods, each < 100 SLOC):
  - `_handle_recovery()` — crash recovery + dimension subscription (lines ~433-482)
  - `_idle_loop()` — idle wait, feedback drain, prompt collection (lines ~491-541)
  - `_execute_turn()` — turn execution, event streaming with `yield` (lines ~543-639)
  - `_handle_turn_result()` — cancel handling, error handling (lines ~657-698)
  - `_drain_events()` — post-turn snapshot, child events, feedback drain (lines ~700-777)
  NOTE: `start()` is an async generator — sub-methods that `yield` must use `async for event in self._execute_turn(): yield event` pattern. State shared between methods (`current_prompts` → store on `self`, `turn_failed` → pass as parameter) must be passed as parameters or stored on `self`.

## Phase 6: Event System Gaps

> **NOTE**: `EventProcessor._handle_event()` is modified by both pre-M4 tasks (6.1, 6.2) and M4 task 18.3 (RunStartedEvent). These changes are to different `case` branches of the same method and should not conflict. If pre-M4 and M4 are developed in parallel, pre-M4's `EventProcessor._handle_event()` changes MUST land first. M4 task 18.3 must rebase onto the pre-M4 version of this method.

- [ ] 6.1 Wire `McpToolsChangedEvent`: emit from `MCPCapability.on_change()` stream, handle in `EventProcessor` to trigger tool list refresh. NOTE: The event is currently defined at `src/agentpool_server/opencode_server/models/events.py:850` (OpenCode server-specific, not core). Either promote to core events or document the cross-layer wiring.
- [ ] 6.2 Distinguish `StreamCompleteEvent(cancelled=True)` in `EventProcessor` (search: `grep -n 'StreamCompleteEvent' src/agentpool_server/opencode_server/event_processor.py`). Emit `SessionStatusEvent(status="cancelled")` for cancelled, `SessionStatusEvent(status="idle")` for completed.
- [ ] 6.3 Remove deprecated `stream_adapter._handle_event` (search: `grep -rn '_handle_event' src/agentpool_server/opencode_server/stream_adapter.py`) kept only for test compatibility. Update tests to use `EventProcessor` directly.

> **RunStartedEvent handling** (originally REQ-1 in `unified-event-routing/spec.md`) was moved to M4 task 18.3 because it modifies the same `EventProcessor._handle_event()` method that M4's OpenCode hardening touches. See spec deferral note.

## Phase 7: Deferred / Nice-to-have (NOT required for M4)

- [ ] 7.1 (N1-ACP) Remove `_agent_pool` constructor threading (25+ refs across messagenode.py + 3 agent files)
- [ ] 7.2 (N2-ACP) Remove `agent_type == "acp"` branching in `session_pool.py:766`
- [ ] 7.3 (N5-ACP) Replace `getattr(logging, ...)` × 6 in ACP transport code with direct method calls
- [ ] 7.4 (N6-ACP) Implement URL-mode elicitation async completion (`input_provider.py:386-392`)
- [ ] 7.5 (N7-ACP) Finish AgentPlanUpdate handling (`client_handler.py:197-202`)
- [ ] 7.6 (N8-ACP) stdio refactor (`stdio.py:225`)
- [ ] 7.7 (N10-ACP) Remove `_ACPSessionProxy` workaround class (`handler.py:758-775`)
- [ ] 7.8 (N11-ACP) Fix `swap_pool` accessing `_acp_sessions` private dict (`acp_agent.py:1077-1078`)
- [ ] 7.9 (N3-OC) Resolve 5 TODOs in OpenCode server (input_provider:156, file_routes:302, app_routes:206, events:853, provider_auth:226)
- [ ] 7.10 Remove `ACPEventConverter.subagent_display_mode` feature — actively used in 15+ locations for 3 display modes (legacy/zed/qwen), NOT just a constructor field. Requires design decision on replacement before removal. Defer until display mode architecture is redesigned. (search: `grep -rn 'subagent_display_mode' src/agentpool_server/acp_server/`)

## Merged into M4 (not in this change)

The following tasks were moved to `m4-multi-config` task group 18 to avoid duplicate changes to the same files:

- ~~Phase 3: OpenCode Server Hardening (5 tasks)~~ → M4 task group 18
- ~~Phase 5: M4 Identity Preparation (4 tasks)~~ → M4 task groups 7-8 (already covered) + task group 18
- ~~4.6 Remove typing.Any in event adapter~~ → M4 task group 18 (OpenCode-specific)
- ~~RunStartedEvent handling~~ → M4 task 18.3 (same EventProcessor method)

## Verification Gates

- [ ] V1 `uv run pytest` — all tests pass
- [ ] V2 `uv run ruff check src/` — no new lint errors
- [ ] V3 `uv run --no-group docs mypy src/` — no new type errors
- [ ] V4 `grep -rn 'hooks_fired' src/` returns 0
- [ ] V5 `grep -rn 'type: ignore\[attr-defined\]' src/agentpool/orchestrator/run.py` returns 0 (narrowed to attr-defined only; 2 arg-type ignores at lines 388, 563 are pre-existing and not in scope)
- [ ] V6 `grep -rn '_mcp_snapshot\|_session_connection_pool' src/` returns 0
- [ ] V7 `grep -rn 'RunStatus' src/` returns 0
- [ ] V8 `grep -rn 'host_context.pool' src/` returns 0
- [ ] V9 `grep -rn '_replaying' src/agentpool/orchestrator/run.py` returns 0 (direct private access replaced by protocol method)
- [ ] V10 Create `tests/integration/test_acp_streaming.py` BEFORE Phase 1 — a snapshot test capturing the event sequence from ACP standalone streaming (including `ToolCallCompleteEvent` with `ToolResultMetadataEvent` enrichment). Generate baseline with `--snapshot-update`. After Phase 1, verify same snapshot passes without `--snapshot-update`.
- [ ] V11 `grep -rn '_channel_publishes_to_event_bus' src/` returns 0 (replaced by `publishes_to_event_bus` property)
- [ ] V12 `grep -rn '_handle_event' src/agentpool_server/opencode_server/stream_adapter.py` returns 0 (deprecated adapter removed)
