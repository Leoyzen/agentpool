# RFC-0034: ACP Session Config Options Unification

## TL;DR

> **Quick Summary**: Implement RFC-0034 to unify ACP and OpenCode model list sources, expose agent roles as switchable config options, and add `providers/*` ACP protocol support — enabling IDE users to select LLM providers and switch agent roles within a single ACP session.
>
> **Deliverables**:
> - `acp/schema/providers.py` — ProviderInfo, LlmProtocol, ProviderStatus type definitions
> - `acp_server/provider_router.py` — ProviderRouter with override/disable/capability support
> - `shared/model_utils.py` — `build_model_state_for_acp()` shared helper
> - Modified `acp_agent.py` — inverted model state logic, agent role swap, config option extensions
> - Modified `acp/schema/capabilities.py` — `providers` capability flag
> - Modified `config_routes.py` — dynamic `/mode` route
>
> **Estimated Effort**: Large (~450 lines, 4 phases)
> **Parallel Execution**: YES — 4 waves (pre-research → Phase 0 foundation → Phase 1∥Phase 2 → Phase 3)
> **Critical Path**: Pre-research → Phase 0 → Phase 1∥Phase 2 → Phase 3 → Final Verification

---

## Context

### Original Request
Implement `docs/rfcs/draft/RFC-0034-acp-session-config-options-unified.md` — a 1257-line RFC proposing Option 2 (unified data source + complete Config Options alignment) across 4 implementation phases.

### Interview Summary
**Key Discussions**:
- **Decision**: Option 2 (统一数据来源 + 完整 Config Options 对齐), 分 4 阶段
- **Phase 0** (ACP Configurable LLM Providers): Add `providers/*` protocol methods, ProviderRouter, capability flag
- **Phase 1** (Shared Model List Logic): Extract `build_model_state_for_acp()` with configured-first, tokonomics-fallback
- **Phase 2** (Agent Role Config Option): Expose `agent_pool.all_agents` as `agent_role` config option with swap support
- **Phase 3** (OpenCode `/mode` fix): Dynamic `agent.get_modes()` instead of static `["default"]`
- **Pre-research spike**: pydantic-ai Provider injection investigation (Open Question #7, Wave 0)

**Research Findings**:
- `shared/model_utils.py` exists — add `build_model_state_for_acp()` to existing file
- `AgentCapabilities` lacks `providers` field — needs adding
- `get_session_model_state()` currently tokonomics-first — needs inversion to configured-first
- `get_session_config_options()` is simple passthrough — easy to extend
- `set_session_config_option()` forwards to `agent.set_mode()` — needs `agent_role` branch
- `_session_agent_locks` already exists at `acp_agent.py:271`
- 17 ACP test files exist, using syrupy snapshots

### Metis Review
**Identified Gaps** (addressed):
- `_session_agent_locks` was incorrectly reported missing — corrected (exists at acp_agent.py:271)
- Snapshot files are syrupy, not `.ambr`
- `AgentCapabilities.create()` has only 1 call site (`InitializeResponse.create()`)
- ProviderRouter thread safety needs `asyncio.Lock`
- Phase 0 must merge before Phase 1/2 branches
- Zed compatibility capped at smoke test
- Added missing acceptance criteria (error codes, agent_role position, /mode fallback)
- Added edge cases (tool call swap, provider collision, timeout)

---

## Work Objectives

### Core Objective
Implement RFC-0034 to unify ACP and OpenCode model list sources, expose agent roles as switchable config options, and add `providers/*` ACP protocol support — enabling IDE users to select LLM providers and switch agent roles within a single ACP session.

### Concrete Deliverables
- `acp/schema/providers.py` — `ProviderInfo`, `LlmProtocol`, `ProviderStatus`
- `acp_server/provider_router.py` — `ProviderRouter` class
- `shared/model_utils.py` — `build_model_state_for_acp()` function
- Modified `acp/schema/capabilities.py` — `providers: bool` field
- Modified `acp_server/acp_agent.py` — 5 method modifications + 2 new methods
- Modified `opencode_server/config_routes.py` — dynamic `/mode` route
- Updated ACP snapshot tests (syrupy re-baseline)

### Definition of Done
- [ ] All 4 phases implemented and passing tests
- [ ] ACP snapshot tests re-baselined
- [ ] `agent_role` config option appears in `config_options` when `len(pool.all_agents) > 1`
- [ ] `providers/*` protocol methods respond correctly to `initialize(capabilities.providers=true)`
- [ ] `/mode` route returns dynamic modes from `agent.get_modes()`

### Must Have
- ProviderRouter with override/disable/capability tracking
- Configured-first model state (tokonomics fallback)
- Agent role swap with lock protection
- `providers` capability flag in InitializeResponse
- Error responses for unknown provider / disable required provider (JSON-RPC -32602)
- All existing tests continue to pass

### Must NOT Have (Guardrails)
- No ACP protocol schema modifications (only Python models)
- No OpenCode `/provider` REST API replacement
- No agent role persistence across sessions
- No runtime manifest reload
- No cross-session config synchronization
- No live provider routing for running sessions
- No full Zed IDE compatibility (keyboard shortcuts remain known limitation)
- No complete provider base URL canonical mapping
- No `SessionModelState` provider metadata extension
- No OpenCode `PATCH /config` semantic alignment with ACP `session/set_config_option`
  - No `typing.Any` or `# type: ignore` without justification (strict typing)
- No excessive comments or AI slop patterns

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (17 ACP test files, syrupy snapshots, conftest.py fixtures)
- **Automated tests**: Tests-after (not TDD)
- **Framework**: pytest + syrupy snapshots
- **Agent-executed QA**: Each task includes Playwright / interactive_bash / curl verification scenarios

### QA Policy
Every task MUST include agent-executed QA scenarios:
- **Frontend/UI**: Playwright — Navigate, interact, assert DOM, screenshot
- **TUI/CLI**: interactive_bash (tmux) — Run command, send keystrokes, validate output
- **API/Backend**: Bash (curl) — Send requests, assert status + response fields
- **Library/Module**: Bash (pytest) — Run tests, assert pass/fail

Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Pre-research + Independent Schema — 3 parallel tasks):
├── Task 0: pydantic-ai Provider injection spike
├── Task 1: acp/schema/providers.py — type definitions
└── Task 3: acp/schema/capabilities.py — providers field

