## 1. Remove Deprecated and Legacy Code

- [ ] 1.1 Remove `MCPManager` and `ToolManager` old tool management classes
- [ ] 1.2 Remove `AgentHooks` and `wrap_instruction()` from hooks system
- [ ] 1.3 Remove `history_processors` module entirely
- [ ] 1.4 Remove runtime dynamic connections (`connect_to()` / `create_connection()`) from `MessageNode`
- [ ] 1.5 Remove old `teams:` / `connections:` YAML syntax translation layer (`graph_translation.py`)
- [ ] 1.6 Remove ACP legacy API endpoints and handlers
- [ ] 1.7 Remove OpenCode legacy SSE path handlers
- [ ] 1.8 Run `ruff check` and `mypy` to verify no import errors from legacy removal

## 2. Remove Non-Core Agent Types

- [ ] 2.1 Delete `src/agentpool/agents/claude_code_agent/` directory and all imports
- [ ] 2.2 Delete `src/agentpool/agents/agui_agent/` directory and all imports
- [ ] 2.3 Delete `src/agentpool/agents/codex_agent/` directory and all imports
- [ ] 2.4 Remove `ClaudeAgentConfig`, `AGUIAgentConfig`, `CodexAgentConfig` from `agentpool_config/manifest.py` `AnyAgentConfig` union
- [ ] 2.5 Remove `ClaudeAgentConfig`, `AGUIAgentConfig`, `CodexAgentConfig` model classes from `agentpool_config/agents.py` (or equivalent config module)
- [ ] 2.6 Update `AgentPool.get_agent()` factory to only instantiate `NativeAgent` and `ACPAgent`
- [ ] 2.7 Remove agent type registration entry points for claude/agui/codex from `pyproject.toml`
- [ ] 2.8 Run tests to ensure `native` and `acp` agents still function correctly

## 3. Remove Agent-Specific Storage Providers

- [ ] 3.1 Delete `src/agentpool_storage/claude_provider/` directory
- [ ] 3.2 Delete `src/agentpool_storage/codex_provider/` directory
- [ ] 3.3 Remove `claude` and `codex` provider type discriminators from storage config models
- [ ] 3.4 Update `AgentPool` storage initialization to only load sql/memory/file/opencode providers
- [ ] 3.5 Verify `opencode_provider` remains intact and functional

## 4. Refactor BaseAgent and Config

- [ ] 4.1 Simplify `BaseAgent` by removing claude/agui/codex-specific abstract methods or hooks
- [ ] 4.2 Review `BaseAgent` against `NativeAgent` and `ACPAgent` to ensure no needed abstractions are removed
- [ ] 4.3 Clean up `agentpool_config/` by removing dead config fields only used by removed agents
- [ ] 4.4 Update `AnyAgentConfig` union documentation and type annotations
- [ ] 4.5 Refactor `BaseAgent` event handler types if simplified by removing multi-agent-type complexity
- [ ] 4.6 Run `mypy src/` to verify type safety after BaseAgent refactor

## 5. Clean Up Tests and Dependencies

- [ ] 5.1 Delete all test files specifically for claude/agui/codex agents
- [ ] 5.2 Delete all test files specifically for claude/codex storage providers
- [ ] 5.3 Delete all test files for deprecated/legacy code paths
- [ ] 5.4 Update `conftest.py` fixtures to remove references to removed agents
- [ ] 5.5 Remove `claude-sdk`, `agui-sdk`, `codex-sdk`, `tiktoken`, and other orphaned packages from `pyproject.toml`
- [ ] 5.6 Run `uv sync` to regenerate lock file with reduced dependencies
- [ ] 5.7 Run `uv run pytest -m unit` to verify unit tests pass
- [ ] 5.8 Run `uv run pytest -m integration` to verify integration tests pass
- [ ] 5.9 Run `duty lint` (ruff + mypy + format check) to ensure code quality

## 6. Final Verification

- [ ] 6.1 Run full test suite: `uv run pytest`
- [ ] 6.2 Verify `native` agent streaming still works end-to-end
- [ ] 6.3 Verify `acp` agent connection and protocol exchange still works
- [ ] 6.4 Check total LOC reduction matches target (~16,000+ LOC removed)
- [ ] 6.5 Review git diff to ensure no unintended files were modified
