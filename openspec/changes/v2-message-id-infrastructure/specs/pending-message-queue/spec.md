## ADDED Requirements

### Requirement: Feedback dataclass extended with message_id, content_blocks, and mode

The `Feedback` dataclass in `lifecycle/types.py` SHALL be extended with three new fields: `message_id`, `content_blocks`, and `mode`. All new fields SHALL have defaults to maintain backward compatibility with existing construction sites.

- `message_id: str` SHALL default to `str(uuid.uuid4())` via `field(default_factory=...)`. It is the agent-owned opaque identifier for this feedback, used as the handle for revoke and replace operations. Callers MAY override with an explicit value.
- `content_blocks: list[dict[str, Any]] | None` SHALL default to `None`. When provided, it carries structured content (e.g. ACP `ContentBlock[]`-shaped dicts, or PydanticAI `UserContent` items). When `None`, `content` is the plain-text representation. The pipeline carries `content_blocks` through without stringification; protocol-specific type mapping (ACP `ContentBlock` ↔ PydanticAI `UserContent`) is deferred to the v2 protocol adapter.
- `mode: str | None` SHALL default to `None` and be auto-derived from `is_steer` in `__post_init__`: `"steer"` when `is_steer=True`, `"queue"` when `is_steer=False`. Callers MAY override with an explicit value.
- `__post_init__` SHALL set `mode` from `is_steer` when `mode` is `None`.
- The existing `content: str` and `is_steer: bool` fields SHALL remain unchanged for backward compatibility.

#### Scenario: Feedback auto-generates message_id

- **WHEN** `Feedback(content="hello", is_steer=True)` is constructed without `message_id`
- **THEN** `message_id` SHALL be a non-empty UUID string
- **AND** `mode` SHALL be `"steer"`
- **AND** `content_blocks` SHALL be `None`

#### Scenario: Feedback with explicit message_id

- **WHEN** `Feedback(content="hello", is_steer=False, message_id="msg_custom")` is constructed
- **THEN** `message_id` SHALL be `"msg_custom"`
- **AND** `mode` SHALL be `"queue"`
- **AND** no auto-generated UUID SHALL overwrite the provided value

#### Scenario: Feedback with explicit mode override

- **WHEN** `Feedback(content="hello", is_steer=True, mode="queue")` is constructed
- **THEN** `mode` SHALL be `"queue"` (explicit override takes precedence)
- **AND** `is_steer` SHALL remain `True` (not derived from mode)

#### Scenario: Feedback with content_blocks

- **WHEN** `Feedback(content="text fallback", is_steer=True, content_blocks=[{"type": "text", "text": "structured"}])` is constructed
- **THEN** `content_blocks` SHALL be `[{"type": "text", "text": "structured"}]`
- **AND** `content` SHALL be `"text fallback"` (not overwritten by content_blocks)

#### Scenario: Backward compatibility with existing construction

- **WHEN** existing code constructs `Feedback(content=message, is_steer=True)` without new fields
- **THEN** the construction SHALL succeed without errors
- **AND** `message_id` SHALL be auto-generated
- **AND** `mode` SHALL be `"steer"`
- **AND** `content_blocks` SHALL be `None`