Wave 1 (Phase 0 Core — 4 tasks after Wave 0, with internal dependency):
├── Task 2: acp_server/provider_router.py — ProviderRouter
├── Task 5: Audit AgentCapabilities.create() call sites
│   (Task 2 ∥ Task 5 → Task 4 → Task 6)
├── Task 4: acp_server/acp_agent.py — handlers + initialize
└── Task 6: Phase 0 tests + snapshot re-baseline

Wave 2 (Phase 1 ∥ Phase 2 — 8 tasks after Wave 1, with internal dependencies):
├── Task 7: shared/model_utils.py — build_model_state_for_acp()
├── Task 9: acp_agent.py — get_agent_role_config_option()
├── Task 10: acp_agent.py — _swap_session_agent()
│   (Task 7 → Task 8, Task 9 → Task 11, Task 10 → Task 12)
├── Task 8: acp_agent.py — get_session_model_state() inversion
├── Task 11: acp_agent.py — get_session_config_options() extension
├── Task 12: acp_agent.py — set_session_config_option() agent_role branch
├── Task 13: Phase 1 tests
└── Task 14: Phase 2 tests

Wave 3 (Phase 3 + Cross-Protocol Validation — 3 parallel tasks after Wave 2):
├── Task 15: config_routes.py — dynamic /mode
├── Task 16: Phase 3 tests
└── Task 17: Cross-protocol integration validation (ACP ↔ OpenCode model list alignment)

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 0,1,3 → Task 2∥5 → Task 4 → Task 6 → Task 7→8, 9→11, 10→12 → Task 13-14 → Task 15-17 → F1-F4 → user okay
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 8 (Wave 2)
```

### Dependency Matrix

| Task | Blocks | Blocked By |
|------|--------|------------|
| 0 | 2 | None |
| 1 | 2 | None |
| 3 | 4, 5 | None |
| 2 | 4 | 0, 1, 3 |
| 4 | 6 | 2 |
| 5 | 6 | 3 |
| 6 | 7-14, 15-17 | 4, 5 |
| 7-14 | 15-17 | 6 |
| 15-17 | F1-F4 | 7-14 |
| F1-F4 | — | 15-17 |

### Agent Dispatch Summary

- **Wave 0**: Tasks 0, 1, 3 → `deep` (research) / `quick` (schema)
- **Wave 1**: Tasks 2, 4-6 → `unspecified-high` (implementation)
- **Wave 2**: Tasks 7-14 → `deep` (logic) / `unspecified-high` (integration)
- **Wave 3**: Tasks 15-17 → `quick` (routing fix + validation)
- **Wave FINAL**: F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 0. **pydantic-ai Provider Injection Pre-Research Spike**

  **What to do**:
  - Navigate to `../pydantic-ai` directory and study its Provider initialization mechanism
  - Find how `Provider` objects are created and attached to agents/models
  - Identify injection points where ProviderRouter override (`base_url`, `api_key`) could be dynamically applied
  - Document findings in a short report (~500 words) with code references

  **Must NOT do**:
  - Do NOT implement any changes in pydantic-ai itself
  - Do NOT write tests for this spike
  - Do NOT modify AgentPool code during this spike

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires deep understanding of pydantic-ai internals and Provider lifecycle
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `uv-package-manager`: Not needed — this is research, not dependency management

  **Parallelization**:
  - **Can Run In Parallel**: YES (only task in Wave 0)
  - **Parallel Group**: Wave 0
    - **Blocks**: Tasks 2, 4, 5, 6 (ProviderRouter + handlers + tests depend on spike design decisions; schema Tasks 1,3 are independent)
  - **Blocked By**: None (can start immediately)

  **References**:
  - `../pydantic-ai/src/pydantic_ai/models/` — Provider implementations
  - `../pydantic-ai/src/pydantic_ai/agent.py` — Agent initialization where model/Provider is set
  - RFC-0034 Open Question #7 — Go/No-Go rubric

  **Acceptance Criteria**:
  - [ ] Research report written to `.omo/evidence/task-0-pydantic-ai-spike.md`
  - [ ] Report includes: (a) how Provider is initialized, (b) where override can be injected, (c) recommendation (GO/NO-GO)

  **QA Scenarios**:
  ```
  Scenario: Research report exists and is readable
    Tool: Bash
    Preconditions: None
    Steps:
      1. cat .omo/evidence/task-0-pydantic-ai-spike.md
    Expected Result: File exists, contains "GO" or "NO-GO" verdict, has code references
    Evidence: .omo/evidence/task-0-pydantic-ai-spike.md
  ```

  **Commit**: NO (spike — no code changes)

- [ ] 1. **`acp/schema/providers.py` — Provider Type Definitions**

  **What to do**:
  - Create `src/acp/schema/providers.py` with the following types (from RFC §3.1):
    - `LlmProtocol = Literal["openai", "anthropic", "google", "mistral", "cohere", "azure_openai", "bedrock"] | str`
    - `class ProviderStatus(Enum)`: `enabled`, `disabled`
    - `class ProviderInfo(BaseModel)`: `id: str`, `name: str`, `protocol: LlmProtocol`, `base_url: str | None`, `api_key_id: str | None`, `status: ProviderStatus`
  - Export all types from the module

  **Must NOT do**:
  - Do NOT add business logic (this is pure schema)
  - Do NOT import from `acp_server` (schema must remain server-agnostic)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Pure type definitions, no logic
  - **Skills**: []

  **Parallelization**:
    - **Can Run In Parallel**: YES — with Tasks 0, 3 (Wave 0)
    - **Parallel Group**: Wave 0
    - **Blocks**: Task 2 (ProviderRouter imports these types), Task 4 (handlers use these types)
  - **Blocked By**: None

  **References**:
  - RFC-0034 §3.1 — Provider 类型定义
  - `src/acp/schema/capabilities.py` — Existing schema patterns to follow

  **Acceptance Criteria**:
  - [ ] File exists: `src/acp/schema/providers.py`
  - [ ] All types match RFC spec exactly
  - [ ] `pytest tests/servers/acp_server/ -k "providers"` → PASS (if tests added)

  **QA Scenarios**:
  ```
  Scenario: Types are importable and match spec
    Tool: Bash
    Preconditions: None
    Steps:
      1. uv run python -c "from acp.schema.providers import ProviderInfo, LlmProtocol, ProviderStatus; print(ProviderStatus.enabled)"
    Expected Result: No ImportError, output is "ProviderStatus.enabled"
    Evidence: .omo/evidence/task-1-types-importable.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `feat(acp): add ProviderInfo, LlmProtocol, ProviderStatus schema types`
  - Files: `src/acp/schema/providers.py`

