## 1. Schema Changes

- [ ] 1.1 Add `turn_complete: bool | None = False` field to `ClientCapabilities` in `src/acp/schema/capabilities.py`
- [ ] 1.2 Update `ClientCapabilities.create()` factory method to accept `turn_complete` parameter
- [ ] 1.3 Verify `ClientCapabilities` serialization/deserialization handles the new field correctly

## 2. Capability Negotiation

- [ ] 2.1 Update `AgentPoolACPAgent.initialize()` to only advertise `turn_complete=True` when `client_capabilities.turn_complete` is truthy
- [ ] 2.2 Ensure `AgentPoolACPAgent.initialize()` stores `client_capabilities` for later use (if not already stored)

## 3. ACPEventConverter Changes

- [ ] 3.1 Add `client_supports_turn_complete: bool = False` parameter to `ACPEventConverter.__init__` in `src/agentpool_server/acp_server/event_converter.py`
- [ ] 3.2 Update `StreamCompleteEvent` branch in `convert()` to only yield `TurnCompleteUpdate` when `self.client_supports_turn_complete` is True
- [ ] 3.3 Update `reset()` to preserve the `client_supports_turn_complete` flag across resets

## 4. Legacy Session Path Updates

- [ ] 4.1 Update `ACPSession.process_prompt()` in `src/agentpool_server/acp_server/session.py` to pass `client_supports_turn_complete` when creating `ACPEventConverter`
- [ ] 4.2 Derive the flag from `self.client_capabilities.turn_complete`

## 5. SessionPool Path Updates

- [ ] 5.1 Update `ACPProtocolHandler.__init__` to store `client_capabilities` (or derive a boolean flag)
- [ ] 5.2 Update `ACPProtocolHandler.handle_prompt()` to block `PromptResponse` until run completion when client does NOT support `turn_complete`
- [ ] 5.3 Use `asyncio.wait_for()` with a timeout (e.g., 60s) when awaiting run completion to prevent deadlocks
- [ ] 5.4 Update `ACPProtocolHandler._event_consumer_loop()` to pass `client_supports_turn_complete` when creating per-session `ACPEventConverter`

## 6. Testing

- [ ] 6.1 Add test for `ClientCapabilities` with `turn_complete=True` and `turn_complete=False`
- [ ] 6.2 Add test for `AgentPoolACPAgent.initialize()` advertising `turn_complete` only when client supports it
- [ ] 6.3 Add test for `ACPEventConverter` emitting `TurnCompleteUpdate` only when flag is True
- [ ] 6.4 Add test for `ACPProtocolHandler.handle_prompt()` blocking behavior for legacy clients
- [ ] 6.5 Add test for `ACPProtocolHandler.handle_prompt()` non-blocking behavior for modern clients
- [ ] 6.6 Run existing ACP server tests to ensure no regressions

## 7. Documentation & Cleanup

- [ ] 7.1 Update any inline comments or docstrings referencing unconditional `turn_complete` behavior
- [ ] 7.2 Verify all modified files pass `ruff check` and type checking
