## Why

Title generation in AgentPool is already fully asynchronous at its core (`_generate_title_core` runs an `Agent` with structured output), but it still exposes a **synchronous** per-call callback (`on_title_generated: Callable[[str], None]`). This callback is passed through `MessageNode.log_session()` → `StorageManager.log_session()` → `StorageManager._generate_title_from_prompt()`, forcing consumers to either block or use workarounds like `loop.create_task()` inside the callback.

Meanwhile, the codebase already has a clean **async signal path** (`metadata_generated.emit()`) that OpenCode and ACP servers use successfully. The sync callback is redundant, adds complexity, and prevents callers from cleanly awaiting async side effects (e.g., persisting to storage, broadcasting SSE). Removing it simplifies the API and makes title generation truly async end-to-end.

## What Changes

- **Remove `session_title_setter` parameter from `MessageNode.log_session()`**
  - The sync callback `Callable[[str], None]` is replaced by the existing async `metadata_generated` Signal.
- **Remove `on_title_generated` parameter from `StorageManager.log_session()` and `_generate_title_from_prompt()`**
  - These methods no longer accept or invoke a synchronous callback.
- **Update OpenCode server message routes**
  - Remove `_update_session_title()` sync callback wrapper.
  - The existing `on_title_generated` signal subscriber already handles title persistence and SSE broadcasting.
- **Update ACP server signal subscriber**
  - Already uses `metadata_generated` Signal; confirm no callback usage remains.
- **Update tests**
  - Remove or update tests that verify the sync callback path.
  - Add tests verifying `metadata_generated` Signal emission on title generation.

## Capabilities

### New Capabilities
<!-- No new capabilities introduced - this is an internal API cleanup -->

### Modified Capabilities
<!-- No existing spec-level requirement changes - this is implementation refactoring -->

## Impact

- **`src/agentpool/messaging/messagenode.py`**: Remove `session_title_setter` parameter from `log_session()`.
- **`src/agentpool/storage/manager.py`**: Remove `on_title_generated` from `log_session()` and `_generate_title_from_prompt()`.
- **`src/agentpool_server/opencode_server/routes/message_routes.py`**: Remove `_update_session_title()` and update `_maybe_generate_title()` to rely solely on the signal path.
- **`src/agentpool/agents/base_agent.py`**: Confirm `log_session()` call no longer passes a callback (already doesn't).
- **Tests**: Update `test_title_generation.py`, `test_title_generation_nonblocking.py`, `test_session_title_fixes.py`.
- **Backward compatibility**: This is a **BREAKING** change for any external code passing `session_title_setter` or `on_title_generated`. Internal consumers (OpenCode, ACP) already use the signal path and are unaffected.
