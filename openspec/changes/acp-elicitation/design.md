## Context

Agentpool serves as an ACP server — exposing agents to ACP clients (IDEs like Zed, custom clients). The ACP specification defines `elicitation/create` for structured user input, but agentpool currently works around this by encoding elicitation into `request_permission` calls. This loses schema information, limits input to boolean/enum, and cannot support URL-mode flows.

The existing `ACPInputProvider.get_elicitation()` converts internal elicitation types (ElicitString, ElicitNumber, ElicitBoolean, ElicitChoice, ElicitUrl, ElicitForm) into `RequestPermissionRequest` options. This is a lossy mapping — string/number inputs become Accept/Decline choices, and URL flows cannot be initiated.

The ACP client capabilities are exchanged during `initialize`, and `ACPSession` stores `client_capabilities: ClientCapabilities`. This provides the signal needed for capability-gated behavior.

## Goals / Non-Goals

**Goals:**
- Add ACP schema types for `elicitation/create` (request, response, notification, error)
- Add `ElicitationCapabilities` to `ClientCapabilities` so clients can declare support
- Add `elicitation_create()` to ACP Client protocol and all client implementations
- Route `elicitation/create` in `ClientSideConnection._handle_client_method()`
- Rewrite `ACPInputProvider.get_elicitation()` to use `elicitation/create` when client declares capability
- Add `ElicitationCompleteNotification` for URL-mode completion signaling
- Maintain full backward compatibility — clients without elicitation capability continue via `request_permission`

**Non-Goals:**
- Agentpool as ACP client receiving elicitation (agent-side protocol changes)
- MCP server elicitation support
- AG-UI or OpenCode server elicitation support
- Changing the internal `ElicitForm`/`ElicitResult` types in `agentpool/ui/elicitation.py`
- Changing the `InputProvider` base class interface

## Decisions

### D1: Elicitation types in a new `elicitation.py` module

**Decision**: Create `src/acp/schema/elicitation.py` as a dedicated module.

**Rationale**: Follows existing pattern where each domain has its own module (`capabilities.py`, `notifications.py`). Keeps elicitation types self-contained and importable without pulling in unrelated types.

**Alternative considered**: Adding types inline to `agent_requests.py`/`client_responses.py` — rejected because elicitation spans both request and response types plus a notification and error, which deserve their own module.

### D2: `ElicitationCreateRequest` extends `BaseAgentRequest` (has `session_id`)

**Decision**: `ElicitationCreateRequest` inherits from `BaseAgentRequest` like `RequestPermissionRequest`, ensuring `session_id` is always present.

**Rationale**: The ACP elicitation spec requires `sessionId` scoping. The existing `BaseAgentRequest(session_id: str)` provides exactly this.

**Fields**: `session_id`, `message: str`, `mode: Literal["form", "url"]`, `requested_schema: dict[str, Any] | None` (form mode), `url: str | None` + `elicitation_id: str | None` (url mode), `tool_call_id: str | None`, `request_id: str | None`.

### D3: `ElicitationCreateResponse` uses three-action model

**Decision**: Response has `action: Literal["accept", "decline", "cancel"]` and optional `content: dict[str, Any] | None` (only when action=accept).

**Rationale**: Matches the ACP RFC's three-action model exactly. `content` carries form data for accept, null for decline/cancel.

### D4: `ElicitationCompleteNotification` added to `AgentNotification`

**Decision**: New notification type with `session_id`, `elicitation_id`, and `result: Literal["completed", "expired", "error"]`.

**Rationale**: The RFC specifies `elicitation/complete` as an agent→client notification for URL-mode completion. Adding it to `AgentNotification` union enables proper routing alongside existing notifications.

### D5: Capability-gated dual-path in `ACPInputProvider.get_elicitation()`

**Decision**: Check `session.client_capabilities.elicitation` — if present and appropriate mode is supported, use `elicitation_create()`. Otherwise, fall back to existing `request_permission()` hack.

**Rationale**: Zero breaking changes. Existing clients continue working identically. New clients that declare elicitation capability get the proper protocol path.

### D6: `URLElicitationRequiredError` with code -32042

**Decision**: Add as a typed error with error code -32042 from the RFC.

**Rationale**: Enables agents to catch this specific error and initiate URL-mode elicitation as a fallback.

## Risks / Trade-offs

- **[Risk] Client implementations must handle new method** → All 3 client implementations (default, headless, noop) get stub implementations. Default and headless auto-accept (consistent with existing permission behavior). Noop returns decline.
- **[Risk] Schema mismatch between internal ElicitForm and JSON Schema** → The existing `to_mcp_schema()` in `agentpool/ui/elicitation.py` already converts internal types to JSON Schema. Reuse this for `requestedSchema` generation.
- **[Risk] `requested_schema` field uses raw `dict[str, Any]` instead of typed schema** → Acceptable for now; the ACP RFC specifies JSON Schema which is inherently dynamic. Type-safe validation happens at the client side.
- **[Trade-off] Only agentpool-as-server direction** → Agentpool-as-client (receiving elicitation from external ACP agents) is out of scope. This means `agent/protocol.py` doesn't get elicitation methods yet.
