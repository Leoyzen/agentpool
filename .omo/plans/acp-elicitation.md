# ACP Elicitation Support

## TL;DR

> **Quick Summary**: Add `elicitation/create` JSON-RPC method support to agentpool as an ACP server, enabling proper structured user input (form + URL modes) with backward-compatible fallback to `request_permission` for legacy clients.
> 
> **Deliverables**:
> - New `src/acp/schema/elicitation.py` module with request/response/notification/error types
> - `ElicitationCapabilities` added to `ClientCapabilities` for capability negotiation
> - `elicitation_create()` method on Client protocol and all 3 client implementations
> - Routing for `"elicitation/create"` in `ClientSideConnection._handle_client_method()`
> - `ElicitationCompleteNotification` in `AgentNotification` union for URL-mode completion
> - Rewritten `ACPInputProvider.get_elicitation()` with capability-gated dual-path
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 5 → Task 8 → Task 10 → Task 12 → F1-F4

---

## Context

### Original Request
Add ACP elicitation format support. ACP-only, minimal dev at converter layer. Focus on agentpool as ACP server (outgoing elicitation). Backward compat with fallback to permission requests. Add ElicitationCompleteNotification as new notification type.

### Interview Summary
**Key Discussions**:
- User wants ACP-only scope — no MCP, AG-UI, or OpenCode server changes
- Agentpool as ACP server is the priority (sending elicitation to clients)
- Backward compatibility via fallback to request_permission hack when client doesn't declare elicitation capability
- New notification type (ElicitationCompleteNotification) for URL-mode completion
- The ACP elicitation spec is an RFD (Request for Dialog), not yet stabilized

**Research Findings**:
- Internal elicitation types exist at `src/agentpool/ui/elicitation.py` with `to_mcp_schema()` conversion
- `ACPInputProvider` currently HACKS all elicitation into `request_permission` calls — lossy for string/number/form types
- `ACPSession` stores `client_capabilities: ClientCapabilities` from initialize
- `ClientSideConnection._handle_client_method()` routes by method string match
- 3 client implementations: DefaultACPClient (auto-grant), HeadlessACPClient (auto-grant), NoOpClient (minimal)
- `ACPNotifications` wraps `client.session_update()` for sending notifications
- **Metis critical finding**: `ElicitationCompleteNotification` as fire-and-forget notification cannot be awaited. URL-mode needs either long-lived request/response (Option A) or notification→future correlation registry (Option B). Plan uses Option A (long-lived request) for form-mode and adds notification type for URL-mode completion signaling (non-blocking).

### Metis Review
**Identified Gaps** (addressed):
- Transport issue with ElicitationCompleteNotification: addressed by making it a signaling notification (fire-and-forget) rather than an awaitable mechanism. The `elicitation/create` request itself is request/response and stays open for URL mode.
- RFD vs stable spec risk: accepted as-is; using the official method names per RFD
- ACPInputProvider bypasses internal ElicitRequest types: continues operating on MCP params for now
- Boolean standalone bug in fallback path: documented but not fixed in this plan
- No timeout mechanism: accepted as known limitation (same as existing request_permission)

---

## Work Objectives

### Core Objective
Implement `elicitation/create` JSON-RPC method in agentpool's ACP server, with capability negotiation and backward-compatible fallback.

### Concrete Deliverables
- `src/acp/schema/elicitation.py` — new module with all elicitation types
- Updated `src/acp/schema/capabilities.py` — ElicitationCapabilities + ClientCapabilities.elicitation
- Updated `src/acp/schema/agent_requests.py` — ElicitationCreateRequest in AgentRequest union
- Updated `src/acp/schema/client_responses.py` — ElicitationCreateResponse in ClientResponse union
- Updated `src/acp/schema/notifications.py` — ElicitationCompleteNotification in AgentNotification union
- Updated `src/acp/schema/messages.py` — "elicitation/create" in ClientMethod literal
- Updated `src/acp/schema/__init__.py` — all new exports
- Updated `src/acp/client/protocol.py` — elicitation_create() on Client protocol
- Updated `src/acp/agent/acp_requests.py` — elicitation_create() convenience method
- Updated `src/acp/client/connection.py` — routing case + notification handling
- Updated 3 client implementations — elicitation_create() method
- Rewritten `src/agentpool_server/acp_server/input_provider.py` — capability-gated dual-path

### Definition of Done
- [ ] `uv run mypy src/acp/ src/agentpool_server/acp_server/` — zero errors
- [ ] `uv run ruff check src/acp/ src/agentpool_server/acp_server/` — zero errors
- [ ] `uv run pytest tests/ -k "acp"` — all existing tests pass
- [ ] `ClientCapabilities(elicitation=ElicitationCapabilities(form=True, url=True)).model_dump()` round-trips correctly
- [ ] `ElicitationCreateRequest` with mode="form" serializes/deserializes correctly
- [ ] `ACPInputProvider.get_elicitation()` uses `elicitation_create` when capability declared
- [ ] `ACPInputProvider.get_elicitation()` falls back to `request_permission` when capability absent

### Must Have
- All 4 ACP schema types (ElicitationCreateRequest, ElicitationCreateResponse, ElicitationCompleteNotification, URLElicitationRequiredError)
- ElicitationCapabilities in ClientCapabilities
- elicitation_create() on Client protocol + all 3 client implementations
- Routing in ClientSideConnection._handle_client_method()
- Capability-gated dual-path in ACPInputProvider.get_elicitation()
- Fallback to existing request_permission hack for legacy clients

