## 1. Core SessionPool & SessionController Changes

- [x] 1.1 Add `parent_session_id` and `lifecycle_policy` fields to `SessionState`
- [x] 1.2 Add `SessionLifecyclePolicy` enum (`independent`, `cascade`, `bound`) with `cascade` as default
- [x] 1.3 Add parent-child index (`_children: dict[str, list[str]]`) to `SessionController`
- [x] 1.4 Update `SessionController._get_or_create_session_locked()` to accept and store `parent_session_id` and `lifecycle_policy`
- [x] 1.5 Add `SessionController.get_children(session_id)` and `SessionController.get_parent(session_id)` methods
- [x] 1.6 Update `SessionController.close_session()` to respect `lifecycle_policy` (cascade, bound, independent)
- [x] 1.7 Update `SessionController._cleanup_expired_sessions()` to respect lifecycle policies
- [x] 1.8 Update `SessionPool.create_session()` signature to accept `parent_session_id` and `lifecycle_policy`

## 2. EventBus Scoped Subscriptions

- [x] 2.1 Update `EventBus.subscribe()` signature to accept `scope: str = "session"`
- [x] 2.2 Add subscriber metadata tracking (queue -> scope mapping) in `EventBus`
- [x] 2.3 Update `EventBus.publish()` to look up session tree and route to matching subscribers based on scope
- [x] 2.4 Implement `scope="session"` behavior (exact match, backward compatible)
- [x] 2.5 Implement `scope="descendants"` behavior (self + all descendants)
- [x] 2.6 Implement `scope="subtree"` behavior (self + parent + siblings + children)
- [x] 2.7 Add `EventBus` unit tests for all three scopes with multi-level session trees

## 3. BaseAgent Session ID Decoupling

- [x] 3.1 Remove `session_id` generation from `BaseAgent.run_stream()`
- [x] 3.2 Update `BaseAgent.run_stream()` to accept `session_id` as required when pool is available
- [x] 3.3 Add ephemeral session ID fallback for standalone `BaseAgent` usage (no pool)
- [x] 3.4 Update `_run_stream_once()` to not regenerate or override session_id
- [x] 3.5 Update `BaseAgent` tests to provide explicit session_id or use standalone mode
- [x] 3.6 Ensure `self.session_id` is still set correctly for backward compatibility

## 4. AgentContext Integration

- [x] 4.1 Update `AgentContext.create_child_session()` to call `pool.session_pool.create_session(parent_session_id=...)` instead of `pool.sessions.create_child_session()`
- [x] 4.2 Ensure `SessionPool.create_session()` internally calls `SessionManager.create_child_session()` for persistence
- [x] 4.3 Update `AgentContext` tests for the new child session creation path
- [x] 4.4 Handle edge case where `pool.session_pool` is None (fallback to ephemeral ID)

## 5. StreamEventEmitter Event Routing

- [x] 5.1 Update `StreamEventEmitter._emit()` to publish to `ctx.pool.session_pool.event_bus` when pool is available
- [x] 5.2 Maintain fallback to `run_ctx.event_queue` when no SessionPool
- [x] 5.3 Remove dependency on `run_ctx.event_bus` (deprecate but don't break)
- [x] 5.4 Add tests verifying background task events reach SessionPool EventBus after turn completion

## 6. Protocol Handler Updates

- [x] 6.1 Update `ACPProtocolHandler._event_consumer_loop()` to subscribe with `scope="descendants"`
- [x] 6.2 Verify ACP client receives child session events after `end_turn`
- [x] 6.3 Update OpenCode server event subscription to use appropriate scope (if applicable)
- [x] 6.4 Add integration test: ACP prompt -> spawn subagent -> subagent events reach client after end_turn

## 7. TurnRunner Integration

- [x] 7.1 Update `TurnRunner._run_turn_unlocked()` to get session_id from SessionPool instead of generating
- [x] 7.2 Ensure `run_ctx` event_queue consumer still works (publishes to EventBus)
- [x] 7.3 Remove `run_ctx.event_bus = self.event_bus` injection (no longer needed)
- [x] 7.4 Update TurnRunner tests for unified session creation

## 8. Testing & Verification

- [x] 8.1 Add test: `test_session_pool_creates_child_session_with_parent_tracking`
- [x] 8.2 Add test: `test_event_bus_descendants_scope_receives_child_events`
- [x] 8.3 Add test: `test_event_bus_subtree_scope_receives_sibling_events`
- [x] 8.4 Add test: `test_lifecycle_policy_cascade_closes_children`
- [x] 8.5 Add test: `test_lifecycle_policy_independent_preserves_children`
- [x] 8.6 Add test: `test_lifecycle_policy_bound_closes_child_immediately`
- [x] 8.7 Add test: `test_baseagent_standalone_generates_ephemeral_session`
- [x] 8.8 Add test: `test_background_task_events_reach_acp_client_after_end_turn`
- [x] 8.9 Run full test suite and fix regressions
- [x] 8.10 Run type checking (mypy) and lint (ruff)
