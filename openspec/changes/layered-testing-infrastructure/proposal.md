## Why

AgentPool has 720+ unit tests that consistently pass, yet ACP/OpenCode/AG-UI protocol endpoints break repeatedly in real usage. The root cause is a missing testing middle layer: there is zero VCR/cassette infrastructure (no real API response replay) and zero subprocess-level e2e tests (the only attempt, `test_acp_via_acp_snapshots.py`, is fully skipped). All "integration" tests use mocked pools/agents, so protocol-level event flows, streaming sequences, tool-call wiring, and subagent delegation through real server processes are never exercised in CI. This creates a false confidence: internal logic is well-tested, but the integration surface where components actually connect is untested.

## What Changes

- **VCR cassette infrastructure**: Introduce `pytest-recording` + `vcrpy` with the same pattern used by pydantic-ai: `ALLOW_MODEL_REQUESTS = False` global gate, `@pytest.mark.vcr` marker, auto-named cassettes, credential scrubbing, `--strict-vcr-cassette-usage` CI enforcement, and cassette hygiene checking.
- **Protocol-level VCR tests**: For each protocol server (ACP, OpenCode, AG-UI, OpenAI API), add tests that wire a real `AgentPool` instance with a VCR-replayable model through the actual server transport, verifying complete event flows (session lifecycle, streaming, tool calls, subagent delegation, error handling).
- **Subprocess e2e test layer**: Fix the broken `test_acp_via_acp_snapshots.py` and create a new `tests/e2e/` directory that spawns real `agentpool serve-acp` / `serve-opencode` processes, connects via protocol clients, and exercises end-to-end conversation flows. Marked `@pytest.mark.e2e` (excluded from default CI run, run nightly or on-demand).
- **Real-model integration test markers**: Retire `@pytest.mark.requires_openai_key` and replace with `@pytest.mark.real_model` (an L4 sub-qualifier, auto-skipped when `OPENAI_API_KEY` is unset). Add `@pytest.mark.real_mcp` for tests requiring real MCP servers.
- **Test pyramid documentation**: Update `AGENTS.md` with layered testing guidelines, VCR recording workflow, and mandatory test-layer requirements for new features.
- **CI pipeline updates**: Add VCR replay stage to `pytest.yml`, add nightly e2e workflow, add cassette hygiene check step.

## Capabilities

### New Capabilities

- `vcr-testing`: VCR cassette infrastructure for recording and replaying real model API responses in tests. Covers `ALLOW_MODEL_REQUESTS` gate, cassette storage/naming/sanitization, `--record-mode` workflow, and `--strict-vcr-cassette-usage` enforcement.
- `protocol-e2e-testing`: End-to-end protocol testing via real server processes (subprocess spawning) and protocol-level VCR tests (real pool + VCR model + server transport). Covers ACP, OpenCode, AG-UI, and OpenAI API protocols.
- `test-layer-guidelines`: Mandatory testing layer requirements for new features, VCR recording workflow documentation, and test marker conventions.

### Modified Capabilities

(None — this is purely additive testing infrastructure.)

## Impact

- **Dependencies**: Add `pytest-recording`, `vcrpy`, `dirty-equals`, `inline-snapshot` to dev dependencies.
- **CI**: `pytest.yml` gains a VCR replay stage and L4a smoke stage; new nightly e2e workflow; cassette hygiene check steps added.
- **Test structure**: New `tests/cassettes/` directory for VCR cassettes; new `tests/e2e/` directory for subprocess tests; new `tests/vcr/` directory for VCR-based protocol tests.
- **conftest.py**: Root conftest gains `ALLOW_MODEL_REQUESTS` gate with httpx transport enforcement, `allow_model_requests` fixture, VCR configuration, cassette sanitization hooks, `CassetteContext` for wire-level assertions, `track_httpx_clients`/`close_httpx_clients` for resource leak prevention.
- **Production code**: Minimal change — `ALLOW_MODEL_REQUESTS` enforcement may require a hook in httpx client creation or a test-only monkeypatch. No behavior change for non-test usage.
- **tests/AGENTS.md**: Comprehensive testing guide with L1-L4 taxonomy, marker reference, VCR recording workflow, and mandatory layer requirements.
- **Phasing**: Implementation split into 3 phases (A: marker+consolidation, B: VCR infra+initial L3, C: L4 e2e+remaining L3) to reduce risk and merge-conflict potential.
