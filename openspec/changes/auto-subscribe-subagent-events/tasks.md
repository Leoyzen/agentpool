## 1. Protocol Layer Auto-Subscription

- [ ] 1.1 Add `SpawnSessionStart` detection in OpenCode message routes — detect `SpawnSessionStart` in the SSE event stream
- [ ] 1.2 Implement `auto_subscribe_subagent_events()` helper — subscribe to EventBus for child_session_id with scope="session"
- [ ] 1.3 Implement event forwarding loop — wrap received events as `SubAgentEvent` and broadcast via SSE
- [ ] 1.4 Add subscription cleanup on `StreamCompleteEvent` or `RunErrorEvent` — unsubscribe from EventBus when child session ends
- [ ] 1.5 Handle nested subagents — if a SubAgentEvent contains another SpawnSessionStart, recursively subscribe

## 2. Business Layer Provider Simplification

### 2.1 BackgroundTaskProvider
- [ ] 2.1.1 Remove manual EventBus subscription from `_consume_events_to_fs()` — delete the `_consume_events_to_fs` coroutine
- [ ] 2.1.2 Simplify `_task_async()` — only launch `process_prompt` via SessionPool, remove dual-path logic
- [ ] 2.1.3 Ensure filesystem output is still written — keep `fs.pipe()` for final result persistence

### 2.2 DelegationProvider
- [ ] 2.2.1 Simplify event handling in DelegationProvider — remove manual SubAgentEvent wrapping when using SessionPool path
- [ ] 2.2.2 Ensure SpawnSessionStart is still emitted — protocol layer needs this to trigger auto-subscription

### 2.3 Backward Compatibility
- [ ] 2.3.1 Test Legacy path (non-SessionPool) still works with manual SubAgentEvent emission
- [ ] 2.3.2 Ensure mixed usage works — some Providers use SessionPool, others use Legacy

## 3. Testing & Verification

- [ ] 3.1 Test auto-subscription triggers on SpawnSessionStart — verify EventBus subscription is created
- [ ] 3.2 Test event forwarding reaches frontend — verify PartDeltaEvent, ToolCallStartEvent appear in SSE stream
- [ ] 3.3 Test subscription cleanup on completion — verify no memory leaks after StreamCompleteEvent
- [ ] 3.4 Test background task result is not empty — verify output.md contains actual content
- [ ] 3.5 Test agent card status sync — verify card changes from "running" to "completed" when task finishes
- [ ] 3.6 Test DelegationProvider events reach frontend — verify sync delegation shows subagent progress
- [ ] 3.7 Test nested subagents — verify subagent-of-subagent events are properly forwarded
- [ ] 3.8 Run full test suite — ensure no regressions in existing tests

## 4. Documentation & Cleanup

- [ ] 4.1 Update BackgroundTaskProvider docstring — document the new architecture
- [ ] 4.2 Update DelegationProvider docstring — document SessionPool vs Legacy path
- [ ] 4.3 Add architecture note to OpenCode server docs — explain auto-subscription mechanism
- [ ] 4.4 Remove deprecated manual event handling code — clean up commented-out legacy code
