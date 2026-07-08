# acp-proxy-chain-refactor - Work Plan

## TL;DR (For humans)

**What you'll get:** ACP agents will use a proxy chain architecture instead of direct subprocess communication. This fixes three critical defects: dead ACPTurn code, 50ms polling latency, and double event conversion in nested scenarios. Hooks, context injection, and tool providers become wire-level interceptors that can block before the terminal agent sees a message.

**Why this approach:** The proxy chain RFD defines a Conductor that routes messages through a chain of proxies, each able to intercept and transform bidirectionally. This directly solves the double conversion problem (proxies pass through untouched when no interception needed) and provides a clean extension model. We wrap existing hooks as HookProxy components rather than rewriting them — preserving tested code while elevating it to wire-protocol level.

**What it will NOT do:** It will not implement ACP remote transport, session fork, ratify the proxy chain RFD, refactor native agents to use proxy chains, support proxy hot-swap, or provide backward compatibility for the internal `_stream_events()` API.

**Effort:** XL
**Risk:** High — implements against an unratified RFD with no Python reference implementation; depends on `unify-hook-system` branch merge; large refactoring scope (~4-5 weeks across 7 phases)
**Decisions to sanity-check:** D3 (ACPClientAdapter non-blocking design), D4 (HookProxy wraps existing hooks), D9 (HookProxy/HookAwareTurn coexistence via _hooks=None)

Your next move: approve to start execution, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): XL effort, High risk, 7-phase proxy chain refactor — Phase 0 (merge unify-hook-system) + ACPClientAdapter + Conductor + Proxy protocol + built-in proxies + server adaptation + cleanup, 29 todos across 7 waves.

## Scope
### Must have
- **Phase 0 prerequisite**: Merge `feature/unify-hook-system` branch into working branch (HookAwareTurn, hooks_fired, renamed methods must exist)
- `ACPClientAdapter` class (`src/agentpool/agents/acp_agent/adapter.py`) — constructor accepts BOTH `ACPAgentAPI` AND notification source (`ACPClientHandler` or `asyncio.Queue`); implements modified `ACPClientProtocol` with non-blocking prompt, async queue streaming, stop_reason property, concurrent prompt rejection, background task error propagation
- Redefined `ACPClientProtocol` in `src/agentpool/agents/acp_agent/turn.py` — `prompt()` returns None, `stream_events()` takes no args, `stop_reason` property
- Bifurcated `ACPClientHandler.session_update()` — state updates in-place, stream data to async queue (maxsize=1000)
- `ACPAgent._stream_events()` body replaced to delegate to `create_turn()` → `ACPTurn.execute()` (NOT deleted in Phase 1 — deletion deferred to Phase 6)
- `src/acp/proxy/` package: `__init__.py`, `protocol.py` (Proxy typing.Protocol), `connection.py` (ProxySideConnection), `constants.py` (wire method names)
- `Conductor(MessageNode[ChatMessage, ChatMessage[str]])` in `src/acp/conductor.py` with `_step` property, anyio task groups, chain init, message routing, passthrough, error propagation
- `ACPClientHandler` ownership transferred from `ACPAgent` to `Conductor` (Conductor wires subprocess connection to handler)
- Rewritten `ACPAgent` — output `ChatMessage[str]`, uses Conductor, `proxy_chain` config support, `use_conductor` feature flag
- `ProxyChainConfig` Pydantic model with `type` discriminator (unknown types raise `ValidationError` at load time)
- Built-in proxies: `HookProxy` (all 4 hook types, wire-level blocking), `ContextInjectionProxy`, `ToolProviderProxy` (experimental)
- HookProxy/HookAwareTurn coexistence via `_hooks=None` mechanism
- Disable `ACPClientHandler.request_permission()` hook firing when HookProxy is active (prevent double-firing)
- Conductor auto-insert HookProxy when agent has hooks and no explicit HookProxy
- Proxy type registry mapping string discriminators to proxy classes
- `AgentPoolACPAgent` refactored as terminal agent (responds to `initialize`, not `proxy/initialize`)
- Legacy `ACPSession.process_prompt()` dual path removed
- `ACPEventConverter` refactored as proxy component (split: define interface, extract stateless functions, implement wrapper, migrate callers)
- `ACPSessionState` deque deleted, model/mode/config preserved in renamed `ACPState`
- `ToolManagerBridge` migrated to `ToolsetFactory` (NOT `ResourceProvider` — deprecated)
- `RunHandle.cancel()` for ACP agents — cancels stream iteration task, not run loop
- `ACPTurn.execute()` catches `CancelledError`, returns without `StreamCompleteEvent`
- Full test suite per phase (unit + integration, pytest with markers)
- Multi-turn integration test (3 turns with steer/followup through Conductor)
- Documentation: AGENTS.md updates, YAML config examples

### Must NOT have (guardrails, anti-slop, scope boundaries)
- ACP remote transport (Streamable HTTP/WS) — future work
- Session fork (`session/fork`) — separate RFD
- Ratify the proxy chain RFD — implement against current draft only
- Refactor native (PydanticAI) agents to use proxy chains — they don't need wire-level interception
- Conductor-in-proxy-mode for tree topologies — future work
- Proxy hot-swap at runtime — explicitly out of scope (raise `NotImplementedError`)
- Backward compatibility for `_stream_events()` internal API — internal method, safe to change
- `getattr`/`hasattr` — always provide full type safety per AGENTS.md rules
- Any `cast()` or `as any` type hacks — strict mypy --strict compliance
- TODO comments left in code unless explicitly deferred
- Migrate to `ResourceProvider` (deprecated) — use `ToolsetFactory` instead
- Delete `_stream_events()` before Phase 6 — keep as thin delegation to ACPTurn.execute() until feature flag removed

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after per phase (pytest with @unit/@integration markers, TestModel for agent testing)
- Evidence: .omo/evidence/task-<N>-acp-proxy-chain-refactor.<ext>
- Each todo includes unit tests (happy + failure paths) and/or integration tests
- Final wave: `uv run pytest && uv run --no-group docs mypy src/ && uv run ruff check src/`
- Per-phase regression: `uv run pytest tests/agents/acp_agent/` after Phase 1, expand scope per phase
- Passthrough test (T23): mock/spy `ACPEventConverter`, assert `call_count == 0` during passthrough

