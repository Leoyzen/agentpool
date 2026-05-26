# ACP MCP Resource Notification Passthrough Plan

## TL;DR

> **Quick Summary**: Implement ACP-server passthrough for MCP tool/prompt/resource notifications by extending the existing MCP callback/signal pipeline and bridging provider signals from `ACPSession` to ACP `ExtNotification`s.
>
> **Deliverables**:
> - MCP `ResourceUpdatedNotification` URI propagation through `MCPMessageHandler`, `MCPClient`, and `MCPResourceProvider`.
> - `ACPSession` lifecycle bridge from MCP provider signals to ACP extension notifications.
> - Cleanup/disconnect logic for the new MCP notification bridge.
> - Unit/integration tests proving list-change, URI update, disabled-server, and cleanup behavior.
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 implementation waves + final verification
> **Critical Path**: Task 1 → Task 3 → Task 5 → Task 7 → Final Verification

---

## Context

### Original Request

The original investigation was about AgentPool ACP server behavior: how to passthrough MCP resource notifications and changes when AgentPool is serving ACP sessions.

### Research Findings

- `src/agentpool/mcp_server/message_handler.py:22-33` already carries tool/prompt/resource list-change callbacks.
- `src/agentpool/mcp_server/message_handler.py:60-79` dispatches MCP server notifications, including `ResourceUpdatedNotification`.
- `src/agentpool/mcp_server/message_handler.py:100-119` forwards tool/resource/prompt list changes but only logs resource content updates.
- `src/agentpool/mcp_server/client.py:69-98` stores MCP notification callbacks on the client.
- `src/agentpool/mcp_server/client.py:203-209` constructs `MCPMessageHandler` with the existing three callbacks.
- `src/agentpool/resource_providers/mcp_provider.py:67-74` wires MCP client callbacks to provider cache invalidation methods.
- `src/agentpool/resource_providers/mcp_provider.py:132-152` invalidates provider caches and emits `tools_changed`, `prompts_changed`, and `resources_changed` signals.
- `src/agentpool/resource_providers/base.py:40-54` defines `ResourceChangeEvent`; `src/agentpool/resource_providers/base.py:79-83` defines provider change signals.
- `src/acp/schema/notifications.py:50-66` defines `ExtNotification(method, params)` for extension notifications, and extension methods should be underscore-prefixed.
- `src/acp/agent/protocol.py:73-75` exposes `ext_method` and `ext_notification` on the ACP client protocol.
- `src/agentpool_server/acp_server/session.py:198-244` already wires agent `state_updated` signals into the session lifecycle.
- `src/agentpool_server/acp_server/session.py:246-289` forwards agent state changes to ACP session notifications; use this as the implementation style reference.
- `src/agentpool_server/acp_server/session.py:295-313` initializes ACP-supplied MCP servers but does not capture returned providers or connect signals.
- `src/agentpool_server/acp_server/session.py:483-497` closes the ACP session but currently does not include MCP notification bridge cleanup.
- `src/agentpool/mcp_server/manager.py:111-145` confirms `setup_server()` returns `MCPResourceProvider | None`, with `None` for disabled configs.
- Existing tests to follow: `tests/servers/acp_server/test_mcp_integration.py:29-45` for ACP MCP config conversion and `tests/servers/acp_server/test_acp_integration.py:40-77` for direct `ACPSession` testing with mocked clients.

### Metis Review

**Identified Gaps (addressed by defaults in this plan)**:
- URI-specific `ResourceUpdatedNotification` must not be conflated with resource list changes.
- Notifications need provenance for multiple MCP servers.
- Disabled MCP servers can return `None` from `setup_server()` and must be skipped.
- Signal handlers must not crash sessions when ACP clients disconnect or session closes.
- Existing non-MCP signal cleanup is a broader issue and must not expand scope.

---

## Work Objectives

### Core Objective

When an MCP server attached to an ACP session reports tool, prompt, resource-list, or resource-content changes, AgentPool should forward a corresponding ACP extension notification to the ACP client without coupling MCP internals to ACP.

### Concrete Deliverables

- Add a URI-bearing resource update event/signal path in resource providers.
- Add a URI-bearing optional callback path in MCP message handling/client code.
- Connect MCP provider signals inside `ACPSession.initialize_mcp_servers()` and send ACP extension notifications.
- Disconnect only the new MCP bridge signal handlers in `ACPSession.close()`.
- Add tests under ACP/MCP server test areas to verify notification flow and cleanup.

### Definition of Done

