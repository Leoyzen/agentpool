## Context

AgentPool's elicitation system lets MCP tools and local tools ask the user questions via `AgentContext.handle_elicitation()`. The `InputProvider` abstraction routes these to the appropriate UI (CLI stdin, ACP SSE, OpenCode RPC).

### The Problem

When a provider supports durable elicitation (checkpoint + resume), the elicitation request must be deferred — the agent run is checkpointed, the question is sent to the client, and when the user responds, the run resumes. This requires raising `CallDeferred` so pydantic-ai's `HandleDeferredToolCalls` capability can intercept it.

### FastMCP Constraint

MCP tools register an elicitation callback with FastMCP via `create_elicitation_callback()`. FastMCP's callback wrapper **catches all exceptions** — if the callback raises `CallDeferred`, FastMCP swallows it and returns an error to the MCP server. This means `CallDeferred` cannot propagate from inside the FastMCP callback.

### Design Decision: Where to Handle the FastMCP Workaround

**Option A (initial implementation, rejected)**: `handle_elicitation()` always uses side-channel + sentinel. Every caller must check the side-channel.

- Problem: Local tools (`question_for_user`) don't check the side-channel. They see the sentinel "decline" and return "User declined" — broken behavior. Migration cost is non-zero for every local tool.

**Option B (corrected design, implemented)**: `handle_elicitation()` raises `CallDeferred` directly. The MCP elicitation callback wrapper catches `CallDeferred` and converts to side-channel + sentinel.

- Benefit: All tools (MCP and local) work automatically. The FastMCP workaround is isolated to `MCPClient.call_tool()`. Zero migration cost for local tools.

## Architecture

### Flow: Local Tool (e.g., `question_for_user`)

```
question_for_user()
  → ctx.handle_elicitation(params)
  → provider.supports_durable_elicitation=True
  → raise CallDeferred(metadata={"elicitation": params, "deferred_kind": "elicitation"})
  ↑ propagates naturally through call stack
  → pydantic-ai HandleDeferredToolCalls intercepts
  → ElicitationDeferredBridge: checkpoint → emit event → register future → block
```

No tool-level adaptation needed.

### Flow: MCP Tool

```
MCPClient.call_tool()
  → registers elicitation_handler with FastMCP
  → self._client.call_tool() (FastMCP)
    → FastMCP calls elicitation_handler
      → try: agent_ctx.handle_elicitation(params)
      → raises CallDeferred  ← handle_elicitation raises directly
      → except CallDeferred: store in side-channel, return ElicitResult(action="decline")
    → FastMCP sees "decline", sends to MCP server
    → MCP server returns tool result
  → check side-channel → raise CallDeferred
  → pydantic-ai HandleDeferredToolCalls intercepts
  → ElicitationDeferredBridge: checkpoint → emit event → register future → block
```

### Flow: Crash Recovery

```
_resume_native_agent()
  → builds cached_elicitation_responses from ElicitationResumePayload
  → agent.run_stream() with _run_ctx containing cached responses
  → tool re-executes
  → ctx.handle_elicitation(params)
  → checks cached_elicitation_responses[tool_call_id]
  → found → return cached ElicitResult (no raise, no side-channel)
  → tool processes response normally
```

### Key Invariants

1. `handle_elicitation()` is the **single entry point** for all elicitation. It either:
   - Returns cached response (crash recovery)
   - Raises `CallDeferred` (durable path)
   - Calls `provider.get_elicitation()` (synchronous path)
2. `_pending_elicitation_deferral` is **only written by** the MCP elicitation callback wrapper (FastMCP workaround), never by `handle_elicitation()` directly.
3. `MCPClient.call_tool()` checks `_pending_elicitation_deferral` after the MCP call returns and re-raises `CallDeferred`.
4. Local tools never touch `_pending_elicitation_deferral` — they get `CallDeferred` for free.
