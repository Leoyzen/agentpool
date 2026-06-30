## Why

When `session/load` replays conversation history to the client, each `SessionUpdate` is sent as a separate `session/update` JSON-RPC notification with an `await` between each. A 100-message session produces 300-600 individual notifications, each paying serialization + TCP flush overhead. Estimated load time: 150-300 seconds. The bottleneck is the wire layer (serial `await send_notification`), not event production.

## What Changes

- **Batch `SessionUpdate` collection in `replay()`**: Convert messages to `SessionUpdate` objects first, then send them in batched groups instead of awaiting each notification individually.
- **New `ext_notification` batch protocol**: Introduce `_batch_session_updates` extension method that sends multiple `SessionUpdate` objects in a single JSON-RPC notification, using ACP's existing extension mechanism.
- **Graceful fallback**: When the client does not support `_batch_session_updates`, degrade to sequential `session/update` notifications automatically.
- **Configurable batch size**: Add `notification_batch_size` (default 20) and `notification_flush_interval` (default 0.0s) to `ACPNotifications` for tuning. The initial proposal suggested 0.05s as the default, but this was rejected in favor of 0.0 (no artificial delay) since the `await` on each `send_batch_update` already provides natural flow control — an inter-batch delay would only slow down replay without benefit in the common stdio transport case.
- **No changes to ACP specification**: Uses `ext_notification` extension, no protocol spec changes required.

## Capabilities

### New Capabilities

- `acp-notification-batching`: Batch `SessionUpdate` delivery during `session/load` replay via `_batch_session_updates` extension notification, with graceful fallback to sequential delivery.

### Modified Capabilities

## Impact

- `src/acp/agent/notifications.py` — `replay()`, `send_update()`, new `send_batch_update()` and `_replay_*()` refactored to return `SessionUpdate` lists instead of sending directly
- `src/agentpool_server/acp_server/acp_agent.py` — `load_session()` calls batched replay
- `src/acp/schema/notifications.py` — no structural changes (batch uses `ext_notification`)
- `tests/acp/test_notifications_replay.py` — update for batch assertions
- `tests/servers/acp_server/test_acp_load.py` — verify batch delivery and fallback
