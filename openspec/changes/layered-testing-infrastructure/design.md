## Context

AgentPool currently has ~850+ tests. A systematic survey of the entire test suite reveals:

**Current distribution (by conceptual layer):**
- **L1 (Unit)**: ~470 files — logic-level tests with `TestModel`/`FunctionModel`/mocks
- **L2 (Integration)**: ~130 files — component integration with mocked pools/agents, FastAPI `TestClient`
- **L3 (VCR)**: 0 files — **completely empty**
- **L4 (Subprocess E2E)**: 1 file (`test_acp_via_acp_snapshots.py`) — **all tests `@pytest.mark.skip`**, broken after SessionPool refactoring

**Marker hygiene:**
- 109 files have **NO markers at all** (capabilities 22, config 9, sessions 25, skills 7, teams 4, manifest 6, host 3, running 3, prompts 3, etc.)
- `acp_snapshot` marker used by 2 files (should be renamed to `snapshot`)
- `requires_openai_key` used by 2 files (should be retired in favor of `real_model`)
- `incompatible_with_thinking` declared but 0 files use it
- `flaky` used by 5 files (should be deflaked)
- `tests/resource_providers/` is dead code (delete)

**Structural redundancy:**
- ACP: 3 resume files → 1, 3 websocket disconnect files → 1, 2 turn_complete files → 1
- OpenCode: 5 subagent files → 1, 3 question files → 1, 3 title files → 1, 3 ensure_session files → 1, 5 session pool files → 1, 2 event bridge files → 1, event adapter+conversion → 1
- Orchestrator: EventBus variants, RunHandle variants, session close/checkpoint, cancel variants, SessionPool e2e, resume variants — each group should merge
- Skills: scattered across 3 dirs (`tests/skills/`, `tests/test_skills/`, `tests/test_config/test_skills_config.py`)
- 8 root test files need relocation

**Protocol coverage gaps:**
- AG-UI: 1 file only (model conversion L1). ZERO protocol transport tests.
- OpenAI API: ZERO test files. Directory doesn't exist.
- OpenCode: 70 files but ALL L1/L2. Zero L3/L4.
- ACP: 49 files, 27 L1 + 19 L2 + 1 L4 (skipped). Zero L3.

**Critical insight from review:** The existing L2 tests that use `MagicMock` pools/agents are the primary false-confidence source — they pass while real integration breaks because the mocks don't exercise real `AgentPool` wiring, `EventBus` routing, or `SessionController` lifecycle. L3 (VCR) tests address this by using a real `AgentPool` with VCR-replayed model responses.

**Reference:** pydantic-ai has 187 VCR cassette files across 14 modules, with 10 concrete VCR patterns and 5 subprocess patterns (see VCR Pattern Catalog below).

## Phasing

This change is large. To reduce risk and merge-conflict potential, implementation is split into 3 phases that can be separate PRs:

**Phase A — Marker & Consolidation & L2 Migration** (low risk, high value, no new infrastructure):
- Marker migration (rename, retire, add new markers)
- Mark 109 unmarked files
- File consolidation (14+ merge groups)
- Delete dead code (`tests/resource_providers/`)
- Update `tests/AGENTS.md` with L1-L4 taxonomy
- CI: marker hygiene check

**Phase B — VCR Infrastructure + L3 Tests** (medium risk, requires human for cassette recording):
- VCR infrastructure (dependencies, conftest, cassette directory)
- `ALLOW_MODEL_REQUESTS` enforcement mechanism
- L3 tests for native agent (5-10 cassettes to start, prove workflow)
- L3 tests for ACP protocol (highest-value protocol)
- CI: VCR replay stage, cassette hygiene check
- [HUMAN-REQUIRED]: Record initial cassettes with real `OPENAI_API_KEY`

**Phase C — L4 E2E + Remaining L3** (builds on Phase B):
- L4a smoke tests (fast, ~30s, runs on PRs)
- L4b full suite (nightly)
- L3 tests for OpenCode, AG-UI, OpenAI API protocols
- L3 tests for core orchestrator areas
- Fix broken `test_acp_via_acp_snapshots.py`
- [HUMAN-REQUIRED]: Record remaining cassettes

## Goals / Non-Goals

**Goals:**
- Establish L1-L4 conceptual layer taxonomy with clear marker mapping.
- Migrate all 109 unmarked tests to appropriate layers.
- Rename `acp_snapshot` → `snapshot`, retire `requires_openai_key` → `real_model`.
- Consolidate redundant test files (14+ merge groups).
- Build VCR infrastructure (L3 layer) with `pytest-recording` + `vcrpy`.
- Wire `ALLOW_MODEL_REQUESTS = False` enforcement into httpx transport layer.
- Design and implement L3 test cases for native agent + ACP protocol (Phase B), remaining protocols + core orchestrator (Phase C).
- Add L4a smoke tests (fast, PR-blocking) and L4b full suite (nightly).
- Fix and restore broken L4 subprocess e2e test.
- Document everything in `tests/AGENTS.md`.

