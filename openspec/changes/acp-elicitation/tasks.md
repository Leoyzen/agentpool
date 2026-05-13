## 1. ACP Schema - Elicitation Types

- [ ] 1.1 Create `src/acp/schema/elicitation.py` with `ElicitationCreateRequest`, `ElicitationCreateResponse`, `ElicitationCompleteNotification`, `URLElicitationRequiredError`
- [ ] 1.2 Add `ElicitationCapabilities(form: bool, url: bool)` and `ClientCapabilities.elicitation` field to `src/acp/schema/capabilities.py`
- [ ] 1.3 Add `ElicitationCreateRequest` to `AgentRequest` union in `src/acp/schema/agent_requests.py`
- [ ] 1.4 Add `ElicitationCreateResponse` to `ClientResponse` union in `src/acp/schema/client_responses.py`
- [ ] 1.5 Add `ElicitationCompleteNotification` to `AgentNotification` union in `src/acp/schema/notifications.py`
- [ ] 1.6 Add `"elicitation/create"` to `ClientMethod` literal in `src/acp/schema/messages.py`
- [ ] 1.7 Update `src/acp/schema/__init__.py` exports with all new types

## 2. ACP Protocol - Client Methods & Routing

- [ ] 2.1 Add `elicitation_create()` method to `Client` protocol in `src/acp/client/protocol.py`
- [ ] 2.2 Add `elicitation_create()` convenience method to `ACPRequests` in `src/acp/agent/acp_requests.py`
- [ ] 2.3 Add `"elicitation/create"` routing in `ClientSideConnection._handle_client_method()` in `src/acp/client/connection.py`

## 3. ACP Protocol - Client Implementations

- [ ] 3.1 Implement `elicitation_create()` in `DefaultACPClient` (auto-accept form, decline URL)
- [ ] 3.2 Implement `elicitation_create()` in `HeadlessACPClient` (auto-accept form, decline URL)
- [ ] 3.3 Implement `elicitation_create()` in `NoOpClient` (decline all)

## 4. ACP Server - Input Provider Rewrite

- [ ] 4.1 Add capability check to `ACPInputProvider.get_elicitation()` — detect `client_capabilities.elicitation`
- [ ] 4.2 Implement form-mode elicitation path using `elicitation_create` with `requested_schema` from `to_mcp_schema()`
- [ ] 4.3 Implement URL-mode elicitation path using `elicitation_create` with `url` + `elicitation_id`
- [ ] 4.4 Implement response mapping: `ElicitationCreateResponse` → internal `ElicitResult`
- [ ] 4.5 Preserve existing `request_permission` fallback path for clients without elicitation capability

## 5. Notification Routing

- [ ] 5.1 Add `ElicitationCompleteNotification` routing in `ClientSideConnection` notification handling
- [ ] 5.2 Add `send_elicitation_complete()` convenience method to `ACPNotifications`

## 6. Verification

- [ ] 6.1 Run `uv run ruff check src/acp/ src/agentpool_server/acp_server/` — no errors
- [ ] 6.2 Run `uv run mypy src/acp/ src/agentpool_server/acp_server/` — no type errors
- [ ] 6.3 Run `uv run pytest tests/ -k "acp"` — existing tests pass