- [ ] `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py` passes.
- [ ] `uv run pytest tests/servers/acp_server/test_mcp_integration.py tests/servers/acp_server/test_acp_integration.py` passes.
- [ ] `uv run ruff check src/agentpool/mcp_server src/agentpool/resource_providers src/agentpool_server/acp_server tests/servers/acp_server` passes.
- [ ] `uv run --no-group docs mypy src/agentpool/mcp_server/message_handler.py src/agentpool/mcp_server/client.py src/agentpool/resource_providers/base.py src/agentpool/resource_providers/mcp_provider.py src/agentpool_server/acp_server/session.py` passes.

### Must Have

- ACP notifications use underscore-prefixed extension methods.
- MCP/provider layers remain ACP-agnostic.
- Resource list changes and resource content updates remain semantically separate.
- Multiple MCP servers include origin/provider information in notification params.
- Disabled MCP servers do not crash initialization.
- Bridge handlers are disconnected during session close.

### Must NOT Have (Guardrails)

- Do not modify ACP core schema to add first-class MCP notifications.
- Do not use `SessionNotification` for MCP resource/tool/prompt change notifications.
- Do not change existing callback signatures for `tool_change_callback`, `prompt_change_callback`, or `resource_change_callback`.
- Do not couple `MCPMessageHandler`, `MCPClient`, or `MCPResourceProvider` directly to ACP classes.
- Do not implement client capability negotiation, debouncing, resume support, reconnection replay, or agent-switch MCP rebinding in this iteration.
- Do not fix pre-existing non-MCP `state_updated` signal cleanup unless required by tests for the new bridge.
- Do not use `getattr`/`hasattr`; preserve project type-safety rules.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No acceptance criteria may require manual IDE/Zed confirmation.

### Test Decision

- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest via `uv run pytest`
- **Agent-Executed QA**: Mandatory for every task.

### QA Policy

Every task must capture evidence in `.sisyphus/evidence/` using exact commands/output logs. For this backend/protocol work, QA is primarily `uv run pytest`, `uv run ruff check`, and `uv run --no-group docs mypy`.

---

## Execution Strategy

### Parallel Execution Waves

```text
Wave 1 (Foundation, can start immediately):
├── Task 1: Add typed resource-update event/signal [quick]
├── Task 2: Add MCP handler/client URI callback path [quick]
└── Task 3: Add notification bridge test scaffolding [quick]

Wave 2 (Core wiring):
├── Task 4: Wire MCPResourceProvider resource updates [quick] (depends: 1, 2)
├── Task 5: Bridge provider signals in ACPSession [unspecified-high] (depends: 1, 3)
└── Task 6: Add error/close-safe bridge behavior [unspecified-high] (depends: 5)

Wave 3 (Tests and integration hardening):
├── Task 7: Add notification flow tests [unspecified-high] (depends: 4, 5)
├── Task 8: Add cleanup/disabled-server tests [quick] (depends: 5, 6)
└── Task 9: Run focused validation and fix scoped failures [unspecified-high] (depends: 7, 8)

Wave FINAL (After ALL tasks — 4 parallel reviews, then user okay):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real QA execution (unspecified-high)
└── F4: Scope fidelity check (deep)
```

### Dependency Matrix

- **1**: blocks 4, 5, 7; blocked by none.
- **2**: blocks 4, 7; blocked by none.
- **3**: blocks 5, 7, 8; blocked by none.
- **4**: blocks 7; blocked by 1, 2.
- **5**: blocks 6, 7, 8, 9; blocked by 1, 3.
- **6**: blocks 8, 9; blocked by 5.
- **7**: blocks 9; blocked by 4, 5.
- **8**: blocks 9; blocked by 5, 6.
- **9**: blocks final verification; blocked by 7, 8.

### Agent Dispatch Summary

- **Wave 1**: 3 tasks — T1/T2/T3 → `quick`.
- **Wave 2**: 3 tasks — T4 → `quick`, T5/T6 → `unspecified-high`.
- **Wave 3**: 3 tasks — T7/T9 → `unspecified-high`, T8 → `quick`.
- **FINAL**: 4 review tasks — F1 → `oracle`, F2/F3 → `unspecified-high`, F4 → `deep`.

---

## TODOs

> Implementation + tests for a concern are kept together where feasible. Every task includes agent-executable QA.