### Must NOT Have (Guardrails)
- NO changes to `src/agentpool/ui/elicitation.py` internal types (they serve MCP path)
- NO changes to `InputProvider.get_elicitation()` base class signature
- NO changes to MCP server, AG-UI server, or OpenCode server code
- NO `to_acp_schema()` method on internal ElicitRequest types
- NO timeout mechanism addition (orthogonal to this feature)
- NO nested agent elicitation forwarding (ACP client path is out of scope)
- NO fixing the boolean standalone bug in fallback path (separate issue)
- NO new session_update types for elicitation

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after (add tests after implementation)
- **Framework**: pytest (existing)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Schema types**: Use Bash (uv run python) — import, construct, validate, round-trip
- **Protocol**: Use Bash (uv run pytest) — existing test infrastructure
- **Server logic**: Use Bash (uv run pytest) — unit tests for dual-path logic

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — schema foundation):
├── Task 1: Create elicitation.py module with all 4 types [quick]
├── Task 2: Add ElicitationCapabilities to capabilities.py [quick]
├── Task 3: Add ElicitationCreateRequest to AgentRequest union [quick]
├── Task 4: Add ElicitationCreateResponse to ClientResponse union [quick]
├── Task 5: Add types to notifications.py, messages.py, __init__.py [quick]

Wave 2 (After Wave 1 — protocol layer):
├── Task 6: Add elicitation_create() to Client protocol [quick]
├── Task 7: Add elicitation_create() to ACPRequests [quick]
├── Task 8: Add routing in _handle_client_method + notification handling [quick]
├── Task 9: Implement elicitation_create in all 3 client implementations [quick]

Wave 3 (After Wave 2 — server integration):
├── Task 10: Rewrite ACPInputProvider.get_elicitation() with dual-path [deep]
├── Task 11: Add send_elicitation_complete() to ACPNotifications [quick]

