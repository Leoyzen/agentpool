## 1. Phase 1: Core Split

- [x] 1.1 Create `src/agentpool/orchestrator/event_bus.py` — move `EventBus`, `EventEnvelope`, `drain_and_merge`, and related helpers from `core.py`
- [x] 1.2 Create `src/agentpool/orchestrator/session_controller.py` — move `SessionController`, `SessionState`, `RunHandle`, `RunStatus` from `core.py`
- [x] 1.3 Create `src/agentpool/orchestrator/session_pool.py` — move `SessionPool`, `SessionPoolConfig`, `SessionPoolMetrics` from `core.py`
- [x] 1.4 Update `orchestrator/__init__.py` — re-export all moved symbols from new modules
- [x] 1.5 Reduce `orchestrator/core.py` to thin re-exports (or remove entirely)
- [x] 1.6 Verify no circular imports between the three new modules
- [x] 1.7 Run `uv run pytest tests/orchestrator/` — all tests pass without modification
- [x] 1.8 Run `uv run mypy src/agentpool/orchestrator/` — no type errors

## 2. Phase 2: Run Stream Unification

- [x] 2.1 Write comparison tests — assert `RunExecutor` output matches `BaseAgent.run_stream()` event ordering for all event types
- [x] 2.2 Refactor `BaseAgent.run_stream()` to delegate to `RunExecutor` instead of using standalone producer/consumer pattern
- [x] 2.3 Remove or gut `BaseAgent._run_stream_once()` — no `asyncio.ensure_future` producer task
- [x] 2.4 Verify pdai Capability hooks fire on standalone run path (write test with a mock `wrap_node_run` Capability)
- [x] 2.5 Verify pdai `before_model_request` hook fires on standalone run path
- [x] 2.6 Verify pdai `after_node_run` hook fires on standalone run path
- [x] 2.7 Update all 44 callers of `run_stream` across protocol servers — ensure they work with unified path
- [x] 2.8 Update ACP server (`acp_server/handler.py`) — verify `ProtocolEventConsumerMixin` works with unified run
- [x] 2.9 Update OpenCode server (`opencode_server/session_pool_integration.py`)
- [x] 2.10 Update AG-UI server (`agui_server/server.py`)
- [x] 2.11 Update OpenAI API server (`openai_api_server/server.py`)
- [x] 2.12 Run `uv run pytest tests/agents/` — all agent tests pass
- [x] 2.13 Run `uv run pytest tests/servers/` — all server integration tests pass
- [x] 2.14 Run `uv run pytest -m acp_snapshot` — ACP snapshot tests pass

## 3. Phase 3: EventBus Backpressure

