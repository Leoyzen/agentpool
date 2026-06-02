## Phase 1: Core Fix — Wire EventBus to SessionController

**Goal**: Fix the root cause (EventBus._session_tree never populated) with minimal changes.

- [ ] 1.1 Modify `EventBus.__init__` to accept optional `session_controller: SessionController | None = None` parameter
- [ ] 1.2 Modify `EventBus._is_descendant()` to query `self._session_controller.get_children(parent_id)` first, fallback to `self._session_tree`. **Must preserve recursive logic**:
  ```python
  def _is_descendant(self, child_id: str, parent_id: str) -> bool:
      if self._session_controller is not None:
          children = self._session_controller.get_children(parent_id)
      else:
          children = self._session_tree.get(parent_id, [])
      return child_id in children or any(
          self._is_descendant(child_id, child) for child in children
      )
  ```
- [ ] 1.3 Modify `EventBus._get_parent()` to query SessionController first, extracting the session_id string (NOT the SessionState object):
  ```python
  if self._session_controller is not None:
      parent_state = self._session_controller.get_parent(session_id)
      if parent_state is not None:
          return parent_state.session_id
  # fallback to internal _session_tree
  for parent_id, children in self._session_tree.items():
      if session_id in children:
          return parent_id
  return None
  ```
- [ ] 1.4 Modify `TurnRunner.__init__` to pass `session_controller` to `EventBus`: `self.event_bus = EventBus(session_controller=session_controller)`
- [ ] 1.5 Verify `TurnRunner` tests still pass: `uv run pytest tests/orchestrator/test_turn_runner.py -xvs`

## Phase 2: Red Flag Tests — Rewrite Assertions of Buggy Behavior

**Goal**: Tests that asserted the BUG must now assert the FIXED behavior.

- [ ] 2.1 Rewrite `test_publish_does_not_deliver_to_parent` → rename to `test_publish_delivers_descendant_events_to_parent`: Create `EventBus(session_controller=controller)`, subscribe parent with `scope="descendants"`, publish child event, **assert queue IS NOT empty** (was: assert empty)
- [ ] 2.2 Rewrite `test_acp_handler_scenario_broken` → rename to `test_acp_handler_delivers_child_events`: Pass `SessionController` to `EventBus`, publish 3 child events, **assert len(received) == 3** (was: assert 0)
- [ ] 2.3 Rewrite `test_subagent_streaming_events_not_routed` → rename to `test_subagent_streaming_events_routed_to_parent`: Use real `SessionPool` with `EventBus` wired to `SessionController`, create parent+child sessions, publish child event, **assert parent_queue is not empty** (was: assert empty)
- [ ] 2.4 Keep `test_is_descendant_always_false_for_empty_tree` as-is (tests fallback behavior when no controller)
- [ ] 2.5 Keep `test_should_receive_descendants_always_false` as-is (tests fallback behavior)
- [ ] 2.6 Run red flag tests: `uv run pytest tests/orchestrator/test_session_tree_redflag.py -xvs` — expect ALL to pass

## Phase 3: SessionStore Migration to SessionController + Add Property Alias + Compatibility Shims

**Goal**: Move persistence from SessionManager to SessionController while keeping store lifecycle in AgentPool. CRITICAL: Add temporary compatibility shims on `SessionPool` so existing `pool.sessions.create_child_session()` and `pool.sessions.store` calls continue to work during migration.

- [ ] 3.1 Modify `AgentPool.__init__` (pool.py:158-159): 
  - Remove `self.sessions = SessionManager(pool=self, store=session_store)`
  - Keep `self._session_store = session_store`
  - **ADD `AgentPool.sessions` property with setter for test compatibility**:
    ```python
    import warnings

    @property
    def sessions(self) -> SessionPool | None:
        if "sessions" in self.__dict__:
            return self.__dict__["sessions"]
        if self.session_pool is not None:
            warnings.warn(
                "AgentPool.sessions is deprecated, use session_pool",
                DeprecationWarning,
                stacklevel=2,
            )
        return self.session_pool

    @sessions.setter
    def sessions(self, value: SessionPool | None) -> None:
        self.__dict__["sessions"] = value
    ```