- [x] 1. Add typed resource-update event/signal

  **What to do**:
  - In `src/agentpool/resource_providers/base.py`, add a frozen/slotted `ResourceUpdatedEvent` dataclass with `provider_name`, `provider_kind`, `uri`, and optional `owner`.
  - Add `resource_updated: Signal[ResourceUpdatedEvent]` to `ResourceProvider`.
  - Add `create_resource_updated_event(uri: str) -> ResourceUpdatedEvent` helper.
  - Keep `ResourceChangeEvent` unchanged for list changes.

  **Must NOT do**:
  - Do not add optional `uri` to `ResourceChangeEvent`.
  - Do not emit ACP notifications here.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused type/model addition in one base module.
  - **Skills**: []
  - **Skills Evaluated but Omitted**: `systematic-debugging` not needed unless tests fail.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 with Tasks 2 and 3
  - **Blocks**: 4, 5, 7
  - **Blocked By**: None

  **References**:
  - `src/agentpool/resource_providers/base.py:40-54` - Existing `ResourceChangeEvent` pattern.
  - `src/agentpool/resource_providers/base.py:79-83` - Existing `Signal[...]` declarations.
  - `tests/integration/test_skill_providers.py:334-379` - Existing signal event typing/handler testing style.
  - `tests/resource_providers/test_aggregating_skills.py:280-307` - Signal forwarding/disconnect mock patterns.

  **Acceptance Criteria**:
  - [ ] New event type is strongly typed and importable.
  - [ ] Existing `ResourceChangeEvent` constructor usage remains compatible.
  - [ ] No new mypy errors in `src/agentpool/resource_providers/base.py`.

  **QA Scenarios**:

  ```text
  Scenario: Resource update event helper creates typed event
    Tool: Bash
    Preconditions: Task 1 implementation complete
    Steps:
      1. Run `uv run python - <<'PY'` importing `ResourceProvider` and constructing a provider named `qa_provider`.
      2. Call `create_resource_updated_event("file:///tmp/example.txt")`.
      3. Assert printed fields equal provider_name=`qa_provider`, resource URI=`file:///tmp/example.txt`, provider_kind=`base`.
    Expected Result: Command exits 0 and prints exact expected fields.
    Failure Indicators: ImportError, missing helper, wrong field values, or non-zero exit.
    Evidence: .sisyphus/evidence/task-1-resource-update-event.txt

  Scenario: Existing list-change event remains unchanged
    Tool: Bash
    Preconditions: Task 1 implementation complete
    Steps:
      1. Run `uv run python - <<'PY'` importing `ResourceChangeEvent` and instantiating it with provider_name/provider_kind/resource_type/owner only.
      2. Assert no `uri` argument is required.
    Expected Result: Command exits 0.
    Failure Indicators: Type/signature incompatibility or runtime constructor failure.
    Evidence: .sisyphus/evidence/task-1-backcompat.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-1-resource-update-event.txt`
  - [ ] `.sisyphus/evidence/task-1-backcompat.txt`

  **Commit**: NO (group with Tasks 2 and 4)

- [x] 2. Add MCP handler/client URI callback path

  **What to do**:
  - In `src/agentpool/mcp_server/message_handler.py`, add optional `resource_updated_callback: Callable[[str], Awaitable[None]] | None`.
  - In `on_resource_updated`, read the typed `message.uri` field directly and call the callback with a string URI.
  - In `src/agentpool/mcp_server/client.py`, add optional `resource_updated_callback` to `MCPClient.__init__`, store it, and pass it to `MCPMessageHandler` in `_get_client()`.
  - Preserve existing callback positional order as much as practical; prefer keyword construction if needed to avoid future order mistakes.

  **Must NOT do**:
  - Do not change existing three callback signatures.
  - Do not use `getattr`/`hasattr` fallback logic.
  - Do not add ACP imports.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Narrow callback plumbing across two files.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 with Tasks 1 and 3
  - **Blocks**: 4, 7
  - **Blocked By**: None

  **References**:
  - `src/agentpool/mcp_server/message_handler.py:22-33` - Existing callback fields.
  - `src/agentpool/mcp_server/message_handler.py:76-119` - Current resource update dispatch/logging.
  - `src/agentpool/mcp_server/client.py:69-98` - Existing callback constructor fields.
  - `src/agentpool/mcp_server/client.py:203-209` - Handler construction site.

  **Acceptance Criteria**:
  - [ ] `ResourceUpdatedNotification` invokes the URI callback exactly once.
  - [ ] Tool/prompt/resource list-change callbacks still behave as before.
  - [ ] No ACP dependency is introduced under `src/agentpool/mcp_server/`.

  **QA Scenarios**:

  ```text
  Scenario: ResourceUpdatedNotification invokes URI callback
    Tool: Bash
    Preconditions: Task 2 implementation complete
    Steps:
      1. Run a focused pytest test or inline async script that constructs `MCPMessageHandler` with `resource_updated_callback` appending URIs to a list.
      2. Pass a constructed MCP `ResourceUpdatedNotification` with URI `file:///tmp/changed.md` to `on_resource_updated`.
      3. Assert callback list equals `["file:///tmp/changed.md"]`.
    Expected Result: Assertion passes and command exits 0.
    Failure Indicators: Callback not invoked, wrong URI, or duplicate invocation.
    Evidence: .sisyphus/evidence/task-2-resource-updated-callback.txt

  Scenario: Existing list-change callback still works
    Tool: Bash
    Preconditions: Task 2 implementation complete
    Steps:
      1. Run a focused test/script invoking `on_resource_list_changed` with `resource_change_callback` incrementing a counter.
      2. Assert counter equals 1 and URI callback counter equals 0.
    Expected Result: List-change and update semantics remain separate.
    Failure Indicators: URI callback fires for list changes or list callback no longer fires.
    Evidence: .sisyphus/evidence/task-2-list-change-backcompat.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-2-resource-updated-callback.txt`
  - [ ] `.sisyphus/evidence/task-2-list-change-backcompat.txt`

  **Commit**: NO (group with Tasks 1 and 4)

- [x] 3. Add notification bridge test scaffolding

  **What to do**:
  - Create `tests/servers/acp_server/test_mcp_notification_bridge.py`.
  - Add reusable fixtures/helpers for:
    - an `AgentPool` with a callback `Agent`, following `tests/servers/acp_server/test_acp_integration.py:40-65`;
    - an `AsyncMock` ACP client;
    - an `ACPSession` configured with temporary cwd and mocked `acp_agent`;
    - a small fake provider or mock signal-bearing provider if direct `MCPResourceProvider` construction is too heavy.
  - Keep scaffolding tests initially small and executable.

  **Must NOT do**:
  - Do not spawn real external MCP subprocesses for unit bridge tests.
  - Do not skip tests on macOS unless subprocess behavior is unavoidable.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Test helper setup with existing patterns.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 with Tasks 1 and 2
  - **Blocks**: 5, 7, 8
  - **Blocked By**: None

  **References**:
  - `tests/servers/acp_server/test_acp_integration.py:40-77` - Direct `ACPSession` setup and `AsyncMock` client pattern.
  - `tests/servers/acp_server/test_mcp_integration.py:29-45` - MCP/ACP conversion test style.
  - `tests/servers/acp_server/test_mcp_integration.py:47-99` - Existing MCP session creation references.

  **Acceptance Criteria**:
  - [ ] New test file imports cleanly.
  - [ ] At least one scaffold sanity test passes before core bridge assertions are added.

  **QA Scenarios**:

  ```text
  Scenario: Bridge test scaffold imports and runs
    Tool: Bash
    Preconditions: Task 3 implementation complete
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q`.
      2. Confirm pytest collects and runs scaffold tests.
    Expected Result: Exit 0 with at least one passing test.
    Failure Indicators: Import errors, fixture errors, collection failures.
    Evidence: .sisyphus/evidence/task-3-scaffold-pytest.txt

  Scenario: Scaffold rejects accidental subprocess dependency
    Tool: Bash
    Preconditions: Task 3 implementation complete
    Steps:
      1. Search the new test file for external commands like `npx`, `uvx`, `test-mcp-server`, or platform skip markers.
      2. Assert none are required by scaffold helpers.
    Expected Result: Search confirms scaffold is pure unit-test style.
    Failure Indicators: Test scaffold requires external MCP process startup.
    Evidence: .sisyphus/evidence/task-3-no-subprocess.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-3-scaffold-pytest.txt`
  - [ ] `.sisyphus/evidence/task-3-no-subprocess.txt`

  **Commit**: NO (group with Tasks 7 and 8)

