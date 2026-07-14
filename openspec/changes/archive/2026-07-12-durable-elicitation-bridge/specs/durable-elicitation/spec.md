# Durable Elicitation Specification

## Overview

Durable elicitation enables agent sessions to survive process crashes during user interaction. When a tool calls `handle_elicitation()` and the provider supports durability, the request is checkpointed and the run is deferred. After the user responds, the session resumes.

## Requirements

### REQ-001: Single Entry Point

`AgentContext.handle_elicitation()` MUST be the single entry point for all elicitation requests, regardless of whether the caller is an MCP tool or a local Python tool.

### REQ-002: Direct CallDeferred Raise

When `provider.supports_durable_elicitation` is `True`, `handle_elicitation()` MUST raise `CallDeferred(metadata={"deferred_kind": "elicitation", "elicitation": <params>})` directly. It MUST NOT return a sentinel value or use a side-channel.

### REQ-003: FastMCP Workaround Isolation

The MCP elicitation callback wrapper in `MCPClient.call_tool()` MUST catch `CallDeferred` from `handle_elicitation()` and convert it to the side-channel + sentinel pattern. This is the ONLY location that writes to `_pending_elicitation_deferral`.

### REQ-004: Post-Call Side-Channel Check

`MCPClient.call_tool()` MUST check `_pending_elicitation_deferral` after the MCP call returns and re-raise `CallDeferred` if set. This `except CallDeferred: raise` MUST be placed before any broad `except Exception` handler.

### REQ-005: Crash Recovery Cache

`handle_elicitation()` MUST check `cached_elicitation_responses[tool_call_id]` before raising `CallDeferred`. If a cached response exists, it MUST be returned directly (no raise, no side-channel).

### REQ-006: Synchronous Path Unchanged

When `provider.supports_durable_elicitation` is `False`, `handle_elicitation()` MUST call `provider.get_elicitation(params)` directly. This behavior is unchanged.

### REQ-007: Zero Migration for Local Tools

Local tools (e.g., `question_for_user`, `ask_followup_question`) that call `handle_elicitation()` MUST work with the durable path without any code changes. `CallDeferred` propagates naturally through the call stack.

### REQ-008: Provider Opt-In

`InputProvider.supports_durable_elicitation` MUST default to `False`. Providers override it dynamically based on runtime capabilities (e.g., `ACPInputProvider` checks `session.checkpoint_enabled`).

### REQ-009: Resume Paths

Two resume paths MUST be supported:
- **In-process**: `ElicitationFutureRegistry.resolve()` unblocks the pending future.
- **Crash recovery**: `_resume_native_agent()` pre-populates `cached_elicitation_responses`, tool re-executes with cached response.

### REQ-010: Session Close Cleanup

`close_session()` MUST call `ElicitationFutureRegistry.reject_all(SessionClosedError())` to unblock any pending futures.