- [ ] 3.2 Modify `AgentPool.__aenter__` (pool.py:243): Change `await self.exit_stack.enter_async_context(self.sessions)` to:
  ```python
  if self._session_store is not None:
      await self.exit_stack.enter_async_context(self._session_store)
  ```
- [ ] 3.3 Modify `SessionController.__init__`: Add `store: SessionStore | None = None` parameter, store as `self.store = store`
- [ ] 3.4 Modify `SessionPool.__init__`: Accept `store: SessionStore | None = None`, pass to `SessionController(pool, store=store, cleanup_callback=...)`
- [ ] 3.5 Modify `AgentPool.__aenter__` (pool.py:257): Pass `store=self._session_store` to `SessionPool(...)` constructor
- [ ] 3.6 **In `SessionPool.create_session()`: BEFORE the `get_or_create_session()` call, load parent data and inject into metadata, and remove the legacy `self.pool.sessions.create_child_session()` call at `core.py:1177-1183`**:
  ```python
  # BEFORE get_or_create_session():
  if parent_session_id is not None and self.sessions.store is not None:
      parent_data = await self.sessions.store.load(parent_session_id)
      if parent_data is not None:
          metadata.setdefault("project_id", parent_data.project_id)
          metadata.setdefault("cwd", parent_data.cwd)
  
  # Then call get_or_create_session() with the enriched metadata:
  state = await self.sessions.get_or_create_session(
      session_id, agent_name=agent_name, parent_session_id=parent_session_id,
      lifecycle_policy=lifecycle_policy, **metadata
  )
  ```
  **Also remove** the legacy `self.pool.sessions.create_child_session()` call at `core.py:1177-1183` (replaced by the logic above).
- [ ] 3.7 **Add compatibility shim `SessionPool.create_child_session()`**:
  ```python
  async def create_child_session(
      self,
      parent_session_id: str,
      agent_name: str,
      agent_type: str = "native",
      child_session_id: str | None = None,
  ) -> str:
      """TEMPORARY shim for backward compatibility."""
      if child_session_id is None:
          from agentpool.utils.identifiers import generate_session_id
          child_session_id = generate_session_id()
      state = await self.create_session(
          session_id=child_session_id,
          parent_session_id=parent_session_id,
          agent_name=agent_name,
          agent_type=agent_type,
      )
      return state.session_id
  ```
- [ ] 3.8 **Add compatibility property `SessionPool.store` with setter for test compatibility**:
  ```python
  @property
  def store(self) -> SessionStore | None:
      if "store" in self.__dict__:
          return self.__dict__["store"]
      return self.sessions.store

  @store.setter
  def store(self, value: SessionStore | None) -> None:
      self.__dict__["store"] = value
  ```
- [ ] 3.9 In `SessionController._get_or_create_session_locked()`: After creating new `SessionState`, if `self.store` is not None, create `SessionData` from state and call `await self.store.save(session_data)`
- [ ] 3.9b **Add `SessionData` runtime import to `src/agentpool/orchestrator/core.py`**:
  ```python
  from agentpool.sessions import SessionData  # noqa: TC001
  ```
  This is needed because `_state_to_data()` instantiates `SessionData(...)` at runtime.
- [ ] 3.9c **Add `SessionStore` TYPE_CHECKING import to `src/agentpool/orchestrator/core.py`**:
  ```python
  if TYPE_CHECKING:
      from agentpool.sessions import SessionStore  # noqa: TC002
  ```
  This is needed for type hints on `SessionController.__init__` and `SessionPool.__init__` store parameters.
- [ ] 3.10 In `SessionController._close_session_unlocked()`: After closing session, if `self.store` is not None, call `await self.store.delete(session_id)`
- [ ] 3.11 Implement `SessionState` → `SessionData` conversion helper in `SessionController`:
  ```python
  def _state_to_data(self, state: SessionState) -> SessionData:
      from agentpool.utils.time_utils import get_now
      return SessionData(
          session_id=state.session_id,
          agent_name=state.agent_name,
          agent_type=state.metadata.get("agent_type", "native"),
          parent_id=state.parent_session_id,
          pool_id=self.pool.manifest.name if self.pool.manifest else None,
          project_id=state.metadata.get("project_id"),
          cwd=state.metadata.get("cwd"),
          metadata=state.metadata,  # Preserve all metadata for OpenCode server compatibility
          created_at=get_now(),
          last_active=get_now(),
      )
  ```
