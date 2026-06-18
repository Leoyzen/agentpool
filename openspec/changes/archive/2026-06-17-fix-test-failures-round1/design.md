## Context

The test suite has 348 failures across ~12 categories. A systematic analysis was done, and the user made the following decisions:

1. **Skill prefix tests**: Remove entirely — the `skill:` prefix requirement is no longer enforced
2. **Standalone/SessionPool tests**: Ignore — removing `_execute_direct()` fallback (commit `0ba08a7d0`) was an intentional architecture decision
3. **Remaining categories**: Fix — these are genuine regressions or missing dependency guards

## Goals / Non-Goals

**Goals:**
- Remove skill-prefix mismatch tests (~7 tests)
- Fix ProviderCurrentConfig `headers` attribute access (Category 2: ~6 tests)
- Fix FakeManifest missing `acp` attribute (Category 3: ~8 tests)
- Add `apprise` ModuleNotFoundError skip guard (Category 4: ~11 tests)
- Fix `get_effective_paths()` deprecated test assertions (Category 6: ~6 tests)
- Fix MCP `register_pending_request` attribute rename (Category 7: ~4 tests)
- Fix MCP provider `UPath` import path (Category 8: ~5 tests)
- Fix SSE / GlobalEvent envelope serialization (Category 9: ~15 tests)
- Fix RFC0011 lineage `parent_session_id` propagation (Category 10: ~2 tests)
- Fix EventProcessor `_child_contexts` attribute rename (Category 11: ~2 tests)
- Fix regex pattern mismatch for exception messages (Category 12: ~5 tests)
- Fix misc. dependency guards and benchmark flakiness (~5 tests)

**Non-Goals:**
- NOT fixing standalone/SessionPool tests (~120 tests) — intentional architecture change
- NOT adding new test coverage — only fixing existing broken tests

## Decisions

### Category 2 — ProviderCurrentConfig.headers
- **Decision**: Add `headers` back to `ProviderCurrentConfig` model, or use `getattr(current, 'headers', None)` in `provider_router.py:105`
- **Rationale**: `headers` is still referenced in the router code; the model change was incomplete

### Category 3 — FakeManifest.acp
- **Decision**: Add `acp: None` field to FakeManifest test mock
- **Rationale**: Minimal change to fix the attribute access

### Category 4 — apprise ModuleNotFoundError
- **Decision**: Add `pytest.mark.skipif` guard checking for `apprise` module
- **Rationale**: `apprise` is an optional dependency, tests should handle its absence gracefully

### Category 5 — Skill prefix (REMOVE)
- **Decision**: Remove the specific test assertions that check for `skill:` prefix
- **Rationale**: User explicitly stated behavior is no longer required

### Category 6 — get_effective_paths()
- **Decision**: Update test assertions to match new ConfigPath behavior, or remove deprecated method tests
- **Rationale**: Method is deprecated; tests still assert old behavior

### Category 7 — AcpMcpConnection.register_pending_request
- **Decision**: Find the new API method name and update test calls
- **Rationale**: Method was renamed during MCP connection refactor

### Category 8 — UPath import
- **Decision**: Update test imports to use correct source for `UPath`
- **Rationale**: Module no longer re-exports `UPath`

### Category 9 — SSE / GlobalEvent
- **Decision**: Fix `_serialize_event()` to always include envelope fields (directory, sessionId) even for non-session events like heartbeat
- **Rationale**: Server heartbeat events lack session context and get serialized without envelope fields

### Category 10 — parent_session_id propagation
- **Decision**: Ensure `parent_session_id` kwarg from `run_stream()` reaches `RunStartedEvent`
- **Rationale**: Parameter is accepted but not forwarded to the event

### Category 11 — _child_contexts rename
- **Decision**: Update EventProcessor references to use current attribute name
- **Rationale**: Internal refactoring renamed the attribute

### Category 12 — Regex pattern mismatches
- **Decision**: Update `pytest.raises(match=...)` patterns to match actual error messages
- **Rationale**: Error messages changed but test assertions were not updated

## Risks / Trade-offs

- **[Low] SSE serialization fix could affect production event format**: Need to verify heartbeat events render correctly in the TUI
- **[Low] parent_session_id fix might expose if field is not supported by all callers**: Short-term, set to None by default
- **[Low] Removing skill-prefix tests: if prefix logic is re-added later, these tests won't catch regressions**: Acceptable given user direction