## Execution strategy
### Parallel execution waves

**Wave 0 (Phase 0 — Prerequisite):** Merge `unify-hook-system` branch. 1 todo.
**Wave 1 (Phase 1 — ACPClientAdapter):** Fix dead ACPTurn, eliminate 50ms polling. Independently shippable. 6 todos.
**Wave 2 (Phase 2 — Conductor + Proxy Protocol):** New `src/acp/proxy/` package and Conductor. 6 todos.
**Wave 3 (Phase 3 — ACPAgent Rewrite):** Rewrite ACPAgent to use Conductor, add YAML config, feature flag. 5 todos.
**Wave 4 (Phase 4 — Built-in Proxies):** HookProxy, ContextInjectionProxy, ToolProviderProxy. 6 todos.
**Wave 5 (Phase 5 — Server-Side):** AgentPoolACPAgent as terminal agent. 3 todos.
**Wave 6 (Phase 6 — Cleanup):** Delete dead code, remove feature flag, full validation. 4 todos.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T0 (merge unify-hook-system) | — | T1-T6 | — |
| T1 (ACPClientAdapter class + protocol) | T0 | T2, T3 | — |
| T2 (adapter methods + error propagation) | T1 | T4, T6 | T3 |
| T3 (handler bifurcation) | T1 | T6 | T2 |
| T4 (ACPAgent fixes — delegate, not delete) | T2 | T6 | T5 |
| T5 (ACPState rename) | T0 | T6 | T4 |
| T6 (Phase 1 tests) | T2, T3, T4, T5 | T7, T8 | — |
| T7 (proxy package) | T0 | T8, T9 | — |
| T8 (Conductor class + handler ownership) | T6, T7 | T9, T10, T11 | — |
| T9 (chain init + detection) | T8 | T10, T12 | — |
| T10 (routing + passthrough + errors) | T9 | T12 | T11 |
| T11 (_step + context manager) | T8 | T12, T13 | T10 |
| T12 (Phase 2 tests) | T9, T10, T11 | T13 | — |
| T13 (ACPAgent rewrite) | T6, T12 | T14, T17 | T14 |
| T14 (config models + ToolsetFactory migration) | T0 | T17 | T13 |
| T15 (AgentPool integration) | T13 | T17 | — |
| T16 (Phase 3 tests + multi-turn) | T13, T14, T15 | T18, T23 | — |
| T17 (proxy registry + impls pkg) | T12 | T18, T19, T20 | — |
| T18 (HookProxy — all 4 hooks) | T17 | T19, T21 | T20 |
| T19 (coexistence + auto-insert + disable request_permission) | T18 | T21 | — |
| T20 (ContextInjectionProxy + ToolProviderProxy) | T17 | T21 | T18 |
| T21 (Phase 4 tests) | T18, T19, T20 | T22 | — |
| T22 (server terminal agent + ACPEventConverter split) | T16, T21 | T23 | — |
| T23 (Phase 5 tests + passthrough zero-conversion) | T22 | T24 | — |
| T24 (delete dead code + remove flag + simplify + docs + validation) | T23 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 0. Merge `feature/unify-hook-system` into working branch
  What to do / Must NOT do: Merge the `feature/unify-hook-system` branch into the current working branch (`feature/acp-proxy-chain-refactor`). This brings `HookAwareTurn` class, `hooks_fired` field on `AgentRunContext`, renamed hook methods (`run_pre_turn_hooks`/`run_post_turn_hooks`), and the `_hooks=None` guard at `orchestrator/turn.py:135`. Must NOT skip conflict resolution — resolve all merge conflicts carefully. Must NOT cherry-pick individual commits — merge the full branch.
  Parallelization: Wave 0 | Blocked by: — | Blocks: T1-T6
  References: `openspec/changes/unify-hook-system/` (change spec); `src/agentpool/orchestrator/turn.py` (HookAwareTurn mixin, line 78 on feature branch); `src/agentpool/agents/acp_agent/turn.py` (ACPTurn inherits HookAwareTurn on feature branch)
  Acceptance criteria: `uv run python -c "from agentpool.orchestrator.turn import HookAwareTurn; print('ok')"` succeeds. `grep -n "hooks_fired" src/agentpool/agents/context.py` returns matches. `uv run pytest tests/agents/acp_agent/test_acp_turn_hooks.py -v` passes.
  QA scenarios: happy — HookAwareTurn importable; hooks_fired field exists; ACPTurn inherits HookAwareTurn; existing hook tests pass. failure — merge conflicts unresolved; import errors. Evidence: `.omo/evidence/task-0-acp-proxy-chain-refactor.log`
  Commit: Y | merge: integrate unify-hook-system branch

- [x] 1. Create ACPClientAdapter class + redefine ACPClientProtocol
  What to do / Must NOT do: Create `src/agentpool/agents/acp_agent/adapter.py` with `ACPClientAdapter` class. Constructor accepts BOTH `ACPAgentAPI` (for `prompt()`/`get_messages()`) AND a notification source (`ACPClientHandler` or `asyncio.Queue`). Redefine `ACPClientProtocol` in `turn.py:35-49` — `prompt()` returns `None`, `stream_events()` takes no args (returns `AsyncIterator[SessionUpdate]`), add `stop_reason` property. Must NOT use `cast()` or `getattr`. Must NOT change `ACPAgentAPI` itself. Must NOT construct adapter with only `ACPAgentAPI` — needs notification source too.
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T2, T3
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-client-adapter/spec.md` (requirements 1-5); `src/agentpool/agents/acp_agent/turn.py:35-49` (ACPClientProtocol); `src/acp/agent/acp_agent_api.py:45-57` (ACPAgentAPI — wraps `Agent` protocol, has `prompt()` at line 159); `src/agentpool/agents/acp_agent/client_handler.py:118-206` (ACPClientHandler — implements `Client` protocol, receives notifications); `src/agentpool/agents/acp_agent/acp_agent.py:632-662` (create_turn with cast hack); Metis finding C3 (adapter needs both API and handler)
  Acceptance criteria: `uv run python -c "from agentpool.agents.acp_agent.adapter import ACPClientAdapter; print('import ok')"` succeeds. `uv run ruff check src/agentpool/agents/acp_agent/adapter.py` passes. `uv run --no-group docs mypy src/agentpool/agents/acp_agent/adapter.py` passes. Constructor signature: `ACPClientAdapter(api: ACPAgentAPI, notification_source: ACPClientHandler | asyncio.Queue)`.
  QA scenarios: happy — import ACPClientAdapter, verify constructor accepts api + notification_source; has `prompt`, `stream_events`, `stop_reason`, `get_messages` methods. failure — `stop_reason` raises `RuntimeError` before streaming; constructor without notification_source raises `TypeError`. Evidence: `.omo/evidence/task-1-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp-agent): create ACPClientAdapter class and redefine ACPClientProtocol

