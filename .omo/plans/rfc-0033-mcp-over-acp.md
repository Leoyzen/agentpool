# RFC-0033: MCP-over-ACP Implementation Plan

## TL;DR

> **Implement native MCP-over-ACP transport support**, allowing ACP clients to inject MCP tools through the ACP channel without separate stdio processes or HTTP ports.
>
> **Deliverables**:
> - Schema extensions: `AcpMcpServer`, `McpCapabilities.acp`, new `AgentMethod`/`ClientMethod` entries
> - `AcpMcpConnectionManager`: per-ACP-connection state management (`acpId` → `connectionId` mapping)
> - `AcpMcpTransport`: `fastmcp.ClientTransport` implementation routing MCP JSON-RPC over ACP
> - `acp_agent.py` integration: handler registration, session lifecycle, cleanup
> - Bidirectional config converters: `convert_acp_mcp_server_to_config()` + `mcp_config_to_acp()`
> - `MCPClient._get_client()` support for `AcpMCPServerConfig`
> - Comprehensive test suite (TDD): unit + integration tests
> - Prerequisite fix: restore `StdioMcpServer.type` discriminator
>
> **Estimated Effort**: Medium-High (~600-900 lines core logic + tests)
> **Parallel Execution**: YES — Wave 1 schema (sequential gate) → Waves 2-4 parallel
> **Critical Path**: StdioMcpServer.type fix → Schema PR → Transport spike → Manager+Transport → acp_agent integration → Converters → E2E verification

---

## Context

### Original Request
Implement RFC-0033 (MCP-over-ACP transport) from the draft RFC document. Allow ACP clients to inject MCP tools via the ACP channel.

### Interview Summary
**Key Decisions**:
- **Parallelization**: HIGHLY PARALLEL — maximize throughput after schema gate
- **TDD**: Test-first — RED (failing test) → GREEN (minimal impl) → REFACTOR for every module
- **Feature Flag**: `acp` capability DEFAULT ON (user override of RFC's suggested default OFF)
- **Scope**: IN = schema, manager, transport, integration, converters, tests; OUT = Bridging, client-side SDK, capability caching, auth extensions

### Research Findings
**Critical Architecture Discovery**:
- `_agent_handler` in `acp/agent/connection.py:251-321` routes ALL agent-side requests. Unknown methods (not starting with `_`) hit `case _:` → `RequestError.method_not_found(method)` at line 320-321.
- `ext_method()` (line 196-198) only handles methods prefixed with `_` (lines 315-319). `mcp/connect` is a standard ACP method, NOT an extension.
- **Conclusion**: Must add explicit `case "mcp/connect"` and `case "mcp/disconnect"` branches to `_agent_handler`. Cannot rely on `ext_method`.

**File Location Corrections** (RFC paths vs. actual):
| RFC Path | Actual Path |
|----------|-------------|
| `agentpool_server/acp_server/acp_converters.py` | `agentpool/agents/acp_agent/acp_converters.py` |
| `agentpool/mcp_server/provider.py` | `agentpool/resource_providers/mcp_provider.py` |

### Metis Review
**Identified Gaps** (addressed in this plan):
1. **`_agent_handler` dispatch**: Must add explicit `mcp/connect`, `mcp/disconnect` cases — NOT via `ext_method`
2. **`filter_servers_by_capabilities`**: Must explicitly filter `AcpMcpServer` when `acp` capability is OFF
3. **`parse_mcp_servers_json` "acp" branch**: Removed from scope — ACP transport MCP servers cannot be configured via static JSON
4. **Security hardening deferred**: Rate limiting, payload caps → Phase 5 (optional)
5. **Feature flag contradiction**: User chose DEFAULT ON; plan reflects this
6. **`assert_never` atomic gate**: All 6 sites must be updated together; treated as single blocking task
7. **TDD + Parallel risk**: Schema is sequential gate; Phases 2-4 parallel AFTER schema merge

---

## Work Objectives

### Core Objective
Enable agentpool to act as an ACP Agent that supports MCP-over-ACP transport: declare `mcpCapabilities.acp: true`, handle `mcp/connect`, route `mcp/message` tool calls bidirectionally, and clean up via `mcp/disconnect`.

### Concrete Deliverables
1. `acp/schema/mcp.py`: `AcpMcpServer` class + restored `StdioMcpServer.type`
2. `acp/schema/capabilities.py`: `McpCapabilities.acp` field + `AgentCapabilities.create(acp_mcp_servers=...)`
3. `acp/schema/messages.py`: `"mcp/connect"`, `"mcp/disconnect"` in `AgentMethod`; `"mcp/message"` in `ClientMethod`
4. `acp/agent/connection.py`: `_agent_handler` routes `mcp/connect` and `mcp/disconnect`
5. `agentpool_server/acp_server/acp_mcp_manager.py`: `AcpMcpConnectionManager`
6. `agentpool_server/acp_server/acp_mcp_transport.py`: `AcpMcpTransport` (fastmcp `ClientTransport`)
7. `agentpool_server/acp_server/acp_agent.py`: Integration (instantiate manager, register handlers, cleanup)
8. `agentpool_server/acp_server/converters.py`: `convert_acp_mcp_server_to_config()` extended
9. `agentpool/agents/acp_agent/acp_converters.py`: `mcp_config_to_acp()` extended
10. `agentpool_config/mcp_server.py`: `AcpMCPServerConfig` + `MCPServerConfig` union updated
11. `agentpool/mcp_server/client.py`: `MCPClient._get_client()` supports `AcpMCPServerConfig`
12. `agentpool/resource_providers/mcp_provider.py`: `transport_type` returns `"acp"`
13. `agentpool/agents/acp_agent/helpers.py`: `filter_servers_by_capabilities` filters by `acp` cap
14. Test files: unit tests for manager + transport, integration tests for full lifecycle

### Definition of Done
- [ ] `pytest tests/servers/acp_server/test_mcp_integration.py` passes (existing tests no regression)
- [ ] New tests pass: `pytest tests/acp_server/test_acp_mcp_manager.py`, `pytest tests/mcp_server/test_acp_mcp_transport.py`
- [ ] `pytest tests/agents/acp_agent/test_acp_converters.py` passes (extended)
- [ ] `mypy src/` passes with zero errors related to new code
- [ ] `ruff check src/` passes

### Must Have
- Schema extensions (all 3 schema files)
- `_agent_handler` routing for `mcp/connect` and `mcp/disconnect`
- `AcpMcpConnectionManager` with register/connect/disconnect/send_message/cleanup
- `AcpMcpTransport` implementing fastmcp `ClientTransport`
- `acp_agent.py` integration (manager lifecycle, handler delegation)
- Bidirectional converter updates (both `converters.py` and `acp_converters.py`)
- `MCPClient._get_client()` support for `AcpMCPServerConfig`
- `MCPResourceProvider.transport_type` returns `"acp"`
- `filter_servers_by_capabilities` filters `AcpMcpServer`
- All `assert_never` sites updated
- StdioMcpServer.type restored
- Unit + integration tests (TDD)

### Must NOT Have (Guardrails)
- **Bridging**: Not implementing stdio/HTTP shim for ACP transport
- **Rate limiting / payload size validation**: Security hardening deferred to follow-up
- **Client-side SDK changes**: Only agentpool (agent-side) implementation
- **`parse_mcp_servers_json` "acp" branch**: ACP transport servers cannot be configured via static JSON
- **Bidirectional `mcp/message` notifications**: Client→agent notifications (tools/list_changed) deferred
- **OAuth for ACP transport**: ACP transport inherits ACP session trust model, no OAuth
- **`AcpMCPServerConfig.wrap_with_mcp_filter()`**: Tool filtering via mcp-filter for ACP transport is out of scope; override to raise `NotImplementedError`

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest, fixtures in conftest.py, TestModel)
- **Automated tests**: YES (TDD)
- **Framework**: pytest
- **TDD workflow**: Each task follows RED → GREEN → REFACTOR. Test file created first, then implementation.

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Library/Module**: Use Bash (pytest) — run specific test files, assert PASS
- **API/Schema**: Use Bash (python REPL) — instantiate schemas, validate serialization

