## 1. Schema Detection Helpers

- [x] 1.1 Add `_is_oneof_schema(schema)` helper that detects `{"type": "string", "oneOf": [...]}` with `const` entries
- [x] 1.2 Add `_is_array_enum_schema(schema)` helper that detects `{"type": "array", "items": {"enum": [...]}}`
- [x] 1.3 Update `_get_form_elicitation()` to check all three predicates (`_is_boolean_schema`, `_is_enum_schema`, `_is_oneof_schema`, `_is_array_enum_schema`) in order

## 2. Option Extraction

- [x] 2.1 Add `_create_oneof_elicitation_options(schema)` that extracts `PermissionOption` list from `oneOf` entries (using `const` as value, `title` as label)
- [x] 2.2 Add `_create_array_enum_elicitation_options(schema)` that extracts `PermissionOption` list from `items.enum` (using `x-option-descriptions` for labels if available)
- [x] 2.3 Ensure both extractors return `None` or empty list if no valid options found, triggering generic fallback

## 3. Response Handling

- [x] 3.1 Add `_handle_oneof_elicitation_response(response, schema)` that maps selected `option_id` back to the `const` value
- [x] 3.2 Add `_handle_array_enum_elicitation_response(response, schema)` that returns the selected option(s) as a list
- [x] 3.3 Wire new handlers into `_get_form_elicitation()` fallback path

## 4. Testing

- [x] 4.1 Add unit tests for `_is_oneof_schema()` with valid and invalid schemas
- [x] 4.2 Add unit tests for `_is_array_enum_schema()` with valid and invalid schemas
- [x] 4.3 Add unit tests for `_create_oneof_elicitation_options()` verifying label/value extraction
- [x] 4.4 Add unit tests for `_create_array_enum_elicitation_options()` verifying description fallback
- [x] 4.5 Add integration tests for `_get_form_elicitation()` with `oneOf` and array-enum schemas
- [x] 4.6 Verify existing enum schema tests still pass (backward compatibility)

## 5. Validation

- [x] 5.1 Run `ruff check` on modified file
- [x] 5.2 Run `mypy` on modified file
- [x] 5.3 Run relevant test suite (`pytest tests/ -k "input_provider"` or similar)
- [x] 5.4 Manual verification: test xeno-agent `question_for_user` with `enum` type via ACP/Zed
