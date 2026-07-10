## ADDED Requirements

### Requirement: SkillService is a runtime-checkable Protocol for skill orchestration

SkillService SHALL be a `@runtime_checkable Protocol` that exposes read-only skill orchestration methods. It SHALL match AgentPool's existing method names exactly: `skill_capabilities` (property returning a list), `skill_provider` (property), `skill_commands` (property), `is_skill_visible_to_node(skill, node_name) -> bool`, and `get_skill_instructions_for_node(skill_name, node_name) -> str` (async). Write operations (register/unregister) SHALL NOT be included.

#### Scenario: AgentPool conforms to SkillService Protocol

- **WHEN** an AgentPool instance is checked with `isinstance(pool, SkillService)`
- **THEN** the result SHALL be `True`
- **AND** all five read-only methods SHALL be accessible via the Protocol

#### Scenario: SkillService excludes write operations

- **WHEN** the SkillService Protocol is inspected
- **THEN** it SHALL NOT include `register_skill_provider`, `unregister_skill_provider`, or any mutation methods
- **AND** only read-only access to skill state SHALL be available

#### Scenario: SkillService method names match AgentPool exactly

- **WHEN** `@runtime_checkable` isinstance check is performed
- **THEN** method names SHALL match exactly: `skill_capabilities`, `skill_provider`, `skill_commands`, `is_skill_visible_to_node`, `get_skill_instructions_for_node`
- **AND** shortened names (e.g., `capabilities` instead of `skill_capabilities`) SHALL NOT be used