- [ ] 2. **`acp_server/provider_router.py` — ProviderRouter Implementation**

  **What to do**:
  - Create `src/agentpool_server/acp_server/provider_router.py` with `ProviderRouter` class (from RFC §3.2):
    - `__init__(self, manifest: AgentsManifest | None)`
    - `_derive_providers_from_manifest()` — extracts `ProviderInfo[]` from `manifest.models.model_variants`
    - `_extract_provider(config)` — infers provider from model string (e.g., "openai:gpt-4o" → "openai")
    - `_infer_llm_protocol(provider)` — maps provider name to `LlmProtocol`
    - `_get_default_base_url(provider)` — best-effort, returns empty string if unknown
    - `get_providers()` → `list[ProviderInfo]`
    - `get_provider(provider_id)` → `ProviderInfo | None`
    - `set_provider_override(provider_id, base_url, api_key)`
    - `disable_provider(provider_id)` / `enable_provider(provider_id)`
    - `is_provider_disabled(provider_id)` → `bool`
    - `_lock: asyncio.Lock` for thread safety
  - Handle errors: unknown provider → raise `ValueError`, required provider disable → raise `ValueError`

  **Must NOT do**:
  - Do NOT implement runtime provider routing for running sessions (out of scope)
  - Do NOT persist overrides to disk
  - Do NOT add complex base URL inference (best-effort only)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Core business logic with error handling and state management
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 1, 3-6 (Wave 1)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 4 (handlers call ProviderRouter), Task 7 (build_model_state_for_acp uses ProviderRouter)
  - **Blocked By**: Task 1 (imports ProviderInfo types)

  **References**:
  - RFC-0034 §3.2 — ProviderRouter 设计
  - `src/agentpool_server/shared/model_utils.py` — Existing `Provider` model for reference
  - `src/agentpool_config/models.py` — `ModelVariant` config structure

  **Acceptance Criteria**:
  - [ ] File exists: `src/agentpool_server/acp_server/provider_router.py`
  - [ ] `pytest tests/servers/acp_server/test_provider_router.py` → PASS (all unit tests)
  - [ ] Thread safety: concurrent `set_provider_override` calls don't corrupt state

  **QA Scenarios**:
  ```
  Scenario: ProviderRouter derives providers from manifest
    Tool: Bash (pytest)
    Preconditions: Create mock manifest with 2 model_variants
    Steps:
      1. router = ProviderRouter(manifest)
      2. providers = router.get_providers()
    Expected Result: len(providers) >= 1, each has id, name, protocol
    Evidence: .omo/evidence/task-2-router-manifest.png

  Scenario: Unknown provider raises ValueError
    Tool: Bash (pytest)
    Preconditions: Router initialized with manifest
    Steps:
      1. router.get_provider("nonexistent")
    Expected Result: Returns None (not crash)
    Evidence: .omo/evidence/task-2-router-unknown.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `feat(acp): add ProviderRouter with override/disable/capability tracking`
  - Files: `src/agentpool_server/acp_server/provider_router.py`

- [ ] 3. **`acp/schema/capabilities.py` — Add `providers` Field**

  **What to do**:
  - Modify `src/acp/schema/capabilities.py`:
    - Add `providers: bool = False` to `AgentCapabilities` dataclass
    - Update `AgentCapabilities.create()` factory to accept `providers: bool = False`
  - Audit all call sites of `AgentCapabilities.create()` — only 1 call site: `InitializeResponse.create()`

  **Must NOT do**:
  - Do NOT modify the ACP protocol specification itself
  - Do NOT break backward compatibility (default `False`)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple field addition with 1 call site
  - **Skills**: []

  **Parallelization**:
    - **Can Run In Parallel**: YES — with Tasks 0, 1, 2 (Wave 0)
    - **Parallel Group**: Wave 0
    - **Blocks**: Task 4 (initialize response includes providers flag)
    - **Blocked By**: None

  **References**:
  - `src/acp/schema/capabilities.py` — Existing `AgentCapabilities` definition
  - `src/acp/schema/initialize.py` — `InitializeResponse.create()` call site

  **Acceptance Criteria**:
  - [ ] `AgentCapabilities` has `providers: bool` field
  - [ ] `AgentCapabilities.create(providers=True)` works
  - [ ] `pytest tests/servers/acp_server/ -k "capabilities"` → PASS

  **QA Scenarios**:
  ```
  Scenario: Capabilities can include providers flag
    Tool: Bash
    Preconditions: None
    Steps:
      1. uv run python -c "from acp.schema.capabilities import AgentCapabilities; c = AgentCapabilities.create(providers=True); print(c.providers)"
    Expected Result: Output is "True"
    Evidence: .omo/evidence/task-3-capabilities-flag.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `feat(acp): add providers capability flag to AgentCapabilities`
  - Files: `src/acp/schema/capabilities.py`

