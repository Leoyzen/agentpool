## Why

AgentPool currently has two parallel tool interception mechanisms: `wrap_tool()` (legacy, direct tools only) and pydantic-ai's capability chain (MCP/ACP tools). This split means confirmation checks, hooks, schema modification, and error handling behave inconsistently across tool sources. Only direct tools get confirmation and hooks; MCP and ACP MCP tools bypass them entirely. This blocks users from building uniform tool harness capabilities (monitoring, rate limiting, schema injection, error recovery) that work across all tool sources.

## What Changes

- **Enhance `NativeAgentHookManager.as_capability()`** to be the single source of truth for tool interception, adding `get_wrapper_toolset` (confirmation via `ApprovalRequiredToolset`), `prepare_tools` (schema modification), `wrap_tool_execute` (error handling), `before_tool_execute`/`after_tool_execute` (hooks + injection)
- **Remove hooks and confirmation logic from `wrap_tool()`** — these are now handled by the capability chain. `wrap_tool()` retains only AgentContext injection for legacy direct tools
- **Remove `if not self.hooks` guard** in `get_agentlet()` so the hooks capability always registers, applying uniformly to all tools
- **Move `tool_confirmation_mode` (always/never/per_tool) to `get_wrapper_toolset()`** — wraps the assembled toolset with pydantic-ai's `ApprovalRequiredToolset` based on mode, using pydantic-ai's native deferred tool + `HandleDeferredToolCalls` mechanism (already bridged via `approval_bridge.py`)
- **No breaking changes to public API** — `tool_confirmation_mode`, hook callbacks, and `InputProvider` interfaces remain unchanged

## Capabilities

### New Capabilities
- `unified-tool-interception`: Central capability providing `prepare_tools` (schema modification + confirmation mode), `wrap_tool_execute` (error handling), `before_tool_execute`/`after_tool_execute` (hooks + injection) — all unified through pydantic-ai's `AbstractCapability` chain, working uniformly across direct tools, MCP tools, and ACP MCP tools

### Modified Capabilities
- _(none — existing capability specs are unchanged; this is a refactoring of the interception layer)_

## Impact

- **Affected code**: `src/agentpool/agents/native_agent/hook_manager.py` (enhanced), `src/agentpool/agents/native_agent/tool_wrapping.py` (simplified), `src/agentpool/agents/native_agent/agent.py` (registration logic)
- **Dependencies**: No new dependencies. Uses existing pydantic-ai `AbstractCapability`, `ToolDefinition`, `HandleDeferredToolCalls`
- **Enables**: Future pydantic-ai capability extensions (tool harness, rate limiting, schema injection, audit logging) that work uniformly across all tool sources
