## 1. SkillService Protocol + HostContext Extension (Foundation)

- [ ] 1.1 Create `src/agentpool/capabilities/skill_service.py` defining `SkillService` as `@runtime_checkable Protocol` with methods matching AgentPool's existing names exactly: `skill_capabilities` (property), `skill_provider` (property), `skill_commands` (property), `is_skill_visible_to_node(skill, node_name) -> bool`, `get_skill_instructions_for_node(skill_name, node_name) -> str` (async). Use TYPE_CHECKING for imports. Exclude write operations.
- [ ] 1.2 Add `main_agent_name: str | None = None` and `skill_service: SkillService | None = None` fields to `HostContext` in `src/agentpool/host/context.py`. Update `AgentPool.get_context()` in `src/agentpool/delegation/pool.py` to pass `skill_service=self` and `main_agent_name=self.main_agent_name`.
- [ ] 1.3 Add `_bind_pool(self, pool: AgentPool[Any] | None) -> None` method to `MessageNode` in `src/agentpool/messaging/messagenode.py`. Body: `self._agent_pool = pool`. Docstring: "Internal: bind node to pool for host_context access. Used by Talk wiring."
- [ ] 1.4 Write unit tests: `isinstance(pool, SkillService)` returns True after pool init, all five methods accessible, HostContext constructed with skill_service and main_agent_name defaults to None, `_bind_pool()` sets internal field correctly.

## 2. Core Agent Migration

- [ ] 2.1 Migrate `src/agentpool/agents/native_agent/agent.py` (3 refs): Replace `self.agent_pool` skill capability accesses with `self.host_context.skill_service.X` (guard: `if ctx.skill_service is not None`). Lines ~915-934.
- [ ] 2.2 Migrate `src/agentpool/delegation/base_team.py` (3 refs): Replace `self.agent_pool.skill_provider` and `self.agent_pool.get_skill_instructions_for_node` with `self.host_context.skill_service.X` (guard: `if ctx.skill_service is not None`). Lines ~789-795, ~411.
- [ ] 2.3 Write/verify tests: `grep -n 'self\.agent_pool' src/agentpool/agents/native_agent/agent.py src/agentpool/delegation/base_team.py` returns 0 results (excluding `self._agent_pool`). `uv run pytest -k "native_agent or team" -x` passes.

## 3. ACP Server Migration

- [ ] 3.1 Migrate `ACPProtocolHandler` in `src/agentpool_server/acp_server/handler.py` (7 refs): Change constructor param from `agent_pool: AgentPool` to `host_context: HostContext`. Store as `self._host_context`. Replace all `self.agent_pool.session_pool` with `self._host_context.session_pool`. Update caller in `acp_agent.py` to pass `host_context=self.host_context`.
- [ ] 3.2 Migrate `AgentPoolACPAgent` in `src/agentpool_server/acp_server/acp_agent.py` (~28 refs): Replace all `self.agent_pool.X` with `self.host_context.X` (manifest, main_agent_name, session_pool, skills). Replace `agent.agent_pool` on other objects with `agent.host_context`. Use `ctx = self.host_context` local var pattern.
- [ ] 3.3 Migrate `ACPSession` in `src/agentpool_server/acp_server/session.py` (~11 refs): Rename `agent_pool` property to `host_context` (returns `self.agent.host_context`). Replace all `self.agent_pool.X` with `self.host_context.X`. Replace `getattr(self.agent, "agent_pool", None)` with `self.agent.host_context`.
- [ ] 3.4 Migrate `src/agentpool_server/acp_server/commands/debug_commands.py` (1 ref): Replace `session.agent_pool.manifest.agents` with `session.host_context.manifest.agents`.
- [ ] 3.5 Write/verify tests: `grep -rn 'self\.agent_pool\b' src/agentpool_server/acp_server/ --include='*.py'` returns 0 results. `uv run pytest tests/agentpool_server/acp_server/ -x` passes. ACP snapshot tests pass (`uv run pytest -m acp_snapshot -x`).

## 4. OpenCode Server + Misc Migration