- [ ] 3.12 Run session tests: `uv run pytest tests/sessions/ -xvs`

## Phase 4: Update OpenCode Server (Critical — Must Complete Before Deleting SessionManager)

**Goal**: Migrate all `pool.sessions.store` references to `pool.session_pool.sessions.store`.

**Note**: `StorageManager` does NOT have `list_sessions(parent_id=...)`. Only `SessionStore` has this method. So we MUST keep accessing the store through `SessionController.store`.

- [ ] 4.1 Modify `src/agentpool_server/opencode_server/state.py:672-673`:
  ```python
  # OLD:
  if self.pool.sessions is not None and self.pool.sessions.store is not None:
      session_data = await self.pool.sessions.store.load(session_id)
  # NEW:
  if self.pool.session_pool is not None and self.pool.session_pool.sessions.store is not None:
      session_data = await self.pool.session_pool.sessions.store.load(session_id)
  ```
- [ ] 4.2 Modify `src/agentpool_server/opencode_server/state.py:763-766`:
  ```python
  # OLD:
  if self.pool.sessions.store:
      await self.pool.sessions.store.save(session_data)
  else:
      await self.pool.storage.save_session(session_data)
  # NEW:
  if self.pool.session_pool is not None and self.pool.session_pool.sessions.store is not None:
      await self.pool.session_pool.sessions.store.save(session_data)
  else:
      await self.pool.storage.save_session(session_data)
  ```
- [ ] 4.3 Modify `src/agentpool_server/opencode_server/routes/session_routes.py:611-612`:
  ```python
  # OLD: if state.pool.sessions.store: await state.pool.sessions.store.save(session_data)
  # NEW: if state.pool.session_pool and state.pool.session_pool.sessions.store:
  #          await state.pool.session_pool.sessions.store.save(session_data)
  ```
- [ ] 4.4 Modify `src/agentpool_server/opencode_server/routes/session_routes.py:729-740`:
  ```python
  # OLD: store = state.pool.sessions.store; child_ids = await store.list_sessions(parent_id=session_id)
  # NEW: store = state.pool.session_pool.sessions.store if state.pool.session_pool else None
  #      if store: child_ids = await store.list_sessions(parent_id=session_id)
  ```
- [ ] 4.5 Modify `src/agentpool_server/opencode_server/routes/session_routes.py:768-769`:
  ```python
  # OLD: if state.pool.sessions.store: await state.pool.sessions.store.save(session_data)
  # NEW: (same pattern as 4.3)
  ```
- [ ] 4.6 Modify `src/agentpool_server/opencode_server/routes/session_routes.py:798-799`:
  ```python
  # OLD: if state.pool.sessions.store: await state.pool.sessions.store.delete(session_id)
  # NEW: (same pattern)
  ```
- [ ] 4.7 Modify `src/agentpool_server/opencode_server/routes/session_routes.py:911-912`:
  ```python
  # OLD: if state.pool.sessions.store: await state.pool.sessions.store.save(session_data)
  # NEW: (same pattern)
  ```
- [ ] 4.8 Modify `src/agentpool_server/opencode_server/server.py:190-191`:
  ```python
  # OLD: if state.pool.sessions.store: await state.pool.sessions.store.save(session_data)
  # NEW: (same pattern)
  ```
- [ ] 4.9 Run OpenCode server tests: `uv run pytest tests/servers/opencode_server/ -xvs`

## Phase 5: Update ACP Server and Delegation Layer

- [ ] 5.1 Modify `src/agentpool_server/acp_server/session_manager.py:56-60` (session_store property):
  ```python
  # OLD:
  if self._pool.sessions is None:
      return None
  return self._pool.sessions.store
  # NEW:
  if self._pool.session_pool is None:
      return None
  return self._pool.session_pool.sessions.store
  ```