- [x] 2. Implement ACPClientAdapter methods — prompt, stream_events, stop_reason, get_messages, concurrent rejection, error propagation
  What to do / Must NOT do: Implement `prompt()` — launch `api.prompt()` as background asyncio task, return None. Implement `stream_events()` — return async iterator from `asyncio.Queue(maxsize=1000)` that notification_source pushes to. Implement `stop_reason` property — returns `PromptResponse.stop_reason` after background task completes, raises `RuntimeError("stop_reason not available until streaming completes")` if accessed early. Implement `get_messages()` — call `api.get_messages()` after prompt completes. Implement concurrent prompt rejection — raise `RuntimeError("Prompt already in progress")`. Implement error propagation — if background `api.prompt()` task raises, propagate exception to `stream_events()` consumer (push exception to queue). Must NOT block in `prompt()`. Must NOT use unbounded queue. Must NOT leave consumer hanging on background task failure.
  Parallelization: Wave 1 | Blocked by: T1 | Blocks: T4, T6 | Can parallelize with: T3
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-client-adapter/spec.md:3-60` (all scenarios); `src/acp/agent/acp_agent_api.py:45-226` (ACPAgentAPI — `prompt()`, `get_messages()`); `src/agentpool/agents/acp_agent/client_handler.py:56-62` (TimeoutableEvent pattern); design.md D3 (adapter design); Metis finding L2 (error propagation)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/test_adapter.py -v` passes. `uv run ruff check src/agentpool/agents/acp_agent/adapter.py` passes. `uv run --no-group docs mypy src/agentpool/agents/acp_agent/adapter.py` passes.
  QA scenarios: happy — prompt launches background task and returns None; stream_events yields items from queue; stop_reason returns correct value; get_messages returns history. failure — concurrent prompt raises RuntimeError; stop_reason before completion raises RuntimeError; queue full blocks push; background task failure propagated to stream_events consumer (not hung). Evidence: `.omo/evidence/task-2-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp-agent): implement ACPClientAdapter non-blocking methods with error propagation

- [x] 3. Bifurcate ACPClientHandler.session_update() — state updates in-place, stream data to queue
  What to do / Must NOT do: Modify `ACPClientHandler.session_update()` at `client_handler.py:118-206`. Process state updates (`CurrentModeUpdate`, `CurrentModelUpdate`, `ConfigOptionUpdate`, `AvailableCommandsUpdate`) in-place — do NOT push to queue. Push only stream-data updates (`AgentMessageChunk`, `ToolCallStart`, `ToolCallComplete`, `ToolCallProgress`) to the adapter's async queue. Must NOT change existing state tracking behavior. Must NOT push state updates to stream queue.
  Parallelization: Wave 1 | Blocked by: T1 | Blocks: T6 | Can parallelize with: T2
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-client-adapter/spec.md:30-42` (bifurcation scenarios); `src/agentpool/agents/acp_agent/client_handler.py:118-206` (session_update); `src/agentpool/agents/acp_agent/session_state.py:37,69-79` (deque mechanism); design.md risk "[ACPClientHandler state update routing]"
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/test_client_handler.py -v -k "bifurcation or state_update"` passes. State updates processed in-place. Stream data pushed to queue.
  QA scenarios: happy — CurrentModelUpdate updates internal state, NOT pushed to queue; AgentMessageChunk pushed to queue, NOT processed as state. failure — queue does not receive state updates; internal state not modified by stream data. Evidence: `.omo/evidence/task-3-acp-proxy-chain-refactor.log`
  Commit: Y | refactor(acp-agent): bifurcate session_update into state and stream paths

- [x] 4. Fix ACPAgent — replace _stream_events body (delegate to ACPTurn), fix create_turn, fix _interrupt
  What to do / Must NOT do: Replace `ACPAgent._stream_events()` body (line 412-611) to delegate to `create_turn()` → `ACPTurn.execute()` (NOT delete — keep as thin wrapper for backward compat until Phase 6). Remove `poll_acp_events()` (line 467-484) and 50ms timeout loop. Fix `create_turn()` (line 632-662) — replace `cast("ACPClientProtocol", self._api)` with `ACPClientAdapter(self._api, self._client_handler)`. Fix `ACPTurn.execute()` (turn.py:136-260) — use `adapter.prompt()`, iterate `adapter.stream_events()`, access `adapter.stop_reason`, call `adapter.get_messages()`. Fix `_interrupt()` (line 664-679) — cancel stream iteration task, not `_prompt_task`. Must NOT delete `_stream_events()` — replace its body. Must NOT use `cast()`. Must NOT break `use_conductor: false` fallback (old path preserved until Phase 6).
  Parallelization: Wave 1 | Blocked by: T2 | Blocks: T6 | Can parallelize with: T5
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-client-adapter/spec.md:62-77` (ACPTurn uses adapter); `openspec/changes/acp-proxy-chain-refactor/specs/acp-single-execution-path/spec.md:18-30` (streaming uses ACPTurn); `src/agentpool/agents/acp_agent/acp_agent.py:412-611` (_stream_events), `:467-484` (poll_acp_events), `:632-662` (create_turn), `:664-679` (_interrupt); `src/agentpool/agents/acp_agent/turn.py:136-260` (execute); `src/agentpool/agents/agent.py` or `base_agent.py:1349` (`_run_stream_once` calls `_stream_events()`); design.md D2; Metis findings C4, M6 (don't delete _stream_events, defer to Phase 6)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/ -v` passes. `grep -n "cast.*ACPClientProtocol" src/agentpool/agents/acp_agent/` returns nothing. `grep -n "poll_acp_events" src/agentpool/agents/acp_agent/acp_agent.py` returns nothing. `_stream_events()` body delegates to `create_turn().execute()`. `use_conductor: false` still works (old path preserved).
  QA scenarios: happy — ACPAgent.run_stream() uses ACPTurn.execute() via _stream_events delegation; create_turn constructs ACPClientAdapter with both api and handler; _interrupt cancels stream iteration. failure — calling run_stream with no active session raises clear error; use_conductor=false falls back to old path. Evidence: `.omo/evidence/task-4-acp-proxy-chain-refactor.log`
  Commit: Y | fix(acp-agent): delegate _stream_events to ACPTurn, fix create_turn and _interrupt

- [x] 5. Delete ACPSessionState deque, create ACPState dataclass
  What to do / Must NOT do: Delete the `deque[SessionUpdate]` from `session_state.py:37`. Delete `pop_update()` (line 76-79), `add_update()` (line 69-74). Preserve `current_model_id`, `models`, `modes`, `config_options`, `available_commands` fields. Rename class to `ACPState`. Update all imports. Must NOT delete model/mode/config/commands state tracking. Must NOT break session load replay (`start_load`/`finish_load` at line 86-95).
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T6 | Can parallelize with: T4
  References: `openspec/changes/acp-proxy-chain-refactor/tasks.md:15` (task 1.13); `src/agentpool/agents/acp_agent/session_state.py:1-96`; design.md risk "[ACPSessionState deletion scope]"; `src/agentpool/agents/acp_agent/client_handler.py` (imports ACPSessionState)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/ -v` passes. `grep -rn "ACPSessionState" src/` returns nothing. `grep -rn "pop_update\|add_update" src/agentpool/agents/acp_agent/` returns nothing. Model/mode/config fields preserved in ACPState.
  QA scenarios: happy — ACPState has model/mode/config/commands fields; session load replay works; imports updated. failure — accessing deleted deque methods raises AttributeError; model switching still works. Evidence: `.omo/evidence/task-5-acp-proxy-chain-refactor.log`
  Commit: Y | refactor(acp-agent): delete ACPSessionState deque, rename to ACPState

- [x] 6. Write Phase 1 tests — adapter, handler bifurcation, integration
  What to do / Must NOT do: Write unit tests for ACPClientAdapter (prompt non-blocking, stream_events queue, stop_reason property, get_messages, concurrent prompt rejection, queue backpressure, background task error propagation). Write unit tests for ACPClientHandler bifurcation. Write integration test: ACPAgent.run_stream() uses ACPTurn (no polling, _stream_events delegates to ACPTurn). Follow patterns in `tests/agents/acp_agent/test_acp_turn_hooks.py` (fake ACP client). Use `@pytest.mark.unit` / `@pytest.mark.integration`. Must NOT use real subprocess in unit tests.
  Parallelization: Wave 1 | Blocked by: T2, T3, T4, T5 | Blocks: T7, T8
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-client-adapter/spec.md:44-60` (backpressure, rejection); `tests/agents/acp_agent/test_acp_turn_hooks.py:1-60` (test patterns); `tests/conftest.py` (fixtures, TestModel)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/test_adapter.py tests/agents/acp_agent/test_client_handler.py -v` passes. `uv run pytest tests/agents/acp_agent/ -v -m unit` passes. Coverage > 80% for `adapter.py` and `client_handler.py`.
  QA scenarios: happy — all adapter methods tested; handler bifurcation verified; integration confirms ACPTurn execution. failure — concurrent prompt raises RuntimeError; background task error propagated; queue full blocks push. Evidence: `.omo/evidence/task-6-acp-proxy-chain-refactor.log`
  Commit: Y | test(acp-agent): add Phase 1 tests for ACPClientAdapter and handler bifurcation

- [x] 7. Create src/acp/proxy/ package — protocol, connection, constants
  What to do / Must NOT do: Create `src/acp/proxy/__init__.py`, `protocol.py` (Proxy typing.Protocol with `proxy_initialize()` returning `intercepted_methods` list, `proxy_successor(method, params, meta)`), `connection.py` (ProxySideConnection wrapping Connection), `constants.py` (PROXY_INITIALIZE, PROXY_SUCCESSOR). Follow patterns from `src/acp/connection.py`, `src/acp/agent/protocol.py`, `src/acp/client/protocol.py`. Must NOT modify existing protocols. Must NOT use `abc.ABC`.
  Parallelization: Wave 2 | Blocked by: T0 | Blocks: T8, T9
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:26-57`; `src/acp/connection.py` (Connection, 272 lines); `src/acp/agent/protocol.py` (Agent, 89 lines); `src/acp/client/protocol.py` (Client, 72 lines); `src/acp/AGENTS.md` (conventions)
  Acceptance criteria: `uv run python -c "from acp.proxy import Proxy, ProxySideConnection; print('ok')"` succeeds. `uv run ruff check src/acp/proxy/` passes. `uv run --no-group docs mypy src/acp/proxy/` passes.
  QA scenarios: happy — Proxy has proxy_initialize/proxy_successor; ProxySideConnection dispatches; constants defined. failure — calling proxy_successor on non-Proxy raises TypeError. Evidence: `.omo/evidence/task-7-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): create proxy package with Proxy protocol and ProxySideConnection

- [x] 8. Create Conductor class with MessageNode inheritance + ACPClientHandler ownership
  What to do / Must NOT do: Create `src/acp/conductor.py` with `Conductor(MessageNode[ChatMessage, ChatMessage[str]])`. Implement subprocess spawning using anyio task groups. Transfer `ACPClientHandler` ownership from `ACPAgent` to `Conductor` — Conductor wires subprocess JSON-RPC connection to both `ClientSideConnection` (notifications) and `AgentSideConnection` (requests). Implement `_step` property. Must NOT leave `ACPClientHandler` owned by `ACPAgent`. Must NOT use `subprocess.Popen` directly.
  Parallelization: Wave 2 | Blocked by: T6, T7 | Blocks: T9, T10, T11
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:3-25`; `src/agentpool/messaging/messagenode.py` (MessageNode); `src/agentpool/messaging/graph_adapter.py` (_step); `src/acp/bridge/bridge.py` (ACPBridge — spawn subprocess, ClientSideConnection, 232 lines); `src/agentpool/agents/acp_agent/client_handler.py` (ACPClientHandler — to be owned by Conductor); design.md D1, D8; Metis finding M3 (handler lifecycle)
  Acceptance criteria: `uv run python -c "from acp.conductor import Conductor; print('ok')"` succeeds. `uv run ruff check src/acp/conductor.py` passes. `uv run --no-group docs mypy src/acp/conductor.py` passes. Conductor is MessageNode subclass. Conductor owns ACPClientHandler.
  QA scenarios: happy — Conductor inherits MessageNode; has _step; uses anyio task groups; owns ACPClientHandler. failure — instantiating without config raises error; _step returns valid Step. Evidence: `.omo/evidence/task-8-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): create Conductor with MessageNode inheritance and handler ownership

- [x] 9. Implement Conductor chain initialization + terminal/proxy detection
  What to do / Must NOT do: Implement chain init — call `proxy/initialize` on each proxy from client toward terminal agent, then `initialize` on terminal agent (last component). Determine terminal vs proxy by chain position. Establish `proxy/successor` forwarding. Must NOT detect from responses — know from configuration. Must NOT send `proxy/initialize` to terminal agent.
  Parallelization: Wave 2 | Blocked by: T8 | Blocks: T10, T12
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:7-25,58-71`; design.md D6; `src/acp/agent/acp_agent_api.py:59-80` (initialize pattern)
  Acceptance criteria: `uv run pytest tests/acp/test_conductor.py -v -k "init"` passes. Conductor sends `proxy/initialize` to proxies, `initialize` to terminal. Zero-proxy case connects directly.
  QA scenarios: happy — N-proxy chain initializes in order; zero-proxy works; terminal receives initialize. failure — proxy crash during init aborts and cleans up; terminal not responding raises error. Evidence: `.omo/evidence/task-9-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement Conductor chain initialization and terminal detection

- [x] 10. Implement Conductor message routing, passthrough, and error propagation
  What to do / Must NOT do: Implement bidirectional `proxy/successor` forwarding. Implement passthrough — use `intercepted_methods` to skip deserialization for unregistered types. Implement error propagation — proxy exceptions produce JSON-RPC error responses, NO silent skipping. Must NOT silently skip failed proxies. Must NOT always deserialize.
  Parallelization: Wave 2 | Blocked by: T9 | Blocks: T12 | Can parallelize with: T11
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:37-47,96-110`; design.md D5; risk "[Proxy chain error propagation]"
  Acceptance criteria: `uv run pytest tests/acp/test_conductor.py -v -k "routing or passthrough or error"` passes. Passthrough forwards raw message. Error produces JSON-RPC error response.
  QA scenarios: happy — message forwarded through chain; passthrough skips deserialization; error forwarded back. failure — proxy exception produces error response (not silent); broken chain detected. Evidence: `.omo/evidence/task-10-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement Conductor message routing, passthrough, and error propagation

- [x] 11. Implement Conductor _step property and async context manager
  What to do / Must NOT do: Implement `_step` for pydantic-graph integration. Implement async context manager — cleanup subprocesses in `finally` block. Store proxy chain as mutable list (allow future hot-swapping, though API not implemented). Must NOT leave orphaned subprocesses. Must NOT cancel `run_ctx.current_task`.
  Parallelization: Wave 2 | Blocked by: T8 | Blocks: T12, T13 | Can parallelize with: T10
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:86-94`; `src/agentpool/messaging/graph_adapter.py` (Step pattern); `src/acp/bridge/bridge.py` (cleanup); design.md D1; Metis finding L4 (mutable list for future hot-swap)
  Acceptance criteria: `uv run pytest tests/acp/test_conductor.py -v -k "step or context or cleanup"` passes. _step returns valid Step. Context manager cleans up subprocesses.
  QA scenarios: happy — _step returns valid Step; exit cleans up; no orphans. failure — subprocess crash detected; cleanup in finally; error raised. Evidence: `.omo/evidence/task-11-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement Conductor _step property and async context manager

- [x] 12. Write Phase 2 tests — Conductor chain init, routing, passthrough, errors
  What to do / Must NOT do: Write unit tests for chain init (zero proxies, N proxies, terminal detection). Write unit tests for routing (forward, passthrough, intercept, error). Use fake/mock proxies. Must NOT use real subprocess in unit tests.
  Parallelization: Wave 2 | Blocked by: T9, T10, T11 | Blocks: T13
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md`; `tests/agents/acp_agent/test_acp_turn_hooks.py` (fake patterns); `tests/conftest.py`
  Acceptance criteria: `uv run pytest tests/acp/test_conductor.py tests/acp/test_proxy_protocol.py -v` passes. Coverage > 80% for `conductor.py` and `proxy/protocol.py`.
  QA scenarios: happy — zero-proxy init; N-proxy init in order; passthrough skips deserialization; error as JSON-RPC. failure — proxy crash aborts and cleans up; error not skipped; orphans cleaned. Evidence: `.omo/evidence/task-12-acp-proxy-chain-refactor.log`
  Commit: Y | test(acp): add Phase 2 tests for Conductor and Proxy protocol

- [ ] 13. Rewrite ACPAgent — init, output type, create_turn, run_stream
  What to do / Must NOT do: Rewrite `ACPAgent.__init__()` (acp_agent.py:129-211) — accept optional `proxy_chain` config, create Conductor instead of direct subprocess. Change output type from `str` to `ChatMessage[str]`. Rewrite `create_turn()` — construct `ACPClientAdapter` from Conductor's connection + handler. Rewrite `run_stream()` — delegate to `ACPTurn.execute()` via graph Step. Add `use_conductor` feature flag (default: true). Must NOT break configs without `proxy_chain`. Must NOT remove `use_conductor: false` fallback.
  Parallelization: Wave 3 | Blocked by: T6, T12 | Blocks: T14, T17 | Can parallelize with: T14
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-single-execution-path/spec.md:1-30`; `src/agentpool/agents/acp_agent/acp_agent.py:129-211,632-662`; `src/agentpool/agents/agent.py` (BaseAgent); `src/agentpool/messaging/messagenode.py` (ChatMessage); design.md D2
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/ -v` passes. Output type is `ChatMessage[str]`. `use_conductor: false` falls back. `use_conductor: true` uses Conductor.
  QA scenarios: happy — use_conductor=true creates Conductor; create_turn constructs adapter; run_stream delegates to ACPTurn; backward compat. failure — invalid proxy_chain raises error; use_conductor=false falls back. Evidence: `.omo/evidence/task-13-acp-proxy-chain-refactor.log`
  Commit: Y | refactor(acp-agent): rewrite ACPAgent to use Conductor, output ChatMessage[str]

- [ ] 14. Create ProxyChainConfig model + migrate ToolManagerBridge to ToolsetFactory
  What to do / Must NOT do: Create `ProxyChainConfig` Pydantic model with `type` discriminator (unknown types raise `ValidationError` at config load time with message "Unknown proxy type: {type}"). Add `proxy_chain: list[ProxyChainConfig] | None` to `ACPAgentConfig` (base.py:218). Add `use_conductor: bool = True` to `BaseACPAgentConfig` (base.py:31). Migrate `ToolManagerBridge` to `ToolsetFactory` (NOT `ResourceProvider` — deprecated at `resource_providers/base.py:90`). Must NOT use `ResourceProvider`. Must NOT use `getattr` for discrimination.
  Parallelization: Wave 3 | Blocked by: T0 | Blocks: T17 | Can parallelize with: T13
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-chain/spec.md:73-85`; `src/agentpool/models/acp_agents/base.py:31,218`; `src/agentpool/tools/factory.py:19` (ToolsetFactory); `src/agentpool/resource_providers/base.py:90` (deprecated warning); `src/agentpool/agents/acp_agent/acp_agent.py:162,209` (ToolManagerBridge); design.md D7; Metis findings C2, M4
  Acceptance criteria: `uv run python -c "from agentpool.models.acp_agents.base import ACPAgentConfig; c = ACPAgentConfig(name='test', command='echo'); print(c.use_conductor)"` outputs `True`. Unknown proxy type raises `ValidationError`. `grep -n "ToolManagerBridge" src/agentpool/agents/acp_agent/acp_agent.py` returns nothing. `grep -n "ResourceProvider" src/agentpool/agents/acp_agent/` returns nothing.
  QA scenarios: happy — proxy_chain parses; use_conductor defaults True; ToolsetFactory used. failure — unknown type raises ValidationError at load time; missing type raises error. Evidence: `.omo/evidence/task-14-acp-proxy-chain-refactor.log`
  Commit: Y | feat(config): add ProxyChainConfig, migrate ToolManagerBridge to ToolsetFactory

- [ ] 15. Update AgentPool to pass proxy chain config to ACPAgent
  What to do / Must NOT do: Update `AgentPool` to pass proxy chain config to ACPAgent during instantiation. Wire config from YAML through to Conductor. Must NOT break existing agent instantiation.
  Parallelization: Wave 3 | Blocked by: T13 | Blocks: T17
  References: `src/agentpool/delegation/pool.py` (AgentPool); `src/agentpool/models/acp_agents/base.py:187` (get_agent method)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/ -v` passes. AgentPool passes proxy_chain config.
  QA scenarios: happy — config flows from YAML to Conductor. failure — missing config handled gracefully. Evidence: `.omo/evidence/task-15-acp-proxy-chain-refactor.log`
  Commit: Y | refactor(pool): pass proxy chain config to ACPAgent during instantiation

- [ ] 16. Write Phase 3 tests — Conductor integration, backward compat, multi-turn
  What to do / Must NOT do: Write integration test: ACPAgent with Conductor + zero proxies (backward compat). Write integration test: ACPAgent with Conductor + proxy chain. Write integration test: multi-turn run (3 turns with steer/followup through Conductor — verify hooks fire per-turn, events stream correctly across turns). Verify existing tests pass with `use_conductor: true`. Must NOT use real subprocess in unit tests.
  Parallelization: Wave 3 | Blocked by: T13, T14, T15 | Blocks: T18, T23
  References: `openspec/changes/acp-proxy-chain-refactor/tasks.md:49-52`; `tests/agents/acp_agent/`; `tests/conftest.py`; Metis finding L3 (multi-turn test)
  Acceptance criteria: `uv run pytest tests/agents/acp_agent/ -v -m integration` passes. `uv run pytest tests/agents/acp_agent/ -v` passes (no regressions). Multi-turn test verifies per-turn hook firing.
  QA scenarios: happy — zero-proxy works; proxy chain works; multi-turn hooks fire per-turn; existing tests pass. failure — use_conductor=false works; invalid config raises error; multi-turn hooks not double-fired. Evidence: `.omo/evidence/task-16-acp-proxy-chain-refactor.log`
  Commit: Y | test(acp-agent): add Phase 3 integration tests including multi-turn

- [ ] 17. Create proxy type registry + impls package
  What to do / Must NOT do: Create proxy type registry — map string discriminators to proxy classes. Create `src/acp/proxy/impls/__init__.py`. Follow existing registry patterns (entry points). Must NOT hardcode proxy types in Conductor.
  Parallelization: Wave 4 | Blocked by: T12 | Blocks: T18, T19, T20
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-impls/spec.md:99-107`; `src/acp/proxy/protocol.py`; `pyproject.toml` (entry points)
  Acceptance criteria: `uv run python -c "from acp.proxy.impls import ProxyRegistry; print('ok')"` succeeds. Registry maps types to classes. Unregistered type raises error.
  QA scenarios: happy — registered type returns class; unregistered raises error. failure — duplicate registration raises error. Evidence: `.omo/evidence/task-17-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): create proxy type registry and impls package

