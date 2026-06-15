## 1. Remove Deprecated and Legacy Code

> ⚠️ **Phase 1 Scope Note**: After codebase audit, MCPManager, ToolManager, AgentHooks, and wrap_instruction() are actively used by the kept `native` and `acp` agents (pool.py:110, base_agent.py:32, native_agent/agent.py:307-308, acp_agent/acp_agent.py:89). They are NOT dead code. This phase removes deprecation warnings and reclassifies these as stable APIs. Only truly dead code paths are deleted.

### 1a. Truly Dead Code (Delete)

- [ ] 1.1 Remove ACP legacy `"websocket"` transport string shortcut from `src/acp/transports.py:160-166`
- [ ] 1.2 Extract `GraphConfig`, `GraphStepConfig`, `GraphEdgeConfig`, `GraphJoinConfig` from `src/agentpool_config/graph_translation.py` into new file `src/agentpool_config/graph_config.py`. Update `pool.py:791` import to `from agentpool_config.graph_config import GraphConfig` — **keep `translate_config` in the import** until 1.3. Verify `pool.py:811` (`GraphConfig.model_validate(graph_data)`) still works.
- [ ] 1.3 Replace `pool.py:800`'s `translate_config(raw_data)` call with `GraphConfig.model_validate(raw_data)`, then **drop `translate_config` from the pool.py:791 import** (no more legacy YAML translation needed). The `_load_graph_config()` function's `path_for_loading` branch now directly validates the raw data as a `GraphConfig`.
- [ ] 1.4 Delete `src/agentpool_config/graph_translation.py` (only `translate_config()` remains after extraction)
- [ ] 1.5 Remove deprecated `history_processors` parameter from Agent constructor (keep `agentpool_config/session.py` config field — active pydantic-ai integration)
- [ ] 1.6 Remove OpenCode legacy SSE paths (verify all consumers migrated to EventBus before deletion — grep for `event_subscribers` and `OpenCodeEventBridge` usage)
- [ ] 1.7 Remove `history_processors` module if no remaining callers after constructor cleanup

### 1b. Reclassification (Keep Code, Remove Warnings)

- [ ] 1.8 Remove `DeprecationWarning` from `MCPManager` and `ToolManager` — they are the stable, active tool management layer
- [ ] 1.9 Remove `DeprecationWarning` from `AgentHooks` and `wrap_instruction()` — they are the stable, active hook system
- [ ] 1.10 Remove `DeprecationWarning` from `MessageNode.connect_to()` and `ConnectionManager.create_connection()` — stable connection API
- [ ] 1.11 Audit and remove any remaining `DeprecationWarning` in `base_agent.py` for reclassified APIs (e.g., line 283)
- [ ] 1.12 Run `ruff check` and `mypy` to verify no import errors from legacy removal

## 2. Remove Non-Core Agent Types

### 2a. Pre-Removal Blockers

- [ ] 2.0 Fix `src/agentpool_server/opencode_server/routes/config_routes.py` `_get_variants_from_agent()` (line 353-380): replace body with `return {}` unconditionally. No remaining agent types support thought-level mode variants. Remove the function-local imports of `ClaudeCodeAgent` and `CodexAgent` (lines 365-366).
- [ ] 2.0.1 Replace `CLAUDE_CODE_ASSISTANT` config fallback in `src/agentpool_cli/serve_opencode.py` (lines 73, 84): replace with `ACP_ASSISTANT` from `config_resources`.
- [ ] 2.0.2 Replace `CLAUDE_CODE_ASSISTANT` reference in `src/agentpool_server/opencode_server/server.py` `__main__` block (line 490): same replacement — use `ACP_ASSISTANT`.

### 2b. Agent Implementation Removal

- [ ] 2.1 Delete `src/agentpool/agents/claude_code_agent/` directory and all imports
- [ ] 2.2 Delete `src/agentpool/agents/agui_agent/` directory and all imports
- [ ] 2.3 Delete `src/agentpool/agents/codex_agent/` directory and all imports

### 2c. Config Model Removal

