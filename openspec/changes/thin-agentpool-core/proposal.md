## Why

AgentPool has accumulated significant technical debt through multiple agent type implementations (claude, agui, codex), agent-specific storage providers, and deprecated legacy APIs. The framework is now ~65,000 LOC with ~25% being removable dead weight. This bloat increases maintenance burden, slows CI, complicates onboarding, and creates confusion about supported vs deprecated features. Thinning the core to native + acp agents only will make the framework leaner, faster to test, and easier to reason about.

## What Changes

### Agent Types (BREAKING)
- **Remove** `claude` agent type and all related code (~2,911 LOC)
- **Remove** `agui` agent type and all related code (~1,729 LOC)
- **Remove** `codex` agent type and all related code (~1,757 LOC)
- **Keep** `native` and `acp` agent types as the only supported first-class agents
- **Remove** `file` agent as a runtime type (keep as config-loading mechanism only)
- Update `AnyAgentConfig` union to only include `NativeAgentConfig` and `ACPAgentConfig`

### Storage Providers (BREAKING)
- **Remove** `claude_provider` (~837 LOC)
- **Remove** `codex_provider` (~440 LOC)
- **Keep** `sql_provider`, `memory_provider`, `file_provider`, `opencode_provider`

### Servers (No Change)
- All server layers remain intact: ACP server, OpenCode server, and their dependencies
- AG-UI server, MCP protocol server, A2A server, OpenAI API server are kept as-is (separate decision)

### Deprecated / Legacy Code Removal (BREAKING)
- **Remove** `MCPManager` / `ToolManager` (old tool management)
- **Remove** `AgentHooks` / `wrap_instruction()` (old hook system)
- **Remove** `history_processors` module
- **Remove** runtime dynamic connections (`connect_to()` / `create_connection()`)
- **Remove** old `teams:` / `connections:` YAML syntax translation layer (`graph_translation.py`)
- **Remove** ACP legacy APIs
- **Remove** OpenCode legacy SSE paths

### Refactoring
- **Simplify** `BaseAgent` base class by removing claude/agui/codex-specific abstractions
- **Simplify** config system by reducing `AnyAgentConfig` union and removing dead config models
- **Clean up** tests: remove tests for removed agents, update fixtures
- **Clean up** dependencies: remove `claude-sdk`, `agui-sdk`, `codex-sdk`, `tiktoken`, and other orphaned packages

## Capabilities

### New Capabilities
<!-- This is a thinning/refactoring change. No new capabilities are introduced. -->
- `lean-core-framework`: Framework core reduced to native + acp agents with simplified abstractions

### Modified Capabilities
<!-- Existing capabilities whose agent type support is reduced -->
- `agentnode-wrapper`: Supported agent types reduced to native and acp only
- `pydantic-graph-teams`: Team composition limited to native/acp agents
- `unified-session-lifecycle`: Session orchestration simplified for fewer agent run loops
- `sessionpool-only-execution`: RunHandle and TurnRunner logic simplified

## Impact

- **Breaking**: YAML configs using `type: claude`, `type: agui`, or `type: codex` will fail validation
- **Breaking**: Code importing `ClaudeCodeAgent`, `AGUIAgent`, `CodexAgent` will fail
- **Breaking**: Storage configs referencing `claude` or `codex` providers will fail
- **Dependencies**: ~8+ packages can be removed from `pyproject.toml`
- **Tests**: Significant test files for removed agents need deletion
- **CI**: Faster test runs due to fewer agent types and removed legacy code paths
- **Docs**: Documentation for removed agents and legacy YAML syntax needs updating