- [ ] 5.1b **CRITICAL**: Modify `src/agentpool_server/acp_server/session_manager.py:101-118` (create_session child path):
  ```python
  # OLD:
  if parent_session_id is not None and self._pool.sessions is not None:
      child_session_id = await self._pool.sessions.create_child_session(
          parent_session_id=parent_session_id,
          agent_name=agent.name,
          agent_type="acp",
      )
  # NEW:
  if parent_session_id is not None and self._pool.session_pool is not None:
      from agentpool.utils.identifiers import generate_session_id
      if session_id is None:
          session_id = generate_session_id()
      child_state = await self._pool.session_pool.create_session(
          session_id=session_id,
          parent_session_id=parent_session_id,
          agent_name=agent.name,
          agent_type="acp",
      )
      session_id = child_state.session_id
      # Load persisted child data to get inherited cwd
      data = (
          await self.session_store.load(session_id)
          if self.session_store
          else None
      )
      effective_cwd = data.cwd if data and data.cwd else cwd
      # Use effective_cwd in the ACPSession construction below:
      session = ACPSession(
          session_id=session_id,
          agent=session_agent,
          cwd=effective_cwd,
          client=client,
          mcp_servers=mcp_servers,
          acp_agent=acp_agent,
          client_capabilities=client_capabilities or ClientCapabilities(),
          client_info=client_info,
          manager=self,
          subagent_display_mode=subagent_display_mode,
      )
  ```
- [ ] 5.2 Modify `src/agentpool_server/acp_server/converters.py`: Check for any `pool.sessions` references (if none, skip)
- [ ] 5.3 Modify `src/agentpool/delegation/team.py:219`: Replace `pool.sessions.create_child_session()` with explicit session ID generation + `SessionPool.create_session()`:
  ```python
  # OLD:
  child_sid = await self.agent_pool.sessions.create_child_session(
      parent_session_id=pool_parent,
      agent_name=node.name,
      agent_type=node.agent_type,
  )
  # NEW:
  from agentpool.utils.identifiers import generate_session_id
  child_sid = generate_session_id()
  child_state = await self.agent_pool.session_pool.create_session(
      session_id=child_sid,
      parent_session_id=pool_parent,
      agent_name=node.name,
      agent_type=node.agent_type,
  )
  child_sid = child_state.session_id
  ```
- [ ] 5.4 Modify `src/agentpool/delegation/teamrun.py:310`: Same pattern as 5.3:
  ```python
  # OLD:
  child_sid = await pool.sessions.create_child_session(
      parent_session_id=parent_session_id,
      agent_name=node.name,
      agent_type=node.agent_type,
  )
  # NEW:
  from agentpool.utils.identifiers import generate_session_id
  child_sid = generate_session_id()
  child_state = await pool.session_pool.create_session(
      session_id=child_sid,
      parent_session_id=parent_session_id,
      agent_name=node.name,
      agent_type=node.agent_type,
  )
  child_sid = child_state.session_id
  ```
- [ ] 5.5 Verify `src/agentpool_toolsets/builtin/subagent_tools.py` uses `ctx.create_child_session()` (delegates through AgentContext, no direct changes needed if Phase 6.1 is correct)
- [ ] 5.6 Verify `src/agentpool_toolsets/builtin/workers.py` uses `ctx.create_child_session()` (delegates through AgentContext, no direct changes needed if Phase 6.1 is correct)

## Phase 6: Update AgentContext and SessionPool

- [ ] 6.1 Modify `src/agentpool/agents/context.py`: In `create_child_session()`, remove fallback to `pool.sessions.create_child_session()`. Only call `pool.session_pool.create_session()` when available. When `pool.session_pool` is None, generate ephemeral session ID.
- [ ] 6.2 ~~Modify `src/agentpool/orchestrator/core.py` `SessionPool.create_session()`: Remove the legacy call to `self.pool.sessions.create_child_session()`~~ (Already done in Phase 3.6 — no-op if legacy call was already removed)
- [ ] 6.3 Run agent tests: `uv run pytest tests/agents/ -xvs`

## Phase 7: Delete SessionManager + Remove Compatibility Shims

**Goal**: Clean up legacy SessionManager and remove temporary compatibility shims now that all callers have been migrated.

- [ ] 7.1 Delete `src/agentpool/sessions/manager.py`
- [ ] 7.2 Remove `SessionManager` from `src/agentpool/sessions/__init__.py`:
  - Remove `from agentpool.sessions.manager import SessionManager` import line
  - Remove `"SessionManager"` from the `__all__` list
