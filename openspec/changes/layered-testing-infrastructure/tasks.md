## Phase A — Marker & Consolidation & L2 Migration (separate PR)

### 1. Marker Taxonomy & Migration

- [x] 1.1 Add `vcr`, `e2e`, `real_model` markers to `pyproject.toml` pytest markers list
- [x] 1.2 Rename `acp_snapshot` marker to `snapshot` in `pyproject.toml` and all 2 files using it
- [x] 1.3 Retire `requires_openai_key` marker — replace with `real_model` in all 2 files using it
- [x] 1.4 Update `pyproject.toml` `addopts` to: `["-m", "not slow and not snapshot and not e2e and not real_model and not real_mcp"]`
- [x] 1.5 Add `real_model` auto-skip logic: if `OPENAI_API_KEY` not set, skip `@pytest.mark.real_model` tests
- [x] 1.6 Add CI check script that verifies every test file has at least one layer marker (`unit`, `integration`, `vcr`, or `e2e`)
- [x] 1.7 Mark all 109 unmarked test files with appropriate layer markers (capabilities=22, config=9, sessions=25, skills=7, teams=4, manifest=6, host=3, running=3, prompts=3, other=27)
- [ ] 1.8 Deflake 5 `@pytest.mark.flaky` tests and remove the marker
- [x] 1.9 Delete `tests/resource_providers/` directory (dead code — already deleted in M3)
- [ ] 1.10 Verify existing tests still pass after marker migration

### 2. Test File Consolidation

- [x] 2.1 Merge ACP `test_resume_*.py` (3 files) → `test_session_resume.py` (skipped — no files matching pattern)
- [x] 2.2 Merge ACP `test_ws_disconnect_*.py` (3 files) → `test_websocket_lifecycle.py` (skipped — no files matching pattern)
- [x] 2.3 Merge ACP `test_turn_complete_*.py` (2 files) → `test_turn_completion.py` (1 file renamed)
- [x] 2.4 Merge OpenCode `test_subagent_*.py` (5 files) → `test_subagent_integration.py` (4 files merged, 7 tests)
- [x] 2.5 Merge OpenCode `test_question_*.py` (3 files) → `test_question_handling.py` (3 files merged, 39 tests)
- [x] 2.6 Merge OpenCode `test_title_*.py` (3 files) → `test_title_management.py` (1 file renamed)
- [x] 2.7 Merge OpenCode `test_ensure_session_*.py` (3 files) → `test_session_provisioning.py` (2 files merged, 22 tests)
- [x] 2.8 Merge OpenCode `test_session_pool_*.py` (5 files) → `test_session_pool_integration.py` (1 file renamed)
- [x] 2.9 Merge OpenCode `test_event_bridge_*.py` (2 files) → `test_event_bridge.py` (1 file merged into existing, 13 tests)
- [x] 2.10 Merge OpenCode `test_event_adapter*.py` + `test_event_conversion*.py` → `test_event_adaptation.py` (2 files merged, 47 tests)
- [ ] 2.11 Merge orchestrator EventBus variant tests → `test_event_bus.py`
- [ ] 2.12 Merge orchestrator RunHandle variant tests → `test_run_handle.py`
- [ ] 2.13 Merge orchestrator session close/checkpoint tests → `test_session_lifecycle.py`
- [ ] 2.14 Merge orchestrator cancel variant tests → `test_cancellation.py`
- [ ] 2.15 Merge orchestrator SessionPool e2e tests → `test_sessionpool_integration.py`
- [ ] 2.16 Merge orchestrator resume variant tests → `test_resume_integration.py`
- [ ] 2.17 Consolidate skills tests: `tests/test_skills/` + `tests/test_config/test_skills_config.py` → `tests/skills/`
- [ ] 2.18 Relocate 8 root test files to appropriate subdirectories
- [ ] 2.19 Verify all test functions preserved after merges (count matches)

### 2b. L2 MagicMock → Real AgentPool + TestModel Migration

