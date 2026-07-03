## ADDED Requirements

### Requirement: teams-to-graph translator exists and runs at config load
The system SHALL provide a `graph_translation.py` module that translates `teams:` YAML configuration sections into equivalent `graph:` YAML configuration sections. The translator SHALL run automatically at config load time when `teams:` is present and `graph:` is absent.

#### Scenario: teams section translated to graph section
- **WHEN** a YAML config contains a `teams:` section with a sequential team `[agent1, agent2, agent3]` and no `graph:` section
- **THEN** the translator SHALL produce a `GraphConfig` with steps `[agent1, agent2, agent3]` and implicit edges `start → agent1 → agent2 → agent3 → end`

#### Scenario: parallel team translated to Fork/Join graph
- **WHEN** a YAML config contains a `teams:` section with a parallel team `[agent1, agent2]` and mode `parallel`
- **THEN** the translator SHALL produce a `GraphConfig` with edges `start → [agent1, agent2]` and `[agent1, agent2] → end`

### Requirement: Translator preserves all TeamConfig fields
The translator SHALL map every `TeamConfig` field to its `GraphConfig` equivalent: `shared_prompt` → step-level prompt, `member_timeout` → step-level timeout, `member_prompt_templates` → per-step prompt templates, `member_retry_attempts` → step-level retry config, `member_retry_delay` → step-level retry delay.

#### Scenario: member_timeout preserved in translation
- **WHEN** a `TeamConfig` has `member_timeout=60.0`
- **THEN** each generated `GraphStepConfig` SHALL carry a timeout of 60.0 seconds

#### Scenario: prompt_template preserved per member
- **WHEN** a `TeamMemberConfig` has `prompt_template="{{ prompt }}"`
- **THEN** the corresponding `GraphStepConfig` SHALL carry the prompt template for that step

### Requirement: Legacy Team and TeamRun classes removed
After the translator is complete and all existing `teams:` configs translate successfully, the `Team` (parallel) and `TeamRun` (sequential) classes SHALL be removed from the codebase. `TeamConfig.get_team()` SHALL be removed.

#### Scenario: Team class not importable
- **WHEN** code attempts `from agentpool.delegation import Team`
- **THEN** the import SHALL raise `ImportError`

#### Scenario: TeamRun class not importable
- **WHEN** code attempts `from agentpool.delegation import TeamRun`
- **THEN** the import SHALL raise `ImportError`

### Requirement: TeamConfig remains as config-only model
`TeamConfig` SHALL remain as a configuration-only model (for YAML parsing) but its `get_team()` factory method SHALL be removed. `TeamConfig` instances SHALL be converted to `GraphConfig` instances by the translator at load time.

#### Scenario: TeamConfig.get_team removed
- **WHEN** `TeamConfig` source code is inspected
- **THEN** it SHALL NOT contain a `get_team` method

#### Scenario: TeamConfig still parseable from YAML
- **WHEN** a YAML config with `teams:` section is loaded
- **THEN** `TeamConfig` SHALL parse successfully and be translatable to `GraphConfig`

### Requirement: Agent connections translated to graph edges
The translator SHALL convert agent `connections:` configuration to `GraphEdgeConfig` objects. Only `NodeConnectionConfig` entries (connections to other agents) SHALL produce edges. `FileConnectionConfig` and `CallableConnectionConfig` entries SHALL be skipped, as they write to external sinks and do not represent edges between graph steps.

#### Scenario: NodeConnectionConfig translated to edge
- **WHEN** an agent has a `NodeConnectionConfig(name="reviewer")` connection
- **THEN** the translator SHALL produce a `GraphEdgeConfig` with `from` set to the source agent name and `to` set to `"reviewer"`

#### Scenario: FileConnectionConfig skipped
- **WHEN** an agent has a `FileConnectionConfig(path="logs/messages.txt")` connection
- **THEN** the translator SHALL NOT produce a `GraphEdgeConfig` for this connection

#### Scenario: CallableConnectionConfig skipped
- **WHEN** an agent has a `CallableConnectionConfig(callable="builtins:print")` connection
- **THEN** the translator SHALL NOT produce a `GraphEdgeConfig` for this connection
