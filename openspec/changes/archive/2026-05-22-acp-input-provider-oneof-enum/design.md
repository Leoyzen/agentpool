## Context

`ACPInputProvider` in `agentpool/src/agentpool_server/acp_server/input_provider.py` provides elicitation support for ACP clients that do not declare the `elicitation/create` capability. It falls back to `request_permission`, which was designed for tool-call confirmation rather than free-form user input.

The current fallback logic detects enum schemas via `_is_enum_schema(schema)`, which only recognizes `{"type": "string", "enum": [...]}`. However, `xeno-agent`'s `question_for_user` tool generates schemas using `oneOf` (for single-select with descriptions) and `array`+`items.enum` (for multi-select). These schemas fail detection, causing the fallback to present generic Accept/Decline buttons instead of the actual options.

OpenCode's `OpenCodeInputProvider` already handles these constructs natively, confirming the schema formats are valid and expected.

## Goals / Non-Goals

**Goals:**
- Extend `_is_enum_schema()` to recognize `oneOf` and `array+items.enum` as enum-like
- Add option extractors for `oneOf` and array-enum schemas
- Map user selections back to the correct schema values
- Maintain backward compatibility with existing `enum` schemas
- Keep changes localized to `input_provider.py`

**Non-Goals:**
- Modifying the ACP `elicitation/create` protocol path
- Changing xeno-agent's schema generation (xeno-agent's schemas are correct)
- Adding support for arbitrary JSON schema constructs beyond enum/oneOf/array-enum

## Decisions

1. **Add `_is_oneof_schema()` and `_is_array_enum_schema()` helpers**
   - Rationale: Keeping detection logic in separate predicates makes the code readable and testable. `_is_enum_schema()` stays unchanged for backward compatibility.
   - Alternative considered: Modifying `_is_enum_schema()` to handle all cases — rejected because it would bloat a single function and conflate different schema shapes.

2. **Add `_create_oneof_elicitation_options()` and `_create_array_enum_elicitation_options()`**
   - Rationale: Each schema shape needs different extraction logic. `oneOf` entries have `const` (value) and optional `title` (label). Array-enum has `items.enum` with optional `x-option-descriptions`.
   - Alternative considered: Reusing `_create_enum_elicitation_options()` — rejected because `enum` is a flat list of values, while `oneOf` is a list of objects.

3. **Add `_handle_oneof_elicitation_response()` and `_handle_array_enum_elicitation_response()`**
   - Rationale: Response mapping differs. `oneOf` responses need to match `const` values. Array-enum responses are already lists.
   - The existing `_handle_enum_elicitation_response()` remains untouched.

4. **No changes to `PermissionOption` or `request_permission` interface**
   - Rationale: The ACP `request_permission` method accepts a list of `PermissionOption` regardless of schema type. We only need to populate that list correctly.

## Risks / Trade-offs

- [Risk] `oneOf` schemas may contain non-`const` entries (e.g., `type` objects) → Mitigation: Filter out entries without `const`, fallback to generic Accept/Decline if no valid options found
- [Risk] Array-enum schemas with `x-option-descriptions` may have mismatched keys → Mitigation: Use `.get()` with empty string fallback, log warning on mismatch
- [Risk] Large option lists may exceed UI display limits → Mitigation: Not in scope — `request_permission` handles presentation

## Migration Plan

No migration needed. This is a backward-compatible enhancement. Existing enum schemas continue to work identically.

## Open Questions

- Should we cap the number of options extracted from `oneOf` to prevent excessively long permission dialogs? (Deferred — can be added later if needed)
