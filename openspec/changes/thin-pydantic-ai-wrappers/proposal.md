## Why

AgentPool currently wraps pydantic-ai with substantial custom abstraction layers that were necessary when pydantic-ai was younger. Over time, pydantic-ai has evolved into a feature-rich framework with native support for toolsets, hooks, MCP servers, history processing, and dynamic instructions. Many of AgentPool's wrapper layers now duplicate or shadow pydantic-ai's native capabilities, creating maintenance overhead and diverging from upstream patterns.

After the `sessionpool-only-architecture` change removes session-scoped mutable state from BaseAgent, the agent instances become pure execution engines — making this the ideal time to thin the wrapper layers and delegate directly to pydantic-ai.

### Strategic Positioning: Complement, Don't Wrap

AgentPool's true value is not "wrapping pydantic-ai" but **complementing it** with capabilities pydantic-ai intentionally does not provide:

| Layer | pydantic-ai (single-agent execution) | AgentPool (multi-agent orchestration) |
|---|---|---|
| **Agent loop** | `Agent.iter()`, `AgentRun`, graph nodes | ✅ Reuse pydantic-ai |
| **Tool/provider** | `AbstractToolset`, `Hooks`, `MCP` | ✅ Delegate to pydantic-ai |
| **Session/turn** | None | 🔴 AgentPool's core value |
| **Event routing** | Single-run streaming | 🔴 `EventBus` with hierarchical subscriptions |
| **Protocol bridge** | None | 🔴 ACP/AG-UI/OpenCode servers |
| **Heterogeneous agents** | Native only | 🔴 Claude Code, ACP, AG-UI agents |

By thinning the wrapper layers in Phase 2, AgentPool's architecture becomes clearer: pydantic-ai handles **how one agent executes**, AgentPool handles **how multiple agents and sessions are orchestrated**.

### What AgentPool Will Delegate to pydantic-ai

- **Tool management**: `ToolManager`/`ResourceProvider` → `AbstractCapability` with `get_toolset()` + `get_instructions()`
- **Lifecycle hooks**: `AgentHooks`/`NativeAgentHookManager` → pydantic-ai `Hooks` capability
- **MCP servers**: `MCPManager` → pydantic-ai `MCP` capability
- **History processing**: Manual resolution → pydantic-ai `ProcessHistory` capability
- **System prompts**: `SystemPrompts`/`wrap_instruction()` → pydantic-ai `instructions` parameter (accepts `str`, `SystemPromptFunc`, or `Sequence` thereof)
- **Streaming events**: `RichAgentStreamEvent` pass-through → native `AgentStreamEvent` (Phase 2g, after capability migration is stable)

### What AgentPool Keeps (Core Differentiation)

- `SessionPool` / `TurnRunner` / `EventBus` — session lifecycle and cross-turn orchestration
- `MessageNode` / `AgentPool` / `ConnectionManager` — multi-agent registry and routing
- Protocol servers (ACP, AG-UI, OpenCode) — protocol bridging
- Heterogeneous agent support (Claude Code, ACP agents, AG-UI agents)

## What Changes

- **BREAKING**: `ToolManager`/`ResourceProvider`, `AgentHooks`/`NativeAgentHookManager`, `MCPManager`, manual history processor resolution, and `SystemPrompts` are collapsed into unified pydantic-ai `AbstractCapability` instances. Each AgentPool provider exposes `as_capability()` returning a capability.
- **BREAKING**: `NativeAgent.get_agentlet()` collects all capabilities via `as_capability()` from providers and passes them as a single `capabilities=[...]` list to `PydanticAgent`, eliminating manual tool flattening and hook resolution.
- AgentPool exposes a `capabilities` configuration field allowing users to directly pass pydantic-ai `AbstractCapability` instances (native or third-party) that are merged with internally-generated capabilities.
- `ChatMessage` and message types remain as the cross-protocol abstraction, but native pydantic-ai message types are used directly where possible.
- `RichAgentStreamEvent` wrapper is NOT thinned in Phase 2 — event thinning is deferred to Phase 2g after capability migration is stable and fully tested.
- Backward-compatibility shim layer maps existing `ToolConfig` / `HookConfig` / `MCPConfig` YAML structures to pydantic-ai capabilities during a deprecation period.
- Tool confirmation (`InputProvider`) remains an AgentPool concern, mapped to pydantic-ai's `ApprovalRequiredToolset` where applicable but preserving AgentPool's UI integration.
- pydantic-ai version is pinned with upper bound (`>=1.102.0,<2.0.0`) with CI tests against main branch.

