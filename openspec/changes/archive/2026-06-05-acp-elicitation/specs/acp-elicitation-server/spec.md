## ADDED Requirements

### Requirement: Capability-gated elicitation in ACPInputProvider
The `ACPInputProvider.get_elicitation` SHALL check `session.client_capabilities.elicitation` before choosing the elicitation path. If the client declares elicitation capability matching the requested mode, the provider SHALL use `elicitation_create`. Otherwise, it SHALL fall back to the existing `request_permission` approach.

#### Scenario: Client supports form elicitation and form input is needed
- **WHEN** `get_elicitation` is called with form-type params AND `client_capabilities.elicitation.form` is `True`
- **THEN** the provider SHALL call `elicitation_create` with `mode="form"` and `requested_schema` derived from the internal elicitation type

#### Scenario: Client supports URL elicitation and URL input is needed
- **WHEN** `get_elicitation` is called with URL-type params AND `client_capabilities.elicitation.url` is `True`
- **THEN** the provider SHALL call `elicitation_create` with `mode="url"`, `url`, and `elicitation_id`

#### Scenario: Client does not support elicitation
- **WHEN** `get_elicitation` is called AND `client_capabilities.elicitation` is `None`
- **THEN** the provider SHALL fall back to the existing `request_permission` approach (boolean→Yes/No, enum→enum options, etc.)

#### Scenario: Client supports form but not URL, and URL is needed
- **WHEN** `get_elicitation` is called with URL-type params AND `client_capabilities.elicitation.url` is `False` (but form is True)
- **THEN** the provider SHALL fall back to the existing `request_permission` approach for the URL case

### Requirement: Form schema generation from internal types
When using the `elicitation_create` path, the provider SHALL convert internal elicitation types to JSON Schema using the existing `to_mcp_schema()` conversion function for the `requestedSchema` field.

#### Scenario: ElicitChoice converts to enum schema
- **WHEN** an `ElicitChoice` with options ["a", "b", "c"] is processed
- **THEN** the `requestedSchema` SHALL be a JSON Schema with `enum: ["a", "b", "c"]` and `type: "string"`

#### Scenario: ElicitForm converts to object schema
- **WHEN** an `ElicitForm` with multiple fields is processed
- **THEN** the `requestedSchema` SHALL be a JSON Schema `object` with `properties` for each field

### Requirement: Elicitation response mapping
The `ACPInputProvider` SHALL map `ElicitationCreateResponse` actions back to internal `ElicitResult` types.

#### Scenario: Accept response with content
- **WHEN** `elicitation_create` returns `action="accept"` with `content={"field": "value"}`
- **THEN** the provider SHALL return an `ElicitResult` with `action="accept"` and `value` populated from the content

#### Scenario: Decline response
- **WHEN** `elicitation_create` returns `action="decline"`
- **THEN** the provider SHALL return an `ElicitResult` with `action="decline"` and `value=None`

#### Scenario: Cancel response
- **WHEN** `elicitation_create` returns `action="cancel"`
- **THEN** the provider SHALL return an `ElicitResult` with `action="cancel"` and `value=None`
