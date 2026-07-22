## Implementation Tasks

### P4: Reset `_message_registered` on turn completion (zero risk, 1 line)

- [ ] P4.1: Add `_message_registered[session_id] = False` in `StreamCompleteEvent` handler in `opencode_event_bridge.py` (after `_finalize_assistant_time()` and `_persist_assistant_message()`)
- [ ] P4.2: Add `_message_registered[session_id] = False` in `RunFailedEvent` handler in `opencode_event_bridge.py`
- [ ] P4.3: Write unit test `test_message_registered_resets_after_stream_complete` — verify flag is `False` after `StreamCompleteEvent`
- [ ] P4.4: Write unit test `test_message_registered_resets_after_run_failed` — verify flag is `False` after `RunFailedEvent`
- [ ] P4.5: Write unit test `test_no_finalize_incomplete_turn_warning_on_turn2` — verify D1 block not entered on turn 2
- [ ] P4.6: Run existing tests to verify no regressions

### P1: Unconditionally emit `PartUpdatedEvent` for protocol-sourced user messages (low risk, pending P1.0)

- [ ] P1.0: **Prerequisite** — Verify TUI `replayedParts` deduplication key. Check whether part IDs from `_deserialize_part()` (meta-reconstructed) match part IDs from `sync.session.sync()` (DB-loaded). If part IDs differ, fix `_deserialize_part()` to preserve original part IDs OR document why `replayedParts` deduplicates by a different key. Document findings.
- [ ] P1.1: Remove `source != "protocol"` guard in `event_processor.py`'s `_process_user_message_inserted()` — always yield `PartUpdatedEvent` for each part (ONLY after P1.0 passes)
- [ ] P1.2: Write unit test `test_protocol_message_emits_part_updated_event` — verify `PartUpdatedEvent` is yielded for `source="protocol"` messages
- [ ] P1.3: Write unit test `test_non_protocol_message_emits_part_updated_event` — verify existing behavior unchanged for non-protocol messages
- [ ] P1.4: Write integration test `test_user_message_parts_display_after_sse_reconnect` — verify TUI receives parts via SSE after reconnection
- [ ] P1.5: Run existing tests to verify no regressions

### P3: Activate recovery path with `set_session_context_data()` (medium risk, same-process only)

- [ ] P3.0: Document that P3 fixes elicitation resume (same-process) only. Cross-process crash recovery requires persisting `EventProcessorContext` to the session store — track as follow-up.
- [ ] P3.1: In `opencode_event_bridge.py`, after `StreamCompleteEvent` handler, call `set_session_context_data(session_id, ctx.serialize())` to persist `EventProcessorContext`
- [ ] P3.2: In `opencode_event_bridge.py`, after `RunFailedEvent` handler, call `set_session_context_data(session_id, ctx.serialize())`
- [ ] P3.3: Write unit test `test_before_consumer_loop_restores_from_persisted_context` — call `set_session_context_data()` with serialized context, then call `_before_consumer_loop()`, assert context is deserialized and `_message_registered = True`
- [ ] P3.4: Add error handling: if `ctx.serialize()` raises, log error and continue (do not crash the turn)
- [ ] P3.5: Write unit test `test_context_serialized_after_stream_complete` — verify `set_session_context_data` is called with serialized context
- [ ] P3.6: Write unit test `test_context_restored_on_resume` — verify `_before_consumer_loop()` restores context from `get_session_context_data()`
- [ ] P3.7: Write unit test `test_before_consumer_loop_resume_path_is_not_dead_code` — verify resume path executes when `set_session_context_data` was called
- [ ] P3.8: Write unit test `test_serialization_failure_falls_back_to_fresh_context` — verify graceful fallback on serialization error
- [ ] P3.9: Write unit test `test_context_serialized_after_run_failed_includes_error_state` — verify serialized context after `RunFailedEvent` captures error state
- [ ] P3.10: Write integration test `test_checkpoint_resume_restores_event_processor_context` — verify full checkpoint→resume flow restores context (same-process only)
- [ ] P3.11: Run existing tests to verify no regressions

### P2: Route `UserMessageInsertedEvent` through `ProtocolChannel` (medium risk)

- [ ] P2.1: In `SessionControllerRunsMixin._emit_user_message_inserted()` in `session_controller_runs.py`, check if `ProtocolChannel` is available (active run) AND `source == "protocol"`. Access path: `session = self.get_session(session_id)`; if `session.current_run_id` is not `None`, get run handle from `self._runs[session.current_run_id]`, check `isinstance(run_handle._comm_channel, ProtocolChannel)`. If both conditions met, publish through `run_handle._comm_channel.publish(event)`. If no (idle session, no ProtocolChannel, or source is not "protocol"), publish directly to `EventBus.publish()` (existing behavior). Note: Only `SessionControllerRunsMixin._emit_user_message_inserted()` is modified — NOT `RunHandle._emit_user_message_inserted()` in `run.py`.
- [ ] P2.2: In `comm_channel.py`'s `ProtocolChannel.publish()`, add deduplication guard: when `_replaying=True` and event is `UserMessageInsertedEvent`, skip EventBus publish and log a warning (for diagnosability of crash-before-delivery edge case). **Note**: Importing `UserMessageInsertedEvent` in `comm_channel.py` may cause a circular import — use duck-typing (e.g., `type(event).__name__ == "UserMessageInsertedEvent"`) or restructure imports if needed. Also add the import of `UserMessageInsertedEvent` if a non-circular import path is available.
- [ ] P2.3: Write unit test `test_steer_message_published_through_protocol_channel` — verify steer messages go through `ProtocolChannel.publish()`
- [ ] P2.4: Write unit test `test_followup_message_published_through_protocol_channel` — verify followup messages go through `ProtocolChannel.publish()`
- [ ] P2.5: Write unit test `test_initial_rest_message_published_directly_to_event_bus` — verify initial REST messages still use direct `EventBus.publish()`
- [ ] P2.6: Write unit test `test_user_message_inserted_not_duplicated_during_replay` — verify deduplication guard prevents double-publish during replay
- [ ] P2.7: Write integration test `test_user_message_journaled_for_steer` — verify `UserMessageInsertedEvent` appears in Journal for steer messages
- [ ] P2.8: Run existing tests to verify no regressions

### E2E Tests

- [ ] E2E.1: Write E2E test `test_full_crash_recovery_with_event_replay` — verify user messages display after crash recovery with durable journal
- [ ] E2E.2: Write E2E test `test_durable_elicitation_resume_user_message_display` — verify user messages display after elicitation resume
- [ ] E2E.3: Write E2E test `test_multiturn_after_recovery_no_false_warning` — verify no "Finalizing incomplete turn" warning after recovery
- [ ] E2E.4: Write E2E test `test_protocol_message_no_duplicate_parts_after_reconnect` — verify TUI renders each part exactly once after SSE reconnection with `sync.session.sync()` (validates P1 deduplication claim)

### Verification

- [ ] V1: Run `uv run pytest -m unit` — all unit tests pass
- [ ] V2: Run `uv run pytest -m integration` — all integration tests pass
- [ ] V3: Run `uv run pytest -m "e2e and not slow"` — all smoke E2E tests pass
- [ ] V4: Run `uv run --no-group docs mypy src/` — no new type errors
- [ ] V5: Run `uv run ruff check src/` — no lint errors
- [ ] V6: Verify log file no longer shows "Finalizing incomplete turn" warning for normal multi-turn conversations
