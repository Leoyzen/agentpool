## ADDED Requirements

### Requirement: Team and TeamRun classes removed
The `Team` class in `src/agentpool/delegation/team.py` and the `TeamRun` class in `src/agentpool/delegation/teamrun.py` SHALL be removed. All multi-agent execution SHALL route through `GraphConfig` + `GraphBuilder` + pydantic-graph. The `teams:` YAML syntax continues to work via auto-translation at config load time.

#### Scenario: Team class not importable
- **WHEN** code attempts `from agentpool.delegation.team import Team`
- **THEN** the import SHALL raise `ImportError`

#### Scenario: TeamRun class not importable
- **WHEN** code attempts `from agentpool.delegation.teamrun import TeamRun`
- **THEN** the import SHALL raise `ImportError`

#### Scenario: teams YAML still works via auto-translation
- **WHEN** a YAML config contains `teams:` with `mode: sequential` and `members: [agent1, agent2]`
- **THEN** the config loader SHALL auto-translate it to a `GraphConfig` and execute via `GraphBuilder` without instantiating `Team` or `TeamRun`

### Requirement: TeamConfig.get_team() removed
The deprecated `TeamConfig.get_team()` factory method SHALL be removed. Teams are resolved via `translate_team_to_graph()` → `GraphConfig` → `GraphBuilder` at config load time.

#### Scenario: get_team method not present
- **WHEN** `TeamConfig` source code is inspected
- **THEN** it SHALL NOT contain a `get_team` method

#### Scenario: TeamConfig still parseable from YAML
- **WHEN** a YAML config with `teams:` section is loaded
- **THEN** `TeamConfig` SHALL parse successfully and be translatable to `GraphConfig`

### Requirement: All callers migrated to GraphConfig
All call sites that instantiate `TeamRun` or call `TeamConfig.get_team()` SHALL be updated to use `GraphConfig` + `GraphBuilder` instead. No code SHALL import from `delegation/team.py` or `delegation/teamrun.py`.

#### Scenario: no imports of Team remain
- **WHEN** `grep -r "from agentpool.delegation.team import" src/ tests/` is run
- **THEN** it SHALL return zero matches

#### Scenario: no imports of TeamRun remain
- **WHEN** `grep -r "from agentpool.delegation.teamrun import" src/ tests/` is run
- **THEN** it SHALL return zero matches

### Requirement: site/examples YAML configs verified
All `teams:` YAML configs in `site/examples/` SHALL be tested against the auto-translation layer to verify they produce correct `GraphConfig` output.

#### Scenario: all example configs translate successfully
- **WHEN** the auto-translation layer is applied to every `config.yml` in `site/examples/` that contains a `teams:` section
- **THEN** each SHALL produce a valid `GraphConfig` without errors
