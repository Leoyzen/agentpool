# L2 MagicMock Migration Inventory

## Summary
- Total files audited: 80
- Category (a) mechanically migratable: 38
- Category (b) requires assertion rewrite: 21
- Category (c) should remain L1: 21

## Categorization Rules

| Category | Criteria |
|----------|----------|
| (a) | Pool is mocked (`MagicMock(pool)` or similar), no `side_effect`, no `call_args`/`assert_called`. Replace mock with `minimal_pool` fixture. |
| (b) | Pool is mocked AND uses `side_effect` for error injection OR `call_args`/`assert_called` for interaction verification. Needs assertions rewritten to event-based or state-based checks. |
| (c) | Only agent or other collaborator is mocked (not the pool). Already proper L1 unit tests — should NOT be migrated. |

## Detailed Inventory

| File | Category | Mock Type | side_effect count | call_args count | Notes |
|------|----------|-----------|-------------------|-----------------|-------|
| acp/test_client_handler_session_update.py | (c) | agent | 0 | 2 | Agent/collaborator mock only — proper L1 unit test |
| acp_server/test_acp_skill_lifecycle.py | (c) | agent | 0 | 17 | Agent/collaborator mock only — proper L1 unit test |
| agents/test_base_agent_run_v2.py | (a) | pool | 0 | 0 | Simple fixture swap |
| agents/test_create_child_session.py | (a) | pool | 0 | 0 | Simple fixture swap |
| agents/test_deprecation_warnings.py | (a) | pool | 0 | 0 | Simple fixture swap |
| capabilities/test_registry.py | (a) | pool | 0 | 0 | Simple fixture swap |
| delegation/test_break_behavior.py | (a) | pool | 0 | 0 | Simple fixture swap |
| delegation/test_cross_provider_session_lifecycle.py | (b) | pool | 1 | 0 | Requires assertion rewrite: side_effect for error injection |
| elicitation/test_e2e_crash_recovery.py | (a) | pool | 0 | 0 | Simple fixture swap |
| elicitation/test_unit_elicitation.py | (b) | pool | 2 | 8 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| host/test_factory.py | (b) | both | 0 | 6 | Requires assertion rewrite: call_args/assert_called for interaction verification |
| integration/test_cross_protocol.py | (a) | both | 0 | 0 | Simple fixture swap |
| integration/test_skill_e2e.py | (a) | pool | 0 | 0 | Simple fixture swap |
| integration/test_v2_message_id_integration.py | (b) | both | 4 | 1 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| lifecycle/test_crash_recovery.py | (c) | agent | 3 | 0 | Agent/collaborator mock only — proper L1 unit test |
| lifecycle/test_run_loop.py | (c) | agent | 0 | 3 | Agent/collaborator mock only — proper L1 unit test |
| lifecycle/test_session_migration.py | (a) | both | 0 | 0 | Simple fixture swap |
| messaging/test_messagenode_bind_pool.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_agent_type_detection.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_cancel_context_preservation.py | (b) | both | 1 | 0 | Requires assertion rewrite: side_effect for error injection |
| orchestrator/test_cancel_e2e.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_cancelled_cleanup_review.py | (b) | pool | 2 | 0 | Requires assertion rewrite: side_effect for error injection |
| orchestrator/test_checkpoint_close_review.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_child_done_events.py | (c) | agent | 0 | 1 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_close_checkpoint.py | (b) | pool | 2 | 0 | Requires assertion rewrite: side_effect for error injection |
| orchestrator/test_close_session.py | (b) | both | 0 | 2 | Requires assertion rewrite: call_args/assert_called for interaction verification |
| orchestrator/test_deprecation.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_e2e.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_envelope_integration.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_integration_redflags.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_multimodal_prompts.py | (c) | agent | 0 | 3 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_multimodal_storage.py | (c) | agent | 0 | 0 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_performance.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_phase6_cleanup.py | (b) | both | 3 | 0 | Requires assertion rewrite: side_effect for error injection |
| orchestrator/test_receive_request.py | (b) | both | 0 | 6 | Requires assertion rewrite: call_args/assert_called for interaction verification |
| orchestrator/test_receive_request_acp.py | (b) | both | 0 | 2 | Requires assertion rewrite: call_args/assert_called for interaction verification |
| orchestrator/test_receive_request_input_provider.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_resume_concurrency.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_resume_session.py | (b) | pool | 0 | 1 | Requires assertion rewrite: call_args/assert_called for interaction verification |
| orchestrator/test_run_handle.py | (c) | agent | 3 | 8 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_run_handle_message_id.py | (c) | agent | 0 | 2 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_run_lifecycle.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_runhandle_checkpoint.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_session_controller.py | (b) | both | 1 | 0 | Requires assertion rewrite: side_effect for error injection |
| orchestrator/test_session_lifecycle.py | (a) | pool | 0 | 0 | Simple fixture swap |
| orchestrator/test_session_pool.py | (b) | both | 3 | 6 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| orchestrator/test_session_pool_input_provider.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_session_pool_public_api.py | (b) | both | 16 | 2 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| orchestrator/test_staged_content_integration.py | (a) | both | 0 | 0 | Simple fixture swap |
| orchestrator/test_steer_callback.py | (c) | agent | 0 | 0 | Agent/collaborator mock only — proper L1 unit test |
| orchestrator/test_subagent_events.py | (c) | agent | 0 | 0 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_available_commands.py | (c) | agent | 0 | 4 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_cancel_then_prompt.py | (a) | both | 0 | 0 | Simple fixture swap |
| servers/acp_server/test_acp_elicitation_resume.py | (a) | pool | 0 | 0 | Simple fixture swap |
| servers/acp_server/test_acp_load.py | (c) | agent | 0 | 5 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_protocol_handler_cancel.py | (b) | pool | 3 | 3 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| servers/acp_server/test_acp_protocol_handler_input_provider.py | (b) | pool | 3 | 26 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| servers/acp_server/test_acp_resume.py | (c) | agent | 1 | 1 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_resume_integration.py | (a) | both | 0 | 0 | Simple fixture swap |
| servers/acp_server/test_acp_session_load.py | (c) | agent | 2 | 1 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_session_resume.py | (c) | agent | 2 | 2 | Agent/collaborator mock only — proper L1 unit test |
| servers/acp_server/test_acp_skills_red_flags.py | (a) | both | 0 | 0 | Simple fixture swap |
| servers/acp_server/test_agent_role.py | (b) | both | 1 | 2 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| servers/acp_server/test_get_session_red_flag.py | (a) | pool | 0 | 0 | Simple fixture swap |
| servers/opencode_server/test_config_routes.py | (c) | agent | 1 | 0 | Agent/collaborator mock only — proper L1 unit test |
| servers/opencode_server/test_ensure_session.py | (c) | agent | 0 | 3 | Agent/collaborator mock only — proper L1 unit test |
| servers/opencode_server/test_ensure_session_durable.py | (c) | agent | 0 | 1 | Agent/collaborator mock only — proper L1 unit test |
| servers/opencode_server/test_ensure_session_store_first.py | (c) | agent | 0 | 0 | Agent/collaborator mock only — proper L1 unit test |
| servers/opencode_server/test_session_title_fixes.py | (c) | agent | 0 | 4 | Agent/collaborator mock only — proper L1 unit test |
| sessions/test_creation_unification.py | (a) | pool | 0 | 0 | Simple fixture swap |
| sessions/test_e2e_lifecycle.py | (a) | pool | 0 | 0 | Simple fixture swap |
| sessions/test_session_controller.py | (a) | pool | 0 | 0 | Simple fixture swap |
| sessions/test_session_hierarchy.py | (a) | pool | 0 | 0 | Simple fixture swap |
| sessions/test_session_id_opaque.py | (a) | both | 0 | 0 | Simple fixture swap |
| sessions/test_session_persistence.py | (a) | pool | 0 | 0 | Simple fixture swap |
| skills/test_mcp_skills_integration.py | (a) | pool | 0 | 0 | Simple fixture swap |
| skills/test_scratchpad_skill_reference_redflag.py | (b) | pool | 1 | 0 | Requires assertion rewrite: side_effect for error injection |
| teams/test_team_streaming.py | (b) | pool | 2 | 1 | Requires assertion rewrite: side_effect for error injection; call_args/assert_called for interaction verification |
| test_load_skill_propagation.py | (a) | pool | 0 | 0 | Simple fixture swap |
| toolsets/test_input_provider_propagation.py | (b) | pool | 3 | 0 | Requires assertion rewrite: side_effect for error injection |