All L2 tests that use `MagicMock(pool)` or `MagicMock(agent)` as the pool/agent dependency SHALL be migrated to use a real `AgentPool` built from a minimal YAML config with `TestModel` as the model. This roots out false confidence at the source.

- [x] 2b.1 Create `tests/fixtures/minimal_pool.py` with `minimal_pool` fixture (real `AgentPool` from inline YAML, single native agent using `TestModel`, no MCP servers, no storage)
- [x] 2b.2 Audit all L2 test files for `MagicMock(pool)` or `MagicMock(agent)` usage — produce inventory list with match counts
- [x] 2b.2.5 Categorize each L2 MagicMock test file into: (a) mechanically migratable (fixture swap, TestModel replaces return_value), (b) requires assertion rewrite (side_effect error injection → TestModel custom sequences, call_args/assert_called → event-based assertions), (c) should remain L1 with targeted mocks (single-collaborator mocks, not pool-level). Document categorization in `tests/MIGRATION_INVENTORY.md`
- [ ] 2b.3 Migrate ACP server L2 tests: replace mock pool with `minimal_pool` fixture in `tests/servers/acp_server/` (19 files) — handle category (b) tests by rewriting assertions
- [ ] 2b.4a Migrate OpenCode server L2 tests — event adapter/conversion group (~8 files)
- [ ] 2b.4b Migrate OpenCode server L2 tests — session pool/provisioning group (~10 files)
- [ ] 2b.4c Migrate OpenCode server L2 tests — subagent/question/title groups (~12 files)
- [ ] 2b.4d Migrate OpenCode server L2 tests — remaining files (~31 files)
- [ ] 2b.5 Migrate orchestrator L2 tests: replace mock pool/agent with `minimal_pool` in `tests/orchestrator/` (~27 files) — handle side_effect/call_args patterns
- [ ] 2b.6 Migrate agentpool_server L2 tests: replace mock pool in `tests/agentpool_server/acp_server/` (12 files)
- [ ] 2b.7 Migrate any remaining L2 tests using MagicMock pool in `tests/integration/`, `tests/sessions/`, `tests/acp/`
- [ ] 2b.8 For tests that assert specific return values: replace mock `.return_value` with `TestModel` custom responses (using `TestModel(custom_result_texts=[...])` or `FunctionModel`)
- [ ] 2b.9 For tests that assert event sequences: verify events still emit correctly with real pool + TestModel (real capability wiring, real EventBus)
- [ ] 2b.10 Verify ALL migrated L2 tests pass with real pool + TestModel (no MagicMock remaining in L2 test files)
- [ ] 2b.11 Add CI check that scans for `MagicMock` usage in `@pytest.mark.integration` test files (warn if found)

### 3. Documentation (Phase A)

- [x] 3.1 Update `tests/AGENTS.md` with L1-L4 conceptual taxonomy table and marker mapping
- [x] 3.2 Update `tests/AGENTS.md` marker reference table (including renamed `snapshot`, retired `requires_openai_key`, new `real_model`)
- [x] 3.3 Update `tests/AGENTS.md` with marker stacking rule (one primary layer marker; `vcr` tests may add `integration` as secondary)
- [x] 3.4 Update `tests/AGENTS.md` with mandatory test layers by feature type table
- [x] 3.5 Update root `AGENTS.md` Testing section to reference `tests/AGENTS.md` and reflect L1-L4 taxonomy
- [x] 3.6 Update `pyproject.toml` test command examples in root `AGENTS.md` to include `tests/vcr/` and `-m e2e`

## Phase B — VCR Infrastructure + Initial L3 (requires [HUMAN] for cassette recording)

### 4. VCR Infrastructure Setup

