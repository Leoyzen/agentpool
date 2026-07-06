## MODIFIED Requirements

### Requirement: YAML parallel teams use GraphBuilder Fork and Join
AgentPool SHALL implement YAML-defined parallel team execution using `pydantic_graph.GraphBuilder` with `Fork` branching to member agents and `Join` collecting results.

After the team cleanup, legacy `Team` (parallel) and `TeamRun` (sequential) classes SHALL be removed. All team execution, whether from `teams:` or `graph:` YAML, SHALL route through `GraphConfig` and `pydantic_graph.GraphBuilder`. The `teams:` → `graph:` translator SHALL convert legacy `TeamConfig` instances to `GraphConfig` instances at config load time.

#### Scenario: teams YAML translated to graph before execution
- **WHEN** a YAML config contains `teams:` with a parallel team `[agent1, agent2]`
- **THEN** the translator SHALL produce a `GraphConfig` with `Fork` from start to `[agent1, agent2]` and `Join` from `[agent1, agent2]` to end, and execution SHALL use `GraphBuilder`

#### Scenario: Legacy Team class not used for execution
- **WHEN** a parallel team is executed from YAML config
- **THEN** the execution SHALL use `GraphConfig` + `GraphBuilder`, NOT the `Team` class

#### Scenario: Legacy TeamRun class not used for execution
- **WHEN** a sequential team is executed from YAML config
- **THEN** the execution SHALL use `GraphConfig` + `GraphBuilder`, NOT the `TeamRun` class