- [x] 3.1 Replace `anyio.create_memory_object_stream()` with `asyncio.Queue(maxsize=...)` in `EventBus.__init__` (PR #96)
- [x] 3.2 Update `subscribe()` to return `asyncio.Queue` (or wrapper with compatible interface) (PR #96)
- [x] 3.3 Update `_send()` to use `put_nowait()` with overflow policy handling (PR #96)
- [x] 3.4 Implement `drop_oldest` overflow policy — `get_nowait()` then `put_nowait()` (PR #96)
- [x] 3.5 Implement `drop_newest` overflow policy — silently discard on `QueueFull` (PR #96)
- [x] 3.6 Implement `drop_subscriber` overflow policy — close queue, remove subscriber (PR #96)
- [x] 3.7 Reject `block` overflow policy with `ValueError` in `EventBus.__init__` (PR #96)
- [x] 3.8 Update `unsubscribe()` to work with `asyncio.Queue` semantics (PR #96)
- [x] 3.9 Update dead subscriber cleanup — detect closed queues (PR #96)
- [x] 3.10 Update replay buffer to use `put_nowait()` for historical events (PR #96)
- [x] 3.11 Audit all `publish()` call sites — ensure no blocking `put()` calls (PR #96)
- [x] 3.12 Update `drain_and_merge()` to work with `asyncio.Queue` (use `get_nowait()` for drain) (PR #96)
- [x] 3.13 Run `uv run pytest tests/agents/events/` — EventBus tests pass (PR #96)
- [x] 3.14 Run `uv run pytest tests/orchestrator/test_envelope_integration.py` (PR #96)

## 4. Phase 4: Team Cleanup

- [x] 4.1 Create `src/agentpool_config/graph_translation.py` module
- [x] 4.2 Implement `translate_team_to_graph()` for sequential teams — `members` → chained steps with implicit edges
- [x] 4.3 Implement `translate_team_to_graph()` for parallel teams — `members` → Fork/Join edges
- [x] 4.4 Map `shared_prompt` to step-level prompt in `GraphStepConfig`
- [x] 4.5 Map `member_timeout` to step-level timeout
- [x] 4.6 Map `member_prompt_templates` to per-step prompt templates
- [x] 4.7 Map `member_retry_attempts` and `member_retry_delay` (document dropped fields if no GraphConfig equivalent — see Open Question 8)
- [x] 4.8 Integrate translator into config loading — auto-translate when `teams:` present, `graph:` absent
- [x] 4.9 Write tests for translator covering all `TeamConfig` field combinations
- [ ] 4.10 Test translator against all `teams:` YAML configs in `site/examples/`
- [ ] 4.11 Remove `Team` class from `src/agentpool/delegation/team.py`
- [ ] 4.12 Remove `TeamRun` class from `src/agentpool/delegation/teamrun.py`
- [ ] 4.13 Remove `TeamConfig.get_team()` factory method
- [ ] 4.14 Update all 50 callers of `TeamRun` — route through `GraphConfig` + `GraphBuilder`
- [ ] 4.15 Remove `src/agentpool/delegation/graph_team.py` `_TeamGraphState` if fully replaced
- [ ] 4.16 Update `AgentPool.__init__` — stop creating `Team`/`TeamRun` instances
- [ ] 4.17 Run `uv run pytest tests/teams/` — team tests updated and passing
- [ ] 4.18 Run `uv run pytest tests/delegation/` — delegation tests passing

## 5. Phase 5: ToolProvider Deprecation

- [x] 5.1 Define `ToolsetFactory` protocol in `src/agentpool/tools/factory.py` — `async def create_toolset() -> Toolset`
- [ ] 5.2 Create `MCPToolsetFactory` — wraps MCP server, produces pdai `Toolset` with MCP tools
- [ ] 5.3 Create `LocalSkillToolsetFactory` — discovers filesystem skills, produces `Toolset`
- [ ] 5.4 Create `PoolToolsetFactory` — exposes agent/team delegation as subagent tools
- [x] 5.5 Create `ToolsetFactory` adapters — wrap old `ResourceProvider` methods for incremental migration
- [ ] 5.6 Migrate `MCPResourceProvider` callers (25) to `MCPToolsetFactory`
- [ ] 5.7 Migrate `LocalResourceProvider` callers (44) to `LocalSkillToolsetFactory`
- [ ] 5.8 Migrate `PoolResourceProvider` callers (1) to `PoolToolsetFactory`
- [ ] 5.9 Migrate `PlanProvider` to pdai `Toolset` subclass (stateful, needs `RunContext.deps`)
- [ ] 5.10 Add `DeprecationWarning` to `CodeModeResourceProvider.__init__` and `RemoteCodeModeResourceProvider.__init__`
- [ ] 5.11 Create `SkillBridgeCapability` in `src/agentpool/capabilities/skill_bridge.py` — injects skill XML into prompts via `before_model_request` hook
- [ ] 5.12 Remove `ResourceProvider` abstract base class (after all callers migrated)
- [ ] 5.13 Remove `AggregatingResourceProvider`, `FilteringResourceProvider`, `StaticResourceProvider`
- [ ] 5.14 Remove `SkillsInstructionProvider` (replaced by `SkillBridgeCapability`)
- [ ] 5.15 Run `uv run pytest tests/resource_providers/` — tests updated and passing
- [ ] 5.16 Run `uv run pytest tests/tools/` — tool tests passing
- [ ] 5.17 Run `uv run pytest tests/toolsets/` — toolset tests passing

## 6. Phase 6: Capability Layer

- [x] 6.1 Create `src/agentpool/capabilities/` package with `__init__.py`
- [x] 6.2 Implement `LoopDetectionCapability` — tracks depth via `wrap_node_run`, raises `LoopDetectionError` at `max_depth`. Uses `contextvars.ContextVar` for cross-run depth propagation
- [x] 6.3 Write tests for `LoopDetectionCapability` — verify depth tracking, error at max_depth, reset between runs
- [x] 6.4 Implement `TokenBudgetCapability` — tracks tokens via `wrap_model_request`, raises `TokenBudgetExceededError`
- [x] 6.5 Write tests for `TokenBudgetCapability` — verify cumulative tracking, error on exceed, multiple model requests
- [x] 6.6 Implement `ToolOutputBudgetCapability` — truncates tool output via `wrap_tool_execute` hook
- [x] 6.7 Write tests for `ToolOutputBudgetCapability` — verify truncation, truncation notice appended
- [x] 6.8 Implement `DynamicContextCapability` — applies compaction via `before_model_request` when near context limit. Logs warning when compaction triggered but no fn configured
- [x] 6.9 Write tests for `DynamicContextCapability` — verify compaction triggers at 80% context
- [x] 6.10 Implement `SkillActivationCapability` — dynamic skill activation via `before_model_request`, supersedes `SkillBridgeCapability`. Injects into `SystemPromptPart.content` (not `msg.system_prompt`)
- [x] 6.11 Write tests for `SkillActivationCapability` — verify skill matching, injection into SystemPromptPart
- [x] 6.12 Implement `MemoryCapability` — persists/retrieves memory via `after_node_run` + `before_model_request`. Shares `_store` dict reference across runs (not shallow copy)
- [x] 6.13 Write tests for `MemoryCapability` — verify persistence across turns, session scoping
- [ ] 6.14 Audit existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) — migrate or document overlap with Capabilities
- [ ] 6.15 Add YAML config support for attaching Capabilities to agents (`capabilities:` section)
- [ ] 6.16 Update `Agent` class to accept and attach Capabilities from config
- [ ] 6.17 Run `uv run pytest tests/agents/` — agent tests with Capabilities passing

## 7. Phase 7: Server Modularization

- [x] 7.1 Add `import-linter` as dev dependency in `pyproject.toml`
- [x] 7.2 Create `.importlinter` config or `[tool.importlinter]` in `pyproject.toml` — define forbidden contracts (core→app)
- [x] 7.3 Run `lint-imports` to get full list of violations — 80 direct violations across 3 contracts (8 server→cli/commands, 72 config→core, 0 acp→server direct); all documented in `ignore_imports` with `allow_indirect_imports = true` to keep CI green while preventing new direct violations
- [ ] 7.4 Fix violation 1 — move code to core or invert dependency
- [ ] 7.5 Fix violation 2 — move code to core or invert dependency
- [ ] 7.6 Fix violation 3 — move code to core or invert dependency
- [ ] 7.7 Fix violation 4 — move code to core or invert dependency
- [ ] 7.8 Fix any additional violations discovered by `lint-imports`
- [ ] 7.9 Add `lint-imports` to CI pipeline (`.github/workflows/`)
- [ ] 7.10 Verify `lint-imports` passes with zero violations (after removing all `ignore_imports` entries and `allow_indirect_imports`)
- [ ] 7.11 Run `uv run pytest` — full test suite passes after import fixes

## 8. Phase 8: Rename to agentwolf

- [x] 8.0 Automated rename script created at `scripts/rename_to_agentwolf.py` (PR #101) — execution deferred until all phases merge
- [ ] 8.1 Rename `src/agentpool/` → `src/agentwolf/` (execute script after all phases merge)
- [ ] 8.2 Rename `src/agentpool_config/` → `src/agentwolf_config/`
- [ ] 8.3 Rename `src/agentpool_server/` → `src/agentwolf_server/`
- [ ] 8.4 Rename `src/agentpool_toolsets/` → `src/agentwolf_toolsets/`
- [ ] 8.5 Rename `src/agentpool_storage/` → `src/agentwolf_storage/`
- [ ] 8.6 Rename `src/agentpool_cli/` → `src/agentwolf_cli/`
- [ ] 8.7 Rename `src/agentpool_commands/` → `src/agentwolf_commands/`
- [ ] 8.8 Rename `src/agentpool_prompts/` → `src/agentwolf_prompts/`
- [ ] 8.9 Update all Python imports (1599+) — `from agentpool` → `from agentwolf`, `import agentpool` → `import agentwolf`
- [ ] 8.10 Update `src/acp/` — fix 2 imports referencing `agentpool` → `agentwolf`
- [ ] 8.11 Update `pyproject.toml` — package names, entry points (`agentpool` → `agentwolf`), `[project.scripts]`
- [ ] 8.12 Update YAML configs in `site/examples/` — all `agentpool` references → `agentwolf`
- [ ] 8.13 Update Markdown documentation — all `.md` files, README, AGENTS.md
- [ ] 8.14 Update docstrings and inline comments referencing `agentpool`
- [ ] 8.15 Update `mkdocs.yml` — site name, nav references
- [ ] 8.16 Update `.github/workflows/` — any references to `agentpool` in CI
- [ ] 8.17 Verify no `agentpool` references remain: `grep -r "agentpool" src/ tests/ site/ *.toml *.yml *.md` returns only this openspec change
- [ ] 8.18 Run `uv sync` — all dependencies resolve with new package names
- [ ] 8.19 Run `uv run pytest` — full test suite passes with new package names
- [ ] 8.20 Run `uv run mypy src/` — type checking passes
- [ ] 8.21 Run `uv run ruff check src/` — linting passes
- [ ] 8.22 Verify `agentwolf --version` CLI command works
- [ ] 8.23 Verify `agentwolf serve-acp config.yml` works with a sample config
