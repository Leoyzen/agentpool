## Design Decisions

### D1: YAML config schema

```yaml
agents:
  my_agent:
    capabilities:
      loop_detection:
        max_depth: 10
      token_budget:
        max_tokens: 100000
      tool_output_budget:
        max_chars: 5000
      dynamic_context:
        context_limit: 8000
        compaction_fn: "agentpool.compactions.default"
      skill_activation:
        skills: ["browser", "git"]
      memory:
        store: "session"
```

Each capability has a config model that maps to its constructor args.

### D2: Reconcile with overlapping openspec changes

- `refactor-skills-as-capabilities` proposes `SkillCapability` which subsumes `SkillActivationCapability`. If that change proceeds, `SkillActivationCapability` becomes a bridge type.
- `unify-tool-interception-to-pydantic-ai-capabilities` proposes `_ToolInterceptCapability` which may subsume `ToolOutputBudgetCapability`'s truncation logic. Track and reconcile.

### D3: Hook audit

Existing hooks (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`) overlap with Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`, `wrap_tool_execute`). Audit each hook, migrate to Capability where the overlap is direct, document where semantics differ.