- [ ] 18. Implement HookProxy — all 4 hook type mappings
  What to do / Must NOT do: Implement `HookProxy` in `src/acp/proxy/impls/hook_proxy.py` implementing `Proxy` protocol. Wrap existing `Hook` instances. Map all 4 hooks: `session/prompt` → `pre_turn` (blocking deny, additional_context), `session/update` ToolCallStart → `pre_tool_use` (modified_input, blocking deny), `session/update` ToolCallComplete → `post_tool_use` (modified_output), JSON-RPC response to `session/prompt` → `post_turn` (correlate by request ID, NOT on individual chunks). Must NOT fire `post_turn` on individual `AgentMessageChunk`. Must NOT modify existing Hook classes. `PermissionHookProxy` from proposal is subsumed by HookProxy's `pre_tool_use` blocking.
  Parallelization: Wave 4 | Blocked by: T17 | Blocks: T19, T21 | Can parallelize with: T20
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-impls/spec.md:3-41`; `src/agentpool/hooks/agent_hooks.py` (Hook, CallableHook, CommandHook, PromptHook, HookInput, HookResult); design.md D4; `src/agentpool/agents/acp_agent/acp_converters.py` (ACP message types); Metis finding M7 (PermissionHookProxy subsumed)
  Acceptance criteria: `uv run pytest tests/acp/test_hook_proxy.py -v` passes. HookProxy implements Proxy. All 4 hooks mapped. Deny blocks.
  QA scenarios: happy — pre_turn injects/denies; pre_tool_use modifies/denies; post_tool_use modifies; post_turn on JSON-RPC response. failure — deny blocks; no matching hooks = passthrough; post_turn NOT on chunks. Evidence: `.omo/evidence/task-18-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement HookProxy with all 4 hook type mappings