- [ ] 4. Wire MCPResourceProvider resource updates

  **What to do**:
  - In `src/agentpool/resource_providers/mcp_provider.py`, pass the new `resource_updated_callback` into `MCPClient`.
  - Add `_on_resource_updated(uri: str) -> None` that does not invalidate the resource list cache unless there is a clear content cache to invalidate.
  - Emit `self.resource_updated.emit(self.create_resource_updated_event(uri))`.
  - Log provider name and URI.

  **Must NOT do**:
  - Do not clear `_resources_cache` for content-only updates unless implementation discovers cached content requiring invalidation.
  - Do not emit `resources_changed` for content updates.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: One provider file plus tests/scripts.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: 7
  - **Blocked By**: 1, 2

  **References**:
  - `src/agentpool/resource_providers/mcp_provider.py:67-74` - Existing MCP client callback wiring.
  - `src/agentpool/resource_providers/mcp_provider.py:132-152` - Existing list-change cache invalidation and signal emission.
  - `src/agentpool/resource_providers/mcp_provider.py:211-238` - Resource cache semantics.

  **Acceptance Criteria**:
  - [ ] `_on_resource_updated("file:///x")` emits a `ResourceUpdatedEvent` with the same URI.
  - [ ] Resource list cache invalidation remains controlled by `_on_resources_changed` only.

  **QA Scenarios**:

  ```text
  Scenario: Provider emits resource_updated event with URI
    Tool: Bash
    Preconditions: Tasks 1, 2, and 4 complete
    Steps:
      1. Run focused pytest or inline async script creating `MCPResourceProvider` with a dummy config.
      2. Connect a handler to `provider.resource_updated` collecting events.
      3. Call `await provider._on_resource_updated("file:///tmp/resource.md")`.
      4. Assert one event with uri `file:///tmp/resource.md` and provider_name equal to provider name.
    Expected Result: Event emitted exactly once with correct URI.
    Failure Indicators: Missing signal, wrong event type, wrong URI, duplicate events.
    Evidence: .sisyphus/evidence/task-4-provider-resource-updated.txt

  Scenario: Resource list cache not invalidated by content update
    Tool: Bash
    Preconditions: Task 4 implementation complete
    Steps:
      1. Set provider `_resources_cache` to a sentinel list in a focused unit test.
      2. Call `_on_resource_updated("file:///tmp/resource.md")`.
      3. Assert `_resources_cache` remains the same sentinel object/value.
    Expected Result: Content update does not behave like list change.
    Failure Indicators: `_resources_cache` becomes `None` or changes unexpectedly.
    Evidence: .sisyphus/evidence/task-4-cache-semantics.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-4-provider-resource-updated.txt`
  - [ ] `.sisyphus/evidence/task-4-cache-semantics.txt`

  **Commit**: YES
  - Message: `feat(mcp): expose resource update notifications`
  - Files: `src/agentpool/resource_providers/base.py`, `src/agentpool/mcp_server/message_handler.py`, `src/agentpool/mcp_server/client.py`, `src/agentpool/resource_providers/mcp_provider.py`
  - Pre-commit: `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q`

- [ ] 5. Bridge provider signals in ACPSession

  **What to do**:
  - In `src/agentpool_server/acp_server/session.py`, capture `provider = await self.agent.mcp.setup_server(cfg)` in `initialize_mcp_servers()`.
  - If `provider is None`, skip signal wiring and continue.
  - Track connected providers in a session-owned collection for later cleanup.
  - Connect provider signals to async session handlers:
    - `tools_changed` → `_mcp/tools/listChanged`
    - `prompts_changed` → `_mcp/prompts/listChanged`
    - `resources_changed` → `_mcp/resources/listChanged`
    - `resource_updated` → `_mcp/resources/updated`
  - Include params with at least `provider_name`, `provider_kind`, `owner`; include `uri` for resource updates.
  - Use direct `await self.client.ext_notification(method, params)` or add a local helper; do not change ACP schema.

  **Must NOT do**:
  - Do not send `skills_changed` ACP notifications.
  - Do not rebind MCP bridges on `switch_active_agent()`.
  - Do not persist MCP bridge state for session resume.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Lifecycle-sensitive session code with multiple signal connections and cleanup implications.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: 6, 7, 8, 9
  - **Blocked By**: 1, 3

  **References**:
  - `src/agentpool_server/acp_server/session.py:198-244` - Session initialization and signal subscription style.
  - `src/agentpool_server/acp_server/session.py:246-289` - Existing session update notification bridge.
  - `src/agentpool_server/acp_server/session.py:295-313` - MCP server initialization loop to modify.
  - `src/agentpool/mcp_server/manager.py:111-145` - `setup_server()` return value and disabled config behavior.
  - `src/acp/schema/notifications.py:50-66` - ACP `ExtNotification` method/params contract.
  - `src/acp/agent/protocol.py:73-75` - ACP client `ext_notification` protocol method.

  **Acceptance Criteria**:
  - [ ] Each provider signal produces exactly one ACP `ext_notification` call with the expected method name.
  - [ ] Notification params identify provider origin.
  - [ ] Disabled MCP providers are skipped without errors.
  - [ ] No ACP imports are added to MCP/provider modules.

  **QA Scenarios**:

  ```text
  Scenario: Provider list-change signals emit ACP ext notifications
    Tool: Bash
    Preconditions: Task 5 implementation complete
    Steps:
      1. Run focused pytest creating an `ACPSession` with `AsyncMock` client and a fake provider wired through the session bridge.
      2. Emit tools, prompts, and resources list-change events.
      3. Assert `client.ext_notification` received `_mcp/tools/listChanged`, `_mcp/prompts/listChanged`, and `_mcp/resources/listChanged` with provider_name in params.
    Expected Result: Three notifications, exact method names, params include origin.
    Failure Indicators: Missing method, wrong prefix, no provider metadata, duplicate calls.
    Evidence: .sisyphus/evidence/task-5-list-change-ext-notifications.txt

  Scenario: Disabled MCP server setup is skipped safely
    Tool: Bash
    Preconditions: Task 5 implementation complete
    Steps:
      1. Run focused pytest where mocked `self.agent.mcp.setup_server` returns `None`.
      2. Call `await session.initialize_mcp_servers()`.
      3. Assert no exception and no signal bridge is tracked for that server.
    Expected Result: Initialization exits normally.
    Failure Indicators: AttributeError on `None`, failed initialization, or bogus tracked provider.
    Evidence: .sisyphus/evidence/task-5-disabled-server.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-5-list-change-ext-notifications.txt`
  - [ ] `.sisyphus/evidence/task-5-disabled-server.txt`

  **Commit**: NO (group with Task 6)

- [ ] 6. Add error/close-safe bridge behavior

  **What to do**:
  - Add a small internal helper in `ACPSession` for sending MCP ACP extension notifications safely.
  - If `self._cancelled` is true or the session is closing/closed, skip notification send.
  - Wrap `client.ext_notification` in `try/except Exception` and log failures without raising through signal emitters.
  - In `close()`, disconnect all new MCP provider signal handlers that were connected by this feature.
  - Track enough handler/provider references to call `disconnect()` on each signal exactly once.

  **Must NOT do**:
  - Do not restructure all session cleanup.
  - Do not attempt to disconnect the existing `agent.state_updated` handlers unless necessary to avoid new bridge test failures.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Race-sensitive lifecycle and exception containment.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: 8, 9
  - **Blocked By**: 5

  **References**:
  - `src/agentpool_server/acp_server/session.py:370-379` - Cancellation flag semantics.
  - `src/agentpool_server/acp_server/session.py:474-481` - Existing safe error notification helper style.
  - `src/agentpool_server/acp_server/session.py:483-497` - Current close lifecycle.
  - `src/agentpool/resource_providers/aggregating.py` - Existing connect/disconnect pattern for provider signals; inspect exact current line numbers before editing.

  **Acceptance Criteria**:
  - [ ] Signal handler exceptions do not propagate to provider signal emitters.
  - [ ] After `ACPSession.close()`, MCP provider signal emits do not call ACP client.
  - [ ] Cleanup is idempotent enough that close error handling remains safe.

  **QA Scenarios**:

  ```text
  Scenario: ACP client notification failure is logged and swallowed
    Tool: Bash
    Preconditions: Task 6 implementation complete
    Steps:
      1. Configure `client.ext_notification` AsyncMock to raise `RuntimeError("disconnected")`.
      2. Emit a provider signal through the session bridge.
      3. Assert the emit call returns without raising.
    Expected Result: Test exits 0 and logs failure.
    Failure Indicators: RuntimeError propagates from signal handler.
    Evidence: .sisyphus/evidence/task-6-client-failure-swallowed.txt

  Scenario: Close disconnects MCP bridge handlers
    Tool: Bash
    Preconditions: Task 6 implementation complete
    Steps:
      1. Create session and connect fake provider signals.
      2. Call `await session.close()`.
      3. Emit provider signals after close.
      4. Assert `client.ext_notification` call count does not increase after close.
    Expected Result: No post-close ACP notifications.
    Failure Indicators: Calls continue after close or close raises due to disconnect.
    Evidence: .sisyphus/evidence/task-6-close-disconnects.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-6-client-failure-swallowed.txt`
  - [ ] `.sisyphus/evidence/task-6-close-disconnects.txt`

  **Commit**: YES
  - Message: `feat(acp): bridge mcp change notifications`
  - Files: `src/agentpool_server/acp_server/session.py`
  - Pre-commit: `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q`

- [ ] 7. Add notification flow tests

  **What to do**:
  - Expand `tests/servers/acp_server/test_mcp_notification_bridge.py` with tests for all four ACP ext notification methods.
  - Verify exact method names:
    - `_mcp/tools/listChanged`
    - `_mcp/prompts/listChanged`
    - `_mcp/resources/listChanged`
    - `_mcp/resources/updated`
  - Verify resource update params include `uri: "file:///tmp/changed.md"`.
  - Verify list-change params include provider identity.

  **Must NOT do**:
  - Do not assert broad/vague “called” behavior only; assert exact method and params.
  - Do not require real Zed/IDE client behavior.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Test design spans session, provider signals, and ACP client mock contract.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 9
  - **Blocked By**: 4, 5

  **References**:
  - `tests/servers/acp_server/test_mcp_notification_bridge.py` - New test scaffold from Task 3.
  - `src/acp/schema/notifications.py:50-66` - Ext notification params semantics.
  - `src/agentpool/resource_providers/base.py:40-54` and new resource update event type - Provider origin fields.

  **Acceptance Criteria**:
  - [ ] Test file covers all four methods with exact expected params.
  - [ ] Resource update URI is verified end-to-end from provider signal to ACP client mock.

  **QA Scenarios**:

  ```text
  Scenario: All MCP change methods are forwarded exactly
    Tool: Bash
    Preconditions: Tasks 4, 5, and 7 complete
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q`.
      2. Inspect pytest output for tests covering tools, prompts, resources list, and resource update.
    Expected Result: All tests pass.
    Failure Indicators: Missing method coverage, wrong method names, wrong params.
    Evidence: .sisyphus/evidence/task-7-notification-flow-pytest.txt

  Scenario: Resource update URI is not lost
    Tool: Bash
    Preconditions: Task 7 implementation complete
    Steps:
      1. Run only the resource update test with `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -k resource_updated -q`.
      2. Assert method `_mcp/resources/updated` and params uri `file:///tmp/changed.md`.
    Expected Result: Test passes with exact URI assertion.
    Failure Indicators: URI missing, changed, or attached to list-change method.
    Evidence: .sisyphus/evidence/task-7-resource-uri-pytest.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-7-notification-flow-pytest.txt`
  - [ ] `.sisyphus/evidence/task-7-resource-uri-pytest.txt`

  **Commit**: NO (group with Task 8)

- [ ] 8. Add cleanup and disabled-server tests

  **What to do**:
  - Add tests proving session close disconnects MCP provider signal handlers.
  - Add tests proving `setup_server()` returning `None` is ignored safely.
  - Add tests proving `client.ext_notification` exceptions are swallowed/logged.
  - Add a negative test proving `skills_changed` is not bridged.

  **Must NOT do**:
  - Do not verify by inspecting private handler internals only; verify observable client calls before/after close.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused negative/cleanup test expansion after bridge exists.
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3
  - **Blocks**: 9
  - **Blocked By**: 5, 6

  **References**:
  - `src/agentpool_server/acp_server/session.py:483-497` - Close lifecycle under test.
  - `src/agentpool/mcp_server/manager.py:127-130` - Disabled-server `None` return.
  - `tests/resource_providers/test_aggregating_skills.py:299-307` - Disconnect mocking style.

  **Acceptance Criteria**:
  - [ ] Post-close provider signal emits do not call ACP client.
  - [ ] Disabled MCP server setup creates no bridge and no exception.
  - [ ] `skills_changed` emits no ACP extension notification.

  **QA Scenarios**:

  ```text
  Scenario: Cleanup and negative tests pass
    Tool: Bash
    Preconditions: Task 8 implementation complete
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -k 'close or disabled or skills or failure' -q`.
      2. Confirm all selected tests pass.
    Expected Result: Exit 0 with cleanup/disabled/negative coverage passing.
    Failure Indicators: Post-close notification, disabled server crash, skills bridged, or client exception propagates.
    Evidence: .sisyphus/evidence/task-8-cleanup-disabled-pytest.txt

  Scenario: Existing ACP/MCP integration tests remain green
    Tool: Bash
    Preconditions: Task 8 implementation complete
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/test_mcp_integration.py tests/servers/acp_server/test_acp_integration.py -q`.
      2. Confirm no regressions in existing session setup patterns.
    Expected Result: Exit 0 or existing platform skips only.
    Failure Indicators: New failures in existing ACP/MCP tests.
    Evidence: .sisyphus/evidence/task-8-existing-tests.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-8-cleanup-disabled-pytest.txt`
  - [ ] `.sisyphus/evidence/task-8-existing-tests.txt`

  **Commit**: YES
  - Message: `test(acp): cover mcp notification bridge`
  - Files: `tests/servers/acp_server/test_mcp_notification_bridge.py`
  - Pre-commit: `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q`

- [ ] 9. Run focused validation and fix scoped failures

  **What to do**:
  - Run the focused validation commands from Definition of Done.
  - Fix only failures caused by this feature.
  - If broader repository failures appear, document them with exact command output and do not expand implementation scope without user approval.

  **Must NOT do**:
  - Do not run unrelated large refactors.
  - Do not silence type/lint errors with `type: ignore`, `# noqa`, `as Any`, or `cast` unless there is no strongly typed alternative and rationale is documented in code review notes.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Requires interpreting test/lint/type failures and keeping fixes scoped.
  - **Skills**: [`systematic-debugging`]
    - `systematic-debugging`: Use only if a validation failure requires root-cause analysis.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 final task
  - **Blocks**: Final Verification
  - **Blocked By**: 7, 8

  **References**:
  - `AGENTS.md` project commands: use `uv run pytest`, `uv run ruff check`, and `uv run --no-group docs mypy`.
  - All files changed by Tasks 1-8.

  **Acceptance Criteria**:
  - [ ] Focused pytest command passes.
  - [ ] Relevant existing ACP/MCP tests pass.
  - [ ] Ruff check passes for changed areas.
  - [ ] Mypy passes for changed source files.

  **QA Scenarios**:

  ```text
  Scenario: Focused validation suite passes
    Tool: Bash
    Preconditions: Tasks 1-8 complete
    Steps:
      1. Run `uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py tests/servers/acp_server/test_mcp_integration.py tests/servers/acp_server/test_acp_integration.py -q`.
      2. Save full output.
    Expected Result: Exit 0 or documented pre-existing platform skips only.
    Failure Indicators: Any failure introduced by MCP notification bridge.
    Evidence: .sisyphus/evidence/task-9-focused-pytest.txt

  Scenario: Static checks pass for changed areas
    Tool: Bash
    Preconditions: Tasks 1-8 complete
    Steps:
      1. Run `uv run ruff check src/agentpool/mcp_server src/agentpool/resource_providers src/agentpool_server/acp_server tests/servers/acp_server`.
      2. Run `uv run --no-group docs mypy src/agentpool/mcp_server/message_handler.py src/agentpool/mcp_server/client.py src/agentpool/resource_providers/base.py src/agentpool/resource_providers/mcp_provider.py src/agentpool_server/acp_server/session.py`.
      3. Save outputs.
    Expected Result: Both commands exit 0.
    Failure Indicators: Ruff or mypy errors in changed files.
    Evidence: .sisyphus/evidence/task-9-static-checks.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-9-focused-pytest.txt`
  - [ ] `.sisyphus/evidence/task-9-static-checks.txt`

  **Commit**: YES
  - Message: `fix(acp): validate mcp notification bridge`
  - Files: Any scoped fixes from validation
  - Pre-commit: Definition of Done commands

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
>
> Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read this plan end-to-end. For each Must Have, verify implementation exists. For each Must NOT Have, search codebase for forbidden patterns. Check all evidence files exist under `.sisyphus/evidence/`. Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run focused pytest, ruff, and mypy commands. Review changed files for weak typing, `getattr`/`hasattr`, `as Any`, broad suppressions, empty catches, and over-abstraction. Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`.

- [ ] F3. **Real QA Execution** — `unspecified-high`
  Execute every QA scenario listed in Tasks 1-9, save outputs, then run cross-task integration: emit list-change and resource-update events through the session bridge and assert ACP mock client calls. Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`.

