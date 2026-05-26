## Requirements

### Requirement: ACPInputProvider recognizes oneOf schemas as enum-like
The system SHALL detect JSON schemas using `oneOf` with `const` entries as enum-like constructs for elicitation fallback.

#### Scenario: oneOf schema with const entries
- **WHEN** an elicitation schema contains `{"type": "string", "oneOf": [{"const": "A", "title": "Option A"}]}`
- **THEN** `ACPInputProvider` SHALL present "Option A" as a selectable option

#### Scenario: oneOf schema without const entries
- **WHEN** an elicitation schema contains `oneOf` entries without `const` fields
- **THEN** `ACPInputProvider` SHALL fallback to generic Accept/Decline options

### Requirement: ACPInputProvider recognizes array+enum schemas as enum-like
The system SHALL detect JSON schemas using `{"type": "array", "items": {"type": "string", "enum": [...]}}` as enum-like constructs for elicitation fallback.

#### Scenario: Array enum schema
- **WHEN** an elicitation schema contains `{"type": "array", "items": {"enum": ["A", "B"]}}`
- **THEN** `ACPInputProvider` SHALL present "A" and "B" as selectable options

#### Scenario: Array enum with descriptions
- **WHEN** an array enum schema includes `items["x-option-descriptions"]` mapping
- **THEN** `ACPInputProvider` SHALL use the descriptions as option labels where available

### Requirement: ACPInputProvider maps oneOf selections back to const values
The system SHALL return the `const` value (not the `title`) from a `oneOf` selection in the elicitation result.

#### Scenario: User selects oneOf option
- **WHEN** a user selects an option derived from `{"const": "value", "title": "Label"}`
- **THEN** the elicitation result content SHALL contain `"value"` (the const), not `"Label"` (the title)

### Requirement: Backward compatibility with existing enum schemas
The system SHALL continue to support existing `{"type": "string", "enum": [...]}` schemas without behavior change.

#### Scenario: Legacy enum schema
- **WHEN** an elicitation schema contains `{"type": "string", "enum": ["A", "B"]}`
- **THEN** `ACPInputProvider` SHALL present "A" and "B" as selectable options, identical to pre-change behavior
