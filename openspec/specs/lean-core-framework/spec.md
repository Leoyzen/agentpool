## ADDED Requirements

### Requirement: Framework supports only native and acp agent types
The system SHALL accept only `native` and `acp` as valid agent type discriminators in all configuration and runtime APIs. All other agent types SHALL be rejected at config validation time.

#### Scenario: YAML config with native agent passes validation
- **WHEN** a YAML config defines `type: native` for an agent
- **THEN** the config is accepted and the agent is instantiated as a `NativeAgent`

#### Scenario: YAML config with acp agent passes validation
- **WHEN** a YAML config defines `type: acp` for an agent
- **THEN** the config is accepted and the agent is instantiated as an `ACPAgent`

#### Scenario: YAML config with removed agent type fails validation
- **WHEN** a YAML config defines `type: claude`, `type: agui`, or `type: codex`
- **THEN** config validation raises a `ValidationError` with a clear message indicating the type is no longer supported

#### Scenario: AnyAgentConfig union only includes native and acp
- **WHEN** code references `AnyAgentConfig` type
- **THEN** the union only contains `NativeAgentConfig` and `ACPAgentConfig`

## REMOVED Requirements

### Requirement: Framework supports claude agent type
**Reason**: Claude Code agent is an external CLI wrapper with high maintenance overhead and overlaps with native agent capabilities. The framework core should focus on pydantic-ai native agents and ACP protocol agents.
**Migration**: Users previously using `type: claude` should migrate to `type: native` with appropriate model configuration, or run Claude Code externally via ACP protocol.

### Requirement: Framework supports agui agent type
**Reason**: AG-UI agent is a remote HTTP-based agent with low adoption. The ACP protocol provides a more robust and standard way to integrate external agents.
**Migration**: Users previously using `type: agui` should migrate to `type: acp` for external agent integration.

### Requirement: Framework supports codex agent type
**Reason**: Codex agent is an OpenAI Codex CLI wrapper that duplicates native agent functionality. Native agents already support OpenAI models directly.
**Migration**: Users previously using `type: codex` should migrate to `type: native` with `model: openai:gpt-4o-codex` or equivalent.