**Non-Goals:**
- Achieving 100% branch coverage.
- Adding mutation testing (`mutmut`).
- Recording cassettes for every provider/model combination (start with `gpt-4o-mini`, 6-7 cassettes in Phase B).
- Rewriting all existing L1 tests (only consolidate redundant L2 files and migrate L2 protocol tests from MagicMock to real AgentPool + TestModel).
- Modifying production code, **except**: (a) the `ALLOW_MODEL_REQUESTS` enforcement hook (test-safety mechanism), and (b) bug fixes revealed by L2 migration when real AgentPool + TestModel surfaces integration bugs that MagicMock was hiding. These bug fixes are scoped to the specific issue discovered and do not include unrelated refactoring.

## Decisions

### D1: L1-L4 Conceptual Layer Taxonomy

**Decision:** Adopt a 4-layer conceptual taxonomy (L1-L4) mapped to pytest markers:

| Conceptual Layer | Primary Marker | Purpose | Network | CI |
|---|---|---|---|---|
| L1 — Unit | `@pytest.mark.unit` | Logic, branches, data transforms | None | ✅ |
| L2 — Integration | `@pytest.mark.integration` | Component wiring (may use TestModel or mocks) | None | ✅ |
| L3 — VCR | `@pytest.mark.vcr` | Real model API responses replayed from cassettes | None (replay) | ✅ |
| L4 — Subprocess E2E | `@pytest.mark.e2e` | Real server process + protocol client | Optional | L4a: ✅ smoke, L4b: ❌ nightly |

L1-L4 are **conceptual labels** for discussion. The actual pytest markers are `unit`, `integration`, `vcr`, `e2e`.

**Marker stacking:** A test carries exactly ONE primary layer marker. However, `@pytest.mark.vcr` tests that also exercise protocol integration SHOULD additionally carry `@pytest.mark.integration` as a secondary marker for filtering purposes. The primary marker determines CI stage assignment; secondary markers are for selective filtering only. This resolves the contradiction: layer markers are not "mutually exclusive" in the absolute sense, but each test has exactly one **primary** layer that determines its CI behavior.

### D2: Orthogonal Markers

| Marker | Purpose | Default CI | Action |
|---|---|---|---|
| `@pytest.mark.slow` | Test takes >1s | ❌ Skip | Include with `-m slow` |
| `@pytest.mark.flaky` | Known intermittent failure | ✅ Run | Deflake and remove marker |
| `@pytest.mark.incompatible_with_thinking` | Fails with thinking models | ✅ Run | Skip conditionally in thinking-model CI |
| `@pytest.mark.snapshot` | Syrupy snapshot tests | ❌ Skip | Regenerate on demand |
| `@pytest.mark.security` | Security-focused tests | ✅ Run | Orthogonal, not layer-specific |

**L4 sub-qualifiers** (only meaningful with `@pytest.mark.e2e`):

| Sub-qualifier | Purpose | Requires |
|---|---|---|
| `@pytest.mark.real_model` | Makes real model API calls | API key in env (auto-skip if unset) |
| `@pytest.mark.real_mcp` | Connects to real MCP server | MCP server running |

### D3: Marker Renames and Retirements

- `acp_snapshot` → `snapshot` (generalize to all syrupy-based snapshot tests)
- `requires_openai_key` → **retire** (replaced by `real_model` sub-qualifier; auto-skip when `OPENAI_API_KEY` unset)
- `security` → **keep** (orthogonal)
- `tests/resource_providers/` → **delete** (dead code)

### D4: Default CI addopts

```toml
addopts = ["-m", "not slow and not snapshot and not e2e and not real_model and not real_mcp"]
```

L4a smoke tests are explicitly included via a separate CI step: `pytest -m "e2e and not slow"`.

### D5: ALLOW_MODEL_REQUESTS Enforcement

**Decision:** The `ALLOW_MODEL_REQUESTS = False` constant in `tests/conftest.py` MUST be enforced at the httpx transport level, not just as a documentation convention.

**Mechanism:** A pytest autouse fixture installs an httpx `MockTransport` handler that rejects all requests when `ALLOW_MODEL_REQUESTS` is `False`. The `allow_model_requests` fixture temporarily disables this handler. VCR (`pytest-recording`) intercepts at a higher level, so VCR tests are unaffected.

