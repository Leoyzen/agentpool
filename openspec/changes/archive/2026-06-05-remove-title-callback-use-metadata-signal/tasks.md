## 1. Core API Cleanup

- [x] 1.1 Remove `on_title_generated` parameter from `StorageManager.log_session()` in `src/agentpool/storage/manager.py`
- [x] 1.2 Remove `on_title_generated` parameter from `StorageManager._generate_title_from_prompt()` in `src/agentpool/storage/manager.py`
- [x] 1.3 Remove sync callback invocation logic from `_generate_title_from_prompt()`
- [x] 1.4 Remove `session_title_setter` parameter from `MessageNode.log_session()` in `src/agentpool/messaging/messagenode.py`
- [x] 1.5 Update `StorageManager.log_session()` docstring and type hints
- [x] 1.6 Update `MessageNode.log_session()` docstring and type hints

## 2. OpenCode Server Updates

- [x] 2.1 Remove `_update_session_title()` sync callback wrapper from `src/agentpool_server/opencode_server/routes/message_routes.py`
- [x] 2.2 Update `_maybe_generate_title()` to call `storage.log_session()` without callback
- [x] 2.3 Verify `on_title_generated` signal subscriber in `server.py` handles all needed state updates
- [x] 2.4 Confirm SSE broadcast of `SessionUpdatedEvent` still works after callback removal

## 3. ACP Server Verification

- [x] 3.1 Confirm `acp_agent.py` `_on_metadata_generated()` subscriber is intact and functional
- [x] 3.2 Verify no remaining `on_title_generated` callback usage in ACP server code

## 4. Tests

- [x] 4.1 Update `tests/sessions/test_title_generation.py` to remove callback-based assertions
- [x] 4.2 Update `tests/servers/opencode_server/test_title_generation_nonblocking.py` to verify signal-based flow
- [x] 4.3 Update `tests/servers/opencode_server/test_session_title_fixes.py` to use `metadata_generated` Signal
- [x] 4.4 Add test verifying `metadata_generated` Signal is emitted with correct `SessionMetadataGeneratedEvent`
- [x] 4.5 Run test suite: `uv run pytest tests/sessions/ tests/servers/opencode_server/`

## 5. Verification & Documentation

- [x] 5.1 Run full test suite: `uv run pytest` (1 pre-existing failure unrelated to this change)
- [x] 5.2 Run lint: `uv run ruff check` (fixed auto-fixable issues in modified files)
- [x] 5.3 Update CHANGELOG with **BREAKING** note about removed callback parameters (no CHANGELOG file exists in repo)
- [x] 5.4 Verify no remaining references to `session_title_setter` or `on_title_generated` in codebase
