## 1. Phase 4: Team/TeamRun Removal

- [x] 1.1 Remove `Team` class from `src/agentpool/delegation/team.py` (file deleted)
- [x] 1.2 Remove `TeamRun` class from `src/agentpool/delegation/teamrun.py` (file deleted)
- [x] 1.3 Remove `TeamConfig.get_team()` factory method
- [x] 1.4 `_TeamGraphState` and `_TeamRunGraphState` are active internal implementation details of BaseTeam's graph execution — not legacy code to remove; they ARE the graph-based replacements for old Team/TeamRun classes
- [x] 1.5 Update `AgentPool.__init__` — stop creating `Team`/`TeamRun` instances
- [x] 1.6 Audit all callers of `TeamRun` and `TeamConfig.get_team()` — create migration list
- [x] 1.7 Migrate all callers to `GraphConfig` + `GraphBuilder` (all callers already use BaseTeam with mode parameter; zero direct TeamRun imports remain)
- [x] 1.8 Remove remaining `from agentpool.delegation.team import` / `from agentpool.delegation.teamrun import` statements (zero remaining)
- [x] 1.9 Test translator against all `teams:` YAML configs in `site/examples/` (28 tests pass)
- [x] 1.10 Run `uv run pytest tests/teams/` — team tests updated and passing
- [x] 1.11 Run `uv run pytest tests/delegation/` — delegation tests passing

## 2. Phase 5: ToolsetFactory Migration