- [ ] 2.4 Remove `ClaudeAgentConfig`, `AGUIAgentConfig`, `CodexAgentConfig` from `agentpool_config/manifest.py` `AnyAgentConfig` union FIRST (let type checker guide cleanup)
- [ ] 2.5 Delete `ClaudeAgentConfig`, `AGUIAgentConfig`, `CodexAgentConfig` model class files from `agentpool_config/`
- [ ] 2.6 Remove `AGUIAgentWorkerConfig` from `agentpool_config/workers.py:60-69` and update `WorkerConfig` union
- [ ] 2.7 Remove agent type registration entry points for claude/agui/codex from `pyproject.toml`

### 2d. Factory, Export, and Import Cleanup

- [ ] 2.8 Update `AgentPool.get_agent()` factory to only instantiate `NativeAgent` and `ACPAgent`
- [ ] 2.9 Update `src/agentpool/agents/__init__.py` — remove `AGUIAgent`, `ClaudeCodeAgent`, `CodexAgent` from imports and `__all__`
- [ ] 2.10 Remove `ClaudeCodeAgent` from `src/agentpool_commands/models.py` isinstance checks (lines 67, 69, 113, 115). Replace `isinstance(node, Agent | ACPAgent | ClaudeCodeAgent)` with `isinstance(node, Agent | ACPAgent)`.
- [ ] 2.11 Remove `claude` file format parsing code from `src/agentpool_config/file_parsing.py` (the `file_agents` config mechanism is kept, but claude-specific format parsing references removed types)
- [ ] 2.12 Delete YAML config resources AND clean up `config_resources/__init__.py` atomically:
  - Delete `src/agentpool/config_resources/codex_agent.yml`, `claude_code_agent.yml`, `agui_test.yml`
  - Remove `CLAUDE_CODE_ASSISTANT` export from `src/agentpool/config_resources/__init__.py`
  - Remove `CODEX_ASSISTANT` export from `src/agentpool/config_resources/__init__.py`
  - Remove `AGUI_TEST` export from `src/agentpool/config_resources/__init__.py`
  - Update `ALL_POOL_CONFIGS` tuple to remove deleted entries
  - Update `__all__` to remove deleted exports
- [ ] 2.13 Delete `examples/agui_remote_agent.py` (imports `AGUIAgent`)
- [ ] 2.14 Run tests to ensure `native` and `acp` agents still function correctly

## 3. Remove Agent-Specific Storage Providers

- [ ] 3.1 Delete `src/agentpool_storage/claude_provider/` directory
- [ ] 3.2 Delete `src/agentpool_storage/codex_provider/` directory
- [ ] 3.3 Remove `claude` and `codex` provider type discriminators from `agentpool_config/storage.py` storage config models
- [ ] 3.4 Update `AgentPool` storage initialization to only load sql/memory/file/opencode providers
- [ ] 3.5 Verify `opencode_provider` remains intact and functional

## 4. Handle codex_adapter Fate

> `codex_adapter` (22 files, ~1,000+ LOC) is imported only by `codex_agent` and `codex_provider` — both removed in Phases 2-3. It becomes orphaned dead code. It is also listed in `pyproject.toml:474` under `[tool.uv.build-backend] module-name`.

- [ ] 4.1 Delete `src/codex_adapter/` directory
- [ ] 4.2 Remove `"codex_adapter"` from `[tool.uv.build-backend] module-name` list in `pyproject.toml`
- [ ] 4.3 Run `uv sync && uv run python -c "import agentpool"` — expect no `ModuleNotFoundError`

## 5. Refactor BaseAgent and Config

> ⚠️ All 8 `@abstractmethod` declarations in `base_agent.py` are implemented by both `NativeAgent` and `ACPAgent`. Zero abstract methods can be removed. This phase focuses on type narrowing and removing dead agent-type-specific code paths.