- [ ] 19. Implement HookProxy/HookAwareTurn coexistence + auto-insert + disable request_permission
  What to do / Must NOT do: Implement coexistence — Conductor passes `_hooks=None` to ACPTurn when HookProxy in chain (HookAwareTurn guard skips). Pass agent's `AgentHooks` when no HookProxy. Implement Conductor auto-insert HookProxy at position 0 when agent has hooks. **Disable `ACPClientHandler.request_permission()` hook firing when HookProxy is active** — Conductor signals handler to skip hooks (prevent double-firing). Must NOT use `hooks_fired` guard. Must NOT double-fire hooks.
  Parallelization: Wave 4 | Blocked by: T18 | Blocks: T21
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-impls/spec.md:42-67`; `src/agentpool/orchestrator/turn.py:78,135` (HookAwareTurn, _hooks=None guard); `src/agentpool/agents/acp_agent/client_handler.py` (request_permission method — search for it); design.md D9; Metis finding M2 (double-firing via request_permission)
  Acceptance criteria: `uv run pytest tests/acp/test_hook_proxy.py -v -k "coexistence or auto_insert or request_permission"` passes. HookProxy in chain → _hooks=None → HookAwareTurn skips → request_permission hooks disabled. No HookProxy → hooks passed to ACPTurn → request_permission active.
  QA scenarios: happy — HookProxy active, HookAwareTurn disabled, request_permission disabled (no double-firing); no HookProxy, both active; auto-insert at position 0. failure — hooks double-fired; _hooks=None not set; request_permission still fires. Evidence: `.omo/evidence/task-19-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement HookProxy coexistence, auto-insert, and request_permission disable

