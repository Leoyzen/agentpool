## Context

AgentPool currently supports 5 agent types (`native`, `acp`, `claude`, `agui`, `codex`) plus a `file` config-loading mechanism, along with 4 agent-specific storage providers and a significant body of deprecated/legacy code. The codebase is ~65,000 LOC with an estimated ~16,000 LOC (25%) being removable dead weight. This creates maintenance overhead, confuses new contributors, and slows CI.

This design targets a surgical removal: keep the core framework intact (MessageNode abstraction, pydantic-graph integration, ACP protocol, OpenCode server), but strip away non-essential agent implementations and legacy APIs.

## Goals / Non-Goals

**Goals:**
- Reduce framework core to `native` and `acp` agent types only
- Remove `claude_provider` and `codex_provider` from storage
- Remove truly dead deprecated/legacy code (ACP legacy transport shortcut, `graph_translation.py`, deprecated Agent constructor param, OpenCode legacy SSE, `history_processors` module)
- Remove `DeprecationWarning` emissions from stable APIs (MCPManager, ToolManager, AgentHooks, connect_to) that power the kept agents
- Simplify `BaseAgent` and config models by removing 3 agent type discriminators
- Clean up tests and dependencies
- Reduce total codebase from ~65,000 LOC to ~49,000 LOC (~25% reduction)
- Clean up tests and dependencies
- Preserve all existing `native` and `acp` functionality without behavior changes

**Non-Goals:**
- No changes to ACP server core logic (kept intact)
- No changes to OpenCode server core logic (kept intact; minor `config_routes.py` refactor required to remove agent-type-specific imports — see Decision 6)
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

### Decision 6: Fix `opencode_server/config_routes.py` BEFORE removing agent implementations
**Rationale**: `config_routes.py:365-366` does module-level imports of `ClaudeCodeAgent` and `CodexAgent` inside `_get_agent_variants()`. Deleting these agent classes first would cause `ImportError` when the OpenCode server starts. The fix is trivial: make `_get_agent_variants()` return `{}` unconditionally since no remaining agent types support thought-level mode variants.
**Alternative considered**: Catch ImportError at runtime. Rejected — deferred errors are harder to debug and defeat the clean-break purpose.

### Decision 7: Remove `codex_adapter` entirely
**Rationale**: `codex_adapter` (22 files, ~1,000+ LOC, listed in `pyproject.toml:474` build modules) is imported only by `codex_agent` and `codex_provider` — both removed in Phases 2-3. It has zero remaining internal consumers.
**Alternative considered**: Keep as standalone utility package. Rejected because it has no independent value without the codex agent integration.

### Decision 8: Reclassify deprecated APIs as stable instead of deleting them
**Rationale**: After codebase audit, MCPManager, ToolManager, AgentHooks, wrap_instruction(), and MessageNode.connect_to() are actively used by the kept `native` and `acp` agents (pool.py:110, base_agent.py:32, native_agent/agent.py:307-308, acp_agent/acp_agent.py:89). They are NOT dead code. Deleting them would break all agent initialization. Removing only their `DeprecationWarning` emissions and reclassifying them as stable APIs achieves the thinning goal without the replacement effort.
**Alternative considered**: Refactor callers to use `as_capability()` pattern, then delete classes. Rejected because it would add 5+ weeks to the project and introduce risk to working production code paths.

### Decision 9: Extract `GraphConfig` before deleting `graph_translation.py`
**Rationale**: `GraphConfig` is defined inside `graph_translation.py` (line 145) but used independently at `pool.py:811` via `GraphConfig.model_validate(graph_data)` in the non-legacy graph path. Deleting the file without extraction would break `AgentPool.__init__()`. Extract `GraphConfig` + its dependency classes (`GraphStepConfig`, `GraphEdgeConfig`, `GraphJoinConfig`) into a new file `agentpool_config/graph_config.py`, update the import in pool.py, then delete `graph_translation.py`.
**Alternative considered**: Move `GraphConfig` into `manifest.py`. Rejected because it would bloat the manifest module (~145 lines of graph config).

## Risks / Trade-offs

