## 1. AdapterToolsetFactory Bridge + Foundation Types

- [x] 1.1 Create `src/agentpool/capabilities/change_event.py` defining `ChangeEvent` as `@dataclass(frozen=True, slots=True)` with fields: `capability_name: str`, `kind: Literal["tools_changed", "prompts_changed", "resources_changed", "skills_changed"] = "tools_changed"`
- [x] 1.2 Create `src/agentpool/capabilities/adapter.py` defining `AdapterToolsetFactory(AbstractCapability)` that wraps any `ResourceProvider` as a pydantic-ai Capability — implements `get_toolset()` returning the provider's tools, `get_instructions()` delegating to provider, `on_change()` bridging provider signals to `ChangeEvent` yields, `__aenter__`/`__aexit__` delegating lifecycle
- [x] 1.3 Write unit tests in `tests/capabilities/test_adapter.py`: adapter wraps `StaticResourceProvider` and exposes same tools, adapter bridges change signals, adapter lifecycle delegation, `on_change()` yields correct `ChangeEvent` kind
- [x] 1.4 Write unit tests for `ChangeEvent`: immutability (FrozenInstanceError), field types, default kind value

## 2. MCPToolset + MCPCapability (replaces MCPResourceProvider)

- [x] 2.1 Create `src/agentpool/capabilities/mcp_capability.py` defining `MCPCapability(AbstractCapability)` wrapping a single MCP server connection — implements `get_toolset()` returning a `pydantic_ai.mcp.MCPToolset`, `on_change()` subscribing to `notifications/tools/list_changed` and yielding `ChangeEvent(kind="tools_changed")`
- [x] 2.2 Implement `ResourceSource` protocol on `MCPCapability`: `list()` calls `resources/list` returning `Resource` with `mcp://{server_name}/{path}` URI scheme, `read(uri)` strips prefix and calls `resources/read`, `exists(uri)` checks via `list()`
- [x] 2.3 Implement `MCPCapability.__aenter__`/`__aexit__` managing MCP server connection lifecycle
- [x] 2.4 Write unit tests in `tests/capabilities/test_mcp_capability.py`: toolset contains MCP tools, `list()` returns resources with correct URI scheme, `read()` returns content, `isinstance(cap, ResourceSource)` is `True`, `isinstance(cap, AbstractCapability)` is `True`
- [x] 2.5 Wire `MCPCapability` into `AgentFactory.compile()` — when agent config references MCP servers, create `MCPCapability` from `HostContext.mcp` instead of `MCPResourceProvider`
- [x] 2.6 Write integration test: agent compiled with `MCPCapability` has MCP tools and resources; verify old `MCPResourceProvider` still works via `AdapterToolsetFactory` for unmigrated agents

## 3. FunctionToolset (replaces StaticResourceProvider)

- [x] 3.1 Create `src/agentpool/capabilities/function_toolset.py` defining `FunctionToolsetCapability(AbstractCapability)` wrapping a list of `Tool` instances — implements `get_toolset()` returning `pydantic_ai.toolsets.FunctionToolset`, `get_instructions()` returning optional instructions, `on_change()` returning `None`
- [x] 3.2 Write unit tests in `tests/capabilities/test_function_toolset.py`: toolset contains correct tools, instructions returned correctly, `on_change()` returns `None`
- [x] 3.3 Wire `FunctionToolsetCapability` into `AgentFactory.compile()` — when agent config has inline `@tool` callables or static tool providers, create `FunctionToolsetCapability` instead of `StaticResourceProvider`
- [x] 3.4 Migrate key consumers: update `src/agentpool_toolsets/builtin/debug.py`, `src/agentpool_toolsets/builtin/skills.py`, `src/agentpool_toolsets/config_creation.py` to subclass `FunctionToolsetCapability` instead of `StaticResourceProvider`
- [x] 3.5 Write integration test: agent compiled with `FunctionToolsetCapability` works identically to agent compiled with `StaticResourceProvider`; verify old provider still works via adapter

## 4. FilteredToolset (replaces FilteringResourceProvider)