- [x] 2.1 Create `ToolsetFactory` protocol + `StaticToolsetFactory` + `AdapterToolsetFactory` in `src/agentpool/tools/factory.py`
- [x] 2.2 Create `MCPToolsetFactory` — wraps MCP server, produces pdai `Toolset` (reconcile with `migrate-to-mcptoolset`)
- [x] 2.3 Create `LocalSkillToolsetFactory` — discovers filesystem skills (reconcile with `refactor-skills-as-capabilities`)
- [x] 2.4 Create `PoolToolsetFactory` — exposes agent/team delegation as subagent tools
- [ ] ~~2.5 Migrate `MCPResourceProvider` callers (25) to `MCPToolsetFactory`~~ *(deferred to follow-up PR — 55 src files + 33 test files reference ResourceProvider; ToolsetFactory implementations are thin wrappers that still delegate to old providers)*
- [ ] ~~2.6 Migrate `LocalResourceProvider` callers (44) to `LocalSkillToolsetFactory`~~ *(deferred to follow-up PR)*
- [ ] ~~2.7 Migrate `PoolResourceProvider` callers (1) to `PoolToolsetFactory`~~ *(deferred to follow-up PR)*
- [ ] ~~2.8 Migrate `PlanProvider` to pdai `Toolset` subclass~~ *(deferred to follow-up PR)*
- [x] 2.9 Add `DeprecationWarning` to `CodeModeResourceProvider.__init__` and `RemoteCodeModeResourceProvider.__init__`
- [ ] ~~2.10 Remove `ResourceProvider` abstract base class~~ *(requires full caller migration first)*
- [ ] ~~2.11 Remove `AggregatingResourceProvider`, `FilteringResourceProvider`, `StaticResourceProvider`~~ *(requires full caller migration first)*
- [ ] ~~2.12 Remove `SkillsInstructionProvider`~~ *(requires full caller migration first)*
- [x] 2.13 Drop task 5.11 `SkillBridgeCapability` — superseded by `SkillActivationCapability` (PR #100)
- [ ] ~~2.14 Run `uv run pytest tests/resource_providers/` — tests updated and passing~~ *(deferred — no test changes in this commit)*
- [ ] ~~2.15 Run `uv run pytest tests/tools/` — tool tests passing~~ *(deferred — no test changes in this commit)*
- [ ] ~~2.16 Run `uv run pytest tests/toolsets/` — toolset tests passing~~ *(deferred — no test changes in this commit)*

## 3. Phase 6: Capability Wiring

- [x] 3.1 Audit `pre_run` hook — compare with `wrap_node_run` Capability hook
- [x] 3.2 Audit `post_run` hook — compare with `after_node_run` Capability hook
- [x] 3.3 Audit `pre_tool_use` hook — compare with `before_tool_execute` / `wrap_tool_execute`
- [x] 3.4 Audit `post_tool_use` hook — compare with `after_tool_execute` Capability hook
- [x] 3.5 Document which hooks migrate to Capabilities and which remain distinct
- [x] 3.6 Add `capabilities:` section to agent config model in `agentpool_config/`
- [x] 3.7 Create config models for each capability (map YAML args to constructor)
- [x] 3.8 Validate capability configs at load time
- [x] 3.9 Update `Agent` class to accept and attach Capabilities from config
- [x] 3.10 Verify Capability hooks fire on standalone run path
- [x] 3.11 Verify Capability hooks fire on graph run path (after Phase 4 Team/TeamRun removal)
- [x] 3.12 Reconcile `SkillActivationCapability` with `refactor-skills-as-capabilities` (SkillCapability)
- [x] 3.13 Reconcile `ToolOutputBudgetCapability` with `unify-tool-interception-to-pydantic-ai-capabilities`
- [x] 3.14 Run `uv run pytest tests/agents/` — agent tests with Capabilities passing
- [x] 3.15 Run `uv run pytest tests/capabilities/` — capability tests still passing

## 4. Phase 7: Server Boundary Fixes

- [x] 4.1 Audit 8 `agentpool_server` → `agentpool_cli`/`agentpool_commands` import violations
- [x] 4.2 Fix each server→cli/commands violation — moved NodeCommand to core, updated 7 ACP command files, used importlib for runtime agentpool_cli import
- [x] 4.3 Remove corresponding `ignore_imports` entries (server→cli violations fully resolved)
- [x] 4.4 Audit 72 `agentpool_config` → `agentpool` import violations — categorized: 42 TYPE_CHECKING, 1 function-level lazy, 5 module-level
- [ ] 4.5 Fix type-reference violations — deferred to #114 (import-linter detects TYPE_CHECKING imports; needs architectural decision)
- [ ] 4.6 Fix runtime-import violations — deferred to #114 (needs shared utils package or factory extraction)
- [ ] 4.7 Remove `allow_indirect_imports = true` from all contracts — deferred to #114
- [x] 4.8 Verify `lint-imports` passes with zero violations (with current ignore_imports in place)
- [x] 4.9 Add `lint-imports` to `.github/workflows/` CI pipeline
- [x] 4.10 Run `uv run lint-imports` — zero violations (with current ignore_imports in place)
- [x] 4.11 Run `uv run pytest` — full test suite passes (CI green)

## 5. Phase 8: Rename Execution

- [x] 5.1 Verify all Phase 4-7 follow-up changes are merged
- [ ] 5.2 Verify clean working tree
- [ ] 5.3 Run `python scripts/rename_to_agentwolf.py` (no --dry-run)
- [ ] 5.4 Commit as single atomic commit: `refactor: rename agentpool to agentwolf`
- [ ] 5.5 Run `uv sync` — all dependencies resolve with new package names
- [ ] 5.6 Run `uv run pytest` — full test suite passes with new package names
- [ ] 5.7 Run `uv run mypy src/` — type checking passes
- [ ] 5.8 Run `uv run ruff check src/` — linting passes
- [ ] 5.9 Verify `agentwolf --version` CLI command works
- [ ] 5.10 Verify `agentwolf serve-acp config.yml` works with a sample config
- [ ] 5.11 Verify no `agentpool` references remain (except openspec/changes/ historical artifacts)

> **Phase 8 is BLOCKED** — deferred by user ("phase8先不做"). The rename script
> (`scripts/rename_to_agentwolf.py`) is ready but execution is an irreversible
> operation affecting 452+ files. Requires explicit user confirmation to proceed.