---

## Execution Strategy

### Phase Gate: Schema (Sequential — Must Complete First)

All schema changes (`acp/schema/*`, `agentpool_config/mcp_server.py`) and `assert_never` updates must be merged as a single atomic unit before any parallel work begins. This is because:
1. Pydantic discriminated unions require ALL variants to be valid simultaneously
2. `assert_never` sites are runtime crash points
3. Type annotations in dependent files won't compile without schemas

### Parallel Execution Waves

```
Wave 1 (Schema Gate — Sequential, 1 agent):
├── T1: Fix StdioMcpServer.type prerequisite
├── T2: Extend schema layer (capabilities, mcp, messages)
├── T3: Add AcpMCPServerConfig + update MCPServerConfig union
├── T4: Update ALL assert_never sites (atomic)
├── T5: Update filter_servers_by_capabilities
└── T6: Schema validation tests (import, serialize, discriminate)

Wave 2 (Post-Schema — MAX PARALLEL, 5-8 agents):
├── T7: AcpMcpConnectionManager + unit tests
├── T8: AcpMcpTransport + unit tests
├── T9: acp_agent.py integration (manager lifecycle, handlers)
├── T10: _agent_handler routing (acp/agent/connection.py)
├── T11: Integration tests (full lifecycle: connect→message→disconnect)
└── T12: Regression tests (existing stdio/SSE/HTTP MCP)

Wave 3 (Final Integration — 3-4 agents):
├── T13: End-to-end verification (LLM via ACP channel calls MCP tool)
├── T14: Cross-module integration (swap_pool, session fork/resume)
├── T15: Type checking + linting + full test suite
└── T16: Documentation update (RFC decision record, schema reference)

Wave FINAL (4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1-T6 (Schema) | — | T7-T16 |
| T7 (Manager) | T1-T6 | T8, T9, T11, T13 |
| T8 (Transport) | T1-T6 | T9, T11, T13 |
| T9 (acp_agent) | T1-T6, T7 | T11, T13 |
| T10 (_agent_handler) | T1-T6 | T9, T11 |
| T11 (Integration) | T7, T8, T9, T10 | T13, T14 |
| T12 (Regression) | T1-T6 | — (can run anytime after schema) |
| T13 (E2E) | T11 | T15 |
| T14 (Cross-module) | T11 | T15 |
| T15 (Quality) | T13, T14 | F1-F4 |
| T16 (Docs) | T15 | — |

### Agent Dispatch Summary

- **Wave 1**: T1-T6 → `quick` (schema changes, straightforward)
- **Wave 2**: T7 → `deep`, T8 → `deep`, T9 → `unspecified-high`, T10 → `quick`, T11 → `unspecified-high`, T12 → `unspecified-high`
- **Wave 3**: T13 → `deep`, T14 → `unspecified-high`, T15 → `quick`, T16 → `writing`
- **FINAL**: F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

### Wave 1: Schema Gate (Sequential)

- [ ] **T1: Fix StdioMcpServer.type Prerequisite**

  **What to do**:
  - Uncomment `type: Literal["stdio"] = Field(default="stdio", init=False)` at `acp/schema/mcp.py:78-79`
  - Verify discriminated union still works
  - Run existing tests: `pytest tests/servers/acp_server/test_mcp_integration.py -v`

  **Must NOT do**:
  - Do NOT change `init=False` to `init=True`
  - Do NOT add AcpMcpServer yet

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (part of schema gate)
  - **Blocks**: T2-T6
  - **Blocked By**: None

  **References**:
  - `src/acp/schema/mcp.py:69-91` — StdioMcpServer
  - `tests/servers/acp_server/test_mcp_integration.py:31-39` — existing usage

  **Acceptance Criteria**:
  - [ ] `StdioMcpServer(name="t", command="c", args=[], env=[]).type == "stdio"`
  - [ ] Existing tests pass

  **QA Scenarios**:
  ```
  Scenario: StdioMcpServer.type auto-populated
    Tool: Bash (python)
    Steps: python -c "from acp.schema import StdioMcpServer; s = StdioMcpServer(name='t', command='c', args=[], env=[]); print(s.type)"
    Expected: "stdio"
    Evidence: .sisyphus/evidence/t1-type.txt
  ```

  **Commit**: YES
  - Message: `fix(acp_schema): restore StdioMcpServer.type discriminator`
  - Files: `src/acp/schema/mcp.py`

- [ ] **T2: Schema Extensions (Capabilities, McpServer, Methods)**

  **What to do**:
  - `acp/schema/capabilities.py`: Add `acp: bool | None = False` to `McpCapabilities`; add `acp_mcp_servers` param to `AgentCapabilities.create()`
  - `acp/schema/mcp.py`: Add `AcpMcpServer` class with `type: Literal["acp"] = Field(default="acp", init=False)` and `id: str` field; update `McpServer` union
    ```python
    class AcpMcpServer(BaseMcpServer):
        type: Literal["acp"] = Field(default="acp", init=False)
        id: str  # ACP server identifier (maps to AcpMCPServerConfig.acp_id)
    ```
  - `acp/schema/messages.py`: Add `mcp/connect`, `mcp/disconnect` to `AgentMethod`; add `mcp/message` to `ClientMethod`
  - `acp/schema/__init__.py`: Import `AcpMcpServer` from `acp.schema.mcp` and add to `__all__`
  - `acp/schema/agent_responses.py`: Add `acp_mcp_servers: bool = False` parameter to `InitializeResponse.create()`
  - `agentpool_server/acp_server/acp_agent.py`: Pass `acp_mcp_servers=True` in `InitializeResponse.create()` call (line 503)
  - Write schema validation tests FIRST (TDD)

  **Must NOT do**:
  - Do NOT add `acp` branch to `parse_mcp_servers_json()` (dead code)
  - Do NOT change existing defaults

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (part of schema gate)
  - **Blocks**: T3-T6
  - **Blocked By**: T1

  **References**:
  - `src/acp/schema/capabilities.py:151-160` — McpCapabilities
  - `src/acp/schema/mcp.py:69-91` — McpServer pattern
  - `src/acp/schema/messages.py:22-48` — AgentMethod/ClientMethod

  **Acceptance Criteria**:
  - [ ] All 4 McpServer variants instantiate without error
  - [ ] `AgentCapabilities.create(acp_mcp_servers=True).mcp_capabilities.acp is True`
  - [ ] New schema tests pass

  **QA Scenarios**:
  ```
  Scenario: Schema union works
    Tool: Bash (python)
    Steps: python -c "from acp.schema import AcpMcpServer; print(AcpMcpServer(name='t', id='x').type)"
    Expected: "acp"
    Evidence: .sisyphus/evidence/t2-schema.txt
  ```

  **Commit**: YES
  - Message: `feat(acp_schema): add AcpMcpServer, McpCapabilities.acp, mcp methods`
  - Files: `src/acp/schema/capabilities.py`, `src/acp/schema/mcp.py`, `src/acp/schema/messages.py`, `tests/acp/test_schema_mcp_over_acp.py`

- [ ] **T3: AcpMCPServerConfig + MCPServerConfig Union**

  **What to do**:
  - `agentpool_config/mcp_server.py`: Add `AcpMCPServerConfig` class with `type: Literal["acp"]`, `acp_id: str`, `timeout: float = 30.0`
  - Update `MCPServerConfig` union to include `AcpMCPServerConfig`
  - Write tests FIRST (TDD)

  **Must NOT do**:
  - Do NOT modify `parse_mcp_servers_json()`

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (part of schema gate)
  - **Blocks**: T4-T6
  - **Blocked By**: T1, T2

  **References**:
  - `src/agentpool_config/mcp_server.py:377-380` — MCPServerConfig union

  **Acceptance Criteria**:
  - [ ] `AcpMCPServerConfig(acp_id="x").type == "acp"`
  - [ ] `MCPServerConfig` union resolves all 4 variants

  **Commit**: YES
  - Message: `feat(config): add AcpMCPServerConfig`
  - Files: `src/agentpool_config/mcp_server.py`, `tests/agentpool_config/test_mcp_server_config.py`

- [ ] **T4: Atomic assert_never Update + Integration Test**

  **What to do**:
  - Update ALL `assert_never` / exhaustive match sites in a SINGLE commit.
    - For **converters** (items 1-2): implement the trivial field mapping (name→name, id→acp_id) since this has no transport dependency
    - For **client.py** (item 4): add `case AcpMCPServerConfig(): raise NotImplementedError("ACP transport requires AcpMcpConnectionManager injection")` as placeholder — actual transport creation logic is in T10
    - For **claude_code_agent** (items 7-8): add `case AcpMCPServerConfig(): raise NotImplementedError(...)`
    - For **codex_agent** (item 9): add `case AcpMCPServerConfig(): raise TypeError(...)`
    - For all other sites: add minimal handling to prevent runtime crashes
  - Write integration test FIRST (TDD)

  **Must NOT do**:
  - Do NOT split across commits
  - Do NOT skip any site

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (part of schema gate)
  - **Blocks**: T5, T6
  - **Blocked By**: T1-T3

  **References**:
  - `src/agentpool_server/acp_server/converters.py:81-101`
  - `src/agentpool/agents/acp_agent/acp_converters.py:372-409`
  - `src/agentpool/resource_providers/mcp_provider.py:77-94`
  - `src/agentpool/mcp_server/client.py:174-201`
  - `src/agentpool_config/mcp_server.py:377-438`
  - `src/agentpool/agents/claude_code_agent/converters.py:200-228` — two match sites needing AcpMCPServerConfig handling
  - `src/agentpool/agents/codex_agent/codex_converters.py:155-164` — TypeError on unsupported config

  **Acceptance Criteria**:
  - [ ] Integration test passes (all 4 variants through all converters)
  - [ ] `mypy src/` passes
  - [ ] Claude Code and Codex agents explicitly reject ACP configs with clear error messages

  **QA Scenarios**:
  ```
  Scenario: assert_never coverage
    Tool: Bash (pytest)
    Steps: pytest tests/integration/test_assert_never_mcp.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t4-assert-never.txt
  ```

  **Commit**: YES (merge gate)
  - Message: `feat(mcp): atomic assert_never update for ACP transport (all converters + agents)`
  - Files: `src/agentpool_server/acp_server/converters.py`, `src/agentpool/agents/acp_agent/acp_converters.py`, `src/agentpool/agents/claude_code_agent/converters.py`, `src/agentpool/agents/codex_agent/codex_converters.py`, `src/agentpool/resource_providers/mcp_provider.py`, `src/agentpool/mcp_server/client.py`, `src/agentpool_config/mcp_server.py`, `tests/integration/test_assert_never_mcp.py`

- [ ] **T5: filter_servers_by_capabilities Update**

  **What to do**:
  - `agentpool/agents/acp_agent/helpers.py`:
    - Add `supports_acp` check alongside existing `supports_http`/`supports_sse`
    - Add `case AcpMcpServer() if not supports_acp` to filter out ACP servers when capability is OFF
    - Add `supported_acp=supports_acp` to the `logger.warning()` call
  - Write test FIRST (TDD)

  **Must NOT do**:
  - Do NOT change stdio/SSE/HTTP behavior

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (part of schema gate)
  - **Blocks**: T6
  - **Blocked By**: T1-T4

  **References**:
  - `src/agentpool/agents/acp_agent/helpers.py:17-73`

  **Acceptance Criteria**:
  - [ ] ACP server filtered when `acp=False`
  - [ ] ACP server passes when `acp=True`

  **QA Scenarios**:
  ```
  Scenario: Filter ACP when off
    Tool: Bash (pytest)
    Steps: pytest tests/agents/acp_agent/test_filter_servers.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t5-filter.txt
  ```

  **Commit**: YES
  - Message: `feat(acp_agent): filter AcpMcpServer by capabilities`
  - Files: `src/agentpool/agents/acp_agent/helpers.py`, `tests/agents/acp_agent/test_filter_servers.py`

- [ ] **T6: Schema Validation + _agent_handler Routing**

  **What to do**:
  - `acp/agent/connection.py`: Add `case "mcp/connect"` and `case "mcp/disconnect"` to `_agent_handler` match block (lines 267-320)
  - Route to `agent.mcp_connect()` / `agent.mcp_disconnect()` methods
  - Write integration test: verify handler dispatch works with mock agent
  - Run full test suite: `pytest tests/` to verify no regressions

  **Must NOT do**:
  - Do NOT modify other handler cases

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (last schema gate task)
  - **Blocks**: T7-T16
  - **Blocked By**: T1-T5

  **References**:
  - `src/acp/agent/connection.py:267-320` — _agent_handler dispatch
  - `src/agentpool_server/acp_server/acp_agent.py:863` — ext_method pattern (but NOT used here)

  **Acceptance Criteria**:
  - [ ] `_agent_handler` matches "mcp/connect" and routes to agent method
  - [ ] `_agent_handler` matches "mcp/disconnect" and routes to agent method
  - [ ] Full test suite passes: `pytest tests/` (no regressions)

  **QA Scenarios**:
  ```
  Scenario: Handler dispatch works
    Tool: Bash (pytest)
    Steps: pytest tests/acp/test_agent_handler_dispatch.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t6-handler-dispatch.txt
  ```

  **Commit**: YES (schema gate final)
  - Message: `feat(acp): add mcp/connect and mcp/disconnect to _agent_handler`
  - Files: `src/acp/agent/connection.py`, `tests/acp/test_agent_handler_dispatch.py`

### Wave 2: Core Implementation (Parallel)

> **CRITICAL — Pre-Wave 2 Spike**: Before any agent begins T7 or T8, run a 30-second verification:
> ```bash
> python -c "from fastmcp.client.transports import ClientTransport; import inspect; print(inspect.getsource(ClientTransport))"
> ```
> If the `ClientTransport` interface differs from the `connect_session()` async context manager pattern assumed in this plan, update T8 BEFORE assigning to agent. This prevents a day of rework.

- [ ] **T7: AcpMcpConnectionManager + Unit Tests**

  **What to do**:
  - Create `agentpool_server/acp_server/acp_mcp_manager.py`
  - Implement `AcpMcpConnectionManager`:
    - `register_server(acp_id: str) -> None`
    - `connect(acp_id: str) -> str` (returns `connection_id`)
    - `send_message(connection_id: str, request: McpJsonRpcRequest) -> McpJsonRpcResponse`
    - `disconnect(connection_id: str) -> None`
    - `cleanup_all() -> None`
  - Use `TypedDict` for `McpJsonRpcRequest`/`Response` (zero Any policy)
  - Write unit tests FIRST (TDD): mock ACP client, test state transitions

  **Must NOT do**:
  - Do NOT implement actual ACP forwarding yet (that's T8 Transport)
  - Do NOT add rate limiting (deferred)

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T8-T12)
  - **Blocks**: T9, T11
  - **Blocked By**: T1-T6 (Schema gate)

  **References**:
  - RFC Technical Design Section 2: AcpMcpConnectionManager
  - `src/acp/client/protocol.py` — Client interface for `send_request`

  **Acceptance Criteria**:
  - [ ] All unit tests pass: `pytest tests/acp_server/test_acp_mcp_manager.py -v`
  - [ ] `connect()` returns unique `connection_id` per call
  - [ ] `send_message()` routes to correct connection
  - [ ] `disconnect()` removes connection from active set
  - [ ] `cleanup_all()` removes all connections

  **QA Scenarios**:
  ```
  Scenario: Manager state machine
    Tool: Bash (pytest)
    Steps: pytest tests/acp_server/test_acp_mcp_manager.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t7-manager-unit.txt
  ```

  **Commit**: YES
  - Message: `feat(acp_mcp): add AcpMcpConnectionManager`
  - Files: `src/agentpool_server/acp_server/acp_mcp_manager.py`, `tests/acp_server/test_acp_mcp_manager.py`

- [ ] **T8: AcpMcpTransport (fastmcp ClientTransport) + Unit Tests**

  **What to do**:
  - Create `agentpool_server/acp_server/acp_mcp_transport.py`
  - Implement `AcpMcpTransport` inheriting from `fastmcp.ClientTransport`
  - Implement the required `connect_session()` async context manager:
    ```python
    @asynccontextmanager
    async def connect_session(self, **session_kwargs) -> AsyncIterator[ClientSession]:
        # Create memory object streams (anyio)
        read_stream, write_stream = anyio.create_memory_object_stream(...)
        # Start background task: bridges stream <-> ACP mcp/message requests
        # Yield ClientSession(read_stream=read_stream, write_stream=write_stream)
    ```
  - The transport accepts a `send_message_callback` in `__init__` (injected by `AcpMcpConnectionManager`)
  - Maintain independent MCP JSON-RPC id space (isolated from ACP JSON-RPC id)
  - Handle request-response pairing by MCP id
  - Write unit tests FIRST (TDD): mock callback, test stream pairing

  **Must NOT do**:
  - Do NOT implement the `connect()/send()/receive()/close()` interface (that's a v2 pattern, not fastmcp v3)
  - Do NOT handle bidirectional client->agent notifications yet (deferred)
  - Do NOT add payload size limits yet (deferred)

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T7, T9-T12)
  - **Blocks**: T9, T11
  - **Blocked By**: T1-T6 (Schema gate)

  **References**:
  - RFC Technical Design Section 4: fastmcp ClientTransport Implementation
  - `src/agentpool/mcp_server/client.py:174-201` — existing transport pattern

  **Acceptance Criteria**:
  - [ ] Transport connects and exchanges JSON-RPC messages via mock streams
  - [ ] MCP id space is independent of ACP id space
  - [ ] Request-response pairing works correctly
  - [ ] All unit tests pass: `pytest tests/mcp_server/test_acp_mcp_transport.py -v`

  **QA Scenarios**:
  ```
  Scenario: Transport request-response pairing
    Tool: Bash (pytest)
    Steps: pytest tests/mcp_server/test_acp_mcp_transport.py::test_request_response_pairing -v
    Expected: PASS
    Evidence: .sisyphus/evidence/t8-transport-pairing.txt
  ```

  **Commit**: YES
  - Message: `feat(acp_mcp): add AcpMcpTransport (fastmcp ClientTransport)`
  - Files: `src/agentpool_server/acp_server/acp_mcp_transport.py`, `tests/mcp_server/test_acp_mcp_transport.py`

- [ ] **T9: acp_agent.py Integration**

  **What to do**:
  - `agentpool_server/acp_server/acp_agent.py`:
    - Add `_mcp_manager: AcpMcpConnectionManager` field to `AgentPoolACPAgent`
    - Instantiate manager in `__post_init__` or `initialize()`
    - Implement `mcp_connect()` and `mcp_disconnect()` methods (handler delegates to manager)
    - Register ACP MCP servers from `session/new` into manager
    - Call `manager.cleanup_all()` on connection teardown
    - Pass `acp_mcp_servers=True` to `InitializeResponse.create()` (line 503)
  - **Design Decision — Connection Wiring (Option A, RECOMMENDED)**:
    - `AcpMcpConnectionManager` constructs `AcpMcpTransport` instances directly
    - Manager injects transport into `MCPClient` via optional `transport` parameter
    - `MCPClient._get_client()` skips transport creation when `transport` is pre-injected
    - This avoids the architectural gap where `MCPClient` has no access to ACP connection
  - Write integration tests FIRST (TDD)

  **Must NOT do**:
  - Do NOT modify existing session/agent creation logic beyond MCP registration

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T7, T8, T10-T12)
  - **Blocks**: T11
  - **Blocked By**: T1-T6 (Schema gate), T7 (Manager)

  **References**:
  - `src/agentpool_server/acp_server/acp_agent.py:189-1070` — AgentPoolACPAgent
  - RFC Technical Design Section 3: acp_agent.py Integration

  **Acceptance Criteria**:
  - [ ] `AgentPoolACPAgent` has `_mcp_manager` attribute
  - [ ] `initialize()` returns `mcpCapabilities.acp: true`
  - [ ] Session creation registers ACP MCP servers with manager

  **QA Scenarios**:
  ```
  Scenario: Agent integration
    Tool: Bash (pytest)
    Steps: pytest tests/servers/acp_server/test_acp_agent_mcp.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t9-agent-integration.txt
  ```

  **Commit**: YES
  - Message: `feat(acp_agent): integrate AcpMcpConnectionManager into AgentPoolACPAgent`
  - Files: `src/agentpool_server/acp_server/acp_agent.py`, `tests/servers/acp_server/test_acp_agent_mcp.py`

- [ ] **T10: Forward/Reverse Converter Updates + MCPClient Integration**

  **What to do**:
  - `agentpool_server/acp_server/converters.py`: Add `case AcpMcpServer()` to `convert_acp_mcp_server_to_config()`
  - `agentpool/agents/acp_agent/acp_converters.py`:
    - Add `case AcpMCPServerConfig()` to `mcp_config_to_acp()`
    - **Guard pre-match crash**: Before calling `config.wrap_with_mcp_filter()`, add `if not isinstance(config, AcpMCPServerConfig) and config.needs_tool_filtering():` (ACP configs don't support mcp-filter wrapping)
  - `agentpool/agents/claude_code_agent/converters.py`: Add explicit `case AcpMCPServerConfig(): raise NotImplementedError("ACP transport MCP servers not supported by Claude Code agent")` at both match sites (lines 209-210 and 227-228)
  - `agentpool/agents/codex_agent/codex_converters.py`: Add explicit `case AcpMCPServerConfig(): raise TypeError("ACP transport MCP servers not supported by Codex agent")` (line 163)
  - `agentpool/mcp_server/client.py`: Add `case AcpMCPServerConfig()` to `MCPClient._get_client()` — if `transport` is pre-injected, use it; else raise NotImplementedError
  - `agentpool/resource_providers/mcp_provider.py`: Add `"acp"` to `transport_type` return Literal
  - Write tests FIRST (TDD) for all changes

  **Must NOT do**:
  - Do NOT modify stdio/SSE/HTTP transport creation
  - Do NOT silently skip ACP configs in Claude Code / Codex converters

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T7-T9, T11-T12)
  - **Blocks**: T11
  - **Blocked By**: T1-T6 (Schema gate), T8 (Transport)

  **References**:
  - `src/agentpool_server/acp_server/converters.py:81-101`
  - `src/agentpool/agents/acp_agent/acp_converters.py:372-409`
  - `src/agentpool/mcp_server/client.py:174-201`
  - `src/agentpool/resource_providers/mcp_provider.py:77-94`

  **Acceptance Criteria**:
  - [ ] `MCPClient._get_client()` accepts optional `transport` parameter
  - [ ] When `transport` is pre-injected (from AcpMcpConnectionManager), uses it for ACP config
  - [ ] When `transport` is None and config is ACP, raises clear error
  - [ ] Forward converter maps `AcpMcpServer(name, id)` → `AcpMCPServerConfig(name, acp_id=id)` (already done in T4, verify)
  - [ ] Reverse converter maps `AcpMCPServerConfig(name, acp_id)` → `AcpMcpServer(name, id=acp_id)` (already done in T4, verify)
  - [ ] Claude Code / Codex agents explicitly reject ACP configs with clear error messages (already done in T4, verify)

  **QA Scenarios**:
  ```
  Scenario: All converter paths work
    Tool: Bash (pytest)
    Steps: pytest tests/integration/test_acp_mcp_converters.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t10-converters.txt
  ```

  **Commit**: YES
  - Message: `feat(mcp): add ACP transport to converters and MCPClient`
  - Files: `src/agentpool_server/acp_server/converters.py`, `src/agentpool/agents/acp_agent/acp_converters.py`, `src/agentpool/mcp_server/client.py`, `src/agentpool/resource_providers/mcp_provider.py`, `tests/integration/test_acp_mcp_converters.py`

- [ ] **T11: Integration Tests (Full Lifecycle)**

  **What to do**:
  - Write integration test: mock ACP client, full lifecycle `session/new` -> `mcp/connect` -> `mcp/message` -> `mcp/disconnect`
  - Test error cases: unknown `acpId`, unknown `connectionId`, timeout
  - Test concurrent `mcp/message` on different `connectionId`s

  **Must NOT do**:
  - Do NOT test actual LLM tool calling (that's T13 E2E)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T7-T10, T12)
  - **Blocks**: T13, T14
  - **Blocked By**: T7, T8, T9, T10

  **References**:
  - `tests/servers/acp_server/test_mcp_integration.py` — existing integration test pattern
  - RFC Implementation Plan Section D: Test Strategy

  **Acceptance Criteria**:
  - [ ] Full lifecycle test passes
  - [ ] Error case tests pass
  - [ ] Concurrent message tests pass

  **QA Scenarios**:
  ```
  Scenario: Full lifecycle
    Tool: Bash (pytest)
    Steps: pytest tests/integration/test_acp_mcp_lifecycle.py -v
    Expected: All PASS
    Evidence: .sisyphus/evidence/t11-lifecycle.txt
  ```

  **Commit**: YES
  - Message: `test(acp_mcp): add integration tests for full MCP-over-ACP lifecycle`
  - Files: `tests/integration/test_acp_mcp_lifecycle.py`

- [ ] **T12: Regression Tests (stdio/SSE/HTTP MCP)**

  **What to do**:
  - Run full existing test suite: `pytest tests/`
  - Verify no regressions in stdio/SSE/HTTP MCP paths
  - Fix any issues caused by schema changes

  **Must NOT do**:
  - Do NOT skip any existing tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T7-T11)
  - **Blocks**: T15
  - **Blocked By**: T1-T6 (Schema gate)

  **References**:
  - `tests/` — full test suite

  **Acceptance Criteria**:
  - [ ] `pytest tests/` passes with zero failures (excluding known flaky tests)
  - [ ] Existing MCP integration tests pass

  **QA Scenarios**:
  ```
  Scenario: Full regression
    Tool: Bash
    Steps: pytest tests/ -x --tb=short
    Expected: All PASS
    Evidence: .sisyphus/evidence/t12-regression.txt
  ```

  **Commit**: NO (results only, no code changes if all pass)

### Wave 3: Final Integration

- [ ] **T13: End-to-End Verification**

  **What to do**:
  - Create end-to-end test: mock ACP client provides MCP tool, LLM agent calls tool via ACP channel
  - Verify full flow: `session/new` with AcpMcpServer -> `mcp/connect` -> LLM prompt -> `mcp/message` tool call -> result returned to LLM
  - Capture evidence (logs, screenshots if applicable)

  **Must NOT do**:
  - Do NOT require real external MCP server (use mock)

  **Recommended Agent Profile**:
  - **Category**: `deep`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T11)
  - **Blocks**: T15
  - **Blocked By**: T11

  **References**:
  - `tests/servers/acp_server/test_mcp_integration.py` — existing E2E pattern

  **Acceptance Criteria**:
  - [ ] E2E test passes: LLM successfully calls MCP tool via ACP channel
  - [ ] Evidence captured

  **QA Scenarios**:
  ```
  Scenario: LLM calls MCP tool via ACP
    Tool: Bash (pytest)
    Steps: pytest tests/e2e/test_acp_mcp_e2e.py -v
    Expected: PASS
    Evidence: .sisyphus/evidence/t13-e2e.txt
  ```

  **Commit**: YES
  - Message: `test(e2e): add end-to-end test for MCP-over-ACP`
  - Files: `tests/e2e/test_acp_mcp_e2e.py`

- [ ] **T14: Cross-Module Integration (swap_pool, session fork/resume)**

  **What to do**:
  - Verify `swap_pool()` handles active MCP-over-ACP connections correctly
  - Verify session fork/resume with AcpMcpServer configs works
  - Add tests if gaps found

  **Must NOT do**:
  - Do NOT modify core pool/session logic unless bug found

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T11)
  - **Blocks**: T15
  - **Blocked By**: T11

  **References**:
  - `src/agentpool_server/acp_server/acp_agent.py:985-1070` — swap_pool()

  **Acceptance Criteria**:
  - [ ] `swap_pool()` does not leak MCP connections
  - [ ] Session fork/resume works with ACP MCP servers

  **QA Scenarios**:
  ```
  Scenario: swap_pool cleanup
    Tool: Bash (pytest)
    Steps: pytest tests/servers/acp_server/test_swap_pool_mcp.py -v
    Expected: PASS
    Evidence: .sisyphus/evidence/t14-swap-pool.txt
  ```

  **Commit**: YES (if fixes needed)

- [ ] **T15: Quality Gate (Type Check + Lint + Full Test Suite)**

  **What to do**:
  - Run `mypy src/` — fix all type errors
  - Run `ruff check src/` — fix all lint errors
  - Run `pytest tests/` — ensure full suite passes
  - Update RFC decision record with actual decisions

  **Must NOT do**:
  - Do NOT ignore type errors with `# type: ignore` without justification

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: NO (final gate)
  - **Blocks**: F1-F4
  - **Blocked By**: T12, T13, T14

  **Acceptance Criteria**:
  - [ ] `mypy src/` passes
  - [ ] `ruff check src/` passes
  - [ ] `pytest tests/` passes

  **QA Scenarios**:
  ```
  Scenario: Quality gate
    Tool: Bash
    Steps: mypy src/ && ruff check src/ && pytest tests/
    Expected: All PASS
    Evidence: .sisyphus/evidence/t15-quality.txt
  ```

  **Commit**: YES (if fixes needed)

