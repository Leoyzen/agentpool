## ADDED Requirements

### Requirement: Capabilities attachable to agents via YAML
The agent YAML config SHALL support a `capabilities:` section that maps capability names to their config models. Each capability config model SHALL map to its constructor arguments. Supported capabilities: `loop_detection`, `token_budget`, `tool_output_budget`, `dynamic_context`, `skill_activation`, `memory`.

### Requirement: Agent class accepts and attaches Capabilities
The `Agent` class SHALL accept Capabilities from config and attach them to the underlying pdai agentlet. Capabilities SHALL be attached via the `capabilities=` parameter in `agentlet.__init__()` or equivalent pdai API.

### Requirement: Existing hooks audited for Capability overlap
All existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) SHALL be audited for overlap with Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`, `wrap_tool_execute`). Each hook SHALL be either migrated to a Capability or documented as having distinct semantics that warrant keeping it.
