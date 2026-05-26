# Learnings

## 2026-04-24 Session Start
- RFC-0028 implements delegation provider session adaptation
- Option 1 chosen: providers call SessionManager.create_child_session() and emit lifecycle events themselves
- SessionManager.create_child_session() already exists in src/agentpool/sessions/manager.py
- AgentRunContext has no depth field currently
- SubagentTools uses getattr(ctx, "current_depth", 0) - must be replaced
- Workers hardcodes depth=1
- ensure_session() can overwrite SessionData - store-first is prerequisite
- tests/sessions/test_session_hierarchy.py is currently skipped

## 2026-04-24 T2: Delegation Depth Guard Primitives
- DelegationDepthError added as RuntimeError subclass (follows existing pattern in exceptions.py)
- MAX_DELEGATION_DEPTH = 10 as module-level constant
- Exception stores current_depth and max_depth as attributes, defaults max_depth to MAX_DELEGATION_DEPTH
- Both exported from agentpool.agents.__init__.py
- Test file: tests/agents/test_delegation_depth_error.py (9 tests, all passing)

## 2026-04-24 T4: Session Hierarchy Tests Revived
- tests/sessions/test_session_hierarchy.py had `SessionManager = None` skip pattern causing all 6 tests to skip
- SessionManager API does NOT have: `create()`, `get()`, `list_sessions()` — these were assumed APIs
- Current SessionManager API: `create_child_session(parent_session_id, agent_name, agent_type)`, `get_child_sessions(parent_session_id)`
- Root sessions must be created via `store.save(SessionData(...))` directly — no `manager.create()` exists
- `store.load(session_id)` replaces `manager.get(session_id)`
- `store.list_sessions(parent_id=...)` or `manager.get_child_sessions(parent_id)` replaces `manager.list_sessions(parent_id=...)`
- SQLSessionStore from agentpool_storage.session_store implements same SessionStore protocol
- MemorySessionStore from agentpool.sessions.store is the in-memory implementation
- mock_pool fixture needs `pool.manifest.name` attribute (not `pool.all_agents`)
- All 133 session tests pass after revival (6 hierarchy + 4 parent-edge + 123 others)

## 2026-04-24 T3: SourceType helpers
- SubAgentType was a phantom import in team.py/teamrun.py (under TYPE_CHECKING) — it never existed in events.py
- Replaced with SourceType = Literal["agent", "team_parallel", "team_sequential"] in messagenode.py
- get_source_type() uses local imports (Team, BaseTeam) to avoid circular deps
- Team is checked before BaseTeam because Team IS a BaseTeam — order matters
- agent_type property on MessageNode delegates to get_source_type() by default; subclasses can override
- The "native" value is NOT a valid SourceType — persistence-domain only (agent_type), event-domain uses "agent"
- Inline match/case in team.py/teamrun.py run_stream replaced with get_source_type() helper

## 2026-04-24 T6: Session ID Format Dependency Audit

### identifier.ascending("session") Usage Sites (PRODUCTION CODE)

1. **`src/agentpool_toolsets/builtin/workers.py`** — lines 106, 107, 197, 198
   - Provider site (generates child/parent session IDs for worker runs)
   - Pending T15 removal — will delegate to SessionManager.create_child_session()

2. **`src/agentpool_toolsets/builtin/subagent_tools.py`** — lines 91, 92, 340, 341
   - Provider site (generates child/parent session IDs for subagent runs)
   - Pending T15 removal — will delegate to SessionManager.create_child_session()

3. **`src/agentpool_server/opencode_server/routes/session_routes.py`** — lines 591, 892
   - OpenCode server routes (create_session, fork session)
   - These use `identifier.ascending("session")` directly — server-level generation
   - Could switch to `generate_session_id()` or stay as-is (same format currently)

4. **`src/agentpool/utils/identifiers.py`** — line 122
   - The `generate_session_id()` convenience function itself calls `ascending("session")`
   - This is the canonical location, not a dependency

### identifier.ascending("session") Usage Sites (DOCS — not blocking)