- [x] 4.1 Create `src/agentpool/capabilities/filtered_toolset.py` defining `FilteredToolsetCapability(AbstractCapability)` wrapping another `AbstractCapability` with a tool filter — `get_toolset()` returns `FilteredToolset`, `on_change()` delegates to wrapped capability, lifecycle delegates
- [x] 4.2 Write unit tests in `tests/capabilities/test_filtered_toolset.py`: filtered toolset excludes disallowed tools, includes allowed tools, `on_change()` delegated, lifecycle delegation
- [x] 4.3 Wire `FilteredToolsetCapability` into `AgentFactory.compile()` — when agent config has tool filters, create `FilteredToolsetCapability` wrapping the base capability instead of `FilteringResourceProvider`
- [x] 4.4 Write integration test: agent compiled with `FilteredToolsetCapability` only exposes allowed tools; verify old `FilteringResourceProvider` still works via adapter

## 5. CombinedToolset (replaces AggregatingResourceProvider)

- [x] 5.1 Create `src/agentpool/capabilities/combined_toolset.py` defining `CombinedToolsetCapability(AbstractCapability)` composing multiple `AbstractCapability` instances — `get_toolset()` returns unified toolset, `get_instructions()` concatenates non-None instructions, `on_change()` merges streams, `__aenter__`/`__aexit__` enters/exits all children
- [x] 5.2 Write unit tests in `tests/capabilities/test_combined_toolset.py`: merged toolsets, concatenated instructions, merged `on_change` streams, empty children list
- [x] 5.3 Wire `CombinedToolsetCapability` into `AgentFactory.compile()` — when multiple capabilities are present, wrap in `CombinedToolsetCapability` instead of `AggregatingResourceProvider`
- [x] 5.4 Write integration test: agent compiled with `CombinedToolsetCapability` has access to all tools from all capabilities; verify old `AggregatingResourceProvider` still works via adapter

## 6. SubagentCapability + SubagentToolset (replaces PoolResourceProvider)

- [x] 6.1 Create `src/agentpool/capabilities/subagent_capability.py` defining `SubagentCapability(AbstractCapability)` with `SubagentToolset` exposing a `spawn_subagent(name: str, prompt: str)` tool — tool delegates to `ctx.deps.delegation.spawn_subagent()` at runtime, NOT to a direct `AgentPool` reference; `on_change()` returns `None`, lifecycle is no-op
- [x] 6.2 Write unit tests in `tests/capabilities/test_subagent_capability.py`: toolset exposes `spawn_subagent` tool, tool calls `DelegationService` mock, no `AgentPool` reference passed, `get_available_agents` returns registry names
- [x] 6.3 Wire `SubagentCapability` into `AgentFactory.compile()` — when agent config includes `subagent` tool type, create `SubagentCapability` instead of `PoolResourceProvider`
- [x] 6.4 Write integration test: agent compiled with `SubagentCapability` can spawn subagents via `DelegationService`; verify old `PoolResourceProvider` still works via adapter

## 7. CodeModeCapability (replaces CodeModeResourceProvider)

- [x] 7.1 Create `src/agentpool/capabilities/code_mode_capability.py` defining `CodeModeCapability(AbstractCapability)` wrapping all agent tools into a single `execute_code` meta-tool accepting Python code — `get_toolset()` returns single-tool toolset, `get_instructions()` returns code mode prompt, `on_change()` returns `None`
- [x] 7.2 Write unit tests in `tests/capabilities/test_code_mode_capability.py`: single `execute_code` tool exposed, code mode instructions returned, inner tools callable via meta-tool
- [x] 7.3 Wire `CodeModeCapability` into `AgentFactory.compile()` — when agent config enables code mode, create `CodeModeCapability` instead of `CodeModeResourceProvider`
- [x] 7.4 Write integration test: agent compiled with `CodeModeCapability` wraps all tools into meta-tool; verify old `CodeModeResourceProvider` still works via adapter

## 8. SkillCapability ResourceSource Supplement

- [x] 8.1 Add `ResourceSource` protocol implementation to existing `SkillCapability` in `src/agentpool/skills/capability.py` — `list()` returns SKILL.md files as `Resource` with `skill://{skill_name}` URI scheme and `mime_type="text/markdown"`, `read(uri)` returns content, `exists(uri)` checks registry, `on_change()` yields `ResourceChange` on skill add/remove
- [x] 8.2 Write unit tests in `tests/capabilities/test_skill_resource_source.py`: `list()` returns skills with correct URI scheme, `read()` returns markdown content, `exists()` returns correct bool, `isinstance(cap, ResourceSource)` is `True`
- [x] 8.3 Wire `SkillCapability` into `AgentFactory.compile()` — verify it replaces `LocalResourceProvider` and is collected as a `ResourceSource` by the factory
- [x] 8.4 Write integration test: agent with skills has SKILL.md content accessible via `ResourceSource.read("skill://...")`; verify old `LocalResourceProvider` still works via adapter