- [ ] **T16: Documentation Update**

  **What to do**:
  - Update RFC-0033 decision record with actual decisions
  - Add schema reference to any relevant docs
  - Document feature flag behavior

  **Must NOT do**:
  - Do NOT write user-facing docs unless explicitly requested

  **Recommended Agent Profile**:
  - **Category**: `writing`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: T15

  **Acceptance Criteria**:
  - [ ] RFC decision record updated
  - [ ] Feature flag documented

  **Commit**: YES
  - Message: `docs(rfc-0033): update decision record and schema reference`
  - Files: `docs/rfcs/draft/RFC-0033-mcp-over-acp-transport.md`

---

## Final Verification Wave

> **4 review agents run in PARALLEL. ALL must APPROVE.**

- [ ] **F1: Plan Compliance Audit** — `oracle`

  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist.

  **Output**: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT`

- [ ] **F2: Code Quality Review** — `unspecified-high`

  Run `mypy src/` + `ruff check src/` + `pytest tests/`. Review all changed files for: `as any`/`@ts-ignore`, empty catches, `console.log` in prod, commented-out code, unused imports. Check AI slop patterns.

  **Output**: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N/N] | VERDICT`

- [ ] **F3: Real Manual QA** — `unspecified-high`

  Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration. Test edge cases: empty state, invalid input, rapid actions.

  **Output**: `Scenarios [N/N] | Integration [N/N] | Edge Cases [N] | VERDICT`