- [ ] 4.1 Add `pytest-recording`, `vcrpy`, `dirty-equals`, `inline-snapshot` to dev dependencies in `pyproject.toml`
- [ ] 4.2 Add `ALLOW_MODEL_REQUESTS = False` module-level constant to `tests/conftest.py`
- [ ] 4.3 Add `_block_model_requests` autouse fixture to `tests/conftest.py` — installs httpx `MockTransport` blocking handler when `ALLOW_MODEL_REQUESTS` is `False`, raises `RuntimeError` with guidance on VCR/`allow_model_requests` fixture
- [ ] 4.4 Add `allow_model_requests` fixture that temporarily disables the blocking handler
- [ ] 4.5 Add `vcr_config` fixture: `filter_headers` (authorization, x-api-key, cookie, set-cookie), `decode_compressed_response=True`, `match_on=['method', 'scheme', 'host', 'path', 'body']`
- [ ] 4.6 Add `json_body_serializer` function: decompress gzip/brotli, normalize smart quotes, scrub credentials, store JSON as `parsed_body` dict
- [ ] 4.7 Add `before_record_request` and `before_record_response` hooks for credential scrubbing and transient header stripping
- [ ] 4.8 Add `track_httpx_clients` / `close_httpx_clients` fixtures (port from pydantic-ai) to prevent httpx resource leaks
- [ ] 4.9 Add `disable_ssrf_protection_for_vcr` fixture (patches URL validation for VCR matching)
- [ ] 4.10 Add `CassetteContext` class (port from pydantic-ai) for wire-level request/response body assertions (`verify_contains`, `verify_ordering`)
- [ ] 4.11 Add `fail_partially_used_vcr_cassettes` autouse fixture for VCR-marked tests
- [ ] 4.12 Create `tests/cassettes/` directory with `.gitkeep`
- [ ] 4.13 Add `tests/check_cassettes.py` script — verifies every `.yaml` cassette has a corresponding test function
- [ ] 4.14 Add `tests/check_vcr_tests.py` script — verifies every `@pytest.mark.vcr` test has a corresponding cassette file (inverse hygiene check)
- [ ] 4.15 Verify existing tests still pass with new conftest changes (no `ALLOW_MODEL_REQUESTS` enforcement regressions)

### 5. L3 VCR Tests — Native Agent (initial 5 cassettes)

- [ ] 5.1 Create `tests/vcr/__init__.py` and `tests/vcr/conftest.py` with `vcr_pool` fixture (real `AgentPool` from minimal YAML config, single native agent)
- [ ] 5.2 Create `tests/vcr/test_native_basic.py` — basic text completion (P1 pattern)
- [ ] 5.3 [HUMAN-REQUIRED] Record cassette for `test_native_basic` using `--record-mode=once` with `OPENAI_API_KEY`
- [ ] 5.4 Verify cassette sanitization (no credentials, decoded bodies) by inspecting the `.yaml` file
- [ ] 5.5 Create `tests/vcr/test_native_tool_call.py` — tool call round trip with wire inspection (P2 pattern)
- [ ] 5.6 [HUMAN-REQUIRED] Record cassette for `test_native_tool_call`
- [ ] 5.7 Create `tests/vcr/test_native_streaming.py` — streaming event sequence: PartStart → PartDelta* → PartEnd → StreamComplete (P6 pattern)
- [ ] 5.8 [HUMAN-REQUIRED] Record cassette for `test_native_streaming`
- [ ] 5.9 Verify all native VCR tests pass with `--strict-vcr-cassette-usage` in CI replay mode

### 6. L3 VCR Tests — ACP Protocol (initial 2-3 cassettes)

- [ ] 6.1 Create `tests/vcr/test_acp_protocol.py` — session_init + basic_completion + streaming_events using paired `AgentSideConnection`/`ClientSideConnection` via pipe (reuse `test_rpc.py` pattern)
- [ ] 6.2 [HUMAN-REQUIRED] Record cassettes for ACP protocol test cases
- [ ] 6.3 Verify ACP event sequence: RunStartedEvent → PartStartEvent → PartDeltaEvent → PartEndEvent → StreamCompleteEvent
- [ ] 6.4 Create `tests/vcr/test_acp_tool_call.py` — tool call through ACP: ToolCallStartEvent + ToolCallCompleteEvent (P2 pattern)
- [ ] 6.5 [HUMAN-REQUIRED] Record cassette for ACP tool call test
- [ ] 6.6 Verify all ACP VCR tests pass in CI replay mode