- [ ] 7.3 Remove `from agentpool.sessions import SessionManager` from `src/agentpool/delegation/pool.py`
- [ ] 7.4 **Remove `SessionPool.create_child_session()` shim** (added in Phase 3.7) — all callers now use `create_session()` directly
- [ ] 7.5 **Remove `SessionPool.store` property shim** (added in Phase 3.8) — all callers now access `pool.session_pool.sessions.store` directly

## Phase 8: Comprehensive Audit

- [ ] 8.1 Global grep `pool\.sessions` in `src/` — verify all non-test references have been migrated
- [ ] 8.2 Global grep `SessionManager` in `src/` — verify no imports remain
- [ ] 8.3 Check `src/agentpool_server/agui_server/`, `src/agentpool_server/mcp_server/`, `src/agentpool_server/openai_api_server/` for `pool.sessions` references
- [ ] 8.4 Check all agent files (`base_agent.py`, `native_agent/agent.py`, `claude_code_agent.py`, `acp_agent.py`, `agui_agent.py`, `codex_agent.py`) for `pool.sessions` references

## Phase 9: Update Tests

- [ ] 9.1 Update `tests/sessions/test_session_manager.py`: Rename or delete; create `tests/sessions/test_session_controller_persistence.py` if needed. Also update or delete `tests/sessions/test_session_id_opaque.py` which has `SessionManager`-specific opaque-ID tests
- [ ] 9.2 Update `tests/sessions/test_session_hierarchy.py`: Adapt to new interfaces
- [ ] 9.3 Update `tests/agents/test_create_child_session.py`: Mock `pool.session_pool` instead of `pool.sessions`
- [ ] 9.4 Update `tests/delegation/test_pool_session_integration.py`: Adapt to `AgentPool.sessions` being a property alias
- [ ] 9.4b Update `tests/delegation/test_cross_provider_session_lifecycle.py`:
  - `test_lifecycle_across_storage_providers` (lines 97-99): Pass `enable_session_pool=True` to `AgentPool` (or mock `pool.session_pool.sessions.store`) so `pool.sessions` returns a real `SessionPool` instead of `None`
  - `test_session_lifecycle_with_inheritance` (lines 470-472): Same as above
  - `test_session_manager_store_integration` (lines 346-347): Remove manual `SessionManager` construction; use `AgentPool(enable_session_pool=True)` and set store via `pool.session_pool.sessions.store = store`
- [ ] 9.5 Update `tests/servers/acp_server/test_acp_session_manager_child_session.py`: Adapt store access path
- [ ] 9.6 Update `tests/servers/opencode_server/` tests that mock `pool.sessions.store`
- [ ] 9.6b Update `tests/teams/test_team_run_stream_session.py` and `tests/teams/test_team_run_stream_depth.py`: Replace mocks of `pool.sessions.create_child_session` with mocks of `pool.session_pool.create_session` returning `SessionState` objects, and update assertions
- [ ] 9.7 Update `tests/toolsets/test_subagent_child_session.py`: Pass `enable_session_pool=True` to `AgentPool` (or mock `pool.session_pool`) so `pool.sessions` returns a real `SessionPool`
- [ ] 9.8 Update `tests/orchestrator/test_session_tree_redflag.py` (already done in Phase 2)
- [ ] 9.9 Global grep `pool\.sessions` in `tests/` — update all remaining mocks

## Phase 10: Final Verification

- [ ] 10.1 Run `uv run pytest tests/orchestrator/ -xvs`
- [ ] 10.2 Run `uv run pytest tests/agents/ -xvs`
- [ ] 10.3 Run `uv run pytest tests/delegation/ -xvs`
- [ ] 10.4 Run `uv run pytest tests/servers/acp_server/ -xvs`
- [ ] 10.5 Run `uv run pytest tests/servers/opencode_server/ -xvs`
- [ ] 10.6 Run `uv run pytest tests/sessions/ -xvs`
- [ ] 10.7 Run `uv run pytest` (full suite)
- [ ] 10.8 Run `uv run ruff check src/`
- [ ] 10.9 Run `uv run mypy src/`
