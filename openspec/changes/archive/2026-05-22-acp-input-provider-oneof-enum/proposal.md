## Why

When xeno-agent's `question_for_user` tool sends elicitation requests to Zed (ACP server with fallback to `request_permission`), the user sees only generic "Accept/Decline" buttons instead of the actual question options. This happens because xeno-agent generates JSON schemas using `oneOf` constructs (e.g., `{"type": "string", "oneOf": [{"const": "A"}, ...]}`), but `ACPInputProvider` only recognizes `enum` keywords for option extraction. As a result, `_is_enum_schema()` returns `false`, and the fallback path presents a generic permission dialog rather than the intended multiple-choice options.

## What Changes

- Extend `ACPInputProvider._is_enum_schema()` to recognize `oneOf` as an enum-like construct
- Add `_create_oneof_elicitation_options()` to extract options from `oneOf` schemas
- Extend enum detection to support `array` type with `items.enum` (for multi-select questions)
- Add `_create_array_enum_elicitation_options()` for array-based enum schemas
- Handle `const`/`title` pairs in `oneOf` entries for richer option labels
- Ensure backward compatibility with existing `enum`-based schemas

## Capabilities

### New Capabilities
- `acp-input-provider-schema-recognition`: Enhanced schema parsing in ACPInputProvider to support `oneOf` and nested `array+enum` constructs for elicitation fallback

### Modified Capabilities
- *(none — this is a pure implementation enhancement, no spec-level behavior changes)*

## Impact

- `src/agentpool_server/acp_server/input_provider.py`: Core enum detection and option creation logic
- `xeno-agent` users connecting via ACP/Zed: Will now see proper multiple-choice options instead of generic Accept/Decline
- No API changes, no breaking changes