- [ ] 4. **`acp_server/acp_agent.py` — Handlers + Initialize Response**

  **What to do**:
  - Modify `src/agentpool_server/acp_server/acp_agent.py`:
    - Add `ProviderRouter` instance to `AgentPoolACPAgent.__init__()`
    - Add handlers for `providers/*` protocol methods:
      - `handle_providers_list()` → `providers/list` response
      - `handle_providers_set()` → validate + `providers/set` response
      - `handle_providers_disable()` → validate + `providers/disable` response
    - Modify `initialize()` to pass `providers=True` to `InitializeResponse.create()`
    - Error handling: unknown provider → `JsonRpcError(code=-32602)`, required provider disable → `JsonRpcError(code=-32602)`

  **Must NOT do**:
  - Do NOT modify `get_session_model_state()` yet (Phase 1)
  - Do NOT modify `get_session_config_options()` yet (Phase 2)
  - Do NOT add `agent_role` handling yet (Phase 2)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Core ACP protocol handlers with error handling
  - **Skills**: []

  **Parallelization**:
    - **Can Run In Parallel**: YES — with Tasks 5-6 (after Task 2 completes)
    - **Parallel Group**: Wave 1
    - **Blocks**: Tasks 7-14 (Phase 1/2 build on these handlers)
    - **Blocked By**: Tasks 1-3 (types, capabilities) and Task 2 (ProviderRouter instance)

  **References**:
  - RFC-0034 §3.3 — `providers/*` handlers 设计
  - `src/agentpool_server/acp_server/acp_agent.py:509-531` — `initialize()` factory usage
  - `src/agentpool_server/acp_server/acp_agent.py:72-144` — `get_session_model_state()` (DO NOT touch yet)

  **Acceptance Criteria**:
  - [ ] `initialize()` response includes `capabilities.providers: true`
  - [ ] `providers/list` returns `ProviderInfo[]`
  - [ ] `providers/set` with unknown provider returns JSON-RPC -32602
  - [ ] `providers/disable` with required provider returns JSON-RPC -32602

  **QA Scenarios**:
  ```
  Scenario: Initialize includes providers capability
    Tool: Bash (pytest)
    Preconditions: ACP agent initialized
    Steps:
      1. Call initialize with capabilities request
      2. Check response capabilities.providers
    Expected Result: capabilities.providers is True
    Evidence: .omo/evidence/task-4-init-capabilities.txt

  Scenario: providers/list returns manifest-derived providers
    Tool: Bash (pytest)
    Preconditions: Agent with manifest containing model_variants
    Steps:
      1. Call providers/list
    Expected Result: List of ProviderInfo with ids matching manifest providers
    Evidence: .omo/evidence/task-4-providers-list.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `feat(acp): add providers/* protocol handlers and initialize capability`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 5. **Audit `AgentCapabilities.create()` Call Sites**

  **What to do**:
  - Run `grep -r "AgentCapabilities.create" src/` to find all call sites
  - Update each call site to pass `providers=True` where appropriate
  - Expected: Only 1 call site — `InitializeResponse.create()` in `acp_agent.py`

  **Must NOT do**:
  - Do NOT change the default value (`providers: bool = False` remains)
  - Do NOT modify tests that explicitly pass `providers=False`

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical audit + update
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 1-4, 6 (Wave 1)
  - **Parallel Group**: Wave 1
  - **Blocks**: None (mechanical task)
  - **Blocked By**: Task 3 (capabilities field must exist first)

  **References**:
  - `src/acp/schema/initialize.py` — `InitializeResponse.create()` implementation

  **Acceptance Criteria**:
  - [ ] All `AgentCapabilities.create()` call sites audited
  - [ ] Initialize response passes `providers=True`

  **QA Scenarios**:
  ```
  Scenario: No unupdated call sites remain
    Tool: Bash
    Steps:
      1. grep -r "AgentCapabilities.create" src/ --include="*.py"
    Expected Result: Only InitializeResponse.create() calls it, with providers=True
    Evidence: .omo/evidence/task-5-audit-grep.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `feat(acp): pass providers=True in InitializeResponse.create()`
  - Files: `src/acp/schema/initialize.py` or related

- [ ] 6. **Phase 0 Tests + Snapshot Re-baseline**

  **What to do**:
  - Add unit tests for `ProviderRouter`:
    - `test_derive_providers_from_manifest()` — multiple model_variants
    - `test_set_provider_override()` — override base_url
    - `test_disable_provider()` — disable + enable
    - `test_is_provider_disabled()` — check disabled state
    - `test_concurrent_override()` — thread safety
  - Update syrupy snapshots for `test_acp_via_acp_snapshots.py`:
    - `initialize` response now includes `providers: true`
    - Re-baseline with `--snapshot-update`

  **Must NOT do**:
  - Do NOT add integration tests for Phase 1/2/3 yet
  - Do NOT modify non-ACP tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Test writing + snapshot management
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 1-5 (Wave 1)
  - **Parallel Group**: Wave 1
  - **Blocks**: None (tests are standalone)
  - **Blocked By**: Tasks 1-5 (test the code being added)

  **References**:
  - `tests/servers/acp_server/test_acp_via_acp_snapshots.py` — Existing snapshot tests
  - `tests/servers/acp_server/conftest.py` — Test fixtures

  **Acceptance Criteria**:
  - [ ] `pytest tests/servers/acp_server/test_provider_router.py` → PASS (new tests)
  - [ ] `pytest tests/servers/acp_server/test_acp_via_acp_snapshots.py --snapshot-update` → PASS
  - [ ] All existing ACP tests still pass

  **QA Scenarios**:
  ```
  Scenario: Snapshot tests re-baselined successfully
    Tool: Bash
    Steps:
      1. uv run pytest tests/servers/acp_server/test_acp_via_acp_snapshots.py -v
    Expected Result: All pass, snapshots updated
    Evidence: .omo/evidence/task-6-snapshots-pass.txt
  ```

  **Commit**: YES — groups with Wave 1
  - Message: `test(acp): Phase 0 — ProviderRouter tests + snapshot re-baseline`
  - Files: `tests/servers/acp_server/test_provider_router.py`, snapshot files

- [ ] 7. **`shared/model_utils.py` — `build_model_state_for_acp()`**

  **What to do**:
  - Add `build_model_state_for_acp()` to existing `src/agentpool_server/shared/model_utils.py`:
    - Signature: `def build_model_state_for_acp(agent: Agent, provider_router: ProviderRouter | None) -> SessionModelState | None`
    - Logic (configured-first, tokonomics-fallback per RFC §4.1):
      1. Get configured variants from `agent.agent_pool.manifest.models.model_variants` (if pool + manifest exist)
      2. Build `ACPModelInfo[]` from configured variants
      3. Filter out disabled providers via `provider_router.is_provider_disabled()`
      4. If configured list is non-empty → return `SessionModelState(...)` with configured list
      5. If empty → call `agent.get_available_models()` (tokonomics fallback)
      6. If tokonomics also empty → return `None`
    - Handle errors gracefully: try/except around `get_available_models()`, return `None` on failure

  **Must NOT do**:
  - Do NOT modify existing OpenCode `Provider` logic
  - Do NOT add provider metadata to `ACPModelInfo._meta` (Open Question #8: deferred)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core business logic with fallback chains and error handling
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 8-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 8 (get_session_model_state delegates to this)
  - **Blocked By**: Task 2 (ProviderRouter must exist)

  **References**:
  - RFC-0034 §4.1 — `build_model_state_for_acp()` 设计
  - `src/agentpool_server/shared/model_utils.py` — Existing file to extend
  - `src/acp/schema/session.py` — `SessionModelState`, `ACPModelInfo` types

  **Acceptance Criteria**:
  - [ ] Function exists and matches signature
  - [ ] Returns `SessionModelState` when configured variants exist
  - [ ] Falls back to tokonomics when configured variants empty
  - [ ] Returns `None` when both sources empty
  - [ ] Filters disabled providers

  **QA Scenarios**:
  ```
  Scenario: Configured variants take priority
    Tool: Bash (pytest)
    Preconditions: Agent with manifest containing model_variants
    Steps:
      1. state = build_model_state_for_acp(agent, router)
    Expected Result: state.available_models matches manifest variants
    Evidence: .omo/evidence/task-7-configured-first.txt

  Scenario: Tokonomics fallback when no configured variants
    Tool: Bash (pytest)
    Preconditions: Agent with NO manifest model_variants
    Steps:
      1. state = build_model_state_for_acp(agent, router)
    Expected Result: state.available_models matches agent.get_available_models()
    Evidence: .omo/evidence/task-7-tokonomics-fallback.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(shared): add build_model_state_for_acp() with configured-first logic`
  - Files: `src/agentpool_server/shared/model_utils.py`

- [ ] 8. **`acp_agent.py` — `get_session_model_state()` Inversion**

  **What to do**:
  - Modify `src/agentpool_server/acp_server/acp_agent.py:get_session_model_state()` (lines 72-144):
    - Invert logic from **tokonomics-first** to **configured-first**:
      - OLD: call `agent.get_available_models()` first, then check `model_variants` override
      - NEW: call `build_model_state_for_acp(agent, self.provider_router)` first
    - If result is `None` → current behavior (return `None`)
    - If result is `SessionModelState` → return it directly
    - Preserve existing error handling (try/except)

  **Must NOT do**:
  - Do NOT change the return type (`SessionModelState | None`)
  - Do NOT remove existing error handling

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Modifying existing core method, behavior preservation critical
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7, 9-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: None (method is called by session init, not by other tasks)
  - **Blocked By**: Task 7 (build_model_state_for_acp must exist)

  **References**:
  - RFC-0034 §4.2 — `get_session_model_state()` 改造
  - `src/agentpool_server/acp_server/acp_agent.py:72-144` — Current implementation
  - `src/agentpool_server/shared/model_utils.py` — `build_model_state_for_acp()` (Task 7)

  **Acceptance Criteria**:
  - [ ] `get_session_model_state()` calls `build_model_state_for_acp()`
  - [ ] Configured variants take priority over tokonomics
  - [ ] Disabled providers are filtered out
  - [ ] Existing tests still pass

  **QA Scenarios**:
  ```
  Scenario: Model state uses configured-first logic
    Tool: Bash (pytest)
    Preconditions: Agent with manifest model_variants
    Steps:
      1. state = await agent.get_session_model_state(session_id)
    Expected Result: state.available_models matches manifest, not tokonomics
    Evidence: .omo/evidence/task-8-inversion.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(acp): invert get_session_model_state() to configured-first`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 9. **`acp_agent.py` — `get_agent_role_config_option()`**

  **What to do**:
  - Add `get_agent_role_config_option()` to `acp_agent.py` (from RFC §5.2):
    - Access `pool` via `agent.agent_pool` (type-safe, project forbids `getattr`/`hasattr`)
    - If pool is None or `len(pool.all_agents) <= 1` → return `None`
    - Build `ConfigOption` with:
      - `id: "agent_role"`
      - `label: "Agent Role"`
      - `category: ConfigOptionCategory.OTHER`
      - `current_value: agent.name`
      - `choices: [ConfigOptionChoice(id=a.name, label=a.name) for a in pool.all_agents]`
    - Return the ConfigOption

  **Must NOT do**:
  - Do NOT return agent_role when only 1 agent exists (per RFC decision)
  - Do NOT add persistence (agent swap is ephemeral)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple data transformation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-8, 10-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 11 (get_session_config_options includes agent_role)
  - **Blocked By**: None

  **References**:
  - RFC-0034 §5.2 — `get_agent_role_config_option()` 设计
  - `src/agentpool_server/acp_server/acp_agent.py:177-188` — `get_session_config_options()` (reference for ConfigOption pattern)
  - `src/acp/schema/config.py` — `ConfigOption`, `ConfigOptionChoice`, `ConfigOptionCategory` types

  **Acceptance Criteria**:
  - [ ] Function returns ConfigOption when pool has >1 agents
  - [ ] Function returns None when pool has <=1 agents
  - [ ] Choices include all agents from pool.all_agents

  **QA Scenarios**:
  ```
  Scenario: Multi-agent pool exposes agent_role
    Tool: Bash (pytest)
    Preconditions: AgentPool with 3 agents
    Steps:
      1. option = get_agent_role_config_option(agent)
    Expected Result: option.id == "agent_role", len(option.choices) == 3
    Evidence: .omo/evidence/task-9-multi-agent.txt

  Scenario: Single-agent pool hides agent_role
    Tool: Bash (pytest)
    Preconditions: AgentPool with 1 agent
    Steps:
      1. option = get_agent_role_config_option(agent)
    Expected Result: option is None
    Evidence: .omo/evidence/task-9-single-agent.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(acp): add get_agent_role_config_option() for multi-agent pools`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 10. **`acp_agent.py` — `_swap_session_agent()`**

  **What to do**:
    - Add `_swap_session_agent()` to `acp_agent.py` (from RFC §5.2):
      - Acquire `self._session_agent_locks[session_id]` lock (prevents concurrent swaps)
      - Call `session.switch_active_agent(new_agent_name)`
      - On success, return `{ "success": True }`
      - On failure, release lock and propagate error
  - Ensure lock cleanup on session end (hook into existing cleanup)

  **Must NOT do**:
  - Do NOT allow swap during active prompt (safety guard)
  - Do NOT persist swap across sessions
  - Do NOT inherit conversation history (new agent starts fresh)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Concurrency control, session management, error handling
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-9, 11-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 12 (set_session_config_option delegates to _swap_session_agent)
  - **Blocked By**: None (uses existing `_session_agent_locks` at acp_agent.py:271)

  **References**:
  - RFC-0034 §5.2 — `_swap_session_agent()` 设计
  - `src/agentpool_server/acp_server/acp_agent.py:271` — `_session_agent_locks` field
  - `src/agentpool_server/acp_server/acp_agent.py:329-332` — Existing lock usage pattern
  - `src/agentpool_server/acp_server/session.py:426-473` — `switch_active_agent()` method

  **Acceptance Criteria**:
  - [ ] Swap succeeds when session idle
  - [ ] Swap fails with -32602 when prompt active
  - [ ] Lock acquired and released correctly
  - [ ] New agent starts fresh (no history inheritance)

  **QA Scenarios**:
  ```
  Scenario: Successful agent swap
    Tool: Bash (pytest)
    Preconditions: Session with idle agent
    Steps:
      1. result = await _swap_session_agent(session_id, "other_agent")
    Expected Result: result["success"] is True, session agent changed
    Evidence: .omo/evidence/task-10-swap-success.txt

  Scenario: Swap blocked during active prompt
    Tool: Bash (pytest)
    Preconditions: Session with _task_lock held
    Steps:
      1. result = await _swap_session_agent(session_id, "other_agent")
    Expected Result: Raises JsonRpcError with code -32602
    Evidence: .omo/evidence/task-10-swap-blocked.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(acp): add _swap_session_agent() with lock protection`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 11. **`acp_agent.py` — `get_session_config_options()` Extension**

  **What to do**:
  - Modify `get_session_config_options()` (lines 177-188) to append `agent_role` config option:
    - Call existing `agent.get_modes()` logic
    - Call `get_agent_role_config_option()` (Task 9)
    - If result is not None, append to the config options list
    - Return combined list

  **Must NOT do**:
  - Do NOT modify existing mode options from `agent.get_modes()`
  - Do NOT change ordering of existing options (append agent_role at end)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple list concatenation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-10, 12-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Task 9 (get_agent_role_config_option must exist)

  **References**:
  - `src/agentpool_server/acp_server/acp_agent.py:177-188` — Current implementation

  **Acceptance Criteria**:
  - [ ] agent_role appended when pool has >1 agents
  - [ ] agent_role absent when pool has <=1 agents
  - [ ] Existing modes unchanged

  **QA Scenarios**:
  ```
  Scenario: Config options include agent_role
    Tool: Bash (pytest)
    Preconditions: AgentPool with 2 agents
    Steps:
      1. options = get_session_config_options()
    Expected Result: Any(o.id == "agent_role" for o in options)
    Evidence: .omo/evidence/task-11-options-include-role.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(acp): append agent_role to get_session_config_options()`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 12. **`acp_agent.py` — `set_session_config_option()` Agent Role Branch**

  **What to do**:
  - Modify `set_session_config_option()` (lines 1012-1040) to handle `agent_role`:
    - If `option_id == "agent_role"`:
      - Validate `value` is a valid agent name in `pool.all_agents`
      - Call `_swap_session_agent(session_id, value)`
      - Return `{ "success": True }`
    - Otherwise, delegate to existing `agent.set_mode()` logic

  **Must NOT do**:
  - Do NOT remove existing mode-setting logic
  - Do NOT allow invalid agent names

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Branching logic with validation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-11, 13-14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Task 10 (_swap_session_agent must exist)

  **References**:
  - `src/agentpool_server/acp_server/acp_agent.py:1012-1040` — Current implementation

  **Acceptance Criteria**:
  - [ ] agent_role option triggers _swap_session_agent
  - [ ] Invalid agent name raises error
  - [ ] Other options still delegate to agent.set_mode()

  **QA Scenarios**:
  ```
  Scenario: Set agent_role swaps agent
    Tool: Bash (pytest)
    Preconditions: Session with agent A, pool has agent B
    Steps:
      1. result = await set_session_config_option("agent_role", "agent_B")
    Expected Result: result["success"] is True, session agent is now B
    Evidence: .omo/evidence/task-12-set-role.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `feat(acp): handle agent_role in set_session_config_option()`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`

- [ ] 13. **Phase 1 Tests — Model State Logic**

  **What to do**:
  - Add tests for `build_model_state_for_acp()` and `get_session_model_state()`:
    - `test_configured_first_priority()` — manifest variants override tokonomics
    - `test_tokonomics_fallback()` — no manifest variants → tokonomics
    - `test_provider_filtering()` — disabled providers excluded
    - `test_empty_state()` — no models → None
    - `test_error_handling()` — get_available_models() raises → None

  **Must NOT do**:
  - Do NOT test Phase 2 logic (agent_role)
  - Do NOT test Phase 3 logic (/mode)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Test writing for complex fallback logic
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-12, 14 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Tasks 7-8 (build_model_state_for_acp + inversion)

  **References**:
  - `tests/servers/acp_server/conftest.py` — Test fixtures
  - `tests/servers/acp_server/test_acp_via_acp_snapshots.py` — Snapshot patterns

  **Acceptance Criteria**:
  - [ ] `pytest tests/servers/acp_server/test_model_state.py` → PASS (all 5 tests)
  - [ ] Coverage includes happy path + error cases

  **QA Scenarios**:
  ```
  Scenario: All model state tests pass
    Tool: Bash
    Steps:
      1. uv run pytest tests/servers/acp_server/test_model_state.py -v
    Expected Result: 5 tests, 0 failures
    Evidence: .omo/evidence/task-13-model-state-tests.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `test(acp): Phase 1 — model state configured-first tests`
  - Files: `tests/servers/acp_server/test_model_state.py`

- [ ] 14. **Phase 2 Tests — Agent Role Swap**

  **What to do**:
  - Add tests for agent role config option and swap:
    - `test_single_agent_no_role()` — 1 agent → no agent_role option
    - `test_multi_agent_has_role()` — 2+ agents → agent_role present
    - `test_role_swap_success()` — swap to valid agent
    - `test_role_swap_blocked_during_prompt()` — swap fails when locked
    - `test_role_swap_invalid_agent()` — swap to invalid name raises error
    - `test_swap_no_history_inheritance()` — new agent starts fresh

  **Must NOT do**:
  - Do NOT test provider logic (Phase 0)
  - Do NOT test model state logic (Phase 1)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Concurrency + session management tests
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 7-13 (Wave 2)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Tasks 9-12 (agent_role methods)

  **References**:
  - `tests/servers/acp_server/conftest.py` — Test fixtures with AgentPool setup

  **Acceptance Criteria**:
  - [ ] `pytest tests/servers/acp_server/test_agent_role.py` → PASS (all 6 tests)
  - [ ] Concurrent swap test validates lock behavior

  **QA Scenarios**:
  ```
  Scenario: All agent role tests pass
    Tool: Bash
    Steps:
      1. uv run pytest tests/servers/acp_server/test_agent_role.py -v
    Expected Result: 6 tests, 0 failures
    Evidence: .omo/evidence/task-14-agent-role-tests.txt
  ```

  **Commit**: YES — groups with Wave 2
  - Message: `test(acp): Phase 2 — agent role config option and swap tests`
  - Files: `tests/servers/acp_server/test_agent_role.py`

- [ ] 15. **`config_routes.py` — Dynamic `/mode` Route**

  **What to do**:
  - Modify `src/agentpool_server/opencode_server/routes/config_routes.py`:
    - Find the `/mode` route handler
    - Replace static `return [Mode(name="default")]` with dynamic:
      - Call `agent.get_modes()`
      - Filter modes with `id="mode"` category
      - Map to `Mode` objects
      - Fallback to `[Mode(name="default", tools={})]` if empty/error
    - Handle errors gracefully (try/except)

  **Must NOT do**:
  - Do NOT modify `PATCH /config` semantics (out of scope)
  - Do NOT change mode format (keep existing Mode structure)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple route modification
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Task 16 (Wave 3)
  - **Parallel Group**: Wave 3
  - **Blocks**: None
  - **Blocked By**: Tasks 7-14 (Phase 1/2 complete, but this is independent)

  **References**:
  - RFC-0034 §6 — Phase 3: OpenCode /mode 修复
  - `src/agentpool_server/opencode_server/routes/config_routes.py` — Current /mode route

  **Acceptance Criteria**:
  - [ ] `/mode` returns modes from `agent.get_modes()`
  - [ ] Fallback to `[Mode(name="default")]` on error
  - [ ] Existing OpenCode tests still pass

  **QA Scenarios**:
  ```
  Scenario: /mode returns dynamic modes
    Tool: Bash (curl)
    Preconditions: OpenCode server running with NativeAgent
    Steps:
      1. curl http://localhost:8080/mode
    Expected Result: JSON array with modes matching agent.get_modes()
    Evidence: .omo/evidence/task-15-mode-dynamic.txt

  Scenario: /mode fallback on error
    Tool: Bash (curl)
    Preconditions: Agent.get_modes() raises exception
    Steps:
      1. curl http://localhost:8080/mode
    Expected Result: [{"name": "default", "tools": {}}]
    Evidence: .omo/evidence/task-15-mode-fallback.txt
  ```

  **Commit**: YES — groups with Wave 3
  - Message: `fix(opencode): dynamic /mode route using agent.get_modes()`
  - Files: `src/agentpool_server/opencode_server/routes/config_routes.py`

- [ ] 16. **Phase 3 Tests — /mode Route**

  **What to do**:
  - Add tests for `/mode` route:
    - `test_mode_returns_dynamic_modes()` — NativeAgent modes returned
    - `test_mode_fallback_on_error()` — error → default mode
    - `test_mode_empty_modes()` — no modes → default mode

  **Must NOT do**:
  - Do NOT test other config routes
  - Do NOT add integration tests beyond /mode

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple route tests
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Task 15 (Wave 3)
  - **Parallel Group**: Wave 3
  - **Blocks**: None
  - **Blocked By**: Task 15 (route must exist)

  **References**:
  - `tests/servers/opencode_server/` — Existing OpenCode server tests

  **Acceptance Criteria**:
  - [ ] `pytest tests/servers/opencode_server/test_config_routes.py` → PASS (3 tests)

  **QA Scenarios**:
  ```
  Scenario: /mode tests pass
    Tool: Bash
    Steps:
      1. uv run pytest tests/servers/opencode_server/test_config_routes.py -v
    Expected Result: 3 tests, 0 failures
    Evidence: .omo/evidence/task-16-mode-tests.txt
  ```

  **Commit**: YES — groups with Wave 3
  - Message: `test(opencode): Phase 3 — /mode route tests`
  - Files: `tests/servers/opencode_server/test_config_routes.py`

- [ ] 17. **Cross-Protocol Integration Validation**

  **What to do**:
  - Add integration test verifying ACP `SessionModelState` and OpenCode `/mode` return semantically aligned model/role information:
    - Create an agent with manifest model_variants
    - Start an ACP session, verify `models` field in `NewSessionResponse`
    - Call OpenCode `/mode`, verify returned modes
    - Assert both protocols reflect the same underlying agent state
    - Verify `agent_role` config option appears in ACP `config_options` iff `/mode` returns multiple modes
  - This serves as the final cross-protocol sanity check

  **Must NOT do**:
  - Do NOT add new features (this is validation only)
  - Do NOT modify production code

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration testing across two protocols
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Tasks 15-16 (Wave 3)
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4 (Wave FINAL)
  - **Blocked By**: Tasks 7-14 (Phase 1/2 code must exist)

  **References**:
  - `tests/servers/acp_server/test_acp_via_acp_snapshots.py` — ACP integration patterns
  - `tests/servers/opencode_server/` — OpenCode server test patterns

  **Acceptance Criteria**:
  - [ ] `pytest tests/integration/test_cross_protocol.py` → PASS
  - [ ] Test covers both ACP and OpenCode protocols

  **QA Scenarios**:
  ```
  Scenario: Cross-protocol integration test passes
    Tool: Bash
    Steps:
      1. uv run pytest tests/integration/test_cross_protocol.py -v
    Expected Result: All tests pass
    Evidence: .omo/evidence/task-17-cross-protocol.txt
  ```

  **Commit**: YES — groups with Wave 3
  - Message: `test(integration): cross-protocol ACP ↔ OpenCode validation`
  - Files: `tests/integration/test_cross_protocol.py`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .omo/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `mypy src/` + `ruff check src/` + `uv run pytest`. Review all changed files for: `typing.Any`, `# type: ignore`, bare `except:`, `print()` in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (data/result/item/temp).
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high` (+ `playwright` skill if UI)
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (features working together, not isolation). Test edge cases: empty state, invalid input, rapid actions. Save to `.omo/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination: Task N touching Task M's files. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 0**: `spike(pydantic-ai): Provider injection pre-research`
- **Wave 1**: `feat(acp): Phase 0 — configurable LLM providers`
- **Wave 2**: `feat(acp): Phase 1 — shared model list + Phase 2 — agent role config`
- **Wave 3**: `fix(opencode): Phase 3 — dynamic /mode route`
- **Wave FINAL**: `test(acp): snapshot re-baseline + final verification`

---

## Success Criteria

### Verification Commands
```bash
# Run ACP tests
uv run pytest tests/servers/acp_server/ -v

# Run snapshot tests
uv run pytest tests/servers/acp_server/test_acp_via_acp_snapshots.py -v --snapshot-update

# Type check
uv run --no-group docs mypy src/

# Lint
uv run ruff check src/
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All ACP snapshot tests pass
- [ ] Type check passes (`mypy src/`)
- [ ] Lint passes (`ruff check src/`)
- [ ] ProviderRouter handles override/disable/capability
- [ ] `build_model_state_for_acp()` uses configured-first logic
- [ ] `agent_role` appears in config_options when >1 agent
- [ ] `/mode` returns dynamic modes
