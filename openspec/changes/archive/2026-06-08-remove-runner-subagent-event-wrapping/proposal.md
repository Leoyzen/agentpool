## Why

Currently, `TurnRunner._maybe_wrap_event` automatically wraps all child session events in `SubAgentEvent` envelopes. This violates the architectural principle that the runner layer should be session-agnostic — events should flow raw through the EventBus, and protocol layers should handle session hierarchy independently. This wrapping also causes bugs: `_task_sync` in `BackgroundTaskProvider` cannot detect completion signals (`StreamCompleteEvent`, `ToolCallStartEvent`) because they are hidden inside `SubAgentEvent` wrappers, causing the lead agent to hang indefinitely.

## What Changes

- **BREAKING**: Remove `TurnRunner._maybe_wrap_event` and stop wrapping child session events as `SubAgentEvent` in the runner layer
- **BREAKING**: Update opencode server `event_processor.py` to route events by `event.session_id` instead of relying on `SubAgentEvent` wrapping
- **BREAKING**: Update ACP server `event_converter.py` to handle raw child session events and remove inline/tool_box subagent rendering modes
- Restore `BackgroundTaskProvider._task_sync` match logic to handle raw events directly (revert SubAgentEvent unwrapping)
- Ensure all stream events carry correct `session_id` metadata for protocol-layer routing

## Capabilities

### New Capabilities
- `session-aware-event-routing`: Protocol layers route events to correct session contexts using `event.session_id` instead of `SubAgentEvent` wrapping

### Modified Capabilities
- `opencode-subagent-rendering`: Requirements change — remove dependency on `SubAgentEvent` wrapping; protocol layer handles child session event routing directly
- `acp-subagent-rendering`: Requirements change — remove inline and tool_box subagent display modes; simplify to legacy mode only until official RFD implementation

## Impact

- **TurnRunner** (`agentpool/orchestrator/core.py`): Removes `_maybe_wrap_event` method; `_publish_event` becomes direct publish
- **EventBus** (`agentpool/orchestrator/core.py`): No changes — already supports `scope="descendants"` for raw event subscription
- **opencode event_processor** (`agentpool_server/opencode_server/event_processor.py`): Refactor to use `event.session_id` for context switching; remove `_process_subagent_event`
- **ACP event_converter** (`agentpool_server/acp_server/event_converter.py`): Remove `_convert_subagent_inline`, `_convert_subagent_tool_box`; simplify subagent handling
- **BackgroundTaskProvider** (`xeno_agent/.../background_task_provider.py`): Revert `_task_sync` match logic to handle raw events
- **Tests**: Update any tests that assert `SubAgentEvent` wrapping from runner layer
