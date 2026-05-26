# Decisions for ACP MCP Resource Notification Bridge

## Decision Log

### 2026-05-22: Wave 1 Planning
- Tasks 1, 2, 3 can run in parallel (no dependencies)
- Task 4 depends on 1, 2
- Task 5 depends on 1, 3
- Tasks 7, 8, 9 are sequential after Wave 2
- Final verification wave runs 4 reviewers in parallel

### Naming Conventions
- Resource content update event: `ResourceUpdatedEvent`
- Resource content update signal: `resource_updated`
- Resource content update callback: `resource_updated_callback`
- ACP extension method: `_mcp/resources/updated`
- ACP list change methods: `_mcp/tools/listChanged`, `_mcp/prompts/listChanged`, `_mcp/resources/listChanged`