```python
# tests/conftest.py
ALLOW_MODEL_REQUESTS = False

@pytest.fixture(autouse=True)
def _block_model_requests(monkeypatch):
    if not ALLOW_MODEL_REQUESTS:
        # Install httpx transport blocker
        def _blocking_handler(request):
            raise RuntimeError(
                f"Real model API call blocked (ALLOW_MODEL_REQUESTS=False). "
                f"Use @pytest.mark.vcr or the allow_model_requests fixture. "
                f"URL: {request.url}"
            )
        # Patch httpx.AsyncClient transport
        ...
```

This prevents accidental real API calls from any code path that uses httpx (OpenAI, Anthropic, etc.).

### D6: VCR Scope — Model API HTTP Only

**Critical clarification:** VCR intercepts **model provider API HTTP calls** (e.g., `POST https://api.openai.com/v1/chat/completions`). VCR does NOT intercept protocol transport (ACP stdio JSON-RPC, OpenCode SSE, AG-UI HTTP).

In L3 tests:
- The **protocol stack runs for real** in-process (real ACP server, real OpenCode server, real EventBus, real SessionController).
- The **model API is replayed** from VCR cassettes.
- The protocol client connects via **real transport** (paired pipe for ACP, FastAPI `TestClient` for OpenCode, HTTP client for AG-UI/OpenAI API).

"Actual server transport" means: real client → real socket/stdio/HTTP connection → real server handler. NOT direct Python function calls to handler methods.

### D7: ACP In-Process Connection Method

**Decision:** L3 ACP tests use the paired pipe pattern from existing `test_rpc.py`:

```python
# Create paired StreamReader/StreamWriter via pipe
client_reader, client_writer = ...
server_reader, server_writer = ...

# Client side
client_conn = ClientSideConnection(reader=client_reader, writer=client_writer)

# Server side
server_conn = AgentSideConnection(reader=server_reader, writer=server_writer)
# Wire server_conn to ACP handler with real AgentPool
```

This exercises the real JSON-RPC serialization/deserialization, framing, and event conversion without spawning a subprocess.

### D8: VCR Pattern Catalog (P1-P10)

**Decision:** Define the 10 VCR patterns referenced in task descriptions. These are adapted from pydantic-ai's 187-cassette codebase:

| Pattern | Name | Description | Use When |
|---|---|---|---|
| **P1** | Basic completion | Single model call, assert response content/structure | Verifying model integration works at all |
| **P2** | Tool-calling round trip | Model requests tool → tool executes → model uses result → final response | Testing tool call wiring, tool schema compliance |
| **P3** | Direct SDK raw call | Bypass agent, call model SDK directly with VCR | Testing model client configuration, provider quirks |
| **P4** | Multi-turn with history | Multiple turns, message_history passed between turns | Testing conversation state, context retention |
| **P5** | Parametrized cross-provider | Same test parametrized over multiple providers | Testing provider compatibility (not for initial cassettes) |
| **P6** | Streaming events | `agent.iter()` + `node.stream()`, assert event sequence | Testing streaming event order, delta aggregation |
| **P7** | Cross-provider replay | Assert trace/span structure across providers | Testing observability, telemetry wiring |
| **P8** | Eval-driven | Use eval framework to drive VCR scenarios | Testing agent quality, not just structure |
| **P9** | Durable workflow | VCR + lifecycle journal/snapshot, test crash recovery | Testing RunLoop recovery, tool idempotency |
| **P10** | Multi-modal | Image/audio input with full message snapshot | Testing multi-modal content handling |

**Initial cassette set (Phase B, 5-10 cassettes):** P1 (native basic), P2 (native tool call), P6 (native streaming), P1 (ACP basic), P2 (ACP tool call). This proves the workflow before scaling.

### D9: Missing pydantic-ai Patterns to Port

| Pattern | Source | Purpose |
|---|---|---|
| `CassetteContext` | pydantic-ai `tests/conftest.py` | Read cassette YAML from disk after recording, assert wire-level request/response bodies (`verify_contains`, `verify_ordering`) |
| `track_httpx_clients` / `close_httpx_clients` | pydantic-ai `tests/conftest.py` | Track all httpx client instances, close them after each test to prevent resource leaks |
| `inline-snapshot` | pydantic-ai dev dep | Complementary to `dirty-equals`; auto-updates snapshot assertions during `--record-mode=once` |
| `disable_ssrf_protection_for_vcr` | pydantic-ai `tests/conftest.py` | Patch URL validation so VCR can match cassette URLs without SSRF check failures |

