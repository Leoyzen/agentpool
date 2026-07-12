## 1. Foundation — HostContext extension + _bind_pool

- [ ] 1.1 Add `main_agent_name: str | None = None` field to `HostContext` in `src/agentpool/host/context.py`. Update `AgentPool.get_context()` in `src/agentpool/delegation/pool.py` to pass `main_agent_name=self.main_agent_name`.
- [ ] 1.2 Add `_bind_pool(self, pool: AgentPool[Any] | None) -> None` method to `MessageNode` in `src/agentpool/messaging/messagenode.py`. Body: `self._agent_pool = pool`.
- [ ] 1.3 Write unit tests: HostContext constructed with main_agent_name defaults to None, `_bind_pool()` sets internal field correctly and `host_context` returns non-None after binding.

## 2. Core Agent Migration

- [ ] 2.1 Migrate `src/agentpool/agents/native_agent/agent.py` (3 refs): Replace `self.agent_pool` skill capability accesses with `self.host_context.pool.X` (interim pattern — will be replaced by `host_context.skill_service.X` in skill-service change). Lines ~915-934.
- [ ] 2.2 Migrate `src/agentpool/delegation/base_team.py` (3 refs): Replace `self.agent_pool.skill_provider` and `self.agent_pool.get_skill_instructions_for_node` with `self.host_context.pool.X`. Lines ~789-795, ~411.
- [ ] 2.3 Migrate `src/agentpool_commands/utils.py` (2 refs): `agent.agent_pool` → `agent.host_context`; `agent.agent_pool.manifest.config_file_path` → `agent.host_context.config_file_path`.
- [ ] 2.4 Migrate `src/agentpool_server/shared/model_utils.py` (1 ref): `agent.agent_pool` → `agent.host_context`.
- [ ] 2.5 Write/verify tests: `grep -n 'self\.agent_pool\b' src/agentpool/agents/native_agent/agent.py src/agentpool/delegation/base_team.py src/agentpool_commands/utils.py src/agentpool_server/shared/model_utils.py` returns 0 results (excluding `self._agent_pool`). `uv run pytest -k "native_agent or team" -x` passes.

## 3. ACP Server Migration

- [ ] 3.1 Migrate `ACPProtocolHandler` in `src/agentpool_server/acp_server/handler.py` (7 refs): Change constructor param from `agent_pool: AgentPool` to `host_context: HostContext`. Store as `self._host_context`. Replace all `self.agent_pool.session_pool` with `self._host_context.session_pool`. Update caller in `acp_agent.py` to pass `host_context=self.host_context`.
- [ ] 3.2 Migrate `AgentPoolACPAgent` in `src/agentpool_server/acp_server/acp_agent.py` (~28 refs): Replace all `self.agent_pool.X` with `self.host_context.X` (manifest, main_agent_name, session_pool, skills). Replace `agent.agent_pool` on other objects with `agent.host_context`.
- [ ] 3.3 Migrate `ACPSession` in `src/agentpool_server/acp_server/session.py` (~11 refs): Rename `agent_pool` property to `host_context` (returns `self.agent.host_context`). Replace all `self.agent_pool.X` with `self.host_context.X`. Replace `getattr(self.agent, "agent_pool", None)` with `self.agent.host_context`.
- [ ] 3.4 Migrate `src/agentpool_server/acp_server/commands/debug_commands.py` (1 ref): Replace `session.agent_pool.manifest.agents` with `session.host_context.manifest.agents`.
- [ ] 3.5 Write/verify tests: `grep -rn 'self\.agent_pool\b' src/agentpool_server/acp_server/ --include='*.py'` returns 0 results. `uv run pytest tests/agentpool_server/acp_server/ -x` passes. ACP snapshot tests pass.

## 4. OpenCode Server Migration

- [ ] 4.1 Migrate `src/agentpool_server/opencode_server/state.py` (1 ref): `self.agent.agent_pool` → `self.agent.host_context`.
- [ ] 4.2 Migrate `src/agentpool_server/opencode_server/server.py` (3 refs): `agent.agent_pool` → `agent.host_context`; `agent.agent_pool.session_pool` → `ctx.session_pool`.
- [ ] 4.3 Migrate `src/agentpool_server/opencode_server/routes/session_routes.py` (2 refs): `agent.agent_pool` → `agent.host_context`; `agent.agent_pool.compaction_pipeline` → `agent.host_context.manifest.get_compaction_pipeline()`.
- [ ] 4.4 Migrate `src/agentpool_server/opencode_server/routes/agent_routes.py` (2 refs): `state.agent.agent_pool` → `state.agent.host_context`.
- [ ] 4.5 Write/verify tests: `grep -rn '\.agent_pool\b' src/agentpool_server/opencode_server/ --include='*.py' | grep -v __pycache__` returns 0 results. `uv run pytest tests/agentpool_server/ -x` passes.

## 5. Factory + Talk Migration

- [ ] 5.1 Migrate `src/agentpool/host/factory.py` (3 refs): Replace `host_context.pool` at lines 419, 509, 567 with `self._pool` (already exists at line 93).
- [ ] 5.2 Migrate `src/agentpool/talk/talk.py` (2 refs): Replace `ctx.pool` wiring at lines 162-163 and 508-509 with `source._agent_pool` + `other._bind_pool(pool)` pattern.
- [ ] 5.3 Write/verify tests: `grep -rn 'host_context\.pool\|ctx\.pool' src/agentpool/host/factory.py src/agentpool/talk/talk.py` returns 0 results. `uv run pytest tests/host/ -k talk -x` passes.

## 6. Documentation + Final Verification

- [ ] 6.1 Update `AGENTS.md`: Remove stale "18 references remain" note. Update anti-patterns section. Note `HostContext.pool` is temporary escape hatch pending skill-service change.
- [ ] 6.2 Run full test suite: `uv run pytest -x` — all tests must pass.
- [ ] 6.3 Run code quality: `uv run ruff check src/` and `uv run --no-group docs mypy src/` — no errors.
- [ ] 6.4 Run DeprecationWarning clean check: `uv run pytest -x -W error::DeprecationWarning` on key suites — zero warnings from migrated source code (covers M2 T11.4/T12.9).
- [ ] 6.5 Verify scope fidelity: `grep -rn '\.agent_pool\b' src/ --include='*.py' | grep -v _agent_pool | grep -v 'agent_pool='` returns 0.
- [ ] 6.6 Verify manual QA: `agentpool run assistant "Hello"` works, `agentpool serve-acp config.yml` starts and handles requests, AgentFactory standalone works without AgentPool (covers M1 T6.1/T6.4/T6.5/T6.6).

## 7. Optional Property Removal (can defer to M4 or skill-service change)

- [ ] 7.1 Remove `agent_pool` property getter and setter from `MessageNode` in `src/agentpool/messaging/messagenode.py`. Update `storage` property to go through `host_context`. Migrate 2 internal property SETs at lines 431 and 441 to `_bind_pool()`.
- [ ] 7.2 Migrate test files: `grep -rn '\.agent_pool\b' tests/ --include='*.py'` — replace read accesses with `.host_context`, setter accesses with `._bind_pool()`.
- [ ] 7.3 Verify: `grep -rn 'def agent_pool' src/agentpool/messaging/messagenode.py` returns 0. `uv run pytest -x` passes.