- [ ] 20. Implement ContextInjectionProxy + ToolProviderProxy
  What to do / Must NOT do: Implement `ContextInjectionProxy` (`src/acp/proxy/impls/context_injection.py`) — intercept `session/prompt`, prepend AGENTS.md and skill instructions. Implement `ToolProviderProxy` (`src/acp/proxy/impls/tool_provider.py`) — reuse `AcpMcpTransport`/`AcpMcpConnectionManager` for MCP-over-ACP (experimental). Register both in registry. Must NOT conflate with HookProxy's additional_context.
  Parallelization: Wave 4 | Blocked by: T17 | Blocks: T21 | Can parallelize with: T18
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-impls/spec.md:68-98`; `src/agentpool/skills/`; `src/agentpool_server/acp_server/acp_mcp_transport.py:30` (AcpMcpTransport); `src/agentpool_server/acp_server/acp_mcp_manager.py:253` (AcpMcpConnectionManager); design.md risk "[Two unratified RFDs]"
  Acceptance criteria: `uv run pytest tests/acp/test_context_injection_proxy.py tests/acp/test_tool_provider_proxy.py -v` passes. Both registered in registry.
  QA scenarios: happy — AGENTS.md prepended; skills injected; tools available. failure — missing AGENTS.md handled; MCP failure raises error. Evidence: `.omo/evidence/task-20-acp-proxy-chain-refactor.log`
  Commit: Y | feat(acp): implement ContextInjectionProxy and ToolProviderProxy (experimental)

- [ ] 21. Write Phase 4 tests — HookProxy, coexistence, ContextInjection, ToolProvider
  What to do / Must NOT do: Write unit tests for HookProxy (all 4 hooks, deny/allow/modify, blocking, JSON-RPC correlation). Write tests for coexistence (_hooks=None, no double-firing, request_permission disabled). Write tests for ContextInjectionProxy. Write tests for ToolProviderProxy. Must NOT use real subprocess.
  Parallelization: Wave 4 | Blocked by: T18, T19, T20 | Blocks: T22
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-proxy-impls/spec.md`; `tests/agents/acp_agent/test_acp_turn_hooks.py`
  Acceptance criteria: `uv run pytest tests/acp/test_hook_proxy.py tests/acp/test_context_injection_proxy.py tests/acp/test_tool_provider_proxy.py -v` passes. Coverage > 80%.
  QA scenarios: happy — all hooks tested; coexistence verified; context injection; tool provider. failure — deny blocks; no double-firing; missing files; MCP failures. Evidence: `.omo/evidence/task-21-acp-proxy-chain-refactor.log`
  Commit: Y | test(acp): add Phase 4 tests for all built-in proxy implementations