## 9. ResourceSource Protocol + AggregatedResourceSource

- [x] 9.1 Create `src/agentpool/capabilities/resource_source.py` defining `ResourceSource` as a `@runtime_checkable Protocol` with `list() -> list[Resource]`, `read(uri: str) -> ResourceContent`, `exists(uri: str) -> bool`, `on_change() -> AsyncIterator[ResourceChange] | None`
- [x] 9.2 Define `Resource` and `ResourceContent` as `@dataclass(frozen=True, slots=True)`, `ResourceChange` as `@dataclass(frozen=True, slots=True)`, and `ResourceNotFoundError(Exception)`
- [x] 9.3 Create `AggregatedResourceSource` composing multiple `ResourceSource` instances — `list()` merges all sources, `read(uri)` routes by URI scheme, `exists(uri)` checks all sources, `on_change()` merges streams
- [x] 9.4 Write unit tests for `Resource`/`ResourceContent` immutability, `ResourceSource` protocol isinstance checks, `ResourceNotFoundError` subclass, and `AggregatedResourceSource` merged list, routed read, unknown URI raises, exists checks all sources

## 10. AgentContext + DelegationService

- [x] 10.1 Create `src/agentpool/capabilities/agent_context.py` defining `AgentContext` as `@dataclass(frozen=True, slots=True)` with fields: `agent_registry: AgentRegistry`, `delegation: DelegationService`, `session: SessionState`, `scope: RunScope`, `resources: ResourceSource | None = None`, `host: HostContext`
- [x] 10.2 Create stub `RunScope` as `@dataclass(frozen=True)` in `src/agentpool/host/context.py` (if not already from M1) with fields: `config_id: str = "default"`, `tenant_id: str = "default"`, `user_id: str = "anonymous"`, `session_id: str = ""` (non-Optional with defaults matching M4's RunScope definition; `session_id` will be auto-generated by RunLoop, empty string as placeholder)
- [x] 10.3 Create `src/agentpool/capabilities/delegation.py` defining `DelegationService` Protocol with `spawn_subagent(name: str, prompt: str) -> AsyncIterator[Any]` and `get_available_agents() -> list[str]`, and `AgentNotFoundError(Exception)` for scope-isolated spawning rejection — RunLoop integration is implemented in task group 15
- [x] 10.4 Write unit tests for `AgentContext` (immutability, all six fields accessible, `resources` defaults to `None`, mypy --strict passes) and `DelegationService` protocol (`spawn_subagent` with valid agent, `AgentNotFoundError` for unknown, `get_available_agents` returns in-scope agents, RunLoop internals not accessible)

## 11. AgentFactory Capability Wiring + Hot-Swap

- [x] 11.1 Modify `AgentFactory.compile()` to produce agents with `list[AbstractCapability]` instead of `list[ResourceProvider]` — create native capabilities based on agent config (MCP servers, skills, tools, subagent, code mode)
- [x] 11.2 Implement `ResourceSource` collection — iterate compiled capabilities, collect `isinstance(cap, ResourceSource)` instances, construct `AggregatedResourceSource` at compile time
- [x] 11.3 Implement `on_change()` subscription — for each capability with non-None `on_change()`, start background task listening for `ChangeEvent`; on event, perform local hot-swap replacing only the affected agent's capability
- [x] 11.4 Implement adapter fallback — if config references an unmigrated `ResourceProvider`, wrap it in `AdapterToolsetFactory` transparently
- [x] 11.5 Modify `AgentPool` to remove `ResourceProvider` lifecycle management — no creation, initialization, or cleanup; infrastructure owned by `HostContext`, capabilities by `AgentFactory`
- [x] 11.6 Write unit tests and integration test: factory produces agents with native capabilities (no `ResourceProvider` attached), `AggregatedResourceSource` constructed, `on_change()` triggers hot-swap, adapter fallback for unmigrated providers, mixed capabilities (native + adapter) function correctly

## 12. Entry-Point Registration

- [x] 12.1 Add `agentpool.capabilities` entry-point group to `pyproject.toml` under `[project.entry-points."agentpool.capabilities"]`
- [x] 12.2 Create `src/agentpool/capabilities/registry.py` defining `discover_entry_point_capabilities() -> dict[str, type[AbstractCapability]]` loading from `importlib.metadata.entry_points(group="agentpool.capabilities")`
- [x] 12.3 Define `CapabilityNotFoundError(Exception)` — raised when YAML `type:` references unknown capability; error message lists all available types
- [x] 12.4 Modify `AgentFactory.compile()` to consult discovered entry-point capabilities when resolving YAML `type:` references
- [x] 12.5 Write unit tests: entry-point discovery returns correct mapping, unknown type raises `CapabilityNotFoundError` with available types listed, entry-point capability wired into agent

## 13. Physical Deletion of ResourceProvider Code

- [x] 13.1 Delete all `ResourceProvider` implementation files: `base.py`, `mcp_provider.py`, `static.py`, `filtering.py`, `aggregating.py`, `pool.py`, `local.py`, `plan_provider.py`, `instruction_provider.py`, `skills_instruction.py`, `resource_info.py`, and `codemode/` directory
- [x] 13.2 Delete `src/agentpool/resource_providers/__init__.py` and the entire `resource_providers/` directory
- [x] 13.3 Delete `src/agentpool/capabilities/adapter.py` (`AdapterToolsetFactory` no longer needed)
- [x] 13.4 Update `src/agentpool_config/toolsets.py` — remove `ResourceProvider` type checking and imports, replace with `AbstractCapability` references
- [x] 13.5 Update all remaining consumers across `src/` and `tests/` — remove `ResourceProvider` imports, replace with native `AbstractCapability` references, update test mocks, remove `as_capability()` method from any remaining code
- [x] 13.6 Verify zero `ResourceProvider` references in `src/` and `tests/` via grep, and `resource_providers/` directory does not exist

## 15. RunLoop Integration

- [x] 15.1 Modify RunLoop._run_loop() to construct AgentContext per Turn from: agent_registry (from HostContext), delegation (DelegationService implementation), session (SessionState from RunLoop), scope (RunScope), resources (AggregatedResourceSource from AgentFactory), host (HostContext)
- [x] 15.2 Implement DelegationService in RunLoop: spawn_subagent(name, prompt) routes through SessionController.receive_request() with new session, get_available_agents() returns agent_registry.list_names()
- [x] 15.3 Modify Turn.execute() to accept AgentContext and inject it into pydantic-ai RunContext as deps
- [x] 15.4 Write integration tests: AgentContext constructed per turn with correct fields, DelegationService.spawn_subagent creates new session, RunContext.deps contains AgentContext
- [x] 15.5 Verify existing agent behavior unchanged when AgentContext is injected (no tool breakage)

## 16. Integration Verification

- [x] 16.1 Run full test suite: `uv run pytest` — all tests must pass without modification
- [x] 16.2 Run mypy: `uv run --no-group docs mypy src/agentpool/capabilities/` — no type errors
- [x] 16.3 Run ruff: `uv run ruff check src/agentpool/capabilities/ && uv run ruff format --check src/agentpool/capabilities/` — no lint or formatting errors
- [x] 16.4 Verify example configs: `agentpool run assistant "Hello"` works with existing YAML configs using native capabilities
- [x] 16.5 Verify ACP server: `agentpool serve-acp config.yml` starts and handles requests with native capabilities
- [x] 16.6 Verify MCP tools and subagent delegation end-to-end: agent with MCP servers has tools/resources without `as_capability()` adapter; agent with `subagent` tool spawns subagents via `DelegationService`
- [x] 16.7 Verify skill injection: agent with skills has SKILL.md content accessible via `ResourceSource.read("skill://...")`
- [x] 16.8 Verify `on_change()` hot-swap: modify MCP server tool list, verify `AgentFactory` performs local capability replacement without affecting other agents
- [x] 16.9 Verify entry-point discovery: a mock third-party package with `agentpool.capabilities` entry point is loaded and usable via YAML `type:` reference
- [x] 16.10 Verify zero `ResourceProvider` references: `grep -r "ResourceProvider" src/ tests/` returns no matches and `resource_providers/` directory is deleted
