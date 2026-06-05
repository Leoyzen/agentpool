## ADDED Requirements

### Requirement: ElicitationCapabilities type in ClientCapabilities
The system SHALL define an `ElicitationCapabilities` model with `form: bool` and `url: bool` fields. The `ClientCapabilities` model SHALL include an optional `elicitation: ElicitationCapabilities | None` field.

#### Scenario: Client declares form elicitation support
- **WHEN** a client sends `initialize` with `capabilities.elicitation.form = true`
- **THEN** the server SHALL recognize the client supports form-mode elicitation

#### Scenario: Client declares URL elicitation support
- **WHEN** a client sends `initialize` with `capabilities.elicitation.url = true`
- **THEN** the server SHALL recognize the client supports URL-mode elicitation

#### Scenario: Client does not declare elicitation capability
- **WHEN** a client sends `initialize` without `capabilities.elicitation`
- **THEN** the server SHALL treat the client as not supporting elicitation and use fallback paths

### Requirement: ElicitationCreateRequest type
The system SHALL define `ElicitationCreateRequest` extending `BaseAgentRequest` with fields: `message: str`, `mode: Literal["form", "url"]`, `requested_schema: dict[str, Any] | None`, `url: str | None`, `elicitation_id: str | None`, `tool_call_id: str | None`, `request_id: str | None`.

#### Scenario: Form-mode elicitation request
- **WHEN** an agent creates an `ElicitationCreateRequest` with `mode = "form"`
- **THEN** the request SHALL include `requested_schema` with a JSON Schema object and SHALL NOT require `url` or `elicitation_id`

#### Scenario: URL-mode elicitation request
- **WHEN** an agent creates an `ElicitationCreateRequest` with `mode = "url"`
- **THEN** the request SHALL include `url` and `elicitation_id` fields and SHALL NOT require `requested_schema`

### Requirement: ElicitationCreateResponse type
The system SHALL define `ElicitationCreateResponse` with `action: Literal["accept", "decline", "cancel"]` and `content: dict[str, Any] | None`.

#### Scenario: User accepts form elicitation
- **WHEN** a client responds with `action = "accept"` to a form-mode request
- **THEN** the response SHALL include `content` with form field values matching the requested schema

#### Scenario: User declines elicitation
- **WHEN** a client responds with `action = "decline"`
- **THEN** the response SHALL have `content = None`

#### Scenario: User cancels elicitation
- **WHEN** a client responds with `action = "cancel"`
- **THEN** the response SHALL have `content = None`

### Requirement: ElicitationCompleteNotification type
The system SHALL define `ElicitationCompleteNotification` with `session_id: str`, `elicitation_id: str`, and `result: Literal["completed", "expired", "error"]`.

#### Scenario: URL elicitation completes successfully
- **WHEN** an agent sends `ElicitationCompleteNotification` with `result = "completed"`
- **THEN** the client SHALL be notified that the URL flow finished and data is available

#### Scenario: URL elicitation expires
- **WHEN** an agent sends `ElicitationCompleteNotification` with `result = "expired"`
- **THEN** the client SHALL be notified that the URL flow timed out

### Requirement: URLElicitationRequiredError type
The system SHALL define `URLElicitationRequiredError` with error code `-32042` and a `url: str` field.

#### Scenario: Agent requires URL-mode but client only supports form
- **WHEN** an agent attempts form-mode elicitation for a flow that requires URL mode
- **THEN** the system MAY raise `URLElicitationRequiredError` with the target URL

### Requirement: elicitation/create in AgentRequest and ClientResponse unions
The `AgentRequest` union SHALL include `ElicitationCreateRequest`. The `ClientResponse` union SHALL include `ElicitationCreateResponse`. The `AgentNotification` union SHALL include `ElicitationCompleteNotification`.

#### Scenario: Request dispatched as elicitation/create
- **WHEN** the ACP connection receives a method "elicitation/create"
- **THEN** it SHALL be parsed as `ElicitationCreateRequest` and included in the `AgentRequest` union

### Requirement: elicitation/create method string
The `ClientMethod` literal SHALL include `"elicitation/create"`.

#### Scenario: Method string matches elicitation routing
- **WHEN** a JSON-RPC message has method `"elicitation/create"`
- **THEN** the system SHALL route it to the elicitation handler