## Files by Category

### Category (a) — Mechanically Migratable (fixture swap only)

- `agents/test_base_agent_run_v2.py` — pool mock at L251
- `agents/test_create_child_session.py` — pool mock at L90, L127, L184
- `agents/test_deprecation_warnings.py` — pool mock at L42
- `capabilities/test_registry.py` — pool mock at L163, L191, L216
- `delegation/test_break_behavior.py` — pool mock at L89
- `elicitation/test_e2e_crash_recovery.py` — pool mock at L270, L492, L633, L789
- `integration/test_cross_protocol.py` — pool mock at L94
- `integration/test_skill_e2e.py` — pool mock at L230, L268
- `lifecycle/test_session_migration.py` — pool mock at L94
- `messaging/test_messagenode_bind_pool.py` — pool mock at L31, L42, L56
- `orchestrator/test_agent_type_detection.py` — pool mock at L22
- `orchestrator/test_cancel_e2e.py` — pool mock at L124
- `orchestrator/test_checkpoint_close_review.py` — pool mock at L52
- `orchestrator/test_deprecation.py` — pool mock at L36
- `orchestrator/test_e2e.py` — pool mock at L51
- `orchestrator/test_envelope_integration.py` — pool mock at L29
- `orchestrator/test_integration_redflags.py` — pool mock at L110, L146, L167, L186
- `orchestrator/test_performance.py` — pool mock at L43
- `orchestrator/test_receive_request_input_provider.py` — pool mock at L39
- `orchestrator/test_resume_concurrency.py` — pool mock at L58
- `orchestrator/test_run_lifecycle.py` — pool mock at L206
- `orchestrator/test_runhandle_checkpoint.py` — pool mock at L167
- `orchestrator/test_session_lifecycle.py` — pool mock at L87, L161, L170, L181
- `orchestrator/test_session_pool_input_provider.py` — pool mock at L43
- `orchestrator/test_staged_content_integration.py` — pool mock at L199
- `servers/acp_server/test_acp_cancel_then_prompt.py` — pool mock at L88
- `servers/acp_server/test_acp_elicitation_resume.py` — pool mock at L33
- `servers/acp_server/test_acp_resume_integration.py` — pool mock at L63
- `servers/acp_server/test_acp_skills_red_flags.py` — pool mock at L135
- `servers/acp_server/test_get_session_red_flag.py` — pool mock at L25, L39
- `sessions/test_creation_unification.py` — pool mock at L31
- `sessions/test_e2e_lifecycle.py` — pool mock at L156
- `sessions/test_session_controller.py` — pool mock at L293
- `sessions/test_session_hierarchy.py` — pool mock at L27
- `sessions/test_session_id_opaque.py` — pool mock at L121
- `sessions/test_session_persistence.py` — pool mock at L52
- `skills/test_mcp_skills_integration.py` — pool mock at L27, L157
- `test_load_skill_propagation.py` — pool mock at L57, L73, L110

