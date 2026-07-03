## ADDED Requirements

### Requirement: Team and TeamRun classes removed
The `Team` class in `src/agentpool/delegation/team.py` and the `TeamRun` class in `src/agentpool/delegation/teamrun.py` SHALL be removed. All multi-agent execution SHALL route through `GraphConfig` + `GraphBuilder` + pydantic-graph. The `teams:` YAML syntax continues to work via auto-translation at config load time.

### Requirement: TeamConfig.get_team() removed
The deprecated `TeamConfig.get_team()` factory method SHALL be removed. Teams are resolved via `translate_team_to_graph()` → `GraphConfig` → `GraphBuilder` at config load time.

### Requirement: All callers migrated to GraphConfig
All call sites that instantiate `TeamRun` or call `TeamConfig.get_team()` SHALL be updated to use `GraphConfig` + `GraphBuilder` instead. No code SHALL import from `delegation/team.py` or `delegation/teamrun.py`.

### Requirement: site/examples YAML configs verified
All `teams:` YAML configs in `site/examples/` SHALL be tested against the auto-translation layer to verify they produce correct `GraphConfig` output.
