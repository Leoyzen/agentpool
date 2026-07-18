## ADDED Requirements

### Requirement: L1-L4 layered testing taxonomy documentation

The `tests/AGENTS.md` SHALL document a 4-layer conceptual testing taxonomy (L1-L4) mapped to pytest markers. L1-L4 are conceptual labels for discussion; the actual pytest markers are `unit`, `integration`, `vcr`, and `e2e`.

The layers SHALL be:
1. **L1 — Unit** (`@pytest.mark.unit`) — Logic-level tests using `TestModel` or `FunctionModel`. No network, no server.
2. **L2 — Integration** (`@pytest.mark.integration`) — Component wiring with real `AgentPool` + `TestModel` or mocked deps. No network.
3. **L3 — VCR** (`@pytest.mark.vcr`) — Real `AgentPool` with model API responses replayed from VCR cassettes. Protocol stack runs for real in-process; only model API HTTP is intercepted.
4. **L4 — Subprocess E2E** (`@pytest.mark.e2e`) — Real `agentpool serve-*` subprocess + protocol client. Highest fidelity, split into L4a smoke (PR-blocking, ~30s) and L4b full (nightly).

#### Scenario: Developer reads testing guidelines

- **WHEN** a developer opens `tests/AGENTS.md` to understand testing requirements
- **THEN** the document SHALL present the 4-layer taxonomy with a table showing: layer name, concept, marker, tools, speed, network dependency, and CI integration
- **AND** provide a decision table for "which layers do I need for my feature?"

### Requirement: Mandatory test layers by feature type

The `tests/AGENTS.md` SHALL specify which testing layers are mandatory for different feature types. A feature is not considered complete until all mandatory layers have tests.

- **New capability** (e.g., new `AbstractCapability` subclass): L1 + L2
- **New protocol handler** (e.g., new event type in ACP/OpenCode): L1 + L2 + L3 + L4a (recommended)
- **Bug fix**: L1 (reproducing the bug) + the layer where the bug manifested
- **Refactor**: Existing tests pass + L1 for any new logic
- **New agent type**: L1 + L2 + L3 + L4a (recommended)

#### Scenario: New protocol handler without L3 test

- **WHEN** a PR adds a new ACP event type but has no L3 VCR test exercising the event through the ACP server
- **THEN** the PR review SHALL flag the missing L3 test as a blocker

### Requirement: VCR recording workflow documentation

The `tests/AGENTS.md` SHALL document the step-by-step VCR recording workflow. All cassette recording steps SHALL be marked as [HUMAN-REQUIRED] because they require real API keys that AI agents do not possess.

#### Scenario: Developer records a new cassette

- **WHEN** a developer follows the VCR recording workflow documented in `tests/AGENTS.md`
- **THEN** the cassette SHALL be created at the expected path
- **AND** the cassette SHALL pass sanitization checks (no credentials, decoded bodies)

### Requirement: Test marker conventions

The `tests/AGENTS.md` SHALL document all test markers with their purpose, CI behavior, and usage examples:

**Layer markers** (one primary per test):
| Marker | Layer | CI Default |
|--------|-------|------------|
| `@pytest.mark.unit` | L1 | ✅ Run |
| `@pytest.mark.integration` | L2 | ✅ Run |
| `@pytest.mark.vcr` | L3 | ✅ Run |
| `@pytest.mark.e2e` | L4 | ❌ Skip (L4a: optional PR stage, L4b: nightly) |

**Orthogonal markers** (can combine with any layer):
| Marker | Purpose | CI Default |
|--------|---------|------------|
| `@pytest.mark.slow` | >1s | ❌ Skip |
| `@pytest.mark.flaky` | Intermittent | ✅ Run (deflake and remove) |
| `@pytest.mark.incompatible_with_thinking` | Fails with thinking models | ✅ Run |
| `@pytest.mark.snapshot` | Syrupy snapshots (renamed from `acp_snapshot`) | ❌ Skip |
| `@pytest.mark.security` | Security tests | ✅ Run |

**L4 sub-qualifiers** (only with `@pytest.mark.e2e`):
| Marker | Purpose | CI Default |
|--------|---------|------------|
| `@pytest.mark.real_model` | Real API calls (replaces `requires_openai_key`) | ❌ Auto-skip if no key |
| `@pytest.mark.real_mcp` | Real MCP server | ❌ Skip |

**Marker stacking:** A test carries exactly ONE primary layer marker. L3 VCR tests that exercise protocol integration MAY additionally carry `@pytest.mark.integration` as a secondary marker for filtering. The primary marker determines CI stage assignment.

### Requirement: L2 tests must use real AgentPool + TestModel

All L2 integration tests SHALL use a real `AgentPool` built from a minimal YAML config with `TestModel` as the model. `MagicMock(pool)` or `MagicMock(agent)` SHALL NOT be used in L2 tests. This eliminates false confidence where mocked pools pass while real integration breaks.

#### Scenario: L2 test with real pool

- **WHEN** an L2 test needs to verify protocol handler behavior
- **THEN** the test SHALL use the `minimal_pool` fixture (real `AgentPool` + `TestModel`)
- **AND** the test SHALL NOT use `MagicMock` for pool or agent dependencies

#### Scenario: CI detects MagicMock in L2 test

- **WHEN** a CI check scans `@pytest.mark.integration` test files and finds `MagicMock` usage for pool/agent
- **THEN** the check SHALL warn (Phase A) or fail (Phase B+) that the test should use real pool + TestModel

#### Scenario: Developer uses correct marker

- **WHEN** a developer writes a VCR test and marks it with `@pytest.mark.vcr`
- **THEN** the test SHALL run in the default CI pipeline
- **AND** the cassette SHALL be replayed (not recorded) in CI
