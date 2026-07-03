## 1. Remove Team and TeamRun classes

- [ ] 1.1 Remove `Team` class from `src/agentpool/delegation/team.py`
- [ ] 1.2 Remove `TeamRun` class from `src/agentpool/delegation/teamrun.py`
- [ ] 1.3 Remove `TeamConfig.get_team()` factory method
- [ ] 1.4 Remove `_TeamGraphState` from `src/agentpool/delegation/graph_team.py` if fully replaced
- [ ] 1.5 Update `AgentPool.__init__` — stop creating `Team`/`TeamRun` instances

## 2. Migrate callers

- [ ] 2.1 Audit all callers of `TeamRun` and `TeamConfig.get_team()` — create migration list
- [ ] 2.2 Migrate all callers to `GraphConfig` + `GraphBuilder`
- [ ] 2.3 Remove any remaining `from agentpool.delegation.team import` or `from agentpool.delegation.teamrun import` statements

## 3. Verify

- [ ] 3.1 Test translator against all `teams:` YAML configs in `site/examples/`
- [ ] 3.2 Run `uv run pytest tests/teams/` — team tests updated and passing
- [ ] 3.3 Run `uv run pytest tests/delegation/` — delegation tests passing
- [ ] 3.4 Run `uv run pytest` — full test suite passes
- [ ] 3.5 Run `uv run mypy src/` — no type errors
- [ ] 3.6 Run `uv run ruff check src/` — no lint errors