- [ ] 4.1 Migrate `src/agentpool_server/opencode_server/state.py` (1 ref): `self.agent.agent_pool` → `self.agent.host_context`.
- [ ] 4.2 Migrate `src/agentpool_server/opencode_server/server.py` (3 refs): `agent.agent_pool` → `agent.host_context`; `agent.agent_pool.session_pool` → `ctx.session_pool`.
- [ ] 4.3 Migrate `src/agentpool_server/opencode_server/routes/session_routes.py` (2 refs): `agent.agent_pool` → `agent.host_context`; `agent.agent_pool.compaction_pipeline` → `agent.host_context.manifest.get_compaction_pipeline()`.
- [ ] 4.4 Migrate `src/agentpool_server/opencode_server/routes/agent_routes.py` (2 refs): `state.agent.agent_pool` → `state.agent.host_context`.
- [ ] 4.5 Migrate `src/agentpool_commands/utils.py` (2 refs) and `src/agentpool_server/shared/model_utils.py` (1 ref): Replace `agent.agent_pool` with `agent.host_context`.
- [ ] 4.6 Write/verify tests: `grep -rn '\.agent_pool\b' src/agentpool_server/ src/agentpool_commands/ --include='*.py' | grep -v __pycache__` returns 0 results. `uv run pytest tests/agentpool_server/ -x` passes.

## 5. Backdoor Removal — HostContext.pool + Factory + Talk

- [ ] 5.1 Migrate `src/agentpool/host/factory.py` (3 refs): Replace `host_context.pool` at lines 419, 509, 567 with `self._pool` (already exists at line 93).
- [ ] 5.2 Migrate `src/agentpool/talk/talk.py` (2 refs): Replace `ctx.pool` wiring at lines 162-163 and 508-509 with `source._agent_pool` + `other._bind_pool(pool)` pattern.
- [ ] 5.3 Remove `pool: AgentPool[Any] | None = None` field from `HostContext` in `src/agentpool/host/context.py` (line 84). Remove `pool=self` from `AgentPool.get_context()` in `src/agentpool/delegation/pool.py` (line 256). Remove AgentPool TYPE_CHECKING import from context.py if no longer needed.
- [ ] 5.4 Write/verify tests: `grep -rn 'host_context\.pool\|ctx\.pool' src/agentpool/host/factory.py src/agentpool/talk/talk.py` returns 0 results. `grep -n 'pool' src/agentpool/host/context.py | grep -v '#' | grep -v 'skill_pool\|pool_'` returns 0 results for the field. `uv run pytest tests/host/ -x` passes. `uv run python -c "from agentpool.host.context import HostContext; assert 'pool' not in HostContext.__dataclass_fields__"` succeeds.

## 6. Documentation + Final Verification

- [ ] 6.1 Update `AGENTS.md`: Remove "agent_pool deprecated for host_context" warning section (the "18 references remain" note is stale). Update anti-patterns section. Add `SkillService` to capabilities table. Note `HostContext.pool` removed.
- [ ] 6.2 Run full test suite: `uv run pytest -x` — all tests must pass.
- [ ] 6.3 Run code quality: `uv run ruff check src/` and `uv run --no-group docs mypy src/` — no errors.
- [ ] 6.4 Run DeprecationWarning clean check: `uv run pytest -x -W error::DeprecationWarning` on key suites — zero warnings from migrated source code (covers M2 T11.4/T12.9).
- [ ] 6.5 Verify scope fidelity: `grep -rn '\.agent_pool\b' src/ --include='*.py' | grep -v _agent_pool | grep -v 'agent_pool='` returns 0 AND `grep -rn 'host_context\.pool' src/` returns 0.
- [ ] 6.6 Verify manual QA: `agentpool run assistant "Hello"` works, `agentpool serve-acp config.yml` starts and handles requests, AgentFactory standalone works without AgentPool (covers M1 T6.1/T6.4/T6.5/T6.6).

## 7. Optional Property Removal (can defer to M4)

- [ ] 7.1 Remove `agent_pool` property getter (lines 140-155) and setter (lines 157-159) from `MessageNode` in `src/agentpool/messaging/messagenode.py`. Update `storage` property (line 257) to go through `host_context`. Migrate 2 internal property SETs at lines 431 and 441 to `_bind_pool()`.
- [ ] 7.2 Migrate test files: `grep -rn '\.agent_pool\b' tests/ --include='*.py'` — replace read accesses with `.host_context`, setter accesses with `._bind_pool()` or `._agent_pool =`.
- [ ] 7.3 Verify: `grep -rn 'def agent_pool' src/agentpool/messaging/messagenode.py` returns 0. `uv run pytest -x` passes.
