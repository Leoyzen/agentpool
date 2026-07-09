## 1. HostContext Dataclass

- [ ] 1.1 Create `src/agentpool/host/__init__.py` with public exports
- [ ] 1.2 Create `src/agentpool/host/context.py` defining `HostContext` as `@dataclass(frozen=True)` with fields: `mcp: MCPManager`, `storage: StorageManager`, `skills_registry: SkillsManager`, `capability_cache: CapabilityCache`, `prompt_manager: PromptManager`, `model_registry: ModelRegistry`, `model_cache: ModelCache`, `config_id: str = "default"`, `tenant_id: str = "default"`
- [ ] 1.3 Create stub `ModelRegistry` and `ModelCache` classes (passthrough to existing model resolution — full implementation deferred to M4)
- [ ] 1.4 Add `AgentPool.get_context() -> HostContext` method that constructs HostContext from pool's existing infrastructure fields
- [ ] 1.5 Write unit tests for HostContext: immutability (FrozenInstanceError), field types, default config_id/tenant_id, construction from AgentPool

## 2. AgentRegistry

- [ ] 2.1 Create `src/agentpool/host/registry.py` defining `AgentRegistry` as a wrapper around `dict[str, MessageNode]` with `get(name)`, `list_names()`, `exists(name)` methods
- [ ] 2.2 Write unit tests for AgentRegistry: lookup, non-existent key, list names

## 3. AgentFactory Extraction

- [ ] 3.1 Create `src/agentpool/host/factory.py` defining `AgentFactory` class with `compile(manifest, host_context) -> AgentRegistry` and `recompile(new_manifest, host_context) -> AgentRegistry` methods
- [ ] 3.2 Move agent instantiation logic from `AgentPool._create_agents()` (or equivalent) into `AgentFactory.compile()` — includes: model resolution, tool/capability injection, team compilation, connection setup, skill loading
- [ ] 3.3 Implement `AgentFactory.recompile()` with diff-based logic: compare old vs new manifest, only recreate agents whose config section changed, preserve unchanged agents from cache
- [ ] 3.4 Add internal compilation cache (`_last_manifest`, `_last_registry`) to AgentFactory for diff comparison
- [ ] 3.5 Write unit tests for AgentFactory: compile produces correct agents, recompile only recreates changed agents, factory does not start infrastructure

## 4. AgentPool Facade

- [ ] 4.1 Modify `AgentPool.__init__()` to create an `AgentFactory` instance and store it as `self._factory`
- [ ] 4.2 Modify `AgentPool.get_agent()` to delegate to `self._factory.compile()` (or retrieve from cached registry) instead of containing instantiation logic
- [ ] 4.3 Modify `AgentPool.get_team()` to delegate to factory registry
- [ ] 4.4 Ensure `AgentPool.agents` property returns from factory registry, not from internal dict
- [ ] 4.5 Remove agent instantiation code from AgentPool (moved to AgentFactory) — keep only infrastructure lifecycle (MCP start/stop, storage init, skills discovery)
- [ ] 4.6 Verify `AgentPool.manifest`, `AgentPool.storage`, `AgentPool.mcp` properties still work (delegate to internal fields, unchanged)

## 5. Compatibility Shim

- [ ] 5.1 Verify `MessageNode.agent_pool` property still works — it returns the AgentPool facade which exposes the same fields as HostContext
- [ ] 5.2 Ensure no deprecation warnings are emitted in M1 (warnings added in M1b)
- [ ] 5.3 Document in code comments that `agent_pool` is a compatibility shim and `HostContext` is the preferred access path

## 6. Integration Verification

- [ ] 6.1 Run full test suite: `uv run pytest` — all tests must pass without modification
- [ ] 6.2 Run mypy: `uv run --no-group docs mypy src/agentpool/host/` — no type errors
- [ ] 6.3 Run ruff: `uv run ruff check src/agentpool/host/` — no lint errors
- [ ] 6.4 Verify example configs: `agentpool run assistant "Hello"` works with existing YAML configs
- [ ] 6.5 Verify ACP server: `agentpool serve-acp config.yml` starts and handles requests
- [ ] 6.6 Verify AgentFactory can be used standalone: `factory = AgentFactory(); registry = await factory.compile(manifest, host_context)` without AgentPool
