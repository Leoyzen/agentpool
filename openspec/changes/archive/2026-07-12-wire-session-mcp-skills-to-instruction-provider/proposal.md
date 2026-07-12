## Why

When MCP servers are connected via ACP (MCP-over-ACP), their skills (published as `skill://` resources) are correctly discovered by `MCPResourceProvider.get_skills()` at the session level, but never reach the `SkillsInstructionProvider` that generates the `<available-skills>` XML injected into agent prompts. This is because session-level `MCPResourceProvider` instances (created in `ACPSession.initialize_mcp_servers()`) are added to the session agent's tool chain but never registered in the pool's `_skill_provider` aggregator, which is the sole source `SkillsInstructionProvider` queries.

## What Changes

- Wire session-level `MCPResourceProvider` instances into the pool's `_skill_provider` aggregator so that MCP-over-ACP skills appear in `<available-skills>` XML and `load_skill` resolution
- Add a method on `AgentPool` to dynamically register additional `ResourceProvider` instances into the existing aggregator after `_setup_skills_provider()` has run
- Call this registration from `ACPSession.initialize_mcp_servers()` after each session MCP provider is created
- Ensure `SkillURIResolver` also learns about session-level providers so `load_skill` with bare skill names resolves correctly

## Capabilities

### New Capabilities
- `session-mcp-skill-wiring`: Session-level MCP providers created during ACP session initialization are dynamically registered into the pool's skill aggregator and URI resolver, making their skills visible to `SkillsInstructionProvider` and `load_skill`.

### Modified Capabilities
<!-- None -->

## Impact

- `src/agentpool/delegation/pool.py` — new `register_skill_provider()` method on `AgentPool`
- `src/agentpool_server/acp_server/session.py` — call pool registration in `initialize_mcp_servers()`
- `src/agentpool/resource_providers/aggregating.py` — may need `add_provider()` method
- `src/agentpool/skills/uri_resolver.py` — may need `register_provider()` to handle late registration
