## ADDED Requirements

### Requirement: Protocol-level VCR tests

The system SHALL provide VCR-backed protocol tests in `tests/vcr/` that wire a real `AgentPool` instance (via `vcr_pool` fixture) with VCR-replayed model API responses through each protocol server's actual transport. VCR intercepts model provider HTTP calls only; the protocol stack (EventBus, SessionController, server handlers, transport) runs for real in-process.

"Actual server transport" means: real client → real socket/stdio/HTTP connection → real server handler. NOT direct Python function calls to handler methods.

For ACP, the in-process connection SHALL use paired `AgentSideConnection`/`ClientSideConnection` via `asyncio.StreamReader`/`StreamWriter` pipe adapters (reusing the pattern from existing `test_rpc.py`).

#### Scenario: ACP protocol VCR test

- **WHEN** a test in `tests/vcr/test_acp_protocol.py` runs with `@pytest.mark.vcr`
- **THEN** the test SHALL build a real `AgentPool` via `vcr_pool` fixture
- **AND** connect the ACP server in-process via paired pipe connections (NOT subprocess)
- **AND** execute a complete session: initialize → send prompt → receive streaming response → close
- **AND** assert that `RunStartedEvent`, `PartStartEvent`, `PartDeltaEvent`, `PartEndEvent`, and `StreamCompleteEvent` are emitted in correct order
- **AND** the model API HTTP call SHALL be replayed from VCR cassette (not real API)

#### Scenario: OpenCode protocol VCR test

- **WHEN** a test in `tests/vcr/test_opencode_protocol.py` runs with `@pytest.mark.vcr`
- **THEN** the test SHALL build a real `AgentPool` via `vcr_pool` fixture
- **AND** start the OpenCode server in-process (FastAPI `TestClient` with real pool, NOT mocked pool)
- **AND** execute a complete session via the OpenCode API
- **AND** assert that SSE events are correctly formatted and ordered
- **AND** the model API HTTP call SHALL be replayed from VCR cassette
- **AND** assert that SSE events are correctly formatted and ordered

#### Scenario: AG-UI protocol VCR test

- **WHEN** a test in `tests/vcr/test_agui_protocol.py` runs with `@pytest.mark.vcr`
- **THEN** the test SHALL build a real `AgentPool` and start the AG-UI server
- **AND** execute a complete session via the AG-UI client
- **AND** assert that AG-UI events match the expected protocol schema

#### Scenario: OpenAI API protocol VCR test

- **WHEN** a test in `tests/vcr/test_openai_api_protocol.py` runs with `@pytest.mark.vcr`
- **THEN** the test SHALL build a real `AgentPool` and start the OpenAI-compatible API server
- **AND** send a chat completion request
- **AND** assert that the response matches the OpenAI API response schema

### Requirement: VCR test with tool calls

The system SHALL provide VCR tests that exercise tool call flows through protocol servers. The test SHALL verify that tool call start/complete events are correctly emitted through the protocol transport.

#### Scenario: Tool call through ACP protocol

- **WHEN** a VCR test sends a prompt that triggers a tool call
- **THEN** the ACP client SHALL receive `ToolCallStartEvent` and `ToolCallCompleteEvent`
- **AND** the tool result SHALL be included in the final response

#### Scenario: Tool call through OpenCode protocol

- **WHEN** a VCR test sends a prompt that triggers a tool call via the OpenCode server
- **THEN** the SSE stream SHALL include tool call events in the correct format
- **AND** the tool result SHALL be reflected in the final message

### Requirement: VCR test with subagent delegation

The system SHALL provide VCR tests that exercise subagent delegation through protocol servers. The test SHALL verify that subagent events (spawn start, subagent stream, spawn complete) are correctly propagated.

#### Scenario: Subagent delegation through ACP

- **WHEN** a VCR test sends a prompt that triggers subagent delegation
- **THEN** the ACP client SHALL receive `SpawnSessionStartEvent` and `SpawnSessionCompleteEvent`
- **AND** the subagent's response SHALL be included in the final result

### Requirement: Subprocess e2e test infrastructure

The system SHALL provide subprocess e2e test infrastructure in `tests/e2e/` that spawns real `agentpool serve-*` processes. Tests SHALL be marked with `@pytest.mark.e2e` and split into two sub-layers:

- **L4a — Smoke** (NOT marked `@pytest.mark.slow`): Server startup + 1 basic prompt + shutdown. ~30s. Runs on PRs.
- **L4b — Full** (marked `@pytest.mark.slow`): Multi-turn, tool calls, subagent, cancellation, error paths. 5-30min. Runs nightly.

#### Scenario: Subprocess e2e test lifecycle

- **WHEN** a test in `tests/e2e/` runs
- **THEN** the test SHALL spawn `agentpool serve-acp` (or `serve-opencode`) as a subprocess via `asyncio.create_subprocess_exec` with `stdout=PIPE` and `stderr=PIPE`
- **AND** assign an ephemeral port via `socket.bind(("", 0))` (for HTTP-based servers)
- **AND** poll a health check endpoint until the server is ready (5s timeout)
- **AND** connect via a protocol client through real socket/stdio/HTTP transport
- **AND** capture stderr for debugging on failure
- **AND** terminate the subprocess in teardown via `SIGTERM` → `wait(timeout=5)` → `SIGKILL` fallback

#### Scenario: Subprocess cleanup on test failure

- **WHEN** a subprocess e2e test fails or raises an exception
- **THEN** the subprocess SHALL still be terminated in the teardown phase
- **AND** stderr SHALL be logged for debugging
- **AND** no orphaned processes SHALL remain after the test session

### Requirement: E2E marker and CI integration

The system SHALL register `@pytest.mark.e2e` as a pytest marker. The default `pyproject.toml` `addopts` SHALL exclude e2e tests from the default run. L4a smoke tests SHALL run in a dedicated PR CI stage (`pytest -m "e2e and not slow"`). L4b full tests SHALL run in a nightly CI workflow (`e2e-nightly.yml`).

#### Scenario: Default pytest run excludes e2e

- **WHEN** a developer runs `pytest` without explicit marker selection
- **THEN** no `@pytest.mark.e2e` tests SHALL run

#### Scenario: PR CI runs L4a smoke tests

- **WHEN** a PR is opened
- **THEN** `pytest -m "e2e and not slow"` SHALL run L4a smoke tests (~30s)
- **AND** the PR SHALL be blocked if L4a smoke tests fail

#### Scenario: Nightly CI runs L4b full tests

- **WHEN** the nightly CI workflow triggers
- **THEN** `pytest -m e2e` SHALL run all subprocess e2e tests
- **AND** test results SHALL be reported to the CI dashboard
