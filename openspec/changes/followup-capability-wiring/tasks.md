## 1. Hook audit

- [ ] 1.1 Audit `pre_run` hook — compare with `wrap_node_run` Capability hook
- [ ] 1.2 Audit `post_run` hook — compare with `after_node_run` Capability hook
- [ ] 1.3 Audit `pre_tool_use` hook — compare with `before_tool_execute` / `wrap_tool_execute` Capability hook
- [ ] 1.4 Audit `post_tool_use` hook — compare with `after_tool_execute` Capability hook
- [ ] 1.5 Document which hooks migrate to Capabilities and which remain distinct

## 2. YAML config support

- [ ] 2.1 Add `capabilities:` section to agent config model in `agentpool_config/`
- [ ] 2.2 Create config models for each capability (map YAML args to constructor)
- [ ] 2.3 Validate capability configs at load time

## 3. Agent class wiring

- [ ] 3.1 Update `Agent` class to accept and attach Capabilities from config
- [ ] 3.2 Verify Capability hooks fire on standalone run path
- [ ] 3.3 Verify Capability hooks fire on graph run path (after Phase 4 Team/TeamRun removal)

## 4. Reconcile with overlapping changes

- [ ] 4.1 Reconcile `SkillActivationCapability` with `refactor-skills-as-capabilities` (SkillCapability)
- [ ] 4.2 Reconcile `ToolOutputBudgetCapability` with `unify-tool-interception-to-pydantic-ai-capabilities` (_ToolInterceptCapability)

## 5. Verify

- [ ] 5.1 Run `uv run pytest tests/agents/` — agent tests with Capabilities passing
- [ ] 5.2 Run `uv run pytest tests/capabilities/` — capability tests still passing
- [ ] 5.3 Run `uv run mypy src/` — no type errors
- [ ] 5.4 Run `uv run ruff check src/` — no lint errors
