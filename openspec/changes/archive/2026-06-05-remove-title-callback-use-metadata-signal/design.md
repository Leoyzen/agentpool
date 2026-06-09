## Context

Title generation in AgentPool currently has a dual notification mechanism:

1. **Async Signal path** (`metadata_generated`): Clean, async, used by OpenCode and ACP servers.
2. **Sync callback path** (`on_title_generated`): A `Callable[[str], None]` passed through `MessageNode.log_session()` → `StorageManager.log_session()` → `StorageManager._generate_title_from_prompt()`. This forces consumers to either block on I/O or use `loop.create_task()` workarounds.

The core title generation (`_generate_title_core()`) is already fully async (it runs an `Agent` with structured output and `await`s the result). The sync callback is the only synchronous residue in the flow.

Current call chain:
```
MessageNode.log_session(session_title_setter=cb)
  → StorageManager.log_session(on_title_generated=cb)
    → _generate_title_from_prompt(on_title_generated=cb)
      → cb(title)  # SYNC CALL inside async method
```

Both OpenCode server (`on_title_generated` signal subscriber in `server.py`) and ACP server (`_on_metadata_generated` in `acp_agent.py`) already listen to the `metadata_generated` Signal and do not depend on the sync callback.

## Goals / Non-Goals

**Goals:**
- Remove the synchronous `session_title_setter` / `on_title_generated` callback from all public APIs.
- Make title generation notification uniformly async via the existing `metadata_generated` Signal.
- Simplify `MessageNode.log_session()`, `StorageManager.log_session()`, and `_generate_title_from_prompt()` signatures.
- Update OpenCode server to stop passing a sync callback and rely solely on the signal subscriber.

**Non-Goals:**
- Changing how title generation works algorithmically (still uses `Agent` with `SessionMetadata` output).
- Modifying the `metadata_generated` Signal behavior (it already works correctly).
- Adding new title generation features (e.g., custom models, different prompts).
- Modifying Claude provider's fallback title derivation (separate non-LLM path).

## Decisions

### Decision 1: Remove callback entirely rather than widening to `Awaitable`

**Rationale**: Widening the callback type to `Callable[[str], Awaitable[None]] | Callable[[str], None]` would require every caller to handle both sync and async variants (checking `inspect.isawaitable()`). This adds complexity for minimal benefit. The `metadata_generated` Signal already provides a cleaner, more decoupled mechanism.

**Alternative considered**: Keep the callback but make it async. Rejected because it duplicates the signal's purpose and adds API surface area.

### Decision 2: Keep `metadata_generated` Signal as the single notification channel

**Rationale**: The signal is already typed, async, and has multiple subscribers. It naturally supports "one-to-many" notification without the caller knowing who receives the event.

### Decision 3: OpenCode server updates session state in the signal subscriber, not inline

**Rationale**: OpenCode server currently has a split path: the sync callback `_update_session_title()` updates in-memory state, while the signal subscriber `on_title_generated()` also updates state and broadcasts SSE. Removing the callback means all updates happen in the signal subscriber, consolidating the logic.

## Risks / Trade-offs

- **[Risk] Breaking change for external callers** → `MessageNode.log_session()` and `StorageManager.log_session()` lose a parameter. Mitigation: Mark as **BREAKING** in changelog. Internal usage is already migrated.
- **[Risk] Signal subscriber ordering** → If multiple subscribers run and one fails, `anyenv.signals.Signal` behavior must be verified (whether failures stop other subscribers). Mitigation: The signal implementation should handle exceptions per-subscriber.
- **[Risk] Race between title generation and session retrieval** → If a consumer calls `get_session_title()` before the signal fires, it may get `None`. This is **pre-existing behavior** - the callback path had the same race. No regression.

## Migration Plan

1. Update `StorageManager`: Remove `on_title_generated` from `log_session()` and `_generate_title_from_prompt()`.
2. Update `MessageNode`: Remove `session_title_setter` from `log_session()`.
3. Update OpenCode `message_routes.py`: Remove `_update_session_title()` callback wrapper. Ensure `_maybe_generate_title()` still triggers generation (the signal path handles the rest).
4. Update tests: Remove callback-based tests, add signal-based assertions.
5. Verify ACP server: Confirm `acp_agent.py` subscriber is intact.

No deployment-order constraints - this is a single-repo change.
