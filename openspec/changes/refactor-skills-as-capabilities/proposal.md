## Why

The current skill system treats skills as pure instruction documents — they inject XML into agent prompts but cannot contribute tools, MCP servers, or lifecycle hooks. This means a skill that needs a browser MCP server (e.g., Playwright) must rely on the user or pool config to provide it separately, breaking the "self-contained skill package" vision. pydantic-ai 1.102.0 introduced `AbstractCapability` — a composable middleware system that can inject instructions, tools, MCP servers, model settings, and lifecycle hooks all through a single unified interface. Refactoring skills as capabilities makes them first-class agent components that can declare and provision everything they need.

## What Changes

- **New**: `SkillCapability(AbstractCapability)` — wraps a skill's instructions, tools, and MCP servers as a composable pydantic-ai capability
- **New**: `mcp_servers` and `tools` fields on `Skill` frontmatter model — skills can declare MCP servers (command/url) and native Python tools (import path) directly in SKILL.md
- **New**: `SkillMcpManager` — manages per-skill MCP server connections with lazy start, idle timeout, and cleanup
- **New**: `SkillToolManager` — dynamically imports and registers Python tools declared via the `tools` frontmatter field (import paths like `"module.path:function_name"`)
- **Modified**: `SkillsInstructionProvider` — delegates instruction injection to `SkillCapability.get_instructions()`; keeps backward-compatible XML format
- **Modified**: `SkillsTools` (load_skill/list_skills) — `load_skill` triggers capability activation; tool/MCP hints included in response
- **Modified**: `Agent.get_agentlet()` — injects `SkillCapability` instances into the `capabilities` list alongside existing `Toolset`, `MCP`, `Hooks` etc.
- **Removed**: Redundant code paths — the dual instruction injection (provider-level + capability-level) is unified; `allowed_tools` is now enforced via `get_wrapper_toolset()` (type preserved as `str` with a `@field_validator` for backward compatibility)
- **Compatible**: Existing YAML config (`skills.paths`, `skills.instruction.mode`, `skills.instruction.max_skills`) continues to work unchanged

## Capabilities

### New Capabilities
- `skill-capability`: Skills are represented as `SkillCapability(AbstractCapability)` instances that provide instructions, tools, and MCP servers through pydantic-ai's capability middleware chain. Skills gain lifecycle hooks, tool filtering, and per-run state isolation.

### Modified Capabilities
<!-- None — existing skill-related behavior (prompt injection, load_skill tool, skill discovery) is preserved with the same external API. The internal implementation changes but no spec-level behavior contracts change. -->

## Impact

- `src/agentpool/skills/` — new `capability.py` (SkillCapability), `skill_mcp_manager.py`, `skill_tool_manager.py`; modified `skill.py` (new fields)
- `src/agentpool/resource_providers/skills_instruction.py` — delegates to SkillCapability; simplified
- `src/agentpool_toolsets/builtin/skills.py` — `load_skill` triggers capability activation
- `src/agentpool/agents/native_agent/agent.py` — `get_agentlet()` injects SkillCapability instances
- `src/agentpool_config/skills.py` — new `SkillMcpServerConfig`, `SkillToolConfig` models
- `src/agentpool/delegation/pool.py` — skill capability registration during pool setup
- Tests: new `test_skill_capability.py`, updated `test_skills.py`