- [ ] 5.1 Narrow `AgentTypeLiteral` (base_agent.py:98) from `Literal["native", "acp", "agui", "claude", "codex"]` to `Literal["native", "acp"]`
- [ ] 5.2 Remove AG-UI frame stack detection from `_should_bypass_session_pool()` (base_agent.py:149-157, Cases 2 & 3) — keep only the ContextVar check (Case 1). The AG-UI **server** (kept) doesn't use this path.
- [ ] 5.3 Audit `AGENT_TYPE == "native"` branches in `base_agent.py` (queue_prompt at line 706, run_stream at line 1057) — they are now tautologies for `else` path (only ACP remains). No code change needed but update docstrings.
- [ ] 5.4 Remove `agui_agents` property from `AgentsManifest` (manifest.py:568-571)
- [ ] 5.5 Remove `claude_code_agents` property from `AgentsManifest` (manifest.py:573-576)
- [ ] 5.6 Fix `isinstance(agent_config, NativeAgentConfig | ClaudeCodeAgentConfig)` at `manifest.py:761` → replace with `isinstance(agent_config, NativeAgentConfig)`
- [ ] 5.7 Remove `AGUIAgentConfig` and `ClaudeCodeAgentConfig` imports from `agentpool_config/models/__init__.py` and their `__all__` entries
- [ ] 5.8 Clean up `agentpool_config/` remaining dead config fields only used by removed agents
- [ ] 5.9 Verify all `@abstractmethod` declarations are still implemented by both `NativeAgent` and `ACPAgent` (methods: `_stream_events`, `get_available_models`, `get_modes`, `_set_mode`, `list_sessions`, `load_session`, `model_name`, `set_model`)
- [ ] 5.10 Update docstrings and comments referencing `ClaudeCodeAgent`, `AGUIAgent`, `CodexAgent` in `base_agent.py`
- [ ] 5.11 Run `mypy src/` to verify type safety after BaseAgent refactor

## 6. Clean Up Tests and Dependencies

### 6a. Test File Deletion

- [ ] 6.1 Delete whole-directory test files for removed agents:
  - `tests/agents/claude_code_agent/`
  - `tests/agents/agui_agent/`
  - `tests/agents/codex_agent/`
  - `tests/agentpool_storage/claude_provider/`
  - `tests/agentpool_storage/codex_provider/`
- [ ] 6.2 Delete test YAML fixtures: claude_code_config.yml, codex_config.yml, agui_config.yml from `tests/`
- [ ] 6.3 Surgically remove removed-agent references from hybrid test files (update imports/fixtures, keep generic tests for native/acp):
  - `tests/agents/test_async_io_operations.py` (12 refs, all in setup — update imports)
  - `tests/agents/test_external_agent_event_sequence.py` (11 refs — remove claude/codex-specific test functions, keep native/acp)
  - `tests/agents/test_event_queue_isolation.py` (7 refs — remove claude-specific test, keep generic)
  - `tests/integration/test_permission_denial_sync.py` (3 refs — update imports)
- [ ] 6.4 Delete test files for removed legacy code paths (graph_translation.py tests, OpenCode SSE tests, ACP legacy transport tests)
- [ ] 6.5 Update `conftest.py` fixtures to remove references to removed agents

### 6b. Dependency Cleanup

- [ ] 6.6 Remove `clawd-code-sdk>=0.1.36` from core dependencies in `pyproject.toml`
- [ ] 6.7 Remove orphaned packages from `pyproject.toml`:
  - `tiktoken` (verify: `uv run python -c "import tiktoken; print(tiktoken.__version__)"` before removal, then `uv run pytest tests/agents/native_agent/ -x` after)
  - `clawd-code-sdk` (already covered in 6.6)
  - Any remaining packages only used by removed agents — audit with `uv tree --invert` per removed package
- [ ] 6.8 Keep `ag-ui-protocol>=0.1.10` in core dependencies — the AG-UI **server** (kept) requires it. Only the agent-side usage goes away.
- [ ] 6.9 Run `uv sync` to regenerate lock file with reduced dependencies
- [ ] 6.10 Run `uv run pytest -m unit` to verify unit tests pass
- [ ] 6.11 Run `uv run pytest -m integration` to verify integration tests pass
- [ ] 6.12 Run `duty lint` (ruff + mypy + format check) to ensure code quality

## 7. Update Documentation

- [ ] 7.1 Update `README.md` — remove `type: claude_code`, `type: codex`, `type: agui` examples; remove "Claude Code, Codex" from the flowchart; update "Direct Integrations" section
- [ ] 7.2 Update `AGENTS.md` — remove references to removed agent types where present
- [ ] 7.3 Delete or update `docs/advanced/agui_example.yml` (5 agents of type: agui)
- [ ] 7.4 Verify `config_resources/__init__.py` cleanup is complete (export cleanup was done in task 2.12). Confirm no remaining references to deleted YAML files in config loading logic.

