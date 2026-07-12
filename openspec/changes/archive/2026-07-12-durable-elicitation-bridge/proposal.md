## Why

MCP elicitation requests ("ask the user" form prompts) are lost when the agent process crashes. The user is mid-answer, the process restarts, and the entire agent run is gone. Existing `DeferredBridge`/`ApprovalBridge` already solve this for deferred tool calls — we mirror that pattern for elicitation.

The initial implementation (commits 0c86b76e3..4faacc252) used a two-level interception (sentinel return + side-channel) where `handle_elicitation()` stored params in a side-channel and returned `ElicitResult(action="decline")` sentinel, with `CallDeferred` only raised in `MCPClient.call_tool()`. This design has a critical flaw: **local tools** (like `question_for_user`, `ask_followup_question`) that call `handle_elicitation()` directly never get `CallDeferred` raised, because they don't pass through `MCPClient.call_tool()`. The sentinel "decline" is misinterpreted as a real user decline.

## What Changes

- **`handle_elicitation()` raises `CallDeferred` directly** when `supports_durable_elicitation=True`. This is the single entry point — all tools (MCP and local) automatically participate in the durable path. No tool-level adaptation needed.
- **MCP elicitation callback wrapper catches `CallDeferred`** and converts to side-channel + sentinel. This isolates the FastMCP workaround (exception-catching) to `MCPClient` only. The side-channel (`_pending_elicitation_deferral`) becomes an internal implementation detail of the MCP client, not a contract on `AgentContext`.
- **`call_tool()` post-call side-channel check** remains unchanged — after MCP call returns, check side-channel and re-raise `CallDeferred`.
- **Crash recovery** unchanged — `handle_elicitation()` checks `cached_elicitation_responses` before raising, returns cached response during tool re-execution.
- **`ElicitationResolutionStrategy`** abstraction retained for future MRTR support.
- **`ElicitationDeferredBridge`** capability + `ElicitationFutureRegistry` unchanged.
- **Resume paths** (in-process + crash recovery) unchanged.

## Capabilities

### Modified Capabilities

- `durable-elicitation`: All elicitation requests (MCP and local) transparently participate in checkpoint/resume when the provider opts in via `supports_durable_elicitation`. Tools require zero adaptation. The FastMCP exception-catching workaround is isolated to `MCPClient.call_tool()`'s elicitation callback wrapper.

## Impact

- **Affected code**:
  - `src/agentpool/agents/context.py` — `handle_elicitation()` durable path changes from "store side-channel + return sentinel" to "raise CallDeferred directly"
  - `src/agentpool/mcp_server/client.py` — `elicitation_handler` adds `try/except CallDeferred` to catch and convert to side-channel + sentinel (FastMCP workaround)
  - `tests/elicitation/test_unit_elicitation.py` — Tests updated: `handle_elicitation()` now raises `CallDeferred` instead of returning sentinel + setting side-channel
- **No API changes**: Public API unchanged. `handle_elicitation()` signature unchanged (still returns `ElicitResult | ErrorData` for non-durable path; durable path raises `CallDeferred` which is already in the tool execution flow)
- **No new dependencies**
- **Breaking changes**: None for users. Internal `_pending_elicitation_deferral` semantics change (only set by MCP callback wrapper, not by `handle_elicitation()` directly)
- **Migration cost for local tools**: Zero. `question_for_user`, `ask_followup_question`, and any future local tools that call `handle_elicitation()` work automatically.