- **[Risk]** Phase 1 originally planned to delete MCPManager, ToolManager, AgentHooks as "dead code" — codebase audit found they power the kept `native`/`acp` agents (pool.py:110, base_agent.py:32, native_agent/agent.py:307-308, acp_agent/acp_agent.py:89) → **Mitigation**: Rescoped Phase 1 to remove only deprecation warnings; reclassify these as stable APIs. This avoids ~5+ weeks of replacement effort.
- **[Risk]** Import chains from core modules to removed agents may be non-obvious → **Mitigation**: Use type checker (mypy) and test suite to catch all references. Run `ruff check` after each removal phase.
- **[Risk]** `opencode_server/routes/config_routes.py` has module-level imports of `ClaudeCodeAgent`/`CodexAgent` that will fail after Phase 2 → **Mitigation**: Fix this file BEFORE removing agent implementations (Decision 6). Added as task 2.0 in revised plan.
- **[Risk]** Test coverage drops significantly after removing ~25% of code → **Mitigation**: Remove corresponding test files atomically. Ensure remaining tests still pass. Set coverage floor at 80% for remaining code.
- **[Risk]** `BaseAgent` simplification may accidentally remove abstractions used by `native` or `acp` → **Mitigation**: Audit confirmed all 8 `@abstractmethod` are implemented by both remaining types. Focus on type narrowing and dead code path removal only.
- **[Risk]** `codex_adapter` is a wheel build module (`pyproject.toml:474`) → **Mitigation**: Remove from `[tool.uv.build-backend] module-name` list alongside directory deletion (Decision 7).
- **[Risk]** `ag-ui-protocol` dependency appears orphaned after removing `agui` agent type → **Mitigation**: Keep it — the AG-UI **server** (kept) requires it. Only the agent-side usage goes away.

## Migration Plan

1. **Phase 1: Deprecated/Legacy Code Cleanup** — Extract `GraphConfig` from `graph_translation.py`, then delete truly dead code (ACP legacy `"websocket"` transport, `graph_translation.py`, deprecated Agent constructor param, OpenCode legacy SSE). Remove deprecation warnings only from actively-used APIs (MCPManager, ToolManager, AgentHooks, connect_to) — these power the kept `native`/`acp` agents and are reclassified as stable APIs.
2. **Phase 2: Agent Type Removal** — First fix three pre-removal blockers: (a) `opencode_server/config_routes.py` `_get_variants_from_agent()` — remove ClaudeCodeAgent/CodexAgent imports, (b) `serve_opencode.py` — replace `CLAUDE_CODE_ASSISTANT` fallback, (c) `server.py` — replace same reference. Then remove `claude`, `agui`, `codex` agent implementations, update `AnyAgentConfig` union, and clean up all cross-cutting imports in `agentpool_commands/models.py`, `config_resources/__init__.py`, etc.
3. **Phase 3: Storage Cleanup** — Remove `claude_provider` and `codex_provider`.
4. **Phase 4: `codex_adapter` Fate** — Delete orphaned `codex_adapter` package + remove from `pyproject.toml` build modules.
5. **Phase 5: BaseAgent & Config Refactor** — Narrow `AgentTypeLiteral` to 2 values, remove AG-UI frame detection from `_should_bypass_session_pool()`, remove `agui_agents`/`claude_code_agents` properties from `AgentsManifest`, clean up config model exports.
6. **Phase 6: Dependency & Test Cleanup** — Update `pyproject.toml`, delete whole-directory test files, surgically remove removed-agent references from hybrid test files, run full test suite.
7. **Phase 7: Documentation** — Update README.md, AGENTS.md, example YAML files, `config_resources/__init__.py` exports.
8. **Phase 8: Final Verification** — Full test suite, grep for remaining references, YAML validation rejection test for all 3 removed types, `file_agents` config verification, OpenCode server endpoint test, entry point scan, LOC reduction check.

Rollback: Each phase is atomic and commit-able. If issues arise, revert the specific phase commit.

## Open Questions

- Should `codex` agent's underlying OpenAI model integration patterns be preserved in `native` agent docs? (Non-blocking — documentation decision.)
- Are there any third-party plugins or entry points referencing removed agent types via string names? (Need to scan entry point configs.)
