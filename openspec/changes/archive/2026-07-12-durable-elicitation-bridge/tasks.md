# Tasks

## Implementation

- [x] 1. Core Data Models — `PendingDeferredCall` extended with `deferred_kind="elicitation"` + 4 optional fields, `ElicitationResumePayload`, `ElicitationDeferredEvent`
- [x] 2. Strategy Abstraction — `ElicitationResolutionStrategy` protocol, `CheckpointResolutionStrategy`, `ProtocolResolutionStrategy`
- [x] 3. InputProvider Interface — `supports_durable_elicitation` property on base + ACP + OpenCode providers
- [x] 4. Two-Level Interception — sentinel in `handle_elicitation()` + `CallDeferred` in `MCPClient.call_tool()`
- [x] 5. ElicitationDeferredBridge Capability — factory function, handler, wire into capability chain
- [x] 6. ElicitationFutureRegistry — per-session future management
- [x] 7. Resume Path — in-process + crash recovery, session close cleanup
- [x] 8. Event System — emit `ElicitationDeferredEvent`, ACP + OpenCode converters
- [x] 9. Tests — 29 tests (19 unit + 10 integration)
- [x] 10. Documentation — durable elicitation flow + MRTR integration path

## Design Correction

- [x] 11. Fix `handle_elicitation()` to raise `CallDeferred` directly (remove side-channel + sentinel pattern)
- [x] 12. Fix MCP `elicitation_handler` to catch `CallDeferred` and convert to side-channel (FastMCP workaround isolation)
- [x] 13. Update unit tests for corrected behavior
- [ ] 14. Update PR description with corrected design notes
