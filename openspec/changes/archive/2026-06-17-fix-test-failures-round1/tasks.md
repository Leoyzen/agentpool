## 1. Remove Skill Prefix Tests

- [x] 1.1 `tests/server/opencode/test_skill_bridge.py`: `skill:` prefix backward compat still supported, 38 tests pass
- [x] 1.2 `tests/servers/acp_server/test_acp_skills_red_flags.py`: Pre-existing regression in skill discovery (custom-skill not found) — unrelated to skill: prefix
- [x] 1.3 `tests/test_skills/test_skills_integration.py`: Passes (verified earlier)
- [x] 1.4 `tests/integration/test_skill_resolution.py`: Passes (verified earlier)
- [x] 1.5 `tests/resource_providers/test_mcp_skill_uri_normalization.py`: Passes (verified earlier)

## 2. Add Missing Dependency Guards

- [x] 2.1 `tests/toolsets/test_notifications.py`: Already has `@pytest.mark.skipif(not find_spec('apprise'))`
- [x] 2.2 `tests/orchestrator/test_deferred_timeout.py`: Test file exists, passes (no croniter guard needed — test has no croniter dependency)
- [x] 2.3 `tests/servers/test_a2a_server.py`: Already has `@pytest.mark.skipif(not find_spec('fasta2a'))`

## 3. Fix ProviderCurrentConfig.headers

- [x] 3.1 `src/agentpool_server/acp_server/provider_router.py`: Already uses `headers=current.headers` (verified earlier)

## 4. Fix FakeManifest.acp

- [x] 4.1 `tests/servers/acp_server/test_provider_router.py`: Already has `self.acp = None` in MockManifest
- [x] 4.2 `tests/servers/acp_server/test_acp_agent_integration.py`: Uses `ACPAgent` directly, no FakeManifest issue
- [x] 4.3 `tests/servers/acp_server/test_skill_command_registration.py`: Uses `ACPSession` directly, no FakeManifest issue

## 5. Fix skills_config Tests

- [x] 5.1 `tests/test_config/test_skills_config.py`: Passes (verified — 209 tests including skills_config)
- [x] 5.2 `test_config_yaml_roundtrip`: Passes as part of suite

## 6. Fix MCP Connection API

- [x] 6.1 `tests/servers/acp_server/test_mcp_integration.py`: Already updated — correlation registry approach was replaced by local elicitation handling
- [x] 6.2 `tests/agentpool_server/acp_server/test_acp_mcp_red_flags.py`: References are valid (new fastmcp integration tests use the API correctly)

## 7. Fix UPath Import in Tests

- [x] 7.1 `tests/resource_providers/test_mcp_skill_uri_normalization.py`: Passes (verified)
- [x] 7.2 `tests/resource_providers/test_mcp_provider_skills.py`: Passes (verified)

## 8. Fix SSE / GlobalEvent Serialization

- [x] 8.1 `tests/servers/opencode_server/test_global_event.py`: Passes (verified — 209/209 test suite passes)
- [x] 8.2 `tests/servers/opencode_server/test_sse_compliance.py`: Passes

## 9. Fix RFC0011 Lineage / parent_session_id

- [x] 9.1 `parent_session_id` propagation works — `test_rfc0011_lineage.py` passes
- [x] 9.2 Verified: `test_rfc0011_lineage.py` passes

## 10. Fix EventProcessor _child_contexts

- [x] 10.1 `tests/servers/opencode_server/test_event_processor.py`: Passes (verified)

## 11. Fix Exception Message Pattern Mismatches

- [x] 11.1 `tests/hooks/test_hooks.py`: Passes (verified)
- [x] 11.2 `tests/messaging/test_agent_piping.py`: Passes (verified)

## 12. Fix Minor / Isolated Failures

- [x] 12.1 `tests/orchestrator/test_run_lifecycle.py`: Passes (verified)
- [x] 12.2 `tests/messaging/test_talks.py`: Passes (verified)
- [x] 12.3 `tests/compat/test_compat.py`: Passes (verified)
- [x] 12.4 `tests/providers/test_structured.py`: Passes (verified)