### D10: L4 Sub-layering — L4a Smoke + L4b Full

**Decision:** Split L4 into two sub-layers:

| Sub-layer | Marker | Scope | Speed | CI |
|---|---|---|---|---|
| **L4a** — Smoke | `@pytest.mark.e2e` + `@pytest.mark.slow` NOT set | Server startup + 1 basic prompt + shutdown | ~30s | ✅ PR (optional stage) |
| **L4b** — Full | `@pytest.mark.e2e` + `@pytest.mark.slow` | Multi-turn, tool calls, subagent, cancellation, error paths | 5-30min | ❌ Nightly |

L4a catches "server won't start" and "basic prompt fails" regressions within minutes of a PR. L4b catches deeper integration issues overnight.

**CI integration:**
- PR pipeline: `pytest -m "e2e and not slow"` (L4a only, ~30s)
- Nightly: `pytest -m e2e` (L4a + L4b)

### D11: Test File Consolidation Plan

Same as before — 14+ merge groups. See tasks.md for the full list.

### D12: VCR Test Design — Initial Set (Phase B)

| Test File | Protocol | Test Cases | VCR Pattern | Cassettes |
|---|---|---|---|---|
| `test_native_basic.py` | Native | basic_completion | P1 | 1 |
| `test_native_tool_call.py` | Native | tool_call_roundtrip | P2 | 1 |
| `test_native_streaming.py` | Native | streaming_event_sequence | P6 | 1 |
| `test_acp_protocol.py` | ACP | session_init, basic_completion, streaming_events, tool_call_flow | P1, P2, P6 | 3-4 |

**Total Phase B: 6-7 cassettes.** This proves the VCR workflow end-to-end before scaling.

**Phase C expansion** adds ACP tool calls, subagent, OpenCode, AG-UI, OpenAI API, and core orchestrator tests — bringing total to ~20-25 cassettes (not 50+).

### D13: Error-Path Cassettes

Each protocol L3 test set SHALL include error-path cassettes:
- Rate limit (429) response
- Server error (500) response
- Malformed stream (interrupted SSE/JSON-RPC)

These are recorded once and replayed. They test that the protocol server correctly propagates errors to the client.

### D14: Subprocess E2E Infrastructure Details

- **Process spawn:** `asyncio.create_subprocess_exec(..., stdout=PIPE, stderr=PIPE)`
- **Stderr capture:** Capture and log stderr on failure for debugging
- **Health check:** Poll health endpoint (or stdio readiness signal) with 5s timeout
- **Port assignment:** `socket.bind(("", 0))` to get ephemeral port, pass to server via `--port` arg
- **Graceful shutdown:** Send `SIGTERM` → `wait(timeout=5)` → `SIGKILL` fallback
- **Signal handling:** On Windows, use `terminate()` instead of `SIGTERM`
- **Cleanup verification:** After all e2e tests, verify no orphaned `agentpool` processes

### D15: `vcr_pool` Fixture (renamed from `real_pool`)

**Decision:** Rename the fixture from `real_pool` to `vcr_pool` to clarify its boundary: it's a real `AgentPool` instance, but model responses come from VCR cassettes, not real API calls.

```python
@pytest.fixture
async def vcr_pool(tmp_path):
    """Real AgentPool with VCR-replayed model responses.
    
    The pool, agents, capabilities, EventBus, SessionController are all real.
    Only the model API HTTP calls are intercepted by VCR.
    """
    config = write_test_config(tmp_path)
    pool = AgentPool(config)
    await pool.__aenter__()
    yield pool
    await pool.__aexit__(None, None, None)
```

### D16: CI Check for VCR Tests Without Cassettes

**Decision:** Add inverse hygiene check: a script that finds `@pytest.mark.vcr` tests WITHOUT corresponding cassette files. This catches tests that were marked VCR but never had cassettes recorded.

```bash
python tests/check_vcr_tests.py  # Fails if any @pytest.mark.vcr test has no cassette
```

### D17: L4a Model Strategy

