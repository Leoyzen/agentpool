## Why

The ACP specification defines an `elicitation/create` JSON-RPC method for agents to request structured user input, but agentpool currently hacks elicitation into `request_permission` calls — losing schema information, supporting only boolean/enum primitives, and preventing URL-mode flows (OAuth, credential collection). As the ACP spec evolves toward proper elicitation, agentpool as an ACP server cannot serve clients that declare elicitation capabilities.

## What Changes

- Add `ElicitationCapabilities` (form/url) to `ClientCapabilities` in ACP schema
- Add `elicitation/create` request/response types to ACP schema (`ElicitationCreateRequest`, `ElicitationCreateResponse`)
- Add `ElicitationCompleteNotification` as a new `AgentNotification` type for URL-mode completion
- Add `URLElicitationRequiredError` (-32042) error type
- Add `elicitation/create` method string to `ClientMethod` literal
- Add `elicitation_create()` to ACP Client protocol, `ACPRequests`, and all client implementations
- Add routing for `elicitation/create` in `ClientSideConnection._handle_client_method()`
- Rewrite `ACPInputProvider.get_elicitation()` to use `elicitation/create` when client declares capability, with fallback to existing `request_permission` hack for backward compatibility

## Capabilities

### New Capabilities
- `acp-elicitation-schema`: ACP schema types for elicitation (ElicitationCapabilities, ElicitationCreateRequest/Response, ElicitationCompleteNotification, URLElicitationRequiredError)
- `acp-elicitation-protocol`: ACP protocol methods and routing (client.elicitation_create, ACPRequests convenience method, connection routing)
- `acp-elicitation-server`: ACP server input provider rewrite with capability-gated elicitation/create and permission fallback

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **ACP Schema** (`src/acp/schema/`): New types added to capabilities, agent_requests, client_responses, notifications, messages, and a new `elicitation.py` module. `__init__.py` exports updated.
- **ACP Protocols** (`src/acp/client/`, `src/acp/agent/`): New method on Client protocol, ACPRequests, and all 3 client implementations (default, headless, noop). New routing case in `ClientSideConnection._handle_client_method()`.
- **ACP Server** (`src/agentpool_server/acp_server/input_provider.py`): Capability-aware `get_elicitation()` with dual-path (elicitation/create vs permission hack).
- **Backward compatibility**: Clients that don't declare `elicitation` capability continue working via the existing `request_permission` path — no breaking changes.