## 8. Final Verification

- [ ] 8.1 Run full test suite: `uv run pytest`
- [ ] 8.2 Verify `native` agent streaming still works: `uv run pytest tests/agents/native_agent/ -m "not slow" -x`
- [ ] 8.3 Verify `acp` agent connection still works: `uv run pytest tests/agents/acp_agent/ -m "not slow" -x`
- [ ] 8.4 Verify YAML config with ALL removed agent types fails validation with clear error:
  ```
  # type: claude
  uv run python -c "from agentpool_config.manifest import AgentsManifest; m = AgentsManifest(agents={'test': {'type': 'claude', 'model': 'openai:gpt-4o', 'system_prompt': 'test'}})"
  # expect ValidationError
  # type: codex
  uv run python -c "...type: codex..."
  # expect ValidationError
  # type: agui
  uv run python -c "...type: agui..."
  # expect ValidationError
  ```
- [ ] 8.5 Verify zero remaining references to removed types in production code:
  ```
  grep -rn "ClaudeCodeAgent\|CodexAgent\|AGUIAgent\|claude_code_agent\|codex_agent\|agui_agent" src/ --include="*.py" | grep -v "/test" | grep -v "/examples"
  ```
  Expected: zero results. **Note**: Comment-only references (e.g., `acp_server/session.py:558`, `mcp_server/tool_bridge.py:103`, `opencode_server/input_provider.py:132`, `agents/events/events.py:621`, `agents/modes.py:69`) are harmless. Add `# keep: comment-only reference` annotations or note them in a cleanup log.
- [ ] 8.6 Verify `file_agents` config mechanism still works:
  ```
  uv run python -c "from agentpool_config.manifest import AgentsManifest; m = AgentsManifest(file_agents={'test': {'path': 'test.yml'}}); print(m.file_agents)"
  # expect no error
  ```
- [ ] 8.7 Verify OpenCode server models endpoint still works (after config_routes.py fix):
  ```
  uv run agentpool serve-opencode src/agentpool/config_resources/acp_assistant.yml &
  curl http://localhost:PORT/config/models  # expect 200
  ```
- [ ] 8.8 Verify ACP server starts without ImportError:
  ```
  uv run agentpool serve-acp src/agentpool/config_resources/acp_assistant.yml &
  # verify process starts, then kill
  ```
- [ ] 8.9 Verify AG-UI server import works (server is kept per Design decisions):
  ```
  uv run python -c "from agentpool_server.agui_server import server; print('OK')"
  # expect OK, no ImportError
  ```
- [ ] 8.10 Verify reclassified stable APIs still work:
  ```
  uv run python -c "from agentpool.tools.tool_manager import ToolManager; from agentpool.hooks import AgentHooks; from agentpool.mcp_server.manager import MCPManager; print('OK')"
  # expect OK — all reclassified APIs importable and instantiable
  ```
- [ ] 8.11 Verify `config_resources` import is clean:
  ```
  uv run python -c "from agentpool.config_resources import ALL_POOL_CONFIGS; assert all(p.exists() for p in ALL_POOL_CONFIGS); print('OK')"
  # expect OK — no dangling Path entries
  ```
- [ ] 8.12 Scan `pyproject.toml` entry points for references to removed types (check `agentpool_toolsets`, `agentpool_commands`, etc.)
- [ ] 8.13 Run `mypy src/` as final type safety gate — expect zero errors
- [ ] 8.14 Grep for removed config class names:
  ```
  grep -rn "ClaudeCodeAgentConfig\|CodexAgentConfig\|AGUIAgentConfig\|AGUIAgentWorkerConfig" src/ --include="*.py"
  ```
  Expected: zero results.
- [ ] 8.15 Check total LOC reduction matches target (~65,000 → ~49,000, ~16,000 LOC removed)
- [ ] 8.16 Review git diff to ensure no unintended files were modified
- [ ] 8.17 Verify import sanity:
  ```
  uv run python -c "import agentpool; from agentpool.agents import Agent, ACPAgent; print('OK')"
  # expect OK — no ImportError
  ```
