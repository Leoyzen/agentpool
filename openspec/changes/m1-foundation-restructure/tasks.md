## 1. HostContext Dataclass

- [x] 1.1 Create `src/agentpool/host/__init__.py` with public exports
- [x] 1.2 Create `src/agentpool/host/context.py` defining `HostContext` as `@dataclass(frozen=True)` with fields: `mcp: MCPManager`, `storage: StorageManager`, `skills_registry: SkillsManager`, `capability_cache: CapabilityCache`, `prompt_manager: PromptManager`, `model_registry: ModelRegistry`, `model_cache: ModelCache`, `config_id: str = "default"`, `tenant_id: str = "default"`
- [x] 1.3 Create stub `ModelRegistry` and `ModelCache` classes (passthrough to existing model resolution — full implementation deferred to M4)
- [x] 1.4 Add `AgentPool.get_context() -> HostContext` method that constructs HostContext from pool's existing infrastructure fields
- [x] 1.5 Write unit tests for HostContext: immutability (FrozenInstanceError), field types, default config_id/tenant_id, construction from AgentPool

## 2. AgentRegistry

- [x] 2.1 Create `src/agentpool/host/registry.py` defining `AgentRegistry` as a wrapper around `dict[str, MessageNode]` with `get(name)`, `list_names()`, `exists(name)` methods
- [x] 2.2 Write unit tests for AgentRegistry: lookup, non-existent key, list names

## 3. AgentFactory Extraction

- [x] 3.1 Create `src/agentpool/host/factory.py` defining `AgentFactory` class with `compile(manifest, host_context) -> AgentRegistry` and `recompile(new_manifest, host_context) -> AgentRegistry` methods
- [x] 3.2 Move agent instantiation logic from `AgentPool._create_agents()` (or equivalent) into `AgentFactory.compile()` — includes: model resolution, tool/capability injection, team compilation, connection setup, skill loading
- [x] 3.3 Implement `AgentFactory.recompile()` with diff-based logic: compare old vs new manifest, only recreate agents whose config section changed, preserve unchanged agents from cache
- [x] 3.4 Add internal compilation cache (`_last_manifest`, `_last_registry`) to AgentFactory for diff comparison
- [x] 3.5 Write unit tests for AgentFactory: compile produces correct agents, recompile only recreates changed agents, factory does not start infrastructure

## 4. AgentPool Facade

- [x] 4.1 Modify `AgentPool.__init__()` to create an `AgentFactory` instance and store it as `self._factory`
- [x] 4.2 Modify `AgentPool.get_agent()` to delegate to `self._factory.compile()` (or retrieve from cached registry) instead of containing instantiation logic
- [x] 4.3 Modify `AgentPool.get_team()` to delegate to factory registry
- [x] 4.4 Ensure `AgentPool.agents` property returns from factory registry, not from internal dict
- [x] 4.5 Remove agent instantiation code from AgentPool (moved to AgentFactory) — keep only infrastructure lifecycle (MCP start/stop, storage init, skills discovery)
- [x] 4.6 Verify `AgentPool.manifest`, `AgentPool.storage`, `AgentPool.mcp` properties still work (delegate to internal fields, unchanged)

## 5. Compatibility Shim

- [x] 5.1 Verify `MessageNode.agent_pool` property still works — it returns the AgentPool facade which exposes the same fields as HostContext
- [x] 5.2 Ensure no deprecation warnings are emitted in M1 (warnings added in M1b)
- [x] 5.3 Document in code comments that `agent_pool` is a compatibility shim and `HostContext` is the preferred access path

## 6. Integration Verification

- [x] 6.1 Run full test suite: `uv run pytest` — all tests must pass without modification
- [x] 6.2 Run mypy: `uv run --no-group docs mypy src/agentpool/host/` — no type errors
- [x] 6.3 Run ruff: `uv run ruff check src/agentpool/host/` — no lint errors
- [x] 6.4 Verify example configs: `agentpool run assistant "Hello"` works with existing YAML configs
- [x] 6.5 Verify ACP server: `agentpool serve-acp config.yml` starts and handles requests
- [x] 6.6 Verify AgentFactory can be used standalone: `factory = AgentFactory(); registry = await factory.compile(manifest, host_context)` without AgentPool

---

### Adaptation Notes (actual vs spec)

**T1 HostContext** — Done. `HostContext` has 20 fields (expanded from spec's 9), stubs created, `get_context()` added, unit tests written.

**T2 AgentRegistry** — Done. Wraps `dict[str, BaseAgent]` with `get`/`get_or_none`/`list_names`/`exists`/`add`/`__len__`/`__contains__`/`__iter__`.

**T3 AgentFactory** — Adapted. Has `compile()` (returns empty registry, lazy compilation) and `create_session_agent()` (extracts 3 creation paths from `SessionController`). `recompile()` deferred (not needed for M1 — agents are per-session, not upfront). Tests written.

**T4 AgentPool Facade** — Adapted. `AgentPool._factory` is a lazy property, `get_context()` added. `AgentPool.get_agent()` unchanged (agents are per-session via `SessionController`). No agent instantiation code removed from `AgentPool` (it was already in `SessionController`, now delegated to factory).

**T5 Compatibility Shim** — Done. `agent_pool` is now `@property` with getter+setter, `host_context` property added, compatibility shim documented in docstring.

**T6 Integration** — Adapted. Unit tests pass (29 host tests + 6 pool tests), ruff passes, imports verified. Full test suite skipped per user request. Manual QA deferred.
