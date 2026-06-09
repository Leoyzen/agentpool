## Why

The ACP `turn_complete` session update was introduced (per draft RFD PR #644) to signal the explicit end of a prompt turn. However, the server currently advertises `turn_complete=True` unconditionally in `InitializeResponse`, and always emits `TurnCompleteUpdate` at stream completion — regardless of whether the client actually supports or expects this event.

This causes legacy/older ACP clients to exhibit "stuck UI" behavior: the frontend continues showing the agent as running and must be manually terminated. Forward compatibility is required so that clients that declare `turn_complete` support receive the new signal, while clients that do not continue to rely on `PromptResponse` as the sole turn-completion indicator.

## What Changes

- **Add `turn_complete` to `ClientCapabilities`**: A new boolean field `turn_complete` in `ClientCapabilities` so clients can explicitly declare support for the `turn_complete` session update.
- **Capability-gated `turn_complete` advertisement**: In `AgentPoolACPAgent.initialize()`, only advertise `turn_complete=True` when the client declares support.
- **Capability-aware `ACPEventConverter`**: Pass `client_capabilities` (or a derived boolean) into `ACPEventConverter` so it can conditionally emit `TurnCompleteUpdate`.
- **Fix `PromptResponse` timing for legacy clients (SessionPool path)**: When the client does NOT support `turn_complete`, `ACPProtocolHandler.handle_prompt()` must await the run's completion before returning `PromptResponse`, ensuring the legacy event order (`events → PromptResponse`) is preserved.
- **Update `ClientCapabilities.create()` factory**: Include the new `turn_complete` parameter.

## Capabilities

### New Capabilities
- `acp-turn-complete-compat`: Forward-compatible handling of the `turn_complete` ACP session update based on client capability negotiation.

### Modified Capabilities
<!-- No existing spec-level requirement changes — this is a protocol compatibility fix. -->

## Impact

- **ACP Schema** (`src/acp/schema/capabilities.py`): New `turn_complete` field on `ClientCapabilities`.
- **ACP Server** (`src/agentpool_server/acp_server/`):
  - `acp_agent.py`: `initialize()` logic gated by client capability.
  - `event_converter.py`: `ACPEventConverter` constructor accepts capability flag; `StreamCompleteEvent` branch conditionally yields `TurnCompleteUpdate`.
  - `session.py`: Pass client capability flag when creating `ACPEventConverter`.
  - `handler.py`: Block `PromptResponse` until run completion for legacy clients.
- **Tests**: ACP server tests may need updates for the new capability field and event timing.