### 7. CI Pipeline Updates (Phase B)

- [ ] 7.1 Add VCR replay stage to `pytest.yml`: `pytest tests/vcr/ --strict-vcr-cassette-usage -m "not e2e"`
- [ ] 7.2 Add cassette hygiene check step to `pytest.yml`: `python tests/check_cassettes.py`
- [ ] 7.3 Add VCR test hygiene check step to `pytest.yml`: `python tests/check_vcr_tests.py`
- [ ] 7.4 Add pre-commit hook that scans cassettes for `authorization` header values (fail if found)
- [ ] 7.5 Add `tests/cassettes/**/*.yaml` to `.gitattributes` with `linguist-generated=true`
- [ ] 7.6 Update `build.yml` to include VCR tests in the matrix runs
- [ ] 7.7 Verify CI pipeline passes with all new stages

## Phase C — L4 E2E + Remaining L3 (builds on Phase B)

### 8. L3 VCR Tests — Remaining Protocols

- [ ] 8.1 Create `tests/vcr/test_opencode_protocol.py` — session_create, prompt_sse_stream, tool_call_events, subagent_events, session_close, error_handling
- [ ] 8.2 [HUMAN-REQUIRED] Record cassettes for OpenCode protocol test cases
- [ ] 8.3 Verify OpenCode SSE event format matches expected schema
- [ ] 8.4 Create `tests/vcr/test_agui_protocol.py` — session_init, event_stream, tool_call, state_sync, error_handling
- [ ] 8.5 [HUMAN-REQUIRED] Record cassettes for AG-UI protocol test cases
- [ ] 8.6 Create `tests/vcr/test_openai_api_protocol.py` — chat_completion, streaming_completion, tool_call, multi_turn, error_handling
- [ ] 8.7 [HUMAN-REQUIRED] Record cassettes for OpenAI API protocol test cases
- [ ] 8.8 Create `tests/vcr/test_acp_subagent.py` — subagent delegation: SpawnSessionStartEvent + SpawnSessionCompleteEvent
- [ ] 8.9 [HUMAN-REQUIRED] Record cassette for ACP subagent test
- [ ] 8.10 Add error-path cassettes for each protocol (rate limit 429, server error 500, malformed stream)
- [ ] 8.11 [HUMAN-REQUIRED] Record error-path cassettes
- [ ] 8.12 Verify all protocol VCR tests pass in CI replay mode

### 9. L3 VCR Tests — Core Orchestrator

- [ ] 9.1 Create `tests/vcr/test_runloop_streaming.py` — real_streaming_event_sequence, multi_turn_idle_wake, steer_mid_turn, followup_between_turns
- [ ] 9.2 [HUMAN-REQUIRED] Record cassettes for RunLoop streaming tests
- [ ] 9.3 Create `tests/vcr/test_turn_tool_calls.py` — real_tool_call_roundtrip, pre_post_hooks_fire, tool_result_injection, multiple_tools_sequential
- [ ] 9.4 [HUMAN-REQUIRED] Record cassettes for Turn tool call tests
- [ ] 9.5 Create `tests/vcr/test_session_controller.py` — real_lifecycle_create_to_close, priority_routing_asap_vs_when_idle, run_handle_state_transitions
- [ ] 9.6 [HUMAN-REQUIRED] Record cassettes for SessionController tests
- [ ] 9.7 Create `tests/vcr/test_event_bus_sequences.py` — real_event_sequence_publish, scoped_subscription_session, scoped_subtree, replay_buffer
- [ ] 9.8 [HUMAN-REQUIRED] Record cassettes for EventBus tests
- [ ] 9.9 Create `tests/vcr/test_lifecycle_recovery.py` — crash_recovery_mark_interrupted, crash_recovery_retry, tool_execution_log_idempotency, snapshot_replay (P9 pattern)
- [ ] 9.10 [HUMAN-REQUIRED] Record cassettes for lifecycle recovery tests
- [ ] 9.11 Create `tests/vcr/test_delegation.py` — real_subagent_spawn, subagent_streaming_events, subagent_tool_inheritance, nested_delegation
- [ ] 9.12 [HUMAN-REQUIRED] Record cassettes for delegation tests
- [ ] 9.13 Verify all orchestrator VCR tests pass in CI replay mode