- `docs/rfcs/draft/RFC-0027-acp-subagent-zed-compatibility.md` — line 966
- `docs/rfcs/draft/RFC-0028-delegation-provider-session-adaptation.md` — lines 53, 83, 84, 103, 454, 455, 512, 513, 1277
- `docs/rfcs/implemented/RFC-0001-workers-teams-session-management.md` — lines 59, 384, 391, 392, 430, 468
- `docs/rfcs/implemented/RFC-0014-spawn-session-events.md` — line 259

### Session ID Parsing — NONE FOUND

- No regex patterns matching `session_\d+` or `ses_\d+` exist in production code
- No code parses session ID counters or assumes sequential format
- `session_id[-8:]` in `src/agentpool_commands/text_sharing/opencode.py` is format-agnostic (takes last 8 chars of any string)
- All session lookups use IDs as opaque dictionary keys: `.get(session_id)`, `dict[session_id]`

### ACP Session Manager

- `ACPSessionManager.create_session()` uses `self.storage.generate_session_id()` which delegates to `identifiers.generate_session_id()` → `ascending("session")`
- Lookups via `_active.get(session_id)` — fully opaque

### OpenCode Server Session Lookups

- `ServerState.sessions` dict uses session_id as opaque key
- `state.messages[session_id]`, `state.session_status[session_id]`, etc. — all opaque dict lookups
- `get_or_load_session(state, session_id)` — opaque string parameter

### Conclusion

- Session IDs are already treated as opaque strings throughout the codebase
- No production code depends on the sequential/ascending format
- The switch from `identifier.ascending("session")` to a different provider (e.g., UUID4) is safe
- Regression test added: `tests/sessions/test_session_id_opaque.py` (23 tests)

