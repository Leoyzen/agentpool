## ADDED Requirements

### Requirement: Capabilities attachable to agents via YAML
The agent YAML config SHALL support a `capabilities:` section that maps capability names to their config models. Each capability config model SHALL map to its constructor arguments. Supported capabilities: `loop_detection`, `token_budget`, `tool_output_budget`, `dynamic_context`, `skill_activation`, `memory`.

#### Scenario: capabilities section parsed from YAML
- **WHEN** a YAML config contains `capabilities: { loop_detection: { max_depth: 10 } }` under an agent
- **THEN** the config loader SHALL parse it into a `LoopDetectionCapability` config model with `max_depth=10`

#### Scenario: unknown capability name rejected
- **WHEN** a YAML config contains `capabilities: { unknown_cap: {} }` under an agent
- **THEN** the config loader SHALL raise a validation error

### Requirement: Agent class accepts and attaches Capabilities
The `Agent` class SHALL accept Capabilities from config and attach them to the underlying pdai agentlet. Capabilities SHALL be attached via the `capabilities=` parameter in `agentlet.__init__()` or equivalent pdai API.

#### Scenario: capabilities fire on standalone run
- **WHEN** an agent with `LoopDetectionCapability(max_depth=3)` attached runs standalone via `agent.run_stream()`
- **THEN** the `wrap_node_run` hook SHALL fire on each node iteration, incrementing the depth counter

#### Scenario: capabilities fire on graph run
- **WHEN** an agent with `TokenBudgetCapability` attached runs as part of a `GraphBuilder` workflow
- **THEN** the `before_model_request` hook SHALL fire before each model call in the graph

### Requirement: Existing hooks audited for Capability overlap
All existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) SHALL be audited for overlap with Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`, `wrap_tool_execute`). Each hook SHALL be either migrated to a Capability or documented as having distinct semantics that warrant keeping it.

#### Scenario: hook audit document produced
- **WHEN** the audit is complete
- **THEN** a document SHALL exist listing each hook, its Capability equivalent (if any), and the decision (migrate or keep)

#### Scenario: migrated hooks removed
- **WHEN** a hook is determined to overlap fully with a Capability
- **THEN** the hook SHALL be removed and its behavior SHALL be handled by the corresponding Capability