### Category (b) — Requires Assertion Rewrite

- `delegation/test_cross_provider_session_lifecycle.py` — pool mock at L727 (side_effect=1, call_args=0)
- `elicitation/test_unit_elicitation.py` — pool mock at L888, L966 (side_effect=2, call_args=8)
- `host/test_factory.py` — pool mock at L86, L105, L133, L160 (side_effect=0, call_args=6)
- `integration/test_v2_message_id_integration.py` — pool mock at L116 (side_effect=4, call_args=1)
- `orchestrator/test_cancel_context_preservation.py` — pool mock at L243, L421, L523 (side_effect=1, call_args=0)
- `orchestrator/test_cancelled_cleanup_review.py` — pool mock at L73, L128 (side_effect=2, call_args=0)
- `orchestrator/test_close_checkpoint.py` — pool mock at L65 (side_effect=2, call_args=0)
- `orchestrator/test_close_session.py` — pool mock at L40, L151, L255 (side_effect=0, call_args=2)
- `orchestrator/test_phase6_cleanup.py` — pool mock at L41 (side_effect=3, call_args=0)
- `orchestrator/test_receive_request.py` — pool mock at L36, L298 (side_effect=0, call_args=6)
- `orchestrator/test_receive_request_acp.py` — pool mock at L34 (side_effect=0, call_args=2)
- `orchestrator/test_resume_session.py` — pool mock at L124 (side_effect=0, call_args=1)
- `orchestrator/test_session_controller.py` — pool mock at L37, L475, L572 (side_effect=1, call_args=0)
- `orchestrator/test_session_pool.py` — pool mock at L27 (side_effect=3, call_args=6)
- `orchestrator/test_session_pool_public_api.py` — pool mock at L32 (side_effect=16, call_args=2)
- `servers/acp_server/test_acp_protocol_handler_cancel.py` — pool mock at L26 (side_effect=3, call_args=3)
- `servers/acp_server/test_acp_protocol_handler_input_provider.py` — pool mock at L28 (side_effect=3, call_args=26)
- `servers/acp_server/test_agent_role.py` — pool mock at L24, L35, L81 (side_effect=1, call_args=2)
- `skills/test_scratchpad_skill_reference_redflag.py` — pool mock at L154 (side_effect=1, call_args=0)
- `teams/test_team_streaming.py` — pool mock at L246, L474 (side_effect=2, call_args=1)
- `toolsets/test_input_provider_propagation.py` — pool mock at L256 (side_effect=3, call_args=0)