- [ ] F4. **Scope Fidelity Check** — `deep`
  Compare actual diff with every task. Verify no ACP schema changes, no SessionNotification misuse, no client capability/debounce/resume/switch-agent expansion, and no MCP-to-ACP coupling outside `ACPSession`. Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`.

---

## Commit Strategy

- **Commit 1**: `feat(mcp): expose resource update notifications` — provider/base/MCP client/message handler changes; pre-commit focused bridge tests.
- **Commit 2**: `feat(acp): bridge mcp change notifications` — `ACPSession` bridge and safe cleanup; pre-commit focused bridge tests.
- **Commit 3**: `test(acp): cover mcp notification bridge` — complete tests; pre-commit focused bridge tests.
- **Commit 4 (optional)**: `fix(acp): validate mcp notification bridge` — only scoped validation fixes if needed.

---

## Success Criteria

### Verification Commands

```bash
uv run pytest tests/servers/acp_server/test_mcp_notification_bridge.py -q
uv run pytest tests/servers/acp_server/test_mcp_integration.py tests/servers/acp_server/test_acp_integration.py -q
uv run ruff check src/agentpool/mcp_server src/agentpool/resource_providers src/agentpool_server/acp_server tests/servers/acp_server
uv run --no-group docs mypy src/agentpool/mcp_server/message_handler.py src/agentpool/mcp_server/client.py src/agentpool/resource_providers/base.py src/agentpool/resource_providers/mcp_provider.py src/agentpool_server/acp_server/session.py
```

### Final Checklist

- [ ] MCP list-change notifications reach ACP client as `_mcp/*/listChanged` extension notifications.
- [ ] MCP resource content updates reach ACP client as `_mcp/resources/updated` with `uri`.
- [ ] Notification params include provider origin metadata.
- [ ] Disabled MCP server setup is handled safely.
- [ ] Session close disconnects the new MCP bridge handlers.
- [ ] No ACP schema changes were made.
- [ ] No MCP/provider layer imports ACP.
- [ ] All tests/static checks pass or pre-existing unrelated failures are documented.