Wave 4 (Verification):
├── Task 12: Run mypy + ruff + pytest verification [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
├── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: Task 1 → Task 5 → Task 8 → Task 10 → Task 12 → F1-F4
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 5 (Wave 1)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | - | 3, 4, 5, 6, 7, 8 | 1 |
| 2 | - | 5, 10 | 1 |
| 3 | 1 | 5 | 1 |
| 4 | 1 | 5 | 1 |
| 5 | 1, 2, 3, 4 | 6, 7, 8 | 1 |
| 6 | 5 | 9 | 2 |
| 7 | 5 | 9 | 2 |
| 8 | 5 | 9, 10 | 2 |
| 9 | 6, 7, 8 | 10 | 2 |
| 10 | 2, 8, 9 | 12 | 3 |
| 11 | 5 | 12 | 3 |
| 12 | 10, 11 | F1-F4 | 4 |

### Agent Dispatch Summary

- **Wave 1**: 5 tasks — all `quick`
- **Wave 2**: 4 tasks — all `quick`
- **Wave 3**: 2 tasks — T10 `deep`, T11 `quick`
- **Wave 4**: 1 task — `quick`
- **FINAL**: 4 tasks — F1 `oracle`, F2 `unspecified-high`, F3 `unspecified-high`, F4 `deep`

---

## TODOs

- [x] 1. Create `src/acp/schema/elicitation.py` with all 4 elicitation types

  **What to do**:
  - Create new file `src/acp/schema/elicitation.py`
  - Define `ElicitationCreateRequest(BaseAgentRequest)` with fields: `message: str`, `mode: Literal["form", "url"]`, `requested_schema: dict[str, Any] | None = None`, `url: str | None = None`, `elicitation_id: str | None = None`, `tool_call_id: str | None = None`, `request_id: str | None = None`
  - Define `ElicitationCreateResponse(Response)` with fields: `action: Literal["accept", "decline", "cancel"]`, `content: dict[str, Any] | None = None`
  - Define `ElicitationCompleteNotification(AnnotatedObject)` with fields: `session_id: str`, `elicitation_id: str`, `result: Literal["completed", "expired", "error"]`
  - Define `URLElicitationRequiredError` with `code: int = -32042` and `url: str`
  - Import `BaseAgentRequest` from `acp.schema.agent_requests`, `Response` from `acp.schema.base`, `AnnotatedObject` from `acp.schema.base`

  **Must NOT do**:
  - Do NOT import or reference `agentpool/ui/elicitation.py` internal types
  - Do NOT add `to_acp_schema()` or `from_acp_schema()` conversion methods

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Tasks 3, 4, 5, 6, 7, 8
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/acp/schema/agent_requests.py:102-115` — `RequestPermissionRequest` pattern for how to extend `BaseAgentRequest` with domain-specific fields
  - `src/acp/schema/client_responses.py:70-84` — `RequestPermissionResponse` pattern for response with convenience class methods
  - `src/acp/schema/notifications.py:16-28` — `SessionNotification` pattern for notification types with `session_id`
  - `src/acp/schema/base.py` — `AnnotatedObject`, `Request`, `Response` base classes with alias_generator=to_camel

  **API/Type References**:
  - ACP RFD elicitation spec: `elicitation/create` method, `mode: "form" | "url"`, three-action response model

  **WHY Each Reference Matters**:
  - `RequestPermissionRequest`: Shows exact pattern for request type — field naming, docstrings, BaseAgentRequest inheritance
  - `RequestPermissionResponse`: Shows response pattern with outcome types and convenience methods
  - `SessionNotification`: Shows notification pattern with session_id scoping
  - `base.py`: Understanding Schema/AnnotatedObject/Request/Response hierarchy is essential for correct inheritance

  **Acceptance Criteria**:
  - [ ] File `src/acp/schema/elicitation.py` exists with all 4 types defined
  - [ ] `ElicitationCreateRequest` extends `BaseAgentRequest` and has `session_id` from parent
  - [ ] `ElicitationCreateResponse` extends `Response`
  - [ ] `ElicitationCompleteNotification` extends `AnnotatedObject`
  - [ ] `URLElicitationRequiredError` has `code = -32042`

  **QA Scenarios**:

  ```
  Scenario: ElicitationCreateRequest construction and serialization
    Tool: Bash (uv run python)
    Preconditions: File exists and is importable
    Steps:
      1. Run: uv run python -c "from acp.schema.elicitation import ElicitationCreateRequest; r = ElicitationCreateRequest(session_id='s1', message='Enter name', mode='form', requested_schema={'type':'string','title':'Name'}); print(r.model_dump(by_alias=True, exclude_none=True))"
      2. Assert output contains 'sessionId', 'message', 'mode', 'requestedSchema'
    Expected Result: Valid JSON with camelCase aliases, all fields present
    Failure Indicators: ImportError, Pydantic validation error, missing fields
    Evidence: .sisyphus/evidence/task-1-request-serialization.txt

  Scenario: ElicitationCreateResponse three-action model
    Tool: Bash (uv run python)
    Preconditions: File exists and is importable
    Steps:
      1. Run: uv run python -c "from acp.schema.elicitation import ElicitationCreateResponse; r = ElicitationCreateResponse(action='accept', content={'name':'Alice'}); print(r.model_dump(by_alias=True))"
      2. Run: uv run python -c "from acp.schema.elicitation import ElicitationCreateResponse; r = ElicitationCreateResponse(action='decline'); print(r.model_dump(by_alias=True))"
    Expected Result: accept with content, decline without content
    Failure Indicators: Pydantic validation error, content not None for decline
    Evidence: .sisyphus/evidence/task-1-response-actions.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(acp): add elicitation schema types`
  - Files: `src/acp/schema/elicitation.py`
  - Pre-commit: `uv run ruff check src/acp/schema/elicitation.py`

- [x] 2. Add `ElicitationCapabilities` to `ClientCapabilities` in capabilities.py

  **What to do**:
  - Add new class `ElicitationCapabilities(AnnotatedObject)` with `form: bool = False` and `url: bool = False`
  - Add `elicitation: ElicitationCapabilities | None = None` field to `ClientCapabilities`
  - Update `ClientCapabilities.create()` classmethod to accept and pass through `elicitation` param
  - Add docstring referencing the ACP RFD

  **Must NOT do**:
  - Do NOT change existing `ClientCapabilities` fields or their defaults
  - Do NOT make `elicitation` required — it MUST be `None` by default for backward compat

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: Tasks 5, 10
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/acp/schema/capabilities.py:13-24` — `FileSystemCapability` pattern: AnnotatedObject with bool fields, used as sub-capability of ClientCapabilities
  - `src/acp/schema/capabilities.py:46-53` — How `fs: FileSystemCapability | None = Field(default_factory=FileSystemCapability)` is declared in ClientCapabilities
  - `src/acp/schema/capabilities.py:58-78` — `ClientCapabilities.create()` classmethod pattern

  **WHY Each Reference Matters**:
  - `FileSystemCapability`: Exact pattern to follow for ElicitationCapabilities — simple AnnotatedObject with bool fields
  - `ClientCapabilities.fs`: Shows how to add a sub-capability field — use `None` default, NOT `Field(default_factory=...)` since we want `None` not an empty instance
  - `create()`: Must update this factory method to include the new field

  **Acceptance Criteria**:
  - [ ] `ElicitationCapabilities` class exists with `form: bool = False` and `url: bool = False`
  - [ ] `ClientCapabilities` has `elicitation: ElicitationCapabilities | None = None`
  - [ ] `ClientCapabilities.create()` accepts `elicitation` param
  - [ ] Existing `ClientCapabilities` serialization still works (no breaking changes)

  **QA Scenarios**:

  ```
  Scenario: ElicitationCapabilities round-trip
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import ClientCapabilities, ElicitationCapabilities; c = ClientCapabilities(elicitation=ElicitationCapabilities(form=True, url=True)); d = c.model_dump(by_alias=True); print(d); c2 = ClientCapabilities.model_validate(d); print(c2.elicitation)"
    Expected Result: elicitation dict with form=True, url=True; round-trip preserves values
    Failure Indicators: Missing elicitation field, validation error
    Evidence: .sisyphus/evidence/task-2-capabilities-roundtrip.txt

  Scenario: ClientCapabilities without elicitation (backward compat)
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import ClientCapabilities; c = ClientCapabilities(); print(c.elicitation); print(c.model_dump(by_alias=True))"
    Expected Result: elicitation=None; output does not contain 'elicitation' key (exclude_none)
    Failure Indicators: elicitation not None, output contains 'elicitation' with default value
    Evidence: .sisyphus/evidence/task-2-backward-compat.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(acp): add elicitation schema types`
  - Files: `src/acp/schema/capabilities.py`

- [x] 3. Add `ElicitationCreateRequest` to `AgentRequest` union in agent_requests.py

  **What to do**:
  - Add `from acp.schema.elicitation import ElicitationCreateRequest  # noqa: TC001` import
  - Add `ElicitationCreateRequest` to the `AgentRequest` union type

  **Must NOT do**:
  - Do NOT modify existing union members
  - Do NOT add elicitation-specific logic to agent_requests.py

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1 (needs ElicitationCreateRequest type)

  **References**:

  **Pattern References**:
  - `src/acp/schema/agent_requests.py:117-126` — Current `AgentRequest` union definition, shows exact pattern for adding new member

  **WHY Each Reference Matters**:
  - Must add to the union in exact same style as existing members

  **Acceptance Criteria**:
  - [ ] `ElicitationCreateRequest` import added with `# noqa: TC001`
  - [ ] `AgentRequest` union includes `ElicitationCreateRequest`

  **QA Scenarios**:

  ```
  Scenario: AgentRequest union includes elicitation
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import AgentRequest, ElicitationCreateRequest; print(ElicitationCreateRequest in AgentRequest.__args__)"
    Expected Result: True
    Failure Indicators: ImportError, False
    Evidence: .sisyphus/evidence/task-3-agent-request-union.txt
  ```

  **Commit**: YES (groups with Wave 1)

- [x] 4. Add `ElicitationCreateResponse` to `ClientResponse` union in client_responses.py

  **What to do**:
  - Add `from acp.schema.elicitation import ElicitationCreateResponse  # noqa: TC001` import
  - Add `ElicitationCreateResponse` to the `ClientResponse` union type

  **Must NOT do**:
  - Do NOT modify existing union members or existing response types

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1 (needs ElicitationCreateResponse type)

  **References**:

  **Pattern References**:
  - `src/acp/schema/client_responses.py:87-96` — Current `ClientResponse` union definition

  **WHY Each Reference Matters**:
  - Must add to the union in exact same style

  **Acceptance Criteria**:
  - [ ] `ElicitationCreateResponse` import added with `# noqa: TC001`
  - [ ] `ClientResponse` union includes `ElicitationCreateResponse`

  **QA Scenarios**:

  ```
  Scenario: ClientResponse union includes elicitation
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import ClientResponse, ElicitationCreateResponse; print(ElicitationCreateResponse in ClientResponse.__args__)"
    Expected Result: True
    Evidence: .sisyphus/evidence/task-4-client-response-union.txt
  ```

  **Commit**: YES (groups with Wave 1)

- [x] 5. Add elicitation types to notifications.py, messages.py, and __init__.py

  **What to do**:
  - **notifications.py**: Add `from acp.schema.elicitation import ElicitationCompleteNotification  # noqa: TC001` and add `ElicitationCompleteNotification` to `AgentNotification` union
  - **messages.py**: Add `"elicitation/create"` to `ClientMethod` literal
  - **__init__.py**: Add imports and `__all__` entries for: `ElicitationCapabilities`, `ElicitationCompleteNotification`, `ElicitationCreateRequest`, `ElicitationCreateResponse`, `URLElicitationRequiredError`

  **Must NOT do**:
  - Do NOT add `ElicitationCompleteNotification` to `ClientNotification` — it's agent→client only
  - Do NOT add `"elicitation/complete"` to `ClientMethod` — it's a notification, not a request method
  - Do NOT remove any existing exports from `__init__.py`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (sequential after Tasks 1-4)
  - **Blocks**: Tasks 6, 7, 8
  - **Blocked By**: Tasks 1, 2, 3, 4

  **References**:

  **Pattern References**:
  - `src/acp/schema/notifications.py:68` — `AgentNotification` union definition
  - `src/acp/schema/messages.py:36-46` — `ClientMethod` literal definition
  - `src/acp/schema/__init__.py:32-44` — Import block for capabilities
  - `src/acp/schema/__init__.py:161-295` — `__all__` list

  **WHY Each Reference Matters**:
  - notifications.py: Must add to the union exactly as existing members
  - messages.py: Must add to the Literal type exactly as existing method strings
  - __init__.py: Must follow existing import grouping pattern and add to __all__ alphabetically

  **Acceptance Criteria**:
  - [ ] `AgentNotification = SessionNotification | ElicitationCompleteNotification | ExtNotification`
  - [ ] `ClientMethod` literal includes `"elicitation/create"`
  - [ ] `__init__.py` exports all 5 new types
  - [ ] `from acp.schema import ElicitationCapabilities` works
  - [ ] `from acp.schema import ElicitationCreateRequest` works

  **QA Scenarios**:

  ```
  Scenario: All new types importable from acp.schema
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import ElicitationCapabilities, ElicitationCompleteNotification, ElicitationCreateRequest, ElicitationCreateResponse, URLElicitationRequiredError; print('OK')"
    Expected Result: "OK" printed with no ImportError
    Evidence: .sisyphus/evidence/task-5-imports.txt

  Scenario: ClientMethod literal includes elicitation/create
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.schema import ClientMethod; print('elicitation/create' in ClientMethod.__args__)"
    Expected Result: True
    Evidence: .sisyphus/evidence/task-5-client-method.txt
  ```

  **Commit**: YES (groups with Wave 1)

- [x] 6. Add `elicitation_create()` method to Client protocol in protocol.py

  **What to do**:
  - Add `from acp.schema import ElicitationCreateRequest, ElicitationCreateResponse` to TYPE_CHECKING imports
  - Add async method `elicitation_create(self, params: ElicitationCreateRequest) -> ElicitationCreateResponse: ...` to `Client` protocol class

  **Must NOT do**:
  - Do NOT modify existing protocol methods
  - Do NOT add default implementations (it's a Protocol)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8)
  - **Blocks**: Task 9
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `src/acp/client/protocol.py:31-33` — `request_permission` method pattern: exact signature style for Client protocol methods
  - `src/acp/client/protocol.py:7-25` — TYPE_CHECKING import pattern for ACP schema types

  **WHY Each Reference Matters**:
  - Must follow exact same signature pattern as `request_permission` for consistency
  - Must use TYPE_CHECKING imports to avoid circular deps

  **Acceptance Criteria**:
  - [ ] `Client` protocol has `elicitation_create` method with correct signature
  - [ ] Types are imported under TYPE_CHECKING guard

  **QA Scenarios**:

  ```
  Scenario: Client protocol has elicitation_create
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.client.protocol import Client; print(hasattr(Client, 'elicitation_create'))"
    Expected Result: True
    Evidence: .sisyphus/evidence/task-6-protocol-method.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `feat(acp): add elicitation protocol methods and routing`
  - Files: `src/acp/client/protocol.py`

- [x] 7. Add `elicitation_create()` convenience method to ACPRequests in acp_requests.py

  **What to do**:
  - Add `from acp.schema import ElicitationCreateRequest, ElicitationCreateResponse` to imports (use TYPE_CHECKING for response)
  - Add `elicitation_create()` method to `ACPRequests` class with params: `message: str`, `mode: Literal["form", "url"]`, `requested_schema: dict[str, Any] | None = None`, `url: str | None = None`, `elicitation_id: str | None = None`, `tool_call_id: str | None = None`, `request_id: str | None = None`
  - Method constructs `ElicitationCreateRequest(session_id=self.id, ...)` and calls `self.client.elicitation_create(request)`
  - Returns `ElicitationCreateResponse`

  **Must NOT do**:
  - Do NOT add schema conversion logic — keep this as a thin wrapper like `request_permission()`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 8)
  - **Blocks**: Task 9
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `src/acp/agent/acp_requests.py:182-209` — `request_permission()` convenience method: exact pattern for constructing request + delegating to client
  - `src/acp/agent/acp_requests.py:10-22` — Import pattern for schema types

  **WHY Each Reference Matters**:
  - `request_permission()`: Shows exact pattern — construct request with session_id, delegate to client, return response
  - Import pattern: Must follow same TYPE_CHECKING vs direct import convention

  **Acceptance Criteria**:
  - [ ] `ACPRequests.elicitation_create()` exists with all params
  - [ ] Method constructs `ElicitationCreateRequest` and calls `self.client.elicitation_create()`

  **QA Scenarios**:

  ```
  Scenario: ACPRequests.elicitation_create method exists
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.agent.acp_requests import ACPRequests; print(hasattr(ACPRequests, 'elicitation_create'))"
    Expected Result: True
    Evidence: .sisyphus/evidence/task-7-acp-requests-method.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Files: `src/acp/agent/acp_requests.py`

- [x] 8. Add routing in `_handle_client_method()` and notification handling in connection.py

  **What to do**:
  - Add `from acp.schema import ElicitationCreateRequest, ElicitationCreateResponse` to imports
  - In `_handle_client_method()`:
    - Add `ElicitationCreateResponse` to the return type union
    - Add case `case "elicitation/create":` that deserializes params as `ElicitationCreateRequest` and calls `client.elicitation_create(request)`
  - In `ClientSideConnection` class (agent-side connection that sends requests to clients):
    - No changes needed for sending — the `send_request("elicitation/create", ...)` pattern works via the existing `Connection.send_request()` method
  - For `ElicitationCompleteNotification` routing: The notification is agent→client. Since `SessionNotification` is already handled, and `ElicitationCompleteNotification` is NOT a `SessionNotification`, add handling for it in the notification sending path. Add `send_elicitation_complete()` to `ClientSideConnection` if it has direct notification methods, or ensure it's sent via `Connection.send_notification()` with the right method string.

  **Must NOT do**:
  - Do NOT modify the existing routing cases
  - Do NOT add a new notification method to `ClientMethod` — `elicitation/complete` is a notification, not a request

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7)
  - **Blocks**: Tasks 9, 10
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `src/acp/client/connection.py:209-264` — `_handle_client_method()` function: exact match/case routing pattern
  - `src/acp/client/connection.py:236-238` — `"session/request_permission"` case: deserialize → call client → return response pattern to follow exactly

  **WHY Each Reference Matters**:
  - `_handle_client_method()`: Must add new case in exact same style as existing cases
  - The request_permission case is the closest analog — same pattern of deserialize + delegate

  **Acceptance Criteria**:
  - [ ] `_handle_client_method()` has `case "elicitation/create":` that deserializes `ElicitationCreateRequest` and calls `client.elicitation_create()`
  - [ ] `ElicitationCreateResponse` is in the return type union
  - [ ] No existing routing cases are modified

  **QA Scenarios**:

  ```
  Scenario: elicitation/create routing works
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "
from acp.client.connection import _handle_client_method
from acp.schema import ElicitationCreateRequest
import asyncio
# Verify the function accepts the method string without error
print('elicitation/create' in str(_handle_client_method.__code__.co_consts))
"
    Expected Result: True (method string appears in function constants)
    Evidence: .sisyphus/evidence/task-8-routing.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Files: `src/acp/client/connection.py`

- [x] 9. Implement `elicitation_create()` in all 3 client implementations

  **What to do**:
  - **DefaultACPClient** (`default_client.py`): Add `elicitation_create()` — auto-accept form mode with empty `content={}`, decline URL mode
  - **HeadlessACPClient** (`headless_client.py`): Add `elicitation_create()` — auto-accept form mode with empty `content={}`, decline URL mode (same as default)
  - **NoOpClient** (`noop_client.py`): Add `elicitation_create()` — decline all requests (return `action="cancel"`)
  - Add TYPE_CHECKING imports for `ElicitationCreateRequest`, `ElicitationCreateResponse` in each file
  - Add `elicitation_calls: list[ElicitationCreateRequest]` tracking list to DefaultACPClient for testability (follows `ext_calls` pattern)

  **Must NOT do**:
  - Do NOT implement real user interaction — these are stub implementations
  - Do NOT modify existing methods in these clients

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (single task touching 3 related files)
  - **Parallel Group**: Wave 2 (sequential after Tasks 6, 7, 8)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 6, 7, 8

  **References**:

  **Pattern References**:
  - `src/acp/client/implementations/default_client.py:70-88` — `request_permission()` pattern: check test queue, then auto-grant first option
  - `src/acp/client/implementations/noop_client.py:46-52` — NoOp `request_permission()`: minimal implementation pattern
  - `src/acp/client/implementations/default_client.py:66-67` — `ext_calls` tracking list pattern for testability

  **WHY Each Reference Matters**:
  - Default client: Shows how to auto-grant with test queue override — replicate for elicitation
  - NoOp client: Shows minimal stub pattern — return cancel for all elicitation
  - Tracking list: Essential for tests to verify elicitation was called with correct params

  **Acceptance Criteria**:
  - [ ] `DefaultACPClient.elicitation_create()` auto-accepts form mode, declines URL mode
  - [ ] `HeadlessACPClient.elicitation_create()` auto-accepts form mode, declines URL mode
  - [ ] `NoOpClient.elicitation_create()` returns cancel for all requests
  - [ ] `DefaultACPClient` has `elicitation_calls` list for testing

  **QA Scenarios**:

  ```
  Scenario: DefaultACPClient auto-accepts form elicitation
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "
import asyncio
from acp.client.implementations.default_client import DefaultACPClient
from acp.schema import ElicitationCreateRequest, ElicitationCreateResponse
client = DefaultACPClient()
req = ElicitationCreateRequest(session_id='s1', message='Name?', mode='form', requested_schema={'type':'string'})
resp = asyncio.run(client.elicitation_create(req))
print(f'action={resp.action}, content={resp.content}')
"
    Expected Result: action=accept, content={}
    Evidence: .sisyphus/evidence/task-9-default-form.txt

  Scenario: NoOpClient declines all elicitation
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "
import asyncio
from acp.client.implementations.noop_client import NoOpClient
from acp.schema import ElicitationCreateRequest
client = NoOpClient()
req = ElicitationCreateRequest(session_id='s1', message='Name?', mode='form')
resp = asyncio.run(client.elicitation_create(req))
print(f'action={resp.action}')
"
    Expected Result: action=cancel
    Evidence: .sisyphus/evidence/task-9-noop-decline.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Files: `src/acp/client/implementations/default_client.py`, `src/acp/client/implementations/headless_client.py`, `src/acp/client/implementations/noop_client.py`

- [x] 10. Rewrite `ACPInputProvider.get_elicitation()` with capability-gated dual-path

  **What to do**:
  - Add `from acp.schema import ElicitationCreateRequest, ElicitationCreateResponse, ElicitationCapabilities` imports
  - Add private helper `_should_use_elicitation()` that checks `self.session.client_capabilities.elicitation`
  - Add private helper `_elicit_via_acp()` that:
    - For form-mode: constructs `ElicitationCreateRequest(mode="form", requested_schema=schema, message=params.message)` and calls `self.session.requests.elicitation_create()`
    - For URL-mode: constructs `ElicitationCreateRequest(mode="url", url=params.url, elicitation_id=params.elicitationId, message=params.message)` and calls `self.session.requests.elicitation_create()`
    - Maps `ElicitationCreateResponse` back to `types.ElicitResult`:
      - `action="accept"` → `ElicitResult(action="accept", content=response.content or {})`
      - `action="decline"` → `ElicitResult(action="decline")`
      - `action="cancel"` → `ElicitResult(action="cancel")`
  - Modify `get_elicitation()` to:
    1. Check `_should_use_elicitation()` — if True and appropriate mode supported, use `_elicit_via_acp()`
    2. Otherwise, fall back to existing `request_permission` hack (preserve ALL existing fallback logic exactly as-is)
  - For form-mode: pass `params.requestedSchema` as `requested_schema` directly (it's already a JSON Schema dict from MCP types)
  - For URL-mode: check `client_capabilities.elicitation.url` specifically

  **Must NOT do**:
  - Do NOT change the `get_elicitation()` method signature
  - Do NOT modify the existing fallback `request_permission` logic (lines 241-283 in current file)
  - Do NOT import or use internal `ElicitRequest`/`ElicitForm` types from `agentpool/ui/elicitation.py`
  - Do NOT fix the boolean standalone bug (line 302: always returns `content={"value": True}`)
  - Do NOT add `to_acp_schema()` methods anywhere

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core business logic with capability negotiation, dual-path routing, and response mapping. Requires understanding of both ACP and MCP type systems.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (with Task 11)
  - **Blocks**: Task 12
  - **Blocked By**: Tasks 2, 8, 9

  **References**:

  **Pattern References**:
  - `src/agentpool_server/acp_server/input_provider.py:198-287` — Current `get_elicitation()` implementation with all the permission hack logic that must be preserved as fallback
  - `src/agentpool_server/acp_server/input_provider.py:213-239` — URL-mode handling via request_permission (current hack)
  - `src/agentpool_server/acp_server/input_provider.py:241-283` — Form-mode handling via request_permission (current hack)
  - `src/agentpool_server/acp_server/session.py` line ~183 — `ACPSession` has `client_capabilities: ClientCapabilities` field
  - `src/acp/agent/acp_requests.py:182-209` — `ACPRequests.request_permission()` pattern to follow for `elicitation_create()`

  **API/Type References**:
  - `mcp.types.ElicitRequestParams` — input params type (has `.message`, `.requestedSchema`)
  - `mcp.types.ElicitRequestURLParams` — URL variant (has `.url`, `.elicitationId`)
  - `mcp.types.ElicitResult` — return type (has `.action`, `.content`)
  - `acp.schema.ElicitationCreateRequest` — new ACP request type
  - `acp.schema.ElicitationCreateResponse` — new ACP response type with three-action model

  **WHY Each Reference Matters**:
  - Current get_elicitation(): Must preserve ALL existing fallback logic exactly — it's the backward-compat path
  - ACPSession.client_capabilities: The key signal for capability-gated routing
  - MCP types: The input/output contract that cannot change
  - ACPRequests pattern: How to call elicitation_create() from the session

  **Acceptance Criteria**:
  - [ ] `_should_use_elicitation()` checks `session.client_capabilities.elicitation` correctly
  - [ ] Form-mode uses `elicitation_create()` when `elicitation.form` is True
  - [ ] URL-mode uses `elicitation_create()` when `elicitation.url` is True
  - [ ] Falls back to existing `request_permission` hack when elicitation capability is None
  - [ ] Falls back to `request_permission` for URL when only `form` is supported
  - [ ] Response mapping: accept→ElicitResult(accept), decline→ElicitResult(decline), cancel→ElicitResult(cancel)
  - [ ] `get_elicitation()` signature unchanged

  **QA Scenarios**:

  ```
  Scenario: Elicitation path when capability declared (form mode)
    Tool: Bash (uv run python)
    Preconditions: Mock session with client_capabilities.elicitation.form = True
    Steps:
      1. Create ACPInputProvider with mock session that has ElicitationCapabilities(form=True, url=True)
      2. Mock session.requests.elicitation_create to return ElicitationCreateResponse(action="accept", content={"name": "test"})
      3. Call get_elicitation with ElicitRequestParams(message="Name?", requestedSchema={"type":"string"})
      4. Assert elicitation_create was called (not request_permission)
      5. Assert result.action == "accept" and result.content == {"name": "test"}
    Expected Result: elicitation_create path used, correct response mapping
    Evidence: .sisyphus/evidence/task-10-form-capability.txt

  Scenario: Fallback path when capability absent
    Tool: Bash (uv run python)
    Preconditions: Mock session with client_capabilities.elicitation = None
    Steps:
      1. Create ACPInputProvider with mock session that has client_capabilities.elicitation = None
      2. Mock session.requests.request_permission to return appropriate response
      3. Call get_elicitation with boolean schema
      4. Assert request_permission was called (not elicitation_create)
    Expected Result: request_permission fallback path used
    Evidence: .sisyphus/evidence/task-10-fallback.txt

  Scenario: URL mode with url capability
    Tool: Bash (uv run python)
    Preconditions: Mock session with client_capabilities.elicitation.url = True
    Steps:
      1. Create ACPInputProvider with mock session
      2. Call get_elicitation with ElicitRequestURLParams
      3. Assert elicitation_create called with mode="url"
    Expected Result: URL elicitation path used
    Evidence: .sisyphus/evidence/task-10-url-capability.txt
  ```

  **Commit**: YES (groups with Wave 3)
  - Message: `feat(acp-server): capability-gated elicitation with permission fallback`
  - Files: `src/agentpool_server/acp_server/input_provider.py`

- [x] 11. Add `send_elicitation_complete()` convenience method to ACPNotifications

  **What to do**:
  - Add `from acp.schema import ElicitationCompleteNotification` import
  - Add `send_elicitation_complete()` method to `ACPNotifications` class:
    - Params: `elicitation_id: str`, `result: Literal["completed", "expired", "error"]`
    - Constructs `ElicitationCompleteNotification(session_id=self.id, elicitation_id=elicitation_id, result=result)`
    - Sends via `self.client.session_update()` — wrap in `SessionNotification` since that's the notification channel, OR send as a raw notification via `Connection.send_notification("elicitation/complete", ...)` if available
    - Actually, since `ElicitationCompleteNotification` is NOT a `SessionNotification` (different type), need to check how to send agent→client notifications. The `Connection.send_notification()` method with the method string should work.

  **Must NOT do**:
  - Do NOT add `ElicitationCompleteNotification` to `SessionUpdate` — it's a standalone notification

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 10)
  - **Blocks**: Task 12
  - **Blocked By**: Task 5

  **References**:

  **Pattern References**:
  - `src/acp/agent/notifications.py:60-76` — `ACPNotifications.__init__()` and `send_update()` pattern
  - `src/acp/agent/notifications.py:168-170` — `send_update()` wraps update in SessionNotification and calls `client.session_update()`
  - `src/acp/connection.py` — `Connection.send_notification()` for raw notifications

  **WHY Each Reference Matters**:
  - ACPNotifications: Shows existing notification sending pattern
  - Need to determine whether ElicitationCompleteNotification goes through session_update or a separate notification channel

  **Acceptance Criteria**:
  - [ ] `ACPNotifications.send_elicitation_complete()` exists with correct signature
  - [ ] Notification is sent to the client

  **QA Scenarios**:

  ```
  Scenario: send_elicitation_complete method exists
    Tool: Bash (uv run python)
    Steps:
      1. Run: uv run python -c "from acp.agent.notifications import ACPNotifications; print(hasattr(ACPNotifications, 'send_elicitation_complete'))"
    Expected Result: True
    Evidence: .sisyphus/evidence/task-11-notification-method.txt
  ```

  **Commit**: YES (groups with Wave 3)
  - Files: `src/acp/agent/notifications.py`

- [x] 12. Run mypy + ruff + pytest verification

  **What to do**:
  - Run `uv run mypy src/acp/ src/agentpool_server/acp_server/` — must have zero type errors
  - Run `uv run ruff check src/acp/ src/agentpool_server/acp_server/` — must have zero lint errors
  - Run `uv run ruff format --check src/acp/ src/agentpool_server/acp_server/` — formatting must be clean
  - Run `uv run pytest tests/ -k "acp"` — all existing ACP tests must pass
  - If any errors, fix them and re-run

  **Must NOT do**:
  - Do NOT add `# type: ignore` comments to suppress errors
  - Do NOT add `# noqa` for legitimate issues
  - Do NOT modify test assertions to make tests pass

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 10, 11

  **References**:
  - `AGENTS.md` — Development commands section

  **Acceptance Criteria**:
  - [ ] mypy: 0 errors
  - [ ] ruff check: 0 errors
  - [ ] ruff format: clean
  - [ ] pytest: all existing tests pass

  **QA Scenarios**:

  ```
  Scenario: Type checking passes
    Tool: Bash
    Steps:
      1. Run: uv run mypy src/acp/ src/agentpool_server/acp_server/
    Expected Result: "Success: no issues found" or 0 errors
    Evidence: .sisyphus/evidence/task-12-mypy.txt

  Scenario: Lint check passes
    Tool: Bash
    Steps:
      1. Run: uv run ruff check src/acp/ src/agentpool_server/acp_server/
    Expected Result: 0 errors
    Evidence: .sisyphus/evidence/task-12-ruff.txt

  Scenario: Existing tests pass
    Tool: Bash
    Steps:
      1. Run: uv run pytest tests/ -k "acp" --no-header -q
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-12-pytest.txt
  ```

  **Commit**: YES (separate)
  - Message: `test(acp): verify elicitation types pass linting and type checking`
  - Pre-commit: `uv run mypy src/acp/ && uv run ruff check src/acp/`

---

## Accepted Plan Divergences (ACP Spec Alignment)

The following divergences from the original plan were accepted during implementation because they align with the actual ACP RFD specification, which supersedes the plan's initial assumptions:

| Plan Said | Implementation | Reason |
|-----------|---------------|--------|
| `ElicitationCapabilities(form: bool, url: bool)` | `ElicitationCapabilities(create: bool \| None = False)` | ACP RFD spec uses single `create` boolean, not `form`/`url` split |
| `ElicitationCreateRequest(mode: Literal["form", "url"])` | No `mode` field; URL mode determined by `url` field presence | ACP RFD uses `requested_schema` (JSON Schema) for form definition, `url` field for URL mode |
| `ElicitationCreateRequest(elicitation_id, tool_call_id, request_id)` | These fields omitted | Not part of ACP RFD spec for `elicitation/create` |
| `ElicitationCompleteNotification(elicitation_id, result)` | `ElicitationCompleteNotification(action, content)` | Follows ACP response model pattern (same 3-action model as ElicitationCreateResponse) |
| `ElicitationCompleteNotification` in `AgentNotification` union | Added to `ClientNotification` union | Correct ACP direction: this is a **client→agent** notification (client signals completion), not agent→client |
| `URLElicitationRequiredError(code: int = -32042)` | No `code` field | Error code is conveyed via JSON-RPC error code, not a model field |

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, import check). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run mypy src/acp/ src/agentpool_server/acp_server/` + `uv run ruff check src/acp/ src/agentpool_server/acp_server/`. Review all changed files for: `as any`/type: ignore, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Mypy [PASS/FAIL] | Ruff [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  From clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (elicitation types + routing + server dual-path working together). Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `feat(acp): add elicitation schema types` - src/acp/schema/elicitation.py, capabilities.py, agent_requests.py, client_responses.py, notifications.py, messages.py, __init__.py
- **Wave 2**: `feat(acp): add elicitation protocol methods and routing` - protocol.py, acp_requests.py, connection.py, client implementations
- **Wave 3**: `feat(acp-server): capability-gated elicitation with permission fallback` - input_provider.py, notifications.py
- **Wave 4**: `test(acp): add elicitation tests` - tests/

---

## Success Criteria

### Verification Commands
```bash
uv run mypy src/acp/ src/agentpool_server/acp_server/  # Expected: 0 errors
uv run ruff check src/acp/ src/agentpool_server/acp_server/  # Expected: 0 errors
uv run pytest tests/ -k "acp"  # Expected: all pass
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] No files changed outside `src/acp/` and `src/agentpool_server/acp_server/`
