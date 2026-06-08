## Context

AgentPool currently supports 5 agent types (`native`, `acp`, `claude`, `agui`, `codex`) plus a `file` config-loading mechanism, along with 4 agent-specific storage providers and a significant body of deprecated/legacy code. The codebase is ~65,000 LOC with an estimated ~16,000 LOC (25%) being removable dead weight. This creates maintenance overhead, confuses new contributors, and slows CI.

This design targets a surgical removal: keep the core framework intact (MessageNode abstraction, pydantic-graph integration, ACP protocol, OpenCode server), but strip away non-essential agent implementations and legacy APIs.

## Goals / Non-Goals

**Goals:**
- Reduce framework core to `native` and `acp` agent types only
- Remove `claude_provider` and `codex_provider` from storage
- Eliminate all deprecated/legacy code paths
- Simplify `BaseAgent` and config models by removing 3 agent type discriminators
- Clean up tests and dependencies
- Preserve all existing `native` and `acp` functionality without behavior changes

**Non-Goals:**
- No changes to ACP server or OpenCode server (kept intact)
- No changes to `sql_provider`, `memory_provider`, `file_provider`, `opencode_provider`
- No changes to pydantic-graph team execution model
- No new features or capability additions
- No migration script for existing claude/agui/codex configs (documented breaking change)

## Decisions

### Decision 1: Remove agent types at the config model layer first
**Rationale**: `AnyAgentConfig` in `agentpool_config/manifest.py` is the single source of truth for supported agent types. Removing `ClaudeAgentConfig`, `AGUIAgentConfig`, `CodexAgentConfig` from the union will cause type errors that guide the rest of the cleanup. This is safer than deleting implementation files first.
**Alternative considered**: Delete implementation files first, then fix config. Rejected because it leads to cascading import errors that are harder to trace.

### Decision 2: Keep `file` agent as config-only mechanism
**Rationale**: `file_agents` is not a runtime agent type but a YAML config loading mechanism that reads agent definitions from files. It has no dedicated agent implementation. Removing it would break config inheritance patterns.
**Alternative considered**: Remove entirely. Rejected because it would break legitimate config composition use cases.

### Decision 3: Remove deprecated code before agent types
**Rationale**: Deprecated code (`MCPManager`, `ToolManager`, `AgentHooks`, `connect_to()`, etc.) has no production callers and minimal test coverage. Removing it first reduces noise when refactoring agent types.
**Alternative considered**: Remove agents first. Rejected because deprecated code imports may reference agent types, creating circular cleanup work.

### Decision 4: Do not create migration shim for removed agents
**Rationale**: This is a major version bump change. Users with claude/agui/codex configs will need to update their YAML. A shim would perpetuate the debt we're trying to eliminate.
**Alternative considered**: Add deprecation warnings for one release cycle. Rejected because it defeats the purpose of a clean break.

### Decision 5: Remove orphaned dependencies in a single `pyproject.toml` pass
**Rationale**: Dependencies like `claude-sdk`, `agui-sdk`, `codex-sdk`, and `tiktoken` will have no importers after agent removal. A single pass keeps the dependency graph clean.
**Alternative considered**: Remove incrementally. Rejected because it creates intermediate broken states.

## Risks / Trade-offs

- **[Risk]** Import chains from core modules to removed agents may be non-obvious → **Mitigation**: Use type checker (mypy) and test suite to catch all references. Run `ruff check` after each removal phase.
- **[Risk]** Server code (AG-UI, MCP protocol, A2A, OpenAI API) imports removed agent types indirectly → **Mitigation**: As per user requirement, these servers are kept intact. Verify no cross-imports exist between removed agents and kept servers.
- **[Risk]** Test coverage drops significantly after removing ~25% of code → **Mitigation**: Remove corresponding test files atomically. Ensure remaining tests still pass.
- **[Risk]** `BaseAgent` simplification may accidentally remove abstractions used by `native` or `acp` → **Mitigation**: Review `BaseAgent` methods against `NativeAgent` and `ACPAgent` implementations before deletion.

## Migration Plan

1. **Phase 1: Deprecated/Legacy Removal** — Delete `MCPManager`, `ToolManager`, `AgentHooks`, `history_processors`, runtime dynamic connections, old YAML translation layer, ACP legacy APIs, OpenCode legacy SSE paths.
2. **Phase 2: Agent Type Removal** — Remove `claude`, `agui`, `codex` agent implementations, then update `AnyAgentConfig` union.
3. **Phase 3: Storage Cleanup** — Remove `claude_provider` and `codex_provider`.
4. **Phase 4: BaseAgent & Config Refactor** — Simplify `BaseAgent` abstractions, clean up config models.
5. **Phase 5: Dependency & Test Cleanup** — Update `pyproject.toml`, remove orphaned tests, run full test suite.

Rollback: Each phase is atomic and commit-able. If issues arise, revert the specific phase commit.

## Open Questions

- Should `codex` agent's underlying OpenAI model integration patterns be preserved in `native` agent docs? (Non-blocking — documentation decision.)
- Are there any third-party plugins or entry points referencing removed agent types via string names? (Need to scan entry point configs.)