- [ ] 22. Refactor AgentPoolACPAgent as terminal agent + remove legacy dual path + split ACPEventConverter
  What to do / Must NOT do: Refactor `AgentPoolACPAgent` to operate as terminal agent behind Conductor — respond to `initialize` (not `proxy/initialize`). Remove legacy `ACPSession.process_prompt()` dual path — consolidate to `ACPProtocolHandler.handle_prompt()`. Split `ACPEventConverter` refactoring into: (a) define proxy component interface, (b) extract stateless conversion functions, (c) implement proxy wrapper, (d) migrate callers. Verify `ACPProtocolHandler` (ProtocolEventConsumerMixin) works unchanged. Must NOT break existing ACP server. Must NOT remove ACPProtocolHandler.
  Parallelization: Wave 5 | Blocked by: T16, T21 | Blocks: T23
  References: `openspec/changes/acp-proxy-chain-refactor/specs/acp-server/spec.md:32-45`; `openspec/changes/acp-proxy-chain-refactor/specs/acp-single-execution-path/spec.md:1-30`; `src/agentpool_server/acp_server/acp_agent.py` (1150 lines); `src/agentpool_server/acp_server/session.py` (939 lines); `src/agentpool_server/acp_server/handler.py` (774 lines); `src/agentpool_server/acp_server/event_converter.py` (912 lines); Metis finding M1 (split ACPEventConverter)
  Acceptance criteria: `uv run pytest tests/servers/acp_server/ -v` passes. AgentPoolACPAgent responds to `initialize`. Legacy `process_prompt()` removed. ACPEventConverter refactored into proxy component.
  QA scenarios: happy — terminal agent in chain; prompt routes through handle_prompt; event converter as proxy. failure — legacy path not reachable; converter broken. Evidence: `.omo/evidence/task-22-acp-proxy-chain-refactor.log`
  Commit: Y | refactor(acp-server): terminal agent, remove dual path, split ACPEventConverter

