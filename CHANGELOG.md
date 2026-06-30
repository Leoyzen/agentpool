# Changelog

## [Unreleased] - PydanticAI Thinning Refactor

### Added
- `HooksCapabilityAdapter` (`src/agentpool/agents/native_agent/hooks_capability_adapter.py`) — new bridge that builds `pydantic_ai.capabilities.Hooks` from AgentPool hook callables with priority combining (deny > ask > allow), matcher filtering, and return type normalization
- `from_agent_hooks()` factory method on `HooksCapabilityAdapter` — extracts `fn`, `matcher`, `input_match` from existing `CallableHook`/`CommandHook`/`PromptHook` instances for transparent migration
- 151 regression tests covering hook combination, ProcessHistoryAdapter, PromptInjectionManager, Tool conversion, and event subclass behavior

### Changed
- `NativeAgentHookManager.as_capability()` now uses `HooksCapabilityAdapter.from_agent_hooks()` instead of `AgentHooks.as_capability()` — injection consumption wrapping preserved
- `AGENTS.md` updated with new architecture conventions (Hooks delegation, ProcessHistory direct usage, event passthrough, ToolKind deprecation)

### Deprecated
- `AgentHooks.as_capability()` — emits `DeprecationWarning`. Use `HooksCapabilityAdapter.from_agent_hooks()` instead
- `Hook` ABC and subclass hierarchy (`CallableHook`, `CommandHook`, `PromptHook`) — remain functional for YAML config instantiation but no longer the primary integration path
- `ProcessHistoryAdapter` — `get_agentlet()` already uses `pydantic_ai.capabilities.ProcessHistory` directly
- `ToolKind` literal type — use string-based tool name patterns instead
- `ToolResult.structured_content` — use PydanticAI's native `ToolReturn` structured return mechanism
- `PartStartEvent`/`PartDeltaEvent` PydanticAI subclassing — `session_id` should be accessed via `AgentContext` or `RunContext.deps`

### Notes
- No behavioral changes — all 734 existing unit tests pass at the same rate as baseline
- 39 pre-existing test failures and 14 errors on `develop/agentic` branch remain unchanged (not caused by this refactor)
- ACP agent path fully preserved — `PromptInjectionManager.queue()`/`pop_queued()`/`flush_pending_to_queue()` remain for non-native agents
