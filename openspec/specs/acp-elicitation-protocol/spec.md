## ADDED Requirements

### Requirement: Client protocol elicitation_create method
The `Client` protocol SHALL define an `elicitation_create` async method that accepts `ElicitationCreateRequest` params and returns `ElicitationCreateResponse`.

#### Scenario: Agent calls elicitation_create on client
- **WHEN** an agent calls `client.elicitation_create(params)` with a valid `ElicitationCreateRequest`
- **THEN** the client SHALL process the request and return an `ElicitationCreateResponse`

### Requirement: ACPRequests elicitation_create convenience method
The `ACPRequests` class SHALL provide an `elicitation_create` method that constructs an `ElicitationCreateRequest` and delegates to the client's `elicitation_create`.

#### Scenario: Convenience method creates form elicitation
- **WHEN** `acp_requests.elicitation_create(session_id, message="Enter name", mode="form", requested_schema={...})` is called
- **THEN** it SHALL construct an `ElicitationCreateRequest` with the provided params and call `client.elicitation_create`

#### Scenario: Convenience method creates URL elicitation
- **WHEN** `acp_requests.elicitation_create(session_id, message="Auth required", mode="url", url="https://...", elicitation_id="...")` is called
- **THEN** it SHALL construct an `ElicitationCreateRequest` with mode="url" and the URL fields

### Requirement: ClientSideConnection routing for elicitation/create
The `ClientSideConnection._handle_client_method` SHALL route `"elicitation/create"` to the `elicitation_create` method on the client implementation.

#### Scenario: Incoming elicitation/create is routed
- **WHEN** `_handle_client_method` receives method `"elicitation/create"`
- **THEN** it SHALL deserialize params as `ElicitationCreateRequest` and call `self.client.elicitation_create(request)`

### Requirement: Default client implementation of elicitation_create
The `DefaultACPClient` SHALL implement `elicitation_create` by auto-accepting with empty content for form mode, or declining for URL mode.

#### Scenario: Default client auto-accepts form elicitation
- **WHEN** `DefaultACPClient.elicitation_create` receives a form-mode request
- **THEN** it SHALL return `ElicitationCreateResponse(action="accept", content={})`

#### Scenario: Default client declines URL elicitation
- **WHEN** `DefaultACPClient.elicitation_create` receives a URL-mode request
- **THEN** it SHALL return `ElicitationCreateResponse(action="decline")`

### Requirement: Headless client implementation of elicitation_create
The `HeadlessACPClient` SHALL implement `elicitation_create` by auto-accepting with empty content for form mode, or declining for URL mode (same behavior as default client).

#### Scenario: Headless client auto-accepts form elicitation
- **WHEN** `HeadlessACPClient.elicitation_create` receives a form-mode request
- **THEN** it SHALL return `ElicitationCreateResponse(action="accept", content={})`

### Requirement: NoOp client implementation of elicitation_create
The `NoOpClient` SHALL implement `elicitation_create` by declining all requests.

#### Scenario: NoOp client declines elicitation
- **WHEN** `NoOpClient.elicitation_create` receives any request
- **THEN** it SHALL return `ElicitationCreateResponse(action="decline")`

### Requirement: ElicitationCompleteNotification routing
The system SHALL route `ElicitationCompleteNotification` through the notification system, enabling agents to signal URL-mode flow completion to clients.

#### Scenario: Agent sends elicitation complete notification
- **WHEN** an agent sends an `ElicitationCompleteNotification` via `ACPNotifications`
- **THEN** the notification SHALL be delivered to the client through the established notification channel