- [ ] 23. Write Phase 5 tests — terminal agent integration, nested passthrough zero-conversion
  What to do / Must NOT do: Write integration test: AgentPoolACPAgent as terminal agent in Conductor chain. Write integration test: nested agentpool (server+client) with ZERO conversion — mock/spy `ACPEventConverter`, assert `call_count == 0` during passthrough. Must NOT use real LLM API — use TestModel or mock.
  Parallelization: Wave 5 | Blocked by: T22 | Blocks: T24
  References: `openspec/changes/acp-proxy-chain-refactor/tasks.md:78-79`; `openspec/changes/acp-proxy-chain-refactor/specs/acp-server/spec.md`; `tests/servers/acp_server/`; Metis finding M5 (measurable zero-conversion criteria)
  Acceptance criteria: `uv run pytest tests/servers/acp_server/ -v -m integration` passes. Terminal agent works. Passthrough test asserts `ACPEventConverter.call_count == 0`.
  QA scenarios: happy — terminal agent works; passthrough zero conversion (converter not called). failure — conversion still happening (converter called); terminal not responding to initialize. Evidence: `.omo/evidence/task-23-acp-proxy-chain-refactor.log`
  Commit: Y | test(acp-server): add Phase 5 integration tests with zero-conversion passthrough

- [ ] 24. Delete dead code, remove feature flag, simplify converters, docs, full validation
  What to do / Must NOT do: Delete `_stream_events()` method (now thin wrapper — safe to delete since use_conductor flag removed). Delete `ACPSessionState` remaining references. Delete `cast()` hack. Remove `use_conductor` feature flag (Conductor is only path). Simplify `acp_converters.py` — passthrough zero conversion. Remove `ToolManagerBridge` deprecated imports. Remove `AgentHooks` deprecation warnings (if any remain after unify-hook-system merge). Update AGENTS.md with proxy chain architecture. Add YAML config examples. Run full validation: `uv run pytest && uv run --no-group docs mypy src/ && uv run ruff check src/ && uv run ruff format --check src/`. Must NOT leave dead code or unused imports. Must NOT use `# type: ignore` without justification.
  Parallelization: Wave 6 | Blocked by: T23 | Blocks: F1-F4
  References: `openspec/changes/acp-proxy-chain-refactor/tasks.md:83-95`; `src/agentpool/agents/acp_agent/acp_agent.py`; `src/agentpool/agents/acp_agent/acp_converters.py`; `src/agentpool/hooks/agent_hooks.py`; `AGENTS.md`; `site/examples/*/config.yml`
  Acceptance criteria: `grep -rn "ACPSessionState\|poll_acp_events\|_stream_events\|cast.*ACPClientProtocol\|use_conductor\|ToolManagerBridge" src/` returns nothing. `uv run pytest` passes (0 failures). `uv run --no-group docs mypy src/` passes (0 errors). `uv run ruff check src/` passes (0 issues). `uv run ruff format --check src/` passes. `grep -n "proxy.chain\|Conductor\|HookProxy" AGENTS.md` returns matches.
  QA scenarios: happy — all dead code removed; tests pass; mypy clean; ruff clean; format clean; docs updated; YAML examples valid. failure — any test failure; any type error; any lint issue; any dead code found. Evidence: `.omo/evidence/task-24-acp-proxy-chain-refactor.log`
  Commit: Y | chore(acp): delete dead code, remove feature flag, simplify, update docs, full validation

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verify all todos match OpenSpec change specs (proposal, design, tasks, 6 spec files)
- [ ] F2. Code quality review — `uv run ruff check src/` + `uv run --no-group docs mypy src/` both clean, no `cast()`/`getattr`/`hasattr`/`as any`
- [ ] F3. Real manual QA — `uv run pytest` full suite passes, `agentpool run <acp_agent> "test prompt"` works end-to-end
- [ ] F4. Scope fidelity — verify no out-of-scope items implemented (no remote transport, no session fork, no native agent proxy chains, no hot-swap)

## Commit strategy

- One commit per todo (25 commits total, including Phase 0 merge)
- Commit type: `feat(acp)` for new features, `refactor(acp-agent)` for refactors, `fix(acp-agent)` for fixes, `test(acp)` for tests, `chore(acp)` for cleanup, `docs` for documentation
- Each commit message follows conventional commits format
- All commits on `feature/acp-proxy-chain-refactor` branch
- Final PR merges to main after all verification passes

## Success criteria

1. `unify-hook-system` merged — HookAwareTurn, hooks_fired, renamed methods exist on working branch
2. ACPAgent uses Conductor with proxy chain — no direct subprocess management
3. ACPTurn.execute() is the single execution path — _stream_events deleted, no polling
4. ACPClientAdapter provides non-blocking prompt + async queue streaming + error propagation
5. Proxy chain supports HookProxy, ContextInjectionProxy, ToolProviderProxy
6. HookProxy/HookAwareTurn coexist via _hooks=None — no double-firing (request_permission disabled when HookProxy active)
7. Passthrough scenarios produce zero event conversion (ACPEventConverter.call_count == 0)
8. AgentPoolACPAgent operates as terminal agent behind Conductor
9. ACPClientHandler owned by Conductor (not ACPAgent)
10. All dead code deleted (ACPSessionState deque, poll_acp_events, _stream_events, cast hack, ToolManagerBridge, use_conductor flag)
11. `uv run pytest` passes with 0 failures
12. `uv run --no-group docs mypy src/` passes with 0 errors
13. `uv run ruff check src/` passes with 0 issues
14. YAML `proxy_chain:` config works with type discriminator (unknown types raise ValidationError at load time)
15. Multi-turn runs work through Conductor (hooks fire per-turn)