- [ ] **F4: Scope Fidelity Check** — `deep`

  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Detect cross-task contamination.

  **Output**: `Tasks [N/N] | Contamination [CLEAN/N] | Unaccounted [CLEAN/N] | VERDICT`

---

## Commit Strategy

- **Schema gate** (T1-T6): Single atomic commit or tightly grouped commits
- **Core implementation** (T7-T12): One commit per task
- **Integration** (T13-T16): One commit per task
- **Final verification**: No commits (review only)

## Success Criteria

### Verification Commands
```bash
# Type checking
mypy src/

# Linting
ruff check src/

# Full test suite
pytest tests/

# Specific new tests
pytest tests/acp_server/test_acp_mcp_manager.py
pytest tests/mcp_server/test_acp_mcp_transport.py
pytest tests/integration/test_acp_mcp_lifecycle.py
pytest tests/e2e/test_acp_mcp_e2e.py
```

### Final Checklist
- [ ] All "Must Have" present and verified
- [ ] All "Must NOT Have" absent and verified
- [ ] All tests pass (`pytest tests/`)
- [ ] Type check passes (`mypy src/`)
- [ ] Lint passes (`ruff check src/`)
- [ ] Evidence files exist for all QA scenarios
- [ ] RFC decision record updated
- [ ] No `assert_never` runtime crashes