### 10. L4 Subprocess E2E Tests

- [ ] 10.1 Create `tests/e2e/__init__.py` and `tests/e2e/conftest.py` with `subprocess_server` fixture: `asyncio.create_subprocess_exec` with `stdout=PIPE`, `stderr=PIPE`, ephemeral port via `socket.bind(("", 0))`, health check polling (5s timeout), teardown via `SIGTERM` → `wait(timeout=5)` → `SIGKILL`, stderr capture for debugging
- [ ] 10.2 Create `tests/e2e/test_acp_subprocess.py` — L4a smoke: server_startup + basic_prompt + server_shutdown (NOT marked `@pytest.mark.slow`); L4b full: multi_turn_conversation, tool_call_e2e, subagent_delegation_e2e, cancellation_e2e (marked `@pytest.mark.slow`)
- [ ] 10.3 Create `tests/e2e/test_opencode_subprocess.py` — L4a smoke: server_startup + basic_prompt + server_shutdown; L4b full: tool_call, session_close, error_paths
- [ ] 10.4 Create `tests/e2e/test_agui_subprocess.py` — L4a smoke: server_startup + event_stream + server_shutdown
- [ ] 10.5 Create `tests/e2e/test_openai_api_subprocess.py` — L4a smoke: server_startup + chat_completion + server_shutdown; L4b full: streaming, multi_turn
- [ ] 10.6 Add subprocess cleanup verification (no orphaned processes after test session)
- [ ] 10.7 Create minimal YAML config fixture for e2e tests — uses `model: test` (TestModel) so no API key is needed. Single agent with bash/read tools.
- [ ] 10.8 Verify L4a smoke tests pass locally with `pytest -m "e2e and not slow"` (~30s)
- [ ] 10.9 Verify L4b full tests pass locally with `pytest -m e2e`
- [ ] 10.10 Add CI note: L4a tests use TestModel (not real_model), so `real_model` auto-skip does NOT apply. L4a should always run when `-m "e2e and not slow"` is selected.

### 11. Fix Broken Subprocess Test

- [ ] 11.1 Audit `tests/servers/acp_server/test_acp_via_acp_snapshots.py` — identify SessionPool refactoring breakage
- [ ] 11.2 Fix or remove the test (if superseded by `tests/e2e/test_acp_subprocess.py`)
- [ ] 11.3 Remove `@pytest.mark.skip` from any tests that are fixed
- [ ] 11.4 Verify fixed tests pass

### 12. CI Pipeline Updates (Phase C)

- [ ] 12.1 Add L4a smoke stage to `pytest.yml`: `pytest -m "e2e and not slow"` (~30s, PR-blocking)
- [ ] 12.2 Create `.github/workflows/e2e-nightly.yml` — nightly cron at 02:00 UTC, runs `pytest -m e2e` (L4a + L4b), reports results
- [ ] 12.3 Verify CI pipeline passes with L4a smoke stage

### 13. Documentation (Phase C)

- [ ] 13.1 Update `tests/AGENTS.md` with VCR recording workflow (step-by-step, marked [HUMAN-REQUIRED])
- [ ] 13.2 Update `tests/AGENTS.md` with test directory structure (including `tests/vcr/`, `tests/e2e/`, `tests/cassettes/`)
- [ ] 13.3 Add "Writing Good Protocol Tests" section to `tests/AGENTS.md` with event sequence assertion examples
- [ ] 13.4 Add "Anti-Patterns" section to `tests/AGENTS.md` (mocking entire pool, no VCR for model-touching tests, skipping L4a for protocol changes, concurrent VCR tests)
- [ ] 13.5 Add L4a/L4b sub-layer explanation to `tests/AGENTS.md`