**Decision:** L4a smoke tests use `TestModel` as the model in the subprocess YAML config. The subprocess `agentpool serve-*` process loads a YAML config that specifies `model: test` (pydantic-ai's `TestModel`). This requires NO API key and produces deterministic responses.

**Rationale:** L4a tests verify process startup, transport connectivity, and basic protocol handshake — NOT model behavior. `TestModel` is sufficient: it returns a deterministic `(0, 'a')` text response that exercises the full event pipeline (RunStarted → PartStart → PartDelta → PartEnd → StreamComplete) without any network dependency.

**CI integration note:** `pytest -m "e2e and not slow"` overrides the default `addopts` `-m` filter. The `real_model` auto-skip fixture still applies (if a test is marked `@pytest.mark.real_model` and no `OPENAI_API_KEY` is set, it skips). L4a tests should NOT be marked `real_model` — they use `TestModel` only.

**Alternatives considered:**
- *VCR cross-process*: Not possible — VCR intercepts httpx in the test process, but the subprocess has its own httpx instance. VCR cannot intercept cross-process.
- *Real model with API key*: Makes L4a depend on `OPENAI_API_KEY` availability in CI. Fragile and unnecessary for smoke testing.

## Risks / Trade-offs

- **[Cassette recording is human-only]** → AI agents cannot record cassettes (no API keys). All recording tasks are marked [HUMAN-REQUIRED]. Mitigation: Start with 6-7 cassettes to prove workflow; provide detailed recording instructions.
- **[Merge risk]** → Merging test files may lose isolation. Mitigation: Clear section headers in merged files; preserve all test function names.
- **[Marker migration errors]** → 109 files misclassified. Mitigation: Automated heuristic check; separate PR (Phase A) to isolate blast radius.
- **[Cassette maintenance]** → Cassettes stale when APIs change. Mitigation: `--record-mode=once`; weekly cron re-records.
- **[Subprocess flakiness]** → Process startup races, port conflicts. Mitigation: Ephemeral ports, health check, proper teardown, stderr capture.
- **[L2 false confidence — RESOLVED]** → User decided: ALL L2 MagicMock pool/agent tests migrate to real AgentPool + TestModel. New task group 2b handles this. **Migration is NOT purely mechanical** — codebase evidence shows 116 files use MagicMock (1669 matches), 68 files use `side_effect` (171 matches), 77 files use `call_args`/`assert_called` (419 matches). Tests fall into three categories: (a) mechanically migratable (swap fixture, TestModel replaces return values), (b) requires assertion rewrite (side_effect error injection → TestModel custom error sequences, call_args verification → event-based assertions), (c) should remain L1 with targeted mocks (pure logic tests that mock a single collaborator, not the pool). Task 2b.2 categorizes all files before migration begins.
- **[VCR + concurrent requests]** → VCR cassettes are sequential; concurrent requests race on playhead. Mitigation: No concurrent-session VCR tests. Concurrent behavior tested in L4 subprocess e2e instead.
- **[Plan size]** → 3 phases, 100+ tasks. Mitigation: Phasing allows independent PRs; Phase A is marker+consolidation+L2 migration (no new infra), Phase B is the critical VCR infrastructure, Phase C is expansion.

## Migration Plan

**Phase A (Marker & Consolidation & L2 Migration):** Marker migration, file consolidation, AND L2 MagicMock→real pool migration. No new infrastructure. CI passes unchanged. Separate PR to minimize merge conflicts.

**Phase B (VCR Infrastructure + Initial L3):** Add dependencies, conftest, enforcement. Record 5-6 initial cassettes [HUMAN]. Add VCR CI stage. New tests are additive.

**Phase C (L4 E2E + Remaining L3):** Build on Phase B. Add L4a smoke (PR-blocking, required) and L4b full (nightly). Record remaining cassettes [HUMAN]. Fix broken subprocess test.

**Rollback:** All changes are additive or reorganizational. No production code changes except the `ALLOW_MODEL_REQUESTS` enforcement hook (which is a test-safety mechanism, not a behavior change).

## Resolved Questions

1. **Bug enumeration:** User trusts pydantic-ai's proven VCR pattern. Existing red tests in the repo serve as historical bug reproductions. No explicit enumeration needed.
2. **L2 migration scope:** **ALL L2 tests using MagicMock pool/agent SHALL migrate to real AgentPool + TestModel.** This is a new task group (2b) to root out false-confidence at the source. **Migration is NOT purely mechanical** — tests are categorized into: (a) mechanically migratable (fixture swap), (b) requires assertion rewrite (side_effect/call_args patterns), (c) should remain L1 with targeted mocks (single-collaborator mocks, not pool-level). Task 2b.2 performs this categorization before migration begins. Bug fixes revealed by migration are in scope.
3. **L4a on every PR:** **Required.** L4a smoke (~30s) runs on every PR and blocks merge. Requires `agentpool` binary installed in CI (already available via `uv sync`).
4. **Reference model:** **`gpt-4o-mini`** for all cassettes. Low cost, stable API, sufficient for streaming/tool-call/structured-output format coverage.
5. **Change splitting:** **One OpenSpec change, 3 phases, separate PRs per phase.** Tracking simplicity outweighs the benefit of splitting into multiple changes.
