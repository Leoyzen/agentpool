## Why

AgentPool carries significant duplication of pydantic-ai v2 functionality and accumulated technical debt from an incomplete refactor. The orchestrator core (`orchestrator/core.py`, 3014 LOC) conflates EventBus, SessionController, and SessionPool in a single file. `BaseAgent.run_stream()` (~150 LOC of standalone path) duplicates the `RunExecutor` run loop but uses bare `async for`, which silently fails to fire pdai Capability hooks (`wrap_node_run`, `before_model_request`, etc.). The ResourceProvider hierarchy re-implements tool assembly that pdai's `Toolset` already provides. Legacy `Team`/`TeamRun` coexist with `graph:` YAML but no translator connects them. These duplications make the framework harder to maintain, harder to upgrade, and prevent adoption of pdai's native Capability extension mechanism.

## What Changes

### Phase 1: Core Split
- Split `orchestrator/core.py` (3014 LOC) into `event_bus.py`, `session_controller.py`, `session_pool.py`
- Move `EventBus` class to `event_bus.py`, `SessionController` to `session_controller.py`, `SessionPool` to `session_pool.py`
- Re-export from `orchestrator/__init__.py` for backward compatibility within the codebase

### Phase 2: Run Stream Unification
- **BREAKING**: Deprecate `BaseAgent.run_stream()` standalone path (Path B)
- Unify all agent streaming through `RunExecutor` which calls `agent_run.next(node)` explicitly
- Ensure pdai Capability hooks fire on every run path
- Remove `_run_stream_once()` producer/consumer pattern that bypasses `RunExecutor`

### Phase 3: EventBus Backpressure
- Replace `anyio.ObjectSendStream`/`ObjectReceiveStream` with `asyncio.Queue` + explicit overflow policies
- Remove `block` policy from publish path (would deadlock run loop)
- Support configurable overflow: `drop_oldest`, `drop_newest`, `drop_subscriber`

### Phase 4: Team Cleanup
- Build `graph_translation.py` (does NOT exist) to translate `teams:` YAML → `graph:` YAML
- **BREAKING**: Remove legacy `Team` (parallel) and `TeamRun` (sequential) classes
- Remove `TeamConfig.get_team()` factory, route all team config through `GraphConfig`

### Phase 5: ToolProvider Deprecation
- **BREAKING**: Replace `ResourceProvider` hierarchy with thin `ToolsetFactory` protocol
- Deprecate `MCPResourceProvider`, `LocalResourceProvider`, `PoolResourceProvider`, `StaticResourceProvider`, `AggregatingResourceProvider`, `FilteringResourceProvider`
- **BREAKING**: Deprecate `CodeModeResourceProvider` (ToolsetFactory can't express single-meta-tool pattern — replaced by pdai Toolset directly)
- Keep `PlanProvider` as stateful pdai toolset (needs `RunContext.deps`)
- Create `SkillBridgeCapability` as interim skill injection bridge

### Phase 6: Capability Layer
- Implement 6 pdai Capabilities:
  - `LoopDetectionCapability`: prevents infinite agent loops via depth tracking
  - `TokenBudgetCapability`: enforces token budget per run
  - `ToolOutputBudgetCapability`: limits tool output size
  - `DynamicContextCapability`: manages context window expansion
  - `SkillActivationCapability`: supersedes `SkillBridgeCapability` from Phase 5
  - `MemoryCapability`: persistent memory across turns

### Phase 7: Server Modularization
- Fix 4 core→app import violations in `agentpool_server/`
- Enforce architectural boundaries via `import-linter` configuration
- Protocol servers depend on `agentpool` core, never the reverse

### Phase 8: Rename
- **BREAKING**: One-shot rename `agentpool` → `agentwolf` across 10 packages
- Update 1599+ imports, CLI entry points, config schemas, documentation
- `acp` package name stays, but 2 imports from agentpool updated
- No alias period (pre-1.0, no external consumers)

## Capabilities

### New Capabilities
- `thin-wrapper-core-split`: Split orchestrator/core.py into three focused modules (event_bus, session_controller, session_pool) with clear separation of concerns
- `unified-run-stream`: Single run execution path through RunExecutor that fires all pdai Capability hooks, replacing the dual-path BaseAgent.run_stream()/RunExecutor split
- `eventbus-queue-backpressure`: EventBus backed by asyncio.Queue with configurable overflow policies instead of anyio memory streams
- `teams-graph-translation`: Automatic translation from legacy `teams:` YAML syntax to `graph:` syntax, enabling removal of Team/TeamRun
- `toolset-factory`: Thin ToolsetFactory protocol replacing the ResourceProvider hierarchy, aligning with pdai's native Toolset
- `pdai-capabilities`: Six pdai Capability implementations (LoopDetection, TokenBudget, ToolOutputBudget, DynamicContext, SkillActivation, Memory) as composable agent extensions
- `server-boundary-enforcement`: import-linter enforced architectural boundaries preventing core→app import violations
- `agentwolf-rename`: Atomic rename of all agentpool packages, imports, CLI, and configs to agentwolf

### Modified Capabilities
- `session-orchestration`: SessionController and SessionPool requirements change — they are extracted from core.py into dedicated modules with re-exports preserved
- `pending-message-queue`: RunExecutor becomes the sole run loop; `BaseAgent.run_stream()` standalone path removed, ensuring Capability hooks fire on all paths
- `event-coalescing`: EventBus internal transport changes from anyio memory streams to asyncio.Queue, coalescing behavior preserved but backpressure model changes
- `pydantic-graph-teams`: Legacy Team/TeamRun removed; all team execution routes through GraphConfig, requiring the teams→graph translator

## Impact

**Affected code (by phase)**:
- Phase 1: `src/agentpool/orchestrator/core.py` (3014 LOC) → 3 files; 64 callers of `SessionController`, 139 callers of `EventBus`
- Phase 2: `src/agentpool/agents/base_agent.py` `run_stream()` (~150 LOC); 44 callers of `run_stream` across servers, agents, tests
- Phase 3: `EventBus` class (~280 LOC); all protocol servers consuming events
- Phase 4: `src/agentpool/delegation/team.py`, `teamrun.py`, `graph_team.py`; `src/agentpool_config/teams.py`; `TeamRun` has 50 callers
- Phase 5: `src/agentpool/resource_providers/` (8 provider classes); 25 callers of `MCPResourceProvider`, 44 callers of `LocalResourceProvider`
- Phase 6: New `src/agentpool/capabilities/` package; all native agents gain Capability support
- Phase 7: `src/agentpool_server/` 4 import violations; new `import-linter` config
- Phase 8: 10 packages (`agentpool`, `agentpool_config`, `agentpool_server`, `agentpool_toolsets`, `agentpool_storage`, `agentpool_cli`, `agentpool_commands`, `agentpool_prompts`, `acp`, entry points); 1599+ imports

**Dependencies**: `pydantic-ai` (Capability API), `pydantic-graph` (GraphBuilder, Step), `import-linter` (new dev dependency)

**Breaking changes**: 5 major breaking changes (Phases 2, 4, 5, 8 + EventBus API in Phase 3). No alias period — pre-1.0 project with no external consumers.

**Test impact**: Existing tests for `EventBus`, `SessionController`, `Team`/`TeamRun`, `ResourceProvider`, and `BaseAgent.run_stream()` will need updating. ~185+ evidence files in `.omo/evidence/` may need regeneration.