## 2026-04-24 T5: AgentContext.create_child_session() Convenience API
- Added async method to AgentContext (not AgentRunContext) as specified
- Method signature: `async def create_child_session(self, agent_name: str, agent_type: str, parent_session_id: str | None = None) -> str`
- Accesses pool via `self.node.agent_pool` (from MessageNode base), sessions via `pool.sessions`
- Uses `self.node.session_id` as default parent when parent_session_id is None
- Fallback chain: pool+sessions → SessionManager.create_child_session(); no pool or no sessions → generate_session_id()
- Edge case: when both parent_session_id and node.session_id are None, falls back to generate_session_id() (can't call create_child_session with None parent)
- No getattr/hasattr used — explicit None checks only
- No events emitted from this method (as specified)
- Tests: 5 passing (pool-backed with inheritance, explicit parent, no pool, pool without sessions, no node session_id)

## 2026-04-24 T8: AgentRunContext.session_id Deprecation Descriptor

### Approach
- Used `_DeprecatedField` data descriptor (defines both `__get__` and `__set__`) to intercept ALL access
- Descriptor is assigned to `AgentRunContext.session_id` AFTER the `@dataclass` decorator runs
- This works because Python's MRO checks data descriptors on the type BEFORE instance `__dict__`
- The dataclass machinery still registers `session_id` in `__dataclass_fields__`, so `asdict()` works

### Key Design Decisions
- Kept `session_id` as `init=True` (in `__init__`) for backward compatibility — existing code passes `AgentRunContext(session_id="foo")`
- Descriptor stores values in `instance.__dict__["_deprecated_session_id"]` (private key) to avoid collision with dataclass's own `__dict__` entries
- `__get__` emits `DeprecationWarning` and lazy-initializes the default UUID4 value on first access
- `__set__` emits `DeprecationWarning` and stores to the private key
- `asdict()` works because it calls `getattr()` which triggers `__get__` → returns the value

### Gotcha: ClassVar doesn't work
- Tried `_session_id_desc: ClassVar[_DeprecatedField]` — ClassVar not imported, and even if it were, dataclass ignores ClassVar fields
- The descriptor must be assigned directly to the class attribute after the class is defined
- `AgentRunContext.session_id = _DeprecatedField(...)` after the class body does the trick

### Mypy
- `# type: ignore[assignment]` on the module-level assignment is needed (descriptor is not a `str`)
- mypy --strict passes on both context.py and base_agent.py

### Tests
- 11 tests in `tests/agents/test_session_id_deprecation.py` — all passing
- Covers: get warning, set warning, default UUID, roundtrip, per-instance isolation, class-level access, asdict inclusion, asdict value match, asdict warning, other fields unaffected, init param presence
- Pre-existing tests (contextvar, event_queue) continue to pass with deprecation warnings emitted

## 2026-04-24 T7: ensure_session() store-first and non-overwriting

- `session_data_to_opencode()` already exists in converters.py — `_session_from_session_data()` simply delegates to it
- `ensure_session()` now has 3 resolution layers: in-memory → store → create-new
- Concurrent callers for the same session_id are serialized with `session_locks[session_id]` (double-check locking)
- Store-first path: does NOT call `store.save()`, does NOT call `bind_agent_to_session()` for children
- Store-first path DOES: register in `state.sessions`, `ensure_runtime_session_state()`, `ensure_input_provider()`, `mark_session_idle()`, broadcast `session_created` + `session_updated`
- When `pool.sessions.store` is `None`, the code falls back to `pool.storage.load_session()` — test mocks need `AsyncMock(return_value=None)` on `pool.storage.load_session`
- `_create_and_persist_session()` extracted as private method to keep `ensure_session()` readable
- Test file: `test_ensure_session_store_first.py` — 12 tests covering TG-2/TG-5/TG-11/TG-17/TG-19/TG-32
- Also fixed `test_concurrent_messages.py` fixture to add `storage.load_session = AsyncMock(return_value=None)`
- Also fixed `test_ensure_session.py` fixture to add `pool.storage.load_session = AsyncMock(return_value=None)`
- `SessionData.agent_type` field exists but is NOT used in `session_data_to_opencode()` conversion — it's stored in the `SessionData` but not mapped to the UI `Session` model. This is correct per the current schema.

## 2026-04-24 T13: ACPSessionManager Child-Session Path

- Added `parent_session_id: str | None = None` parameter to `ACPSessionManager.create_session()`
- When `parent_session_id` is provided AND `self._pool.sessions is not None`: delegates to `self._pool.sessions.create_child_session(parent_session_id=..., agent_name=agent.name, agent_type="acp")` which inherits project_id/cwd from parent
- When `parent_session_id is None` or `self._pool.sessions is None`: preserves existing top-level behavior (direct SessionData save with computed project_id from cwd)
- `session_store` property now safely handles `self._pool.sessions is None` (returns None instead of AttributeError)
- ACP callers in `acp_agent.py` (new_session, load_session, fork_session, resume_session, prompt) are all top-level — no `parent_session_id` available from delegation context yet, so they all use the default `None`
- Child session gets `effective_cwd` from inherited parent data (falls back to provided cwd)
- If caller provides explicit `session_id` alongside `parent_session_id`, a warning is logged and the child-generated ID takes precedence
- Test file: `tests/servers/acp_server/test_acp_session_manager_child_session.py` — 5 tests (all passing)
  - test_top_level_session_has_no_parent
  - test_child_session_inherits_parent_project_id
  - test_child_session_uses_effective_cwd_for_acp_session
  - test_no_parent_session_id_preserves_existing_behavior
  - test_child_session_without_pool_sessions_falls_back_to_top_level
- All existing ACP tests pass (65/65, excluding 1 pre-existing snapshot failure in test_acp_via_acp_snapshots)
- All session tests pass (133/133)

## 2026-04-24 T12: TeamRun.run_stream() Depth + Child Sessions

- Added `depth: int = 0` parameter to `TeamRun.run_stream()` signature (alongside `require_all: bool = True`)
- Pops `session_id` from kwargs before forwarding: `kwargs.pop("session_id", None)` → stored as `parent_session_id`
- Pops `depth` from kwargs: `kwargs.pop("depth", None)` — explicit parameter takes precedence
- Computes `child_depth = depth + 1`, checks against `MAX_DELEGATION_DEPTH` → raises `DelegationDepthError` if exceeded
- For each member in sequence:
  - Creates child session: if pool available AND parent_session_id provided, uses `pool.sessions.create_child_session(parent_session_id=..., agent_name=member.name, agent_type=member.agent_type)`; else `generate_session_id()`
  - Emits `SpawnSessionStart(child_session_id=child_sid, parent_session_id=..., spawn_mechanism="spawn", source_type=get_source_type(member), source_name=member.name, depth=child_depth, description=...)`
  - Forwards `session_id=child_sid, parent_session_id=parent_session_id, depth=child_depth` to member's `run_stream()`
  - Wraps member events in `SubAgentEvent` with `depth=child_depth, child_session_id=child_sid, parent_session_id=parent_session_id`
  - Nested SubAgentEvents get `depth + 1` increment and preserve `child_session_id`/`parent_session_id`
- Sequential handoff unchanged: `current_message = (event.message.content,)` on `StreamCompleteEvent`
- `TeamRun.run()` is NOT modified (out of scope)
- Test file: `tests/teams/test_team_run_stream_depth.py` — 16 tests (all passing)
  - Covers: depth param, TypeError prevention, default depth, depth propagation, child sessions, SpawnSessionStart fields, pool-backed child sessions, fallback generate_session_id, sequential handoff, depth guard, nested SubAgentEvent depth, kwargs pop semantics, require_all behavior

## 2026-04-24 T9: SubagentTools Child Session Adaptation

### Changes Made to `subagent_tools.py`
1. **Removed `identifier` import** — `from agentpool.utils import identifiers as identifier` no longer needed
2. **Added imports**: `DelegationDepthError`, `MAX_DELEGATION_DEPTH` from `agentpool.agents.exceptions`
3. **`task()` method**: 
   - Computes `current_depth = ctx.run_ctx.depth if ctx.run_ctx is not None else 0`
   - Guards `if current_depth >= MAX_DELEGATION_DEPTH: raise DelegationDepthError(current_depth)` BEFORE creating session
   - Calls `child_session_id = await ctx.create_child_session(agent_name=agent_or_team, agent_type="native")`
   - Uses `parent_session_id = ctx.node.session_id or ""` (empty string fallback, not identifier.ascending)
   - Emits exactly one `SpawnSessionStart` with `depth=child_depth` (computed as `current_depth + 1`)
   - Passes `depth=child_depth` to both `node.run_stream()` (sync and async modes)
4. **`_stream_task()` function**:
   - Removed `SpawnSessionStart` emission entirely (was duplicate)
   - Removed `identifier.ascending("session")` fallbacks for `_child_session_id`/`_parent_session_id`
   - `child_session_id` and `parent_session_id` are now required `str` params (not `str | None`)
   - Removed `prompt` parameter (was only used for SpawnSessionStart metadata)
   - Updated docstring to clarify caller must emit SpawnSessionStart

### Key Design Decisions
- `parent_session_id` defaults to `ctx.node.session_id or ""` (empty string) — the SpawnSessionStart event requires a non-None parent; `create_child_session()` already handles the None case internally by falling back to `generate_session_id()`
- Depth guard fires BEFORE `create_child_session()` — prevents creating orphaned sessions when depth is exceeded
- `agent_type="native"` passed to `create_child_session()` — appropriate for SubagentTools which delegates to native agents/teams

### Tests Added (8 tests in `test_subagent_child_session.py`)
- `test_single_spawn_session_start_per_delegation` — integration: exactly 1 SpawnSessionStart per delegation
- `test_run_started_session_id_matches_spawn_child_id` — RunStartedEvent.session_id matches SpawnSessionStart.child_session_id
- `test_child_session_data_persists_with_parent_id` — child SessionData persisted with correct parent_id
- `test_delegation_depth_error_at_max_depth` — DelegationDepthError raised at MAX_DELEGATION_DEPTH
- `test_stream_task_does_not_emit_spawn_session_start` — _stream_task() never emits SpawnSessionStart
- `test_depth_guard_before_session_creation` — depth guard prevents create_child_session call
- `test_task_uses_run_ctx_depth` — SpawnSessionStart.depth=1 for first delegation from depth=0
- `test_subagent_tools_does_not_import_identifiers` — module doesn't have `identifier` in namespace

### Gotcha: Agent session_id timing
- Agent doesn't have `session_id` until after first `run_stream()` call
- Tests that need the parent session_id should read it AFTER the run, not before

## 2026-04-24 T11: Team.run_stream() Session/Depth Adaptation

### Changes Made
- Added `depth: int = 0` parameter to `Team.run_stream()` signature
- Popped `session_id`, `depth`, and `parent_session_id` from kwargs before forwarding to members
  - `session_id` kwarg is captured and used as `parent_sid` (the caller's session = parent for children)
  - `depth` kwarg is discarded (explicit `depth` parameter is source of truth)
  - `parent_session_id` is popped to avoid duplicate keyword when we explicitly pass it to members
- Child session creation: `pool.sessions.create_child_session()` when pool available; `generate_session_id()` fallback
- `SpawnSessionStart` emitted per member BEFORE member events begin
- Nested `SubAgentEvent` preserves `child_session_id` and `parent_session_id` from inner teams
- `DelegationDepthError` raised when `child_depth > MAX_DELEGATION_DEPTH`
- No intermediate Team session — hierarchy is flat (member sessions are children of the CALLER's session)

### Key Design Decision: parent_sid Resolution
- `parent_sid = session_id_kwarg or self.session_id`
- The popped `session_id` kwarg represents the CALLER's session, which becomes the parent for children
- `self.session_id` is the fallback when no session_id is passed in kwargs

### SupportsRunStream Check
- Added `isinstance(node, SupportsRunStream)` guard before calling `node.run_stream()`
- If node doesn't support streaming, `SpawnSessionStart` is still emitted but stream ends there
- Previously, calling `run_stream` on non-streaming nodes would cause AttributeError

### Removed Import
- `TeamRun` import removed from team.py (was unused after T3 refactored inline match/case to `get_source_type()`)

### Test File
- `tests/teams/test_team_run_stream_session.py` — 12 tests covering:
  - Signature: depth param with default 0
  - Depth guard: DelegationDepthError at MAX_DELEGATION_DEPTH
  - Depth at limit: no error at MAX-1
  - SpawnSessionStart emission per member
  - SpawnSessionStart precedes SubAgentEvent per member
  - SubAgentEvent preserves child/parent session IDs
  - SpawnSessionStart carries session IDs
  - Out-of-pool Team: generates session IDs without persistence
  - Pool-backed Team: calls create_child_session()
  - Kwargs popping: no duplicate keyword errors
  - Team.run() unchanged
  - Nested SubAgentEvent session IDs preserved

## 2026-04-24 T10: WorkersTools Child Sessions and Depth Propagation

### Changes Made to `workers.py`
1. **Removed `identifier` import** — `from agentpool.utils import identifiers as identifier` no longer needed
2. **Added imports**: `DelegationDepthError`, `MAX_DELEGATION_DEPTH` from `agentpool.agents.exceptions`
3. **`_create_agent_tool()` method**:
   - Computes `current_depth = ctx.run_ctx.depth if ctx.run_ctx is not None else 0`
   - Guards `if current_depth >= MAX_DELEGATION_DEPTH: raise DelegationDepthError(current_depth)` before session creation
   - Calls `child_session_id = await ctx.create_child_session(agent_name=agent_name, agent_type="native", parent_session_id=parent_session_id)`
   - Uses `generate_session_id()` for parent_session_id fallback (instead of `identifier.ascending("session")`)
   - All `depth=1` replaced with `depth=child_depth` (computed as `current_depth + 1`)
   - Passes `depth=child_depth` to `worker.run_stream()`
4. **`_create_node_tool()` method**:
   - Same depth computation and guard as `_create_agent_tool()`
   - Calls `child_session_id = await ctx.create_child_session(agent_name=node_name, agent_type=worker.agent_type, parent_session_id=parent_session_id)`
   - Uses `worker.agent_type` from MessageNode for persistence-domain type string
   - All `depth=1` replaced with `depth=child_depth`
   - Passes `depth=child_depth` to `worker.run_stream()`
5. **Preserved**: pass_message_history behavior, reset_history_on_run behavior, conversation history management, try/finally history restore

### Bug Fix: Team/TeamRun parent_session_id kwargs conflict
- When workers.py passes `parent_session_id` in kwargs to `worker.run_stream()`, and the worker is a Team or TeamRun, the `**kwargs` forwarding caused `TypeError: got multiple values for keyword argument 'parent_session_id'`
- **Team.py fix**: Already popped `parent_session_id` from kwargs (T11), but was discarding it. Changed to capture as `parent_session_id_kwarg` and use it in `parent_sid` resolution with priority: `parent_session_id_kwarg or session_id_kwarg or self.session_id`
- **TeamRun.py fix**: Was popping `session_id` into `parent_session_id` variable (confusing). Changed to pop `session_id`, `depth`, AND `parent_session_id` from kwargs. Resolution: `parent_session_id_kwarg or session_id_kwarg or self.session_id`

### Tests Added (4 new tests in `test_workers.py`)
- `test_worker_spawn_depth_equals_parent_depth_plus_one` — SpawnSessionStart.depth=1 when parent at depth=0
- `test_worker_child_session_has_correct_parent` — child_session_id != parent_session_id, both start with "ses_"
- `test_delegation_depth_error_at_max_depth` — DelegationDepthError raised when depth=MAX_DELEGATION_DEPTH
- `test_subagent_event_depth_propagation` — SubAgentEvent.depth matches SpawnSessionStart.depth

### Pre-existing test failures (NOT caused by our changes)
- `test_structured_worker_output` — requires real model (gpt-5-nano unavailable)
- `test_history_sharing` — requires real model (gpt-5-nano unavailable)

## 2026-04-24 T14: Cross-Provider Event/Depth/Session Lifecycle Tests

### Test File
- `tests/delegation/test_cross_provider_session_lifecycle.py` — 18 tests, all passing

### Covered Test Goals
- TG-1: SubagentTools child session has correct parent_id in SessionData
- TG-3: Team member SpawnSessionStart precedes SubAgentEvent content
- TG-4: SubagentTools emits exactly one SpawnSessionStart per delegation
- TG-7: Team member child_session_id appears in SubAgentEvent
- TG-8: RunStartedEvent.session_id == SpawnSessionStart.child_session_id (subagent)
- TG-9: Depth increments by 1 per delegation level
- TG-10: ACP child session inherits parent project_id/cwd
- TG-14: SubagentTools depth guard raises DelegationDepthError before session creation
- TG-15: WorkersTools child session persisted with correct parent
- TG-16: TeamRun sequential members each get own child session
- TG-18: Nested Team → SubAgentEvent preserves inner child/parent session IDs
- TG-22: Mixed agent type Team (native + TeamRun members) all get child sessions

### Cross-Provider Invariants Verified
- Event ordering: SpawnSessionStart index < first SubAgentEvent index per child_session_id
- Non-streaming: Team.run() and TeamRun.run() do NOT emit SpawnSessionStart
- SpawnSessionStart.depth == SubAgentEvent.depth for same child delegation
- Pool-backed Team and TeamRun both call create_child_session()
- All child_session_ids are unique across providers

### Key Learnings
- Mixed agent type teams (TG-22): Real ACP agents can't be tested in unit tests due to client requirements. Tested with Agent + TeamRun combination instead, which covers the source_type differentiation (agent vs team_sequential).
- Cross-provider child_session_id uniqueness: When SubagentTools delegates to a Team, the SubagentTools child and Team member children all get unique session IDs — this is automatically guaranteed by generate_session_id() / create_child_session() using UUID-based IDs.
- ACP session manager tests require mocking ACPSession, ClientCapabilities, and pool.storage.generate_session_id — well-established pattern from T13 tests.
- Workers tools tests require setting TestModel on both main and worker agents explicitly via set_model().

## 2026-04-24 T15: Legacy Provider Session/Depth Pattern Removal Verification

### Verification Results — ALL CLEAN, no removals needed

1. **`identifier.ascending("session")`** — Only found in `session_routes.py` (2 occurrences, lines 591/892) — TOP-LEVEL session creation, explicitly out of scope ✅
2. **`getattr(ctx, "current_depth", 0)`** — Not found anywhere in src/. T9 already replaced with `ctx.run_ctx.depth if ctx.run_ctx is not None else 0` ✅
3. **`depth=1` hardcoded** — Only found in `pool.py` line 248 (CLI depth, out of scope) and `file_routes.py` (filesystem maxdepth, unrelated) ✅
4. **`getattr(ctx` in delegation/toolsets** — Only `getattr(ctx.pool, "skill_resolver", None)` in skills.py (unrelated to depth) ✅
5. **`identifier` import** — Already removed from subagent_tools.py (T9) and workers.py (T10) ✅

### Test Results
- `tests/toolsets/test_subagent_child_session.py` — 8/8 passed ✅
- `tests/tools/test_workers.py` — 21/23 passed (2 failures: model HTTP 500 for gpt-5-nano, pre-existing) ✅
- `tests/teams/` — 11/11 passed ✅
- `tests/servers/acp_server/` — 65/66 passed (1 pre-existing snapshot failure) ✅

### Conclusion
All legacy patterns were already cleaned up in T9-T13. T15 is a verification-only task — no code changes required.

## 2026-04-24 T16: Broad Validation and Regression Fix

### Regressions Found and Fixed (2 test failures in RFC scope)

1. **test_task_tool_return_format** — `MagicMock` comparison with `int`
   - Root cause: `ctx = MagicMock()` → `ctx.run_ctx.depth` returns `MagicMock`, not `int`
   - Our RFC change added `ctx.run_ctx.depth if ctx.run_ctx is not None else 0` comparison
   - Fix: Added `ctx.run_ctx.depth = 0` to test mock setup
   - Also needed: `ctx.create_child_session = AsyncMock(return_value="child_session_123")` — our RFC added this call

2. **test_task_tool_async_mode_return_format** — Same MagicMock issue
   - Same fix: `ctx.run_ctx.depth = 0` and `ctx.create_child_session = AsyncMock(return_value="child_session_123")`

### Formatting Fixes
- `src/agentpool/delegation/team.py` — ruff format fixed multi-line expression formatting
- `src/agentpool/delegation/team.py` — ruff check --fix for import sorting (I001)
- `src/agentpool_toolsets/builtin/subagent_tools.py` — ruff check --fix for import sorting (I001)
- `src/agentpool_toolsets/builtin/workers.py` — ruff check --fix for import sorting (I001)

### MyPy Results
- Only pre-existing error: `workers.py:89` — BaseTeam assignment to BaseAgent variable
- No new errors from RFC changes

### Pre-existing Failures Confirmed (NOT RFC regressions)
- `test_history_sharing` — requires real model (gpt-5-nano 500 errors)
- `test_structured_worker_output` — requires real model (gpt-5-nano 500 errors)
- `test_execute_command_simple` — ACP snapshot mismatch
- `test_pool_skills` (4 tests) — provider naming change
- `test_claude_code_*` (4 tests) — ClaudeCodeAgentConfig missing attribute
- `test_async_io_operations` (3 tests) — ClaudeCodeHookManager signature change
- `test_group_stats_aggregation` — transient (passes in isolation, state leak in batch)
- PLR0915 (too many statements) — 80+ pre-existing occurrences across codebase

### Key Learning: Mock Context Depth Pattern
When using `MagicMock()` for `AgentContext` in tests, any new attribute access introduced by RFC changes will return `MagicMock` objects instead of expected types. Always explicitly set:
- `ctx.run_ctx.depth = <int>` (not None, not left as MagicMock)
- `ctx.create_child_session = AsyncMock(return_value=...)` (not left as sync MagicMock)
- `ctx.node.session_id = <str>` (not left as MagicMock)

This is the standard pattern for all delegation provider tests going forward.