## Capabilities

### New Capabilities

- `unified-capability-integration`: AgentPool's internal providers (tool providers, hooks, MCP, history processors) expose functionality through a unified `as_capability()` interface returning pydantic-ai `AbstractCapability` instances. `get_agentlet()` collects all capabilities (internal + user-provided) and passes them as `capabilities=[...]` to `PydanticAgent`.
- `direct-capability-passthrough`: AgentPool configuration supports direct injection of pydantic-ai `AbstractCapability` instances via `capabilities` field, enabling use of native, third-party, or custom capabilities without AgentPool wrapper.
- `eventbus-capability-adapter`: pydantic-ai `Hooks` capability publishes lifecycle events (`before_run`, `after_run`, `before_tool_execute`, `after_tool_execute`) to AgentPool's `EventBus` so protocol servers and cross-session consumers continue to receive events.
- `thinned-event-stream` (Phase 2g): Stream events from native pydantic-ai agents propagate pydantic-ai's native `AgentStreamEvent` types directly where they overlap with AgentPool's event taxonomy. **Deferred until Phase 2g.**

### Modified Capabilities

- `native-agent`: Requirements change — `get_agentlet()` now collects unified capabilities from all providers instead of manually flattening tools and resolving hooks.

## Impact

- `agentpool/tools/manager.py`: `ToolManager` and `ResourceProvider` deprecated; thin compatibility shim remains during transition.
- `agentpool/hooks/base.py`: `AgentHooks` and `NativeAgentHookManager` deprecated; compatibility shim maps to `Hooks` capability.
- `agentpool/mcp_server/manager.py`: `MCPManager` deprecated; compatibility shim delegates to `MCP` capability.
- `agentpool/agents/native_agent/agent.py`: `get_agentlet()` reconstructed to use pydantic-ai capabilities and instructions directly.
- `agentpool/utils/context_wrapping.py`: `wrap_instruction()` deprecated; existing instruction functions converted to pydantic-ai compatible signatures.
- `agentpool/agents/events/events.py`: Event taxonomy **NOT changed in Phase 2** — `RichAgentStreamEvent` remains the external interface. Thinning is Phase 2g.
- `agentpool_config/tools.py`, `agentpool_config/hooks.py`, `agentpool_config/mcp.py`: YAML config models updated to support pydantic-ai native structures alongside legacy structures.
- `pyproject.toml`: pydantic-ai version pinned to `>=1.102.0,<2.0.0` with CI compatibility tests.
- Tests: Inventory of affected test files:
  - `tests/tools/test_manager.py` — migrate from `ToolManager` to capability construction
  - `tests/hooks/test_hooks.py` — migrate from `AgentHooks` to `Hooks` capability
  - `tests/hooks/test_native_agent_hook_manager.py` — migrate hook wrapping tests
  - `tests/mcp_server/test_manager.py` — migrate from `MCPManager` to `MCP` capability
  - `tests/agents/test_native_agent.py` — update `get_agentlet()` tests
  - `tests/agents/test_base_agent.py` — update event stream tests (Phase 2g)
  - `tests/config/test_yaml_loading.py` — add capability config tests
  - Backward-compat tests added for shim layer.
- EventBus adapter: New adapter wiring pydantic-ai `Hooks` lifecycle callbacks to AgentPool `EventBus` for protocol server compatibility.