### Category (c) — Should Remain L1 (agent/collaborator mocks only)

- `acp/test_client_handler_session_update.py` — agent mock at L35 (side_effect=0, call_args=2)
- `acp_server/test_acp_skill_lifecycle.py` — agent mock at L104 (side_effect=0, call_args=17)
- `lifecycle/test_crash_recovery.py` — agent mock at L99, L340, L398, L458 (side_effect=3, call_args=0)
- `lifecycle/test_run_loop.py` — agent mock at L84, L249, L275, L311 (side_effect=0, call_args=3)
- `orchestrator/test_child_done_events.py` — agent mock at L72 (side_effect=0, call_args=1)
- `orchestrator/test_multimodal_prompts.py` — agent mock at L56, L96, L123, L150 (side_effect=0, call_args=3)
- `orchestrator/test_multimodal_storage.py` — agent mock at L533 (side_effect=0, call_args=0)
- `orchestrator/test_run_handle.py` — agent mock at L101, L140, L182, L220 (side_effect=3, call_args=8)
- `orchestrator/test_run_handle_message_id.py` — agent mock at L55 (side_effect=0, call_args=2)
- `orchestrator/test_steer_callback.py` — agent mock at L33 (side_effect=0, call_args=0)
- `orchestrator/test_subagent_events.py` — agent mock at L59 (side_effect=0, call_args=0)
- `servers/acp_server/test_acp_available_commands.py` — agent mock at L40 (side_effect=0, call_args=4)
- `servers/acp_server/test_acp_load.py` — agent mock at L195, L246, L313, L357 (side_effect=0, call_args=5)
- `servers/acp_server/test_acp_resume.py` — agent mock at L116, L146, L174, L224 (side_effect=1, call_args=1)
- `servers/acp_server/test_acp_session_load.py` — agent mock at L67, L257 (side_effect=2, call_args=1)
- `servers/acp_server/test_acp_session_resume.py` — agent mock at L63, L229, L285, L337 (side_effect=2, call_args=2)
- `servers/opencode_server/test_config_routes.py` — agent mock at L20, L47, L59, L71 (side_effect=1, call_args=0)
- `servers/opencode_server/test_ensure_session.py` — agent mock at L25 (side_effect=0, call_args=3)
- `servers/opencode_server/test_ensure_session_durable.py` — agent mock at L38 (side_effect=0, call_args=1)
- `servers/opencode_server/test_ensure_session_store_first.py` — agent mock at L33 (side_effect=0, call_args=0)
- `servers/opencode_server/test_session_title_fixes.py` — agent mock at L557 (side_effect=0, call_args=4)
